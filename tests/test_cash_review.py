from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
import json,sqlite3,unittest
from pypdf import PdfReader
from erp import cash_review as cr,cash_closures as cl,cash_control_sources as sources,cash_bags as bags
from erp.domain import DomainError
import test_cash_closures as base
from test_cash_closures import PARTNER,CASHIER,OTHER,filled,movement,ORIGIN

class ReviewTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp;cmd=base.ClosureTests.cmd;bag=base.ClosureTests.bag;create=base.ClosureTests.create;snapshot=base.ClosureTests.snapshot
 def op(self,action,**body):return cr.execute(self.store,action,dict(request_id=uuid4().hex,confirmed=True,**body),PARTNER)
 def delivered(self,shift='Mañana'):return self.cmd('submit',self.create(filled(),shift=shift),as_user=CASHIER,delivered=True)
 def item(self,c,amount=10000,note=''):
  item=cr.lookup(self.store,c['bag_id'],PARTNER)
  return {k:item[k] for k in ('bag_id','bag_version','closure_version')}|dict(amount=amount,note=note)
 def batch(self,items):
  b=self.op('batch-create',name='Revisión semanal QA')
  return self.op('batch-save',id=b['id'],version=b['version'],items=items)
 def dashboard(self,**kw):return cr.overview(self.store,{k:[v] for k,v in dict(start='2026-10-16',end='2026-10-17',**kw).items()},PARTNER)
 def test_draft_no_count_confirmation_idempotence_and_qr_rescan(self):
  c=self.delivered();b=self.batch([self.item(c)])
  self.assertIsNone(cl.view(self.store,c['id'],PARTNER)['bag']['verified_amount']);self.assertEqual(b['total_counted'],10000)
  with self.assertRaises(DomainError):cr.execute(self.store,'batch-confirm',dict(request_id=uuid4().hex,id=b['id'],version=b['version']),PARTNER)
  body=dict(request_id=uuid4().hex,confirmed=True,id=b['id'],version=b['version'])
  one=cr.execute(self.store,'batch-confirm',body,PARTNER);two=cr.execute(self.store,'batch-confirm',body,PARTNER)
  self.assertEqual(one,two);self.assertEqual(one['state'],'confirmed');self.assertEqual(one['version'],3)
  result=cr.lookup(self.store,ORIGIN+'/?module=cash&bag='+c['bag_id'],PARTNER)
  self.assertTrue(result['already_counted']);self.assertFalse(result['eligible'])
  c=cl.view(self.store,c['id'],PARTNER);self.assertEqual(c['status'],'pending');self.assertEqual(c['bag']['verified_amount'],10000)
  self.assertEqual(self.snapshot(['cc_cash_events'])['cc_cash_events'],[])
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cr_counts').fetchone()[0],1)
 def test_duplicate_save_and_atomic_rollback_after_single_count(self):
  c1=self.delivered();c2=self.delivered('Tarde');item=self.item(c1)
  with self.assertRaises(DomainError):self.batch([item,item])
  b=self.batch([item,self.item(c2)]);self.cmd('count',c2,verified_amount=10000)
  before=self.snapshot()
  with self.assertRaises(DomainError):self.op('batch-confirm',id=b['id'],version=b['version'])
  self.assertEqual(before,self.snapshot());self.assertIsNone(cl.view(self.store,c1['id'],PARTNER)['bag']['verified_amount'])
 def test_concurrent_batches_cannot_double_count_and_single_race(self):
  c=self.delivered();b1=self.batch([self.item(c)]);b2=self.batch([self.item(c)])
  def confirm(b):
   try:return self.op('batch-confirm',id=b['id'],version=b['version'])['state']
   except DomainError as e:return e.status
  with ThreadPoolExecutor(2) as pool:result=list(pool.map(confirm,[b1,b2]))
  self.assertCountEqual(result,['confirmed',409])
  c=cl.view(self.store,c['id'],PARTNER)
  with self.assertRaises(DomainError):self.cmd('count',c,verified_amount=10000)
  with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM cash_bag_events WHERE action='verify'").fetchone()[0],1)
 def test_stale_draft_version_bag_edit_and_amount_validation(self):
  c=self.delivered();b=self.batch([self.item(c)])
  self.op('batch-save',id=b['id'],version=b['version'],items=[])
  with self.assertRaises(DomainError):self.op('batch-save',id=b['id'],version=b['version'],items=[self.item(c)])
  for amount in (True,-1,1.1):
   with self.assertRaises(DomainError):self.batch([self.item(c,amount)])
  b=self.batch([self.item(c)]);d=filled();d['note']='Cambio después de seleccionar';self.cmd('save',c,data=d,reason='Nota QA')
  with self.assertRaises(DomainError):self.op('batch-confirm',id=b['id'],version=b['version'])
 def test_difference_needs_note_and_keeps_each_bag_despite_net_zero(self):
  c1=self.delivered();c2=self.delivered('Tarde');b=self.batch([self.item(c1,9900),self.item(c2,10100,'Sobrante contado')])
  with self.assertRaises(DomainError):self.op('batch-confirm',id=b['id'],version=b['version'])
  b=self.op('batch-save',id=b['id'],version=b['version'],items=[self.item(c1,9900,'Faltante contado'),self.item(c2,10100,'Sobrante contado')]);b=self.op('batch-confirm',id=b['id'],version=b['version'])
  self.assertEqual(b['total_difference'],0);self.assertEqual(b['absolute_differences'],200)
  self.assertEqual(self.dashboard()['counts']['differences'],2)
 def test_roles_revocation_and_gets_do_not_write(self):
  c=self.delivered();before=self.snapshot()
  for account in (CASHIER,OTHER,{'role':'partner','realm':'bag','username':'QA'}):
   for fn,args in [(cr.overview,({},)),(cr.lookup,(c['bag_id'],)),(cr.get_detail,(c['id'],))]:
    with self.assertRaises(DomainError) as e:fn(self.store,*args,account)
    self.assertEqual(e.exception.status,403)
  with self.assertRaises(DomainError):cr.execute(self.store,'batch-create',dict(request_id=uuid4().hex,name='QA'),PARTNER,lambda db:False)
  self.dashboard();cr.lookup(self.store,c['bag_id'],PARTNER);cr.get_detail(self.store,c['id'],PARTNER)
  self.assertEqual(before,self.snapshot())
 def test_filters_manual_review_ignores_unrelated_reads_but_closure_change_invalidates(self):
  c=self.delivered();other=self.create(shift='Otra');d=cr.get_detail(self.store,c['id'],PARTNER)
  d=self.op('review',closure_id=c['id'],fingerprint=d['fingerprint'],note='Revisado con fuentes pendientes')
  self.assertTrue(d['reviewed']);self.assertEqual(d['status'],'pending')
  self.assertEqual(len(self.dashboard(state='reviewed')['rows']),1)
  self.assertEqual(len(self.dashboard(cashier='erp:otra-qa')['rows']),0)
  self.assertEqual(len(self.dashboard(register=self.reg)['rows']),2)
  sources.record(self.store,'2026-10-16','sales',scope={'restaurant_id':'1','local_id':'1'},error='Fuente incompleta')
  self.assertTrue(cr.get_detail(self.store,c['id'],PARTNER)['reviewed'])
  data=deepcopy(c['data']);data['note']='Cambio del propio cierre';self.cmd('save',c,data=data,reason='Corrección documentada')
  self.assertFalse(cr.get_detail(self.store,c['id'],PARTNER)['reviewed'])
  with self.assertRaises(DomainError):self.op('review',closure_id=c['id'],fingerprint=d['fingerprint'],note='Revisión obsoleta')
 def test_alerts_only_observed_sessions_and_post_delivery_changes(self):
  c=self.cmd('submit',self.create(filled()),as_user=CASHIER)
  self.assertIn('closure_without_delivery',cr.get_detail(self.store,c['id'],PARTNER)['alerts'])
  c=self.cmd('deliver',c,as_user=CASHIER);self.assertIn('delivery_without_count',cr.get_detail(self.store,c['id'],PARTNER)['alerts'])
  d=filled();d['note']='Aclaración';self.cmd('save',c,data=d,reason='Nota posterior')
  self.assertIn('modified_after_delivery',cr.get_detail(self.store,c['id'],PARTNER)['alerts'])
  self.assertNotIn('register_without_closure',[a['kind'] for a in self.dashboard()['alerts']])
  self.cmd('register',name='CAJA 2',provider_register_id='2')
  payload={'ok':True,'data':{'date':'2026-10-16','shifts':{'1':{'registers':{'2':[{'openedDate':'2026-10-16T12:00:00Z','closedDate':'2026-10-16T20:00:00Z','initialAmount':100,'paymentMethods':[{'paymentMethodID':1000,'amount':500}]}]}}}}}
  sources.record(self.store,'2026-10-16','collection',payload,{'restaurant_id':'1','local_id':'1'})
  self.assertEqual([a['kind'] for a in self.dashboard()['alerts']].count('register_without_closure'),1)
  self.assertNotIn('register_without_closure',[a['kind'] for a in self.dashboard(cashier='erp:cajera-qa')['alerts']])
 def test_batch_mixed_dates_and_count_period_are_separate(self):
  c1=self.delivered()
  class Future(datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,10,17,12,tzinfo=tz)
  # Historical weekend fixture predates the operating-calendar restriction.
  with patch('erp.cash_closures.datetime',Future),patch('erp.cash_closures.calendar.is_operational',return_value=True):
   c2=self.cmd('create',as_user=CASHIER,identity={'business_date':'2026-10-17','register_id':self.reg,'shift_name':'Otro día'},data=filled(),bag_id=self.bag()['id']);c2=self.cmd('submit',c2,as_user=CASHIER,delivered=True)
  b=self.batch([self.item(c1),self.item(c2)]);b=self.op('batch-confirm',id=b['id'],version=b['version'])
  self.assertEqual({r['business_date'] for r in b['items']},{'2026-10-16','2026-10-17'})
  s=cr.overview(self.store,{'start':['2026-10-17'],'end':['2026-10-17']},PARTNER)['summary']
  self.assertEqual(s['by_closure_date']['delivered']['value'],10000);self.assertEqual(s['by_count_date']['value'],20000)
  self.assertEqual({r['closure_date'] for r in s['counts_in_period']},{'2026-10-16','2026-10-17'})
 def test_resolution_note_optional_attachment_review_and_immutable_history(self):
  c=self.cmd('count',self.delivered(),verified_amount=9900,reason='Faltan 100');d=cr.get_detail(self.store,c['id'],PARTNER)
  with self.assertRaises(DomainError):self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='accept',note='Sin propuesta',proposal_id=1)
  d=self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='propose',note='Faltante reconocido; no se inventa ingreso')
  d=self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='accept',proposal_id=d['resolution_events'][-1]['id'],note='Evidencia revisada')
  self.assertEqual(d['resolution_state'],'resolved');self.assertEqual(d['bag']['verified_difference'],-100);self.assertEqual(d['status'],'pending')
  with self.store.connect() as db:
   for table in ('cr_difference_events','cr_counts'):
    for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET actor=\'x\''):
     with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  d=self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='reopen',note='Nueva evidencia pendiente')
  self.assertEqual(d['resolution_state'],'open');self.assertEqual(len(d['resolution_events']),3)
 def test_method_totals_missing_tips_cash_guard_and_cross_method_differences(self):
  c=self.delivered();d=cr.get_detail(self.store,c['id'],PARTNER)
  self.assertIsNone(d['payment_comparison']['totals']['toteat_total'])
  methods=[dict(key=k,sales=0,tip=0) for k in cr.calc.METHODS];methods[0].update(sales=26600,tip=1000);methods[1]['sales']=100
  with self.assertRaises(DomainError):self.op('methods',closure_id=c['id'],fingerprint=d['fingerprint'],reference='QA',methods=methods,cash_basis='unknown')
  d=self.op('methods',closure_id=c['id'],fingerprint=d['fingerprint'],reference='Reporte Toteat QA: caja, fecha y turno verificados',methods=methods,cash_basis='net_sales_before_movements')
  t=d['payment_comparison']['totals'];self.assertEqual(t['difference'],0);self.assertEqual(t['different_methods'],2);self.assertEqual(t['absolute_differences'],200);self.assertIsNone(d['calculation']['expected'])
  methods[2]['tip']=None;d=self.op('methods',closure_id=c['id'],fingerprint=d['fingerprint'],reference='Evidencia incompleta QA',methods=methods,cash_basis='net_sales_before_movements')
  self.assertIsNone(d['payment_comparison']['totals']['difference']);self.assertEqual(d['payment_comparison']['totals']['pending_methods'],1)
  changed=filled();changed['note']='Cambio';self.cmd('save',c,data=changed,reason='Corrección con revisión posterior')
  self.assertFalse(cr.get_detail(self.store,c['id'],PARTNER)['payment_comparison']['source']['valid'])
 def test_legacy_bag_counts_reuse_original_without_creating_closure(self):
  b=self.bag();bags.execute(self.store,'complete',dict(request_id=uuid4().hex,confirmed=True,version=b['version'],business_date='2026-10-16',register_name='Caja existente QA',shift_name='Mañana',responsible='Cajera QA',counted=20000,retained=10000),CASHIER,ORIGIN,b['id'])
  item=cr.lookup(self.store,b['id'],PARTNER);self.assertTrue(item['eligible']);self.assertTrue(item['legacy'])
  batch=self.batch([{k:item[k] for k in ('bag_id','bag_version','closure_version')}|dict(amount=10000,note='')]);batch=self.op('batch-confirm',id=batch['id'],version=batch['version'])
  self.assertEqual(batch['state'],'confirmed');self.assertEqual(len(cl.listing(self.store,PARTNER)['closures']),0)
  self.assertTrue(cr.lookup(self.store,b['id'],PARTNER)['already_counted'])
 def test_pdf_no_writes_and_preserves_summary_context(self):
  from erp.cash_review_pdf import render
  c=self.delivered();self.cmd('count',c,verified_amount=10000);data=self.dashboard();before=self.snapshot()
  pdf=render(data);self.assertEqual(pdf,render(data));text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages)
  self.assertIn('Por fecha comercial del cierre',text);self.assertIn('Por fecha de contabilización',text);self.assertIn('10.000',text)
  self.assertEqual(before,self.snapshot());self.assertGreater(PdfReader(BytesIO(pdf)).pages[0].mediabox.width,PdfReader(BytesIO(pdf)).pages[0].mediabox.height)
 def test_schema_is_additive_idempotent_and_confirmed_batch_frozen(self):
  b=self.batch([self.item(self.delivered())]);b=self.op('batch-confirm',id=b['id'],version=b['version']);before=self.snapshot();cl.initialize(self.store);self.assertEqual(before,self.snapshot())
  with self.store.connect() as db:
   for sql in ("UPDATE cr_batches SET name='changed'","DELETE FROM cr_batch_items","UPDATE cr_batch_items SET amount=1","DELETE FROM cr_batch_versions"):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
 def test_correction_preserves_frozen_batch_and_confirmed_count_identity(self):
  c=self.delivered();b=self.batch([self.item(c)]);b=self.op('batch-confirm',id=b['id'],version=b['version']);c=cl.view(self.store,c['id'],PARTNER)
  old=dict(c['bag']);d=filled();d['cash']['100']=5
  c=self.cmd('save',c,data=d,reason='Declaración corregida');c=self.cmd('submit',c,reason='Presentación corregida')
  self.assertEqual(cr.batch_view(self.store,b['id'],PARTNER),b)
  self.assertEqual(c['bag']['verified_amount'],old['verified_amount']);self.assertEqual(c['bag']['verified_at'],old['verified_at']);self.assertEqual(c['bag']['verified_by'],old['verified_by'])
  self.assertEqual(c['bag']['verified_difference'],-100);self.assertTrue(cr.lookup(self.store,c['bag_id'],PARTNER)['already_counted'])
 def test_invalid_identifiers_are_domain_errors_and_cash_missing_is_not_zero(self):
  b=self.op('batch-create',name='QA')
  for value in ({},[],None,'not-a-bag'):
   with self.assertRaises(DomainError):self.op('batch-save',id=b['id'],version=b['version'],items=[dict(bag_id=value)])
  c=self.create();p=cr.get_detail(self.store,c['id'],PARTNER)['payment_comparison']
  self.assertIsNone(p['totals']['absolute_differences']);self.assertIsNone(p['totals']['difference'])
 def test_summary_keeps_stages_separate_and_latest_presentation_while_draft(self):
  c=self.delivered();c=self.cmd('count',c,verified_amount=10000);d=filled();d['cash']['100']=5
  self.cmd('save',c,data=d,reason='Corrección no presentada')
  s=self.dashboard()['summary']['by_closure_date']
  self.assertEqual(s['declared']['value'],84900);self.assertEqual(s['prepared']['value'],10000);self.assertEqual(s['delivered']['value'],10000);self.assertEqual(s['counted']['value'],10000);self.assertEqual(s['retained']['value'],74900)
 def test_new_declaration_reopens_review_without_rewriting_old_resolution(self):
  c=self.cmd('count',self.delivered(),verified_amount=9900,reason='Faltante QA');d=cr.get_detail(self.store,c['id'],PARTNER)
  d=self.op('review',closure_id=c['id'],fingerprint=d['fingerprint'],note='Primera revisión QA')
  d=self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='propose',note='Propuesta original QA')
  d=self.op('difference',closure_id=c['id'],fingerprint=d['fingerprint'],kind='accept',proposal_id=d['resolution_events'][-1]['id'],note='Revisión original QA');history=deepcopy(d['resolution_events'])
  value=filled();value['cash']['100']=5;c=self.cmd('save',c,data=value,reason='Declaración corregida QA');c=self.cmd('submit',c,reason='Presentación corregida QA')
  new=cr.get_detail(self.store,c['id'],PARTNER)
  self.assertEqual(new['resolution_events'],history);self.assertEqual(new['resolution_state'],'open');self.assertFalse(new['reviewed']);self.assertEqual(new['bag']['verified_difference'],-200)
  self.assertEqual(new['review_history'][0]['note'],'Primera revisión QA');self.assertNotEqual(new['fingerprint'],history[-1]['fingerprint'])
 def test_correction_and_batch_confirmation_serialize_without_lost_count(self):
  c=self.delivered();b=self.batch([self.item(c)]);value=filled();value['cash']['100']=5
  def work(action):
   try:
    if action=='count':self.op('batch-confirm',id=b['id'],version=b['version'])
    else:self.cmd('save',c,data=value,reason='Corrección concurrente QA')
    return action
   except DomainError as e:return e.status
  with ThreadPoolExecutor(2) as pool:out=list(pool.map(work,['count','correct']))
  self.assertEqual(out.count(409),1)
  final=cl.view(self.store,c['id'],PARTNER)
  if 'count' in out:self.assertEqual(final['bag']['verified_amount'],10000);self.assertEqual(final['status'],'pending')
  else:self.assertIsNone(final['bag']['verified_amount']);self.assertEqual(final['status'],'draft')
 def test_offsetting_payment_differences_block_reconciliation_and_cash_expected(self):
  c=base.ClosureTests.review(self,self.create(filled()));c=self.cmd('submit',c,as_user=CASHIER)
  self.assertEqual(c['calculation']['expected'],84900);self.assertTrue(c['calculation']['can_reconcile'])
  d=cr.get_detail(self.store,c['id'],PARTNER);methods=[dict(key=k,sales=0,tip=0) for k in cr.calc.METHODS];methods[0].update(sales=26600,tip=1000);methods[1]['sales']=100
  self.op('methods',closure_id=c['id'],fingerprint=d['fingerprint'],reference='Reporte independiente QA',methods=methods,cash_basis='net_sales_before_movements')
  c=cl.view(self.store,c['id'],PARTNER);self.assertIsNone(c['calculation']['expected']);self.assertFalse(c['calculation']['can_reconcile'])
  self.assertEqual(c['payment_comparison']['totals']['difference'],0)
  with self.assertRaises(DomainError):self.cmd('reconcile',c,reason='No permitir compensación entre medios')
