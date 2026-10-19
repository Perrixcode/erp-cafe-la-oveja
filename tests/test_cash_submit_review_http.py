import unittest
from uuid import uuid4
import test_cash_closures_http as base
from erp import cash_closures as cl,cash_bags as bags
from test_cash_closures import PARTNER,filled

class FinalReviewHTTPTests(unittest.TestCase):
 setUp=base.ClosureHTTPTests.setUp;stop=base.ClosureHTTPTests.stop;call=base.ClosureHTTPTests.call;command=base.ClosureHTTPTests.command;create=base.ClosureHTTPTests.create;error=base.ClosureHTTPTests.error
 def test_mobile_qr_account_must_review_then_confirm_without_delivery(self):
  b=bags.execute(self.store,'create',dict(request_id=uuid4().hex,confirmed=True,quantity=1),PARTNER,self.url);b=bags.batch_view(self.store,b['batch_id'])['bags'][0]
  c=self.create(data=filled(),bag_id=b['id']);prefix=cl.PREFIX+'/'+c['id'];before=cl.view(self.store,c['id'],{'role':'partner','username':'QA'})
  review=self.call(prefix+'/submission-review',self.mobile);self.assertEqual(review['closure']['version'],c['version']);self.assertEqual(cl.view(self.store,c['id'],PARTNER),before)
  self.error(403,self.call,prefix+'/submission-review',self.cashier)
  body=dict(request_id=uuid4().hex,version=c['version'],confirmed=True)
  self.error(400,self.call,prefix+'/submit',self.mobile,body)
  body['review_token']=review['review_token'];self.error(400,self.call,prefix+'/submit',self.mobile,dict(body,confirmed=False))
  result=self.call(prefix+'/submit',self.mobile,body);self.assertEqual(result['delivery']['state'],'prepared');self.assertIsNone(result['delivery']['delivered_at'])
  self.assertEqual(result,self.call(prefix+'/submit',self.mobile,body));self.assertTrue(self.call('/cash-submit-review.js'))
 def test_fresh_version_with_old_review_is_rejected(self):
  c=self.create(data=filled());prefix=cl.PREFIX+'/'+c['id'];review=self.call(prefix+'/submission-review',self.mobile)
  data=filled();data['note']='Corrección después de revisión';new=self.command(prefix+'/save',self.mobile,version=c['version'],data=data)
  self.error(409,self.call,prefix+'/submit',self.mobile,dict(request_id=uuid4().hex,version=new['version'],review_token=review['review_token'],confirmed=True))
