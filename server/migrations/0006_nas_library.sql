-- NAS library: payloads are cataloged once, deletions are terminal until a verified share restore.
CREATE TABLE blobs_sync (
  sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('ibt','sto','htm','field','rpy')),
  first_name TEXT NOT NULL, source_mtime TEXT, collector_id TEXT REFERENCES collectors(collector_id),
  received_at TEXT NOT NULL, spool_present INTEGER NOT NULL DEFAULT 1,
  nas_state TEXT NOT NULL DEFAULT 'pending' CHECK(nas_state IN ('pending','archived','failed')),
  nas_archived_at TEXT, nas_attempts INTEGER NOT NULL DEFAULT 0, nas_error TEXT
);
INSERT INTO blobs_sync SELECT * FROM blobs;
DROP TABLE blobs;
ALTER TABLE blobs_sync RENAME TO blobs;
CREATE INDEX idx_blobs_nas ON blobs(nas_state);
CREATE TABLE blob_occurrences_sync (
  occurrence_id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('ibt','sto','htm','field','rpy')),
  file_name TEXT NOT NULL, relative_path TEXT, source_mtime TEXT,
  collector_id TEXT REFERENCES collectors(collector_id), customer_id INTEGER REFERENCES drivers(customer_id),
  seen_at TEXT NOT NULL, UNIQUE(sha256,collector_id,relative_path,file_name,source_mtime)
);
INSERT INTO blob_occurrences_sync SELECT * FROM blob_occurrences;
DROP TABLE blob_occurrences;
ALTER TABLE blob_occurrences_sync RENAME TO blob_occurrences;
CREATE TABLE uploads_sync (
  sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('ibt','sto','htm','field','rpy')),
  file_name TEXT NOT NULL, relative_path TEXT, source_mtime TEXT,
  collector_id TEXT REFERENCES collectors(collector_id), durable_offset INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK(state IN ('open','verified','failed','deleted')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT
);
INSERT INTO uploads_sync SELECT * FROM uploads;
DROP TABLE uploads;
ALTER TABLE uploads_sync RENAME TO uploads;
CREATE TABLE nas_library_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity TEXT,
  last_ok_at TEXT, last_error TEXT, object_cursor TEXT NOT NULL DEFAULT ''
);
INSERT INTO nas_library_meta(singleton) VALUES(1);
CREATE TABLE nas_library_objects (
  kind TEXT NOT NULL CHECK(kind IN ('ibt','sto','htm','rpy')), sha256 TEXT NOT NULL,
  size INTEGER NOT NULL CHECK(size>0), relative_path TEXT NOT NULL UNIQUE,
  legacy_path TEXT, state TEXT NOT NULL CHECK(state IN ('legacy','rename_pending','canonical_verified','conflict','deleted')),
  last_verified_at TEXT, deleted_at TEXT, error TEXT, verified_fingerprint TEXT,
  PRIMARY KEY(kind,sha256)
);
CREATE TABLE nas_inbox_receipts (
  customer_id INTEGER NOT NULL REFERENCES drivers(customer_id), car_folder TEXT NOT NULL,
  relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY(customer_id,car_folder,relative_path,sha256)
);
-- Relocation intent precedes the filesystem change; final occurrence+receipt commit together.
CREATE TABLE nas_inbox_pending (
  customer_id INTEGER NOT NULL REFERENCES drivers(customer_id), car_folder TEXT NOT NULL,
  relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
  PRIMARY KEY(customer_id,car_folder,relative_path,sha256)
);
