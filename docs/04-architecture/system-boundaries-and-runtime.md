# System Boundaries and Runtime

## Runtime shape

The released application is a per-user Windows desktop product. A native WPF host owns process lifecycle, title-bar behavior, iconography, error containment, live-monitor windows, and a WebView-based Blazor UI. The deterministic Python coach engine runs as a local child process behind an MCP/JSON protocol. Internet integrations are optional and may not block local analysis.

| ID | Requirement |
| --- | --- |
| `ARCH-001` | The normal user experience MUST start from one installed app entry without requiring a terminal, Python installation, repository checkout, or manual service startup. |
| `ARCH-002` | The desktop host MUST supervise the bundled backend, detect startup failure, and present actionable recovery without exposing a console window. |
| `ARCH-003` | Local analysis MUST remain usable when Garage61, OpenAI/Codex, or the internet is unavailable. |
| `ARCH-004` | Contracts between UI, coordinator, and backend MUST be versioned and validated at startup or request boundaries. |
| `ARCH-005` | A backend or mapping exception MUST be contained to a recoverable product state where practical. |
| `ARCH-006` | Long-running import, analysis, archive, or AI work MUST remain cancelable or visibly bounded and MUST NOT freeze navigation. |
| `ARCH-007` | Background refresh MUST be event-driven or bounded; the UI MUST NOT expose distracting success-status churn. |
| `ARCH-008` | Development fixtures and preview hosts MUST be impossible to mistake for production data or acceptance. |

## Project responsibilities

| Component | Responsibility |
| --- | --- |
| `iRacingCoach.App` | Windows host, startup, window chrome, WebView, live monitor, fixture launch controls |
| `iRacingCoach.UI` | User workflows, layout, state presentation, accessibility, charts/maps |
| `iRacingCoach.Contracts` | Stable models crossing component boundaries |
| `iRacingCoach.BackendClient` | MCP transport and backend request handling |
| `iRacingCoach.Coordinator` | App state, mapping, archive, settings, credentials, engine and live-source coordination |
| `iRacingCoach.Installer` / `Uninstaller` | Per-user lifecycle and durable-data preservation |
| `iracing-coach` | IBT parsing, deterministic analysis, reporting, setup/tuning workflows, Garage61 adapter, MCP server |

## Process and failure contract

| ID | Requirement |
| --- | --- |
| `PROC-001` | At most one managed backend instance SHOULD serve one app instance unless a documented multi-instance mode exists. |
| `PROC-002` | Process shutdown MUST terminate supervised children without deleting durable data. |
| `PROC-003` | Protocol timeouts and malformed responses MUST produce redacted support details and a retry/restart path. |
| `PROC-004` | Logs MUST correlate app, backend, job, event, and request identifiers without containing secrets. |
| `PROC-005` | The app MUST distinguish not installed, not configured, disconnected, unavailable, degraded, and failed states when their remedies differ. |

Implementation references: `App.xaml.cs`, `StartupRegistration.cs`, `CoachEngine.cs`, `McpBackendClient.cs`, `RuntimeMapper.cs`, and the MCP contracts under `contracts/`.
