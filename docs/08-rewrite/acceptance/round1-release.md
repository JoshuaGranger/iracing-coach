# Web round 1 on the live server (2026-10-04)

Acceptance of web round 1 (`932bdc4`) as a user meets it, on the released service `http://192.168.1.81:8210` (release facts: `server-release.md`, "Release of `932bdc4`"). Playwright Chromium from `web/node_modules`, run from `C:\Users\joshu\source\worktrees\iracing-coach-w9` on the dev PC. Read-only except the preference writes the SPA makes (last investigation) and the text-size write below, which was put back.

## Result

| Check | Command | Result |
|---|---|---|
| SPA at 1280x720, 1024x768, 390x844 | `node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\w9r1\spa3` | exit 0, 90 checks passed, 0 failed (07:10:38Z to 07:11:59Z) |
| Release checks at the same sizes | `node tools/rig/v1_candidate_check.mjs --base http://192.168.1.81:8210 --out <scratch>\w9r1\v1` | exit 0, 232 checks passed, 0 failed (to 07:13:30Z) |
| Screens at 1536x864 | `node tools/rig/live_screens.mjs --base http://192.168.1.81:8210 --out C:\Users\joshu\source\coach-migration\screens-round1 --prefix live` | exit 0, 8 screens, no console error |

The investigated race is session 9 (Las Vegas, 89 laps, the first `ready` Race the API lists).

## What the checks saw

| Page | At every viewport |
|---|---|
| Sessions | Lands on Joshua Granger's sessions; first event cards in 238 to 273 ms; with "Older sessions" pressed until it goes, **652 tiles in 642 event cards** (24 drawn first) = the API's 652 sessions (four pages of 200), each tile's `data-state` equal to the API; phase labels: Race 33, Lone Qualify as Qualifying 5, Practice 11, Offline Testing as Test 603; no horizontal scroll. The driver has no saved `session_filters`, so the every-session check ran. |
| Race, Runs, Sections, Traces | Render in 227 to 257, 17 to 29, 38 to 48 and 188 to 198 ms. The Traces header reads **`3 laps · Δ vs L7`** (`data-zero-lap 7`): the zero lap is named. 10 s pointer sweep on Traces (`spa_check`) and on Sections and Traces (`v1_candidate_check`): **0 requests** in 602 moves each time; the map readout follows the chart cursor (29.7 % to 71.2 % at desktop). Sections and Traces fit 1280x720 and 1024x768 exactly (document 720 / 768 px), all 6 drawn trace rows and the map in view. |
| Setups | **64 setups** (the API's 64) each reachable from a car card with every version list and capture stack open; 1,170 files with values unknown listed (API 1,170); 61 named entries, each titled with a name the API gives its setups; the capture stack holds 1 setup and no named one. Setup 71's detail shows 58 values. |
| Live | Renders its status without a session (`Connecting`, then `Waiting for iRacing` in the screen); no horizontal scroll. |
| Errors | No console error and no failed response on the passing runs. |

**Text size round-trips through prefs.** Prefs before: `text_size` absent, version 21. Picking Larger in one browser sent `PUT /api/v1/drivers/1507810/prefs` (200); prefs then held `text_size: "larger"` (version 22) and the root font went 16 to 18 px. A second browser with no local storage (390x844) opened at `data-text-size="larger"`, 18 px. The value was put back by `PUT` with `If-Match: 22` and `{"text_size": null}` (200, version 23); prefs now read `text_size: null`, the default the owner had.

## Setup library after the migration

From `GET /api/v1/drivers/1507810/setups?limit=200` before (06:57Z) and after (07:01Z) the release, with the start-up report in `server-release.md`:

| | Before | After |
|---|---|---|
| Setups | 67 | **64** (69, 70 and 2 merged) |
| Without a name | 19: captures 69 and 70, the 16 `.htm` exports 53 to 68, recording 11 | **1**: recording 11 |
| `.htm` exports | 16, all shown as unnamed | 16, **named from the file**, for example 68 `NOAPS_MaconiSetupShop Sonoma 26S3 Q`, 53 `NOAPS_MaconiSetupShop Sonoma 26S3 R` |
| Setup 71 (AtlantaSS 26S3 Q) | 2 observations | 3 (with capture 70) |
| Setup 10 (`claude LV R CAM.sto`) | 14 observations | 15 (with capture 69) |
| Setup 1 (NHMS 26S3 R) | 4 observations | 5 (with recording setup 2) |
| Files with values unknown | 1,170 | 1,170 |

The library screen (`live-setups.png`) opens with AtlantaSS 26S3 Q (3x) and `claude LV R CAM.sto` (15x) at the top of the Supra card, 58 setups, 12 raced.

## The first run, during the re-projection

The first `spa_check` run (07:01:46Z to 07:05:01Z, `<scratch>\w9r1\spa`) failed 10 checks: at desktop, Race, Runs, Sections and Traces of session 9 each timed out at 30 s, and `/api/v1/analyses/{id}/track` answered `409 projection_pending` for 10 analyses across the three viewports. Projection version 2 rebuilds an analysis's views on the first request; drawing all 642 event cards asked for many silhouettes at once, and session 9's views queued behind them. Tablet and phone, a minute later, rendered session 9 in 222 and 246 ms. A second run (07:05:30Z) failed only on one more first-time `409`. Requesting every analysis's track once then (about 07:07Z): 11 answered 200 and 640 were still pending; the container's CPU fell from 48.6 % to idle within about 25 s, and at about 07:10Z 649 answered 200 and 2 answered 404 (sessions 132 and 249: pit-only recordings with no track profile, as `migration.md` recorded). The third run is the result above. A driver opening the app first after a projection-version change meets the same: silhouettes and views fill in as their projections finish, which the SPA retries on `analysis_ready`.

## Screens (1536x864, live server)

In `C:\Users\joshu\source\coach-migration\screens-round1\`: `live-sessions.png`, `live-race.png`, `live-runs.png`, `live-sections.png`, `live-traces.png` (cursor at 38 %, header `3 LAPS · Δ VS L7`), `live-setups.png`, `live-setup-detail.png` (setup 71), `live-live.png`. The per-viewport screenshots of the checks are under `<scratch>\w9r1\spa3` and `v1`, with `spa-check.json` and `v1-check.json`.

## Seen and not changed

- Session 9's Race tile shows Best `-` and Pace `-`: its overview has no `best_clean_lap_s` (every lap of the race is excluded; Traces shows `EXCL` on each), so the tile shows no clean best. Data, not a rendering fault.
- The Traces lap rail shows grey sector blocks for session 9: the race view of this analysis carries no `sector_times_s`.
- The server reports no field for the lazy re-projection's progress; `queue.light` stayed 0 while the 640 projections ran.

## Script changes for the new structure

- `tools/e2e/spa_check.mjs`: Sessions as event cards (`li.session-row[data-session][data-state]`, "Older sessions" pressed until gone, all API pages read), phase labels from `.sx-band b`, skipped under saved filters; the Traces zero-lap header; the pointer sweep 5 to 10 s; Setups by car card (`section.s-car`, folds opened, setup links counted against the API, `li[data-entry=file]` against `unlinked_files`).
- `tools/rig/v1_candidate_check.mjs`: Setups by entry (named entries titled as the API names them, only unnamed setups in the capture stack), the setup list read with `limit=200`, and the text-size round trip with the value put back.
- `tools/rig/live_screens.mjs` (new): the eight screens against a live server at one size.
