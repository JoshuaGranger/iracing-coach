# Wave 3b: the v1 candidate release (W9)

Released 2026-10-03 under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (waves 0 to 3 and the wave-3 end-to-end fixes), then verified from the dev PC as a user over HTTP against `http://192.168.1.81:8210`. Nothing was pushed to GitHub. The sim PC was not used: its remote agent is stopped and only the owner can restart it, so every sim step below is blocked on the sim agent.

## Result

The candidate is released and healthy, the recordings already on the server still list and render, the wave-3 fixes hold on the real service (Sections and Traces fit 1280x720 and 1024x768, zero requests during 10 s pointer sweeps, setups named from their recordings, iRating no longer taken from an offline session, manifests carry session types), and one more real race went from upload to visible in 29.5 s (12.3 s from `verified`). v1 acceptance is not complete: the rig steps (sim-PC install, a race recorded on the rig, D12 migration, G3, G4, the rig-screen check) are blocked on the sim agent.

## Release

| Item | Value |
|---|---|
| Commit | `2e3b9625a6bcd6012958c6bdc64b92227946d87e` (`main`, "Fix the end-to-end defects found on the dev PC and the mini PC (rewrite wave 3)"), fast-forwarded into `w9/deploy` from `514b752` |
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`), exit 0 |
| Started, finished | 05:48:04Z, 05:50:07Z (2 min 3 s) |
| Build on the N100 | 118 s (`g-house-app ... built in 118s`); `cargo build --release --locked -p coach-server` 113.8 s with 2 jobs at `nice 10`; `vite build` 287 ms, initial chunk `index-JIRUEp8w.js` 30.20 kB (10.69 kB gzip) |
| Image | `iracing-coach:2e3b9625a6bcd6012958c6bdc64b92227946d87e`, 144 MB (manifest `sha256:dba925d4...fe57a`, config `sha256:974c6cd5...25b4`) |
| Previous image | `iracing-coach:15a53f4161b7233ee55153b43f4d423f1dae7db9` (`server-release.md`), kept on the host |
| Stack | revision `fe9f13bca58e8ff2` (Compose file unchanged); `deploys.log`: start 05:50:02Z, recovery point 05:50:02Z (`sqlite ok .../db/coach.sqlite3 286720`), healthy 05:50:05Z |
| Recovery point | `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T055002Z.tar`, 2,672,640 B (`db/`, `snapshots/`, the SQLite copy) |
| Idle after start | 2.97 MiB container memory, 0.01 % CPU (`docker stats`) |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"`:

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=fe9f13bca58e8ff2
recorded=fe9f13bca58e8ff2
iracing-coach-app-1   Up 10 seconds (healthy)   iracing-coach:2e3b9625a6bcd6012958c6bdc64b92227946d87e
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`

`curl -s http://192.168.1.81:8210/api/v1/status` after the release: `nas.state ok` (`last_ok_at` 05:50:03Z, `pending_archive 0`), queues and spool empty, `python_version 3.12.15`, `worker_profile_version post-race-foundations-v23`, `api_version 1`, `collector_protocol 1`, `live_table_version 1`, `projection_version 1`, no collectors.

## Data on the server before the upload

One driver (Joshua Granger, 4 sessions), all `ready`: the G1 New Hampshire race (session 1, subsession 87641773, `analysis_id 31419c583d3959b352c3a86c`), the overlap race (session 2, 87648500), the reconnect-group practice (session 3, 87621937, two recordings) and the Iowa Offline Testing session (session 4). `GET /api/v1/drivers` still read `irating 1`: the rating stored before the fix stays until the next rated recording (it came back with the upload below). Setups: 3, each now named `NOAPS_MaconiSetupShop NHMS 26S3 R.sto` (the name the unmodified recordings loaded; before this release the library showed "Unnamed Toyota Supra Class B setup"). The names are derived when the library is read, so no re-ingest was needed.

## Browser checks (Playwright Chromium on the dev PC, emulated viewports)

Two scripts, each run before and after the upload:

- `node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\v1-spa-before --session 1`, then `--out ...\v1-spa-after --session 5` (the new race): landing, library against the API, Race, Runs, Sections, Traces, Sections and Traces cursors, 5 s sweep, Setups and a setup's values, console and failed responses.
- `node tools/rig/v1_candidate_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\v1-before --session 1`, then `--out ...\v1-after --session 5` (new, W9): a 10 s pointer sweep on Sections and on Traces with the request log, Sections and Traces against the viewport height, every drawn trace row inside the viewport, every setup row naming the setup as the API does, the Live page without a collector.

| Check | Desktop 1280x720 | Tablet 1024x768 | Phone 390x844 |
|---|---|---|---|
| `/` opens the single driver's Sessions | pass | pass | pass |
| Library rows equal the API, all `ready` (4 before, 5 after; Race, Practice, Test labels) | pass | pass | pass |
| Race / Runs / Sections / Traces render, session 1 (ms) | 216 / 7 / 42 / 186 | 215 / 6 / 20 / 231 | 212 / 7 / 44 / 192 |
| Race / Runs / Sections / Traces render, session 5, the new upload (ms) | 223 / 38 / 44 / 219 | 214 / 7 / 21 / 113 | 209 / 5 / 36 / 216 |
| Sections cursor follows the pointer (63.5 % to 79.7 % lap) | pass | pass | pass |
| Traces map readout follows the chart cursor | 29.8 % to 70.7 % | 29.8 % to 71.0 % | 29.5 % to 72.3 % |
| **Sections fit the viewport** (document height vs window), sessions 1 and 5 | 720 / 720 px | 768 / 768 px | stacked by design: 1,606 and 1,553 px |
| **Traces fit the viewport**, sessions 1 and 5 | 720 / 720 px; map and all 5 rows inside the viewport | 768 / 768 px; map and all 5 rows inside | stacked by design: 1,509 and 1,525 px; map in view, rows below it |
| **Requests during a 10 s pointer sweep** (Sections, Traces; 602 moves each), sessions 1 and 5 | 0, 0 and 0, 0 | 0, 0 and 0, 0 | 0, 0 and 0, 0 |
| Requests during the 5 s Traces sweep (`spa_check`) | 0 | 0 | 0 |
| **Setups name the NH race's setup**: rows equal the API (3 before, 4 after), each row shows its name | pass (`NOAPS_MaconiSetupShop NHMS 26S3 R.sto` x 3, then `iowa_2025_oval.sto` Raced) | pass | pass |
| Setup detail shows values (62) | pass | pass | pass |
| Live page renders its status without a collector | "Waiting for data", `data-live false` | same (or "Connecting" before the socket opens) | same |
| No horizontal page scroll on any page | pass | pass | pass |
| No console error, no failed (>= 400) response | pass | pass | pass |

`v1_candidate_check.mjs`: 0 failures of 56 results (before) and 0 of 59 (after). `spa_check.mjs`: 0 failures after the upload; before it, 1 failure, the desktop library's first load at 7,463 ms against the script's 5 s limit, the same 7.5 s `e2e-mini-pc.md` saw on its first load. A navigation-timing probe in three fresh contexts of a newly launched browser (first context included) measured 205 to 211 ms to library rows, document 75 to 79 ms, DNS 1.7 ms, every asset and API call 2 to 11 ms; tablet and phone in the same `spa_check` run loaded in 191 and 180 ms, and the after-upload run's desktop in 184 ms. The 7.5 s therefore belongs to `spa_check`'s first step in a newly launched browser, not to the service (W9 harness note below).

Screenshots and JSON reports (scratch, not in the repository): `<scratchpad>\shots\v1-spa-before\`, `v1-spa-after\`, `v1-before\`, `v1-after\` (`{desktop,tablet,phone}-{library,race,runs,sections,traces,setups,setup-detail,v1-sections,v1-traces,v1-setups,v1-live}.png`, `spa-check.json`, `v1-check.json`).

## Upload of one more real recording

Recording: the Iowa race of 2026-08-03 (`stockcars2 supra2019_iowa 2025 oval 2026-08-03 20-09-53.ibt`, 146,188,289 B, SHA-256 `60752f7970213f217e6c375ad9bc7db631b8848fd8b73131cb933affc2bba1d1`, subsession 87697310), a timestamp-preserving scratch copy of the dev PC's file, opened read-only. `HEAD /api/v1/uploads/60752f79...` answered 404 before the run.

`python tools/e2e/time_to_visible.py --server http://192.168.1.81:8210 --file <copy>` (runs `tools/rig/g1_upload.py`, the collector-protocol section 4 client), started 05:52:59.97Z, exit 0:

| Measure | Value |
|---|---|
| Hash | 0.14 s |
| Upload | create `201`, 35 chunks of 4 MiB, 16.95 s (8.63 MB/s), 0 conflicts, 0 retries, final state `verified`; wall to `verified` 17.21 s |
| Jobs | `discover` 05:53:15 to 05:53:16; `archive` 05:53:16 to 05:53:19 (after discovery, the wave-3 order); `analyze` 05:53:16 to 05:53:27 (11 s); `project` 05:53:27 |
| Session | new row 5, `Race`, Iowa Speedway, 55 laps recorded, P11 to P17, 3 stops, `analysis_state ready`, `analysis_id 5b1d8fb5397fa02ed19b574f` |
| Views after ready | race 42 ms, runs 28 ms, investigation 24 ms, trace catalog 13 ms, track 10 ms, all `200` |
| **Upload complete (`verified`) to analysed and visible** (session `ready` and its race view served) | **12.29 s** |
| **First byte to visible** | **29.5 s** (budget: session end to ready <= 2 min for a 203 MB race, `architecture.md` section 7) |
| NAS | `nas.state ok`, `last_ok_at 05:53:19Z`, `pending_archive 0`; spool empty again |
| NAS manifest (`raw/60/60752f79....manifest.json`, read in the container) | `recording.session_types ["Race"]`: the wave-3 fix holds (G1's manifests carry `[]`) |
| Driver | `irating 1636` (was 1 after the earlier offline upload) |
| Setup library | 4 setups; the new `iowa_2025_oval.sto`, Raced |
| Container after the run | 146.9 MiB, `memory.peak` 2,059,620,352 B (1.92 GiB, page cache of the upload included), `memory.events oom_kill 0` |

Both browser scripts then ran again on session 5 (table above): all checks pass.

## Blocked on the sim agent

The remote agent on DESKTOP-SIM is stopped (`remote_status` fails with `connection_failed`) and only the owner can restart it. Not attempted, each blocked on the sim agent:

| Step | Source |
|---|---|
| Sim-PC collector install (`coach-collector.exe install --server http://192.168.1.81:8210`) | `workstreams.md` wave 3, `architecture.md` section 10 |
| A real race recorded on the rig appears analysed in the browser (wave-3 exit condition) | `workstreams.md` Waves |
| Collector presence, the Live page reaching Live and live tiles against the released service | `workstreams.md` W3, W7 |
| Rig-screen device check (D11) | `owner-decisions` D11 |
| D12 migration, steps 1 to 5, and `acceptance/migration.md` | `workstreams.md` D12 |
| G3 garage capture (`tools/rig/g3-garage.ps1`) | `workstreams.md` Gates |
| G4 rig performance (`tools/rig/g4-perf.ps1`) | `workstreams.md` Gates |

## Not measured here

- Tailnet access and a real phone or tablet: the viewports are emulated in Chromium on the dev PC.
- A cold re-analysis on this image: the existing analyses are unchanged (the worker and its profile did not change), and the new upload is the image's only analysis.

## Findings for owners

| Owner | Severity | Finding |
|---|---|---|
| W7 | minor | Three Setups rows read `NOAPS_MaconiSetupShop NHMS 26S3 R.sto` with different values (one Raced, 3 sightings; two with 1 sighting): the file was evidently changed between sessions under one name. The rows are told apart only by sightings and date; a short distinguishing line (first session or fingerprint) would let the driver pick the right one. |
| W8 | observation | The new Iowa race's Traces open with lap 56 (12.833 s, the partial final lap, ending near 33 %) among the 3 selected laps beside laps 12 and 47; its lines stop mid-chart. Check whether a partial lap belongs in the default comparison. |
| Integration (`tools/e2e/`) | harness | `tools/e2e/spa_check.mjs`'s first desktop load in a newly launched browser takes about 7.5 s (twice now); a separate navigation-timing probe in fresh contexts measures about 0.2 s. The script's first step should be investigated before its 5 s limit is used as a gate. |

## Polish release (2026-10-03)

Released 2026-10-03 under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`081a1f0` to `035a78b`, "Polish the v1 candidate: same-name setups, default lap selection, spool rule"), then verified from the dev PC as a user over HTTP against `http://192.168.1.81:8210`. Nothing was pushed to GitHub. The sim PC was not used (its remote agent is stopped), so the "Blocked on the sim agent" table above still stands. This record is committed on `w9/deploy` after the release; the image holds `035a78b`.

### Result

The polish is released and healthy. The drivers answer is identical before and after the release (Joshua Granger, 6 sessions, iRating 1636, last session 2026-08-04); afterwards all 6 sessions are `ready` and 9 setups are listed (9 also in `e2e-mini-pc.md`). The two findings the first record left open hold on real recordings at desktop, tablet and phone: Setups rows that share a `.sto` name now show their seen span and the count of the other versions, and no race's Traces default selection contains a partial lap (the lap rail marks it `Partial`, and `Excl` on laps the backend does not call comparable). `spa_check.mjs` and `v1_candidate_check.mjs` (sessions 5 and 1) exit 0.

### Release

| Item | Value |
|---|---|
| Commit | `035a78b61be77d4c4e708097c267f90e9abc6b35` (`main`), fast-forwarded into `w9/deploy` from `081a1f0` |
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`), exit 0 |
| Started, finished | 12:28:49Z, 12:30:57Z (2 min 8 s) |
| Build on the N100 | 122 s (`g-house-app ... built in 122s`): 9 cached layers including `pnpm install --frozen-lockfile`; `vite build` 275 ms, initial chunk `index-CZnMMfW2.js` 30.20 kB (10.68 kB gzip); `cargo build --release --locked -p coach-server` 1 min 57 s with 2 jobs at `nice 10`, the five workspace crates recompiled and the dependency crates taken from the build cache |
| Image | `iracing-coach:035a78b61be77d4c4e708097c267f90e9abc6b35`, 144 MB (manifest `sha256:83d1dc4e...`, config `sha256:1938689a...`; the running container's image ID equals that config digest) |
| Previous image | `iracing-coach:2e3b9625a6bcd6012958c6bdc64b92227946d87e`, kept on the host |
| Stack | revision `fe9f13bca58e8ff2` (Compose file unchanged); `deploys.log`: start 12:30:51Z, recovery point 12:30:52Z (`sqlite ok .../db/coach.sqlite3 311296`), healthy 12:30:55Z; container started 12:30:52Z, 0 restarts |
| Recovery point | `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T123051Z.tar`, 4,741,120 B |
| Memory | 3.1 MiB right after start, 20.1 MiB after about 6 minutes of browser checks (`docker stats`, limit 4 GiB); no OOM kill and no error or warning line in the container log |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"`:

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=fe9f13bca58e8ff2
recorded=fe9f13bca58e8ff2
iracing-coach-app-1   Up 25 seconds (healthy)   iracing-coach:035a78b61be77d4c4e708097c267f90e9abc6b35
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`

`curl -s http://192.168.1.81:8210/api/v1/status` after the release: `nas.state ok` (`last_ok_at` 12:30:53Z, `pending_archive 0`; still `ok` after the checks, `last_ok_at` 12:36:53Z), queues empty, spool 122 B in 2 files (the setup blobs; unchanged, `contracts/nas-layout.md`), `python_version 3.12.15`, `worker_profile_version post-race-foundations-v23`, `api_version 1`, `collector_protocol 1`, `live_table_version 1`, `projection_version 1`, `collectors []` (the offline dev-PC collector listed before the restart is not listed after it).

The status API carries no commit, so the commit is shown three ways: the image tag the stack reports, the container's image ID equal to the build's config digest, and the SPA the service serves (`index.html` loads `index-CZnMMfW2.js` and `index-BvbicCWi.css`, as this build emitted them; the previous release served `index-JIRUEp8w.js`).

### Browser checks (Playwright Chromium on the dev PC, emulated viewports)

- `node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\spa-after` (default session 5, the Iowa race), 12:31:53Z to 12:32:36Z, exit 0. It now warms the browser first: `warm-up load 7339 ms (not checked)`.
- `node tools/rig/v1_candidate_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\v1-after-s5 --session 5`, 12:33:01Z to 12:34:25Z, exit 0; then `--session 1` (`v1-after-s1`), 12:34:25Z to 12:35:49Z, exit 0.
- A targeted check of the two polished items at all three viewports (a scratch script, not in the repository: it reads the API for the truth, here the trace catalog and the setup list, and compares what the page shows with it): 105 of 105 pass.

| Check | Desktop 1280x720 | Tablet 1024x768 | Phone 390x844 |
|---|---|---|---|
| `/` opens the single driver's Sessions | pass | pass | pass |
| Library rows equal the API, all `ready` (6; Race, Practice, Test labels) | pass | pass | pass |
| Library loaded after the warm-up (ms) | 180 | 175 | 206 |
| Race / Runs / Sections / Traces render, session 5, `spa_check` (ms) | 222 / 37 / 19 / 115 | 188 / 6 / 41 / 212 | 209 / 5 / 44 / 181 |
| Sections / Traces render, `v1_candidate_check` sessions 5 and 1 (ms) | 228 / 245 and 226 / 193 | 217 / 218 and 224 / 195 | 204 / 213 and 199 / 196 |
| Sections cursor follows the pointer (62.5 % to 78.4 % lap) | pass | pass | pass |
| Traces map readout follows the chart cursor | 29.8 % to 70.7 % | 29.8 % to 71.0 % | 29.5 % to 72.3 % |
| **Sections fit the viewport**, sessions 5 and 1 | 720 / 720 px | 768 / 768 px | stacked by design: 1,553 and 1,662 px |
| **Traces fit the viewport**, sessions 5 and 1 | 720 / 720 px; map and all 5 rows inside | 768 / 768 px; map and all 5 rows inside | stacked by design: 1,509 and 1,565 px; map in view, rows below it |
| **Requests during a 10 s pointer sweep** (Sections, Traces; about 600 moves each), sessions 5 and 1 | 0 | 0 | 0 |
| Requests during the 5 s Traces sweep (`spa_check`) | 0 | 0 | 0 |
| Setups: rows equal the API (9), each of the 6 named setups has a row naming it | pass | pass | pass |
| Setup detail shows values (`spa_check` opens the first row, now the synthetic `e2e garage capture.sto`, 3 values shown; it was the NH setup, 62 shown, before the garage replay added setups 5 to 8) | pass | pass | pass |
| Live page without a collector | "Waiting for data" (or "Connecting" while the socket opens), `data-live false` | same | same |
| No horizontal page scroll on any page | pass | pass | pass |
| No console error, no failed (>= 400) response | pass | pass | pass |

`spa_check.mjs`: 0 failures of 81 results. `v1_candidate_check.mjs`: 0 failures of 65 results for session 5 and 0 of 65 for session 1.

### The two polished items, on the real service

**Setups.** Three setups share the file name `NOAPS_MaconiSetupShop NHMS 26S3 R.sto` with different values. Each library row now says when its version was seen and how many other versions there are; the six other rows keep `last <date>` and carry no note (all three viewports):

| Setup | Chips | Meta line |
|---|---|---|
| 1 | Raced, Recording | Toyota Supra Class B · 3 sightings · Aug 1, 2026 to Aug 2, 2026 · Same file name, different values (2 others) |
| 2 | Recording | Toyota Supra Class B · 1 sighting · Aug 1, 2026 · Same file name, different values (2 others) |
| 3 | Recording | Toyota Supra Class B · 1 sighting · Aug 4, 2026 · Same file name, different values (2 others) |

Each detail view says why, for example setup 1: "2 other setups carry the file name NOAPS_MaconiSetupShop NHMS 26S3 R.sto: the file was changed between sessions, so each version has its own values. This one was seen Aug 1, 2026 to Aug 2, 2026, in 3 recordings. Compare with the others below." (setups 2 and 3: "seen Aug 1, 2026, in 1 recording" and "seen Aug 4, 2026, in 1 recording"). The compare picker lists same-name setups by seen span instead of a time, and the detail of a uniquely named setup (8) has no note. In the first release the three rows differed only by sightings and the last date.

**Traces.** Each race's Traces were opened fresh (the lap selection is not persisted) and the lap rail was read against the backend's trace catalog (`comparison_eligible`, `exclusion_reasons`, `complete`):

| Race | Laps (comparable) | Partial lap | Default selection | Lap rail |
|---|---|---|---|---|
| Session 5, Iowa Speedway race (87697310) | 57 (0) | 56, 12.833 s | 10 (24.944), 12 (24.887), 47 (24.890) | 56 `Partial` and not selected; the other 56 laps `Excl` |
| Session 2, New Hampshire overlap race (87648500) | 82 (29) | 81, 10.417 s | 59 (30.825), 60 (30.786), 61 (30.887) | 81 `Partial` and not selected; the 52 other non-comparable laps `Excl` |
| Session 1, New Hampshire G1 race (87641773) | 80 (0) | 79, 9.283 s | 7 (30.619), 8 (30.551), 9 (30.632) | 79 `Partial` and not selected; the other 79 laps `Excl` |

No default selection holds a partial lap (3 races at 3 viewports), and every `Partial` and `Excl` mark equals the catalog's (0 mismatched rows in 9 of 9 reads). The first release's Iowa Traces opened on laps 12, 47 and the partial 56. A partial lap stays selectable by hand: clicking its row selects it and clicking again removes it (all three races, three viewports). In sessions 1 and 5 the backend marks no lap comparable, so the default falls back to the three fastest complete green laps; session 2 has 29 comparable laps and takes its three fastest.

### Not measured here

- Tailnet access and a real phone or tablet: the viewports are emulated in Chromium on the dev PC.
- No recording was uploaded, so ingest, analysis and view projection did not run on this image (they last ran on the previous image). The commit changes the SPA (static files), contract text and one server detail: `coach-views` retries its final directory rename only when Windows reports `PermissionDenied`, so Linux behaviour is unchanged, but only a new upload would exercise it on the mini PC.
- A cold re-analysis: the worker and its profile did not change.
- The sim-PC steps listed under "Blocked on the sim agent" are unchanged.

### Findings for owners

| Owner | Severity | Finding |
|---|---|---|
| W7 | resolved | Same-name setups: the three `...R.sto` rows now carry their seen span and "Same file name, different values (2 others)" (first record, finding 1). |
| W8 | resolved | No default selection holds the partial final lap, and the rail marks it `Partial` (first record, finding 2). |
| Integration (`tools/e2e/`) | resolved | `spa_check.mjs` warms the browser: the unmeasured warm-up load took 7,339 ms and each viewport's library then loaded in 175 to 206 ms (first record, finding 3). |
| W8 | observation | In a race where the backend marks no lap comparable (sessions 1 and 5: traffic, caution, repair context, partial), every lap row carries `Excl`, so the mark separates nothing and its reason is only in the tooltip. Consider showing the leading reason, or dropping the mark when every row has it. |
| W7 | negligible | On a direct move from one setup's detail to another's (URL edit, history), the page renders once with the previous setup's detail under the new id before the data settle (`useResource` returns the old key's data for the render before its effect resets it), so the same-name note reads "Another setup carries ..." with the previous setup's span. A MutationObserver measured 1 to 4 ms in the DOM and a settled page by about 40 ms, so a user does not see it; the first run of the targeted check read that render and failed 3 of 9 detail checks until it waited for the settled text. Worth knowing wherever a component mixes route parameters with `useResource` data. |

Screenshots, JSON reports and logs (scratch, not in the repository): `<scratchpad>\w9r\shots\spa-after\`, `v1-after-s5\`, `v1-after-s1\`, `polish\` (`{desktop,tablet,phone}-setups.png`, `desktop-setup-3-detail.png`, `desktop-traces-s{1,2,5}.png`, `verify-polish.json`), and `<scratchpad>\w9r\{release,spa_check,v1_candidate_check,verify_polish}.log`.
