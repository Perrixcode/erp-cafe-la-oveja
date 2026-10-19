"""Deterministic weekly proposals; confirmation changes scheduling only."""
from datetime import date, timedelta
import hashlib
import json
import re

from erp import payables as ap
from erp.domain import DomainError
from erp.store import now

REASONS = {
    'hold':'Pago en disputa o pausa', 'review':'Saldo anterior sin validar',
    'missing_issue':'Falta fecha de emisión', 'missing_due':'Falta vencimiento',
    'missing_folio':'Falta folio verificable', 'missing_condition':'Falta condición de pago',
    'missing_tax_id':'Falta RUT del proveedor', 'missing_bank':'Ficha de transferencia incompleta',
    'missing_document':'Falta factura original PDF o foto', 'credit':'Aplica primero los créditos disponibles',
    'later':'Vence después de la semana', 'scheduled':'Ya tiene programación',
}


def parameters(data):
    ap.choice(data.get('currency','CLP'),{'CLP'},'Moneda')
    budget = ap.integer(data.get('budget'), 'Presupuesto semanal CLP', 0)
    raw = ap.day(data.get('week_start'), 'Semana')
    partial = data.get('allow_partial', True)
    if type(partial) is not bool:
        raise DomainError('Indica si se permiten pagos parciales.')
    try:
        start = date.fromisoformat(raw)
        start -= timedelta(days=start.weekday())
        end = start + timedelta(days=6)
    except (ValueError, OverflowError):
        raise DomainError('Semana fuera del rango permitido.') from None
    if end.isoformat() < ap.local_day():
        raise DomainError('Elige la semana actual o una posterior.')
    return dict(week_start=start.isoformat(), week_end=end.isoformat(), budget=budget, allow_partial=partial)


def active_hold(db, invoice_id):
    row = db.execute('SELECT * FROM ap_invoice_holds WHERE invoice_id=? AND released_at IS NULL', (invoice_id,)).fetchone()
    return dict(row) if row else None


def build(db, data):
    params = parameters(data)
    today = ap.local_day()
    suppliers = {r['id']:dict(r) for r in db.execute('SELECT * FROM ap_suppliers')}
    settled = {r[0]:r[1] for r in db.execute('SELECT invoice_id,SUM(amount) FROM ap_payments WHERE voided_at IS NULL GROUP BY invoice_id')}
    for row in db.execute('''SELECT a.invoice_id,SUM(a.amount) FROM ap_account_applications a
        JOIN ap_account_entries e ON e.id=a.entry_id WHERE a.voided_at IS NULL AND e.voided_at IS NULL GROUP BY a.invoice_id'''):
        settled[row[0]] = settled.get(row[0],0)+row[1]
    credits = {r[0]:r[1] for r in db.execute('''SELECT e.supplier_id,SUM(e.amount-COALESCE(a.total,0)) FROM ap_account_entries e
        LEFT JOIN (SELECT entry_id,SUM(amount) total FROM ap_account_applications WHERE voided_at IS NULL GROUP BY entry_id) a ON a.entry_id=e.id
        WHERE e.voided_at IS NULL GROUP BY e.supplier_id''')}
    originals = {r[0] for r in db.execute('SELECT DISTINCT invoice_id FROM ap_files WHERE payment_id IS NULL')}
    holds = {r['invoice_id']:dict(r) for r in db.execute('SELECT * FROM ap_invoice_holds WHERE released_at IS NULL')}
    paid_amount=db.execute('SELECT COALESCE(SUM(amount),0) FROM ap_payments WHERE voided_at IS NULL AND paid_on BETWEEN ? AND ?', (params['week_start'],params['week_end'])).fetchone()[0]
    paid_amount+=db.execute("SELECT COALESCE(SUM(amount),0) FROM ap_account_entries WHERE kind='payment' AND voided_at IS NULL AND occurred_on BETWEEN ? AND ?", (params['week_start'],params['week_end'])).fetchone()[0]
    candidates, excluded, existing = [], [], []
    reserved = 0
    for invoice in db.execute('SELECT * FROM ap_invoices ORDER BY id'):
        balance = invoice['amount'] - settled.get(invoice['id'],0)
        if invoice['voided_at'] or (balance <= 0 and not invoice['payment_review']):
            continue
        supplier = suppliers[invoice['supplier_id']]
        row = dict(id=invoice['id'], version=invoice['version'], supplier_id=supplier['id'],
                   supplier_version=supplier['version'], supplier_name=supplier['name'], tax_id=supplier['tax_id'],
                   folio=invoice['folio'], issued_on=invoice['issued_on'], due_on=invoice['due_on'],
                   balance=None if invoice['payment_review'] else balance,
                   available_credit=credits.get(supplier['id'],0), reasons=[])
        reasons = row['reasons']
        if invoice['id'] in holds:
            reasons.append('hold');row['hold_reason']=holds[invoice['id']]['reason'];row['hold_id']=holds[invoice['id']]['id']
        if invoice['payment_review']:reasons.append('review')
        if not invoice['issued_on']:reasons.append('missing_issue')
        if not invoice['due_on']:reasons.append('missing_due')
        if not invoice['folio'] or invoice['folio'] in ('0','X') or invoice['folio'].startswith('SALDO-FILA-'):reasons.append('missing_folio')
        if invoice['condition']=='unknown':reasons.append('missing_condition')
        if not supplier['tax_id']:reasons.append('missing_tax_id')
        if not all(supplier[k] for k in ('bank','account_number','account_type','account_holder','account_tax_id')):reasons.append('missing_bank')
        if invoice['id'] not in originals:reasons.append('missing_document')
        if row['available_credit']>0:reasons.append('credit')
        if invoice['due_on'] and invoice['due_on']>params['week_end']:reasons.append('later')
        if invoice['scheduled_on'] and invoice['planned_amount']:
            reasons.append('scheduled')
            in_week = params['week_start'] <= invoice['scheduled_on'] <= params['week_end']
            amount = invoice['planned_amount']
            reserved += amount if in_week else 0
            existing.append(dict(row, scheduled_on=invoice['scheduled_on'], planned_amount=amount, in_week=in_week))
        elif reasons:
            excluded.append(row)
        else:
            candidates.append(row)
    candidates.sort(key=lambda r:(r['due_on'],r['issued_on'],r['supplier_id'],r['id']))
    remaining=max(0,params['budget']-reserved-paid_amount)
    count=0
    for row in candidates:
        amount=min(row['balance'],remaining) if params['allow_partial'] or row['balance']<=remaining else 0
        if count>=100:amount=0
        row.update(proposed_amount=amount, priority='Vencida' if row['due_on']<today else 'Vence hasta el fin de la semana',
                   decision='Propuesta parcial' if 0<amount<row['balance'] else 'Propuesta completa' if amount else 'Pendiente por presupuesto o límite de 100 facturas')
        remaining-=amount
        count+=bool(amount)
    result=dict(params=params,today=today,currency='CLP',method='transfer',candidates=candidates,excluded=excluded,existing=existing,
                reserved_amount=reserved,paid_amount=paid_amount,available_budget=max(0,params['budget']-reserved-paid_amount),over_budget=max(0,reserved+paid_amount-params['budget']),
                proposed_total=sum(r['proposed_amount'] for r in candidates),remaining_budget=remaining,
                suggested_date=max(today,params['week_start']),reasons=REASONS)
    result['token']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return result


def preview(store, data):
    with store.connect() as db:
        db.execute('BEGIN')
        return build(db, data)


def mutate(db, action, data, actor, target):
    if action=='payment_hold':
        invoice=ap.record(db,'ap_invoices',target)
        ap.version(data,invoice)
        if invoice['voided_at']:
            raise DomainError('La factura está anulada.',409)
        hold=data.get('hold')
        if type(hold) is not bool:raise DomainError('Indica si se pausa o se resuelve la pausa.')
        reason=ap.text(data.get('reason'),'Motivo',True)
        current=active_hold(db,target)
        if hold:
            if current:raise DomainError('La factura ya tiene una pausa vigente.',409)
            if invoice['amount']<=ap.settled(db,target):raise DomainError('La factura ya está saldada.',409)
            key=db.execute('INSERT INTO ap_invoice_holds(invoice_id,reason,actor,created_at) VALUES(?,?,?,?)',(target,reason,actor,now())).lastrowid
            db.execute("UPDATE ap_invoices SET scheduled_on='',planned_amount=0 WHERE id=?",(target,))
            after=dict(hold_id=key,hold=True,reason=reason)
        else:
            if not current:raise DomainError('La factura no tiene una pausa vigente.',409)
            db.execute('UPDATE ap_invoice_holds SET released_at=?,released_by=?,release_reason=? WHERE id=?',(now(),actor,reason,current['id']))
            after=dict(hold_id=current['id'],hold=False,reason=reason)
        ap.touch(db,target)
        ap.event(db,'payment_paused' if hold else 'payment_pause_resolved',actor,reason,invoice,after,supplier=invoice['supplier_id'],invoice=target)
        return ap.record(db,'ap_invoices',target)

    if action!='proposal_save':raise DomainError('Operación de propuesta no encontrada.',404)
    if data.get('confirmed') is not True:raise DomainError('Confirma la revisión de la propuesta.')
    current=build(db,data)
    if data.get('token')!=current['token']:
        raise DomainError('Cambió un saldo, documento, ficha o programación. Genera una propuesta actualizada antes de guardar.',409)
    scheduled=ap.day(data.get('scheduled_on'),'Fecha de programación')
    if not max(current['today'],current['params']['week_start'])<=scheduled<=current['params']['week_end']:
        raise DomainError('Programa una fecha vigente dentro de la semana elegida.')
    items=data.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=100:raise DomainError('Selecciona entre 1 y 100 facturas.')
    eligible={row['id']:row for row in current['candidates']}
    selected=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise DomainError('Selección no válida.')
        key=ap.integer(item.get('id'),'Factura')
        if key in seen or key not in eligible:raise DomainError('La selección contiene una factura repetida o no elegible.',409)
        seen.add(key);row=eligible[key]
        ap.version(item,row)
        amount=ap.integer(item.get('amount'),'Monto propuesto CLP',1,row['balance'])
        selected.append(dict(row,amount=amount))
    total=sum(row['amount'] for row in selected)
    if total+current['reserved_amount']+current['paid_amount']>current['params']['budget']:
        raise DomainError('La propuesta, lo ya pagado y lo programado superan el presupuesto semanal.',409)
    notes=ap.text(data.get('notes',''),'Nota de la revisión',maximum=1000)
    key=db.execute('''INSERT INTO ap_payment_proposals(week_start,week_end,budget,reserved_amount,paid_amount,total,scheduled_on,actor,created_at,notes,source_json,selected_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(current['params']['week_start'],current['params']['week_end'],current['params']['budget'],current['reserved_amount'],current['paid_amount'],total,scheduled,actor,now(),notes,json.dumps(current,ensure_ascii=False),json.dumps(selected,ensure_ascii=False))).lastrowid
    for row in selected:
        before=ap.record(db,'ap_invoices',row['id'])
        db.execute('UPDATE ap_invoices SET scheduled_on=?,planned_amount=?,version=version+1,updated_at=? WHERE id=?',(scheduled,row['amount'],now(),row['id']))
        ap.event(db,'scheduled_from_proposal',actor,notes,before,dict(proposal_id=key,scheduled_on=scheduled,planned_amount=row['amount']),supplier=row['supplier_id'],invoice=row['id'])
    result=dict(id=key,total=total,count=len(selected),scheduled_on=scheduled,reserved_amount=current['reserved_amount'],paid_amount=current['paid_amount'],budget=current['params']['budget'])
    ap.event(db,'payment_proposal_saved',actor,notes,after=result)
    return result


def handle(handler, store, path, query, actor):
    if handler.command=='GET':
        with store.connect() as db:
            if path=='/api/suppliers/payment-proposals':
                return handler.send(200,{'proposals':[dict(r) for r in db.execute('SELECT id,week_start,week_end,budget,reserved_amount,paid_amount,total,scheduled_on,actor,created_at,notes FROM ap_payment_proposals ORDER BY id DESC LIMIT 20')]})
            match=re.fullmatch(r'/api/suppliers/payment-proposals/(\d+)',path)
            if match:
                row=ap.record(db,'ap_payment_proposals',int(match[1]))
                row['source']=json.loads(row.pop('source_json'));row['selected']=json.loads(row.pop('selected_json'))
                return handler.send(200,row)
    if handler.command=='POST':
        data=handler.body()
        if path=='/api/suppliers/payment-proposal':return handler.send(200,preview(store,data))
        if path=='/api/suppliers/payment-proposals':return handler.send(201,ap.execute(store,'proposal_save',data,actor))
        match=re.fullmatch(r'/api/suppliers/invoices/(\d+)/payment-hold',path)
        if match:return handler.send(200,ap.execute(store,'payment_hold',data,actor,int(match[1])))
    raise DomainError('Ruta de propuestas no encontrada.',404)
