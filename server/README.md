# coach-server

The mini-PC service: HTTP API and SPA hosting, collector WebSocket and live relay, upload and ingest, NAS archiver, job runner for the Python worker, view serving, scheduler and snapshots. Contracts: `docs/08-rewrite/contracts/`; design: `docs/08-rewrite/architecture.md`.

```
coach-server [serve] [--dev]    run the service
coach-server healthcheck        GET /health on the local listener, exit 0 on 200 (Compose healthcheck)
coach-server rebuild-index      rebuild blobs, recordings, setup files and drivers from the NAS manifests
```

`--dev` keeps every directory under `./dev-data` (`COACH_DEV_DIR`) and listens on `127.0.0.1:8210`.

| Variable | Default | Meaning |
|---|---|---|
| `COACH_BIND` | `0.0.0.0:8080` | listener |
| `COACH_DATA` | `/data` | `db/`, `spool/`, `py/`, `views/`, `snapshots/` |
| `COACH_NAS` | `/nas/apps/iracing-coach` | `$NAS`, written only by the archiver thread |
| `COACH_WEB_DIR` | `/app/web/dist` | SPA files (`index.html` is the fallback for client routes) |
| `COACH_WORKER_SCRIPTS` | `/app/iracing-coach/skills/analyze-iracing-race/scripts` | analyzer bundle (digest and profile version) |
| `COACH_PYTHON` | `python3.12` | interpreter |
| `COACH_WORKER_COMMAND` | `nice -n 10 python3.12 -X utf8 -u .../mcp_server.py` | whole worker command, whitespace separated |
| `COACH_SPOOL_LIMIT_BYTES` | 20 GiB | spool reservation limit |
| `COACH_TZ_OFFSET_MINUTES` | 0 | local-day offset for the 04:00 NAS backup |

Tests: `cargo test -p coach-server`. They need a Python 3 for the stub worker (`COACH_TEST_PYTHON`, else `python3`, else `python`); without one the worker tests print `skipped`. The real worker test also needs `iracing-coach/` and `test-data/ibt/synthetic-race.ibt`. Fault points for the crash tests are in `src/faults.rs`.
