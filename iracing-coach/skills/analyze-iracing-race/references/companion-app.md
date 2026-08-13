# Companion app integration contract

Use this reference when building a Windows companion UI over the iRacing Coach backend. Keep deterministic telemetry math, archive writes, and credentials behind the existing MCP/CLI boundary; use the AI layer for synthesis, research, coaching language, and workflow guidance.

## Product workflows

| Workflow | User experience | Backend sequence | Durable state |
| --- | --- | --- | --- |
| Race planning | Select an upcoming car/track/race; review fuel range, cautions, pit windows, prior performance, track knowledge, and coaching targets. | `iracing_companion_dashboard` -> `iracing_knowledge_cache_status` -> `iracing_strategy_history`; research and `archive_iracing_knowledge` only when the seasonal bundle is not fresh. Add Garage61 after its official API is ready. | Seasonal knowledge bundle plus race-length strategy history. |
| Race analysis | Select a recent race or accept the latest Race default; show a concise Race Card first, then optional summary, runs, tires, fuel, incidents, tow/repair time, service, charts, and diagnostics. | `discover_iracing_sessions` when selection is ambiguous -> `analyze_iracing_race` with the pinned session -> render returned `race_card` immediately -> optional `find_iracing_telemetry_events` or bounded `query_iracing_telemetry` calls for a specific unresolved question. | Archived `race-card.md`, `analysis.json`, full Markdown report, visuals, fingerprints, and historical run rows. |
| Create a starting tune | Select season, car, exact track layout, and available source setup; review a race/Q starting package. | `catalog_iracing_setups` -> `build_open_setup_package`, with an analysis path when available. | Fingerprinted baseline package and provenance. Source STO/HTML files remain read-only. |
| Progressive tuning | Enter an entry/center/exit symptom after a run; receive one controlled change, expected effect, risk, success criteria, and rollback; record the result. | Analyze the finalized run -> `iracing_setup_history` -> `recommend_open_setup_tuning` -> user applies/saves the change in iRacing -> analyze the result -> `record_open_setup_feedback`. | Experiment state, setup fingerprints, driver wording, result analysis, outcome, and rollback history. |

The dashboard is the common entry point. Its current `contract_version` is `1`; it returns recent Race sessions, analysis status, `race_card_path`/`race_card_available`, full artifact paths, tuning packages, Garage61 local readiness, and capability flags. The UI must validate the contract version, tolerate new optional fields, and fail clearly when a required field is absent.

## Architecture

Keep four boundaries:

1. **Companion UI:** navigation, session selection, forms, charts, notifications, and workflow state. Do not parse IBTs, calculate strategy, or hold service credentials here.
2. **Local coordinator:** start and supervise backend/AI processes, queue long-running jobs, map structured results into view models, and persist UI-only state. Invoke commands with argument arrays rather than shell-built strings.
3. **Deterministic coach backend:** the existing Python MCP/CLI surface owns discovery, IBT decoding, analysis, archives, setup history, and Garage61 API calls.
4. **AI orchestration:** Codex or another approved agent consumes compact evidence and calls the backend for additional bounded data. For a rich Windows client, prefer Codex app-server over local stdio so authentication, persistent threads, streamed events, structured output, and interruption remain behind a supported programmatic boundary. Do not send an entire raw race to the model when a profile, event search, or slice answers the question.

For the initial post-race app, launch the MCP server as a child process over stdio. A listening local HTTP service is unnecessary. Run the app in Joshua's logged-on Windows session so it can use user-bound credentials and, later, iRacing shared memory. Keep local analysis on the critical path and AI synthesis off it: the backend should normally return the deterministic Race Card in seconds, the UI should display it immediately, and any optional AI refinement should update the card asynchronously without hiding or replacing its evidence labels.

Semi-live tuning is a later sidecar capability. That sidecar should read IRSDK shared memory, detect run/lap boundaries, and update live charts locally. Invoke AI analysis at a completed lap block, pit stop, finalized IBT, or explicit user request rather than streaming raw high-rate samples continuously. Never analyze or archive an IBT while it is still changing.

The sidecar may display live tow and repair countdowns locally, but durable analysis still waits for a finalized IBT. After service completes, recompute run eligibility before offering a setup change or personalized target trace.

### Racing PC

- Run the installed companion UI, local coordinator, bundled backend runtime, and optional Codex background process.
- Read iRacing data from the configured Documents root; write only to the configured coach archive and app-owned logs/settings.
- Keep Garage61 credentials encrypted for the logged-on Windows user.
- Require no development toolchain when the app is deployed as a self-contained package.
- Make startup, session detection, and background work visible without opening a console window.

### Development PC

- Build the frontend/coordinator, run unit and integration tests, and produce versioned deployment artifacts.
- Use synthetic/sanitized IBTs and temporary archives; do not copy Joshua's DPAPI credential or browser profile from the racing PC.
- Package the compatible Python backend, dependencies, skill resources, and frontend together, or declare and validate an existing runtime during installation.
- Publish checksums and a rollbackable package. Preserve the racing PC's archive and configuration during upgrades.

Avoid coupling the product contract to one UI framework or an exact runtime version. Choose a supported Windows desktop stack, pin build dependencies in the project, and make the shipped runtime self-contained where practical.

## Backend contracts

### MCP: primary app integration

`scripts/mcp_server.py` is a local stdio JSON-RPC server. It exposes bounded domain operations and intentionally exposes no generic filesystem or shell tool. Group the current tools as follows:

- **Home and selection:** `iracing_companion_dashboard`, `inventory_iracing_data`, `discover_iracing_sessions`.
- **Race evidence:** `analyze_iracing_race`, `query_iracing_telemetry`, `find_iracing_telemetry_events`, `iracing_strategy_history`.
- **Seasonal knowledge:** `iracing_knowledge_cache_status`, `archive_iracing_knowledge`.
- **Setup work:** `catalog_iracing_setups`, `build_open_setup_package`, `recommend_open_setup_tuning`, `record_open_setup_feedback`, `iracing_setup_history`.
- **Garage61:** `garage61_auth_status`, `sync_garage61_references`.

Treat tool results as authoritative structured data. For `analyze_iracing_race`, render the inline `race_card.markdown` first and retain its `path`, `timing`, evidence tags, phase model, and omitted-row count. Render `analysis_path`, `race_card_path`, `report_path`, and visual artifact paths returned by the backend; do not reconstruct archive paths in the UI. After resolving `latest`, pin subsequent calls to the exact session/source returned by discovery or analysis. For an "important transients" action, call `find_iracing_telemetry_events` with `selection_mode: "severity"` and display its scan/candidate/omission metadata; use chronological mode for a user-selected lap or record window.

The default Race Card is local-first and bounded. It must show the user's observed corner entry/minimum/exit speeds, braking, turn-in, and phase behavior only when the underlying per-metric sample gate passes. Fresh/settled/worn labels require supported phase evidence; otherwise show early/middle/late or an older-set proxy with exact green-lap-on-set bounds and an explicit limitation. Groove migration may be directional only after geometry calibration; never turn path movement into a "best groove" claim without a controlled performance comparison. Exact coaching targets require a usable aligned representative-lap comparison.

Treat `damage_repair` as part of the primary race contract. The UI should provide a collapsible incident/repair panel and annotate the race timeline and lap chart. Show pit-road transit, stall occupancy, service-active time, mandatory repair, optional repair completed/remaining, tow countdown/elapsed time, and confirmed fast-repair use as separate clocks. Clearly state that intervals may overlap and that incident points do not prove damage. Shade repair/tow laps and repair-correlated candidate windows; exclude them from the clean pace trend, Lap Plan targets, setup comparisons, and ordinary pit-loss history by default, with a diagnostic include toggle.

For a pit visit, show a waterfall-style time breakdown only where intervals are non-overlapping. Otherwise use aligned timeline bars so simultaneous tires, fuel, and repair are visible without double-counting. If optional repair remained when the car left, carry a visible "repair remaining" badge onto the following run. If channels are absent, display unavailable; if they are present and remain zero, display no recorded tow/repair workload.

Use the returned timing fields to make latency visible in diagnostics. Product acceptance targets are: cached Race Card in well under one minute, uncached post-race analysis and ordinary planning/tuning within two minutes end-to-end, and starting-package research within four minutes. A time budget is not permission to invent an answer: when evidence is absent, render an unavailable field and allow the user to request a deeper background investigation.

Run analysis, native event search, Garage61 sync, and package generation as cancellable background jobs in the UI. Prevent duplicate writes for the same in-flight operation. Persist workflow state in app storage and backend artifacts rather than relying on a chat thread as the database.

### CLI: fallback and diagnostics

`scripts/coach_cli.py` provides the same domain workflows as JSON-producing commands:

`dashboard`, `inventory`, `discover`, `analyze`, `telemetry-query`, `telemetry-events`, `auth-status`, `configure-auth`, `garage61-sync`, `cache-status`, `history`, `setup-catalog`, `setup-package`, `setup-recommend`, `setup-feedback`, and `setup-history`.

Use the CLI for installer diagnostics, smoke tests, or when MCP hosting is unavailable. Parse stdout as JSON. Treat a nonzero exit and JSON stderr as a failed operation; exit `130` means cancellation. Never pass a token on the command line. Prefer MCP for the interactive app so individual operations remain explicit and typed.

## Local data and authentication boundaries

- Default source root: `C:\Users\joshu\Documents\iRacing`. Allow an explicit local descendant; reject UNC/device paths at the backend boundary.
- Default read-only install root: `C:\Program Files (x86)\iRacing`, with `C:\Program Files\iRacing` as a fallback. Allow `IRACING_COACH_INSTALL_ROOT` for portable discovery. Inventory and read useful version/content metadata only; never patch, unpack, replace, or write simulator files.
- Default archive root: `C:\Users\joshu\Documents\iRacing Coach\data`. The backend owns its schema and writes; the UI reads returned contracts and must not mutate archive internals directly.
- Raw IBTs, replays, STOs, and HTML exports are source artifacts and remain read-only. For each accepted finalized IBT, the companion app additionally creates a verified content-addressed durable copy under its portable Documents home; it never mutates the source, substitutes a hard link for durable content, or copies `.rpy`, STO, or HTML artifacts merely because they are adjacent.
- A simulator-loadable STO is never generated or overwritten. The user applies and saves setup changes in iRacing.
- Store the Garage61 PAT only through the backend's Windows user-bound secure store. The frontend may receive readiness, permission, and error status, never the secret.
- A signed-in browser session is an optional interactive fallback, not durable API authentication. Never inspect cookies, password stores, or Garage61 Agent internal state.
- Redact authorization headers and secrets from logs, crash reports, analytics, and AI context. Keep the app useful offline; local race analysis and setup history must not depend on web availability.

## Deployment and validation

On the development PC:

1. Run the Python unit suite and contract-focused tests for MCP, CLI, telemetry, storage, reporting, setup workflows, and Garage61 adapters.
2. Run frontend unit tests against recorded contract fixtures and an end-to-end smoke test against a temporary backend/archive.
3. Test large-IBT behavior, active-file deferral, cancellation, offline mode, corrupt input, credential redaction, and upgrade/rollback.
4. Test missing repair channels, all-zero repair channels, incident-only sessions, mandatory and optional repair overlap, leaving with optional repair remaining, towing, fast-repair request without use, and confirmed fast-repair use.
5. Build a self-contained Windows package with pinned dependencies and a versioned backend/contract compatibility declaration.

The workspace-level `companion-app-contract/` package is the permanent product/build contract. Its checked-in MCP tool snapshot, partial forward-compatible output schemas, sanitized fixtures, compatibility manifest, and verifier accompany this reference; do not substitute the older installed-plugin cache.

For portable development and deployment, set `IRACING_COACH_PYTHON`, `IRACING_COACH_IRACING_ROOT`, optional `IRACING_COACH_INSTALL_ROOT`, `IRACING_COACH_DATA`, and `PYTHONUTF8=1` only in the backend child-process environment. The Documents and archive roots freeze the trusted source/archive boundaries for that process; the install root is independently read-only. UNC/device paths and descendants outside the configured boundaries remain rejected. Package a Python 3.10+ runtime and preserve the plugin directory structure.

On the racing PC after install or upgrade:

1. Run `dashboard` and `inventory` health checks.
2. Verify the configured source/archive roots and that source telemetry/setup files remain unchanged.
3. Analyze a known finalized Race and open its archived report/visuals.
4. Exercise one setup-package and progressive-tuning cycle against test data before relying on it during a live week.
5. Run `auth-status` only after Garage61 approval and local PAT configuration.

Release acceptance should cover all four workflows, app restart/resume, a missing network, a missing Garage61 credential, and an IBT that is still recording. Keep migration code backward-compatible with existing archive artifacts or provide a non-destructive rebuild path from the original IBTs.

## Garage61 deferred integration

The official API application is pending approval. Do not block dashboard, planning from local history, race analysis, starting packages, or progressive tuning while waiting.

Until approval:

- show the dashboard's local request/credential status;
- reuse authorized manual exports or browser-selected data only when explicitly obtained;
- label the comparison scope and do not present it as the full public field;
- avoid repeated automatic API retries.

After approval:

1. Configure the PAT on the racing PC through the secure no-echo flow.
2. Call `garage61_auth_status` and display granted permissions without exposing the token.
3. Enable `sync_garage61_references` for missing/stale seasonal components and explicit refreshes.
4. Keep `global_visible_laps_approved` false unless Garage61 separately grants that capability.
5. Cache authorized comparison metadata and telemetry by season/car/exact layout/setup cohort, and preserve local analysis when Garage61 is unavailable.
