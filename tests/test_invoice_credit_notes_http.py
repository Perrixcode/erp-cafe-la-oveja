import base64,json,unittest
from uuid import uuid4
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import test_supplier_account_http as base
from http_test_support import authenticated_opener
from erp import payables as ap

class InvoiceNoteHTTPTests(unittest.TestCase):
    setUp=base.SupplierAccountHTTPTests.setUp;stop=base.SupplierAccountHTTPTests.stop;post=base.SupplierAccountHTTPTests.post
    path='/api/suppliers/account-invoice-credit-notes'
    def body(self):
        return dict(request_id=uuid4().hex,supplier_id=self.supplier['id'],origin_invoice_id=self.invoice['id'],origin_version=self.invoice['version'],
                    amount=4000,folio='009',occurred_on='2026-01-02',notes='Corrección FICTICIA revisada',confirmed=True,actor='actor-forjado',
                    proof=dict(name='nota.pdf',base64=base64.b64encode(b'%PDF-1.4\nSynthetic only\n%%EOF').decode()))
    def test_session_partner_and_origin_boundaries(self):
        body=self.body()
        for opener,code in ((None,401),(authenticated_opener(self.server,self.root,'cashier','caja-qa'),403),(authenticated_opener(self.server,self.root,'production','produccion-qa'),403)):
            with self.assertRaises(HTTPError) as e:(opener.open if opener else urlopen)(Request(self.url+self.path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'}))
            self.assertEqual(e.exception.code,code);e.exception.close()
        with self.assertRaises(HTTPError) as e:self.post(self.path,body,Origin='https://foreign.invalid')
        self.assertEqual(e.exception.code,403);e.exception.close()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_account_entries').fetchone()[0],0)
    def test_atomic_note_proof_retry_and_export_round_trip(self):
        body=self.body();note=self.post(self.path,body);self.assertEqual(self.post(self.path,body),note)
        self.assertEqual((note['folio'],note['applied'],note['available'],note['actor']),('9',4000,0,'socio-http-qa'))
        invoice=next(r for r in ap.view(self.store)['invoices'] if r['id']==self.invoice['id']);self.assertEqual((invoice['amount'],invoice['credited'],invoice['balance']),(10000,4000,6000))
        with self.partner.open(self.url+'/api/suppliers/account-files/'+str(note['file_id'])) as response:self.assertEqual(response.read(),base64.b64decode(body['proof']['base64']))
        with self.partner.open(self.url+'/api/suppliers/accounts/'+str(self.supplier['id'])+'/export.csv') as response:self.assertIn('Factura origen 123',response.read().decode('utf-8-sig'))
        with self.assertRaises(HTTPError) as e:self.post(self.path,dict(body,request_id=uuid4().hex))
        self.assertEqual(e.exception.code,409);e.exception.close()

if __name__=='__main__':unittest.main()
