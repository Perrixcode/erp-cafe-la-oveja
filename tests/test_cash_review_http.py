import json,unittest
from uuid import uuid4
from urllib.error import HTTPError
import test_cash_closures_http as base
from erp import cash_review as cr,cash_closures as cl,cash_bags as bags
from test_cash_closures import PARTNER,filled

class ReviewHTTPTests(unittest.TestCase):
 setUp=base.ClosureHTTPTests.setUp;stop=base.ClosureHTTPTests.stop;call=base.ClosureHTTPTests.call;command=base.ClosureHTTPTests.command;create=base.ClosureHTTPTests.create;error=base.ClosureHTTPTests.error
 def test_permissions_csrf_readonly_and_revocation(self):
  route=cr.PREFIX
  self.error(401,self.call,route);self.error(403,self.call,route,self.cashier);self.error(403,self.call,route,self.mobile);self.error(403,self.call,route,self.prod)
  self.call(route,self.partner)
  body=dict(request_id=uuid4().hex,name='Lote HTTP QA')
  self.error(403,self.call,route+'/batch-create',self.partner,body,{'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'})
  self.error(403,self.call,route+'/batch-create',self.partner,body,{'Content-Type':'application/json'})
  one=self.call(route+'/batch-create',self.partner,body);self.assertEqual(one,self.call(route+'/batch-create',self.partner,body))
  self.error(409,self.call,route+'/batch-create',self.partner,dict(body,name='Cambió'))
  self.call('/api/partner/logout',self.partner,{})
  self.error(401,self.call,route,self.partner)
 def test_batch_routes_and_summary_pdf(self):
  b=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),PARTNER,self.url);b=bags.batch_view(self.store,b['batch_id'])['bags'][0]
  c=self.create(who=self.partner,data=filled(),bag_id=b['id']);c=self.command(cl.PREFIX+'/'+c['id']+'/submit',self.partner,version=c['version'],delivered=True)
  candidate=self.call(cr.PREFIX+'/lookup?value='+b['id'],self.partner);batch=self.command(cr.PREFIX+'/batch-create',self.partner,name='HTTP QA')
  batch=self.command(cr.PREFIX+'/batch-save',self.partner,id=batch['id'],version=batch['version'],items=[{k:candidate[k] for k in ('bag_id','bag_version','closure_version')}|dict(amount=10000,note='Conteo QA')])
  self.error(403,self.call,cr.PREFIX+'/batch/'+batch['id'],self.mobile)
  batch=self.command(cr.PREFIX+'/batch-confirm',self.partner,id=batch['id'],version=batch['version']);self.assertEqual(batch['state'],'confirmed')
  self.assertEqual(self.call(cr.PREFIX+'/batch/'+batch['id'],self.partner)['state'],'confirmed')
  for suffix in ('preview.pdf','download.pdf'):self.assertTrue(self.call(cr.PREFIX+'/'+suffix+'?start=2026-10-16&end=2026-10-17',self.partner).startswith(b'%PDF-'))
  self.assertTrue(self.call('/cash-review.js'))
 def test_invalid_filters_and_methods_missing_zero(self):
  self.error(400,self.call,cr.PREFIX+'?start=bad',self.partner)
  self.error(400,self.call,cr.PREFIX+'?state=other',self.partner)
  c=self.create(who=self.partner);d=self.call(cr.PREFIX+'/closure/'+c['id'],self.partner)
  self.assertIsNone(d['payment_comparison']['totals']['toteat_total']);self.assertIsNone(d['payment_comparison']['totals']['declared_total'])
 def test_pdf_snapshot_matches_preview_download_and_rejects_changed_report(self):
  overview=self.call(cr.PREFIX,self.partner);query='?snapshot='+overview['snapshot']
  preview=self.call(cr.PREFIX+'/preview.pdf'+query,self.partner);download=self.call(cr.PREFIX+'/download.pdf'+query,self.partner)
  self.assertEqual(preview,download)
  self.create(who=self.partner)
  self.error(409,self.call,cr.PREFIX+'/preview.pdf'+query,self.partner)
