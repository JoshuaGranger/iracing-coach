# Wave 1 follow-ups (2026-10-02)

Reconciliation of every contract change request in `wave1-results.md`, checked against the wave-1 code (`dd0ce49`), the C# and Python sources, and `owner-decisions-2026-10-02.md`. The rerun Codex ingest review (`ingest-review-codex-sol.md`) arrived during reconciliation; where it showed the contract silent or unsafe the contract was clarified, and every code finding is assigned below. Contract edits: `contracts/README.md`, change log entry "wave-1 reconciliation". After regeneration `cargo check --workspace` and `pnpm -C web typecheck` pass; the one known test break is W2 item 1.

## Dispositions

| Request | Decision | Reason |
|---|---|---|
| W1-1 `- Key:` item is an empty map | accept | Python stores `{}` there (`ibt_reader.py:417`). |
| W1-2 literal subset: i64 overflow, `\N{}`, surrogates, key and hash errors | accept | States what the port already does; Python raises or keeps text the same way. |
| W1-3 `_as_int` overflow, `driver()` vs `summary()` | accept | No Rust field holds a Python big int; `summary()` must keep Python's `None == None` match. |
| W1-4 `parse_live` bounds, future mtime | accept | Matches C# bounds; a future mtime is "too recent", age 0. |
| W2-1 cue bases | accept | Each string checked in `LiveTelemetry.cs` (725, 794-797, 822-834, 930-937); cue 14 has none. |
| W2-2 presence bit order, NaN session time | accept | Fixed by the independent frame vectors. |
| W2-3 unit display labels | accept with change | Added as table-level `unit_labels` plus generated `unit_label()`; no `TileText.unit`. The generator change is W2's. |
| W2-4 .NET text rules | accept | Without them the TS twin cannot match the overlay; table in `crate-api.md` 4.7. |
| W2-5 `LiveTile.unit` is a unit id | accept | Labels are display text; layouts must survive a label change. |
| W2 notes (channel_values, tow and pit-road presence, first_of_epoch, vectors log) | accept | Additive or descriptive; recorded. |
| W2 note: epoch unchanged at disconnect, unlike C# | keep contract | Only numbering differs; boundaries and frames are identical and disconnected frames carry no channels. |
| W3-1 drop `regex` from coach-ibt | accept | Keeps the 3 MB collector budget (D5 "very lightweight") with about 1.4 MB margin and no runtime cost. The alternative, workspace `opt-level = "s"`, is rejected: it also size-optimizes the server's hashing and projection. |
| W3-2 `live_table_version` in hello | accept | `CollectorHello` is normative. |
| W3-3 garage edge rules (a)-(e) | accept; (e) tightened | P5 evidence for (a); (e) must close the old visit before any snapshot (review 13). |
| W3-4 `status.sim` | accept | Defined from the collector's rule. |
| W3-5 no ureq, tungstenite, tracing in the collector | accept | Size budget; `architecture.md` is not a contract, so it is an architect follow-up. |
| W4-1 `setup_files.linked_setup_id` missing | accept with change: derive, no column | Migration 0001 stays equal to `db-schema.sql`; a derived link cannot go stale when files are renamed or new observations arrive. Name-only links are barred for ambiguous visits (review 14). |
| W4-2 `collector_newer_than_server` | accept | Additive; `live-frame.md` already required it. |
| W4-3 compare wait setting, tuning context without ETag | accept | Matches the code; the merged response has no stable tag. |
| W4-4 workspace pins, runtime `jsonschema` | accept | Scaffold change through the contract process; request validation is a runtime need. |
| W4-5 `group_id`, server-derived digest | accept | Python returns `group_id` (`ibt_reader.py:1955`); the worker reports no digest. |
| W4-6 `USERPROFILE` on Windows dev | accept | Python's Windows home lookup reads it. |
| W4-7 tuning_experiments as legacy_items | accept with change | W6's tool exists and reads a copied archive directory, so the bundle now carries `legacy_archive` files and `legacy_analyses`; the server runs the tool. |
| W5-1 128 signals, `omitted_signals` | accept | 76 real signals; about 8 MB for 82 laps, inside the 16 MiB traces bound. |
| W5-2 input shapes and envelope version | accept | Describes what the projector accepts. |
| W5-3 null profile version | accept with change: keep string, `""` documented | Avoids a generated-type change in three workstreams; `analyses.profile_version` is NOT NULL. |
| W5-4 shrink ladder prunes unread fields | accept | Already permitted by `info.description`. |
| W5-5 `caution`, `incident_count`, `fastest` | accept: removed | No backend source. The browser derives the fastest lap from `lap_time_s`, and flag fields carry caution. |
| W5-6 `X-Coach-Trace-Laps` | accept | Stated. |
| W6-1 `raw_source_policy` object | accept | Matches `workflow.py:2225`. |
| W6-2 `group_id`, `local-file` by `time_source` | accept | Same as W4-5. |
| W6-3 separators after root prefixes, SQLite as row dumps | accept | Only path text differs between platforms. |
| W6-4 `HOME` required, PyYAML never used | accept | Recorded in the environment table and section 5. |
| W6-5 `import_legacy_tuning_experiments` | accept | Added to the tool table. |
| W6-6, W6-7 Python contract and compatibility fields | accept, no rewrite-contract change | The Python MCP contracts are W6's own. |
| W6-8 WSL check default `all` | accept | Scaffold change through the contract process (W6). |
| W6-9 golden emitter independent of PyYAML | noted | W1 regenerates goldens. |
| W7-1 draft and annotation bodies | accept | `TuningDraftBody` and `corners` typed, unknown fields still round-trip. |
| W7-2 `PUT prefs` | accept: top-level merge | What the server implements; a full body remains valid. |
| W7-3 `analysis_id` per observation | accept | Additive; removes a second request. |
| W7-4 baseline purpose | reject adding it | `build_open_setup_package` has no purpose argument; nothing to pass. |
| W8-1 CCOL alignment as a file offset | accept | Encoder and decoder already agree. |
| W8-2 bin positions, auxiliary keys, `rad` | accept with change | Bin i is at `(i + 0.5) / bins`, the backend's bucket-centre `lap_pct`; no extra channel. Fixed signals and their keys listed. |
| W8-3 tire-wear and tire-stress | accept with change | `tire-stress` is already the fixed `tire_stress_proxy` signal. Tire wear is out of v1: C# computed it in the UI (AGENTS.md forbids UI telemetry math) and the backend has no per-bin wear. |
| W8-4 current-profile G2 fixture | accept with change: synthetic | Private recordings never enter the repository; a generated 82-lap race analysed with the current profile serves G2. |
| W8-5 413 on traces | accept | Already in the contract; the browser's remedy stated. |
| W9-1 image environment names | accept | W9's names, already set in the released image, become `contracts/server-config.md`; the server renames its reads. |
| W9-2 `healthcheck`, start-up directories | accept | Both exist in W4's server; W9 switches Compose after the first server release. |
| W9-3 NAS uid mapping, first-release values | accept | Architect follow-up (`architecture.md` section 10). |

## W1

1. `crates/coach-ibt`: remove the `regex` dependency (`Cargo.toml`). Replace `yaml.rs` `INTEGER_PATTERN` and `FLOAT_PATTERN` and `literal.rs` `INT_TOKEN`, `FLOAT_TOKEN` and `IMAG_TOKEN` with hand-written matchers of the same grammar; replace `gate.rs` `EXCLUDED_LEAVES` with the `setup-keys.json` subset matcher (ASCII case folding) and a unit test that fails on any `EXCLUDED_LEAF_PATTERNS` entry outside the subset. Proof: `cargo test -p coach-ibt` (77 YAML cases, fingerprint tests unchanged), `cargo clippy -p coach-ibt -- -D warnings`, and `cargo tree -p coach-collector -e normal -i regex` reports nothing.
2. Regenerate the goldens with `emit_session_golden.py` (W6 made session parsing PyYAML-free) and confirm they are byte-identical. Proof: `cargo test -p coach-ibt --test parity`.
3. Run the private-corpus parity. W6's `ibt_reader` fix (`DriverSetupIsModified`, fixture fallback `DriverSetupModified`, regression test) landed in `dd0ce49`, which was W1's only blocker on real data; W6's parity already used the New Hampshire (`582b11e2`), Iowa, practice and test-session recordings. Proof: `COACH_PRIVATE_CORPUS=<that folder> cargo test -p coach-ibt --test parity -- --ignored`, result recorded in the W1 report.

## W2

1. `crates/coach-live/tests/engine_vectors.rs`: empty `PENDING_BASIS_IDS`; the test now fails because the table matches C# ("cue 1 basis now matches C#"). Proof: `cargo test -p coach-live`.
2. `tools/wire/gen-wire.mjs` (scaffold change through the contract process): validate `unit_labels` keys against the tiles' units and emit `unit_label(unit) -> &str` in `live_channels.rs` and `unitLabel(unit)` in `live-channels.ts`; then `pnpm gen:wire`. Proof: `pnpm gen:wire` followed by `git diff --exit-code` on a clean tree.
3. `crates/coach-live/src/tiles.rs`: replace the private `unit_label` match with `coach_wire::live_channels::unit_label`. Proof: `cargo test -p coach-live` (engine.json tile readings unchanged).

## W3

1. After W1 item 1, rebuild and record the exe size and SHA-256. Proof: `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` (about 1.6 MB expected, at most 3 MB).
2. `src/garage.rs`: on re-entry inside the 5 s window, close the open visit with its own last snapshot before any `UpdateCount` check, then open the new visit (`collector-protocol.md` section 8; review 13). Proof: a garage test that re-enters with a changed count and asserts the old commit's values and the new visit's first snapshot.
3. `src/garage_io.rs`: compare size and mtime before and after hashing a `.sto` and restart resolution when they differ (review 12). Proof: a test that changes the file during hashing.
4. `src/garage_io.rs`, `src/app.rs`, `src/journal.rs`: keep a failed journal append and retry it; send no garage event without a durable journal; at start truncate a torn tail to the last newline and fsync the repair (reviews 10, 11). Proof: journal tests with a torn tail and with a failing writer.
5. `src/http.rs`: chunk writes in short, gate-checked attempts and shut the socket down when off-track evidence lapses, so an abort completes within 100 ms even when the server stops reading (review 2). Proof: uploader test against a mock that stops reading mid-chunk.
6. `src/sdk.rs`, `src/win/map.rs`: only "mapping does not exist" counts as no map; any other open or header failure is unknown evidence (section 4; review 3). Proof: gate unit tests for each open result.
7. `src/uploader.rs`, `src/scan.rs`: check the gate during folder traversal and journal reconciliation and keep unfinished work when interrupted (review 4). Proof: scan test that closes the gate mid-walk.
8. `src/stream.rs`: while demand stands, start every new stream session with the latch set (boundary or sim reconnect), and apply the server's `viewers`, `stream.start` and `stream.release` after a driver change (section 6; reviews 16, 17). Proof: stream-latch tests for a boundary and a driver change with a viewer present.
9. Run on the sim under the standing rules, simulator not in use: `record-sdk 30`, then G3 with the `--dry-run` build (unblocked by the lift).

All W3 items: `cargo test -p coach-collector`, `cargo clippy -p coach-collector --all-targets -- -D warnings`.

## W4

1. `src/config.rs`: read the `contracts/server-config.md` names (`COACH_DATA_DIR`, `COACH_NAS_DIR`, `COACH_WORKER_SCRIPT` as the script file with its directory as the scripts directory, `COACH_CONTRACTS_DIR`; production web default `/app/web`). Proof: a config unit test over the image environment.
2. Validate `analyze_iracing_race`, `build_open_setup_package` and `recommend_structured_open_setup_tuning` results against `analyze-result-v1`, `setup-package-v1` and `tuning-recommendation-v1` from `COACH_CONTRACTS_DIR`; a mismatch fails the job as `tool_error` (contracts README). Proof: stub-worker tests with a valid and an invalid result.
3. `src/setups.rs`: add `analysis_id` (the session's current analysis) to recording observations; in `linked_setup`, skip the name-only fallback for commits whose visit has `ambiguous_name = 1` (review 14). Proof: contract test of `GET /setups/{id}` and a linking test with an ambiguous commit.
4. `src/setups.rs`: acknowledge a garage event only after its observation is stored; attribute a missing driver to `collectors.last_customer_id` and a missing car to the visit; leave an event that is still unattributed unacknowledged (section 8 Attribution; review 15). Proof: garage replay tests for each case.
5. `src/import.rs`: accept `legacy_analyses` and `legacy_archive`; verify each file's SHA-256 and path, write them under the import job root's `legacy/`, resolve each legacy analysis to the current analysis of the matching session (null when unresolved), run `import_legacy_tuning_experiments` on the light lane, add its counts to the import summary, bump `user_data_generation`; stop storing `tuning_experiments` rows as `legacy_items`. Proof: an import test with the stub worker and, with `$py`, the real tool on a generated legacy archive.
6. `src/ws.rs`, `src/hub.rs`: on a collector driver change recount viewers for the new driver and send `viewers` and `stream.start` or `stream.release` (review 17); enforce the 10 s slow-browser limit on sustained lag, with timeouts on backlog and close sends (review 18). Proof: live-relay tests.
7. `src/ingest.rs`: recovery fsyncs the file and both rename directories before `verified` (review 1, blocker); take the hash lock before reading upload and blob state (6); recheck state and cutoff under the lock before idle cleanup (7); admit reservations atomically against incoming, ready, restaged bytes and all outstanding reservations, adding only an upload's increment on resume (5); reconcile every occurrence into `setup_files` in the commit transaction (23); retry pending ingest after a SQLite failure (22). Proof: `cargo test -p coach-server --test ingest` with crash tests that kill a child process at each cut and check acknowledged bytes before any retransmission (review 25).
8. `src/archiver.rs`, `src/handlers.rs`: restage through the upload admission and restore the clamped `source_mtime`, fsynced before the rename into `ready/` (`nas-layout.md` section 4; reviews 8, 9). Proof: restage test that analyses a restaged recording at once.
9. `src/worker.rs`, `src/jobs.rs`: one deadline and cancellation scope over stdin delivery, response reading and exit; kill the whole process group after the grace period even when the leader has exited; release admission through a guard on every path and retry terminal state writes; run deferred orphan cleanup when the driver's lanes go idle and at start (reviews 19, 20, 21, 24). Proof: stub-worker tests (stdout closed without exit, a SIGTERM-ignoring child, an injected SQLite failure).
10. Root `Cargo.toml` (scaffold change through the contract process): pin `tokio-stream 0.1`, `uuid 1`, `libc 0.2`, `tempfile 3`, `tower 0.5`, `http-body-util 0.1`, `serde_yaml_ng 0.10` in `[workspace.dependencies]`, then switch `server/Cargo.toml` to `workspace = true`. Proof: `cargo check --workspace`.

All W4 items: `cargo test -p coach-server`, `cargo clippy -p coach-server --no-deps --all-targets -- -D warnings`, `wsl -d Ubuntu -- bash tools/wsl/check.sh server`.

## W5

1. `src/traces.rs`: `MAX_SIGNALS` 64 to 128; `omitted_signals` lists only the keys beyond 128. Proof: `cargo test -p coach-views` with a synthetic analysis of 130 additional signals, and the private-corpus run serving all 76 real signals.
2. Add `examples/project.rs` (`cargo run -p coach-views --example project -- <analysis.json> <out_dir>`) so W6 can turn the generated G2 analysis into a committed catalog and CCOL fixture. Proof: the example's output validates in the existing fixture tests.

## W6

1. `tools/wsl/check.sh` (scaffold change through the contract process): default scope `all`, drop the "17 tests fail" comment. Proof: `wsl -d Ubuntu -- bash tools/wsl/check.sh`.
2. G2 fixture: generate a synthetic 82-lap race with `tools/generate_fixtures`, analyse it with the current profile, project it with W5's example, and commit the catalog and `traces.ccol` under `test-data/` (no private data). Proof: `verify-repository.ps1` and W8's `pnpm -C web perf:g2` on it.
3. Before wave 4: `export_contracts.py` stops reading `DurableArchive.cs` and the `tools/dev` tests stop reading `companion-app`. Proof: `verify-repository.ps1` with `companion-app/` absent.

## W7

1. `web/src/setups/`: use `SetupObservation.analysis_id` for Tune this setup (one request). Proof: `pnpm -C web test`, `pnpm -C web e2e --project=app`.
2. `web/src/live/tiles.ts`: unit text from the generated `unitLabel` (after W2 item 2), layouts store unit ids, cue secondary text from every cue's basis, and the `crate-api.md` 4.7 text rules tested against `crates/coach-live/tests/vectors/engine.json` tile readings. Proof: `pnpm -C web test`.
3. `web/src/tuning/model.ts`: type the draft from `Schemas["TuningDraftBody"]`. Proof: `pnpm -C web typecheck`.
4. `web/mock/`: drop `fastest`, `caution` and `incident_count` from fixtures and add `omitted_signals: []`. Proof: `pnpm -C web e2e --project=app`.

## W8

1. `web/src/investigation/geometry/trace-model.ts` and `cursor/telemetry-cursor.ts`: place bin i at `(i + 0.5) / bins` instead of `i / (bins - 1)`, so the cursor, map marker and backend `lap_pct` agree. Proof: `pnpm -C web test -- investigation` with a geometry test at the first and last bins.
2. Traces loader: on a 413, request lap ranges (halves, recursively) and join the CCOL bodies. Proof: a loader test with a mock 413.
3. `web/src/investigation/fixtures/synthetic.ts`: drop `fastest` and `incident_count` to match the contract. Proof: `pnpm -C web test -- investigation`.
4. Re-run G2 on W6's committed 82-lap fixture once it lands. Proof: `pnpm -C web perf:g2`.

## W9

1. After the first release of the real server (it creates `db/` and `snapshots/` at start): Compose healthcheck `coach-server healthcheck`, `app.env` `RECOVERY_PATHS=db snapshots` and `RECOVERY_SQLITE=db/coach.sqlite3`. Release it only after W4 items 7 and 8 land (R23). Proof: `release.sh iracing-coach .`, `g-house-stack status iracing-coach` healthy, `curl -s http://192.168.1.81:8210/api/v1/status` with `nas.state = ok`.
2. `tools/migrate/build_import.py` (wave 3): write `legacy_analyses` and `legacy_archive` (`history.sqlite3`, `tuning/`) instead of `tuning_experiments`; map legacy live-layout units (`°C`, `°F`, `m/s²`, `laps`) to ids; map legacy drafts to `TuningDraftBody` fields. Proof: the D12 report in `acceptance/migration.md`.
3. The image environment needs no change: its names are the contract. Proof: W4 item 1's config test over the same values.

## Architect

1. `architecture.md` section 8: the collector uses its own HTTP, WebSocket and logging (`src/http.rs`, `src/ws.rs`, `src/logging.rs`), no `ureq`, `tungstenite` or `tracing`; `coach-ibt` uses no `regex`; `coach-server` uses `jsonschema` at run time for request validation.
2. `architecture.md` section 10: the NAS maps the container's root to uid 1024 (gid 100, mode 0777); the first-release `RECOVERY_PATHS=.` and Python healthcheck with their reasons (`acceptance/first-release.md`); environment names by reference to `contracts/server-config.md`.
3. `workstreams.md` step 0b and W4: `jsonschema` is a runtime dependency of `coach-server`.
