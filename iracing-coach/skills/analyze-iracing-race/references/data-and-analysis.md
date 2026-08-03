# Local data and analysis contract

## Data discovery

Resolve the Windows Known Documents folder before falling back to `C:\Users\joshu\Documents`. The primary root is `Documents\iRacing`:

- `telemetry\*.ibt`: authoritative recorded telemetry and embedded YAML.
- `setups\<car>\*.sto`: saved setup artifacts; hash/reference them, but prefer the IBT `CarSetup` tree for the setup actually driven.
- `replay\*.rpy`: useful for a local SubSessionID join, not a telemetry replacement.
- `lapfiles`: best/optimal lap files; proprietary and optional.
- `app.ini`: disk-logging status. Read only unless the user explicitly requests a configuration change.

For executable coverage, call `iracing_local_inventory_workflow` from `scripts/workflow.py`; `iracing_data_inventory_workflow` and `inventory_iracing_data_workflow` are equivalent aliases. From the `scripts` directory, a direct fallback is:

```powershell
python -c "import json; from workflow import iracing_local_inventory_workflow as inventory; print(json.dumps(inventory(), indent=2))"
```

The inventory recursively stat-counts every file below the selected Documents root, classifies IBT, RPY, STO, lap, INI, and CFG artifacts, and returns recent paths plus setup/replay references. It reads only a safe telemetry/replay-key whitelist from `app.ini` and never changes it.

With `include_known_roots=True`, it also reports read-only file/extension/size metadata for standard iRacing install roots and local-app-data content. It does not parse game `.dat` packages. In local application data it excludes authentication, browser, cookie, cache, local/session storage, user-data, web-cache, and `iracing-electron` state. Never inspect those stores to extend inventory coverage.

The normal Windows install is `C:\Program Files (x86)\iRacing`; `C:\Program Files\iRacing` is also checked. `IRACING_COACH_INSTALL_ROOT` may select a different local install. Treat the complete install tree as read-only. Human-readable `version_system.txt` and per-car/per-track `version.txt` files may support content or physics cache fingerprints, but `.dat`/`.pak` assets remain opaque unless a later documented, tested reader is added.

## Latest Race selection

1. Read IBT headers and YAML without loading full sample buffers.
2. Group by `WeekendInfo.SubSessionID` and sim session.
3. Select the session whose `SessionInfo.Sessions[*].SessionType` or matching embedded event metadata is `Race`.
4. Select the newest completed race by embedded start/session metadata; use file mtime only as a tie-breaker.
5. Join multiple race IBTs for the same SubSessionID/session when a reconnect or re-entry split recording.

## Required channels

Use aliases and report absent fields. Important channels include:

- time/position: `SessionTime`, `Lap`, `LapCompleted`, `LapDistPct`, `RaceLaps`, `PlayerCarPosition`, `PlayerCarClassPosition`;
- state: `SessionFlags`, `OnPitRoad`, `PlayerCarInPitStall`, `PitstopActive`, `PitSvFlags`, `PlayerTrackSurface`;
- incidents/repair: `PlayerCarMyIncidentCount` (with driver/team aliases), `PlayerIncidents`, the `SessionFlags` repair-required bit, `PlayerCarTowTime`, `PitRepairLeft`, `PitOptRepairLeft`, `PlayerCarPitSvStatus`, `PlayerFastRepairsUsed`/`FastRepairUsed`, `FastRepairAvailable`, and `dpFastRepair`/the fast-repair service flag as requests only;
- controls: `Speed`, `Throttle`/`ThrottleRaw`, `Brake`/`BrakeRaw`, `SteeringWheelAngle`, gear, RPM;
- dynamics: lateral/longitudinal acceleration, orientation/rates, XYZ velocity, per-wheel speed/odometer, ABS state, and steering torque;
- fuel/service: `FuelLevel`, `FuelLevelPct`, `FuelUsePerHour`, `PitSvFuel`, per-corner tire-use counters, and service-state transitions;
- tires: per-corner wear, surface and carcass temperatures, live/cold pressure with channel/unit provenance, and tire-set odometers;
- in-car/requested adjustments: brake bias, weight jacker, grille tape, and pit fuel request. A `dp*` trace is a request, not proof of completed service;
- comparison conditions: `TrackTempCrew`/`TrackTemp`, `AirTemp`, density, pressure, humidity, wind, precipitation, `WeatherDeclaredWet`, `TrackWetness`, and `TrackUsage`. `TrackWetness` is a categorical SDK state, not a percentage;
- track shape: latitude/longitude/altitude when available.

## Full-telemetry architecture

Every analyzed source must preserve its complete variable catalog: name, description, unit, SDK type/type code, offset, element count, `count_as_time`, and byte size. For grouped recordings, keep authoritative catalogs per IBT and derive explicit union, intersection, and signature-conflict summaries. Never let a first-wins union hide a type/count/unit conflict.

Keep these concepts separate in artifacts:

- **recorded**: present in the raw IBT catalog;
- **loaded**: decoded into the bounded routine analysis table;
- **analyzed**: actually queried by an analyzer;
- **unloaded**: still available for a later bounded query.

Routine post-race analysis remains selective at 20 Hz because a large IBT can contain roughly 80 million channel-record values at native rate. Do not change the normal loader to eagerly materialize every channel. Instead:

1. Preserve every catalog and source fingerprint.
2. Load the high-value race/setup/tire/condition subset for the normal report.
3. Use `iter_telemetry_chunks` for bounded native-rate specialist passes.
4. Use `find_iracing_telemetry_events` to locate bounded native-record events before decoding a raw window.
5. Use `profile_telemetry`/`query_iracing_telemetry` for compact type-aware profiling or a targeted native slice.

The reader supports all current SDK primitive types (`char`, `bool`, signed `int`, unsigned `bitfield`, `float`, and `double`) plus arrays. A `count_as_time` array represents sub-tick samples; retain its element order and report its effective rate. Replace non-finite values with JSON-safe nulls in raw slices, while profiles count them explicitly.

`query_iracing_telemetry` is bounded to 12 named channels and 2,000 returned samples in slice mode. Record bounds are per source, start-inclusive/end-exclusive. Profile mode streams without retaining full columns and caches results by source SHA-256. Catalog mode never decodes sample buffers.

`find_iracing_telemetry_events` streams only the channels needed for the selected detectors and returns at most 500 events across selected sources. It supports brake onset/release, pit transitions, steering-torque peaks (including ordered `count_as_time` sub-ticks), shock-velocity peaks, and wheel-speed divergence. Filter by lap, session-time, normalized lap distance—including a start/finish-wrapping zone—or native record range. Each event carries exact source path/SHA-256, native record index, `SessionTime`, lap, lap distance, measured channels, method, classification, and limitation. Event caches are keyed by source SHA-256, detector version, selection mode, and the complete bounded query; a changed source or detector cannot reuse an old result.

`selection_mode: "chronological"` returns earliest matching events and may stop scanning when the cap is reached. `selection_mode: "severity"` scans the complete requested record/filter window and retains a deterministic, balanced strongest subset in bounded memory. Severity is a within-detector ranking score—peak/threshold ratio for torque and shocks, calibrated threshold score for wheel divergence, and transition strength for braking—not a causal or cross-physics importance claim. Check `scan_complete`, `candidate_event_count`, `candidate_counts_by_type`, `omitted_event_count`, and returned counts before describing coverage. Use severity mode for “most important transient” discovery; use chronological mode for known narrow windows and exact event chronology.

Resolve a session once, then pin follow-up native queries to the exact returned IBT path or SubSessionID instead of repeating `latest`. Query grouped/reconnected recordings one source file at a time because record bounds are per source and the sample budget is shared. Routine lap `start_index`/`end_index` values index the downsampled analysis table, not the native IBT; locate native windows using `SessionTime`, `Lap`, `LapDistPct`, source path, and returned native record indices.

Before analysis or a query, reject zero-record, truncated, trailing-partial, recently modified, or changing files. A completed disk IBT must have `file_size == buffer_offset + record_count * buffer_length`. Verify size/mtime across decode and SHA-256 hashing; a source change aborts before report/database/profile writes.

Raw-source retention is reference-only by default: do not silently copy gigabytes of IBTs. Archive full catalogs, compact profiles, derived analyses, and SHA-256 fingerprints. If an original is later deleted, existing artifacts remain usable but new raw-channel queries are unavailable. A future companion app may offer an explicit opt-in hard-link/copy retention policy.

## Race Card derived-data contract

A Race Card-producing analysis must expose the following compact objects, or an explicit unavailable status and reason. Do not manufacture numeric placeholders for missing data.

- `tire_phase_model`: observational `early`, `middle`, and `late` run phases with clean-sample counts and exact per-tire green-lap-on-set bounds whenever a zero-age lifecycle boundary is confirmed. A confirmed-age late phase is an `older-set/late-run proxy`, not measured worn tread. Optional `fresh`, `settled`, and `worn` inclusive bounds require session- or history-derived pace/control/tire change points; never create them from universal fixed lap counts or chronological thirds. Keep caution-lap and heat-cycle handling separate.
- `corner_definitions[]`: stable `corner_id`; validated name, naming status, and source; plus normalized lap-distance bounds for entry, center/minimum, and exit. When no map validates a name, retain the load-zone ID and mark the name unavailable.
- `corner_phase_metrics[]`: `corner_id`, corner phase, observational run phase, optional supported tire-state phase, entry/minimum/exit speed, brake onset/peak/release, throttle pickup, steering work/corrections, clean-sample count, traffic cohort, and `comparison_quality`. Preserve each metric's measured/derived/proxy status and units.
- `groove_model[]`: normalized lateral offset or lane fraction; inside/outside sign calibration and source; phase medians, variance, deltas, and migration trigger; confidence; clean-air/traffic classification; and `unavailable_reason`. Coordinates or steering without a validated sign/lane transform are insufficient for groove direction.
- `strategy_card`: green/caution fuel burn, recorded start-fuel assumption, reserve, all-green and observed-caution ranges, minimum stops, pit window/equal-stint target, and availability of rules, position, and pit-loss evidence.
- `evidence_registry[]`: stable `statement_id`, evidence type (`measured`, `derived`, `inferred`, `proxy`, or `unavailable`), source channel/artifact, confidence, and limitation.
- `damage_repair`: channel coverage; incident-point changes; tow episodes; pit visits with pit-road, stall, service-active, mandatory-repair-active, and optional-repair-active durations; peak/countdown/remaining repair seconds; fast-repair request versus confirmed counter change; affected/candidate laps and runs; target/coaching eligibility; and explicit component/severity and time-overlap limitations.

Separate clean laps, traffic/dirty-air laps, cautions, pit transitions, and invalid/off-track samples before deriving corner or groove phase medians. Keep comparison cohort and setup scope attached to the metric. An exact coaching target requires a cached aligned reference with `comparison_quality.status: usable`; otherwise emit relative guidance plus `unavailable`. A groove recommendation requires calibrated sign and adequate clean samples; otherwise emit `unavailable_reason` and no high/low claim.

## Fast-path query and cache discipline

The default race-analysis path is local-first with research policy `cache_only`:

1. Resolve the latest Race once, pin the exact source files/SubSessionID, and validate their SHA-256 identities.
2. Reuse an existing analysis artifact when its source fingerprint and analyzer contract are valid; otherwise run the bounded local analysis.
3. Read only validated seasonal knowledge, cached Garage61 comparisons, and strategy history already on disk.
4. Produce the Race Card even when optional caches are missing, stale, partial, or unauthorized. Record the gap instead of blocking or inventing a value.
5. Perform browser/web/Garage61 refresh work only after the card, or when explicitly requested.

Canonicalize every local analysis, telemetry query, and event query by source SHA-256 plus the complete operation: mode/detector version, selectors, channels, record/time/lap-distance bounds, filters, event types, target rate, and selection mode. Keep the result in turn-local state and never issue an identical query twice in one turn. A later report section must reuse the existing result. Changed bounds, filters, or source fingerprints form a new query.

Network work is never required to complete the fast-path card. Missing official corner names fall back to stable load-zone IDs; missing usable comparisons produce `[U] Exact target unavailable`; missing lateral calibration produces `[U] Groove direction unavailable`. Continue to use bounded profiles/event windows rather than materializing a full IBT.

## Damage, towing, and repair context

Use recorded SDK state conservatively:

- An incident-count increase measures awarded incident points; it does not prove physical damage.
- `PlayerCarTowTime > 0` measures a tow countdown. Group contiguous positive samples into a tow episode and retain both elapsed episode time and the recorded timer peak.
- The `SessionFlags` repair bit measures iRacing's repair-required state; it does not identify a component or severity.
- `PitRepairLeft` and `PitOptRepairLeft` measure mandatory and optional repair time remaining while active. For each pit visit, preserve the peak observed workload, countdown served, last value before stall/service exit, whether zero was reached while still in the stall, and any remaining optional repair at departure.
- A fast-repair request flag or `dpFastRepair` is only a request. Confirm use only when `PlayerFastRepairsUsed` or `FastRepairUsed` increments.
- `PlayerCarPitSvStatus` explains service progress and stall-position/service failures. Preserve its raw value and known SDK label; a status such as `cant_fix_that` is explanatory state, not component-level diagnosis.
- Pit-road duration, stall duration, service-active duration, and timer-active duration are overlapping measured intervals. Report each independently; do not sum them into repair-exclusive time loss.
- Repair timers prove a repair workload, not the damaged component. Component, aero balance, suspension geometry, engine health, and exact on-track performance loss remain unavailable unless corroborated by separate measured evidence.

Tag tow, pit, and repair-active laps in the lap/run contract. A lap between a recent incident increase and a later repair workload is a derived repair-correlated candidate, not confirmed damage onset. A run after leaving with optional repair remaining is confirmed incomplete optional-repair context, but the exact pace cost remains unavailable. Exclude these laps from clean target-lap, tire-falloff, setup A/B, and representative-reference calculations by default; retain them for the race narrative and allow an explicit diagnostic view.

## Runs and flags

Define a run as track activity between confirmed pit-service episodes. Debounce pit-stall/service flags and fuel/tire state changes. Merely crossing the pit-road line is not sufficient evidence of service. Do not split a race merely because a new IBT began.

Keep repair context attached to the run boundary. A repair stop still ends a run, but its duration must not become an ordinary strategic pit-loss sample. A post-stop run with optional repair remaining must not be treated as a clean setup or tire-management baseline.

Classify green/caution exposure from sampled `SessionFlags`, then cross-check embedded or remote official caution totals. Allow mixed laps and fractional green/caution allocation when transitions happen mid-lap.

## Tire observations and causality

iRacing exposes tire readings discretely around pit service. A usable observation requires wear-channel values both before and after the service window and at least one common wear value changing by the freshness threshold. Temperature or pressure without changed wear is not a wear measurement. Assign only a confirmed fresh post-service reading to the tire set/run that just ended. Record:

- sample/session timestamp;
- pit-service event and run number;
- L/M/R remaining percentage for LF/RF/LR/RR;
- surface/carcass temperature and live/cold pressure with channel and source-unit provenance;
- requested versus confirmed service, using per-corner tire-use counter increments and/or odometer resets as replacement evidence;
- whether the wear reading changed;
- a session-local tire-set number, accumulated distance, green/caution laps, and green-run heat cycles;
- lowest tire and cross-car/inner-middle-outer imbalance.

Use `measured_at_stop` only for a confirmed fresh change. Use `stale_or_unconfirmed_at_stop` when wear values exist but do not establish a new post-service reading, `unavailable_at_stop` when no usable wear reading exists at the stop, and `unmeasured_final_run` when the run has no ending service. Never interpolate an exact wear curve or report temperature/pressure as wear.

Use position-binned control proxies to explain likely causes:

- brake-energy proxy: integral of brake fraction × speed;
- brake-steer overlap duration;
- steering-work/scrub proxy: integral of absolute steering × speed;
- lateral-g exposure;
- corrections/counter-steer;
- entry, minimum, and exit speed;
- throttle pickup;
- early/middle/late run changes.

Use native-rate targeted passes for brake onset/release, brief wheel-speed divergence, shock peaks, steering corrections/torque, and pit transitions. Brake, pit, torque, and shock events are threshold/peak derivations from measured SDK channels. Wheel-speed divergence is a diagnostic proxy calibrated against prior completed clean-unbraked laps in the same 1/120-lap-distance bin; oval stagger, path radius, unloaded-wheel behavior, sensor semantics, and baseline coverage still prevent treating it as proof of lock, wheelspin, wear, or a setup cause.

On NASCAR ovals, pay special attention to right-front remaining wear, RF inner/middle/outer distribution, excessive entry speed, late/heavy brake release, sustained wheel angle, and throttle timing. Do not assume an RF cause merely because the event is an oval; require measured pattern plus trace evidence.

## Fuel and strategy

Compute fuel used from negative `FuelLevel` deltas and exclude refuel jumps. Separate green and caution burn. Treat requested pit-service fuel as a request, not guaranteed delivered fuel.

Archive a chronological `race_timeline` with race start/end, grouped sampled caution periods, pit/service events, requested versus confirmed tires/fuel, evidence class, and channel provenance. This is a telemetry chronology; do not claim official caution boundaries from sampled flags alone.

Strategy forecasts must include reserve, expected caution mix, overtime/GWC exposure, pit loss, service constraints, and scheduled distance when known. The deterministic `strategy_forecast` reports all-green and observed-caution-mix fuel range, an operational reserve, minimum fuel stops, and equal-stint all-green targets when the inputs are sufficient. Label it fuel feasibility, not optimal strategy: it lacks perfect future cautions and may lack position, pit loss, stage/service rules, and overtime requirements. Historical comparison keys are season + exact car + exact layout + fixed/open + race length; filter further by weather, tire compound, BoP, and setup fingerprint.

## Confidence

- High: local controls/fuel/flags/setup plus a confirmed fresh pit-service tire reading and authoritative race metadata.
- Medium: strong local telemetry but missing tire snapshot, result enrichment, or comparison laps.
- Low: Garage61/results-only fallback or major required channels missing.
