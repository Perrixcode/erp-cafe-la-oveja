CREATE TABLE IF NOT EXISTS dc_sections (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
 position INTEGER NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dc_assets (
 sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>0),
 storage TEXT NOT NULL CHECK(storage IN ('database','legacy-salon','legacy-labels')), content BLOB,
 CHECK((storage='database' AND content IS NOT NULL) OR (storage IN ('legacy-salon','legacy-labels') AND content IS NULL))
);
CREATE TABLE IF NOT EXISTS dc_documents (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('document','checklist_template','checklist')),
 state TEXT NOT NULL CHECK(state IN ('draft','active','archived')),
 latest_version INTEGER NOT NULL, live_version INTEGER, revision INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dc_versions (
 document_id TEXT NOT NULL REFERENCES dc_documents(id), version INTEGER NOT NULL,
 section_id TEXT NOT NULL REFERENCES dc_sections(id), title TEXT NOT NULL, normalized_title TEXT NOT NULL,
 description TEXT NOT NULL, filename TEXT, asset_sha TEXT REFERENCES dc_assets(sha256), items_json TEXT,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(document_id,version),
 CHECK((asset_sha IS NOT NULL AND filename IS NOT NULL AND items_json IS NULL) OR (asset_sha IS NULL AND filename IS NULL AND items_json IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS dc_versions_section ON dc_versions(section_id,normalized_title);
CREATE TABLE IF NOT EXISTS dc_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL REFERENCES dc_documents(id),
 event TEXT NOT NULL, actor TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dc_runs (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
 responsible TEXT NOT NULL, performed_on TEXT NOT NULL, items_json TEXT NOT NULL, notes TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(document_id,version) REFERENCES dc_versions(document_id,version)
);
CREATE TABLE IF NOT EXISTS dc_requests (
 request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, operation TEXT NOT NULL,
 payload_sha TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
