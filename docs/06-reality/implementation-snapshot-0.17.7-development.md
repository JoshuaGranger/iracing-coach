# iRacing Coach 0.17.7: replay cache and continued native QA

Date: September 21, 2026. Application source: `98d023b1692f0f87118ea7efbdeb2ee932338e1a`. This follows the [0.17.6 development snapshot](implementation-snapshot-0.17.6-development.md). Resumed testing first used the unchanged 0.17.6 v3 candidate, then produced 0.17.7 to address the two new findings below.

## Environment and data

Joshua asked to resume the waiting DESKTOP-SIM task. A fresh native capture and navigation succeeded. The isolated application was launched from `C:\Temp\ICQA0176v3-0921\app` with the existing disposable profile. Tests use copies of real recordings. Installed 0.17.0, production settings and original recordings remain unchanged. Active live telemetry remains excluded at Joshua's request.

## Repeated analysis and planning: rebuilding an unchanged replay

The full Bristol recording repeatedly spent over 25 seconds showing Building, including when changing only the planned distance. Direct monotonic measurements through the supported backend tool dispatcher confirmed the problem:

| 0.17.6 operation | Execution time |
| --- | ---: |
| Analyze race, telemetry-analysis cache hit | 31.232 s |
| Live replay materialization within that analysis | 29.110 s |
| Strategy history | 0.571 s |
| Fuel plan | 0.573 s |

Replay reconstruction accounted for about 93% of the analysis call. It decoded and processed the same 343 finalized chunks on every request, despite a valid telemetry-analysis cache. The analysis response was approximately 65.8 MB; JSON serialization added 0.606 seconds. Python startup, pipe transport and UI projection are excluded from these direct-call measurements.

Version 0.17.7 caches the finalized display replay. Every lookup still reads and verifies the actual source chunk SHA-256 hashes. Eligibility requires finalized manifests and valid chunks. The key includes the full manifest contents, source paths and digests, plus the materializer and decoder source digests. Changes invalidate the derived result. A cache entry has its own result digest and strict-JSON check; damaged entries rebuild. The entry is atomically replaced per session phase, so ordinary revisions do not accumulate a new large file each time. A failed cache write leaves normal analysis usable.

Growing captures remain uncached. No telemetry math, sampling, event selection, gap handling or evidence labels changed. Tests preserve exact replay output, reject same-size/same-mtime source tampering, handle missing chunks and changed manifests, rebuild corrupt caches, and cover decoder revisions and unavailable cache storage. The performance regression fails against the original storage implementation and passes after the fix.

## Analysis chart labels

At 1426×893, opening Customize with ten chart rows caused the paired Speed/Air temperature label to wrap beyond its row and clip the beginning of the name. Each selected signal now occupies its own bounded line. Long names and units use an ellipsis; the existing accessible label, focus/hover explanation and selected-chart inspector retain the full information. The chart geometry and saved layout are unchanged.

## Planning layout

The previously blocked final-build visual check now passes at a 1426×893 window. The zone heading occupies its own row; Early, Middle and Late share equal-width columns. Full text wraps without overlap. The native screenshot was saved immediately as `RESUMED-0176-corner-grid.png` in the private evidence directory.

The recorded-reference plan displayed 160 laps, one fuel stop, a lap-80 target and a conservative 141.9-lap range. Changing 160 → 100 → 160 correctly changed the decision to zero stops with a 41.9-lap margin, then back to one stop at lap 80. These decisions were correct before the cache fix. Native observation intervals included tool latency; the direct backend measurements above establish the performance defect separately.

Additional native 0.17.6 checks passed: Ctrl+Home, maximize to 2560×1392 and restore to 1426×893; Clear laps followed by individual lap selection; switching between protected Default and the saved paired layout; replay midpoint seeking to lap 67; dated numeric setup comparison; and resetting comparison to the single-setup view. No new application error was logged during these interactions.

## Analysis performance investigation

A separate local profile used the real Iowa recording at its native sample rate and a new disposable archive. The profiler recorded 64.85 seconds for the cold workflow; instrumentation adds substantial overhead, so this number must not be compared with ordinary application timings.

Within that instrumented run, optional trace preparation accounted for 23.61 seconds and the complete lap-trace payload for 30.55 seconds. Telemetry loading accounted for 1.79 seconds. These identify areas for further investigation; they do not establish the size of a possible end-to-end improvement. The following unprofiled cached call took 1.22 seconds. No cold-analysis trace optimization or sampling change was made in this pass. The prior controlled DESKTOP-SIM Bristol recording without this full replay remains about 18.7 seconds cold and 1.8 seconds cached; it is a different workload from the 31-second grouped recording measured above.

## Verification and evidence

Repository verification passed with all 1,039 backend tests, contract checks and the MCP end-to-end check. All 390 application tests passed. Contracts and fixtures were regenerated and remain unchanged. The .NET self-contained win-x64 development publish succeeded.

- Development executable directory: `companion-app/artifacts/dev/v0.17.7-dev-20260921-225557`.
- Transport: `iRacingCoach-0.17.7-development-transfer.zip`, 87,256,337 bytes, 669 manifested files.
- SHA-256: `4f91ec5d03e53371e517f05a3735b2ae8e3473f3a1f940a3c2a2de354101934b`.
- Private transfer/evidence directory: `\\192.168.1.82\Joshua\iRacing-Coach-QA-20260921`; destination hash verified.

This development transport reuses installed Python/Coach Engine runtimes through isolated junctions. It is not an installer or full portable release. Private native screenshots, the incremental `RESUMED-QA-0176.md` report, compact timing JSON and profiler output remain outside source control, in the NAS QA directory and ignored local artifacts.

## Final 0.17.7 retest

DESKTOP-SIM verified all 669 manifest entries and launched the test from `C:\Temp\ICQA0177-0921\app`. The installed executable hash is unchanged.

| Same grouped Bristol recording | 0.17.6 repeated call | 0.17.7 cache population | 0.17.7 repeated call |
| --- | ---: | ---: | ---: |
| Analyze race | 31.232 s | 31.818 s | 3.079 s |
| Replay step within analysis | 29.110 s | 29.456 s | 0.963 s |

The repeated analysis call is approximately 90.1% faster. The telemetry-analysis cache was valid in every column; "cache population" refers specifically to constructing the new finalized-replay cache. This does not establish a faster first-ever full telemetry analysis. On the final repeated pass, history took 0.578 seconds, fuel planning 0.559 seconds and analysis-response JSON serialization 0.595 seconds. Startup, pipe transport and native UI work remain outside these direct-call timings.

Both final-build passes exactly matched the preserved 0.17.6 replay as complete parsed JSON. Canonical replay SHA-256: `83497a828981201eaa3af154d59cbabf2459ab0903f35cbdd0e688e0897e5a0e`. Both retain 8,203 display frames, 16 display gap markers, coverage for 205,798 unique source frames, all events and unchanged fuel decisions. The raw temporal coverage has 38 gaps; that count and the retained display markers are distinct and unchanged.

Final native 0.17.7 checks passed:

- Planning 160 → 100 → 160 retained the 141.9-lap range and produced one stop/lap 80 → zero stops/+41.9 laps → one stop/lap 80. The first 160-lap plan was visibly ready by 14.460 seconds after input began; subsequent 100- and 160-lap plans were ready by 8.434 and 7.963 seconds. These are observation upper bounds, including input and capture timing.
- With Customize open and ten charts at 1426×893, the two signal names occupy separate bounded lines without vertical clipping. Full names and units remain readable in the inspector. Screenshot: `RESUMED-0177-labels-1426.png`.
- Midpoint replay seeking displayed 28:29, lap 67 and 130 mph with the recorded cars and telemetry. Keyboard selection of 2x worked; playback advanced to 28:55/lap 68 and was paused after 29:08. Screenshot: `RESUMED-0177-replay-midpoint.png`.
- No native input/capture failure recurred. The application error log contained no new operation failure, and the installed executable hash remained unchanged.

The attempted border drag did not actually resize the window, so 1280×720 is not verified. The first replay-cache population still took about 32 seconds; first-time cold analysis and trace preparation remain performance work. Active simulator telemetry, external services, a completed tuning A/B cycle, exact-short-gap native seeking and complete every-control/every-resolution acceptance remain outside the evidence in this pass. Original raw files and the production installation were preserved.
