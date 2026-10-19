"""Independent terminal comparisons and reviewed cash analytics; no cash postings."""
from datetime import date,datetime,timedelta
from pathlib import Path
from uuid import uuid4
import hashlib,json,re,sqlite3
from erp import cash_closures as cl,cash_review as review,cash_closure_calc as calc,cash_toteat as toteat
from erp.cash_bags import canonical
from erp.domain import DomainError
PREFIX='/api/cash-insights'

def initialize(db):
 statements='''CREATE TABLE IF NOT EXISTS ci_terminal_reports (
 id TEXT PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 terminal_id TEXT NOT NULL, reference TEXT NOT NULL, revision INTEGER NOT NULL,
 supersedes TEXT UNIQUE REFERENCES ci_terminal_reports(id), report_json TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(terminal_id,reference,revision));
 CREATE INDEX IF NOT EXISTS ci_terminal_closure ON ci_terminal_reports(closure_id,created_at,id);
 CREATE TRIGGER IF NOT EXISTS ci_terminal_no_update BEFORE UPDATE ON ci_terminal_reports BEGIN SELECT RAISE(ABORT,'Immutable terminal evidence'); END;
 CREATE TRIGGER IF NOT EXISTS ci_terminal_no_delete BEFORE DELETE ON ci_terminal_reports BEGIN SELECT RAISE(ABORT,'Immutable terminal evidence'); END;'''
 statement=''
 for line in statements.splitlines(True):
  statement+=line
  if sqlite3.complete_statement(statement):db.execute(statement);statement=''
 if statement.strip():raise ValueError('Incomplete migration')

def allow(account):
 if not cl.partner(account):raise DomainError('La analítica y conciliación requieren una cuenta de socio autorizada.',403)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def text(value,label,maximum=500,optional=False):return calc.optional_text(value,label,maximum) if optional else cl.bags.text(value,label,maximum)
def nullable_amount(v,label):return calc.amount(v,label)

def filters(query):
 if set(query)-{'start','end','compare_start','compare_end','register','cashier','shift'} or any(len(v)!=1 for v in query.values()):raise DomainError('Filtros no válidos.')
 base=review.query_filters({k:v for k,v in query.items() if k in ('start','end','register','cashier')})
 if base['end']>datetime.now(calc.TZ).date().isoformat():raise DomainError('El rango no puede terminar en el futuro.')
 days=(date.fromisoformat(base['end'])-date.fromisoformat(base['start'])).days+1
 previous_end=date.fromisoformat(base['start'])-timedelta(days=1)
 cs=query.get('compare_start',[(previous_end-timedelta(days=days-1)).isoformat()])[0];ce=query.get('compare_end',[previous_end.isoformat()])[0]
 try:
  first,last=date.fromisoformat(cs),date.fromisoformat(ce)
  if cs!=first.isoformat() or ce!=last.isoformat() or first>last or (last-first).days>365 or ce>datetime.now(calc.TZ).date().isoformat():raise ValueError()
 except (TypeError,ValueError):raise DomainError('Selecciona un período de comparación válido de hasta 366 días.') from None
 shift=query.get('shift',[''])[0]
 if len(shift)>100:raise DomainError('Filtro de turno no válido.')
 return dict(base,shift=shift,compare_start=cs,compare_end=ce)

def ownership(c):
 p=c['responsible'];safe=bool(p.get('key') and p.get('name') and not p.get('shared') and not p.get('legacy'))
 # Shared or legacy declarations need a separate verified identity before evaluation.
 return dict(key=p['key'] if safe else 'unassigned',name=p['name'] if safe else 'Sin asignación segura',safe=safe,declared_name=p.get('name'),last_editor=c.get('last_editor'))

def context_db(db,cid,session_key=None):
 if not isinstance(cid,str) or not cid or len(cid)>140:raise DomainError('Selecciona un cierre válido.')
 if session_key is not None and (not isinstance(session_key,str) or len(session_key)>200):raise DomainError('Selecciona una sesión válida.')
 row=db.execute('SELECT * FROM cl_closures WHERE id=?',(cid,)).fetchone()
 if not row:raise DomainError('Cierre no encontrado.',404)
 c=cl.presentation(db,dict(row))
 source=toteat.project(db,c['business_date'],c['register_id'],session_key or c['external_session_key'])
 selected=source['selected'];methods=[m for m in (selected or {}).get('methods',[]) if m['provider_id']=='5008' and m['name'].casefold().strip()=='mercado pago point smart']
 reference=dict(closure_id=cid,closure_version=c['version'],business_date=c['business_date'],register_id=c['register_id'],shift=c['shift_name'],session=selected,
  source_read=source['sources']['collection'],provider_method_id='5008',provider_total=methods[0]['reported_total'] if len(methods)==1 else None,responsible=ownership(c))
 return dict(closure=dict(id=cid,version=c['version'],date=c['business_date'],register=c['register_name'],shift=c['shift_name'],responsible=c['responsible']['name']),source=source,reference=reference,token=digest(dict(reference,source_read={k:reference['source_read'][k] for k in ('state','payload_hash','error')})),attachments=c['attachments'],history=terminal_history(db,cid))

def comparable_report(report):
 reasons=[];source=report['source'];session=source.get('session');cut=report.get('economic_scope') or {};verified_cut=cut.get('kind')=='verified_cut'
 compared_total=cut.get('toteat_total') if verified_cut else source.get('provider_total')
 if not session:reasons.append('Falta seleccionar la sesión exacta de Toteat.')
 if not verified_cut and (not session or not session.get('closed_at')):reasons.append('La sesión Toteat no está cerrada; falta un corte económico independiente verificado.')
 if cut.get('kind')=='unknown':reasons.append('El alcance económico no está verificado.')
 if not report['period_start'] or not report['period_end']:reasons.append('Falta el período del cupón del terminal.')
 elif verified_cut:
  if report['period_start']!=cut.get('start') or report['period_end']!=cut.get('end'):reasons.append('El cupón y el corte económico verificado tienen períodos diferentes.')
  if not cut.get('verified') or not cut.get('reference') or not report.get('attachment_id'):reasons.append('Falta evidencia del corte económico independiente.')
  if session and cut.get('start') and cut.get('end') and (cut.get('start')<session['opened_at'] or (session.get('closed_at') and cut.get('end')>session['closed_at'])):reasons.append('El corte económico no pertenece a la sesión seleccionada.')
 elif session and (report['period_start']!=session['opened_at'] or report['period_end']!=session['closed_at']):reasons.append('Los períodos del cupón y Toteat son diferentes.')
 if compared_total is None:reasons.append('Falta el total reportado por Toteat para Mercado Pago Point Smart.')
 if source['source_read']['state']!='observed':reasons.append('La lectura Toteat está pendiente, antigua o falló; actualízala.')
 if report['basis']!='gross_with_tips':reasons.append('El cupón no identifica un bruto comparable con propinas; un abono neto bancario no es comparable.')
 if not report['scope_verified']:reasons.append('Falta verificar que el cupón corresponde a esta caja y terminal.')
 if not report['adjustments_verified']:reasons.append('Falta revisar la misma base de propinas y devoluciones.')
 if not report['reviewed']:reasons.append('La comparación está pendiente de revisión.')
 return dict(comparable=not reasons,reasons=reasons,difference=report['total']-compared_total if not reasons else None,compared_total=compared_total,scope='verified_economic_cut' if verified_cut else 'administrative_session',
  meaning='Total bruto del cupón menos total reportado Toteat. No acredita abono bancario ni falta de efectivo.')

def terminal_history(db,cid=None):
 rows=db.execute('SELECT * FROM ci_terminal_reports '+('WHERE closure_id=? ' if cid else '')+'ORDER BY created_at DESC,id DESC',(cid,) if cid else ()).fetchall();result=[]
 for row in rows:
  value=json.loads(row['report_json']);value.update(id=row['id'],closure_id=row['closure_id'],revision=row['revision'],supersedes=row['supersedes'],actor=row['actor'],created_at=row['created_at'])
  value['comparison']=comparable_report(value)
  c=db.execute('SELECT version,business_date,register_id FROM cl_closures WHERE id=?',(row['closure_id'],)).fetchone()
  if c and c['version']!=value['source']['closure_version']:value['comparison']=dict(comparable=False,difference=None,reasons=['El cierre cambió después de registrar este cupón; conserva el original y registra la corrección revisada.'])
  if c and value['source'].get('session'):
   current=toteat.project(db,c['business_date'],c['register_id'],value['source']['session']['session_key']);session=current['selected'];methods=[m for m in (session or {}).get('methods',[]) if m['provider_id']=='5008' and m['name'].casefold().strip()=='mercado pago point smart']
   verified_cut=(value.get('economic_scope') or {}).get('kind')=='verified_cut'
   changed=not session or session['opened_at']!=value['source']['session']['opened_at']
   if not verified_cut:changed=changed or session['closed_at']!=value['source']['session']['closed_at'] or len(methods)!=1 or methods[0]['reported_total']!=value['source']['provider_total'] or current['sources']['collection']['state']=='error'
   if changed: # Administrative closing later does not rewrite a documented economic cut.
    value['comparison']=dict(comparable=False,difference=None,reasons=['La evidencia pertinente de Toteat cambió; requiere revisión.'])
  value['superseded']=bool(db.execute('SELECT 1 FROM ci_terminal_reports WHERE supersedes=?',(row['id'],)).fetchone());result.append(value)
 return result

def save_terminal(store,payload,account,identity_check=None):
 allow(account);allowed={'request_id','confirmed','closure_id','source_token','session_key','terminal_id','reference','period_start','period_end','total','tip','refunds','transaction_count','debit','credit','prepaid','basis','scope_verified','adjustments_verified','reviewed','note','attachment_id','supersedes','economic_scope'}
 if not isinstance(payload,dict) or set(payload)-allowed:raise DomainError('Campos de cupón no admitidos.')
 rid=payload.get('request_id')
 if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',rid) or payload.get('confirmed') is not True:raise DomainError('Confirma el registro del cupón con una clave de operación.')
 signature=digest(['terminal-report',cl.actor(account),payload])
 with store.connect() as db:
  db.execute('BEGIN IMMEDIATE')
  if identity_check and not identity_check():raise DomainError('La sesión venció.',401)
  prior=db.execute('SELECT * FROM cr_requests WHERE request_id=?',(rid,)).fetchone()
  if prior:
   if prior['signature']!=signature:raise DomainError('La clave ya corresponde a otros datos.',409)
   return json.loads(prior['response_json'])
  context=context_db(db,payload.get('closure_id'),payload.get('session_key'))
  if payload.get('source_token')!=context['token']:raise DomainError('El cierre o la fuente cambió. Actualiza la comparación y revisa antes de guardar.',409)
  reference=cl.key(text(payload.get('reference'),'Referencia única del cupón',180));terminal=cl.key(text(payload.get('terminal_id'),'Identificador del terminal',100))
  total=nullable_amount(payload.get('total'),'Total bruto del cupón')
  if total is None:raise DomainError('Indica el total del cupón; no se infiere cero.')
  start=calc.instant(payload.get('period_start'),'Inicio del cupón');end=calc.instant(payload.get('period_end'),'Fin del cupón')
  if (start and start>cl.stamp()) or (end and end>cl.stamp()):raise DomainError('El período comprobado no puede estar en el futuro.')
  if start and end and start>=end:raise DomainError('El período del cupón debe terminar después de su inicio.')
  basis=payload.get('basis','unknown')
  if basis not in ('unknown','gross_with_tips','net_settlement'):raise DomainError('Base del cupón no válida.')
  values={k:nullable_amount(payload.get(k),k) for k in ('tip','refunds','transaction_count','debit','credit','prepaid')}
  if values['tip'] is not None and values['tip']>total:raise DomainError('La propina no puede superar el total bruto.')
  if all(values[k] is not None for k in ('debit','credit','prepaid')) and sum(values[k] for k in ('debit','credit','prepaid'))!=total:raise DomainError('El desglose débito/crédito/prepago no suma el total bruto del cupón.')
  for k in ('scope_verified','adjustments_verified','reviewed'):
   if type(payload.get(k)) is not bool:raise DomainError('Marca explícitamente el alcance de la revisión.')
  attachment=payload.get('attachment_id') or None
  if attachment and not any(a['id']==attachment for a in context['attachments']):raise DomainError('El adjunto no pertenece a este cierre.')
  cut=payload.get('economic_scope') or {'kind':'session_period'}
  calc.shape(cut,('kind','start','end','toteat_total','reference','verified'),'Corte económico')
  if cut.get('kind') not in ('session_period','verified_cut','unknown'):raise DomainError('Tipo de corte económico no válido.')
  if cut['kind']=='verified_cut':
   cut=dict(kind='verified_cut',start=calc.instant(cut.get('start'),'Inicio económico'),end=calc.instant(cut.get('end'),'Fin económico'),toteat_total=calc.amount(cut.get('toteat_total'),'Total Toteat del corte'),reference=text(cut.get('reference'),'Evidencia del corte',1500),verified=cut.get('verified') is True)
   if cut['end'] and cut['end']>cl.stamp():raise DomainError('El corte comprobado no puede estar en el futuro.')
   if not cut['start'] or not cut['end'] or cut['start']>=cut['end'] or not attachment:raise DomainError('El corte verificado requiere período y comprobante adjunto.')
  else:cut={'kind':cut['kind']}
  last=db.execute('SELECT * FROM ci_terminal_reports WHERE terminal_id=? AND reference=? ORDER BY revision DESC LIMIT 1',(terminal,reference)).fetchone();supersedes=payload.get('supersedes') or None
  if last:
   if last['id']!=supersedes or last['closure_id']!=payload['closure_id']:raise DomainError('Este cupón ya está registrado. Abre su última versión para corregirlo, sin duplicarlo.',409)
  elif supersedes:raise DomainError('La versión indicada no corresponde a este cupón.',409)
  report=dict(economic_scope=cut,terminal_id=terminal,reference=reference,period_start=start,period_end=end,total=total,currency='CLP',basis=basis,source=context['reference'],note=text(payload.get('note'),'Nota y evidencia de revisión',1500),attachment_id=attachment,**values,**{k:payload[k] for k in ('scope_verified','adjustments_verified','reviewed')})
  ident=uuid4().hex;db.execute('INSERT INTO ci_terminal_reports VALUES(?,?,?,?,?,?,?,?,?)',(ident,payload['closure_id'],terminal,reference,(last['revision']+1) if last else 1,supersedes,canonical(report),cl.actor(account),cl.stamp()))
  result=next(x for x in terminal_history(db,payload['closure_id']) if x['id']==ident)
  db.execute('INSERT INTO cr_requests VALUES(?,?,?)',(rid,signature,canonical(result)));return result

def aggregate(lines,kind):
 included=[x for x in lines if x[kind]['eligible']];amounts=[x[kind]['difference'] for x in included];denominator=sum(x[kind]['denominator'] for x in included if x[kind]['denominator'] is not None)
 return dict(records=len(lines),reviewed_known=len(included),pending=len(lines)-len(included),without_difference=sum(v==0 for v in amounts),without_difference_percent=100*sum(v==0 for v in amounts)/len(amounts) if amounts else None,
  shortage=sum(-v for v in amounts if v<0) if amounts else None,surplus=sum(v for v in amounts if v>0) if amounts else None,shortage_count=sum(v<0 for v in amounts),surplus_count=sum(v>0 for v in amounts),
  handled=denominator if included else None,absolute_difference_percent=100*sum(abs(v) for v in amounts)/denominator if included and denominator>0 else None,
  denominator_label='A físico declarado de los cierres incluidos' if kind=='cash' else 'C entregado declarado de las bolsas incluidas',closure_ids=[x['id'] for x in included])

def lines_db(db,f,start,end):
 rows=[dict(r) for r in db.execute('SELECT * FROM cl_closures WHERE business_date BETWEEN ? AND ? ORDER BY business_date,id',(max(start,'2026-10-16'),end))] if end>='2026-10-16' else []
 result=[]
 for row in rows:
  person=json.loads(row['responsible_json'])
  safe_key=person['key'] if person.get('key') and person.get('name') and not person.get('shared') and not person.get('legacy') else 'unassigned'
  if f['register'] and row['register_id']!=f['register'] or f['cashier'] and safe_key!=f['cashier'] or f['shift'] and row['shift_name']!=f['shift']:continue
  c=review.detail(db,cl.presentation(db,row));owner=ownership(c);v=c['calculation'];bag=c['bag'];delivered=bool(c['delivery'] and c['delivery']['state'] in ('delivered','received'))
  eligible=c['reviewed'] and c['status']!='draft' and owner['safe'];cash_diff=v['difference'];bag_diff=bag['verified_difference'] if bag and delivered and bag['verified_at'] else None
  pending=[]
  if c.get('cashier_v2'):pending.extend(c.get('v2_pending',[]))
  if c['status']=='draft':pending.append(dict(cause='Declaración sin presentar',responsible=owner['name'],action='Completar y presentar el cierre'))
  if c['submitted_at'] and not delivered:pending.append(dict(cause='Bolsa aún sin entrega registrada',responsible=owner['name'],action='Registrar la entrega física cuando ocurra'))
  if delivered and bag and not bag['verified_at']:pending.append(dict(cause='Bolsa entregada sin conteo de recepción',responsible='Administración / receptor autorizado',action='Contar y registrar la recepción de esta bolsa'))
  if not c['reviewed'] or v['expected'] is None:pending.append(dict(cause='Fuentes o revisión pendientes',responsible='Administración',action='Revisar apertura, corte económico y movimientos'))
  if c['has_differences'] and c['resolution_state']!='resolved':pending.append(dict(cause='Diferencia sin resolución revisada',responsible='Administración',action='Revisar la diferencia y documentar su resolución'))
  if not owner['safe']:pending.append(dict(cause='Identidad sin comprobación documental',responsible='Administración',action='Conservar sin asignación hasta verificar la identidad'))
  corrections=db.execute("SELECT COUNT(*) FROM cl_versions WHERE closure_id=? AND action='save' AND reason<>''",(c['id'],)).fetchone()[0]
  result.append(dict(pending_work=pending,created_at=c['created_at'],submitted_at=c['submitted_at'],delivered_at=c['delivery']['delivered_at'] if c['delivery'] else None,counted_at=bag['verified_at'] if bag else None,correction_versions=corrections,id=c['id'],date=c['business_date'],register_id=c['register_id'],register=c['register_name'],shift=c['shift_name'],owner=owner,status=c['status'],reviewed=c['reviewed'],resolution=c['resolution_state'],note=c['review']['note'] if c['review'] else None,
   cash=dict(difference=cash_diff,denominator=v['A'],eligible=bool(eligible and cash_diff is not None and v['A'] is not None)),delivery=dict(difference=bag_diff,denominator=bag['bag_amount'] if bag else None,eligible=bool(eligible and bag_diff is not None and bag['bag_amount'] is not None)),
   source_coverage=v['coverage'],source_errors=[s for s in toteat.project(db,c['business_date'],c['register_id'],c['external_session_key'])['sources'].values() if s['state'] in ('error','missing','stale')]))
 return result

def operational_metrics(lines):
 now=datetime.fromisoformat(cl.stamp());durations=[];pending=[]
 for row in lines:
  if row['delivered_at'] and row['counted_at']:
   seconds=(datetime.fromisoformat(row['counted_at'])-datetime.fromisoformat(row['delivered_at'])).total_seconds()
   if seconds>=0:durations.append(seconds)
  for item in row['pending_work']:
   pending.append(dict(item,closure_id=row['id'],calendar_age_seconds=max(0,(now-datetime.fromisoformat(row['created_at'])).total_seconds())))
 return dict(records=len(lines),presented=sum(x['submitted_at'] is not None for x in lines),complete_reviewed=sum(x['reviewed'] and x['status']!='draft' and not x['pending_work'] for x in lines),correction_versions=sum(x['correction_versions'] for x in lines),custody_sample=len(durations),delivery_to_count_mean_seconds=sum(durations)/len(durations) if durations else None,pending=pending,notice='Antigüedad calendario desde el registro, no indicador de atraso. Tiempo de entrega a conteo solo con ambos instantes conocidos. No puntúa personas ni acredita custodia intermedia.')

def overview(store,query,account):
 allow(account);f=filters(query)
 with store.connect() as db:
  db.execute('BEGIN');lines=lines_db(db,f,f['start'],f['end']);previous=lines_db(db,f,f['compare_start'],f['compare_end']);groups=[]
  for dimension in ('cashier','register','shift','day'):
   buckets={}
   for row in lines:
    k,label=(row['owner']['key'],row['owner']['name']) if dimension=='cashier' else (row['register_id'],row['register']) if dimension=='register' else (row['shift'],row['shift']) if dimension=='shift' else (row['date'],row['date'])
    buckets.setdefault((k,label),[]).append(row)
   groups.extend(dict(dimension=dimension,key=k,label=label,cash=aggregate(rows,'cash'),delivery=aggregate(rows,'delivery'),closure_ids=[r['id'] for r in rows]) for (k,label),rows in sorted(buckets.items(),key=lambda x:x[0][1]))
  terminals=[r for r in terminal_history(db) if f['start']<=r['source']['business_date']<=f['end'] and (not f['register'] or f['register']==r['source']['register_id']) and (not f['cashier'] or f['cashier']==r['source']['responsible']['key']) and (not f['shift'] or f['shift']==r['source'].get('shift'))]
  # Only the latest revision contributes. Card discrepancies never offset cash.
  current=[r for r in terminals if not r['superseded']];verified=[r for r in current if r['comparison']['comparable']];diffs=[r['comparison']['difference'] for r in verified]
  people={json.loads(r[0])['key']:json.loads(r[0])['name'] for r in db.execute('SELECT responsible_json FROM cl_closures')};people['unassigned']='Sin asignación segura';registers=[dict(r) for r in db.execute('SELECT id,name FROM cl_registers ORDER BY name')];shifts=[r[0] for r in db.execute('SELECT DISTINCT shift_name FROM cl_closures ORDER BY shift_name')]
  readings=[dict(r) for r in db.execute('SELECT business_date,endpoint,state,reason,read_at FROM cc_source_reads WHERE id IN (SELECT MAX(id) FROM cc_source_reads WHERE business_date BETWEEN ? AND ? GROUP BY business_date,endpoint) ORDER BY business_date,endpoint',(f['start'],f['end']))]
  return dict(filters=f,as_of=cl.stamp(),timezone='America/Santiago',rows=lines,previous_rows=previous,groups=groups,
   current={k:aggregate(lines,k) for k in ('cash','delivery')},previous={k:aggregate(previous,k) for k in ('cash','delivery')},previous_full_coverage=f['compare_start']>='2026-10-16',
   terminal_reports=terminals,terminal_metrics=dict(records=len(current),reviewed=len(verified),pending=len(current)-len(verified),shortage=sum(-v for v in diffs if v<0) if diffs else None,surplus=sum(v for v in diffs if v>0) if diffs else None),
   registers=registers,cashiers=[dict(key=k,name=n) for k,n in sorted(people.items(),key=lambda p:p[1])],shifts=shifts,sources=readings,
   operations=operational_metrics(lines),available_for_payments=None,notice='Evaluación solo sobre diferencias revisadas, conocidas y con identidad segura. Las diferencias de caja y recepción de bolsa son métricas distintas; no se suman ni prueban responsabilidad. No hay ranking ni sanción automática. Disponible para pagos requiere efectivo contado/custodiado y obligaciones/restricciones identificadas.')

def handle(handler,store,path,query,account,identity_check=None):
 allow(account)
 if handler.command=='GET':
  if path==PREFIX:return handler.send(200,overview(store,query,account))
  if path==PREFIX+'/context':
   if set(query)-{'closure_id','session_key'} or any(len(v)!=1 for v in query.values()):raise DomainError('Consulta de cupón no válida.')
   with store.connect() as db:
    db.execute('BEGIN');return handler.send(200,context_db(db,query.get('closure_id',[None])[0],query.get('session_key',[None])[0]))
 if handler.command=='POST' and path==PREFIX+'/terminal':return handler.send(200,save_terminal(store,handler.body(maximum=16384),account,identity_check))
 raise DomainError('Ruta de análisis no disponible.',404)
