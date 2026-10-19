"""Explicit warehouse entries for receipts; legacy stock is recognized without rewriting it."""
from erp import payables as ap, procurement as pc
from erp.domain import DomainError
from erp.store import now


def documents(db,receipt_id):
    return [dict(r) for r in db.execute('''SELECT d.*,w.id AS entry_id,
        (SELECT id FROM pc_stock_documents WHERE reversal_of=d.id) AS reversal_id
        FROM pc_stock_documents d LEFT JOIN pc_receipt_entries w ON w.stock_document_id=d.id
        WHERE d.receipt_id=? OR w.receipt_id=? ORDER BY d.id''',(receipt_id,receipt_id))]


def enrich(db,receipt):
    docs=documents(db,receipt['id']);receipt['warehouse_entries']=docs
    any_entered=False;any_pending=False
    for line in receipt['lines']:
        order_line=ap.record(db,'pc_order_lines',line['order_line_id'])
        legacy=db.execute('''SELECT COALESCE(SUM(l.quantity_micros),0) FROM pc_stock_lines l
            JOIN pc_stock_documents d ON d.id=l.document_id WHERE l.receipt_line_id=?
            AND NOT EXISTS(SELECT 1 FROM pc_stock_documents WHERE reversal_of=d.id)''',(line['id'],)).fetchone()[0]
        numerator=legacy*pc.SCALE
        if numerator%order_line['base_ratio_micros']:raise DomainError('Revisa la equivalencia histórica de esta recepción.',409)
        entered=numerator//order_line['base_ratio_micros']+db.execute('''SELECT COALESCE(SUM(l.quantity_micros),0)
            FROM pc_receipt_entry_lines l JOIN pc_receipt_entries e ON e.id=l.entry_id
            WHERE l.receipt_line_id=? AND NOT EXISTS(SELECT 1 FROM pc_stock_documents WHERE reversal_of=e.stock_document_id)''',(line['id'],)).fetchone()[0]
        if entered>line['accepted_micros']:raise DomainError('Los ingresos históricos superan lo aceptado; revisa la recepción.',409)
        pending=0 if receipt['voided_at'] else line['accepted_micros']-entered
        line.update(entered_micros=entered,warehouse_pending_micros=pending,entered=pc.decimal(entered),warehouse_pending=pc.decimal(pending),unit_id=order_line['unit_id'],unit_code=order_line['unit_code'],item_name=order_line['item_name'])
        any_entered|=entered>0;any_pending|=pending>0
    receipt['warehouse_state']='voided' if receipt['voided_at'] else 'partial' if any_entered and any_pending else 'entered' if any_entered else 'pending' if any_pending else 'not_applicable'


def enter(db,receipt_id,data,actor):
    receipt=ap.record(db,'pc_receipts',receipt_id);ap.version(data,receipt)
    if receipt['voided_at']:raise DomainError('La recepción está revertida.',409)
    if data.get('confirmed') is not True:raise DomainError('Confirma las cantidades y la bodega antes de ingresar existencias.')
    order=pc.order_view(db,receipt['order_id'])
    ap.version({'version':data.get('order_version')},order)
    receipt=next(r for r in order['receipts'] if r['id']==receipt_id)
    destination=ap.record(db,'pc_warehouses',ap.integer(data.get('warehouse_id'),'Bodega destino'))
    occurred=ap.day(data.get('occurred_on'),'Fecha de ingreso')
    if not receipt['received_on']<=occurred<=ap.local_day():raise DomainError('El ingreso debe estar entre la recepción y hoy.')
    reason=ap.text(data.get('reason'),'Antecedente del ingreso',True,1000)
    rows=data.get('lines')
    if not isinstance(rows,list) or not 1<=len(rows)<=100:raise DomainError('Selecciona entre 1 y 100 líneas pendientes de ingreso.')
    available={l['id']:l for l in receipt['lines']};seen=set();clean=[]
    from erp.supply_planning import position
    for row in rows:
        if not isinstance(row,dict):raise DomainError('Línea de ingreso inválida.')
        key=ap.integer(row.get('receipt_line_id'),'Línea recibida')
        if key in seen or key not in available:raise DomainError('Línea repetida o ajena a esta recepción.')
        seen.add(key);line=available[key];original=ap.record(db,'pc_order_lines',line['order_line_id'])
        if type(row.get('unit_id')) is not int or row['unit_id']!=original['unit_id']:raise DomainError('Ingresa en la unidad acordada; se conserva la equivalencia original.')
        amount=pc.in_unit(db,pc.quantity(row.get('quantity'),'Cantidad a ingresar'),original['unit_id'])
        if amount>line['warehouse_pending_micros']:raise DomainError('La cantidad supera lo aceptado pendiente de ingreso.',409)
        base=pc.base_quantity(db,amount,original);current=position(db,original['item_id'],destination['id'])
        if current['count'] and receipt['received_on']<current['count']['occurred_on']:
            raise DomainError('La recepción es anterior al conteo de esta bodega. Revisa una corrección actual para no duplicar existencias.',409)
        if current['net_micros']+base>pc.MAX_QUANTITY:raise DomainError('La entrada supera el rango de stock permitido.',409)
        clean.append((line,original,amount,base))
    # Keep the legacy schema unchanged: an explicit entry is a linked adjustment document.
    doc=db.execute("INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at) VALUES('adjustment',?,?,?,?)",(occurred,reason,actor,now())).lastrowid
    entry=db.execute('INSERT INTO pc_receipt_entries(receipt_id,stock_document_id) VALUES(?,?)',(receipt_id,doc)).lastrowid
    for line,original,amount,base in clean:
        stock=db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros) VALUES(?,?,?,?,?)',(doc,original['item_id'],destination['id'],original['base_unit_id'],base)).lastrowid
        db.execute('INSERT INTO pc_receipt_entry_lines(entry_id,receipt_line_id,stock_line_id,quantity_micros) VALUES(?,?,?,?)',(entry,line['id'],stock,amount))
    db.execute('UPDATE pc_receipts SET version=version+1 WHERE id=?',(receipt_id,));pc.touch_order(db,order['id'])
    result={'id':entry,'receipt_id':receipt_id,'order_id':order['id'],'stock_document_id':doc,'warehouse_id':destination['id']}
    pc.audit(db,'order',order['id'],'warehouse_entered',actor,reason,receipt,result)
    return result


def reverse_all(db,receipt_id,actor,reason):
    reversals=[]
    for doc in documents(db,receipt_id):
        if doc['reversal_id']:continue
        lines=[dict(r) for r in db.execute('SELECT * FROM pc_stock_lines WHERE document_id=?',(doc['id'],))]
        for line in lines:
            if db.execute('SELECT 1 FROM pc_counts WHERE item_id=? AND warehouse_id=? AND document_id>?',(line['item_id'],line['warehouse_id'],doc['id'])).fetchone():
                raise DomainError('Hay un conteo posterior; registra una corrección actual documentada.',409)
            balance=db.execute('SELECT COALESCE(SUM(quantity_micros),0) FROM pc_stock_lines WHERE item_id=? AND warehouse_id=?',(line['item_id'],line['warehouse_id'])).fetchone()[0]
            if balance<line['quantity_micros']:raise DomainError('Movimientos posteriores impiden revertir los ingresos; revisa el libro de movimientos.',409)
        reverse=db.execute("INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at,reversal_of) VALUES('reversal',?,?,?,?,?)",(ap.local_day(),reason,actor,now(),doc['id'])).lastrowid
        for line in lines:
            db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros,reverses_line_id) VALUES(?,?,?,?,?,?)',(reverse,line['item_id'],line['warehouse_id'],line['base_unit_id'],-line['quantity_micros'],line['id']))
        reversals.append(reverse)
    return reversals
