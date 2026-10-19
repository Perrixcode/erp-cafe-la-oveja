-- Proposal only. These tables are initialized only when the isolated feature is enabled.
CREATE TABLE IF NOT EXISTS bag_cashiers (
 id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 access_role TEXT NOT NULL DEFAULT 'cashier' CHECK(access_role IN('cashier','access_admin')),
 shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN(0,1)),
 enabled INTEGER NOT NULL CHECK(enabled IN(0,1)), version INTEGER NOT NULL DEFAULT 1,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bag_access_credentials (
 cashier_id TEXT PRIMARY KEY REFERENCES bag_cashiers(id), salt BLOB NOT NULL,
 password_hash BLOB NOT NULL, n INTEGER NOT NULL, r INTEGER NOT NULL, p INTEGER NOT NULL,
 version INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bag_access_enrollments (
 token_hash TEXT PRIMARY KEY, cashier_id TEXT NOT NULL REFERENCES bag_cashiers(id),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER
);
CREATE TABLE IF NOT EXISTS bag_access_sessions (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 cashier_id TEXT NOT NULL REFERENCES bag_cashiers(id), device_name TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS bag_access_sessions_cashier ON bag_access_sessions(cashier_id);
CREATE TABLE IF NOT EXISTS bag_access_limits (
 scope TEXT NOT NULL, window INTEGER NOT NULL, used INTEGER NOT NULL,
 PRIMARY KEY(scope,window)
);
CREATE TABLE IF NOT EXISTS bag_access_events (
 id INTEGER PRIMARY KEY, cashier_id TEXT, action TEXT NOT NULL,
 actor TEXT NOT NULL, created_at INTEGER NOT NULL, before_json TEXT, after_json TEXT
);
