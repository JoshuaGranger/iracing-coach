# Codex phase 1 Rust analysis port review

Model: gpt-6.1-sol (Codex Sol 6.1), codex-cli 0.159.0-alpha.12.1 (app-resources codex.exe)
Reasoning effort: high
Sandbox mode: danger-full-access (--dangerously-bypass-approvals-and-sandbox; -c sandbox_mode="danger-full-access" -c approval_policy="never"; read-only enforced only by prompt instruction)
Date: 2026-10-03
Courier note: the text below is Codex output verbatim. A one-line probe (first line of AGENTS.md) succeeded before the review. An earlier run was killed by the harness time limit with no output and was rerun. git status --porcelain before and after the run are identical (apart from a UTF-8 BOM in the saved before-file). Codex stated it wrote no files and ran no builds or tests.

---

Read-only review. No files changed; no builds or file-writing tests run. Oracle edge cases were checked with CPython 3.12.14; Rust outcomes below follow from source inspection.

1. **Blocker — integer rounding overflows for an in-range result.** [round.rs:140](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-numerics/src/round.rs:140>)  
   **Input:** `round_int_ndigits(-1, -38)`. CPython returns `0`. Rust computes `2 * (10**38 - 1)`, overflowing `i128`: debug builds panic; release builds subsequently return `IntBeyondI64`. The integer vectors jump from `-20` to `-40`, missing this branch.  
   **Smallest fix:** return zero immediately for integer `ndigits <= -20`; every supported `i64` rounds to zero there.

2. **Blocker — sampling overflow becomes successful native output.** [sampling.rs:66](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/sampling.rs:66>)  
   **Input:** `synthetic-race.ibt`, `channels=["Speed"]`, `target_hz=5e-324`. `60 / target` becomes infinity. CPython raises `OverflowError` at `int(position)` after the first index; Rust’s saturating cast ends iteration successfully with `[0]`. The writer serializes the infinite `step` as JSON `null`, which the Python seam ignores.  
   **Smallest fix:** detect non-finite positions before conversion, return an error, and preserve the oracle’s exception at the seam. Add the positive-subnormal case.

3. **Major — the seam does not verify column membership or order.** [native_stages.py:233](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:233>)  
   **Input:** request `["Speed", "Lap"]`; return a store with matching source metadata and `channel_mode="selected"` but only `Lap`, reversed columns, or no columns. Every returned column is individually checked, so all these stores pass. The returned table differs from the oracle; missing channels can activate downstream defaults.  
   **Smallest fix:** compare the complete ordered column-name list with Python’s normalized selection, including the complete catalogue for `channels=None`.

4. **Major — sample membership, order and rate are trusted.** [native_stages.py:253](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:253>)  
   **Input:** a structurally valid 20 Hz store containing 40 repeated or reversed indices and `sample_rate_hz=123.0`. Length checks pass; source-layout checks do not establish the sampling plan. Wrong indices change provenance, while a wrong rate changes downstream analysis context.  
   **Smallest fix:** validate sampling fields and every index against `_sampling_plan` and `_iter_sample_indices` for the actual request.

5. **Major — corrupt text offsets silently manufacture missing text.** [native_stages.py:126](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:126>)  
   **Input:** two text samples, data `b"ab"`, offsets `[0, 2, 100]`. The reader returns `["ab", ""]`; Python slicing silently clamps the out-of-range endpoint. Descending offsets similarly produce empty strings.  
   **Smallest fix:** require first offset zero, monotonic offsets, and final offset equal to the data length before slicing.

6. **Major — Python block offsets can address the header instead of the data section.** [native_stages.py:155](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:155>)  
   **Input:** `sample_indices={"offset": -base, "length": 4}`, `sample_count=1`. The reader consumes `COAC` from the magic as a sample index and passes its length check. Fractional offsets also get silently truncated by `int()`.  
   **Smallest fix:** require actual nonnegative integer offsets/lengths, 8-byte alignment, and ranges wholly inside the data section.

7. **Major — Rust silently discards incomplete index/offset words.** [store.rs:258](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/store.rs:258>)  
   **Input:** an in-bounds sample-index block of five bytes, or a text-offset block of nine bytes. `parse` accepts it; `.as_chunks().0` silently drops the trailing byte. Declared sample counts and numeric-column lengths are also unchecked. Python’s reader rejects incomplete words.  
   **Smallest fix:** validate exact block lengths against sample count, element width and column count during parsing; reject every remainder.

8. **Major — worker-interface parity does not establish native execution.** [analysis_parity.py:1203](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:1203>)  
   **Input:** a worker launch that drops `IRACING_COACH_NATIVE_STAGES`. All four worker cases can compare Python against Python and pass. `_analysis_parity` checks a native-stage timer; `_worker_parity` performs no equivalent check.  
   **Smallest fix:** capture and assert native decode execution for every worker recording, with zero native executions on the oracle side.

9. **Major — arbitrary native golden failures count as parity.** [analysis_parity.py:1095](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:1095>)  
   **Input:** `truncated-race.ibt`; Python raises `IbtTruncatedError`, while native `golden` panics or fails for an unrelated reason. The golden comparison marks this identical merely because both rejected. The workflow comparison cannot expose the decoder failure because Python’s preliminary scan rejects the recording first.  
   **Smallest fix:** compare normalized error categories and messages; require native panics and unrelated failures to fail the gate.

10. **Major — SQLite comparison removes more than the raw-mode allowlist.** [analysis_parity.py:243](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:243>)  
    **Input:** otherwise identical SQLite rows with `created_at="corrupt-A"` versus `"corrupt-B"`, or differing `last_updated` strings. Both are erased by parsed normalization, even though the documented allowance covers ISO instants in `*_at` values. SQLite has no independent raw row-value comparison.  
    **Smallest fix:** apply the exact raw allowlist to SQLite values: require ISO matches and remove undocumented key exemptions.

11. **Major — exact statistics retain one allocated big integer per observation.** [stats.rs:67](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-numerics/src/stats.rs:67>)  
    **Input:** `mean`, `pvariance` or `pstdev` over ten million copies of `0.1`. Rust retains ten million `(BigInt, shift)` entries—approximately 480 MB of additional payload before allocator overhead. CPython accumulates numerator totals by denominator, requiring one denominator bucket for this input. This defeats the intended memory reduction before any result differs.  
    **Smallest fix:** aggregate exact partials by denominator, or determine the common shift first and accumulate without retaining per-item ratios.

12. **Minor — Rust does not enforce the declared store stage or column shape.** [store.rs:225](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/store.rs:225>)  
    **Input:** a version-1 store declaring `stage="kernels"`, or `type_code=4` with `element="i32"`. Rust accepts it; the Python reader rejects it. A later Rust consumer can therefore interpret incompatible data successfully.  
    **Smallest fix:** validate `stage=="decode"`, type-to-element correspondence, positive counts, and required text-offset presence in `parse`.

13. **Minor — array reconstruction adds a full extra pointer array.** [native_stages.py:133](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:133>)  
    **Input:** one 64-element channel across 189,857 samples. The flat list remains alive while every per-sample list is created, adding roughly 97 MB of transient references. The oracle constructs per-row lists without retaining this additional flat list.  
    **Smallest fix:** materialize arrays in bounded row batches, releasing each flat batch after constructing its rows.

## Disposition: resolve-numerics

Scope `crates/coach-numerics/`: findings 1 and 11. The other eleven are outside this crate. Oracle: bundled CPython 3.12.14 (Windows) and 3.12.3 (WSL).

| Finding | Decision | Reason | Files changed |
|---|---|---|---|
| 1 `round_int_ndigits(-1, -38)` overflows | Accept | Reproduced: CPython gives `0` for every int and every `ndigits` from -20 to -41; the new vectors panicked in debug (`attempt to multiply with overflow`, round.rs:140) and in release returned `IntBeyondI64` for 4 cases (`-1`, `-9`, `-5*10**18`, `-2**63` at -38). `ndigits <= -20` now returns 0 (every `i64` is below `10**20 / 2`); -19 still yields `IntBeyondI64` where Python's result is `+-10**19`. Regression: 225 vectors (9 ints including both `i64` edges, `ndigits` -17 to -41), `round.json` 4,375 to 4,600 cases. | `src/round.rs`, `tools/gen_vectors.py`, `vectors/common/round.json` |
| 11 exact statistics keep one big integer per item | Accept | No output divergence (memory only), but measured: `mean` of 1,000,000 copies of `0.1` grew the heap 48,000,048 bytes. `sums` now runs one pass holding two running sums, rescaled when a larger denominator `2**k` appears (at most 1,074 times); results unchanged. Regression: `tests/stats_memory.rs`, a counting global allocator, 1,000,000 items, `mean`/`pvariance`/`pstdev` of `0.1` and `mean`/`variance`/`pvariance`/`stdev` of mixed int/float data with growing denominators: CPython bits and peak growth under 64 KiB each; fails on the previous code. | `src/stats.rs`, `tests/stats_memory.rs` |

Re-run 2026-10-03: `gen_vectors.py --check` 11 of 11 identical (Windows twice, WSL once); `cargo test -p coach-numerics` pass on Windows and Linux (7 unit, 1 memory, 11 vector families); clippy `-D warnings` clean on both; `cargo fmt --check` clean; port gate `analysis_parity.py corpus --native-stages decode` on the fixtures: 2/2 recordings and 4/4 worker cases identical (coach-analysis does not link coach-numerics yet). `docs/08-rewrite/port/numerics.md` still states the old counts (round.json 4,375; 30,308 cases); its owner should update them to 4,600 and 30,533.

## Disposition: resolve-decode

Scope `crates/coach-analysis/`, the Python seam (`native_stages.py`, `iracing-coach/tests/test_native_stages.py`) and `tools/parity/`: findings 2 to 10, 12 and 13. Findings 1 and 11 are `crates/coach-numerics/` (resolve-numerics above). Each claimed divergence was run through the bundled CPython 3.12.14 before acceptance.

| Finding | Decision | Reason | Files changed |
|---|---|---|---|
| 2 infinite sampling step succeeds natively | Accept | Reproduced: `load_telemetry(synthetic-race.ibt, ["Speed"], 5e-324)` and `1e-307` raise `OverflowError: cannot convert float infinity to integer`; `1e-300` (finite step 6e301) returns `[0]`. `sample_indices` now returns `DecodeError::SampleIndexOverflow` (that message) at an infinite position, so no store with a null `step` is written. At the seam it is `NativeStageError` carrying the oracle's message, as `worker-interface.md` section 7 specifies for every native failure; the worker clamps `target_hz` to 1..60, so this input never reaches a job. Regression: 90 new CPython vectors (`1e-300`, `1e-307`, `5e-324` at five tick rates and six record counts, 40 of them `OverflowError`; the 510 earlier cases unchanged), a unit test, a request test (no store written), and a seam test against the executable. | `src/sampling.rs`, `src/lib.rs`, `src/decode.rs`, `tests/decode.rs`, `tests/vectors/sampling.json`, `tools/emit_sampling_vectors.py`, `test_native_stages.py` |
| 3 seam does not check column membership or order | Accept | True: each returned column was checked against the catalogue, never the set or order. `load_telemetry` now requires the column names to equal `_normalise_channels`' result exactly (whole catalogue in file order for `None`, requested names in caller order without repeats). Regression: `SeamValidationTests` feeds the seam stores with only `Lap`, reversed, empty, the catalogue less one, the catalogue rotated: all rejected; correct stores still equal the reader's table. | `native_stages.py`, `test_native_stages.py` |
| 4 sample indices and rate trusted | Accept | True: only lengths were checked. The seam now recomputes the reader's own `_sampling_plan` and checks `target_hz`, `step`, `integral_step` and `sample_rate_hz` bit for bit (an int 20 for 20.0 fails) and every index against `_iter_sample_indices`, streamed (no second list; 190k indices compare in C via `starmap`); `sample_rate_hz` returned is the plan's. Regression: reversed, repeated, short, long indices and each wrong sampling field rejected. | `native_stages.py`, `test_native_stages.py` |
| 5 corrupt text offsets manufacture text | Accept | Reproduced: `b"ab"[2:100]` is `""`, and descending `[0, 2, 1]` gives `["ab", ""]`. Offsets must start at 0, not decrease and end at the text length; a non-UTF-8 slice is `NativeStageError`. Regression: four offset vectors and a split two-byte character. | `native_stages.py`, `test_native_stages.py` |
| 6 block offsets can address the header | Accept | Reproduced: `struct.unpack("<I", b"COAC")` reads as a valid index and `int(0.5)` is 0. Blocks now need integer (not bool), non-negative, 8-aligned offsets and lengths wholly inside the file's data section; `sample_count` must be a non-negative int; a numeric column may not carry `text_offsets`. Regression: seven block specs (negative to `-base`, fractional, float length, bool, unaligned, past the end) plus the two header cases. | `native_stages.py`, `test_native_stages.py` |
| 7 Rust drops partial words | Accept | True for Rust (`as_chunks().0`); CPython's `memoryview.cast("I")` of 5 bytes raises, so Python already rejected. `ColumnStore::parse` now validates every block: inside the data section, 8-aligned, exactly `4 * sample_count` index bytes, `sample_count * count * width` per numeric column, `8 * (sample_count + 1)` offset bytes per text column with offsets rising from 0 to the text length and UTF-8 samples. Regression: `store_rejects_inexact_blocks_and_shapes` (index block +1 byte, sample count -1, 9-byte offset block, short array column, count changed, unaligned block, broken and descending offsets). | `src/store.rs`, `tests/decode.rs` |
| 8 worker parity does not prove native execution | Accept | True: `_worker_parity` had no counterpart to the in-process timer check. Every worker call now runs through `tools/parity/worker_trace.py`, which starts `mcp_server.py` unchanged as `__main__` (scripts folder as `sys.path[0]`) and logs each `native_stages.load_telemetry` call; `capture.json` keeps the calls and the gate requires none on the oracle side and exactly one successful call per recording on the native side (reconnect: 2). Regression: `WorkerNativeDecodeTests` (lost stage, missing recording, failed call, oracle using the stage, no trace, format-2 capture) and a shim smoke test. | `tools/parity/analysis_parity.py`, `tools/parity/worker_trace.py` (new), `tools/parity/test_analysis_parity.py` (new) |
| 9 any native golden failure counts as parity | Accept | True: `identical = oracle is None and native is None`. `coach-analysis golden` now prints its rejection on stdout (`DecodeError::python_class`: the oracle's exception class, the message, a truncation's region and byte range); the gate passes a rejection only with exit 1, a recorded rejection, the oracle's class (coach-ibt's YAML and OSError equivalences), the oracle's text verbatim for the messages coach-analysis writes itself (`IbtChannelError`, `ValueError`, `OverflowError`) and the same region, start, end and available bytes for `IbtTruncatedError`. A panic (101), usage error, unrecorded failure or other class fails. Regression: `GoldenRejectionTests` (crashes, wrong class, changed facts, changed message) and the truncated fixture through the gate. | `src/lib.rs`, `src/golden.rs`, `src/main.rs`, `tools/parity/analysis_parity.py`, `tools/parity/test_analysis_parity.py` |
| 10 SQLite comparison erases more than the raw allowlist | Accept | True: raw mode listed SQLite files as `sqlite` and compared rows only in parsed mode, whose key rules erase `created_at="corrupt-A"` and any `last_updated`. Raw mode now writes `raw/<name>.dump.json` with only the raw allowlist per value (ISO instant in a `*_at` column, a `*_ms` / `elapsed_seconds` number, a 64-hex `analysis_sha256`, the text rules inside other strings). Parsed mode keeps its normalisation as the diagnostic. Capture format 2 to 3. Regression: `SqliteRawDumpTests` (corrupt `*_at`, differing `last_updated`, non-hash `analysis_sha256`, text `*_ms`, corrupt `*_at` inside JSON text stay visible; real instants, hashes, elapsed numbers, root and slug fold). `worker-interface.md` section 8 still says raw mode compares SQLite only as parsed row dumps; its owner should name the allowlisted raw dump. | `tools/parity/analysis_parity.py`, `tools/parity/README.md`, `tools/parity/test_analysis_parity.py` |
| 12 Rust ignores stage and column shape | Accept | True: Rust parsed `stage="kernels"` and `type_code=4` with `element="i32"`; Python rejected both. `parse` now requires `stage == "decode"`, the type's own element, a known type code, `count > 0`, unique non-empty names, `text_offsets` exactly on `utf8` columns. Regression: in the same store test. | `src/store.rs`, `tests/decode.rs` |
| 13 flat list kept beside array rows | Accept | Measured with `tracemalloc` (20,000 x 64 i32): whole-column flattening left 15.4 MB transient beside the rows against 5.1 MB for the block itself. Arrays are now built 4,096 samples at a time from the typed view, and bool columns map straight from the view. Values unchanged (batch sizes 1, 2 and 4,096 give identical tables). Regression: the `tracemalloc` bound (fails with one batch per column) and the batch-equality test. | `native_stages.py`, `test_native_stages.py` |

Re-run 2026-10-03 (`coach-analysis` release SHA-256 `41ec0b38d231`):

| Command | Result |
|---|---|
| `cargo test -p coach-analysis` (Windows; WSL) | 14 passed (4 unit, 10 integration) on each |
| `cargo clippy -p coach-analysis --all-targets -- -D warnings` (Windows; WSL); `cargo fmt -p coach-analysis --check` | clean |
| `test_native_stages` with `IRACING_COACH_NATIVE_BIN` (Windows 3.12.14; WSL 3.12.3) | 19 passed each |
| `tools/parity/test_analysis_parity.py` with the executable (Windows; WSL) | 8 passed each |
| `tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>` | exit 0; 1,390 passed, 0 failed, 10 skipped |
| `analysis_parity.py corpus --fixtures-only --native-stages decode` in WSL (Linux build) | PASS: 2/2 recordings, 4/4 worker cases, same `analysis_id` values as Windows |
| `analysis_parity.py corpus --native-stages decode` with `COACH_PRIVATE_CORPUS` (64 files, 50 unique) | PASS: 52/52 recordings (golden native and 20 Hz, analysis raw and parsed), 4/4 worker cases; native decode traced 1/2/1/1 calls in the worker cases, 0 on the oracle side |

Not changed here: `worker-interface.md` section 8 (SQLite raw dumps, finding 10) and `phase1-decode.md` (vector count 510 to 600; store validation; the gate's rejection, trace and SQLite rules) belong to their owners. Private recordings with paths over 260 characters are skipped silently by `Path.rglob` on Windows; the raw-archive copies were placed at short paths for this run.
