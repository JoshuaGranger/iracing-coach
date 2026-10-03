# Wave 3: the released service on the mini PC, as a user (integration lead)

Run 2026-10-03 05:19Z to 05:24Z from the dev PC against `http://192.168.1.81:8210`, the release of `15a53f4` that passed G1 (`server-release.md`, `g1-analysis.md`). Only HTTP, as a browser and a collector use it: no container, host or release change, no SSH. The run's own writes: one recording upload, and the preference writes the SPA makes when an investigation opens (`last_investigation`).

## Before the upload

`GET /api/v1/status`: `nas.state ok`, queues and spool empty, Python 3.12.15, profile `post-race-foundations-v23`, no collector. One driver with three sessions: the G1 race (session 1, `subsession:87641773:2`, `analysis_id 31419c583d3959b352c3a86c`), the overlap race (session 2, 87648500) and the reconnect-group practice (session 3, two recordings).

`node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\mini-before --session 1` (Playwright Chromium, emulated viewports):

| Check | Desktop 1280x720 | Tablet 1024x768 | Phone 390x844 |
|---|---|---|---|
| `/` opens the single driver's Sessions | pass | pass | pass |
| Library: 3 rows equal to the API, all Analyzed | pass | pass | pass |
| G1 race: Race / Runs / Sections / Traces render (ms) | 207 / 36 / 43 / 199 | 119 / 8 / 19 / 189 | 110 / 8 / 35 / 234 |
| Sections cursor follows the pointer (63.5 % to 79.7 % lap) | pass | pass | pass |
| Traces: map readout follows the chart cursor | 29.8 % to 70.7 % | 29.8 % to 71.0 % | 29.5 % to 72.3 % |
| Traces: requests during a 5 s, 300-move pointer sweep | 0 | 0 | 0 |
| Setups: 2 listed, the race setup shows 62 values | pass | pass | pass |
| No horizontal page scroll | pass | pass | pass |
| No console error | fail: `GET /api/v1/drivers/0/prefs` 404 (W7 minor, `e2e-dev-pc.md`) | same | same |

The first desktop load took 7.5 s to show the library; three repeated cold loads in new browser contexts then took 195 to 238 ms (document 45 to 78 ms, assets 7 to 9 ms each, API calls 2 to 4 ms), so the first figure is attributed to the first connection from a newly launched browser, not to the service. The same loads show `/drivers` requested 3 times and `/drivers/<id>/prefs` twice (W7 minor). The 1280x720 Traces view of the G1 race is 3,710 px high, as on the dev PC (W8 major).

## Upload of a different session type

Recording: the Iowa Offline Testing session of 2026-08-03 (`22a20e50dcff6f548c5afd6d5cd93440d7d818a4af60469e4dbcd183ca52ab8d`, 13,289,951 B, SubSessionID 0), a scratch copy of the dev PC's file, read-only. The service held Race and Practice sessions only; `HEAD /api/v1/uploads/22a20e50...` answered 404 before the run.

`python tools/e2e/time_to_visible.py --server http://192.168.1.81:8210 --file <copy>` (runs `tools/rig/g1_upload.py`, the collector-protocol section 4 client), started 05:22:01.956Z:

| Measure | Value |
|---|---|
| Hash, upload | 0.02 s; 4 chunks in 1.58 s (8.4 MB/s), create `201`, 0 conflicts, 0 retries, final state `verified` |
| Jobs | `discover` 05:22:02 (succeeded), `archive` 05:22:02 (succeeded), `analyze` 05:22:02 to 05:22:04, `project` 05:22:04 |
| Session | new row 4, `Offline Testing`, Iowa Speedway, `analysis_state ready`, `analysis_id cef488c5c7e7563c98ad2d82` (the dev PC's Windows worker produced the same id for the same file) |
| Views after ready | race, runs, investigation, trace catalog, track: all `200`, 0 to 18 ms |
| NAS (status endpoint) | `nas.state ok`, `last_ok_at 05:22:02Z`, `pending_archive 0`; spool empty again (the copy was deleted after archiving) |
| **Upload complete (verified) to analysed and visible** (session `ready` and its race view served) | **2.57 s**; first byte to visible 4.3 s |

Then `spa_check.mjs` again (`shots\mini`): the library lists 4 sessions at all three viewports, the new one labelled Test with "best 24.457 · 2 laps"; the G1 race's investigation results are as before (race 107 to 117 ms, traces 183 to 219 ms, 0 requests during the sweeps); Setups lists 3 setups.

Side effect for the owner: after this upload `/api/v1/drivers` reports `irating 1` (iRacing writes 1 into offline sessions); the next official race restores it. Defect W4 minor in `e2e-dev-pc.md`.

## Not measured here

- A race-sized recording end to end: G1 measured it on this release (first upload byte to projected views 44.9 s for 203 MB).
- Tailnet access and a real phone or tablet: the viewports are emulated in Chromium on the dev PC.
- Collector presence and live tiles on the mini PC: no collector runs against it until the sim-PC install, which is blocked on the DESKTOP-SIM remote agent (stopped; only the owner can restart it).

Screenshots and JSON reports: `<scratchpad>\e2e\shots\mini-before\`, `shots\mini\`; upload report `<scratchpad>\e2e\logs\mini-upload.json`.
