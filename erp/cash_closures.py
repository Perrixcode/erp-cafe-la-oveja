"""One versioned closure per register/day/shift. Source evidence never posts ledger money."""
import base64,hashlib,json,re,sqlite3,unicodedata
from datetime import date,datetime,timezone
from pathlib import Path
from uuid import uuid4
from erp.domain import DomainError
from erp import cash_bags as bags,cash_closure_calc as calc,cash_closure_imports as imports,cash_business_day as calendar

PREFIX='/api/cash-closures'
ID=r'[a-f0-9]{32}'
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def initialize(store):
 with store.connect() as db:
  db.executescript(Path(__file__).with_name('cash_closure_schema.sql').read_text())
  from erp import cash_review
  cash_review.initialize(db)
  from erp import cash_toteat
  cash_toteat.initialize(db)
  from erp import cash_insights
  cash_insights.initialize(db)
  from erp import cashier_v2
  cashier_v2.initialize(db)
def actor(account):return account.get('realm','erp')+':'+account['username']
def partner(account):return account.get('role')=='partner' and account.get('realm','erp')=='erp'
def allow(account):
 if account.get('role') not in ('partner','cashier','bag_cashier'):raise DomainError('Tu perfil no permite cierres.',403)
def key(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
def get(db,cid,account):
 allow(account);r=db.execute('SELECT * FROM cl_closures WHERE id=?',(cid,)).fetchone()
 if not r:raise DomainError('Cierre no encontrado.',404)
 if not partner(account) and actor(account) not in (r['creator_key'],json.loads(r['responsible_json']).get('key')):raise DomainError('Solo puedes abrir tus propios cierres.',403)
 return dict(r)
def identity(db,values,existing_day=None):
 calc.shape(values,('business_date','register_id','shift_name','external_session_key'),'Identificación')
 day=values.get('business_date')
 try:
  if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day or day<'2026-10-16' or day>datetime.now(calc.TZ).date().isoformat():raise ValueError()
 except ValueError:raise DomainError('Usa una fecha comercial desde el 16/10/2026 hasta hoy, en Chile.') from None
 if day!=existing_day and not calendar.is_operational(date.fromisoformat(day)):raise DomainError('El día operativo es de lunes a viernes. Conserva el día del turno aunque lo cierres después.')
 register=db.execute('SELECT * FROM cl_registers WHERE id=?',(values.get('register_id'),)).fetchone()
 if not register:raise DomainError('Selecciona una caja configurada.')
 shift=bags.text(values.get('shift_name'),'Turno',100);external=values.get('external_session_key') or None
 if external:
  found=[]
  for r in db.execute("SELECT normalized_json FROM cc_source_reads WHERE business_date=? AND endpoint='collection'",(day,)):
   found.extend(s for s in json.loads(r[0]).get('sessions',[]) if s['session_key']==external and s['register_id']==register['provider_register_id'])
  if not found:raise DomainError('Sesión externa incompatible con la caja y fecha.')
 return {'business_date':day,'register_id':register['id'],'shift_name':shift,'shift_key':key(shift),'external_session_key':external}
def source_reference(db,row,data):
 source=data['cash_source']
 if source['type']!='toteat_sales':return None
 from erp.cash_toteat import exact_cash_reference
 return exact_cash_reference(db,row,source['read_id'])
def latest_review(db,cid):
 row=db.execute('SELECT * FROM cl_source_reviews WHERE closure_id=? ORDER BY id DESC LIMIT 1',(cid,)).fetchone()
 return dict(row) if row else None
def review_hash(row,data):
 return hashlib.sha256(bags.canonical([{k:row[k] for k in ('business_date','register_id','shift_key','external_session_key')},data]).encode()).hexdigest()
def calculation(db,row,data=None):
 from erp import cashier_v2
 if cashier_v2.is_v2(db,row['id']):return cashier_v2.legacy_calculation(db,row)
 data=imports.effective(db,row,data or json.loads(row['data_json']));review=latest_review(db,row['id'])
 if review and review['input_hash']==review_hash(row,data):review=dict(review,input_hash=calc.input_hash(data))
 else:review=None
 value=calc.calculate(data,review,source_reference(db,row,data))
 try:movement_links(db,row,data)
 except DomainError as error:
  value.update(expected=None,difference=None,coverage='incomplete',can_reconcile=False,can_submit=False)
  value['missing'].append('Revisa los vínculos de movimientos antes de presentar.')
  value['warnings'].append('Revisar vínculo de origen: '+str(error))
 value['source_reference']=source_reference(db,row,data)
 value['imports']=data.get('imports',[])
 from erp.cash_review import payment_comparison
 comparison=payment_comparison(db,dict(row,data=data,calculation=value))
 if comparison['source'] and (not comparison['source']['valid'] or comparison['totals']['pending_methods'] or comparison['totals']['different_methods']):
  value['can_reconcile']=False
  value['warnings'].append('La fuente Toteat por medios tiene diferencias o campos pendientes; revisa cada medio antes de conciliar.')
  cash=next(m for m in comparison['rows'] if m['key']=='cash')
  if cash['difference'] not in (None,0):
   value.update(expected=None,difference=None,coverage='incomplete')
   value['warnings'].append('El efectivo Toteat transcrito difiere del cobro declarado; el esperado requiere revisar nuevamente sus fuentes.')
 return value
def movement_links(db,row,data):
 """Resolve aliases to one physical event; no automatic postings or payment creation."""
 references=[];seen=set();register=dict(db.execute('SELECT * FROM cl_registers WHERE id=?',(row['register_id'],)).fetchone())
 for m in data['movements']:
  refs=[]
  if m['reference']:refs.append(('manual:'+m['source']+':'+key(m['reference']),row['register_id']))
  if m['source']=='erp_event' and not m['cash_event_id']:raise DomainError('Selecciona el movimiento ERP existente.')
  if m['cash_event_id']:
   event=db.execute('SELECT * FROM cc_cash_events WHERE id=?',(m['cash_event_id'],)).fetchone()
   if not event or not register['location_id']:raise DomainError('El movimiento requiere una caja vinculada a su ubicación de efectivo.')
   direction='out' if event['from_id']==register['location_id'] else 'in' if event['to_id']==register['location_id'] else None
   if direction!=m['direction'] or event['amount']!=m['amount'] or event['occurred_at']!=m['occurred_at']:raise DomainError('Monto, hora o dirección no coinciden con el movimiento existente.')
   refs.append(('event:'+event['id'],row['register_id']))
   for p in db.execute('SELECT payment_source,payment_id FROM cc_payment_links WHERE event_id=?',(event['id'],)):
    from erp.cash_control import payment
    payment(db,p['payment_source'],p['payment_id']) # invalidated supplier evidence must be reviewed
    refs.append(('payment:'+p['payment_source']+':'+str(p['payment_id']),'global'))
  if m['payment_id']:
   from erp.cash_control import payment
   p=payment(db,m['payment_source'],m['payment_id'])
   if m['kind'] not in ('supplier','expense') or m['amount'] is None or p['amount']!=m['amount']:raise DomainError('Vincula el mismo pago en efectivo por su importe completo.')
   refs.append(('payment:'+m['payment_source']+':'+str(m['payment_id']),'global'))
   linked=list(db.execute('SELECT event_id FROM cc_payment_links WHERE payment_source=? AND payment_id=?',(m['payment_source'],m['payment_id'])))
   if linked and (len(linked)!=1 or m['cash_event_id']!=linked[0]['event_id']):raise DomainError('Este pago ya tiene un egreso físico: selecciona ese mismo evento, sin crear otro.')
  if m['toteat_id']:refs.append(('toteat-transcribed:'+m['toteat_id'],row['register_id']))
  if m['attachment_id'] and not db.execute('SELECT 1 FROM cl_attachments WHERE id=? AND closure_id=?',(m['attachment_id'],row['id'])).fetchone():raise DomainError('Comprobante ajeno a este cierre.')
  for ref,scope in set(refs):
   if (ref,scope) in seen:raise DomainError('Egreso duplicado por sus vínculos: registra el movimiento físico una sola vez.')
   seen.add((ref,scope));owner=db.execute('SELECT closure_id,movement_id FROM cl_movement_claims WHERE reference_key=? AND register_scope=?',(ref,scope)).fetchone()
   if owner and (owner['closure_id']!=row['id'] or owner['movement_id']!=m['id']):raise DomainError('Esta operación física ya está asociada a otro movimiento/cierre.',409)
   references.append((ref,scope,row['id'],m['id']))
 return references
def validate_times(data):
 fields=[('counted_at','Fecha y hora del conteo',data['counted_at']),('sealed_at','Fecha y hora de cierre de la bolsa',data['sealed_at']),('opening.as_of','Fecha y hora del fondo al abrir',data['opening']['as_of'])]
 fields.extend(('movements.'+str(i)+'.occurred_at','Fecha y hora del movimiento '+str(i+1),m['occurred_at']) for i,m in enumerate(data['movements']))
 now=stamp()
 for path,label,value in fields:
  if value and value>now:raise calc.FieldError(path,label+': no puede estar en el futuro. Ahora es '+calc.local_time(now)+'. Ingresa la fecha y hora reales en Chile.')
  if value and path!='opening.as_of' and value<'2026-10-16T03:00:00+00:00':raise calc.FieldError(path,label+': debe ser desde el 16/10/2026 a las 00:00 de Chile. La fecha comercial se indica por separado.')
def presentation(db,row,history=False):
 result=dict(row);result['data']=json.loads(result.pop('data_json'));result['responsible']=json.loads(result.pop('responsible_json'))
 result['register_name']=db.execute('SELECT name FROM cl_registers WHERE id=?',(row['register_id'],)).fetchone()[0]
 result['calculation']=calculation(db,row);result['source_review']=latest_review(db,row['id'])
 if row['status']=='reconciled' and not result['calculation']['can_reconcile']:
  result['stored_status']=row['status'];result['status']='pending';result['source_changed']=True
 delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(row['id'],)).fetchone()
 result['delivery']=dict(delivery) if delivery else None
 result['bag']=bags.project(bags.row_for(db,row['bag_id']),db) if row['bag_id'] else None
 if result['bag']:
  b=result['bag'];result['delivery_status']='difference' if b['status']=='difference' else 'counted' if b['verified_amount'] is not None else delivery['state'] if delivery else 'reserved'
 else:result['delivery_status']='unlinked'
 result['attachments']=[dict(r) for r in db.execute('SELECT id,name,mime,bytes,sha256 FROM cl_attachments WHERE closure_id=?',(row['id'],))]
 from erp.cash_review import payment_comparison
 result['payment_comparison']=payment_comparison(db,result)
 from erp.cash_toteat import snapshot
 frozen=db.execute("SELECT calculation_json FROM cl_versions WHERE closure_id=? AND action='submit' ORDER BY version DESC LIMIT 1",(row['id'],)).fetchone() if row['submitted_at'] else None
 result['toteat_source']=json.loads(frozen[0]).get('toteat_source') if frozen else snapshot(db,row)
 result['toteat_source_frozen']=bool(row['submitted_at'])
 from erp import cashier_v2
 if cashier_v2.is_v2(db,row['id']):
  v=cashier_v2.view_db(db,row);result['cashier_v2']={'folio':v['folio'],'deposit_status':v['deposit_status'],'revision':v['revision'],'status':v['status'],'summary':v['summary'],'live_summary':v['live_summary'],'signature':v['signature'],'events':v['events'],'history':v['submitted_history'],'photos':v['photos'],'missing_evidence_reasons':v['missing_evidence_reasons'],'note':v['note'],'reference_attachments':v['reference_attachments']}
 if history:result['history']=[dict(r,data=json.loads(r['data_json']),calculation=json.loads(r['calculation_json'])) for r in db.execute('SELECT * FROM cl_versions WHERE closure_id=? ORDER BY version DESC',(row['id'],))]
 return result
def view(store,cid,account):
 with store.connect() as db:return presentation(db,get(db,cid,account),True)
def submission_review_db(db,row,account):
 result=presentation(db,row,True)
 token=hashlib.sha256(bags.canonical([actor(account),account.get('role'),result]).encode()).hexdigest()
 return {'closure':result,'review_token':token}
def submission_review(store,cid,account):
 with store.connect() as db:
  db.execute('BEGIN')
  return submission_review_db(db,get(db,cid,account),account)
def listing(store,account):
 allow(account)
 with store.connect() as db:
  rows=list(db.execute('SELECT * FROM cl_closures '+('' if partner(account) else "WHERE creator_key=? OR json_extract(responsible_json,'$.key')=? ")+'ORDER BY business_date DESC,updated_at DESC LIMIT 200',() if partner(account) else (actor(account),actor(account))))
  return {'closures':[presentation(db,dict(r)) for r in rows],'registers':[dict(r) for r in db.execute('SELECT id,name,provider_register_id,location_id FROM cl_registers ORDER BY name')],'can_admin':partner(account),'today':calendar.operational_day(datetime.now(calc.TZ).date()).isoformat(),'calendar_today':datetime.now(calc.TZ).date().isoformat(),'denominations':calc.DENOMINATIONS,'paper_notice':'Marcha blanca: conserva el papel como respaldo. Requiere conexión; no funciona sin internet.'}
def options(store,cid,account):
 with store.connect() as db:
  from erp import cash_toteat
  row=get(db,cid,account);reg=db.execute('SELECT * FROM cl_registers WHERE id=?',(row['register_id'],)).fetchone();sales=[]
  source=cash_toteat.project(db,row['business_date'],row['register_id'],row['external_session_key'])
  sessions=[dict(s,read_id=source['sources']['collection']['read_id']) for s in source['sessions']]
  if row['external_session_key'] and source['sources']['sales']['read_id']:
   reference=cash_toteat.exact_cash_reference(db,row,source['sources']['sales']['read_id'])
   sales=[dict(read_id=reference['read_id'],read_at=reference['read_at'],net=reference['net'] if reference['valid'] else None,issues=[] if reference['valid'] else [reference['reason']])]
  events=[dict(r) for r in db.execute('SELECT * FROM cc_cash_events WHERE business_date=? AND (from_id=? OR to_id=?) ORDER BY occurred_at',(row['business_date'],reg['location_id'],reg['location_id']))] if reg['location_id'] else []
  return {'sales':sales,'sessions':sessions,'events':events,'accounting_available':False,'toteat':source,'notice':'La carga Toteat se muestra por sesión. Apertura, efectivo esperado y movimientos requieren revisión; no se consulta accountingmovements.'}
def reserve_bag(db,row,bag_id,allow_existing=False):
 if row.get('bag_id'):
  if bag_id!=row['bag_id']:raise DomainError('La bolsa reservada no puede liberarse ni reutilizarse.',409)
  return
 b=bags.row_for(db,bag_id)
 if b['status']!='blank' and not allow_existing:raise DomainError('Esta bolsa ya se utilizó. Su número y QR son de un solo uso.',409)
 if b['status']=='void':raise DomainError('La bolsa está anulada y no puede reutilizarse.',409)
 owner=db.execute('SELECT id FROM cl_closures WHERE bag_id=?',(bag_id,)).fetchone()
 if owner and owner[0]!=row['id']:raise DomainError('La bolsa ya está reservada por otro cierre.',409)
 db.execute('UPDATE cl_closures SET bag_id=? WHERE id=?',(bag_id,row['id']));row['bag_id']=bag_id
def sync_bag(db,row,value,account,reason):
 before=dict(bags.row_for(db,row['bag_id']));responsible=json.loads(row['responsible_json']);now=stamp()
 if before['status']=='void':raise DomainError('Bolsa anulada: requiere revisión.',409)
 values={'business_date':row['business_date'],'register_name':db.execute('SELECT name FROM cl_registers WHERE id=?',(row['register_id'],)).fetchone()[0],
  'shift_name':row['shift_name'],'responsible':responsible['name'],'counted':value['cash_at_seal'],'retained':value['B'],'bag_amount':value['C']}
 if before['status']!='blank' and all(before[k]==v for k,v in values.items()):return
 changed_amount=before['bag_amount']!=value['C'];status='pending' if changed_amount or before['status']=='blank' else before['status']
 fields=','.join(k+'=?' for k in values)
 db.execute('UPDATE cash_bags SET '+fields+',status=?,version=version+1,updated_at=?,declared_at=COALESCE(declared_at,?),declared_by=COALESCE(declared_by,?) WHERE id=?',(*values.values(),status,now,now,actor(account),row['bag_id']))
 if before['verified_amount'] is not None:
  difference=before['verified_amount']-value['C']
  db.execute('UPDATE cash_bags SET status=?,verified_difference=? WHERE id=?',('difference' if difference else 'verified',difference,row['bag_id']))
 after=dict(bags.row_for(db,row['bag_id']));after.update(closure_id=row['id'],physical_count_A=value['A'],post_count_net=value['post_count_net'],correction_reason=reason,
  declaration_identity={'account_id':responsible['key'],'account_name':responsible['name'],'declared_person':responsible['name'],'person_verified':not responsible.get('shared'),'authentication':'existing_account'})
 db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',(row['bag_id'],before['batch_id'],'complete' if before['status']=='blank' else 'correct',actor(account),bags.canonical(before),bags.canonical(after),now))
def version(db,row,action,account,reason):
 row=dict(db.execute('SELECT * FROM cl_closures WHERE id=?',(row['id'],)).fetchone());value=calculation(db,row)
 from erp.cash_toteat import snapshot
 value['toteat_source']=snapshot(db,row)
 if row['bag_id']:
  b=bags.row_for(db,row['bag_id']);value['delivery_count']={k:b[k] for k in ('bag_amount','verified_amount','verified_difference','verified_at','verified_by')}
 db.execute('INSERT INTO cl_versions VALUES(?,?,?,?,?,?,?,?,?)',(row['id'],row['version'],action,actor(account),reason,row['status'],row['data_json'],bags.canonical(value),stamp()))
 return row

def execute(store,action,payload,account,cid=None,identity_check=None):
 allow(account)
 fields={'register':('name','provider_register_id','location_id'),
  'create':('identity','data','bag_id','responsible_cashier_id','declared_name'),
  'adopt':('identity','data','bag_id','reason'), 'save':('data','identity','bag_id','reason'),
  'submit':('review_token','reason'),'review':('opening_verified','payments_verified','movements_verified','evidence','reason'),
  'reconcile':('reason',),'deliver':(),'receive':(),'count':('verified_amount','reason'),
  'import-source':('source_token','reason'),'resolve-import':('import_id','resolution','amount','attachment_id','reason'),
  'attach':('file_name','file_type','file_data','reason')}
 if action not in fields:raise DomainError('Operación no disponible.',404)
 calc.shape(payload,('request_id','version','confirmed',*fields[action]),'Operación')
 rid=payload.get('request_id')
 if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',rid):raise DomainError('Falta la clave de operación.')
 if payload.get('confirmed') is not True:raise DomainError('Confirma expresamente la operación.')
 sig=hashlib.sha256(bags.canonical([action,cid,payload,actor(account)]).encode()).hexdigest()
 admin=action in ('register','adopt','review','reconcile','receive','count')
 if admin and not partner(account):raise DomainError('Esta acción requiere una persona autorizada.',403)
 with store.connect() as db:
  db.execute('BEGIN IMMEDIATE')
  if identity_check and not identity_check(db):raise DomainError('La sesión venció o fue revocada.',401)
  previous=db.execute('SELECT * FROM cl_requests WHERE request_id=?',(rid,)).fetchone()
  if previous:
   if previous['signature']!=sig:raise DomainError('La clave ya se usó con otros datos.',409)
   return json.loads(previous['response_json'])
  now=stamp();reason=calc.optional_text(payload.get('reason'),'Motivo de corrección',1500) or ''
  if action=='register':
   name=bags.text(payload.get('name'),'Nombre de caja',100);provider=calc.optional_text(payload.get('provider_register_id'),'ID de caja Toteat',40)
   if provider and not provider.isdigit():raise DomainError('ID de caja Toteat no válido.')
   if any(key(r[0])==key(name) for r in db.execute('SELECT name FROM cl_registers')):raise DomainError('La caja ya está configurada.',409)
   loc=payload.get('location_id') or None
   if loc and not db.execute('SELECT 1 FROM cc_locations WHERE id=?',(loc,)).fetchone():raise DomainError('Ubicación de efectivo inexistente.')
   reg=uuid4().hex;db.execute('INSERT INTO cl_registers VALUES(?,?,?,?,?,?)',(reg,name,provider,loc,actor(account),now));result={'register_id':reg}
  else:
   if action in ('create','adopt'):
    values=identity(db,payload.get('identity',{}));cid=uuid4().hex
    responsible={'key':actor(account),'name':account.get('name',account['username']),'shared':bool(account.get('shared'))}
    if responsible['shared']:responsible['name']=bags.text(payload.get('declared_name'),'Persona responsable',100)
    if payload.get('responsible_cashier_id'):
     if not partner(account):raise DomainError('No puedes declarar otra identidad.',403)
     p=db.execute('SELECT id,name,shared FROM bag_cashiers WHERE id=? AND enabled=1',(payload['responsible_cashier_id'],)).fetchone()
     if not p:raise DomainError('Cuenta responsable no disponible.')
     responsible={'key':'bag:'+p['id'],'name':p['name'],'shared':bool(p['shared'])}
     if p['shared']:responsible['name']=bags.text(payload.get('declared_name'),'Persona responsable',100)
    legacy=None
    if action=='adopt':
     legacy=bags.row_for(db,payload.get('bag_id'));trace=bags.traceability(db,legacy['id']);original=trace['original_declaration']
     if not original or legacy['status']=='blank':raise DomainError('Selecciona una entrega existente.')
     original_id=(original.get('identity') or {}).get('account_id')
     responsible={'key':('bag:'+original_id) if original_id else original['actor'],'name':original['responsible'],'shared':bool((original.get('identity') or {}).get('shared')),'original_at':original['created_at'],'legacy':True}
     if values['business_date']!=legacy['business_date']:raise DomainError('La fecha debe coincidir con la entrega original.')
    data=calc.normalize(payload.get('data') or calc.empty(2));validate_times(data)
    if data.get('imports'):raise DomainError('No se admite trazabilidad enviada por el cliente.')
    if data.get('schema_version')==2 and not partner(account) and any(m['used'] is not False and (m['sales'] is not None or m['tip'] is not None) for m in data['methods']):raise DomainError('El desglose interno requiere una cuenta autorizada.',403)
    db.execute('INSERT INTO cl_closures(id,business_date,register_id,shift_name,shift_key,external_session_key,creator_key,responsible_json,last_editor,status,version,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,\'draft\',1,?,?,?)',
      (cid,values['business_date'],values['register_id'],values['shift_name'],values['shift_key'],values['external_session_key'],actor(account),bags.canonical(responsible),actor(account),bags.canonical(data),now,now))
    row=get(db,cid,account)
    if payload.get('bag_id'):reserve_bag(db,row,payload['bag_id'],action=='adopt')
    if legacy:
     reason='Vinculación a entrega existente; importes e historial conservados. '+reason
     db.execute("INSERT INTO cl_deliveries(closure_id,bag_id,state) VALUES(?,?,'prepared')",(cid,legacy['id']))
   else:
    row=get(db,cid,account)
    from erp import cashier_v2
    if cashier_v2.is_v2(db,cid) and action not in ('deliver','receive','count'):
     raise DomainError('Este cierre usa V2. Conserva su firma e historial y usa las acciones V2.',409)
    if type(payload.get('version')) is not int or payload['version']!=row['version']:raise DomainError('El cierre cambió. Vuelve a abrirlo sin sobrescribir otra versión.',409)
    if action=='save':
     if row['submitted_at'] and (not partner(account) or not reason):raise DomainError('Corregir un cierre presentado requiere una persona autorizada y motivo.',403 if not partner(account) else 400)
     data=calc.normalize(payload.get('data',{}));validate_times(data)
     values=identity(db,payload.get('identity') or {k:row[k] for k in ('business_date','register_id','shift_name','external_session_key')},row['business_date'])
     candidate=dict(row,**values);data=imports.save_guard(db,row,candidate,data,partner(account));movement_links(db,candidate,data);calc.calculate(data)
     if row['data_json']==bags.canonical(data) and all(row[k]==v for k,v in values.items()) and payload.get('bag_id',row['bag_id'])==row['bag_id']:
      result=presentation(db,row,True);result['unchanged']=True;db.execute('INSERT INTO cl_requests VALUES(?,?,?)',(rid,sig,bags.canonical(result)));return result
     if payload.get('bag_id'):reserve_bag(db,row,payload['bag_id'])
     db.execute("UPDATE cl_closures SET business_date=?,register_id=?,shift_name=?,shift_key=?,external_session_key=?,data_json=?,status='draft' WHERE id=?",(values['business_date'],values['register_id'],values['shift_name'],values['shift_key'],values['external_session_key'],bags.canonical(data),cid))
    elif action in ('import-source','resolve-import'):
     if row['submitted_at'] and (not partner(account) or not reason):raise DomainError('Resolver un cierre presentado requiere una cuenta autorizada y motivo.',403)
     data=imports.import_source(db,row,payload,actor(account),now) if action=='import-source' else imports.resolve(db,row,payload,actor(account),now,partner(account))
     data=calc.normalize(data)
     db.execute("UPDATE cl_closures SET data_json=?,status='draft' WHERE id=?",(bags.canonical(data),cid))
    elif action=='submit':
     token=payload.get('review_token')
     if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):raise DomainError('Abre la revisión final antes de confirmar el cierre.')
     if token!=submission_review_db(db,row,account)['review_token']:raise DomainError('Los datos o las fuentes cambiaron. Revisa nuevamente antes de confirmar el cierre.',409)
     if row['status']!='draft':raise DomainError('Este cierre ya fue presentado; no genera otra entrega.',409)
     if row['submitted_at'] and (not partner(account) or not reason):raise DomainError('La corrección presentada requiere permiso de corrección y motivo.',403 if not partner(account) else 400)
     data=json.loads(row['data_json']);validate_times(data);value=calculation(db,row)
     if not value['can_submit']:raise DomainError('No se puede presentar: '+' '.join(value['missing']))
     if not row['bag_id']:raise DomainError('Vincula una bolsa QR disponible antes de presentar.')
     claims=movement_links(db,row,data)
     for claim in claims:db.execute('INSERT OR IGNORE INTO cl_movement_claims VALUES(?,?,?,?)',claim)
     sync_bag(db,row,value,account,reason)
     delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(cid,)).fetchone()
     if not delivery:db.execute("INSERT INTO cl_deliveries(closure_id,bag_id,state) VALUES(?,?,'prepared')",(cid,row['bag_id']))
     db.execute("UPDATE cl_closures SET status=?,submitted_at=COALESCE(submitted_at,?) WHERE id=?",('submitted' if value['expected'] is not None and value['difference']==0 else 'pending',now,cid))
    elif action=='review':
     if not reason:raise DomainError('Indica el motivo o resultado de la revisión de fuentes.')
     fields=[payload.get(k) for k in ('opening_verified','payments_verified','movements_verified')]
     if any(type(v) is not bool for v in fields):raise DomainError('Revisa apertura, cobros y movimientos por separado.')
     data=json.loads(row['data_json']);validate_times(data);movement_links(db,row,data)
     if fields[0] and any(data['opening'][k] is None for k in ('amount','as_of','reference')):raise DomainError('No se puede verificar una apertura incompleta.')
     if fields[2] and not data['movements_complete']:raise DomainError('Primero completa la relación de movimientos.')
     evidence=bags.text(payload.get('evidence'),'Evidencia revisada',1500)
     db.execute('INSERT INTO cl_source_reviews(closure_id,input_hash,opening_verified,payments_verified,movements_verified,evidence,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(cid,review_hash(row,data),*map(int,fields),evidence,actor(account),now))
     if row['status']=='reconciled':db.execute("UPDATE cl_closures SET status='pending' WHERE id=?",(cid,))
    elif action=='reconcile':
     value=calculation(db,row)
     if row['status'] not in ('submitted','pending') or not value['can_reconcile']:raise DomainError('Conciliar requiere cierre presentado, apertura/cobertura revisadas y diferencia A − esperado igual a cero.')
     movement_links(db,row,json.loads(row['data_json']))
     if not reason:raise DomainError('Deja constancia de la conciliación.')
     db.execute("UPDATE cl_closures SET status='reconciled' WHERE id=?",(cid,))
    elif action in ('deliver','receive','count'):
     if not row['submitted_at'] or row['status']=='draft':raise DomainError('Primero presenta el cierre completo.')
     delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(cid,)).fetchone()
     if not delivery:raise DomainError('No hay entrega vinculada.')
     if action=='deliver':
      if delivery['state']!='prepared':raise DomainError('La entrega ya está registrada.',409)
      db.execute("UPDATE cl_deliveries SET state='delivered',delivered_at=?,delivered_by=? WHERE closure_id=?",(now,actor(account),cid))
     elif action=='receive':
      if delivery['state']!='delivered':raise DomainError('Registra primero la entrega física; una recepción no puede duplicarse.',409)
      db.execute("UPDATE cl_deliveries SET state='received',received_at=?,received_by=? WHERE closure_id=?",(now,actor(account),cid))
     else:
      if delivery['state'] not in ('delivered','received'):raise DomainError('La bolsa todavía no está entregada.')
      from erp.cash_review import apply_count
      apply_count(db,row,payload.get('verified_amount'),reason,account)
    elif action=='attach':
     if row['submitted_at'] and not partner(account):raise DomainError('Una persona autorizada debe añadir evidencia a un cierre presentado.',403)
     if row['submitted_at'] and not reason:raise DomainError('Indica el motivo para añadir evidencia a un cierre presentado.')
     mime=payload.get('file_type');name=bags.text(payload.get('file_name'),'Nombre del comprobante',120)
     if mime not in ('application/pdf','image/png','image/jpeg'):raise DomainError('Usa PDF, PNG o JPEG.')
     try:body=base64.b64decode(payload.get('file_data',''),validate=True)
     except (ValueError,TypeError):raise DomainError('Archivo no válido.') from None
     if not 0<len(body)<=2*1024*1024:raise DomainError('El comprobante admite hasta 2 MiB.')
     if not ((mime=='application/pdf' and body.startswith(b'%PDF-')) or (mime=='image/png' and body.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/jpeg' and body.startswith(b'\xff\xd8\xff'))):raise DomainError('El contenido no coincide con el formato declarado.')
     digest=hashlib.sha256(body).hexdigest()
     db.execute('INSERT OR IGNORE INTO cl_attachments VALUES(?,?,?,?,?,?,?,?,?)',(uuid4().hex,cid,name,mime,digest,len(body),body,actor(account),now))
    else:raise DomainError('Operación no disponible.',404)
    db.execute('UPDATE cl_closures SET version=version+1,last_editor=?,updated_at=? WHERE id=?',(actor(account),now,cid))
   row=version(db,get(db,cid,account),action,account,reason)
   result=presentation(db,row,True)
  db.execute('INSERT INTO cl_requests VALUES(?,?,?)',(rid,sig,bags.canonical(result)))
  return result

def bag_projection(db,bag_id):
 if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cl_closures'").fetchone():return None
 row=db.execute('SELECT * FROM cl_closures WHERE bag_id=?',(bag_id,)).fetchone()
 if not row:return None
 submitted=db.execute("SELECT calculation_json FROM cl_versions WHERE closure_id=? AND action='submit' ORDER BY version DESC LIMIT 1",(row['id'],)).fetchone()
 value=json.loads(submitted[0]) if submitted else None
 return {'closure_id':row['id'],'closure_status':row['status'],'physical_count_A':value['A'] if value else None,'post_count_net':value['post_count_net'] if value else None,'cash_at_seal':value['cash_at_seal'] if value else None}

def handle(handler,store,path,query,account,identity_check=None):
 from erp import cash_review, cashier_v2
 if path==cashier_v2.PREFIX or path.startswith(cashier_v2.PREFIX+'/'):return cashier_v2.handle(handler,store,path,query,account,identity_check)
 if path in (PREFIX+'/toteat',PREFIX+'/toteat/refresh'):
  from erp import cash_toteat
  return cash_toteat.handle(handler,store,path,query,account,identity_check)
 if path==cash_review.PREFIX or path.startswith(cash_review.PREFIX+'/'):return cash_review.handle(handler,store,path,query,account,identity_check)
 allow(account);match=re.fullmatch(PREFIX+'/('+ID+r')(?:/(save|submit|review|reconcile|deliver|receive|count|attach|import-source|resolve-import|options|submission-review|preview.pdf|download.pdf|attachments/('+ID+')))?',path)
 if handler.command=='GET':
  if path==PREFIX:return handler.send(200,listing(store,account))
  if match:
   cid=match[1];suffix=match[2]
   if suffix=='submission-review':return handler.send(200,submission_review(store,cid,account))
   if suffix=='options':return handler.send(200,options(store,cid,account))
   if suffix and suffix.startswith('attachments/'):
    with store.connect() as db:
     get(db,cid,account);r=db.execute('SELECT * FROM cl_attachments WHERE id=? AND closure_id=?',(match[3],cid)).fetchone()
     if not r:raise DomainError('Comprobante no encontrado.',404)
     return handler.send(200,r['content'],r['mime'],{'Content-Disposition':'attachment; filename="comprobante-'+r['id']+'"'})
   value=view(store,cid,account)
   if suffix in ('preview.pdf','download.pdf'):
    if query.get('version') and query['version']!=[str(value['version'])]:raise DomainError('La versión cambió. Abre el cierre nuevamente antes de revisar el PDF.',409)
    from erp.cash_closure_pdf import render
    if value.get('cashier_v2'):
     from erp.cashier_v2_pdf import render as render_v2
     with store.connect() as db:body=render_v2(db,cashier_v2.view_db(db,get(db,cid,account)),None)
     return handler.send(200,body,'application/pdf',{'Content-Disposition':('inline' if suffix=='preview.pdf' else 'attachment')+'; filename="cierre-v2-'+cid[:8]+'.pdf"'})
    return handler.send(200,render(value),'application/pdf',{'Content-Disposition':('inline' if suffix=='preview.pdf' else 'attachment')+'; filename="cierre-'+cid[:8]+'.pdf"'})
   if not suffix:return handler.send(200,value)
 if handler.command=='POST':
  action=path[len(PREFIX)+1:] if not match else match[2]
  if action in ('create','adopt','register') or match:
   if handler.headers.get('Origin') and handler.headers.get('Origin')!=handler.headers.get('Host') and handler.headers.get('Origin') not in ('http://'+handler.headers.get('Host',''),'https://'+handler.headers.get('Host','')):raise DomainError('Origen externo rechazado.',403)
   try:return handler.send(200,execute(store,action,handler.body(maximum=3*1024*1024 if action=='attach' else 65536),account,match[1] if match else None,identity_check))
   except sqlite3.IntegrityError as exc:
    if 'UNIQUE' in str(exc):raise DomainError('Ya existe un cierre para esa caja/fecha/turno, una bolsa reservada o una referencia vinculada. Abre el registro existente.',409) from None
    raise
 raise DomainError('Ruta de cierres no disponible.',404)
