-- Field uploads. migrate() disables foreign keys outside this transaction, then checks all references.


CREATE TABLE blobs_field (
  sha256           TEXT PRIMARY KEY,
  size             INTEGER NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm', 'field')),
  first_name       TEXT NOT NULL,                -- original file name at first receipt
  source_mtime     TEXT,
  collector_id     TEXT REFERENCES collectors(collector_id),
  received_at      TEXT NOT NULL,                -- verified (fsynced) on NVMe
  spool_present    INTEGER NOT NULL DEFAULT 1,   -- /data/spool/ready/<sha256> exists
  nas_state        TEXT NOT NULL DEFAULT 'pending' CHECK (nas_state IN ('pending', 'archived', 'failed')),
  nas_archived_at  TEXT,
  nas_attempts     INTEGER NOT NULL DEFAULT 0,
  nas_error        TEXT
);

INSERT INTO blobs_field SELECT * FROM blobs;

DROP TABLE blobs;

ALTER TABLE blobs_field RENAME TO blobs;

CREATE TABLE blob_occurrences_field (
  occurrence_id  INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL,                     -- blob row may not exist yet (upload open)
  kind           TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm', 'field')),
  file_name      TEXT NOT NULL,
  relative_path  TEXT,
  source_mtime   TEXT,
  collector_id   TEXT REFERENCES collectors(collector_id),
  customer_id    INTEGER REFERENCES drivers(customer_id),  -- as reported, else the collector's last driver
  seen_at        TEXT NOT NULL,
  UNIQUE (sha256, collector_id, relative_path, file_name, source_mtime)
);

INSERT INTO blob_occurrences_field SELECT * FROM blob_occurrences;

DROP TABLE blob_occurrences;

ALTER TABLE blob_occurrences_field RENAME TO blob_occurrences;

CREATE TABLE uploads_field (
  sha256         TEXT PRIMARY KEY,
  size           INTEGER NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm', 'field')),
  file_name      TEXT NOT NULL,
  relative_path  TEXT,
  source_mtime   TEXT,
  collector_id   TEXT REFERENCES collectors(collector_id),
  durable_offset INTEGER NOT NULL DEFAULT 0,
  state          TEXT NOT NULL CHECK (state IN ('open', 'verified', 'failed')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  error          TEXT
);

INSERT INTO uploads_field SELECT * FROM uploads;

DROP TABLE uploads;

ALTER TABLE uploads_field RENAME TO uploads;

CREATE INDEX idx_blobs_nas ON blobs(nas_state);

CREATE TABLE field_recordings (
  sha256 TEXT PRIMARY KEY REFERENCES blobs(sha256),
  customer_id INTEGER NOT NULL REFERENCES drivers(customer_id),
  subsession_id INTEGER,
  session_num INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  first_time REAL NOT NULL,
  last_time REAL NOT NULL
);
CREATE INDEX idx_field_session ON field_recordings(customer_id, subsession_id, session_num);
