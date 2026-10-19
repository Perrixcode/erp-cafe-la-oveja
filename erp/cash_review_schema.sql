-- Additive review workflow. No seed records or operational state changes.
CREATE TABLE IF NOT EXISTS cr_batches (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN('draft','confirmed')),
 version INTEGER NOT NULL, creator TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, confirmed_at TEXT, confirmed_by TEXT
);
CREATE TABLE IF NOT EXISTS cr_batch_items (
 batch_id TEXT NOT NULL REFERENCES cr_batches(id), bag_id TEXT NOT NULL REFERENCES cash_bags(id),
 closure_id TEXT REFERENCES cl_closures(id), closure_version INTEGER,
 bag_version INTEGER NOT NULL, declared_amount INTEGER NOT NULL, business_date TEXT NOT NULL,
 amount INTEGER CHECK(amount>=0), note TEXT NOT NULL, PRIMARY KEY(batch_id,bag_id)
);
CREATE TABLE IF NOT EXISTS cr_batch_versions (
 batch_id TEXT NOT NULL REFERENCES cr_batches(id), version INTEGER NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,version)
);
CREATE TABLE IF NOT EXISTS cr_counts (
 bag_id TEXT PRIMARY KEY REFERENCES cash_bags(id), closure_id TEXT REFERENCES cl_closures(id),
 batch_id TEXT REFERENCES cr_batches(id), amount INTEGER NOT NULL CHECK(amount>=0),
 declared_amount INTEGER NOT NULL, business_date TEXT NOT NULL, counted_at TEXT NOT NULL,
 actor TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cr_reviews (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 fingerprint TEXT NOT NULL, note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cr_difference_events (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 kind TEXT NOT NULL CHECK(kind IN('propose','accept','reopen')), proposal_id INTEGER,
 fingerprint TEXT NOT NULL, note TEXT NOT NULL, attachment_id TEXT REFERENCES cl_attachments(id),
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cr_method_sources (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 input_hash TEXT NOT NULL, reference TEXT NOT NULL, methods_json TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cr_requests (
 request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cr_batch_locked BEFORE UPDATE ON cr_batches WHEN OLD.state='confirmed' BEGIN SELECT RAISE(ABORT,'Confirmed batch is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cr_batch_no_delete BEFORE DELETE ON cr_batches BEGIN SELECT RAISE(ABORT,'Batch history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cr_item_insert_locked BEFORE INSERT ON cr_batch_items WHEN (SELECT state FROM cr_batches WHERE id=NEW.batch_id)='confirmed' BEGIN SELECT RAISE(ABORT,'Confirmed batch is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cr_item_update_locked BEFORE UPDATE ON cr_batch_items WHEN (SELECT state FROM cr_batches WHERE id=OLD.batch_id)='confirmed' OR (SELECT state FROM cr_batches WHERE id=NEW.batch_id)='confirmed' BEGIN SELECT RAISE(ABORT,'Confirmed batch is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cr_item_delete_locked BEFORE DELETE ON cr_batch_items WHEN (SELECT state FROM cr_batches WHERE id=OLD.batch_id)='confirmed' BEGIN SELECT RAISE(ABORT,'Confirmed batch is immutable'); END;

CREATE INDEX IF NOT EXISTS cr_reviews_closure ON cr_reviews(closure_id,id);
CREATE INDEX IF NOT EXISTS cr_difference_closure ON cr_difference_events(closure_id,id);
CREATE INDEX IF NOT EXISTS cr_sources_closure ON cr_method_sources(closure_id,id);
CREATE INDEX IF NOT EXISTS cr_count_time ON cr_counts(counted_at);
