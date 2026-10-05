# Web investigation

Race, Runs, Sections and Traces on one viewport-bound workspace, in the Broadcast language (`docs/08-rewrite/design`, Direction B) with Direction A's fixed columns on every data board. The shell (W7) mounts one component:

```tsx
import { InvestigationWorkspace, createAnalysisLoader } from "./investigation";

const loader = createAnalysisLoader();            // 3 analyses, 96 MB, in memory
<InvestigationWorkspace
  loader={loader}
  analysisId={analysisId}
  view={last?.view} comparisonId={last?.comparison_id} sectionId={last?.section_id}
  onSelectionChange={(s) => saveLastInvestigation({ analysis_id: analysisId, view: s.view, comparison_id: s.comparisonId, section_id: s.sectionId })}
  // P · Q · R: the event's phases (session id, recorded type, analysis state when known); replaces sessionPicker.
  phases={phases} currentSessionId={sessionId} onPhase={(id) => navigate(...)}
  // Saved layouts, Default included; commands: select, create (New, Copy, first edit of Default), edit, delete.
  traceLayouts={layouts} activeTraceLayoutId={activeId} onTraceLayoutCommand={persist}
  // Without traceLayouts: the shell's active layout (traceLayout, traceLayoutName, onTraceLayoutChange) plus Default, kept for the visit.
  reducedMotion={prefs.reduced_motion}
/>
```

Requests are made by `load.ts` only: the overview (race, runs, investigation, track) when an analysis opens, the catalog plus the default channel set when Traces opens, and a layout's other channels when it needs them (on mount and on layout change). Pointer movement, hover, selection and layout editing make none (`cursor/` is checked for network calls by a unit test; the G2 harness logs requests during a sweep). A loaded analysis stays in the cache and stays inspectable when the server drops. A traces request the server refuses with 413 is repeated for lap ranges (halves, recursively) and the bodies are joined. Bin i lies at lap fraction (i + 0.5) / bins, the backend's `lap_pct`.

## Web round 2 (`docs/08-rewrite/web-round-2-plan.md`, Investigation)

- **One comparison selector**: the Comparisons list on Race (rows named by their groups, `Run 2 → Run 3`, `Run 3: L61–65 → L85–89`); no toolbar Compare. The selection moves the Race brackets (`Earlier L61–65`, `Later L85–89`, split around omitted laps, in the group's run colour), the comparison panel, the Traces rail marks (a bar at the row's right edge: solid Earlier, outlined Later) and the comparison strip above the traces. `ui/comparison-model.ts` holds the names, brackets, rows and bars; nothing in it recomputes a median, a delta or a screening.
- **The picture** (`ui/ComparisonPicture.tsx`): Earlier and Later lap dots on one lap-time scale with the backend's median marks, the backend's signed difference large with its direction, green/total counts, paired bars for `fuel_start_gal` (else the `fuel` metric), the `throttle` and the `traffic` metrics, each from zero on its own labelled scale. No pace result: one line (`No pace result · every lap screened · close traffic`).
- **Race**: a hollow dot is `Not used in pace fit`; hover, focus or tap names the run's fit and the backend's reasons. Caution laps are subdued dots in the caution band at their actual time (on the top edge, dashed, when off the scale). Pit markers are compact; the stop card says `Repairs and penalties: not extracted` once.
- **Runs**: the ordered run palette (`--run-1` …, `core/palette.ts`) on trend lines, run cards and the rail's sticky run row, line style second; the observed trend (the backend's 5-observation median) and the screened fit drawn apart, the fit clipped to its `domain` and labelled with the backend's slope and eligible count (`ui/runs-model.ts`); run cards: sparkline, fit, used, screened, fuel band, tire laps.
- **Traces**: lap colour is race progress over the full phase (`progressColors`, cut into at most `max_color_groups` steps so paths stay bounded), never recoloured by a filter; the endpoints sit in the rail's toolbar. The toolbar is inside the rail's scroll parent, `--toolbar-height` tall; the run rows stick at that height with no top margin, so pixel 0 of scrolling moves nothing.
- **Sections**: a section click is a state change; the cursor keeps its place across the charts. Section times are coloured gain/loss against the selected comparison, neutral without a pace result. The shell writes `last_investigation` to the device at once and to the prefs when the investigation is left (`ui/investigation-slot.tsx`): a prefs write shows the outbox bar above the workspace, and that shift re-fitted and redrew it on every click.
- Checks: `src/investigation/__tests__/round2-investigation.test.ts`; `e2e/app/investigation.spec.ts` at 1440, 1024 and 390 on `fixtures/round2.ts` (served in place of the mock's `an-103`), which also writes the screenshots to `web/target/round2/A3/`.

## Round 3, Race and Runs (`backlog.md` #39–#47; supersedes the Race lines above)

- **Race** has no comparison machinery (#43, #45): no Comparisons list, brackets or panel; the chart takes the tab. Under it, one line per run in the run's colour: `Run 1 · L3–24 · +0.033 s/lap · 15.9 gal` (the fit's laps and slope, fuel at the run's start; a figure the backend did not report is left out). A comparison in the route still drives the Traces rail marks and strip and Sections. The lead action's `Inspect section` entry is `inspectSectionEntry` in `ui/comparison-model.ts`, for the Sections tab.
- **Lines** (#44): laps are dots; the only line is the backend's screened fit, a smooth monotone curve over the laps of its fitted span (`fitOnLaps`, `smoothPath` in `ui/runs-model.ts`); the observed trend is not drawn. Hollow dots: `Not in pace fit · close traffic` on hover; the header says once `Pace fit: 39 of 77 laps`.
- **Legend** (#40): the runs only. Pit, incident, caution, fastest (a magenta ring) and position explain themselves on hover. The run palette (`identity.runs`) has no amber and no red.
- **Run identity by tire set** (#42): `runIdentities` gives a run after a stop with `tires_changed: []` its set's hue, lighter (`Run 3 · same tires`), on Race, Runs and the rail's run rows; a stop without a tire record claims no shared set.
- **Context layers** (#46, `ui/race-layers.ts`): toggles in the chart header, off by default, remembered on the device (`coach.race-layers`): track temperature (thin line, right axis), track usage (strip tint, offered only with a `track_usage_percent` series), traffic (tick under the dot, `clean_context.traffic_proximity_fraction`), wheel slip (ring, the wheel-speed spin and lock proxies), fuel (faint line, `fuel.start_l`). Each is one mark per lap from the race view's laps, read out on the lap with its evidence label; a layer no lap reports is not offered.
- **Runs** (#47): each run card toggles its run (dots and fit leave, the card dims, the chart rescales); one run always stays; remembered for the tab's session (`coach.runs-hidden.<analysis>`).
- **Pit detail** (#39, #41): tires, fuel and the stop time; nothing about fields the backend does not report. The plot's top margin and `overflow: visible` keep the pit tag's glow whole.
- Checks: `src/investigation/__tests__/round3-race-runs.test.ts`; `e2e/app/investigation.spec.ts` on `fixtures/round3.ts`, screenshots to `web/target/round3/R3/`.

## Round 4, Race, Runs, Traces and the rail (`backlog.md` #58–#63, #66; supersedes the lines above where they differ)

- **Run line** (#59a): `Run 1 · L3–24 · fall-off +0.07 s/lap · 0.33 gal/lap`. The span is the run's green laps (`plot_pace`); the fall-off is the last green lap time less the first over the laps between, traffic laps included (`fallOff` in `ui/runs-model.ts`), absent under three green laps; the burn is the runs view's `fuel.green_gal_per_lap`. Hover, focus or tap puts `observed · L3 … s → L24 … s` in the chart readout. No `Pace fit: n of m` header on Race or Runs.
- **Fit coverage** (#59a): the screened fit is drawn (Race curve, Runs line and card sparkline, the card's Fit figure) only when its eligible count is at least half the run's green laps (`fitDrawn`); otherwise nothing is drawn and nothing says why. Run cards add the observed fall-off beside the fit.
- **Layers** (#60–#62): toggles carry the name only (`Track temp`, `Track usage`, `Fuel`); the readout tags a figure `· measured` and a lap without one `Fuel no reading`. Traffic and wheel slip are gone: the backend has no per-lap gap ahead or behind and no per-lap slip figure, so no `Gaps` layer either.
- **Fuel line** (#63): one line through every lap with a reading; a refill is a step up at the lap boundary with the new level labelled; only an absent reading breaks it (`seriesSegments`, `fuelPath` in `ui/race-layers.ts`). Runs no longer break it: the backend's service lap belongs to no run, which split the line at every stop (the 2026-10-02 Las Vegas gap).
- **Rail** (#58): lap numbers are plain on every lap; a lap screened out of the fit shows the chart's hollow dot in the flag column, and the backend's reason is the row's hover. The spotlight marks the row, not the number.
- **No earlier/later anywhere** (#66): the Traces comparison strip and the rail's group marks are gone (`ComparisonPicture.tsx` deleted, the drawing helpers left `ui/comparison-model.ts`); the backend's comparisons stay in the model.
- Checks: `src/investigation/__tests__/round4-race-runs.test.ts` (with the Vegas-shaped fuel vector); `e2e/app/investigation.spec.ts` on `fixtures/round4.ts`, screenshots to `web/target/round4/S2/`.

## Lap rules (orchestrator ruling on audit T7)

Clean = complete, green, not a pit-in or pit-out lap, no incident points, not off track, whatever `comparison_eligible` says. The time-delta zero line is the fastest clean lap of the selection, else the fastest complete timed lap; it is named in the traces header, the delta row and the map readout (`Δ vs L7`). Best three, Fastest, the Clean filter, the fastest-lap magenta and the sector colours use the same rule. The backend's screening shows only in the row's hover (`Not used in Run N pace fit` or `Partial lap`, with its reasons) and, for a screened green lap, the hollow dot in the flag column; the fuel cell holds the figure or `—`. A partial lap (`complete: false`, or the `partial` exclusion when `complete` is absent) is never a baseline. Race marks the same fastest lap from the race view. The spotlight changes stroke and draw order only and offers only laps the active filters keep. Post-run service (`post_run_service`) is not drawn as an in-race stop. A channel request that fails names its row Failed with Retry in the traces header; rows that cannot fit at their floor scroll inside the chart. Sections draws labels and its map cursor only from lap fractions.

| Path | What |
|---|---|
| `ccol.ts` | CCOL decoder and the test encoder |
| `cache.ts`, `load.ts` | LRU cache and the loader (`AnalysisHandle`: `overview()`, `traces()`, `ensureSignals()`) |
| `investigation-model.ts` | typed read of `race_investigation` (schema 2); fails on an incompatible schema or a missing required field |
| `geometry/trace-model.ts` | columns, ranges, smoothing, paths, the lap rules above, sector cells (the server's `sector_times_s`, else the session clock, TimeAtPercent), the calculated tire-wear row (CalculatedTireWear.cs) |
| `geometry/rail-model.ts` | rail groups (each lap under the run that last started), flags, Δ text, status chips, incidents per lap (`x0` included), pit stops (the race view's `pit_stops`, else `runs[].pit_service`) |
| `geometry/map-model.ts`, `geometry/budget.ts` | map projection, ribbons, plausibility rules; catalog budgets and the lap palette |
| `layout-model.ts`, `layout-sets.ts` | trace layout rules (`AnalysisTraceLayouts.cs`); saved layouts New, Copy, Delete (`AnalysisTraceLayoutSets.cs`) |
| `cursor/` | `telemetry-cursor.ts` (chart and map cursor, map readout fields), `track-map.ts`, `race-inspection.ts`, `trace-layout-editor.ts`, `cursor-core.ts` and `cursor-config.ts` (pure parts) |
| `ui/` | Preact components and `investigation.css` (tokens from `gen/theme.css`, race colours falling back to the design README's values, every size rem); `LapRail.tsx` (strict grid, one state model), `PitPictogram.tsx`, `PhaseSelector.tsx`, `TrackPanel.tsx` (map strip), `TracesView.tsx` (full screen, layouts), `RaceView.tsx` (position dots, gain/loss segments, pit and incident tags); `fit.ts` bounds the workspace to the window (`--ri-fit`); `unit.ts` is the root font size for geometry drawn in pixels |
| `fixtures/`, `harness/` | deterministic synthetic analysis (and `round2.ts`, the same race dressed for the round-2 checks), the static g2 router (bare `traces` answers the catalog's default set), the G2 page |

Trace signal ids are the catalog's. The cursor and charts use these catalog source keys when present: `lap_pct` (bin x), `session_time_s` (time delta, sector times), `latitude` and `longitude` (laps on the map). A signal whose catalog unit is `rad` and whose id is `steering` or `steering-wheel-angle` is shown in degrees.
