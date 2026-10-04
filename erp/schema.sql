PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS order_customer_changes (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    customer TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    pickup_at TEXT NOT NULL,
    fulfillment TEXT NOT NULL CHECK(fulfillment IN ('retiro','despacho')),
    delivery_address TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('demo', 'demo-toteat', 'toteat', 'manual-sos')),
    source_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    customer TEXT NOT NULL,
    pickup_at TEXT NOT NULL,
    fulfillment TEXT NOT NULL CHECK(fulfillment IN ('retiro','despacho')),
    comments TEXT NOT NULL DEFAULT '',
    payment_confirmed INTEGER NOT NULL CHECK(payment_confirmed IN (0,1)),
    is_demo INTEGER NOT NULL DEFAULT 1 CHECK(is_demo IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    source_item_id TEXT NOT NULL,
    flavor TEXT NOT NULL,
    size TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    kind TEXT NOT NULL CHECK(kind = 'torta'),
    comments TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pendiente' CHECK(status IN ('pendiente','solicitado','marcado_solicitado','marcado','entregado','cancelado')),
    UNIQUE(order_id, source_item_id)
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    item_id INTEGER REFERENCES items(id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_pickup ON orders(pickup_at);
CREATE INDEX IF NOT EXISTS history_order ON history(order_id, id);
CREATE TABLE IF NOT EXISTS stock (
    flavor TEXT NOT NULL,
    size TEXT NOT NULL,
    physical INTEGER CHECK(physical IS NULL OR physical >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(flavor,size),
    CHECK(physical IS NULL OR reserved <= physical)
);
CREATE TABLE IF NOT EXISTS stock_history (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (sku TEXT PRIMARY KEY, bases_json TEXT, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','verified')),
    reference TEXT
);
CREATE TABLE IF NOT EXISTS recipe_history (
    id INTEGER PRIMARY KEY, sku TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_products (
    sku TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('toteat-manual','manual-demo')),
    source_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    UNIQUE(source,source_id)
);
CREATE TABLE IF NOT EXISTS catalog_imports (
    digest TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    added INTEGER NOT NULL,
    unchanged INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_scheduling (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    timing TEXT NOT NULL CHECK(timing IN ('scheduled','immediate','unclassified')),
    customer_phone TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS simulation_orders (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS toteat_scheduling (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    source_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_attachments (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    payment_id TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_scheduling (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    request_id TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','scheduled')),
    payment_status TEXT NOT NULL CHECK(payment_status IN ('unpaid','paid_manual','paid','discount_settled')),
    payment_evidence TEXT NOT NULL,
    partner_username TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS toteat_order_links (
    source_id TEXT PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    snapshot_json TEXT NOT NULL,
    partner_username TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_reviews (
    source_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','linked','distinct')),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reception_sources (
    source_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reception_decisions (
    source_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL CHECK(decision IN ('scheduled','immediate','review')),
    order_id INTEGER REFERENCES orders(id),
    partner_username TEXT NOT NULL,
    reason TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reception_events (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    order_id INTEGER REFERENCES orders(id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reception_events_source ON reception_events(source_id,id);
CREATE TABLE IF NOT EXISTS order_source_alerts (
    source_id TEXT PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    state TEXT NOT NULL CHECK(state IN ('cancelled','partial_cancel','refund_review')),
    digest TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    resolution TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
