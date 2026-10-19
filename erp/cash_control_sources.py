"""Minimal, versioned financial projections. Provider evidence is never a cash movement."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from erp.domain import DomainError
from erp.cash_bags import canonical, stamp

ENDPOINTS = {'collection','sales','accountingmovements'}


def clp(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,str)):
        raise DomainError('Importe de origen no válido.')
    try: number=Decimal(str(value))
    except InvalidOperation: raise DomainError('Importe de origen no válido.') from None
    if not number.is_finite() or number!=number.to_integral_value() or abs(number)>10**12:
        raise DomainError('Importe CLP de origen no válido.')
    return int(number)


def utc(value):
    if not isinstance(value,str) or not value: return None
    try:
        moment=datetime.fromisoformat(value.replace('Z','+00:00'))
        if moment.year<2000:return None
        # The provider contract stores openedDate/closedDate in UTC without offset.
        return moment.replace(tzinfo=moment.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:return None


def collection(day,payload,scope):
    data=payload.get('data')
    if not isinstance(data,dict) or data.get('date')!=day or not isinstance(data.get('shifts'),dict):
        raise DomainError('Contrato collection no válido para esta fecha.')
    for key,field in [('localID','local_id'),('restaurantID','restaurant_id')]:
        if key in data and str(data[key])!=scope[field]:raise DomainError('Ámbito collection incompatible.')
    rows=[];seen=set()
    from erp.cash_toteat import collection_methods
    for shift_id,shift in data['shifts'].items():
        if not isinstance(shift,dict) or not isinstance(shift.get('registers'),dict):raise DomainError('Turno de origen incompleto.')
        for register_id,sessions in shift['registers'].items():
            if not isinstance(sessions,list):raise DomainError('Sesiones de origen incompletas.')
            for session in sessions:
                if not isinstance(session,dict):raise DomainError('Sesión de origen no válida.')
                opened=utc(session.get('openedDate'));closed=utc(session.get('closedDate'))
                if not opened:raise DomainError('Apertura sin instante válido.')
                if session.get('closedDate') and not closed:raise DomainError('Cierre con fecha no válida.')
                if closed and closed<opened:raise DomainError('Cierre anterior a la apertura.')
                key=hashlib.sha256(canonical([scope,day,str(shift_id),str(register_id),opened]).encode()).hexdigest()
                if key in seen:raise DomainError('Sesión ambigua: requiere identidad del proveedor.')
                seen.add(key)
                methods=session.get('paymentMethods')
                if not isinstance(methods,list):raise DomainError('Medios de cierre ausentes.')
                cash=[m for m in methods if isinstance(m,dict) and str(m.get('paymentMethodID'))=='1000']
                if len(cash)>1:raise DomainError('Medio efectivo duplicado en cierre.')
                # Field presence proves an observed report, not theoretical vs declared semantics.
                reported=clp(cash[0]['amount']) if cash and closed else None
                opening=clp(session['initialAmount']) if session.get('initialAmount') is not None else None
                if opening is not None and opening<0:raise DomainError('Fondo de apertura negativo: requiere revisión.')
                rows.append(dict(session_key=key,business_date=day,shift_id=str(shift_id),shift_name=str(shift.get('name',''))[:100],
                    register_id=str(register_id),register_name=str(session.get('registerName',session.get('resgisterName','')))[:100],
                    opened_at=opened,closed_at=closed,opening=opening,cash_reported=reported,
                    methods=collection_methods(methods),expected=None,state='source_incomplete',reason='Falta validar teórico frente a declarado y movimientos.'))
    return {'sessions':rows,'coverage':'observed_sessions','expected':None,
            'reason':'collection no acredita movimientos ni el tipo de importe del cierre.'}


def sales(day,payload,scope):
    rows=payload.get('data')
    if not isinstance(rows,list):raise DomainError('Contrato sales no válido.')
    groups={};seen={};issues=[]
    for row in rows:
        if not isinstance(row,dict):raise DomainError('Pago de origen no válido.')
        order=row.get('orderId');payment=row.get('paymentId')
        if order is None or payment is None:raise DomainError('Pago sin identidad.')
        key=(str(order),str(payment))
        if key in seen:
            if seen[key]!=canonical(row):raise DomainError('Pago duplicado con valores en conflicto.')
            continue
        seen[key]=canonical(row)
        register=str(row.get('registerId',''))
        if not register:raise DomainError('Pago sin caja identificada.')
        group=groups.setdefault(register,dict(register_id=register,register_name=str(row.get('registerName',''))[:100],net=0,payments=0,issues=[]))
        forms=row.get('paymentForms')
        if not isinstance(forms,list):raise DomainError('Pago sin medios.')
        cash=[f for f in forms if isinstance(f,dict) and str(f.get('id'))=='1000']
        if len(cash)>1:raise DomainError('Más de un componente efectivo: revisar atribución.')
        change=clp(row.get('change'))
        if not forms or any(not isinstance(f,dict) for f in forms):
            group['issues'].append('Medios de pago incompletos.');continue
        if any(str(f.get('method','')).upper()=='CASH' and str(f.get('id'))!='1000' for f in forms):
            group['issues'].append('Medio efectivo sin correspondencia validada.');continue
        if cash and len(forms)>1:
            group['issues'].append('Pago mixto pendiente de atribución verificada.');continue
        if str(row.get('fiscalType','')).upper()=='NC' or row.get('referencedPayment') or row.get('referencedPayment*'):
            group['issues'].append('Devolución pendiente de contrato verificado.');continue
        if change<0 or (change and not cash):
            group['issues'].append('Vuelto sin atribución validada.');continue
        if cash:
            gross=clp(cash[0].get('amount'))
            if gross<change or gross<0:
                group['issues'].append('Efectivo/vuelto incompatible.');continue
            group['net']+=gross-change;group['payments']+=1
    for group in groups.values():
        if group['issues']:group['net']=None
        group['state']='partial' # No complete-response marker or closed-shift guarantee in this contract.
    from erp.cash_toteat import sales_payments
    return {'registers':list(groups.values()),'payments':sales_payments(rows,scope),'projection_version':3,'coverage':'observed_payments',
            'reason':'Ventas observadas por caja; no acredita egresos ni cierre completo. Propinas físicas ya incluidas.'}


def record(store,day,endpoint,payload=None,scope=None,error=None,read_at=None):
    if endpoint not in ENDPOINTS:raise DomainError('Fuente no admitida.')
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise DomainError('Fecha de origen no válida.')
    scope=scope or {}
    if set(scope)!={'restaurant_id','local_id'} or any(not isinstance(v,str) or not v.isdigit() for v in scope.values()):
        raise DomainError('Ámbito del origen no válido.')
    normal={};state='error';reason=error or ''
    if not error:
        if not isinstance(payload,dict) or payload.get('ok') is not True:reason='Proveedor no confirmó éxito.'
        else:
            try:
                if endpoint=='collection':normal=collection(day,payload,scope)
                elif endpoint=='sales':normal=sales(day,payload,scope)
                else:raise DomainError('Movimientos sin contrato real validado: no se contabilizan.')
                state='partial';reason=normal['reason']
            except DomainError as exc:reason=str(exc)
            except (KeyError,TypeError,ValueError):reason='Contrato de origen incompleto o con tipos no válidos.'
    # Persist only minimal normalized evidence; never raw responses/credentials/customer data.
    normalized=canonical(normal);fingerprint=hashlib.sha256(normalized.encode()).hexdigest()
    at=utc(read_at) if read_at else stamp()
    if not at:raise DomainError('Fecha de lectura no válida.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT scope FROM cc_source_reads LIMIT 1').fetchone()
        if prior and prior['scope']!=canonical(scope):raise DomainError('El ámbito de Toteat cambió; revisión requerida.',409)
        cursor=db.execute('INSERT INTO cc_source_reads(business_date,endpoint,state,reason,read_at,scope,payload_hash,normalized_json) VALUES(?,?,?,?,?,?,?,?)',
            (day,endpoint,state,reason[:500],at,canonical(scope),fingerprint,normalized))
        return {'id':cursor.lastrowid,'state':state,'reason':reason}


def latest(db,start,end):
    return [dict(r) for r in db.execute('''SELECT s.* FROM cc_source_reads s JOIN
      (SELECT business_date,endpoint,MAX(id) id FROM cc_source_reads WHERE business_date BETWEEN ? AND ? GROUP BY business_date,endpoint) x
      ON s.id=x.id ORDER BY s.business_date,s.endpoint''',(start,end))]
