# Gate G1 again: the native stages on the N100 (W9)

Date 2026-10-03. Re-measurement of gate G1 (`g1-analysis.md`, same thresholds, same recording, same oracle rule) after port phases 1 and 2 (`port/phase1-decode.md`, `port/phase2-kernels.md`) shipped in the image. Release record: `server-release.md`, "Release of `dccfadf`".

## Result: PASS (both runs, every criterion), and the native path ran in the container

Image `iracing-coach:dccfadf1f40c9654b8d8de41feb2ef260a96c518`, container started 20:13:35Z and idle (queues 0, nothing running). Runs 20:20:24Z to 20:21:07Z.

| Criterion | Run 1 (cold) | Run 2 (cold) | Threshold | | Before (`15a53f4`, Python worker) |
|---|---|---|---|---|---|
| Analysis wall (worker process, runner timer) | 8.50 s (CPU 7.39 s user + 0.86 s system, the children included) | 8.40 s (7.35 + 0.87) | at most 60 s | pass | 15.3 s / 15.48 s |
| Worker peak RSS (`VmHWM`, sampled 10/s) | 261,976 KiB = 0.250 GiB | 264,868 KiB = 0.253 GiB | at most 3.0 GiB | pass | 2.290 / 2.291 GiB |
| `coach-analysis kernels` child peak | 660,764 KiB = 0.630 GiB | 660,752 KiB = 0.630 GiB | recorded | | (none) |
| `coach-analysis decode` child peak | 135,436 KiB = 0.129 GiB | 135,288 KiB = 0.129 GiB | recorded | | (none) |
| Process tree, largest simultaneous `VmRSS` sum | 813,364 KiB = 0.776 GiB | 815,640 KiB = 0.778 GiB | recorded | | 2.29 GiB (one process) |
| Container `memory.current`, window max | 1.229 GiB | 1.295 GiB | recorded | | 2.606 / 2.868 GiB |
| Container memory kill | `oom_kill 0` before and after | same | none | pass | none |
| HA p95 / failed | 0.894 ms / 0 of 11 | 1.019 ms / 0 of 11 | at most 100.848 ms (baseline 0.848 + 100) / 0 | pass | 0.922 / 1.175 ms |
| Frigate `camera_fps` (sum) | 20.318 (+0.4 %) | 20.145 (-0.4 %) | 20.235 +/-5 % | pass | +0.4 % / +0.8 % |
| Frigate `process_fps` (sum) | 20.318 (+0.4 %) | 20.145 (-0.4 %) | 20.235 +/-5 % | pass | +0.5 % / +0.9 % |
| Frigate `skipped_fps` (sum) | 0.000 (max 0.0) | 0.000 (max 0.0) | at most 0.000 + 0.1 | pass | 0.000 |
| Frigate `detection_fps` (recorded) | 3.209 | 3.573 | baseline 1.615; not a criterion | | 6.511 / 17.312 |
| `analysis_id` | `31419c583d3959b352c3a86c` | the same | oracle the same | | the same |
| Parity, `tools/parity` raw `--cross-platform` | 12 of 12 files identical | 12 of 12 identical | identical | pass | 11 of 11 |
| Parity, `analysis.json` alone (`g1.py strict`) | identical except the 3 run timestamps (`analyzed_at`, `track_geometry.cache.updated_at`, `track_geometry.geometry_provenance.observations[0].observed_at`) | the same 3 | see `g1-analysis.md` | pass | the same 3 |
| Kernel-call trace (`g1.py calls`, `worker-interface.md` section 8) | 1 native decode of the recording, 17 kernel calls equal entry for entry to the oracle's 17 entry-point calls, 0 Python entry-point calls; processes worker, `coach-analysis decode`, `coach-analysis kernels` | the same | native path ran | pass | (Python) |

Container `memory.peak` at the end of the gate: 1,394,225,152 B = 1.298 GiB (before: 3.135 GiB, reached in the overlap window; 2.868 GiB `memory.current` in run 2). The windows' `memory.current` includes page cache of the files the runs wrote and read on `/data` (the 194 MiB recording copy, the 121 MiB decode store, the 24.8 MB `analysis.json`); the process tree itself peaked at 0.78 GiB.

In short: wall time -44 % (15.3 to 8.5 s), worker peak -89 % (2.29 to 0.25 GiB), whole worker tree -66 % (2.29 to 0.78 GiB), container peak -59 % (3.135 to 1.298 GiB, not like for like: no overlap window this time). The N100 gains less in wall time than the dev PC did (-41 to -43 %): the same order.

### Windows (mini-PC clock, UTC)

| Window | From | s | HA n, p95 ms, max ms, failed | Frigate camera / process / skipped / detection fps | Container `memory.current` max |
|---|---|---|---|---|---|
| baseline | 20:19:24 | 60.0 | 60, 0.848, 1.057, 0 | 20.235 / 20.235 / 0.000 / 1.615 | 0.391 GiB (page cache of run 1's copy, made before the sampler started) |
| run 1 | 20:20:24 | 10.8 | 11, 0.894, 0.894, 0 | 20.318 / 20.318 / 0.000 / 3.209 | 1.229 GiB |
| gap | 20:20:35 | 22.0 | 22, 0.771, 0.807, 0 | 20.236 / 20.236 / 0.000 / 1.845 | 0.413 GiB |
| run 2 | 20:20:56 | 10.8 | 11, 1.019, 1.019, 0 | 20.145 / 20.145 / 0.000 / 3.573 | 1.295 GiB |
| tail | 20:21:07 | 30.0 | 30, 0.954, 1.086, 0 | 20.217 / 20.193 / 0.000 / 19.923 | 0.461 GiB |

The abort rule was armed at 20:20:21 (twice the baseline p95: 1.694 ms) and never fired.

## What changed in the method, and why

- **Run 1 is a cold run, not the upload path.** The server has held this recording since the first G1 (`HEAD /api/v1/uploads/582b11e2...` answers 200), and it never re-runs a succeeded analysis; the worker's per-driver cache would answer a re-run anyway (`g1-analysis.md`, run 2). `RUN1=cold` in `tools/rig/g1-analysis.sh` makes run 1 the same in-container cold run as run 2, read from the server's NAS copy with the server's committed mtime; the preflight then requires the server to hold the recording. The server's own upload, archive and projection path was measured in the first G1 and did not change in this release apart from the worker command.
- **Cold runs moved from `/tmp` to `/data/run/g1-cold`.** The native decode store goes to the worker's `TMPDIR`; the container's 256 MB `/tmp` tmpfs cannot hold the 194 MiB recording copy and the 121 MiB store, and tmpfs pages count as memory that cannot be reclaimed. The server's job roots and worker home are on `/data` too (`/data/py/<customer>/{jobs,tmp}`). Only that directory is written and `clean` removes it; nothing the server owns is touched.
- **Every cold run is traced.** `g1_cold_run.py run --trace` starts the worker through the logic of `tools/parity/worker_trace.py` (written into the cold base) and `g1.py calls` applies the section 8 count rule against the oracle capture: one native decode per recording, one kernel call per oracle entry-point call, no Python entry point on the native side, and a `coach-analysis kernels` process seen.
- **The whole tree is measured.** The worker is no longer the process that does the work: `g1_cold_run.py` samples every descendant (`VmHWM` per process, the simultaneous `VmRSS` sum) and the container cgroup's `memory.current`; `g1_sampler.sh` also records `coach-analysis` processes.
- **No overlap window.** It needs recordings the server does not hold; none is on the dev PC (the migration imported them all), and the sim PC is outside this task.

Oracle: a fresh Windows capture with the bundled CPython 3.12.14 (pure Python, no native stage) from a scratch copy given the server's mtime 2026-08-01T21:01:46Z; `analysis_id` `31419c583d3959b352c3a86c`, 11.96 s; its trace shows 17 entry-point calls (`_caution_flag_series` twice, `measure_pit_loss` not at all) and no native call.

## The deferred analyses

The four analyses the memory ceiling deferred at migration (`migration.md`, step 4) were re-requested through the API (`POST /api/v1/sessions/{id}/analysis`, 20:26:50Z): each answered `202 {"kind":"analyze","state":"deferred"}` (jobs 3540 to 3543). **The server cannot run them in this release**: admission is the server's own formula (`server/src/jobs.rs` `analysis_estimate`, 11.5 x bytes + 0.15 GiB, x1.5 for groups; ceiling 3.0 GiB in `config.rs`), unchanged, so the estimates are the migration's (3.05 to 3.89 GiB, Kansas 22.6 GiB). The re-request created new rows because the analyzer digest changed with phase 2's scripts: `queue.deferred` is now 8 (4 superseded rows from the previous digest stay; nothing was deleted).

To answer whether they complete, `tools/rig/g1-deferred.sh` ran three of them in the container exactly as the server would (the image's worker command, the server's environment, `nice 10`, `oom_score_adj 1000`, the group selector, the recordings from the server's NAS copies), cold, traced, one after another, HA and Frigate sampled (20:22:43Z to 20:26:07Z). All three completed:

| Session | Recordings | Old estimate | Wall | Worker | Children (peak each) | Tree `VmRSS` sum | Worker + largest child | `analysis_id` | Kernel calls |
|---|---|---|---|---|---|---|---|---|---|
| 18, Las Vegas test | 1, 270,486,936 B | 3.047 GiB | 5.93 s | 0.108 GiB | decode 0.168, kernels 0.687 | 0.769 GiB | 0.795 GiB | `0d0405af2e3803ca258905e6` | 15 |
| 105, Bristol race (legacy, Taurus) | 2, 197,460,025 B | 3.397 GiB | 9.13 s | 0.219 GiB | decode 0.123, merge 1.077, kernels 0.600 | 1.110 GiB | 1.296 GiB | `7bb0b7c8c56390f2fc4c78ab` | 18 |
| 123, Bristol race (legacy, Supra) | 2, 227,938,532 B | 3.887 GiB | 15.04 s | 0.419 GiB | decode 0.096 and 0.055, merge 1.169, kernels 0.803 | 1.199 GiB | 1.587 GiB | `43a48188d11904441bc6d8d5` | 18 |

Each run made one native decode per recording without error, no Python entry-point call, and left no `coach-decode-*` or `coach-merge-*` directory. HA p95 per window 0.942, 7.326 (one 7.3 ms response among 11), 0.795 ms against a 0.886 ms baseline, no failed request, the abort rule never fired; Frigate `camera_fps` 20.10 / 20.27 / 20.30 against 20.145, `skipped_fps` 0. Container `memory.current` max 1.565, 1.914, 2.217 GiB (page cache included); `memory.peak` 2.355 GiB; `oom_kill 0`.

Not run: session 152, the 2,093,549,028-byte Kansas test. The race's ratio projects a tree near 8 GiB (the decode store alone would be about 1.3 GB, and the kernels child reads it whole), over `mem_limit`. It stays deferred under the proposal below and needs the phase-3 mapped store.

These results were measured, not stored: the server's sessions 18, 105 and 123 still show the analyses they had. They become the server's once W4 applies the admission change below and the deferred jobs are re-requested.

## Proposal (contract change request; nothing applied)

The multi-file merge is new information: `coach-analysis merge` is the largest process of a two-recording group (1.08 and 1.17 GiB), so the group factor is now measured, not assumed. The conservative bound for a job is the worker's peak plus its largest child's (they can coexist; the sampled sum stays below it): 4.65 x bytes on the race, 3.16 x on the 270 MB test, 7.05 x and 7.48 x on the two groups.

`contracts/worker-interface.md` section 6, as proposed:

| Item | Rule |
|---|---|
| Admission (analysis) | before `analyze`: estimated peak = **5.0** x total bytes of the job's recordings + 0.15 GiB for one recording; for a group of two or more recordings 1.5 x that (`coach-analysis merge` holds the per-file stores and the merged store at once). If the estimate > **2.0 GiB** the job is `deferred` with `exceeds_memory_budget` (G1 native: worker plus largest child 0.880 GiB at 203.2 MB, one file; 1.587 GiB at 227.9 MB, two files) |
| Shared memory budget | unchanged rules; a job starts only when its estimate plus the estimates of running jobs plus 0.3 GiB for the server is <= **2.7 GiB** |
| At the limit | container `mem_limit` **3 GiB**, no swap; the rest unchanged |

| Job | Bytes | Bound | Proposed estimate | Bound / estimate | Old estimate |
|---|---|---|---|---|---|
| race, 1 file | 203.2 MB | 0.880 GiB | 1.096 GiB | 80 % | 2.327 GiB |
| session 18, 1 file | 270.5 MB | 0.795 GiB | 1.410 GiB | 56 % | 3.047 GiB (deferred) |
| session 105, 2 files | 197.5 MB | 1.296 GiB | 1.604 GiB | 81 % | 3.397 GiB (deferred) |
| session 123, 2 files | 227.9 MB | 1.587 GiB | 1.817 GiB | 87 % | 3.887 GiB (deferred) |
| session 152, 1 file | 2,093.5 MB | not run | 9.90 GiB: deferred | | 22.57 GiB |

Admitted under the proposal: one recording up to 397 MB (old: 260 MB), a group up to 254 MB in total (old: 173 MB), with a container 1 GiB smaller (4 to 3 GiB, the same 0.3 GiB margin above the budget). D2's "at least 3 GiB until the memory stage lands" holds either way. Server changes (W4): `jobs.rs` `analysis_estimate` 11.5 to 5.0; `config.rs` `analysis_ceiling_bytes` 3.0 to 2.0 GiB and `memory_budget_bytes` 3.7 to 2.7 GiB; then, in the same release, the stack's `deploy/mini-pc/docker-compose.yml` `mem_limit`/`memswap_limit` 4g to 3g and `architecture.md` section 10. Not measured yet with the native stages: the whole container with uploads overlapping an analysis, and a tuning job loading two analyses; re-measure the overlap with the next new recordings before lowering `mem_limit`, and keep the "raise the factor to the observed ratio" rule.

## Follow-ups (W4)

1. **A server setting for the native stages.** `worker_env` (`server/src/worker.rs`) clears the environment and pins `IRACING_COACH_NATIVE_STAGES` empty; nothing configures it. This release turns the stages on in the image's interpreter shim (`deploy/mini-pc/python3.12-native`, `COACH_PYTHON`), which sets `IRACING_COACH_NATIVE_STAGES=decode,kernels` and `IRACING_COACH_NATIVE_BIN=/usr/local/bin/coach-analysis` and execs `python3.12` (same pid, so `nice`, `oom_score_adj` and the kill path are unchanged). Give the server a setting (for example `COACH_NATIVE_STAGES`, passed to the worker as the contract names it), then point `COACH_PYTHON` back at `/usr/local/bin/python3.12`.
2. **Orphan sweep for native stores.** The server's sweep (`worker.rs` `cleanup_after_kill` and `cleanup_strays`, over the driver's archive root) covers `*.<pid>.tmp`, `.write-*` and `.copy-*` only; section 6 also names `coach-decode-*` and `coach-merge-*` under the driver's `TMPDIR` (`/data/py/<customer>/tmp`). A killed or timed-out native worker leaves a store of about 0.6 x the recording's bytes there until then. None was left by any run here.
3. **The admission change above**, then re-request sessions 18, 105 and 123.
4. **Deferred rows per analyzer digest.** Re-requesting a deferred analysis after the digest changed adds a deferred row and keeps the old one, so `queue.deferred` counts superseded jobs (8 now, 4 sessions).

## Commands

```bash
# Oracle (dev PC, bundled CPython 3.12.14), scratch copy with the server's mtime
python -X utf8 tools/parity/analysis_parity.py capture-worker \
  --source "C:/p2i/g1n/in/stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt" \
  --out C:/p2i/g1n/cap/win --base C:/p2i/g1n/base --keep-base      # 31419c58..., 11.96 s

# Gate (Git bash, worktree iracing-coach-w9); RECORDING is read, never written
RUN1=cold RECORDING=".../Documents/iRacing/telemetry/stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt" \
ORACLE=C:/p2i/g1n/cap/win bash tools/rig/g1-analysis.sh C:/p2i/g1n/run          # exit 0, aborted=''

# Deferred analyses, cold in the container
bash tools/rig/g1-deferred.sh C:/p2i/g1n/def 18 105 123                          # exit 0, aborted=''
for s in 18 105 123 152; do curl -X POST -d '{}' http://192.168.1.81:8210/api/v1/sessions/$s/analysis; done
```

Two script defects found on the way and fixed before the recorded run: with `MSYS_NO_PATHCONV=1` Git bash's native `curl` cannot open `/dev/null` (exit 23 at the preflight `HEAD`; the headers now go to a file), and the oracle's `analysis.json` path reached the Windows Python as `/c/...` (the strict step failed to open it; now `cygpath -w`). The strict comparison was re-run on the same pulled archives; no measurement was repeated. Recordings, captures and samples stayed in `C:\p2i\g1n` on the dev PC; the sampler directories in the publisher's home on the mini PC were removed. Nothing private entered the repository.
