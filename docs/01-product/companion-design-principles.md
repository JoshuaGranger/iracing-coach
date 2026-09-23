# Design around a racing question

Date: September 22, 2026. Product direction is binding; studies and proposed features are not claims of installed functionality or acceptance of the whole application.

## North star

> I would rather an app that has 5 features that win me more races than an app that has 1000 features, graphics, and charts that don't help me win any more races.

Joshua also requires: **as capability increases, complexity cannot increase from the user's interface or experience.** The result should be simple, powerful and beautiful. These instructions are also recorded in `AGENTS.md` and requirements `PROD-020`, `PROD-021`, `UI-023` and `UI-024` so future development reads them before adding features.

Every proposed feature must answer three questions: which racing decision becomes better, what the driver can do differently, and how a subsequent recording can show whether it helped. A chart that is merely interesting does not pass. Remove, consolidate or defer controls with no defensible role in that outcome. Preserve useful preparation, reliability and safety functions when they enable the racing workflow; do not turn a preference for five effective features into an arbitrary quota.

Acceptance needs a complete path from observation through action to evaluation. Examples include a repeatable reduction in comparable-run pace loss, improved consistency without sacrificing pace, avoiding a fuel mistake, or an experiment that demonstrably improves a relevant driving measurement. Race wins are the ambition, not a causal promise from one recording. Current experiment history and next-recording follow-up remain incomplete.

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

The latest revision and app-wide decisions are in [Visual companion redesign](visual-companion-redesign-2026-09-22.md). It replaces the primary screened straight lines with observed-run medians, labels the cohorts separately, removes shading, and recomputes the confirmed section merge. The prior descriptions below are retained as development history, not current UI acceptance.

### Prior compact revision: one-screen investigation

Joshua rejected the prior study's page-length layout, prose, hidden driving inputs, separate inspection slider and absent map. He also required exact run age for each selected lap and information about slipping/sliding. These corrections are binding requirements `RA-135`–`RA-137` and `UI-025`; a thin global top navigation remains an exploration under `NAV-008`.

The current `compact-investigation.html` replaces that presentation with:

- A 36 px analysis navigation strip and one viewport-bound workspace; no page headline, instructional paragraphs, telemetry accordion or slider.
- The backend's recorded track layout with the selected area, preceding approach, start/finish and synchronized position marker. Numbered detected areas are not official turn numbers.
- All six channels visible together: speed, elapsed time gap, throttle, brake, steering and body sideslip. Pointer motion and touch dragging inspect the charts directly; arrows/Home/End are keyboard equivalents. Map selection and inspection retain the same pair and area.
- Each actual lap's accumulated green exposure beside its identity, not only a general six-lap pool label. Area 4 uses laps 36 and 132 at 22.7 and 23.8 green equivalents since their run starts.
- Exact source sideslip preserved through the backend study projection. It is a chassis velocity angle with a signed peak retained in each distance bin, not tire slip or a grip estimate. Summaries require at least two values and 80% area coverage; the trace retains gaps. No advice to copy the faster pass's input curves is implied.

For this Area 4 pair, the slower pass has lower median absolute body slip (2.782° versus 3.084°), despite arriving about 6 mph slower and losing time. This is an observation about this screened pair; it does not certify less tire sliding, diagnose understeer, or identify the cause. Entry conditions, run age, speed, steering and slip have to be interpreted together.

The six critical channels occupy the available vertical space. Details about method and caveats are behind one information button, while exact lap age remains visible. The left column now holds racing evidence rather than global app navigation. Source changes are restricted to the offline study/backend projection; the installed app and full app shell are unchanged.

Current revision verification: all 1,076 backend tests, 21 contracts and 28 fixtures passed. Three added cases cover exact-lap exposure with signed body-slip values, absent slip without steering-based inference, and sparse slip that must not produce whole-area statistics. JavaScript syntax passed. Browser checks at 1280×720 and 390×844 found all six channels, pair ages, map and area choices within the viewport without document overflow. Direct chart and map dragging synchronized all six cursors/readouts and map position; keyboard Home/End, area selection, both tabs, details dialog and Escape were exercised. Both fitted run lines remain. No console errors were captured. These are standalone-study checks, not installed-app or complete global-navigation acceptance.

### Earlier revisions (superseded presentation)

These are interactive presentation studies, not additions to the released 0.18.0 application. Private derived inputs and generated pages are in the ignored `companion-app/artifacts/companion-design-20260922/` directory. The existing temporary mobile gallery serves the study through its restricted route.

### Compare the runs on a shared scale

**Question:** Did the second run follow the same fade pattern, or did its performance change at comparable green exposure?

Following Joshua's critique, the revised study opens with both runs overlaid on green laps into each run. It removes the redundant race-lap axis toggle; chronology remains the job of race progression. Solid/dashed least-squares trend lines use only screened points and stop at the first/last fitted observation. The underlying dots and exact lap selection remain. The existing comparison of laps 35–37 and 130–132 is about 22–24 green-lap equivalents, with Run 2 slower by 0.270 seconds per lap.

Green exposure and fitting come from backend code, not UI arithmetic. Fuel differs substantially between the windows; identical tire condition, grip, damage state or driving intent cannot be inferred. The fitted pace lines are descriptive, not a tire degradation model or a prediction of an unobserved stint. The study fits the plotted investigation eligibility set; it must not silently relabel these coefficients as a separately calculated run-summary slope.

**Before production:** add an explicit optional per-lap exposure field to the backend projection, preserve missing/gapped exposure, test race and run alignment across cautions/service/partial laps, and integrate the view into the existing investigation with keyboard and touch equivalents. Do not create another top-level analysis section.

### Select the places where time changed

**Question:** Where did the extra time appear, and which driving observations changed there?

The revised study uses the screened similar-exposure comparison directly and removes the finish-comparison selector. The three largest losses are visible; four remaining areas sit under one disclosure. Rank bars retain the two groups' median differences. Selecting an area instead compares the fastest and slowest actual segment instances among those same six laps, not median traces or the globally fastest/slowest whole lap.

Speed and cumulative time difference include the preceding area and approach. One shared inspection position updates all readings; throttle, brake and steering open together under Driving inputs. Timing on entry, added inside, and on exit refer to an explicit context origin. Selecting 62.5–77% compares laps 36 and 132: the latter arrives about 6.0 mph slower, with a 0.121-second gap accumulated since 25% lap distance; another 0.096 seconds accumulates inside. The upstream loss and entry speed make the preceding exit worth inspecting. They do not prove a mechanical or driving cause, or physically allocate the in-area loss to driver inputs within that area.

The first area starts context at the available beginning of the same lap; the study does not invent a previous-lap continuation. Missing approach coverage withholds cumulative time gaps. Missing segment coverage excludes that candidate. Fastest/slowest are limited to the six-lap comparison pool, which is visible. Area medians are not an additive whole-lap breakdown. Percentage bounds remain until verified corner names exist.

**Before production:** integrate optional backend projections into the app contract with fixtures, preserve exact cohort context in rank/trace handoffs, support verified cross-lap approach context, and complete the one-change trial and subsequent-recording check. Do not ship another top-level analysis destination. This study offers evidence for selecting a trial; it does not complete the outcome loop.

### Complexity comparison

| Task | Initial study | Revised study |
| --- | --- | --- |
| Compare run shapes | Choose an axis before the useful overlay | Overlay and fit visible immediately; no axis controls |
| Find comparable loss | Change away from unequal-age finish comparison | Similar exposure already selected; no mode selector |
| Choose a relevant area | Seven equal-priority buttons | Three largest losses, one disclosure for the rest |
| Inspect what preceded the loss | No aligned upstream traces | Select an area once; speed and gap appear together; inputs available in one disclosure |

There are still two study views. The production target is deeper investigation in the existing race workspace, not two new application pages. Additional data must reduce investigative work rather than create a new configuration task.

## Next opportunities beyond these studies

| Driver question | Proposed behavior | Required evidence and failure handling |
|---|---|---|
| Where did the race turn? | Select a visible bend in race progression and inspect the before/after windows with contemporaneous conditions and control changes. | Backend change detection must account for cautions, pits, traffic and missing samples; an event near a bend is not automatically the cause. |
| Was that stop worth it? | Show service cost, observed position recovery and subsequent pace advantage on one post-stop timeline. | Rival/field and strategy context determine whether opportunity cost is knowable. Show observed outcomes when the counterfactual cannot be supported. |
| Did the change I tried help? | Retain one intended change and its success criteria, find a compatible later practice/test recording, and reopen the before/after result. Remember unsuccessful experiments. | Exact car/layout/setup lineage, comparable runs and confounds; insufficient data leaves a pending experiment rather than a verdict. |
| What should I do next session? | Carry one supported driving focus from analysis into a practice test, then check it against the next recording. | Specific observed issue and expected trace change, no invented target inputs. The app must support practice discovery and experiment history for this to work end to end. |

These future opportunities are proposals, not completed features. The north star and September 22 run/segment feedback are explicit user requirements. Favor improvements that remove investigative work from the driver while keeping observations inspectable and the resulting action measurable.

## Study verification

- Existing repository verification: 1,067 backend tests, 21 contracts and 28 fixtures passed before the study work.
- The study reads the latest redacted v19 Bristol investigation. Its 127 plotted laps retain the backend's plotting and comparison-screening decisions.
- Exposure uses the backend `_run_exposure` function; highlighted cohort start/end values were checked against the stored backend comparison within rounding precision.
- The revised study's least-squares lines, segment selection, boundary interpolation and upstream timing are computed in backend `investigation_study.py`, reusing existing backend helpers. Browser work is presentation and selection.
- Six focused tests cover fit eligibility/domain, inherited versus in-area time, segment ranking independently of whole-lap ranking, missing approach, missing segment, incompatible age, and reversed traces. Final repository verification passed 1,073 backend tests, 21 contracts and 28 fixtures.
- Reproducible study builder/template are under `tools/design-studies/`. Input analysis and generated private pages remain ignored. JavaScript syntax, phone layout, exact-lap selection and synchronized trace inspection have separate browser checks. No production UI or contract changed, and no new package was built.
- Browser revision checks passed at 390×844 and 1280×720: all seven area selections, entry/exit inspection with the keyboard slider, synchronized readings/cursors in all five charts, driving-input disclosure, mobile return, exact lap keyboard selection and both overlaid fitted lines. No horizontal overflow or captured console errors were observed. The standalone mobile review page scrolls; this is not proof of the production app's fixed-height workspace requirement.
