# iRacing Coach 0.17.3 development: simulator data and catalog pagination

Date: September 21, 2026. This continues the DESKTOP-SIM QA request. Backend verification and native visual acceptance are separate results.

## Verified on DESKTOP-SIM

The existing private NAS share provided a working transfer route without network, firewall, or credential changes. The 0.17.2 development transport and all 202 manifested files were hash-verified before testing with the installed Python runtime. Installed iRacing Coach remained 0.17.0.

The exact September 20 15:09 Bristol Supra recording was analyzed at native rate into a fresh disposable archive. Both the response and all six generated JSON files passed strict validation. The original recording's SHA-256 remained `01bc1d3c5d46fd73938c38e94e1e5feaac79025af7a1c7d70dbf9a9f4edafa89`.

| Measurement | Installed 0.17.0 baseline | Patched 0.17.2 backend |
|---|---:|---:|
| Cold analysis | 28.211 s | 18.846 s |
| Cached analysis | 2.787 s | 1.738 s |
| Strict JSON | Failed: bare Infinity | Passed |

Cold means an empty application cache, not a cleared OS disk cache. The warm run followed in the same Python process. Workflow timings exclude imports and response serialization. These separate runs show observed improvement, not a controlled performance comparison. First analysis still exceeds the desired few seconds.

The real setup catalog confirmed the original missing-file cause: requesting 500 entries returned no Bristol matches; 1,000 entries returned 16, including the Supra qualifying/race setups at positions 912/913. A second defect remained: filtering took place after an internal 1,000-group cutoff. This hid 79 of 1,079 groups, including all 31 Tundra setups.

## Setup library changes

The backend now indexes within the existing 2,000-entry safety bound before filtering. The catalog API adds optional `offset` and returns `next_offset` and a stable `catalog_revision`. Each response stays bounded to at most 1,000 entries. Baseline selection uses the same larger bounded index.

The app loads all catalog pages before replacing its inventory. It rejects a changed revision, non-advancing or invalid pagination, excessive pages, and oversized results. If a later page fails, the last complete library remains visible. A finished paginated catalog is not labeled truncated; underlying file or entry truncation still produces the warning. The existing 2,000-file scan limit remains, so this is not an unlimited catalog claim.

Contract exports were regenerated. Analysis math, schema version 2, and analysis profile v17 are unchanged; valid race-analysis caches remain compatible.

## Local verification

- 383 application tests passed, with no failures or skips.
- 1,019 backend tests passed; repository and contract/export verification passed.
- A synthetic 1,079-group regression verifies two pages, 31 late Tundra results, complete filtering, stable revisions, and source-change detection.
- Application regressions verify all pages load and that changed revisions, invalid offsets, or a failed second page cannot replace the previous complete inventory.
- A .NET self-contained Release win-x64 development publish succeeded without warnings or errors.

## Development artifact

Application source commit: `f48e4c91c846417bc5ea5293be7b58fcb19742dc`.

The development transport is 87,244,390 bytes and includes 668 manifested files. SHA-256: `ac53b9ad3507ae4ec28d7131ff25292de803cae023d2d58ea749ec9995a9c720`.

The .NET runtime is included because DESKTOP-SIM's shared runtime was only version 8.0.28. Python and Coach Engine still use the already installed runtimes through junctions in a disposable directory. This is an isolated development test copy, not an installer or a complete portable release. The installed app and earlier release packages are unchanged.

## Native testing on DESKTOP-SIM

The initial resumed native attempt still reported Windows `GetCursorPos` access denied. Earlier captures were stale despite accessibility navigation. Neither failure proves an application layout defect. No new native visual pass was claimed from those attempts.

Native input and capture subsequently recovered in Windows session 3. A short disposable profile, copied real data, and seeded first-run completion allowed normal UI testing. The isolated host deliberately disables Coach Engine and external credentials, so this does not validate onboarding or external connections.

| Section | Observed result |
|---|---|
| Home | September 20 15:09 Bristol discovered automatically; analysis reached Ready with 128 green laps, 31 caution laps, one stop, 15.264 s fastest clean lap, and 18.45 gal used. Labels show Bristol Motor Speedway / Single Pit Road / Toyota Supra Class B. |
| Setup library | Real backend pages returned 500 + 500 + 79 entries with a stable revision and no scan truncation. Native library displayed 1,079 local entries; Bristol search found 20 local setups plus the recorded setup. Tundra filtering returned all 31 entries. All 1,104 copied source files matched their originals. |
| Race Analysis | Bristol map and aligned traces rendered. Clear produced an explicit empty state; Best three restored three laps. Temperature signal search, speed map, zoom, and Fit track responded. |
| Technical Data | Four summary cards rendered without persistent overlap at 1426 × 893. One stop, 14.28 s service, 139.4-lap fuel range and one all-green stop matched backend evidence. |
| Planning | A 160-lap plan produced one stop, two stints and a lap-80 target. A 100-lap plan produced zero scheduled stops and 39.4 laps of fuel margin. Distances 0 and 1001 disabled Build with validation. No Infinity crash recurred. |
| Live popout | Waiting state without simulator; standard 558 × 434 and expanded 125% 697 × 542 settled with all three columns and controls visible. Standard size and Return to full app worked. |
| Progressive Tuning | Late phase and load-zone controls responded; analysis remained disabled pending turn verification. No fabricated feedback or setup changes were submitted. |
| Settings | Mint-to-Blue preview worked and Mint was restored. This initial pass did not validate save/restart persistence. |

The short-profile error log contained only the expected isolated Garage61-disabled error. Earlier long-profile runs failed while archiving to a 269-character path; this was later reproduced locally and addressed in 0.17.4. Transitional screenshots sometimes showed the previous state; settled captures agreed with accessibility.

Save a copy failed because the UI called `copy_iracing_setup_to_coach`, which did not exist in the backend registry or implementation. This was confirmed as a product defect, not a file-picker or isolation limitation. The 0.17.4 follow-up supplies the operation.

The first Bristol IBT lacked full-field replay channels and correctly reported unavailable playback. A different finalized 946-frame replay archive and matching recording were subsequently found for a later test. This snapshot does not claim their playback passed.

The profiler identified repeated finite-value normalization as a cold-analysis cost. Its 79.521 s instrumented run is diagnostic, not a user-facing benchmark. Compact source reports are retained under `companion-app/artifacts/remote-qa-20260921`; detailed private screenshots and telemetry stay on DESKTOP-SIM.

Live simulator cadence, external services, full every-control and resolution coverage, verified geometry, and completed tuning feedback remain outside this pass. Some setup-only car and track labels still expose raw identifiers because trusted display metadata was unavailable.
