---
name: analyze-iracing-race
description: Inventory Joshua's local iRacing data; analyze recorded races, telemetry (.ibt), setups, tires, fuel, cautions, incidents, damage/repair context, towing, strategy, and driving; and build NASCAR-first open-setup packages with controlled tuning history. Use when asked to find, analyze, summarize, compare, or coach an iRacing race, run, stint, lap, car/track combination, tire issue, fuel plan, pit/repair time, damage, race strategy, new-week package, setup or tune an open race, or diagnose a handling complaint such as tight/loose entry, center, exit, bottoming, bumps, or wheelspin. When no session is named, select the latest recorded Race session rather than the newest telemetry file.
---

# Analyze an iRacing Race

Produce an evidence-first post-race review with direct coaching. Prioritize NASCAR across oval and road-course events, while keeping the workflow valid for every iRacing discipline.

## Core rules

- Treat source telemetry and source `.sto` setup files as read-only. Never generate, overwrite, or reverse-engineer a simulator-loadable `.sto`.
- Default to the latest session whose embedded metadata says `Race`. Group files by `SubSessionID` and sim session; never infer Race from file modification time alone.
- Use local IBT/YAML as the authority for high-frequency controls, fuel, pit service, the complete setup actually driven, and discrete tire readings. A same-name HTML/STO artifact is supporting provenance, not a replacement for the embedded `CarSetup` tree.
- Preserve the complete per-source IBT channel catalog, even though routine analysis loads only a high-value subset. Distinguish recorded, loaded, and analyzer-consumed channels. Use bounded native-rate queries for omitted channels and transient details; never materialize every channel from a large race into the model context.
- Analyze only finalized IBTs. Require the declared telemetry extent to equal the file size, a short quiet period, stable size/mtime through decoding and hashing, and a verified SHA-256 before writing artifacts.
- Give garage-change recommendations only for an open setup. For a fixed session, provide driving, tire, and strategy coaching without implying that the user can tune the event setup.
- Let driver feedback identify the handling symptom. Telemetry may corroborate where and when it appears, but it never uniquely identifies a setup parameter as the cause.
- Change one setup system per controlled A/B experiment, preserve an exact fingerprinted baseline, name the expected effect and risk, and provide a rollback.
- Treat tire wear as a discrete pit-service observation for the preceding run. Accept it as measured only when wear channels exist and change across the service window; temperature or pressure alone is insufficient. Never present it as continuous or invent final-run wear.
- Phrase control-to-wear causality as likely or consistent with the evidence. Brake, steering, load, and track-position traces locate driving habits; they do not reveal exactly when wear occurred.
- Treat incident-count changes as measured incident points, not proof of physical damage. Use recorded tow time, mandatory/optional repair timers, and confirmed fast-repair counter changes as repair evidence; iRacing telemetry does not identify every damaged component or its exact aerodynamic/mechanical severity.
- Separate pit-road transit, stall occupancy, service-active time, tow time, and repair-timer activity. Repair may overlap fuel or tire service, so do not add overlapping clocks or call all stall time repair-exclusive time loss.
- Prefer comparable, representative Garage61 laps over a lone world-record lap. Keep fixed and open setup cohorts separate.
- Reuse a fresh seasonal car/track bundle; do not repeat that research during the same iRacing season unless relevant physics/content changed or the bundle is incomplete/invalid. Reuse the optional Garage61 index and CSVs independently, and sync them only when missing/stale or explicitly requested.
- Never read Garage61's internal agent database, browser cookie stores, saved passwords, or raw authentication state.

## Workflow

Read [companion-app.md](references/companion-app.md) when implementing, packaging, or handing off a standalone companion UI over this skill's MCP/CLI backend.

### Fast path: local Race Card first

- For ordinary race analysis and planning, use `cache_only` as the default research policy. Resolve and analyze the pinned local Race IBT, read validated seasonal knowledge, cached Garage61 comparisons, and strategy history already on disk, then render the Race Card before any browser, web, Garage61 authentication, sync, or cache refresh.
- Network access is not on the critical path. Missing, stale, pending, or unavailable enrichment must not delay the Race Card; use telemetry load-zone IDs, relative coaching, and explicit `[U] unavailable` labels where evidence is missing.
- Reuse an analysis, native-query, event-query, or cache-status result within the turn. Never issue an identical query twice for the same source SHA-256, mode, selectors, channels, bounds, filters, event types, and rate.
- If Garage61 API access is still pending, do not call authentication or sync on every race. Reuse authorized cached/manual data and state the limitation once.
- Run optional network enrichment only after the default Race Card has been delivered, or when the user explicitly asks to refresh/research. A later enrichment may add evidence; it must not silently replace measured local facts.

### 1. Resolve and analyze the session

Call `discover_iracing_sessions` when the user names a date, session, or ambiguous race. Otherwise call `analyze_iracing_race` with `selector: "latest"`.

If the local MCP tools are unavailable, run `scripts/coach_cli.py analyze --session latest` with the bundled Codex Python runtime or another Python 3.10+ interpreter. Read [data-and-analysis.md](references/data-and-analysis.md) before changing the parser, run boundaries, tire attribution, or strategy model.

Inspect `source.channel_coverage` in every analysis. The routine post-race pass deliberately keeps memory bounded while preserving the full recorded catalog. Call `query_iracing_telemetry` when a conclusion needs a channel that was not loaded, a native-rate transient, an SDK array such as `SteeringWheelTorque_ST`, or a focused record window:

- `mode: "catalog"` searches every recorded name, description, type, unit, and array shape without decoding samples.
- `mode: "profile"` streams selected channels (or, when explicitly necessary, the whole catalog) into compact type-aware statistics and caches the result by source SHA-256.
- `mode: "slice"` returns at most 2,000 samples from at most 12 explicitly named channels, preserving native record indices and array values. Keep the slice as small as the question permits.

Call `find_iracing_telemetry_events` before requesting a raw slice when the question concerns brake onset/release, pit transitions, steering-torque peaks, shock-velocity peaks, or wheel-speed divergence. Filter by lap, session-time, lap-distance, or native record range and select only the needed event types. Use `selection_mode: "chronological"` for a known window; use `selection_mode: "severity"` to scan the complete requested window and retain a balanced strongest subset across matched event types. It returns exact source-record indices, reports candidate/omission and scan-completeness metadata, and caches the bounded result by source SHA-256 plus query. Brake/pit/torque/shock events are derived from recorded channels; wheel-speed divergence is a calibrated diagnostic proxy, never proof of lock, spin, tire wear, or setup cause.

Use `target_hz: null` for a follow-up native-rate slice around the returned event record. Use 20 Hz for ordinary summaries and slow conditions. If MCP is unavailable, use the JSON CLI equivalents `telemetry-events` and `telemetry-query`; do not fall back to manually loading an entire IBT into context.

After resolving `latest`, pin every specialist query to the returned exact `source_files` path or SubSessionID. Do not query `latest` again because a new recording could change the target between calls. For grouped or reconnected sessions, query each returned source file separately; record bounds are per source and the returned-sample budget is shared. Analysis-table lap indices are not native IBT record indices, so locate a native event window with recorded `SessionTime`, `Lap`, and `LapDistPct` rather than reusing a routine lap index.

The companion app permanently retains every accepted finalized raw IBT as an atomic, verified, content-addressed copy under its portable Documents data home. Originals remain read-only, identical bytes are deduplicated, and analysis records retain both source and durable-copy provenance. A raw-copy failure must remain retryable and explicit; it must not mutate the original or pretend that future raw-channel queries are durable.

Use `race_timeline` for sampled race-control periods and confirmed/requested service chronology. Use `strategy_forecast` as a fuel-feasibility forecast: it may estimate all-green and observed-caution-mix range, reserve, minimum stops, and equal-stint targets, but it does not prove the optimal pit call without position, pit-loss, future-caution, and rule evidence.

Inspect `damage_repair` before explaining pace, selecting comparison laps, judging a stop, or recommending setup changes. Exclude tow/repair/pit laps from normal pace coaching. Treat laps between an incident increase and a later recorded repair workload, and laps run after departing with optional repair remaining, as damage-correlated context rather than clean proof of tire falloff or setup behavior. Preserve the distinction between confirmed repair evidence and inferred affected-lap boundaries.

When local-data coverage matters, execute `iracing_local_inventory_workflow` from `scripts/workflow.py` (aliases: `iracing_data_inventory_workflow` and `inventory_iracing_data_workflow`). It inventories the full Documents data root and returns read-only metadata for known install/local roots. It never reads authentication or browser stores. See [data-and-analysis.md](references/data-and-analysis.md) for the exact scope and safe invocation.

If no Race IBT exists:

1. Report that disk telemetry is missing.
2. Fall back to Garage61 and iRacing result data when available.
3. Mark every unavailable measurement and reduce confidence.
4. Check `Documents\iRacing\app.ini` logging settings without silently changing them.

### 2. Build or tune an open setup when requested

Read [setup-tuning.md](references/setup-tuning.md) whenever the user asks for a new-week package, setup choice, open-race tune, or handling change.

For a new week, call `catalog_iracing_setups`, then `build_open_setup_package`. Tell the user that the package is a coaching/baseline record and that they still load or save the setup in iRacing. Prefer an exact current-season car/track/session-type baseline; otherwise transfer only the logic from a defensible donor family and revalidate tech, dynamic platform, gearing, and Q-versus-R intent. Treat an HTML filename/header conflict as provisional until a target-track IBT names that exact setup artifact.

For a handling complaint, analyze the relevant session, call `iracing_setup_history`, then call `recommend_open_setup_tuning` with the driver's original symptom wording. Use builder-note directions only when `builder_note_provenance.used` is true; provisional and donor-derived notes are intentionally suppressed. Apply only the first planned change in the simulator, save it under a new name, run the controlled test, and call `record_open_setup_feedback` with the result and candidate analysis. Never modify the source STO. If the session is fixed, stop at coaching.

Do not tune around a damaged or incompletely repaired car. If the candidate run is tow/repair affected, optional repair remained at departure, or the clean comparison window is insufficient, withhold the garage change and request a finalized clean A/B run after repair.

Sections 3-5 are enrichment. Their validated on-disk results may feed the fast path, but status checks, syncs, browsing, and refreshes happen after the default Race Card unless the user explicitly requested research or refresh.

### 3. Check the seasonal knowledge cache

Derive context from the analysis and call `iracing_knowledge_cache_status`.

- `fresh`: reuse the validated car/track research. Inspect `manifest.files.garage61` separately; `fresh` does not mean that optional component exists.
- `missing`: gather the full car/track/reference bundle. A new season key normally returns `missing` while preserving the prior season's archive.
- `incomplete`: preserve any valid components, then gather the missing research. Empty facts or sources—and a Garage61-only sync—do not make a bundle fresh.
- `stale`: refresh because the bundle was explicitly invalidated or the supplied sim/physics fingerprint changed within the season.
- `invalid`: rebuild from trusted sources because the manifest, component shape, or content hash failed validation.

Key research bundles by iRacing season + car ID/path + exact track layout + fixed/open setup. Keep race length in strategy history, not as a reason to repeat track/car research. Pass the same stable `sim_physics_fingerprint` string/object when checking and archiving a bundle; a mismatch marks it stale before the next season. Include only relevant sim build, tire model, car physics, and track-content identifiers—not timestamps or other volatile values. Retain tire compound, BoP, weather, fuel state, and setup fingerprint as comparison metadata.

### 4. Gather Garage61 comparisons

Inspect `manifest.files.garage61` and the archived Garage61 index first. Reuse a valid seasonal index and its CSVs. Call `garage61_auth_status`, then `sync_garage61_references` only when the component is missing/stale, the user explicitly requests a refresh, or credentials have newly become available. Do not retry a still-pending API request every race. Read [garage61-and-web.md](references/garage61-and-web.md) before authentication, browser fallback, candidate ranking, downloads, or web research.

Select comparison candidates in this order:

1. Same car and exact track variant.
2. Same iRacing/Garage61 season.
3. Same fixed/open setup type.
4. Same tire compound and similar BoP, track state, temperature, weather, and fuel state.
5. Clean, complete telemetry.
6. Several drivers modestly faster than Joshua, plus a smaller elite sample.

Query fixed and open separately and tag the cohort locally. For open sessions, compare setup fingerprints and explain when setup differences weaken a driving conclusion.

Garage61's normal personal API access is limited to Joshua and teammates. State the observed comparison scope. Global-visible lap search remains disabled unless Garage61 grants that capability and `global_visible_laps_approved` is explicitly set to `true`; never infer approval from a valid token or signed-in browser. If it is unavailable, use authorized website selection/export or manually exported CSVs, and never describe the smaller pool as the full public field.

Download authorized reference CSVs when possible. The sync preserves their fields, normalizes recognized units, aligns local and reference traces in identical lap-distance bins, and stores `comparison_quality`, `reference_comparisons`, `benchmark_profile`, and `coaching_targets`. Use exact targets only when `comparison_quality.status` is `usable`; label `cross_setup_fallback` and any weak coverage. Deltas are local minus reference.

### 5. Gather car and track knowledge only when needed

For a missing, incomplete, stale, or invalid bundle, use current primary sources for:

- exact track layout, length, banking/elevation, pit-road rules, and corner names;
- official car manual/setup notes, drivetrain, braking, tire behavior, and relevant release notes;
- series format, scheduled distance, stage or caution rules when applicable;
- a track map or source image when it materially improves the coaching report.

Reconstruct a local track shape from telemetry coordinates when available. Store source URLs, titles, retrieval time, facts, and image/manual provenance. Do not treat search snippets or unlabeled community claims as established facts.

Call `archive_iracing_knowledge` after research with non-empty facts and sources plus the same sim/physics fingerprint used for the status check. Include Garage61 query scope and candidate metadata in the bundle.

### 6. Add historical strategy context

Call `iracing_strategy_history` for the current context. Prefer same-season history, same fixed/open type, and the same scheduled lap/minute distance. For open races, prioritize similar setup fingerprints and weather.

Evaluate each stop using:

- green and caution laps in the run;
- green/caution fuel burn and fuel remaining;
- measured tire remaining at the stop, if present;
- pace degradation and early-versus-late control load;
- whether the stop occurred under caution;
- scheduled distance, overtime/GWC reserve, pit loss, service rules, and track position when known;
- comparable prior races and runs.
- measured pit-road/stall/service durations, repair-timer activity, tow time, and whether optional repair remained at departure.

Separate requested service from confirmed service. Use tire-use counter changes and/or a per-corner odometer reset to confirm replacement, then carry a session-local tire-set index with distance, green/caution laps, heat/run cycles, pressure, temperature, and measured wear. Do not treat a tire request flag as proof that the tire was changed.

Do not call a strategy optimal from fuel and tire data alone when position, pit-loss, or future-caution evidence is missing. Give the best-supported next-race plan and list the uncertainty.

Do not learn ordinary pit loss or normal stint pace from a repair/tow-affected stop or lap. Report repair-related context separately from the strategic pit decision.

### 7. Produce the coaching report

Read [report-contract.md](references/report-contract.md). The default visible output is a Race Card, not the full analysis dump. For an oval, keep everything before the evidence appendix to 300 words or fewer. Lead with one bottom-line sentence and no more than three short actions.

Make the corner playbook the central coaching element. Cover each material corner or telemetry load zone by phase, prioritizing entry, center/minimum, and exit behavior across observational early/middle/late phases. When a zero-age lifecycle boundary is confirmed, show exact per-tire green-lap-on-set bounds and label the late phase `older-set/late-run proxy`, never measured worn tread. Use fresh/settled/worn tire-state phases only when session- or history-derived pace/control/tire change points support their inclusive green-lap bounds. State the basis and confidence and track caution/heat-cycle exposure separately.

Give exact corner/load-zone targets only when a cached aligned reference has `comparison_quality.status: usable`. Otherwise give relative instructions and explicitly render `[U] Exact target unavailable`. A telemetry-derived load zone is not an official corner name unless a sourced map establishes the name. Give groove migration only when a lateral-position model has a calibrated inside/outside sign and adequate clean samples; otherwise render `[U] Groove direction unavailable` rather than guessing high/low movement.

End with three compact race triggers for tire phase, pit strategy, and adjustment/rollback, followed by a short evidence appendix. Tag evidence as `[M]` measured, `[D]` derived, `[I]` inferred coaching, `[P]` proxy, or `[U]` unavailable. Keep full run tables, event lists, channel catalogs, and research detail in the archived/full report or provide them only on request.

When material repair or towing evidence exists, add one concise race-context statement and shade/exclude affected laps in visuals and target-lap selection. Keep the corner playbook central. State incident points separately and never infer the damaged component from repair seconds or pace loss.

Use mph, gallons, °F, psi, pounds, and inches by default. Use charts and the telemetry-derived track map only when they materially clarify pace falloff, tire balance, fuel windows, or corner deltas.

For an open-setup experiment, include the baseline fingerprint, reported symptom, corroborating telemetry, one planned change, expected effect, side effects, success criteria, and rollback. Keep measured, derived, inferred, and planned statements distinct.

## Authentication

Prefer the securely stored Garage61 personal access token for routine API calls. The user enters it through `scripts/configure-garage61.ps1`; never ask them to paste it into chat or place it in a command argument, repository, report, or cache.

Keep authenticated API traffic on the exact HTTPS `garage61.net` origin. The client rejects other production hosts and cross-origin redirects before forwarding the Bearer token; loopback HTTP(S) origins exist only for tests.

Use the persistent signed-in browser for Garage61 UI capabilities and iRacing enrichment that lack an API path. Reauthentication can still be required after service-side expiration or revocation. A signed-in iRacing member page is not an iRacing Data API OAuth client credential.

## Failure behavior

- Continue local analysis when web, Garage61, or result enrichment fails.
- Name the missing source and the consequence for confidence.
- Never fabricate tire readings, setup parameters, cautions, field position, or benchmark deltas.
- Never fabricate damage, a damaged component, exclusive repair time loss, or an affected-lap boundary. Mark unavailable or inferred context explicitly.
- Preserve successful local artifacts and cache writes even when one enrichment adapter fails.
- Offer the next concrete recovery action, not a generic error.
