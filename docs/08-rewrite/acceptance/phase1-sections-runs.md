# Phase 1: Sections comparison picture, run tire pictures, time lost in section headers (2026-10-09)

Backlog #98 (G6), #99 (G8), #100 (G7-lite); context in `../mastery-2026-10-08/GAP-LEDGER.md`. Source commits on `main`: `a05dde2` and `22825a9` (time lost, projection 6), `1a77a27` (tire pictures), `97fafe8` (Sections chart), and the integration commit that follows them (loader hookup, lap-row cells, phone grid, this record). Nothing was pushed, released or run against the live server; the screenshots come from the fixture API (`web/e2e/app/phase1-sections-runs.spec.ts`, synthetic Round-2 race with invented figures), so they show layout and wiring, not Joshua's data.

## What each item shows

**G6, Sections (#98).** Under a section tab: six channels on one chart (speed, gap to the median lap, throttle, brake, road-wheel steering, body slip) over the approach and the section. The approach (the backend's lead-in, the section's length clamped to 3 to 8 % of a lap, stopping at the previous section's start) is drawn dashed left of one boundary line. The fastest and slowest listed laps (the pass pair) are thick on the chart, on the map racing lines and in the lap list; the other four are thinner. The pair's entry speed to the median lap is written beside their lines (`Δ +0.1`, `Δ −3.8`, mph). Each lap row carries Green (laps into the run, `run_age_laps`) and Entry (entry speed to the median lap at the section start, mph; green faster, red slower) beside Tires and Traffic.

**G7-lite, time lost in the headers (#100).** Each section tab carries `-0.31 s · best L17` (the driver's median pass through the section minus his best pass, on lap 17) and `18 % tire energy`. The tabs are ordered worst first.

**G8, Runs (#99).** Each run card that has a measured reading shows the four tires from above with three bars per tire (tread left, in the sim's L/M/R order), coloured by surface temperature on a fixed 120 to 220 °F key, plus `RF 61 % · 28 laps` and `at L28 · 146–206 °F`. Hover lists every tire's bands and pressure.

## Where the data comes from

| Screen | Producer | Projection | Screen |
|---|---|---|---|
| Six channels, approach, pass pair | the traces' CCOL bins (`speed`, `session-time`, `throttle`, `brake`, `steering`, `slip`) | `traces.ccol` and `section_laps` (median lap) served as recorded | `SectionTraces.tsx`, `section-traces-model.ts`; the gap row uses the Traces delta arithmetic, zero at the approach start, plus means behind |
| Green | `race.json laps[].run_age_laps` | passed through | `section-context.ts` `greenText`, `SectionsView.tsx` `LapRow` |
| Entry | the speed bins at the section start, lap less median lap | none (read in the browser from served bins) | `entrySpeedDeltas` |
| `-0.31 s · best L17`, tire energy | `analysis_engine._pace_attribution` (`time_loss`, `tire_energy`, `lap_reference`) | `coach-views` `pace_attribution.rs` keeps up to 32 priorities, sorted by `recoverable_s`; `section_id` is set only when exactly one detected section overlaps at least 50 % of the priority's span; `InvestigationView.pace_attribution`, `PROJECTION_VERSION` 6 | `load.ts` passes the field to `parseInvestigation`, `SectionsView.tsx` `paceFigures` |
| Tire picture | `runs.json runs[].tire_observation` (`remaining_percent`, `surface_temperature_f`, `pressure`), `tire_set_lifecycle` | kept and consumed as served | `runs-model.ts` `runTires`, `RunsView.tsx`, `TireDiagram` in `src/ui/pictograms.tsx` |

## Evidence rules

- No figure is computed by the UI except two differences of served values: the gap row and the entry speed (both from recorded bins, as the Traces delta row already does). The time lost and the tire energy are the Python producer's, passed through coach-views.
- Time lost is a measured section-time difference and uses the derived colour. Tire energy is derived when the producer grades it `slip_weighted`; a `load_only` grade draws in the proxy colour with the label `tire energy proxy`. A null energy is not drawn.
- A header is shown only for a priority attached to exactly one detected section; unattached or unknown ids are hidden. There is no name or position fallback. Detected sections are not official turn numbers.
- Without a median lap the gap row and the entry labels stay empty; the median is never replaced by another reference lap.
- A tire reading belongs to the stop that ended the run (`measurement_note`), so the picture is labelled `at L{last lap of the run}`. A tire or band the server did not send draws as an empty track.
- Python math, evidence labels and the parity corpus were not touched (`git diff 90f863f -- test-data tools/parity iracing-coach` is empty).

## Known limits

- Tire pictures appear only after a stop that measured the tires; a run whose final stop was not measured, and the committed g2 fixture, show no picture. That is correct, not a fault.
- Headers appear only for sections that map uniquely to one producer priority; the tire-energy figure needs a graded producer value. Older projections (before 6) show plain tabs.
- On a phone the Δ bar collapses to nothing and the Traffic cell shows share and side mark only, to make room for Green and Entry; headings are shortened by dropping their units.
- A median lap among the six listed laps (fewer than seven clean laps) leaves the gap row and entry labels empty (item 1 report).
- No per-lap tire-age series on the Race chart (needs a backend field), no I/M/O letters on the tire bars (the meaning is on hover, unavailable by touch), no groove tint or targets.
- Nothing here is checked against a real recording yet; the g2 race has no clean section laps.

## Gates

| Gate | Result |
|---|---|
| `web` vitest (incl. the new loader regression test), typecheck, build | 645 tests passed |
| `node tools/wire/gen-wire.mjs --check` | passed |
| `tools/check.ps1 -PythonPath <codex python>` | all checks passed (repository verification, Rust fmt, clippy, tests, wire and theme drift, web typecheck, tests, build) |
| `tools\check.ps1 -Linux` | passed (Rust checks; Python 1485 tests, 38 skipped) |
| Playwright `phase1-sections-runs`, `sections-r4`, `sections-u3`, `run-tires-g8` after the last CSS change; `investigation` before it, at 1440x900, 1024x768, 390x844 | passed; no horizontal page scroll, no lap-row cell outside the list |
| Parity fixtures | `git diff --exit-code 90f863f -- test-data tools/parity iracing-coach` clean |

## Screenshots

`../../releases/phase1/`, fixture API, three widths.

| View | 1440 | 1024 | 390 |
|---|---|---|---|
| Sections: six channels, approach, pass pair, headers with the two figures, lap rows with Green and Entry | `sections-1440.png` | `sections-1024.png` | `sections-390.png` |
| Runs: tire pictures on two run cards, none on the third | `runs-1440.png` | `runs-1024.png` | `runs-390.png` |
