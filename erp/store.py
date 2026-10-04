"""Persistencia SQLite. Cada cambio y su historial se guardan juntos."""

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


def validate_order(data):
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
        validate_product(item)
        items.append(item)
    return dict(source=source, source_id=source_id, channel=channel,
                customer=clean_text(data.get("customer"), "Cliente"), pickup_at=pickup,
                fulfillment=fulfillment, comments=clean_text(data.get("comments", ""), "Comentarios", False, 1000),
                payment_confirmed=1, is_demo=1, items=items)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        order["status_label"] = order_status(order["items"])
        document = db.execute("SELECT source,status FROM documents WHERE order_id=?",(order_id,)).fetchone()
        order["receipt"] = dict(document) if document else {"source":"toteat","status":"pending"}
        return order

    def get(self, order_id):
        with self.connect() as db:
            return self._get(db, order_id)

    def list(self, start, end):
        with self.connect() as db:
            ids = db.execute("SELECT id FROM orders WHERE pickup_at>=? AND pickup_at<? ORDER BY pickup_at,id", (start + "T00:00", end + "T24:00")).fetchall()
            return [self._get(db, row["id"]) for row in ids]

    def _event(self, db, order_id, item_id, action, actor, reason, before, after):
        db.execute("INSERT INTO history(order_id,item_id,action,actor,occurred_at,reason,before_json,after_json) VALUES(?,?,?,?,?,?,?,?)",
                   (order_id, item_id, action, actor, now(), reason,
                    json.dumps(before, ensure_ascii=False) if before is not None else None,
                    json.dumps(after, ensure_ascii=False)))

    def create(self, data, actor):
        order = validate_order(data)
        actor = clean_text(actor, "Responsable", maximum=80)
        items = order.pop("items")
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
            for item in items:
                self._insert_item(db, order_id, item)
            after = self._get(db, order_id)
            self._event(db, order_id, None, "creado", actor, "Pedido ficticio con pago simulado confirmado", None, after)
            return after

    def _insert_item(self, db, order_id, item):
        columns = ["order_id"] + list(item)
        db.execute(f"INSERT INTO items({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [order_id] + list(item.values()))

    def _check_version(self, order, expected):
        if type(expected) is not int or order["version"] != expected:
            raise DomainError("El pedido cambió en otra ventana. Cierra el panel y recarga antes de continuar.", 409)

    def _touch(self, db, order_id):
        db.execute("UPDATE orders SET version=version+1,updated_at=? WHERE id=?", (now(), order_id))

    def update(self, order_id, data, actor, reason, version):
        updated = validate_order(data)
        actor = clean_text(actor, "Responsable", maximum=80)
        reason = clean_text(reason, "Motivo de corrección", maximum=500)
        new_items = updated.pop("items")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self._get(db, order_id)
            self._check_version(before, version)
            if (updated["source"], updated["source_id"]) != (before["source"], before["source_id"]):
                raise DomainError("Fuente e ID son inmutables para prevenir duplicados.")
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
                    self._insert_item(db, order_id, item)
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

    def notifications(self):
        # Una solicitud vigente por ítem, derivada del estado y su evento transaccional.
        # El historial conserva eventos resueltos. No se ejecuta despacho externo.
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT i.id AS item_id,o.id AS order_id,o.customer,o.pickup_at,
                i.flavor,i.size,i.quantity,h.actor,h.occurred_at,h.id AS event_id
                FROM items i JOIN orders o ON o.id=i.order_id
                JOIN history h ON h.id=(SELECT MAX(id) FROM history WHERE item_id=i.id)
                WHERE i.status='marcado_solicitado' ORDER BY o.pickup_at,i.id""")]

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
                if item['status'] != 'entregado': continue
                event = next((e for e in self.history(order['id']) if e['item_id']==item['id'] and e['action']=='estado' and e['after']['status']=='entregado'),None)
                if event:
                    delivered += 1
                    scheduled = datetime.fromisoformat(order['pickup_at']).replace(tzinfo=ZoneInfo('America/Santiago'))
                    on_time += datetime.fromisoformat(event['occurred_at']) <= scheduled
        return {'orders':len(orders),'units':sum(i['quantity'] for o in orders for i in o['items'] if i['kind']=='torta' and i['status']!='cancelado'),
                'channels':channels,'mark_samples':len(durations),'mark_minutes':round(sum(durations)/len(durations),1) if durations else None,
                'on_time':on_time,'on_time_total':delivered}

    def catalog(self):
        catalog = deepcopy(CATALOG)
        with self.connect() as db:
            updates = {row['sku']:dict(row) for row in db.execute('SELECT * FROM recipes')}
        for product in catalog:
            if product['sku'] in updates:
                update = updates[product['sku']]
                product.update(bases=json.loads(update['bases_json']),version=update['version'])
        return catalog

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
            cleaned.append({'name':clean_text(base.get('name'),'Base',maximum=80),'size':product['size'],'quantity':str(quantity)})
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
        if not any((p['flavor'],p['size']) == (flavor,size) for p in CATALOG):
            raise DomainError('Selecciona un producto entero del catálogo demo para el conteo.')
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
