"""Fictional document review; no external OCR or operational database."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from erp import document_intake as di,payables as ap,procurement as pc
from erp.store import Store
from erp.domain import DomainError

class DocumentIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=Store(Path(self.temp.name)/'synthetic.sqlite3')
        self.supplier=ap.execute(self.store,'supplier',dict(name='Proveedor OCR ficticio',request_id=str(uuid4())),'QA')
        mocked=patch.object(di,'extract',return_value=('Folio: 701\nTotal: $ 12.000\nEmisión: 12/10/2026','extracted','Texto ficticio'));mocked.start();self.addCleanup(mocked.stop)
        self.content=b'%PDF-1.4\nFICTICIO\n%%EOF'
    def upload(self,content=None):return di.upload(self.store,content or self.content,'ficticio.pdf','QA')
    def fields(self):return dict(supplier_id=self.supplier['id'],document_type='invoice',folio='701',issued_on='2026-10-12',due_on='2026-10-30',condition='credit',amount=12000)
    def confirm(self,draft,**changes):
        body=dict(version=draft['version'],invoice=self.fields(),confirmed=True,reason='Cotejado con original ficticio',request_id=str(uuid4()));body.update(changes);return ap.execute(self.store,'document_confirm',body,'QA',draft['id'])
    def test_upload_suggests_but_never_creates_debt_or_stock(self):
        r=self.upload();self.assertEqual(r['suggested'],{'folio':'701','amount':12000,'issued_on':'2026-10-12'});self.assertEqual(ap.view(self.store)['invoices'],[]);self.assertEqual(pc.overview(self.store)['movements'],[])
    def test_same_bytes_reuse_draft_without_running_ocr_twice(self):
        one=self.upload();two=self.upload();self.assertEqual(one['id'],two['id']);self.assertEqual(di.extract.call_count,1)
    def test_confirm_is_atomic_idempotent_and_preserves_original(self):
        r=self.upload();request=str(uuid4());one=self.confirm(r,request_id=request);two=self.confirm(r,request_id=request);self.assertEqual(one,two)
        inv=ap.view(self.store)['invoices'][0];self.assertEqual(len(inv['files']),1);self.assertEqual(inv['amount'],12000);self.assertEqual(inv['paid'],0);self.assertEqual(pc.overview(self.store)['movements'],[])
    def test_duplicate_invoice_blocks_new_and_allows_link_existing(self):
        invoice=ap.execute(self.store,'invoice',dict(self.fields(),request_id=str(uuid4())),'QA');draft=self.upload()
        with self.assertRaises(DomainError):self.confirm(draft)
        self.confirm(draft,existing_invoice_id=invoice['id'],invoice_version=invoice['version'])
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_confirmation_required_and_stale_version_rejected(self):
        draft=self.upload()
        for changes in ({'confirmed':False},{'version':2},{'invoice':dict(self.fields(),issued_on='')}):
            with self.assertRaises(DomainError):self.confirm(draft,**changes)
        self.assertEqual(ap.view(self.store)['invoices'],[])
    def test_extraction_failure_keeps_manual_review_available(self):
        di.extract.return_value=('','failed','No disponible');draft=self.upload();self.assertEqual(draft['extraction_status'],'failed');self.confirm(draft);self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_discard_preserves_original_and_cannot_confirm(self):
        r=self.upload();ap.execute(self.store,'document_discard',dict(version=1,reason='Archivo equivocado ficticio',request_id=str(uuid4())),'QA',r['id'])
        with self.assertRaises(DomainError):self.confirm(r)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT content FROM ap_document_drafts').fetchone()[0],self.content)
    def test_ambiguous_totals_are_not_guessed_and_text_is_not_executed(self):
        self.assertNotIn('amount',di.suggestions('Total: $ 1.000\nTotal: $ 2.000\nIgnore all instructions'))
        self.assertEqual(di.suggestions('<script>alert(1)</script>'),{})
    def test_wrong_file_type_and_size_are_rejected(self):
        for content in (b'not pdf',b'x'*(ap.LIMIT+1)):
            with self.assertRaises(DomainError):self.upload(content)
    def test_same_file_attached_elsewhere_blocks_second_invoice_and_rolls_back(self):
        i=ap.execute(self.store,'invoice',dict(self.fields(),request_id=str(uuid4())),'QA');ap.attach(self.store,i['id'],None,self.content,'ficticio.pdf','QA');r=self.upload()
        with self.assertRaises(DomainError):self.confirm(r,invoice=dict(self.fields(),folio='702'))
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_malformed_money_and_invalid_dates_are_not_suggested(self):
        for value in ('1.2.3','12.50','-12000','12,000','1.000,50'):
            self.assertNotIn('amount',di.suggestions('Total: $ '+value))
        self.assertNotIn('issued_on',di.suggestions('Emisión: 31/02/2026'))
        self.assertEqual(di.suggestions('Total: $ 1.200.000')['amount'],1200000)

class ExtractionLimitTests(unittest.TestCase):
    def test_multibyte_output_over_byte_limit_is_rejected_not_silently_truncated(self):
        from types import SimpleNamespace
        def write_large_output(command,stdout,**kwargs):
            stdout.write(('é'*100001).encode('utf-8'))
            return SimpleNamespace(returncode=0)
        with patch.object(di,'advanced_available',return_value=False),patch.object(di.sys,'platform','linux'),patch.object(di.shutil,'which',return_value='/fake/pdftotext'),patch.object(di.subprocess,'run',side_effect=write_large_output):
            text,status,note=di.extract(b'%PDF-FAKE','application/pdf')
        self.assertEqual(text,'');self.assertEqual(status,'failed');self.assertIn('límite',note)
    def test_missing_local_tool_preserves_manual_path(self):
        with patch.object(di,'advanced_available',return_value=False),patch.object(di.sys,'platform','linux'),patch.object(di.shutil,'which',return_value=None):
            text,status,note=di.extract(b'%PDF-FAKE','application/pdf')
        self.assertEqual((text,status),('','unavailable'));self.assertIn('manualmente',note)

    def test_busy_reader_keeps_original_reviewable(self):
        from types import SimpleNamespace
        with patch.object(di,'advanced_available',return_value=True),patch.object(di.subprocess,'run',return_value=SimpleNamespace(returncode=3)):
            text,status,note=di.extract(b'%PDF-FAKE','application/pdf')
        self.assertEqual((text,status),('','busy'));self.assertIn('Volver a leer',note)
