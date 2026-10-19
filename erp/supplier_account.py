"""Supplier payments, monetary credit notes and applications; never bank/stock writes.

Historical ap_payments are read directly, not copied into this ledger. This makes
the additive schema migration repeatable without inventing dates or double debt.
All mutations run inside payables.execute's write lock and idempotency contract.
"""
import csv
import base64
import binascii
import hashlib
import io
import json
import re
from datetime import datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from erp import payables as ap
from erp.domain import DomainError
from erp.store import now


def allocated(db, invoice, kind):
    return db.execute('''SELECT COALESCE(SUM(a.amount),0) FROM ap_account_applications a
        JOIN ap_account_entries e ON e.id=a.entry_id WHERE a.invoice_id=? AND e.kind=?
        AND a.voided_at IS NULL AND e.voided_at IS NULL''', (invoice, kind)).fetchone()[0]


def applied(db, entry):
    return db.execute('SELECT COALESCE(SUM(amount),0) FROM ap_account_applications WHERE entry_id=? AND voided_at IS NULL', (entry,)).fetchone()[0]


def origin_credit(db, invoice):
    return db.execute("SELECT COALESCE(SUM(amount),0) FROM ap_account_entries WHERE origin_invoice_id=? AND kind='credit_note' AND voided_at IS NULL", (invoice,)).fetchone()[0]


def validate_applications(db, entry, items):
    if not isinstance(items, list) or len(items) > 100:
        raise DomainError('Selecciona hasta 100 facturas para aplicar el importe.')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise DomainError('Aplicación no válida.')
        key = ap.integer(item.get('invoice_id'), 'Factura')
        if key in seen:
            raise DomainError('Una factura está repetida en las aplicaciones.')
        seen.add(key)
        invoice = ap.record(db, 'ap_invoices', key)
        ap.version(item, invoice)
        if invoice['supplier_id'] != entry['supplier_id'] or invoice['voided_at'] or invoice['payment_review']:
            raise DomainError('Aplica solo a facturas vigentes y con saldo validado del mismo proveedor.', 409)
        amount = ap.integer(item.get('amount'), 'Monto a aplicar')
        if amount > invoice['amount'] - ap.settled(db, key):
            raise DomainError('La aplicación supera el saldo pendiente de la factura.', 409)
        result.append((invoice, amount))
    if sum(amount for _, amount in result) > entry['amount'] - applied(db, entry['id']):
        raise DomainError('Las aplicaciones superan el saldo disponible del pago o nota de crédito.', 409)
    return result


def apply_items(db, entry, items, actor):
    validated = validate_applications(db, entry, items)
    for invoice, amount in validated:
        key = db.execute('INSERT INTO ap_account_applications(entry_id,invoice_id,amount,actor,created_at) VALUES(?,?,?,?,?)',
                         (entry['id'], invoice['id'], amount, actor, now())).lastrowid
        ap.touch(db, invoice['id'], amount)
        ap.event(db, 'account_applied', actor, after=dict(application_id=key, entry_id=entry['id'], kind=entry['kind'], amount=amount),
                 supplier=entry['supplier_id'], invoice=invoice['id'])
    return len(validated)


def mutate(db, action, data, actor, target):
    if action == 'account_invoice_credit_note':
        origin = ap.integer(data.get('origin_invoice_id'), 'Factura de origen')
        invoice = ap.record(db, 'ap_invoices', origin)
        ap.version({'version': data.get('origin_version')}, invoice)
        amount = ap.integer(data.get('amount'), 'Monto de nota de crédito')
        reason = ap.text(data.get('notes'), 'Motivo de la nota de crédito recibida', True, 1000)
        # Only this origin invoice is affected. Existing payments and any
        # available overpayment remain intact in the same account ledger.
        allocated_amount = min(amount, max(0, invoice['amount'] - ap.settled(db, origin)))
        items = [dict(invoice_id=origin, version=invoice['version'], amount=allocated_amount)] if allocated_amount else []
        if 'applications' in data:
            raise DomainError('La nota desde factura se aplica solo a su factura de origen.')
        proof = data.get('proof')
        content = None
        if proof is not None:
            if not isinstance(proof, dict) or not isinstance(proof.get('base64'), str):
                raise DomainError('Adjunta un documento PDF, JPG o PNG.')
            if len(proof['base64']) > 4 * ((ap.LIMIT + 2) // 3):
                raise DomainError('El documento debe pesar hasta 8 MB.', 413)
            try: content = base64.b64decode(proof['base64'], validate=True)
            except (ValueError, binascii.Error):
                raise DomainError('El documento no llegó en un formato válido.') from None
        result = mutate(db, 'account_credit_note', dict(data, notes=reason, applications=items), actor, target)
        file_id = attach_in_transaction(db, result['id'], content, proof.get('name'), actor)['id'] if proof is not None else None
        ap.event(db, 'invoice_credit_note_received', actor, reason=reason,
                 after=dict(entry_id=result['id'],folio=result['folio'],amount=amount,applied=allocated_amount,available=amount-allocated_amount,file_id=file_id),
                 supplier=invoice['supplier_id'], invoice=origin)
        return dict(result, applied=allocated_amount, available=amount-allocated_amount, file_id=file_id,
                    invoice_original=invoice['amount'], invoice_balance=invoice['amount']-ap.settled(db,origin))
    if action in ('account_joint_payment', 'account_invoice_payment'):
        items = data.get('applications')
        if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
            raise DomainError('Selecciona las facturas y los importes a aplicar.')
        # Same ledger and write lock for individual payments and joint payments.
        # Selection includes rows with a zero allocation, so their versions and
        # balances also form part of the human-reviewed total.
        selection = data.get('invoices', [dict(id=i.get('invoice_id'), version=i.get('version')) for i in items])
        selected = ap.selections(db, dict(items=selection))
        if action == 'account_invoice_payment' and len(selected) != 1:
            raise DomainError('Selecciona una factura para el pago individual.')
        if action == 'account_joint_payment' and len(selected) < 2:
            raise DomainError('Selecciona entre 2 y 100 facturas para el pago conjunto.')
        supplier = ap.integer(data.get('supplier_id'), 'Proveedor')
        if any(invoice['supplier_id'] != supplier for invoice, _ in selected):
            raise DomainError('Todas las facturas deben pertenecer al mismo proveedor.', 409)
        balances = {invoice['id']:invoice['amount']-ap.settled(db, invoice['id']) for invoice, _ in selected}
        versions = {invoice['id']:invoice['version'] for invoice, _ in selected}
        if any(i.get('invoice_id') not in balances or i.get('version') != versions[i['invoice_id']] for i in items):
            raise DomainError('Las aplicaciones no corresponden a la selección revisada.', 409)
        selected_balance = sum(balances.values())
        total = ap.integer(data.get('amount'), 'Monto efectivamente pagado')
        allocated_total = sum(ap.integer(i.get('amount'), 'Monto por factura') for i in items)
        if allocated_total != min(total, selected_balance):
            raise DomainError('Distribuye el pago entre las facturas seleccionadas hasta cubrir el pago o su saldo total.')
        excess = total-allocated_total
        if excess:
            confirmation = data.get('overpayment_confirmation')
            if not isinstance(confirmation, dict) or confirmation.get('confirmed') is not True or confirmation.get('selected_balance') != selected_balance or confirmation.get('excess') != excess:
                raise DomainError('Confirma el pago de '+ap.money(total)+' para facturas con saldo de '+ap.money(selected_balance)+'. Los '+ap.money(excess)+' restantes quedarán como saldo a nuestro favor con este proveedor.', 409)
        proof = data.get('proof')
        if proof is not None and (not isinstance(proof, dict) or not isinstance(proof.get('base64'), str)):
            raise DomainError('Adjunta un único comprobante PDF, JPG o PNG.')
        if proof is not None:
            encoded = proof['base64']
            if len(encoded) > 4 * ((ap.LIMIT + 2) // 3):
                raise DomainError('El comprobante debe pesar hasta 8 MB.', 413)
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise DomainError('El comprobante no llegó en un formato válido.') from None
        # The caller owns one BEGIN IMMEDIATE transaction for payment, allocations,
        # proof, audit and idempotency. A failed attachment rolls all of them back.
        entry = mutate(db, 'account_payment', data, actor, target)
        file_id = None
        if proof is not None:
            file_id = attach_in_transaction(db, entry['id'], content, proof.get('name'), actor)['id']
        return dict(entry, file_id=file_id, applied=allocated_total, available=excess)
    if action in ('account_payment', 'account_credit_note'):
        if data.get('confirmed') is not True:
            raise DomainError('Confirma el pago realizado o la nota de crédito recibida.')
        ap.choice(data.get('currency', 'CLP'), {'CLP'}, 'Moneda')
        supplier = ap.integer(data.get('supplier_id'), 'Proveedor')
        ap.record(db, 'ap_suppliers', supplier)
        amount = ap.integer(data.get('amount'), 'Monto total en pesos CLP')
        occurred = ap.day(data.get('occurred_on'), 'Fecha del documento o pago')
        if occurred > ap.local_day():
            raise DomainError('No se registran pagos o notas de crédito con fecha futura.')
        kind = 'payment' if action == 'account_payment' else 'credit_note'
        folio, origin, method = '', None, ''
        if kind == 'payment':
            method = ap.choice(data.get('method'), ap.METHODS, 'Medio de pago')
        else:
            origin = ap.integer(data.get('origin_invoice_id'), 'Factura de origen de la nota de crédito')
            invoice = ap.record(db, 'ap_invoices', origin)
            ap.version({'version': data.get('origin_version')}, invoice)
            if invoice['supplier_id'] != supplier or invoice['voided_at'] or invoice['payment_review']:
                raise DomainError('La nota requiere una factura vigente y validada del mismo proveedor.', 409)
            if occurred < invoice['issued_on']:
                raise DomainError('La nota de crédito no puede preceder a su factura de origen.')
            folio = ap.text(data.get('folio'), 'Folio de nota de crédito', True, 80).upper()
            if folio.isascii() and folio.isdigit():
                folio = str(int(folio))
            if amount + origin_credit(db, origin) > invoice['amount']:
                raise DomainError('Las notas de crédito vigentes superan el importe de la factura de origen.', 409)
        key = db.execute('''INSERT INTO ap_account_entries(supplier_id,kind,amount,currency,occurred_on,method,reference,folio,origin_invoice_id,notes,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (supplier, kind, amount, 'CLP', occurred, method,
            ap.text(data.get('reference', ''), 'Referencia', maximum=160), folio, origin,
            ap.text(data.get('notes', ''), 'Notas', maximum=1000), actor, now())).lastrowid
        entry = ap.record(db, 'ap_account_entries', key)
        count = apply_items(db, entry, data.get('applications', []), actor)
        # Even an unapplied NC changes the origin's correction/void constraints.
        if origin and not any(item['invoice_id'] == origin for item in data.get('applications', [])):
            ap.touch(db, origin)
        initial_ids = [r[0] for r in db.execute('SELECT id FROM ap_account_applications WHERE entry_id=? ORDER BY id', (key,))]
        ap.event(db, 'account_'+kind+'_recorded', actor, after=dict(entry, initial_application_ids=initial_ids,
                 initial_available_credit=amount-applied(db, key)), supplier=supplier)
        return dict(entry, applied_count=count)

    if action == 'account_unapply':
        application = ap.record(db, 'ap_account_applications', target)
        entry = ap.record(db, 'ap_account_entries', application['entry_id'])
    else:
        entry = ap.record(db, 'ap_account_entries', target)
    ap.version(data, entry)
    if entry['voided_at']:
        raise DomainError('El pago o nota de crédito está anulado.', 409)
    if action == 'account_apply':
        if data.get('confirmed') is not True:
            raise DomainError('Confirma las facturas y los importes a aplicar del saldo a favor.')
        if not data.get('applications'):
            raise DomainError('Indica al menos una aplicación.')
        apply_items(db, entry, data['applications'], actor)
        db.execute('UPDATE ap_account_entries SET version=version+1 WHERE id=?', (entry['id'],))
        return ap.record(db, 'ap_account_entries', entry['id'])

    reason = ap.text(data.get('reason'), 'Motivo de anulación', True)
    if action == 'account_unapply':
        if application['voided_at']:
            raise DomainError('Esta aplicación ya está anulada.', 409)
        invoice = ap.record(db, 'ap_invoices', application['invoice_id'])
        ap.version({'version': data.get('invoice_version')}, invoice)
        to_void = [application]
    elif action == 'account_void':
        later = later_applications(db, entry)
        if later:
            details = ', '.join('#'+str(a['id'])+' a factura '+a['folio']+' ('+ap.money(a['amount'])+')' for a in later)
            raise DomainError('Este pago tiene crédito aplicado posteriormente. Revierte primero las aplicaciones '+details+'. Después podrás anular el pago conservando su comprobante.', 409)
        to_void = [dict(r) for r in db.execute('SELECT * FROM ap_account_applications WHERE entry_id=? AND voided_at IS NULL', (entry['id'],))]
        versions = data.get('invoices', [])
        if not isinstance(versions, list) or any(not isinstance(v, dict) for v in versions):
            raise DomainError('Versiones de facturas no válidas.')
        expected = {a['invoice_id'] for a in to_void}
        if len(versions) != len(expected) or {v.get('id') for v in versions if type(v.get('id')) is int} != expected:
            raise DomainError('Actualiza las facturas afectadas antes de anular.', 409)
        for value in versions:
            ap.version(value, ap.record(db, 'ap_invoices', value['id']))
    else:
        raise DomainError('Operación de cuenta corriente no encontrada.', 404)
    stamp = now()
    touched = set()
    for application in to_void:
        db.execute('UPDATE ap_account_applications SET voided_at=?,void_reason=?,voided_by=? WHERE id=?', (stamp, reason, actor, application['id']))
        touched.add(application['invoice_id'])
        ap.event(db, 'account_application_voided', actor, reason, application,
                 {'voided_at': stamp, 'voided_by': actor}, supplier=entry['supplier_id'], invoice=application['invoice_id'])
    if action == 'account_void':
        db.execute('UPDATE ap_account_entries SET voided_at=?,void_reason=?,voided_by=? WHERE id=?', (stamp, reason, actor, entry['id']))
        if entry['origin_invoice_id']:
            touched.add(entry['origin_invoice_id'])
        ap.event(db, 'account_entry_voided', actor, reason, entry, {'voided_at': stamp, 'voided_by': actor}, supplier=entry['supplier_id'])
    for invoice_id in touched:
        ap.touch(db, invoice_id)
    db.execute('UPDATE ap_account_entries SET version=version+1 WHERE id=?', (entry['id'],))
    return ap.record(db, 'ap_account_entries', entry['id'])


def payment_origin(db, entry):
    if entry['kind'] != 'payment':
        return None
    events = db.execute("SELECT after_json FROM ap_events WHERE action='account_payment_recorded' AND supplier_id=? ORDER BY id DESC", (entry['supplier_id'],))
    for event in events:
        value = json.loads(event['after_json'])
        if value.get('id') == entry['id']:
            return value
    return None


def later_applications(db, entry):
    if entry['kind'] != 'payment':
        return []
    origin = payment_origin(db, entry) or {}
    # For old payments without an initial snapshot, require explicit reversal
    # of applications instead of guessing which consumed the original credit.
    initial = set(origin.get('initial_application_ids', []))
    return [dict(r) for r in db.execute("SELECT a.*,i.folio FROM ap_account_applications a JOIN ap_invoices i ON i.id=a.invoice_id WHERE a.entry_id=? AND a.voided_at IS NULL ORDER BY a.id", (entry['id'],)) if r['id'] not in initial]


def entries(db):
    rows = [dict(r) for r in db.execute('SELECT * FROM ap_account_entries ORDER BY occurred_on,id')]
    applications = [dict(r) for r in db.execute('''SELECT a.*,i.folio,i.version AS invoice_version
        FROM ap_account_applications a JOIN ap_invoices i ON i.id=a.invoice_id ORDER BY a.id''')]
    files = [dict(r) for r in db.execute('SELECT id,entry_id,name,mime,bytes,sha256,actor,created_at FROM ap_account_files ORDER BY id')]
    for row in rows:
        row['applications'] = [a for a in applications if a['entry_id'] == row['id']]
        row['applied'] = sum(a['amount'] for a in row['applications'] if not a['voided_at']) if not row['voided_at'] else 0
        row['available'] = row['amount'] - row['applied'] if not row['voided_at'] else 0
        row['files'] = [f for f in files if f['entry_id'] == row['id']]
        origin = payment_origin(db, row) or {}
        row['initial_available_credit'] = origin.get('initial_available_credit')
        row['reversal_blockers'] = later_applications(db, row) if not row['voided_at'] else []
    return rows


def select_journal(journal, start='', end='', kind='all'):
    start, end = ap.day(start, 'Desde', False), ap.day(end, 'Hasta', False)
    ap.choice(kind, {'all', 'invoices', 'payments', 'credit_notes', 'applications'}, 'Tipo de movimiento')
    if start and end and start > end:
        raise DomainError('La fecha inicial no puede superar la fecha final.')
    def category(row):
        if row['kind'] in ('invoice', 'invoice_void'):
            return 'invoices'
        if row['kind'] in ('application', 'application_void'):
            return 'applications'
        if row['kind'] == 'credit_note' or row.get('source_kind') == 'credit_note':
            return 'credit_notes'
        return 'payments'
    period = [row for row in journal if (not start or row['date'] >= start) and (not end or row['date'] <= end)]
    visible = [row for row in period if kind == 'all' or category(row) == kind]
    opening = sum(row['debit']-row['credit'] for row in journal if start and row['date'] < start)
    return visible, dict(start=start, end=end, kind=kind, opening_balance=opening,
                         closing_balance=opening+sum(row['debit']-row['credit'] for row in period),
                         shown_debit=sum(row['debit'] for row in visible), shown_credit=sum(row['credit'] for row in visible),
                         shown_count=len(visible), period_count=len(period),
                         undated_count=sum(not row['date'] for row in journal))


def statement(store, supplier_id, start='', end='', kind='all'):
    data = ap.view(store)
    supplier = next((s for s in data['suppliers'] if s['id'] == supplier_id), None)
    if not supplier:
        raise DomainError('Proveedor no encontrado.', 404)
    invoices = [r for r in data['invoices'] if r['supplier_id'] == supplier_id]
    account = [r for r in data['account_entries'] if r['supplier_id'] == supplier_id]
    journal = []
    def add(kind, label, date, created, debit=0, credit=0, **links):
        journal.append(dict(kind=kind, label=label, date=date, created_at=created, debit=debit, credit=credit, **links))
    def business_day(stamp):
        return datetime.fromisoformat(stamp).astimezone(ZoneInfo('America/Santiago')).date().isoformat()
    for invoice in invoices:
        if invoice['payment_review']:
            continue
        add('invoice', ap.TYPES[invoice['document_type']]+' '+invoice['folio'], invoice['issued_on'], invoice['created_at'],
            debit=invoice['amount'], invoice_id=invoice['id'])
        if invoice['voided_at']:
            add('invoice_void', 'Anulación de factura '+invoice['folio'], business_day(invoice['voided_at']), invoice['voided_at'], credit=invoice['amount'], invoice_id=invoice['id'])
        for payment in invoice['payments']:
            add('legacy_payment', f"Pago histórico PH-{payment['id']} · factura "+invoice['folio'], payment['paid_on'], payment['created_at'], credit=payment['amount'], invoice_id=invoice['id'], payment_id=payment['id'], actor=payment['actor'])
            if payment['voided_at']:
                add('legacy_payment_void', 'Anulación de pago histórico · '+payment['void_reason'], business_day(payment['voided_at']), payment['voided_at'], debit=payment['amount'], payment_id=payment['id'])
    for entry in account:
        code = f"CC-{entry['id']}"
        label = ('Pago / anticipo '+code if entry['kind'] == 'payment' else 'Nota de crédito '+entry['folio']+' · '+code)
        if entry['reference']:
            label += ' · '+entry['reference']
        if entry['kind'] == 'credit_note':
            origin = next((i for i in invoices if i['id'] == entry['origin_invoice_id']), None)
            label += ' · Factura origen '+(origin['folio'] if origin else str(entry['origin_invoice_id']))
            label += ' · Aplicado vigente '+ap.money(entry['applied'])+' · Disponible '+ap.money(entry['available'])
        if entry['kind'] == 'payment':
            label += ' · Aplicado vigente '+ap.money(entry['applied'])+' · Saldo a favor disponible '+ap.money(entry['available'])
        add(entry['kind'], label, entry['occurred_on'], entry['created_at'], credit=entry['amount'], entry_id=entry['id'], actor=entry['actor'], invoice_id=entry['origin_invoice_id'] if entry['kind']=='credit_note' else None)
        if entry['voided_at']:
            add('entry_void', 'Anulación de '+label+' · '+entry['void_reason'], business_day(entry['voided_at']), entry['voided_at'], debit=entry['amount'], entry_id=entry['id'], source_kind=entry['kind'], actor=entry['voided_by'])
        for application in entry['applications']:
            add('application', 'Aplicación '+code+' de '+ap.money(application['amount'])+' a factura '+application['folio'], business_day(application['created_at']), application['created_at'], entry_id=entry['id'], invoice_id=application['invoice_id'], application_id=application['id'], actor=application['actor'])
            if application['voided_at']:
                add('application_void', 'Aplicación '+code+' anulada · '+application['void_reason'], business_day(application['voided_at']), application['voided_at'], entry_id=entry['id'], invoice_id=application['invoice_id'], application_id=application['id'], actor=application['voided_by'])
    journal.sort(key=lambda row: (row['date'], row['created_at']))
    running = 0
    for row in journal:
        running += row['debit'] - row['credit']
        row['balance'] = running
    available = sum(entry['available'] for entry in account)
    net = supplier['balance'] - available
    visible, period = select_journal(journal, start, end, kind)
    return dict(supplier=supplier, invoices=invoices, entries=account, journal=visible, period=period,
                totals=dict(outstanding=supplier['balance'], available_credit=available, net_balance=net,
                            payable=max(net, 0), in_favor=max(-net, 0), consistent=running == net),
                today=data['today'], currency='CLP', methods=ap.METHODS)


def export_statement(data):
    """Export the same filtered journal, retaining full-account running balances."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(['proveedor_id','proveedor','rut','fecha','movimiento','tipo','cargo_clp','abono_clp',
                     'saldo_acumulado_cuenta_clp','id_factura','id_pago_historico','id_cuenta','id_aplicacion',
                     'registrado_por','fecha_registro','saldo_anterior_intervalo_clp','saldo_final_intervalo_clp',
                     'saldo_actual_clp','fecha_consulta'])
    def safe(value):
        return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else value
    for row in data['journal']:
        writer.writerow([safe(value) for value in [data['supplier']['id'],data['supplier']['name'],data['supplier']['tax_id'],
                         row['date'],row['label'],row['kind'],row['debit'],row['credit'],row['balance'],
                         row.get('invoice_id',''),row.get('payment_id',''),row.get('entry_id',''),row.get('application_id',''),
                         row.get('actor',''),row['created_at'],data['period']['opening_balance'],data['period']['closing_balance'],
                         data['totals']['net_balance'],data['today']]])
    return b'\xef\xbb\xbf'+stream.getvalue().encode('utf-8')


def attach_in_transaction(db, entry_id, content, filename, actor):
    if not 0 < len(content) <= ap.LIMIT:
        raise DomainError('El archivo debe pesar entre 1 byte y 8 MB.', 413)
    mime = ap.file_type(content)
    name = ap.text(filename, 'Nombre del archivo', True, 160)
    digest = hashlib.sha256(content).hexdigest()
    entry = ap.record(db, 'ap_account_entries', entry_id)
    old = db.execute('SELECT id FROM ap_account_files WHERE entry_id=? AND sha256=?', (entry_id, digest)).fetchone()
    if old:
        return {'id': old['id'], 'already_attached': True}
    key = db.execute('INSERT INTO ap_account_files(entry_id,name,mime,sha256,bytes,content,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',
                     (entry_id, name, mime, digest, len(content), content, actor, now())).lastrowid
    ap.event(db, 'account_file_attached', actor, after=dict(entry_id=entry_id, file_id=key, sha256=digest, name=name), supplier=entry['supplier_id'])
    return {'id': key}


def attach(store, entry_id, content, filename, actor):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return attach_in_transaction(db, entry_id, content, filename, actor)


def handle(handler, store, path, query, actor):
    match = re.fullmatch(r'/api/suppliers/accounts/(\d+)(/export\.(csv|pdf))?', path)
    if handler.command == 'GET' and match:
        data = statement(store, int(match[1]), query.get('from',[''])[0], query.get('to',[''])[0], query.get('kind',['all'])[0])
        if match[3] == 'pdf':
            from erp.supplier_finance_pdf import statement_pdf
            return handler.send(200, statement_pdf(data), 'application/pdf',
                                {'Content-Disposition': f'attachment; filename="cartola-proveedor-{match[1]}.pdf"'})
        if match[2]:
            return handler.send(200, export_statement(data), 'text/csv; charset=utf-8',
                                {'Content-Disposition': f'attachment; filename="cartola-proveedor-{match[1]}.csv"'})
        return handler.send(200, data)
    file_match = re.fullmatch(r'/api/suppliers/account-files/(\d+)', path)
    if handler.command == 'GET' and file_match:
        with store.connect() as db:
            file = ap.record(db, 'ap_account_files', int(file_match[1]))
        if hashlib.sha256(file['content']).hexdigest() != file['sha256'] or len(file['content']) != file['bytes']:
            raise DomainError('El archivo requiere revisión de integridad.', 409)
        extension = {'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg'}[file['mime']]
        disposition = 'attachment' if query.get('download') == ['1'] else 'inline'
        return handler.send(200, file['content'], file['mime'], {'Content-Disposition': f'{disposition}; filename="cuenta-{file["id"]}.{extension}"'})
    if handler.command == 'POST':
        if path == '/api/suppliers/account-invoice-credit-notes':
            data = handler.body(maximum=4 * ((ap.LIMIT + 2) // 3) + 65536)
            return handler.send(201, ap.execute(store, 'account_invoice_credit_note', data, actor))
        if path in ('/api/suppliers/account-joint-payments', '/api/suppliers/account-invoice-payments'):
            # Base64 adds 4/3 overhead; the decoded file still has the same 8 MB limit.
            data = handler.body(maximum=4 * ((ap.LIMIT + 2) // 3) + 65536)
            action = 'account_joint_payment' if path.endswith('account-joint-payments') else 'account_invoice_payment'
            return handler.send(201, ap.execute(store, action, data, actor))
        upload = re.fullmatch(r'/api/suppliers/account-entries/(\d+)/files', path)
        if upload:
            handler.check_write_origin()
            try: length = int(handler.headers.get('Content-Length', '0'))
            except ValueError: raise DomainError('Tamaño no válido.') from None
            if not 0 < length <= ap.LIMIT:
                raise DomainError('El archivo debe pesar hasta 8 MB.', 413)
            content = handler.rfile.read(length)
            if len(content) != length:
                raise DomainError('El archivo llegó incompleto.')
            return handler.send(201, attach(store, int(upload[1]), content, unquote(handler.headers.get('X-File-Name', 'documento')), actor))
        data = handler.body()
        if path in ('/api/suppliers/account-payments', '/api/suppliers/account-credit-notes'):
            action = 'account_payment' if path.endswith('account-payments') else 'account_credit_note'
            return handler.send(201, ap.execute(store, action, data, actor))
        match = re.fullmatch(r'/api/suppliers/account-entries/(\d+)/(apply|void)', path)
        if match:
            return handler.send(200, ap.execute(store, 'account_'+match[2], data, actor, int(match[1])))
        match = re.fullmatch(r'/api/suppliers/account-applications/(\d+)/void', path)
        if match:
            return handler.send(200, ap.execute(store, 'account_unapply', data, actor, int(match[1])))
    raise DomainError('Ruta de cuenta corriente no encontrada.', 404)
