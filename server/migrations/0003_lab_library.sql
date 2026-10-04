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
