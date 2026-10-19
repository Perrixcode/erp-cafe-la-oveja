import unittest,json
from copy import deepcopy
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import test_cash_closures as base
from erp import cash_closures as cl,cash_review as cr
from erp.domain import DomainError

class FinalReviewTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp;cmd=base.ClosureTests.cmd;create=base.ClosureTests.create;bag=base.ClosureTests.bag;snapshot=base.ClosureTests.snapshot
 def body(self,c,account=base.CASHIER):return dict(request_id=uuid4().hex,confirmed=True,version=c['version'],review_token=cl.submission_review(self.store,c['id'],account)['review_token'])
 def test_open_review_or_cancel_does_not_present_and_missing_confirmation_is_rejected(self):
  c=self.create(base.filled());before=self.snapshot();r=cl.submission_review(self.store,c['id'],base.CASHIER)
  self.assertEqual(before,self.snapshot());self.assertEqual(r['closure']['status'],'draft')
  for body in [dict(request_id=uuid4().hex,version=c['version'],confirmed=True),dict(self.body(c),confirmed=False)]:
   with self.assertRaises(DomainError):cl.execute(self.store,'submit',body,base.CASHIER,c['id'])
  self.assertEqual(before,self.snapshot())
 def test_confirmed_submission_never_delivers_physically(self):
  c=self.create(base.filled());body=self.body(c);c=cl.execute(self.store,'submit',body,base.CASHIER,c['id'])
  self.assertEqual(c['delivery']['state'],'prepared');self.assertIsNone(c['delivery']['delivered_at']);self.assertIsNone(c['bag']['verified_amount'])
  with self.assertRaises(DomainError):cl.execute(self.store,'submit',dict(body,delivered=True),base.CASHIER,c['id'])
  delivered=self.cmd('deliver',c,as_user=base.CASHIER);self.assertEqual(delivered['delivery']['state'],'delivered')
 def test_saved_changes_require_fresh_review(self):
  c=self.create(base.filled());old=self.body(c);data=base.filled();data['cash']['100']=5;c=self.cmd('save',c,as_user=base.CASHIER,data=data)
  with self.assertRaises(DomainError) as e:cl.execute(self.store,'submit',dict(old,version=c['version']),base.CASHIER,c['id'])
  self.assertEqual(e.exception.status,409);self.assertEqual(cl.view(self.store,c['id'],base.CASHIER)['status'],'draft')
  new=self.body(c);self.assertNotEqual(old['review_token'],new['review_token']);self.assertEqual(cl.execute(self.store,'submit',new,base.CASHIER,c['id'])['calculation']['A'],85000)
 def test_source_changed_without_closure_version_requires_review_again(self):
  c=self.create(base.filled());old=self.body(c);d=cr.get_detail(self.store,c['id'],base.PARTNER)
  methods=[dict(key=k,sales=None,tip=None) for k in cr.calc.METHODS]
  cr.execute(self.store,'methods',dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],fingerprint=d['fingerprint'],reference='Nueva evidencia sintética',methods=methods,cash_basis='unknown'),base.PARTNER)
  self.assertEqual(cl.view(self.store,c['id'],base.CASHIER)['version'],c['version'])
  with self.assertRaises(DomainError) as e:cl.execute(self.store,'submit',old,base.CASHIER,c['id'])
  self.assertEqual(e.exception.status,409)
 def test_review_is_bound_to_actor_and_ownership_permissions_still_apply(self):
  c=self.create(base.filled())
  with self.assertRaises(DomainError):cl.submission_review(self.store,c['id'],base.OTHER)
  with self.assertRaises(DomainError):cl.execute(self.store,'submit',self.body(c,base.PARTNER),base.CASHIER,c['id'])
  with self.assertRaises(DomainError):cl.execute(self.store,'submit',self.body(c),base.CASHIER,c['id'],lambda db:False)
 def test_same_confirmation_twice_concurrently_is_one_presentation(self):
  c=self.create(base.filled());body=self.body(c)
  with ThreadPoolExecutor(2) as pool:out=list(pool.map(lambda _:cl.execute(self.store,'submit',body,base.CASHIER,c['id']),range(2)))
  self.assertEqual(out[0],out[1]);self.assertEqual(out[0]['delivery']['state'],'prepared')
  with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM cl_versions WHERE action='submit'").fetchone()[0],1)
 def test_review_does_not_bypass_incomplete_closure_validation(self):
  c=self.create();r=cl.submission_review(self.store,c['id'],base.CASHIER);self.assertFalse(r['closure']['calculation']['can_submit'])
  with self.assertRaises(DomainError):cl.execute(self.store,'submit',self.body(c),base.CASHIER,c['id'])
