# Web app source map

TypeScript, Preact and Vite. Wire types are generated (`pnpm gen:wire` writes `gen/api.d.ts`, `gen/live-channels.ts`, `gen/setup-keys.ts`; `pnpm gen:theme` writes `gen/theme.css` and `gen/theme.ts`). Never edit `gen/`.

Owners: W7 owns everything here except `investigation/` (W8). W8 reads the shared modules below and asks W7 for changes; the modules keep their signatures stable.

## Shared modules (stable for W8)

| Module | Use |
|---|---|
| `core/router.ts` | Hash routes: `#/d/{customer}/s/{session}/{race\|runs\|sections\|traces}?comparison=..&section=..`. `useRoute()`, `navigate(route)`, `href(route, query)`, `setQuery(patch)` (replaces history, `null` removes a key), `parseHash`, `formatRoute`. |
| `core/api.ts` | `getJson(path, {signal})`, `request(method, path, body, {idempotencyKey, ifMatch})`, `paths.*` (every endpoint path, including the view endpoints `race`, `runs`, `investigation`, `traceCatalog`, `traces`, `track`), `Schemas` (generated types), `ApiError` (`status`, `code`), `NetworkError`, `VersionConflictError`, `reachable` store. Errors carry the server's stable `error` code; a `409 projection_pending` means retry on `analysis_ready`. |
| `core/resource.ts` | `useResource(key, loader?)`: stale-while-revalidate read keyed by request path; a failed refresh keeps the last value and reports `error`. readers mounting together share one request and a value under 3 s old is not refetched on mount. `invalidate(prefix)`, `invalidateKey(key)` (exact key; a list path is a prefix of every path below it), `cachedValue`, `setCached`. At most 96 keys. W8's analysis cache (3 analyses, 96 MB) is separate and lives in `investigation/cache.ts`. |
| `core/sse.ts` | `events` (one `/api/v1/events` stream per tab), `useServerEvent(type, handler)`, `events.onReopen(fn)`. The shell already invalidates cached reads on `library_changed`, `analysis_ready`, `recording_state` and `presence`. |
| `core/outbox.ts` | `outbox.enqueue({method, path, body, ifMatch?, label, coalesceKey?})` for user-authored writes (notes, drafts, layouts, prefs, annotations). Persisted in localStorage with an `Idempotency-Key` per write, sent in order, retried through outages, visible in the shell until accepted. `outbox.observe(path, version)` records the version just read so the write sends `If-Match`. A 412 becomes an explicit choice in the shell (`ui/outbox-bar.tsx`). |
| `core/prefs.ts` | `usePrefs(customerId | null)` (null makes no request), `savePrefs`, `rememberInvestigation(customerId, {session_id, analysis_id, view, comparison_id, section_id})` (call it whenever the investigation selection changes; the Sessions page offers Resume from it, it never opens the last race by itself), `getLastInvestigation`, `useReducedMotion`. |
| `core/text-size.ts` | The driver's text size (Default, Larger, Largest): `textSize` store, `chooseTextSize(size, customerId)`, `useSavedTextSize(customerId)`, `initTextSize()` (called by `main.tsx` before the first render). The step sets the root font size (`data-text-size` on `<html>`, `gen/theme.css`); it is saved as `text_size` in the driver's prefs (an unknown field of `DriverPrefs`: the server keeps unknown top-level prefs fields), localStorage `coach.text-size` is only the pre-load hint. The control is `ui/text-size-control.tsx`, in the top bar. |
| `core/jobs.ts` | `waitForJob(id)`, `useJobRun()` for requests that answer with a result or `202 JobRef`. |
| `core/store.ts` | `createStore`, `useStore`, `safeStorage`, `readJson`, `writeJson` (every storage access is guarded). |
| `core/format.ts` | Lap times, signed deltas, dates (UTC), labels. Missing values are `-`, never zero. |
| `ui/kit.tsx`, `ui/notes.tsx` | `Chip`, `Notice`, `Empty`, `Loading`, `PageHead`; `NotesPanel({customerId, kind, subjectId})` for notes on a session, setup or analysis. |
| `styles.css` | One stylesheet on the generated theme tokens. The investigation workspace can add its own CSS file inside `investigation/`. |

## Investigation slot

`ui/investigation-slot.tsx` loads `investigation/index.tsx` (default export) when it exists and shows a notice when it does not. The component receives `InvestigationProps` (`customerId`, `sessionId`, `view`, `query`) and renders inside `<main class="main main-wide">`. It fills the viewport itself; the shell adds no page header there.

## Rules the shell keeps

- Type scale (owner, 2026-10-03: "hard to see because the sizes are too small"): body 16 px, secondary and axis text 14 px, labels on tabs and buttons and numbers 15 px, section titles 18 px, controls 36 px tall (44 px on touch and phones), all at the default root. They are tokens in `config/theme.dark.json` (`typography`), emitted in **rem** by `pnpm gen:theme`, so the browser's default font size and the driver's text-size step scale type, controls and spacing together. Write sizes as `var(--text-*)`, `var(--control-height)`, `var(--touch-target)` and `var(--space-*)`, never as px; a chart that draws in pixels (an SVG whose view box is its own size, a canvas) reads the root with `investigation/ui/unit.ts`. `perf/type-scale.mjs --check` fails a page whose computed sizes fall under these minimums.
- Pointer movement never causes a request; loaded data stays on screen when the server drops (banner, not a blank page).
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
| Live: frame decoder, tiles, trends, layout editor | `pages/live.tsx`, `live/*` (`frame.ts` decoder, `encode.ts` test encoder, `tiles.ts` formatting from the generated table, `history.ts` ring buffer, `connection.ts` socket, `layout.ts` model, `grid.tsx`, `editor.tsx`) |

## Development

- `pnpm -C web mock` serves `dist/` and the whole `/api/v1` surface from fixtures (`web/mock/`): drivers, sessions, setups, tuning, notes and drafts with `If-Match` and `Idempotency-Key`, SSE, binary live frames at the requested rate, and fixture views (race, runs, investigation, trace catalog, CCOL traces, track). Test control: `POST /__mock/state {drivers, live, conflictNext, failWrites, delayMs, reset}` and `POST /__mock/event {...ServerEvent}`.
- `pnpm -C web e2e --project=app` builds into `test-results/app-dist`, starts the mock on 4373 and runs desktop 1280x720, tablet 1024x768 and phone 390x844.
