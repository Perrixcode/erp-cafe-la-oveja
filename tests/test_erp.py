from http_test_support import authenticated_opener
"""Pruebas de negocio y HTTP con bases temporales, sin Internet."""
import copy
import json
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_server
from erp.demo import seed_demo
from erp.domain import DomainError, date_range, summarize, stock_summary, whatsapp
from erp.store import Store
from erp.catalog import CATALOG, production_plan
from erp.toteat_readonly import load_internal_example, map_real_response, read_remote, request_plan


def sample():
    data = copy.deepcopy(load_internal_example()["orders"][0])
    data.pop("day_offset")
    data.pop("time")
    data["pickup_at"] = "2026-10-05T12:00"
    return data


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "demo.sqlite3"
        self.store = Store(self.path)

    def test_payment_required(self):
        data = sample(); data["payment_confirmed"] = False
        with self.assertRaises(DomainError): self.store.create(data, "Demo")

    def test_only_demo_sources(self):
        data = sample(); data["source"] = "toteat"
        with self.assertRaises(DomainError): self.store.create(data, "Demo")

    def test_duplicate_no_double_count(self):
        data = sample(); self.store.create(data, "Demo")
        with self.assertRaises(DomainError) as error: self.store.create(data, "Demo")
        self.assertEqual(error.exception.status, 409)
        orders = self.store.list("2026-10-05", "2026-10-05")
        self.assertEqual(len(orders), 1)
        self.assertEqual(summarize(orders)["outstanding"], 1)

    def test_same_external_id_different_source_allowed(self):
        data = sample(); self.store.create(data, "Demo")
        data["source"] = "demo"; self.store.create(data, "Demo")
        self.assertEqual(len(self.store.list("2026-10-05", "2026-10-05")), 2)

    def test_full_flow_keeps_item_and_history(self):
        order = self.store.create(sample(), "Demo")
        item_id = order["items"][0]["id"]
        for status in ["marcado_solicitado", "marcado", "entregado"]:
            order = self.store.transition(order["id"], item_id, status, "Operador demo", "Prueba", order["version"])
        self.assertEqual(order["items"][0]["id"], item_id)
        self.assertEqual(len(self.store.history(order["id"])), 4)
        self.assertEqual(summarize([order])["outstanding"], 0)
        self.assertEqual(summarize([order])["metrics"]["delivered"], 1)

    def test_cannot_skip_state(self):
        order = self.store.create(sample(), "Demo")
        with self.assertRaises(DomainError): self.store.transition(order["id"], order["items"][0]["id"], "marcado", "Demo", "Prueba", order["version"])

    def test_stale_version_rejected(self):
        order = self.store.create(sample(), "Demo")
        self.store.transition(order["id"], order["items"][0]["id"], "marcado_solicitado", "Demo", "Prueba", 1)
        with self.assertRaises(DomainError) as error: self.store.transition(order["id"], order["items"][0]["id"], "marcado", "Demo", "Prueba", 1)
        self.assertEqual(error.exception.status, 409)

    def test_cancel_and_reversal(self):
        order = self.store.create(sample(), "Demo")
        order = self.store.transition(order["id"], order["items"][0]["id"], "cancelado", "Demo", "Prueba", order["version"])
        self.assertEqual(summarize([order])["outstanding"], 0)
        order = self.store.transition(order["id"], order["items"][0]["id"], "pendiente", "Demo", "Error de registro", order["version"], True)
        self.assertEqual(summarize([order])["outstanding"], 1)

    def test_correction_audited(self):
        data = sample(); order = self.store.create(data, "Demo")
        data["items"][0]["quantity"] = 4
        updated = self.store.update(order["id"], data, "Demo editor", "Cantidad corregida", 1)
        self.assertEqual(summarize([updated])["outstanding"], 4)
        event = self.store.history(order["id"])[0]
        self.assertEqual(event["before"]["items"][0]["quantity"], 1)
        self.assertEqual(event["after"]["items"][0]["quantity"], 4)

    def test_cannot_remove_saved_item(self):
        data = sample(); order = self.store.create(data, "Demo")
        data["items"][0]["source_item_id"] = "replacement"
        with self.assertRaises(DomainError): self.store.update(order["id"], data, "Demo", "No borrar", 1)

    def test_changes_to_marked_item_require_reversal_and_rollback(self):
        data = sample(); order = self.store.create(data, "Demo")
        order = self.store.transition(order["id"], order["items"][0]["id"], "marcado_solicitado", "Demo", "Prueba", 1)
        data["customer"] = "Cambio no guardado"; data["items"][0]["quantity"] = 4
        with self.assertRaises(DomainError): self.store.update(order["id"], data, "Demo", "Corregir", order["version"])
        self.assertEqual(self.store.get(order["id"])["customer"], order["customer"])

    def test_duplicate_item_ids_rejected(self):
        data = sample(); data["items"].append(copy.deepcopy(data["items"][0]))
        with self.assertRaises(DomainError): self.store.create(data, "Demo")

    def test_quantity_and_reserved_marker_validation(self):
        for quantity in [0,-1,True,1.5,"2"]:
            data = sample(); data["items"][0]["quantity"] = quantity
            with self.assertRaises(DomainError): self.store.create(data, "Demo")
        data = sample(); data["customer"] = "**nombre"
        with self.assertRaises(DomainError): self.store.create(data, "Demo")

    def test_whole_only_and_reserved_vitrine_message(self):
        data = sample()
        data['items'][0]['kind'] = 'trozo'
        with self.assertRaises(DomainError): self.store.create(data,'Demo')
        order = self.store.create(sample(),'Demo')
        stock = stock_summary([{'flavor':'Frambuesa','size':'20 personas','physical':5,'reserved':2}])
        text = whatsapp([order],'2026-10-05','2026-10-05',stock)
        self.assertIn('** reserva vitrina: 2',text)
        self.assertIn('venta entera: 3',text)
        self.assertNotIn('** = trozos',text)
        self.assertEqual(summarize([order])['outstanding'],1)

    def test_marked_only_in_summary_not_products_to_mark(self):
        order = self.store.create(sample(), "Demo")
        for status in ["marcado_solicitado", "marcado"]:
            order = self.store.transition(order["id"],order["items"][0]["id"],status,"Demo","Prueba",order["version"])
        text = whatsapp([order], "2026-10-05", "2026-10-05")
        self.assertIn("Sin productos pendientes de marcar.", text)
        self.assertEqual(summarize([order])["outstanding"], 1)

    def test_persistence_reopen(self):
        order = self.store.create(sample(), "Demo")
        reopened = Store(self.path)
        self.assertEqual(reopened.get(order["id"]), order)
        self.assertEqual(len(reopened.history(order["id"])), 1)

    def test_seed_once_even_another_day(self):
        seed_demo(self.store, date(2026,10,5)); seed_demo(self.store, date(2026,10,6))
        self.assertEqual(len(self.store.list("2026-10-05","2026-10-06")),6)
        self.assertEqual(len(self.store.list("2026-10-07","2026-10-07")),0)

    def test_week_boundaries(self):
        self.assertEqual(date_range("2026-10-04","week"), ("2026-09-28","2026-10-04"))
        with self.assertRaises(DomainError): date_range("not-a-date","day")

    def test_toteat_no_network_or_invented_mapping(self):
        self.assertIn("det=false&ic=DEMO-1", request_plan("orderstatus","DEMO-1")["url"])
        self.assertFalse(request_plan("products")["executable"])
        with self.assertRaises(RuntimeError): read_remote()
        with self.assertRaises(NotImplementedError): map_real_response({})
        with self.assertRaises(ValueError): request_plan("sale")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(Path(self.temp.name)/"test.sqlite3", 0, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener=authenticated_opener(self.server,Path(self.temp.name))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        req = Request(self.url+path, data=json.dumps(data).encode() if data else None, headers=headers or {})
        return self.opener.open(req, timeout=5)

    def test_create_and_duplicate_over_http(self):
        headers={"Content-Type":"application/json","X-ERP-Local":"1"}
        with self.request('/api/orders', {'order':sample(),'actor':'Demo'}, headers) as response:
            self.assertEqual(response.status,201)
        with self.assertRaises(HTTPError) as error: self.request('/api/orders',{'order':sample(),'actor':'Demo'},headers)
        self.assertEqual(error.exception.code,409)
        error.exception.close()
        with self.request('/api/board?date=2026-10-05') as response:
            self.assertEqual(json.load(response)["summary"]["outstanding"],1)

    def test_external_origin_and_missing_header_blocked(self):
        for headers in [{"Content-Type":"application/json"},{"Content-Type":"application/json","X-ERP-Local":"1","Origin":"https://example.com"}]:
            with self.assertRaises(HTTPError) as error: self.request('/api/orders',{'order':sample(),'actor':'Demo'},headers)
            self.assertEqual(error.exception.code,403)
            error.exception.close()

    def test_static_health_invalid_date_and_traversal(self):
        with self.request('/') as response:
            self.assertIn(b'Tortas y dulces enteros',response.read())
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        with self.request('/api/health') as response: self.assertTrue(json.load(response)['authentication_required'])
        for path,code in [('/api/board?date=no',400),('/../app.py',404)]:
            with self.assertRaises(HTTPError) as error: self.request(path)
            self.assertEqual(error.exception.code,code)
            error.exception.close()


if __name__ == '__main__':
    unittest.main()
