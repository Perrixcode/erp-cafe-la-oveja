from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import make_server


class PartnerModuleGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.sqlite3'
        self.server = make_server(self.db, port=0, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def fingerprint(self):
        with closing(sqlite3.connect(self.db)) as db:
            return '\n'.join(db.iterdump())

    def test_client_cannot_grant_partner_permission_and_database_stays_unchanged(self):
        before = self.fingerprint()
        for path in ('/api/sos/orders', '/api/orders/1/schedule-without-payment'):
            for role in ('socio', 'cajera', 'jefa'):
                data = json.dumps({'actor': 'Socio ficticio', 'role': role, 'is_partner': True, 'payment_confirmed': False}).encode()
                request = Request(self.url + path, data=data, method='POST', headers={
                    'Content-Type': 'application/json', 'X-ERP-Local': '1', 'X-ERP-Role': role,
                    'Authorization': 'Bearer FICTICIO_NO_VALIDO', 'Cookie': 'role=socio'})
                with self.assertRaises(HTTPError) as denied: urlopen(request, timeout=3)
                with denied.exception as response:
                    self.assertEqual(response.code, 403)
                    self.assertEqual(json.load(response)['code'], 'partner_auth_required')
        self.assertEqual(self.fingerprint(), before)

    def test_sos_and_operational_board_require_login(self):
        for method in ('GET', 'PUT'):
            request = Request(self.url + '/api/sos/orders', method=method)
            with self.assertRaises(HTTPError) as denied: urlopen(request, timeout=3)
            denied.exception.close(); self.assertEqual(denied.exception.code, 403)
        with self.assertRaises(HTTPError) as denied: urlopen(self.url + '/api/board', timeout=3)
        denied.exception.close();self.assertEqual(denied.exception.code,401)
