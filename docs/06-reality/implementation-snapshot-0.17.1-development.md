# iRacing Coach 0.17.1 development: real-recording stress test

Date: September 20–21, 2026. This is a development candidate, not user acceptance or complete simulator certification.

## Scope and evidence

The local browser preview was exercised against isolated copies of two real Toyota Supra Class B recordings: a fixed-setup Iowa race and an open-setup New Hampshire race. Original telemetry was read-only; hashes were checked when copying and when checking decoder parity. Test preferences and synthetic driver feedback stayed inside a disposable profile. No Garage61 or ChatGPT credentials were used.

Earlier inspection on DESKTOP-SIM established the cause of the blank September 20 Bristol race: an infeasible no-stop strategy emitted a bare `Infinity`, which the application's strict JSON reader rejected. Three saved open-setup analyses contained that defect. The recording itself had 159 laps, two runs, and one stop. Remote input subsequently failed with Windows access denied despite the desktop being unlocked; the remote task then returned empty completed runs. The patched Bristol recording has **not** been retested on DESKTOP-SIM.

## Fixes

### Race analysis and Home

- Serialize infeasible strategy duration as JSON null while retaining feasibility and rejection reasons. Reject and rebuild legacy caches containing non-finite JSON; enforce strict JSON at cache, archive, and MCP boundaries.
- Decode telemetry columns with validated binary strides instead of a Python call for every sample/channel pair. Scalar, array, string, boolean, and fractional sampling semantics have parity tests.
- Store compact JSON and reuse small persisted archive projections instead of repeatedly loading complete reports to build the race and setup lists.
- Show queued, active, and failed analysis states. Failed background analysis no longer silently leaves the service looking ready; opening the race can retry it.
- Prefer the track's display name over its internal recording identifier, avoiding labels such as Bristol FullPit beside Single Pit Road.
- Do not merge offline recordings solely because they reuse the simulator's session ID. Include recording event time, track, car, and session phase; unknown event times remain separate.
- Apply final damage, traffic, restart, and other established clean-lap exclusions consistently to summary and technical pace claims. Analysis profile v17 and UI cache envelope 14 retire older results.
- Use short atomic-write temporary names so otherwise-valid Windows archive paths do not fail because the temporary filename duplicates the destination name.

### Planning, setups, tuning, and settings

- Clear stale plan status when inputs change.
- Normalize recorded setup track labels against the installed track list, so a middle-dot separator does not prevent selecting a valid New setup context.
- Explain missing baseline setup files and the action needed to continue.
- Shorten instructional and implementation-oriented copy across Home, analysis, planning, setups, connections, and settings.
- Restore defaults through the active path provider. An isolated profile previously switched its recording/install roots back to production defaults; confinement checks rejected subsequent backend work. A regression test covers the corrected roots.

### Installation and upgrades

- Paint disabled installer text with readable colors; snapshot options before background installation; prevent duplicate installation and user-close during replacement.
- Stop and remove recognized prior installations in supported legacy locations, retry transient file locks, and report removal failures instead of silently ignoring them. Skip the current installation and refuse linked legacy roots.
- Run installation lifecycle tests with a confined app profile. The process replacement test explicitly disables WebView and therefore makes no native rendering claim.

## Interaction coverage

| Area | Actions actually exercised |
|---|---|
| Home | Recording discovery, automatic analysis, fixed/open summaries, navigation |
| Live telemetry editor | All 31 widget additions; number/gauge/bar/chart styles; units, precision, history, colors; move/resize keyboard controls; rename, duplicate, reset, delete, undo/redo; preset and driving trace expansion |
| Race analysis | Search and filters; five track-map modes; fit and lap filters; all 57 Iowa lap rows; selection shortcuts; incident detail; all 74 trace choices; add/remove/copy/delete layouts and fullscreen |
| Technical data | All four sections; pit stop and tire snapshot selectors; fuel scenarios; racecraft evidence |
| Replay | Iowa's unavailable state correctly explains missing full-field evidence; full playback is not certified |
| Planning | Recorded race plan; practice assumptions; open setup; timed event converted to 72 estimated laps with a pit stop |
| Setups | Car/track/season/search filters; recorded setup values; race/qualifying contexts; baseline lookup and missing-source behavior |
| Progressive Tuning | Fixed/open context selection; goals, stint phase, turn selection, entry/center/exit balance, severity/confidence, priorities, symptoms and notes; correction editor opened without saving invented geometry |
| Settings | Nine themes, motion and glance controls, preference save, defaults, copy/backup guide, connections and diagnostics |

No browser console errors were observed during the interaction sweep. Test automation timing includes control round trips and is not a rendering benchmark.

## Performance

Real Iowa native-rate analysis on the local host:

| Measurement | Before | After |
|---|---:|---:|
| First analysis | 61.338 s | 20.356 s for decoder/serialization changes; 24.409 s after v17 evidence corrections while tests also ran |
| Cached analysis | 4.380 s | 1.300–1.345 s |

The performance-only comparison preserved all metric/trace values; differences were generated timestamps and identifiers. Version 17 intentionally changes clean-evidence claims. First analysis still exceeds a few seconds and is an outstanding performance target. Results are local measurements, not DESKTOP-SIM measurements.

## Verification and remaining gaps

Repository verification: **1,017 Python tests passed**, contract/export checks passed. Application: **374 tests passed**. Native project build: zero warnings/errors before final packaging.

Full completion is blocked by remote access and unavailable hardware/service contexts. Outstanding checks include patched Bristol on DESKTOP-SIM, connected live simulator input and timing, the native telemetry popout, live Garage61/ChatGPT, and setup generation/export with a matching baseline.

A native launch on DESKTOP-JOSHUA exposed an additional host compatibility issue: WebView2's composition control terminated in `Direct3DHelper.CreateD3D9Device` with HRESULT `0x8876086A`. The browser preview runs successfully. This native rendering issue is **unresolved**; a passing headless lifecycle test must not be presented as proof that native rendering works on this host. The installed DESKTOP-SIM app had rendered earlier, but the updated package still requires testing there.

Exact package checksums and installer lifecycle results are recorded alongside the generated package, after the source commit is frozen. Private race reports and raw telemetry are excluded from source control.
