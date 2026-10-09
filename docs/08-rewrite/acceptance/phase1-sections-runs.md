# Phase 1: Sections comparison picture, run tire pictures, time lost in section headers (2026-10-09)

Backlog #98 (G6), #99 (G8), #100 (G7-lite); context in `../mastery-2026-10-08/GAP-LEDGER.md`. Source commits on `main`: `a05dde2` and `22825a9` (time lost, projection 6), `1a77a27` (tire pictures), `97fafe8` (Sections chart), the integration commit `79bc0b8` (loader hookup, lap-row cells, phone grid, this record), and the review-fix commit that follows it (the Codex review of `79bc0b8`, kept outside the repository:Entry removed, median reference kept, approach marker, boundary split, overlap tolerance). Nothing was pushed, released or run against the live server; the screenshots come from the fixture API (`web/e2e/app/phase1-sections-runs.spec.ts`, synthetic Round-2 race with invented figures), so they show layout and wiring, not Joshua's data.

## What each item shows

**G6, Sections (#98).** Under a section tab: six channels on one chart (speed, gap to the median lap, throttle, brake, road-wheel steering, body slip) over the approach and the section. The approach (the backend's lead-in, the section's length clamped to 3 to 8 % of a lap, stopping at the previous section's start) is drawn dashed left of one boundary line. Dashed and solid meet exactly at the section start, cut on the drawn segment between the two bins around it. With the cursor in the approach, the map marker sits at the same lap distance on the track before the section. The fastest and slowest listed laps (the pass pair) are thick on the chart, on the map racing lines and in the lap list; the other four are thinner. Entry speed reads from the dashed approach against the grey median line; no entry figure is computed in the browser. Each lap row carries Green (laps into the run, `run_age_laps`) beside Tires and Traffic.

**G7-lite, time lost in the headers (#100).** Each section tab carries `-0.31 s · best L17` (the driver's median pass through the section minus his best pass, on lap 17) and `18 % tire energy`. The tabs are ordered worst first.

**G8, Runs (#99).** Each run card that has a measured reading shows the four tires from above with three bars per tire (tread left, in the sim's L/M/R order), coloured by surface temperature on a fixed 120 to 220 °F key, plus `RF 61 % · 28 laps` and `at L28 · 146–206 °F`. Hover lists every tire's bands and pressure.

## Where the data comes from

| Screen | Producer | Projection | Screen |
|---|---|---|---|
| Six channels, approach, pass pair | the traces' CCOL bins (`speed`, `session-time`, `throttle`, `brake`, `steering`, `slip`) | `traces.ccol` and `section_laps` (median lap) served as recorded | `SectionTraces.tsx`, `section-traces-model.ts`; the gap row uses the Traces delta arithmetic, zero at the approach start, plus means behind |
| Green | `race.json laps[].run_age_laps` | passed through | `section-context.ts` `greenText`, `SectionsView.tsx` `LapRow` |
| `-0.31 s · best L17`, tire energy | `analysis_engine._pace_attribution` (`time_loss`, `tire_energy`, `lap_reference`) | `coach-views` `pace_attribution.rs` keeps up to 32 priorities, sorted by `recoverable_s`; `section_id` is set only when exactly one detected section overlaps at least 50 % of the priority's span; `InvestigationView.pace_attribution`, `PROJECTION_VERSION` 6 | `load.ts` passes the field to `parseInvestigation`, `SectionsView.tsx` `paceFigures` |
| Tire picture | `runs.json runs[].tire_observation` (`remaining_percent`, `surface_temperature_f`, `pressure`), `tire_set_lifecycle` | kept and consumed as served | `runs-model.ts` `runTires`, `RunsView.tsx`, `TireDiagram` in `src/ui/pictograms.tsx` |

## Evidence rules

- No figure is computed by the UI except one difference of served values: the gap row (from recorded bins, as the Traces delta row already does). The Entry column and the chart's entry labels of `79bc0b8` were removed after review: they computed telemetry in the browser (against the backend-authority rule), disagreed with the smoothed series drawn, and lost their unit on a phone. A per-lap entry-speed figure belongs to the backend: `race_inspection.comparison_inspection` already measures `entry_speed_difference` per pass pair (with `_sample`'s timing and coverage checks); projecting it per lap is the future path. The time lost and the tire energy are the Python producer's, passed through coach-views.
- Time lost is a measured section-time difference and uses the derived colour. Tire energy is derived when the producer grades it `slip_weighted`; a `load_only` grade draws in the proxy colour with the label `tire energy proxy`. A null energy is not drawn.
- A header is shown only for a priority attached to exactly one detected section (half-overlap test with a 1e-9 lap tolerance, so two equal halves that floating point rounds apart stay ambiguous); unattached or unknown ids are hidden. There is no name or position fallback. Detected sections are not official turn numbers.
- The gap row's reference is the section's reported `median_lap`, also when it is one of the six listed laps (it then keeps its shade and no grey line is drawn). Without a median lap the gap row stays empty; the median is never replaced by another reference lap.
- A tire reading belongs to the stop that ended the run (`measurement_note`), so the picture is labelled `at L{last lap of the run}`. A tire or band the server did not send draws as an empty track.
- Python math, evidence labels and the parity corpus were not touched (`git diff 90f863f -- test-data tools/parity iracing-coach` is empty).

## Known limits

- Tire pictures appear only after a stop that measured the tires; a run whose final stop was not measured, and the committed g2 fixture, show no picture. That is correct, not a fault.
- Headers appear only for sections that map uniquely to one producer priority; the tire-energy figure needs a graded producer value. Older projections (before 6) show plain tabs.
- On a phone the Δ bar is short and the Traffic cell shows share and side mark only, to make room for Green; headings are shortened by dropping their units.
- No per-lap entry-speed figure until the backend projects one (see Evidence rules).
- No per-lap tire-age series on the Race chart (needs a backend field), no I/M/O letters on the tire bars (the meaning is on hover, unavailable by touch), no groove tint or targets.
- Nothing here is checked against a real recording yet; the g2 race has no clean section laps.

## Gates

| Gate | Result |
|---|---|
| `web` vitest (incl. the new loader regression test), typecheck, build | 643 tests passed (the Entry tests removed, gap-reference and boundary-split tests added) |
| `node tools/wire/gen-wire.mjs --check` | passed |
| `tools/check.ps1 -PythonPath <codex python>` | all checks passed (repository verification, Rust fmt, clippy, tests, wire and theme drift, web typecheck, tests, build) |
| `tools\check.ps1 -Linux` | passed (Rust checks; Python 1485 tests, 38 skipped) |
| Playwright `phase1-sections-runs`, `sections-r4`, `sections-u3` after the last CSS change; `run-tires-g8`, `investigation` before it (review fixes), at 1440x900, 1024x768, 390x844 | passed; no horizontal page scroll, no lap-row cell outside the list, the approach marker check |
| Parity fixtures | `git diff --exit-code 90f863f -- test-data tools/parity iracing-coach` clean |

## Screenshots

`../../releases/phase1/`, fixture API, three widths.

| View | 1440 | 1024 | 390 |
|---|---|---|---|
| Sections: six channels, approach, pass pair, headers with the two figures, lap rows with Green | `sections-1440.png` | `sections-1024.png` | `sections-390.png` |
| Runs: tire pictures on two run cards, none on the third | `runs-1440.png` | `runs-1024.png` | `runs-390.png` |

## Released (2026-10-09)

| | |
|---|---|
| Build id live | `iracing-coach:cd3f758cc67f6d35a5ffcc84301b5f9a946e6e58`, container `iracing-coach-app-1` healthy. `/health` is `{"schema_version":3,"server_version":"0.19.0","status":"ok"}`; the workspace version did not change, so the build is told apart by the image tag (`docker ps`) and the stack's `deploys.log` |
| Time (UTC) | `release.sh iracing-coach .` (Git bash, from `main` at `cd3f758`) started 16:05:08Z, exit 0 at 16:08:14Z (build 144 s on the mini PC); `deploys.log`: `start` 16:07:33Z, recovery point `iracing-coach-20261009T160734Z.tar`, `healthy` 16:08:12Z. Sim state at release: in garage, not on track |
| Start-up log | `setup identity unchanged` (72 setups), `offline sessions unchanged` (662 recordings, 310 with the lap counter stopped) |
| `GET /api/v1/status` | `projection_version 6` (was 5), `worker_profile_version post-race-foundations-v26` (unchanged), `queue {analysis 0, deferred 318, light 0}`, `nas.state ok` |

**Regeneration.** The contracts README rule for a `PROJECTION_VERSION` bump applies: a stored view at an older version is re-projected on the first request for it (the request answers 409 `ProjectionPending`, a `project` job runs, the next request is 200). No `reanalyze` was called: the Python profile did not change, so there is nothing to re-analyse. Counted from `GET /api/v1/jobs`: **6 `project` jobs at projection 6, all `succeeded`** (finished by 16:11:14Z), one for each current analysis of the Charlotte sessions 668, 670, 671, 672, 673 and 674, triggered by the smoke below. Every other session's stored views stay at projection 5 until someone first opens them (the Sessions list and overviews are unaffected); the 318 deferred recordings are as before.

### Live smoke (read-only; Playwright Chromium against `http://192.168.1.81:8210`, 1440x900; no console error, no failed response, no horizontal scroll)

- **Races.** Both Charlotte races of 2026-10-06 (session 670, 9:09 PM, `DNF L14`; session 674, 10:09 PM, `DNF L35`) carry `pace_attribution` `{status unavailable, reason no_clean_green_racing_laps, priorities []}`: the producer found no clean green racing lap in either. Their Sections tab therefore shows the tabs A and B without header figures and `0 clean laps timed`, with no channels to draw. That is the rule (Known limits: a header needs a graded priority) and the same empty state the tab had before Phase 1; it is not a fault, but it means **the six-channel picture and the two header figures cannot be seen on either race**.
- **Sections, practice 671 (10-06 9:34 PM, 46 laps, 3 stops; `#/d/1507810/s/671/sections?section=B`).** `pace_attribution` is `usable` with two priorities. Tab headers read `B -0.18 s · best L15 / 50 % tire energy` and `A -0.13 s · best L15 / 50 % tire energy`, worst first. Six channels (Speed mph, Gap to median s, Throttle %, Brake %, Road-wheel steering deg, Body slip deg) over the approach (54.0 to 62.0 %) and the section (62.0 to 84.0 %); the approach is dashed and the boundary line sits at 62.0 %; the legend names Median L37 and Approach; the lap list carries Green (1, 2, 3, 4, 9, 12) beside Tires and Traffic; fastest and slowest three are thick on chart, map and list. `Approach` label and the cursor readout (`73.0%`) match the fixture.
- **Runs, practice 671.** Four run cards, each with a tire picture (`RF 88 % · 12 laps / at L12 · 94-120 °F`, `RR 97 % · 16 laps`, `RF 82 % · 30 laps`, `RF 84 % · 43 laps / at L46 · 89 °F`): all four runs have a stop or post-run-service reading. Session 672 (two-lap practice, run with no reading, `tire_observation` null) shows its run card with no picture (no tire element in the DOM). Session 674 shows pictures on its two listed runs, both with readings.
- **Differences from the fixture screenshots.** Real data: the fixture's Round-2 race has invented figures and a third run without a picture; no live run in 671 lacks one, so the "none" case was checked on 672 instead. The Tires cell of the live lap rows is the lap count into the tire set (31, 32, 33, 16, 9, 42), not the fixture's invented values. The two header figures show `-0.18 s` and `-0.13 s` where the fixture shows invented ones. Tire bars are cool (94 to 120 °F) in 671, so they sit at the cold end of the 120 to 220 °F key. Layout, header order, dashed approach and card grid match the fixture at 1440.
- **Not seen.** 1024 and 390 live pages were not captured (the request was for 1440); no collector, sim or setup data was touched.

Screenshots (live, 1440): `../../releases/phase1/live-sections-1440.png` (practice 671, section B), `../../releases/phase1/live-runs-1440.png` (practice 671).

Nothing was pushed to GitHub.
