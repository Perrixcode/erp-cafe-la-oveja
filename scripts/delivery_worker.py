"""Una lectura histórica por ciclo, reutilizando credencial, lector y espera de Toteat."""
from datetime import datetime, timedelta, timezone
from erp import delivery_finance as finance
from erp.store import Store
from erp.toteat_inbox import read_inbox, stamp
from scripts.probe_toteat_comments import read_helper, ProbeFailure
from scripts.toteat_sales_worker import configuration, ROOT


def delivery_cycle(inbox, root=ROOT, reader=read_helper, current_day=None):
    store = Store(root/'data/erp-demo.sqlite3')
    if not finance.enabled(store): return
    from scripts.toteat_worker import cooldown_seconds
    if cooldown_seconds(read_inbox(inbox.path, False)): return
    current_day = current_day or finance.today()
    finance.request_refresh(store, 'Servidor', current_day, automatic=True)
    prior = read_inbox(inbox.path, False).get('delivery_reader', {})
    if prior.get('retry_at') and datetime.now(timezone.utc) < datetime.fromisoformat(prior['retry_at']): return
    config = configuration(root)
    if not config: return
    with store.connect() as db:
        pending = db.execute("SELECT day FROM delivery_days WHERE state='pending' AND day<=? ORDER BY (last_success IS NOT NULL),day ASC LIMIT 1", (str(current_day),)).fetchone()
    if not pending: return
    day = pending[0]
    try:
        payload = reader(root/config['binary'], ['sales-one-day', day.replace('-', '')], config['scope'])
        count = finance.apply_day(store, day, payload, config['scope'])
        inbox.set_sales_state('__delivery__', {'state': 'receiving', 'last_success': stamp(), 'source_day': day, 'records': count, 'interval_seconds': 90})
    except ProbeFailure as error:
        finance.fail_day(store, day, error.code)
        state = {'state': 'retrying', 'source_day': day, 'error': error.code, 'http_status': error.http_status, 'last_success': prior.get('last_success')}
        if error.http_status == 429:
            state['retry_at'] = (datetime.now(timezone.utc) + timedelta(seconds=max(300, getattr(error, 'retry_after', 300) or 0))).isoformat()
            with store.connect() as db: db.execute("UPDATE delivery_days SET state='pending' WHERE day=?", (day,))
        inbox.set_sales_state('__delivery__', state)
    except (ValueError, finance.DomainError):
        finance.fail_day(store, day, 'La respuesta requiere revisión; se conserva la lectura anterior.')
        inbox.set_sales_state('__delivery__', {'state': 'review_required', 'source_day': day, 'error': 'delivery_contract_requires_review', 'last_success': prior.get('last_success')})
