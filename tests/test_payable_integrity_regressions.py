"""Synthetic regressions for invoice edits and supplier identity in reports."""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from erp import payable_baseline as baseline, payable_reports as reports
from erp import payables as ap, sii_purchases as sii
from erp.domain import DomainError
from erp.store import Store
from test_supplier_opening import fixture


class PayableIntegrityRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / 'regressions.sqlite3')

    def command(self, action, data, target=None):
        return ap.execute(self.store, action, dict(data, request_id=str(uuid4())), 'QA integridad', target)

    def supplier(self, name='Proveedor QA', rut='76111111-1'):
        return self.command('supplier', dict(name=name, tax_id=rut))

    def invoice(self, supplier, folio='42', amount=10000):
        return self.command('invoice', dict(supplier_id=supplier['id'], document_type='invoice',
                            folio=folio, amount=amount, issued_on='2026-01-01', condition='credit'))

    def current(self, key):
        return next(row for row in ap.view(self.store)['invoices'] if row['id'] == key)

    def edit_data(self, invoice, **changes):
        return dict(self.current(invoice['id']), reason='Corrección ficticia revisada', **changes)

    def edit(self, invoice, **changes):
        return self.command('invoice', self.edit_data(invoice, **changes), invoice['id'])

    def financial_snapshot(self):
        with self.store.connect() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('ap_invoices', 'ap_payments', 'ap_events', 'ap_requests',
                                  'ap_invoice_sources', 'ap_import_resolutions', 'ap_baseline_links')}

    def assert_edit_rejected_without_changes(self, invoice, **changes):
        before = self.financial_snapshot()
        body = dict(self.edit_data(invoice, **changes), request_id=str(uuid4()))
        for _ in range(2):
            with self.assertRaises(DomainError) as error:
                ap.execute(self.store, 'invoice', body, 'QA integridad', invoice['id'])
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(self.financial_snapshot(), before)

    def opening(self, snapshot=None, total=19200, count=5):
        path = self.root / 'synthetic-opening.json'
        path.write_text(json.dumps(snapshot or fixture()))
        baseline.initialize(self.store, path, 'QA apertura', total, count)

    def upload(self, line):
        header = 'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n'
        return sii.upload(self.store, (header+line+'\n').encode(), 'synthetic.csv', 'QA integridad')

    def test_edit_rejects_historical_identity_of_pending_paid_and_void_invoices(self):
        for state in ('pending', 'paid', 'void'):
            with self.subTest(state=state):
                self.store = Store(self.root / (state+'.sqlite3'))
                supplier = self.supplier()
                original = self.edit(self.invoice(supplier), folio='99')
                if state == 'paid':
                    self.command('payment', dict(version=original['version'], amount=10000,
                                 paid_on='2026-01-02', method='transfer', confirmed=True), original['id'])
                elif state == 'void':
                    self.command('void_invoice', dict(version=original['version'], reason='QA anulación'), original['id'])
                other = self.invoice(supplier, folio='43')
                self.assert_edit_rejected_without_changes(other, folio='00042')

    def test_edit_preserves_other_suppliers_historical_rut_identity(self):
        supplier = self.supplier()
        self.edit(self.invoice(supplier), folio='99')
        self.command('supplier', dict(supplier, tax_id='76222222-2', reason='RUT corregido QA'), supplier['id'])
        old_rut = self.supplier('Otra ficha QA', '76111111-1')
        other = self.invoice(old_rut, folio='43')
        self.assert_edit_rejected_without_changes(other, folio='42')

    def test_edit_excludes_own_history_and_retry_does_not_repeat_audit(self):
        original = self.invoice(self.supplier())
        original = self.edit(original, folio='99')
        body = dict(self.edit_data(original, folio='00042'), request_id=str(uuid4()))
        result = ap.execute(self.store, 'invoice', body, 'QA integridad', original['id'])
        self.assertEqual(result['folio'], '00042')  # Preserve the reviewed printed identifier.
        after = self.financial_snapshot()
        self.assertEqual(ap.execute(self.store, 'invoice', body, 'QA integridad', original['id']), result)
        self.assertEqual(self.financial_snapshot(), after)
        self.edit(original, due_on='2026-02-01')
        self.assertEqual(self.current(original['id'])['balance'], 10000)

    def test_edit_excludes_own_sii_source_and_keeps_csv_duplicate_protection(self):
        preview = self.upload('33;76111111-1;Proveedor QA;42;01/01/2026;10000')
        sii.confirm(self.store, preview['id'], dict(rows=[2], confirmed=True), 'QA integridad')
        original = ap.view(self.store)['invoices'][0]
        self.edit(original, folio='99')
        self.edit(original, folio='42', due_on='2026-02-01')
        self.assertEqual(sii.confirm(self.store, preview['id'], dict(rows=[2], confirmed=True), 'QA integridad')['already_imported'], 1)
        self.assertEqual(len(ap.view(self.store)['invoices']), 1)
        self.assertTrue(self.current(original['id'])['payment_review'])
        self.edit(original, folio='99')
        other = self.invoice(ap.view(self.store)['suppliers'][0], folio='43')
        self.assert_edit_rejected_without_changes(other, folio='42')

    def test_edit_respects_reviewed_sii_links_and_excludes_own_link(self):
        supplier = self.supplier()
        # Establish a reviewed source link while 42 is the current identity.
        # A replaced folio in edit history alone now offers a confirmed create.
        original = self.invoice(supplier)
        preview = self.upload('33;76111111-1;Proveedor QA;42;01/01/2026;10000')
        row = preview['rows'][0]
        decision = dict(confirmed=True, action='link', token=row['review_token'], reason='Mismo documento QA')
        result = sii.confirm(self.store, preview['id'], dict(rows=[2], confirmed=True, overrides={'2': decision}), 'QA integridad')
        self.assertEqual(result['linked'], 1)
        self.edit(original, folio='42')
        self.edit(original, folio='99')
        other = self.invoice(supplier, folio='43')
        self.assert_edit_rejected_without_changes(other, folio='42')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_import_resolutions').fetchone()[0], 1)

    def test_edit_rejects_paid_opening_evidence_without_an_operational_invoice(self):
        self.opening()
        other = self.invoice(self.supplier('Proveedor C', '76333333-3'), folio='777', amount=2222)
        self.assert_edit_rejected_without_changes(other, folio='1003', amount=1000)

    def test_edit_does_not_confuse_another_suppliers_unidentified_opening_amount(self):
        self.opening()
        other = self.invoice(self.supplier('Otro proveedor QA', '76444444-4'), folio='777', amount=2222)
        self.assertEqual(self.edit(other, amount=7000)['amount'],7000)

    def test_edit_does_not_confuse_same_suppliers_unidentified_opening_amount(self):
        self.opening()
        supplier=next(s for s in ap.view(self.store)['suppliers'] if s['name']=='Proveedor A')
        other=self.invoice(supplier,folio='777',amount=2222)
        self.assertEqual(self.edit(other,amount=7000)['amount'],7000)

    def test_miroh_1932_is_not_the_paid_opening_without_folio(self):
        snapshot=fixture()
        snapshot['ledger']=[snapshot['ledger'][0],dict(row=107,name='MIROH SPA',folio=None,amount=154545,paid=154545,balance=0,due=None,status='Pagado')]
        snapshot['form']=[dict(row=2,name='Proveedor A',folio=1001,amount=10000)]
        self.opening(snapshot,total=8000,count=1)
        supplier=self.supplier('Miroh spa','')
        with self.store.connect() as db:
            opening_before=db.execute('SELECT original FROM ap_initial_baselines').fetchone()[0]
            payments_before=[tuple(r) for r in db.execute('SELECT * FROM ap_payments ORDER BY id')]
        body=dict(request_id=str(uuid4()),supplier_id=supplier['id'],document_type='invoice',folio='1932',
                  issued_on='2026-10-01',due_on='2026-11-01',amount=154545,condition='credit')
        result=ap.execute(self.store,'invoice',body,'QA integridad')
        after=self.financial_snapshot()
        self.assertEqual(ap.execute(self.store,'invoice',body,'QA integridad'),result)
        self.assertEqual(self.financial_snapshot(),after)
        current=self.current(result['id'])
        self.assertEqual((current['folio'],current['paid'],current['balance']),('1932',0,154545))
        self.edit(current,due_on='2026-11-02')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT original FROM ap_initial_baselines').fetchone()[0],opening_before)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM ap_payments ORDER BY id')],payments_before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pc_stock_lines').fetchone()[0],0)
        with self.assertRaises(DomainError) as error:self.invoice(supplier,folio='001932',amount=154545)
        self.assertEqual(error.exception.status,409);self.assertIn('Ya existe',str(error.exception));self.assertNotIn('según apertura',str(error.exception))
        other=self.invoice(supplier,folio='1933',amount=154545)
        self.assert_edit_rejected_without_changes(other,folio='1932')

    def test_europack_date_edit_excludes_itself_and_unrelated_miroh_form_evidence(self):
        snapshot=fixture();row=dict(snapshot['ledger'][0],name='Europack',folio=67590,amount=199337,paid=0,balance=199337)
        snapshot['ledger']=[row]
        snapshot['form']=[dict(row=80,name='Europack',folio=67590,amount=199337),
                          dict(row=100,name='Miroh spa',folio=67590,amount=199337)]
        self.opening(snapshot,total=199337,count=1)
        original=ap.view(self.store)['invoices'][0]
        with self.store.connect() as db:
            raw=db.execute('SELECT original FROM ap_initial_baselines').fetchone()[0]
            from erp.payable_duplicates import DuplicateIndex
            matches=DuplicateIndex(db).find(dict(original,name='Europack',tax_id=''),exclude_id=original['id'])
            self.assertEqual(matches,[])
        current=self.edit(original,issued_on='2026-09-03',due_on='2026-10-03')
        self.assertEqual((current['id'],current['supplier_id'],current['folio'],current['amount']),
                         (original['id'],original['supplier_id'],'67590',199337))
        self.assertEqual((current['issued_on'],current['due_on']),('2026-09-03','2026-10-03'))
        self.assertEqual(len(ap.view(self.store)['invoices']),1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT original FROM ap_initial_baselines').fetchone()[0],raw)
        supplier=ap.view(self.store)['suppliers'][0]
        with self.assertRaises(DomainError) as error:self.invoice(supplier,folio='67590',amount=199337)
        self.assertIn('Europack',str(error.exception));self.assertNotIn('Miroh',str(error.exception))
        another=self.invoice(supplier,folio='67591',amount=1234)
        self.assert_edit_rejected_without_changes(another,folio='67590',amount=199337)

    def test_same_folio_amount_is_allowed_for_two_explicit_suppliers_without_rut(self):
        europack=self.supplier('Europack','');miroh=self.supplier('Miroh spa','')
        first=self.invoice(europack,folio='67590',amount=199337)
        second=self.invoice(miroh,folio='67590',amount=199337)
        self.edit(first,due_on='2026-10-03');self.edit(second,due_on='2026-10-04')
        self.assertEqual(len(ap.view(self.store)['invoices']),2)
        with self.assertRaises(DomainError):self.invoice(europack,folio='67590',amount=199337)

    def test_renamed_supplier_still_matches_own_legacy_evidence_without_rut(self):
        self.opening();supplier=self.supplier('Proveedor C','')
        supplier=self.command('supplier',dict(supplier,name='Nombre actualizado QA',reason='Corrección ficticia'),supplier['id'])
        other=self.invoice(supplier,folio='777',amount=2222)
        self.assert_edit_rejected_without_changes(other,folio='1003',amount=1000)

    def test_edit_excludes_own_linked_opening_and_preserves_historic_payment(self):
        snapshot = fixture()
        row = snapshot['ledger'][0]
        snapshot['ledger'] = [row]
        snapshot['form'] = [dict(row=2, name=row['name'], folio=row['folio'], amount=row['amount'])]
        self.opening(snapshot, total=8000, count=1)
        original = ap.view(self.store)['invoices'][0]
        self.edit(original, due_on='2026-02-01')
        current = self.current(original['id'])
        self.assertEqual((current['balance'], current['paid']), (8000, 2000))
        self.assertEqual(current['payments'], original['payments'])

    def test_edit_allows_same_name_folio_and_amount_for_distinct_known_ruts(self):
        first = self.supplier()
        second = self.supplier(rut='76222222-2')
        self.invoice(first)
        other = self.invoice(second, folio='43')
        self.edit(other, folio='00042')
        self.assertEqual(ap.view(self.store)['totals']['balance'], 20000)
        self.assertEqual({r['folio'] for r in ap.view(self.store)['invoices']}, {'42', '00042'})

    def test_dashboard_separates_same_name_suppliers_and_aggregates_each_id(self):
        first = self.supplier('Nombre compartido QA', '76111111-1')
        second = self.supplier('Nombre compartido QA', '76222222-2')
        self.invoice(first, amount=10000)
        self.invoice(first, folio='43', amount=2000)
        other = self.invoice(second, amount=5000)
        payment = dict(version=other['version'], amount=1000, paid_on='2026-01-02',
                       method='transfer', confirmed=True, request_id=str(uuid4()))
        ap.execute(self.store, 'payment', payment, 'QA integridad', other['id'])
        ap.execute(self.store, 'payment', payment, 'QA integridad', other['id'])
        report = reports.report(self.store, month='2026-01')
        self.assertEqual((report['kpis']['suppliers'], report['kpis']['balance'], report['kpis']['payments']), (2, 16000, 1000))
        rows = {row['supplier_id']: row for row in report['suppliers']}
        self.assertEqual((rows[first['id']]['amount'], rows[first['id']]['count']), (12000, 2))
        self.assertEqual((rows[second['id']]['amount'], rows[second['id']]['count']), (4000, 1))
        self.assertEqual(rows[first['id']]['tax_id'], '761111111')
        self.assertEqual(rows[second['id']]['tax_id'], '762222222')
        self.assertNotEqual(rows[first['id']]['label'], rows[second['id']]['label'])
        concentration = next(r for r in report['findings'] if r['title'] == 'Mayor saldo por proveedor')
        self.assertIn('75.0 %', concentration['detail'])
        self.assertIn('761111111', concentration['detail'])
        self.assertEqual(reports.report(self.store, month='2026-01'), report)

    def test_dashboard_rename_does_not_merge_supplier_identity(self):
        first = self.supplier('Nombre inicial QA')
        second = self.supplier('Otro nombre QA', '76222222-2')
        self.invoice(first)
        self.invoice(second)
        self.command('supplier', dict(first, name=second['name'], reason='Nombre actualizado QA'), first['id'])
        report = reports.report(self.store, month='2026-01')
        self.assertEqual(report['kpis']['suppliers'], 2)
        self.assertEqual([row['supplier_id'] for row in report['suppliers']], [first['id'], second['id']])
        self.assertEqual({row['name'] for row in report['suppliers']}, {second['name']})

    def test_dashboard_keeps_supplier_without_rut_and_excludes_unvalidated_debt(self):
        first = self.supplier(rut='')
        self.invoice(first)
        preview = self.upload('33;76222222-2;Importado QA;77;01/01/2026;5000')
        sii.confirm(self.store, preview['id'], dict(rows=[2], confirmed=True), 'QA integridad')
        report = reports.report(self.store, month='2026-01')
        self.assertEqual((report['kpis']['suppliers'], report['kpis']['balance'], report['kpis']['review']), (1, 10000, 1))
        self.assertEqual(report['suppliers'][0]['supplier_id'], first['id'])
        self.assertEqual(report['suppliers'][0]['tax_id'], '')
        self.assertEqual(report['suppliers'][0]['label'], first['name'])


if __name__ == '__main__':
    unittest.main()
