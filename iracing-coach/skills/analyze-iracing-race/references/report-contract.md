# Coaching report contract

## Default Race Card

Lead with the outcome. The first visible response is this compact card, populated from local analysis and validated evidence already on disk:

```markdown
# [Track] Race Card — [Car] · [Fixed/Open] · [Distance]

Bottom line: [single decisive sentence]

- Start: [highest-value opening-run action]
- Long run: [tire/pace protection action]
- Strategy: [fuel, pit-window, or reserve action]

## Corner playbook
| Corner/phase | Early | Middle | Late / older-set proxy | Groove migration |
|---|---|---|---|---|
| [sourced name or load-zone ID] | [...] | [...] | [...] | [...] |

## Race triggers
- Tire phase: [...]
- Pit: [...]
- Adjust/rollback: [...]
```

For ovals, everything before the evidence appendix must be 300 words or fewer. For road courses, use at most 420 words. Keep the headline to 18 words, each opening action to 14 words, and each playbook cell to 12 words or 72 characters. Put no more than two numeric targets in one cell. Use exactly three compact race-trigger lines. Do not dump full run tables, event lists, channel catalogs, setup inventories, or research notes into the default view.

## Corner-by-phase priority

Every mapped corner or material telemetry load zone must appear. Split rows by entry, center/minimum, or exit when the required action changes by phase. On an oval, this may be T1 entry, T2 center/exit, T3 entry, and T4 center/exit when a source validates those names. Otherwise use stable telemetry IDs such as `Load zone 1 entry`; never turn a derived zone into an official corner name.

Default to observational early/middle/late run phases and state each phase's exact per-tire green-lap-on-set bounds when a zero-age lifecycle boundary is confirmed. Label a confirmed-age late phase `older-set/late-run proxy`, never `worn`. Use fresh/settled/worn columns only when session- or history-derived pace/control/tire change points support inclusive green-lap-age boundaries; never derive them from universal lap counts or chronological thirds. Track caution exposure and heat cycles separately. If a phase lacks enough clean samples, put `[U] Phase guidance unavailable` in that cell.

Use exact speeds, brake points, brake-release values, minimum speeds, or throttle-pickup targets only when a cached aligned comparison has `comparison_quality.status: usable` and the relevant signal is present. Otherwise give a relative instruction and render `[U] Exact target unavailable`; do not browse merely to fill the cell.

Groove migration requires a lateral-position model with a calibrated inside/outside sign, adequate clean samples, and a supported phase delta. If any requirement fails, render `[U] Groove direction unavailable`. Never infer “move up” or “move down” from steering angle, coordinates without sign calibration, or generic track lore.

## Evidence tags and appendix

Use these evidence classes consistently:

- `[M]` measured directly from a named local or authorized reference channel/artifact;
- `[D]` derived by a documented calculation from measured inputs;
- `[I]` inferred coaching or likely causal interpretation;
- `[P]` diagnostic proxy that does not prove the underlying event or cause;
- `[U]` unavailable, unmeasured, uncalibrated, or unsupported.

After the Race Card, add an **Evidence appendix** of no more than six bullets or 180 words, whichever is shorter. Include only facts and gaps that materially affect the card: source/session identity, tire endpoint status, comparison quality/setup scope, strategy assumptions, tire-phase basis, and missing groove/target evidence. Tag every appendix bullet. Preserve measured, derived, inferred, proxy, and unavailable claims as separate statements.

When recorded tow or repair workload materially affected the race, prioritize one appendix bullet for it. Report incident points, tow duration, pit-road/stall/service time, mandatory repair, optional repair completed/remaining, and confirmed fast-repair use only when their channels support the statement. Do not crowd the corner playbook with an incident-only observation.

## Directness

Prefer:

> Run 1 faded 0.11 s/lap after Lap 8. Give up 2–3 mph at Load zone 1 entry for the first 10 green laps, release earlier, and protect the RF. `[D] [I]`

Avoid:

> Consider possibly being smoother because tire wear may have occurred.

Only use the numeric example style when its aligned telemetry has `comparison_quality.status: usable`. State that deltas are local minus reference. When `setup_scope` is `cross_setup_fallback` or setup differences dominate, say so and identify which conclusion remains robust.

## Race triggers and strategy

The three trigger lines must tell the driver what changes the plan:

- tire phase: green-lap boundary plus observed control/pace symptom;
- pit: fuel window, recorded start-fuel assumption, reserve, and caution-sensitive range;
- adjust/rollback: one measurable driving or open-setup experiment and its stop condition.

If the candidate run is repair/tow affected or optional repair remained at departure, make the adjust/rollback trigger require a clean repaired-car validation run before a setup conclusion.

Render deterministic strategy as fuel feasibility. State minimum stops and a pit window/equal-stint target when inputs allow. Do not call a stop optimal unless position, pit loss, service/rule constraints, future-caution uncertainty, and relevant history support that stronger judgment. Mark unavailable inputs explicitly.

## Tire wording

Always separate:

- measured: tire remaining from wear channels that changed across the pit-service window;
- derived: lap-time slope, brake/steer/load proxies, and early/late deltas;
- inferred: likely timing or cause of wear.

Temperature or pressure alone does not prove fresh wear. Render `stale_or_unconfirmed_at_stop` as an unconfirmed/stale reading, not a tire percentage. For `unavailable_at_stop` or `unmeasured_final_run`, write `tire wear not measured` rather than estimating a percentage.

When service evidence exists, state requested tires/fuel separately from confirmed delivery. A tire-use counter increment or odometer reset can confirm a tire replacement; `PitSvFlags` alone cannot. Report session-local tire-set age/distance without implying knowledge of pre-session use.

## Telemetry completeness and diagnostics

In the appendix or full report, report recorded, routine-loaded, and analyzer-consumed channel counts plus native and analysis rates. Say that the raw IBT is referenced, not silently copied. Do not list hundreds of channel names; use a compact profile or bounded slice only when a conclusion needs omitted/native-rate evidence.

Use transient event details only when they change the coaching conclusion: source record/time, lap distance, measured channels, derived method, and limitation. Never dump a long event list. Keep measured values, threshold/peak events, diagnostic proxies, and causal interpretation distinct.

Label wheel-lock, wheelspin, steering-work, brake-energy, and platform metrics as derived diagnostics or proxies. On an oval, stagger and inside/outside path radius can affect wheel-speed divergence. Do not turn a proxy coincidence or pace slope alone into a causal tire-management instruction; require corroborating clean-lap controls/dynamics and retain uncertainty.

Report `dp*` channels as requested pit adjustments, not confirmed garage changes. Report `TrackWetness` as a categorical SDK state, not a percentage.

Report incident-count changes as points, not damage. Repair timers and tow countdowns are measured state, but timer seconds do not identify the component or exclusive pace/time loss. Keep pit-road, stall, service-active, and repair-active durations separate because they overlap. Never describe a zero repair timer as proof that the car is identical to its undamaged state.

## Full archived report

Retain the deeper report as an archived artifact or provide it on request. Its order is:

1. race summary and confidence;
2. race-control/service timeline;
3. damage/tow/repair context and time breakdown when recorded;
4. run table with green/caution mix, fuel, pace, tire endpoint, and pit timing;
5. tire management and position-binned evidence;
6. detailed corner/reference comparison;
7. strategy forecast and historical context;
8. material evidence gaps.

The full report may expand the evidence behind the Race Card, but it must preserve the same evidence tags and must not upgrade an unavailable or proxy result into a measured fact.

## Visuals

Include visuals only when data exists and the relationship materially improves a decision:

- lap-time trend annotated by run and caution;
- lap-time trend with tow/repair/pit laps and repair-correlated candidate windows visually distinguished and excluded from the clean trend by default;
- tire remaining by corner and surface position;
- fuel by lap with pit/refuel points and projected window;
- calibrated track map colored by time/control delta versus reference.

Do not create decorative charts or a track map with fabricated geometry.
