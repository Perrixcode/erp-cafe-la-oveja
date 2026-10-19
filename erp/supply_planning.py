"""Exact ingredient stock, reviewed invoice prices and explainable local proposals."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from datetime import date,timedelta
from erp import payables as ap
from erp import procurement as pc
from erp.domain import DomainError
from erp.store import now


def position(db,item,warehouse):
    rows=[tuple(r) for r in db.execute('SELECT id,quantity_micros FROM pc_stock_lines WHERE item_id=? AND warehouse_id=? ORDER BY id',(item,warehouse))]
    count=db.execute('SELECT * FROM pc_counts WHERE item_id=? AND warehouse_id=? ORDER BY id DESC LIMIT 1',(item,warehouse)).fetchone()
    return dict(net_micros=sum(r[1] for r in rows),verified=bool(count),count=dict(count) if count else None,
                token=hashlib.sha256(json.dumps([rows,dict(count) if count else None],sort_keys=True).encode()).hexdigest())


def check_position(db,item,warehouse,token):
    current=position(db,item,warehouse)
    if token!=current['token']:raise DomainError('Los movimientos cambiaron. Actualiza y revisa nuevamente las cantidades.',409)
    return current


def receipt_date_guard(db,order,received):
    for line in order['lines']:
        current=position(db,line['item_id'],order['warehouse_id'])
        if current['count'] and received<current['count']['occurred_on']:
            raise DomainError('La recepción es anterior al último conteo verificado. Registra una corrección documentada a fecha actual para no duplicar existencias.',409)


def signature(invoice):
    return hashlib.sha256(json.dumps({k:invoice[k] for k in ('supplier_id','document_type','folio','issued_on','amount','voided_at')},sort_keys=True).encode()).hexdigest()


def mutate(db,action,data,actor,target):
    reason=ap.text(data.get('reason'),'Antecedente / motivo',True,1000)
    if action=='stock_reverse':
        doc=ap.record(db,'pc_stock_documents',target)
        if doc['kind'] in ('receipt','reversal') or db.execute('SELECT 1 FROM pc_receipt_entries WHERE stock_document_id=?',(target,)).fetchone() or db.execute('SELECT 1 FROM pc_counts WHERE document_id=?',(target,)).fetchone():
            raise DomainError('Revierte la recepción desde su pedido; corrige un conteo con un nuevo conteo.',409)
        if db.execute('SELECT 1 FROM pc_stock_documents WHERE reversal_of=?',(target,)).fetchone():raise DomainError('El movimiento ya está revertido.',409)
        lines=[dict(r) for r in db.execute('SELECT * FROM pc_stock_lines WHERE document_id=?',(target,))]
        for line in lines:
            current=position(db,line['item_id'],line['warehouse_id'])
            if current['count'] and current['count']['document_id']>target:raise DomainError('Hay un conteo posterior: registra una corrección actual documentada.',409)
            if current['net_micros']-line['quantity_micros']<0:raise DomainError('El reverso dejaría stock negativo.',409)
        key=db.execute("INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at,reversal_of) VALUES('reversal',?,?,?,?,?)",(ap.local_day(),reason,actor,now(),target)).lastrowid
        for line in lines:
            db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros,reverses_line_id) VALUES(?,?,?,?,?,?)',(key,line['item_id'],line['warehouse_id'],line['base_unit_id'],-line['quantity_micros'],line['id']))
        pc.audit(db,'stock',key,'stock_reversed',actor,reason,doc,{'id':key});return {'id':key}
    if action=='stock':
        item=ap.record(db,'pc_items',ap.integer(data.get('item_id'),'Insumo'))
        warehouse=ap.record(db,'pc_warehouses',ap.integer(data.get('warehouse_id'),'Bodega'))
        kind=ap.choice(data.get('kind'),{'count','issue','waste','adjustment','transfer'},'Movimiento')
        if data.get('confirmed') is not True:raise DomainError('Confirma la revisión física de cantidades y unidad base.')
        current=check_position(db,item['id'],warehouse['id'],data.get('stock_token'))
        amount=pc.in_unit(db,pc.quantity(data.get('quantity'),'Cantidad en unidad base',kind=='count'),item['base_unit_id'])
        delta=amount-current['net_micros'] if kind=='count' else -amount if kind in ('issue','waste','transfer') else amount*(1 if data.get('direction')=='in' else -1 if data.get('direction')=='out' else 0)
        if kind=='adjustment' and not delta:raise DomainError('Elige entrada o salida para el ajuste.')
        if kind!='count' and not current['verified']:raise DomainError('Verifica primero un conteo físico para este insumo y bodega.',409)
        if current['net_micros']+delta<0 or current['net_micros']+delta>pc.MAX_QUANTITY:raise DomainError('El movimiento dejaría una cantidad fuera de rango.',409)
        destination=None
        if kind=='transfer':
            destination=ap.record(db,'pc_warehouses',ap.integer(data.get('destination_id'),'Bodega destino'))
            if destination['id']==warehouse['id']:raise DomainError('Selecciona otra bodega destino.')
            other=check_position(db,item['id'],destination['id'],data.get('destination_token'))
            if not other['verified']:raise DomainError('Verifica el conteo del destino antes de traspasar.',409)
            if other['net_micros']+amount>pc.MAX_QUANTITY:raise DomainError('La cantidad destino excede el rango.')
        key=db.execute('INSERT INTO pc_stock_documents(kind,occurred_on,reason,actor,created_at) VALUES(?,?,?,?,?)',('adjustment' if kind=='count' else kind,ap.local_day(),reason,actor,now())).lastrowid
        if delta:db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros) VALUES(?,?,?,?,?)',(key,item['id'],warehouse['id'],item['base_unit_id'],delta))
        if destination:db.execute('INSERT INTO pc_stock_lines(document_id,item_id,warehouse_id,base_unit_id,quantity_micros) VALUES(?,?,?,?,?)',(key,item['id'],destination['id'],item['base_unit_id'],amount))
        if kind=='count':db.execute('INSERT INTO pc_counts(item_id,warehouse_id,document_id,counted_micros,occurred_on,actor,created_at,reason) VALUES(?,?,?,?,?,?,?,?)',(item['id'],warehouse['id'],key,amount,ap.local_day(),actor,now(),reason))
        pc.audit(db,'stock',key,'stock_'+kind,actor,reason,{'net_micros':current['net_micros']},{'id':key,'item_id':item['id'],'warehouse_id':warehouse['id'],'delta':delta,'destination_id':destination['id'] if destination else None})
        return {'id':key,'position':position(db,item['id'],warehouse['id'])}
    if action=='price_void':
        row=ap.record(db,'pc_invoice_lines',target)
        if row['voided_at']:raise DomainError('La línea ya está anulada.',409)
        db.execute('UPDATE pc_invoice_lines SET voided_at=?,voided_by=?,void_reason=? WHERE id=?',(now(),actor,reason,target))
        pc.audit(db,'price',target,'price_voided',actor,reason,row,{'id':target});return {'id':target}
    if action=='price':
        invoice=ap.record(db,'ap_invoices',ap.integer(data.get('invoice_id'),'Factura'));ap.version({'version':data.get('invoice_version')},invoice)
        line=ap.record(db,'pc_order_lines',ap.integer(data.get('order_line_id'),'Línea de pedido'));order=pc.order_view(db,line['order_id']);ap.version({'version':data.get('order_version')},order)
        if invoice['voided_at'] or invoice['supplier_id']!=order['supplier_id']:raise DomainError('La factura debe estar vigente y ser del proveedor del pedido.')
        if not db.execute('SELECT 1 FROM pc_invoice_links WHERE invoice_id=? AND order_id=? AND voided_at IS NULL',(invoice['id'],order['id'])).fetchone():raise DomainError('Vincula primero la factura al pedido.')
        amount=ap.integer(data.get('amount_clp'),'Importe de esta línea en CLP');basis=ap.choice(data.get('basis'),{'net','gross'},'Base tributaria')
        quantity=pc.in_unit(db,pc.quantity(data.get('quantity')),line['unit_id']);base=pc.base_quantity(db,quantity,line)
        if db.execute('SELECT 1 FROM pc_invoice_lines WHERE invoice_id=? AND basis<>? AND voided_at IS NULL',(invoice['id'],basis)).fetchone():raise DomainError('Mantén la misma base tributaria en las líneas de una factura.')
        if db.execute('SELECT 1 FROM pc_document_lines WHERE invoice_id=?',(invoice['id'],)).fetchone():raise DomainError('La factura ya tiene líneas revisadas desde su documento; revisa esas líneas para evitar duplicarlas.',409)
        other=db.execute('SELECT COALESCE(SUM(amount_clp),0) FROM pc_invoice_lines WHERE invoice_id=? AND voided_at IS NULL',(invoice['id'],)).fetchone()[0]
        if other+amount>invoice['amount']:raise DomainError('Las líneas superan el total de la factura; revisa importes y base tributaria.',409)
        if data.get('confirmed') is not True:raise DomainError('Confirma cantidad, importe de línea y base tributaria.')
        key=db.execute('INSERT INTO pc_invoice_lines(invoice_id,order_line_id,quantity_micros,base_quantity_micros,amount_clp,basis,invoice_signature,reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(invoice['id'],line['id'],quantity,base,amount,basis,signature(invoice),reason,actor,now())).lastrowid
        pc.audit(db,'order',order['id'],'invoice_line_reviewed',actor,reason,after={'price_id':key,'invoice_id':invoice['id'],'order_line_id':line['id']});return {'id':key}
    if action=='policy':
        item=ap.record(db,'pc_items',ap.integer(data.get('item_id'),'Insumo'));warehouse=ap.record(db,'pc_warehouses',ap.integer(data.get('warehouse_id'),'Bodega'))
        supplier=ap.record(db,'ap_suppliers',ap.integer(data.get('supplier_id'),'Proveedor'));unit=ap.record(db,'pc_units',ap.integer(data.get('unit_id'),'Unidad de compra'))
        if unit['id']!=item['base_unit_id'] and not db.execute('SELECT 1 FROM pc_equivalences WHERE item_id=? AND unit_id=?',(item['id'],unit['id'])).fetchone():raise DomainError('Configura la equivalencia de compra antes de la política.')
        values={k:pc.in_unit(db,pc.quantity(data.get(k),k,True),item['base_unit_id']) for k in ('minimum','target','daily')}
        if values['target']<values['minimum']:raise DomainError('El objetivo no puede ser menor al mínimo.')
        lead=ap.integer(data.get('lead_days'),'Plazo explícito en días',0,3650)
        old=db.execute('SELECT * FROM pc_reorder_policies WHERE item_id=? AND warehouse_id=?',(item['id'],warehouse['id'])).fetchone()
        if ap.integer(data.get('version'),'Versión de política',0)!=(old['version'] if old else 0):raise DomainError('La política cambió; actualiza antes de guardar.',409)
        fields=dict(item_id=item['id'],warehouse_id=warehouse['id'],supplier_id=supplier['id'],unit_id=unit['id'],minimum_micros=values['minimum'],target_micros=values['target'],daily_micros=values['daily'],lead_days=lead,reason=reason,actor=actor,updated_at=now())
        if old:
            key=old['id'];db.execute('UPDATE pc_reorder_policies SET '+','.join(k+'=?' for k in fields)+',version=version+1 WHERE id=?',(*fields.values(),key))
        else:key=db.execute('INSERT INTO pc_reorder_policies('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
        pc.audit(db,'policy',key,'policy_saved',actor,reason,dict(old) if old else None,fields);return {'id':key}
    raise DomainError('Operación de planificación no encontrada.',404)


def overview(db,base):
    positions=[]
    for item in base['items']:
        for warehouse in base['warehouses']:
            p=position(db,item['id'],warehouse['id']);positions.append(dict(p,item_id=item['id'],warehouse_id=warehouse['id'],item_name=item['name'],warehouse_name=warehouse['name'],unit_id=item['base_unit_id'],quantity=pc.decimal(p['net_micros'])))
    prices=[]
    for record in db.execute('SELECT p.*,l.order_id,l.item_id,l.unit_code,l.base_unit_id,l.ordered_micros,l.base_ratio_micros FROM pc_invoice_lines p JOIN pc_order_lines l ON l.id=p.order_line_id ORDER BY p.id DESC'):
        row=dict(record);invoice=ap.record(db,'ap_invoices',row['invoice_id']);order=next(r for r in base['orders'] if r['id']==row['order_id']);line=next(r for r in order['lines'] if r['id']==row['order_line_id'])
        linked=any(i['invoice_id']==invoice['id'] and not i['voided_at'] for i in order['invoices'])
        valid=not row['voided_at'] and linked and signature(invoice)==row['invoice_signature'] and not invoice['voided_at']
        row.update(valid=bool(valid),supplier_id=invoice['supplier_id'],supplier_name=order['supplier_name'],folio=invoice['folio'],issued_on=invoice['issued_on'],item_name=line['item_name'],quantity=pc.decimal(row['quantity_micros']),base_quantity=pc.decimal(row['base_quantity_micros']),unit_price=str((Decimal(row['amount_clp'])*pc.SCALE/row['base_quantity_micros']).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)),received=pc.decimal(line['accepted_micros']),ordered=pc.decimal(line['ordered_micros']))
        prices.append(row)
    from erp import document_lines
    prices.extend(document_lines.prices(db))
    groups={}
    for row in sorted((r for r in prices if r['valid']),key=lambda r:(r['issued_on'],r.get('source','manual'),r['id']),reverse=True):
        group=groups.setdefault((row['item_id'],row['basis']),{})
        group.setdefault(row['supplier_id'],row)
    comparisons=[dict(item_id=key[0],basis=key[1],item_name=next(iter(rows.values()))['item_name'],base_unit_id=next(iter(rows.values()))['base_unit_id'],rows=sorted(rows.values(),key=lambda r:Fraction(r['amount_clp'],r['base_quantity_micros']))) for key,rows in groups.items()]
    matching=[]
    for order in base['orders']:
        for line in order['lines']:
            reviewed=[r for r in prices if r['order_line_id']==line['id'] and r['valid']]
            invoiced=sum(r['quantity_micros'] for r in reviewed)
            matching.append(dict(order_id=order['id'],order_line_id=line['id'],item_name=line['item_name'],unit_code=line['unit_code'],ordered=line['ordered'],accepted=line['accepted'],invoiced=pc.decimal(invoiced),difference=pc.decimal(invoiced-line['accepted_micros']),status='unreviewed' if not reviewed else 'mismatch' if invoiced!=line['accepted_micros'] else 'matched'))
    policies=[dict(r) for r in db.execute('SELECT * FROM pc_reorder_policies ORDER BY id')];suggestions=[]
    for policy in policies:
        p=next(r for r in positions if r['item_id']==policy['item_id'] and r['warehouse_id']==policy['warehouse_id']);item=next(r for r in base['items'] if r['id']==policy['item_id'])
        due_orders=[o for o in base['orders'] if o['warehouse_id']==policy['warehouse_id'] and o['state'] in ('open','partial') and any(l['item_id']==item['id'] and l['pending_micros'] for l in o['lines'])]
        incoming=sum(pc.base_quantity(db,l['pending_micros'],l) for o in due_orders for l in o['lines'] if l['item_id']==item['id'])
        projected=p['net_micros']+incoming-policy['daily_micros']*policy['lead_days']
        needed=max(0,policy['target_micros']-projected) if projected<policy['minimum_micros'] else 0
        ratio=pc.SCALE if policy['unit_id']==item['base_unit_id'] else db.execute('SELECT base_ratio_micros FROM pc_equivalences WHERE item_id=? AND unit_id=?',(item['id'],policy['unit_id'])).fetchone()[0]
        unit=ap.record(db,'pc_units',policy['unit_id']);step=10**(6-unit['precision']);purchase=((needed*pc.SCALE+ratio*step-1)//(ratio*step))*step
        blocked=[]
        if not p['verified']:blocked.append('Falta conteo físico verificado.')
        if any(l['warehouse_pending_micros'] and next(x for x in o['lines'] if x['id']==l['order_line_id'])['item_id']==item['id'] for o in base['orders'] for r in o['receipts'] if not r['voided_at'] for l in r['lines']):blocked.append('Hay cantidades recibidas sin ingreso a bodega; revisa su destino antes de comprar.')
        horizon=(date.fromisoformat(ap.local_day())+timedelta(days=policy['lead_days'])).isoformat()
        if any(o['expected_on'] and o['expected_on']>horizon for o in due_orders):blocked.append('Hay entregas posteriores al plazo de reposición; revisa el abastecimiento intermedio.')
        if any(not o['expected_on'] or o['overdue'] for o in due_orders):blocked.append('Hay entregas pendientes atrasadas o sin fecha; revisa su llegada antes de decidir.')
        supplier=next(s for s in base['suppliers'] if s['id']==policy['supplier_id']);profile=supplier['profile']
        if profile['minimum_amount_clp'] is not None:blocked.append('Verifica el mínimo monetario del proveedor con una cotización vigente.')
        if profile['minimum_quantity'] is not None:
            if profile['minimum_unit_id']!=unit['id']:blocked.append('El mínimo del proveedor usa otra unidad; revisión necesaria.')
            elif purchase:purchase=max(purchase,pc.quantity(profile['minimum_quantity']))
        if purchase>pc.MAX_QUANTITY:blocked.append('La cantidad calculada excede el rango.')
        suggestions.append(dict(policy,**{k:p[k] for k in ('item_name','warehouse_name')},supplier_name=supplier['name'],unit_code=unit['code'],stock=p['quantity'] if p['verified'] else None,incoming=pc.decimal(incoming),projected=pc.decimal(projected) if p['verified'] else None,quantity=pc.decimal(purchase) if not blocked else None,blocked=blocked,status='blocked' if blocked else 'suggested' if purchase else 'covered',explanation='Proyección = stock verificado + pedidos pendientes − consumo diario revisado × plazo. Si cae bajo el mínimo, reponer hasta el objetivo; redondear hacia arriba a la unidad de compra.'))
    today=ap.local_day();soon=(date.fromisoformat(today)+timedelta(days=7)).isoformat()
    invoices=[dict(r) for r in db.execute('SELECT * FROM ap_invoices WHERE voided_at IS NULL')]
    for r in invoices:r['balance']=r['amount']-ap.settled(db,r['id'])
    pending=[r for r in invoices if r['balance']>0]
    priorities=dict(overdue=sum(r['balance'] for r in pending if not r['payment_review'] and r['due_on'] and r['due_on']<today),due_soon=sum(r['balance'] for r in pending if not r['payment_review'] and today<=r['due_on']<=soon),review=sum(bool(r['payment_review']) for r in pending),missing_due=sum(not r['due_on'] for r in pending),pending_orders=sum(o['state'] in ('open','partial') for o in base['orders']),late_orders=sum(o['overdue'] for o in base['orders']),open_issues=sum(o['open_issues'] for o in base['orders']),missing_invoice=sum(o['missing_invoice'] and o['state']!='cancelled' for o in base['orders']),line_pending=sum(r['status']!='matched' for r in matching),unverified=sum(not p['verified'] for p in positions),suggestions=sum(r['status']=='suggested' for r in suggestions),documents=db.execute('SELECT COUNT(*) FROM ap_document_drafts WHERE invoice_id IS NULL AND discarded_at IS NULL').fetchone()[0],holds=db.execute('SELECT COUNT(*) FROM ap_invoice_holds WHERE released_at IS NULL').fetchone()[0],directory_pending=sum(not s['profile']['reviewed'] for s in base['suppliers']))
    priorities['warehouse_pending']=sum(r['warehouse_state'] in ('pending','partial') for o in base['orders'] for r in o['receipts'])
    return dict(comparisons=comparisons,counts=[dict(r) for r in db.execute("SELECT c.*,i.name AS item_name,w.name AS warehouse_name FROM pc_counts c JOIN pc_items i ON i.id=c.item_id JOIN pc_warehouses w ON w.id=c.warehouse_id ORDER BY c.id DESC")],priorities=priorities,positions=positions,prices=prices,matching=matching,policies=policies,suggestions=suggestions,unconfigured=[p for p in positions if not any(r['item_id']==p['item_id'] and r['warehouse_id']==p['warehouse_id'] for r in policies)])
