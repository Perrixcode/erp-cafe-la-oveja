from copy import deepcopy
from datetime import date,datetime,timezone
from unittest.mock import patch
from uuid import uuid4
import base64,json,unittest
import test_cash_closures as base
from test_cash_closures import PARTNER,CASHIER,filled,movement
from test_cash_insights import source,SCOPE
from erp import cash_closures as cl,cash_closure_calc as calc,cash_closure_imports as imports,cash_toteat as ct,cash_review as cr,cash_insights as ci,cash_control_sources as src,cash_business_day as calendar
from erp.domain import DomainError

DAY='2026-10-16'
def totals():
 d=filled();fresh=calc.empty(2);d.update({k:fresh[k] for k in ('schema_version','method_scheme','report_cutoff_at','imports','methods')})
 d['report_cutoff_at']=d['counted_at']
 for m in d['methods']:m.update(used=False,total=0)
 d['methods'][0].update(used=True,total=27700,sales=None,tip=None)
 return d

def collection(cash=27700,mp=134100,closed='2026-10-16T18:00:00Z'):
 p=source(mp);s=p['data']['shifts']['1']['registers']['1'][0];s['closedDate']=closed
 s['paymentMethods']+=[dict(paymentMethodID=1000,paymentMethod='cash',amount=cash),dict(paymentMethodID=5119,paymentMethod='Mercat',amount=12000)]
 return p

class OperationalTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp;cmd=base.ClosureTests.cmd;bag=base.ClosureTests.bag;create=base.ClosureTests.create;snapshot=base.ClosureTests.snapshot;review=base.ClosureTests.review
 def linked(self):
  src.record(self.store,DAY,'collection',collection(),SCOPE)
  d=totals()
  for k in ('cash','point_smart','mercat'):
   next(m for m in d['methods'] if m['key']==k).update(used=None,total=None)
  c=self.create(d);v=cl.options(self.store,c['id'],CASHIER)['toteat']
  return self.cmd('save',c,as_user=CASHIER,data=c['data'],identity=dict(business_date=DAY,register_id=self.reg,shift_name='Mañana',external_session_key=v['selected']['session_key']))
 def imported(self):
  c=self.linked();v=cl.options(self.store,c['id'],CASHIER)['toteat']
  return self.cmd('import-source',c,as_user=CASHIER,source_token=v['import_token'])
 def test_known_total_unknown_split_presents_no_tip_requirement(self):
  c=self.create(totals());c=self.cmd('submit',c,as_user=CASHIER)
  self.assertEqual(c['calculation']['all_methods_total'],27700);self.assertIsNone(c['data']['methods'][0]['sales']);self.assertIsNone(c['data']['methods'][0]['tip'])
  self.assertEqual(c['delivery']['state'],'prepared');self.assertEqual(c['status'],'pending')
 def test_known_split_preserved_total_once_no_cash_event(self):
  d=totals();d['methods'][0].update(sales=26700,tip=1000)
  c=self.cmd('create',identity=dict(business_date=DAY,register_id=self.reg,shift_name='Admin'),data=d,bag_id=self.bag()['id'])
  c=self.review(c);self.assertEqual(c['calculation']['expected'],84900);self.assertEqual(c['calculation']['all_methods_total'],27700)
  self.assertEqual(self.snapshot(['cc_cash_events'])['cc_cash_events'],[])
 def test_provider_aggregate_mercat_emergency_separate(self):
  d=totals();next(m for m in d['methods'] if m['key']=='point_smart').update(used=True,total=150700);next(m for m in d['methods'] if m['key']=='mercat').update(used=True,total=12000)
  v=calc.calculate(calc.normalize(d));self.assertEqual(v['all_methods_total'],190400);self.assertEqual([m['key'] for m in v['methods']],list(calc.PROVIDER_METHODS))
  with self.assertRaises(DomainError):calc.normalize(dict(d,methods=d['methods']+[dict(d['methods'][1],key='debit')]))
 def test_equations_before_and_after_count_are_distinct(self):
  d=totals();d['opening']['amount']=36800;d['methods'][0]['total']=111000;m=movement();m.update(amount=80500,occurred_at='2026-10-16T17:00:00Z');d['movements']=[m];c=self.review(self.create(d));self.assertEqual(c['calculation']['expected'],67300)
  d=totals();d['cash']={str(v):0 for v in calc.DENOMINATIONS};d['cash']['20000']=5;d['retained']={str(v):0 for v in calc.DENOMINATIONS};d['retained']['20000']=1;m=movement();m['amount']=10000;d['movements']=[m]
  v=calc.calculate(calc.normalize(d));self.assertEqual((v['A'],v['B'],v['C'],v['cash_at_seal']),(100000,20000,70000,90000))
 def test_import_is_server_owned_provisional_and_cannot_be_laundered(self):
  c=self.imported();self.assertEqual(len(c['data']['imports']),3);self.assertEqual(c['data']['imports'][0]['endpoint'],'collection')
  d=deepcopy(c['data']);d['cash_source'].update(type='manual_report',read_id=None,basis='net_sales_before_movements',reference='Imported is not independent')
  c=self.cmd('save',c,data=d);c=self.review(c);self.assertIsNone(c['calculation']['expected'])
  for imports_value in ([],[dict(d['imports'][0],status='manual_replaced')]):
   with self.assertRaises(DomainError):self.cmd('save',c,data=dict(d,imports=imports_value))
  c=self.cmd('submit',c,as_user=CASHIER);self.assertEqual(c['status'],'pending')
 def test_scope_change_invalidates_imports_preserves_manual_and_physical(self):
  c=self.imported();before=deepcopy(c['data']);reg=self.cmd('register',name='Caja 2',provider_register_id='2')['register_id']
  c=self.cmd('save',c,as_user=CASHIER,data=c['data'],identity=dict(business_date=DAY,register_id=reg,shift_name='Mañana'))
  for key in ('cash','retained','movements','methods'):self.assertEqual(c['data'][key],before[key])
  self.assertTrue(all(x['status']=='scope_changed' for x in c['calculation']['imports']));self.assertFalse(c['calculation']['can_submit'])
 def test_cutoff_change_and_import_edit_require_explicit_resolution(self):
  c=self.imported();d=deepcopy(c['data']);d['report_cutoff_at']='2026-10-16T17:18:00Z';c=self.cmd('save',c,data=d)
  self.assertFalse(c['calculation']['can_submit']);self.assertEqual(c['calculation']['imports'][0]['status'],'scope_changed')
  i=c['data']['imports'][0];c=self.cmd('resolve-import',c,as_user=CASHIER,import_id=i['id'],resolution='discard',reason='El alcance no corresponde')
  self.assertEqual(c['data']['imports'][0]['status'],'discarded');self.assertIsNone(c['data']['methods'][0]['total']);self.assertEqual(c['data']['cash'],d['cash'])
 def test_same_session_refresh_changes_only_pertinent_import(self):
  c=self.imported();p=collection();p['data']['shifts']['1']['registers']['2']=[dict(p['data']['shifts']['1']['registers']['1'][0])];src.record(self.store,DAY,'collection',p,SCOPE)
  current=cl.view(self.store,c['id'],PARTNER);self.assertTrue(all(x['status']=='provisional' for x in current['calculation']['imports']))
  src.record(self.store,DAY,'collection',collection(mp=999),SCOPE);current=cl.view(self.store,c['id'],PARTNER)
  self.assertEqual({x['method']:x['status'] for x in current['calculation']['imports']},{'cash':'provisional','point_smart':'source_changed','mercat':'provisional'})
 def test_manual_review_survives_unrelated_source_but_import_review_does_not(self):
  c=self.create(totals());d=cr.get_detail(self.store,c['id'],PARTNER);cr.execute(self.store,'review',dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],fingerprint=d['fingerprint'],note='Manual independent report'),PARTNER)
  src.record(self.store,DAY,'collection',collection(),SCOPE);self.assertTrue(cr.get_detail(self.store,c['id'],PARTNER)['reviewed'])
 def test_operating_calendar_friday_open_weekend_and_monday_closes_tuesday(self):
  self.assertEqual(calendar.operational_day(date(2026,10,17)),date(2026,10,16));self.assertEqual(calendar.operational_day(date(2026,10,18)),date(2026,10,16));self.assertEqual(calendar.previous_day(date(2026,10,19)),date(2026,10,16))
  with self.assertRaises(DomainError):self.cmd('create',identity=dict(business_date='2026-10-17',register_id=self.reg,shift_name='Weekend'))
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return datetime(2026,10,20,15,tzinfo=timezone.utc).astimezone(tz)
  d=totals();d.update(counted_at='2026-10-20T10:00:00Z',sealed_at='2026-10-20T10:05:00Z')
  with patch('erp.cash_closures.datetime',Clock),patch('erp.cash_closures.stamp',return_value='2026-10-20T15:00:00+00:00'):
   c=self.cmd('create',identity=dict(business_date='2026-10-19',register_id=self.reg,shift_name='Lunes'),data=d)
  self.assertEqual(c['business_date'],'2026-10-19');self.assertTrue(c['data']['counted_at'].startswith('2026-10-20'))
 def test_open_empty_and_twenty_outside_remain_unknown_unassigned(self):
  s=dict(session_key='one',register_id='1',opened_at=DAY+'T11:00:00+00:00',closed_at=DAY+'T18:00:00+00:00');payments=[dict(payment_key=str(i),payment_id=str(i),order_id='QA'+str(i),register_id='1',paid_at=DAY+'T23:00:00+00:00',methods=[],issues=[]) for i in range(20)]
  v=ct.session_sales([s],s,payments+payments);self.assertEqual(v['unassigned_payments'],20);self.assertEqual(len(v['pending_payments']),20);self.assertTrue(all(x['reason']=='outside_session_windows' and x['assigned_session'] is None for x in v['pending_payments']));self.assertFalse(v['import_eligible'])
  p=collection(closed='');p['data']['shifts']['1']['registers']['1'][0]['paymentMethods']=[];r=src.collection(DAY,p,SCOPE)['sessions'][0];self.assertIsNone(r['cash_reported']);self.assertEqual(r['methods'],[])
 def test_generic_contrast_never_reports_unproved_difference(self):
  d=totals();d['methods'][0]['comparison'].update(kind='terminal',reference='No period',amount=27701);v=calc.calculate(calc.normalize(d));self.assertIsNone(v['methods'][0]['independent_difference'])
 def test_mp_verified_cut_survives_later_admin_close(self):
  src.record(self.store,DAY,'collection',collection(closed=''),SCOPE);c=self.create(totals());c=self.cmd('attach',c,file_name='corte.pdf',file_type='application/pdf',file_data=base64.b64encode(b'%PDF-1.4\nsynthetic').decode())
  with self.store.connect() as db:ctx=ci.context_db(db,c['id'])
  p=dict(request_id=uuid4().hex,confirmed=True,closure_id=c['id'],source_token=ctx['token'],session_key=ctx['reference']['session']['session_key'],terminal_id='QA',reference='QA-cut',period_start=DAY+'T11:00:00Z',period_end=DAY+'T17:00:00Z',total=150700,basis='gross_with_tips',scope_verified=True,adjustments_verified=True,reviewed=True,note='Independent verified economic cut',attachment_id=c['attachments'][0]['id'],economic_scope=dict(kind='verified_cut',start=DAY+'T11:00:00Z',end=DAY+'T17:00:00Z',toteat_total=150700,reference='Attached actual cut QA',verified=True))
  r=ci.save_terminal(self.store,p,PARTNER);self.assertEqual(r['comparison']['difference'],0)
  src.record(self.store,DAY,'collection',collection(mp=170000,closed='2026-10-17T12:00:00Z'),SCOPE)
  with self.store.connect() as db:r=ci.terminal_history(db,c['id'])[0]
  self.assertEqual(r['comparison']['difference'],0)
  r['economic_scope']['end']=DAY+'T16:00:00+00:00';self.assertIsNone(ci.comparable_report(r)['difference'])
 def test_empty_eligible_sample_is_not_perfect_or_available(self):
  self.create(totals());v=ci.overview(self.store,{'start':[DAY],'end':[DAY]},PARTNER)
  self.assertIsNone(v['current']['cash']['without_difference_percent']);self.assertIsNone(v['available_for_payments']);self.assertEqual(v['operations']['custody_sample'],0)
 def test_v2_cannot_be_downgraded_to_drop_provenance(self):
  c=self.imported()
  with self.assertRaises(DomainError):self.cmd('save',c,data=filled())
 def test_cannot_manually_replace_import_without_independent_attachment(self):
  c=self.imported();i=c['data']['imports'][0]
  for user in (PARTNER,CASHIER):
   with self.assertRaises(DomainError):self.cmd('resolve-import',c,as_user=user,import_id=i['id'],resolution='manual',amount=i['value'],reason='Only a claim, no evidence')

if __name__=='__main__':unittest.main()
