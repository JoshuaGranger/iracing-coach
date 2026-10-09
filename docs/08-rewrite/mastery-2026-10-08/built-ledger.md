# iRacing Coach: what exists today versus what existed before

Built read-only from the repository at `C:\Users\joshu\source\repos\iRacing Coach`, `main` at `0bf443a` (2026-10-07, 407 commits, first commit 2026-08-03). "Before" means the retired Windows desktop app (`companion-app/`, WPF + Blazor, C# coordinator + Python MCP backend), last present at `3ff5e87`, deleted in `f2d35fd` (2026-10-03). "Today" means the rewrite: Rust collector on the sim PC, Rust server + Python 3.12 worker (with native Rust decode/kernels/writer stages) in a container on the mini PC, raw recordings on the NAS, TypeScript/Preact web app.

How the evidence was gathered: docs read in the requested order (capability-status, all `06-reality` snapshots, gaps doc, owner decisions, backlog, web-round-2-plan, acceptance records, releases, the five workflow docs, the visual redesign), `git log` over all 407 commits, `git ls-tree` of `3ff5e87:companion-app` (666 files), and then the code: `web/src` (about 26.4k lines non-test, 12.8k lines of tests), `server/src` (18.0k), `collector/src` (14.0k), `crates/*` (38.7k), `iracing-coach/.../scripts` (52.9k lines Python, 27 MCP tools). Claims about code say what a grep or file read showed; claims about behaviour on a real rig or phone come from the acceptance records and are marked as such.

Conventions: "backlog #n" is `docs/08-rewrite/backlog.md`; "D1..D15" are `docs/08-rewrite/owner-decisions-2026-10-02.md`; "RA-, LT-, SU-, PT-" are requirement IDs in `docs/02-workflows/*.md`. Nothing in the rewrite is "Accepted" by Joshua for any scope (snapshot 0.19.0 and `capability-status.md` both say so).

Headline numbers: the desktop app had 42 Razor files (12.3k lines), 10 JS files (3.6k), a 20.4k-line C# coordinator (52 files); the web app has 8 page files (drivers, sessions, setups, setup-detail, baseline, tuning, live, and a hidden component gallery) plus the investigation workspace (4 tabs). The top bar has three destinations (Sessions, Setups, Live); the desktop had four plus Settings (Sessions, Plan, Setups, Live, Settings).

## Summary

Kept at parity or better: Traces workbench (cursor, 75 signals, layouts, 640 bins), Runs, Live tiles and the rig overlay, Setups library and compare, Progressive Tuning (deterministic), recording capture and archive, track maps.

Biggest reductions from the desktop app to the web app (all parked by D8 or removed on Joshua's request; the data is preserved by the D12 import and the worker archive):

1. **Race planning, fuel and pit strategy** (Plan page, `plan_iracing_race`, stop windows) - no page, no server job.
2. **Details / Technical data and race grades** (Pit, Tire, Fuel, Racecraft overview; A+ to F rubric; Race Card) - not served to the UI.
3. **Test day and saved tests** (run-by-run setup review, next-change card, Save test, later-recording Check) - backend tools exist, nothing wired.
4. **Race replay** (participant map, running order, flag timeline, `.ircr2` capture) - gone, collector writes no replay.
5. **Ask / AI coaching and optional AI tuning selection** - gone (D10: later, with an API key).
6. **Garage61** (Connections, sync, representative laps) - only a token-file seam.
7. **Settings, Connections, Diagnostics, first-run** (themes, reduced motion, health test, logs, report reclaim, error boundary) - no page; `/api/v1/status` is served but not shown.
8. **Comparison and corner coaching** (earlier-vs-later panel, hypotheses, per-corner entry / minimum / exit rows, Sections with approach, pass pairs and six channels) - comparisons computed but undrawn; Sections has four channels.
9. **Live extras** (driving traces, racing line on the Live page; crew view) - not built.
10. **Tire / capability learning** - gone.

New in the web era: Lab library, Drivers landing and any-browser access over the tailnet, passive garage setup capture, setup Versions, observed-trend lines, DNF and lap-0 grid reading, server-side native-stage analysis (cold 203 MB race 15.3 s -> 8.5 s).

Not built but promised for wave B: Crew Chief tab with a per-car field stream (#26) and the Driver page (#27).

---


## Section A: feature areas

Status words: **kept** (same capability, possibly rebuilt), **reduced** (part of it gone), **removed** (nothing in the web app), **new** (did not exist before). "Backend computes" means the Python worker (or its Rust twin) produces it in `analysis.json` or an MCP tool; "no UI shows" means no page, API route or view file delivers it to the browser.

### A.0 Where the backend output stops (applies to every row below)

The worker's analysis document has 25 top-level blocks (`contracts/analysis-view-v1.schema.json`). `crates/coach-views` (projection version 5) forwards only some of them to the browser:

| analysis block | In the API / views? | Read by the web app? |
|---|---|---|
| `identity`, `race_summary`, `laps`, `runs`, `track_geometry`, `track_profile`, `lap_traces` / `lap_trace_columns` | yes (race, runs, track, trace catalog, CCOL traces, tuning views) | yes |
| `race_investigation` (comparisons, hypotheses, actions, sections, timeline, run trend and fit, pass pairs) | yes (investigation view) | partly: sections, laps, run fits and pass data are read; comparisons, hypotheses, driving corrections and actions are parsed in `investigation-model.ts` but not drawn (removed on Joshua's request in rounds 3-4: #43, #45, #66) |
| `damage_repair`, `strategy`, `conditions` | yes (race view) | partly: incident events, fuel forecast and per-lap conditions only |
| `race_grades`, `field_pace`, `data_quality`, `corner_tire_age`, `tire_learning` | yes (race / runs views; the OpenAPI lists `x-consumed-fields` for them) | no (the Sections `Tires` column reads `tire_set_lifecycle` from the runs view's run objects, not these blocks) |
| `technical_insights`, `strategy_planning`, `pace_attribution`, `groove_evolution`, `race_replay`, `setup_telemetry`, `garage61_representative_laps` | no: type-checked then dropped by `coach-views/src/lib.rs` (`REQUIRED` / `OPTIONAL_OBJECTS`); they exist only in the worker's stored `analysis.json` (40-80 MB per session) | no |

MCP tools (27 in `contracts/mcp-tools.v1.json`). The server calls 8 (`server/src/handlers.rs`): `discover_iracing_sessions`, `analyze_iracing_race`, `catalog_iracing_setups`, `compare_iracing_setups`, `build_open_setup_package`, `recommend_structured_open_setup_tuning`, `record_open_setup_feedback` (the tuning outcome job), `iracing_setup_history` (plus `import_legacy_tuning_experiments` inside the one-time D12 import). Never wired to a server route or job kind: `plan_iracing_race`, `review_test_day`, `save_driving_trial`, `list_driving_trials`, `load_driving_trial`, `evaluate_driving_trial`, `garage61_auth_status`, `sync_garage61_references`, `iracing_strategy_history`, `query_iracing_telemetry`, `find_iracing_telemetry_events`, `inventory_iracing_data`, `iracing_companion_dashboard`, `iracing_knowledge_cache_status`, `archive_iracing_knowledge`, `copy_iracing_setup_to_coach`, `reclaim_report_storage`, `recommend_open_setup_tuning` (the non-structured form). Server job kinds: discover, analyze, project, archive, restage, setup_files, setup_compare, baseline, tuning_recommend, tuning_outcome, tuning_history, import, snapshot, reanalyze.

### A.1 Library and race analysis

| Area | Desktop app (up to `3ff5e87`) | Web app today | Backend computes, no UI shows | Status |
|---|---|---|---|---|
| Sessions library (was Home, then Sessions) | Home race desk (0.9.3 to 0.18.0), replaced by one **Sessions** library in round 1 (2026-09-23): events grouped race / qualifying / practice / offline, search box (track, car, series), All / Races / Practice and tests filter, saved-Tests strip on top, test-day grouping, archived analyses stay listed when the recording is gone, unreadable-report notice, quiet background analysis queue with status chips and a job tray | One card per event (`web/src/sessions/`) with R, Q, P lines. Race line: finish with up/down change against the official grid, `DNF - L14`, green and caution laps, best and pace (green laps, traffic included) with trend, stops, burn per lap, long run. Practice: Pos x/N, best, long run, fall-off. Qualifying: Pos, lap 1, lap 2. Filters Car, Track, Series, Session, Setup (saved in prefs), phase toggles R/Q/P, track silhouettes, Fixed/Open lock, Analyze / Retry / Queued / Deferred chips, last-tab memory, "Live now" button, 24 events then "Older sessions". Lab library only through `?library=lab` | nothing material unused | **reduced** (no search box by design #1i, no saved tests, no test-day grouping, no job tray) and **new** (cards, grid-referenced finish, DNF, Lab) |
| Race tab (progression) | `RaceInvestigationView` + `RaceProgressionChart`: lap-time progression with screened laps, run trend, caution, service and incident marks, position; every backend comparison with exact laps, fuel, exposure, track temperature, explanation state; hypotheses, "Show evidence", Save test; grade drawer | Race chart: lap dots (hollow = screened for a non-traffic reason, 55 % fill = traffic lap), position line starting at lap 0 on the official grid, caution band with caution lap times, pit pictograms (tires lit, fuel gauge, stop time), incident tags `x2`, one observed-trend line per run with s/lap, runs-only legend, per-lap context toggles (Track temp, Track usage, Fuel), one text line per run | comparisons (earlier vs later groups, pace delta, fuel, throttle, traffic, corners), hypotheses, driving corrections, actions, track-evolution summary, screened pace fit (not drawn since #90), repair and penalty fields (`pit_service.penalty_served_s`) | **reduced** (comparison machinery removed on request) |
| Runs tab | `RunOverlayChart`: runs overlaid on green exposure, centred five-lap observed median, optional screened fit confined to its domain | Runs overlaid on green exposure, ordered run palette (no amber), observed-trend line, run cards (trend slope and laps, fuel, used, screened, tire laps) that toggle a run, same-tire-set colouring | fit points and domain are parsed but not drawn | **kept** |
| Sections (corner-by-corner) | `SectionInspectionPanel` + `race-inspection.js`: neutral review sections from a catalog (Bristol A-E, IRP A-G), later-minus-earlier medians for the chosen comparison, fastest vs slowest real pass pairs, bounded approach lead-in (dashed), entry / inside / exit gap, entry-speed difference, six synchronized channels (speed, time gap, throttle, brake, steering, body slip), conditional test strip | Section tabs for detected sections; three fastest and three slowest clean laps by section time (green and red shades, median as zero), each lap's real racing line on the map, Tires and Traffic columns, four traces (speed, throttle, brake, steering) with the median lap in grey and the shared cursor, `Open in Traces` | pass-pair inspection (`inspection.pairs`: entry / exit gaps, entry speed, steering pair), time-gap and slip channels for a section, the `review_sections.v1.json` catalog, per-corner entry / minimum / exit speed and brake rows (`Comparison.corners`) | **reduced** (4 channels not 6, no approach, no pair inspection) and changed (ranked by section time) |
| Traces (telemetry workbench) | `TelemetryWorkspace.razor` (2,604 lines) + cursor JS: lap rail, aggregate track map, one to ten trace rows (primary + secondary signal), about 74 signals, drag-drop toolbox, global named layouts, spotlight, full screen, hover cards, pit popovers | Same workbench in TypeScript: lap rail (flags, time, delta, sectors, fuel, incidents, pit pictogram, conditions on hover), Green / Clean filters, Fastest / Best three / Clear, map modes (Traces, Speed, Throttle, Brake, tire stress) rotated to the long axis, 75-signal catalog, 640 bins per lap, continuous interpolated cursor, drag-drop toolbox, layouts select / New / Copy / Delete, Spotlight of drawn laps, full screen; zero requests on pointer movement; fits 1280x720 and 1024x768 | nothing hidden; channels outside the default set are fetched on demand | **kept** (layout rename absent) |
| Grades and Details (Technical data) | Details tab: 2x2 overview with drill-ins (Pit strategy incl. two-tire vs four-tire retrospective, Tire management per-corner tread / temperature / pressure and learned prediction, Fuel management, Racecraft and pace); race grades A+ to F (`race-execution-v2`: Pace 30, Consistency 20, Tire 20, Strategy 15, Racecraft 15); Race Card | none. Parts of the data show as trace signals (tire temperature, pressure, wear, fuel) and as per-run fuel and tire-lap figures | `race_grades` (served, unread), `technical_insights` (dropped by projection), `strategy`, `tire_learning`, `race_card` (`race-card-v1`) | **removed** (backlog #5; parked by D8) |
| Qualifying review | Qualifying kept a one-screen combined review (corner comparison, grades) separate from the race tabs | Qualifying is a P, Q, R phase opened in the same four-tab investigation; the Sessions Q line shows Pos, lap 1, lap 2 | grades, corner comparison | **reduced** (backlog #6 open) |
| Replay | Replay tab (`RaceReplayWorkspace.razor`, 1,262 lines): participant map, leader / lap rail, running-order grid, flag timeline, known-only comparison, player telemetry; `.ircr2` v2 live-replay capture up to 60 Hz; replay cache | none; the collector writes no `.ircr2` (no on-track disk work) | `race_replay` block, `replay_manifest.py`, `live_replay_v2.py` | **removed** (backlog #8; parked) |
| Test day | Test day tab (`TestDayView.razor`, 874 lines): overlay of every run by lap of run, one row per run (setup change, early / late windows, fall-off, Faster / Slower / Within scatter / Not comparable, tires), one next-change card; discovery tags test days | none; Offline Testing sessions list as Test lines; offline stints join one Coach session only while the setup is the same (`offline_sessions.rs`, #32); runs visible in Runs | `review_test_day` (`test_day.py`, 2,059 lines, `test-day-review-v1`) | **removed** (backlog #10) |
| Saved tests (driving trials) | Save test from a passing comparison; Tests at the top of Sessions; Check against later recordings (Improved, Worse, Not established, Not comparable); `TrialAction` / `TrialDetail` | none | `save/list/load/evaluate_driving_trial` (`driving_trials.py`) | **removed** (backlog #10) |
| Track maps | Exact-configuration vector geometry per recording, pit layers, approach dash | Same geometry from `/analyses/{id}/track`; centreline from clean racing laps (profile v24), rotated so the long axis is horizontal, silhouettes in Sessions, section segments, real racing lines | geometry confidence / provenance beyond what the map reads | **kept**, improved |

### A.2 Setups and tuning

| Area | Desktop app | Web app today | Backend computes, no UI shows | Status |
|---|---|---|---|---|
| Setups library and compare | **Setups** page: library with search and Car / Track / Season filters, `.sto` files vs values embedded in recordings, values from the recordings that loaded a file unchanged, Compare keyed on iRacing car path (changed vs unknown vs identical, readouts apart), "Save a copy" of a `.sto` into the Coach archive, link into tuning | Cars as collapsible sections of 10 rows with "Show 10 more", Car / Name / Raced filters, Raced marks, a divider for cars whose files have no decoded values, per-car accent. Detail: grouped values, garage readouts and run readings apart, sightings, **Versions** board (changed parameters per version, recorded runs), Compare, notes, `Tune this setup`. The library fills itself from recordings, garage `.htm` exports and passive garage capture (collector two-moment rule) | `iracing_setup_history`; `copy_iracing_setup_to_coach` (not exposed; the server stores setup blobs); units of exported-sheet values (audit U21) | **kept**, plus **new** (garage capture, versions, self-filling library) |
| Find a baseline (Starting Tune) | Four-stage rail Event, Source, Checks, Run; installed-car and installed-layout selectors; season automatic; race vs qualifying never relabelled | One form (season, car, track) -> `build_open_setup_package` -> race and qualifying candidate tiles, donor and why, cautions, a four-item checks list, link to tuning | package fields beyond those shown (`readPackage` ignores, does not drop, the rest) | **reduced** |
| Progressive Tuning | `TuningPage` (903 lines) + track selector + feedback editor; evidence-contract v2: representative race, exact configuration, verified turns, structured feedback, drafts and map corrections, O'Reilly / Xfinity ruleset, one change at a time, optional AI selection among whitelisted candidates, linked result capture | `pages/tuning.tsx` (724 lines) + `tuning/model.ts`: entry from `Tune this setup`, fixed-screen turn map, stage / phase / symptom / severity / confidence cards, drafts and turn annotations through the outbox, deterministic recommendation, experiment outcome | `GET tuning/history` (not shown); AI selection gone | **kept**, **reduced** (no AI, no history); no acceptance record runs it on a real recording |
| Setup identity | Fingerprint over garage adjustments; readouts excluded | Derived keys excluded, re-fingerprint merge at server start (`setup_identity.rs`), `.htm` exports named from the file | - | **kept** |

### A.3 Live

| Area | Desktop app | Web app today | Backend computes, no UI shows | Status |
|---|---|---|---|---|
| Live telemetry page | Full page with equal-share named grid, Toolbox, 31 metric tiles, Number / Gauge / Bar / Chart forms, trend windows (15 / 30 / 60 s, 1 / 3 laps), undo, driving traces and track map as a subordinate region, 60 Hz canvas | Live page streams at 60 Hz (frame-time governor steps to 30 / 10 Hz), 32 tiles over 45 channels, direct-manipulation editor (drag, resize, swap, palette, style strip: Text / Trend / Bar / Gauge, colour, size, unit, decimals, window, ABS mark; Undo; autosave), per-device layout and "Show on rig", Waiting shapes. Verified at 60 Hz with two browsers during G4 (parked car) | per-car channels (`CarIdx*`) are read by the engine for gaps only | **kept** (rebuilt); driving traces and racing line **removed** (backlog #7) |
| Live monitor on the rig | WPF always-on-top popout (selector, scale 70-200 %, tray Exit watchdog) | Collector overlay in Rust: Win32 layered click-through window, GDI, repaint of dirty tiles only, tray (Show monitor, Move monitor, Monitor size, Pause coaching cue, Open Coach, Set server address, Exit), global hotkey, layout pushed from the server; 14 MB working set, 1.2 % of one core (G4) | - | **kept** (new implementation) |
| Coach cue | Text tile: black flag, tow, repair, fuel limit, caution, pit road, gap closing, pace band, braking overshoot; safe-glance hold | Same 15 cues in `live-channels.json`, safe-glance on by default, "Pause coaching cue"; no audio (D8) | - | **kept** |
| Crew chief | Not a screen. Six backend producers (`lap_reference`, `time_loss`, `tire_energy`, `pit_loss`, `strategy_model`, `competitor_pace`) emitted `pace_attribution`, `strategy_planning`, `field_pace`; the 0.16.0 review found they never reached a UI | none. Any browser on the tailnet can open Live (watch only by D6/D7), but there is no read-only mode, no per-car field stream, no Driver / Crew Chief switch | all three blocks (only `field_pace` is even served) | **never built** (backlog #26, wave B) |
| Driver page | none | Drivers landing (list; one driver redirects to Sessions). The driver name in the top bar is plain text, not a link | no statistics endpoint; `drivers.latest_irating` only | **planned, not built** (backlog #27) |

### A.4 Planning, AI, integrations, settings

| Area | Desktop app | Web app today | Backend computes, no UI shows | Status |
|---|---|---|---|---|
| Race planning, fuel, pit | **Plan** page (299 lines): car, track and layout, setup, reference recording, race distance, lap time, green fuel per lap, start fuel, Build plan; one decision with stop windows and a full-tank reach strip; saved plans | no Plan page. Live has `fuel-laps`, `laps-remaining`, `pit-window` tiles; the Race chart shows pit pictograms | `plan_iracing_race`, `race_plan_decision.py` (1,008 lines), `strategy_model.py`, `pit_loss.py`, `strategy_planning`, `iracing_strategy_history` | **removed** (backlog #9) |
| Ask / AI coaching | **Ask** tab (`RaceCoachPanel`): follow-up questions to a bundled Coach Engine (ChatGPT) with a numeric packet and a response validator; optional AI choice among tuning candidates | none (D10: a later phase, API key like the g-house apps) | `race_coach_packet.py`, `coach_tool_authority.py`, `ai-coaching-output.schema.json` | **removed** (backlog #11) |
| Garage61 | Connections card: machine-bound token, status axes, sync of representative laps, fitness gates | only the seam: worker env var `IRACING_COACH_GARAGE61_TOKEN_FILE` (`server/src/worker.rs`); no route, job or UI | `garage61_client.py` (847 lines), `garage61_fitness.py`, `sync_garage61_references`, `garage61_representative_laps` (dropped) | **removed** (seam only) |
| Connections, diagnostics, Settings | **Settings**: Data and folders, Move or back up, App behavior (nine themes + custom colour, reduced motion, safe glance), Telemetry popout, Connections (Garage61, ChatGPT), Troubleshooting (health test, logs, verify installation, last error with copyable support reference), reclaimable report storage, First-run page, error boundary with Retry and Copy support details | no Settings, Connections or Diagnostics page. A "Server unreachable" banner, an outbox bar for pending writes with a 412 conflict choice, per-session analysis chips. `GET /api/v1/status` (NAS state, queue, collectors) is served and `api.status` exists, but no page calls it. Prefs fields `theme_color`, `safe_glance`, `units`, `text_size` exist in the contract with no control and no consumer | `/status`, job list and cancel (`api.cancelJob` defined, unused), admin routes (`import`, `reanalyze`, `snapshot`, `remove`, `library/*`) | **removed** |
| Lab library | none | server-side `library` (main / lab), `lab_windows`, classify repair; Joshua's list excludes bot and agent data (597 recordings filed lab on 2026-10-04); no control, `?library=lab` only (#34) | - | **new** |
| Notes | per-turn / per-phase tuning notes; no general notes (D12: general notes did not exist on the sim) | notes API for session, setup and analysis (`NoteSubjectKind`); `NotesPanel` is mounted only on setup detail | - | **new**, partial |
| Tire and capability learning; wet; multiclass | NASCAR-first local tire model (schema 1, three-session floor); wet-weather and multiclass hidden, not implemented | none; wet and multiclass still not implemented anywhere | `tire_model_gate.py`, `tire_learning` (served in the runs view, unread) | model **removed**; wet / multiclass **never built** |

### A.5 Capture, storage, install, access

| Area | Desktop app | Web app today | Status |
|---|---|---|---|
| Recording capture and retention | SDK reader inside the WPF app; IBT discovery in `Documents\iRacing\telemetry`; raw IBT copied into the portable archive (SHA-256, content-addressed, append-only, no pruning) | Collector uploads finalized recordings off track only (the gate aborts within 100 ms), resumable 4 MiB hash-verified HTTP, server archives to the NAS content-addressed with manifests; backfill command; nothing written under `Documents\iRacing`; 668 recordings (20.3 GB) archived at migration | **kept**, changed (G4: 0 bytes written or read on track) |
| Background analysis and jobs | quiet sequential queue yielding to live telemetry; job tray with cancel; schema-versioned UI cache | server job runner (analysis lane + light lane, interactive jobs first), native Rust decode / kernels / writer with memory admission (cold 203 MB race 15.3 s -> 8.5 s, worker peak 2.29 -> 0.25 GiB), re-projection on first request, SSE `analysis_ready`; no job tray in the UI | **kept**, UI **reduced** |
| Install, upgrade, tray | 500 MB installer and portable zip with Python 3.12 and the Coach Engine; upgrade / rollback / uninstall logic; tray Exit watchdog | collector 1.7 MB exe, `coach-collector.exe install` (HKCU Run, no admin); server released with the managed stack tool; no installer framework, no auto-update; autostart at sign-in and the tray are not exercised in any acceptance record | **changed** |
| Access and devices | one PC, one window | any browser (desktop, rig, tablet, phone) over the tailnet (`:8210` on the LAN, Tailscale Serve `:8443`), no authentication (D7), hash routes, SSE, outbox with `If-Match` / `Idempotency-Key`, 44 px phone targets; every device check is Chromium emulation (G2 at 6x CPU throttle: 2.07 s phone first interactive) | **new** |
| Migration of user data | n/a | D12 one-time import: 31 legacy analyses (28 resolved), 1 tuning draft, 4 live layouts, prefs, 49 legacy items; general notes, saved Tests, plans, turn annotations, Ask threads and experiments did not exist on the sim | **done** once |

---

## Section B: backlog items #1 to #97

Source: `docs/08-rewrite/backlog.md` (173 lines), cross-checked against `git log`, `acceptance/round2-release.md` (it also holds the round 3, 4 and 5 release records) and the code. "Released" means a release record names it; "in source" means committed on `main` with tests but no release record exists (the last release record is round 5, `bfef9d7`, 2026-10-06). The backlog's own "Open" table still lists some rows that later work finished; those are flagged.

### B.1 Done and released

| # | Item (one line) | Round / commit |
|---|---|---|
| 1, 1a-1o | One visual language (Broadcast pages, fixed-column boards, race colour tokens); raised type scale (selector later removed); time-delta zero line named; lap rail strict grid; agentic prose removed; Live update rate (always 60 Hz, governor); resume banner removed; Analyzed chip only on exceptions; Sessions colour; multi-select filters saved as `session_filters`; Setups car cards; Fixed/Open; P Q R selector; pit pictograms; position chart; themed scrollbars | web round 1, `932bdc4`, released `88b1b82` (2026-10-04) |
| 2 | Lap rail detail (sector blocks, pit pictogram, flag column, chips) | round 1 |
| 3 | Session list figures (green / caution, longest run, pace trend, fuel) | round 1 (reshaped later by #33, #57) |
| 4 | Chart full screen, saved layouts New / Copy / Delete, tire-wear row | round 1 |
| (audit) | Web semantics audit: 60 of 62 findings fixed; U9 and U21 listed as open (U9 waited on #16) | round 1 |
| 14 | Bot test days under Joshua's account: decided 2026-10-04 to separate them | resolved by #18 |
| 15 | Coach container starts with the NAS down (host automount, `rslave` bind) | `4c3c5f0`, 2026-10-03; measured idle, not against a real outage |
| 17 | Sessions "ick": dense rows, restraint over colour | round 2 wave A `d827360`; reshaped by #33, #50-#52, #70-#72 |
| 18 | Lab library: bot and agent recordings leave Joshua's list (597 recordings filed lab, 51 sessions left in his list) | `5a0f273`, `d827360` |
| 19 | Filters in the heading row | round 2 |
| 20 | "Values known" chip removed; exceptions only | round 2 |
| 21 | Setups cars as collapsible sections, 10 rows then "Show 10 more" | round 2; #37 made it an inner scroll |
| 22 | "Progressive tuning" heading button removed; one entry `Tune this setup` | round 2 |
| 23 | Setup detail header: name once, `Raced N x`, first seen | round 2 |
| 24 | Setup Versions: changed parameters per version plus Recorded runs (no pace claim) | round 2 |
| 25 | Live layout editor rebuilt as direct manipulation (drag, resize, style strip, Undo, touch select-then-place) | round 2 |
| 28a-28h | Investigation cleanup: no EXCL, one comparison selector (later removed by #43), caution lap dots, run palette, fit drawn, gradient lap colours, Sections state change not remount | round 2 |
| 28i | Pit popover repairs and penalties: answered "nothing drawn until extracted"; still not extracted (see B.3) | round 2 / #39 |
| 29 | Sticky run row no longer jumps on first scroll | round 2 |
| 30 | Selected segment ignores hover | round 2 |
| 31 | Tailscale app like the rest of g-house (`https://minipc.tail4e6ca6.ts.net:8443/`) | 2026-10-04, g-house `7a68f341` |
| 33 | Sessions: race prominent; practice = position, best, long run, pace loss; qualifying = position, lap 1, lap 2 | round 3 `d608ad8` |
| 34 | Library (Joshua / Lab) control removed from the UI; `?library=lab` only | round 3 |
| 35, 36 | No Clear button in the heading, no active-filter chip row; the dropdown carries and clears its selection | round 3 |
| 37 | Setups: past ten rows the car section scrolls inside | round 3 |
| 38 | Setups Car filter lists only cars with setups | round 3 |
| 39 | Pit stop detail: the "not extracted" sentence removed | round 3 |
| 40 | Run palette without amber; legend = runs only | round 3 |
| 41 | Pit halo no longer clipped | round 3 |
| 42 | Run colour by tire set (lighter shade after a no-tire stop) | round 3 (alternative "no split at a no-tire stop" still awaits Joshua) |
| 43, 45 | Earlier / later comparison machinery and bottom panel off the Race tab | round 3 |
| 44 | Dots plus the backend's smooth fit instead of a connect-the-dots line | round 3 (superseded by #90) |
| 46 | Per-lap context toggles (track temp, usage, traffic, slip proxy, fuel) | round 3; traffic and slip dropped by #61, #62 |
| 47 | Run cards toggle their run | round 3 |
| 48, 49 | Sections: three fastest and three slowest clean laps per section, section tabs, `Open in Traces` | round 3 |
| 50 | Gap between event cards | round 4 `b0b539e`, release record `fe30607` |
| 51 | Fixed/Open as a lock glyph in the card corner | round 4 |
| 52 | P / Q / R badges and phase hues | round 4 |
| 53 | Green lap count coloured green | round 4 |
| 54 | `Long run` label | round 4 |
| 55 | `P3 / 24` position over field size | round 4 |
| 56 | Finish change measured against the official grid (Talladega P5 bug) | round 4 |
| 57 | Race card shows burn per lap instead of fuel used | round 4 |
| 58 | Box around lap numbers removed | round 4 |
| 59 | Fall-off per run, fit drawn only at 50 % coverage; (b) traffic rule question answered by #67 | round 4 |
| 60 | Evidence words off the trace toggles | round 4 |
| 61 | Traffic tick dropped | round 4 |
| 62 | Wheel-slip layer dropped (no per-lap slip measure) | round 4 |
| 63 | Fuel-line gap fixed (Vegas 10/02 laps 25-28) | round 4 |
| 64 | Telemetry back on the Sections screen | round 4 |
| 65 | Track maps rebuilt from a clean racing lap (rule `clean-racing-laps-v1`, profile v24) | round 4 (S4) |
| 66 | Comparison strip removed from Traces | round 4 |
| 67 | Traffic vs open-air lap marker by side | round 5 `bfef9d7` (mark softened by #92) |
| 68 | `P4 up 8` without "from x" | round 5 |
| 69 | Lock inside the track block | round 5 |
| 70, 71 | Phase spacing and identity hierarchy | round 5 |
| 72 | Phases as stacked line items | round 5 |
| 73 | Per-car accent colour (stable hue from the car identity) | round 5 |
| 74 | Cars without decoded values below a `Files without values` divider, screen names | round 5 |
| 75 | Sections rows gain `Tires` and `Traffic` | round 5 |
| 76 | Sections map draws each lap's real racing line | round 5 |
| 77 | 640 bins per lap, continuous interpolated cursor, smooth curves | round 5 (profile v25, projection 4) |
| 78 | Focus ring only for the keyboard, app-wide | round 5 |
| 79 | Spotlight lists only drawn laps | round 5 |
| 80 | One control recipe, text centred in every chip / pill / tab (measured by e2e) | round 5 |
| 81 | Maps rotated so the long axis is horizontal | round 5 |
| 82, 83 | Lap-rail gradient key spans the rail; gutters between Fuel and Inc | round 5 |

Also done outside the numbered list: `Lab` filing windows and classify repair; offline stints join only on one setup and a stopped lap counter (1024) joins nothing (decision of #32, bug-fix wave 2, `5a0f273`); garage snapshots never mint setups; one-lap-to-green is a caution only in a Race (W2); collector CPU 2.98 % -> 0.26 % of a core (W3, build `a256511` installed on the sim `9985a93`).

### B.2 Done in source on `main`, no release record

| # | Item (one line) | Commit |
|---|---|---|
| 85 | Sessions race line read `-` on 21 of 26 races: Best and Pace now from complete green laps, traffic included (`best_green_lap_s`, `race_pace_median_s`) | `2e72953` |
| 86 | Event card phase order Race, Qualifying, Practice | `2e72953` |
| 87 | P/Q/R letter centred in its badge | `2e72953` |
| 88 | R / Q / P phase toggle chips in the heading, remembered per device | `2e72953` |
| 89 | Setups type scale brought to the Sessions list's sizes through shared tokens | `2e72953` |
| 90 | Race chart: per-run straight observed-trend line with its slope (`BackendRun.observed_trend`, `coach-views/src/trend.rs`, projection 5) | `2e72953` |
| 91 | Bug: Vegas run 2 fall-off read -0.10 s/lap because the restart lap counted; now +0.0756 | `2e72953` (regression tests in `trend.rs` and `round6-trend.test.ts`) |
| 92 | Quieter traffic mark (55 % fill dot, no ring or chevron) | `2e72953` |
| 93 | Runs tab cards: labels no longer collide with figures | `2e72953` |
| 95 | Best and race pace count laps driven on with damage; only tow / repair episode laps excluded; null bests recompute at start | `2254a1b` |
| 96 | Position chart starts at lap 0 on the official grid position | `0bf443a` (2026-10-07) |
| 97 | A race recording that stops before the checkered reads `DNF - L14` (`race_summary.finish`, analysis profile change in `analysis_engine.py`) | `0bf443a`; stored analyses show it after re-analysis |

Follow-up inside #90: the Sessions practice `Fall-off` (`longest_run_pace_loss_s`, last minus first screened lap) is not yet on the new trend definition (`server/src/overview.rs`).

### B.3 In progress, partly done, or waiting on Joshua

| # | Item | State |
|---|---|---|
| 16 | Setup identity counted derived keys (every garage visit minted two "unnamed" setups) | The backlog row says "In progress", but setup identity version 2 (`server/src/setup_identity.rs`, contract change log 2026-10-04) shipped: setups 69, 70 merged into 71 (round-1 acceptance), 72 into 73 (round-2 start). Codex Sol review still marked pending in `contracts/README.md`. Treat as done in code, row not updated |
| 32 | Offline stints and setups (decided 2026-10-04) | Rule implemented (`offline_sessions.rs`). Open question to Joshua: the seven Kansas 2026-09-28 09:23-10:11 files, his or the bot's; they stay in his list until he says |
| 28i / 39 | Repairs and penalties on a pit stop | Nothing drawn: repair time and penalties are not extracted; `pit_service.penalty_served_s` is a consumed field with no mark |
| 42 | No-tire stop restarts the run | Backend segmentation kept; alternative (a no-tire stop does not split the run) waits for his word |
| 12 | Bulk `.sto` harvest | Lives in the separate `iRacing-utility` repo (harvester built, pilot captured 1 file; 8 of 37 owned cars hold unknown files; paused on #16) |
| 13 | Port phase 4 for recordings over about 400 MB (the 2 GB Kansas file) | "Only if wanted"; session 152 is listed and deferred, 9.90 GiB estimated under the native formula |

### B.4 Open (not started in code)

| # | Item | Notes |
|---|---|---|
| 5 | Details tab: stop, tire and per-run fuel cards, race grades | server view + web; worker already produces `technical_insights`; parked by D8 |
| 6 | Qualifying review with grades | not ported, unrecorded |
| 7 | Live racing line and driving traces on the Live page | web + live frame fields |
| 8 | Replay | needs full-field capture on the rig; parked |
| 9 | Plan (fuel and stop) | server job + web; parked |
| 10 | Test day review and saved Tests | server job + web; parked |
| 11 | Ask | server + API key; parked (D10) |
| 26 | Crew Chief tab on Live (per-car field stream about 5 Hz, track map with every car, field board) | wave B; after the G4 gate; no code |
| 27 | Driver page (name in the top bar opens statistics, tabs per racing style) | wave B; no code |
| 84 | Lab-window garage observations count as `main` (A5), so Lab-only setups show in Joshua's list; name a setup made by modifying a named file `<file> - modified` | confirmed open: `server/src/library.rs` tests assert `garage_commit` counts in Main |
| 94 | Sections tabs become iRacing's own sectors (`SplitTimeInfo`), detected corner sections as finer splits | specified in the backlog (after #93); no sector-tab code (`SplitTimeInfo` is read only by the analysis engine for sector times) |

Counts: 97 numbered items (plus sub-items 1a to 1o). Released 70 (#1-#4, #14, #15, #17-#25, #28-#31, #33-#83; #28i, #39 and #42 carry the caveats in B.3), in source 12 (#85-#93, #95-#97), partly done or waiting 4 (#12, #13, #16, #32), open 11 (#5-#11, #26, #27, #84, #94). Not numbered but still open from the backlog tables: Barlow Semi Condensed font fallback (needs a download approval), `148 values` count on Setups (no `value_count` in `SetupSummary`), exported-sheet units (U21), fuel gauge scale (`DriverCarFuelMaxLtr` not exposed), regenerating the G2 fixture to projection 2+, series / Fixed-Open after `rebuild-index`, Setups filters across devices.

---

## Section C: timeline of major pivots

Commits per day (from `git log`): 2026-08-03 12, 08-04 41, 08-05 5, 08-06 2, 08-07 13, 08-08 1, 08-10 2, 08-11 1, 08-12 4, 08-14 1, 08-15 13, 08-16 53, 08-17 16, 08-18 6, 08-19 9, then nothing until 09-20 4, 09-21 11, 09-22 8, 09-23 14, 09-24 77, 09-25 48, then 10-02 11, 10-03 33, 10-04 16, 10-05 1, 10-06 4, 10-07 1.

| Date | Commits | What changed | Why (per the docs) |
|---|---|---|---|
| 2026-08-03 | `4edf203` initial source; `da8cb2e` 0.10.0; `b259c75` 0.11.0; `ed96871` 0.11.1 | Baseline 0.9.3: per-user Windows desktop host (WPF + BlazorWebView), bundled Python engine supervised by a C# coordinator over MCP, Home, Live Telemetry, Race Analysis (seven tabs), Race Planning, Setup Library, Progressive Tuning, Connections, Settings, installer. Same day: Live Monitor rebuilt as a topmost 3x2 grid (0.10.0); full Live page and monitor share one named layout, Toolbox added, user-facing **Setup Library removed** (0.11.0); live trend canvas at native 60 Hz (0.11.1) | `implementation-snapshot-0.9.3` to `0.11.1`: the Setup Library "did not support a useful decision"; real SDK behaviour was still unvalidated |
| 08-04 to 08-07 | `f315c91`..`0bb46a8` (61 commits) | Race Analysis becomes a telemetry-first workbench: vertical Runs/Laps rail, aggregate track map, shared multicolour cursor, tray-exit fixes. 0.12.0 (`bba5f53`) live dashboard studio with drag / replace; 0.13.0 (`1e7868c`) equal-share grid and session epoch; 0.14.0 (`43eb62b`) phase- and group-qualified analysis identity and `race-execution-v2` grades; 0.14.1, 0.14.2 (`0bb46a8`) cursor ownership and drag-drop trace editor | `releases/RELEASE_0.12.0` to `0.14.2`; `0.14.2` is the last accepted packaged release (root README) |
| 08-08 to 08-11 | `1b2f356`, `9e4befb`, `c5e09eb`, `8bced21` | 0.15.0 / 0.16.0: Race Analysis split into Telemetry, Technical data and Race replay; exact-configuration vector maps; global named trace layouts; Progressive Tuning evidence-contract v2; permanent raw-IBT retention; NASCAR-first tire/capability learning; replay capture v2 (60 Hz); GPS sentinel rejection; bounded 500-lap selections | `implementation-snapshot-0.15.0` and `0.16.0`: "overhaul race analysis replay and tuning" |
| 08-12 to 08-19 | `b868e24`, `4f15c14`, then 66 commits 08-15/16, `9992d47`..`83847c3` (08-17), `fb55ce3`, `b664f71` | Repository restructured for mainline development; backend hardening workstreams (evidence records, identity split, atomic containment, live-truth policy, race-plan decision authority, coach-tool authority, replay manifest); six crew-chief producers (`lap_reference`, `time_loss`, `tire_energy`, `pit_loss`, `strategy_model`, `competitor_pace`); cache fixes after the 0.16.0 review found a zero hit rate; IBT decoder ported to C# byte-identical | memory note and `docs/07-agent-review`: producers shipped but "nothing consumes them"; the app "was never compute-bound" |
| 09-20 to 09-21 | `616a3b3`, `0350ab5`, `178fb62`, `f48e4c9`..`98d023b` | 0.17.0 development round: native-rate (not 20 Hz) analysis, next-event planning (`plan_iracing_race`), the **setup library returns** (search, filters, recorded values); 0.17.1 to 0.17.7 are DESKTOP-SIM QA fixes (bare `Infinity` JSON hid 77 of 158 analyses, catalog pagination, same-content setup rows, replay `racing` phase filter, replay cache 29 s -> hit) | `testing-feedback-0.17.0`, snapshots 0.17.1-0.17.7: real recordings on the sim exposed defects fixtures hid |
| 09-22 | `67ab162`, `7e9a356`, `088fabf`, `bdd68db`, `43dbb1e` | 0.18.0 "explain the race" (Bristol pace-loss case); **north star recorded**: "I would rather an app that has 5 features that win me more races than ... 1000 features"; compact one-screen investigation study; 257-control usefulness audit; design debate (Opus vs Astra, 95.2 % agreement) | `visual-companion-redesign-2026-09-22`, `whole-app-usefulness-audit`: capability may grow, interface complexity may not |
| 09-23 | `5729896`, `1192daa`, `db00bda`, `9077917` | Development round 1: **Sessions replaces Home and the recording browser**; Race / Runs / Sections investigation; saved tests with later-recording Check; Plan follows the reviewed race; nav becomes Sessions, Plan, Setups, Live, Settings; `NavRail`, `HomePage` deleted; cold analysis 24.8 -> 21.9 s (10.3 s after the 09-24 column-wise change) | `implementation-snapshot-0.18.0-development-round-1`: "one investigation, one saved test, one plan" |
| 09-24 to 09-25 | 125 commits, `9c320c0`..`8ce7170` | Round 2 on the sim (native click-through at 1280x800 on copies of real data): Test day review, setups with values from recordings, car-path compare, report-copy reclaim, window placement, steering shown as steering applied at the front wheels (TD-037). 2026-09-25: "main is the dev branch" | `implementation-snapshot-0.18.0-development-round-2`; this is the last desktop work |
| 10-02 | `0ce6ad2` decisions, `e350617` scaffold, `9fb5223` deploy, `dd0ce49` wave 1, `514b752`, `18f57f6` | **Rewrite decided** after a 15-question interview: desktop app to a Docker stack on the mini PC, Rust collector on the sim PC, NAS for raw recordings, TypeScript web UI (D1-D15). Five measured proofs: P1 Python on the N100 failed only Frigate `detection_fps` -> staged Rust port; P2 Razor WASM 74-78 s at 6x throttle -> TypeScript SPA; P3 Rust collector 8.6 MB vs C# 276 MB; P4 NAS share 5.6-7.3 MB/s -> HTTP upload; P5 two-moment garage capture. Wave 1 builds collector, server, crates, worker, web in one commit; first mini-PC release; gate G1 and G2 pass | `owner-decisions-2026-10-02.md`: "Instead of being a local PC installation ... a docker container on my home's mini pc"; "whatever programming language(s) will maximize the performance" |
| 10-03 | `2e3b962`, `081a1f0` v1 candidate, `23d006c` design directions, `035a78b`, `4014b16`, `ae65a0a`, `c374de0`, `1f6e3ea`, `3ff5e87`, `f2d35fd`, `fd0a38c`, `7d38242`, `4c3c5f0`, `2d0c0cb` | Integration, v1 candidate released; gates G3 (garage capture) and G4 (rig, parked car) pass; collector installed on the sim; **D12 migration** (657 sessions, 668 recordings); port phases 1 decode, 2 kernels, 3 writer (byte-identical on 52 recordings); **desktop app deleted** (`f2d35fd`); native stages in the image (cold race 15.3 -> 8.5 s); NAS host automount; type scale raised | Joshua: "retire it now. not in use. go all-in" (D13); "I was never satisfied with the app. There will be criticisms and changes. Just get it up and going." |
| 10-04 | `932bdc4`, `88b1b82`, `280087f`, `5a0f273`, `d827360`, `d608ad8`, `b0b539e`, `9985a93` | **Web round 1** (Broadcast look, semantics audit 60/62, backlog #1-#4); his comments #17-#30 -> Codex debate (92 %) -> web-round-2-plan; bug-fix wave 2 and the **Lab library**; round 2 wave A released; round 3 (#33-#49) and round 4 (#50-#66) the same day; Tailscale app (#31); collector `a256511` on the sim | each round is "Joshua's complaints on the live previous round", run by parallel agents on owned paths, gated, released, stored analyses re-queued |
| 10-05 to 10-06 | `36147f2`, `bfef9d7`, `ff7bad9`, `2e72953`, `2254a1b` | Round 5 (#67-#83): traffic marker, 640-bin traces and continuous cursor, rotated maps, per-car accents, centred controls. Agent build caches had filled C: (131 GB of Rust targets removed; scratch moved to G:). Round 6 (#85-#93) and #95 in source: green-lap best and pace, observed trend lines, R/Q/P order and toggles, quieter type | backlog #67-#95; run trend is the "observed trend", distinct from the screened fit (AGENTS.md) |
| 10-07 | `0bf443a` | #96 lap 0 at the grid position, #97 a recording that stops before the checkered reads DNF (new `race_summary.finish`, worker change) | backlog #96, #97 |

What the pivots cost in capability (the reductions in one view): Plan, Details / grades, Test day, saved tests, Replay, Ask, Garage61, Settings / diagnostics, tire learning and the comparison panels were all removed or parked between 2026-09-23 (Sessions replaces Home) and 2026-10-04 (comparison machinery removed on request). Parking is recorded by D8 and the October 3 ruling, and each parked item's data is preserved by the D12 import (`legacy_items`, the worker archive).

---

## Section D: promised in the docs, not found in the code

Each line names the doc, the promise, and what a search of the code showed. "Parked" items are listed here only when a doc still states them as a requirement without saying they are parked.

### D.1 Doc and code disagree (decide which is right)

1. **`AGENTS.md` (product north star):** "track location, exact comparison and run context, and critical telemetry visible together" and "Every chart makes comparison membership, units and selection marks understandable". The web app draws no comparison anywhere: the Comparisons list, brackets, panel and Traces strip were removed in rounds 3 and 4 (#43, #45, #66), and `web/src/investigation/README.md` says "No earlier/later anywhere". The backend still computes comparisons (`race_investigation`) and `investigation-model.ts` parses them. Either AGENTS.md or the UI is out of date.
2. **`AGENTS.md` and RA-135 / RA-137:** supported slip evidence in the primary lost-time view. The Sections tab draws four channels (`SECTION_SIGNALS` in `ui/section-traces-model.ts`: speed, throttle, brake, steering); the desktop drew six (adding time gap and body slip). The per-lap slip layer was dropped (#62). Slip exists only as Traces signals (`slip`, wheel-slip channels).
3. **RA-132 / RA-133 (approach, entry / inside / exit gap, real pass pairs, cumulative time difference from a context origin):** `race_inspection.py` computes them; the web model carries `PassPair` but no component draws a lead-in, an entry gap or a cumulative difference.
4. **`owner-decisions` D6 "read-only live page":** the Live page has no read-only mode. Anyone on the tailnet sees the layout editor and can save layouts (no auth by D7). The per-car field stream (about 5 Hz, `web-round-2-plan.md` wave B) is not in `live-frame.md`, the collector, the server or the UI (no `CarIdx` stream; the engine reads car arrays only for gaps).
5. **`web-round-2-plan.md` wave B / backlog #27:** the top-bar driver name opens a Driver page. `TopBar` in `web/src/app.tsx` renders the name as a `<span>`; no route, no statistics endpoint exists.
6. **OpenAPI `x-consumed-fields` for the race view** (`race_grades`, `field_pace`, `conditions`, `data_quality`, `strategy`, `damage_repair`) says the web app reads them; it reads only incident events, the fuel forecast and per-lap conditions. `race_grades` is served and unused.
7. **`docs/README.md` names `implementation-snapshot-0.19.0-rewrite.md` as the current baseline.** That snapshot is written at `ef98f0a` (2026-10-03) and itself says `main` is ahead. No snapshot covers web rounds 1 to 6 (2026-10-04 to 10-07): their behaviour is recorded only in `web/src/**/README.md`, `backlog.md` and the round release records. `backlog.md` is also stale in two places: #16 reads "In progress" though it shipped, and its "Done" list has no entry for round 4 or round 6.

### D.2 Promised, no implementation found

8. **Saved tests and the later-recording check** (RA-134; `visual-companion-redesign`: "A later recording is the next major capability ... Saved trials reopen and survive restart"): no route, job kind or UI. `driving_trials.py` is not called by the server.
9. **Cancel from another page / progress control** (NAV-001; redesign table "Active work has a conditional progress/cancel control"): `POST /jobs/{id}/cancel` exists and `api.cancelJob` is defined, but nothing in `web/src/pages` or `web/src/ui` calls it; there is no job tray.
10. **Diagnostics** (DIAG-001 to DIAG-006: versions, root validation, last error with a copyable reference): the server serves `GET /api/v1/status` (NAS state, queue, collectors) and `api.status` exists, but no page calls it. The tray's second line ("N recordings not uploaded") is the only surface for upload problems.
11. **Settings preferences** (SET-010 theme colour; LT safe glance; units; text size): `DriverPrefs` carries `theme_color`, `safe_glance`, `units`, `text_size`; the web app reads only `reduced_motion`, `session_filters`, `setup_filters`, `last_investigation` and the active layout ids. The collector reads no prefs (`safe_glance` lives in `collector.json`).
12. **Notes on sessions and analyses** (`web/src/ui/README.md`: "NotesPanel ... for notes on a session, setup or analysis"; OpenAPI `NoteSubjectKind`): the panel is mounted only in `pages/setup-detail.tsx`.
13. **Tuning history** (`GET /drivers/{id}/tuning/history`, "failed experiments searchable", PT-046, PT-063): the page does not show it (known follow-up 8 in the 0.19.0 snapshot). The optional AI selection among whitelisted candidates (PT-050 to PT-052) has no counterpart.
14. **Penalties and repairs on a pit stop** (#28i, #39, RA-100 to RA-103): consumed fields exist (`pit_service.penalty_served_s`) but no mark or row.
15. **iRacing's own sectors as Sections tabs** (#94) and **lab-window garage filing** (#84): specified, not coded.
16. **Trace layout rename** (RA-059C names create / select / rename / delete): the toolbox offers select, New, Copy, Delete only. **Seeded Race and Qualifying live dashboards** (LMG-009): only `Default` is built in (`server/src/userdata.rs`), the four migrated layouts aside.
17. **Technical data / grades** (RA-060 to RA-060D), **Race replay** (RA-120 to RA-129), **Test day**, **Race Planning** (RP-001 to RP-049), **Ask**, **Garage61 Connections** (CON-001 to CON-008), **tire learning** (RA-085 to RA-088): the `02-workflows` documents still state them as MUST requirements. They are parked by D8, but the workflow files carry no "parked" marker; only `capability-status.md` and the 0.19.0 snapshot do.
18. **Practice `Fall-off` on the trend definition** (#90 follow-up): `server/src/overview.rs` still uses last-minus-first screened lap.
19. **Live driving traces and racing line** (LT-016, backlog #7): no map or trace panel on the Live page.

### D.3 Promised and built, but never verified where the docs say it matters

20. Real phone, real tablet, a tailnet device, and the Live page on the rig's own screen (D11): every device check is Chromium emulation on the dev PC (`acceptance/v1-candidate.md`, `e2e-mini-pc.md`, `capability-status.md`).
21. Collector autostart at sign-in, the tray menu in use, and starting the stack with the NAS actually down (`collector-install.md`, `server-release.md`).
22. Motion-dependent live engine branches on the rig: G4 used a parked car for the main windows. The later bot-driven run measured the collector at 2.3 % of a core while moving (`94d8968`, above the 1.5 % G4-3 limit); the CPU fix (`5a0f273`, recorded as 2.98 % -> 0.26 %) was installed as `a256511` on 2026-10-04, but `collector-install.md` holds only an idle sample for it. No record re-measures G4-3 on a moving car after the fix.
23. Compare, Find a baseline and Progressive Tuning on a real recording (no acceptance record exercises them); Garage61 and any AI account (not implemented).
24. The 2 GB Kansas recording (session 152) is deferred; native analysis of it has not been tried (`g1-native.md`, backlog #13).

### D.4 Housekeeping found on the way

25. `server/%SystemDrive%/ProgramData/Microsoft/Windows/Caches/` (six Windows cache databases) is still tracked in git (`dd0ce49`); the 0.19.0 snapshot flagged it as follow-up 13. An untracked `C:/Program Files/Git/root/.cache/iracing-coach/target-R4` cargo build-cache tree (another path-expansion accident from an agent run) sits in the repo root.
