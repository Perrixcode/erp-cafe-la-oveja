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


class CashControlHTTPTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.server=make_server(self.root/'test.sqlite3',0,seed=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.partner=authenticated_opener(self.server,self.root)
        self.cashier=authenticated_opener(self.server,self.root,'cashier','caja-http-qa')
        self.production=authenticated_opener(self.server,self.root,'production','produccion-http-qa')
        self.store=Store(self.root/'test.sqlite3')
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,body,opener=None,**headers):
        req=Request(self.url+'/api/cash-control/location',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers})
        with (opener or self.partner).open(req) as response:return json.load(response)
    def test_summary_roles_routes_and_get_no_mutation(self):
        with self.store.connect() as db:before=list(db.iterdump())
        for route in ['summary','closures','movements','balances','reconciliations']:
            target=self.url+'/api/cash-control/'+route+'?start=2026-10-16&end=2026-10-16'
            for opener,status in [(None,401),(self.cashier,403),(self.production,403)]:
                with self.assertRaises(HTTPError) as err:(opener.open if opener else urlopen)(target)
                self.assertEqual(err.exception.code,status);err.exception.close()
            with self.partner.open(target) as response:self.assertIsNone(json.load(response)['metrics']['expected']['value'])
        with self.store.connect() as db:self.assertEqual(list(db.iterdump()),before)
    def test_explicit_confirm_origin_and_idempotency(self):
        body=dict(request_id=uuid4().hex,version=1,confirmed=True,name='Caja QA',kind='register')
        for opener,headers,status in [(self.cashier,{},403),(self.partner,{'Origin':'https://foreign.invalid'},403),(self.partner,{'X-ERP-Local':'0'},403),(self.partner,{'Content-Type':'text/plain'},415)]:
            with self.assertRaises(HTTPError) as err:self.post(body,opener,**headers)
            self.assertEqual(err.exception.code,status);err.exception.close()
        one=self.post(body);self.assertEqual(one,self.post(body))
        with self.assertRaises(HTTPError) as err:self.post(dict(body,request_id=uuid4().hex,name='Otra'))
        self.assertEqual(err.exception.code,409);err.exception.close()
    def test_assets_existing_qr_routes_and_filters(self):
        for path,mime in [('/cash-control.js','javascript'),('/cash-control.css','css')]:
            with urlopen(self.url+path) as response:self.assertIn(mime,response.headers['Content-Type'])
        with self.partner.open(self.url+'/api/cash-bags') as response:self.assertEqual(json.load(response)['total'],0)
        for query in ['start=bad','start=2026-10-16&end=2026-10-15','unexpected=1']:
            with self.assertRaises(HTTPError) as err:self.partner.open(self.url+'/api/cash-control/summary?'+query)
            self.assertEqual(err.exception.code,400);err.exception.close()


if __name__=='__main__':unittest.main()
