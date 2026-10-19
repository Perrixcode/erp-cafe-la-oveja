"""Duplicate prevention includes paid invoices, edited identities and opening evidence."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4
from erp.store import Store
from erp.domain import DomainError
from erp import payables as ap, sii_purchases as sii, payable_baseline as baseline
from test_supplier_opening import fixture

HEAD='Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'

class ImportSelectionAndDuplicatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'erp.sqlite3')
    def command(self,action,data,target=None):return ap.execute(self.store,action,dict(data,request_id=str(uuid4())),'QA duplicados',target)
    def supplier(self,name='Proveedor QA',rut='76111111-1'):return self.command('supplier',dict(name=name,tax_id=rut))
    def invoice(self,supplier,**changes):return self.command('invoice',dict(dict(supplier_id=supplier['id'],document_type='invoice',folio='42',issued_on='2026-10-01',amount=10000,condition='credit'),**changes))
    def upload(self,*rows):return sii.upload(self.store,(HEAD+'\n'.join(rows)+'\n').encode(),'QA.csv','QA duplicados')
    def opening(self):
        path=self.root/'baseline.json';path.write_text(json.dumps(fixture()))
        baseline.initialize(self.store,path,'QA apertura',19200,5)
        path.unlink()
    def confirm(self,preview,rows,**extra):return sii.confirm(self.store,preview['id'],dict(rows=rows,confirmed=True,**extra),'QA duplicados')
    def test_subset_retry_and_conditions_never_infer_payment(self):
        preview=self.upload('33;76111111-1;Uno QA;42;01/10/2026;10000','33;76222222-2;Dos QA;43;01/10/2026;20000')
        self.assertEqual(self.confirm(preview,[2],conditions={'2':'credit'})['imported'],1)
        self.assertEqual(self.confirm(preview,[2],conditions={'2':'cash'})['already_imported'],1)
        invoices=ap.view(self.store)['invoices'];self.assertEqual(len(invoices),1);self.assertEqual(invoices[0]['condition'],'credit')
        self.assertEqual(sii.preview(self.store,preview['id'])['counts']['new'],1)
        self.assertEqual(self.confirm(preview,[3],conditions={'3':'cash'})['imported'],1)
        view=ap.view(self.store);self.assertEqual(view['totals']['balance'],0);self.assertTrue(all(r['payment_review'] for r in view['invoices']))
        self.assertEqual(sum(len(r['payments']) for r in view['invoices']),0)
    def test_paid_invoice_cannot_be_selected_or_recreated(self):
        supplier=self.supplier();invoice=self.invoice(supplier)
        self.command('payment',dict(amount=10000,paid_on='2026-10-01',method='transfer',confirmed=True,version=1),invoice['id'])
        preview=self.upload('33;76.111.111-1;Nombre cambiado QA;00042;01/10/2026;10000')
        self.assertEqual(preview['rows'][0]['status'],'duplicate');self.assertIn('Pagada',preview['rows'][0]['issue'])
        with self.assertRaises(DomainError):self.confirm(preview,[2])
        with self.assertRaises(DomainError):self.invoice(supplier,folio='00042')
        view=ap.view(self.store);self.assertEqual(len(view['invoices']),1);self.assertEqual(view['invoices'][0]['paid'],10000)
    def test_original_identity_survives_manual_folio_and_rut_corrections(self):
        supplier=self.supplier();invoice=self.invoice(supplier)
        self.command('invoice',dict(invoice,folio='99',reason='Corrección ficticia'),invoice['id'])
        self.command('supplier',dict(supplier,tax_id='76222222-2',name='Razón social corregida QA',reason='Corrección ficticia'),supplier['id'])
        preview=self.upload('33;76111111-1;Otro nombre QA;42;01/10/2026;10000')
        self.assertNotEqual(preview['rows'][0]['status'],'new')
        with self.assertRaises(DomainError):self.confirm(preview,[2])
        old_rut=self.supplier('Segundo proveedor QA','76111111-1')
        with self.assertRaises(DomainError):self.invoice(old_rut)
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_historic_paid_opening_is_checked_without_live_sheet(self):
        self.opening()
        preview=self.upload('33;76333333-3;Razón social diferente QA;1003;01/10/2026;1000')
        row=preview['rows'][0];self.assertEqual(row['status'],'conflict');self.assertTrue(row['possible_duplicate'])
        self.assertTrue(any(m['state']=='Pagada según apertura' for m in row['matches']))
        with self.assertRaises(DomainError):self.confirm(preview,[2])
        supplier=self.supplier('Proveedor C','76333333-3')
        with self.assertRaises(DomainError):self.invoice(supplier,folio='1003',amount=1000)
        self.assertEqual(len(ap.view(self.store)['invoices']),5)
    def test_unknown_rut_alias_and_missing_opening_folio_require_review(self):
        supplier=self.supplier('Nombre comercial QA','');self.invoice(supplier)
        p=self.upload('33;76111111-1;Nombre legal distinto QA;42;01/10/2026;10000')
        self.assertTrue(p['rows'][0]['possible_duplicate'])
        with self.assertRaises(DomainError):self.confirm(p,[2])
        self.opening_after_fresh_store()
        p=self.upload('33;76111111-1;Otra razón social QA;777;01/10/2026;7000')
        self.assertTrue(p['rows'][0]['possible_duplicate'])
    def opening_after_fresh_store(self):
        self.store=Store(self.root/'opening-only.sqlite3');self.opening()
    def test_same_folio_amount_for_different_known_ruts_is_not_duplicate(self):
        self.invoice(self.supplier())
        p=self.upload('33;76222222-2;Otro proveedor QA;42;01/10/2026;10000')
        self.assertEqual(p['rows'][0]['status'],'new');self.assertEqual(self.confirm(p,[2])['imported'],1)
        self.assertEqual(len(ap.view(self.store)['invoices']),2)
    def test_completing_rut_resolves_fuzzy_collision_for_other_supplier(self):
        supplier=self.supplier('Nombre comercial QA','');self.invoice(supplier)
        p=self.upload('33;76222222-2;Proveedor distinto QA;42;01/10/2026;10000')
        self.assertEqual(p['rows'][0]['status'],'conflict')
        self.command('supplier',dict(supplier,tax_id='76111111-1',reason='RUT confirmado QA'),supplier['id'])
        self.assertEqual(sii.preview(self.store,p['id'])['rows'][0]['status'],'new')
        self.assertEqual(self.confirm(p,[2])['imported'],1)
    def test_stale_preview_rechecks_whole_selection_atomically(self):
        p=self.upload('33;76111111-1;Uno QA;42;01/10/2026;10000','33;76222222-2;Dos QA;43;01/10/2026;20000')
        self.invoice(self.supplier())
        with self.assertRaises(DomainError):self.confirm(p,[3,2])
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_concurrent_distinct_files_only_create_one_invoice(self):
        a=self.upload('33;76111111-1;Uno QA;42;01/10/2026;10000');b=self.upload('33;76111111-1;Nombre alternativo QA;00042;01/10/2026;10000')
        barrier=threading.Barrier(2);results=[]
        def worker(preview):
            barrier.wait()
            try:results.append(self.confirm(preview,[2])['imported'])
            except DomainError as error:results.append(error.status)
        threads=[threading.Thread(target=worker,args=(p,)) for p in (a,b)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(results,[1,409]);self.assertEqual(len(ap.view(self.store)['invoices']),1)
    def test_invalid_selection_metadata_cannot_create_any_invoice(self):
        p=self.upload('33;76111111-1;Uno QA;42;01/10/2026;10000')
        for rows,conditions in [([],{}),([2,2],{}),([2],{'3':'cash'}),([2],{'2':'paid'}),([2],{'2':True}),([2],[])]:
            with self.subTest(rows=rows,conditions=conditions),self.assertRaises(DomainError):self.confirm(p,rows,conditions=conditions)
        self.assertEqual(ap.view(self.store)['invoices'],[])
