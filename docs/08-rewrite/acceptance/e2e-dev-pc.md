# Wave 3: dev-PC end to end (integration lead)

Run 2026-10-03 05:01Z to 05:28Z on DESKTOP-JOSHUA. Everything ran as normal processes with scratch state; no sim, no container, no change to the mini PC. Gate definition: `workstreams.md`, Waves, wave 3 ("dev-PC end to end green"). Defects are listed at the end by owner.

## Result

The chain works end to end on real recordings: finalization gate, resumable upload with a collector killed mid-file, ingest, NAS archive, Python analysis, projection, library, investigation, setups, live presence and tiles from a replayed SDK stream, the on-track upload gate and garage capture. Not green: the investigation's Traces view is not viewport-bound in the real shell at desktop and tablet sizes (W8), and with a collector connected the SPA refetches every driver resource every 5 s, so requests leave the page during pointer movement (W7). Both are majors below.

## Build

| Item | Value |
|---|---|
| Tree | `main` at `0686351`; the server built from it unchanged (cargo's fingerprint for `coach-server` predates W4's uncommitted `server/src/jobs.rs` edit, 05:04:31Z), `tools/e2e/` new |
| Server | `cargo build --release -p coach-server` (`CARGO_TARGET_DIR=target/e2e`, 4 jobs): 181 s, exit 0; `coach-server.exe` 15,133,184 B, SHA-256 `b6df5662...ecfff` |
| Collector | `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc`: 50 s, exit 0; 1,578,496 B, SHA-256 `f17201eb...b35b` |
| SPA | `pnpm -C web build --outDir <scratch>\web-dist --emptyOutDir`: built in 259 ms; initial chunk `index-Cgab1wYl.js` 29.5 KB (10.4 KB gzip). The mini PC serves the same asset hashes. |

## Setup

- Server: `coach-server serve --dev` with `COACH_BIND=127.0.0.1:8291`, `COACH_DATA_DIR=%TEMP%\e2e-w3\data`, `COACH_NAS_DIR=%TEMP%\e2e-w3\nas` (the NAS stand-in), `COACH_WEB_DIR=<scratch>\web-dist`, `COACH_PYTHON=` the stdlib Python 3.12.14, `COACH_WORKER_SCRIPT` and `COACH_CONTRACTS_DIR` from the tree. `/health` 200, `/api/v1/status` `nas.state = ok`, `python_version 3.12.14`, worker profile `post-race-foundations-v23`.
- Collector: `coach-collector run --server http://127.0.0.1:8291 --iracing-root %TEMP%\e2e-w3\iracing --state-dir %TEMP%\e2e-w3\cstate --no-ui`, started with `Start-Process` so its pid was known. iRacing was not running, so the collector had "no map" off-track evidence (collector-protocol section 4).
- Watched folder: copies (timestamps kept) of four dev-PC recordings, never the originals: the New Hampshire race `582b11e2...fa74` (203,246,134 B, subsession 87641773 session 2), its practice `49374699...` (7.9 MB, session 0) and lone qualifying `527f9457...` (9.9 MB, session 1), and an Iowa Offline Testing recording `22a20e50...` (13.3 MB, SubSessionID 0). `setups\stockcars2 supra2019\` held two synthetic `.sto` files (`e2e baseline.sto`, `e2e garage capture.sto`); the dev PC has no setup files of its own.
- First attempt discarded: with all data under the session scratchpad (`...\scratchpad\e2e\server\data`, 150 characters deep) the race analysis failed `tool_error` `FileNotFoundError` on `...\archive\analysis-cache\.<sha>.json.<pid>.tmp`, a 290-character path the Windows Python cannot create. The run was restarted with the short root above; the failed run's server and collector state are kept in scratch (`failed-run-*`).

## Steps and results

| # | Step | Result |
|---|---|---|
| 1 | Race placed in the watched folder; collector started 05:08:48Z; `tools/e2e/watch_upload.py --kill-pid <pid> --kill-at 60000000` | create at +0.92 s (hash 0.9 s), 4 MiB chunks at about 200 MB/s; collector killed at offset 62,914,560; the server's durable offset settled at 96,468,992 (in-flight chunks it had already fsynced); `incoming/582b....part` 96,468,992 B |
| 2 | Collector restarted (same state folder) | first `HEAD` after restart: offset 96,468,992, then 100,663,296 (+4 MiB) 0.6 s later: the upload resumed from the server offset, not from 0; `verified` at 05:08:58.2Z |
| 3 | Ingest and jobs | `discover` and `archive` 05:08:58; `analyze` 05:08:58 to 05:09:11 (13 s); `project` 05:09:11. `analysis_id 31419c583d3959b352c3a86c`, equal to the G1 Windows oracle and to the mini PC. Verified to analysed and served: 13 s |
| 4 | NAS stand-in | `raw/58/582b....ibt` (203,246,134 B) with its manifest; both `.sto` files under `setups/`; `nas.pending_archive 0`. The race manifest has `"session_types": []` (the G1 finding, see W4) |
| 5 | Practice and qualifying moved into the folder 05:09:38Z | each uploaded, discovered into its own session row (`subsession:87641773:0` Practice, `:1` Lone Qualify), archived, analysed (1 s and 2 s) and projected by 05:09:42Z |
| 6 | Duplicate recording | collector restart with all files present: occurrences 5, jobs 14 before and after (the `seen` memo; no hash, no create). The same `UploadCreate` posted twice by hand: `200 {"state":"analyzed","offset":9916548}` both times, occurrences unchanged, no job. A byte-identical copy under a new name (`... 15-03-46 copy.ibt`): one recording row, a second occurrence with the new name, no job (contract: every create with a new name records an occurrence) |
| 7 | `POST /api/v1/sessions/1/analysis` after success | `202 {"job_id":5,"kind":"analyze","state":"succeeded"}`, nothing queued (the wording is now `x-idempotency` in `api.openapi.yaml`) |
| 8 | Replay fixture | `make_sdkfix.py <scratch>\live.sdkfix --customer-id <driver> --name <driver>`: 10,200 ticks at 60 Hz, map 26,240 B, row 1,424 B, 6 session-info updates |
| 9 | Collector `--replay live.sdkfix --speed 1` started 05:15:44.1Z (T0); Iowa recording moved into the folder at T0 + 5 s | presence: `sim in_session`, `on_track true`, driver attributed; driver `live: true` |
| 10 | On-track gate | `HEAD` of the Iowa recording answered 404 from T0 + 5 s until T0 + 94.2 s: no create and no bytes while `IsOnTrack` was 1 (90 s). The car left the track at T0 + 94.0 s; the whole file was `parsed` at T0 + 94.23 s. Discovered as session 4 (Offline Testing, Iowa), analysed in 1 s |
| 11 | Live page (`tools/e2e/live_check.mjs`, T0 + 5 s to T0 + 25 s) | at desktop, tablet and phone: Live reached 86 to 197 ms after opening, 6 tiles with values (P3, gap 0.62 s, fuel 18.2 US gal, coach cue "Building your pace range"), 369 / 247 / 189 binary frames in about 6 s (60, 30, 10 Hz plus backlog), no horizontal scroll, no console error. Sessions page offers "Live now"; the nav shows the live dot |
| 12 | Stream latch | after the browsers left (viewers 0) the collector kept `streaming: true` until the replay's sim exit, then `streaming: false`, `sim not_running`, driver `live: false` (collector-protocol section 6) |
| 13 | Garage capture | one visit `2db99cdc...`: snapshots at `UpdateCount` 3, 4, 5, 6 and 7 (the last one 0.5 s after the exit, inside the 5 s window), then `garage.commit` with `setup_name "e2e garage capture.sto"`, `sto_sha256 e853ab37...` (the file's hash), `is_modified 0`. The library shows that setup named with the file linked (`sto_files[0].linked_setup_id = 7`); `e2e baseline.sto` (the session-start name) stays "values unknown", as section 8 requires |
| 14 | SPA, collector connected (`spa_check.mjs`, `shots\dev-connected`) | see the table below; the pointer sweep saw 3 requests per viewport |
| 15 | SPA, collector stopped (`shots\dev`) | the same checks; 0 requests during each 5 s sweep |

## SPA checks (`node tools/e2e/spa_check.mjs --base http://127.0.0.1:8291 --out <scratch>\shots\dev --session 1`)

| Check | Desktop 1280x720 | Tablet 1024x768 | Phone 390x844 |
|---|---|---|---|
| `/` opens `#/d/<driver>/sessions` (one driver) | pass | pass | pass |
| Library rows equal the API (4), states `ready`, labels Race, Qualifying, Practice, Test | pass | pass | pass |
| Race, Runs, Sections, Traces render (race 105 to 129 ms, traces 104 to 213 ms) | pass | pass | pass |
| Sections: pointer moves the inspection cursor (63.5 % to 79.7 % lap) | pass | pass | pass |
| Traces: map readout follows the chart cursor (29.8 % to 70.7 %) | pass | pass | pass |
| Traces: zero requests during a 5 s, 300-move sweep | pass with no collector; **fail** with a collector connected (`GET /drivers`, 2 x `GET /drivers/<id>/prefs`, W7) | same | same |
| Setups: 7 setups listed (3 from recordings, 4 from the garage visit); a recording setup shows 62 values (`shots\dev-pre`), the first garage setup the fixture's 3 | pass | pass | pass |
| No horizontal page scroll on any page | pass | pass | pass |
| Traces fit the viewport | **fail**: document 3,716 px high, each trace row 694 px, the track map below the fold (W8) | **fail**: 3,716 px | stacked by design |
| Sections fit the viewport | **fail** by 95 px (815 px) | | |
| No console error | **fail**: `GET /api/v1/drivers/0/prefs` 404 on the first visit (W7) | same | same |

Screenshots (scratch, not in the repository): `<scratchpad>\e2e\shots\dev\{desktop,tablet,phone}-{library,race,runs,sections,traces,setups,setup-detail}.png`, `shots\dev-connected\`, `shots\live\{desktop,tablet,phone}-{live,sessions-live}.png`, `shots\probe-desktop-*.png`; logs in `<scratchpad>\e2e\logs\` (`restart-phase1.jsonl`, `restart-phase2.jsonl`, `gate-watch.jsonl`, server and collector output). After the run the server and collector this run started were stopped and every scratch copy of a recording (watched folder, NAS stand-in, spool) was deleted; the originals were only read.

## Not done here

- Real SDK map, overlay on the rig screen, `record-sdk` fixtures, G3, G4, sim-PC install, D12 copy: blocked on the DESKTOP-SIM remote agent (stopped; only the owner can restart it).
- An on-track abort of an in-flight chunk: the gated recording never started uploading on track, so the 100 ms abort path was not exercised here (W3's tests cover it).
- The W4 change in the tree that holds `archive` until `discover` ends was not in this build and is not verified.

## Defects found (owner, severity)

| Owner | Severity | Defect | Reproduction | Observed | Expected | Clause |
|---|---|---|---|---|---|---|
| W8 | major | Traces (and Sections) are not viewport-bound inside the app shell | dev server above, open `#/d/<id>/s/1/traces` at 1280x720 or 1024x768 | `document.scrollHeight` 3,716 px; 5 rows of 694 px (3-lap qualifying: 274 px rows, 1,616 px); the track map sits below the fold; Sections 815 px at 1280x720. W8's harness passes because it mounts the workspace in its own bounded page | Race, Runs, Sections, Traces on one viewport-bound workspace at 1280x720 and 1024x768 with map, context and critical rows visible together | `AGENTS.md` (one well-planned screen); `workstreams.md` W8 deliverables; W8 `perf/views.mjs` "fits the viewport height" |
| W7 | major | Presence events refetch every driver resource every 5 s | connect any collector, open Traces, sweep the pointer | each `presence` SSE (one per collector `status`, every 5 s) runs `invalidate("/api/v1/drivers")`, a prefix of every driver path: `GET /drivers` and `GET /drivers/<id>/prefs` twice (and sessions, setups when mounted) every 5 s; 3 requests inside a 5 s sweep at every viewport | presence updates the drivers list only (exact key, or from the event payload) | `owner-decisions` D3 ("Pointer movement never causes a request"); `workstreams.md` W8 acceptance and G2 (zero requests during pointer movement) |
| W7 | minor | First visit requests prefs of driver 0 | fresh browser profile, open `/` | `GET /api/v1/drivers/0/prefs` 404 and a console error (`app.tsx` `usePrefs(id ?? 0)`) on every first visit; `/drivers` is fetched 3 times and prefs twice on a cold load | no request without a driver; one request per resource | `api.openapi.yaml` `/drivers/{customer_id}/prefs` |
| W7 | minor | The session picker is blank | open the race of an event with practice and qualifying (`#/d/<id>/s/1/race`) | the `Session` select has options Practice and Qualifying only and shows an empty value, because `SessionDetail.event_sessions` lists the other phases | the picker shows the current session selected among the event's phases | `api.openapi.yaml` `SessionDetail.event_sessions` ("Other phases of the same event") |
| W4 | minor | Setups from recordings are unnamed | ingest a recording whose `.sto` the server does not hold | `SetupSummary.names` is `[]` and the library shows "Unnamed Toyota Supra Class B setup" although the recording names `NOAPS_MaconiSetupShop NHMS 26S3 R.sto`, unmodified | the name an unmodified recording loaded is shown (or the contract says names come only from held files and the SPA titles from observations) | `api.openapi.yaml` `SetupSummary.names`; `collector-protocol.md` section 8 (name-only link) |
| W4 | minor | Driver iRating overwritten by an offline session | ingest an Offline Testing recording (SubSessionID 0) after a race | `/api/v1/drivers` `irating` 1539 becomes 1 (iRacing writes 1 for offline sessions); also on the mini PC after this run's upload | iRating from the newest session that carries a real rating | `db-schema.sql` `drivers.latest_irating` |
| W4 | minor | Archive manifests have empty `session_types` (G1 finding, still present) | upload any recording | `recording.session_types: []` in every manifest; the tree's uncommitted `jobs.rs` change was not in this build | the session types discovery stores | `nas-layout.md` manifest; `manifest.schema.json` |
| W4 | minor | Presence SSE repeats unchanged status | `curl -N /api/v1/events` with a collector idle | one `presence` event every 5 s that differs only in `last_seen_at` | events on change (online, sim, driver, on track, streaming, queue) | `collector-protocol.md` section 5 |
| W4 | minor | `POST /sessions/{id}/analysis` returns an undocumented 422 | queue analysis of a session without recordings | `422 no_recordings` (`api.rs` `queue_analysis`) | listed in the operation's responses | `api.openapi.yaml` operation responses (202, 404) |
| W6 | minor | Worker cannot write its analysis cache under a deep data root on Windows | `--dev` with `COACH_DATA_DIR` about 150 characters deep | `analyze` fails `tool_error` `FileNotFoundError` on a 290-character `analysis-cache` temp path | the dev worker works under any data root the server accepts, or the limit is stated for `--dev` | `server-config.md` (`--dev` defaults), `worker-interface.md` section 2 |
