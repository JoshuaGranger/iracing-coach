# Deterministic backend integration

## Runtime and source

Backend root:

`../iracing-coach`

The runtime uses Python 3.10+ and the Python standard library. PowerShell is used for startup and the Windows DPAPI Garage61 credential flow. Do not add a runtime dependency merely to parse IBT metadata; the bundled parser has a tested strict YAML fallback.

Key entry points:

- MCP: `../iracing-coach/skills/analyze-iracing-race/scripts/start-mcp.ps1`
- MCP implementation: `../iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py`
- CLI: `../iracing-coach/skills/analyze-iracing-race/scripts/coach_cli.py`
- Tests: `../iracing-coach/tests`

The production package should place an embedded Python runtime beside the app and set `IRACING_COACH_PYTHON` to its absolute `python.exe` before starting the backend.

## Process configuration

Set these variables only in the backend child process environment:

| Variable | Meaning |
| --- | --- |
| `IRACING_COACH_PYTHON` | Explicit packaged Python 3.10+ executable used by `start-mcp.ps1`. |
| `IRACING_COACH_IRACING_ROOT` | Trusted local iRacing Documents root. All selected source paths must remain descendants. |
| `IRACING_COACH_INSTALL_ROOT` | Optional read-only iRacing installation root. Auto-detects `C:\Program Files (x86)\iRacing` and `C:\Program Files\iRacing` when omitted. |
| `IRACING_COACH_DATA` | Trusted coach archive root. All analysis/package/history paths must remain descendants. |
| `PYTHONUTF8=1` | Force UTF-8 behavior; the launcher also sets this. |

UNC and device paths are intentionally rejected. Freeze the two trusted roots when starting a worker; do not allow a request to broaden them. The checked-in `config/defaults.json` describes Joshua's racing PC, while process variables make development fixtures and installed locations portable.

Development example:

```powershell
$env:IRACING_COACH_PYTHON = "C:\path\to\python.exe"
$env:IRACING_COACH_IRACING_ROOT = "C:\dev\iracing-fixtures"
$env:IRACING_COACH_INSTALL_ROOT = "C:\Program Files (x86)\iRacing"
$env:IRACING_COACH_DATA = "C:\dev\iracing-coach-archive"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File ".\iracing-coach\skills\analyze-iracing-race\scripts\start-mcp.ps1"
```

## MCP framing and lifecycle

The backend is a newline-delimited UTF-8 JSON-RPC 2.0 stdio server. Send exactly one JSON object per line and continuously drain stdout and stderr. It does not use `Content-Length` framing.

Initialize:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"iracing_coach_companion","version":"0.1.0"},"capabilities":{}}}
```

Then call `tools/list` and verify the names/input schemas against `contracts/mcp-tools.v1.json`. Tool invocation:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"iracing_companion_dashboard","arguments":{"limit":20}}}
```

`tools/call` returns MCP content. The domain JSON is serialized inside `result.content[0].text`; parse that second JSON layer and inspect `result.isError`. Protocol-level failures instead use the JSON-RPC `error` object.

Compatibility versions and supported MCP protocols are in `contracts/compatibility.json`. Reject unsupported required versions clearly, while tolerating unknown optional fields.

## Tool groups

- Home: `iracing_companion_dashboard`, `inventory_iracing_data`, `discover_iracing_sessions`.
- Race: `analyze_iracing_race`, `query_iracing_telemetry`, `find_iracing_telemetry_events`, `iracing_strategy_history`.
- Knowledge: `iracing_knowledge_cache_status`, `archive_iracing_knowledge`.
- Setup: `catalog_iracing_setups`, `build_open_setup_package`, `recommend_open_setup_tuning`, `record_open_setup_feedback`, `iracing_setup_history`.
- Garage61: `garage61_auth_status`, `sync_garage61_references`.

Never infer schemas from this prose; use `tools/list` and the checked-in contract snapshot.

## Worker model and cancellation

The current MCP loop executes one call synchronously and cannot receive a cancellation message while that call runs.

Use this release-one coordinator pattern:

- One short-lived or restartable dashboard/diagnostic worker.
- A disposable MCP or CLI worker for each analysis, Garage61 sync, setup package, or tuning operation.
- One in-flight write operation per canonical session/package key.
- Cancellation terminates the worker process tree, marks the job cancelled, then verifies that no partial artifact was advertised.
- A later backend job protocol may add progress/cooperative cancellation, but the UI must not depend on it now.

Use argument arrays and redirected standard streams. Never construct a shell command from user text. Capture bounded stderr, redact secrets, and retain exit `130` as cancellation for CLI workers.

## CLI fallback

The CLI prints JSON to stdout and machine-readable errors to stderr:

```powershell
python -X utf8 .\iracing-coach\skills\analyze-iracing-race\scripts\coach_cli.py dashboard `
  --root "C:\path\to\iRacing" --archive-root "C:\path\to\archive" --limit 20
```

Commands: `dashboard`, `inventory`, `discover`, `analyze`, `telemetry-query`, `telemetry-events`, `auth-status`, `configure-auth`, `garage61-sync`, `cache-status`, `history`, `setup-catalog`, `setup-package`, `setup-recommend`, `setup-feedback`, and `setup-history`.

Never pass a Garage61 token as an argument or environment variable.

## Data and artifact rules

- Render returned `analysis_path`, `report_path`, `race_card_path`, and visual paths. Do not reconstruct archive paths.
- Pin all follow-up operations to the exact discovery `group_id` after resolving `latest` once. A numeric SubSessionID remains backward compatible, but it is ambiguous when Qualifying and Race share that identifier; the phase-qualified `group_id` is authoritative for an opened workspace and its cache.
- Never open SQLite for writes from the UI. Use backend tools.
- Raw iRacing telemetry and setup files are read-only and remain outside the archive.
- Treat the entire iRacing installation root as read-only. The backend may inventory file/version metadata for content and physics fingerprints, but the app must never patch, replace, unpack, or write game files.
- An active/changing IBT is deferred, not partially analyzed.
- The copied private archive contains absolute racing-PC paths. It can be inspected as private regression material, but frontend automated tests must use sanitized fixtures and temporary archives.

## Error handling

Display the actionable backend error and consequence without replacing it with a generic AI message. Required states include missing root, no recorded Race, active file, truncated file, unavailable channel, corrupt archive artifact, fixed setup, damage-confounded tuning, Garage61 unconfigured, Garage61 unauthorized, offline, and cancellation.

Local analysis must continue when Codex, Garage61, or web enrichment is unavailable.
