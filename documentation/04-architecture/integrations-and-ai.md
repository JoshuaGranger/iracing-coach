# Integrations and AI

External services enrich the product but do not define the critical local path.

## Garage61

| ID | Requirement |
| --- | --- |
| `G61-001` | Garage61 configuration MUST be a Connections workflow with save, replace, remove, and connection-test behavior. Settings contains ordinary application preferences and subordinate diagnostics. |
| `G61-002` | The UI MUST never display the complete stored credential after entry. |
| `G61-003` | A failed, unauthorized, rate-limited, or unavailable response MUST identify the state and a useful remedy without breaking local analysis. |
| `G61-004` | Garage61 data MUST identify provider provenance, retrieval time, scope, and alignment fitness. |
| `G61-005` | External reference laps or setups MUST NOT be treated as the user's recorded facts. |
| `G61-006` | The adapter MUST obey provider authorization, API terms, rate limits, and supported endpoints; scraped or invented data is forbidden. |

The backend includes a Garage61 adapter and tests. End-to-end behavior with a real authorized account is environment-dependent and is not established solely by fixture tests.

## Integrated Coach Engine

| ID | Requirement |
| --- | --- |
| `AI-001` | The shipped product MUST require no separate agent setup for deterministic analysis. |
| `AI-002` | Deterministic outputs MUST remain available without an AI account. |
| `AI-003` | AI-generated interpretation MUST be grounded in a bounded evidence packet and MUST preserve recorded/derived/unknown distinctions. |
| `AI-004` | The AI MUST NOT modify source telemetry or silently overwrite settings, setups, reports, or tuning history. |
| `AI-005` | Any tool capable of writing durable state MUST have an explicit contract, constrained path scope, validation, and audit trail. |
| `AI-006` | Prompt, model, tool-contract, evidence-packet, and response versions SHOULD be retained when necessary to reproduce a recommendation. |
| `AI-007` | Service unavailability, authentication failure, refusal, or malformed output MUST fall back to deterministic behavior. |
| `AI-008` | AI wording MUST NOT raise confidence above the underlying evidence or invent exact targets. |

## Reality status

The current coordinator can start and probe the bundled deterministic coach engine. Optional Codex/OpenAI coaching is not part of the locally accepted critical path. A capability label such as "available" is insufficient evidence of a complete, authenticated, safe end-to-end AI workflow.

Implementation references: `garage61_client.py`, `Garage61CredentialStore.cs`, `CoachEngine.cs`, `AI_ORCHESTRATION.md`, and `contracts/mcp-tools.v1.json`.
