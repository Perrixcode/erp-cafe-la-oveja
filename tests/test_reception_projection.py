"""Regresión de Lectura tras corregir entrega inmediata; todo es sintético."""
from datetime import date
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import inbox_view
from erp import reception
from erp.store import Store
from erp.toteat_inbox import get_received, reception_history
from scripts.toteat_sales_worker import sales_cycle
import test_reception as reception_tests


class ReceptionProjectionTests(unittest.TestCase):
    setUp = reception_tests.ReceptionTests.setUp
    close = reception_tests.ReceptionTests.close
    api = reception_tests.ReceptionTests.api
    login = reception_tests.ReceptionTests.login
    received = reception_tests.ReceptionTests.received
    sos_body = reception_tests.ReceptionTests.sos_body
    prepare = reception_tests.ReceptionTests.prepare
    schedule = reception_tests.ReceptionTests.schedule

    def poll(self, row):
        config = {
            'scope': self.scope, 'binary': 'unused-fixture',
            'shift_start': '2026-10-04', 'start_after': '2026-10-04T00:00:00+00:00',
            'test_order_ids': [str(row['orderId'])],
        }
        # Ejecuta el lector real con transporte y base temporales, sin credenciales.
        with patch('scripts.toteat_sales_worker.configuration', return_value=config), \
                patch('scripts.toteat_sales_worker.Store', return_value=self.store):
            sales_cycle(self.inbox, self.root, lambda *args: {'ok': True, 'data': [row]},
                        current_day=date(2026, 10, 4))

    def decide(self, key, decision):
        received = get_received(self.inbox.path, key)
        return self.api('/api/toteat/reception/decide', {
            'source_key': key, 'decision': decision,
            'revision': reception.context(self.store, received)['revision'],
            'reason': 'Corrección de clasificación ficticia',
        })[0]

    def assert_scheduled_view(self, order):
        board = self.api('/api/toteat')[0]
        self.assertEqual(board['orders'], [])
        self.assertEqual(board['reviewed_orders'], [])
        self.assertEqual(board['scheduled_orders'], 1)
        agenda = self.api('/api/board?date=2026-11-03&period=day&scope=tests')[0]
        self.assertEqual([o['id'] for o in agenda['orders']], [order['id']])
        self.assertEqual(agenda['toteat']['orders'], [])

    def corrected_immediate(self, reopen):
        self.login()
        row, _, received = self.prepare(False)
        key = received['key']
        self.poll(row)
        self.assertEqual(len(self.api('/api/toteat')[0]['orders']), 1)
        self.decide(key, 'immediate')
        self.poll(row)
        self.assertEqual(get_received(self.inbox.path, key)['scheduling']['reason'], 'classified_immediate')
        self.assertEqual(len(self.api('/api/toteat')[0]['reviewed_orders']), 1)
        if reopen:
            self.decide(key, 'review')
            self.poll(row)
            self.assertEqual(get_received(self.inbox.path, key)['scheduling']['reason'], 'manual_review_required')
            self.assertEqual(len(self.api('/api/toteat')[0]['orders']), 1)
        body = self.schedule(get_received(self.inbox.path, key))
        order = self.api('/api/toteat/reception/schedule', body)[0]
        return row, key, body, order

    def test_reopen_then_schedule_uses_current_decision_before_next_poll(self):
        row, key, body, order = self.corrected_immediate(reopen=True)
        old_reader = get_received(self.inbox.path, key)['scheduling']
        old_source_history = reception_history(self.inbox.path, key)
        self.assert_scheduled_view(order)
        # La entrega física reportada no es un registro digital de entrega.
        self.assertTrue(all(i['status'] == 'pendiente' for i in order['items']))
        self.assertEqual(order['delivery_timing'], 'scheduled')
        self.assertTrue(order['payment_confirmed'])
        self.assertEqual(order['source_comment'], row['comment'])
        self.assertEqual(get_received(self.inbox.path, key)['scheduling'], old_reader)
        self.assertEqual(reception_history(self.inbox.path, key), old_source_history)

        again = self.api('/api/toteat/reception/schedule', body)[0]
        self.assertEqual(again, order)
        new_request = self.schedule(get_received(self.inbox.path, key))
        with self.assertRaises(HTTPError) as conflict:
            self.api('/api/toteat/reception/schedule', new_request)
        conflict.exception.close()
        self.assertEqual(conflict.exception.code, 409)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT order_id FROM toteat_order_links WHERE source_id=?', (key,)).fetchone()[0], order['id'])
        history = self.store.history(order['id'])
        actions = [e['action'] for e in reception.context(self.store, get_received(self.inbox.path, key), True)['history']]
        for action in ('venta_inmediata_confirmada', 'revision_reabierta', 'agendado_por_socio'):
            self.assertIn(action, actions)
        for _ in range(2):
            self.poll(row)
            self.assert_scheduled_view(order)
            self.assertEqual(self.store.get(order['id']), order)
            self.assertEqual(self.store.history(order['id']), history)
        self.assertEqual(inbox_view(self.inbox.path, Store(self.store.path))['orders'], [])

    def test_direct_schedule_supersedes_previous_immediate_poll(self):
        _, _, _, order = self.corrected_immediate(reopen=False)
        self.assert_scheduled_view(order)

    def test_explicit_digital_delivery_keeps_same_link_and_does_not_reopen_reading(self):
        _, key, _, order = self.corrected_immediate(reopen=True)
        for status in ('marcado_solicitado', 'marcado', 'entregado'):
            order = self.store.transition(order['id'], order['items'][0]['id'], status,
                                          'socio-qa', 'Movimiento ficticio confirmado', order['version'])
        self.assert_scheduled_view(order)
        self.assertEqual(self.store.get(order['id'])['items'][0]['status'], 'entregado')
        self.assertEqual(reception.context(self.store, get_received(self.inbox.path, key))['order_id'], order['id'])

    def test_manual_schedule_resolves_only_classification_and_missing_schedule_data(self):
        self.login()
        _, _, received = self.prepare()
        order = self.api('/api/toteat/reception/schedule', self.schedule(received))[0]
        for reason in ('classified_immediate', 'manual_review_required', 'comment_requires_review',
                       'web_delivery_contract_requires_review'):
            with self.subTest(reason=reason):
                self.inbox.set_sales_state(received['key'], {'status': 'needs_review', 'reason': reason})
                self.assert_scheduled_view(order)

    def test_financial_product_and_unknown_reviews_remain_visible_after_schedule(self):
        self.login()
        _, _, received = self.prepare()
        order = self.api('/api/toteat/reception/schedule', self.schedule(received))[0]
        for reason in ('multiple_payments_require_review', 'payment_not_settled', 'balance_requires_review',
                       'refund_requires_review', 'sos_product_mismatch', 'sos_test_scope_mismatch',
                       'changed_order_already_in_progress', 'changed_product_lines_require_review',
                       'receipt_order_mismatch', 'source_requires_review', 'future_unknown_reason'):
            with self.subTest(reason=reason):
                self.inbox.set_sales_state(received['key'], {'status': 'needs_review', 'reason': reason})
                board = self.api('/api/toteat')[0]
                self.assertEqual([r['key'] for r in board['orders']], [received['key']])
                self.assertEqual(board['orders'][0]['reception']['order_id'], order['id'])
        self.assertEqual(self.store.get(order['id']), order)

    def test_source_alert_overrides_resolved_classification(self):
        _, key, _, order = self.corrected_immediate(reopen=True)
        reception.record_alert(self.store, key, 'partial_cancel', {'fixture': 'anulación parcial'})
        board = self.api('/api/toteat')[0]
        self.assertEqual([r['key'] for r in board['orders']], [key])
        self.assertEqual(board['orders'][0]['reception']['alert']['state'], 'partial_cancel')
        self.assertEqual(self.store.get(order['id'])['items'], order['items'])

    def test_no_partner_decision_does_not_clear_reader_review_even_with_imported_order(self):
        self.login()
        _, _, received = self.prepare()
        existing_key = self.inbox.receive_sale(self.scope, self.candidate, self.store.catalog())
        for reason in ('classified_immediate', 'manual_review_required', 'comment_requires_review',
                       'web_delivery_contract_requires_review'):
            with self.subTest(reason=reason):
                for key in (received['key'], existing_key):
                    self.inbox.set_sales_state(key, {'status': 'needs_review', 'reason': reason})
                board = self.api('/api/toteat')[0]
                self.assertEqual({r['key'] for r in board['orders']}, {received['key'], existing_key})

    def test_decision_for_another_order_cannot_clear_reader_review(self):
        _, key, _, order = self.corrected_immediate(reopen=True)
        with self.store.connect() as db:
            db.execute('UPDATE reception_decisions SET order_id=? WHERE source_id=?', (self.existing['id'], key))
        board = self.api('/api/toteat')[0]
        self.assertEqual([r['key'] for r in board['orders']], [key])
        self.assertEqual(board['orders'][0]['reception']['order_id'], order['id'])

    def test_authorized_unpaid_schedule_does_not_infer_payment_or_delivery(self):
        self.login()
        _, _, received = self.prepare(False)
        self.decide(received['key'], 'review')
        self.inbox.set_sales_state(received['key'], {'status': 'needs_review', 'reason': 'manual_review_required'})
        order = self.api('/api/toteat/reception/schedule', self.schedule(received))[0]
        self.assert_scheduled_view(order)
        self.assertFalse(order['payment_confirmed'])
        self.assertEqual(order['manual_scheduling']['payment_status'], 'unpaid')
        self.assertEqual(order['items'][0]['status'], 'pendiente')

    def test_delivery_block_and_invalid_manual_date_do_not_reopen_corrected_delivered_order(self):
        self.login()
        comment = ('*** DELIVERY D-QA-1 ***\nENTREGA: viernes 16 07:55 p. m.\nZONA: Mercat\n'
                   'NOMBRE: Cliente ficticio\nTELÉFONO: 000000000\nFECHA: LUNES19DEOCTIBRE\n'
                   'HORA:13:00\nESTADO:PAGADO\nPLATAFORMA:LOCAL')
        row, _, received = self.prepare(False, comment=comment)
        self.poll(row)
        key = received['key']
        self.assertEqual(get_received(self.inbox.path, key)['scheduling']['reason'], 'comment_requires_review')
        self.decide(key, 'immediate')
        # El socio corrige antes de otro polling; persiste el motivo del comentario.
        body = self.schedule(get_received(self.inbox.path, key))
        order = self.api('/api/toteat/reception/schedule', body)[0]
        for status in ('marcado_solicitado', 'marcado', 'entregado'):
            order = self.store.transition(order['id'], order['items'][0]['id'], status,
                                          'socio-qa', 'Entrega ficticia confirmada', order['version'])
        details = reception.context(self.store, get_received(self.inbox.path, key))
        self.assertEqual(details['parsed']['issues'], ['fecha_pendiente'])
        self.assertIsNone(details['parsed']['pickup_at'])
        self.assertEqual(details['original_comment'], comment)
        self.assertEqual(order['pickup_at'], body['order']['pickup_at'])
        self.assertEqual(order['items'][0]['status'], 'entregado')
        self.assertEqual(order['source_comment'], comment)
        self.assert_scheduled_view(order)


if __name__ == '__main__':
    unittest.main()
