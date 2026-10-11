-- iRacing Coach server SQLite schema, version 1.
-- File: /data/db/coach.sqlite3 on the mini PC NVMe. One writer thread (coach-server).
-- Connection pragmas: journal_mode=WAL, synchronous=FULL, foreign_keys=ON, busy_timeout=5000.
-- Migrations are additive (new tables, new nullable columns, new indexes) so the previous
-- release can run on a newer schema after a failed release (architecture.md section 10).
-- Times are UTC ISO-8601 text with 'Z'. Hashes are lowercase hex SHA-256.
-- User-authored rows carry `version` for If-Match optimistic concurrency.

CREATE TABLE schema_migrations (
  version      INTEGER PRIMARY KEY,
  applied_at   TEXT NOT NULL,
  description  TEXT NOT NULL,
  app_version  TEXT NOT NULL
);

-- Drivers, keyed by iRacing customer id (D7).
CREATE TABLE drivers (
  customer_id     INTEGER PRIMARY KEY,
  display_name    TEXT NOT NULL,
  latest_irating  INTEGER,
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL
);

CREATE TABLE collectors (
  collector_id       TEXT PRIMARY KEY,           -- UUID from collector.json
  machine_name       TEXT NOT NULL,
  collector_version  TEXT NOT NULL,
  protocol           INTEGER NOT NULL,
  first_seen_at      TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  last_customer_id   INTEGER REFERENCES drivers(customer_id)
);

-- Content-addressed files received from collectors (recordings and setup files).
CREATE TABLE blobs (
  sha256           TEXT PRIMARY KEY,
  size             INTEGER NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm')),
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
CREATE INDEX idx_blobs_nas ON blobs(nas_state);

-- Every POST /api/v1/uploads, including those whose bytes are already held (collector-protocol.md
-- section 4). Name, path and driver belong to the occurrence, not to the immutable blob.
CREATE TABLE blob_occurrences (
  occurrence_id  INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL,                     -- blob row may not exist yet (upload open)
  kind           TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm')),
  file_name      TEXT NOT NULL,
  relative_path  TEXT,
  source_mtime   TEXT,
  collector_id   TEXT REFERENCES collectors(collector_id),
  customer_id    INTEGER REFERENCES drivers(customer_id),  -- as reported, else the collector's last driver
  seen_at        TEXT NOT NULL,
  UNIQUE (sha256, collector_id, relative_path, file_name, source_mtime)
);

-- Uploads in progress (rows removed or closed when verified/failed).
CREATE TABLE uploads (
  sha256         TEXT PRIMARY KEY,
  size           INTEGER NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('ibt', 'sto', 'htm')),
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

-- One row per recording (.ibt blob), filled at ingest from the IBT header and session info.
CREATE TABLE recordings (
  sha256               TEXT PRIMARY KEY REFERENCES blobs(sha256),
  customer_id          INTEGER REFERENCES drivers(customer_id),
  ingest_state         TEXT NOT NULL CHECK (ingest_state IN ('verified', 'parsed', 'discovered', 'failed')),
  ingest_error         TEXT,
  record_count         INTEGER,
  tick_rate            INTEGER,
  session_info_sha256  TEXT,
  subsession_id        INTEGER,
  track                TEXT,
  track_config         TEXT,
  car_path             TEXT,
  car_name             TEXT,
  series_name          TEXT,
  season_name          TEXT,
  start_time_utc       TEXT,
  header_json          TEXT NOT NULL DEFAULT '{}',  -- header summary, unknown fields preserved
  parsed_at            TEXT
);
CREATE INDEX idx_recordings_driver ON recordings(customer_id, start_time_utc);
CREATE INDEX idx_recordings_subsession ON recordings(subsession_id);

-- Library rows: one per discovered session group (Python discovery: SubSessionID + sim_session_num).
-- A reconnected race spans several recordings. v1: each recording belongs to exactly one group,
-- the one discovery reports for it (the recording's final SessionNum, ibt_reader._read_edge_scalar);
-- earlier phases inside a continuous recording get no row of their own.
-- group_key is stored as the worker reports it, except that a path-dependent fallback key
-- (time_source = mtime, ibt_reader._fallback_group_key) is stored as 'local-file:<sha256>' and
-- analysed with the recording's job-root path as selector (worker-interface.md section 3).
CREATE TABLE sessions (
  session_id        INTEGER PRIMARY KEY,
  customer_id       INTEGER NOT NULL REFERENCES drivers(customer_id),
  subsession_id     INTEGER,
  sim_session_num   INTEGER,
  group_key         TEXT NOT NULL,                -- e.g. 'subsession:<id>:<num>' (worker selector)
  session_type      TEXT,                         -- Race, Qualify, Practice, Offline Testing, ...
  track             TEXT,
  car_path          TEXT,
  start_time_utc    TEXT,
  overview_json     TEXT NOT NULL DEFAULT '{}',   -- SessionSummary.overview (discovery + latest analysis)
  analysis_state    TEXT NOT NULL DEFAULT 'none'
                     CHECK (analysis_state IN ('none', 'queued', 'running', 'ready', 'failed', 'deferred')),
  analysis_reason   TEXT,                         -- e.g. exceeds_memory_budget
  current_analysis_id TEXT,
  discovered_at     TEXT NOT NULL,
  UNIQUE (customer_id, group_key)
);
CREATE INDEX idx_sessions_driver ON sessions(customer_id, start_time_utc DESC);
CREATE INDEX idx_sessions_event ON sessions(customer_id, subsession_id);

CREATE TABLE session_recordings (
  session_id        INTEGER NOT NULL REFERENCES sessions(session_id),
  recording_sha256  TEXT NOT NULL REFERENCES recordings(sha256),
  PRIMARY KEY (session_id, recording_sha256)
);

CREATE TABLE analyses (
  analysis_id          TEXT PRIMARY KEY,           -- from the worker
  session_id           INTEGER NOT NULL REFERENCES sessions(session_id),
  recordings_sha256    TEXT NOT NULL,              -- JSON array of the member recordings analysed
  membership_sha256    TEXT NOT NULL,              -- SHA-256 of the sorted member hashes joined by '\n'
  -- Becomes sessions.current_analysis_id only if membership_sha256 still equals the session's
  -- current membership digest when the job finishes; otherwise it is stored but not current.
  schema_version       INTEGER NOT NULL,
  profile_version      TEXT NOT NULL,              -- e.g. post-race-foundations-v23
  analysis_path        TEXT NOT NULL,              -- under /data/py/<customer_id>/archive/reports
  analysis_bytes       INTEGER NOT NULL,
  lap_count            INTEGER,
  created_at           TEXT NOT NULL,
  projection_version   INTEGER,                    -- view files built for this version
  projection_state     TEXT NOT NULL DEFAULT 'pending' CHECK (projection_state IN ('pending', 'ready', 'failed')),
  projection_error     TEXT,
  superseded_by        TEXT REFERENCES analyses(analysis_id)
);
CREATE INDEX idx_analyses_session ON analyses(session_id, created_at DESC);

-- Jobs for both worker lanes and server-side work.
CREATE TABLE jobs (
  job_id           INTEGER PRIMARY KEY,
  kind             TEXT NOT NULL CHECK (kind IN (
                     'discover', 'analyze', 'project', 'archive', 'restage',
                     'setup_files', 'setup_compare', 'baseline', 'tuning_recommend',
                     'tuning_outcome', 'tuning_history', 'import', 'snapshot', 'reanalyze')),
  lane             TEXT NOT NULL CHECK (lane IN ('analysis', 'light', 'io', 'server')),
  priority         INTEGER NOT NULL DEFAULT 0,       -- higher first; interactive = 100
  state            TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted', 'deferred')),
  idempotency_key  TEXT UNIQUE,                      -- analyze: 'analyze:<session_id>:<membership_sha256>:<analyzer bundle digest>:<profile>'
  customer_id      INTEGER REFERENCES drivers(customer_id),
  subject_json     TEXT NOT NULL DEFAULT '{}',
  attempts         INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  started_at       TEXT,
  finished_at      TEXT,
  error_code       TEXT,                             -- worker_oom, worker_crash, timeout, tool_error, exceeds_memory_budget, ...
  error_message    TEXT,
  result_json      TEXT
);
CREATE INDEX idx_jobs_queue ON jobs(state, lane, priority DESC, job_id);

-- Setup library (D9). A setup = distinct values fingerprint per driver and car (setup-keys.json).
CREATE TABLE setups (
  setup_id            INTEGER PRIMARY KEY,
  customer_id         INTEGER NOT NULL REFERENCES drivers(customer_id),
  car_path            TEXT NOT NULL,
  values_fingerprint  TEXT NOT NULL,
  values_json         TEXT NOT NULL,                 -- latest full CarSetup leaf map (strings as published)
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  UNIQUE (customer_id, car_path, values_fingerprint)
);

CREATE TABLE setup_observations (
  observation_id    INTEGER PRIMARY KEY,
  setup_id          INTEGER NOT NULL REFERENCES setups(setup_id),
  customer_id       INTEGER NOT NULL REFERENCES drivers(customer_id),
  source            TEXT NOT NULL CHECK (source IN ('recording', 'garage_snapshot', 'garage_commit', 'htm_export')),
  recording_sha256  TEXT REFERENCES recordings(sha256),
  visit_id          TEXT REFERENCES garage_visits(visit_id),
  blob_sha256       TEXT REFERENCES blobs(sha256),     -- the .htm for htm_export
  setup_name        TEXT,                              -- DriverSetupName; NULL = name unknown
  sto_sha256        TEXT,                              -- hash of the named .sto at commit time
  is_modified       INTEGER,                           -- DriverSetupIsModified; meaningful at garage exit and in recordings
  load_type         TEXT,                              -- DriverSetupLoadTypeName
  update_count      INTEGER,
  observed_at       TEXT NOT NULL
);
CREATE INDEX idx_setup_obs_setup ON setup_observations(setup_id, observed_at);
CREATE INDEX idx_setup_obs_name ON setup_observations(customer_id, setup_name);
-- Replayed garage events are acknowledged, never inserted twice (collector-protocol.md section 8).
CREATE UNIQUE INDEX uq_setup_obs_snapshot ON setup_observations(visit_id, update_count)
  WHERE source = 'garage_snapshot';
CREATE UNIQUE INDEX uq_setup_obs_commit ON setup_observations(visit_id)
  WHERE source = 'garage_commit';
-- A garage_commit or recording observation with is_modified <> 0 never sets a .sto's linked values.

CREATE TABLE garage_visits (
  visit_id         TEXT PRIMARY KEY,                   -- UUID from the collector
  collector_id     TEXT NOT NULL REFERENCES collectors(collector_id),
  customer_id      INTEGER REFERENCES drivers(customer_id),
  subsession_id    INTEGER,
  session_num      INTEGER,
  car_path         TEXT,
  entered_at       TEXT,
  exited_at        TEXT,
  committed_name   TEXT,
  committed_sto_sha256 TEXT,
  ambiguous_name   INTEGER NOT NULL DEFAULT 0
);

-- .sto/.htm files seen in the driver's setups folder; values unknown unless linked (D9).
CREATE TABLE setup_files (
  customer_id     INTEGER NOT NULL REFERENCES drivers(customer_id),
  blob_sha256     TEXT NOT NULL REFERENCES blobs(sha256),
  car_folder      TEXT NOT NULL,
  relative_path   TEXT NOT NULL,                       -- under setups/<car_folder>/, forward slashes
  format          TEXT NOT NULL CHECK (format IN ('sto', 'htm')),
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  present         INTEGER NOT NULL DEFAULT 1,           -- still in the folder at the last scan
  PRIMARY KEY (customer_id, car_folder, relative_path, blob_sha256)
);

-- User-authored data (D12 converts legacy data into these once).
CREATE TABLE notes (
  note_id        INTEGER PRIMARY KEY,
  customer_id    INTEGER NOT NULL REFERENCES drivers(customer_id),
  subject_kind   TEXT NOT NULL CHECK (subject_kind IN ('session', 'setup', 'analysis')),
  subject_id     TEXT NOT NULL,
  body           TEXT NOT NULL CHECK (length(body) <= 16384),
  version        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  legacy_source  TEXT
);
CREATE INDEX idx_notes_subject ON notes(customer_id, subject_kind, subject_id);

CREATE TABLE trace_layouts (
  customer_id  INTEGER NOT NULL REFERENCES drivers(customer_id),
  layout_id    TEXT NOT NULL,
  name         TEXT NOT NULL,
  body_json    TEXT NOT NULL CHECK (length(body_json) <= 65536),
  version      INTEGER NOT NULL DEFAULT 1,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (customer_id, layout_id)
);

CREATE TABLE live_layouts (
  customer_id  INTEGER NOT NULL REFERENCES drivers(customer_id),
  layout_id    TEXT NOT NULL,
  name         TEXT NOT NULL,
  body_json    TEXT NOT NULL CHECK (length(body_json) <= 65536),
  version      INTEGER NOT NULL DEFAULT 1,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (customer_id, layout_id)
);

CREATE TABLE driver_prefs (
  customer_id             INTEGER PRIMARY KEY REFERENCES drivers(customer_id),
  active_live_layout_id   TEXT,
  last_investigation_json TEXT,                     -- {session_id, analysis_id, view, comparison_id, section_id}
  prefs_json              TEXT NOT NULL DEFAULT '{}', -- theme accent, reduced motion, units, safe glance, active trace layout
  version                 INTEGER NOT NULL DEFAULT 1,
  updated_at              TEXT NOT NULL
);

CREATE TABLE tuning_drafts (
  customer_id   INTEGER NOT NULL REFERENCES drivers(customer_id),
  identity_key  TEXT NOT NULL,                      -- exact session identity (TuningSessionIdentity)
  body_json     TEXT NOT NULL CHECK (length(body_json) <= 262144),
  version       INTEGER NOT NULL DEFAULT 1,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (customer_id, identity_key)
);

CREATE TABLE turn_annotations (
  customer_id       INTEGER NOT NULL REFERENCES drivers(customer_id),
  track_config_key  TEXT NOT NULL,
  map_identity      TEXT NOT NULL,
  body_json         TEXT NOT NULL CHECK (length(body_json) <= 65536),
  version           INTEGER NOT NULL DEFAULT 1,
  updated_at        TEXT NOT NULL,
  PRIMARY KEY (customer_id, track_config_key, map_identity)
);
-- Tuning experiments and outcomes stay in the worker's per-driver history.sqlite3
-- (tuning_experiments); the server indexes them by job result only.

-- Parked features' data, preserved verbatim by D12 (saved Tests, plans, trials, activity).
CREATE TABLE legacy_items (
  item_id      INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES drivers(customer_id),
  kind         TEXT NOT NULL,                       -- saved_test, plan, trial, activity, setting, other
  source_path  TEXT NOT NULL,                       -- path in the legacy data root
  body_json    TEXT NOT NULL,
  imported_at  TEXT NOT NULL,
  import_id    INTEGER NOT NULL REFERENCES imports(import_id)
);

CREATE TABLE imports (
  import_id     INTEGER PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL UNIQUE,               -- idempotent: the same bundle imports once
  imported_at   TEXT NOT NULL,
  summary_json  TEXT NOT NULL
);

-- Browser outbox replay (api.openapi.yaml conventions). Rows older than 7 days are deleted.
CREATE TABLE request_log (
  idempotency_key  TEXT PRIMARY KEY,
  method           TEXT NOT NULL,
  path             TEXT NOT NULL,
  status           INTEGER NOT NULL,
  response_json    TEXT,
  created_at       TEXT NOT NULL
);

-- 'user_data_generation': incremented in the same transaction as every user-authored write, and by
-- every light job that writes the worker archive (tuning outcome, recommendation, import).
CREATE TABLE server_counters (
  name   TEXT PRIMARY KEY,
  value  INTEGER NOT NULL
);

CREATE TABLE snapshots (
  snapshot_id  INTEGER PRIMARY KEY,
  taken_at     TEXT NOT NULL,
  data_version INTEGER NOT NULL,                    -- user_data_generation captured (server_counters), not PRAGMA data_version
  local_path   TEXT NOT NULL,
  nas_day      TEXT,                                -- yyyy-mm-dd once copied
  bytes        INTEGER NOT NULL,
  state        TEXT NOT NULL CHECK (state IN ('local', 'archived', 'failed'))
);

INSERT INTO schema_migrations(version, applied_at, description, app_version)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'initial rewrite schema', '0.19.0');

-- Migration 2 (2026-10-04): garage snapshots wait for their visit's commit (collector-protocol.md
-- section 8, "Setup identity of a visit"). A snapshot never creates a setup: its values are kept
-- here, and the visit's garage.commit (the only event that creates a setup) makes each snapshot an
-- observation of the setup it equals, or of none. Additive: a server of version 1 ignores the table.
CREATE TABLE garage_snapshots (
  visit_id            TEXT NOT NULL REFERENCES garage_visits(visit_id),
  update_count        INTEGER NOT NULL,
  customer_id         INTEGER NOT NULL REFERENCES drivers(customer_id),
  car_path            TEXT NOT NULL,
  values_json         TEXT NOT NULL,                 -- CarSetup leaf map as published
  values_fingerprint  TEXT NOT NULL,                 -- setup-keys.json rule at arrival
  observed_at         TEXT NOT NULL,
  resolved_at         TEXT,                          -- NULL until the visit's commit is stored
  PRIMARY KEY (visit_id, update_count)
);
CREATE INDEX idx_garage_snapshots_pending ON garage_snapshots(visit_id) WHERE resolved_at IS NULL;

-- Migration 3 (2026-10-04): the Lab library (web-round-2-plan.md, "Lab library"; backlog #18). A
-- recording belongs to the main library (Joshua's own driving) or to the lab (the utility's bot,
-- its setup harvest and the other agent-driven rig runs); the driver stays who drove it and raw
-- files are never moved. A lab window is the utility's statement that it drives `car` at `track`
-- from `from_utc` until `until_utc`; a recording whose start falls in a window, whose car and track
-- match and which is a solo offline test is filed lab (server/src/library.rs). Additive: a server
-- of version 2 ignores the column and the table.
CREATE TABLE lab_windows (
  window_id    INTEGER PRIMARY KEY,
  from_utc     TEXT NOT NULL,                     -- ISO 8601 UTC, whole seconds
  until_utc    TEXT NOT NULL,
  car          TEXT NOT NULL,                     -- CarPath (e.g. 'stockcars2 supra2019') or the car's screen name
  track        TEXT NOT NULL,                     -- TrackName (e.g. 'irp'), TrackDisplayName or TrackID
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL,                     -- who stated it: 'testdriver', 'garage_harvest', 'historical', ...
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  CHECK (until_utc >= from_utc)
);
-- One window per start, car and track: posting the same three again updates it (closing a window).
CREATE UNIQUE INDEX idx_lab_windows_key ON lab_windows(from_utc, lower(car), lower(track));

ALTER TABLE recordings ADD COLUMN library TEXT NOT NULL DEFAULT 'main' CHECK (library IN ('main', 'lab'));
ALTER TABLE recordings ADD COLUMN library_window_id INTEGER REFERENCES lab_windows(window_id);  -- the window that filed it lab
CREATE INDEX idx_recordings_library ON recordings(library);

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
