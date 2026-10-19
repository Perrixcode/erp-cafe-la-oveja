"""Synthetic tests of money, imports, retries, evidence and permissions."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from app import make_server
from erp import payables as ap, sii_purchases as sii
from erp.backups import create_backup, restore_backup, verify_backup
from erp.domain import DomainError
from erp.store import Store
from http_test_support import authenticated_opener

PDF=b'%PDF-1.4\n% Synthetic evidence only\n%%EOF\n'
HEAD='Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'
def csv_content(rows=None):
    return (HEAD+'\n'.join(rows or ['1;33;Del Giro;76.123.456-7;Proveedor QA;123;01/10/2026;11900','2;34;Del Giro;76.222.222-2;Proveedor Dos QA;456;02/10/2026;5000'])+'\n').encode()

class PayablesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'data/erp-demo.sqlite3')
        self.supplier=self.do('supplier',dict(name='Proveedor ficticio QA',tax_id='76.111.111-1',bank='Banco QA',account_number='000123',account_holder='Titular QA',account_tax_id='76111111-1',account_type='Corriente'))
        self.invoice=self.do('invoice',self.invoice_data())
    def do(self,action,data,target=None):return ap.execute(self.store,action,dict(request_id=str(uuid4()),**data),'socio-qa',target)
    def invoice_data(self,**changes):return dict(dict(supplier_id=self.supplier['id'],document_type='invoice',folio='00042',issued_on='2026-01-01',due_on='2026-01-30',amount=10000,condition='credit'),**changes)
    def current(self):return next(r for r in ap.view(self.store)['invoices'] if r['id']==self.invoice['id'])
    def payment(self,amount=3000,**changes):
        data=dict(amount=amount,paid_on='2026-01-02',method='transfer',reference='QA',confirmed=True,version=self.current()['version']);data.update(changes)
        return self.do('payment',data,self.invoice['id'])
    def test_partial_full_and_void_payment(self):
        self.payment();r=self.current();self.assertEqual((r['paid'],r['balance'],r['status']),(3000,7000,'partial'))
        p=self.payment(7000);r=self.current();self.assertEqual((r['balance'],r['status']),(0,'paid'))
        self.do('void_payment',dict(version=r['version'],reason='Comprobante de otra factura'),p['id'])
        self.assertEqual(self.current()['balance'],7000);self.assertTrue(self.current()['payments'][0]['voided_at'])
        events=ap.history(self.store,r['id'])['events'];self.assertTrue(all(e['actor']=='socio-qa' for e in events));self.assertEqual(events[0]['action'],'void_payment')
    def test_duplicate_normalized_folio_rut_and_type(self):
        self.assertEqual(self.invoice['folio'],'00042');self.assertEqual(self.supplier['tax_id'],'761111111')
        with self.assertRaises(DomainError):self.do('invoice',self.invoice_data(folio='42'))
        with self.assertRaises(DomainError):self.do('supplier',dict(name='Otro',tax_id='76111111-1'))
        self.do('invoice',self.invoice_data(document_type='exempt'));self.assertEqual(len(ap.view(self.store)['invoices']),2)
    def test_idempotency_and_request_collision(self):
        d=dict(request_id=str(uuid4()),amount=1000,paid_on='2026-01-02',method='transfer',confirmed=True,version=1)
        first=ap.execute(self.store,'payment',d,'socio-qa',self.invoice['id']);self.assertEqual(first,ap.execute(self.store,'payment',d,'socio-qa',self.invoice['id']))
        with self.assertRaises(DomainError):ap.execute(self.store,'payment',{**d,'amount':2000},'socio-qa',self.invoice['id'])
        self.assertEqual(self.current()['paid'],1000)
    def test_concurrent_payments(self):
        results=[]
        def worker():
            try:results.append(self.payment(8000,version=1)['amount'])
            except DomainError as err:results.append(err.status)
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,[8000,409]);self.assertEqual(self.current()['balance'],2000)
    def test_invalid_payments_leave_balance_unchanged(self):
        for d in [dict(amount=10001),dict(amount=0),dict(amount=True),dict(amount=3.5),dict(paid_on='2999-01-01'),dict(confirmed=False),dict(version=True),dict(method=[])]:
            with self.subTest(d=d),self.assertRaises(DomainError):self.payment(**d)
        self.assertEqual(self.current()['paid'],0)
    def test_schedule_abono_and_whatsapp(self):
        r=self.current();self.do('schedule',dict(scheduled_on='2026-10-14',items=[dict(id=r['id'],version=r['version'],amount=5000)]))
        self.payment();r=self.current();self.assertEqual(r['planned_amount'],2000)
        msg=ap.message(self.store,{'items':[dict(id=r['id'],version=r['version'])]});self.assertEqual(msg['total'],2000)
        for value in ('saldo $7.000','Banco QA','000123'):self.assertIn(value,msg['text'])
        self.payment(7000);self.assertEqual(self.current()['scheduled_on'],'')
        with self.assertRaises(DomainError):ap.message(self.store,{'items':[dict(id=r['id'],version=r['version'])]})
    def test_schedule_atomic_and_duplicate_selections(self):
        r=self.current();second=self.do('invoice',self.invoice_data(folio='43'))
        with self.assertRaises(DomainError):self.do('schedule',dict(scheduled_on='2026-10-14',items=[dict(id=r['id'],version=1,amount=1000),dict(id=second['id'],version=999,amount=1000)]))
        self.assertEqual(self.current()['scheduled_on'],'')
        with self.assertRaises(DomainError):ap.message(self.store,{'items':[dict(id=r['id'],version=1)]*2})
    def test_edits_and_void_preserve_payments(self):
        self.payment()
        with self.assertRaises(DomainError):self.do('invoice',{**self.invoice_data(amount=1000),'reason':'QA','version':2},self.invoice['id'])
        with self.assertRaises(DomainError):self.do('void_invoice',dict(version=2,reason='QA'),self.invoice['id'])
        self.assertEqual(self.current()['paid'],3000)
    def test_attachment_dedup_bound_and_validated(self):
        p=self.payment();a=ap.attach(self.store,self.invoice['id'],p['id'],PDF,'comprobante.pdf','socio-qa')
        self.assertEqual(a['id'],ap.attach(self.store,self.invoice['id'],p['id'],PDF,'otro.pdf','socio-qa')['id'])
        second=self.do('invoice',self.invoice_data(folio='43'))
        with self.assertRaises(DomainError):ap.attach(self.store,second['id'],p['id'],PDF,'x.pdf','socio-qa')
        for content in (b'<html>bad</html>',b'a'*(ap.LIMIT+1)):
            with self.assertRaises(DomainError):ap.attach(self.store,self.invoice['id'],None,content,'x.pdf','socio-qa')
        self.assertNotIn('content',self.current()['files'][0]);self.assertEqual(len(self.current()['files']),1)
    def test_backup_recovers_blobs_imports_and_payments(self):
        ap.attach(self.store,self.invoice['id'],None,PDF,'original.pdf','socio-qa');self.payment();batch=sii.upload(self.store,csv_content(),'compras.csv','socio-qa')
        backup=create_backup(self.root);verify_backup(backup);dest=restore_backup(backup,self.root/'restored');recovered=Store(dest/'data/erp-demo.sqlite3')
        self.assertEqual(ap.view(recovered),ap.view(self.store));self.assertEqual(sii.preview(recovered,batch['id'])['counts']['new'],2)
        with recovered.connect() as db:self.assertEqual(db.execute('SELECT content FROM ap_files').fetchone()[0],PDF)
    def test_sii_preview_confirm_idempotency_and_unknown_debt(self):
        p=sii.upload(self.store,csv_content(),'compras.csv','socio-qa');self.assertEqual(p['counts']['new'],2);self.assertEqual(len(ap.view(self.store)['invoices']),1)
        body=dict(rows=[2,3],confirmed=True);self.assertEqual(sii.confirm(self.store,p['id'],body,'socio-qa')['imported'],2)
        self.assertEqual(sii.confirm(self.store,p['id'],body,'socio-qa')['already_imported'],2);self.assertEqual(sii.preview(self.store,p['id'])['counts']['duplicate'],2)
        v=ap.view(self.store);self.assertEqual(v['totals']['review'],2);self.assertEqual(v['totals']['balance'],10000)
        r=next(i for i in v['invoices'] if i['payment_review'])
        with self.assertRaises(DomainError):ap.message(self.store,dict(items=[dict(id=r['id'],version=1)]))
        with self.assertRaises(DomainError):self.do('payment',dict(version=1,amount=1,paid_on='2026-01-02',method='transfer',confirmed=True),r['id'])
        self.do('reconcile',dict(version=1,previous_paid=1000,confirmed=True,reason='Comprobantes anteriores'),r['id'])
        r=next(i for i in ap.view(self.store)['invoices'] if i['id']==r['id']);self.assertEqual(r['balance'],r['amount']-1000);self.assertFalse(r['payment_review']);self.assertEqual(r['payments'][0]['paid_on'],'')
    def test_sii_duplicates_conflicts_credit_notes_errors(self):
        p=sii.upload(self.store,csv_content(['1;33;Del Giro;76.111.111-1;Proveedor QA;42;01/01/2026;10000','2;61;Del Giro;76.111.111-1;Proveedor QA;98;01/01/2026;1000','3;33;Del Giro;76.222.222-2;Dos;12;01/10/2026;invalid']),'x.csv','socio-qa')
        self.assertEqual([r['status'] for r in p['rows']],['duplicate','unsupported','error'])
        d=sii.upload(self.store,csv_content(['1;33;Del Giro;76.111.111-1;Proveedor QA;42;01/01/2026;9999']),'x.csv','socio-qa');self.assertEqual(d['rows'][0]['status'],'conflict')
        with self.assertRaises(DomainError):sii.confirm(self.store,p['id'],dict(rows=[2,3],confirmed=True),'socio-qa')
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_sii_repeated_rows_invalid_headers_and_unidentified_supplier(self):
        row='1;33;Del Giro;76.333.333-3;Proveedor QA;7;01/10/2026;1000';p=sii.upload(self.store,csv_content([row,row]),'x.csv','socio-qa');self.assertEqual(p['counts']['conflict'],2)
        with self.assertRaises(DomainError):sii.upload(self.store,b'Folio;Monto\n1;1000','x.csv','socio-qa')
        self.do('supplier',dict(name='Proveedor QA'));p=sii.upload(self.store,csv_content(),'x.csv','socio-qa');self.assertEqual(p['rows'][0]['status'],'conflict')
    def test_import_edit_preserves_source_and_prevents_duplicate(self):
        p=sii.upload(self.store,csv_content(),'x.csv','socio-qa');sii.confirm(self.store,p['id'],dict(rows=[2],confirmed=True),'socio-qa');r=next(i for i in ap.view(self.store)['invoices'] if i['source'])
        self.assertEqual(self.do('invoice',{**r,'amount':12000,'folio':'999','due_on':'2026-11-01','reason':'Corrección con documento'},r['id'])['due_on'],'2026-11-01')
        self.assertEqual(sii.preview(self.store,p['id'])['rows'][0]['status'],'conflict')
        self.assertEqual(next(i for i in ap.view(self.store)['invoices'] if i['id']==r['id'])['source']['source_amount'],11900)

class PayablesHTTPTests(unittest.TestCase):
    def test_roles_origins_actor_csv_and_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);server=make_server(root/'erp.sqlite3',0,seed=False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_address[1]);store=Store(root/'erp.sqlite3');partner=authenticated_opener(server,root)
                h={'Content-Type':'application/json','X-ERP-Local':'1'};p=dict(request_id=str(uuid4()),name='QA HTTP',actor='spoofed')
                for opener,code in [(None,401),(authenticated_opener(server,root,'cashier','caja-qa'),403),(authenticated_opener(server,root,'production','produccion-qa'),403)]:
                    for path,body in [('/api/suppliers/payables',None),('/api/suppliers/providers',json.dumps(p).encode()),('/api/suppliers/files/1',None),('/api/suppliers/imports',csv_content())]:
                        with self.subTest(path=path,code=code),self.assertRaises(HTTPError) as err:(opener.open if opener else urlopen)(Request(url+path,data=body,headers=h))
                        self.assertEqual(err.exception.code,code);err.exception.close()
                with partner.open(Request(url+'/api/suppliers/providers',data=json.dumps(p).encode(),headers=h)) as response:self.assertEqual(response.status,201)
                with store.connect() as db:self.assertEqual(db.execute('SELECT actor FROM ap_events').fetchone()[0],'socio-http-qa')
                for path,body in [('/api/suppliers/providers',json.dumps(p).encode()),('/api/suppliers/imports',csv_content())]:
                    with self.assertRaises(HTTPError) as err:partner.open(Request(url+path,data=body,headers={**h,'Origin':'https://foreign.invalid'}))
                    self.assertEqual(err.exception.code,403);err.exception.close()
                with partner.open(Request(url+'/api/suppliers/imports',data=csv_content(),headers={'X-ERP-Local':'1','X-File-Name':'qa.csv'})) as response:preview=json.load(response)
                self.assertEqual(preview['counts']['new'],2)
                r=ap.execute(store,'invoice',dict(request_id=str(uuid4()),supplier_id=1,document_type='invoice',folio='1',issued_on='2026-01-01',amount=1000,condition='credit'),'QA')
                with partner.open(Request(url+f'/api/suppliers/invoices/{r["id"]}/files',data=PDF,headers={'X-ERP-Local':'1','X-File-Name':'qa.pdf'})) as response:key=json.load(response)['id']
                with partner.open(url+f'/api/suppliers/files/{key}') as response:self.assertEqual(response.read(),PDF);self.assertEqual(response.headers['Content-Type'],'application/pdf')
                for asset in ('payables.js','payables.css'):
                    with urlopen(url+'/'+asset) as response:self.assertEqual(response.status,200)
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
