# Optional Codex orchestration

## Recommended adapter

Use the Codex app-server as a second local child process over stdio for rich in-app coaching: authentication state, persistent threads, streamed agent messages, approvals, structured output, and cancellation. The official [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server.md) describes it as the interface for deep integrations in custom clients. It uses newline-delimited JSON over stdio and supports generated TypeScript/JSON Schemas tied to the installed Codex version.

At build/package time, generate and pin the installed app-server schemas:

```powershell
codex app-server generate-json-schema --out .\generated\codex-app-server
```

Do not hand-code its complete protocol from this packet. Validate the generated version during startup.

## Availability rule

Codex is optional. The deterministic dashboard, Race Card, reports, charts, planning history, setup packages, and tuning experiment records must remain usable when Codex is absent, signed out, offline, rate-limited, interrupted, or still thinking.

Display local results first. Add AI refinement asynchronously and label it as inferred coaching. Never hide or overwrite measured/derived evidence.

## Startup and authentication

1. Spawn `codex app-server` with stdio redirected.
2. Send `initialize`, then the `initialized` notification.
3. Call `account/read` and show signed-in/offline state without exposing tokens.
4. If needed, use the app-server managed ChatGPT browser or device-code flow. Do not copy `auth.json`, browser cookies, or tokens into this project.
5. Call `model/list` and use a supported user-selectable/default model; do not hard-code a model that may disappear.

The app-server can persist and refresh ChatGPT-managed authentication. The UI should open its returned browser URL or display the device verification URL/code. Credential values never enter companion logs or the deterministic backend.

## Thread model

- Race Analysis: one thread per exact SubSessionID/analysis ID.
- Race Planning: one thread per season/car/exact-layout/setup-scope/race-distance context.
- Starting Tune: one thread per setup package ID.
- Progressive Tuning: resume the package thread and attach each experiment/result analysis.

Persist only thread IDs and workflow keys in app state. Backend archives remain the racing source of truth.

## Prompt and evidence boundary

Provide compact JSON from backend results, not an entire raw IBT or full channel matrix. Include:

- Race Card object and evidence registry.
- Relevant run/corner/strategy/damage summaries.
- Exact driver question or symptom wording.
- Cached comparison quality and setup scope.
- A short list of allowed follow-up backend operations.

The developer instruction for every coaching turn must require:

- Never invent missing telemetry, tire wear, damage component, caution, pit-loss, position, target, or setup parameter.
- Preserve `[M]`, `[D]`, `[I]`, `[P]`, and `[U]` status.
- Return unavailable when evidence is insufficient.
- Never call a repair-affected run clean.
- Never recommend a setup change for fixed sessions or from a damage-confounded test.
- Keep NASCAR coaching direct and corner-by-corner, while remaining discipline-neutral in the data model.

Use `contracts/ai-coaching-output.schema.json` as `turn/start.outputSchema`. The visible deterministic Race Card remains authoritative if AI output fails schema validation.

## Tool access

The safest first release does not give Codex generic shell/filesystem access from the companion app. Supply compact evidence and expose only the iRacing Coach MCP server with its bounded domain tools. Use read-only sandboxing for ordinary coaching. Any future research mode should make network activity explicit and keep Garage61 authentication inside the backend.

## Progress and cancellation

Stream agent-message deltas and turn state into the job tray. `turn/interrupt` cancels the AI turn independently of deterministic analysis. The user may close or navigate away without losing the completed local result.

Do not use `codex exec` as the primary interactive adapter. It remains a reasonable diagnostics/fallback path, while app-server is designed for streamed rich clients. The official [Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk.md) is an alternative wrapper if the chosen coordinator language can use it cleanly.

