import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4

from app import make_server
from erp.store import Store
from http_test_support import authenticated_opener


class CashBagsHTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.server=make_server(self.root/'synthetic.sqlite3',0,seed=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.partner=authenticated_opener(self.server,self.root)
        self.cashier=authenticated_opener(self.server,self.root,'cashier','caja-ficticia')
        self.production=authenticated_opener(self.server,self.root,'production','produccion-ficticia')
        self.store=Store(self.root/'synthetic.sqlite3')

    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def post(self,path,data,opener=None,**headers):
        req=Request(self.url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers})
        with (opener or self.partner).open(req,timeout=10) as response:return json.load(response)

    def create(self):
        result=self.post('/api/cash-bags/batches',dict(request_id=uuid4().hex,quantity=4,confirmed=True))
        with self.partner.open(self.url+'/api/cash-bags/batches/'+result['batch_id']) as response:return json.load(response)

    def test_auth_roles_and_read_only_scan(self):
        batch=self.create();bag=batch['bags'][0]
        with self.store.connect() as db:before=list(db.iterdump())
        for path in ['/api/cash-bags','/api/cash-bags/'+bag['id'],'/api/cash-bags/batches/'+batch['id']+'/labels.pdf']:
            for opener,status in [(None,401),(self.production,403)]:
                with self.assertRaises(HTTPError) as err:(opener.open if opener else urlopen)(self.url+path)
                self.assertEqual(err.exception.code,status);err.exception.close()
            with self.cashier.open(self.url+path) as response:self.assertEqual(response.status,200)
        with urlopen(self.url+bag['path']) as response:
            html=response.read().decode();self.assertIn('auth-locked',html);self.assertNotIn('BOL-000001',html)
        with self.store.connect() as db:self.assertEqual(before,list(db.iterdump()))

    def test_writes_require_confirmation_origin_and_existing_role(self):
        body=dict(request_id=uuid4().hex,quantity=4,confirmed=True)
        for opener,headers,status in [(self.cashier,{},403),(self.production,{},403),(self.partner,{'Origin':'https://foreign.invalid'},403),(self.partner,{'X-ERP-Local':'0'},403),(self.partner,{'Content-Type':'text/plain'},415)]:
            with self.assertRaises(HTTPError) as err:self.post('/api/cash-bags/batches',body,opener,**headers)
            self.assertEqual(err.exception.code,status);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.post('/api/cash-bags/batches',{**body,'confirmed':False})
        self.assertEqual(err.exception.code,400);err.exception.close()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0],0)

    def test_cashier_delivery_and_partner_verification_are_separate(self):
        bag=self.create()['bags'][0];path='/api/cash-bags/'+bag['id']
        body=dict(request_id=uuid4().hex,version=1,confirmed=True,business_date='2026-10-15',register_name='Caja QA',shift_name='Turno QA',responsible='Persona QA',counted=67300,retained=36800)
        declared=self.post(path+'/complete',body,self.cashier)
        self.assertEqual(declared['status'],'pending');self.assertEqual(declared['declared_by'],'caja-ficticia')
        verify=dict(request_id=uuid4().hex,confirmed=True,version=2,verified_amount=30500,note='')
        with self.assertRaises(HTTPError) as err:self.post(path+'/verify',verify,self.cashier)
        self.assertEqual(err.exception.code,403);err.exception.close()
        result=self.post(path+'/verify',verify)
        self.assertEqual(result['status'],'verified');self.assertEqual(result['verified_by'],'socio-http-qa')

    def test_pdf_inline_first_and_download_same_bytes(self):
        batch=self.create();path='/api/cash-bags/batches/'+batch['id']+'/labels.pdf'
        with self.partner.open(self.url+path) as response:
            inline=response.read();self.assertTrue(inline.startswith(b'%PDF-'));self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'))
        with self.partner.open(self.url+path+'?download=1') as response:
            self.assertEqual(response.read(),inline);self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))

    def test_unknown_paths_and_ids_do_not_expose_data(self):
        for path in ['/api/cash-bags/'+'0'*32,'/api/cash-bags/batches/'+'0'*32,'/api/cash-bags/not-an-id']:
            with self.assertRaises(HTTPError) as err:self.partner.open(self.url+path)
            self.assertEqual(err.exception.code,404);err.exception.close()


if __name__=='__main__':unittest.main()
