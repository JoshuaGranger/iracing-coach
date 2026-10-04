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
