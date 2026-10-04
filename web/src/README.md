# Web app source map

TypeScript, Preact and Vite. Wire types are generated (`pnpm gen:wire` writes `gen/api.d.ts`, `gen/live-channels.ts`, `gen/setup-keys.ts`; `pnpm gen:theme` writes `gen/theme.css` and `gen/theme.ts`). Never edit `gen/`.

Owners: W7 owns everything here except `investigation/` (W8). W8 reads the shared modules below and asks W7 for changes; the modules keep their signatures stable.

## Shared modules (stable for W8)

| Module | Use |
|---|---|
| `core/router.ts` | Hash routes: `#/d/{customer}/s/{session}/{race\|runs\|sections\|traces}?comparison=..&section=..`. `useRoute()`, `navigate(route)`, `href(route, query)`, `setQuery(patch)` (replaces history, `null` removes a key), `parseHash`, `formatRoute`. |
| `core/api.ts` | `getJson(path, {signal})`, `qs(params)` (an array value repeats its key: `car=a&car=b`), `request(method, path, body, {idempotencyKey, ifMatch})`, `paths.*` (every endpoint path, including the view endpoints `race`, `runs`, `investigation`, `traceCatalog`, `traces`, `track`), `Schemas` (generated types), `ApiError` (`status`, `code`), `NetworkError`, `VersionConflictError`, `reachable` store. Errors carry the server's stable `error` code; a `409 projection_pending` means retry on `analysis_ready`. |
| `core/resource.ts` | `useResource(key, loader?)`: stale-while-revalidate read keyed by request path; a failed refresh keeps the last value and reports `error`. readers mounting together share one request and a value under 3 s old is not refetched on mount. `invalidate(prefix)`, `invalidateKey(key)` (exact key; a list path is a prefix of every path below it), `cachedValue`, `setCached`. At most 96 keys. W8's analysis cache (3 analyses, 96 MB) is separate and lives in `investigation/cache.ts`. |
| `core/sse.ts` | `events` (one `/api/v1/events` stream per tab), `useServerEvent(type, handler)`, `events.onReopen(fn)`. The shell already invalidates cached reads on `library_changed`, `analysis_ready`, `recording_state` and `presence`. |
| `core/outbox.ts` | `outbox.enqueue({method, path, body, ifMatch?, label, coalesceKey?})` for user-authored writes (notes, drafts, layouts, prefs, annotations). Persisted in localStorage with an `Idempotency-Key` per write, sent in order, retried through outages, visible in the shell until accepted. `outbox.observe(path, version)` records the version just read so the write sends `If-Match`. A 412 becomes an explicit choice in the shell (`ui/outbox-bar.tsx`). |
| `core/prefs.ts` | `usePrefs(customerId | null)` (null makes no request), `savePrefs`, `rememberInvestigation(customerId, {session_id, analysis_id, view, comparison_id, section_id})` (call it whenever the investigation selection changes; Sessions reopens a session on its last tab from it, silently), `getLastInvestigation`, `useReducedMotion`. Types `SessionFilters` (`session_filters`, the Sessions page's selection), `SetupFilters`. |
| `core/jobs.ts` | `waitForJob(id)`, `useJobRun()` for requests that answer with a result or `202 JobRef`. |
| `core/store.ts` | `createStore`, `useStore`, `safeStorage`, `readJson`, `writeJson` (every storage access is guarded). |
| `core/format.ts` | Lap times, signed deltas, dates and times in the driver's local zone, labels. Missing values are `-`, never zero. |
| `ui/kit.tsx`, `ui/notes.tsx` | `Chip`, `Notice`, `Empty`, `Loading`, `PageHead`; `NotesPanel({customerId, kind, subjectId})` for notes on a session, setup or analysis. The racing marks of the Broadcast direction: `Dot` (green, caution, pit, zero), `FlagGlyph` (white, checkered), `Stripe`, `SetupBadge` (Fixed lock, Open dashed), `PositionChange` (▲8 gain, ▼2 loss), `IncidentChip` (`x0` to `x4+`), `StatusChip` (`Excl`, `Partial`), `Stat`, `TrackSilhouette`, `EventHeader` and `PhaseTile` (the event card: identity block, one tile per phase). |
| `ui/multi-select.tsx` | The library filter, one component on Sessions and Setups: `MultiSelect({label, noun, options, selected, onChange})` (searchable, checkbox options with an identity `mark` and a count, `n of m` and Clear), `FilterChip` (the whole pill removes it), `FilterChips` (divider, chips, Clear). Styles in `styles.css` (`.filter-bar`, `.ms-*`, `.filter-chip`). |
| `ui/segmented.tsx` | `Segmented({label, options, value, onChange, variant})`: the segmented selector (`aria-pressed`, arrow keys move between segments). `variant="phase"` is the P Q R selector; an option's `band` (`p`, `q`, `r`) is its phase colour, `disabled` plus `title` names a phase that cannot open. |
| `ui/pictograms.tsx` | `PitStopPictogram({stop})` (rail cell and chart marker), `PitStopCard({stop})` (hover and details), `TireDiagram`, `FuelGauge`. `stop` is `{lap, tires: ("LF"\|"RF"\|"LR"\|"RR")[] \| null, gallons, capacityGal, durationS}`; changed tires are lit green, a stop with no gallons draws no gauge. |
| `core/identity.ts` | `carColor(carPath)`: the car's identity colour (`var(--car-1)` to `var(--car-3)`), stable by first-seen order kept on this device. |
| `pages/kit.tsx` | The component gallery at `#/__kit` (not linked): every mark, selector, pictogram and the event card on one page, for screenshots and for the scrollbar test. |
| `styles.css` | One stylesheet on the generated theme tokens. The investigation workspace, Sessions, Setups and Tuning keep their own CSS files beside their code. |

## Investigation slot

`ui/investigation-slot.tsx` loads `investigation/index.tsx` (default export) when it exists and shows a notice when it does not. The component receives `InvestigationProps` (`customerId`, `sessionId`, `view`, `query`) and renders inside `<main class="main main-wide">`. It fills the viewport itself; the shell adds no page header there. The slot passes the event's phases with their analysis state (`event_sessions`) to the P Q R selector, and the driver's saved chart layouts (`trace-layouts`, `active_trace_layout_id`): New, Copy, Delete and edits show at once and are written through the outbox.

## Colour and components (Broadcast direction)

Colour is racing data and nothing else: `--race-green` (green flag, gain, tire changed), `--race-caution`, `--race-pit`, `--race-best` (fastest lap), `--race-loss` (cost), `--race-fuel`, the car identity colours `--car-1` to `--car-3` and the phase bands `--phase-p`, `--phase-q`, `--phase-r`. Interaction (selected, current, focus, primary action, cursor) is light: `--color-interaction`, which is also `--color-accent` and `--color-focus`. Telemetry channel colours (`--telemetry-speed` and so on) identify channels in single-lap channel plots only. Display type is `--font-display` (Bahnschrift, then DIN Alternate, then Barlow Semi Condensed) at `font-stretch: condensed` for labels (`.dc`) and `semi-condensed` for numerals (`.d`); `--text-hero`, `--text-figure` and `--text-heading` are the large sizes. Class names of the shared blocks: `bc-card`, `bc-event`, `bc-id`, `bc-tiles`, `bc-tile`, `bc-band`, `bc-hero`, `bc-stats`, `bc-stat`; marks `dot-*`, `badge`, `change`, `inc-chip`, `state-chip`, `stripe`; `.seg` (and `.seg.phase`) for the selectors; buttons are pills (`.btn`, `.btn-accent` is the one primary action per view). Scrollbars are themed once, at the root: `scrollbar-color` from the border token, thin, no arrow buttons; panels inherit it.

## Rules the shell keeps

- Type scale (owner, 2026-10-03: "hard to see because the sizes are too small"): body 16 px, secondary and axis text 14 px, labels on tabs and buttons and numbers 15 px, section titles 18 px, controls 36 px tall (44 px on touch and phones), all at the default root. They are tokens in `config/theme.dark.json` (`typography`), emitted in **rem** by `pnpm gen:theme`, so the browser's default font size scales type, controls and spacing together; there is one scale and no text-size setting (owner, 2026-10-04). Write sizes as `var(--text-*)`, `var(--control-height)`, `var(--touch-target)` and `var(--space-*)`, never as px; a chart that draws in pixels (an SVG whose view box is its own size, a canvas) reads the root with `investigation/ui/unit.ts`. `perf/type-scale.mjs --check` fails a page whose computed sizes fall under these minimums.
- Pointer movement never causes a request; loaded data stays on screen when the server drops (banner, not a blank page). Live is the exception: tiles read Waiting when no live frame arrived for max(2 s, three frame intervals), on a closed socket and on a hello that says the sim is not live.
- Live always asks for 60 Hz. There is no rate setting: `live/rate.ts` lowers the stream rate (60, 30, 10 Hz) when the measured frame time stays over 25 ms and tries the next step up after a calm minute.
- Plain HTTP on the LAN is not a secure context: no `crypto.randomUUID` (use `core/uuid.ts`), no clipboard, no service worker.
- Motion: `data-motion="reduced"` is set on `<html>` when the driver or the OS asks for it; structural transitions use `--motion-structure`, which is 0 in that case.
- Phone targets are 44 px; there is no horizontal page scroll at 390 px.

## Pages

| Page | File |
|---|---|
| Drivers (one driver opens straight to it) | `pages/drivers.tsx` |
| Sessions library | `pages/sessions.tsx` |
| Setups library, detail with compare, Find a baseline | `pages/setups.tsx`, `pages/setup-detail.tsx`, `pages/baseline.tsx`, `setups/model.ts` |
| Progressive Tuning | `pages/tuning.tsx`, `tuning/model.ts`, `tuning/map.ts`, `tuning/track-map.tsx` |
| Live: frame decoder, tiles, trends, layout editor | `pages/live.tsx`, `live/*` (`frame.ts` decoder, `encode.ts` test encoder, `tiles.ts` formatting from the generated table plus the tile trend value and the display range, `history.ts` ring buffer with rolling lap windows and gap breaks, `connection.ts` socket and freshness, `rate.ts` the frame-time governor, `board.tsx` the render loop, `layout.ts` model, `grid.tsx`, `editor.tsx`) |

## Development

- `pnpm -C web mock` serves `dist/` and the whole `/api/v1` surface from fixtures (`web/mock/`): drivers, sessions, setups, tuning, notes and drafts with `If-Match` and `Idempotency-Key`, SSE, binary live frames at the requested rate, and fixture views (race, runs, investigation, trace catalog, CCOL traces, track). Test control: `POST /__mock/state {drivers, live, conflictNext, failWrites, delayMs, reset}` and `POST /__mock/event {...ServerEvent}`.
- `pnpm -C web e2e --project=app` builds into `test-results/app-dist`, starts the mock on 4373 and runs desktop 1280x720, tablet 1024x768 and phone 390x844.
