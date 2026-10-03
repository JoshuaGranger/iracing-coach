# Wave 1 results and contract change requests (2026-10-02)

Structured reports returned by the nine wave-1 workstream agents, verbatim. Contract change requests (CCRs) are reconciled by the integration step; the Codex ingest review did not run (sandbox failure) and is rerun in integration.

## W1 - done, acceptance passed: True

W1 is done: crates/coach-ibt/ implements the full contract API, and every acceptance command passes except the private-corpus run, which could not be done here (no corpus).

Built (src/): SDK header, IBT and live variable tables with raw-value readers; IbtFile::open in Python's bounds order; session-info decoding (BOM, cp1252 fallback); the ibt_reader YAML subset and _yaml_scalar, including the review's literal_eval rules; the last-record edge read; the _extract_summary port; typed extracts reading DriverSetupIsModified with fallback to DriverSetupModified; finalization gate (Windows writer check); streaming SHA-256; setup fingerprint. Scaffold #![allow] removed.

Golden emitter: crates/coach-ibt/tools/emit_session_golden.py. tests/parity.rs also checks 77 cases in tests/yaml-cases/*.cases and prints every quoted or flow value.

Acceptance:
- cargo test -p coach-ibt: 38 passed, 1 ignored.
- cargo clippy -p coach-ibt -- -D warnings: clean (also --all-targets, and in WSL).
- emit_session_golden.py test-data\ibt crates\coach-ibt\tests\golden, then cargo test -p coach-ibt --test parity: 2 passed; regenerating leaves the goldens byte-identical.
- Private corpus (--ignored): not run on real data. The test runs the emitter itself and passed on a stand-in folder of fixture copies.
- Truncated fixture: rejected as Truncated (1856, 7616, 7615); the gate reports Unreadable.
- WSL clippy and tests: pass.

Open issues:
- Parity on real recordings depends on W6's uncommitted ibt_reader.py DriverSetupIsModified fix.
- Windows CPython gives no UTC text outside epoch [-43200, 32536850399]; the port follows Linux per the contract, and the parity test accepts that difference for goldens made on Windows.

### Blockers

No private corpus is available on this machine, so the --ignored parity test was run only on a stand-in folder of fixture copies.

### Contract change requests

1. crate-api.md 3.5 YamlNode::Null: a list item `- Key:` with no value and no block becomes an empty Map, because Python stores {} there (`item_map[key] = {}`). The port does this.
2. crate-api.md 3.5 literal subset: state that integers beyond i64 inside a literal become Str(decimal), like plain scalars. Also state that these are IbtError::Yaml: \N{...} escapes and lone surrogates; a quoted key that evaluates to something other than a string; an unhashable dict key or set element (Python raises TypeError there).
3. crate-api.md 3.4/3.5 conversions: _as_int values beyond i64 come back as None. SessionInfo::driver() returns None when DriverCarIdx is unknown, while summary() keeps Python's None == None match on the first driver without a CarIdx.
4. crate-api.md 3.2/3.7: VarTable::parse_live returns Bounds when num_vars is outside 1..=16384, var_header_offset < 112, or the table runs past the map. A recording whose modified time is in the future gives NotReadyReason::TooRecent { age: 0 }.

## W2 - done, acceptance passed: True

Built crates/coach-live: reader normalisations, session-epoch rule, truth policy, gaps, pace band, fuel, braking cue, all 15 cues, safe-glance hold, frame codec, tile and cue text (.NET-exact F, 0.00, P0 and TimeSpan rules), SessionContext, Sample::read_row. The caution mask is 0xC308: the C# source already holds the backend value; the file's dotnet_legacy 0xC008 is stale.

Vectors come from running the real C# source (IRacingSdkTelemetrySource over a synthetic SDK map, LiveTelemetryService, LiveTelemetryCatalog.Read): crates/coach-live/tests/vectors/engine.json, 35 scenarios, 1,067 steps, 2,870 tile readings. The harness (tools/csharp-oracle) regenerates it byte for byte. Frame goldens, written by an independent Python encoder, are in docs/08-rewrite/contracts/vectors/live-frame/.

Acceptance, run by me:
- cargo test -p coach-live: 68 pass (also in WSL Ubuntu).
- cargo clippy -p coach-live -- -D warnings: clean; also --all-targets --no-deps. cargo fmt --check: clean.
- cargo bench -p coach-live: engine tick median 0.3 us, p99 0.7 us (bound 20 us); frame encode 0.2 us. A counting allocator shows zero allocations after warm-up.
- Engine states equal C# on every step; all 15 cue strings and 33 tile definitions equal C#.

Open: cue bases (CCR 1); pace-range tile can read 1 ms low because channels 33 and 34 are f32 (0.24 % of readings, asserted); LiveMonitorLayouts.cs has no home in crate-api, so it is not ported; the on-rig re-proof waits for the sim pause to lift. Contracts README change log not edited (orchestrator).

### Contract change requests

CCR 1 (live-channels.json cues[].basis; regenerate coach-wire). The C# coach-cue tile shows a basis as secondary text for every cue; the table lists one only for cues 2, 4, 5 and 9. Add: 1 "iRacing race status"; 3 "iRacing repair timer"; 6 "SessionFlags"; 7 "iRacing race status"; 8 "OnPitRoad"; 10 "Clean in-session baseline"; 11 "Clean in-session brake-peak baseline by lap-distance zone"; 12 "Three clean in-session laps are required for a defensible baseline."; 13 "Clean in-session baseline"; 15 "User control". Cue 14 needs none (the tile reads "Waiting"). All 15 templates already equal C# byte for byte. After the change, empty PENDING_BASIS_IDS in tests/engine_vectors.rs (the test fails when the table starts to match).

CCR 2 (live-frame.md, additive). State the presence bit order: channel i is bit i%8, least significant first, of presence byte i/8. State that absent session_time is the quiet NaN 0x7FF8000000000000 on write and any NaN on read. The new vectors fix both.

CCR 3 (crate-api.md 4.7 and live-channels.json). format_tile puts the unit in primary after a space; C# keeps DisplayValue and Unit separate. Display labels are missing from the table: C to "Â°C", F to "Â°F", m/s2 to "m/sÂ²", lap to "laps", the other unit ids as written, and no unit for time tiles. Add them (for example unit_labels) so the TS twin matches. TileText.unit is not needed.

CCR 4 (crate-api.md 4.7 and 4.6, live-frame.md rules). Pin the text rules for the TS twin: F{n} is the exact value, ties to even; fixed2 and 0.## round half away from zero after 15 significant digits; percent0 is en-US P0 (exact value times 100, ties to even, thousands separators, for example 1,567%); ceil is Math.Ceiling; lap time is ticks truncated and the fraction truncated to milliseconds, as m:ss.fff from 60 s and s.fff below, hours dropped; the signed delta uses the zero section when it rounds to zero. coach-live/src/format.rs and engine.json are the reference.

CCR 5 (api.openapi.yaml LiveTile.unit). unit must be a live-channels.json tiles[].units id (C, F, m/s2, lap), not the C# label (Â°C). format_tile falls back to the default unit for an unknown unit, as C# does. D12 layout migration must map Â°C, Â°F, m/sÂ², laps.

Notes needing no review: additive pub LiveState::channel_values; tow_active is Some only when PlayerCarTowTime exists; on_pit_road is Some(false) when missing (C# ?? false); first_of_epoch is set only on the connected state whose call incremented the epoch; vectors/live-frame/ needs its change-log entry in contracts/README.md; the contract's disconnect rule (epoch unchanged) differs from C#, which also increments at the disconnect.

## W3 - done, acceptance passed: True

Built collector/ (lib + bin, about 14k lines with tests, plus README.md): event-driven SDK reader (torn-read guard, map reopen), --replay fixtures and record-sdk, live pipeline (60 fps decimation, epoch marker, stream latch), P5 garage capture with journal and gated .sto hash, uploader (finalization gate, resume, 4 MiB chunks, gate check every 32 KiB), link, overlay/tray/hotkey, install/uninstall, --dry-run.
Acceptance, run by me:
- `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc`: exe 2,968,576 B (under 3 MB), sha256 58e7307e...ef4f.
- `cargo test -p coach-collector`: 108 passed (unit, replay pipeline, uploader, link, garage, end to end, renderer); WSL passes (abort-timing test is Windows-only).
- `cargo clippy -p coach-collector --all-targets -- -D warnings`: clean on Windows and WSL; `cargo fmt --check` clean; `cargo check --workspace` ok.
- Ignored manual tests passed: win_ui_smoke (overlay styles, hides, clean exit), win_install (throwaway registry key).
- In-flight chunk abandoned in under 100 ms; restart resumes at the server offset.
- Replay at 60 Hz, headless dry run: working set 14.7 MB, 0.27 % of one core.
Open issues:
- Nothing ran against the real sim: Win32 map and event, overlay on the rig screen, garage capture on the real SDK. Run `record-sdk 30` first.
- Only 31 KB under the 3 MB budget: coach-ibt's regex is about 1.4 MB of it. I wrote own HTTP and WebSocket clients and a logger instead of ureq, tungstenite and tracing.
- G4 CPU and RSS budgets are unmeasured with the real engine on track.
- The `seen` memo expires after 7 days.

### Contract change requests

1. W1 / coach-ibt: drop `regex` from yaml.rs (INTEGER_PATTERN, FLOAT_PATTERN), literal.rs and gate.rs in favour of hand-written matchers (or an ASCII-only regex build). Regex is reachable from the collector through typed extraction and costs about 1.4 MB, which leaves the collector exe 31 KB under its 3 MB limit. Without it the exe is about 1.6 MB. Alternative: root Cargo.toml `[profile.release] opt-level = "s"` gives 2.75 MB (measured).
2. collector-protocol.md section 3, step 1: the hello list omits `live_table_version`, which CollectorHello requires. The collector sends it (api.openapi.yaml is normative).
3. collector-protocol.md section 8, document what the collector does:
 (a) a snapshot arriving up to 5 s after IsInGarage 1 to 0 belongs to the visit (P5: Apply & Close published the last count after the exit);
 (b) a visit with no UpdateCount change and no name change sends no commit;
 (c) a name change with no UpdateCount change sends one synthesized snapshot at the current count, so the commit has values;
 (d) a named commit also queues its .sto for upload;
 (e) re-entering the garage inside the 5 s window closes the old visit first.
4. collector-protocol.md section 5: define `status.sim`. Used here: not_running = no map, status bit 0 clear, or stale; in_session = live data with a player car and DriverInfo; menus = live data without them.
5. architecture.md section 8: the collector uses no ureq, tungstenite or tracing (src/http.rs, src/ws.rs, src/logging.rs, about 700 lines, size budget). tungstenite is a dev-dependency for the test mock. Cargo.lock lost the unused ureq/rustls entries.

## W4 - done, acceptance passed: True

Built server/ (coach-server lib + bin, 21 modules): migrations (0001 equals db-schema.sql, pre-migrate copy), all 50 non-WebSocket operations of api.openapi.yaml, SSE, collector hub and live relay (viewer counting, 10 min/10 Hz backlog, per-browser decimation, 4001/4002/4008), upload/ingest with the section-4 reconciliation, archiver thread per nas-layout.md, job runner with shared memory budget and worker supervision, setup library (recording, garage, .htm), scheduler/snapshots/backups, SPA hosting, idempotent replay, healthcheck, rebuild-index, --dev. Server calls coach-ibt and coach-views only through traits (adapters.rs); tests use fakes.

Results (all run by me): cargo test -p coach-server on Windows: 6+5+17+15+5+10 pass, 0 fail. Same suite in WSL passes (adds the SIGTERM snapshot test). cargo clippy -p coach-server --no-deps --all-targets -D warnings and cargo fmt --check clean. wsl -d Ubuntu -- bash tools/wsl/check.sh server: "WSL check passed (server)" (clippy -D warnings on the 5 packages, all tests). Contract test calls every operation and validates each response against api.openapi.yaml, then asserts none is missing. Crash tests cut every upload and archive point; all 7 reconciliation rows, ENOSPC on a chunk and in the commit (503, offset kept), concurrent chunks, duplicate .sto, garage resend, outbox replay, analysis-membership, stub worker (timeout, crash, isError, cancel, orphan cleanup) and the real worker on synthetic-race.ibt all pass. Idle RSS 20.8 MB (WSL debug build).

Open issues: OOM kill needs a cgroup oom_kill increase, so only the classifier is unit-tested; archiver stall state is untested; legacy tuning_experiments are parked as legacy_items until W6's import tool exists; the .htm observations rely on parsed_html.fields[*].raw.

### Contract change requests

1. db-schema.sql: collector-protocol section 8 and SetupFileEntry.linked_setup_id name setup_files.linked_setup_id, which the schema lacks. The server derives links from setup_observations (is_modified = 0 with the sto hash, or an unmodified recording naming the file and car folder, or an htm_export of the blob). Add the column or reword section 8.
2. api.openapi.yaml: ServerStatus gains an optional boolean collector_newer_than_server (collector-protocol section 6 and live-frame.md say it is reported in status). The server already emits it.
3. api.openapi.yaml: the 202 for POST /setups/compare waits 10 s by default; state it is configurable. The tuning context (GET .../tuning) carries no ETag because it merges request-time fields; note it.
4. Root Cargo.toml [workspace.dependencies]: add pins for tokio-stream 0.1, uuid 1 (v4), libc 0.2, tempfile 3, tower 0.5 (util), http-body-util 0.1, serde_yaml_ng 0.10; server/Cargo.toml uses them directly and enables tokio feature process. Also promote jsonschema from dev-dependency to a runtime dependency of coach-server (request validation).
5. worker-interface.md section 4: the discover result key is group_id (the document says group_key); and document that the server derives the analyzer bundle digest (SHA-256 of the scripts directory's .py files) and profile version (ANALYSIS_PROFILE_VERSION text) for the analyze idempotency key, since the worker does not report them. W6 could expose both through initialize.
6. worker-interface.md: add USERPROFILE to the Windows dev environment table; the Python worker fails with 'Could not determine home directory' without it (HOME alone only works on POSIX).
7. W6/W9: the D12 import path for tuning_experiments is not available; ImportBundle.tuning_experiments are stored as legacy_items kind tuning_experiment and counted in the import summary as awaiting worker import.

## W5 - done, acceptance passed: True

Built crates/coach-views. project() streams analysis.json (only needed subtrees; trace points go straight into columns; bare NaN/Infinity become null and are counted) and writes race, runs, investigation (absent when the backend has none), trace catalog, traces.ccol, track (normalized_local_vector copied, no reprojection) and tuning (RuntimeMapper identity/map/eligibility port). Includes the exclusion-reason rule, select_traces, an x-consumed-fields shrink ladder (unread subtrees first, then unread fields, else TooLarge), and atomic temp-dir output.

Acceptance, all run by me:
- cargo test -p coach-views: 29 unit, 1 api_shape, 2 consumed-fields drift, 11 fixtures (analysis-view-* valid, unknown-field and defective cases; race-investigation.json valid, unknown-field, wrong-version), 29 synthetic. 1 ignored (corpus). All pass.
- cargo clippy -p coach-views --all-targets -D warnings and cargo fmt --check: clean.
- Same clippy and tests in WSL Ubuntu: pass.
- Every view validates against the wire schema; every CCOL channel equals the backend values and missingness bit for bit; 500-lap synthetic stays within all bounds (race, runs, catalog, traces default 5.1 MB).
- COACH_PRIVATE_CORPUS (release): real 42 MB doc, 0.15 s, 66 MB peak; 83 MB doc, 0.31 s, 122 MB peak, views validate and decode exactly.

Open issues: real recordings have 76 channels against the 64-signal cap; the last 12 are listed in omitted_signals, not served. Lap.caution, incident_count and TraceLap.fastest are not filled (no backend source). serde_json float_roundtrip is enabled and unifies workspace-wide. Null/blank fields fall through to the fallback in tuning identity (C# kept an empty string).

### Contract change requests

1. api.openapi.yaml TraceCatalog.signals maxItems 64 -> 128. A real 82-lap recording yields 15 fixed + 61 additional signals. W5 caps at 64 (MAX_SIGNALS in traces.rs, one constant) and adds an optional omitted_signals array; add that property to the schema or drop it.
2. crate-api.md section 5: state the input shape. The worker's analysis.json is flat with schema_version 2 and analysis_id; the nested analyze-result form {analysis_id, analysis_view} needs analysis_view.schema_version 1. ProjectionSummary.schema_version and every ViewEnvelope.schema_version are always the envelope version 1. race_investigation.schema_version must be 2.
3. ViewEnvelope.profile_version is a required string but analysis_profile_version may be null (the valid-empty-sections fixture): W5 emits "". Make it nullable or document "".
4. crate-api.md shrink rule: after emptying unread x-source subtrees, W5 prunes fields outside the consumed lists (permitted by the info.description). Mention it.
5. Lap and TraceLap optional fields caution, incident_count and fastest have no stated backend source. W5 fills run_number (runs[].lap_numbers), run_age_laps (race_investigation.laps[].green_exposure), pit_in and pit_out (pit_entry, pit_exit). Drop the others or name a source.
6. W4: X-Coach-Trace-Laps is the length of the laps array in the CCOL header JSON; select_traces returns only bytes.

## W6 - done, acceptance passed: True

W6's acceptance commands all pass, and the Python worker now gives the same results on Windows and Linux.

Built:
- The six worker-interface.md section 5 changes:
  - `IRACING_COACH_RAW_ARCHIVE=external` skips the raw copy.
  - The Garage61 token can come from a file named by `IRACING_COACH_GARAGE61_TOKEN_FILE`. No Windows store is needed, and a file readable by others is refused on Linux.
  - `ibt_reader` now reads `DriverSetupIsModified`, with a regression test.
  - Case-only directory ties sort the same way on every file system.
  - Tests that read `companion-app` were removed; Windows-only path tests skip on Linux and gained Linux equivalents.
  - The reviewed analyzer digest was updated. Analysis output is unchanged, but every `analysis_id` moves because ibt_reader.py and setup_catalog.py changed.
- Defect found by the parity run: on Linux an installed PyYAML read `SteeringRatio: 12:1` as 721. Setup fingerprints and trace catalogs differed. Session info now always uses the built-in parser, with a test.
- Stable-selector and two-file reconnect tests, plus end-to-end tests of `mcp_server.py` run as the server runs it.
- New MCP tool `import_legacy_tuning_experiments` for D12; contracts regenerated.
- Parity harness moved to `tools/parity/`: `--native-stages`, `--raw`, `--unpinned-identity`, `capture-worker` (synthetic, reconnect, restaged, local-file), and `--cross-platform`. A README records the result.

Acceptance:
- `verify-repository.ps1`: OK; 1377 tests, 0 failed, 7 skipped.
- `export_contracts.py --check`: ok.
- WSL python3.12 suite: 1377 run, 0 failures, 0 errors, 26 skipped.
- Parity, Windows 3.12.14 against WSL 3.12.3: the synthetic race and the four worker cases, and the private New Hampshire race (582b11e2), Iowa race, practice and test session, both in process and through the worker. Every parsed and raw comparison is identical, with equal `analysis_id`s.
- The only Windows/Linux differences are one CRLF in the analysis-cache file and path separators after the root prefixes.

Open:
- `export_contracts.py` still reads `DurableArchive.cs`, so it breaks when `companion-app` is deleted in wave 4.
- `tools/dev` tests read `companion-app`.
- The evidence registry was already missing 646 backend tests.

### Contract change requests

1. worker-interface.md section 5 item 1: with `IRACING_COACH_RAW_ARCHIVE=external`, `source.raw_source_policy` stays an object: `{mode: "external", durably_copied: false, archive: {status: "external", items: []}, raw_reanalysis_durable: null}`. The analyze result's `raw_archive.status` is "external". Any value other than unset or "external" fails the job.
2. Sections 3 and 4: the discovery field is `group_id`, not `group_key`. A group keyed by path is the one with `time_source == "mtime"`; the server stores it as `local-file:<sha256>` and selects it by its path in the job root.
3. Section 8 cross-platform rule: besides root prefixes and line endings, the path separators that follow `<archive_root>` or `<iracing_root>` are normalised to `/`. Raw mode compares SQLite only as parsed row dumps.
4. Section 2: the worker needs `HOME` set; without it a tool fails with "Could not determine home directory". Session YAML now never uses PyYAML, so the image rule "no PyYAML" is enforced in code.
5. Python MCP contracts (W6-owned, regenerated): new write tool `import_legacy_tuning_experiments` (tool count 27) for W9's D12 step 2. Arguments: `legacy_archive_root` (inside the job root), `analysis_map` ([{legacy_analysis_id, analysis_id, analysis_path}]), optional `dry_run`.
6. `garage61-auth-status-v1.credential_storage` allows "token-file" as well as "windows-user-dpapi".
7. `compatibility.json` environment lists `IRACING_COACH_RAW_ARCHIVE`, `IRACING_COACH_GARAGE61_TOKEN_FILE` and `IRACING_COACH_NATIVE_STAGES`.
8. Scaffold `tools/wsl/check.sh`: the Python suite is now clean on Linux. Make `all` the default scope and drop the "17 tests fail" comment.
9. W1 note: `ibt_reader._parse_session_info` always uses the subset parser, so `emit_session_golden` output no longer depends on whether PyYAML is installed. `tools/generate_fixtures._build_tiny_ibt` takes keywords but writes byte-identical `synthetic-race.ibt` and `truncated-race.ibt`.

## W7 - done, acceptance passed: True

Built the W7 web app: hash-routed shell, typed API client, SSE client, persisted pending-write outbox, Drivers landing, Sessions library, Setups (library, detail, compare, Find a baseline), Progressive Tuning, Live page, and the web/mock/ fixture server. Mounts W8's investigation workspace in a slot (session to analysis, route, last-investigation, trace layouts, one session picker). web/src/README.md documents the shared modules for W8.

Acceptance, all run by me:
- `pnpm -C web typecheck`: clean.
- `pnpm -C web test`: 22 files, 170 tests pass (includes W8's tests).
- `pnpm -C web build`: ok; initial JS is about 19 KB gzip (limit 150). The investigation chunk is lazy.
- `pnpm -C web e2e --project=app`: 105 passed, 1 skipped (a keyboard-move test that skips on phone). Runs on 1280x720, 1024x768 and 390x844 against web/mock/.
- Phone checks: 44 px targets and no horizontal scroll. Reduced motion is honoured.
- Wire and theme drift checks: clean.

Open issues:
- Tuning map `verified` is my reading of C#: the projected map is verified, or every submitted turn was verified by the driver. I submit only turns with feedback; confirm the worker accepts that.
- Tuning history (`GET tuning/history`) is not shown.
- Playwright config now uses port 4373 and a webServer that rebuilds into test-results/app-dist.

Contract change requests: none are blocking. Suggestions:
1. Specify `TuningDraft.body` and `TurnAnnotations.body` (I use goal, general_note, representative_run_ids, open_target_analysis_id, feedback; and `{corners: [...]}`). D12 migration must map legacy drafts to it.
2. State whether `PUT prefs` replaces or merges; I send the full merged body.
3. Add an optional `analysis_id` per observation on `SetupDetail`; Tune-this-setup now makes two requests.
4. `BaselineRequest` has no race/qualifying purpose, so the old purpose choice is dropped.

### Contract change requests

none

## W8 - done, acceptance passed: True

Built the investigation workspace in web/src/investigation/ and the G2 harness in web/perf/. Race, Runs, Sections and Traces sit on one viewport-bound workspace. Ported as TS: telemetry cursor, track map, race-inspection (object input), trace layout editor and rules, context splitter. Chart geometry is pure functions under the catalog budgets. Also built: CCOL decoder and encoder, cache (3 analyses, 96 MB), loader, README files. Nothing committed.

Acceptance, run by me:
- `pnpm -C web test -- investigation`: 9 files, 76 tests pass. Full `pnpm -C web test`: 22 files pass. Typecheck clean.
- `pnpm -C web perf:g2` (6x throttle, median of 3, 10 s sweeps, Chromium 153): PASS.
  - Phone: first interactive 1867 ms; cursor 59.3 fps driven, 59.5 fps real mouse.
  - Tablet: 1785 ms; 60.1 and 59.5 fps.
  - Requests during the sweeps: 0.
  - Dev PC load was 36-49 % before each run.
  - Also passes with all 82 laps selected (5 s sweeps): 59.6-60.2 fps.
- `node perf/views.mjs` (my own check, not a gate): all checks pass at desktop, tablet and phone. These cover the Playwright DOM checks, 44 px phone targets, no horizontal scroll and 1280x720 height fit.

First pass was about 41 fps. I reworked the cursor loop: value cards on the canvas, compositor-only map marker, no pointer events on the trace SVG, 100 ms readout throttle, CSS containment.

Open issues:
- G2 uses a synthetic fixture. No real 82-lap analysis exists in the repo.
- Throttling is emulation, not a phone.
- Not ported: the C# calculated tire-wear row, the sector-square columns in the lap rail, and the layout-library UI (new, copy, delete). W7 persists layouts through `onTraceLayoutChange`.
- A 413 on the traces endpoint is shown as an error, with no lap chunking.

Contract change requests:
1. CCOL: state that the data section starts at 8-byte alignment of the file offset (12 + H), as implemented.
2. TraceCatalog: state bin x positions (a `lap_pct` channel, or uniform i/(bins-1)). Fix the auxiliary source keys: `session_time_s`, `latitude`, `longitude`. Note that steering ids carry unit `rad`, which the browser shows in degrees.
3. Catalog: add `tire-wear` and `tire-stress` channels, or confirm they are out of v1.
4. W5/W6: supply a sanitized current-profile CCOL and catalog fixture under test-data/ for G2.
5. api: document the 413 response on traces (lap-subset requests) for analyses near 16 MiB.

### Contract change requests

none

## W9 - done, acceptance passed: True

I built the deploy files, released the skeleton to the mini PC, and it is healthy. The NAS is readable and writable from inside the container. The status endpoint (`/api/v1/status`) returns 404 because W4 has not built it yet, so a probe I run inside the container stands in for that check.

Work is on branch `w9/deploy` in `C:/Users/joshu/source/worktrees/iracing-coach-w9`, made from local main. Two commits: `9fb5223` (deploy files, released) and `899192a` (records and scripts). Nothing was pushed.

**What I built:**
- `.dockerignore` and `deploy/mini-pc/` (`Dockerfile`, `docker-compose.yml`, `app.env`, `nas_probe.py`), with all base images pinned by digest.
- `tools/migrate/inventory_legacy.py`.
- Two read-only sim-side scripts in `tools/rig/`: `Export-LegacyCoachData.ps1` and `Get-SimRecordingInventory.ps1`.
- Two acceptance records in `docs/08-rewrite/acceptance/`: `first-release.md` and `migration-inventory.md`.

**Acceptance commands:**
- `release.sh iracing-coach .`: exit 0. The cold image build took 226 s on the N100; the stack was healthy within 5 s.
- `g-house-stack status iracing-coach`: healthy.
- `curl :8210/health`: 200.
- `curl :8210/api/v1/status`: 404 (not built yet by W4).
- `docker exec iracing-coach-app-1 python3.12 /app/deploy/nas_probe.py`: state ok. It created `iracing-coach/.probe` over NFS and wrote, flushed and read back a 4 MiB file with an equal hash. The NAS maps the container's root user to uid 1024, and writes are accepted.
- The Python 3.12.15 worker inside the container starts and lists 26 tools.
- `verify-repository.ps1`: 1,347 tests pass.
- Linux `cargo build --locked -p coach-server` in WSL passes.

**Open issues:**
- `app.env` uses `RECOVERY_PATHS=.` because the release tool refuses recovery paths that don't exist yet (`db`, `snapshots`). It must switch once a released server has created `db/coach.sqlite3`.
- The healthcheck uses Python until W4 adds a `coach-server healthcheck` subcommand.
- Tailnet access is unverified: the dev PC was not on the tailnet at the time.
- The stack still cannot start while the NAS is down (risk R3, owner question Q1).
- Sim steps are blocked: the export and recording inventory runs, G3 and G4.
- The G1 script is still to write (wave 2).

### Contract change requests

1. W4 (server): read its paths from these image environment variables, or name the ones it wants instead: COACH_BIND, COACH_DATA_DIR=/data, COACH_NAS_DIR=/nas/apps/iracing-coach, COACH_WEB_DIR=/app/web, COACH_PYTHON=/usr/local/bin/python3.12, COACH_WORKER_SCRIPT=/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py, COACH_CONTRACTS_DIR=/app/contracts. Do not `include_str!` anything from `docs/` or `test-data/`: they are outside the build context.
2. W4: add a `coach-server healthcheck` subcommand that exits 0 when `/health` answers 200. Have the server create `/data/db` and `/data/snapshots` at first start. When both have landed, W9 switches the Compose healthcheck and `app.env` to `RECOVERY_PATHS=db snapshots` and `RECOVERY_SQLITE=db/coach.sqlite3`.
3. `architecture.md` section 10 (architect): record that the NAS maps the container's root user to uid 1024 (gid 100, mode 0777), and the first-release values of `RECOVERY_PATHS` and the healthcheck, with the reasons given in `acceptance/first-release.md`.

## Codex ingest review

Codex (gpt-6.1-sol, high, workspace-write, app-resources codex.exe) ran but could not read any file. Every shell and node read failed with: "Failed to create unified exec process: helper_unknown_error: apply deny-read ACLs" (windows sandbox failed: helper_unknown_error: apply deny-read ACLs). Codex returned no findings and said the review was blocked and not a clean review. No other model was substituted and no review file was written. git status --porcelain before and after was identical (164 lines).
