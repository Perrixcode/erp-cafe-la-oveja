"""Server-owned per-field provenance. An import never certifies a declaration."""
from copy import deepcopy
from uuid import uuid4
from erp import cash_closure_calc as calc
from erp.domain import DomainError

CONTRACT={('1000','cash'):'cash',('1000','efectivo'):'cash',('5008','mercado pago point smart'):'point_smart',('5119','mercat'):'mercat',('50005','transferencias'):'transfer',('50007','mp emergencias'):'mp_emergency'}

def upgrade(data):
 result=deepcopy(data)
 if result.get('schema_version')!=2:
  result.update(schema_version=2,method_scheme='legacy',report_cutoff_at=None,imports=[])
  for m in result['methods']:m['total']=calc.method_total(m)
 return result

def scope(row,data):
 return {**{k:row.get(k) for k in ('business_date','register_id','external_session_key','shift_key')},'report_cutoff_at':data.get('report_cutoff_at')}

def evidence(view,method):
 session=view.get('selected')
 if not session:return None
 matches=[m for m in session.get('methods',[]) if CONTRACT.get((m['provider_id'],m['name'].casefold().strip()))==method]
 if len(matches)!=1:return None
 return dict(session_key=session['session_key'],provider_register_id=session['register_id'],opened_at=session['opened_at'],closed_at=session['closed_at'],method=matches[0])

def token(view):
 return calc.input_hash(dict(day=view['business_date'],register=view['register_id'],session=view['selected'],read=view['sources']['collection']))

def effective(db,row,data):
 result=deepcopy(data);active=[x for x in result.get('imports',[]) if x['status']!='discarded']
 if not active:return result
 from erp import cash_toteat
 view=cash_toteat.project(db,row['business_date'],row['register_id'],row['external_session_key'])
 bykey={m['key']:m for m in result['methods']}
 for item in active:
  resolution=item.get('resolution') or {}
  if item['status']=='manual_pending':
   if resolution.get('scope')!=scope(row,result):item['status']='scope_changed'
   continue
  if resolution.get('action')=='manual':
   if resolution['scope']!=scope(row,result):item['status']='scope_changed'
   elif bykey[item['method']].get('total')!=resolution['amount']:item['status']='value_changed'
   continue
  if item['scope']!=scope(row,result):item['status']='scope_changed'
  elif bykey[item['method']].get('total')!=item['value']:item['status']='value_changed'
  elif calc.input_hash(evidence(view,item['method']))!=item['evidence_hash']:item['status']='source_changed'
 return result

def save_guard(db,row,candidate,data,admin):
 old=__import__('json').loads(row['data_json']);prior=old.get('imports',[])
 if data.get('imports',[])!=prior:raise DomainError('La trazabilidad solo cambia mediante una resolución explícita.',409)
 if old.get('schema_version')==2 and data.get('schema_version')!=2:raise DomainError('No se puede eliminar el esquema de totales y su trazabilidad.')
 if old.get('schema_version')==2 and data.get('method_scheme')!=old.get('method_scheme'):raise DomainError('Conserva los medios originales de este cierre.')
 if data.get('schema_version')==2 and not admin:
  before={m['key']:m for m in old['methods']}
  for m in data['methods']:
   for k in ('sales','tip'):
    expected=before.get(m['key'],{}).get(k)
    if m[k]!=expected and not (before.get(m['key'],{}).get('used') is False and m['used'] is True and expected==0 and m[k] is None) and not (m['used'] is False and m[k]==0 and expected in (0,None)):
     raise DomainError('El desglose interno requiere una cuenta autorizada.',403)
 return effective(db,candidate,data)

def import_source(db,row,payload,actor,now):
 from erp import cash_toteat
 data=upgrade(__import__('json').loads(row['data_json']))
 if not row.get('external_session_key'):raise DomainError('Vincula y guarda la sesión exacta antes de importar.')
 view=cash_toteat.project(db,row['business_date'],row['register_id'],row['external_session_key'])
 if payload.get('source_token')!=token(view):raise DomainError('La fuente cambió; actualiza Toteat antes de importar.',409)
 source=view['sources']['collection']
 if not view['selected'] or source['state']!='observed':raise DomainError('Actualiza la lectura de esta sesión antes de importar.')
 count=0
 for m in data['methods']:
  ev=evidence(view,m['key']);value=ev['method']['reported_total'] if ev else None
  if m['used'] is False or m['total'] is not None or value is None or value<0:continue
  m['total']=value;m['used']=True;count+=1
  data['imports'].append(dict(id=uuid4().hex,method=m['key'],field='methods.'+m['key']+'.total',value=value,status='provisional',scope=scope(row,data),endpoint='collection',read_id=source['read_id'],payload_hash=source['payload_hash'],evidence_hash=calc.input_hash(ev),evidence=ev,observed_at=source['last_success'],imported_at=now,actor=actor,resolution=None))
 if not count:raise DomainError('No hay totales vacíos con una fuente contractual disponible. Los datos existentes se conservan.')
 return data

def resolve(db,row,payload,actor,now,admin):
 data=upgrade(__import__('json').loads(row['data_json']))
 found=[x for x in data['imports'] if x['id']==payload.get('import_id')]
 if len(found)!=1 or found[0]['status'] in ('discarded','manual_replaced'):raise DomainError('Importación no disponible para resolver.')
 item=found[0];method=next(m for m in data['methods'] if m['key']==item['method']);action=payload.get('resolution')
 reason=calc.optional_text(payload.get('reason'),'Motivo y evidencia independiente',1500)
 if not reason:raise DomainError('Explica la resolución de este importe importado.')
 if action=='discard':
  if method['total']==item['value']:method['total']=None;method['used']=None
  item['status']='manual_pending' if method['total'] is not None else 'discarded'
 elif action=='manual':
  if not admin:raise DomainError('Sustituir una importación por evidencia manual requiere revisión autorizada.',403)
  value=calc.amount(payload.get('amount'),'Total comprobado')
  if value is None:raise DomainError('Indica el total comprobado de forma independiente.')
  attachment=payload.get('attachment_id')
  if not db.execute('SELECT 1 FROM cl_attachments WHERE closure_id=? AND id=?',(row['id'],attachment)).fetchone():raise DomainError('Adjunta el reporte independiente al cierre y selecciónalo.')
  if not data.get('report_cutoff_at'):raise DomainError('Indica el corte económico comprobado del reporte.')
  method['total']=value;method['used']=True;item['status']='manual_replaced'
 else:raise DomainError('Selecciona descartar o sustituir con evidencia manual.')
 item['resolution']=dict(action=action,reason=reason,actor=actor,at=now,scope=scope(row,data),amount=method['total'],attachment_id=payload.get('attachment_id'))
 return data
