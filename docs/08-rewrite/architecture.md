# Rewrite architecture (v1)

Authority: `owner-decisions-2026-10-02.md` (D1 to D15, Prove-wave rulings, Still binding), refined by `prove/verdicts.md`. Measured inputs: `spikes/*.md`, `prove/P1` to `P5`. Interfaces: `contracts/`. Plan: `workstreams.md`. Risks: `risks.md`.

## 1. System

```
 Sim PC (Windows)                     Mini PC (Docker, stack iracing-coach)          NAS 192.168.1.82
 ┌─────────────────────────┐  WS     ┌──────────────────────────────────────┐ NFS  ┌──────────────────┐
 │ coach-collector.exe     │────────▶│ coach-server (Rust)                  │─────▶│ /volume1/apps/   │
 │  SDK reader + live eng. │ presence│  HTTP API, SPA files, live relay     │ only │  iracing-coach/  │
 │  overlay + tray         │ stream  │  ingest, archiver, job runner        │writer│  raw/ setups/    │
 │  garage capture         │  HTTP   │  SQLite (NVMe)                       │      │  backups/        │
 │  upload queue (journal) │────────▶│  python3.12 worker (process per job) │      └──────────────────┘
 └─────────────────────────┘ uploads └──────────────────────────────────────┘
          ▲ reads only                       ▲ HTTP + SSE + WS (no auth, LAN/tailnet)
 Documents\iRacing (telemetry, setups)   Browsers: desktop, rig, tablet, phone (TypeScript SPA)
```

The collector dials out; the server never dials the rig. The server is the only writer of the NAS folder, of SQLite and of derived data. Raw recordings live on the NAS (D4 ruling); SQLite and derived data live on the mini PC NVMe.

## 2. Components

### 2.1 Collector (sim PC, Rust, no .NET, no WebView)

One per-user process, `coach-collector.exe`, started by an HKCU Run value. Single instance (`Local\iRacingCoach.Collector` mutex).

- **SDK reader.** Opens `Local\IRSDKMemMapFileName` read-only and waits on `Local\IRSDKDataValidEvent` (100 ms timeout) instead of the retired 2 ms poll (500 wakeups/s); falls back to a high-resolution waitable timer at 0.8 x tick period if the event is absent. Torn-read guard: copy the newest buffer, re-read its tick, retry once if it changed. Session info is re-parsed when the header update counter changes; data counts as live only with status bit 0 set and an advancing tick (P5: stale maps persist with status 0). Disconnected: probe the map once per second.
- **Live engine** (`crates/coach-live`): the C# rules in `spikes/live-stack.md` section 2 (truth policy, clean laps, pace band, fuel, gaps, braking cue, the 13 cues, safe-glance hold), driven on the reader thread at the SDK tick. Output: one `LiveState` per tick, encoded as a live frame (`contracts/live-frame.md`) when streaming and handed to the overlay.
- **Overlay.** Win32 layered window, `WS_EX_LAYERED | TOPMOST | TRANSPARENT | NOACTIVATE | TOOLWINDOW` (proved in P3), GDI drawing, repaint at most 20 Hz and only when dirty. Click-through by default; tray "Move monitor" lifts click-through until the next click outside. Layout comes from the server (`layout.set`) and the last acknowledged layout is cached locally; placement and scale are machine-local.
- **Tray.** Status line, show/hide monitor, Move monitor, Pause coaching cue, Open Coach (browser), Set server address, Exit. Optional global hotkey (same rules as today).
- **Recording watcher.** `ReadDirectoryChangesW` on `Documents\iRacing\telemetry` and `...\setups`, plus a full scan at start. Finalization gate (copied from `workflow.py:1254-1295`): record count > 0, file size equals buffer offset + count x buffer length, 2 s quiet, and the file opens with read sharing. Recordings are uploaded from where iRacing wrote them; the collector makes no copies (no extra disk writes on the sim). It never writes, renames or deletes anything under `Documents\iRacing`.
- **Uploader.** Resumable hash-verified HTTP upload (`contracts/collector-protocol.md` section 4). Uploads, hashing and folder scans run only with fresh off-track evidence (iRacing not running, or live data with `IsOnTrack == 0`; stale or unknown counts as on track); they re-check before every 4 MiB chunk or block and abort when the evidence lapses. Thread I/O priority background; no file work on the SDK reader thread. A journal (`%LOCALAPPDATA%\iRacingCoach\journal.jsonl`) holds pending recordings and every garage event; a recording entry is dropped when the server acknowledges `verified` (fsynced on the server), a garage entry on `garage.ack` (committed in SQLite).
- **Garage capture** (D9, P5 two-moment rule): while `IsInGarage == 1`, every `CarSetup.UpdateCount` change records a value snapshot; when `DriverSetupName` changes at garage exit, the collector hashes `setups\<car>\<name>` on the uploader thread and commits the visit (last snapshot gets the name and hash; earlier snapshots stay "values seen, name unknown"). Each event is journaled, then sent over the WebSocket, and kept until `garage.ack`. A modified commit never defines the named `.sto`'s values.
- **Server link.** One WebSocket: hello, presence, ingest state, stream control, layout sync, binary live frames.

### 2.2 Server (mini PC, Rust)

One binary, `coach-server`, in one container with the Python worker. Subsystems:

| Subsystem | Responsibility |
|---|---|
| HTTP API (`/api/v1`) | Drivers, sessions, views, setups, tuning, notes, layouts, jobs (`contracts/api.openapi.yaml`). Serves the SPA's static files. |
| Events | `GET /api/v1/events` (SSE): presence, recording and job state, analysis ready. |
| Collector hub | `/ws/collector`: presence, ingest state, stream control, layout push, live frame intake. |
| Live relay | `/ws/live/{driverId}`: fans frames out to browsers at the rate each asks for (1, 10, 30 or 60 Hz); keeps a 10 min, 10 Hz backlog per driver while streaming; counts viewers. |
| Ingest | Upload receive into `/data/spool`, hash verify, then IBT header and session-info parse (`crates/coach-ibt`): driver identity, session metadata, `CarSetup` values. The setup library fills from this step alone (D9), before any analysis. |
| Archiver | The only code that touches `/nas`: copies spool to NAS (`contracts/nas-layout.md`), restages NAS raw files for re-analysis, writes backups. Runs on a dedicated OS thread over a `soft` NFS mount, so an unreachable NAS stalls archiving only and its calls fail after about 45 s instead of blocking shutdown. |
| Job runner | Two lanes, each one job at a time: **analysis** (`analyze_iracing_race`, then view projection) and **light** (discovery, setup compare, Find a baseline, tuning, `.htm` parsing). Interactive light jobs jump the queue. |
| Projector (`crates/coach-views`) | After each analysis, reads `analysis.json` once and writes bounded view files (race, runs, investigation, trace catalog, traces as columnar binary, track geometry) keyed by `analysis_id` and projection version. Copies backend values; computes no telemetry math. |
| Store | SQLite (`contracts/db-schema.sql`), one writer thread, WAL, small read pool. |
| Scheduler | Hourly user-data snapshot (only when `user_data_generation` changed) as an immutable generation under `/data/snapshots/` with the light lane held, and daily to NAS `backups/` (`contracts/nas-layout.md` section 5); snapshot on SIGTERM. |

### 2.3 Python worker (v1 production analysis, permanent oracle)

`python3.12 -X utf8 -u iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py`, one process per job, fed JSON-RPC on stdin (`contracts/worker-interface.md`). Roots on NVMe: a job root `IRACING_COACH_IRACING_ROOT=/data/py/<customer_id>/jobs/<job_id>` made of hard links (the job's recordings in `telemetry/`, the driver's received setup files in `setups/<car>/`), deleted after the job; and a per-driver archive `IRACING_COACH_DATA=/data/py/<customer_id>/archive`. No PyYAML. Raw copy into the archive is switched off (recordings live on the NAS).

### 2.4 NAS

NFSv4.1 export `/volume1/apps` (admits 192.168.1.81 only, no credentials); the container sees only its own folder, `/nas/apps/iracing-coach/`, a host automount (section 10), with content-addressed `raw/`, `setups/`, and `backups/`. Layout and atomic protocol: `contracts/nas-layout.md`. The sim PC needs no NAS access.

### 2.5 Browser UI (TypeScript SPA)

Static files served by `coach-server`. Pages: Drivers (redirects when there is one driver), Sessions library, investigation workspace (Race, Runs, Sections, Traces), Setups (library, compare, Find a baseline, Progressive Tuning), Live (dashboard and the rig overlay's layout editor). The JS cursor, map, inspection, trace-layout and live modules carry over from `companion-app/src/iRacingCoach.UI/wwwroot` (deleted in wave 4; in Git history) with their `dotnetRef` callbacks replaced by TS functions; chart geometry (`TelemetryWorkspace.razor:1632-2425`) is ported to TS as pure functions of the 160-bin traces. Pointer movement never causes a request. Loaded analyses stay in memory (at most 3, 96 MB) and remain inspectable when the server drops; writes go through a visible pending outbox (localStorage) with `If-Match` versions, a persisted `Idempotency-Key` per queued write (a retried create returns the original result), and explicit conflict choice. No service worker (the site is plain HTTP on LAN and tailnet, not a secure context; clipboard is avoided for the same reason).

## 3. Main flows

1. **Recording.** iRacing finishes an `.ibt` -> collector gate passes -> car not on track -> `POST /api/v1/uploads` (idempotent by SHA-256) -> chunks -> server verifies the whole-file hash, fsyncs, sets the spool file's mtime to the original, clamped to at most now - 10 s (Python's 2 s quiet rule reads mtime) -> `verified` -> parse header and session info -> driver, setup observation -> queue `discover` (light) and `archive` -> discovery groups the recording into session rows (`SubSessionID + sim_session_num`; a reconnected race joins its existing row) -> per new or changed session, queue `analyze` over all its recordings (job keyed by the membership digest; a finished analysis becomes current only if the membership still matches) -> projection -> SSE `analysis.ready`. The spool copy is deleted once archived and no queued job needs it.
2. **Garage capture.** Collector `garage.snapshot` / `garage.commit` -> server `setup_observations` and `garage_visits` -> collector uploads the `.sto` by hash -> setup library links name, hash and values.
3. **Live.** Browser opens Live -> viewer count 0 -> 1 -> `stream.start` -> collector sends binary frames -> server fans out. Viewers drop to 0 -> `stream.release`; the collector streams until the stream session ends (sim disconnect, subsession, session number or driver change; not an engine epoch reset), then stops (D5; `contracts/collector-protocol.md` section 6).
4. **Review.** Browser loads the session list, then one analysis's view files (immutable URLs, cached), then works locally.

## 4. Process and thread model

| Process | Threads / tasks |
|---|---|
| `coach-collector.exe` | reader+engine (time-critical, normal priority, event-driven); UI (overlay, tray, hotkey message loop); net (blocking WebSocket send/receive, bounded 30-frame send queue); uploader (background I/O priority, one upload at a time); watcher (directory events, gate timers). Channels between them are bounded; the reader never blocks on another thread. |
| `coach-server` | tokio runtime, 2 worker threads: HTTP, SSE, WebSockets, job supervision. SQLite writer thread (all writes serialized). Archiver thread (all `/nas` I/O). `spawn_blocking` for hashing, IBT parsing and projection. |
| Python worker | At most two concurrently: one analysis-lane process, one light-lane process, both admitted through one shared memory budget (`contracts/worker-interface.md` section 6). `nice 10`, `oom_score_adj 1000`, killed by process group on timeout or cancel. |

## 5. Data stores

| Store | Location | Writer | Contents | Protection |
|---|---|---|---|---|
| iRacing files | sim `Documents\iRacing` | iRacing; collector creates new managed setup copies only | `.ibt`, `.rpy`, `.sto`, `.htm` | sources never replaced/deleted; managed `Coach Synced` copies follow `contracts/nas-sync.md` |
| Collector state | sim `%LOCALAPPDATA%\iRacingCoach\` | collector | `collector.json` (server address, collector id, placement, cached layout), `journal.jsonl`, `logs\` (2 x 5 MB) | rebuildable except placement |
| Spool | mini PC `/data/spool/{incoming,ready}` | server | temporary upload and active analysis/restage bytes | 20 GiB including open reservations; archived large payloads released, queued work restages on admission |
| SQLite | `/data/db/coach.sqlite3` | server writer thread | index, ingest state, jobs, setup library, all user-authored data | release recovery point (backup API), hourly snapshot, daily NAS copy |
| Python archives | `/data/py/<customer_id>/archive` | worker | `reports/` (derived), `history.sqlite3` (includes user tuning experiments), `tuning/`, `trials/` | user parts in every snapshot; reports re-derivable |
| View files | `/data/views/<analysis_id>/p<projection>/` | projector | bounded view payloads | re-derivable from `analysis.json` |
| Snapshots | `/data/snapshots/latest/`, NAS `backups/` | scheduler | SQLite (backup API) and Python user data | NAS daily copies are never pruned (each is a few MB) |
| Library | NAS `iracing-coach/library/{telemetry,replays,setups}/` | archiver; deliberate user share actions | one canonical payload per kind/hash, distinct setup versions and durable catalog | legacy raw/setup payloads relocate without copying; sim deletion retained; share deletion remembered |
| Browser | memory, localStorage | SPA | loaded analyses, pending writes, view state | per device |

## 6. When a box is down

| Down | Collector | Server | Browser |
|---|---|---|---|
| Sim PC off | n/a | full review of existing data; Live indicator off | normal |
| Collector stopped | iRacing unaffected; no overlay; garage events in that window are lost; on restart the startup scan uploads every recording the server lacks (server answers `have` for the rest) | normal | Live off |
| Mini PC or LAN down | capture, overlay, live engine and garage capture continue; uploads and events wait in the journal; reconnect backoff 1 s to 30 s with jitter | n/a | loaded data stays inspectable; writes stay visibly pending; banner |
| NAS down | unaffected | uploads continue into the spool up to 20 GB; analysis of new recordings proceeds; re-analysis of archived-only recordings and NAS backups wait; status shows `nas: unreachable` or `stalled`; archiver calls fail after about 45 s (soft mount) and retry, so shutdown and release recovery are not blocked | normal; affected jobs show waiting |
| NAS down when the stack starts | unaffected | the stack starts: the container holds only the host's automount until the first NAS access (section 10), so status shows `nas: unreachable` or `stalled` as in the row above and the 60 s probe retries the mount; measured with the automount idle, not yet proved against a real outage (`risks.md` R3 closed) | normal; affected jobs show waiting |
| Python worker crash or memory kill | n/a | job `failed` (`worker_oom`, `worker_crash`, `timeout`); server keeps running (worker has `oom_score_adj 1000`); `interrupted` jobs retry once | job shows failed with reason |
| Tailnet down | n/a | n/a | guests cannot connect; LAN unaffected |

## 7. Resource budgets

| Budget | Value | Basis |
|---|---|---|
| Collector RSS | <= 32 MB | P3 Rust prototype 8.6 MB without engine |
| Collector CPU on track | <= 1.5 % of one core mean, streaming at 60 Hz | P3: 0.65 % mean, 1.14 % streaming, without engine |
| Collector disk on track | 0 bytes except log lines and garage-commit journal entries (< 1 KB each), together <= 64 KB per session; no `.ircr2`, no copies; no file reads for hashing or scanning | P4 (no bulk on track), live-stack section 5 |
| Collector network on track | live stream <= 32 KB/s per stream at 60 Hz; presence <= 0.5 KB/s; no uploads | P3: 17 KB/s at 295 B/frame |
| Collector off track | uploads at link speed (measured ceiling 6 to 8 MB/s), hashing at background priority | P4 |
| Sim impact (rig gate) | see `workstreams.md` W9 gate G4 | P3 noise floor |
| Container `mem_limit` / `memswap_limit` | 4 GiB / 4 GiB (no swap; host swap is full) | P1 peak 2.27 GiB + server <= 0.3 GiB + light lane; ruling: >= 3 GiB. Not a sum of worst cases: every worker job is admitted through the shared budget below |
| Shared worker budget | running jobs' declared estimates + 0.3 GiB server <= 3.7 GiB; a job that does not fit waits (tuning with a target loads two `analysis.json`, about 1.6 GiB, so it waits for a large analysis) | `contracts/worker-interface.md` section 6; G1 measures the whole-container peak with overlapping work |
| Analysis admission | estimated peak = 11.5 x file bytes + 0.15 GiB (x 1.5 for multi-recording groups until G1 measures one) must be <= 3.0 GiB (about 250 MB single recordings); larger are `deferred: exceeds_memory_budget` until the port lowers the factor by measurement | P1: 2.273 GiB at 203.2 MB, one file |
| CPU | `cpus: 2.0`, `cpu_shares: 256`, worker `nice 10`; one analysis job at a time | P1 used CPUWeight 20; HA untouched; Frigate share preserved |
| Analysis wall time | timeout 10 min; expected <= 30 s per 203 MB | P1 13.0 s (Python 3.11); 3.12 re-measured at first release |
| Session end to ready | <= 2 min for a 203 MB race at the measured LAN ceiling | upload about 35 s + discovery + 13 s analysis + projection |
| Server RSS (idle) | <= 128 MB | budget |
| View payloads | race <= 2 MiB, investigation <= 4 MiB, traces <= 16 MiB per request | ui-data section 1.4 budgets |
| NVMe | spool <= 20 GB; derived data monitored (alert at 50 GB) | 360 GB free |
| Startup | healthy within 30 s (release tool waits 120 s); migrations additive only | infra section B |

## 8. Technology choices

| Choice | Why |
|---|---|
| Rust 1.99 (MSVC on Windows, glibc Linux in the image) | D2/D5; one language for collector and server; P3 footprint |
| `windows` crate (collector) | Direct Win32: memory map, events, layered window, tray, GDI; proved in P3 |
| Collector's own HTTP/1.1 client, WebSocket client (RFC 6455) and file logger over `std` (`collector/src/http.rs`, `ws.rs`, `logging.rs`); no `ureq`, `tungstenite`, `rustls` or `tracing` in the exe | Plain `http`/`ws` only (D7); the uploader needs gate-checked writes to a non-blocking socket to abandon a chunk within 100 ms; the 3 MB exe budget (1.58 MB at wave 1). `tungstenite` is a collector dev-dependency for the test server mock only |
| `tokio` + `axum` + `tower-http` (server) | Mature async HTTP, WebSocket, SSE, static files and compression |
| `rusqlite` (bundled SQLite) | Mature, no system library, WAL, backup API |
| `serde`, `serde_json`, `sha2`; `thiserror` in the crates | Standard, mature |
| `tracing` + `tracing-subscriber` (server only) | Structured logs to stdout (Compose json-file); the collector keeps its own logger for size |
| `jsonschema` (server at run time; dev-dependency elsewhere) | The server validates request bodies against `wire.schema.json` (compiled in from `crates/coach-wire`) and worker results against the Python MCP contracts in `COACH_CONTRACTS_DIR`; crate and collector tests validate outputs against the same schemas |
| `typify` (build time) + `regress` | Generates Rust wire types from the contract's JSON Schema; `regress` is the pattern engine its generated code calls for `pattern` constraints. It is in the collector's dependency tree through `coach-wire`, but the collector constructs no wire type and its release exe holds none of it |
| No `regex` in `coach-ibt`, `coach-live`, `coach-views` | What they link reaches the collector; YAML and literal tokens use hand-written matchers and setup-key patterns a subset matcher (`contracts/crate-api.md` sections 1 and 3.6) |
| Python 3.12 (stdlib only) | D2 ruling: v1 worker and oracle; 3.12 for compensated `sum()` |
| TypeScript 5 + Vite + Preact | D3; Preact keeps the bundle small for phones; the existing JS modules are framework-free |
| `openapi-typescript` | Generates TS wire types from `contracts/api.openapi.yaml`, types only, no runtime |
| Vitest + Playwright | Unit tests; browser end-to-end and the 6x CPU-throttle gate |
| Docker multi-stage image, built on the mini PC | mini-pc skill; no build args, committed tree only |
| PyO3 (port phase only) | In-process substitution of Python stages by Rust stages for parity runs |

## 9. Repository layout

```
Cargo.toml, rust-toolchain.toml, .cargo/config.toml   Rust workspace (scaffold)
package.json, pnpm-workspace.yaml                     pnpm workspace (scaffold)
crates/coach-wire/     generated wire types, live channel table, frame codec types
crates/coach-ibt/      IBT header, variable table, session-info parser, CarSetup/driver extraction, finalization gate
crates/coach-live/     live engine, cues, tile formatting, frame encoder
crates/coach-views/    analysis.json -> bounded view files and traces binary
collector/             Windows binary
server/                Linux binary (also builds and tests on Windows)
web/                   SPA (src/gen/ is generated)
iracing-coach/         Python worker and oracle (unchanged location)
tools/                 check.ps1, wire/ and theme/ generators, parity/, migrate/, rig/, wsl/
deploy/mini-pc/        docker-compose.yml, app.env, Dockerfile
docs/08-rewrite/       decisions, evidence, architecture, contracts, plan
(companion-app/ was deleted in wave 4; it is in Git history, last present at 3ff5e87)
contracts/             Python MCP contracts (oracle); rewrite contracts are in docs/08-rewrite/contracts/
```

## 10. Build and release

- **Local gate:** `tools/check.ps1` (repository verification, `cargo fmt --check`, `cargo clippy --workspace -- -D warnings`, `cargo test --workspace`, wire and theme drift checks, `pnpm -C web typecheck test build`). `tools/check.ps1 -Linux` runs `tools/wsl/check.sh` in WSL Ubuntu, by default with scope `all` (workspace clippy and tests, then the Python suite), the same default as `check.sh`; `-LinuxScope rust|server|python` narrows it.
- **Image** (`deploy/mini-pc/Dockerfile`, context `.`, every base pinned by digest): stage 1 `rust:1.99-bookworm` builds `coach-server` and `coach-analysis` (`cargo build --release --locked -p coach-server -p coach-analysis`, 2 jobs at `nice 10`, BuildKit cache mounts); stage 2 `node:24-bookworm-slim` builds `web/dist` with pnpm 9.15.4; stage 3 `python:3.12-slim-bookworm` (its base already carries `ca-certificates`) adds the server binary, the native analysis stages as `/usr/local/bin/coach-analysis`, `web/dist` as `/app/web`, `iracing-coach/` (compiled with `compileall`), the Python MCP `contracts/` and the NAS probe, sets the environment of `contracts/server-config.md` section 1 (`COACH_PYTHON=/usr/local/bin/python3.12`, the plain interpreter), and has `coach-server` as its entry point (`serve` is the default subcommand). The build asserts Python 3.12 and runs `coach-analysis --version`. **Native stages:** the image sets neither `COACH_NATIVE_STAGES` nor `COACH_NATIVE_BIN`; with the executable at `/usr/local/bin/coach-analysis` the server's default is `decode,kernels,writer`, which it passes to every worker as `IRACING_COACH_NATIVE_STAGES` with `IRACING_COACH_NATIVE_BIN`, and which selects the native admission model (5.0 x bytes, 2.0 GiB per-job ceiling, 2.7 GiB shared budget; `contracts/server-config.md` section 3, `worker-interface.md` section 6). `COACH_NATIVE_STAGES=none` in the stack would return to the pure-Python worker and its model, and then needs `mem_limit` 4g again. The interpreter shim the native release used before the server had this setting (`python3.12-native`, releases `fd0a38c` and `dccfadf`) is gone. No PyYAML. The root `.dockerignore` is an allowlist (`.cargo/`, the Cargo and pnpm manifests and locks, `crates/`, `collector/`, `server/`, `web/`, `iracing-coach/`, `contracts/`, `deploy/mini-pc/`), so `docs/`, `test-data/`, build outputs and `rust-toolchain.toml` stay out (the toolchain file would make rustup fetch a second toolchain and the Windows target).
- **Compose** (`deploy/mini-pc/docker-compose.yml`): one service `app`, `image: ${APP_IMAGE}`, `pull_policy: never`, `restart: unless-stopped`, `init: true` (reaps worker processes, forwards SIGTERM), `stop_grace_period: 30s` (the snapshot on SIGTERM), host port 8210 -> 8080, `mem_limit: 3g`, `memswap_limit: 3g` (the native model's 2.7 GiB shared worker budget plus 0.3 GiB margin, no swap; 4g for the pure-Python worker; `worker-interface.md` section 6, measured in `acceptance/g1-native.md`), `cpus: 2.0`, `cpu_shares: 256`, volumes `${APP_DATA}:/data`, a bind of the host's plain directory `/mnt/nas-iracing-coach` at `/nas/apps` with `propagation: rslave` and `create_host_path: false` (the NAS folder is a host automount, next bullet), and `/etc/localtime` read-only (local time for the 04:00 daily copy); `read_only: true` root filesystem with a 256 MB `tmpfs: /tmp`; json-file logging 20m x 3. The container runs as root without extra privileges. **NFS identity:** the NAS squashes the container's root to uid 1024, gid 100 (Synology's admin account); the folder `iracing-coach/` was created over NFS by the first-release probe, so it and every later server write are owned 1024:100, mode 0777, and a 4 MiB write, flush and hashed read-back passed. Nothing in the design depends on the owner uid (`acceptance/first-release.md`; R2 closed).
- **NAS folder (host automount; R3 closed 2026-10-03):** the Compose NFS volume is gone, because Docker mounts a volume when the container starts, so a NAS that did not answer meant the stack did not start. The mini PC mounts the Coach's own folder, `192.168.1.82:/volume1/apps/iracing-coach` (NFSv4.1, `soft,timeo=150,retrans=3,noatime`, read-write), with a systemd automount at `/mnt/nas-iracing-coach/iracing-coach` (idle after 120 s, twice the server's 60 s probe), and the stack binds the plain parent `/mnt/nas-iracing-coach` at `/nas/apps` with `propagation: rslave`, so `COACH_NAS_DIR=/nas/apps/iracing-coach` is unchanged. The units are `mnt-nas-iracing-coach-iracing-coach.{mount,automount}` in the g-house repository's `ops/host/systemd/` (commit `a15e066d`, not pushed or merged at 2026-10-03), bootstrap-only like `mnt-nas-backups` (a g-house release refreshes only six named units): applied to this host by hand and installed by `install-household-host.sh` on a fresh one. Measured on the host: the parent must be bound, not the automount point (that mounts the NFS when the container starts); `rslave` is required (Docker's default `rprivate` fails with `ELOOP` after 40 automount requests); the directory under the automount is `chattr +i`, so a write while no automount is active fails instead of landing on the NVMe. The container sees only its own folder, not the backup sets on the same share (`/mnt/nas-apps` is g-house's separate `hard` mount of the whole share). At release `4c3c5f0` the automount was idle when the container started and the first NAS access, from the `archiver` thread, mounted it one second later (`acceptance/server-release.md`). The folder must exist on the NAS for the mount to succeed (the server creates the layout beneath it, not the folder). Not proved against a real NAS outage or a reboot.
- **Healthcheck:** `coach-server healthcheck` (`contracts/server-config.md` section 2: GET `/health` on the `COACH_BIND` port, exit 0 only on a 200), interval 30 s, timeout 5 s, retries 3, `start_period` 60 s, `start_interval` 2 s. The first release (wave 1, the scaffold skeleton) ran a `python3.12 -c` GET of `/health` instead, because the skeleton had no subcommand and any argument started a second server; the tree's Compose file runs the subcommand from the first real-server release on.
- **app.env:** `DOCKERFILE=deploy/mini-pc/Dockerfile`, `CONTEXT=.`, `HEALTH_SERVICES=app`, then the recovery set. Target: `RECOVERY_PATHS=db snapshots`, `RECOVERY_SQLITE=db/coach.sqlite3`. First release and the first real-server release: `RECOVERY_PATHS=.` with `RECOVERY_SQLITE` empty, because `g-house-stack` fails a recovery point whose listed path or SQLite file does not exist and the skeleton created neither; the server creates `db/` and `snapshots/` at start (`contracts/server-config.md` section 2), so the release after the first real-server release switches to the target values.
- **Server environment:** names, image values and `--dev` defaults are the contract `contracts/server-config.md`; the server compiles in nothing from outside the build context.
- **Release:** commit, then `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash). D14 is standing permission for this stack. Health is `/health` (DB open, migrations applied; independent of the NAS). A failed release restores the previous Compose file and image; data is not rolled back, so schema migrations are additive and the previous binary must run on the new schema. Before migrating, the server writes `db/pre-migrate-v<N>.sqlite3`.
- **Collector:** `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` (static CRT), SHA-256 recorded; installed through the remote tools to `%LOCALAPPDATA%\Programs\iRacingCoach\coach-collector.exe`, then `coach-collector.exe install --server http://192.168.1.81:8210` (HKCU Run value, config, start in the interactive session). `uninstall` removes the Run value and its own files only. No installer framework, no admin rights, no inbound firewall rule. The server reports the collector's version and protocol; a mismatch shows "Collector update needed".
- **Versions:** one workspace version for server, collector and web; contract versions are independent (`api` v1, collector protocol 1, live frame 1, projection integer).

## 11. Test strategy

| Layer | What | Where it runs |
|---|---|---|
| Unit | each crate and module (`cargo test`), web (`vitest`), Python (`unittest`, Linux-clean) | dev PC; WSL for Linux |
| Golden vectors | `test-data/live-truth-conformance-v1.json`; C# `LiveTelemetry_*` engine tests ported to JSON vectors; SDK byte fixtures recorded on the rig; IBT session-info parity against Python `ibt_reader` | dev PC |
| Contract | server integration tests call every endpoint on fixture data and validate responses against `api.openapi.yaml` (`jsonschema`); generated types make TS and Rust agree at compile time; frame codec round trip Rust -> TS | dev PC |
| Parity | `tools/parity/analysis_parity.py` byte comparison on the synthetic fixture and the private corpus: parsed mode (existing normalisation) as a diagnostic, raw mode with an explicit substitution allowlist as the gate, unpinned-identity and worker-interface captures (`contracts/worker-interface.md` section 8); Linux 3.12 container output vs Windows 3.12 oracle (root prefixes and newlines only) | dev PC, WSL, mini PC |
| End to end | collector with a fake SDK map (replays an SDK byte fixture) -> server on the dev PC -> worker -> projection -> Playwright on the SPA | dev PC |
| Performance gates | G1 analysis on the N100; G2 SPA at 6x CPU throttle; G3 garage capture on the rig; G4 sim impact on track (`workstreams.md`) | mini PC, dev PC, sim PC |

## 12. Analysis port plan (P1 FAIL -> staged Rust port, D2 ruling)

v1 ships with the Python worker. The port started on 2026-10-03 (owner ruling of that morning: v1 acceptance is not a gate) and proceeds in phases, each merged only when the parity gate passes. Python stays the orchestrator and the oracle; a Rust stage is selected per stage by `IRACING_COACH_NATIVE_STAGES` (`contracts/worker-interface.md` section 7). Phase 1 is not in-process: it is the executable `coach-analysis decode` (crate `crates/coach-analysis`), started by the worker per recording, which writes a coach column store file (`.ccs`, format version 1) that the Python seam `native_stages.py` reads. The PyO3 module `coach_native` this section first named is deferred; phase 2 keeps a process and the store as well (`port/phase2-plan.md` section 4).

| Phase | Stage | Profile share | Gate |
|---|---|---|---|
| 1 | IBT column decode by the executable `coach-analysis decode` into a coach column store file (boundary A: `_load_analysis_telemetry` -> `TelemetryTable`); landed 2026-10-03 (`port/phase1-decode.md`) | 11.8 % time, 42 % peak (85 % with column copies) | decoded-matrix hash equal (`tools/parity/emit_ibt_golden.py`) and full `analysis.json` byte parity |
| 2 | Per-sample kernels on Rust columns: `_normalize_column`, `_apply_steps`, `_lap_trace_payload`, `_lap_summaries`, `_additional_trace_sources`, `_track_profile`, `_setup_channel_series`, `_setup_telemetry` | 71.5 % time | per-stage insertion-order output equal (`engine-output.insertion-order.json`) and full byte parity |
| 3 | `analysis.json` writer (`storage._json_text`, Python `repr` floats) | 6.2 % time | byte parity of every persisted file |

**Why decode precedes kernels (stated per the ruling).** The kernels consume decoded columns. Feeding Rust kernels from Python decode would mean converting the Python float lists, which are the memory peak the port exists to remove, into a Rust buffer on every job; and the only clean boundary the spike found is A (decode to table), while a mid-engine boundary needs the expensive key-order encoding either way. Phase 1 is expected to lift the analysis admission ceiling, but only once the remaining Python list materializations are inventoried and changed (`contracts/worker-interface.md` section 7); the benefit is measured, not assumed. After each phase, peak RSS on the N100 is re-measured and `mem_limit` and the admission formula are lowered only on measurement.

Determinism rules every Rust stage must meet (spike section 5): Neumaier compensated `sum()` in element order, exact `math.fsum`, `Fraction`-exact `statistics.mean/pstdev`, round-half-even `round()`, Python `repr` float text, int/float type preservation, per-site `json.dumps` flags, Unicode 15 case folding. `analysis_id` does not change through the port: it hashes the Python analyzer bundle, which stays in the repository and image; a Python change still changes it, and parity forces the Rust stage to follow.

## 13. Where this design departs from the orchestrator's starting points

- **NAS mount is read-write, not read-only:** the D4 ruling makes the server the NAS writer (uploads arrive over HTTP), so the collector never writes the share.
- **Ingest notification is HTTP, not a WebSocket message:** `POST /api/v1/uploads` is idempotent by hash and works without the WebSocket; the WebSocket carries the later acknowledgements and presence.
- **No outbox copy on the sim:** the collector uploads from iRacing's own file and keeps a reference journal, which removes all sim disk writes; the cost is that a recording deleted by the user before upload is lost (stated in `risks.md`).
- **`mem_limit` is 4 GiB, not P1 peak plus a small margin:** a light job that loads one `analysis.json` (about 0.9 GiB) fits beside an analysis; heavier light jobs wait through the shared worker budget.
- **Recordings over about 250 MB are deferred, not analysed:** the 2 GB Kansas file would need about 22 GiB in Python; it is listed and its setup is captured, and port phases 1-2 lift the limit.

### Owner refinement: automatic NAS library sync (2026-10-24)

`contracts/nas-sync.md` supersedes the raw/setup payload locations and collector no-write
statements above for the scoped feature. `\\192.168.1.82\apps\iracing-coach\library` is the
browseable canonical archive, not an export duplicate. Existing payloads relocate on the NAS
without bulk copying; field recordings and metadata/user-data backups keep their prior layout.
Replay uploads use the same resumed and verified transport, with no telemetry analysis. Setup
inbox imports remain locked and Coach saves create versions; only new managed copies arrive
on the rig. The app never mirrors a sim deletion, and a pinned-identity-checked share deletion
creates a durable tombstone so synchronization cannot resurrect it. Original sim files and
the retired desktop archive remain read-only. Large payloads have no second durable mini-PC
cache; active upload/analysis scratch is released when safe. See the contract for exact state,
path, quota, interruption and restoration rules.