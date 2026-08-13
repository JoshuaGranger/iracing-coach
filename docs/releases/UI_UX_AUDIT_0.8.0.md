# iRacing Coach 0.8.0 UI/UX audit and remediation

Date: 2026-08-02  
Scope: native WPF/Blazor Hybrid application, Live Monitor, first-run flow, installer shell, tray surfaces, debug-only representative states, and the production UI source.

## Outcome

The application now uses a single calm charcoal design system, one coherent SVG icon language, compact racing-first information hierarchy, responsive layouts, visible keyboard focus, and direct product language. Normal screens no longer expose developer-oriented protocol, schema, tool, workspace, runtime, prompt, or raw-path detail. Evidence classes and unavailable reasons remain intact through restrained labels and expandable technical records.

The WPF/Blazor Hybrid stack was retained. It already provides the required Windows integration, packaged local coordinator, system tray, always-on-top Live Monitor, self-contained deployment, and a testable shared UI without risking a rewrite of the verified telemetry backend.

## Four-pass remediation

### 1. System-level consistency

- Rebuilt the dark theme around neutral charcoal layers instead of blue surfaces.
- Added the missing 20 px and 28 px spacing tokens and applied a consistent 4/8-based scale.
- Reduced radii, eliminated decorative movement, and standardized borders, control heights, selected, pressed, disabled, warning, error, and unavailable states.
- Added `ProductIcon.razor` and replaced mixed glyph/emoji navigation and action icons.
- Matched the WPF title bar, application icon, tray icon, Live Monitor, and installer colors to the product theme.
- Added stable tabular numerals for telemetry values and maintained softer text contrast without pure white on pure black.
- Added `:focus-visible`, `prefers-reduced-motion`, `prefers-contrast`, and Windows forced-colors rules.

### 2. Workflow usability

- Home is now a compact Race Desk. Recordings and live state update automatically; manual refresh controls and routine update noise were removed.
- Service health lives quietly in the navigation rail. The header stays empty unless a problem needs action.
- Race Analysis opens a newest-first recorded-race browser and keeps unavailable deep-analysis features out of the interface instead of simulating them.
- Race Planning automatically starts from the most recently raced car and reports only comparable recorded history. It explicitly distinguishes history from a prediction.
- Setups presents discovered local setup files as read-only and keeps the original source intact. Technical fingerprints are disclosed only on request.
- Progressive Tuning now starts from an eligible recorded race and uses run phase, corner phase, balance, corner/zone, and optional detail controls. The result proposes one controlled change, explains the intended effect and risk, preserves the baseline, and records the comparison outcome.
- Connections stores Garage61 credentials through the app's protected settings flow and presents repair/reconnect actions without implementation terminology.
- Settings leads with portable data and migration tasks. Raw paths and diagnostics are behind deliberate disclosure controls.
- First Run adapts to detected iRacing data and offers an honest repair action when the packaged Coach Engine is unavailable.

### 3. Screen-level polish

- Removed oversized health cards, excessive page prose, sticky controls that covered content, unused UI-gallery remnants, and empty diagnostic space.
- Rebalanced page margins, navigation width, split panes, data rows, compact empty states, and notification placement.
- Added deliberate layout collapse at 1020 px and single-column workflow behavior at 860 px.
- Fixed a selector-specificity defect that leaked clipped service text into the collapsed rail.
- Tightened Live Monitor spacing and reduced its primary cue to one glanceable instruction.
- Kept evidence and recovery messages specific: problem, consequence, and action are shown without stack traces.

### 4. Pixel and copy review

- Replaced robotic and developer-facing copy across Home, Live telemetry, Analysis, Planning, Setups, Progressive Tuning, Connections, Settings, Diagnostics, and First Run.
- Removed routine “updating” and “updated just now” messages.
- Removed “local workspace,” “provisional donor,” and normal-screen protocol/runtime/schema language.
- Reviewed native screenshots at compact, standard, and full-HD sizes and corrected wrapping, clipped rail labels, panel density, title-bar color, control alignment, disclosure hierarchy, and error prominence.

## Native QA matrix

| Surface | States inspected | Window/capture size |
| --- | --- | --- |
| Home | populated, empty, actionable error | 1000x700, 1366x768, 1440-class |
| Live telemetry | disconnected, connected green, caution, repair-aware capability behavior | 1366x768, 1920x1080 |
| Live Monitor | green and caution; expanded/compact information hierarchy | 548x269 and desktop overlay |
| Race Analysis | populated, empty, partial/error, selected race, completed-card scenario, running job tray | 1366x768, 1920x1080 |
| Race Planning | automatic recent-car selection and comparable-history result | 1366x768 |
| Setups | populated read-only library and narrow navigation | 1000x700 |
| Progressive Tuning | guided feedback and generated controlled experiment | 1366x768 |
| Connections | disconnected/recovery states | 1366x768 |
| Settings / troubleshooting | portable data, collapsed paths, connection summary, explicit diagnostic disclosure | 1366x768 |
| First Run | iRacing-ready and repair-required ChatGPT step | 1000x700 |

The responsive DOM was also exercised at a 2560x1440 window. The desktop capture API returned a blank WebView compositor surface at that exact size, so it is not accepted as a visual baseline. No broad image tolerance is used: the checked-in baseline verifier requires exact dimensions and SHA-256 for 12 native captures.

## Interaction and accessibility results

- Every visible native control was enumerated through the Windows accessibility tree and checked for a usable accessible name.
- Direct coordinator/state tests cover navigation, selection, save, credential redaction, troubleshooting, tuning, outcome recording, live-state transitions, cancellation and duplicate-work safeguards.
- 10,000 in-memory navigation transitions completed in 19 ms in the final test run; no navigation path performs backend work on the UI thread.
- Keyboard focus is explicit and unclipped; semantic button, label, fieldset, details, status, and grouped-control markup is present.
- Color is not the only carrier of live, caution, evidence, error, or availability state.
- High-contrast, increased-contrast, and reduced-motion adaptations are implemented in CSS. The host is per-monitor-DPI aware and vector icons remain sharp at scaling changes.
- Windows input injection could inspect but not activate this captured WPF window in the current Codex desktop session. After repeated activation failures, button behavior was verified through isolated debug state transitions and automated handler tests rather than claiming successful physical mouse clicks.

## Automated verification

- .NET solution: clean Debug build, 0 warnings, 0 errors.
- .NET product tests: 50/50 passed.
- Python deterministic-backend tests: 173/173 passed.
- Handoff verifier: 109 files verified, 17 contracts loaded, 14 fixtures loaded, 16 MCP tools discovered, end-to-end smoke test passed.
- Contract export: current and unchanged.
- Visual baselines: 12 exact hashes and dimensions verified by `tools/VerifyVisualBaselines.ps1`.
- Evidence/security regression coverage confirms that unsupported wet, multiclass, global Garage61, setup-writing, and missing-evidence claims are not presented as working features.

## Performance evidence

- Coordinator navigation stress: 10,000 transitions in 19 ms.
- Handoff end-to-end deterministic dashboard/analysis smoke path: 47.203 ms backend time on the development PC.
- Live telemetry publish/compute stress test completed without dropped frames; its full test case completed in 763 ms.
- Cached UI navigation and selection remain local. Long-running analysis, connection, copy/migration, and AI work use cancellable background paths and do not block navigation.

These are fixture/test measurements, not predictions of IBT parsing time on the racing PC.

## Remaining limitations

### Product

- Track maps, multi-lap charts, grading, package generation, setup comparison, archive-restore wizard, updater UI, and other previously identified unavailable/nonfunctional features remain intentionally absent rather than being exposed as placeholders.
- The current Progressive Tuning builder is structured and evidence-bound but does not yet provide a clickable telemetry-derived track map or multi-card priority editor.

### Data

- Race Planning can summarize comparable recorded runs but does not invent an upcoming schedule, pit prediction, or owned-car list when iRacing does not expose the required local evidence.
- Opaque `.sto` content remains read-only. The app does not claim to write simulator-loadable setups.

### External access

- Garage61 global-visible comparison remains disabled until Garage61 grants the required scope. Personal credentials are Windows-user protected and deliberately excluded from portable copies.
- ChatGPT coaching requires a healthy packaged Coach Engine and a local account connection. Deterministic race tools remain usable offline.

## Evidence and artifacts

- Final screenshots and exact-hash manifest: `artifacts/qa/v0.8.0/`
- Visual verifier: `tools/VerifyVisualBaselines.ps1`
- Canonical theme: `../../config/theme.dark.json`
- Generated web theme: `src/iRacingCoach.UI/wwwroot/theme.generated.css`
- Generated native theme: `src/iRacingCoach.App/Theme.Generated.xaml`

All representative content used for screenshots is sanitized and compiled only in Debug builds. Release builds contain no seeded races, cars, setups, Garage61 data, or user credentials.
