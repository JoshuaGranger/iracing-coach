# Bristol pace-loss investigation and acceptance case

Date: 2026-09-22. This is a measured case study and a list of required workflow improvements, not acceptance of the whole application. It also records an unreleased correction to the 0.17.7 development source. The installed simulator-PC application is unchanged.

## User need

Joshua wants to open a race in which the car was strong early and much slower late, and understand what changed: when, by how much, where around the lap, and whether driving, tire/service history, track conditions, traffic or damage evidence supports an explanation. Generic coaching prose and isolated charts do not fulfill this need.

The recalled example was a recent Bristol **open** race, possibly with a stop after lap 100, and possibly not the latest race because that one might have damage. These details are recollection, not evidence to impose on the recording.

## Candidate search and measured sequence

The existing DESKTOP-SIM task inspected nine metadata-identified race groups: six Bristol, three Bristol open. The strongest match is September 20 at 15:09 Central, subsession 88784656/session 2, Toyota Supra Class B. User confirmation of the identity remains separate from this evidence match.

- The race was scheduled for 160 laps. The analysis contains 159 complete recorded race laps plus boundary/cooldown material.
- The player was P1 through lap 73, P2 on 74, P3 on 76 and P5 on 92.
- A stop across laps 102-103 changed all four tires, confirmed by use counters and odometer resets. Fuel addition was 6.339 L.
- The player returned P3, reached P2 during 106 and returned P3 during 136. P3 is the last recorded position, not an independently checked official result.
- Best screened lap: 116, 15.264 seconds. Therefore the late fade was not an immediate permanent loss of pace at the stop.
- Incident points increased by four on 106 and by four on 142. There were no recorded positive repair timers, tow, repair-required state or confirmed fast-repair uses. This does not certify absence of physical damage.

The later September 20 open race (88792080) had four stops and three optional-repair episodes. The September 15 open race (88667162) stopped after 59 and 109, with later repair/tow context. Neither matches the recollection as closely.

## Defensible comparisons

| Laps | Mean lap time | Interpretation |
|---|---:|---|
| 35-37 | 15.647 s | First-stint reference; all pass backend lap-level screening |
| 83, 85-89 | 16.454 s | First-stint fade; all pass screening |
| 116-120 | 15.468 s | Second-stint recovery; all pass screening |
| 130-132 | 15.913 s | Second fade already measurable; all pass screening |
| 150-159 | 16.874 s | Observed final pace; all fail proximity screening and follow incident context |

Laps 35-37 and 130-132 are predominantly green lap ordinals 22-24 of their stints. Prior accumulated sampled green exposure differs by only about 0.126 lap between corresponding observations. The second window is 0.266 s slower, with track temperature 77.67 F versus 78.33 F and lap-start fuel 21.45 L versus 57.57 L. Mean absolute steering rises from 40.13 to 43.77 degrees; average throttle remains similar (67.1% versus 66.7%). Distance-aligned traces show lower corner speed with more steering input.

This is descriptive, not a controlled causal comparison. The first group is 7-9 continuous green laps after a caution; the second is 20-22. Initial zero age of the first set is unconfirmed, and the second group follows the lap-106 incident increase. All four changes at the stop are confirmed, but final-set wear is unmeasured. A hotter track, heavier fuel load or the later brake-bias changes do not explain this specific matched window; other track state, tire condition, driving response and unmeasured damage are unresolved.

## Important evidence limitations

- There are no eligible reference laps after 132. The existing second-run "late" mean represents laps 131-132, not the finish through 159. Those final laps must remain visible as observations with explicit exclusions.
- Native `CarDistAhead`/`CarDistBehind` metadata says meters. Out-of-range 500000 m values do not explain the selected-lap proximity flags. A recurring near-object region around 9-17% lap distance cannot be identified as a racing competitor or pit-lane object without per-car positions. Proximity is not proof of blockage or dirty air.
- Per-car field channels and a matching durable replay are unavailable for this candidate. Leadership is recorded; exact dominance margins and rival degradation cannot be reconstructed.
- Track temperature stays at 78 F from 131 through 159. That weakens a simple temperature-change explanation, but does not measure all grip/rubber/track effects.
- Incident counts cannot identify physical damage or mental focus. Neither zero repair timers nor good early speed proves an undamaged car.
- The recorded caution flags disagree with zero-valued official metadata. Use the recorded flag timeline with the mismatch preserved.

## Required development, grouped by app section

| Section | Acceptance requirement exposed by this race | Current gap |
|---|---|---|
| Race overview | Select both fade windows on a race-lap timeline with position, caution, service, incident/repair and adjustment context. | No integrated change investigation; run and phase summaries do not establish a breakpoint. |
| Lap comparison | Compare across stints using actual lap IDs, green exposure, laps since restart, fuel, conditions and tire-age certainty. | Manual selection exists; a matched diagnostic cohort workflow does not. |
| Corner/telemetry | Open the same selected cohorts and track area in synchronized traces, and quantify where the speed/input difference occurred. | Backend corner and attribution evidence is richer than the normal Technical Data presentation; own-best/median attribution is not an early/late comparison. |
| Tires/service | Join confirmed tire changes and fuel additions with ages and correctly attributed measured wear endpoints. | Pieces are separated; the final set cannot inherit the preceding set's measured wear. |
| Explanations | Show supporting, contradicting and unavailable evidence for competing causes, then save the investigation. | Generic metrics or guidance do not answer why a particular race changed. |
| Data quality | Expose selection scope and actual rejection reasons. Preserve observed final laps and distinguish chart Clean filtering from coaching eligibility. | Clean chart filtering is weaker than backend reference screening. The first corner metric reports insufficient samples when proximity rejection is the actual reason. |

Useful source entry points: `RaceTechnicalData.razor`, `TelemetryWorkspace.razor`, `RuntimeMapper.cs`, and authoritative backend `analysis_engine.py` sections for runs, corner/tire age, pace attribution and damage screening. The acceptance case does not authorize exact target telemetry, unsupported groove names or damage onset.

## Confirmed numeric defect and bounded correction

The old run pace regression used the ordinal of each surviving eligible lap as its x coordinate. Traffic, restart and other rejected laps disappeared from the elapsed exposure. In this race it reported approximately 0.0965 and 0.0850 seconds per lap, which are actually per surviving observation. Strategy and coaching consumed the same distorted number.

The correction accumulates recorded green-lap fractions between retained timing observations, retaining exposure from fully recorded laps excluded for traffic, restart or other comparison reasons. Fully caution exposure adds zero; mixed laps contribute their recorded fraction. The fraction is the proportion of observed time under green, so it remains a sampled exposure approximation rather than measured wear. Eligibility and the established first-observation behavior stay unchanged. Incomplete or missing interior laps and invalid exposure withhold the rate with an explicit reason; an incomplete fragment is not silently counted as a full green lap. Early/late means remain separate and are unaffected by this repair.

The same Bristol observations produce approximately 0.0224 and 0.0308 seconds per recorded green-lap equivalent. These are observed pace trends, not isolated wear rates. Analysis profile `post-race-foundations-v18` invalidates stale cached answers; the app's current-profile mapper is aligned. Additive slope-basis and unavailable-reason fields preserve JSON compatibility.

Regression coverage includes sparse rejected laps, caution and mixed exposure, partial and missing interior laps, contiguous samples, irrelevant unknown exposure outside the comparison span, and missing/nonfinite/out-of-range fractions.

### Source verification

- Pre-change repository verification passed 1,039/1,039 backend tests.
- Final repository verification passed 1,048/1,048 backend tests, with 21 contracts, 27 fixtures and the MCP end-to-end exercise passing.
- All nine new regression tests pass. The initial sparse/caution cases and the later incomplete-recording cases were first observed failing before their respective repairs.
- Application tests passed 390/390 on the final run. An earlier run hit the catalog call-count concurrency assertion (expected 8, observed 7); that test passed alone and in the full rerun without unrelated catalog changes. This is retained as test reliability context, not claimed fixed by the pace change.
- Contracts/fixtures were regenerated; only the generated compatibility profile changed. Contract export and diff whitespace checks pass.
- The new production helper applied to the exact Bristol exported lap summaries gives 0.0224 and 0.0308.

### Fresh real-recording regression on DESKTOP-SIM

A new disposable Python backend copy applied the two changed source modules after verifying the transfer/member hashes. The normal workflow then reanalyzed the same 208,403-sample Bristol source at native rate. Profile v18 and a cache miss were confirmed. Installed production and development executables/runtimes were not changed.

- New slopes: **0.0224 / 0.0308**, versus **0.0965 / 0.0850** before.
- Exact equality of complete lap objects, all lap times, valid-green lists and coaching-reference lists. Strict finite JSON passed. Original/copy source SHA-256 stayed unchanged.
- Fresh analysis wall time: **18.255 s**; workflow total 18.149 s, decode/analysis 17.116 s. This is a cold backend regression measurement, not a new UI performance improvement or an end-to-end response-time claim.
- The strategy median changes from 0.0907 to 0.0266. The same simplified planning model now ranks one stop above the other tested counts, rather than three. This demonstrates the bug's downstream effect; it does not validate optimal race strategy or assume a future caution schedule.
- Two slope-threshold coaching warnings disappear. The proxy tire-management grade changes B+ to A-, and overall C- to C. These grade changes are numerical consequences, not new evidence of driving quality or completion of grade calibration (`GAP-005`). Local fade detection must not depend solely on a global trend threshold.
- Artifact: private `BRISTOL-INVESTIGATION-v18-regression.json` (with exact by-stop-count comparisons) and its companion Markdown report.
- Source SHA-256: `01bc1d3c5d46fd73938c38e94e1e5feaac79025af7a1c7d70dbf9a9f4edafa89`.
- Analyzer bundle: `ab8d91e3dc99e02e5b54f24b2a49ee3baf504c1e3b64eb2e5086e859bc55104b`; workflow pipeline: `71d1ee97f6080e8942491df70fcff3d21a803f95d67443fb480e003d461ab290`.

## Evidence storage and scope

Private report and derived evidence: `companion-app/artifacts/bristol-investigation-20260922/`, including the illustrated PDF, exact cohort metrics and retained redacted analysis. DESKTOP-SIM exports are also retained under the private NAS QA folder with `BRISTOL-INVESTIGATION-` names. Paid setup values and personal raw recordings are not checked in.

No new installer or desktop development transport was produced for this bounded investigation. Full feature/button acceptance, active live telemetry and the larger diagnostic UI work remain separate unfinished work.
