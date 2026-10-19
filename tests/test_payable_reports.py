import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from erp import payables as ap, payable_reports as reports, payable_baseline as baseline
from erp.domain import DomainError
from erp.store import Store
from test_supplier_opening import fixture

class ReportsAndBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root/'erp.sqlite3')
        self.file=self.root/'opening.json';self.file.write_text(json.dumps(fixture()))
    def command(self,action,data,target=None):return ap.execute(self.store,action,{**data,'request_id':str(uuid4())},'socio QA',target)
    def test_one_time_initialization_preserves_known_balances_no_future_sheet_link(self):
        result=baseline.initialize(self.store,self.file,'Inicialización QA',19200,5)
        self.assertFalse(result['already_initialized']);v=ap.view(self.store);self.assertEqual(v['totals']['balance'],19200);self.assertEqual(v['totals']['review'],0)
        self.assertEqual(sum(r['balance']>0 for r in v['invoices']),5);self.assertEqual(len(v['invoices']),5)
        self.assertTrue(all(not r['issued_on'] for r in v['invoices']))
        self.assertTrue(baseline.initialize(self.store,self.file,'Inicialización QA',19200,5)['already_initialized'])
        self.file.unlink();r=v['invoices'][0]
        self.command('payment',dict(version=r['version'],amount=100,paid_on='2026-01-01',method='transfer',confirmed=True),r['id'])
        self.assertEqual(ap.view(self.store)['totals']['balance'],19100)
        with self.store.connect() as db:self.assertTrue(db.execute('SELECT original FROM ap_initial_baselines').fetchone()[0])
    def test_opening_wrong_total_or_existing_operations_rejected(self):
        with self.assertRaises(DomainError):baseline.initialize(self.store,self.file,'QA',19000,5)
        self.assertEqual(ap.view(self.store)['invoices'],[])
        s=self.command('supplier',{'name':'QA'})
        self.command('invoice',dict(supplier_id=s['id'],folio='1',document_type='invoice',issued_on='2026-01-01',amount=1000,condition='credit'))
        with self.assertRaises(DomainError):baseline.initialize(self.store,self.file,'QA',19200,5)
    def test_operational_kpis_monthly_payments_and_csv_formula_escaping(self):
        supplier=self.command('supplier',{'name':'=PROVEEDOR_QA'})
        invoice=self.command('invoice',dict(supplier_id=supplier['id'],folio='7',document_type='invoice',issued_on='2026-01-01',due_on='2026-01-30',amount=10000,condition='credit'))
        self.command('payment',dict(version=1,amount=4000,paid_on='2026-02-01',method='transfer',confirmed=True),invoice['id'])
        r=reports.report(self.store,'erp','2026-02');self.assertEqual(r['kpis']['balance'],6000);self.assertEqual(r['kpis']['payments'],4000);self.assertEqual(r['kpis']['purchases'],0);self.assertEqual(r['kpis']['missing_proof'],1)
        text=reports.export_csv(self.store).decode('utf-8-sig');cells=list(csv.reader(io.StringIO(text),delimiter=';'));self.assertEqual(cells[1][3],"'=PROVEEDOR_QA")
        self.assertIn('4000',reports.export_csv(self.store,'erp','payments').decode('utf-8-sig'))
        for month in ('2026-99','2026-1'):
            with self.assertRaises(DomainError):reports.report(self.store,'erp',month)
    def test_missing_issue_dates_reported_without_fabricating_monthly_purchases(self):
        baseline.initialize(self.store,self.file,'QA',19200,5);r=reports.report(self.store,'erp','2026-10')
        self.assertEqual(r['kpis']['purchases'],0);self.assertEqual(r['kpis']['payments'],0);self.assertEqual(r['kpis']['balance'],19200)
        self.assertIn('Compras sin fecha de emisión',[v['title'] for v in r['findings']])
