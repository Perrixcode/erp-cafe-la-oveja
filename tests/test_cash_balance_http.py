import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4
import unittest
import test_cash_control_http as base

class BalanceHTTPTests(unittest.TestCase):
    setUp=base.CashControlHTTPTests.setUp;stop=base.CashControlHTTPTests.stop
    def command(self,action,body,opener=None,headers=None):
        req=Request(self.url+'/api/cash-control/'+action,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1',**(headers or {})})
        with (opener or self.partner).open(req) as response:return json.load(response)
    def summary(self):
        with self.partner.open(self.url+'/api/cash-control/summary?start=2026-10-16&end=2026-10-16') as response:return json.load(response)
    def test_origin_permissions_confirmation_retry_and_no_cash_event(self):
        before=self.summary();self.assertIsNone(before['expected_range']['origin'])
        body=dict(request_id=uuid4().hex,version=before['version'],confirmed=True,cutoff_day='2026-10-16',amount=100,evidence='Punto de partida FICTICIO de prueba')
        for opener in (self.cashier,self.production):
            with self.assertRaises(HTTPError) as err:self.command('balance-origin',body,opener)
            self.assertEqual(err.exception.code,403);err.exception.close()
        with self.assertRaises(HTTPError) as err:urlopen(Request(self.url+'/api/cash-control/balance-origin',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-ERP-Local':'1'}))
        self.assertEqual(err.exception.code,401);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.command('balance-origin',body,headers={'Origin':'https://foreign.invalid'})
        self.assertEqual(err.exception.code,403);err.exception.close()
        with self.assertRaises(HTTPError) as err:self.command('balance-origin',dict(body,confirmed=False))
        self.assertEqual(err.exception.code,400);err.exception.close()
        saved=self.command('balance-origin',body);self.assertEqual(self.command('balance-origin',body),saved)
        after=self.summary();self.assertEqual(after['expected_range']['origin']['amount'],100);self.assertIsNone(after['expected_range']['final'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cc_cash_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cc_balance_origins').fetchone()[0],1)
        with self.assertRaises(HTTPError) as err:self.command('balance-origin',dict(body,request_id=uuid4().hex,amount=200))
        self.assertEqual(err.exception.code,409);err.exception.close()

if __name__=='__main__':unittest.main()
