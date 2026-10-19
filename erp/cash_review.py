"""Review projections and atomic counts. Never posts cash events or reconciles a closure."""
import hashlib,json,re
from datetime import date,datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
from erp.domain import DomainError
from erp import cash_closures as cl,cash_bags as bags,cash_closure_calc as calc,cash_control as cc,cash_control_sources as sources
PREFIX=cl.PREFIX+'/review-panel'

def initialize(db):
 db.executescript(Path(__file__).with_name('cash_review_schema.sql').read_text())
 for table in ('cr_counts','cr_batch_versions','cr_reviews','cr_difference_events','cr_method_sources','cr_requests'):
  for action in ('UPDATE','DELETE'):
   db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable review history'); END")

def allow(account):
 if not cl.partner(account):raise DomainError('La revisión requiere una cuenta autorizada.',403)
def digest(value):return hashlib.sha256(bags.canonical(value).encode()).hexdigest()
def query_filters(query):
 if set(query)-{'start','end','register','cashier','state'} or any(len(v)!=1 for v in query.values()):raise DomainError('Filtros no válidos.')
 today=datetime.now(calc.TZ).date();start=cc.day(query.get('start',[max(date(2026,10,16),today-timedelta(days=6)).isoformat()])[0]);end=cc.day(query.get('end',[today.isoformat()])[0])
 if start<'2026-10-16' or end<start or (date.fromisoformat(end)-date.fromisoformat(start)).days>365:raise DomainError('Usa un rango de hasta 366 días desde el 16/10/2026.')
 result=dict(start=start,end=end,register=query.get('register',[''])[0],cashier=query.get('cashier',[''])[0],state=query.get('state',['all'])[0])
 if result['state'] not in ('all','pending','presented','differences','reviewed') or any(len(result[k])>140 for k in ('register','cashier')):raise DomainError('Filtro no válido.')
 return result

def payment_comparison(db,c):
 if c['calculation'].get('v2'):
  rows=[dict(key='terminal:'+t['terminal_id'],name=t['terminal_id'],declared_sales=None,declared_tip=None,declared_total=t['declared'],toteat_sales=None,toteat_tip=None,toteat_total=t['expected'],difference=t['difference'],state='pending' if t['difference'] is None else 'comparable') for t in c['calculation']['terminals']]
  totals={k:None for k in ('declared_sales','declared_tip','toteat_sales','toteat_tip')}
  for k in ('declared_total','toteat_total','difference'):
   totals[k]=sum(r[k] for r in rows) if rows and all(r[k] is not None for r in rows) else None
  totals.update(absolute_differences=sum(abs(r['difference']) for r in rows if r['difference'] is not None) if any(r['difference'] is not None for r in rows) else None,different_methods=sum(r['difference'] not in (None,0) for r in rows),pending_methods=sum(r['difference'] is None for r in rows))
  return dict(rows=rows,totals=totals,history=[],source=None,v2=True,notice='V2: comparación individual por terminal y corte comprobado. No se exigen propinas separadas ni se compensan diferencias.')
 source=db.execute('SELECT * FROM cr_method_sources WHERE closure_id=? ORDER BY id DESC LIMIT 1',(c['id'],)).fetchone()
 valid=bool(source and source['input_hash']==cl.review_hash(c,c['data']))
 if c['data'].get('schema_version')==2 and (not c['data'].get('report_cutoff_at') or c['data']['report_cutoff_at']!=c['data']['counted_at']):valid=False
 observed=json.loads(source['methods_json']) if source else []
 bykey={m['key']:m for m in observed};rows=[]
 for m in c['calculation']['methods']:
  s=bykey.get(m['key'],{});sales=s.get('sales') if valid else None;tip=s.get('tip') if valid else None
  total=s.get('total') if valid and 'total' in s else sales+tip if sales is not None and tip is not None else None
  rows.append(dict(key=m['key'],name=calc.LABELS[m['key']],declared_sales=m['sales'],declared_tip=m['tip'],declared_total=m['total'],toteat_sales=sales,toteat_tip=tip,toteat_total=total,
   difference=m['total']-total if total is not None and m['total'] is not None else None,state='comparable' if total is not None and m['total'] is not None else 'pending'))
 def all_sum(key):return sum(r[key] for r in rows) if all(r[key] is not None for r in rows) else None
 totals={k:all_sum(k) for k in ('declared_sales','declared_tip','declared_total','toteat_sales','toteat_tip','toteat_total','difference')}
 totals['absolute_differences']=sum(abs(r['difference']) for r in rows if r['difference'] is not None) if any(r['difference'] is not None for r in rows) else None
 totals['different_methods']=sum(r['difference'] not in (None,0) for r in rows);totals['pending_methods']=sum(r['state']=='pending' for r in rows)
 history=[dict(r) for r in db.execute('SELECT id,reference,actor,created_at FROM cr_method_sources WHERE closure_id=? ORDER BY id DESC',(c['id'],))]
 return dict(rows=rows,totals=totals,history=history,source=dict(id=source['id'],reference=source['reference'],actor=source['actor'],created_at=source['created_at'],valid=valid,methods=observed) if source else None,
  notice='Fuente Toteat transcrita y verificada por una persona. La caché actual no contiene ventas y propinas comparables de todos los medios. Pendiente no significa cero. El total no compensa diferencias entre medios.')

def legacy_fingerprint(db,c):
 reads=[(r['endpoint'],r['scope'],r['payload_hash'],r['state']) for r in sources.latest(db,c['business_date'],c['business_date'])]
 method=db.execute('SELECT MAX(id) FROM cr_method_sources WHERE closure_id=?',(c['id'],)).fetchone()[0]
 return digest([c['id'],c['version'],c['bag']['version'] if c['bag'] else None,reads,method])

def fingerprint(db,c):
 method=db.execute('SELECT MAX(id) FROM cr_method_sources WHERE closure_id=?',(c['id'],)).fetchone()[0]
 if c['calculation'].get('v2'):
  value=json.loads(json.dumps(c.get('cashier_v2',{})));value.get('live_summary',{}).get('source_read',{}).pop('read_id',None);value['events']=[e for e in value.get('events',[]) if e['kind']!='admin_review']
  return 'v2:'+digest([c['id'],c['version'],c['bag']['version'] if c['bag'] else None,value,method])
 # Only relevant evidence; unrelated boxes and daily payload changes are excluded.
 evidence=c['calculation'].get('imports',[])
 automatic=c['calculation'].get('source_reference') if c['data']['cash_source']['type']=='toteat_sales' else None
 return 'v2:'+digest([c['id'],c['version'],c['bag']['version'] if c['bag'] else None,method,evidence,automatic])

def matches_fingerprint(db,c,value):
 return value==(fingerprint(db,c) if value.startswith('v2:') else legacy_fingerprint(db,c))

def v2_pending(c):
 v=c.get('cashier_v2')
 if not v:return []
 pending=[]
 def add(code,cause,action,responsible='Administración'):
  pending.append(dict(code=code,cause=cause,action=action,responsible=responsible,closure_id=c['id'],business_date=c['business_date'],register_name=c['register_name']))
 late=[e for e in v['events'] if e['kind']=='late_comparison'];live=v.get('live_summary',{});live_changed=any(live.get(k)!=v['summary'].get(k) for k in ('expected','terminals','selected_session','source_state'));comparison=late[-1]['detail']['comparison'] if late else v['summary']
 declared={p['id'] for p in v['photos']}
 extra=[p for p in v.get('reference_attachments',[]) if p['id'] not in declared]
 for kind in ('mp','toteat'):
  images=[p for p in v['photos'] if p['kind']==kind]
  if not any(p['legible'] for p in images):
   supplemental=any(p['kind']==kind for p in extra)
   if not (supplemental and c['reviewed']):add('photo_'+kind,'Respaldo '+kind.upper()+(' posterior por revisar' if supplemental else ' ilegible' if images else ' ausente'),'Revisar el respaldo posterior' if supplemental else 'Adjuntar un respaldo independiente en el cierre; conservar el motivo original')
 if comparison.get('expected') is None:add('cash_source','Fuente comparable de efectivo pendiente','Verificar apertura, ventas y movimientos del mismo corte; no inferir cero')
 for t in comparison.get('terminals',[]):
  if t['difference'] is None:add('terminal_cut','Corte no comparable: '+t['terminal_id'],'Revisar terminal, período y respaldo; registrar comparación posterior si el cierre ya está firmado')
 if v['status']=='submitted':
  if not any(e['kind']=='deposit_safe' for e in v['events']):add('deposit','Depósito en caja fuerte pendiente','Registrar el depósito cuando ocurra',c['responsible']['name'])
  if not c['delivery'] or c['delivery']['state']!='received':add('receipt','Bolsa sin recepción registrada','Confirmar entrega física y registrar recepción cuando ocurran')
  if not c['bag'] or c['bag']['verified_amount'] is None:add('count','Bolsa sin conteo administrativo','Contar físicamente la bolsa recibida y registrar el resultado')
 if not c['reviewed']:add('late_review' if late or live_changed else 'human_review','Fuentes tardías pendientes de revisión humana' if late or live_changed else 'Revisión humana pendiente','Abrir declaración, evidencias y comparaciones; dejar constancia de revisión')
 if c['has_differences'] and c['resolution_state']!='resolved':add('difference','Diferencia sin resolución revisada','Documentar y revisar la resolución; no modificar el importe firmado')
 return pending

def detail(db,c):
 c=dict(c);fp=fingerprint(db,c);events=[dict(r) for r in db.execute('SELECT * FROM cr_difference_events WHERE closure_id=? ORDER BY id',(c['id'],))]
 last=events[-1] if events else None;review=db.execute('SELECT * FROM cr_reviews WHERE closure_id=? ORDER BY id DESC LIMIT 1',(c['id'],)).fetchone()
 b=c['bag'];delivery=c['delivery'];alerts=[]
 if c['submitted_at'] and (not delivery or delivery['state']=='prepared'):alerts.append('closure_without_delivery')
 if delivery and delivery['state'] in ('delivered','received') and b and b['verified_amount'] is None:alerts.append('delivery_without_count')
 if delivery and delivery['delivered_at']:
  versions=list(db.execute('SELECT version,action,created_at FROM cl_versions WHERE closure_id=? ORDER BY version',(c['id'],)))
  first=next((v['version'] for v in versions if v['action']=='deliver' and v['created_at']>=delivery['delivered_at']),None)
  if first is None:first=next((v['version'] for v in versions if v['action']=='submit' and v['created_at']>=delivery['delivered_at']),None)
  if any(v['action'] in ('save','submit') and ((first is not None and v['version']>first) or v['created_at']>delivery['delivered_at']) for v in versions):alerts.append('modified_after_delivery')
 comp=payment_comparison(db,c);late=[e for e in c.get('cashier_v2',{}).get('events',[]) if e['kind']=='late_comparison'];latest=late[-1]['detail']['comparison'] if late else None
 differences=bool(latest and (latest.get('difference') not in (None,0) or any(t['difference'] not in (None,0) for t in latest['terminals']))) or any(v not in (None,0) for v in (c['calculation']['difference'],b['verified_difference'] if b else None)) or comp['totals']['different_methods']>0
 reviewed=bool(review and matches_fingerprint(db,c,review['fingerprint']))
 c.update(fingerprint=fp,alerts=alerts,payment_comparison=comp,review=dict(review) if review else None,reviewed=reviewed,has_differences=differences,
  review_history=[dict(r) for r in db.execute('SELECT * FROM cr_reviews WHERE closure_id=? ORDER BY id DESC',(c['id'],))],resolution_events=events,resolution_state=('resolved' if last['kind']=='accept' else 'proposed' if last['kind']=='propose' else 'open') if last and matches_fingerprint(db,c,last['fingerprint']) else 'open')
 if c.get('cashier_v2'):
  human=[e for e in c['cashier_v2']['events'] if e['kind']=='admin_review']
  c['v2_human_review']=dict(human[-1],current=reviewed) if human else None
  c['v2_pending']=v2_pending(c)
 c['flags']=dict(pending=c['status'] in ('draft','pending') or bool(alerts) or not reviewed,presented=bool(c['submitted_at']),differences=differences,reviewed=reviewed)
 return c

def get_detail(store,cid,account):
 allow(account)
 with store.connect() as db:
  db.execute('BEGIN');return detail(db,cl.presentation(db,cl.get(db,cid,account),True))

def overview_db(db,query,account):
 f=query_filters(query);registers=[dict(r) for r in db.execute('SELECT id,name,provider_register_id,location_id FROM cl_registers ORDER BY name')]
 all_rows=[dict(r) for r in db.execute('SELECT * FROM cl_closures ORDER BY business_date DESC,id')]
 people={json.loads(r['responsible_json'])['key']:json.loads(r['responsible_json'])['name'] for r in all_rows}
 def matches(r):return (not f['register'] or r['register_id']==f['register']) and (not f['cashier'] or json.loads(r['responsible_json'])['key']==f['cashier'])
 selected=[r for r in all_rows if matches(r) and f['start']<=r['business_date']<=f['end']]
 rows=[detail(db,cl.presentation(db,r)) for r in selected]
 counts={k:sum(r['flags'][k] for r in rows) for k in ('pending','presented','differences','reviewed')}
 alerts=[dict(kind=a,closure_id=r['id'],business_date=r['business_date'],register_name=r['register_name'],bag=r['bag']['label'] if r['bag'] else None) for r in rows for a in r['alerts']]
 # Only observed closed sessions prove a missing closure. No calendar/register assumptions.
 reads=sources.latest(db,f['start'],f['end']);observed=0
 if not f['cashier']:
  for read in reads:
   if read['endpoint']!='collection' or read['state']=='error':continue
   for s in json.loads(read['normalized_json']).get('sessions',[]):
    if not cl.calendar.is_operational(date.fromisoformat(s['business_date'])):continue
    regs=[r for r in registers if r['provider_register_id']==s['register_id'] and (not f['register'] or r['id']==f['register'])]
    if not regs or not s.get('closed_at'):continue
    observed+=1;reg=regs[0];same=[r for r in selected if r['register_id']==reg['id'] and r['business_date']==s['business_date']]
    linked=any(r['external_session_key']==s['session_key'] for r in same) or db.execute('SELECT 1 FROM cc_closure_links WHERE session_key=?',(s['session_key'],)).fetchone()
    if not linked:
     linked=any((lambda d: d is not None and json.loads(d[0])['identity']['session_key']==s['session_key'])(db.execute('SELECT declaration_json FROM cv2_drafts WHERE closure_id=?',(r['id'],)).fetchone()) for r in same)
    if not linked:alerts.append(dict(kind='register_without_closure' if not same else 'session_link_pending',business_date=s['business_date'],register_name=reg['name'],session_key=s['session_key'],read_id=read['id']))
 summary=period_summary(db,rows,[r for r in all_rows if matches(r)],f)
 batches=[dict(r) for r in db.execute('SELECT * FROM cr_batches ORDER BY updated_at DESC,id')]
 candidates=[candidate(db,r['id'],account) for r in db.execute("SELECT id FROM cash_bags WHERE status='pending' ORDER BY business_date,number LIMIT 500")]
 return dict(v2_pending_tasks=[p for c in rows for p in c.get('v2_pending',[])],candidates=candidates,filters=f,registers=registers,cashiers=[dict(key=k,name=v) for k,v in sorted(people.items(),key=lambda v:v[1])],counts=counts,
  rows=[r for r in rows if f['state']=='all' or r['flags'][f['state']]],alerts=alerts,observed_closed_sessions=observed,summary=summary,batches=batches,
  notice='Solo registros existentes. Sin sesión de caja observada no se puede afirmar que falte un cierre. Las alertas de caja sin cierre no se atribuyen a una cajera sin evidencia.')

def overview(store,query,account):
 allow(account)
 with store.connect() as db:
  db.execute('BEGIN');result=overview_db(db,query,account)
  result['snapshot']=digest({k:result[k] for k in ('filters','registers','cashiers','summary','alerts','notice')})
  return result

def amount_metric(values):
 known=[v for v in values if v is not None]
 return dict(value=sum(known) if known else None,records=len(values),known=len(known),pending=len(values)-len(known),complete=bool(values) and len(known)==len(values))

def period_summary(db,rows,all_rows,f):
 # Use last submitted snapshots even while corrections are still drafts.
 declared=[];prepared=[];delivered=[];counted=[];retained=[];withdrawals={};lines=[]
 for c in rows:
  snap=db.execute("SELECT calculation_json,data_json FROM cl_versions WHERE closure_id=? AND action='submit' ORDER BY version DESC LIMIT 1",(c['id'],)).fetchone()
  value=json.loads(snap[0]) if snap else {};data=json.loads(snap[1]) if snap else {};b=c['bag'];d=c['delivery'];sent=bool(d and d['state'] in ('delivered','received'))
  declared.append(value.get('A'));prepared.append(value.get('C'));retained.append(value.get('B'))
  delivered.append(b['bag_amount'] if sent and b else None)
  counted.append(b['verified_amount'] if b else None)
  lines.append(dict(closure_id=c['id'],date=c['business_date'],register=c['register_name'],cashier=c['responsible']['name'],bag=b['label'] if b else None,declared=value.get('C'),delivered=b['bag_amount'] if sent and b else None,retained=value.get('B'),counted=b['verified_amount'] if b else None,counted_at=b['verified_at'] if b else None,correction_pending=bool(c['status']=='draft' and c['submitted_at'])))
  for m in data.get('movements',[]):
   if m['kind']=='custody' and m['direction']=='out':
    identity='event:'+m['cash_event_id'] if m['cash_event_id'] else c['id']+':'+m['id']
    withdrawals[identity]=dict(closure_id=c['id'],reference=m['reference'],amount=m['amount'],date=c['business_date'],destination=m['destination'],state='assignment_pending')
 counted_in_period=[]
 # Unlinked legacy deliveries have no reliable configured-register/cashier identity.
 if not f['register'] and not f['cashier']:
  all_rows=all_rows+[dict(bag_id=r['id'],business_date=r['business_date']) for r in db.execute('SELECT b.id,b.business_date FROM cash_bags b WHERE b.verified_amount IS NOT NULL AND NOT EXISTS(SELECT 1 FROM cl_closures c WHERE c.bag_id=b.id)')]
 for r in all_rows:
  if not r['bag_id']:continue
  b=bags.row_for(db,r['bag_id'])
  if b['verified_amount'] is None or not b['verified_at']:continue
  count_day=datetime.fromisoformat(b['verified_at']).astimezone(calc.TZ).date().isoformat()
  if f['start']<=count_day<=f['end']:counted_in_period.append(dict(bag_id=b['id'],label=f"BOL-{b['number']:06d}",closure_date=r['business_date'],count_date=count_day,counted_at=b['verified_at'],amount=b['verified_amount']))
 return dict(by_closure_date=dict(declared=amount_metric(declared),prepared=amount_metric(prepared),delivered=amount_metric(delivered),counted=amount_metric(counted),retained=amount_metric(retained),withdrawals_pending=amount_metric([v['amount'] for v in withdrawals.values()])),
  by_count_date=amount_metric([r['amount'] for r in counted_in_period]),counts_in_period=counted_in_period,withdrawals=list(withdrawals.values()),rows=lines,
  notice='Declarado A, preparado C, entregado y contabilizado son etapas del mismo efectivo: no se suman entre sí. El fondo retenido es la suma de declaraciones, no un saldo disponible acumulado. Los retiros a custodia no se asignan a pagos sin un vínculo explícito; no se infiere que estén gastados. Los borradores de corrección conservan la última presentación en este resumen. Los totales por fecha de cierre incluyen cierres digitales; los conteos por fecha incluyen también bolsas antiguas solo cuando no hay filtro de caja/cajera, porque carecen de un vínculo de identidad verificado.')

def has_count(db,b):
 return b['verified_amount'] is not None or db.execute("SELECT 1 FROM cash_bag_events WHERE bag_id=? AND action='verify' LIMIT 1",(b['id'],)).fetchone() is not None or db.execute('SELECT 1 FROM cr_counts WHERE bag_id=?',(b['id'],)).fetchone() is not None

def candidate(db,bag_id,account):
 b=dict(bags.row_for(db,bag_id));row=db.execute('SELECT * FROM cl_closures WHERE bag_id=?',(bag_id,)).fetchone()
 result=dict(bag_id=bag_id,label=f"BOL-{b['number']:06d}",bag_version=b['version'],already_counted=has_count(db,b),amount=b['verified_amount'],eligible=False)
 if not row:
  eligible=bool(not result['already_counted'] and b['status']=='pending' and b['bag_amount'] is not None)
  return dict(result,eligible=eligible,legacy=True,closure_id=None,closure_version=None,business_date=b['business_date'],register_name=b['register_name'],declared_amount=b['bag_amount'],cashier=b['responsible'],reason='Ya contabilizada: no se añade nuevamente.' if result['already_counted'] else '' if eligible else 'La bolsa debe tener una entrega declarada vigente.')
 c=cl.get(db,row['id'],account);delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(c['id'],)).fetchone()
 result.update(closure_id=c['id'],closure_version=c['version'],business_date=c['business_date'],register_name=db.execute('SELECT name FROM cl_registers WHERE id=?',(c['register_id'],)).fetchone()[0],declared_amount=b['bag_amount'],cashier=json.loads(c['responsible_json'])['name'])
 result['eligible']=bool(not result['already_counted'] and c['submitted_at'] and c['status']!='draft' and delivery and delivery['state'] in ('delivered','received') and b['status']=='pending')
 result['reason']='Ya contabilizada: no se añade nuevamente.' if result['already_counted'] else '' if result['eligible'] else 'Primero presenta y entrega la bolsa; resuelve cualquier corrección pendiente.'
 return result

def lookup(store,value,account):
 allow(account)
 if not isinstance(value,str) or len(value)>2000:raise DomainError('QR no válido.')
 if value.startswith(('https://','http://')):
  u=urlsplit(value);value=parse_qs(u.query).get('bag',[u.path.split('/')[-1]])[0]
 with store.connect() as db:
  db.execute('BEGIN')
  if re.fullmatch(r'BOL-\d{6}',value):
   r=db.execute('SELECT id FROM cash_bags WHERE number=?',(int(value[4:]),)).fetchone();value=r['id'] if r else ''
  if not re.fullmatch(bags.BAG_ID,value.lower()):raise DomainError('Usa el QR completo o el número BOL-000001.')
  return candidate(db,value.lower(),account)

def apply_count(db,row,amount,note,account,batch_id=None):
 """Caller holds BEGIN IMMEDIATE and has checked role, session and version."""
 b=dict(bags.row_for(db,row['bag_id']))
 if has_count(db,b):raise DomainError('Esta bolsa ya tiene un conteo confirmado; revisa su historial.',409)
 delivery=db.execute('SELECT * FROM cl_deliveries WHERE closure_id=?',(row['id'],)).fetchone()
 if row['id'] and (row['status']=='draft' or not row['submitted_at'] or not delivery or delivery['state'] not in ('delivered','received') or b['status']!='pending'):raise DomainError('La bolsa debe estar presentada y entregada.',409)
 if not row['id'] and b['status']!='pending':raise DomainError('La bolsa debe tener una entrega declarada vigente.',409)
 amount=bags.integer(amount,'Efectivo contabilizado');difference=amount-b['bag_amount']
 if difference and not note:raise DomainError('Explica la diferencia del conteo de bolsa.')
 now=cl.stamp();who=cl.actor(account)
 db.execute('INSERT INTO cr_counts VALUES(?,?,?,?,?,?,?,?,?)',(b['id'],row['id'],batch_id,amount,b['bag_amount'],row['business_date'],now,who,note))
 db.execute("UPDATE cash_bags SET status=?,verified_amount=?,verified_difference=?,verified_at=?,verified_by=?,verification_note=?,version=version+1,updated_at=? WHERE id=?",('difference' if difference else 'verified',amount,difference,now,who,note,now,b['id']))
 after=dict(bags.row_for(db,b['id']),verification_identity={'key':who,'name':account.get('name',account['username'])},count_batch_id=batch_id)
 db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',(b['id'],b['batch_id'],'verify',who,bags.canonical(b),bags.canonical(after),now))
 db.execute("UPDATE cl_deliveries SET state='received',received_at=COALESCE(received_at,?),received_by=COALESCE(received_by,?) WHERE closure_id=?",(now,who,row['id']))

def batch_view_db(db,bid):
 r=db.execute('SELECT * FROM cr_batches WHERE id=?',(bid,)).fetchone()
 if not r:raise DomainError('Lote no encontrado.',404)
 result=dict(r)
 if r['state']=='confirmed':
  saved=db.execute('SELECT snapshot_json FROM cr_batch_versions WHERE batch_id=? AND version=?',(bid,r['version'])).fetchone()
  if saved:return json.loads(saved[0])
 items=[]
 for row in db.execute('SELECT * FROM cr_batch_items WHERE batch_id=? ORDER BY business_date,bag_id',(bid,)):
  item=dict(row);b=bags.row_for(db,item['bag_id']);c=db.execute('SELECT version,register_id,responsible_json FROM cl_closures WHERE id=?',(item['closure_id'],)).fetchone()
  item.update(label=f"BOL-{b['number']:06d}",register_name=db.execute('SELECT name FROM cl_registers WHERE id=?',(c['register_id'],)).fetchone()[0] if c else b['register_name'],cashier=json.loads(c['responsible_json'])['name'] if c else b['responsible'],difference=item['amount']-item['declared_amount'] if item['amount'] is not None else None,
   already_counted=has_count(db,b),stale=result['state']=='draft' and (item['bag_version']!=b['version'] or (c is not None and item['closure_version']!=c['version'])))
  items.append(item)
 result.update(items=items,total_declared=sum(r['declared_amount'] for r in items),total_counted=sum(r['amount'] for r in items) if items and all(r['amount'] is not None for r in items) else None,
  total_difference=sum(r['difference'] for r in items) if items and all(r['difference'] is not None for r in items) else None,absolute_differences=sum(abs(r['difference']) for r in items if r['difference'] is not None) if any(r['difference'] is not None for r in items) else None)
 return result

def batch_view(store,bid,account):
 allow(account)
 with store.connect() as db:db.execute('BEGIN');return batch_view_db(db,bid)

def execute(store,action,payload,account,identity_check=None):
 allow(account)
 fields={'batch-create':('name',),'batch-save':('id','version','items'),'batch-confirm':('id','version'),'review':('closure_id','fingerprint','note'),
 'difference':('closure_id','fingerprint','kind','proposal_id','note','attachment_id'),'methods':('closure_id','fingerprint','reference','methods','cash_basis')}
 if action not in fields:raise DomainError('Operación no disponible.',404)
 calc.shape(payload,('request_id','confirmed',*fields[action]),'Revisión');rid=payload.get('request_id')
 if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',rid):raise DomainError('Falta la clave de operación.')
 if action not in ('batch-create','batch-save') and payload.get('confirmed') is not True:raise DomainError('Confirma expresamente la operación.')
 sig=digest([action,payload,cl.actor(account)])
 with store.connect() as db:
  db.execute('BEGIN IMMEDIATE')
  if identity_check and not identity_check(db):raise DomainError('La sesión venció o fue revocada.',401)
  old=db.execute('SELECT * FROM cr_requests WHERE request_id=?',(rid,)).fetchone()
  if old:
   if old['signature']!=sig:raise DomainError('La clave ya se usó con otros datos.',409)
   return json.loads(old['response_json'])
  now=cl.stamp();who=cl.actor(account)
  if action.startswith('batch-'):
   if action=='batch-create':
    bid=uuid4().hex;name=bags.text(payload.get('name'),'Nombre del lote',100)
    db.execute("INSERT INTO cr_batches VALUES(?,?,'draft',1,?,?,?,NULL,NULL)",(bid,name,who,now,now))
   else:
    bid=cc.identifier(payload.get('id'));batch=batch_view_db(db,bid)
    if batch['state']!='draft' or type(payload.get('version')) is not int or payload['version']!=batch['version']:raise DomainError('El lote cambió o ya fue confirmado. Vuelve a abrirlo.',409)
    if action=='batch-save':
     items=payload.get('items')
     if not isinstance(items,list) or len(items)>200:raise DomainError('Un lote admite hasta 200 bolsas.')
     normalized=[];seen=set()
     for item in items:
      calc.shape(item,('bag_id','bag_version','closure_version','amount','note'),'Bolsa de lote');bag_id=item.get('bag_id')
      if not isinstance(bag_id,str) or not re.fullmatch(bags.BAG_ID,bag_id):raise DomainError('Identificador de bolsa no válido.')
      if bag_id in seen:raise DomainError('La misma bolsa está repetida en el lote.',409)
      seen.add(bag_id);current=candidate(db,bag_id,account)
      if not current['eligible']:raise DomainError(current['reason'],409)
      if any(item.get(k)!=current[k] or (current[k] is not None and type(item.get(k)) is not int) for k in ('bag_version','closure_version')):raise DomainError('La bolsa o su cierre cambió. Escanéala nuevamente.',409)
      amount=None if item.get('amount') is None else bags.integer(item['amount'],'Monto contado');note=calc.optional_text(item.get('note'),'Nota',1500) or ''
      normalized.append((bid,bag_id,current['closure_id'],current['closure_version'],current['bag_version'],current['declared_amount'],current['business_date'],amount,note))
     db.execute('DELETE FROM cr_batch_items WHERE batch_id=?',(bid,));db.executemany('INSERT INTO cr_batch_items VALUES(?,?,?,?,?,?,?,?,?)',normalized)
    else:
     if not batch['items']:raise DomainError('Añade al menos una bolsa al lote.')
     # All checks and writes share the same transaction; any conflict rolls back the full batch.
     for item in batch['items']:
      current=candidate(db,item['bag_id'],account)
      if not current['eligible'] or item['stale']:raise DomainError('Una bolsa cambió o ya fue contabilizada. Revisa el borrador completo.',409)
      row=cl.get(db,item['closure_id'],account) if item['closure_id'] else dict(id=None,bag_id=item['bag_id'],business_date=item['business_date']);apply_count(db,row,item['amount'],item['note'],account,bid)
      if not row['id']:continue
      db.execute('UPDATE cl_closures SET version=version+1,last_editor=?,updated_at=? WHERE id=?',(who,now,row['id']))
      cl.version(db,row,'count',account,'Lote '+bid+': '+item['note'])
     db.execute("UPDATE cr_batches SET state='confirmed',confirmed_at=?,confirmed_by=?,version=version+1,updated_at=? WHERE id=?",(now,who,now,bid))
    # Confirmed trigger prevents a second update: apply version/timestamp in the confirming statement above.
    if action=='batch-save':db.execute('UPDATE cr_batches SET version=version+1,updated_at=? WHERE id=?',(now,bid))
   result=batch_view_db(db,bid)
   db.execute('INSERT INTO cr_batch_versions VALUES(?,?,?,?,?,?)',(bid,result['version'],who,action,bags.canonical(result),now))
  else:
   row=cl.get(db,cc.identifier(payload.get('closure_id')),account);c=detail(db,cl.presentation(db,row));fp=c['fingerprint']
   if payload.get('fingerprint')!=fp:raise DomainError('El cierre o sus fuentes cambiaron. Abre la versión vigente.',409)
   if action=='methods':
    reference=bags.text(payload.get('reference'),'Referencia del reporte Toteat',1500);methods=payload.get('methods')
    if not isinstance(methods,list) or len(methods)!=6:raise DomainError('Incluye los seis medios, dejando vacíos los pendientes.')
    clean=[];keys=set()
    for m in methods:
     calc.shape(m,('key','sales','tip','total'),'Medio Toteat')
     if m.get('key') not in [x['key'] for x in c['data']['methods']] or m['key'] in keys:raise DomainError('Medio ausente o duplicado.')
     if m['key']=='cash' and any(m.get(k) is not None for k in ('sales','tip','total')) and payload.get('cash_basis')!='net_sales_before_movements':raise DomainError('El efectivo Toteat debe ser ventas netas de vuelto antes de movimientos; collection.cash no es comparable.')
     keys.add(m['key']);clean.append(dict(key=m['key'],sales=calc.amount(m.get('sales'),'Ventas Toteat'),tip=calc.amount(m.get('tip'),'Propinas Toteat')))
     if 'total' in m:clean[-1]['total']=calc.amount(m['total'],'Total Toteat')
    db.execute('INSERT INTO cr_method_sources(closure_id,input_hash,reference,methods_json,actor,created_at) VALUES(?,?,?,?,?,?)',(row['id'],cl.review_hash(row,c['data']),reference,bags.canonical(clean),who,now))
   elif action=='review':
    note=bags.text(payload.get('note'),'Constancia de revisión',1500)
    db.execute('INSERT INTO cr_reviews(closure_id,fingerprint,note,actor,created_at) VALUES(?,?,?,?,?)',(row['id'],fp,note,who,now))
    if c.get('cashier_v2'):
     from erp import cashier_v2
     last=cashier_v2.latest(db,row['id'])
     cashier_v2.event(db,row['id'],'admin_review',dict(closure_version=row['version'],submission_version=last['number'] if last else None,snapshot_hash=last['snapshot_hash'] if last else None,fingerprint=fp,note=note,reviewer_name=account.get('name') or account['username']),account,last['id'] if last else None)
   else:
    kind=payload.get('kind');note=bags.text(payload.get('note'),'Nota sobre la diferencia',1500);attachment=payload.get('attachment_id') or None
    if kind not in ('propose','accept','reopen'):raise DomainError('Resolución no válida.')
    if attachment and not db.execute('SELECT 1 FROM cl_attachments WHERE id=? AND closure_id=?',(attachment,row['id'])).fetchone():raise DomainError('El comprobante debe pertenecer a este cierre.')
    last=c['resolution_events'][-1] if c['resolution_events'] else None;proposal=None
    if kind=='accept':
     if not last or last['kind']!='propose' or last['fingerprint']!=fp or type(payload.get('proposal_id')) is not int or payload['proposal_id']!=last['id']:raise DomainError('Primero registra y revisa una propuesta vigente.',409)
     proposal=last['id']
    elif kind=='propose' and not c['has_differences']:raise DomainError('No hay una diferencia numérica vigente para resolver.')
    elif kind=='reopen' and not last:raise DomainError('No hay una resolución que reabrir.')
    db.execute('INSERT INTO cr_difference_events(closure_id,kind,proposal_id,fingerprint,note,attachment_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(row['id'],kind,proposal,fp,note,attachment,who,now))
   result=detail(db,cl.presentation(db,row,True))
  db.execute('INSERT INTO cr_requests VALUES(?,?,?)',(rid,sig,bags.canonical(result)))
  return result

def handle(handler,store,path,query,account,identity_check=None):
 allow(account);suffix=path[len(PREFIX):]
 if handler.command=='GET':
  if suffix=='':return handler.send(200,overview(store,query,account))
  if suffix=='/lookup':
   if set(query)!={'value'} or len(query['value'])!=1:raise DomainError('Indica una bolsa.')
   return handler.send(200,lookup(store,query['value'][0],account))
  if re.fullmatch('/batch/'+cl.ID,suffix):return handler.send(200,batch_view(store,suffix.split('/')[-1],account))
  if re.fullmatch('/closure/'+cl.ID,suffix):return handler.send(200,get_detail(store,suffix.split('/')[-1],account))
  if suffix in ('/preview.pdf','/download.pdf'):
   from erp.cash_review_pdf import render
   params=dict(query);snapshot=params.pop('snapshot',None)
   if snapshot is not None and (len(snapshot)!=1 or not re.fullmatch(r'[a-f0-9]{64}',snapshot[0])):raise DomainError('Referencia de resumen no válida.')
   data=overview(store,params,account)
   if snapshot is not None and snapshot[0]!=data['snapshot']:raise DomainError('El resumen cambió. Actualiza el panel antes de abrir el PDF.',409)
   return handler.send(200,render(data),'application/pdf',{'Content-Disposition':('inline' if suffix=='/preview.pdf' else 'attachment')+'; filename="Revision_de_cierres.pdf"'})
 if handler.command=='POST' and re.fullmatch('/[a-z-]+',suffix):
  origin=handler.headers.get('Origin');host=handler.headers.get('Host','')
  if origin and origin not in ('http://'+host,'https://'+host):raise DomainError('Origen externo rechazado.',403)
  return handler.send(200,execute(store,suffix[1:],handler.body(maximum=512*1024 if suffix=='/batch-save' else 65536),account,identity_check))
 raise DomainError('Ruta de revisión no encontrada.',404)
