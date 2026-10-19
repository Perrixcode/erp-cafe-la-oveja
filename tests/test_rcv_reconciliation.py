"""RCV reconciliation with fictional documents and disposable databases only."""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from erp import payables as ap, sii_purchases as sii, sii_review, document_intake as intake, invoice_ocr
from erp.domain import DomainError
from erp.store import Store
from test_sii_purchases import csv_file, document, tax_detail


class RcvReconciliationTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.store=Store(Path(folder.name)/'synthetic.sqlite3')

    def command(self,action,body,target=None):
        return ap.execute(self.store,action,dict(body,request_id=str(uuid4())),'QA',target)

    def test_printed_folio_survives_import_and_numeric_identity_prevents_duplicates(self):
        content=csv_file([document(**{'Folio':'000123'})])
        preview=sii.upload(self.store,content,'202610-ficticio.csv','QA')
        self.assertEqual(ap.view(self.store)['invoices'],[])
        row=preview['rows'][0]
        self.assertEqual((row['folio'],row['identity']),('000123',['761111111','33','123']))
        body=dict(rows=[row['row']],confirmed=True,review_version=preview['review']['version'])
        sii.confirm(self.store,preview['id'],body,'QA')
        self.assertEqual(sii.confirm(self.store,preview['id'],body,'QA')['already_imported'],1)
        invoice=ap.view(self.store)['invoices'][0]
        self.assertEqual(invoice['folio'],'000123')
        self.assertEqual(invoice['source']['source_folio'],'000123')
        self.assertEqual(invoice['issued_on'],'2026-07-31')
        self.assertEqual(invoice['due_on'],'')
        self.assertEqual(invoice['condition'],'unknown')
        self.assertTrue(invoice['payment_review'])
        self.assertFalse(invoice['overdue'])
        self.command('reconcile',dict(version=invoice['version'],previous_paid=5000,confirmed=True,reason='Antecedente ficticio'),invoice['id'])
        before=ap.view(self.store)['invoices']
        again=sii.upload(self.store,csv_file([document(**{'Folio':'123'})]),'otro-ficticio.csv','QA')
        self.assertEqual(again['rows'][0]['status'],'duplicate')
        self.assertEqual(ap.view(self.store)['invoices'],before)
        with self.assertRaises(DomainError):
            self.command('invoice',dict(supplier_id=invoice['supplier_id'],document_type='invoice',folio='00123',amount=11900,issued_on='2026-07-31',condition='unknown'))

    def test_credit_and_debit_notes_are_classified_without_debt_or_auto_application(self):
        preview=sii.upload(self.store,csv_file([document(),document(**{'Tipo Doc':'61'}),document(**{'Tipo Doc':'56'})]),'ficticio.csv','QA')
        self.assertEqual([r['document_kind'] for r in preview['rows']],['invoice','credit_note','debit_note'])
        self.assertEqual(preview['document_totals']['credit_note'],dict(count=1,amount=11900))
        self.assertEqual(preview['review']['progress']['pending']['amount'],11900)
        self.assertEqual(preview['counts']['unsupported'],2)
        for row in preview['rows'][1:]:
            self.assertFalse(row.get('can_override',False))
            with self.assertRaises(DomainError):sii.confirm(self.store,preview['id'],dict(rows=[row['row']],confirmed=True),'QA')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_invoices').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_account_entries').fetchone()[0],0)

    def test_duplicate_identity_uses_rut_type_and_folio_without_losing_zeroes(self):
        rows=sii.parse(csv_file([document(**{'Folio':'000123'}),document(),
            document(**{'Tipo Doc':'34'}),document(**{'RUT Proveedor':'76222222-2'}),
            document(**{'Tipo Doc':'61'}),document(**{'Tipo Doc':'61','Folio':'00123'})]))
        self.assertEqual([r['status'] for r in rows],['conflict','conflict','new','new','unsupported','unsupported'])
        self.assertEqual(rows[0]['folio'],'000123')
        self.assertEqual(rows[-1]['file_duplicates'],[6,7])
        with self.store.connect() as db:
            sii.classify(db,rows)
        self.assertFalse(any(r.get('can_override') for r in rows[-2:]))

    def test_money_and_dates_are_row_errors_and_confirmation_is_atomic(self):
        for field,value in [('Monto Neto','12.345'),('Monto IVA Recuperable','-1'),('Monto Total','1,5'),('Fecha Docto','31/02/2026')]:
            with self.subTest(field=field):
                preview=sii.upload(self.store,csv_file([document(),document(**{field:value,'Folio':'124'})]),'ficticio.csv','QA')
                self.assertEqual(preview['rows'][1]['status'],'error')
                self.assertEqual(preview['rows'][1]['row'],3)
                self.assertTrue(preview['rows'][1]['issue'])
                with self.assertRaises(DomainError):sii.confirm(self.store,preview['id'],dict(rows=[2,3],confirmed=True),'QA')
                self.assertEqual(ap.view(self.store)['invoices'],[])
        with self.assertRaises(DomainError):sii.confirm(self.store,preview['id'],dict(rows=[2],confirmed=False),'QA')

    def test_tax_continuations_are_preserved_and_not_added_as_documents(self):
        content=csv_file([document(**{'Codigo Otro Impuesto':'27','Valor Otro Impuesto':'50','Tasa Otro Impuesto':'10'}),tax_detail()])
        preview=sii.upload(self.store,content,'ficticio.csv','QA')
        self.assertEqual(len(preview['rows']),1)
        self.assertEqual(preview['tax_detail_count'],1)
        self.assertEqual(preview['rows'][0]['amount'],11900)
        self.assertEqual(preview['rows'][0]['original']['Valor Otro Impuesto'],'50')
        self.assertEqual(preview['rows'][0]['tax_details'][0]['original']['Valor Otro Impuesto'],'100')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT content FROM ap_import_batches').fetchone()[0],content)

    def test_replacement_keeps_review_across_padded_folio_and_never_deletes_invoices(self):
        initial=sii.upload(self.store,csv_file([document(**{'Folio':'000123'}),document(**{'Folio':'124'})]),'inicial.csv','QA')
        draft=initial['review']['draft'];draft['details']={'2':dict(note='Nota humana ficticia',follow_up='',discard_reason='')}
        sii_review.save(self.store,initial['id'],dict(version=initial['review']['version'],draft=draft),'QA')
        current=sii.preview(self.store,initial['id'])
        with self.store.connect() as db:
            comparison=sii_review.comparison(db,initial['id'],csv_file([document()]),'actualizado.csv',current['review']['version'])[0]
        self.assertEqual(comparison['counts']['same'],1)
        self.assertEqual(comparison['counts']['missing'],1)
        self.assertEqual(ap.view(self.store)['invoices'],[])


class StructuredReviewTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.store=Store(Path(folder.name)/'synthetic.sqlite3')
        self.text='Proveedor Ficticio SpA\nFACTURA ELECTRONICA\nFolio: 000800\nFecha Emision: 01/10/2026\nQA100  Producto ficticio caja 5x1 kg  2  5000  10000\nMonto Neto: 10000\nMonto IVA: 1900\nMonto Total: 11900'

    def draft(self,suffix='A'):
        raw=json.dumps(dict(reader_version='fixture',text=self.text,raw_text=self.text,line_readings=[]))
        with patch.object(intake,'extract',return_value=(raw,'extracted','Ficticio')):
            return intake.upload(self.store,b'%PDF-1.4\nFICTICIO '+suffix.encode()+b'\n%%EOF',suffix+'.pdf','QA')

    def test_structured_reading_supplies_fields_and_lines_while_partial_review_is_retained(self):
        draft=self.draft();partial=dict(draft_id=draft['id'],invoice={'folio':'000999'})
        ap.execute(self.store,'document_save',dict(version=draft['version'],review=partial,reason='Avance humano',request_id=str(uuid4())),'QA',draft['id'])
        with self.store.connect() as db:value=intake.draft_view(db,draft['id'])
        self.assertEqual(value['review'],partial)
        self.assertEqual(value['suggested']['folio'],'000800')
        self.assertEqual(value['suggested']['amount'],11900)
        self.assertEqual(value['suggested']['issued_on'],'2026-10-01')
        self.assertEqual(value['suggested']['lines'][0]['quantity'],'2')
        self.assertEqual(value['suggested']['lines'][0]['amount_clp'],10000)
        self.assertEqual(ap.view(self.store)['invoices'],[])

    def test_foreign_draft_review_rejected_and_discarded_document_never_reprocessed(self):
        a,b=self.draft('A'),self.draft('B')
        with self.assertRaises(DomainError):
            ap.execute(self.store,'document_save',dict(version=1,review={'draft_id':a['id']},reason='Ficticio',request_id=str(uuid4())),'QA',b['id'])
        ap.execute(self.store,'document_discard',dict(version=1,reason='Ficticio',request_id=str(uuid4())),'QA',a['id'])
        with patch.object(intake,'extract',side_effect=AssertionError('Do not reprocess discarded originals')):
            with self.assertRaises(DomainError):intake.reextract(self.store,a['id'],dict(version=2,request_id=str(uuid4())),'QA')
            same=intake.upload(self.store,b'%PDF-1.4\nFICTICIO A\n%%EOF','A.pdf','QA')
            self.assertEqual(same['state'],'discarded')
