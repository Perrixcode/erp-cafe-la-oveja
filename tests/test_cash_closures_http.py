from http.server import ThreadingHTTPServer
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import Request,urlopen,build_opener,HTTPCookieProcessor
from urllib.error import HTTPError
from uuid import uuid4
import base64,json,unittest
from app import handler_for
from erp.store import Store
from erp.bag_access import BagAccess
from erp import cash_closures as cl,cash_bags as bags
from http_test_support import authenticated_opener

class ClosureHTTPTests(unittest.TestCase):
 def setUp(self):
  t=TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.store=Store(self.root/'test.sqlite3');self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.store,bag_access_enabled=True));self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
  self.url='http://127.0.0.1:'+str(self.server.server_address[1]);self.partner=authenticated_opener(self.server,self.root);self.cashier=authenticated_opener(self.server,self.root,'cashier','cajera-http');self.prod=authenticated_opener(self.server,self.root,'production','cocina-http')
  self.access=BagAccess(self.store);self.person=self.access.manage(dict(action='save',name='Móvil QA',email='movil@example.invalid',enabled=True),'QA')['id'];p=self.access.management()['cashiers'][0]
  self.access.manage(dict(action='set_password',id=p['id'],version=p['version'],confirmed=True,password='Clave movil sintética QA 2026',password_confirmation='Clave movil sintética QA 2026'),'QA')
  self.mobile=build_opener(HTTPCookieProcessor(CookieJar()));self.call('/api/bag-access/login',self.mobile,dict(email='movil@example.invalid',password='Clave movil sintética QA 2026',remember=False,device_name='Prueba'))
  self.reg=self.command('/api/cash-closures/register',self.partner,name='CAJA HTTP')['register_id']
 def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
 def call(self,path,opener=None,body=None,headers=None):
  req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or ({'Content-Type':'application/json','X-ERP-Local':'1','Origin':self.url} if body is not None else {}))
  with (opener.open if opener else urlopen)(req,timeout=4) as r:
   content=r.read();return json.loads(content) if 'json' in r.headers['Content-Type'] else content
 def command(self,path,opener=None,**body):
  deliver=body.pop('delivered',False) if path.endswith('/submit') else False
  if path.endswith('/submit'):body['review_token']=self.call(path.removesuffix('/submit')+'/submission-review',opener)['review_token']
  result=self.call(path,opener,dict(request_id=uuid4().hex,confirmed=True,**body))
  return self.command(path.removesuffix('/submit')+'/deliver',opener,version=result['version']) if deliver else result
 def create(self,who=None,**body):return self.command('/api/cash-closures/create',who or self.mobile,identity=dict(business_date='2026-10-16',register_id=self.reg,shift_name=uuid4().hex),**body)
 def error(self,code,fn,*a,**kw):
  with self.assertRaises(HTTPError) as e:fn(*a,**kw)
  self.assertEqual(e.exception.code,code);e.exception.close()
 def test_erp_mobile_roles_readonly_and_revocation(self):
  self.error(401,self.call,'/api/cash-closures');self.error(403,self.call,'/api/cash-closures',self.prod)
  c=self.create();self.assertEqual(c['responsible']['key'],'bag:'+self.person);self.error(403,self.call,'/api/cash-closures/'+c['id'],self.cashier)
  with self.store.connect() as db:before=list(db.iterdump())
  self.call('/api/cash-closures/'+c['id'],self.mobile);self.call('/api/cash-closures/'+c['id']+'/options',self.mobile)
  with self.store.connect() as db:self.assertEqual(list(db.iterdump()),before)
  self.call('/api/bag-access/logout',self.mobile,{});self.error(401,self.call,'/api/cash-closures/'+c['id'],self.mobile)
 def test_csrf_idempotence_and_incomplete_submit(self):
  body=dict(request_id=uuid4().hex,confirmed=True,identity=dict(business_date='2026-10-16',register_id=self.reg,shift_name='Mañana'))
  for headers,code in [({'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'},403),({'Content-Type':'application/json'},403),({'Content-Type':'text/plain','X-ERP-Local':'1'},415)]:self.error(code,self.call,'/api/cash-closures/create',self.mobile,body,headers)
  c=self.call('/api/cash-closures/create',self.mobile,body);self.assertEqual(c,self.call('/api/cash-closures/create',self.mobile,body))
  self.error(400,self.command,'/api/cash-closures/'+c['id']+'/submit',self.mobile,version=c['version']);self.error(403,self.command,'/api/cash-closures/'+c['id']+'/receive',self.mobile,version=c['version'])
 def test_attachment_limit_content_and_ownership_pdf(self):
  c=self.create();body=dict(version=c['version'],file_name='evidencia.pdf',file_type='application/pdf',file_data=base64.b64encode(b'%PDF-'+b'0'*70000).decode());c=self.command('/api/cash-closures/'+c['id']+'/attach',self.mobile,**body)
  file='/api/cash-closures/'+c['id']+'/attachments/'+c['attachments'][0]['id'];self.assertTrue(self.call(file,self.mobile).startswith(b'%PDF-'));self.error(403,self.call,file,self.cashier)
  for raw in [b'notpdf',b'%PDF-'+b'0'*(2*1024*1024)]:self.error(400,self.command,'/api/cash-closures/'+c['id']+'/attach',self.mobile,**dict(body,version=c['version'],file_data=base64.b64encode(raw).decode()))
  for path in ['preview.pdf','download.pdf']:self.assertTrue(self.call('/api/cash-closures/'+c['id']+'/'+path,self.mobile).startswith(b'%PDF-'))
 def test_qr_reservation_owner_reopens_and_no_legacy_bypass(self):
  b=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),{'role':'partner','username':'QA'},self.url);b=bags.batch_view(self.store,b['batch_id'])['bags'][0];c=self.create(bag_id=b['id'])
  s=self.call('/api/bag-access/session?bag='+b['id'],self.mobile);self.assertFalse(s['bag']['available']);self.assertEqual(s['digital_closure_id'],c['id'])
  self.error(409,self.command,'/api/bag-access/'+b['id']+'/deliver',self.mobile,business_date='2026-10-16',register_name='QA',shift_name='QA',counted=1000,retained=500)
  self.assertEqual(cl.view(self.store,c['id'],{'realm':'bag','role':'bag_cashier','username':self.person})['version'],1)
 def test_assets_without_auth(self):
  for path in ['/cash-closures.js','/cash-closures.css','/?module=cash&bag=00000000-0000-4000-8000-000000000001']:self.assertTrue(self.call(path))
