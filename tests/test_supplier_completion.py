"""Regression contracts for rereading and supplier-facing PDF, synthetic data only."""
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from pypdf import PdfReader
from erp import document_intake as di,payables as ap,procurement as pc,purchase_pdf,invoice_ocr
from erp.store import Store
from erp.domain import DomainError
from erp.ocr_descriptions import spacing

class ReadingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=Store(Path(temp.name)/'synthetic.sqlite3')
        self.content=b'%PDF-1.4\nFICTIONAL\n%%EOF'
        with patch.object(di,'extract',return_value=('Folio: 701','extracted','ficticio')):self.draft=di.upload(self.store,self.content,'ficticio.pdf','QA')
        self.review={'invoice':{'folio':'HUMANO'},'lines':[{'description':'Corrección humana'}]}
        ap.execute(self.store,'document_save',dict(version=1,reason='Revisión ficticia',review=self.review,request_id=str(uuid4())),'QA',self.draft['id'])
    def candidate(self,status='extracted'):
        with patch.object(di,'extract',return_value=('Folio: 702',status,'ficticio')):
            return di.reextract(self.store,self.draft['id'],dict(version=2,request_id=str(uuid4())),'QA')
    def current(self):
        with self.store.connect() as db:return di.draft_view(db,self.draft['id'])
    def apply(self,candidate,**changes):
        body=dict(version=2,candidate_id=candidate['candidate_id'],mode='preserve',confirmed=True,reason='Comparación ficticia',request_id=str(uuid4()));body.update(changes)
        return ap.execute(self.store,'document_apply_reading',body,'QA',self.draft['id'])
    def test_preview_does_not_replace_human_or_old_text(self):
        candidate=self.candidate();r=self.current();self.assertEqual(r['version'],2);self.assertEqual(r['extracted_text'],'Folio: 701');self.assertEqual(r['review'],self.review)
        self.assertEqual(candidate['suggested']['folio'],'702')
    def test_preserve_and_replay_keep_original_and_unsaved_fields(self):
        candidate=self.candidate();request=str(uuid4());snapshot={'invoice':{'folio':'SIN GUARDAR'}}
        one=self.apply(candidate,review=snapshot,request_id=request);self.assertEqual(one,self.apply(candidate,review=snapshot,request_id=request))
        r=self.current();self.assertEqual(r['version'],3);self.assertEqual(r['review'],snapshot);self.assertEqual(r['suggested']['folio'],'702')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM ap_document_drafts').fetchone()[0],self.content)
            before=json.loads(db.execute("SELECT before_json FROM ap_events WHERE action='document_reading_applied'").fetchone()[0])
            self.assertEqual(before['review'],self.review);self.assertEqual(before['unsaved_review'],snapshot);self.assertEqual(before['extracted_text'],'Folio: 701')
    def test_replace_requires_separate_confirmation_and_retains_audit(self):
        c=self.candidate()
        with self.assertRaises(DomainError):self.apply(c,mode='replace')
        self.assertEqual(self.current()['review'],self.review)
        self.apply(c,mode='replace',replace_confirmed=True);self.assertIsNone(self.current()['review'])
    def test_failure_stale_other_actor_and_other_draft_rejected(self):
        with self.assertRaises(DomainError):self.apply(self.candidate('failed'))
        c=self.candidate()
        for changes in ({'confirmed':False},{'version':1},{'candidate_id':'unknown-candidate'}):
            with self.assertRaises(DomainError):self.apply(c,**changes)
        with self.assertRaises(DomainError):
            ap.execute(self.store,'document_apply_reading',dict(version=2,candidate_id=c['candidate_id'],mode='preserve',confirmed=True,reason='QA',request_id=str(uuid4())),'OTRO',self.draft['id'])
        ap.execute(self.store,'document_save',dict(version=2,reason='Cambio humano',review=self.review,request_id=str(uuid4())),'QA',self.draft['id'])
        with self.assertRaises(DomainError):self.apply(c,version=3)
        self.assertEqual(self.current()['extracted_text'],'Folio: 701')
    def test_no_financial_or_stock_side_effects(self):
        self.apply(self.candidate());self.assertEqual(ap.view(self.store)['invoices'],[]);self.assertEqual(pc.overview(self.store)['movements'],[])
    def test_general_spacing_and_structured_evidence_keep_uncertainty(self):
        self.assertEqual(spacing('Producto2x3kg'),'Producto 2x3 kg');self.assertEqual(spacing('Formato Sx1 Kio'),'Formato Sx 1 Kio')
        line='ABC123\tProducto nuevo 2x3 kg\t2\t1000\t2000'
        evidence={'reader_version':'fixture','text':line,'raw_text':'texto crudo','line_readings':[{'raw':'Producto nueuo','normalized':'Producto nuevo 2x3 kg','alternatives':[],'needs_review':True}]}
        result=invoice_ocr.suggest(json.dumps(evidence))['lines'][0]
        self.assertEqual(result['quantity'],'2');self.assertEqual(result['format'],'2x3 kg');self.assertEqual(result['reading']['raw'],'Producto nueuo');self.assertTrue(result['needs_review'])

class PurchasePDFTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=Store(Path(temp.name)/'synthetic.sqlite3')
        self.supplier=ap.execute(self.store,'supplier',dict(name='Café y Piñón ficticio',contact='Vendedora ficticia',bank='BANCO INTERNO NO EXPORTAR',account_number='SECRETO FICTICIO',request_id=str(uuid4())),'QA')
    def plan(self):
        return pc.execute(self.store,'agenda_save',dict(supplier_id=self.supplier['id'],scheduled_on='2026-10-14',expected_on='2026-10-17',notes='Entregar café y piñón. Observación ficticia.',reason='QA',lines=[dict(description='Café molido caja 6x1 kg',quantity='2',item_id=None,unit_id=None)],request_id=str(uuid4())),'QA')
    def test_real_pdf_accents_allowlist_and_no_writes(self):
        plan=self.plan()
        with self.store.connect() as db:
            before='\n'.join(db.iterdump());content,name=purchase_pdf.export(db,'agenda',plan['id']);self.assertEqual(before,'\n'.join(db.iterdump()))
        self.assertTrue(content.startswith(b'%PDF-'));self.assertEqual(name,'pedido-ag-1.pdf')
        text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(content)).pages)
        for value in ('BORRADOR','Café y Piñón','Vendedora ficticia','2026-10-17','caja 6x1 kg','Por confirmar','Observación'):self.assertIn(value,text)
        for value in ('BANCO INTERNO','SECRETO FICTICIO','Saldo','Costo'):self.assertNotIn(value,text)
    def test_multipage_repeats_headers_and_every_product_once(self):
        plan=self.plan()
        with self.store.connect() as db:data=purchase_pdf.payload(db,'agenda',plan['id'])
        data['lines']=[dict(description=('Producto ficticio %03d · ' % n)+'Descripción extensa con café y piñón. '*6,quantity=str(n+1),unit='Caja 6 × 1 kg') for n in range(100)]
        reader=PdfReader(BytesIO(purchase_pdf.render(data)));self.assertGreater(len(reader.pages),2)
        pages=[p.extract_text() for p in reader.pages];text='\n'.join(pages)
        for n in range(100):self.assertEqual(text.count('Producto ficticio %03d'%n),1)
        for page in pages:
            if 'Producto ficticio' in page:self.assertIn('CANTIDAD',page);self.assertIn('PRODUCTO',page)
        Path(tempfile.gettempdir(),'pedido-multipagina-ficticio.pdf').write_bytes(purchase_pdf.render(data))
    def test_missing_document_does_not_export(self):
        with self.store.connect() as db,self.assertRaises(DomainError):purchase_pdf.export(db,'agenda',999)
