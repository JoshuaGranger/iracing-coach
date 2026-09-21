# iRacing Coach 0.17.2 development: installed-app QA follow-up

Date: September 20–21, 2026. This follows the supplied DESKTOP-SIM report for installed version 0.17.0. It is local development verification, not completion of the requested every-control simulator test.

## Evidence received

The remote report confirms access to all seven primary pages. Race replay Play/Pause/Back, technical data, setup search and recorded values, tuning context controls, installation health, telemetry customization, and the native popout were exercised. Main-window screenshots remained stale and accessibility later returned a malformed repeating tree. Those tooling failures prevent a complete visual or every-control result; the desktop was already unlocked.

The report also confirms a Bristol planning view error caused by bare `Infinity` in saved JSON, 1,197 race-catalog and 1,197 setup-catalog parse errors, a misleading planning readiness label, internal display names, possible expanded-popout clipping, and missing local Bristol setups in search. The remote machine still has 0.17.0; these follow-up changes have not been installed there.

## Changes by app section

### Race analysis, Home, and archive catalogs

The 0.17.1 changes already correct non-finite strategy serialization and rebuild invalid legacy analysis caches. This follow-up also remembers invalid report revisions in the shared catalog reader. An unchanged invalid file produces one parse/log failure per application instance, rather than one failure per catalog per refresh. Changing the file length or modification time allows another attempt. Transient file-access failures are not cached.

Discovery now forwards the recorded car display name, complementing 0.17.1's track display-name fix. This avoids manufacturing primary labels from internal car folder names when the recording provides a display name.

### Race planning

Malformed JSON from reference analysis or plan generation is contained within the planning page, with a retry instruction. Optional history failures no longer prevent the core plan. Starting another build clears the prior result. The reference summary now explains missing inputs or invalid distance and shows the building state instead of always claiming readiness.

### Setup library

The application previously requested only 500 local catalog entries. It now requests the backend's supported 1,000 entries and displays an incomplete-library warning when the backend reports either an entry or scan limit. Search still covers only loaded entries; this is not unlimited discovery. The backend's 2,000-file scan bound remains.

The remote count of 527 is consistent with 500 local entries plus archived setups, but the exact missing Bristol files have not been located in the remote catalog ordering. Their recovery remains a DESKTOP-SIM retest item, not a proven result of the limit change.

### Native telemetry popout

Screen-edge positioning now runs after WPF has applied the new size. Previously it read the old native rectangle immediately after changing the scale, allowing the subsequent larger window to extend past a display edge. This is a code correction consistent with the report; the original multi-monitor clipping observation still needs native visual verification.

## Verification

| Check | Result and scope |
|---|---|
| Application tests | 380 passed, none failed or skipped |
| Backend/repository verification | 1,017 Python tests passed; contracts and exports verified |
| Native development publish | Release win-x64 publish succeeded without warnings or errors |
| Invalid report regression | 100 repeat reads succeed without reopening an exclusively locked invalid source; valid replacement loads; a subsequent invalid revision is reported once again |
| Planning regressions | Invalid reference/plan JSON stays inside the page; retry succeeds; invalid optional history still permits a plan |
| Setup regression | A synthetic 600-entry catalog exposes 100 Bristol matches after entry 500; reported scan truncation produces the warning |
| Direct browser check with real recordings | Home shows canonical Iowa/New Hampshire and Toyota Supra names; distance zero disables Build and explains the missing valid distance; New Hampshire open-setup evidence produces a 160-lap plan with two stops; setup records load; no browser warnings/errors observed |

Browser testing used the same isolated profile and read-only source copies as 0.17.1. The 160-lap plan is a test scenario using the real 80-lap New Hampshire reference. It is not a claim that a 160-lap race was recorded. No credentials, raw telemetry, purchased setups, or original source files were changed.

Evidence logs are under `companion-app/artifacts/remote-followup-*.log`. The new development executable is under `companion-app/artifacts/dev/v0.17.2-dev-20260920-230912/`. It uses local runtime/backend junctions and is suitable for this development PC only. No installer or portable archive was generated, and the earlier 0.17.1 packages remain unchanged.

## Remaining work

- Retest the latest Bristol analysis and 160-lap plan on DESKTOP-SIM with patched code; measure its cold and warm paths again. The reported 28.211/2.787-second timings belong to 0.17.0.
- Confirm the local Bristol setup files appear and check whether the larger library still reaches a backend bound.
- Repeat expanded-popout positioning at the original monitor edge and DPI; complete native visual and keyboard coverage once screenshots/accessibility are reliable.
- Resolve the existing local WebView2 composition launch failure (`0x8876086A`). A successful native build and browser test do not prove native rendering on DESKTOP-JOSHUA.
- Complete connected simulator cadence, service integration, matching-baseline setup export, and remaining every-control scenarios listed in the remote report and prior snapshot.

First-analysis latency remains a performance target. Previous local 0.17.1 measurements were about 20–24 seconds cold and 1.3 seconds warm; this follow-up makes no new decoder-speed claim.
