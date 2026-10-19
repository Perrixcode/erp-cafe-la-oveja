"""Local purchase/receipt records and exact, separate ingredient movements.

No external ordering, finance entries, cake stock or automatic initial balances.
"""
from decimal import Decimal
import hashlib
import json
import re
import sqlite3

from erp import payables as ap
from erp.domain import DomainError
from erp.payable_duplicates import normalized
from erp.store import now

SCALE = 1_000_000
MAX_QUANTITY = 999_999_999_999_999


def reference_key(value):
    key=''.join(char for char in normalized(value) if char.isalnum())
    return str(int(key)) if key and key.isascii() and key.isdigit() else key


def quantity(value, label='Cantidad', zero=False):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,6})?',value):
        raise DomainError(label+': usa texto decimal positivo con hasta seis decimales, sin redondear.')
    result=int(Decimal(value)*SCALE)
    if result < (0 if zero else 1) or result>MAX_QUANTITY:
        raise DomainError(label+': cantidad fuera del rango permitido.')
    return result


def decimal(value):
    return format(Decimal(value)/SCALE,'f').rstrip('0').rstrip('.') if value%SCALE else str(value//SCALE)


def in_unit(db, value, unit_id):
    unit=ap.record(db,'pc_units',unit_id)
    if value % (10**(6-unit['precision'])):
        raise DomainError('La cantidad no respeta los decimales configurados para '+unit['code']+'.')
    return value


def base_quantity(db, amount, line):
    product=amount*line['base_ratio_micros']
    if product%SCALE or product//SCALE>MAX_QUANTITY:
        raise DomainError('La equivalencia requiere redondeo o supera el rango permitido; revisa las unidades.')
    return in_unit(db,product//SCALE,line['base_unit_id'])


def audit(db, entity, key, action, actor, reason='', before=None, after=None):
    db.execute('INSERT INTO pc_events(entity,entity_id,action,actor,reason,created_at,before_json,after_json) VALUES(?,?,?,?,?,?,?,?)',
               (entity,key,action,actor,reason,now(),json.dumps(before or {},ensure_ascii=False),json.dumps(after or {},ensure_ascii=False)))


def touch_order(db,key):
    db.execute('UPDATE pc_orders SET version=version+1 WHERE id=?',(key,))


def order_view(db,key):
    order=ap.record(db,'pc_orders',key)
    order['supplier_name']=ap.record(db,'ap_suppliers',order['supplier_id'])['name']
    order['warehouse_name']=ap.record(db,'pc_warehouses',order['warehouse_id'])['name']
    lines=[]
    for item in db.execute('SELECT * FROM pc_order_lines WHERE order_id=? ORDER BY id',(key,)):
        row=dict(item)
        sums=db.execute('''SELECT COALESCE(SUM(l.accepted_micros),0),COALESCE(SUM(l.rejected_micros),0)
            FROM pc_receipt_lines l JOIN pc_receipts r ON r.id=l.receipt_id
            WHERE l.order_line_id=? AND r.voided_at IS NULL''',(row['id'],)).fetchone()
        row.update(accepted_micros=sums[0],rejected_micros=sums[1],pending_micros=row['ordered_micros']-sums[0]-row['cancelled_micros'])
        for field in ('ordered','accepted','rejected','pending','cancelled'):row[field]=decimal(row[field+'_micros'])
        lines.append(row)
    order['lines']=lines
    order['state']='cancelled' if order['cancelled_at'] else 'complete' if all(r['pending_micros']==0 for r in lines) else 'partial' if any(r['accepted_micros'] for r in lines) else 'open'
    order['overdue']=bool(order['expected_on'] and order['expected_on']<ap.local_day() and order['state'] in ('open','partial'))
    order['receipts']=[]
    for receipt in db.execute('SELECT * FROM pc_receipts WHERE order_id=? ORDER BY id DESC',(key,)):
        row=dict(receipt);row['lines']=[dict(r) for r in db.execute('SELECT * FROM pc_receipt_lines WHERE receipt_id=? ORDER BY id',(row['id'],))]
        for line in row['lines']:
            line['accepted']=decimal(line['accepted_micros']);line['rejected']=decimal(line['rejected_micros'])
        document=db.execute('SELECT id FROM pc_stock_documents WHERE receipt_id=?',(row['id'],)).fetchone()
        row['stock_document_id']=document[0] if document else None
        from erp import receipt_warehouse
        receipt_warehouse.enrich(db,row)
        order['receipts'].append(row)
    order['invoices']=[dict(r) for r in db.execute('''SELECT l.*,i.folio,i.amount,i.voided_at AS invoice_voided_at,i.supplier_id AS invoice_supplier_id
        FROM pc_invoice_links l JOIN ap_invoices i ON i.id=l.invoice_id WHERE l.order_id=? ORDER BY l.id''',(key,))]
    order['issues']=[dict(r) for r in db.execute('SELECT * FROM pc_issues WHERE order_id=? ORDER BY id DESC',(key,))]
    order['open_issues']=sum(not r['resolved_at'] for r in order['issues'])
    order['missing_invoice']=not any(not r['voided_at'] and not r['invoice_voided_at'] for r in order['invoices'])
    return order


def execute(store,action,data,actor,target=None):
    if not isinstance(data,dict):raise DomainError('Se espera un objeto JSON.')
    request=ap.text(data.get('request_id'),'Identificador de operación',True,100)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request):raise DomainError('Identificador de operación no válido.')
    signature=hashlib.sha256(json.dumps([action,target,actor,data],sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT * FROM pc_requests WHERE request_id=?',(request,)).fetchone()
        if prior:
            if prior['signature']!=signature:raise DomainError('La solicitud ya se usó con otros datos.',409)
            return json.loads(prior['response_json'])
        try:result=mutate(db,action,data,actor,target)
        except sqlite3.IntegrityError:
            raise DomainError('Ya existe ese código, equivalencia o referencia. Revisa el registro existente.',409) from None
        db.execute('INSERT INTO pc_requests VALUES(?,?,?)',(request,signature,json.dumps(result)))
        return result


def link_invoice(db,order,invoice_id,invoice_version,actor,reason):
    invoice=ap.record(db,'ap_invoices',ap.integer(invoice_id,'Factura'))
    ap.version({'version':invoice_version},invoice)
    if invoice['supplier_id']!=order['supplier_id'] or invoice['voided_at']:
        raise DomainError('Vincula una factura vigente del mismo proveedor.',409)
    prior=db.execute('SELECT id FROM pc_invoice_links WHERE order_id=? AND invoice_id=? AND voided_at IS NULL',(order['id'],invoice_id)).fetchone()
    if prior:return prior[0]
    key=db.execute('INSERT INTO pc_invoice_links(order_id,invoice_id,actor,created_at,reason) VALUES(?,?,?,?,?)',(order['id'],invoice_id,actor,now(),reason)).lastrowid
    audit(db,'order',order['id'],'invoice_linked',actor,reason,after={'link_id':key,'invoice_id':invoice_id})
    return key


def validate_invoice_edit(db,invoice_id,supplier_id):
    if db.execute('''SELECT 1 FROM pc_invoice_links l JOIN pc_orders o ON o.id=l.order_id
        WHERE l.invoice_id=? AND l.voided_at IS NULL AND o.supplier_id<>?''',(invoice_id,supplier_id)).fetchone():
        raise DomainError('La factura está vinculada a un pedido de otro proveedor. Revisa y retira el vínculo antes de cambiar su proveedor.',409)


def stock_receipt(db,order,receipt_id,accepted,actor,received,reason):
    if not accepted:return None
    key=db.execute("INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at,receipt_id) VALUES('receipt',?,?,?,?,?)",(received,reason,actor,now(),receipt_id)).lastrowid
    for line,receipt_line,amount in accepted:
        balance=db.execute('SELECT COALESCE(SUM(quantity_micros),0) FROM pc_stock_lines WHERE item_id=? AND warehouse_id=?',(line['item_id'],order['warehouse_id'])).fetchone()[0]
        if balance+base_quantity(db,amount,line)>MAX_QUANTITY:raise DomainError('La entrada supera el rango de stock permitido.',409)
        db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros,receipt_line_id) VALUES(?,?,?,?,?,?)',
                   (key,line['item_id'],order['warehouse_id'],line['base_unit_id'],base_quantity(db,amount,line),receipt_line))
    audit(db,'order',order['id'],'stock_received',actor,reason,after={'receipt_id':receipt_id,'stock_document_id':key})
    return key


def mutate(db,action,data,actor,target):
    if action.startswith('agenda_'):
        from erp import purchase_agenda
        return purchase_agenda.mutate(db,action,data,actor,target)
    if action=='document_order':
        from erp import document_lines
        return document_lines.prepare_order(db,target,data,actor)
    if action=='warehouse_entry':
        from erp import receipt_warehouse
        return receipt_warehouse.enter(db,target,data,actor)
    if action in {'stock','stock_reverse','price','price_void','policy'}:
        from erp import supply_planning
        return supply_planning.mutate(db,action,data,actor,target)
    if action=='directory':
        from erp import supplier_directory
        return supplier_directory.save(db,target,data,actor)
    if action=='unit':
        code=ap.text(data.get('code'),'Código de unidad',True,20).lower()
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}',code):raise DomainError('Código de unidad: usa letras, números, guion o guion bajo.')
        fields=dict(code=code,name=ap.text(data.get('name'),'Nombre de unidad',True,80),dimension=ap.choice(data.get('dimension'),{'mass','volume','count','package','other'},'Magnitud'),precision=ap.integer(data.get('precision'),'Decimales',0,6),actor=actor,created_at=now())
        table='pc_units'
    elif action=='warehouse':
        name=ap.text(data.get('name'),'Nombre de bodega',True,100)
        fields=dict(name=name,name_key=normalized(name),actor=actor,created_at=now());table='pc_warehouses'
    elif action=='item':
        unit=ap.record(db,'pc_units',ap.integer(data.get('base_unit_id'),'Unidad base'))
        code=ap.text(data.get('code'),'Código de insumo',True,60).upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,59}',code):raise DomainError('Código de insumo: usa letras, números y guiones.')
        fields=dict(code=code,name=ap.text(data.get('name'),'Nombre del insumo',True,160),base_unit_id=unit['id'],actor=actor,created_at=now());table='pc_items'
    elif action=='equivalence':
        item=ap.record(db,'pc_items',ap.integer(data.get('item_id'),'Insumo'));ap.version(data,item)
        unit=ap.record(db,'pc_units',ap.integer(data.get('unit_id'),'Unidad de compra'))
        if unit['id']==item['base_unit_id']:raise DomainError('La unidad base ya tiene equivalencia 1; no se redefine.')
        ratio=quantity(data.get('base_quantity'),'Cantidad de unidad base por una unidad de compra')
        reason=ap.text(data.get('reason'),'Antecedente de la equivalencia',True)
        fields=dict(item_id=item['id'],unit_id=unit['id'],base_ratio_micros=ratio,reason=reason,actor=actor,created_at=now());table='pc_equivalences'
        db.execute('UPDATE pc_items SET version=version+1 WHERE id=?',(item['id'],))
    else:table=None
    if table:
        key=db.execute('INSERT INTO '+table+'('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
        result=ap.record(db,table,key);audit(db,action,key,'created',actor,fields.get('reason',''),after=result);return result

    if action=='order':
        if data.get('confirmed') is not True:raise DomainError('Confirma la revisión del pedido local.')
        supplier=ap.record(db,'ap_suppliers',ap.integer(data.get('supplier_id'),'Proveedor'))
        ap.version({'version':data.get('supplier_version')},supplier)
        warehouse=ap.record(db,'pc_warehouses',ap.integer(data.get('warehouse_id'),'Bodega'))
        ordered=ap.day(data.get('ordered_on'),'Fecha del pedido');expected=ap.day(data.get('expected_on',''),'Entrega esperada',False)
        if ordered>ap.local_day() or (expected and expected<ordered):raise DomainError('Revisa las fechas del pedido y entrega.')
        reference=ap.text(data.get('reference',''),'Referencia externa',maximum=100)
        notes=ap.text(data.get('notes',''),'Notas',maximum=1000)
        rows=data.get('lines')
        if not isinstance(rows,list) or not 1<=len(rows)<=100:raise DomainError('El pedido necesita entre 1 y 100 líneas.')
        clean=[];seen=set()
        for row in rows:
            if not isinstance(row,dict):raise DomainError('Línea de pedido inválida.')
            item=ap.record(db,'pc_items',ap.integer(row.get('item_id'),'Insumo'));ap.version(row,item)
            if item['id'] in seen:raise DomainError('Agrupa cada insumo en una sola línea.')
            seen.add(item['id']);unit=ap.record(db,'pc_units',ap.integer(row.get('unit_id'),'Unidad'))
            ratio=SCALE
            if unit['id']!=item['base_unit_id']:
                equivalent=db.execute('SELECT base_ratio_micros FROM pc_equivalences WHERE item_id=? AND unit_id=?',(item['id'],unit['id'])).fetchone()
                if not equivalent:raise DomainError('Falta una equivalencia explícita para ese insumo y unidad.')
                ratio=equivalent[0]
            amount=in_unit(db,quantity(row.get('quantity')),unit['id'])
            line=dict(item_id=item['id'],unit_id=unit['id'],item_name=item['name'],unit_code=unit['code'],base_unit_id=item['base_unit_id'],base_ratio_micros=ratio,ordered_micros=amount)
            base_quantity(db,amount,line);clean.append(line)
        key=db.execute('INSERT INTO pc_orders(supplier_id,warehouse_id,ordered_on,expected_on,reference,reference_key,notes,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (supplier['id'],warehouse['id'],ordered,expected,reference,reference_key(reference),notes,actor,now())).lastrowid
        for row in clean:
            db.execute('INSERT INTO pc_order_lines(order_id,'+','.join(row)+') VALUES('+','.join('?' for _ in range(len(row)+1))+')',(key,*row.values()))
        result=order_view(db,key);audit(db,'order',key,'order_created',actor,notes,after=result);return result

    if action in {'receipt','cancel','invoice_link','issue'}:
        order=order_view(db,target);ap.version(data,order)
    if action=='cancel':
        if order['cancelled_at'] or order['state']=='complete':raise DomainError('El pedido ya está cerrado; revisa sus recepciones.',409)
        reason=ap.text(data.get('reason'),'Motivo de cancelación',True)
        for line in order['lines']:db.execute('UPDATE pc_order_lines SET cancelled_micros=ordered_micros-? WHERE id=?',(line['accepted_micros'],line['id']))
        db.execute('UPDATE pc_orders SET cancelled_at=?,cancelled_by=?,cancel_reason=? WHERE id=?',(now(),actor,reason,target));touch_order(db,target)
        result=order_view(db,target);audit(db,'order',target,'order_cancelled',actor,reason,order,result);return result
    if action=='invoice_link':
        reason=ap.text(data.get('reason'),'Antecedente del vínculo',True)
        link=link_invoice(db,order,data.get('invoice_id'),data.get('invoice_version'),actor,reason);touch_order(db,target)
        return {'id':link,'order_id':target}
    if action=='invoice_unlink':
        link=ap.record(db,'pc_invoice_links',target);order=order_view(db,link['order_id']);ap.version(data,order)
        if link['voided_at']:raise DomainError('El vínculo ya fue retirado.',409)
        reason=ap.text(data.get('reason'),'Motivo para retirar vínculo',True)
        db.execute('UPDATE pc_invoice_links SET voided_at=?,voided_by=?,void_reason=? WHERE id=?',(now(),actor,reason,target));touch_order(db,order['id'])
        audit(db,'order',order['id'],'invoice_unlinked',actor,reason,link,{'link_id':target});return {'id':target,'order_id':order['id']}
    if action=='issue':
        detail=ap.text(data.get('detail'),'Discrepancia',True,1000)
        kind=ap.choice(data.get('kind'),{'quantity','quality','document','other'},'Tipo de discrepancia')
        key=db.execute('INSERT INTO pc_issues(order_id,kind,detail,actor,created_at) VALUES(?,?,?,?,?)',(target,kind,detail,actor,now())).lastrowid
        touch_order(db,target);audit(db,'order',target,'issue_created',actor,detail,after={'issue_id':key,'kind':kind});return {'id':key,'order_id':target}
    if action=='resolve_issue':
        issue=ap.record(db,'pc_issues',target);ap.version(data,issue)
        if issue['resolved_at']:raise DomainError('La discrepancia ya está resuelta.',409)
        reason=ap.text(data.get('reason'),'Resultado de revisión',True)
        db.execute('UPDATE pc_issues SET resolved_at=?,resolved_by=?,resolution=?,version=version+1 WHERE id=?',(now(),actor,reason,target));touch_order(db,issue['order_id'])
        audit(db,'order',issue['order_id'],'issue_resolved',actor,reason,issue,{'issue_id':target});return {'id':target,'order_id':issue['order_id']}
    if action=='receipt':
        if data.get('confirmed') is not True:raise DomainError('Confirma la revisión física de la recepción.')
        if order['state'] in {'cancelled','complete'}:raise DomainError('El pedido está cerrado; no admite más recepciones.',409)
        received=ap.day(data.get('received_on'),'Fecha de recepción')
        if not order['ordered_on']<=received<=ap.local_day():raise DomainError('La recepción debe estar entre la fecha del pedido y hoy.')
        reference=ap.text(data.get('reference'),'Referencia única de esta entrega',True,100);refkey=reference_key(reference)
        if not refkey:raise DomainError('Completa una referencia verificable de esta entrega.')
        if db.execute('SELECT 1 FROM pc_receipts r JOIN pc_orders o ON o.id=r.order_id WHERE o.supplier_id=? AND r.reference_key=? AND r.voided_at IS NULL',(order['supplier_id'],refkey)).fetchone():
            raise DomainError('Esta entrega ya está registrada para el proveedor. Revisa su recepción antes de duplicarla.',409)
        notes=ap.text(data.get('notes',''),'Notas',maximum=1000)
        rows=data.get('lines');available={r['id']:r for r in order['lines']};seen=set();clean=[]
        if not isinstance(rows,list) or not 1<=len(rows)<=100:raise DomainError('Selecciona entre 1 y 100 líneas recibidas.')
        for row in rows:
            if not isinstance(row,dict):raise DomainError('Línea de recepción inválida.')
            key=ap.integer(row.get('order_line_id'),'Línea de pedido')
            if key in seen or key not in available:raise DomainError('Línea repetida o ajena a este pedido.')
            seen.add(key);line=available[key]
            if row.get('unit_id')!=line['unit_id'] or type(row.get('unit_id')) is not int:raise DomainError('Recibe en la unidad acordada en el pedido.')
            accepted=in_unit(db,quantity(row.get('accepted'),'Aceptado',True),line['unit_id'])
            rejected=in_unit(db,quantity(row.get('rejected'),'Rechazado',True),line['unit_id'])
            if not accepted+rejected or accepted+rejected>line['pending_micros']:raise DomainError('La recepción supera lo pendiente o no contiene cantidades.',409)
            base_quantity(db,accepted,line)
            reason=ap.text(row.get('reason',''),'Motivo del rechazo',bool(rejected))
            clean.append((line,accepted,rejected,reason))
        previous=db.execute('SELECT id FROM pc_receipts WHERE order_id=? AND reference_key=? AND voided_at IS NOT NULL ORDER BY id DESC LIMIT 1',(target,refkey)).fetchone()
        receipt=db.execute('INSERT INTO pc_receipts(order_id,received_on,reference,reference_key,notes,actor,created_at,replaces_receipt_id) VALUES(?,?,?,?,?,?,?,?)',
                           (target,received,reference,refkey,notes,actor,now(),previous[0] if previous else None)).lastrowid
        accepted_lines=[]
        for line,accepted,rejected,reason in clean:
            key=db.execute('INSERT INTO pc_receipt_lines(receipt_id,order_line_id,accepted_micros,rejected_micros,reason) VALUES(?,?,?,?,?)',(receipt,line['id'],accepted,rejected,reason)).lastrowid
            if accepted:accepted_lines.append((line,key,accepted))
            if rejected:
                detail=line['item_name']+': '+decimal(rejected)+' '+line['unit_code']+' rechazados. '+reason
                issue=db.execute("INSERT INTO pc_issues(order_id,receipt_id,kind,detail,actor,created_at) VALUES(?,?,'quantity',?,?,?)",(target,receipt,detail,actor,now())).lastrowid
                audit(db,'order',target,'issue_created',actor,reason,after={'issue_id':issue,'receipt_id':receipt,'detail':detail})
        document=None  # Receipt is informational; warehouse entry is explicitly confirmed later.
        if data.get('invoice_id') is not None:link_invoice(db,order,data['invoice_id'],data.get('invoice_version'),actor,'Referencia en recepción '+reference)
        touch_order(db,target);result={'id':receipt,'order_id':target,'stock_document_id':document}
        audit(db,'order',target,'receipt_confirmed',actor,notes,order,order_view(db,target));return result
    if action=='reverse_receipt':
        receipt=ap.record(db,'pc_receipts',target);ap.version(data,receipt)
        order=order_view(db,receipt['order_id']);ap.version({'version':data.get('order_version')},order)
        if receipt['voided_at']:raise DomainError('La recepción ya está revertida.',409)
        reason=ap.text(data.get('reason'),'Motivo del reverso',True)
        from erp import receipt_warehouse
        reversals=receipt_warehouse.reverse_all(db,target,actor,reason)
        reverse=reversals[0] if reversals else None
        if order['cancelled_at']:
            for row in db.execute('SELECT * FROM pc_receipt_lines WHERE receipt_id=?',(target,)):
                db.execute('UPDATE pc_order_lines SET cancelled_micros=cancelled_micros+? WHERE id=?',(row['accepted_micros'],row['order_line_id']))
        db.execute('UPDATE pc_receipts SET voided_at=?,voided_by=?,void_reason=?,version=version+1 WHERE id=?',(now(),actor,reason,target))
        for issue in db.execute('SELECT * FROM pc_issues WHERE receipt_id=? AND resolved_at IS NULL',(target,)).fetchall():
            resolution='Recepción revertida: '+reason
            db.execute('UPDATE pc_issues SET resolved_at=?,resolved_by=?,resolution=?,version=version+1 WHERE id=?',(now(),actor,resolution,issue['id']))
            audit(db,'order',order['id'],'issue_resolved',actor,resolution,dict(issue),{'issue_id':issue['id']})
        touch_order(db,order['id']);result={'id':target,'order_id':order['id'],'stock_reversal_id':reverse,'stock_reversal_ids':reversals}
        audit(db,'order',order['id'],'receipt_reversed',actor,reason,order,order_view(db,order['id']));return result
    raise DomainError('Operación de abastecimiento no encontrada.',404)


def overview(store):
    from erp import supplier_directory
    with store.connect() as db:
        db.execute('BEGIN')
        result={name:[dict(r) for r in db.execute('SELECT * FROM pc_'+name+' ORDER BY id')] for name in ('units','warehouses','items','equivalences')}
        result['suppliers']=supplier_directory.list_profiles(db)
        result['orders']=[order_view(db,r[0]) for r in db.execute('SELECT id FROM pc_orders ORDER BY id DESC')]
        result['invoices']=[dict(r) for r in db.execute('SELECT id,supplier_id,folio,amount,version,voided_at FROM ap_invoices ORDER BY id DESC')]
        result['movements']=[dict(r) for r in db.execute('''SELECT l.*,d.kind,d.occurred_on,d.reason,d.actor,d.receipt_id,d.reversal_of,
            i.name AS item_name,u.code AS unit_code,w.name AS warehouse_name FROM pc_stock_lines l
            JOIN pc_stock_documents d ON d.id=l.document_id JOIN pc_items i ON i.id=l.item_id
            JOIN pc_units u ON u.id=l.base_unit_id JOIN pc_warehouses w ON w.id=l.warehouse_id ORDER BY l.id DESC''')]
        for row in result['movements']:
            row['quantity']=decimal(row['quantity_micros'])
            entry=db.execute('SELECT receipt_id FROM pc_receipt_entries WHERE stock_document_id=?',(row['document_id'],)).fetchone()
            if entry:row.update(kind='warehouse_entry',receipt_id=entry[0])
        result['balances']=[dict(r) for r in db.execute('''SELECT l.item_id,l.warehouse_id,i.name AS item_name,u.code AS unit_code,w.name AS warehouse_name,SUM(l.quantity_micros) AS net_micros
            FROM pc_stock_lines l JOIN pc_items i ON i.id=l.item_id JOIN pc_units u ON u.id=l.base_unit_id
            JOIN pc_warehouses w ON w.id=l.warehouse_id GROUP BY l.item_id,l.warehouse_id ORDER BY i.name,w.name''')]
        for row in result['balances']:row.update(net=decimal(row['net_micros']),physical_quantity=None,opening_verified=False)
        from erp import supply_planning
        result.update(supply_planning.overview(db,result))
        from erp import purchase_agenda
        result.update(purchase_agenda.overview(db,result['suppliers']))
        result.update(today=ap.local_day(),currency='CLP',ledger_scope='ingredients_local',opening_verified=False)
        return result


def handle(handler,store,path,query,actor):
    prefix='/api/suppliers/procurement';tail=path[len(prefix):]
    if handler.command=='GET':
        pdf_match=re.fullmatch(r'/(agenda|orders)/(\d+)/pdf',tail)
        if pdf_match:
            from erp import purchase_pdf
            with store.connect() as db:
                db.execute('BEGIN');content,name=purchase_pdf.export(db,pdf_match[1],int(pdf_match[2]))
            return handler.send(200,content,'application/pdf',{'Content-Disposition':'attachment; filename="'+name+'"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
        if tail in ('','/'):return handler.send(200,overview(store))
        match=re.fullmatch(r'/orders/(\d+)(/history)?',tail)
        if match:
            with store.connect() as db:
                db.execute('BEGIN');key=int(match[1]);result=order_view(db,key)
                if match[2]:result={'events':[dict(r) for r in db.execute("SELECT * FROM pc_events WHERE entity='order' AND entity_id=? ORDER BY id DESC",(key,))]}
                return handler.send(200,result)
        match=re.fullmatch(r'/directory/(\d+)/history',tail)
        if match:
            with store.connect() as db:return handler.send(200,{'events':[dict(r) for r in db.execute("SELECT * FROM pc_events WHERE entity='supplier' AND entity_id=? ORDER BY id DESC",(int(match[1]),))]})
    if handler.command=='POST':
        data=handler.body()
        if tail=='/directory-preview':
            from erp import supplier_directory
            with store.connect() as db:
                db.execute('BEGIN');return handler.send(200,supplier_directory.preview(db,data))
        if tail=='/agenda':return handler.send(201,execute(store,'agenda_save',data,actor))
        agenda_match=re.fullmatch(r'/agenda/(\d+)/(save|order|done|postpone)',tail)
        if agenda_match:return handler.send(201,execute(store,'agenda_'+agenda_match[2],data,actor,int(agenda_match[1])))
        doc_match=re.fullmatch(r'/documents/(\d+)/order',tail)
        if doc_match:return handler.send(201,execute(store,'document_order',data,actor,int(doc_match[1])))
        action={'/units':'unit','/warehouses':'warehouse','/items':'item','/equivalences':'equivalence','/orders':'order','/stock':'stock','/prices':'price','/policies':'policy'}.get(tail);target=None
        if not action:
            match=re.fullmatch(r'/(orders|receipts|invoice-links|issues|directory|stock|prices)/(\d+)(?:/(receipts|cancel|invoices|issues|reverse|void|resolve|warehouse-entries))?',tail)
            if match:
                kind,key,suffix=match.groups();target=int(key)
                action={('orders','receipts'):'receipt',('orders','cancel'):'cancel',('orders','invoices'):'invoice_link',('orders','issues'):'issue',('receipts','reverse'):'reverse_receipt',('receipts','warehouse-entries'):'warehouse_entry',('invoice-links','void'):'invoice_unlink',('issues','resolve'):'resolve_issue',('directory',None):'directory',('stock','reverse'):'stock_reverse',('prices','void'):'price_void'}.get((kind,suffix))
        if action:return handler.send(201,execute(store,action,data,actor,target))
    raise DomainError('Ruta de abastecimiento no encontrada.',404)
