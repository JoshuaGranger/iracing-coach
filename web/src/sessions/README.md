# Sessions library (`web/src/sessions/`)

Page: `pages/sessions.tsx`. Look: design direction B (event cards, session tiles) with direction A's fixed-column discipline for the figures (`docs/08-rewrite/design/README.md`). Backlog #1f, #1g, #1h, #1i, #1k, #3 and the Sessions findings of `reviews/web-semantics-audit.md` (S1 to S5).

| File | Holds |
|---|---|
| `model.ts` | Events and phases (running order P, Q, R), filters (`Selection`, `filterSessions`, `facetOptions`), figure rules (pace tone, place change, result band, event best over the whole event whatever the filter shows; Fixed and Open together are no setup filter, as `session_filters` stores it), analysis exceptions, `eventPhaseOptions`. Pure; unit-tested. |
| `dates.ts` | Local date and time (audit S1). |
| `library.ts` | `useLibrary(customerId)`: the whole library in 200-row pages under one cache key; the filters work on it here. |
| `event-card.tsx` | The event card and the session tile. |
| `silhouette.ts`, `track-silhouette.tsx` | Track silhouette from the analysis geometry, fetched once per track layout when its card is on screen, kept in memory and in the device's storage. |
| `tab-memory.ts` | The last tab per session, remembered silently from the hash; the row opens there. |
| `sessions.css` | All styling. Race data colours come from `--race-*`, `--phase-*`, `--car-*`, interaction from `--color-interaction`. |

Rules that are easy to get wrong:

- A normal (ready) session shows no state. Only Analyzing, Queued, Deferred, Failed and Not analyzed are named.
- Colour carries data only: green and caution dots with their counts, gain and loss, best lap (event best), pit (stops), fuel, pace fall-off and improvement. Selected and open are light, never a hue.
- Filters: any chosen value within one filter, all filters together. The selection is saved as `session_filters` in the driver's prefs (the query values of `GET /sessions`: car_path, track name, series name, `race`/`qualify`/`practice`, `fixed`/`open`) and mirrored on the device only for the first paint. The Setup filter appears once a session states its setup type.
- Fixed or Open comes from `is_fixed_setup` (true fixed, false open); null means the recording does not say: no badge, never read as open.
- The investigation header's P . Q . R selector takes the event's phases and their analysis state from the session detail (`event_sessions`), in the shell (`ui/investigation-slot.tsx`).
