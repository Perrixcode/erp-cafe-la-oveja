"""Payment excess and existing credit ledger: fictional, disposable SQLite only."""
import base64,io,json,unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4
from unittest.mock import patch
from erp import payables as ap,supplier_account as account,supplier_finance_pdf as pdf,payable_reports as reports
from erp.domain import DomainError
import test_joint_payment as fixture

class PaymentExcessTests(unittest.TestCase):
    setUp=fixture.JointPaymentTests.setUp
    command=fixture.JointPaymentTests.command
    snapshot=fixture.JointPaymentTests.snapshot
    def body(self,total=450,rows=None):
        rows=self.rows if rows is None else rows
        remaining=total;apps=[]
        for row in rows:
            amount=min(remaining,row['amount']);remaining-=amount
            if amount:apps.append(dict(invoice_id=row['id'],version=row['version'],amount=amount))
        balance=sum(r['amount'] for r in rows)
        return dict(request_id=str(uuid4()),supplier_id=self.supplier['id'],amount=total,occurred_on='2026-01-02',method='transfer',confirmed=True,
                    invoices=[dict(id=r['id'],version=r['version']) for r in rows],applications=apps,
                    overpayment_confirmation=dict(confirmed=True,selected_balance=balance,excess=max(0,total-balance)),
                    proof=dict(name='excedente-ficticio.pdf',base64=base64.b64encode(self.proof).decode()))
    def save(self,body):
        return ap.execute(self.store,'account_invoice_payment' if len(body['invoices'])==1 else 'account_joint_payment',body,'socio-excedente-qa')
    def entry(self):return account.statement(self.store,self.supplier['id'])['entries'][0]
    def void(self,entry):
        return self.command('account_void',dict(version=entry['version'],reason='Reversa ficticia',invoices=[dict(id=a['invoice_id'],version=a['invoice_version']) for a in entry['applications'] if not a['voided_at']]),entry['id'])
    def test_three_invoices_450_leaves_50_one_payment_one_proof_and_one_cash_credit(self):
        result=self.save(self.body());self.assertEqual((result['amount'],result['applied'],result['available']),(450,400,50))
        st=account.statement(self.store,self.supplier['id']);self.assertEqual((st['totals']['outstanding'],st['totals']['available_credit'],st['totals']['net_balance']),(0,50,-50));self.assertTrue(st['totals']['consistent'])
        self.assertEqual(sum(r['credit'] for r in st['journal']),450)
        self.assertEqual([r['balance'] for r in st['invoices']],[0,0,0]);self.assertEqual(len(st['entries']),1);self.assertEqual(len(st['entries'][0]['files']),1)
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],450)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_payments').fetchone()[0],0)
    def test_individual_exact_partial_and_excess_use_same_ledger(self):
        for row,total in zip(self.rows,[100,60,250]):
            saved=self.save(self.body(total,[row]));self.assertEqual(saved['available'],max(0,total-row['amount']))
        value=ap.view(self.store);self.assertEqual({r['folio']:r['balance'] for r in value['invoices']},{'801':0,'802':40,'803':0})
        self.assertEqual(len(value['account_entries']),3);self.assertEqual(sum(e['available'] for e in value['account_entries']),50)
    def test_partial_selection_including_zero_allocations_is_version_checked(self):
        body=self.body(50);self.assertEqual(len(body['applications']),1)
        self.save(body);self.assertEqual(self.entry()['applied'],50)
    def test_missing_false_or_wrong_excess_confirmation_has_no_side_effects(self):
        before=self.snapshot()
        for confirmation in (None,{},dict(confirmed=False,selected_balance=400,excess=50),dict(confirmed=True,selected_balance=399,excess=51)):
            body=self.body();body['overpayment_confirmation']=confirmation
            with self.assertRaises(DomainError):self.save(body)
            self.assertEqual(self.snapshot(),before)
    def test_excess_must_cover_selected_balance_not_leave_debt_and_unused_money(self):
        body=self.body();body['applications'][0]['amount']=50;before=self.snapshot()
        with self.assertRaises(DomainError):self.save(body)
        self.assertEqual(self.snapshot(),before)
    def test_changed_balance_after_review_rejects_until_new_explicit_review(self):
        body=self.body();self.command('payment',dict(version=1,amount=25,paid_on='2026-01-02',method='cash',confirmed=True),self.rows[0]['id']);before=self.snapshot()
        with self.assertRaises(DomainError) as err:self.save(body)
        self.assertEqual(err.exception.status,409);self.assertEqual(self.snapshot(),before)
    def test_invalid_file_rolls_back_everything_including_credit_and_audit(self):
        before=self.snapshot();body=self.body();body['proof']['base64']=base64.b64encode(b'invalid').decode()
        with self.assertRaises(DomainError):self.save(body)
        self.assertEqual(self.snapshot(),before)
        with patch.object(account,'attach_in_transaction',side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):self.save(self.body())
        self.assertEqual(self.snapshot(),before)
    def test_duplicate_requests_and_concurrent_double_click_create_one_record(self):
        body=self.body();barrier=threading.Barrier(2)
        def run(_):barrier.wait();return self.save(deepcopy(body))
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(run,range(2)))
        self.assertEqual(result[0],result[1]);before=self.snapshot();self.assertEqual(self.save(body),result[0]);self.assertEqual(self.snapshot(),before)
    def test_different_requests_with_same_old_versions_cannot_double_pay(self):
        barrier=threading.Barrier(2)
        def run(_):
            body=self.body();barrier.wait()
            try:return self.save(body)
            except DomainError as err:return err.status
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertIn(409,results)
    def test_credit_apply_confirm_reuse_proof_then_guarded_reversal(self):
        self.save(self.body());entry=self.entry();invoice=self.command('invoice',dict(supplier_id=self.supplier['id'],document_type='invoice',folio='804',amount=50,issued_on='2026-01-03',condition='credit'))
        body=dict(version=entry['version'],applications=[dict(invoice_id=invoice['id'],version=invoice['version'],amount=50)])
        before=self.snapshot()
        with self.assertRaises(DomainError):self.command('account_apply',body,entry['id'])
        self.assertEqual(self.snapshot(),before)
        self.command('account_apply',dict(body,confirmed=True),entry['id']);entry=self.entry();self.assertEqual(entry['available'],0);self.assertEqual(len(entry['files']),1)
        before=self.snapshot()
        with self.assertRaises(DomainError) as err:self.void(entry)
        self.assertIn('804',str(err.exception));self.assertEqual(self.snapshot(),before)
        later=entry['reversal_blockers'][0]
        self.command('account_unapply',dict(version=entry['version'],invoice_version=next(a for a in entry['applications'] if a['id']==later['id'])['invoice_version'],reason='Deshacer aplicación posterior ficticia'),later['id'])
        self.void(self.entry());st=account.statement(self.store,self.supplier['id']);self.assertEqual(st['totals']['outstanding'],450);self.assertEqual(st['totals']['available_credit'],0);self.assertTrue(st['totals']['consistent']);self.assertEqual(len(st['entries'][0]['files']),1)
    def test_credit_cannot_cross_supplier_or_exceed_available(self):
        self.save(self.body());other=self.command('supplier',dict(name='Otro ficticio'));invoice=self.command('invoice',dict(supplier_id=other['id'],document_type='invoice',folio='998',amount=60,issued_on='2026-01-02',condition='credit'));before=self.snapshot()
        with self.assertRaises(DomainError):self.command('account_apply',dict(version=1,confirmed=True,applications=[dict(invoice_id=invoice['id'],version=1,amount=50)]),self.entry()['id'])
        self.assertEqual(self.snapshot(),before)
    def test_cartola_and_pdf_show_payment_applications_and_separate_credit(self):
        from pypdf import PdfReader
        self.save(self.body());st=account.statement(self.store,self.supplier['id'])
        text=' '.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.statement_pdf(st))).pages)
        for expected in ['Deuda pendiente','Saldo a nuestro favor disponible','450','50','801','802','803','socio-excedente-qa','excedente-ficticio.pdf']:self.assertIn(expected,text)
        self.assertIn('Saldo a favor disponible $50',account.export_statement(st).decode('utf-8-sig'))
