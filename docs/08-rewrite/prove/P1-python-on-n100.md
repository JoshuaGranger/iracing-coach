# P1: does the Python analysis need a Rust port on the N100?

Date 2026-10-02. Decides D2. Written by the P1 proof agent.

## Thresholds (written before measuring, not adjustable afterwards)

PASS means Python stays the production worker for v1 and a Rust analysis port becomes an optional later phase that needs its own measured justification. All of the following, on both cold runs on the mini PC:

- Wall time of one cold analysis of the recording is at most 120 s.
- Peak RSS is at most 3 GiB.
- Home Assistant response unchanged within noise: the p95 of per-second response times during the run is at most baseline p95 + max(100 ms, 25% of baseline p95), with no failed request.
- Frigate detection fps unchanged within noise: the mean detection_fps during the run is within +/-10% of the baseline mean, and the rate of skipped or dropped frames does not increase over baseline.

FAIL means: port only the stages the profile shows dominate, in that order.

Abort rule (Home Assistant degradation): if any HA request fails, or three consecutive per-second responses exceed twice the baseline p95, kill the analysis at once, record it, and count the run as failed.

Both runs must pass.

## Method (commands)

Recording: `C:/Users/joshu/Documents/iRacing/telemetry/stockcars2 supra2019_newhampshire oval 2026-08-01 15-09-02.ibt`, 203,246,134 bytes (203.2 MB, 193.8 MiB), SHA-256 `582b11e2e65b40ff1aada96f75ba1a86af4ae6ded2ce05a11888e6314416fa74` (equal to the raw-archive folder name, and equal on both machines after scp). It is a Race session (subsession 87641773) at New Hampshire in the Supra. The snapshot names "the 203 MB New Hampshire race" without a date; this is the only New Hampshire race recording of about 203 MB (the others are 208.9, 202.5, 198.5 and 147.0 MB). It was found on the dev PC, so the sim PC was not touched (no remote tools, no lease).

Entry point: the production path, the MCP server `mcp_server.py`, fed three JSON-RPC lines on stdin (`initialize`, `notifications/initialized`, `tools/call analyze_iracing_race` with `target_hz: null`, which is native rate as the desktop app calls it, `CompanionState.cs:1157`). `coach_cli.py analyze` was not used because it cannot pass native rate (its range is 1 to 60 Hz, default 20).

Mini PC (`ssh g-house-pc`, user `g-house-publisher`, Debian 12, Python 3.11.2, 4 cores, load average 0.66 before start):

1. `tar --exclude=__pycache__ --exclude=tests -czf backend.tgz iracing-coach` (558 KB); `scp` of the recording, `backend.tgz`, `req.jsonl`, `runner.py`, `sampler.sh` and `p1.sh` into `~/coach-p1/`. Remote `sha256sum` matched the local one.
2. `python3 --version` gave 3.11.2 (at least 3.10: pass).
3. Sampler (`sampler.sh`, background bash loop, about 1.02 s per cycle): `curl -s -o /dev/null -m 10 -w '%{time_total},%{http_code}' http://127.0.0.1:8123/` appended to `ha.csv`, and `curl -s -m 5 http://127.0.0.1:5000/api/stats` appended to `frigate.jsonl`. Started by `p1.sh`: 60 s baseline, run1, 15 s gap, run2, 30 s tail. The skipped or dropped counter used is the per-camera `skipped_fps` rate, the only such counter in the `/api/stats` camera block (there is no cumulative drop counter there).
4. Each run (`p1.sh`): `rm -rf data; mkdir data` (cold: empty analysis cache, no history DB), then `sudo -n systemd-run --scope -q --uid=$(id -u) --gid=$(id -g) -p MemoryMax=3G -p CPUWeight=20 python3 runner.py runN`. `--uid` and `--gid` keep the process owned by the publisher so the watchdog can kill it without further sudo. `runner.py` starts `python3 -X utf8 -u .../scripts/mcp_server.py < req.jsonl` with `IRACING_COACH_DATA=~/coach-p1/data`, `IRACING_COACH_IRACING_ROOT=~/coach-p1/iracing`, `PYTHONUTF8=1`, `LANG=C.UTF-8`, `HOME` and `TMPDIR` inside `~/coach-p1`; samples `VmHWM` from `/proc/<pid>/status` every 0.5 s; reads `ru_maxrss`, `ru_utime` and `ru_stime` from `resource.getrusage(RUSAGE_CHILDREN)` (the numbers `/usr/bin/time -v` would report; `/usr/bin/time` is absent) and `cpu.stat` of the scope cgroup; and runs the abort watchdog on `ha.csv` (kill on any non-200 response, or three consecutive responses over 2x the baseline p95).
5. Before the baseline, `python3 -m compileall -q iracing-coach` was run once, so both runs use compiled modules as a container image would.
6. Idle control (supplementary, added after the Frigate result was known, no analysis running): `control.sh`, 300 samples at 1 per second of `curl -s http://127.0.0.1:5000/api/stats` into `control.jsonl`.
7. Cleanup: `rm -rf ~/coach-p1`; afterwards `ls ~/coach-p1` reported no such directory and no `sampler`, `control.sh`, `mcp_server` or `p1.sh` process remained.

Dev PC (DESKTOP-JOSHUA, i5-11400, bundled Python 3.12.14), scratch `.../scratchpad/prove/P1/devbase` (backend copy, recording copy, own data root). Drivers `dev/drv.py` and `dev/tm.py` import `mcp_server` and call `handle()` with the same `initialize` and `tools/call` messages in one process:

- plain: `python.exe drv.py plain <base> <ibt>`
- cProfile: `python.exe drv.py cprofile <base> <ibt>`, then `prof.py` and `prof2.py` over `cprofile.pstats`
- tracemalloc: `python.exe tm.py <base> <ibt> 0 tm1` (find the traced peak), then `python.exe tm.py <base> <ibt> <97% of that peak> tm2` (snapshot the first time traced memory reaches 97% of the pass-1 peak, `tracemalloc.start(1)`).

A first tracemalloc attempt (4 frames, repeated snapshots) reached 16.6 GB working set on the dev PC after 17 minutes; I stopped it (my own process) and replaced it with the two-pass method above. No other process was touched.

Dev PC load (CPU load percent from `Win32_Processor.LoadPercentage` just before each run, other proofs running concurrently): plain 46%, cProfile 35%, the aborted tracemalloc attempt 73%. Dev PC timings are therefore noisy; the mini PC timings are the ones that matter.

## Numbers

### Mini PC cold analysis (two runs, both `ok:true`, analysis cache miss, `analysis_id b086292623c9d4913ce48140`, stderr empty, exit 0)

| | run1 | run2 | threshold |
|---|---|---|---|
| Wall time (runner timer, 0.5 s poll resolution, so up to 0.5 s high) | 13.01 s | 13.01 s | at most 120 s |
| CPU seconds, scope `cpu.stat` usage_usec | 12.56 s (user 11.21, system 1.35) | 12.64 s (user 11.27, system 1.37) | none |
| `ru_utime` + `ru_stime` | 11.18 + 1.35 s | 11.24 + 1.36 s | none |
| Peak RSS (`VmHWM` sampled, equal to `ru_maxrss`) | 2,383,860 KiB = 2.273 GiB | 2,384,304 KiB = 2.274 GiB | at most 3 GiB |
| Scope throttled | `nr_throttled 0` | `nr_throttled 0` | none |
| Data root size after the run | 252,364,040 bytes (includes the 203 MB raw copy) | same | none |
| Abort watchdog | not triggered | not triggered | |

Run 1 and run 2 differ by less than 0.5 s in wall time and 0.001 GiB in RSS. The page cache was warm (the recording had just been copied; I cannot drop caches without root) and compiled bytecode was present. Reading a cold file from NVMe adds little for 203 MB but was not measured.

### Home Assistant (`curl` against `http://127.0.0.1:8123/`, per-second `time_total`)

Command: `python.exe analyze_mini.py mini` over `ha.csv` and `markers.log`. p95 is the value at rank round(0.95 x (n - 1)) of the sorted samples.

| Window | n | mean ms | p50 ms | p95 ms | max ms | failed requests |
|---|---|---|---|---|---|---|
| Baseline (60 s before run1) | 59 | 0.736 | 0.709 | 0.869 | 1.134 | 0 |
| run1 | 13 | 0.983 | 0.796 | 1.636 | 1.746 | 0 |
| gap (no analysis) | 15 | 0.743 | 0.736 | 0.830 | 0.836 | 0 |
| run2 | 13 | 0.861 | 0.815 | 0.970 | 1.500 | 0 |
| tail (30 s after) | 29 | 0.858 | 0.736 | 1.208 | 3.002 | 0 |

Allowed run p95 is baseline p95 + max(100 ms, 25% of baseline p95) = 0.869 + 100 = 100.869 ms. run1 p95 1.636 ms and run2 p95 0.970 ms are both far inside. The longest streak of responses over 2x baseline p95 (1.738 ms) was 1 in run1 (one sample at 1.746 ms) and 0 in run2, so the abort rule did not trigger. The in-run watchdog recomputed its 2x limit from all rows so far (1.738 ms for run1, 1.926 ms for run2, because run2's included run1's samples); the table above uses the 60 s baseline only, as written in the thresholds.

### Frigate (`/api/stats`, four cameras, summed over cameras)

| Window | n | sum detection_fps (mean) | sum camera_fps (mean) | sum process_fps (mean) | sum skipped_fps (mean, max) | detector inference_speed ms (mean) |
|---|---|---|---|---|---|---|
| Baseline | 59 | 13.032 | 20.169 | 20.139 | 0.000, 0.000 | 7.89 |
| run1 | 13 | 6.592 (-49.4%) | 20.208 | 20.208 | 0.000, 0.000 | 7.94 |
| gap (no analysis) | 15 | 7.200 (-44.7%) | 20.160 | 20.220 | 0.000, 0.000 | 7.93 |
| run2 | 13 | 8.892 (-31.8%) | 20.169 | 20.238 | 0.000, 0.000 | 7.96 |
| tail | 29 | 13.197 (+1.3%) | 20.297 | 20.297 | 0.000, 0.000 | 8.04 |

Per-camera detection_fps mean (baseline, run1, gap, run2, tail): driveway 5.82, 1.49, 2.04, 3.69, 6.67; front 1.10, 0.00, 0.00, 0.00, 0.53; kitchen 5.66, 5.00, 5.06, 5.10, 5.90; patio 0.45, 0.10, 0.10, 0.10, 0.10.

By the written threshold (mean detection_fps within +/-10% of the baseline mean): run1 at -49.4% fails and run2 at -31.8% fails. The skipped-frame criterion passes (0 before, 0 during). Camera fps, process fps and detector inference time did not move, and the 15 s gap with no analysis running is also 44.7% below baseline.

Supplementary idle control (not part of the written rule, no analysis running, `python.exe control.py mini/control.jsonl`): 300 consecutive seconds of summed detection_fps had mean 21.7, min 13.3, max 35.3. Taking the first 59 s as a baseline (mean 18.8) and sliding a 13 s window over the rest, only 58 of 229 windows (25%) lie within +/-10% of that baseline; windows ranged from -29.3% to +87.7%. Frigate's detection_fps follows how much motion is in view, so a one-minute baseline does not make this criterion a reliable detector of interference. The run1 deviation (-49.4%) is below the control's lowest window (-29.3%), so the control does not fully cover it; I have no measurement that separates the two explanations.

### Dev PC profile (cold analysis of a copy, own data root, Python 3.12.14)

Plain run: wall 13.51 s, peak working set 2,464,518,144 bytes = 2.295 GiB (`GetProcessMemoryInfo`), `ok:true`, same `analysis_id`. cProfile run: 20.42 s (profile overhead), peak working set 2.298 GiB.

Share of cProfile time (profiled workflow 19.48 s; GC pause active as in production):

| Part | Functions | Cumulative s | Share of workflow |
|---|---|---|---|
| IBT decode | `workflow._load_analysis_telemetry` (includes `ibt_reader.load_telemetry` 2.20 s, `_strided_scalar_column` 2.02 s, `memoryview.tolist` 1.48 s) | 2.30 | 11.8% |
| Per-sample kernels and analysis | `analysis_engine.analyze_telemetry` | 13.93 | 71.5% |
| Writing analysis files | `storage.save_report_artifacts` 0.59 + `workflow._write_json_cache` 0.61 (`storage._json_text` 0.53 for 4 calls) | 1.20 | 6.2% |
| Everything else | file SHA-256 and raw copy (`archive_raw_telemetry` 0.54, `file_sha256` 0.48 over 2 calls), identity hashes (`stable_hash` 0.51), remaining glue; by subtraction | 2.05 | 10.5% |

Inside the 13.93 s of analysis: `_lap_trace_payload` 5.15 s (26.4% of workflow), `_lap_summaries` 2.71 s, `_additional_trace_sources` 1.72 s, `_track_profile` 1.26 s, `build_track_geometry` (race_foundations) 1.13 s, `_setup_telemetry` 0.84 s, `_setup_channel_series` 0.79 s, `build_race_investigation` 0.51 s. These overlap in places; use them as a ranking, not a partition. JSON encoding as a whole (`json.encoder.iterencode`, 308 `dumps` calls) is 1.93 s (9.9%), wider than the analysis file writes because it also serves hashes and the response.

Self time (`tottime`) by module out of 20.41 s: `analysis_engine.py` 7.95 s (38.9%), C builtins 7.92 s (38.8%), `json/encoder.py` 1.93 s (9.5%), `race_foundations.py` 0.77, `workflow.py` 0.62, `ibt_reader.py` 0.56, `race_investigation.py` 0.27, the rest under 0.1 each. Top self-time functions: `iterencode` 1.93, `memoryview.tolist` 1.48, `_lap_trace_payload` 1.30, `_apply_steps` 1.00, `_normalize_column` 0.85, `sorted` 0.78 (99,704 calls), `max` 0.70 (1.27 million calls), `round` 0.64, `sum` 0.61, `_track_profile` 0.58.

Top 10 functions by cumulative time (cProfile, as printed; the first three are harness wrappers): `drv.call` 20.42; `mcp_server.handle` 20.38; `mcp_server.call_tool` 19.99; `workflow.analyze_race_workflow` 19.48; `workflow._analyze_race_workflow` 19.22; `analysis_engine.analyze_telemetry` 13.93; `analysis_engine._lap_trace_payload` 5.15; `analysis_engine._lap_summaries` 2.71; `workflow._load_analysis_telemetry` 2.30; `ibt_reader.load_telemetry` 2.20.

Memory (tracemalloc, one frame, snapshot at 97% of the traced peak; traced peak 2,073,837,359 bytes = 1.93 GiB, which is 84% of the 2.295 GiB working set, the rest being interpreter and allocator overhead; the traced runs took 151 s and 174 s, about 11x slower, so only the proportions matter):

| Allocating module / function | Bytes at snapshot | Share of 2,012,926,841 |
|---|---|---|
| `ibt_reader.py` (decode), almost all in `_strided_scalar_column` (840.0 MB) | 849.9 MB | 42.2% |
| `analysis_engine.py`, of which `_apply_steps` 421.0 MB, `TelemetryTable.__init__` 253.7 MB (list copies of the decoded columns), `_normalize_column` 225.2 MB, `_row_means` 80.4 MB | 1,120.1 MB | 55.6% |
| `workflow.py` (`_load_analysis_telemetry`) | 36.7 MB | 1.8% |
| JSON writing (storage, json encoder) | not among the top 15 allocators at the snapshot | about 0% |

The peak is almost entirely decoded and converted per-sample float columns held as Python lists; the analysis.json writer is not part of the peak. This attributes by allocation site; the `TelemetryTable.__init__` and `_apply_steps` copies derive from decode output, so decode plus column materialisation is roughly 85% of the peak.

## Result

FAIL by the written thresholds, on one criterion only. Both runs were measured:

| Criterion | run1 | run2 |
|---|---|---|
| Wall at most 120 s | pass (13.0 s, 9.2x margin) | pass (13.0 s) |
| Peak RSS at most 3 GiB | pass (2.273 GiB) | pass (2.274 GiB) |
| HA p95 within allowance, no failed request | pass (1.636 ms vs limit 100.869 ms; 0 failed) | pass (0.970 ms; 0 failed) |
| Frigate mean detection_fps within +/-10% of baseline | FAIL (-49.4%) | FAIL (-31.8%) |
| Frigate skipped or dropped rate not increased | pass (0.000 vs 0.000) | pass (0.000 vs 0.000) |
| Abort rule | not triggered | not triggered |

"Both runs must pass" is not met, so under the thresholds fixed in advance the proof result is FAIL, and the rule that follows a FAIL is to port only the stages the profile shows dominate, in order: per-sample analysis kernels and the trace payload first (71.5% of time, led by `_lap_trace_payload` 26.4% and `_lap_summaries` 14%), IBT decode second (11.8% of time, about 42% of the peak allocation directly and about 85% counting the column copies), then JSON writing (6% to 10%).

Read the Frigate failure with the supplementary control in view. It fails a metric that did not discriminate (25% of idle 13 s windows would also have failed it); it is not evidence that the analysis starved Frigate, since camera, process and detector speed were unchanged and nothing was skipped. I did not change the threshold and did not re-run to look for a passing result.

## Decision impact

- D2 (staged Rust port): confirmed by the letter of the proof, because P1 did not pass. The measurement does not by itself justify the port: on the N100 one cold analysis of the largest race takes 13 s of one core at 2.27 GiB, HA is untouched, and Frigate's frame handling is untouched. If the architect judges the Frigate criterion invalid, the same numbers support Python as the v1 production worker with the port optional. That choice is the architect's; this report does not make it.
- If a port proceeds, the profile gives its order: per-sample kernels and trace payload first, decode and column materialisation second, serialisation last. Memory is dominated by Python float lists, which a Rust column store would remove; that is also what decides whether a 3 GiB `mem_limit` is needed.
- Not decided here: float and `sum()` bit-exactness, which the spike lists as the hard part of any port. This proof measured cost only.

## Notes

- Python on the mini PC is 3.11.2. The spike (section 5) says CPython 3.12 sums floats with Neumaier compensation and 3.11 does not, so output from the mini PC's Python may differ in last bits from the 3.12 oracle. The `analysis_id` was the same on the N100 (3.11.2) and the dev PC (3.12.14), `b086292623c9d4913ce48140`. I did not compare `analysis.json` bytes between them (the mini PC data was deleted): UNKNOWN. A container image should pin 3.12.
- Comparison to the snapshot (10.3 s on the main PC, 2,332 MiB): the dev PC plain run was 13.5 s under 46% load from other proofs, the N100 took 13.0 s. The N100 figure is not comparable with the quiet-machine 10.3 s.
- The N100 had swap full (975 of 976 MiB) and about 8.5 GiB available at the start (`free -m`). I did not sample memory pressure or swap activity during the runs: UNKNOWN. The scope limit `MemoryMax=3G` was not hit.
- Resolution: wall time has 0.5 s granularity; HA is sampled once per second, so a stall shorter than a second between samples would not show; each run window holds 13 samples.
- sudo use, for the record: one `sudo -n true` preflight, one smoke-test `systemd-run --scope` (printing `id` and the cgroup path to confirm `--uid` works), then the two measured scopes. No other sudo. No container, service, deploy tool, firewall or `/srv/g-house` path was touched. The empty `/tmp/g-stack-installed` file seen on the host is from the earlier infra spike, not from this proof, and was left alone.
- `ha.csv`, `frigate.jsonl` and `control.jsonl` were pulled to `.../scratchpad/prove/P1/mini/` before the mini PC folder was deleted. The large recording copy and data roots in the scratch folder are deleted at the end.
- Needs owner: nothing.
