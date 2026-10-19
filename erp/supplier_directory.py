"""Operational supplier profiles reuse ap_suppliers; no external reads/imports."""
import json
import re

from erp import payables as ap
from erp.domain import DomainError
from erp.payable_duplicates import normalized
from erp.store import now

TEXT_FIELDS={'products_text':2000,'schedule_note':1000,'order_channel':160,'order_contact':160,'order_url':1000,'substitute_text':500,'source_reference':1000}
NULL_FIELDS=(*TEXT_FIELDS,'order_days','cutoff_time','delivery_days','delivery_lead_days','payment_term_days','minimum_amount_clp','minimum_quantity','minimum_unit_id','substitute_ids')


def nullable_text(value,label,maximum):
    return None if value is None or value=='' else ap.text(value,label,maximum=maximum)


def days(value,label):
    if value is None:return None
    if not isinstance(value,list) or len(value)>7 or any(type(n) is not int or not 0<=n<=6 for n in value) or len(set(value))!=len(value):
        raise DomainError(label+': selecciona días distintos de lunes a domingo.')
    return sorted(value)


def clean(db,data,supplier_id):
    result={key:nullable_text(data.get(key),key,limit) for key,limit in TEXT_FIELDS.items()}
    if result['order_url']:
        from urllib.parse import urlsplit
        parsed=urlsplit(result['order_url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise DomainError('Enlace de pedido: usa una dirección HTTPS sin credenciales.')
    result['order_days_json']=json.dumps(days(data.get('order_days'),'Días de pedido')) if data.get('order_days') is not None else None
    result['delivery_days_json']=json.dumps(days(data.get('delivery_days'),'Días de entrega')) if data.get('delivery_days') is not None else None
    cutoff=nullable_text(data.get('cutoff_time'),'Hora límite',5)
    if cutoff and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',cutoff):raise DomainError('Hora límite: usa HH:MM.')
    result['cutoff_time']=cutoff
    for field,maximum,minimum in [('delivery_lead_days',3650,0),('payment_term_days',3650,0),('minimum_amount_clp',ap.MAX_AMOUNT,1)]:
        result[field]=None if data.get(field) in (None,'') else ap.integer(data[field],field,minimum,maximum)
    amount=data.get('minimum_quantity');unit_id=data.get('minimum_unit_id')
    if (amount in (None,''))!=(unit_id in (None,'')):raise DomainError('El mínimo en cantidad necesita una unidad explícita, o ambos campos vacíos.')
    result.update(minimum_quantity=None,minimum_unit_id=None)
    if amount not in (None,''):
        from erp.procurement import quantity,in_unit,decimal
        unit_id=ap.integer(unit_id,'Unidad del mínimo');amount=in_unit(db,quantity(amount,'Cantidad mínima'),unit_id)
        result.update(minimum_quantity=decimal(amount),minimum_unit_id=unit_id)
    substitutes=data.get('substitute_ids')
    if substitutes is not None:
        if not isinstance(substitutes,list) or len(substitutes)>20:raise DomainError('Indica hasta 20 proveedores sustitutos existentes.')
        for key in substitutes:
            ap.integer(key,'Proveedor sustituto');ap.record(db,'ap_suppliers',key)
            if key==supplier_id:raise DomainError('El proveedor no puede ser su propio sustituto.')
        if len(set(substitutes))!=len(substitutes):raise DomainError('Hay sustitutos repetidos.')
    result['substitute_ids_json']=None if substitutes is None else json.dumps(substitutes)
    if type(data.get('reviewed')) is not bool:raise DomainError('Indica si los datos operativos ya fueron revisados.')
    result['reviewed']=int(data['reviewed'])
    return result


def list_profiles(db):
    result=[]
    for supplier in db.execute('SELECT id,name,tax_id,notes,contact,phone,email,terms,bank,account_type,account_number,account_holder,account_tax_id,version FROM ap_suppliers ORDER BY name COLLATE NOCASE,id'):
        row=dict(supplier);profile=db.execute('SELECT * FROM pc_supplier_profiles WHERE supplier_id=?',(row['id'],)).fetchone()
        row['profile']={key:None for key in NULL_FIELDS}
        row['profile'].update(version=0,reviewed=False,actor=None,updated_at=None)
        if profile:
            row['profile'].update(dict(profile))
            for field in ('order_days','delivery_days','substitute_ids'):
                value=row['profile'].pop(field+'_json');row['profile'][field]=json.loads(value) if value is not None else None
            row['profile']['reviewed']=bool(row['profile']['reviewed'])
        row['profile']['alerts_enabled']=False
        row['profile']['schedule_relationship']=None
        result.append(row)
    return result


def save(db,key,data,actor):
    from erp.procurement import audit
    supplier=ap.record(db,'ap_suppliers',key);ap.version({'version':data.get('supplier_version')},supplier)
    before=next(row for row in list_profiles(db) if row['id']==key)
    expected=ap.integer(data.get('profile_version'),'Versión de ficha operativa',0)
    if expected!=before['profile']['version']:raise DomainError('La ficha operativa cambió; actualiza antes de guardar.',409)
    reason=ap.text(data.get('reason'),'Motivo o fuente de la actualización',True)
    fields=clean(db,data,key)
    contact={field:ap.text(data.get(field,supplier[field]),field,required=field=='name',maximum=1000 if field=='notes' else 160) for field in ('name','tax_id','notes','contact','phone','email','terms','bank','account_type','account_number','account_holder','account_tax_id')}
    contact['tax_id']=ap.tax_id(contact['tax_id'])
    contact['account_tax_id']=ap.tax_id(contact['account_tax_id'])
    if any(contact[field]!=supplier[field] for field in contact):
        ap.mutate(db,'supplier',dict(supplier,**contact,reason=reason),actor,key)
    fields.update(actor=actor,updated_at=now())
    if expected:
        db.execute('UPDATE pc_supplier_profiles SET '+','.join(k+'=?' for k in fields)+',version=version+1 WHERE supplier_id=?',(*fields.values(),key))
    else:
        db.execute('INSERT INTO pc_supplier_profiles(supplier_id,'+','.join(fields)+') VALUES('+','.join('?' for _ in range(len(fields)+1))+')',(key,*fields.values()))
    result=next(row for row in list_profiles(db) if row['id']==key)
    audit(db,'supplier',key,'directory_saved',actor,reason,before,result);return result


def preview(db,data):
    """Review-only mapping of normalized rows; no supplier or RUT is invented."""
    if not isinstance(data,dict) or not isinstance(data.get('rows'),list) or not 1<=len(data['rows'])<=200:
        raise DomainError('La previsualización necesita entre 1 y 200 filas normalizadas.')
    suppliers=list_profiles(db);result=[]
    for index,row in enumerate(data['rows'],1):
        if not isinstance(row,dict):raise DomainError('Fila de directorio no válida.')
        name=ap.text(row.get('supplier_name'),'Nombre del proveedor',True,160)
        rut=ap.tax_id(row.get('tax_id') or '')
        matches=[s for s in suppliers if s['tax_id']==rut] if rut else [s for s in suppliers if normalized(s['name'])==normalized(name)]
        proposed={key:row.get(key) for key in NULL_FIELDS}
        proposed.update({key:row.get(key) for key in ('contact','phone','email','terms')})
        warnings=['No se ha guardado ni creado ningún proveedor.','Días de pedido y entrega son independientes; no se deduce una relación ni se activan avisos.']
        if not rut:warnings.append('RUT no informado; la coincidencia por nombre requiere revisión humana.')
        if proposed.get('substitute_text'):warnings.append('El sustituto en texto necesita vincularse manualmente a un proveedor existente.')
        result.append({'row':index,'supplier_name':name,'tax_id':rut or None,'status':'candidate' if len(matches)==1 else 'ambiguous' if matches else 'unmatched',
                       'candidate_ids':[s['id'] for s in matches],'proposed':proposed,'warnings':warnings})
    return {'rows':result,'read_only':True,'automatic_import':False,'alerts_enabled':False}
