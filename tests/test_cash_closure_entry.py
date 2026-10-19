from copy import deepcopy
import unittest
from erp import cash_closures as cl,cash_closure_calc as calc
from erp.domain import DomainError
import test_cash_closures as base

class EntryTests(unittest.TestCase):
 setUp=base.ClosureTests.setUp;cmd=base.ClosureTests.cmd;bag=base.ClosureTests.bag;create=base.ClosureTests.create;snapshot=base.ClosureTests.snapshot
 def test_unknown_opening_source_stays_pending_but_can_save_and_present(self):
  c=self.create();d=base.filled();d['opening']={'amount':None,'as_of':None,'reference':None};d['cash_source']=calc.empty()['cash_source']
  c=self.cmd('save',c,as_user=base.CASHIER,data=d);self.assertTrue(c['calculation']['can_submit']);self.assertIsNone(c['data']['opening']['amount'])
  c=self.cmd('submit',c,as_user=base.CASHIER);self.assertEqual(c['status'],'pending');self.assertIsNone(c['calculation']['expected']);self.assertFalse(c['calculation']['can_reconcile']);self.assertEqual(c['delivery']['state'],'prepared')
 def test_partial_step_three_saves_without_inventing_zeros(self):
  d=base.filled();d['opening']={'amount':None,'as_of':None,'reference':None};d['methods'][1].update(used=None,sales=None,tip=None);d['movements_complete']=False
  c=self.create(d);c=self.cmd('save',c,as_user=base.CASHIER,data=deepcopy(d));self.assertIsNone(c['data']['methods'][1]['sales']);self.assertFalse(c['calculation']['can_submit'])
 def test_wrong_opening_order_points_to_field_and_rolls_back(self):
  c=self.create(base.filled());before=self.snapshot();d=base.filled();d['opening']['as_of']='2026-10-16T17:25:00Z'
  with self.assertRaises(calc.FieldError) as e:self.cmd('save',c,as_user=base.CASHIER,data=d)
  self.assertIn('opening.as_of',e.exception.field_errors);self.assertIn('14:19',str(e.exception));self.assertIn('no la hora del cierre',str(e.exception));self.assertEqual(self.snapshot(),before)
 def test_movement_before_opening_points_to_opening(self):
  d=base.filled();m=base.movement();m['occurred_at']='2026-10-16T11:40:00Z';d['movements']=[m]
  with self.assertRaises(calc.FieldError) as e:calc.calculate(calc.normalize(d))
  self.assertIn('opening.as_of',e.exception.field_errors);self.assertIn('movimiento 1',str(e.exception))
 def test_count_seal_and_movement_times_keep_integrity(self):
  d=base.filled();d['sealed_at']='2026-10-16T17:18:00Z'
  with self.assertRaises(calc.FieldError) as e:calc.normalize(d)
  self.assertIn('sealed_at',e.exception.field_errors)
  d=base.filled();m=base.movement();m['occurred_at']='2026-10-16T17:21:00Z';d['movements']=[m]
  with self.assertRaises(calc.FieldError) as e:calc.calculate(calc.normalize(d))
  self.assertIn('movements.0.occurred_at',e.exception.field_errors)
 def test_future_time_has_specific_field_and_chile_clock(self):
  d=calc.normalize(base.filled());d['counted_at']='2026-10-18T01:00:00+00:00'
  with self.assertRaises(calc.FieldError) as e:cl.validate_times(d)
  self.assertIn('counted_at',e.exception.field_errors);self.assertIn('17/10/2026 01:00 (Chile)',str(e.exception))
 def test_opening_may_reference_prior_verified_balance_without_old_movements(self):
  d=base.filled();d['opening']['as_of']='2026-10-15T23:00:00-03:00';c=self.create(d)
  self.assertEqual(c['data']['opening']['as_of'],'2026-10-16T02:00:00+00:00');self.assertIsNone(c['calculation']['expected'])
  d['counted_at']='2026-10-15T23:00:00-03:00'
  with self.assertRaises(calc.FieldError):self.create(d,shift='Fuera del período')
 def test_chile_and_utc_inputs_normalize_to_same_order(self):
  utc=calc.normalize(base.filled());local=base.filled();local.update(counted_at='2026-10-16T14:19:00-03:00',sealed_at='2026-10-16T14:20:00-03:00');local['opening']['as_of']='2026-10-16T08:46:59-03:00'
  self.assertEqual(calc.normalize(local),utc)
