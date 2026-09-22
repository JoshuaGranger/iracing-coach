# iRacing Coach 0.17.5: simulator-PC stress-test findings and fixes

Date: September 21, 2026. Source: `703b0450e065238521155fbbf70b3f0a6e729cf8`. This report records the resumed real-data test and its final development candidate. The installed production executable on DESKTOP-SIM remains 0.17.0, with its original hash unchanged.

## Outcome

Real simulator data exposed and confirmed setup-catalog truncation, a missing setup-copy operation, Windows archive-path failures, and setup selection collisions. These are fixed in the 0.17.3–0.17.5 development builds. The earlier planning/JSON fixes also passed the Bristol retest. All seven primary pages were reached across the installed and development test passes, but every-control testing is incomplete: Windows native input failed again after useful interaction testing.

The final source passes 387 application tests and 1,029 backend tests. The 0.17.5 transport was independently hash-verified on DESKTOP-SIM, but was not launched there after the native-input failure. Its selection fix was exercised locally in the browser with disposable setup files.

## Setup library

### Complete inventory and reliable selection

Pagination now loads all 1,079 local setup groups on DESKTOP-SIM, including all 31 Tundra entries beyond the former internal cutoff. The app validates pagination and retains the last complete inventory on failure. The existing 2,000-file/entry bounds remain. Search found all 20 local Bristol setups; additional recorded setup entries account for higher native totals. See the [0.17.3 evidence](implementation-snapshot-0.17.3-development.md).

Three real qualifying setup files in different Chevy, Ford and Toyota car folders have identical bytes. The former UI used their content hash as row identity, so selecting Ford could keep Chevy details and highlight multiple rows. Version 0.17.5 identifies each local file by normalized full path and retains its hash separately for evidence. Selection indicators now expose explicit true/false accessibility values.

A local browser test used 1,079 disposable catalog entries plus three identical-content files. Ford and Toyota selected independently; exactly one row was selected. Saving Ford retained the Ford source in the manifest. Clear filters restored all 1,082 entries. A regression verifies distinct identities and stable selection across refresh. Remote read-only inspection confirmed the same-content trigger exists in the real library. Native 0.17.5 selection still awaits a retest.

### Save a copy

The previously absent backend operation now makes a verified independent copy into the Coach archive, preserves provenance, deduplicates identical content, and refuses to replace changed destinations. It only accepts a nonempty `.sto` under the configured iRacing setup directory, with a 16 MiB limit checked again after fingerprinting. Original setup bytes are never changed. The operation remains unavailable to autonomous Coach workflows.

Native 0.17.4 testing confirmed successful copy and repeated-click deduplication with original/source/destination hash equality. Version 0.17.5 also separates copy status from unrelated baseline/default messages. Full implementation and race/conflict safeguards are recorded in the [0.17.4 snapshot](implementation-snapshot-0.17.4-development.md).

## Race analysis, archives and planning

The September 20 15:09 Bristol Supra recording now populates Home with 128 green laps, 31 caution laps, one stop, 15.264 seconds fastest clean lap, and 18.45 gallons used. Track labels show Bristol Motor Speedway / Single Pit Road. Technical Data shows one stop, 14.28 seconds service and a 139.4-lap fuel range. All response and generated report JSON passed strict validation.

Planning generated a one-stop, two-stint plan for 160 laps and a zero-stop plan with 39.4 laps of fuel margin for 100 laps. Invalid 0/1001-lap inputs disabled Build. The earlier Infinity-related planning crash did not recur.

New raw archive copies use the short name `telemetry.ibt` inside their full-hash directory, retaining the simulator filename in provenance. Existing archived names are reused unchanged. This fixed the reproduced Windows filename-expansion failure and passed the longer-profile real-data check. It does not establish arbitrary-length Windows path support.

Repeated optional-trace normalization was removed without changing native-rate analysis or evidence rules. Exact-output local comparisons showed analysis-only reductions of 8.5% for Iowa and 5.7% for New Hampshire. However, the controlled DESKTOP-SIM Bristol comparison was effectively flat:

| Build | Fresh application cache | Cached analysis |
|---|---:|---:|
| 0.17.3 | 18.671 s | 1.709 s |
| 0.17.4 | 18.662 s | 1.802 s |

These single runs exclude import/response-serialization costs and do not clear the OS disk cache. Cold analysis remains substantially above the requested few seconds. The latest selection fix does not change analysis. No universal speed improvement is claimed.

Known abbreviated corner observations now have readable speed, brake and lap-position labels. Missing/censored observations remain qualified. Native wrapping of this new text remains unverified.

## Interaction coverage

| Area | Completed checks | Remaining checks |
|---|---|---|
| Home and navigation | All seven primary pages reached; latest Bristol populated; readiness and canonical labels | Full viewport/resolution sweep |
| Race Analysis | Map and aligned traces; Clear; Best three; temperature search; speed legend; zoom; Fit track | Saved trace-layout edit, remaining controls |
| Technical Data / Planning | Summary cards; valid 100/160-lap plans; invalid distance handling; no repeat JSON crash | New corner-text wrapping and remaining filters |
| Setups | Full real inventory; Bristol/Tundra results; native copy/dedup; local final-build selection and copy provenance | Native 0.17.5 selection, remaining car/track/season controls; numeric comparison needs a suitable decoded setup |
| Race replay | Authentic matched archive; final response and saved report contain partial replay with 324 display frames and three gaps | Native reload, play/pause, scrub, speed, focus, zoom and timeline |
| Live telemetry | Waiting-state popout; standard/expanded 125% sizes; return to full app | Active simulator cadence and real-time behavior |
| Progressive Tuning | Phase/load-zone controls; evidence gating without invented values | Verified geometry and completed tuning feedback/A-B workflow |
| Settings | Blue Save/restart persistence; Mint restored | External account connections and remaining settings |

The isolated test host deliberately disabled external credentials and live connection. The authentic replay's raw compute cache is written before enrichment; its unavailable replay field is not a backend defect. Final workflow/report enrichment passed. A native 0.17.3 page still showed unavailable; native 0.17.4 reload could not be checked after input access failed.

Some setup-only car/track names still show raw identifiers where trusted local display metadata is unavailable. No unverified aliases were invented. Installer lifecycle was outside this development-copy pass.

## Verification and artifact

- Application tests: 387 passed, zero failed or skipped.
- Backend tests: 1,029 passed, zero failed or skipped; contract/export verification passed.
- Release win-x64 development publish: zero warnings or errors; .NET runtime included.
- Transport: `iRacingCoach-0.17.5-development-transfer.zip`, 87,252,871 bytes, 668 manifested files.
- SHA-256: `d637c6bd4371e208faf0060f868330c8abf4e02f1c9bbe6a42bebf1e642bbd51`.
- Private transfer: `\\192.168.1.82\Joshua\iRacing-Coach-QA-20260921`.
- DESKTOP-SIM extraction: `C:\Temp\ICQA0175-0921\app`; every manifest size/hash verified. This candidate was not launched there.

This is a development transport, not an installer or a complete portable release. Python and Coach Engine reuse the existing installed runtimes when running an isolated copy. Production app/settings and raw source files were preserved. Build binaries, paid setup contents, private telemetry and screenshots are excluded from Git.

Compact private evidence is retained in `companion-app/artifacts/remote-qa-20260921`, including `DESKTOP-SIM-0174-QA.md`, `DESKTOP-SIM-0175-QA.md`, the final transport-verification JSON and `selection-ui-0175.json`. Full-suite logs are `companion-app/artifacts/selection-followup-app-tests.log` and `selection-followup-repository.log`.

## Remaining work

1. Recover native input access and finish the specific controls listed above. The last bounded recovery returned Windows `GetCursorPos: Access is denied (0x80070005)` while the app remained responsive; further input attempts stopped.
2. Measure active simulator telemetry in a connected session. No active live acceptance is claimed from waiting-state or recorded-data tests.
3. Continue cold-analysis optimization against a repeatable real-data benchmark while preserving exact output, source bytes and evidence screening. Bristol still takes about 19 seconds on first analysis.
4. Resolve display names from trusted metadata and finish corner-text/viewport visual checks.

This candidate is ready for the remaining development retests, not a claim of full stress-test completion or user acceptance.
