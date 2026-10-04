"""Persistencia SQLite. Cada cambio y su historial se guardan juntos."""

import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from erp.domain import CHANNELS, NEXT_STATUS, STATUSES, DomainError, order_status
from erp.catalog import CATALOG, validate_product
from erp.catalog_import import canonical, validate_catalog
from erp.toteat_scheduling import parse_comment, settlement, channel_from_platform


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value, label, required=True, maximum=160):
    if not isinstance(value, str):
        raise DomainError(f"{label}: se espera texto.")
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise DomainError(f"{label}: completa el campo (máximo {maximum} caracteres).")
    # Una línea por campo mantiene seguro y legible el texto de WhatsApp.
    if any(ord(c) < 32 for c in value) or "**" in value:
        raise DomainError(f"{label}: no uses saltos de línea, controles ni ** (reservado para reserva de vitrina).")
    return value


def validate_order(data, catalog=CATALOG):
    if not isinstance(data, dict):
        raise DomainError("Se espera un pedido JSON.")
    if data.get("is_demo") is not True:
        raise DomainError("Este prototipo admite solo pedidos ficticios.")
    if data.get("payment_confirmed") is not True:
        raise DomainError("Solo se confirman pedidos con pago verificado. Un comprobante no acredita abono bancario.")
    source = clean_text(data.get("source"), "Fuente", maximum=40).lower()
    if source not in {"demo", "demo-toteat"}:
        raise DomainError("Solo se permiten fuentes de demostración.")
    source_id = clean_text(data.get("source_id"), "ID fuente", maximum=80)
    if not source_id.upper().startswith("DEMO-"):
        raise DomainError("El ID ficticio debe comenzar por DEMO-.")
    channel = data.get("channel")
    if channel not in CHANNELS:
        raise DomainError("Canal no válido.")
    fulfillment = data.get("fulfillment")
    if fulfillment not in {"retiro", "despacho"}:
        raise DomainError("Selecciona retiro o despacho.")
    timing = data.get('delivery_timing')
    if timing not in ('scheduled', 'immediate', 'unclassified'):
        raise DomainError('Selecciona entrega inmediata o programada; no se deduce del comentario.')
    phone = clean_text(data.get('customer_phone', ''), 'Teléfono ficticio', required=timing == 'scheduled', maximum=40)
    pickup = data.get("pickup_at")
    try:
        parsed = datetime.strptime(pickup, "%Y-%m-%dT%H:%M")
        if parsed.strftime("%Y-%m-%dT%H:%M") != pickup:
            raise ValueError
    except (ValueError, TypeError):
        raise DomainError("Fecha y hora de retiro no válidas.") from None
    rows = data.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise DomainError("El pedido necesita entre 1 y 50 ítems.")
    items = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DomainError("Ítem no válido.")
        item = {key: clean_text(row.get(key), label, maximum=80) for key, label in
                (("source_item_id", "ID ítem"), ("flavor", "Sabor"), ("size", "Tamaño"), ("sku", "SKU"))}
        if item["source_item_id"] in seen:
            raise DomainError("El ID de cada ítem debe ser único dentro del pedido.")
        seen.add(item["source_item_id"])
        quantity = row.get("quantity")
        if type(quantity) is not int or not 1 <= quantity <= 999:
            raise DomainError("La cantidad debe ser un entero entre 1 y 999.")
        if row.get("kind") != "torta":
            raise DomainError("Este módulo admite solo tortas enteras; excluye trozos y porciones.")
        if any(word in item["sku"].casefold() for word in ("trozo", "porcion", "porción", "slice")):
            raise DomainError("Los SKU de trozos o porciones están fuera de este módulo.")
        item.update(quantity=quantity, kind=row["kind"], comments=clean_text(row.get("comments", ""), "Comentario del ítem", False, 500))
        validate_product(item, catalog)
        items.append(item)
    return dict(source=source, source_id=source_id, channel=channel,
                customer=clean_text(data.get("customer"), "Cliente"), pickup_at=pickup,
                fulfillment=fulfillment, comments=clean_text(data.get("comments", ""), "Comentarios", False, 1000),
                payment_confirmed=1, is_demo=1, items=items, delivery_timing=timing, customer_phone=phone)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_source_constraints()
        with self.connect() as db:
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM items WHERE status='solicitado'").fetchall():
                item = dict(row)
                db.execute("UPDATE items SET status='pendiente' WHERE id=?",(item['id'],))
                self._touch(db,item['order_id'])
                self._event(db,item['order_id'],item['id'],'reversion','Migración demo',
                            'Se retiró el paso heredado Solicitar producción. Vuelve a pendiente de marcado sin asumir que fue elaborado.',
                            item,dict(item,status='pendiente'))

    def _migrate_source_constraints(self):
        # Conserva IDs, hijos e historial al ampliar la antigua tabla solo-demo.
        with self.connect() as db:
            row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'").fetchone()
            if not row or "'manual-sos'" in row[0]:
                return
            definition = Path(__file__).with_name('schema.sql').read_text().split('CREATE TABLE IF NOT EXISTS orders (',1)[1].split(');',1)[0]
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE orders_next (' + definition + ')')
            db.execute('INSERT INTO orders_next SELECT * FROM orders')
            db.execute('DROP TABLE orders')
            db.execute('ALTER TABLE orders_next RENAME TO orders')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise DomainError('La migración requiere revisión; no se aplicó.')
            db.commit()
            db.execute('PRAGMA foreign_keys=ON')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, order_id):
        row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise DomainError("Pedido no encontrado.", 404)
        order = dict(row)
        order["is_demo"] = bool(order["is_demo"])
        order["payment_confirmed"] = bool(order["payment_confirmed"])
        order["items"] = [dict(row) for row in db.execute("SELECT * FROM items WHERE order_id=? ORDER BY id", (order_id,))]
        schedule = db.execute('SELECT timing,customer_phone FROM order_scheduling WHERE order_id=?', (order_id,)).fetchone()
        order['delivery_timing'] = schedule['timing'] if schedule else 'unclassified'
        order['customer_phone'] = schedule['customer_phone'] if schedule else ''
        simulation = db.execute('SELECT archived_at FROM simulation_orders WHERE order_id=?', (order_id,)).fetchone()
        order['is_simulation'] = simulation is not None
        order['simulation_archived'] = bool(simulation and simulation['archived_at'])
        order["status_label"] = order_status(order["items"])
        document = db.execute("SELECT source,status FROM documents WHERE order_id=?",(order_id,)).fetchone()
        order["receipt"] = dict(document) if document else {"source":"toteat","status":"pending"}
        source = db.execute('SELECT payload_json FROM toteat_scheduling WHERE order_id=?',(order_id,)).fetchone()
        if source:
            order['toteat_schedule'] = json.loads(source[0])
            order['scheduling_status'] = 'scheduled'
            if all(item['status']=='pendiente' for item in order['items']):
                order['status_label'] = 'Agendado'
        attachment = db.execute('SELECT bytes,received_at FROM document_attachments WHERE order_id=?',(order_id,)).fetchone()
        if attachment:
            order['receipt'] = {'source':'toteat','status':'verified','bytes':attachment['bytes'],
                                'received_at':attachment['received_at'],'url':f'/api/orders/{order_id}/receipt'}
        changed = db.execute('SELECT * FROM order_customer_changes WHERE order_id=?',(order_id,)).fetchone()
        order['delivery_address'] = ''
        order['customer_edited'] = bool(changed)
        if changed:
            order.update({k:changed[k] for k in ('customer','customer_phone','pickup_at','fulfillment','delivery_address')})
            if source and order['customer_phone']:
                order['toteat_schedule']['warnings'] = [w for w in order['toteat_schedule']['warnings'] if w not in ('telefono_pendiente','telefono_por_revisar')]
            if source:
                order['toteat_schedule']['warnings'] = [w for w in order['toteat_schedule']['warnings'] if w not in ('fecha_pasada','anio_inferido')]
        manual = db.execute('SELECT status,payment_status,payment_evidence,partner_username,created_at FROM manual_scheduling WHERE order_id=?',(order_id,)).fetchone()
        if manual:
            order['manual_scheduling'] = dict(manual)
            order['scheduling_status'] = manual['status']
            if manual['status']=='draft':order['status_label']='Pendiente de autorización'
            elif manual['payment_status']=='unpaid':order['status_label']='Agendado sin pago'
            elif all(i['status']=='pendiente' for i in order['items']):order['status_label']='Agendado'
            link = db.execute('SELECT source_id FROM toteat_order_links WHERE order_id=?',(order_id,)).fetchone()
            order['toteat_link'] = json.loads(link[0]) if link else None
        order['source_comment'] = order.get('toteat_schedule',{}).get('original_comment',order['comments'] if source else '')
        from erp.reception import order_alert
        order['source_alert'] = order_alert(db,order_id)
        if order['source_alert'] and order['source_alert']['state']=='cancelled':order['status_label']='Anulado'
        return order

    def import_toteat_schedule(self, scope, transaction, candidate, is_test=False, receipt=None):
        """Entrada interna del lector GET; no está expuesta como POST de la UI."""
        if self.operating_mode() != 'toteat-local':
            raise DomainError('La recepción requiere la instalación con catálogo local.')
        order_id_source = str(transaction.get('orderId'))
        if candidate.get('order_id') != order_id_source or candidate.get('payment_id') != str(transaction.get('paymentId')):
            raise ValueError('sale_identity_mismatch')
        source_id = json.dumps([str(scope['restaurant_id']),str(scope['local_id']),order_id_source],separators=(',',':'))
        from erp.reception import require_importable
        require_importable(self,source_id)
        money = settlement(transaction)
        reference = datetime.fromisoformat(money['date_closed'])
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        parsed = parse_comment(transaction['comment'],reference.astimezone(ZoneInfo('America/Santiago')).date()) if isinstance(transaction.get('comment'),str) else None
        is_test = is_test or bool(parsed and parsed['test_marker'])
        from erp.sos import reconcile_sale, hold_possible_duplicate
        linked = reconcile_sale(self,source_id,transaction,candidate,money,is_test,receipt)
        if linked is not None:return linked
        if parsed is None:raise ValueError('comment_not_text')
        if parsed['issues']:
            raise ValueError('comment_requires_review:' + ','.join(parsed['issues']))
        hold_possible_duplicate(self,source_id,transaction,candidate,parsed,is_test)
        products = {str(p['source_product']['idToteat']):p for p in self.catalog() if p.get('source_product')}
        items = []; seen = set()
        for row in candidate['products']:
            product = products.get(str(row.get('catalog_id')))
            line_id = row.get('line_id')
            quantity = row.get('quantity')
            if not product or type(line_id) not in (str,int) or str(line_id) in seen or isinstance(quantity,bool):
                raise ValueError('invalid_source_item')
            try:
                count = Decimal(str(quantity))
            except InvalidOperation:
                raise ValueError('invalid_source_quantity') from None
            if not count.is_finite() or count != count.to_integral_value() or not 1 <= count <= 999:
                raise ValueError('invalid_source_quantity')
            seen.add(str(line_id))
            items.append({'source_item_id':str(line_id),'flavor':product['flavor'],'size':product['size'],
                          'sku':product['sku'],'quantity':int(count),'kind':'torta','comments':''})
        if not items:
            raise ValueError('missing_source_items')
        channel = channel_from_platform(parsed['platform'])
        evidence = {'order_id':order_id_source,'payment':money,'warnings':parsed['warnings'],
                    'year_inferred':parsed['year_inferred'],'platform_original':parsed['platform'],
                    'payment_note_original':parsed['payment_note'],'discount_policy':'Descuentos registrados en Toteat, autorizados por dueños según regla del negocio',
                    'is_test':is_test,'source':'sales'}
        data = {'source':'toteat','source_id':source_id,'channel':channel,'customer':parsed['customer'],
                'pickup_at':parsed['pickup_at'],'fulfillment':'retiro','comments':parsed['original'],
                'payment_confirmed':int(money['state']=='paid'),'is_demo':int(is_test)}
        digest = hashlib.sha256(canonical({'order':data,'phone':parsed['customer_phone'],'items':items,'evidence':evidence}).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            from erp.reception import guard_import
            guard_import(db,source_id)
            # El socio puede ingresar/vincular un SOS mientras se procesa la venta.
            # Revalidar con el bloqueo de escritura evita crear una segunda demanda.
            if db.execute('SELECT 1 FROM toteat_order_links WHERE source_id=?',(source_id,)).fetchone():
                raise ValueError('source_link_changed_retry')
            hold_possible_duplicate(self,source_id,transaction,candidate,parsed,is_test,db=db)
            saved = db.execute("SELECT id FROM orders WHERE source='toteat' AND source_id=?",(source_id,)).fetchone()
            if saved:
                order_id = saved['id']
                previous = db.execute('SELECT source_digest FROM toteat_scheduling WHERE order_id=?',(order_id,)).fetchone()
                if previous and previous[0] == digest:
                    if receipt:
                        self._attach_receipt(db,order_id,money['payment_id'],receipt)
                    return self._get(db,order_id)
                before = self._get(db,order_id)
                if any(i['status']!='pendiente' for i in before['items']):
                    raise ValueError('changed_order_already_in_progress')
                old = {i['source_item_id']:i for i in before['items']}
                if set(old) != seen:
                    raise ValueError('changed_product_lines_require_review')
                db.execute('UPDATE orders SET '+','.join(k+'=?' for k in data)+' WHERE id=?',[*data.values(),order_id])
                for item in items:
                    db.execute('UPDATE items SET '+','.join(k+'=?' for k in item)+' WHERE id=?',[*item.values(),old[item['source_item_id']]['id']])
                self._touch(db,order_id)
            else:
                before = None
                stamp = now()
                cursor = db.execute('INSERT INTO orders('+','.join(data)+',created_at,updated_at) VALUES('+','.join('?' for _ in range(len(data)+2))+')',[*data.values(),stamp,stamp])
                order_id = cursor.lastrowid
                for item in items:
                    self._insert_item(db,order_id,item)
                db.execute("INSERT INTO documents(order_id,source,status) VALUES(?,'toteat','pending')",(order_id,))
            db.execute("INSERT INTO order_scheduling VALUES(?,'scheduled',?) ON CONFLICT(order_id) DO UPDATE SET customer_phone=excluded.customer_phone",(order_id,parsed['customer_phone']))
            if is_test:
                db.execute('INSERT OR IGNORE INTO simulation_orders VALUES(?,?,NULL)',(order_id,now()))
            db.execute('INSERT INTO toteat_scheduling VALUES(?,?,?) ON CONFLICT(order_id) DO UPDATE SET source_digest=excluded.source_digest,payload_json=excluded.payload_json',
                       (order_id,digest,json.dumps(evidence,ensure_ascii=False)))
            if receipt:
                self._attach_receipt(db,order_id,money['payment_id'],receipt)
            after = self._get(db,order_id)
            self._event(db,order_id,None,'agendado' if before is None else 'datos_toteat_actualizados','Lector Toteat',
                        'Mesa cerrada y saldada con descuento registrado en Toteat' if money['state']=='discount_settled' else 'Mesa cerrada y pago registrado en Toteat',before,after)
            return after

    def _attach_receipt(self, db, order_id, payment_id, receipt):
        filename = receipt.get('filename','')
        digest = receipt.get('sha256','')
        import re
        if not re.fullmatch(r'[a-f0-9]{64}',digest) or filename != digest+'.pdf' or type(receipt.get('bytes')) is not int or receipt['bytes']<=0:
            raise ValueError('invalid_receipt_reference')
        previous = db.execute('SELECT sha256 FROM document_attachments WHERE order_id=?',(order_id,)).fetchone()
        if previous and previous[0] == digest:
            return
        db.execute('INSERT INTO document_attachments VALUES(?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET filename=excluded.filename,sha256=excluded.sha256,bytes=excluded.bytes,payment_id=excluded.payment_id,received_at=excluded.received_at',
                   (order_id,filename,digest,receipt['bytes'],payment_id,now()))
        db.execute("UPDATE documents SET status='verified',reference=? WHERE order_id=?",(digest,order_id))
        self._event(db,order_id,None,'boleta_adjunta','Lector Toteat','PDF oficial vinculado al pago de esta orden',None,{'sha256':digest,'payment_id':payment_id})

    def receipt_file(self, order_id):
        with self.connect() as db:
            row = db.execute('SELECT filename,sha256,bytes FROM document_attachments WHERE order_id=?',(order_id,)).fetchone()
            if not row:
                raise DomainError('La boleta todavía no está disponible.',404)
            return dict(row)

    def get(self, order_id):
        with self.connect() as db:
            return self._get(db, order_id)

    def list(self, start, end, include_archived=False):
        with self.connect() as db:
            ids = db.execute("SELECT o.id FROM orders o LEFT JOIN order_customer_changes c ON c.order_id=o.id WHERE COALESCE(c.pickup_at,o.pickup_at)>=? AND COALESCE(c.pickup_at,o.pickup_at)<? ORDER BY COALESCE(c.pickup_at,o.pickup_at),o.id", (start + "T00:00", end + "T24:00")).fetchall()
            orders = [self._get(db, row["id"]) for row in ids]
            return [order for order in orders if order.get('scheduling_status')!='draft' and (include_archived or not order["simulation_archived"])]

    def update_customer(self, order_id, data, partner, reason, version):
        if not isinstance(data,dict):
            raise DomainError('Completa los datos del cliente y la entrega.')
        fields = {k:clean_text(data.get(k,''),label,required=k!='delivery_address',maximum=500 if k=='delivery_address' else 160)
                  for k,label in [('customer','Cliente'),('customer_phone','Teléfono'),('delivery_address','Dirección')]}
        import re
        if not re.fullmatch(r'[+\d ()-]{7,25}',fields['customer_phone']):
            raise DomainError('Revisa el teléfono; admite dígitos, +, espacios, guiones y paréntesis.')
        fields['fulfillment'] = data.get('fulfillment')
        if fields['fulfillment'] not in ('retiro','despacho'):
            raise DomainError('Selecciona retiro en local o delivery.')
        if fields['fulfillment']=='despacho' and not fields['delivery_address']:
            raise DomainError('Para delivery, completa la dirección.')
        fields['pickup_at'] = data.get('pickup_at')
        try:
            parsed = datetime.strptime(fields['pickup_at'],'%Y-%m-%dT%H:%M')
            if parsed.strftime('%Y-%m-%dT%H:%M') != fields['pickup_at']:raise ValueError
            # Rechaza horas inexistentes por el cambio horario de Chile.
            local = parsed.replace(tzinfo=ZoneInfo('America/Santiago'))
            if local.astimezone(timezone.utc).astimezone(local.tzinfo).replace(tzinfo=None) != parsed:raise ValueError
        except (ValueError,TypeError):
            raise DomainError('Fecha y hora de entrega no válidas en Chile.') from None
        reason = clean_text(reason,'Motivo del cambio',maximum=500)
        actor = clean_text(partner.get('name'),'Socio',maximum=80)
        username = clean_text(partner.get('username'),'Usuario del socio',maximum=80)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            before = self._get(db,order_id)
            self._check_version(before,version)
            if before['delivery_timing']!='scheduled' or all(i['status'] in ('entregado','cancelado') for i in before['items']):
                raise DomainError('Solo se pueden modificar entregas programadas aún activas.')
            if all(before.get(k)==v for k,v in fields.items()):return before
            names = list(fields)
            db.execute('INSERT INTO order_customer_changes(order_id,'+','.join(names)+') VALUES('+','.join('?' for _ in range(len(names)+1))+') ON CONFLICT(order_id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in names),[order_id,*fields.values()])
            self._touch(db,order_id)
            after = self._get(db,order_id)
            self._event(db,order_id,None,'datos_cliente',actor,reason,
                        {k:before.get(k) for k in names},dict(fields,socio_autenticado=username))
            return after

    def _event(self, db, order_id, item_id, action, actor, reason, before, after):
        db.execute("INSERT INTO history(order_id,item_id,action,actor,occurred_at,reason,before_json,after_json) VALUES(?,?,?,?,?,?,?,?)",
                   (order_id, item_id, action, actor, now(), reason,
                    json.dumps(before, ensure_ascii=False) if before is not None else None,
                    json.dumps(after, ensure_ascii=False)))

    def create(self, data, actor, simulation=False):
        if self.operating_mode() == 'toteat-local' and not simulation:
            raise DomainError('La carga ficticia está desactivada. La recepción verificada de comandas Toteat está pendiente.',409)
        order = validate_order(data, self.catalog())
        actor = clean_text(actor, "Responsable", maximum=80)
        if simulation and (not order['customer'].casefold().startswith('cliente demo') or (order['customer_phone'] and set(order['customer_phone']) != {'0'})):
            raise DomainError('Las simulaciones usan Cliente demo y un teléfono ficticio de ceros. No ingreses datos personales reales.')
        items = order.pop("items")
        timing, phone = order.pop('delivery_timing'), order.pop('customer_phone')
        if timing == 'unclassified':
            raise DomainError('Un pedido nuevo requiere tipo de entrega explícito.')
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM orders WHERE source=? AND source_id=?", (order["source"], order["source_id"])).fetchone():
                raise DomainError("Este pedido ya existe (misma fuente + ID). No se duplicó ni se sumó otra vez.", 409)
            stamp = now()
            columns = list(order) + ["created_at", "updated_at"]
            placeholders = ",".join("?" for _ in columns)
            result = db.execute(f"INSERT INTO orders({','.join(columns)}) VALUES({placeholders})", list(order.values()) + [stamp, stamp])
            order_id = result.lastrowid
            db.execute("INSERT INTO documents(order_id,source,status) VALUES(?,'toteat','pending')",(order_id,))
            db.execute('INSERT INTO order_scheduling VALUES(?,?,?)', (order_id,timing,phone))
            if simulation:
                db.execute('INSERT INTO simulation_orders VALUES(?,?,NULL)',(order_id,stamp))
            for item in items:
                self._insert_item(db, order_id, dict(item, status='entregado') if timing == 'immediate' else item)
            after = self._get(db, order_id)
            self._event(db, order_id, None, "creado", actor, "Venta ficticia inmediata confirmada como entregada" if timing == "immediate" else "Pedido ficticio programado con pago simulado confirmado", None, after)
            return after

    def _insert_item(self, db, order_id, item):
        columns = ["order_id"] + list(item)
        db.execute(f"INSERT INTO items({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [order_id] + list(item.values()))

    def _check_version(self, order, expected):
        if order.get('simulation_archived'):
            raise DomainError('Esta prueba está retirada. Restáurala antes de modificarla.',409)
        if type(expected) is not int or order["version"] != expected:
            raise DomainError("El pedido cambió en otra ventana. Cierra el panel y recarga antes de continuar.", 409)

    def _touch(self, db, order_id):
        db.execute("UPDATE orders SET version=version+1,updated_at=? WHERE id=?", (now(), order_id))

    def update(self, order_id, data, actor, reason, version):
        updated = validate_order(data, self.catalog())
        actor = clean_text(actor, "Responsable", maximum=80)
        reason = clean_text(reason, "Motivo de corrección", maximum=500)
        new_items = updated.pop("items")
        timing, phone = updated.pop('delivery_timing'), updated.pop('customer_phone')
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self._get(db, order_id)
            self._check_version(before, version)
            if before['is_simulation'] and (not updated['customer'].casefold().startswith('cliente demo') or (phone and set(phone) != {'0'})):
                raise DomainError('Las simulaciones usan Cliente demo y un teléfono ficticio de ceros. No ingreses datos personales reales.')
            if (updated["source"], updated["source_id"]) != (before["source"], before["source_id"]):
                raise DomainError("Fuente e ID son inmutables para prevenir duplicados.")
            if timing != before['delivery_timing']:
                if timing == 'unclassified':
                    raise DomainError('No se puede borrar una clasificación de entrega confirmada.')
                if timing == 'immediate' and any(item['status'] != 'entregado' for item in before['items']):
                    raise DomainError('Una venta inmediata confirma entrega: confirma la entrega de los ítems antes de reclasificar este pedido.')
            old_items = {row["source_item_id"]: row for row in before["items"]}
            if not old_items.keys() <= {row["source_item_id"] for row in new_items}:
                raise DomainError("No se pueden borrar ítems ni cambiar sus IDs. Usa Cancelar y conserva el historial.")
            db.execute(f"UPDATE orders SET {','.join(key+'=?' for key in updated)} WHERE id=?", list(updated.values()) + [order_id])
            for item in new_items:
                old = old_items.get(item["source_item_id"])
                if old:
                    changed = any(old[key] != value for key, value in item.items())
                    if changed and old["status"] != "pendiente":
                        raise DomainError("Para corregir un ítem solicitado, marcado, entregado o cancelado, primero revierte su estado a Pendiente con un motivo.")
                    db.execute(f"UPDATE items SET {','.join(key+'=?' for key in item)} WHERE id=?", list(item.values()) + [old["id"]])
                else:
                    if timing == 'immediate':
                        raise DomainError('No agregues ítems a una venta inmediata ya confirmada. Registra otra venta demo para mantener trazabilidad.')
                    self._insert_item(db, order_id, item)
            db.execute('INSERT INTO order_scheduling VALUES(?,?,?) ON CONFLICT(order_id) DO UPDATE SET timing=excluded.timing,customer_phone=excluded.customer_phone', (order_id,timing,phone))
            self._touch(db, order_id)
            after = self._get(db, order_id)
            self._event(db, order_id, None, "correccion", actor, reason, before, after)
            return after

    def transition(self, order_id, item_id, status, actor, reason, version, correction=False):
        actor = clean_text(actor, "Responsable", maximum=80)
        reason = clean_text(reason, "Motivo", maximum=500)
        if status not in STATUSES:
            raise DomainError("Estado no válido.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = self._get(db, order_id)
            if order.get('scheduling_status')=='draft':
                raise DomainError('Un socio debe autorizar primero el agendamiento sin pago.',403)
            if order['source']=='toteat' and order.get('scheduling_status')!='scheduled':
                raise DomainError('Primero debe confirmarse el pago y cierre para pasar a Agendadas.',403)
            if order.get('source_alert',{} ) and order['source_alert']['state']=='cancelled':
                raise DomainError('El pedido está anulado. Revisa su historial antes de cualquier nuevo encargo.',403)
            self._check_version(order, version)
            item = next((row for row in order["items"] if row["id"] == item_id), None)
            if not item:
                raise DomainError("Ítem no encontrado en el pedido.", 404)
            old_status = item["status"]
            if status == old_status:
                raise DomainError("El ítem ya tiene ese estado.", 409)
            if correction:
                if status != "pendiente":
                    raise DomainError("Una reversión vuelve a Pendiente; luego sigue el flujo normal.")
            elif status != NEXT_STATUS.get(old_status) and not (status == "cancelado" and old_status in {"pendiente", "solicitado", "marcado_solicitado", "marcado"}):
                raise DomainError("Transición no permitida. Sigue la secuencia o revierte con motivo.")
            db.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))
            self._touch(db, order_id)
            after = dict(item, status=status)
            self._event(db, order_id, item_id, "reversion" if correction else "estado", actor, reason, item, after)
            return self._get(db, order_id)

    def archive_simulation(self, order_id, actor, version, archived=True):
        actor = clean_text(actor,'Responsable',maximum=80)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            before = self._get(db,order_id)
            if not before['is_simulation']:
                raise DomainError('Solo se pueden retirar o restaurar pedidos de prueba.',400)
            if type(version) is not int or version != before['version'] or before['simulation_archived'] == archived:
                raise DomainError('La prueba cambió o ya tiene ese estado. Actualiza antes de continuar.',409)
            db.execute('UPDATE simulation_orders SET archived_at=? WHERE order_id=?',(now() if archived else None,order_id))
            self._touch(db,order_id)
            after = self._get(db,order_id)
            self._event(db,order_id,None,'prueba_retirada' if archived else 'prueba_restaurada',actor,
                        'Prueba retirada de las vistas activas; historial recuperable' if archived else 'Prueba restaurada por el usuario',before,after)
            return after

    def history(self, order_id):
        with self.connect() as db:
            self._get(db, order_id)
            rows = [dict(row) for row in db.execute("SELECT * FROM history WHERE order_id=? ORDER BY id DESC", (order_id,))]
        for row in rows:
            row["before"] = json.loads(row.pop("before_json") or "null")
            row["after"] = json.loads(row.pop("after_json"))
        return rows

    def stock(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM stock ORDER BY flavor,size")]

    def notification_scope(self):
        with self.connect() as db:
            rows = db.execute("SELECT key,value FROM metadata WHERE key IN ('operating_mode','demo_cleanup_backup','demo_seed_date') ORDER BY key").fetchall()
        return hashlib.sha256((str(self.path.resolve()) + json.dumps([tuple(row) for row in rows])).encode()).hexdigest()[:24]

    def notifications(self):
        # Una solicitud vigente por ítem, derivada del estado y su evento transaccional.
        # El historial conserva eventos resueltos. No se ejecuta despacho externo.
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT i.id AS item_id,o.id AS order_id,COALESCE(c.customer,o.customer) AS customer,COALESCE(c.pickup_at,o.pickup_at) AS pickup_at,
                i.flavor,i.size,i.quantity,h.actor,h.occurred_at,h.id AS event_id,
                EXISTS(SELECT 1 FROM simulation_orders WHERE order_id=o.id) AS is_simulation
                FROM items i JOIN orders o ON o.id=i.order_id
                LEFT JOIN order_customer_changes c ON c.order_id=o.id
                JOIN history h ON h.id=(SELECT MAX(id) FROM history WHERE item_id=i.id)
                WHERE i.status='marcado_solicitado' AND NOT EXISTS(SELECT 1 FROM simulation_orders WHERE order_id=o.id AND archived_at IS NOT NULL) ORDER BY COALESCE(c.pickup_at,o.pickup_at),i.id""")]

    def analytics(self, orders):
        channels, durations = {}, []
        on_time = delivered = 0
        for order in orders:
            channels[order['channel']] = channels.get(order['channel'],0) + 1
            pending = {}
            for event in reversed(self.history(order['id'])):
                if event['action'] not in {'estado','reversion'}: continue
                status = event['after']['status']
                stamp = datetime.fromisoformat(event['occurred_at'])
                item = event['item_id']
                if status == 'marcado_solicitado': pending[item] = stamp
                elif status == 'marcado' and item in pending:
                    durations.append((stamp-pending.pop(item)).total_seconds()/60)
                elif status in {'pendiente','cancelado'}: pending.pop(item,None)
            for item in order['items']:
                if order['delivery_timing'] != 'scheduled' or item['status'] != 'entregado': continue
                event = next((e for e in self.history(order['id']) if e['item_id']==item['id'] and e['action']=='estado' and e['after']['status']=='entregado'),None)
                if event:
                    delivered += 1
                    scheduled = datetime.fromisoformat(order['pickup_at']).replace(tzinfo=ZoneInfo('America/Santiago'))
                    on_time += datetime.fromisoformat(event['occurred_at']) <= scheduled
        return {'orders':len(orders),'units':sum(i['quantity'] for o in orders for i in o['items'] if i['kind']=='torta' and i['status']!='cancelado'),
                'channels':channels,'mark_samples':len(durations),'mark_minutes':round(sum(durations)/len(durations),1) if durations else None,
                'on_time':on_time,'on_time_total':delivered}

    def operating_mode(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='operating_mode'").fetchone()
        return row[0] if row else 'demo'

    def catalog(self):
        catalog = [] if self.operating_mode() == 'toteat-local' else deepcopy(CATALOG)
        with self.connect() as db:
            catalog.extend(json.loads(row['payload_json']) for row in db.execute('SELECT payload_json FROM catalog_products ORDER BY imported_at,sku'))
            updates = {row['sku']:dict(row) for row in db.execute('SELECT * FROM recipes')}
        if self.operating_mode() == 'toteat-local':
            catalog = [p for p in catalog if p.get('source') == 'toteat-manual']
        for product in catalog:
            if product['sku'] in updates:
                update = updates[product['sku']]
                product.update(bases=json.loads(update['bases_json']),version=update['version'],recipe_status='edited',recipe_note='Receta editada localmente con historial; revisión del responsable.')
        return catalog

    def import_catalog(self, payload, actor):
        products = validate_catalog(payload)
        actor = clean_text(actor, 'Responsable de incorporación', maximum=80)
        # Orden independiente: repetir el mismo conjunto no duplica productos ni auditoría.
        products.sort(key=lambda product: product['sku'])
        digest = hashlib.sha256(canonical(products).encode()).hexdigest()
        added = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = deepcopy(CATALOG)
            existing.extend(json.loads(row['payload_json']) for row in db.execute('SELECT payload_json FROM catalog_products'))
            by_sku = {p['sku']: p for p in existing}
            stamp = now()
            for product in products:
                previous = by_sku.get(product['sku'])
                if previous:
                    if canonical(previous) != canonical(product):
                        raise DomainError('Catálogo: identidad existente con datos diferentes. No se reemplazó ningún registro; requiere revisión explícita.', 409)
                    continue
                origin = product['source_product']
                for old in existing:
                    if (old['flavor'], old['size']) == (product['flavor'], product['size']):
                        raise DomainError('Catálogo: producto/formato ya vinculado a otra identidad. No se fusionó stock ni historial.', 409)
                    if old.get('source') != product['source'] or not old.get('source_product'):
                        continue
                    prior = old['source_product']
                    if prior['idToteat'] == origin['idToteat'] or prior['localCode'] == origin['localCode']:
                        raise DomainError('Catálogo: identificador de origen reutilizado. Lote detenido.', 409)
                    if prior['categoryId'] == origin['categoryId'] and (prior['category'], old['catalog_group']) != (origin['category'], product['catalog_group']):
                        raise DomainError('Catálogo: categoría existente incompatible. Lote detenido.', 409)
                db.execute('INSERT INTO catalog_products VALUES(?,?,?,?,?,?)',
                           (product['sku'], product['source'], origin['id'], canonical(product), stamp, actor))
                existing.append(product); by_sku[product['sku']] = product; added += 1
            unchanged = len(products) - added
            db.execute('INSERT OR IGNORE INTO catalog_imports VALUES(?,?,?,?,?,?,?)',
                       (digest, payload['source'], payload['source_reference'], stamp, actor, added, unchanged))
        return {'added': added, 'unchanged': unchanged, 'total': len(products), 'source': payload['source'], 'synchronized': False}

    def update_recipe(self, sku, bases, actor, reason, version):
        product = next((p for p in self.catalog() if p['sku'] == sku),None)
        if not product:
            raise DomainError('Producto entero no encontrado.',404)
        if bases is not None and (not isinstance(bases,list) or not 1 <= len(bases) <= 10):
            raise DomainError('Indica de 1 a 10 bases o deja la receta pendiente.')
        cleaned = None if bases is None else []
        for base in bases or []:
            if not isinstance(base,dict): raise DomainError('Base no válida.')
            try:
                quantity = Decimal(str(base.get('quantity')))
                if not quantity.is_finite() or not Decimal('0') < quantity <= 999 or quantity.as_tuple().exponent < -3:
                    raise InvalidOperation
            except (InvalidOperation,ValueError):
                raise DomainError('Cantidad de base: mayor que cero, máximo 999 y tres decimales.') from None
            unit=base.get('unit') or None
            if (unit is not None and not isinstance(unit,str)) or unit not in {None,'whole_sponge','sponge_disc','sheet','unit'}:
                raise DomainError('Selecciona una unidad de producción válida o déjala por confirmar.')
            diameter=base.get('diameter_cm')
            if diameter in (None,''):diameter=None
            else:
                try:
                    number=Decimal(str(diameter))
                    if not number.is_finite() or not 0<number<=100 or number.as_tuple().exponent < -2:raise InvalidOperation
                    diameter=format(number.normalize(),'f')
                except (InvalidOperation,ValueError):
                    raise DomainError('Diámetro: centímetros mayores que cero, hasta 100 y dos decimales; vacío si no está confirmado.') from None
            cleaned.append({'name':clean_text(base.get('name'),'Base',maximum=80),'size':product['size'],'quantity':str(quantity),'unit':unit,'diameter_cm':diameter})
        actor = clean_text(actor,'Responsable',maximum=80)
        reason = clean_text(reason,'Motivo',maximum=500)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM recipes WHERE sku=?',(sku,)).fetchone()
            expected = row[0] if row else 0
            if type(version) is not int or version != expected:
                raise DomainError('La receta cambió. Actualiza antes de continuar.',409)
            after = json.dumps(cleaned,ensure_ascii=False)
            db.execute('INSERT INTO recipes VALUES(?,?,?) ON CONFLICT(sku) DO UPDATE SET bases_json=excluded.bases_json,version=excluded.version',(sku,after,expected+1))
            db.execute('INSERT INTO recipe_history(sku,actor,reason,occurred_at,before_json,after_json) VALUES(?,?,?,?,?,?)',(sku,actor,reason,now(),json.dumps(product['bases'],ensure_ascii=False),after))
        return dict(product,bases=cleaned,version=expected+1)

    def stock_history(self):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM stock_history ORDER BY id DESC LIMIT 100")]
        for row in rows:
            row["before"] = json.loads(row.pop("before_json") or "null")
            row["after"] = json.loads(row.pop("after_json"))
        return rows

    def update_stock(self, data, actor, reason, version):
        if not isinstance(data, dict):
            raise DomainError("Se espera un conteo ficticio.")
        flavor = clean_text(data.get("flavor"), "Sabor", maximum=80)
        size = clean_text(data.get("size"), "Tamaño", maximum=80)
        if not any((p['flavor'],p['size']) == (flavor,size) for p in self.catalog()):
            raise DomainError('Selecciona un producto entero del catálogo local para el conteo.')
        physical, reserved = data.get("physical"), data.get("reserved")
        if physical is not None and (type(physical) is not int or not 0 <= physical <= 999):
            raise DomainError("Stock físico: usa 0 a 999 enteras o deja desconocido.")
        if type(reserved) is not int or not 0 <= reserved <= 999:
            raise DomainError("Reserva vitrina: usa una cantidad entera entre 0 y 999.")
        if physical is not None and reserved > physical:
            raise DomainError("La reserva para vitrina no puede superar el conteo físico.")
        actor = clean_text(actor, "Responsable", maximum=80)
        reason = clean_text(reason, "Motivo", maximum=500)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM stock WHERE flavor=? AND size=?", (flavor, size)).fetchone()
            before = dict(row) if row else None
            expected = before["version"] if before else 0
            if type(version) is not int or version != expected:
                raise DomainError("El conteo cambió. Cierra el panel y actualiza antes de continuar.", 409)
            stamp = now()
            db.execute("INSERT INTO stock(flavor,size,physical,reserved,version,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(flavor,size) DO UPDATE SET physical=excluded.physical,reserved=excluded.reserved,version=excluded.version,updated_at=excluded.updated_at", (flavor,size,physical,reserved,expected+1,stamp))
            after = dict(db.execute("SELECT * FROM stock WHERE flavor=? AND size=?", (flavor,size)).fetchone())
            db.execute("INSERT INTO stock_history(actor,reason,occurred_at,before_json,after_json) VALUES(?,?,?,?,?)", (actor,reason,stamp,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False)))
            return after
