"""Only disposable synthetic databases and generated test documents."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import threading

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from erp import payables as ap, supplier_account as account, payment_receipts as receipts
from erp.domain import DomainError
from erp.store import Store
from test_supplier_finance_pdf import FictionalAccounts


def source_pdf(pages=3, label='RESPALDO FICTICIO NO OPERATIVO'):
    stream=BytesIO();c=Canvas(stream,pagesize=(400,600))
    for i in range(pages):
        c.drawString(40,400,label+' '+str(i+1));c.showPage()
    c.save();r=PdfReader(BytesIO(stream.getvalue()));r.pages[0].rotate(90);r.pages[0].cropbox.lower_left=(10,20)
    w=PdfWriter();w.append(r);out=BytesIO();w.write(out);return out.getvalue()


def picture(fmt='PNG', size=(800,400), exif=False):
    image=Image.new('RGB',size,'#326144');stream=BytesIO();opts={}
    if exif:
        orientation=Image.Exif();orientation[274]=6;opts['exif']=orientation
    image.save(stream,format=fmt,**opts);return stream.getvalue()


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.store=Store(self.root/'synthetic.sqlite3');self.f=FictionalAccounts(self.store)
        self.s=self.f.supplier('Proveedor FICTICIO <QA>');self.a=self.f.invoice(self.s,'1001');self.b=self.f.invoice(self.s,'1002')
        self.legacy=self.f.command('payment',dict(version=1,amount=1000,paid_on='2026-01-02',method='cash',confirmed=True),self.a['id'])
        self.joint=self.f.payment(self.s,7000,[self.f.allocation(self.a,2000),self.f.allocation(self.b,3000)])

    def data(self,keys=None):
        listing=receipts.listing(self.store,'2026-01-02')
        return receipts.prepare(self.store,listing['date'],keys or [r['key'] for r in listing['payments']],listing['token'])

    def attach(self,raw,name='comprobante.pdf',legacy=False):
        if legacy:return ap.attach(self.store,self.a['id'],self.legacy['id'],raw,name,'QA sintético')
        return account.attach(self.store,self.joint['id'],raw,name,'QA sintético')

    def logical(self):
        with self.store.connect() as db:return list(db.iterdump())

    def test_both_payment_models_joint_once_and_no_financial_writes(self):
        self.attach(source_pdf());before=self.logical();listing=receipts.listing(self.store,'2026-01-02');data=self.data();raw=receipts.render_pdf(data)
        self.assertEqual(len(listing['payments']),2);self.assertEqual(sum(r['amount'] for r in data['payments']),8000)
        joint=next(r for r in data['payments'] if r['source']=='account');self.assertEqual(len(joint['invoices']),2);self.assertEqual(joint['unapplied'],2000)
        self.assertEqual(len(data['evidence']),1);self.assertEqual(before,self.logical());self.assertTrue(raw.startswith(b'%PDF-'))

    def test_native_all_pdf_pages_rotation_and_crop_preserved(self):
        self.attach(source_pdf());r=PdfReader(BytesIO(receipts.render_pdf(self.data())))
        pages=[p for p in r.pages if 'RESPALDO FICTICIO' in p.extract_text()]
        self.assertEqual(len(pages),3);self.assertEqual(pages[0].rotation,90);self.assertEqual(tuple(pages[0].cropbox.lower_left),(10,20))
        self.assertEqual(tuple(pages[0].mediabox),(0,0,400,600))
        for i in range(3):self.assertIn('NO OPERATIVO '+str(i+1),pages[i].extract_text())

    def test_duplicate_proof_in_two_payments_is_included_once(self):
        raw=source_pdf();self.attach(raw);self.attach(raw,legacy=True);data=self.data()
        self.assertEqual(len(data['evidence']),1);self.assertEqual(len(data['evidence'][0]['payments']),2)
        pdf=PdfReader(BytesIO(receipts.render_pdf(data)));self.assertEqual(sum('RESPALDO FICTICIO' in p.extract_text() for p in pdf.pages),3)
        self.assertEqual(sum(p['amount'] for p in data['payments']),8000)

    def test_unreadable_latest_never_auto_falls_back_and_explicit_choice_is_traced(self):
        first=self.attach(source_pdf());self.attach(b'%PDF-1.7\nbroken newest\n%%EOF')
        data=self.data();row=next(r for r in data['payments'] if r['source']=='account')
        self.assertIsNone(row['selected_file']);self.assertEqual(len(row['files']),2);self.assertIn('Último comprobante no legible',row['warnings'][0])
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(receipts.render_pdf(data))).pages)
        self.assertIn('no se pudo abrir',text)
        listing=receipts.listing(self.store,'2026-01-02');key='CC-'+str(self.joint['id'])
        explicit=receipts.prepare(self.store,listing['date'],[key],listing['token'],{key:first['id']})
        self.assertEqual(explicit['payments'][0]['selected_file']['id'],first['id'])
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(receipts.render_pdf(explicit))).pages)
        self.assertIn('seleccionada expresamente',text);self.assertIn('2 versión(es)',text)
        with self.assertRaises(DomainError):receipts.prepare(self.store,listing['date'],[key],listing['token'],{key:99999})

    def test_newest_valid_selected_and_subset_honored(self):
        self.attach(source_pdf(label='OLD FICTICIO'));latest=self.attach(source_pdf(2,'LATEST FICTICIO'))
        data=self.data(['CC-'+str(self.joint['id'])]);self.assertEqual(len(data['payments']),1)
        self.assertEqual(data['payments'][0]['selected_file']['id'],latest['id'])
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(receipts.render_pdf(data))).pages)
        self.assertIn('LATEST FICTICIO 2',text);self.assertNotIn('OLD FICTICIO',text)

    def test_missing_and_unreadable_are_visible_not_fabricated(self):
        self.attach(b'%PDF-1.7\nbroken only\n%%EOF');data=self.data();self.assertFalse(data['evidence'])
        self.assertTrue(all(r['attachment'] is None for r in data['payments']))
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(receipts.render_pdf(data))).pages)
        self.assertIn('2 pagos sin respaldo incorporable',text);self.assertIn('Sin comprobante',text)

    def test_images_preserve_aspect_and_exif_orientation(self):
        for fmt,exif,size in [('PNG',False,(800,400)),('JPEG',True,(400,800))]:
            with self.subTest(fmt=fmt):
                raw=picture(fmt,exif=exif);meta=dict(bytes=len(raw),mime='image/png' if fmt=='PNG' else 'image/jpeg',sha256=hashlib.sha256(raw).hexdigest())
                item=receipts.inspect_file(raw,meta);self.assertEqual(item['size'],size)
                self.attach(raw,'foto.'+fmt.lower());data=self.data();output=receipts.render_pdf(data)
                image_page=PdfReader(BytesIO(output)).pages[-1];objects=image_page['/Resources']['/XObject'];im=next(v.get_object() for v in objects.values() if v.get_object()['/Subtype']=='/Image')
                self.assertEqual((im['/Width'],im['/Height']),size)
                operations=image_page.get_contents().operations
                scales=[args for args,op in operations if op==b'cm' and args[0]>10 and args[3]>10]
                self.assertTrue(any(abs(float(a[0])/float(a[3])-size[0]/size[1])<.0001 for a in scales))

    def test_encrypted_pdf_integrity_and_size_limits(self):
        w=PdfWriter();w.add_blank_page(100,100);w.encrypt('ficticia');b=BytesIO();w.write(b);self.attach(b.getvalue())
        self.assertIn('contraseña',self.data()['payments'][0]['warnings'][0])
        with patch.object(receipts,'MAX_BYTES',1),self.assertRaises(DomainError) as error:self.data()
        self.assertEqual(error.exception.status,413)
        with self.store.connect() as db:db.execute("UPDATE ap_account_files SET sha256=?",('0'*64,))
        self.assertIn('integridad',self.data()['payments'][0]['warnings'][0])

    def test_page_limits_do_not_silently_drop_pdf_pages(self):
        self.attach(source_pdf(3))
        with patch.object(receipts,'MAX_PAGES',2):
            data=self.data();self.assertFalse(data['evidence']);self.assertIn('más de 2 páginas',data['payments'][0]['warnings'][0])
        self.attach(source_pdf(2,'SECOND'),legacy=True)
        with patch.object(receipts,'MAX_PAGES',4),self.assertRaises(DomainError) as error:self.data()
        self.assertEqual(error.exception.status,413)

    def test_stale_snapshot_invalid_selection_and_invalid_dates(self):
        old=receipts.listing(self.store,'2026-01-02');self.attach(picture(),'foto.png')
        with self.assertRaises(DomainError) as error:receipts.prepare(self.store,old['date'],[r['key'] for r in old['payments']],old['token'])
        self.assertEqual(error.exception.status,409)
        for keys in ([],['CC-1','CC-1'],['bad'],['CC-9999']):
            with self.subTest(keys=keys),self.assertRaises(DomainError):
                fresh=receipts.listing(self.store,'2026-01-02');receipts.prepare(self.store,fresh['date'],keys,fresh['token'])
        with self.assertRaises(DomainError):receipts.listing(self.store,'2026-02-30')

    def test_void_credit_notes_other_dates_and_undated_not_invented(self):
        self.f.payment(self.s,500)
        self.f.command('account_credit_note',dict(supplier_id=self.s['id'],amount=10,occurred_on='2026-01-02',folio='NC-FICTICIO',confirmed=True,origin_invoice_id=self.a['id'],origin_version=self.f.current(self.a)['version']))
        with self.store.connect() as db:
            db.execute("UPDATE ap_account_entries SET voided_at='QA' WHERE id=?",(self.joint['id'],))
            db.execute("UPDATE ap_payments SET paid_on='' WHERE id=?",(self.legacy['id'],))
        listing=receipts.listing(self.store,'2026-01-02');self.assertEqual(len(listing['payments']),1);self.assertEqual(listing['undated_payments'],1)
        self.assertEqual(receipts.listing(self.store,'2026-01-03')['payments'],[])
        with patch.object(ap,'local_day',return_value='2026-01-02'):self.assertEqual(receipts.listing(self.store)['date'],'2026-01-02')

    def test_many_folios_and_large_summary_paginate_without_loss(self):
        invoices=[self.f.invoice(self.s,str(5000+i)) for i in range(50)]
        joint=self.f.payment(self.s,5000,[self.f.allocation(i,100) for i in invoices])
        for _ in range(90):self.f.payment(self.s,100)
        raw=receipts.render_pdf(self.data());r=PdfReader(BytesIO(raw));self.assertGreater(len(r.pages),5)
        text='\n'.join(p.extract_text() for p in r.pages)
        for i in invoices:self.assertIn(i['folio'],text)
        self.assertIn('93 pagos',text)

    def test_embedded_pdf_actions_are_not_exported(self):
        from pypdf.generic import DictionaryObject,NameObject,TextStringObject
        reader=PdfReader(BytesIO(source_pdf(1)));writer=PdfWriter();writer.append(reader)
        writer.pages[0][NameObject('/AA')]=DictionaryObject({NameObject('/O'):DictionaryObject({NameObject('/S'):NameObject('/JavaScript'),NameObject('/JS'):TextStringObject('app.alert("ficticio")')})})
        stream=BytesIO();writer.write(stream);self.attach(stream.getvalue())
        exported=PdfReader(BytesIO(receipts.render_pdf(self.data())))
        self.assertTrue(all('/AA' not in p for p in exported.pages))


class ReceiptHTTPTests(unittest.TestCase):
    def setUp(self):
        from app import make_server
        from http_test_support import authenticated_opener
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.server=make_server(self.root/'synthetic.sqlite3',0,seed=False);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root)
        self.cashier=authenticated_opener(self.server,self.root,'cashier','caja-qa');self.production=authenticated_opener(self.server,self.root,'production','produccion-qa')
        f=FictionalAccounts(Store(self.root/'synthetic.sqlite3'));s=f.supplier('FICTICIO HTTP');f.payment(s,1234)

    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def test_new_interface_assets_are_served_exactly(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('payment-receipts.js','payment-receipts.css','payables.js'):
            with urlopen(self.url+'/'+name) as response:
                self.assertEqual(response.read(),(root/'static'/name).read_bytes())
                self.assertTrue(response.headers['Content-Type'].startswith('text/'))

    def test_permission_boundaries_and_read_only_contract(self):
        for opener,status in [(None,401),(self.cashier,403),(self.production,403)]:
            for path in (receipts.PREFIX,receipts.PREFIX+'.pdf',receipts.PREFIX+'/preview'):
                with self.subTest(path=path,status=status),self.assertRaises(HTTPError) as error:(opener.open if opener else urlopen)(self.url+path)
                self.assertEqual(error.exception.code,status);error.exception.close()
        with self.partner.open(self.url+receipts.PREFIX+'?date=2026-01-02') as response:data=json.load(response)
        query=urlencode(dict(date=data['date'],ids=data['payments'][0]['key'],token=data['token']))
        with self.partner.open(self.url+receipts.PREFIX+'/preview?'+query) as response:
            preview=json.load(response);self.assertEqual(len(preview['payments']),1);self.assertNotIn('evidence',preview)
        with self.partner.open(self.url+receipts.PREFIX+'.pdf?'+query) as response:
            self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertTrue(response.headers['Content-Disposition'].startswith('inline'))
            self.assertIn('sin respaldo incorporable',PdfReader(BytesIO(response.read())).pages[0].extract_text())
        with self.partner.open(self.url+'/') as response:self.assertIn("frame-src 'self' blob:",response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as error:self.partner.open(Request(self.url+receipts.PREFIX,data=b'{}',headers={'X-ERP-Local':'1','Content-Type':'application/json'}))
        self.assertEqual(error.exception.code,405);error.exception.close()


if __name__=='__main__':unittest.main()
