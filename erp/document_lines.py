"""Reviewed commercial lines independent from physical receipt and payment."""
from decimal import Decimal, ROUND_HALF_UP
import json
from erp import payables as ap, procurement as pc, supply_planning as sp
from erp.domain import DomainError
from erp.store import now


def decimal_money(value,label):
    import re
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,12}(?:\.\d{1,6})?',value):raise DomainError(label+': usa un decimal positivo sin separador de miles.')
    return Decimal(value)


def validate(db, payload, invoice=None):
    if not isinstance(payload,dict):raise DomainError('Revisión inválida.')
    rows=payload.get('lines',[])
    if not isinstance(rows,list) or len(rows)>100:raise DomainError('Revisa hasta 100 líneas por factura.')
    basis=ap.choice(payload.get('basis','net'),{'net','gross'},'Base tributaria')
    clean=[]
    for row in rows:
        if not isinstance(row,dict):raise DomainError('Línea inválida.')
        qty=pc.quantity(row.get('quantity'),'Cantidad facturada')
        price=decimal_money(row.get('unit_price'),'Precio unitario')
        discount=decimal_money(row.get('discount','0'),'Descuento de línea')
        amount=ap.integer(row.get('amount_clp'),'Importe de línea')
        expected=int((Decimal(qty)/pc.SCALE*price-discount).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        if expected!=amount:raise DomainError('Cantidad × precio − descuento no coincide con el importe de línea. Revisa el original.')
        line=dict(code=ap.text(row.get('code',''),'Código proveedor',maximum=100),description=ap.text(row.get('description'),'Descripción',True,500),format=ap.text(row.get('format',''),'Formato / envase',maximum=200),quantity=pc.decimal(qty),unit_price=str(price),discount=str(discount),amount_clp=amount,quantity_micros=qty,item_id=None,unit_id=None,base_unit_id=None,base_quantity_micros=None)
        if row.get('item_id') is not None:
            item=ap.record(db,'pc_items',ap.integer(row['item_id'],'Insumo'));unit=ap.record(db,'pc_units',ap.integer(row.get('unit_id'),'Unidad'))
            ap.version({'version':row.get('item_version')},item)
            ratio=pc.SCALE
            if unit['id']!=item['base_unit_id']:
                equivalent=db.execute('SELECT base_ratio_micros FROM pc_equivalences WHERE item_id=? AND unit_id=?',(item['id'],unit['id'])).fetchone()
                if not equivalent:raise DomainError('Configura la equivalencia explícita del formato antes de comparar precios.')
                ratio=equivalent[0]
            pc.in_unit(db,qty,unit['id'])
            line.update(item_id=item['id'],unit_id=unit['id'],base_unit_id=item['base_unit_id'],base_quantity_micros=pc.base_quantity(db,qty,dict(base_ratio_micros=ratio,base_unit_id=item['base_unit_id'])))
        elif row.get('unit_id') is not None:raise DomainError('Selecciona el insumo para asociar una unidad.')
        clean.append(line)
    totals={k:ap.integer(payload.get(k,0),k,0) for k in ('net','tax','exempt','other_taxes','discount_total')}
    if rows:
        if payload.get('lines_confirmed') is not True:raise DomainError('Confirma la revisión de productos, cantidades, formatos e importes.')
        subtotal=sum(r['amount_clp'] for r in clean)
        if basis=='net':
            if subtotal!=totals['net']+totals['exempt']:raise DomainError('La suma de líneas debe coincidir con neto más exento.')
            total=subtotal+totals['tax']+totals['other_taxes']-totals['discount_total']
        else:total=subtotal-totals['discount_total']
        if invoice and total!=invoice['amount']:raise DomainError('Las líneas e impuestos no cuadran con el total de la factura.')
    return clean,basis,totals


def register(db,draft,invoice,payload,actor,reason):
    rows,basis,totals=validate(db,payload,invoice)
    if not rows:return
    if db.execute('SELECT 1 FROM pc_document_lines WHERE invoice_id=?',(invoice['id'],)).fetchone() or db.execute('SELECT 1 FROM pc_invoice_lines WHERE invoice_id=? AND voided_at IS NULL',(invoice['id'],)).fetchone():raise DomainError('La factura ya tiene líneas revisadas. Adjunta el archivo sin volver a ingresarlas.',409)
    for ordinal,line in enumerate(rows,1):
        fields=dict(draft_id=draft['id'],invoice_id=invoice['id'],ordinal=ordinal,detail_json=json.dumps(line,ensure_ascii=False),**{k:line[k] for k in ('item_id','unit_id','base_unit_id','base_quantity_micros','quantity_micros','amount_clp')},basis=basis,invoice_signature=sp.signature(invoice),actor=actor,reason=reason,created_at=now())
        db.execute('INSERT INTO pc_document_lines('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values()))


def list_lines(db,draft_id):
    return [dict(dict(r),detail=json.loads(r['detail_json'])) for r in db.execute('SELECT d.*,l.order_id FROM pc_document_lines d LEFT JOIN pc_order_lines l ON l.id=d.order_line_id WHERE draft_id=? ORDER BY ordinal',(draft_id,))]


def prepare_order(db,target,data,actor):
    draft=ap.record(db,'ap_document_drafts',target);ap.version(data,draft)
    if not draft['invoice_id']:raise DomainError('Registra primero la factura revisada.')
    invoice=ap.record(db,'ap_invoices',draft['invoice_id']);ap.version({'version':data.get('invoice_version')},invoice)
    if invoice['voided_at']:raise DomainError('La factura está anulada.',409)
    lines=list_lines(db,target)
    if not lines or any(not r['item_id'] or r['voided_at'] for r in lines):raise DomainError('Cada línea debe tener insumo y unidad revisados para preparar el pedido.')
    if any(r['order_line_id'] or r['invoice_signature']!=sp.signature(invoice) for r in lines):raise DomainError('Las líneas ya tienen pedido o cambió la factura. Revisa sus vínculos.',409)
    if db.execute('SELECT 1 FROM pc_invoice_links WHERE invoice_id=? AND voided_at IS NULL',(invoice['id'],)).fetchone():raise DomainError('Esta factura ya está vinculada a un pedido; revisa ese pedido para evitar duplicarlo.',409)
    supplier=ap.record(db,'ap_suppliers',invoice['supplier_id']);ap.version({'version':data.get('supplier_version')},supplier)
    grouped={};versions=data.get('item_versions',{})
    if not isinstance(versions,dict):raise DomainError('Actualiza el catálogo.')
    for row in lines:
        item=ap.record(db,'pc_items',row['item_id']);ap.version({'version':versions.get(str(item['id']))},item)
        existing=grouped.get(item['id'])
        if existing and existing['unit_id']!=row['unit_id']:raise DomainError('Agrupa las líneas del mismo insumo en una unidad de compra revisada antes de preparar la OC.')
        if existing:existing['quantity']=pc.decimal(pc.quantity(existing['quantity'])+row['quantity_micros'])
        else:grouped[item['id']]=dict(item_id=item['id'],version=item['version'],unit_id=row['unit_id'],quantity=pc.decimal(row['quantity_micros']))
    reason=ap.text(data.get('reason'),'Antecedente del pedido',True,1000)
    order=pc.mutate(db,'order',dict(supplier_id=supplier['id'],supplier_version=supplier['version'],warehouse_id=data.get('warehouse_id'),ordered_on=data.get('ordered_on'),expected_on=data.get('expected_on',''),reference='',notes=reason,confirmed=data.get('confirmed'),lines=list(grouped.values())),actor,None)
    pc.link_invoice(db,order,invoice['id'],invoice['version'],actor,reason)
    for row in lines:
        ordered=next(l for l in order['lines'] if l['item_id']==row['item_id'])
        db.execute('UPDATE pc_document_lines SET order_line_id=? WHERE id=?',(ordered['id'],row['id']))
    db.execute('UPDATE ap_document_drafts SET version=version+1 WHERE id=?',(target,))
    pc.audit(db,'order',order['id'],'document_lines_linked',actor,reason,after={'draft_id':target,'invoice_id':invoice['id']})
    return {'id':target,'order_id':order['id']}


def prices(db):
    rows=[]
    for record in db.execute('SELECT p.*,i.name AS item_name,u.code AS unit_code FROM pc_document_lines p JOIN pc_items i ON i.id=p.item_id JOIN pc_units u ON u.id=p.unit_id ORDER BY p.id DESC'):
        row=dict(record);invoice=ap.record(db,'ap_invoices',row['invoice_id']);supplier=ap.record(db,'ap_suppliers',invoice['supplier_id'])
        valid=not row['voided_at'] and not invoice['voided_at'] and row['invoice_signature']==sp.signature(invoice)
        line=ap.record(db,'pc_order_lines',row['order_line_id']) if row['order_line_id'] else None
        order=pc.order_view(db,line['order_id']) if line else None
        if order:valid=valid and any(i['invoice_id']==invoice['id'] and not i['voided_at'] for i in order['invoices'])
        row.update(source='document',price_key='document:'+str(row['id']),valid=bool(valid),supplier_id=supplier['id'],supplier_name=supplier['name'],folio=invoice['folio'],issued_on=invoice['issued_on'],quantity=pc.decimal(row['quantity_micros']),base_quantity=pc.decimal(row['base_quantity_micros']),unit_price=str((Decimal(row['amount_clp'])*pc.SCALE/row['base_quantity_micros']).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)),order_id=order['id'] if order else None)
        rows.append(row)
    return rows


def map_lines(db,target,data,actor):
    draft=ap.record(db,'ap_document_drafts',target);ap.version(data,draft)
    if not draft['invoice_id']:raise DomainError('Asocia los insumos en la revisión antes de registrar.')
    invoice=ap.record(db,'ap_invoices',draft['invoice_id']);ap.version({'version':data.get('invoice_version')},invoice)
    if invoice['voided_at']:raise DomainError('La factura está anulada.',409)
    rows=list_lines(db,target);mappings=data.get('mappings')
    if not isinstance(mappings,list) or len(mappings)!=len(rows) or len({r.get('line_id') for r in mappings if isinstance(r,dict)})!=len(rows):raise DomainError('Revisa la asociación de todas las líneas una sola vez.')
    if data.get('confirmed') is not True:raise DomainError('Confirma las equivalencias revisadas.')
    reason=ap.text(data.get('reason'),'Antecedente de asociación',True,1000)
    for row in rows:
        if row['order_line_id'] or row['invoice_signature']!=sp.signature(invoice):raise DomainError('La línea ya tiene pedido o cambió la factura; revisa el vínculo antes de modificarla.',409)
        mapping=next((r for r in mappings if r['line_id']==row['id']),None)
        if not mapping:raise DomainError('Línea ajena a este documento.')
        detail=dict(row['detail'],**{k:mapping.get(k) for k in ('item_id','unit_id','item_version')})
        clean,_,_=validate(db,dict(lines=[detail],basis='gross',lines_confirmed=True))
        updated=clean[0]
        db.execute('UPDATE pc_document_lines SET item_id=?,unit_id=?,base_unit_id=?,base_quantity_micros=?,detail_json=? WHERE id=?',(updated['item_id'],updated['unit_id'],updated['base_unit_id'],updated['base_quantity_micros'],json.dumps(updated,ensure_ascii=False),row['id']))
        pc.audit(db,'document_line',row['id'],'catalog_mapped',actor,reason,row['detail'],updated)
    db.execute('UPDATE ap_document_drafts SET version=version+1 WHERE id=?',(target,))
    return {'id':target}
