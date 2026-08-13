# Production capability audit — 0.7.0

Audit date: 2026-08-02

The executable registry in `src/iRacingCoach.Coordinator/CapabilityRegistry.cs` is the canonical inventory. Every `ProductCapability` has exactly one `CapabilityDefinition` containing its user value, data source, classification, applicability rule, validation method, temporary failure states, fallback, and production-visibility decision. `CapabilityRegistryTests.Inventory_ClassifiesEveryProductCapabilityExactlyOnce` prevents an unclassified capability from shipping.

## Inventory

| Capability | Classification | Production rule |
| --- | --- | --- |
| Home | Supported now | Always shown |
| Live telemetry | Supported now with temporary disconnected state | Shows “Waiting for iRacing”; data cards appear only after SDK connection |
| Live Monitor | Supported now | Always usable; session-only rows collapse when disconnected |
| Race analysis | Supported now | Finalized Race sessions only |
| Race planning | Conditionally applicable | Navigation and workflow card require recorded race history |
| Setup library | Supported now | Shows discovered read-only `.sto` files or one useful empty state |
| Progressive tuning | Conditionally applicable | Requires an analyzed open-setup race and embedded setup |
| Connections | Supported now | Working ChatGPT and personal Garage61 connection actions only |
| Settings | Supported now | Working storage, migration, Windows, connection, and Live Monitor settings only |
| Backup and migration | Supported now | Integrity-checks the durable Coach folder before copying |
| Raw telemetry relocation | Conditionally applicable | Action appears only when an archived source is missing |
| ChatGPT coaching | Supported now with temporary reconnect/restart states | Hidden if the private engine needs repair; reconnect state is actionable |
| Garage61 personal connection | Supported now with temporary retry state | Token management and granted personal-data adapter only |
| Garage61 global-field comparison | Permanently unsupported | Absent from all production surfaces and AI active capabilities |
| Qualifying analysis | Not implemented | Qualifying metadata remains internal; no production tab/session/action |
| Setup comparison | Not implemented | Removed from setup UI |
| Setup package builder | Not implemented | Removed from setup UI and descriptions |
| Setup experiments tab | Not implemented | Removed; working progressive tuning remains a separate contextual workflow |
| Track map | Not implemented | Empty compatibility component removed; supported lap-distance/load-zone output remains |
| Exact target trace | Not implemented | Missing target claims omitted; validated personal pace range is the live fallback |
| Push-to-pass | Not implemented | Absent |
| Weight jacker | Not implemented | Absent |
| Wet-weather analysis | Not implemented | Absent; measured temperature remains conditional |
| Multiclass analysis | Not implemented | Absent; no redundant class controls are created |
| Leader gap | Conditionally applicable | Requires a valid live same-lap scoring interval |
| Gap ahead | Conditionally applicable | Requires a valid live same-lap car ahead |
| Gap behind | Conditionally applicable | Requires a valid live same-lap car behind |
| Personal pace range | Conditionally applicable | Requires at least three clean completed session laps |
| Pit window | Conditionally applicable | Requires both defensible bounds |
| Fuel hard limit | Conditionally applicable | Requires measured live fuel burn |
| Last lap | Conditionally applicable | Requires a completed player lap |
| Leader last lap | Conditionally applicable | Requires a completed leader lap |
| Tire phase | Conditionally applicable | Requires a supported phase rather than an unknown baseline |
| Weather | Conditionally applicable | Requires at least one measured temperature channel |
| Brake bias | Conditionally applicable | Requires the live channel for the current car |
| Repair | Conditionally applicable | Requires a positive recorded repair timer |
| Official filter | Conditionally applicable | Requires an identified official Race event |
| Hosted/league filter | Conditionally applicable | Requires an identified hosted or league Race event |
| AI-event filter | Conditionally applicable | Requires an identified AI Race event |
| Fixed-setup filter | Conditionally applicable | Requires a fixed Race event |
| Open-setup filter | Conditionally applicable | Requires an open Race event |
| Analyzed filter | Conditionally applicable | Requires an analyzed Race event |
| Needs-analysis filter | Conditionally applicable | Requires a finalized Race that has not been analyzed |

## Surface audit

| Surface | Decision |
| --- | --- |
| Navigation | Planning and tuning are contextual; fake setup package wording and tabs removed |
| Home | Workflow cards use the same registry and auto-fit without holes |
| Race browser | Race-only production workflow; filters derive from real event metadata |
| Race Card | Missing internal `[U]` claims are retained in JSON but omitted from the visible card |
| Live telemetry page | Disconnected state contains only recovery guidance and working display settings; every metric card is data-driven |
| Live Monitor | Gap, timing, pit, pace, tire, and weather blocks collapse independently and rebalance |
| Planning | No disabled empty selectors; result card appears only after a request |
| Setups | Read-only library only; setup fields render only when identified |
| Progressive tuning | Fixed and unanalyzed sessions excluded; no empty recommendation card |
| Connections | Repair and reconnect actions are mutually appropriate; no global Garage61 selector |
| Settings | All controls have working persistence/effects; raw-source action is contextual |
| Diagnostics | Missing technical capabilities may remain here for troubleshooting only |
| Tray menu | Pause action appears only during a live connection; telemetry status is a non-actionable temporary-state line |
| Onboarding | Only working folder, ChatGPT, Garage61, verification, and continue actions remain |
| Reports | Unsupported fields and sections are omitted; raw analysis evidence is unchanged |
| AI | Compact evidence contains active capabilities and `setupChangesAllowed`; schema makes contextual sections optional |

## Temporary states retained

- iRacing disconnected: automatic retry plus a clear start/join instruction.
- Garage61 token saved while offline: retrying state plus token replacement/disconnect actions.
- ChatGPT signed out: reconnect action; deterministic Race Card remains usable.
- Coach Engine restarting or damaged: restart state or a single repair action.
- Analysis jobs and archive migration: visible only while the user is waiting for completion.

## Useful fallbacks

- Global Garage61 comparison → comparable personal local history.
- Exact target trace → clean personal pace range or relative load-zone coaching.
- Geographic track map → telemetry-derived lap-distance/load profile.
- AI explanation → deterministic Race Card remains the source of truth.
- Missing raw IBT → archived reports and normalized evidence remain usable; relocation appears only when needed.

## Validation

- 47 .NET capability, context, source-audit, live-pipeline, storage, and coordination tests passed in Release configuration.
- 173 Python report, Race Card, telemetry, workflow, archive, and integration tests passed.
- Handoff verification passed: 109 files, 17 contracts, 14 fixtures, 16 MCP tools, manifest checksums, and MCP end-to-end analysis.
- Native UI QA covered Home, Live telemetry, Race analysis, Setups, Connections, Settings, and the separate WPF Live Monitor. Contextual grids were inspected at the packaged desktop dimensions.
- Installer lifecycle QA passed replacement of a running prior version, simulated rollback, prior-payload removal, durable archive continuity, clean uninstall, reinstall, and second clean uninstall.
- Release installer: `artifacts/dist/iRacingCoach-0.7.0-Setup.exe` (498,599,974 bytes).
- SHA-256: `49fe954fdae0f702f3a9e78fb2b1c74682d28461d52338d079284c5bcd43de69`.

## Before and after evidence

- Before: `artifacts/qa/live-monitor-unavailable-baseline.jpg` shows the former fixed layout containing unavailable values.
- After: `artifacts/qa/v0.7.0-live-monitor-adaptive-after.png` shows the Live Monitor with only supported rows.
- After: `artifacts/qa/v0.7.0-live-adaptive-after.png` shows the capability-driven Live telemetry page.
- After: `artifacts/qa/v0.7.0-analysis-empty-adaptive-after.png` shows a balanced single empty state without an empty preview column or irrelevant filters.
- After: `artifacts/qa/v0.7.0-home-contextual-after.png` shows the contextual navigation and workflow grid without unavailable planning or tuning actions.
