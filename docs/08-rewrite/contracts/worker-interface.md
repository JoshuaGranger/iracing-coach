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
| `IRACING_COACH_NATIVE_STAGES` | empty in v1 (section 7) |
| `IRACING_COACH_NATIVE_BIN` | `/usr/local/bin/coach-analysis` once the image carries it (section 7); read only when `IRACING_COACH_NATIVE_STAGES` is non-empty; unset means `coach-analysis` on `PATH` |
| `PYTHONUTF8` | `1` |
| `PYTHONDONTWRITEBYTECODE` | `1` (image is precompiled) |
| `LANG` | `C.UTF-8` |
| `HOME`, `TMPDIR` | `/data/py/<customer_id>/tmp` (required: without `HOME` a tool fails with "Could not determine home directory") |
| `PATH` | `/usr/local/bin:/usr/bin:/bin` |

Windows development (`coach-server --dev` on the dev PC) adds `USERPROFILE` set to the same directory as `HOME` (Python's home lookup reads it there, not `HOME`), and passes through only `SystemRoot`, `SYSTEMROOT`, `PATH`, `PATHEXT`, `TEMP` and `TMP`, which the interpreter needs to start.

Windows path length (`--dev` only; Linux has no such limit). The worker opens each recording at `<COACH_DATA_DIR>\py\<customer_id>\jobs\<job_id>\telemetry\<original name>` (section 3) with ordinary Win32 calls, so on a Windows without `LongPathsEnabled` that full path must stay under 260 characters (`MAX_PATH`). A longer one is not an analysis failure in the worker: discovery reports it as a per-file record (`kind: "error"`, `error_type: "FileNotFoundError"`) and returns no group for the recording, so the server fails the discovery job `no_session_found` and marks the recording `ingest_state = failed`. The segments after the data root add about 35 characters plus the file name, and iRacing's names (car, track, timestamp) run to about 90, so keep `COACH_DATA_DIR` well under 130 characters (for example `%TEMP%\e2e-w3\data`). The worker's archive root (`IRACING_COACH_DATA`) is not bound by this: archive and analysis-cache writes use extended-length (`\\?\`) paths (wave 3, `workflow._write_json_cache`, `iracing-coach/tests/test_worker_interface.py` `DeepArchiveRootTests`).

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
| Orphans | after a kill, delete the killed process's `*.<pid>.tmp` at once; delete `.write-*` and `.copy-*` under that driver's archive root only when no worker process for that driver is running (names are random, `storage.py:668,710`), and `coach-decode-*` directories (native decode stores, section 7) under that driver's `TMPDIR` on the same condition; SQLite WAL recovers itself |
| Admission (analysis) | before `analyze`: estimated peak = 11.5 x total bytes of the job's recordings + 0.15 GiB for one recording; for a group of two or more recordings 1.5 x that (`_load_analysis_telemetry` keeps every decoded table while it builds merged columns, sample references and provenance) until G1 measures a multi-file group. If the estimate > 3.0 GiB the job is `deferred` with `exceeds_memory_budget` (P1: 2.273 GiB at 203.2 MB, one file) |
| Shared memory budget | every worker job declares an estimate before it starts: analysis as above; light jobs that load `analysis.json` (tuning recommend, which loads the driving and the target analysis, `tuning_workflow.py:1114-1116`; baseline with `analysis_path`; history) 10 x the sum of the loaded files' bytes; other light jobs 0.25 GiB; projection 0.6 GiB. A job starts only when its estimate plus the estimates of running jobs plus 0.3 GiB for the server is <= 3.7 GiB; otherwise it waits (interactive light jobs wait at the head of the queue). G1 measures the whole-container peak with an analysis, a two-analysis tuning job and an upload overlapping |
| At the limit | container `mem_limit` 4 GiB, no swap; the worker has `oom_score_adj` 1000 so the kernel kills it, not the server; result `worker_oom`, not retried automatically; the estimate factor is raised to the observed ratio |
| Concurrency | one analysis-lane and one light-lane process at most, further limited by the shared budget; interactive light jobs (priority 100) run before ingest discovery |
| Restart | jobs `running` at server start become `interrupted` and are requeued once |

## 7. Rust stage substitution (port phases, after v1)

- `IRACING_COACH_NATIVE_STAGES` is a comma list (`decode`, `kernels`, `json_writer`). The Python call site checks the list once per job and calls the native stage instead of its own; with the variable empty, behaviour is unchanged. A listed stage that the build does not provide, or an unknown name, fails the job (`native_stages.NativeStageError`): a silent Python fallback would let a parity run compare Python with Python. No other Python code changes.
- The native stages live in crate `crates/coach-analysis`. Phase 1 (`decode`) runs as the executable `coach-analysis` (`IRACING_COACH_NATIVE_BIN`, else `PATH`), started by the worker per recording, and hands its result over in a coach column store file (below). In-process substitution through a PyO3 module (`coach_native`) is deferred to phase 2, where boundary B must return Python objects in process; the column store is the input phase-2 kernels read (a Rust kernel maps the same file instead of converting Python lists). Phase 1 chose a process and a file because the decoder's memory is released before Python builds its lists, the executable does not link against one Python ABI, and coach-server can write the same store at ingest later. Measured cost of the hand-over: `docs/08-rewrite/port/phase1-decode.md`.

### Boundary A: `decode` (phase 1)

Call site: `workflow._load_analysis_telemetry` calls `native_stages.load_telemetry(path, channels, target_hz)` instead of `ibt_reader.load_telemetry`. It returns the same mapping, key for key and value for value (`metadata`, `session_info`, `available_variables`, `variables`, `samples`, `sample_indices`, `native_tick_rate_hz`, `sample_rate_hz`, `source_record_count`, `sample_count`, `channel_selection`). Header, session info, variable catalogue and summary still come from `ibt_reader.scan_ibt` in Python (they are identity data, about 13 ms on the 203 MB race); the native stage decodes the samples. The multi-file merge, `sample_provenance`, `TelemetryTable` and everything after it are unchanged Python.

Request: `coach-analysis decode` reads one JSON object on standard input and exits 0 after writing the store, printing one JSON summary line; on failure it exits 1 with the message on standard error, which the worker raises as `NativeStageError`.

```json
{"version": 1, "source": "<path scan_ibt resolved>", "out": "<store path>",
 "channels": ["Speed", "..."] | null, "target_hz": "<repr(float(target_hz))>" | null}
```

`channels` null decodes every variable; otherwise the names are selected in caller order without repeats and an absent or empty name fails with the reader's `IbtChannelError` text. `target_hz` travels as Python `repr` text so the value parses exactly. The store goes to a `coach-decode-*` temporary directory under `TMPDIR` that the worker deletes after reading it (a killed worker leaves it to the section 6 orphan rule).

Column interface, per SDK type (what the store holds and what Python receives):

| SDK type | Store element | Python value per element |
|---|---|---|
| char (0) | `utf8`: the sample's text and `u64` offsets; one string per sample whatever `count` | `str`: bytes before the first NUL, UTF-8 (a BOM kept), else cp1252 with `errors="replace"` |
| bool (1) | `bool`: one byte, 0 or 1 (any nonzero source byte is 1) | `bool` |
| int (2) | `i32` LE | `int` |
| bitfield (3) | `u32` LE | `int` |
| float (4) | `f32` LE, bits as stored | `float` widened from the f32 |
| double (5) | `f64` LE, bits as stored | `float` |

A variable with `count > 1` (other than char) is a list of `count` elements per sample, stored sample-major. Decoded columns have no missing values: every selected variable has one value per sample, and non-finite floats are kept as stored (NaN payloads included; `None` first appears in the multi-file merge and in the kernels' normalisation). `sample_indices` are the native record indices of `_iter_sample_indices`, one per sample, as `u32`.

Coach column store, format version 1 (`.ccs`):

```
offset 0   8 bytes  magic "COACHCS\0"
offset 8   u32 LE   format version (1)
offset 12  u32 LE   header length H
offset 16  H bytes  header, UTF-8 JSON
           zero padding to a multiple of 8: the data section starts here
           blocks, each at an offset relative to the data section that is a multiple of 8
```

Header fields (all required, `text_offsets` only on `utf8` columns; a reader ignores fields it does not know): `format` (`"coach-column-store"`), `version` (1), `stage` (`"decode"`), `producer` (`"coach-analysis <version>"`), `source` (`file_size`, `record_count`, `tick_rate`, `buffer_offset`, `buffer_length`, `num_variables`: the worker fails the job unless each equals its own scan), `sampling` (`target_hz` or null, `step`, `integral_step`, `sample_rate_hz`, `sample_count`), `channel_mode` (`"all"` or `"selected"`), `sample_indices` (block), `columns` (in decode order: `name`, `type_code`, `count`, `var_offset`, `element`, `data` block, and for `utf8` `text_offsets` block of `sample_count + 1` u64). A block is `{"offset", "length"}` in bytes. A reader rejects another magic, another `version`, a `stage` it does not expect, an element it does not know, a column whose type, count or offset differs from the scanned variable, and a block outside the file. A change of layout or meaning bumps `version`; adding an optional header field does not.

Python sites that still materialise lists after phase 1, and what each needs: `docs/08-rewrite/port/phase1-decode.md` section 5. `TelemetryTable` still copies each column into a list (`analysis_engine.py` is part of the analyzer bundle, so reading buffers there changes `analysis_id`); that change moves with phase 2. The memory benefit is measured, never assumed; the admission formula (section 6) changes only on an N100 measurement.

### Boundary B: `kernels` (phase 2)

Each kernel returns the same Python objects (dict insertion order included) the Python stage returns, in process (PyO3), reading columns from the coach column store.

### Boundary D: `json_writer` (phase 3)

`storage._json_text` and `workflow._write_json_cache` call the native writer.

## 8. Parity check

`python tools/parity/analysis_parity.py capture --native-stages <list> --native-bin <coach-analysis> --source <ibt> --out <out>` then `compare <python-out> <native-out>` (existing normalisation: timestamps, elapsed times, analyzer digest pinned). Pass = every file byte-identical. `analysis_parity.py corpus --native-stages <list> --native-bin <coach-analysis> --out <dir>` runs the whole gate in one command: the decoded-matrix golden, the in-process capture pair in raw and parsed mode for every fixture and every `COACH_PRIVATE_CORPUS` recording, and the four worker-interface cases, and fails unless all are identical and the native capture's own timings show that the native stage ran.

The existing harness parses persisted JSON and re-serializes it before comparing, so it cannot see the writer's own bytes. W6 adds, before phase 3 and used by every phase:
- **Raw mode** (`--raw`): compares the original persisted bytes after textual substitution of an explicit allowlist only (ISO instants in `*_at` values, the report-directory timestamp slug, `*_ms` / `elapsed_seconds` values, the timestamp-derived `analysis_sha256`, the job-root and archive-root prefixes as `<iracing_root>` / `<archive_root>`). Phase 3 passes only in raw mode; the parsed comparison stays as a diagnostic.
- **Production identity:** one capture pair per phase with the analyzer digest unpinned, run in the same image; `analysis_id` and run ids must be equal between Python and native.
- **Worker-interface captures:** captures driven through `mcp_server.py` with the section 3 roots and selectors (not one explicit file) for a two-file reconnect group with overlapping samples, a restaged recording, and a `local-file` fallback recording; Linux container against Windows oracle with only the root prefixes, line endings, and the path separators that follow `<archive_root>` or `<iracing_root>` (normalised to `/`) normalized. Raw mode compares SQLite files only as parsed row dumps. Corpus: `test-data/ibt/synthetic-race.ibt` (committed) and the private corpus named by `COACH_PRIVATE_CORPUS` (at least the 203 MB New Hampshire race, the 146 MB Iowa race, one practice, one test session; never committed). Phase 1 also requires `tools/parity/emit_ibt_golden.py` matrix-hash equality. An unexplained difference fails the stage (D2); no tolerance.
