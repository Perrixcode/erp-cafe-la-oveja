"""Three documentary sources, immutable imports and private original attachments.

Report claims never change the chat base, courier assignment or a payment.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
import zipfile
from datetime import date
from erp.delivery_finance import encoded, now
from erp import delivery_chat as chat
from erp.domain import DomainError


def key(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))


def import_package(store,path,month,library_file_id,actor):
    if not re.fullmatch(r'\d{4}-\d{2}',month or '') or not isinstance(actor,str) or not actor.strip() or len(actor)>160 or not isinstance(library_file_id,str) or not library_file_id.startswith('libfile_'):
        raise DomainError('Falta período, responsable o identidad de Library.')
    raw=Path(path).read_bytes()
    if len(raw)>16*1024*1024:raise DomainError('Paquete demasiado grande.')
    digest=hashlib.sha256(raw).hexdigest()
    try:
        date.fromisoformat(month+'-01')
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
            if len(infos)>80 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>24*1024*1024:raise ValueError()
            manifest=json.loads(archive.read('manifest.json'))
            if manifest['schema']!='comparacion_repartos_evidencia_manifest_v1' or manifest['periodo']!=month:raise ValueError()
            files={}
            for entry in manifest['archivos']:
                name=entry['archivo'];p=PurePosixPath(name)
                if p.is_absolute() or '..' in p.parts or name in files:raise ValueError()
                content=archive.read(name)
                if len(content)!=entry['bytes'] or hashlib.sha256(content).hexdigest()!=entry['sha256']:raise ValueError()
                files[name]=content
            if len(files)!=manifest['cantidad_archivos_sin_manifest'] or set(files)|{'manifest.json'}!={i.filename for i in infos}:raise ValueError()
        summary=json.loads(files['resumen_conjunto.json'])
        rows=json.loads(files['cruce_440_importes.json'])
        cases=json.loads(files['pendientes_conciliacion.json'])
        source=summary['fuentes']['chat']['sha256']
        if summary['schema']!='comparacion_repartos_tres_fuentes_evidencia_v1' or summary['periodo']!=month or summary['moneda']!='CLP':raise ValueError()
        if not rows or len(rows)>10000 or len(cases)>500:raise ValueError()
        reports=summary['planillas'];drivers=[r['conductor'] for r in reports]
        if len(set(drivers))!=len(drivers) or any(not d or len(d)>100 for d in drivers):raise ValueError()
        documents=[]
        for report in reports:
            selected=[r for r in rows if r['conductor_informe']==report['conductor']]
            if len(selected)!=report['cantidad_importes_informe'] or sum(r['importe_informe_clp'] for r in selected)!=report['bruto_declarado_informe_clp']:raise ValueError()
            origin=summary['fuentes'][key(report['conductor'])];name=origin['original'];content=files[name]
            if Path(name).suffix not in ('.pdf','.xlsx') or hashlib.sha256(content).hexdigest()!=origin['sha256']:raise ValueError()
            for field in ('adelanto_declarado_clp','saldo_declarado_clp'):
                amount=report.get(field)
                if amount is not None and (type(amount) is not int or not 0<=amount<=10**10):raise ValueError()
            documents.append((str(len(documents)+1),report['conductor'],Path(name).name,origin['sha256'],content))
        if len(rows)!=summary['suma_dos_informes']['cantidad_importes'] or sum(r['importe_informe_clp'] for r in rows)!=summary['suma_dos_informes']['bruto_clp']:raise ValueError()
        seen=set()
        for row in rows:
            identifier=(row['conductor_informe'],row['ordinal_informe'])
            if identifier in seen or identifier[0] not in drivers or type(identifier[1]) is not int or identifier[1]<=0:raise ValueError()
            seen.add(identifier)
            if row['fecha_informe'][:7]!=month or type(row['importe_informe_clp']) is not int or not 0<=row['importe_informe_clp']<=10**8:raise ValueError()
            date.fromisoformat(row['fecha_informe'])
        if len({c['caso_id'] for c in cases})!=len(cases):raise ValueError()
        for case in cases:
            if case['asignacion_aprobada'] is not False or case['pago_aprobado'] is not False or case['fecha'][:7]!=month:raise ValueError()
        payload={'summary':summary,'rows':rows,'cases':cases,'manifest':manifest}
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT package_sha256 FROM delivery_audit_batches WHERE month=?',(month,)).fetchone()
            if old:
                if old[0]!=digest:raise DomainError('Ya existe otra versión documental de este período. Requiere revisión.',409)
                return {'imported':False,'rows':len(rows),'documents':len(documents)}
            batch=db.execute('SELECT source_sha256 FROM delivery_chat_batches WHERE month=?',(month,)).fetchone()
            if not batch or batch[0]!=source:raise ValueError()
            chat_rows={r['record_id']:json.loads(r['payload_json']) for r in db.execute('SELECT * FROM delivery_chat_records WHERE source_sha256=?',(source,))}
            for row in rows:
                original=chat_rows[row['chat_registro_id']]
                driver=None if row['conductor_explicito_chat']=='Sin conductor explícito' else row['conductor_explicito_chat']
                if driver!=original['driver']:raise ValueError()
                if any(row[field]!=original[target] for field,target in [('chat_fecha_hora','sent_at'),('importe_original_chat_clp','amount_clp'),('estado_base_original','analysis_status'),('chat_linea_inicio','line_start'),('chat_linea_fin','line_end')]):raise ValueError()
                if row['fecha_informe']!=original['sent_at'][:10]:raise ValueError()
            for report in reports:
                base=chat.totals([r for r in chat_rows.values() if r['driver']==report['conductor']])
                if base['base']!=report['bruto_base_chat_provisional_clp'] or base['base_records']!=report['cantidad_base_chat_provisional']:raise ValueError()
            for case in cases:
                if any(ref not in chat_rows for ref in re.findall(r'\d+\.\d+',case['chat_registros_id'])):raise ValueError()
            db.execute('INSERT INTO delivery_audit_batches VALUES(?,?,?,?,?,?,?)',(month,digest,library_file_id,source,encoded(payload),now(),actor))
            db.executemany('INSERT INTO delivery_audit_documents VALUES(?,?,?,?,?,?)',[(month,*d) for d in documents])
        return {'imported':True,'rows':len(rows),'documents':len(documents)}
    except (KeyError,TypeError,ValueError,AttributeError,zipfile.BadZipFile):
        raise DomainError('El paquete de comparación no supera los controles; no se importó.') from None


def view(store,month,snapshot=None):
    snapshot=snapshot or chat.view(store,month);month=snapshot['month']
    with store.connect() as db:
        batch=db.execute('SELECT * FROM delivery_audit_batches WHERE month=?',(month,)).fetchone()
        if not batch:return {'available':False,'month':month,'reports':[],'rows':[],'cases':[],'documents':[]}
        payload=json.loads(batch['payload_json'])
        documents=[dict(r) for r in db.execute('SELECT document_key,driver,filename,sha256 FROM delivery_audit_documents WHERE month=? ORDER BY document_key',(month,))]
    reports=[]
    for report in payload['summary']['planillas']:
        base=next((c for c in snapshot['couriers'] if c['driver']==report['conductor']),None)
        if base is None:raise DomainError('La base de chat requiere revisión antes del cruce.',409)
        daily=[]
        for day in snapshot['days']:
            declared=[r for r in payload['rows'] if r['conductor_informe']==report['conductor'] and r['fecha_informe']==day['day']]
            original=[r for r in snapshot['records'] if r['driver']==report['conductor'] and r['sent_at'].startswith(day['day'])]
            totals=chat.totals(original)
            amount=sum(r['importe_informe_clp'] for r in declared) if declared else None
            daily.append(dict(day=day['day'],report_amount=amount,report_count=len(declared) if declared else None,
                              chat_amount=totals['base'] if original else None,chat_count=totals['base_records'] if original else None,
                              difference=amount-totals['base'] if declared and original else None,
                              count_difference=len(declared)-totals['base_records'] if declared and original else None))
        reports.append(dict(report,chat_base=base['base'],chat_count=base['base_records'],difference=report['bruto_declarado_informe_clp']-base['base'],count_difference=report['cantidad_importes_informe']-base['base_records'],toteat=None,days=daily))
    return {'available':True,'month':month,'reports':reports,'rows':payload['rows'],'cases':payload['cases'],'documents':documents,
            'comparison':three_sources(snapshot,payload['rows'],reports),
            'imported_at':batch['imported_at'],'actor':batch['actor'],'package_sha256':batch['package_sha256'],
            'library_file_id':batch['library_file_id'],'source_sha256':batch['source_sha256'],
            'matched_by_order':False,'payment_approved':False,'limits':payload['summary'].get('restricciones',[])}


def three_sources(snapshot, claims, reports):
    """Keep global Toteat and partial, possibly overlapping reports in their own scopes."""
    drivers={r['conductor'] for r in reports}
    def period(start,end):
        days=[d for d in snapshot['days'] if start<=d['day']<=end]
        records=[r for r in snapshot['records'] if start<=r['sent_at'][:10]<=end]
        stated=[r for r in claims if start<=r['fecha_informe']<=end]
        covered=[r for r in records if r['driver'] in drivers]
        base=chat.totals(records);covered_base=chat.totals(covered)
        known=[d for d in days if d['toteat'] is not None]
        complete=bool(days) and len(known)==len(days)
        fee=sum(d['toteat'] for d in known) if complete else None
        count=sum(d['toteat_records'] for d in known) if complete else None
        claimed=sum(r['importe_informe_clp'] for r in stated) if stated else None
        return {'start':start,'end':end,'toteat_amount':fee,'toteat_count':count,
                'toteat_known_days':len(known),'days':len(days),
                'chat_amount':base['base'] if records else None,'chat_count':base['base_records'] if records else None,
                'report_amount':claimed,'report_count':len(stated) if stated else None,
                'report_chat_amount':covered_base['base'] if covered else None,
                'report_chat_count':covered_base['base_records'] if covered else None,
                'chat_toteat_difference':base['base']-fee if records and fee is not None else None,
                'report_chat_difference':claimed-covered_base['base'] if stated and covered else None,
                'chat_toteat_count_difference':base['base_records']-count if records and count is not None else None,
                'report_chat_count_difference':len(stated)-covered_base['base_records'] if stated and covered else None}
    days=[period(d['day'],d['day']) for d in snapshot['days']]
    weeks=[period(w['start'],w['end']) for w in snapshot['weeks']]
    return {'days':days,'weeks':weeks,'month':period(snapshot['days'][0]['day'],snapshot['days'][-1]['day']),
            'report_drivers':sorted(drivers),'reports_cover_global':False,'matched_by_order':False}


def document(store,month,identifier):
    with store.connect() as db:
        item=db.execute('SELECT filename,sha256,content FROM delivery_audit_documents WHERE month=? AND document_key=?',(month,identifier)).fetchone()
    if not item:raise DomainError('Documento no encontrado.',404)
    content=bytes(item['content'])
    if hashlib.sha256(content).hexdigest()!=item['sha256']:raise DomainError('Documento pendiente de verificación.',409)
    pdf=item['filename'].endswith('.pdf')
    return content,('application/pdf' if pdf else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),('pdf' if pdf else 'xlsx')
