import unittest
from test_cash_closures_http import ClosureHTTPTests
from erp import cash_toteat as ct

class SourceHTTPTests(unittest.TestCase):
 setUp=ClosureHTTPTests.setUp;stop=ClosureHTTPTests.stop;call=ClosureHTTPTests.call;command=ClosureHTTPTests.command;create=ClosureHTTPTests.create;error=ClosureHTTPTests.error
 def path(self,cid=None):return '/api/cash-closures/toteat?day=2026-10-16&register_id='+self.reg+('&closure_id='+cid if cid else '')
 def test_sources_require_existing_authorized_role_and_own_closure(self):
  c=self.create();self.error(401,self.call,self.path());self.error(403,self.call,self.path(),self.prod);self.error(404,self.call,self.path(),self.mobile)
  self.assertEqual(self.call(self.path(c['id']),self.mobile)['state'],'pending');self.error(403,self.call,self.path(c['id']),self.cashier)
  self.assertEqual(self.call(self.path(),self.partner)['state'],'pending')
 def test_refresh_requires_same_origin_and_only_queues_read(self):
  c=self.create();path='/api/cash-closures/toteat/refresh';body=dict(day='2026-10-16',closure_id=c['id'])
  self.error(403,self.call,path,self.mobile,body,{'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'})
  self.error(403,self.call,path,self.mobile,body,{'Content-Type':'application/json'})
  first=self.call(path,self.mobile,body);second=self.call(path,self.mobile,body);self.assertEqual(first,second)
  self.error(403,self.call,path,self.cashier,body);self.error(400,self.call,path,self.partner,dict(day='2026-10-16',confirmed=True))
 def test_new_static_asset_and_old_routes_remain_available(self):self.assertIn(b'OvejaToteat',self.call('/cash-toteat.js'))

 def test_import_command_preserves_role_csrf_version_and_idempotence(self):
  from test_cash_operational_day import collection,SCOPE
  from erp import cash_control_sources as src
  from uuid import uuid4
  self.reg=self.command('/api/cash-closures/register',self.partner,name='Caja contractual',provider_register_id='1')['register_id']
  src.record(self.store,'2026-10-16','collection',collection(),SCOPE);c=self.create();path='/api/cash-closures/'+c['id']
  view=self.call(path+'/options',self.mobile)['toteat'];c=self.command(path+'/save',self.mobile,version=c['version'],data=c['data'],identity=dict(business_date=c['business_date'],register_id=self.reg,shift_name=c['shift_name'],external_session_key=view['selected']['session_key']))
  view=self.call(path+'/options',self.mobile)['toteat'];body=dict(request_id=uuid4().hex,confirmed=True,version=c['version'],source_token=view['import_token'])
  self.error(403,self.call,path+'/import-source',self.cashier,body)
  self.error(403,self.call,path+'/import-source',self.mobile,body,{'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'})
  first=self.call(path+'/import-source',self.mobile,body);self.assertEqual(self.call(path+'/import-source',self.mobile,body),first)
  self.assertEqual(len(first['data']['imports']),3);self.assertIsNone(first['calculation']['expected'])
  self.error(409,self.command,path+'/resolve-import',self.mobile,version=c['version'],import_id=first['data']['imports'][0]['id'],resolution='discard',reason='Stale version')

del ClosureHTTPTests
