from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import json,sqlite3,unittest
from erp import cash_bags as bags,cash_control as cc,cash_control_sources as sources,cash_closures as cl,cash_closure_calc as calc
from erp.domain import DomainError
from erp.store import Store

PARTNER={'role':'partner','username':'socio-qa','name':'Socio QA'}
CASHIER={'role':'cashier','username':'cajera-qa','name':'Cajera QA'}
OTHER={'role':'cashier','username':'otra-qa','name':'Otra QA'}
ORIGIN='http://localhost:8888'
def filled():
 d=calc.empty();d['cash']={str(v):0 for v in calc.DENOMINATIONS};d['cash'].update({'20000':3,'10000':2,'2000':2,'500':1,'100':4})
 d['retained']=dict(d['cash']);d['retained']['10000']=1
 d.update(counted_at='2026-10-16T17:19:00Z',sealed_at='2026-10-16T17:20:00Z',movements_complete=True)
 for m in d['methods']:m.update(used=False)
 d['methods'][0].update(used=True,sales=26700,tip=1000)
 d['opening']={'amount':57200,'as_of':'2026-10-16T11:46:59Z','reference':'Conteo físico apertura QA'}
 d['cash_source']={'type':'manual_report','reference':'Reporte Efectivo en caja QA','basis':'net_sales_before_movements','read_id':None}
 return d
def movement(**kw):
 return dict(id=uuid4().hex,kind='expense',direction='out',amount=1000,occurred_at='2026-10-16T17:19:30Z',origin='Caja QA',destination='Exterior QA',reason='Gasto documentado QA',responsible='Cajera QA',source='manual_report',reference='QA-'+uuid4().hex,**kw)

class ClosureTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=Store(Path(self.temp.name)/'qa.sqlite3')
  bags.initialize(self.store);cc.initialize(self.store);cl.initialize(self.store)
  clock=patch('erp.cash_closures.stamp',return_value='2026-10-17T04:00:00+00:00');clock.start();self.addCleanup(clock.stop)
  self.reg=self.cmd('register',name='CAJA 1',provider_register_id='1')['register_id']
 def cmd(self,action,c=None,as_user=PARTNER,**kw):
  deliver=kw.pop('delivered',False) if action=='submit' else False
  if action=='submit':kw['review_token']=cl.submission_review(self.store,c['id'],as_user)['review_token']
  result=cl.execute(self.store,action,dict(request_id=uuid4().hex,confirmed=True,**({'version':c['version']} if c else {}),**kw),as_user,c['id'] if c else None)
  return self.cmd('deliver',result,as_user=as_user) if deliver else result
 def bag(self):
  batch=bags.execute(self.store,'create',{'request_id':uuid4().hex,'confirmed':True,'quantity':1},PARTNER,ORIGIN)
  return bags.batch_view(self.store,batch['batch_id'])['bags'][0]
 def create(self,data=None,bag=True,**kw):
  return self.cmd('create',as_user=CASHIER,identity={'business_date':'2026-10-16','register_id':self.reg,'shift_name':kw.pop('shift','Mañana')},data=data or calc.empty(),**({'bag_id':self.bag()['id']} if bag else {}),**kw)
 def review(self,c,**kw):return self.cmd('review',c,opening_verified=True,payments_verified=True,movements_verified=True,evidence='Reporte completo QA contrastado manualmente',reason='Cobertura y apertura revisadas QA',**kw)
 def snapshot(self,tables=None):
  with self.store.connect() as db:
   names=tables or [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
   return {n:[tuple(r) for r in db.execute('SELECT * FROM '+n)] for n in names}
 def test_empty_is_missing_draft_allowed_submit_blocked(self):
  c=self.create();self.assertEqual(c['status'],'draft');self.assertIsNone(c['calculation']['A']);self.assertIsNone(c['calculation']['expected'])
  with self.assertRaises(DomainError):self.cmd('submit',c,as_user=CASHIER)
  self.assertEqual(self.snapshot(['cc_cash_events'])['cc_cash_events'],[])
 def test_cashier_isolation_and_partner_scoped_actions(self):
  c=self.create();self.assertEqual(len(cl.listing(self.store,OTHER)['closures']),0)
  for action in ('receive','count','review','reconcile'):
   with self.assertRaises(DomainError) as e:self.cmd(action,c,as_user=CASHIER)
   self.assertEqual(e.exception.status,403)
  with self.assertRaises(DomainError):cl.view(self.store,c['id'],OTHER)
  with self.assertRaises(DomainError):cl.listing(self.store,{'role':'production','username':'kitchen'})
 def test_unique_box_day_shift_and_qr_single_use(self):
  c=self.create()
  with self.assertRaises(sqlite3.IntegrityError):self.create(shift='  MAÑANA  ')
  with self.assertRaises(DomainError):self.create(shift='Tarde',bag=False,bag_id=c['bag_id'])
  with self.assertRaises(DomainError):self.cmd('save',c,as_user=CASHIER,data=calc.empty(),bag_id=self.bag()['id'])
  with self.assertRaises(DomainError):bags.execute(self.store,'complete',{'request_id':uuid4().hex,'confirmed':True,'version':1},PARTNER,ORIGIN,c['bag_id'])
 def test_cajera_numbers_original_last_editor_no_duplicate_capture(self):
  c=self.create(filled());body=dict(request_id=uuid4().hex,confirmed=True,version=c['version'],review_token=cl.submission_review(self.store,c['id'],CASHIER)['review_token'])
  submitted=cl.execute(self.store,'submit',body,CASHIER,c['id']);again=cl.execute(self.store,'submit',body,CASHIER,c['id'])
  self.assertEqual(submitted,again);self.assertEqual(submitted['status'],'pending')
  self.assertEqual([submitted['calculation'][k] for k in ('A','B','C')],[84900,74900,10000])
  b=bags.bag_view(self.store,c['bag_id']);self.assertEqual(b['bag_amount'],10000)
  same=self.cmd('save',submitted,data=filled(),reason='Revisión sin cambio')
  self.assertTrue(same['unchanged']);self.assertEqual(same['responsible']['name'],'Cajera QA')
  corrected=deepcopy(filled());corrected['note']='Revisado por Socio QA'
  edited=self.cmd('save',same,data=corrected,reason='Aclaración documentada')
  self.assertEqual(edited['last_editor'],'erp:socio-qa');self.assertEqual(edited['creator_key'],'erp:cajera-qa');self.assertEqual(edited['responsible']['name'],'Cajera QA')
  self.assertEqual(bags.bag_view(self.store,c['bag_id'])['bag_amount'],10000)
 def test_owner_cannot_correct_presented_or_change_responsible(self):
  c=self.cmd('submit',self.create(filled()),as_user=CASHIER)
  with self.assertRaises(DomainError):self.cmd('save',c,as_user=CASHIER,data=filled(),reason='Intento')
  with self.assertRaises(DomainError):self.cmd('save',c,data=filled(),responsible_cashier_id='other',reason='Intento')
 def test_revision_and_retry_conflict_and_concurrent_submit(self):
  c=self.create(filled());body={'request_id':uuid4().hex,'version':c['version'],'confirmed':True,'review_token':cl.submission_review(self.store,c['id'],CASHIER)['review_token']}
  with ThreadPoolExecutor(2) as pool:out=list(pool.map(lambda _:cl.execute(self.store,'submit',body,CASHIER,c['id']),range(2)))
  self.assertEqual(out[0],out[1])
  with self.assertRaises(DomainError):cl.execute(self.store,'submit',dict(body,delivered=True),CASHIER,c['id'])
  with self.assertRaises(DomainError):self.cmd('save',c,data=filled())
  with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM cash_bag_events WHERE action='complete'").fetchone()[0],1)
 def test_denominations_nonnegative_integral_B_subset_and_null_zero(self):
  for bad in (-1,1.2,True):
   d=filled();d['cash']['20000']=bad
   with self.assertRaises(DomainError):calc.normalize(d)
  d=filled();d['retained']['5000']=1
  with self.assertRaises(DomainError):calc.normalize(d)
  d=filled();d['cash']['10']=None
  self.assertIsNone(calc.calculate(calc.normalize(d))['A'])
  d['cash']['10']=0;self.assertEqual(calc.calculate(calc.normalize(d))['A'],84900)
 def test_after_count_adjustment_changes_C_preserves_A_and_dashboard(self):
  d=filled();d['movements']=[movement()];c=self.cmd('submit',self.create(d),as_user=CASHIER)
  self.assertEqual(c['calculation']['A'],84900);self.assertEqual(c['calculation']['C'],9000)
  b=bags.bag_view(self.store,c['bag_id']);self.assertEqual(b['counted'],84900);self.assertEqual(b['cash_at_seal'],83900);self.assertEqual(b['bag_amount'],9000)
  v=cc.overview(self.store,{'start':['2026-10-16'],'end':['2026-10-16'],'scope':['tests']})
  self.assertEqual(v['metrics']['declared']['value'],9000);self.assertEqual(v['rows'][0]['counted'],84900)
 def test_expected_requires_source_review_and_difference_uses_A_not_C(self):
  c=self.create(filled());self.assertIsNone(c['calculation']['expected'])
  c=self.review(c);self.assertEqual(c['calculation']['expected'],84900);self.assertEqual(c['calculation']['difference'],0)
  c=self.cmd('submit',c,as_user=CASHIER);c=self.cmd('reconcile',c,reason='A igual a esperado, fuente manual revisada')
  self.assertEqual(c['status'],'reconciled');self.assertIsNone(c['bag']['verified_amount'])
 def test_count_bag_does_not_reconcile_closure(self):
  c=self.cmd('submit',self.create(filled()),as_user=CASHIER,delivered=True)
  c=self.cmd('count',c,verified_amount=10000)
  self.assertEqual(c['delivery_status'],'counted');self.assertEqual(c['status'],'pending');self.assertIsNone(c['calculation']['expected'])
  with self.assertRaises(DomainError):self.cmd('count',c,verified_amount=10000)
 def test_amount_correction_preserves_confirmed_count_and_recalculates_difference(self):
  c=self.cmd('submit',self.create(filled()),as_user=CASHIER,delivered=True);c=self.cmd('count',c,verified_amount=10000)
  d=filled();d['cash']['100']=5
  c=self.cmd('save',c,data=d,reason='Recuento documentado: una moneda adicional')
  self.assertEqual(c['bag']['verified_amount'],10000) # correction draft has not replaced delivery
  c=self.cmd('submit',c,reason='Presento corrección con evidencia')
  self.assertEqual(c['bag']['bag_amount'],10100);self.assertEqual(c['bag']['verified_amount'],10000);self.assertEqual(c['bag']['verified_difference'],-100)
  with self.assertRaises(DomainError):self.cmd('count',c,verified_amount=10100)
  self.assertTrue(any(h['calculation'].get('delivery_count',{}).get('verified_amount')==10000 for h in c['history']))
 def test_review_changes_require_explanation_to_submit_difference(self):
  d=filled();d['opening']['amount']=57000;c=self.create(d);c=self.review(c)
  self.assertEqual(c['calculation']['difference'],200)
  with self.assertRaises(DomainError):self.cmd('submit',c,as_user=CASHIER)
  d['difference_explanation']='Sobrante físico contado, pendiente de origen';c=self.cmd('save',c,as_user=CASHIER,data=d)
  c=self.review(c);c=self.cmd('submit',c,as_user=CASHIER)
  self.assertEqual(c['status'],'pending')
  with self.assertRaises(DomainError):self.cmd('reconcile',c,reason='No corresponde ocultar diferencia')
 def test_review_invalidated_by_identity_change(self):
  c=self.review(self.create(filled()))
  c=self.cmd('save',c,data=filled(),identity={'business_date':'2026-10-16','register_id':self.reg,'shift_name':'Tarde'})
  self.assertIsNone(c['calculation']['expected'])
 def test_business_day_separate_from_next_midnight_and_external_id(self):
  d=filled();d.update(counted_at='2026-10-17T03:05:00Z',sealed_at='2026-10-17T03:07:00Z',external_id='TOTEAT-EXTERNO-QA')
  c=self.create(d);self.assertEqual(c['business_date'],'2026-10-16');self.assertTrue(c['data']['counted_at'].startswith('2026-10-17'))
  with self.assertRaises(DomainError):self.cmd('save',c,data={**d,'counted_at':'2026-10-17T03:05'})
 def test_tips_total_unused_methods_no_bank_settlement(self):
  v=calc.calculate(calc.normalize(filled()));self.assertEqual(v['methods'][0]['total'],27700)
  self.assertEqual(v['methods'][1]['total'],0);self.assertIsNone(v['methods'][1]['bank_settlement'])
  d=filled();d['methods'][1]['used']=None
  self.assertIsNone(calc.calculate(calc.normalize(d))['methods'][1]['total'])
  d=filled();d['methods'][1]['sales']=1
  with self.assertRaises(DomainError):calc.normalize(d)
 def test_report_after_movements_never_subtracts_again_or_reconciles(self):
  d=filled();d['cash_source']['basis']='report_after_movements';c=self.review(self.create(d))
  self.assertIsNone(c['calculation']['expected']);self.assertFalse(c['calculation']['can_reconcile'])
 def test_toteat_read_with_adjusted_or_unknown_basis_stays_pending(self):
  payload={'ok':True,'data':[{'orderId':'basis-qa','paymentId':'1','registerId':'1','registerName':'CAJA 1','change':2300,'paymentForms':[{'id':1000,'amount':30000}]}]}
  reading=sources.record(self.store,'2026-10-16','sales',payload,{'restaurant_id':'1','local_id':'1'})
  for basis in ('report_after_movements','unknown'):
   with self.subTest(basis=basis):
    d=filled();d['cash_source']={'type':'toteat_sales','reference':None,'basis':basis,'read_id':reading['id']}
    c=self.review(self.create(d,shift=basis))
    self.assertIsNone(c['calculation']['expected']);self.assertIsNone(c['calculation']['difference'])
    self.assertFalse(c['calculation']['can_reconcile']);self.assertEqual(c['calculation']['coverage'],'incomplete')
 def test_source_net_provisional_until_exact_identity_and_stale_source_invalidates(self):
  payload={'ok':True,'data':[{'orderId':'x','paymentId':'1','registerId':'1','registerName':'CAJA 1','dateClosed':'2026-10-16T12:00:00Z','change':2300,'paymentForms':[{'id':1000,'amount':30000}]}]}
  collection={'ok':True,'data':{'date':'2026-10-16','shifts':{'1':{'name':'Mañana','registers':{'1':[{'openedDate':'2026-10-16T11:00:00Z','closedDate':'2026-10-16T18:00:00Z','paymentMethods':[{'paymentMethodID':1000,'amount':84900}]}]}}}}}
  sources.record(self.store,'2026-10-16','collection',collection,{'restaurant_id':'1','local_id':'1'})
  reading=sources.record(self.store,'2026-10-16','sales',payload,{'restaurant_id':'1','local_id':'1'})
  d=filled();d['cash_source']={'type':'toteat_sales','reference':None,'basis':'net_sales_before_movements','read_id':reading['id']}
  c=self.create(d);session=cl.options(self.store,c['id'],CASHIER)['sessions'][0]['session_key']
  c=self.cmd('save',c,data=d,identity={'business_date':'2026-10-16','register_id':self.reg,'shift_name':'Mañana','external_session_key':session})
  c=self.review(c);self.assertEqual(c['calculation']['source_reference']['net'],27700);self.assertIsNone(c['calculation']['expected'])
  sources.record(self.store,'2026-10-16','sales',scope={'restaurant_id':'1','local_id':'1'},error='Origen temporalmente sin cobertura')
  self.assertIsNone(cl.view(self.store,c['id'],PARTNER)['calculation']['expected'])
 def test_same_reference_not_reused_in_another_closure(self):
  d=filled();d['movements']=[movement()];one=self.cmd('submit',self.create(d),as_user=CASHIER)
  two=self.create(d,shift='Tarde')
  with self.assertRaises(DomainError):self.cmd('submit',two,as_user=CASHIER)
  self.assertEqual(self.snapshot(['cc_cash_events'])['cc_cash_events'],[])
 def test_append_only_history_and_no_get_writes(self):
  c=self.create(filled());before=self.snapshot();cl.view(self.store,c['id'],PARTNER);cl.listing(self.store,PARTNER);self.assertEqual(before,self.snapshot())
  with self.store.connect() as db:
   for sql in ('DELETE FROM cl_versions','UPDATE cl_versions SET reason=\'changed\'','DELETE FROM cl_closures'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
 def test_session_revocation_rechecked_inside_transaction(self):
  before=self.snapshot()
  with self.assertRaises(DomainError):cl.execute(self.store,'create',{'request_id':uuid4().hex,'confirmed':True,'identity':{}},CASHIER,identity_check=lambda db:False)
  self.assertEqual(before,self.snapshot())
 def test_adopt_cajera_preserves_prior_two_events_and_amounts(self):
  b=self.bag();values=dict(request_id=uuid4().hex,confirmed=True,version=1,business_date='2026-10-16',register_name='1',shift_name='Viernes 16/10',responsible='Cajera QA',counted=84900,retained=74900)
  b=bags.execute(self.store,'complete',values,CASHIER,ORIGIN,b['id'])
  with self.store.connect() as db:
   legacy=dict(b,declared_by='Socio QA',version=3)
   db.execute('INSERT INTO cash_bag_events(bag_id,batch_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',(b['id'],b['batch_id'],'complete','Socio QA',bags.canonical(legacy),'2026-10-16T17:22:56+00:00'))
   db.execute('UPDATE cash_bags SET version=3,declared_by=? WHERE id=?',('Socio QA',b['id']))
  before=self.snapshot(['cash_bags','cash_bag_events','cash_bag_sequence'])
  c=self.cmd('adopt',identity={'business_date':'2026-10-16','register_id':self.reg,'shift_name':'Viernes 16/10'},bag_id=b['id'],reason='Caso aislado de Cajera')
  self.assertEqual(before,self.snapshot(['cash_bags','cash_bag_events','cash_bag_sequence']))
  self.assertEqual(c['responsible']['name'],'Cajera QA');self.assertEqual(c['last_editor'],'erp:socio-qa')
  self.assertEqual(c['bag']['counted'],84900);self.assertEqual(c['bag']['retained'],74900);self.assertEqual(c['bag']['bag_amount'],10000)
 def test_pdf_preview_has_letter_pages_and_versions(self):
  from io import BytesIO
  from pypdf import PdfReader
  from erp.cash_closure_pdf import render
  c=self.create(filled());before=self.snapshot();raw=render(c);self.assertEqual(before,self.snapshot())
  pdf=PdfReader(BytesIO(raw));self.assertTrue(all(float(p.mediabox.width)==612 and float(p.mediabox.height)==792 for p in pdf.pages))
  text='\n'.join(p.extract_text() for p in pdf.pages)
  self.assertIn('MARCHA BLANCA',text);self.assertIn('84.900',text);self.assertIn('movimientos de Toteat sigue pendiente de validar',text)

if __name__=='__main__':unittest.main()
