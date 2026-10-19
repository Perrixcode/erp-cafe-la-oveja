"""Supplier creation is atomic with invoices and respects existing identities."""
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4
from erp import payables as ap, sii_purchases as sii
from erp.domain import DomainError
from erp.store import Store


class InvoiceSupplierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name)/'qa.sqlite3')

    def do(self, action, data, target=None):
        return ap.execute(self.store, action, dict(request_id=str(uuid4()), **data), 'QA proveedor', target)

    def invoice_data(self, **changes):
        return dict(dict(new_supplier={'name': 'Proveedor nuevo QA', 'tax_id': '76.333.333-3'},
                         document_type='invoice', folio='501', issued_on='2026-10-01',
                         amount=10000, condition='credit'), **changes)

    def test_invoice_creates_supplier_and_audits_both_atomically(self):
        result = self.do('invoice', self.invoice_data())
        view = ap.view(self.store)
        self.assertEqual(len(view['suppliers']), 1)
        supplier = view['suppliers'][0]
        self.assertEqual((supplier['tax_id'], supplier['name'], result['supplier_id']),
                         ('763333333', 'Proveedor nuevo QA', supplier['id']))
        self.assertEqual(supplier['balance'], 10000)
        self.assertCountEqual([e['action'] for e in ap.history(self.store, result['id'])['events']],
                              ['supplier_saved', 'invoice_saved'])

    def test_existing_rut_is_reused_without_changing_supplier_details(self):
        existing = self.do('supplier', dict(name='Nombre original QA', tax_id='76333333-3', bank='Banco ficticio', account_number='1234'))
        result = self.do('invoice', self.invoice_data())
        self.assertEqual(result['supplier_id'], existing['id'])
        with self.store.connect() as db:
            self.assertEqual(ap.record(db, 'ap_suppliers', existing['id']), existing)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_suppliers').fetchone()[0], 1)

    def test_missing_rut_allowed_but_ambiguous_name_requires_selection(self):
        self.do('invoice', self.invoice_data(new_supplier={'name': 'Proveedor Ágil QA'}))
        with self.assertRaises(DomainError) as raised:
            self.do('invoice', self.invoice_data(folio='502', new_supplier={'name':'proveedor agil  qa','tax_id':'76333333-3'}))
        self.assertEqual(raised.exception.status, 409)
        with self.assertRaises(DomainError):
            self.do('invoice', self.invoice_data(folio='502', new_supplier={'name':'Proveedor Ágil QA'}))
        self.assertEqual(len(ap.view(self.store)['suppliers']), 1)

    def test_distinct_ruts_can_share_a_name(self):
        self.do('invoice', self.invoice_data())
        self.do('invoice', self.invoice_data(folio='502', new_supplier={'name':'Proveedor nuevo QA','tax_id':'76444444-4'}))
        self.assertEqual(len(ap.view(self.store)['suppliers']), 2)

    def test_invalid_invoice_rolls_back_supplier_and_audit(self):
        for changes in ({'amount': 0}, {'document_type': 'unknown'}, {'due_on': '2026-09-30'}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.do('invoice', self.invoice_data(**changes))
        with self.store.connect() as db:
            for table in ('ap_suppliers', 'ap_invoices', 'ap_events', 'ap_requests'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)

    def test_duplicate_invoice_still_blocked_when_supplier_is_supplied_inline(self):
        result = self.do('invoice', self.invoice_data())
        self.do('payment', dict(version=1, amount=10000, paid_on='2026-10-01', method='transfer', confirmed=True), result['id'])
        with self.assertRaises(DomainError):
            self.do('invoice', self.invoice_data(folio='000501'))
        view = ap.view(self.store)
        self.assertEqual((len(view['suppliers']), len(view['invoices']), view['invoices'][0]['paid']), (1, 1, 10000))

    def test_retry_and_concurrent_invoices_share_one_supplier(self):
        request = dict(self.invoice_data(), request_id=str(uuid4()))
        first = ap.execute(self.store, 'invoice', request, 'QA proveedor')
        self.assertEqual(first, ap.execute(self.store, 'invoice', request, 'QA proveedor'))
        barrier = threading.Barrier(2); results = []; failures = []
        def worker(folio):
            barrier.wait()
            try: results.append(self.do('invoice', self.invoice_data(folio=folio)))
            except Exception as err: failures.append(str(err))
        threads = [threading.Thread(target=worker, args=(folio,)) for folio in ('502', '503')]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['supplier_id']==first['supplier_id'] for r in results))
        self.assertEqual(len(ap.view(self.store)['suppliers']), 1)

    def test_conflicting_or_invalid_supplier_input_creates_nothing(self):
        for changes in ({'supplier_id':1}, {'new_supplier':None}, {'new_supplier':{'name':''}},
                        {'new_supplier':{'name':'QA', 'tax_id':'invalid'}}):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.do('invoice', self.invoice_data(**changes))
        self.assertEqual(ap.view(self.store)['suppliers'], [])

    def test_forced_sii_creation_adds_supplier_only_on_final_confirmation(self):
        header = 'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'
        row = '33;76333333-3;Proveedor forzado QA;600;01/10/2026;15000\n'
        preview = sii.upload(self.store, (header+row+row).encode(), 'ficticio.csv', 'QA proveedor')
        candidate = preview['rows'][0]
        self.assertTrue(candidate['can_override'])
        self.assertEqual(ap.view(self.store)['suppliers'], [])
        with self.assertRaises(DomainError):
            sii.confirm(self.store, preview['id'], dict(rows=[2], confirmed=True), 'QA proveedor')
        self.assertEqual(ap.view(self.store)['suppliers'], [])
        request = dict(rows=[2], confirmed=True, overrides={'2':dict(confirmed=True, reason='Repetida en el archivo; importar solo una', action=candidate['review_action'], token=candidate['review_token'])})
        self.assertEqual(sii.confirm(self.store, preview['id'], request, 'QA proveedor')['imported'], 1)
        self.assertEqual(sii.confirm(self.store, preview['id'], request, 'QA proveedor')['already_imported'], 1)
        view = ap.view(self.store)
        self.assertEqual(len(view['suppliers']), 1)
        self.assertEqual(view['invoices'][0]['supplier_id'], view['suppliers'][0]['id'])
        self.assertTrue(view['invoices'][0]['payment_review'])

    def test_sii_multiple_invoices_same_new_rut_create_one_supplier(self):
        content = 'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n33;76333333-3;Proveedor nuevo QA;701;01/10/2026;10000\n33;76333333-3;Proveedor nuevo QA;702;01/10/2026;12000\n'
        preview = sii.upload(self.store, content.encode(), 'ficticio.csv', 'QA proveedor')
        self.assertEqual(sii.confirm(self.store, preview['id'], dict(rows=[2,3], confirmed=True), 'QA proveedor')['imported'], 2)
        view = ap.view(self.store)
        self.assertEqual(len(view['suppliers']), 1)
        self.assertEqual(len({r['supplier_id'] for r in view['invoices']}), 1)
