# Master development prompt: iRacing Coach companion app, next round

You are the primary development agent responsible for advancing an existing Windows iRacing coaching companion application. Work directly in the copied `iRacing Coach` workspace on the development PC. This is an implementation assignment, not a request for a design-only response, a mockup-only response, or a new project that ignores the existing backend.

Read this file completely before acting. Then read `companion-app-handoff/START_HERE.md` and every document it marks required. Inspect the actual current application source, the `iracing-coach` backend, the handoff schemas, fixtures, tests, and the current UI before choosing what to preserve or replace.

This prompt contains the newest product decisions from Joshua. It supersedes older handoff prose when they conflict, especially concerning navigation, screen behavior, diagnostic visibility, progressive-tuning experiments, AI/local responsibility, Garage61 readiness, and freedom to reconsider the frontend technology. The deterministic backend's evidence rules, source-file protections, credential handling, and truthfulness requirements remain authoritative.

Do not stop after producing a plan. Implement as much of this specification as can be completed safely in the current development round, verify it, and leave a clear continuation record for anything genuinely unfinished. Ask Joshua only when a missing decision would materially change the product or require new authority. Make reasonable implementation choices within this specification without repeatedly asking for preferences already answered here.

## 1. Product mission

Build a polished, fast, trustworthy desktop race-engineering and driver-coaching workstation that Joshua can use without opening a general-purpose Codex chat.

The product has five visible areas:

1. Race Analysis
2. Race Planning
3. Setups & Packages
4. Progressive Tuning
5. Settings, with Diagnostics on the same page

The app is NASCAR-first across ovals and road courses, but its underlying session, telemetry, track, grading, and setup models must remain usable for other iRacing disciplines.

The two non-negotiable outcomes are:

1. Make the app as useful as possible for making Joshua a better driver and race strategist.
2. Do not waste time, latency, or paid AI resources on work that deterministic local code can perform reliably.

Do not reduce functionality merely to minimize AI usage. Use AI wherever nuanced synthesis, ambiguous trade-off reasoning, research, coaching language, or setup judgment materially improves the result. Conversely, do not send raw races, chart generation, arithmetic, filtering, parsing, caching, grading calculations, or repeatable feature extraction to AI when local code can do it faster and more consistently.

The app should feel like a real product, not a frontend pasted over a chat box. AI is an expert reasoning layer inside the product, not the navigation model and not the database.

## 2. Required working method

Before changing code:

1. Run the handoff verifier and existing backend/frontend tests.
2. Inventory the existing solution, language, UI stack, process boundaries, and implemented screens.
3. Exercise the current app enough to understand Joshua's complaints rather than assuming the handoff documents describe the exact current state.
4. Record an architecture decision for the retained or revised technology stack.
5. Identify the smallest set of additive backend-contract changes needed for the new workflows.

Preserve working backend behavior. Do not rewrite the tested Python telemetry backend merely because another language is available. A rewrite or native acceleration is justified only when profiling identifies a user-visible bottleneck and parity tests prove the replacement produces equivalent results. It is acceptable to add compiled or native helpers for narrowly measured hot paths, but keep one authoritative implementation for every calculation.

Use contract-first development:

- Extend schemas additively and preserve compatibility with contract version 1.
- Tolerate unknown optional fields.
- Add sanitized fixtures for every new state.
- Add deterministic tests for every grading, session grouping, flag, run, planning, setup, and tire-model calculation.
- Never reconstruct backend archive paths in the UI when a result supplies them.
- Never make the UI parse IBT files or mutate backend archives directly.

## 3. Technology freedom and quality bar

You may use C#, C++, Rust, Python, TypeScript, WPF, WinUI, Blazor Hybrid, a native canvas, or another appropriate supported stack. The existing handoff recommends .NET 10 with a WPF host and Blazor Hybrid views because it balances Windows integration, process supervision, deployment, and rich SVG/canvas telemetry UI. Treat that as the default unless the current code or measured evidence supports something better.

Technology freedom is not permission for an unnecessary rewrite. Choose the stack that maximizes:

- delivered functionality;
- UI polish and interaction quality;
- telemetry rendering performance;
- background-process reliability;
- packaging simplicity on the racing PC;
- maintainability and testability;
- fast iteration on the development PC;
- low runtime resource usage.

Document any deviation from the current WPF/Blazor recommendation with concrete benefits and migration costs. The shipped racing-PC application must be self-contained and must not require Visual Studio, a Python installation, Node, or another development toolchain.

The UI must look sleek, premium, modern, and deliberately designed. Joshua described the desired atmosphere as similar to Codex: gentle charcoal and dark gray layers, low visual fatigue, restrained saturation, soft but sufficient contrast, and no pure-black/pure-white glare. It should also feel like a serious racing workstation rather than generic enterprise software.

Follow `UI_DESIGN_SYSTEM.md` and `config/theme.dark.json` unless a measured implementation constraint requires an equivalent adaptation. Keep the visual language original. Avoid generic Bootstrap pages, giant empty cards, walls of settings text, neon traces, excessive gradients, racing-photo decoration, or tiny low-contrast labels.

## 4. Architecture: local-first, AI-enhanced

Maintain four clear boundaries:

1. **Companion UI**: navigation, selection, forms, track interaction, charts, view state, and accessibility.
2. **Local coordinator**: process supervision, job queue, cancellation, deduplication, settings, view-model mapping, and resumable UI workflow state.
3. **Deterministic coach backend**: session discovery, IBT decoding, event grouping, telemetry calculations, archives, grading inputs, strategy math, setup history, Garage61 API, and all source-of-truth data.
4. **AI orchestration**: nuanced coaching, evidence-aware synthesis, setup trade-offs, research, contradiction handling, and natural-language interaction.

### 4.1 What must be local and deterministic

The following work belongs in local code and must not require an AI turn:

- filesystem and session discovery;
- IBT validation, hashing, parsing, and channel cataloging;
- grouping by event, SubSessionID, sim session, reconnect, Qualify, and Race;
- lap, sector, run, tire-set, caution, mixed-flag, pit, service, tow, and repair segmentation;
- fuel use and fuel-window arithmetic;
- statistics, trendlines, smoothing, resampling, and confidence gates;
- track-coordinate projection and vector-map generation;
- telemetry chart data and screen-pixel downsampling;
- Garage61 candidate filtering, ranking inputs, rate limiting, caching, and trace alignment;
- race-grade feature calculations and deterministic rubric application;
- setup inventory, fingerprints, parameter normalization, and diffs;
- storing and retrieving tuning feedback cards and experiment history;
- local cache/index maintenance and seasonal invalidation;
- UI rendering and all basic summaries.

### 4.2 What should use AI

Use AI for work where reasoning adds real value:

- condensing many valid local findings into direct coaching priorities;
- explaining why a control pattern is likely contributing to a handling or tire problem;
- reconciling driver feedback with telemetry without pretending telemetry uniquely identifies setup cause;
- selecting and explaining setup compromises;
- identifying contradictions between corners, phases, or proposed changes;
- deciding whether multiple proposed setup changes are sufficiently independent for one experiment;
- converting an evidence-backed target trace into corner-by-corner instructions;
- current manual, rule, car, and track research when the seasonal cache needs it;
- interpreting unusual races where traffic, damage, cautions, setup, and driver behavior interact;
- answering optional free-form follow-up questions.

AI outputs must be structured, schema-validated, evidence-tagged, and additive. AI may not silently overwrite measured or derived facts. If AI is missing, signed out, offline, rate-limited, interrupted, or fails schema validation, every deterministic workflow must remain useful.

### 4.3 Use AI now to reduce AI dependence later

During development, use AI aggressively to help design, implement, test, and improve local algorithms. The goal is to convert repeatable analysis into deterministic product capability. At runtime, invoke AI only after compact local evidence exists and only for the extra reasoning that code cannot reliably provide.

Instrument AI calls with:

- workflow and reason for invocation;
- compact input size;
- latency;
- model selected from the installed Codex catalog rather than a permanently hard-coded slug;
- outcome and schema validity;
- cache/reuse status;
- no secrets or raw authorization data.

Do not log private prompts or telemetry by default. Provide a user-visible setting for diagnostic AI metadata without exposing content or credentials.

### 4.4 Codex integration

Use the current supported Codex app-server interface for the rich integration unless verified current documentation directs otherwise. Start it as a supervised child process, use the default local stdio JSONL transport, perform the required `initialize`/`initialized` handshake, discover available models dynamically, inspect account state without exposing tokens, stream item/turn events, support interruption, and pin generated schemas to the installed Codex version.

Use one persistent logical AI thread per workflow context:

- Race Analysis: exact event/SubSessionID and analysis identity.
- Race Planning: season, car, exact layout, fixed/open, and distance.
- Starting package: package identity.
- Progressive tuning: setup lineage plus experiment history.

Persist only thread identifiers and workflow keys in app-owned state. The coach archive remains the racing source of truth.

Do not give the AI generic unrestricted shell/filesystem access from the app. Expose the bounded iRacing Coach domain tools and compact structured evidence. Keep the AI provider behind an internal interface so another provider could be supported later, but do not add a second credential workflow merely for architectural purity in this round.

## 5. Navigation and home

Replace or substantially redesign the existing frontend navigation when necessary. The current interaction model is not authoritative.

Primary navigation:

- Home
- Race Analysis
- Race Planning
- Setups & Packages
- Progressive Tuning
- Settings

The Home screen should remain calm and compact. It may provide:

- the four primary workflow actions;
- a concise recent-races list;
- one quiet health/status strip;
- a persistent background-job tray;
- small independent status chips for the deterministic backend, Codex, Garage61, and iRacing live connection.

Do not let health cards, explanatory text, or diagnostics dominate Home.

## 6. Race Analysis workflow

### 6.1 Race Analysis opens a race browser

Clicking Race Analysis must first open an intuitive list of recorded Race sessions. It must not immediately analyze an unexplained default session or drop the user into a blank prompt.

Default behavior:

- Include all recorded Race sessions: official, hosted, league, and AI when identifiable.
- Sort newest first.
- Provide obvious filters such as All, Official, Hosted/League, AI, Fixed, Open, Analyzed, and Needs Analysis.
- Support search by car, track, date, series, and session identifier.
- Group reconnect/split IBTs into one race.
- Group Qualify and Race recordings from the same event rather than presenting them as unrelated races.

Each race row/card should show as much of the following as is actually available without fabricating placeholders:

- date and local start time;
- car and class;
- track and exact layout;
- series/session label;
- official/hosted/league/AI status;
- fixed/open setup;
- scheduled and completed laps or timed duration;
- start and finish position;
- field size;
- incidents;
- caution count and caution laps;
- number of pit stops/runs;
- whether damage/tow/repair materially affected the race;
- analyzed/cached state;
- overall race grade when a supported grade exists;
- a compact one-line result or most important limitation.

Single-click selects the race and shows an immediate cached preview/overview. Double-click or an explicit **Open Analysis** action enters the deep-dive workspace. If no cached analysis exists, start the deterministic analysis in a cancellable background job automatically while keeping navigation responsive. Never make the user type “analyze my last race” to use the core product.

### 6.2 Event-level Qualify and Race navigation

Inside the analysis workspace, place a clear segmented control near the upper left:

`Qualify | Race`

Include Practice under a secondary control when recorded, but keep Qualify and Race primary. Maintain separate conditions, setup identity, runs, fastest laps, sectors, and coaching context. Do not combine qualifying laps with race pace or tire conclusions by default.

### 6.3 Immediate analysis hierarchy

Display in this order:

1. Event/session header and Qualify/Race selector.
2. Compact deterministic Race Card.
3. Strict race grades and three highest-value improvement priorities.
4. Run/lap navigator.
5. Track and synchronized telemetry workspace.
6. Detailed tabs/panels for corners, tires, fuel/strategy, interruptions, setup, and evidence.

Local deterministic content appears first. Optional AI refinement may stream into clearly labeled coaching areas without hiding the existing result.

### 6.4 Race grading system

Implement an evidence-aware American letter-grade system with plus/minus modifiers:

`A+`, `A`, `A-`, `B+`, `B`, `B-`, `C+`, `C`, `C-`, `D+`, `D`, `D-`, `F`

The purpose is to identify room for improvement, not to make Joshua feel good. Grades must be difficult to earn. An A+ represents an unusually complete race at approximately the execution standard expected from a strong 3k-iRating driver in a comparable context. A merely better-than-Joshua's-recent-average race is not automatically an A.

Grade the race against its **defensible achievable execution envelope**: what a highly capable driver could reasonably have achieved with that car, track, setup class, fuel, tire state, conditions, traffic, cautions, and damage context. Do not call an impossible mathematical splice or world-record lap the race's optimal possibility.

Use five categories:

1. **Pace & Corner Execution**
2. **Consistency & Smoothness**
3. **Tire Management**
4. **Pit & Fuel Strategy**
5. **Racecraft & Incident Avoidance**

Recommended starting weights are 30%, 20%, 20%, 15%, and 15%, respectively. Make weights versioned and configurable in code, not scattered through UI components. If a category is genuinely unavailable, display `N/A`, explain why, and redistribute its weight only according to an explicit rubric. Do not silently assume a neutral score.

The pace component may use a representative Garage61 cohort centered around roughly 3k iRating when global-visible access and comparable telemetry are actually available. Favor a distribution of comparable laps, not one extreme lap. Other categories must use race-specific telemetry and context; a Garage61 hot lap alone cannot grade strategy or racecraft.

Reference hierarchy:

1. Usable same-car, exact-layout, same-season, same fixed/open, condition-comparable Garage61 cohort.
2. Strong same-context personal history and prior clean races.
3. Best supported clean portions of the selected event.
4. Explicit `N/A` or provisional status when the envelope cannot be established.

Do not allow A+ unless evidence confidence is high, pace is consistent with the target cohort, no material category is weak, and the race was unusually complete. Do not penalize outcomes outside the driver's control as though they were execution failures. Damage, traffic, cautions, and unavoidable incidents must be separated from avoidable driver behavior.

Use conventional numeric-to-letter boundaries internally if useful, but the feature calculations and calibration must define what the numeric score means. Do not start with arbitrary point deductions and call the result scientific. Version the rubric, retain the input evidence, and make every grade auditable.

Each category card must show:

- letter grade;
- confidence/evidence status;
- one-sentence reason;
- the strongest positive behavior;
- the highest-value improvement;
- a drill or action for the next race when appropriate;
- a details view with the measurements and rubric inputs.

The overall grade summarizes this one race against its achievable envelope. Historical grade trends may be shown secondarily, but do not redefine the race grade as improvement versus the user's recent average.

### 6.5 Runs, laps, flags, sectors, and pit stops

Create a legible run-oriented navigator inspired by the useful concepts in Garage61 but with better visibility.

Define a run using confirmed pit-service boundaries according to the backend contract. Track tire-set age separately because a stop does not prove that tires changed.

Run headers should show:

- run number and lap range;
- total, green, caution, and mixed-lap exposure;
- tire-set identity and confirmed age where available;
- average clean pace and degradation;
- fuel used and remaining;
- setup fingerprint/change status;
- fastest lap in run;
- pit/service summary;
- clean/confounded eligibility;
- repair remaining or other interruption context.

Every lap row must have a prominent state rail plus text/icon, not a tiny color-only marker:

- GREEN
- CAUTION
- MIXED
- PIT IN
- PIT OUT
- INCOMPLETE
- OFF TRACK
- INCIDENT
- REPAIR/TOW

For a mixed lap, show the fractional or segmented green/yellow exposure when supported. Highlight fastest lap and personal-best sectors with restrained badges and an accent edge rather than a saturated full-row fill. Use the session's actual sector definitions. Keep official event best, personal best, and theoretical best distinct.

### 6.6 Track map and synchronized telemetry

Build a telemetry-derived vector track map from latitude/longitude when available. Use a normalized distance strip when geometry is unavailable. Optional authoritative imagery may be placed underneath when its source and alignment are known. Do not unpack proprietary iRacing `.dat` files or fabricate track edges.

Allow one or multiple laps to be selected. Default to no more than four observed laps plus one target/reference so the view remains legible. Non-focused traces should become quieter but remain distinguishable.

Provide map color modes for:

- speed;
- throttle;
- brake;
- steering;
- time delta;
- estimated tire stress;
- calibrated groove/path position.

Use a shared cursor: hovering or dragging on the map updates every trace and the corresponding lap-distance/corner readout; hovering a chart moves the map marker. Support zoom, reset, keyboard access, and clear legends.

Required per-lap chart stack:

1. Speed and time delta
2. Throttle and brake
3. Gear and RPM
4. Steering-wheel angle
5. Optional dynamics, tire temperatures/pressures, ride heights, or shock traces

Use distance rather than time as the primary comparison axis. Downsample for drawing using screen-pixel min/max preservation so short brake or steering spikes are not erased. Preserve full-detail data for focused inspection without rendering millions of points.

### 6.7 Target Lap behavior

Use these separate reference types:

- Best Actual Lap
- Garage61 Reference Lap
- iRacing Theoretical Optimal
- Target Lap

Call the model-assisted, condition-adjusted recommendation **Target Lap**, not Optimal Lap. It represents a supported target for a stated tire age/phase, fuel load, track temperature, setup, and traffic condition. Show it as a virtual dashed row and dashed trace with provenance, setup scope, scenario tags, evidence class, and confidence.

Use a stepped Early/Middle/Late or supported tire-state selector until continuous interpolation is justified. Unlock a continuous tire-age slider only when the model has enough calibrated data. If interpolation is unavailable, the control must visibly snap to supported phases rather than draw a falsely precise curve.

Prefer an actual recorded reference archetype plus conservative zone adjustments. Do not splice incompatible best points into a physically impossible control trace. If a usable aligned reference does not exist, show relative coaching and `[U] Exact target unavailable`.

### 6.8 Tire stress, wear, and falloff

Treat these as separate concepts:

- permanent tread wear measured at service;
- thermal state and pressure;
- observed grip/pace falloff;
- modeled stress contribution.

The first implementation may show a normalized tire-stress contribution heatmap using braking energy, brake/steer overlap, steering work, lateral load, wheel-speed divergence proxies, yaw/slip proxies, throttle/wheelspin proxies, tire temperature/pressure, platform movement, track temperature, fuel, traffic, cautions, and heat cycles.

Do not claim exact continuous wear. End-of-run wear is an aggregate label observed only at confirmed service. Build the future calibrated model as a constrained, versioned model whose estimated nonnegative lap/zone contributions sum to the observed run endpoint. Separate general car behavior, track/layout effects, conditions, setup fingerprint, and driver history. Display uncertainty and model maturity.

Use wording such as `high estimated right-front stress contribution`, never `Lap 12 removed 1.3% of the RF` unless a future validated measurement genuinely supports it.

Exclude or separately model pit, caution, tow, repair, off-track, and damage-correlated samples. An unmeasured final run remains unmeasured.

### 6.9 Damage, towing, repair, and interruptions

Make interruption context visible in the event list, grade calculations, run rows, lap rows, track view, and detailed analysis.

Keep incident points, damage evidence, towing, pit-road transit, stall occupancy, service-active time, mandatory repair, optional repair, and fast-repair confirmation distinct. Overlapping timers must use parallel timeline lanes and must not be added into a fictitious repair-exclusive total.

Repair/tow laps and defensible repair-correlated windows are excluded from clean pace, target, tire-falloff, setup comparison, and ordinary pit-loss learning by default. Provide a diagnostic include toggle without silently changing the default grade.

## 7. Race Planning workflow

Clicking Race Planning must open a clear planner, not a blank chat field.

Provide two entry paths:

### 7.1 Upcoming Event

Let the user select an upcoming iRacing series/event. Use a supported official iRacing schedule/data source when possible to prefill:

- season/week;
- series;
- car or eligible cars;
- track and exact layout;
- scheduled laps or timed duration;
- fixed/open setup;
- qualifying format;
- relevant race rules when available.

Research and use the current supported iRacing authentication/data mechanism. Do not scrape browser cookies, saved passwords, or internal browser state. Keep an interface boundary around the schedule provider and provide manual fallback when official data is unavailable.

### 7.2 Manual Plan

Allow manual selection of:

- car;
- exact track/layout;
- laps or timed duration;
- fixed/open setup;
- qualifying/race intent;
- weather and track assumptions;
- expected caution scenario;
- optional starting fuel or rules override.

Sort car and track choices with the most recently used first, while retaining search and complete lists.

### 7.3 Planning output

The planning page is the one-stop-shop for approaching the upcoming race. Include:

- all-green fuel requirement and operational reserve;
- plausible caution-mix scenarios;
- minimum fuel stops and defensible pit windows;
- expected green-lap tire-run range;
- fuel and tire sensitivity by strategy branch;
- target pace by tire/run phase;
- qualifying plan and qualifying target behavior;
- corner-by-corner entry, center, and exit guidance;
- target speed, braking, throttle, steering, and groove only when supported;
- restart and traffic guidance;
- brake-bias starting point or range when supported;
- in-car adjustment guidance for named scenarios;
- fixed/open implications;
- three concise race triggers for tire phase, pit decision, and adjustment/rollback;
- source, confidence, and evidence status for every material claim;
- relevant prior races and cached Garage61 comparisons.

In-car adjustment advice may include brake bias, weight jacker, anti-roll-bar settings, traction controls, engine maps, grille tape, or other car-appropriate controls only when that car actually exposes them. Never show a generic control as though every car has it. Separate a recorded/requested adjustment from proof it was applied or serviced.

Fuel feasibility is deterministic. Do not label a strategy optimal unless position, pit loss, service/rule constraints, caution uncertainty, overtime exposure, and relevant history support that stronger claim.

Planning should be cache-first. Reuse the season/car/exact-layout/fixed-open knowledge package, cached Garage61 index/CSVs, and relevant strategy history. Do not repeat extensive research for the same valid combination during the same season unless content/physics changed or the cache is incomplete.

## 8. Setups & Packages

Rename the unclear Setup Library to **Setups & Packages**.

Provide these sections:

1. **Library**
2. **Compare**
3. **Build Package**
4. **Experiments**

### 8.1 Library

Inventory setup artifacts by car, track/layout claim, Q/R intent, season, source, and last use. Treat `.sto` files as opaque read-only artifacts with path, size, timestamps, and SHA-256. Use the IBT `CarSetup` tree as the authority for what was driven. Parse readable HTML exports and preserve filename/header conflicts rather than hiding them.

Show a useful visual parameter hierarchy for decoded IBT/HTML data: tires, aero, alignment, springs, shocks, bars, heights, differential, gearing, brakes, and car-specific sections. Display source and confidence. Provide a safe **Open containing folder** action and clear instructions for loading/saving inside iRacing.

### 8.2 Compare

Allow two or more readable setup snapshots to be compared. Group changes by system and highlight coupled values, tech implications, Q/R intent, provenance, and uncertainty. Do not imply that same-stem STO and HTML files match unless evidence establishes it.

### 8.3 Build Package

Use a short wizard:

`Context -> Source -> Package -> Baseline Run`

Select season, car, exact layout, Q or race intent, and exact/donor source. Show hashes, setup lineage, filename/header conflicts, donor reasoning, official manual targets, tech/dynamic checks, and rollback identity. Produce a coaching worksheet/package. The user applies and saves the setup inside iRacing.

### 8.4 Editable worksheet, not a fake STO editor

Provide an in-app structured setup worksheet that can hold current, planned, and tested values plus notes. It may be edited and versioned as coach data, but it must not claim to be a simulator-loadable STO unless a supported encoding method is separately proven.

### 8.5 Future STO research

Do not block this release on STO writing. Create a clearly isolated future R&D backlog item to investigate supported or officially documented setup export/import mechanisms. Prefer an official SDK, documented interchange format, or safe iRacing integration. Do not overwrite source STO files and do not make an unverified encoder part of the production path.

## 9. Progressive Tuning workflow

Progressive Tuning begins by selecting a recorded Race, qualifying, or suitable test session. Do not begin with a large empty text box.

Disable garage-change recommendations for fixed sessions. Require a finalized, sufficiently clean run and screen damage/tow/repair-confounded evidence before tuning.

### 9.1 Graphical feedback builder

Build an interactive feedback workflow:

1. Select the session and setup baseline.
2. Select run phase: Early, Middle, or Late, with actual green-lap-on-set bounds when known.
3. Click a named corner or telemetry load zone on the track map.
4. Select corner phase: Entry, Center, Exit, Transition, or Whole Corner.
5. Choose one or more symptoms.
6. Set severity from 1-5.
7. Set driver confidence from 1-5.
8. Optionally add a concise free-text note.
9. Add the item as a feedback card.
10. Reorder cards by priority.

Initial symptom vocabulary should include:

- tight/understeer;
- loose/oversteer;
- braking instability;
- poor rotation;
- snap oversteer;
- wheelspin/lacks drive;
- bottoming or splitter contact;
- bump/curb sensitivity;
- steering too heavy or too light;
- slow direction change;
- unstable on throttle lift;
- runs out of adjustment range;
- other, with optional text.

Do not force feedback for every corner or every run phase. The app should make one precise complaint easy to enter.

Keep driver feedback and telemetry corroboration visually separate. Telemetry may support where/when a symptom occurs but may not replace the driver's description or uniquely prove a setup cause.

### 9.2 Conflict and trade-off reasoning

Evaluate all feedback cards together. Detect when requested fixes are complementary, independent, coupled, or contradictory. Explain conflicts plainly. Let the user choose which problem has priority when the compromise is subjective.

The default experiment remains one setup system at a time because it provides clean attribution. However, Joshua does not require a rigid one-change-only rule. Permit a multi-change experiment when:

- the systems are demonstrably independent or the changes are an explicitly coupled sequence required to maintain tech/heights/preload;
- each change addresses a distinct documented complaint;
- cross-coupling risk is low and stated;
- each change has its own expected effect, risk, validation metric, and rollback value;
- the UI warns that causal attribution is weaker than a single-system A/B test.

When independence is uncertain, when complaints conflict, or when a platform/legality issue dominates, recommend one system first. Do not stack speculative changes merely to reduce iteration count.

Represent recommendations as an experiment bundle containing one or more change groups. Extend backend schemas and history carefully rather than hiding concurrent changes in prose.

Every experiment must preserve:

- exact baseline fingerprint;
- original feedback cards and priority;
- telemetry corroboration and limitations;
- planned values/directions;
- expected effects;
- possible side effects;
- success criteria;
- matched-test requirements;
- per-change rollback;
- final keep/rollback/inconclusive outcome.

After the user runs the candidate, make the result-review interaction graphical: improved, unchanged, worse, or inconclusive per feedback card, with optional revised severity and note. Analyze the candidate telemetry, compare matched phases, and retain unsuccessful experiments so they are not repeated under comparable conditions.

## 10. Garage61 Pro integration

Design and implement on the expectation that Joshua will have Garage61 Pro and that the personal token will become Pro-authorized.

Do not assume that Pro or `driving_data` permission grants global-visible lap search. Keep `global_visible_laps_approved` false until Garage61 explicitly grants it and local configuration deliberately enables it.

Required behavior:

- Configure the PAT only through the backend's secure no-echo Windows user-bound flow.
- Never expose the token to the UI, AI context, command line, logs, crash reports, or copied development workspace.
- Verify account and granted permissions before sync.
- Keep authenticated traffic pinned to exact HTTPS `garage61.net` and reject cross-origin credential forwarding.
- Query exact car/layout/season and fixed/open cohorts separately.
- Filter/rank by tire compound, BoP, track state, temperature, weather, fuel, cleanliness, rating, and pace gap when fields exist.
- Prefer 3-5 representative drivers modestly faster than Joshua plus 1-2 elite examples.
- Use an approximately 3k-iRating comparable cohort to help calibrate the pace ceiling for A/A+ grading, without treating iRating alone as proof of driving quality.
- Download authorized CSVs once, preserve unknown fields, normalize units with recorded assumptions, align by lap distance, and cache them by season/car/exact layout/setup cohort.
- Rate-limit, deduplicate, and back off politely.
- Make comparison scope visible: own/team, approved global-visible, website-selected, or manual export.
- Continue all local workflows when Garage61 is offline or unauthorized.

Do not web-scrape Garage61 as the routine production integration when the official API can perform the operation. A signed-in browser remains an explicitly invoked fallback for capabilities the API does not expose.

## 11. Full telemetry and semi-live capability

Preserve the complete catalog of every recorded IBT channel. Maintain a purpose registry classifying each channel as core analysis, visualization, tire model, handling/setup, strategy/race control, live-only, diagnostics, redundant/deprecated, car-specific, or unsupported. `Use 100% of telemetry` means catalog everything and deliberately decide how each channel is used; it does not mean feed every value to AI or eagerly decode eighty million values on every race.

Add normalized, cached per-lap trace artifacts for the analysis UI. Store sufficient resolution for accurate comparison and map rendering while retaining raw-source references for bounded native-rate queries. Use lazy specialist queries for transient events.

Plan and, if the post-race foundation is stable enough, implement a live IRSDK sidecar that:

- reads iRacing shared memory locally;
- detects current session, lap, run, pit, caution, tire, fuel, tow, repair, and track-condition state;
- updates local charts and status without AI;
- triggers optional AI only at a completed lap block, pit stop, explicit user request, or finalized IBT;
- never treats a changing IBT as finalized archival evidence;
- does not continuously stream 60 Hz raw telemetry to AI.

Track temperature is already available in normal telemetry when recorded; do not build a redundant recorder merely because the UI needs it. Use live capture for immediacy and state changes, not for duplicating every durable IBT value.

## 12. Settings and Diagnostics

Replace separate, spacious Settings and Diagnostics pages with one compact **Settings** page.

Use concise sections for:

- iRacing Documents source root;
- read-only iRacing installation root;
- coach archive root;
- packaged backend/runtime;
- Garage61 connection and granted scope;
- Codex connection/account/model availability;
- units and display;
- cache/archive retention;
- live sidecar behavior;
- privacy and diagnostic logging.

At the bottom, use a strong divider and a **Diagnostics** section on the same page. Diagnostics must be shown by default, not collapsed. Keep it dense and useful rather than fluffy.

Diagnostics should include:

- app/backend/runtime versions;
- contract compatibility;
- source/archive validation;
- process health;
- Garage61 and Codex readiness without credentials;
- recent job timings and stage timings;
- channel coverage summary;
- last error with a useful recovery action;
- cache state and size;
- buttons for health test, open logs, copy redacted support bundle, and verify installation.

Do not fill the page with explanatory prose. Use compact rows, status chips, tooltips, and links to deeper help.

## 13. Performance and responsiveness

The app should feel immediate even when enrichment continues.

Targets:

- Cached race preview/list data: effectively instant.
- Cached full Race Card: comfortably under one minute and normally far faster.
- Uncached ordinary race analysis: less than two minutes end-to-end, with useful deterministic content appearing much sooner.
- Race planning and progressive-tuning recommendation: less than two minutes.
- New starting-package research: less than four minutes.
- Opening cached maps/charts and switching selected laps: interactive, without an AI call.

These are product targets, not permission to invent answers. Missing evidence produces an unavailable result and an optional deeper background job.

Requirements:

- Parse/import a finalized IBT once and cache derived artifacts.
- Deduplicate identical work by source hash and full query key.
- Never block the UI thread on backend, network, AI, or file hashing.
- Use cancellable background jobs and a persistent job tray.
- Allow navigation while jobs run.
- Prevent duplicate writes for the same in-flight operation.
- Show meaningful stages and elapsed time.
- Reuse seasonal and Garage61 caches.
- Measure before optimizing or rewriting.

## 14. Evidence and truthfulness

Preserve these literal evidence classes throughout UI, exports, AI prompts, grades, targets, and setup recommendations:

- `[M]` measured
- `[D]` derived
- `[I]` inferred coaching
- `[P]` diagnostic proxy
- `[U]` unavailable/unsupported

Never fabricate:

- tire wear between service observations;
- final-run wear;
- a damaged component or exact severity;
- repair-exclusive time by adding overlapping intervals;
- caution boundaries not supported by telemetry/results;
- field position or pit loss;
- Garage61 scope;
- exact target values from an unusable comparison;
- directional high/low groove before inside/outside calibration;
- setup values hidden inside an opaque STO;
- proof that a requested pit adjustment or service was completed;
- an overall/category grade when its essential evidence is absent.

Unavailable is a legitimate product state and should be visually neutral, specific, and actionable.

## 15. Security and data boundaries

- Treat source `.ibt`, `.rpy`, `.sto`, HTML exports, iRacing install content, and configuration as read-only unless Joshua explicitly authorizes a narrow change.
- Write only to app-owned settings/logs and the coach archive through the backend.
- Reject untrusted UNC/device paths at backend trust boundaries.
- Do not inspect browser cookie stores, saved passwords, Garage61 Agent databases, Codex auth files, or unrelated local-app data.
- Do not copy racing-PC user-bound credentials to the development PC.
- Redact paths and private identifiers appropriately in support bundles while retaining useful diagnostics.
- Package upgrades non-destructively and preserve existing archives/settings.

## 16. Contract additions to design and implement

Use names appropriate to the existing codebase, but ensure structured contracts exist for these product concepts:

- Event browser row and event grouping, including Qualify/Race children.
- Event/session overview statistics.
- Run/lap status, mixed flag exposure, fastest lap, and sector bests.
- Normalized lap telemetry traces and telemetry-derived track geometry.
- Target Lap/reference provenance and capability gates.
- Race grade rubric version, category inputs, category grades, overall grade, confidence, unavailable reasons, and next actions.
- Upcoming-event schedule and manual planning request.
- Race planning output, including qualifying and in-car-adjustment scenarios.
- Setup library entry, readable parameter tree, and setup diff.
- Graphical tuning feedback cards.
- Tuning conflict/trade-off analysis.
- Experiment bundle with independent/coupled change groups and per-change rollback.
- Tire-stress feature record and calibrated-model maturity.
- Live sidecar status when implemented.

Prefer additive version-2 schemas or optional contract extensions. Add fixtures for complete, partial, unavailable, conflicting, repair-confounded, fixed-session, offline, and Pro-without-global-access states.

## 17. Testing and acceptance

Do not declare the round complete until the applicable tests pass.

At minimum test:

- newest-first race browsing and every filter;
- reconnect/event grouping;
- Qualify/Race separation;
- cached and uncached selection behavior;
- green, caution, mixed, pit, incomplete, incident, and repair/tow lap states;
- run boundaries versus tire-set boundaries;
- fastest lap and sector calculations;
- grade repeatability, rubric versioning, N/A behavior, damage/traffic screening, and A+ gates;
- strategy planning with all-green, caution, missing-rule, and missing-position states;
- recent car/track ordering;
- fixed/open behavior;
- setup library conflicts and diffs;
- graphical feedback creation, deletion, priority ordering, and persistence;
- contradictory tuning complaints;
- valid independent multi-change bundle and rejected unsafe bundle;
- baseline fingerprint and rollback;
- track map with GPS and distance-strip fallback;
- stepped phase selector and continuous-slider capability gate;
- multi-lap trace synchronization and performance;
- target unavailable/usable states;
- tire endpoint measured, stale, unavailable, and final-run-unmeasured states;
- incident-only, tow, mandatory repair, optional repair overlap, repair remaining, and fast-repair request/use distinctions;
- Garage61 offline, unauthorized, Pro, own/team-only, and explicitly approved global-visible scopes;
- Codex missing, signed out, interrupted, invalid schema, and successful refinement;
- active/changing/truncated IBT deferral;
- cancellation, restart/resume, app upgrade, and archive preservation;
- Windows scaling at 100%, 150%, and 200%;
- keyboard-only navigation, focus, high contrast, reduced motion, and color-vision safety;
- secret and authorization-header redaction.

Capture visual QA screenshots for every primary screen in populated, empty, loading, unavailable, warning, repair-confounded, and long-content states. A technically functional but confusing or visually unfinished screen does not meet acceptance.

Run the handoff verifier after changing contracts/fixtures and regenerate the manifest/checksums only through the supplied scripts.

## 18. Delivery order

Use this recommended sequence while keeping the app runnable after every milestone:

### Milestone 1: foundation and information architecture

- Verify/build current solution.
- Lock the chosen stack and process boundaries.
- Replace navigation and combine Settings/Diagnostics.
- Implement the race browser and event grouping.
- Preserve existing deterministic Race Card behavior.

### Milestone 2: deep Race Analysis

- Qualify/Race toggle.
- Run/lap navigator and status visibility.
- Track map and synchronized multi-lap charts.
- Damage/repair annotations.
- Strict race-grade engine and UI.
- Target Lap capability gates.

### Milestone 3: Race Planning

- Upcoming-event provider plus manual fallback.
- Strategy, tire, qualifying, in-car adjustment, and corner-plan output.
- Seasonal/history/Garage61 cache use.

### Milestone 4: Setups & Packages and Progressive Tuning

- Library, readable viewer, compare, package wizard, and experiments.
- Graphical track-based feedback.
- Conflict/trade-off reasoning.
- Controlled single- or justified multi-system experiment bundles.

### Milestone 5: Garage61 Pro and AI refinement

- Activate secure Pro-authorized integration.
- Add representative comparator sync and grade/target integration.
- Add Codex app-server orchestration with structured outputs and cost/latency instrumentation.

### Milestone 6: tire calibration and semi-live sidecar

- Persist stress features and model maturity.
- Begin calibrated aggregate-label tire model.
- Add live local IRSDK views when the post-race product is stable.

Do not postpone a simple deterministic capability merely because a later AI or Garage61 milestone could enhance it.

## 19. Required final handoff from you

At the end of this development round, provide Joshua with:

- a concise description of what now works;
- screenshots of the major redesigned screens;
- exact build/package location;
- installer or self-contained portable `win-x64` package;
- version and compatible backend/contract version;
- test and verifier results;
- measured performance for cached and uncached representative workflows;
- any remaining limitations separated into product, data, and external-access categories;
- a non-destructive upgrade/install procedure for the racing PC;
- a prioritized next-round backlog, including the isolated supported-STO-writing research task.

Do not hand back only source code or a conceptual recommendation. Deliver a tested runnable artifact if the development environment permits it. If an external dependency genuinely blocks one feature, complete the rest, preserve offline behavior, and state the exact blocker and recovery action.

## 20. Product decisions already approved by Joshua

Do not ask these again:

- Show all recorded Race sessions by default, newest first, with filters.
- Single-click preview and explicit/double-click deep analysis are acceptable.
- Use the five strict grade categories above.
- A+ should be unusually difficult and approximate a complete 3k-iRating-level race.
- Grade the selected race against its defensible achievable possibility, not merely Joshua's recent trend.
- Support both official upcoming-event selection and manual planning.
- Include qualifying plans, brake bias, and car-appropriate in-car adjustments.
- Use Setups & Packages with a readable library/compare/worksheet workflow; simulator-loadable STO generation is separate future research.
- Use a clickable track-based graphical progressive-tuning feedback builder.
- Default to one setup system, but allow justified independent or explicitly coupled multi-change experiments.
- Use Target Lap terminology and evidence-gated tire-phase controls.
- Use telemetry-derived vector track maps, with optional authoritative imagery.
- Assume Garage61 Pro will be configured, without assuming global-visible approval.
- Show Diagnostics by default at the bottom of the single Settings page.
- Use AI extensively for nuanced reasoning and development acceleration, but local code for repeatable work.
- You may substantially redesign the frontend and may choose the best technology stack, provided capability, polish, packaging, performance, truthfulness, and tested backend behavior are preserved.

Begin by verifying the copied handoff and inspecting the current app. Then produce a short implementation plan and start implementing it.
