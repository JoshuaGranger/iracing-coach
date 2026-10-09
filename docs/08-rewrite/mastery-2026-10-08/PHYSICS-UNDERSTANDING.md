> **Corrections from the Codex review (PHYSICS-UNDERSTANDING-REVIEW-codex.md, 9 Oct), read before citing this file:** (1) `analysis_engine.py:1353` already emits `sim_build` into the identity, so contradiction A2 is narrower than stated: the build and the setup fingerprint exist but do not enter the tyre matching key. (2) The "28 % low" drive budget (A3) is a capacity-budget error; at the Vegas example speed the engine demand (about 5.3-5.5 m/s²) is below both budgets, so the practical loss is smaller than 28 %. (3) The grip-vs-age −7.5 % pools controller programmes, assumes 12° banking and admits pressure-intervention runs: an association, not a law. (4) RF pressure: tighter is settled; "slower-or-null" rests on one SE (+0.078 ± 0.039 s/lap), and F7's opposite signs are compatible with noise, not shown to be noise. (5) Transcription fixes: IRP LF camber +6.0° (not +4.7°); the brake-bias comparison is 68 % vs 64 %; RR +3/+6 psi (not LR) accompanies the LR wear change. (6) The pit tool requests integer kPa through the SDK using 6.894757; the delivered lattice (F37) still holds.'


# Physics-model understanding: iRacing Coach and the test-driver utility

Written 2026-10-09 (Claude, read-only on both repositories). Inputs: Codex's independent audit `PHYSICS-AUDIT-codex.md` and `BASELINE-TUNING-codex.md` ("WHAT WE ALREADY KNOW"); the utility's registers `lab-notes\irp-bot-study\FINDINGS-REGISTER.md` (F1-F47) and `data\relationships\FINDINGS.md` (R1-R18), `TODO.md`, `audits\codex-2026-09-27\EXPERIMENTS.md`; `testdriver\TIRE-AND-TRACK-MODEL.md`, `TUNING-NOTES.md`; Coach `iracing-coach\skills\analyze-iracing-race\scripts\` and utility `testdriver\` code; the September 26 data bundle, the Vegas P4/P6 tables, the E1 ledgers (local `.orchestrate\...20261006T151235Z\results*` and NAS `N:\iRacing Temp\claude-drops\e1-20261006`). Scratch scripts and their outputs are in `_work\` next to this file; every number below names its source in brackets.

Conventions. **C/** = `iracing-coach\skills\analyze-iracing-race\scripts\`; **T/** = `iRacing-utility\testdriver\`; **F**n = FINDINGS-REGISTER; **R**n = relationships FINDINGS; **A**n = Codex audit contradiction n; **W/** = `_work\`. Status words follow the registers: **exact** (reproduced bit- or rule-exactly in its domain), **identified** (a locally isolated response or constant with a stated uncertainty), **conditional** (a fitted relationship that needs its original conditions), **unidentified** (the data do not discriminate the candidate laws), **assumed** (a modelling or policy choice), **bot evidence** (true for this controller at its speed; human benefit untested). L/M/R is left/middle/right of the tread seen from behind the car (R-README), so LF-L is the outside shoulder and RF-L the inside.

---

## 1. The model, law by law

### 1.1 Garage and pit delivery (the exact layer)

| Law | Equation and constants | Status | Evidence | Where in code |
|---|---|---|---|---|
| **Cold-pressure lattice** | live `<c>coldPressure` = (half-psi garage setting) × **k**, k = 6.8911603 ± 0.0000007 kPa/psi, stored as float32, not rounded to 0.01 kPa (800/800 off that grid). 14 psi = 96.47626, 20 = 137.82321, 28 = 192.95250, 34 = 234.29945 kPa. The SI constant 6.894757 is excluded. | k **identified**; lattice **exact** (one value per setting, path-independent: 31 settings, 788 garage rows, W/a2); the float *expression* **unidentified** (section 3a) | F36, R2; W/a_pressure_float.out, a2, a3, a4 | Utility: none (the bot requests psi through the SDK). Coach converts with the SI constant: `KPA_TO_PSI = 0.14503773773020923` (C/analysis_engine.py:76), `value * 6.894757293168` (C/setup_catalog.py:227). Garage psi is therefore mis-displayed by 0.05 % (28 psi reads 27.99 psi; harmless for display, wrong for lattice reasoning). |
| **Pit-service delivery** | a whole-kPa request is fitted on the **nearest half psi** of the lattice (16/16 parked, 24/24 driven); same on both corners of an axle; stable from completion to first motion; 28/31/34 psi requests arrive as 192.95/213.63/234.30 kPa | **exact** on tested cases | F37, F39; `results\e1-unrounded-lattice.txt` | T/pitservice.py (`--pit-pressures`); the orchestrator's `pit service: ... cold pressures now` line |
| **Fitting temperature** | reset ("fresh car") set surface = T_air + 0.07 ± 0.04 °C (n 112); pit-service set = T_air + 13.24 ± 0.20 °C (n 267); free fit 13.49 + 0.990 T_air equally good; no whole-°F offset is exact | **conditional** (one ambient level per preparation; E8 needed) | R2 corrections; A-table row "Fitting temperature" reproduced the +13.245/SD 0.203 cluster | none in code; used in `runs.csv` columns `fit_T_<c>`, `fit_offset_RF_C` |
| **Set identity and resets** | a reset refuels to 70.75 L (F42: +1.80 L over a burned-down state), fits new tyres at ambient, resets the odometer; carcass/wear channels hold the previous set's stall readout; the first IBT after a garage visit lists LR/RR swapped; TrackTemp ≡ TrackTempCrew in whole °F | **measured** (pipeline facts) | R12, F28, F42, F44 | T/drive.py, cycle.py (tyre age restarts after a reset, cf3ddb0); T/tools/balance.py `--readout`; Coach reads `CarSetup` and stall channels in C/analysis_engine.py:1306-1382 |

### 1.2 Tyre state evolution (pressure, heat, wear)

| Law | Equation and constants | Status | Evidence | Where in code |
|---|---|---|---|---|
| **Hot pressure (gas state)** | candidate (P_g + p_atm)/(P_0g + p_atm) = (T_gas/T_fit)(V_0/V). Slopes dP/d(T_carcass − T_fit): RF 0.72-0.77, RR 0.73-0.79, LF 0.52-0.56, LR 0.49-0.52 kPa/°C. Rear-axle ratio RR/LR 1.490 (1.488-1.493, n 104) → absolute offset 100.4 kPa (99.2-101.2); front RF/LF 1.391 violates axle symmetry. New (W/b): at a fixed 0.93 pace the RF hot/cold ratio is 1.0827/1.0822/1.0816 at 28/31/34 psi; hot = cold × 1.077 + const (slope 7.42 kPa per psi of cold, residual SD 0.04 kPa). Gauge-proportional predicts 7.46 kPa/psi, absolute-pressure at equal gas temperature 7.27; the RF surface was +2.3 °C hotter at 34 psi, which the absolute law would need as +0.9 kPa. | **unidentified** (pressure alone fixes only (T/T_0)(V_0/V); the two laws now differ by 0.2 kPa/psi, within the thermal confound) | R3 and its audit correction; F45; W/b_rf_pressure.out | none in Coach or the bot; Coach reports hot pressures (C/analysis_engine.py:2186-2294) |
| **Pressure build-up** | S-shaped approach to a plateau; fraction of the laps-50-60 rise: age 5 0.28-0.32, 10 0.53-0.55, 20 0.81-0.85, 30 0.94-0.96, 40 1.00 (RF/RR/LR; LF slower). Effective τ 10-13 laps. Single distance-exponential fails (λ 11→37 km with window); Weibull misses age ≥ 40 by 4.6 kPa. Amplitude follows work: plan L vs capped A at age 30: 263.6 ± 0.7 vs 255.8 ± 0.1 kPa. "Pressure follows laps, surface temperature follows pace" (3 runs). Approximately, not exactly, universal (group differences to 0.09 in normalised fraction). | shape **observed**, law **unidentified** | R4 and corrections; TIRE-AND-TRACK-MODEL run 1-3 tables | none |
| **Surface temperature** | dT_s/dt = −(T_s − T_bulk)/τ + heating(slip); τ at 40 m/s 2.8-4.3 s (RF-M 3.3). Lap-mean equilibrium, e.g. RF = −10.0 + 0.075 E_lat + 0.47 T_track + 0.41 T_gas (R² 0.72, SD 6.1 °C); RR −1.7 + 0.046 E + 0.56 T_track + 0.43 T_gas (SD 1.4). Heating coefficients collinear. | **conditional** | R5 | none (Coach shows lap-mean surface temps; bot logs them) |
| **Carcass (bulk) temperature** | pit-only channel; post-run readouts RF 107/102/102 °C after 62 laps at IRP (R setup), 124-131 °C after out+15 at Vegas. Implied gas temperature (R3) ≈ carcass − 13 ± 7 °C. Dynamic model absent. | **unidentified** | R3, R17, R18, F34 | C/analysis_engine.py stall readout; T/tools/balance.py `--readout` |
| **Wear rate** | ln w = a + b ln E_lat + c T_track (w in %/km incl. out/in lap): RF (−24.27, 3.46 ± 0.43, 0.0353 ± 0.0055), RR (−11.87, 1.39, 0.0439), LR (−9.43, 0.92, 0.0355), LF (−8.87, 0.83, 0.0259); with goal fixed effects the RF exponent is 2.70 ± 0.53. E9 (F46): the dose direction replicated (RF 0.14-0.41 pp/km across 0.94/0.965/0.99 pace) but no frozen family (linear, power 5.40, exponential) covered all three held-out runs. New (W/b): at a fixed 0.93 pace, RF wear after out+8 rises +0.039 pp per psi of RF cold pressure (0.93 → 1.16 % from 28 to 34 psi). | **conditional regression**; exact law **unidentified** | R6, F46, F13 ("~0.27 %/lap" is a regime summary), W/b_rf_pressure.out | Coach: historical rate (100 − remaining)/green_laps per band (C/race_foundations.py:1403-1433); not a physics law |
| **Tread split** | LF wear shares 57.9/40.1/2.0 % (outside heavy: +4.7° camber), RF 33.7/34.1/32.2, rears even. LF cold 14/17/20 psi → LF outboard share 57.1/61.0/63.9 % and LF rate 0.048/0.041/0.034 %/km (identified total effect); RF 193/214/234 kPa: shares unchanged within 0.1 pp (R7) and within 0.3 pp in W/b (33.3/33.9/32.8 at every pressure). RF crown M − flank mean −1.46 → −1.83 °C from 28 to 34 psi: the centre gets *relatively cooler* with pressure in this regime. | LF-pressure response **identified**; crown/camber rules **unidentified** | R7, F45, W/b | none |

### 1.3 Tyre force and grip (what the car can do)

| Law | Equation and constants | Status | Evidence | Where in code |
|---|---|---|---|---|
| **Friction ellipse (bot lap model)** | usage = specific force / VertAccel: lateral |a_y| ≤ μ_y(v) a_z with μ_y = 1.40/1.42/1.38 at 25/40/60 m/s; braking μ_b 1.30; drive μ_d 0.90; combined (a_x/μ_x a_z)^p + (a_y/μ_y a_z)^p ≤ 1, p = 2 | **assumed** (fitted to Joshua's 22.443 lap: simulated 22.40 s, speed rms 0.34 m/s) | T/model/irp_grip.json `calibration`; TIRE-AND-TRACK-MODEL "Model prediction accuracy" | T/lapsim.py:43-60 (`Car`), 258-266 (`_long_limits` ellipse branch) |
| **Empirical g-g-v envelope** | 99.5th-percentile |μ_y| by speed row and μ_x column from clean samples (no slide, slip within 0.03 rad of the reference, no wheelspin/lock); at 40-45 m/s and μ_x ≈ 0 the human-clean envelope is 1.41-1.44; it peaks while braking (μ_x −0.3) and falls under drive | **conditional demand envelope**, not capacity (driving policy and the 1.30 rad steering cap shape it) | T/model/irp_grip.json `g_g_v_human_clean`; A row "Empirical combined-force envelope" | T/gripmap.py:60-100; T/lapsim.py:64-95 (`env_rows`, `mux_range`) |
| **Banked corner-speed limit (planner)** | v² = g(μ cos θ + sin θ) / (κ cos θ − μ(κ sin θ + down)); G = 9.81; `down` default 0 | **assumed** constant-μ point mass; Kansas zone μ calibrated to target minima | T/trackref.py:32-45 | T/trackref.py `lat_limit_speed`; T/tools/ksplan.py:100 |
| **Load sensitivity** | none modelled. New (W/d3): at IRP the a_z lever arm among limit samples is 3.9-6.2 %, so load sensitivity is not identifiable there; cross-track the limit ratio a_y/a_z is 1.16-1.22 at a_z 13.3-13.5 (IRP, p99 apex, θ = 12° assumed) vs 1.22 at a_z 19.0 (Vegas human Q lap) and 0.98 at 16.2 (bot at its ceiling), confounded by aero and surface. | **unidentified** | W/d3_apex_fixed_theta.out; BASELINE-TUNING banked numbers; F21 | — |
| **Grip vs age (the one solid grip law)** | p99 apex a_y/a_z falls −7.5 % (T1-2) and −7.7 % (T3-4) from age 2-5 to 46-70; apex a_y p99 −9 to −10 %; apex speed p99 −3.3 to −4.4 % (bot, R/OPT, 3,240/3,017 apex points) | **measured** (bot) | W/d3; F13, F16 (probe onset 0.999 → 0.969 by age 16) | T/controller.py tyre-age pace schedule (`--tyre-k`, `--tyre-peak`) |
| **Grip vs surface temperature, pressure, wear** | 707 steering-limited laps: R² 0.03-0.21, unstable signs, positive fuel coefficient (non-physical). RF 88-92 °C "sets the limit" is a hypothesis. Vegas F34/R18: the hotter-RF setup fades less. | **unidentified** | R8, F12, F34 | none that recomputes; Coach shows evidence labels only |
| **Slip, crab, limit detection** | body slip β = atan(v_y/v_x); rear slide when slip beyond the speed-scaled reference crab > 0.045 rad; front slide when yaw < 0.80 × steady-state yaw for the steering with a_y > 8; wheelspin rear/front wheel-speed excess 0.04; lock-up ratio −0.06. Crab lines: Vegas default β = 0.0146 + 0.0016 a_y (8,555 frames, P3 run 12, 0.050 at a_y 22) — but the P4 fixed-pace default reads 0.0436 at a_y 22, and the setups differ: at a_y 16 blind 0.033, shop 0.029, G61 0.023 rad. | diagnostics **conditional**; crab **identified per setup**, with an internal inconsistency of 0.006 rad on the default | F20, F27, F33, R13; gripmap thresholds | T/gripmap.py:87-100; T/controller.py (traction control, `--tc-*`), T/tools/balance.py |
| **Steering response** | wheel angle = b + A_κ κ + A_a v² κ; IRP fit (−0.10177, 26.5766, 0.049629); defaults (−0.11, 28.3, 0.0495); Kansas v8 (−0.03354, 47.6946, 0.009500, audit). Steady-state yaw gain 0.97-1.06× model at 40-55 m/s; small-signal straight gain 1.86×; mid-corner at the limit 0.43×. Setup-specific gradients 15-21 mrad per m/s² (Vegas); at a_y 16 blind 0.270/0.258, shop 0.348/0.337, G61 0.425/0.419 rad. | **locally identified** per setup | F14, R14, F27, F33 | T/reference.py:458-466 `steering_model`; T/controller.py:52-94 `VehicleParams`; T/trackref.py:229 (feed-forward uses in-plane a_y), T/tools/ksplan.py:129 (uses horizontal v²κ) |
| **RF pressure → balance (new law candidate)** | wheel angle at a_y 17.64 m/s²: +0.0229 rad/psi (T1-2, CI 0.0175-0.0282) and +0.0269 (T3-4, CI 0.0254-0.0285); replicate disagreement 0.0007-0.0055 rad; G5 lap means give the same sign at the limit: steering per unit a_y +0.000193 ± 0.000017 per kPa (t 11; control SD 0.0012 vs effect 0.0040 per 3 psi) | **bot evidence**, direction settled; magnitude needs the admission re-run (section 5) | F45, W/b_rf_pressure.out | none yet (the law Coach's Progressive Tuning could cite) |

### 1.4 Vehicle: longitudinal, braking, gearing, aero, fuel

| Law | Equation and constants | Status | Evidence | Where in code |
|---|---|---|---|---|
| **Full-throttle / coast model** | a = P·thr/v − C_d v² − C_0 − C_c |a_y| − B·brake. IRP: P 325.301 (specific power, m²/s³), C_d 0.000250947, C_0 1.382521, B 14.3585, C_c −0.00355 → clipped 0 (0.0775 in the `opt_cc` references); Kansas v8 312.056, 0.000282176, 0.743658, 13.2102, 0.043906 (A). Coast at IRP: 1.05 + 0.000478 v² + 0.0653 |a_y| (resid SD 0.20; mid-corner 3.25 vs model 1.88). Vegas full-throttle refit C_0 0.705, C_c 0.060 (rms 0.24; Kansas over-predicts by 0.43-0.58 m/s² at a_y 22-30). Top speed `v_top` 62 (comment says 60.3). | **conditional lumped fit**; power, drag, rolling, engine braking and scrub not separated | F14, F19 (the better coast fit made a slower controller), F24/R15 | T/controller.py:112-140 `accel`, `accel_for_pedals`; T/trackref.py:51-56 `long_accel`; T/lapsim.py:50-54, 97-104; refs `meta.vehicle_model` |
| **Braking and brake bias** | bot: linear brake coefficient B inside the ellipse; Coach: bias forward for loose entry, rearward for tight entry, one garage step | brake gain **conditional**; bias direction **qualitative prior** (BB68 −0.031 s/lap, CI −0.067..+0.005; probe null) | F6, F16, F18, R11 | C/tuning_engine.py:620, 640; T/controller.py |
| **Gearing** | rpm per m/s by gear 407.2/264.4/199.4/150.9; shift 8300, redline 9100 | ratio **identified**; shift policy **assumed** | refs `meta.rpm_per_mps_by_gear` | T/controller.py; T/trackref.py `v_max_rpm` |
| **Aero** | C_d v² only; optional `down·v²` (default 0); tape Q: −0.022 rad of wheel at a_y 22 (more front downforce), +0.051 s/lap over flying laps 1-2 for the bot at Vegas, water/oil rise at IRP | drag **conditional**; downforce/balance maps **unidentified** | F26, F27, R11 | T/trackref.py `down`; T/lapsim.py none |
| **Fuel** | burn 0.176 + 0.0162·throttle-s + 0.0000976·E_drive L/lap (RMS 0.009; mean 0.543); idle 0.00138 L/s; fuel vs tyre age corr −0.997 → mass effect on pace **unidentified**; "0.08-0.10 s by lap 60" in TIRE-AND-TRACK-MODEL is superseded | burn **conditional**; mass effect **unidentified** | R9, F40, F42 | Coach `strategy_model.py` uses measured burn; T/tools/fuelburn.py |

### 1.5 Track

| Law | Equation and constants | Status | Evidence | Where in code |
|---|---|---|---|---|
| **Banked-road mechanics** | a_y = v²κ_h cos θ − g sin θ; a_z = g cos θ cos φ + v²(κ_h sin θ + κ_v); dv/dt = a_x − g sin φ; G 9.80665 | **conditional Newtonian** (point mass; derivatives and smoothing matter) | TIRE-AND-TRACK-MODEL "Track" | T/lapsim.py:13-18, 207-212 `lateral_needs`; T/trackref.py:48 `in_plane_ay` |
| **IRP geometry** | reference length 1,077.24 m vs track 1,083.43 m; apex curvature from the 1 Hz apex fit κ 0.0113 (T1-2, R 89 m) and 0.0103 (T3-4, R 97 m) with θ = 12° assumed; the track model's quadratic surface gives **7.5° at the reference line** falling to 1° at e = 15 m, while the notes say "~12°"; the apex intercepts (−2.98 ± 0.05 and −1.68 ± 0.14 m/s²) do not fit one banking, so the fixed-line assumption fails across pace levels (W/d3) | geometry **identified conditional on calibration**; banking **inconsistent between model and notes** (section 4, item 8) | T/model/irp_track.json `z_coef`; W/d3_apex_fixed_theta.out; TIRE-AND-TRACK-MODEL "Banking ~12 deg" | T/trackmodel.py (z(s,e) = c0 + c1 e + c2 e²); T/estimator.py |
| **Vegas geometry** | derived central radii 232.3 m (231.7-233.2) both turns; survey banking 20.2°/20.0°; G61 edges vs surveyed wall +0.42 m (rms 0.49), inside paint +0.17 m | **measured**, edge definition unknown | BASELINE-TUNING table; F29 | T/survey.py, surveybuild.py |
| **Track temperature** | T_eq = 2.30 + 0.940 T_air + 15.3 max(0, sin α_sun)³; first-order lag 25 min; RMS 0.28 °C in session 16 (quantisation floor 0.16); exponent bootstrap 3.0-3.5; fails with hidden cloud (sessions 3, 6) | **conditional** | R1 and corrections | none |
| **Rubber / usage** | no channel; `SessionTrackRubberState` label; session 17 "moderately high usage" differs by +13 °C track; E3 launches failed the weather match | **unidentified** | R10, F41, F43 | Coach `track_usage` is a matching feature (C/race_foundations.py:1280) |
| **Track-temperature → lap time** | inside one block 0.050 s/lap/°C (bot, Vegas) and 0.031 (G61 humans); IRP 0.025 ± 0.015 fit **failed held-out** by +0.055 s/lap; never normalise across sessions | **bot evidence, within-block only**; cross-session correction **rejected** | F15, F31, R16 | none (correctly) |

### 1.6 Setup → behaviour (what the garage changes do)

| Change | Measured response | Status | Evidence |
|---|---|---|---|
| **RF cold pressure +6 psi** (IRP, fixed pace) | +0.137/+0.162 rad of wheel at a_y 17.6; lap time +0.036 s/lap even at fixed pace (23.4513 → 23.4868; replicate spread ≤ 0.0026 s); RF hot +44.5 kPa; RF wear +25 %; crown −0.37 °C relative | **bot evidence** (W/b) | F45, W/b |
| **RF +3 psi at the limit** (G5) | lap +0.21 and −0.07 (two screens) vs control noise SD 0.115 s/lap; regression over 9 runs +0.078 ± 0.039 s/lap per 3 psi; steer-limit time +0.6-0.9 s/lap; T1-2 limit pace −1.2 to −1.7 % | slower-or-null, tighter | W/b; F7 corrected |
| **LF/LR pressure** | LF 14→20 psi: outboard share 57.1→63.9 %, LF rate 0.048→0.034 %/km; LR +3/+6 → LR wear 0.062→0.085/0.093 %/km; RR +6 psi +0.37 s/lap (single run) | LF split **identified**; others **bot evidence** | R7, R11 |
| **Camber package** (Vegas P4: LF +4.7→+4.1, RF −4.9→−4.2, with cross 53.1→53.6 %, FARB preload 0→−7.1 Nm, truck-arm −1.9→+16.8 Nm) | −0.144 s/lap (halves −0.169/−0.119); −0.032 rad of wheel at a_y 22 | **package**, not camber alone (LV-P6C unrun) | F26, F27 |
| **RR track bar +13 mm** (Vegas) | +0.053 s/lap; −0.014 rad of wheel with +0.0007 rad more crab (looser); IRP v5 (RR −25 mm, with toe and preload side effects): rear let go earlier | direction agrees with the garage hint; neither test is clean | F26, F27, TUNING-NOTES "I: rear track bar" |
| **Rear toe +1/−1 vs +5/−5 mm** (G61 S007 vs blind) | the only unique item of the fastest setup: +0.155 rad of wheel and −0.011 rad of crab at a_y 16; model forecast toe/0.68 m → −0.006 rad crab, +0.07 rad wheel (about half) | **registered forecast, untested** (LV-P6S H1) | F33, R13 |
| **Tape Q** | −0.022 rad at a_y 22; +0.051 s/lap (Vegas flying laps 1-2); IRP RF surface −4 °C, RF wear 0.178 %/km | **bot evidence** | F26, F27, R11 |
| **Brake bias 68 vs 65 %** | −0.031 s/lap over 30 laps (CI crosses 0); probe null | unresolved at 0.05 s/lap | F6, F16, F18 |
| **Fast-setup priors (observational)** | Vegas fast cohort vs default: cross +3.7 pp, RR spring −79 N/mm (6/7 soften), track-bar split −76 mm (less rake), LF camber +1.4°, RF caster −0.6°; Charlotte: front bar +20 mm (9/13), RR/RF cold pressures unchanged 13/13; race and qualifying must not be pooled (Vegas R cross 54.2 % vs Q 59.75 %) | **observational** (package families, not isolated gains) | BASELINE-TUNING "What actually moves consistently" |
| **Coach garage rules** | entry loose → bias forward; entry tight → bias rearward; centre tight/loose → cross weight ∓ one step; exit loose → "Lower both rear track-bar ends one small equal step; preserve rake"; exit tight → cross before rake; bumps → one HS compression click | **qualitative priors** with eligibility screens | C/tuning_engine.py:600-760 |

### 1.7 Coach statistical models (not physics, and labelled so)

| Model | Form | Status | Where |
|---|---|---|---|
| Historical tyre predictor | per band rate (100 − remaining)/green_laps, weighted 1/(0.25 + feature distance)²; remaining extrapolated linearly; min 3 eligible observations from 3 sessions; capability pace = observed pace + slope × (age difference); confidence high/medium/low from session count, effective n and median distance | **assumed statistical predictor** (section 2, A1/A2) | C/race_foundations.py:1309-1512 |
| Model gate | ≥ 8 held-out events, > 5 % over laps-only and cohort-median baselines, interval coverage within 0.10 | **unwired** library (section 2) | C/tire_model_gate.py:51-66; zero callers |
| Strategy deterioration | linear lap slope; stint time n t_0 + d n(n−1)/2 | **assumed bounded scenario model** | C/strategy_model.py:24-36, 335 |
| Stress and energy proxies | brake ∫brake·v dt, steering ∫|steer|·v dt; stress 0.35/0.35/0.30 brake/steering/lateral; tyre energy ∫|a|v dt (J/kg), slip-weighted variant by sin|β|; wear-energy diagnostic refuses < 5 paired observations | **defined proxies**, uncalibrated (A11) | C/analysis_engine.py:1996-1997, 2518-2520; C/tire_energy.py |
| Starting tune | selection of an existing source `.sto`/`.htm` by purpose and shape; no physics generation | **artifact selection** | C/starting_tune.py |

---

## 2. Contradictions and their resolution

### 2.1 The three code-level claims (verified against the code)

**A1 — "The Coach's traced predictor path bypasses its newer validation gate." Real.**

The gate module declares the contract and that it is enforced: *"So the order is fixed, and it is enforced here rather than trusted."* and *"Nothing here trains anything, opens a file, or reads a clock. It decides whether a model is allowed to exist and how honestly it may be described."* (C/tire_model_gate.py:1-40). Its constants are `MINIMUM_HELD_OUT_EVENTS = 8`, `CALIBRATION_TOLERANCE = 0.1`, `MINIMUM_RELATIVE_IMPROVEMENT = 0.05` (lines 55-66). A repository-wide search for its public functions (`gate_decision`, `evaluate_candidate`, `model_representation`, `summarize_cohorts`) finds **no caller outside the module and its tests** (W/grep, 2026-10-09).

The shipped path is:

```
C/workflow.py:2805-2809
    tire_learning = analysis.get("tire_learning")
    if isinstance(tire_learning, Mapping):
        tire_learning = dict(tire_learning)
        tire_model = store.update_tire_learning(tire_learning)
        tire_learning["prediction"] = tire_model.get("prediction") or {...}

C/storage.py:3479-3481  (_update_tire_learning_locked)
        current_age = package.get("current_tire_age") or {}
        prediction_context = package.get("prediction_context") or {}
        prediction = build_tire_prediction(list(observations.values()), current_age, prediction_context)

C/analysis_engine.py:7342-7349
    if tire_prediction.get("status") == "predicted" and predicted_life is not None:
        tire_metrics.append(metric("Estimated life", round(predicted_life, 1), "green laps", "predicted", ...
```

and the same for "Capability pace" (7354) and "Estimated tire-age cost" (7366). The only guard on the path is the predictor's own `if len(eligible) < 3 or len(sessions) < 3: return {... "status": "unavailable"}` (C/race_foundations.py:1356-1368) and its self-assigned confidence tiers (1457-1471), which are count/similarity heuristics, not held-out calibration. The gate was designed as the first diagnostic of `MODEL-UTILITY-001` to run before the similarity machinery (`.git/Agents/reference/debate/second-debate-turn-01-claude.md:42`); the predictor predates it. So the gap is real and unclosed: numbers labelled `predicted` reach the surface without a gate verdict. The predictor's `status`/`reason`/`confidence` fields are honest about counts; they are not what the gate promised.

**A2 — "Historical tyre matching omits exact setup and simulator-build identity." Real.**

The matching key is built from family, car, track, configuration, **fixed/open** and compound only:

```
C/race_foundations.py:1092-1104
    context = {"family": family, "car_id": ..., "car_path": ..., "track_id": ..., "track_config": ...,
        "setup_type": "fixed" if identity.get("is_fixed_setup") else "open",
        "tire_compound": tire_compound}
    context_key = _safe_slug(f"{family or 'unsupported'}-{car_id or car_path}-{track_id or track_name}-{track_config}-"
        f"{context['setup_type']}-{tire_compound ...}")
```

and the payload calls this *"exact car/track-configuration/setup model and tire compound"* (line 1354) and requires "the same ... setup type" (1361). The matching features (`tire_feature_values`, 1261-1308) are track/air temperature, usage, wetness, caution fraction, wheel lock, wheelspin, ABS, yaw p95, brake energy and steering work; nothing from the garage sheet. Coach *does* compute a setup identity on the same analysis (`setup_fingerprint` and `garage_setup_fingerprint`, C/analysis_engine.py:1310-1319) and the tuning tables store `setup_fingerprint` (C/storage.py:1351), but neither reaches the tyre context or features. No simulator build string exists in the identity (the only `version` in C/ibt_reader.py:857 is the IBT file-format version). F30 shows why this matters: a 2026 S1 setup fails tech in the S4 car, and R17/R18 show tyre wear differing by 1-2 pp over 15 laps between setups of the same car. Two open-setup observations with different garage sheets on different builds are pooled as the same "setup model".

**A3 — "Bot planning mixes banked limits with flat-road force allocation." Real, and quantified.**

The lateral limit is banked:

```
T/trackref.py:32-45
def lat_limit_speed(kappa, bank, mu, down, v_cap=120.0):
    """... m v^2 k cos(b) - m g sin(b)  <=  mu (m g cos(b) + m v^2 k sin(b) + m down v^2) ..."""
    den = k * c - mu * (k * s + down)
    num = G * (mu * c + s)
    return min(v_cap, math.sqrt(num / den))
```

but the longitudinal budget in the same planner is flat-road:

```
T/trackref.py:111-117 (solve_speed, forward pass)
            mg = [float(m) * G for m in mu]
            ...
            if combined:
                u = (vi / vl[i]) ** 2
                room = mg[i] * sqrt(1.0 - u * u) if u < 1.0 else 0.0
```

(same at 134-137 for braking). Two errors: `u = (v/v_lat)²` is the lateral utilisation only when a_y ∝ v² and a_z is constant; on banking a_y has the −g sin θ offset and a_z grows with v². And the budget is μ·g, not μ·a_z. Worked example at Vegas (κ = 1/232.3, θ = 20°, μ = 1.0): v_lat = 69.9 m/s; at v = 0.85 v_lat = 59.4 m/s the planner's u = 0.7225 while the true a_y/(μ a_z) = 10.92/14.41 = 0.758; the planner's drive room is 9.807 × √(1 − 0.7225²) = 6.78 m/s² where the banked budget is 14.41 × √(1 − 0.758²) = 9.40 m/s² (W, hand calculation from the formulas above). The controller repeats the flat scaling at run time: `vlat = [p[i] * self.vlat[i] ...]; mu = [p[i] * p[i] * self.mu[i] ...]` (T/controller.py:1177-1178), which equates pace² with friction only under flat, constant-load assumptions. Related inconsistencies the audit listed are also present: `ksplan.replan` writes the steering feed-forward with horizontal `v[i] ** 2 * kappa[i]` (T/tools/ksplan.py:129) while `trackref.build` uses `ch["ay_inplane"]` (T/trackref.py:229); `lapsim` applies the grip map `mu_scale` in the envelope branch (`/ sc`, lines 222, 243, 260) but not in the ellipse branch (lines 225, 265). Practical weight: on the IRP line the planner is reconciled by the fitted pace factor and the zone μ, so lap plans are self-consistent at the calibration point; the error shows up as a 28 % under-allocation of drive force on 20° banking and as a non-physical utilisation number wherever the planner's `usage` is read as grip usage.

### 2.2 The audit's other contradictions, with the data's verdict

| # | Claim (A) | Verdict after this pass |
|---|---|---|
| A4 | "Kansas track-bar inversion" is really the IRP v5 → Vegas forecast; Vegas RR +13 mm agreed with the garage hint; Coach moves both ends equally | **Stands.** The Coach rule (C/tuning_engine.py:705) is "Lower both rear track-bar ends one small equal step; preserve rake"; both experiments moved one end and changed rake, toe and preload. No evidence against the Coach rule exists; none for it either. |
| A5 | The camber result identifies a package | **Stands** (F26 lists cross 53.1→53.6 %, FARB preload 0→−7.1, truck-arm −1.9→+16.8 Nm). LV-P6C is registered and unrun. |
| A6 | 85 % ceiling is a controller limit | **Stands**; F47 adds that the probe's A/A precision (SD 0.0067/0.0050 vs ≤ 0.005 gate) is insufficient for setup ranking. |
| A7 | RF pressure sign unresolved; F45's gate failed | **Corrected by the data (section 3b).** The balance sign is settled in three independent measures (fixed-pace wheel angle, lap-mean steering per unit a_y at the limit, steering-limit time); the two opposite-sign 5-lap screens are one noise draw: control-to-control SD 0.115 s/lap against screens of −0.07 and +0.21. Lap time at the limit is slower-or-null (+0.078 ± 0.039 s/lap per 3 psi). The "hot middle = overinflated" reading is wrong in this regime (crown becomes more negative with pressure). |
| A8 | Old claims superseded by the registers | **Stands**; the registers prevail (F12 hypothesis, F13 regime summaries, R6 association, R9 fuel unidentified, R2 approximate, R3 unidentified). |
| A9 | Temperature is not a normaliser or health verdict | **Stands** (F15 held-out failure; F34 hotter-RF setup fades less). |
| A10 | gripmap's documented torque criterion is not in the mask | **Confirmed in code**: torque is loaded (T/gripmap.py:87) and the front-slide mask is `(|yaw_ss| > 0.15) & (yaw < 0.80 yaw_ss) & (ay > 8)` (line 93); `--human` is parsed (182) and the first sample file is always the human one. Documentation defect, not a physics error. |
| A11 | "Energy" grade vs load-only shares | **Stands** (C/tire_energy.py); Rust port not inspected here. |
| A12 | Build compatibility by label; psi constant | **Stands and sharpened**: Coach uses `KPA_TO_PSI = 0.14503773773020923` (C/analysis_engine.py:76) and `6.894757293168` (C/setup_catalog.py:227); the garage lattice constant is 6.8911603 (F36). Two named conversions are needed (SI for physical channels, garage for setting display/lattice reasoning), not a global replacement. |

Two internal inconsistencies the audit did not list:

- **Crab line on the Vegas default**: F20's fit 0.0146 + 0.0016 a_y (P3 run 12, push edge, slides included) predicts 0.050 rad at a_y 22; the P4 fixed-pace default measured 0.0436 (F27). The traction control's reference crab is 0.006 rad too high at the limit if it carries the F20 line; each setup needs a fixed-pace crab line (R13 says so; the default's own two numbers disagree).
- **IRP banking**: the track model's surface gives 7.5° at the reference line in both turn cores and ~1° at e = 15 m (T/model/irp_track.json `z_coef`, W/a); TIRE-AND-TRACK-MODEL states "~12 deg" and the apex-intercept test cannot choose (section 3d). Everything that converts a_y into "grip usage" at IRP (TIRE-AND-TRACK-MODEL: "v × yaw rate / g over-reads it on the banking, 1.4 where the road-plane usage is ~0.9") depends on which banking is right.

---

## 3. Existing-data checks run for this document (no sim time)

### 3a. The pressure float expression (F36 ledger: 816 rows, 31 settings 13.5-45.5 psi, ascending and descending, NAS `e1-20261006\ledger-all.csv`)

- **Path independence holds for the lattice.** Every one of the 31 settings has exactly one live float32 value whenever it is reached from any direction (788 garage rows), with one exception: at the RF/RR garage minimum (28.0 psi) a value 8 ulp low (192.952377 instead of 192.952499) appeared twice, RF at ledger state g086 (tick 20128) and RR at g116 (tick 22878), each one state after arriving at 28.0 from 28.5, and each reverting to the modal value 301 ticks (~5 s) later with no further click [W/a2_pressure_float_clamp.out]. So the "two values for 28 psi" in F36 is a transient of the clamp at the minimum, not a cumulative-arithmetic lattice.
- **Cumulative stepping is refuted**: v_{n+1} = f32(v_n ± f32(0.5 k)) reproduces 84 of 178 click transitions; anchored stepping f32(min + n × step) reproduces at best 12/15 (left axle) and 11/16 (right) [W/a_pressure_float.out, a4_anchored.out].
- **A single-rounding direct product is also refuted**: no real k makes f32(psi × k) match all 31 modal values (the per-setting feasible k intervals do not intersect: 14.0 psi needs k ≥ 6.891161237, 32.0 psi needs k ≤ 6.891160011); the best any single constant achieves is 21/31, misses are ±1-2 ulp; implied k per setting spans 6.891159762-6.891161237, about three float32 ulps of k [W/a2, a3_expression_grid.out]. Two-stage forms (psi → Pa in float32 → × 0.001) do slightly better (23/31) but none of 13 families reaches 31/31.
- **What it decides**: the lattice is a deterministic function of the setting (safe for Coach to tabulate and for pit requests to target), k = 6.8911603 ± 0.0000007 stands as the identified constant, and the exact float expression stays **unidentified** because it is not a one-constant, one-rounding product. **What settles it** (zero laps, ~15 garage minutes): read the same 31 settings with the garage in Metric units (does the display show 96.5 or 96.48?), read the `.sto`'s stored value for each, and extend the lattice to the RF/RR maximum; a two-rounding expression leaves a signature in which settings miss by exactly one ulp.

### 3b. The RF pressure sign at matched state

**Tuning sweep rf-sweep-1 (6 Oct, fixed pace 0.93, out-lap + 8, ABC/CBA, R setup, static weather 33.9 °C track)** [W/b_rf_pressure.out; `results-queue\tuning-sweep\analysis-report.json`, `secondary-report.json`, `laps.csv`]:

| RF cold psi (kPa) | wheel at a_y 17.64, T1-2 / T3-4 [rad] | lap mean laps 2-8 [s] | RF hot [kPa] | hot/cold | RF surface L/M/R [°C] | RF worn after out+8 [%] |
|---|---|---|---|---|---|---|
| 28 (192.95) | 0.635, 0.638 / 0.741, 0.746 | 23.4500, 23.4526 | 208.90 | 1.0827 | 54.8/53.6/55.3 | 0.937, 0.917 |
| 31 (213.63) | 0.723, 0.727 / 0.828, 0.830 | 23.4713, 23.4736 | 231.18 | 1.0822 | 56.0/54.7/56.7 | 1.065, 1.038 |
| 34 (234.30) | 0.774, 0.773 / 0.905, 0.905 | 23.4856, 23.4880 | 253.42 | 1.0816 | 57.1/55.7/58.0 | 1.164, 1.153 |

Slopes: wheel +0.0229 (T1-2) and +0.0269 (T3-4) rad/psi, monotonic in both replicates [F45]; lap time **+0.0059 s/lap per psi** at a fixed commanded pace (residual SD 0.0025 s), i.e. the tighter car is slower even below the limit with the guard in log-only mode; RF hot **+7.42 kPa per psi** of cold (1.077 kPa/kPa; gauge-proportional predicts 7.46, absolute-pressure at equal gas temperature 7.27, and the +2.3 °C surface rise would add about +0.9 kPa to the absolute prediction, so R3 stays unidentified but one matched-temperature pair would now decide it); RF wear **+0.039 pp per psi** (+25 % over 6 psi); crown M − flank mean −1.46 → −1.83 °C.

**G5 screens at the limit (26-27 Sep, 5 laps, ages 2-5; `relationships\laps.csv`)**: RF31 vs the two bracketing controls +0.212 (run 104) and −0.072 s/lap (run 113); RF34 +0.162 (run 114); the adjacent control-to-control differences are −0.078, +0.174, −0.101, +0.091 (SD 0.115 s/lap). Over the 9 control/RF runs: lap time **+0.0038 ± 0.0019 s/lap per kPa** (= +0.078 s per 3 psi, t 2.0); lap-mean steering per unit a_y **+0.000193 ± 0.000017 per kPa** (t 11; +0.0040 per 3 psi = +7.7 %, against a control SD of 0.0012); steering-limit time +0.6 to +0.9 s/lap; T1-2 limit pace −1.2 to −1.7 %; RF hot +21.9/+43.5 kPa.

**Decision**: more RF cold pressure makes this car tighter at every level tested (three independent measures, two days, two builds), reaches the steering limit sooner, wears the RF faster, and is slower or null on lap time (+0.08 ± 0.04 s/lap per 3 psi at the limit; +0.036 s/lap per 6 psi at fixed pace). The "opposite signs" in F7 were one noise draw. Domain: bot, IRP, R setup, 28-34 psi (28 is the garage minimum). F45's registered 0.004 rad repeat gate was set below the instrument's actual repeat (0.0007-0.0055 rad); the law is admissible on a gate of 2 × the observed replicate SD, which section 5 registers.

### 3c. Crab line per setup from the Vegas P6 balance data

Infeasible on disk: `vegas\p6\` holds only the summary at a_y 16 and at the push edge (blind 0.0334/0.0424, shop 0.0294/0.0394, G61 0.0226/0.0355 rad, from `LV-P6-balance-ay16.txt`); the 60 Hz drive logs that a per-setup line needs are on the sim (`logs\lv\p6`). The two-point gradients (0.0010-0.0014 rad per m/s²) are not lines because the push-edge value includes slip. The default's own inconsistency (0.050 predicted by F20 vs 0.0436 measured in P4 at a_y 22) stands as item 4 in section 4.

### 3d. Load sensitivity from the 1 Hz series (89,663 on-track samples; 3,346 + 3,131 per-lap apex points, bot on the unmodified R/OPT/Q line, cold 96.5/193 kPa) [W/d2_apex_load.out, d3_apex_fixed_theta.out]

- Apex relation (lowest-speed 1 s sample per lap and turn): T1-2 a_y = (0.01104 ± 0.00004) v² − (2.979 ± 0.052); T3-4 a_y = (0.01011 ± 0.00009) v² − (1.680 ± 0.143) m/s². The intercepts should be −g sin θ: −1.28 at 7.5°, −2.04 at 12°. T3-4 sits between (2.5-2.8 SE from either); T1-2 implies 17.7°, which no banking claim supports, so the fixed-line assumption fails (slower laps sit at a different apex radius) and **the banking question is not decidable this way** (use VertAccel at 60 Hz instead; section 4 item 8).
- Limit by tyre age (p99 of apex a_y/a_z with θ = 12° assumed): T1-2 1.164 → 1.077 (age 2-5 → 46-70, −7.5 %); T3-4 1.221 → 1.128 (−7.7 %); apex a_y p99 −9 to −10 %; apex speed p99 −3.3 to −4.4 %. This is the cleanest grip-vs-age number in the data set and agrees with F13/F16.
- Load sensitivity: among the top-5 % apex samples the geometric a_z spans 14.53-15.43 (T1-2, 6.2 %) and 12.24-12.72 m/s² (T3-4, 3.9 %). A tyre with μ ∝ F_z^−0.1 would move the ratio 0.4-0.6 %, below run-to-run scatter: **not identifiable at IRP alone**. Cross-track, the limit ratio is 1.16-1.22 at a_z 13.3-13.5 (IRP), 1.22 at 19.0 (Vegas human Q lap, derived) and 0.98 at 16.2 (bot ceiling, F21), confounded by aero, surface and controller; **indicative, not evidence**.
- Humans (127/119 laps) run +0.67 ± 0.08 and +0.42 ± 0.11 m/s² above the bot's a_y-v² line at the same apex speed: a 3-4 % tighter apex radius, i.e. a different line, not more grip.

---

## 4. What is unclear, ranked

**H** = rank for coaching a human; **B** = rank for generating a baseline setup. Costs are active sim minutes (launch and garage wall time extra). "Signature" states what each candidate law would show. Items 1-11 follow the audit's list, corrected; 12-14 are added.

| H / B | Item | What settles it | Cost | Signatures |
|---|---|---|---|---|
| 1 / 10 | **Whether Coach's `predicted` tyre numbers deserve their label** (A1/A2). | Existing data only: run `tire_model_gate.summarize_cohorts` over the archive (Joshua's command), then `evaluate_candidate` against the laps-only and cohort-median baselines on event-held-out splits, with setup fingerprint and build added to the context key. Wire the verdict into the three `predicted` metrics or render `unavailable`. | 0 | Genuine value: beats both baselines by > 5 % with coverage within 0.10 on ≥ 8 held-out events. Leakage/cohort mixing: performance collapses when setup/build are keyed separately. If cohorts rarely reach n = 3 the feature is structurally unavailable and the scope is collection. |
| 2 / 4 | **Frames and crab: the body-slip reference per setup** (F20 vs F27 disagree by 0.006 rad on the same default; G61 setups crab 0.010 rad less). | Existing data: refit β vs a_y per setup from the fixed-pace logs on the sim (`logs\lv\p4`, `p6`), slides excluded, and compare `v·yaw_rate`, heading-curvature and accelerometer a_y. Then a 5-minute straight and slow-corner steering test in both directions. | 0 + 5 | Normal crab: β linear in a_y without yaw divergence; a sign/offset error reverses or offsets all setups alike; a setup effect moves intercept and slope per setup only. |
| 3 / 1 | **Per-tyre force law: load sensitivity, slip curves, combined limit.** | Not identifiable at IRP (3d). Needs matched-state steady corners at two normal loads: Vegas fixed-pace passes at 0.70/0.76/0.82 of human speed (a_z 15-17.5) and IRP at 0.90/0.93/0.96 (a_z 12.5-13.5), same setup family, reading wheel angle and crab vs a_y plus shock deflection × spring rate as corner-load proxies; then progressive brake/drive sweeps for the combined boundary. | 60 + 40 | Constant μ: a_y/a_z at onset invariant with a_z. Load-sensitive: ratio falls with a_z at the same slip. Controller boundary: moves when the steering cap or guard changes, not with a_z. |
| 4 / 2 | **Setup → static pose → load transfer** (camber vs wedge/preload; track-bar mean vs rake; perch vs ride height). | Stationary garage mapping (section 5, experiment 1): one-click ± perturbations with the full readout captured per click and the baseline restored between them. Builds LV-P6C's "camber only" setup as a by-product. | 45 garage, 0 laps | Isolated effect: the readout moves only in the clicked control and its documented dependents; package coupling: cross weight, preloads or ride heights move with camber or one bar end. |
| 5 / 3 | **Separate pressure, surface heat, bulk heat and wear effects on grip.** | E2 tyres-only zero-fuel stop and E6 worn-cool-restart (both need the drive-in pit path); meanwhile the RF/LF pressure sweeps at fixed pace give the pressure arm without the thermal confound. | 60 + 60 (+ pit-path build) | Reversible thermal: pace returns after cooling and re-fades on the same history. Persistent wear: W stays slower than F at matched fuel after the reheat transient. Fuel-only: no B − A gain at matched post-stop fuel. |
| 6 / 5 | **Engine/rolling/aero/corner-loss separation; front/rear aero.** | Refit existing straights and turns with RPM, gear, throttle, grade and tape explicit; validate on whole held-out runs (F19 warns a better fit can make a worse controller). Then matched pull/coast runs in both directions. | 0 + 20 | Power ∝ 1/v; drag ∝ airspeed²; rolling ≈ flat; engine braking varies with gear; scrub follows a_y (C_c 0.060 at Vegas vs 0 at IRP). Aero load grows with v² and ride height, unlike a mechanical balance shift. |
| 7 / 6 | **Wear functional form and thermal dynamics** (R6 exponent 3.46 vs 2.70 vs 5.40; no family covers F46's held-out runs). | E7 thermal steps with achieved-forcing gates (F44 fix: high plateau 0.96, not 0.98); recompute exposure over the same out/timed/in distance as the endpoint; smooth vs peaky work at equal total. | 55 | Linear dose: equal wear at equal total work. Convex power: more wear from peaks. Temperature-activated: follows thermal history at equal dose. One-node heat: single exponential; two-state: delayed response and hysteresis after high → low. |
| 8 / 8 | **True surface: IRP banking 7.5° (track model at the line) vs 12° (notes); apex curvature; usable width.** | Existing 60 Hz IBTs: a_z − g cos θ = v² κ sin θ with κ from the heading rate gives θ directly; cross-check with the Alt-derived model at several smoothing scales. | 0 | A real feature stays at the same station across smoothing and affects accelerometer, Alt and yaw traces together; a differentiation artefact moves with smoothing or ride height. |
| 9 / 7 | **Fuel mass vs tyre age** (corr −0.997; F42 resets refuel). | E5 fuel-burndown (planned) or E2's fuel-only stop; human telemetry refuelling discontinuities first. | 0 + 150 | Mass: an immediate step after refuelling with no wear change; ageing: continuous. |
| 10 / 9 | **Gas state** (absolute vs gauge; p_atm 100.4 kPa from the rear axle; front axle violates symmetry). | One matched-temperature pair: reset sets at RF 28 vs 34 psi, capped 6 laps, accept if RF surface within 0.5 °C/lap; the sweep's slope 7.42 kPa/psi already brackets the two laws by 0.2 kPa/psi. | 15 | Absolute: hot slope 1.055 kPa/kPa at equal gas temperature; gauge-proportional: 1.083; a volume change: ratios differ between cooled and hot endpoints. |
| 11 / 11 | **Exact pressure float expression; fitting-temperature rule.** | Metric-display and `.sto` readings of the 31 lattice points (3a); E8 parked cells at a second ambient level for T_fit. | 15 garage + 70 parked | One-rounding product: all 31 within one float32 rounding of one k (refuted). Two-rounding: a reproducible pattern of 1-ulp misses by unit path. T_fit = T_air + c: slope 1 across ambient; fixed absolute: slope 0; history-dependent: varies with dwell. |
| 12 / 12 | **Track thermal forcing and rubber** (R1 fails under hidden cloud; E3 launches never matched weather). | E8 parked weather cells; usage cells only after two launches match within 0.2 °C air / one °F bin. | 70 + 80 | Solar lookup vs lagged cubic differ near dawn/dusk; a rubber effect survives weather matching, an apparent one disappears. |
| 13 / 13 | **The bot's limit instrument** (F47: probe A/A SD 0.0067/0.0050 vs ≤ 0.005; mixed onsets). | Steering-cap and shaping changes on the probe before any further at-limit ranking; register the onset criterion from controller code. | 30 | A controller boundary moves with cap/shaping; a tyre boundary does not. |
| 14 / 14 | **Coach's garage-unit conversion** (SI vs lattice constant). | Code change, no sim: two named constants. | 0 | — |

---

## 5. The next five experiments, in order (registrable in `iRacing-utility\experiments\`)

All follow `experiments\TEMPLATE.md` (headings fixed), pin `--steer vjoy --pedals vjoy`, run solo offline Test Drive on the Supra with the owner's control files backed up and the bot's profile selected through iRacing (Settings > Driving > Input Calibration), one lease, a Coach lab window open, no idling holds (F25), every attempt kept. "Matched temperature" means: static weather (x0) at one declared track °F bin, the same sim time of day, the same preparation (fresh set per run from the pit service; reset sets only where stated), and the first timed lap's air within 0.2 °C of the block's first run; a violation is recorded, never corrected by a temperature model (F15).

**1. `garage-map-1` — stationary setup → readout mapping at Vegas (and IRP), zero laps.**
Question: which readouts move when one control moves (item 4). Block: from the loaded default, apply one click ± in each of LF camber, RF camber, LR perch, RR perch, LR track bar, RR track bar, FARB preload, truck-arm preload, LF/RF packer, cross weight (via the LR perch), with the full CarSetup leaf vector (84 leaves, `results\e5-CarSetup-leaf-names.json`) and the live cold pressures captured before and after each click through the garage-harvest watch; restore the baseline between controls by reloading the unmodified file (never saving over it); 2 repeats per control, order reversed in the second. Build `claude LV R CAM only` at the end (truck-arm preload −1.9 Nm, FARB slack 1 mm with preload 0, cross 53.1 %) so LV-P6C can run. Prerequisites: pit lane not involved; the car stays in the garage; the sim window must be large enough to see the whole garage page (TUNING-NOTES "Running the garage at Vegas"). Outcome: a coupling matrix (which leaf moves per click, by how much), with "Too Low"/tech flags recorded. ~45 min.

**2. `pressure-balance-2` — RF 28/31/34 psi and LF 14/17/20 psi at fixed pace, admission re-run (IRP).**
Question: the pressure → balance, hot-pressure, wear and crown laws per psi (items 5, 10, 11). Block: two sub-blocks of 12 runs × 9 laps (out-lap + 8 at pace 0.93, `--guard-log-only`, reference `refs/irp_line_opt_ft3b.json`), RF sweep ABC/CBA/ABC/CBA (4 runs per level), then the LF sweep the same way with the RF at 28; fresh set per run through `--pit-pressures` (no garage), fuel as the stall gives it, no resets. Primary: wheel angle at the lowest common p95 a_y per complex (F35 rule), slope per psi; admission gate: replicate SD ≤ 2 × the rf-sweep-1 replicate SD (0.006 rad) and a 28→34 change > 0.010 rad. Secondary: fixed-pace lap time, RF/LF hot pressure slope per psi (gas-law bracket), RF/LF wear and L/M/R split from the next IBT's first frame, crown. Pit-lane prerequisite: nearest-half-psi delivery confirmed on the orchestrator's `cold pressures now` line (192.95/213.63/234.30; 96.48/117.15/137.82 kPa). Matched temperature: static x0 at 33.9 °C track as in rf-sweep-1, first timed lap air within 0.2 °C across the block. ~2 h.

**3. `fit-temp-cells-1` — E1b + E8: fitting temperature and solar/lag, parked.**
Question: T_fit rule (reset vs pit set) at two ambient levels and the track-temperature forcing law (items 11, 12). Block: 8 parked cells × 8 min, 2 air levels (by sim time of day under fixed weather, e.g. 09:00 and 15:00) × 2 preparations (reset set; pit-service set with tyres only and zero fuel) × 2 repeats, order reversed in the second half; each cell records the first native sample after creation, 20 s active hold, a 3 m move and return; one 24-min continuous trace in a fixed configuration. Prerequisites: pit lane for the pit-service cells (parked amendment path proven in `pressure-lattice-1p`); weather mode must allow fixed clouds. Signatures: T_fit = T_air + c (slope 1), fixed absolute (slope 0), history (varies with dwell); solar lookup vs lagged cubic. ~70 min.

**4. `thermal-steps-2` — E7 with achieved-forcing gates.**
Question: time vs distance vs heat input in pressure/temperature build-up; one- vs two-state thermal dynamics (item 7). Block: 4 runs × 12 timed laps, plateaus 0.94/0.96/0.94 and 0.96/0.94/0.96 (the 0.98 plateau slid in F44), 2 each in balanced order; smoke must show achieved corner forcing within 0.002 of request with no slide; a 60 s stationary tail *without* Esc (the reset tail cannot measure same-set cooling, F44). Prerequisites: fresh pit set per run, matched temperature as above, fuel as the stall gives it. Freeze candidate constants on the first LHL/HLH pair, predict the second. ~55 min with smoke.

**5. `tyres-only-stop-1` — E2: change tyres, add no fuel.**
Question: what a fresh set is worth at matched fuel, and whether old-set fade is thermal, wear or fuel (items 5, 9). Block: 4 trajectories ABBA (A keep tyres, B four new tyres), each 20 timed laps at the frozen plan L, a two-lap capped approach, the stop (`--pit-no-fuel`, equal dwell both arms), then 2 capped laps + 6 timed. Prerequisite that must be built and smoked first: a drive-in pit path (the current return is Esc/Test, which refuels and fits new tyres, F42); fuel at the first post-stop lap matched within 0.10 L across the pair; odometer persistence (A) and reset (B) verified. Matched temperature as above. ~70 min after the pit path exists; the pit path is the gating work item.

Not in the five, ready to run when Joshua offers Vegas time: LV-P6b, LV-P6C (after experiment 1 builds the setup), LV-P6S H1 (rear toe), as registered in `vegas\registrations`.

---

## 6. Summary (10 lines)

1. The only exact layer is garage and delivery: the half-psi lattice at 6.8911603 kPa/psi is a path-independent function of the setting (the "second value" is a 5-second clamp transient at the minimum), pit requests land on the nearest half psi; the float expression is not a one-constant product and stays unidentified.
2. Everything above that layer is conditional (R1-R7 fits), bot evidence (F5-F47) or assumed (the planner's ellipse and constant-μ limit); no tyre force, thermal, wear or setup law is recovered.
3. Codex's three code-level contradictions are all real: the predictor path has no gate caller (C/storage.py:3481 → C/analysis_engine.py:7342), the tyre context key carries fixed/open and compound but no setup fingerprint or build (C/race_foundations.py:1092-1104), and the planner's drive/brake budget is flat-road μg√(1−u²) under a banked lateral limit (T/trackref.py:111-117), 28 % low on 20° banking.
4. The RF pressure sign is settled on balance (+0.023-0.027 rad/psi fixed pace; +7.7 % steering per unit a_y per 3 psi at the limit), slower-or-null on lap time (+0.08 ± 0.04 s/lap per 3 psi); F7's opposite signs were noise (control SD 0.115 s/lap).
5. The sweep also fixed hot = cold × 1.077 + c (7.42 kPa/psi), RF wear +25 % per 6 psi at fixed pace, and a crown that cools relative to the flanks with pressure.
6. Grip vs age is the cleanest grip law in the data: apex limit ratio −7.5 to −7.7 % from age 2-5 to 46-70; load sensitivity is not identifiable at IRP (a_z lever arm 4-6 %) and needs matched-state corners at two normal loads.
7. IRP's banking is inconsistent between the track model (7.5° at the line) and the notes (12°); the apex-intercept test cannot choose; VertAccel at 60 Hz can.
8. The crab reference on the Vegas default disagrees with itself by 0.006 rad (F20 vs F27); each setup needs its own fixed-pace crab line before slip-based traction control is trusted.
9. For coaching, the first job is zero-sim: gate the predictor on held-out data with setup and build in the key, and refit frames and crab from existing logs; for a baseline setup, the first job is the stationary garage map, then matched-state force measurements.
10. The next sim time goes to `garage-map-1`, `pressure-balance-2`, `fit-temp-cells-1`, `thermal-steps-2`, then `tyres-only-stop-1` once a drive-in pit path exists.
