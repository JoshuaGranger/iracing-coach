# WHAT A BASELINE SETUP MUST GET RIGHT

**Build the baseline around dynamic tyre loads, a stable aerodynamic platform, and usable balance over the intended stint.** Track geometry tells us the loads the car must withstand. The missing link is how this particular car converts garage adjustments into wheel loads, alignment, grip and temperature.

The practical path is therefore:

**Current-build setup reference → track-load calculation → coherent setup package → bounded prediction → verification against the next recording.**

A strong baseline is possible before recovering every simulator equation. A reliably near-zero-iteration baseline requires reusable identification of the car, especially its suspension and tyres. The existing audit supports this distinction. [Physics audit][A]

**The dominant quantities**

“First-order” below means capable of materially changing usable grip, sustained speed or stint performance. “Second-order” means normally a refinement after the main platform and balance are correct; a severe error can still make it decisive.

| Setup quantity | Physical quantity it sets | Track inputs that matter | Priority |
|---|---|---|---|
| **Static wedge/crossweight** | Diagonal load distribution; the starting point for the four tyre loads under cornering and acceleration | Road-plane lateral demand, throttle demand, corner duration, entry/exit transitions | **First-order.** Changes which axle/tyre runs out of usable grip. Crossweight is a resulting load distribution, not an independent suspension component. |
| **Nose weight / ballast** | Longitudinal CG location, static front/rear loads, axle force demand and longitudinal load transfer | Braking severity, corner radius and speed, acceleration zones, aerodynamic loading | **First-order.** Moving ballast changes both available axle grip and the force each axle must generate. “More front weight means more front grip” is incomplete. |
| **Front/rear roll-stiffness distribution** | Distribution of elastic lateral load transfer; body roll and platform attitude | Lateral and normal loads, banking transitions, sustained cornering, bumps | **First-order.** With load-sensitive tyres, changing the distribution of load transfer changes axle capacity. Total load transfer is not freely eliminated by fitting softer springs. |
| **Front anti-roll bar: diameter, arms, slack and preload** | Roll stiffness, diagonal preload and—in this asymmetric front suspension—engagement of the front support springs and some heave response | Banking/load transitions, front compression, corner duration, splitter clearance | **First-order and coupled.** Diameter alone does not describe the bar. Preserve arm geometry, slack, preload and spring engagement together. The observed Supra parameter space has no separately adjustable rear anti-roll bar. |
| **Track-bar mean height and rake** | Rear roll-centre geometry, geometric load transfer, jacking and pose-dependent axle movement; associated rear-steer response | Lateral load, rear travel/roll, throttle pickup, banking transitions | **First-order.** Equal movement of both ends and movement of one end are different interventions. The arithmetic mean is only a convenient descriptor, not an exact roll-centre calculation. |
| **Rear toe, truck-arm geometry and rear steer** | Rear thrust direction, effective rear slip angles, anti-squat and axle motion through travel | Exit traction, speed, lateral load, acceleration and braking | **First-order for this evidence.** Rear crab materially affected the Vegas controller’s throttle use and tyre loading. It must not automatically be classified as a slide. |
| **Springs, perches, packers and coil-bind state** | Static pose, effective wheel rates, support engagement, travel limits, pitch/roll and aerodynamic attitude | Normal load, airspeed, banking, vertical curvature, bumps and transitions | **First-order.** A nominally soft spring can become an extremely stiff support after binding. Engagement state matters more than an isolated spring-rate number. |
| **Camber** | Contact orientation under load, camber thrust, lateral capacity and tread load distribution | Body roll relative to the road, suspension travel, steering angle, load and corner duration | **First-order.** Predict dynamic camber relative to the road. Do not subtract the track’s banking angle from the garage camber. |
| **Caster and front toe** | Steering-dependent camber, jacking, steering response and tyre scrub | Steering angle/radius, time in corners versus straights, braking and surface disturbances | Usually **second-order**, but large toe or caster changes can become first-order through scrub, heating or coupled pose changes. |
| **Cold pressures** | Inflation state, tyre stiffness, loaded radius and contact/force characteristics | Wheel loads, stint duration, air/track temperatures, preparation state and cooling exposure | **First-order.** Cold pressure is a controllable initial condition; hot pressure is an outcome. Neither the ideal-gas equation nor a temperature spread identifies optimum pressure. |
| **Final drive** | Engine operating range and tractive force versus speed | Corner minimum speed, straight length, exit acceleration, draft/top-speed requirement | **First-order if wrong**, otherwise mostly constrained by permitted ratios. Do not infer that unanimous use of a ratio proves an unconstrained optimum. |
| **Brake bias** | Front/rear braking-force demand and remaining combined-slip capacity | Braking demand, load transfer, entry curvature and grip | **First-order at a braking short track; generally second-order at nearly flat-out intermediates.** Retain a stable reference until braking evidence supports a change. |
| **Tape/grille configuration** | Cooling airflow, drag and aerodynamic balance | Airspeed, ambient temperature, full-throttle duration, draft exposure and run length | **First-order through cooling feasibility**, and potentially pace. Qualifying and race packages require separate treatment. |
| **Dampers** | Rate and timing of load transfer, support engagement and wheel contact | Bump spectrum, transitions, braking/throttle rates | Usually **second-order in steady corners**, potentially first-order where the platform repeatedly engages/disengages or loses contact. |

These are the physical relationships the model must represent, not identified Supra response coefficients. The audit documents the missing quantitative mappings. [Physics audit][A]

One car-specific correction matters: the very large front **“Shock spring rate”** values are support springs on the damper, engaged after sufficient compression. Packers determine engagement. They must not be treated as continuously active conventional wheel springs. The official manual also identifies approximately **6 mm** centre-front splitter clearance as an aerodynamic threshold; that is a manual reference, not a verified optimum on every bump or build. [O’Reilly car manual][MAN]

# WHAT WE ALREADY KNOW

**Evidence boundary.** I read and analysed both setup JSON files, decoded both compressed G61 edge files, read the Vegas survey table, P4/P6 registrations and setup comparison, the findings register, and Coach’s tuning implementation. The supplied Kansas scratchpad path does not exist. Literal file reads worked, but directory searches failed with a sandbox initialization error, preventing a complete search for IRP session dumps. Consequently, **I cannot honestly supply authenticated IRP and Kansas default parameter vectors**. Purchased IRP R/Q values are not substituted for an iRacing default or reproduced below.

**Track geometry: banking alone is a poor setup predictor**

I calculated approximate horizontal radii from the midpoint of the G61 inside/outside edges, using local metre coordinates and three-point circles with points separated by **50 m** of recorded station. Results below cover **500–850 m** and **1,600–1,950 m**. These are **derived central-corner geometry**, not measured racing-line radii. Repeating the calculation with **25 m** and **75 m** spacing kept the Vegas medians near **232–233 m** and Charlotte near **197 m / 192–193 m**. [Vegas edges][VE] [Charlotte edges][CE]

| Track | Geometry and speed evidence |
|---|---|
| **Vegas** | G61 lap coordinate **2,414.01 m**. Derived corner radii: **232.3 m [231.7–233.2 m]** and **232.3 m [230.3–234.9 m]**, median [interquartile range]. Survey banking: **20.2° / 20.0°** in the corner cores. The sampled fast qualifying laps have corner minima around **79–81 m/s**. [Vegas edges][VE] [Survey][MAP] [Vegas telemetry][VT] |
| **Charlotte** | G61 lap coordinate **2,414.016 m**. Derived radii: **197.0 m [195.3–198.1 m]** and **191.6 m [190.6–194.6 m]**. The brief supplies **24°** nominal banking; the edge CSV contains no elevation with which to verify it. Sampled current-season qualifying minima are approximately **75–78 m/s**. [Charlotte edges][CE] [Charlotte telemetry][CT] |
| **Kansas** | The runbook describes approximately **235 m** turns and truck-derived banking of **17.6–18.4°** along the measured line. Those are line-specific measurements, not a substitute for a complete lane-dependent surface. A comparable speed/default-setup record was not recovered. [Track-map runbook][RUN] |
| **IRP** | The study establishes a short-track operating regime and strong entry/centre/exit differences. Its model distinguishes a reference-line length of **1,077.24 m** from a track-length value of **1,083.43 m**. Neither should silently replace the venue’s nominal lap length or another source’s station coordinate. [IRP track model][IRPM] |

**Inference:** Charlotte combines greater nominal banking with tighter corners and lower corner speeds. It is not simply “Vegas with more banking.”

For illustration, using the derived central radius and a sampled speed—not claiming those describe the same contact-point trajectory—the banked point-mass equations give:

| Illustrative corner condition | Road-plane lateral demand | Normal demand before aero |
|---|---:|---:|
| Vegas: **81.2 m/s**, **232.3 m**, **20.2°** | **23.25 m/s²** | **19.00 m/s²**, approximately **1.94 g** |
| Charlotte: **78.0 m/s**, **197.0 m**, assumed **24°** | **24.22 m/s²** | **21.52 m/s²**, approximately **2.19 g** |

**Derived, conditional calculations**, using the audit’s banked mechanics and **9.80665 m/s²** gravity. They illustrate why normal load and lateral demand must be considered together; they are not estimates of tyre grip. [Physics audit][A] [Vegas edges][VE] [Charlotte edges][CE] [Survey][MAP] [Vegas telemetry][VT] [Charlotte telemetry][CT]

**The verified default comparison**

The following is the current-season modal Vegas default and Charlotte’s `2026 S4` default. Four-corner vectors always mean **LF / RF / LR / RR**. Front pairs mean **LF / RF**; rear pairs mean **LR / RR**. All numbers in this table come from the two setup datasets, with Vegas’s published default summary cross-checked against the P6 default column. [Vegas setups][V] [Vegas default summary][VS] [Charlotte setups][C] [P6 setup comparison][BSG]

| Parameter | Vegas default | Charlotte default |
|---|---:|---:|
| Crossweight | **53.1%** | **59.4%** |
| Nose weight | **52.4%** | **51.9%** |
| Ballast forward | **1,397 mm** | **1,143 mm** |
| Cold pressures | **152 / 314 / 152 / 310 kPa** | **152 / 310 / 152 / 310 kPa** |
| Front shock springs | **1,138 / 1,138 N/mm** | **1,225 / 1,225 N/mm** |
| Front packers | **31.8 / 38.1 mm** | **34.9 / 38.1 mm** |
| Rear springs | **61 / 245 N/mm** | **96 / 228 N/mm** |
| Spring perches | **−101 / −113 / −87 / −107 mm** | **−83 / −121 / −164 / −112 mm** |
| Static ride heights | **120 / 128 / 154 / 162 mm** | **127 / 128 / 169 / 169 mm** |
| Corner weights | **4,024 / 4,444 / 4,131 / 3,547 N** | **3,475 / 4,898 / 4,700 / 3,073 N** |
| Front ARB diameter | **44 mm** | **44 mm** |
| ARB arm asymmetry | None | **2** |
| ARB slack; attachment | **1 mm; attached** | **0 mm; attached** |
| ARB preload, readout | **0.0 N·m** | **−54.5 N·m** |
| Track-bar heights | **159 / 279 mm** | **159 / 279 mm** |
| Bar mean / right-minus-left split, derived | **219 / 120 mm** | **219 / 120 mm** |
| Camber | **+4.7 / −4.9 / +2.0 / −2.0°** | **+4.6 / −4.6 / +1.7 / −1.9°** |
| Front caster | **+11.5 / +13.2°** | **+11.2 / +12.9°** |
| Toe-in | **−1 / −1 / +5 / −5 mm** | **−1 / 0 / +4 / −4 mm** |
| Truck-arm mounts | bottom / bottom | bottom / bottom |
| Truck-arm preload, readout | **−1.9 N·m** | **−24.0 N·m** |
| RR travel to coil bind | OFF | OFF |
| LS compression | **3 / 7 / 7 / 5 clicks** | **3 / 7 / 7 / 5 clicks** |
| HS compression | **7 / 9 / 6 / 6 clicks** | **3 / 5 / 6 / 6 clicks** |
| HS compression slope | **2 / 2 / 3 / 3 clicks** | **8 / 8 / 8 / 8 clicks** |
| LS rebound | **8 / 7 / 7 / 8 clicks** | **8 / 7 / 7 / 8 clicks** |
| HS rebound slope | **7 / 7 / 6 / 3 clicks** | **7 / 7 / 6 / 3 clicks** |
| Steering ratio / offset | **12:1 / +6°** | **12:1 / +6°** |
| Front brake bias | **65.0%** | **65.0%** |
| Final drive | **3.60:1** | **3.70:1** |
| Tape | Race | Race |

What this supports:

- **Observed:** Charlotte’s default has **6.3 percentage points more crossweight**, **0.5 points less nose weight**, stiffer front support springs, a stiffer LR spring, a softer RR spring and a higher rear platform. It retains the same nominal track-bar heights and ARB diameter. [V][V] [C][C]
- **Inference:** the relevant difference is a complete platform/load-distribution package. Equal bar diameter does not imply equal front roll response when asymmetry, preload, springs and engagement differ.
- **Not established:** a causal slope such as “add wedge per degree of banking.” There are too few independent tracks, and radius, speed, platform, surface and default-design choices change together.
- **Observed build effect:** Vegas previously used **1,778 mm** ballast and **28.6 / 34.9 mm** front packers; the newer default uses **1,397 mm** and **31.8 / 38.1 mm**. Old shared values subsequently failed tech when reconstructed in the newer car. Track geometry had not supplied the reason for that change. [Vegas default summary][VS] [F30][F]

**Shared fast setups: population and calculation**

The files contain **126 Vegas records** and **117 Charlotte records**. Of these, **55 Vegas** and **43 Charlotte** records link to sampled laps. Thus the brief’s Charlotte count describes the lap-linked subset, not the whole JSON. [Vegas setups][V] [Charlotte setups][C]

For a reproducible descriptive comparison, I:

- Used lap-linked records, excluded records flagged as default-name setups, and excluded NOAPS/Maconi-labelled packages.
- Separated Race and Qualifying **tape configurations**.
- Selected the faster half within each track/tape group, rounding upward, using each record’s fastest linked lap.
- Subtracted the default for that lap’s season, rather than trusting the season embedded in the setup filename.
- Weighted each setup record equally.

This leaves **7 Vegas records: 3 Race, 4 Qualifying**, and **13 Charlotte records: 9 Race, 4 Qualifying**. Membership is Vegas `S001,S002,S003,S007,S008,S010,S014`; Charlotte `S002,S003,S004,S006,S007,S008,S009,S010,S015,S017,S019,S020,S029`. [Calculated from V][V] [Calculated from C][C]

**These are fast-associated settings, not isolated setup gains.** Weather, fuel, driver, drafting and build differ. Several Charlotte records are variants of the same package. A fast lap on race tape does not establish long-run performance.

In the next tables, each entry is **median deviation [25th–75th percentile deviation]** from the season-matched default. Interpolated quartiles describe spread; they are not legal garage increments. All figures are calculations from the cohorts above. [Vegas setups][V] [Charlotte setups][C]

| Parameter; deviation unit | Vegas | Charlotte |
|---|---:|---:|
| Crossweight; percentage points | **+3.7 [+0.5, +6.5]** | **0 [−0.4, 0]** |
| Nose weight; percentage points | **−1.2 [−1.55, +0.25]** | **0 [−1.1, 0]** |
| Ballast forward; mm | **−508 [−647.5, +70]** | **−13 [−432, 0]** |
| LF pressure; kPa | **+20 [0, +41]** | **0 [0, +31]** |
| RF pressure; kPa | **−4 [−4, 0]** | **0 [0, 0]** |
| LR pressure; kPa | **+20 [0, +41]** | **0 [0, +27]** |
| RR pressure; kPa | **0 [0, 0]** | **0 [0, 0]** |
| Front ARB diameter; mm | **+13 [0, +20]** | **+20 [0, +20]** |
| ARB slack; mm | **0 [−0.5, +2]** | **0 [0, +1]** |
| ARB preload readout; N·m | **0 [−116.65, +6.4]** | **+0.3 [+0.1, +54.5]** |
| LF shock spring; N/mm | **0 [0, +437]** | **0 [−175, 0]** |
| RF shock spring; N/mm | **+437 [+43.5, +437]** | **0 [−350, +350]** |
| LF packer; mm | **−4.8 [−9.5, −1.6]** | **−6.3 [−17.4, −6.3]** |
| RF packer; mm | **−1.6 [−3.95, 0]** | **−3.2 [−9.5, 0]** |
| LR spring; N/mm | **+44 [+26.5, +61.5]** | **0 [−17, 0]** |
| RR spring; N/mm | **−79 [−170.5, −17]** | **−18 [−88, 0]** |
| LR track bar; mm | **+25 [+9.5, +41.5]** | **0 [0, +38]** |
| RR track bar; mm | **−38 [−79, −6]** | **−25 [−114, 0]** |
| Track-bar mean; mm, derived | **−6 [−25.25, +3.25]** | **−12.5 [−44.5, 0]** |
| Track-bar split; mm, derived | **−76 [−114, −40.5]** | **−25 [−127, −25]** |
| LF camber; degrees | **+1.4 [−0.05, +1.5]** | **+1.3 [+0.3, +1.3]** |
| RF camber; degrees | **−1.1 [−1.1, +0.05]** | **+0.1 [0, +0.6]** |
| LR camber; degrees | **0 [0, 0]** | **+0.3 [0, +0.3]** |
| RR camber; degrees | **0 [0, 0]** | **−0.1 [−0.1, 0]** |
| LF caster; degrees | **−0.1 [−0.4, +0.65]** | **−0.8 [−0.8, 0]** |
| RF caster; degrees | **−0.6 [−0.95, −0.15]** | **−0.2 [−1.1, 0]** |
| LF toe; mm | **0 [−3, 0]** | **0 [−5, 0]** |
| RF toe; mm | **−5 [−5, +0.5]** | **0 [−3, 0]** |
| LR toe; mm | **−1 [−2, −0.5]** | **0 [0, +1]** |
| RR toe; mm | **+1 [+0.5, +2.5]** | **0 [−1, 0]** |
| Truck-arm preload readout; N·m | **+4.7 [−3.8, +15.7]** | **+23.8 [+11.4, +26.1]** |
| Brake bias; percentage points | **0 [−3, 0]** | **0 [−4, 0]** |
| Steering offset; degrees | **+4 [+1.5, +7]** | **+2 [0, +6]** |
| Final-drive ratio | **0 [0, 0]** | **0 [0, 0]** |

The remaining numeric fields, in **LF / RF / LR / RR** order, use the same median [interquartile spread] convention. [Calculated from V][V] [Calculated from C][C]

| Parameter | Vegas deviations | Charlotte deviations |
|---|---|---|
| Perches, mm | **13 [4.5,27.5] / 0 [−1.5,15.5] / −55 [−66,−19.5] / 0 [−2.5,24]** | **13 [0,13] / 2 [0,8] / −14 [−14,0] / −2 [−9,0]** |
| Ride heights, mm | **0 [−1.5,1.5] / −8 [−9.5,−3.5] / 9 [7.5,16.5] / 6 [−1,9.5]** | **−10 [−10,0] / 0 [−13,0] / 0 [−3,2] / 7 [0,8]** |
| Corner weights, N | **−268 [−627.5,2] / 238 [73,451] / 274 [7,639.5] / −245 [−470,−82]** | **−49 [−50,0] / −4 [−123,1] / 56 [0,56] / 1 [−2,116]** |
| LS compression, clicks | **4 [0,7] / 0 [0,3] / 0 [0,3] / 0 [0,3]** | **7 [0,7] / 3 [0,3] / 3 [0,3] / 5 [0,5]** |
| HS compression, clicks | **1 [0,3] / 1 [0,1] / 1 [0,4] / 0 [0,2.5]** | **7 [7,7] / 5 [5,5] / 4 [4,4] / 4 [4,4]** |
| HS compression slope, clicks | **4 [2,8] / 4 [2,8] / 1 [0,7] / 2 [0,7]** | **2 [2,2] / 2 [2,2] / 2 [2,2] / 2 [2,2]** |
| LS rebound, clicks | **0 [0,2] / 0 [0,3] / 0 [0,3] / 0 [0,2]** | **2 [1,2] / 3 [2,3] / 3 [2,3] / 2 [1,2]** |
| HS rebound slope, clicks | **0 [0,3] / 0 [0,3] / 0 [0,4] / 1 [0,7]** | **3 [3,3] / 3 [3,3] / 4 [4,4] / 7 [7,7]** |

Categorical settings reveal distinct architectures:

- Vegas: ARB asymmetry **Max in 3/7**, setting **2 in 2/7**, and one each None/setting **5**. RR coil bind is OFF in **5/7**, with **2.5 in** and **2.75 in** in the others. Truck-arm mounts are bottom in **6/7** and top in **1/7**. [Vegas setups][V]
- Charlotte: asymmetry **2 in 8/13**, **5 in 3/13**, and one each **4/Max**. RR coil bind is OFF in **10/13**, with **2.4375 in** in the remaining **3/13**. All have bottom truck-arm mounts. [Charlotte setups][C]
- Every selected record keeps the front bar attached. Steering ratios vary substantially: Vegas **8:1–16:1**, Charlotte **10:1–14:1**. These are driver/control choices as well as setup descriptors. [V][V] [C][C]

**Race and qualifying must not be pooled when generating a setup.** Their absolute medians make the distinction clear:

| Quantity | Vegas Race | Vegas Qualifying | Charlotte Race | Charlotte Qualifying |
|---|---:|---:|---:|---:|
| Crossweight | **54.2%** | **59.75%** | **59.0%** | **64.9%** |
| Front ARB | **44 mm** | **64 mm** | **64 mm** | **64 mm** |
| LF / RF shock spring | **1,138 / 1,138 N/mm** | **1,575 / 1,575 N/mm** | **1,225 / 1,225 N/mm** | **875 / 1,575 N/mm** |
| LR / RR spring | **70 / 228 N/mm** | **122.5 / 74.5 N/mm** | **96 / 210 N/mm** | **96 / 52 N/mm** |
| LF / RF camber | **+4.4 / −4.8°** | **+6 / −6°** | **+5.9 / −4.0°** | **+6 / −6°** |

These are descriptive medians, including interpolated values; they are **not proposed setups**. The Charlotte qualifying sample is especially concentrated in one package family. [Calculated from V][V] [Calculated from C][C]

**What actually moves consistently**

- RR springs soften or remain unchanged in both samples: **6/7 Vegas** and **9/13 Charlotte** soften; none stiffen.
- LR track-bar height never decreases; RR height usually decreases. The conspicuous common tendency is **less track-bar rake**, not simply “lower both ends.”
- Charlotte front bars increase in **9/13** records; none decrease.
- Every selected Vegas record has less RF caster and greater LR static ride height than its matched default.
- Charlotte RF and RR cold pressures remain unchanged in **13/13** records.
- Final drive stays unchanged in both samples. [Calculated from V][V] [Calculated from C][C]

**Inference:** there are reusable *package families*: modest-bar/default-like race packages, and more aggressive bar/support/coil-bind packages. There is no support here for independently applying each column’s median adjustment.

**What the experiments add**

The Vegas tests provide local response evidence that the cross-track distributions cannot:

- P4’s more-upright front-camber **package** improved the bot’s race screen by **0.144 s/lap**, or **0.103 s/lap** after that block’s temperature adjustment. It also changed wedge and preloads, so this is not a camber-only coefficient. [F26][F]
- Raising the RR track bar **13 mm** slowed the bot by **0.053 s/lap** and increased rear crab. The earlier IRP-based directional forecast did not transfer. [F26–F27][F]
- In P6, the public G61 package averaged **33.791 s/lap** versus **33.973 s/lap** for the blind package, with only **two versus one** valid race runs. The public package had substantially less fade, but the block was incomplete and conditions differed. [F32][F]
- At a common **16 m/s²** lateral acceleration, that package needed approximately **0.155 rad** more steering-wheel angle and had approximately **0.011 rad** less rear crab. It was tighter yet faster for this controller. [F33–F35][F]
- Its RF carcass was hotter while rear wear/fade improved. Temperature alone therefore cannot be the objective function. [F34][F]

The IRP tests add **directional** evidence: pressure, front-bar, alignment and track-bar changes interacted with controller limits; combined changes did not reliably add their apparent individual benefits. Lowering one track-bar end also changed other setup state. These are useful warnings about coupling, not a transferable numerical IRP-default prior. [Physics audit][A] [IRP tuning notes][IRPN]

# THE BASELINE GENERATOR, v0

**v0 should generate a complete, traceable baseline worksheet by selecting a coherent measured package and making only supported adjustments.** Physics should calculate demand, reject unsuitable packages and explain choices. It should not invent missing suspension or tyre coefficients.

This is implementable using existing data, without new simulator experiments. It is new backend work: the present code does not already perform this calculation.

**Inputs**

| Input | Required treatment |
|---|---|
| Exact car and simulator build | Use `stockcars2 supra2019`; preserve build identity separately from season. A filename’s season is not proof of compatibility. |
| Complete default setup | Readable `CarSetup` or authenticated export, including controls and computed readouts. An opaque `.sto` supplies identity, not decoded values. |
| Garage schema | Distinguish controls, derived state, runtime readings, allowed categories, steps and constraints. The supplied `garage-layout.json` describes UI positions/probes; it is not a parameter-range database. [Garage layout][GL] |
| Track surface | Configuration identity, station system, lane-dependent curvature/banking, grade, vertical curvature, surface classes and boundaries, each with provenance and uncertainty. |
| Expected driving | Feasible line/speed envelope, braking/acceleration demand and driver stability preference. |
| Initial state | Fuel state, new/used tyres, reset versus pit fitting, temperatures, weather, wind and track state. Fuel is an input state; do not invent a garage starting-fuel control absent from this car. [F40][F] |
| Objective | Qualifying or race; race horizon and priority among sustained pace, tyre life and stability. |
| Evidence library | Current-build defaults, public whole packages, validated local interventions and compatible telemetry/endpoints. Purchased packages remain access-controlled and are excluded from published numeric priors. |

**Step A — Build an honest track-demand model**

Use the survey’s road surface when available. With G61 edges alone, derive plan-view curvature and width; obtain banking independently. **Latitude/longitude edges cannot identify banking, bumps or surface roughness.**

Keep painted line, G61 edge, wall and legal boundary distinct. Vegas’s surveyed outer wall differs from G61’s outer edge by a mean **0.42 m**; it is not simply a global coordinate shift. [F29][F]

For a simple banked segment:

\[
a_{y,\mathrm{road}}=\frac{v^2}{R}\cos\theta-g\sin\theta
\]

\[
a_{n,\mathrm{road}}=g\cos\theta+\frac{v^2}{R}\sin\theta
\]

Then add properly projected grade, vertical curvature and aerodynamic forces. Use one consistent coordinate convention throughout the speed planner and load calculation.

**Physics:** gravity and inertial demand.

**Prior:** available tyre capacity, aerodynamic coefficients and the driver’s feasible limit.

The existing banked-speed/flat-force-budget inconsistency must be corrected before using the planner to choose setup changes. Otherwise it can rank a setup against a fictitious load case. [Physics audit, contradictions][A]

Summarize the resulting exposure:

- Sustained and peak normal load.
- Road-plane lateral demand and demand/normal-load ratio.
- Time under combined lateral and longitudinal demand.
- Load-change rates through entry, banking transitions and exit.
- Airspeed exposure, straight duration and cooling opportunity.
- Bump/travel demands where genuinely measured.

**Step B — Recover the default’s physical state**

Use measured corner weights whenever available.

Let \(W\) be total static weight in newtons, \(n\) front fraction, \(l\) left fraction, and \(c\) crossweight fraction, defined as RF plus LR. Then:

\[
\begin{aligned}
W_{LF}&=\tfrac{W}{2}(n+l-c),&
W_{RF}&=\tfrac{W}{2}(n-l+c),\\
W_{LR}&=\tfrac{W}{2}(-n+l+c),&
W_{RR}&=\tfrac{W}{2}(2-n-l-c).
\end{aligned}
\]

**Physics:** static equilibrium.

**Important:** nose weight plus wedge alone does not determine all four loads. Total weight and left/right distribution are also needed. Nor do these equations tell us which perch movements will create the desired loads while preserving heights.

Store static alignment, heights, bar/preload state and support/coil-bind mode alongside those loads. Treat discrepancies between garage-entry and committed readouts as measurement-state issues, not automatically as new independent setups. Coach’s setup identity contract already records several derived fields separately. [Setup identity contract][KEYS]

**Step C — Select a whole package using physical similarity**

Rank candidates in this order:

1. Same car, compatible build, exact layout and intended use, with credible stint evidence.
2. Same target track and compatible build with weaker performance evidence.
3. A compatible-build donor with similar **load exposure and operating mode**.
4. The supplied default.

For donor similarity, use normal load, lateral demand, speed, braking, corner duration, transitions and surface—not just oval length or nominal banking.

Preserve package categories:

- Default-like support arrangement versus RR coil bind.
- Bar diameter **with** arms, slack and preload.
- Springs **with** packers/perches and static pose.
- Rear toe **with** track-bar/truck-arm geometry.
- Race cooling versus qualifying cooling.

Select an actual representative package—a medoid or authenticated source—not an imaginary car assembled from marginal medians.

**Prior:** package selection and the relative importance of the similarity features.

**Physics:** exposure features and incompatibility checks.

The present donor selector is a keyword classifier. This replaces its descriptive track-family matching with an explicit, inspectable load comparison. [Tuning implementation][TE]

**Step D — Make only supportable adaptations**

| System | v0 rule |
|---|---|
| **Pressures** | Retain the selected package’s preparation-specific values unless comparable data supports a correction. Quantize with F36/F37. Do not automatically minimize pressure or chase a universal hot target. |
| **Platform** | Preserve the spring/packer/bar architecture. Use observed dynamic clearances to reject packages unsuited to target loads. Without a verified setup-to-height response, retain the reference rather than calculate fictional packer millimetres. |
| **Wedge/nose** | Use current-build package priors. Solve perch corrections only where existing garage observations identify a local mapping and its constraints. |
| **Roll balance** | Use measured response or package priors. Do not equate garage spring rates directly to wheel rates. |
| **Track bars/rear steer** | Treat mean height, rake and toe separately. A validated equal-end intervention cannot authorize a one-end change. |
| **Camber/caster/toe** | Preserve a successful dynamic-alignment family. Adapt only with identified travel/steer response; banking is not a direct camber subtraction. |
| **Gearing** | Use permitted ratios and the expected RPM envelope. Where the ratio is effectively constrained, retain it. |
| **Brake bias** | Start from the stable reference, then adapt only to supported braking demand or driver preference within verified limits. |
| **Tape** | Match run duration and cooling evidence. A short qualifying observation does not establish race cooling feasibility. |
| **Dampers** | Retain the package unless a measured transient or bump problem identifies the need to change it. |

The eventual roll calculation has a clear physical structure:

\[
K_{\phi,\mathrm{springs}}=\sum_i k_{\mathrm{wheel},i}y_i^2,
\qquad
k_{\mathrm{wheel}}=k_{\mathrm{spring}}r^2
\]

where \(r\) is spring travel divided by wheel travel. The elastic roll moment is divided by front/rear roll stiffness; geometric and unsprung load transfer are added separately.

**The structure is physics; the Supra motion ratios, engagement curves, roll-centre locations and effective bar stiffness are not presently identified.** v0 must not fill them with guessed constants and call the result measured load transfer. [Physics audit][A]

**Step E — Handle pressure and temperature as state predictions**

The verified cold-pressure mapping is:

\[
P_{\mathrm{cold,kPa}}
\approx 6.8911603\,P_{\mathrm{garage,psi}},
\qquad
P_{\mathrm{garage,psi}}\in0.5\mathbb Z
\]

The reported conversion uncertainty is **±0.0000007 kPa/psi** in the tested domain. Pit requests resolve to the nearest half-psi lattice value. The ideal nearest-lattice rounding bound is therefore approximately **1.72 kPa**, before range clipping or request-format effects. That last number is a calculation, not measured hot-pressure uncertainty. [F36–F39][F]

**F36/F37 predicts what gets fitted. It does not predict pressure growth.**

For hot pressure, a candidate physical structure is:

\[
P_{g}(t)+P_{\mathrm{atm}}
=
\big(P_{g,0}+P_{\mathrm{atm}}\big)
\frac{T_{\mathrm{gas}}(t)}{T_{\mathrm{fit}}}
\frac{V_0}{V(t)}
\]

with absolute temperature. Gas temperature and volume remain unobserved; carcass temperature cannot simply replace gas temperature. [R2–R4 and corrections][R]

Therefore v0 uses:

- **Comparable existing traces:** per-tyre pressure rise and surface-temperature evolution, conditioned on preparation, weather, workload and package identity.
- **Actual service endpoints:** carcass temperatures and tread remaining.
- **Constrained interpolation within observed support:** a monotone heat-soak component where appropriate, plus transient surface response.
- **Unavailable outside support:** no numerical tyre forecast when suitable evidence does not exist.

Maintain distinct outputs for surface, carcass and gas state. The last is latent unless independently supported. Do not turn a previous pit readout into a continuous carcass trace. [F28][F]

**Step F — Return a complete result, including uncertainty**

The backend output should contain:

```text
identity
  exact car / build / track / objective / source package

full_setup
  every applicable control with value, unit and provenance
  unchanged controls explicitly retained
  derived readout targets separately identified

application
  ordered garage worksheet
  verified increments and constraints
  manual tech check and rollback source

predictions
  speed/load envelope
  entry/centre/exit balance by run stage
  per-tyre surface-temperature and pressure trajectories, when supported
  carcass/wear endpoints, when supported
  uncertainty method, applicability and missing quantities

validation
  expected actual setup
  eligible recording conditions
  measurements that confirm or contradict the forecast
```

An unavailable prediction is a supported backend result. A fabricated temperature with a wide interval is not.

**A concrete complete package v0 can reproduce**

The public Vegas `S007` package is a useful worked example of source-based generation. This is its published package, **not a claim that it is the human optimum or automatically legal on a later build**. All following values are from that record. Four-corner vectors use LF/RF/LR/RR order. [Vegas S007][V]

| Controls | Complete source values |
|---|---|
| Cold pressure | **152 / 314 / 152 / 317 kPa** |
| Ballast; brake bias; tape | **1,702 mm; 59.0%; Race** |
| Steering ratio; offset | **12:1; +9°** |
| Front ARB | **43 mm; asymmetry 2; slack 2 mm; attached** |
| Perches | **−101 / −113 / −87 / −107 mm** |
| Front shock springs; packers | **1,138 / 1,138 N/mm; 28.6 / 34.9 mm** |
| Rear springs; RR coil-bind setting | **70 / 228 N/mm; OFF** |
| Camber | **+4.4 / −4.5 / +1.9 / −1.9°** |
| Front caster | **+10.9 / +12.6°** |
| Toe-in | **−1 / −1 / +1 / −1 mm** |
| Rear track bars; truck-arm mounts | **191 / 235 mm; bottom / bottom** |
| LS compression | **3 / 7 / 7 / 5 clicks** |
| HS compression | **7 / 9 / 6 / 6 clicks** |
| HS compression slope | **2 / 2 / 3 / 3 clicks** |
| LS rebound | **8 / 7 / 7 / 8 clicks** |
| HS rebound slope | **7 / 7 / 6 / 3 clicks** |
| Final drive | **3.60:1** |

Its source readouts are nose **53.2%**, cross **54.2%**, ride heights **123 / 120 / 163 / 160 mm**, ARB preload **0.0 N·m** and truck-arm preload **16.7 N·m**. These are checks on the reproduced package, not additional independent knobs. [Vegas S007][V]

The P6 reconstruction retained a different steering offset and had small alignment/preload readout differences. It must retain its own identity rather than being silently treated as byte-for-byte identical to S007. [P6 registration][P6]

For this example, v0’s defensible balance expectation is **more rear security and greater steering demand than the blind package in the tested regime**. The historical RF hot-pressure endpoint of **408 kPa** and RF carcass ranges of approximately **130–131 / 127–128 / 120–121°C** are useful comparison observations, **not forecasts for Joshua’s next run**. [F33–F34][F]

**Honest error bars**

| Output | What v0 can honestly claim |
|---|---|
| Source settings | Exact displayed source values, subject to display rounding and identified readout-state differences. |
| Track geometry | Sensitivity to lane and smoothing; disagreement between survey and edge definitions. This is not automatically a statistical confidence interval. |
| Static loads | Readout precision and propagated input uncertainty. New-perch predictions need a separately validated garage-response model. |
| Balance | Directional prior or a local measured response interval. Do not transfer the Vegas bot’s steering differences directly to a human at higher speed. |
| Hot pressure | Empirical prediction interval only after whole-run validation. Existing pressure extrapolation missed long-run RF pressure by about **4.6 kPa**; that is a demonstrated miss, not a universal error bar. [Audit][A] |
| Surface temperature | The reported IRP RF fit has residual SD around **6.1°C** within its fitted domain. It does not justify a narrow new-track interval. [R5][R] |
| Tyre life and lap gain | Currently no defensible general new-track numerical guarantee. Display “not calibrated” until the validation gate passes. |

# WHAT WOULD MAKE IT NEAR-ZERO ITERATION

**Identify the car once across representative operating regimes, then reuse that model at new tracks.** Near-zero iteration at the destination is plausible; zero calibration anywhere is not.

The most valuable next work is not a larger random setup search. It is identifying the small number of couplings that prevent reliable transfer.

| Order of work | Existing data first | Cheapest additional identification and expected signature | Audit items |
|---|---|---|---|
| **Measurement and provenance** | Reconcile recorded setup, build, preparation, fuel, telemetry frames and pit readouts. Recompute setup-specific crab references. Mark controller saturation/interventions. | Only unresolved signs/frames need short low-speed checks. A frame error produces systematic offsets/sign changes; instability produces growing residual yaw/slip beyond the normal crab relation. | **4**, plus **10** |
| **Garage controls → static state** | Reconstruct all before/after garage vectors, including seemingly incidental height, toe, crossweight and preload changes. Distinguish load snapshots from committed state. | Stationary positive/negative garage-step perturbations, restoring the entire baseline. Identify a local Jacobian from controls to corner weights, heights and alignment. Non-reversibility reveals coupling, quantization or mode changes. | **2** |
| **Support engagement and aero platform** | Use dynamic ride-height/shock traces with speed, banking and normal load. Separate free suspension, support engagement and coil bind. | Matched steady-speed runs with small packer/support changes. Engagement gives a kink in travel versus load. Aero effects grow with airspeed and change with pitch/height; purely mechanical changes remain at low speed. | **2, 5** |
| **Tyre force and load sensitivity** | Reanalyse human and bot force-demand sweeps separately, retaining failed/limited cases and controller flags. Use wheel contact kinematics where channels permit. | Progressive steady-corner and brake/drive sweeps over differing normal loads. Constant friction predicts unchanged force/load ratio; load sensitivity changes that ratio. Combined-slip capacity contracts as drive/braking demand rises. | **1**, supported by **4** |
| **Independent rear-steer and balance response** | Reuse P4/P6 matched-lateral comparisons; retain the registered but unrun hybrid hypotheses as predictions. | Separate rear toe, equal bar-height movement and bar rake, restoring static pose/preload. Toe should shift thrust/crab response; height/rake should change response with suspension pose and load. The effects need not have the same sign at all operating points. | **2, 4** |
| **Pressure versus thermal grip** | Match existing observations by preparation, pressure, surface state, tyre age and workload. Do not use interpolated wear as an independent measurement. | Verified same-set low/high/low workload, followed by matched-state pressure interventions. Fast recovery suggests surface heat; slower hysteresis suggests bulk/gas state; persistent loss suggests wear or another irreversible state. | **3**, then **6** |
| **Cooling, power and drag** | Refit straight and corner samples with RPM, gear, airspeed, grade, tape and engine state explicit. | Matched pull/coast runs and tape comparisons. Power, aerodynamic drag, engine braking and corner scrub have different speed/gear/load signatures. Measure cooling over the intended exposure. | **5** |
| **Stint evolution** | Reconcile real tyre-service endpoints with the entire exposure that produced them. Search genuine same-tyre refuelling events. | Compare smooth versus peaky workloads at similar total exposure; compare a same-tyre refill with a matched no-refill stop. An immediate post-refill response separates mass from continuous ageing. | **6, 7** |
| **Track-state transfer** | Cross-register surveys and edges; test geometry smoothing; analyse weather/rubber residuals by session. | Repeat only unresolved surface areas. Rubber tests require matched actual weather/state before their results can isolate rubber. | **8, 9** |

The audit’s pressure-float problem, **item 11**, is low priority for baseline quality. The operational lattice is already adequate. Better identification of fitting temperature matters more than reproducing every float bit. [Physics audit, ranked unclear list][A]

**Which uncertainties actually block the promise?**

- **Block a genuinely predictive new-track optimum:** tyre load/combined-slip response (**1**), setup-to-state mapping (**2**), and mechanical/aerodynamic platform response (**5**).
- **Block a defensible race-stint optimum:** thermal/pressure/grip separation (**3**) and wear/thermal evolution (**6**).
- **Block trustworthy interpretation:** measurement frames/controller observability (**4**) and enforced uncertainty validation (**10**).
- **Can initially be bounded:** fuel effects (**7**), incomplete road geometry (**8**) and track-state effects (**9**), provided the app narrows its operating domain.
- **Do not block a useful baseline worksheet:** recovering the exact pressure floating-point expression (**11**), a complete global tyre model, or every low-impact damper coefficient. [Audit][A]

The upgrade can be a **piecewise physical model with measured local response maps**. It does not need to reproduce every hidden simulator equation. But it must know when support engagement, build, tyre state or operating speed moves it into another regime.

**How to prove that the work reduced iteration**

Freeze the model and predict withheld **tracks, complete setups and complete sessions**. Compare against:

- The exact-track iRacing default.
- The best eligible source-selection baseline.
- The new physical generator.

Measure intended-stint performance, stability, tyre endpoints, prediction coverage and how much garage correction was needed after the first recording. Random lap splits would leak the same setup/driver/session into both training and validation.

Coach already has a gate requiring at least **8 held-out events**, more than **5%** improvement over honest baselines, and interval-calibration error within **10 percentage points**. The audit found that the predictor path bypassed that gate. The baseline generator should enforce it at the output boundary and compare identical held-out event identities. [Model gate][GATE] [Physics audit][A]

Small setup gains also need appropriate precision. The audit cites approximately **19 runs per arm** for one historical **0.05 s/lap** target. A short alternating block can screen large physical responses; it does not automatically prove a small pace advantage. [Physics audit][A]

# HOW IT SHOWS UP IN COACH

**One “Baseline” screen that answers: “What should I load, why does it fit, and what should the next recording prove?”**

This is one race-winning capability, not a new setup browser or dashboard collection. It follows Joshua’s product rules: visible context, no accordions, backend-owned evidence and a decision whose benefit can be checked later. [Coach contributor instructions][AG]

The screen has five visible areas:

| Area | What Joshua sees | What he does |
|---|---|---|
| **Event and intent** | Car, exact layout, build compatibility, race/qualifying intent and intended run length | Confirms the event and chooses the objective |
| **Recommended baseline** | One package, source, compatibility status and a concise explanation of its main tradeoff | Uses the existing source or follows the complete garage worksheet |
| **Track and expected behaviour** | Map with load zones; predicted entry/centre/exit tendency and the specific places likely to challenge the platform or rear traction | Selects a location only if he wants to report a problem |
| **Next-run checks** | The few observations that would validate or contradict the package: actual setup, platform, balance, tyre state and clean-run exposure | Drives the planned recording |
| **Verdict and next action** | Keep, adjust, or inconclusive; one supported next change when needed | Keeps the baseline or makes the named correction |

No fabricated projected lap gain. No tyre thermometer coloured “optimal” without a supported optimum. No map labelled with official turn numbers unless the annotation source supports them.

Use explicit evidence labels:

- **Recorded** — source setup/readout or actual telemetry.
- **Derived** — geometry, load calculation or screened measurement.
- **Predicted** — forecast with an applicability domain and validated interval.
- **Prior** — package tendency or qualitative rule.
- **Driver report** — Joshua’s observation.
- **Unavailable** — insufficient backend support.

Source identifiers and hashes remain subordinate technical information. The racing explanation should say, for example:

> “This package has better rear security in the comparable Vegas tests. Expect more steering through the centre. Check that it lets you pick up throttle without increasing rear corrections.”

The interface can show the complete worksheet as a continuous table on the same screen, with critical context kept visible. It does not need accordion sections or a separate “setup intelligence” page.

**Where it joins the current backend**

The existing implementation provides useful foundations:

- `starting_tune.py` distinguishes source purpose, readable parameters and actual loadable artifacts.
- `tuning_engine.py` validates exact recorded context and builds deterministic candidates.
- The installed rules cover brake bias, crossweight, paired track-bar movement and paired front packers.
- The rule catalog explicitly has unverified numeric ranges, and current candidate generation sets `proposed_values` to `null`. Presence of a setting is not verification of its range or step. [Starting Tune code][ST] [Tuning code][TE] [Rule catalog][RULE]

Add a separate deterministic **baseline-generation contract** that accepts a verified default/source and track model **before a driving recording exists**. Do not weaken Progressive Tuning’s requirement for recorded evidence to force a zero-history request through it.

The generator owns source selection, calculations, full setup, predictions and validation plan. The server/UI display that result. Optional AI may explain or select among supplied candidates; it must not invent garage values, legality or confidence. [Workflow requirements][WF] [Evidence contract][EC]

The package remains a worksheet/source artifact. Applying and saving the setup stays in iRacing; Coach’s existing contract prohibits generating or overwriting a simulator-loadable `.sto`. [Workflow requirements][WF]

**Checking the next recording**

The backend should automatically:

1. **Verify what was actually driven.** Compare embedded `CarSetup` with the expected complete control vector. Keep derived readouts separate. A mismatch invalidates the planned setup comparison.
2. **Verify applicability.** Match car/build/layout and screen tyre preparation, fuel, weather, traffic, repairs, resets and usable exposure.
3. **Compare like operating points.** Check steering and crab at common speed/lateral demand within observed support. Do not extrapolate the tighter setup to a load it never reached.
4. **Compare predicted trajectories and endpoints.** Pressure and surface temperatures use time-aligned traces; carcass/wear use correctly associated service endpoints.
5. **Issue one verdict.** Prediction supported, contradicted, or unassessable. Preserve Joshua’s subjective rating separately.
6. **Choose the next correction by the failed prediction.** Platform failure first; otherwise the supported balance/thermal issue. Save the result against the package and suppress equivalent failed recommendations.

The existing representative-run rule requires **6 clean green laps**, with at least **2 in each run third**. That supports the current comparison workflow; it does not establish late-stint performance when the intended race run is much longer. Validation must cover the horizon being claimed. [Evidence contract][EC]

# SUMMARY

1. A good baseline targets dynamic tyre loads, platform stability and usable stint balance.
2. Geometry supplies load demand; it does not identify the car’s setup response.
3. Vegas and Charlotte show coherent package differences, not a banking-to-knob formula.
4. Keep build, race/qualifying intent and suspension operating mode separate.
5. Generate from whole measured packages; never assemble independent median settings.
6. F36/F37 solves cold-pressure delivery, not optimum pressure or hot-pressure growth.
7. v0 can produce a complete worksheet; unsupported thermal and pace forecasts stay unavailable.
8. Static garage mapping, platform identification and tyre load sensitivity offer the biggest upgrades.
9. Validate on withheld tracks and sessions, then check the prediction against Joshua’s next recording.
10. Near-zero destination iteration means amortizing car identification—not pretending calibration is unnecessary.

[A]: <C:/Users/joshu/Documents/Codex/coach-requirements-2026-10-08/PHYSICS-AUDIT-codex.md>
[V]: <C:/Users/joshu/Documents/Codex/vegas-g61-sealed-2026-09-28/setups.json>
[VS]: <C:/Users/joshu/Documents/Codex/vegas-g61-sealed-2026-09-28/setups.md>
[C]: <C:/Users/joshu/Documents/Codex/charlotte-g61-2026-10-07/setups.json>
[VE]: <C:/Users/joshu/Documents/Codex/vegas-g61-sealed-2026-09-28/telemetry/track_edges.csv.gz>
[CE]: <C:/Users/joshu/Documents/Codex/charlotte-g61-2026-10-07/telemetry/track_edges.csv.gz>
[VT]: <C:/Users/joshu/Documents/Codex/vegas-g61-sealed-2026-09-28/TELEMETRY.md>
[CT]: <C:/Users/joshu/Documents/Codex/charlotte-g61-2026-10-07/TELEMETRY.md>
[MAP]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/p2-map/vegas-table.md>
[RUN]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/RUNBOOK-TRACKMAP.md>
[IRPM]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/model/irp_track.json>
[IRPN]: <C:/Users/joshu/source/repos/iRacing-utility/testdriver/TUNING-NOTES.md>
[F]: <C:/Users/joshu/source/repos/iRacing-utility/lab-notes/irp-bot-study/FINDINGS-REGISTER.md>
[R]: <C:/Users/joshu/source/repos/iRacing-utility/lab-notes/irp-bot-study/data/relationships/FINDINGS.md>
[BSG]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/p6/setups-BSG.md>
[P6]: <C:/Users/joshu/Documents/Codex/irp-bot-study-2026-09-26/vegas/registrations/REG-LV-P6.md>
[GL]: <C:/Users/joshu/source/repos/iRacing-utility/setups/garage-layout.json>
[AG]: <C:/Users/joshu/source/repos/iRacing Coach/AGENTS.md>
[WF]: <C:/Users/joshu/source/repos/iRacing Coach/docs/02-workflows/setups-and-progressive-tuning.md>
[EC]: <C:/Users/joshu/source/repos/iRacing Coach/docs/04-architecture/tuning-evidence-v2.md>
[ST]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/starting_tune.py>
[TE]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/tuning_engine.py>
[RULE]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/config/tuning-rules/nascar-oreilly-xfinity-2026s3-v1.json>
[KEYS]: <C:/Users/joshu/source/repos/iRacing Coach/docs/08-rewrite/contracts/setup-keys.json>
[GATE]: <C:/Users/joshu/source/repos/iRacing Coach/iracing-coach/skills/analyze-iracing-race/scripts/tire_model_gate.py>
[MAN]: https://s100.iracing.com/wp-content/uploads/2026/03/UM-NASCAR-Oreilly_Manual_V1.pdf