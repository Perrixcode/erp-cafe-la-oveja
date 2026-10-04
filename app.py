"""Servidor web de desarrollo, limitado a 127.0.0.1. Python estándar."""
import argparse
import hashlib
import json
import os
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
from erp.toteat_inbox import read_inbox, get_received, reception_history
from erp.partner_auth import PartnerAuth
from erp import sos, reception
from erp.backups import BackupRunner
from erp.attention import attention_summary

ROOT = Path(__file__).resolve().parent
BUSINESS_TIMEZONE = "America/Santiago"
MAX_BODY = 64 * 1024


def today():
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()


def inbox_view(path,store=None):
    """La recepción conserva su evidencia; la bandeja muestra solo lo pendiente."""
    result = read_inbox(path)
    received = result.get('orders', [])
    linked=set()
    if store:
        with store.connect() as db:
            linked={r[0] for r in db.execute("SELECT l.source_id FROM toteat_order_links l JOIN manual_scheduling m ON m.order_id=l.order_id WHERE m.status='scheduled'")}
    pending=[];reviewed=[];scheduled=0
    for order in received:
        details=reception.context(store,order) if store else {}
        order['reception']=details
        decision=(details.get('decision') or {}).get('decision')
        alert=details.get('alert') or {}
        if alert and not alert.get('resolution'):
            pending.append(order)
        elif decision=='immediate' or (alert.get('state')=='cancelled' and alert.get('resolution')=='cancel'):
            reviewed.append(order)
        elif details.get('order_id') or order.get('scheduling',{}).get('status')=='scheduled' or order.get('key') in linked:
            if order.get('scheduling',{}).get('status')=='needs_review':pending.append(order)
            else:scheduled+=1
        else:pending.append(order)
    result.update(orders=pending,reviewed_orders=reviewed,pending_orders=len(pending),scheduled_orders=scheduled)
    return result


def handler_for(store, data_root=None, public_origin=None, transport=BaseHTTPRequestHandler):
    reader_path = ROOT/'private/toteat-reader.sqlite3' if store.path.resolve() == (ROOT/'data/erp-demo.sqlite3').resolve() else store.path.parent/'toteat-reader.sqlite3'
    if data_root:reader_path=Path(data_root)/'private/toteat-reader.sqlite3'
    if public_origin:
        parsed_origin=urlsplit(public_origin)
        if parsed_origin.scheme!='https' or not parsed_origin.hostname or parsed_origin.path or parsed_origin.query or parsed_origin.fragment or parsed_origin.username:
            raise ValueError('Configure an exact HTTPS origin without path.')
    partners = PartnerAuth(reader_path.parent/'partner-auth.sqlite3')
    class Handler(transport):
        server_version = "OvejaLocal/0.1"

        def send(self, status, payload, content_type="application/json; charset=utf-8", extra_headers=None):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def check_host(self):
            port = self.server.server_address[1]
            if public_origin:
                if self.headers.get("Host") != urlsplit(public_origin).netloc:raise DomainError("Host no autorizado.",403)
                return
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                raise DomainError("Acceso permitido solo desde localhost.", 403)

        def body(self):
            self.check_host()
            expected = public_origin or "http://" + self.headers["Host"]
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
            partner = None
            user = None
            if path.startswith('/api/toteat/reception') or path == '/api/sos' or path.startswith('/api/sos/') or re.fullmatch(r'/api/orders/\d+/(schedule-without-payment|customer)', path):
                partner = partners.session(self.headers.get('Cookie'))
                if not partner or partner['role'] != 'partner':
                    return self.send(403, {'error': 'Esta acción requiere una sesión de socio autenticada.',
                                           'code': 'partner_auth_required', 'available': False})
            public = {('GET','/api/health'),('GET','/api/partner/session'),('POST','/api/partner/login'),('POST','/api/partner/logout')}
            if path.startswith('/api/') and (self.command,path) not in public:
                user = partners.authorize(self.headers.get('Cookie'), 'read')
            if self.command == "GET":
                transfer_photo=re.fullmatch(r'/api/transfers/(\d+)/photo',path)
                if transfer_photo:
                    partners.require(self.headers.get('Cookie'))
                    from erp.transfers import read_photo
                    content,kind,extension=read_photo(os.environ.get('OVEJA_TRANSFERS_SNAPSHOT'),int(transfer_photo[1]))
                    disposition='attachment' if parse_qs(parsed.query).get('download')==['1'] else 'inline'
                    return self.send(200,content,kind,{'Content-Disposition':f'{disposition}; filename="comprobante-{transfer_photo[1]}.{extension}"'})
                if path == '/api/transfers':
                    partners.require(self.headers.get('Cookie'))
                    from erp.transfers import read_transfers
                    return self.send(200,read_transfers(os.environ.get('OVEJA_TRANSFERS_SNAPSHOT'),{k:v[0] for k,v in parse_qs(parsed.query).items()}))
                if path == '/api/toteat/reception':
                    key=parse_qs(parsed.query).get('key',[''])[0]
                    try:received=get_received(reader_path,key)
                    except (ValueError,OSError):raise DomainError('Comanda no encontrada.',404) from None
                    details=reception.context(store,received,True)
                    details['history']=sorted(details['history']+reception_history(reader_path,key),key=lambda e:e['occurred_at'],reverse=True)
                    return self.send(200,dict(received=received,**details))
                if path == '/api/sos/orders':
                    return self.send(200,{'orders':sos.list_orders(store),'reviews':sos.list_reviews(store),'sources':inbox_view(reader_path,store)['orders']})
                if path == '/api/partner/session':
                    account = partners.session(self.headers.get('Cookie'))
                    return self.send(200, {'configured':partners.configured(),'user':account,
                                          'partner':account if account and account['role']=='partner' else None})
                receipt_match = re.fullmatch(r'/api/orders/(\d+)/receipt', path)
                if receipt_match:
                    partners.authorize(self.headers.get('Cookie'),'receipt')
                    reference = store.receipt_file(int(receipt_match[1]))
                    directory = ROOT/'private/receipts' if store.path.resolve() == (ROOT/'data/erp-demo.sqlite3').resolve() else store.path.parent/'receipts'
                    if data_root:directory=Path(data_root)/'private/receipts'
                    filename = reference['filename']
                    if not re.fullmatch(r'[a-f0-9]{64}\.pdf', filename):
                        raise DomainError('Referencia de boleta no válida.',404)
                    file = directory/filename
                    if not file.is_file() or file.is_symlink() or file.stat().st_size > 8*1024*1024:
                        raise DomainError('La boleta todavía no está disponible.',404)
                    data = file.read_bytes()
                    if not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest() != reference['sha256']:
                        raise DomainError('El archivo de boleta requiere revisión.',409)
                    disposition = 'attachment' if parse_qs(parsed.query).get('download') == ['1'] else 'inline'
                    return self.send(200,data,'application/pdf',{'Content-Disposition':f'{disposition}; filename="boleta-pedido-{receipt_match[1]}.pdf"'})
                order_match = re.fullmatch(r'/api/orders/(\d+)',path)
                if order_match:
                    order=store.get(int(order_match[1]))
                    if order.get('scheduling_status')=='draft':partners.require(self.headers.get('Cookie'))
                    return self.send(200,order)
                if path == "/api/toteat":
                    return self.send(200,inbox_view(reader_path,store))
                if path == "/api/health":
                    return self.send(200, {"ok": True, "service":"oveja-erp", "catalog_mode":"local", "authentication_required":True})
                if path == "/api/board":
                    query = parse_qs(parsed.query)
                    selected = query.get("date", [today().isoformat()])[0]
                    period = query.get("period", ["day"])[0]
                    start, end = date_range(selected, period, query.get("end", [None])[0])
                    scope = query.get('scope',['operations'])[0]
                    if scope not in {'operations','tests','archived'}:
                        raise DomainError('Vista de registros no válida.')
                    orders = store.list(start, end, include_archived=scope == 'archived')
                    test_order_count = sum(o['is_simulation'] and not o['simulation_archived'] for o in orders)
                    if store.operating_mode() == 'toteat-local' or scope != 'operations':
                        orders = [o for o in orders if (not o['is_simulation'] if scope == 'operations' else o['is_simulation'] and o['simulation_archived'] == (scope == 'archived'))]
                    stock = stock_summary([] if scope != 'operations' else store.stock())
                    catalog = store.catalog()
                    with store.connect() as db:
                        seed = db.execute("SELECT value FROM metadata WHERE key='demo_seed_date'").fetchone()
                    reader=inbox_view(reader_path,store)
                    attention=attention_summary(store,reader,scope,today())
                    backup_root=ROOT if reader_path.parent==ROOT/'private' else store.path.parent
                    backup=BackupRunner(data_root or backup_root).status() if user['role']=='partner' else None
                    return self.send(200, {"attention":attention,"backup":backup,"toteat":reader, "test_order_count":test_order_count, "operating_mode":store.operating_mode(), "scope":scope, "notification_scope":store.notification_scope(), "orders": orders, "summary": summarize(orders), "stock": stock, "catalog": catalog, "production": production_plan(orders,catalog), "notifications": store.notifications(), "analytics": store.analytics(orders), "whatsapp": whatsapp(orders, start, end, stock), "start": start, "end": end, "today": today().isoformat(), "seed_date": seed[0] if seed else None, "timezone": BUSINESS_TIMEZONE, "statuses": STATUSES})
                if path == '/api/home':
                    return self.send(200,{'attention':attention_summary(store,inbox_view(reader_path,store),'operations',today()),'reader':read_inbox(reader_path,False)})
                if path == "/api/notifications":
                    return self.send(200, {"notifications":store.notifications(), "scope":store.notification_scope()})
                if path == "/api/stock":
                    return self.send(200, {"stock": stock_summary(store.stock()), "history": store.stock_history()})
                match = re.fullmatch(r"/api/orders/(\d+)/history", path)
                if match:
                    if store.get(int(match[1])).get('scheduling_status')=='draft':partners.require(self.headers.get('Cookie'))
                    return self.send(200, store.history(int(match[1])))
                assets = {"/theme.css": ("theme.css", "text/css"), "/fonts/NotoSans.ttf": ("fonts/NotoSans.ttf", "font/ttf"),"/home.js": ("home.js", "text/javascript"),"/transfers.js": ("transfers.js", "text/javascript"),"/reception.js": ("reception.js", "text/javascript"),"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/notifications.js": ("notifications.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
                if path in assets:
                    name, kind = assets[path]
                    return self.send(200, (ROOT / "static" / name).read_bytes(), kind + "; charset=utf-8")
            elif self.command in {"POST", "PUT"}:
                data = self.body()
                if user:
                    # La identidad para el historial nunca procede del formulario.
                    data['actor'] = user['username']
                    permission = None
                    if path == '/api/stock': permission = 'stock'
                    elif path == '/api/recipes': permission = 'recipe'
                    elif path.startswith('/api/simulations') or path == '/api/orders': permission = 'simulate'
                    elif re.fullmatch(r'/api/orders/\d+',path): permission = 'correct'
                    elif re.fullmatch(r'/api/orders/\d+/items/\d+/status',path):
                        permission = 'correct' if data.get('correction') is True or data.get('status') in {'pendiente','cancelado'} else {'marcado_solicitado':'request_mark','marcado':'confirm_mark','entregado':'deliver'}.get(data.get('status'),'correct')
                    if permission: partners.authorize(self.headers.get('Cookie'),permission)
                if path in ('/api/toteat/reception/schedule','/api/toteat/reception/decide','/api/toteat/reception/resolve') and self.command=='POST':
                    try:received=get_received(reader_path,data.get('source_key'))
                    except (ValueError,OSError):raise DomainError('Selecciona una comanda recibida y verificada.') from None
                    if path.endswith('/schedule'):
                        return self.send(201,reception.schedule(store,received,data.get('order'),partner,data.get('reason'),data.get('request_id'),data.get('revision')))
                    if path.endswith('/resolve'):
                        return self.send(200,reception.resolve_alert(store,received['key'],partner,data.get('reason'),data.get('version'),data.get('decision')))
                    return self.send(200,reception.decide(store,received,data.get('decision'),partner,data.get('reason'),data.get('revision')))
                if path in ('/api/sos/orders','/api/sos/reconcile') and self.command=='POST':
                    received=None
                    if data.get('source_key'):
                        try:received=get_received(reader_path,data['source_key'])
                        except (ValueError,OSError):raise DomainError('La comanda seleccionada no está en la lectura verificada.') from None
                    if path=='/api/sos/orders':
                        return self.send(201,sos.create(store,data.get('order'),partner,data.get('reason'),data.get('request_id'),received))
                    if received is None:raise DomainError('Selecciona una comanda recibida.')
                    return self.send(200,sos.resolve(store,received,data.get('order_id'),data.get('decision'),partner,data.get('reason'),data.get('version')))
                override=re.fullmatch(r'/api/orders/(\d+)/schedule-without-payment',path)
                if override and self.command=='POST':
                    return self.send(200,sos.authorize_unpaid(store,int(override[1]),partner,data.get('reason'),data.get('version')))
                if path == '/api/partner/login' and self.command == 'POST':
                    token = partners.login(data.get('username'),data.get('password'))
                    return self.send(200,{'ok':True},extra_headers={'Set-Cookie':partners.cookie(token,secure=bool(public_origin))})
                if path == '/api/partner/logout' and self.command == 'POST':
                    partners.logout(self.headers.get('Cookie'))
                    return self.send(200,{'ok':True},extra_headers={'Set-Cookie':partners.cookie('',clear=True,secure=bool(public_origin))})
                customer = re.fullmatch(r'/api/orders/(\d+)/customer',path)
                if customer and self.command == 'PUT':
                    partner = partners.require(self.headers.get('Cookie'))
                    return self.send(200,store.update_customer(int(customer[1]),data.get('customer'),partner,data.get('reason'),data.get('version')))
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
    backups = BackupRunner(ROOT) if args.db.resolve()==(ROOT/'data/erp-demo.sqlite3').resolve() else None
    if backups:backups.start()
    print(f"ERP Oveja: http://127.0.0.1:{server.server_address[1]} · Ctrl+C para detener", flush=True)
    print(f"SQLite: {args.db.resolve()} · Solo localhost · Acceso con sesión", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if backups:backups.stop()
        server.server_close()


if __name__ == "__main__":
    main()
