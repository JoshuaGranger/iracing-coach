# coach-server

The mini-PC service: HTTP API and SPA hosting, collector WebSocket and live relay, upload and ingest, NAS archiver, job runner for the Python worker, view serving, scheduler and snapshots. Contracts: `docs/08-rewrite/contracts/`; design: `docs/08-rewrite/architecture.md`.

```
coach-server [serve] [--dev]    run the service
coach-server healthcheck        GET /health on the local listener, exit 0 on 200 (Compose healthcheck)
coach-server rebuild-index      rebuild blobs, recordings, setup files and drivers from the NAS manifests
```

`--dev` keeps every directory under `./dev-data` (`COACH_DEV_DIR`), uses `python3.12` and the repository's `contracts/`, and listens on `127.0.0.1:8210`.

The environment is the one `docs/08-rewrite/contracts/server-config.md` defines; the image sets every variable in the first table, and the production defaults equal those values. An empty value counts as unset.

| Variable | Default (production) | Meaning |
|---|---|---|
| `COACH_BIND` | `0.0.0.0:8080` | listener |
| `COACH_DATA_DIR` | `/data` | `db/`, `spool/`, `py/`, `views/`, `snapshots/`, `run/` (created at start) |
| `COACH_NAS_DIR` | `/nas/apps/iracing-coach` | `$NAS`, written only by the archiver thread |
| `COACH_WEB_DIR` | `/app/web` | SPA files (`index.html` is the fallback for client routes); a missing directory disables static hosting |
| `COACH_PYTHON` | `/usr/local/bin/python3.12` | worker interpreter |
| `COACH_WORKER_SCRIPT` | `/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py` | worker entry point; its directory is where the analyzer digest and profile version are read |
| `COACH_CONTRACTS_DIR` | `/app/contracts` | the Python MCP contracts that worker results are validated against (`analyze-result-v1`, `setup-package-v1`, `tuning-recommendation-v1`); a mismatch fails the job as `tool_error` |

Optional: `COACH_DEV_DIR` (root of the `--dev` directories, `dev-data`), `COACH_WORKER_COMMAND` (whitespace-separated command that replaces `nice`, interpreter and script), `COACH_SPOOL_LIMIT_BYTES` (20 GiB), `COACH_TZ_OFFSET_MINUTES` (0, local-day offset for the 04:00 NAS backup).

Tests: `cargo test -p coach-server`. They need a Python 3 for the stub worker (`COACH_TEST_PYTHON`, else `python3`, else `python`); without one the worker tests print `skipped`. The stub builds its results from the sanitized fixtures in `test-data/`, so they meet the Python contracts. The real worker tests also need `iracing-coach/` and `test-data/ibt/synthetic-race.ibt`. Fault points for the crash tests are in `src/faults.rs`; `tests/crash.rs` runs the server in a child process and ends it at each point with `FaultMode::Kill`, then checks everything it had acknowledged before any retransmission.
