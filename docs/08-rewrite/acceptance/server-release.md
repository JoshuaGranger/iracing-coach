# First real-server releases (wave 2, W9)

Two releases on 2026-10-03 (evening of 2026-10-02 local) under D14, from branch `w9/deploy` after a fast-forward merge of local `main`. They replace the wave-1 skeleton (`first-release.md`) with the wave-1 integrated server: W4's recovery-durability and restage fixes are in `main`, so R23 no longer holds the release.

## Release 1: the real server

| Item | Value |
|---|---|
| Commit | `615377cff592e4b777fd3049fef6aa5d83f91f77` (`main`: wave-1 integration and the reconciled contracts) |
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash), exit 0 |
| Started, finished | 04:27:04Z, 04:29:49Z (2 min 45 s in total) |
| Build on the N100 | 159 s (`g-house-app ... built in 159s`); `cargo build --release --locked -p coach-server` 154.8 s with 2 jobs at `nice 10` (registry cache warm from the skeleton build, all crates compiled); `pnpm install --frozen-lockfile` 2.5 s; `vite build` 0.4 s (initial `index` chunk 10.4 KB gzip) |
| Image | `iracing-coach:615377cff592e4b777fd3049fef6aa5d83f91f77`, 144 MB (skeleton 133 MB) |
| Stack | revision `fe9f13bca58e8ff2`; recovery point `iracing-coach-20261003T042944Z.tar` (10 KB: the skeleton never wrote to `/data`); listening 04:29:45Z, healthy 04:29:47Z |
| Healthcheck | Compose already ran `/usr/local/bin/coach-server healthcheck` (committed in wave 1, first exercised here): `Up (healthy)` |
| Start-up directories | `db/` (`coach.sqlite3`, WAL), `py/`, `run/`, `snapshots/`, `spool/`, `views/` under `/srv/g-house/apps/iracing-coach` (`server-config.md` section 2) |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"`:

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=fe9f13bca58e8ff2
recorded=fe9f13bca58e8ff2
iracing-coach-app-1   Up 9 seconds (healthy)   iracing-coach:615377cff592e4b777fd3049fef6aa5d83f91f77
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`

`curl -s http://192.168.1.81:8210/api/v1/status`:

```json
{"api_version":1,"collector_newer_than_server":false,"collector_protocol":1,"collectors":[],"disk_free_bytes":382442172416,"live_table_version":1,"nas":{"last_ok_at":"2026-10-03T04:29:45Z","pending_archive":0,"state":"ok"},"projection_version":1,"python_version":"3.12.15","queue":{"analysis":0,"deferred":0,"light":0},"server_version":"0.19.0","spool":{"bytes":0,"files":0,"limit_bytes":21474836480},"worker_profile_version":"post-race-foundations-v23"}
```

`nas.state = ok`: W9's acceptance check, which the skeleton could only stand in for with the in-container probe.

## Release 2: recovery set switched to user data

Change: `deploy/mini-pc/app.env` `RECOVERY_PATHS=. -> db snapshots`, `RECOVERY_SQLITE= -> db/coach.sqlite3` (`architecture.md` section 10, `server-config.md` section 2), possible now that release 1 created both paths; `g-house-stack` refuses a listed path that does not exist.

| Item | Value |
|---|---|
| Commit | `15a53f4161b7233ee55153b43f4d423f1dae7db9` (`w9/deploy`) |
| Command | `release.sh iracing-coach .`, exit 0; 04:30:12Z to 04:30:19Z (6.8 s) |
| Build | 1 s (every layer cached; only the tag changes) |
| Image | `iracing-coach:15a53f4161b7233ee55153b43f4d423f1dae7db9` (same layers as release 1) |
| Stack | revision `fe9f13bca58e8ff2` (Compose file unchanged); healthy 04:30:17Z |
| Recovery point | `iracing-coach-20261003T043014Z.tar`, 573 KB: `db/` (`coach.sqlite3-wal`, `-shm`), `snapshots/`, and `sqlite/...coach.sqlite3` copied through SQLite (`deploys.log`: `sqlite ok /srv/g-house/apps/iracing-coach/db/coach.sqlite3 258048`) |
| Idle after start | 3.4 MiB container memory (`docker stats`), 0.01 % CPU |

`/api/v1/status` after release 2: `nas.state = ok` (`last_ok_at` 04:30:15Z), queues empty, spool empty.

## Unchanged from the first release

- Tailnet access from a tailnet device, start with the NAS down (R3, owner question Q1) and the off-host backup of `/srv/g-house/apps/iracing-coach` remain as `first-release.md` states.
- Not in the recovery set by design: `spool/` (uploads in flight; the collector resumes from the server offset), `py/` (worker archives: reports re-derivable, user tuning data reaches `snapshots/` through the hourly snapshot), `views/` (re-derivable), `run/`.

## Release of `ae65a0a`: the rig and migration fixes (2026-10-03)

Released under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`802459a` to `ae65a0a`, "Fix what the rig track and the migration found"). Nothing was pushed to GitHub. The server side of the commit: `coach-views` takes a missing or null `track_profile` (the cause of the 84 failed projections in `migration.md`), `POST /api/v1/admin/remove` (library cleanup), and a `ready` session whose newer analysis is over the memory ceiling keeps `analysis_reason exceeds_memory_budget`. The worker (`iracing-coach/`) and the SPA did not change.

| Item | Value |
|---|---|
| Commit | `ae65a0ae7089fd6b98a95e69127597954689137b` |
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`), exit 0 |
| Started, finished | 16:16:01Z, 16:18:08Z (2 min 7 s) |
| Build on the N100 | 120 s (`g-house-app ... built in 120s`): `cargo build --release --locked -p coach-server` 1 min 56 s with 2 jobs at `nice 10` (`coach-wire`, `coach-ibt`, `coach-views`, `coach-live` and `coach-server` recompiled, dependencies from the build cache); `pnpm install` cached; `vite build` 270 ms with the same asset names as the previous release (`index-CZnMMfW2.js` 30.20 kB, 10.68 kB gzip) |
| Image | `iracing-coach:ae65a0ae7089fd6b98a95e69127597954689137b`, 145 MB (manifest `sha256:0d77c800...`, config `sha256:c9a970eb...`; the running container's image ID equals that config digest) |
| Previous image | `iracing-coach:035a78b61be77d4c4e708097c267f90e9abc6b35` (`v1-candidate.md`, polish release), kept on the host |
| Stack | revision `fe9f13bca58e8ff2` (Compose file unchanged); `deploys.log`: start 16:18:02Z, recovery point 16:18:02Z (`sqlite ok .../db/coach.sqlite3 7221248`), healthy 16:18:06Z; container started 16:18:04Z, 0 restarts |
| Recovery point | `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T161802Z.tar`, 24,248,320 B: the first one that holds the migrated library (the previous, `...123051Z.tar`, 4,741,120 B) |
| Memory | 19.8 MiB after start; 55.9 MiB at 16:32Z after the re-projection, the library cleanup and the browser checks (limit 4 GiB), `OOMKilled false`; the container log since start has no error line and one warning, the admin removal record (by design) |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"`:

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=fe9f13bca58e8ff2
recorded=fe9f13bca58e8ff2
iracing-coach-app-1   Up 22 seconds (healthy)   iracing-coach:ae65a0ae7089fd6b98a95e69127597954689137b
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`

`curl -s http://192.168.1.81:8210/api/v1/status` at 16:18:31Z: `nas.state ok` (`last_ok_at` 16:18:04Z, `pending_archive 0`), `queue {analysis 0, deferred 4, light 0}` (the four analyses over the memory ceiling, `migration.md`), spool 2,170,209 B in 859 files (setup blobs), `python_version 3.12.15`, `worker_profile_version post-race-foundations-v23`, `api_version 1`, `collector_protocol 1`, `live_table_version 1`, `projection_version 1`; the sim collector `fc450983-...` reconnected after the restart (`online true`, `last_seen_at` 16:18:24Z). Still `nas.state ok` at 16:32:39Z (`last_ok_at` 16:32:04Z) after the work below.

Exercised on this release the same day: the re-projection of the 84 failed analyses (`migration.md`, re-projection section), the first real `POST /api/v1/admin/remove` (`library-cleanup.md`), and the collector update on the sim (`collector-install.md`). `node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\w9\r4\shots\spa-s5 --session 5` (the Iowa race) at 16:27Z: 0 failures of 84 results at desktop, tablet and phone (race 107 / 97 / 206 ms, traces 220 / 197 / 233 ms, 0 requests during each 5 s pointer sweep; the library and setup checks compare the first page of 50).
