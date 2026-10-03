# First mini-PC release (wave 1, W9)

Released 2026-10-03 01:58 UTC (2026-10-02 evening local) under D14 from branch `w9/deploy`, commit `9fb5223322a146c89f90d159bff00867bacbf05f` (wave-0 scaffold plus the deploy files). The server is the scaffold skeleton: it serves `/health` only.

## What runs

| Item | Value |
|---|---|
| Stack, container | `iracing-coach`, `iracing-coach-app-1` |
| Image | `iracing-coach:9fb5223…`, 133 MB; bases pinned by digest: `rust:1.99-bookworm` (1.99.0), `node:24-bookworm-slim`, `python:3.12-slim-bookworm` (Python 3.12.15) |
| Build | cold, on the N100: 226 s in total, of which `cargo build --release --locked -p coach-server` 130 s (2 jobs, `nice 10`); web 1.3 s |
| Port | host 8210 -> 8080 (`http://192.168.1.81:8210`) |
| Limits (`docker inspect`) | memory 4 GiB, memory+swap 4 GiB, `cpus` 2.0, `cpu_shares` 256, read-only root, `init` |
| Volumes | `/srv/g-house/apps/iracing-coach` -> `/data`; Compose NFS volume `iracing-coach_nas` (`:/volume1/apps`) -> `/nas/apps` |
| Idle use | 1.2 MiB RSS, 0.00 % CPU |
| Recovery point | `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T015846Z.tar` (empty data directory) |

## Acceptance

| Check (workstreams.md W9) | Result |
|---|---|
| `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` | exit 0; `iracing-coach is running 9fb5223…`; stack healthy 5 s after start |
| `ssh g-house-pc "sudo -n g-house-stack status iracing-coach"` | `recorded=92a9b228cc26f928`, `iracing-coach-app-1 Up 46 seconds (healthy)` |
| `curl -s http://192.168.1.81:8210/health` | 200 `{"schema_version":0,"server_version":"0.19.0","status":"ok"}` |
| `curl -s http://192.168.1.81:8210/api/v1/status` -> `nas.state = ok` | **not available**: 404, the skeleton has no status endpoint yet (W4). Replaced by the in-container probe below, as the wave plan allows |
| NAS probe from inside the container: `docker exec iracing-coach-app-1 python3.12 /app/deploy/nas_probe.py` | `state: ok`, exit 0 (below) |
| Python worker in the image | `mcp_server.py` answers `initialize` (`iracing-coach-local` 0.3.0, protocol 2025-06-18) and `tools/list` (26 tools) with the `worker-interface.md` section 2 variables and roots on the container's `/tmp`, exits 0 |

Run again once W4's server is released: `curl -s http://192.168.1.81:8210/api/v1/status` must show `nas.state = ok`.

## NAS probe (risks R2 and the NFS uid question)

`deploy/mini-pc/nas_probe.py`, run as the container's user (uid 0) at 2026-10-03T01:59:34Z:

- Mount: `nfs4` from `:/volume1/apps`, options `rw,noatime,vers=4.1,soft,proto=tcp,timeo=150,retrans=3,sec=sys,addr=192.168.1.82`; none of the contract's options missing.
- The share was empty (0 entries; root uid 0, mode 0777). The probe created `iracing-coach/` and `iracing-coach/.probe/` over NFS, wrote a 4 MiB file with `O_CREAT|O_EXCL`, fsynced (0.041 s), dropped the client cache, read it back: SHA-256 equal. It then removed its probe file; `iracing-coach/.probe/` (empty) remains.
- **The NAS maps the container's root to uid 1024, gid 100** (Synology's root squash to its admin account): the new folder and file are owned 1024:100, mode 0777. Writes are accepted, so R2 is closed for this export; NFS identity is "root -> 1024", not uid 0 as `architecture.md` section 10 assumed. Nothing in the design depends on the owner uid.
- The folder was created over NFS rather than SMB from the dev PC (D14 allowed either) so that its owner is the one every later server write has; an SMB-created folder would carry the SMB account instead.

## Deviations from architecture.md section 10 (all in W9 paths)

| Item | Section 10 | Released | Why, and when it changes |
|---|---|---|---|
| Healthcheck | `coach-server healthcheck` | `python3.12 -c` GET of `/health` (`start_period 60s`, `start_interval 2s`) | the skeleton has no subcommand; any argument would start a second server. Switch when W4 ships `healthcheck` |
| `RECOVERY_PATHS` / `RECOVERY_SQLITE` | `db snapshots` / `db/coach.sqlite3` | `.` / empty | `g-house-stack` fails the recovery point when a listed path or SQLite file does not exist (`g-house-stack` lines 88, 103), and the data directory is empty until a server creates them. Switch in the first release after a released server has created `db/coach.sqlite3` and `snapshots/` |
| Toolchain file | in context | excluded by `.dockerignore` | `rust-toolchain.toml` names channel `1.99` and the Windows target, which would make rustup in the image download a second toolchain; the image's 1.99.0 is the same version |
| Node stage | `node:24-bookworm` | `node:24-bookworm-slim`, pnpm 9.15.4 from npm | smaller download; no corepack dependency |
| `ca-certificates` | added | already in the Python base image | no apt step needed |
| Extras | none | `init: true`, `stop_grace_period: 30s`, `/tmp` tmpfs 256 MB, `/etc/localtime` read-only | worker reaping and SIGTERM forwarding, time for the snapshot on SIGTERM, a bounded tmpfs, local time for the 04:00 daily backup |
| Server paths | not stated | `COACH_BIND`, `COACH_DATA_DIR=/data`, `COACH_NAS_DIR=/nas/apps/iracing-coach`, `COACH_WEB_DIR=/app/web`, `COACH_PYTHON`, `COACH_WORKER_SCRIPT=/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py`, `COACH_CONTRACTS_DIR=/app/contracts` in the image | proposed to W4 as a contract change; only `COACH_BIND` is read today |

## Not verified

- Tailnet access: from the dev PC `http://minipc.tail4e6ca6.ts.net:8210/health` timed out, and so did Home Assistant on 8123, so the dev PC was not on the tailnet at the time; not a stack fault, still to check from a tailnet device.
- Start while the NAS is down (R3, owner question Q1): unchanged; the Compose NFS volume must mount for the stack to start.
- Off-host backup: `/srv/g-house/apps/iracing-coach` is not in the household off-host backup (mini-pc skill); the server's own daily NAS copy (`nas-layout.md` section 5) is the planned off-host copy.

## Gates

| Gate | State |
|---|---|
| G1 analysis on the N100 | wave 2 (`tools/rig/g1-analysis.sh`, not written yet) |
| G3 garage capture | **[sim: blocked]** |
| G4 rig performance | **[sim: blocked]** |
