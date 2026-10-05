"""Tarifas de reparto de Toteat. Día operativo de /sales; nunca ejecuta pagos."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo
from erp.domain import DomainError

START = date(2026, 9, 1)
PRODUCT_ID = 'TOTEATDVYCOST'
TZ = ZoneInfo('America/Santiago')


def now(): return datetime.now(timezone.utc).isoformat()
def today(): return datetime.now(TZ).date()
def encoded(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def money(value):
    if isinstance(value, bool) or value is None: raise DomainError('Toteat no informó un importe válido.')
    try: number = Decimal(str(value))
    except InvalidOperation: raise DomainError('Importe no válido.') from None
    if not number.is_finite() or number != number.to_integral_value() or abs(number) > 10**11: raise DomainError('El importe debe ser pesos CLP enteros.')
    return int(number)


def identity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value) or len(str(value)) > 100: raise DomainError('Falta identidad de la operación Toteat.')
    return str(value)


def extract(payload, scope):
    if not isinstance(payload, dict) or payload.get('ok') is not True or not isinstance(payload.get('data'), list): raise DomainError('Toteat no confirmó una lectura completa.')
    restaurant, local = identity(scope.get('restaurant_id')), identity(scope.get('local_id'))
    results = {}
    for row in payload['data']:
        if not isinstance(row, dict): raise DomainError('Respuesta de venta inválida.')
        lines = row.get('products', [])
        if not isinstance(lines, list): raise DomainError('Detalle de productos incompleto.')
        costs = [p for p in lines if isinstance(p, dict) and p.get('id') == PRODUCT_ID]
        if not costs: continue
        order_id, payment_id = identity(row.get('orderId')), identity(row.get('paymentId'))
        normalized = {}; warnings = []
        for i, p in enumerate(costs):
            line_id = identity(p.get('lineId', p.get('lineId*', 'delivery-cost' if len(costs) == 1 else None)))
            quantity = p.get('quantity')
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0: raise DomainError('Cantidad de reparto no válida.')
            value = {'line_id': line_id, 'paid': money(p.get('payed')), 'gross': money(p.get('netPrice')), 'discount': money(p.get('discounts', 0)), 'quantity': quantity}
            if line_id in normalized and normalized[line_id] != value: raise DomainError('La misma línea contiene importes distintos.')
            normalized[line_id] = value
            if value['discount'] or value['paid'] != value['gross']: warnings.append('tarifa_modificada')
            if value['paid'] < 0 or str(row.get('fiscalType', '')).upper() == 'NC': warnings.append('devolucion_requiere_revision')
        for key in ('dateOpen', 'dateClosed'):
            try: datetime.fromisoformat(row[key])
            except (KeyError, TypeError, ValueError): raise DomainError('Falta la fecha de la venta.') from None
        value = {'restaurant_id': restaurant, 'local_id': local, 'order_id': order_id, 'payment_id': payment_id,
                 'opened_at': row['dateOpen'], 'closed_at': row['dateClosed'], 'fee': sum(v['paid'] for v in normalized.values()),
                 'sale_paid': money(row.get('payed')), 'lines': list(normalized.values()), 'warnings': sorted(set(warnings)),
                 'fiscal_type': str(row.get('fiscalType', '')), 'courier_id': None}
        if value['sale_paid'] < 0: value['warnings'] = sorted(set(value['warnings'] + ['devolucion_requiere_revision']))
        key = encoded([restaurant, local, payment_id])
        if key in results and results[key] != value: raise DomainError('El pago aparece con información contradictoria.')
        results[key] = value
    return results


def request_refresh(store, actor, current_day=None, automatic=False):
    day = current_day or today()
    if day < START: raise DomainError('El histórico comienza el 1 de septiembre de 2026.')
    stamp = now()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR IGNORE INTO metadata VALUES('delivery_tracking_enabled','1')")
        added = 0
        for offset in range((day - START).days + 1):
            added += db.execute('INSERT OR IGNORE INTO delivery_days(day,state,requested_at) VALUES(?,?,?)', (str(START + timedelta(days=offset)), 'pending', stamp)).rowcount
        previous = db.execute("SELECT value FROM metadata WHERE key='delivery_daily_requested'").fetchone()
        pending = db.execute("SELECT COUNT(*) FROM delivery_days WHERE state='pending'").fetchone()[0]
        if not added and (pending or automatic and previous and previous[0] == str(day)):
            return {'queued': bool(pending), 'pending_days': pending, 'coalesced': True}
        # El primer lote también registra el día: al terminar no debe encolarse
        # otra vez en cada ciclo del lector. Un cambio de día refresca el lote.
        if not pending or automatic and (not previous or previous[0] != str(day)):
            db.execute("UPDATE delivery_days SET state='pending',requested_at=? WHERE day<=?", (stamp, str(day)))
        db.execute("INSERT INTO metadata VALUES('delivery_daily_requested',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(day),))
        db.execute('INSERT INTO delivery_sync_requests(actor,occurred_at,kind) VALUES(?,?,?)', (actor, stamp, 'daily' if automatic else 'consultation'))
        return {'queued': True, 'pending_days': (day - START).days + 1, 'coalesced': False}


def enabled(store):
    with store.connect() as db: return bool(db.execute("SELECT 1 FROM metadata WHERE key='delivery_tracking_enabled' AND value='1'").fetchone())


def apply_day(store, day, payload, scope, observed_at=None):
    selected = date.fromisoformat(day)
    if selected < START or selected > today(): raise DomainError('Día de lectura fuera del histórico.')
    rows = extract(payload, scope); stamp = now()
    observed_at = observed_at or stamp
    try:
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None or observed > datetime.now(timezone.utc): raise ValueError()
    except (TypeError, ValueError): raise DomainError('Fecha de consulta no válida.') from None
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous_rows = {r['source_key']: dict(r) for r in db.execute('SELECT * FROM delivery_transactions WHERE source_day=?', (day,))}
        for key, value in rows.items():
            old = db.execute('SELECT * FROM delivery_transactions WHERE source_key=?', (key,)).fetchone()
            if old and old['source_day'] != day: raise DomainError('El mismo pago apareció en dos días operativos. Revisar el turno antes de sumar.')
            body = encoded(value); digest = hashlib.sha256(body.encode()).hexdigest()
            if not old or old['digest'] != digest or old['source_missing']:
                db.execute('INSERT INTO delivery_finance_history(source_key,occurred_at,before_json,after_json) VALUES(?,?,?,?)', (key, stamp, old['payload_json'] if old else None, body))
            db.execute('''INSERT INTO delivery_transactions(source_key,source_day,order_id,payment_id,fee,sale_paid,payload_json,digest,last_seen,source_missing)
                VALUES(?,?,?,?,?,?,?,?,?,0) ON CONFLICT(source_key) DO UPDATE SET fee=excluded.fee,sale_paid=excluded.sale_paid,payload_json=excluded.payload_json,digest=excluded.digest,last_seen=excluded.last_seen,source_missing=0''',
                (key, day, value['order_id'], value['payment_id'], value['fee'], value['sale_paid'], body, digest, observed_at))
        for key, old in previous_rows.items():
            if key not in rows and not old['source_missing']:
                db.execute('UPDATE delivery_transactions SET source_missing=1 WHERE source_key=?', (key,))
                db.execute('INSERT INTO delivery_finance_history(source_key,occurred_at,before_json,after_json) VALUES(?,?,?,?)', (key, stamp, old['payload_json'], encoded({'source_missing': True, 'previous_amount_preserved': True})))
        db.execute('''INSERT INTO delivery_days(day,state,requested_at,last_success,error) VALUES(?,'complete',?,?,'')
            ON CONFLICT(day) DO UPDATE SET state='complete',last_success=excluded.last_success,error='' ''', (day, stamp, observed_at))
    return len(rows)


def fail_day(store, day, error):
    with store.connect() as db:
        db.execute("UPDATE delivery_days SET state='error',error=?,last_attempt=? WHERE day=?", (error[:200], now(), day))


def view(store, month=None):
    current = today()
    month = month or current.strftime('%Y-%m')
    try:
        start = date.fromisoformat(month + '-01')
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if start.strftime('%Y-%m') != month or end < START or start > current: raise ValueError()
    except (ValueError, TypeError): raise DomainError('Selecciona un mes desde septiembre de 2026.') from None
    start, end = max(start, START), min(end, current)
    with store.connect() as db:
        days = {r['day']: dict(r) for r in db.execute('SELECT * FROM delivery_days WHERE day BETWEEN ? AND ?', (str(start), str(end)))}
        transactions = [dict(r) for r in db.execute('SELECT * FROM delivery_transactions WHERE source_day BETWEEN ? AND ? ORDER BY source_day DESC,source_key', (str(start), str(end)))]
        couriers = []; assignments = {}  # Liquidación individual se conectará en su módulo.
        latest = db.execute('SELECT MAX(last_success) FROM delivery_days').fetchone()[0]
        progress = dict(db.execute("SELECT COUNT(*) total,SUM(state='pending') pending,SUM(state='error') errors FROM delivery_days").fetchone())
        requests = [dict(r) for r in db.execute('SELECT actor,occurred_at,kind FROM delivery_sync_requests ORDER BY id DESC LIMIT 5')]
    def scoped_order(row):
        value = json.loads(row['payload_json'])
        return (value['restaurant_id'], value['local_id'], row['order_id'])
    order_counts = Counter(scoped_order(r) for r in transactions)
    daily = []; by_courier = {}
    for offset in range((end - start).days + 1):
        day = str(start + timedelta(days=offset)); meta = days.get(day, {})
        rows = [r for r in transactions if r['source_day'] == day]
        fees = collected = review = assigned = 0
        details = []
        for row in rows:
            value = json.loads(row['payload_json']); warnings = list(value['warnings'])
            if row['source_missing']: warnings.append('ausente_en_ultima_lectura')
            if order_counts[scoped_order(row)] > 1: warnings.append('varios_pagos_revisar_tarifa')
            assignment = assignments.get(row['source_key'])
            fees += row['fee']; collected += row['sale_paid']; review += bool(warnings)
            if assignment and not warnings:
                assigned += row['fee']
                person = by_courier.setdefault(assignment['courier_id'], {'id': assignment['courier_id'], 'name': assignment['name'], 'kind': assignment['kind'], 'amount': 0, 'deliveries': 0})
                person['amount'] += row['fee']; person['deliveries'] += 1
            details.append({'order_id': row['order_id'], 'payment_id': row['payment_id'], 'fee': row['fee'], 'sale_paid': row['sale_paid'],
                            'courier': assignment['name'] if assignment else None, 'warnings': sorted(set(warnings)),
                            'opened_at': value['opened_at'], 'closed_at': value['closed_at']})
        known = bool(meta.get('last_success'))
        daily.append({'day': day, 'state': meta.get('state', 'not_loaded'), 'last_success': meta.get('last_success'), 'error': meta.get('error', ''),
                      'fee': fees if known else None, 'sale_paid': collected if known else None, 'records': len(rows), 'review_count': review,
                      'assigned_amount': assigned, 'unassigned_amount': fees - assigned if known else None, 'details': details})
    return {'month': month, 'start': str(start), 'end': str(end), 'history_start': str(START), 'timezone': str(TZ), 'date_basis': 'toteat_shift_day',
            'source': {'endpoint': 'sales', 'product_id': PRODUCT_ID, 'product_name': 'Costo Delivery', 'amount_field': 'products[].payed'},
            'days': daily, 'last_sync': latest, 'progress': progress, 'refresh_requests': requests, 'couriers': couriers,
            'individual': list(by_courier.values()), 'team_reference': {'habitual': 2, 'replacement': 2},
            'totals': {'fee': sum(d['fee'] or 0 for d in daily), 'sale_paid': sum(d['sale_paid'] or 0 for d in daily), 'assigned_amount': sum(d['assigned_amount'] for d in daily),
                       'unassigned_amount': sum(d['unassigned_amount'] or 0 for d in daily), 'review_count': sum(d['review_count'] for d in daily),
                       'known_days': sum(d['last_success'] is not None for d in daily), 'total_days': len(daily), 'records': sum(d['records'] for d in daily)},
            'payment_executed': False, 'courier_collections_reconciled': False}
