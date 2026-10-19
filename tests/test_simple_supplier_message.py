from pathlib import Path
import importlib.util,unittest
from unittest.mock import patch
import test_payables as support
from erp import payables as ap
from erp.domain import DomainError

class SimpleMessageTests(unittest.TestCase):
 setUp=support.PayablesTests.setUp
 do=support.PayablesTests.do
 invoice_data=support.PayablesTests.invoice_data
 current=support.PayablesTests.current
 def message(self,ids=None):
  rows=ap.view(self.store)['invoices'];items=[dict(id=r['id'],version=r['version'],amount=r['planned_amount'] or r['balance']) for r in rows if ids is None or r['id'] in ids]
  return ap.message(self.store,dict(items=items,week_start='2026-10-16'))
 def test_exact_requested_example_no_due(self):
  with self.store.connect() as db:db.execute("UPDATE ap_suppliers SET name='PATRICIO GAETE' WHERE id=?",(self.supplier['id'],));db.execute("UPDATE ap_invoices SET amount=1465000,due_on='' WHERE id=?",(self.invoice['id'],))
  r=self.message();self.assertEqual(r['simple_text'],'PATRICIO GAETE - $1.465.000 - Sin vencimiento registrado');self.assertEqual(r['total'],1465000)
 def test_multiple_due_dates_same_supplier_no_invented_date_and_total(self):
  self.do('invoice',self.invoice_data(folio='43',amount=5000));self.do('invoice',self.invoice_data(folio='44',amount=7000,due_on='2026-02-03'));self.do('invoice',self.invoice_data(folio='45',amount=2000,due_on=''))
  r=self.message();self.assertEqual(r['simple_text'],'Proveedor ficticio QA - $15.000 - 30/01/2026\nProveedor ficticio QA - $7.000 - 03/02/2026\nProveedor ficticio QA - $2.000 - Sin vencimiento registrado');self.assertEqual(sum(g['amount'] for g in r['simple_groups']),r['total']);self.assertEqual(r['total'],24000)
 def test_selection_and_planned_amount_match_existing_message(self):
  extra=self.do('invoice',self.invoice_data(folio='43',amount=9999));r=self.current();self.do('schedule',dict(scheduled_on='2026-10-16',items=[dict(id=r['id'],version=r['version'],amount=3500)]))
  result=self.message([r['id']]);self.assertEqual(result['total'],3500);self.assertEqual(result['simple_text'],'Proveedor ficticio QA - $3.500 - 30/01/2026');self.assertNotIn('9.999',result['simple_text']);self.assertEqual(result['count'],1)
  self.assertEqual(result['messages'][0]['total'],3500)
 def test_detailed_message_fields_identical_to_unmodified_baseline_and_readonly(self):
  path=Path(__file__).resolve().parents[3]/'control-efectivo/source/erp/payables.py';spec=importlib.util.spec_from_file_location('baseline_message',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  row=self.current();body=dict(items=[dict(id=row['id'],version=row['version'])],week_start='2026-10-16')
  with self.store.connect() as db:before=list(db.iterdump())
  with patch.object(ap,'local_day',return_value='2026-10-16'),patch.object(old,'local_day',return_value='2026-10-16'):
   expected=old.message(self.store,body);actual=ap.message(self.store,body)
  self.assertEqual({k:actual[k] for k in expected},expected)
  with self.store.connect() as db:self.assertEqual(list(db.iterdump()),before)
 def test_stale_and_duplicate_selection_still_rejected(self):
  row=self.current()
  for items in [[dict(id=row['id'],version=0)],[dict(id=row['id'],version=row['version'])]*2]:
   with self.assertRaises(DomainError):ap.message(self.store,dict(items=items))
