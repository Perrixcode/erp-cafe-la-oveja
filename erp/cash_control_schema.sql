-- Additive. No opening balances, transactions or historical bags are seeded.
CREATE TABLE IF NOT EXISTS cc_settings (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), start_date TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO cc_settings(singleton,start_date) VALUES(1,'2026-10-16');
CREATE TABLE IF NOT EXISTS cc_source_reads (
 id INTEGER PRIMARY KEY, business_date TEXT NOT NULL, endpoint TEXT NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL, read_at TEXT NOT NULL,
 scope TEXT NOT NULL, payload_hash TEXT NOT NULL, normalized_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cc_source_day ON cc_source_reads(business_date,endpoint,id);
CREATE TABLE IF NOT EXISTS cc_closure_links (
 bag_id TEXT PRIMARY KEY REFERENCES cash_bags(id), session_key TEXT NOT NULL UNIQUE,
 source_read_id INTEGER NOT NULL REFERENCES cc_source_reads(id),
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cc_locations (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL
 CHECK(kind IN ('register','safe','custody','transit')),
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cc_openings (
 location_id TEXT PRIMARY KEY REFERENCES cc_locations(id), amount INTEGER NOT NULL CHECK(amount>=0),
 as_of TEXT NOT NULL, evidence TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cc_cash_events (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('transfer','income','expense')),
 amount INTEGER NOT NULL CHECK(amount>0),
 from_id TEXT REFERENCES cc_locations(id), to_id TEXT REFERENCES cc_locations(id),
 occurred_at TEXT NOT NULL, business_date TEXT NOT NULL,
 reference TEXT NOT NULL UNIQUE, note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 CHECK((kind='transfer' AND from_id IS NOT NULL AND to_id IS NOT NULL AND from_id<>to_id)
 OR (kind='income' AND from_id IS NULL AND to_id IS NOT NULL)
 OR (kind='expense' AND from_id IS NOT NULL AND to_id IS NULL))
);
CREATE INDEX IF NOT EXISTS cc_events_time ON cc_cash_events(occurred_at);
CREATE TABLE IF NOT EXISTS cc_payment_links (
 event_id TEXT NOT NULL REFERENCES cc_cash_events(id),
 payment_source TEXT NOT NULL CHECK(payment_source IN ('ap_payments','ap_account_entries')),
 payment_id INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
 actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(event_id,payment_source,payment_id)
);
CREATE TABLE IF NOT EXISTS cc_requests (
 request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cc_audit (
 id INTEGER PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);

-- Global starting points and source classifications are annotations, not cash events.
CREATE TABLE IF NOT EXISTS cc_balance_origins (
 id TEXT PRIMARY KEY, cutoff_day TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
 evidence TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 supersedes TEXT UNIQUE REFERENCES cc_balance_origins(id)
);
CREATE TABLE IF NOT EXISTS cc_income_classifications (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES cc_cash_events(id),
 classification TEXT NOT NULL CHECK(classification IN ('other_external','already_in_sales')),
 evidence TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 supersedes TEXT UNIQUE REFERENCES cc_income_classifications(id)
);
CREATE INDEX IF NOT EXISTS cc_income_event ON cc_income_classifications(event_id);
CREATE TRIGGER IF NOT EXISTS cc_balance_origin_no_update BEFORE UPDATE ON cc_balance_origins BEGIN SELECT RAISE(ABORT,'Immutable starting point'); END;
CREATE TRIGGER IF NOT EXISTS cc_balance_origin_no_delete BEFORE DELETE ON cc_balance_origins BEGIN SELECT RAISE(ABORT,'Immutable starting point'); END;
CREATE TRIGGER IF NOT EXISTS cc_income_classification_no_update BEFORE UPDATE ON cc_income_classifications BEGIN SELECT RAISE(ABORT,'Immutable classification'); END;
CREATE TRIGGER IF NOT EXISTS cc_income_classification_no_delete BEFORE DELETE ON cc_income_classifications BEGIN SELECT RAISE(ABORT,'Immutable classification'); END;
