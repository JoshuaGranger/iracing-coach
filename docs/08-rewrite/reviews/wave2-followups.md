# Wave 2 follow-ups (2026-10-02)

Reconciliation of the contract change requests the wave-1 follow-up agents raised (`wave1-followup-results.md`) and the architect follow-up assigned there, each checked against the code at `615377c` plus the tree. Contract edits: `contracts/README.md`, change log entry "wave-2 reconciliation". `architecture.md` sections 8, 10 and 11 and `risks.md` R21 to R23 are updated. After regeneration `cargo check --workspace`, `cargo test -p coach-server --test contracts --test import` and `pnpm -C web typecheck` pass.

## Dispositions

| Request | Decision | Reason |
|---|---|---|
| Architect: `architecture.md` section 8 | applied | Checked in the manifests and sources: the collector's `src/http.rs`, `ws.rs`, `logging.rs` replace `ureq`, `tungstenite` (a dev-dependency for the mock only) and `tracing`; `coach-ibt` has no `regex`; `server/src/schema.rs` validates requests and worker results with `jsonschema` at run time. |
| Architect: `architecture.md` section 10 | applied | NAS root squash to 1024:100, the first-release healthcheck and `RECOVERY_PATHS=.` with their reasons, from `acceptance/first-release.md` and the deploy files; environment by reference to `contracts/server-config.md`; image and Compose lines corrected to the committed files (slim Node stage, allowlist `.dockerignore`, `init`, grace period, localtime). |
| W4-1 `collector-protocol.md` section 6, driver change | accept, widened | States what `server/src/ws.rs` does. Also stated: `welcome` always carries `stream_wanted: false`, `viewers: 0`, `layout: null`, because demand and layout belong to a driver that only the first `status` names; section 9 sends `layout.set` on driver changes, not on `welcome`. No code changes. |
| W4-2 `worker-interface.md` import row | accept | `legacy_import.py` returns `experiment_count`, `resolved_count`, `unresolved_count` and no skipped count; `server/src/import.rs` already reports the stated keys. |
| W6-1 change-log line for `tools/wsl/check.sh` | accept | Recorded in the wave-2 entry. |
| W6-2 `tools/check.ps1 -LinuxScope` default | accept: `all` | The Linux Python suite is clean (1377 run, OK), so both entry points share one default and `-Linux` checks what section 10 says it checks. |
| W6-3 G2 on `test-data/g2/` | accept | W8's wave-2 task; the harness change is in progress in the tree. |
| W6-4 retire the JavaScript syntax gate in wave 4 | accept, split | The script and its tests are W6's `tools/*.ps1`; the `ci.yml` step goes with W9's removal of the .NET CI job. |
| W6-5 `contracts/compatibility.json` still names .NET, WPF, Blazor | accept | W6's own Python contract; a follow-up below. |
| W7 `crate-api.md` 4.7 `fixed2` row | accept | `format.rs` and `engine.json` use `0.00` for cue arguments and `0.##` only for the steering ratio; the row is split, and fuel's `0.0` estimated laps added with the same rule. |
| W9-1 `draft_identity_key` for imported drafts | accept with change: `legacy_analysis_id` per draft | One resolution path: the draft names its legacy analysis, already listed in `legacy_analyses`, and the server recomputes the key from the resolved analysis exactly as `GET .../tuning` does. Reading `body.legacy.representativeSession` would make the server parse an unspecified legacy body; a separate `subject_ref` would duplicate `legacy_analyses`. |
| W9-2 slot for the worker's `tuning/drafts/` | accept: `legacy_archive` suffices, no conversion | They are write-only records of past recommend calls (`storage.load_tuning_draft` has no caller); the next call writes its own. They stay preserved because the server keeps every bundle at `import/<sha256>.json` and never deletes it (`server/src/import.rs`). |
| W1 note: `coach-wire` links `regress` into `coach-ibt`'s tree | no action | The collector uses `coach_wire` only for `live_channels` tables and builds no pattern-checked wire type; the 1,578,496 B release exe contains no `regress` string (crate paths of `serde_json` and `sha2` do), so the 3 MB budget is untouched. W3's acceptance build measures every change. Recorded in `crate-api.md` section 1, `architecture.md` section 8, R22. |
| W9 note: `config::tests::production_defaults_equal_the_image_values` fails on Windows | resolved before this run | `cargo test -p coach-server --lib config`: 4 passed on Windows. |
| Found in verification: the server matches `setup-keys.json` with `regex` (`server/src/setups.rs`), whose Unicode case folding differs from `coach_ibt`'s ASCII folding | contract tightened (`crate-api.md` 3.6) | One implementation of the setup-identity rules: a key that one side excludes and the other fingerprints would split a setup. W4 follow-up below. |
| Wave-1 architect item 3 (`workstreams.md` step 0b and W4: `jsonschema` is a runtime dependency) | not applied here | `workstreams.md` is outside this task's paths; the fact is recorded in `architecture.md` section 8. |

## W4

1. `server/src/import.rs`: re-key imported tuning drafts (`api.openapi.yaml` `ImportBundle.tuning_drafts`). Build the legacy-analysis map once per import and use it for experiments and drafts. For each draft with a `legacy_analysis_id` that resolves to a session's current analysis with a projected tuning context, compute the key with `tuning::draft_identity_key` from that analysis's identity and binding (the code path of `GET .../tuning`), rewrite `body.open_target_analysis_id` through the map (null when unresolved), and insert under the new key; the first draft in bundle order wins a key. Every other draft becomes a `legacy_items` row (kind `tuning_draft`, body = the bundle item). Summary keys `tuning_drafts` and `tuning_drafts_unresolved`. Proof: a test in `server/tests/import.rs` where a resolved draft is then returned by `GET /drivers/{id}/tuning-drafts/{key}` with the key from `GET .../tuning` for that analysis, an unresolved and a duplicate draft are parked, and the open target is rewritten; `cargo test -p coach-server --test import`.
2. `server/src/setups.rs`: replace the `regex`-based `is_identity_excluded` with `coach_ibt::is_identity_excluded` (`crate-api.md` 3.6) and drop `regex` from `server/Cargo.toml`; the root `regex` pin is then unused and is removed by the same scaffold change through the contract process. Proof: a unit test that `Chassis.Rear.Truc\u{212A}ArmPreload` (Kelvin sign) is not excluded and every contract pattern's literal is; `cargo test -p coach-server`, `cargo clippy -p coach-server --no-deps --all-targets -- -D warnings`, `wsl -d Ubuntu -- bash tools/wsl/check.sh server`.

## W6

1. `contracts/compatibility.json` (through `tools/export_contracts.py`): the application runtime, native host and view layer still read .NET 10, WPF and Blazor Hybrid; describe the rewrite (Rust collector and server, TypeScript SPA, Python 3.12 worker). Proof: `export_contracts.py --check`; `verify-repository.ps1 -PythonPath $py`.
2. Wave 4, before `companion-app/` is deleted: remove `tools/Invoke-JavaScriptSyntaxGate.ps1` and its tests (W9 removes the `ci.yml` step). Proof: `verify-repository.ps1 -PythonPath $py` on a copy without `companion-app/`.

## W9

1. `tools/migrate/build_import.py`: write `legacy_analysis_id` on every `tuning_drafts` item (the draft's `representativeSession.analysisId`, null when absent); keep `identity_key` as the legacy file stem. The D12 report lists `tuning_drafts` and `tuning_drafts_unresolved` from the import summary. Proof: `$py -m unittest discover -s tools/migrate -p "test_*.py"` with a test that each draft's `legacy_analysis_id` appears in `legacy_analyses`; the synthetic bundle validates against `ImportBundle` (ajv).
2. Release the real server (wave-1 W9 item 1; the R23 hold is met), then in the following release set `RECOVERY_PATHS=db snapshots` and `RECOVERY_SQLITE=db/coach.sqlite3` (`architecture.md` section 10). Proof: `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .`; `ssh g-house-pc "sudo -n g-house-stack status iracing-coach"` healthy; `curl -s http://192.168.1.81:8210/api/v1/status` shows `nas.state = ok`.
3. Wave 4: remove the `ci.yml` JavaScript syntax-gate step with the .NET CI job (scaffold change through the contract process). Proof: `tools/check.ps1` green without `companion-app/`.

## No change needed

W1, W2, W5, W7, W8: the decisions above match their code (`format.rs`, `web/src/live/tiles.ts` and `text.ts` already use `0.00` and `0.##` this way). W3: no code change; `record-sdk 30` and G3 with the `--dry-run` build stay blocked on the DESKTOP-SIM remote agent, which is stopped and only Joshua can restart (R21).
