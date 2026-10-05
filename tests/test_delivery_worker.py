"""El histórico comparte límites y respuestas con el lector; todo usa datos ficticios."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from erp import delivery_finance as f
from erp.store import Store
from erp.toteat_inbox import Inbox,read_inbox
from scripts.delivery_worker import delivery_cycle
from scripts.toteat_worker import cooldown_seconds
from scripts.probe_toteat_comments import ProbeFailure
from scripts.toteat_sales_worker import sales_cycle
from test_delivery_finance import SCOPE,sale,payload

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'private').mkdir()
        self.store=Store(self.root/'data/erp-demo.sqlite3');self.inbox=Inbox(self.root/'private/toteat-reader.sqlite3')
        self.config={'binary':'private/fake','scope':SCOPE,'shift_start':'2026-09-01','start_after':'2026-09-01T00:00:00+00:00'}
        self.reader=Mock(return_value=payload(sale()))

    def cycle(self):
        with patch('scripts.delivery_worker.configuration',return_value=self.config):
            delivery_cycle(self.inbox,self.root,self.reader,current_day=date(2026,9,2))

    def test_disabled_module_never_reads_extra_endpoint(self):
        self.cycle();self.reader.assert_not_called()

    def test_one_pending_day_per_cycle_no_duplicates_after_restart(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,2));self.cycle()
        self.assertEqual(self.reader.call_args.args[1],['sales-one-day','20260901']);self.assertEqual(self.reader.call_count,1)
        self.reader.return_value=payload();self.cycle()
        self.assertEqual(self.reader.call_args.args[1],['sales-one-day','20260902']);self.assertEqual(self.reader.call_count,2)
        self.cycle();self.assertEqual(self.reader.call_count,2)
        v=f.view(Store(self.store.path),'2026-09');self.assertEqual(v['totals']['fee'],2700)
        self.assertEqual(read_inbox(self.inbox.path,False)['delivery_reader']['state'],'receiving')

    def test_delivery_429_pauses_all_readers_and_does_not_zero_history(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,2))
        f.apply_day(self.store,'2026-09-01',payload(sale()),SCOPE)
        failure=ProbeFailure('http_error',429);failure.retry_after=600
        self.reader.side_effect=failure;self.cycle()
        state=read_inbox(self.inbox.path,False)
        self.assertGreater(cooldown_seconds(state),590)
        self.cycle();self.assertEqual(self.reader.call_count,1)
        self.assertEqual(f.view(self.store,'2026-09')['totals']['fee'],2700)

    def test_other_readers_backoff_prevents_history_fetch(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,2))
        self.inbox.set_sales_state('__reader__',{'http_status':429,'retry_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()})
        self.cycle();self.reader.assert_not_called()

    def test_invalid_response_and_network_error_are_visible(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,2));self.reader.return_value={'ok':False,'data':[]};self.cycle()
        self.assertEqual(f.view(self.store,'2026-09')['days'][0]['state'],'error')
        self.reader.side_effect=ProbeFailure('network_error');self.cycle()
        self.assertEqual(f.view(self.store,'2026-09')['days'][1]['error'],'network_error')

    def test_normal_sales_response_reused_without_new_get(self):
        f.request_refresh(self.store,'Socio ficticio',date(2026,9,2))
        with patch('scripts.toteat_sales_worker.configuration',return_value=self.config):
            sales_cycle(self.inbox,self.root,self.reader,current_day=date(2026,9,2))
        self.assertEqual(self.reader.call_count,1)
        self.assertEqual(f.view(self.store,'2026-09')['totals']['fee'],2700)
        self.assertEqual(read_inbox(self.inbox.path,False)['sales_reader']['state'],'receiving')
