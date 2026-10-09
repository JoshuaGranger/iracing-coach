# CONFIRMED

The three principal code contradictions survive review. The largest weaknesses are in claims of identification, experimental admission, and the age analysis.

Read-only throughout. I checked the cited code, registers, reports, pressure ledger, original pressure watcher, and relevant lap rows; recalculations appear below. Python launches failed during sandbox initialization, so I could not independently refit the compressed 1 Hz series or original 10 Hz thermal data. “Matches source” below means source verification, not independent validation of that source’s experiment.

| Claim | Evidence checked |
|---|---|
| **A1: the prediction path bypasses the validation gate.** | [workflow.py:2805][workflow] calls storage; [storage.py:3481][storage] directly calls `build_tire_prediction`; [analysis_engine.py:7342][analysis] displays the three predicted metrics. This path does not obtain a gate verdict. The predictor’s three-observation/three-session minimum and confidence heuristics are not held-out validation. |
| **A2: the tyre context key omits setup fingerprint and simulator build.** | [race_foundations.py:1091][foundations] keys family, car, track/configuration, fixed/open and compound. Neither fingerprint nor build participates. Some supporting statements are incorrect—see below. |
| **A3: banked lateral limit, flat-road longitudinal budget.** | [trackref.py:32][trackref] uses banking in `lat_limit_speed`; `solve_speed` uses `μg√(1−u²)` with `u=(v/v_lat)²`. Independent calculation gives **69.907 m/s** lateral limit; at **59.421 m/s**, planner room **6.782 m/s²**, banked room **9.404 m/s²**: **27.877% low**. |
| Associated planner inconsistencies. | [controller.py:1177][controller] scales friction by pace²; [ksplan.py:129][ksplan] uses horizontal `v²κ` for steering while `trackref.build` uses in-plane acceleration. [lapsim.py:252][lapsim] applies `mu_scale` in the envelope branch but omits it in the ellipse branch. |
| Pressure lattice’s descriptive numbers and rejection of simple stepping/product models. | Independently processed [ledger-all.csv][ledger]: **816 rows**, **788 garage rows**, **31 inferred settings**. The listed 14/20/28/34 psi values reproduce. All **816** live values are off the 0.01-kPa grid; the cited 800 excludes the 16 pit rows. Cumulative stepping reproduces **84/178** transitions. Correct interval arithmetic still gives a maximum **21/31** matches for a single direct-product constant. The 23/31 two-stage result matches the recorded grid search. |
| **The −8-ulp transient exists and lasts approximately five seconds.** | Original [watch-garage.csv][watch] confirms **192.95237731933594**, versus **192.95249938964844**, for **301 consecutive samples** on each affected corner. RF ticks **20128–20428**, RR **22878–23178**; normal value returns on the next tick. Duration **5.016667 s**. |
| Nearest-half-psi pit delivery on tested requests. | Ledger’s **16 parked** requests and [e1-unrounded-lattice.txt][delivery]’s **24 driven** requests support the verdict. This establishes the tested request cases, not all rounding boundaries. |
| **RF steering response: +0.023/+0.027 rad/psi.** | Recalculated from [analysis-report.json][pressure-report]: **0.022851 / 0.026917 rad/psi**, with the quoted 95% intervals. Both balanced halves are monotonic. Passing-input-lap sensitivity retains the sign: **0.022784 / 0.027105**. |
| RF secondary pressure/wear results. | [secondary-report.json][secondary] reproduces **hot = 1.076666 × cold + 1.164271 kPa**, residual SD **0.04160 kPa**; hot increase **44.516875 kPa** over 6 psi. Wear slope **0.038606 percentage points/psi**, endpoint increase **24.989%**; crown changes approximately **−1.46→−1.83°C**. These are local associations. |
| G5 regression point estimates. | Recomputed relevant rows of [relationships/laps.csv][laps]: lap slope **0.00375208 s/kPa**; steering/acceleration slope **0.000193188/kPa**. Multiplying by 3 psi gives **+0.0775685 s/lap** and approximately **+0.0040** steering/acceleration. |
| **IRP model-versus-notes banking discrepancy.** | Applied [trackmodel.py][trackmodel]’s `atan(−cross_slope)` to [irp_track.json][trackjson]. Turn-core means at `e=0` are approximately **7.50° / 7.64°**; at `e=15 m`, **0.95° / 0.75°**. [TIRE-AND-TRACK-MODEL.md][modelnotes] says approximately **12°**. Reference/track lengths reproduce as **1077.237892 / 1083.432006 m**. |
| Vehicle, predictor and proxy coefficients generally match their code citations. | Checked IRP/Kansas reference metadata, `VehicleParams`, `lapsim.Car`, longitudinal equations, gearing, clipping of negative `long_Cc`, predictor weighting/extrapolation, strategy arithmetic, stress weights **0.35/0.35/0.30**, and the five-pair energy diagnostic minimum. Calibration attribution needs correction below. |
| Most inherited register numbers are accurately transcribed. | [R1–R18][relationships] support the fitting offsets, pressure-temperature slopes, build-up fractions, surface regressions, wear coefficients, tread shares, fuel regression, confounding and held-out temperature failure. [F1–F47][findings] and P4/P6 reports support the quoted package effects and failed experimental gates. Exceptions follow. |

# REFUTED OR WEAKENED

| Claim | What the file actually shows | Corrected number / interpretation |
|---|---|---|
| **“Grip vs age: the one solid grip law.”** | [d3_apex_fixed_theta.py][d3] pools controller programmes, assumes 12° banking, constructs normal load from speed and fitted curvature, and does not require samples to be at a measured tyre limit. Its filters admit rear-pressure interventions: G5 runs **107/108/110/111** pass despite changed RR/LR pressures. The controller also deliberately reduces pace with age. | **−7.5%/−7.7% describes this selected, pooled demand statistic. It does not identify tyre-capacity loss.** No defensible corrected causal percentage is available. |
| The age statistic uses **3240/3017** cleaned apex points. | Those are the trimmed regression counts. The age calculation returns to the **untrimmed `bot` array**. The **89,663** count comes from a different scratch analysis with different filtering. | Apex pools: **3346/3131**; displayed age bins 2–70 contain **3112/2897** points. |
| Load-sensitivity span **3.9–6.2%**, presented alongside the fixed-12° analysis. | Those spans come from [d2_apex_load.py][d2], using intercept-derived banking approximately **17.68°/9.86°**, not 12°. | Converting its rounded endpoints consistently to 12° gives approximately **13.05–13.65 / 12.74–13.33 m/s²**, about **4.6% in both turns**. Insufficient identification remains a reasonable conclusion. |
| The failed RF repeat gate can now be treated as passed/admissible. | Both `passes_registered_effect_and_repeat_gate` fields are **false**. Pair disagreements exceed **0.004 rad**. Run 15 also has a failed input lap. A new threshold is a new protocol, not a retrospective pass. | Pooled within-pressure replicate SDs are **0.002048 / 0.002444 rad**; twice these are **0.004097 / 0.004888**, not a demonstrated **0.006-rad** threshold. |
| **+0.078 ± 0.039** establishes “slower-or-null,” and F7’s opposite signs “were noise.” | The uncertainty in [b_rf_pressure.py][b] is **one standard error**, based on nine run means. Compatibility with noise does not establish noise as the explanation. | Estimate **+0.07757**, SE **0.03889**; ordinary OLS **95% t interval −0.01438 to +0.16952 s/lap per 3 psi**. Direction of lap-time effect remains unresolved. |
| Control noise SD is **0.115 s/lap**. | This is the **population SD of four adjacent control differences**, not the SD of control run means. The differences share observations. | Five control means have sample SD **0.07198 s/lap**. Difference SD is **0.11515 population / 0.13297 sample**. |
| RF +6 psi gives a general fixed-pace **+0.036 s/lap** effect. | This correctly describes drive-log **laps 2–8**, excluding the first flying lap. The secondary report also supplies the complete eight-flying-lap IBT outcome. | **+0.03550 s/lap** for the selected seven-lap window; **+0.0227625 s/lap** for the eight-lap IBT mean. State the window. |
| Hot-pressure fit is a recovered law; one surface-temperature-matched pair would decide absolute versus gauge scaling. | The fit reproduces, but [R3][relationships] explicitly leaves gas temperature and volume inseparable. Matching surface temperature does not establish equal gas temperature, fitting state or volume. Pressure itself changes heating. | Keep **1.076666×cold + 1.164271 kPa** as a six-run conditional fit. The approximately **0.897-kPa** endpoint gap from the proposed absolute prediction does not identify its cause. |
| All **31** settings have demonstrated path independence; the transient proves a clamp with no further click. | Modal values are repeatable where revisited. But **45 and 45.5 psi occur once each**. The watcher establishes the transient’s duration, not operator actions or the internal clamp mechanism. | “Repeatability on revisited settings, with two minimum-pressure transients” is supported. Universal path independence and the precise mechanism remain unproven. |
| Direct-product feasible intervals quoted in §3a. | [a2_pressure_float_clamp.py][a2] adds adjacent float32 values **before** converting to double, rounding away the intended midpoint. Several reported intervals incorrectly collapse to zero width. | Correct global lower bound **6.891160964966** at 14 psi; upper bound **6.891159996620** at 32.5 psi. Still disjoint: the rejection survives, its numerical proof changes. |
| **“No simulator build string exists in the identity”; “nothing from the garage sheet” affects matching.** | [analysis_engine.py:1353][analysis] already emits `sim_build`, sourcing `SimMode`, then `BuildType`, then metadata. [race_foundations.py:1251][foundations] also checks setup steering-offset compatibility when using steering work. | The key omission stands. The supporting absolutes are false. Existing `sim_build` provenance also needs validation before treating it as an actual version identifier. |
| Vegas example demonstrates **28% less actual drive force**. | `solve_speed` takes the minimum of tyre room and engine demand. At the example speed, cited IRP/Kansas power coefficients request less than even the erroneous flat budget. | Engine demand approximately **5.47 / 5.25 m/s²**, below **6.78**. The **capacity budget** is 27.88% low; the drive-force clipping difference at this example is **zero**. |
| Ellipse defaults were calibrated to the quoted human lap. | `irp_grip.json` explicitly describes a station multiplier on **`optline.CAR_ENVELOPE`**, an empirical-envelope model, not the default ellipse. The notes/reference metadata also disagree with the calibration label’s lap time. | Reference metadata: **22.433333 s**, versus calibration prose **22.443 s**. Separate ellipse assumptions from envelope calibration. |
| Human-clean envelope is shaped by the bot’s **1.30-rad cap**. | [gripmap.py][gripmap] builds `g_g_v_human_clean` using the human-file mask. | The bot cap does not directly constrain that human-only table. Its demand-envelope caveat remains valid. |
| Vegas default crab discrepancy proves its reference is **0.006 rad too high**. | Arithmetic discrepancy exists, but push and fixed-pace populations differ. [P4][p4] itself gives default fixed-pace crab **0.0436/0.0431**, versus push-run **0.0524/0.0556**, evaluated at the same reference acceleration. | F20 prediction is **0.0498**: gaps **0.0062/0.0067 rad**. This demonstrates a transfer/selection issue, not an isolated reference error. |
| P6’s fixed/push summaries imply two-point crab gradients **0.0010–0.0014 rad/(m/s²)**. | [LV-P6-balance-ay16.txt][p6balance] evaluates the reported crab fits at **16 m/s²**, including the push-run rows. They are not two different acceleration coordinates. | No such two-point gradient is identified from those summary values. |
| Steering comparisons use the same `a_y` as the force equations. | [tools/balance.py:89][balance] uses **`v × yaw_rate`**; the force analysis uses accelerometer/road-plane quantities. `balance.py` also defines crab as **−atan2(vy,vx)**, unlike `gripmap.py`’s signed body-slip variable. | Label the acceleration proxy and sign convention explicitly. Equal numeric values across these analyses are not automatically matched physical states. |
| Several setup numbers/interventions. | Raw laps and R11 contradict three transcriptions; [P6 setup comparison][p6setups] contradicts rear toe being the only distinctive setting. | IRP LF camber **+6.0°, not +4.7°**; brake-bias comparison **68 versus 64%, not 65%**; **RR +3/+6 psi**, not LR, accompanies LR wear **0.062→0.085/0.093%/km**. G also differs in ballast, crossweight, bars and pressure. |
| Miscellaneous ranges are presented too narrowly. | [Baseline tuning][baseline], R1/R12 and P6 retain distinctions omitted by the synthesis. | Vegas second-turn radius IQR **230.3–234.9 m**; whole-°F track samples **149541/149543**; joint-bootstrap exponent intervals extend to **2.88–3.75**; Vegas RF carcass values across cited tread bands/setups span **115–131°C**. |
| Utility has no conversion and requests psi through the SDK. | [pitservice.py:41][pit] uses **6.894757**, rounds psi to integer kPa, then broadcasts integer kPa. | User input is psi; **SDK request is kPa**. The documented delivered lattice values still hold. |
| Ranked priorities and experiment costs are established by the evidence. | Force identification is baseline rank 1, garage mapping rank 2, yet the summary reverses their execution order. Probe validity is rank 13 despite being a prerequisite. The parked-cell arithmetic also fails. | Keep coaching gate work first; promote frames/banking, planner consistency and probe validity ahead of force/age attribution. **8×8 + 24 = 88 minutes**, not approximately 70, for the stated parked traces unless overlap is explicitly designed. |

# MISSING FROM THE SYNTHESIS

- **Use the stable garage fingerprint.** `setup_fingerprint` includes last-stop temperatures, wear and hot pressures; repeated runs of one setup can hash differently. The proposed cohort fix should use the existing `garage_setup_fingerprint`, plus verified build provenance and an explicit unknown state. Otherwise “exact setup matching” can destroy cohort occupancy.

- **Controller and intervention membership must accompany the age statistic.** Record pace schedule, caps, guard state, tyre preparation, all four cold pressures, setup and session. Exclude or stratify interventions before estimating age effects; use run/session-level uncertainty.

- **The supposed three independent RF balance measures are not independent experiments.** Steering angle, steering/acceleration and time at the steering cap are related outcomes of the same controller. They strengthen a local tightening interpretation without supplying three independent causal replications.

- **Per-tyre load identification is still missing.** Cross-track comparisons change surface, aero, speed and operating state together. Shock displacement × spring rate needs motion ratios, support engagement, preload and load-path treatment before becoming a corner-load measurement. Static garage mapping alone does not identify dynamic load transfer.

- **The banking test needs nuisance terms.** The proposed direct `VertAccel` inversion omits the vertical-curvature and grade terms already present in `lapsim`, and requires consistent chassis/road frames. Higher sampling rate alone does not settle banking.

- **The experiments do not fully separate thermal recovery from wear.** A fresh-tyre stop changes both. The worn-cool-reheat arm remains necessary. Static x0 parked weather, with no demonstrated changing thermal input, also cannot by itself identify a 25-minute environmental lag.

- **Gate execution requires an evaluation pipeline.** `evaluate_candidate` accepts supplied scores; it does not create event-held-out splits or train the baselines. Cohort diagnostics, event identity, scoring and uncertainty calibration must precede wiring its verdict into the display.

# SUMMARY

All three principal code contradictions are confirmed; the Vegas budget shortfall is 27.88%.
The −8-ulp, 5.0167-second transients are confirmed; universal path independence and clamp mechanism are not.
RF steering, hot-pressure and wear numbers reproduce locally, but the registered admission gates remain failed.
The G5 lap-time effect includes zero at 95%; the −7.5% age statistic is not an identified grip law.
Prioritize gate wiring, consistent frames/banking, planner correction and instrument validity before new force-law claims.

[workflow]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/workflow.py:2805>
[storage]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/storage.py:3481>
[analysis]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/analysis_engine.py:1353>
[foundations]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/race_foundations.py:1091>
[trackref]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/trackref.py:32>
[controller]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/controller.py:1177>
[ksplan]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/tools/ksplan.py:129>
[lapsim]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/lapsim.py:252>
[ledger]: <N:/iRacing Temp/claude-drops/e1-20261006/ledger-all.csv>
[watch]: <N:/iRacing Temp/claude-drops/e1-20261006/watch-garage.csv>
[delivery]: <C:/Users/joshu/source/repos/iRacing-utility/.orchestrate/use-the-remote-engineer-tools-to-connect-to-desk-20261006T151235Z/results/e1-unrounded-lattice.txt>
[pressure-report]: <C:/Users/joshu/source/repos/iRacing-utility/.orchestrate/use-the-remote-engineer-tools-to-connect-to-desk-20261006T151235Z/results-queue/tuning-sweep/analysis-report.json>
[secondary]: <C:/Users/joshu/source/repos/iRacing-utility/.orchestrate/use-the-remote-engineer-tools-to-connect-to-desk-20261006T151235Z/results-queue/tuning-sweep/secondary-report.json>
[laps]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/data/relationships/laps.csv>
[trackmodel]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/trackmodel.py:362>
[trackjson]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/model/irp_track.json>
[modelnotes]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/TIRE-AND-TRACK-MODEL.md>
[relationships]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/data/relationships/FINDINGS.md>
[findings]: <C:/Users/joshu/source/repos/iRacing-utility/lab-notes/irp-bot-study/FINDINGS-REGISTER.md>
[d3]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/_work/d3_apex_fixed_theta.py>
[d2]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/_work/d2_apex_load.py>
[b]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/_work/b_rf_pressure.py>
[a2]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/_work/a2_pressure_float_clamp.py>
[gripmap]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/gripmap.py>
[p4]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/p4/LV-P4-declare.txt>
[p6balance]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/p6/LV-P6-balance-ay16.txt>
[balance]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/tools/balance.py:89>
[p6setups]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/p6/setups-BSG.md>
[baseline]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/BASELINE-TUNING-codex.md>
[pit]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/pitservice.py:41>