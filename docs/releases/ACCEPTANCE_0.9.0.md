# iRacing Coach 0.9.0 acceptance record

Date: 2026-08-02

This record applies the current corrective-development prompt. Superseded Starting Tune and permanently unsupported UI requirements are not release features.

## Passed release gates

- [x] Self-contained Windows x64 installer and portable package with SHA-256 checksums.
- [x] Release includes the application, deterministic backend, portable Python runtime, pinned signed Codex runtime, schemas, and uninstaller.
- [x] Existing installed versions are stopped and replaced; simulated failed replacement restores the prior payload.
- [x] Clean uninstall removes app-owned binaries, replaceable state, DPAPI credential fixtures, and private Codex state.
- [x] `Documents\iRacing Coach` and the source `Documents\iRacing` tree remain byte-identical through upgrade and uninstall tests.
- [x] Handoff contracts, fixtures, manifest, checksums, MCP initialize/ping/tools-list, end-to-end analysis, and backend tests pass.
- [x] 51 desktop tests pass with zero build warnings; 173 Python tests plus 48 subtests pass.
- [x] Release builds contain no debug fixture provider or seeded production race data.
- [x] Garage61 secrets are protected per Windows user and excluded from portable data.
- [x] Local analysis, planning, setup, tuning, live telemetry, and archived data remain usable without Garage61 or AI.

## Passed driver workflows

- [x] Race Analysis opens a searchable event browser and a distinct deep-analysis workspace.
- [x] Qualifying and race recordings can remain grouped in the event browser when both exist.
- [x] Recorded runs, lap counts, fuel, pit/service context, repair-confounding evidence, and strict race grades render from backend evidence.
- [x] Up to five recorded laps can be selected for synchronized local comparison.
- [x] A telemetry-derived vector track map is used when positional channels exist; normalized lap distance is explicitly labeled when they do not.
- [x] Speed, throttle, brake, steering, tire-stress proxy, gear/RPM, and available dynamics traces use a shared distance cursor and preserve short extrema during bounded downsampling.
- [x] Tire stress is explicitly identified as a proxy rather than measured per-lap wear.
- [x] Race Planning auto-selects the latest raced car and produces fuel use, range, stop count, pit target, tire guidance, triggers, corner phases, assumptions, and comparable local history when supported.
- [x] Setup Library displays supported exported setup fields in human-readable groups and provides read-only diffs without claiming opaque `.sto` editability.
- [x] Progressive Tuning starts from an analyzed open-setup race and captures run phase, corner phase, balance, severity, confidence, priority, and optional notes.
- [x] Live Telemetry renders a moving telemetry-derived map, driver inputs, live metrics, local scrolling traces, configurable lap history, race gaps, connection age, and a separate always-on-top Live Monitor.
- [x] Live coaching pause/resume and app-to-monitor open/hide actions were exercised in the native Windows app.
- [x] Persistent resource status distinguishes local engine reads/cache use, Garage61 requests, AI work, failures, and current activity without exposing private content.
- [x] Settings explains portable data, machine-bound connections, migration, and read-only iRacing source folders.

## Visual QA completed

- [x] Primary 1440-class populated views reviewed in the native WPF/WebView2 window.
- [x] Analysis, planning, tuning, settings, and Live Monitor captures are checksum-verified by `tools\VerifyVisualBaselines.ps1`.
- [x] Title bar, icon, charcoal theme, selected navigation, status placement, long scrolling content, background jobs, chart legends, and evidence labels were inspected.
- [x] A crowding defect in tuning severity/confidence controls and an oversized priority checkbox were found during native QA, corrected, rebuilt, and rechecked.
- [x] Keyboard-accessible names and logical native accessibility trees were verified for primary navigation and workflow controls.

## Measured performance

- Cached Race Card JSON parsing and UI mapping, 2,000 iterations: 0.1184 ms median, 0.1871 ms p95, 0.4657 ms max.
- Sanitized ordinary local analysis: approximately 53-59 ms backend elapsed in repeated handoff smoke runs.
- Live update compute latency: asserted below 25 ms by the coordinator regression test; the native debug replay remained responsive with 1,800 retained samples and no visible interaction lag.

## Honest remaining limits

### Temporary data/session limits

- A physical track outline, gear/RPM, dynamics channels, tire endpoints, damage details, and setup fields appear only when the selected recording actually contains defensible evidence.
- Live race fields appear only while iRacing exposes them through the local SDK; stale/disconnected state remains explicit.
- Qualifying remains selectable in grouped event history, but the richest grade and strategy review is currently race-focused.

### External-service limits

- Garage61 reference laps require a valid per-PC connection and service approval/availability.
- AI coaching requires the bundled Coach Engine to be healthy and the user to complete its managed sign-in; deterministic work does not wait for it.

### Deferred product work

- Numeric time-delta overlays, calibrated groove/lane classification, target/reference lap insertion, map zoom/drag controls, continuous tire-phase target modeling, and a qualifying-specific briefing are not represented as finished release features.
- A complete screenshot matrix at 100%, 150%, and 200% Windows scale was not automated in this round; the saved 1440-class native captures and the earlier compact/minimum-window baselines remain the visual evidence available.
- No video artifact was recorded. Native interaction proof consisted of direct button-by-button Windows automation and saved populated-screen captures.

