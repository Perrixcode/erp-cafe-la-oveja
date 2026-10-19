PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS transfer_exports (
    request_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, actor TEXT NOT NULL,
    filters_json TEXT NOT NULL, row_count INTEGER NOT NULL, content_sha256 TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS delivery_days (
    day TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending', requested_at TEXT,
    last_success TEXT, last_attempt TEXT, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS delivery_transactions (
    source_key TEXT PRIMARY KEY, source_day TEXT NOT NULL, order_id TEXT NOT NULL,
    payment_id TEXT NOT NULL, fee INTEGER NOT NULL, sale_paid INTEGER NOT NULL,
    payload_json TEXT NOT NULL, digest TEXT NOT NULL, last_seen TEXT NOT NULL,
    source_missing INTEGER NOT NULL DEFAULT 0 CHECK(source_missing IN (0,1))
);
CREATE INDEX IF NOT EXISTS delivery_transactions_day ON delivery_transactions(source_day);
CREATE TABLE IF NOT EXISTS delivery_finance_history (
    id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, occurred_at TEXT NOT NULL,
    before_json TEXT, after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_sync_requests (
    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, kind TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_chat_batches (
    source_sha256 TEXT PRIMARY KEY, month TEXT NOT NULL UNIQUE,
    package_sha256 TEXT NOT NULL, library_file_id TEXT NOT NULL,
    source_library_file_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
    summary_json TEXT NOT NULL, imported_at TEXT NOT NULL, actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_chat_records (
    source_sha256 TEXT NOT NULL REFERENCES delivery_chat_batches(source_sha256),
    record_id TEXT NOT NULL, day TEXT NOT NULL, driver TEXT, amount INTEGER,
    analysis_status TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(source_sha256,record_id)
);
CREATE TABLE IF NOT EXISTS delivery_chat_messages (
    source_sha256 TEXT NOT NULL REFERENCES delivery_chat_batches(source_sha256),
    message_ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(source_sha256,message_ordinal)
);
CREATE TABLE IF NOT EXISTS delivery_audit_batches (
  month TEXT PRIMARY KEY, package_sha256 TEXT NOT NULL, library_file_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, imported_at TEXT NOT NULL, actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_audit_documents (
  month TEXT NOT NULL, document_key TEXT NOT NULL, driver TEXT NOT NULL, filename TEXT NOT NULL,
  sha256 TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY(month, document_key),
  FOREIGN KEY(month) REFERENCES delivery_audit_batches(month)
);

CREATE TABLE IF NOT EXISTS delivery_adjustments (
  id TEXT PRIMARY KEY, month TEXT NOT NULL REFERENCES delivery_chat_batches(month), driver TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('addition','deduction')), amount INTEGER NOT NULL CHECK(amount > 0),
  concept TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  void_request_id TEXT UNIQUE, void_actor TEXT, void_at TEXT, void_reason TEXT
);
CREATE INDEX IF NOT EXISTS delivery_adjustments_period ON delivery_adjustments(month, driver);
-- Initial supplier balances are documentary evidence, separate from future payments.
CREATE TABLE IF NOT EXISTS supplier_opening_batches (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, source_title TEXT NOT NULL,
    source_modified TEXT NOT NULL, imported_at TEXT NOT NULL, actor TEXT NOT NULL,
    original BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS supplier_opening_records (
    batch_id TEXT NOT NULL REFERENCES supplier_opening_batches(id), source_row INTEGER NOT NULL,
    supplier TEXT NOT NULL, original_json TEXT NOT NULL,
    PRIMARY KEY(batch_id,source_row)
);
CREATE TABLE IF NOT EXISTS supplier_opening_annotations (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES supplier_opening_batches(id),
    target_kind TEXT NOT NULL CHECK(target_kind IN ('row','supplier')), target TEXT NOT NULL,
    condition TEXT NOT NULL CHECK(condition IN ('inherit','unknown','credit','cash')),
    note TEXT NOT NULL, version INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(batch_id,target_kind,target,version)
);

CREATE TABLE IF NOT EXISTS marking_message_copies (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL, scope TEXT NOT NULL,
    previous_event INTEGER NOT NULL, through_event INTEGER NOT NULL,
    message_sha256 TEXT NOT NULL, copy_key TEXT NOT NULL, copied_at TEXT NOT NULL,
    UNIQUE(username,scope,copy_key)
);

-- Task assignment MVP: recipients are not login accounts; lists keep their own snapshots.
CREATE TABLE IF NOT EXISTS task_catalog (
    id INTEGER PRIMARY KEY, area TEXT NOT NULL CHECK(area IN ('kitchen','floor')),
    title TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
    unit TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS task_workers (
    id INTEGER PRIMARY KEY, area TEXT NOT NULL CHECK(area IN ('kitchen','floor')),
    name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS task_lists (
    id INTEGER PRIMARY KEY, area TEXT NOT NULL CHECK(area IN ('kitchen','floor')),
    work_date TEXT NOT NULL, worker_id INTEGER NOT NULL REFERENCES task_workers(id),
    worker_name TEXT NOT NULL, items_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    copied_from INTEGER REFERENCES task_lists(id), updated_at TEXT NOT NULL, actor TEXT NOT NULL,
    UNIQUE(area,work_date,worker_id)
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
    before_json TEXT, after_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_requests (
    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result_json TEXT NOT NULL
);
-- Operational accounts payable. Opening evidence remains in its original tables.
CREATE TABLE IF NOT EXISTS ap_suppliers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, tax_id TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    bank TEXT NOT NULL DEFAULT '', account_type TEXT NOT NULL DEFAULT '', account_number TEXT NOT NULL DEFAULT '',
    account_holder TEXT NOT NULL DEFAULT '', account_tax_id TEXT NOT NULL DEFAULT '',
    terms TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ap_supplier_tax ON ap_suppliers(tax_id) WHERE tax_id<>'';
CREATE TABLE IF NOT EXISTS ap_invoices (
    id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES ap_suppliers(id),
    document_type TEXT NOT NULL CHECK(document_type IN ('invoice','exempt','receipt','other')),
    folio TEXT NOT NULL, issued_on TEXT NOT NULL, due_on TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL CHECK(amount>0), description TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL CHECK(condition IN ('credit','cash','unknown')),
    scheduled_on TEXT NOT NULL DEFAULT '', planned_amount INTEGER NOT NULL DEFAULT 0 CHECK(planned_amount>=0),
    voided_at TEXT, payment_review INTEGER NOT NULL DEFAULT 0 CHECK(payment_review IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(supplier_id,document_type,folio)
);
CREATE TABLE IF NOT EXISTS ap_payments (
    id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id),
    amount INTEGER NOT NULL CHECK(amount>0), paid_on TEXT NOT NULL,
    method TEXT NOT NULL CHECK(method IN ('transfer','cash','card','other')),
    reference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL, created_at TEXT NOT NULL, voided_at TEXT, void_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ap_files (
    id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id),
    payment_id INTEGER REFERENCES ap_payments(id), name TEXT NOT NULL, mime TEXT NOT NULL,
    sha256 TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=8388608),
    content BLOB NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ap_files_invoice ON ap_files(invoice_id);
CREATE INDEX IF NOT EXISTS ap_payments_invoice ON ap_payments(invoice_id);
CREATE TABLE IF NOT EXISTS ap_events (
    id INTEGER PRIMARY KEY, supplier_id INTEGER REFERENCES ap_suppliers(id),
    invoice_id INTEGER REFERENCES ap_invoices(id), action TEXT NOT NULL, actor TEXT NOT NULL,
    reason TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_requests (
    request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ap_import_batches (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, content BLOB NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_import_reviews (
    batch_id TEXT PRIMARY KEY REFERENCES ap_import_batches(id),
    version INTEGER NOT NULL DEFAULT 1,
    total_rows INTEGER NOT NULL,
    draft_json TEXT NOT NULL DEFAULT '{}',
    replaced_by TEXT REFERENCES ap_import_batches(id),
    closed_at TEXT, closed_by TEXT,
    actor TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_invoice_sources (
    invoice_id INTEGER PRIMARY KEY REFERENCES ap_invoices(id),
    batch_id TEXT NOT NULL REFERENCES ap_import_batches(id), source_row INTEGER NOT NULL,
    original_json TEXT NOT NULL, source_tax_id TEXT NOT NULL, source_type TEXT NOT NULL, source_folio TEXT NOT NULL,
    source_amount INTEGER NOT NULL, source_date TEXT NOT NULL, UNIQUE(batch_id,source_row)
);

CREATE TABLE IF NOT EXISTS ap_initial_baselines (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, original BLOB NOT NULL,
    total INTEGER NOT NULL, rows INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_import_resolutions (
    batch_id TEXT NOT NULL REFERENCES ap_import_batches(id), source_row INTEGER NOT NULL,
    invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id),
    action TEXT NOT NULL CHECK(action IN ('create','link')),
    reason TEXT NOT NULL, actor TEXT NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(batch_id,source_row)
);
CREATE TABLE IF NOT EXISTS ap_baseline_links (
    invoice_id INTEGER PRIMARY KEY REFERENCES ap_invoices(id),
    baseline_id TEXT NOT NULL REFERENCES ap_initial_baselines(id),
    source_row INTEGER NOT NULL, original_json TEXT NOT NULL,
    UNIQUE(baseline_id,source_row)
);

-- Reversible removal from the SII review inbox; invoices and source evidence remain.
CREATE TABLE IF NOT EXISTS ap_import_trash (
    batch_id TEXT PRIMARY KEY REFERENCES ap_import_batches(id),
    deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL
);

-- Supplier current account v1: additive; legacy ap_payments remain authoritative.
CREATE TABLE IF NOT EXISTS ap_account_entries (
    id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES ap_suppliers(id),
    kind TEXT NOT NULL CHECK(kind IN ('payment','credit_note')),
    amount INTEGER NOT NULL CHECK(amount>0), currency TEXT NOT NULL CHECK(currency='CLP'),
    occurred_on TEXT NOT NULL, method TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
    folio TEXT NOT NULL DEFAULT '', origin_invoice_id INTEGER REFERENCES ap_invoices(id),
    notes TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, created_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, voided_at TEXT, void_reason TEXT NOT NULL DEFAULT '', voided_by TEXT NOT NULL DEFAULT '',
    CHECK((kind='payment' AND origin_invoice_id IS NULL AND folio='') OR
          (kind='credit_note' AND origin_invoice_id IS NOT NULL AND folio<>''))
);
CREATE UNIQUE INDEX IF NOT EXISTS ap_account_credit_folio
    ON ap_account_entries(supplier_id,folio) WHERE kind='credit_note';
CREATE TABLE IF NOT EXISTS ap_account_applications (
    id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES ap_account_entries(id),
    invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id), amount INTEGER NOT NULL CHECK(amount>0),
    actor TEXT NOT NULL, created_at TEXT NOT NULL, voided_at TEXT, void_reason TEXT NOT NULL DEFAULT '', voided_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ap_account_application_invoice ON ap_account_applications(invoice_id);
CREATE INDEX IF NOT EXISTS ap_account_application_entry ON ap_account_applications(entry_id);
CREATE TABLE IF NOT EXISTS ap_account_files (
    id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES ap_account_entries(id),
    name TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=8388608), content BLOB NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(entry_id,sha256)
);

-- Reviewed weekly planning only: no payment or stock movement is created.
CREATE TABLE IF NOT EXISTS ap_invoice_holds (
    id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id),
    reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    released_at TEXT, released_by TEXT NOT NULL DEFAULT '', release_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ap_invoice_active_hold ON ap_invoice_holds(invoice_id) WHERE released_at IS NULL;
CREATE TABLE IF NOT EXISTS ap_payment_proposals (
    id INTEGER PRIMARY KEY, week_start TEXT NOT NULL, week_end TEXT NOT NULL,
    budget INTEGER NOT NULL CHECK(budget>=0), reserved_amount INTEGER NOT NULL CHECK(reserved_amount>=0),
    paid_amount INTEGER NOT NULL CHECK(paid_amount>=0),
    total INTEGER NOT NULL CHECK(total>0), scheduled_on TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL, selected_json TEXT NOT NULL
);

-- Procurement has its own ledger. It never writes provisional cake stock.
CREATE TABLE IF NOT EXISTS pc_units (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    dimension TEXT NOT NULL, precision INTEGER NOT NULL CHECK(precision BETWEEN 0 AND 6),
    actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_warehouses (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_items (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    base_unit_id INTEGER NOT NULL REFERENCES pc_units(id),
    version INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_equivalences (
    id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES pc_items(id),
    unit_id INTEGER NOT NULL REFERENCES pc_units(id), base_ratio_micros INTEGER NOT NULL CHECK(base_ratio_micros>0),
    reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(item_id,unit_id)
);
CREATE TABLE IF NOT EXISTS pc_orders (
    id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES ap_suppliers(id),
    warehouse_id INTEGER NOT NULL REFERENCES pc_warehouses(id),
    ordered_on TEXT NOT NULL, expected_on TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
    reference_key TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    cancelled_at TEXT, cancelled_by TEXT NOT NULL DEFAULT '', cancel_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS pc_order_reference ON pc_orders(supplier_id,reference_key) WHERE reference_key<>'';
CREATE TABLE IF NOT EXISTS pc_order_lines (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES pc_orders(id),
    item_id INTEGER NOT NULL REFERENCES pc_items(id), unit_id INTEGER NOT NULL REFERENCES pc_units(id),
    item_name TEXT NOT NULL, unit_code TEXT NOT NULL, base_unit_id INTEGER NOT NULL REFERENCES pc_units(id),
    base_ratio_micros INTEGER NOT NULL CHECK(base_ratio_micros>0),
    ordered_micros INTEGER NOT NULL CHECK(ordered_micros>0), cancelled_micros INTEGER NOT NULL DEFAULT 0 CHECK(cancelled_micros>=0),
    UNIQUE(order_id,item_id), CHECK(cancelled_micros<=ordered_micros)
);
CREATE TABLE IF NOT EXISTS pc_receipts (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES pc_orders(id),
    received_on TEXT NOT NULL, reference TEXT NOT NULL, reference_key TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, created_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, voided_at TEXT, voided_by TEXT NOT NULL DEFAULT '', void_reason TEXT NOT NULL DEFAULT '',
    replaces_receipt_id INTEGER REFERENCES pc_receipts(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS pc_active_receipt_reference ON pc_receipts(order_id,reference_key) WHERE voided_at IS NULL;
CREATE TABLE IF NOT EXISTS pc_receipt_lines (
    id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL REFERENCES pc_receipts(id),
    order_line_id INTEGER NOT NULL REFERENCES pc_order_lines(id),
    accepted_micros INTEGER NOT NULL CHECK(accepted_micros>=0), rejected_micros INTEGER NOT NULL CHECK(rejected_micros>=0),
    reason TEXT NOT NULL DEFAULT '', UNIQUE(receipt_id,order_line_id), CHECK(accepted_micros+rejected_micros>0)
);
CREATE TABLE IF NOT EXISTS pc_stock_documents (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('receipt','issue','waste','adjustment','transfer','reversal')),
    occurred_on TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    receipt_id INTEGER UNIQUE REFERENCES pc_receipts(id), reversal_of INTEGER UNIQUE REFERENCES pc_stock_documents(id),
    CHECK((kind='receipt' AND receipt_id IS NOT NULL AND reversal_of IS NULL) OR
          (kind='reversal' AND reversal_of IS NOT NULL AND receipt_id IS NULL) OR
          (kind IN ('issue','waste','adjustment','transfer') AND receipt_id IS NULL AND reversal_of IS NULL))
);
CREATE TABLE IF NOT EXISTS pc_stock_lines (
    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES pc_stock_documents(id),
    item_id INTEGER NOT NULL REFERENCES pc_items(id), warehouse_id INTEGER NOT NULL REFERENCES pc_warehouses(id),
    base_unit_id INTEGER NOT NULL REFERENCES pc_units(id), quantity_micros INTEGER NOT NULL CHECK(quantity_micros<>0),
    receipt_line_id INTEGER UNIQUE REFERENCES pc_receipt_lines(id),
    reverses_line_id INTEGER UNIQUE REFERENCES pc_stock_lines(id), CHECK(receipt_line_id IS NULL OR reverses_line_id IS NULL)
);
CREATE INDEX IF NOT EXISTS pc_stock_balance ON pc_stock_lines(item_id,warehouse_id,id);
CREATE TABLE IF NOT EXISTS pc_invoice_links (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES pc_orders(id), invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id),
    actor TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL,
    voided_at TEXT, voided_by TEXT NOT NULL DEFAULT '', void_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS pc_active_invoice_link ON pc_invoice_links(order_id,invoice_id) WHERE voided_at IS NULL;
CREATE TABLE IF NOT EXISTS pc_issues (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES pc_orders(id), receipt_id INTEGER REFERENCES pc_receipts(id),
    kind TEXT NOT NULL CHECK(kind IN ('quantity','quality','document','other')), detail TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    resolved_at TEXT, resolved_by TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pc_supplier_profiles (
    supplier_id INTEGER PRIMARY KEY REFERENCES ap_suppliers(id), products_text TEXT,
    order_days_json TEXT, cutoff_time TEXT, delivery_days_json TEXT, delivery_lead_days INTEGER,
    schedule_note TEXT, order_channel TEXT, order_contact TEXT, order_url TEXT, payment_term_days INTEGER,
    minimum_amount_clp INTEGER, minimum_quantity TEXT, minimum_unit_id INTEGER REFERENCES pc_units(id),
    substitute_ids_json TEXT, substitute_text TEXT, source_reference TEXT,
    reviewed INTEGER NOT NULL CHECK(reviewed IN (0,1)), version INTEGER NOT NULL DEFAULT 1,
    actor TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_events (
    id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, action TEXT NOT NULL,
    actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pc_events_entity ON pc_events(entity,entity_id,id);
CREATE TABLE IF NOT EXISTS pc_requests (
    request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);

-- Optional warehouse entries. Legacy receipt documents and lines remain intact.
CREATE TABLE IF NOT EXISTS pc_receipt_entries (
    id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL REFERENCES pc_receipts(id),
    stock_document_id INTEGER NOT NULL UNIQUE REFERENCES pc_stock_documents(id)
);
CREATE INDEX IF NOT EXISTS pc_receipt_entries_receipt ON pc_receipt_entries(receipt_id,id);
CREATE TABLE IF NOT EXISTS pc_receipt_entry_lines (
    id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES pc_receipt_entries(id),
    receipt_line_id INTEGER NOT NULL REFERENCES pc_receipt_lines(id),
    stock_line_id INTEGER NOT NULL UNIQUE REFERENCES pc_stock_lines(id),
    quantity_micros INTEGER NOT NULL CHECK(quantity_micros>0),
    UNIQUE(entry_id,receipt_line_id)
);

-- Supplier plan: verified ingredient counts, comparable invoice lines and replenishment.
CREATE TABLE IF NOT EXISTS pc_counts (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES pc_items(id), warehouse_id INTEGER NOT NULL REFERENCES pc_warehouses(id),
 document_id INTEGER NOT NULL UNIQUE REFERENCES pc_stock_documents(id), counted_micros INTEGER NOT NULL CHECK(counted_micros>=0),
 occurred_on TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_invoice_lines (
 id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id), order_line_id INTEGER NOT NULL REFERENCES pc_order_lines(id),
 quantity_micros INTEGER NOT NULL CHECK(quantity_micros>0), base_quantity_micros INTEGER NOT NULL CHECK(base_quantity_micros>0),
 amount_clp INTEGER NOT NULL CHECK(amount_clp>0), basis TEXT NOT NULL CHECK(basis IN ('net','gross')),
 invoice_signature TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 voided_at TEXT, voided_by TEXT, void_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pc_invoice_line_active ON pc_invoice_lines(invoice_id,order_line_id) WHERE voided_at IS NULL;
CREATE TABLE IF NOT EXISTS pc_reorder_policies (
 id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES pc_items(id), warehouse_id INTEGER NOT NULL REFERENCES pc_warehouses(id),
 supplier_id INTEGER NOT NULL REFERENCES ap_suppliers(id), unit_id INTEGER NOT NULL REFERENCES pc_units(id),
 minimum_micros INTEGER NOT NULL CHECK(minimum_micros>=0), target_micros INTEGER NOT NULL CHECK(target_micros>=minimum_micros),
 daily_micros INTEGER NOT NULL CHECK(daily_micros>=0), lead_days INTEGER NOT NULL CHECK(lead_days>=0),
 reason TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(item_id,warehouse_id)
);
CREATE TABLE IF NOT EXISTS ap_document_drafts (
 id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, name TEXT NOT NULL, mime TEXT NOT NULL,
 bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=8388608), content BLOB NOT NULL,
 extracted_text TEXT NOT NULL, extraction_status TEXT NOT NULL, extraction_note TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 invoice_id INTEGER REFERENCES ap_invoices(id), reviewed_by TEXT, reviewed_at TEXT,
 discarded_at TEXT, discard_reason TEXT
);

-- Reviewable purchasing intentions; availability is kept in supplier profiles.
CREATE TABLE IF NOT EXISTS pc_purchase_agenda (
 id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES ap_suppliers(id),
 origin_on TEXT NOT NULL, scheduled_on TEXT NOT NULL, expected_on TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending','postponed','done')), notes TEXT NOT NULL DEFAULT '',
 lines_json TEXT NOT NULL, order_id INTEGER UNIQUE REFERENCES pc_orders(id),
 external_reference TEXT NOT NULL DEFAULT '', completed_on TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(supplier_id,scheduled_on)
);
CREATE TABLE IF NOT EXISTS ap_document_reviews (
 draft_id INTEGER PRIMARY KEY REFERENCES ap_document_drafts(id), payload_json TEXT NOT NULL,
 actor TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_document_lines (
 id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES ap_document_drafts(id),
 invoice_id INTEGER NOT NULL REFERENCES ap_invoices(id), ordinal INTEGER NOT NULL,
 detail_json TEXT NOT NULL, item_id INTEGER REFERENCES pc_items(id), unit_id INTEGER REFERENCES pc_units(id),
 base_unit_id INTEGER REFERENCES pc_units(id), base_quantity_micros INTEGER,
 quantity_micros INTEGER NOT NULL CHECK(quantity_micros>0), amount_clp INTEGER NOT NULL CHECK(amount_clp>0),
 basis TEXT NOT NULL CHECK(basis IN ('net','gross')), invoice_signature TEXT NOT NULL,
 order_line_id INTEGER REFERENCES pc_order_lines(id), actor TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, voided_at TEXT, voided_by TEXT, void_reason TEXT,
 UNIQUE(invoice_id,ordinal)
);
