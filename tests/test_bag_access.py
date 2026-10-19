import concurrent.futures
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen,build_opener,HTTPCookieProcessor
from http.cookiejar import CookieJar
from uuid import uuid4

from app import handler_for
from erp.store import Store
from erp import cash_bags as bags
from erp.bag_access import BagAccess,COOKIE,REMEMBER_SECONDS,client_key,HASH_N,HASH_R,HASH_P
from erp.domain import DomainError
from http_test_support import authenticated_opener


PASSWORD='Clave sintetica de prueba 2026'
NEW_PASSWORD='Otra frase sintetica de prueba 2026'

class BagAccessTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.store=Store(self.root/'fake.sqlite3');bags.initialize(self.store)
        self.clock=[1800000000];self.access=BagAccess(self.store,lambda:self.clock[0])
        self.partner={'role':'partner','username':'Socio ficticio'}
        result=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=4),self.partner,'http://localhost:8878')
        self.bag=bags.batch_view(self.store,result['batch_id'])['bags'][0]
        self.person=self.access.manage(dict(action='save',name='Cajera Ficticia',email='cajera@example.invalid',enabled=True),'Socio ficticio')['id']
        self.set_password()

    def profile(self):return next(c for c in self.access.management()['cashiers'] if c['id']==self.person)
    def set_password(self,password=PASSWORD):
        return self.access.manage(dict(action='set_password',id=self.person,version=self.profile()['version'],confirmed=True,password=password,password_confirmation=password),'Socio ficticio')
    def login(self,client='test',remember=True,password=PASSWORD):
        token,seconds=self.access.login(dict(email=self.profile()['email'],password=password,remember=remember,device_name='Celular ficticio'),client)
        return COOKIE+'='+token,seconds
    def activation(self):
        return self.access.manage(dict(action='prepare_activation',id=self.person,version=self.profile()['version'],confirmed=True),'Socio ficticio')['activation_path'].split('token=')[1]
    def body(self):return dict(request_id=uuid4().hex,confirmed=True,business_date='2026-10-16',register_name='Caja ficticia',shift_name='Prueba',counted=13000,retained=3000)
    def submit(self,header,body=None):return self.access.submit(header,self.bag['id'],body or self.body(),'http://localhost:8878','test')
    def error(self,status,fn,*args):
        with self.assertRaises(DomainError) as err:fn(*args)
        self.assertEqual(err.exception.status,status)

    def test_pending_disabled_unknown_and_wrong_password_fail_generically(self):
        self.access.manage(dict(action='save',name='Pendiente ficticia',email='pending@example.invalid',enabled=True),'Socio ficticio')
        errors=[]
        for email,password in [('pending@example.invalid',PASSWORD),('unknown@example.invalid',PASSWORD),('cajera@example.invalid','wrong')]:
            with self.assertRaises(DomainError) as err:self.access.login(dict(email=email,password=password,remember=False),'test')
            self.assertEqual(err.exception.status,401);errors.append(str(err.exception))
        self.assertEqual(len(set(errors)),1)
        self.assertEqual(len(self.access.management()['cashiers']),2)

    def test_password_hash_salt_and_secret_redaction(self):
        with self.store.connect() as db:row=dict(db.execute('SELECT * FROM bag_access_credentials').fetchone())
        self.assertEqual((row['n'],row['r'],row['p']),(2**17,8,1));self.assertEqual(len(row['salt']),16);self.assertEqual(len(row['password_hash']),32)
        self.set_password()
        with self.store.connect() as db:
            second=dict(db.execute('SELECT * FROM bag_access_credentials').fetchone());events=[dict(r) for r in db.execute('SELECT * FROM bag_access_events')]
        self.assertNotEqual(row['salt'],second['salt']);self.assertNotEqual(row['password_hash'],second['password_hash'])
        public=json.dumps({'management':self.access.management(),'events':events})
        for secret in [PASSWORD,'password_hash',row['salt'].hex(),second['password_hash'].hex()]:self.assertNotIn(secret,public)

    def test_login_attempt_limits_persist_across_restart(self):
        for _ in range(5):self.error(401,self.access.login,dict(email='cajera@example.invalid',password='wrong',remember=False),'test')
        self.access=BagAccess(self.store,lambda:self.clock[0]);self.error(429,self.login)
        self.clock[0]+=901;self.assertTrue(self.access.view(self.login()[0],self.bag['id'])['signed_in'])

    def test_activation_expires_is_one_use_and_binds_registered_email(self):
        token=self.activation();self.assertFalse(self.profile()['has_password']);self.error(401,self.login)
        data=dict(token=token,email='cajera@example.invalid',password=NEW_PASSWORD,password_confirmation=NEW_PASSWORD)
        self.error(401,self.access.activate,dict(data,email='other@example.invalid'),'test')
        self.assertTrue(self.access.activate(data,'test')['password_set']);self.error(401,self.access.activate,data,'test')
        self.error(401,self.login,'test',True,PASSWORD);self.assertTrue(self.login(password=NEW_PASSWORD)[0])
        token=self.activation();self.clock[0]+=1200;self.error(401,self.access.activate,dict(data,token=token),'test')

    def test_activation_concurrency_and_replacement(self):
        old=self.activation();token=self.activation();data=dict(token=token,email='cajera@example.invalid',password=NEW_PASSWORD,password_confirmation=NEW_PASSWORD)
        self.error(401,self.access.activate,dict(data,token=old),'test')
        def activate(_):
            try:return self.access.activate(data,'test')
            except DomainError as err:return err.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(activate,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertIn(401,results)
        with self.store.connect() as db:
            rows=list(db.execute('SELECT token_hash FROM bag_access_enrollments'));self.assertNotIn(token,[r[0] for r in rows])

    def test_change_password_requires_current_secret_and_revokes_all_devices(self):
        first,_=self.login();second,_=self.login('second')
        data=dict(current_password='wrong',password=NEW_PASSWORD,password_confirmation=NEW_PASSWORD)
        self.error(401,self.access.change_password,first,data,'test')
        self.access.change_password(first,dict(data,current_password=PASSWORD),'test')
        for session in [first,second]:self.assertFalse(self.access.view(session,self.bag['id'])['signed_in'])
        self.error(401,self.login);self.assertTrue(self.login(password=NEW_PASSWORD)[0])

    def test_admin_recovery_without_email_invalidates_password_and_sessions(self):
        first,_=self.login();second,_=self.login('second');self.set_password(NEW_PASSWORD)
        self.assertFalse(self.access.view(first,self.bag['id'])['signed_in']);self.assertFalse(self.access.view(second,self.bag['id'])['signed_in'])
        self.error(401,self.login);self.assertTrue(self.login(password=NEW_PASSWORD)[0])
        with self.store.connect() as db:self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%challenge%'").fetchone())

    def test_admin_recovery_clears_account_lock_without_removing_ip_limit(self):
        for _ in range(5):self.error(401,self.access.login,dict(email='cajera@example.invalid',password='wrong',remember=False),'test')
        self.error(429,self.login);self.set_password(NEW_PASSWORD)
        self.assertTrue(self.login(password=NEW_PASSWORD)[0])
        from erp.bag_access import digest
        with self.store.connect() as db:self.assertGreater(db.execute('SELECT used FROM bag_access_limits WHERE scope=?',(digest('login-ip:test'),)).fetchone()[0],5)

    def test_password_policy_confirmation_and_recovery_permissions(self):
        for password,confirmation in [('short','short'),(PASSWORD,'different'),(' '*16,' '*16)]:
            self.error(400,self.access.manage,dict(action='set_password',id=self.person,version=self.profile()['version'],confirmed=True,password=password,password_confirmation=confirmation),'Socio ficticio')
        self.error(400,self.access.manage,dict(action='prepare_activation',id=self.person,version=self.profile()['version'],confirmed=False),'Socio ficticio')
        session,_=self.login()
        self.error(403,self.access.manage,dict(action='set_password',id=self.person,version=self.profile()['version'],confirmed=True,password=NEW_PASSWORD,password_confirmation=NEW_PASSWORD),'bag-access:'+self.person,session)

    def test_sessions_survive_restart_expire_and_revoke_per_device(self):
        first,seconds=self.login();self.assertEqual(seconds,REMEMBER_SECONDS)
        self.clock[0]+=61;second,_=self.login('test2')
        self.access=BagAccess(self.store,lambda:self.clock[0])
        self.assertTrue(self.access.view(first,self.bag['id'])['signed_in'])
        self.access.logout(first);self.assertFalse(self.access.view(first,self.bag['id'])['signed_in']);self.assertTrue(self.access.view(second,self.bag['id'])['signed_in'])
        device=self.access.management()['devices'][0]
        self.access.manage(dict(action='revoke_device',session_id=device['id']),'Socio ficticio')
        self.assertFalse(self.access.view(second,self.bag['id'])['signed_in'])
        self.clock[0]+=61;third,_=self.login(remember=False);self.clock[0]+=8*3600
        self.assertFalse(self.access.view(third,self.bag['id'])['signed_in'])
    def test_disable_invalidates_sessions_and_pending_activation(self):
        session,_=self.login();token=self.activation();person=self.profile()
        self.access.manage(dict(action='save',id=person['id'],version=person['version'],email=person['email'],name=person['name'],enabled=False),'Socio ficticio')
        self.assertFalse(self.access.view(session,self.bag['id'])['signed_in'])
        self.error(401,self.access.activate,dict(token=token,email=person['email'],password=NEW_PASSWORD,password_confirmation=NEW_PASSWORD),'test')
        self.error(401,self.submit,session)
    def test_get_scan_changes_no_data_and_hides_existing_amounts(self):
        session,_=self.login();self.submit(session)
        with self.store.connect() as db:before=list(db.iterdump())
        v=self.access.view(session,self.bag['id']);self.assertEqual(v['cashier']['name'],'Cajera Ficticia');self.assertFalse(v['bag']['available'])
        self.assertEqual(set(v['bag']),{'id','label','available'});self.assertNotIn('counted',json.dumps(v));self.assertNotIn('13000',json.dumps(v));self.assertEqual(self.access.view('',self.bag['id']),{'signed_in':False})
        with self.store.connect() as db:self.assertEqual(before,list(db.iterdump()))
    def test_name_from_registered_identity_idempotency_and_delivery_lock(self):
        session,_=self.login();body=self.body();self.error(400,self.submit,session,dict(body,responsible='Spoofed'))
        result=self.submit(session,body);self.assertEqual(result,{'received':True,'label':'BOL-000001'});self.assertEqual(self.submit(session,body),result)
        self.error(409,self.submit,session,self.body());self.error(409,self.submit,session,dict(body,counted=14000))
        stored=bags.bag_view(self.store,self.bag['id']);self.assertEqual(stored['responsible'],'Cajera Ficticia');self.assertEqual(stored['counted'],13000)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM cash_bag_events WHERE action='complete'").fetchone()[0],1)
        ordinary=dict(self.body(),version=2,responsible='Authenticated cashier')
        self.error(409,bags.execute,self.store,'complete',ordinary,{'role':'cashier','username':'old-cashier'},'http://localhost:8878',self.bag['id'])
        corrected=bags.execute(self.store,'complete',dict(ordinary,request_id=uuid4().hex,reason='Corrección ficticia de responsable'),self.partner,'http://localhost:8878',self.bag['id'])
        self.assertEqual(corrected['version'],3)
    def test_two_deliveries_race_first_wins_without_overwrite(self):
        session,_=self.login();bodies=[self.body(),dict(self.body(),counted=23000)]
        def call(body):
            try:return self.submit(session,body)
            except DomainError as e:return e.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(call,bodies))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertIn(409,results)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM cash_bag_events WHERE action='complete'").fetchone()[0],1)
    def test_revocation_rechecked_inside_delivery_transaction(self):
        session,_=self.login()
        def revoked_identity(db):
            db.execute('UPDATE bag_access_sessions SET revoked_at=?',(self.clock[0],))
            return self.access.session_row(db,session)
        self.error(401,bags.execute,self.store,'complete',dict(self.body(),version=1),{'role':'bag_cashier','username':self.person},'http://localhost:8878',self.bag['id'],revoked_identity)
        self.assertEqual(bags.bag_view(self.store,self.bag['id'])['status'],'blank')
    def test_name_email_changes_and_no_duplicates(self):
        session,_=self.login();person=self.access.management()['cashiers'][0]
        self.error(409,self.access.manage,dict(action='save',name='Another',email='CAJERA@example.invalid',enabled=True),'Socio ficticio')
        self.access.manage(dict(action='save',id=person['id'],version=person['version'],email='new@example.invalid',name='Renamed test',enabled=True),'Socio ficticio')
        self.assertFalse(self.access.view(session,self.bag['id'])['signed_in'])
        self.error(409,self.access.manage,dict(action='save',id=person['id'],version=1,email='new@example.invalid',name='Renamed',enabled=True),'Socio ficticio')

    def test_shared_account_distinguishes_authenticated_account_and_declared_person(self):
        self.access.manage(dict(action='save',id=self.person,version=self.profile()['version'],email='cajera@example.invalid',name='Caja compartida ficticia',enabled=True,shared=True),'Socio ficticio')
        self.set_password()
        session,_=self.login();self.error(400,self.submit,session,self.body())
        result=self.submit(session,dict(self.body(),declared_name='Persona declarada ficticia'));self.assertTrue(result['received'])
        stored=bags.bag_view(self.store,self.bag['id']);identity=stored['declaration_identity']
        self.assertEqual(stored['responsible'],'Persona declarada ficticia');self.assertTrue(identity['shared']);self.assertFalse(identity['person_verified'])
        self.assertEqual(identity['account_name'],'Caja compartida ficticia');self.assertEqual(identity['declared_person'],'Persona declarada ficticia')
        public=json.dumps(self.access.view(session,self.bag['id']));self.assertNotIn('Persona declarada ficticia',public)

    def test_individual_cannot_replace_registered_name(self):
        session,_=self.login();self.error(400,self.submit,session,dict(self.body(),declared_name='Different person'))

    def test_access_admin_role_is_scoped_and_revocation_checked(self):
        self.access.manage(dict(action='save',id=self.person,version=self.profile()['version'],email='cajera@example.invalid',name='Administrador ficticio',enabled=True,access_role='access_admin'),'Socio ficticio')
        session,_=self.login();actor=self.access.require_admin(session)
        self.access.manage(dict(action='save',email='second@example.invalid',name='Segunda ficticia',enabled=False),actor,session)
        self.assertEqual(len(self.access.management()['cashiers']),2)
        self.error(400,self.access.manage,dict(action='save',email='third@example.invalid',name='Bad role',enabled=True,access_role='partner'),actor,session)
        self.access.logout(session)
        self.error(403,self.access.manage,dict(action='save',email='third@example.invalid',name='After logout',enabled=True),actor,session)
        with self.store.connect() as db:
            event=db.execute("SELECT before_json,after_json FROM bag_access_events WHERE action='updated'").fetchone()
            self.assertEqual(json.loads(event[0])['access_role'],'cashier');self.assertEqual(json.loads(event[1])['access_role'],'access_admin')

class BagAccessHTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.store=Store(self.root/'fake.sqlite3')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.store,bag_access_enabled=True));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root);self.old_cashier=authenticated_opener(self.server,self.root,'cashier','old-cashier')
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        person=self.post('/api/bag-cashiers',dict(action='save',name='Cajera Ficticia',email='test@example.invalid',enabled=True),self.partner)
        self.post('/api/bag-cashiers',dict(action='set_password',id=person['id'],version=1,confirmed=True,password=PASSWORD,password_confirmation=PASSWORD),self.partner)
        batch=self.post('/api/cash-bags/batches',dict(request_id=uuid4().hex,confirmed=True,quantity=1),self.partner)
        with self.partner.open(self.url+'/api/cash-bags/batches/'+batch['batch_id']) as r:self.bag=json.load(r)['bags'][0]
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,path,data,opener=None,headers=None):
        req=Request(self.url+path,data=json.dumps(data).encode(),headers={'Origin':self.url,'Content-Type':'application/json','X-ERP-Local':'1',**(headers or {})})
        with (opener or self.client).open(req) as r:return json.load(r)
    def login(self):return self.post('/api/bag-access/login',dict(email='test@example.invalid',password=PASSWORD,remember=True))
    def test_qr_and_all_existing_erp_apis_remain_isolated(self):
        with urlopen(self.url+self.bag['path']) as r:
            html=r.read().decode();self.assertIn('/bag-entry.js',html);self.assertNotIn('/app.js',html);self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.login()
        for path in ['/api/cash-bags','/api/cash-bags/'+self.bag['id'],'/api/document-center','/api/board','/api/partner/session','/api/bag-cashiers']:
            if path=='/api/partner/session':
                with self.client.open(self.url+path) as r:self.assertNotIn('Cajera Ficticia',r.read().decode())
                continue
            with self.assertRaises(HTTPError) as error:self.client.open(self.url+path)
            self.assertIn(error.exception.code,[401,403]);error.exception.close()
        with self.client.open(self.url+'/api/bag-access/session?bag='+self.bag['id']) as r:self.assertTrue(json.load(r)['signed_in'])
        with self.assertRaises(HTTPError) as error:self.old_cashier.open(self.url+'/api/bag-cashiers')
        self.assertEqual(error.exception.code,403);error.exception.close()
    def test_write_origin_and_forged_identity_rejected(self):
        data=dict(email='test@example.invalid',password=PASSWORD,remember=True)
        for headers in [{'Origin':'https://evil.invalid'},{'Origin':''},{'Sec-Fetch-Site':'cross-site'},{'X-ERP-Local':'0'},{'Content-Type':'text/plain'}]:
            with self.assertRaises(HTTPError) as err:self.post('/api/bag-access/login',data,headers=headers)
            self.assertIn(err.exception.code,[403,415]);err.exception.close()
    def test_feature_disabled_by_default_and_no_new_tables(self):
        other=Store(self.root/'disabled.sqlite3');handler_for(other)
        with other.connect() as db:self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='bag_cashiers'").fetchone())

    def test_wsgi_explicit_activation_flag_and_default_isolation(self):
        from erp.wsgi import create_app
        from io import BytesIO
        for flag in [False,True]:
            app=create_app(self.root/('wsgi-'+str(flag)),'https://erp.example.invalid',bag_access_enabled=flag)
            status=[]
            body=b''.join(app({'REQUEST_METHOD':'GET','PATH_INFO':'/api/bag-access/session','HTTP_HOST':'erp.example.invalid','SERVER_PORT':'8010','REMOTE_ADDR':'127.0.0.1','wsgi.input':BytesIO()},lambda s,h:status.append(s)))
            self.assertEqual(status[0].split()[0],'200' if flag else '401')
            if flag:self.assertEqual(json.loads(body),{'signed_in':False})

    def test_email_admin_can_manage_only_module_people_and_cashier_cannot(self):
        with self.partner.open(self.url+'/api/bag-cashiers') as r:person=json.load(r)['cashiers'][0]
        self.post('/api/bag-cashiers',dict(action='save',id=person['id'],version=person['version'],email=person['email'],name=person['name'],enabled=True,access_role='access_admin'),self.partner)
        self.login()
        with self.client.open(self.url+'/bag-cashiers') as r:self.assertIn('Personas y accesos',r.read().decode())
        self.post('/api/bag-cashiers',dict(action='save',name='Nueva ficticia',email='new@example.invalid',enabled=False))
        for path in ['/api/cash-bags','/api/board','/api/document-center']:
            with self.assertRaises(HTTPError) as err:self.client.open(self.url+path)
            self.assertEqual(err.exception.code,401);err.exception.close()
        for role in ['partner','production','cashier,partner']:
            with self.assertRaises(HTTPError) as err:self.post('/api/bag-cashiers',dict(action='save',name='Bad role',email='invalid@example.invalid',enabled=True,access_role=role))
            self.assertEqual(err.exception.code,400);err.exception.close()

    def test_client_ip_uses_only_proxy_added_address(self):
        from types import SimpleNamespace
        from erp.bag_access import digest
        fake=SimpleNamespace(client_address=('127.0.0.1',1),headers={'X-Forwarded-For':'198.51.100.1, 203.0.113.2'})
        self.assertEqual(client_key(fake),digest('203.0.113.2'))
        fake.client_address=('192.0.2.3',1);self.assertEqual(client_key(fake),digest('192.0.2.3'))

    def test_no_registration_otp_or_password_reset_by_cashier(self):
        for path in ['/api/bag-access/register','/api/bag-access/request-code','/api/bag-access/verify-code']:
            with self.assertRaises(HTTPError) as err:self.post(path,{})
            self.assertEqual(err.exception.code,404);err.exception.close()
        self.login()
        for action in ['set_password','prepare_activation','save']:
            with self.assertRaises(HTTPError) as err:self.post('/api/bag-cashiers',{'action':action})
            self.assertEqual(err.exception.code,403);err.exception.close()

    def test_secure_cookie_and_no_plaintext_management_or_activation_page(self):
        from erp.bag_access import cookie
        value=cookie(COOKIE,'synthetic',REMEMBER_SECONDS,True)
        for flag in ['HttpOnly','Secure','SameSite=Strict','Max-Age=1209600']:self.assertIn(flag,value)
        with self.partner.open(self.url+'/api/bag-cashiers') as r:
            body=r.read().decode();self.assertNotIn(PASSWORD,body);self.assertNotIn('password_hash',body);self.assertNotIn('salt',body)
        with urlopen(self.url+'/bag-activate') as r:
            html=r.read().decode();self.assertIn('type="password"',html);self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')

if __name__=='__main__':unittest.main()
