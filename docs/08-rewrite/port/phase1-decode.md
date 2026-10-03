# Port phase 1: IBT decode

Date 2026-10-03. Authority: owner decision D2 (staged Rust port, byte-identical output against the Python oracle) and `architecture.md` section 12, which takes decode before the kernels because the kernels consume decoded columns. Interface: `contracts/worker-interface.md` sections 7 and 8.

## 1. Result

- The decode stage is native: `crates/coach-analysis` decodes a recording's selected channels and writes a coach column store; with `IRACING_COACH_NATIVE_STAGES=decode` the worker reads that store instead of decoding, and hands the analysis exactly the table `ibt_reader.load_telemetry` returns.
- Parity: byte-identical on every recording available: the two fixtures and 50 unique private recordings (64 files, 14 duplicates by SHA-256; every race, practice and test file on the dev PC), at native rate and 20 Hz for the decoded-matrix golden and in raw and parsed mode for the complete analysis, plus the four worker-interface cases; the same on Linux for the fixtures and worker cases. No numeric difference occurred, so nothing needed explaining.
- Cost on the dev PC, 203 MB New Hampshire race: worker wall time 11.56 s with Python decode and 10.72 s with native decode (median of 5 alternating cold runs, -7 %); peak working set 2.294 and 2.295 GiB (unchanged); the decoder process alone 0.22 s and 133.5 MiB.
- Phase 1 alone does not move the worker's peak memory: the decode lists it replaces are rebuilt by the Python seam, because `TelemetryTable` and everything after it still take Python lists. The memory win needs the phase-2 changes in section 5.

## 2. Design

### What the stage is

`ibt_reader.load_telemetry(path, channels, target_hz)` per recording: select variables, apply the sampling plan, decode every selected variable of every chosen record. Everything else in `workflow._load_analysis_telemetry` stays Python: `scan_ibt` (header, session-info YAML, variable catalogue, summary; about 13 ms on the 203 MB race), the finalization and file-signature checks, `source_catalogs`, `sample_provenance`, the multi-file merge, then `TelemetryTable`, normalisation and the kernels. The stage computes no derived or normalised column: `_normalize_column`, `_apply_steps`, `_fraction_column`, `_bool_column` and the cached column views live in `analysis_engine.py`, part of the analyzer bundle (a change there changes `analysis_id`), and are phase-2 kernels (`architecture.md` section 12).

### Hand-off

`workflow._load_analysis_telemetry` reads `IRACING_COACH_NATIVE_STAGES` once per job through `native_stages.requested_stages()` and calls `native_stages.load_telemetry` instead of `ibt_reader.load_telemetry` when it names `decode` (two import lines and seven lines in `workflow.py`; `workflow.py` and the new `native_stages.py` are outside the analyzer bundle, so `analysis_id` is unchanged). `native_stages.load_telemetry` scans the recording in Python, starts `coach-analysis decode` with a JSON request on standard input, reads the store from a temporary directory, checks the store's source layout and columns against its own scan, and builds the reader's mapping key for key. An unknown stage name, a stage this build does not provide (`kernels`, `json_writer`), a missing executable, a failed decode or an inconsistent store fails the job with `NativeStageError`; nothing falls back to Python.

The contract named a PyO3 module (`coach_native`) for every phase and left the column format open. Phase 1 uses an executable and a file instead, recorded in `worker-interface.md` section 7 through the contract change log:

- the decoder's memory (about 134 MiB on the race) is released before Python builds its lists, instead of adding to the worker's peak;
- the executable links no Python ABI, so the image needs no Python headers or matching interpreter at build time, and Windows and Linux builds are plain `cargo build`;
- coach-server can write the same store at ingest later, and phase-2 kernels read it directly.

PyO3 remains the plan for phase 2, where boundary B must return Python objects in process. `architecture.md` section 12 still describes phase 1 as PyO3; its owner should align that sentence with the contract.

### Shared parsing

`coach_ibt::IbtFile::open` supplies the header, the disk subheader, the variable table and every bounds check (the port of `_read_layout`, already parity-tested against Python in `crates/coach-ibt/tests/parity.rs`); `coach_ibt::decode_session_info` supplies the cp1252 fallback for char channels. `coach-analysis` parses nothing itself: it selects variables (`_normalise_channels`, same error text), applies the sampling plan and gathers each selected variable's bytes from the chosen records in 8 MiB blocks. A malformed recording is rejected by Python's own `scan_ibt` before the decoder runs, so the worker's error for a bad file is the oracle's (the gate checks it on `truncated-race.ibt`).

### Column interface

Per SDK type (`worker-interface.md` section 7 has the full table): bool as one byte 0/1 (any nonzero source byte, as `struct` `?`), int as i32, bitfield as u32, float as f32 and double as f64 with their stored bits (NaN payloads, signalling NaNs and infinities kept), char as UTF-8 text with offsets (bytes before the first NUL; UTF-8 keeping a BOM, else cp1252 with `errors="replace"`). Array variables hold `count` elements per sample, sample-major; the seam slices them into per-sample lists. There are no missing values at this stage; `None` first appears in the multi-file merge and in normalisation. `sample_indices` are the native record indices, one per sample. The Python seam turns each column into values with `memoryview.cast(...).tolist()` (the same conversion the reader's typed-view path uses), `map(bool, ...)` for bools and `bytes.decode("utf-8")` for text.

### Coach column store

Format version 1, `.ccs`: magic `COACHCS\0`, u32 version, u32 header length, a JSON header (source layout, sampling, channel mode, the sample-index block and one entry per column with its type, count, row offset, element and data block), then 8-byte-aligned blocks. Readers reject another magic or version, an unknown element, a block outside the file, and a column that does not match the scanned variable; unknown header fields are ignored. Rust: `coach_analysis::store` (writer and `ColumnStore` reader); Python: `native_stages.read_store`.

### Float semantics

The stage's only arithmetic is the sampling plan: `step = tick_rate / target` (one IEEE division of two exact doubles) and `position += step` with `int(position)` truncation. Rust `f64` does the same operations, so no compensated sum, `fsum` or `repr` is needed and `crates/coach-numerics` is not used (it was not present when this phase was built; nothing here needs consolidating into it). `crates/coach-analysis/tests/vectors/sampling.json` holds 510 cases produced by the oracle's own `_sampling_plan` and `_iter_sample_indices` with CPython 3.12.14 (five tick rates, seventeen targets including `30.000000000000004`, `59.99999999999999`, `1e-05`, zero and negative, six record counts up to 216,001), compared bit for bit on the step and output rate and by SHA-256 on the indices. The decode golden's matrix hash re-packs floats as Python's `struct.pack("<f", float(x))` does, which quiets a signalling NaN; the store itself keeps the stored bits.

### Files

| Path | Content |
|---|---|
| `crates/coach-analysis/src/decode.rs` | selection, gather, column types |
| `crates/coach-analysis/src/sampling.rs` | the sampling plan |
| `crates/coach-analysis/src/store.rs` | coach column store v1 writer and reader |
| `crates/coach-analysis/src/request.rs` | the stdin request of `coach-analysis decode` |
| `crates/coach-analysis/src/golden.rs` | the `emit_ibt_golden.py` fields from a native decode |
| `crates/coach-analysis/src/main.rs` | `coach-analysis decode`, `golden`, `inspect` |
| `crates/coach-analysis/tests/decode.rs`, `tests/vectors/sampling.json`, `tools/emit_sampling_vectors.py` | fixture golden, edge types against the oracle, store round trip, sampling vectors |
| `iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py`, `workflow.py` | the seam |
| `iracing-coach/tests/test_native_stages.py` | stage selection, store reader, seam, and (with the executable) native against Python on every type, rate and selection |
| `tools/parity/analysis_parity.py` (`corpus`, `--native-bin`), `emit_ibt_golden.py` (batched), `measure_worker.py` | the gate and the measurements |

## 3. Parity

Command (Windows, bundled Python 3.12.14, release build of `coach-analysis`):

```powershell
$env:COACH_PRIVATE_CORPUS = '<scratch copies of Documents\iRacing\telemetry and the raw archive>'
& $py tools\parity\analysis_parity.py corpus --native-stages decode --native-bin target\port-decode\release\coach-analysis.exe --out target\port-decode\gp
```

For every recording: the decode golden (`emit_ibt_golden.emit`, all channels, at native rate and 20 Hz) against `coach-analysis golden` on every field (sample indices, variables with offsets and units, rates, counts, `matrix_sha256`); one cold `analyze_race_workflow` capture with no native stage and one with `decode`, compared in raw mode (persisted bytes after the documented allowlist: `*_at` instants, the report-directory timestamp slug, `*_ms` and `elapsed_seconds`, the timestamp-derived `analysis_sha256`, the root prefixes) and in parsed mode (including the engine's in-memory output in insertion order); and a check that the native capture's own stage timings contain `native_stages.load_telemetry` and the oracle's do not. The fixtures add the four worker-interface cases (`synthetic`, `reconnect` with two overlapping files, `restaged`, `local-file`) driven through `mcp_server.py`, compared raw and parsed with equal `analysis_id`. A recording both sides reject must fail with the same exception line.

Run 2026-10-03 on DESKTOP-JOSHUA, `coach-analysis` release build SHA-256 `8ee8396c58103cf4`, 18 min 30 s for the whole corpus. `=` is identical on every golden field; analysis times are the workflow wall times of the two output runs while other jobs shared the machine, so they rank, they do not measure (section 4 measures). Every native capture's timings contain `native_stages.load_telemetry` and no oracle capture's do. The final build (SHA-256 `5a4e758eeb707914`, after clippy-only changes in `golden.rs` and `store.rs`) was re-run on the fixtures, the worker cases and recordings 8, 9 and 50: identical. Recordings are the copies in the dev-PC scratch folder, never committed; rows 3 to 17 come from the old app's raw archive, the rest from `Documents\iRacing\telemetry`.

| # | Kind | Recording | MB | SHA-256 | Golden native/20 Hz | Analysis raw / parsed | Workflow s, oracle / native |
|---|---|---|---|---|---|---|---|
| 1 | fixture | synthetic-race.ibt | 0.0 | `540f7208f1bd` | =/= | identical / identical | 0.17 / 0.18 |
| 2 | fixture | truncated-race.ibt | 0.0 | `cd7f4eb5c396` | =/= | both reject: IbtTruncatedError | - |
| 3 | private | Supra, New Hampshire, 2026-07-27 22:09:39 | 147.0 | `02b00a3cca29` | =/= | identical / identical | 8.29 / 7.94 |
| 4 | private | Supra, New Hampshire, 2026-08-01 20:09:37 | 128.6 | `0c749e369bf8` | =/= | identical / identical | 7.07 / 6.52 |
| 5 | private | Tundra, Kentucky, 2026-07-31 22:24:51 | 40.8 | `13bf212d4b49` | =/= | identical / identical | 2.82 / 2.71 |
| 6 | private | Supra, New Hampshire, 2026-08-01 21:09:00 | 202.5 | `447f6f61eb16` | =/= | identical / identical | 14.80 / 14.19 |
| 7 | private | Supra, New Hampshire, 2026-07-27 20:09:55 | 125.2 | `4f98b65998d0` | =/= | identical / identical | 7.28 / 6.54 |
| 8 | private | Supra, New Hampshire, 2026-08-01 15:09:02 | 203.2 | `582b11e2e65b` | =/= | identical / identical | 12.78 / 12.16 |
| 9 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `60752f797021` | =/= | identical / identical | 8.89 / 8.18 |
| 10 | private | Supra, Daytona, 2026-08-01 18:39:25 | 75.3 | `60d1ecc277fd` | =/= | identical / identical | 5.06 / 5.94 |
| 11 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `7ac18c9c8fed` | =/= | identical / identical | 14.89 / 12.80 |
| 12 | private | Tundra, Kentucky, 2026-08-02 22:24:10 | 144.4 | `7fe60f3aedcf` | =/= | identical / identical | 9.95 / 9.79 |
| 13 | private | Supra, New Hampshire, 2026-07-28 21:09:03 | 198.5 | `8f76c042b43f` | =/= | identical / identical | 15.26 / 13.28 |
| 14 | private | Taurus, Indianapolis, 2026-07-29 21:39:27 | 213.9 | `96a350822126` | =/= | identical / identical | 12.62 / 11.57 |
| 15 | private | Tundra, Kentucky, 2026-07-28 22:23:24 | 128.5 | `a58773367610` | =/= | identical / identical | 7.71 / 7.10 |
| 16 | private | Supra, New Hampshire, 2026-07-31 21:09:04 | 208.9 | `c5c7c4da6780` | =/= | identical / identical | 14.90 / 14.95 |
| 17 | private | Supra, New Hampshire, 2026-08-01 14:09:38 | 124.9 | `f60cbb8ce5c7` | =/= | identical / identical | 6.22 / 5.94 |
| 18 | private | Taurus, Indianapolis, 2026-07-27 23:19:28 | 50.2 | `3192d474fc6a` | =/= | identical / identical | 3.48 / 3.41 |
| 19 | private | Taurus, Indianapolis, 2026-07-27 23:32:33 | 0.3 | `bc3abab2e0cb` | =/= | identical / identical | 0.23 / 0.24 |
| 20 | private | Taurus, Indianapolis, 2026-07-29 21:19:55 | 43.4 | `7263ba84511f` | =/= | identical / identical | 2.65 / 2.46 |
| 21 | private | Taurus, Indianapolis, 2026-07-29 21:35:58 | 11.8 | `054cf3dfe295` | =/= | identical / identical | 1.07 / 1.05 |
| 22 | private | Supra, Daytona, 2026-08-01 18:27:08 | 16.0 | `c95f9b053722` | =/= | identical / identical | 1.27 / 1.28 |
| 23 | private | Supra, Daytona, 2026-08-01 18:32:17 | 6.6 | `4bcef1fd2be5` | =/= | identical / identical | 0.68 / 0.72 |
| 24 | private | Supra, Daytona, 2026-08-01 18:34:17 | 11.6 | `cfd28589b7af` | =/= | identical / identical | 1.06 / 1.03 |
| 25 | private | Supra, Iowa, 2026-08-03 20:02:24 | 8.0 | `6f404cd4ac46` | =/= | identical / identical | 0.89 / 0.94 |
| 26 | private | Supra, Iowa, 2026-08-03 20:04:59 | 7.2 | `cd2b3c20fb0d` | =/= | identical / identical | 0.86 / 0.84 |
| 27 | private | Supra, Iowa, 2026-08-03 20:59:41 | 13.3 | `22a20e50dcff` | =/= | identical / identical | 1.07 / 1.09 |
| 28 | private | Supra, Iowa, 2026-08-03 22:27:43 | 43.9 | `265b83f2d863` | =/= | identical / identical | 3.73 / 3.40 |
| 29 | private | Supra, New Hampshire, 2026-07-27 22:01:53 | 8.9 | `3583870bf3b9` | =/= | identical / identical | 0.84 / 0.86 |
| 30 | private | Supra, New Hampshire, 2026-07-28 21:03:59 | 7.0 | `e56b513e0f9e` | =/= | identical / identical | 0.78 / 0.80 |
| 31 | private | Supra, New Hampshire, 2026-07-31 20:42:15 | 23.9 | `525e1591f28b` | =/= | identical / identical | 1.42 / 1.28 |
| 32 | private | Supra, New Hampshire, 2026-07-31 20:48:41 | 46.5 | `719244c4d68c` | =/= | identical / identical | 3.73 / 3.47 |
| 33 | private | Supra, New Hampshire, 2026-07-31 21:01:27 | 8.4 | `4f53d89d5934` | =/= | identical / identical | 0.89 / 0.87 |
| 34 | private | Supra, New Hampshire, 2026-07-31 21:03:56 | 6.0 | `99d54b6f018a` | =/= | identical / identical | 0.75 / 0.75 |
| 35 | private | Supra, New Hampshire, 2026-08-01 13:44:55 | 65.7 | `c4ebf0253f37` | =/= | identical / identical | 4.50 / 4.31 |
| 36 | private | Supra, New Hampshire, 2026-08-01 14:04:32 | 9.9 | `dfb778364979` | =/= | identical / identical | 0.95 / 0.98 |
| 37 | private | Supra, New Hampshire, 2026-08-01 15:01:30 | 7.9 | `493746997a6f` | =/= | identical / identical | 0.87 / 0.87 |
| 38 | private | Supra, New Hampshire, 2026-08-01 15:03:46 | 9.9 | `527f9457af8b` | =/= | identical / identical | 1.03 / 1.04 |
| 39 | private | Supra, New Hampshire, 2026-08-01 20:00:04 | 7.4 | `ef4b55866932` | =/= | identical / identical | 0.82 / 0.84 |
| 40 | private | Supra, New Hampshire, 2026-08-01 20:04:37 | 8.2 | `a8d039e18be7` | =/= | identical / identical | 0.86 / 0.93 |
| 41 | private | Supra, New Hampshire, 2026-08-01 21:03:47 | 0.3 | `a39fbaeae9b6` | =/= | identical / identical | 0.21 / 0.22 |
| 42 | private | Supra, New Hampshire, 2026-08-01 21:04:07 | 7.3 | `7a6fe0798e69` | =/= | identical / identical | 0.83 / 0.92 |
| 43 | private | Supra, New Hampshire, 2026-08-02 13:30:23 | 1.4 | `eee4ac362623` | =/= | identical / identical | 0.20 / 0.23 |
| 44 | private | Tundra, Kentucky, 2026-07-28 22:12:05 | 15.1 | `7cf70fc05da7` | =/= | identical / identical | 1.35 / 1.23 |
| 45 | private | Tundra, Kentucky, 2026-07-28 22:16:00 | 0.3 | `2a3291236467` | =/= | identical / identical | 0.21 / 0.20 |
| 46 | private | Tundra, Kentucky, 2026-07-28 22:16:38 | 9.2 | `b8dc17e34d78` | =/= | identical / identical | 0.89 / 0.93 |
| 47 | private | Tundra, Kentucky, 2026-07-28 22:19:14 | 7.6 | `1e4154bd3171` | =/= | identical / identical | 0.78 / 0.84 |
| 48 | private | Tundra, Kentucky, 2026-07-31 22:16:21 | 9.3 | `ef442f0c8075` | =/= | identical / identical | 0.94 / 0.95 |
| 49 | private | Tundra, Kentucky, 2026-07-31 22:19:03 | 13.6 | `ab6188605c0c` | =/= | identical / identical | 1.23 / 1.18 |
| 50 | private | Tundra, Kentucky, 2026-08-02 22:14:59 | 3.3 | `836e9e87f431` | =/= | identical / identical | 0.28 / 0.32 |
| 51 | private | Tundra, Kentucky, 2026-08-02 22:16:32 | 8.7 | `af651dad2f80` | =/= | identical / identical | 0.98 / 0.91 |
| 52 | private | Tundra, Kentucky, 2026-08-02 22:19:00 | 7.5 | `8fe7216a37df` | =/= | identical / identical | 0.82 / 0.85 |

Worker-interface cases (fixtures, through `mcp_server.py`, analyzer digest unpinned):

| Case | Raw and parsed | `analysis_id` | analyze ms, oracle / native |
|---|---|---|---|
| synthetic | identical | `9f4f93d4315bc43c14ebf2e7` | 364.3 / 379.6 |
| reconnect | identical | `18bb89fd9d2ec273dff77d87` | 418.5 / 441.0 |
| restaged | identical | `9f4f93d4315bc43c14ebf2e7` | 371.9 / 413.2 |
| local-file | identical | `3fbea207efcae8cd78eb0517` | 379.7 / 397.4 |

Linux: WSL Ubuntu, Python 3.12.3, Linux release build: `corpus --fixtures-only` identical for both fixtures and the four worker cases, with the same `analysis_id` values as Windows; `test_native_stages` 11 tests with the executable, none skipped. The private corpus was run on Windows only.

## 4. Measurements (dev PC)

DESKTOP-JOSHUA (i5-11400, Windows 11, bundled Python 3.12.14), `tools/parity/measure_worker.py`: the production worker protocol (complete environment, job root of hard links, discovered group id, `target_hz: null`), one cold process per run in a fresh archive, Python and native runs alternating, page cache warm. Peak is the worker process's peak working set; the decoder child's peak is measured alone on the same request. The N100 numbers come from the deploy step.

Recording: the 203,246,134-byte New Hampshire race (`582b11e2`), 189,857 records, 167 of 415 analysis channels present. CPU load from other work on the machine was 39 % before and 29 % after the runs.

| | Python decode | Native decode |
|---|---|---|
| Worker wall, median of 5 (range) | 11.56 s (11.39 to 11.64) | 10.72 s (10.66 to 11.27) |
| Worker peak working set, median (range) | 2.294 GiB (2.294 to 2.296) | 2.295 GiB (2.295 to 2.296) |
| Decoder process alone, median of 5 | | 0.22 s, 133.5 MiB peak; decode 0.15 s, store write 0.05 s, store 126,846,168 bytes |
| `analysis_id` | `31419c583d3959b352c3a86c` | `31419c583d3959b352c3a86c` |

An earlier set of 3 runs each, under 52 % background load, gave medians 11.91 and 11.20 s and the same peaks. The peak does not move because it is set later, in the kernels, by the lists section 5 lists; the native decoder's own peak never coincides with it (the decoder exits before the worker builds its lists). The time saved is the decode step itself (next paragraph); the rest of the worker is unchanged Python. The P1 profile therefore still holds for the N100: wall time about 13 s less roughly 0.8 s, peak unchanged until phase 2.

The decode step inside the worker (`load_telemetry` for the 167 analysis channels at native rate, same process, warm): Python 1.42 to 1.63 s, native 0.66 to 0.67 s, of which the decoder process takes 0.23 to 0.27 s (decode 0.15 s, writing the 127 MB store 0.05 s) and the seam's `read_store` 0.44 s (building 31.7 million Python values).

## 5. Python sites that still materialise lists

The P1 profile attributes the 2.3 GiB peak almost entirely to per-sample Python lists. After phase 1, the sites are:

| Site | What it holds | Needed change (phase) |
|---|---|---|
| `native_stages.read_store` (was `ibt_reader._strided_scalar_column`) | one Python value per sample and channel, the decode lists (840 MB in the P1 snapshot, mostly the float objects themselves) | hand `TelemetryTable` typed buffers instead of lists (phase 2: needs `TelemetryTable` to accept them, an analyzer-bundle change) |
| `workflow._load_analysis_telemetry`, `sample_provenance` | one dict per sample (189,857 on the race) | keep as indices plus a file table, or build it natively; it is written into the analysis, so the JSON must not change (phase 2 or 3) |
| `workflow._load_analysis_telemetry`, multi-file merge | a merged list per channel and merged provenance while every per-file table is alive (why section 6 admits groups at 1.5x) | merge natively in the store by `SessionTime` with the same duplicate rule (phase 2) |
| `TelemetryTable.__init__` | `list(values)` per channel (254 MB in P1) | read store columns; no copy (phase 2, analyzer bundle) |
| `_normalize_column`, `_finite_values` | `list(map(float, ...))` per channel read (225 MB in P1) | native finite/complete columns (phase 2 kernel) |
| `_apply_steps`, `_fraction_column`, `_wheel_ratio_column`, `_row_means`, `_bool_column`, `_distinct_map` | derived float lists (421 MB `_apply_steps`, 80 MB `_row_means` in P1) | native kernels with Python's exact float semantics: `fsum` in `_row_means`, operation order in `_apply_steps` (phase 2) |

## 6. What phase 2 needs from this stage

- The store as the kernels' input: every selected channel at its SDK width with stored bits, sample indices, and the source layout; map it, never convert Python lists into Rust.
- Normalisation semantics to reproduce exactly from these raw values: `float()` widening of f32, `_finite` (non-finite to `None`, bool and int to float), the `_isfinite(sum(values, 0.0))` fast path being only a shortcut, bool columns as `bool(byte)`, flags as `int(_finite(v) or 0)`.
- The multi-file merge moved into the store (one merged store per group with per-sample source file and record index), so `sample_provenance` and the merged columns come from Rust; until then a group is decoded file by file as now.
- `TelemetryTable` taking columns from the store (analyzer-bundle change, so a deliberate `analysis_id` change recorded through `test_analysis_pipeline_identity.py`), which is where phase 1's memory benefit is realised.
- The image: `coach-analysis` built for `x86_64-unknown-linux-gnu`, installed at `/usr/local/bin/coach-analysis`, `IRACING_COACH_NATIVE_BIN` set when the stage is switched on; the server's orphan sweep for `coach-decode-*` (`worker-interface.md` section 6); the N100 peak and wall time re-measured before the admission formula changes.

## 7. Acceptance

| Command | Result |
|---|---|
| `cargo test -p coach-analysis` (Windows) | 12 passed (3 unit, 9 integration, the edge-type oracle comparison running the bundled Python) |
| `cargo clippy -p coach-analysis --all-targets -- -D warnings` | clean |
| `analysis_parity.py corpus --native-stages decode` with `COACH_PRIVATE_CORPUS` | PASS: 52/52 recordings, 4/4 worker cases |
| `analysis_parity.py corpus --fixtures-only` in WSL (Linux build, python3.12) | PASS: 2/2 recordings, 4/4 worker cases |
| `tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>` | exit 0; 1,383 passed, 0 failed, 9 skipped (the two executable-dependent `test_native_stages` cases skip without `IRACING_COACH_NATIVE_BIN`) |
| `test_native_stages` with `IRACING_COACH_NATIVE_BIN` (Windows and Linux) | 11 passed |
| `wsl -d Ubuntu -- bash tools/wsl/check.sh rust` (workspace clippy and tests, `coach-analysis` included) | WSL check passed |

Not done here: the N100 measurement and the image change (deploy step), the Codex Sol review of the contract change (D15), and the `architecture.md` section 12 sentence that still names PyO3 for phase 1.
