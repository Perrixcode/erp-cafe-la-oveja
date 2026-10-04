"""Permisos comprobados por HTTP, con cuentas y pedidos exclusivamente ficticios."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import make_server
from erp.partner_auth import PartnerAuth
from erp.store import Store
from http_test_support import authenticated_opener
from test_erp import sample


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'orders.sqlite3';self.store=Store(self.path)
        self.order=self.store.create(sample(),'Fixture ficticio')
        self.server=make_server(self.path,0,seed=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.clients={role:authenticated_opener(self.server,self.path.parent,role,role+'-qa') for role in ['partner','production','cashier']}

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,role=None,body=None,method=None,headers=None):
        request=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json','X-ERP-Local':'1',**(headers or {})},method=method)
        response=(self.clients[role].open if role else urlopen)(request,timeout=3)
        with response:return json.load(response)

    def denied(self,code,*args,**kwargs):
        with self.assertRaises(HTTPError) as error:self.request(*args,**kwargs)
        with error.exception as response:self.assertEqual(response.status,code)

    def test_every_private_read_requires_session_even_with_forged_role(self):
        for path in ['/api/board','/api/toteat','/api/notifications','/api/stock',f'/api/orders/{self.order["id"]}',f'/api/orders/{self.order["id"]}/history',f'/api/orders/{self.order["id"]}/receipt?download=1']:
            self.denied(401,path,headers={'Cookie':'role=partner; oveja_partner=fake','X-ERP-Role':'partner','Authorization':'Bearer fake'})
        self.assertEqual(self.request('/api/health'),{'ok':True,'service':'oveja-erp','catalog_mode':'local','authentication_required':True})
        self.assertIsNone(self.request('/api/partner/session')['user'])

    def transition(self,role,status,**patch):
        order=self.store.get(self.order['id'])
        return self.request(f'/api/orders/{order["id"]}/items/{order["items"][0]["id"]}/status',role,
                            {'status':status,'actor':'SOCIO FALSIFICADO','role':'partner','reason':'Prueba ficticia de permisos','version':order['version'],**patch})

    def test_production_can_request_but_cannot_confirm_cancel_reverse_or_edit_customer(self):
        order=self.transition('production','marcado_solicitado')
        self.assertEqual(order['items'][0]['status'],'marcado_solicitado')
        self.assertEqual(self.store.history(order['id'])[0]['actor'],'production-qa')
        for role,status,patch in [('production','marcado',{}),('production','cancelado',{}),('production','pendiente',{'correction':True}),('cashier','marcado',{}),('cashier','cancelado',{})]:
            with self.assertRaises(HTTPError) as error:self.transition(role,status,**patch)
            with error.exception as response:self.assertEqual(response.status,403)
        for role in ['production','cashier']:
            for path,method in [(f'/api/orders/{order["id"]}/customer','PUT'),('/api/sos/orders','POST'),(f'/api/orders/{order["id"]}/schedule-without-payment','POST'),('/api/stock','PUT'),('/api/simulations','POST')]:
                self.denied(403,path,role,{'role':'partner','actor':'Socio falso'},method)
        self.assertEqual(self.store.get(order['id']),order)
        self.transition('partner','marcado')
        delivered=self.transition('cashier','entregado')
        self.assertEqual(delivered['items'][0]['status'],'entregado')
        self.assertEqual(self.store.history(order['id'])[0]['actor'],'cashier-qa')

    def test_recipe_permissions_and_document_access(self):
        product=self.store.catalog()[0]
        body={'sku':product['sku'],'bases':None,'version':product['version'],'reason':'Receta ficticia por confirmar','actor':'FALSO'}
        self.denied(403,'/api/recipes','cashier',body,'PUT')
        self.request('/api/recipes','production',body,'PUT')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT actor FROM recipe_history ORDER BY id DESC LIMIT 1').fetchone()[0],'production-qa')
        self.denied(403,f'/api/orders/{self.order["id"]}/receipt','production')
        for role in ['partner','cashier']:
            self.denied(404,f'/api/orders/{self.order["id"]}/receipt',role)
            self.assertIn('orders',self.request('/api/board',role))

    def test_logout_and_disabled_account_revoke_access_immediately(self):
        self.request('/api/partner/logout','cashier',{})
        self.denied(401,'/api/board','cashier')
        with closing(sqlite3.connect(self.path.parent/'partner-auth.sqlite3')) as db,db:
            db.execute("UPDATE partners SET enabled=0 WHERE username='production-qa'")
        self.denied(401,'/api/board','production')
        self.assertIsNone(self.request('/api/partner/session','production')['user'])

    def test_no_accounts_does_not_open_the_erp(self):
        with closing(sqlite3.connect(self.path.parent/'partner-auth.sqlite3')) as db,db:db.execute('UPDATE partners SET enabled=0')
        self.assertFalse(self.request('/api/partner/session')['configured'])
        self.denied(401,'/api/board')

    def test_previous_partner_accounts_migrate_without_password_reset(self):
        path=self.path.parent/'old-auth.sqlite3';salt=b'0'*16
        password='Clave-ficticia-heredada'
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE partners(username TEXT PRIMARY KEY,name TEXT NOT NULL,salt BLOB NOT NULL,digest BLOB NOT NULL)')
            db.execute('INSERT INTO partners VALUES(?,?,?,?)',('legacy','Socio ficticio',salt,PartnerAuth.derive(password,salt)))
        auth=PartnerAuth(path);token=auth.login('legacy',password)
        self.assertEqual(auth.session(auth.cookie(token))['role'],'partner')
        self.assertIn('sos',auth.require(auth.cookie(token))['permissions'])
