# Gate G1: analysis on the N100 (wave 2, W9)

Gate definition: `workstreams.md`, Gates, G1. Method: proof P1 (`prove/P1-python-on-n100.md`), inside the released container instead of a host scope.

## Thresholds (written 2026-10-03 before any measurement; not adjustable afterwards)

Recording: the 203 MB New Hampshire race, `stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt`, 203,246,134 bytes, SHA-256 `582b11e2e65b40ff1aada96f75ba1a86af4ae6ded2ce05a11888e6314416fa74`, subsession 87641773, session 2 (Race). Both runs must pass every criterion.

| Criterion | Threshold |
|---|---|
| Wall time of each cold analysis (worker process, `analyze_iracing_race`) | at most 60 s |
| Worker peak RSS (`VmHWM`) | at most 3.0 GiB (3,145,728 KiB) |
| Container memory kill | none: the container cgroup's `memory.events` `oom_kill` unchanged, no job `worker_oom` |
| Home Assistant (`http://127.0.0.1:8123/`, per-second `time_total`) | run-window p95 at most baseline p95 + 100 ms; no failed request. p95 is the value at rank round(0.95 x (n - 1)) (P1) |
| Frigate `camera_fps`, `process_fps` (`/api/stats`, summed over cameras) | run-window mean within +/-5 % of the baseline mean ("unchanged"; P1 saw both move under 0.5 %) |
| Frigate `skipped_fps` (summed over cameras) | run-window mean at most baseline mean + 0.1 fps |
| Frigate `detection_fps` | recorded, not a criterion (P1 idle control: -29 % to +88 % with nothing running) |
| Parity | the container's `analysis.json` byte-equal to the Windows Python 3.12 oracle's apart from line endings and root prefixes (`tools/parity`, raw mode, `--cross-platform`) |

Abort rule (P1): if any Home Assistant request fails, or three consecutive per-second responses exceed twice the baseline p95, the run is cancelled at once (run 1 through `POST /api/v1/jobs/{id}/cancel`, run 2 by killing its own worker), recorded, and counted as failed.

Windows: baseline is the 60 s before run 1; run 1 lasts from the first upload byte to the end of the last job it caused (ingest, archive, discovery, analysis, projection); a 15 s gap; run 2; a 30 s tail. Recorded but not gated: the whole-container peak (`memory.peak`, and `memory.current` sampled 4 times per second per window) and, when arranged, the same with uploads overlapping an analysis and a two-recording reconnect group's worker peak (the factors in `worker-interface.md` section 6).

## Result: PASS (both runs, every criterion)

Run 2026-10-03 04:38:30Z to 04:43:29Z on the released image `iracing-coach:15a53f4161b7233ee55153b43f4d423f1dae7db9` (`server-release.md`). The container had started at 04:30:17Z and was idle: queues and spool empty, and `HEAD /api/v1/uploads/582b11e2...` answered 404 before run 1.

| Criterion | Run 1 (server path) | Run 2 (cold, container `/tmp`) | Threshold | |
|---|---|---|---|---|
| Analysis wall | 15.3 s worker process (sampled lifetime, 0.25 s resolution); `analyze` job 17 s (04:39:56 to 04:40:13, 1 s resolution, includes job-root links and result validation) | 15.48 s (runner timer); CPU 14.11 s user + 1.16 s system | at most 60 s | pass |
| Worker peak RSS | 2,401,668 KiB = 2.290 GiB (`VmHWM`) | 2,401,784 KiB = 2.291 GiB (`ru_maxrss`, equal to the sampled `VmHWM`) | at most 3.0 GiB | pass |
| Container memory kill | `oom_kill 0` before and after; no job failed | same | none | pass |
| HA p95 / failed | 0.922 ms / 0 of 45 | 1.175 ms / 0 of 17 | at most 101.036 ms (baseline 1.036 + 100) / 0 | pass |
| Frigate `camera_fps` (sum) | 20.211 (+0.4 %) | 20.294 (+0.8 %) | 20.127 +/-5 % | pass |
| Frigate `process_fps` (sum) | 20.211 (+0.5 %) | 20.294 (+0.9 %) | 20.107 +/-5 % | pass |
| Frigate `skipped_fps` (sum) | 0.000 (max 0.0) | 0.000 (max 0.0) | at most 0.000 + 0.1 | pass |
| Frigate `detection_fps` (recorded) | 6.511 | 17.312 | baseline 6.067; not a criterion | |
| `analysis_id` | `31419c583d3959b352c3a86c` | `31419c583d3959b352c3a86c` | oracle `31419c583d3959b352c3a86c` | |
| Parity, `tools/parity` raw `--cross-platform` | 11 of 11 persisted files identical | 11 of 11 identical | identical | pass |
| Parity, `analysis.json` alone, roots, separators and newlines only (`g1.py strict`) | identical except 3 run timestamps: `analyzed_at`, `track_geometry.cache.updated_at`, `track_geometry.geometry_provenance.observations[0].observed_at` | the same 3 | see the note | pass |

The abort rule was armed at 04:39:30 (twice the baseline p95: 2.072 ms) and never fired. No Home Assistant request failed in any window.

Parity note: `analysis.json` carries three wall-clock instants written by the run itself, on which no two runs can agree. The parity harness's raw mode (the gate of `worker-interface.md` section 8) allows `*_at` instants as its only substitution besides roots, separators and newlines; every other byte, every float included, is equal. Container and oracle `analysis.json` are 24,795,609 and 24,796,009 bytes; the difference is the length of the Windows root prefix.

### Windows (mini-PC clock, UTC)

| Window | From | To | s | HA n, p95 ms, max ms, failed | Frigate camera / process / skipped / detection fps | Container `memory.current` max |
|---|---|---|---|---|---|---|
| baseline | 04:38:31.3 | 04:39:31.3 | 60.0 | 60, 1.036, 2.585, 0 | 20.127 / 20.107 / 0.000 / 6.067 | 6.9 MB |
| run 1 | 04:39:31.3 | 04:40:16.2 | 44.9 | 45, 0.922, 1.831, 0 | 20.211 / 20.211 / 0.000 / 6.511 | 2.606 GiB |
| gap | 04:40:16.2 | 04:40:34.4 | 18.2 | 18, 0.882, 0.962, 0 | 20.239 / 20.239 / 0.000 / 11.306 | 0.614 GiB |
| run 2 | 04:40:34.4 | 04:40:51.5 | 17.1 | 17, 1.175, 2.122, 0 | 20.294 / 20.294 / 0.000 / 17.312 | 2.868 GiB |
| tail | 04:40:51.5 | 04:41:21.5 | 30.0 | 30, 1.002, 1.119, 0 | 20.173 / 20.173 / 0.000 / 14.573 | 0.622 GiB |
| overlap | 04:41:31.8 | 04:42:29.2 | 57.3 | 57, 1.144, 1.922, 0 | 20.207 / 20.204 / 0.000 / 14.058 | 3.134 GiB |
| overlap tail | 04:42:29.2 | 04:42:59.2 | 30.0 | 30, 1.008, 1.476, 0 | 20.287 / 20.287 / 0.000 / 7.167 | 0.751 GiB |

`memory.current` includes the page cache of files the container wrote or read (the spool copy in run 1; in run 2 the 194 MiB `/tmp` copy, which is tmpfs and counts as memory). Container `memory.peak` at the end: 3,366,637,568 bytes = 3.135 GiB, reached in the overlap window, against `mem_limit` 4 GiB; `oom_kill` stayed 0.

### Run 1: the production path, end to end

- Upload (`tools/rig/g1_upload.py`, collector-protocol section 4, from the dev PC's `Documents\iRacing\telemetry`, read-only): 49 chunks of 4 MiB in 24.07 s (8.45 MB/s), create `201`, 0 conflicts, 0 retries, final state `verified`; `source_mtime` 2026-08-01T21:01:46Z.
- Jobs: `discover` 04:39:55 to :56 (session 1, `subsession:87641773:2`, Race); `archive` 04:39:55 to :59 (203 MB to the NAS in about 4 s; `raw/58/582b11e2....ibt` and its manifest present); `analyze` 04:39:56 to 04:40:13; `project` 04:40:13 (under 1 s).
- First upload byte to projected views: 44.9 s (`architecture.md` section 7 budget: 2 min). Views served afterwards: race 264 KB, runs 142 KB, investigation 119 KB, trace catalog 76 KB, track 26 KB, each in under 30 ms. The spool copy was deleted after archiving.

### Run 2: why it ran in the container's /tmp

A second analysis through the API is not cold. After run 1, `POST /api/v1/sessions/1/analysis` returned `{"job_id":3,"kind":"analyze","state":"succeeded"}` and queued nothing: the server never re-runs a succeeded analysis with the same membership, analyzer digest and profile. A re-run would also be answered from the worker's per-driver analysis cache, whose key does not depend on paths; no API call clears it, and nothing under `/srv/g-house` is touched by hand. Uploading under another driver is not a cold path either: the server attributes a recording from its IBT, and it already holds the bytes. Run 2 therefore used `tools/rig/g1_cold_run.py` through `docker exec` in the released container: the same image, interpreter, script, worker environment (`worker_env` in `server/src/worker.rs`), `nice 10`, `oom_score_adj 1000` and JSON-RPC lines as the server, with a fresh archive and job root in the container's `/tmp`. The recording was copied read-only from the NAS archive (`/nas/apps/iracing-coach/raw/58/...`, 1.78 s) with the server's committed mtime before the window opened; `/tmp` was emptied afterwards.

### Also measured (not gated; inputs to `worker-interface.md` section 6)

Overlap window: the 202.5 MB New Hampshire race of 2026-08-01 21:09 (`447f6f61...`) was uploaded (23.66 s, 8.56 MB/s). While its analysis ran (04:41:56 to 04:42:15, worker `VmHWM` 2,397,896 KiB = 2.287 GiB), the two recordings of New Hampshire practice subsession 87621937 session 0 were uploaded (23.9 MB in 2.95 s, 46.5 MB in 5.44 s); they form a reconnect group.

| Measured | Value | Admission estimate (`11.5 x bytes + 0.15 GiB`, x1.5 for groups) |
|---|---|---|
| 203.2 MB race, worker peak | 2.290 GiB | 2.327 GiB (measured 98.4 % of it) |
| 202.5 MB race, worker peak | 2.287 GiB | 2.319 GiB (98.6 %) |
| Practice part 1 alone (23.9 MB), worker peak | 0.295 GiB | its analysis became non-current when part 2 joined, as `worker-interface.md` requires |
| Reconnect group, 2 recordings, 70.4 MB, worker peak | 0.821 GiB | 1.356 GiB; the single-recording formula alone gives 0.904 GiB, so this pair shows no group penalty (measured / single formula = 0.91) |
| Whole container: analysis, two uploads, discovery and archiving at once | `memory.current` max 3.134 GiB; `memory.peak` 3.135 GiB | budget: running estimates + 0.3 GiB <= 3.7 GiB |
| Discovery worker (light lane), each | 32 to 33 MiB | |

The pair is the only reconnect group on the dev PC (the other repeated subsessions are practice or qualifying stubs under 1 MB). One small pair cannot justify lowering the 1.5 group factor; it shows the factor is conservative. A tuning recommendation loading two analyses was not part of the overlap (no tuning request was arranged), so that peak is still unmeasured.

Python 3.12 in the container against P1's host Python 3.11, same recording: 15.3 to 15.5 s against 13.0 s wall, 2.290 GiB against 2.273 GiB peak.

## Commands

```bash
# Oracle (dev PC, Windows Python 3.12.14); the input copy carries the server's whole-second mtime 2026-08-01T21:01:46Z
python tools/parity/analysis_parity.py capture-worker --source "<scratch>/in/stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt" \
  --out <scratch>/cap/win --base <scratch>/base --keep-base
#   analysis_id 31419c583d3959b352c3a86c, selector subsession:87641773:2, 13.4 s

# Gate (dev PC, Git bash)
RECORDING=".../Documents/iRacing/telemetry/stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt" \
ORACLE=<scratch>/cap/win \
OVERLAP_RACE=".../stockcars2 supra2019_newhampshire oval 2026-08-01 21-09-00.ibt" \
PAIR_A=".../stockcars2 supra2019_newhampshire oval 2026-07-31 20-42-15.ibt" \
PAIR_B=".../stockcars2 supra2019_newhampshire oval 2026-07-31 20-48-41.ibt" \
bash tools/rig/g1-analysis.sh <scratch>/run
#   exit 0, aborted=''
```

`tools/rig/`: `g1-analysis.sh` orchestrates; `g1_sampler.sh` samples HA, Frigate, the container's cgroup memory and the worker processes' `/proc` on the mini PC (in the publisher's home, removed afterwards); `g1_upload.py` is the upload client; `g1_cold_run.py` is the in-container cold run; `g1.py` waits for jobs (with the abort), captures a pulled archive for `tools/parity`, compares `analysis.json` strictly and reports the windows. Recordings and captures stayed in dev-PC scratch; nothing private entered the repository.

Correction made during this run: the first parity step passed container paths (`/data/...`, `/tmp/...`) from Git bash to the Windows Python as arguments, Git bash rewrote them into Windows paths, no root was substituted, and the comparison failed on paths alone. The script now sets `MSYS_NO_PATHCONV=1` and uses `pwd -W`. The comparison was re-run on the same pulled archives, after removing the `history.sqlite3-wal` and `-shm` files that the failed step's SQLite dump had created in the local copies. No measurement was repeated.

## Findings for owners

- W4: every NAS manifest written so far has `recording.session_types: []`, although discovery stored `Race` or `Practice` for each session. The `archive` job (io lane) starts at verify, in parallel with `discover` (light lane), and reads the session types from `session_recordings` before discovery commits them. Manifests are immutable, so the four archived ones keep the empty list. Not a G1 criterion.
- W4 (contract wording): `POST /api/v1/sessions/{id}/analysis` is described as idempotent "while one is queued or running"; the server also returns an existing succeeded job with the same key and queues nothing. Harmless for v1 (identical inputs and code give the identical analysis), but the summary should say so; it also leaves no cold re-analysis path for measurements such as this gate.

## Blocked on the sim agent

The remote agent on DESKTOP-SIM is stopped (`remote_status` fails with `connection_failed`), and only the owner can restart it. Not attempted in this run: the collector install on the sim PC, `coach-collector record-sdk` fixtures, gate G3 (garage capture), gate G4 (rig performance), the D12 migration copy (steps 1 and 3) and the rig-screen check.
