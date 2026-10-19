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


def handler_for(store, data_root=None, public_origin=None, transport=BaseHTTPRequestHandler, bag_access_enabled=False):
    reader_path = ROOT/'private/toteat-reader.sqlite3' if store.path.resolve() == (ROOT/'data/erp-demo.sqlite3').resolve() else store.path.parent/'toteat-reader.sqlite3'
    if data_root:reader_path=Path(data_root)/'private/toteat-reader.sqlite3'
    if public_origin:
        parsed_origin=urlsplit(public_origin)
        if parsed_origin.scheme!='https' or not parsed_origin.hostname or parsed_origin.path or parsed_origin.query or parsed_origin.fragment or parsed_origin.username:
            raise ValueError('Configure an exact HTTPS origin without path.')
    partners = PartnerAuth(reader_path.parent/'partner-auth.sqlite3')
    from erp import cash_bags, cash_control, cash_closures
    cash_bags.initialize(store)
    cash_control.initialize(store)
    cash_closures.initialize(store)
    access=None
    if bag_access_enabled:
        from erp.bag_access import BagAccess
        access=BagAccess(store)
    class Handler(transport):
        server_version = "OvejaLocal/0.1"
        bag_access_available = bag_access_enabled

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
            frame_ancestors="'self'" if content_type.startswith('application/pdf') else "'none'"
            self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors {frame_ancestors}; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def check_host(self):
            port = self.server.server_address[1]
            if public_origin:
                if self.headers.get("Host") != urlsplit(public_origin).netloc:raise DomainError("Host no autorizado.",403)
                return
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                raise DomainError("Acceso permitido solo desde localhost.", 403)

        def check_write_origin(self):
            self.check_host()
            expected = public_origin or "http://" + self.headers["Host"]
            origin = self.headers.get("Origin")
            if origin and origin != expected:
                raise DomainError("Origen externo rechazado.", 403)
            if self.headers.get("X-ERP-Local") != "1":
                raise DomainError("Falta encabezado de operación local.", 403)

        def body(self, maximum=MAX_BODY):
            self.check_write_origin()
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise DomainError("Se requiere application/json.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise DomainError("Tamaño no válido.") from None
            if not 0 < length <= maximum:
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
            if path==cash_closures.PREFIX or path.startswith(cash_closures.PREFIX+'/'):
                account=partners.session(self.headers.get('Cookie'))
                identity_check=None
                if not account and access:
                    with store.connect() as db:limited=access.session_row(db,self.headers.get('Cookie'))
                    if limited:
                        account={'role':'bag_cashier','realm':'bag','username':limited['id'],'name':limited['name'],'shared':bool(limited['shared'])}
                        identity_check=lambda db: (lambda r: bool(r and r['id']==account['username']))(access.session_row(db,self.headers.get('Cookie')))
                if not account:raise DomainError('Inicia sesión para abrir tu cierre.',401)
                if account.get('realm')!='bag':identity_check=lambda db: bool(partners.session(self.headers.get('Cookie')))
                return cash_closures.handle(self,store,path,parse_qs(parsed.query),account,identity_check)
            if access:
                origin=public_origin or 'http://'+self.headers['Host']
                if path=='/api/bag-access' or path.startswith('/api/bag-access/'):
                    return access.route(self,path,parse_qs(parsed.query),origin)
                if path=='/api/bag-cashiers':
                    erp_user=partners.session(self.headers.get('Cookie'))
                    actor=erp_user['username'] if erp_user and erp_user['role']=='partner' else access.require_admin(self.headers.get('Cookie'))
                    if self.command=='GET':return self.send(200,access.management())
                    if self.command=='POST':return self.send(200,access.manage(self.body(maximum=8192),actor,self.headers.get('Cookie')))
                    raise DomainError('Método no permitido.',405)
                query=parse_qs(parsed.query)
                scanned=(path=='/' and query.get('module')==['cash'] and len(query.get('bag',[]))==1 and re.fullmatch(cash_bags.BAG_ID,query['bag'][0]))
                if self.command=='GET' and scanned and not partners.session(self.headers.get('Cookie')):
                    return self.send(200,(ROOT/'static/bag-entry.html').read_bytes(),'text/html; charset=utf-8',{'X-Robots-Tag':'noindex, nofollow, noarchive'})
                if self.command=='GET' and path=='/bag-activate':
                    return self.send(200,(ROOT/'static/bag-activate.html').read_bytes(),'text/html; charset=utf-8',{'X-Robots-Tag':'noindex, nofollow, noarchive'})
                if self.command=='GET' and path=='/bag-cashiers':
                    erp_user=partners.session(self.headers.get('Cookie'))
                    if not erp_user or erp_user['role']!='partner':access.require_admin(self.headers.get('Cookie'))
                    return self.send(200,(ROOT/'static/bag-cashiers.html').read_bytes(),'text/html; charset=utf-8')
            if path.startswith('/api/toteat/reception') or path == '/api/sos' or path.startswith('/api/sos/') or re.fullmatch(r'/api/orders/\d+/(schedule-without-payment|customer)', path):
                partner = partners.session(self.headers.get('Cookie'))
                if not partner or partner['role'] != 'partner':
                    return self.send(403, {'error': 'Esta acción requiere una sesión de socio autenticada.',
                                           'code': 'partner_auth_required', 'available': False})
            public = {('GET','/api/health'),('GET','/api/partner/session'),('POST','/api/partner/login'),('POST','/api/partner/logout')}
            if path.startswith('/api/') and (self.command,path) not in public:
                user = partners.authorize(self.headers.get('Cookie'), 'read')
            if path=='/api/cash-insights' or path.startswith('/api/cash-insights/'):
                from erp import cash_insights
                def insights_identity():
                    current=partners.session(self.headers.get('Cookie'))
                    return bool(current and current.get('role')=='partner' and current.get('username')==user['username'])
                return cash_insights.handle(self,store,path,parse_qs(parsed.query,keep_blank_values=True),user,insights_identity)
            if path.startswith('/api/cash-control/'):
                return cash_control.handle(self,store,path,parse_qs(parsed.query,keep_blank_values=True),user)
            if path == '/api/cash-bags' or path.startswith('/api/cash-bags/'):
                origin = public_origin or 'http://127.0.0.1:'+str(self.server.server_address[1])
                return cash_bags.handle(self,store,path,parse_qs(parsed.query),user,origin)
            if path == '/api/document-center' or path.startswith('/api/document-center/'):
                from erp import document_center
                return document_center.route(self,store,partners,Path(data_root) if data_root else ROOT,parsed,user)
            if path == '/api/tasks' or path.startswith('/api/tasks/'):
                from erp import task_assignment
                account = partners.require(self.headers.get('Cookie'))
                return task_assignment.handle(self, store, path, parse_qs(parsed.query), account['username'])
            if path.startswith('/api/suppliers/'):
                from erp import supplier_opening
                account = partners.require(self.headers.get('Cookie'))
                if self.command == 'GET' and path == '/api/suppliers/opening':
                    return self.send(200, supplier_opening.view(store))
                if self.command == 'POST' and path == '/api/suppliers/opening/annotations':
                    return self.send(200, supplier_opening.annotate(store, self.body(), account['username']))
                from erp import payables
                return payables.handle(self, store, path, parse_qs(parsed.query), account['username'])
            if path.startswith('/api/delivery/'):
                from erp import delivery_finance
                account=partners.require(self.headers.get('Cookie'))
                if self.command=='POST' and path=='/api/delivery/adjustments':
                    from erp import delivery_adjustments
                    return self.send(200,delivery_adjustments.create(store,self.body(),account['username']))
                adjustment_void=re.fullmatch(r'/api/delivery/adjustments/([A-Za-z0-9_-]{12,100})/void',path)
                if self.command=='POST' and adjustment_void:
                    from erp import delivery_adjustments
                    return self.send(200,delivery_adjustments.void(store,adjustment_void[1],self.body(),account['username']))
                if self.command=='GET' and path in ('/api/delivery/summary','/api/delivery/summary.pdf','/api/delivery/summary.csv'):
                    from erp import delivery_summary
                    query=parse_qs(parsed.query,keep_blank_values=True)
                    value=delivery_summary.view(store,query.get('month',[None])[0],query.get('driver',['all']))
                    if path.endswith('.pdf') or path.endswith('.csv'):
                        pdf=path.endswith('.pdf');extension='pdf' if pdf else 'csv'
                        disposition='inline' if pdf and query.get('download')!=['1'] else 'attachment'
                        content=delivery_summary.pdf(value) if pdf else delivery_summary.csv_bytes(value)
                        return self.send(200,content,'application/pdf' if pdf else 'text/csv; charset=utf-8',
                                         {'Content-Disposition':disposition+'; filename="resumen-repartidores-'+value['month']+'.'+extension+'"'})
                    return self.send(200,value)
                if self.command=='GET' and path in ('/api/delivery/chat','/api/delivery/chat/evidence'):
                    from erp import delivery_chat
                    query=parse_qs(parsed.query)
                    if path.endswith('/evidence'):
                        return self.send(200,delivery_chat.evidence(store,query.get('source',[None])[0],query.get('record',[None])[0],query.get('incident',[None])[0]))
                    return self.send(200,delivery_chat.view(store,query.get('month',[None])[0]))
                if self.command=='GET' and path in ('/api/delivery/audit','/api/delivery/audit/document','/api/delivery/settlement','/api/delivery/settlement/pdf'):
                    from erp import delivery_audit, delivery_settlement
                    query=parse_qs(parsed.query,keep_blank_values=True); month=query.get('month',[None])[0]
                    if path=='/api/delivery/audit':return self.send(200,delivery_audit.view(store,month))
                    if path=='/api/delivery/audit/document':
                        content,kind,extension=delivery_audit.document(store,month,query.get('id',[''])[0])
                        disposition='attachment' if query.get('download')==['1'] or extension!='pdf' else 'inline'
                        return self.send(200,content,kind,{'Content-Disposition':disposition+'; filename="informe-repartidor.'+extension+'"'})
                    value=delivery_settlement.view(store,month,query.get('driver',['all']))
                    if path.endswith('/pdf'):
                        disposition='attachment' if query.get('download')==['1'] else 'inline'
                        return self.send(200,delivery_settlement.pdf(value),'application/pdf',{'Content-Disposition':disposition+'; filename="liquidacion-provisional-'+value['month']+'.pdf"'})
                    return self.send(200,value)
                if self.command=='GET' and path=='/api/delivery/fees':
                    value=delivery_finance.view(store,parse_qs(parsed.query).get('month',[None])[0])
                    reader=read_inbox(reader_path,False)
                    value['reader']=reader.get('delivery_reader',{'state':reader.get('state','not_configured')})
                    return self.send(200,value)
                if self.command=='POST' and path=='/api/delivery/refresh':
                    self.body()
                    return self.send(202,delivery_finance.request_refresh(store,account['username']))
            if self.command=='POST' and path=='/api/whatsapp/copied':
                from erp.marking_message import acknowledge
                return self.send(200,acknowledge(store,user['username'],self.body()))
            if self.command == "GET":
                if path == '/api/documents/salon/etiquetas-bolsas-000001-000020.pdf':
                    destination='/api/document-center/documents/salon-etiquetas-bolsas-000001-000020/file'
                    if parse_qs(parsed.query).get('download')==['1']:destination+='?download=1'
                    return self.send(307,b'',extra_headers={'Location':destination})
                if path == '/api/documents/salon/conteo-semanal-lunes-a-viernes.pdf':
                    destination='/api/document-center/documents/salon-conteo-semanal/file'
                    if parse_qs(parsed.query).get('download')==['1']:destination+='?download=1'
                    return self.send(307,b'',extra_headers={'Location':destination})
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
                    return self.send(200,read_transfers(os.environ.get('OVEJA_TRANSFERS_SNAPSHOT'),{k:v[0] for k,v in parse_qs(parsed.query).items()},os.environ.get('OVEJA_TRANSFERS_SOCKET')))
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
                if path == '/api/whatsapp':
                    from erp.marking_message import view as marking_message
                    return self.send(200,marking_message(store,today(),parse_qs(parsed.query).get('scope',['operations'])[0],user['username']))
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
                assets = {"/delivery-review.js": ("delivery-review.js", "text/javascript"), "/delivery-chat.js": ("delivery-chat.js", "text/javascript"), "/delivery.js": ("delivery.js", "text/javascript"), "/theme.css": ("theme.css", "text/css"), "/fonts/NotoSans.ttf": ("fonts/NotoSans.ttf", "font/ttf"),"/home.js": ("home.js", "text/javascript"),"/transfers.js": ("transfers.js", "text/javascript"),"/reception.js": ("reception.js", "text/javascript"),"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/notifications.js": ("notifications.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
                if access:
                    assets.update({'/bag-activate.js':('bag-activate.js','text/javascript'),'/bag-entry.js':('bag-entry.js','text/javascript'),'/bag-access.css':('bag-access.css','text/css'),'/bag-cashiers.js':('bag-cashiers.js','text/javascript')})
                assets['/stock-count.js'] = ('stock-count.js', 'text/javascript')
                assets.update({'/cash-control.js':('cash-control.js','text/javascript'),'/cash-control.css':('cash-control.css','text/css')})
                assets.update({'/cashier-v2.js':('cashier-v2.js','text/javascript'),'/cashier-v2.css':('cashier-v2.css','text/css'),'/cashier-v2-server.js':('cashier-v2-server.js','text/javascript')})
                assets.update({'/cash-insights.js':('cash-insights.js','text/javascript'),'/cash-toteat.js':('cash-toteat.js','text/javascript'),'/cash-submit-review.js':('cash-submit-review.js','text/javascript'),'/cash-review.js':('cash-review.js','text/javascript'),'/cash-closures.js':('cash-closures.js','text/javascript'),'/cash-closures.css':('cash-closures.css','text/css')})
                assets.update({'/cash-bags.js':('cash-bags.js','text/javascript'),'/cash-bags.css':('cash-bags.css','text/css')})
                assets.update({'/tasks.js': ('tasks.js','text/javascript'), '/tasks.css': ('tasks.css','text/css'),
                               '/tasks-print.js': ('tasks-print.js','text/javascript'), '/tasks-print.css': ('tasks-print.css','text/css')})
                assets['/supplier-opening.js'] = ('supplier-opening.js', 'text/javascript')
                assets['/date-range.js'] = ('date-range.js', 'text/javascript')
                assets['/supplier-accounts.js'] = ('supplier-accounts.js', 'text/javascript')
                assets['/supplier-accounts.css'] = ('supplier-accounts.css', 'text/css')
                assets['/joint-payment.js'] = ('joint-payment.js', 'text/javascript')
                assets['/supplier-account.js'] = ('supplier-account.js', 'text/javascript')
                assets['/payment-proposal.js'] = ('payment-proposal.js', 'text/javascript')
                assets['/supplier-directory.js'] = ('supplier-directory.js', 'text/javascript')
                assets['/supply-planning.js'] = ('supply-planning.js', 'text/javascript')
                assets['/document-intake.js'] = ('document-intake.js', 'text/javascript')
                assets['/purchase-agenda.js'] = ('purchase-agenda.js', 'text/javascript')
                assets['/image-attachment.js'] = ('image-attachment.js', 'text/javascript')
                assets['/procurement.js'] = ('procurement.js', 'text/javascript')
                assets['/procurement.css'] = ('procurement.css', 'text/css')
                assets['/workspace.css'] = ('workspace.css', 'text/css')
                assets['/design-system.css'] = ('design-system.css', 'text/css')
                assets['/loading-state.js'] = ('loading-state.js', 'text/javascript')
                assets['/document-center.js'] = ('document-center.js', 'text/javascript')
                assets['/document-center.css'] = ('document-center.css', 'text/css')
                assets['/technical-sheets.js'] = ('technical-sheets.js', 'text/javascript')
                assets['/technical-sheets.css'] = ('technical-sheets.css', 'text/css')
                assets.update({'/payables.js': ('payables.js', 'text/javascript'), '/payables.css': ('payables.css', 'text/css')})
                assets.update({'/payment-receipts.js': ('payment-receipts.js', 'text/javascript'), '/payment-receipts.css': ('payment-receipts.css', 'text/css')})
                if path in assets:
                    name, kind = assets[path]
                    return self.send(200, (ROOT / "static" / name).read_bytes(), kind + "; charset=utf-8")
            elif self.command in {"POST", "PUT"}:
                data = self.body()
                transfer_followup=re.fullmatch(r'/api/transfers/(\d+)/follow-up',path)
                if self.command=='POST' and (transfer_followup or path=='/api/transfers/export'):
                    account=partners.require(self.headers.get('Cookie'))
                    if transfer_followup:
                        from erp.transfer_followup import call
                        payload={key:data.get(key) for key in ('action','reason','version','request_id','source_ref')}
                        payload.update(actor=account['username'],revision_id=int(transfer_followup[1]))
                        return self.send(200,call(os.environ.get('OVEJA_TRANSFERS_SOCKET'),'/follow-up',payload))
                    from erp.transfers import export_csv
                    content=export_csv(os.environ.get('OVEJA_TRANSFERS_SNAPSHOT'),data.get('filters'),store,account['username'],data.get('request_id'),os.environ.get('OVEJA_TRANSFERS_SOCKET'))
                    return self.send(200,content,'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="oveja-transferencias.csv"'})
                if user:
                    # La identidad para el historial nunca procede del formulario.
                    data['actor'] = user['username']
                    permission = None
                    if path in ('/api/stock','/api/stock/batch'): permission = 'stock'
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
                    return self.send(200,reception.decide(store,received,data.get('decision'),partner,data.get('reason'),data.get('revision'),data.get('confirmation')))
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
                if path == '/api/stock/batch' and self.command == 'PUT':
                    return self.send(200, {'rows':store.update_stock_batch(data.get('entries'),data.get('actor'),data.get('reason'))})
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
                payload = {"error": str(error)}
                if getattr(error, 'field_errors', None):
                    payload['field_errors'] = error.field_errors
                from erp.payable_duplicates import HistoricalIdentityWarning
                if isinstance(error, HistoricalIdentityWarning):
                    payload['historical_duplicate'] = error.historical_duplicate
                self.send(error.status, payload)
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
