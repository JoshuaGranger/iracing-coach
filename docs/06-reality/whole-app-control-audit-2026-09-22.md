# iRacing Coach: whole-application usefulness and control audit

Date: September 22, 2026. Source baseline: `7e9a356dec0fe659b04ff93b81c6c24d36f8dbd3`, development 0.17.7 plus unreleased Bristol trend correction. This is an independent source and workflow audit. It is **not** a claim that every control has been clicked in the installed app. Product development in parallel must be verified separately.

**Historical source baseline:** This full control-by-control rationale preserves the initial audit and its development notes. Wording such as required, pending or proposed below refers to that audit stage. The [current findings/status](whole-app-usefulness-audit-2026-09-22.md), [acceptance coverage](whole-app-acceptance-coverage-2026-09-22.md) and [0.18 snapshot](implementation-snapshot-0.18.0-development.md) supersede its implementation/test status. No new test count or runtime pass is established by making this audit durable. Source line numbers and fingerprints describe the captured baseline and can differ from current files.

## The product problem

The application has substantial acquisition, charting, storage, and setup safeguards. It asks the driver to supply too much of the investigative reasoning. A driver who remembers “I was fast, pitted around 100, and then lost the car” should not need to discover the correct channels, distinguish tire age from laps since caution, locate useful comparisons, and reconcile several separate categories before getting an answer.

The useful sequence is: **what changed → where it changed → which explanations fit the data → what I should try → how the next run will test it.** Each conclusion needs the actual laps and measurements, a way to inspect them, and a clear distinction between an observation, a likely explanation, and a controlled experiment. That is the standard applied below.

## Inventory and scope

The baseline source inventory contains **257 control declarations and gesture families** across 25 product files. This count includes repeated declarations (for example, two Back buttons in mutually exclusive layouts), native popout controls, system-tray actions, and native HTML disclosures. It is not 257 distinct business functions. A generated lap/turn/widget button can have many instances. The detailed matrix below groups equivalent instances by behavior, and the [machine-readable declaration inventory](whole-app-control-inventory-2026-09-22.json) retains exact source lines and SHA-256 fingerprints. The [declaration appendix](whole-app-control-inventory-2026-09-22.md) provides clickable source references.

| Area | Source declarations / families |
|---|---:|
| Home | 7 |
| Navigation, shell and jobs | 14 |
| First run | 13 |
| Live dashboard, layout editor and driving traces | 34 |
| Native popout and system tray | 14 |
| Race browser and workspace tabs | 14 |
| Lap/trace workspace | 34 |
| Technical data | 10 |
| Race replay | 10 |
| Planning | 12 |
| Setup library and Starting Tune | 27 |
| Progressive Tuning, turn map and feedback | 39 |
| Settings, Connections and diagnostics | 28 |
| Unmounted RaceCardPanel evidence disclosure | 1 |
| **Total** | **257** |

The audit also reviewed the dynamic signal catalogs: trace options include track usage, wetness, conditions, suspension, wheel, race-state and control channels; live widgets include fuel range, gaps, cues, repair workload and pace ranges. Having a channel in a toolbox is not equivalent to using it in a diagnosis.

## Highest-priority findings

| ID | Finding and impact | Concrete repair | Source evidence |
|---|---|---|---|
| U01 | The analysis opens on telemetry and best-lap comparisons. The user's race question has no entry point, saved investigation, or answer-to-proof navigation. | Make a race review lead with the race's biggest changes, compatible comparison windows, measured input/condition changes, and a next-session test. Keep traces one action away. | AnalysisWorkspacePage:6–8, 201–224; TelemetryWorkspace:347, 893 |
| U02 | Optional coaching is advertised and connected, but no rendered Razor control invokes `AskRaceCoachAsync`, binds `CoachQuestion`, or renders `CoachAnswer`. | Connect question, progress, cancel, answer and follow-up states to the current race identity. Numeric packet remains authoritative. Offline investigation must still work. | NavRail:27; ConnectionsPage:9–23; CompanionState:2654–2730; source-wide call-site search |
| U03 | Some UI verdicts imply unsupported racing judgments. More stops than the minimum fuel stops becomes “One extra stop”; tire remaining >=70% becomes “Strong margin.” | State measurements neutrally unless the backend actually evaluates the decision. Fuel feasibility is not optimal pit strategy; remaining tread is not proven useful pace life. | RaceTechnicalData:492–507 |
| U04 | Track usage exists as a trace signal but is absent from the investigation story. Conditions can change within a run and between runs. | Compare recorded usage, wetness and temperature in the same lap windows; explain when usage is only session metadata rather than a measured time series. State whether the recording can discriminate track change from driving/tire change. | AnalysisTraceLayouts:78–90; Bristol case study in docs/06-reality |
| U05 | “Open [artifact]” discards job identity and navigates to generic analysis. Non-session Retry tells the user to find the original page. | Pass the actual job/result context to its opener; restore the correct result or offer the actual retry operation. | JobTray:21–23; CompanionState:3007–3036 |
| U06 | “New setup” opens a source-selection worksheet, not a newly created simulator setup. The final Open tuning clears setup context. | Name the deliverable “Find a baseline.” Hand off only to a fingerprint-matched setup and linked recording; otherwise open the recording review step. | SetupPage:12, 124; StartingTunePage:96–99, 163–167; CompanionState:268–286 |
| U07 | First-run detection can fail, instructing the user to correct Settings, while the navigation rail is hidden and that page has no Settings recovery control. | Put a folder-correction action in the failed detection state. | FirstRunPage:12–18; CompanionShell:8; CompanionState:2492–2505 |
| U08 | Tuning has durable backend experiment/outcome data but the visible page presents the current result only. No searchable experiment history makes prior failures hard to learn from. | Add a history for the chosen car/layout/setup lineage with tried change, result, matching limitations and reopen action. | TuningPage:132–185; docs PT-046, PT-063 |
| U09 | Starting Tune asks for five clean laps, but Race Analysis surfaces completed Race events and qualifying siblings. A useful practice/test baseline may be hard to discover through the prescribed path. | Ensure recorded practice/test sessions have an explicit browse/open route and can be linked to the baseline; do not require a full race to evaluate a setup experiment. | StartingTunePage:96; AnalysisPage:54; AnalysisWorkspacePage:223; docs PT-001, PT-060 |
| U10 | The clean-lap filter and coaching eligibility are not the same. A chart selection can look clean while comparison evidence is excluded for traffic/repair context. | Use clear filter names and expose the actual exclusion reason. Analysis selections should be deliberate about observed versus eligible laps. | TelemetryWorkspace:160–161; existing Bristol case study |
| U11 | First-run and service connection copy occupies more attention than the first useful result. “Evidence,” “local,” “safe,” “supported,” “baseline,” “workflow” recur without telling the driver what to do. | Remove invariant assurances from routine racing views. Keep specific limitations next to affected claims, and use verbs that describe the actual result. | FirstRunPage, HomePage, SettingsPage, technical fallback strings |
| U12 | Attractive replay and layout customization are disconnected from the driver's question. A fastest-lap ghost is often the wrong comparison for late-run fade. | Accept investigation lap/window links, seek directly to the loss/traffic event, and compare a context-matched reference. Retain fastest as an explicit option. | RaceReplayWorkspace:175–183; TelemetryWorkspace:347, 893 |

## Home and navigation

Source: `HomePage.razor`, `NavRail.razor`, `CompanionShell.razor`, `JobTray.razor`; exact declarations are in the inventory appendix.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Greeting / Race desk / introductory sentence | Time-based greeting plus assertion recordings appear automatically. | Low information. Replace the dominant heading with recent racing work or a concrete pending action. Avoid “we are ready” narration. |
| Open live telemetry | Calls SetLiveMonitorVisible(true), opening native always-on-top popout. | Useful, but the same words as the Live telemetry navigation destination imply a page. Label “Open telemetry popout.” |
| Review a race | Navigates to analysis browser, no backend work itself. | Keep. The browser should lead to a useful race explanation, with analysis status clear. |
| Plan the next race | Opens planning when capability visible. | Keep; should carry current car/layout/reference when coming from a review. |
| Open your setup library | Opens setup source inventory. | Keep while it supports a real compare/tune decision. Copy should be “Compare setups.” |
| Improve a setup | Opens current tuning context when visible. | Keep only with understandable prerequisites. Without a selection, offer the relevant analyzed run, not arbitrary last state. |
| Recent race row | Opens selected race and waits for its analysis. Displays result, green/caution laps, fastest, slope, fuel. | Useful entry point. Whole-race slope alone obscures different runs and a late collapse. Surface the strongest measured finding and failed/pending status. |
| Loading / empty / analysis failed states | Loading prose, no races prose, or troubleshooting link. | Must distinguish discovery failure, no race recording and analysis still running. Show actual recovery action. |
| Brand / six section links / Settings | Navigation to named page; capability gating hides some links. | Necessary, but source structure should not dictate the user's workflow. Cross-links should preserve race identity. |
| Collapse navigation | Changes RailCollapsed. | Useful at smaller widths. Native focus, reflow and persistence remain to test. |
| Race analysis / Garage61 / AI service statuses | Passive status rows. | Show errors relevant to the current task. A connection lamp is not a feature; AI connection especially needs reachable coaching. |
| Header troubleshooting alert | Opens Settings with diagnostics expanded. | Keep; short issue text and concrete repair preferable to “Needs attention.” |
| Error-boundary Retry view | Resets current view rendering. | Necessary recovery; it may not cure bad input. Preserve failure reference and offer recovery without losing selection. |
| Copy support details | Copies contained error details. | Keep, with credential redaction verified. |
| Return home | Escapes failed view. | Keep; does not resolve the fault and must not be counted as a pass. |
| Toast dismiss | Clears transient status. | Keep; failures needing action must not rely only on transient toast. |
| Job tray expand/collapse | Shows jobs, status and actions. | Necessary for long work, but “Working” is too weak. Expose stage, current recording, elapsed time and cancellation when known. |
| Cancel job | Cancels registered job token. | Keep; verify disposable worker ends, no damaged archive, and later retry works. |
| Retry job | Only session-key jobs actually rerun; others show toast. | Repair U05; a button must perform the retry it names. |
| Open result/artifact | Baseline action routes to generic analysis. | Repair U05; open the exact completed race/result. |
| Clear finished | Removes complete/cancelled/failed jobs from tray. | Useful housekeeping; must not erase durable results or diagnostics. |

## Live driving, dashboards and native popout

Source: `LiveTelemetryPage.razor`, `LiveTelemetryLayoutGrid.razor`, `LiveTelemetryVisuals.razor`, `LiveTelemetryCatalog.cs`, `LiveMonitorWindow.xaml(.cs)`, `MainWindow.xaml.cs`.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Dashboard selector | Selects factory/custom layout shared with popout. | Keep. Factory choices should answer driving tasks (fuel/traffic/technique) rather than show every possible value. |
| Customize / Done | Enters or exits layout editing. | Keep subordinate. It consumes considerable product complexity compared with missing diagnostic help. |
| Toolbox open/close | Reveals layout/widget editor. | Keep; keyboard focus and small-window clipping need exact native verification. |
| Undo / Redo | Applies layout snapshots during editor lifetime. | Useful safeguard; popout-side layout changes invalidate history. Explain that only when it happens. |
| Open/hide popout | Toggles native telemetry monitor. | Keep; one clear name across Home, dashboard and tray. |
| Select tile / drag handle / keyboard move | Chooses and moves a tile, with Alt arrows. | Keep; verify no overlap, focus remains, pointer and keyboard produce identical durable layout. |
| Resize tile / Shift arrows | Changes tile grid span. | Useful, same checks as move; cannot be judged from screenshots alone. |
| Tile quick settings open/close | Reveals display style, color, history, ABS option. | Keep but duplicated with inspector; same settings must stay synchronized. |
| Widget style | Selects allowed numeric/status/gauge/trend modes per metric. | Useful only where the visual answers a driving decision; a repair workload trend is not a completed repair claim. |
| Color | Stores accent for applicable styles. | Low-value customization; keep out of primary workflow. Colors must remain readable and not imply performance grades. |
| Unit / decimals | Changes display conversion and precision. | Useful; precision should not imply source accuracy. Confirm settings also apply to popout. |
| History duration | Selects 15/30/60 seconds or one/three laps, per supported widget. | Useful for recent behavior; missing samples must remain gaps. |
| ABS highlight | Highlights explicit ABS state; disabled if absent. | Keep. Disabled reason must be visible/focusable, not only hover. |
| Remove widget | Removes selected tile; also Delete key. | Keep reversible via Undo. Verify remove last tile gives a clear add path. |
| Rename / columns / rows | Renames custom layout; changes 1–8 grid dimensions. | Keep with overflow prevention. Explain automatic custom copy when editing a factory layout. |
| New / Duplicate / Reset / Delete layout | Creates or copies layouts; reset/delete disabled for factory layout. | Useful secondary tools. Verify delete current layout selects a valid replacement and never mutates factory defaults. |
| Search widget catalog | Filters by metric name/source label. | Useful; “source” is a technical search affordance, not primary racing terminology. |
| Add/drag metric | Places dynamic catalog choice in available cell. | Must explain no room and missing source values. Do not fill dashboard with permanent unavailable shells. |
| Live cue | Displays PrimaryCue.Message/Basis, subject to safe-glance policy. | This is core companion value. Cue must tell driver action and trigger, with stale/connectivity truth. Broad real-session correctness remains untested. |
| Fuel remaining / fuel laps / pit window | Displays source and deterministic forecasts. | Core. Distinguish hard fuel deadline from strategic tire-stop window; verify uncertainty changes after caution/service. |
| Position / leader/ahead/behind gaps / laps | Displays recorded race situation. | Core; gaps must use actual timing evidence and handle class/overall identity correctly. |
| Speed / gear / RPM / pedals / steering / accelerations | Instrumentation and control history. | Useful in practice; secondary while racing unless tied to a chosen technique exercise. |
| Tire phase / pace range | Supported derived phase/target views. | Potentially useful; do not claim opponent/ideal targets without data. When absent, prefer actionable setup of a reference. |
| Mandatory/optional repair workload | Current recorded repair fields. | Useful for pit decision, but workload is not proof of when damage began or total loss. |
| Air/track temperature / bias / pit-road state | Recorded context. | Useful subordinate conditions. Track usage should be considered in post-session comparison even when absent live. |
| Driving traces disclosure | Mounts/unmounts chart after transition. | Useful practice tool; auto collapse while racing may be preferable. Motion/timing must not disrupt telemetry sampling. |
| Trace history laps 1–10 | Persists LiveMonitor.HistoryLaps. | Useful; verify reconnect and new session reset old traces correctly. |
| Popout layout selector | Switches shared layout. | Keep. Active live proof deferred by user; recordings cannot establish live input/render cadence. |
| Return to full app / popout close | Native return/hide actions. | Keep; close tooltip currently says return to full app, potentially muddling two actions. |
| Popout size panel / 80%,100%,125% / close | Sets scaling preset. | Useful; exact monitor DPI, restored/maximized placement and off-screen recovery remain untested. |
| Popout drag / resize / global hotkey | Native movement, monitor geometry and show/hide. | Necessary for racing rig; verify focus stays with simulator when appropriate. |
| System tray Open / Show popout / Settings | Restores relevant window/context. | Keep; minimize/close-to-tray state must be predictable. |
| System tray Pause/resume live coaching | Toggles cue generation pause. | Useful. Main dashboard needs obvious paused state so silence is understood. |
| System tray Exit | Terminates app after host shutdown path. | Keep; verify retained recordings and write completion. |

## Race discovery and analysis workspace

Source: `AnalysisPage.razor`, `AnalysisWorkspacePage.razor`, `TelemetryWorkspace.razor`, trace layout scripts/catalog.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Search races | Matches track, car, series. | Useful. Also needs meaningful date/setup/status discovery for remembered races. |
| Race filter / Clear filters | Applies available history filter. | Keep; no-result and no-recording are distinct. |
| Race/event row | Opens selected event's race analysis. | Core; candidate identification should make recent open Bristol vs fixed/damaged runs easy. |
| Analysis Retry | Reanalyzes failed selected recording. | Necessary; failed status must retain cause and progress must identify operation. |
| Back to races | Closes workspace. | Keep search/scroll/selection to avoid repeatedly finding the same race. |
| Race/Qualifying switch | Analyzes linked event phase. | Useful, but practice/test must have an explicit useful route for tuning. |
| Telemetry/Technical/Replay tabs | Switches source views; resets to telemetry on analysis identity change. | Useful expert tools; first view should answer what happened. U01/U12. |
| Grades disclosure (non-race) | Shows category grade, explanation and improvement. | Calibration gaps mean grading must not dominate numerical evidence. Helpful actions matter more than decorative scores. |
| Track position toggle | Shows map context panel. | Keep; should open automatically for a location-specific finding. |
| Laps & runs toggle | Shows lap rail. | Keep; matching selected windows must remain visible even with rail collapsed. |
| Track color Type selector | Colors trace by selected signal/mode. | Useful proof view. Legend/range and signal availability must be explicit. |
| Fit track / pan / zoom | Changes map viewport. | Keep. Exact-configuration geometry only; do not fake groove or line. |
| Context splitter | Resizes map/lap context, keyboard accessible separator. | Useful secondary control; fit at minimum window size and no drag jitter. |
| Green laps checkbox | Filters flag context. | Keep; green does not mean traffic-free or repair-free. |
| Clean laps checkbox | Filters current comparable trace predicate plus incidents. | U10: name/semantics must match actual exclusions; observed late fade must remain inspectable even if not coaching-eligible. |
| Run disclosure / details tooltip | Opens lap group and early/late/fuel/tire summary. | Useful grouping; show exact eligible lap endpoints rather than vague Early/Late. |
| Lap row/select button | Adds/removes lap comparison selection. | Core. Support evidence-linked multi-lap windows and aligned age, not just arbitrary individual overlays. |
| Incident badge | Shows recorded counter event details. | Keep; no badge is not “no damage.” Zero-point events require explicit source. |
| Pit entry/exit popover | Reveals service/lap context. | Useful; link directly from timeline/pace finding to this stop. |
| Condition/popover hover targets | Reveal per-lap supporting conditions. | Helpful but too hidden for causal assessment; selected comparison must surface them automatically. |
| Fastest | Selects fastest eligible lap. | Useful technique reference, weak tire-degradation comparator. Label reference context. |
| Best three | Selects three quick laps. | Useful for consistency, not default answer to every question. Replace diagnostic default with evidence-selected comparable windows. |
| Clear / Show all | Clears selection or lap filters. | Keep; clear must leave usable empty instructions, not error. “Show all” should specify whether laps or chart traces. |
| Spotlight menu / lap / Clear | Emphasizes one selected lap while keeping comparison traces. | Useful visual aid; keyboard/focus/selection interplay needs native tests. |
| Trace customize / close | Opens chart editor. | Keep secondary to investigation. A driver should not need it to discover Track usage. |
| Full screen / Esc | Expands trace pane and restores focus. | Useful; test chart resizing and exit restoration. |
| Layout selector / New / Copy / Delete / Rename | Durable global analysis layouts, protected factory set. | Useful expert customization. Cross-race carryover is intentional; lost finding context is not. |
| Signal search | Finds channels by names. | Useful. Explain channels in racing language; “Slip angle” must not silently imply tire slip. |
| Add signal / drag to new row or existing slot | Adds one of 74 catalog signals; max ten rows. | Useful proof mechanism; unavailable signals need why, not misleading zero. |
| Remove signal / chart | Removes trace or chart, with minimum-row protection. | Keep; test paired-row semantics and focus. |
| Chart reorder / resize / keyboard | Arranges rows and two-signal comparison. | Useful but subordinate. Existing 1426px evidence is not proof at 1280×720 or scaled monitor. |
| Linked chart cursor / map cursor | Shows aligned point-by-point values. | Core proof. Add measured corner-phase summary and selected delta so user does not estimate differences visually. |
| Time delta | Relative to selected clean fastest trace, fallback primary. | Useful only with visible reference identity. Arbitrary fastest reference can confound late-run advice. |
| Track usage / wetness / temperatures / weather signals | Available catalog channels. | U04: display matched-window measurements and quality; a toolbox option is not an analysis conclusion. |
| Calculated tire wear | Derived distribution, not continuous measured tire wear. | Needs plain, always-accessible explanation of estimate and endpoint constraints. Do not let appearance imply measured per-corner wear. |

## Technical data and replay

Source: `RaceTechnicalData.razor`, `RaceReplayWorkspace.razor`.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Pit strategy card | Opens pit metrics, service/tire decisions and recorded outcomes. | Keep, but U03 fuel-stop minimum is not strategy quality. Need alternatives only when timing supports them. |
| Tire management card | Opens measured snapshots and predicted outlook. | Core. Separate observed endpoint wear, pace fade and model estimate; do not assert remaining pace life from tread alone. |
| Fuel management card | Opens burn, all-green range, reserve and stop feasibility. | Core. Clear enough to make a plan; include original comparable laps and assumptions. |
| Racecraft & pace card | Opens run curves, metrics and Garage61 references. | Core, currently category-based. Must connect time loss to corner/input/condition evidence and reasonable next action. |
| Back from detail | Returns overview. | Keep; preserve chosen run/stop for continued investigation. |
| Pit stop selector | Selects exact service episode. | Useful. Show stop lap and changed tires; link to affected pre/post windows and replay. |
| Tire snapshot selector | Chooses stop's physical tire reading. | Useful. Show age/run and whether changed/kept; missing bands remain missing. |
| Pace run selector | Selects a run's trend. | Useful. Exact early/late lap samples and exclusions are required; observed/confounded slowdown must not disappear. |
| Find/Refresh Garage61 laps | Requests authorized reference sync, with busy state. | Optional useful reference. Driver scope, conditions and alignment limits matter; do not imply global target pace. |
| “Next lap” findings | Renders backend metric Action prose. | Often wrong temporal framing for pit/setup/next-session advice. Use specific action timing and trigger. Generic exhortations should be removed. |
| Unavailable cards | Explain missing source or model. | Keep only if the driver can take a useful action. Repeated “No supported finding” blocks should collapse to a concise limitation. |
| Replay unavailable state | Shows reason; no recovery control in component. | Link to analysis/data recovery when possible; source absence must not be presented as temporary loading. |
| Leader lap history | Seeks to leader lap using retained replay timing. | Useful race context. Driver-selected analysis lap needs its own direct seek path. |
| Play/Pause | Drives recorded clock/rendering. | Useful; should stop when leaving tab and honor missing intervals. |
| Previous/Next lap | Seeks by current player lap. | Useful; clarify distinction from leader-lap list. |
| Scrubber | Seeks along flag-colored timeline. | Useful; keyboard granularity and absent-data intervals must remain truthful. |
| Event marker / event list button | Seeks to recorded event time. | Core explanation support. Pace-loss onset and service comparison should use the same path. |
| Speed selector | Changes replay playback rate. | Keep; high speed must not overrun UI or misalign traces. |
| Fastest reference toggle | Overlays fastest clean recorded lap. | Keep explicit option; also offer investigation-matched reference with lap age/context. |
| ABS checkbox | Highlights explicitly recorded intervention. | Keep; relevant only to cars/channels that record it. |
| Track/field markers / running-order table | Read-only participant locations/order. | Useful for traffic interpretation. Cannot show competitor controls, tires, damage or exact groove without recorded evidence. |
| Leader vs you comparison | Shows available comparative race state. | Useful proof for “dominated” claim; own pace alone cannot establish relative dominance. |

## Planning

Source: `PlanningPage.razor`, planning state in `CompanionState.cs`.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Car / exact track-layout / Fixed-Open | Chooses event and auto-selects matching recorded reference. | Necessary. Show reference change clearly; changing context must invalidate stale result. |
| Distance / laps-minutes | Required numerical race length; uses declared reference limits when appropriate. | Core. Timed events need pace assumption and overrun rule visible. |
| Based on recording / practice estimates | Chooses evidence source or manual estimate. | Core. Filtered reference must match car/layout/setup and date; don’t silently treat practice estimate as measured. |
| Fuel assumptions disclosure | Opens burn/start-fuel/lap-time overrides. | Useful; list exactly which values override recording in result. |
| Green fuel/lap | Sets manual burn. | Useful for practice preparation; unit and plausibility validation required. |
| Starting fuel | Sets usable starting amount. | Useful; no hidden inference that tank capacity equals race start fuel or event limit. |
| Representative lap | Sets timed-race pace. | Useful; longer race estimate and reserve sensitivity should be visible. |
| Build plan | Calls deterministic planning, shows busy state. | Core. The headline must call this a fuel plan unless tire/traffic/pit-loss optimization is actually supported. |
| Planning assumptions disclosure | Shows calculation conditions. | Core trust detail; critical assumptions should also appear beside decision. |
| Opening plan / equal-stint targets / range | Displays backend fuel feasibility and balanced stops. | Useful, but balanced fuel stops are not automatically recommended optimal strategy. Label accordingly. |
| Priorities / tires / response triggers | Displays selected backend actions after text-based suppression. | Potential companion value. Replace generic habits with measured issue → action → trigger → expected verification. Text substring suppression is brittle. |
| Comparable run cards | Static history of matching runs. | Useful context but no opener or selection; link to actual race and the matching evidence window. |
| Carry into next session | Not present as a visible action. | Missing journey: save the selected preparation focus and offer it in the live cue/practice review, with user control. |

## Setups and Starting Tune

Source: `SetupPage.razor`, `StartingTunePage.razor`, setup state. Documentation has a contradiction: SU-002/0.11.0 says no browse/library screen, while current source intentionally has a useful linked library. Resolve to current user intent rather than obeying stale removal prose.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| New setup | Opens existing-source finder and worksheet. | Misleading U06: rename Find a baseline. Does not create a loadable STO. |
| Setup search / car / track / season filters | Filters local sources and recorded setup values. | Useful. Exact configuration and fingerprint still decide compatibility, not display labels. |
| Clear filters | Resets all filters. | Useful for no matches, pointless when library truly empty; empty state should offer baseline/recording actions. |
| Setup row | Selects setup; resets comparison. | Core. Distinguish opaque STO from recorded parameters, date and source identity. |
| Tune this setup | Chooses linked recording and verifies setup fingerprint before tuning. | Useful controlled handoff. Disabled state should say what is missing; multiple linked recordings should be selectable. |
| Show source | Opens Explorer selecting source path. | Useful provenance/recovery; label Open source file location. Not a substitute for readable setup data. |
| Save a copy | Copies existing STO unchanged. | Useful protection; label Save setup copy, show destination/result and duplicate behavior. |
| Linked performance Analyze | Opens exact linked race. | Core. Performance is not setup causation; compare matched conditions and driver context. |
| Open Race Analysis (no linked source) | Routes to generic browser. | Needed but should prefilter current car/track/setup when feasible. |
| Compare with selector | Shows same car/track setup parameter differences. | Useful decision tool, but readable differences need units, same/different grouping and setup-purpose context. |
| Setup group disclosures | Shows numeric settings by system. | Useful; source field values preserved, unknown fields not silently lost. |
| Back from Starting Tune | Returns setup library. | Keep draft and selected source. |
| Event/Source/Checks/Run step rail | Changes worksheet stage after package exists. | Useful only if stages produce a real driving action and subsequent comparison; four screens should not add ceremony. |
| Race/Qualifying | Chooses intended source purpose. | Necessary; race source cannot silently become qualifying source. |
| Car/track comboboxes | Selects local catalog or exact typed fallback if catalog absent. | Useful. Existing empty-catalog state must not block valid recorded event context. |
| Find starting point | Builds backend source package; busy and stale-response protection. | Useful if source match quality and actual deliverable are clear. |
| Cautions disclosure | Explains donor/source risks. | Keep specific mismatch or missing facts, remove repeated invariant disclaimers. |
| Change event / Review checks / Back / Baseline run | Moves worksheet stages. | Keep current source identity throughout. |
| Run five clean laps | Static instruction before tuning handoff. | Five may establish a short baseline, not long-run tire fade. Prompt should match chosen goal and evidence needs. |
| Open tuning | Baseline clears setup context and navigates arbitrary tuning state. | Repair U06. Choose verified fingerprint-matched linked recording; otherwise route to reviewing a new recording. |

## Progressive Tuning

Source: `TuningPage.razor`, `TuningTrackSelector.razor`, `ProgressiveTuningFeedbackEditor.razor`.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Back to setup library | Returns source setup context. | Useful; preserve selection and experiment. |
| Representative race selector | Loads exact analysis/setup, draft and map. | Core. Should be labeled Recording when practice/test included; avoid hiding suitable baseline evidence. |
| Open Race Analysis empty action | Opens browser when no evidence. | Useful, but explain whether recording not analyzed, wrong setup, missing metadata or no clean runs. |
| Retry race | Retries failed tuning context load. | Keep race identity and error; allow edit feedback recovery where possible. |
| Goal | Long-run pace/tire life/restart pace/stability affects deterministic ranking. | Core. State how success will be evaluated, not just a different dropdown value. |
| Early/Mid/Late tabs | Selects within-run feedback phase. | Useful. Show actual selected run/lap windows so user knows what “late” means. |
| Turn map / list selection | Selects exact annotated turn and opens editor. | Core where geometry supported. Whole-lap feedback must remain usable when official turns absent. |
| Turn correction toggle/close | Opens manual label/bound editor. | Necessary recovery; one-time setup should not dominate routine feedback. |
| Corner label / Entry-Apex-Exit sliders / map handles | Records exact user correction bounds. | Useful, but lap-percent sliders are technical. Show location directly and validate ordering/wrap. |
| Save correction | Submits versioned correction. | Keep geometry identity; verify stale maps cannot retarget feedback. |
| Highest priority star | Prioritizes selected complaint. | Useful when symptoms conflict. Explain what priority changes in candidate selection. |
| Close turn editor | Dismisses current form. | Must preserve pending note save; race switch/dispose within 350ms deserves an explicit persistence test. |
| Entry/Center/Exit tight/comfortable/loose | Toggles sparse primary balance reports. | Excellent driver-oriented input. Distinguish Comfortable from unassessed. |
| Add symptom / symptom stage / symptom choices | Captures braking, throttle, wheelspin, harshness, low grip etc. | Useful precise feedback; multiple simultaneous symptoms supported. |
| Add note / text | Stores turn explanation, debounced 350ms. | Useful. Do not lose final keystrokes on close/navigation or silently use note alone to authorize change. |
| Severity / confidence sliders | Weights strength and repeatability. | Useful when wording is clear; subjective confidence is not telemetry confidence. |
| Representative run toggles (max three) | Overrides automatic evidence run selection. | Core. Show why other runs excluded and matching constraints; do not merely say insufficient evidence. |
| Run behavior / tire facts | Displays selected measured summaries. | Core proof, but needs trace and investigation links and competing condition context. |
| Open-setup target | Explicit compatible target for fixed-source feedback. | Essential guard; preserve exact fingerprint and manual-change boundary. |
| General feedback | Autosaved race note. | Useful to capture the user's actual question. Should be considered in explanation, not ignored or treated as numeric proof. |
| Begin analysis | Creates controlled test from structured feedback + backend whitelist. | Name result “Suggest a test”; analysis is already done elsewhere. Show relevant missing prerequisite when disabled. |
| Edit feedback | Returns from current recommendation. | Useful, preserve prior experiment record and revised draft separately. |
| Try / Expected / Watch / Verify result | Shows one system recommendation and checks. | Core value. Needs exact current setup context, manual step bounds, rollback and evidence for/against choice. |
| Comparison run selector | Links later compatible recording or driver-only report. | Core. Explain rejected comparison; matching weather, track usage, tire age and driving matter. |
| Improved/Worse/No change/Inconclusive | Saves subjective or evidence-linked result. | Useful only if it affects future advice and can be reopened. Avoid implying causation from driver's single click. |
| Experiment history | No visible browsing control. | U08: durable records hidden from driver are not a useful learning companion. |

## Settings, connections, diagnostics and first run

Source: `SettingsPage.razor`, `ConnectionsPage.razor`, `DiagnosticsPage.razor`, `FirstRunPage.razor`.

| Control / feature | Purpose and actual behavior | Usefulness judgment / required improvement |
|---|---|---|
| Folder locations disclosure | Reveals Coach read-only path and iRacing paths. | Keep secondary. Normal racing screens should not expose paths. |
| iRacing Documents / installation fields | Edits local roots; Save validates and refreshes. | Necessary recovery. Folder picker would reduce errors; explain installation versus Documents concretely. |
| Guide me / backup guide close | Shows review/check/copy instructions. | Useful admin task; call action “Back up or move data.” |
| Check and prepare | Verifies archive/checkpoints writes and readiness. | Keep; safe-to-copy is point-in-time. Clearly state copy still must be performed and offer folder opening. |
| Find missing telemetry | Launches file location workflow and hashes selected IBT. | Necessary durability recovery; explain which recording needs it and accept only matching identity. |
| Theme swatches / custom color | Sets theme; save behavior partly immediate in state. | Low-priority preference. Needs readable contrast with any chosen accent. |
| Open when I sign in | Persisted startup behavior. | Useful automatic ingestion; reflect actual registration failure. |
| Reduce motion | Disables nonessential interpolation/transitions. | Necessary accessibility; should be effective across charts/replay/popout. |
| Show/hide hotkey | Persists global popout shortcut; host reports invalid/conflict. | Useful rig control; validate before losing previous working binding. |
| Safe-glance cues | Holds ordinary cue changes for appropriate moments. | Core driving behavior; explain meaning plainly. Does not establish safety or cue correctness without live testing. |
| Open with iRacing | Shows popout on connection. | Useful, must honor user closing it for current session. |
| Connections disclosure | Reveals optional service setup. | Keep subordinate; no forced connection before local race review. |
| Connect ChatGPT / verification code | Starts supported sign-in. | Useful only with reachable companion interaction (U02). |
| Repair installation | Launches repair process when runtime absent. | Necessary, never background mutate installed app during QA without scope. |
| Garage61 password/token field / Connect | Stores machine protected token and validates provider. | Necessary. Secret never written to artifacts/logs; no token displayed after save. |
| Disconnect Garage61 | Removes saved connection. | Useful user control; cached authorized data availability and stale status must be clear. |
| Credential/authentication/permission/availability rows | Four provider diagnostic axes. | Good troubleshooting, excessive routine UI. Collapse when connected or simply not configured. |
| Restore defaults / Save | Resets or persists preferences. | Necessary. Show unsaved changes and what restores; do not surprise user by persisting unrelated fields. |
| Troubleshooting disclosure | Shows runtime/data diagnostics. | Keep. Plain issue-and-action labels beat technical assurance text. |
| Run health test / Open logs | Refreshes health, opens logs folder. | Useful support actions; no test-pass claim without actual underlying check. |
| First-run Detect/Continue | Finds expected source directory and advances. | U07 missing-path dead end requires direct correction action. |
| First-run Connect / Do this later | Optional ChatGPT/Garage61 setup. | Useful skip; first useful recording should require minimal ceremony. |
| First-run Verify installation | Runs checks and advances. | Keep; affected failures must block only dependent features, not offline analysis. |
| First-run Open iRacing Coach | Completes setup and opens Home. | Keep; landing should show discovered recordings or concrete next step. |

## Narrative and prose standards

“Agentic prose” is not only a word-choice problem. A paragraph that says “review the available evidence before assigning cause” while offering no comparison is work passed back to the user. Conversely, “Track usage rose from A to B across these laps; temperature stayed within C; this recording cannot isolate grip from driving inputs; compare these traces” is useful even though it contains a limitation.

Remove routine claims such as “local monitoring stays local,” “your data is safe,” “evidence-backed,” “the app kept running,” and “what to carry forward” when they do not change a decision. Keep the real fact: “No temperature was recorded,” “This tire reading is from lap 103,” “These laps include nearby traffic,” “Fuel requires one stop,” or “Apply one garage step in iRacing, then compare the same lap-age window.”

Prefer actual actions: Review race, Compare these laps, Show the stop, Try this in practice, Find a baseline, Suggest a test, Open recording, Retry analysis. Avoid renaming a feature to “companion” unless it actually remembers the question, preserves the evidence and checks what happened next.

## Journey-level acceptance

| Journey | What a complete useful result requires | Baseline judgment |
|---|---|---|
| What happened in Bristol? | Find the correct open race; two fades and pit recovery; relative positions; matched lap ages; corner/input change; track usage/temperature; traffic/damage alternatives; trace links. | Partial data plumbing; no integrated explanation. This round's investigation work must be proved with actual race data. |
| What should I do differently? | One or two concrete changes tied to measured loss, with expected trace difference, caution about uncertain cause, and controlled practice test. | Fragmented generic action prose; not a complete outcome. |
| Plan my next race | Fuel feasibility plus explicit tire/pace/strategy limitations, selected long-run focus, and relevant response triggers. | Useful fuel calculator; broader racing plan remains partial. |
| Should I change the setup? | Separate driving/track/traffic from consistent balance complaint; exact setup; supported candidate; manual step; rollback; matched result history. | Strong safeguards, incomplete end-to-end usability and real A/B acceptance. |
| Help while I drive | Timely short cues from the chosen plan, correct fuel/gaps/flags, no distraction, paused/stale states obvious. | Substantial implementation; active live acceptance explicitly deferred. |
| My recordings should always be there | Automatic discovery, real progress/recovery, retained source, cached reopen, correct event identities, backup/relocate path. | Substantial implementation, recorded QA found/fixed specific failures; breadth/performance and all states remain unproven. |

## What is explicitly untested here

- No native clicks were performed by this audit agent. Source inspection cannot prove hit targets, accessibility tree, chart cursor correctness, focus, clipping or frame delivery.
- No active simulator session, sustained live telemetry, multi-monitor/DPI/hotkey conflict, physical driving cue timing, or 60/244 Hz performance test.
- No authorized current ChatGPT/Garage61 sign-in lifecycle, token rejection, scope failure, network interruption or cross-PC credential migration exercised here.
- No complete real same-car/layout/setup-lineage A/B experiment with comparable conditions and a reopened prior failed experiment.
- No exhaustive 1280×720 / 1920×1080 restored/maximized/scaled/native/web matrix or screen-reader sweep.
- No installer upgrade/uninstall cycle in this audit; historical installer evidence is not evidence for new development binaries.
- No disk-full/raw-source removal/corruption/interrupted backup matrix. Raw files and installed production app were not changed.

The declaration inventory makes the remaining interaction work countable. Completion requires a state matrix per family (ready, empty, missing data, busy, cancel, failure/retry, persisted/reopened), tied to an exact executable and real data. Passing unit tests or reaching all seven pages is not that evidence.

## Repairs made by this audit workstream

These are development-source changes after the baseline inventory. The other workstreams own the new race investigation, visible follow-up coaching, exact result routing and backend analysis. Their validation is recorded separately.

| Repair | Changed source | Verification |
|---|---|---|
| Baseline finder now says what it does. Empty setup library opens recording review instead of a pointless Clear filters. A disabled tuning action explains the missing recording. | SetupPage.razor | UI project compiled; affected setup/planning tests passed. |
| Starting Tune carries a fingerprint-matched baseline through the central state API. Without a linked compatible recording, it leads to recording review and preserves the package. The run instruction distinguishes a full green run for fade from a few quick laps. | StartingTunePage.razor; central API implemented by root workstream | UI project compiled; central handoff behavior must also pass root's identity tests/native walkthrough. |
| Failed first-run detection can open folder settings; Settings offers Continue setup until setup completes. | FirstRunPage.razor, SettingsPage.razor | Compiled. Native missing-folder/save/return/continue sequence remains to exercise. |
| Home no longer spends its main heading on a time-of-day greeting, repeats unavailable sublabels on each row, or describes user tasks as Workflows. | HomePage.razor | Compiled; rendering/layout remains native acceptance. |
| Fuel planning labels explicitly describe fuel feasibility and minimum fuel stops, not an optimal stop decision. It distinguishes balanced fuel stints from tire/caution strategy. | PlanningPage.razor | Planning/decision source tests passed in affected suite. Numerical engine unchanged here. |
| Routine first-run reassurance and connection boilerplate reduced; Garage61 diagnostic axes moved into a disclosure; cue-delay setting explains actual timing; backup and diagnostics use task verbs. | FirstRunPage.razor, ConnectionsPage.razor, SettingsPage.razor, DiagnosticsPage.razor | Compiled; no authentication/provider behavior changed. |
| Final tuning-note keystrokes survive immediate phase/turn/race changes and navigation away. | ProgressiveTuningFeedbackEditor.razor, TuningPage.razor, CompanionState.cs tuning mutation methods | Five new behavioral regressions passed, described below. |

The note-persistence defect was more than awkward copy: notes waited 350/450 milliseconds before saving, then turn/phase/race changes or disposal cancelled the timer. The correction captures the exact note, turn, phase and callback when typed, flushes before dependent actions, and binds writes to the original draft before waiting for the mutation lock. Queued saves merge with the latest stored version of that original draft and never publish it over another selected race. A replacement map draft is rejected.

Focused verification: **56/56 tests passed**, including existing ProgressiveTuningCoordinatorTests, ProgressiveTuningUiTests, PlanningAndStartingTuneUiTests, DevelopmentRoundTests, DurableCompatibilityTests and five new TuningNotePersistenceTests. The new tests execute rapid typing then phase/turn change, dispose after callback replacement, general-note race change/dispose, concurrent source edits held behind a mutation lock then race change, and stale-map rejection. These tests do not substitute for the native event/focus matrix.

Native regression steps for this repair: type a unique last word and immediately select another phase; return and confirm it. Repeat with a different turn, race, page, and app restart. While an earlier save is delayed, type a final note then switch race; reopen both races and verify the note appears only in its original context. Finally type a note and immediately select Begin analysis; the submitted feedback must contain the final word.

## Subsequent native/browser findings and source repairs

The baseline declaration matrix above remains unchanged. The [current usefulness audit](whole-app-usefulness-audit-2026-09-22.md) and [acceptance companion](whole-app-acceptance-coverage-2026-09-22.md) record the current evidence status.

| Finding | Actual evidence | Repair / proof limits |
|---|---|---|
| U03: Bristol fuel range contradicted post-stop margin. | Native 0.17.7 isolated baseline, September 20 3:09 PM race, lap-102 stop: 55.4 range vs 56 remaining alongside +12.2 margin. QA-USEFULNESS-0177-pit-fuel-contradiction.png. Redacted analysis confirms pre-service55.42, post-service68.19, remaining56, margin12.19. | RuntimeMapper now consumes post_stop_all_green_range_laps; UI states before-reserve green projection and next-run-start distance; SAFE becomes Projected surplus. Missing/null post range stays absent, zero stays zero. New-candidate native proof remains separate. |
| Racecraft overview was overcrowded at 1280×720. | Parent's browser interaction found thirteen metrics overlapping labels/values. | Six purpose-specific outcome/pace slots replace the exhaustive overview; all phase/control measurements remain in a collapsed drilldown disclosure. Root owns visual retest. |

Affected regression suite: 28/28 passed, including five new PitFuelPresentationTests cases for the numeric contradiction, missing/null/zero post-stop fields, and overview-versus-full-detail retention. This is source/test evidence, not a claim of exhaustive native acceptance.
