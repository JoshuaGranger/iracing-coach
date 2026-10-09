# MODEL INVENTORY

**The repositories contain useful measurements, conditional models and conservative coaching rules, but they do not yet identify the simulator’s tyre or setup physics well enough to generate a defensible zero-iteration optimum.** Determinism makes reverse engineering possible; it does not make the governing law uniquely recoverable from a limited set of partially observed laps.

This was a read-only, one-pass audit. I read the named core Python modules, relevant Coach/Rust code, selected model/reference JSON, F1–F47, R1–R18, the prior audit, and the Garage61 study reports and setup comparisons. I also independently checked the complete 442-row `runs.csv`. No files were changed and no simulator work was performed.

The requested repository copy of `lab-notes\irp-bot-study\audits\codex-2026-09-27\AUDIT.md` was absent; I read the copy under the September 26 data bundle instead. Directory-search and Python launches encountered `helper_unknown_error: apply deny-read ACLs`; literal file reads worked. Consequently, I did **not** decode `series-1hz.csv.gz`, replay the raw Garage61 telemetry, or run Python/Rust parity tests. The full `laps.csv` read exceeded the tool’s output limit; I did not treat that truncated read as a complete dataset.

Source conventions below:

- **C/** = Coach’s `iracing-coach/skills/analyze-iracing-race/scripts/`.
- **T/** = utility’s `testdriver/`.
- **F** = [FINDINGS-REGISTER.md](C:/Users/joshu/source/repos/iRacing-utility/lab-notes/irp-bot-study/FINDINGS-REGISTER.md).
- **R** = [relationships/FINDINGS.md](C:/Users/joshu/source/repos/iRacing-utility/lab-notes/irp-bot-study/data/relationships/FINDINGS.md).
- **Exact** means an observed rule reproduced within a specified domain—not recovered simulator source code. **Identified** means a locally isolated response or parameter. **Conditional** means a useful relationship requiring its original conditions. **Assumed** means a modelling or policy choice. **Unidentified** means the evidence does not discriminate the relevant alternatives.

| Physical model | Code/notes: form and important constants | Evidence and audit status |
|---|---|---|
| Tyre lateral/longitudinal capacity | `T/lapsim.py:Car`: speed-interpolated friction ellipse. At 25/40/60 m/s, lateral coefficients 1.40/1.42/1.38; braking 1.30; drive 0.90; combined-slip exponent 2. An empirical envelope can replace this. | **Assumed/conditional.** No identified per-tyre force-versus-load/slip law. Successful lap reconstruction does not identify these coefficients uniquely. F9, F14–F18, F47. |
| Empirical combined-force envelope | `T/gripmap.py`: `LongAccel/VertAccel`, `LatAccel/VertAccel`; 99.5th-percentile lateral demand by speed and longitudinal-demand bin, minimum 40 samples. Clean samples exclude classified slides and nearby frames. `irp_optimal_sim3` metadata uses the same envelope at 30 and 60 m/s, peaking near 1.50 while braking and declining under drive. | **Conditional observed demand envelope**, not certified tyre capacity. Driving policy, steering cap, filtering and coverage shape the boundary. Per-wheel loads and load sensitivity are absent. |
| Slip, crab and limit detection | `T/controller.py`, `gripmap.py`: body sideslip relative to a reference; reference crab approximately scales with speed squared. Grip-map thresholds include rear residual slip >0.045 rad, yaw response <0.8 of expected for front slide, rear/front wheel-speed excess >0.04 and front lock ratio <−0.06. | **Conditional diagnostics.** F20/R13 identify substantial setup-dependent crab; F35/F47 show controller intervention and mixed limit mechanisms. Body sideslip is not a tyre slip angle. |
| Steering/understeer response | `T/reference.py`, `controller.py`: wheel angle `b + Aκ κ + Aa v²κ`. Defaults approximately −0.11, 28.3, 0.0495. Stored IRP fit: −0.10177, 26.5766, 0.049629. Kansas v8: −0.03354, 47.6946, 0.009500. | **Locally identified response**, not universal steering physics. F14: steady-state gain about 0.97–1.06 of model, straight small-signal gain 1.86, near-limit gain 0.43. R13–R14 are setup-specific. |
| Banking, grade and vertical curvature | `T/lapsim.py`: road-plane lateral demand `v²κh cosθ − g sinθ`; normal demand `g cosθ cosφ + v²(κh sinθ + κv)`; longitudinal gravity subtracts `g sinφ`. Uses `g=9.80665`. | Newtonian **conditional mechanics**, given frame definitions, geometry and a point-mass approximation. Does not identify suspension loads or aero. Geometry derivatives and smoothing materially affect results. |
| Banked corner-speed limit | `T/trackref.py`: `v² = g(μ cosθ + sinθ) / [|κ|cosθ − μ(|κ|sinθ + down)]`, with speed cap and `g=9.81`. `down` is downforce acceleration per speed squared; default zero. | **Assumed constant-μ point-mass limit.** Correct within its stated idealization; not a measured tyre law. Kansas zone μ values are calibrated to target corner speeds. |
| Combined acceleration/braking planning | `T/lapsim.py` uses envelope or ellipse forward/backward passes. `T/trackref.py:solve_speed` uses remaining longitudinal capacity `μg sqrt(1−u²)`, `u=(v/vlat)²`; iterative spatial passes. | **Assumed**, with a banking inconsistency discussed below. A computed minimum time remains conditional on the capacity model and control feasibility. |
| Engine, drag, rolling and cornering losses | Bot longitudinal form approximately `a = throttle·P/v − Cd v² − C0 − Cc|ay| − B·brake`. IRP stored values: P≈325.30, Cd≈0.000250947, C0≈1.38252, B≈14.3585; fitted Cc≈−0.00355 is clipped to zero. Kansas v8: 312.056, 0.000282176, 0.743658, 13.2102, 0.043906. P is **specific power**, not engine horsepower. | **Conditional lumped fit.** Power, drag, rolling resistance, engine braking, tyre scrub, grade and wind are incompletely separated. F14 identifies coast deceleration `1.05 + 0.000478v² + 0.0653|ay|`; F24/R15 show Kansas coefficients do not transfer unchanged to Vegas. |
| Braking and brake bias | Bot uses a linear brake-deceleration coefficient plus grip limit. Coach `C/tuning_engine.py` recommends forward bias for loose entry and rearward for tight entry, with eligibility safeguards. | Brake gain is **conditional**; setup direction is a **qualitative prior**. F6/R11: BB68’s nominal gain −0.031 s/lap has a confidence interval crossing zero; capped tests show no pace effect. No identified hydraulic, axle-force or lockup model. |
| Gearing and RPM | `T/controller.py` uses RPM/speed ratios, default gear ratios approximately 407/264/199/151 RPM per m/s, shift 8300 and redline 9100. Kansas fourth-gear reference ≈97.6 RPM per m/s. `lapsim.Car.v_top=62`, despite its comment calculating 60.3 m/s. | Ratio within a gear is **locally identified**; shift thresholds/caps are **assumed policy**. No complete torque/RPM/throttle/drivetrain map. |
| Cold-pressure representation and delivery | R2/F36–F39: garage half-psi lattice maps to live cold pressure with factor **6.8911603 ± 0.0000007 kPa per displayed psi**. Whole-kPa service requests resolve to nearest half psi: 16/16 parked and 24/24 driven requests. | Lattice/delivery **exact on tested cases**; factor **identified**. Exact float expression remains **unidentified** because identical settings can yield different float32 values depending on the adjustment path. Live 0.01-kPa rounding is refuted. |
| Fitting temperature | R2: reset sets approximately `Tair+0.073°C`, SD≈0.038; pit sets approximately `Tair+13.245°C`, SD≈0.203. Free pit fit `13.49+0.990Tair` is similarly good. | **Conditional**, not an exact constant-temperature or whole-°F-offset rule. My independent `runs.csv` check reproduced the pit cluster at about +13.245°C with SD 0.203°C. F39 leaves ambient dependence open. |
| Hot-pressure gas law | R3 candidate: `(Pg+patm)/(P0g+patm) = (Tgas,K/Tfit,K)(V0/V)`. Rear pressure-temperature slope ratio ≈1.49 supports absolute rather than gauge scaling under comparable thermal/volume conditions. Front ratio ≈1.39. | **Conditional support**, not uniquely identified gas-state law. Gas temperature and tyre volume are unobserved; carcass temperature is not gas temperature. R3’s own correction says atmospheric offset and gas equation remain unidentified. |
| Pressure build-up | R4: S-shaped approach to a plateau over roughly 40 laps; effective timescale 10–13 laps. Single distance-exponential fits fail; fitted length scale varies substantially with observation window. Weibull improves extrapolation but still misses long-run RF pressure by about 4.6 kPa. | Shape is **observed**, functional law **unidentified**. F44 did not deliver a valid same-set thermal transient. |
| Surface-temperature dynamics | R5 candidate `dTs/dt = −(Ts−Tbulk)/τ + heating(slip)`. At 40 m/s, fitted τ≈2.8–4.3 s across reported channels, RF middle≈3.3 s. Lap-mean RF fit: `−10 + 0.075E + 0.47Ttrack + 0.41Tgas`, where `E=∫|ay vy|dt`; other tyres have different coefficients. | **Conditional**. Heating coefficients are collinear; RF residual SD≈6.1°C. Original high-rate thermal fit was not independently replayed here. No complete conduction/convection/contact-heating law. |
| Carcass and gas thermal state | R2–R5 use fitting temperatures, pressure-derived gas proxies and pit carcass measurements. F28 says carcass/wear channels hold the previous run’s stall result; first post-garage IBT can swap rear labels. | Dynamic carcass/gas model **unidentified**. Pit endpoints cannot be treated as continuous carcass observations. |
| Grip versus temperature, pressure and wear | Historical notes describe RF temperature windows, approximately 0.3% grip loss/lap and about 0.27% RF wear/lap. R8 attempts multivariable grip/pace fits. No authoritative forward law maps these states to tyre forces. | **Unidentified.** F12 is explicitly a hypothesis; F13 describes regimes. R8 coefficients are unstable/confounded. F34/R18 show the hotter-RF setup can be faster and fade less. |
| Wear rate | R6: `ln w = a + b ln E + cTtrack`, w in percentage points/km. `(a,b,c)`: RF `(−24.27,3.46,.0353)`, RR `(−11.87,1.39,.0439)`, LR `(−9.43,.92,.0355)`, LF `(−8.87,.83,.0259)`. | **Conditional regression; exact law unidentified.** Session controls change the RF exponent. F46’s new power fit gives 5.4011; none of the frozen candidate families covered all three held-out observations. |
| Wear/temperature distribution across tread | R7 reports LF wear shares ≈57.9/40.1/2.0%, RF≈33.7/34.1/32.2%. LF pressure 14/17/20 psi changes outside wear share to 57.1/61.0/63.9%, while total wear rate decreases. | Some pressure-treatment responses **locally identified**, but generic crown/camber rules **unidentified**. Left/right channels must first be converted to physical inside/outside. R7, F28, F34, F45. |
| Coach load, “energy” and stress proxies | `C/analysis_engine.py`: brake proxy `∫brake·vdt`, steering proxy `∫|steering|vdt`, stress weights 0.35 brake/0.35 steering/0.30 lateral load. `C/tire_energy.py`: `∫|ay|vdt`, `∫|ax|vdt`, and optional lateral term `∫|ay|v|sinβbody|dt`. | **Defined diagnostic proxies**, not measured tyre heat or wear. Units can be J/kg without the quantity being actual dissipated energy. Endpoint-allocated wear traces are not independent observations. |
| Coach historical tyre-life and pace predictor | `C/race_foundations.py:build_tire_prediction`: weighted historical rate `(100−remaining)/green_laps`; weight `1/(0.25+feature_distance)²`; remaining extrapolated linearly. Minimum three eligible observations from three sessions. Pace extrapolated using observed stint slope. | **Assumed statistical predictor**, not physics. Similarity/count-based confidence is not calibrated uncertainty; pace slope mixes tyre state, fuel, weather and driving. Its separate validation gate is bypassed in the traced workflow. |
| Track temperature | R1: `Teq≈2.302+0.9396Tair+15.3208 max(0,sinαsun)³`, first-order lag≈25 min. | **Conditional weather-state model**. Good selected-session fits coexist with failed sessions. Exponent, lag, cloud forcing and initialization are not uniquely separated. R12’s channel equality/quantization are measurement facts, not this physical law. |
| Rubber, material and track evolution | R10; F41/F43. Track-usage UI exposes a percentage and Generate control; launches intended to compare usage did not match weather. | **Unidentified** effects on grip, heat or wear. Absence of a rubber telemetry channel does not imply absence of rubber physics. |
| Aero and cooling | Bot includes `Cd v²`; `trackref` has optional constant `down·v²`. Tape comparisons measure changes in steering/pace/temperature. No front/rear aero map versus ride height, pitch, yaw, wind or tape. | Lumped drag **conditional**; aero balance/downforce/cooling maps **unidentified**. F24/F27 support effects, not unique attribution. F25 demonstrates consequential engine overheating. |
| Weight transfer, springs, bars, wedge and geometry | Coach tuning catalog uses qualitative crossweight and paired track-bar rules; packers address bottoming. Utility notes record coupled ride-height, toe, preload and crossweight changes. There is no four-corner suspension equilibrium/kinematics solver. | **Unidentified physical mapping**; qualitative **assumptions** and measured package effects. F26/F30/F33; IRP v5. Spring/wheel rates, motion ratios, roll stiffness distribution, roll centres, compliance and bump-stop curves are missing. |
| Camber and toe | Setup exports provide static values; R7/F26/F33 relate packages to tread, steering and crab. Rear-toe hybrid forecast: approximately −0.006 rad crab and +0.07 rad steering wheel. | Package response **conditional**; independent camber/toe laws **unidentified**. The hybrid forecast is registered but untested. |
| Fuel consumption and fuel mass | R9 burn mean≈0.543 L/lap; conditional fit `0.176 + 0.0162·throttle_seconds + 0.0000976·Edrive`, RMS≈0.009 L/lap. Idle≈0.00138 L/s. Coach uses measured burn and stint trends for planning. | Burn **conditional**, not constant. Causal fuel-mass effect on pace/balance **unidentified**; fuel and age correlate≈−0.997. F40/F42 invalidated proposed crossover routes. |
| Stint/strategy prediction | `C/strategy_model.py`: linear lap deterioration; stint time `nt0+d n(n−1)/2`, pit-loss scenarios and fuel feasibility. | **Assumed bounded scenario model**, useful operationally. The fitted deterioration coefficient is not a tyre-only law. |
| Track/line geometry and localization | `T/trackmodel.py`: lane-dependent surface `z(s,e)=c0+c1e+c2e²`; banking from its lateral derivative. `estimator.py`: Frenet kinematics, including `de/dt=v sin(error)` and curvature-based lateral correction from distance-rate ratio. | **Identified geometry conditional on measurement/calibration**. Altitude may include vehicle pose; smoothing affects curvature. F29 shows surveyed walls and G61 edges differ systematically. Neither necessarily defines legal track limits. |
| Synthetic vehicle used to test the controller | `T/sim_harness.py`: imposed capacity `(19.5+0.0012v²)×grip×wear_factor`; yaw lag 0.12 s, steering lag 0.04 s, drive cap 8 m/s² and stipulated slide degradation. | **Assumed test plant**. Passing its tests validates behavior against those injected assumptions, not iRacing physics. |
| Damage/repair and model eligibility | Coach screens repair timers, repair progress, incidents, resets, tyre endpoints and clean runs. Structured tuning withholds numerical changes where ranges are unverified. | **Evidence policy**, appropriately conservative. Zero repair time is not proof of an undamaged car; pace loss is not a damage measurement. F25 illustrates why engine condition also matters. |

# CONTRADICTIONS

1. **The Coach’s model-validation contract is not enforced on the predictor path I traced.**  
   [tire_model_gate.py](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/tire_model_gate.py>) requires at least eight held-out events, improvement greater than 5% over honest baselines, and interval calibration within ten percentage points. However, `workflow.py` → `storage.py:_update_tire_learning_locked` directly calls `build_tire_prediction`; `analysis_engine.py` then renders estimated life, capability pace and tyre-age cost when its status is `predicted`. That path contains no gate verdict.

   The predictor’s context key includes car, track configuration, **fixed/open setup type** and compound—not exact setup fingerprint or simulator build. Its “exact … setup model” wording therefore overstates matching. Three sessions can produce predictions even with no comparable features. Historical rate quantiles are also not calibrated predictive intervals. This is the most consequential Coach finding.

2. **The banked lateral limit and combined-force budget use different mechanics.**  
   [trackref.py](C:/Users/joshu/source/repos/iRacing-utility/testdriver/trackref.py) computes a banked corner-speed limit, then `solve_speed` allocates remaining drive/brake force using flat-road `μg` and `u=(v/vlat)²`. On banking, lateral tyre demand has a gravity offset and normal load changes with speed. Consequently, that speed ratio is not the actual friction utilization.

   `controller.SpeedPlan.tables` similarly scales μ by pace squared. That conversion holds only under restricted flat-road, constant-normal-load assumptions. Banking requires recomputing both road-plane demand and normal load.

3. **Other planner inconsistencies can contaminate a physics comparison.**
   
   - `trackref.build` uses road-plane lateral acceleration in steering feedforward; `ksplan.replan` uses horizontal `v²κ`.
   - `lapsim` applies local grip multipliers to the empirical-envelope branch but not its ellipse branch.
   - Its empirical utilization calculation interpolates at `ax/az`, although a uniformly scaled envelope requires the longitudinal coordinate `ax/(az·scale)`.
   - `hold_speed` checks whether nonnegative longitudinal envelope capacity remains; this alone does not establish force balance on a grade.
   - Kansas friction values are selected to hit target human corner minima. Agreement with those minima is calibration, not an independent prediction.

   The old Kansas **two-times-power planner versus one-times-power controller** mismatch is documented as corrected in v8; it should not be reported as an unresolved current defect.

4. **The “Kansas track-bar inversion” needs a provenance correction.**  
   I verified the inversion in the **IRP v5 → Vegas forecast**, not a separate Kansas experiment. IRP lowering the RR bar worsened exit behavior, but also changed rear toe, required an LF perch correction and changed truck-arm preload. It was one run per configuration. It did not establish that lower rear roll centre intrinsically reduces rear grip.

   Vegas subsequently raising the RR bar by 13 mm slowed this bot by 0.053 s/lap and increased rear crab, opposing the IRP-based forecast and agreeing with the garage’s qualitative direction. Furthermore, the Coach proposes moving **both bar ends equally**, preserving rake; the experiment moved one end. These are different interventions. A universal reversal of the Coach’s track-bar rule is unsupported.

5. **The camber result identifies a package, not camber alone.**  
   F26’s faster package changed LF/RF camber, crossweight **53.1→53.6%**, front-bar preload **0→−7.1 Nm**, and truck-arm preload **−1.9→+16.8 Nm**. The measured −0.144 s/lap and lower steering demand are real package observations. They do not identify a camber-only gain, a wedge-only gain, or a universal “more front grip” mechanism. The registered separating experiment remains unrun.

6. **The 85% corner-speed ceiling is a controller limitation, not a tyre law.**  
   F21 describes front push, steering accumulation and rear snap at 0.867–0.92 of human corner speeds; 0.85 is clean. F35 shows the guard slowing tighter setups during supposedly fixed-pace tests. F47 now finds mixed rear-slip/slide/steering-limit onsets and inadequate repeatability.

   Therefore, bot setup rankings are rankings of **car plus controller in that operating regime**. They cannot establish the human-limit optimum. Even interpreting 85% speed as 72.25% grip would be wrong on banking.

7. **The RF pressure sign is unresolved; newer steering evidence is narrower than a setup recommendation.**  
   R8’s RF +3 psi screens are **−0.071 and +0.272 s/lap**, opposite signs. Their average is not a stable directional law. F45 shows RF28→34 increased matched-lateral steering in both halves, but repeat discrepancies exceeded its registered 0.004-rad admission threshold. That supports a repeatable-looking direction requiring better precision; it does not admit pace ranking or transfer to humans/Vegas.

   “Hot middle means overinflated” also fails as a universal interpretation: the LF crown-temperature excess decreased as pressure rose in the prior audit, and F45’s RF centre remained cooler.

8. **Several old physical claims are explicitly superseded by their own evidence register.**
   
   - RF 88–92°C as the cause of the limit: F12 labels it a hypothesis.
   - Grip/wear loss per lap as constants: F13 calls them regime summaries.
   - RF wear exponent 3.46 as a recovered law: F46’s held-out results reject that promotion.
   - Fuel burn-down worth 0.08–0.10 s by lap 60 in `TIRE-AND-TRACK-MODEL.md`: R9/F40/F42 leave the causal pace effect unidentified.
   - Fitting temperature as an exact offset: corrected R2 supports an approximate relationship.
   - R3’s heading says gas-law “form identified,” while its body and audit correction explicitly say the gas-state equation remains unidentified.

   The register and corrected findings should prevail over historical narrative wording.

9. **Temperature cannot provide a universal normalization or tyre-health verdict.**  
   F15’s fixed temperature corrections failed held-out comparisons. Vegas F31’s within-block coefficient does not authorize cross-session adjustment. Charlotte’s same-day qualifying estimate is near zero with an interval crossing zero, while broader season regressions change with cohort selection.

   F34/R18’s faster, lower-fade setup has the **hotter RF** and shifts wear toward the front. The Charlotte report’s attribution of the remaining deterioration to tyre wear after subtracting an estimated temperature portion is also unsupported: pressure, thermal state, fuel, traffic and driving remain.

10. **The grip-map documentation promises more than the classifier implements.**  
    [gripmap.py](C:/Users/joshu/source/repos/iRacing-utility/testdriver/gripmap.py) describes front-slide detection using falling steering torque/pneumatic trail. Torque is loaded but not used in the mask. The front-slide test is a yaw-response threshold with a positive-lateral-acceleration condition. Its `--human` paths are parsed but ignored when selecting human files; the first sample file always supplies that classification. These are concrete implementation/provenance defects.

11. **Coach “energy” labels need tighter physical interpretation.**  
    [tire_energy.py](<C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/tire_energy.py>) correctly disclaims a wear measurement. Nevertheless, chassis sideslip does not supply contact-patch slip: each wheel also needs steering angle, yaw contribution, position and wheel speed. A car can have near-zero body sideslip while its tyres dissipate substantial energy.

    Additionally, `grade="slip_weighted"` can be set while `total_load_j_per_kg`, segment shares and ranking still use the **load-only** lateral-plus-longitudinal sum. The separate slip field is legitimate as a proxy; the grade does not describe how the reported shares were calculated. The Rust implementation inspected preserves this behavior.

12. **Build compatibility and unit semantics are not solved by metadata labels or parity.**  
    The tuning catalog’s `2026S3` label is not an executable build restriction: `_ruleset_supports_car` checks car-path prefixes. F30 demonstrates actual cross-build setup incompatibility. Exact setup values also need not imply unchanged computed geometry across builds.

    Python/Rust use the conventional SI kPa-to-psi conversion. F36 identifies a different **garage display/control mapping**. This calls for separate, explicitly named conversions—not globally replacing the physical SI unit constant. Byte-identical Python/Rust output can reproduce the same wrong interpretation; it establishes implementation agreement, not physical truth.

The Coach’s strict clean-run selection, repair uncertainty, evidence labels, source-setup preservation and withholding of unsupported numerical setup changes are strengths. They should remain in place while the gaps above are resolved.

# UNCLEAR, RANKED

**B** ranks importance for a physics-generated baseline; **H** ranks importance for human coaching. These are judgment rankings. Each row starts with existing-data work, followed by the cheapest useful simulator experiment.

| B / H | Unclear item | Cheapest discriminating work and expected signatures |
|---|---|---|
| **1 / 3** | **Per-tyre load sensitivity, slip curves and combined-force limit** | Reanalyse valid human and bot sweeps separately, retaining steering saturation and intervention flags. Compare demand at matched speed, state and normal-load conditions; do not fit only the surviving “clean” boundary. Then use short, progressive steady-corner and brake/drive sweeps. Constant μ predicts normalized capacity invariant with load; load-sensitive tyres predict changing force/load ratio. A true combined-force boundary moves systematically with longitudinal demand; a controller boundary moves when steering/guard policy changes. |
| **2 / 5** | **Setup→static pose→load-transfer mapping** | Reconstruct all garage before/after vectors already recorded, including computed toe, crossweight, ride heights and preloads. Next perform stationary one-click ± perturbations, restoring the complete baseline between them. Separate mean track-bar height from rake; camber setting from wedge/preload. An isolated camber effect follows camber with other state restored; a package effect follows one of the coupled variables. This is cheaper and more identifying than another uncontrolled lap-time screen. |
| **3 / 4** | **Separate pressure, surface heat, bulk heat and wear effects on grip** | Use only actual endpoints and genuine pressure/surface traces; remove interpolated wear as an independent predictor. Seek matched states across short/long, reset/pit and different-pressure runs. Then a verified same-set low–high–low workload sequence: rapid reversible recovery suggests a surface-temperature component; slower hysteresis suggests bulk/gas state; persistent loss after cooling suggests wear or another irreversible state. A pressure intervention at matched thermal state distinguishes pressure from their usual correlation. F44 did not execute this successfully. |
| **4 / 2** | **Measurement frames, crab, force normalization and controller observability** | Existing-data check: compare `v·yaw_rate`, numerical heading curvature, accelerometer channels and bank/grade projections; derive a crab line separately for every setup. Cross-check O/M/I orientation and post-garage rear swaps against garage readouts. Under normal crab, body slip varies smoothly with lateral demand without yaw divergence; loss of stability produces residual slip/yaw growth beyond that line. A coordinate/sign error reverses or offsets relationships systematically. If still ambiguous, short straight and opposite-direction low-speed steering tests are sufficient. |
| **5 / 6** | **Engine/rolling/aero/corner-loss separation and front/rear aero balance** | Refit existing straight and turn samples with speed, RPM, gear, throttle, grade and tape held explicit; validate on entire withheld runs. Then matched straight pull/coast tests, preferably opposite directions, plus steady corner tests at several speeds. Constant-power contribution scales as `1/v`; drag as airspeed²; near-constant rolling loss stays approximately flat; engine braking varies with gear/RPM; scrub follows lateral state. Aero load changes grow with airspeed² and ride-height/pitch changes, unlike a speed-independent mechanical balance shift. |
| **6 / 7** | **Wear functional form and thermal dynamics** | Recompute exposure over the same out/timed/in distance used for endpoint wear. Compare `∫f(work(t))dt` against `f(mean work)×duration`; split validation by whole run/session. Then match total work using smooth versus peaky workloads. Linear dose predicts equal wear; convex power laws predict more wear from peaks; temperature-activated wear follows thermal history even at equal mechanical dose. For heat, one-node dynamics give a single exponential response; multiple thermal states give multiple timescales/hysteresis. F46 currently admits none of the proposed exact laws. |
| **7 / 8** | **Fuel mass versus tyre-age effects** | Search existing clean refuelling discontinuities, especially human telemetry, while preserving tyre identity. Then physically drive into the pits and compare a same-tyre refill against a matched no-refill stop; verify no reset, tyre change or repair. A mass effect produces an immediate step after refuelling, with little instantaneous wear change; tyre aging remains continuous. Use acceleration, steering and braking response as well as lap time. The original “set 20 L” and reset-return burn-down protocols are invalid under F40/F42. |
| **8 / 9** | **True road surface, banking, curvature and usable width** | Cross-register existing surveyed points, human trajectories and G61 edges. Repeat geometry derivatives across reasonable smoothing scales. A true feature stays at the same station and affects multiple independent traces; a differentiation artifact changes strongly with smoothing or follows vehicle ride-height/roll. Where disagreement remains, a slow multi-lane survey is cheaper than a fast optimization experiment. Physical wall, painted line and legal boundary must remain distinct. |
| **9 / 10** | **Track thermal forcing and rubber effects** | Refit R1 with correct session clock, initialization, quantization and available weather categories; assess residuals by session, not random samples. Solar-law alternatives differ most near dawn/dusk and after changes in forcing; hidden cloud/initial-state effects produce session-specific residual structure. For rubber, first achieve matched actual air/track temperatures across usage settings, then compare short identical runs. A rubber effect survives weather matching; an apparent weather effect disappears. F43’s launches did not satisfy this prerequisite. |
| **10 / 1** | **Whether the Coach’s estimates deserve their displayed confidence** | This is predominantly existing-data work: trace every predictor consumer through an enforced gate; use exact build/setup identities and event-held-out validation against laps-only and cohort-median baselines. Compare the same held-out event IDs—not merely equal counts. Genuine predictive value survives those splits with calibrated intervals; leakage/cohort mixing causes performance collapse. Separately check Python/Rust payload bytes on the same fixtures. No simulator time is needed to establish the current gate bypass. |
| **11 / 11** | **Exact pressure float expression and fitting-temperature rule** | Use F36’s existing ascending/descending click ledger to test float32 operation order, cumulative stepping and display rounding. A direct multiplication gives one value per setting; incremental arithmetic gives path dependence. For fitting temperature, repeat reset and pit service at a second substantially different ambient level and record move-off state. Constant offset gives slope 1; fixed absolute temperature gives slope 0; thermal-history dependence gives variation with dwell/preparation. Do not use inferred gas temperature to “validate” the gas law that generated it. |

Two experimental disciplines are essential:

- **Measure the physical response before asking for lap-time ranking.** Matched-lateral steering, crab, acceleration and true wear endpoints usually require less simulator time and reveal more than a noisy at-limit lap total.
- **Respect the measured precision.** F7a suggests roughly 19 runs per arm for its 0.05 s/lap target; F47’s current probe fails its own precision gate. Four ABBA runs are useful for screening or large effects, but do not automatically establish a small setup advantage.

# TOWARD A PREDICTIVE SETUP MODEL

The required structure is a **stateful four-wheel vehicle model with a measurement model around it**. A single fitted grip number, optimum temperature or setup-direction table cannot supply the missing mappings.

The forward calculation should be:

```text
Setup controls + car/build identity
                ↓
Static equilibrium, alignment, preloads and geometry
                ↓
Suspension motion, aero platform and four wheel loads
                ↓
Wheel slip states → tyre forces → vehicle motion
                ↕
Surface/carcass/gas temperature, pressure and wear
                ↓
Lap/stint performance under a specified driver and objective
```

| Setup input | Physical quantities it must change in the model | What is known / missing |
|---|---|---|
| Cold pressures | Gas charge, carcass stiffness, loaded radius, contact distribution, force/slip curves, rolling loss | Garage mapping identified; pressure-to-force, stiffness and radius maps missing. |
| Springs and perches | Static corner loads/ride heights, wheel rates, travel, roll/pitch stiffness, aero platform | Garage coupling observed; motion ratios, equilibrium solver and dynamic maps missing. |
| Anti-roll bars and preload | Axle roll stiffness distribution, diagonal preload, wheel-load transfer | Qualitative direction and isolated package observations; quantitative maps missing. |
| Crossweight/wedge and ballast | Four static loads, CG position, transfer distribution and balance | Values/readouts available; effects cannot be inferred without tyre load sensitivity and suspension geometry. |
| Track-bar mean height and rake | Rear roll centre, geometric transfer, jacking, rear steer and pose-dependent alignment | Coupling demonstrated; independent height/rake response missing. |
| Camber, caster and toe | Wheel orientation through travel, effective slip angles, camber thrust, scrub and tread loading | Static settings and package responses available; kinematic and tyre response maps missing. |
| Packers/bump stops | Clearance, travel limits, nonlinear wheel rate, load spikes and aero-height control | Bottoming evidence and qualitative safeguards; contact/stiffness curves missing. |
| Brake bias | Front/rear braking force demand and combined-slip reserve | Directional prior and bot experiments; pressure/torque/lockup maps missing. |
| Tape/body aero settings | Drag, front/rear downforce, cooling and engine-temperature evolution | Measured total effects; independent maps missing. |
| Gearing and fuel | Engine operating point, tractive force, mass/CG and consumption | Gear ratios and local burn fits available; full engine map and causal fuel-mass effects missing. |

For each wheel, the force law needs the structure

\[
(F_x,F_y)_i =
\Phi_i(F_{z,i},\kappa_i,\alpha_i,\gamma_i,p_i,T_{s,i},T_{c,i},w_i,
       v,\text{surface},\text{compound},\text{build}).
\]

The slip inputs must come from **wheel contact-point kinematics**. Chassis translation plus yaw gives each wheel a different velocity; steering rotates it into the wheel frame; effective rolling radius and wheel speed determine longitudinal slip. Body sideslip alone cannot substitute for these quantities.

The thermal model should distinguish surface zones, carcass/bulk and gas. Candidate equations can combine slip work, hysteresis, conduction and speed-dependent cooling, but their terms must be identified independently enough to avoid several interchangeable explanations. Pressure requires absolute pressure, gas temperature and volume; wear requires a validated accumulated-damage law. The current data supplies useful constraints on these components, not their complete equations.

Aero needs separate drag and front/rear downforce maps versus **airspeed, ride height, pitch and yaw**. Fuel changes mass and CG; its consumption map depends on engine operation. Suspension and aero must feed back into wheel loads. Otherwise, spring, bar, wedge and ballast predictions have no physical route to lap time.

A zero-iteration baseline for a new track would need:

- **Car:** exact simulator build, mass/inertia/CG, suspension geometry and stiffness, alignment kinematics, tyre force/state laws, aero/cooling maps, engine/drivetrain/brakes, garage limits and the setup-to-state mapping.
- **Track:** a three-dimensional drivable surface—not just length, one radius and one banking angle—including lane-dependent curvature/bank, grade, vertical curvature, bumps, surface material and boundaries.
- **Initial state:** actual fuel, tyre preparation, thermal state, wear, weather, wind and track usage.
- **Objective:** qualifying lap or specified stint length, acceptable stability margin, tyre-life requirements and driver/control capabilities.
- **Validation domain:** evidence that the identified car laws predict withheld tracks, states and interventions without retuning to their results.

The optimization can then select legal setup controls and a feasible speed/line trajectory together. It should return predicted performance **and uncertainty/sensitivity**. A setup that is marginally quicker only under poorly identified parameters is not a strong baseline.

“Zero iteration” at the destination track is plausible after sufficient reusable car identification. It does not mean zero calibration anywhere. Until those mappings exist, the defensible starting setup remains a current-build measured reference plus explicitly bounded adjustments. Coach’s `starting_tune.py` already treats starting tunes principally as identified source artifacts; it is not presently a physics generator.

Finally, exact-law identification needs a stricter acceptance standard than a high R²: freeze the candidate, predict deliberately discriminating interventions, test complete held-out runs/builds, and account for quantization and floating-point behavior. Where multiple laws remain observationally equivalent, report that equivalence instead of declaring one recovered source expression.

# SUMMARY

1. No complete tyre, thermal, wear or setup law is presently recovered exactly.
2. The strongest exact results concern scoped measurement and garage-delivery behavior.
3. The Coach’s traced predictor path bypasses its newer validation gate.
4. Historical tyre matching omits exact setup and simulator-build identity.
5. Bot planning mixes banked limits with flat-road force allocation.
6. The 85% speed ceiling and setup rankings depend materially on the controller.
7. Track-bar and camber results identify coupled packages, not isolated universal directions.
8. RF pressure sign, fitting temperature, fuel effects and wear laws remain unresolved.
9. The cheapest next work is measurement correction, existing-data validation and stationary setup mapping.
10. A predictive baseline requires identified setup geometry, four-wheel forces and state evolution together.