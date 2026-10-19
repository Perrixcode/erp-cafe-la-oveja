"""Partner-only history confirmations; loopback and fictional SQLite."""
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4
from erp import payables as ap
from http_test_support import authenticated_opener
import test_supplier_account_http as support

class HistoricalFolioHTTPTests(unittest.TestCase):
    setUp=support.SupplierAccountHTTPTests.setUp
    stop=support.SupplierAccountHTTPTests.stop
    post=support.SupplierAccountHTTPTests.post
    def corrected(self):
        ap.execute(self.store,'invoice',dict(self.invoice,folio='999',reason='Corrección ficticia',request_id=str(uuid4())),'QA',self.invoice['id'])
        return dict(supplier_id=self.supplier['id'],document_type='invoice',folio='00123',issued_on='2026-01-01',amount=10000,condition='credit',request_id=str(uuid4()),actor='forjado')
    def snapshot(self):
        with self.store.connect() as db:return [tuple(r) for r in db.execute('SELECT id,folio FROM ap_invoices')]
    def test_warning_response_and_confirmation_actor_are_authenticated(self):
        body=self.corrected();before=self.snapshot()
        with self.assertRaises(HTTPError) as err:self.post('/api/suppliers/invoices',body)
        self.assertEqual(err.exception.code,409);payload=json.load(err.exception);err.exception.close()
        self.assertEqual(before,self.snapshot());warning=payload['historical_duplicate'];self.assertEqual(warning['matches'][0]['current_folio'],'999')
        body['historical_confirmation']=dict(confirmed=True,reason='Revisión ficticia HTTP',token=warning['token'])
        result=self.post('/api/suppliers/invoices',body);self.assertEqual(result['folio'],'00123')
        self.assertEqual(self.post('/api/suppliers/invoices',body),result)
        with self.store.connect() as db:
            rows=list(db.execute("SELECT actor,reason FROM ap_events WHERE action='historical_identity_confirmed'"))
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['actor'],'socio-http-qa');self.assertEqual(rows[0]['reason'],'Revisión ficticia HTTP')
    def test_roles_origin_and_json_boundaries_are_unchanged(self):
        body=self.corrected();before=self.snapshot();path='/api/suppliers/invoices'
        for opener,code in ((None,401),(authenticated_opener(self.server,self.root,'cashier','caja-qa'),403),(authenticated_opener(self.server,self.root,'production','produccion-qa'),403)):
            with self.assertRaises(HTTPError) as err:
                (opener.open if opener else urlopen)(Request(self.url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'}),timeout=3)
            self.assertEqual(err.exception.code,code);self.assertNotIn('historical_duplicate',json.load(err.exception));err.exception.close()
        for headers,code in (({'Origin':'https://foreign.invalid'},403),({'Content-Type':'text/plain'},415)):
            with self.assertRaises(HTTPError) as err:self.post(path,body,**headers)
            self.assertEqual(err.exception.code,code);err.exception.close()
        self.assertEqual(before,self.snapshot())
    def test_live_duplicate_does_not_offer_confirmation(self):
        body=dict(self.invoice,request_id=str(uuid4()),check_duplicates=False,historical_confirmation={'confirmed':True,'reason':'QA','token':'forged'})
        with self.assertRaises(HTTPError) as err:self.post('/api/suppliers/invoices',body)
        self.assertEqual(err.exception.code,409);payload=json.load(err.exception);err.exception.close()
        self.assertNotIn('historical_duplicate',payload);self.assertIn('Ya existe',payload['error'])
