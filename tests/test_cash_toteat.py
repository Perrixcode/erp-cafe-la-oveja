from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import json,unittest
from test_cash_closures import ClosureTests,filled,PARTNER,CASHIER
from erp import cash_toteat as ct,cash_control_sources as src,cash_closures as cl
from erp.domain import DomainError
SCOPE={'restaurant_id':'1','local_id':'1'}
DAY='2026-10-16'

def collection():
 def session(opened,closed,cash):return dict(openedDate=DAY+'T'+opened+'Z',closedDate=DAY+'T'+closed+'Z' if closed else '',initialAmount=57200,paymentMethods=[dict(paymentMethodID=1000,paymentMethod='cash',amount=cash),dict(paymentMethodID=2,paymentMethod='debit',amount=12000)],registerName='CAJA 1')
 return {'ok':True,'data':{'date':DAY,'shifts':{'1':{'name':'Mañana','registers':{'1':[session('11:00:00','18:00:00',27700)]}},'2':{'name':'Tarde','registers':{'1':[session('18:00:00','23:00:00',40000)]}}}}}

def sales():
 return {'ok':True,'data':[dict(orderId='QA-'+str(i),paymentId=str(i),registerId=1,registerName='CAJA 1',dateClosed=DAY+'T'+hour+'Z',change=change,total=net-tip,totalWithGratuity=net,gratuity=tip,paymentForms=[dict(id=1000,method='CASH',amount=net+change)]) for i,hour,net,tip,change in [(1,'12:00:00',27700,1000,2300),(2,'19:00:00',40000,2000,0)]]}

class ToteatTests(unittest.TestCase):
 setUp=ClosureTests.setUp;cmd=ClosureTests.cmd;bag=ClosureTests.bag;create=ClosureTests.create;review=ClosureTests.review;snapshot=ClosureTests.snapshot
 def sources(self):
  a=src.record(self.store,DAY,'collection',collection(),SCOPE);b=src.record(self.store,DAY,'sales',sales(),SCOPE)
  with self.store.connect() as db:ses=json.loads(db.execute('SELECT normalized_json FROM cc_source_reads WHERE id=?',(a['id'],)).fetchone()[0])['sessions']
  return a,b,ses
 def view(self,session=None,at=None):
  with self.store.connect() as db:return ct.project(db,DAY,self.reg,session,at)
 def linked(self,session):
  c=self.create(filled());return self.cmd('save',c,data=c['data'],identity=dict(business_date=DAY,register_id=self.reg,shift_name='Mañana',external_session_key=session))
 def test_session_methods_are_preserved_without_inventing_tips(self):
  self.sources();v=self.view();self.assertEqual(len(v['sessions']),2)
  self.assertEqual(v['sessions'][0]['methods'][1]['reported_total'],12000);self.assertIsNone(v['sessions'][0]['methods'][1]['tip']);self.assertIsNone(v['selected'])
 def test_two_shifts_have_disjoint_sales_and_unknown_is_not_zero(self):
  _,_,sessions=self.sources();one=self.view(sessions[0]['session_key']);two=self.view(sessions[1]['session_key'])
  self.assertEqual(one['sales']['methods'][0]['total'],27700);self.assertEqual(two['sales']['methods'][0]['total'],40000)
  self.assertEqual(one['sales']['methods'][0]['tip'],1000);self.assertFalse(one['sales']['complete'])
 def test_options_deduplicate_multiple_reads(self):
  self.sources();self.sources();c=self.create();opts=cl.options(self.store,c['id'],CASHIER)
  self.assertEqual(len(opts['sessions']),2);self.assertEqual(opts['sales'],[])
 def test_daily_cash_reference_is_invalid_without_exact_session(self):
  _,read,_=self.sources();d=filled();d['cash_source'].update(type='toteat_sales',read_id=read['id']);c=self.create(d)
  self.assertFalse(c['calculation']['source_reference']['valid']);self.assertIsNone(c['calculation']['expected'])
 def test_exact_session_cash_reference_never_uses_collection_cash(self):
  _,read,sessions=self.sources();c=self.linked(sessions[0]['session_key']);d=deepcopy(c['data']);d['cash_source'].update(type='toteat_sales',read_id=read['id'])
  c=self.cmd('save',c,data=d);self.assertFalse(c['calculation']['source_reference']['valid']);self.assertEqual(c['calculation']['source_reference']['net'],27700);self.assertIsNone(c['calculation']['expected'])
 def test_failed_refresh_retains_prior_read_and_invalidates_copy(self):
  _,_,sessions=self.sources();src.record(self.store,DAY,'sales',scope=SCOPE,error='No disponible')
  v=self.view(sessions[0]['session_key']);self.assertEqual(v['sources']['sales']['state'],'error');self.assertEqual(v['sales']['methods'][0]['total'],27700)
 def test_stale_is_visible_not_zero(self):
  _,_,sessions=self.sources();later=(datetime.now(timezone.utc)+timedelta(minutes=7)).isoformat()
  v=self.view(sessions[0]['session_key'],later);self.assertEqual(v['sources']['sales']['state'],'stale');self.assertEqual(v['sales']['methods'][0]['total'],27700)
 def test_unknown_tip_and_mixed_payment_are_not_zero(self):
  p=sales();p['data'][0].pop('gratuity');p['data'][1]['paymentForms'].append(dict(id=2,method='debit',amount=1000))
  rows=ct.sales_payments(p['data'],SCOPE);self.assertIsNone(rows[0]['methods'][0]['tip']);self.assertTrue(all(m['tip'] is None for m in rows[1]['methods']))
 def test_identical_duplicate_payment_is_counted_once_conflict_rejected(self):
  p=sales();p['data'].append(deepcopy(p['data'][0]));result=src.record(self.store,DAY,'sales',p,SCOPE)
  with self.store.connect() as db:normal=json.loads(db.execute('SELECT normalized_json FROM cc_source_reads WHERE id=?',(result['id'],)).fetchone()[0])
  self.assertEqual(len(normal['payments']),2)
  p['data'][-1]['change']=1;self.assertEqual(src.record(self.store,DAY,'sales',p,SCOPE)['state'],'error')
 def test_overlap_or_missing_timestamp_never_assigned_to_a_shift(self):
  _,_,sessions=self.sources();payments=ct.sales_payments(sales()['data'],SCOPE);overlap=deepcopy(sessions);overlap[1]['opened_at']=overlap[0]['opened_at']
  result=ct.session_sales(overlap,overlap[0],payments);self.assertEqual(result['payments'],0);self.assertEqual(result['unassigned_payments'],1)
  payments[0]['paid_at']=None;self.assertEqual(ct.session_sales(sessions,sessions[0],payments)['payments'],0)
 def test_open_session_stays_provisional_and_cash_not_expected(self):
  p=collection();p['data']['shifts']['1']['registers']['1'][0]['closedDate']='';p['data']['shifts'].pop('2')
  src.record(self.store,DAY,'collection',p,SCOPE);v=self.view();self.assertEqual(v['state'],'provisional');self.assertIsNone(v['selected']['expected']);self.assertIsNone(v['selected']['cash_reported'])
 def test_missing_selected_session_is_not_replaced(self):
  self.sources();v=self.view('absent');self.assertTrue(v['missing_session']);self.assertIsNone(v['selected'])
 def test_concurrent_refresh_requests_deduplicated_without_closure_writes(self):
  self.create(filled());before=self.snapshot(['cl_closures','cl_versions','cash_bags','cl_source_reviews'])
  with ThreadPoolExecutor(max_workers=5) as pool:out=list(pool.map(lambda _:ct.request_refresh(self.store,DAY),range(5)))
  self.assertEqual(len({r['requested_at'] for r in out}),1);self.assertEqual(self.snapshot(['cl_closures','cl_versions','cash_bags','cl_source_reviews']),before)
 def test_snapshots_freeze_at_presentation_and_no_get_writes(self):
  _,_,sessions=self.sources();c=self.linked(sessions[0]['session_key']);c=self.cmd('submit',c,as_user=CASHIER);frozen=deepcopy(c['toteat_source'])
  changed=sales();changed['data'][0]['paymentForms'][0]['amount']=99999;src.record(self.store,DAY,'sales',changed,SCOPE)
  before=self.snapshot();current=cl.view(self.store,c['id'],PARTNER);self.assertEqual(current['toteat_source'],frozen);self.assertTrue(current['toteat_source_frozen']);self.view(sessions[0]['session_key']);self.assertEqual(self.snapshot(),before)
 def test_changed_session_does_not_change_physical_or_manual_data(self):
  _,_,sessions=self.sources();c=self.create(filled());before=deepcopy(c['data'])
  c=self.cmd('save',c,data=c['data'],identity=dict(business_date=DAY,register_id=self.reg,shift_name='Tarde',external_session_key=sessions[1]['session_key']))
  self.assertEqual(c['data'],before)

 def test_provider_card_id_keeps_debit_credit_and_prepaid_separate(self):
  _,_,sessions=self.sources();rows=[]
  for i,name in enumerate(('DEBIT_CARD','CREDIT_CARD','PREPAID_CARD')):
   r=deepcopy(sales()['data'][0]);r.update(orderId='card-'+str(i),paymentId=str(i),total=900,totalWithGratuity=1000,gratuity=100,change=0,paymentForms=[dict(id=5008,method='MERCADO_PAGO_POINT_SMART',name=name,amount=1000,tip=100)]);rows.append(r)
  result=ct.session_sales(sessions,sessions[0],ct.sales_payments(rows,SCOPE));self.assertEqual(len(result['methods']),3);self.assertEqual(result['issues'],[])
  self.assertEqual({m['key'] for m in result['methods']},{'debit','credit',None});self.assertEqual(sum(m['tip'] for m in result['methods']),300)
 def test_mixed_payment_explicit_tips_reconcile_without_inventing_split(self):
  r=deepcopy(sales()['data'][0]);r.update(total=15000,totalWithGratuity=16500,gratuity=1500,change=3500,paymentForms=[dict(id=1000,method='CASH',amount=9000,tip=500),dict(id=5008,method='MERCADO_PAGO_POINT_SMART',name='DEBIT_CARD',amount=11000,tip=1000)])
  parts=ct.sales_payments([r],SCOPE)[0]['methods'];self.assertEqual([(p['sales'],p['tip']) for p in parts],[(5000,500),(10000,1000)])
  r['paymentForms'][0]['tip']=400;parts=ct.sales_payments([r],SCOPE)[0]['methods'];self.assertTrue(all(p['tip'] is None for p in parts))

 def test_manual_review_fingerprint_ignores_unrelated_daily_reads(self):
  from erp.cash_review import fingerprint
  self.sources();c=self.create(filled())
  with self.store.connect() as db:first=fingerprint(db,c)
  self.sources()
  with self.store.connect() as db:self.assertEqual(fingerprint(db,c),first)
  src.record(self.store,DAY,'sales',scope=SCOPE,error='Consulta fallida')
  with self.store.connect() as db:self.assertEqual(fingerprint(db,c),first)

del ClosureTests
