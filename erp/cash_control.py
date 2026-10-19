"""Cash control: conservative read models and explicit, partner-only custody commands."""
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from erp.domain import DomainError
from erp.cash_bags import canonical,stamp,text,integer,project,traceability
from erp import cash_control_sources as sources

TZ=ZoneInfo('America/Santiago')
PREFIX='/api/cash-control'


def initialize(store):
    with store.connect() as db:db.executescript(Path(__file__).with_name('cash_control_schema.sql').read_text())


def day(value):
    try:
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
        return value
    except (ValueError,TypeError):raise DomainError('Fecha no válida.') from None


def instant(value):
    try:
        moment=datetime.fromisoformat(value.replace('Z','+00:00'))
        if moment.tzinfo is None:raise ValueError()
        return moment.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError,AttributeError):raise DomainError('El corte requiere fecha, hora y zona horaria.') from None


def identifier(value,optional=False):
    if optional and value in (None,''):return None
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise DomainError('Referencia no válida.')
    return value


def midnight(value):
    return datetime.combine(date.fromisoformat(value),datetime.min.time(),TZ).astimezone(timezone.utc).isoformat(timespec='seconds')


def filters(query,start_date):
    allowed={'start','end','register','shift','scope'}
    if set(query)-allowed or any(len(v)!=1 for v in query.values()):raise DomainError('Filtros no válidos.')
    today=datetime.now(TZ).date().isoformat()
    start=day(query.get('start',[today])[0]);end=day(query.get('end',[start])[0])
    if end<start or (date.fromisoformat(end)-date.fromisoformat(start)).days>365:raise DomainError('Selecciona un rango de hasta 366 días, en orden.')
    scope=query.get('scope',['operations'])[0]
    if scope not in {'operations','tests'}:raise DomainError('Ámbito no válido.')
    result=dict(start=start,end=end,effective_start=max(start,start_date),control_start=start_date,scope=scope)
    for key in ['register','shift']:
        value=query.get(key,[''])[0]
        if not isinstance(value,str) or len(value)>120:raise DomainError('Filtro de caja/turno no válido.')
        result[key]=value
    return result


def metric(value,state='recorded',reason='',source='ERP',**extra):
    return dict(value=value,state=state if value is not None else 'missing',reason=reason,source=source,**extra)


def rollup(rows):
    counted=[b for b in rows if b['verified_amount'] is not None]
    pending=[b for b in rows if b['status']=='pending']
    return dict(declarations=len(rows),verified=len(counted),pending=len(pending),
        declared=sum(b['bag_amount'] for b in rows) if rows else None,
        counted=sum(b['verified_amount'] for b in counted) if counted else None,
        pending_amount=sum(b['bag_amount'] for b in pending) if rows else None,
        difference=sum(b['verified_amount']-b['bag_amount'] for b in counted) if counted else None,
        state='recorded_subset' if rows else 'missing')


def position(db,location,at):
    opening=db.execute('SELECT * FROM cc_openings WHERE location_id=?',(location,)).fetchone()
    if not opening or opening['as_of']>=at:return None
    net=db.execute('''SELECT COALESCE(SUM(CASE WHEN to_id=? THEN amount ELSE -amount END),0)
      FROM cc_cash_events WHERE (from_id=? OR to_id=?) AND occurred_at>=? AND occurred_at<?''',
      (location,location,location,opening['as_of'],at)).fetchone()[0]
    return opening['amount']+net


def payment(db,source,key):
    if source not in {'ap_payments','ap_account_entries'} or type(key) is not int:raise DomainError('Referencia de pago no válida.')
    row=db.execute('SELECT * FROM '+source+' WHERE id=?',(key,)).fetchone()
    if not row or row['voided_at'] or row['method']!='cash' or (source=='ap_account_entries' and row['kind']!='payment'):
        raise DomainError('Se requiere un pago existente, vigente y en efectivo.')
    return dict(row)


def ledger_view(db,f):
    start=midnight(f['effective_start']);end=midnight((date.fromisoformat(f['end'])+timedelta(days=1)).isoformat())
    locations=[]
    for r in db.execute('SELECT * FROM cc_locations ORDER BY name'):
        item=dict(r);opening=db.execute('SELECT * FROM cc_openings WHERE location_id=?',(r['id'],)).fetchone()
        item['opening']=dict(opening) if opening else None
        item['initial_balance']=opening['amount'] if opening and opening['as_of']==start else position(db,r['id'],start)
        item['final_balance']=position(db,r['id'],end) if start<end else None
        item['state']='tracked_only' if item['final_balance'] is not None else 'opening_required'
        locations.append(item)
    events=[dict(r) for r in db.execute('SELECT * FROM cc_cash_events WHERE occurred_at>=? AND occurred_at<? ORDER BY occurred_at DESC,id',(start,end))] if start<end else []
    for row in events:
        row['payment_links']=[dict(r) for r in db.execute('SELECT * FROM cc_payment_links WHERE event_id=?',(row['id'],))]
        for link in row['payment_links']:
            try:payment(db,link['payment_source'],link['payment_id']);link['state']='linked'
            except DomainError:link['state']='payment_requires_review'
    external=sum(r['amount']*(1 if r['kind']=='income' else -1 if r['kind']=='expense' else 0) for r in events)
    payments=[dict(r) for r in db.execute('''SELECT p.id,p.amount,p.paid_on day,s.name supplier,'ap_payments' source
      FROM ap_payments p JOIN ap_invoices i ON i.id=p.invoice_id JOIN ap_suppliers s ON s.id=i.supplier_id
      WHERE p.method='cash' AND p.voided_at IS NULL AND p.paid_on>=? ORDER BY p.paid_on DESC,p.id DESC LIMIT 200''',(f['control_start'],))]
    payments += [dict(r) for r in db.execute('''SELECT p.id,p.amount,p.occurred_on day,s.name supplier,'ap_account_entries' source
      FROM ap_account_entries p JOIN ap_suppliers s ON s.id=p.supplier_id
      WHERE p.kind='payment' AND p.method='cash' AND p.voided_at IS NULL AND p.occurred_on>=?
      ORDER BY p.occurred_on DESC,p.id DESC LIMIT 200''',(f['control_start'],))]
    return dict(locations=locations,events=events,payments=payments,
        net_recorded=metric(external if events else None,'recorded_subset','Solo movimientos registrados; no acredita flujo completo.'),
        absolute_balance=metric(None,reason='El saldo físico global requiere saldo inicial y cobertura completa por ubicación.'),
        notice='Las transferencias internas conservan el total. Abrir/contar una bolsa no registra automáticamente traslados ni ingresos.')


def overview(store,query):
    with store.connect() as db:
        db.execute('BEGIN') # One coherent snapshot for rows, metrics and versions.
        settings=dict(db.execute('SELECT * FROM cc_settings WHERE singleton=1').fetchone())
        f=filters(query,settings['start_date'])
        rows=[project(r,db) for r in db.execute('''SELECT c.* FROM cash_bags c JOIN cash_bag_batches b ON b.id=c.batch_id
          WHERE c.business_date BETWEEN ? AND ? AND c.status IN ('pending','verified','difference') AND b.is_test=?
          ORDER BY c.business_date,c.register_name,c.shift_name,c.number''',(f['effective_start'],f['end'],int(f['scope']=='tests')))]
        options={'registers':sorted({r['register_name'] for r in rows}),'shifts':sorted({r['shift_name'] for r in rows})}
        reads=sources.latest(db,f['effective_start'],f['end']);sessions=[];sales=[]
        for r in reads:
            normalized=json.loads(r['normalized_json'])
            for session in normalized.get('sessions',[]):sessions.append(dict(session,read_id=r['id'],read_at=r['read_at']))
            for group in normalized.get('registers',[]):sales.append(dict(group,business_date=r['business_date'],read_at=r['read_at']))
        # Filter values identify ERP text or a Toteat ID explicitly; never infer cross-system matches.
        for s in sessions:
            options['registers'].append('Toteat:'+s['register_id']);options['shifts'].append('Toteat:'+s['shift_id'])
        for g in sales:options['registers'].append('Toteat:'+g['register_id'])
        options={k:sorted(set(v)) for k,v in options.items()}
        options['register_labels']={'Toteat:'+s['register_id']:s['register_name']+' · Toteat' for s in sessions+sales}
        options['shift_labels']={'Toteat:'+s['shift_id']:s['shift_name']+' · Toteat' for s in sessions}
        all_sessions={s['session_key']:s for s in sessions}
        rows=[b for b in rows if (not f['register'] or b['register_name']==f['register']) and (not f['shift'] or b['shift_name']==f['shift'])]
        sessions=[s for s in sessions if (not f['register'] or 'Toteat:'+s['register_id']==f['register']) and (not f['shift'] or 'Toteat:'+s['shift_id']==f['shift'])]
        sales=[s for s in sales if (not f['register'] or 'Toteat:'+s['register_id']==f['register']) and not f['shift']]
        for b in rows:
            trace=traceability(db,b['id']);b.update(original_declaration=trace['original_declaration'],last_editor=trace['last_editor'])
            link=db.execute('SELECT * FROM cc_closure_links WHERE bag_id=?',(b['id'],)).fetchone()
            b['closure_link']=dict(link) if link else None
            b['reconciliation']='source_pending';b['expected']=None;b['closure_difference']=None
            b['linked_cash_reported']=None;b['observed_comparison']=None
            if link:
                current=all_sessions.get(link['session_key'])
                prior=db.execute('SELECT payload_hash FROM cc_source_reads WHERE id=?',(link['source_read_id'],)).fetchone()
                current_read=next((r for r in reads if current and r['id']==current['read_id']),None)
                if current and current_read and prior and current_read['payload_hash']==prior['payload_hash']:
                    b['linked_cash_reported']=current['cash_reported']
                    b['observed_comparison']=b['counted']-current['cash_reported'] if current['cash_reported'] is not None else None
                else:b['reconciliation']='source_changed'
            if b.get('closure_id'):
                from erp import cash_closures
                digital=dict(db.execute('SELECT * FROM cl_closures WHERE id=?',(b['closure_id'],)).fetchone())
                value=cash_closures.calculation(db,digital)
                b['digital_closure']={'id':digital['id'],'status':'pending' if digital['status']=='reconciled' and not value['can_reconcile'] else digital['status'],'coverage':value['coverage'],'draft_correction':digital['status']=='draft'}
                # A draft correction cannot replace the last presented bag or a reviewed expectation.
                b['expected']=value['expected'] if digital['status']!='draft' else None
                b['closure_difference']=value['difference'] if digital['status']!='draft' else None
                b['reconciliation']='reconciled' if digital['status']=='reconciled' and value['can_reconcile'] else 'source_pending'
        grouped={};daily=[]
        for b in rows:grouped.setdefault((b['business_date'],b['register_name'],b['shift_name']),[]).append(b)
        groups=[dict(business_date=k[0],register=k[1],shift=k[2],**rollup(v)) for k,v in grouped.items()]
        cursor=date.fromisoformat(f['effective_start']);last=date.fromisoformat(f['end'])
        while cursor<=last:
            d=cursor.isoformat();daily.append(dict(day=d,**rollup([b for b in rows if b['business_date']==d])));cursor+=timedelta(days=1)
        sums=rollup(rows)
        latest_issues=[dict(r) for r in db.execute('''SELECT endpoint,reason,read_at,business_date FROM cc_source_reads WHERE id IN
          (SELECT MAX(id) FROM cc_source_reads GROUP BY endpoint) AND state='error' ''')]
        coverage=[dict(day=r['business_date'],endpoint=r['endpoint'],state=r['state'],reason=r['reason'],read_at=r['read_at']) for r in reads]
        observed_days={r['business_date'] for r in reads if r['state']!='error'}
        metrics=dict(
          declared=metric(sums['declared'],'recorded_subset','Suma de C en fichas del rango; no saldo disponible.'),
          counted=metric(sums['counted'],'partial' if sums['pending'] else 'recorded_subset','Conteo físico confirmado solo de las fichas verificadas.'),
          pending=metric(sums['pending_amount'],'recorded_subset','C declarado aún pendiente de conteo.'),
          difference=metric(sums['difference'],'recorded_subset','Conteo menos C, solo en fichas contadas.'),
          expected=metric(None,source='Toteat',reason='Movimientos y semántica del cierre pendientes; no hay esperado completo.'),
          received=metric(None,reason='No se infiere recepción por guardar la declaración ni por un traslado pendiente.'))
        return dict(filters=f,version=settings['version'],as_of=stamp(),timezone='America/Santiago',metrics=metrics,
            counts=sums,rows=rows,groups=groups,daily=daily,options=options,source_sessions=sessions,source_sales=sales,
            coverage=dict(days_requested=len(daily),days_with_observations=len(observed_days),complete_days=0,
                          sources=coverage,latest_issues=latest_issues,state='incomplete'),
            ledger=ledger_view(db,f),
            notice='Control desde el 16/10/2026. El efectivo anterior requiere confirmación. Datos sin cobertura se muestran sin información.')


def execute(store,action,data,account):
    if account.get('role')!='partner':raise DomainError('Solo una persona autorizada puede configurar o registrar control de efectivo.',403)
    fields={
      'location':{'name','kind'},'opening':{'location_id','amount','as_of','evidence'},
      'movement':{'kind','amount','from_id','to_id','occurred_at','reference','note'},
      'payment-link':{'event_id','payment_source','payment_id','amount'},
      'closure-link':{'bag_id','session_key','read_id'},
    }
    if action not in fields:raise DomainError('Operación no disponible.',404)
    if set(data)-fields[action]-{'request_id','confirmed','version'}:raise DomainError('Campos no admitidos.')
    request_id=data.get('request_id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',request_id):raise DomainError('Falta clave de operación.')
    if data.get('confirmed') is not True:raise DomainError('Confirma expresamente la operación.')
    actor=account['username'];signature=hashlib.sha256(canonical([action,data,actor]).encode()).hexdigest()
    try:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM cc_requests WHERE request_id=?',(request_id,)).fetchone()
            if prior:
                if prior['signature']!=signature:raise DomainError('Clave de operación usada con otros datos.',409)
                return json.loads(prior['response_json'])
            config=db.execute('SELECT * FROM cc_settings WHERE singleton=1').fetchone()
            if type(data.get('version')) is not int or data['version']!=config['version']:raise DomainError('Los datos cambiaron. Actualiza antes de guardar.',409)
            now=stamp();result={}
            if action=='location':
                kind=data.get('kind');name=text(data.get('name'),'Ubicación',100)
                if kind not in {'register','safe','custody','transit'}:raise DomainError('Tipo de ubicación no válido.')
                key=uuid4().hex;db.execute('INSERT INTO cc_locations VALUES(?,?,?,?,?)',(key,name,kind,actor,now));result={'id':key}
            elif action=='opening':
                location=identifier(data.get('location_id'));amount=integer(data.get('amount'),'Saldo inicial')
                at=instant(data.get('as_of'));evidence=text(data.get('evidence'),'Evidencia del saldo y corte',500)
                if at<midnight(config['start_date']) or at>now:raise DomainError('El corte debe estar entre el inicio del control y ahora.')
                if db.execute('SELECT 1 FROM cc_openings WHERE location_id=?',(location,)).fetchone():raise DomainError('Esta apertura ya fue confirmada. Requiere revisión para corregirla.',409)
                if db.execute('SELECT 1 FROM cc_cash_events WHERE (from_id=? OR to_id=?) AND occurred_at<?',(location,location,at)).fetchone():
                    raise DomainError('El corte debe preceder los movimientos ya registrados.',409)
                db.execute('INSERT INTO cc_openings VALUES(?,?,?,?,?,?)',(location,amount,at,evidence,actor,now))
                # A newly confirmed opening must not create a negative historical running balance.
                running=amount
                for event in db.execute('SELECT * FROM cc_cash_events WHERE from_id=? OR to_id=? ORDER BY occurred_at,id',(location,location)):
                    running+=event['amount']*(1 if event['to_id']==location else -1)
                    if running<0:raise DomainError('El saldo inicial no cubre los movimientos registrados.',409)
                result={'location_id':location,'amount':amount,'as_of':at}
            elif action=='movement':
                kind=data.get('kind');amount=integer(data.get('amount'),'Monto');from_id=identifier(data.get('from_id'),True);to_id=identifier(data.get('to_id'),True)
                if not amount:raise DomainError('El movimiento requiere un monto mayor que cero.')
                if not ((kind=='transfer' and from_id and to_id and from_id!=to_id) or (kind=='income' and not from_id and to_id) or (kind=='expense' and from_id and not to_id)):
                    raise DomainError('Origen y destino incompatibles con el tipo de movimiento.')
                at=instant(data.get('occurred_at'))
                if at<midnight(config['start_date']) or at>now:raise DomainError('Movimiento fuera del período de control.')
                for loc in filter(None,[from_id,to_id]):
                    if not db.execute('SELECT 1 FROM cc_locations WHERE id=?',(loc,)).fetchone():raise DomainError('Ubicación inexistente.')
                    opening=db.execute('SELECT * FROM cc_openings WHERE location_id=?',(loc,)).fetchone()
                    if opening and at<opening['as_of']:raise DomainError('El movimiento precede el corte inicial.')
                    newer=db.execute('SELECT MAX(occurred_at) FROM cc_cash_events WHERE from_id=? OR to_id=?',(loc,loc)).fetchone()[0]
                    if newer and at<newer:raise DomainError('El movimiento precede otro registrado. Revisa el orden.',409)
                # Flows may be recorded before an opening; balances remain unknown until confirmed.
                balance=position(db,from_id,'9999-12-31T00:00:00+00:00') if from_id else None
                if balance is not None and balance<amount:raise DomainError('Saldo registrado insuficiente.',409)
                key=uuid4().hex;reference=text(data.get('reference'),'Referencia única del hecho',180);note=text(data.get('note'),'Motivo',500)
                db.execute('INSERT INTO cc_cash_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,kind,amount,from_id,to_id,at,datetime.fromisoformat(at).astimezone(TZ).date().isoformat(),reference,note,actor,now))
                result={'id':key,'kind':kind,'global_effect':0 if kind=='transfer' else amount*(1 if kind=='income' else -1)}
            elif action=='payment-link':
                event=db.execute('SELECT * FROM cc_cash_events WHERE id=?',(identifier(data.get('event_id')),)).fetchone()
                if not event or event['kind']!='expense':raise DomainError('Vincula el pago al egreso físico existente; un retiro a custodia es un traslado.')
                p=payment(db,data.get('payment_source'),data.get('payment_id'));amount=integer(data.get('amount'),'Monto vinculado')
                event_used=db.execute('SELECT COALESCE(SUM(amount),0) FROM cc_payment_links WHERE event_id=?',(event['id'],)).fetchone()[0]
                payment_used=db.execute('SELECT COALESCE(SUM(amount),0) FROM cc_payment_links WHERE payment_source=? AND payment_id=?',(data['payment_source'],p['id'])).fetchone()[0]
                if amount<=0 or event_used+amount>event['amount'] or payment_used+amount>p['amount']:raise DomainError('El vínculo supera el egreso o el pago pendiente de vincular.',409)
                db.execute('INSERT INTO cc_payment_links VALUES(?,?,?,?,?,?)',(event['id'],data['payment_source'],p['id'],amount,actor,now))
                result={'event_id':event['id'],'global_effect':0}
            else:
                if not isinstance(data.get('bag_id'),str) or not isinstance(data.get('session_key'),str) or type(data.get('read_id')) is not int:
                    raise DomainError('Referencia de sesión no válida.')
                bag=db.execute('SELECT * FROM cash_bags WHERE id=?',(data.get('bag_id'),)).fetchone()
                read=db.execute("SELECT * FROM cc_source_reads WHERE id=? AND endpoint='collection' AND state='partial'",(data.get('read_id'),)).fetchone()
                session=next((s for s in json.loads(read['normalized_json']).get('sessions',[]) if s['session_key']==data.get('session_key')),None) if read else None
                if not bag or bag['status'] not in {'pending','verified','difference'} or not session or bag['business_date']!=session['business_date']:
                    raise DomainError('La ficha y sesión no son compatibles con la fecha elegida.')
                latest=db.execute("SELECT MAX(id) FROM cc_source_reads WHERE endpoint='collection' AND business_date=?",(read['business_date'],)).fetchone()[0]
                if latest!=read['id']:raise DomainError('La lectura de origen cambió. Actualiza antes de vincular.',409)
                db.execute('INSERT INTO cc_closure_links VALUES(?,?,?,?,?)',(bag['id'],session['session_key'],read['id'],actor,now))
                result={'bag_id':bag['id'],'reconciliation':'source_pending'}
            db.execute('UPDATE cc_settings SET version=version+1 WHERE singleton=1')
            result['version']=config['version']+1
            db.execute('INSERT INTO cc_audit(action,actor,payload_json,created_at) VALUES(?,?,?,?)',(action,actor,canonical(data),now))
            db.execute('INSERT INTO cc_requests VALUES(?,?,?)',(request_id,signature,canonical(result)))
            return result
    except sqlite3.IntegrityError:raise DomainError('Registro duplicado o referencia no disponible; no se aplicó la operación.',409) from None


def handle(handler,store,path,query,account):
    if account.get('role')!='partner':raise DomainError('Control de efectivo requiere sesión autorizada.',403)
    if handler.command=='GET' and path in {PREFIX+'/summary',PREFIX+'/closures',PREFIX+'/movements',PREFIX+'/balances',PREFIX+'/reconciliations'}:
        return handler.send(200,overview(store,query))
    if handler.command=='POST' and path.startswith(PREFIX+'/'):
        return handler.send(200,execute(store,path[len(PREFIX)+1:],handler.body(),account))
    raise DomainError('Ruta de control de efectivo no encontrada.',404)
