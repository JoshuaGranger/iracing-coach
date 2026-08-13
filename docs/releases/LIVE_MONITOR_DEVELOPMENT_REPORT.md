# iRacing Coach Live Monitor — v0.4.0 development report

Date: 2026-08-02  
Backend: `iracing-coach-local` 0.3.0  
MCP protocol: 2025-06-18  
Contract surface: MCP v1, 16 tools

## Delivered

- A native, separate, always-on-top WPF Live Monitor using the same `CompanionState`, local IRSDK reader, and deterministic evaluator as the main Live telemetry page.
- Compact and Expanded layouts, resize/drag, position lock, opacity, click-through, hidden controls with a small recovery handle, default-placement reset, safe monitor recovery, and Per-Monitor V2 DPI behavior.
- Portable persistence for visibility, layout mode, monitor, position, size, opacity, click-through, position lock, visible controls, safe-glance behavior, secondary fields, reconnect behavior, and optional global hotkey.
- Main Live telemetry and Home controls, a real Windows tray menu, close/minimize-to-tray, first-close explanation, double-click restore, pause/resume, Settings, explicit Exit, and single-instance activation.
- Local IRSDK shared-memory input with overall/class scoring, same-lap leader/ahead/behind intervals, lap/position/flag/fuel/weather/repair/input data, defensive disconnect/reconnect behavior, and no console window.
- Deterministic priority across flags, penalties/tow/mandatory repair, fuel, caution/pit state, traffic, context-valid pace, persistent driving feedback, and informational state.
- Clean in-session target bands only after three comparable laps; unsupported strategic pit windows remain explicitly unavailable and separate from the derived fuel hard limit.
- Rolling multi-second gap trends with caution/pit suppression and correct physical-gap direction for the car behind.
- Safe-glance cue queuing with urgent alert override.
- Persistent brake-peak feedback by lap-distance load zone using Joshua's clean in-session repeatability baseline. It requires three comparable unusual laps and excludes caution, pit, repair, tow, black-flag, and close-traffic-confounded laps.
- Installer v0.4.0 upgrade replacement that closes the prior installed process, stages the new payload transactionally, restores the prior payload on failure, and leaves `Documents\iRacing Coach` untouched.

Production state is never seeded. The scenario sources used for visual QA are compiled only into Debug/Preview builds.

## Visual QA captures

- [Compact green flag](artifacts/qa/live-monitor-compact-green.jpg)
- [Expanded](artifacts/qa/live-monitor-expanded.jpg)
- [Caution](artifacts/qa/live-monitor-caution.jpg)
- [Critical fuel](artifacts/qa/live-monitor-critical-fuel.jpg)
- [Mandatory repair](artifacts/qa/live-monitor-repair-warning.jpg)
- [Persistent braking cue](artifacts/qa/live-monitor-persistent-braking.jpg)
- [Unavailable clean baseline](artifacts/qa/live-monitor-unavailable-baseline.jpg)
- [Disconnected / waiting](artifacts/qa/live-monitor-disconnected.jpg)

Native interaction QA also covered opening and hiding from Home and Live telemetry, Compact/Expanded switching, control hiding/restoration, hover/focus contrast, lock and reset controls, close-to-tray, monitor-only operation, and second-launch activation of the existing instance.

## Verification

- Full Release solution build: succeeded with 0 warnings and 0 errors.
- Companion tests: 27 passed, 0 failed.
- Live-specific regression coverage includes alert priority, safe-glance delay/override, rolling gap direction and caution suppression, fuel hard limit separation, clean-lap pace evidence, physical gap versus lap-time difference, portable layout persistence, pipeline latency/drop counters, persistent versus transient brake feedback, repair-confounded suppression, and disconnect baseline reset.
- Backend/handoff verifier: passed, including 109 manifest files, 17 contracts, 14 fixtures, 16 tools, backend tests, and the synthetic MCP end-to-end flow.
- Single-instance test: a second executable invocation exited with code 0, the process count remained one, and the hidden main window was restored.
- Active Live Monitor performance with the main window hidden: 8.95–10.72% of one CPU core, 0.75–0.89% of the 12-logical-processor machine, 189–192 MB working set in the instrumented Debug build, and zero dropped frames in the automated pipeline test.
- Native monitor rendering is capped at 5 Hz while the shared telemetry evaluator continues at 10 Hz. The hidden WebView no longer re-renders for every telemetry frame.

## Evidence and limitations

- `CarIdxF2Time` intervals are accepted only for same-lap comparisons; different-lap, invalid, missing, caution, and pit-cycle comparisons remain unavailable/stale rather than being normalized speculatively.
- The current target hierarchy has a defensible clean in-session fallback. Aligned representative-lap and validated pre-race policy inputs can be added later without changing the monitor contract.
- No validated live strategic pit-window model is wired yet. The UI therefore shows strategic window unavailable while keeping the fuel hard limit distinct. It never labels a fuel-only result as strategy or optimal.
- Exclusive-fullscreen iRacing may cover an always-on-top window. Diagnostics explains that borderless-windowed mode or a second monitor is the supported arrangement; the app does not change iRacing display settings.
- Real racing-PC checks at 150%/200% scaling, an actual disconnected second monitor, borderless iRacing, and representative long sessions remain hardware acceptance checks. Per-Monitor V2 behavior, placement bounds, and reconnect paths are implemented and covered by local state/interaction tests.

## Release

- Installer: `artifacts/dist/iRacingCoach-0.4.0-Setup.exe`
- Checksum: `artifacts/dist/iRacingCoach-0.4.0-Setup.exe.sha256`
- Size: 378,886,182 bytes
- SHA-256: `5ab2ff8b86481515b2d2126b34e074d6255c631769126088dc11106043c15bcb`

The installer passed two installs against the same disposable target. The second run stopped the running first copy, removed a marker placed in the first payload, replaced all app files, retained all required runtime/backend files, and left no staging or backup folder.
