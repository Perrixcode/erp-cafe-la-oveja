"""Ingreso de contingencia y conciliación local. Nunca escribe en Toteat."""
from collections import Counter
from contextlib import nullcontext
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
import uuid
from zoneinfo import ZoneInfo

from erp.domain import DomainError, CHANNELS
from erp.store import clean_text,now
from erp.catalog_import import canonical


def actor(partner):
    return (clean_text(partner.get('name'),'Socio',maximum=80),
            clean_text(partner.get('username'),'Cuenta de socio',maximum=80))


def customer_fields(data):
    if not isinstance(data,dict):raise DomainError('Completa los datos del pedido SOS.')
    value={k:clean_text(data.get(k,''),label,required=k!='delivery_address',maximum=500 if k=='delivery_address' else 160)
           for k,label in [('customer','Cliente'),('customer_phone','Teléfono'),('delivery_address','Dirección')]}
    if not re.fullmatch(r'[+\d ()-]{7,25}',value['customer_phone']):raise DomainError('Revisa el teléfono.')
    value['fulfillment']=data.get('fulfillment')
    if value['fulfillment'] not in ('retiro','despacho'):raise DomainError('Selecciona retiro o delivery.')
    if value['fulfillment']=='despacho' and not value['delivery_address']:raise DomainError('Delivery requiere dirección.')
    value['pickup_at']=data.get('pickup_at')
    try:
        parsed=datetime.strptime(value['pickup_at'],'%Y-%m-%dT%H:%M')
        local=parsed.replace(tzinfo=ZoneInfo('America/Santiago'))
        if parsed.strftime('%Y-%m-%dT%H:%M')!=value['pickup_at'] or local.astimezone(timezone.utc).astimezone(local.tzinfo).replace(tzinfo=None)!=parsed:raise ValueError
    except (TypeError,ValueError):raise DomainError('Fecha y hora no válidas en Chile.') from None
    return value


def items_for(store,rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=50:raise DomainError('Agrega entre 1 y 50 productos enteros.')
    catalog={p['sku']:p for p in store.catalog()};items=[];seen=set()
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict) or not isinstance(row.get('sku'),str) or row['sku'] not in catalog:raise DomainError('Selecciona un producto del catálogo local.')
        product=catalog[row['sku']];quantity=row.get('quantity')
        if type(quantity) is not int or not 1<=quantity<=999:raise DomainError('Cantidad entera entre 1 y 999.')
        if product['sku'] in seen:raise DomainError('Agrupa la cantidad del mismo producto en una sola línea.')
        seen.add(product['sku']);items.append(dict(source_item_id=str(index),sku=product['sku'],flavor=product['flavor'],size=product['size'],quantity=quantity,kind='torta',comments=''))
    return items


def totals(items):
    result=Counter()
    for item in items:result[item['sku']]+=item['quantity']
    return dict(result)


def received_totals(store,received):
    products={str(p['source_product']['idToteat']):p['sku'] for p in store.catalog() if p.get('source_product')}
    result=Counter()
    for item in received.get('items',[]):
        sku=products.get(str(item.get('productCodeToteat')))
        try:quantity=Decimal(str(item.get('quantity')))
        except Exception:raise DomainError('Cantidad fuente inválida.') from None
        if not sku or not quantity.is_finite() or quantity!=quantity.to_integral_value() or not 1<=quantity<=999:raise DomainError('Productos de la comanda requieren revisión.')
        result[sku]+=int(quantity)
    if not result:raise DomainError('La comanda no tiene productos verificados.')
    return dict(result)


def source_identity(received):
    if not isinstance(received,dict) or not all(isinstance(received.get(k),str) and received[k] for k in ('restaurant_id','local_id','order_id')):raise DomainError('Selecciona una comanda recibida y verificada.')
    return json.dumps([received[k] for k in ('restaurant_id','local_id','order_id')],separators=(',',':'))


def bind_in_transaction(store,db,order,received,partner,reason):
    name,username=actor(partner);source_id=source_identity(received)
    if order['source']!='manual-sos':raise DomainError('Solo se vinculan pedidos de origen Manual SOS.')
    from erp.reception import require_bindable
    require_bindable(db,source_id)
    if totals(order['items'])!=received_totals(store,received):raise DomainError('Los productos y cantidades no coinciden. Revisa ambos pedidos antes de vincular.')
    existing=db.execute('SELECT order_id FROM toteat_order_links WHERE source_id=? OR order_id=?',(source_id,order['id'])).fetchone()
    if existing:
        link=db.execute('SELECT source_id FROM toteat_order_links WHERE order_id=?',(order['id'],)).fetchone()
        if existing[0]==order['id'] and link and link[0]==source_id:return False
        raise DomainError('La comanda o el pedido ya están vinculados.',409)
    if db.execute("SELECT 1 FROM orders WHERE source='toteat' AND source_id=?",(source_id,)).fetchone():raise DomainError('Esta comanda ya tiene un pedido agendado. No se crea una segunda demanda.',409)
    db.execute('INSERT INTO toteat_order_links VALUES(?,?,?,?,?)',(source_id,order['id'],json.dumps(received,ensure_ascii=False),username,now()))
    store._event(db,order['id'],None,'vinculo_toteat',name,reason,None,{'source_id':source_id,'socio_autenticado':username})
    db.execute("UPDATE reconciliation_reviews SET status='linked',version=version+1,updated_at=? WHERE source_id=?",(now(),source_id))
    return True


def create(store,data,partner,reason,request_id,received=None,authorize_now=False,reception_revision=None):
    if store.operating_mode()!='toteat-local':raise DomainError('SOS requiere la instalación con catálogo local.')
    name,username=actor(partner);reason=clean_text(reason,'Motivo de contingencia',maximum=500)
    try:request_id=str(uuid.UUID(request_id))
    except (ValueError,TypeError,AttributeError):raise DomainError('Identificador de solicitud inválido.') from None
    fields=customer_fields(data);items=items_for(store,data.get('items'));channel=data.get('channel')
    if channel not in CHANNELS:raise DomainError('Selecciona el canal de origen.')
    payment=data.get('payment_status')
    if payment not in ('unpaid','paid_manual'):raise DomainError('Selecciona pendiente de pago o pago verificado por socio.')
    evidence=clean_text(data.get('payment_evidence',''),'Referencia de verificación del pago',required=payment=='paid_manual',maximum=500)
    if payment=='paid_manual' and data.get('payment_verified') is not True:raise DomainError('Confirma que el socio verificó el pago; un comprobante solo no acredita recepción.')
    if payment=='unpaid' and data.get('payment_verified'):raise DomainError('Un pedido sin pago no puede marcarse pagado.')
    if type(data.get('is_test',False)) is not bool:raise DomainError('Indicador de prueba inválido.')
    is_test=data.get('is_test',False)
    comments=clean_text(data.get('comments',''),'Nota de contingencia',required=False,maximum=1000)
    normalized=dict(fields,items=items,channel=channel,payment_status=payment,payment_evidence=evidence,is_test=is_test,comments=comments,source_id=source_identity(received) if received else None,socio=username,authorize_now=authorize_now)
    digest=hashlib.sha256(canonical(normalized).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT order_id,request_digest FROM manual_scheduling WHERE request_id=?',(request_id,)).fetchone()
        if prior:
            if prior['request_digest']!=digest:raise DomainError('La misma solicitud llegó con datos diferentes.',409)
            return store._get(db,prior['order_id'])
        if reception_revision is not None:
            from erp.reception import check_revision
            check_revision(db,received,reception_revision)
        # Una recarga con otro request_id tampoco crea el mismo encargo dos veces.
        for row in db.execute('SELECT id FROM orders').fetchall():
            saved=store._get(db,row[0])
            if saved['simulation_archived'] or all(i['status'] in ('entregado','cancelado') for i in saved['items']):continue
            if bool(saved['is_demo'])==is_test and normalized_name(saved['customer'])==normalized_name(fields['customer']) and saved['pickup_at']==fields['pickup_at'] and totals(saved['items'])==totals(items):
                raise DomainError('Ya hay un pedido coincidente. Revísalo antes de crear otro SOS.',409)
        timestamp=now();data_order=dict(source='manual-sos',source_id='SOS-'+uuid.uuid4().hex[:12].upper(),channel=channel,customer=fields['customer'],pickup_at=fields['pickup_at'],fulfillment=fields['fulfillment'],comments=comments,payment_confirmed=int(payment=='paid_manual'),is_demo=int(is_test),created_at=timestamp,updated_at=timestamp)
        cursor=db.execute('INSERT INTO orders('+','.join(data_order)+') VALUES('+','.join('?' for _ in data_order)+')',list(data_order.values()));order_id=cursor.lastrowid
        for item in items:store._insert_item(db,order_id,item)
        db.execute("INSERT INTO order_scheduling VALUES(?,'scheduled',?)",(order_id,fields['customer_phone']))
        db.execute('INSERT INTO order_customer_changes VALUES(?,?,?,?,?,?)',(order_id,fields['customer'],fields['customer_phone'],fields['pickup_at'],fields['fulfillment'],fields['delivery_address']))
        db.execute("INSERT INTO documents(order_id,source,status) VALUES(?,'toteat','pending')",(order_id,))
        db.execute('INSERT INTO manual_scheduling VALUES(?,?,?,?,?,?,?,?)',(order_id,request_id,digest,'scheduled' if payment=='paid_manual' else 'draft',payment,evidence,username,timestamp))
        if is_test:db.execute('INSERT INTO simulation_orders VALUES(?,?,NULL)',(order_id,timestamp))
        order=store._get(db,order_id)
        store._event(db,order_id,None,'sos_creado',name,reason,None,dict(order,socio_autenticado=username))
        if received:bind_in_transaction(store,db,order,received,partner,reason)
        if authorize_now and payment=='unpaid':
            db.execute("UPDATE manual_scheduling SET status='scheduled' WHERE order_id=?",(order_id,))
            store._touch(db,order_id)
            store._event(db,order_id,None,'agendado_sin_pago',name,reason,order,dict(store._get(db,order_id),socio_autenticado=username))
        if reception_revision is not None:
            from erp.reception import record_scheduled
            record_scheduled(store,db,received,order_id,partner,reason)
        return store._get(db,order_id)


def authorize_unpaid(store,order_id,partner,reason,version):
    name,username=actor(partner);reason=clean_text(reason,'Motivo de autorización sin pago',maximum=500)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');before=store._get(db,order_id);manual=before.get('manual_scheduling')
        if not manual or manual['payment_status']!='unpaid':raise DomainError('Este pedido no tiene una excepción sin pago pendiente.',409)
        if manual['status']=='scheduled':return before
        store._check_version(before,version)
        db.execute("UPDATE manual_scheduling SET status='scheduled' WHERE order_id=?",(order_id,))
        store._touch(db,order_id)
        after=store._get(db,order_id)
        store._event(db,order_id,None,'agendado_sin_pago',name,reason,before,dict(after,socio_autenticado=username))
        return after


def list_orders(store):
    with store.connect() as db:return [store._get(db,r[0]) for r in db.execute('SELECT order_id FROM manual_scheduling ORDER BY created_at DESC')]


def list_reviews(store):
    with store.connect() as db:return [dict(json.loads(r['payload_json']),source_key=r['source_id'],version=r['version']) for r in db.execute("SELECT * FROM reconciliation_reviews WHERE status='pending' ORDER BY updated_at")]


def normalized_name(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD',value.casefold()) if not unicodedata.combining(c)).split())


def sale_totals(store,candidate):
    products={str(p['source_product']['idToteat']):p['sku'] for p in store.catalog() if p.get('source_product')};result=Counter()
    for row in candidate['products']:
        sku=products.get(str(row.get('catalog_id')))
        try:count=Decimal(str(row.get('quantity')))
        except Exception:raise ValueError('invalid_source_quantity') from None
        if not sku or not count.is_finite() or count!=count.to_integral_value() or not 1<=count<=999:raise ValueError('invalid_source_quantity')
        result[sku]+=int(count)
    return dict(result)


def hold_possible_duplicate(store,source_id,transaction,candidate,parsed,is_test=False,db=None):
    with (store.connect() if db is None else nullcontext(db)) as db:
        if db.execute("SELECT 1 FROM orders WHERE source='toteat' AND source_id=?",(source_id,)).fetchone():return
        incoming=sale_totals(store,candidate);possible=[]
        for row in db.execute('SELECT m.order_id FROM manual_scheduling m LEFT JOIN toteat_order_links l ON l.order_id=m.order_id WHERE l.order_id IS NULL').fetchall():
            order=store._get(db,row[0])
            if bool(order['is_demo'])!=bool(is_test) or order['simulation_archived'] or all(i['status'] in ('entregado','cancelado') for i in order['items']):continue
            same_day=order['pickup_at'][:10]==(parsed['pickup_at'] or '')[:10]
            same_name=normalized_name(order['customer'])==normalized_name(parsed['customer'])
            phone=lambda s:re.sub(r'\D','',s)
            same_phone=bool(phone(parsed['customer_phone'])) and phone(parsed['customer_phone'])==phone(order['customer_phone'])
            if totals(order['items'])==incoming and (same_day or same_name or same_phone):
                possible.append({'order_id':order['id'],'customer':order['customer'],'pickup_at':order['pickup_at'],'is_test':order['is_demo']})
        if not possible:return
        payload={'order_id':str(transaction['orderId']),'customer':parsed['customer'],'pickup_at':parsed['pickup_at'],'original_comment':transaction.get('comment',''),'candidates':possible}
        digest=hashlib.sha256(canonical(payload).encode()).hexdigest()
        review=db.execute('SELECT digest,status FROM reconciliation_reviews WHERE source_id=?',(source_id,)).fetchone()
        if review and review['digest']==digest and review['status']=='distinct':return
        if not review or review['digest']!=digest:
            db.execute("INSERT INTO reconciliation_reviews(source_id,digest,payload_json,status,updated_at) VALUES(?,?,?,'pending',?) ON CONFLICT(source_id) DO UPDATE SET digest=excluded.digest,payload_json=excluded.payload_json,status='pending',version=version+1,updated_at=excluded.updated_at",(source_id,digest,json.dumps(payload,ensure_ascii=False),now()))
    raise ValueError('possible_sos_duplicate')


def resolve(store,received,order_id,decision,partner,reason,version):
    name,username=actor(partner);reason=clean_text(reason,'Motivo de conciliación',maximum=500);source_id=source_identity(received)
    if decision not in ('link','distinct'):raise DomainError('Selecciona vincular o confirmar que es otro pedido.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');review=db.execute('SELECT * FROM reconciliation_reviews WHERE source_id=?',(source_id,)).fetchone()
        if review and review['status']=='distinct' and decision=='distinct':
            return {'decision':'distinct','waiting_next_sales_read':True}
        existing=db.execute('SELECT order_id FROM toteat_order_links WHERE source_id=?',(source_id,)).fetchone()
        if decision=='link' and existing and type(order_id) is int and existing[0]==order_id:
            return store._get(db,order_id)
        if review and (type(version) is not int or review['version']!=version):raise DomainError('La revisión cambió; actualiza antes de resolver.',409)
        if decision=='distinct':
            if not review:raise DomainError('No existe una coincidencia pendiente para descartar.')
            payload=json.loads(review['payload_json']);payload['resolution']={'partner':username,'reason':reason,'at':now()}
            db.execute("UPDATE reconciliation_reviews SET status='distinct',payload_json=?,version=version+1,updated_at=? WHERE source_id=?",(json.dumps(payload,ensure_ascii=False),now(),source_id))
            for candidate in payload['candidates']:
                store._event(db,candidate['order_id'],None,'conciliacion_distinta',name,reason,None,{'source_id':source_id,'socio_autenticado':username})
            return {'decision':'distinct','waiting_next_sales_read':True}
        if type(order_id) is not int:raise DomainError('Selecciona el pedido SOS.')
        order=store._get(db,order_id)
        if bind_in_transaction(store,db,order,received,partner,reason):store._touch(db,order_id)
        return store._get(db,order_id)


def reconcile_sale(store,source_id,transaction,candidate,money,is_test,receipt):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        from erp.reception import guard_import
        guard_import(db,source_id)
        link=db.execute('SELECT order_id FROM toteat_order_links WHERE source_id=?',(source_id,)).fetchone()
        if not link:return None
        order=store._get(db,link[0])
        if bool(order['is_demo'])!=bool(is_test):raise ValueError('sos_test_scope_mismatch')
        if totals(order['items'])!=sale_totals(store,candidate):raise ValueError('sos_product_mismatch')
        evidence={'source':'sales','order_id':str(transaction['orderId']),'payment':money,'warnings':[],
                  'original_comment':transaction.get('comment','') if isinstance(transaction.get('comment'),str) else '',
                  'is_test':is_test,'sos_source_preserved':True}
        digest=hashlib.sha256(canonical({'evidence':evidence,'products':candidate['products']}).encode()).hexdigest()
        previous=db.execute('SELECT source_digest FROM toteat_scheduling WHERE order_id=?',(order['id'],)).fetchone()
        if previous and previous[0]==digest:
            if receipt:store._attach_receipt(db,order['id'],money['payment_id'],receipt)
            return store._get(db,order['id'])
        db.execute('UPDATE orders SET payment_confirmed=? WHERE id=?',(int(money['state']=='paid'),order['id']))
        db.execute("UPDATE manual_scheduling SET status='scheduled',payment_status=? WHERE order_id=?",(money['state'],order['id']))
        db.execute('INSERT INTO toteat_scheduling VALUES(?,?,?) ON CONFLICT(order_id) DO UPDATE SET source_digest=excluded.source_digest,payload_json=excluded.payload_json',(order['id'],digest,json.dumps(evidence,ensure_ascii=False)))
        if receipt:store._attach_receipt(db,order['id'],money['payment_id'],receipt)
        store._touch(db,order['id']);after=store._get(db,order['id'])
        store._event(db,order['id'],None,'pago_toteat_recibido','Lector Toteat','Venta cerrada vinculada por identidad verificada; datos SOS conservados',order,after)
        return after
