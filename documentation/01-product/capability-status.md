# Capability status

This inventory separates intended user-facing capability from current-main development reality. Reality labels are based on current source, focused automated tests, and the named local interaction evidence; they do not imply real SDK, private-library, or product acceptance unless the acceptance boundary explicitly says so. Only Joshua can accept a product scope or artifact.

| Capability | Intended state | Current reality | Acceptance boundary |
| --- | --- | --- | --- |
| Home | Compact race desk, recent races, live state, next actions | Implemented; finalized uncached races enter a quiet sequential background cache queue that yields to connected live telemetry and interactive analysis, and rows update with supported race-shape, pace, and tire/load context | Source and automated priority behavior verified; private-library breadth and combined-load measurement remain conditional |
| Live Telemetry | Full-screen equal-share named grid, non-shrinking overlay Toolbox, configurable forms, fuel and safe cues | Implemented with SDK source and timestamped display-synchronized tile canvases; missing intervals split retained paths | Source/replay interaction verified; sustained real SDK/display cadence remains open |
| Live Monitor | Separate always-on-top, display-only renderer and selector for the same portable named layouts | Implemented with machine-local physical scale and retained, incrementally updated tile visuals painted on the display dispatcher | Source/focused tests verify the rendering path; continued real-race, cadence, and multi-monitor validation required |
| Race browser | Newest-first friendly event rows at least as informative as Home; whole row opens | Implemented when cached measurements exist | Local fixture behavior verified; historical source breadth pending |
| Full Race Analysis | Telemetry, Technical data, and Race replay with exact-configuration vector maps and separate global named trace layouts | Implemented in development source with focused contract/mapping tests; exact-config geometry and global layout persistence are wired, but the new surfaces are not product-accepted | The overhaul matrix still requires exhaustive 1280x720/1920x1080 interaction evidence against the exact executable; representative real-recording breadth remains open |
| Technical data | Fixed two-by-two overview for Pit strategy, Tire management, Fuel management, and Racecraft & pace, with full-area drill-ins | Implemented in development source with supported/unavailable reason-and-action branches and focused source/mapping tests | Every supported/partial/unavailable category and drill-in must still pass the fixed-screen visual and truthfulness matrix |
| Race replay | Recorded-data participant map, leader/lap rail, running-order grid, always-visible horizontal flag timeline, known-only comparison, and player telemetry | Implemented in development source behind canonical coverage/geometry/player-frame gates; unusable recordings receive one concise unavailable state | Representative full-field recordings, shared-clock playback/load measurement, and exhaustive visual/playback checks remain required; `.rpy` discovery alone is never evidence |
| Tire/capability learning | Portable deterministic NASCAR-first local model with measured/proxy/predicted separation, confidence/bounds, and eventual Garage61 references | Schema-1 local observation/model/prediction and scoped-reference storage are implemented with a three-matching-session floor and feature-aware confidence; ordinary session-local calculated wear remains separately labeled | Calibration, recovery/migration breadth, representative compatible local history, and authorized Garage61 reference evidence remain required; unsupported or weakly matched domains stay unavailable/low-confidence |
| Raw IBT retention | Permanent verified content-addressed copies in the portable Documents home | Implemented in the offline workflow with pre-analysis copy, SHA-256 verification, deduplication, atomic manifest replacement, and no automatic pruning | Interrupted-copy recovery, backup/migration, storage reporting, privacy exclusion, and exact-executable lifecycle evidence remain required |
| Qualifying phase | Present only when recorded and openable | Conditional | Automated coverage exists; real paired-event acceptance pending |
| Race Planning | Manual matching-history planner and briefing | Implemented for recorded-history path | Official upcoming-event discovery not implemented |
| Setup indexing | Internal read-only discovery used by analysis and Starting Tune | Implemented; no first-class library UI | Local verification exists |
| Starting Tune package | Guided open-setup package workflow | Implemented as Event → Source → Checks → Run | Automated coverage exists; real baseline acceptance pending |
| Progressive Tuning | Representative-race, verified-turn, stage/phase feedback workflow producing one controlled open-setup test with deterministic fallback and optional bounded AI selection | Evidence-contract v2 is implemented in development source with exact recording/open-target/map/setup identity gates, atomic portable drafts and corrections, versioned O'Reilly/Xfinity rules, one-change/manual-application safeguards, strict AI candidate/evidence membership, and linked result capture | Not product-accepted: the complete browser/native visual matrix and at least one real clean compatible open-setup A/B cycle remain required; official turn-map breadth, other car families, calibrated numeric steps/ranges, and real AI-account interaction remain conditional or unavailable |
| Connections | Garage61 and private Coach Engine service management inside Settings | Implemented | Real Garage61/ChatGPT account acceptance pending |
| Settings and Diagnostics | Compact task-first settings, subordinate Connections, portable preferences, troubleshooting, and backup preparation | Implemented | Source structure verified; integrated visual, external-auth, and accessibility acceptance remain conditional |
| Garage61 own/team API | Protected machine credential, health, bounded sync | Conditional | Storage/status covered; real authorized sync pending |
| Garage61 global comparison | Only with separately approved scope | Unsupported now | MUST remain absent until permission exists |
| AI coaching | Optional bounded Coach Engine synthesis | Conditional | Runtime packaged; real account/schema interaction pending |
| Wet-weather analysis | Only when validated wet data exists | Not implemented | Hidden from production |
| Multiclass analysis | Class-aware scoring and context | Not implemented | Hidden from production |
| Target/reference trace | Evidence-gated aligned comparison | Partial/conditional | Actual recorded multi-lap trace works; validated external target remains limited |
| Tray behavior | Minimize/close options, monitor controls, popout hide/show, and definitive tray Exit | Implemented in 0.14.0 source with non-activating automatic show, connected-session manual-hide suppression, explicit popout destruction during app exit, and an armed shutdown deadline | Focused automated contract passes; a genuine mouse-driven tray Exit against the exact 0.14.0 package remains pending |

## Capability registry alignment

The production `CapabilityRegistry` is the user-visible inventory. Current source keeps setup comparison as an internal conditional capability, removes the unused library surface, and aligns shared live layouts, recorded track zones, and Starting Tune with their real implementations. Registry tests prove the encoded visibility cases; they do not prove that every real recording supplies the prerequisite data.

Vehicle sideslip retains a separate truth boundary from Yaw Rate and tire slip. The deterministic engine now derives signed sideslip in degrees from paired finite recorded `VelocityX`/`VelocityY` samples under positive-forward-velocity and 5 m/s planar-speed guards; guarded samples remain gaps, while Yaw Rate is untouched. The UI's `Slip Angle` trace is therefore a derived vehicle-sideslip series when those inputs pass, not a native `SlipAngle` field or tire-slip measurement. Focused derivation/mapping checks and one Iowa recording support that scoped path; broad cross-car/session validation remains open.

## Visibility rules

| ID | Requirement |
| --- | --- |
| `CAP-001` | A permanently unsupported or unimplemented capability MUST NOT appear as an actionable production control. |
| `CAP-002` | A conditionally applicable control MUST appear only when the current recorded/live context supplies its prerequisite. |
| `CAP-003` | A temporary outage MAY retain the control only with one concise state and recovery action. |
| `CAP-004` | Missing individual evidence MUST remove or weaken the dependent statement, not erase unrelated supported content. |
| `CAP-005` | Capability visibility decisions MUST be testable without making service requests. |
| `CAP-006` | The capability inventory MUST match the behaviors present in production source and the traceability matrix. |
| `CAP-007` | A permanently impossible or intentionally unsupported capability MUST have no actionable production shell. |
| `CAP-008` | A contextual capability MUST be hidden when its prerequisite is absent unless the workflow expects that measurement; an expected but absent measurement MAY use concise `Not measured` or `Insufficient evidence` wording. |
| `CAP-009` | Missing contextual evidence MUST NOT be converted to zero, neutral, average, false, or manufactured data. |
