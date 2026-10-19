"""Immutable opening evidence and auditable purchase conditions; no payment execution."""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from erp.domain import DomainError
from erp.delivery_adjustments import concept, request_id, stamp

CONDITIONS = {'unknown': 'Sin definir', 'credit': 'Crédito', 'cash': 'Contado / contra entrega', 'inherit': 'Usar condición del proveedor'}


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD', str(value or '')) if not unicodedata.combining(c)).casefold().split())


def document_key(row):
    return normalized(row['name']), str(row.get('folio')) if row.get('folio') is not None else ''


def short_folio(value):
    return value is not None and str(value).isdigit() and 0 <= int(str(value)) <= 7


def date_text(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    return str(value)


def validate(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('file'), dict):
        raise DomainError('Falta la identidad del archivo fuente.')
    source = snapshot['file']
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,160}', source.get('id', '')) or not source.get('modified_time') or not source.get('title'):
        raise DomainError('Identidad o fecha de la fuente no válida.')
    for section in ('ledger', 'form'):
        rows = snapshot.get(section)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
            raise DomainError('El lote necesita libro y formulario con filas auditadas.')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or type(row.get('row')) is not int or not 1 <= row['row'] <= 100000 or row['row'] in seen:
                raise DomainError('Fila de origen repetida o no válida.')
            seen.add(row['row'])
            if not isinstance(row.get('name'), str) or not row['name'].strip() or len(row['name']) > 200:
                raise DomainError('Proveedor de origen no válido.')
            for key in ('amount', 'paid', 'balance') if section == 'ledger' else ('amount',):
                value = row.get(key)
                if (value is None and key != 'amount'):
                    continue
                if type(value) is not int or abs(value) > 100_000_000_000 or (key != 'balance' and value < 0):
                    raise DomainError('Importe de origen no válido; se requieren pesos enteros.')
            for key in ('folio', 'due', 'date', 'day', 'status'):
                value = row.get(key)
                if value is not None and (type(value) not in (str, int, float) or len(str(value)) > 200):
                    raise DomainError('Referencia de origen no válida.')
            due = row.get('due')
            if isinstance(due, (int, float)) and not 0 <= due <= 100000:
                raise DomainError('Vencimiento de origen no válido.')
    return snapshot


def import_snapshot(store, path, actor):
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise DomainError('La evidencia supera el tamaño permitido.')
    raw = path.read_bytes()
    try:
        snapshot = validate(json.loads(raw))
    except (ValueError, TypeError, UnicodeError):
        raise DomainError('La evidencia JSON no es válida.') from None
    actor = concept(actor, 3, 100)
    digest = hashlib.sha256(raw).hexdigest()
    source = snapshot['file']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT id FROM supplier_opening_batches WHERE source_id=?', (source['id'],)).fetchone()
        if existing:
            if existing['id'] != digest:
                raise DomainError('Ya existe un saldo inicial de esta fuente. Una versión distinta necesita conciliación explícita.', 409)
            return {'batch_id': digest, 'rows': len(snapshot['ledger']), 'already_imported': True}
        # One initial source avoids accidentally adding overlapping opening balances.
        if db.execute('SELECT 1 FROM supplier_opening_batches').fetchone():
            raise DomainError('Ya existe un lote inicial. No se acumulan fuentes automáticamente.', 409)
        db.execute('INSERT INTO supplier_opening_batches VALUES(?,?,?,?,?,?,?)',
                   (digest, source['id'], source['title'], source['modified_time'], stamp(), actor, raw))
        db.executemany('INSERT INTO supplier_opening_records VALUES(?,?,?,?)',
                       [(digest, r['row'], r['name'], json.dumps(r, ensure_ascii=False)) for r in snapshot['ledger']])
    return {'batch_id': digest, 'rows': len(snapshot['ledger']), 'already_imported': False}


def findings(ledger, form):
    issues = []
    def add(kind, title, rows=(), forms=(), detail=''):
        issues.append({'kind': kind, 'title': title, 'rows': list(rows), 'form_rows': list(forms), 'detail': detail})
    ledger_keys = defaultdict(list)
    form_keys = defaultdict(list)
    for r in ledger:
        if r.get('folio') is not None:
            ledger_keys[(*document_key(r), r['amount'])].append(r)
        balance = r.get('balance')
        if balance is not None and balance < 0:
            add('negative', 'Saldo negativo por aclarar', [r['row']], detail='No se compensa con otras facturas ni se considera crédito confirmado.')
        if balance is None:
            add('blank_balance', 'Saldo vacío en la planilla', [r['row']], detail='Se muestra el cálculo por separado; el original se conserva vacío.')
        elif balance != r['amount'] - (r.get('paid') or 0):
            add('balance_difference', 'Saldo distinto de monto menos abono registrado', [r['row']])
    for r in form:
        form_keys[(*document_key(r), r['amount'])].append(r)
    for group in ledger_keys.values():
        if len(group) > 1:
            add('duplicate', 'Posible documento duplicado en el libro', [r['row'] for r in group], detail='Coinciden nombre normalizado, folio e importe. No demuestra doble pago.')
    for key, group in form_keys.items():
        if len(group) > 1:
            add('form_duplicate', 'Entrada repetida en el formulario', forms=[r['row'] for r in group], detail='Se conserva como antecedente; no crea otra obligación.')
        exact = ledger_keys.get(key, [])
        if not exact:
            candidates = [r for r in ledger if document_key(r) == key[:2] and key[1]]
            add('amount_difference' if candidates else 'form_unmatched',
                'Importes distintos entre formulario y libro' if candidates else 'Formulario sin coincidencia exacta en el libro',
                [r['row'] for r in candidates], [r['row'] for r in group],
                'No se elige un importe ni se agrega deuda automáticamente.')
        for f in group:
            if f.get('due') is not None and exact and any(date_text(r.get('due')) != date_text(f['due']) for r in exact):
                add('due_difference', 'Vencimiento del formulario pendiente de confirmar', [r['row'] for r in exact], [f['row']],
                    'Fecha declarada en formulario: ' + date_text(f['due']) + '. No se aplica automáticamente al libro.')
    return issues


def view(store):
    with store.connect() as db:
        batch = db.execute('SELECT * FROM supplier_opening_batches').fetchone()
        if not batch:
            return {'available': False}
        snapshot = json.loads(batch['original'])
        rows = [json.loads(r['original_json']) for r in db.execute('SELECT original_json FROM supplier_opening_records WHERE batch_id=? ORDER BY source_row', (batch['id'],))]
        history = [dict(r) for r in db.execute('SELECT * FROM supplier_opening_annotations WHERE batch_id=? ORDER BY target_kind,target,version', (batch['id'],))]
    latest = {(a['target_kind'], a['target']): a for a in history}
    issues = findings(rows, snapshot['form'])
    for row in rows:
        own = latest.get(('row', str(row['row'])))
        default = latest.get(('supplier', row['name']))
        condition = own['condition'] if own and own['condition'] != 'inherit' else default['condition'] if default else 'unknown'
        computed = row['amount'] - (row.get('paid') or 0)
        balance = row.get('balance')
        row.update(condition=condition, condition_source='row' if own and own['condition'] != 'inherit' else 'supplier', folio_review=short_folio(row.get('folio')),
                   annotation=own, computed_balance=computed, due_text=date_text(row.get('due')),
                   payment_state='review' if balance is None or balance < 0 else 'paid' if balance == 0 else 'partial' if (row.get('paid') or 0) > 0 else 'pending',
                   issues=[i for i, issue in enumerate(issues) if row['row'] in issue['rows']])
    suppliers = [{'name': name, 'annotation': latest.get(('supplier', name)),
                  'condition': latest.get(('supplier', name), {}).get('condition', 'unknown')}
                 for name in sorted({r['name'] for r in rows}, key=normalized)]
    positives = [r for r in rows if (r.get('balance') or 0) > 0]
    totals = {'rows': len(rows), 'positive': sum(r['balance'] for r in positives), 'pending_rows': len(positives),
              'negative': sum(r['balance'] for r in rows if (r.get('balance') or 0) < 0),
              'negative_rows': sum((r.get('balance') or 0) < 0 for r in rows),
              'net_recorded': sum(r.get('balance') or 0 for r in rows), 'blank_balances': sum(r.get('balance') is None for r in rows),
              'missing_folio': sum(r.get('folio') is None for r in rows), 'short_folio': sum(r['folio_review'] for r in rows), 'missing_due': sum(not r.get('due_text') for r in rows)}
    return {'available': True, 'batch': {k: batch[k] for k in ('id', 'source_id', 'source_title', 'source_modified', 'imported_at', 'actor')},
            'rows': rows, 'form': snapshot['form'], 'suppliers': suppliers, 'issues': issues, 'history': history,
            'totals': totals, 'conditions': CONDITIONS, 'payment_executed': False}


def annotate(store, data, actor):
    identifier = request_id(data.get('request_id'))
    kind, target, condition, batch_id = (data.get(k) for k in ('target_kind', 'target', 'condition', 'batch_id'))
    if kind not in ('row', 'supplier') or not isinstance(target, str) or not isinstance(condition, str) or condition not in CONDITIONS or (kind == 'supplier' and condition == 'inherit'):
        raise DomainError('Selecciona una condición válida para el registro o proveedor.')
    if not isinstance(batch_id, str) or not re.fullmatch(r'[a-f0-9]{64}', batch_id) or type(data.get('version')) is not int or data['version'] < 0:
        raise DomainError('Falta la versión del registro.')
    note = concept(data.get('note'), 5, 1000)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM supplier_opening_annotations WHERE id=?', (identifier,)).fetchone()
        if existing:
            if tuple(existing[k] for k in ('batch_id', 'target_kind', 'target', 'condition', 'note', 'actor', 'version')) != (batch_id, kind, target, condition, note, actor, data['version'] + 1):
                raise DomainError('La solicitud ya se utilizó con otros datos.', 409)
            return dict(existing)
        if kind == 'row':
            valid = target.isascii() and target.isdecimal() and str(int(target)) == target and db.execute('SELECT 1 FROM supplier_opening_records WHERE batch_id=? AND source_row=?', (batch_id, target)).fetchone()
        else:
            valid = db.execute('SELECT 1 FROM supplier_opening_records WHERE batch_id=? AND supplier=?', (batch_id, target)).fetchone()
        if not valid:
            raise DomainError('Registro de origen no encontrado.', 404)
        current = db.execute('SELECT MAX(version) FROM supplier_opening_annotations WHERE batch_id=? AND target_kind=? AND target=?', (batch_id, kind, target)).fetchone()[0] or 0
        if current != data['version']:
            raise DomainError('Otro socio modificó este registro. Actualiza la vista antes de guardar.', 409)
        db.execute('INSERT INTO supplier_opening_annotations VALUES(?,?,?,?,?,?,?,?,?)',
                   (identifier, batch_id, kind, target, condition, note, current + 1, actor, stamp()))
        return dict(db.execute('SELECT * FROM supplier_opening_annotations WHERE id=?', (identifier,)).fetchone())
