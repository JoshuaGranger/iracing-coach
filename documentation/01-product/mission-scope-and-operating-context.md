# Mission, scope, and operating context

## Product promise

iRacing Coach is a personal Windows race-engineering and driver-coaching workstation. It turns local iRacing recordings, live SDK state, setups, race history, seasonal knowledge, and authorized comparisons into actionable planning, post-race analysis, setup development, and live race support.

It is not a general chat client, social service, multi-user SaaS product, or replacement for iRacing.

## User and environment

- One person: Joshua. There are no application user accounts or shared-workspace concepts.
- Windows 10/11 x64 desktop, normally in Joshua's logged-on racing session.
- iRacing source files live under the resolved Windows Documents folder and remain read-only.
- Durable learned/user-created state lives under `Documents\iRacing Coach` and should move between PCs.
- Machine-bound credentials and private runtime state remain outside the portable folder and must be reconnected after transfer.
- The installed app must run without Visual Studio, the .NET SDK, a system Python, Node, or manual plugin installation.

## Primary workflows

| ID | Workflow | Product outcome |
| --- | --- | --- |
| `PROD-001` | Race Planning | Build one traceable briefing for the selected friendly car, exact layout, setup type, and distance. |
| `PROD-002` | Race Analysis | Open a recorded event and inspect deterministic Telemetry, Technical data, and a synchronized recorded-data Race replay, including laps, runs, participants, tires, fuel, strategy, setup, and interruption context where supported. |
| `PROD-003` | Starting Tune | Build a defensible coaching package and baseline plan without generating a simulator-loadable STO. |
| `PROD-004` | Progressive Tuning | Turn structured driver feedback plus clean recorded evidence into controlled, reversible experiments. |

Live Telemetry and Live Monitor are first-class supporting workflows.

## Product principles

| ID | Requirement |
| --- | --- |
| `PROD-010` | The application MUST show useful deterministic local results before optional AI or network enrichment. |
| `PROD-011` | The application MUST NOT fabricate values, causal conclusions, permissions, or certainty to fill a screen. |
| `PROD-012` | The application MUST remain useful offline and without Garage61 or ChatGPT. |
| `PROD-013` | Repeated parsing, filtering, arithmetic, chart preparation, grading inputs, and caching MUST be local deterministic work. |
| `PROD-014` | AI SHOULD be used only where nuanced synthesis, research, ambiguity, or trade-off reasoning materially improves the result. |
| `PROD-015` | The product MUST be NASCAR-first while keeping session, telemetry, setup, and evidence models discipline-neutral. |
| `PROD-016` | Normal views MUST use friendly racing language and MUST keep raw paths, folder identifiers, fingerprints, and protocol terms in technical details. |
| `PROD-017` | A structurally impossible feature MUST be removed rather than shipped as a permanent empty or “Unavailable” shell. |
| `PROD-018` | Temporarily disconnected services MAY show a compact actionable recovery state. |
| `PROD-019` | Navigation alone MUST NOT trigger backend, Garage61, or AI work. |

## Non-goals and prohibitions

- No raw IBT editor.
- No `.rpy` playback/editing, source telemetry mutation, setup mutation, purchased HTML mutation, or iRacing installation mutation. Race replay is a read-only visualization derived from retained IBT evidence.
- No unverified `.sto` decoder or writer in the production path.
- No Garage61 credential scraping or global-lap claim without explicit scope.
- No continuous high-rate telemetry upload to AI.
- No use of chat threads as the racing database.
- No release acceptance claim from fixture evidence alone.
