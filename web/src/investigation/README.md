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

## Lap rules (orchestrator ruling on audit T7)

Clean = complete, green, not a pit-in or pit-out lap, no incident points, not off track, whatever `comparison_eligible` says. The time-delta zero line is the fastest clean lap of the selection, else the fastest complete timed lap; it is named in the traces header, the delta row and the map readout (`Δ vs L7`). Best three, Fastest, the Clean filter, the fastest-lap magenta and the sector colours use the same rule. The backend's screening shows only as the `Excl` chip with its reasons. A partial lap (`complete: false`, or the `partial` exclusion when `complete` is absent) is never a baseline. Race marks the same fastest lap from the race view. The spotlight changes stroke and draw order only and offers only laps the active filters keep. Post-run service (`post_run_service`) is not drawn as an in-race stop. A channel request that fails names its row Failed with Retry in the traces header; rows that cannot fit at their floor scroll inside the chart. Sections draws labels and its map cursor only from lap fractions.

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
| `fixtures/`, `harness/` | deterministic synthetic analysis, the static g2 router (bare `traces` answers the catalog's default set), the G2 page |

Trace signal ids are the catalog's. The cursor and charts use these catalog source keys when present: `lap_pct` (bin x), `session_time_s` (time delta, sector times), `latitude` and `longitude` (laps on the map). A signal whose catalog unit is `rad` and whose id is `steering` or `steering-wheel-angle` is shown in degrees.
