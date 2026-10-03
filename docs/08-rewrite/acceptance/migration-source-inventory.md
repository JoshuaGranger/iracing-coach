# D12 migration source inventory (sim PC copy, step 1)

Run of 2026-10-03, about 08:47 to 09:10 CDT (13:47 to 14:10Z). Companion to `migration-inventory.md`; it replaces that file's UNKNOWN rows for saved Tests, plans and notes. It supersedes the blocked run of 2026-10-02 (agent down, nothing copied); the code-derived map in section 4 is kept from that run and annotated with what the copy showed.

## 1. Result

**Step 1 done.** The simulator was free: `remote_job processes` on DESKTOP-SIM showed `iRacingService64` (service, session 0) but no `iRacingSim64DX11`. No lease was taken. Everything below was read through `remote_job` (read-only PowerShell 5.1, `-NoProfile`, `PSModulePath` reset) and one `remote_copy`.

Copy location (outside the repository): `C:\Users\joshu\source\coach-migration\sim-2026-10-03\`

| File | Content |
|---|---|
| `copy\` | 117 files, 10,291,016 bytes, same relative layout as `Documents\iRacing Coach` |
| `manifest.json` | sim path, relative path, size, mtime (UTC), SHA-256 of every copied file (117); all 117 re-hashed on the dev PC after transfer: 0 mismatches |
| `raw-inventory.csv` | 662 `.ibt` rows: name, size, mtime, folder hash |
| `setups-inventory.csv` | 193 car folders: `.sto`, `.htm`, other counts, bytes |
| `raw-archive-sources.txt` | 35 lines `folder-sha256|original file name` read from `data\telemetry-traces\raw\*\manifest.json` `source_discoveries[].source_path` |
| `sim-copy.zip` | the transport archive (989,059 bytes, SHA-256 `c15ca1a93079ad76947116658c58d4f2d8c9459153b472825a53a9210e79ec8a`), kept for provenance |
| `work\history-work.sqlite3` | second copy of `history.sqlite3`; all queries ran on this, never on `copy\` |

Method: one PowerShell job on the sim read the source files (shared read, no writes beside the archive), SHA-256 hashed each, and wrote a zip to the sim's `%TEMP%` (`coach-copy-20261003.zip`); one `remote_copy` (copyId `c0a10000-0000-4000-8000-000000000006`, verified, staging closed) brought it over; a job then deleted the temp zip. That temporary file on the sim is the only write the run made there. It avoided the 8-transfer staging cap that made per-file `remote_copy` unusable.

`data\auth` and `%LOCALAPPDATA%\iRacingCoach\credentials` were never read. `settings.json` has no credential-like key names (searched for token, password, secret, apikey, credential: 0 hits); `settings.json.before-schema-5.backup.json` was not copied.

## 2. What was copied (sim counts, bytes)

| Source (under `Documents\iRacing Coach`) | Files | Bytes |
|---|---|---|
| `settings.json` | 1 | 15,055 |
| `portable-settings\` | 1 | 2,410 |
| `data\history.sqlite3` (no `-wal`, no `-shm`; written 2026-09-20 22:54) | 1 | 708,608 |
| `data\tuning\` | 7 | 1,326,914 |
| `data\activity-history\` | 43 | 8,199 |
| `data\test-artifacts\` | 29 | 7,381,420 |
| `data\strategy-history\` | 5 | 313,869 |
| `data\telemetry-events\` | 10 | 130,382 |
| `data\telemetry-profiles\` | 4 | 31,223 |
| `data\archive\` | 1 | 7,345 |
| `research\` | 13 | 356,422 |
| `portable-state.json`, `archive-manifest.json` | 2 | 9,169 |
| **Total** | **117** | **10,291,016** (about 9.8 MiB) |

Present but empty on the sim (nothing to copy): `data\trials`, `data\event-plans`, `data\ai-coaching`, `data\race-index`, `data\setup-history`, `data\target-laps`, `data\driver-models`, `data\garage61`, `data\tuning-experiments`, root `backups`, `setups`, `user-library`. Not copied by rule (size or derived): `data\reports` 909 files 6,845,767,757 B; `data\analysis-cache` 63 files 1,252,861,712 B; `data\ui-analysis-cache` 9 files 129,353,545 B; `data\telemetry-traces` 2,406 files 4,584,040,195 B (its `raw\` holds 35 `.ibt` + 35 `.json`; `live-replay\` 2,336 files); `data\season-cache` 18 files 3,582,189 B (not user-authored, left on the sim); `data\tire-models` 15 files; `data\track-geometry` 10 files; root `exports` 84 files 586,862,468 B; `track-map-*` asset folders; `iracing-coach`, `companion-app-handoff`, `.validation-deps`, `logs`. Note that `data\trials` and `data\event-plans` exist as empty folders, so D12's "saved Tests and plans" are, on this sim, an empty set.

## 3. Raw and setup inventories (counts)

- `Documents\iRacing\telemetry`: 628 `.ibt`, 16,154,878,825 bytes (15.05 GiB), file names dated 2026-09-25 to 2026-10-02 only, 628 distinct names; 624 `stockcars2` (Supra) and 4 `stockcars`. The spike counted 617 files; 11 were added since (the P3 and P5 recordings and later).
- `data\telemetry-traces\raw\<sha256>\`: 35 `.ibt`, 4,268,967,692 bytes, mtimes 2026-08-08 to 2026-10-02. These are the archived copies of recordings dated 2026-07-31 to 2026-09-29 (names in `raw-archive-sources.txt`); they are the only copy of those recordings on the sim, because the live telemetry folder holds nothing dated before 2026-09-25 (why is not established here).
- Setups: 193 car folders; 1,204 `.sto`, 16 `.htm` (all in `stockcars2 supra2019`), 9 other (the `-Current-` files); 148 of the 193 folders contain no `.sto`. Names only, nothing copied.

## 4. Where each legacy user-authored object lives (verified against the copy)

Paths are under `Documents\iRacing Coach`. "Bundle slot" is the `ImportBundle` field in `contracts/api.openapi.yaml` (kind values from `db-schema.sql` `legacy_items`). Source file and line references are from the code reading of 2026-10-02.

| Object | Location and format | Verified content on the sim | References to other objects | Bundle slot |
|---|---|---|---|---|
| Settings | `settings.json`, camelCase, `settingsSchemaVersion` 5 (`SettingsStore.cs:19,144-173`) | 15,055 bytes. Keys: `coachHome`, `iRacingRoot`, `iRacingInstallRoot`, `firstRunComplete`=true, `settingsSchemaVersion`=5, `coachThreadIds` (0 entries), `launchAtSignIn`=false, `useReducedMotion`=false, `themeColor`, `customThemeColor`, `diagnosticIncludeConfounded`=false, `liveMonitor` (9 keys), `raceAnalysisTraces` (1 key, `rows`), `raceAnalysisTraceLayouts` (`activeLayoutId`=factory-default, `userLayouts` 0, `legacyLayoutImportCompleted`=true). No `lastInvestigation`. | none (no thread ids, no bookmark) | `trace_layouts` (nothing user-made), `live_layouts`, `prefs` |
| Tuning drafts and notes (C#) | `portable-settings\tuning-drafts\<64-hex>.json`, schema 2 (`ProgressiveTuningCoordinator.cs:427-489`) | **1 draft** (`9bbc6e8f...600f9`), `updatedUtc` 2026-08-11T02:06:39Z, subsession 87648500 (New Hampshire, Supra), goal `long-run-pace`, ruleset `nascar-oreilly-xfinity-2026s3-v1`, `generalNote` empty, `feedback` 0, `turnCorrections` 0, `representativeRunIds` ["2"]. Contains no free-text note. | `analysisId` f34c4831... resolves to 1 row in `sessions`; run 2 exists in `runs` (2 rows for that analysis); `sourceSha256` 447f6f61... equals the folder name of `data\telemetry-traces\raw\447f6f61...` whose original file is `stockcars2 supra2019_newhampshire oval 2026-08-01 21-09-00.ibt` | `tuning_drafts` (+ `notes`: none to import) |
| Turn annotations | `portable-settings\tuning-turn-annotations\` | **Absent** (the single `portable-settings` file is the draft above) | n/a | `turn_annotations`: empty set |
| Tuning drafts (Python) | `data\tuning\drafts\` | **Absent** (`data\tuning` holds `catalog`, `inputs`, `packages` only) | n/a | empty set |
| Tuning experiments | table `tuning_experiments`; folders `data\tuning\experiments`, `data\tuning-experiments` | table **0 rows**; `data\tuning-experiments` empty; no `experiments` folder | n/a | empty set |
| Saved Tests | `data\trials\` | folder empty | n/a | empty set |
| Plans | `data\event-plans\` | folder empty | n/a | empty set |
| Ask threads | `data\ai-coaching\` and `coachThreadIds` | folder empty; `coachThreadIds` has 0 entries | n/a | empty set |
| Activity history | `data\activity-history\*.json` | 43 files, 2026-08-03 to 2026-09-21; actions: `ui-analysis-cache` 36, `strategy-history` 7. Audit only. | `artifactId` stems | `legacy_items` kind `activity` (optional) |
| Strategy history | `data\strategy-history\strategy-subsession-<id>-<n>.json` | 5 files (subsessions 87648500, 87675901, 87871648, 87987759, 88012152, all session 2), created 2026-08-03 to 2026-08-18, 14/3/1/3/3 result rows. Derived rows, not authored. Of their analysis ids only 1 (in the 87648500 file) is still in `sessions`. | `analysis_id`, `source_path` | not imported (derived) |
| Tuning inputs (hand-authored) | `data\tuning\inputs\iowa-2026s3.json` | 1 file, keys `layout`, `corner_duty`, `banking` (all strings) | file name `<track>-<season>` | `notes` or `legacy_items` kind `other` |
| Tuning packages and catalog | `data\tuning\packages\2026s3\<car>\<track>\setup-<hash20>.json` (5), `catalog\index.json` | 5 packages: supra2019 iowa, supra2019 portland-full, streetstock3 richmond, toyota-tundra-trd-pro richmond, toyotacamry richmond; catalog is a scan index (keys incl. `entries`, `source_file_count`). Re-derivable from the purchased setups. | `tuning_packages` rows (same 5) | re-derived |
| Test artifacts | `data\test-artifacts\<name>\` | 6 folders (`corner-tire-age`, `handoff-analyze`, `handoff-dashboard`, `installed-plugin-pycache-20260801`, `run-number-regression`, `workflow-smoke`), 29 files, 5 own `history.sqlite3` (1 session each, `handoff-dashboard` 0), dated 2026-08-01 to 08-02. Test output, not user-authored. | none | not imported |
| Located recordings | `data\race-index\source-locations` | empty | n/a | none |
| Research | `research\` | 13 files: `sto-writer` (schema, research notes) and `stream-deck-xl` (audit and spec documents). Project documentation, not racing notes. | none | not imported |
| Bookkeeping | `portable-state.json`, `archive-manifest.json` | `safeToCopy` false at 2026-10-02T14:37Z ("Coach folder is active"), `credentialsIncluded` false, `connectionsRequiredOnNewPc` ChatGPT and Garage61 | none | not imported |

### 4.1 `history.sqlite3` (the copy's work file; counts verified)

- Integrity `ok`, journal mode wal, `user_version` 0. Tables and rows: `sessions` 31, `runs` 75, `knowledge_bundles` 2 (2026-08-02T00:41:24 to 00:41:26Z), `tuning_packages` 5, `tuning_experiments` 0.
- `sessions`: all 31 are `Race` (`sim_session_type`), 31 distinct subsessions, 5 distinct car keys, 10 distinct track keys, `analyzed_at` 2026-08-02T00:34:29Z to 2026-09-21T03:54:20Z, `session_start` 2026-07-28T01:09:55Z (the 2026-07-27 20:09 local New Hampshire race) to 2026-09-21T02:47:26Z; `setup_type` fixed 21, open 10; no empty `source_path`. `runs` are numbered 1 to 5 and every run belongs to a session.
- Primary key `analysis_id` (24 hex characters); see section 4.2 for the recording link.
- The database was last written 2026-09-20 22:54 and does not cover later sessions; `data\reports` holds 909 files (the spike's 156 report files was a different count; not re-examined here).

### 4.2 Reference chain to recordings (checked against the inventory)

1. Every `sessions.source_path` has the form `C:\Users\joshu\Documents\iRacing\telemetry\<name>.ibt` (31 of 31, all in that one folder).
2. **0 of 31** of those names exist in the sim's current `Documents\iRacing\telemetry` folder (it holds only 2026-09-25 and later files).
3. **28 of 31** names match the original name of an archived recording in `data\telemetry-traces\raw\<sha256>\` (`raw-archive-sources.txt`), giving the recording hash. **3 of 31** (the New Hampshire races of 2026-07-27 20-09-55, 2026-07-27 22-09-39 and 2026-07-28 21-09-03) have no recording anywhere on the sim; they can be migrated as analysis-only history rows or from the main PC's files, not from the sim.
4. The one draft's `sourceSha256` is among the 35 archive folder names (resolved).

This replaces the 2026-10-02 plan, which assumed the sim's telemetry folder would hold the unarchived recordings: it does not.

## 5. Format check on the dev PC's own Coach root (proxy, from the 2026-10-02 run)

Unchanged from the earlier run and not repeated by this one (the dev PC's Coach root: settings schema 5, 3 tuning drafts, `history.sqlite3` with 15 sessions, 41 runs, 0 experiments). It confirms the code reading; it says nothing about the sim.

## 6. Gaps in the wave-1 tools (still open; not edited here)

- `tools/rig/Export-LegacyCoachData.ps1` (`$Items`) and `tools/migrate/inventory_legacy.py` (`USER_AUTHORED`) omit `data\trials`, `data\event-plans`, `data\ai-coaching`, `data\race-index\source-locations` and `research\`. They are empty on this sim, but the lists should still name them so another machine's copy is complete.
- The export script stages a copy on the sim before hashing, which writes to the sim; this run used one temp zip (deleted) for the same purpose.
- `migration-inventory.md` says notes outside drafts do not exist and saved Tests have no known location. Verified result: on the sim there are no saved Tests, no plans, no turn annotations, no Ask threads, no experiments and no free-text notes at all; the only authored items are 1 empty-note tuning draft, 1 hand-written track-traits file (`iowa-2026s3.json`) and the settings. The `live-replay` folder (2,336 files under `telemetry-traces`) is not described by any earlier document and was not opened.

## 7. UNKNOWN (remaining)

1. Whether the 3 unarchived July sessions' recordings exist on the main PC or elsewhere.
2. Contents of `data\reports` against `sessions` (whether any analysis a store names exists only as a report, not in the database).
3. Why the live telemetry folder holds nothing before 2026-09-25 (only the archive keeps the older recordings).
4. Whether the unchanged `data\tuning\inputs\iowa-2026s3.json` is the only hand-authored input: yes for the sim as copied; other PCs unknown.
5. Recording SHA-256 of the 628 live-folder files (never computed; names, sizes and mtimes only).

## 8. Step 2 and 3 (next)

1. Run `tools/migrate/inventory_legacy.py` on `coach-migration\sim-2026-10-03\copy` (after adding the folders in section 6), output outside the repository; the database is already duplicated at `work\history-work.sqlite3`.
2. Import bundle: 1 draft, 31 sessions and 75 runs as history, 5 tuning packages (re-derive), settings prefs, `iowa-2026s3.json` as a note; link the 28 resolvable sessions to archive recordings by hash and flag the 3 July sessions as having no recording.
3. Re-ingest the 628 live recordings and 35 archived recordings by hash under the new system's own SHA-256; the live folder's files are the 2026-09-25 to 2026-10-02 set.
