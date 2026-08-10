# Technical Data signal coverage — 0.15.0 development

Status: current development source, not the stable packaged `0.14.2` release.

This audit covers every signal exposed by `AnalysisTraceLayouts.Signals` and the non-trace evidence used by Technical Data. “Primary” means the signal can support a driver-facing finding when its prerequisites are present. “Context” means it qualifies or visualizes a finding but is not independently causal. “Diagnostic” means it remains available in Telemetry or a drill-in, while Technical Data deliberately avoids turning it into noisy or unsupported advice.

Technical Data never treats channel presence as proof of a cause. A missing channel suppresses its finding. A zero value is displayed only when zero is a known observation, not as a missing-data placeholder.

## Driver controls, pace, and motion

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `speed` | Normalizes wheel-speed divergence for lock/spin summaries and supports pace drill-ins. | Primary/supporting | Speed alone does not explain lap-time loss. |
| `delta` | Visual comparison aid for clean laps and reference laps. | Context | It is derived against a selected reference and is not an independent coaching conclusion. |
| `throttle` | Gates rear-wheelspin detection and remains available for trace comparison. | Primary/supporting | No “too early/late” verdict is made without corner and comparison context. |
| `brake` | Builds braking-work and wheel-lock summaries. | Primary | Brake pressure alone does not establish front-tire abuse. |
| `tire-wear` | Visual calculated-wear trace only. | Diagnostic | Technical Data uses confirmed pit-service O/M/I readings and the local model instead, avoiding circular claims from the calculated trace. |
| `gear` | Trace and drivetrain diagnostic. | Diagnostic | Gear choice needs corner, transmission, rev-limit, and reference context; no generic primary verdict. |
| `rpm` | Trace and drivetrain diagnostic. | Diagnostic | RPM alone does not establish a missed shift or lost time. |
| `steering` | Builds early/late steering-work change and supports tire-management review. | Primary | More steering work is presented as a review target, not proof of scrub or understeer. |
| `slip` | Trace-level handling comparison. | Diagnostic | Slip angle needs track phase, speed, and a suitable reference before it can be coached. |
| `yaw` | Contributes to condition/load matching in the local tire model and trace review. | Context | Yaw rate is not labeled oversteer/understeer by itself. |
| `lateral-g` | Trace-level cornering and line comparison. | Context | Banking, radius, and speed confound a standalone G conclusion. |
| `longitudinal-g` | Trace-level braking/acceleration comparison. | Context | It supplements pedal traces but is not an independent fault label. |
| `vertical-g` | Bump/platform diagnostic. | Diagnostic | No causal setup conclusion without track-location and suspension correlation. |
| `clutch` | Trace-level drivetrain diagnostic. | Diagnostic | Relevant mainly to launch/shift cases; omitted from the default race summary. |
| `abs-active` | Adds confirmed ABS-active time to tire/braking findings when nonzero. | Primary | Shown only when the channel exists; zero and missing are distinct. |
| `abs-cut` | Available for ABS intervention detail and highlighted brake traces. | Context | Intervention percentage is not converted to a braking-quality grade by itself. |
| `brake-bias` | Available beside braking traces. | Context | A setting value cannot prove the correct bias without balance and lock evidence. |
| `steering-torque` | Steering-effort diagnostic. | Diagnostic | Wheelbase settings and force-feedback configuration prevent a universal primary threshold. |
| `pitch` | Platform attitude trace. | Diagnostic | Not converted to setup advice without track-normalized suspension evidence. |
| `roll` | Platform attitude trace. | Diagnostic | Same guard as pitch; banking is a major confounder. |
| `pitch-rate` | Transient platform diagnostic. | Diagnostic | Useful for targeted review, not a stable race-level score. |
| `roll-rate` | Transient platform diagnostic. | Diagnostic | Useful for targeted review, not a stable race-level score. |

## Wheels and tires

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `lf-wheel-speed` | Contributes to front-lock and per-wheel divergence summaries. | Primary | Compared with vehicle speed; raw wheel speed is not judged alone. |
| `rf-wheel-speed` | Contributes to front-lock and per-wheel divergence summaries. | Primary | Same guard as LF. |
| `lr-wheel-speed` | Contributes to rear-wheelspin summaries. | Primary | Requires throttle and vehicle-speed gates. |
| `rr-wheel-speed` | Contributes to rear-wheelspin summaries. | Primary | Requires throttle and vehicle-speed gates. |
| `lf-wheel-slip` | Direct trace of normalized LF divergence. | Context | Technical Data uses bounded event duration, not an isolated spike. |
| `rf-wheel-slip` | Direct trace of normalized RF divergence. | Context | Technical Data uses bounded event duration, not an isolated spike. |
| `lr-wheel-slip` | Direct trace of normalized LR divergence. | Context | Technical Data uses bounded event duration, not an isolated spike. |
| `rr-wheel-slip` | Direct trace of normalized RR divergence. | Context | Technical Data uses bounded event duration, not an isolated spike. |
| `lf-pressure` | Shown in the selected pit-stop tire card. | Context | Pressure does not prove wear or an optimal setup value. |
| `rf-pressure` | Shown in the selected pit-stop tire card. | Context | Same guard as LF. |
| `lr-pressure` | Shown in the selected pit-stop tire card. | Context | Same guard as LF. |
| `rr-pressure` | Shown in the selected pit-stop tire card. | Context | Same guard as LF. |
| `lf-carcass-temp` | O/M/I carcass average in the tire drill-in when available. | Context | Temperature is not substituted for a missing wear reading. |
| `rf-carcass-temp` | O/M/I carcass average in the tire drill-in when available. | Context | Same guard as LF. |
| `lr-carcass-temp` | O/M/I carcass average in the tire drill-in when available. | Context | Same guard as LF. |
| `rr-carcass-temp` | O/M/I carcass average in the tire drill-in when available. | Context | Same guard as LF. |
| `lf-surface-temp` | O/M/I surface average in the tire drill-in when available. | Context | Surface heat is transient and does not establish wear. |
| `rf-surface-temp` | O/M/I surface average in the tire drill-in when available. | Context | Same guard as LF. |
| `lr-surface-temp` | O/M/I surface average in the tire drill-in when available. | Context | Same guard as LF. |
| `rr-surface-temp` | O/M/I surface average in the tire drill-in when available. | Context | Same guard as LF. |

Confirmed O/M/I wear is not a continuously updated trace source. It is taken from fresh pit-service readings, assigned to the preceding run, and kept distinct from temperature and pressure. Technical Data exposes most-worn band, front/rear and left/right wear balance, early-to-late pace, steering/braking load, wheel events, and condition-matched local tire-life/capability estimates when each item is supported.

## Fuel

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `fuel-level` | Green/caution burn, run consumption, range, stop margin, finish reserve, and confirmed fuel-add checks. | Primary | Positive level changes are associated with pit service; unexplained resets are not silently treated as fuel added. |
| `fuel-use-rate` | High-frequency consumption trace. | Diagnostic | The race summary favors level change over complete laps because instantaneous mass-flow is noisy and uses a different unit. |

Fuel findings include green and caution burn, all-green and observed-mix range, minimum scheduled stops, finish or post-stop margin, run-to-run burn spread, total used, and confirmed fuel added. The observed-mix range is retrospective; it is never described as a forecast of future cautions.

## Chassis and suspension

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `center-front-ride-height` | Targeted platform trace. | Diagnostic | No race-level causal finding without track-location normalization and setup context. |
| `lf-ride-height` | Targeted platform trace. | Diagnostic | Same guard; banking and bumps confound raw height. |
| `rf-ride-height` | Targeted platform trace. | Diagnostic | Same guard. |
| `lr-ride-height` | Targeted platform trace. | Diagnostic | Same guard. |
| `rr-ride-height` | Targeted platform trace. | Diagnostic | Same guard. |
| `lf-shock-deflection` | Targeted suspension trace. | Diagnostic | Deflection correlation does not uniquely identify a setup cause. |
| `rf-shock-deflection` | Targeted suspension trace. | Diagnostic | Same guard. |
| `lr-shock-deflection` | Targeted suspension trace. | Diagnostic | Same guard. |
| `rr-shock-deflection` | Targeted suspension trace. | Diagnostic | Same guard. |
| `lf-shock-velocity` | Targeted suspension trace. | Diagnostic | Requires corner/bump segmentation and a comparison lap. |
| `rf-shock-velocity` | Targeted suspension trace. | Diagnostic | Same guard. |
| `lr-shock-velocity` | Targeted suspension trace. | Diagnostic | Same guard. |
| `rr-shock-velocity` | Targeted suspension trace. | Diagnostic | Same guard. |

These signals remain first-class trace choices because they are valuable during a specific investigation. They are deliberately absent from the primary Technical Data overview until a track-normalized, comparison-based rule can support a useful conclusion.

## Conditions

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `track-temperature` | Matching feature for local tire wear and capability pace. | Context/model | Never compared across races without exact car/track/setup/compound scope. |
| `air-temperature` | Matching feature for the local tire model and race context. | Context/model | Not a standalone performance verdict. |
| `wind-speed` | Trace/context for lap comparability. | Context | Direction and exposure are needed before attributing pace. |
| `humidity` | Session context and future model feature. | Context | No primary relationship is claimed without supported history. |
| `fog` | Session context. | Diagnostic | Does not currently drive a defensible race-level finding. |
| `precipitation` | Wet-condition context. | Context | Dry and wet comparisons are not pooled silently. |
| `air-pressure` | Session context and future model feature. | Context | No standalone performance threshold. |
| `air-density` | Session context and future model feature. | Context | No standalone causal conclusion. |
| `track-wetness` | Separates wet/dry context for traces and model matching. | Context/model | Missing wetness cannot be assumed dry. |
| `track-usage` | Matching feature for local tire wear and capability pace when available. | Context/model | Numeric state is not translated into grip without supporting observations. |
| `weather-wet` | Boolean wet-session guard. | Context | Used for gating/comparability, not as a performance grade. |

## Race state and traffic

| Signal ID | Current Technical Data use | Class | Guard or defensible exclusion |
|---|---|---|---|
| `overall-position` | Start/finish net and restart/early/middle/late position phases. | Primary | Phase movement is association, not proof of passing skill or fault. |
| `class-position` | Class-scoped trace and fallback context. | Context | Overall and class position are not mixed; current overview prioritizes the applicable player position contract. |
| `distance-ahead` | Traffic-gap trace for targeted lap review. | Context | Opponent identity and multi-car interactions are incomplete, so it is not used as a causal primary metric. |
| `distance-behind` | Traffic-gap trace for targeted lap review. | Context | Same guard as distance ahead. |
| `on-pit-road` | Pit-run grouping, pit timeline, and service association. | Primary | Pit-road transit, stall/service, repair, and penalty time remain distinct. |
| `track-surface` | Validity/context for on-track, pit, off-track, and incomplete samples. | Primary/supporting | Surface state alone does not identify fault or damage. |

## Non-trace evidence used by Technical Data

| Evidence | Use | Strict limit |
|---|---|---|
| Lap timing, completion, flags, and pit time | Clean pace, consistency, green/caution exposure, restarts, and phase grouping. | Pit, caution, incomplete, and ineligible laps are not treated as clean pace. |
| Pit service state | Service window, confirmed tire corners, fuel added, repairs, and penalty service. | Requested service is not completion proof. |
| Confirmed O/M/I wear snapshots | Most wear, corner/band balance, and local tire-model observations. | A reading belongs to the preceding run and may be unavailable after the final run. |
| Incident-count changes | Race-execution context. | Points do not identify contact type, fault, damaged component, or pace cost. |
| Repair/tow timers and fast-repair counters | Separate exception context and completed repair time. | Overlapping clocks are not added as independent losses; zero does not prove an undamaged car. |
| Local tire history | Condition/load-matched wear life, pace cost, and capability pace. | Requires exact model context and enough eligible sessions; old observations are retained, not rewritten. |
| Garage61 references | Optional external lap comparison in the racecraft drill-in. | Only accessible telemetry with adequate alignment is used; absence does not manufacture a benchmark. |
| Race tire-service rules | Intended guard for two-vs-four legality and mandatory calls. | Not currently present in the analysis contract, so legality and mandatory-service conclusions are explicitly withheld. |
| Historical two-vs-four strategy outcomes | Intended context for service/pace/position tradeoffs. | Not currently stored in the tire model; the current comparison is strictly within-race and shown only when both calls are confirmed. |

## Two-tire versus four-tire contract

The retrospective comparison is emitted only when the race contains at least one confirmed two-tire stop and one confirmed four-tire stop. It can compare:

- confirmed service duration;
- measured outgoing per-corner O/M/I wear summarized to comparable wear;
- following-run early clean pace;
- pit-cycle position change;
- following-run green-lap sample size and caution state as visible context.

The result is an association from this race, not a recommendation or causal proof. The app withholds any unavailable dimension rather than substituting zero. It also states that traffic, fuel, weather, flag timing, rules, and prior strategy history are not controlled when those facts are absent.
