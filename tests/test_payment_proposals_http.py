"""Real session/origin checks and weekly planning HTTP in temporary SQLite."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from app import make_server
from erp import payables as ap
from erp.store import Store
from http_test_support import authenticated_opener


class PaymentProposalHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        fixed=patch.object(ap,'local_day',return_value='2026-10-14');fixed.start();self.addCleanup(fixed.stop)
        self.server=make_server(self.root/'synthetic.sqlite3',0,seed=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root)
        self.store=Store(self.root/'synthetic.sqlite3')
        supplier=ap.execute(self.store,'supplier',dict(request_id=str(uuid4()),name='HTTP C ficticio',tax_id='76111111-1',bank='Banco QA',account_type='Corriente',account_number='QA',account_holder='QA',account_tax_id='76111111-1'),'QA')
        self.invoice=ap.execute(self.store,'invoice',dict(request_id=str(uuid4()),supplier_id=supplier['id'],document_type='invoice',folio='101',amount=10000,issued_on='2026-10-01',due_on='2026-10-10',condition='credit'),'QA')
        ap.attach(self.store,self.invoice['id'],None,b'%PDF-1.4\nQA\n%%EOF','qa.pdf','QA')

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def post(self,path,data,**headers):
        with self.partner.open(Request(self.url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers}),timeout=3) as response:return json.load(response)

    def test_routes_keep_partner_and_origin_controls(self):
        cashier=authenticated_opener(self.server,self.root,'cashier','caja-qa')
        paths=[('/api/suppliers/payment-proposals',None),('/api/suppliers/payment-proposals/1',None),('/api/suppliers/payment-proposal',b'{}'),('/api/suppliers/payment-proposals',b'{}'),(f'/api/suppliers/invoices/{self.invoice["id"]}/payment-hold',b'{}')]
        for opener,code in ((None,401),(cashier,403)):
            for path,body in paths:
                with self.subTest(path=path,code=code),self.assertRaises(HTTPError) as error:
                    (opener.open if opener else urlopen)(Request(self.url+path,data=body,headers={'X-ERP-Local':'1'}),timeout=3)
                self.assertEqual(error.exception.code,code);error.exception.close()
        for path,body in paths:
            if body is None:continue
            with self.assertRaises(HTTPError) as error:self.post(path,{},Origin='https://foreign.invalid')
            self.assertEqual(error.exception.code,403);error.exception.close()

    def test_preview_review_save_and_history_preserve_payment_state(self):
        preview=self.post('/api/suppliers/payment-proposal',dict(week_start='2026-10-14',budget=4000))
        row=preview['candidates'][0]
        self.assertEqual(row['proposed_amount'],4000)
        body=dict(preview['params'],request_id=str(uuid4()),token=preview['token'],scheduled_on='2026-10-16',confirmed=True,actor='forged',items=[dict(id=row['id'],version=row['version'],amount=3500)])
        saved=self.post('/api/suppliers/payment-proposals',body)
        self.assertEqual(self.post('/api/suppliers/payment-proposals',body),saved)
        with self.partner.open(self.url+'/api/suppliers/payment-proposals/'+str(saved['id']),timeout=3) as response:record=json.load(response)
        self.assertEqual(record['actor'],'socio-http-qa');self.assertEqual(record['selected'][0]['amount'],3500)
        with self.partner.open(self.url+'/api/suppliers/payment-proposals',timeout=3) as response:self.assertEqual(len(json.load(response)['proposals']),1)
        view=ap.view(self.store);self.assertEqual(view['totals']['balance'],10000);self.assertEqual(view['totals']['scheduled'],3500)
        self.assertEqual(view['invoices'][0]['paid'],0)
        with self.partner.open(self.url+'/payment-proposal.js',timeout=3) as response:self.assertIn(b'OvejaPaymentProposal',response.read())

    def test_hold_is_audited_and_stales_previous_proposal(self):
        preview=self.post('/api/suppliers/payment-proposal',dict(week_start='2026-10-14',budget=4000))
        path=f'/api/suppliers/invoices/{self.invoice["id"]}/payment-hold'
        self.post(path,dict(request_id=str(uuid4()),version=1,hold=True,reason='Disputa ficticia',actor='forged'))
        with self.assertRaises(HTTPError) as error:
            self.post('/api/suppliers/payment-proposals',dict(preview['params'],request_id=str(uuid4()),token=preview['token'],scheduled_on='2026-10-16',confirmed=True,items=[dict(id=self.invoice['id'],version=1,amount=4000)]))
        self.assertEqual(error.exception.code,409);error.exception.close()
        row=ap.view(self.store)['invoices'][0];self.assertEqual(row['payment_hold']['actor'],'socio-http-qa')
        self.post(path,dict(request_id=str(uuid4()),version=row['version'],hold=False,reason='Revisión ficticia resuelta'))
        self.assertIsNone(ap.view(self.store)['invoices'][0]['payment_hold'])
