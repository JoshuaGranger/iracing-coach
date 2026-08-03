# Sanitized fixture catalog

Every fixture in this directory is synthetic and contains no real driver/account ID, purchased setup, credential, or raw race recording.

| Fixture | Purpose |
| --- | --- |
| `dashboard-empty.json` | First-run/no-recorded-race UI. |
| `dashboard-populated.json` | Exact `iracing_companion_dashboard` domain shape with recent Race sessions, archived-analysis links, capabilities, Garage61 readiness, and setup-package index. Damage badges are not currently returned by this tool. |
| `discovery.json` | Grouped/reconnected Race plus malformed-file diagnostic. |
| `analyze-repair-heavy.json` | Complete analyze-result shell with race-window-scoped tow/repair evidence, distinct timer-positive/countdown-progress/countdown-reduction measures, remaining optional repair, request-vs-confirmed fast repair, strategy, timings, and Race Card. |
| `race-card.json` | Standalone deterministic Race Card view. |
| `track-phase-visualization.json` | Calibrated synthetic UI case with synchronized observed and best-supported target traces, explicit phase provenance, magnitude-only steering, evidence-gated directional groove labels, and non-additive interruption spans. It is visibly watermarked and is not driving advice. |
| `track-phase-visualization-unavailable.json` | Required no-geometry/no-comparison state: normalized distance strip, one observed phase, no target traces, no directional groove, and no exact steering-angle claim. |
| `telemetry-events.json` | Exact `find_iracing_telemetry_events` domain shape for a severity-mode result; scan and omission metadata are nested under `summary`. |
| `setup-package.json` | Exact `build_open_setup_package` domain result for a provisional donor baseline. |
| `setup-recommendation.json` | Exact persisted `recommend_open_setup_tuning` domain result containing a one-change controlled plan. |
| `setup-recommendation-damage-blocked.json` | Exact non-persisted tuning result after material repair context. |
| `garage61-auth-status-states.json` | Exact `garage61_auth_status` domain shapes for unconfigured, pending, unavailable/offline, and permission-error states. |
| `ui-job-states.json` | **Companion UI projection only**, explicitly marked `fixture_kind: companion-ui-projection`; it is not an MCP response. |
| `mcp-tool-error.json` | MCP `tools/call` nested domain-error framing. |
| `ibt/synthetic-race.ibt` | Tiny valid Race IBT for real MCP scan, dashboard, query, and deliberately low-evidence full-analysis smoke tests. |
| `ibt/truncated-race.ibt` | One-byte-truncated IBT for rejection tests. |

Regenerate them from the workspace root with `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\companion-app-handoff\scripts\generate-fixtures.ps1`. Golden frontend snapshots should be derived from these files, not from the private `data/` directory.

Both visualization fixtures conform to `contracts/track-phase-visualization-v1.schema.json`. A usable comparison authorizes only a **Best-supported target**, never an unqualified optimal-lap claim. When interpolation, signed/normalized steering, inside/outside calibration, or comparison evidence is unavailable, the corresponding UI control or claim must remain unavailable.

Except for the two track-visualization projections and `ui-job-states.json`, JSON fixtures in this catalog model current backend domain results or MCP framing. The UI may derive additional view fields, but must not treat projection-only fields as backend fields.

Run `python -X utf8 .\companion-app-handoff\scripts\mcp_e2e_smoke.py` to start the real stdio backend with `fixtures/ibt` as its trusted source root and a temporary archive, then execute a dashboard call and complete analysis of `synthetic-race.ibt`. It requires no private telemetry.
