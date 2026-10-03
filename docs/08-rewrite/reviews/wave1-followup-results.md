# Wave 1 integration: follow-up reports (2026-10-02)

Reconciliation: 46 change requests accepted, 1 rejected. Reconciler summary:

I reconciled all 47 wave-1 change requests: 46 accepted (8 of them with a change) and 1 rejected (W7's baseline purpose field, because the Python tool takes no such argument). Disputed calls:
- Setup-file links are derived from observations, not stored in a new column, so migration 0001 stays equal to db-schema.sql.
- coach-ibt drops regex to protect the collector's 3 MB budget. A workspace-wide opt-level "s" was rejected because it would also shrink-optimise the server.
- W9's environment names become the new contract server-config.md, since the released image already sets them.
- Import bundles now carry the legacy archive files so the server can run W6's import tool.

The Codex ingest review rerun appeared mid-task (reviews/ingest-review-codex-sol.md: 1 blocker, 22 major findings). Where it showed a contract gap I clarified collector-protocol.md sections 4, 6 and 8 and the nas-layout.md restage rule. Every code finding is assigned in wave1-followups.md, and risks.md gains R22 to R24.

Acceptance:
- api.openapi.yaml: parses (js-yaml), valid OpenAPI 3.1 (swagger-parser), 113 schemas compile under ajv 2020-12, all 260 $refs resolve, every x-source object has x-consumed-fields: PASS.
- pnpm gen:wire and pnpm gen:theme: regenerated; a second run shows no drift.
- cargo check --workspace: PASS.
- pnpm -C web typecheck: PASS.
- cargo test -p coach-ibt and -p coach-views: PASS.
- cargo test -p coach-live: engine_vectors fails as its own guard intends, now that the cue bases match C#. W2 fixes it by emptying PENDING_BASIS_IDS.

Files: C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/contracts/ (README.md change log, new server-config.md), C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/reviews/wave1-followups.md, C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/risks.md. Nothing committed.

## Follow-up assigned to the architect (not yet applied)

Update architecture.md section 8: the collector has its own http, ws and logging (no ureq, tungstenite or tracing), coach-ibt has no regex, and coach-server uses jsonschema at runtime. In section 10, record the NAS uid 1024 mapping and the first-release RECOVERY_PATHS and healthcheck values, and point to server-config.md.

## Codex ingest review

ok=True; findings=25; blockers=1; Codex ran with danger-full-access and read files fine. git status differed: before was only `?? .debate/`; after also showed modified docs/08-rewrite/contracts/{collector-protocol.md,crate-api.md,live-channels.json,live-frame.md,setup-keys.json,worker-interface.md} plus untracked contracts/server-config.md. Codex said it changed nothing, and these look like a concurrent workflow, but that is unverified and I did not fix it. Blocker count is from Codex finding 1 only; I did not count severities beyond it.

## W1 - done, acceptance passed: True

All three W1 follow-ups are done. coach-ibt no longer uses regex, the goldens regenerate byte-identical, and the private-corpus parity passes.

1. Removed `regex` from `crates/coach-ibt/Cargo.toml`. The `Cargo.lock` change is one line, dropping `regex` from coach-ibt's dependency list. New file `src/matchers.rs` holds hand-written matchers that accept the same languages as the five old patterns (YAML integer and float; Python int, float and imaginary tokens). It also holds `LeafPattern`, the setup-keys.json subset matcher with ASCII case folding. `gate.rs` uses it, and the test `every_contract_pattern_is_inside_the_subset` fails on any pattern outside the subset. Unit tests cover accepted and rejected inputs. An uncommitted scratch check compared each matcher with the old regex on 199,457,865 inputs and found 0 differences. One deliberate change from the contract: non-ASCII case folds no longer match (the Kelvin sign is not `k`). Fingerprints are unchanged.
2. Fixed `tools/emit_session_golden.py`: its path redaction looped over a set, so the result depended on the hash seed and an absolute path could leak into a golden. It now replaces the longest spelling first. Goldens are byte-identical for seeds 0-5 and for relative, absolute and other-directory path forms.
3. Private corpus: ran the parity test on 49 local recordings (Documents\iRacing\telemetry; the 15-file local raw archive, including 582b11e2 and the Iowa race, also passes on its own). The Python and Rust `driver_setup_modified` values agree on every recording (all `false`).

Acceptance:
- `cargo test -p coach-ibt`: 33+1+8+2 pass, 1 ignored.
- `cargo clippy -p coach-ibt -- -D warnings` and with `--all-targets`: clean. `cargo fmt --check`: clean.
- `cargo tree -p coach-collector -e normal -i regex` (also `--target all`): nothing to print.
- emit_session_golden then `cargo test -p coach-ibt --test parity`: 2 pass, goldens unchanged.
- `COACH_PRIVATE_CORPUS=... cargo test -p coach-ibt --test parity -- --ignored`: 15/15 and 49/49 recordings checked, pass.
- Truncated fixture rejected: `truncated_fixture_is_rejected` passes.
- WSL Ubuntu: clippy `--all-targets` clean, tests pass.

### Contract change requests

none. Note for W3/architect: coach-wire still links `regress` (typify's pattern engine) into coach-ibt's normal tree; it is not `regex` and is outside W1's paths.

## W2 - done, acceptance passed: True

Applied the three W2 follow-ups.

1. crates/coach-live/tests/engine_vectors.rs: removed PENDING_BASIS_IDS and the machinery that depended on it (contract_basis, Tally.csharp_bases, the cue_id parameter of check_tiles, and the "now matches C#" assertion). Every tile secondary line is now compared against C# unconditionally. Dropped the stale "Cue bases" entry from tests/vectors/README.md.
2. tools/wire/gen-wire.mjs: validateLive now fails if a unit_labels key is not listed by any tile. It emits `unit_label(unit) -> &str` into live_channels.rs and `unitLabel(unit)` (a Map lookup, falling back to the id) into live-channels.ts. Ran `pnpm gen:wire`; only the two live-channels outputs changed.
3. crates/coach-live/src/tiles.rs: deleted the private unit_label match and imported coach_wire::live_channels::unit_label. The old "time" => "" arm was dead, because no tile or channel uses a "time" unit.

Acceptance, all run by me:
- `cargo test -p coach-live`: all suites pass, including engine_vectors, frame_vectors and conformance.
- `cargo clippy -p coach-live -- -D warnings`: clean.
- `cargo bench -p coach-live`: engine tick median 300 ns, p99 600 ns (limit 20 us).
- `node tools/wire/gen-wire.mjs --check`: no drift, after `pnpm gen:wire`. I did not run `git diff --exit-code`, because the tree is dirty from other agents' work.
- `pnpm -C web typecheck`: passes.
- `cargo check --workspace`: passes.

No new test was added. Behaviour is unchanged, and the existing vector test already checks every tile unit label against C#. I touched nothing outside crates/coach-live/, tools/wire/gen-wire.mjs and its two regenerated outputs (crates/coach-wire/src/gen/live_channels.rs, web/src/gen/live-channels.ts).

### Contract change requests

none

## W3 - partial, acceptance passed: True

Applied follow-up items 1 to 8 under collector/; item 9 (record-sdk 30, G3 on the sim) was NOT run.

Acceptance, all run by me:
- `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc`: exe 1,578,496 B (limit 3 MB), sha256 7c695fef1149150b75a935f85786d3c7c13404e416c40052662e8ad7f9e4bac4. W1 had already removed regex: `cargo tree -p coach-collector -e normal -i regex` prints nothing.
- `cargo test -p coach-collector`: 131 passed, 0 failed, 3 ignored (the manual UI and install tests). The same suite passes in WSL Ubuntu.
- `cargo clippy -p coach-collector --all-targets -- -D warnings`: clean on Windows and WSL. `cargo fmt --check` and `cargo check --workspace` pass.

Changes, each with a regression test: garage re-entry closes the old visit first and the new visit starts from the old count. The `.sto` signature is checked across hashing. The journal repairs a torn tail, retries failed appends in order, and sends nothing while the file is unavailable. Chunk writes use a non-blocking socket polled every 2 ms (abort within 100 ms with a stalled server). Only a missing mapping counts as "no map"; I also fixed the throttled-pass `NoMap` flip after a close. Scans and journal matching check the gate per entry and are kept when interrupted. The stream latch is demand at each session start, and demand is dropped on a driver change.

Disposition rows for findings 2, 3, 4, 10, 11, 12, 13, 16 and 17 (collector half) are appended to the review file under "W3 dispositions". All are accepted; none needs a contract change.

Item 9 was skipped: the run rules say no task needs the sim and not to use it, which conflicts with the task text. It is still to run: the G3 dry-run build and record-sdk.

### Blockers

Item 9 (record-sdk 30 and G3 with the --dry-run build on the sim) was not run. The run-wide rules say the sim PC is not needed in this run and must not be used, which conflicts with the task text. Needs a run that permits the sim, with iRacingSim64DX11 confirmed not running first.

### Contract change requests

None.

## W4 - done, acceptance passed: True

All ten W4 follow-ups and every ingest-review finding on server/ are done. Changes are in `server/` plus the root `Cargo.toml` pins; nothing is committed.

Follow-ups:
- Config reads the `server-config.md` names, tested over the image environment.
- Worker results are validated against analyze-result-v1, setup-package-v1 and tuning-recommendation-v1; a mismatch fails the job as `tool_error`.
- `SetupObservation.analysis_id` added; name-only linking is barred for ambiguous visits.
- Garage events are acknowledged only once attributed and stored.
- Imports run `import_legacy_tuning_experiments` on the light lane.
- Dependency pins moved to the workspace.
- The viewer recount, slow-browser limit and worker supervision changes are in.

Review: all 17 findings that touch server/ accepted (15 with change); the other eight are W3's. Each has a regression test, including child-process kill tests at every upload and archive cut.

Results, all run by me:
- `cargo test -p coach-server` on Windows: 12+5+4+3+5+28+22+9+10 pass, 0 fail, stable over 3 runs.
- `cargo clippy -p coach-server --no-deps --all-targets -- -D warnings`: clean.
- `cargo fmt -p coach-server --check`: clean.
- `wsl -d Ubuntu -- bash tools/wsl/check.sh server`: "WSL check passed (server)". This includes the SIGTERM-ignoring-descendant test and idle RSS.
- `cargo check --workspace` and `cargo check -p coach-server --locked`: pass.
- Real worker: analysis output meets analyze-result-v1; the real import tool ran on a generated legacy archive.

Also removed seven tracked junk files from `server/%SystemDrive%/` (unstaged deletions).

Dispositions and two contract change requests are appended to ingest-review-codex-sol.md under "W4 dispositions".

### Contract change requests

1. collector-protocol.md section 6 (driver change): state that the zero-demand `viewers 0` and `stream.release` follow a change from a previous driver. The first `status` after `hello` has no demand to cancel, so the server sends nothing for a driver without viewers (it already behaves this way).
2. worker-interface.md section 4, `import_legacy_tuning_experiments` row: the tool returns `experiment_count`, `resolved_count` and `unresolved_count` with no "skipped" count. The server reports them in the import summary as `tuning_experiments_imported`, `tuning_experiments_resolved` and `tuning_experiments_unresolved`, plus `legacy_analyses`, `legacy_analyses_resolved` and `legacy_analyses_unresolved`.

## W5 - done, acceptance passed: True

Both W5 follow-ups applied inside crates/coach-views/.

1. `src/traces.rs`: MAX_SIGNALS raised from 64 to 128. `omitted_signals` now lists only the keys beyond 128, because the existing loop already pushes only past the cap. In `tests/synthetic.rs` the old 52-extra test became a 130-extra test: 128 signals served, 128 CCOL columns, omitted = s113..s129 (17 keys), TraceCatalog schema-valid. A new test covers the 76 real signals (15 fixed + 61 extra): all served, `omitted_signals` absent, schema-valid, 76 CCOL columns.

2. `examples/project_file.rs` renamed to `examples/project.rs` (nothing else referenced the old name), doc comment updated. Usage: `cargo run -p coach-views --example project -- <analysis.json> <out_dir>`. I ran it on a synthetic analysis.json the tests had written. It wrote race, runs, investigation, traces-catalog, traces.ccol, track and tuning and printed the summary. The test-data view and race-investigation JSON files are not analysis.json, so it correctly rejects them with "missing required field analysis_id".

Acceptance, run with CARGO_TARGET_DIR=target/W5:
- `cargo test -p coach-views`: all pass. Unit 29, api_shape 1, consumed 2, fixtures 11, synthetic 30; the corpus test is ignored because COACH_PRIVATE_CORPUS is not set.
- `cargo clippy -p coach-views --all-targets -- -D warnings`: clean.
- One early synthetic run failed `bins_per_lap_follows_the_backend`. A rerun of that test passed, and three further full synthetic runs passed 30/30. I did not find the cause; I suspect scratch-directory file locking on Windows.

Not done: the private-corpus run serving all 76 real signals was not executed, as no corpus path was given. The 76-signal test stands in for it.

### Contract change requests

none

## W6 - done, acceptance passed: True

I applied all three W6 follow-ups. Nothing is committed.

1. **WSL check default.** `tools/wsl/check.sh` now runs `all` when given no scope, and the "17 tests fail" comment is gone.

2. **G2 fixture.** `python tools/generate_fixtures.py --g2` builds a synthetic 82-lap race (85 MB, 150 channels, one four-tire stop), analyses it in process with the current profile and projects it with coach-views' `project` example. It writes all seven views into `test-data/g2/` (catalog: 77 signals, 83 laps; `traces.ccol` 6.8 MB), plus a `fixture.json` manifest with each file's size and hash. Paths in the output are rewritten to the container's paths and timestamps are fixed; a second run gave identical output. `verify_repository.py` checks the files against the manifest; two new tests cover it.

3. **No more `companion-app` reads.** `export_contracts.py` no longer parses `DurableArchive.cs`: the two version fields it read are frozen at 1 (new authority `retired-csharp-symbol`). The generated `compatibility.json` is unchanged. The version-registry and toolchain tests no longer read `companion-app`; the evidence declarations were updated to match.

I also fixed 4 tools/dev test failures that were already there before my changes: the nine `analysis-view-*.json` test vectors lacked four empty sections the schema now requires.

**Acceptance results:**
- `verify-repository.ps1 -PythonPath $py`: OK, 1377 tests (1370 passed, 7 skipped); also OK on a copy of the repo with `companion-app/` removed.
- tools/dev suite: 175 OK, 1 skipped (OK without `companion-app/` too).
- WSL `python3.12 -m unittest discover -s iracing-coach/tests`: 1377 run, OK (26 skipped).
- `check.sh` with no argument (scope `all`) on committed HEAD plus my changes: "WSL check passed (all)". In the live tree its clippy step fails on W4's in-progress `server/src/worker.rs`, not on my changes.
- Parity, Windows against WSL, raw: synthetic race and the G2 race both identical (10/10 files).
- `cargo test -p coach-views` and the coach-views corpus test on the G2 analysis: pass. `coach-server --test contracts`: pass.

### Blockers

I could not run `pnpm -C web perf:g2` on the new fixture: the harness still builds its own TypeScript fixture, so W8 (follow-up item 4) has to point it at `test-data/g2/`. I did not re-run the private-corpus parity because `iracing-coach/` is unchanged since W6's last run.

### Contract change requests

1. Scaffold: the `contracts/README.md` change log has no line recording the W6-8 change to `tools/wsl/check.sh` (default scope `all`); the reconciler may want to add one.
2. `tools/check.ps1` still defaults `-LinuxScope` to `rust`. Align it with `check.sh` or keep it as is.
3. W8: load `test-data/g2/{traces-catalog.json, traces.ccol, track.json, race.json, runs.json, investigation.json}` in `web/perf/` for gate G2.
4. W9, wave 4: `tools/Invoke-JavaScriptSyntaxGate.ps1`, its tests and the matching `ci.yml` step check `companion-app/` JavaScript paths. Their tests pass without the folder (they use made-up paths), but the gate has nothing to check once it is gone; retire it with the .NET CI job.
5. `contracts/compatibility.json` still says the UI is C#, .NET 10, WPF and Blazor. I left it unchanged; correcting it is a Python-contract change for a later pass.

## W7 - done, acceptance passed: True

All four W7 follow-ups applied. Nothing committed.

1. Setups: `tuneTarget()` in `web/src/setups/model.ts` takes the newest recording observation with `analysis_id`; `setup-detail.tsx` navigates with it and sends no session request. Tests: model unit test plus an e2e assertion of zero `/sessions/{id}` requests. The mock now fills `analysis_id` from the session's current analysis.
2. Live tiles: new `live/text.ts` holds the .NET text rules (ties-to-even `F{n}`, 15-digit `0.00` and `0.##`, `P0`, truncating lap time). `tiles.ts` is rewritten as the twin of `format_tile`: unit labels from generated `unitLabel`, a unit id the tile does not list falls back to the default, every cue shows its basis, and tile text matches Rust (gear numeric, `Leader` keyed on position, en-dash pace range, secondary texts). The editor shows unit labels and hides Decimals where the overlay fixes 0. Tests: `text.test.ts`, and `tiles.test.ts` replays all 2,870 tile readings of `engine.json` (only the accepted 1 ms f32 pace-range difference is tolerated); layout test rejects labels.
3. `tuning/model.ts`: `DraftBody` is derived from `Schemas["TuningDraftBody"]`.
4. Mock: `fastest`, `caution` and `incident_count` were already absent; added `omitted_signals: []`; envelope `schema_version` set to 1.

Acceptance, run by me: `pnpm -C web typecheck` clean; `pnpm -C web test` 23 files, 192 tests pass; `pnpm -C web build` passes (entry chunk 10 KB gzip); `pnpm -C web e2e --project=app` 105 passed, 1 skipped.

Contract text defect: the crate-api 4.7 table says `fixed2` is `0.##`, but the vectors and `format.rs` use `0.00` ("0.70", "1.00"). I followed the vectors; the table row should be corrected.

### Contract change requests

crate-api.md 4.7 text-rules table: the `fixed2` cue argument row says `0.##` (trailing zeros dropped). `engine.json` and `crates/coach-live/src/format.rs` use `0.00` ("0.70 s", "1.00 s"); `0.##` applies only to the steering ratio. Correct the table row.

## W8 - partial, acceptance passed: True

Follow-ups 1-3 are applied. Follow-up 4 (G2 on W6's 82-lap fixture) is not done, because that fixture has not landed; G2 ran on the regenerated synthetic fixture instead.

1. Bin positions: `geometry/trace-model.ts` falls back to (i + 0.5) / bins when the table has no lap_pct channel. `cursor/telemetry-cursor.ts` arrow-key step is now 1 / bins. The synthetic fixture's lap-pct and curvature use bucket centres. Geometry tests cover the first and last bins and the no-lap_pct fallback.
2. 413 on traces: `load.ts` repeats the request by lap ranges (halves, recursively, `lapRangeText` writes `1-40,52`) and joins the bodies with the new `joinCcol` in `ccol.ts`. `ensureSignals` uses the same path. A single lap still over the bound fails as a 413. `joinCcol` widens a channel to f64 when its dtype differs between bodies. Tests use a mock 413 server: halving order, identical joined table, extra channels, single-lap failure, join with mixed dtypes.
3. `fixtures/synthetic.ts` no longer sets `fastest` or `incident_count`.
4. W6's committed catalog and `traces.ccol` are not in `test-data/` yet. G2 on the synthetic fixture passes at 6x throttle (median of 3, 0 requests): phone 2330 ms first interactive, 60.1 driven fps, 59.2 real fps; tablet 2069 ms, 58.7 driven, 57.3 real. Re-run `pnpm -C web perf:g2` once the fixture lands and the harness points at it.

Acceptance, all run by me:
- `pnpm -C web typecheck`: pass.
- `pnpm -C web test -- investigation`: 82 tests pass.
- `pnpm -C web test`: 192 tests pass (23 files).
- `pnpm -C web perf:g2`: PASS, result in `target/W8/g2-result.json`.

I also added a README sentence on the 413 remedy and bin positions in `web/src/investigation/README.md`. Paths touched: `ccol.ts`, `load.ts`, `geometry/trace-model.ts`, `cursor/telemetry-cursor.ts`, `fixtures/synthetic.ts`, and `__tests__/{ccol,load,geometry}.test.ts` under `web/src/investigation/`.

### Blockers

Follow-up 4 waits on W6 committing the 82-lap catalog and traces.ccol under test-data/ (W6 item 2). The G2 harness would also need a small change to serve that fixture instead of the synthetic one.

### Contract change requests

none

## W9 - partial, acceptance passed: False

Item 1, deploy: `deploy/mini-pc/docker-compose.yml` now runs `/usr/local/bin/coach-server healthcheck`. I tested it against the tree's server on the dev PC: exit 1 with no listener, 0 while `serve --dev` was up, 1 after it stopped. At start that server created `db/` and `snapshots/`. `app.env` still has `RECOVERY_PATHS=.`, because the released skeleton never created `db/` and `g-house-stack` refuses a missing path. The switch belongs in the release after the first real-server release, and that release waits for W4 items 7 and 8 (R23). Both steps are recorded in `acceptance/first-release.md`. I made no release, as this run requires.

Item 2, migration: `tools/migrate/build_import.py` is new, since there was no file to change. It writes `legacy_analyses` and `legacy_archive` with no `tuning_experiments`, converts unit labels to ids from `live-channels.json`, and maps the old drafts to `TuningDraftBody` fields. The export and inventory scripts now also cover `trials`, `event-plans`, `ai-coaching` and the raw-archive manifests, which supply the recording hashes. `migration.md` waits for the sim copy (wave 3).

Item 3: the Dockerfile environment matches W4's IMAGE table.

Commands:
- `$py -m unittest discover -s tools/migrate -p "test_*.py"`: 10/10 OK
- ajv check against `ImportBundle`: synthetic bundle and dev-PC copy both valid (18 analyses, 17 with a hash)
- `verify-repository.ps1`: 1370 passed, 0 failed, 7 skipped
- `release.sh`: not run (no mini-PC changes allowed in this run)
- `g-house-stack status`: healthy (old skeleton image)
- `curl /health`: 200
- `curl /api/v1/status`: 404 (skeleton)

### Blockers

The W9 acceptance (release.sh, then status with nas.state = ok) needs a real-server release. This run does not allow mini-PC changes, and R23 holds the release until W4 items 7 and 8 land. The RECOVERY_PATHS switch can only follow that release. W4's in-progress test `config::tests::production_defaults_equal_the_image_values` fails on a Windows host: the default worker path is built with a backslash (`scripts\mcp_server.py`). The image values themselves match. Side effect: while testing the healthcheck I ran `taskkill /F /IM coach-server.exe`, which would also have stopped any other agent's coach-server.exe running at that moment.

### Contract change requests

1. `ImportBundle.tuning_drafts`: `identity_key` is the old file name, a key built from the old `analysis_id`. A new tuning context computes `draft_identity_key` from the new `analysis_id`, so imported drafts never attach. Proposal: the server recomputes the key from the resolved analysis, using the old identity that `build_import.py` keeps under `body.legacy.representativeSession` (recording hashes, subsession, car, setup fingerprint). Alternatively, add an optional `subject_ref` to each draft. 2. Python tuning drafts (`data/tuning/drafts`) reach the server only inside `legacy_archive`. The contract should state that this is enough, or name a slot for them.

