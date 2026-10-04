# Sessions library (`web/src/sessions/`)

Page: `pages/sessions.tsx`. Specification: `docs/08-rewrite/web-round-2-plan.md`, "Sessions" (backlog #17, #19, #18). Round 1's event cards and session tiles are gone; the list is one compact row per event.

| File | Holds |
|---|---|
| `model.ts` | Events and phases (running order P, Q, R), filters (`Selection`, `filterSessions`, `facetOptions`), the cell rules (`phaseSlots`, `colouredFigures`, `phaseLayout`, `phaseColumns`), analysis exceptions, `eventPhaseOptions`. Pure; unit-tested. |
| `dates.ts` | Local date and time (audit S1). |
| `library.ts` | `useLibrary(customerId, library)`: the whole library in 200-row pages under one cache key per library (`main` keeps the plain URL); the filters work on it here. |
| `event-row.tsx` | `EventRow` (identity and phase cells) and `PhaseCell`; `SetupBadge`, the Fixed/Open constraint chip. |
| `silhouette.ts`, `track-silhouette.tsx` | Track silhouette from the analysis geometry, about 1.5 rem tall, fetched once per track layout when its row is on screen, kept in memory and in the device's storage. |
| `tab-memory.ts` | The last tab per session, remembered silently from the hash; the cell opens there. |
| `sessions.css` | All styling. Race data colours come from `--race-*`, phase hues from `--phase-*`, the car stripe from `--car-*`. |

## The row

- **Identity** (left, 14 to 17 rem): silhouette (1.5 rem) and track, `config · date` with the Fixed/Open chip, car stripe and car name (the series is in its tooltip, not drawn).
- **Phases**: equal cells, one per phase, in running order. `phaseLayout` takes the phases present in what is shown, so a phase keeps its column in every row (two events line up to compare) and a filter that leaves only races leaves one column. A repeated phase (two practices) follows running order.
- **Layout by the row's own width** (container queries, not a device test): at 62 rem and wider the identity is at the left of the cells; below that it is a line above them; below 40 rem (a phone) the phases stack as rows. Nothing sits behind a disclosure.
- **Cell**: a 4 x 2 grid. The header slot (`time` over the phase name, with its own Fixed/Open chip only when it differs from the event) and the seven labelled slots in the plan's order: position or finish, green and caution laps, best, pace, stops, fuel, longest green run. A missing value is `—`. A phase with no figures shows its lap count and the one exception chip that explains it. The whole cell is the link when the phase can open.
- **Type**: the owner's minimums (2026-10-03) beat the plan's 0.75 rem: names 16 px (`--text-body`), figures 15 px (`--text-data`), labels and secondary lines 14 px (`--text-compact`). The row grows to fit; `perf/type-scale.mjs` selects `.sx-id h2`, `.sx-meta`, `.sx-car`, `.sx-l`, `.sx-v` and `.sx-p`, so the audit measures them. No local `--sx-*` tokens: the stylesheet reads the theme's tokens.

## Rules that are easy to get wrong

- **Colour budget.** Gain or loss on a supported position change (race only), amber on a caution count above zero, the phase's one unsaturated hue as an edge, the car stripe. Nothing else: the finish is a plain number (no win flag), best, pace, stops, fuel and run carry no colour, there is no fill and no glow. The event's fastest lap is marked by weight (`data-event-best`), never by colour, and never across phases. `colouredFigures` is the tested budget: at most two per cell.
- **Pace slot.** The backend trend (`pace_slope_s_per_lap`) with its sign. The overview has no green pace; the slot reads an optional `green_pace_median_s` and prints it before the trend when the server sends one (server gap, see the task result). Until then the slot is the trend alone.
- **Chips.** Fixed/Open is the one constraint chip. Exception chips (`Not analyzed`, `Failed · reason`, `Deferred · reason`, `Queued`, `Analyzing`) appear only on a phase that is not ready; `Analyze` and `Retry` are the chip's own action. A ready phase shows nothing.
- **Filters** sit in the page heading by the shared width rule (`ui/filter-bar.tsx`): inline at the right when they fit, else `Filters · N` (popover on a pointer, sheet on touch). The `Library: Joshua | Lab` control is inside that surface, default Joshua, never saved. Any chosen value within one filter, all filters together. The selection is saved as `session_filters` in the driver's prefs (the query values of `GET /sessions`: car_path, track name, series name, `race`/`qualify`/`practice`, `fixed`/`open`) and mirrored on the device only for the first paint. The Setup filter appears once a session states its setup type. Clear is in the surface; chosen values show as removable chips under the heading.
- Fixed or Open comes from `is_fixed_setup` (true fixed, false open); null means the recording does not say: no chip, never read as open.
- The investigation header's P . Q . R selector takes the event's phases and their analysis state from the session detail (`event_sessions`), in the shell (`ui/investigation-slot.tsx`).
- Round 1 figures kept: event best over the whole event whatever the filter shows (`groupEvents`), longest run, pace and fuel on every cell, the saved filter selections.
