from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from app import make_server
from erp.domain import DomainError
from erp.partner_auth import PartnerAuth
from erp.store import Store
from erp.catalog_import import load_catalog
from erp.toteat_comments_probe import inspect_sales


class PartnerCustomerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'orders.sqlite3';self.store=Store(self.path)
        fixture=load_catalog(Path(__file__).resolve().parents[1]/'fixtures/catalog_import_demo.json');fixture['source']='toteat-manual'
        self.store.import_catalog(fixture,'Fixture ficticio')
        with self.store.connect() as db:db.execute("INSERT INTO metadata VALUES('operating_mode','toteat-local')")
        self.auth=PartnerAuth(self.root/'partner-auth.sqlite3')
        self.auth.create('socio-qa','Socio ficticio','Clave-ficticia-QA-2026')
        p=self.store.catalog()[0]
        self.row={'orderId':9007199254740099,'paymentId':9007199254740011,'dateClosed':'2026-10-04T18:00:00','dateOpen':'2026-10-04T17:00:00','comment':'Nombre: Cliente ficticio\nTeléfono: 000000000\nFecha: 30/10/2026\nHorario: 18:00\nPlataforma: LOCAL','total':100,'payed':100,'discounts':0,'difference':0,'paymentForms':[],'products':[{'id':p['source_product']['id'],'quantity':2,'lineId':1,'isExtra':False}]}
        _,candidates=inspect_sales({'ok':True,'data':[self.row]},[p['source_product'] for p in self.store.catalog()],str(self.row['orderId']))
        self.candidate=candidates[0];self.scope={'restaurant_id':'DEMO','local_id':'1'}
        self.order=self.store.import_toteat_schedule(self.scope,self.row,self.candidate)
        self.server=make_server(self.path,port=0,seed=False);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.cookie=''

    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def api(self,path,body=None,method=None,headers=None):
        extra={'Content-Type':'application/json','X-ERP-Local':'1','Cookie':self.cookie};extra.update(headers or {})
        request=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,method=method or ('POST' if body is not None else 'GET'),headers=extra)
        with urlopen(request,timeout=3) as response:
            cookie=response.headers.get('Set-Cookie')
            if cookie:self.cookie=cookie.split(';',1)[0]
            return json.load(response),cookie

    def login(self):return self.api('/api/partner/login',{'username':'socio-qa','password':'Clave-ficticia-QA-2026'})

    def body(self):
        return {'customer':{'customer':'Cliente ficticio corregido','customer_phone':'000000001','pickup_at':'2026-11-02T15:30','fulfillment':'despacho','delivery_address':'Calle Ficticia 123, Comuna Demo'},'reason':'Cliente pide delivery y nueva fecha','version':self.store.get(self.order['id'])['version'],'actor':'Actor falso','role':'socio'}

    def change(self,body=None):return self.api(f"/api/orders/{self.order['id']}/customer",body or self.body(),'PUT')[0]

    def test_only_authenticated_partner_can_edit_and_logout_revokes(self):
        before=self.store.get(self.order['id'])
        for headers in ({},{'Cookie':'role=socio'},{'X-ERP-Role':'socio'}):
            with self.assertRaises(HTTPError) as denied:self.api(f"/api/orders/{self.order['id']}/customer",self.body(),'PUT',headers)
            denied.exception.close();self.assertEqual(denied.exception.code,403)
        self.assertEqual(before,self.store.get(self.order['id']))
        _,cookie=self.login();self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        changed=self.change();self.assertEqual(changed['fulfillment'],'despacho')
        self.api('/api/partner/logout',{})
        with self.assertRaises(HTTPError) as denied:self.change()
        denied.exception.close();self.assertEqual(denied.exception.code,403)

    def test_edit_moves_month_preserves_original_and_survives_source_refresh_and_restart(self):
        self.login()
        order=self.store.transition(self.order['id'],self.order['items'][0]['id'],'marcado_solicitado','QA','Solicitud ficticia',self.order['version'])
        body=self.body();changed=self.change(body)
        self.assertEqual(changed['comments'],self.row['comment']);self.assertEqual(changed['items'],order['items'])
        self.assertEqual(changed['receipt'],order['receipt']);self.assertEqual(changed['payment_confirmed'],order['payment_confirmed'])
        self.assertEqual(self.store.list('2026-10-01','2026-10-31'),[])
        self.assertEqual(self.store.list('2026-11-01','2026-11-30')[0]['id'],order['id'])
        self.assertEqual(self.store.notifications()[0]['pickup_at'],'2026-11-02T15:30')
        refreshed=self.store.import_toteat_schedule(self.scope,self.row,self.candidate)
        self.assertEqual(refreshed['customer'],changed['customer']);self.assertEqual(refreshed['pickup_at'],changed['pickup_at'])
        self.assertEqual(Store(self.path).get(order['id'])['delivery_address'],body['customer']['delivery_address'])
        event=self.store.history(order['id'])[0]
        self.assertEqual(event['actor'],'Socio ficticio');self.assertEqual(event['after']['socio_autenticado'],'socio-qa')
        self.assertEqual(event['before']['pickup_at'],'2026-10-30T18:00')
        with self.assertRaises(HTTPError) as conflict:self.change(body)
        conflict.exception.close();self.assertEqual(conflict.exception.code,409)
        body=self.body();body['customer']['fulfillment']='retiro';body['customer']['delivery_address']=''
        self.assertEqual(self.change(body)['fulfillment'],'retiro')

    def test_invalid_address_phone_or_time_rolls_back_and_finished_order_rejected(self):
        self.login();before=self.store.get(self.order['id'])
        for patch in [{'delivery_address':''},{'customer_phone':'hola'},{'pickup_at':'2026-09-06T00:30'}]:
            body=self.body();body['customer'].update(patch)
            with self.assertRaises(HTTPError) as denied:self.change(body)
            denied.exception.close();self.assertEqual(denied.exception.code,400)
            self.assertEqual(self.store.get(self.order['id']),before)
        current=before
        for status in ['marcado_solicitado','marcado','entregado']:
            current=self.store.transition(current['id'],current['items'][0]['id'],status,'QA','Prueba',current['version'])
        with self.assertRaises(HTTPError) as denied:self.change()
        denied.exception.close();self.assertEqual(denied.exception.code,400)

    def test_password_is_hashed_sessions_expire_and_login_is_rate_limited(self):
        with closing(sqlite3.connect(self.auth.path)) as db:
            row=db.execute('SELECT salt,digest FROM partners').fetchone()
        self.assertEqual(len(row[0]),16);self.assertNotEqual(row[1],b'Clave-ficticia-QA-2026')
        token=self.auth.login('socio-qa','Clave-ficticia-QA-2026');header=self.auth.cookie(token)
        self.assertEqual(self.auth.require(header)['username'],'socio-qa')
        for session in self.auth.sessions.values():session['expires']=0
        self.assertIsNone(self.auth.session(header))
        for _ in range(5):
            with self.assertRaises(DomainError):self.auth.login('socio-qa','incorrecta')
        with self.assertRaises(DomainError) as denied:self.auth.login('socio-qa','Clave-ficticia-QA-2026')
        self.assertEqual(denied.exception.status,429)
        with self.assertRaises(DomainError):self.auth.create('socio-qa','Socio ficticio','Otra-clave-ficticia')

    def test_toteat_without_scheduling_evidence_cannot_be_marked(self):
        self.login()
        with self.store.connect() as db:
            db.execute('DELETE FROM toteat_scheduling WHERE order_id=?',(self.order['id'],))
        with self.assertRaises(HTTPError) as denied:
            self.api(f"/api/orders/{self.order['id']}/items/{self.order['items'][0]['id']}/status",
                     {'status':'marcado_solicitado','actor':'QA','reason':'Prueba','version':self.order['version']})
        denied.exception.close();self.assertEqual(denied.exception.code,403)
        self.assertEqual(self.store.get(self.order['id'])['items'][0]['status'],'pendiente')

    def test_authenticated_partner_cannot_create_invalid_sos_or_turn_paid_order_unpaid(self):
        self.login();before=self.store.get(self.order['id']);history=self.store.history(self.order['id'])
        for path,status in [('/api/sos/orders',400),(f"/api/orders/{self.order['id']}/schedule-without-payment",400)]:
            with self.assertRaises(HTTPError) as denied:self.api(path,{'role':'socio','actor':'Socio ficticio'})
            with denied.exception as response:
                self.assertEqual(response.code,status)
        self.assertEqual(self.store.get(self.order['id']),before);self.assertEqual(self.store.history(self.order['id']),history)

if __name__=='__main__':unittest.main()
