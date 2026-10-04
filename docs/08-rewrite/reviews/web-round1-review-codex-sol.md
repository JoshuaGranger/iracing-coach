# Codex web round 1 investigation review

Model: gpt-6.1-sol (Codex Sol 6.1), app-resources codex.exe
Reasoning effort: high
Sandbox mode: danger-full-access (--dangerously-bypass-approvals-and-sandbox; read-only enforced only by prompt instruction)
Date: 2026-10-04
Courier note: the text below is Codex output verbatim (15 findings). A one-line probe (first line of AGENTS.md) succeeded before the review. git status --porcelain before and after the run are identical (136 lines, BOM stripped). Codex reported no browser checks were run.

---

Found **15 issues**. Review used source inspection and in-memory fixture probes; no files were created, modified, or deleted. Browser checks were not run. “Variant” below means an altered payload held only in memory.

The current backlog has no integration-record section; I used its #1b ruling and the requested 0.14.1 clean rule.

1. **Wrong result — a lap labeled Partial can become the baseline.** [trace-model.ts:445](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/geometry/trace-model.ts:445>)  
   **Reproduction:** G2 catalog variant: omit optional `complete` from lap 83, retain its `partial` exclusion. Selecting 6 and 83 chooses **83, 3.983 s** as zero; the rail still labels it Partial. It also passes Clean.  
   **Smallest fix:** reject `partial` evidence in both the clean rule and baseline pool; unknown completeness must not imply complete. T1/T2 remain incomplete.

2. **Wrong result — fallback sectors fabricate session-best times from missing coverage.** [trace-model.ts:513](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/geometry/trace-model.ts:513>)  
   **Reproduction:** G2 variant: make lap 10’s first 48 session-time bins missing. Its first sector becomes **0.000 s, session best**, because both boundaries receive the first available timestamp.  
   **Smallest fix:** apply the contract’s two-bin boundary-coverage limit; return null beyond it instead of holding distant endpoint values.

3. **Misleading — viewport fitting silently clips driving-input rows.** [cursor-config.ts:71](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/cursor/cursor-config.ts:71>), [investigation.css:466](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/investigation.css:466>)  
   **Reproduction:** geometry probe: ten rows in a 300 px surface end at **438 px**; six rows at a 20 px root in a 260 px surface end at **328 px**. Paint containment clips the overflow, with no chart scrolling.  
   **Smallest fix:** provide reachable vertical scrolling when the row minimum exceeds available height, or revise fitting so every row remains visible.

4. **Misleading — Clear leaves the previous selection’s cursor and telemetry active.** [TraceChart.tsx:98](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/TraceChart.tsx:98>)  
   **Reproduction:** G2 Traces → move the cursor → Clear. Configuration becomes null, but the existing cursor is neither updated nor disposed. The map can continue reporting the old laps beneath **“Avg 0 laps”** and “No laps selected.”  
   **Smallest fix:** dispose or explicitly empty the cursor when selection becomes empty; clear its map readouts and marker.

5. **Misleading — Clean can contain a hidden pit lap.** [TracesView.tsx:232](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/TracesView.tsx:232>)  
   **Reproduction:** G2 → Clean → Spotlight L41. Selection becomes **3, 6, 11, 41**; L41 is plotted but hidden from the filtered rail.  
   **Smallest fix:** restrict Spotlight choices to active filters, or explicitly clear conflicting filters before adding the lap.

6. **Misleading — R8 suppresses highlighting but still invents section locations.** [SectionsView.tsx:120](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/SectionsView.tsx:120>)  
   **Reproduction:** G2 track variant: replace `main_path.pct` values with null. `sectionsReady` is false, yet four section labels and the synchronized map cursor remain, positioned using point-index fractions.  
   **Smallest fix:** gate labels and inspection cursor points on `sectionsReady`, alongside the highlight.

7. **Misleading — Tire load remains blank after its channel arrives.** [TrackPanel.tsx:64](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/TrackPanel.tsx:64>)  
   **Reproduction:** G2 served with default channels → choose Tire load. In-memory render probe: **0 ribbon segments before loading, 0 after loading, 160 after changing selection**. The memo depends on the unchanged data object.  
   **Smallest fix:** pass the channel revision/load tick into TrackPanel and include it in ribbon and legend dependencies. T5 remains incomplete.

8. **Misleading — failed channel requests become permanent “Loading.”** [TracesView.tsx:113](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/TracesView.tsx:113>)  
   **Reproduction:** G2 default-channel response; return 503 for the Gear request. The rejection is swallowed, Gear stays “Loading,” and there is no retry action.  
   **Smallest fix:** retain channel-load errors and expose a retry that reruns the missing-channel request.

9. **Misleading — Race and Traces identify different fastest laps.** [RaceView.tsx:95](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/RaceView.tsx:95>)  
   **Reproduction:** unmodified synthetic fixture: Race marks **L58, 30.050 s** fastest; Traces marks **L1, 30.000 s**. Race still prefers coaching eligibility, including restart exclusions.  
   **Smallest fix:** use the same 0.14.1 clean-lap pool and incident context for both fastest markers.

10. **Misleading — post-run service appears as an in-race stop.** [rail-model.ts:152](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/geometry/rail-model.ts:152>)  
    **Reproduction:** G2 race-view variant: add a stop at L82 with `post_run_service: true`. The flag is discarded; the rail and progression chart present an ordinary Pit L82. The analyzer explicitly distinguishes service after the recorded race ends.  
    **Smallest fix:** preserve the flag and distinguish post-run service; exclude it from ordinary in-race markers.

11. **Misleading — R2 still defaults missing screening fields to affirmative evidence.** [investigation-model.ts:297](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/investigation-model.ts:297>)  
    **Reproduction:** G2 variants: remove `pace_comparable` → parsed as true; remove pass `eligible` or steering `comparable` → also true. Removing `schema_version` is accepted.  
    **Smallest fix:** validate required screening booleans and schema version explicitly, using the existing format-error path.

12. **Misleading — selecting both setup types changes results after reload.** [sessions/model.ts:188](</C:/Users/joshu/source/repos/iRacing Coach/web/src/sessions/model.ts:188>)  
    **Reproduction:** unmodified mock library → select Fixed and Open. **13 sessions** match. Persistence writes `setup: null`; restoring it shows **14**, adding unknown-setup session 114.  
    **Smallest fix:** normalize both selected types to “no filter” consistently before filtering and saving, or preserve both explicitly.

13. **Misleading — filtering changes the purported event-best lap.** [sessions.tsx:95](</C:/Users/joshu/source/repos/iRacing Coach/web/src/pages/sessions.tsx:95>)  
    **Reproduction:** mock event containing race 110: qualifying 109 has **15.310 s**, race has **15.534 s**. Selecting Session → Race recalculates event best as 15.534 and colors the slower race figure as best.  
    **Smallest fix:** compute event-best metadata from the complete event, then filter its displayed sessions.

14. **Misleading — a blocked duplicate phase hides an openable phase.** [PhaseSelector.tsx:41](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/PhaseSelector.tsx:41>)  
    **Reproduction:** mock variant with two Practice sessions in one event, first failed and second ready. The selector keeps only the failed one and disables P; the ready practice cannot be reached there.  
    **Smallest fix:** when consolidating a phase, prefer the current session, then an openable session, rather than the first array entry.

15. **Cosmetic — T14 is still only partly fixed.** [LapRail.tsx:127](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/ui/LapRail.tsx:127>), [format.ts:40](</C:/Users/joshu/source/repos/iRacing Coach/web/src/investigation/format.ts:40>)  
    **Reproduction:** unmodified G2 rail: run headers have no observed-pace popover; sub-minute times still read **28.408**, not **0:28.408**; conditions remain the four-value tooltip called out in T14.  
    **Smallest fix:** implement the remaining rail details, or explicitly disposition these differences instead of marking T14 wholly fixed.

---

## Disposition

Resolve step, 2026-10-04. Reproductions checked first: findings 1, 9, 12 and 13 by a probe running the pre-fix rules on the review's data (lap 83 is green at 3.983 s and wins once `complete` is dropped; Race L58 30.050 s against Traces L1 30.000 s; 13 sessions against 14 after the reload; race 110's 15.534 s taken as event best over qualifying's 15.310 s); findings 7 and 8 on `web/mock` once its bare traces request answers the catalog default set, as the server does; the others from the pre-fix code path the review cites. Each fix has a test: unit tests in `web/src/investigation/__tests__/review-sol.test.ts` and `web/src/sessions/review-sol.test.ts`, screens in `web/e2e/app/review-sol.spec.ts` (all three viewports).

| # | Decision | Reason | Fix | Test |
|---|---|---|---|---|
| 1 | Accept | The projector writes `partial` whenever `complete` is not true; a missing `complete` must not make a partial lap the zero lap. | `isCompleteLap` (no `complete: false`, no `partial` exclusion) in the clean rule, the baseline pool, Spotlight, the rail flag, Δ and chip. | unit, finding 1 |
| 2 | Accept | The contract nulls a sector whose boundary the bins stop more than two bins short of; holding the end value made 0.000 s session bests. | `timeAtFraction` holds the first or last recorded bin only within two bins, null beyond. | unit, finding 2 |
| 3 | Accept | Rows clipped by paint containment hide driving inputs. | Rows keep their floor (`chartContentHeight`); the chart frame scrolls vertically (`--ri-chart-min`). | unit and e2e, finding 3 (1280x520 and 1024x520 overflow, last row reachable) |
| 4 | Accept | Old laps' readouts under "No laps selected" are wrong data. | On an empty selection the cursor is disposed and its canvas cleared, map readouts reset to `—`, marker hidden, readout head `No laps`. | e2e, finding 4 |
| 5 | Accept | Spotlight added a lap the filtered rail hides. | Spotlight lists only complete timed laps the active Green and Clean filters keep (`spotlightChoices`). | unit, finding 5 |
| 6 | Accept | Labels and the map cursor were placed from guessed fractions. | `sectionMapPoints`: labels and the inspection cursor use map points only when every point has its lap fraction. | unit, finding 6 |
| 7 | Accept | The ribbon memo ignored channels filled in place. | `TrackPanel` takes the load tick for ribbon, legend and strip. | e2e, finding 7 |
| 8 | Accept | A swallowed rejection left rows on Loading for good. | Failed channels kept; the row says `Failed`; the traces header shows `<channel> failed` and Retry, which refetches. | unit and e2e, finding 8 |
| 9 | Accept | One fastest lap per race (ruling on T7). | `raceFastestLap` uses the shared `fastestCleanLap` over the race view's laps and incidents (investigation laps without them). | unit, finding 9 |
| 10 | Accept | Service after the race ended is not an in-race stop. | `PitStop.postRun` from `post_run_service` (runs fallback: `ended_with_post_run_service`); `inRaceStops` for the rail and the progression chart; its timeline pit marker stays suppressed. | unit, finding 10 |
| 11 | Accept | The backend always emits these (`race_investigation.py:643`, `:860`; `race_inspection.py:359`; `steering_basis.py:386`); a missing one is a format error. | `schema_version`, `pace_comparable`, pass `eligible` and steering `comparable` required through `InvestigationFormatError`. | unit, finding 11 |
| 12 | Accept | `session_filters` cannot hold both types, so the page must read them as it stores them. | `activeFilters`: Fixed and Open together are no setup filter, before and after a reload. | unit, finding 12 |
| 13 | Accept | Event best belongs to the event, not the filtered view. | `groupEvents(sessions, selection)` computes best, head and setup type from every phase, then keeps the matching phases. | unit, finding 13 |
| 14 | Accept | A failed duplicate hid an analyzed phase. | `orderedPhases(phases, current)` takes the current session, else the first openable, else the first. | unit, finding 14 |
| 15 | Reject | The rail is built to the chosen design (`design/README.md`, Lap rail; Direction B with A's columns): both mockups write sub-minute times as `30.675`, the run header is `Run n · L-range · ● green ● caution` without a popover, conditions sit in the lap-time tooltip. These T14 differences from the retired app are design decisions, not defects; the backlog's "T1-T14 fixed" should read "T14 per design". | none | none |

Mock changes that the screens need: `web/mock` serves the catalog default set for a bare traces request and other channels on `?channels=` (Slip angle and Tire stress are outside the default set), and `POST /__mock/state {failChannels}` answers 503 for those channels. `web/mock/data.d.mts` types the fixture for unit tests.
