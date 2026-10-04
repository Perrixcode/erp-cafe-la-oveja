"""Pendientes de pedidos agendados. No infiere pago, producción ni entrega."""
from datetime import datetime, timedelta, timezone


def attention_summary(store,reader,scope,today,now=None):
    now=now or datetime.now(timezone.utc)
    end=today+timedelta(days=6)
    rows=[]
    counts={'upcoming':0,'overdue':0,'unmarked':0,'unpaid':0,'incomplete':0}
    if scope != 'archived':
        for order in store.list('0001-01-01','9999-12-31'):
            if scope=='operations' and order['is_simulation']:continue
            if scope=='tests' and not order['is_simulation']:continue
            active=[item for item in order['items'] if item['status'] not in {'entregado','cancelado'}]
            if not active or order['delivery_timing']=='immediate':continue
            day=order['pickup_at'].split('T')[0];issues=[]
            if today.isoformat()<=day<=end.isoformat():issues.append('upcoming')
            if day<today.isoformat():issues.append('overdue')
            if day<=end.isoformat() and any(item['status'] in {'pendiente','marcado_solicitado'} for item in active):issues.append('unmarked')
            if (order.get('manual_scheduling') or {}).get('payment_status')=='unpaid':issues.append('unpaid')
            if not order.get('customer_phone') or (order['fulfillment']=='despacho' and not order.get('delivery_address')):issues.append('incomplete')
            if issues:
                for issue in issues:counts[issue]+=1
                rows.append({'id':order['id'],'customer':order['customer'],'pickup_at':order['pickup_at'],'issues':issues,'is_simulation':order['is_simulation']})
    alerts=[]
    if store.operating_mode()=='toteat-local':
        for source,label,seconds in [(reader,'comandas abiertas',120),(reader.get('sales_reader') or {},'ventas cerradas',240)]:
            try:age=(now-datetime.fromisoformat((source.get('last_success') or '').replace('Z','+00:00'))).total_seconds()
            except (ValueError,TypeError):age=float('inf')
            if age>seconds or source.get('state')!='receiving':
                alerts.append('Lectura de '+label+' sin actualización reciente. Los datos conservados pueden estar incompletos.')
    rows.sort(key=lambda row:(row['pickup_at'],row['id']))
    return {'counts':counts,'orders':rows,'start':today.isoformat(),'end':end.isoformat(),'reader_alerts':alerts}
