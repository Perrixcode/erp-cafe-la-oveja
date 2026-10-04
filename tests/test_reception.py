"""Recepción y decisiones: datos y cuentas estrictamente ficticios."""
import copy
import unittest
from urllib.error import HTTPError
from erp import reception
from erp.domain import DomainError
from erp.store import Store
from erp.toteat_inbox import get_received
from erp.toteat_scheduling import channel_from_platform
import test_sos as sos_tests


class ReceptionTests(unittest.TestCase):
    setUp=sos_tests.SOSTests.setUp
    close=sos_tests.SOSTests.close
    api=sos_tests.SOSTests.api
    login=sos_tests.SOSTests.login
    received=sos_tests.SOSTests.received
    sos_body=sos_tests.SOSTests.sos_body

    def prepare(self,paid=True,comment='Nombre: Cliente ficticio'):
        row,candidate,key=self.received(comment=comment)
        if paid:reception.observe_sale(self.store,self.scope,row,candidate)
        return row,candidate,get_received(self.inbox.path,key)

    def schedule(self,received):
        body=self.sos_body();body['source_key']=received['key']
        body['revision']=reception.context(self.store,received)['revision']
        return body

    def test_unpaid_force_same_identity_payment_empty_comment_and_local_edit_survive(self):
        self.login();row,candidate,received=self.prepare(False)
        body=self.schedule(received)
        order=self.api('/api/toteat/reception/schedule',body)[0]
        self.assertEqual(order['status_label'],'Agendado sin pago');self.assertFalse(order['payment_confirmed'])
        again=self.api('/api/toteat/reception/schedule',body)[0];self.assertEqual(order['id'],again['id'])
        self.assertEqual(self.api('/api/toteat')[0]['orders'],[])
        customer=dict(customer='Cliente corregido',customer_phone='000000009',pickup_at='2026-11-04T19:00',fulfillment='retiro',delivery_address='')
        edited=self.store.update_customer(order['id'],customer,self.partner,'Cambio pedido ficticio',order['version'])
        reception.observe_sale(self.store,self.scope,row,candidate)
        paid=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        self.assertEqual(paid['id'],order['id']);self.assertEqual(paid['customer'],edited['customer']);self.assertEqual(paid['pickup_at'],edited['pickup_at'])
        self.assertTrue(paid['payment_confirmed']);self.assertEqual(paid['source_comment'],row['comment'])
        self.assertEqual(Store(self.store.path).get(order['id'])['customer'],edited['customer'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)

    def test_paid_name_only_waits_for_review_partner_completes_and_schedules(self):
        self.login();row,candidate,received=self.prepare()
        with self.assertRaisesRegex(ValueError,'comment_requires_review'):self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        context=self.api('/api/toteat/reception?key='+__import__('urllib.parse').parse.quote(received['key']))[0]
        self.assertTrue(context['history']);self.assertTrue(context['payment'])
        result=self.api('/api/toteat/reception/schedule',self.schedule(received))[0]
        self.assertTrue(result['payment_confirmed']);self.assertEqual(result['scheduling_status'],'scheduled')

    def test_immediate_stays_out_of_agenda_and_can_be_scheduled_by_partner(self):
        self.login();row,candidate,received=self.prepare()
        body=dict(source_key=received['key'],revision=reception.context(self.store,received)['revision'],decision='immediate',reason='Venta de vitrina comprobada en fixture')
        self.api('/api/toteat/reception/decide',body);self.api('/api/toteat/reception/decide',body)
        with self.assertRaisesRegex(ValueError,'classified_immediate'):self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        board=self.api('/api/toteat')[0];self.assertEqual(board['orders'],[]);self.assertEqual(len(board['reviewed_orders']),1)
        self.assertEqual(self.store.stock(),[])
        order=self.api('/api/toteat/reception/schedule',self.schedule(received))[0]
        self.assertTrue(order['payment_confirmed'])

    def test_identity_revision_permissions_and_products_are_enforced(self):
        row,candidate,received=self.prepare(False);body=self.schedule(received)
        with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/schedule',body)
        denied.exception.close();self.assertEqual(denied.exception.code,403)
        self.login();reception.observe_sale(self.store,self.scope,row,candidate)
        with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/schedule',body)
        denied.exception.close();self.assertEqual(denied.exception.code,409)
        body=self.schedule(received);body['order']['items']=[dict(sku='forged',quantity=99)]
        order=self.api('/api/toteat/reception/schedule',body)[0];self.assertEqual(order['items'][0]['quantity'],2)

    def test_force_requires_partner_reason_and_audits_without_payment(self):
        _,_,received=self.prepare(False);body=self.schedule(received)
        for role in ('production','cashier'):
            username='qa-'+role
            self.auth.create(username,'Cuenta ficticia '+role,'Clave-ficticia-QA-2026',role)
            self.api('/api/partner/login',{'username':username,'password':'Clave-ficticia-QA-2026'})
            with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/schedule',body,headers={'X-ERP-Role':'socio'})
            denied.exception.close();self.assertEqual(denied.exception.code,403)
            immediate=dict(source_key=received['key'],revision=body['revision'],decision='immediate',reason='Intento ficticio sin permiso')
            with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/decide',immediate)
            denied.exception.close();self.assertEqual(denied.exception.code,403)
        self.login()
        with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/schedule',dict(body,reason=' '))
        denied.exception.close();self.assertEqual(denied.exception.code,400)
        order=self.api('/api/toteat/reception/schedule',body)[0]
        self.assertFalse(order['payment_confirmed']);self.assertEqual(order['receipt']['status'],'pending')
        events=self.store.history(order['id'])
        event=next(e for e in events if e['action']=='agendado_por_socio')
        self.assertEqual(event['actor'],'socio-qa');self.assertEqual(event['reason'],body['reason'])

    def test_legacy_closed_comment_is_review_not_waiting_for_close(self):
        row,_,received=self.prepare(False,comment='Nombre: Cliente ficticio')
        details=reception.context(self.store,received)
        self.assertEqual(details['original_comment'],row['comment'])
        self.assertIsNotNone(details['closed_at']);self.assertIsNone(details['payment'])
        self.assertEqual(details['review_state'],'schedule_incomplete')
        self.assertIn('Fecha de entrega',details['missing_fields']);self.assertIn('Hora de entrega',details['missing_fields'])
        self.assertIsNone(details['decision']);self.assertIsNone(details['order_id'])
        row,_,received=self.prepare(True,comment='Nombre: Cliente ficticio')
        details=reception.context(self.store,received)
        self.assertIsNotNone(details['payment']);self.assertEqual(details['review_state'],'schedule_incomplete')

    def test_manual_immediate_records_delivery_without_inventing_payment_and_sync_preserves_it(self):
        self.login();row,candidate,received=self.prepare(False)
        body=dict(source_key=received['key'],revision=reception.context(self.store,received)['revision'],decision='immediate',reason='Socio confirma entrega ficticia desde vitrina')
        with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/decide',body,headers={'Origin':'https://otro-sitio.invalid'})
        denied.exception.close();self.assertEqual(denied.exception.code,403)
        with self.assertRaises(HTTPError) as denied:self.api('/api/toteat/reception/decide',dict(body,reason=''))
        denied.exception.close();self.assertEqual(denied.exception.code,400)
        self.api('/api/toteat/reception/decide',body);self.api('/api/toteat/reception/decide',body)
        details=reception.context(self.store,received,True)
        self.assertIsNone(details['payment']);self.assertEqual(details['review_state'],'delivered_immediate')
        events=[e for e in details['history'] if e['action']=='venta_inmediata_confirmada']
        self.assertEqual(len(events),1);self.assertFalse(events[0]['after']['payment_recorded'])
        self.assertEqual(events[0]['after']['delivery_status'],'Entregada inmediata')
        self.assertEqual(events[0]['actor'],'socio-qa')
        reception.observe_sale(self.store,self.scope,row,candidate)
        with self.assertRaisesRegex(ValueError,'classified_immediate'):self.store.import_toteat_schedule(self.scope,row,candidate)
        self.assertEqual(reception.context(self.store,received)['review_state'],'delivered_immediate')
        self.assertEqual(self.api('/api/toteat')[0]['orders'],[])

    def test_explicit_cancellation_dedupes_preserves_payment_blocks_reversal(self):
        key=self.order['source_id'];row=dict(orderId=self.row['orderId'],orderStatus='CANCELLED')
        before=self.store.get(self.order['id'])
        reception.inspect_cancellation(self.store,self.scope,row);reception.inspect_cancellation(self.store,self.scope,row)
        order=self.store.get(self.order['id']);self.assertEqual(order['status_label'],'Anulado')
        self.assertEqual(order['items'][0]['status'],'cancelado');self.assertEqual(order['payment_confirmed'],before['payment_confirmed'])
        history=self.store.history(order['id']);self.assertEqual(history[0]['before']['status_label'],'Agendado')
        self.assertEqual(sum(e['action']=='anulacion_detectada' for e in history),1)
        with self.assertRaises(DomainError):self.store.transition(order['id'],order['items'][0]['id'],'pendiente','QA','Reversión ficticia',order['version'],True)
        with self.assertRaisesRegex(ValueError,'source_cancelled'):self.store.import_toteat_schedule(self.scope,self.row,self.candidate)

    def test_partial_and_credit_note_require_review_numeric_status_never_cancels(self):
        row,candidate,received=self.prepare();product=self.store.catalog()[0]['source_product']['idToteat']
        reception.inspect_cancellation(self.store,self.scope,dict(orderId=row['orderId'],orderStatus=201))
        self.assertIsNone(reception.context(self.store,received)['alert'])
        lines=[dict(isExtra=False,productCodeToteat=product,quantity=1,lineNumber=n,cancelled=n==1) for n in (1,2)]
        reception.inspect_cancellation(self.store,self.scope,dict(orderId=row['orderId'],document=dict(line=lines)))
        context=reception.context(self.store,received);self.assertEqual(context['alert']['state'],'partial_cancel')
        with self.assertRaises(DomainError):reception.schedule(self.store,received,self.sos_body()['order'],self.partner,'Prueba',self.sos_body()['request_id'],context['revision'])
        reception.resolve_alert(self.store,received['key'],self.partner,'Cliente confirma encargo ficticio',context['alert']['version'],'keep')
        reception.observe_sale(self.store,self.scope,dict(row,fiscalType='NC',products=[]))
        self.assertEqual(reception.context(self.store,received)['alert']['state'],'refund_review')
        self.assertIsNone(reception.context(self.store,received)['alert']['resolution'])

    def test_platform_aliases(self):
        for raw,expected in [('Local','Presencial'),(' IG ','Instagram'),('Instagram','Instagram'),('web','Web/Mercat'),('Mercat','Web/Mercat'),('Otro','No informado')]:self.assertEqual(channel_from_platform(raw),expected)

if __name__=='__main__':unittest.main()
