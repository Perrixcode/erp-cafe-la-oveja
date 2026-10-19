"""Read-only provider evidence for a single register/session; never a declaration."""
from datetime import date, datetime, timedelta, timezone
import hashlib, json, re, unicodedata
from zoneinfo import ZoneInfo
from erp.domain import DomainError
from erp.cash_bags import canonical
from erp.cash_control_sources import clp, utc

TZ=ZoneInfo('America/Santiago')
FRESH_SECONDS=300

def initialize(db):
 db.execute('''CREATE TABLE IF NOT EXISTS cl_toteat_sync (
 business_date TEXT PRIMARY KEY, requested_at TEXT NOT NULL, state TEXT NOT NULL,
 started_at TEXT, finished_at TEXT, retry_at TEXT, error TEXT NOT NULL DEFAULT '')''')

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def age(at,at_now=None):
 try:return max(0,(datetime.fromisoformat(at_now or now())-datetime.fromisoformat(at)).total_seconds())
 except (TypeError,ValueError):return float('inf')
def day(value):
 try:
  if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value or value<'2026-10-16' or value>datetime.now(TZ).date().isoformat():raise ValueError()
 except ValueError:raise DomainError('Usa una fecha comercial desde el 16/10/2026 hasta hoy, en Chile.') from None
 return value

def method_key(identity,name):
 label=' '.join(''.join(c for c in unicodedata.normalize('NFKD',str(name).casefold()) if not unicodedata.combining(c)).split())
 if str(identity)=='1000' and label in ('','cash','efectivo'):return 'cash'
 # Names are evidence; an unknown provider ID is never guessed to be a card.
 return {'debit_card':'debit','credit_card':'credit','transferencias':'transfer','debit':'debit','debito':'debit','tarjeta de debito':'debit','credit':'credit','credito':'credit','tarjeta de credito':'credit','transfer':'transfer','transferencia':'transfer','transferencia bancaria':'transfer','mp emergencias':'mp_emergency'}.get(label)

def collection_methods(methods):
 result=[];seen=set()
 for m in methods:
  if not isinstance(m,dict):raise DomainError('Medio de collection no válido.')
  ident=str(m.get('paymentMethodID',''));name=str(m.get('paymentMethod',''))[:100]
  if not ident or ident in seen:raise DomainError('Medio de collection duplicado o sin identidad.')
  seen.add(ident)
  # amount is preserved as reported, without claiming sales, tips or cash balance.
  result.append(dict(provider_id=ident,name=name,key=method_key(ident,name),reported_total=clp(m['amount']) if m.get('amount') is not None else None,sales=None,tip=None))
 return result

def sales_payments(rows,scope):
 result=[];seen={}
 for row in rows:
  ident=hashlib.sha256(canonical([scope,str(row.get('orderId')),str(row.get('paymentId'))]).encode()).hexdigest()
  if ident in seen:
   if seen[ident]!=canonical(row):raise DomainError('Pago repetido con valores en conflicto.')
   continue
  seen[ident]=canonical(row)
  forms=row.get('paymentForms',[]);parts=[];issues=[]
  cash=[f for f in forms if isinstance(f,dict) and str(f.get('id'))=='1000']
  change=clp(row['change']) if row.get('change') is not None else None
  if str(row.get('fiscalType','')).upper()=='NC' or row.get('referencedPayment') or row.get('referencedPayment*'):issues.append('Devolución: requiere revisar el contrato de origen.')
  if len(cash)>1 or change is None or change<0 or (change and not cash):issues.append('Vuelto sin atribución segura.')
  for f in forms:
   if not isinstance(f,dict):issues.append('Medio incompleto.');continue
   fid=str(f.get('id',''));name=str(f.get('name') or f.get('method') or '')[:100]
   amount=clp(f['amount']) if f.get('amount') is not None else None
   net=amount
   if fid=='1000':net=amount-change if amount is not None and change is not None and len(cash)==1 else None
   if net is not None and net<0:issues.append('Importe neto incompatible.');net=None
   parts.append(dict(provider_id=fid,provider_method=str(f.get('method',''))[:100],component_key=canonical([fid,str(f.get('method','')),name]),name=name,key=method_key(fid,name),total=net,sales=None,tip=None))
  if not parts:issues.append('Pago sin medios observados.')
  # Verified against the existing provider response: 248/248 payments reconcile
  # sum(amount)-change == totalWithGratuity == total+gratuity; sum(form.tip)==gratuity.
  # Missing fields do not imply zero. A failed identity leaves the split unknown.
  if parts and not issues and all(x['total'] is not None for x in parts) and all(row.get(k) is not None for k in ('gratuity','totalWithGratuity','total')):
   gratuity=clp(row['gratuity']);gross=clp(row['totalWithGratuity']);sale=clp(row['total'])
   tips=[clp(f['tip']) for f in forms] if all(f.get('tip') is not None for f in forms) else [gratuity] if len(parts)==1 else []
   if len(tips)==len(parts) and all(0<=tip<=part['total'] for tip,part in zip(tips,parts)) and sum(tips)==gratuity and sum(x['total'] for x in parts)==gross==sale+gratuity and sale>=0:
    for part,tip in zip(parts,tips):part.update(sales=part['total']-tip,tip=tip)
  result.append(dict(payment_key=ident,order_id=str(row.get('orderId'))[:120],payment_id=str(row.get('paymentId'))[:120],register_id=str(row.get('registerId','')),paid_at=utc(row.get('dateClosed')),methods=parts,issues=issues))
 return result

def reads(db,business_day,endpoint):
 latest=db.execute('SELECT * FROM cc_source_reads WHERE business_date=? AND endpoint=? ORDER BY id DESC LIMIT 1',(business_day,endpoint)).fetchone()
 good=db.execute("SELECT * FROM cc_source_reads WHERE business_date=? AND endpoint=? AND state!='error' ORDER BY id DESC LIMIT 1",(business_day,endpoint)).fetchone()
 return (dict(latest) if latest else None,dict(good) if good else None)

def read_status(latest,good,at_now=None):
 return dict(state='error' if latest and latest['state']=='error' else 'stale' if good and age(good['read_at'],at_now)>FRESH_SECONDS else 'observed' if good else 'missing',
  last_attempt=latest['read_at'] if latest else None,last_success=good['read_at'] if good else None,read_id=good['id'] if good else None,
  payload_hash=good['payload_hash'] if good else None,error=latest['reason'] if latest and latest['state']=='error' else None,
  stale=not good or age(good['read_at'],at_now)>FRESH_SECONDS)

def session_sales(sessions,selected,payments):
 grouped={};unassigned=0;count=0;seen=set();issues=[];pending=[]
 for p in payments:
  if p['register_id']!=selected['register_id']:continue
  if p['payment_key'] in seen:continue
  seen.add(p['payment_key']);at=p.get('paid_at')
  candidates=[s for s in sessions if s['register_id']==p['register_id'] and at and s['opened_at']<=at and (not s['closed_at'] or at<s['closed_at'])]
  reason='order_close_is_not_session_identity'
  if not at:reason='missing_order_close_time'
  elif not candidates:reason='outside_session_windows'
  elif len(candidates)>1:reason='overlapping_session_windows'
  pending.append(dict(payment_key=p['payment_key'],order_id=p.get('order_id'),payment_id=p.get('payment_id'),order_closed_at=at,reason=reason,assigned_session=None))
  if len(candidates)!=1:
   unassigned+=1;continue
  if candidates[0]['session_key']!=selected['session_key']:continue
  count+=1
  if p['issues']:issues.extend(p['issues']);continue
  for m in p['methods']:
   key=m.get('component_key',canonical([m['provider_id'],m['name']]));g=grouped.setdefault(key,dict(provider_id=m['provider_id'],provider_method=m.get('provider_method',''),component_key=key,name=m['name'],key=m['key'],total=0,sales=0,tip=0,payments=0))
   if g['name']!=m['name'] or g['key']!=m['key']:issues.append('Identidad del medio cambió dentro de la lectura.')
   for field in ('total','sales','tip'):g[field]=None if g[field] is None or m[field] is None else g[field]+m[field]
   g['payments']+=1
 if issues:
  for g in grouped.values():g.update(total=None,sales=None,tip=None)
 return dict(methods=list(grouped.values()),pending_payments=pending,import_eligible=False,payments=count,unassigned_payments=unassigned,issues=sorted(set(issues)),coverage='observed_payments',complete=False,attribution='register_and_order_closed_at',exact_identity=False)

def project(db,business_day,register_id,session_key=None,at_now=None):
 business_day=day(business_day)
 reg=db.execute('SELECT * FROM cl_registers WHERE id=?',(register_id,)).fetchone()
 if not reg:raise DomainError('Selecciona una caja configurada.')
 c_latest,c_good=reads(db,business_day,'collection');s_latest,s_good=reads(db,business_day,'sales')
 all_sessions=json.loads(c_good['normalized_json']).get('sessions',[]) if c_good else []
 sessions={s['session_key']:s for s in all_sessions if s['register_id']==reg['provider_register_id']}
 selected=sessions.get(session_key) if session_key else next(iter(sessions.values())) if len(sessions)==1 else None
 sales_body=json.loads(s_good['normalized_json']) if s_good else {}
 projection=session_sales(all_sessions,selected,sales_body.get('payments',[])) if selected else None
 c_status=read_status(c_latest,c_good,at_now);s_status=read_status(s_latest,s_good,at_now)
 sync=db.execute('SELECT * FROM cl_toteat_sync WHERE business_date=?',(business_day,)).fetchone()
 result=dict(business_date=business_day,register_id=register_id,provider_register_id=reg['provider_register_id'],register_name=reg['name'],
  requested_session_key=session_key,sessions=list(sessions.values()),selected=selected,sales=projection,
  sources={'collection':c_status,'sales':s_status},sync=dict(sync) if sync else None,
  state='closed' if selected and selected['closed_at'] else 'provisional' if selected else 'pending',
  missing_session=bool(session_key and not selected),sales_contract_available='payments' in sales_body,
  notice='Ventas atribuidas provisionalmente por caja y hora de cierre de la orden; sales no entrega una identidad de sesión. Datos observados de Toteat. No confirman conteo físico, recepción ni conciliación. El efectivo reportado no es un saldo disponible ni un esperado validado. Propinas sin desglose quedan pendientes.')
 from erp.cash_closure_imports import token
 result['import_token']=token(result)
 return result

def snapshot(db,row):
 if not row.get('external_session_key'):return None
 value=project(db,row['business_date'],row['register_id'],row['external_session_key'])
 return {k:value[k] for k in ('business_date','register_id','provider_register_id','requested_session_key','selected','sales','sources','state','notice')}

def exact_cash_reference(db,row,read_id):
 if not row.get('external_session_key'):return dict(valid=False,reason='Vincula la sesión exacta de Toteat; una suma diaria no acredita un turno.')
 view=project(db,row['business_date'],row['register_id'],row['external_session_key'])
 source=view['sources']['sales'];session=view['selected'];sales=view['sales']
 read=db.execute("SELECT * FROM cc_source_reads WHERE id=? AND endpoint='sales' AND business_date=?",(read_id,row['business_date'])).fetchone()
 current=db.execute("SELECT * FROM cc_source_reads WHERE id=?",(source['read_id'],)).fetchone() if source['read_id'] else None
 cash=[m for m in (sales or {}).get('methods',[]) if m['key']=='cash']
 valid=bool(read and current and read['payload_hash']==current['payload_hash'] and read['state']!='error' and source['state']=='observed' and view['sources']['collection']['state']=='observed' and session and session['closed_at'] and sales and not sales['unassigned_payments'] and not sales['issues'] and len(cash)==1 and cash[0]['total'] is not None and sales.get('exact_identity') is True)
 return dict(valid=valid,net=cash[0]['total'] if len(cash)==1 else None,read_id=read_id,read_at=read['read_at'] if read else None,payload_hash=read['payload_hash'] if read else None,session_key=row['external_session_key'],coverage='observed_payments',reason='Ventas observadas por caja y hora de cierre de la orden. Falta identidad de sesión en sales; no acredita efectivo esperado.')

def request_refresh(store,business_day):
 business_day=day(business_day);at=now()
 with store.connect() as db:
  db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM cl_toteat_sync WHERE business_date=?',(business_day,)).fetchone()
  if row and ((row['state'] in ('queued','running') and age(row['requested_at'])<300) or (row['retry_at'] and row['retry_at']>at) or (row['finished_at'] and age(row['finished_at'])<90)):return dict(row)
  # At most seven queued days; every request is one business day, not a range.
  if db.execute("SELECT COUNT(*) FROM cl_toteat_sync WHERE state IN ('queued','running')").fetchone()[0]>=7:raise DomainError('Hay consultas pendientes. Espera antes de solicitar otra fecha.',429)
  db.execute("INSERT INTO cl_toteat_sync(business_date,requested_at,state) VALUES(?,?,'queued') ON CONFLICT(business_date) DO UPDATE SET requested_at=excluded.requested_at,state='queued',error='',retry_at=NULL",(business_day,at))
  return dict(db.execute('SELECT * FROM cl_toteat_sync WHERE business_date=?',(business_day,)).fetchone())

def handle(handler,store,path,query,account,identity_check=None):
 from erp import cash_closures as cl
 cl.allow(account)
 if handler.command=='GET' and path==cl.PREFIX+'/toteat':
  if set(query)-{'day','register_id','session_key','closure_id'} or any(len(v)!=1 for v in query.values()):raise DomainError('Filtros de Toteat no válidos.')
  with store.connect() as db:
   if not cl.partner(account):
    owned=cl.get(db,query.get('closure_id',[None])[0],account)
    if owned['business_date']!=query.get('day',[None])[0] or owned['register_id']!=query.get('register_id',[None])[0]:raise DomainError('Guarda la identificación del cierre antes de consultar esa caja y fecha.',409)
   return handler.send(200,project(db,query.get('day',[None])[0],query.get('register_id',[None])[0],query.get('session_key',[None])[0]))
 if handler.command=='POST' and path==cl.PREFIX+'/toteat/refresh':
  if handler.headers.get('Origin') not in (None,'http://'+handler.headers.get('Host',''),'https://'+handler.headers.get('Host','')):raise DomainError('Origen externo rechazado.',403)
  payload=handler.body(maximum=2048)
  if set(payload)-{'day','closure_id'} or 'day' not in payload:raise DomainError('La actualización admite solo una fecha comercial.')
  if not cl.partner(account):
   with store.connect() as db:
    owned=cl.get(db,payload.get('closure_id'),account)
    if owned['business_date']!=payload['day']:raise DomainError('Consulta la fecha comercial de tu cierre.',403)
  if identity_check:
   with store.connect() as db:
    if not identity_check(db):raise DomainError('La sesión venció.',401)
  return handler.send(200,request_refresh(store,payload['day']))
 raise DomainError('Ruta de consulta no disponible.',404)
