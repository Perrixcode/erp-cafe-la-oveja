"""Current-account HTTP contract against temporary SQLite and loopback only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from app import make_server
from erp import payables as ap
from erp.store import Store
from http_test_support import authenticated_opener


class SupplierAccountHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.server = make_server(self.root/'synthetic.sqlite3', 0, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = 'http://127.0.0.1:'+str(self.server.server_address[1])
        self.partner = authenticated_opener(self.server, self.root)
        self.store = Store(self.root/'synthetic.sqlite3')
        self.supplier = ap.execute(self.store, 'supplier', dict(name='Proveedor HTTP ficticio',request_id=str(uuid4())), 'QA')
        self.invoice = ap.execute(self.store, 'invoice', dict(supplier_id=self.supplier['id'],document_type='invoice',folio='123',amount=10000,issued_on='2026-01-01',condition='credit',request_id=str(uuid4())), 'QA')

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, body, **headers):
        request = Request(self.url+path, data=json.dumps(body).encode(), headers={'Content-Type':'application/json','X-ERP-Local':'1',**headers})
        with self.partner.open(request, timeout=3) as response:
            return json.load(response)

    def payment_body(self):
        return dict(request_id=str(uuid4()),supplier_id=self.supplier['id'],amount=4000,occurred_on='2026-01-02',method='cash',confirmed=True,actor='actor-forjado',applications=[dict(invoice_id=self.invoice['id'],version=1,amount=3000)])

    def test_new_routes_keep_session_partner_and_origin_boundaries(self):
        cashier = authenticated_opener(self.server,self.root,'cashier','caja-qa')
        production = authenticated_opener(self.server,self.root,'production','produccion-qa')
        paths = [(f'/api/suppliers/accounts/{self.supplier["id"]}',None),(f'/api/suppliers/accounts/{self.supplier["id"]}/export.csv',None),('/api/suppliers/account-files/1',None),
                 ('/api/suppliers/account-payments',b'{}'),('/api/suppliers/account-credit-notes',b'{}'),
                 ('/api/suppliers/account-entries/1/apply',b'{}'),('/api/suppliers/account-entries/1/void',b'{}'),
                 ('/api/suppliers/account-applications/1/void',b'{}'),('/api/suppliers/account-entries/1/files',b'%PDF-QA')]
        paths.extend([(f'/api/suppliers/accounts/{self.supplier["id"]}/export.pdf',None),('/api/suppliers/pending.pdf',None)])
        for opener,code in ((None,401),(cashier,403),(production,403)):
            for path,body in paths:
                with self.subTest(path=path,code=code),self.assertRaises(HTTPError) as error:
                    (opener.open if opener else urlopen)(Request(self.url+path,data=body,headers={'X-ERP-Local':'1'}),timeout=3)
                self.assertEqual(error.exception.code,code)
                error.exception.close()
        for path,body in paths:
            if body is None:
                continue
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:
                self.partner.open(Request(self.url+path,data=body,headers={'X-ERP-Local':'1','Origin':'https://foreign.invalid'}),timeout=3)
            self.assertEqual(error.exception.code,403)
            error.exception.close()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_account_entries').fetchone()[0],0)

    def test_payment_application_proof_and_actor_round_trip(self):
        body = self.payment_body()
        entry = self.post('/api/suppliers/account-payments',body)
        self.assertEqual(self.post('/api/suppliers/account-payments',body),entry)
        self.assertEqual(entry['actor'],'socio-http-qa')
        raw = b'%PDF-1.4\nSynthetic QA only\n%%EOF'
        path = f'/api/suppliers/account-entries/{entry["id"]}/files'
        def upload():
            with self.partner.open(Request(self.url+path,data=raw,headers={'X-ERP-Local':'1','X-File-Name':'ficticio.pdf'}),timeout=3) as response:
                return json.load(response)
        file = upload()
        self.assertEqual(upload()['id'],file['id'])
        with self.partner.open(self.url+f'/api/suppliers/account-files/{file["id"]}?download=1',timeout=3) as response:
            self.assertEqual(response.read(),raw)
            self.assertIn('attachment',response.headers['Content-Disposition'])
        with self.partner.open(self.url+f'/api/suppliers/accounts/{self.supplier["id"]}',timeout=3) as response:
            statement = json.load(response)
        self.assertEqual(statement['totals']['net_balance'],6000)
        self.assertEqual(statement['totals']['available_credit'],1000)
        self.assertTrue(statement['totals']['consistent'])
        self.assertEqual(statement['entries'][0]['files'][0]['actor'],'socio-http-qa')
        self.assertNotIn('content',statement['entries'][0]['files'][0])
        self.post(f'/api/suppliers/account-entries/{entry["id"]}/apply',dict(request_id=str(uuid4()),version=1,confirmed=True,applications=[dict(invoice_id=self.invoice['id'],version=2,amount=1000)]))
        with self.store.connect() as db:
            actors = {r[0] for r in db.execute("SELECT actor FROM ap_events WHERE action LIKE 'account_%'")}
        self.assertEqual(actors,{'socio-http-qa'})

    def test_note_and_void_round_trip_and_file_integrity(self):
        note = self.post('/api/suppliers/account-credit-notes',dict(request_id=str(uuid4()),supplier_id=self.supplier['id'],amount=2000,occurred_on='2026-01-03',confirmed=True,folio='15',origin_invoice_id=self.invoice['id'],origin_version=1,applications=[dict(invoice_id=self.invoice['id'],version=1,amount=2000)]))
        self.post(f'/api/suppliers/account-entries/{note["id"]}/void',dict(request_id=str(uuid4()),version=1,reason='Corrección ficticia',invoices=[dict(id=self.invoice['id'],version=2)]))
        from erp import supplier_account as account
        file = account.attach(self.store,note['id'],b'%PDF-1.4\nQA\n%%EOF', 'QA.pdf','QA')
        with self.store.connect() as db:
            db.execute("UPDATE ap_account_files SET content=? WHERE id=?",(b'corrupt',file['id']))
        with self.assertRaises(HTTPError) as error:
            self.partner.open(self.url+f'/api/suppliers/account-files/{file["id"]}',timeout=3)
        self.assertEqual(error.exception.code,409)
        error.exception.close()
        self.assertEqual(ap.view(self.store)['invoices'][0]['balance'],10000)

    def test_statement_filter_and_csv_use_identical_rows(self):
        entry=self.post('/api/suppliers/account-payments',self.payment_body())
        base=f'/api/suppliers/accounts/{self.supplier["id"]}'
        query='?from=2026-01-02&to=2026-01-02&kind=payments'
        with self.partner.open(self.url+base+query,timeout=3) as response:
            data=json.load(response)
        self.assertEqual(len(data['journal']),1)
        with self.partner.open(self.url+base+'/export.csv'+query,timeout=3) as response:
            exported=response.read().decode('utf-8-sig')
            self.assertIn('text/csv',response.headers['Content-Type'])
            self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual(len(exported.splitlines()),2)
        self.assertIn('CC-'+str(entry['id']),exported)
        with self.assertRaises(HTTPError) as error:
            self.partner.open(self.url+base+'?from=invalid',timeout=3)
        self.assertEqual(error.exception.code,400)
        error.exception.close()

    def test_pdf_downloads_share_filters_and_do_not_mutate_business_records(self):
        from io import BytesIO
        from pypdf import PdfReader
        entry=self.post('/api/suppliers/account-payments',self.payment_body())
        with self.store.connect() as db:before='\n'.join(db.iterdump())
        paths=[f'/api/suppliers/accounts/{self.supplier["id"]}/export.pdf?from=2026-01-02&to=2026-01-02&kind=payments',
               '/api/suppliers/pending.pdf?supplier=does-not-filter-the-consolidated']
        for path in paths:
            with self.partner.open(self.url+path,timeout=10) as response:
                self.assertEqual(response.headers['Content-Type'],'application/pdf')
                self.assertIn('attachment;',response.headers['Content-Disposition'])
                self.assertIn('.pdf',response.headers['Content-Disposition'])
                self.assertIn('no-store',response.headers['Cache-Control'])
                self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
                content=response.read();self.assertTrue(content.startswith(b'%PDF-'))
            text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)
            self.assertIn('$ 6.000',text)
            if '/accounts/' in path:
                self.assertIn('CC-'+str(entry['id']),text);self.assertIn('Pagos y anulaciones',text)
                self.assertNotIn('Factura 123',text)
            else:self.assertIn('Factura 123',text)
        with self.store.connect() as db:self.assertEqual('\n'.join(db.iterdump()),before)

    def test_pdf_rejects_missing_supplier_and_invalid_filters(self):
        base=f'/api/suppliers/accounts/{self.supplier["id"]}/export.pdf'
        for path,code in [('/api/suppliers/accounts/999999/export.pdf',404),(base+'?from=bad',400),
                          (base+'?from=2026-02-01&to=2026-01-01',400),(base+'?kind=bad',400)]:
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:self.partner.open(self.url+path,timeout=3)
            self.assertEqual(error.exception.code,code);error.exception.close()

    def test_large_invoice_attachment_retry_preserves_one_file_and_event(self):
        content=b'\x89PNG\r\n\x1a\n'+b'FICTICIO'*(90000)+b'IEND'+b'\0'*4
        path=self.url+f'/api/suppliers/invoices/{self.invoice["id"]}/files'
        request=lambda raw:Request(path,data=raw,headers={'X-ERP-Local':'1','X-File-Name':'ficticio-grande.png','Content-Type':'image/png'})
        with self.partner.open(request(content),timeout=5) as response:first=json.load(response)
        with self.partner.open(request(content),timeout=5) as response:retry=json.load(response)
        self.assertEqual(first['id'],retry['id']);self.assertTrue(retry['already_attached'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_files').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ap_events WHERE action='file_attached'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT content FROM ap_files').fetchone()[0],content)
        oversized=request(b'');oversized.add_header('Content-Length',str(ap.LIMIT+1))
        with self.assertRaises(HTTPError) as error:self.partner.open(oversized,timeout=5)
        self.assertEqual(error.exception.code,413);error.exception.close()
