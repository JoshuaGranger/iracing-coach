# Implementation Snapshot: 0.16.0 Development

Status: active development source and explicitly requested simulator-feedback installer candidate. This is not a user-accepted stable release.

Evidence date: 2026-08-11.

## Version boundary

The application, backend client, Coach Engine client, installer, uninstaller, repair path, browser asset keys, visual-QA identity, and release script identify `0.16.0`. The latest accepted packaged baseline remains `0.14.2`; the 0.15.0 candidate remains immutable prior development evidence.

`BuildRelease.ps1` now refuses mixed source identities and refuses to package a dirty or untracked working tree before it resets any release directory. The exact source commit is written into the bundled Coach Engine manifest.

## Implemented development reality

- Race Analysis rejects sentinel GPS samples and implausible auxiliary geometry before a track observation can become canonical. Real Iowa and Daytona recordings regenerate complete main, pit, entry, and exit paths instead of the prior diagonal outliers; defensive UI gates also suppress poisoned legacy layers.
- Selected laps remain logical selections at any count, while chart rendering, cursor detail, and point density use explicit budgets. A 500-lap selection no longer creates one unbounded SVG/data graph per lap. Literal selected-lap spatial spread still controls the colored map ribbon, while individual trace mode remains approximately one pixel wide.
- Chart and map pointer work is coalesced to the display frame, uses cached geometry and native HTML tooltip cards, keeps the map cursor a constant screen size while zooming, and avoids synchronous text/layout measurement in the pointer path. The Customize drawer reuses compositor-friendly cached geometry during its shared 500 ms structural motion.
- Laps and runs uses content-sized one-row columns rather than fixed narrow slots. Track and laps keep the shared one-third/two-thirds splitter, and Track zoom remains cursor-centered with Fit as the maximum zoom-out extent.
- Technical Data carries every supported finding into both the four-card overview and its corresponding drill-in without an arbitrary item cap. Pit, tire, fuel, driving dynamics, and racecraft use graphical decision views, complete values, concise takeaway/action text, and strict known-only two-tire versus four-tire comparison states.
- No-stop race strategy is a useful result rather than an empty card. Race planning recomputes fuel decisions for the requested distance, suppresses irrelevant pit language for a race comfortably within range, and presents a recommended opening decision before supporting calculations.
- Starting Tune uses installed-car and installed-layout type-or-browse controls, places Race/Qualifying with the workflow rail, and treats the current season as automatic context.
- Progressive Tuning keeps the track in the left two-thirds and one consolidated right-side toolbox. Hover and selected corners use different emphasis; the active corner editor stays in the toolbox; severity/confidence remain visible with explanations; and the race chooser distinguishes open from fixed evidence.
- Full Live Telemetry fits its remaining viewport without document scrolling. Trend wells grow with their cards and share a softer graphite instrument treatment with the native popout. The popout has a centered selector, deterministic size presets, no pointer-captured resize loop, full/main visual parity, and mutually exclusive full-app/popout visibility. Tray left-click and Return to app restore the main application.
- New live replay capture retains distinct SDK ticks up to 60 Hz in bounded, nonblocking, atomic delta-binary/gzip chunks. Queue drops, time gaps, cadence, compression, and write metrics are explicit. Legacy v1 captures remain readable. Raw high-rate fidelity stays on disk while analysis/UI replay data is bounded for long races, and observed player/session events remain distinct from inferred incident types.
- Race replay opens at the first usable grid/race-phase frame instead of an empty get-in-car instant, preserves interior gaps, uses one flag-colored seek rail, and never converts a missing car lap position to start/finish. Older approximately 2 Hz captures remain approximately 2 Hz; they are not presented as retroactively high-rate.
- The canonical high-contrast graphite theme is synchronized across generated web variables, native WPF resources, and the telemetry popout. Nine curated accents plus a custom color remain available; the default mint is brighter and surfaces are neutral graphite rather than green-on-green.

## Recorded development evidence

The current development source passed these integrated gates on 2026-08-11:

| Gate | Result | Scope |
| --- | --- | --- |
| Integrated .NET suite | 255/255 passed | Desktop host, coordinator, contracts, Race Analysis, Race replay, planning, tuning, live monitor, installer, and source-policy checks |
| Integrated Python suite | 247/247 passed | Telemetry analysis, geometry, Technical Data, storage, replay extraction/capture, strategy, and truthfulness contracts |
| Browser JavaScript syntax gate | 9/9 passed | Every first-party JavaScript asset loaded by the Hybrid UI |
| Release solution build | 0 warnings, 0 errors | Current source-tree Release build; this is not the final installed artifact |

Current real-data evidence includes the supplied 12-race development corpus and direct regeneration of Iowa (78,350 source samples) and Daytona (23,439 source samples), each rejecting one `(0,0)` sentinel while producing plausible complete geometry.

The August 9 Iowa Open recording supplied a usable legacy Race replay of 7,775 frames across five segments at about 1.935 Hz. The replay was opened and exercised in the real-data browser path at both 1280x720 and 1920x1080. It began at the first playable recorded state, rendered the oval and recorded participants, kept playback/seek/speed on the shared clock, and used one flag-colored seek rail without page overflow or clipped primary controls. This proves the recorded-data reconstruction path for that capture; it is not a claim that the legacy source was sampled at display rate.

High-density browser evidence used a real 82-lap selection. All 82 laps remained logically selected while rendering stayed bounded to 252 chart trace paths, 27 map paths, and about 3,408 DOM elements. Repeated actual pointer movement measured 62 ms average/113 ms maximum tool round-trip over the aligned charts and 66 ms average/70 ms maximum over the track map. Fifty Customize open/close cycles ended with 0 px layout drift and no overflow. Automated density coverage also exercises 1, 3, 20, 82, and synthetic 500-lap selections, with 500 logical laps retained behind fixed render, detail, bin, and vertex budgets rather than 500 unbounded per-lap graphs.

The browser inspection also covered Iowa, Daytona, Portland, and New Hampshire geometry; the two-by-two Technical Data overview and all four investigations; Progressive Tuning fixed-screen layout and corner editor; Settings theme/copy guidance; Starting Tune selectors; Race Planning; and full Live Telemetry at 1280x720 and 1920x1080 where applicable. These are current-source development checks. They do not certify every maximized, restored, scaling, keyboard, reduced-motion, missing, loading, and error cell in the binding acceptance matrices.

Replay storage benchmarks cover 24- and 64-car fields through 60 Hz and deliberately saturated nonblocking queues. No 0.16.0 installer hash, portable-archive identity, source commit, or installed lifecycle result is claimed in this snapshot until the exact frozen package is produced and tested.

## Honest limits and pending acceptance

- A legacy replay cannot gain information it never recorded. The supplied Iowa live capture has 7,775 frames across five segments at about 1.935 Hz; newer sessions use the high-rate v2 recorder.
- Public full-field SDK data includes position/scoring but not competitor fuel, tire condition, setup, private penalties, or semantic contact cause. Those values are not inferred. A player incident-count increase cannot by itself distinguish car contact, wall contact, loss of control, or fault.
- The real corpus contains confirmed four-tire calls but no confirmed real two-tire/right-side comparison. The comparison contract is covered synthetically and remains withheld on real races until both call types and comparable outcomes are known.
- Track-turn screenshots/annotations from the simulator remain an authorized acquisition workflow, not permission to invent official turn bounds from telemetry load zones.
- Automated tests and local browser checks do not substitute for Joshua's simulator-PC judgment of high-refresh cursor feel, popout usefulness while driving, or overall visual quality.
- The recorded pointer numbers above include browser-control/tool round-trip overhead and are useful regression measurements, not a display-frame-time, input-latency, or 60/120/144/240 Hz delivery claim. Sustained simulator-PC cadence still requires instrumentation against the packaged executable and real SDK source.
- The complete maximized/nonmaximized, scaling, keyboard, reduced-motion, missing/loading/error, native popout, upgrade, rollback, uninstall, and data-preservation matrices remain package acceptance work. Passing the current source gates does not make 0.16.0 a stable or user-accepted release.
