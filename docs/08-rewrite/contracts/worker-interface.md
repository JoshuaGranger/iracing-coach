# Worker interface (v1)

How `coach-server` runs the Python backend, and how a Rust stage replaces a Python stage later. Source facts: `spikes/python-portability.md` sections 1-2 and 5, `prove/P1`.

## 1. Command

```
nice -n 10 python3.12 -X utf8 -u /app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py
```

Spawned with `tokio::process`, its own process group, `oom_score_adj` set to 1000 (in `pre_exec`), stdin/stdout piped, stderr captured to a 1 MiB ring and logged on failure. One process per job. Never a shell.

## 2. Environment (complete; nothing inherited)

| Variable | Value |
|---|---|
| `IRACING_COACH_IRACING_ROOT` | `/data/py/<customer_id>/jobs/<job_id>` |
| `IRACING_COACH_DATA` | `/data/py/<customer_id>/archive` |
| `IRACING_COACH_RAW_ARCHIVE` | `external` (new switch, section 5) |
| `IRACING_COACH_GARAGE61_TOKEN_FILE` | set only for Garage61 jobs (later phase); file on `/run/coach` tmpfs, mode 0600 |
| `IRACING_COACH_NATIVE_STAGES` | empty in v1 (section 6) |
| `PYTHONUTF8` | `1` |
| `PYTHONDONTWRITEBYTECODE` | `1` (image is precompiled) |
| `LANG` | `C.UTF-8` |
| `HOME`, `TMPDIR` | `/data/py/<customer_id>/tmp` (required: without `HOME` a tool fails with "Could not determine home directory") |
| `PATH` | `/usr/local/bin:/usr/bin:/bin` |

Windows development (`coach-server --dev` on the dev PC) adds `USERPROFILE` set to the same directory as `HOME` (Python's home lookup reads it there, not `HOME`), and passes through only `SystemRoot`, `SYSTEMROOT`, `PATH`, `PATHEXT`, `TEMP` and `TMP`, which the interpreter needs to start.

`IRACING_COACH_INSTALL_ROOT` is unset. The container network stays enabled for the server; v1 runs no networked tool.

## 3. Roots the server prepares

```
/data/py/<customer_id>/jobs/<job_id>/telemetry/<original name>.ibt     hard links to /data/spool/ready/<sha256>: the job's recording(s)
/data/py/<customer_id>/jobs/<job_id>/setups/<car_folder>/<rel path>    hard links to received .sto/.htm blobs (latest blob per path)
/data/py/<customer_id>/archive/                                        Python archive (reports, history.sqlite3, tuning, trials)
```

The job root is created before the job and deleted after it (links only). A discovery job's root holds one recording. An analysis job's root holds every recording of the session's group (a reconnected race spans several files); members whose spool copy is gone are restaged from the NAS first. Spool files keep the recording's original mtime (set at commit), so `_assert_finalized_ibt`'s 2 s quiet rule passes. Path bounds (`_bounded_path`) hold because every path lies under these roots. Python's `discover_sessions` groups by `SubSessionID + sim_session_num`; cross-file test-day tagging is not used in v1 (Test day is parked).

**Stable selectors.** Job roots differ per job, so a group key that embeds the recording's path is not stable: `ibt_reader._fallback_group_key` uses `metadata["path"]` when `time_source == "mtime"` (recordings with no server ids and no usable session start time). Such a group always has one member; the server recognises it by the discovery group's `time_source == "mtime"`. The server stores it as `local-file:<sha256>` and runs `analyze_iracing_race` with `selector` = that recording's path in the analysis job root. Every other group is selected by the worker's `group_id`, which holds no path (the server stores it as `sessions.group_key`). Each recording belongs to one group only (the final `SessionNum`, `db-schema.sql` sessions comment). W6 adds regression tests for a recording with no server ids and no session start time, and for a two-file reconnect.

## 4. Protocol

Three lines on stdin, then close stdin:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"coach-server","version":"<v>"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"<tool>","arguments":{...}}}
```

Read stdout lines until the response with `id: 2`. Result: `result.content[0].text` is compact JSON. `result.isError: true` carries `{error, message, tool}`: job `failed` with `error_code = tool_error`. The process exits 0 on stdin EOF; any other exit before a response is `worker_crash` (exit by signal 9 with the cgroup's `memory.events` `oom_kill` incremented is `worker_oom`).

### Tools used in v1

| Lane | Tool | Arguments (paths are under the roots above) | Server use of the result |
|---|---|---|---|
| light | `discover_iracing_sessions` | `root` (the job root, holding one recording), `races_only: false`, `limit: 50` | per session group (result `sessions[]`, each keyed by `group_id`): find or create the driver's `sessions` row by that key (`local-file:<sha256>` for a `time_source == "mtime"` group), add the recording to `session_recordings` |
| analysis | `analyze_iracing_race` | `selector: <group_id>` (or the recording's job-root path for `local-file`), `iracing_root`, `archive_root`, `target_hz: null` | `analysis_path`, identity, `analysis_id`; then projection |
| light | `catalog_iracing_setups` | `root`, `archive_root`, `car`, `maximum_entries: 1000` | values of `.htm` exports (`htm_export` observations) |
| light | `compare_iracing_setups` | `left`, `right`, `scheme: "recording"` | compare rows, passed through |
| light | `build_open_setup_package` | `iracing_root`, `archive_root`, `season`, `car`, `track`, optional `analysis_path` | Find a baseline result (validated against `contracts/setup-package-v1.schema.json`) |
| light | `recommend_structured_open_setup_tuning` | per `contracts/mcp-tools.v1.json`; `ai_response` never sent in v1 | Progressive Tuning result (validated against `tuning-recommendation-v1`) |
| light | `record_open_setup_feedback` | `experiment_id`, `outcome`, `notes`, optional `result_analysis_path` | outcome stored in the worker's `tuning_experiments` |
| light | `iracing_setup_history` | `analysis_path` or `package_id`, `archive_root`, `limit: 100` | tuning history view |
| light | `import_legacy_tuning_experiments` (write tool, D12 only) | `legacy_archive_root` (the import job root's `legacy/`, holding the bundle's `legacy_archive` files), `analysis_map` (`[{legacy_analysis_id, analysis_id, analysis_path}]`, null ids and paths for unresolved analyses), `archive_root`, optional `dry_run` | result `legacy-tuning-import-v1`: `experiment_count`, `resolved_count`, `unresolved_count` and per-experiment `experiments[]` (no skipped count: every row is recorded, unresolved references nulled and listed). The import summary reports them as `tuning_experiments_imported`, `tuning_experiments_resolved`, `tuning_experiments_unresolved`, beside the server's `legacy_analyses`, `legacy_analyses_resolved`, `legacy_analyses_unresolved`; bumps `user_data_generation` |

**Analyze idempotency key.** The worker does not report its analyzer bundle digest or profile, so the server derives both from the scripts directory at start: the digest is the first 16 hex characters of SHA-256 over the directory's `*.py` files in path order, each as file name, `0x00`, contents, `0x00`; the profile is the `ANALYSIS_PROFILE_VERSION = "..."` text in `analysis_engine.py`. Any Python change therefore changes the key. The worker's own `analysis_id` stays the identity of a result.

## 5. Python changes required for v1 (owned by W6)

Status (wave 1): all six landed in the wave-1 commit (`dd0ce49`), with regression tests.

1. `IRACING_COACH_RAW_ARCHIVE=external`: skip `archive_raw_telemetry`. `source.raw_source_policy` stays an object: `{mode: "external", durably_copied: false, archive: {status: "external", items: []}, note, raw_reanalysis_durable: null}`, and the analyze result's `raw_archive.status` is `"external"`. Any value other than unset or `external` fails the job. The oracle uses the same switch in every parity run.
2. `secure_store`: honour `IRACING_COACH_GARAGE61_TOKEN_FILE` before the Windows check (about 20 lines).
3. `ibt_reader.py`: read `DriverSetupIsModified` (P5); keep `DriverSetupModified` only as the fixture fallback. Session info is always parsed by the built-in subset parser, never PyYAML (an installed PyYAML read `SteeringRatio: 12:1` as 721), so the image rule "no PyYAML" is also enforced in code.
4. Deterministic tie-break in `key=str.lower` sorts: sort by `(key, name)` (`ibt_reader.py:1797`, `setup_catalog.py:1391`).
5. Tests: Windows-literal path tests skip on POSIX; tests that read `companion-app/` C# sources are removed or rewritten before `companion-app/` is deleted.
6. Each change that alters output bumps the analyzer bundle (existing rules in `test_analysis_pipeline_identity.py`).

## 6. Timeouts, cancellation, memory

| Item | Rule |
|---|---|
| Timeouts | `analyze_iracing_race` 10 min; `discover`, `catalog` 2 min; other light tools 3 min |
| Cancel | `POST /api/v1/jobs/{id}/cancel` or timeout: SIGTERM to the process group, SIGKILL after 5 s; job `cancelled` or `failed/timeout` |
| Orphans | after a kill, delete the killed process's `*.<pid>.tmp` at once; delete `.write-*` and `.copy-*` under that driver's archive root only when no worker process for that driver is running (names are random, `storage.py:668,710`); SQLite WAL recovers itself |
| Admission (analysis) | before `analyze`: estimated peak = 11.5 x total bytes of the job's recordings + 0.15 GiB for one recording; for a group of two or more recordings 1.5 x that (`_load_analysis_telemetry` keeps every decoded table while it builds merged columns, sample references and provenance) until G1 measures a multi-file group. If the estimate > 3.0 GiB the job is `deferred` with `exceeds_memory_budget` (P1: 2.273 GiB at 203.2 MB, one file) |
| Shared memory budget | every worker job declares an estimate before it starts: analysis as above; light jobs that load `analysis.json` (tuning recommend, which loads the driving and the target analysis, `tuning_workflow.py:1114-1116`; baseline with `analysis_path`; history) 10 x the sum of the loaded files' bytes; other light jobs 0.25 GiB; projection 0.6 GiB. A job starts only when its estimate plus the estimates of running jobs plus 0.3 GiB for the server is <= 3.7 GiB; otherwise it waits (interactive light jobs wait at the head of the queue). G1 measures the whole-container peak with an analysis, a two-analysis tuning job and an upload overlapping |
| At the limit | container `mem_limit` 4 GiB, no swap; the worker has `oom_score_adj` 1000 so the kernel kills it, not the server; result `worker_oom`, not retried automatically; the estimate factor is raised to the observed ratio |
| Concurrency | one analysis-lane and one light-lane process at most, further limited by the shared budget; interactive light jobs (priority 100) run before ingest discovery |
| Restart | jobs `running` at server start become `interrupted` and are requeued once |

## 7. Rust stage substitution (port phases, after v1)

- A PyO3 extension module `coach_native` (crate `crates/coach-analysis`, built into the image) exposes one function per ported stage with the Python stage's exact inputs and outputs.
- `IRACING_COACH_NATIVE_STAGES` is a comma list (`decode`, `kernels`, `json_writer`). The Python call site checks the list once per job and calls the native function instead of its own; with the variable empty, behaviour is unchanged. No other Python code changes.
- Boundary A (`decode`): `_load_analysis_telemetry` receives a native column store; `TelemetryTable` reads columns through the buffer protocol instead of copying lists. Phase 1 entry deliverable, before any decode code: a written column interface (per SDK type: element type, array-valued variables, how missing values appear, ownership and lifetime, provenance and `sample_indices`) prototyped against the unchanged downstream stages, and an inventory of the Python sites that still materialize lists (normalization, unit conversion, multi-file merge) with the changes each needs. The memory benefit of phase 1 is measured on the N100, never assumed; the admission formula changes only on that measurement.
- Boundary B (`kernels`): each kernel returns the same Python objects (dict insertion order included) the Python stage returns.
- Boundary D (`json_writer`): `storage._json_text` and `workflow._write_json_cache` call the native writer.

## 8. Parity check

`python tools/parity/analysis_parity.py capture --native-stages <list> <ibt> <out>` then `compare <python-out> <native-out>` (existing normalisation: timestamps, elapsed times, analyzer digest pinned). Pass = every file byte-identical.

The existing harness parses persisted JSON and re-serializes it before comparing, so it cannot see the writer's own bytes. W6 adds, before phase 3 and used by every phase:
- **Raw mode** (`--raw`): compares the original persisted bytes after textual substitution of an explicit allowlist only (ISO instants in `*_at` values, the report-directory timestamp slug, `*_ms` / `elapsed_seconds` values, the timestamp-derived `analysis_sha256`, the job-root and archive-root prefixes as `<iracing_root>` / `<archive_root>`). Phase 3 passes only in raw mode; the parsed comparison stays as a diagnostic.
- **Production identity:** one capture pair per phase with the analyzer digest unpinned, run in the same image; `analysis_id` and run ids must be equal between Python and native.
- **Worker-interface captures:** captures driven through `mcp_server.py` with the section 3 roots and selectors (not one explicit file) for a two-file reconnect group with overlapping samples, a restaged recording, and a `local-file` fallback recording; Linux container against Windows oracle with only the root prefixes, line endings, and the path separators that follow `<archive_root>` or `<iracing_root>` (normalised to `/`) normalized. Raw mode compares SQLite files only as parsed row dumps. Corpus: `test-data/ibt/synthetic-race.ibt` (committed) and the private corpus named by `COACH_PRIVATE_CORPUS` (at least the 203 MB New Hampshire race, the 146 MB Iowa race, one practice, one test session; never committed). Phase 1 also requires `tools/parity/emit_ibt_golden.py` matrix-hash equality. An unexplained difference fails the stage (D2); no tolerance.
