-- Additive. No registers, amounts, users or operational closures are seeded.
CREATE TABLE IF NOT EXISTS cl_registers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider_register_id TEXT UNIQUE,
 location_id TEXT UNIQUE REFERENCES cc_locations(id), actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cl_closures (
 id TEXT PRIMARY KEY, business_date TEXT NOT NULL, register_id TEXT NOT NULL REFERENCES cl_registers(id),
 shift_name TEXT NOT NULL, shift_key TEXT NOT NULL, external_session_key TEXT UNIQUE,
 bag_id TEXT UNIQUE REFERENCES cash_bags(id), creator_key TEXT NOT NULL, responsible_json TEXT NOT NULL,
 last_editor TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN('draft','submitted','pending','reconciled')),
 version INTEGER NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 submitted_at TEXT, UNIQUE(business_date,register_id,shift_key)
);
CREATE TABLE IF NOT EXISTS cl_versions (
 closure_id TEXT NOT NULL REFERENCES cl_closures(id), version INTEGER NOT NULL,
 action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, state TEXT NOT NULL,
 data_json TEXT NOT NULL, calculation_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(closure_id,version)
);
CREATE TABLE IF NOT EXISTS cl_deliveries (
 closure_id TEXT PRIMARY KEY REFERENCES cl_closures(id), bag_id TEXT NOT NULL UNIQUE REFERENCES cash_bags(id),
 state TEXT NOT NULL CHECK(state IN('prepared','delivered','received')),
 delivered_at TEXT, delivered_by TEXT, received_at TEXT, received_by TEXT
);
CREATE TABLE IF NOT EXISTS cl_movement_claims (
 reference_key TEXT NOT NULL, register_scope TEXT NOT NULL,
 closure_id TEXT NOT NULL REFERENCES cl_closures(id), movement_id TEXT NOT NULL,
 PRIMARY KEY(reference_key,register_scope)
);
CREATE TABLE IF NOT EXISTS cl_source_reviews (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id), input_hash TEXT NOT NULL,
 opening_verified INTEGER NOT NULL, payments_verified INTEGER NOT NULL, movements_verified INTEGER NOT NULL,
 evidence TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cl_attachments (
 id TEXT PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 name TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
 content BLOB NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(closure_id,sha256)
);
CREATE TABLE IF NOT EXISTS cl_requests (
 request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cl_versions_no_update BEFORE UPDATE ON cl_versions BEGIN SELECT RAISE(ABORT,'Append-only closure history'); END;
CREATE TRIGGER IF NOT EXISTS cl_versions_no_delete BEFORE DELETE ON cl_versions BEGIN SELECT RAISE(ABORT,'Append-only closure history'); END;
CREATE TRIGGER IF NOT EXISTS cl_reviews_no_update BEFORE UPDATE ON cl_source_reviews BEGIN SELECT RAISE(ABORT,'Append-only review history'); END;
CREATE TRIGGER IF NOT EXISTS cl_reviews_no_delete BEFORE DELETE ON cl_source_reviews BEGIN SELECT RAISE(ABORT,'Append-only review history'); END;
CREATE TRIGGER IF NOT EXISTS cl_closure_no_delete BEFORE DELETE ON cl_closures BEGIN SELECT RAISE(ABORT,'Closure identity cannot be reused'); END;
CREATE TRIGGER IF NOT EXISTS cl_bag_sticky BEFORE UPDATE OF bag_id ON cl_closures WHEN OLD.bag_id IS NOT NULL AND NEW.bag_id IS NOT OLD.bag_id BEGIN SELECT RAISE(ABORT,'Bag cannot be released or reused'); END;
CREATE TRIGGER IF NOT EXISTS cl_original_sticky BEFORE UPDATE OF responsible_json,creator_key ON cl_closures WHEN NEW.responsible_json<>OLD.responsible_json OR NEW.creator_key<>OLD.creator_key BEGIN SELECT RAISE(ABORT,'Original responsible and creator are immutable'); END;
CREATE TRIGGER IF NOT EXISTS cl_claims_no_delete BEFORE DELETE ON cl_movement_claims BEGIN SELECT RAISE(ABORT,'Physical movement references cannot be reused'); END;
