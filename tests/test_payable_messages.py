"""One coordination message per supplier; preparation never records payments."""
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch
from datetime import datetime,timezone

from erp import payables as ap
from erp.domain import DomainError
from erp.store import Store


class SupplierMessagesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'qa.sqlite3')
        self.a = self.command('supplier', dict(name='Proveedor A QA', tax_id='76111111-1', bank='Banco QA', account_number='000123'))
        self.b = self.command('supplier', dict(name='Proveedor B QA', tax_id='76222222-2'))

    def command(self, action, data, target=None):
        return ap.execute(self.store, action, dict(data, request_id=str(uuid4())), 'QA mensajes', target)

    def invoice(self, supplier, folio, amount):
        return self.command('invoice', dict(supplier_id=supplier['id'], document_type='invoice',
                            folio=folio, amount=amount, issued_on='2026-10-01', condition='credit'))

    def prepare(self, invoices, **extra):
        return ap.message(self.store, dict(items=[dict(id=i['id'], version=i['version']) for i in invoices], **extra))

    def test_one_message_per_supplier_with_only_selected_invoices(self):
        a1 = self.invoice(self.a, '101', 10000)
        a2 = self.invoice(self.a, '102', 20000)
        self.invoice(self.a, '103', 99000)
        b1 = self.invoice(self.b, '201', 4000)
        before = ap.view(self.store)
        result = self.prepare([a1, b1, a2], week_start='2026-10-14')
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['total'], 34000)
        self.assertEqual(len(result['messages']), 2)
        first, second = result['messages']
        self.assertEqual(first['invoice_ids'], [a1['id'], a2['id']])
        self.assertEqual(first['total'], 30000)
        self.assertNotIn('103', first['text'])
        self.assertNotIn(self.b['name'], first['text'])
        self.assertEqual(second['invoice_ids'], [b1['id']])
        self.assertIn('Día de pago: Por definir', first['text'])
        removed='Al completar este pago: reacciona con ✅ y envía el comprobante.'
        for message in result['messages']:
            self.assertNotIn(removed,message['text'])
            self.assertNotIn(removed,message['text_with_bank'])
        self.assertEqual(first['text'], '*Proveedor A QA*\nSemana del 12/10/2026 al 18/10/2026\nDía de pago: Por definir\n*Total a pagar: $30.000*\n\nFacturas incluidas:\n• Factura 101 · pagar $10.000 · Sin vencimiento registrado\n• Factura 102 · pagar $20.000 · Sin vencimiento registrado')
        self.assertIn('Al realizar los pagos, enviar los comprobantes indicando el proveedor y folio.', result['text'])
        self.assertEqual((result['week_start'], result['week_end']), ('2026-10-12', '2026-10-18'))
        self.assertIn('TOTAL A PAGAR: $34.000', result['summary'])
        self.assertEqual(ap.view(self.store), before)

    def test_bank_details_are_optional_in_individual_texts(self):
        row = self.invoice(self.a, '101', 10000)
        msg = self.prepare([row])['messages'][0]
        self.assertNotIn('000123', msg['text'])
        self.assertIn('Cuenta: 000123', msg['text_with_bank'])
        self.assertIn('Banco: Banco QA', msg['text_with_bank'])
        self.assertTrue(msg['text_with_bank'].startswith(msg['text']))

    def test_requested_amount_respects_abonos_and_schedule(self):
        row = self.invoice(self.a, '101', 10000)
        self.command('payment', dict(version=1, amount=3000, paid_on='2026-10-01', method='transfer', confirmed=True), row['id'])
        self.command('schedule', dict(scheduled_on='2026-10-14', items=[dict(id=row['id'], version=2, amount=5000)]))
        row = ap.view(self.store)['invoices'][0]
        result = self.prepare([row], week_start='2026-10-12')
        self.assertEqual(result['total'], 5000)
        self.assertIn('pagar $5.000 · saldo $7.000', result['messages'][0]['text'])
        self.assertIn('Día de pago: 14/10/2026', result['messages'][0]['text'])

    def test_mixed_days_and_dates_outside_week_remain_explicit(self):
        a1 = self.invoice(self.a, '101', 10000)
        a2 = self.invoice(self.a, '102', 20000)
        self.command('schedule', dict(scheduled_on='2026-10-20', items=[dict(id=a1['id'], version=1, amount=10000)]))
        rows = ap.view(self.store)['invoices']
        result = self.prepare(rows, week_start='2026-10-12')
        text = result['messages'][0]['text']
        self.assertIn('Varias fechas; ver facturas', text)
        self.assertIn('20/10/2026 (fuera de esta semana)', text)
        self.assertIn('Día: por definir', text)
        self.assertEqual(result['count'], 2)

    def test_invalid_week_or_stale_selection_cannot_prepare_list(self):
        row = self.invoice(self.a, '101', 10000)
        for value in ('', None, [], 'no fecha', '9999-12-31'):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.prepare([row], week_start=value)
        self.command('payment', dict(version=1, amount=10000, paid_on='2026-10-01', method='transfer', confirmed=True), row['id'])
        with self.assertRaises(DomainError):
            self.prepare([row])
        current = ap.view(self.store)['invoices'][0]
        with self.assertRaises(DomainError):
            self.prepare([current])

    def test_due_dates_use_chile_today_even_when_selected_week_differs(self):
        rows=[]
        for number,due in enumerate(['2026-10-12','2026-10-14','2026-10-15','']):
            rows.append(self.command('invoice',dict(supplier_id=self.a['id'],document_type='invoice',folio='D'+str(number),amount=1000,issued_on='2026-10-01',condition='credit',due_on=due)))
        before=ap.view(self.store)
        with patch.object(ap,'datetime') as clock:
            clock.now.side_effect=lambda tz:datetime(2026,10,15,1,0,tzinfo=timezone.utc).astimezone(tz)
            result=self.prepare(rows,week_start='2026-10-19')
        self.assertEqual(str(clock.now.call_args.args[0]),'America/Santiago')
        for text in (result['text'],result['messages'][0]['text'],result['messages'][0]['text_with_bank']):
            for value in ('Vencimiento: 12/10/2026 · vencida hace 2 días','Vencimiento: 14/10/2026 · vence hoy','Vencimiento: 15/10/2026 · vence en 1 día','Sin vencimiento registrado'):self.assertIn(value,text)
            self.assertNotIn('Al completar este pago: reacciona',text)
        self.assertEqual(ap.view(self.store),before)
