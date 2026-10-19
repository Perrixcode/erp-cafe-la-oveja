"""Facturas y pagos manuales de proveedores; sin ejecución bancaria ni mensajería."""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, unquote
from zoneinfo import ZoneInfo

from erp.domain import DomainError
from erp.store import now

LIMIT = 8 * 1024 * 1024
MAX_AMOUNT = 999_999_999_999
TYPES = {'invoice': 'Factura', 'exempt': 'Factura exenta', 'receipt': 'Boleta', 'other': 'Otro documento'}
METHODS = {'transfer': 'Transferencia', 'cash': 'Efectivo', 'card': 'Tarjeta', 'other': 'Otro'}
CONDITIONS = {'credit': 'Crédito', 'cash': 'Contado / contra entrega', 'unknown': 'Sin definir'}
SUPPLIER_FIELDS = ('name', 'tax_id', 'contact', 'phone', 'email', 'bank', 'account_type',
                   'account_number', 'account_holder', 'account_tax_id', 'terms', 'notes')


def local_day():
    return datetime.now(ZoneInfo('America/Santiago')).date().isoformat()


def text(value, label, required=False, maximum=500):
    if not isinstance(value, str):
        raise DomainError(f'{label}: se espera texto.')
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise DomainError(f'{label}: completa el campo sin saltos de línea (máximo {maximum} caracteres).')
    return value


def day(value, label, required=True):
    if not value and not required and isinstance(value, str):
        return ''
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise DomainError(f'{label}: fecha no válida.') from None
    return value


def integer(value, label, minimum=1, maximum=MAX_AMOUNT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f'{label}: usa un número entero entre {minimum} y {maximum}.')
    return value


def choice(value, options, label):
    if not isinstance(value, str) or value not in options:
        raise DomainError(f'{label}: selecciona una opción válida.')
    return value


def tax_id(value):
    value = text(value, 'RUT', maximum=20).upper().replace('.', '').replace('-', '').replace(' ', '')
    if value and not re.fullmatch(r'\d{7,8}[0-9K]', value):
        raise DomainError('RUT: usa el número con dígito verificador, o déjalo vacío.')
    return value


def record(db, table, key):
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
    if not row:
        raise DomainError('Registro no encontrado.', 404)
    return dict(row)


def version(data, current):
    integer(data.get('version'), 'Versión')
    if data['version'] != current['version']:
        raise DomainError('El registro cambió. Actualiza la vista antes de guardar.', 409)


def event(db, action, actor, reason='', before=None, after=None, supplier=None, invoice=None):
    db.execute('INSERT INTO ap_events(supplier_id,invoice_id,action,actor,reason,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (supplier, invoice, action, actor, reason, json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False), now()))


def paid(db, invoice):
    from erp import supplier_account
    legacy = db.execute('SELECT COALESCE(SUM(amount),0) FROM ap_payments WHERE invoice_id=? AND voided_at IS NULL', (invoice,)).fetchone()[0]
    return legacy + supplier_account.allocated(db, invoice, 'payment')


def credited(db, invoice):
    from erp import supplier_account
    return supplier_account.allocated(db, invoice, 'credit_note')



def settled(db, invoice):
    return paid(db, invoice) + credited(db, invoice)


def touch(db, invoice, payment_delta=0):
    current = record(db, 'ap_invoices', invoice)
    balance = current['amount'] - settled(db, invoice)
    planned = max(0, min(current['planned_amount'] - payment_delta, balance))
    db.execute('UPDATE ap_invoices SET version=version+1,updated_at=?,planned_amount=?,scheduled_on=? WHERE id=?',
               (now(), planned, current['scheduled_on'] if planned else '', invoice))


def execute(store, action, data, actor, target=None):
    """One lock, one audit and one idempotency result for every mutation."""
    request_id = text(data.get('request_id'), 'Identificador de operación', True, 100)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', request_id):
        raise DomainError('Identificador de operación no válido.')
    signature = hashlib.sha256(json.dumps([action, target, actor, data], sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM ap_requests WHERE request_id=?', (request_id,)).fetchone()
        if old:
            if old['signature'] != signature:
                raise DomainError('Este identificador ya se usó para otra operación.', 409)
            return json.loads(old['response_json'])
        try:
            result = mutate(db, action, data, actor, target)
        except sqlite3.IntegrityError:
            raise DomainError('Ya existe ese RUT o documento del mismo proveedor y tipo. Revisa los registros antes de duplicarlo.', 409) from None
        db.execute('INSERT INTO ap_requests VALUES(?,?,?)', (request_id, signature, json.dumps(result)))
        return result


def invoice_supplier(db, data, actor, target):
    """Resolve or create the supplier inside the invoice's transaction."""
    if 'new_supplier' not in data:
        supplier = integer(data.get('supplier_id'), 'Proveedor')
        record(db, 'ap_suppliers', supplier)
        return supplier
    if target or data.get('supplier_id') not in (None, ''):
        raise DomainError('Selecciona un proveedor existente o ingresa uno nuevo para esta factura.')
    value = data['new_supplier']
    if not isinstance(value, dict):
        raise DomainError('Completa el nombre y, si lo tienes, el RUT del proveedor.')
    name = text(value.get('name', ''), 'Nombre del proveedor', True, 160)
    rut = tax_id(value.get('tax_id', ''))
    if rut:
        existing = db.execute('SELECT id FROM ap_suppliers WHERE tax_id=?', (rut,)).fetchone()
        if existing:
            return existing['id']
    from erp.payable_duplicates import normalized
    matches = [r for r in db.execute('SELECT id,name,tax_id FROM ap_suppliers')
               if normalized(r['name']) == normalized(name) and (not rut or not r['tax_id'])]
    if matches:
        raise DomainError('Ya hay un proveedor con este nombre y falta un RUT para compararlos. '
                          'Selecciónalo en Proveedor si corresponde a la misma empresa, o completa su RUT en la ficha antes de continuar.', 409)
    return mutate(db, 'supplier', {'name': name, 'tax_id': rut}, actor, None)['id']


def mutate(db, action, data, actor, target, *, check_duplicates=True):
    if action in ('document_confirm','document_discard','document_save','document_map','document_apply_reading'):
        from erp import document_intake
        return document_intake.mutate(db,action,data,actor,target)
    if action in ('proposal_save','payment_hold'):
        from erp import payment_proposals
        return payment_proposals.mutate(db,action,data,actor,target)
    if action.startswith('account_'):
        from erp import supplier_account
        return supplier_account.mutate(db, action, data, actor, target)
    if action == 'supplier':
        fields = {key: text(data.get(key, ''), key, key == 'name', 1000 if key == 'notes' else 160) for key in SUPPLIER_FIELDS}
        for key in ('tax_id', 'account_tax_id'):
            fields[key] = tax_id(fields[key])
        before = record(db, 'ap_suppliers', target) if target else {}
        if target:
            version(data, before)
            reason = text(data.get('reason'), 'Motivo de la corrección', True)
            db.execute('UPDATE ap_suppliers SET '+','.join(f'{key}=?' for key in fields)+',version=version+1,updated_at=? WHERE id=?', (*fields.values(), now(), target))
        else:
            reason = ''
            target = db.execute('INSERT INTO ap_suppliers('+','.join(fields)+',created_at,updated_at) VALUES('+','.join('?' for _ in range(len(fields)+2))+')', (*fields.values(), now(), now())).lastrowid
        result = record(db, 'ap_suppliers', target)
        event(db, 'supplier_saved', actor, reason, before, result, supplier=target)
        return result
    if action == 'invoice':
        supplier = invoice_supplier(db, data, actor, target)
        fields = dict(supplier_id=supplier, document_type=choice(data.get('document_type'), TYPES, 'Tipo de documento'),
                      folio=text(data.get('folio'), 'Folio', True, 80).upper(),
                      issued_on=day(data.get('issued_on', ''), 'Emisión', False), due_on=day(data.get('due_on', ''), 'Vencimiento', False),
                      amount=integer(data.get('amount'), 'Total con impuestos en CLP'),
                      description=text(data.get('description', ''), 'Descripción', maximum=1000),
                      condition=choice(data.get('condition'), CONDITIONS, 'Condición'))
        if fields['due_on'] and fields['due_on'] < fields['issued_on']:
            raise DomainError('El vencimiento no puede ser anterior a la emisión.')
        # Keep the printed identifier, including leading zeroes. DuplicateIndex
        # compares numeric identities so 00042 and 42 cannot create two invoices.
        supplier_record=record(db,'ap_suppliers',supplier)
        from erp.payable_duplicates import DuplicateIndex, HistoricalIdentityWarning
        candidate=dict(fields, tax_id=supplier_record['tax_id'], name=supplier_record['name'])
        matches = DuplicateIndex(db).find(candidate, exclude_id=target)
        history_confirmation = None
        # CSV exceptions never bypass the current identity check. This executes
        # under the same BEGIN IMMEDIATE lock as the eventual insert/update.
        current_match = next((m for m in matches if m['source']=='ERP' and m['exact']), None)
        blocking = next((m for m in matches if not m['historical_only']), None) if check_duplicates else current_match
        if blocking:
            qualifier = 'Ya existe' if blocking['exact'] else 'Posible duplicado'
            raise DomainError(f"{qualifier}: {blocking['supplier_name']}, folio {blocking['folio'] or 'sin folio'}, {blocking['state'].lower()} ({blocking['source']}). Revisa el registro existente antes de guardar la factura.", 409)
        if check_duplicates:
            if matches:
                warning = HistoricalIdentityWarning(candidate, matches, actor, target)
                confirmation = data.get('historical_confirmation')
                if not isinstance(confirmation,dict) or confirmation.get('confirmed') is not True or confirmation.get('token') != warning.historical_duplicate['token']:
                    raise warning
                confirmation_reason = text(confirmation.get('reason'), 'Motivo de la confirmación histórica', True)
                history_confirmation = (warning.proof, confirmation_reason, warning.historical_duplicate['token'])
        from erp.payable_duplicates import folio as identity_folio
        old_source=next((r for r in db.execute('SELECT invoice_id,source_folio FROM ap_invoice_sources WHERE source_tax_id=? AND source_type=? AND invoice_id<>?',
                              (supplier_record['tax_id'],fields['document_type'],target or 0)) if identity_folio(r['source_folio'])==identity_folio(fields['folio'])),None)
        if old_source:
            raise DomainError('Este documento ya fue importado y corregido. Revisa su factura existente.',409)
        before = record(db, 'ap_invoices', target) if target else {}
        if target:
            version(data, before)
            if before['voided_at']:
                raise DomainError('El documento está anulado.', 409)
            from erp import procurement
            procurement.validate_invoice_edit(db,target,supplier)
            from erp import supplier_account
            source_credit = supplier_account.origin_credit(db, target)
            if source_credit and (fields['amount'] < source_credit or supplier != before['supplier_id']):
                raise DomainError('La factura tiene notas de crédito vigentes: conserva su proveedor y un total que cubra esas notas.', 409)
            earliest_note = db.execute("SELECT MIN(occurred_on) FROM ap_account_entries WHERE origin_invoice_id=? AND kind='credit_note' AND voided_at IS NULL", (target,)).fetchone()[0]
            if earliest_note and fields['issued_on'] and earliest_note < fields['issued_on']:
                raise DomainError('La emisión de la factura no puede ser posterior a sus notas de crédito vigentes.', 409)
            total_paid = settled(db, target)
            if fields['amount'] < total_paid or (total_paid and supplier != before['supplier_id']):
                raise DomainError('La corrección no puede reducir el total bajo los pagos y créditos aplicados ni cambiar su proveedor.', 409)
            reason = text(data.get('reason'), 'Motivo de la corrección', True)
            db.execute('UPDATE ap_invoices SET '+','.join(f'{key}=?' for key in fields)+' WHERE id=?', (*fields.values(), target))
            touch(db, target)
        else:
            reason = ''
            target = db.execute('INSERT INTO ap_invoices('+','.join(fields)+',created_at,updated_at) VALUES('+','.join('?' for _ in range(len(fields)+2))+')', (*fields.values(), now(), now())).lastrowid
        result = record(db, 'ap_invoices', target)
        event(db, 'invoice_saved', actor, reason, before, result, supplier=supplier, invoice=target)
        if history_confirmation:
            proof, confirmation_reason, token = history_confirmation
            event(db, 'historical_identity_confirmed', actor, confirmation_reason, proof,
                  {'invoice_id':target,'folio':result['folio'],'review_token':token}, supplier=supplier, invoice=target)
        return result
    if action in ('payment', 'void_invoice', 'void_payment', 'reconcile'):
        payment = record(db, 'ap_payments', target) if action == 'void_payment' else None
        invoice_id = payment['invoice_id'] if payment else target
        invoice = record(db, 'ap_invoices', invoice_id)
        version(data, invoice)
        if invoice['voided_at']:
            raise DomainError('La factura está anulada.', 409)
        if action == 'reconcile':
            if not invoice['payment_review'] or data.get('confirmed') is not True:
                raise DomainError('Confirma la revisión del saldo importado.', 409)
            previous_paid = integer(data.get('previous_paid'), 'Total pagado anteriormente', 0, invoice['amount'])
            reason = text(data.get('reason'), 'Antecedente de la revisión', True)
            if previous_paid:
                db.execute('INSERT INTO ap_payments(invoice_id,amount,paid_on,method,reference,notes,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (invoice_id, previous_paid, '', 'other', 'Saldo anterior validado', reason, actor, now()))
            db.execute('UPDATE ap_invoices SET payment_review=0 WHERE id=?', (invoice_id,))
            event(db, 'balance_reviewed', actor, reason, invoice, {'previous_paid': previous_paid, 'reviewed_on': local_day()}, supplier=invoice['supplier_id'], invoice=invoice_id)
            touch(db, invoice_id)
            return record(db, 'ap_invoices', invoice_id)
        if action == 'payment':
            if invoice['payment_review']:
                raise DomainError('Primero valida el saldo de esta factura importada.', 409)
            amount = integer(data.get('amount'), 'Monto del pago')
            if amount > invoice['amount'] - settled(db, invoice_id):
                raise DomainError('El pago supera el saldo pendiente. Actualiza y revisa el monto.', 409)
            paid_on = day(data.get('paid_on'), 'Fecha del pago')
            if paid_on > local_day():
                raise DomainError('Un pago realizado no puede tener fecha futura. Usa Programación semanal de pagos.')
            if data.get('confirmed') is not True:
                raise DomainError('Confirma que el pago ya fue realizado.')
            fields = dict(invoice_id=invoice_id, amount=amount, paid_on=paid_on,
                          method=choice(data.get('method'), METHODS, 'Medio de pago'),
                          reference=text(data.get('reference', ''), 'Referencia', maximum=160),
                          notes=text(data.get('notes', ''), 'Notas', maximum=1000), actor=actor, created_at=now())
            key = db.execute('INSERT INTO ap_payments('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', tuple(fields.values())).lastrowid
            result = record(db, 'ap_payments', key)
            event(db, 'payment_recorded', actor, after=result, supplier=invoice['supplier_id'], invoice=invoice_id)
        else:
            reason = text(data.get('reason'), 'Motivo de anulación', True)
            if payment:
                if payment['voided_at']:
                    raise DomainError('El pago ya está anulado.', 409)
                db.execute('UPDATE ap_payments SET voided_at=?,void_reason=? WHERE id=?', (now(), reason, target))
                result = record(db, 'ap_payments', target)
            else:
                from erp import supplier_account
                if settled(db, invoice_id) or supplier_account.origin_credit(db, invoice_id):
                    raise DomainError('La factura tiene pagos o notas de crédito vigentes. Revísalos antes de anularla.', 409)
                db.execute("UPDATE ap_invoices SET voided_at=?,scheduled_on='',planned_amount=0 WHERE id=?", (now(), invoice_id))
                result = record(db, 'ap_invoices', invoice_id)
            event(db, action, actor, reason, payment or invoice, result, supplier=invoice['supplier_id'], invoice=invoice_id)
        touch(db, invoice_id, data['amount'] if action == 'payment' else 0)
        return result
    if action == 'schedule':
        scheduled = day(data.get('scheduled_on', ''), 'Fecha programada', False)
        items = selections(db, data)
        for invoice, item in items:
            amount = integer(item.get('amount'), 'Monto a programar') if scheduled else 0
            if amount > invoice['amount'] - settled(db, invoice['id']):
                raise DomainError('El monto a programar supera el saldo pendiente.', 409)
            db.execute('UPDATE ap_invoices SET scheduled_on=?,planned_amount=?,version=version+1,updated_at=? WHERE id=?', (scheduled, amount, now(), invoice['id']))
            event(db, 'scheduled', actor, before=invoice, after=dict(date=scheduled, amount=amount), supplier=invoice['supplier_id'], invoice=invoice['id'])
        return {'updated': len(items)}
    raise DomainError('Operación no encontrada.', 404)


def selections(db, data):
    items = data.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise DomainError('Selecciona entre 1 y 100 facturas.')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise DomainError('Selección no válida.')
        key = integer(item.get('id'), 'Factura')
        if key in seen:
            raise DomainError('Una factura está repetida en la selección.')
        seen.add(key)
        invoice = record(db, 'ap_invoices', key)
        version(item, invoice)
        from erp import payment_proposals
        if payment_proposals.active_hold(db,key):
            raise DomainError('Una factura está en disputa o pausa de pago. Resuelve la pausa antes de programarla o incluirla en un listado.',409)
        if invoice['payment_review'] or invoice['voided_at'] or invoice['amount'] <= settled(db, key):
            raise DomainError('Una factura seleccionada necesita validar su saldo, está saldada o anulada. Actualiza la vista.', 409)
        result.append((invoice, item))
    return result


def view(store):
    today = local_day()
    soon = (date.fromisoformat(today)+timedelta(days=7)).isoformat()
    with store.connect() as db:
        db.execute('BEGIN')
        suppliers = [dict(r) for r in db.execute('SELECT * FROM ap_suppliers ORDER BY name COLLATE NOCASE,id')]
        payments = [dict(r) for r in db.execute('SELECT * FROM ap_payments ORDER BY paid_on DESC,id DESC')]
        files = [dict(r) for r in db.execute('SELECT id,invoice_id,payment_id,name,mime,sha256,bytes,actor,created_at FROM ap_files ORDER BY id')]
        invoices = [dict(r) for r in db.execute('SELECT * FROM ap_invoices ORDER BY issued_on DESC,id DESC')]
        holds = {r['invoice_id']:dict(r) for r in db.execute('SELECT * FROM ap_invoice_holds WHERE released_at IS NULL')}
        from erp import supplier_account
        account_entries = supplier_account.entries(db)
        applications = [dict(a, kind=e['kind'], entry_voided_at=e['voided_at']) for e in account_entries for a in e['applications']]
        names = {s['id']: s['name'] for s in suppliers}
        for invoice in invoices:
            invoice['payment_hold'] = holds.get(invoice['id'])
            invoice['supplier_name'] = names[invoice['supplier_id']]
            invoice['payments'] = [p for p in payments if p['invoice_id'] == invoice['id']]
            invoice['files'] = [f for f in files if f['invoice_id'] == invoice['id']]
            invoice['applications'] = [a for a in applications if a['invoice_id'] == invoice['id']]
            applied = [a for a in invoice['applications'] if not a['voided_at'] and not a['entry_voided_at']]
            invoice['paid'] = sum(p['amount'] for p in invoice['payments'] if not p['voided_at']) + sum(a['amount'] for a in applied if a['kind'] == 'payment')
            invoice['credited'] = sum(a['amount'] for a in applied if a['kind'] == 'credit_note')
            invoice['balance'] = 0 if invoice['voided_at'] else invoice['amount'] - invoice['paid'] - invoice['credited']
            invoice['status'] = 'void' if invoice['voided_at'] else 'review' if invoice['payment_review'] else ('settled' if invoice['credited'] else 'paid') if not invoice['balance'] else 'partial' if invoice['paid'] or invoice['credited'] else 'pending'
            invoice['overdue'] = bool(not invoice['payment_review'] and invoice['balance'] and invoice['due_on'] and invoice['due_on'] < today)
            invoice['due_soon'] = bool(not invoice['payment_review'] and invoice['balance'] and invoice['due_on'] and today <= invoice['due_on'] <= soon)
        for invoice in invoices:
            src = db.execute('SELECT batch_id,source_row,source_tax_id,source_type,source_folio,source_amount,source_date FROM ap_invoice_sources WHERE invoice_id=?', (invoice['id'],)).fetchone()
            invoice['source'] = dict(src) if src else None
        from erp import sii_review
        imports = sii_review.listing(db)
        for supplier in suppliers:
            rows = [r for r in invoices if r['supplier_id'] == supplier['id']]
            supplier['available_credit'] = sum(e['available'] for e in account_entries if e['supplier_id'] == supplier['id'])
            supplier.update(balance=sum(r['balance'] for r in rows if not r['payment_review']), invoices=len(rows), overdue=sum(r['balance'] for r in rows if r['overdue']))
            supplier['net_balance'] = supplier['balance'] - supplier['available_credit']
    pending = [r for r in invoices if r['balance'] and not r['payment_review']]
    return dict(today=today, currency='CLP', suppliers=suppliers, invoices=invoices, types=TYPES, methods=METHODS, conditions=CONDITIONS,
                account_entries=account_entries, imports=imports, totals=dict(review=sum(r['payment_review'] and not r['voided_at'] for r in invoices), balance=sum(r['balance'] for r in pending), overdue=sum(r['balance'] for r in pending if r['overdue']),
                            due_soon=sum(r['balance'] for r in pending if r['due_soon']), scheduled=sum(r['planned_amount'] for r in pending),
                            missing_due=sum(not r['due_on'] for r in pending), missing_document=sum(not any(f['payment_id'] is None for f in r['files']) for r in invoices if not r['voided_at'])))


def history(store, invoice):
    with store.connect() as db:
        current = record(db, 'ap_invoices', invoice)
        return {'events': [dict(r) for r in db.execute('SELECT * FROM ap_events WHERE invoice_id=? OR (invoice_id IS NULL AND supplier_id=?) ORDER BY id DESC', (invoice, current['supplier_id']))]}


def money(amount):
    return '$'+format(amount, ',').replace(',', '.')


def due_label(due_on, today):
    if not due_on:return 'Sin vencimiento registrado'
    due=date.fromisoformat(due_on);days=(due-date.fromisoformat(today)).days
    relative='vence hoy' if days==0 else ('vence en ' if days>0 else 'vencida hace ')+str(abs(days))+(' día' if abs(days)==1 else ' días')
    return 'Vencimiento: '+due.strftime('%d/%m/%Y')+' · '+relative


def message(store, data):
    today=local_day()
    chosen_day = date.fromisoformat(day(data.get('week_start', today), 'Semana del listado'))
    week_start = chosen_day - timedelta(days=chosen_day.weekday())
    try:
        week_end = week_start + timedelta(days=6)
    except OverflowError:
        raise DomainError('Semana del listado: fecha fuera de rango.') from None
    week_label = week_start.strftime('%d/%m/%Y')+' al '+week_end.strftime('%d/%m/%Y')
    with store.connect() as db:
        db.execute('BEGIN')
        groups = {}
        for invoice, _ in selections(db, data):
            supplier = record(db, 'ap_suppliers', invoice['supplier_id'])
            balance = invoice['amount'] - settled(db, invoice['id'])
            invoice.update(balance=balance, requested=invoice['planned_amount'] or balance)
            groups.setdefault(supplier['id'], (supplier, []))[1].append(invoice)
        lines = ['PAGOS A PROVEEDORES · OVEJA', 'Preparado el '+today, '']
        total = 0
        messages = []
        for supplier, invoices in groups.values():
            subtotal = sum(i['requested'] for i in invoices)
            dates = {i['scheduled_on'] for i in invoices}
            scheduled = next(iter(dates)) if len(dates) == 1 else None
            payment_day = (date.fromisoformat(scheduled).strftime('%d/%m/%Y') if scheduled else 'Por definir') if len(dates) == 1 else 'Varias fechas; ver facturas'
            provider_lines = [f"*{supplier['name']}*", 'Semana del '+week_label,
                              'Día de pago: '+payment_day, '*Total a pagar: '+money(subtotal)+'*', '', 'Facturas incluidas:']
            lines.extend([supplier['name'], 'RUT: '+(supplier['tax_id'] or 'por completar')])
            for invoice in invoices:
                total += invoice['requested']
                due=due_label(invoice['due_on'],today)
                lines.append(f"• {TYPES[invoice['document_type']]} {invoice['folio']} · pagar {money(invoice['requested'])} · saldo {money(invoice['balance'])} · {due}")
                provider_lines.append(f"• {TYPES[invoice['document_type']]} {invoice['folio']} · pagar {money(invoice['requested'])}"+
                                      (f" · saldo {money(invoice['balance'])}" if invoice['requested'] != invoice['balance'] else '')+' · '+due)
                if invoice['scheduled_on']:
                    lines.append('  Programado: '+invoice['scheduled_on'])
                if len(dates) > 1 or (invoice['scheduled_on'] and not week_start.isoformat() <= invoice['scheduled_on'] <= week_end.isoformat()):
                    invoice_day = date.fromisoformat(invoice['scheduled_on']).strftime('%d/%m/%Y') if invoice['scheduled_on'] else 'por definir'
                    outside = bool(invoice['scheduled_on'] and not week_start.isoformat() <= invoice['scheduled_on'] <= week_end.isoformat())
                    provider_lines.append('  Día: '+invoice_day+(' (fuera de esta semana)' if outside else ''))
            lines.append('Subtotal a pagar: '+money(subtotal))
            bank_lines = ['Datos de transferencia:', 'RUT proveedor: '+(supplier['tax_id'] or 'por completar')]
            for label, key in [('Banco', 'bank'), ('Tipo de cuenta', 'account_type'), ('Cuenta', 'account_number'), ('Titular', 'account_holder'), ('RUT titular', 'account_tax_id')]:
                lines.append(label+': '+(supplier[key] or 'por completar'))
                bank_lines.append(label+': '+(supplier[key] or 'por completar'))
            if supplier['email']:
                lines.append('Correo: '+supplier['email'])
                bank_lines.append('Correo: '+supplier['email'])
            lines.append('')
            provider_text = '\n'.join(provider_lines)
            messages.append(dict(supplier_id=supplier['id'], supplier_name=supplier['name'], total=subtotal,
                                 count=len(invoices), invoice_ids=[i['id'] for i in invoices], text=provider_text,
                                 text_with_bank=provider_text+'\n\n'+'\n'.join(bank_lines)))
        lines.extend(['TOTAL A PAGAR: '+money(total), 'Al realizar los pagos, enviar los comprobantes indicando el proveedor y folio.', 'Resumen para coordinar pagos; confirma los datos de transferencia antes de pagar.'])
        count = sum(len(rows) for _, rows in groups.values())
        summary = '\n'.join(['Hola. Comparto el listado de pagos propuestos para la semana del '+week_label+':',
                             f'{len(messages)} proveedor(es) · {count} factura(s)', 'TOTAL A PAGAR: '+money(total), '',
                             'Enviaré un mensaje por proveedor para marcar cada pago con ✅ y adjuntar su comprobante.'])
        # The compact option shares validated selection and amounts with the detailed message.
        simple_groups = []
        for supplier, invoices in groups.values():
            by_due = {}
            for invoice in invoices:
                by_due[invoice['due_on'] or ''] = by_due.get(invoice['due_on'] or '', 0) + invoice['requested']
            for due, amount in sorted(by_due.items(), key=lambda item: (not item[0], item[0])):
                simple_groups.append(dict(supplier_id=supplier['id'], supplier_name=supplier['name'], due_on=due, amount=amount))
        simple_text = '\n'.join(f"{g['supplier_name']} - {money(g['amount'])} - "+(date.fromisoformat(g['due_on']).strftime('%d/%m/%Y') if g['due_on'] else 'Sin vencimiento registrado') for g in simple_groups)
        return {'text': '\n'.join(lines), 'total': total, 'count': count, 'summary': summary, 'messages': messages,
                'week_start': week_start.isoformat(), 'week_end': week_end.isoformat(),
                'simple_text': simple_text, 'simple_groups': simple_groups}


def file_type(content):
    if content.startswith(b'%PDF-') and b'%%EOF' in content[-2048:]:
        return 'application/pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n') and b'IEND' in content[-32:]:
        return 'image/png'
    if content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9'):
        return 'image/jpeg'
    raise DomainError('Adjunta un PDF, JPG o PNG válido (máximo 8 MB).', 415)


def attach(store, invoice_id, payment_id, content, filename, actor):
    if not 0 < len(content) <= LIMIT:
        raise DomainError('El archivo debe pesar entre 1 byte y 8 MB.', 413)
    mime = file_type(content)
    name = text(filename, 'Nombre del archivo', True, 160)
    digest = hashlib.sha256(content).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        invoice = record(db, 'ap_invoices', invoice_id)
        if payment_id is not None:
            integer(payment_id, 'Pago')
            payment = record(db, 'ap_payments', payment_id)
            if payment['invoice_id'] != invoice_id:
                raise DomainError('El pago no corresponde a esta factura.', 409)
        existing = db.execute('SELECT id FROM ap_files WHERE invoice_id=? AND payment_id IS ? AND sha256=?', (invoice_id, payment_id, digest)).fetchone()
        if existing:
            return {'id': existing['id'], 'already_attached': True}
        key = db.execute('INSERT INTO ap_files(invoice_id,payment_id,name,mime,sha256,bytes,content,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (invoice_id, payment_id, name, mime, digest, len(content), content, actor, now())).lastrowid
        event(db, 'file_attached', actor, after={'file_id': key, 'payment_id': payment_id, 'name': name, 'sha256': digest}, supplier=invoice['supplier_id'], invoice=invoice_id)
        return {'id': key}


def handle(handler, store, path, query, actor):
    if path.startswith('/api/suppliers/payment-receipts'):
        from erp import payment_receipts
        return payment_receipts.handle(handler,store,path,query)
    if path=='/api/suppliers/documents' or path.startswith('/api/suppliers/documents/'):
        from erp import document_intake
        return document_intake.handle(handler,store,path,query,actor)
    if path=='/api/suppliers/procurement' or path.startswith('/api/suppliers/procurement/'):
        from erp import procurement
        return procurement.handle(handler,store,path,query,actor)
    if path.startswith('/api/suppliers/payment-proposal') or re.fullmatch(r'/api/suppliers/invoices/\d+/payment-hold',path):
        from erp import payment_proposals
        return payment_proposals.handle(handler,store,path,query,actor)
    if path.startswith(('/api/suppliers/accounts/', '/api/suppliers/account-')):
        from erp import supplier_account
        return supplier_account.handle(handler, store, path, query, actor)
    if handler.command == 'GET':
        if path == '/api/suppliers/pending.pdf':
            from erp.supplier_finance_pdf import pending_data, pending_pdf
            data = pending_data(store)
            return handler.send(200, pending_pdf(data), 'application/pdf',
                                {'Content-Disposition': 'attachment; filename="pendientes-proveedores-'+data['today']+'.pdf"'})
        if path == '/api/suppliers/imports':
            from erp import sii_review
            with store.connect() as db:
                return handler.send(200, {'files': sii_review.listing(db)})
        if path in ('/api/suppliers/dashboard','/api/suppliers/export.csv'):
            from erp import payable_reports
            source=query.get('source',['erp'])[0]
            if path.endswith('.csv'):
                kind=query.get('kind',['invoices'])[0]
                return handler.send(200,payable_reports.export_csv(store,source,kind),'text/csv; charset=utf-8',
                                    {'Content-Disposition':'attachment; filename="proveedores-'+kind+'.csv"'})
            return handler.send(200,payable_reports.report(store,source,query.get('month',[None])[0]))
        batch_match = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})', path)
        if batch_match:
            from erp import sii_purchases
            return handler.send(200, sii_purchases.preview(store, batch_match[1]))
        if path == '/api/suppliers/payables':
            return handler.send(200, view(store))
        payment_message = re.fullmatch(r'/api/suppliers/payments/(\d+)/message', path)
        if payment_message:
            with store.connect() as db:
                db.execute('BEGIN')
                payment = record(db, 'ap_payments', int(payment_message[1]))
                invoice = record(db, 'ap_invoices', payment['invoice_id'])
                supplier = record(db, 'ap_suppliers', invoice['supplier_id'])
                if payment['voided_at'] or not payment['paid_on']:
                    raise DomainError('Este registro no corresponde a un pago vigente con fecha confirmada.', 409)
                message_text = (f"Hola, {supplier['name']}. Registramos el pago de {money(payment['amount'])} "
                                f"del {payment['paid_on']}, correspondiente a {TYPES[invoice['document_type']]} {invoice['folio']}.\n"
                                f"Referencia: {payment['reference'] or 'sin referencia'}.\n"
                                f"Saldo pendiente de esta factura: {money(invoice['amount'] - settled(db, invoice['id']))}.\n"
                                "Adjuntamos el comprobante del pago.")
                return handler.send(200, {'text': message_text})
        match = re.fullmatch(r'/api/suppliers/invoices/(\d+)/history', path)
        if match:
            return handler.send(200, history(store, int(match[1])))
        match = re.fullmatch(r'/api/suppliers/files/(\d+)', path)
        if match:
            with store.connect() as db:
                file = record(db, 'ap_files', int(match[1]))
            if len(file['content']) != file['bytes'] or hashlib.sha256(file['content']).hexdigest() != file['sha256']:
                raise DomainError('El archivo requiere revisión de integridad.', 409)
            extension = {'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg'}[file['mime']]
            disposition = 'attachment' if query.get('download') == ['1'] else 'inline'
            return handler.send(200, file['content'], file['mime'], {'Content-Disposition': f'{disposition}; filename="documento-{file["id"]}.{extension}"'})
    if handler.command == 'POST':
        upload = re.fullmatch(r'/api/suppliers/invoices/(\d+)/files', path)
        replacement = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/(replace|replacement-preview)', path)
        is_csv = path == '/api/suppliers/imports' or replacement
        if upload or is_csv:
            # Same origin/write header contract as JSON mutations, before reading bytes.
            handler.check_write_origin()
            try:
                length = int(handler.headers.get('Content-Length', '0'))
                payment_id = int(query['payment'][0]) if 'payment' in query else None
            except ValueError:
                raise DomainError('Tamaño o pago no válido.') from None
            if not 0 < length <= LIMIT:
                raise DomainError('El archivo debe pesar hasta 8 MB.', 413)
            content = handler.rfile.read(length)
            if len(content) != length:
                raise DomainError('El archivo llegó incompleto. Intenta nuevamente.')
            if is_csv:
                from erp import sii_purchases
                if replacement:
                    from erp import sii_review
                    if replacement[2] == 'replace' and query.get('confirmed') != ['1']:
                        raise DomainError('Confirma el reemplazo del archivo.')
                    try: review_version = int(query.get('version', [''])[0])
                    except ValueError: raise DomainError('Versión de la revisión no válida.') from None
                    name = unquote(handler.headers.get('X-File-Name', 'compras.csv'))
                    if replacement[2] == 'replacement-preview':
                        return handler.send(200, sii_review.compare(store, replacement[1], content, name, review_version))
                    return handler.send(201, sii_review.replace(store, replacement[1], content, name, actor, review_version, query.get('token',[''])[0]))
                return handler.send(201, sii_purchases.upload(store, content, unquote(handler.headers.get('X-File-Name', 'compras.csv')), actor))
            return handler.send(201, attach(store, int(upload[1]), payment_id, content, unquote(handler.headers.get('X-File-Name', 'documento')), actor))
        large_review = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/(review|confirm)', path)
        data = handler.body(maximum=LIMIT) if large_review else handler.body()
        trash_match = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/trashed', path)
        if trash_match:
            from erp import sii_review
            return handler.send(200, sii_review.set_trashed(store, trash_match[1], data, actor))
        close_match = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/closed', path)
        if close_match:
            from erp import sii_review
            return handler.send(200, sii_review.set_closed(store, close_match[1], data, actor))
        review_match = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/review', path)
        if review_match:
            from erp import sii_review
            return handler.send(200, sii_review.save(store, review_match[1], data, actor))
        batch_match = re.fullmatch(r'/api/suppliers/imports/([a-f0-9]{64})/confirm', path)
        if batch_match:
            from erp import sii_purchases
            return handler.send(200, sii_purchases.confirm(store, batch_match[1], data, actor))
        if path == '/api/suppliers/message':
            return handler.send(200, message(store, data))
        if path == '/api/suppliers/schedule':
            return handler.send(200, execute(store, 'schedule', data, actor))
        if path == '/api/suppliers/providers':
            return handler.send(201, execute(store, 'supplier', data, actor))
        if path == '/api/suppliers/invoices':
            return handler.send(201, execute(store, 'invoice', data, actor))
        match = re.fullmatch(r'/api/suppliers/(providers|invoices|payments)/(\d+)(/payments|/void|/reconcile)?', path)
        if match:
            kind, key, suffix = match.groups()
            action = {('providers', None): 'supplier', ('invoices', None): 'invoice', ('invoices', '/payments'): 'payment', ('invoices', '/void'): 'void_invoice', ('payments', '/void'): 'void_payment', ('invoices', '/reconcile'): 'reconcile'}.get((kind, suffix))
            if action:
                return handler.send(200, execute(store, action, data, actor, int(key)))
    raise DomainError('Ruta de proveedores no encontrada.', 404)
