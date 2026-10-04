"""Una sola identidad de producto: entrega inmediata o programada explícita."""
import tempfile
import unittest
from pathlib import Path
from erp.catalog import production_plan
from erp.domain import DomainError, summarize
from erp.store import Store
from test_erp import sample


class DeliveryTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'timing.sqlite3')

    def test_immediate_sale_delivered_without_mark_or_production_or_stock(self):
        data=sample();data.update(delivery_timing='immediate',customer_phone='')
        order=self.store.create(data,'Demo')
        self.assertTrue(all(i['status']=='entregado' for i in order['items']))
        self.assertEqual(summarize([order])['outstanding'],0)
        self.assertEqual(production_plan([order],self.store.catalog())['totals'],[])
        self.assertEqual(self.store.notifications(),[]);self.assertEqual(self.store.stock(),[])
        history=self.store.history(order['id'])
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['after']['items'][0]['status'],'entregado')
        self.assertEqual(self.store.analytics([order])['on_time_total'],0)
        self.assertEqual(Store(self.store.path).get(order['id']),order)

    def test_scheduled_waits_and_same_product_is_not_duplicated(self):
        data=sample(); scheduled=self.store.create(data,'Demo')
        data.update(source_id='DEMO-INSTANT',delivery_timing='immediate')
        immediate=self.store.create(data,'Demo')
        self.assertEqual(scheduled['items'][0]['sku'],immediate['items'][0]['sku'])
        self.assertEqual(scheduled['items'][0]['status'],'pendiente')
        self.assertEqual(summarize([scheduled,immediate])['outstanding'],1)

    def test_new_order_requires_explicit_timing_and_scheduled_phone(self):
        for timing in (None,'','unclassified','automatic'):
            data=sample();data['delivery_timing']=timing
            with self.assertRaises(DomainError):self.store.create(data,'Demo')
        data=sample();data['customer_phone']=''
        with self.assertRaises(DomainError):self.store.create(data,'Demo')

    def test_unpaid_needs_separate_authorization_flow_not_autoapproved(self):
        for timing in ('scheduled','immediate'):
            data=sample();data.update(delivery_timing=timing,payment_confirmed=False)
            with self.assertRaises(DomainError):self.store.create(data,'Demo')

    def test_legacy_order_not_reinterpreted_from_comment_or_date(self):
        order=self.store.create(sample(),'Demo')
        with self.store.connect() as db:db.execute('DELETE FROM order_scheduling WHERE order_id=?',(order['id'],))
        before=self.store.history(order['id'])
        reopened=Store(self.store.path)
        self.assertEqual(reopened.get(order['id'])['delivery_timing'],'unclassified')
        self.assertEqual(reopened.history(order['id']),before)
        self.assertEqual(reopened.get(order['id'])['items'][0]['status'],'pendiente')

    def test_reclassifying_pending_as_immediate_cannot_silently_deliver(self):
        data=sample();order=self.store.create(data,'Demo');before=self.store.history(order['id'])
        data['delivery_timing']='immediate'
        with self.assertRaises(DomainError):self.store.update(order['id'],data,'Demo','Cambiar tipo',order['version'])
        self.assertEqual(self.store.get(order['id']),order);self.assertEqual(self.store.history(order['id']),before)

    def test_duplicate_immediate_sale_cannot_enter_twice(self):
        data=sample();data['delivery_timing']='immediate'
        order=self.store.create(data,'Demo')
        with self.assertRaises(DomainError):self.store.create(data,'Demo')
        self.assertEqual(len(self.store.history(order['id'])),1)


if __name__=='__main__':unittest.main()
