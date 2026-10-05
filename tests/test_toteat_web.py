"""Contrato web observado, reproducido solo con personas e identidades ficticias."""
from copy import deepcopy
import unittest
from erp import reception
from erp.domain import DomainError
from erp.toteat_inbox import get_received, read_inbox
from erp.toteat_web import project
import test_sos


def web_row(product=9001, identifier=9007199254740999):
    return {'orderId':identifier,'channel':'webstore','orderStatus':200,'status':'created',
            'document':{'customer':{'name':'Cliente web ficticio','phoneNumber':'000000009','email':'demo@example.invalid',
                                     'delivery':{'address':'Dirección ficticia 123','city':'Ciudad demo'}},
                        'line':[{'lineNumber':1,'productCodeToteat':product,'productName':'Torta ficticia entera',
                                 'quantity':2,'isExtra':False,'amountAfterTax':22000,'status':'PRINTED'}],
                        'payments':[{'id':identifier+1,'amount':22000,'amountPaid':22000,'discount':{'amount':0},
                                     'paymentForms':[{'method':'MERCAT','amount':22000}],
                                     'urlDTE':'https://example.invalid/boleta-ficticia.pdf'}]}}


class WebProjectionTests(unittest.TestCase):
    def test_complete_payment_does_not_infer_timing_delivery_or_receipt_download(self):
        value=project(web_row())
        self.assertEqual(value['payment']['payment_id'],'9007199254741000')
        self.assertEqual(value['payment']['paid'],'22000')
        self.assertIsNone(value['timing']);self.assertIsNone(value['fulfillment'])
        self.assertTrue(value['receipt_available']);self.assertNotIn('urlDTE',str(value))

    def test_unpaid_partial_unverified_methods_and_malformed_finances_require_review(self):
        for changes in [{'amountPaid':0},{'amountPaid':10000},{'amountPaid':None},{'amount':True},
                        {'amountPaid':'NaN'},{'amount':20000},{'paymentForms':[]},
                        {'paymentForms':[{'method':'CASH','amount':22000}]},
                        {'paymentForms':[{'method':'MERCAT','amount':10000}]},
                        {'discount':{'amount':1000}}]:
            with self.subTest(changes=changes):
                row=web_row();row['document']['payments'][0].update(changes)
                self.assertIsNone(project(row)['payment'])

    def test_multiple_and_conflicting_payments_do_not_double_count(self):
        row=web_row();payment=row['document']['payments'][0]
        row['document']['payments'].append(deepcopy(payment))
        self.assertEqual(project(row)['payment']['total'],'22000')
        row['document']['payments'][1]['id']+=1
        self.assertIsNone(project(row)['payment'])
        row['document']['payments'][1]['id']=payment['id'];row['document']['payments'][1]['amountPaid']=1
        self.assertIsNone(project(row)['payment'])

    def test_non_web_order_never_uses_web_payment_rules(self):
        row=web_row();row['channel']='pos';self.assertIsNone(project(row))


class WebReceptionTests(unittest.TestCase):
    setUp=test_sos.SOSTests.setUp
    close=test_sos.SOSTests.close
    api=test_sos.SOSTests.api
    login=test_sos.SOSTests.login
    received=test_sos.SOSTests.received
    sos_body=test_sos.SOSTests.sos_body

    def opened(self,comment=None):
        row,candidate,key=self.received(comment=comment)
        product=self.store.catalog()[0]['source_product']['idToteat']
        web=web_row(product,row['orderId'])
        self.inbox.apply({'ok':True,'data':[web]},self.scope,{product})
        # receive_sale en la fixture adjunta comentario cerrado: retirar solo esa evidencia ficticia.
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(self.inbox.path)) as db,db:db.execute('DELETE FROM received_sales_comments')
        return row,candidate,key,web

    def test_open_paid_client_fields_without_agenda_or_stock_and_repeat_is_idempotent(self):
        row,candidate,key,web=self.opened()
        received=get_received(self.inbox.path,key);ctx=reception.context(self.store,received)
        self.assertEqual(ctx['review_state'],'web_paid_pending_fulfillment')
        self.assertEqual(ctx['channel'],'Web/Mercat');self.assertFalse(ctx['can_classify_immediate'])
        self.assertEqual(ctx['parsed']['customer'],'Cliente web ficticio')
        self.assertEqual(ctx['parsed']['customer_phone'],'000000009')
        self.assertIsNone(ctx['parsed']['pickup_at']);self.assertIsNone(ctx['closed_at']);self.assertIsNone(ctx['order_id'])
        self.assertNotIn('Nombre y apellido',ctx['missing_fields']);self.assertIn('Fecha de entrega',ctx['missing_fields'])
        before=read_inbox(self.inbox.path,False)
        self.assertFalse(self.inbox.receive_detail({'ok':True,'data':web},self.scope,{web['document']['line'][0]['productCodeToteat']}))
        self.assertEqual(read_inbox(self.inbox.path,False),before)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)
        self.assertEqual(self.store.stock(),[])

    def test_closed_sale_keeps_same_identity_and_never_auto_schedules_from_complete_comment(self):
        row,candidate,key,web=self.opened()
        self.inbox.receive_sale(self.scope,candidate,self.store.catalog())
        candidate=dict(candidate,source_channel='webstore')
        reception.observe_sale(self.store,self.scope,row,candidate)
        with self.assertRaisesRegex(ValueError,'web_delivery_contract_requires_review'):
            self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        received=get_received(self.inbox.path,key);ctx=reception.context(self.store,received)
        self.assertEqual(ctx['review_state'],'web_paid_pending_fulfillment')
        self.assertEqual(read_inbox(self.inbox.path)['stored_orders'],1)
        self.assertIsNone(ctx['order_id']);self.assertFalse(ctx['can_classify_immediate'])
        with self.assertRaises(DomainError):reception.decide(self.store,received,'immediate',self.partner,'Prueba de bloqueo web',ctx['revision'])

    def test_partner_schedule_links_open_payment_then_closed_sale_to_same_order(self):
        row,candidate,key,web=self.opened();received=get_received(self.inbox.path,key)
        ctx=reception.context(self.store,received);body=self.sos_body()
        order=reception.schedule(self.store,received,body['order'],self.partner,body['reason'],body['request_id'],ctx['revision'])
        self.assertTrue(order['payment_confirmed']);self.assertEqual(order['channel'],'Web/Mercat')
        self.assertEqual(order['items'][0]['status'],'pendiente')
        candidate=dict(candidate,source_channel='webstore');reception.observe_sale(self.store,self.scope,row,candidate)
        updated=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        repeated=self.store.import_toteat_schedule(self.scope,row,candidate,is_test=True)
        self.assertEqual(order['id'],updated['id']);self.assertEqual(updated['id'],repeated['id'])
        self.assertEqual(updated['items'][0]['status'],'pendiente')

    def test_comment_date_requires_review_and_payment_alert_overrides_open_evidence(self):
        row,candidate,key,web=self.opened()
        web['comment']='Fecha: 30/10/2026\nHorario: 18:00'
        self.inbox.receive_detail({'ok':True,'data':web},self.scope,{web['document']['line'][0]['productCodeToteat']})
        received=get_received(self.inbox.path,key);ctx=reception.context(self.store,received)
        self.assertEqual(ctx['parsed']['pickup_at'],'2026-10-30T18:00');self.assertIsNone(ctx['order_id'])
        reception.record_alert(self.store,key,'refund_review',{'source':'fixture'})
        ctx=reception.context(self.store,received)
        self.assertIsNone(ctx['payment']);self.assertFalse(ctx['can_classify_immediate'])
