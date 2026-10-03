# Rewrite contracts

These files are the only interfaces between workstreams. Code follows them; a workstream that needs a change edits only the contract (one commit touching `docs/08-rewrite/contracts/` and the regenerated files), states the reason in the commit message, and the change is reviewed (Codex Sol 6.1 for protocol and ingest changes, D15) before dependent code lands. Additive optional fields need no review.

| File | Defines | Consumers |
|---|---|---|
| `api.openapi.yaml` | HTTP API, SSE events, collector WebSocket JSON messages, all JSON wire types | server, web, collector |
| `collector-protocol.md` | Collector to server behaviour: identity, presence, upload, acknowledgements, streaming, reconnect, versioning | collector, server |
| `live-frame.md` + `live-channels.json` | Binary live frame layout, channels, cue templates, tile catalog | collector, server (header only), web |
| `nas-layout.md` + `manifest.schema.json` | NAS folder layout, atomic write protocol, manifests, crash recovery, deletion rules | server, migration |
| `db-schema.sql` | Server SQLite schema v1 | server |
| `worker-interface.md` | How the server runs the Python worker; how Rust stages substitute; parity | server, Python worker, port |
| `setup-keys.json` | `CarSetup` keys excluded from setup identity (P5 derived values) | server, collector |
| `crate-api.md` (written in step 0a, `workstreams.md`) | Public Rust types and signatures of `coach-ibt`, `coach-live`, `coach-views` | collector, server, the crates' owners |
| `vectors/live-frame/` (added by W2) | Golden encoded frames with decoded values | collector, server, web |

## Single source of truth for wire types

`api.openapi.yaml` (OpenAPI 3.1, schemas in JSON Schema 2020-12) is the source. Generated, committed, and checked for drift by `tools/check.ps1`:

| Output | Tool | Path |
|---|---|---|
| TypeScript types | `openapi-typescript` (pinned in `package.json`) | `web/src/gen/api.d.ts` |
| Component schemas as one JSON Schema document | `tools/wire/gen-wire.mjs` | `crates/coach-wire/schema/wire.schema.json` |
| Rust types | `typify` (`import_types!` in `crates/coach-wire`) | compiled from `wire.schema.json` |
| Live channel and tile tables | `tools/wire/gen-wire.mjs` | `web/src/gen/live-channels.ts`, `crates/coach-wire/src/gen/live_channels.rs` |
| Setup identity exclusions | `tools/wire/gen-wire.mjs` | `web/src/gen/setup-keys.ts`, `crates/coach-wire/src/gen/setup_keys.rs` |

Command: `pnpm gen:wire` at the repository root. Schemas use only constructs both generators handle: objects, arrays, enums, `type: [T, "null"]` for nullable, `oneOf` with a `kind` discriminator. Unknown optional fields are preserved by readers (`additionalProperties: true` where a backend subtree passes through).

The Python MCP contracts in the top-level `contracts/` remain the oracle's own; the server validates worker results against them where they exist (`analyze-result-v1`, `setup-package-v1`, `tuning-recommendation-v1`).
