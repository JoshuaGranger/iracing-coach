# Race Analysis geometry and performance

Status: implemented and regression-tested in the August 2026 development iteration.

This note records the failures behind the Iowa/Daytona map corruption and the
selection-size-dependent cursor lag, the boundaries used to fix them, and the
repeatable measurements that must remain green.

## Root causes

### Corrupted Iowa and Daytona geometry

The telemetry producer accepted finite `Lat=0, Lon=0` values as real GPS
samples. A sentinel near a pit transition then owned the raw-coordinate
normalization extrema. The main racing loop was compressed to a microscopic
island while the sentinel-derived pit entry, pit exit, and line primitives
crossed most of the SVG. The cache ranked these payloads as complete because
the lap-percent coverage and 500-point count were valid; it did not score
geometric plausibility.

Evidence from the supplied simulator corpus:

| Track | Broken raw bounds | Broken main-loop span | Visible consequence |
| --- | --- | ---: | --- |
| Iowa | longitude `-93.01586771..0`, latitude `0..41.67691307` | `0.00005703` normalized | oval plus long blue/white/yellow diagonals |
| Daytona | longitude `-81.07493337..0`, latitude `0..29.19148735` | `0.00015798` normalized | compressed oval and implausible auxiliary lines |

The corrected producer now rejects non-finite coordinates, the `0/0` GPS
sentinel, and isolated samples outside the robust local GPS cluster. Main-loop
bounds alone own normalization. The producer and cache both require a
meaningful normalized span and bounded relative segment lengths. Auxiliary
paths and lines are accepted only when plausible relative to the verified main
loop. The UI applies the same defensive checks, so an old poisoned cache cannot
reintroduce a continental-scale pit line.

Real IBT regeneration, read-only from the supplied corpus, produced:

| Track | Decode + build time | Samples | Main / pit / entry / exit points | Rejected GPS | Result |
| --- | ---: | ---: | --- | ---: | --- |
| Iowa | `54.138 s` | 78,350 | `500 / 180 / 20 / 70` | 1 | usable, plausible, main span `1.0` |
| Daytona | `16.949 s` | 23,439 | `500 / 180 / 20 / 70` | 1 | usable, plausible, main span `1.0` |

Corrected Iowa bounds were longitude `-93.01586069..-93.01055621` and latitude
`41.67246965..41.67689767`. Corrected Daytona bounds were longitude
`-81.07493337..-81.06252042` and latitude `29.17867895..29.19148735`.
The exact two poisoned cache files were also passed through the repaired cache
gate: each published zero main points, zero auxiliary points, zero lines, and
an `unavailable` status, forcing clean regeneration instead of reuse.

### Cursor and large-selection lag

The original chart emitted one SVG path for every selected lap, row, and
signal. Pointer movement was coalesced, but `ResizeObserver` still searched and
mutated individual lap paths. At 500 selected laps and ten rows this created
thousands of DOM nodes and a large amount of browser style/paint work. Tooltip
text was also inside an SVG whose `preserveAspectRatio="none"` transform could
stretch glyphs vertically during a resize.

The corrected architecture keeps all logical laps selected while bounding only
display work:

- traces are compound paths grouped into the fixed 20-color identity palette;
- a signal has a 48,000-vertex background budget, divided across all logical
  traces and capped by screen-pixel density;
- a spotlight trace may use the full screen-pixel budget, preserving the
  focused comparison;
- the synchronized cursor serializes at most 24 representative detailed traces
  at 160 bins, while a separate 160-bin aggregate still includes every selected
  lap;
- resize applies one transform to each bounded render layer rather than finding
  every lap path;
- tooltip cards are ordinary HTML overlays with natural text metrics;
- map traces use the same bounded representative-point policy;
- map cursor radii are inversely scaled with zoom, keeping a constant on-screen
  cursor size.

The render-budget regression uses 500 synthetic laps with 500 points each and
a 1,000 px plot:

| Logical laps | Palette path groups per signal | Detailed cursor traces | Rendered points per background lap |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 500 |
| 3 | 3 | 3 | 500 |
| 20 | 20 | 20 | 500 |
| 82 | 20 | 24 | 500 |
| 500 | 20 | 24 | 96 |

At 500 laps, the background total is therefore exactly 48,000 points per
signal instead of 250,000. Selection, aggregate values, track-spread ribbons,
lap filtering, and the exact focused lap remain logically complete.

## Structural-animation breakthrough

The Customize drawer was expensive because its open/close transition changed
layout width while a blurred surface and thousands of descendant path nodes
were being recomputed. Three changes matter together:

1. Chart paths are grouped and cached before structural motion.
2. Resize work is one animation-frame-owned transform per render layer.
3. The drawer surface animates compositor-friendly transform/opacity/visibility
   and does not use backdrop blur.

`analysis-trace-layout.js` exposes a 50-cycle diagnostic rather than relying on
a single subjective click:

```js
await window.iracingCoachAnalysisTraceLayout.benchmarkStructuralMotion(
  document.querySelector('[data-analysis-trace-studio]'),
  50
)
```

The result reports cycle count, sampled frames, average and maximum frame gap,
frames over 25 ms, width range, and cumulative layout-shift score. Cursor
diagnostics are available from `window.iracingCoachAnalysisCursor`
and report logical trace count, detailed tooltip trace count, aggregate bins,
rendered path nodes/layers, frame count, frames over 25 ms, maximum gap, and
resize callbacks.

For release QA, run the structural diagnostic after selecting 1, 3, 20, 82,
and 500 synthetic laps. Exercise open/close, a row reorder, track hover, chart
hover, zoom, and Fit track. The acceptance target is no clipping or text
stretching, no cursor work proportional to logical lap count, no map cursor
growth under zoom, and no repeated layout-shift accumulation over the 50-cycle
drawer run. The browser frame-gap figures are hardware-dependent and must be
recorded with the app build and display refresh rate rather than treated as a
universal timing constant.

## Regression commands

Backend geometry/cache and replay merge:

```powershell
& 'C:\Program Files\iRacing Coach\python\python.exe' -m unittest `
  'iracing-coach\tests\test_race_foundations.py' -v
```

Race Analysis UI behavior and bounded render work:

```powershell
dotnet test companion-app\tests\iRacingCoach.Tests\iRacingCoach.Tests.csproj `
  --filter "FullyQualifiedName~RaceAnalysisBehaviorTests|FullyQualifiedName~CapabilityRegistryTests"
```

The Python suite includes exact Iowa/Daytona poisoned-cache patterns followed
by clean regeneration for the same configuration keys. The UI suite includes
the explicit 1/3/20/82/500 budget table above.
