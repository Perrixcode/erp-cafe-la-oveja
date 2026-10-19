-- Additive pilot storage. All evidence remains in the existing backed-up ERP database.
CREATE TABLE IF NOT EXISTS cv2_drafts (
 closure_id TEXT PRIMARY KEY REFERENCES cl_closures(id),
 declaration_json TEXT NOT NULL, revision INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('draft','submitted')), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv2_submissions (
 id TEXT PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 number INTEGER NOT NULL, folio TEXT NOT NULL UNIQUE,
 snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
 signature_json TEXT NOT NULL, integrity_hash TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(closure_id,number)
);
CREATE TABLE IF NOT EXISTS cv2_reviews (
 token_hash TEXT PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 revision INTEGER NOT NULL, actor TEXT NOT NULL, fingerprint TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv2_events (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 submission_id TEXT REFERENCES cv2_submissions(id), kind TEXT NOT NULL,
 detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS cv2_one_deposit ON cv2_events(submission_id) WHERE kind='deposit_safe';
CREATE UNIQUE INDEX IF NOT EXISTS cv2_comparison_dedupe ON cv2_events(submission_id,detail_json) WHERE kind='late_comparison';
CREATE TABLE IF NOT EXISTS cv2_requests (
 actor TEXT NOT NULL, request_id TEXT NOT NULL, signature TEXT NOT NULL,
 response_json TEXT NOT NULL, PRIMARY KEY(actor,request_id)
);
CREATE TABLE IF NOT EXISTS cv2_images (
 attachment_id TEXT PRIMARY KEY REFERENCES cl_attachments(id),
 closure_id TEXT NOT NULL REFERENCES cl_closures(id), kind TEXT NOT NULL CHECK(kind IN ('mp','toteat')),
 width INTEGER NOT NULL, height INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cv2_cash_references (
 id INTEGER PRIMARY KEY, closure_id TEXT NOT NULL REFERENCES cl_closures(id),
 scope_json TEXT NOT NULL, components_json TEXT NOT NULL, expected INTEGER NOT NULL,
 evidence TEXT NOT NULL, attachment_id TEXT NOT NULL REFERENCES cl_attachments(id),
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
