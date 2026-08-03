# Live Telemetry and Live Monitor

All high-rate live math is local. Live support must remain useful without AI and must never treat a changing IBT as finalized archive evidence.

## Live Telemetry page

| ID | Requirement |
| --- | --- |
| `LT-001` | The page MUST reconnect automatically to iRacing SDK shared memory in the logged-on Windows session. |
| `LT-002` | Connected state SHOULD show track position, position/class position, lap timing/trend, physical gaps, controls, gear/RPM, dynamics, fuel, temperatures, and supported race state. |
| `LT-003` | High-rate values, rolling traces, gaps, pace range, fuel range, and cues MUST be computed locally. |
| `LT-004` | AI MUST NOT be called per sample, frame, or render. |
| `LT-005` | Physical time gaps MUST remain distinct from pace comparison. |
| `LT-006` | Pace/input cues require a clean personal baseline; repair-confounded laps cannot create them. |
| `LT-007` | Ordinary cue changes MUST obey safe-glance gating on straights, caution, pit road, or lap completion. Critical warnings MAY bypass the gate. |
| `LT-008` | Fuel hard limit MUST remain distinct from a strategic pit window. |
| `LT-009` | Disconnection MUST clear session-specific baselines and stale actionable cues. |
| `LT-010` | Disconnected state MUST retain a professional structural preview and one clear waiting explanation. |
| `LT-011` | A deterministic replay interface MUST exercise connected behavior without iRacing or private telemetry. |

## Live Monitor

| ID | Requirement |
| --- | --- |
| `LM-001` | Live Monitor MUST be a distinct movable, resizable, always-on-top window. |
| `LM-002` | It MUST show only glanceable priorities: position, last lap/delta, leader gap/lap, pace, fuel/pit, temperature, load/tire warning, and urgent caution/pit/repair state when supported. |
| `LM-003` | Disconnected and replay states MUST be visibly distinguishable. |
| `LM-004` | Monitor position/size MUST be machine-local, while logical visibility/preferences may be portable. |
| `LM-005` | Opening/hiding the monitor MUST change the actual window, not only an internal boolean. |

## Tray and shutdown

| ID | Requirement |
| --- | --- |
| `TRAY-001` | The app SHOULD support minimize-to-tray and configurable close-to-tray. |
| `TRAY-002` | The tray menu SHOULD expose Show App, Show/Hide Live Monitor, connection state, and Exit. |
| `TRAY-003` | Exit MUST terminate live SDK, backend, and optional Coach Engine workers cleanly. |

## Current evidence

Version 0.9.3 includes an SDK source, deterministic JSON replay, safe-glance logic, rolling gap/pace/fuel models, a distinct WPF monitor, and fixture coverage. Replay exercised three green laps plus a caution. Real simulator timing, tray behavior, multi-monitor ergonomics, and sustained live resource use remain HOME_QA work.
