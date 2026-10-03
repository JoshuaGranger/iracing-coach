# Codex phase 3 Rust analysis port review

Model: gpt-6.1-sol (Codex Sol 6.1), app-resources codex.exe
Reasoning effort: high
Sandbox mode: danger-full-access (--dangerously-bypass-approvals-and-sandbox; read-only enforced only by prompt instruction)
Date: 2026-10-03
Courier note: the text below is Codex output verbatim. A one-line probe (first line of AGENTS.md) succeeded before the review. git status --porcelain before and after the run are identical (30 lines).

---

Read-only review. Probes used CPython 3.12.14 and the Windows binary whose SHA-256 matches the phase-3 document (`d5bca335ccd6d74e…`). No builds or file-writing tests ran.

1. **Blocker — The pickle handoff changes `default=str` traversal and evaluation count.**  
   [native_stages.py:1189](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:1189>), [json.rs:230](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/writer/json.rs:230>).  
   Let `Counter.__str__` increment a counter and return its decimal text. Starting fresh, `[o, o]` produces native `["1","1"]\n`, versus oracle `["1","2"]\n`. Two objects sharing a counter, inserted under `"z"` then `"a"`, produce native `{"a":"2","z":"1"}\n`, versus oracle `{"a":"1","z":"2"}\n`. Pickling evaluates insertion order and memoizes fragments; JSON evaluates sorted traversal on every occurrence. Fallback also repeats side effects: `[o, {1}]` produces native `["2","{1}"]\n`, versus oracle `["1","{1}"]\n`. All reproduced.  
   **Smallest fix:** mark these objects unsupported without first invoking `json.dumps` or custom methods, then run the whole oracle once. Add fresh-object regression vectors.

2. **Major — Mapping immutability is an unenforced safety assumption.**  
   [mapped.rs:33](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/mapped.rs:33>), [mapped.rs:54](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/mapped.rs:54>).  
   Concrete trigger: call the public safe `Mapping::open`/`ColumnStore::read` on a store, then overwrite or truncate that same file through another handle while reading it. Unix truncation can cause `SIGBUS`; overwrites invalidate validated contents and the immutable-slice premise. Windows mapping prevents truncation, but the ordinary open does not exclude writers, and its handle is immediately dropped. Private job paths reduce exposure without enforcing this public API’s premise; `inspect` accepts arbitrary paths.  
   **Smallest fix:** expose the immutability requirement through an unsafe constructor, retain a Windows handle denying write sharing, and keep mutable/untrusted paths on an owned-buffer reader.

3. **Major — Mapping length is checked against `usize`, not `isize::MAX`.**  
   [mapped.rs:35](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/mapped.rs:35>), [mapped.rs:54](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/mapped.rs:54>).  
   Concrete input: a successfully mapped file of `0x80000000` bytes on a 32-bit process with sufficient address space. The conversion to `usize` succeeds, but `slice::from_raw_parts` requires the allocation’s total size to fit `isize::MAX`. The resulting slice violates that safety requirement.  
   **Smallest fix:** reject lengths exceeding `isize::MAX` before mapping.

4. **Major — Python can write whole subtrees while the gate counts a native write with no fallback.**  
   [native_stages.py:1193](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:1193>), [native_stages.py:1063](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/native_stages.py:1063>).  
   Concrete input: `{"lap_traces": Seq([...])}`, where `Seq` subclasses `list`. Python serializes the entire list; Rust appends the resulting fragment. `Seq([{1, 2}])` also hides a set inside that fragment: the native call succeeds with zero fallback calls, despite the documented set fallback rule. Reproduced. Large analysis subtrees can therefore remain Python-written while passing the “every serialization native” counters.  
   **Smallest fix:** count Python fragment serialization explicitly and fail the native gate on it, or send container subclasses through the counted whole-call fallback.

5. **Major — Valid recordings can pass the corpus gate without reaching any writer.**  
   [analysis_parity.py:1294](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:1294>), [analysis_parity.py:1327](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:1327>).  
   Concrete input: a valid recording for which both captures fail with the same normalized `KeyError: analysis` before serialization. `_pair_result` returns `identical=True`; writer-count checks run only when both captures succeeded. The pure-function probe reproduced acceptance of this pair. This can turn a shared regression into a passing phase-3 result.  
   **Smallest fix:** permit matching rejection only for explicitly designated invalid fixtures. Require successful captures and writer counts for every valid corpus member.

6. **Major — Linux “worker own peak” includes waited-for descendants.**  
   [measure_worker.py:175](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/measure_worker.py:175>), [phase3-writer.md:174](<C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/port/phase3-writer.md:174>).  
   Concrete workload: a small worker starts and reaps a child allocating 100 MB. In the read-only WSL probe, the worker’s own `RUSAGE_SELF.ru_maxrss` was **11,652 KiB**, while `wait4(worker).ru_maxrss` was **107,520 KiB**. The reported metric therefore includes the largest reaped descendant peak; it is neither worker-only RSS nor simultaneous tree RSS.  
   **Smallest fix:** obtain worker-only peak RSS from the worker itself, or rename the metric and correct its documentation.

7. **Major — The reported whole-workflow `tracemalloc` peak loses earlier peaks.**  
   [phase3-writer.md:178](<C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/port/phase3-writer.md:178>), [remainder.py:54](C:/p3/remainder.py:54), [remainder.py:95](C:/p3/remainder.py:95).  
   Each wrapper resets the global peak; the workflow figure reads that reset peak after its final call. Concrete workload: wrapped allocation/free of 20 MB, followed by wrapped allocation/free of 0.1 MB. Using the profiler’s extracted wrapper in memory, it recorded the 20 MB component but reported approximately **0.1 MB** for the whole workflow. Component peaks also include nested calls, so they cannot establish additive memory savings.  
   **Smallest fix:** measure the whole-workflow peak in a separate pass without resets, and validate the claimed savings with a comparison measurement.

8. **Minor — The hash gate checks equality of booleans, not that either boolean is true.**  
   [analysis_parity.py:463](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:463>), [analysis_parity.py:1086](<C:/Users/joshu/source/repos/iRacing Coach/tools/parity/analysis_parity.py:1086>).  
   Concrete captures: both cache envelopes contain an incorrect hash, and both check files contain `{"envelope_equals_stable_hash":false}`. The check files compare equal; no assertion rejects their false value. Missing check files on both sides likewise evade the filename-set comparison.  
   **Smallest fix:** require this artifact on both sides and require its value to be exactly `true`.

9. **Minor — A truncated empty store can pass Rust validation.**  
   [store.rs:374](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/store.rs:374>), [store.rs:408](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/store.rs:408>).  
   Concrete input: a valid decode header with zero samples, `sample_indices={"offset":0,"length":0}`, and no columns, whose file ends immediately after an unaligned header, omitting required padding. `data_start` exceeds EOF, but `data()` substitutes an empty slice; the zero-length block passes. Python rejects the same range because its base exceeds file size.  
   **Smallest fix:** reject `data_start > bytes.len()` before constructing the store; add this empty-store truncation vector.

10. **Minor — Header-length arithmetic can panic on 32-bit builds.**  
    [store.rs:393](<C:/Users/joshu/source/repos/iRacing Coach/crates/coach-analysis/src/store.rs:393>).  
    Concrete input: a 16-byte prefix with valid magic/version and `H=0xffffffff`, on a 32-bit build with overflow checks. `16 + header_len` overflows before the bounds check, producing a panic instead of a clear truncated-header error.  
    **Smallest fix:** calculate the header end with `checked_add`, and check conversion of the aligned data start before using it.


---

## Disposition

Resolved 2026-10-03 (task p3-resolve), not committed. Every claimed divergence was first reproduced against the binding oracle, WSL Ubuntu `python3.12` 3.12.3 with the phase-3 Linux build (`108dad8171f42056`), by the scratch probe `probe.py`: finding 1 `["1","1"]` against `["1","2"]`, `{"a":"2","z":"1"}` against `{"a":"1","z":"2"}`, `["2","{1}"]` against `["1","{1}"]`; finding 4 the right bytes with zero fallbacks counted; finding 9 Python `NativeStageError: block runs past the end of the file`, Rust `inspect` accepted. Finding 6 by a WSL probe (worker `RUSAGE_SELF` 11,284 KiB, `wait4` 113,956 KiB); findings 2, 3, 5, 7, 8 and 10 by reading the code (2, 3 and 10 are soundness, not output; 5, 7 and 8 concern the gate and the measurements, not the analysis bytes). After the fixes the same probe gives the oracle's bytes with one counted fallback in each writer case and the same rejection on both sides (fixed Linux build `08ea6c31ff2ac550`, Windows `903835d867deed6b`).

| # | Severity | Decision | Reason | Fix | Regression |
|---|---|---|---|---|---|
| 1 | Blocker | Accept | Reproduced on the oracle: the pickler evaluated `default=str` and subclass methods once per object in insertion order before the writer ran, and again on fallback; `json.dumps` calls them in sorted traversal order, once per occurrence. | `_WriterPickler.reducer_override` raises `_WriterUnsupported` for any object outside the exact types without calling anything of it; `writer_call` then runs the site's oracle once, from the start, as a counted fallback, before any child starts. The seam sends no marker any more (the reader and the request format still accept one). | `test_native_stages.WriterSeamTests.test_default_str_runs_once_in_python_order` (the review's three cases plus a dict subclass's `items`, bytes and sha256, fresh objects per side, no executable may start) and `test_an_object_whose_str_raises_raises_from_the_site`; with the old reducer patched back in, 13 subtests fail. Writer vectors 926 to 938: the subclass, `default=str` and raising cases are kept as marker-reader vectors (`seam_fallback: true`) and classes, `type(None)` and builtin functions added. |
| 2 | Major | Accept | A safe public constructor rested on an unenforced rule; `inspect` mapped arbitrary paths. | `Mapping::open` and the new `ColumnStore::map` are `unsafe fn` with the precondition documented; only the kernels child (`serve.rs`) and the merge stage (`main.rs`) call `map`, with `SAFETY` comments, on the job's private store. On Windows the mapping keeps its handle, opened sharing read and delete but not write. `ColumnStore::read` (inspect, tests) now holds an owned buffer. | `mapped::tests::maps_whole_files_and_empty_ones` (Windows: opening for writing or `File::create` fails while mapped, works after) and `a_file_open_for_writing_is_not_mapped`; `tests/decode.rs` checks `read` is owned and `map` mapped. |
| 3 | Major | Accept | `slice::from_raw_parts` requires at most `isize::MAX` bytes; only `usize` was checked (reachable on 32-bit). | `mapped::mappable_len` refuses lengths beyond `isize::MAX` before mapping. | `mapped::tests::lengths_beyond_isize_max_are_refused`. |
| 4 | Major | Accept | Reproduced: `{"lap_traces": Seq([{1, 2}])}` was a native write with zero fallbacks though Python wrote the subtree (and the set inside it). | Same fix as 1: a container subclass stops the pickle and the whole call is a counted fallback, which `writer_call_problems` fails. | `WriterSeamTests.test_container_subclasses_are_counted_fallbacks` (four subclass values; asserts the gate reports the fallback). |
| 5 | Major | Accept | `_pair_result` passed any shared normalised failure, so a regression that broke a valid recording on both sides passed. | `analysis_parity.EXPECTED_REJECTIONS` (`test-data/ibt/truncated-race.ibt` only, `expects_rejection`): only it may, and must, be rejected identically; any other recording or worker case that fails on either side fails the gate. | `test_analysis_parity.SharedFailureTests`. |
| 6 | Major | Accept | Reproduced in WSL: `wait4`'s `ru_maxrss` includes reaped children. The worker's own peak cannot be read from outside after exit, and phase 3 reported no Linux figure. | Renamed: `measure_worker.py` labels the Linux figure "worker-or-child peak RSS" and the report carries `peak_kind`; the module docstring, `tools/parity/README.md` and `phase3-writer.md` section 4 corrected. | Documentation and label; no Linux figure was published. |
| 7 | Major | Accept | The profiler reset the global peak in every wrapper, so the whole-workflow figure (60.9 MB) lost earlier peaks; per-row peaks are inclusive. | Whole-workflow peak re-measured in a separate pass with nothing wrapped and no reset (scratch `whole_peak.py`): race 128.9 MB with the writer, 234.2 MB without it, Indianapolis 88.3 MB. Per-row peaks labelled inclusive and not additive; the "about 40 MB" saving claim withdrawn (`phase3-writer.md` section 5). | The measurement itself (2 passes each, identical). |
| 8 | Minor | Accept | The gate compared the check files for equality only: false on both sides, or the file missing on both, passed. | `analysis_parity.sha256_check_problems`: a capture of format 5 or later must hold `outputs/analysis-sha256-check.json` equal to exactly `{"envelope_equals_stable_hash": true}`, checked per side in `compare_captures`. | `test_analysis_parity.Sha256CheckTests` (false/false, missing/missing, `1`/`1`, `{}`, mixed). |
| 9 | Minor | Accept | Reproduced: Rust accepted an empty store truncated before the data section that Python rejects. | `ColumnStore::from_backing` rejects `data_start > len` ("data section starts past the end of the file"); `data()` no longer substitutes an empty slice. | `tests/decode.rs::store_rejects_a_data_section_past_the_end` (padded valid, truncated rejected by `parse`, `read` and `map`); fails with the old check restored. |
| 10 | Minor | Accept | `16 + header_len` could overflow before the bounds check on a 32-bit build. | `checked_add` for the header end and a checked conversion of the aligned data start. | `tests/decode.rs::store_rejects_header_lengths_past_the_end` (`u32::MAX`, `u32::MAX - 15`, `2^31`, 64); no 32-bit target is installed here, so the overflow itself is excluded by construction, not by a 32-bit run. |

Verification (2026-10-03): Rust `cargo test -p coach-analysis -p coach-numerics` pass on Windows (analysis lib 33, decode 12, writer 2, the rest as `phase3-writer.md` section 6) and WSL (lib 32: the Windows-only share test is not built there); `cargo clippy --all-targets -D warnings` and `cargo fmt --check` clean on both. `emit_writer_vectors.py --check` identical on 3.12.14 and 3.12.3 (938 cases). `test_native_stages` with the executable 50 pass on both; `test_analysis_parity.py` 11 pass on both. Binding gate, WSL: `COACH_PRIVATE_CORPUS=~/p2i/corpus python3.12 -X utf8 tools/parity/analysis_parity.py corpus --native-stages decode,kernels,writer --native-bin ~/p3r/bin/coach-analysis --out ~/p3r/gate-corpus`: PASS, 52/52 recordings (51 identical raw and parsed with 3 native writes each and no fallback; `truncated-race.ibt` the one expected rejection) and 4/4 worker cases, `analysis_id` values unchanged. Measurements (Windows, `measure_worker.py`, 5 alternating cold runs, `decode,kernels` against `decode,kernels,writer`): race 5.58 s to 5.08 s, worker peak 0.228 to 0.221 GiB, tree peak commit 0.572 and 0.571 GiB, kernels child 0.517 GiB; Indianapolis 4.68 s to 4.31 s, 0.165 to 0.163 GiB, 0.554 and 0.553 GiB; `analysis_id` unchanged: `port/phase3-writer.md` section 4's figures hold.

Follow-up outside this task's paths: `contracts/worker-interface.md` section 8 still says any other object "becomes the marker function ... with the JSON text `json.dumps` writes for it, or the exception it raised"; since finding 1 such an object is a counted fallback written by the oracle, and the seam sends no marker (the request and answer formats are unchanged).
