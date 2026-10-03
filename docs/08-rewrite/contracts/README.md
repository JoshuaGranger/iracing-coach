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
| `crate-api.md` | Public Rust types, signatures and error enums of `coach-ibt`, `coach-live`, `coach-views`, the generated `coach-wire` items they use, crate ownership and dependencies | collector, server, the crates' owners, scaffold (step 0b) |
| `server-config.md` | Environment the image sets for `coach-server`, directories it creates at start, the `healthcheck` subcommand | server, deploy |
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

## Change log

Each contract change adds one entry here in the same commit; review per the rule at the top.

### 2026-10-02, step 0a: shared interfaces (architect; review: Codex Sol 6.1 with the plan, D15)

Reason: workstreams that never read each other's code need field-level types, not function names (`workstreams.md` step 0a).

- `crate-api.md` added: `coach_ibt` (SdkHeader, VarTable and readers, IbtFile, RecordingSummary, SessionInfo with WeekendInfo, SessionEntry, PlayerDriver, DriverSetup, CarSetup, setup fingerprint, finalization gate, streaming hash, IbtError), `coach_live` (SessionContext, Sample with every SDK variable the engine reads, Engine, LiveState mirroring live-channels table 1, frame codec, tile formatting, FrameError), `coach_views` (project, ProjectionSummary, view files, select_traces, ProjectError, SelectError), the generated `coach_wire` item names, crate owners and dependencies. Reader-side normalisations of the C# SDK source move into the engine so vectors cover them. Scaffold adds `regex` to workspace dependencies.
- `api.openapi.yaml`:
  - `TuningContext.identity` and `.map` typed as `TuningSessionIdentity` and `TuningMapView` (field by field from C#). C# `RaceId`, `EventKey`, `Selector` become the server-bound `TuningSessionBinding`; `AnalysisPath` is dropped (server-internal). `TuningContext` = projector-written `TuningContextProjection` + `binding`, typed `open_targets` (`TuningOpenTarget`), `draft_identity_key` (formula stated); `can_use_as_evidence` added; `representative_runs` typed as `BackendRun` with the C# eligibility rule.
  - `TuningRecommendRequest.map_identity` typed as `TuningMapSubmission` without its hashes: the server adds `geometry_hash` and `annotation_hash` (Python float formatting stays server-side). `feedback` typed (`TuningFeedbackItem`), `representative_run_ids` max 3, `goal` and `ruleset_id` enums, all to match the worker's closed input schema (`mcp_server.py:700-769`); these request objects are therefore closed (`additionalProperties: false`), an intended exception to round-tripping.
  - `x-consumed-fields` on every `x-source` object (convention in `info.description`), plus `Lap`, `TraceLap`, `SteeringBasis` (now `x-source`), and the new shared `BackendRun`. `field_pace`, `conditions`, `corner_tire_age`, `tire_learning` and geometry provenance are read by no v1 screen (empty lists).
  - `TrackGeometry` corrected: the backend geometry is `normalized_local_vector` (`race_foundations.py:528`), not metres; it is copied without reprojection and gains `status`, `track_configuration_key`, `geometry_hash`, `transform` (GPS to geometry space, needed to draw recorded laps on the map), `quality`, typed `start_finish`, `pit_commitment_line`, `pit_merge_line`. `PathSeries.pct` items may be null.
  - `TraceCatalog.sector_start_pcts` added (Traces sector bands).
  - Fixed a YAML defect: the `q` parameter description on `/api/v1/drivers/{customer_id}/sessions` was unquoted and split into stray keys.
- Proof: the file parses (js-yaml), validates as OpenAPI 3.1 (`@apidevtools/swagger-parser`), every component schema compiles under JSON Schema 2020-12 (ajv), every `$ref` resolves, and every `x-source` object carries `x-consumed-fields`.

### 2026-10-02, step 0a review resolution (architect; review: Codex Sol 6.1, `reviews/crate-api-review-codex-sol.md`, all 15 findings accepted)

Reason: the review found defects in the step-0a interfaces; each was checked against the C# or Python source before it was accepted (disposition table in the review file).

- `crate-api.md`:
  - `coach_ibt` depends on `serde` (derive); `coach_live` public types derive no serde traits.
  - Readers keep present non-finite values raw (`VarTable` scalar and array readers, `edge_scalar`, `Sample`); outputs never carry them. C# `??` fallbacks select by presence (`SessionLapsRemainEx`, `TrackTempCrew`, `BrakeABSactive`), and Python's edge read returns the last record's NaN.
  - IBT header text follows Python `_decode_c_string`; live header text follows C# `Encoding.ASCII`.
  - `RecordingSummary.start_time_utc` and `end_time_utc` are `Option<String>` (`_safe_iso`).
  - Quoted and flow YAML values are evaluated as Python `ast.literal_eval` over a stated subset; `YamlNode::Literal` added; other literal kinds are a YAML error.
  - Frame codec: `FrameError::TooManyChannels`; `BadCue` carries its reason (truncated, k > 4, unknown id, priority or evidence); the collector carries the epoch marker to the next sent frame.
  - `TileText.min` and `max`, `display_range` (C# `ApplyRange`), and the per-tile missing texts (Waiting, Insufficient evidence, Ratio not recorded, Not measured).
  - Projection shrink empties unconsumed `x-source` subtrees to null or `{}` by schema.
- `api.openapi.yaml`: `Lap` and `TraceLap` replace the undefined `clean` with required `comparison_eligible` and `exclusion_reasons` (C# rule, order, join and unmatched case stated); `TuningOpenTarget` states the full `CompatibleOpenTarget` order and presence rules; the track endpoint summary names `normalized_local_vector`.
- `live-frame.md`: bit4 is a hint (receivers compare `session_epoch`); `channel_count` above the decoder's table is rejected; cue `k <= 4` and discriminant checks.
- Regenerated: `pnpm gen:wire` (`web/src/gen/api.d.ts`, `crates/coach-wire/schema/wire.schema.json`).

### 2026-10-02, wave-1 reconciliation (integration; review: Codex Sol 6.1 for the protocol and ingest items, D15)

Reason: the wave-1 contract change requests (`reviews/wave1-results.md`, W1 to W9) and the behaviour-defining findings of the rerun ingest review (`reviews/ingest-review-codex-sol.md`). Dispositions, with the code each follow-up needs, are in `reviews/wave1-followups.md`. `db-schema.sql` and `manifest.schema.json` are unchanged: setup-file links are derived, not stored, so migration 0001 stays equal to the schema.

- `live-channels.json`: every cue but 14 carries its C# basis (W2, checked against `LiveTelemetry.cs`); new `unit_labels` (`C` to `°C`, `F` to `°F`, `m/s2` to `m/s²`, `lap` to `laps`); layouts store unit ids. Table version unchanged (no channel appended). Generator output for `unit_labels` is a follow-up.
- `live-frame.md`: presence bit order (bit `i % 8` of byte `i / 8`, least significant first); absent `session_time` written as quiet NaN `0x7FF8000000000000`, any NaN read as absent; text rules referenced to `crate-api.md` 4.7.
- `vectors/live-frame/` (W2, wave 1): golden frames from an independent Python encoder; this entry records their addition.
- `crate-api.md`: `coach_ibt` drops `regex` (collector exe budget; W3) in favour of hand-written token matchers and a setup-key pattern subset; `parse_live` bounds; `_as_int` overflow is None; `driver()` versus `summary()` when `DriverCarIdx` is missing; `- Key:` list items become empty maps; literal-subset errors and i64 overflow; `TooRecent { age: 0 }` for a future mtime (W1). generated `coach_wire::live_channels::unit_label`; `LiveState::channel_values`; `tow_active` and `on_pit_road` presence; `first_of_epoch` on the incrementing state; the C# disconnect-epoch difference; `format_tile` unit and label rule and the .NET text-rule table (W2). Projector input shapes, envelope `schema_version` 1, `profile_version` "" for null, field pruning in the shrink ladder, 128-signal catalog cap with `omitted_signals`, `X-Coach-Trace-Laps` (W5).
- `setup-keys.json`: patterns limited to a subset matchable without a regex engine; patterns themselves unchanged, so every fingerprint is unchanged.
- `api.openapi.yaml`: `ServerStatus.collector_newer_than_server` (W4); compare inline wait is a server setting; tuning context carries no ETag (W4); `ViewEnvelope` version descriptions (W5); `TraceCatalog.signals` maxItems 128, `omitted_signals`, fixed-signal list, bin positions `(i + 0.5) / bins` (W5, W8); CCOL data section alignment stated as a file offset (W8); `Lap.caution`, `Lap.incident_count`, `TraceLap.fastest` removed (no backend source; the browser derives the fastest lap from `lap_time_s`) and `Lap` sources named (W5); `SetupFileEntry.linked_setup_id` derived (W4); `SetupObservation.analysis_id` (W7); `TuningDraftBody` and the turn-annotation `corners` body (W7); `PUT prefs` is a top-level merge (W7); `LiveTile.unit` is a unit id (W2); `ImportBundle.tuning_experiments` replaced by `legacy_analyses` and `legacy_archive` so the server can run W6's `import_legacy_tuning_experiments` (W4, W6); 413 on traces explained.
- `collector-protocol.md`: `hello` lists `live_table_version`; `status.sim` defined; garage edge rules (5 s post-exit snapshot, no-op visits, synthesized snapshot on a name-only change, `.sto` queueing, re-entry closes the old visit before any snapshot) (W3, review 13); "no map" means the mapping does not exist, any other open failure is unknown evidence (review 3); recovery fsyncs before `verified` (review 1); latch set in every stream session while demand stands, demand recounted on a driver change (review 16, 17); setup-file links derived from observations, never by name for an ambiguous visit (W4, review 14); garage events acknowledged only once attributed and stored (review 15).
- `worker-interface.md`: discovery key is `group_id` and `local-file` groups are `time_source == "mtime"` (W4, W6); `raw_source_policy` stays an object in external mode (W6); `HOME` required, `USERPROFILE` on Windows dev (W4, W6); new tool row `import_legacy_tuning_experiments`; the server-derived analyzer digest and profile for the analyze key (W4); section 5 marked landed; cross-platform parity also normalises separators after the root prefixes and compares SQLite as row dumps (W6).
- `nas-layout.md`: restage goes through upload admission and restores the clamped source mtime before `ready/` (review 8, 9).
- `server-config.md` added: the image's environment names (W9's, which the released image already sets), defaults, start-up directories, `healthcheck` (W9 CCR 1-2).
- Proof: `api.openapi.yaml` parses (js-yaml), validates as OpenAPI 3.1 (`@apidevtools/swagger-parser`), all 113 component schemas compile under JSON Schema 2020-12 (ajv), every `$ref` resolves, every `x-source` object carries `x-consumed-fields`; `pnpm gen:wire` and `pnpm gen:theme` regenerated; `cargo check --workspace` passes.
