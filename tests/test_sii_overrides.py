"""Reviewed duplicate exceptions retain payment history and transactional guards."""
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

HEAD = 'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'


class SiiOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = Store(self.root/'qa.sqlite3')

    def command(self, action, data, target=None):
        return ap.execute(self.store, action, dict(data, request_id=str(uuid4())), 'QA override', target)

    def upload(self, *rows):
        return sii.upload(self.store, (HEAD+'\n'.join(rows)+'\n').encode(), 'ficticio.csv', 'QA override')

    def decision(self, preview, number=2):
        row = next(r for r in preview['rows'] if r['row']==number)
        return dict(confirmed=True, action=row['review_action'], token=row['review_token'], reason='Documentos revisados con respaldo ficticio')

    def confirm(self, preview, rows=(2,), overrides=None):
        return sii.confirm(self.store, preview['id'], dict(rows=list(rows), confirmed=True,
                           overrides=overrides if overrides is not None else {'2': self.decision(preview)}), 'QA override')

    def invoice(self):
        supplier = self.command('supplier', dict(name='Proveedor QA', tax_id='76111111-1'))
        return self.command('invoice', dict(supplier_id=supplier['id'], document_type='invoice', folio='42',
                            amount=10000, issued_on='2026-10-01', condition='credit'))

    def opening(self):
        path = self.root/'baseline.json'; path.write_text(json.dumps(fixture()))
        baseline.initialize(self.store, path, 'QA apertura', 19200, 5)

    def test_paid_exact_duplicate_links_without_new_debt_or_mutation(self):
        invoice = self.invoice()
        self.command('payment', dict(version=1, amount=10000, paid_on='2026-10-01', method='transfer', confirmed=True), invoice['id'])
        before = ap.view(self.store)['invoices']
        p = self.upload('33;76111111-1;Proveedor QA;42;01/10/2026;10000')
        self.assertEqual(p['rows'][0]['review_action'], 'link')
        self.assertEqual(p['rows'][0]['matches'][0]['paid'], 10000)
        result = self.confirm(p)
        self.assertEqual((result['imported'], result['linked']), (0, 1))
        self.assertEqual(ap.view(self.store)['invoices'], before)
        self.assertEqual(self.confirm(p)['already_imported'], 1)
        row = sii.preview(self.store, p['id'])['rows'][0]
        self.assertTrue(row['already_imported']); self.assertFalse(row['can_override'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_import_resolutions').fetchone()[0], 1)
            event = db.execute("SELECT actor,reason FROM ap_events WHERE action='sii_source_linked'").fetchone()
            self.assertEqual(event['actor'], 'QA override'); self.assertTrue(event['reason'])

    def test_possible_paid_opening_can_be_overridden_but_requires_balance_review(self):
        self.opening()
        p = self.upload('33;76333333-3;Otra razón social QA;1003;01/10/2026;1000')
        self.assertEqual(p['rows'][0]['review_action'], 'create')
        with self.assertRaises(DomainError): self.confirm(p, overrides={})
        before = ap.view(self.store)['totals']['balance']
        self.assertEqual(self.confirm(p)['imported'], 1)
        view = ap.view(self.store); self.assertEqual(view['totals']['balance'], before)
        row = next(r for r in view['invoices'] if r['source'])
        self.assertTrue(row['payment_review']); self.assertEqual(row['payments'], [])
        with self.store.connect() as db:
            event = db.execute("SELECT before_json,reason FROM ap_events WHERE action='sii_duplicate_overridden'").fetchone()
            self.assertIn('Pagada según apertura', event['before_json']); self.assertTrue(event['reason'])

    def test_missing_or_invalid_confirmation_cannot_override(self):
        self.opening(); p = self.upload('33;76333333-3;Otra razón social QA;1003;01/10/2026;1000')
        for change in ({'reason':''}, {'reason':True}, {'confirmed':False}, {'token':'stale'}, {'action':'link'}):
            with self.subTest(change=change), self.assertRaises(DomainError):
                self.confirm(p, overrides={'2':dict(self.decision(p), **change)})
        with self.assertRaises(DomainError): self.confirm(p, overrides={'3':self.decision(p)})
        self.assertEqual(len(ap.view(self.store)['invoices']), 5)

    def test_paid_state_change_invalidates_entire_selection(self):
        invoice = self.invoice()
        p = self.upload('33;76111111-1;Proveedor QA;42;01/10/2026;10000',
                        '33;76222222-2;Proveedor nuevo QA;43;01/10/2026;20000')
        self.command('payment', dict(version=1, amount=1000, paid_on='2026-10-01', method='transfer', confirmed=True), invoice['id'])
        with self.assertRaises(DomainError): self.confirm(p, rows=(3,2))
        self.assertEqual(len(ap.view(self.store)['invoices']), 1)
        refreshed = sii.preview(self.store, p['id'])
        self.assertEqual(self.confirm(refreshed, rows=(2,3))['imported'], 1)

    def test_file_duplicates_allow_only_one_reviewed_row(self):
        line = '33;76111111-1;Proveedor QA;42;01/10/2026;10000'
        p = self.upload(line, line)
        with self.assertRaises(DomainError):
            self.confirm(p, rows=(2,3), overrides={str(r):self.decision(p,r) for r in (2,3)})
        self.assertEqual(ap.view(self.store)['invoices'], [])
        self.assertEqual(self.confirm(p)['imported'], 1)

    def test_invalid_and_unsupported_rows_cannot_be_forced(self):
        p = self.upload('61;76111111-1;Proveedor QA;42;01/10/2026;1000',
                        '33;76111111-1;Proveedor QA;43;01/10/2026;invalid')
        for number in (2,3):
            self.assertFalse(p['rows'][number-2].get('can_override'))
            with self.assertRaises(DomainError):
                self.confirm(p, rows=(number,), overrides={str(number):dict(confirmed=True,reason='QA',token='fake',action='create')})

    def test_concurrent_force_from_two_files_still_creates_one_invoice(self):
        self.opening()
        a = self.upload('33;76333333-3;Otra razón social QA;1003;01/10/2026;1000')
        b = self.upload('33;76333333-3;Otra razón social distinta QA;1003;01/10/2026;1000')
        barrier = threading.Barrier(2); results = []
        def run(p):
            barrier.wait()
            try: results.append(self.confirm(p)['imported'])
            except DomainError as error: results.append(error.status)
        workers = [threading.Thread(target=run,args=(p,)) for p in (a,b)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertCountEqual(results, [1,409]); self.assertEqual(len(ap.view(self.store)['invoices']), 6)

    def test_link_keeps_differences_and_void_state(self):
        invoice = self.invoice()
        self.command('void_invoice', dict(version=1, reason='Anulación ficticia'), invoice['id'])
        before = ap.view(self.store)['invoices']
        p = self.upload('33;76111111-1;Proveedor QA;42;02/10/2026;12000')
        self.assertEqual(self.confirm(p)['linked'], 1)
        self.assertEqual(ap.view(self.store)['invoices'], before)

    def test_same_name_supplier_without_rut_requires_explicit_decision(self):
        self.command('supplier', dict(name='Proveedor QA'))
        p = self.upload('33;76111111-1;Proveedor QA;42;01/10/2026;10000')
        self.assertTrue(p['rows'][0]['supplier_matches'])
        self.assertEqual(self.confirm(p)['imported'], 1)
        self.assertEqual(len(ap.view(self.store)['suppliers']), 2)
