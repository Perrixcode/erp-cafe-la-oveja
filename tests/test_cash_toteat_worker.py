from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import json,unittest
from erp.store import Store
from erp import cash_bags,cash_control,cash_closures,cash_toteat as ct,cash_control_sources as src
from scripts.cash_toteat_worker import sync_cycle
from scripts.probe_toteat_comments import ProbeFailure
from test_cash_toteat import collection,sales,DAY,SCOPE

class WorkerTests(unittest.TestCase):
 def setUp(self):
  t=TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);(self.root/'private').mkdir();(self.root/'data').mkdir();self.store=Store(self.root/'data/erp-demo.sqlite3')
  cash_bags.initialize(self.store);cash_control.initialize(self.store);cash_closures.initialize(self.store)
  self.cfg=patch('scripts.cash_toteat_worker.configuration',return_value=dict(transport='systemd',scope=SCOPE));self.cfg.start();self.addCleanup(self.cfg.stop)
 def reader(self,args,scope):return dict(payload=collection() if args[0]=='collection-one-day' else sales())
 def test_one_queued_day_no_financial_writes(self):
  with self.store.connect() as db:before={t:list(db.execute('SELECT * FROM '+t)) for t in ('cash_bags','cl_closures','cl_versions','cc_cash_events')}
  ct.request_refresh(self.store,DAY);calls=[]
  def reader(args,scope):calls.append(args);return self.reader(args,scope)
  result=sync_cycle(self.root,reader);self.assertEqual(result['state'],'done');self.assertEqual(calls,[['collection-one-day','20261016'],['sales-one-day','20261016']])
  with self.store.connect() as db:
   for t,rows in before.items():self.assertEqual(list(db.execute('SELECT * FROM '+t)),rows)
 def test_reuses_fresh_sales_already_collected(self):
  src.record(self.store,DAY,'sales',sales(),SCOPE);ct.request_refresh(self.store,DAY);calls=[]
  def reader(args,scope):calls.append(args[0]);return self.reader(args,scope)
  result=sync_cycle(self.root,reader);self.assertEqual(calls,['collection-one-day']);self.assertTrue(result['results'][1]['reused'])
 def test_simultaneous_collector_runs_share_lock(self):
  ct.request_refresh(self.store,DAY);entered=Event();release=Event();calls=[]
  def reader(args,scope):calls.append(args);entered.set();release.wait(3);return self.reader(args,scope)
  with ThreadPoolExecutor(max_workers=2) as pool:
   one=pool.submit(sync_cycle,self.root,reader);self.assertTrue(entered.wait(2));two=pool.submit(sync_cycle,self.root,reader);self.assertEqual(two.result()['state'],'busy');release.set();self.assertEqual(one.result()['state'],'done')
  self.assertEqual(len(calls),2)
 def test_throttling_preserves_previous_data_and_global_cooldown(self):
  src.record(self.store,DAY,'collection',collection(),SCOPE);ct.request_refresh(self.store,DAY)
  def bad(*args):raise ProbeFailure('http_error',429)
  result=sync_cycle(self.root,bad);self.assertEqual(result['state'],'error')
  self.assertEqual(sync_cycle(self.root,lambda *a:self.fail('Unexpected repeated request'))['state'],'cooldown')
  with self.store.connect() as db:
   latest,good=ct.reads(db,DAY,'collection');self.assertEqual(latest['state'],'error');self.assertIsNotNone(good)
 def test_provider_auth_does_not_create_credentials_or_expand_permissions(self):
  ct.request_refresh(self.store,DAY)
  def bad(*args):raise ProbeFailure('provider_not_authorized',200)
  result=sync_cycle(self.root,bad);self.assertEqual(result['state'],'error');self.assertEqual(len(result['results']),1)
  self.assertEqual({p.name for p in (self.root/'private').iterdir()},{'toteat-closure-sync.lock'})

 def test_weekend_refreshes_friday_without_fabricating_weekend_turn(self):
  class Saturday(datetime):
   @classmethod
   def now(cls,tz=None):return datetime(2026,10,17,16,tzinfo=timezone.utc).astimezone(tz)
  calls=[]
  def reader(args,scope):calls.append(args);return self.reader(args,scope)
  with patch('scripts.cash_toteat_worker.datetime',Saturday):result=sync_cycle(self.root,reader)
  self.assertEqual(result['business_date'],'2026-10-16');self.assertEqual([x[1] for x in calls],['20261016','20261016'])
  with self.store.connect() as db:self.assertEqual([r[0] for r in db.execute('SELECT business_date FROM cl_toteat_sync')],['2026-10-16'])
