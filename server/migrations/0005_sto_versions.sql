-- Imported STO occurrences remain immutable and locked in setup_files.
-- User-owned bytes live in SQLite so hourly and daily user-data snapshots include every version.
CREATE TABLE sto_working_files (
  file_id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES drivers(customer_id),
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  car_folder TEXT NOT NULL COLLATE NOCASE,
  parent_file_id TEXT NOT NULL,
  parent_version INTEGER NOT NULL,
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(customer_id, car_folder, name_key)
);
CREATE TABLE sto_revisions (
  file_id INTEGER NOT NULL REFERENCES sto_working_files(file_id),
  version INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  content BLOB NOT NULL CHECK(length(content) <= 16777216),
  document_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  message TEXT NOT NULL,
  based_on_version INTEGER,
  PRIMARY KEY(file_id, version)
);
-- Committed in the same transaction as the new revision, closing the response/crash retry gap.
CREATE TABLE sto_write_receipts (
  operation_id TEXT PRIMARY KEY,
  request_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL
);
