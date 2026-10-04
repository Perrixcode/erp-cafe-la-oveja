from http_test_support import authenticated_opener
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from app import make_server
from erp.catalog_import import load_catalog
from erp.maintenance import remove_demo_data
from erp.store import Store
from erp.domain import DomainError
from test_erp import sample


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'local.sqlite3';self.store=Store(self.path)
        payload=load_catalog(Path(__file__).resolve().parents[1]/'fixtures/catalog_import_demo.json');payload['source']='toteat-manual'
        self.store.import_catalog(payload,'Fixture ficticio')
        remove_demo_data(self.store,Path(self.temp.name)/'backups')
        self.product=self.store.catalog()[0]
        self.server=make_server(self.path,port=0,seed=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.opener=authenticated_opener(self.server,self.path.parent)

    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def api(self,path,body=None):
        request=Request(self.url+path,data=None if body is None else json.dumps(body).encode(),headers={} if body is None else {'X-ERP-Local':'1','Content-Type':'application/json'},method='GET' if body is None else 'POST')
        with self.opener.open(request,timeout=3) as response:return json.load(response)

    def order(self):
        data=sample();p=self.product
        data['items']=[dict(source_item_id='1',sku=p['sku'],flavor=p['flavor'],size=p['size'],quantity=1,kind='torta',comments='')]
        return data

    def create(self):return self.api('/api/simulations',{'order':self.order(),'actor':'QA ficticio'})

    def test_simulation_separate_from_operations_stock_and_catalog(self):
        p=self.product;self.store.update_stock(dict(flavor=p['flavor'],size=p['size'],physical=5,reserved=2),'QA ficticio','Fixture',0)
        before=self.store.stock();catalog=self.store.catalog()
        order=self.create();self.assertTrue(order['is_simulation']);self.assertTrue(order['is_demo'])
        operations=self.api('/api/board?date=2026-10-05')
        tests=self.api('/api/board?scope=tests&date=2026-10-05')
        self.assertEqual(operations['orders'],[]);self.assertEqual(operations['analytics']['orders'],0)
        self.assertEqual(operations['stock']['available'],3)
        self.assertEqual(len(tests['orders']),1);self.assertEqual(tests['scope'],'tests');self.assertEqual(tests['stock']['rows'],[])
        self.assertEqual(self.store.stock(),before);self.assertEqual(self.store.catalog(),catalog)

    def test_request_notification_not_marked_until_confirmation(self):
        order=self.create();item=order['items'][0]['id']
        order=self.store.transition(order['id'],item,'marcado_solicitado','QA ficticio','Solicitud de prueba',order['version'])
        notices=self.api('/api/notifications');self.assertEqual(len(notices['notifications']),1)
        self.assertTrue(notices['notifications'][0]['is_simulation'])
        self.api('/api/orders/'+str(order['id'])+'/history')
        self.assertEqual(self.store.get(order['id'])['items'][0]['status'],'marcado_solicitado')
        order=self.store.transition(order['id'],item,'marcado','QA ficticio','Confirmación de prueba',order['version'])
        self.assertEqual(self.api('/api/notifications')['notifications'],[])
        order=self.store.transition(order['id'],item,'entregado','QA ficticio','Entrega de prueba',order['version'])
        self.assertEqual(order['items'][0]['status'],'entregado')

    def test_archive_and_restore_keep_history_and_hide_pending_alerts(self):
        order=self.create();item=order['items'][0]['id']
        order=self.store.transition(order['id'],item,'marcado_solicitado','QA ficticio','Aviso',order['version'])
        archived=self.api(f"/api/simulations/{order['id']}/archive",{'actor':'QA ficticio','version':order['version']})
        self.assertTrue(archived['simulation_archived']);self.assertEqual(self.api('/api/notifications')['notifications'],[])
        self.assertEqual(self.api('/api/board?scope=tests&date=2026-10-05')['orders'],[])
        self.assertEqual(len(self.api('/api/board?scope=archived&date=2026-10-05')['orders']),1)
        self.assertEqual(len(self.store.history(order['id'])),3)
        restored=self.api(f"/api/simulations/{order['id']}/restore",{'actor':'QA ficticio','version':archived['version']})
        self.assertFalse(restored['simulation_archived']);self.assertEqual(len(self.store.history(order['id'])),4)
        self.assertEqual(len(self.api('/api/notifications')['notifications']),1)

    def test_duplicate_and_normal_demo_route_cannot_repopulate(self):
        self.create()
        with self.assertRaises(HTTPError) as duplicate:self.create()
        self.assertEqual(duplicate.exception.code,409);duplicate.exception.close()
        with self.assertRaises(HTTPError) as denied:self.api('/api/orders',{'order':self.order(),'actor':'QA ficticio'})
        denied.exception.close()
        self.assertEqual(len(self.store.catalog()),4)
        self.assertEqual(self.api('/api/board?date=2026-10-05')['orders'],[])

    def test_immediate_simulation_enters_delivered_with_fake_contact(self):
        data=self.order();data.update(delivery_timing='immediate',customer_phone='')
        order=self.api('/api/simulations',{'order':data,'actor':'QA ficticio'})
        self.assertEqual(order['items'][0]['status'],'entregado')
        self.assertEqual(self.api('/api/notifications')['notifications'],[])
        self.assertEqual(self.api('/api/board?scope=tests&date=2026-10-05')['summary']['outstanding'],0)

    def test_edit_cannot_replace_simulation_contact_with_unmarked_data(self):
        order=self.create();history=self.store.history(order['id'])
        for field,value in [('customer','Nombre sin etiqueta de prueba'),('customer_phone','NO_ES_CERO')]:
            data=self.order();data[field]=value
            with self.assertRaises(DomainError):
                self.store.update(order['id'],data,'QA ficticio','Cambio de prueba',order['version'])
            self.assertEqual(self.store.get(order['id']),order)
            self.assertEqual(self.store.history(order['id']),history)


if __name__=='__main__':unittest.main()
