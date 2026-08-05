# Race Analysis

Race Analysis is the deepest workflow. It must let the user choose an event, open it directly, and inspect supported evidence without an extra chat or preview step.

## Race browser

| ID | Requirement |
| --- | --- |
| `RA-001` | The base page MUST list finalized Race events newest first. |
| `RA-002` | Reconnected/split recordings from one SubSessionID/session MUST group into one event. |
| `RA-003` | Qualifying and Race recordings from the same event MUST group under one event. |
| `RA-004` | The complete event row MUST perform one action: open that event's full analysis. |
| `RA-005` | The browser MUST NOT contain a permanent preview pane, detached Race Review, nested child-session buttons, or a second “open full analysis” step. |
| `RA-006` | Each row MUST use friendly available facts: local time, track/layout, car, series/scope, setup type, result, distance, and analysis state. |
| `RA-007` | Search and filters MUST operate only on recorded/indexed fields and MUST adapt when a category is absent. |
| `RA-008` | Cached analysis SHOULD open effectively immediately; reanalysis MUST be explicit. |
| `RA-009` | Active, changing, zero-record, truncated, or trailing-partial IBTs MUST be deferred without advertising an artifact. |
| `RA-010` | Selecting an event MUST immediately open its Race telemetry when present. Slower reads MUST show a quiet loading state inside the telemetry panel; the user must not toggle another session to trigger loading. |

## Workspace hierarchy

| ID | Requirement |
| --- | --- |
| `RA-020` | The workspace MUST lead with one compact event bar containing friendly identity, recorded counts, an unclipped phase selector when applicable, and strict grade. It SHOULD consume roughly one-tenth of the usable height. |
| `RA-021` | Only recorded, openable phases may appear. Nonexistent phases and roadmap controls MUST be absent. |
| `RA-022` | Switching Qualifying/Race MUST remain inside the event and preserve separate setup, conditions, laps, and conclusions. |
| `RA-023` | Race sessions MUST place a `Telemetry` / `Race review` segmented selector directly below Grades. `Telemetry` MUST be the default and contain the complete visual lap, map, and chart workbench without summary cards beside it. `Race review` MUST contain the fastest-lap takeaway plus the race-specific shape, strategy, corner, pit, and repair findings. Qualifying MUST retain its current combined workspace until it receives a separate specification. |
| `RA-024` | Each active Race section MUST favor one-screen use at a typical desktop width. An insight card MUST contain useful race-specific evidence or be contextually absent; a capability word, invariant explanation, or empty setup surface is never valid content. |
| `RA-025` | A mapping/backend/render failure MUST be contained and provide Retry plus Copy Support Details without closing the app. |

## Vertical Runs/Laps rail

| ID | Requirement |
| --- | --- |
| `RA-030` | The rail MUST be vertical, independently scrollable, and left of the map/charts. |
| `RA-031` | Every row MUST use stable columns in this order: recorded flags, lap number with trace color, pace with inline delta, sectors, measured lap fuel use, incidents, and pit direction. The flag and lap-number columns MUST remain compact without artificial whitespace. The fastest clean time MUST be visibly magenta; other supported deltas MUST appear inline in parentheses after the lap time rather than consuming a second line. |
| `RA-032` | Rows MUST expose selection color/control and available green, caution, black, white, checkered, pit, incomplete, off-track, incident, service, repair, or tow state with text/icon as well as color. Multiple sampled flag states MUST remain visible together and right-aligned in the first column. Pit state MUST distinguish recorded entry and exit as `PIT (in)` and `PIT (out)`. Flag, pit, and incident states MUST remain independently visible when they overlap. |
| `RA-033` | The rail MUST support unlimited multi-select, Select all, Clear, and clean/green filtering. Enabling the clean filter MUST remove dirty laps from both the rail and the active trace selection. Clear MUST render an empty telemetry state until another selection is made. Its initial comparison SHOULD choose a small useful set rather than every lap. |
| `RA-034` | Lap zero, partial parade, and otherwise invalid laps MUST NOT enter clean pace or reference selection. |
| `RA-035` | Tire age, fuel, incident, repair, and fastest/sector markers MUST appear only when supported. |
| `RA-036` | The rail MUST integrate run boundaries, flag mix, pace, fuel, and measured tire context. Dense secondary run detail MAY use one keyboard-accessible popover rather than a separate page section. The pit popup MUST open only from the PIT badge and float outside the rail beside the pointer: its top-left corner anchors below the pointer, or its bottom-left corner anchors above it when downward space is insufficient. It MUST show only supported service facts such as confirmed changed tires, recorded per-corner wear, recorded fuel added, service time, estimated fuel range/sufficiency, race laps remaining, damage-repair time, and penalty-service time; an unavailable field MUST NOT be guessed. |
| `RA-037` | Laps MUST remain in chronological start-to-finish order. Run headers and horizontal separators MUST show run boundaries without moving unassigned caution, pit, incomplete, or incident laps into an “Other laps” bucket. |
| `RA-038` | Per-lap trace colors MUST progress continuously from red through orange, yellow, green, blue, indigo, and violet across the full recorded lap count; spacing between colors MUST be derived from that count. Any lap tied for the fastest clean time is the exception and MUST use the same magenta as its fastest-lap time text everywhere that lap color appears. |
| `RA-039` | When trace timing and the session's actual sector definitions support it, each row SHOULD show sector markers derived from recorded session time at those boundaries. A chronological new best is green and the final session-fastest sector is magenta. Sector tooltips MUST contain only the sector identifier and recorded time; color carries the performance state without repeating explanatory suffixes. The UI MUST NOT divide a lap into manufactured equal sectors; unsupported sectors are omitted. |
| `RA-040` | A run excluded from coaching comparison MUST still show its directly observed green-lap pace range when usable lap times exist, while labeling the coaching comparison excluded and retaining the exclusion reason. |
| `RA-040A` | In the desktop three-column workbench, the aligned-traces panel MUST define the shared bottom edge. The lap rail and track panel MUST stretch to that edge, while the lap rail scrolls internally rather than growing to the height of all lap rows. Per-lap fuel MUST be shown only from the recorded negative fuel-level change accumulated during that lap; unavailable values remain unavailable. |
| `RA-040B` | Hovering a lap time MUST show a floating per-lap conditions card beside the pointer. It SHOULD include recorded sky state, track and air temperature, wind speed/direction, humidity, fog, pressure, air density, precipitation, and the inherited session track-usage state when supported. Values MUST come from samples within that lap or explicit session metadata; missing fields remain unavailable and MUST NOT be inferred. |
| `RA-040C` | Every lap row MUST use one stable seven-column alignment for flags, lap identity/color, pace/delta, sectors, fuel, incidents, and pit state. Columns MUST NOT move when optional values are absent. Up to three simultaneous flags, a long pace delta, three sector markers, fuel, an incident badge, and either pit-direction badge MUST fit without collision, clipping, wrapping, or horizontal scrolling at supported desktop widths. The pace delta MUST sit below the time with their right edges aligned. |

## Map and telemetry

| ID | Requirement |
| --- | --- |
| `RA-040` | The map MUST use recorded position geometry; when absent, use a normalized distance strip rather than invented geometry. |
| `RA-041` | Map and charts MUST share one bidirectional aligned-distance cursor. Between recorded geometry samples, the marker MUST interpolate on the recorded polyline instead of snapping off the rendered curve. |
| `RA-042` | Multiple selected laps MUST use consistent per-lap trace colors across the rail, cursor legends, and every comparison chart. The aggregate track map uses metric-strength color rather than a per-lap color. |
| `RA-043` | Required distance-aligned channels are Speed, Time Delta, Throttle, Brake, Gear, RPM, and Steering. |
| `RA-044` | RPM MUST have an independent readable scale and MUST vary when the recorded source varies. |
| `RA-045` | Slip Angle, Yaw Rate, Lateral Acceleration, and Longitudinal Acceleration SHOULD appear when the source channels exist. |
| `RA-046` | Each channel MUST use one shared scale across selected laps; per-lap normalization is forbidden. |
| `RA-047` | Screen drawing MUST preserve short extrema through min/max-aware downsampling. |
| `RA-048` | The workspace MUST support focus/hide, legends with units, zoom/reset, and keyboard-accessible selection. |
| `RA-049` | A target/reference trace MUST identify provenance, scope, scenario, and confidence and MUST require a usable aligned comparison. |
| `RA-050` | Map color controls MUST sit with the map, identify that they depict the average of all currently selected laps, and never appear to control an adjacent chart. Speed, throttle, brake, steering, and tire-load values at each aligned track position MUST be averaged across those laps; the cursor readout MUST use the same aggregate scope. |
| `RA-050A` | Every map metric MUST use a low-to-high strength gradient with a distinct, readable family: blue/cyan for speed, green for throttle, red for brake, purple for steering magnitude, and yellow-to-red for tire load. Missing and effectively zero control/load values MUST remain visually neutral. |
| `RA-051` | The crosshair MUST derive from the rendered chart bounds so Windows scaling and responsive layout cannot offset the pointer from the inspected sample. |
| `RA-052` | Each chart row MUST show a shared-cursor value for every selected lap, using the compact bare lap number plus the same color marker as the corresponding trace. The repeated word “Lap” MUST be omitted from cursor rows. |
| `RA-053` | Steering MUST be presented as human-readable left/right degrees. Analog display traces MAY use slight documented smoothing, but it MUST preserve braking, throttle, and steering events and MUST NOT alter analysis calculations. |
| `RA-054` | Map rendering MUST preserve the recorded driving direction after conversion into screen coordinates. Zero brake MUST render as neutral track color; increasing brake input MUST increase, not decrease, brake emphasis. |
| `RA-055` | Pointer conversion MUST use the SVG's rendered screen transform, including view-box scaling and letterboxing. The map SHOULD use the available panel height while preserving track shape rather than leaving a large unused area below a miniature map. |
| `RA-056` | Trace rows SHOULD use the available analysis-panel height. SVG chart coordinates MUST track the rendered chart width so labels retain normal proportions under responsive layout. The telemetry workbench MUST respond to its available content width—not only the outer window width—by compacting and proportionally scaling the complete laps/map/charts workbench so all three remain visible together without horizontal page overflow. |
| `RA-057` | Cursor values SHOULD open left of the crosshair when space permits. Each chart's cursor card MUST size itself to that chart's widest currently displayed value instead of sharing a fixed width. Each row MUST show only the selected-lap values that fit without overlapping adjacent rows; while the pointer is over the chart, the wheel MUST page through remaining selected laps without scrolling the surrounding page. |

## Integrated insight contracts

### Race shape

- `RA-060`: MUST summarize result, totals, grades, and at most the changing useful priorities without repeating a detached Race Card or static methodology prose.

### Corner comparison

- `RA-070`: MUST include every supported material recorded track area. Internal segmentation terms such as `load zone` MUST NOT be exposed as the primary user label.
- `RA-071`: SHOULD separate early/middle/late phase and entry/center/exit where evidence supports it.
- `RA-072`: SHOULD show entry speed, brake onset/peak/release, minimum speed, steering work, throttle pickup, exit speed, and tire-management instruction when supported.
- `RA-073`: Exact targets and groove direction MUST obey comparison and geometry gates; relative coaching remains acceptable when exact targets are unavailable.
- `RA-074`: Corner zones MUST come from recorded/derived session segmentation. The UI MUST NOT manufacture generic corner names when segmentation is absent.

### Runs and tires

- `RA-080`: MUST define runs from confirmed service boundaries, not file boundaries or pit-road crossing alone.
- `RA-081`: MUST show available lap bounds, flag mix, pace/falloff, fuel, tire endpoint, service, and clean/confounded status.
- `RA-082`: Measured tire remaining at a confirmed service belongs to the preceding run.
- `RA-083`: Final-run wear MUST remain unmeasured when no service observation exists.
- `RA-084`: Blank headings/columns are forbidden; omit unsupported fields.

### Fuel & Strategy

- `RA-090`: MUST show supported green/caution burn, observed consumption, range, reserve, required stops, pit targets, and recorded stop context.
- `RA-091`: MUST call deterministic output fuel feasibility unless position, pit-loss, rules, cautions, overtime, and history justify stronger optimization language.
- `RA-092`: A page MUST NOT present contradictory range availability and exact range claims without a traceable distinction.

### Pit and repairs

- `RA-100`: MUST separate incident points, pit road, stall, service, tow, mandatory repair, optional repair, and fast-repair confirmation.
- `RA-101`: Overlapping clocks MUST be parallel and MUST NOT be added into repair-exclusive loss.
- `RA-102`: Zero recorded repair/tow channels may support “No recorded tow/repair workload”; absent channels may not.
- `RA-103`: Repair/tow/candidate laps MUST be excluded from ordinary clean trends, targets, setup comparisons, and pit-loss learning by default.

### Setup and evidence

- `RA-110`: Race Analysis MUST NOT show a setup panel unless the selected recording contains useful, readable setup values that materially change the analysis.
- `RA-111`: Evidence coverage, exclusions, provenance, and technical identifiers belong in diagnostics or a subordinate disclosure, not the primary workspace.
- `RA-112`: Repeated limitations that are invariant across races belong in a tooltip or documentation, not a primary card.

## Grading

Grades use `A+` through `F` and evaluate Pace & Corner Execution, Consistency & Smoothness, Tire Management, Pit & Fuel Strategy, and Racecraft & Incident Avoidance. A+ requires unusually complete, high-confidence execution near a defensible strong 3k-iRating standard. Unavailable categories require an explicit rubric decision; they may not silently receive a neutral score.

The current fixture mapping exposes only a subset of category grades in some sanitized scenarios. Full rubric calibration and real-field validation remain a product/test gap.
