# Implementation Snapshot: 0.11.0

Status: development candidate. Real telemetry, live-race usefulness, and real clean-run tuning still require direct local validation.

## Material changes from 0.10.0

- The full Live Telemetry page and miniature Live Monitor now use the same named layout, grid, tile definitions, typed telemetry catalog, portable preferences, and live readings.
- Live Telemetry adds a collapsible right-side Toolbox for layout creation, duplication, reset, deletion, grid sizing, tile selection, movement, resizing, display style, unit, precision, removal, search, and metric addition. Driving traces remain available as a subordinate disclosure.
- Opening a recorded event immediately enters its Race session and clears stale analysis. Cached telemetry opens immediately; slower reads show a quiet loader inside the telemetry panel without requiring a Qualifying/Race tab workaround.
- Race Analysis uses a compact event bar, collapsed grade detail, one concise dynamic focus strip, telemetry-first hierarchy, polished flag/best/delta lap rows, map-local color control, and a rendered-coordinate cursor shared by map and charts.
- Static wear/repeatability explanations are removed from primary analysis and planning views. Generic race-plan prose is suppressed; supported changing measurements remain visible. Technical limitations stay in subordinate disclosures.
- The user-facing Setup Library is removed. Setups now contains only the four-stage Starting Tune workflow backed by real indexed setup files.
- Progressive Tuning automatically loads the selected open-setup race, shows its recorded geometry and zones, accepts per-zone feedback, and never manufactures fallback corner zones.
- Connections is embedded in Settings and removed from primary navigation.
- Race Planning uses a compact real-history form, a readable recorded-race selector, and omits repeated generic guidance.

## Verification at snapshot creation

- Release build: zero warnings and zero errors.
- .NET: 74 passed.
- Python: 173 passed with `unittest` discovery.
- Interactive local testing exercised the shared live layout, toolbox collapse/editing, custom tile replacement, miniature-monitor synchronization, immediate race opening, chart cursor alignment, compact analysis, tuning track-zone feedback, controlled recommendation, Settings connections, Starting Tune rail, and streamlined planning output.

Packaged screenshots, artifact hashes, installer lifecycle results, timing, and privacy checks belong in the 0.11.0 release record. Real SDK behavior requires direct validation on the racing PC.
