# D12 migration: re-ingest, re-analysis and import (task rig-W9)

Run 2026-10-03, 14:42Z to 15:48Z, against the released service `http://192.168.1.81:8210` (HTTP only, as a user or a collector uses it; no container or host change). Order as `workstreams.md` requires: step 1 (copy, done earlier: `migration-source-inventory.md`), step 3 (raw re-ingest), step 4 (re-analysis), step 2 (import), step 5 (this report). Private files (bundle, logs, report JSON) are outside the repository under `C:\Users\joshu\source\coach-migration\w9\`.

## Result

| Item | Count |
|---|---|
| Legacy raw archive (`Documents\iRacing Coach\data\telemetry-traces\raw\`) | **35 of 35** held by the server, **35 of 35** with a NAS manifest; every folder name equals the SHA-256 of its file (35 of 35) |
| Live telemetry folder (`Documents\iRacing\telemetry`, 631 `.ibt`, 16,237,490,875 bytes at the end of the run) | **630 of 631** held, 630 with a NAS manifest; 1 never finalized by iRacing (below) |
| Recordings on the server | 668 (20,290,383,413 bytes): all `ingest_state discovered`, all `nas_state archived`, **668 of 668** with `$NAS/raw/<aa>/<sha256>.manifest.json` (checked over SMB on `\\192.168.1.82\apps\iracing-coach`) |
| Sessions (driver 1507810) | 657: Offline Testing 608, Race 33, Practice 11, Lone Qualify 5. Analysis state: ready 572, none 82, deferred 3 |
| Setups | 68 setups (48 named, 18 raced; sources: recording 48, `.htm` export 16, garage snapshot 5, garage commit 2) and 1,174 setup files listed with values unknown |
| Import (`POST /api/v1/admin/import`, job 3538) | legacy analyses 31: **28 resolved**, 3 unresolved; tuning drafts 1 (0 unresolved); live layouts 4; notes 0 (0 unresolved); turn annotations 0; tuning experiments 0; legacy items 49 |
| Re-analysis (`POST /api/v1/admin/reanalyze`, job 3537) | `{"queued": 0}`: every session already had an analysis at profile `post-race-foundations-v23` or was deferred |

**Verdict: the raw recordings are complete (every legacy raw hash has a NAS manifest) and the import resolved everything that has a recording. Not clean: 82 sessions have an analysis but no view (a projection defect), 4 analyses are deferred by the memory ceiling (two of them owner races), 1 live-folder recording is unfinalized and cannot be ingested, and the collector has no `backfill` command. Details and owners below.**

## Step 3: raw re-ingest

| Source | How | Numbers |
|---|---|---|
| `Documents\iRacing\telemetry\*.ibt` and `Documents\iRacing\setups\**\*.sto` and `*.htm` | The installed collector's normal scan and uploader (`collector-install.md`), newest first, off track only; it ran from 14:42:57Z and drained by 15:44Z, paused for the 19 min of G4 on track | 1,850 files seen (630 `.ibt`, 1,220 setup files: 1,204 `.sto`, 16 `.htm`), 1,833 upload entries opened and all 1,833 completed (journal at 15:45Z; the 17-file difference between the `seen` memo and the upload entries was not traced); about 16.2 GB at about 6.2 MB/s with the archive upload below running beside it |
| `Documents\iRacing Coach\data\telemetry-traces\raw\<sha256>\*.ibt` | `powershell -NoProfile -ExecutionPolicy Bypass -File tools\migrate\Backfill-RawArchive.ps1 -Server http://192.168.1.81:8210 -Log ...\backfill-raw.jsonl` on the sim (new; read-only, shared read, stops if `iRacingSim64DX11` starts), 15:08:24 to 15:18:31Z | 35 recordings, 4,268,967,692 bytes: 31 uploaded (state `verified`, 0 retries, 0 offset conflicts, 583 s of transfer), 4 already held from earlier tests (`analyzed`). Each upload names the original file (from the folder's `manifest.json`, 35 of 35 equal to `raw-archive-sources.txt`) |

The collector has **no `backfill` subcommand** (D12 step 3 and `workstreams.md` name `coach-collector backfill`; `collector/src/cli.rs` has `run`, `install`, `uninstall`, `record-sdk`). For the live folder this does not matter (the uploader scans it), but the legacy archive is outside the folders it watches, hence the script. Owner: W3.

## Step 4: re-analysis

`curl -X POST -d '{"customer_id":1507810}' http://192.168.1.81:8210/api/v1/admin/reanalyze` at 15:46:38Z: job 3537 succeeded at once with `queued 0`. Discovery had already queued an analysis for every new session at the current profile (the analysis queue drained to 0 before the call). The four analyses over the admission ceiling stay deferred:

| Session | Recording(s) | Estimate | Note |
|---|---|---|---|
| 152 | `stockcars2 supra2019_kansas oval 2026-09-27 23-53-21.ibt`, 2,093,549,028 bytes | 24,236,875,095 B | the 2 GB Kansas recording: listed and deferred, as planned |
| 18 | `stockcars2 supra2019_vegas oval 2026-10-02 16-57-17.ibt`, 270,486,936 bytes | 3,271,661,037 B | a 270 MB test recording |
| 105 | Bristol (Ford Taurus) 2026-08-19 21-39-29 (197,206,664 B) + 22-30-52 (253,361 B) | 3,647,777,341 B | **legacy race** `bd86f999...` (subsession 88091655): its current analysis is the one-recording one, whose projection failed, so the session shows no view |
| 123 | Bristol (Supra) 2026-09-20 21-08-55 (146,209,324 B) + 21-47-26 (81,729,208 B) | 4,173,531,587 B | **legacy race** `fa1f0447...` (subsession 88792080): `ready` on an earlier one-recording analysis; the two-recording analysis is deferred |

The ceiling defers two of the owner's own races, not only the Kansas outlier (a 228 MB two-recording race estimates 4.17 GB). Owner: W4/W6 (estimate factors in `worker-interface.md` section 6, or the port's memory stage).

## Step 2: import

Bundle: `python tools\migrate\build_import.py C:\Users\joshu\source\coach-migration\sim-2026-10-03\copy --customer-id 1507810 --machine DESKTOP-SIM --out ...\w9\import-bundle.json --recordings ...\w9\archive-recordings.json` (the recordings map is the 35 lines of `raw-archive-sources.txt`, folder hash to original name; the copy holds no raw manifests). Bundle SHA-256 `06e153b17e39b6cdb4ac17b60d5f125e6d911da197f094098cc73e07c47e0121` (2,923,569 bytes), identical across two builds. Contents: 31 legacy analyses (28 with a recording hash), 1 tuning draft, 4 live layouts (`Race`, `Qualifying`, `Migrated 0.9.3`, `Default Copy`; 0 tiles dropped), 0 trace layouts (none user-made), prefs, 0 notes, 0 turn annotations, legacy archive of 8 files (`history.sqlite3`, `tuning/catalog/index.json`, `tuning/inputs/iowa-2026s3.json`, 5 tuning packages), 49 legacy items (43 activity, 5 strategy history, 1 settings). Not converted: `data/test-artifacts` (test output).

`curl -X POST --data-binary @import-bundle.json http://192.168.1.81:8210/api/v1/admin/import` at 15:46:48Z: job 3538 succeeded in the same second: `legacy_analyses 31, legacy_analyses_resolved 28, legacy_analyses_unresolved 3, tuning_drafts 1, tuning_drafts_unresolved 0, layouts 4, legacy_items 49, notes_attached 0, notes_unresolved 0, turn_annotations 0, tuning_experiments_imported 0`.

## Step 5: the old-to-new map

`python tools\migrate\migration_report.py --customer-id 1507810 --archive ...\archive-recordings.json --backfill-log ...\backfill-raw.jsonl --telemetry-names ...\telemetry-names.txt --bundle ...\import-bundle.json --out ...\w9\report.json` (new; read-only HTTP and SMB). Each legacy analysis resolved as the server's import does (recording hash first, then subsession and session number). Legacy session = `history.sqlite3` `sessions` row (all `Race`, sim session 2).

| Legacy analysis | Subsession | Track, start (UTC) | Recording | New session | New analysis | State |
|---|---|---|---|---|---|---|
| e277da8d3247374ffea16882 | 87526384 | New Hampshire, 2026-07-28 01:09 | none on the sim | unresolved | | |
| d442f2e73f150db1e4294f4e | 87528795 | New Hampshire, 2026-07-28 03:09 | none on the sim | unresolved | | |
| 5c0cd8cc72743ae1e9547315 | 87552515 | New Hampshire, 2026-07-29 02:09 | none on the sim | unresolved | | |
| 4bd8297a2f976b2b671a8b92 | 87623484 | New Hampshire, 2026-08-01 02:09 | c5c7c4da... | 148 | 199ff41121c0aca5aa599447 | ready |
| 530187890495892cab2b2a3f | 87624987 | Kentucky, 2026-08-01 03:24 | 13bf212d... | 109 | 8bb5c9af3a6d5f60a88165c4 | ready |
| 8b845b546c08e2dd43a61a75 | 87640693 | New Hampshire, 2026-08-01 19:09 | f60cbb8c... | 138 | 2d45c813dbb7d68912297a90 | ready |
| de5b3be9cc6800a7085747a9 | 87641773 | New Hampshire, 2026-08-01 20:09 | 582b11e2... | 1 | 31419c583d3959b352c3a86c | ready |
| ef7496ded72a0c31aa75dd19 | 87645794 | Daytona, 2026-08-01 23:39 | 60d1ecc2... | 120 | 1a8f654f8e11b554df72c8f5 | ready |
| 74a7d291995b36cf56793327 | 87647456 | New Hampshire, 2026-08-02 01:09 | 0c749e36... | 141 | c6839a9fa4bf5da26016a7f8 | ready |
| f34c4831a8e1c48c25aa4c24 | 87648500 | New Hampshire, 2026-08-02 02:09 | 447f6f61... | 2 | 76943d02452a052c72f22713 | ready (the tuning draft's analysis) |
| 27b9de7b36a76efb5a902963 | 87675901 | Kentucky, 2026-08-03 03:24 | 7fe60f3a... | 144 | ef8b251887969bd1826bc999 | ready |
| 3839d79f30957504b4d8090b | 87697310 | Iowa, 2026-08-04 01:09 | 60752f79... | 5 | 5b1d8fb5397fa02ed19b574f | ready |
| 6fcf0b1968fadce2c59f223a | 87746076 | Iowa, 2026-08-06 01:09 | 9825fc24... | 129 | 2dac9775f835d12725b0b0ff | ready |
| c565dc6f918dc913af30d6c0 | 87845200 | Iowa, 2026-08-10 02:09 | 7784ab27... | 151 | e1545e5b488d8fe390b27402 | ready |
| bc665b145ad3ade12ec9a096 | 87871648 | Portland, 2026-08-11 01:12 | af594e3a... | 126 | 20a50f7455371a311fbed982 | ready |
| f493219a5f9e7306d366bb74 | 87970797 | Richmond, 2026-08-15 02:24 | ec9343d3... | 136 | 182b87d51f9742d96c03c1b6 | ready |
| d339b395e15a0e6d65dea16f | 87987759 | Richmond, 2026-08-15 19:24 | 10ec1abd... | 143 | 39bd4d00a485bc49fecbdc07 | ready |
| b213bb85dfc619ce71644a4b | 88012152 | Richmond, 2026-08-16 19:24 | 39a20608... | 142 | b5f97234849b973f443c309f | ready |
| a3a0f3e2aa0b17c2dbf023c4 | 88090962 | Richmond, 2026-08-20 02:20 | 51a59134... | 107 | b02ead8d8db71b28effcf32a | ready |
| bd86f9992989bfa680188fc7 | 88091655 | Bristol, 2026-08-20 03:30 | 07e33833... | 105 | e6e384da76d876f74f3c4072 | deferred (two-recording analysis); current one has no view |
| a99423c436a82326c99cec5d | 88324519 | Daytona, 2026-08-29 17:10 | e2b8db33... | 111 | 16c7defef2a95ea806e348ea | ready |
| b4e55e78f94fba8dc2b31a87 | 88329225 | Daytona, 2026-08-29 21:09 | 3fed364c... | 146 | 2daacb35078cfb06f0756f36 | ready |
| ae7b1748175fa9e9f5c6ae52 | 88628993 | Gateway, 2026-09-14 02:08 | 56ff972c... | 150 | 58374b9d16a8475e7946d8f4 | ready |
| 50ca0fe0a38e277b027f1dc8 | 88665917 | Bristol, 2026-09-16 01:09 | 8dac6f93... | 124 | f7b389c6e724d9849ba11c12 | ready |
| 44779021646de5abd64db74b | 88667162 | Bristol, 2026-09-16 02:09 | 9ec887f8... | 147 | 2b2b4370c7e4f038c398ecab | ready |
| 778f9b0dd6cbf5b8f4475147 | 88668549 | Auto Club, 2026-09-16 03:49 | 940d9fbe... | 106 | 831189f0c7f326ba0d0df2f9 | ready |
| ff592627cfeae3ae65c5aa25 | 88669463 | Oulton Park, 2026-09-16 04:28 | b274ea4e... | 118 | 314c1488037506237d882c4b | ready |
| d881c44681fc02d6fcdf0ece | 88692982 | Bristol, 2026-09-17 03:42 | 69c2da4f... | 113 | a9ebe0e26836d58bec6ad35e | ready |
| 4eda7d6fb16aa19e827c3054 | 88784656 | Bristol, 2026-09-20 20:09 | 01bc1d3c... | 149 | 3900dd7ab5b4b64b7f05325f | ready |
| b0353095b21b1f1f07f91a7c | 88790968 | Bristol, 2026-09-21 01:09 | 461193d9... | 145 | a6147e721dec93c6f0ac44a0 | ready |
| fa1f0447ec3b62a1e4904218 | 88792080 | Bristol, 2026-09-21 02:47 | 6870c6e3... | 123 | a7691ef5f010976aff791734 | ready (two-recording analysis deferred) |

User-authored objects and where they went:

| Object | Count | Attached to |
|---|---|---|
| Tuning draft `9bbc6e8f...600f9` (New Hampshire, subsession 87648500, goal `long-run-pace`, empty note) | 1 | resolved: legacy analysis `f34c4831...` -> session 2, analysis `76943d02452a052c72f22713` |
| Notes, saved Tests, plans, turn annotations, Ask threads, tuning experiments | 0 | none exist on the sim (`migration-source-inventory.md` section 4) |
| Live layouts | 4 | the driver's live layouts |
| Prefs (`settings.json`) | 1 | the driver's prefs; the whole file without credential-like keys also as a legacy item |
| `data\tuning\inputs\iowa-2026s3.json` (hand-written track traits) and the 5 tuning packages | 6 files | the driver's legacy archive, byte for byte (with `history.sqlite3` and the catalog) |
| Activity history (43), strategy history (5) | 48 | legacy items (kinds `activity`, `other`) |

## Not converted or not clean, with reasons

1. **3 legacy analyses unresolved**: the New Hampshire races of 2026-07-27 20:09, 22:09 and 2026-07-28 21:09 local (subsessions 87526384, 87528795, 87552515). No recording of them exists on the sim (neither folder); `migration-source-inventory.md` 7.1 asks whether the main PC has them. They stay in the imported `history.sqlite3` only.
2. **1 live-folder recording not ingested**: `stockcars2 supra2019_vegas oval 2026-09-28 19-49-27.ibt` (74,231,808 bytes, last write 2026-09-29T01:08:43Z). Its header has `recordCount 0` (iRacing never finalized it, presumably a crash or kill during the 2026-09-28 bot session), so the collector's finalization gate correctly refuses it, and it stays in the collector's queue for good (`uploads_pending 1, bytes_pending 74231808` at the end of the run, re-checked every few seconds while off track). Defect note for W3: an entry that cannot become final should leave the pending count (for example after 24 h unchanged) instead of showing a permanent backlog.
3. **82 sessions show no analysis view** (`analysis_state none`): all `Offline Testing` (IRP 50, Las Vegas 17, Kansas 15), recordings 1.2 to 41 MB (median 2.4 MB). Their analysis succeeded but projection failed: 84 `project` jobs `projection_failed: missing required field track_profile`. Reproduction: session 31 (`stockcars2 supra2019_vegas oval 2026-09-28 19-46-33.ibt`, 1,228,184 bytes, SHA-256 `16ab4eb6a8ec...61898`), analysis `edf81b22b2db65ce27a3a826`, `GET /api/v1/sessions/31` shows `projection_state failed`; `GET /api/v1/jobs?state=failed` lists the jobs. `crates/coach-views` treats `track_profile` as required (`lib.rs:222`) but the worker omits it for short test recordings. Owner: W5 (or W6 if the worker should always emit it). Also session 105's current one-recording analysis is one of these.
4. **4 analyses deferred** by the memory ceiling (step 4 table), including two legacy races (Bristol 2026-08-19 and 2026-09-20).
5. **Setup library pollution from an earlier test**: the `e2e baseline.sto` and `e2e garage capture.sto` files and four garage setups uploaded by the dev-PC replay test (`e2e-mini-pc.md`) are in the owner's library. Not deleted (no data deletion on the mini PC); listed for the owner.
6. **Not imported by design**: old reports (`data\reports`, D12), `data\test-artifacts`, `research\`, caches.

## Left as found

On the sim: the archive script, its log and the inventory files were deleted with `C:\Users\joshu\coach-rig\`; nothing under `Documents\iRacing` or `Documents\iRacing Coach` was written. On the dev PC: bundle, logs and `report.json` under `C:\Users\joshu\source\coach-migration\w9\`. On the server: nothing deleted.
