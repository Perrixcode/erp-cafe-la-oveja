import unittest
from uuid import uuid4
from urllib.error import HTTPError
import json
import test_cash_closures_http as base
from test_cash_closures import filled

class EntryHTTPTests(unittest.TestCase):
 setUp=base.ClosureHTTPTests.setUp;stop=base.ClosureHTTPTests.stop;call=base.ClosureHTTPTests.call;command=base.ClosureHTTPTests.command;create=base.ClosureHTTPTests.create;error=base.ClosureHTTPTests.error
 def test_field_error_reaches_cashier_without_changing_draft(self):
  c=self.create(data=filled());d=filled();d['opening']['as_of']='2026-10-16T17:25:00Z'
  with self.assertRaises(HTTPError) as caught:self.call('/api/cash-closures/'+c['id']+'/save',self.mobile,dict(request_id=uuid4().hex,confirmed=True,version=c['version'],data=d))
  self.assertEqual(caught.exception.code,400);error=json.loads(caught.exception.read());self.assertIn('opening.as_of',error['field_errors']);self.assertIn('Chile',error['error'])
  stored=self.call('/api/cash-closures/'+c['id'],self.mobile);self.assertEqual(stored['version'],c['version']);self.assertEqual(stored['data'],c['data'])
 def test_unprivileged_source_unknown_remains_presentable(self):
  d=filled();d['opening']={'amount':None,'as_of':None,'reference':None};d['cash_source']['basis']='unknown';c=self.create(data=d)
  review=self.call('/api/cash-closures/'+c['id']+'/submission-review',self.mobile);self.assertTrue(review['closure']['calculation']['can_submit']);self.assertFalse(review['closure']['calculation']['can_reconcile'])
