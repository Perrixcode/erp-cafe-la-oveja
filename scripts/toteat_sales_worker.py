"""Consulta automática GET de ventas del turno; dedupe, comentarios y boleta privada."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from erp.store import Store
from erp import reception
from erp.toteat_comments_probe import inspect_sales
from erp.toteat_inbox import canonical, stamp
from erp.toteat_receipts import download_receipt
from scripts.probe_toteat_comments import read_helper, ProbeFailure

ROOT = Path(os.environ.get('OVEJA_DATA_ROOT',Path(__file__).resolve().parents[1]))


def configuration(root):
    path = root/'private/toteat-sales-config.json'
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    if config.get('version') != 1 or config.get('enabled') is not True:
        return None
    systemd=config.get('transport')=='systemd' and config.get('binary')=='systemd-toteat' and bool(os.environ.get('CREDENTIALS_DIRECTORY'))
    if not systemd and config.get('binary') not in ('private/oveja-toteat-diagnostics','private/oveja-toteat-automation'):
        raise ValueError('unapproved_sales_reader')
    if not systemd and hashlib.sha256((root/config['binary']).read_bytes()).hexdigest() != config['binary_sha256']:
        raise ValueError('sales_reader_changed')
    date.fromisoformat(config['shift_start'])
    datetime.fromisoformat(config['start_after'])
    if not all(isinstance(config.get('scope',{}).get(k),str) for k in ('restaurant_id','local_id')):
        raise ValueError('missing_sales_scope')
    return config


def sales_cycle(inbox, root=ROOT, reader=read_helper, downloader=download_receipt, current_day=None):
    config = configuration(root)
    if not config:
        return
    from erp.toteat_inbox import read_inbox
    prior = read_inbox(inbox.path,False).get('sales_reader',{})
    if prior.get('retry_at') and datetime.now(timezone.utc) < datetime.fromisoformat(prior['retry_at']):
        return
    current = dict(config['scope']);binary = root/config['binary']
    shift = prior.get('verified_sales_day',config['shift_start'])
    safe = {'state':'receiving','checked_at':stamp(),'automatic_scheduling':True,'shift_start':shift,
            'shift_lookup_automatic':config.get('automatic_shift_lookup') is True,'interval_seconds':90}
    try:
        if config.get('automatic_shift_lookup'):
            response = reader(binary,['shift-status'],current)['data']
            if not isinstance(response,dict) or any(str(response.get(k)) != current[c] for k,c in [('restaurantId','restaurant_id'),('localNumber','local_id')]):
                raise ValueError('shift_scope_mismatch')
            # En la respuesta real date avanzó con el reloj de consulta; no es
            # una fecha de apertura acreditada. Nunca reemplaza el día de ventas.
            safe['shift_status']=str(response.get('status','unknown'))
            safe['shift_status_reported_at']=str(response.get('date',''))
        day_now=current_day or datetime.now(ZoneInfo('America/Santiago')).date()
        confirmed=date.fromisoformat(shift)
        floor=max(date.fromisoformat(config['shift_start']),confirmed-timedelta(days=1),day_now-timedelta(days=13))
        alternatives=sorted({(floor+timedelta(days=n)).isoformat() for n in range(max(0,(day_now-floor).days+1))}-{confirmed.isoformat()},reverse=True)
        cursor=prior.get('discovery_cursor',0)
        shifts=sorted({shift,alternatives[cursor%len(alternatives)]}) if config.get('automatic_shift_lookup') and alternatives else [shift]
        safe['discovery_cursor']=(cursor+1)%len(alternatives) if alternatives else 0
        safe['queried_sales_days']=shifts
        safe['verified_sales_day']=shift
        store = Store(root/'data/erp-demo.sqlite3');catalog = store.catalog()
        source_catalog = [p['source_product'] for p in catalog if p.get('source_product')]
        transactions = {}; returned = 0
        for day in shifts:
            start = date.fromisoformat(day)
            payload = reader(binary,['sales-one-day',start.strftime('%Y%m%d')],current)
            if not isinstance(payload.get('data'),list):
                raise ValueError('invalid_sales_rows')
            returned += len(payload['data'])
            if payload['data'] and day>safe['verified_sales_day']:
                safe['verified_sales_day']=day
                safe['shift_start']=day
            for row in payload['data']:
                key = (str(row.get('orderId')),str(row.get('paymentId')))
                if key in transactions and canonical(transactions[key]) != canonical(row):
                    raise ValueError('conflicting_sale_identity')
                transactions[key] = row
        _, candidates = inspect_sales({'ok':True,'data':list(transactions.values())},source_catalog,'not-an-order')
        # Financial cancellations can omit products. Inspect only identities already received.
        known={o['key'] for o in read_inbox(inbox.path).get('orders',[])}
        for transaction in transactions.values():
            key=reception.source_key(current,transaction.get('orderId'))
            if key in known and (str(transaction.get('fiscalType','')).upper()=='NC' or transaction.get('referencedPayment') or transaction.get('referencedPayment*')):
                reception.observe_sale(store,current,transaction)
        counts = Counter(c['order_id'] for c in candidates)
        scheduled = reviewed = receipts = 0
        for candidate in candidates:
            source_id = candidate['order_id']
            key = canonical([current['restaurant_id'],current['local_id'],source_id])
            try:
                closed = candidate.get('date_closed')
                if not isinstance(closed,str) or not closed:
                    continue
                moment = datetime.fromisoformat(closed)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                if source_id not in config.get('include_order_ids',[]) and moment < datetime.fromisoformat(config['start_after']):
                    continue
                key = inbox.receive_sale(current,candidate,catalog)
                row = transactions[(source_id,candidate['payment_id'])]
                reception.observe_sale(store,current,row,candidate)
                if counts[source_id] != 1:
                    raise ValueError('multiple_payments_require_review')
                row = transactions[(source_id,candidate['payment_id'])]
                order = store.import_toteat_schedule(current,row,candidate,is_test=source_id in config.get('test_order_ids',[]))
                if row.get('fiscalId') not in (None,0,'0','') and order['receipt'].get('status') != 'verified':
                    detail = reader(binary,['detail',source_id],current)['data']
                    if str(detail.get('orderId')) != source_id:
                        raise ValueError('receipt_order_mismatch')
                    payments = detail.get('document',{}).get('payments',[])
                    matching = [p for p in payments if str(p.get('id'))==candidate['payment_id'] and p.get('urlDTE') and p.get('amount')==row.get('total') and p.get('amountPaid')==row.get('payed')]
                    urls = {p['urlDTE'] for p in matching}
                    if len(urls)==1:
                        try:
                            receipt = downloader(next(iter(urls)),root/'private/receipts')
                            order = store.import_toteat_schedule(current,row,candidate,is_test=source_id in config.get('test_order_ids',[]),receipt=receipt)
                            receipts += 1
                        except (OSError,ValueError):
                            # El archivo no bloquea un pedido ya saldado; se vuelve a intentar.
                            pass
                inbox.set_sales_state(key,{'status':'scheduled','order_id':order['id'],'customer':order['customer'],
                    'customer_phone':order['customer_phone'],'pickup_at':order['pickup_at'],
                    'payment_state':order['toteat_schedule']['payment']['state'],
                    'warnings':order['toteat_schedule']['warnings'],'is_test':order['is_simulation'],
                    'receipt':order['receipt'],'observed_at':stamp()})
                scheduled += 1
            except ValueError as error:
                reason = str(error).split(':')[0]
                allowed = {'source_cancelled','source_alert_requires_review','classified_immediate','manual_review_required','multiple_payments_require_review','comment_requires_review','payment_not_settled','refund_requires_review',
                           'balance_requires_review','changed_order_already_in_progress','changed_product_lines_require_review',
                           'invalid_source_item','invalid_source_quantity','missing_source_items','receipt_order_mismatch','missing_order_comment','possible_sos_duplicate','sos_product_mismatch','sos_test_scope_mismatch'}
                inbox.set_sales_state(key,{'status':'needs_review','reason':reason if reason in allowed else 'source_requires_review','observed_at':stamp()})
                reviewed += 1
        safe.update(last_success=stamp(),transactions_returned=returned,scheduled=scheduled,reviewed=reviewed,receipts_downloaded=receipts)
        inbox.set_sales_state('__reader__',safe)
    except ProbeFailure as error:
        previous = prior.get('last_success')
        auth = error.code in {'keychain_access_required','keychain_unavailable'} or error.http_status in (400,401,403)
        config['enabled'] = False if auth else True
        if auth:
            path = root/'private/toteat-sales-config.json'
            path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
        inbox.set_sales_state('__reader__',dict(safe,state='access_required' if auth else 'retrying',error=error.code,http_status=error.http_status,
            last_success=previous,automatic_scheduling=False if auth else True,
            retry_at=(datetime.now(timezone.utc)+timedelta(seconds=max(300,getattr(error,'retry_after',0)))).isoformat()))
