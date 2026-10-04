# Lab library: the rule, the historical windows, the dry run

Task A5 of web round 2 (`web-round-2-plan.md`, "Lab library"; backlog #18, resolves #14). Written 2026-10-04 on the dev PC. Nothing here has been applied to the mini PC: the release applies the window list below and runs the repair (dry run first).

## The rule (server, `server/src/library.rs`)

- Every recording carries `library` `main` (default, never null) or `lab` (migration 3, `server/migrations/0003_lab_library.sql`). The driver stays who drove it. Raw files, the NAS and analyses are not touched.
- A **lab window** (`lab_windows`: from, until, car, track, note, source) is the utility's statement that it drives `car` at `track` in that time. `POST /api/v1/admin/library/windows` stores one; posting the same `from`, `car` and `track` again replaces `until`, `note` and `source` (that is how the utility closes a window). A window is at most 72 hours long. Every post is logged to `run/library.jsonl`.
- **At ingest** a recording is filed `lab` when all of these hold, else `main`:
  1. its start (`start_time_utc`, the time the session list shows) is inside a window, both ends included;
  2. its `CarPath` or car screen name equals the window's `car` (case ignored);
  3. its `TrackName`, `TrackDisplayName` or `TrackID` equals the window's `track` (case ignored);
  4. it is a solo offline test: no `SubSessionID` and `EventType` Test (without an event type, a session type that names a test). The utility drives only solo offline Test Drives (iRacing-utility `AGENTS.md`), so a hosted session or **a race Joshua drove against AI is main even inside a window**.
- **Overlap rule:** a recording whose start falls in a window without the matching car, track or session kind stays `main`; the repair lists it under `overlaps_kept_main` with the reason (`car`, `track`, `not_solo_offline_test`).
- **Sessions** are `lab` when any of their recordings is (`SessionSummary.library`): the stints of one sim session are one drive, and Lab never enters the main counts.
- **Queries:** the session list, the driver and driver-list statistics (`session_count`, `last_session_at`), the filter choices (`cars-and-tracks`) and the setup library's usage (`observation_count`, `sources`, `raced`; a setup used only in the other library is left out; names and `.sto` links come from every observation) take `library` and default to `main`; `library=lab` is explicit; any other value is `400`.
- **Repair:** `POST /api/v1/admin/library/classify` applies the stored windows to every parsed recording with the same rule. `dry_run` defaults to true; the real call (`{"dry_run": false}`) is one transaction, idempotent (a second call reports no change) and logged to `run/library.jsonl` with its report. It also files a recording back to `main` when no window holds it any more.
- After `coach-server rebuild-index` the windows are gone with the old database: post the list below again and run the repair.

## Historical windows (the list the release applies)

Times are the sim's local time (CDT, UTC-5) as iRacing names its files; the server stores UTC. Car is `stockcars2 supra2019` (NASCAR O'Reilly Toyota Supra) in every window. Evidence is in iRacing-utility (`lab-notes/`, `testdriver/README.md`, `testdriver/TUNING-NOTES.md`, `testdriver/model/irp_grip.json`), the IRP study bundle (`Documents\Codex\irp-bot-study-2026-09-26`: `data/ibt-index-20260927.csv`, `vegas/`), the sim's telemetry listing of 2026-10-03 (`coach-migration/w9/telemetry-names.txt`, 631 names) and Coach's own acceptance records.

| # | Local (CDT) | Track | What drove | Evidence | Kept main around it |
|---|---|---|---|---|---|
| 1 | 2026-09-24 22:00 to 09-25 11:00 | `irp` | vJoy/mouse bot bring-up, push test day (runs A to J), fastest-lap attempt, vJoy session | `TUNING-NOTES.md` run tables (IBTs `2026-09-24 22-19-28` to `2026-09-25 02-41-07`), the fastest-lap attempt 11:00-11:25Z, `README.md` "Live: vJoy session (2026-09-25, 09:48-10:30)", `irp_grip.json` bot files to `09-55-55` | Joshua's own IRP files of 2026-09-20 and 2026-09-23 (last `21-09-03`, his race) end a day before |
| 2 | 2026-09-25 15:00 to 09-27 08:00 | `irp` | optimal-lap blocks 1 to 11, the 60-lap programme, Goals 1 to 6, probe mode, registered blocks, blind survey | `irp_grip.json` bot files from `2026-09-25 15-28-47`; `TUNING-NOTES.md` blocks 5 to 11 (22:31Z on); study README timeline to 27 Sep 06:05Z; `ibt-index-20260927.csv` (273 files, `2026-09-26 00-10-47` to `2026-09-27 01-05-54`); `README.md` probe and registered-block sections (27 Sep, `--until 2026-09-27T11:30:00Z`); telemetry listing ends `2026-09-27 07-38-27` | nothing of Joshua's at IRP in the listing between |
| 3 | 2026-09-27 09:30 to 09-28 10:15 | `kansas oval` | Kansas bot (model, pedals and control; Codex driver audit) | `README.md` "Kansas: one model for plan, pedals and control (Codex audit, 2026-09-27/28)"; `refs/kansas_v*.json` from `2026-09-27 09-45-14` to `10-10-08`; listing: 97 files 09:45-23:53 and 7 files 2026-09-28 09:23-10:11 | **the 2026-09-28 morning part is inferred** (the section's 27/28 dating and the Vegas session starting 15:15Z right after); Decision 2026-10-04: the release applies the window to 2026-09-28 01:00 only; the seven 09:23-10:11 files stay main until Joshua says whether he or the bot drove them (a wrong Lab filing would hide his data; a wrong main filing only clutters). |
| 4 | 2026-09-28 10:00 to 20:30 | `vegas oval` | Las Vegas blind setups P4, the head-to-head P6 | `vegas/p5/session-default-vegas.yaml` (same session since 15:15Z); `LV-P4-table.csv`, `LV-P6-table.csv` (IBTs `10-18-23` to `19-49-27`); `TUNING-NOTES.md` P6 stopped 00:57Z 29 Sep | Joshua's Vegas evening of 2026-09-29 (`20-33-06` to `21-09-49`, practice, qualifying, race) |
| 5 | 2026-10-02 16:50 to 18:30 | `vegas oval` | P3 parked car, P5 garage test | `library-cleanup.md` ("Left for the owner": `16-57-17`, `18-18-02`) | Joshua's 2026-10-02 races (`09-39-56` to `10-09-39`, `19-52-50` to `21-08-50`) |
| 6 | 2026-10-03 08:50 to 10:10 | `vegas oval` | G3 garage capture, G4 parked car | `g3-garage.md` (13:54-14:11Z: `09-01-49`, `09-08-36`), `g4-performance.md` (lease to 15:07:35Z: `09-45-56`) | |
| 7 | 2026-10-03 23:00 to 23:30 | `vegas oval` | setup harvest pilot (garage visits) | `lab-notes/harvest-2026-10-03.md` (04:05-04:27Z on 10-04; "no recording, the car never went on track") | |
| 8 | 2026-10-04 07:43 to 07:54 | `vegas oval` | setup harvest resume (two garage visits) | `harvest-2026-10-03.md` resume table (12:43-12:54Z) | |
| 9 | 2026-10-04 08:00 to 08:52 | `irp` | G4 bot run: sessions 658 (`08-11-20`) and 659 (the `08-39-52` stub) | `g4-performance.md` bot-driven part (lease 13:05-13:51Z) | |

No harvest visit at IRP on 2026-10-03 is recorded in the notes; the only IRP garage visits of the harvest tools are the G4 run's setup load on 2026-10-04, inside window 9. The harvest windows 7 and 8 are kept so that a recording from those visits, if one exists anywhere, files lab.

Not covered on purpose: Joshua's own sessions of 2026-07-27 to 2026-08-03 (Taurus, Supra, Tundra; the dev PC's telemetry), 2026-09-20 and 2026-09-23 at IRP, 2026-09-29 at Talladega and Las Vegas, and his 2026-10-02 races; the end-to-end replays of his real sessions (W9) are his recordings.

The body the release posts, one window per call (`source` `historical`):

```json lab-windows
[
  {"from": "2026-09-24T22:00:00-05:00", "until": "2026-09-25T11:00:00-05:00", "car": "stockcars2 supra2019", "track": "irp", "note": "1 bot bring-up, push test day, fastest-lap attempt, vJoy session", "source": "historical"},
  {"from": "2026-09-25T15:00:00-05:00", "until": "2026-09-27T08:00:00-05:00", "car": "stockcars2 supra2019", "track": "irp", "note": "2 optimal-lap work, 60-lap programme, Goals 1-6, probe and blocks", "source": "historical"},
  {"from": "2026-09-27T09:30:00-05:00", "until": "2026-09-28T01:00:00-05:00", "car": "stockcars2 supra2019", "track": "kansas oval", "note": "3 Kansas bot (the 28 Sep 09:23-10:11 files wait for the owner)", "source": "historical"},
  {"from": "2026-09-28T10:00:00-05:00", "until": "2026-09-28T20:30:00-05:00", "car": "stockcars2 supra2019", "track": "vegas oval", "note": "4 Las Vegas P4 and P6", "source": "historical"},
  {"from": "2026-10-02T16:50:00-05:00", "until": "2026-10-02T18:30:00-05:00", "car": "stockcars2 supra2019", "track": "vegas oval", "note": "5 P3 parked car, P5 garage test", "source": "historical"},
  {"from": "2026-10-03T08:50:00-05:00", "until": "2026-10-03T10:10:00-05:00", "car": "stockcars2 supra2019", "track": "vegas oval", "note": "6 G3 garage capture, G4 parked car", "source": "historical"},
  {"from": "2026-10-03T23:00:00-05:00", "until": "2026-10-03T23:30:00-05:00", "car": "stockcars2 supra2019", "track": "vegas oval", "note": "7 setup harvest pilot", "source": "historical"},
  {"from": "2026-10-04T07:43:00-05:00", "until": "2026-10-04T07:54:00-05:00", "car": "stockcars2 supra2019", "track": "vegas oval", "note": "8 setup harvest resume", "source": "historical"},
  {"from": "2026-10-04T08:00:00-05:00", "until": "2026-10-04T08:52:00-05:00", "car": "stockcars2 supra2019", "track": "irp", "note": "9 G4 bot run, sessions 658 and 659", "source": "historical"}
]
```

Release steps: post each window (`POST /api/v1/admin/library/windows`), `POST /api/v1/admin/library/classify` with `{}` (dry run) and read `changes` and `overlaps_kept_main`, then `{"dry_run": false}`, then once more with `{"dry_run": false}` (expect no change). Check: sessions 658 and 659 and the 2026-09-25 bot sessions are absent from `GET /api/v1/drivers/1507810/sessions` and present with `?library=lab`; Joshua's 2026-10-02 race and his 2026-09-23 IRP race are in his list.

## Dry run on the dev-PC fixtures

`tests/lab_library.rs::corpus_dry_run` reads every `.ibt` under `COACH_LAB_CORPUS` with the real parser and files it by the list above (the block is read from this file; the same rule as the repair). Run 2026-10-04 over the dev PC's four recording folders, read only:

| Folder | Recordings | Lab | Main |
|---|---|---|---|
| `Documents\iRacing\telemetry` (Joshua, 2026-07-27 to 2026-08-03: Taurus, Supra, Tundra) | 49 | 0 | 49 |
| `source\coach-migration\rig-W3-2026-10-03\pack` (G3 garage capture, Vegas `09-01-49`, `09-08-36`) | 2 | 2 (window 6) | 0 |
| `source\repos\iracing-testdriver\data` (Joshua 2026-09-20 x2 and 2026-09-23 x6; bot 2026-09-25 `17-56-25`, `18-21-40`, `18-52-34`) | 11 | 3 (window 2) | 8 |
| `source\repos\iracing-testdriver\data\g2` (bot Goal 2, 2026-09-26 `06-20-53` to `08-22-26`) | 11 | 11 (window 2) | 0 |
| **Total** | **73** | **16** | **57** |

0 unreadable, 0 overlaps kept main. Every recording of Joshua's own (the July and August sessions, IRP 2026-09-20 and 2026-09-23, including his best lap `2026-09-23 21-04-24` and race `21-09-03`) stays main; every bot and rig file is lab.

For the live library, the same windows over the sim's telemetry listing of 2026-10-03 (631 names; start and car and track from the names, every file assumed a solo offline test, so this is an upper bound): 608 lab (window 2: 409, 3: 104, 4: 90, 5: 2, 6: 3) and 23 main (Talladega 2026-09-29 x4, Las Vegas 2026-09-29 x6, Las Vegas 2026-10-02 x13). The server's own dry run is the number to read before applying; the bot sessions 658 and 659 of 2026-10-04 (window 9) came after the listing.

## Tests

`cargo test -p coach-server` (`tests/lab_library.rs`): ingest classification (the bot run and its stub lab, a Tundra test and a race against AI in the same window main, a later test main); every list defaults to main and takes `library=lab` (sessions, session detail, driver and driver-list counts, cars-and-tracks, setup usage, `400` for another value); the repair (dry run changes nothing, the real call applies the plan and logs it, a second call changes nothing, the overlap rule, a moved window files back to main); window close by re-post and the malformed bodies. The rule's unit tests are in `src/library.rs`. `tests/api.rs` exercises the three new operations against the contract.
