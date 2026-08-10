# Implementation Snapshot: 0.15.0 Development

Status: active development source for direct-executable feedback. This is not a packaged release or user-acceptance record.

Evidence date: 2026-08-10.

## Version boundary

The application source, backend client identity, and development publish path identify `0.15.0`. The latest stable packaged artifact remains `0.14.2`; its source commit, installer and portable hashes, payload measurements, and acceptance boundaries remain immutable in [the 0.14.2 snapshot](implementation-snapshot-0.14.2.md). No 0.15.0 installer, portable archive, upgrade certification, or release hash is claimed here.

## Implemented development reality

- Race Analysis separates Telemetry, Technical data, and Race replay. Telemetry has a shared chart cursor, selected-lap averaging, exact-configuration vector maps, independently collapsible Track and Laps panels, a categorized drag-and-drop trace toolbox, global named layouts, spotlighting, and truthful recorded-data gates.
- Technical data presents fixed-screen Pit strategy, Tire management, Fuel management, and Racecraft and pace summaries with supporting drill-ins. The source now carries every supported material finding into both the overview and its drill-in rather than truncating categories to three values. Pit strategy includes a strict within-race two-tire/four-tire retrospective only when both calls and their corresponding service, wear, following-run pace, or pit-cycle facts are known; rules, causation, and unavailable history remain explicitly withheld. Unsupported inputs produce concise reasons instead of manufactured conclusions.
- Race replay consumes recorded participant, flag, and player telemetry on one clock when the recording contains the required channels. Older or incomplete recordings receive a single explicit unavailable state; `.rpy` discovery alone is not treated as replay evidence.
- Progressive Tuning evidence-contract v2 uses a representative recorded race, exact car and track-configuration identity, verified named turns, structured early/mid/late-run feedback, durable drafts and map corrections, a versioned NASCAR O'Reilly/Xfinity ruleset, one controlled garage change, rollback guidance, and optional AI selection constrained to the deterministic candidate whitelist.
- Finalized raw IBTs are copied into append-only content-addressed portable storage without moving or deleting the originals. Track geometry, telemetry traces, tire observations/models, target laps, layouts, tuning drafts, and experiment outcomes remain portable user data.
- The NASCAR-first tire/capability foundation keeps recorded measurements, derived proxies, predictions, confidence and bounds, and eventual Garage61 references distinct. It withholds estimates when compatibility or evidence thresholds are not met.
- Backend archive and iRacing fallback roots are derived from the active Windows user's `Documents` folder when no explicit environment or configured override is supplied; machine-local absolute user paths are not source fallbacks.
- Performance repairs retain the .NET/WPF shell and Blazor/WebView workspaces. Race Analysis cursor work now coalesces to one display frame with no synchronous layout probe in that frame, Live Telemetry performs no idle animation or layout work, active dashboard drag reuses cached geometry instead of the approximately 3,900 layout reads per second measured in the baseline harness, resize rebuilds the canvas backing store once after settling, and automatic discovery no longer blocks on unavailable mapped drives. The measured causes were browser-side scheduling/layout/allocation and synchronous discovery; the evidence does not justify a C++ rewrite.
- The complete mapped-signal audit and each signal's primary, contextual, diagnostic, or excluded role are recorded in [Technical Data signal coverage](technical-data-signal-coverage-0.15.0-development.md).

## Recorded development evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| Integrated .NET suite | 232 passed, 0 failed | Desktop, coordinator, mapping, layout, replay, Garage61, and Progressive Tuning source contracts |
| Integrated Python suite | 230 passed, 0 failed | Deterministic analysis, storage, raw archive, tire foundations, Garage61, and structured tuning contracts |
| Handoff inventory | 17 MCP tools; 114 manifested files | Contract/export/manifest consistency, not packaged-product acceptance |
| Browser-driven development review | Core Race Analysis, Technical data, Progressive Tuning, Live Telemetry, Home, and Settings states exercised at primary desktop sizes | Local development host and available recordings; not real live-session or broad hardware acceptance |
| Instrumented performance repair | Race cursor forced-layout probes approximately 27–40/frame to 0; live idle frames/layout probes to 0; active drag layout reads approximately 3,900/s to 0; resize backing-store rebuild once after settle; approximately 21 s mapped-drive startup stall removed | Static/source instrumentation and local harness evidence; not real-session latency or accepted 244 Hz simulator validation |

## Open acceptance boundaries

- The full-field Race replay supported state still requires a representative recording containing participant-position channels; the available older recording proves only the truthful unavailable path.
- Real iRacing capture cadence, cursor-to-photon latency, high-refresh presentation, combined-load behavior, and while-driving usefulness require exact-executable real-session evidence. The local harness repairs do not constitute accepted 244 Hz simulator validation.
- The Garage61 reference action remains explicit and external. Local tests do not claim a successful service result or representative-lap breadth.
- Tire and target-lap models remain conditional on compatible retained history and calibration; unavailable or low-confidence output must remain visible as such.
- Native window, tray Exit, scaling, reduced-motion, animation, and every-control review belong to the exact development executable used for feedback. Passing automated or browser-host checks does not substitute for those gates.
- Joshua has not accepted 0.15.0 merely because implementation and local checks pass.
