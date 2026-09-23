# Visual companion redesign
Date: September 22, 2026 (local). Status: design decision report and revised Bristol study; not an application release.

## Direction

Joshua approved the compact visual investigation and asked for the same approach throughout the app. The standard is now: **show the racing situation, the evidence and the useful action with the least interface necessary**. More capability must reduce the driver's work.

The product is a companion when it remembers the question, preserves context between tools, proposes a supported test and helps evaluate the next recording. Replacing paragraphs with more charts is not sufficient.

This report builds on the [whole-app usefulness audit](../06-reality/whole-app-usefulness-audit-2026-09-22.md) and its 257 control families. Source inspection is not proof that every control works in the installed app.

## Adversarial review

Three scored rounds plus the agenda exchange: **Claude Opus 5.5, xhigh** against **GPT-6 Astra, xhigh**, with Codex orchestrating. The requested Claude model required updating the existing CLI from 2.1.261 to 2.1.280; the debate then ran successfully without a model substitution.

The deterministic driver reports **95.2% final agreement**, up from 87.5% in round one; 13 items covered, 14 challenges resolved and 14 concessions. These statistics describe agreement between the participants, not evidence that the design will help win races.

The strongest revisions were:

- **Do not open the newest race unconditionally.** This user's Bristol case was not necessarily the latest recording. Restore a valid prior investigation or show the session library. Keep a single visible session picker. New arrivals must not silently replace the selection.
- **Show the experienced run before fitting clean-air potential.** A short screened fit ending halfway through Run 2 obscured the driver's actual complaint. Use an observed trend, retain affected laps and keep screened-fit method figures separate.
- **No invented turn boundaries.** Apply the specifically confirmed merge and retain its provenance. A desired count of four is not enough to invent exact turn boundaries.
- **Readable beats crammed.** Desktop should keep map and six critical channels together. Phone review needs usable text and touch targets, even where that requires a modest scroll.
- **A later recording is the next major capability.** The app must remember a trial and reopen the corresponding evidence. A new graph without that continuity has less value.

The most contested initial item was the opening screen; both participants rejected the original newest-race default. Remaining uncertainty concerns live-display priorities, sequencing of selection changes, mobile inspection ergonomics and exact track annotations.

Full transcript and driver tally are retained in the ignored artifact directory:
`companion-app/artifacts/companion-design-20260922/debates/.debate/how-should-iracing-coach-become-a-simple-20260923T040852Z/`.
The generated deliverable is `report.html`. Its long topic is the evidence brief supplied to Claude, whose debate subprocess had tools disabled; this was not a second independent repository or native-app audit.

A convenience copy of the standalone debate report is in `C:/Users/joshu/Downloads/iRacingCoach-visual-design-debate.html`. The debate report itself has not been hosted. In Claude Code, a hosted copy can be published with:

```text
/debate publish "C:/Users/joshu/.codex/worktrees/iracing-coach-development-round-1/iRacing Coach/companion-app/artifacts/companion-design-20260922/debates/.debate/how-should-iracing-coach-become-a-simple-20260923T040852Z"
```

## App-wide changes

These are the recommended production changes. None is claimed complete because it appears in this table.

| Current section | Why it exists | Simplify or remove | Useful visual/action | Acceptance |
| --- | --- | --- | --- | --- |
| Global navigation | Move between distinct racing jobs | Replace the broad rail and repeated breadcrumb with a thin labeled strip. Remove persistent healthy-service chatter. | Sessions · Plan · Setups · Live, plus Settings. Active work has a conditional progress/cancel control. Failures have a recovery path. | Every destination reachable by keyboard; labels legible at supported Windows scaling; navigation starts no foreground backend request. Cancel remains available from another page. |
| Home / race library | Find or resume the relevant recording | Consolidate duplicate recent-race lists and four navigation cards into Sessions. Avoid a second dashboard. | A compact recording list, validated resume and a relevant pending-trial result when supported. Clicking a row opens that exact recording. | Restoring, invalid caches, no recordings, failed analysis and new arrivals are distinct truthful states. Preserve quiet prioritized background analysis. |
| Race Analysis | Explain what changed, where and under what conditions | Remove repeated headings, generic commentary and independent controls that lose the selected context. Keep progression as the starting view. | Progression → overlaid runs → selected track section and synchronized telemetry in the same investigation. Exact laps, run exposure and material confounds stay visible. | The Bristol question is answerable without reconstructing the story from prose or moving through unrelated pages. Reference-excluded finish laps remain visible. |
| Race Planning | Make a fuel and stop decision | Remove duplicated fuel cards, repeated summaries and corner coaching already available in analysis. Keep editable assumptions reachable. | One race-distance/range graphic with supported stop windows and one explicit fuel decision. Selected recording/car/layout carry through. | The driver can state fuel and stop plan without doing arithmetic from the chart. Estimated values and source assumptions are identifiable; unavailable strategy remains unavailable. |
| Setups | Understand and safely reuse a setup | In comparison, suppress identical rows by default. Move source-folder utilities away from the primary action. | Changed and unknown values first, identical count, full values one step away; linked recordings retain their identity. | Missing values never count as equal. Comparison respects units and compatibility. Source/purchased files stay untouched. |
| Progressive Tuning | Turn observed handling into a test | Integrate the workflow beside selected evidence, instead of maintaining another full investigation destination. Avoid repeated car/race/turn selection. | Choose one supported driving/setup trial; retain its metric, area, conditions and expected result. Provide accessible trial history independent of the source recording. | Saved trials reopen and survive restart. Fixed setup restrictions remain. No inference of a target input curve or guaranteed improvement. |
| Live telemetry | Help with an immediate decision while driving | Keep analysis configuration and post-session diagnostics out of the driving display. | Supported fuel/range, flags/gap and a timely relevant cue; detailed telemetry remains available for deliberate inspection. | Real driving QA is required. No accepted redesign claim from replay, screenshots or disconnected fixtures. |
| Settings / connections / diagnostics | Configure once and recover when necessary | Group provider/account detail and routine health here. Remove generic assurances and duplicated statuses. | Concise failure, cause and recovery action. Jobs report a meaningful stage/count; diagnostic export stays safe. | Missing provider credentials do not block deterministic analysis. No endless Working state without observable progress/failure. Configuration and archive portability remain intact. |

The four main labels are a consequence of consolidating jobs, not a quota. Replay, reports, setup copies and advanced evidence can remain useful contextual actions without becoming more primary destinations.

## Bristol critique: changes made in the study

### Comparison identity

Removed the ambiguous `Laps 35–37 / 130–132` header. Runs now has two distinct groups:

- Run 1: **3 laps — 35, 36, 37**, at 21.7–23.7 accumulated green equivalents.
- Run 2: **3 laps — 130, 131, 132**, at 21.8–23.8.

Their median difference remains +0.270 s/lap. Fuel, 15.2 versus 5.7 gallons, is adjacent to that result. Trace headers identify the actual selected run and lap, not merely the pool.

### Chart regions

Removed translucent chart bands. The six compared observations have outer rings and a labeled axis bracket. Telemetry has one aligned entry boundary with Approach and Section labels. Selection is also visible on the track map. These marks describe selection; they are not uncertainty bands or overlapping run envelopes.

### Run trends

The main chart now shows a **centered five-observation median** of recorded lap times, including plotted traffic-affected laps. Edge windows use three or four observations; sequences shorter than three retain dots only. The backend computes it. It is an observed trend, not a clean-air estimate, tire-degradation model or driving target.

Lines stop at supported observations and break across nonconsecutive plotted laps or invalid exposure continuity. No time is invented at green-age zero. Screened OLS coefficients, eligible counts and supported domains remain under Comparison details. This is a deliberate replacement of the primary straight fit, rather than extrapolating it into the fade.

Measured coverage on the real redacted Bristol input:

| Run | Plotted laps | Trend coverage | Continuous plotted lap ranges |
| --- | ---: | ---: | --- |
| 1 | 76 | 74; two isolated dots only | 13–24, 29–44, 50–55, 60–99 |
| 2 | 51 | 49; two isolated dots only | 111–159 |

Run 2's late fade remains visible as a continuous trend. In production, known caution intervals must be distinguishable from unknown data gaps without pooling observations across materially different restart regimes.

### Track sections

Merged the former sections 5–7 into **77.5–86% lap distance**, as Joshua specified. The backend recomputes times across the full interval, including the spaces between the former sections. It reranks the actual passes rather than reusing a previous pair or adding medians.

The merged group-median difference is approximately −0.008 s. Its fastest and slowest actual passes are both in Run 1: laps 35 and 36, approximately 0.0467 s apart. Both run identities now appear beside the traces, because the pool extremes and the Run 2-minus-Run 1 bars answer different questions.

**Not complete:** there are five neutral analysis sections A–E, not a verified four-turn map. The supplied correction establishes one merge; the other exact turn boundaries remain unverified. Bristol's [official seating map](https://www.bristolmotorspeedway.com/documents/bristol-speedway-seating.pdf) is a reference for venue labels, not evidence for precise telemetry-distance splits. A future track annotation must record its source, configuration and verification state. Archived trials must retain their concrete bounds when annotation versions change.

### Desktop and mobile

At 1280×720, the map and all six channels remain simultaneously visible with no document overflow. On phone widths the study now uses 44 px section-selection buttons and larger primary text rather than shrinking everything to force no scrolling. Complete mobile acceptance remains open: some secondary axis/range labels are still 11 px, map label hit areas are small, and sticky inspection context needs further work. This is not a production mobile interface.

## Production sequence

### Gate 1 — one complete investigation

Integrate the revised projection and presentation into the existing application contract and Race Analysis, with the shared compact navigation work needed to make it fit. Keep calculations, eligibility, grouping, exposure and missing-data decisions in the backend. Extend contracts/fixtures and compatibility handling rather than embedding this private case-specific builder in the app.

Native task acceptance:

1. Find the remembered Bristol recording; selection must not drift to the newest event.
2. Locate the late fade on progression and the overlaid run observations.
3. Identify exactly which laps are compared, their run exposure and fuel difference.
4. Select the relevant place on the map and inspect speed, time gap and inputs including the approach.
5. Confirm unavailable or excluded evidence cannot produce a misleading target or causal claim.
6. Switch pages during analysis and cancel from another destination without corrupting the archive.

Use the actual application client area at 100% and 150% Windows scaling, including maximized and smaller windows. A 1280×720 browser viewport does not by itself prove native Windows fit.

### Gate 2 — close the trial loop

Complete practice/test discovery and opening, stable experiment history and source-context handoffs. A saved trial carries its exact recording, setup lineage where relevant, area bounds/version, exposure window, selected change, metric and success criterion.

When a compatible recording arrives, open the measured before/after evidence without asking the driver to reconstruct those choices. Distinguish changed conditions from insufficient evidence. Use **Not comparable** or **Not established** when appropriate. An unsupported Improved/Unchanged badge is not coaching.

### Subsequent app sections

Apply the Plan and Setups designs above with their own task acceptance. In-page simplifications can proceed independently; changes to shared selection/navigation require integration coverage. Live priorities need actual driving evaluation. These gates do not remove the requirement to inspect the remaining application controls and failure states.

## Implementation and verification scope

Changed in this round: offline deterministic study projection, its regression tests, case-specific review annotations, interactive presentation, product requirements and this report.

Not changed: installed 0.18.0 application, production shell/navigation, live monitor, release version or packages. No raw racing data was edited. No production contracts changed, because these functions remain explicitly outside the app contract.

Evidence resides in `companion-app/artifacts/companion-design-20260922/`; generated private inputs and debate transcript stay out of Git. The public review page contains only the same bounded derived racing data already authorized for the temporary gallery.

Repository verification passed: **1,079 backend tests, 21 contracts and 28 fixtures**. The 12 focused study tests include traffic retention, trend edge/domain behavior, missing-lap and run isolation, full merged-interval timing, trace gaps and slip coverage. JavaScript syntax passed. Browser checks exercised both views, merged-section selection, exact run/lap labels, six synchronized cursors and map position with keyboard End, details and Escape. At 1280×720, all six channels fit with zero document overflow and no shaded chart regions; the run plot rings exactly six comparison laps. At 390×844, Runs fits the viewport; Time loss uses readable primary labels and 44 px list buttons with a short vertical scroll. Native application and complete mobile usability acceptance remain open.
