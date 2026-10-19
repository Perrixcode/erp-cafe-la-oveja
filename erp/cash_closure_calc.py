"""Null-aware, integer CLP closure calculations. No network, payments or ledger writes."""
from datetime import date,datetime,timezone
import hashlib,json,re
from zoneinfo import ZoneInfo
from erp.domain import DomainError
from erp.cash_bags import canonical,integer,text

DENOMINATIONS=(20000,10000,5000,2000,1000,500,100,50,10)
METHODS=('cash','debit','credit','transfer','mp_emergency','other')
PROVIDER_METHODS=('cash','point_smart','transfer','mp_emergency','mercat','other')
LABELS={'cash':'Efectivo','debit':'Débito','credit':'Crédito','prepaid':'Prepago','point_smart':'Mercado Pago Point Smart','mercat':'Mercat','transfer':'Transferencia','mp_emergency':'MP Emergencias','other':'Otros'}
TZ=ZoneInfo('America/Santiago')

class FieldError(DomainError):
 def __init__(self,path,message):
  super().__init__(message);self.field_errors={path:message}

def local_time(value):return datetime.fromisoformat(value).astimezone(TZ).strftime('%d/%m/%Y %H:%M')+' (Chile)'

def optional_text(value,label,maximum=500):return None if value in (None,'') else text(value,label,maximum)
def amount(value,label):return None if value in (None,'') else integer(value,label,10**10)
def instant(value,label='Momento',path=None):
 if value in (None,''):return None
 try:
  v=datetime.fromisoformat(value.replace('Z','+00:00'))
  if not v.tzinfo:raise ValueError()
  return v.astimezone(timezone.utc).isoformat(timespec='seconds')
 except (ValueError,AttributeError,TypeError):
  message=label+': ingresa una fecha y hora completas; se usa la hora de Chile.'
  raise (FieldError(path,message) if path else DomainError(message)) from None
def shape(value,keys,label):
 if not isinstance(value,dict) or set(value)-set(keys):raise DomainError(label+': campos no admitidos.')
 return value
def denominations(value,label):
 value=shape(value or {},{str(d) for d in DENOMINATIONS},label)
 result={str(d):amount(value.get(str(d)),label+' '+str(d)) for d in DENOMINATIONS}
 if any(v is not None and v>100000 for v in result.values()):raise DomainError('Cantidad de billetes o monedas demasiado alta.')
 return result
def denom_total(value):return sum(int(k)*v for k,v in value.items()) if all(v is not None for v in value.values()) else None
def empty(schema_version=1):
 result={'cash':{str(d):None for d in DENOMINATIONS},'retained':{str(d):None for d in DENOMINATIONS},
  'counted_at':None,'sealed_at':None,'opening':{'amount':None,'as_of':None,'reference':None},
  'cash_source':{'type':'manual_report','reference':None,'basis':'unknown','read_id':None},
  'methods':[{'key':k,'name':None,'used':None,'sales':None,'tip':None,'comparison':{'kind':None,'reference':None,'amount':None}} for k in METHODS],
  'movements':[],'movements_complete':False,'note':None,'difference_explanation':None,'external_id':None}
 if schema_version==2:
  result.update(schema_version=2,method_scheme='provider',report_cutoff_at=None,imports=[])
  result['methods']=[dict(result['methods'][0],key=k,total=None) for k in PROVIDER_METHODS]
 return result
def method_total(m):
 if 'total' in m:return m['total'] if m['used'] is not None else None
 return m['sales']+m['tip'] if m['used'] is not None and m['sales'] is not None and m['tip'] is not None else None
def normalize(raw):
 v2=raw.get('schema_version')==2;shape(raw,empty(2) if v2 else empty(),'Cierre');base=empty(2 if v2 else 1);base.update(raw)
 keys=METHODS
 if v2:
  if base['method_scheme'] not in ('legacy','provider'):raise DomainError('Esquema de medios no válido.')
  keys=PROVIDER_METHODS if base['method_scheme']=='provider' else METHODS
  base['report_cutoff_at']=instant(base['report_cutoff_at'],'Corte económico del reporte','report_cutoff_at')
  if not isinstance(base['imports'],list) or len(base['imports'])>120:raise DomainError('Trazabilidad no válida.')
 base['cash']=denominations(base['cash'],'Conteo A');base['retained']=denominations(base['retained'],'Fondo B')
 for k,label in [('counted_at','Fecha y hora del conteo'),('sealed_at','Fecha y hora de cierre de la bolsa')]:base[k]=instant(base[k],label,k)
 if base['counted_at'] and base['sealed_at'] and base['sealed_at']<base['counted_at']:raise FieldError('sealed_at','La bolsa se cerró antes del conteo indicado ('+local_time(base['counted_at'])+'). Revisa ambas horas e ingresa la hora real de cierre de la bolsa.')
 for d in DENOMINATIONS:
  a,b=base['cash'][str(d)],base['retained'][str(d)]
  if a is not None and b is not None and b>a:raise DomainError('El fondo no puede contener más piezas de una denominación que el conteo A.')
 opening=shape(base['opening'],('amount','as_of','reference'),'Apertura')
 base['opening']={'amount':amount(opening.get('amount'),'Fondo inicial'),'as_of':instant(opening.get('as_of'),'Fecha y hora del fondo al abrir','opening.as_of'),'reference':optional_text(opening.get('reference'),'Evidencia de apertura')}
 source=shape(base['cash_source'],('type','reference','basis','read_id'),'Origen de ventas en efectivo')
 if source.get('type') not in ('manual_report','toteat_sales'):raise DomainError('Origen de efectivo no válido.')
 if source.get('basis') not in ('net_sales_before_movements','report_after_movements','unknown'):raise DomainError('Indica el significado del efectivo de origen.')
 rid=source.get('read_id')
 if rid is not None and (type(rid) is not int or rid<1):raise DomainError('Lectura de origen no válida.')
 base['cash_source']={'type':source['type'],'basis':source['basis'],'reference':optional_text(source.get('reference'),'Origen del reporte'),'read_id':rid}
 methods=base['methods']
 if not isinstance(methods,list) or len(methods)!=len(keys):raise DomainError('Revisa los seis medios de pago.')
 normalized=[];seen=set()
 for m in methods:
  shape(m,('key','name','used','sales','tip','comparison',*(['total'] if v2 else [])),'Medio de pago');key=m.get('key')
  if key not in keys or key in seen:raise DomainError('Medio ausente o duplicado.')
  seen.add(key);used=m.get('used')
  if used is not None and type(used) is not bool:raise DomainError('Marca el medio como usado, no usado o pendiente.')
  sales=amount(m.get('sales'),'Ventas sin propina');tip=amount(m.get('tip'),'Propina')
  if used is False:
   if sales not in (None,0) or tip not in (None,0) or (v2 and m.get('total') not in (None,0)):raise DomainError('Un medio no usado no puede tener importes.')
   sales=tip=0 # Explicitly declared unused, never inferred from blanks.
  name=optional_text(m.get('name'),'Nombre de otro medio',100)
  c=shape(m.get('comparison') or {},('kind','reference','amount'),'Contraste independiente')
  kind=c.get('kind') or None
  if kind not in (None,'terminal','transfer_list','wallet_report','other'):raise DomainError('Tipo de contraste independiente no válido.')
  normalized.append({'key':key,'name':name,'used':used,'sales':sales,'tip':tip,'comparison':{'kind':kind,'reference':optional_text(c.get('reference'),'Referencia independiente'),'amount':amount(c.get('amount'),'Monto independiente')}})
  if v2:normalized[-1]['total']=0 if used is False else amount(m.get('total'),'Total cobrado')
 base['methods']=sorted(normalized,key=lambda m:keys.index(m['key']))
 if type(base['movements_complete']) is not bool:raise DomainError('Confirma si terminaste de detallar movimientos.')
 moves=base['movements']
 if not isinstance(moves,list) or len(moves)>60:raise DomainError('Hasta 60 movimientos por cierre.')
 rows=[];ids=set();refs=set()
 for index,m in enumerate(moves):
  shape(m,('id','kind','direction','amount','occurred_at','origin','destination','reason','responsible','source','reference','cash_event_id','payment_source','payment_id','toteat_id','attachment_id'),'Movimiento')
  mid=m.get('id')
  if not isinstance(mid,str) or not re.fullmatch(r'[a-f0-9]{32}',mid) or mid in ids:raise DomainError('Identificador de movimiento ausente o duplicado.')
  ids.add(mid)
  if m.get('kind') not in ('income','expense','custody','supplier','transfer') or m.get('direction') not in ('in','out'):raise DomainError('Tipo o dirección del movimiento no válido.')
  if m['kind']=='income' and m['direction']!='in' or m['kind'] in ('expense','supplier') and m['direction']!='out':raise DomainError('Dirección incompatible con el movimiento.')
  if m.get('source') not in ('manual_report','erp_event','toteat_transcription'):raise DomainError('Declara el origen del movimiento.')
  row={k:m.get(k) for k in ('id','kind','direction','source','cash_event_id','payment_source','payment_id','toteat_id','attachment_id')}
  row.update(amount=amount(m.get('amount'),'Monto del movimiento'),occurred_at=instant(m.get('occurred_at'),'Fecha y hora del movimiento '+str(index+1),'movements.'+str(index)+'.occurred_at'))
  for key in ('origin','destination','reason','responsible','reference'):row[key]=optional_text(m.get(key),key,300)
  if row['amount']==0:raise DomainError('Un movimiento requiere un monto mayor que cero.')
  if row['reference']:
   ref=row['source']+':'+row['reference'].casefold()
   if ref in refs:raise DomainError('La misma referencia aparece dos veces; une sus vínculos en un único movimiento.')
   refs.add(ref)
  for key in ('cash_event_id','attachment_id'):
   if row[key] and (not isinstance(row[key],str) or not re.fullmatch(r'[a-f0-9]{32}',row[key])):raise DomainError('Vínculo no válido.')
  row['toteat_id']=optional_text(row['toteat_id'],'Identificador Toteat',120)
  if row['payment_source'] not in (None,'','ap_payments','ap_account_entries'):raise DomainError('Origen del pago no válido.')
  row['payment_source']=row['payment_source'] or None
  if row['payment_id'] is not None and (type(row['payment_id']) is not int or row['payment_id']<1):raise DomainError('Pago no válido.')
  if bool(row['payment_source'])!=(row['payment_id'] is not None):raise DomainError('Completa origen e identificador del pago.')
  rows.append(row)
 base['movements']=rows
 for key in ('note','difference_explanation','external_id'):base[key]=optional_text(base[key],key,1500 if key!='external_id' else 150)
 return base

def input_hash(data):return hashlib.sha256(canonical(data).encode()).hexdigest()

def calculate(data,review=None,source_reference=None):
 a=denom_total(data['cash']);b=denom_total(data['retained']);missing=[];warnings=[]
 if a is None:missing.append('Completa todas las denominaciones de A, incluso las no usadas con cero.')
 if b is None:missing.append('Completa todas las denominaciones de B.')
 if not data['counted_at']:missing.append('Falta la hora del conteo.')
 if not data['sealed_at']:missing.append('Falta la hora de sellado.')
 if not data['movements_complete']:missing.append('Revisa y confirma la lista de movimientos, incluso si no hubo.')
 before=after=0;movement_missing=False;refs=[]
 opening=data['opening']
 for index,m in enumerate(data['movements']):
  required=('amount','occurred_at','origin','destination','reason','responsible','reference')
  if any(m.get(k) is None for k in required):missing.append('Completa monto, hora, referencia y responsables de cada movimiento.');movement_missing=True;continue
  path='movements.'+str(index)+'.occurred_at'
  if data['sealed_at'] and m['occurred_at']>data['sealed_at']:raise FieldError(path,'El movimiento '+str(index+1)+' ocurrió después de cerrar la bolsa ('+local_time(data['sealed_at'])+'). Revisa su hora real o la hora de cierre de la bolsa.')
  if opening['as_of'] and m['occurred_at']<opening['as_of']:raise FieldError('opening.as_of','El fondo al abrir figura a las '+local_time(opening['as_of'])+', después del movimiento '+str(index+1)+' ('+local_time(m['occurred_at'])+'). Revisa la fecha y hora reales. Si no conoces la apertura, deja su hora vacía; la conciliación quedará pendiente.')
  if m['occurred_at']<'2026-10-16T03:00:00+00:00':raise FieldError(path,'La fecha del movimiento debe ser desde el 16/10/2026 a las 00:00 de Chile, cuando comenzó este control.')
  net=m['amount']*(1 if m['direction']=='in' else -1)
  if data['counted_at'] and m['occurred_at']>data['counted_at']:after+=net
  else:before+=net
  refs.append(m['id'])
 if opening['as_of'] and data['counted_at'] and opening['as_of']>data['counted_at']:raise FieldError('opening.as_of','La hora del fondo al abrir es posterior al conteo ('+local_time(data['counted_at'])+'). Aquí corresponde la apertura de la caja, no la hora del cierre. Corrige el dato o déjalo vacío si no lo conoces; la conciliación quedará pendiente.')
 totals=[];method_view=[]
 for m in data['methods']:
  total=method_total(m)
  if total is None:missing.append('Completa o marca no usado: '+LABELS[m['key']]+'.')
  if m['key']=='other' and m['used'] and not m['name']:missing.append('Nombra el otro medio de pago.')
  independent=m['comparison']['amount'];contrast=None # No period/base/terminal proof in this legacy field.
  if independent is not None and (not m['comparison']['kind'] or not m['comparison']['reference']):missing.append('Identifica la evidencia del contraste independiente.')
  split_matches=total==m['sales']+m['tip'] if total is not None and m['sales'] is not None and m['tip'] is not None else None
  method_view.append(dict(m,total=total,split_matches=split_matches,independent_difference=contrast,comparison_state='scope_unverified',bank_settlement=None));totals.append(total)
 cash=method_view[0]['total'];source=data['cash_source'];source_ok=False
 if source['type']=='manual_report':
  warnings.append('Transcripción manual: requiere contrastar el reporte Efectivo en caja. No es una lectura automática validada.')
  source_ok=bool(source['reference']) and source['basis']=='net_sales_before_movements'
 else:
  source_ok=bool(source['basis']=='net_sales_before_movements' and source_reference and source_reference.get('valid') and cash is not None and source_reference.get('net')==cash)
  if not source_ok:warnings.append('La lectura Toteat falta, cambió o no coincide con el efectivo neto declarado.')
 for imported in data.get('imports',[]):
  if imported['status'] in ('scope_changed','source_changed','value_changed'):
   missing.append('Resuelve el importe importado de '+LABELS[imported['method']]+': cambió su alcance, fuente o valor.')
  if imported['status'] not in ('discarded','manual_replaced'):
   if imported['method']=='cash':source_ok=False
   warnings.append('Importe importado de '+LABELS[imported['method']]+': provisional, no acredita conciliación.')
 if data.get('schema_version')==2 and data.get('report_cutoff_at')!=data.get('counted_at'):
  source_ok=False;warnings.append('El corte económico del reporte debe estar comprobado hasta el conteo físico; la conciliación permanece pendiente.')
 if source['basis']=='report_after_movements':warnings.append('El reporte ya incluye movimientos: no se descuentan otra vez. Falta separar ventas netas antes de movimientos.')
 reviewed=bool(review and review.get('input_hash')==input_hash(data))
 covered=bool(reviewed and review['opening_verified'] and review['payments_verified'] and review['movements_verified'])
 opening_ok=opening['amount'] is not None and bool(opening['as_of'] and opening['reference'])
 if not opening_ok:warnings.append('Fondo de apertura pendiente de verificar. Puedes presentar la declaración; la conciliación de efectivo seguirá pendiente.')
 expected=opening['amount']+cash+before if covered and source_ok and opening_ok and cash is not None and not movement_missing and data['movements_complete'] else None
 if expected is not None and expected<0:raise DomainError('El esperado sería negativo; revisa apertura y movimientos.')
 adjusted=a+after if a is not None and not movement_missing and data['counted_at'] and data['sealed_at'] else None
 c=adjusted-b if adjusted is not None and b is not None else None
 if c is not None and c<0:raise DomainError('El fondo supera el efectivo disponible al sellar.')
 difference=a-expected if a is not None and expected is not None else None
 if difference not in (None,0) and not data['difference_explanation']:missing.append('Explica la diferencia antes de presentar.')
 return {'A':a,'B':b,'C':c,'post_count_net':after if not movement_missing else None,'cash_at_seal':adjusted,'before_count_net':before if not movement_missing else None,
  'expected':expected,'difference':difference,'methods':method_view,'all_methods_total':sum(totals) if all(v is not None for v in totals) else None,
  'coverage':'reviewed' if expected is not None else 'incomplete','review_matches':reviewed,'source_matches':source_ok,
  'missing':list(dict.fromkeys(missing)),'warnings':warnings,'can_submit':not missing and c is not None,
  'can_reconcile':not missing and expected is not None and difference==0,
  'notice':'Diferencia = A − esperado. La bolsa C excluye el fondo y no se compara con el esperado. Propinas bancarias mensuales no generan egreso diario.'}
