# Rewrite contracts

These files are the only interfaces between workstreams. Code follows them; a workstream that needs a change edits only the contract (one commit touching `docs/08-rewrite/contracts/` and the regenerated files), states the reason in the commit message, and the change is reviewed (Codex Sol 6.1 for protocol and ingest changes, D15) before dependent code lands. Additive optional fields need no review.

| File | Defines | Consumers |
|---|---|---|
| `api.openapi.yaml` | HTTP API, SSE events, collector WebSocket JSON messages, all JSON wire types | server, web, collector |
| `collector-protocol.md` | Collector to server behaviour: identity, presence, upload, acknowledgements, streaming, reconnect, versioning | collector, server |
| `live-frame.md` + `live-channels.json` | Binary live frame layout, channels, cue templates, tile catalog | collector, server (header only), web |
| `nas-layout.md` + `manifest.schema.json` | NAS folder layout, atomic write protocol, manifests, crash recovery, deletion rules | server, migration |
| `db-schema.sql` | Server SQLite schema v1 | server |
| `worker-interface.md` | How the server runs the Python worker; how Rust stages substitute; parity | server, Python worker, port |
| `setup-keys.json` | `CarSetup` keys excluded from setup identity (P5 derived values) | server, collector |
| `crate-api.md` | Public Rust types, signatures and error enums of `coach-ibt`, `coach-live`, `coach-views`, the generated `coach-wire` items they use, crate ownership and dependencies | collector, server, the crates' owners, scaffold (step 0b) |
| `server-config.md` | Environment the image sets for `coach-server`, directories it creates at start, the `healthcheck` subcommand | server, deploy |
| `vectors/live-frame/` (added by W2) | Golden encoded frames with decoded values | collector, server, web |

## Single source of truth for wire types

`api.openapi.yaml` (OpenAPI 3.1, schemas in JSON Schema 2020-12) is the source. Generated, committed, and checked for drift by `tools/check.ps1`:

| Output | Tool | Path |
|---|---|---|
| TypeScript types | `openapi-typescript` (pinned in `package.json`) | `web/src/gen/api.d.ts` |
| Component schemas as one JSON Schema document | `tools/wire/gen-wire.mjs` | `crates/coach-wire/schema/wire.schema.json` |
| Rust types | `typify` (`import_types!` in `crates/coach-wire`) | compiled from `wire.schema.json` |
| Live channel and tile tables | `tools/wire/gen-wire.mjs` | `web/src/gen/live-channels.ts`, `crates/coach-wire/src/gen/live_channels.rs` |
| Setup identity exclusions | `tools/wire/gen-wire.mjs` | `web/src/gen/setup-keys.ts`, `crates/coach-wire/src/gen/setup_keys.rs` |

Command: `pnpm gen:wire` at the repository root. Schemas use only constructs both generators handle: objects, arrays, enums, `type: [T, "null"]` for nullable, `oneOf` with a `kind` discriminator. Unknown optional fields are preserved by readers (`additionalProperties: true` where a backend subtree passes through).

The Python MCP contracts in the top-level `contracts/` remain the oracle's own; the server validates worker results against them where they exist (`analyze-result-v1`, `setup-package-v1`, `tuning-recommendation-v1`).

## Change log

### 2026-10-10, recorded iRacing sectors and field comparison

Owner request: Sections must use iRacing sectors in track order in P/Q/R; Race needs a car selector and Results Summary after Traces.

- Python profile v27, investigation `sections-trials-v8`: `iracing_sectors` carries the recorded `SplitTimeInfo.Sectors` partition. Active review sections use it exclusively; absent/invalid definitions yield an explicit unavailable reason. Detected corners stay separate from official sector labels. Optional `lap_traces.traces[].sector_boundaries` carries raw interpolated start/finish crossing timestamps; binned native trace output is unchanged. The section timing projection consumes these endpoints under the same coverage checks.
- `RaceView.field_pace.results`: additive optional Python schema 1 participant summaries, recorded lap progression and pit-road entries. Competitor timing uses consecutive observed counter transitions, interpolating start/finish distance wraps when available; sampled timing is approximate and delayed last-lap scoreboard values are never assigned to the wrong lap. Player summaries and service are scoped to this phase; an empty observed tire-change list does not establish zero tires. Recorded order is not a final-classification claim. Fuel and observed tire changes exist only for the player; private competitor service is unavailable. No UI telemetry math. The race view retains summaries under its 2 MiB budget; lap progression can be omitted with an explicit budget reason.
- `LastInvestigation.view` adds `results`. Generated wire outputs regenerated. Projection version 6 becomes 7 so old projections are refreshed. Existing analyses require reanalysis for the new backend sector/field evidence.
- Racing decisions: locate loss in the same sector across sessions; compare observed race pace and pit visits against competitors; inspect a competitor's recorded progression. Later recordings can check whether those pace and service differences persist.
- Verification: Python sector and field-results regressions, Rust sector boundary/parity vectors and field projection preservation, web picker/table/map tests and desktop/tablet/phone visual checks. Native stage algorithms are unchanged.

Each contract change adds one entry here in the same commit; review per the rule at the top.

### 2026-10-09, Phase 1 item 3: section pace attribution (additive optional field, no review needed)

Reason: Sections needs the driver's median-minus-best section time and the section's share of lap tire energy to identify where a typical pass costs most time.

- `InvestigationView.pace_attribution`: optional nullable `PaceAttribution`, producer schema 1, at most 32 priorities, ordered by `recoverable_s` descending. Copy producer quantities unchanged; `best_lap` is the best pass through that section, not a best whole lap. Preserve unknown optional fields. No Python producer or parity fixture change.
- Evidence follows the computed-provenance precedent of `ObservedTrend`: declared in `x-source`, without invented per-priority evidence keys. Pace attribution is derived; tire energy is derived for `slip_weighted`, proxy for `load_only`.
- `section_id`: only a unique detected investigation section overlapping at least half the priority span attaches; otherwise null and hidden by the UI. No name or positional fallback. The contract records this mapping and the consumed fields.
- `crate-api.md` and `coach-views::PROJECTION_VERSION`: 5 becomes 6 so stored analyses are re-projected. Regenerated with `pnpm gen:wire` before dependent implementation.

### 2026-10-07, B96/B97: a race's finish, and the start on lap 0 (web round 6, backlog #96 #97; additive optional fields, no review needed)

Reason: backlog #97, a race he wrecked out of and left read as a finish (the 2026-10-06 21:09 Charlotte race: "P6", the place he was running when the recording stopped), and #96, the Race chart's position line began at lap 1's place instead of the start.

- Python (oracle, `analysis_engine._race_finish`, called by `analyze_telemetry` for `race_summary`): `race_summary.finish`, null outside a Race session (`sim_session_type`), else `status` (`checkered`: the samples of the race session, by `SessionNum`, reached `SessionState` checkered 5 or cool-down 6; `recording_ended`: they reached racing 4 and never either; `unknown`: no `SessionState`, or never racing), `finished` (true, false, null), `last_lap` and `position_at_end` (the last lap summary's `lap` and `position.end`), `last_session_state`, `laps_remaining_at_end` (`SessionLapsRemainEx` at the last sample, null at 32767), `evidence_class` `measured_session_state`, `rule`. Not a ported stage: no native kernel changes. `ANALYSIS_PROFILE_VERSION` v25 becomes v26 (`ANALYZER_BUNDLE_REVIEWED_SHA256` reviewed): every other value is unchanged and a v25 analysis reads as unknown, but the worker's analysis cache is keyed on the profile, so without the bump a re-analysis would answer from the v25 cache; `POST /admin/reanalyze` re-queues every stored session. Tests: `iracing-coach/tests/test_race_finish.py`.
- `api.openapi.yaml`: `RaceView.race_summary` lists `finish` in `x-consumed-fields` (the shrink ladder keeps it; `coach-views` `consumed::RACE_RACE_SUMMARY`) and describes it. `SessionOverview.finished`, `retired_lap`, `retired_position`: the backend's `finished`, and with `finished` false its `last_lap` and `position_at_end`; null otherwise; always sent (`server/src/overview.rs`, `FINISH_KEYS`). An overview stored without `finished` is rebuilt from its views at start. No projection version change: `race_summary` is copied whole.
- Web: the Sessions Finish slot reads `DNF · L14` for a race with `finished` false and no official `results_position`; the Race chart's position line starts at lap 0 with `start_position` (the shell passes the session's start and retirement to the workspace as `raceResult`).
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/finish_fields.rs`; unit tests in `src/overview.rs`), the Python tests above, `web/src/sessions/dnf-97.test.ts`, `web/src/investigation/__tests__/start-dnf-96-97.test.ts`, and the e2e cases in `sessions.spec.ts` and `investigation.spec.ts`.

### 2026-10-06, B90: each run's observed pace trend on the runs view (web round 6, backlog #90 #91 #93; additive optional field, no review needed; one new projection version)

Reason: backlog #90, the Race chart draws each run's best-fit line with its slope, traffic laps included, and #91, the run's fall-off read `−0.10 s/lap` on a slowing run because it took the restart lap as the run's first. The fit comes from the server, not the browser.

- `api.openapi.yaml`: `BackendRun.observed_trend` (`ObservedTrend` or null; added to `x-consumed-fields` so the shrink ladder keeps it): `slope_s_per_lap`, `intercept_s` (the line's time at lap 0), `first_lap`, `last_lap`, `laps_used`, the least squares of `lap_time_s` on lap over the run's projected `race.json` lap rows that are complete, green, under 1 s on pit road, neither pit-in nor pit-out, timed, with no `exclusion_reasons` but `close_traffic`, and not an incident's `candidate_lap`; null under three such laps. Projector-computed (`crates/coach-views/src/trend.rs`, `#[doc(hidden)] pub mod trend` so the server can reuse the rule); distinct from the backend's screened fit.
- `crate-api.md`: `PROJECTION_VERSION` 4 becomes 5 so stored analyses are re-projected (the server queues the projection on the first request for a stale view). Overviews are rebuilt from the views at that projection.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-views` (unit tests in `trend.rs`: a hand-computed four-lap vector, the exclusions, and the 10/02 Vegas run 1 shape, +0.0756 s/lap over laps 11–27 where the old first-to-last read −0.095; `tests/synthetic.rs` checks every run carries the field and the view validates).

### 2026-10-06, B85: a race's best and median green lap with traffic included on the Sessions row (web round 6, backlog #85; additive optional fields, no review needed)

Reason: backlog #85, the Sessions race line read `—` for Best and Pace on 21 of 26 races: `best_clean_lap_s` and `green_pace_median_s` are over the laps the backend screened as clean, and the screen leaves out close traffic, which is nearly every race lap.

- `api.openapi.yaml`: `SessionOverview.best_green_lap_s` and `race_pace_median_s`, the minimum and the median of the backend's `lap_time_s` over its `race.json` `laps[]` rows that are complete, green for the whole lap, under 1 s on pit road, neither pit-in nor pit-out, on track, without pit, tow or repair evidence and without a backend exclusion reason other than `close_traffic` or `restart` (`server/src/overview.rs`, `green_laps`). Both always sent, null when no lap qualifies. `best_clean_lap_s` and `green_pace_median_s` are unchanged.
- Server, no schema change: at start, overviews at the current projection version without `best_green_lap_s` are rebuilt from their views (`overview::refresh_on_start`). No re-projection: the lap rows are already in `race.json`.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/round6_fields.rs`; unit tests in `src/overview.rs`, including the sanitized traffic-heavy race `test-data/g2/race.json`: best 28.408 s, pace 28.5105 s where `best_clean_lap_s` is null).

### 2026-10-05, traffic side: `clean_context.traffic_ahead_fraction`, `traffic_behind_fraction`, `traffic_side` (web round 5, backlog #67 and #75; additive optional fields, no review needed)

Reason: backlog #67 and #75, the Race chart's traffic marker and the Sections `Traffic` column name the side the other car was on; the backend stated only how much of the lap had close traffic.

- Python (oracle, `analysis_engine._lap_summaries` through `_traffic_sides`): each lap's `clean_context` gains `traffic_ahead_fraction` and `traffic_behind_fraction`, the share of the observed lap time with a car ahead within max(25 m, 0.75 s), or behind within max(15 m, 0.40 s), from the recorded `CarDistAhead` and `CarDistBehind` (4 decimals; null without the channels), and `traffic_side`: null when `traffic_proximity_fraction` is 0 or null; `both` when each side's fraction is >= 0.10; else the side whose fraction is; else the side with the larger fraction (`both` when equal). The rounded fractions decide, as the screen does. `traffic_proximity_fraction`, `traffic_screened`, `traffic_rule` and the close-traffic screen are unchanged. No profile bump of its own (every existing value is unchanged; an analysis without the keys reads as side unknown).
- Native kernel `analysis_engine._lap_summaries` (`crates/coach-analysis`) ported byte for byte. Kernel vectors regenerated on both platforms: the laps unit, with two new synthetic tables (`laps-traffic-sides`, `laps-traffic-behind-only`), and the sections and field calls whose recorded arguments carry the laps. `coach-views` `consumed::LAP` copies the three keys, so the race view's shrink ladder keeps them.
- `api.openapi.yaml`: `Lap` describes the three keys and lists them in `x-consumed-fields`; regenerated with `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).

### 2026-10-05, U4: traces at 640 bins per lap (web round 5 task U4; description changes and one new projection version, no review needed; analysis profile v25)

Reason: backlog #77, the traces were binned at 160 bins per lap (10-20 m on an oval) and the cursor stepped from bin to bin.

- Python (oracle, `analysis_engine._lap_trace_columns`): the analysis gains `lap_trace_columns`, the lap traces at 640 bins per lap as one list per point key (`_lap_trace_payload` at `maximum_bins=640`, eight laps a call; `session_time_s` is the time the car passed each bin centre, linear between the two samples either side, where the 160-bin payload keeps the bin's median sample time). `lap_traces` stays at 160 bins: the investigation, the trials and the section times read it. `ANALYSIS_PROFILE_VERSION` v24 becomes v25, so `POST /admin/reanalyze` re-queues every stored session; an analysis without `lap_trace_columns` projects at 160 bins as before.
- Native kernels: no change; `_lap_trace_payload` already takes any `maximum_bins`, and the traces vectors now record the 640-bin calls (Windows).
- `coach-views`: `traces.ccol` and the catalog's `bins_per_lap` and `signals` come from `lap_trace_columns` when present; the catalog's laps and `sector_times_s` and `InvestigationView.section_laps` stay on `lap_traces`. `PROJECTION_VERSION` 3 becomes 4.
- `crate-api.md` section 5: `PROJECTION_VERSION` 4; `bins_per_lap` names its source. `api.openapi.yaml`: the traces endpoint summary and `TraceCatalog.bins_per_lap` describe both densities. Regenerated with `pnpm gen:wire`.

### 2026-10-05, U2: `SetupFileEntry.car_name` on the unlinked files (web round 5 task U2; additive optional nullable field, no review needed)

Reason: backlog #74, the Setups page named cars without a decoded setup by their raw folder (`acuraarx06gtp`).

- `api.openapi.yaml`: `SetupFileEntry.car_name` (string or null), sent on `SetupPage.unlinked_files` entries and absent on `sto_files`: the screen name a recording of that car folder carries (the newest wins), or of the car whose setup a file in the folder is linked to; null when no recording names it. Filled by `server/src/setups.rs`; the setups `q` search also matches it on unlinked files. Regenerated with `pnpm gen:wire`.

### 2026-10-04, S3: `section_laps[].median_lap` (web round 4 task S3; additive, no review needed)

Reason: backlog #64, the Sections telemetry draws the median lap in grey and needs the backend to name it.

- `api.openapi.yaml`: `InvestigationView.section_laps.sections[].median_lap` (integer or null): the lower-middle lap of the sorted clean section times. Emitted by `crates/coach-views/src/section_laps.rs`; regenerated with `pnpm gen:wire`.

### 2026-10-04, S4: track centreline from clean racing laps, true proportions (web round 4 task S4; additive optional field, no review needed; analysis profile v24)

Reason: backlog #65: the track maps were lopsided with a jagged stretch. The backend's main path was the per-bin median of every off-pit sample in raw degrees: pit approach and exit samples (off pit road, on the in and out laps) bent the line, and longitude was not scaled by cos(latitude), stretching every map east-west (24 % at Las Vegas).

- Python (oracle, `race_foundations.build_track_geometry`, rule `clean-racing-laps-v1`): the main path comes from the clean racing-speed laps only (every sample off pit road, on track, under no caution, a full lap of distance; laps within 5 % of the fastest one's duration), else every off-pit sample as before; every layer's x is longitude times `cos(latitude of the GPS centre)`; a complete loop is smoothed by a seven-point circular moving average. `track_geometry.transform.longitude_scale` and `track_geometry.centreline` (`rule`, `source`, `laps`, `smoothing_points`) are new. The per-track geometry cache ranks the current rule above older geometry. `ANALYSIS_PROFILE_VERSION` v23 becomes v24, so `POST /admin/reanalyze` re-queues every stored session.
- Native kernel `analysis_engine.build_track_geometry` (`crates/coach-analysis`) ported byte for byte; field vectors regenerated on both platforms (two new synthetic tables, `field-surface`, `field-no-lap`).
- `api.openapi.yaml`: `GeometryTransform.longitude_scale` (optional; 1 when absent). `coach-views` carries it into `track.json`; the browser multiplies a recorded longitude by it before the transform.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).

### 2026-10-04, S1: field size, official place, grid position and burn per lap on the Sessions row (web round 4 task S1; additive optional fields, no review needed)

Reason: backlog #55 to #57 and `web-round-2-plan.md` "Round 4": practice and qualifying show `P3 / 24`, a race finish change is measured against the official starting position and names it (`from P12`), the race cell shows burn per lap instead of fuel used. Session 23 (Talladega 2026-09-29) shows why the grid matters: its race recording holds P12 through the pace laps, while the lone-qualifying cell showed P5, the position when that recording ended, before the rest of the field qualified.

- `api.openapi.yaml`: `SessionOverview.field_size` and `results_position` (the session's official results and the driver's overall place in them, from a recording of a later session of the event: the sim writes a recording's session info when it starts, so only a later recording holds a session's final results), `start_position` (races: the overall grid place from `QualifyResultsInfo.Results`, else the backend's `starting_position`), `fuel_per_lap_gal` (the backend's `strategy.measured_green_fuel_gal_per_lap`). All four always sent, null without the evidence.
- Server, no schema change: the parser stores a results summary in `recordings.header_json.results` (`overview::results_summary`: per session the car count and the overall place, counted by `Position` so 0- and 1-based lists agree; the grid). At start, in the background, recordings stored without it are re-read (header and session info only, spool or NAS) and every overview at the current projection version takes the figures; overviews without `fuel_per_lap_gal` are rebuilt from their views. No re-projection: `strategy` is already in `race.json`. A projection also refreshes the other sessions of its event.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/round4_fields.rs`; unit tests in `src/overview.rs`), `pnpm -C web typecheck`.

### 2026-10-04, R4: per-section clean-lap times (web round 3 task R4; additive optional field, no review needed; one new projection version)

Reason: backlog #48 and #49 and `web-round-2-plan.md` "Round 3": Sections lists, per detected section, the three fastest and three slowest clean laps by that section's time around the section's median. The investigation view carried section times only for a comparison's two passes. Projection version 2 becomes 3 so stored analyses are re-projected (the server queues the projection on the first request for a stale view).

- `api.openapi.yaml`: `InvestigationView.section_laps` (`SectionLaps`, `SectionLap`): per `race_investigation.sections.sections[]` entry, `clean_lap_count`, `median_s`, `fastest` and `slowest` (up to three each, never sharing a lap) with `lap`, `section_s`, `delta_s`. A lap's section time is the backend's own `race_inspection.section_elapsed`, ported line for line into `crates/coach-views/src/section_laps.rs`; clean is the backend's `race_investigation.laps[].eligible`. Optional: views projected before version 3 lack it.
- `crate-api.md` section 5: `PROJECTION_VERSION` 3; the projected field.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-views` (`section_laps::tests`: 180 vectors from the Python function, withheld cases included, exact to 3 decimals; `tests/section_laps.rs`: ranking, the screen, few laps, no laps, bad bounds, schema validation).

### 2026-10-04, R1: Sessions row figures for practice and qualifying (web round 3 task R1; additive optional fields, no review needed)

Reason: backlog #33 and `web-round-2-plan.md` "Round 3": the practice cell shows its longest run without a reset to the pits or garage and the pace lost over it, the qualifying cell its first two timed laps.

- `api.openapi.yaml`: `SessionOverview.longest_run_laps` (laps of the backend run with the most laps, from `race.json` `laps[].run_number`), `SessionOverview.longest_run_pace_loss_s` (last minus first `lap_time_s` of that run's green laps the backend's screen kept, `race_investigation.laps[].eligible`; null under three), `SessionOverview.timed_lap_1_s` and `timed_lap_2_s` (the first two complete laps with a time, under 1 s on pit road, neither pit-in nor pit-out). All four always sent, null without the evidence; read from the stored views, nothing screened again.
- Server, no schema change: overviews stored without these keys gain them at start, in the background, from the projected views (`server/src/overview.rs`, the round-2 refresh widened).
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/round3_fields.rs`; unit tests in `src/overview.rs`, one on `test-data/race-investigation.json`), `pnpm -C web typecheck`.

### 2026-10-04, C2: server fields for web round 2 (web round 2 task C2; additive optional fields, no review needed)

Reason: `web-round-2-plan.md` Sessions (green pace with its supported trend in each phase cell), Setups (`⚑ Raced N×`, Versions with the changed-parameter count and a unit per changed key) and the filter rule (several cars on the Setups page). The web read these as provisional fields; the server now sends them.

- `api.openapi.yaml`: `SessionOverview.green_pace_median_s` (median backend `lap_time_s` over the green laps the backend's screen kept, `race_investigation.laps[].eligible`) and `SessionOverview.pace_slope_supported` (true when a run of the phase has the backend's screened fit, `race_investigation.runs[].fit`; read, never recomputed), both always sent, null without the evidence. `SetupSummary.raced_count` (distinct race sessions of the asked library; null when a recording of the setup is in no session or one of unknown type). `GET /setups/{setup_id}` takes `library` (usage figures and versions of that library, `main` default; `400` otherwise) and `SetupDetail.versions` (schema `SetupVersion`: the setups of the car sharing a name, oldest first, `previous_setup_id` and `changed_count`, the backend's `counts.changed` of the finished comparison, null until it exists). `POST /setups/compare` keys its job by worker bundle, values and scheme, so the same pair answers from the finished job. `SetupCompareRow.unit` (the displayed unit both values share). `SetupFilters.car_paths` beside `car_path`; unknown fields stay preserved.
- Server, no schema change: overviews stored before this rule gain the two fields at start, in the background, from the projected views (`server/src/overview.rs`).
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/round2_fields.rs`; unit tests in `src/overview.rs` and `src/setups.rs`, one on `test-data/race-investigation.json`), `pnpm -C web typecheck`.

### 2026-10-04, A5: the Lab library (web round 2 task A5; additive, no review needed)

Reason: backlog #18 (resolves #14) and `web-round-2-plan.md` "Lab library": bot-driven and other agent-driven recordings leave Joshua's session list and counts without being deleted or moved. Rule, historical windows and dry run: `acceptance/lab-library.md`.

- `db-schema.sql`: migration 3 appended (`server/migrations/0003_lab_library.sql`): `recordings.library` (`main` default, never null, or `lab`), `recordings.library_window_id`, table `lab_windows` (from, until, car, track, note, source; one per start, car and track). Additive; a version-2 server ignores them.
- `api.openapi.yaml`: query parameter `library` (`main` default, `lab`; any other value `400`) on `GET /drivers`, `/drivers/{customer_id}`, `/drivers/{customer_id}/sessions`, `/drivers/{customer_id}/cars-and-tracks` and `/drivers/{customer_id}/setups` (setup usage: counts, sources and `raced` from that library's recordings; garage visits and exports count in main); `SessionSummary.library` (optional, always sent: `lab` when any of the session's recordings is); schemas `Library`, `LabWindowWrite`, `LabWindow`, `LabWindowList`, `LibraryClassifyRequest`, `LibraryChange`, `LibraryOverlap`, `LibraryClassifyReport`; response `BadRequest`; operations `GET`/`POST /api/v1/admin/library/windows` (state or close a window) and `POST /api/v1/admin/library/classify` (the repair: dry run by default, idempotent, logged to `run/library.jsonl`). A recording is lab only when its start is inside a window, its car and track equal the window's and it is a solo offline test, so a hosted session or a race against AI stays main.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`). For iRacing-utility: its feed excerpt records `GET /api/v1/drivers` and `/drivers/{customer_id}/setups`, which gained the parameter, so `feed/tests/test_contract.py` needs a re-record once this is committed.
- Proof: `cargo test -p coach-server` (`tests/lab_library.rs`, `tests/api.rs` contract coverage, `tests/misc.rs` schema pin), `pnpm -C web typecheck`.

### 2026-10-04, W6b: offline stints join only on one setup; a stopped lap counter joins nothing (worker W6b; review: pending)

Reason: the W4 join rule made all 14 stints of the bot's 2026-09-25 IRP Test Drive (one sim session over 15 h, on 09-25 and 09-26) one session, analysed with one setup although the bot drove four setups; and the eleven stints of 09-26 record `Lap` 1024 throughout (the sim's counter stops there: `Lap` stays 1024, 1025 for 5 s after each start/finish crossing, `LapLastLapTime` frozen at 24.518 s), so their laps cannot be told apart and joined to 09-25's laps 93-145 they would break that analysis. This closes W4's "Open for W6" note below (the worker side landed with W6).

- `worker-interface.md` section 3: new paragraph "Offline stints": the join rule both sides apply, now with the setup (equal values fingerprints of the recordings' own `CarSetup`, `setup-keys.json`) and the stopped lap counter (last `Lap` sample 1024 or more: never joined; the worker refuses the analysis with `LapCounterStoppedError`, the server defers it with `lap_counter_stopped`).
- `collector-protocol.md` section 8: one paragraph: a recording's own `CarSetup` decides which offline stints share a session. No message or collector change.
- Server, no wire or schema change: `recordings.header_json.lap_last` (the last `Lap` sample, null when absent; the start-up backfill adds it to older offline rows); the repair merges only stints of one setup and reports sessions an earlier rule joined across a setup change (`sessions_with_mixed_stints`, left alone) and recordings with a stopped counter (`recordings_lap_counter_stopped`).
- Proof on the dev PC's copies (`cargo test -p coach-server --test offline_sessions corpus_report` with `COACH_OFFLINE_CORPUS`, and the worker's selection over the same files): the IRP Test Drive's three stints of 09-25 (one setup) are one session, analysed together (20 laps, 93-145, 3 runs); its eleven of 09-26 stay apart and are deferred; the two Las Vegas rig stints of 2026-10-03 are now two sessions, because the G3 garage test changed the left-front cold pressure from 155 to 152 kPa between them (`acceptance/g3-garage.md` V2 to V5). Python and Rust fingerprints agree on all 16 files.

### 2026-10-04, W2: one lap to green is a caution only in a Race (worker W2; review: pending)

Reason: the bot-driven G4 run (`acceptance/g4-performance.md`) showed the Coach cue "Caution" for 50 laps of a solo Offline Testing session and no "Laps to pit" estimate. iRacing holds `SessionFlags` `0x200` (one lap to green) through the whole of a test session (`0x10000200` in the real Test Drive capture `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix`); the engine read it as caution, so no lap was clean and no fuel-use sample was taken.

- `live-channels.json`: the `flag_state` channel's `source` text states the rule. No channel, enum, cue or tile changes; no generated file changes.
- Behaviour (`coach-live`): `0x200` alone is a caution in a Race and when the session type is unknown, as the backend's `analysis_engine._caution_flag_series` has it; yellow, yellow waving, caution and caution waving (`0x8`, `0x100`, `0x4000`, `0x8000`) count in every session. The session type is the one the sample's own `SessionNum` names in the session info. The conformance file's session-agnostic `caution_mask` and flag vectors are unchanged.

### 2026-10-04, W4: only a garage commit makes a setup; offline stints join one session (worker W4; review: Codex Sol 6.1 for `collector-protocol.md` section 8, pending)

Reason: the resumed setup harvest (`iRacing-utility/lab-notes/harvest-2026-10-03.md`) left setup 72, a load snapshot that read the Supra's left-front camber +5.9 deg beside its own commit 73 at +5.8 deg; and the bot-driven G4 run (`acceptance/g4-performance.md`, G4-9) made two library sessions (658, 659) of one Test Drive.

- `collector-protocol.md` section 8: new paragraph "Setup identity of a visit": only `garage.commit` creates a setup; snapshots wait for it and become observations of the setup they equal (fingerprint, then the sim's rounding: camber 0.1 deg, pressures 0.1 psi), else of none; existing snapshot-only setups fold at start. Delivery and Attribution say where a snapshot is stored. No message or collector change.
- `db-schema.sql`: migration 2 appended (`server/migrations/0002_garage_snapshots.sql`): table `garage_snapshots` (values of each snapshot, `resolved_at` once its visit's commit decided). Additive; a version-1 server ignores it (a commit that references a snapshot stored there stays in the collector's journal until a version-2 server takes it). The file is now migrations 1 and 2 in order; the server test pins that.
- Server, no wire change: `recordings.header_json` gains the disk header's `session_start_time` and `session_end_time`. An offline recording (`SubSessionID` and `SessionID` 0, disk-header times) whose sim session already has a library session joins it at discovery: same driver, car, track and configuration, session number, `SessionUniqueID` and type, session start (`sessionStartDate - sessionStartTime`) within 2 s, no overlap (`src/offline_sessions.rs`). At every start, in the background, the server reads those times for older rows (spool or NAS, headers only) and merges sessions an older server split (`run/offline-sessions.jsonl`); on the dev PC's 51 recordings it would merge 1 (the two rig stints of 2026-10-03 at Las Vegas).
- Open for W6 (`worker-interface.md`): `analyze_iracing_race` selects one discovery group, and `ibt_reader._fallback_group_key` keys an offline recording by its own file start, so the analysis of a joined session covers only the stint its stored group key names; the analyze job's result says so (`recordings_not_analysed`). The worker needs to key offline groups by the session start above (or accept the session's recordings as one selection). Closed: the worker selects a joined session's stints (W6); the rule gained the setup and the lap counter (W6b, above).
- Proof: `cargo test -p coach-server` (`tests/setup_identity.rs`: the 72/73 shape live and as a fold, the G3 capture's four visits, the rounding rule; `tests/offline_sessions.rs`: the G4 shape in either upload order, the repair dry run, merge and second run; `tests/misc.rs`: the schema pin).

### 2026-10-02, step 0a: shared interfaces (architect; review: Codex Sol 6.1 with the plan, D15)

Reason: workstreams that never read each other's code need field-level types, not function names (`workstreams.md` step 0a).

- `crate-api.md` added: `coach_ibt` (SdkHeader, VarTable and readers, IbtFile, RecordingSummary, SessionInfo with WeekendInfo, SessionEntry, PlayerDriver, DriverSetup, CarSetup, setup fingerprint, finalization gate, streaming hash, IbtError), `coach_live` (SessionContext, Sample with every SDK variable the engine reads, Engine, LiveState mirroring live-channels table 1, frame codec, tile formatting, FrameError), `coach_views` (project, ProjectionSummary, view files, select_traces, ProjectError, SelectError), the generated `coach_wire` item names, crate owners and dependencies. Reader-side normalisations of the C# SDK source move into the engine so vectors cover them. Scaffold adds `regex` to workspace dependencies.
- `api.openapi.yaml`:
  - `TuningContext.identity` and `.map` typed as `TuningSessionIdentity` and `TuningMapView` (field by field from C#). C# `RaceId`, `EventKey`, `Selector` become the server-bound `TuningSessionBinding`; `AnalysisPath` is dropped (server-internal). `TuningContext` = projector-written `TuningContextProjection` + `binding`, typed `open_targets` (`TuningOpenTarget`), `draft_identity_key` (formula stated); `can_use_as_evidence` added; `representative_runs` typed as `BackendRun` with the C# eligibility rule.
  - `TuningRecommendRequest.map_identity` typed as `TuningMapSubmission` without its hashes: the server adds `geometry_hash` and `annotation_hash` (Python float formatting stays server-side). `feedback` typed (`TuningFeedbackItem`), `representative_run_ids` max 3, `goal` and `ruleset_id` enums, all to match the worker's closed input schema (`mcp_server.py:700-769`); these request objects are therefore closed (`additionalProperties: false`), an intended exception to round-tripping.
  - `x-consumed-fields` on every `x-source` object (convention in `info.description`), plus `Lap`, `TraceLap`, `SteeringBasis` (now `x-source`), and the new shared `BackendRun`. `field_pace`, `conditions`, `corner_tire_age`, `tire_learning` and geometry provenance are read by no v1 screen (empty lists).
  - `TrackGeometry` corrected: the backend geometry is `normalized_local_vector` (`race_foundations.py:528`), not metres; it is copied without reprojection and gains `status`, `track_configuration_key`, `geometry_hash`, `transform` (GPS to geometry space, needed to draw recorded laps on the map), `quality`, typed `start_finish`, `pit_commitment_line`, `pit_merge_line`. `PathSeries.pct` items may be null.
  - `TraceCatalog.sector_start_pcts` added (Traces sector bands).
  - Fixed a YAML defect: the `q` parameter description on `/api/v1/drivers/{customer_id}/sessions` was unquoted and split into stray keys.
- Proof: the file parses (js-yaml), validates as OpenAPI 3.1 (`@apidevtools/swagger-parser`), every component schema compiles under JSON Schema 2020-12 (ajv), every `$ref` resolves, and every `x-source` object carries `x-consumed-fields`.

### 2026-10-02, step 0a review resolution (architect; review: Codex Sol 6.1, `reviews/crate-api-review-codex-sol.md`, all 15 findings accepted)

Reason: the review found defects in the step-0a interfaces; each was checked against the C# or Python source before it was accepted (disposition table in the review file).

- `crate-api.md`:
  - `coach_ibt` depends on `serde` (derive); `coach_live` public types derive no serde traits.
  - Readers keep present non-finite values raw (`VarTable` scalar and array readers, `edge_scalar`, `Sample`); outputs never carry them. C# `??` fallbacks select by presence (`SessionLapsRemainEx`, `TrackTempCrew`, `BrakeABSactive`), and Python's edge read returns the last record's NaN.
  - IBT header text follows Python `_decode_c_string`; live header text follows C# `Encoding.ASCII`.
  - `RecordingSummary.start_time_utc` and `end_time_utc` are `Option<String>` (`_safe_iso`).
  - Quoted and flow YAML values are evaluated as Python `ast.literal_eval` over a stated subset; `YamlNode::Literal` added; other literal kinds are a YAML error.
  - Frame codec: `FrameError::TooManyChannels`; `BadCue` carries its reason (truncated, k > 4, unknown id, priority or evidence); the collector carries the epoch marker to the next sent frame.
  - `TileText.min` and `max`, `display_range` (C# `ApplyRange`), and the per-tile missing texts (Waiting, Insufficient evidence, Ratio not recorded, Not measured).
  - Projection shrink empties unconsumed `x-source` subtrees to null or `{}` by schema.
- `api.openapi.yaml`: `Lap` and `TraceLap` replace the undefined `clean` with required `comparison_eligible` and `exclusion_reasons` (C# rule, order, join and unmatched case stated); `TuningOpenTarget` states the full `CompatibleOpenTarget` order and presence rules; the track endpoint summary names `normalized_local_vector`.
- `live-frame.md`: bit4 is a hint (receivers compare `session_epoch`); `channel_count` above the decoder's table is rejected; cue `k <= 4` and discriminant checks.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).

### 2026-10-02, wave-1 reconciliation (integration; review: Codex Sol 6.1 for the protocol and ingest items, D15)

Reason: the wave-1 contract change requests (`reviews/wave1-results.md`, W1 to W9) and the behaviour-defining findings of the rerun ingest review (`reviews/ingest-review-codex-sol.md`). Dispositions, with the code each follow-up needs, are in `reviews/wave1-followups.md`. `db-schema.sql` and `manifest.schema.json` are unchanged: setup-file links are derived, not stored, so migration 0001 stays equal to the schema.

- `live-channels.json`: every cue but 14 carries its C# basis (W2, checked against `LiveTelemetry.cs`); new `unit_labels` (`C` to `Â°C`, `F` to `Â°F`, `m/s2` to `m/sÂ²`, `lap` to `laps`); layouts store unit ids. Table version unchanged (no channel appended). Generator output for `unit_labels` is a follow-up.
- `live-frame.md`: presence bit order (bit `i % 8` of byte `i / 8`, least significant first); absent `session_time` written as quiet NaN `0x7FF8000000000000`, any NaN read as absent; text rules referenced to `crate-api.md` 4.7.
- `vectors/live-frame/` (W2, wave 1): golden frames from an independent Python encoder; this entry records their addition.
- `crate-api.md`: `coach_ibt` drops `regex` (collector exe budget; W3) in favour of hand-written token matchers and a setup-key pattern subset; `parse_live` bounds; `_as_int` overflow is None; `driver()` versus `summary()` when `DriverCarIdx` is missing; `- Key:` list items become empty maps; literal-subset errors and i64 overflow; `TooRecent { age: 0 }` for a future mtime (W1). generated `coach_wire::live_channels::unit_label`; `LiveState::channel_values`; `tow_active` and `on_pit_road` presence; `first_of_epoch` on the incrementing state; the C# disconnect-epoch difference; `format_tile` unit and label rule and the .NET text-rule table (W2). Projector input shapes, envelope `schema_version` 1, `profile_version` "" for null, field pruning in the shrink ladder, 128-signal catalog cap with `omitted_signals`, `X-Coach-Trace-Laps` (W5).
- `setup-keys.json`: patterns limited to a subset matchable without a regex engine; patterns themselves unchanged, so every fingerprint is unchanged.
- `api.openapi.yaml`: `ServerStatus.collector_newer_than_server` (W4); compare inline wait is a server setting; tuning context carries no ETag (W4); `ViewEnvelope` version descriptions (W5); `TraceCatalog.signals` maxItems 128, `omitted_signals`, fixed-signal list, bin positions `(i + 0.5) / bins` (W5, W8); CCOL data section alignment stated as a file offset (W8); `Lap.caution`, `Lap.incident_count`, `TraceLap.fastest` removed (no backend source; the browser derives the fastest lap from `lap_time_s`) and `Lap` sources named (W5); `SetupFileEntry.linked_setup_id` derived (W4); `SetupObservation.analysis_id` (W7); `TuningDraftBody` and the turn-annotation `corners` body (W7); `PUT prefs` is a top-level merge (W7); `LiveTile.unit` is a unit id (W2); `ImportBundle.tuning_experiments` replaced by `legacy_analyses` and `legacy_archive` so the server can run W6's `import_legacy_tuning_experiments` (W4, W6); 413 on traces explained.
- `collector-protocol.md`: `hello` lists `live_table_version`; `status.sim` defined; garage edge rules (5 s post-exit snapshot, no-op visits, synthesized snapshot on a name-only change, `.sto` queueing, re-entry closes the old visit before any snapshot) (W3, review 13); "no map" means the mapping does not exist, any other open failure is unknown evidence (review 3); recovery fsyncs before `verified` (review 1); latch set in every stream session while demand stands, demand recounted on a driver change (review 16, 17); setup-file links derived from observations, never by name for an ambiguous visit (W4, review 14); garage events acknowledged only once attributed and stored (review 15).
- `worker-interface.md`: discovery key is `group_id` and `local-file` groups are `time_source == "mtime"` (W4, W6); `raw_source_policy` stays an object in external mode (W6); `HOME` required, `USERPROFILE` on Windows dev (W4, W6); new tool row `import_legacy_tuning_experiments`; the server-derived analyzer digest and profile for the analyze key (W4); section 5 marked landed; cross-platform parity also normalises separators after the root prefixes and compares SQLite as row dumps (W6).
- `nas-layout.md`: restage goes through upload admission and restores the clamped source mtime before `ready/` (review 8, 9).
- `server-config.md` added: the image's environment names (W9's, which the released image already sets), defaults, start-up directories, `healthcheck` (W9 CCR 1-2).
- Proof: `api.openapi.yaml` parses (js-yaml), validates as OpenAPI 3.1 (`@apidevtools/swagger-parser`), all 113 component schemas compile under JSON Schema 2020-12 (ajv), every `$ref` resolves, every `x-source` object carries `x-consumed-fields`; `pnpm gen:wire` and `pnpm gen:theme` regenerated; `cargo check --workspace` passes.

### 2026-10-02, wave-2 reconciliation (architect; review: Codex Sol 6.1 for the `collector-protocol.md` wording, D15)

Reason: the contract change requests the wave-1 follow-up agents raised (`reviews/wave1-followup-results.md`: W4, W6, W7, W9, W1's note). Dispositions and the code each needs are in `reviews/wave2-followups.md`. `db-schema.sql`, `manifest.schema.json`, `nas-layout.md`, `live-frame.md`, `live-channels.json`, `setup-keys.json` and `server-config.md` are unchanged.

- `collector-protocol.md`: section 6 states when a driver change sends `viewers 0` and `stream.release` (only from a previous driver; a connection's first driver without viewers gets nothing) and that `welcome` carries no demand and no layout because both belong to a driver the first `status` names; section 9 sends `layout.set` on every driver change instead of on `welcome` (W4; what the server does).
- `worker-interface.md`: the `import_legacy_tuning_experiments` row names the tool's result counts (no skipped count) and the import-summary keys the server reports (W4).
- `crate-api.md`: 4.7 splits the `fixed2` cue argument (`0.00`, always two decimals) from the steering ratio (`0.##`), as `format.rs` and `engine.json` do (W7). Section 1 lists `regress` under `coach_wire` and records that it does not reach the collector exe (W1 note). 3.6 makes `coach_ibt::is_identity_excluded` and `setup_values_fingerprint` the only implementation of the `setup-keys.json` rules for the server and the collector; the browser's non-`u` `RegExp` agrees.
- `api.openapi.yaml`: `ImportBundle.tuning_drafts` items gain optional `legacy_analysis_id`; the server re-keys each draft to the resolved analysis's `draft_identity_key` or parks it as `legacy_items` (kind `tuning_draft`); `legacy_archive` states that the worker's `tuning/drafts/` files are carried and preserved, not converted (W9).
- Scaffold: `tools/wsl/check.sh` defaults to scope `all` (W6, wave-1 follow-up, entry missing until now); `tools/check.ps1 -LinuxScope` defaults to `all` to match it (W6).
- Proof: `api.openapi.yaml` parses (js-yaml), validates as OpenAPI 3.1 (`@apidevtools/swagger-parser`), all 113 component schemas compile under JSON Schema 2020-12 (ajv), all 260 `$ref`s resolve, all 18 `x-source` objects carry `x-consumed-fields`; `pnpm gen:wire` and `pnpm gen:theme` regenerated with no drift on a second check; `cargo check --workspace` passes.

### 2026-10-03, wave 3: analysis re-queue wording (integration lead; additive clarification, no review needed)

Reason: W9's G1 finding (`acceptance/g1-analysis.md`, Findings for owners): `POST /api/v1/sessions/{id}/analysis` also returns an existing succeeded job with the same key and queues nothing, which the summary did not say.

- `api.openapi.yaml`: the operation gains `x-idempotency`, stating what `server/src/jobs.rs` (`enqueue_tx`, `queue_analysis_tx`) does: queued, running or succeeded jobs with the analysis key are returned unchanged; failed, cancelled or interrupted ones are re-queued under the same `job_id`; a deferred one stays deferred while over the admission ceiling. The summary is unchanged, so no generated file changes.
- Proof: `node tools/wire/gen-wire.mjs --check` reports no drift; on the dev-PC server a second `POST /api/v1/sessions/1/analysis` after a succeeded analysis answered `202 {"job_id":5,"kind":"analyze","state":"succeeded"}` (`acceptance/e2e-dev-pc.md`).

### 2026-10-03, wave 3b: `no_recordings` response (integration, e2e2; additive clarification, no review needed)

Reason: W4's wave-3 report and `acceptance/e2e-dev-pc.md` (W4 minor): `POST /api/v1/sessions/{session_id}/analysis` answers `422 no_recordings` for a session that has no recordings (`server/src/api.rs` `queue_analysis`), and the operation listed only 202 and 404.

- `api.openapi.yaml`: the operation lists `"422"` with the `Error` body, code `no_recordings`, nothing queued. No behaviour changes.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts` gains the 422 response; `wire.schema.json` unchanged, as responses are not component schemas).

### 2026-10-03, wave 3b: Windows path length for `--dev` (integration, e2e2; additive clarification, no review needed)

Reason: W6's wave-3 report and `acceptance/e2e-dev-pc.md` (W6 minor): under a data root about 150 characters deep the dev worker could not create its `analysis-cache` temporary file (290 characters). W6 fixed the archive side (extended-length paths); the recordings the worker opens from the job root stay bound by `MAX_PATH`, which no contract said.

- `worker-interface.md` section 2: a paragraph on the Windows `MAX_PATH` bound for `--dev`: the job-root recording path `<COACH_DATA_DIR>\py\<customer_id>\jobs\<job_id>\telemetry\<original name>` must stay under 260 characters without `LongPathsEnabled`; a longer one comes back from discovery as a per-file `FileNotFoundError` record, and the server fails the discovery `no_session_found`; the archive root is not bound. Linux and the image are unaffected.

### 2026-10-03, polish: setup blobs stay in the spool (polish-docs; additive clarification, no review needed)

Reason: `acceptance/v1-candidate.md` and `e2e-mini-pc.md` found setup (`.sto`) blobs staying in the spool after archiving, which the deletion table did not state.

- `nas-layout.md` section 7: the spool row and a note state that only `ibt` blobs are released after archiving; setup blobs stay because job roots hard-link them (`worker-interface.md` section 3) and no setup restage exists. No server behaviour changes; the behaviour is judged correct for v1.

### 2026-10-03, port phase 1: native decode hand-off (port-decode; review: Codex Sol 6.1 pending, D15)

Reason: port phase 1 (`workstreams.md` wave 5, D2) needs the decode stage's hand-off fixed before any server or image change depends on it; section 7 named a PyO3 module and left the column format open.

- `worker-interface.md` section 6: the orphan rule also covers `coach-decode-*` directories under the driver's `TMPDIR`.
- `worker-interface.md` section 2: `IRACING_COACH_NATIVE_BIN` (path of `coach-analysis`, read only when native stages are listed); the `IRACING_COACH_NATIVE_STAGES` row points at section 7.
- `worker-interface.md` section 7: phase 1 runs as the executable `coach-analysis decode` (crate `crates/coach-analysis`) and hands over a coach column store, format version 1, specified there (request, layout, header, per-type column interface, rejection and versioning rules); the call site is `workflow._load_analysis_telemetry` through the new `native_stages.py`; an unknown or unprovided stage fails the job. PyO3 (`coach_native`) moves to phase 2, which reads the same store. `TelemetryTable` buffer reads move to phase 2 (analyzer bundle).
- `worker-interface.md` section 8: `--native-bin` and the one-command `corpus` gate.
- Proof: `cargo test -p coach-analysis`; `analysis_parity.py corpus` identical on the fixtures, the four worker cases and the private corpus (`docs/08-rewrite/port/phase1-decode.md`).

### 2026-10-03, G3 follow-up: garage re-entry join and unmodified exit name (W3; review: Codex Sol 6.1 pending, D15)

Reason: the G3 rig run (`acceptance/g3-garage.md`) found that iRacing drops `IsInGarage` for 40 to 83 ms before every load and Apply (the collector fix joins those dips, which section 8 contradicted), and that a visit which reloads the file already named, or ends after Revert, commits values only because the SDK name cannot change, so "every committed visit carries the last-loaded file's name" could not hold.

- `collector-protocol.md` section 8: new edge rule, re-entry join: `IsInGarage` back at 1 within 1.5 s of its fall continues the visit (one visit, one commit); the old rule ("re-entering inside the 5 s window closes the open visit first") now applies from 1.5 s. New commit rule: when the name did not change and the exit state has `DriverSetupIsModified` 0, the commit carries the exit name and the hash of that file (`is_modified: false`, `load_type` set, `.sto` queued), since unmodified means the values are that file's; modified or unknown exit stays values only. The exit state is read when the 5 s window closes, not at the exit tick; a visit with no snapshot still sends no commit. The `Modified setups` paragraph notes that such a commit links like any unmodified commit.
- No wire change: `GarageCommit` fields are unchanged. Server code needs no change (it already links an unmodified commit by `sto_sha256`); the `setup_name` description in `api.openapi.yaml` ("null when it did not change within 5 s") is now stale and is left for the contracts owner.
- Proof: `cargo test -p coach-collector` (`garage::tests::*`, `tests/garage.rs::leaving_unmodified_on_the_same_name_commits_that_file_with_its_hash`, `tests/rig_g3.rs`: the reload visit of the real capture now carries its file's name and hash, the discard and revert visits stay values only).

### 2026-10-03, W4: library cleanup operation (server; additive, no review needed)

Reason: the end-to-end runs left test entries in the owner's live library (`acceptance/migration.md` "Not converted or not clean" item 5: `e2e baseline.sto`, `e2e garage capture.sto`, four garage setups, and the synthetic or test recordings and sessions those runs uploaded). The server had no way to remove anything, and a removal by hand in SQLite would miss derived rows and view files.

- `api.openapi.yaml`: new admin operation `POST /api/v1/admin/remove` with `AdminRemoveRequest`, `AdminRemoveReport` (and its parts). Removes named setup file entries and recordings by hash together with their derived rows and view files; `dry_run` defaults to true and lists exactly what a real call would remove. Idempotent, one transaction, logged (server log and `$COACH_DATA_DIR/run/admin-removals.jsonl`). Never touches the NAS, the collector's journal, the worker's archive or user-authored rows; a target with a note, a running job or no NAS copy is `blocked` and left whole. A session that keeps other recordings gets its analysis made current again over the rest (or queued). Additive: no existing operation or schema changes.
- No `GET /api/v1/sessions/{id}/analysis` is added: `GET /api/v1/sessions/{id}` already carries `session.analysis_state`, `analysis_id` and the analyses list, so a second read of the same state would be a second source. `workstreams.md` G4-9 should name the session read (`analysis_state` `ready` and the race view answering 200), which is what the rig did.
- `SessionSummary.analysis_reason` (no schema change): besides `failed` and `deferred`, a `ready` session reports the reason when a newer analysis of its current recordings (or of a newer profile) is deferred, for example `exceeds_memory_budget`; null otherwise.
- Regenerated: `node tools/wire/gen-wire.mjs` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-server` (`tests/admin.rs`, the contract-coverage test in `tests/api.rs`, `tests/jobs.rs`); `node tools/wire/gen-wire.mjs --check` reports no drift.

### 2026-10-03, port phase 1 follow-up: SQLite in raw mode (p2-plan; clarification of the gate that landed with the review resolution, reviewed there: Codex Sol 6.1 finding 10)

Reason: `reviews/port-phase1-review-codex-sol.md` finding 10 made raw mode compare SQLite files as row dumps under the raw allowlist (`tools/parity/analysis_parity.py`, capture format 3); `worker-interface.md` section 8 still said raw mode compared SQLite only as parsed row dumps.

- `worker-interface.md` section 8: raw mode compares each SQLite file as a raw row dump (`raw/<name>.dump.json`) with only the raw allowlist applied value by value (ISO instant in a `*_at` column, `*_ms` or `elapsed_seconds` number, 64-hex `analysis_sha256`, the text rules inside other strings); the parsed row dumps stay a diagnostic. No code changes.

### 2026-10-03, port phase 2: the kernels stage (port-integration; review: Codex Sol 6.1 pending, D15, before the kernels land)

Reason: `port/phase2-plan.md` sections 4.2 to 4.7. Boundary B was PyO3 in process; the units return an ordered value tree the codec serialises, the measured JSON hand-off is 0.3 s against about 6 s of kernels on the 203 MB race, and a child process keeps the build free of a Python ABI and releases its memory on exit.

- `worker-interface.md` section 7, boundary B: `coach-analysis kernels`, a child process per analysis speaking JSON lines (start with the store, the table fields and the steering basis; one call per entry point; `result`, `raises` or `error`; `{"$handle": n}` for the setup series), codec version 1 (`scripts/native_codec.py`, moved from `crates/coach-analysis/tools/kernel_codec.py`); `kernels` requires `decode`; an entry point the build does not provide fails the job; `StoreColumn` sequences over the mapped store and `StoreTable` instead of list copies, the entry points rebound for one analysis without changing the analyzer bundle (`analysis_id` unchanged); the `merge` store (decode layout, `stage: "merge"`, `provenance` blocks, optional per-column `present`) and its `coach-analysis merge` request. Phase 1's sentence that deferred PyO3 to phase 2 is replaced.
- `worker-interface.md` section 6: the orphan rule also covers `coach-merge-*`.
- `worker-interface.md` section 8: the kernel-call count rule.
- Integration (p2-integrate): the child serves `_setup_channel_series` as the handle the contract names (`dispatch::HANDOFF`; the vectors' replay entry still returns the dict); the boundary-B cost sentence now cites the measured race hand-off (`port/phase2-kernels.md`). Proof: `crates/coach-analysis/tests/kernels_handoff.rs`, the `decode,kernels` corpus gate on Windows and WSL.

### 2026-10-03, port phase 3: the writer stage and the mapped store (p3; review: Codex Sol 6.1 pending, D15)

Reason: `port/phase2-kernels.md` section 5 (boundary D, child memory). Boundary D named `storage._json_text` and `workflow._write_json_cache` and stage `json_writer`, with no protocol; the kernels child read the whole store into memory.

- `worker-interface.md` section 7: the stage is `writer` (`json_writer` is now an unknown name; nothing ever provided it); boundary D is `coach-analysis write`, one run per serialisation of the analysis object at three sites (`analysis.json` bytes, the cache envelope's `analysis_sha256`, every `_write_json_cache` file), a request line plus a protocol-5 pickle, answers `ok`/`raises`/`python_exception`/`fallback`; the counted fallback for values outside the writer's model is the one exception to the no-fallback rule. Other `_json_text` writes stay Python.
- `worker-interface.md` section 7, column store: optional header field `alignment` (4096: header padded with spaces, data section and every block on a page); format version stays 1; the kernels child and the merge stage map the store.
- `worker-interface.md` section 8: the writer count rule (site calls equal on both captures, every native-side call written natively, no fallback) and the `analysis_sha256` recomputation (`outputs/analysis-sha256-check.json`); capture format 5.
- Proof: `crates/coach-analysis/tests/writer.rs` (926 oracle vectors, `tools/emit_writer_vectors.py`), `tests/decode.rs` (page alignment, mapped read), `analysis_parity.py corpus --native-stages decode,kernels,writer` on Windows and WSL (`port/phase3-writer.md`).

### 2026-10-03, port phase 3 follow-up: the writer's counted fallback (W6; clarification of the behaviour reviewed there: Codex Sol 6.1 findings 1 and 4)

Reason: `reviews/port-phase3-review-codex-sol.md` (Disposition, follow-up) and `port/phase3-writer.md` section 7: since finding 1 the seam stops the pickle at any object outside the exact types without calling it, and the site's own Python runs once as a counted fallback; `worker-interface.md` still said such an object became the marker function with the JSON text or exception the pickler recorded for it. The review placed the sentence in section 8; it is in section 7, boundary D.

- `worker-interface.md` section 7, boundary D: an object outside the exact types (and a pickling `RecursionError`) starts no child and is a counted fallback (`native_stages.writer_fallback`), the site's code run once from the start; the seam sends no marker; the request still names the marker global and the reader still accepts it, so the request and answer formats are unchanged; a `python_exception` answer is a `NativeStageError`, as `native_stages.writer_call` already raised.
- `worker-interface.md` section 8: the writer rule says what a fallback is (the writer's `fallback` answer or the seam's stop, both counted). No behaviour changes.
- `contracts/compatibility.json` (generated by `tools/export_contracts.py`): `IRACING_COACH_NATIVE_STAGES` lists the stages as `decode, kernels, writer` (was `json_writer`).
- Proof: `test_native_stages.WriterSeamTests.test_the_contract_describes_the_counted_fallback`, `test_worker_interface.CompatibilityContractTests.test_the_native_stage_list_names_the_seam_stages`; `export_contracts.py --check` green.

### 2026-10-03, W4: native stages setting, native admission, orphan sweep, one deferred row (W4; follow-ups 1 to 4 of `acceptance/g1-native.md`; additive, no review needed)

Reason: the server forced `IRACING_COACH_NATIVE_STAGES` empty, so the image enabled the stages through an interpreter shim; its admission and sweep still modelled the pure-Python worker; re-asking for a deferred analysis after the analyzer changed added a second deferred row.

- `server-config.md`: new section 3 and the setting `COACH_NATIVE_STAGES` (default `decode,kernels,writer` when the executable is present, none otherwise, `none` to turn off) with `COACH_NATIVE_BIN`; start-up fails on an unknown stage, `kernels` without `decode`, or a listed stage without its executable. The image's shim `python3.12-native` and `COACH_PYTHON` pointing at it are no longer needed (W9's release).
- `worker-interface.md` section 2: `IRACING_COACH_NATIVE_STAGES` and `IRACING_COACH_NATIVE_BIN` come from that setting. Section 6: orphan rule states when and where the server removes `coach-decode-*` and `coach-merge-*` (kill with the driver idle, driver going idle, server start); admission is 5.0 x bytes + 0.15 GiB (x1.5 for a group, ceiling 2.0 GiB, shared budget 2.7 GiB, container `mem_limit` 3 GiB) with native `decode` and `kernels`, 11.5 x, 3.0 GiB, 3.7 GiB and 4 GiB otherwise, with the N100 measurements; a deferred analysis is one row per session.
- `api.openapi.yaml` is unchanged: `x-idempotency` already says a deferred analysis stays deferred while over the ceiling; the `job_id` it answers is the session's one deferred row.
- Proof: `cargo test -p coach-server` (`config::tests`, `tests/jobs.rs`: the native formula against the N100 peaks and the Bristol estimates, the Python formula kept, the worker environment and a worker that reads it, the scratch sweep after a kill, at idle and at start, one deferred row per session).

### 2026-10-03, wave 4: `companion-app/` deleted, its CI removed (W9; scaffold change; no protocol change, no review needed)

Reason: `workstreams.md` wave 4 ("W9 deletes `companion-app/` and its CI"); the owner retired the desktop app. It is in Git history, last present at `3ff5e87`.

- Scaffold `.github/workflows/ci.yml`: the first-party JavaScript syntax step, its evidence upload and the `javascript` evidence normalizer are removed (the gate scanned only `companion-app/` JavaScript). `tools/check.ps1` and `tools/verify-repository.ps1` never referenced the app and are unchanged.
- `crate-api.md` (source facts) and `worker-interface.md` section 5 item 5: wording only; the C# sources are named as Git history, and the C#-reading-tests precondition is recorded as met.
- Outside the contracts, in the same commit: `tools/Invoke-JavaScriptSyntaxGate.ps1` and its tests, the `javascript` family of `tools/New-EvidenceReport.ps1`, the 278 `dotnet` declarations of `tools/evidence/evidence-declarations.json` (their fixture paths pointed into the deleted tree) and `crates/coach-live/tools/csharp-oracle/` (built only against the deleted sources; the committed `engine.json` is the record). Proof: `tools/dev/tests/test_desktop_app_retired.py`.

### 2026-10-04, web round data: series and fixed setup per session, session filters, sector times, pit stops, prefs (server; additive optional fields, no review needed; one new projection version)

Reason: the web round (`backlog.md` 1i, 1j, 1k, 1l, 1m, 1a, 2; `reviews/web-semantics-audit.md` T5, T8, S2) needs data the API did not carry. Projection version 1 becomes 2 so stored analyses are re-projected (the server queues the projection on the first request for a stale view; the browser retries on `analysis_ready`).

- `api.openapi.yaml`:
  - `SessionSummary.is_fixed_setup` (`WeekendOptions.IsFixedSetup` of the first recording that states it; null when none does); `series_name` already existed and is now described. `SessionDetail.event_sessions[]` gains `analysis_state` and `analysis_reason`, so the phase selector offers only phases that can open.
  - `GET /drivers/{id}/sessions`: repeatable `car`, `track`, `series` and `type` (alternatives within one, all filters must hold), and `setup=fixed|open`. A single `type` still works. `CarTrackOptions.series` lists the series options.
  - `GET /drivers/{id}/setups`: `raced=true|false` (never raced is the new case; `raced_only` unchanged).
  - `POST /setups/compare` answers 422 `different_schemes` when one setup is known only from a garage export and the other from a recording (audit U3); the contract's 422 text names the three codes.
  - `RaceView.pit_stops[]` (`PitStop`): one entry per run that ends in a pit service, projected from `runs[].pit_service` (lap, service window, tires changed, fuel added in litres and gallons, post-run flag).
  - `TraceLap.sector_times_s[]`: seconds per sector from the lap's `session-time` bins at `TraceCatalog.sector_start_pcts` (the retired desktop app's interpolation rule; bin resolution; null where the lap's bins do not reach a boundary).
  - `DriverPrefsWrite.text_size` (`TextSize`: default, larger, largest), `session_filters` (`SessionFilters`) and `setup_filters` (`SetupFilters`); `text_size` was already round-tripping as an unknown field.
- `crate-api.md` section 5: `PROJECTION_VERSION` 2; the two projected fields.
- `GET /analyses/{id}/traces` is unchanged: any channel the catalog lists is served from the CCOL when named in `channels`; only the default eight come without it. The server's built-in `default` trace layout is now the six critical channels, one row each (Throttle and Brake were one row).
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).
- Proof: `cargo test -p coach-views` (`sector_times_*`, `pit_stops_*`), `cargo test -p coach-server` (`tests/library.rs`, `tests/views.rs`), `node tools/wire/gen-wire.mjs --check`.

### 2026-10-04, setup identity version 2: the bar preload leaf, export keys, start-up reconciliation (setup identity fix; review: Codex Sol 6.1 pending, D15)

Reason: the setup harvest of 2026-10-04 (`iRacing-utility`, `lab-notes/harvest-2026-10-03.md`): every garage visit left its snapshots (setups 69 and 70) beside the named commit (71), differing only in `Chassis.FrontArb.Preload`, a value the sim recomputes (P5; the Prove-wave ruling on D9 excludes derived keys). Version 1 excluded the leaves `ArbPreload` and `TruckArmPreload` only, while the Supra, Tundra and Taurus publish the anti-roll-bar preload as a leaf named `Preload` in the bar's section, and the Taurus its rear bar's as `Chassis.Rear.Preload` beside `ArbDiameter` and `LinkSlack`. The 16 setups of the migration's `.htm` exports (53 to 68) showed as "Unnamed", and their snake_case keys matched no pattern.

- `setup-keys.json` version 2: `^(Arb|TruckArm)Preload$` becomes `^(Arb|TruckArm)?Preload$` (the subset allows `?` after its group), so the bare `Preload` leaf is derived wherever it is published; the table keeps 7 entries. Patterns ignore `_` in a key's segment, so an export's `chassis.left_front.corner_weight`, `chassis.front_arb.preload` or `tires.left_front.last_temps_omi` is excluded like its `CarSetup` spelling; no pattern holds `_` and no `CarSetup` key does, so no `CarSetup` key changes class. Corner weights, ride heights, last temperatures and pressures, tread and the revision counter were already excluded; no other key P5 lists is missing.
- Evidence that no user-set leaf named `Preload` exists: the G3 fixture, whose only `Preload` leaf is `Chassis.FrontArb.Preload` (`crates/coach-ibt/tests/setup_identity.rs`), and the `CarSetup` of the owner's 49 recordings on the dev PC (Supra 32, Tundra 12, Taurus 5; read in place, nothing committed): every `Preload` leaf sits in an anti-roll-bar section with the bar's `LinkSlack`, and the Supra's unmodified recordings of one file name differ there, in corner weights, truck-arm preload and run readings, and between two tracks in `Chassis.Rear.RearEndRatio` (kept as a setting). A car that publishes a user-set leaf named `Preload` (not seen) would need a narrower rule.
- `crate-api.md` 3.6: the optional group, the `_` rule, and when the browser's `RegExp` agrees.
- Server, no wire change: at every start, before any listener, `src/setup_identity.rs` recomputes each stored setup's fingerprint from its values, rewrites those that changed and merges setups of one driver and car whose fingerprints now coincide. The named setup survives (an unmodified recording or garage commit gave it a name), else the first seen, then the lowest id; observations and notes move to it, so names, `.sto` hashes, sightings and file links (all derived from observations) follow; it spans the group's first and last sightings and keeps the latest values; setups without any observation are removed unless a note names them. One transaction; the report goes to the server log and one line of `$COACH_DATA_DIR/run/setup-identity.jsonl`; a start with nothing to change writes nothing. Exact because the rule only excluded more keys: observations keep no values of their own, so a rule that excluded fewer keys could not split stored setups (it would need the sources ingested again).
- Server: a setup with no known `.sto` name is named after its linked `.htm` exports' file names without the extension (`SetupSummary.names`). The `names` description in `api.openapi.yaml` ("Known .sto names") should say so; left for the contracts owner, as that file and its generated `web/` types were being edited by the web round.
- Regenerated outputs (`node tools/wire/gen-wire.mjs`): `crates/coach-wire/src/gen/setup_keys.rs` and `web/src/gen/setup-keys.ts` (`VERSION` 2, the one pattern).
- Proof: `cargo test -p coach-ibt` (optional-group and `_` cases; on the fixture, publishes that differ only in derived values share a fingerprint and a visit's last snapshot and its Apply & Close values are one setup), `cargo test -p coach-server` (`tests/setup_identity.rs`: the 69/70/71 shape and the export shape, a second start unchanged, the next visit's snapshot joining the named setup; `tests/jobs.rs`: an export's name), `node tools/wire/gen-wire.mjs --check`.
