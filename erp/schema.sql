PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('demo', 'demo-toteat')),
    source_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    customer TEXT NOT NULL,
    pickup_at TEXT NOT NULL,
    fulfillment TEXT NOT NULL CHECK(fulfillment IN ('retiro','despacho')),
    comments TEXT NOT NULL DEFAULT '',
    payment_confirmed INTEGER NOT NULL CHECK(payment_confirmed = 1),
    is_demo INTEGER NOT NULL DEFAULT 1 CHECK(is_demo = 1),
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
