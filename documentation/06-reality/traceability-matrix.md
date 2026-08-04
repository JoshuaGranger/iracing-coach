# Traceability Matrix

This matrix maps specification areas to implementation and verification anchors. A path demonstrates where evidence should be inspected; it does not by itself prove conformance.

| Specification | Main implementation anchors | Test/evidence anchors | Snapshot status |
| --- | --- | --- | --- |
| `HOME-*`, `NAV-*` | `CompanionShell.razor`, `NavRail.razor`, `HomePage.razor` | `FinalProductFixtureTests.cs`, UI packet | Implemented with fixture acceptance |
| `RA-001` to `RA-025` | `AnalysisPage.razor`, `AnalysisWorkspacePage.razor`, `DashboardMapper.cs`, `native_events.py` | `FinalProductFixtureTests.cs`, `test_native_events.py`, QA report | Implemented; real-library breadth pending |
| `RA-030` to `RA-054` | `TelemetryWorkspace.razor`, `AnalysisWorkspacePage.razor`, `analysis_engine.py` | full-telemetry Python tests, .NET source assertions, native fixture walkthrough | Implemented against fixtures; continued real-channel breadth validation required |
| `RA-060` to `RA-112` | integrated analysis insight rail and lap/run popovers, `analysis_engine.py`, `reporting.py`, `race_card.py` | analysis/report/damage/race-card tests and native fixture walkthrough | Broad implementation; some rubric/data combinations partial |
| `PLAN-*` | `PlanningPage.razor`, planning contracts/mappers, deterministic report data | `FinalProductFixtureTests.cs`, reporting/workflow tests | Implemented fixture workflow |
| `SETUP-*` | `SetupPage.razor`, `RuntimeMapper.SetupPackage`, `setup_catalog.py`, coordinator contracts | setup-catalog, package, and product-truth tests | Internal indexing and Starting Tune implemented; unused library UI removed |
| `TUNE-*` | `SetupPage.razor`, `TuningPage.razor`, `TuningTrackSelector.razor`, `tuning_engine.py`, `tuning_workflow.py` | tuning-engine/workflow tests | Starting Tune and event-linked progressive tuning implemented; real A/B pending |
| `LIVE-*`, `LMG-*` | `LiveTelemetryPage.razor`, `LiveTelemetryLayoutGrid.razor`, `LiveMonitorWindow.*`, `LiveMonitorLayouts.cs`, `LiveTelemetryCatalog.cs`, `LiveTelemetry.cs`, `IRacingSdkTelemetrySource.cs` | `LiveMonitorTests.cs`, replay/live service tests, fixture interaction packet | Shared full/miniature layout and replay verified; continued direct real-telemetry validation required |
| `CONN-*`, `SET-*`, `DIAG-*` | embedded `ConnectionsPage.razor`, `SettingsPage.razor`, diagnostics, stores, logging | coordinator/backend and production-source tests | Implemented inside Settings; real external auth varies |
| `EV-*` | Python analysis/report/tuning modules, `EvidenceBadge.razor`, `RuntimeMapper`, grade audit UI | contract, engine, and product-truth tests | Five-category availability is explicit; rubric calibration still requires representative expert review |
| `TD-*` | IBT reader, native events, storage, analysis engine, live source | Python telemetry/event/storage tests | Implemented with synthetic/sanitized sources; real-field pending |
| `PORT-*`, `SEC-*` | durable archive, portable logical settings, machine placement/credential stores | coordinator, monitor migration, and path-security tests, privacy scan | Machine-bound credential and placement split implemented |
| `ARCH-*`, `PROC-*` | app startup, coach engine, backend client, coordinator | backend/coordinator tests and release build | Implemented local runtime shape |
| `G61-*` | Garage61 client and credential UI/store | Garage61 adapter tests | Adapter/configuration implemented; real service acceptance conditional |
| `AI-*` | coach-engine orchestration and MCP contracts | engine probe, contract tests | Deterministic engine implemented; optional AI is noncritical/partial |
| `INST-*`, `UPG-*`, `UN-*` | installer/uninstaller, release/lifecycle scripts | installer-upgrade evidence | Locally verified; representative-PC breadth pending |
| `QA-*`, `REL-*` | build/verify scripts and release packet | release records, targeted iteration evidence, direct local/native validation | Gate scope follows the changed behavior; packaged releases require full applicable release evidence |
| `UI-*`, `ACC-*`, `PERF-*` | Razor/CSS/WPF host, performance probe | visual baselines, UI audit, performance packet | Substantial fixture evidence; exhaustive accessibility/performance acceptance pending |

## Maintenance rule

When a requirement changes, the author must inspect all mapped implementation and evidence anchors. When implementation changes behavior, the author must update the affected requirement or record a deliberate deviation. When a test changes without a requirement change, reviewers must confirm that the test still measures the requirement rather than merely current behavior.
