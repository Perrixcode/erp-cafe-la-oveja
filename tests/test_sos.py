"""Fixtures locales: no cuentas, pedidos ni pagos de la instalación real."""
import copy
import json
import uuid
import unittest
from urllib.error import HTTPError
from erp import sos
from erp.domain import DomainError
from erp.toteat_inbox import Inbox,read_inbox,canonical
from scripts.toteat_sales_worker import sales_cycle
import test_partner_customer as base


class SOSTests(unittest.TestCase):
    close=base.PartnerCustomerTests.close
    api=base.PartnerCustomerTests.api
    login=base.PartnerCustomerTests.login
    # Solo reutiliza el armado de catálogo/servidor y helpers HTTP, sin heredar casos.
    def setUp(self):
        base.PartnerCustomerTests.setUp(self)
        # Este pedido fuente sirve para probar que una identidad ya agendada no se duplica.
        self.existing=self.order
        self.inbox=Inbox(self.root/'toteat-reader.sqlite3')
        self.partner={'username':'socio-qa','name':'Socio ficticio'}

    def sos_body(self):
        return {'request_id':str(uuid.uuid4()),'reason':'Contingencia ficticia de conexión',
                'order':{'customer':'Cliente SOS ficticio','customer_phone':'000000001','pickup_at':'2026-11-03T16:00','fulfillment':'despacho','delivery_address':'Calle Ficticia 20','channel':'Presencial','payment_status':'unpaid','payment_evidence':'','is_test':True,'comments':'Nota SOS ficticia','items':[{'sku':self.store.catalog()[0]['sku'],'quantity':2}]}}

    def create_sos(self,body=None):return self.api('/api/sos/orders',body or self.sos_body())[0]
    def authorize(self,order,reason='Socio autoriza prueba sin pago'):
        return self.api(f"/api/orders/{order['id']}/schedule-without-payment",{'version':order['version'],'reason':reason})[0]

    def received(self,number=9007199254740200,comment=None):
        row=dict(self.row,orderId=number,paymentId=number+1,comment=comment or 'Nombre: Cliente SOS ficticio\nTeléfono: 000000001\nFecha: 03/11/2026\nHorario: 16:00\nPlataforma: LOCAL')
        from erp.toteat_comments_probe import inspect_sales
        _,candidates=inspect_sales({'ok':True,'data':[row]},[p['source_product'] for p in self.store.catalog()],str(number));candidate=candidates[0]
        key=self.inbox.receive_sale(self.scope,candidate,self.store.catalog())
        return row,candidate,key

    def test_unpaid_draft_requires_partner_override_and_never_becomes_paid(self):
        self.login();order=self.create_sos()
        self.assertFalse(order['payment_confirmed']);self.assertEqual(order['manual_scheduling']['status'],'draft')
        self.assertEqual(self.store.list('2026-11-01','2026-11-30'),[])
        with self.assertRaises(DomainError):self.store.transition(order['id'],order['items'][0]['id'],'marcado_solicitado','QA','Prueba',order['version'])
        scheduled=self.authorize(order);again=self.authorize(order)
        self.assertEqual(scheduled['id'],again['id']);self.assertEqual(scheduled['version'],again['version'])
        self.assertEqual(scheduled['status_label'],'Agendado sin pago');self.assertFalse(scheduled['payment_confirmed'])
        self.assertEqual(scheduled['receipt']['status'],'pending');self.assertEqual(scheduled['items'][0]['status'],'pendiente')
        self.assertEqual(len(self.store.list('2026-11-01','2026-11-30')),1)
        history=self.store.history(order['id']);self.assertEqual(len(history),2)
        self.assertEqual(history[0]['actor'],'Socio ficticio');self.assertEqual(history[0]['after']['socio_autenticado'],'socio-qa')

    def test_no_session_fake_roles_csrf_and_logout_cannot_authorize(self):
        body=self.sos_body()
        for path in ['/api/sos/orders','/api/orders/1/schedule-without-payment']:
            with self.assertRaises(HTTPError) as denied:self.api(path,body,headers={'Cookie':'role=socio','X-ERP-Role':'socio'})
            denied.exception.close();self.assertEqual(denied.exception.code,403)
        self.login()
        with self.assertRaises(HTTPError) as denied:self.api('/api/sos/orders',body,headers={'Origin':'https://otro-sitio.invalid'})
        denied.exception.close();self.assertEqual(denied.exception.code,403)
        order=self.create_sos(body);self.api('/api/partner/logout',{})
        for path in [f"/api/orders/{order['id']}",f"/api/orders/{order['id']}/history"]:
            with self.assertRaises(HTTPError) as denied:self.api(path)
            denied.exception.close();self.assertEqual(denied.exception.code,401)
        with self.assertRaises(HTTPError) as denied:self.authorize(order)
        denied.exception.close();self.assertEqual(denied.exception.code,403)

    def test_repeat_request_id_and_same_business_data_do_not_duplicate(self):
        self.login();body=self.sos_body();first=self.create_sos(body);second=self.create_sos(body)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(self.store.history(first['id'])),1)
        changed=copy.deepcopy(body);changed['order']['customer']='Otro cliente ficticio'
        with self.assertRaises(HTTPError) as conflict:self.create_sos(changed)
        conflict.exception.close();self.assertEqual(conflict.exception.code,409)
        body['request_id']=str(uuid.uuid4())
        with self.assertRaises(HTTPError) as conflict:self.create_sos(body)
        conflict.exception.close();self.assertEqual(conflict.exception.code,409)

    def test_manual_paid_requires_evidence_and_is_labelled_as_declaration(self):
        self.login();body=self.sos_body();body['order']['payment_status']='paid_manual'
        with self.assertRaises(HTTPError) as denied:self.create_sos(body)
        denied.exception.close();self.assertEqual(denied.exception.code,400)
        body['order'].update(payment_evidence='Verificación bancaria ficticia',payment_verified=True)
        order=self.create_sos(body);self.assertTrue(order['payment_confirmed'])
        self.assertEqual(order['manual_scheduling']['payment_status'],'paid_manual');self.assertEqual(order['scheduling_status'],'scheduled')
        with self.assertRaises(HTTPError) as denied:self.authorize(order)
        denied.exception.close();self.assertEqual(denied.exception.code,409)

    def test_invalid_customer_items_time_and_unknown_source_roll_back(self):
        self.login()
        for patch in [{'delivery_address':''},{'pickup_at':'2026-09-06T00:30'},{'customer_phone':'hola'},{'payment_status':'paid'},{'items':[{'sku':'UNKNOWN','quantity':1}]},{'items':[{'sku':self.store.catalog()[0]['sku'],'quantity':True}]}]:
            body=self.sos_body();body['order'].update(patch)
            with self.assertRaises(HTTPError) as denied:self.create_sos(body)
            denied.exception.close();self.assertEqual(denied.exception.code,400)
        body=self.sos_body();body['source_key']='not-observed'
        with self.assertRaises(HTTPError) as denied:self.create_sos(body)
        denied.exception.close();self.assertEqual(denied.exception.code,400)
        self.assertEqual(sos.list_orders(self.store),[])

    def test_exact_received_identity_settles_same_sos_and_preserves_local_data(self):
        self.login();row,candidate,key=self.received();body=self.sos_body();body['source_key']=key
        order=self.authorize(self.create_sos(body));before_items=order['items']
        self.assertEqual(self.api('/api/toteat')[0]['orders'],[])
        order=self.store.transition(order['id'],order['items'][0]['id'],'marcado_solicitado','QA','Prueba',order['version'])
        paid=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        repeated=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        self.assertEqual(paid['id'],order['id']);self.assertEqual(repeated['version'],paid['version'])
        self.assertEqual(paid['source'],'manual-sos');self.assertTrue(paid['payment_confirmed'])
        self.assertEqual(paid['manual_scheduling']['payment_status'],'paid')
        self.assertEqual(paid['comments'],'Nota SOS ficticia');self.assertEqual(paid['source_comment'],row['comment'])
        self.assertEqual(paid['fulfillment'],'despacho');self.assertEqual(paid['delivery_address'],'Calle Ficticia 20')
        self.assertEqual(paid['items'][0]['status'],'marcado_solicitado');self.assertEqual(paid['items'][0]['id'],before_items[0]['id'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)
        self.inbox.set_sales_state(key,{'status':'needs_review','reason':'sos_product_mismatch'})
        self.assertEqual(len(self.api('/api/toteat')[0]['orders']),1)

    def test_ambiguous_match_is_held_until_partner_links_or_confirms_distinct(self):
        self.login();order=self.authorize(self.create_sos());row,candidate,key=self.received()
        with self.assertRaisesRegex(ValueError,'possible_sos_duplicate'):self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        reviews=sos.list_reviews(self.store);self.assertEqual(len(reviews),1)
        self.assertEqual(reviews[0]['candidates'][0]['order_id'],order['id'])
        payload={'source_key':key,'decision':'link','order_id':order['id'],'version':reviews[0]['version'],'reason':'Identidad de comanda comprobada por socio ficticio'}
        self.api('/api/sos/reconcile',payload)
        self.api('/api/sos/reconcile',payload)
        result=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        self.assertEqual(result['id'],order['id']);self.assertEqual(sos.list_reviews(self.store),[])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)

    def test_partner_distinct_resolution_is_audited_before_separate_import(self):
        self.login();order=self.authorize(self.create_sos());row,candidate,key=self.received()
        with self.assertRaises(ValueError):self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        review=sos.list_reviews(self.store)[0]
        self.api('/api/sos/reconcile',{'source_key':key,'decision':'distinct','version':review['version'],'reason':'Se comprobaron dos encargos diferentes en fixture'})
        imported=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        self.assertNotEqual(imported['id'],order['id']);self.assertEqual(self.store.history(order['id'])[0]['action'],'conciliacion_distinta')

    def test_already_imported_source_and_mismatching_products_cannot_link(self):
        self.login();key=self.inbox.receive_sale(self.scope,self.candidate,self.store.catalog());body=self.sos_body();body['source_key']=key
        with self.assertRaises(HTTPError) as denied:self.create_sos(body)
        denied.exception.close();self.assertEqual(denied.exception.code,409)
        row,candidate,key=self.received();body=self.sos_body();body.update(source_key=key);body['order']['items'][0]['quantity']=3
        with self.assertRaises(HTTPError) as denied:self.create_sos(body)
        denied.exception.close();self.assertEqual(denied.exception.code,400)
        self.assertEqual(sos.list_orders(self.store),[])

    def test_sos_arriving_between_projection_and_insert_cannot_create_second_demand(self):
        from unittest.mock import patch
        self.login();row,candidate,key=self.received();original=sos.hold_possible_duplicate;created=[]
        def concurrent(store,*args,**kwargs):
            result=original(store,*args,**kwargs)
            if not created:
                body=self.sos_body()
                created.append(sos.create(store,body['order'],self.partner,body['reason'],body['request_id']))
            return result
        with patch('erp.sos.hold_possible_duplicate',side_effect=concurrent):
            with self.assertRaisesRegex(ValueError,'possible_sos_duplicate'):
                self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)

    def test_simulated_sos_does_not_hold_a_real_sale(self):
        self.login();self.create_sos();row,candidate,key=self.received()
        real=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=False)
        self.assertFalse(real['is_simulation']);self.assertEqual(sos.list_reviews(self.store),[])

    def test_source_test_marker_is_honored_before_settling_linked_sos(self):
        self.login();row,candidate,key=self.received(comment='Cliente SOS ficticio\nFecha: 03/11/2026\nHorario: 16:00\nPrueba, no considerar')
        body=self.sos_body();body['source_key']=key
        manual=self.create_sos(body)
        linked=self.store.import_toteat_schedule(self.scope,row,candidate)
        self.assertEqual(linked['id'],manual['id']);self.assertTrue(linked['is_simulation'])
