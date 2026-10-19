from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4
import json,sqlite3,unittest
import test_cash_closures as base
from test_cash_closures import PARTNER,CASHIER,filled
from erp import cash_insights as ci,cash_closures as cl,cash_review as cr,cash_control_sources as src
from erp.domain import DomainError

DAY='2026-10-16'
SCOPE={'restaurant_id':'1','local_id':'1'}
def source(total=134100,closed='18:00:00'):
 return {'ok':True,'data':{'date':DAY,'shifts':{'1':{'name':'Mañana','registers':{'1':[dict(openedDate=DAY+'T11:00:00Z',closedDate=DAY+'T'+closed+'Z' if closed else '',initialAmount=57200,paymentMethods=[dict(paymentMethodID=5008,paymentMethod='Mercado Pago Point Smart',amount=total)])]}}}}}

class InsightTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp;cmd=base.ClosureTests.cmd;bag=base.ClosureTests.bag;create=base.ClosureTests.create;snapshot=base.ClosureTests.snapshot;review=base.ClosureTests.review
 def context(self,c):
  with self.store.connect() as db:return ci.context_db(db,c['id'])
 def setup_coupon(self):
  src.record(self.store,DAY,'collection',source(),SCOPE);c=self.create(filled());ctx=self.context(c)
  return c,dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],source_token=ctx['token'],session_key=ctx['reference']['session']['session_key'],terminal_id='Terminal QA',reference='Cupón QA 1',period_start=DAY+'T11:00:00Z',period_end=DAY+'T18:00:00Z',total=150700,basis='gross_with_tips',scope_verified=True,adjustments_verified=True,reviewed=True,note='Cupón sintético del mismo terminal, período y base; prueba aislada')
 def save(self,p):return ci.save_terminal(self.store,p,PARTNER)
 def dashboard(self,**kw):return ci.overview(self.store,{k:[v] for k,v in dict(start=DAY,end='2026-10-17',**kw).items()},PARTNER)
 def reviewed(self,c):
  d=cr.get_detail(self.store,c['id'],PARTNER)
  return cr.execute(self.store,'review',dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],fingerprint=d['fingerprint'],note='Revisión sintética de evidencias'),PARTNER)
 def test_real_example_different_period_is_pending_not_shortage(self):
  c,p=self.setup_coupon();p['period_end']=DAY+'T23:00:00Z';r=self.save(p)
  self.assertEqual(r['source']['provider_total'],134100);self.assertEqual(r['total'],150700)
  self.assertFalse(r['comparison']['comparable']);self.assertIsNone(r['comparison']['difference'])
  self.assertEqual(self.dashboard()['terminal_metrics'],dict(records=1,reviewed=0,pending=1,shortage=None,surplus=None))
 def test_exact_period_and_review_compare_without_financial_posting(self):
  c,p=self.setup_coupon();before=self.snapshot();r=self.save(p);after=self.snapshot()
  self.assertEqual(r['comparison']['difference'],16600);self.assertTrue(r['comparison']['comparable']);self.assertIsNone(r['tip'])
  self.assertEqual({k:v for k,v in after.items() if k not in ('ci_terminal_reports','cr_requests')},{k:v for k,v in before.items() if k not in ('ci_terminal_reports','cr_requests')})
 def test_missing_zero_net_basis_flags_and_open_period(self):
  c,p=self.setup_coupon();baseline=self.save(p)
  for change in ({'period_start':None},{'period_end':None},{'basis':'net_settlement'},{'basis':'unknown'},{'scope_verified':False},{'adjustments_verified':False},{'reviewed':False}):
   report=deepcopy(baseline);report.update(change);v=ci.comparable_report(report);self.assertFalse(v['comparable']);self.assertIsNone(v['difference'])
  report=deepcopy(baseline);report['source']['session']['closed_at']=None;self.assertIsNone(ci.comparable_report(report)['difference'])
  for state in ('missing','stale','error'):
   report=deepcopy(baseline);report['source']['source_read']['state']=state;self.assertIsNone(ci.comparable_report(report)['difference'])
  for amount in (None,True,-1,1.5):
   with self.assertRaises(DomainError):self.save(dict(p,request_id=uuid4().hex,total=amount))
  src.record(self.store,DAY,'collection',source(0),SCOPE);p.update(request_id=uuid4().hex,reference='Zero QA',total=0,source_token=self.context(c)['token']);zero=self.save(p)
  self.assertEqual(zero['comparison']['difference'],0);self.assertIsNone(zero['refunds'])
 def test_duplicate_retry_correction_and_immutable_versions(self):
  c,p=self.setup_coupon();a=self.save(p);self.assertEqual(self.save(p),a)
  with self.assertRaises(DomainError):self.save(dict(p,total=134100))
  with self.assertRaises(DomainError):self.save(dict(p,request_id=uuid4().hex))
  b=self.save(dict(p,request_id=uuid4().hex,supersedes=a['id'],total=134100))
  self.assertEqual(b['revision'],2);self.assertEqual(b['comparison']['difference'],0)
  self.assertEqual(self.dashboard()['terminal_metrics']['records'],1)
  with self.store.connect() as db:
   rows=ci.terminal_history(db,c['id']);self.assertTrue(next(r for r in rows if r['id']==a['id'])['superseded'])
   for sql in ('DELETE FROM ci_terminal_reports','UPDATE ci_terminal_reports SET actor=\'x\''):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  with self.assertRaises(DomainError):self.save(dict(p,request_id=uuid4().hex,supersedes=a['id']))
  other=self.create(shift='Tarde');ctx=self.context(other)
  with self.assertRaises(DomainError):self.save(dict(p,request_id=uuid4().hex,closure_id=other['id'],source_token=ctx['token'],supersedes=b['id']))
 def test_token_stable_on_identical_refresh_changed_source_invalidates(self):
  c,p=self.setup_coupon();src.record(self.store,DAY,'collection',source(),SCOPE);self.assertEqual(self.context(c)['token'],p['source_token']);a=self.save(p)
  src.record(self.store,DAY,'collection',source(134101),SCOPE)
  with self.assertRaises(DomainError) as e:self.save(dict(p,request_id=uuid4().hex,reference='Other'))
  self.assertEqual(e.exception.status,409);self.assertFalse(self.context(c)['history'][0]['comparison']['comparable'])
 def test_source_failure_and_closure_revision_require_new_review(self):
  c,p=self.setup_coupon();self.save(p);src.record(self.store,DAY,'collection',scope=SCOPE,error='Falla sintética')
  self.assertIsNone(self.context(c)['history'][0]['comparison']['difference'])
  src.record(self.store,DAY,'collection',source(),SCOPE);d=filled();d['note']='Aclaración';self.cmd('save',c,data=d)
  self.assertIn('cierre cambió',self.context(c)['history'][0]['comparison']['reasons'][0])
 def test_breakdown_attachment_and_role_validation(self):
  c,p=self.setup_coupon()
  for extra in ({'debit':100,'credit':200,'prepaid':0},{'tip':150701},{'attachment_id':'another-closure'},{'scope_verified':'yes'},{'confirmed':False}):
   with self.assertRaises(DomainError):self.save(dict(p,**extra))
  with self.assertRaises(DomainError):ci.save_terminal(self.store,p,CASHIER)
  with self.assertRaises(DomainError):ci.save_terminal(self.store,p,PARTNER,lambda:False)
  r=self.save(dict(p,debit=116000,credit=34700,prepaid=0));self.assertEqual(r['debit']+r['credit']+r['prepaid'],r['total'])
 def test_reviewed_bag_differences_do_not_cancel_cash_or_each_other(self):
  for shift,count in [('Mañana',9900),('Tarde',10100),('Noche',10000)]:
   c=self.cmd('submit',self.create(filled(),shift=shift),as_user=CASHIER,delivered=True);c=self.cmd('count',c,verified_amount=count,reason='Conteo QA');self.reviewed(c)
  self.create(shift='Pendiente');v=self.dashboard();m=v['current']['delivery'];self.assertEqual((m['records'],m['reviewed_known'],m['pending']),(4,3,1))
  self.assertEqual((m['shortage'],m['surplus'],m['shortage_count'],m['surplus_count']),(100,100,1,1));self.assertAlmostEqual(m['without_difference_percent'],100/3);self.assertAlmostEqual(m['absolute_difference_percent'],200/30000*100)
  self.assertIsNone(v['current']['cash']['shortage']);self.assertIsNone(v['available_for_payments']);self.assertFalse(v['previous_full_coverage']);self.assertIsNone(v['previous']['delivery']['shortage'])
 def test_original_responsible_editor_and_unknown_identity(self):
  c=self.cmd('submit',self.create(filled()),as_user=CASHIER,delivered=True);d=filled();d['note']='Edición del socio';c=self.cmd('save',c,data=d,reason='Aclaración');c=self.cmd('submit',c,as_user=PARTNER,reason='Aclaración revisada');c=self.cmd('count',c,verified_amount=10000);self.reviewed(c)
  v=self.dashboard();self.assertEqual(v['rows'][0]['owner']['name'],'Cajera QA');self.assertEqual(v['rows'][0]['owner']['last_editor'],'erp:socio-qa');self.assertTrue(v['rows'][0]['delivery']['eligible'])
  for marker in ('shared','legacy'):
   person=deepcopy(c);person['responsible'][marker]=True;self.assertFalse(ci.ownership(person)['safe']);self.assertEqual(ci.ownership(person)['key'],'unassigned')
 def test_filters_and_gets_readonly(self):
  c,p=self.setup_coupon();self.save(p);before=self.snapshot();self.dashboard();self.context(c);self.assertEqual(before,self.snapshot())
  self.assertEqual(len(self.dashboard(shift='Mañana',register=self.reg,cashier='erp:cajera-qa')['rows']),1)
  self.assertEqual(self.dashboard(shift='No existe')['terminal_reports'],[])
  self.assertEqual(self.dashboard(cashier='erp:socio-qa')['rows'],[])
  for q in ({'start':['2026-10-15']},{'start':[DAY,DAY]},{'unknown':['x']},{'compare_start':['2026-10-20'],'compare_end':[DAY]}):
   with self.assertRaises(DomainError):ci.overview(self.store,q,PARTNER)
  with self.assertRaises(DomainError):ci.overview(self.store,{},CASHIER)
