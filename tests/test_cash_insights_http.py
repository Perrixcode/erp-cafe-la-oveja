import unittest
import test_cash_closures_http as base

class InsightHTTPTests(unittest.TestCase):
 setUp=base.ClosureHTTPTests.setUp;stop=base.ClosureHTTPTests.stop;call=base.ClosureHTTPTests.call;command=base.ClosureHTTPTests.command;create=base.ClosureHTTPTests.create;error=base.ClosureHTTPTests.error
 def test_partner_only_readonly_context_and_static(self):
  self.error(401,self.call,'/api/cash-insights')
  for who in (self.cashier,self.prod,self.mobile):self.error(403 if who!=self.mobile else 401,self.call,'/api/cash-insights',who)
  c=self.create()
  with self.store.connect() as db:before=list(db.iterdump())
  self.assertEqual(len(self.call('/api/cash-insights?start=2026-10-16&end=2026-10-17',self.partner)['rows']),1)
  self.assertEqual(self.call('/api/cash-insights/context?closure_id='+c['id'],self.partner)['closure']['id'],c['id'])
  with self.store.connect() as db:self.assertEqual(list(db.iterdump()),before)
  self.assertIn(b'window.OvejaCashInsights',self.call('/cash-insights.js'))
 def test_csrf_and_invalid_body(self):
  self.error(403,self.call,'/api/cash-insights/terminal',self.partner,{}, {'Content-Type':'application/json','X-ERP-Local':'1','Origin':'https://evil.invalid'})
  self.error(403,self.call,'/api/cash-insights/terminal',self.partner,{}, {'Content-Type':'application/json'})
  self.error(400,self.call,'/api/cash-insights/terminal',self.partner,{})
  self.error(404,self.call,'/api/cash-insights/context?closure_id=absent',self.partner)
