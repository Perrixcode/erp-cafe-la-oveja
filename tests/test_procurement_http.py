"""Local HTTP authorization and procurement integration; fictional temporary data."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4

from app import make_server
from erp import payables as ap,procurement as pc
from erp.store import Store
from http_test_support import authenticated_opener


class ProcurementHTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        fixed=patch.object(ap,'local_day',return_value='2026-10-14');fixed.start();self.addCleanup(fixed.stop)
        self.server=make_server(self.root/'synthetic.sqlite3',0,seed=False);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root);self.store=Store(self.root/'synthetic.sqlite3')
        self.supplier=ap.execute(self.store,'supplier',dict(name='Proveedor HTTP ficticio',request_id=str(uuid4())),'QA')

    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def post(self,path,body,**headers):
        request=Request(self.url+'/api/suppliers/procurement'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers})
        with self.partner.open(request,timeout=3) as response:return json.load(response)

    def write(self,path,body):return self.post(path,dict(body,request_id=str(uuid4()),actor='forged-actor'))

    def get(self,path=''):
        with self.partner.open(self.url+'/api/suppliers/procurement'+path,timeout=3) as response:return json.load(response)

    def setup_order(self):
        unit=self.write('/units',dict(code='u',name='Unidad HTTP',dimension='count',precision=0))
        location=self.write('/warehouses',dict(name='Bodega HTTP ficticia'))
        item=self.write('/items',dict(code='HTTP-QA',name='Insumo HTTP ficticio',base_unit_id=unit['id']))
        order=self.write('/orders',dict(supplier_id=self.supplier['id'],supplier_version=1,warehouse_id=location['id'],ordered_on='2026-10-14',confirmed=True,lines=[dict(item_id=item['id'],version=1,unit_id=unit['id'],quantity='10')]))
        return unit,order

    def test_all_new_routes_keep_partner_only_access(self):
        paths=[('/orders/1/pdf',None),('/agenda/1/pdf',None),('',None),('/orders/1',None),('/orders/1/history',None),('/directory/1/history',None),('/units',{}),('/warehouses',{}),('/items',{}),('/equivalences',{}),('/orders',{}),('/orders/1/receipts',{}),('/orders/1/cancel',{}),('/orders/1/invoices',{}),('/orders/1/issues',{}),('/receipts/1/reverse',{}),('/receipts/1/warehouse-entries',{}),('/invoice-links/1/void',{}),('/issues/1/resolve',{}),('/directory/1',{}),('/directory-preview',{})]
        for role in (None,'cashier','production'):
            opener=authenticated_opener(self.server,self.root,role,role+'-qa') if role else None
            for path,body in paths:
                with self.subTest(role=role,path=path),self.assertRaises(HTTPError) as error:
                    request=Request(self.url+'/api/suppliers/procurement'+path,data=json.dumps(body).encode() if body is not None else None,headers={'X-ERP-Local':'1'})
                    (opener.open if opener else urlopen)(request,timeout=3)
                self.assertEqual(error.exception.code,403 if role else 401);error.exception.close()
        for path,body in paths:
            if body is None:continue
            with self.subTest(origin=path),self.assertRaises(HTTPError) as error:self.post(path,{},Origin='https://foreign.invalid')
            self.assertEqual(error.exception.code,403);error.exception.close()

    def test_create_receive_retry_history_and_reverse_use_authenticated_actor(self):
        unit,order=self.setup_order();path=f'/orders/{order["id"]}/receipts'
        body=dict(request_id=str(uuid4()),version=1,received_on='2026-10-14',reference='ENTREGA-HTTP-QA',confirmed=True,actor='forged',lines=[dict(order_line_id=order['lines'][0]['id'],unit_id=unit['id'],accepted='4',rejected='1',reason='Daño ficticio')])
        receipt=self.post(path,body);self.assertEqual(self.post(path,body),receipt)
        current=self.get('/orders/'+str(order['id']));self.assertEqual(current['actor'],'socio-http-qa');self.assertEqual(current['lines'][0]['pending'],'6')
        self.assertEqual(self.get()['movements'],[])
        line=current['receipts'][0]['lines'][0]
        self.write(f'/receipts/{receipt["id"]}/warehouse-entries',dict(version=1,order_version=current['version'],warehouse_id=current['warehouse_id'],occurred_on='2026-10-14',reason='Ingreso HTTP ficticio',confirmed=True,lines=[dict(receipt_line_id=line['id'],unit_id=unit['id'],quantity='2')]))
        current=self.get('/orders/'+str(order['id']))
        self.assertEqual(self.get()['movements'][0]['actor'],'socio-http-qa')
        self.assertTrue(any(r['action']=='receipt_confirmed' for r in self.get(f'/orders/{order["id"]}/history')['events']))
        self.write(f'/receipts/{receipt["id"]}/reverse',dict(version=2,order_version=current['version'],reason='Corrección HTTP ficticia'))
        data=self.get();self.assertEqual(data['balances'][0]['net'],'0');self.assertEqual(len(data['movements']),2)
        self.assertEqual(ap.view(self.store)['totals']['balance'],0)

    def test_directory_profile_and_preview_do_not_create_suppliers_or_alerts(self):
        self.write('/directory/'+str(self.supplier['id']),dict(supplier_version=1,profile_version=0,products_text='Insumos QA',reviewed=False,reason='Ficha ficticia',order_days=[0,2],delivery_days=[1,4]))
        result=self.get()['suppliers'];self.assertEqual(len(result),1);self.assertFalse(result[0]['profile']['alerts_enabled']);self.assertEqual(result[0]['tax_id'],'')
        preview=self.post('/directory-preview',{'rows':[{'supplier_name':'Proveedor desconocido QA'}]})
        self.assertEqual(preview['rows'][0]['status'],'unmatched');self.assertFalse(preview['automatic_import'])
        self.assertEqual(len(self.get()['suppliers']),1)
        history=self.get('/directory/'+str(self.supplier['id'])+'/history')['events'];self.assertEqual(history[0]['actor'],'socio-http-qa')

    def test_assets_and_sections_are_served(self):
        for path,expected in [('/image-attachment.js',b'OvejaImageAttachment'),('/design-system.css',b'--rail'),('/loading-state.js',b'OvejaLoading'),('/workspace.css',b'load-state--blocking'),('/procurement.js',b'OvejaProcurement'),('/procurement.css',b'pc-quantities'),('/app.js','Pedidos y recepción'.encode())]:
            with self.partner.open(self.url+path,timeout=3) as response:self.assertIn(expected,response.read())

    def test_extended_routes_remain_partner_only_and_same_origin(self):
        paths=['/stock','/stock/1/reverse','/prices','/prices/1/void','/policies','/agenda','/agenda/1/save','/agenda/1/order','/agenda/1/postpone','/agenda/1/done','/documents/1/order']
        for role in (None,'cashier','production'):
            opener=authenticated_opener(self.server,self.root,role,role+'-extended') if role else None
            for path in paths:
                with self.subTest(role=role,path=path),self.assertRaises(HTTPError) as error:
                    request=Request(self.url+'/api/suppliers/procurement'+path,data=b'{}',headers={'X-ERP-Local':'1'})
                    (opener.open if opener else urlopen)(request,timeout=3)
                self.assertEqual(error.exception.code,403 if role else 401);error.exception.close()
        for path in paths:
            with self.assertRaises(HTTPError) as error:self.post(path,{},Origin='https://foreign.invalid')
            self.assertEqual(error.exception.code,403);error.exception.close()

    def test_document_http_permissions_upload_review_and_private_original(self):
        from erp import document_intake
        paths=[('/documents/1/apply-reading',b'{}'),('/documents',None),('/documents',b'%PDF-1.4\nFAKE\n%%EOF'),('/documents/1',None),('/documents/1/file',None),('/documents/1/confirm',b'{}'),('/documents/1/discard',b'{}'),('/documents/1/save',b'{}'),('/documents/1/reextract',b'{}'),('/documents/1/map',b'{}')]
        for role in (None,'cashier','production'):
            opener=authenticated_opener(self.server,self.root,role,role+'-docs') if role else None
            for path,body in paths:
                with self.assertRaises(HTTPError) as error:
                    request=Request(self.url+'/api/suppliers'+path,data=body,headers={'X-ERP-Local':'1'})
                    (opener.open if opener else urlopen)(request,timeout=3)
                self.assertEqual(error.exception.code,403 if role else 401);error.exception.close()
        with patch.object(document_intake,'extract',return_value=('Folio: 801','extracted','Ficticio')):
            request=Request(self.url+'/api/suppliers/documents',data=b'%PDF-1.4\nFAKE\n%%EOF',headers={'X-ERP-Local':'1','X-File-Name':'ficticio.pdf'})
            with self.partner.open(request) as response:draft=json.load(response)
        body=dict(version=1,reason='Revisión ficticia',confirmed=True,request_id=str(uuid4()),actor='forged',invoice=dict(supplier_id=self.supplier['id'],document_type='invoice',folio='801',issued_on='2026-10-14',amount=1000,condition='credit'))
        request=Request(self.url+'/api/suppliers/documents/'+str(draft['id'])+'/confirm',data=json.dumps(body).encode(),headers={'X-ERP-Local':'1','Content-Type':'application/json'})
        with self.partner.open(request) as response:result=json.load(response)
        with self.partner.open(self.url+'/api/suppliers/documents/'+str(draft['id'])) as response:self.assertEqual(json.load(response)['reviewed_by'],'socio-http-qa')
        with self.partner.open(self.url+'/api/suppliers/documents/'+str(draft['id'])+'/file') as response:self.assertIn(b'FAKE',response.read())
        self.assertEqual(len(ap.view(self.store)['invoices']),1)

    def test_pdf_download_is_private_and_does_not_change_order(self):
        unit,order=self.setup_order()
        before=self.get('/orders/'+str(order['id']))
        with self.partner.open(self.url+'/api/suppliers/procurement/orders/'+str(order['id'])+'/pdf') as response:
            self.assertEqual(response.headers['Content-Type'],'application/pdf')
            self.assertIn('attachment;',response.headers['Content-Disposition'])
            self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertTrue(response.read().startswith(b'%PDF-'))
        self.assertEqual(before,self.get('/orders/'+str(order['id'])))
