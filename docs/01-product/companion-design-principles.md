# Design around a racing question

Date: September 22, 2026. This is a development method and proposal record, not a claim of new installed features or acceptance of the whole application.

Joshua singled out the race progression graphic as the first independently proposed feature that impressed him and asked for more of that kind of invention. Preserve what made it useful: it makes a race understandable and makes the next investigation easier to choose.

## What worked in race progression

The starting problem was a remembered sequence: dominant early, a stop near lap 100, then a large loss of pace. The graphic puts recorded pace, position, cautions, service and incident events on a shared lap axis. A reader can locate the change, see whether a recovery or a repeated fade exists, and choose a comparison without first configuring traces.

The useful design move is joining related observations around one question. Its value is not the number of series, a novel chart shape, or decorative polish. Chronological proximity is visible without pretending it proves causation. Excluded finish laps remain visible because they are part of what the driver experienced; their reference limitations remain identifiable.

## Repeatable development method

1. Write the driver's actual question in ordinary language. Examples: Where did this race turn? Did the second run fade differently? Was that stop worth it? Did the change I tried help?
2. Trace a real case through the app. Identify the comparisons, context and calculations that the user currently has to assemble mentally. The developer should do that work first.
3. Choose a shared comparison frame: race lap for sequence, green exposure for run comparison, distance for driving inputs, or compatible before/after recordings for an experiment.
4. Show the smallest set of observations that changes a decision. Put exact laps, units and material confounds in reach. A metric earns space by helping answer the current question.
5. Make the next investigation a direct action that retains the chosen race, laps, area and reference. Do not send the driver back to a generic toolbox.
6. End with a concrete driving/setup test when the evidence supports one: what to change, what should improve, what should stay comparable, and how to judge the next recording. An uncertain cause can still lead to a useful discriminating test.
7. Evaluate the result on a real recording. Can the driver find the turning point, explain the comparison and identify a next action without reading an essay or reconciling contradictory totals?

Treat copy as part of the reasoning interface. Keep a sentence if it conveys an observation, a relevant limitation, a decision, or an action. Remove generic assurances and prose that merely narrates the UI.

## Two concrete studies using the Bristol recording

These are interactive presentation studies, not additions to the released 0.18.0 application. Private derived inputs and generated pages are in the ignored `companion-app/artifacts/companion-design-20260922/` directory. The existing temporary mobile gallery serves the study through its restricted route.

### Compare the runs on a shared scale

**Question:** Did the second run follow the same fade pattern, or did its performance change at comparable green exposure?

The study switches the same observed lap points between race-lap position and green laps into each run, retaining run colors, screened/excluded point distinctions, exact lap selection and temperature context. It highlights the backend's existing comparison of laps 35–37 and 130–132: about 22–24 green-lap equivalents, with Run 2 slower by 0.270 seconds per lap.

This makes the comparison that was previously a numeric option visible as part of the race's shape. Green exposure comes from the existing backend calculation, not a UI reconstruction of tire wear. Fuel differs substantially between the windows; identical tire condition, grip, damage state or driving intent cannot be inferred. The aligned curves are descriptive, not a fitted degradation model or a prediction of an unobserved stint.

**Before production:** add an explicit optional per-lap exposure field to the backend projection, preserve missing/gapped exposure, test race and run alignment across cautions/service/partial laps, and integrate the view into the existing investigation with keyboard and touch equivalents. Do not create another top-level analysis section.

### Select the places where time changed

**Question:** Where did the extra time appear, and which driving observations changed there?

The study ranks the backend's area-time differences on a common signed scale. Selecting an area updates its entry/minimum/exit speed, brake, throttle and steering measurements together with any existing area-specific driving trial. Switching between the observed finish fade and the screened similar-exposure comparison changes the full set together. On a phone, selecting an area brings its details into view and provides a return to the area list.

This reduces the work of finding the relevant area and reconciling several cards. For the finish comparison, the 62.5–77% area has a 0.322-second median difference, minimum speed falls from about 108.3 to 99.5 mph, and median brake demand rises from 0 to 5.1%. Those are observations; the five finish laps have close-traffic exclusions. Area medians cover only selected portions of the lap and must not be presented as an additive decomposition of the whole-lap median. Until a verified turn mapping exists, do not replace percentage bounds with invented corner names.

**Before production:** preserve exact cohort context in both rank and trace views, handle negative changes and absent inputs, show selected lap rows on evidence handoff, and link accepted official turn names only where provenance exists. The study reuses backend comparison values and actions without introducing new causal rules.

## Next opportunities beyond these studies

| Driver question | Proposed behavior | Required evidence and failure handling |
|---|---|---|
| Where did the race turn? | Select a visible bend in race progression and inspect the before/after windows with contemporaneous conditions and control changes. | Backend change detection must account for cautions, pits, traffic and missing samples; an event near a bend is not automatically the cause. |
| Was that stop worth it? | Show service cost, observed position recovery and subsequent pace advantage on one post-stop timeline. | Rival/field and strategy context determine whether opportunity cost is knowable. Show observed outcomes when the counterfactual cannot be supported. |
| Did the change I tried help? | Retain one intended change and its success criteria, find a compatible later practice/test recording, and reopen the before/after result. Remember unsuccessful experiments. | Exact car/layout/setup lineage, comparable runs and confounds; insufficient data leaves a pending experiment rather than a verdict. |
| What should I do next session? | Carry one supported driving focus from analysis into a practice test, then check it against the next recording. | Specific observed issue and expected trace change, no invented target inputs. The app must support practice discovery and experiment history for this to work end to end. |

These are proposed directions, not newly accepted requirements or completed features. Favor improvements that remove investigative work from the driver while keeping the observations inspectable.

## Study verification

- Existing repository verification: 1,067 backend tests, 21 contracts and 28 fixtures passed before the study work.
- The study reads the latest redacted v19 Bristol investigation. Its 127 plotted laps retain the backend's plotting and comparison-screening decisions.
- Exposure uses the backend `_run_exposure` function; highlighted cohort start/end values were checked against the stored backend comparison within rounding precision.
- Area values, metric pairs and driving trials are copied from the backend projection. Chart position and display formatting are the only browser calculations.
- JavaScript syntax passed. Browser checks covered race/exposure switching, keyboard lap selection, both comparison cohorts, positive and negative area changes, and mobile detail selection at 390×844. There was no horizontal overflow or captured console error. This does not add production application acceptance or a new package version.
