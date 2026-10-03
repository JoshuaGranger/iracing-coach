# Port phase 3: the analysis.json writer and the mapped store

Date 2026-10-03. Authority: owner decision D2 and its ruling (port the stages the profile shows dominate, JSON writing third, each gated on byte-identical output; an unexplained difference is a failure). Interface: `contracts/worker-interface.md` sections 7 (boundary D, column store alignment) and 8 (writer rule), change-log entry "port phase 3" in `contracts/README.md`. Builds on `phase1-decode.md`, `phase2-kernels.md` (section 5 lists what this phase needed) and `numerics.md`.

## 1. Result

- **Writer.** With `IRACING_COACH_NATIVE_STAGES` naming `writer`, the three serialisations of the analysis object (`analysis.json`, the analysis-cache file, the cache's `analysis_sha256`) are written by `coach-analysis write`, which reproduces CPython 3.12's `json.dumps` byte for byte from a pickle of the object (section 2.1). Parity: `analysis_parity.py corpus --native-stages decode,kernels,writer` passes on Linux (WSL, the binding oracle) and Windows: both fixtures and all 50 unique private recordings identical in raw and parsed mode, the truncated fixture rejected identically, the four worker-interface cases identical with the phase-1 and phase-2 `analysis_id` values; every analysis serialisation written natively (3 per analysis, site for site), no Python fallback, and the envelope hash equal to Python's on every capture. Also 926 committed oracle vectors (938 since the review) and 50,000 random stress cases per platform. The Codex Sol 6.1 review's ten findings were all accepted and fixed (section 7); the WSL corpus gate passed again on the fixed build.
- **Mapped store.** The decode and merge stages write page-aligned columns and the kernels child maps the store instead of reading it (section 2.2): on the race the process tree's peak commit drops from 0.690 to 0.572 GiB (-17 %) and the kernels child from 0.635 to 0.517 GiB (the 127 MB store), wall time unchanged.
- **Cost** (dev PC, 5 alternating cold worker runs, medians, `decode,kernels` against `decode,kernels,writer`): 203 MB race 5.64 s to 5.06 s (-10 %), 214 MB Indianapolis race (the largest recording on this PC; the 2 GB Kansas recording is not on it) 4.70 s to 4.33 s (-8 %); worker peak working set 0.229 to 0.220 GiB and 0.165 to 0.163 GiB; tree peak commit unchanged at 0.572 and 0.553 GiB, because the kernels child sets it and the writer child (0.16 GiB commit) runs after it exits. Per serialisation of the race's analysis: `analysis.json` 0.26 s against 0.44 s, the hash 0.23 s against 0.40 s.
- **What is left in Python** (section 5): 0.69 s of the race's 4.30 s in-process workflow (16 %), of which 0.18 s is the recording's SHA-256 (C code) and 0.32 s per-sample Python in damage, identity, investigation and runs; 61 % of the time is the kernels hand-off. A phase 4 that ports the per-sample Python remainder saves about 0.3 s and 40 MB of transient Python allocation per race; the kernels hand-off and the kernels child's 0.5 GiB are the larger targets.

## 2. Design

### 2.1 The writer (boundary D)

The worker serialises its analysis object three times per analysis, each time with `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ...)` over the whole object (24.8 MB on the 203 MB race, 94 % of it `lap_traces`). With `IRACING_COACH_NATIVE_STAGES` naming `writer`, each of these sites hands the object to `coach-analysis write` instead (`native_stages.writer_call`):

| Site (`native_stages.WRITER_SITES`) | Python replaced | `ensure_ascii`, `allow_nan`, terminator | Returns |
|---|---|---|---|
| `analysis_json`: `storage._analysis_json_bytes`, the `analysis.json` payload of `save_report_artifacts` | `_json_text(dict(analysis)).encode("utf-8")` | false, false, `\n` | the bytes; `save_report_artifacts` stages and replaces files as before |
| `analysis_sha256`: `workflow._analysis_sha256`, the analysis-cache envelope's hash (and the cache-hit check) | `stable_hash(analysis, 64)` | true, true, none | the hex SHA-256 of the text |
| `json_cache`: `workflow._write_json_cache` (analysis cache; profile and event caches use it too) | `Path.write_text(json.dumps(...) + "\n", encoding="utf-8")` | false, false, `os.linesep` (the text-mode translation of the one newline) | none: the child writes the temporary file the site then renames |

The other `json.dumps` calls of a workflow (manifests, the history index, small report files) are each under 10 ms on the race and stay Python. The stage is independent of `decode` and `kernels` (it also serialises a cached analysis). No analyzer-bundle file changes (`workflow.py`, `storage.py` and `native_stages.py` are outside it), so `analysis_id` is unchanged; the server's analyze idempotency key changes with any `*.py` change, as intended.

**Hand-off: a pickle.** Python has to give Rust the object, and walking it in Python costs what `json.dumps` costs; the C pickler walks it in C without formatting a float. Measured on the race's analysis (dev PC, bundled CPython 3.12.14, best of 3): `_json_text` 0.527 s (24.8 MB), the `stable_hash` text 0.443 s, `pickle.dumps` protocol 5 0.056 s (15.6 MB, every shared key string once through the memo), with the seam's `reducer_override` 0.070 s, `marshal` 0.036 s (rejected: no hook for subclasses and other objects, and its format is private to an interpreter version). Exact `dict`, `list`, `tuple`, `str`, `bytes`, `int`, `float`, `bool` and `None` take the pickler's C fast paths, which run no Python code; anything else (a subclass, a `Path`, an enum, a class or function, an object `default=str` would stringify) reaches `_WriterPickler.reducer_override`, which stops the pickle without calling anything of the object, and the site's own Python code writes the whole call (a counted fallback, below). Before the review it pickled a reference to `native_stages._writer_marker` with the text `json.dumps` writes for that object, evaluated once in the pickler's insertion order: wrong when `default=str` or a subclass's methods have side effects, which `json.dumps` calls in sorted traversal order, once per occurrence (review findings 1 and 4). The request format and the reader still carry the marker; the seam sends none. On the race no object reaches `reducer_override`. The Rust reader (`writer/pickle.rs`) accepts data opcodes only and the one global the request names; anything else is a protocol failure (exit 1, `NativeStageError`).

**What the encoder reproduces** (`writer/json.rs`, CPython 3.12 `Modules/_json.c` in its order): `null`/`true`/`false`; `int` repr, beyond `i64` from the pickle's two's complement bytes (`coach_numerics::int_repr_from_le_bytes`) under `sys.get_int_max_str_digits()` with CPython's `ValueError`; `float.__repr__`, `NaN`/`Infinity`/`-Infinity` or `ValueError: Out of range float values are not JSON compliant: <repr>`; strings for both `ensure_ascii` settings (named escapes, lowercase `\u00XX` controls, with `ensure_ascii` every non-ASCII character as `\uXXXX` and astral ones as a surrogate pair, lone surrogates as themselves); `[]`/`{}` before the circular-reference check, `ValueError: Circular reference detected` where the encoder's markers find a cycle (the pickle memo keeps object identity, so a shared object is written twice and a cycle is a cycle); dict items in `list.sort` order of `(key, value)` tuples, which for distinct keys is the key order, with CPython's comparison sequence for mixed keys (`coach_numerics::sort_permutation`: the order of NaN keys and the first `TypeError: '<' not supported between instances of 'A' and 'B'`), keys converted after sorting (`str` as is, `float` as above, `true`/`false`/`null`, `int` repr); `repr(bytes)` for `default=str`; and the UTF-8 encoding of the text: a lone surrogate is `UnicodeEncodeError` with CPython's start, end and characters (for the cache file the seam first creates the empty file, as `write_text` does; the exception's `object` holds only the surrogates at their positions, the one attribute that differs).

**Outside the model, written by the oracle.** Any object outside the exact types above (stopped in the seam before the writer starts), a set, a frozenset or a bytearray (their `str` needs `repr` of members in hash order), a dict key that is not exactly `str`, `int` within `i64`, `float`, `bool` or `None` (a key type with its own comparison), containers nested more than 200 deep (the interpreter's recursion limit decides), or a pickling `RecursionError`: the writer answers `fallback`, and the site runs its own Python code, so the result is Python's by construction. `native_stages.writer_fallback` counts these calls; the gate fails on any. None occurs in the corpus.

**Speed.** The first version took 0.57 s in the child on the race, slower than `json.dumps`: `coach_numerics::float_repr` costs about 280 ns a float (an allocated `format!` and a `u128` tie check with two `to_string` calls) and the race has 1,064,859 floats. Three changes, none altering a byte: `coach_numerics::float_repr_into` (additive) writes Rust's shortest digits from a stack buffer and calls the same tie check only when a `u64` test cannot rule a tie out (about 90 ns a float; `numerics.md` section 9); the sorted key order is cached by the key objects' identity (the 160 bins of every lap repeat a few key sets); lists of at least 8 items within 3 levels of the root of a graph of at least 200,000 nodes are encoded by up to 4 threads, each a contiguous run into its own buffer, the buffers joined in order and the first stop in order returned (the sequential result: each run meets its stops in traversal order; the threads start with the markers of the path above the list). On the race: parse 0.033 s, encode 0.100 s (was 0.42 s), for 1,140,094 nodes.

### 2.2 The mapped store

`phase2-kernels.md` section 4 found the kernels child reading the whole store (`ColumnStore::read` was `fs::read`, then the data section was copied once more by `parse`). Now:

- The decode and merge stages pad the store header with JSON whitespace so the data section starts on a 4096-byte boundary of the file, and start every block on such a boundary (`alignment: 4096` in the header, an optional field; format version 1 unchanged; every reader accepts any multiple of 8, so older stores and the vector stores the Python tools write still read). A kernel that touches a column faults in that column's pages and no neighbour's. Padding costs at most a page per block (a few hundred kB on a 127 MB store).
- `ColumnStore::map` maps the file read-only (`coach_analysis::mapped`: `CreateFileMappingW`/`MapViewOfFile` on Windows, `mmap` on Linux, declared in the module; no dependency, so `Cargo.lock` is unchanged) and validates it in place; `block()` returns slices of the mapping. Since the review (findings 2 and 3) `Mapping::open` and `ColumnStore::map` are `unsafe fn`s whose precondition is that nothing writes or truncates the file while it is mapped; only the kernels child and the merge stage call them, on the job's private `coach-decode-*`/`coach-merge-*` store that a finished stage wrote. On Windows the mapping keeps its file handle, opened sharing read and delete but not write, so a later open for writing fails (and an existing writer makes the mapping fail); Linux cannot enforce the rule. A length beyond `isize::MAX` is refused before mapping. `ColumnStore::read` (`coach-analysis inspect`, the tests) and `ColumnStore::parse` hold the bytes. The crate's `forbid(unsafe_code)` became `deny(unsafe_code)` with one allowed module (two system calls and one `slice::from_raw_parts`) and the two `allow`ed call sites.
- Mapped pages are file backed: they count in the working set while resident but not in committed memory, and the system can drop and re-read them. The kernels child and the merge stage map; the Python seam already mapped (`StoreFile`).

The writer itself reads no store: it receives the finished analysis object, which no longer holds a sample, so there is nothing for it to map.

### 2.3 Files

| Path | Content |
|---|---|
| `crates/coach-analysis/src/writer/mod.rs` | request, answer, `coach-analysis write` |
| `crates/coach-analysis/src/writer/pickle.rs` | the pickle reader (protocol 5 data opcodes, the marker) |
| `crates/coach-analysis/src/writer/json.rs` | the encoder, key order, escaping, threads |
| `crates/coach-analysis/src/mapped.rs` | the read-only mapping |
| `crates/coach-analysis/src/store.rs` | page-aligned layout, `ColumnStore` over a mapping |
| `crates/coach-analysis/src/main.rs`, `lib.rs` | `write` subcommand; modules |
| `crates/coach-numerics/src/{intrepr.rs, repr.rs, decimal.rs, bigint.rs, lib.rs}` | `int_repr_from_le_bytes`, `float_repr_into` (additive) |
| `crates/coach-analysis/tests/writer.rs`, `tests/vectors/writer/cases.json`, `tools/emit_writer_vectors.py` | 926 oracle vectors; `--stress` |
| `crates/coach-numerics/tests/float_repr_into.rs` | `float_repr_into` against `float_repr` |
| `crates/coach-analysis/tests/decode.rs`, `kernels_codec.rs` | alignment and mapped read; a mapping is not `Clone` |
| `iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py` | `writer` stage, `writer_call`, `_WriterPickler`, counters, `writer_call_problems` |
| `.../scripts/workflow.py`, `.../scripts/storage.py` | the three sites |
| `iracing-coach/tests/test_native_stages.py` | seam tests (with and without the executable) |
| `tools/parity/analysis_parity.py`, `worker_trace.py`, `test_analysis_parity.py`, `README.md` | writer counts, `analysis-sha256-check.json`, capture format 5 |
| `tools/parity/measure_worker.py` | `--baseline-bin`, largest process commit |

## 3. Parity

Commands (release builds of the final source, copied aside; Windows SHA-256 `d5bca335ccd6d74e`, Linux `108dad8171f42056`):

```powershell
$env:COACH_PRIVATE_CORPUS = 'C:\u3c'   # short-path junction to the scratch copies
& $py -X utf8 tools\parity\analysis_parity.py corpus --native-stages decode,kernels,writer --native-bin C:\p3\bin\coach-analysis.exe --out C:\p3\cw
```

```bash
COACH_PRIVATE_CORPUS=~/p2i/corpus python3.12 -X utf8 tools/parity/analysis_parity.py corpus \
  --native-stages decode,kernels,writer --native-bin ~/p3/bin/coach-analysis --out ~/p3/gate-corpus
```

Per recording, as in phase 2 (decode golden at native rate and 20 Hz, one cold oracle capture and one native capture compared raw and parsed, the decode stage in the native timings, kernel calls count for count) plus the writer rule (`worker-interface.md` section 8): the three writer sites called equally often on both sides, each native-side call a native write (`Writes` below: 3 everywhere, with the Windows writer time of the output run in milliseconds), no fallback, and `outputs/analysis-sha256-check.json` (Python's `stable_hash` of the cached analysis equals the envelope's hash, which the raw allowlist otherwise hides) identical, true on both sides. Run 2026-10-03 on DESKTOP-JOSHUA, both gates at the same time with the WSL check, so workflow times rank and do not measure. Recordings are the phase-1 scratch copies, never committed.

| # | Kind | Recording | MB | SHA-256 | Golden | Analysis raw / parsed | Kernel calls | Writes (Windows ms) | Workflow s, oracle / native (Windows) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | fixture | synthetic-race.ibt | 0.0 | `540f7208f1bd` | = | identical / identical | 17 | 3 (60) | 0.17 / 0.24 |
| 2 | fixture | truncated-race.ibt | 0.0 | `cd7f4eb5c396` | = | both reject: IbtTruncatedError | - | - | - / - |
| 3 | private | Supra, New Hampshire, 2026-07-27 22:09:39 | 147.0 | `02b00a3cca29` | = | identical / identical | 18 | 3 (481) | 7.71 / 4.05 |
| 4 | private | Supra, New Hampshire, 2026-08-01 20:09:37 | 128.6 | `0c749e369bf8` | = | identical / identical | 17 | 3 (448) | 6.88 / 3.72 |
| 5 | private | Tundra, Kentucky, 2026-07-31 22:24:51 | 40.8 | `13bf212d4b49` | = | identical / identical | 17 | 3 (200) | 2.73 / 1.58 |
| 6 | private | Supra, New Hampshire, 2026-08-01 21:09:00 | 202.5 | `447f6f61eb16` | = | identical / identical | 18 | 3 (828) | 13.80 / 7.04 |
| 7 | private | Supra, New Hampshire, 2026-07-27 20:09:55 | 125.2 | `4f98b65998d0` | = | identical / identical | 17 | 3 (495) | 7.12 / 3.93 |
| 8 | private | Supra, New Hampshire, 2026-08-01 15:09:02 | 203.2 | `582b11e2e65b` | = | identical / identical | 17 | 3 (932) | 16.53 / 7.38 |
| 9 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `60752f797021` | = | identical / identical | 17 | 3 (595) | 11.30 / 5.41 |
| 10 | private | Supra, Daytona, 2026-08-01 18:39:25 | 75.3 | `60d1ecc277fd` | = | identical / identical | 18 | 3 (272) | 4.69 / 2.33 |
| 11 | private | Supra, Iowa, 2026-08-03 20:09:53 | 146.2 | `7ac18c9c8fed` | = | identical / identical | 17 | 3 (608) | 9.40 / 5.02 |
| 12 | private | Tundra, Kentucky, 2026-08-02 22:24:10 | 144.4 | `7fe60f3aedcf` | = | identical / identical | 17 | 3 (462) | 8.26 / 4.19 |
| 13 | private | Supra, New Hampshire, 2026-07-28 21:09:03 | 198.5 | `8f76c042b43f` | = | identical / identical | 17 | 3 (693) | 12.85 / 6.57 |
| 14 | private | Taurus, Indianapolis, 2026-07-29 21:39:27 | 213.9 | `96a350822126` | = | identical / identical | 18 | 3 (517) | 12.68 / 5.36 |
| 15 | private | Tundra, Kentucky, 2026-07-28 22:23:24 | 128.5 | `a58773367610` | = | identical / identical | 17 | 3 (474) | 6.95 / 3.95 |
| 16 | private | Supra, New Hampshire, 2026-07-31 21:09:04 | 208.9 | `c5c7c4da6780` | = | identical / identical | 17 | 3 (748) | 13.02 / 6.43 |
| 17 | private | Supra, New Hampshire, 2026-08-01 14:09:38 | 124.9 | `f60cbb8ce5c7` | = | identical / identical | 17 | 3 (328) | 6.11 / 3.25 |
| 18 | private | Taurus, Indianapolis, 2026-07-27 23:19:28 | 50.2 | `3192d474fc6a` | = | identical / identical | 18 | 3 (224) | 3.46 / 1.76 |
| 19 | private | Taurus, Indianapolis, 2026-07-27 23:32:33 | 0.3 | `bc3abab2e0cb` | = | identical / identical | 17 | 3 (50) | 0.19 / 0.28 |
| 20 | private | Taurus, Indianapolis, 2026-07-29 21:19:55 | 43.4 | `7263ba84511f` | = | identical / identical | 17 | 3 (153) | 2.39 / 1.28 |
| 21 | private | Taurus, Indianapolis, 2026-07-29 21:35:58 | 11.8 | `054cf3dfe295` | = | identical / identical | 18 | 3 (111) | 1.02 / 0.76 |
| 22 | private | Supra, Daytona, 2026-08-01 18:27:08 | 16.0 | `c95f9b053722` | = | identical / identical | 18 | 3 (125) | 1.22 / 0.87 |
| 23 | private | Supra, Daytona, 2026-08-01 18:32:17 | 6.6 | `4bcef1fd2be5` | = | identical / identical | 17 | 3 (90) | 0.63 / 0.59 |
| 24 | private | Supra, Daytona, 2026-08-01 18:34:17 | 11.6 | `cfd28589b7af` | = | identical / identical | 18 | 3 (112) | 0.99 / 0.76 |
| 25 | private | Supra, Iowa, 2026-08-03 20:02:24 | 8.0 | `6f404cd4ac46` | = | identical / identical | 17 | 3 (128) | 0.91 / 0.78 |
| 26 | private | Supra, Iowa, 2026-08-03 20:04:59 | 7.2 | `cd2b3c20fb0d` | = | identical / identical | 18 | 3 (112) | 0.80 / 0.74 |
| 27 | private | Supra, Iowa, 2026-08-03 20:59:41 | 13.3 | `22a20e50dcff` | = | identical / identical | 18 | 3 (116) | 1.05 / 0.78 |
| 28 | private | Supra, Iowa, 2026-08-03 22:27:43 | 43.9 | `265b83f2d863` | = | identical / identical | 18 | 3 (237) | 3.32 / 1.90 |
| 29 | private | Supra, New Hampshire, 2026-07-27 22:01:53 | 8.9 | `3583870bf3b9` | = | identical / identical | 17 | 3 (110) | 0.78 / 0.70 |
| 30 | private | Supra, New Hampshire, 2026-07-28 21:03:59 | 7.0 | `e56b513e0f9e` | = | identical / identical | 17 | 3 (109) | 0.72 / 0.68 |
| 31 | private | Supra, New Hampshire, 2026-07-31 20:42:15 | 23.9 | `525e1591f28b` | = | identical / identical | 17 | 3 (90) | 1.21 / 0.80 |
| 32 | private | Supra, New Hampshire, 2026-07-31 20:48:41 | 46.5 | `719244c4d68c` | = | identical / identical | 18 | 3 (259) | 3.36 / 1.98 |
| 33 | private | Supra, New Hampshire, 2026-07-31 21:01:27 | 8.4 | `4f53d89d5934` | = | identical / identical | 17 | 3 (122) | 0.80 / 0.76 |
| 34 | private | Supra, New Hampshire, 2026-07-31 21:03:56 | 6.0 | `99d54b6f018a` | = | identical / identical | 17 | 3 (102) | 0.69 / 0.64 |
| 35 | private | Supra, New Hampshire, 2026-08-01 13:44:55 | 65.7 | `c4ebf0253f37` | = | identical / identical | 18 | 3 (256) | 4.21 / 2.19 |
| 36 | private | Supra, New Hampshire, 2026-08-01 14:04:32 | 9.9 | `dfb778364979` | = | identical / identical | 17 | 3 (122) | 0.89 / 0.77 |
| 37 | private | Supra, New Hampshire, 2026-08-01 15:01:30 | 7.9 | `493746997a6f` | = | identical / identical | 18 | 3 (117) | 0.79 / 0.72 |
| 38 | private | Supra, New Hampshire, 2026-08-01 15:03:46 | 9.9 | `527f9457af8b` | = | identical / identical | 18 | 3 (128) | 0.92 / 0.78 |
| 39 | private | Supra, New Hampshire, 2026-08-01 20:00:04 | 7.4 | `ef4b55866932` | = | identical / identical | 18 | 3 (101) | 0.76 / 0.70 |
| 40 | private | Supra, New Hampshire, 2026-08-01 20:04:37 | 8.2 | `a8d039e18be7` | = | identical / identical | 18 | 3 (117) | 0.77 / 0.70 |
| 41 | private | Supra, New Hampshire, 2026-08-01 21:03:47 | 0.3 | `a39fbaeae9b6` | = | identical / identical | 15 | 3 (48) | 0.18 / 0.27 |
| 42 | private | Supra, New Hampshire, 2026-08-01 21:04:07 | 7.3 | `7a6fe0798e69` | = | identical / identical | 17 | 3 (118) | 0.71 / 0.73 |
| 43 | private | Supra, New Hampshire, 2026-08-02 13:30:23 | 1.4 | `eee4ac362623` | = | identical / identical | 15 | 3 (50) | 0.18 / 0.26 |
| 44 | private | Tundra, Kentucky, 2026-07-28 22:12:05 | 15.1 | `7cf70fc05da7` | = | identical / identical | 17 | 3 (132) | 1.18 / 0.90 |
| 45 | private | Tundra, Kentucky, 2026-07-28 22:16:00 | 0.3 | `2a3291236467` | = | identical / identical | 17 | 3 (50) | 0.20 / 0.28 |
| 46 | private | Tundra, Kentucky, 2026-07-28 22:16:38 | 9.2 | `b8dc17e34d78` | = | identical / identical | 18 | 3 (129) | 0.89 / 0.79 |
| 47 | private | Tundra, Kentucky, 2026-07-28 22:19:14 | 7.6 | `1e4154bd3171` | = | identical / identical | 17 | 3 (114) | 0.78 / 0.71 |
| 48 | private | Tundra, Kentucky, 2026-07-31 22:16:21 | 9.3 | `ef442f0c8075` | = | identical / identical | 18 | 3 (122) | 0.87 / 0.77 |
| 49 | private | Tundra, Kentucky, 2026-07-31 22:19:03 | 13.6 | `ab6188605c0c` | = | identical / identical | 17 | 3 (124) | 1.05 / 0.81 |
| 50 | private | Tundra, Kentucky, 2026-08-02 22:14:59 | 3.3 | `836e9e87f431` | = | identical / identical | 15 | 3 (49) | 0.28 / 0.31 |
| 51 | private | Tundra, Kentucky, 2026-08-02 22:16:32 | 8.7 | `af651dad2f80` | = | identical / identical | 18 | 3 (118) | 0.85 / 0.72 |
| 52 | private | Tundra, Kentucky, 2026-08-02 22:19:00 | 7.5 | `8fe7216a37df` | = | identical / identical | 18 | 3 (106) | 0.76 / 0.68 |

Worker cases, both platforms identical, 17 kernel calls and one native write per site each: `synthetic` `9f4f93d4315bc43c14ebf2e7`, `reconnect` `18bb89fd9d2ec273dff77d87`, `restaged` `9f4f93d4315bc43c14ebf2e7`, `local-file` `3fbea207efcae8cd78eb0517`, the same values as phases 1 and 2. No Windows/Linux difference occurred.

**Writer vectors.** `tools/emit_writer_vectors.py` writes `tests/vectors/writer/cases.json` (926 cases: 46 hand-written values in three modes, `bytes`, `bytes` with `\r\n`, `sha256`, and 400 random values in two), each the seam's own pickle with the oracle's outcome; `--check` is identical under 3.12.14 (Windows) and 3.12.3 (Linux). They cover float repr edges and float32 values widened to double, `NaN`/`Infinity` in both modes and as keys, ints at `i64` and the 4,300-digit limit, every control character, `\u2028`, astral characters and lone surrogates and surrogate runs in both modes, `bytes`, empty and shared containers, cycles through lists, dicts and tuples and a cycle after an earlier error, mixed key types (`TypeError` with CPython's operand order, NaN keys, bool keys), subclasses and objects `default=str` turns into strings, an object whose `__str__` raises, a 150-deep nesting, and the seven fallback cases. `--stress 25000` (random values only, not committed) produced 50,000 cases per platform (seeds 11 on Windows, 12 on Linux); `COACH_WRITER_VECTORS=<file> cargo test --release -p coach-analysis --test writer` replays them: all identical.

**The checks fail when they should.** Each of these planted defects made its check fail, and the files were restored byte for byte: `float_repr_into` without the tie check (`tests/float_repr_into.rs`); uppercase hex escapes, the `NaN` message without its value, no circular-reference check, reversed string-key order, a cached key order reversed, a mixed-key `TypeError` swallowed (`tests/writer.rs`); the threads' results joined in reverse (`threads_give_the_sequential_result`); a header padded past the page (`tests/decode.rs`). Two mutated writer builds against the fixtures gate: one extra byte in the `bytes` mode failed the raw comparison of `analysis.json`; one extra byte hashed in the `sha256` mode left `analysis.json` identical but failed `analysis-sha256-check.json` (oracle true, native false).

## 4. Measurements (dev PC)

`tools/parity/measure_worker.py` as in phase 2 (the worker driven through `mcp_server.py` as coach-server drives it, one cold process per run in a fresh archive, the modes alternating, Windows job-object accounting for the process tree). New: `--baseline-bin` (a second build for the baseline side) and the largest single process's peak commit in the summary. DESKTOP-JOSHUA (i5-11400, Windows 11), bundled CPython 3.12.14, the gate binaries, machine idle (CPU load 2 % before).

```powershell
& $py -X utf8 tools\parity\measure_worker.py --source C:\p2i\race.ibt --native-bin C:\p3\bin\coach-analysis.exe `
  --baseline-stages decode,kernels --native-stages decode,kernels,writer --runs 5 --base C:\p3\mw --out C:\p3\measure-race.json
```

| | Race `582b11e2`, 203,246,134 bytes, 167 channels | | Indianapolis `96a35082`, 213,856,203 bytes, 166 channels | |
|---|---|---|---|---|
| | `decode,kernels` | `decode,kernels,writer` | `decode,kernels` | `decode,kernels,writer` |
| Worker wall time, median (range) | 5.64 s (5.62 to 5.71) | 5.06 s (4.99 to 5.11), -10 % | 4.70 s (4.68 to 4.77) | 4.33 s (4.32 to 4.49), -8 % |
| Worker peak working set | 0.229 GiB (0.228 to 0.231) | 0.220 GiB (0.219 to 0.221) | 0.165 GiB (0.165 to 0.167) | 0.163 GiB (0.162 to 0.163) |
| Process tree peak commit | 0.572 GiB (0.571 to 0.572) | 0.572 GiB (0.571 to 0.573) | 0.553 GiB (0.553 to 0.554) | 0.554 GiB (0.552 to 0.554) |
| Largest process, peak commit | 0.517 GiB (`kernels` child) | 0.517 GiB (`kernels` child) | 0.499 GiB | 0.499 GiB |
| Processes per run | 3 (worker, decode, kernels) | 6 (and three `write`) | 3 | 6 |
| `analysis_id` | `31419c583d3959b352c3a86c` | the same | `e4622404a48ad2bf70c95125` | the same |

The tree peak does not move with the writer: it is set while the kernels child runs, and the writer children start after it has exited. A `write` child alone on the race's analysis (the `bytes` and `sha256` requests the seam sends, 3 runs each): 0.20 to 0.22 s and 0.17 s, peak working set 114 MiB, peak commit 164 MiB (the 16 MB pickle, about 1.1 million nodes, the 25 MB text and the four threads' buffers). The decoder alone: 0.19 s, 133.6 MiB peak, store 127,248,008 bytes (126,846,168 in phase 2: page padding adds 401,840 bytes, 0.3 %).

**Mapped store**, the same race, `decode,kernels` on both sides, the phase-2 build (`C:\p2i\bin`, reads the store whole) as baseline against this build (`--baseline-bin`), 5 alternating cold runs:

| | Phase-2 build | Phase-3 build (mapped) |
|---|---|---|
| Worker wall time, median (range) | 5.61 s (5.57 to 5.69) | 5.58 s (5.55 to 5.65) |
| Process tree peak commit | 0.690 GiB (0.689 to 0.690) | 0.572 GiB (0.571 to 0.573), -17 % |
| `kernels` child peak commit | 0.635 GiB | 0.517 GiB, -0.118 GiB (the store) |

Mapped pages are file backed: they are in the child's working set while resident but not in its commit, and the system may drop them under pressure. The child's remaining 0.517 GiB (views as `f64`, the `PyValue` trees and the codec text of the largest results) was not broken down here; it is now the tree peak and the next memory target.

**Per serialisation**, the race's final analysis object in one process (idle, 3 repetitions, `native_stages.writer_call` including the pickle, the child and the transfer, against the site's own Python): `analysis.json` bytes 0.26 to 0.34 s against 0.43 to 0.44 s; the `analysis_sha256` 0.23 to 0.26 s against 0.40 s; equal results every time. Of a native call about 0.07 s is the pickle, 0.03 s the child's parse and 0.10 s its encoding (1,064,859 floats); the rest is the process start and the pipes. On short recordings the process starts dominate (in-process profile, section 5 method, median of 3): the 9.9 MB qualifying session `527f9457` spends 0.117 s in its three native writes against 0.114 s of Python JSON in the same places without the writer, the 40.8 MB Kentucky race `13bf212d` 0.202 s against 0.277 s; the stage breaks even at about 10 MB of recording.

Not measured here: Linux and the N100 (the container's cgroup peak, which includes the children, belongs to the G1 deploy measurement). On Linux `measure_worker.py` cannot report the worker's own peak: `wait4`'s `ru_maxrss` is the largest peak of the worker and every child it waited for (a probe in WSL: a worker reaping a 100 MB child reports 113,956 KiB against its own 11,284 KiB), neither worker-only nor simultaneous; the report names it in `peak_kind` (review finding 6). An earlier version of this sentence called it the worker's own peak.

## 5. What still runs in Python after phase 3

Method (`C:\p3\remainder.py`, scratch, not committed): the production `analyze_race_workflow` in process with `decode,kernels,writer`, cold, a fresh archive per run, every function below wrapped to record its exclusive time (a wrapper's time excludes the wrapped functions it calls, so the rows add up to the workflow), median of 3 runs; then one run under `tracemalloc` (one frame) recording, per row, the largest traced Python allocation above a call's entry (Python objects only; the native children are separate processes, measured in section 4). The per-row MB are inclusive (a row's peak includes the wrapped calls it makes: archive writes contain the writer's 68.1 MB) and do not add. That pass reset the global peak at every wrapped call, so its whole-workflow figure (60.9 MB race, 40.2 MB Indianapolis) lost earlier peaks (review finding 7); the **Workflow** row's peak is now a separate pass with nothing wrapped and no reset (scratch `whole_peak.py`, `tracemalloc` from start to end, after one warm-up run, 2 passes, identical): race 128.9 MB with the writer and 234.2 MB without it (`decode,kernels`), Indianapolis 88.3 MB. Same machine, idle. The `decode,kernels` run is the same profile with the writer stage off.

| Component (functions) | Race s | Race % | Race traced MB | Indianapolis s | % | MB | Without the writer (race) s |
|---|---|---|---|---|---|---|---|
| Native kernels: 17 entry points, codec both ways and the child (`native_stages.kernel_call`) | 2.627 | 61.2 | 102.6 | 2.363 | 62.8 | 64.9 | 2.635 |
| Native writer: 3 sites, pickle and child (`writer_call`) | 0.731 | 17.0 | 68.1 | 0.507 | 13.5 | 46.1 | 0 |
| Native decode: scan, decode child, store checks (`_load_analysis_telemetry`) | 0.224 | 5.2 | 2.2 | 0.230 | 6.1 | 2.0 | 0.217 |
| Source SHA-256 of the recording (`ArchiveStore.source_fingerprints`, C hashing) | 0.177 | 4.1 | 2.1 | 0.186 | 4.9 | 2.1 | 0.176 |
| Damage and repair (`_damage_repair_summary`, Python over `StoreTable`) | 0.137 | 3.2 | 20.1 | 0.081 | 2.1 | 20.9 | 0.134 |
| Race investigation (`build_race_investigation`, `investigation_lap_exclusions`: walks the 160-bin traces) | 0.101 | 2.4 | 0.4 | 0.139 | 3.7 | 0.5 | 0.101 |
| Identity and session (`_identity`, `_find_race_session`, `_steering_basis`) | 0.079 | 1.8 | 8.5 | 0.082 | 2.2 | 8.8 | 0.079 |
| Archive writes (`save_report_artifacts`, `record_analysis`; their JSON is the writer's) | 0.050 | 1.2 | 68.1 | 0.042 | 1.1 | 46.1 | 0.490 |
| Report rendering (`render_report`, `render_visuals`, race card) | 0.011 | 0.3 | 0.2 | 0.012 | 0.3 | 0.2 | 0.011 |
| Fingerprint and replay (`_analysis_fingerprint`, `build_race_replay`) | 0.004 | 0.1 | 1.2 | 0.004 | 0.1 | 1.2 | 0.004 |
| Runs (`_runs`; `_service_events` is a kernel) | 0.003 | 0.1 | 10.8 | 0.003 | 0.1 | 11.3 | 0.003 |
| Coaching, grades, insights | 0.002 | 0.0 | 0.0 | 0.001 | 0.0 | 0.0 | 0.002 |
| Strategy (`_strategy`, `_strategy_planning`, `_scheduled_race_laps`, `_field_pace`) | 0.000 | 0.0 | 0.9 | 0.000 | 0.0 | 0.8 | 0.000 |
| Tire lifecycle (`_annotate_tire_set_lifecycle`, `build_tire_learning`) | 0.000 | 0.0 | 0.0 | 0.000 | 0.0 | 0.0 | 0.000 |
| Other orchestration (the workflow body, checks, cache reads, small JSON files) | 0.126 | 2.9 | - | 0.127 | 3.4 | - | 0.997 |
| **Workflow, in process** | **4.295** | | **128.9 peak** (separate pass) | **3.764** | | **88.3 peak** (separate pass) | **4.851** (234.2 MB peak) |

Reading it for a phase 4:

- **Python's own work after phase 3** is the rows from damage down to orchestration: about 0.51 s on the race (12 %) and 0.49 s on Indianapolis (13 %), plus the 0.18 s SHA-256, which is already C code (`hashlib`) and is the integrity check of the recording, not a port candidate. Strategy, tire lifecycle, runs and coaching cost nothing measurable: they read laps and runs, not samples. The per-sample Python left is damage and repair (0.14 s, 20 MB), identity (0.08 s, 8.5 MB, four whole columns), runs' single-sample reads (11 MB) and the investigation's walk of the bins (0.10 to 0.14 s). Porting all of them would save about 0.32 s per race, under 10 % of the time and nothing of the tree peak. Their inclusive peaks (20, 8.5, 11 MB) do not add up to a memory saving, and none was measured as one; the one comparison measured is the writer's: 234.2 MB of whole-workflow Python peak without it, 128.9 MB with it.
- **The hand-off is the larger cost.** The kernels row is 61 % of the workflow and holds the codec both ways (phase 2 measured 0.6 s of `_lap_trace_payload`'s 1.3 s as the JSON decode of `lap_traces` into Python objects, which Python then needs for the investigation and the report) and its 103 MB of Python allocation; the writer row is 17 %, of which the pickle and the process start are a third. The process tree's peak is the kernels child (0.517 GiB), not Python (0.22 GiB worker peak).
- So a phase 4 is worth it only if it changes the boundary rather than the remaining functions: keep `lap_traces` in the child and give Python lazy access (or move the investigation and the report into Rust too), and cut the child's views. Porting damage, identity, runs and investigation alone would bring the race from about 5.06 s to about 4.75 s on this PC.

## 6. Acceptance

2026-10-03, `CARGO_TARGET_DIR=target/p3` on Windows, `~/.cache/p3/target` (gate build) and `~/.cache/p3/target-check` (check) in WSL, `CARGO_BUILD_JOBS=4`.

| Command | Result |
|---|---|
| `cargo test -p coach-analysis -p coach-numerics` (Windows) | pass: analysis lib 31 (with `mapped`, `writer::pickle`, `writer::json` incl. `threads_give_the_sequential_result`), main 3, decode 10, codec 10, field 1, handoff 2, laps 1, sections 1, table 6, traces 1, writer 2; numerics lib 11 (with `intrepr`), float_repr_into 2, stats_memory 1, vectors 13 |
| the same in WSL (inside `tools/wsl/check.sh all`) | pass, same counts |
| `cargo clippy -p coach-analysis -p coach-numerics --all-targets -- -D warnings`; `cargo fmt -p coach-analysis -p coach-numerics --check` (Windows) | clean |
| `emit_writer_vectors.py --check` (Windows 3.12.14; WSL 3.12.3) | identical both |
| `emit_writer_vectors.py --stress 25000` then `COACH_WRITER_VECTORS=... cargo test --release --test writer` (Windows seed 11; WSL seed 12) | 50,000 cases each, identical |
| mutation checks (section 3) | 8 planted defects and 2 mutated gate builds, every one caught |
| `analysis_parity.py corpus --native-stages decode,kernels,writer`, WSL (binding, `python3.12` 3.12.3, Linux build) | PASS: 52/52 recordings, 4/4 worker cases; `analysis_id` values unchanged |
| the same on Windows (3.12.14) | PASS: 52/52, 4/4, same `analysis_id` values |
| `--fixtures-only` in WSL before the corpus | PASS: 2/2, 4/4 |
| `test_native_stages` with `IRACING_COACH_NATIVE_BIN` (Windows; WSL) | 48 pass each (12 skip without the executable) |
| `tools/parity/test_analysis_parity.py` with the executable (Windows; WSL) | 9 pass each |
| `tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>` | exit 0 (1,429 tests: 1,410 passed, 19 skipped) |
| `tools/wsl/check.sh all` (workspace clippy `-D warnings`, workspace tests, Python suite) | passed (Python: 1,429 run, 39 skipped) |
| `measure_worker.py`, race and Indianapolis, `decode,kernels` against `decode,kernels,writer`; race phase-2 build against this build | section 4 |

Not done here: the N100 measurement, the image change. The Codex Sol 6.1 review is resolved in section 7. `tools/export_contracts.py` (outside this task's paths) still describes the stage list as `decode, kernels, json_writer` in its generated environment text; it should read `writer`. Nothing is committed.

## 7. Codex Sol 6.1 review

`reviews/port-phase3-review-codex-sol.md` (ten findings) is resolved there in its Disposition table: all ten accepted, each claimed divergence first reproduced against the Linux CPython 3.12.3 oracle, each fixed with a regression test or vector. In short: the seam no longer evaluates `default=str` or subclass methods while pickling; any object outside the exact types is a counted fallback written once by the site's own code (findings 1 and 4, section 2.1); `Mapping::open` and `ColumnStore::map` are `unsafe`, the Windows mapping denies write sharing and `inspect` reads an owned copy (2), a mapping beyond `isize::MAX` is refused (3); the store reader rejects a data section past the end of the file and a header length that overflows (9, 10); the corpus gate allows a shared rejection only for `truncated-race.ibt` and requires `analysis-sha256-check.json` to state `true` on each side (5, 8); the Linux peak in `measure_worker.py` is labelled as including reaped children (6, section 4); the whole-workflow `tracemalloc` peak was re-measured without resets and the component peaks are labelled inclusive (7, section 5).

Rust `cargo test -p coach-analysis -p coach-numerics` pass on Windows (analysis lib 33, decode 12, writer 2, the rest as section 6) and WSL (lib 32: the Windows-only share test is not built there); `cargo clippy --all-targets -D warnings` and `cargo fmt --check` clean on both. `emit_writer_vectors.py --check` identical on 3.12.14 and 3.12.3 (938 cases). `test_native_stages` with the executable 50 pass on both; `test_analysis_parity.py` 11 pass on both. Binding gate, WSL: `COACH_PRIVATE_CORPUS=~/p2i/corpus python3.12 -X utf8 tools/parity/analysis_parity.py corpus --native-stages decode,kernels,writer --native-bin ~/p3r/bin/coach-analysis --out ~/p3r/gate-corpus`: PASS, 52/52 recordings (51 identical raw and parsed with 3 native writes each and no fallback; `truncated-race.ibt` the one expected rejection) and 4/4 worker cases, `analysis_id` values unchanged. Measurements (Windows, `measure_worker.py`, 5 alternating cold runs, `decode,kernels` against `decode,kernels,writer`): race 5.58 s to 5.08 s, worker peak 0.228 to 0.221 GiB, tree peak commit 0.572 and 0.571 GiB, kernels child 0.517 GiB; Indianapolis 4.68 s to 4.31 s, 0.165 to 0.163 GiB, 0.554 and 0.553 GiB; `analysis_id` unchanged: section 4's figures hold.

The contract text `contracts/worker-interface.md` section 8 (outside this task) still describes the marker as the seam's form for such objects; the request and answer formats are unchanged.
