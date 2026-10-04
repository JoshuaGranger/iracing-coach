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
| `IRACING_COACH_NATIVE_STAGES` | the server's native stage setting (`server-config.md` section 3): the comma list `decode,kernels,writer` when the image carries `coach-analysis`, empty without it or when the setting is `none` (section 7) |
| `IRACING_COACH_NATIVE_BIN` | the executable the server resolved (`/usr/local/bin/coach-analysis` in the image; `server-config.md` section 3); set only when `IRACING_COACH_NATIVE_STAGES` is non-empty, which is also the only time the worker reads it |
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

**Offline stints (landed, W6 and W6b).** Discovery keys an offline recording (`SubSessionID` and `SessionID` 0, timed by the disk header) by its own file start, so each stint of a Test Drive is a group of its own. The server joins stints into one library session (`server/src/offline_sessions.rs`) and the worker, given any stint's `group_id`, selects the same stints from the job root (`workflow._offline_session_groups`): same driver, car, track and configuration, session number, `SessionUniqueID` and type; session start (`sessionStartDate - sessionStartTime`) within 2 s; no overlap; **the same setup**: equal values fingerprints of the recordings' own `CarSetup` (`setup-keys.json`, derived keys excluded; the server reads it from the recording's `recording` setup observation, the worker from the raw session-info text, both as `coach_ibt::setup_values_fingerprint`), because an analysis has one setup and a test day changes setups between stints. A setup change starts another session of the same sim session; a stint that returns to an earlier setup joins that setup's session. A joined selection is keyed by its first stint's `group_id` and lists `joined_group_ids`; its analysis covers every stint. **A stopped lap counter:** the sim stops counting at lap 1024 of one sim session (`Lap` stays 1024, 1025 for 5 s after each start/finish crossing; `LapLastLapTime` freezes). A recording whose last `Lap` sample is 1024 or more is never joined; the worker refuses its analysis (`LapCounterStoppedError`, a tool error naming the file), and the server defers it before calling the worker (job `deferred` with `error_code` `lap_counter_stopped` and an `error_message` naming the file; the session's `analysis_reason` is that code; read from `recordings.header_json.lap_last`). Single-stint selections are unchanged, so the parity corpus is unaffected.

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
5. Tests: Windows-literal path tests skip on POSIX; tests that read `companion-app/` C# sources are removed or rewritten before `companion-app/` is deleted. Done: none remained when `companion-app/` was deleted in wave 4 (2026-10-03).
6. Each change that alters output bumps the analyzer bundle (existing rules in `test_analysis_pipeline_identity.py`).

## 6. Timeouts, cancellation, memory

| Item | Rule |
|---|---|
| Timeouts | `analyze_iracing_race` 10 min; `discover`, `catalog` 2 min; other light tools 3 min |
| Cancel | `POST /api/v1/jobs/{id}/cancel` or timeout: SIGTERM to the process group, SIGKILL after 5 s; job `cancelled` or `failed/timeout` |
| Orphans | after a kill, delete the killed process's `*.<pid>.tmp` at once; delete `.write-*` and `.copy-*` under that driver's archive root only when no worker process for that driver is running (names are random, `storage.py:668,710`), and `coach-decode-*` and `coach-merge-*` directories (native stores, section 7, with whatever the kernels child mapped from them) under that driver's `TMPDIR` (`/data/py/<customer_id>/tmp`) on the same condition: right after a kill when the driver has no other worker, else once its lanes go idle, and at server start. The server removes nothing else from `TMPDIR` (it is the worker's `HOME`); the kernels child's diagnostics file is anonymous and ends with the process; SQLite WAL recovers itself |
| Admission (analysis) | before `analyze`: estimated peak = F x total bytes of the job's recordings + 0.15 GiB for one recording; for a group of two or more recordings 1.5 x that (Python: `_load_analysis_telemetry` keeps every decoded table while it builds merged columns, sample references and provenance; native: `coach-analysis merge` holds the per-file stores and the merged store at once). F = **5.0** when the worker runs native `decode` and `kernels` (`server-config.md` section 3), else **11.5**. If the estimate > the ceiling, **2.0 GiB** with F = 5.0 and 3.0 GiB with F = 11.5, the job is `deferred` with `exceeds_memory_budget`. Measured: pure Python 2.273 GiB at 203.2 MB, one file (P1); native on the N100 (`acceptance/g1-native.md`) the worker plus its largest child 0.880 GiB at 203.2 MB, one file, 0.795 GiB at 270.5 MB, 1.296 GiB at 197.5 MB in two files, 1.587 GiB at 227.9 MB in two files (each bound is 56 % to 87 % of its estimate); the ceiling then admits one recording up to 397 MB and a group up to 254 MB in total. The server stores `estimate_bytes` in the job; a deferred analysis is one row per session: asking again after the analyzer digest, the profile or the recordings changed moves that row to the new key, keeping its `job_id` (re-queued if it now fits), and removes the other deferred rows of the session |
| Shared memory budget | every worker job declares an estimate before it starts: analysis as above; light jobs that load `analysis.json` (tuning recommend, which loads the driving and the target analysis, `tuning_workflow.py:1114-1116`; baseline with `analysis_path`; history) 10 x the sum of the loaded files' bytes; other light jobs 0.25 GiB; projection 0.6 GiB. A job starts only when its estimate plus the estimates of running jobs plus 0.3 GiB for the server is <= the budget, **2.7 GiB** with the native model and 3.7 GiB with the Python one; otherwise it waits (interactive light jobs wait at the head of the queue). G1 measures the whole-container peak with an analysis, a two-analysis tuning job and an upload overlapping |
| At the limit | container `mem_limit` 3 GiB with the native model (4 GiB for the pure-Python worker, the budget plus the same 0.3 GiB margin), no swap; the worker has `oom_score_adj` 1000 so the kernel kills it, not the server; result `worker_oom`, not retried automatically; the estimate factor is raised to the observed ratio |
| Concurrency | one analysis-lane and one light-lane process at most, further limited by the shared budget; interactive light jobs (priority 100) run before ingest discovery |
| Restart | jobs `running` at server start become `interrupted` and are requeued once |

## 7. Rust stage substitution (port phases, after v1)

- `IRACING_COACH_NATIVE_STAGES` is a comma list (`decode`, `kernels`, `writer`; phase 3 named the stage `writer`, the name `json_writer` used before it existed is unknown). The Python call site checks the list once per job and calls the native stage instead of its own; with the variable empty, behaviour is unchanged. A listed stage that the build does not provide, or an unknown name, fails the job (`native_stages.NativeStageError`): a silent Python fallback would let a parity run compare Python with Python. The one exception is the writer's counted fallback for values outside its model (boundary D), which the parity gate requires to be zero. No other Python code changes.
- The native stages live in crate `crates/coach-analysis`. Phase 1 (`decode`) runs as the executable `coach-analysis` (`IRACING_COACH_NATIVE_BIN`, else `PATH`), started by the worker per recording, and hands its result over in a coach column store file (below). Phase 2 (`kernels`) runs as a child process of the same executable over that store (boundary B); phase 3 (`writer`) runs `coach-analysis write` once per serialisation of the analysis object (boundary D); there is no PyO3 module. Phase 1 chose a process and a file because the decoder's memory is released before Python builds its lists, the executable does not link against one Python ABI, and coach-server can write the same store at ingest later. Measured cost of the hand-over: `docs/08-rewrite/port/phase1-decode.md`.

### Boundary A: `decode` (phase 1)

Call site: `workflow._load_analysis_telemetry` calls `native_stages.load_telemetry(path, channels, target_hz)` instead of `ibt_reader.load_telemetry`. It returns the same mapping, key for key and value for value (`metadata`, `session_info`, `available_variables`, `variables`, `samples`, `sample_indices`, `native_tick_rate_hz`, `sample_rate_hz`, `source_record_count`, `sample_count`, `channel_selection`). Header, session info, variable catalogue and summary still come from `ibt_reader.scan_ibt` in Python (they are identity data, about 13 ms on the 203 MB race); the native stage decodes the samples. With `decode` alone, the multi-file merge, `sample_provenance`, `TelemetryTable` and everything after it are unchanged Python; `kernels` changes them (boundary B).

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

Page alignment (phase 3, optional header field `alignment`, version still 1): the decode and merge stages pad the header JSON with trailing spaces so the data section starts on a 4096-byte boundary of the file, and start every block on such a boundary (`"alignment": 4096`). Readers keep accepting any multiple of 8, so earlier stores and the vector stores the Python tools write read unchanged. The kernels child and the merge stage map the store read-only (`coach_analysis::mapped`) instead of reading it whole, so a column costs memory only when a kernel reads its pages; the file must not change while it is mapped (it is private to the job's `coach-decode-*` or `coach-merge-*` directory).

Python sites that still materialise lists after phase 1, and what each needs: `docs/08-rewrite/port/phase1-decode.md` section 5. With `decode` alone `TelemetryTable` still copies each column into a list; `kernels` replaces it at run time with `StoreTable` over the store (boundary B), so no analyzer-bundle file changes. The memory benefit is measured, never assumed; the admission formula (section 6) changes only on an N100 measurement.

### Boundary B: `kernels` (phase 2)

Each kernel replaces one module-level entry point of the analysis that reads per-sample columns (`docs/08-rewrite/port/phase2-plan.md` section 2; the list is `native_stages.KERNEL_ENTRIES`) and returns the same Python objects (types, float bits, dict insertion order) the entry point returns. They run in a child process, not in process: no Python ABI in the build, the child's memory is released when it exits, and the measured hand-off does not justify PyO3 (on the 203 MB race every kernel call, child work and JSON both ways included, takes 2.7 s against 7.0 to 7.7 s for the same entry points in Python; `port/phase2-kernels.md`).

- **Stages.** `kernels` requires `decode` (`kernels` alone fails the job): the kernels read the decode store, and converting Python lists into one would rebuild the memory the stage removes. An entry point the build does not provide fails the job; nothing falls back to Python.
- **Table.** With `kernels`, `native_stages.load_telemetry(..., columns="store")` keeps the store mapped (read only) in its `coach-decode-*` directory and returns each channel and `sample_indices` as a `StoreColumn`: a read-only sequence whose `len`, indexing and slicing convert only the items asked for, exactly as the list reader converts them, and whose iteration walks the mapping. `sample_provenance` is a sequence computed on access. A multi-file group is merged natively (below). `native_stages.kernel_session(telemetry)` wraps the one `analyze_telemetry` call in `workflow.py`: for its duration `analysis_engine.TelemetryTable` is `native_stages.StoreTable` (the oracle's `__init__` on the mapping with empty samples, then `channels` and `length` from the store; every inherited method is the oracle's code) and each entry point is rebound on its module to a shim that calls `native_stages.kernel_call`; `_fallback_telemetry_digest` gets a shim over plain lists. Everything is restored, the child stopped and the store closed and deleted in `finally`. No file of `analysis_engine.ANALYZER_SOURCE_FILES` changes, so `analysis_id` is unchanged.
- **Process.** `coach-analysis kernels`, started at the first kernel call of the analysis and kept until it returns. One JSON object per line on standard input and output, in order; standard error is diagnostics (the worker keeps it in a temporary file and reports its tail on failure). Values are codec version 1 (`scripts/native_codec.py`, shared with the kernel vectors), every list inline; the table is `{"$table": 1}`, its channel mapping `{"$table_channels": 1}`, one of its channels by identity `{"$table_column": "<name>"}`, a value the child keeps `{"$handle": n}`.

```
start   {"version": 1, "codec": 1, "store": "<path>", "table": {"codec": 1, "length", "channel_names",
         "metadata", "session_info", "variables", "available_variables", "channel_units"},
         "steering_basis": <steering_basis.SteeringBasis>}
        -> {"ok": true, "length": n, "entries": ["analysis_engine._lap_summaries", ..., "pit_loss.measure_pit_loss"]}
call    {"entry": "<module.name>", "args": [...], "kwargs": {...}}
        -> {"result": <value>, "accessed": [<channel>, ...]}
         | {"raises": {"class": "<builtin exception>", "message": "..."}, "accessed": [...]}
         | {"error": "..."}
```

`table` holds the fields of `TelemetryTable` as Python derived them (the same object as the vectors' `table.json`); `steering_basis` is `_steering_basis(table)`, which the oracle has computed and cached before its first ported call. The worker fails the job (`NativeStageError`) unless the start answer's `length` is the table's and `entries` lists every entry point. `accessed` is added to the table's `accessed_channels`; every name must be a table channel. `raises` is an exception the oracle raises for the same input: the shim raises that builtin class with that message. `error`, an unreadable answer, a closed output or a non-zero exit is `NativeStageError`. `_setup_channel_series` answers `{"$handle": n}`, which the shims pass back to `_setup_telemetry` and `_track_profile`, its only readers. A refused start answers `{"error"}` and exits 1; end of input exits 0.

- **Merge store** (multi-file groups). `coach-analysis merge` reads `{"version": 1, "inputs": ["<decode store>", ...], "channels": ["<name>", ...], "out": "<path>"}` on standard input (the per-file decode stores in group file order, the merged channel order `workflow._load_analysis_telemetry` computes) and writes the column merge of `_load_analysis_telemetry`: samples ordered by `(has_no_time, time, file_index, sample_index)` on `SessionTime` (else `SessionTimeOfDay`), a sample within 1e-9 s of the previous kept time filling only that row's empty cells, first non-null wins. The store is the decode layout with `stage: "merge"` (a decode-only reader rejects it): `sample_indices` holds each merged sample's native record index in its source file, a `provenance` object holds the block `file_index` (u16 LE per sample: the source file's position among the inputs with samples), and each column may carry a `present` block (one byte per sample, 0 or 1; absent means every cell is present); a missing cell's data is zero (empty text). Files with no sample are skipped; a channel two inputs store with different element types or counts fails the merge. `channels` must equal the columns in order. The metadata merge (representative session info, variable catalogues, rates) stays in Python; the merged provenance keeps no `contributors` list (nothing in the analysis reads provenance). Its directory is `coach-merge-*`, under the same orphan rule as `coach-decode-*` (section 6).

### Boundary D: `writer` (phase 3)

The analysis object is serialised natively at the three sites that write it (`native_stages.WRITER_SITES`), each by `native_stages.writer_call(site, mode, value, oracle)` when `writer` is listed (independent of the other stages; `docs/08-rewrite/port/phase3-writer.md`):

| Site | Python it replaces | Mode | Result |
|---|---|---|---|
| `analysis_json`: `storage._analysis_json_bytes` (the `analysis.json` payload of `save_report_artifacts`) | `_json_text(dict(analysis)).encode("utf-8")` | `bytes`: `ensure_ascii=False`, `allow_nan=False`, terminator `\n` | the bytes |
| `analysis_sha256`: `workflow._analysis_sha256` (the analysis-cache envelope's hash, and the cache-hit check) | `stable_hash(analysis, 64)` | `sha256`: `ensure_ascii=True`, `allow_nan=True`, no terminator | the hex SHA-256 |
| `json_cache`: `workflow._write_json_cache` (analysis cache, telemetry-profile and event caches) | `Path.write_text(json.dumps(...) + "\n", encoding="utf-8")` | `file`: as `bytes`, terminator `os.linesep` (text-mode translation of the one newline), written to the temporary path the site then renames | none |

Every mode is `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ...)`. Other `json.dumps` and `_json_text` calls (manifests, the history index, small report files; each under 10 ms on the 203 MB race) stay Python.

`coach-analysis write` reads one JSON request line and then the value as a pickle, protocol 5, written by `native_stages._WriterPickler` (CPython's C pickler: exact `dict`, `list`, `tuple`, `str`, `bytes`, `int`, `float`, `bool`, `None` take its fast paths, as do an exact `set`, `frozenset` or `bytearray`, which the writer answers with `fallback`). Any other object (a subclass, a class, a function, an `Enum`, a `Path`, anything `default=str` would stringify) stops the pickle in `reducer_override` without calling anything of the object, and no child starts: the call is a counted fallback (`native_stages.writer_fallback`), and the site's own Python code runs once, from the start, so `default=str` and a subclass's methods run in `json.dumps`'s traversal order, once per occurrence, as in Python. The seam sends no marker. The request still names the marker global `native_stages._writer_marker`, and the reader still accepts it with a recorded JSON text or exception (`python_exception`), so the formats below are unchanged; the reader accepts only data opcodes and that one global.

```
request  {"version": 1, "ensure_ascii": bool, "allow_nan": bool, "sort_keys": true, "separators": [",", ":"],
          "indent": null, "terminator": "" | "\n" | "\r\n", "output": "bytes" | "file" | "sha256",
          "path": "<file>" (file only), "marker": ["<module>", "<qualname>"], "max_str_digits": n}
answer   {"ok": true, "length": n} (bytes: then n bytes; file: written) | {"ok": true, "length": n, "sha256": hex}
       | {"raises": {"class": "ValueError" | "TypeError", "message": "..."}}
       | {"raises": {"class": "UnicodeEncodeError", "encoding": "utf-8", "start": s, "end": e, "characters": [...], "reason": "surrogates not allowed"}}
       | {"python_exception": i} | {"fallback": "<reason>"}
```

The bytes are CPython 3.12's: key order (`list.sort` of the items, including the `TypeError` of keys that do not order and the order of NaN keys), separators, no indentation, `float.__repr__` (`coach-numerics`), `int` repr beyond `i64` under `sys.get_int_max_str_digits()`, string escaping for both `ensure_ascii` settings (lowercase `\uXXXX`, surrogate pairs, lone surrogates), `NaN`/`Infinity` or `ValueError: Out of range float values are not JSON compliant: <repr>`, `ValueError: Circular reference detected` where the encoder's markers find a cycle, `repr(bytes)` for `default=str`, and the UTF-8 encoding's `UnicodeEncodeError` for a lone surrogate (in `file` mode the seam then creates the empty file, as `write_text` does; the exception's `object` holds only the surrogates at their positions). The seam raises each exception with the oracle's class and message; since it sends no marker, a `python_exception` answer names nothing it recorded and is a `NativeStageError`. `fallback` (a set, frozenset or bytearray, a dict key other than exactly `str`, `int` within `i64`, `float`, `bool` or `None`, containers nested more than 200 deep), like an object stopped in the seam above or a pickling `RecursionError` (neither starts a child), runs the site's own Python code once; `native_stages.writer_fallback` counts every one of these, and the section 8 gate fails on any. A request or pickle the seam never sends exits 1 (`NativeStageError`). No analyzer-bundle file changes, so `analysis_id` is unchanged.

## 8. Parity check

`python tools/parity/analysis_parity.py capture --native-stages <list> --native-bin <coach-analysis> --source <ibt> --out <out>` then `compare <python-out> <native-out>` (existing normalisation: timestamps, elapsed times, analyzer digest pinned). Pass = every file byte-identical. `analysis_parity.py corpus --native-stages <list> --native-bin <coach-analysis> --out <dir>` runs the whole gate in one command: the decoded-matrix golden, the in-process capture pair in raw and parsed mode for every fixture and every `COACH_PRIVATE_CORPUS` recording, and the four worker-interface cases, and fails unless all are identical and the native capture's own timings show that the native stage ran.

**Writer calls, site for site** (`writer`). The counters also wrap the three writer sites (both captures), `native_stages.writer_call` and `native_stages.writer_fallback`: both captures call each site equally often and the oracle capture reaches all three; the oracle capture makes no native write; with `writer` the native capture writes every site call natively and falls back for none (a fallback, whether the writer answered `fallback` or the seam stopped the pickle at an object outside the exact types, is the site's own Python run once and is counted, so the gate sees it). Because the raw allowlist hides `analysis_sha256`, every capture also records `outputs/analysis-sha256-check.json`: Python's `stable_hash` of the cached analysis equals the envelope's hash (compared between the captures like any output). Capture format 5 carries the writer counts.

**Kernel calls, count for count** (`kernels`). Both captures count calls with `native_stages.install_call_counters` (in-process captures in their timing record, worker captures through `worker_trace.py`): `native`, the `native_stages.kernel_call` calls by entry point; `oracle`, the calls of the ported entry points themselves, outermost only (an entry point called inside another runs inside that one's kernel). The native capture's `native` must equal the oracle capture's `oracle` entry for entry, the native capture must show no `oracle` call and the oracle capture no `native` call, per recording and per worker case. Without `kernels` the native capture must show no kernel call. Capture format 4 carries the counts; an older capture fails the rule.

The existing harness parses persisted JSON and re-serializes it before comparing, so it cannot see the writer's own bytes. W6 adds, before phase 3 and used by every phase:
- **Raw mode** (`--raw`): compares the original persisted bytes after textual substitution of an explicit allowlist only (ISO instants in `*_at` values, the report-directory timestamp slug, `*_ms` / `elapsed_seconds` values, the timestamp-derived `analysis_sha256`, the job-root and archive-root prefixes as `<iracing_root>` / `<archive_root>`). Phase 3 passes only in raw mode (and the `analysis_sha256` recomputation above); the parsed comparison stays as a diagnostic.
- **Production identity:** one capture pair per phase with the analyzer digest unpinned, run in the same image; `analysis_id` and run ids must be equal between Python and native.
- **Worker-interface captures:** captures driven through `mcp_server.py` with the section 3 roots and selectors (not one explicit file) for a two-file reconnect group with overlapping samples, a restaged recording, and a `local-file` fallback recording; Linux container against Windows oracle with only the root prefixes, line endings, and the path separators that follow `<archive_root>` or `<iracing_root>` (normalised to `/`) normalized. Raw mode compares each SQLite file as a raw row dump (`raw/<name>.dump.json`) to which only the raw allowlist is applied, value by value: an ISO instant in a `*_at` column, a `*_ms` or `elapsed_seconds` number, a 64-hex `analysis_sha256`, and the allowlist's text rules inside every other string (so `created_at="corrupt"` or a differing `last_updated` stays visible); the parsed row dumps with their key-based normalisation remain a diagnostic. Corpus: `test-data/ibt/synthetic-race.ibt` (committed) and the private corpus named by `COACH_PRIVATE_CORPUS` (at least the 203 MB New Hampshire race, the 146 MB Iowa race, one practice, one test session; never committed). Phase 1 also requires `tools/parity/emit_ibt_golden.py` matrix-hash equality. An unexplained difference fails the stage (D2); no tolerance.
