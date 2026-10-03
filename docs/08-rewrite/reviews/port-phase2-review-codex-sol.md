# Codex phase 2 Rust analysis port review

Model: gpt-6.1-sol (Codex Sol 6.1), app-resources codex.exe
Reasoning effort: high
Sandbox mode: danger-full-access (--dangerously-bypass-approvals-and-sandbox; -c sandbox_mode="danger-full-access" -c approval_policy="never"; read-only enforced only by prompt instruction)
Date: 2026-10-03
Courier note: the text below is Codex output verbatim. A one-line probe (first line of AGENTS.md) succeeded before the review. git status --porcelain before and after the run are identical.

---

Eight findings, most severe first. No files changed; no builds or file-writing tests ran. Read-only probes used Python `-B` and the existing kernel binary.

1. **Blocker — U5 ignores merged-column presence.**  
   [field/mod.rs:106](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/field/mod.rs:106>) builds `RawColumn` without the presence bitmap; `Series::finite` reads the zero-filled bytes as observations.

   **Divergent input:** a merged store with `Lat=[None,35,35]`, `Lon=[-96,-96,-96]`, and `LapDistPct=[0.5,0.5,0.5]`. Python excludes the missing latitude; Rust treats it as zero and counts it as rejected GPS. Geometry’s `quality.rejected_gps_samples` differs. Missing `Speed` similarly becomes zero and incorrectly excludes groove samples.

   An existing-store probe reproduced **0 versus 120 rejected GPS samples**. The committed merged synthetic has no field vectors; the field synthetics do not exercise presence gaps.

   **Smallest fix:** carry presence into `RawColumn`, return `None`/false for absent cells before decoding, and add merged geometry and groove vectors.

2. **Major — Native merge rejects mixed float widths that Python accepts.**  
   [table/merge.rs:233](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/table/merge.rs:233>) requires identical element types across files. Python’s merge in [workflow.py:1499](</C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/workflow.py:1499>) preserves each decoded value without that restriction.

   **Divergent input:** two grouped recordings with distinct session times: the first stores `Speed` as SDK float32, the second as float64—for example `20.0` followed by `20.0000001`. Python produces a merged analysis; Rust aborts before producing `analysis.json`.

   **Smallest fix:** support lossless f32/f64 merging by widening f32 cells into an f64 output column. Add a mixed-width merge vector with a double value that cannot survive f32 conversion.

3. **Major — Numeric text parsing rejects Unicode decimal digits accepted by Python.**  
   [coach-numerics/parse.rs:34](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-numerics/src/parse.rs:34>) returns `Unsupported` for non-ASCII numeric characters. [value.rs:276](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/value.rs:276>) propagates that failure; field parsing instead silently converts it to missing evidence.

   **Divergent input:** `_dt_series(["٠", "١"], 1.0)`. Python returns `[1.0,1.0]`; the existing Rust binary returned `Unsupported: non-ASCII digit`. The same problem reaches session-number parsing and text telemetry.

   **Smallest fix:** implement CPython’s Unicode decimal-digit normalization. Non-decimal numeric characters must follow Python’s `ValueError` path, rather than becoming a port-limit error. Extend text vectors beyond ASCII digits.

4. **Major — Successful Python integer conversions become native failures, and the probe blesses them.**  
   [table/views.rs:791](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/table/views.rs:791>) restricts flag conversions to `i64`; [table/summaries.rs:309](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/table/summaries.rs:309>) does likewise for tire compound.

   **Divergent input:** a finite float64 `PlayerTireCompound=9223372036854775808.0`. Python emits integer `9223372036854775808`; Rust fails with `IntBeyondI64`. A `SessionFlags` sample of the same value similarly breaks `_caution_flag_series`.

   [kernels_table.rs:105](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/tests/kernels_table.rs:105>) explicitly expects native failure when the oracle’s flag integer exceeds `i64`, so this check does not enforce parity.

   **Smallest fix:** preserve these integers using the existing `BigInt` codec representation and appropriate bit operations. Make the probe compare the successful oracle result instead of expecting failure.

5. **Minor — Missing required `accessed` silently defaults to an empty list.**  
   [native_stages.py:879](</C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:879>) accepts a result without its required access record.

   **Divergent input:** a child returns the correct `_driver_adjustments_summary` result for a recorded `dpQTape`, but omits `"accessed":["dpQTape"]`. The shim accepts it; if no other caller reads that channel, the emitted analyzed-channel list differs. The current Rust writer supplies the field, but the receiving seam accepts an incompatible response.

   **Smallest fix:** require `accessed` on result/exception answers and require exactly one outcome. Missing fields should raise `NativeStageError`.

6. **Minor — Zero bins reject an empty trace result Python returns.**  
   [traces/payload.rs:274](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/traces/payload.rs:274>) rejects `maximum_bins < 1` before examining laps.

   **Divergent input:** `_lap_trace_payload(table, [], maximum_bins=0)`. Python returns the normal payload with `distance_bins_per_lap:0`, `trace_count:0`, and no traces. Rust returns an internal error. This was reproduced read-only.

   **Smallest fix:** preserve the oracle’s empty-input behavior before applying a nonpositive-bin restriction; cover this call explicitly.

7. **Minor — Approximate string `repr` changes geometry identity.**  
   [field/mod.rs:444](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/field/mod.rs:444>) treats several Python-nonprintable characters as printable, including U+2066.

   **Divergent input:** `build_track_geometry({}, {}, {"track_id":["\u2066"],"track_name":"X"})`. Python escapes the character while formatting the container and produces `track_configuration_key:"u2066-x"`; Rust produces `"x"`. This was reproduced. It affects unusual container-valued metadata accepted by the oracle.

   **Smallest fix:** use the oracle’s complete Unicode printable classification for Python string `repr`, rather than the partial exclusion list.

8. **Minor — Service lookahead is evaluated after Python would return.**  
   [laps/service.rs:82](</C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/kernels/laps/service.rs:82>) computes lookahead windows even when there are no service groups. Python returns immediately when `hit_indices` is empty.

   **Divergent input:** no stall/service hits, no qualifying pit-road fuel rise, and metadata `sample_rate=4e307`. The bridge calculation remains finite; Python returns `[]`. Rust subsequently evaluates `sample_rate*5.0`, overflows, and raises `OverflowError`. This was reproduced.

   **Smallest fix:** return the empty event list, after `table.check()`, before computing lookahead and flag windows when no groups exist.

---

## Disposition

2026-10-03, task p2-resolve. Each divergent input was first run through the binding oracle (WSL Ubuntu `python3.12` 3.12.3); every claimed divergence reproduced, so all eight are accepted. Each fix has a regression vector or test that fails with the fix reverted (checked by mutation for 1, 4, 6, 7 and 8; 2, 3 and 5 fail by construction: the old code refused the input). Vectors are recorded on Windows (3.12.14) and Linux; the new ones are identical on both apart from the interpreter fields.

| # | Finding | Decision | Oracle result (Linux) | Fix | Regression |
|---|---|---|---|---|---|
| 1 | U5 ignores merged-column presence | accept: the zero fill was read as an observation | `Lat=[None,35,35]`: 0 rejected GPS samples; `[0.0,35,35]`: 1 | `field::RawColumn` carries the store's presence; a missing cell is `None` (`_finite` is `None`, `bool` false) | `review-field-merged`: `field-oval` split into two overlapping files, merged by the oracle, with missing `Lat`, `Lon`, `Speed`, `LapDistPct`, `OnPitRoad` cells (merge-stage store, every unit's calls) |
| 2 | Native merge rejects mixed float widths | accept: Python merges the floats as they are | merged lists of the decoded floats, analysis produced | `table::merge` writes a `double` column when files store a channel as `float` and `double` (widening is exact); any other element or count mix still fails explicitly (Python would hold `int` beside `float` or lists of two lengths) | merge cases `mixed-width` (float then double, values a float cannot hold) and `mixed-width-double-first`, native merge against the oracle's columns |
| 3 | Unicode decimal digits rejected | accept: a port limit where Python returns a value | `_dt_series(["٠","١"],1.0)` = `[1.0, 1.0]`; `int("١٢")` = 12; `float("²")`, `float("½")` raise `ValueError` | `coach-numerics` `parse_float`/`parse_int` apply `_PyUnicode_TransformDecimalAndSpaceToASCII` (code points below U+007F kept, `isspace` to space, Nd digit to ASCII, anything else `ValueError`) from tables generated from the interpreter (`tools/gen_unicode.py`, Unicode 15.0.0, `--check` identical on both platforms); no `Unsupported` remains in parsing | numerics family `parse_unicode` (999 cases: every non-ASCII Nd digit, non-decimal digits, Unicode spaces, mixed scripts); `review-unicode-text` (text channels in six digit scripts, `_dt_series` called on digit text) |
| 4 | Integers beyond `i64` fail natively; the probe blessed it | accept: `SessionFlags` and the tire compound only pass Python ints through | `PlayerTireCompound=2**63` gives `9223372036854775808`; `_caution_flag_series` returns `9223372036854775808` and `-1180591620717411303424` | flag views hold each int's low 64 bits (`int_from_float_low64`, exact for every mask the readers test or combine); `_caution_flag_series` returns the exact int from the finite view; tire compound and `PitSvFlags` (`|=`, exact wide OR) as `BigInt`; session-number keys compare the integral floats. The view probe adds `flags_low64` beyond `i64`, and the test compares it instead of expecting failure | numerics family `int_low64` (621 cases); `review-big-ints` (flags of `2**63` to `1.8e308` with caution and black bits in the low 64 bits, Offline Testing clearing beside them, a `SessionNum` of `2**64`, big tire compound and service flags) |
| 5 | Missing `accessed` defaults to an empty list | accept: the contract (worker-interface.md section 8) requires it | not an oracle question | the shim requires `accessed` and exactly one of `result` and `raises`, else `NativeStageError` | `test_native_stages`: fake child `--no-accessed`, `--both-outcomes`, `--no-outcome` |
| 6 | Zero bins rejected | accept | `_lap_trace_payload(table, [], maximum_bins=0)` and `=-3` return the empty payload; with laps, `-3` returns one bin per lap (`lap_pct` 1.166667) and `0` raises `ZeroDivisionError: float division by zero` | nonpositive bins ported (`min(bins - 1, max(0, ...))`, which `clamp` would have panicked on); the zero case raises the oracle's exception at the first point | five direct calls recorded on `review-track-id` (empty and real laps, bins 0, -3, 1) |
| 7 | Approximate `repr` printable set | accept | key `u2066-x` | `str.isprintable` from the generated CPython table, in U5's `repr` and U2's `KeyError` message | `review-track-id`: a track id list of 14 characters (format, unassigned, private-use tag, bidi marks, line separator, emoji, accented letter) |
| 8 | Service lookahead after Python returns | accept | `sample_rate=4e307` with no hit: `[]` | `_service_events` returns `[]` (after `table.check()`) before the lookahead and flag windows | `review-huge-rate` (every unit's calls at that rate) |

Port limits that remain explicit errors (`numerics.md` section 5): an `int` beyond `i64` where the analysis uses it as a number (lap, set and stint numbers, bins, track-location and session-state comparisons), and a merged channel stored with two element types other than `float` and `double`. The `numerics.md` line on non-ASCII digits is superseded by finding 3 (not edited here; outside this task's paths).

Verification after the fixes: `phase2-kernels.md` section 7.
