"""Synthetic current-account transactions; no operational database or bank access."""
import json
import csv
import io
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from uuid import uuid4

from erp import payables as ap, supplier_account as account, payable_reports as reports
from erp import sii_purchases as sii
from erp.domain import DomainError
from erp.store import Store


class SupplierAccountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'synthetic.sqlite3'
        self.store = Store(self.path)
        self.supplier = self.command('supplier', {'name':'Cuenta QA','tax_id':'76111111-1'})
        self.first = self.invoice('100', 10000)
        self.second = self.invoice('101', 6000)

    def command(self, action, data, target=None):
        if action == 'account_apply':data=dict(data,confirmed=True)
        return ap.execute(self.store, action, dict(data, request_id=str(uuid4())), 'socio-cuenta-qa', target)

    def invoice(self, folio, amount, supplier=None):
        return self.command('invoice', dict(supplier_id=(supplier or self.supplier)['id'], document_type='invoice',
                            folio=folio, amount=amount, issued_on='2026-01-01', condition='credit'))

    def current(self, invoice):
        return next(r for r in ap.view(self.store)['invoices'] if r['id'] == invoice['id'])

    def application(self, invoice, amount):
        return dict(invoice_id=invoice['id'], version=self.current(invoice)['version'], amount=amount)

    def payment(self, amount, applications=(), **changes):
        return self.command('account_payment', dict(dict(supplier_id=self.supplier['id'], amount=amount,
                            currency='CLP', occurred_on='2026-01-02', method='transfer', confirmed=True,
                            applications=list(applications)), **changes))

    def note(self, amount, applications=(), **changes):
        return self.command('account_credit_note', dict(dict(supplier_id=self.supplier['id'], amount=amount,
                            currency='CLP', occurred_on='2026-01-03', folio='000900', confirmed=True,
                            origin_invoice_id=self.first['id'], origin_version=self.current(self.first)['version'],
                            applications=list(applications)), **changes))

    def entry(self, key):
        return next(e for e in account.statement(self.store, self.supplier['id'])['entries'] if e['id'] == key)

    def void(self, entry):
        entry = self.entry(entry['id'])
        invoices = {a['invoice_id']:a['invoice_version'] for a in entry['applications'] if not a['voided_at']}
        return self.command('account_void', dict(version=entry['version'], reason='Corrección QA',
                            invoices=[dict(id=k, version=v) for k,v in invoices.items()]), entry['id'])

    def snapshot(self):
        with self.store.connect() as db:
            tables = ('ap_invoices','ap_payments','ap_events','ap_requests','ap_account_entries','ap_account_applications','stock','stock_history')
            return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in tables}

    def test_one_payment_applies_to_multiple_invoices_once(self):
        p = self.payment(13000, [self.application(self.first,10000),self.application(self.second,3000)])
        self.assertEqual((self.current(self.first)['balance'],self.current(self.second)['balance']), (0,3000))
        self.assertEqual(self.entry(p['id'])['available'], 0)
        report = reports.report(self.store, month='2026-01')
        self.assertEqual(report['kpis']['payments'],13000)
        self.assertEqual(report['kpis']['balance'],3000)
        self.assertEqual(report['kpis']['missing_proof'],1)
        self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])

    def test_audit_timestamps_use_chilean_business_day_in_statement(self):
        from unittest.mock import patch
        with patch.object(account,'now',return_value='2026-02-02T01:00:00+00:00'):
            payment = self.payment(1000,[self.application(self.first,1000)])
            self.void(payment)
        rows = account.statement(self.store,self.supplier['id'])['journal']
        for row in rows:
            if row['kind'] in ('application','application_void','entry_void'):
                self.assertEqual(row['date'],'2026-02-01')

    def test_advance_and_partial_applications_preserve_total_account_balance(self):
        p = self.payment(20000)
        before = account.statement(self.store,self.supplier['id'])
        self.assertEqual(before['totals']['in_favor'],4000)
        self.command('account_apply',dict(version=1,applications=[self.application(self.first,3000)]),p['id'])
        e = self.entry(p['id'])
        self.command('account_apply',dict(version=e['version'],applications=[self.application(self.first,7000),self.application(self.second,6000)]),p['id'])
        after = account.statement(self.store,self.supplier['id'])
        self.assertEqual((after['totals']['outstanding'],after['totals']['available_credit'],after['totals']['net_balance']),(0,4000,-4000))
        self.assertEqual(after['journal'][-1]['balance'],-4000)
        self.assertTrue(after['totals']['consistent'])

    def test_partial_legacy_payment_plus_new_payment_and_credit_note(self):
        self.command('payment',dict(version=1,amount=2000,paid_on='2026-01-02',method='cash',confirmed=True),self.first['id'])
        self.payment(3000,[self.application(self.first,3000)])
        self.note(1000,[self.application(self.first,1000)])
        invoice = self.current(self.first)
        self.assertEqual((invoice['paid'],invoice['credited'],invoice['balance']),(5000,1000,4000))
        self.assertEqual(len(invoice['payments']),1)
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],5000)
        text = reports.export_csv(self.store,kind='payments').decode('utf-8-sig')
        self.assertIn('cuenta-',text)
        self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])

    def test_credit_note_applies_and_void_reopens_balance_without_stock(self):
        before_stock = {k:v for k,v in self.snapshot().items() if k.startswith('stock')}
        note = self.note(10000,[self.application(self.first,10000)])
        self.assertEqual(self.current(self.first)['status'],'settled')
        self.assertEqual(self.current(self.first)['paid'],0)
        self.void(note)
        self.assertEqual(self.current(self.first)['balance'],10000)
        result = account.statement(self.store,self.supplier['id'])
        self.assertTrue(result['totals']['consistent'])
        self.assertTrue(any(r['kind']=='entry_void' for r in result['journal']))
        self.assertEqual({k:v for k,v in self.snapshot().items() if k.startswith('stock')},before_stock)

    def test_credit_note_on_paid_invoice_creates_credit_for_other_invoice(self):
        self.payment(10000,[self.application(self.first,10000)])
        note = self.note(3000)
        self.command('account_apply',dict(version=1,applications=[self.application(self.second,2000)]),note['id'])
        result = account.statement(self.store,self.supplier['id'])
        self.assertEqual(result['totals']['net_balance'],3000)
        self.assertEqual(self.current(self.second)['credited'],2000)
        self.assertEqual(self.entry(note['id'])['available'],1000)

    def test_unapply_restores_invoice_and_available_credit_without_new_money(self):
        p = self.payment(4000,[self.application(self.first,4000)])
        e = self.entry(p['id']);a=e['applications'][0]
        self.command('account_unapply',dict(version=e['version'],invoice_version=a['invoice_version'],reason='Factura equivocada QA'),a['id'])
        self.assertEqual(self.current(self.first)['balance'],10000)
        self.assertEqual(self.entry(p['id'])['available'],4000)
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],4000)
        self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])

    def test_void_multi_invoice_payment_is_atomic_and_audited(self):
        p = self.payment(11000,[self.application(self.first,9000),self.application(self.second,2000)])
        old = self.snapshot()
        with self.assertRaises(DomainError):
            self.command('account_void',dict(version=1,reason='QA',invoices=[dict(id=self.first['id'],version=2)]),p['id'])
        self.assertEqual(self.snapshot(),old)
        self.void(p)
        self.assertEqual((self.current(self.first)['balance'],self.current(self.second)['balance']),(10000,6000))
        self.assertTrue(all(a['voided_at'] for a in self.entry(p['id'])['applications']))
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],0)

    def test_overapplication_of_entry_or_invoice_rolls_back_everything(self):
        for amount,applications in [(3000,[self.application(self.first,2000),self.application(self.second,2000)]),
                                    (20000,[self.application(self.first,10001)])]:
            before=self.snapshot()
            with self.assertRaises(DomainError):self.payment(amount,applications)
            self.assertEqual(self.snapshot(),before)
        p=self.payment(1000)
        before=self.snapshot()
        with self.assertRaises(DomainError):self.command('account_apply',dict(version=1,applications=[self.application(self.first,1001)]),p['id'])
        self.assertEqual(self.snapshot(),before)

    def test_legacy_payment_cannot_exceed_balance_after_credit(self):
        self.note(9000,[self.application(self.first,9000)])
        with self.assertRaises(DomainError):
            self.command('payment',dict(version=self.current(self.first)['version'],amount=1001,paid_on='2026-01-02',method='cash',confirmed=True),self.first['id'])

    def test_invoice_corrections_and_void_respect_credit_applications_and_origins(self):
        self.note(5000)
        current=self.current(self.first)
        with self.assertRaises(DomainError):self.command('invoice',dict(current,amount=4999,reason='QA'),current['id'])
        with self.assertRaises(DomainError):self.command('invoice',dict(current,issued_on='2026-02-01',reason='QA'),current['id'])
        with self.assertRaises(DomainError):self.command('void_invoice',dict(version=current['version'],reason='QA'),current['id'])
        self.payment(2000,[self.application(self.second,2000)])
        current=self.current(self.second)
        with self.assertRaises(DomainError):self.command('invoice',dict(current,amount=1999,reason='QA'),current['id'])

    def test_clp_requires_integer_amount_and_rejects_other_currencies(self):
        for amount in (True,0,-1,0.5,1000.0,'1000',ap.MAX_AMOUNT+1):
            with self.subTest(amount=amount),self.assertRaises(DomainError):self.payment(amount)
        for currency in ('USD',None,[]):
            with self.subTest(currency=currency),self.assertRaises(DomainError):self.payment(1000,currency=currency)
        p=self.payment(1,[self.application(self.first,1)])
        self.assertEqual(self.current(self.first)['balance'],9999)
        self.assertEqual(self.entry(p['id'])['amount'],1)

    def test_stale_or_cross_supplier_or_repeated_invoice_is_rejected(self):
        other=self.command('supplier',dict(name='Cuenta QA',tax_id='76222222-2'))
        invoice=self.invoice('100',3000,other)
        variants=[[self.application(invoice,1000)], [dict(self.application(self.first,1000),version=999)],
                  [self.application(self.first,1000)]*2]
        for applications in variants:
            before=self.snapshot()
            with self.assertRaises(DomainError):self.payment(2000,applications)
            self.assertEqual(self.snapshot(),before)

    def test_credit_note_duplicate_folio_and_cumulative_amount_are_blocked(self):
        self.note(6000)
        with self.assertRaises(DomainError):self.note(1000,folio='900')
        with self.assertRaises(DomainError):self.note(4001,folio='901')
        self.note(4000,folio='901')

    def test_idempotency_for_new_entry_application_and_void(self):
        body=dict(request_id=str(uuid4()),supplier_id=self.supplier['id'],amount=2000,currency='CLP',
                  occurred_on='2026-01-02',method='cash',confirmed=True)
        p=ap.execute(self.store,'account_payment',body,'QA')
        self.assertEqual(ap.execute(self.store,'account_payment',body,'QA'),p)
        application=dict(request_id=str(uuid4()),version=1,confirmed=True,applications=[self.application(self.first,1000)])
        result=ap.execute(self.store,'account_apply',application,'QA',p['id'])
        self.assertEqual(ap.execute(self.store,'account_apply',application,'QA',p['id']),result)
        before=self.snapshot()
        with self.assertRaises(DomainError):ap.execute(self.store,'account_payment',dict(body,amount=3000),'QA')
        self.assertEqual(self.snapshot(),before)
        entry=self.entry(p['id']);allocation=entry['applications'][0]
        self.command('account_unapply',dict(version=entry['version'],invoice_version=allocation['invoice_version'],reason='Revertir crédito antes del pago'),allocation['id'])
        void=dict(request_id=str(uuid4()),version=self.entry(p['id'])['version'],reason='QA',invoices=[])
        result=ap.execute(self.store,'account_void',void,'QA',p['id'])
        before=self.snapshot()
        self.assertEqual(ap.execute(self.store,'account_void',void,'QA',p['id']),result)
        self.assertEqual(self.snapshot(),before)

    def test_concurrent_applications_only_one_wins(self):
        p=self.payment(10000);barrier=threading.Barrier(2);results=[]
        applications=[self.application(self.first,8000)]
        def run():
            barrier.wait()
            try:self.command('account_apply',dict(version=1,applications=applications),p['id']);results.append(200)
            except DomainError as error:results.append(error.status)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,[200,409])
        self.assertEqual(self.current(self.first)['paid'],8000)

    def test_additive_migration_repeated_preserves_legacy_payment_and_attachment(self):
        legacy=self.command('payment',dict(version=1,amount=2000,paid_on='2026-01-02',method='cash',confirmed=True),self.first['id'])
        ap.attach(self.store,self.first['id'],legacy['id'],b'%PDF-1.4\nQA\n%%EOF\n','qa.pdf','QA')
        with self.store.connect() as db:
            old={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('ap_invoices','ap_payments','ap_files','ap_events','ap_requests')}
            for t in ('ap_account_files','ap_account_applications','ap_account_entries'):db.execute('DROP TABLE '+t)
        for _ in range(3):
            self.store=Store(self.path)
            with self.store.connect() as db:
                self.assertEqual(old,{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in old})
            self.assertEqual(self.current(self.first)['paid'],2000)
            self.assertEqual(account.statement(self.store,self.supplier['id'])['entries'],[])
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],2000)

    def test_undated_historic_opening_payment_is_not_assigned_today(self):
        with self.store.connect() as db:
            db.execute("UPDATE ap_invoices SET issued_on='' WHERE id=?",(self.first['id'],))
            db.execute("INSERT INTO ap_payments(invoice_id,amount,paid_on,method,actor,created_at) VALUES(?,2000,'','other','QA','2026-01-01T00:00:00Z')",(self.first['id'],))
        statement=account.statement(self.store,self.supplier['id'])
        self.assertTrue(statement['totals']['consistent'])
        self.assertEqual(next(r for r in statement['journal'] if r['kind']=='legacy_payment')['date'],'')
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],0)

    def test_sii_unvalidated_invoice_and_credit_settlement_duplicate(self):
        preview=sii.upload(self.store,b'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n33;76111111-1;Cuenta QA;222;01/01/2026;3000\n','qa.csv','QA')
        sii.confirm(self.store,preview['id'],dict(rows=[2],confirmed=True),'QA')
        invoice=next(r for r in ap.view(self.store)['invoices'] if r['folio']=='222')
        with self.assertRaises(DomainError):self.payment(1000,[self.application(invoice,1000)])
        self.note(10000,[self.application(self.first,10000)])
        duplicate=sii.upload(self.store,b'Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Total\n33;76111111-1;Cuenta QA;100;01/01/2026;10000\n','duplicate.csv','QA')
        self.assertEqual(duplicate['rows'][0]['status'],'duplicate')
        self.assertEqual(duplicate['rows'][0]['matches'][0]['balance'],0)

    def test_proof_is_bound_deduplicated_and_preserved_after_void(self):
        p=self.payment(1000);content=b'%PDF-1.4\nQA\n%%EOF\n'
        first=account.attach(self.store,p['id'],content,'QA.pdf','QA')
        self.assertEqual(account.attach(self.store,p['id'],content,'again.pdf','QA')['id'],first['id'])
        self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['missing_proof'],0)
        self.void(p)
        self.assertEqual(len(self.entry(p['id'])['files']),1)
        with self.assertRaises(DomainError):account.attach(self.store,p['id'],b'<html/>','bad.pdf','QA')

    def test_schedule_and_message_use_balance_after_credit_note(self):
        self.note(3000,[self.application(self.first,3000)])
        current=self.current(self.first)
        message=ap.message(self.store,dict(items=[dict(id=current['id'],version=current['version'])]))
        self.assertEqual(message['total'],7000)
        with self.assertRaises(DomainError):self.command('schedule',dict(scheduled_on='2026-10-20',items=[dict(id=current['id'],version=current['version'],amount=7001)]))

    def test_void_after_later_applications_and_reversal_preserves_surplus_and_audit(self):
        for kind in ('payment','credit_note'):
            with self.subTest(kind=kind):
                creator = self.payment if kind == 'payment' else self.note
                entry = creator(8000,[self.application(self.first,2000)])
                stale = self.entry(entry['id'])
                self.command('account_apply',dict(version=stale['version'],applications=[self.application(self.second,3000)]),entry['id'])
                before = self.snapshot()
                with self.assertRaises(DomainError):
                    self.command('account_void',dict(version=stale['version'],reason='Vista antigua',invoices=[dict(id=self.first['id'],version=stale['applications'][0]['invoice_version'])]),entry['id'])
                self.assertEqual(self.snapshot(),before)
                current=self.entry(entry['id']);application=current['applications'][0]
                self.command('account_unapply',dict(version=current['version'],invoice_version=application['invoice_version'],reason='Aplicación ficticia a corregir'),application['id'])
                current=self.entry(entry['id'])
                self.command('account_apply',dict(version=current['version'],applications=[self.application(self.first,4000)]),entry['id'])
                self.assertEqual(self.entry(entry['id'])['available'],1000)
                if kind == 'payment':
                    with self.assertRaises(DomainError):self.void(entry)
                    for later in self.entry(entry['id'])['reversal_blockers']:
                        current=self.entry(entry['id']);application=next(a for a in current['applications'] if a['id']==later['id'])
                        self.command('account_unapply',dict(version=current['version'],invoice_version=application['invoice_version'],reason='Revertir uso posterior antes del pago'),application['id'])
                self.void(entry)
                final=self.entry(entry['id'])
                self.assertEqual(final['available'],0)
                self.assertEqual(final['voided_by'],'socio-cuenta-qa')
                self.assertTrue(all(a['voided_at'] and a['voided_by'] for a in final['applications']))
                self.assertEqual(final['applications'][0]['void_reason'],'Aplicación ficticia a corregir')
                self.assertEqual((self.current(self.first)['balance'],self.current(self.second)['balance']),(10000,6000))
                self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])
                self.assertEqual(reports.report(self.store,month='2026-01')['kpis']['payments'],0)
                if kind == 'credit_note':
                    with self.assertRaises(DomainError):self.note(1000)

    def test_repeated_schema_keeps_new_entries_applications_and_documents(self):
        entry=self.payment(5000,[self.application(self.first,1000)])
        account.attach(self.store,entry['id'],b'%PDF-1.4\nQA\n%%EOF','qa.pdf','QA')
        self.note(1000,[self.application(self.second,500)])
        tables=('ap_account_entries','ap_account_applications','ap_account_files','ap_payments','ap_events','ap_requests')
        with self.store.connect() as db:
            before={name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY 1')] for name in tables}
        for _ in range(3):
            self.store=Store(self.path)
            with self.store.connect() as db:
                self.assertEqual(before,{name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY 1')] for name in tables})
            self.assertTrue(account.statement(self.store,self.supplier['id'])['totals']['consistent'])

    def test_filtered_statement_export_retains_account_balances_and_payment_identity(self):
        entry=self.payment(5000,[self.application(self.first,3000),self.application(self.second,1000)],reference='Referencia QA')
        self.note(2000,[self.application(self.first,2000)])
        data=account.statement(self.store,self.supplier['id'],'2026-01-02','2026-01-03','payments')
        self.assertEqual(len(data['journal']),1)
        self.assertEqual((data['period']['opening_balance'],data['period']['closing_balance']),(16000,9000))
        self.assertEqual((data['period']['shown_credit'],data['journal'][0]['balance']),(5000,11000))
        self.assertEqual(data['totals']['net_balance'],9000)
        data['supplier']['name']='=2+3'
        rows=list(csv.DictReader(io.StringIO(account.export_statement(data).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['proveedor'],"'=2+3")
        self.assertEqual(rows[0]['id_cuenta'],str(entry['id']))
        self.assertIn('CC-'+str(entry['id']),rows[0]['movimiento'])
        self.assertIn('Referencia QA',rows[0]['movimiento'])
        self.assertEqual(rows[0]['saldo_final_intervalo_clp'],'9000')
        self.assertEqual(rows[0]['registrado_por'],'socio-cuenta-qa')

    def test_filter_validation_unknown_dates_empty_period_and_credit_void_category(self):
        for start,end,kind in [('2026-02-01','2026-01-01','all'),('bad','','all'),('','','unknown')]:
            with self.assertRaises(DomainError):account.statement(self.store,self.supplier['id'],start,end,kind)
        with self.store.connect() as db:
            db.execute("UPDATE ap_invoices SET issued_on='' WHERE id=?",(self.second['id'],))
        note=self.note(1000)
        self.void(note)
        data=account.statement(self.store,self.supplier['id'],kind='credit_notes')
        self.assertEqual({r['kind'] for r in data['journal']},{'credit_note','entry_void'})
        self.assertEqual(data['period']['undated_count'],1)
        empty=account.statement(self.store,self.supplier['id'],'2099-01-01','2099-01-02')
        self.assertEqual(empty['journal'],[])
        self.assertEqual(empty['period']['opening_balance'],16000)
        self.assertEqual(empty['period']['closing_balance'],16000)


if __name__ == '__main__':unittest.main()
