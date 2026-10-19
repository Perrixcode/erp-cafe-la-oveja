import unittest,json,base64
from uuid import uuid4
from urllib.error import HTTPError
import test_joint_payment_http as fixture
class PaymentExcessHTTPTests(fixture.JointPaymentHTTPTests):
    path='/api/suppliers/account-invoice-payments'
    def body(self):
        return dict(request_id=str(uuid4()),supplier_id=self.supplier['id'],amount=12000,occurred_on='2026-01-02',method='transfer',confirmed=True,actor='forged',
            invoices=[dict(id=self.invoice['id'],version=1)],applications=[dict(invoice_id=self.invoice['id'],version=1,amount=10000)],overpayment_confirmation=dict(confirmed=True,selected_balance=10000,excess=2000),proof=dict(name='prueba.pdf',base64=base64.b64encode(b'%PDF-1.4\nFicticio\n%%EOF').decode()))
    def test_file_larger_than_default_json_limit_saved_once_and_downloaded_identically(self):
        body=self.body();result=self.post(self.path,body);self.assertEqual(self.post(self.path,body),result);self.assertEqual(result['actor'],'socio-http-qa');self.assertEqual(result['available'],2000)
        with self.partner.open(self.url+f'/api/suppliers/account-files/{result["file_id"]}',timeout=3) as response:self.assertEqual(response.read(),base64.b64decode(body['proof']['base64']))
    def test_confirmation_and_actor_are_checked_on_server(self):
        body=self.body();body.pop('overpayment_confirmation')
        with self.assertRaises(HTTPError) as err:self.post(self.path,body)
        self.assertEqual(err.exception.code,409);err.exception.close()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_account_entries').fetchone()[0],0)
