"""Importes reales del concepto, cobertura, idempotencia y conservación ante fallos."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from erp import delivery_finance as f
from erp.domain import DomainError
from erp.store import Store

SCOPE={'restaurant_id':'DEMO','local_id':'1'}

def sale(payment='DEMO-P1',order='DEMO-O1',fee=2700):
    return {'orderId':order,'paymentId':payment,'payed':18700,'dateOpen':'2026-09-01T23:50:00',
            'dateClosed':'2026-09-03T02:00:00','fiscalType':'BE','products':[
                {'id':f.PRODUCT_ID,'name':'Costo Delivery','lineId':'DEMO-L1','quantity':1,'payed':fee,'netPrice':fee,'discounts':0},
                {'id':'DEMO-CAKE','lineId':'DEMO-L2','quantity':1,'payed':16000,'netPrice':16000}]}

def payload(*rows): return {'ok':True,'data':list(rows)}

class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'finance.sqlite3')
        self.day=patch.object(f,'today',return_value=date(2026,10,5));self.day.start();self.addCleanup(self.day.stop)

    def view(self): return f.view(self.store,'2026-09')
    def count(self,table):
        with self.store.connect() as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_only_product_id_paid_amount_not_sale_or_quantity_or_name(self):
        row=sale();row['products'][0]['quantity']=2
        row['products'][1]['name']='Costo Delivery'
        f.apply_day(self.store,'2026-09-01',payload(row),SCOPE)
        v=self.view();self.assertEqual(v['totals']['fee'],2700);self.assertEqual(v['totals']['sale_paid'],18700)
        self.assertEqual(v['date_basis'],'toteat_shift_day')
        self.assertEqual(v['days'][0]['details'][0]['closed_at'],'2026-09-03T02:00:00')
        self.assertIsNone(v['days'][2]['fee'])

    def test_repeated_snapshot_repeated_payment_and_restart_do_not_duplicate(self):
        row=sale();row['products'].append(deepcopy(row['products'][0]))
        for _ in range(3):f.apply_day(self.store,'2026-09-01',payload(row,row),SCOPE)
        self.assertEqual(self.count('delivery_transactions'),1);self.assertEqual(self.count('delivery_finance_history'),1)
        self.store=Store(self.store.path);self.assertEqual(self.view()['totals']['fee'],2700)

    def test_changed_payment_updates_with_history_not_append_to_total(self):
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        f.apply_day(self.store,'2026-09-01',payload(sale(fee=3000)),SCOPE)
        self.assertEqual(self.view()['totals']['fee'],3000);self.assertEqual(self.count('delivery_finance_history'),2)

    def test_overlap_wrong_shift_rejected_atomically(self):
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        with self.assertRaises(DomainError):f.apply_day(self.store,'2026-09-02',payload(sale('DEMO-NEW'),sale()),SCOPE)
        self.assertEqual(self.count('delivery_transactions'),1);self.assertEqual(self.count('delivery_days'),1)

    def test_different_scopes_do_not_collide_or_flag_split_payment(self):
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        f.apply_day(self.store,'2026-09-02',payload(sale()),dict(SCOPE,local_id='2'))
        self.assertEqual(self.view()['totals']['fee'],5400);self.assertEqual(self.view()['totals']['review_count'],0)

    def test_empty_day_known_zero_failed_or_unqueried_day_unknown(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,3))
        f.apply_day(self.store,'2026-09-01',payload(),SCOPE)
        f.fail_day(self.store,'2026-09-02','http_error')
        days=self.view()['days'];self.assertEqual(days[0]['fee'],0)
        self.assertIsNone(days[1]['fee']);self.assertEqual(days[1]['state'],'error');self.assertIsNone(days[2]['fee'])
        self.assertEqual(self.view()['totals']['known_days'],1)

    def test_failed_refresh_preserves_original_money_and_last_success(self):
        observed='2026-09-03T10:30:00+00:00'
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE,observed)
        f.fail_day(self.store,'2026-09-01','timeout')
        d=self.view()['days'][0];self.assertEqual(d['fee'],2700);self.assertEqual(d['last_success'],observed)
        self.assertEqual(d['state'],'error');self.assertEqual(d['error'],'timeout')

    def test_missing_source_preserves_amount_with_review_then_reappears(self):
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        f.apply_day(self.store,'2026-09-01',payload(),SCOPE)
        v=self.view();self.assertEqual(v['totals']['fee'],2700)
        self.assertIn('ausente_en_ultima_lectura',v['days'][0]['details'][0]['warnings'])
        self.assertEqual(v['totals']['review_count'],1)
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        self.assertEqual(self.view()['totals']['review_count'],0);self.assertEqual(self.count('delivery_finance_history'),3)

    def test_discounts_refunds_split_payments_never_auto_liquidated(self):
        a=sale(fee=2000);a['products'][0].update(netPrice=2700,discounts=700)
        b=sale('DEMO-P2',fee=-1000);b['fiscalType']='NC';b['payed']=-1000
        f.apply_day(self.store,'2026-09-01',payload(a,b),SCOPE)
        v=self.view();self.assertEqual(v['totals']['fee'],1000);self.assertEqual(v['totals']['review_count'],2)
        self.assertEqual(v['individual'],[]);self.assertFalse(v['payment_executed']);self.assertFalse(v['courier_collections_reconciled'])
        self.assertIn('varios_pagos_revisar_tarifa',v['days'][0]['details'][0]['warnings'])

    def test_bad_amount_identity_or_response_never_overwrites_data(self):
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        for value in [True,None,'NaN','Infinity',2.5,10**12]:
            with self.subTest(value=value),self.assertRaises(DomainError):f.apply_day(self.store,'2026-09-01',payload(sale(fee=value)),SCOPE)
        for value in [{'data':[]},{'ok':False,'data':[]},{'ok':True,'data':None}]:
            with self.assertRaises(DomainError):f.apply_day(self.store,'2026-09-01',value,SCOPE)
        conflicting=sale();conflicting['products'][0]['payed']=3000
        with self.assertRaises(DomainError):f.apply_day(self.store,'2026-09-01',payload(sale(),conflicting),SCOPE)
        self.assertEqual(self.view()['totals']['fee'],2700);self.assertEqual(self.count('delivery_finance_history'),1)

    def test_initial_queue_completes_without_endless_automatic_requeue(self):
        first=f.request_refresh(self.store,'Socio ficticio',date(2026,9,2))
        second=f.request_refresh(self.store,'Socio ficticio',date(2026,9,2))
        self.assertFalse(first['coalesced']);self.assertTrue(second['coalesced'])
        for day in ['2026-09-01','2026-09-02']:f.apply_day(self.store,day,payload(),SCOPE)
        automatic=f.request_refresh(self.store,'Servidor',date(2026,9,2),automatic=True)
        self.assertFalse(automatic['queued']);self.assertEqual(self.count('delivery_sync_requests'),1)
        third=f.request_refresh(self.store,'Servidor',date(2026,9,3),automatic=True)
        self.assertEqual(third['pending_days'],3);self.assertEqual(self.count('delivery_sync_requests'),2)

    def test_consultation_refresh_retries_errors_keeps_data_and_coalesces(self):
        f.request_refresh(self.store,'Servidor',date(2026,9,1),automatic=True)
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        f.fail_day(self.store,'2026-09-01','http_error')
        result=f.request_refresh(self.store,'Socio ficticio',date(2026,9,1))
        self.assertTrue(result['queued']);self.assertEqual(self.view()['days'][0]['fee'],2700)
        self.assertEqual(self.view()['days'][0]['state'],'pending')
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,1));self.assertEqual(self.count('delivery_sync_requests'),2)

    def test_month_coverage_and_daily_sum_are_exact(self):
        for day,fee in [('2026-09-01',2700),('2026-09-30',3100),('2026-10-01',2400)]:
            f.apply_day(self.store,day,payload(sale(day,day,fee)),SCOPE)
        v=self.view();self.assertEqual(v['totals']['fee'],5800)
        self.assertEqual(sum(d['fee'] or 0 for d in v['days']),v['totals']['fee'])
        self.assertEqual(v['totals']['known_days'],2);self.assertEqual(v['totals']['total_days'],30)
        self.assertEqual(f.view(self.store,'2026-10')['totals']['fee'],2400)
        for month in ['2026-08','2026-11','2026-9','2026-09-01']:
            with self.assertRaises(DomainError):f.view(self.store,month)
