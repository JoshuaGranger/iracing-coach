# Round 6 release (2026-10-06 to 2026-10-09)

Complaints #85 to #97 (`../backlog.md`, Done). Source commits `2e72953` (race best and pace from green laps, run trend lines with slope, R/Q/P order and toggles, quieter type), `2254a1b` (best and race pace count laps driven on with damage, #95) and `0bf443a` (lap 0 at the grid position, #96; a race recording that stops before the checkered reads DNF, #97). Checked on the live service `http://192.168.1.81:8210` on 2026-10-09 14:40 to 14:50Z from the dev PC; everything below the release facts is read-only except the one `reanalyze` call named.

## Release facts

| | |
|---|---|
| Running image | `iracing-coach:0bf443a2f3c05baa05ecdafc72fc84054cec2967`, container `iracing-coach-app-1` healthy, up 46 hours (started 2026-10-07 16:55:26Z; the stack's `deploys.log` has `start` 16:54:50Z, recovery point `iracing-coach-20261007T165450Z.tar`, `healthy` 16:55:29Z) |
| Version | `/health` `{"schema_version":3,"server_version":"0.19.0","status":"ok"}`; the workspace version did not change in round 6, so the build is told apart by the image tag. `GET /api/v1/status`: `worker_profile_version post-race-foundations-v26`, `projection_version 5`, `nas.state ok`, collector `0.19.0` online, `sim not_running`, upload queue 0 |
| Start-up log | `setup identity unchanged` (72 setups), `offline sessions unchanged` (658 recordings, 310 with the lap counter stopped), `session overviews refreshed` (58 sessions) |
| `main` at `90f863f` | Differs from `0bf443a` only by 11 files under `docs/08-rewrite/mastery-2026-10-08/` (the mastery pack: no code, no Dockerfile input), so the running image is the current code and **no new release was made on 2026-10-09**. The 2026-10-07 release was done without a release record; this file is that record |

## Re-analysis

| When | Call | Result |
|---|---|---|
| 2026-10-07 16:55:30Z (right after the start) | `POST /api/v1/admin/reanalyze`, job 5200 | `{"queued": 338}`; the analysis lane then ran at least 78 `analyze` jobs (the first 200 jobs the API lists) and 78 `project` jobs, last finished 17:08:01Z; every one `succeeded` |
| 2026-10-09 14:44:44Z | `POST /api/v1/admin/reanalyze` `{}`, job 5299 | 202, `succeeded`, `{"queued": 310}`: the 310 recordings whose analysis is deferred with `lap_counter_stopped` (Lab bot recordings, never joined), which are re-offered on every call and stay deferred. Afterwards `queue {analysis 0, deferred 318, light 0}`, the same as before the call |
| Sessions | 396 session rows: 78 ready with no reason, 310 ready with `lap_counter_stopped`, 8 ready with `exceeds_memory_budget`; both Charlotte races below read profile `post-race-foundations-v26`, projection 5 |

**DNF confirmed.** The race of 2026-10-06 9:09 PM local (02:09Z on 10-07, session 670): the Sessions line reads `FINISH DNF · L14` (before: P6). Its race view carries `race_summary.finish` `{status recording_ended, finished false, last_lap 14, position_at_end 6, last_session_state racing, laps_remaining_at_end 76}`. The 10:09 PM race (session 674) reads `DNF · L35` (`last_lap 35, position_at_end 17, laps_remaining_at_end 9`). Of the 35 races in the library, 24 read finished and 11 read DNF.

## Seen on the live site

Playwright Chromium against the live server, one page at each of 1440x900, 1024x768 and 390x844 (no horizontal scroll, no console error and no failed response at any size). Files in `../../releases/round6/`.

| Acceptance line | 1440 | 1024 | 390 |
|---|---|---|---|
| #86/#88 Sessions: R, Q, P chips in the heading; Charlotte of 10-06, race line first, then qualifying, then practice; `DNF · L35`, `DNF · L14` (#97), Best and Pace on the race lines (#85) | `sessions-1440.png` | `sessions-1024.png` | `sessions-390.png` |
| #97 Race chart of session 670: position line ends at `DNF · L14` | `race-670-dnf-1440.png` | `race-670-dnf-1024.png` | `race-670-dnf-390.png` |
| #96/#97 Race chart of session 674: lap 0 at the grid position, `DNF · L35` | `race-674-dnf-1440.png` | `race-674-dnf-1024.png` | `race-674-dnf-390.png` |
| #90/#93 Runs of the 10-06 practice (session 671): each run's trend line in its colour with `+0.08 s/lap · 11 laps` at the line's end, run cards as a grid with Fit `+0.08 s/lap · 11 laps` | `runs-671-trend-1440.png` | `runs-671-trend-1024.png` | `runs-671-trend-390.png` |

Seen and not changed: on the 1440 Runs chart of session 671 the two slope labels (`+0.12 s/lap · 11 laps`, `+0.08 s/lap · 11 laps`) sit over data dots where the runs overlap. The Runs tab of the race in session 674 shows Fit `—` for both runs (Used 0): its green laps are screened out, so no trend is drawn, which is the rule, not a fault.

## The missing Charlotte race (Joshua, 2026-10-07: "I did two charlotte races yesterday but one doesn't show up")

**Finding: no race is missing on the server or on the sim. Both Charlotte races of 2026-10-06 are in his main library, analysed and reading DNF. Not a bug in storage, filing or analysis; one open question for Joshua at the end.**

Checked, in order:

1. **Recordings and sessions tables** (read-only, `mode=ro`, inside the container). 10 Charlotte recordings, all `ingest_state discovered`, all `nas_state archived`, no `failed`, no blob without a recording row, no failed or cancelled job. They make seven session rows, all `library main`, all `ready`:

| Event (subsession) | Local time (CDT) | Phase | Session | Recordings | Result |
|---|---|---|---|---|---|
| 89180179 | 8:32 PM | Practice | 668 | 2 | 41 laps |
| 89181419 (series 62, open setup) | 9:03 PM | Lone Qualify | 669 | 2 | P7 |
| 89181419 | 9:09 PM | **Race** | 670 | 2 (16 s start file, then the race) | **DNF L14**, 13 laps |
| 89181391 (series 103) | 9:34 PM | Practice, 63 min, 46 laps, 3 stops | 671 | 1 | P12 |
| 89182578 (series 103, fixed setup) | 10:01 PM | Practice | 672 | 1 | 2 laps |
| 89182578 | 10:04 PM | Lone Qualify | 673 | 1 | P2 |
| 89182578 | 10:09 PM | **Race** | 674 | 1 | **DNF L35**, 34 laps |

2. **Join rule.** No Charlotte recording is filed into another session: each subsession id keeps its own rows, and the two races have different subsession, series (62 and 103), season and setup.
3. **Deferrals.** None of the seven sessions is deferred. `exceeds_memory_budget` (8 sessions) and `lap_counter_stopped` (310) belong to the IRP and Kansas Lab and bot recordings, none to Charlotte.
4. **Lab windows.** Windows 11 (2026-10-06 17:19:43Z to 18:21:42Z) and 12 (18:39:45Z to 23:26:44Z) are Supra at `irp`; both close before the first Charlotte recording (01:32Z on 10-07) and name another track, so they cannot match one. The API lists all seven Charlotte sessions as `library main`, and the first 200 Lab sessions (newest first) hold Indianapolis Raceway Park only for 2026-10-06 (sessions 658 to 667). Windows 11 and 12 caught no Charlotte race.
5. **Sim PC (DESKTOP-SIM, read-only; `iRacingSim64DX11` not running, collector idle, no lease taken, nothing changed).** `Documents\iRacing\telemetry` holds exactly the same 10 Charlotte files by name (20-32-43, 20-35-11, 21-03-55, 21-05-30, 21-09-00, 21-09-26, 21-34-03, 22-01-50, 22-04-07, 22-09-13) and no other 10-06 evening file, and none dated after 10-06. The collector's `journal.jsonl` has a `recording`, `recording_done` and `seen` line (with the sha256 the server holds) for each of the 10; no `rejected` line; the log has no error. Garage visits (setup snapshots) for the evening show the same subsessions and phases as the table; there is no visit or recording for any other Charlotte race.
6. **Server availability.** The stack's `deploys.log` shows two starts on 10-07, at 01:49:40Z and 01:59:28Z, while he was in the 8:35 PM practice; every file still reached the server (blob `received_at` between 01:34:18Z and 03:37:31Z, the last the 10:09 PM race, 108 MB) and was archived to the NAS.

**What the data does show that could read as a missing race.**

- Joshua's two races are 9:09 PM (session 670) and 10:09 PM (session 674). The 9:09 PM race is **his DNF at lap 14** and its date is 10-06 local but 10-07 in UTC, which is why the 2026-10-07 note names it "10-07 9:09". Before the 2026-10-07 16:55Z release it read `P6` with no DNF; he may have taken that line for the wrong race, or looked while one of the two was not yet analysed (the 10:09 PM race was discovered at 03:37:32Z, as he left).
- The Sessions list shows **four** Charlotte event cards for the evening, two of them practice only (8:32 PM, 9:34 PM). The 9:34 PM card is a 63-minute practice in series 103 that ends at 03:00:47Z; the next recording, 22 seconds later at 10:01 PM, is the practice of a different subsession (89182578) that holds the 10:09 PM race. If a third race was run in subsession 89181391, iRacing never wrote it: the file ends in session 0 and nothing follows it on the sim.
- One stretch has no telemetry: the 9:01:36 to 9:03:55 PM pre-race practice of subsession 89181419 (the garage visit exists, no `.ibt` was written; iRacing writes a file only while the car is on track).

**Fix.** None to code. Nothing was lost, filed wrongly or left unanalysed. The one real defect found on the way is already shipped: the pre-release `P6` on a race he was wrecked out of (#97).

**Open question for Joshua.** Which time and which card does he not see? If it is neither the 9:09 PM nor the 10:09 PM race, the sim holds no recording of it. If it is the 9:34 PM card, that session is practice by iRacing's own header (`event_type Practice`, `sim_session_num 0`, field of 1 recorded).

## Also

- `git push` was not used; nothing was pushed to GitHub.
- The mastery pack (`../mastery-2026-10-08/`, commits `a2242fb`, `8e4e394`, `90f863f`) was committed to `main` after the release and is the reason `main` is ahead of the running image.
