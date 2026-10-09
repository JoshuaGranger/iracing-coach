# iRacing Coach gap ledger

Built 2026-10-09, read-only, from `requests-catalog.md` (Q1-Q264), `built-ledger.md`, and the repository at `C:\Users\joshu\source\repos\iRacing Coach` (`main` at `0bf443a`). Nothing in either repository was changed.

A Codex audit of the catalog against the raw transcripts (`requests-catalog-AUDIT-codex.md`, 2026-10-09) found that some catalog rows treat the August specification, decision-log text and agent summaries as his verbatim asks, and listed 40 asks the catalog missed. This ledger uses it three ways. Missed asks are cited as **A-rows** (A1-A40, the audit's table order; Appendix B gives each one's quote, date and where it landed). Catalog rows the audit could not find in the transcripts are marked **(lower confidence)** where an entry rests on them, and the entry then names his own words that do support it. The audit's TOP 15 is the second priority signal beside the catalog's "asked more than once". Where a specification and his own words differ, his words win.

One entry per requested capability that is missing, reduced or unverified today. Rows of the catalog that are done are not here; the index at the end maps the gap rows to entries.

## Conventions

- `scripts/` = `iracing-coach/skills/analyze-iracing-race/scripts/`. `coach-views` = `crates/coach-views/src/lib.rs` (the projection: `analysis.json` -> view files). "Projection keeps" means the field reaches a view file the browser can fetch. "Route" means `server/src/api.rs`. "Web" means `web/src`.
- The projection today (coach-views `REQUIRED` / `OPTIONAL_OBJECTS`): type-checked and then **dropped**: `technical_insights`, `strategy_planning`, `pace_attribution`, `groove_evolution`, `race_replay`, `setup_telemetry`, `garage61_representative_laps`. **Kept but read by no screen**: `race_grades`, `field_pace`, `conditions` (race view), `corner_tire_age`, `tire_learning` (runs view), `race_investigation.comparisons/hypotheses/actions`, `strategy.forecast.race_plan_decision`, `pit_service.penalty_served_s`, `tire_observation.*`.
- Size is for one agent with the repository's tools: **S** <= 1 day, **M** 2-5 days, **L** > 5 days or needs data that is not captured yet.
- "Check" is his acceptance rule (`docs/01-product/companion-design-principles.md`): which racing decision becomes better, what he can do differently, and how a later recording shows whether it helped.
- Every design below respects: Sessions, Setups, Live and the four-tab investigation as the only destinations (Plan and Driver are the two pages he asked for by name); one investigation screen; no accordions over inputs; the synchronized chart/map cursor; pointer movement makes no request; evidence as a colour token (`evidence.measured/derived/inferred/proxy/unavailable` in `config/theme.dark.json`), never a sentence; pictograms and figures over text; P/Q/R segments; dense tiles; colour is racing data only (amber = caution alone).

## Order

Sections follow the round-2 debate order he approved (correctness -> dense Sessions/Setups -> the comparison picture -> Live -> Crew Chief -> Driver), with the investigation ("features that win races") ahead of setups because every one of his three questions (Q13) is answered there. Inside a section, entries follow how often he asked. The three most-asked missing capabilities are Garage61 reference laps (G23), the setup shop's performance figures (G15) and the tire/physics model (G18); they sit where their value lands on screen, and the roadmap in `MASTERY.md` says when.

Two priority signals, side by side. The catalog's count treats some restatements as repeats (the audit: Q84-Q86 are one message; Q15 and Q164 are one message; summaries and pasted briefs do not count), so the audit's TOP 15, which weighs consequence, clarity and independent restatement, is the second column.

| Entry | Catalog "asked more than once" (rank, times) | Audit TOP 15 |
|---|---|---|
| G28 visual, no prose, smooth motion | 1 (11), 4 (8) | 1, 11 |
| cross-cutting: capability up, complexity flat | 7 (7) | 2 |
| G18 tire and physics model (with G17) | 6 (7) | 3 |
| G23 Garage61 reference data | 2 (9) | 4 |
| G15 setup shop (with G17 baseline) | 5 (8) | 5 |
| G2, G26 fast analysis, sim impact proven | 10 (5) | 6 |
| G6, G7, G10, G12 the coach: explain, prove, say what to change | north star | 7 |
| G11 pre-race brief | 13 (4) | 8 |
| G20, G22, G5 use every channel, the field included | 14 (4), 20 (4) | 9 |
| runs overlaid with trend and slope (done, #90; context in G6) | 9 (5) | 10 |
| G19 `.sto` read, compare, generate, apply | 8 (6) | 12 |
| live editor (done, #25; verify on the rig in G26) | 15 (4) | 13 |
| cross-cutting: truthful claims, impossible features discussed first (G2's result question, G17, G18) | 19 (4) | 14 |
| cross-cutting: finish authorized work, tested (G26) | 3 (9) | 15 |
| G4 sectors | 16 (4) | - |
| G25 Driver page, G24 Ask, G21 live line, G9, G13, G14 | - | - |

---

## A. Correctness first

### G1. Release round 6 and bring the record up to date

**Asked.** The complaints of 2026-10-07: "why are some "best" missing?" (Q57; the audit notes he asked for the omission to be explained and fixed, not for a figure on every race: #85 and #95 answer with the best complete green lap, traffic and damage laps included, null when none qualifies), "I want the run pace best-fit lines here. with the slope shown (pace change / lap)" (Q58), "Lap 0 should show my starting position" (Q60), "I did not finish 6th at the 9:09 race. I got wrecked out" (Q61), "race should be at the top (R, Q, P in the list)" (Q205), P/Q/R toggle chips (Q149), "the setups page feels way too big/loud" (Q191). Backlog #85-#93, #95-#97.

**Exists.** All in source on `main` with tests (`2e72953`, `2254a1b`, `0bf443a`); no release record since round 5 (`bfef9d7`, 2026-10-06). #97 changes the analysis profile (v26), so stored sessions show DNF only after `POST /api/v1/admin/reanalyze`. Docs out of date: backlog #16 reads "In progress" though setup identity v2 shipped; `docs/README.md` names the 0.19.0 snapshot as current; `AGENTS.md` describes a comparison the UI no longer draws (built ledger D.1); the `02-workflows` files state parked MUSTs with no marker.

**In today's app.** No new surface. Release (`release.sh iracing-coach .`), reanalyze (about 8.5 s per race on the N100, 51 sessions in his list), write `acceptance/round6-release.md` with the three viewports, move #85-#97 to Done, mark #16 done, add "parked by D8" markers to the workflow files and settle AGENTS.md against G6. **Size S.** **Check:** his next round of complaints starts from what he sees, not from what is in source.

**Risk.** None technical.

### G2. Every race he drove is listed and analyzed

**Asked.** "it finished and still the latest bristol race doesn't have data. also it says full pit and single pit both in the same race" (X 2026-09-21, Q17); "I did two charlotte races yesterday but one doesn't show up. Why?" (C 2026-10-07, Q172). Backlog #13 (the 2 GB Kansas file).

**Exists.** Upload -> discovery -> analysis -> projection with SSE; Sessions chips `Queued`, `Deferred · reason`, `Failed · reason` with Retry, `Not analyzed`; tray second line "N recordings not uploaded"; admission defers big files (`deferred: exceeds_memory_budget`; native model 5.0 x bytes, 2 GiB per job; session 152's 2 GB file estimates 9.9 GiB); `lap_counter_stopped` deferral for sim sessions past lap 1024. `GET /api/v1/status` is served and shown nowhere. The missing Charlotte race of 2026-10-06 is undiagnosed in any record. Candidates: still on the sim (the collector uploads off track only; or the collector was not running), filed into another session row by the join rule, deferred, or a recording that ended mid-race (which #97 now reads as DNF).

**In today's app.** Diagnose first (server log, `recordings` and `sessions` rows, the collector journal on the sim when it is free). Then: the Sessions heading gets one quiet line when the collector reports recordings waiting (`1 recording on the sim`, from presence; additive `collectors[].pending_uploads` in `ServerStatus` and the SSE `presence` event), and the status glyph of G27 names deferrals. Configuration identity (Bristol Fullpit vs Single Pit Road) is `track_config` on the overview; verify the two labels never share a card. Data path: `collector-protocol.md` section 5 presence -> `server/src/hub.rs` -> `GET /status`, SSE -> `pages/sessions.tsx`. **Size M** (diagnosis S; port phase 4 for files over 400 MB is L and "only if wanted", #13). **Check:** the next race week, every race he drove is on the list within 2 minutes of the session end (the G4-9 rule) and the count equals his own.

**Also asked: the real result after a wreck-out (A39).** "on a race where I wreck out and leave it doesn't have the rest of the race and where I really finished I guess? Like I did not finish 6th at the 9:09 race. I got wrecked out" (C 2026-10-07). The audit reads this as a request for the actual classification after he left, which #97's `DNF · L14` does not give: a recording that stops cannot know it. The result exists only in a later recording of the same event (the S1 rule) or at iRacing's results service, which needs his login. In today's app: the Finish slot keeps `DNF · L14` until a result arrives; a `results` job fetches his official result for the subsession (a stack secret per D10, never in the repository) and fills `results_position` and `field_size`; nothing is invented. **Size M**; whether he wants his iRacing login held as a secret is his call.

**Also asked: proof of no loss (A30, A1).** The 2026-10-03 review brief he supplied required proof of no "loss or duplication of a recording across crash, restart, disk-full, NAS-down, mini-PC-down and partial-chunk cases"; the server's ingest crash tests cover those (W4 acceptance), and the missing Charlotte race is the first field case to check against them. The 2026-08-13 ruling (A1) asked that tests cover "partial recordings, conflicting sources, YAML-only data, and time-limited races": time-limited races are not in any acceptance record (see G11).

**Risk.** The 2 GB file does not fit the 3 GiB container under any current formula; only port phase 4 lifts it.

### G3. Penalties and repairs on the stop

**Asked.** "on the pit indicators (pop up boxes) does it show penalties and damage repairs when they happen?" (C 2026-10-04, Q40); the sentence "Repairs and penalties: not extracted" rejected as agentic prose (#39). Backlog #28i.

**Exists.** Backend, kept and unread: `runs[].pit_service.penalty_served_s` (consumed, `BACKEND_RUN`), `damage_repair.episodes[].timing.repair_work_completed_s`, `summary.total_repair_work_completed_s`, `summary.confirmed_fast_repair_uses` (consumed, `RACE_DAMAGE_REPAIR`); `scripts/analysis_engine.py:6676-6683` already sums penalty seconds for the dropped technical insights. The penalty *kind* (black flag, drive-through, stop-and-go) is not extracted. Web: `PitStopCard` in `ui/pictograms.tsx` shows tires, fuel, stop time. Desktop: the same, plus the stop timeline in Details.

**In today's app.** The pit pictogram's card gains two rows drawn only when the figure is > 0: `Repair 4.2 s` (evidence measured when a recorded repair episode overlaps the stop) and `Penalty 12.0 s`; the Race chart's pit marker and the lap rail's Pit cell carry a small loss-coloured tick for a stop with either. Data path: `runs.json pit_service.penalty_served_s` and `race.json damage_repair.episodes` -> `investigation/geometry/rail-model.ts` pit stops -> `PitPictogram.tsx`. Contract: none for seconds; the kind needs `pit_service.penalty_kind` from the player's `SessionFlags` (backend, profile bump, parity vectors, native `_lap_summaries` twin). **Size S** (seconds), **M** (kinds). **Check:** the next race with a penalty shows it on the stop; race pace figures unchanged.

**Risk.** AGENTS.md: repair-only time only with backend evidence; draw `repair_work_completed_s`, never a subtraction from the stop time.

### G4. Sections become iRacing's sectors

**Asked.** "Bristol has 4 turns yet the speed measurement sections are broken into 7 sections" (X 2026-09-23, Q25); "should these sections maybe grow to entire sectors? since the track is already divided for us cleanly into sectors by iracing?" (C 2026-10-07, Q59). Backlog #94.

**Exists.** The backend reads `SplitTimeInfo.Sectors` -> `lap_traces.sector_start_pcts` (`scripts/analysis_engine.py:2403`, `2582`); `TraceCatalog.sector_start_pcts` is served; the lap rail's S1/S2 blocks use server `sector_times_s`. Sections tabs are the detected sections (`race_investigation.sections`), ranked by `coach-views/src/section_laps.rs`.

**In today's app.** The Sections tab strip becomes the sectors (S1 · S2 · S3 from `sector_start_pcts`), each with the three fastest and three slowest clean laps by sector time, the median as zero, the real racing lines and the four traces as now; the detected corner sections remain as a second, smaller strip inside the chosen sector. Data path: `section_laps::build` runs over sector intervals too; `InvestigationView.section_laps.sections[]` gains `kind` (`sector` | `detected`), `start_pct`, `end_pct` -> `SectionsView.tsx`. Contract: additive field, `PROJECTION_VERSION` 6. **Size S-M.** **Check:** a sector's listed time equals the rail's S1/S2 for the same lap; the loss he reads in a sector matches the sim's own split.

**Risk.** Two-sector ovals give coarse tabs. The audit (A38): his sentence is a proposal ("should these sections maybe grow to entire sectors?"), not a settled rule, and "detected corners as finer splits" is the backlog's addition, not his words. Build the sector tabs; keep the finer strip only if he keeps it after seeing it.

### G5. Per-lap evidence the chart cannot yet draw: slip, gaps, rubber

**Asked.** "I don't get any information about how much my car is slipping/sliding" (X 2026-09-23, Q24); "the track usage went up from prior runs to the last run... so does that impact driving? Why didn't your analysis tell me anything about track usage?" (X 2026-09-22, Q20); "data toggles to show things like track temp, track usage, traffic indicators, slip per lap" (C 2026-10-04, Q45, Q133); "wheel slip just highlights a few dots with no info" (Q50; #62 dropped the proxy); "the traffic overlay seems useless" (#61 dropped the tick; the per-lap gap lines were proposed and not built).

**Exists.** Body sideslip and wheel-slip channels at 60 Hz in the traces (`slip` signals; `_slip_summary` per section in `scripts/race_inspection.py:385`, which the desktop's six-channel Sections drew); per-lap `clean_context.traffic_proximity_fraction`, `traffic_ahead/behind_fraction`, `traffic_side` (consumed, drawn as the 55 % dot); `CarDistAhead/Behind` read only for those fractions; per-lap `conditions.track_temperature_f`, `track_usage_percent` (consumed, toggles exist); `_track_evolution` in `scripts/race_investigation.py:331` (kept, unread). No per-lap slip figure; no per-lap gap in seconds.

**In today's app.** Two backend lap fields: `clean_context.body_slip_median_abs_deg` (median |beta| over the lap's on-track samples, measured) and `clean_context.gap_ahead_s`, `gap_behind_s` (lap medians of `CarDistAhead/Behind` divided by the car's own speed, measured). Race chart toggles `Slip` (a ring sized by the figure, readout `2.8 deg · measured`) and `Gaps` (two thin lines on the right axis); Sections lap rows gain a `Slip` column beside Tires and Traffic; the run line under the chart gains `rubber 42 -> 61 %` when `track_usage_percent` moved during the run. Data path: `analysis_engine._lap_summaries` -> `Lap` (`consumed.rs LAP`) -> `race.json` -> `investigation/ui/race-layers.ts`, `section-context.ts`. Contract: additive `Lap.clean_context.*`, analysis profile bump, parity vectors, native `_lap_summaries` byte-identical, `pnpm gen:wire`. **Size M.** **Check:** a lap he felt sliding reads higher than its neighbours; on the Bristol-type fade, the run line shows whether slip, traffic or rubber moved with the pace.

**Risk.** Body slip is a chassis velocity angle, not tyre slip (label it so; AGENTS.md "supported slip evidence"); a lap median hides a single moment, so the 60 Hz trace stays the detail.

---

## B. The investigation: his three questions

### G6. The comparison picture: exact comparison, run age, fuel, approach, time gap and slip

**Asked.** "this feature needs to go a step further and show me telemetry differences between the fastest segments and the lowest segments. but note that the segment before can impact the speed/situation going into the section" (X 2026-09-23, Q23); "it tells me what laps it is comparing but it does not tell me how far into a green run that is ... I don't get any information about how much my car is slipping/sliding" (Q24); "maybe I lost a lot of time in segment X but really it's because my entry speed into that segment was slower than before" (Q70); "show me data/trends/proof or whatever of the conclusions" (Q19); spotlight only drawn traces (Q7, done). Removed on his word: the earlier/later machinery and its prose (#43, #45, #66: "there is so much ambiguity behind it and it clutters the interface a lot"). AGENTS.md still requires "exact comparison and run context, and critical telemetry visible together" and "supported slip evidence".

**Exists.** Backend: `race_investigation.comparisons[]` (cohorts with run exposure `_run_exposure`, fuel/throttle/traffic metrics, `corners` entry/minimum/exit, `hypotheses`, `actions`) kept in `investigation.json`, parsed by `investigation/investigation-model.ts`, drawn nowhere; `scripts/race_inspection.py` `comparison_inspection` (414: pass pairs, `approach_start` 395, entry/inside/exit gaps, entry-speed difference, `_slip_summary` 385). Web Sections today: three fastest and three slowest clean laps by section time, Tires and Traffic columns, four channels (`SECTION_SIGNALS` in `ui/section-traces-model.ts`: speed, throttle, brake, steering), median lap grey, real racing lines. Desktop: six channels (plus time gap and body slip), dashed approach, pair inspection.

**In today's app.** No new tab, no sentence. Sections gains: (1) two channels, time gap to the median lap (cumulative, from the bins' `session_time_s`, the same arithmetic the Traces delta row uses) and body slip, so six channels sit together as the 2026-09-22 study showed fits at 1280x720; (2) the approach: the section chart's x-axis begins one section earlier, drawn dashed, so entry speed is visible before the boundary; (3) each lap row shows its green exposure (`run_age_laps`, already projected) and its entry-speed delta to the median lap; (4) the fastest and slowest of the six get the thick stroke by default (the pass pair). Data path: `lap_trace_columns` (640 bins: speed, lat/lon, `session_time_s`, slip) -> `traces.ccol` -> `SectionTraces.tsx`; `race.json laps[].run_age_laps`. Contract: none if the slip signal is in the catalog's default set (else additive). **Size M.** **Check:** the Bristol case: open the fade run's slowest sector and read entry speed and slip against the fastest pass without a sentence; after a practice spent on that entry, the next recording's pass sits nearer the fastest.

**Risk.** The AGENTS.md/UI mismatch needs his word: the comparison he wants is fastest-vs-slowest *passes* with context, not earlier-vs-later *groups*; write that into AGENTS.md (G1).

### G7. Corner playbook and coaching targets

**Asked.** Spec 2026-08-05 (lower confidence: specification text he commissioned and read back, not his own words; the audit could not find Q64-Q67 as his asks): "## Corner playbook: columns Corner/phase, Early, Middle, Late / older-set proxy, Groove migration ... Every mapped corner or material telemetry load zone must appear" (Q64); "exact entry speed, minimum speed, peak-brake, brake-release, steering, and throttle-pickup targets only when `comparison_quality.status` is `usable`" (Q65); groove direction only when calibrated (Q66); oval focus "right-front remaining wear, RF inner/middle/outer distribution, excessive entry speed, late/heavy brake release, sustained wheel angle, and throttle timing" (Q67). His own words behind the entry: "To understand how to be faster on the track" (C 2026-08-17, Q13); "I want you to be ready to coach me on it" (Las Vegas, C 2026-09-27, Q69); "tell me what I needed to do differently" (X 2026-09-22, Q19); "corner coaching: don't understand the question" (X 2026-08-15, Q71) was confusion, not delegation (audit A31), so the corner semantics are still his to confirm.

**Exists.** `scripts/race_card.py` `_corner_rows` (691), `_target_for_zone` (533), `_exact_target_cell` (550), `_groove_cell` (563), `_has_groove_calibration` (654) -> `race_card.corner_playbook` (`contracts/race-card-v1.schema.json`); `analysis_engine._pace_attribution` (8792: priorities = time lost per corner against the reference lap plus the corner's share of lap tire energy; `time_loss.analyze_time_loss`, `tire_energy.segment_energy`, `lap_reference.compare_laps`), dropped by the projection; `groove_analysis.analyze_groove_evolution` (440) -> `groove_evolution`, dropped; `race_investigation.comparisons[].corners` kept, undrawn. Desktop: per-corner entry/minimum/exit rows in the comparison panel. Web: none.

**In today's app.** On Sections, each sector or section tab header carries two figures from `pace_attribution.priorities[]`: `-0.31 s vs L17` (time lost against the named reference lap, derived) and `18 % of lap tire energy` (derived); the detected-section strip orders by time lost. Targets appear only when `comparison_quality.status` is `usable`, as small marks on the median-lap trace at the brake-release and throttle-pickup bins, never as text; otherwise nothing. Groove: a high/low tint on the map segment only when `inside_outside_sign_calibrated` (Q66). Data path: `_pace_attribution` -> `InvestigationView.pace_attribution` (bounded: at most 32 priorities, no sentences) by coach-views -> `SectionsView.tsx` header. Contract: additive investigation field, projection bump, `x-source` + `x-consumed-fields`. **Size M**; **L** with groove. **Check:** after he works a corner, the next race's priority list reorders and his sector time there drops against the median.

**Risk.** "copying a faster lap's inputs is not a coaching objective" (AGENTS.md): targets come from his own usable reference only; `_pace_attribution` needs `LapDistPct`, `SessionTime`, `Lap`; groove calibration is rare.

### G8. Tire stress: how hard I am being on the tires

**Asked.** "to understand how hard I'm being on the tires" (C 2026-08-17, Q13, Q129); "Amount of laps on tires also matters" (2026-09-26); "the attached screenshot shows how garage61 shows fuel and tire usage over a run. our version of that in our app is much more primitive and less appealing. I want to go in the G61 direction" (C 2026-08-18, Q14); "a picture of the 4 tires with changed ones green" (Q82, done for stops).

**Exists.** Served and unread in `runs.json`: `runs[].tire_observation.tires.*.remaining_percent.*`, `carcass_temperature_f.*`, `surface_temperature_f.*`, `pressure.psi/kind` (consumed `BACKEND_RUN`), `driving_load.early_brake_vs_late_percent`, `early_steer_vs_late_percent`; `corner_tire_age` and `tire_learning` (consumed lists empty; first to be nulled by the shrink ladder); dropped: `pace_attribution` tire energy, `setup_telemetry`. Traces has tire temperature, pressure and wear signals and a tire-stress map mode. Desktop: Tire management drill-in with per-corner tread/temperature/pressure and the learned prediction.

**In today's app.** The Runs tab run card gains a four-tire pictogram (the existing `TireDiagram` in `ui/pictograms.tsx`): remaining % at the run's end as fill, I/M/O as three bars (left tires O/M/I, right I/M/O, Q7), surface temperature as the bar's hue band, `RF 61 % · 23 laps` under it. The Race chart gets a `Tire laps` toggle (laps on the set per lap from `tire_set_lifecycle`). Fuel over the run is the existing fuel line. Data path: `runs.json tire_observation` -> `investigation/ui/runs-model.ts` -> `RunsView.tsx` -> `TireDiagram`. Contract: none (fields already consumed); keep `corner_tire_age` out of the first shrink step if the card reads it. **Size S-M.** **Check:** a race driven with less entry steering shows higher RF remaining at equal laps than the last race on that track.

**Risk.** Tire readings exist only at stops, so a run's tire state is its end state; the pictogram must say `at L47` and nothing before it.

### G9. Details: stop, tire, fuel and racecraft cards (and the grades question)

**Asked.** "Race Analysis adds three top sections: Telemetry, Technical data, and Race replay"; technical-data subjects "pit strategy, tire management, fuel management, racecraft & pace" (DL 2026-08-07, Q4; lower confidence: decision-log wording the audit could not find in the transcripts); "right now we have a poverty of information on many screens so let's go with option 2" (more density, X 2026-08-15, Q11; his words, with the questionnaire's question text missing). Backlog #5, parked by D8.

**Exists.** `scripts/analysis_engine.py build_technical_insights` (6606) -> `technical_insights[]` with subjects pit (7055), tires (7419), fuel (7619), racecraft (7967), each a list of metrics with label, value, unit, `evidence_type`, detail, action, tone; dropped by the projection. `_race_grades` (8306; rubric race-execution-v2) -> `race_grades` kept in `race.json`, unread. `strategy.pit_assessments[]` (post-stop range, pit-cycle position change) consumed, unread. Desktop: `RaceTechnicalData.razor` (930 lines), 2x2 overview with drill-ins.

**In today's app.** Not a fifth tab. The run lines under the Race chart open, on hover or tap, into run cards with the stop figures (`stop 31.4 s · 2 tires · 8.1 gal · cycle -2`, from `pit_stops` and `pit_assessments`); the Runs tab carries the tire pictogram (G8) and fuel per run (exists); racecraft figures (places gained under green vs under caution, restarts) sit on the position band's hover. Every figure comes with its `evidence_type` colour; the `detail` and `action` strings are not shown. Grades: not drawn unless he asks; a letter names no decision (AGENTS.md test). If he wants them, one letter per category on the Sessions race line's hover. Data path: `technical_insights` kept by coach-views as `RaceView.technical_insights` (metrics only, no strings) -> `RaceView.tsx`, `RunsView.tsx`. Contract: additive field, projection bump. **Size M.** **Check:** before the next race at that track he can read what the last stop cost and what fuel-saving gained; after it the cards show the change.

**Risk.** `technical_insights` is written around sentences; only label, value, unit and evidence survive projection, by rule.

### G10. The Race Card: bottom line, three actions, three triggers

**Asked.** Spec 2026-08-05 (lower confidence: specification text, not his words; Q1, Q73, Q74 were not found as his asks): "show a concise Race Card first" (Q1); "Bottom line: [single decisive sentence] - Start: [highest-value opening-run action] - Long run: [tire/pace protection action] - Strategy: [fuel, pit-window, or reserve action] ... ## Race triggers - Tire phase - Pit - Adjust/rollback" (Q73); word budgets 300/420 (Q74). His own words: "This type of explanation you gave me is generally what I want the app to do. And show me data/trends/proof or whatever of the conclusions and tell me what I needed to do differently" (X 2026-09-22, Q19); the brief's questions in Q78. Against text: "the amount of text and agentic prose in this screenshot is nauseating" (X 2026-09-23). The card's *shape* is the specification's; the three-tile form below is what his words support.

**Exists.** `scripts/race_card.py build_race_card` (1120) and `render_race_card` (1346), called in `scripts/workflow.py:2876`; written as `race-card.md` and returned inline in the analyze result (`race_card`, keys `bottom_line`, `actions`, `corner_playbook`, `race_triggers`, `evidence_appendix`, word counts). Not part of `analysis_view`: no view file, no route, nothing the browser can reach. Desktop: the Race Card in Details.

**In today's app.** Not a text card. The card's three actions and three triggers become one row of three tiles at the top of the Race tab, pictogram plus figure, no sentence: Start (`L1-5 · hold P2 · -0.12 s/lap vs field`), Long run (`+0.06 s/lap · RF 61 % at L47`), Strategy (`2 stops · window L38-44 · 1 lap reserve`); each tile lights its evidence on the same screen (the run, the stop, the fuel line). The `bottom_line` sentence is not shown. The triggers map onto the existing Live tiles (`tire-phase`, `pit-window`). Data path: the server keeps the analyze result's `race_card` (structured fields only, no `markdown`) as `race_card.json` beside the views -> `RaceView.race_card` on `GET /analyses/{id}/race` -> `RaceView.tsx` tiles. Contract: `RaceView.race_card` additive; `worker-interface.md`: the result's `race_card` is persisted; `server/src/handlers.rs analyze` stores it. **Size M.** **Check:** the card's Strategy figure for the last race equals what Plan (G11) says for the next; after the next race the card shows whether the opening action held.

**Risk.** The card emits `unavailable` where `comparison_quality` is not usable; the tile must then be absent, not a sentence (standing rule 8).

### G11. The pre-race brief: Plan for any installed car and layout

**Asked.** Spec 2026-08-05 (Q72, Q75, Q76: specification text, lower confidence as his asks): "Race planning: Select an upcoming car/track/race; review fuel range, cautions, pit windows, prior performance, track knowledge, and coaching targets" (Q72). His own words: "to understand what pit strategy has the highest chance of victory" (C 2026-08-17, Q13); "race planning should be any installed car+layout ... the worst thing the race planning could do is outright lie about something, but I really feel like we should have enough information to make educated guesses" (X 2026-08-15, Q77); "'I'm going to be doing a race in an hour and I'm not familiar with this track. how many laps should I do between pit stops? how aggressively should I drive in the beginning? what setup should I be using? what are the strengths and weaknesses of the setup?' > like essentially an engineer/crew chief/driver brief going into the race" (C 2026-08-18, Q78); "the standard should be one lap of reserve" (Q79); every race event type (Q80); "move on to Las Vegas speedway because that is the next track this week and I want you to be ready to coach me on it" (Q69); "get a bunch of representative data for charlotte motor speedway" (Q142); seasonal knowledge bundle (Q143, spec). The brief he supplied on 2026-08-16 (A10, relayed): "One typed RacePlanDecision owns exact fuel range, stops, margin, one-green-lap reserve and qualified observed-caution scenario", which is what `race_plan_decision.py` became. "essentially useless" in August; parked by D8; backlog #9. Asked 4 times before parking; audit TOP 15 rank 8.

**Exists.** MCP tool `plan_iracing_race` (`scripts/mcp_server.py:503`) -> `scripts/race_plan_decision.py` (`decide` 413, `decide_from_range` 368, `from_payload` 900; `contracts/race-plan-decision-v1.schema.json`: `stop_windows`, `minimum_stops`, `equal_stint_pit_targets`, `reserve_green_laps`, `caution_scenario`, `final_stint_margin_laps`, `no_stop_language_permitted`); `strategy_model.compare_strategies` (378) + `pit_loss.measure_pit_loss` (197) -> `strategy_planning` (dropped: which plan is quickest over measured quantities, `position_context` from `field_pace`); tool `iracing_strategy_history`. For the recorded race, `strategy.forecast.race_plan_decision` is in `race.json` (consumed) and unread; `fuel_per_lap_gal` is on the Sessions row. Server: no job kind, no route. Web: no Plan page; Live has `fuel-laps`, `laps-remaining`, `pit-window` tiles. Desktop: Plan page (299 lines): car, track, setup, reference recording, distance, lap time, burn, start fuel -> stop windows, full-tank reach strip, saved plans.

**In today's app.** One page, `Plan`, reached from Sessions (beside the filters) or from a race card's Strategy tile. Top row: event (car · track · layout · laps or minutes · expected cautions from this series' recent races in his library). A race-distance strip with the full-tank reach and the stop windows `[max(0, D-(S-k)R), min(D, kR)]` as bands, one explicit decision (`2 stops · pit L38-44 and L80-86 · 1 lap reserve`), and the quickest plan against its alternatives as a small bar chart from `strategy_planning.plan_comparison` (its own label: race-time comparison, not finishing position). Then the prior-performance row (best, pace, fall-off at this track from the overviews) and the setup row (the setups raced here, G15). Any installed car+layout: inputs fall back to the car's recorded burn at any track and the layout's length, coloured `inferred`. Nothing fabricated; an unknown input leaves its band empty. Data path: server job `plan` (light lane) -> worker `plan_iracing_race` -> `POST /drivers/{id}/plans`, `GET /drivers/{id}/plans/{plan_id}` (new) -> `pages/plan.tsx`; `strategy_planning` kept by coach-views on `race.json` for the recorded-race side. Contract: new operations, `RacePlan` schema from race-plan-decision-v1, `strategy_planning` consumed fields, DB table `plans` (user-authored, snapshotted). **Size L** (the plan itself M; the setup and aggression rows depend on G15 and G18). **Check:** the planned stop window against the stop he made; the measured fuel margin at the flag against the planned reserve; a plan that lied is a failure.

**Risk.** Time-limited and laps+time races (Q80; A1 asked for them in the tests on 2026-08-13) need `scheduled_minutes` handling verified on a timed race; the caution mix is a history estimate; the field's pace is not modelled (`strategy_planning` says so in its limitations).

### G12. Trials: save the one change and check it against the next recording

**Asked.** "tell me what I needed to do differently" (X 2026-09-22, Q19; his words); "Gate 2: practice discovery, durable trial history and the later-recording check ... Verdicts: improved, worse, not_established, not_comparable; never 'unchanged'" (C 2026-09-24, Q156; lower confidence: found only in a compaction summary, and it quotes implementation scope as if it were his); DL 2026-08-08: "later compatible practice/test/qualifying/race analyses may prove the A/B result" (Q105; the decision-log text exists, his authorship is not established); the acceptance principle he approved on 2026-09-22 ("Joshua approved the compact visual investigation and asked for the same approach throughout the app", `visual-companion-redesign-2026-09-22.md`). Parked by D8; backlog #10. "dont want tests" (#1i) was about the Sessions filter, not the capability.

**Exists.** `scripts/driving_trials.py` (`create_trial` 102, `evaluate_trial` 153, `save/list/load/evaluate_driving_trial_workflow` 322-364; trials live in the worker archive `trials/`); `race_investigation.actions[]` and `driving_corrections` kept in `investigation.json`, parsed, undrawn. Tuning side: `record_open_setup_feedback` (job `tuning_outcome`, route exists), `GET /drivers/{id}/tuning/history` (route exists, unread). Server: no trial job or route. Web: none. Desktop: Save test from a passing comparison, the Tests strip, Check with Improved / Worse / Not established / Not comparable.

**In today's app.** On Sections, one button `Save as test`, present only when the backend has an eligible action for the selection; it stores the exact recording, the sector bounds, the six laps, the metric (sector time, entry speed, slip) and the expected direction. When a compatible later recording (same car, layout, setup lineage) is analyzed, its Sessions card shows one small verdict mark (`improved -0.08 s` in gain colour, `not comparable` in neutral) and the investigation opens with before and after side by side on Sections. No Tests tab, no strip. Data path: server jobs `trial_save`, `trial_evaluate` (light lane; the analyze job queues evaluation of open trials for that car and track) -> worker tools -> `GET /drivers/{id}/trials` -> Sessions card mark, `SectionsView.tsx` before/after. Contract: new operations and a `Trial` schema from the trial fields. **Size M.** **Check:** this entry *is* the check: a verdict exists on the next recording; "not established" shows as such.

**Risk.** Setup lineage and exposure rules will call most open-race pairs not comparable; practice is where trials pay. The 2026-09-22 debate: "A later recording is the next major capability."

### G13. Test day review: run by run, one next change

**Asked.** DL 2026-08-08: "Progressive Tuning becomes a representative-recording, exact-turn, per-run-stage experiment workflow" (Q105; decision-log text, authorship not established); the 2026-09-24 handoff's test-day discovery; the bot's test days exist to feed Coach (Q208, Q214). His own words on 2026-09-26 (A25): "During goal 5 I also want you to keep track of tire wear with all the setup changes", "Add a 30-minute time slot after goal 5", "Then, I want a 2-hour goal 6 where that optimal setup is used and the driver does 30-lap runs": a test day reviewed setup by setup, with wear per configuration and a validation program after it. Backlog #10.

**Exists.** `scripts/test_day.py review_test_day_workflow` (2025; `contracts/test-day-review-v1.schema.json`: `runs` with setup change, early/late windows, fall-off, verdict Faster / Slower / Within scatter / Not comparable, tires; `next_change`). No job kind, no route. Offline sessions join one Coach session per setup (#32, `server/src/offline_sessions.rs`); the Runs tab shows one session's stints. Desktop: `TestDayView.razor` (874 lines).

**In today's app.** An Offline Testing event with two or more sessions (setups) on one day gets a `Test day` line on its Sessions card; the investigation's Runs tab, opened from it, overlays the runs of every setup of the day by lap of run (colour = setup), the run cards show the setup's changed keys (from Versions), the fall-off and the RF remaining at the run's end (A25's wear per setup change), and `next_change` becomes the proposed change on the tuning page's entry. Data path: job `test_day_review` -> `GET /drivers/{id}/test-days/{date}` -> `RunsView.tsx` over several `runs.json` files (client-side join, nothing recomputed) and `setups/version-board.tsx`. Contract: new operation and schema. **Size M.** **Check:** the day's verdicts match what he felt; the next change's effect is measured in the following session.

**Risk.** His own test days are rare; the bot's are Lab and stay out of his list by his rule (#34), reachable only by `?library=lab`.

### G14. Qualifying review

**Asked.** Spec: "review a race/Q starting package", "separate Q and R variants" (Q144; specification text, lower confidence); the brief he supplied on 2026-08-16 (A12, relayed): "Purpose belongs to identity. Implement Q/R x source-shape matrix." His own words: "Real q has out lap to line and then 2 laps to execute" (C 2026-09-26, Q145); "qualifying should only have position, lap 1, lap 2" (C 2026-10-04, Q146, done). Backlog #6.

**Exists.** The Q phase opens in the four-tab investigation; the Sessions Q line shows Pos, lap 1, lap 2 (`timed_lap_1_s`, `timed_lap_2_s`); setups per recording through `setup_observations`; grades unread.

**In today's app.** On the Q phase, Sections lists lap 1 against lap 2 (two laps, not six) with the sector deltas; the Sessions Q line adds the setup name and `vs R · 3 changes` when the Q and R setups differ (the Versions diff). Data path: existing views; `setup_ids` on the session detail; the compare job for the diff. Contract: none. **Size S.** **Check:** at the next event his lap 2 improves where lap 1 vs lap 2 showed the loss.

**Risk.** None.

---

## C. Setups and tuning

### G15. The setup shop: performance per setup and per version

**Asked.** 8 times (2026-08-18 x3, 10-02, 10-04 x3, 10-06). "it shows my typical/average performance with that setup?" (C 2026-08-18, Q111); "a sexy, intuitive display of known setups and performance of that setup (including tire wear data, if we get that far, and pace dropoff, lap times, all the types of things you would want to compare between setups)" (Q112); "a 'version history' ... show the parameters that changed and how it impacted pace & tire life" (C 2026-10-04, Q119). #24 shipped the diff and the recorded runs with "no pace claim".

**Exists.** Versions board (`web/src/setups/version-board.tsx`: changed keys, recorded runs), compare, `raced_count`; per run: `observed_trend`, `tire_observation`, `fuel.used_gal` in `runs.json`; per session: best, pace, fall-off on the overview; tool `iracing_setup_history` (job `tuning_history`). No per-setup aggregate anywhere.

**In today's app.** A Setups row gains three figures from the races on that setup: `best 28.41 · pace 28.62 · fall-off +0.06 s/lap` (medians over its races, with `4 races · Charlotte`); the detail's Versions row gains the same three plus `RF 61 % at 23 laps` from the runs on that version, each with its evidence (`4 runs · green`), nothing under two runs; Compare gets an outcome column. Data path: the server aggregates stored overviews and `runs.json` figures (`SetupSummary.outcome`, `SetupVersion.outcome`, additive) -> `pages/setups.tsx`, `version-board.tsx`. Contract: additive fields; the server takes medians of backend figures as the overview rule already does (stated in the OpenAPI, nothing re-screened). **Size M.** **Check:** he keeps or reverts a version by its outcome; the next race on the kept version lands inside its stated spread.

**Risk.** Track and weather confound setups; default to same-track figures.

### G16. Progressive tuning proven on his recordings, with its history

**Asked.** Spec: "Enter an entry/center/exit symptom after a run; receive one controlled change" (Q103; specification text, lower confidence); DL 2026-08-18: "per-turn feedback entry that is optional per turn ... iterating the tuning setup using AI, known and learned data and tendencies, tire wear, and whatever telemetry is available" (Q106; decision-log wording not found in the transcripts); his own words: "progressive tuning: the first step in this workflow should be intuitively picking a setup from a list ... I think" (C 2026-08-18, Q113, with the "I think" the audit restores; done as `Tune this setup`).

**Exists.** `pages/tuning.tsx` + `tuning/model.ts`; routes `GET /analyses/{id}/tuning`, `POST .../tuning/recommendations` (worker `recommend_structured_open_setup_tuning` -> `scripts/tuning_engine.py recommend_tuning` 531: one conservative ordered change; blockers for fixed setup, repair context, no symptom), `POST .../experiments/{id}/outcome`, `GET /drivers/{id}/tuning/history` (served, not shown); drafts and turn annotations through the outbox. Never exercised on a real recording in an acceptance record. The AI selection among whitelisted candidates (PT-050..052) is gone by D10.

**In today's app.** The tuning page gains a history strip from `tuning/history`: each past experiment a chip (`-0.5 psi LF · Charlotte · improved`), so failed experiments are findable (PT-063); the recommendation shows current and proposed value as a two-cell diff; the Versions board records the applied change when the next garage capture shows it. Acceptance: run `Tune this setup` end to end on one of his open-setup races on the mini PC and write the record. Data path: existing routes. Contract: none. **Size S** (history) + **S** (acceptance run). **Check:** the experiment's outcome is recorded from the next session's runs; the verdict is never "unchanged".

**Risk.** Most of his races are fixed setup (advisory only); symptoms are entered by hand.

### G17. Baseline tuning from physics and the track

**Asked.** "try to make the best/fastest setup for the Supra on Kansas. Do not use: Setups on my computer / Setups on garage61 / Telemetry data from my past races to scrape setup ... go into it totally blind ... what kind of track is it? What type of setup (roughly) is used on this track? set the car up; do laps and test; iterate" (C 2026-09-27, Q124, with "to scrape setup" restored per audit A33); "how can we make the app go from zero tuning awareness + a map/understanding of the track/banking/layout/physics = a damn good tuning setup ... with very little iteration?" (C 2026-10-09, Q125); "a "scratch build" where we start from a fixed setup and tune it" (C 2026-08-18, A18: the starting point he proposed, offered as an idea open to counter-proposal); "best car setup to use (if open race)" (C 2026-08-18, Q78). Spec: "Create a starting tune: Select season, car, exact track layout, and available source setup; review a race/Q starting package" (Q102, lower confidence). The 2026-08-16 brief (A12, relayed): "HTML is evidence. Only validated STO permits Load."

**Exists.** Find a baseline (`pages/baseline.tsx`) -> `build_open_setup_package` (`scripts/tuning_workflow.py:506`; donor by `tuning_engine.choose_oreilly_donor(track_characteristics)` 228; `starting_tune.capability` 251) -> race and Q candidate tiles, donor and why, checks. Track geometry per configuration (`race_foundations.build_track_geometry`, centreline only, no banking). `PHYSICS-AUDIT-codex.md` (2026-10-09, same folder): "the repositories contain useful measurements, conditional models and conservative coaching rules, but they do not yet identify the simulator's tyre or setup physics well enough to generate a defensible zero-iteration optimum"; identified: the cold-pressure lattice (6.8911603 kPa per displayed psi) and the pit fitting temperature (about +13.245 C); conditional: wear regression, surface-temperature dynamics; unidentified: the tyre force law, roll-stiffness/load-transfer mapping, aero. Empirical priors on disk: 126 Vegas and 43 Charlotte shared setups with values (Codex folders); the baseline-tuning brief (`codex-baseline-tuning-prompt.md`) is in progress.

**In today's app.** Find a baseline keeps its one form and adds a row of the track's measured inputs (length, banking, turn radii, straight length, from the survey or the geometry; measured or inferred). Its starting point, when the physics and priors say nothing for a key, is the series' fixed or default setup (A18). It emits a full setup sheet (every garage key) with, per key, the rule that set it (`physics` / `prior` / `donor` / `default`) as a colour token, plus predicted cold pressures and end-of-run temperatures with error bars. `Tune this baseline` opens the tuning page. After the next recording on it, the sheet shows predicted against measured temperatures and pressures per tire. Data path: `build_open_setup_package` gains `track_inputs` and a generator stage (new module in `scripts/`, oracle first) -> setup-package-v1 extended -> `pages/baseline.tsx`. Contract: `setup-package-v1` additive (`baseline.keys[].rule`, `predicted`), OpenAPI `BaselinePackage`. **Size L** (v0 generator M once the Codex brief lands; the identification experiments L, in iRacing-utility). **Check:** predicted against measured RF temperature and pressure after the first run; laps to a competitive time compared with the donor route.

**Risk.** With the laws unidentified, v0 is priors calibrated by physics, not physics; never writes a `.sto` (G19); purchased setup values never appear in any output.

### G18. The tire and physics model: predictive wear, exact laws

**Asked.** 7 times (08-15, 08-17, 09-26, 09-27 x2, 10-06, 10-09). "all these things combine to a predictable amount of tire wear... it is all known physics in the real world, none of it is guesswork" (X 2026-08-15, Q128); "IRacing is a program ... your laps around the track and data gathering is simply a reverse-engineering of the math" (C 2026-09-27, Q130); three experiment designs to separate temperature, wear, fuel and rubber (Q131); "evaluate our physics model and if there is anything you are unclear about I want you to pursue cleaning up that understanding" (C 2026-10-09, Q132); DL 2026-08-07: a tire-wear model that keeps "measurements, proxy wear, predictions, and confidence/bounds distinct" (Q127; decision-log wording, lower confidence). The method he set on 2026-09-27 (A28, a brief he pasted): "Reproduce every numeric claim in FINDINGS.md", "Mark each claim reproduced, mismatched (with your number) or not reproducible", "test alternative functional forms", "check identification (data range, confounding) and the structure of the residuals", "Rank the top 10 experiments by information gained per sim-minute", and the relationships to examine: tread temperature against camber and pressure, wear against slip, pressure against volume, fuel burn. Then (A27): "After the debate I want you to start a 6-hour session to learn more and fill in some of the gaps that your debate with codex unveils." Audit TOP 15 rank 3.

**Exists.** `race_foundations.build_tire_learning` (1082; NASCAR family, status `collecting`), `build_tire_prediction` (weighted historical rate, three sessions minimum), `scripts/tire_model_gate.py` (`gate_decision` 295, `evaluate_candidate` 448; the audit notes its gate is bypassed in the traced workflow) -> `tire_learning` in `runs.json`, unread; `tire_energy.wear_energy_diagnostic` (382); `strategy.median_green_lap_degradation_s_per_lap`. iRacing-utility holds the IRP and Vegas studies (F1-F47, R1-R18): wear regression `ln w = a + b ln E + c T_track` per tire (conditional), the pressure build-up S-curve, surface-temperature time constant about 3 s. Audit verdict: no identified forward law from tire state to grip or wear.

**In today's app.** No screen of its own. The model appears where decisions are: Runs cards (predicted remaining at the run's end against measured, with its band), Plan (stint length from predicted fall-off), Baseline (predicted temperatures and pressures). The one new visible thing is the prediction error, a small bar on the run card (`pred 58 % · meas 61 %`): the model's scorecard. Data path: `tire_learning.prediction` (`runs.json`; consumed fields to add) -> `RunsView.tsx`; model work is backend plus utility experiments, with parity vectors for any ported stage. **Size L** (utility experiments ranked by information per sim-minute, as A28 asks: the same-set thermal transient F44, temperature versus wear separation, rubber; wear tracked per setup change as in A25). **Check:** the prediction error on each new race shrinks over the season; a model that fails its gate draws nothing (standing rule 8).

**Risk.** The audit's ranked unclear items need experiments the bot has not completed; Lab data is excluded from his library by rule, so training reads Lab explicitly on the server side.

### G19. Setup files: decode, write, apply, harvest (.sto)

**Asked.** 6 times (08-15, 08-18 x3, 08-20, 10-02). "load the setups into iracing for me (or generate STOs if we ever figure that out)" (X 2026-08-15, Q107); "take control of the iracing tuning setup screen and enter all the values in ~10-15 seconds by controlling the computer" (C 2026-08-18, Q108); "the application can generate STO (if you can figure out how). I do not want to overwrite STO" (Q109); "compare STO files without having to first run iRacing ... I want you to attack the encryption and figure it out" (Q110); "setup source: I don't understand this either. if you can read a source then use it" (X 2026-08-15, A5: his challenge to the models' availability assumptions, not an approval of their restrictions); D9: ".sto decode 'really, really want'" (Q114; the detailed D9 wording is lower confidence, the wish is his); "Did you harvest all my available setups?" (#12). The 2026-08-16 brief (A12): "Only validated STO permits Load." Parking lot (2026-10-02): harvest and decryption are utility functions. Audit TOP 15 rank 12.

**Exists.** `.sto` payloads are encrypted; no decoder (DL 2026-08-18). The harvest lives in iRacing-utility (one file piloted; 8 of 37 owned cars hold files without values; 706 files of unowned cars cannot be harvested). The Setups page lists files without values below a divider (#74); garage capture fills values when he loads a file.

**In today's app.** Nothing new in Coach; the honest surface exists (`Files without values · N`). The utility's harvest fills them through the public upload API when the sim is free (resume at Supra file 2, #12). Applying a setup by driving the garage stays in the utility (solo, copies). **Size:** harvest S per car per sim session; decryption L or infeasible. **Check:** the count under the divider falls; a harvested file's values equal the next recording's `CarSetup`.

**Risk.** Decryption may never land; `.sto` writing stays forbidden until a format is proven; garage automation costs sim time and can fail tech (the AtlantaSS note in #12).

---

## D. Live and the crew chief

### G20. Crew Chief mode: the whole field, live, and a watch-only page

**Asked.** "the data monitor collector on the racing PC should be gathering all extra telemetry it has availability to gather, such as other car positions, lap times ... what cars took what tires in the pit stop, whether they took gas, how long their pit stop was, all the things ... on the 'live' screen I want to be able to see a live race dashboard > the track map with all the cars on it, statistics about when each car's last pit stop was and which tires they took and their fuel and estimated laps of fuel remaining ... then the live telemetry from my car. then I want to have like a tab/selector at the top right where there is 'Driver' and 'Crew Chief' options" (C 2026-10-04, Q94); D6: "A friend or family member sees a read-only live page" (Q91; owner-decision wording, lower confidence as verbatim; the audit confirms the decision was recorded); "The goal of the app is to be my crew chief and engineering companion" (C 2026-08-17, Q68); "I want it to literally gather every single bit of telemetry available" (Q164). On availability (A5, X 2026-08-15): "live channel: why would a live channel be missing? either it's available always or it's not? need better information here", so the field frame must say which per-car channels the sim publishes and never show a guess. The 2026-10-02 prompt (A29): the tray opens "the telemetry monitor ... which will show whatever the user configures real-time from the live data" (the overlay does this today). The 2026-10-03 review brief (A30, relayed): "streaming continues until the session really ends while anyone watches" (the collector-protocol section 6 rule). Wave B, backlog #26; crew-chief calls on the overlay parked (Q96, decision-log wording). Audit TOP 15 rank 9.

**Exists.** Live frame v1 carries the player's 45 channels only; the engine reads `CarIdxPosition/ClassPosition/Lap/F2Time/LastLapTime` for gaps (`crates/coach-live/src/sample.rs:99-103`) and sends none of them. `.ibt` recordings already hold every car's `CarIdx*` arrays (`race_foundations.build_race_replay` and `competitor_pace.analyze_field` read them). iRacing publishes pit *service* (tires per corner, fuel) for the player only; for others, stall time and compound are observable (#26 reading). No read-only mode: anyone on the tailnet edits layouts (D6 unmet). G4 passed on a parked car; the moving-car collector CPU read 2.34 % against the 1.5 % budget before the CPU fix, which was measured idle only (G26).

**In today's app.** Live gets a `Driver · Crew Chief` segmented selector top right, remembered per device, new devices Driver. Crew Chief: the configuration's track map (the latest analysis's `track.json` for that track; none when there is none) with every car as a dot in class colour and the player ringed; a position-ordered field board (pos · car · gap · last lap · pit state · last-stop lap · laps since · observed stall s · compound when known); the player's telemetry strip underneath; stale state greyed with its age. No fuel figure for any car but the player's; `~18 laps · estimate` only when stall time and a burn are both known (his "if we can guess that", labelled). Crew Chief is read-only by construction (no editor); Driver keeps the editor. Data path: collector field frame at about 5 Hz (new binary message: per-car lap pct, position, lap, on pit road, last lap, pace flags, compound; stall timer derived by the collector) -> server relay (`/ws/live/{id}?field=1`) -> `web/src/live/field.ts` -> `CrewView.tsx`. Contract: `live-frame.md` field message, `live-channels.json` field table, `collector-protocol.md` section 6 demand (`stream.start` with `field`), OpenAPI `LiveHello.field`. **Size L.** Depends on G26 (the field stream's cost proven on a moving car against the G4 budget: collector <= 1.5 % of a core, <= 32 KB/s per stream). **Check:** during the next race a watcher sees the field's stops and gaps as they happen; afterwards the recording's `race_replay` (G22) agrees with what was shown.

**Risk.** A 24-car frame at 5 Hz is 2-3 KB/s and fits; `CarIdxTireCompound` and `CarIdxOnPitRoad` are published, other cars' service is not; "nothing travels back to the car in v1" (D6) stands; overlay cue growth stays parked (D8).

### G21. Live racing line and driving traces on the Live page

**Asked.** Restore item "Live racing line and driving traces on the Live page" (Q95; backlog #7; desktop `LiveTelemetryVisuals.razor`, `live-telemetry-chart.js`). Lower confidence: the audit found "live racing line" only in summaries and review context, never as his own message; the restore question he did ask was general ("was all that in the last desktop app and you didn't port it over", Q28). Tile forms Number / Bar / Gauge / Chart are his (X 2026-08-05, Q85; done per metric). Confirm he wants this before building.

**Exists.** Frame channels `latitude`, `longitude`, `lap_dist_pct`, `speed`, `throttle`, `brake`, `steering_*`; track geometry per configuration in any analysis's `track.json`; tile trend windows (15 / 30 / 60 s, 1 / 3 laps) in `web/src/live/history.ts`.

**In today's app.** Two new tiles in the palette: `Map` (the configuration's centreline from the latest analysis at this track, the car from lat/lon through the geometry transform, the current lap's line behind it, the last lap faint) and `Inputs` (throttle, brake and steering over the last lap by distance, the previous lap grey). Tiles obey the editor; the rig overlay draws neither unless its GDI budget is proven. Data path: a small route `GET /drivers/{id}/live-track?track_config_key=` (the server picks the driver's latest analysis at that track) -> `track.json` -> `live/map-tile.tsx`. Contract: one additive route. **Size M.** **Check:** on the tablet or rig screen the map keeps the stream rate without frame drops (the G2 harness on Live); the line matches the recording afterwards.

**Risk.** A track with no analysis has no map (honest absence).

### G22. Replay: the field at the cursor

**Asked.** DL 2026-08-07: "Race replay is a fixed-screen recorded-data visualization with synchronized participant map, leader rail, running-order grid, always-visible horizontal flag timeline, known-only comparisons, player telemetry, own fastest-clean overlay" (Q151; decision-log wording, lower confidence as his words); his own words: "everything is required except Garage61" over a list that included Race Replay (X 2026-08-15, Q152); the 2026-08-16 brief (A13, relayed) named "replay phase, cadence and clock". Parked by D8; backlog #8 says "needs full-field capture on the rig" (the capture is in the `.ibt` already; the rig need is live, G20).

**Exists.** `race_foundations.build_race_replay` (912; 2 Hz frames from `CarIdxLapDistPct/Position/OnPitRoad/PaceFlags/LastLapTime`, participants from the session info, flag states, a `coverage[]` list of what was recorded) -> `race_replay`, REQUIRED by coach-views and then dropped; `scripts/replay_manifest.py`, `live_replay_v2.py` (`.ircr2`; the collector writes none, by design). Desktop: `RaceReplayWorkspace.razor` (1,262 lines).

**In today's app.** Not a fifth tab. A `Field` toggle on the Traces and Sections map draws every car's position at the cursor's session time (the cursor already maps bin -> `session_time_s`), the player ringed, with a small running-order column (pos · car · gap) beside the map; on the Race chart a `Field` toggle draws the leader's and the car-behind's lap times as thin lines (from `field_pace.relative`, served and unread). Data path: coach-views writes `race_replay` as its own view `field.json` (2 Hz frames: about 7,200 x 24 cars, 1-2 MB, bound 4 MiB) -> `GET /analyses/{id}/field` -> `TrackPanel.tsx` field layer. Contract: new view and route (additive), projection bump; `field_pace` consumed fields filled. **Size M-L.** **Check:** "Was that stop worth it?" (design principles): the position recovered after his stop is visible at the cursor; the next race's stop timing is chosen with it.

**Risk.** Coverage depends on the channels recorded (`coverage[]`); 24 dots at the cursor must keep G2 (>= 55 fps), so they go on the canvas marker layer.

---

## E. Driver, AI and external data

### G23. Garage61 reference laps inside the app

**Asked.** 9 times (08-15 x2, 08-17, 08-18 x2, 09-26, 09-27, 10-06, 10-07), the most-asked missing capability. "right now I cannot get competitive data from Garage61"; "maybe Garage61 integration would give us an estimate of how much fuel that car consumes per lap on that track?" (Q135); the email asking Garage61 for API access, "Even 100 laps per week would be valuable" (Q136); "pursue if it is possible to use a scraping method to gather fastest/representative laps for comparison" (Q137); "'iRacing Coach' is my personal 'Garage61 plus' experiment ... it is very challenging to select and add laps to the analysis" (Q138); "You were supposed to have used garage61 data to become better than me. Did that happen?" (Q140); "get a bunch of representative data for charlotte motor speedway" (Q142). D10: server-side token as a root-only secret (Q141; the "root-only" wording is lower confidence, the decision is recorded); Garage61 enriches and never blocks (Q134, specification text). What the catalog missed (audit): "a graphic showing the telemetry of your fastest 3 laps vs my fastest 3 laps" and "use the setup from my fastest lap and try to reproduce the fastest lap ... iterate/develop/learn/try until you can beat my fastest speed" (C 2026-09-25, A22): a reference can be the bot's laps as well as Garage61's; "the entire idea is that I hope for it to be a personal project ... I want them to be able to see what I produce and integrate any of the features/inspirations into their own website. that would not bother me" (X 2026-08-18, A17); "there is a risk of stealing all the data and rendering Garage61 useless. I think that needs to explicitly acknowledged and encourage Alex to defend against that" (X 2026-08-18, A15), with the safeguards he intended to propose (A16): "individually approved public OAuth applications, narrow comparison searches, small result sets, firm usage limits, short-lived downloads, full respect for privacy settings, audit logging, and immediate revocation. Aggregate results could also replace raw telemetry where possible"; "Not the email. Don't send email. Put together the information for me to send it" (X 2026-08-17, A14). Audit TOP 15 rank 4.

**Exists.** `scripts/garage61_client.py` (847: `Garage61Client` 218, `rank_comparable_laps` 576, `parse_telemetry_csv` 552), `garage61_fitness.py` (status axes, claim fitness), tools `garage61_auth_status`, `sync_garage61_references`; `garage61_representative_laps` REQUIRED by coach-views and dropped; the worker env seam `IRACING_COACH_GARAGE61_TOKEN_FILE` in `server/src/worker.rs`; no route, job or UI. The API exposes other users' laps only inside a team; his browser or bot reads in-page CSVs (memory: never open Analyze). Pulled data sits in the Codex folders (Vegas 126 setups, Charlotte 43, track edges).

**In today's app.** The Traces lap rail gains one `Reference` group: representative laps for this car and track from Garage61 (`G61 · 28.102 · 2026-10-01 · team`) or from the Lab library (the bot's best laps, `Lab · 28.311`, A22), the source, scope and date always on the row, stale never hidden (Q135: "it's still data"); drawn like any lap, so the Delta row and Sections compare against it with one toggle (Q138's "fewer clicks"), which is also A22's best-three-against-best-three graphic. Plan (G11) reads the reference burn per lap as `inferred`. The sync follows the safeguards he himself proposed to Garage61 (A16): narrow searches, small result sets, firm usage limits, privacy settings respected. Data path: server job `garage61_sync` (light lane, daily and on demand) -> worker `sync_garage61_references` -> `garage61_representative_laps` kept by coach-views as `reference.json` (bins at the catalog's density) -> `GET /analyses/{id}/reference` -> the rail. Contract: new view and route; the secret through `g-house-app set` (D10); the scope label is mandatory ("label the comparison scope and do not present it as the full public field"). **Size M** for own and team laps; public-field laps depend on Garage61 answering Q136 or on his browser session (utility). **Check:** his lap against the reference in the sector where he loses most; the next race closes it.

**Risk.** Scraping terms are his call and belong to the utility, not Coach (and he framed the relationship as one where Garage61 may adopt Coach's ideas, A17); a stale reference is labelled, never blocked; the 2026-08-13 ruling on Garage61 metadata precedence and compatibility paths (A2, relayed) still applies to any sync that reads older artifacts.

### G24. Ask: the AI coach behind an API key

**Asked.** "I want AI to be as useful as possible with this integration. if there is some extra permission or capability we should add to maximize the usefulness of this coaching tool... that is the goal... so propose it" (X 2026-08-15, Q153; the catalog's "read-only" is not his, audit A32: proposals may include capabilities that act, and each one is still his to approve); "Output must help Joshua drive, plan, analyze, and tune. Generic coaching prose ... [is] not [a] substitute for race-specific value" (Q155, a brief he commissioned); the 2026-08-16 brief (A13, relayed): "numeric Race Coach backend", "evidence-linked AI packets and tool authority", which `race_coach_packet.py` and `coach_tool_authority.py` are; D10: "Ask ... will use an API key the way the g-house apps do" (Q157; found only in the October 4 summary, the decision is recorded). Backlog #11.

**Exists.** `scripts/race_coach_packet.py` (`build_packet` 302; supported and unavailable sections of numbers), `coach_tool_authority.py` (`authorize` 206, allowlists), `contracts/ai-coaching-output.schema.json` (validated output), `ai-tuning-output.schema.json`. The desktop's `RaceCoachPanel` and `CoachEngine` (ChatGPT) are gone; no server route; no key.

**In today's app.** Not a chat. One `Ask` button on the investigation toolbar opens a narrow panel with a question box; the answer is shown only as the evidence rows the validator accepted (claim · figure · lap or section link · evidence colour), each row lighting its place on the chart; anything the validator rejects is not shown; no paragraph. The packet is the analysis's own numbers, never raw telemetry. Data path: `POST /analyses/{id}/ask` (light lane) -> worker builds the packet -> the model API with the stack secret -> validator -> `AskAnswer` rows -> `AskPanel.tsx`. Contract: new operation and schema (ai-coaching-output); secret per D10; deterministic analysis unaffected offline. **Size M.** **Check:** a claim is a measurable ("entry speed T3 -4 mph vs your best") and the next recording tests it; a claim that cannot be tied to a figure is rejected by design.

**Risk.** His prose rule against model output: the validator is the product; cost per question; the model and SDK choice (the `claude-api` skill applies when built).

### G25. Driver statistics page

**Asked.** "i want the driver name at the top right to be something clickable. and I want it to take me to a statistics page that paints a picture about my skill level and accomplishments. I want there to be tabs, one for each series/racing style in iracing, and also an 'All' that combines everything into one picture" (C 2026-10-04, Q62). Wave B, backlog #27. `TopBar` renders the name as a `<span>` (`web/src/app.tsx:146`).

**Exists.** Per session on the overview: `results_position`, `start_position`, `field_size`, `best_green_lap_s`, `race_pace_median_s`, `fuel_per_lap_gal`, `finished`, `retired_lap`; `drivers.latest_irating`; `DriverInfo` per recording holds licence and iRating (a history can be plotted from recordings alone); `field_pace.relative` (pace against the leader; served, unread). No aggregate endpoint.

**In today's app.** `#/d/{id}/driver`: tabs All · Oval · Road · Dirt Oval · Dirt Road (only those present), Series as a filter; one dense board: starts · wins · podiums · average start -> average finish · positions gained, each with its coverage denominator (`of 26 recorded`), DNFs; a pace row (median green pace against the field cohort when `field_pace` names one); green-lap consistency; incidents per race; a rating line from recorded `IRating` snapshots by date; tracks and cars as chips. No single score, no career totals the recordings cannot support. Data path: `GET /drivers/{id}/stats?library=main` (the server aggregates stored overviews and header results; `field_pace` kept for the pace row) -> `pages/driver.tsx`. Contract: new operation and `DriverStats` schema; `field_pace` consumed fields. **Size M.** **Check:** the picture moves with each race; every figure names its recordings on hover.

**Risk.** Official results exist only when a later recording of the event holds them (the S1 rule); many finishes are the running position when the recording ended, and each figure must say so.

---

## F. Platform, verification and housekeeping

### G26. Proven on the rig and on real devices

**Asked.** "a very lightweight and optimized installation that does not impact simulator performance" (C 2026-10-02, Q98); all four device classes (D11, Q99); "Distinguish implementation, automated verification, real-system verification, packaged-artifact acceptance, and Joshua's acceptance. Do not collapse them into 'done.'" (Q224); "give me an installer to try it on my simulator at the end" (Q245); "do a full stress test, live evaluation of how everything functions with real data. click every single screen/option" (Q247).

**Exists / unverified** (built ledger D.3): G4 passed on a parked car; moving, the collector read 2.34 % of a core against the 1.5 % budget (`94d8968`); the CPU fix (2.98 % -> 0.26 %, idle) was installed as `a256511` on 2026-10-04 and never re-measured on a moving car; every device check is Chromium emulation; collector autostart at sign-in and the tray are unexercised; the NAS outage was never real; Compare, Find a baseline and Progressive Tuning were never run on a real recording in an acceptance record; collector updates are hand-installed over the remote tools ("Collector update needed" is shown).

**In today's app.** No screen. Work: (1) G4 re-run on a moving car (the bot at IRP, solo, sim free) with the installed collector, `tools/rig/g4-perf.ps1`; (2) one real-device pass (his phone over the tailnet, the tablet, the rig screen: Live, Sessions, Traces) with screenshots he can open on his phone (A21); (3) autostart: sign out and in on the sim when free (never reboot); (4) Compare, Baseline and Tuning on one real open-setup race, recorded; (5) a collector self-update path (`coach-collector.exe update` from `/collector/latest`, hash-checked) or a written manual path; (6) the recovery cases of A30 (crash, restart, disk full, NAS down, mini PC down, partial chunk) re-run against the released server, not only in unit tests. Every record in the form he set on 2026-08-15 (A8, a brief he supplied): "commit SHA, changed-file manifest, tests run, evidence tier, known limitations, and rollback notes", with "at least one adversarial or fault test". **Size S each, M together.** **Check:** records under `docs/08-rewrite/acceptance/`; the G4 line true on a moving car before G20 ships. Audit TOP 15 rank 6.

**Risk.** Sim time only when it is free (rule); a real NAS outage test is his infrastructure.

### G27. Status, jobs and recovery, visible and quiet

**Asked.** "takes forever? seems extremely un-optimized. this should take seconds at most..?" (X 2026-09-21, Q16); robustness to offline, missing credentials and in-progress recordings (Q174, specification text); the 2026-08-16 brief (A11, relayed): "Supply rollback, rebuild-from-raw, migration-resume and old/new-reader specifications", "Define compatibility windows"; the redesign table's "Active work has a conditional progress/cancel control" and DIAG-001..006 (orchestrator promises, not his words).

**Exists.** `GET /api/v1/status` (NAS state, spool, queue, collectors, disk, `collector_newer_than_server`) served, shown nowhere; `GET /jobs`, `POST /jobs/{id}/cancel` (`api.cancelJob` defined, unused); SSE events; Sessions chips; the "Server unreachable" banner; the outbox bar; the tray line.

**In today's app.** No Settings page. One quiet glyph in the top bar right of the driver name: nothing when all is normal; otherwise a dot and the one word that matters (`NAS`, `Queue 3`, `Collector update`, `Sim · 2 waiting`), hover for the figures; a running analysis shows a thin progress line under its Sessions card with Cancel (the existing route). Data path: `api.status` + SSE -> `TopBar` in `app.tsx`. Contract: none. **Size S.** **Check:** when a race is missing (G2) the reason is one hover away.

**Risk.** Keep it quiet (standing rule 3: no routine success noise).

### G28. Look and motion: what is still open after six rounds

**Asked.** "Keep in mind you are supposed to make this a world-class interface. Consider spacing, smoothness, intuition, innovativeness" (X 2026-08-05, Q175); "Motion must look and feel smooth ... Graphics matter. The product should look like a polished, professional race-engineering workstation" (C 2026-08-15, Q177, Q178, a debate brief he commissioned); "overall the webpage lacks character and vibrance" (C 2026-10-04, Q196); "I did tell you I wanted vibrant so the color itself isn't bad, but this just isn't perfect yet ... too large and colorful and less sleek" (#17; lower confidence: the audit did not find this later quotation); "the microscopic gains we've made versus the effort that I put into describing it to you got very old" (X 2026-09-20, Q184). On the cursor his words were "some gentle smoothing on the traces I guess? like interpolation? and more data points on the chart? I don't want the data to become fake" (C 2026-10-06); 640 bins and monotone curves were the implementation's choice (A36). "Agentic prose" was never a universal deletion rule (A40); the requirement is to communicate visually. He reviews from his phone: "Show me some screenshots of the app pages to give you critique. I'm on mobile at the moment" (X 2026-09-22, A21). Audit TOP 15 ranks 1 and 11.

**Exists.** The Broadcast direction (`docs/08-rewrite/design/README.md`), tokens in `config/theme.dark.json`, six rounds of his fixes (#1-#97), one type scale, the control recipe (#80), keyboard-only focus (#78), rotated maps, 640-bin curves and the continuous cursor. Open: the Barlow Semi Condensed bundled fallback (needs his download approval), `--motion-structure` transitions never checked on a real device, round 6 unreleased (G1), no acceptance by him of any scope.

**In today's app.** Nothing to build before his next look; the loop is his: release round 6, he looks on the laptop, the tablet, the rig and his phone, complaints become round 7, and "no coding. just giving feedback. I'll tell you when to code" (X 2026-09-21, A20) separates the two. Keep the colour budget (colour is data; amber is caution's alone; at most three coloured figures per row), the one type scale (Sessions sizes everywhere, #89) and centred controls. Each round record's screenshots must open on his phone (a tailnet page or an artifact link, A21). **Size S per round.** **Check:** his words; each round record carries the three viewports.

**Risk.** Drift back to decoration (standing rules 3, 4, 12).

### G29. Small restores and open decisions

| Item | Where it stands | Size |
|---|---|---|
| Trace layout rename (RA-059C) | toolbox offers select, New, Copy, Delete | S |
| Seeded Race and Qualifying live dashboards (LMG-009) | only `Default` built in (`server/src/userdata.rs`) | S |
| Practice `Fall-off` on the #90 trend definition | `server/src/overview.rs` still last-minus-first screened lap | S |
| No-tire stop and the run split (#42) | backend splits, colour by tire set shipped; the alternative waits for his word | S, if he wants it |
| Kansas 2026-09-28 09:23-10:11, seven solo files (#32) | his or the bot's; they stay in his list until he says | decision |
| Lab-window garage observations count as `main`; `<file> · modified` naming (#84) | `server/src/library.rs` tests assert the current behaviour | S |
| Fuel gauge scale | `DriverCarFuelMaxLtr` not exposed; the gauge scales to the largest recorded run-start fuel | S |
| `148 values` on Setups | no `SetupSummary.value_count` | S |
| Exported-sheet units (audit U21) | server stores raw text; units need the server | S |
| Setups filters across devices | `setup_filters` holds one car, the page selects several | S |
| Notes on sessions and analyses | `NotesPanel` mounted only on setup detail; D12: general notes never existed | S, low value unless asked |
| Series and Fixed/Open after `rebuild-index` | null until a recording is re-ingested | S |
| G2 fixture at projection 2+ (W6) | committed fixture predates projection 2 | S |
| Housekeeping | `server/%SystemDrive%/ProgramData/...` cache files tracked in git; a stray cargo target tree in the repo root | S |

---

## Appendix A. What the backend already produces and which entry would show it

| Block or field | Producer | Today | Entry |
|---|---|---|---|
| `technical_insights` | `analysis_engine.build_technical_insights` | dropped | G9 |
| `strategy_planning` | `analysis_engine._strategy_planning` (`strategy_model`, `pit_loss`) | dropped | G11, G10 |
| `pace_attribution` | `analysis_engine._pace_attribution` (`time_loss`, `tire_energy`, `lap_reference`) | dropped | G7, G8 |
| `groove_evolution` | `groove_analysis.analyze_groove_evolution` | dropped | G7 |
| `race_replay` | `race_foundations.build_race_replay` | dropped | G22 |
| `setup_telemetry` | `analysis_engine._setup_telemetry` | dropped | G16, G17 |
| `garage61_representative_laps` | Garage61 sync | dropped | G23 |
| `race_grades` | `analysis_engine._race_grades` | kept (`race.json`), unread | G9 (if wanted) |
| `field_pace` | `analysis_engine._field_pace` (`competitor_pace`) | kept, consumed `[]`, first to be nulled | G22, G25, G11 |
| `conditions` (race) | per-lap conditions | kept, consumed `[]` | G5 |
| `corner_tire_age` | `_corner_tire_age_summary` | kept (`runs.json`), consumed `[]` | G8 |
| `tire_learning` | `race_foundations.build_tire_learning` | kept (`runs.json`), consumed `[]` | G18, G8 |
| `race_investigation.comparisons`, `hypotheses`, `actions`, `driving_corrections` | `race_investigation.py` | kept, parsed, undrawn | G6, G12 |
| `strategy.forecast.race_plan_decision`, `pit_assessments[]` | `analysis_engine` strategy | kept, consumed, unread | G10, G11, G9 |
| `pit_service.penalty_served_s`; `damage_repair.episodes[].timing.repair_work_completed_s` | runs / damage repair | kept, consumed, unread | G3 |
| `tire_observation.*`, `driving_load.*` on runs | runs | kept, consumed, unread | G8 |
| `race_card` (analyze result, not `analysis_view`) | `race_card.build_race_card` | not stored by the server | G10 |
| MCP tools never wired | `plan_iracing_race` (G11); `review_test_day` (G13); `save/list/load/evaluate_driving_trial` (G12); `garage61_auth_status`, `sync_garage61_references` (G23); `iracing_strategy_history` (G11); `query_iracing_telemetry`, `find_iracing_telemetry_events` (G24 packet); `inventory_iracing_data`, `iracing_companion_dashboard` (G27); `copy_iracing_setup_to_coach` (G19); `reclaim_report_storage` (none); `recommend_open_setup_tuning` (superseded) | | |

## Appendix B. The asks the catalog missed (Codex audit, `requests-catalog-AUDIT-codex.md`)

A-rows in the audit's table order. "Relayed" means he supplied a brief written in another voice: it binds the work, but it is not proof he authored every clause. Dates are 2026, UTC. Where an A-row is a product ask it names its entry; otherwise it goes to the MASTERY "unrecorded" list.

| A | Date, source | His words (or the relayed brief) | Lands in |
|---|---|---|---|
| A1 | 08-13, C:14 | "`SessionLapsTotal` can be an explicit fallback/cross-check." "Test partial recordings, conflicting sources, YAML-only data, and time-limited races" (relayed ruling) | G2, G11 |
| A2 | 08-13, C:19 | "replace unrestricted metadata searching with ordered, schema-specific paths. Include compatibility paths for older analysis artifacts" (Garage61, relayed) | G23 |
| A3 | 08-13, C:20 | "Do not include ragged-column policy or performance optimization in the same patch." "give Claude one explicit read-only IBT rather than broad access to `Documents`." "Profile only after the correctness patch" | MASTERY, unrecorded (process) |
| A4 | 08-15, X:50220 | "personal simulator PC only. proceed with this expectation." "in the future I might consider giving it to a couple of friends or making the github repository public" | MASTERY, what Coach is for |
| A5 | 08-15, X:50232 | "setup source: I don't understand this either. if you can read a source then use it." "live channel: why would a live channel be missing? either it's available always or it's not? need better information here" | G19, G20 |
| A6 | 08-15, X:50287 | "each model should develop on its own github branch. there will be an implementation debate again afterwards before merging." | MASTERY, unrecorded (process) |
| A7 | 08-15, C:312; 08-16, C:346 | Excluded from the August authorization: "access to Joshua's real telemetry, archive, setups, credentials, or Garage61 account"; "unrestricted network access"; "Do not change repository visibility, delete branches, rewrite history, force-push, or merge into main." (relayed) | MASTERY, standing rules note |
| A8 | 08-15, C:328 | "Every implementation handoff must include commit SHA, changed-file manifest, tests run, evidence tier, known limitations, and rollback notes." "add at least one adversarial or fault test." (relayed) | G26 |
| A9 | 08-16, C:1438 | "Target six substantive handoffs." "No new plan documents or plan-review rounds." "Block only demonstrated P0/P1 correctness, privacy, security, contract, fail-open or data-loss defects." (relayed) | MASTERY, unrecorded (process) |
| A10 | 08-16, C:1514 | "One typed RacePlanDecision owns exact fuel range, stops, margin, one-green-lap reserve and qualified observed-caution scenario." (relayed) | G11 |
| A11 | 08-16, C:1493, C:1533 | "Preserve required-field failure and unknown optional fields." "Define compatibility windows." "Supply rollback, rebuild-from-raw, migration-resume and old/new-reader specifications." (relayed) | G27, G29 |
| A12 | 08-16, C:1525 | "Purpose belongs to identity." "Implement Q/R x source-shape matrix." "HTML is evidence." "Only validated STO permits Load." (relayed) | G14, G17, G19 |
| A13 | 08-16, C:1519, C:1565 | "Typed evidence-backed clean, damage, repair, missing and live states." "numeric Race Coach backend"; "evidence-linked AI packets and tool authority"; "Garage61 isolation, status and fitness-vector/shape"; "replay phase, cadence and clock" (relayed) | G22, G23, G24 |
| A14 | 08-17, X:83586 | "Not the email. Don't send email. Put together the information for me to send it." | G23; MASTERY, standing rules note |
| A15 | 08-17, X:83588; 08-18, X:83619 | "His question is how will giving me this access help other people"; "there is a risk of stealing all the data and rendering Garage61 useless. I think that needs to explicitly acknowledged and encourage Alex to defend against that (give ideas)" | G23 |
| A16 | 08-18, X:83659 | "individually approved public OAuth applications, narrow comparison searches, small result sets, firm usage limits, short-lived downloads, full respect for privacy settings, audit logging, and immediate revocation. Aggregate results could also replace raw telemetry where possible." (his proposal to Garage61) | G23 |
| A17 | 08-18, X:83681 | "the entire idea is that I hope for it to be a personal project." "I want them to be able to see what I produce and integrate any of the features/inspirations into their own website. that would not bother me." | MASTERY, what Coach is for; G23 |
| A18 | 08-18, C:1728 | "a "scratch build" where we start from a fixed setup and tune it" | G17 |
| A19 | 09-20, X:83733 | "review all previous chats, all commits, and all development on this project. look for questions/complaints/specifications/and so on." "create a document (including screenshots) that explains how it functions today and what development you think you should take on." | MASTERY, unrecorded (this document's own lineage) |
| A20 | 09-21, X:83761 | "no coding. just giving feedback. I'll tell you when to code." | G28; MASTERY, standing rules note |
| A21 | 09-20, X:83742; 09-22, X:83849 | "Can you serve the pdf at a temporary website or something so I can open it on my phone"; "Show me some screenshots of the app pages to give you critique. I'm on mobile at the moment." | G26, G28 |
| A22 | 09-25, C:2210, C:2245 | "a graphic showing the telemetry of your fastest 3 laps vs my fastest 3 laps"; "use the setup from my fastest lap and try to reproduce the fastest lap"; "iterate/develop/learn/try until you can beat my fastest speed. use the IRP R (race) setup" | G23 (reference laps from the Lab), utility |
| A23 | 09-25, C:2221 | "you can change the steering and throttle sensitivity/settings to whatever you need it to be." (the inputs must stay human-reproducible) | utility; MASTERY, unrecorded |
| A24 | 09-25, C:2231; 09-26, C:2730 | "is it possible to get a window to watch your test drives on the other pc?"; "There is a tinny double sound type thing coming through the tv so make sure only capturing sound once" | utility; MASTERY, unrecorded |
| A25 | 09-26, C:2728, C:2734 | "During goal 5 I also want you to keep track of tire wear with all the setup changes"; "Add a 30-minute time slot after goal 5"; "Then, I want a 2-hour goal 6 where that optimal setup is used and the driver does 30-lap runs" | G13, G18 |
| A26 | 09-26, C:2748 | "after crashing it should hold the brakes and go back to the stall faster. Right now it seems like it waits for the car to stop rolling" | utility; MASTERY, unrecorded |
| A27 | 09-27, C:2789 | "After the debate I want you to start a 6-hour session to learn more and fill in some of the gaps that your debate with codex unveils." | G18 |
| A28 | 09-27, X:83929 | "Reproduce every numeric claim in FINDINGS.md"; "Mark each claim reproduced, mismatched (with your number) or not reproducible."; "test alternative functional forms"; "check identification (data range, confounding) and the structure of the residuals"; "Rank the top 10 experiments by information gained per sim-minute." (a brief he pasted) | G18 |
| A29 | 10-02, X:83952 | "the telemetry monitor that can be opened from clicking the app in the system tray which will show whatever the user configures real-time from the live data"; the web version must keep the smooth chart and map cursor | G20, G26 (exists as the overlay; verify on the rig) |
| A30 | 10-03, X:83979 | "loss or duplication of a recording across crash, restart, disk-full, NAS-down, mini-PC-down and partial-chunk cases"; "any network transfer or hashing that could run while the car is on track"; "streaming continues until the session really ends while anyone watches" (relayed review brief) | G2, G20, G26 |
| A31 | 08-15, X:50232, X:50285 | "corner coaching: don't understand the question"; "21. don't understand" (confusion, not delegation or prohibition) | G7; MASTERY, unrecorded correction (Q71, Q163) |
| A32 | 08-15, X:50301 | "if there is some extra permission or capability we should add to maximize the usefulness of this coaching tool... that is the goal... so propose it." (no "read-only") | G24 |
| A33 | 09-27, C:2982 | "Telemetry data from my past races to scrape setup" (the dropped qualifier in Q124) | G17 |
| A34 | 08-15, C:55; 08-20, C:1771 | "Preserve backend authority for telemetry math" (relayed); "I also told you to eliminate python from the project and that too is unresolved." (backend authority, not Python's permanence) | MASTERY, standing rules note |
| A35 | 10-04, C:3701 | "i hate this at the top, why would i want to resume. i'll just click the line again" (the banner's removal is his; "reopen silently on the last tab" was the implementation's reading) | done (#1f); MASTERY note |
| A36 | 10-06, C:4040 | "I would like some gentle smoothing on the traces I guess? like interpolation? and more data points on the chart? I don't want the data to become fake." (640 bins and monotone curves were implementation choices) | G28 |
| A37 | 10-07, C:4383 | "why are some "best" missing?" (an explanation and a fix, not a figure on every race) | G1 |
| A38 | 10-07, C:4401 | "should these sections maybe grow to entire sectors?" (a proposal; "finer splits" is not his) | G4 |
| A39 | 10-07, C:4411 | "on a race where I wreck out and leave it doesn't have the rest of the race and where I really finished I guess?" (the real classification, not only a DNF reading) | G2 |
| A40 | 10-04, C:3910; 10-05, C:3981 | "agentic prose"; "the extra telemetry traces have agentic prose" (remove unhelpful explanatory prose and communicate visually; not a rule to delete every sentence about the software) | G28 |

Date corrections the audit made, carried here: Q245's installer quote is 2026-08-20 01:23 (a different installer request is 08-18); Q227's "stop asking for permission" is 2026-08-15 23:43; Q254's "ping me when the build waves start" is 2026-10-03 00:20; Q230's exact-path deletion rule is from an 08-16 summary and "Do not delete any file" is a separate 08-17 instruction; Q220's "outright lie" line is clarification answer 3; Q6's claim that pit pictograms superseded the Back / Pit entry / Pit exit labels is unsupported (different elements); Q104's "Do not recommend a garage change for a fixed session" is not "nothing for fixed sessions"; Q27's "road-wheel angle" is an interpretation of ratio-independent steering, not his term.

## Appendix C. Catalog rows -> entries (gap rows only)

| Entry | Rows | Backlog |
|---|---|---|
| G1 | Q57, Q58, Q60, Q61, Q149, Q191, Q205 | #85-#97 |
| G2 | Q17, Q172 | #13 |
| G3 | Q40 | #28i, #39 |
| G4 | Q25, Q59 | #94 |
| G5 | Q20, Q24, Q45, Q50, Q133 | #61, #62 |
| G6 | Q7, Q19, Q23, Q24, Q70, Q138 | #43, #45, #66 (his removals) |
| G7 | Q64, Q65, Q66, Q67, Q69, Q71 | |
| G8 | Q13, Q14, Q82, Q129 | |
| G9 | Q4, Q11 | #5 |
| G10 | Q1, Q13, Q19, Q73, Q74 | |
| G11 | Q13, Q69, Q72, Q75, Q76, Q77, Q78, Q79, Q80, Q142, Q143 | #9 |
| G12 | Q19, Q105, Q156 | #10 |
| G13 | Q105, Q208, Q214 | #10 |
| G14 | Q144, Q145 | #6 |
| G15 | Q111, Q112, Q119 | #24 |
| G16 | Q103, Q106, Q113 | |
| G17 | Q78, Q102, Q124, Q125 | |
| G18 | Q127, Q128, Q129, Q130, Q131, Q132 | |
| G19 | Q107, Q108, Q109, Q110, Q114, Q126 | #12 |
| G20 | Q68, Q91, Q94, Q96, Q164 | #26 |
| G21 | Q85, Q95 | #7 |
| G22 | Q151, Q152 | #8 |
| G23 | Q134, Q135, Q136, Q137, Q138, Q140, Q141, Q142 | |
| G24 | Q106, Q153, Q154, Q155, Q157 | #11 |
| G25 | Q62 | #27 |
| G26 | Q98, Q99, Q224, Q245, Q247 | |
| G27 | Q16, Q174 | |
| G28 | Q175, Q177, Q178, Q183, Q184, Q196 | #17 |
| G29 | Q44, Q171 | #32, #42, #84 |

Rows not listed are done, are operating or working-style rules (sections L, M, N, O of the catalog), or were superseded by his later words.
