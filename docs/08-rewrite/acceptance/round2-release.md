# Round 2 release (2026-10-04)

Commits `5a0f273` (bug-fix wave 2 and the Lab library) and `d827360` (web round 2 wave A), released to the mini PC as `iracing-coach:d827360…`, healthy, `/health` schema 3, server 0.19.0. Spec: `../web-round-2-plan.md`; debate: `.debate/how-should-the-iracing-coach-web-app-be--20261004T143238Z/report.html`.

## What the server did at start

- Setup identity reconciled: setup 72 (the harvest's load snapshot) folded into 73, 2 observations moved, 66 setups checked, nothing unreadable.
- Session overviews refreshed: 653 (the new `green_pace_median_s` and `pace_slope_supported`).
- Offline sessions repaired (not a dry run; the rule: same sim session, same setup): 605 recordings checked, 19 merges, all of them bot Supra test sessions at IRP, Kansas and Las Vegas (the largest: 68 recordings of 2026-09-25 into session 656; the G4 pair 659 into 658); 310 recordings with the lap counter stopped at 1024, deferred, never joined; `sessions_with_mixed_stints` empty.

## Lab library

The nine historical windows of `lab-library.md` were posted (`POST /api/v1/admin/library/windows`, all 201) and `POST /api/v1/admin/library/classify` ran as a dry run, then for real, then again: 665 recordings checked, **597 filed lab**, 3 overlaps kept main (Kansas 2026-09-28 10:02–10:11 local inside the Vegas window, track mismatch), second real run: no change. Per window: 2 (IRP bot) 409, 3 (Kansas bot, to 09-28 01:00) 97, 4 (Vegas P4/P6) 89, 9 (G4 bot run) 2; windows 1, 5–8 matched nothing (those files were never in the library or were removed on 2026-10-03). Joshua's library: 51 sessions, last 2026-10-02 Vegas race; Lab: 329 sessions. Open: the seven Kansas files of 2026-09-28 09:23–10:11 local stay main until Joshua says who drove them.

## Seen on the live site

Sessions at 390 and 1440 (dense rows, Fixed/Open chips, filters in the heading with the Library control), the 2026-10-02 Vegas race investigation (runs in colour, `Not used in pace fit`, comparison brackets, the drawn comparison panel with the backend's paired figures).

## Also

- Tailscale: `https://minipc.tail4e6ca6.ts.net:8443/` (Tailscale Serve, tailnet only); Home Assistant tile updated (g-house `7a68f341`).
- iRacing-utility `3df62fb` (lab windows: `python -m feed lab open|close|list`, the harvest's own window) and `0d8898e` (feed contract excerpt re-recorded at `d827360`), pushed.
- Not yet on the sim: the collector build with the CPU fix (W3) and the caution gate is in the live engine (W2) — install at the next free sim window (`collector-install.md`), then re-measure G4-3.
- Wave B (Crew Chief with the field stream, the Driver page) waits for Joshua's look at wave A.

# Round 3 release (2026-10-04, evening)

Commit `d608ad8` (complaints #33–#49, spec in `../web-round-2-plan.md` "Round 3"), released as `iracing-coach:d608ad8…`, healthy. The collector on the sim was updated to the `a256511` build earlier the same day (`collector-install.md`).

- Start: setup identity unchanged (65), offline sessions unchanged (605 recordings, 310 with the lap counter stopped).
- The projection moved to version 3 (per-lap section times); a stored analysis re-projects on its first request and its overview then carries the round-3 fields (`longest_run_laps`, `longest_run_pace_loss_s`, `timed_lap_1_s`, `timed_lap_2_s`). Every analysis of Joshua's 51 sessions was requested once after the release to re-project them up front; the first request answers `409 projection_pending` and the next one `200`.
- Seen on the live site by page text: Sessions rows as race / practice (Pos, Best, Run, Fall-off) / qualifying (Pos, Lap 1, Lap 2) blocks; no Library control; filters without Clear.
- Known: races whose analysis is deferred (`exceeds_memory_budget`, backlog #13) show `—` for best and pace, as before.

# Round 4 release (2026-10-04, late)

Commits `b0b539e` (complaints #50–#66) and `47eb540` (only round screenshots stay tracked under `web/target`), released as `iracing-coach:47eb540…`, healthy. Analysis profile `post-race-foundations-v24` (track geometry rule `clean-racing-laps-v1`): every stored analysis is older than the worker's profile, so Joshua's 51 sessions were queued for re-analysis first (`POST /api/v1/sessions/{id}/analysis`, 51 × 202); the Lab sessions follow through `POST /api/v1/admin/reanalyze` once his are done. Until a session is re-analysed its map is the old outline.

# Round 5 release (2026-10-06)

Commit `bfef9d7` (complaints #67–#83, spec `../web-round-2-plan.md` "Round 5"), released as `iracing-coach:bfef9d7…`, healthy. Analysis profile v25 (640-bin `lap_trace_columns`, traffic sides per lap), projection 4: Joshua's 51 sessions were re-queued first (51 × 202), then `POST /api/v1/admin/reanalyze` (job 4806) for the rest. Until a session is re-analysed its traces stay at 160 bins and its traffic marks carry no side.

Also this day: the agents' build caches had filled C: (1.9 GB free): 131 GB of per-agent Rust targets under `target/` and 98 GB under WSL `~/.cache/iracing-coach` were deleted; all agent scratch now lives under `G:\Agent Files\` with a README and a LEDGER (memory `agent-scratch-on-g-drive`); the WSL disk is sparse and needs an in-distro `fstrim` (sudo) to give its space back to C:. WSL hung once while C: was full; Joshua restarted it (`taskkill /F /IM wslservice.exe`).
