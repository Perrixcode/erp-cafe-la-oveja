"""Synthetic weekly planning; no operational data or transfer execution."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from erp import payables as ap, payment_proposals as proposals, payable_reports as reports
from erp.domain import DomainError
from erp.store import Store


class PaymentProposalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'synthetic.sqlite3')
        fixed=patch.object(ap,'local_day',return_value='2026-10-14');fixed.start();self.addCleanup(fixed.stop)
        self.supplier=self.command('supplier',dict(name='Proveedor ficticio C',tax_id='76111111-1',bank='Banco ficticio',account_type='Corriente',account_number='QA-123',account_holder='Titular ficticio',account_tax_id='76111111-1'))
        self.first=self.invoice('101',10000,'2026-10-10')
        self.second=self.invoice('102',6000,'2026-10-15')

    def command(self,action,data,target=None):
        return ap.execute(self.store,action,dict(data,request_id=str(uuid4())),'socio-propuesta-qa',target)

    def invoice(self,folio,amount,due,proof=True,**changes):
        data=dict(supplier_id=self.supplier['id'],document_type='invoice',folio=folio,amount=amount,issued_on='2026-10-01',due_on=due,condition='credit')
        data.update(changes)
        invoice=self.command('invoice',data)
        if proof:ap.attach(self.store,invoice['id'],None,b'%PDF-1.4\nDocumento ficticio\n%%EOF','qa.pdf','QA')
        return invoice

    def current(self,invoice):
        return next(r for r in ap.view(self.store)['invoices'] if r['id']==invoice['id'])

    def preview(self,budget=12000,**changes):
        return proposals.preview(self.store,dict(dict(week_start='2026-10-14',budget=budget,allow_partial=True),**changes))

    def body(self,preview,items=None,**changes):
        items=items if items is not None else [dict(id=r['id'],version=r['version'],amount=r['proposed_amount']) for r in preview['candidates'] if r['proposed_amount']]
        return dict(dict(preview['params'],token=preview['token'],items=items,confirmed=True,scheduled_on=preview['suggested_date']),**changes)

    def snapshot(self,financial=False):
        names=('ap_payments','ap_account_entries','ap_account_applications','stock','stock_history')
        if not financial:names+=('ap_invoices','ap_events','ap_requests','ap_invoice_holds','ap_payment_proposals')
        with self.store.connect() as db:return {n:[tuple(r) for r in db.execute('SELECT * FROM '+n+' ORDER BY 1')] for n in names}

    def test_preview_is_read_only_deterministic_and_oldest_due_first(self):
        before=self.snapshot();value=self.preview()
        self.assertEqual(value,self.preview())
        self.assertEqual(before,self.snapshot())
        self.assertEqual(value['params']['week_start'],'2026-10-12')
        self.assertEqual(value['params']['week_end'],'2026-10-18')
        self.assertEqual([(r['id'],r['proposed_amount']) for r in value['candidates']],[(self.first['id'],10000),(self.second['id'],2000)])
        self.assertEqual(value['proposed_total'],12000)
        self.assertEqual(value['remaining_budget'],0)

    def test_whole_invoice_option_and_zero_budget(self):
        value=self.preview(6000,allow_partial=False)
        self.assertEqual([r['proposed_amount'] for r in value['candidates']],[0,6000])
        self.assertEqual(self.preview(0)['proposed_total'],0)

    def test_strict_clp_and_week_validation(self):
        for amount in (True,-1,1000.0,'1000',ap.MAX_AMOUNT+1):
            with self.subTest(amount=amount),self.assertRaises(DomainError):self.preview(amount)
        for args in (dict(currency='USD'),dict(allow_partial='yes'),dict(week_start='bad'),dict(week_start='2025-01-01'),dict(week_start='9999-12-31')):
            with self.subTest(args=args),self.assertRaises(DomainError):self.preview(**args)
        self.assertEqual(self.preview(1)['proposed_total'],1)

    def test_existing_weekly_schedule_reserves_budget_without_overwriting(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        value=self.preview(10000)
        self.assertEqual(value['reserved_amount'],4000)
        self.assertEqual(value['proposed_total'],6000)
        self.assertEqual([r['id'] for r in value['candidates']],[self.second['id']])
        self.command('proposal_save',self.body(value))
        self.assertEqual((self.current(self.first)['scheduled_on'],self.current(self.first)['planned_amount']),('2026-10-16',4000))

    def test_weekly_cash_already_paid_is_not_freed_back_into_budget(self):
        self.command('payment',dict(version=1,amount=10000,paid_on='2026-10-14',method='cash',confirmed=True),self.first['id'])
        value=self.preview(12000)
        self.assertEqual(value['paid_amount'],10000)
        self.assertEqual(value['available_budget'],2000)
        self.assertEqual(value['proposed_total'],2000)
        self.assertEqual(value['candidates'][0]['id'],self.second['id'])
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value,[dict(id=self.second['id'],version=1,amount=2001)]))

    def test_partial_payment_reduces_reservation_without_changing_weekly_commitment(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        self.command('payment',dict(version=2,amount=1000,paid_on='2026-10-14',method='cash',confirmed=True),self.first['id'])
        value=self.preview(5000)
        self.assertEqual((value['paid_amount'],value['reserved_amount'],value['proposed_total']),(1000,3000,1000))

    def test_account_payment_counts_once_and_note_never_consumes_cash_budget(self):
        self.command('account_payment',dict(supplier_id=self.supplier['id'],amount=3000,occurred_on='2026-10-14',method='cash',confirmed=True,applications=[dict(invoice_id=self.first['id'],version=1,amount=1000),dict(invoice_id=self.second['id'],version=1,amount=2000)]))
        self.command('account_credit_note',dict(supplier_id=self.supplier['id'],amount=1000,occurred_on='2026-10-14',folio='C-77',origin_invoice_id=self.first['id'],origin_version=2,confirmed=True,applications=[dict(invoice_id=self.first['id'],version=2,amount=1000)]))
        value=self.preview(6000)
        self.assertEqual(value['paid_amount'],3000)
        self.assertEqual(value['proposed_total'],3000)

    def test_other_week_schedule_is_excluded_but_does_not_reserve_this_week(self):
        self.command('schedule',dict(scheduled_on='2026-10-20',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        value=self.preview()
        self.assertEqual(value['reserved_amount'],0)
        self.assertFalse(value['existing'][0]['in_week'])
        self.assertEqual(value['proposed_total'],6000)

    def test_monday_sunday_boundaries_and_undated_historical_payment(self):
        with patch.object(ap,'local_day',return_value='2026-10-19'):
            for day,amount in [('2026-10-11',100),('2026-10-12',200),('2026-10-18',300),('2026-10-19',400)]:
                self.command('payment',dict(version=self.current(self.first)['version'],amount=amount,paid_on=day,method='cash',confirmed=True),self.first['id'])
                self.command('account_payment',dict(supplier_id=self.supplier['id'],amount=amount,occurred_on=day,method='cash',confirmed=True))
        with self.store.connect() as db:
            db.execute("INSERT INTO ap_payments(invoice_id,amount,paid_on,method,reference,notes,actor,created_at) VALUES(?,50,'','other','Saldo anterior validado','','QA','2026-10-14T12:00:00Z')",(self.first['id'],))
        with patch.object(ap,'local_day',return_value='2026-10-18'):
            self.assertEqual(self.preview()['paid_amount'],1000)
        with patch.object(ap,'local_day',return_value='2026-10-19'):
            self.assertEqual(self.preview(week_start='2026-10-19')['paid_amount'],800)

    def test_void_payment_then_correct_date_recalculates_cash_without_restoring_schedule(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        payment=self.command('payment',dict(version=2,amount=1000,paid_on='2026-10-14',method='cash',confirmed=True),self.first['id'])
        old=self.preview(5000)
        self.command('void_payment',dict(version=3,reason='Fecha ficticia equivocada'),payment['id'])
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(old))
        value=self.preview(5000)
        self.assertEqual((value['paid_amount'],value['reserved_amount'],value['available_budget']),(0,3000,2000))
        self.command('payment',dict(version=4,amount=1000,paid_on='2026-10-11',method='cash',confirmed=True),self.first['id'])
        value=self.preview(5000)
        self.assertEqual((value['paid_amount'],value['reserved_amount'],value['available_budget']),(0,2000,3000))

    def test_void_account_payment_frees_cash_but_keeps_remaining_partial_schedule(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        payment=self.command('account_payment',dict(supplier_id=self.supplier['id'],amount=1000,occurred_on='2026-10-14',method='cash',confirmed=True,applications=[dict(invoice_id=self.first['id'],version=2,amount=1000)]))
        self.assertEqual((self.preview(5000)['paid_amount'],self.preview(5000)['reserved_amount']),(1000,3000))
        self.command('account_void',dict(version=1,reason='Pago ficticio anulado',invoices=[dict(id=self.first['id'],version=3)]),payment['id'])
        value=self.preview(5000)
        self.assertEqual((value['paid_amount'],value['reserved_amount'],value['available_budget']),(0,3000,2000))
        self.assertEqual(self.current(self.first)['balance'],10000)

    def test_unapplying_keeps_cash_consumed_and_requires_credit_review(self):
        payment=self.command('account_payment',dict(supplier_id=self.supplier['id'],amount=1000,occurred_on='2026-10-14',method='cash',confirmed=True,applications=[dict(invoice_id=self.first['id'],version=1,amount=1000)]))
        with self.store.connect() as db:application=db.execute('SELECT id FROM ap_account_applications WHERE entry_id=?',(payment['id'],)).fetchone()[0]
        self.command('account_unapply',dict(version=1,invoice_version=2,reason='Reasignación ficticia'),application)
        value=self.preview(5000)
        self.assertEqual((value['paid_amount'],value['available_budget'],value['proposed_total']),(1000,4000,0))
        self.assertTrue(all('credit' in row['reasons'] for row in value['excluded']))

    def test_moving_partial_schedule_between_weeks_and_cancelling_invalidates_preview(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        self.command('payment',dict(version=2,amount=1000,paid_on='2026-10-14',method='cash',confirmed=True),self.first['id'])
        value=self.preview(5000)
        self.command('schedule',dict(scheduled_on='2026-10-20',items=[dict(id=self.first['id'],version=3,amount=3000)]))
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value))
        current=self.preview(5000);following=self.preview(5000,week_start='2026-10-19')
        self.assertEqual((current['paid_amount'],current['reserved_amount'],current['available_budget']),(1000,0,4000))
        self.assertEqual((following['paid_amount'],following['reserved_amount'],following['available_budget']),(0,3000,2000))
        self.command('schedule',dict(scheduled_on='',items=[dict(id=self.first['id'],version=4)]))
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(current))
        self.assertEqual(self.preview(5000)['candidates'][0]['balance'],9000)

    def test_credit_note_and_its_void_do_not_consume_cash_or_restore_old_schedule(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        note=self.command('account_credit_note',dict(supplier_id=self.supplier['id'],amount=1000,occurred_on='2026-10-14',folio='C-VOID',origin_invoice_id=self.first['id'],origin_version=2,confirmed=True,applications=[dict(invoice_id=self.first['id'],version=2,amount=1000)]))
        self.assertEqual((self.preview()['paid_amount'],self.preview()['reserved_amount']),(0,3000))
        self.command('account_void',dict(version=1,reason='NC ficticia anulada',invoices=[dict(id=self.first['id'],version=self.current(self.first)['version'])]),note['id'])
        value=self.preview()
        self.assertEqual((value['paid_amount'],value['reserved_amount']),(0,3000))
        self.assertEqual(self.current(self.first)['balance'],10000)

    def test_incomplete_documents_and_future_due_are_explained(self):
        missing=self.invoice('103',3000,'',proof=False,issued_on='',condition='unknown')
        later=self.invoice('104',3000,'2026-11-01')
        with self.store.connect() as db:db.execute('UPDATE ap_invoices SET payment_review=1 WHERE id=?',(self.second['id'],))
        rows={r['id']:r for r in self.preview()['excluded']}
        self.assertTrue({'missing_issue','missing_due','missing_condition','missing_document'}<=set(rows[missing['id']]['reasons']))
        self.assertIn('later',rows[later['id']]['reasons'])
        self.assertIn('review',rows[self.second['id']]['reasons'])
        self.assertIsNone(rows[self.second['id']]['balance'])

    def test_incomplete_supplier_is_excluded_without_altering_bank_data(self):
        supplier=dict(ap.view(self.store)['suppliers'][0],bank='',tax_id='',reason='Ficha ficticia incompleta')
        self.command('supplier',supplier,self.supplier['id'])
        value=self.preview()
        self.assertEqual(value['candidates'],[])
        self.assertTrue(all({'missing_bank','missing_tax_id'}<=set(r['reasons']) for r in value['excluded']))

    def test_unapplied_advance_requires_allocation_before_proposing(self):
        payment=self.command('account_payment',dict(supplier_id=self.supplier['id'],amount=2000,occurred_on='2026-10-02',method='cash',confirmed=True))
        value=self.preview()
        self.assertEqual(value['proposed_total'],0)
        self.assertTrue(all('credit' in r['reasons'] for r in value['excluded']))
        self.command('account_apply',dict(version=1,applications=[dict(invoice_id=self.first['id'],version=1,amount=2000)]),payment['id'])
        self.assertEqual(self.preview()['candidates'][0]['balance'],8000)

    def test_applied_note_reduces_proposal_without_becoming_a_payment(self):
        self.command('account_credit_note',dict(supplier_id=self.supplier['id'],amount=2000,occurred_on='2026-10-02',folio='77',origin_invoice_id=self.first['id'],origin_version=1,confirmed=True,applications=[dict(invoice_id=self.first['id'],version=1,amount=2000)]))
        self.assertEqual(self.preview()['candidates'][0]['balance'],8000)
        self.assertEqual(reports.report(self.store,month='2026-10')['kpis']['payments'],0)

    def test_hold_clears_schedule_excludes_and_release_does_not_restore_it(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=2000)]))
        self.command('payment_hold',dict(version=2,hold=True,reason='Diferencia ficticia de factura'),self.first['id'])
        row=self.current(self.first)
        self.assertEqual(row['planned_amount'],0)
        self.assertEqual(row['payment_hold']['actor'],'socio-propuesta-qa')
        self.assertIn('hold',next(r for r in self.preview()['excluded'] if r['id']==row['id'])['reasons'])
        for action in ('schedule','message'):
            with self.assertRaises(DomainError):
                if action=='message':ap.message(self.store,dict(items=[dict(id=row['id'],version=row['version'])]))
                else:self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=row['id'],version=row['version'],amount=1000)]))
        self.command('payment_hold',dict(version=row['version'],hold=False,reason='Diferencia revisada'),row['id'])
        self.assertIsNone(self.current(self.first)['payment_hold'])
        self.assertEqual(self.current(self.first)['planned_amount'],0)
        self.assertIn(row['id'],[r['id'] for r in self.preview()['candidates']])

    def test_reviewed_amounts_save_only_scheduling_with_immutable_evidence(self):
        value=self.preview(10000)
        items=[dict(id=r['id'],version=r['version'],amount=6000 if r['id']==self.first['id'] else 1000) for r in value['candidates']]
        before=self.snapshot(financial=True)
        result=self.command('proposal_save',self.body(value,items,notes='Distribución revisada ficticia'))
        self.assertEqual(result['total'],7000)
        self.assertEqual(before,self.snapshot(financial=True))
        self.assertEqual(ap.view(self.store)['totals']['scheduled'],7000)
        self.assertEqual(ap.view(self.store)['totals']['balance'],16000)
        with self.store.connect() as db:
            saved=dict(db.execute('SELECT * FROM ap_payment_proposals').fetchone())
        self.assertEqual(saved['actor'],'socio-propuesta-qa')
        self.assertEqual(json.loads(saved['source_json'])['token'],value['token'])
        self.assertEqual(sum(r['amount'] for r in json.loads(saved['selected_json'])),7000)

    def test_invalid_selections_and_budget_are_atomic(self):
        value=self.preview(10000);a=dict(id=self.first['id'],version=1,amount=1000)
        variants=[[a,a],[dict(a,id=999)], [dict(a,version=999)], [dict(a,amount=True)], [dict(a,amount=0)], [dict(a,amount=10001)], [dict(a,amount=10000),dict(id=self.second['id'],version=1,amount=1)]]
        for items in variants:
            before=self.snapshot()
            with self.subTest(items=items),self.assertRaises(DomainError):self.command('proposal_save',self.body(value,items))
            self.assertEqual(before,self.snapshot())

    def test_changes_to_balances_suppliers_holds_or_documents_invalidate_preview(self):
        value=self.preview()
        self.command('payment',dict(version=1,amount=1000,paid_on='2026-10-14',method='cash',confirmed=True),self.first['id'])
        before=self.snapshot()
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value))
        self.assertEqual(before,self.snapshot())
        value=self.preview()
        supplier=dict(ap.view(self.store)['suppliers'][0],bank='Otro banco ficticio',reason='QA')
        self.command('supplier',supplier,self.supplier['id'])
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value))
        value=self.preview()
        self.command('payment_hold',dict(version=self.current(self.first)['version'],hold=True,reason='QA'),self.first['id'])
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value))
        missing=self.invoice('103',2000,'2026-10-16',proof=False)
        value=self.preview()
        ap.attach(self.store,missing['id'],None,b'%PDF-1.4\nQA\n%%EOF','qa.pdf','QA')
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value))

    def test_dates_confirmation_and_tampered_parameters_rejected(self):
        value=self.preview()
        for changes in (dict(scheduled_on='2026-10-13'),dict(scheduled_on='2026-10-19'),dict(confirmed=False),dict(budget=12001)):
            with self.subTest(changes=changes),self.assertRaises(DomainError):self.command('proposal_save',self.body(value,**changes))

    def test_idempotent_save_does_not_duplicate_proposals(self):
        body=dict(self.body(self.preview()),request_id=str(uuid4()))
        first=ap.execute(self.store,'proposal_save',body,'QA')
        self.assertEqual(first,ap.execute(self.store,'proposal_save',body,'QA'))
        with self.assertRaises(DomainError):ap.execute(self.store,'proposal_save',dict(body,request_id=str(uuid4())),'QA')
        with self.assertRaises(DomainError):ap.execute(self.store,'proposal_save',dict(body,notes='Cambio'),'QA')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_payment_proposals').fetchone()[0],1)

    def test_concurrent_confirmation_only_one_wins(self):
        value=self.preview();barrier=threading.Barrier(2);results=[]
        def run():
            barrier.wait()
            try:self.command('proposal_save',self.body(value));results.append(201)
            except DomainError as error:results.append(error.status)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(results,[201,409])

    def test_reservations_over_budget_are_visible_and_block_new_amounts(self):
        self.command('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=self.first['id'],version=1,amount=4000)]))
        value=self.preview(3000)
        self.assertEqual(value['over_budget'],1000)
        self.assertEqual(value['proposed_total'],0)
        with self.assertRaises(DomainError):self.command('proposal_save',self.body(value,[dict(id=self.second['id'],version=1,amount=1)]))

    def test_proposal_limit_and_repeat_migration_preserve_records(self):
        for n in range(103,204):self.invoice(str(n),1,'2026-10-17')
        value=self.preview(20000)
        self.assertEqual(sum(r['proposed_amount']>0 for r in value['candidates']),100)
        self.command('proposal_save',self.body(value))
        before=self.snapshot()
        for _ in range(3):
            self.store=Store(self.store.path)
            self.assertEqual(before,self.snapshot())


if __name__=='__main__':unittest.main()
