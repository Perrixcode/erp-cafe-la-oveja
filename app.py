"""Servidor web de desarrollo, limitado a 127.0.0.1. Python estándar."""
import argparse
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from erp.demo import seed_demo
from erp.domain import DomainError, STATUSES, date_range, summarize, stock_summary, whatsapp
from erp.store import Store
from erp.catalog import production_plan

ROOT = Path(__file__).resolve().parent
BUSINESS_TIMEZONE = "America/Santiago"
MAX_BODY = 64 * 1024


def today():
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OvejaLocal/0.1"

        def send(self, status, payload, content_type="application/json; charset=utf-8"):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def check_host(self):
            port = self.server.server_address[1]
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                raise DomainError("Acceso permitido solo desde localhost.", 403)

        def body(self):
            self.check_host()
            expected = "http://" + self.headers["Host"]
            origin = self.headers.get("Origin")
            if origin and origin != expected:
                raise DomainError("Origen externo rechazado.", 403)
            if self.headers.get("X-ERP-Local") != "1":
                raise DomainError("Falta encabezado de operación local.", 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise DomainError("Se requiere application/json.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise DomainError("Tamaño no válido.") from None
            if not 0 < length <= MAX_BODY:
                raise DomainError("Solicitud vacía o demasiado grande.", 413)
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise DomainError("JSON no válido.") from None
            if not isinstance(data, dict):
                raise DomainError("Se espera un objeto JSON.")
            return data

        def dispatch(self):
            self.check_host()
            parsed = urlsplit(self.path)
            path = parsed.path
            if self.command == "GET":
                if path == "/api/health":
                    return self.send(200, {"ok": True, "demo": True, "orders_demo": store.operating_mode() == "demo", "operating_mode": store.operating_mode(), "catalog_mode": "local", "toteat_connected": False})
                if path == "/api/board":
                    query = parse_qs(parsed.query)
                    selected = query.get("date", [today().isoformat()])[0]
                    period = query.get("period", ["day"])[0]
                    start, end = date_range(selected, period, query.get("end", [None])[0])
                    scope = query.get('scope',['operations'])[0]
                    if scope not in {'operations','tests','archived'}:
                        raise DomainError('Vista de registros no válida.')
                    orders = store.list(start, end, include_archived=scope == 'archived')
                    if store.operating_mode() == 'toteat-local' or scope != 'operations':
                        orders = [o for o in orders if (not o['is_simulation'] if scope == 'operations' else o['is_simulation'] and o['simulation_archived'] == (scope == 'archived'))]
                    stock = stock_summary([] if scope != 'operations' else store.stock())
                    catalog = store.catalog()
                    with store.connect() as db:
                        seed = db.execute("SELECT value FROM metadata WHERE key='demo_seed_date'").fetchone()
                    return self.send(200, {"operating_mode":store.operating_mode(), "scope":scope, "notification_scope":store.notification_scope(), "orders": orders, "summary": summarize(orders), "stock": stock, "catalog": catalog, "production": production_plan(orders,catalog), "notifications": store.notifications(), "analytics": store.analytics(orders), "whatsapp": whatsapp(orders, start, end, stock), "start": start, "end": end, "today": today().isoformat(), "seed_date": seed[0] if seed else None, "timezone": BUSINESS_TIMEZONE, "statuses": STATUSES})
                if path == "/api/notifications":
                    return self.send(200, {"notifications":store.notifications(), "scope":store.notification_scope()})
                if path == "/api/stock":
                    return self.send(200, {"stock": stock_summary(store.stock()), "history": store.stock_history()})
                match = re.fullmatch(r"/api/orders/(\d+)/history", path)
                if match:
                    return self.send(200, store.history(int(match[1])))
                assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/notifications.js": ("notifications.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
                if path in assets:
                    name, kind = assets[path]
                    return self.send(200, (ROOT / "static" / name).read_bytes(), kind + "; charset=utf-8")
            elif self.command in {"POST", "PUT"}:
                data = self.body()
                if path == "/api/stock" and self.command == "PUT":
                    return self.send(200, store.update_stock(data.get("stock"), data.get("actor"), data.get("reason"), data.get("version")))
                if path == "/api/recipes" and self.command == "PUT":
                    return self.send(200, store.update_recipe(data.get("sku"),data.get("bases"),data.get("actor"),data.get("reason"),data.get("version")))
                if path == "/api/simulations" and self.command == "POST":
                    return self.send(201,store.create(data.get('order'),data.get('actor'),simulation=True))
                simulation = re.fullmatch(r'/api/simulations/(\d+)/(archive|restore)',path)
                if simulation and self.command == 'POST':
                    return self.send(200,store.archive_simulation(int(simulation[1]),data.get('actor'),data.get('version'),archived=simulation[2]=='archive'))
                if path == "/api/orders" and self.command == "POST":
                    return self.send(201, store.create(data.get("order"), data.get("actor")))
                match = re.fullmatch(r"/api/orders/(\d+)", path)
                if match and self.command == "PUT":
                    return self.send(200, store.update(int(match[1]), data.get("order"), data.get("actor"), data.get("reason"), data.get("version")))
                match = re.fullmatch(r"/api/orders/(\d+)/items/(\d+)/status", path)
                if match and self.command == "POST":
                    return self.send(200, store.transition(int(match[1]), int(match[2]), data.get("status"), data.get("actor"), data.get("reason"), data.get("version"), correction=data.get("correction") is True))
            raise DomainError("Ruta no encontrada.", 404)

        def safe_dispatch(self):
            try:
                self.dispatch()
            except DomainError as error:
                self.send(error.status, {"error": str(error)})
            except Exception:
                # No revelar payloads ni trazas en la respuesta del navegador.
                self.send(500, {"error": "No se pudo completar la operación local. Revisa el servidor."})
                raise

        do_GET = safe_dispatch
        do_POST = safe_dispatch
        do_PUT = safe_dispatch

        def log_message(self, _format, *_args):
            pass  # No registrar nombres, comentarios ni URLs con datos.

    return Handler


def make_server(database, port=8765, seed=True):
    store = Store(database)
    if seed:
        seed_demo(store, today())
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(store))


def main():
    parser = argparse.ArgumentParser(description="ERP Café La Oveja · prototipo local ficticio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "erp-demo.sqlite3")
    parser.add_argument("--empty", action="store_true", help="No cargar ejemplos en una base nueva")
    args = parser.parse_args()
    server = make_server(args.db, args.port, seed=not args.empty)
    print(f"ERP DEMO: http://127.0.0.1:{server.server_address[1]} · Ctrl+C para detener", flush=True)
    print(f"SQLite: {args.db.resolve()} · Solo localhost · Toteat desconectado", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
