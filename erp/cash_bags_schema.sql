-- Additive migration. Rollback is disabling routes; keep assigned numbers and history.
CREATE TABLE IF NOT EXISTS cash_bag_sequence (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_number INTEGER NOT NULL CHECK(next_number>0)
);
INSERT OR IGNORE INTO cash_bag_sequence(singleton,next_number) VALUES(1,1);
CREATE TABLE IF NOT EXISTS cash_bag_batches (
  id TEXT PRIMARY KEY, first_number INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100),
  link_origin TEXT NOT NULL, is_test INTEGER NOT NULL CHECK(is_test IN(0,1)),
  actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_bags (
  id TEXT PRIMARY KEY, number INTEGER NOT NULL UNIQUE CHECK(number>0),
  batch_id TEXT NOT NULL REFERENCES cash_bag_batches(id),
  status TEXT NOT NULL DEFAULT 'blank' CHECK(status IN('blank','pending','verified','difference','void')),
  business_date TEXT, register_name TEXT, shift_name TEXT, responsible TEXT,
  counted INTEGER CHECK(counted>=0), retained INTEGER CHECK(retained>=0), bag_amount INTEGER CHECK(bag_amount>=0),
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  declared_at TEXT, declared_by TEXT, void_reason TEXT,
  verified_amount INTEGER CHECK(verified_amount>=0), verified_difference INTEGER,
  verified_at TEXT, verified_by TEXT, verification_note TEXT,
  CHECK((counted IS NULL AND retained IS NULL AND bag_amount IS NULL) OR
        (counted IS NOT NULL AND retained IS NOT NULL AND bag_amount IS NOT NULL AND retained<=counted AND bag_amount=counted-retained)),
  CHECK(status NOT IN('pending','verified','difference') OR (business_date IS NOT NULL AND register_name IS NOT NULL AND shift_name IS NOT NULL AND responsible IS NOT NULL AND counted IS NOT NULL)),
  CHECK(status NOT IN('verified','difference') OR (verified_amount IS NOT NULL AND verified_difference=verified_amount-bag_amount)),
  CHECK(status!='verified' OR verified_difference=0), CHECK(status!='difference' OR verified_difference<>0)
);
CREATE INDEX IF NOT EXISTS cash_bags_batch ON cash_bags(batch_id,number);
CREATE TABLE IF NOT EXISTS cash_bag_events (
  id INTEGER PRIMARY KEY, bag_id TEXT REFERENCES cash_bags(id), batch_id TEXT NOT NULL REFERENCES cash_bag_batches(id),
  action TEXT NOT NULL, actor TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_bag_requests (
  request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_bag_imports (
  import_key TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
  batch_id TEXT NOT NULL UNIQUE REFERENCES cash_bag_batches(id), actor TEXT NOT NULL, created_at TEXT NOT NULL
);
