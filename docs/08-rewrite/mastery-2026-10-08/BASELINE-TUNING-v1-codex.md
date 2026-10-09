# WHAT CHANGES FROM v0

**BASELINE TUNING v1 — 9 October 2026**

**Build a complete baseline from a coherent setup, calculate the track’s demands correctly, change only what the evidence supports, and check the result in the next recording.** Near-zero iteration comes from identifying reusable car responses before arriving at the next track.

This is a read-only design. No files were changed. Experimental findings below come from the supplied files; new budgets and acceptance targets are explicitly marked **proposed**.

| Topic | v1 decision |
|---|---|
| **RF pressure direction** | Correct the audit’s “unresolved” verdict. In the tested **IRP, R setup, bot, 28–34 psi** domain, increasing RF pressure makes the car tighter. Steering-wheel response was **+0.0229 rad/psi**, reported interval **0.0175–0.0282**, in T1–2, and **+0.0269**, interval **0.0254–0.0285**, in T3–4. Lap-time evidence remains slower-or-null: approximately **+0.08 ± 0.04 s/lap per 3 psi** at the limit. v0’s retain-source-pressure default remains appropriate outside this domain. Raising RF pressure is not a supported cure for push here. [Synthesis, pressure findings][S-RF] [Synthesis, coefficients][S-FORCE] |
| **Pressure lattice** | Replace the audit’s adjustment-path explanation. The settled lattice is **path-independent across 31 settings and 788 garage observations**; the exceptional minimum-pressure value was a transient. Use the measured lookup where available, with **6.8911603 ± 0.0000007 kPa/garage-psi** as the approximate conversion. The exact floating-point expression remains unidentified. Keep physical SI conversion separate. [Synthesis, lattice reconciliation][S-LATTICE] |
| **Banked force budget** | Promote v0’s warning to a prerequisite for predictive planning. Replace both flat-road `μg` and `u=(v/v_lat)²`. In the synthesis’s illustrative banked case, available longitudinal acceleration is **9.40 m/s²**, versus the old planner’s **6.78**, approximately **28% too low**. This is a worked example, not a universal correction factor. [Synthesis, verified contradictions][S-CODE] |
| **Predictor gate** | The bypass is confirmed. Enforce the gate before issuing any `Predicted` tyre-life, capability-pace or tyre-age-cost result. Add exact setup and simulator-build identity to matching. Existing observation counts and similarity-based confidence do not establish prediction accuracy. [Synthesis, verified contradictions][S-CODE] |
| **Ageing** | Add the observed **7.5–7.7% decline in apex demand/normal-load ratio**, comparing tyre ages **2–5 with 46–70 laps**. Preserve its bot, setup and assumed-banking context. It is an ageing observation, not an isolated wear law or permission to reduce every tyre’s friction coefficient by that percentage. [Synthesis, age analysis][S-AGE] |
| **IRP geometry** | Treat **7.5° from the surface model versus approximately 12° in the notes** as unresolved. Reconcile native-rate acceleration, yaw, trajectory and elevation before publishing an IRP grip-utilisation forecast. The two angles are sensitivity scenarios, not statistical bounds. [Synthesis, track model][S-TRACK] |
| **Crab and setup coupling** | Require setup-specific crab references. The default Vegas reference disagrees with another measurement by about **0.006 rad**. Camber results still identify a package; equal track-bar movement, rake changes and rear-toe changes remain separate interventions. [Synthesis, reconciliation][S-CODE] |
| **Product and application scope** | Extend the existing Baseline workflow. v0 overstated the `.sto` restriction: Joshua permitted generating **new** files while forbidding overwrites; decoding/application remain parked capabilities. v1 delivers a source reference and garage worksheet without making file generation a dependency. [Requests Q104–Q110][Q-F] [Built ledger, setups][B-SETUPS] |

The central v0 choices survive: preserve whole setup architectures, separate race and qualifying, use geometry to calculate demand rather than invent garage coefficients, and leave unsupported forecasts unavailable. [v0 generator][V-GENERATOR]

# BASELINE GENERATOR v1

**Classification used in every step:**

- **Physics:** equilibrium, force projection, kinematics or a stated physical model.
- **Prior:** an observed package tendency, local empirical response or borrowed parameter.
- **Assumption:** an objective, admission rule, approximation or decision policy.

These classifications describe the calculation. Every output also carries its evidence strength, uncertainty and applicability.

**Inputs and their actual availability**

| Input | Read from Coach | Treatment |
|---|---|---|
| Recordings | Archived native-rate telemetry, embedded `CarSetup`, laps/runs, tyre-set lifecycle, conditions, fuel, traffic and damage screening | Analyse native samples; display bins are not the identification dataset. Preserve driver versus bot provenance. |
| Garage snapshots | Passive garage capture and `.htm` exports | Separate committed controls, calculated readouts and running measurements. Preserve units, capture time and preparation state. |
| Setup library | Setup identities, versions, source artifacts, parameter comparisons and linked recordings | A readable vector and a loadable artifact are different capabilities. An opaque `.sto` cannot supply invented values. |
| Track information | Existing recording-derived geometry/profile; imported track edges and surveys where available | Plan-view edges supply boundaries and horizontal geometry. Banking, elevation, bumps and boundary meaning require independent evidence. |
| Model metadata | Existing setup fingerprints and evidence records; new build, calibration and experiment metadata | Exact simulator-build identity is currently missing from the audited tyre context. Add capture/provenance support; do not substitute season or IBT format version. |

The library, garage capture, geometry and tuning foundations exist. The physical generator, reliable forecast gate integration and complete later-recording assessment require new work. [Built ledger, data delivery][B-DATA] [Built ledger, setups][B-SETUPS] [Synthesis, identity defect][S-CODE]

**1. Resolve the event and intended result — Assumption**

Read exact car path, track/layout identity, fixed/open status, season, available build evidence, selected source and intended use.

Produce separate objectives:

- **Qualifying:** performance over the specified qualifying sequence, with its fuel, preparation and cooling requirements.
- **Race:** performance and stability over the specified stint horizon.

Joshua’s earlier request used a **30-lap race objective**; that is a supported example, not a universal default. Baseline generation must work without a target-track recording. Progressive Tuning continues to require recorded evidence. Fixed sessions produce no garage-change recommendation. [Requests Q102–Q105, Q124–Q125][Q-F]

**Uncertainty:** unresolved layout blocks physical transfer. Unknown build permits a clearly provisional source worksheet, but blocks a claim of current-build calibrated prediction.

**2. Reconstruct the evidence and tyre-set history — Physics + Assumption**

Normalize units and coordinate conventions. Associate each run with the actual control fingerprint, preparation, tyre set, fuel state and conditions.

Use service events and odometer behaviour to distinguish:

- Fresh reset tyres from fresh pit-service tyres.
- A new run from a new tyre set.
- Continuous surface/pressure telemetry from delayed carcass/wear endpoints.

The synthesis reports reset-set surface temperature near **air +0.07 ±0.04°C**, versus pit-service sets near **air +13.24 ±0.20°C** in the measured conditions. Those preparations cannot be pooled blindly. It also documents stale stall readings and a post-garage rear-channel swap; resolve affected observations against service/readout evidence rather than reversing all rear channels globally. [Synthesis, measurement layer][S-MEASUREMENT]

**Uncertainty:** store timestamp, orientation and service-association quality. An ambiguous endpoint is excluded from fitting that endpoint law; it is not converted into a continuous wear trace.

**3. Build the track and feasible-line envelope — Physics + Prior**

Align track stations, recording trajectories, edges and surveys. Calculate curvature over several smoothing scales and across the lanes actually supported by evidence.

For each segment, retain:

- Horizontal curvature, banking, grade and vertical curvature.
- Usable width and the meaning of each boundary.
- Observed or prior speed range.
- Braking, throttle and sustained-corner exposure.

For IRP, reconcile banking using native-rate `VertAccel`, lateral acceleration, heading/yaw curvature and elevation, accounting for sensor frame, vehicle pose and aero. If ambiguity remains, retain both banking scenarios and withhold results whose conclusion changes between them.

**Uncertainty:** report lane/smoothing/source spread as a **sensitivity range**. Do not call it a confidence interval. Missing elevation leaves banking and bump demands unknown. The Vegas edge-to-wall disagreement of approximately **0.42 m** demonstrates why an edge cannot silently become a wall or legal boundary. [v0 geometry treatment][V-GENERATOR] [Synthesis, track model][S-TRACK]

**4. Calculate one consistent force budget — Physics; capacity parameters are Prior**

For a level, steadily banked segment:

\[
a_y=v^2\kappa\cos\theta-g\sin\theta
\]

\[
a_n=g\cos\theta+v^2\kappa\sin\theta
\]

Use a consistent three-dimensional road frame for grade and vertical curvature. Add aerodynamic normal force separately; do not invent a downforce map.

For an assumed elliptical capacity model, define \(N/m\) as normal load per unit mass, including supported aero:

\[
u_y=\frac{|a_y|}{\mu_y(N/m)}
\]

\[
|a_{x,\mathrm{tyre}}|
\leq \mu_x(N/m)\sqrt{\max(0,1-u_y^2)}
\]

Apply propulsion/brake limits, drag and grade consistently when converting tyre force into vehicle acceleration. Recompute demand and normal load when pace changes.

Correct the utility planner/controller’s related inconsistencies together: banked allocation, pace scaling, steering feed-forward frame, grip scaling across ellipse/envelope branches, and grade equilibrium. [Synthesis, verified planner defect][S-CODE] [Audit, related defects][A-CONTRADICTIONS]

**Uncertainty:** geometry-derived demand can be displayed as `Derived`. Available grip and maximum speed remain conditional on the capacity model. A recorded demand envelope is not automatically the tyre’s capacity envelope.

**5. Select a coherent source and recover its static state — Prior + Physics**

First exclude incompatible car/build, intent, preparation and suspension-mode combinations. Then rank eligible **whole packages**:

1. Exact-layout, compatible-build packages with relevant stint evidence.
2. Exact-layout packages with weaker performance evidence.
3. Compatible donors with similar load, speed, braking, transition and cooling exposure.
4. The authenticated current default.

Retain a default-like, support-spring or coil-bind architecture as a complete family. Do not combine independent median settings from unrelated packages. Fast-associated population values remain priors, not causal gains. [v0 source selection][V-GENERATOR]

Read corner weights directly when available. Otherwise, with total weight \(W\), front fraction \(n\), left fraction \(l\), and crossweight fraction \(c=(RF+LR)/W\):

\[
(W_{LF},W_{RF},W_{LR},W_{RR})
=\frac W2(n+l-c,\ n-l+c,\ -n+l+c,\ 2-n-l-c)
\]

Nose weight and crossweight alone are insufficient.

**Uncertainty:** retain source precision and readout variability. A package with missing controls is incomplete; an authenticated loadable source can still be offered, but its missing parameters cannot be represented as a complete generated vector.

**6. Construct supported garage adaptations — Physics + Prior**

Represent controls by \(u\), static readouts by \(y\), and the measured local garage response by:

\[
\Delta y \approx J\,\Delta u
\]

The garage-map experiment supplies \(J\), legal tested increments, coupling, reversibility and operating-mode boundaries. Solve only within that measured neighbourhood, choosing the smallest set of legal changes that meets the supported target.

Do not solve an underdetermined adjustment as though it were unique. Crossweight is a readout target; “crossweight via LR perch” is not a second independent control.

| System | v1 adaptation rule |
|---|---|
| Pressure | Use the settled garage lattice. In the admitted IRP domain, increasing RF pressure predicts tighter balance. Do not extrapolate below the tested garage minimum or transfer the coefficient to another track/setup. |
| Platform | Preserve spring, packer, bar and support-engagement architecture until dynamic maps support a change. Shock-spring rate is not automatically wheel rate. |
| Wedge, ballast and roll balance | Require the garage response and a supported dynamic response. Static load arithmetic alone cannot identify the best crossweight. |
| Camber, caster and toe | Preserve measured geometry dependencies. Do not subtract banking from garage camber or attribute the camber package’s gain to camber alone. |
| Track bars and rear steer | Treat equal-end movement, rake and toe independently; restore specified coupled readouts. |
| Gearing, brake bias, tape and dampers | Retain the source unless legal settings and relevant RPM, braking, cooling or transient evidence support adaptation. |

**Uncertainty:** propagate garage-map repeatability and model residuals. If they span a support-engagement boundary or reverse the expected benefit, retain the source.

RF coefficients in the synthesis are **steering-wheel** measurements. Coach’s driver-facing steering should remain ratio-independent; numerical conversion requires verified steering ratio/kinematics. Otherwise display the directional evidence. [Synthesis, pressure response][S-FORCE] [Requests, steering principle][Q-PRINCIPLES]

**7. Predict tyre state only within identified support — Physics structure + Prior**

Keep separate state variables for surface temperature, carcass/bulk temperature, gas state, pressure, wear and tyre age.

The gas-law candidate is:

\[
P_g(t)+P_{\rm atm}
=(P_{g,0}+P_{\rm atm})
\frac{T_{\rm gas}(t)}{T_{\rm fit}}
\frac{V_0}{V(t)}
\]

Use absolute temperatures. Gas temperature and volume are latent; surface or carcass temperature cannot silently replace them.

Existing comparable trajectories may support empirical predictions after validation. Preserve these distinctions:

| Evidence | Permitted use |
|---|---|
| RF hot-pressure slope **7.42 kPa per garage-psi**, residual SD **0.04 kPa** in the short fixed-pace sweep | Local response evidence; not a general forecast interval or proof of the gas equation. |
| RF wear increased approximately **25% over +6 psi** in that sweep | Local treatment result, conditional on preparation, exposure and controller. |
| Long-run pressure extrapolation missed by approximately **4.6 kPa** | Demonstrated model failure to address, not a universal uncertainty allowance. |
| RF surface-temperature regression residual SD approximately **6.1°C** | Fitted-domain residual information, not a new-track prediction interval. |
| Apex ratio declined **7.5–7.7%** with age | Age-conditioned observed envelope; no isolated attribution to wear, pressure or heat. |

All figures are from the [synthesis’s state models][S-STATE], [pressure analysis][S-RF] and [age analysis][S-AGE].

**Uncertainty:** report prediction intervals from whole-run/event validation. If no defensible interval exists, return `Unavailable`. Never convert “hotter” into “worse” or crown temperature into an automatic pressure command.

**8. Rank candidates according to the actual objective — Assumption + Physics constraints**

Reject candidates that violate verified garage constraints, operating-mode limits or supported platform/cooling/stability requirements.

Where performance prediction is validated:

- Race ranking uses the complete intended stint, including deterioration and stability.
- Qualifying ranking uses the declared short-run preparation and sequence.
- Evaluate correlated uncertainty in geometry, initial state and model coefficients.
- Prefer a candidate whose advantage survives that uncertainty.

Where performance prediction is unavailable, rank by source compatibility, relevant evidence and demonstrated feasibility. Return the result as a **source-based baseline**, without a fabricated predicted gain.

**Uncertainty:** if candidate differences are smaller than validated prediction/repeatability uncertainty, retain the stronger reference. Statistical indistinguishability is not proof of equal performance.

**9. Enforce prediction admission at the producer boundary — Assumption**

Run cohort diagnosis before model selection. Include exact setup fingerprint, build evidence, car, layout, compound and preparation; keep driver/controller identity available for matching.

For the existing tyre predictor, require:

- At least **8 held-out events**.
- More than **5% error improvement** over both laps-only and cohort-median baselines.
- Interval coverage within **0.10** of the declared nominal coverage.
- Identical held-out event identities for candidate and baseline comparisons.

These are existing gate requirements, not new performance guarantees. Enforce them on every consumer, including cached results. [Synthesis, gate defect][S-CODE]

Train and validate on whole events. For cross-setup and cross-track claims, additionally withhold complete setups and tracks. Explicitly validated transfer models may learn across setups; the historical “same setup” predictor must not silently pool them.

**Uncertainty:** pass/fail is per model, metric and domain. A passing tyre-life model does not validate a balance or lap-time model. Unknown historical builds remain unknown.

**10. Emit the complete worksheet and frozen next-run test — Assumption**

Return one selected package per objective, containing:

- Every applicable control, including retained values, units and provenance.
- Derived readout targets separately from controls.
- Ordered garage operations and necessary compensating adjustments.
- Source/rollback identity and current compatibility/tech-check status.
- Supported predictions, their intervals and unavailable reasons.
- Expected actual setup fingerprint and verification tolerances.
- A frozen next-recording assessment plan.

Application remains manual in v1. Garage snapshots can verify the applied vector; the next recording verifies what was driven.

The assessment produces **two separate verdicts**:

1. **Prediction:** supported, contradicted or unassessable.
2. **Benefit:** improved, worse, not established or not comparable.

A car behaving as forecast does not prove that the setup made it faster. Without a comparable reference, the first recording establishes behaviour and feasibility.

The existing representative-run minimum is **6 clean green laps, with at least 2 in each run third**. That cannot validate a substantially longer race horizon. Compare matched tyre age, conditions, fuel and supported operating points; preserve driver feedback separately. [v0, next-recording contract][V-CHECK]

# THE EXPERIMENT PROGRAMME

**Preserve the synthesis’s first five experiments, then identify the specific responses the generator lacks.** Admission is based on physical response and held-out prediction before small lap-time rankings.

All driven comparisons retain every attempt, pin build/setup/controller versions, use solo offline sessions, and record interventions, resets, repairs and preparation. Matched conditions mean the same declared track-temperature bin, static weather and time, preparation, and first-timed-lap air within **0.2°C**. Temperature mismatches are not repaired using a cross-session pace correction. [Synthesis, experiment protocol][S-FIVE]

**Before simulator time: existing-data work and tools**

- Enforce the predictor gate; diagnose archive coverage with setup/build identities.
- Refit setup-specific crab from the existing high-rate logs; reconcile frames and IRP banking.
- Correct the banked planner and unit semantics.
- Build a transactional **garage-map runner** around garage capture: full before/after vectors, settled readouts, restoration, tech flags and attempt records.
- Build the **drive-in pit path** needed to preserve tyres and fuel. Demonstrate keep-tyres/no-fuel, tyres-only and fuel-only service without Esc/Test resets. **Proposed smoke budget: 15–25 sim minutes**, excluding coding.
- Correct Lab provenance before new garage experiments contaminate the ordinary setup library; the ledger records this as an open issue. [Built ledger, backlog][B-OPEN]

**1. `garage-map-1` — controls to static state**

**Block:** Preserve the synthesis’s positive/negative one-click perturbations for cambers, perches, track-bar ends, preloads and packers; restore the complete baseline between interventions. Repeat twice with reversed order. Capture the full **84-leaf** readout and cold pressures. Crossweight via LR perch is one intervention with several outputs.

**Minutes:** approximately **45 garage minutes**, zero laps; budget another block if the second track or operating mode behaves differently.

**Prerequisites:** garage-map runner; authenticated baseline; settled capture; complete garage visibility. No drive-in pit path required.

**Law identified:** local control-to-readout coupling, reversibility, quantisation and mode changes. Build the isolated camber candidate for LV-P6C with the original crossweight and preloads restored. [Synthesis, experiment 1][S-FIVE]

**2. `pressure-balance-2` — confirm pressure response**

**Block:** Two twelve-run sub-blocks, each using **ABC/CBA/ABC/CBA**. RF **28/31/34 psi**, then LF **14/17/20 psi** with RF held at 28. Each run is out-lap plus **8 laps at 0.93 pace**, guard logging only. Primary outcome: steering at the lowest common supported p95 lateral acceleration per corner complex. Secondary outcomes: pressure, wear endpoint, tread distribution and lap time.

**Minutes:** approximately **120**.

**Prerequisites:** fresh pit-service sets, verified delivered pressures, matched temperature and overlapping measured fuel state. A reset is not an interchangeable preparation. Equal commanded pace must also produce comparable achieved line and forcing.

**Law identified:** local pressure-to-balance and state response. Freeze the repeatability threshold from the original replicate data before new runs, using the synthesis’s **2× original SD** rule; distinguish SD from pairwise spread. Require the registered RF endpoint effect above **0.010 rad**. Do not adjust the gate after seeing the rerun. [Synthesis, experiment 2][S-FIVE]

**3. `fit-temp-cells-1` — preparation and fitting temperature**

**Block:** Preserve **2 ambient levels × 2 preparations × 2 repeats**, each **8 minutes**, reversing order in the second half. Record the first native sample, the specified hold/move sequence, and a separate **24-minute** continuous trace. Control clouds and record engine/thermal state during planned stationary periods.

**Minutes:** **88 minutes minimum** from the specified durations; **proposed booking 90–110**. The synthesis’s approximately 70-minute estimate does not cover the stated blocks.

**Prerequisites:** verified parked service path, zero-fuel tyre service, native-rate capture and controlled weather. If ambient is changed through time of day, record that confounding explicitly.

**Law identified:** preparation-dependent fitting temperature and evidence for offset, fixed-temperature or history-dependent behaviour. The continuous trace identifies lag only if it contains sufficient thermal excitation. [Synthesis, experiment 3][S-FIVE]

**4. `thermal-steps-2` — state dynamics**

**Block:** **4 runs × 12 timed laps**, using low/high/low and high/low/high sequences at **0.94/0.96** pace. Freeze models on the first pair and predict the second. Finish with the **60-second same-set stationary tail without Esc**.

**Minutes:** approximately **55**, including smoke.

**Prerequisites:** fresh pit sets, matched conditions, verified tyre identity and achieved forcing within **0.002** of the registered target without slides.

**Law identified:** pressure/temperature response to time, distance and achieved work; one-state versus delayed multi-state behaviour. This does not independently identify the grip consequence of every thermal state. [Synthesis, experiment 4][S-FIVE]

**5. `tyres-only-stop-1` — replacement benefit at matched fuel**

**Block:** Four **ABBA** trajectories: A retains tyres; B replaces all four. Each has **20 timed laps**, two capped approach laps, equal-dwell stop, then two capped laps and six timed laps. No fuel addition.

**Minutes:** approximately **70**, after the pit path is working.

**Prerequisites:** drive-in pit path; first post-stop fuel matched within **0.10 L**; verified old-set persistence in A and new-set identity in B; matched conditions.

**Law identified:** tyre-replacement effect at matched fuel. Replacement also changes thermal state, so this alone does not separate permanent wear from reversible thermal fade. [Synthesis, experiment 5][S-FIVE]

The first five occupy approximately **380–400 simulator minutes**, including garage and parked work, before launch/debug overhead and the pit-path smoke. This is a planning sum of the budgets above.

**Generator-specific additions, in order**

The following designs and expanded budgets are **proposed**. Existing cost anchors are identified in the synthesis’s ranked programme. [Synthesis, remaining experiments][S-RANKED]

| Order | Block design | Sim minutes | Prerequisites | Law or capability admitted |
|---|---|---:|---|---|
| **6. Measurement and limit instrument** | Short straight/opposite-direction steering checks if existing data cannot settle signs. Then repeated identical probes with steering cap and shaping varied separately, balanced in order. Register the onset classification before testing. | **5 + 30** | Correct force frames; setup-specific crab; fixed controller versions; fresh matched sets. | Separates controller saturation from vehicle instability. Until repeatability passes, at-limit setup rankings remain unavailable. |
| **7. Dynamic platform** | For each relevant suspension mode, test baseline and one-step packer/support changes at several achieved normal loads; reverse order and restore static pose. Hold back a complete run/configuration. | **75–100** | Garage map; validated travel/ride-height channels; matched preparation; functioning instrument. | Support engagement, travel limits, effective stiffness and platform response. Spring rate × shock deflection remains a proxy until motion ratios and support forces are calibrated. |
| **8. Force/load and combined demand** | Retain the proposed Vegas pace levels **0.70/0.76/0.82** and IRP **0.90/0.93/0.96**, counterbalanced with fresh matched sets. Add controlled brake/drive sweeps and within-track lane/load contrasts where feasible. | **60 + 40** | Resolved geometry; dynamic load estimates; matched tyre state; controller-limit exclusions; calibrated wheel kinematics where available. | Local load sensitivity and combined-force boundary. Cross-track differences alone cannot separate surface, aero and load. Admit an effective axle/vehicle map if individual tyre laws remain unidentifiable. |
| **9. Isolated balance controls** | Run LV-P6C camber-only and LV-P6S rear-toe work; then separate equal-end bar movement from rake. Use baseline/intervention/intervention/baseline blocks, restoring mapped static side effects. Add wedge or roll-stiffness perturbations only in a fixed support mode. | **90–150** | Garage map; comparable achieved load; setup-specific crab; matched conditions. | Camber, rear thrust, bar geometry and load-transfer responses that the generator can apply independently. Unsupported package decomposition remains prohibited. |
| **10. Reversible heat, persistent degradation and fuel mass** | First compare worn-cool-restart and fresh-set trajectories at matched fuel and reheat exposure. Separately compare same-tyre fuel-only service with equal-dwell no-fuel service in balanced blocks. | **60 + up to 150** | Drive-in pit path; tyre identity; no resets; thermal-state evidence; adequate fuel contrast. | Reversible thermal response, persistent set degradation, and fuel-mass effects. These replace the confounded age/fuel explanation. |
| **11. Power, losses and cooling** | Matched pull/coast passes by gear and direction where feasible. Then race/qualifying tape blocks at matched pose, including the intended race-length cooling exposure. | **20 + 60–100** | Verified gearing; grade/wind context; stable platform; matched preparation; engine-temperature capture. | Conditional power/drag/engine-braking/scrub separation, tape balance and cooling feasibility. A better regression must also predict held-out behaviour. |
| **12. Wear dose and remaining state transfer** | Match total exposure using smooth versus peaky work, in reversed blocks, measuring complete out/timed/in-lap wear. Add the matched-temperature pressure pair as a gas-state discriminator. Run rubber contrasts only after weather matching succeeds. | **55–80**, plus **15** for pressure; **80 conditional** for usage | Reliable endpoints; thermal model; pit path; matched temperatures; recorded pressure and fuel. | Wear functional form and pressure-state discrimination. Matched surface temperature alone does **not** establish equal gas temperature or volume. Rubber remains a context restriction if weather cannot be matched. |
| **13. Frozen generator challenge** | At withheld events, compare exact-track default, best eligible source and generated package. Two counterbalanced stints per arm, equal preparation, specified race horizon and separate qualifying sequence where claimed. Freeze the model before the event. | **Proposed 90–130 per event** for a race block; extend for precision and Q testing | All models used by that candidate admitted; actual setup verified; human-relevant pace coverage; automatic assessment working. | Whether generation reduces destination-track changes while preserving or improving performance. |

If native recordings cannot resolve important geometry, insert a **proposed 15–30-minute multi-lane survey** before affected force/platform experiments. Exact pressure float-expression recovery is lower priority: a stable lookup already supports the generator.

**Admission and stopping rules**

A physical response can be admitted without proving a tiny lap-time gain. Freeze the law, predict a whole held-out block, and require the sign and uncertainty to survive replication. Keep controller-limited and failed attempts in the record.

The audit’s historical precision estimate was approximately **19 runs per arm for a 0.05 s/lap target**. Two repeats can screen an effect; they cannot automatically prove a small pace advantage. [Audit, experimental precision][A-PRECISION]

**Proposed definition of near-zero destination iteration:** at least **90%** of held-out target events require no more than one primary corrective change after the first representative recording, while meeting a predeclared performance non-inferiority margin and all stability/cooling constraints. A starting performance margin could be **0.2% of intended-stint time**, explicitly a v1 policy assumption to test against practical repeatability.

Evaluate against both the default and best eligible source. Use at least the existing **8-event** minimum before promoting validated prediction claims; report the uncertainty of the iteration-success rate separately. This demonstrates performance against tested alternatives, not a global optimum.

# COACH IMPLEMENTATION

**Extend the current producer → projection → route → screen path, and complete the feedback loop.** The ledger shows that Starting Tune already calls `build_open_setup_package`, while Progressive Tuning already has recommendation and outcome jobs. Saved driving trials and their later-recording checks are not currently wired into the web product. [Built ledger][B-DATA] [Built ledger, setups][B-SETUPS]

Sizes describe scope: **S** is a bounded correction, **M** spans a contract and its consumers, **L** contains substantial modelling or new workflow logic.

| Workstream | Size | Concrete deliverable | Completion evidence |
|---|---:|---|---|
| **Truth corrections** | **S**, with build capture **M** | Separate garage/SI pressure conversion; enforce predictor verdicts; add setup/build provenance and invalidate unsupported cached predictions. Correct planner mechanics in the utility before using its new experiments. | A missing/failed gate cannot produce `Predicted`; unknown build remains unknown; banked reference cases reconcile. |
| **Backend producer** | **L** | Add `baseline_generator.py` and a versioned baseline-generation contract, called by the existing starting-package workflow. Implement input admission, whole-package selection, demand calculation, legal adaptations, uncertainty and complete worksheet output. | Deterministic operation without AI/network; complete supported controls; explicit incomplete/unavailable states; fixed-session enforcement. |
| **Projection fields** | **M** | Add a standalone baseline view and an assessment view. Extend `coach-views`, wire types, schemas and consumed-field declarations so every displayed field survives projection. | Producer-to-browser fixture proves values, evidence, intervals and reasons arrive unchanged. No UI physics. |
| **Route and persistence** | **M** | Extend the existing baseline request/job; persist immutable package versions, expected setup and assessment plan. Add retrieval and recording-check routes. | A generated baseline reopens after restart; analysis completion can assess it; retries do not duplicate packages or outcomes. |
| **Web screen** | **M** | Replace the current form-and-tiles result with one baseline workspace: context, selected package/worksheet, real track evidence, next-run checks and result. | All critical decision context visible together; no driving-input accordions; no unsupported numeric claims; pointer interaction remains local. |
| **Next-recording evaluator** | **L** | Reuse tuning screening and outcome machinery; compare expected versus actual setup, eligibility, forecast and benefit. Attach results to the baseline and observed setup version. | Tests distinguish setup mismatch, short exposure, inconclusive benefit and contradicted prediction. Repeated failed recommendations are suppressed. |
| **Identification tools and admitted models** | **L**, staged | Garage-map runner, drive-in service path, corrected instrument and versioned local response maps from the programme. | Each model has a frozen registration, applicability, held-out results and invalidation rules. Research results enter production only through that record. |

**Backend and projection contract**

Use a proposed `baseline-plan-v1` result with these required blocks:

| Block | Required contents |
|---|---|
| `identity` | Package/version, car/layout, season, build evidence, objective/horizon, source/control fingerprint, driver/controller applicability, model versions. |
| `availability` | Complete worksheet, source-only, incomplete or unavailable; actionable reason and missing input. |
| `setup` | Full control vector; original/proposed values; units; retained/changed status; source; verified increments; separate readout targets. |
| `application` | Ordered operations, compensating adjustments, expected readouts, tech-check status and rollback source. |
| `track_demand` | Geometry source, station/zone definitions, supported line/speed range, lateral/normal/combined demand and sensitivity bounds. |
| `expectations` | Metric, location/run stage, value or direction, evidence class, population, applicability, interval type/level and gate verdict. |
| `validation_plan` | Required recording conditions and horizon, comparator, expected fingerprint, metrics, thresholds and matching rules. |
| `assessment` | Actual setup match, eligible exposure, prediction verdict, benefit verdict, driver report and one supported next action. |

Every uncertain number needs **what the interval means**: source precision, replicate SD, parameter interval, sensitivity range or prediction interval. These are not interchangeable.

Use driver-facing labels **Recorded, Derived, Proxy, Predicted, Prior, Driver report, Unavailable**. Preserve bot/human and exact/conditional status as additional context. No missing number becomes zero, and no heuristic confidence tier becomes a calibrated probability. This implements Q127’s separation of measurements, proxies, predictions and bounds. [Requests Q127][Q-F]

**Routes**

The existing endpoint is:

```text
POST /api/v1/drivers/{id}/baseline
```

Extend its request with exact identities, objective/horizon, selected source, preparation and contract version. Continue returning the existing asynchronous job reference. [Current API][API]

Proposed additions:

```text
GET  /api/v1/drivers/{id}/baselines/{package_id}
POST /api/v1/drivers/{id}/baselines/{package_id}/checks
```

The check request identifies a recording/analysis; compatible completed analyses can trigger the same evaluator automatically. Persist links to expected and actual setup identities. Use the existing tuning outcome mechanism for subsequent controlled corrections.

Keep the current web destination:

```text
/d/{id}/setups/baseline
```

Add package selection to its route/query state rather than another top-level destination. Continue entering Progressive Tuning from **Tune this setup** with the actual recording context. [Current router][ROUTER] [Requests Q113][Q-F]

**One screen and one racing decision**

The screen answers: **What should I load, what should it improve, and did it work?**

- A compact persistent header holds event, build compatibility, Race/Q and stint length.
- The selected package and complete garage worksheet occupy one bounded pane; changed controls are prominent, retained controls remain inspectable.
- A real track map shares location with the supported evidence. Relevant steering/throttle/brake evidence stays visible when assessing a run.
- A compact next-run/result area shows the required check, then benefit and one next action.

Keep the full worksheet scrollable within its pane rather than making the user scroll through several pages to recover critical context. Display unavailable explanations where they affect the decision; do not reserve large charts for unsupported forecasts.

Use calibrated road-wheel steering, explicit tyre/run age, readable units and real geometry. Normal source status does not need another chip. Technical hashes remain subordinate. [Coach product instructions][AGENTS] [Requests, product principles][Q-PRINCIPLES]

**Delivery sequence and acceptance**

First ship truth corrections, the complete source-based worksheet and automatic next-recording check. Then admit garage-map adaptations. Add physical performance forecasts only as the experiment programme validates them.

Required verification includes:

- Contract/wire and producer-to-view preservation checks.
- Fixed/open, unknown-build, incomplete-source and mode-boundary cases.
- Pressure-lattice versus SI conversion and banked-force regression cases.
- Event-held-out gate enforcement, including cached legacy output.
- A real recording flowing through generation, applied-setup verification and benefit assessment.
- A full intended-horizon check before any race-stint claim.

Run the repository’s normal checks, including Linux server checks, when implementing. The ledger explicitly says real-recording acceptance for Baseline/Progressive Tuning has not yet been demonstrated; fixture success alone does not close that gap. [Built ledger, unverified workflows][B-ACCEPTANCE]

# WHAT TO TELL JOSHUA

1. Coach should give you one complete setup and a clear reason to use it.
2. It can start without a recording of you driving that track.
3. Track geometry tells us the loads; the car tests tell us which adjustments work.
4. More RF pressure made the tested IRP setup tighter, so we will stop treating that direction as unresolved.
5. We will correct the banking calculations and enforce the prediction checks.
6. Garage mapping comes first because one adjustment can quietly change several things.
7. Race and qualifying setups will have separate goals and checks.
8. Unsupported gains, tyre life and temperature targets will stay out of the recommendation.
9. Your next recording will show whether the setup behaved as expected and whether it helped.
10. The aim is reusable car knowledge that gets the next track right with little correction.

[V-GENERATOR]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/BASELINE-TUNING-codex.md:216
[V-CHECK]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/BASELINE-TUNING-codex.md:545
[S-MEASUREMENT]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:11
[S-STATE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:20
[S-FORCE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:31
[S-TRACK]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:55
[S-CODE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:95
[S-LATTICE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:187
[S-RF]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:194
[S-AGE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:214
[S-RANKED]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:223
[S-FIVE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-UNDERSTANDING.md:246
[A-CONTRADICTIONS]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-AUDIT-codex.md:51
[A-PRECISION]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-AUDIT-codex.md:140
[B-DATA]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/built-ledger.md:39
[B-SETUPS]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/built-ledger.md:69
[B-OPEN]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/built-ledger.md:220
[B-ACCEPTANCE]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/built-ledger.md:293
[Q-F]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/requests-catalog.md:149
[Q-PRINCIPLES]: C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/requests-catalog.md
[AGENTS]: <C:/Users/joshu/source/repos/iRacing Coach/AGENTS.md:5>
[API]: <C:/Users/joshu/source/repos/iRacing Coach/web/src/core/api.ts:156>
[ROUTER]: <C:/Users/joshu/source/repos/iRacing Coach/web/src/core/router.ts:88>