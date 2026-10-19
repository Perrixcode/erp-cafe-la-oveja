from io import BytesIO
import json,os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from erp.toteat_transport import fetch,paced_financial_open
from scripts.probe_toteat_comments import ProbeFailure


class CashTransportTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        (self.root/'toteat').write_text(json.dumps(dict(xir='123',xil='1',xiu='2',xapitoken='SYNTHETIC-ONLY-NOT-A-REAL-KEY')))
        self.patch=patch.dict(os.environ,{'CREDENTIALS_DIRECTORY':str(self.root)});self.patch.start();self.addCleanup(self.patch.stop)
    def test_exact_get_modes_reuse_scope_no_sales_in_accounting(self):
        requests=[]
        def opener(req,timeout):requests.append(req);return BytesIO(b'{"ok":true,"data":{}}')
        for mode in ['collection-one-day','accounting-one-day']:fetch([mode,'20261016'],{'restaurant_id':'123','local_id':'1'},opener)
        self.assertTrue(all(r.method=='GET' for r in requests));self.assertTrue(requests[0].full_url.startswith('https://api.toteat.com/mw/or/1.0/collection?'))
        params=parse_qs(urlsplit(requests[1].full_url).query)
        self.assertEqual(params['include_sales'],['false']);self.assertEqual(params['initial_date'],['20261016']);self.assertEqual(params['final_date'],['20261016'])
    def test_financial_budget_is_shared_and_preserves_state_between_calls(self):
        with patch.dict(os.environ,{'OVEJA_DATA_ROOT':str(self.root)}),patch('erp.toteat_transport.time.time',side_effect=[100,100,100.5,121]),patch('erp.toteat_transport.time.sleep') as sleep:
            opener=lambda request,timeout:BytesIO(b'{}')
            paced_financial_open(opener,None).close();paced_financial_open(opener,None).close()
            sleep.assert_called_once_with(20.5)
        self.assertEqual((self.root/'private/toteat-get-budget.lock').read_text(),'121')
    def test_invalid_dates_scope_and_provider_denial_fail_without_exposing_secret(self):
        for args in [['collection-one-day','20260230'],['accounting-one-day','20261016&foo=1'],['accounting-one-day','20261016','extra']]:
            with self.assertRaises(ProbeFailure):fetch(args,opener=lambda *a,**k:self.fail('Unexpected network'))
        with self.assertRaises(ProbeFailure) as err:fetch(['collection-one-day','20261016'],{'restaurant_id':'124','local_id':'1'})
        self.assertEqual(err.exception.code,'scope_mismatch')
        with self.assertRaises(ProbeFailure) as err:fetch(['accounting-one-day','20261016'],opener=lambda *a,**k:BytesIO(b'{"ok":false,"msg":{"texto":"Not Authorized","tipo":7}}'))
        self.assertEqual(err.exception.code,'provider_not_authorized');self.assertEqual(err.exception.http_status,200);self.assertNotIn('SYNTHETIC',str(err.exception))


if __name__=='__main__':unittest.main()
