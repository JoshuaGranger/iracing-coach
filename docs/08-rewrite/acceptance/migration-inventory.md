# D12 migration inventory (wave 1, W9)

What can be known about the legacy data without the sim PC, and the scripts that collect the rest once Joshua lifts the sim-PC pause in writing. The migration itself (`workstreams.md`, D12 steps 1 to 5) is wave 3; its report will be `migration.md`.

## Where the old app keeps user-authored data (from source)

The Coach root is `Documents\iRacing Coach` (`Models.cs:896-898`); its durable folders are listed in `DurableArchive.cs:124-147`.

| Data | Location | Writer | D12 handling |
|---|---|---|---|
| Settings, trace layouts, live-monitor layout | `settings.json` | C# `JsonSettingsStore` | layouts and trace choices only; no credentials (those are in `data/auth/`, never read) |
| Tuning drafts (goal, general note, feedback, turn corrections, representative runs, map identity) | `portable-settings/tuning-drafts/*.json` | `PortableTuningDraftStore` (`ProgressiveTuningCoordinator.cs:436`) | drafts and their notes; references by session, run and map identity |
| Turn annotations | `portable-settings/tuning-turn-annotations/` | `PortableTuningTurnAnnotationStore` (`ProgressiveTuningCoordinator.cs:500`) | tuning map annotations |
| Tuning experiments, packages, knowledge bundles, sessions, runs | `data/history.sqlite3` (`storage.py:1242-1330`) | Python worker | `tuning_experiments` through W6's import tool; the rest is re-derived |
| Setup packages, catalogs, track inputs | `data/tuning/{packages,catalog,inputs}/` | Python worker | packages re-derivable; inputs checked for hand edits |
| Activity history | `data/activity-history/*.json` (`CompanionState.cs:4514`) | C# | `legacy_items` |
| Strategy history | `data/strategy-history/` | C# | `legacy_items` (Plan is parked) |
| Saved Tests, test artifacts | `data/test-artifacts/` (D12 list) | not found in the C# or Python source by name | location still UNKNOWN; resolved from the sim copy |
| Notes outside drafts | none found by store class or folder name | | resolved from the sim copy |
| Archived raw recordings | `data/telemetry-traces/raw/*.ibt` | old app | re-ingested through the upload path (step 3) |

Re-derived, not imported: `data/reports`, `analysis-cache`, `ui-analysis-cache`, `race-index`, `season-cache`, `track-geometry`, `tire-models`, `telemetry-events`, `telemetry-profiles`, `garage61`, `ai-coaching`, `driver-models`, `archive`.

## Dev-PC trial run (2026-10-02)

The dev PC holds its own legacy Coach root (not the sim's). The export and inventory scripts were run against it as a dry run; outputs stayed in session scratch (they name private files).

| Command | Result |
|---|---|
| `tools/rig/Export-LegacyCoachData.ps1 -Destination <scratch>\legacy-copy` | 162 files copied, 0 hash mismatches; a second run refuses the non-empty destination |
| `tools/rig/Get-SimRecordingInventory.ps1 -OutFile <scratch>\recordings.json` | 49 telemetry recordings (2.59 GB), 30 archived raw (2.23 GB), 0 setup files; `-Hash` checked on a fixture tree (SHA-256 of `abc` correct) |
| `$py tools/migrate/inventory_legacy.py "Documents\iRacing Coach" --out <scratch>` | 162 user-authored files, 30 raw recordings, 2 unclassified (`archive-manifest.json`, `portable-state.json`); refuses an output path inside the repository |
| same on the exported copy | 162 user-authored files: the export covers every user-authored file the inventory finds |
| export again after adding `data\trials`, `data\event-plans`, `data\ai-coaching` and the raw-archive `manifest.json` files (wave-1 follow-up) | 177 files (162 plus 15 manifests), 0 recordings, 0 hash mismatches |
| `$py tools/migrate/build_import.py <copy> --customer-id 1 --machine DESKTOP-JOSHUA --out <scratch>` | valid `ImportBundle` (ajv over `wire.schema.json`): 3 live layouts (units `°C` to `C`, `laps` to `lap`, `time` to the tile default), 1 trace layout, 3 tuning drafts, 18 legacy analyses (17 with a recording hash), 5 `legacy_archive` files, 154 `legacy_items`, nothing dropped |
| same, wave 2 (drafts name `legacy_analysis_id`, wave-2 follow-up W9-1), on a fresh export (177 files, 0 hash mismatches) | valid `ImportBundle` (ajv); every one of the 3 drafts names a legacy analysis that `legacy_analyses` lists with a recording hash; `tuning_drafts_without_analysis` 0; other counts as above |

Dev-PC counts: `history.sqlite3` holds 15 sessions, 41 runs, 2 knowledge bundles, 2 tuning packages, 0 tuning experiments; 3 tuning-draft files; 152 activity-history entries; 1 strategy-history entry; `user-library/`, `data/tuning-experiments/`, `data/setup-history/`, `data/target-laps/` empty; `data/test-artifacts/` absent. No saved Tests, plans or free notes exist here, so their format is still unseen.

## Blocked on the sim-PC pause

| Step | Script (ready) | Run where |
|---|---|---|
| D12 step 1: copy user data | `tools/rig/Export-LegacyCoachData.ps1 -Destination <folder>` (read-only on the source; never `data\auth`; refuses while the old app runs) | sim PC, through the remote tools; then copy the folder to the dev PC |
| Legacy inventory | `$py tools/migrate/inventory_legacy.py <copied root> --out <outside the repository>` | dev PC, on the copy |
| Raw hash list for step 5 | `tools/rig/Get-SimRecordingInventory.ps1 -OutFile <file> -Hash` (below-normal priority; stops if `iRacingSim64DX11` starts) | sim PC, off track |
| Steps 2 to 5 | `tools/migrate/build_import.py` (ready, tested on synthetic data and the dev-PC copy), backfill, reanalysis, `migration.md` | wave 3 |

## Tuning drafts in the report

A legacy draft key hashes the old `analysis_id`, so no draft keeps its key (`api.openapi.yaml` `ImportBundle.tuning_drafts`). Each bundle draft names its `representativeSession.analysisId` as `legacy_analysis_id`, listed in `legacy_analyses` with its stable identity; the server re-keys the draft from the resolved current analysis or parks it as a `legacy_items` row (kind `tuning_draft`). `migration.md` lists, from the `POST /api/v1/admin/import` summary, `tuning_drafts` (stored under a new key) and `tuning_drafts_unresolved` (parked), with each draft's legacy file stem (`identity_key`), its legacy analysis and the new key or the reason it was parked. `build_import.py` reports `tuning_drafts_without_analysis` beforehand: those drafts are parked whatever the import resolves.
