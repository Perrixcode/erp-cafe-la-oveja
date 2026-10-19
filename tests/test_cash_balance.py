"""Synthetic conservation, deduplication, unknown coverage and starting-point tests."""
import sqlite3
import unittest
from uuid import uuid4
import test_cash_control as base
from erp import cash_control as cc,cash_control_sources as sources,payables as ap
from erp.domain import DomainError

class BalanceTests(unittest.TestCase):
    setUp=base.CashControlTests.setUp
    version=base.CashControlTests.version
    command=base.CashControlTests.command
    location=base.CashControlTests.location
    move=base.CashControlTests.move
    dump=base.CashControlTests.dump
    bag=base.CashControlTests.bag

    def view(self,start='2026-10-16',end='2026-10-16'):
        return cc.overview(self.store,dict(start=[start],end=[end],scope=['operations']))['expected_range']

    def origin(self,amount=100,day='2026-10-16'):
        return self.command('balance-origin',cutoff_day=day,amount=amount,evidence='Apertura total FICTICIA; no representa una operación real')

    def sales(self,amount=50,day='2026-10-16',key=1,change=10):
        payload={'ok':True,'data':[dict(orderId=key,paymentId=key,registerId='1',registerName='QA',change=change,
                    dateClosed=day+'T12:00:00Z',paymentForms=[dict(id=1000,method='CASH',amount=amount+change)])]}
        return sources.record(self.store,day,'sales',payload,base.SCOPE)

    def test_opening_plus_sales_minus_external_is_independent_of_count(self):
        self.origin();self.sales();loc=self.location('Caja QA');self.move(loc,None,20,kind='expense')
        before=self.dump();v=self.view();self.assertEqual((v['initial'],v['sales'],v['expenses'],v['final']),(100,50,20,130))
        self.assertEqual(v['known_variation'],30);self.assertEqual(v['state'],'provisional');self.assertFalse(v['coverage']['complete'])
        self.assertEqual(before,self.dump())
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cash_bags').fetchone()[0],0)

    def test_internal_transfer_and_custody_are_neutral_then_external_payment_once(self):
        self.origin();self.sales();cash=self.location('Caja QA');safe=self.location('Caja fuerte QA','safe');custody=self.location('Custodia QA','custody')
        self.move(cash,safe,30);self.assertEqual(self.view()['final'],150)
        self.move(cash,custody,20);expense=self.move(custody,None,20,kind='expense');self.assertEqual(self.view()['final'],130)
        supplier=ap.execute(self.store,'supplier',dict(request_id=uuid4().hex,name='Proveedor FICTICIO'),'QA')
        payment=ap.execute(self.store,'account_payment',dict(request_id=uuid4().hex,confirmed=True,supplier_id=supplier['id'],amount=20,occurred_on='2026-10-16',method='cash',applications=[]),'QA')
        self.command('payment-link',event_id=expense['id'],payment_source='ap_account_entries',payment_id=payment['id'],amount=20)
        self.assertEqual(self.view()['final'],130);self.assertEqual(self.view()['transfers'],2)

    def test_unknown_opening_keeps_known_variation_but_no_absolute_balance(self):
        self.sales();loc=self.location('Caja QA');self.move(loc,None,20,kind='expense')
        v=self.view();self.assertEqual(v['known_variation'],30);self.assertIsNone(v['initial']);self.assertIsNone(v['final'])
        self.assertEqual(v['state'],'opening_required')

    def test_missing_day_is_not_zero_and_starting_point_alone_is_not_final(self):
        self.origin();v=self.view();self.assertIsNone(v['final']);self.assertIsNone(v['known_variation']);self.assertIsNone(v['daily'][0]['known_cash'])
        self.sales();v=self.view(end='2026-10-17');self.assertEqual(v['final'],150);self.assertEqual(v['coverage']['days_missing'],1)
        self.assertIsNone(v['daily'][1]['sales']);self.assertIsNone(v['daily'][1]['known_variation'])

    def test_latest_reads_deduplicate_and_conflicting_cross_day_payment_is_pending(self):
        self.origin();self.sales();self.sales();self.assertEqual(self.view()['sales'],50)
        self.sales(day='2026-10-17');v=self.view(end='2026-10-17');self.assertIsNone(v['sales']);self.assertTrue(v['coverage']['identity_issues'])

    def test_duplicate_payment_across_opening_or_filter_boundary_is_excluded(self):
        self.sales();self.sales(day='2026-10-17');self.origin(day='2026-10-17')
        v=self.view(start='2026-10-17',end='2026-10-17');self.assertIsNone(v['sales']);self.assertIsNone(v['final'])
        self.assertEqual(v['coverage']['excluded_payments'],1);self.assertEqual(len(v['daily']),1)

    def test_collection_report_and_repeated_retained_funds_never_become_income(self):
        self.origin();self.sales();sources.record(self.store,'2026-10-16','collection',base.collection(initialAmount=100,finalAmount=999999),base.SCOPE)
        self.bag();self.bag();v=self.view();self.assertEqual((v['sales'],v['final']),(50,150))

    def test_income_needs_overlap_classification_and_corrections_preserve_history(self):
        self.origin();self.sales();cash=self.location('Caja QA');income=self.move(None,cash,50,kind='income')
        self.assertEqual(len(self.view()['unknown_incomes']),1);self.assertEqual(self.view()['final'],150)
        self.command('classify-income',event_id=income['id'],classification='already_in_sales',evidence='Mismo ingreso FICTICIO ya observado en Toteat')
        self.assertEqual(self.view()['final'],150);self.assertEqual(self.view()['sales_mirrors'],1)
        self.command('classify-income',event_id=income['id'],classification='other_external',evidence='Corrección FICTICIA: aporte externo distinto de ventas')
        self.assertEqual(self.view()['final'],200)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cc_income_classifications').fetchone()[0],2)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM cc_income_classifications')

    def test_origin_correction_is_versioned_and_does_not_insert_ledger_events(self):
        self.origin();self.origin(200);self.sales();v=self.view();self.assertEqual(v['final'],250);self.assertEqual(len(v['origin_history']),2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cc_cash_events').fetchone()[0],0)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE cc_balance_origins SET amount=999')
        with self.assertRaises(DomainError):cc.execute(self.store,'balance-origin',dict(request_id=uuid4().hex,version=self.version(),confirmed=True,cutoff_day='2026-10-16',amount=1,evidence='QA'),base.CASHIER)

    def test_range_initial_uses_prior_flows_not_sum_of_closures(self):
        self.origin();self.sales();self.sales(30,day='2026-10-17',key=2)
        v=self.view(start='2026-10-17',end='2026-10-17');self.assertEqual((v['initial'],v['known_variation'],v['final']),(150,30,180))
        self.origin(300,day='2026-10-17');v=self.view(end='2026-10-17');self.assertEqual(v['state'],'range_precedes_origin');self.assertIsNone(v['initial']);self.assertIsNone(v['final'])

    def test_latest_error_preserves_known_evidence_with_explicit_pending_coverage(self):
        self.origin();self.sales();sources.record(self.store,'2026-10-16','sales',scope=base.SCOPE,error='Falla FICTICIA')
        v=self.view();self.assertEqual(v['sales'],50);self.assertEqual(v['coverage']['days_with_source_error'],1);self.assertFalse(v['coverage']['complete'])

if __name__=='__main__':unittest.main()
