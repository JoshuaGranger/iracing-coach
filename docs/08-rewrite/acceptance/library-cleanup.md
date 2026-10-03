# Library cleanup: the end-to-end test entries (W9)

Run 2026-10-03 against `http://192.168.1.81:8210` (the `ae65a0a` release, `server-release.md`) with `POST /api/v1/admin/remove`, HTTP only: a dry run at 16:22:01Z, the removal at 16:27:57Z (dev-PC clock, about 2 s ahead of the server's). No SSH write, no file deleted by hand.

## Result

Removed: the two synthetic setup files the replayed end-to-end run uploaded (`e2e baseline.sto`, `e2e garage capture.sto`) and the four garage setups (5 to 8) its replayed SDK stream created. No recording was removed: every recording the end-to-end and v1-candidate runs uploaded is a copy of one of the owner's real sessions. Five recordings that agents made on the rig for the prove and gate runs are left for the owner (below).

## How the entries were identified

| Source | What it uploaded | Synthetic? | Decision |
|---|---|---|---|
| `e2e-mini-pc.md`, wave 3b replay (e2e2) | `setups\stockcars2 supra2019\e2e baseline.sto` (58 B of text) and `e2e garage capture.sto` (64 B), and the garage visits of the replayed fixture (`make_sdkfix.py`): an uncounted first attempt at 05:58Z and the counted run at 12:02Z to 12:03Z | yes | remove |
| the same run | session 6, the New Hampshire practice of 2026-08-01 (`49374699...`, 7,896,460 B), a copy of the owner's recording | no | keep |
| `e2e-mini-pc.md`, wave 3 | session 4, the Iowa Offline Testing session of 2026-08-03 (`22a20e50...`, 13,289,951 B), a copy of the owner's recording | no | keep |
| `v1-candidate.md` | session 5, the Iowa race of 2026-08-03 (`60752f79...`, 146,188,289 B), a copy of the owner's recording | no | keep |
| `g1-analysis.md` | sessions 1 to 3 (New Hampshire races and practice), the owner's recordings | no | keep |
| `e2e-dev-pc.md` | nothing on the mini PC (local dev server) | | |

The server's data agrees. Setups 5, 6 and 7 are `garage_snapshot` only and setup 8 is `garage_commit` and `garage_snapshot` named `e2e garage capture.sto`; each has the fixture's 4 values (`Chassis.Front.SteeringRatio 16.5:1`, left and right front cold pressure 154/174 kPa stepping to 160/180 kPa, `UpdateCount` 3 to 7), and their 12 observations belong to two visits only: `98d25625-2c0b-49a8-bc24-dcdc39f79521` (05:58:28Z to 05:58:39Z) and `cb0b248c-dc8f-40d9-b19d-f5924001f495` (12:02:55Z to 12:03:06Z). A scan of all 668 recordings through the session details found every file name in iRacing's own pattern, none under 200 KB, all `archived` on the NAS, and no session with a note. The offline dev-PC collector of the e2e2 run (`bcd322db-...`) is no longer listed in `status.collectors`, and the remove operation does not cover collectors.

## Dry run (16:22:01Z)

Request: `{"customer_id":1507810,"setup_files":["e2e baseline.sto","e2e garage capture.sto"],"dry_run":true}`

| Name | Found | Blocked | Files | Garage visits | Setup observations | Setups removed | Blobs removed (spool copy and database rows) |
|---|---|---|---|---|---|---|---|
| `e2e baseline.sto` | true | none | `stockcars2 supra2019/e2e baseline.sto`, driver 1507810, present, `be7dbedbde2324d0beca9ecc103ea3ef25011d22e4f7940bda79069ad8eaf481` | none | 0 | none | `be7dbedb...f481` |
| `e2e garage capture.sto` | true | none | `stockcars2 supra2019/e2e garage capture.sto`, driver 1507810, present, `38ac7cf927f131f6f1e2e893675868099f1b9cc0545a33e6bf4da0df2c15242c` | `98d25625-...`, `cb0b248c-...` | 12 | 5, 6, 7, 8 | `38ac7cf9...242c` |

`recordings: []`, `blobs_kept: []` for both, `kept`: NAS raw files and manifests, the collector's upload journal, the worker's per-driver archive, user-authored rows. Only the `stockcars2 supra2019` files matched the two names.

## Removal (16:27:57Z)

The same request with `"dry_run": false` answered 200 with the dry run's report and `applied: true` for both names (`blocked` empty), `log: /data/run/admin-removals.jsonl`. The server wrote one line to that file (`"at":"2026-10-03T16:27:55Z"`, server clock) and a `WARN coach_server::admin: admin removal applied` line to its log.

| Check after the removal | Result |
|---|---|
| `GET /api/v1/setups/5` to `/8` | 404, 404, 404, 404 (setups 9 and 10 answer 200) |
| Setup library (driver 1507810) | 64 setups (68 before), 1,173 setup files with values unknown (1,174 before) |
| Sessions | 657, unchanged (655 ready, 2 deferred) |
| Spool | 857 files, 2,170,087 B (859 files, 2,170,209 B before: the two `.sto` blobs, 58 + 64 B) |
| The same dry run again | `found: false` for both names, nothing to remove (idempotent) |
| NAS | not touched by design; the `.sto` copies under `setups/` stay, and `rebuild-index` would list them again |
| Notes, layouts, preferences | none removed; the driver's preferences are unchanged (`version 4`) |

## Left for the owner

Recordings that agents made on the rig with the owner's iRacing account during the prove and gate runs. They are real iRacing recordings, not synthetic and not from the end-to-end runs, so they were kept. One call removes any of them (`{"recordings":["<sha256>", ...]}`, dry run first):

| Session | Recording | Size | SHA-256 | Made by |
|---|---|---|---|---|
| 18 | `stockcars2 supra2019_vegas oval 2026-10-02 16-57-17.ibt` (analysis deferred, over the memory ceiling) | 270,486,936 B | `d2d7da9947f5d4f686c071af8ec4191750ace2420f17b5b320eb46124fb04f4d` | P3, parked car (`prove/P3-collector-language.md`) |
| 17 | `stockcars2 supra2019_vegas oval 2026-10-02 18-18-02.ibt` | 2,960,161 B | `a4fb3c3a68348c8b876e5fd0fafa6b98e410501391f0f0679bc076886825383f` | P5, garage test (`prove/P5-garage-setup-publish.md`) |
| 8 | `stockcars2 supra2019_vegas oval 2026-10-03 09-01-49.ibt` | 4,768,467 B | `01fa08522628e3f40a65c5618b6ef71a327b4b9a375e8fa2184af0fe8f05d545` | G3, garage test (`g3-garage.md`) |
| 7 | `stockcars2 supra2019_vegas oval 2026-10-03 09-08-36.ibt` | 1,986,468 B | `6ec7e0eb5ecf09bd6f550a6139e7301b66cff264ea2da40dc76e56172b76cabe` | G3, garage test |
| 37 | `stockcars2 supra2019_vegas oval 2026-10-03 09-45-56.ibt` | 75,857,115 B | `937d4e306df5d86fd7a4c5ab2881f9d45e4157b32d453d74ec6efd8aa8ed968e` | G4, parked car (`g4-performance.md`) |

Also kept: setup 10, `claude LV R CAM.sto`, whose garage observations come from the G4 session (the values of a real file the bot study made), and the bot-study recordings of 2026-09-25 to 2026-09-28 (Indianapolis Raceway Park, Las Vegas, Kansas), which are the owner's data gathering, not test entries.

## 2026-10-03 (evening): the five rig-test sessions removed at the owner's word

Joshua: "Remove them". Dry run first, then `POST /api/v1/admin/remove` with `dry_run false` for the five recordings listed above (sessions 18, 17, 8, 7 and 37; P3, P5, G3 and G4 parked-car and garage runs). All five applied, none blocked; one analysis, four jobs and one setup observation each were removed with them. Kept untouched: NAS raw files and manifests, the collector's journal, the worker's per-driver archive, user-authored rows. Server log: `/data/run/admin-removals.jsonl`.
