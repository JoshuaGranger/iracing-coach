# Rewrite backlog (owner feedback)

Items Joshua has raised or confirmed since the v1 candidate, in the order they arrived. "We will work through them later" (2026-10-03). Evidence for the restore items: the Explore comparison of the last desktop app at `3ff5e87` with the web app (see the conversation of 2026-10-03; file names and line counts below are from that comparison).

## Open

| # | Item | Size | Where it lived before | Notes |
|---|---|---|---|---|
| 5 | Details tab: stop, tire and per-run fuel cards, race grades ("Race review" in 0.14.2) | server view + web | `RaceTechnicalData.razor` (930), `RuntimeMapper.cs`, `analysis_engine.build_technical_insights`, `_race_grades` | Parked by D8; worker already produces `technical_insights`. |
| 6 | Qualifying review with grades | server view + web | `AnalysisWorkspacePage.razor` | Not ported, unrecorded. |
| 7 | Live racing line and driving traces on the Live page | web (+ live frame fields) | `LiveTelemetryVisuals.razor`, `live-telemetry-chart.js` | |
| 8 | Replay | rig capture + server + web | `RaceReplayWorkspace.razor`, `.ircr2` capture | Parked by D8; needs full-field capture on the rig. |
| 9 | Plan | server job + web | `PlanningPage.razor`, `race_plan_decision.py`, `strategy_model.py` | Parked by D8. |
| 10 | Test day review and saved Tests | server job + web | `TestDayView.razor`, `test_day.py`, `driving_trials.py` | Parked by D8. |
| 11 | Ask | server + API key | `RaceCoachPanel.razor`, `CoachEngine.cs`, `race_coach_packet.py` | Parked by D8/D10; API key like the g-house apps. |
| 12 | Bulk `.sto` harvest (values for the setup files never driven) | iRacing-utility + sim time | — | Harvester built (`iRacing-utility` d82ce78); pilot captured 1 file. 8 of 37 owned cars hold unknown files: 207 loads cover 464 files; 706 files belong to 27 unowned cars and cannot be harvested. Paused on #16; resumes at Supra file 2. The next Supra session starts on the AtlantaSS setup (fails tech) until the restore step ships. |
| 16 | **Setup identity counts derived keys** (found by the harvest, 2026-10-03): every garage visit creates two "unnamed" setups because the sim recalculates `Chassis.FrontArb.Preload` and `setup-keys.json` does not exclude that leaf; the migration's `.htm` exports also show as "Unnamed" (rows 53-68). Fix in progress: derived keys out of the fingerprint, a re-fingerprint merge on the server, `.htm` names from the file name. Setups 69 and 70 merge into 71 with that fix. | contract + coach-ibt + server | — | In progress. |
| 13 | Phase 4 of the port for recordings over ~400 MB (the 2 GB Kansas file) | server | — | Only if wanted; Python remainder is 16 % of wall time. |
| 14 | Bot test days record under Joshua's account | server + utility | — | Decide whether to flag or separate them. |

### Left from the round of 2026-10-04 (web round 1)

Follow-ups of #1 to #4 that this round could not close, and why.

| Item | Why it is open |
|---|---|
| Barlow Semi Condensed, the display font's bundled fallback | Needs a font download and the owner's approval. Bahnschrift covers Windows; other systems fall back to Segoe UI or sans-serif. |
| `148 values` on Setups (design) | `SetupSummary` has no `value_count`; the library shows `Values known` until the server sends it. |
| Units of exported-sheet values (audit U21, partly done) | The server stores only the export's raw text (`server/src/handlers.rs`); snake_case leaves and readout rules are fixed in the web, units need the server. |
| Same file name, different values (audit U9) | Waits for the setup-identity fix (#16): derived keys out of the fingerprint. |
| Fuel gauge scale | The car's capacity (`DriverCarFuelMaxLtr`) is not exposed; the pit pictogram's gauge scales to the largest recorded run-start fuel. |
| `pit_stops` and `sector_times_s` in `test-data/g2` | The committed fixture predates projection version 2; the web uses the server fields when present and computes from the runs view and the session clock otherwise (the G2 harness exercises the fallback, the mock and unit tests the server fields). Regenerating the fixture is W6's. |
| Series and Fixed/Open after `rebuild-index` | Rows rebuilt from the NAS carry `series_name` and `is_fixed_setup` null: no badge, no Setup filter match, until a recording is re-ingested. |
| Setups filters across devices | Sessions saves its selection as `session_filters`; Setups keeps its Car, Name and Raced choice for the visit only (`setup_filters` holds one car, the page selects several). |

## Done

- #1 One visual language (2026-10-04): Direction B "Broadcast" pages with Direction A's fixed-column boards, race colour tokens, display type, interaction in light only (`design/README.md`).
- #1a Text size (2026-10-04): larger type scale and a per-driver Default/Larger/Largest step saved in prefs (`text_size`); `perf/type-scale.mjs --check` passes.
- #1b Time-delta zero line (2026-10-04): fastest clean lap of the selection (complete, green, no pit, no incident, on track), else fastest complete lap, named in the header (`Δ vs lap 6`); laps 2, 6, 7, 8, 9, 40 now zero on lap 6.
- #1c Lap rail (2026-10-04): strict column grid, 1.76x wider, one selected/spotlight/focus-visible state model, sector blocks, flags, chips, pit pictogram.
- #1d Agentic prose (2026-10-04): disclaimer sentences removed; incident `L50 · x2`; the banned-phrase grep over src, mock, e2e and perf is clean.
- #1e Live update rate (2026-10-04): no control; always 60 Hz, a frame-time governor steps down to 30 and 10 Hz.
- #1f Resume banner (2026-10-04): removed; a session reopens on its last tab silently.
- #1g Analyzed chip (2026-10-04): only Analyzing, Queued, Deferred · reason, Failed · reason with Retry, Not analyzed with Analyze.
- #1h Sessions colour (2026-10-04): event cards with green/caution dots, ▲/▼ position change, event-best lap, stops, long run, pace, fuel.
- #1i Sessions filters (2026-10-04): searchable multi-select Car, Track, Series, Session, Setup with removable chips (one component with Setups), saved as `session_filters`; no Tests, no tab strip, no search box.
- #1j Setups page (2026-10-04): car cards, values known/unknown at a glance, raced flag, versions and garage captures stacked, source in the detail only.
- #1k Fixed/Open (2026-10-04): badge per event and per differing session from `is_fixed_setup`; Setup filter.
- #1l Phase selector (2026-10-04): P · Q · R segments; a phase that cannot open is disabled and names its state.
- #1m Pit pictograms (2026-10-04): car from above with changed tires lit, fuel gauge, lap and stop time, in the rail and on the chart marker; from the race view's `pit_stops`.
- #1n Position chart (2026-10-04): one hoverable dot per lap (`Lap N · Pn`), gain/loss segments, readable P axis, caution shading.
- #1o Scrollbars (2026-10-04): themed once at the root from the tokens.
- #2 Lap rail detail (2026-10-04): sector blocks (server `sector_times_s` when sent), pit pictogram, flag column, incident and status chips.
- #3 Session list figures (2026-10-04): green/caution laps, longest run, pace trend, fuel on every tile.
- #4 Chart full screen, saved layouts New/Copy/Delete persisted to the driver's trace layouts, tire-wear row (2026-10-04).
- Web semantics audit (2026-10-04): 60 of 62 findings fixed (T1-T14, R1-R8, S1-S5, L1-L9, K1, U1-U8, U10-U20, U22-U25); U9 and U21 are listed above.
- Five rig-test sessions removed from the library (2026-10-03).
- e2e test setup entries removed (2026-10-03).
- #15 The Coach container starts while the NAS is unreachable (2026-10-03): the Coach's NAS folder is a soft host automount, bound into the container with `rslave`; measured with the automount idle, not proved against a real outage or a reboot (`acceptance/server-release.md`, `4c3c5f0`; the g-house units are `a15e066d`, unmerged).
