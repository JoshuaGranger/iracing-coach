# Rewrite proofs P1 to P5: verdicts (2026-10-02)

Compiled from the five reports in this folder, checked against each report's own written thresholds and against `../owner-decisions-2026-10-02.md` (not edited). Numbers are the reports' own; "UNKNOWN" is carried over unchanged.

## P1: Python analysis on the N100 (decides D2)

Thresholds, as written (both cold runs must pass):
- Wall time of one cold analysis at most 120 s.
- Peak RSS at most 3 GiB.
- HA p95 during the run at most baseline p95 + max(100 ms, 25% of baseline p95), no failed request.
- Frigate mean detection_fps within +/-10% of the baseline mean, and skipped or dropped rate not above baseline.
- Abort rule: any HA request fails, or three consecutive responses over 2x baseline p95.
- FAIL means: port only the stages the profile shows dominate, in that order.

Numbers (Python 3.11.2, 4-core N100, 203 MB New Hampshire race):
- Wall 13.01 s and 13.01 s. CPU 12.56 s and 12.64 s. Peak RSS 2.273 and 2.274 GiB. Throttling 0.
- HA: baseline p95 0.869 ms, run1 1.636 ms, run2 0.970 ms, limit 100.869 ms, 0 failed, abort not triggered (longest streak 1).
- Frigate summed detection_fps: baseline 13.03, run1 6.59 (-49.4%), gap with no analysis 7.20 (-44.7%), run2 8.89 (-31.8%), tail 13.20. camera_fps about 20.2, inference 7.9 ms and skipped_fps 0.000 unchanged.
- Idle control (no analysis): only 25% of 13 s windows fall within +/-10%; range -29.3% to +87.7%.
- Profile: per-sample kernels 71.5% of time, IBT decode 11.8% (about 42% of peak allocation, about 85% with column copies), file writes 6.2%, other 10.5%.

Result: FAIL, on the Frigate criterion alone. Check: both runs are outside +/-10%, so the report's verdict follows from its thresholds. One caution the report understates: run1 (-49.4%) is below the control's lowest window (-29.3%), so the control does not explain it away; the report says so itself. The threshold was not changed and the runs were not repeated.

Decisions:
- D2: staged port stands by the written rule. Order by the rule: kernels and trace payload, then decode, then JSON writing. D2 puts ingestion and IBT decode first; this differs from the rule's order but is not a contradiction (the Rust server needs decode for ingest, and decode plus column copies is most of the memory). Recommendation: keep D2's order, then kernels. The numbers alone also support Python as v1 worker with the port optional; that choice remains the owner's directive (maximum performance), not a measurement result.
- Container: pin Python 3.12 (3.11 lacks compensated float sums; byte parity of analysis.json across versions is UNKNOWN), `mem_limit` at least 3 GiB.
- D3, D4, D5, D9: unaffected.

## P2: Razor in WebAssembly versus TypeScript SPA (decides D3)

Thresholds, as written (all three must hold):
1. At least 70% of v1 UI and view-state lines reusable in a browser without server coupling.
2. First interactive render of Traces 3 s or less at 6x CPU throttle.
3. Cursor loop 55 fps or more under continuous pointer movement at 6x CPU throttle.
- Judged on the worse of phone 390x844 and tablet 1024x768; FAIL means the TypeScript SPA stands.

Numbers:
- Reusable: 84.3% conservative, 91.4% direct, 77.3% with the Windows host counted (23,691 lines, 63 files).
- WASM at 6x (median of 3): phone 74,563 ms and 9.2 fps; tablet 77,463 ms and 9.1 fps. Unthrottled 7.0 to 7.6 s, 60 fps. Payload 3,815,717 B over 78 requests. AOT not tested (wasm-tools absent), effect UNKNOWN.
- Blazor Server comparison at 6x: phone 3,838 ms, 9.6 fps; tablet 3,849 ms, 9.2 fps.

Result: FAIL (thresholds 2 and 3 failed; threshold 1 passed). Check: follows. Caveat recorded: threshold 3 did not discriminate. The cursor loop is shared plain JS and Blazor Server also gets about 9 fps at 6x, so the SPA has not been shown to meet 55 fps at 6x and needs its own measurement.

Decisions:
- D3: TypeScript SPA confirmed, on first-render cost and payload, not on reuse. Plan an SPA cursor-loop measurement at 6x as an acceptance gate; do not assume 55 fps.
- D2, D4, D5, D9: unaffected.

## P3: collector language (decides the D5 implementation)

Thresholds, as written (C# route; Rust measured against the same):
- Sim FrameRate, CpuUsageFG, CpuUsageBG and GpuUsage medians within 2% of baseline, and p1 FrameRate within 3%.
- Prototype RSS <= 150 MB.
- Streaming on versus off changes those sim medians by less than 1%.
- Rule: if only one route passes, it is the decision; if both are indistinguishable, report both and ask the owner; if neither can be measured, needs-owner.

Numbers (run 2, 300 s per phase, parked car, FPS capped near 180):
- FPS +0.10% (C#), +0.06% (Rust); p1 +0.23%, +0.21%; FG -1.96%, -1.18%; GPU -0.89%, -1.07%; BG -14.88% both (a decrease).
- Stream on versus off BG: C# -2.08%, Rust -1.05%; FPS, FG and GPU under 1% for both.
- RSS: C# WPF 276 MB (an empty WPF window 230 MB), Rust 8.6 MB. Exe 65 MB versus 0.35 MB.
- Baseline noise: BG 0.169 to 0.191 across baselines, base2 FG +5.5%, p1 -4.99% with nothing running. A spin-wait defect (Rust v1, 24% of a core) cost -6.7% FPS, so the harness does detect a real cost.

Result, corrected: by the letter neither route passes. Both fail the two BG criteria, and C# also fails RSS. The report labels the proof "pass" because it ran to a decision and it excludes BG as below the noise and favourable in direction. That exclusion is a judgement, not a written rule, and the written decision rule has no branch for "neither passes". With BG set aside, Rust is the only route that clears every criterion and C#/WPF fails on RSS. Further limits: the Rust prototype has no live engine (lower bound), the car was parked, production polls every 2 ms (not measured), and C# without WPF (about 35 MB in emulation) was not measured on the sim.

Decisions:
- D5 implementation: Rust (no WebView, no .NET) confirmed on footprint, not on sim impact; sim impact does not depend on language. Re-prove on track with the real engine, in the agreed solo sessions.
- D6: 60 Hz streaming over the LAN showed no measurable sim cost (within about 1 to 2%, parked).
- D2, D3, D4, D9: unaffected.

## P4: NAS transport (tests D4)

Thresholds, as written (PASS requires all):
- (a) Temp-name-then-rename atomic as seen from a second client: across 20 trials never the final name with a partial size or unreadable content.
- (b) A 200 MB copy from the sim PC to the share completes at >= 50 MB/s.
- (c) When the share mapping disappears mid-copy, the final name never appears, the copier receives an error, and the partial temp file is identifiable and removable.
- (d) The mini PC can enumerate the NAS exports or shares without credentials, or a documented account is the only missing piece.
- Any of (a), (b), (c) failing is FAIL for D4 as designed (the fallback becomes the bulk path). (d) failing is needs-owner.

Numbers:
- (a) 20 of 20 final names at 52,428,800 bytes with matching SHA-256 (run B); 14 of 14 (run A). Sensitivity weak: the sim's SMB cache hid partial `.tmp` files.
- (b) Sim PC 7.26, 5.58, 6.14 MB/s against 50. Dev PC 6.69, 7.88, 7.34. Dev PC to mini PC over SSH about 6 MB/s. Both links 1 Gbps; ping to the NAS averages 6 ms. Cause UNKNOWN.
- (c) Default buffered writes: no error, the copy completed after the mapping was deleted. Write-through: error after 54.5 MB, no final name, `disap2.bin.tmp` visible and removed.
- (d) `showmount -e` works unauthenticated (apps and Backups exported to 192.168.1.81 only); no `smbclient`; ports 445 and 2049 open. No mount tried; uid/mode of SMB-written files over NFS UNKNOWN.

Result: FAIL, on (b). Check: follows. (c) holds only for a write-through copier, so the default behaviour fails it as written; the collector must write through. The ceiling is the same on every path tested, including NAS-free dev PC to mini PC, so the fallback is not shown to be faster. 20 to 36 s per 146 to 203 MB recording.

Contradiction with D4 (stated plainly): D4 makes the NAS share the bulk path. Measured, the share delivers about 7 MB/s, one seventh of the threshold, and the pre-set rule makes the direct hash-verified upload the bulk path. The ceiling cause is unknown and the upload route measured the same order, so neither route is proven faster. Recommendation: keep the control protocol as D4 specifies, adopt the report's resumable hash-verified HTTP upload as the bulk path (the server writes raw files onward to the NAS, so raw recordings still live on the NAS and the sim PC needs no NAS access), have the collector write through before rename, and find the ceiling's cause (iperf3 or equivalent on a quiet LAN, switch and NAS ports) before any further transport work, then re-run (b).

Decisions:
- D4: contradicted. Resulting decision pending the owner (below). Raw-recordings-on-NAS and no SQLite on the share are unaffected.
- D5: no bulk transfer on track is reinforced.
- Owner chore: delete the empty folder tree `C:\192.168.1.82` on the dev PC (the delete tool refused).

## P5: garage setup publication (tests D9)

Thresholds, as written:
- PASS: before the car leaves the garage, loading or applying a .sto changes the CarSetup section (UpdateCount increments, values match the loaded file's known values) and DriverSetupName reflects the loaded file, with DriverSetupIsModified reflecting unsaved changes.
- FAIL: nothing updates until the car leaves the garage or goes on track; D9's passive capture must then be redesigned around that moment, and the report must say so.

Numbers (Supra, Las Vegas, two loads, one edit):
- CarSetup UpdateCount went 3 to 10 across the visit, each load published in about 1 to 2 s; the unapplied edit appeared about 6.5 s after the click (+/- 3 s).
- DriverSetupName stayed on the session-start file through both loads, all applies and the edit. It changed 0.27 s after IsInGarage went 1 to 0, to the last file loaded, with IsModified 0 to 1. LoadTypeName and PassedTech never changed.
- Values versus earlier recordings of the same setups (71 keys): 0, 1, 4 (all derived), 0 differing; all user-set values matched.

Result, corrected: neither PASS nor FAIL as written. The PASS conjunct on DriverSetupName and IsModified is not met, and the FAIL definition (nothing updates until leaving) is also not met, because values update in the garage. The report's "fail" label means "not pass"; the finding is mixed. Not tested: leaving without Apply, Revert, a new save name, another car, driving out.

Decision, D9: values capture works in the garage; the name arrives only at garage exit and only for the last file loaded. This narrows D9's wording ("when a setup is loaded or applied ... linked to the .sto by name and file hash"): earlier loads in a visit have values but no name. The report says no contradiction; I read it as a narrowing, not a reversal, and the design below is within D9's intent.
- Capture on every UpdateCount change while IsInGarage is 1; commit, linked to the .sto by name and file hash, when DriverSetupName changes at garage exit.
- Intermediate loads are "values seen, name unknown": match by user-set values or drop; never attach the final name.
- Exclude derived keys (corner weights, ride heights, preloads, last temps and pressures, tread) or use a 1 N / 1 mm / 0.2 Nm tolerance.
- IsModified is meaningful only after garage exit. Require status bit 0 or an advancing tick (stale session info persists with status 0).
- D2 defect to fix: the Python reader should read `DriverSetupIsModified`, not `DriverSetupModified`.

## Summary table

| Decision | Result of the proof | Resulting decision | Owner action needed |
|---|---|---|---|
| D2 analysis worker and port scope | P1 FAIL on Frigate detection_fps only (-49.4%, -31.8%); the criterion is noisy (control: 25% of idle windows pass); all other criteria pass | Staged Rust port stands by the written rule; pin Python 3.12 and `mem_limit` 3 GiB; keep D2's decode-first order, then kernels | No |
| D3 UI technology | P2 FAIL: 74.6 s and 77.5 s at 6x against 3 s; 9.2 fps against 55 | TypeScript SPA confirmed; SPA cursor loop must be measured at 6x | No |
| D5 collector language | P3 literally neither route passes; Rust clears every criterion except noise-limited BG, C# WPF fails RSS (276 MB) | Rust collector confirmed on footprint; re-prove on track with the live engine | No |
| D4 transport | P4 FAIL on (b): 5.6 to 7.3 MB/s against 50; (a) pass, (c) only with write-through | Contradicted; recommend HTTP upload as bulk path with server-side write to the NAS, collector write-through, find the ceiling | Yes |
| D9 garage capture | P5 mixed: values live in the garage, name only at exit and only for the last file | Two-moment capture as above; narrowing, not reversal | No |

## Owner rulings (2026-10-02, evening)

Joshua was shown the numbers above and ruled:

- **D2: B, the staged Rust port by the written rule.** v1 ships with the Python worker; the port follows after v1 is usable, in the rule's order (per-sample kernels and trace payload, then IBT decode, then JSON writing), each stage gated on byte-identical output against the Python oracle. If the architect takes decode first for a dependency reason, the plan states the reason. Pin Python 3.12; `mem_limit` at least 3 GiB until the memory stage lands.
- **D4: HTTP upload is the bulk path.** Collector to service over a resumable, hash-verified, acknowledged, idempotent HTTP connection; the service writes raw recordings onward to the NAS over NFS; the sim PC needs no NAS access; writers use write-through or hash-after-flush before commit. The LAN ceiling is the owner's infrastructure item and the design must not depend on it being fixed.
- D3 (TypeScript SPA), the D5 implementation (Rust collector) and the D9 two-moment capture stand as recorded above. The full wording is in `../owner-decisions-2026-10-02.md`, section "Prove-wave rulings".
