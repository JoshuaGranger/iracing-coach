# Race Analysis Overhaul Acceptance Matrix

This matrix is the binding acceptance plan for the vector-map, global-layout, Technical data, tire/capability, raw-retention, and Race replay round. It supplements `QA-001` through `QA-014`; it does not convert source inspection, a fixture, or an automated test into visual or real-telemetry acceptance.

## Required environments

Every visual row is exercised in the exact development executable at 1280x720 and 1920x1080, both maximized and nonmaximized. Repeat motion rows with ordinary motion and reduced motion. Use supported dense, sparse, and unavailable recordings; identify fixture versus personal/real recording in the evidence without copying raw personal telemetry into source control or the evidence packet.

## Changed-surface matrix

| Area | Required actions and states | Automated evidence | Direct visual/interaction evidence |
| --- | --- | --- | --- |
| Race header | Open Race; switch Telemetry, Technical data, and Race replay in both directions; use Back; switch Qualifying/Race where both exist | Tab identity/default/state tests; no legacy `Race review` action | Every label/control fits; one-row header does not move, clip, or create page scroll |
| Vector map | Open each `Type`: Traces, Speed, Throttle, Brake, Tire load; select one/many/no laps; inspect available/missing main, pit, entry, exit, commitment, merge, and S/F layers; open each legend state | Exact configuration-key/cache validation; layer-presence truth; palette/legend contract; no cross-config fallback | Auto-fit preserves shape and line distinction; default Traces use lap colors; metric gradients visibly cover supported range; missing state gives correct reason/action |
| Context and toolbox | Toggle Track and Laps through all four combinations; open/close Customize repeatedly; resize during/after motion | Mounted/inert state tests; shared timing/reflow contract; stable left-column width | No overlap, intermediate clipping, end stutter, width jump, lost selection, or lost scroll/map state |
| Analysis layouts | Select Default; try protected rename/delete; create, select, edit, rename, and delete a custom layout; restart and open another event; open a race missing a saved signal | Durable separate/global store; Default immutability; active-delete fallback; missing-signal reason/action | Controls remain compact and reachable; names/selection do not collide; no Live Telemetry dashboard is mutated |
| Technical overview | Open Technical data; inspect Pit strategy, Tire management, Fuel management, Racecraft & pace with full, partial, loading, and unavailable evidence | Exactly four categories; supported/unsupported field gating; no legacy label | Two-by-two overview fits without page scroll; category content is legible and useful rather than filler |
| Technical investigations | Open each quadrant; exercise its controls, hover/focus detail, internal scroll if present, and Back; repeat after phase/event change | Investigation identity and state-reset/preservation tests; truthful reason/action tests | One investigation consumes the full area; Back returns cleanly; no stale category, clipped chart, or unexpected page scroll |
| Tire learning | Ingest duplicate and distinct supported observations; restart; predict in-domain and out-of-domain; inspect measured, proxy, local prediction, and external reference distinctions | Stable observation IDs; idempotence; deterministic model fingerprint; threshold/null path; O/M/I orientation; confidence/bounds; atomic recovery | Labels, confidence, scope, and bounds are readable; unavailable output is not presented as a weak prediction |
| Raw IBT durability | Finalize/import, hash/copy, retry an interrupted copy, rediscover duplicate bytes, restart, remove original, back up/migrate, ordinary uninstall | Atomic content-addressed copy; SHA-256 verification; deduplication; reference repair; no automatic pruning; original unchanged | Storage/failure state is understandable and quiet when healthy; analysis/replay remains available after original removal |
| Race replay shell | Open supported, loading, partial, unavailable, and failed replay; invoke every playback/seek/speed control | Shared-clock state tests; bounded/cancelled seek work; coverage/reason contracts; one-hour/64-car adaptive-materialization and seek soak; corrupt compressed/uncompressed-size rejection | Fixed screen fits; controls never clip; failure remains contained with useful recovery |
| Replay participants/map | Play, pause, seek, cross a leader-lap boundary, inspect multiclass and missing-participant frames | Recorded-only marker fields; class/user/leader semantics; gap guard; leader-lap auto-scroll trigger | Number/class remain legible; user and class leader are clearly emphasized; rail scroll is stable and not continuously fighting the user |
| Replay grid/timeline | Play through green/yellow/white/checkered and into supported cooldown; seek before/after checker; inspect running order, the always-visible horizontal flag timeline, playback marker, and overlapping recorded events | Recorded flag/event derivation; grid and continuous-timeline state share one clock; no duplicate/invented events | The horizontal flag timeline never disappears or swaps out at checker; updates are synchronized and unclipped; flags/events retain labels and time/lap context |
| Replay comparison | Inspect user/class-leader and no-comparison states across seeks | Known-only field whitelist; no competitor controls/fuel/tire/setup/incidents | Comparison updates with the shared clock and becomes a clear reason/action state when alignment is absent |
| Replay telemetry | Play/seek across lap boundary; toggle own fastest-clean dotted overlay; select Brake ABS highlight on/off and inspect missing ABS | Player-only aligned traces; fastest-clean eligibility; dotted style; explicit ABS-only guard and gaps | One-lap scroll, map, cursor, controls, overlay, and ABS emphasis remain synchronized without jitter or misleading continuity |
| Accessibility | Keyboard through every changed control, popover, layout menu, quadrant, replay control, internal scroll region, and Back; inspect names/states/focus | Roles/names/pressed/selected/disabled state tests; focus-order contracts | Visible focus, logical order, no keyboard trap, color-independent meaning, screen-reader spot check |
| Motion/performance | Repeat section, map type, drill-in, context, toolbox, replay, leader auto-scroll, and checkered-state timeline updates under load | Obsolete-work cancellation; bounded frame/cache tests; reduced-motion path; no unbounded callbacks | Intermediate frames inspected; no jitter, clipping, ghost layer, stale frame, overflow, or endpoint snap; record delivered/dropped frames where cadence is claimed |
| High-density analysis | Select 1, 3, 20, 82, all available real laps, and a synthetic 500-lap case; move both cursors continuously and open/close Customize 50 times | Explicit selected-lap/render/cursor/vertex budgets; deterministic representative grouping; cache-invalidation and frame-cost assertions | Logical selection remains complete; UI motion and pointer feedback remain responsive; no clipped lap-row content, giant tooltip text, or zoom-scaled cursor |

## 0.16.0 development evidence recorded 2026-08-11

This is an exercised subset of the matrix, not a declaration that the entire matrix is accepted:

- The integrated source gates passed 255/255 .NET tests, 247/247 Python tests, 9/9 JavaScript syntax checks, and a Release solution build with zero warnings and zero errors.
- Real Iowa, Daytona, Portland, and New Hampshire recordings exercised corrected exact-configuration geometry. Iowa and Daytona regeneration rejected one `(0,0)` sentinel apiece instead of drawing the prior diagonal outliers.
- The real August 9 Iowa Open legacy replay contained 7,775 frames across five segments at about 1.935 Hz. The browser replay was opened and exercised at 1280x720 and 1920x1080, including first-playable-state selection, map/participant rendering, playback, seek, speed, and the unified flag-colored rail.
- A real 82-lap selection retained all 82 logical selections while rendering 252 chart paths, 27 map paths, and about 3,408 DOM elements. Recorded browser-control round trips were 62 ms average/113 ms maximum for chart pointer movement and 66 ms average/70 ms maximum for map pointer movement.
- Fifty Customize open/close cycles ended with 0 px layout drift and no overflow. The automated synthetic 500-lap case retained all logical selections behind fixed rendering/detail/bin/vertex budgets.

Still open here are the exhaustive maximized/nonmaximized, native-window, scaling, keyboard, reduced-motion, supported/missing/loading/error, and packaged-executable repetitions required by the rows above. The pointer measurements include automation/tool overhead and are not a paint-frame, input-latency, or high-refresh acceptance claim.

## Truthfulness cases

The evidence packet must explicitly demonstrate these negative cases:

- a track configuration with no valid pit geometry does not borrow another layout's pit road;
- a saved trace layout encountering a missing signal shows the actual cause and useful action without zero-filling;
- a Race replay with partial participant coverage omits unsupported cars/times and never invents competitor controls, tires, fuel, setup, damage, or incidents;
- a player recording without an explicit ABS channel does not infer ABS from braking or wheel behavior;
- a final run without measured tire endpoints remains unmeasured, while any learned prediction is separately labeled with scope, confidence, and bounds;
- insufficient or out-of-domain tire evidence returns unavailable, not a low-confidence point estimate;
- an unavailable or unauthorized Garage61 reference does not weaken local history or become synthetic model evidence;
- deleting the original iRacing IBT after verified retention does not remove the durable analysis/replay source.

## Exit rule

Automated checks must pass, but this matrix is not accepted until every applicable direct-interaction cell has artifact, environment, source, result, and retained failure evidence. A row may be marked conditional only for an explicit data/service prerequisite; visual failure, clipping, or an invented fallback is not conditional success.
