# Implementation Snapshot: 0.11.1

Status: targeted live-rendering development candidate. HOME_QA has not accepted real SDK cadence or live-race usefulness.

## Material change from 0.11.0

- The SDK reader is polled every 8 ms so a native 60 Hz stream is not reduced by the prior 100 ms polling ceiling.
- Every captured native frame is published through a dedicated high-rate trace event. Full-history snapshots are copied only at a slower bounded cadence to avoid turning 60 Hz capture into large repeated allocations.
- The full-page speed, throttle, brake, and steering trace is a canvas renderer driven by `requestAnimationFrame`, rather than an SVG path rebuilt by the Blazor page at 4 Hz.
- Time-window scrolling continues between incoming frames. Screen-pixel buckets retain both minima and maxima so short control spikes survive downsampling.
- Map, numeric, layout, navigation, tray, and status surfaces retain slower bounded updates; high-rate painting does not require a full application rerender.
- Reduced-motion mode still paints incoming telemetry but disables between-sample coasting.

## Verification at snapshot creation

- Release build: zero warnings and zero errors.
- .NET: 74 passed.
- Python/handoff baseline: 173 passed; handoff verifier passed before implementation.
- JavaScript syntax check: passed with the bundled Node runtime.
- Accelerated deterministic replay delivered an effective 60 frame-per-second input to the chart. Native Windows review confirmed the canvas, chart labels, four traces, track map, controls, and full-session lap chart remained responsive.
- Five-second open-chart sample: approximately 18.4% of one CPU core, 223.2 MB working set, and no working-set growth during the sample.

The replay proves the rendering path and cadence contract, not real shared-memory acceptance. HOME_QA remains responsible for confirming actual iRacing 60 Hz behavior on the racing PC.
