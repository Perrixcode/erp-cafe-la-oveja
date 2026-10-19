import test_cash_closures as base
from erp import cash_closures as cl,cash_control as cc,cash_closure_calc as calc,cash_bags as bags
from erp.domain import DomainError
from erp.store import Store
from uuid import uuid4
from pathlib import Path
import base64,json,sqlite3,unittest

class ClosureExtraTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp
 cmd=base.ClosureTests.cmd
 bag=base.ClosureTests.bag
 create=base.ClosureTests.create
 review=base.ClosureTests.review
 def cc(self,action,**kw):
  with self.store.connect() as db:v=db.execute('SELECT version FROM cc_settings').fetchone()[0]
  return cc.execute(self.store,action,dict(request_id=uuid4().hex,confirmed=True,version=v,**kw),base.PARTNER)
 def payment(self,amount):
  with self.store.connect() as db:
   db.execute("INSERT INTO ap_suppliers(id,name,created_at,updated_at) VALUES(1,'Proveedor QA','QA','QA')")
   db.execute("INSERT INTO ap_account_entries(id,supplier_id,kind,amount,currency,occurred_on,method,actor,created_at) VALUES(1,1,'payment',?,'CLP','2026-10-16','cash','QA','QA')",(amount,))
 def test_transfer_custody_supplier_global_conserved_no_double_withdrawal(self):
  box=self.cc('location',name='Caja con ubicación',kind='register')['id'];custody=self.cc('location',name='Custodia Socio QA',kind='custody')['id'];self.reg=self.cmd('register',name='CAJA VINCULADA',location_id=box)['register_id']
  e=self.cc('movement',kind='transfer',amount=1000,from_id=box,to_id=custody,occurred_at='2026-10-16T17:19:30Z',reference='Retiro 1 QA',note='Custodia para proveedor')
  d=base.filled();m=base.movement(cash_event_id=e['id']);m.update(kind='custody',source='erp_event',occurred_at='2026-10-16T17:19:30+00:00',reference='Retiro 1 QA',destination='Socio QA');d['movements']=[m];c=self.cmd('submit',self.create(d),as_user=base.CASHIER);self.assertEqual(c['calculation']['C'],9000)
  p=self.cc('movement',kind='expense',amount=800,from_id=custody,to_id=None,occurred_at='2026-10-16T17:21:00Z',reference='Proveedor 1 QA',note='Pagado desde custodia');self.payment(800);self.cc('payment-link',event_id=p['id'],payment_source='ap_account_entries',payment_id=1,amount=800)
  v=cc.overview(self.store,{'start':['2026-10-16'],'end':['2026-10-16']});self.assertEqual(v['ledger']['net_recorded']['value'],-800);self.assertEqual(cl.view(self.store,c['id'],base.PARTNER)['calculation']['C'],9000)
  bad=base.movement(cash_event_id=p['id']);bad.update(kind='supplier',amount=800,source='erp_event',occurred_at='2026-10-16T17:21:00Z',payment_source='ap_account_entries',payment_id=1);d['movements'].append(bad)
  with self.assertRaises(DomainError):self.cmd('save',c,data=d,reason='Intento de descontar dos veces')
 def test_payment_alias_and_void_invalidates_review(self):
  box=self.cc('location',name='Caja pago QA',kind='register')['id'];self.reg=self.cmd('register',name='Caja pago vinculada',location_id=box)['register_id'];self.payment(1000)
  ev=self.cc('movement',kind='expense',amount=1000,from_id=box,to_id=None,occurred_at='2026-10-16T17:00:00Z',reference='Pago existente QA',note='Proveedor');self.cc('payment-link',event_id=ev['id'],payment_source='ap_account_entries',payment_id=1,amount=1000)
  d=base.filled();d['opening']['amount']+=1000;m=base.movement(cash_event_id=ev['id']);m.update(source='erp_event',occurred_at='2026-10-16T17:00:00Z');d['movements']=[m];c=self.review(self.create(d));c=self.cmd('submit',c,as_user=base.CASHIER);c=self.cmd('reconcile',c,reason='Revisado QA')
  other=base.movement(payment_source='ap_account_entries',payment_id=1);d['movements'].append(other)
  with self.assertRaises(DomainError):self.cmd('save',c,data=d,reason='Duplicado QA')
  with self.store.connect() as db:db.execute("UPDATE ap_account_entries SET voided_at='QA' WHERE id=1")
  current=cl.view(self.store,c['id'],base.PARTNER);self.assertEqual(current['status'],'pending');self.assertIsNone(current['calculation']['expected'])
 def test_generic_bank_contrast_requires_verified_scope(self):
  d=base.filled();d['methods'][1].update(used=True,sales=50000,tip=5000,comparison={'kind':'terminal','reference':'Lote terminal QA','amount':54900});v=calc.calculate(calc.normalize(d));self.assertEqual(v['methods'][1]['total'],55000);self.assertIsNone(v['methods'][1]['independent_difference']);self.assertIsNone(v['methods'][1]['bank_settlement']);self.assertEqual(v['all_methods_total'],82700)
 def test_migration_restore_preprinted_ids_and_append_only(self):
  from erp.cash_bags_import import import_manifest
  with self.store.connect() as db:
   for first,last in [(1,20),(21,40)]:import_manifest(db,json.loads((Path(bags.__file__).parent/'data'/f'cash-bags-preprinted-{first:06d}-{last:06d}.json').read_text()),'QA')
   before=[tuple(r) for r in db.execute('SELECT * FROM cash_bags ORDER BY number')]
  cl.initialize(self.store);cl.initialize(self.store)
  with self.store.connect() as db:self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM cash_bags ORDER BY number')],before);self.assertEqual(db.execute('SELECT next_number FROM cash_bag_sequence').fetchone()[0],41)
  c=self.create(base.filled(),bag=False,bag_id=before[0][0]);c=self.cmd('submit',c,as_user=base.CASHIER);restored=self.store.path.with_name('restored.sqlite3')
  with self.store.connect() as db:
   with sqlite3.connect(restored) as out:db.backup(out)
  rs=Store(restored);self.assertEqual(cl.view(rs,c['id'],base.PARTNER),cl.view(self.store,c['id'],base.PARTNER))
  with rs.connect() as db:
   self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM cl_versions')
 def test_attachments_dedup_and_foreign_link_rejected(self):
  c=self.create();body=dict(file_name='QA.pdf',file_type='application/pdf',file_data=base64.b64encode(b'%PDF-QA').decode());c=self.cmd('attach',c,as_user=base.CASHIER,**body);c=self.cmd('attach',c,as_user=base.CASHIER,**body);self.assertEqual(len(c['attachments']),1)
  d=base.filled();d['movements']=[base.movement(attachment_id=c['attachments'][0]['id'])];other=self.create(shift='Otro')
  with self.assertRaises(DomainError):self.cmd('save',other,data=d)
