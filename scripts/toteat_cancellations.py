"""Revisión acotada GET de identidades ya observadas; ausencia nunca implica anulación."""
from datetime import datetime, timezone
from erp import reception
from erp.store import Store
from erp.toteat_inbox import read_inbox, stamp
from scripts.probe_toteat_comments import read_helper, ProbeFailure
from scripts.toteat_sales_worker import configuration, ROOT


def cancellation_cycle(inbox,root=ROOT,reader=read_helper,limit=3):
    config=configuration(root)
    if not config:return
    state=read_inbox(inbox.path)
    prior=state.get('cancellation_reader',{})
    if prior.get('retry_at') and datetime.now(timezone.utc)<datetime.fromisoformat(prior['retry_at']):return
    rows=sorted(state.get('orders',[]),key=lambda row:row['key'])
    if not rows:return
    store=Store(root/'data/erp-demo.sqlite3');scope=config['scope'];index=prior.get('cursor',0)%len(rows)
    checked=0;errors=0
    for offset in range(min(limit,len(rows))):
        row=rows[(index+offset)%len(rows)]
        if (row['restaurant_id'],row['local_id'])!=(scope['restaurant_id'],scope['local_id']):continue
        try:
            detail=reader(root/config['binary'],['detail',row['order_id']],scope)['data']
            if not isinstance(detail,dict) or str(detail.get('orderId'))!=row['order_id']:raise ValueError('source_identity_mismatch')
            reception.inspect_cancellation(store,scope,detail);checked+=1
        except (ProbeFailure,ValueError,KeyError):errors+=1
    inbox.set_sales_state('__cancellations__',{'state':'receiving' if not errors else 'review_required','last_success':stamp() if checked else prior.get('last_success'), 'checked':checked,'errors':errors,'cursor':(index+min(limit,len(rows)))%len(rows),'known_orders':len(rows),'interval_seconds':90,'coverage':'known_orders_only'})
