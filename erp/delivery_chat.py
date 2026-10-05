"""Importación auditada y provisional de repartos. Sin pagos ni inferencias de responsable."""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import zipfile
from erp.delivery_finance import encoded, now, view as fees_view
from erp.domain import DomainError

STATUS = {'structured_subtotal':'Base provisional', 'pending_driver':'Repartidor sin confirmar',
          'pending_amount':'Monto sin confirmar', 'pending_duplicate_or_reassigned':'Repetición o reasignación',
          'pending_possible_duplicate':'Posible duplicado', 'pending_failed_attempt_payment':'Intento fallido · pago por revisar'}
RECORDS = 'september_deliveries_audited.json'
MESSAGES = 'september_evidence_messages.json'
SUMMARY = 'september_analysis_summary.json'


def fail(): raise DomainError('El paquete auditado no supera los controles; no se importó.')


def read_package(path, month):
    raw = Path(path).read_bytes()
    if len(raw)>8*1024*1024: fail()
    try:
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
            if len(infos)>20 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>8*1024*1024:fail()
            manifest=json.loads(archive.read('manifest.json'))
            if manifest['package_schema']!='erp_delivery_import_provisional_v1':fail()
            source=manifest['source_sha256']
            if not isinstance(source,str) or not re.fullmatch('[a-f0-9]{64}',source):fail()
            verified={}
            for entry in manifest['files']:
                name=entry['name']
                if Path(name).name!=name or name in verified:fail()
                content=archive.read(name)
                if len(content)!=entry['bytes'] or hashlib.sha256(content).hexdigest()!=entry['sha256']:fail()
                verified[name]=content
            records=json.loads(verified[RECORDS]);messages=json.loads(verified[MESSAGES]);summary=json.loads(verified[SUMMARY])
        if summary['source_sha256']!=source or summary['source_library_file_id']!=manifest['source_library_file_id']:fail()
        if not records or len(records)>10000 or len(messages)>20000:fail()
        evidence={}
        for message in messages:
            ordinal=message['id']
            if type(ordinal) is not int or ordinal<=0 or ordinal in evidence:fail()
            timestamp=datetime.fromisoformat(message['timestamp'])
            if timestamp.tzinfo is not None or message['timestamp'][:7]!=month:fail()
            if not isinstance(message['text'],str) or len(message['text'])>30000:fail()
            evidence[ordinal]=message
        seen=set()
        for record in records:
            key=record['id'];value=record['amount_clp'];driver=record['driver']
            if not isinstance(key,str) or not re.fullmatch(r'[0-9]+\.[0-9]+',key) or key in seen:fail()
            seen.add(key)
            timestamp=datetime.fromisoformat(record['sent_at'])
            if timestamp.tzinfo is not None or record['sent_at'][:7]!=month or record['operational_date'] is not None:fail()
            if value is not None and (type(value) is not int or not 0<=value<=10**8):fail()
            if driver is not None and (not isinstance(driver,str) or not driver.strip() or len(driver)>100):fail()
            if record['driver_basis'] not in ('explicit','message_footer','missing'):fail()
            if (driver is None)!=(record['driver_basis']=='missing'):fail()
            if record['analysis_status'] not in STATUS:fail()
            if record['analysis_status']=='structured_subtotal' and (driver is None or value is None):fail()
            message=evidence[record['message_id']]
            if not message['line_start']<=record['line_start']<=record['line_end']<=message['line_end']:fail()
            if record['sent_at']!=message['timestamp']:fail()
            for ordinal in record.get('support_message_ids',[]):
                if ordinal not in evidence:fail()
            for key in ('customer','address','raw_block','sender','date_basis'):
                if record.get(key) is not None and (not isinstance(record[key],str) or len(record[key])>30000):fail()
        additional=summary.get('additional_text_only_attempt')
        for incident in ([additional] if additional else [])+summary.get('unallocated_incidents',[]):
            if incident['date'][:7]!=month:fail()
            date.fromisoformat(incident['date'])
            for ordinal in incident.get('support_message_ids',incident.get('message_ids',[])):
                if ordinal not in evidence:fail()
        base=[r for r in records if r['analysis_status']=='structured_subtotal']
        controls={'candidate_count':len(records),'structured_subtotal_count':len(base),
                  'known_gross_clp':sum(r['amount_clp'] or 0 for r in records),
                  'structured_subtotal_clp':sum(r['amount_clp'] for r in base),'evidence_message_count':len(messages)}
        if any(manifest[k]!=v for k,v in controls.items()):fail()
        mapped={'candidate_records':len(records),'numeric_records':sum(r['amount_clp'] is not None for r in records),
                'raw_numeric_total_clp':controls['known_gross_clp'],'structured_subtotal_records':len(base),
                'structured_subtotal_clp':controls['structured_subtotal_clp'],
                'delivery_messages':len({r['message_id'] for r in records})}
        if any(summary[k]!=v for k,v in mapped.items()):fail()
        expected={x['driver']:(x['records'],x['known_amount_clp']) for x in summary['structured_subtotal_by_driver']}
        drivers={r['driver'] for r in base}
        actual={d:(sum(r['driver']==d for r in base),sum(r['amount_clp'] for r in base if r['driver']==d)) for d in drivers}
        if expected!=actual:fail()
        return {'source':source,'manifest':manifest,'summary':summary,'records':records,'messages':messages,
                'package_sha256':hashlib.sha256(raw).hexdigest(),'controls':controls}
    except (KeyError,ValueError,TypeError,AttributeError,zipfile.BadZipFile):fail()


def import_package(store,path,month,library_file_id,actor):
    if not isinstance(actor,str) or not actor.strip() or len(actor)>160:raise DomainError('Falta responsable de la importación.')
    if not isinstance(library_file_id,str) or not library_file_id.startswith('libfile_'):raise DomainError('Falta identidad de Library.')
    package=read_package(path,month);source=package['source']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT package_sha256 FROM delivery_chat_batches WHERE source_sha256=?',(source,)).fetchone()
        if old:
            if old[0]!=package['package_sha256']:raise DomainError('La misma fuente cambió. Requiere revisión de versiones.',409)
            return {'imported':False,**package['controls']}
        if db.execute('SELECT 1 FROM delivery_chat_batches WHERE month=?',(month,)).fetchone():
            raise DomainError('Ya existe otro export para este mes. No se suman fuentes que podrían superponerse.',409)
        db.execute('INSERT INTO delivery_chat_batches VALUES(?,?,?,?,?,?,?,?,?)',
                   (source,month,package['package_sha256'],library_file_id,package['manifest']['source_library_file_id'],
                    encoded(package['manifest']),encoded(package['summary']),now(),actor.strip()))
        db.executemany('INSERT INTO delivery_chat_records VALUES(?,?,?,?,?,?,?)',
                       [(source,r['id'],r['sent_at'][:10],r['driver'],r['amount_clp'],r['analysis_status'],encoded(r)) for r in package['records']])
        db.executemany('INSERT INTO delivery_chat_messages VALUES(?,?,?)',[(source,m['id'],encoded(m)) for m in package['messages']])
    return {'imported':True,**package['controls']}


def totals(rows):
    base=[r for r in rows if r['analysis_status']=='structured_subtotal']
    return {'records':len(rows),'base_records':len(base),'base':sum(r['amount_clp'] for r in base),
            'known_gross':sum(r['amount_clp'] or 0 for r in rows),
            'unknown_amounts':sum(r['amount_clp'] is None for r in rows),'pending':len(rows)-len(base)}


def context_incidents(summary):
    result=[]
    additional=summary.get('additional_text_only_attempt')
    if additional:result.append(dict(additional,key='additional-attempt',label='Segundo intento autorizado en texto · sin ficha ni monto confirmado',message_ids=additional.get('support_message_ids',[])))
    for index,item in enumerate(summary.get('unallocated_incidents',[])):
        label={'return_and_later_redelivery':'Devolución y reprogramación · sin vínculo seguro',
               'do_not_deliver':'Instrucción de no entregar · sin vínculo seguro'}.get(item.get('type'),'Incidencia contextual por revisar')
        result.append(dict(item,key='unallocated-'+str(index),label=label))
    return result


def view(store,month):
    finance=fees_view(store,month);month=finance['month']
    with store.connect() as db:
        batch=db.execute('SELECT * FROM delivery_chat_batches WHERE month=?',(month,)).fetchone()
        if not batch:return {'month':month,'available':False,'payment_executed':False}
        rows=[json.loads(r[0]) for r in db.execute('SELECT payload_json FROM delivery_chat_records WHERE source_sha256=? ORDER BY day,record_id',(batch['source_sha256'],))]
    summary=json.loads(batch['summary_json']);daily=[]
    for day in finance['days']:
        selected=[r for r in rows if r['sent_at'][:10]==day['day']]
        daily.append(dict(day=day['day'],**totals(selected),toteat=day['fee'],toteat_records=day['records']))
    weeks={}
    for day in daily:
        when=date.fromisoformat(day['day']);monday=when-timedelta(days=when.weekday())
        entry=weeks.setdefault(str(monday),{'start':day['day'],'end':day['day'],'days':[]})
        entry['end']=day['day'];entry['days'].append(day)
    weekly=[]
    for value in weeks.values():
        selected=[r for r in rows if value['start']<=r['sent_at'][:10]<=value['end']]
        known=[d for d in value['days'] if d['toteat'] is not None]
        weekly.append(dict(start=value['start'],end=value['end'],**totals(selected),
                           toteat=sum(d['toteat'] for d in known) if len(known)==len(value['days']) else None))
    drivers=sorted({r['driver'] for r in rows if r['driver']})
    couriers=[dict(driver=driver,**totals([r for r in rows if r['driver']==driver])) for driver in drivers]
    if any(r['driver'] is None for r in rows):couriers.append(dict(driver=None,**totals([r for r in rows if r['driver'] is None])))
    fields=('id','sent_at','driver','amount_clp','analysis_status','edited','customer','line_start','line_end','message_id','suggested_amount_clp')
    return {'available':True,'month':month,'source_sha256':batch['source_sha256'],'imported_at':batch['imported_at'],'actor':batch['actor'],
            'source_library_file_id':batch['source_library_file_id'],'package_library_file_id':batch['library_file_id'],
            'totals':totals(rows),'days':daily,'weeks':weekly,'couriers':couriers,
            'records':[{**{k:r[k] for k in fields if k in r},'status_label':STATUS[r['analysis_status']]} for r in rows],
            'incidents':context_incidents(summary),'labels':STATUS,
            'coverage':{k:summary.get(k) for k in ('image_omissions','deleted_messages','edited_delivery_messages','edited_delivery_records')},
            'toteat':{'total':finance['totals']['fee'] if finance['totals']['known_days']==finance['totals']['total_days'] else None,
                      'records':finance['totals']['records'],'known_days':finance['totals']['known_days'],'total_days':finance['totals']['total_days']},
            'date_basis':'message_local_date_without_timezone','payment_executed':False,'final_settlement':False}


def evidence(store,source,record_id=None,incident_key=None):
    with store.connect() as db:
        batch=db.execute('SELECT summary_json,actor,imported_at FROM delivery_chat_batches WHERE source_sha256=?',(source,)).fetchone()
        if not batch:raise DomainError('Lote no encontrado.',404)
        if record_id:
            row=db.execute('SELECT payload_json FROM delivery_chat_records WHERE source_sha256=? AND record_id=?',(source,record_id)).fetchone()
            if not row:raise DomainError('Ficha no encontrada.',404)
            record=json.loads(row[0]);ids=[record['message_id'],*record.get('support_message_ids',[])]
        else:
            record=next((i for i in context_incidents(json.loads(batch['summary_json'])) if i['key']==incident_key),None)
            if not record:raise DomainError('Incidencia no encontrada.',404)
            ids=record['message_ids']
        messages=[]
        for identifier in dict.fromkeys(ids):
            row=db.execute('SELECT payload_json FROM delivery_chat_messages WHERE source_sha256=? AND message_ordinal=?',(source,identifier)).fetchone()
            if row:messages.append(json.loads(row[0]))
    return {'record':record,'messages':messages,'source_sha256':source,'imported_at':batch['imported_at'],'actor':batch['actor'],
            'ordinal_notice':'Los identificadores y líneas pertenecen a la auditoría del export; no son IDs nativos de WhatsApp.'}
