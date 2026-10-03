# Port phase 2: per-sample analysis kernels

Date 2026-10-03. Authority: owner decision D2 and its ruling (port the stages the profile shows dominate, each gated on byte-identical output; an unexplained difference is a failure), `architecture.md` section 12. Plan: `phase2-plan.md` (units, vectors, integration). Interface: `contracts/worker-interface.md` sections 7 and 8. Builds on `phase1-decode.md` (decode stage, column store) and `numerics.md` (`crates/coach-numerics`).

## 1. Result

- The 17 entry points of `analysis_engine.analyze_telemetry` that read per-sample columns run in Rust (`crates/coach-analysis/src/kernels/`, five units). With `IRACING_COACH_NATIVE_STAGES=decode,kernels` the worker keeps the decode store mapped, builds no column list and no `TelemetryTable` copy, and calls each ported entry point in one `coach-analysis kernels` child per analysis. Python keeps the orchestration and the functions that read only laps, runs and the analysis dict.
- Parity: `analysis_parity.py corpus --native-stages decode,kernels` passes on Windows and on Linux (WSL, the binding oracle): both fixtures and all 50 unique private recordings (64 files, 14 duplicates by SHA-256) byte-identical in raw and parsed mode, the truncated fixture rejected identically, and the four worker-interface cases identical with the same `analysis_id` values on both platforms. No unit diverged, so nothing needed fixing inside a unit and no blocker remains.
- No ported entry point runs in Python on the native side: every native capture shows zero oracle entry-point calls and one kernel call per oracle call, count for count, per recording and per worker case; one native decode per recording; one `kernels` child per analysis (3 processes per native worker run: worker, decode, kernels). `analysis_id` is unchanged (no analyzer-bundle file changes).
- Cost on the dev PC, 203 MB New Hampshire race, 5 alternating cold worker runs, Python kernels (`decode`) against native (`decode,kernels`), medians: worker wall time 10.78 s to 6.14 s (-43 %); worker peak working set 2.296 to 0.229 GiB (-90 %); peak committed memory of the whole process tree (worker and children at the same moment) 2.309 to 0.691 GiB (-70 %). The `kernels` child is now the largest process (0.635 GiB peak commit).

## 2. Design

### Units

| Unit | Entry points | Rust module | Lines | Vectors (committed calls; corpus calls per platform) |
|---|---|---|---|---|
| I0 skeleton | value tree, codec v1, dispatch, session, errors | `kernels/{value,codec,dispatch,session,error}.rs` | 2,360 (with `serve.rs`) | codec: 63 documents round trip |
| U1 table | `_setup_channel_series`, `_setup_telemetry`, `_conditions_summary`, `_driver_adjustments_summary`, `_caution_flag_series`, `_dt_series`; the native merge | `kernels/table/` | 2,609 | 1,369 Windows / 1,346 Linux calls, 23,372 / 22,916 views |
| U2 laps | `_lap_summaries`, `_service_events`, `_disrupted_sample_indices` | `kernels/laps/` | 1,595 | 11; 305 |
| U3 traces | `_lap_trace_payload`, `_track_profile` | `kernels/traces/` | 1,875 | 8; 208 |
| U4 sections | `_corner_tire_age_summary`, `_pace_attribution`, `_clean_lap_traces`, `pit_loss.measure_pit_loss` | `kernels/sections/` | 3,962 | 22; 358 |
| U5 field | `analyze_groove_evolution`, `build_track_geometry` | `kernels/field/` | 2,947 | 28; 228 |

Each unit passed alone against vectors recorded from the oracle's own calls (fixtures, synthetic tables for the branches no recording reaches, and the private corpus at native rate and 20 Hz), on Windows and Linux; the unit reports list the calls per entry, the platform comparison (no Windows/Linux difference in any result) and the oracle branches no vector reaches. Every numeric primitive comes from `coach-numerics`; no unit changed it.

### Composition

`dispatch::UNITS` collects each unit's `ENTRIES`; `Session` holds the store, the table metadata, the steering basis Python computed, one `Table` (U1's view cache, shared by every entry) and the handles. Entries share nothing else: each is a pure function of the table and its arguments, and the access record is reset per call. The child (`kernels/serve.rs`, compiled into the binary) answers the start request with the entries it provides, and the worker fails the job unless every entry of `native_stages.KERNEL_ENTRIES` is among them.

The one composition change made here: `_setup_channel_series` is served by `dispatch::HANDOFF`, a variant that keeps U1's `SetupSeries` in the session and answers `{"$handle": n}`, as the contract already specified; `_setup_telemetry` (U1) and `_track_profile` (U3) take the handle. The replay entry still returns the dict the vectors record. Before this, the series crossed the pipe three times (returned, then sent to both readers: 43 MB of JSON each way on the 65 MB practice session), and the practice analysis took 7.0 s with the kernels against about 5 s in Python; with the handle it takes 1.1 s. `tests/kernels_handoff.rs` replays every recorded `_setup_telemetry` and `_track_profile` call with the handle in place of the dict: 12 committed calls, 112 with U1's corpus vectors and 112 with U3's, identical, with the same accessed channels as the replay entry.

### Hand-off

| Side | What happens |
|---|---|
| Python, `workflow._load_analysis_telemetry` | `native_stages.load_telemetry(..., columns="store")`: the phase-1 scan, decode and validation, then `StoreColumn` sequences over the mapped store; a multi-file group is merged by `coach-analysis merge` into one `merge` store |
| Python, `kernel_session` | rebinds `analysis_engine.TelemetryTable` to `StoreTable` and the 17 entry points to shims for one `analyze_telemetry` call, restores them in `finally`, stops the child, deletes the store |
| Python, shim | encodes the arguments (codec v1, the table by reference), sends one line, decodes the answer, adds `accessed` to `table.accessed_channels`, raises the oracle's exception for a `raises` answer |
| Rust, `coach-analysis kernels` | reads the store once, serves calls until end of input |

Hand-off cost per entry on the race (`decode,kernels`, one run, dev PC; `exchange` is send, child, receive and `json.loads`; `codec` is the Python encode and decode around it):

| Entry point | Calls | Total s | Exchange s | Codec s | Request MB |
|---|---|---|---|---|---|
| `_lap_trace_payload` | 1 | 1.308 | 0.947 | 0.361 | 0.21 |
| `_lap_summaries` | 1 | 0.300 | 0.287 | 0.013 | 0.14 |
| `_setup_telemetry` | 1 | 0.220 | 0.220 | 0.000 | 0.00 |
| `_dt_series` | 1 | 0.173 | 0.139 | 0.034 | 0.00 |
| `_setup_channel_series` | 1 | 0.134 | 0.133 | 0.000 | 0.00 |
| `_caution_flag_series` | 2 | 0.120 | 0.053 | 0.067 | 0.00 |
| `build_track_geometry` | 1 | 0.110 | 0.108 | 0.002 | 0.00 |
| `_track_profile` | 1 | 0.104 | 0.096 | 0.008 | 0.00 |
| `analyze_groove_evolution` | 1 | 0.061 | 0.029 | 0.032 | 2.14 |
| `_conditions_summary` | 1 | 0.055 | 0.055 | 0.000 | 0.00 |
| `_corner_tire_age_summary` | 1 | 0.040 | 0.026 | 0.013 | 0.99 |
| `_pace_attribution` | 1 | 0.035 | 0.023 | 0.012 | 0.96 |
| `_driver_adjustments_summary` | 1 | 0.023 | 0.023 | 0.000 | 0.00 |
| `_clean_lap_traces` | 1 | 0.013 | 0.008 | 0.005 | 0.21 |
| `_disrupted_sample_indices` | 1 | 0.010 | 0.007 | 0.003 | 0.00 |
| `_service_events` | 1 | 0.003 | 0.003 | 0.000 | 0.00 |

`measure_pit_loss` is not called on the race (no clean reference lap). In all: `analyze_telemetry` 3.08 s, of which 2.71 s in the shims and 0.38 s in the Python that stays; the store-backed load 0.23 s. The plain Python analysis of the same recording took 7.0 to 7.7 s (`phase2-plan.md` section 1). The largest single cost left is `lap_traces` itself: 23 MB of JSON written by the child, parsed by `json.loads` and walked by the codec's `decode` (about 0.6 s of the 1.3 s); phase 3 removes the round trip by writing `analysis.json` natively.

### What Python still does per sample

A call-count trace of every analyzer-bundle function during the native race analysis (the shims and the codec excluded) shows no ported entry point. The per-sample work left is in code that stays Python by the plan's rule: `_damage_repair_summary`, `_identity`, `_scheduled_race_laps` and `_runs` read single samples and a few whole columns through `StoreTable` (`_finite` 419,434 calls, `_bool` 85,330), and `build_race_investigation` walks the 160-bin traces (`race_investigation._number` 370,790 calls). Together with the trace decode they are most of the remaining Python time (section 4).

## 3. Parity

Command (Windows, bundled CPython 3.12.14; Linux, WSL Ubuntu `python3.12` 3.12.3; release builds of `coach-analysis` copied aside so the gate binary could not change during the run):

```powershell
$env:COACH_PRIVATE_CORPUS = 'C:\u3c'   # junction to the scratch copies, short paths
& $py -X utf8 tools\parity\analysis_parity.py corpus --native-stages decode,kernels --native-bin <coach-analysis.exe> --out C:\p2i\cw
```

```bash
COACH_PRIVATE_CORPUS=~/p2i/corpus python3.12 -X utf8 tools/parity/analysis_parity.py corpus \
  --native-stages decode,kernels --native-bin ~/p2i/bin/coach-analysis --out ~/p2i/gate-corpus
```

Per recording: the phase-1 decode golden at native rate and 20 Hz; one cold capture with no native stage and one with `decode,kernels`, compared in raw and parsed mode; the native decode in the native capture's stage timings only; and the kernel-call rule (`worker-interface.md` section 8). The fixtures add the four worker-interface cases through `mcp_server.py` (`worker_trace.py` counts the calls in each worker process). The output directory must be a short path on Windows: the scratchpad path plus the archive's cache file name exceeds 260 characters and the oracle fails with `FileNotFoundError` on both sides.

Run 2026-10-03 on DESKTOP-JOSHUA, Windows and WSL gates at the same time, release builds of the final source (Windows SHA-256 `8c06d05a6e6fe27a`, Linux `635c47e4ebd56795`). `=` is identical on every golden field at native rate and 20 Hz on both platforms; analysis is raw / parsed and identical on both platforms in every row; kernel calls are the native capture's `native_stages.kernel_call` count, equal entry for entry to the oracle capture's entry-point calls (17 entry points; the oracle calls some twice and some not at all depending on the recording, for example `_caution_flag_series` twice on the practice session and `measure_pit_loss` not on the race, so 15 to 18). Workflow times are the two output runs on Windows while both gates and the WSL check shared the machine: they rank, they do not measure (rows 7 and 17 are such outliers; section 4 measures). Recordings are the phase-1 scratch copies, never committed.

| # | Kind | Recording | MB | SHA-256 | Golden | Analysis raw / parsed | Kernel calls | Workflow s, oracle / native |
|---|---|---|---|---|---|---|---|---|
| 1 | fixture | synthetic-race.ibt | 0.0 | `540f7208f1bd` | = | identical / identical | 17 | 0.24 / 0.28 |
| 2 | fixture | truncated-race.ibt | 0.0 | `cd7f4eb5c396` | = | both reject: IbtTruncatedError | - | - |
| 3 | private | Supra, New Hampshire, 2026-07-27 22:09:39 | 147.0 | `02b00a3cca29` | = | identical / identical | 18 | 8.74 / 4.68 |
| 4 | private | Supra, New Hampshire, 2026-08-01 20:09:37 | 128.6 | `0c749e369bf8` | = | identical / identical | 17 | 8.89 / 4.69 |
| 5 | private | Tundra, Kentucky, 2026-07-31 22:24:51 | 40.8 | `13bf212d4b49` | = | identical / identical | 17 | 3.33 / 1.77 |
| 6 | private | Supra, New Hampshire, 2026-08-01 21:09:00 | 202.5 | `447f6f61eb16` | = | identical / identical | 18 | 14.58 / 7.52 |
| 7 | private | Supra, New Hampshire, 2026-07-27 20:09:55 | 125.2 | `4f98b65998d0` | = | identical / identical | 17 | 8.23 / 9.59 |
| 8 | private | Supra, New Hampshire, 2026-08-01 15:09:02 | 203.2 | `582b11e2e65b` | = | identical / identical | 17 | 19.01 / 7.64 |
| 9 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `60752f797021` | = | identical / identical | 17 | 9.72 / 5.74 |
| 10 | private | Supra, Daytona, 2026-08-01 18:39:25 | 75.3 | `60d1ecc277fd` | = | identical / identical | 18 | 4.67 / 2.98 |
| 11 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `7ac18c9c8fed` | = | identical / identical | 17 | 9.44 / 5.38 |
| 12 | private | Tundra, Kentucky, 2026-08-02 22:24:10 | 144.4 | `7fe60f3aedcf` | = | identical / identical | 17 | 7.91 / 4.52 |
| 13 | private | Supra, New Hampshire, 2026-07-28 21:09:03 | 198.5 | `8f76c042b43f` | = | identical / identical | 17 | 14.24 / 7.49 |
| 14 | private | Taurus, Indianapolis, 2026-07-29 21:39:27 | 213.9 | `96a350822126` | = | identical / identical | 18 | 12.17 / 5.95 |
| 15 | private | Tundra, Kentucky, 2026-07-28 22:23:24 | 128.5 | `a58773367610` | = | identical / identical | 17 | 7.79 / 4.35 |
| 16 | private | Supra, New Hampshire, 2026-07-31 21:09:04 | 208.9 | `c5c7c4da6780` | = | identical / identical | 17 | 13.61 / 7.28 |
| 17 | private | Supra, New Hampshire, 2026-08-01 14:09:38 | 124.9 | `f60cbb8ce5c7` | = | identical / identical | 17 | 6.55 / 10.71 |
| 18 | private | Taurus, Indianapolis, 2026-07-27 23:19:28 | 50.2 | `3192d474fc6a` | = | identical / identical | 18 | 4.14 / 2.11 |
| 19 | private | Taurus, Indianapolis, 2026-07-27 23:32:33 | 0.3 | `bc3abab2e0cb` | = | identical / identical | 17 | 0.27 / 0.27 |
| 20 | private | Taurus, Indianapolis, 2026-07-29 21:19:55 | 43.4 | `7263ba84511f` | = | identical / identical | 17 | 3.26 / 1.52 |
| 21 | private | Taurus, Indianapolis, 2026-07-29 21:35:58 | 11.8 | `054cf3dfe295` | = | identical / identical | 18 | 1.63 / 1.17 |
| 22 | private | Supra, Daytona, 2026-08-01 18:27:08 | 16.0 | `c95f9b053722` | = | identical / identical | 18 | 2.05 / 1.43 |
| 23 | private | Supra, Daytona, 2026-08-01 18:32:17 | 6.6 | `4bcef1fd2be5` | = | identical / identical | 17 | 0.97 / 0.70 |
| 24 | private | Supra, Daytona, 2026-08-01 18:34:17 | 11.6 | `cfd28589b7af` | = | identical / identical | 18 | 1.13 / 0.81 |
| 25 | private | Supra, Iowa, 2026-08-03 20:02:24 | 8.0 | `6f404cd4ac46` | = | identical / identical | 17 | 0.98 / 0.78 |
| 26 | private | Supra, Iowa, 2026-08-03 20:04:59 | 7.2 | `cd2b3c20fb0d` | = | identical / identical | 18 | 0.79 / 0.70 |
| 27 | private | Supra, Iowa, 2026-08-03 20:59:41 | 13.3 | `22a20e50dcff` | = | identical / identical | 18 | 1.70 / 1.04 |
| 28 | private | Supra, Iowa, 2026-08-03 22:27:43 | 43.9 | `265b83f2d863` | = | identical / identical | 18 | 4.18 / 2.42 |
| 29 | private | Supra, New Hampshire, 2026-07-27 22:01:53 | 8.9 | `3583870bf3b9` | = | identical / identical | 17 | 0.88 / 0.73 |
| 30 | private | Supra, New Hampshire, 2026-07-28 21:03:59 | 7.0 | `e56b513e0f9e` | = | identical / identical | 17 | 0.78 / 0.69 |
| 31 | private | Supra, New Hampshire, 2026-07-31 20:42:15 | 23.9 | `525e1591f28b` | = | identical / identical | 17 | 1.42 / 0.86 |
| 32 | private | Supra, New Hampshire, 2026-07-31 20:48:41 | 46.5 | `719244c4d68c` | = | identical / identical | 18 | 3.68 / 2.20 |
| 33 | private | Supra, New Hampshire, 2026-07-31 21:01:27 | 8.4 | `4f53d89d5934` | = | identical / identical | 17 | 0.85 / 0.75 |
| 34 | private | Supra, New Hampshire, 2026-07-31 21:03:56 | 6.0 | `99d54b6f018a` | = | identical / identical | 17 | 0.79 / 0.65 |
| 35 | private | Supra, New Hampshire, 2026-08-01 13:44:55 | 65.7 | `c4ebf0253f37` | = | identical / identical | 18 | 4.45 / 2.50 |
| 36 | private | Supra, New Hampshire, 2026-08-01 14:04:32 | 9.9 | `dfb778364979` | = | identical / identical | 17 | 0.93 / 0.77 |
| 37 | private | Supra, New Hampshire, 2026-08-01 15:01:30 | 7.9 | `493746997a6f` | = | identical / identical | 18 | 0.81 / 0.72 |
| 38 | private | Supra, New Hampshire, 2026-08-01 15:03:46 | 9.9 | `527f9457af8b` | = | identical / identical | 18 | 0.93 / 0.79 |
| 39 | private | Supra, New Hampshire, 2026-08-01 20:00:04 | 7.4 | `ef4b55866932` | = | identical / identical | 18 | 0.75 / 0.68 |
| 40 | private | Supra, New Hampshire, 2026-08-01 20:04:37 | 8.2 | `a8d039e18be7` | = | identical / identical | 18 | 0.83 / 0.72 |
| 41 | private | Supra, New Hampshire, 2026-08-01 21:03:47 | 0.3 | `a39fbaeae9b6` | = | identical / identical | 15 | 0.18 / 0.21 |
| 42 | private | Supra, New Hampshire, 2026-08-01 21:04:07 | 7.3 | `7a6fe0798e69` | = | identical / identical | 17 | 0.74 / 0.69 |
| 43 | private | Supra, New Hampshire, 2026-08-02 13:30:23 | 1.4 | `eee4ac362623` | = | identical / identical | 15 | 0.22 / 0.20 |
| 44 | private | Tundra, Kentucky, 2026-07-28 22:12:05 | 15.1 | `7cf70fc05da7` | = | identical / identical | 17 | 1.14 / 0.83 |
| 45 | private | Tundra, Kentucky, 2026-07-28 22:16:00 | 0.3 | `2a3291236467` | = | identical / identical | 17 | 0.20 / 0.23 |
| 46 | private | Tundra, Kentucky, 2026-07-28 22:16:38 | 9.2 | `b8dc17e34d78` | = | identical / identical | 18 | 0.85 / 0.72 |
| 47 | private | Tundra, Kentucky, 2026-07-28 22:19:14 | 7.6 | `1e4154bd3171` | = | identical / identical | 17 | 0.75 / 0.66 |
| 48 | private | Tundra, Kentucky, 2026-07-31 22:16:21 | 9.3 | `ef442f0c8075` | = | identical / identical | 18 | 0.87 / 0.71 |
| 49 | private | Tundra, Kentucky, 2026-07-31 22:19:03 | 13.6 | `ab6188605c0c` | = | identical / identical | 17 | 1.13 / 0.91 |
| 50 | private | Tundra, Kentucky, 2026-08-02 22:14:59 | 3.3 | `836e9e87f431` | = | identical / identical | 15 | 0.29 / 0.27 |
| 51 | private | Tundra, Kentucky, 2026-08-02 22:16:32 | 8.7 | `af651dad2f80` | = | identical / identical | 18 | 0.87 / 0.72 |
| 52 | private | Tundra, Kentucky, 2026-08-02 22:19:00 | 7.5 | `8fe7216a37df` | = | identical / identical | 18 | 0.75 / 0.66 |

Worker cases (both platforms identical, 17 kernel calls each, `analysis_id` the same on Windows and Linux): `synthetic` `9f4f93d4315bc43c14ebf2e7`, `reconnect` (two overlapping files merged by `coach-analysis merge`) `18bb89fd9d2ec273dff77d87`, `restaged` `9f4f93d4315bc43c14ebf2e7`, `local-file` `3fbea207efcae8cd78eb0517`.

Before the handle change the same gate had passed the fixtures, the worker cases and recordings 3 to 6 on both platforms with the setup series passed by value; it was stopped and rerun on the final build.

## 4. Measurements (dev PC)

`tools/parity/measure_worker.py` drives `mcp_server.py` as coach-server does (complete environment, job root, group selector, `target_hz: null`), one cold process per run in a fresh archive, alternating the two modes. New in this phase: `--baseline-stages` (here `decode`, so both sides decode natively and differ only in the kernels) and, on Windows, a job object per run whose accounting gives the peak committed memory of the whole process tree at one moment and its process count, because the worker's own peak no longer includes the process that does the work.

```powershell
& $py -X utf8 tools\parity\measure_worker.py --source C:\p2i\race.ibt --native-bin C:\p2i\bin\coach-analysis.exe `
  --baseline-stages decode --native-stages decode,kernels --runs 5 --base C:\p2i\mw --out C:\p2i\measure-race.json
```

DESKTOP-JOSHUA (i5-11400, Windows 11), bundled CPython 3.12.14, the same release build as the gate, the machine otherwise idle apart from other agents' background work (CPU load 23 % before, 15 % after). Race: `582b11e2` New Hampshire, Supra, 203,246,134 bytes, 189,857 samples, 80 laps.

| | Python kernels (`decode`) | Native kernels (`decode,kernels`) | Change |
|---|---|---|---|
| Worker wall time, median (range) | 10.78 s (9.96 to 10.84) | 6.14 s (5.88 to 6.27) | -43 % |
| Worker peak working set | 2.296 GiB (2.296 to 2.296) | 0.229 GiB (0.228 to 0.230) | -90 % |
| Process tree peak commit | 2.309 GiB (2.309 to 2.310) | 0.691 GiB (0.690 to 0.691) | -70 % |
| Largest single process, peak commit | 2.309 GiB (worker) | 0.635 GiB (`kernels` child) | |
| Processes per run | 2 (worker, decode) | 3 (worker, decode, kernels) | |
| `analysis_id` | `31419c583d3959b352c3a86c` | the same | |

The decoder alone: 0.20 s, 133.0 MiB peak, 126,846,168-byte store (as in phase 1). Phase 1 measured the pure-Python worker at 11.56 s and 2.294 GiB on the same recording; with native decode it was 10.72 s and 2.295 GiB.

The tree peak is the honest figure for admission: the worker drops to 0.23 GiB, but the child's 0.64 GiB happens while the worker holds about 0.06 GiB, and the two peaks do not add. Where the child's commit goes was not broken down in this phase; the plan estimated under 0.5 GiB (store 127 MB, views at most about 130 x 1.5 MB). Known: the child reads the store whole (`ColumnStore::read` is `fs::read`, 127 MB on the race), not mapped as section 4.5 of the plan intended. Candidates, unmeasured: views of `f32` columns held as `f64`, the `PyValue` tree of `lap_traces` and its 23 MB JSON text alive at the same time. The admission formula (`worker-interface.md` section 6) and `mem_limit` change only on the N100 G1 measurement.

## 5. What phase 3 needs

- **The JSON writer (boundary D).** `lap_traces` is 94 % of the race's `analysis.json`; today it is built in Rust, written as 23 MB of codec JSON, parsed and decoded into Python objects (about 0.6 s of the 1.3 s of `_lap_trace_payload`), held through the rest of the analysis, then serialised again by the Python writer. A native writer can keep the subtree in the child and emit it directly; the hand-off then needs a handle for the analysis result, as `_setup_channel_series` has now.
- **The Python per-sample remainder.** `_damage_repair_summary`, `_identity`, `_scheduled_race_laps` and `_runs` still read samples through `StoreTable` (`_finite` and `_bool` 0.5 million calls on the race) and `build_race_investigation` walks the bins in Python (`_number` 0.37 million calls). They are about 0.4 s of the race analysis; port them only if the profile after the writer still ranks them.
- **Child memory.** Map the store instead of reading it (`ColumnStore::parse` over a mapping; `#![forbid(unsafe_code)]` means a safe mapping crate or a scoped exception), then break down the remaining 0.5 GiB before the N100 G1 run sets the admission factor.
- **Contract review.** Done: the Codex Sol 6.1 review (`reviews/port-phase2-review-codex-sol.md`) found eight divergences; all were reproduced against the Linux oracle, accepted and fixed (section 7).
- **Linux tree memory.** `measure_worker.py` reports the process tree only on Windows; the N100 measurement needs the container's cgroup peak, which includes the child.

## 6. Acceptance

2026-10-03, `CARGO_TARGET_DIR=target/p2-integrate` on Windows and `~/.cache/p2i/target` (gate build) and `~/.cache/p2i/target-check` (check) in WSL, `CARGO_BUILD_JOBS=4`.

| Command | Result |
|---|---|
| `cargo test -p coach-analysis` (Windows) | pass: lib 24, serve 3, decode 10, codec 10, field 1, handoff 2, laps 1, sections 1, table 6, traces 1 |
| the same in WSL (inside `tools/wsl/check.sh all`) | pass, same counts |
| `cargo test --release -p coach-analysis --test kernels_handoff` with `COACH_KERNEL_VECTORS=target/U1/kernel-vectors`, then `target/U3/kv` (Windows) | 112 reader calls identical each |
| `cargo clippy -p coach-analysis --all-targets -- -D warnings`; `cargo fmt -p coach-analysis --check` (Windows) | clean |
| `analysis_parity.py corpus --native-stages decode,kernels` with `COACH_PRIVATE_CORPUS`, Windows (3.12.14) | PASS: 52/52 recordings, 4/4 worker cases |
| the same in WSL (`python3.12` 3.12.3, Linux build), binding | PASS: 52/52 recordings, 4/4 worker cases, same `analysis_id` values |
| `--fixtures-only` in WSL and on Windows (before the handle change) | PASS: 2/2, 4/4 |
| `test_native_stages` with `IRACING_COACH_NATIVE_BIN` (Windows; WSL) | 38 pass, none skipped (the full-parity test now runs) |
| `tools/parity/test_analysis_parity.py` (Windows) | 8 pass |
| `tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>` | exit 0 (1,419 tests: 1,405 passed, 14 skipped) |
| `tools/wsl/check.sh all` (workspace clippy `-D warnings`, workspace tests, Python suite) | passed (Python: 1,419 run, 34 skipped) |
| `measure_worker.py` on the race, `decode` against `decode,kernels`, 5 alternating cold runs | section 4 |
| Codex Sol 6.1 review (D15) | run after this table; resolved in section 7 |

Files changed by the integration: `crates/coach-analysis/src/kernels/{dispatch.rs, session.rs, serve.rs, table/mod.rs}` (the `HANDOFF` entry and `Session::serve`), `crates/coach-analysis/tests/kernels_handoff.rs` (new), `tools/parity/measure_worker.py` (`--baseline-stages`, process-tree accounting), `contracts/worker-interface.md` and `contracts/README.md` (measured boundary-B cost), this file. The seam, the gate and the counters are the earlier integration step's (`native_stages.py`, `workflow.py`, `native_codec.py`, `analysis_parity.py`, `worker_trace.py`) and needed no change. Nothing is committed.

## 7. Review resolution (Codex Sol 6.1)

2026-10-03. The review's eight findings, each reproduced on the Linux oracle before it was accepted, and their fixes and regression vectors: the Disposition table at the end of `reviews/port-phase2-review-codex-sol.md`. In short: merged-store presence in U5's raw columns (1); a `float`/`double` channel mix merges into a `double` column (2); CPython's Unicode digit and space handling in `parse_float`/`parse_int`, from tables generated from the interpreter (3); flag views as each int's low 64 bits with the exact ints where they leave the kernel, and `BigInt` tire compound and service flags (4); the seam requires `accessed` and exactly one outcome (5); nonpositive trace bins (6); CPython's `isprintable` table for `repr` (7); `_service_events` returns before its windows when nothing was hit (8). The port limits that remain are listed under the table.

New vectors, recorded on both platforms (identical apart from the interpreter fields): five synthetic tables under `tests/vectors/kernels/<platform>/` (`review-big-ints`, `review-unicode-text`, `review-huge-rate`, `review-field-merged`, `review-track-id`, from `tools/review_vectors.py`, every unit's calls plus seven direct calls through the new `EXTRA_CALLS` hook), two native-merge cases (`mixed-width`, `mixed-width-double-first`), and two `coach-numerics` families (`common/parse_unicode.json` 999 cases, `common/int_low64.json` 621 cases; the earlier families keep their seeds and files). The views probe adds `flags_low64` when a flag is beyond `i64` (only `table-setup-units` has one). `emit_kernel_vectors.py` also stopped writing the probe name into `index.json` as the recording name (a shadowed loop variable); eight table `index.json` files were corrected.

`coach-numerics` additions: `unicode` (`decimal_digit`, `is_space`, `is_printable`; `unicode_tables.rs` generated by `tools/gen_unicode.py`), `int_from_float_low64`. `numerics.md` section 5's line on non-ASCII digits no longer holds.

Run on DESKTOP-JOSHUA, `CARGO_TARGET_DIR=target/p2-resolve` on Windows, `~/.cache/p2r/target` in WSL, `CARGO_BUILD_JOBS=4`; gate binaries copied aside (Windows SHA-256 `feb4f1f3cf2afe87`, Linux `a062e5b5a67c2ad5`).

| Command | Result |
|---|---|
| `cargo test -p coach-numerics -p coach-analysis` (Windows; WSL) | pass both: numerics lib 9, vectors 13 (with `parse_unicode`, `int_low64`), memory 1; analysis lib 24, serve 3, decode 10, codec 10, field 1, handoff 2, laps 1, sections 1, table 6, traces 1 |
| mutation check: each of fixes 1, 4, 6, 7, 8 reverted alone | the review vectors fail (field, table, traces, laps replays) |
| `cargo clippy -p coach-analysis -p coach-numerics --all-targets -- -D warnings` (Windows; WSL); `cargo fmt --check` | clean |
| `gen_unicode.py --check`, `gen_vectors.py --check` (3.12.14 Windows; 3.12.3 Linux) | identical; 13 of 13 files identical |
| `emit_kernel_vectors.py --fixtures --check` (both) | no file differs or is missing (933 `new`: units the per-unit synthetic folders were never recorded with) |
| `table_vectors.py merge --check` (both) | identical |
| `test_native_stages` with `IRACING_COACH_NATIVE_BIN` (Windows; WSL) | 38 pass |
| `test_analysis_parity.py` with `IRACING_COACH_NATIVE_BIN` (Windows; WSL) | 8 pass |
| `analysis_parity.py corpus --native-stages decode,kernels`, WSL (binding) | PASS: 52/52 recordings, 4/4 worker cases; `analysis_id` values unchanged (`9f4f93d4315bc43c14ebf2e7`, `18bb89fd9d2ec273dff77d87`, `3fbea207efcae8cd78eb0517`) |
| the same on Windows | PASS: 52/52, 4/4, same `analysis_id` values |
| `measure_worker.py` on the race, `decode` against `decode,kernels`, 5 alternating cold runs, machine idle | wall 9.74 s (9.52 to 10.22) to 5.71 s (5.59 to 6.53), -41 %; worker peak 2.296 to 0.229 GiB; tree peak commit 2.310 to 0.691 GiB; 2 and 3 processes; `analysis_id` equal; decoder alone 0.21 s, 133.6 MiB. Section 4's figures hold: the fixes cost nothing measurable |

Nothing is committed.
