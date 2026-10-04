# Web investigation

Race, Runs, Sections and Traces on one viewport-bound workspace. The shell (W7) mounts one component:

```tsx
import { InvestigationWorkspace, createAnalysisLoader } from "./investigation";

const loader = createAnalysisLoader();            // 3 analyses, 96 MB, in memory
<InvestigationWorkspace
  loader={loader}
  analysisId={analysisId}
  view={last?.view} comparisonId={last?.comparison_id} sectionId={last?.section_id}
  onSelectionChange={(s) => saveLastInvestigation({ analysis_id: analysisId, view: s.view, comparison_id: s.comparisonId, section_id: s.sectionId })}
  traceLayout={activeLayout.body} traceLayoutName={activeLayout.name} onTraceLayoutChange={saveActiveLayout}
  reducedMotion={prefs.reduced_motion}
  sessionPicker={<SessionPicker />}
/>
```

Requests are made by `load.ts` only: the overview (race, runs, investigation, track) when an analysis opens, and the catalog plus CCOL traces when Traces opens. Pointer movement, hover, selection and layout editing make none (`cursor/` is checked for network calls by a unit test; the G2 harness logs requests during a sweep). A loaded analysis stays in the cache and stays inspectable when the server drops. A traces request the server refuses with 413 is repeated for lap ranges (halves, recursively) and the bodies are joined. Bin i lies at lap fraction (i + 0.5) / bins, the backend's `lap_pct`.

| Path | What |
|---|---|
| `ccol.ts` | CCOL decoder and the test encoder |
| `cache.ts`, `load.ts` | LRU cache and the loader (`AnalysisHandle`: `overview()`, `traces()`, `ensureSignals()`) |
| `investigation-model.ts` | typed read of `race_investigation` (schema 2); fails on an incompatible schema or a missing required field |
| `geometry/` | chart geometry as pure functions: `budget.ts` (catalog budgets), `trace-model.ts` (columns, ranges, smoothing, paths, laps), `map-model.ts` (map projection, ribbons, plausibility rules) |
| `cursor/` | `telemetry-cursor.ts` (chart and map cursor), `track-map.ts`, `race-inspection.ts` (object input), `trace-layout-editor.ts`, `context-splitter.ts`, `cursor-core.ts` and `cursor-config.ts` (pure parts) |
| `layout-model.ts` | trace layout rules (`AnalysisTraceLayouts.cs`) |
| `ui/` | Preact components and `investigation.css` (tokens from `gen/theme.css`, every size rem); `fit.ts` bounds the workspace to the window (`--ri-fit`), because the shell's `main` and `page` do not; `unit.ts` is the root font size for the geometry the charts draw in pixels (margins, label offsets, the cursor's value cards), so they follow the driver's text size |
| `fixtures/`, `harness/` | deterministic synthetic analysis, its router, the G2 page |

Trace signal ids are the catalog's. The cursor and charts use these catalog source keys when present: `lap_pct` (bin x), `session_time_s` (time delta), `latitude` and `longitude` (laps on the map). A signal whose catalog unit is `rad` and whose id is `steering` or `steering-wheel-angle` is shown in degrees.
