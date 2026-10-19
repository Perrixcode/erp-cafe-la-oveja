"""Received notes, original invoice conservation and atomic proof, synthetic only."""
import base64
import json
from uuid import uuid4
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_supplier_account as base
from erp import payables as ap,supplier_account as account
from erp.domain import DomainError

class InvoiceCreditNoteTests(unittest.TestCase):
    setUp=base.SupplierAccountTests.setUp;command=base.SupplierAccountTests.command
    invoice=base.SupplierAccountTests.invoice;current=base.SupplierAccountTests.current
    payment=base.SupplierAccountTests.payment;application=base.SupplierAccountTests.application
    entry=base.SupplierAccountTests.entry;void=base.SupplierAccountTests.void;snapshot=base.SupplierAccountTests.snapshot
    def body(self,amount=4000,**values):
        return dict(dict(request_id=uuid4().hex,supplier_id=self.supplier['id'],origin_invoice_id=self.first['id'],origin_version=self.current(self.first)['version'],
                         amount=amount,occurred_on='2026-01-03',folio='000900',confirmed=True,notes='Devolución FICTICIA revisada',
                         proof=dict(name='nota-ficticia.pdf',base64=base64.b64encode(b'%PDF-1.4\nSynthetic note only\n%%EOF').decode())),**values)
    def note(self,amount=4000,**values):return ap.execute(self.store,'account_invoice_credit_note',self.body(amount,**values),'Socio QA')
    def full_snapshot(self):
        with self.store.connect() as db:return list(db.iterdump())
    def test_partial_then_full_note_preserve_original_and_paid_amount(self):
        first=self.note();self.assertEqual((first['applied'],first['available'],first['invoice_original'],first['invoice_balance']),(4000,0,10000,6000))
        second=self.note(6000,folio='901');invoice=self.current(self.first)
        self.assertEqual((invoice['amount'],invoice['paid'],invoice['credited'],invoice['balance']),(10000,0,10000,0))
        self.assertEqual(len(self.entry(first['id'])['files']),1);self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM ap_events WHERE action='invoice_credit_note_received' AND invoice_id=?",(self.first['id'],)).fetchone()[0],2)
    def test_paid_invoice_creates_available_note_without_duplicating_payment_excess(self):
        payment=self.payment(12000,[self.application(self.first,10000)]);original=self.entry(payment['id']);note=self.note(3000)
        self.assertEqual((note['applied'],note['available'],note['invoice_balance']),(0,3000,0))
        after=self.entry(payment['id'])
        self.assertEqual({k:v for k,v in after.items() if k!='applications'},{k:v for k,v in original.items() if k!='applications'})
        self.assertEqual([(a['id'],a['amount']) for a in after['applications']],[(a['id'],a['amount']) for a in original['applications']]);self.assertEqual(self.current(self.first)['paid'],10000)
        statement=account.statement(self.store,self.supplier['id']);self.assertEqual(statement['totals']['available_credit'],5000);self.assertTrue(statement['totals']['consistent'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ap_payments').fetchone()[0],0)
    def test_partial_payment_applies_only_remaining_balance_and_keeps_rest_available(self):
        self.payment(7000,[self.application(self.first,7000)]);note=self.note(8000)
        self.assertEqual((note['applied'],note['available'],note['invoice_balance']),(3000,5000,0))
        invoice=self.current(self.first);self.assertEqual((invoice['amount'],invoice['paid'],invoice['credited']),(10000,7000,3000))
    def test_retry_concurrency_and_normalized_folio_do_not_duplicate(self):
        payload=self.body()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:ap.execute(self.store,'account_invoice_credit_note',payload,'Socio QA'),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0]['folio'],'900')
        before=self.full_snapshot()
        with self.assertRaises(DomainError) as e:self.note(1000,folio='900')
        self.assertEqual(e.exception.status,409);self.assertEqual(before,self.full_snapshot())
    def test_invalid_document_rolls_back_note_application_audit_and_request(self):
        before=self.full_snapshot()
        for proof in (dict(name='bad.txt',base64=base64.b64encode(b'not a pdf or image').decode()),dict(name='x.pdf',base64='!broken!')):
            with self.assertRaises(DomainError):self.note(proof=proof)
            self.assertEqual(before,self.full_snapshot())
    def test_invalid_scope_date_total_reason_confirmation_or_version_changes_nothing(self):
        other=self.command('supplier',dict(name='Otro proveedor FICTICIO'))
        for values in (dict(supplier_id=other['id']),dict(occurred_on='2025-12-31'),dict(occurred_on='2999-01-01'),dict(amount=10001),dict(notes=''),dict(confirmed=False),dict(origin_version=0),dict(applications=[])):
            before=self.full_snapshot()
            with self.assertRaises(DomainError):self.note(**values)
            self.assertEqual(before,self.full_snapshot())
        self.note(6000)
        with self.assertRaises(DomainError):self.note(5000,folio='901')
    def test_same_folio_other_supplier_is_allowed_and_invoice_amount_cannot_be_rewritten(self):
        self.note();other=self.command('supplier',dict(name='Proveedor FICTICIO distinto'));invoice=self.invoice('100',10000,other)
        note=self.note(supplier_id=other['id'],origin_invoice_id=invoice['id'],origin_version=invoice['version'])
        self.assertEqual(note['supplier_id'],other['id']);self.assertEqual(self.current(self.first)['amount'],10000)
    def test_statement_and_export_trace_origin_and_credit_once(self):
        note=self.note();statement=account.statement(self.store,self.supplier['id']);row=next(r for r in statement['journal'] if r['kind']=='credit_note')
        self.assertEqual(row['invoice_id'],self.first['id']);self.assertIn('Factura origen 100',row['label']);self.assertEqual(row['credit'],4000)
        exported=account.export_statement(statement);text=exported.decode('utf-8-sig') if isinstance(exported,bytes) else exported
        self.assertIn('Factura origen 100',text);self.assertEqual(sum(r['credit'] for r in statement['journal']),4000)
        self.void(note);after=account.statement(self.store,self.supplier['id']);self.assertTrue(after['totals']['consistent']);self.assertEqual(self.current(self.first)['balance'],10000)
        self.assertEqual(len(self.entry(note['id'])['files']),1)

if __name__=='__main__':unittest.main()
