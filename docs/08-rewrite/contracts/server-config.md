# Server configuration (v1)

How the image (`deploy/mini-pc/Dockerfile`) tells `coach-server` where things are. The image sets every variable in the first table; the server's defaults equal those values, so a missing variable changes nothing in production. An empty value counts as unset.

## 1. Image environment

| Variable | Image value | Meaning | Default without it (`--dev`) |
|---|---|---|---|
| `COACH_BIND` | `0.0.0.0:8080` | HTTP listener; Compose maps host 8210 to it | `127.0.0.1:8210` |
| `COACH_DATA_DIR` | `/data` | NVMe data root (`architecture.md` section 5) | `dev-data/data` |
| `COACH_NAS_DIR` | `/nas/apps/iracing-coach` | `$NAS` of `nas-layout.md` | `dev-data/nas` |
| `COACH_WEB_DIR` | `/app/web` | the built SPA (contents of `web/dist`); a missing directory disables static hosting | `web/dist` |
| `COACH_PYTHON` | `/usr/local/bin/python3.12` | worker interpreter (`worker-interface.md` section 1) | `python3.12` |
| `COACH_WORKER_SCRIPT` | `/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py` | worker entry point; its directory is the scripts directory the analyzer digest and profile are read from (`worker-interface.md` section 4) | `iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py` |
| `COACH_CONTRACTS_DIR` | `/app/contracts` | the Python MCP contracts that worker results are validated against (`README.md`: `analyze-result-v1`, `setup-package-v1`, `tuning-recommendation-v1`) | `contracts` |

Optional, never set by the image (the native stage settings of section 3 included): `COACH_DEV_DIR` (root of the `--dev` defaults, `dev-data`), `COACH_WORKER_COMMAND` (whitespace-separated command that replaces `nice`, interpreter and script; tests and stub workers), `COACH_SPOOL_LIMIT_BYTES` (spool cap, 20 GB), `COACH_TZ_OFFSET_MINUTES` (local offset for the 04:00 daily snapshot, 0).

## 2. Rules

- Nothing outside the image build context is read at run time or compiled in: no `include_str!` or `include_bytes!` from `docs/` or `test-data/` (`.dockerignore` excludes them). Compiled-in data comes from `server/` and `crates/` only (for example `crates/coach-wire/schema/wire.schema.json`).
- At start, before the listener opens, the server creates under `COACH_DATA_DIR`: `db/`, `spool/incoming/`, `spool/ready/`, `py/`, `views/`, `snapshots/` and `run/`. Once a released server has started, the stack's recovery set is `RECOVERY_PATHS=db snapshots` with `RECOVERY_SQLITE=db/coach.sqlite3`.
- `coach-server healthcheck` connects to `127.0.0.1` on the port of `COACH_BIND`, sends `GET /health`, and exits 0 only on a 200. The Compose healthcheck runs it.
- Secrets never travel in these variables (`owner-decisions-2026-10-02.md`, Still binding).

## 3. Native stages

The worker runs the Rust analysis stages of `worker-interface.md` section 7 when the server tells it to. The server starts every worker with a cleared environment, so these two settings are the only way the stages reach it. Both are read once at start.

| Variable | Default | Meaning |
|---|---|---|
| `COACH_NATIVE_STAGES` | every stage, `decode,kernels,writer`, when the executable below is present; none otherwise | comma list of `decode`, `kernels`, `writer` (spaces ignored, repeats dropped, passed in that order); `none` turns every stage off even with the executable present; `kernels` needs `decode`. An empty value counts as unset |
| `COACH_NATIVE_BIN` | production: `/usr/local/bin/coach-analysis` if that file exists, else `coach-analysis` on the server's `PATH`; `--dev`: `coach-analysis` (`.exe` on Windows) on the server's `PATH` | the `coach-analysis` executable |

The server passes the resolved list as `IRACING_COACH_NATIVE_STAGES` and the resolved executable path as `IRACING_COACH_NATIVE_BIN` (the latter only when the list is not empty). The image therefore needs no interpreter shim: `COACH_PYTHON` is `/usr/local/bin/python3.12` again.

Start-up fails, with the variable named, when a stage list names an unknown stage or `kernels` without `decode`, or when a stage is listed (or `COACH_NATIVE_BIN` is set and the stages are not `none`) and the executable is not there: a worker that silently ran Python would let a release believe it was native. Absent both settings and without the executable the server runs the pure-Python worker.

The setting also selects the admission model (`worker-interface.md` section 6): with `decode` and `kernels` listed, analysis admission uses 5.0 x the recording bytes, a 2.0 GiB per-job ceiling and a 2.7 GiB shared budget; otherwise it uses 11.5 x, 3.0 GiB and 3.7 GiB. The stack's container `mem_limit` is 3 GiB for the first and 4 GiB for the second (`deploy/mini-pc/docker-compose.yml`, W9).
