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

## Release of `dccfadf`: the native analysis stages (2026-10-03)

Released under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`fda35d0` to `1f6e3ea`, port phases 1 and 2). Nothing was pushed to GitHub. Two commits on top of `main`:

- `fd0a38c` "Ship coach-analysis in the image and run the native stages": the server stage builds `-p coach-server -p coach-analysis` and the runtime installs `/usr/local/bin/coach-analysis`; `COACH_PYTHON` is the image's shim `/app/deploy/worker-python`, which sets `IRACING_COACH_NATIVE_STAGES=decode,kernels` and `IRACING_COACH_NATIVE_BIN=/usr/local/bin/coach-analysis` and execs `python3.12`, because `coach-server` clears the worker environment and pins the stages empty with no setting (W4 follow-up, `g1-native.md`). The image build asserts both (`coach-analysis --version`; the shim's environment). G1 rig changes for the re-measurement. `mem_limit` unchanged (4g); Compose file unchanged.
- `dccfadf` "Name the worker interpreter shim so the server still finds it": released first as `worker-python`, `fd0a38c` reported `python_version: null` in `/api/v1/status` (the server runs the first worker-command part whose file name starts with `python` with `-V`). Renamed `/app/deploy/python3.12-native`; `python_version` is `3.12.15` again.

| Item | `fd0a38c` | `dccfadf` |
|---|---|---|
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`), exit 0 | the same, exit 0 |
| Started, finished | 20:09:49Z, 20:12:30Z (2 min 41 s) | 20:12:59Z, 20:13:38Z (39 s) |
| Build on the N100 | 125 s: `cargo build --release --locked -p coach-server -p coach-analysis` 1 min 59 s with 2 jobs at `nice 10` (`coach-numerics` and `coach-analysis` compiled for the first time, in parallel with the server crates; dependencies from the build cache); `vite build` 394 ms, same asset names | 3 s (every layer but the shim's cached) |
| Image | `iracing-coach:fd0a38c0...`, 147 MB | `iracing-coach:dccfadf1f40c9654b8d8de41feb2ef260a96c518`, 147 MB (manifest `sha256:741df388...`, config `sha256:616dd7b6...`, the running container's image ID); `coach-analysis` 1,961,256 B |
| Stack | revision `fe9f13bca58e8ff2` (Compose file unchanged); recovery point 20:11:56Z (`sqlite ok ... 7270400`), healthy 20:12:30Z | recovery point `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T201304Z.tar` 20:13:04Z, healthy 20:13:37Z; container started 20:13:35Z, 0 restarts, `OOMKilled false`, memory limit 4,294,967,296 |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"`:

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=fe9f13bca58e8ff2
recorded=fe9f13bca58e8ff2
iracing-coach-app-1   Up 2 seconds (healthy)   iracing-coach:dccfadf1f40c9654b8d8de41feb2ef260a96c518
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`. `/api/v1/status` at 20:13:41Z: `nas.state ok` (`last_ok_at` 20:13:35Z, `pending_archive 0`), `queue {analysis 0, deferred 4, light 0}`, `python_version 3.12.15`, `worker_profile_version post-race-foundations-v23`, the sim collector online. In the container: `COACH_PYTHON=/app/deploy/python3.12-native` in the server's environment, `memory.peak` 17.7 MB after start, `oom_kill 0`. Still `nas.state ok` at 20:25:35Z after the gate and the deferred runs; `deferred 8` after the deferred analyses were re-requested (`g1-native.md`).

Exercised on this release: gate G1 with the native stages (PASS: 8.50 and 8.40 s, worker 0.25 GiB, tree 0.78 GiB, container peak 1.30 GiB, byte parity, 17 kernel calls count for count) and the three deferred analyses that fit (all complete, 5.9 to 15.0 s), `g1-native.md`. The server's own analysis path will first run natively on the next recording it has not analysed: every session's analysis is current and the worker's cache answers a re-run. That the server uses the shim is shown by its environment and by `python_version`, which it gets by running the shim.

## Release of `7d38242`: the server's native stages, no shim, `mem_limit` 3g (2026-10-03)

Released under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`f2d35fd` to `ef98f0a`: port phase 3, the `companion-app/` deletion, W4's `COACH_NATIVE_STAGES` setting, native admission, native-scratch sweep and one deferred row per session). Nothing was pushed to GitHub. One commit on top of `main`:

- `7d38242` "Drop the interpreter shim: the server's native stages drive the worker": `COACH_PYTHON=/usr/local/bin/python3.12` (the plain interpreter); `deploy/mini-pc/python3.12-native` deleted and no longer copied; the build asserts Python 3.12 through `$COACH_PYTHON` and runs `coach-analysis --version`. The image sets neither `COACH_NATIVE_STAGES` nor `COACH_NATIVE_BIN`, so the server's default applies: `decode,kernels,writer` with `/usr/local/bin/coach-analysis`, passed to every worker, and the native admission model (5.0 x bytes, 2.0 GiB ceiling, 2.7 GiB budget). Compose `mem_limit`/`memswap_limit` 4g to **3g** (`worker-interface.md` section 6). `architecture.md` section 10 updated; G1 rig: the cold runner resolves the stages as the server does, `g1.py calls` applies the writer rule.

| Item | Value |
|---|---|
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`, background), exit 0 |
| Started, finished | 22:44:48Z, 22:47:31Z (2 min 43 s) |
| Build on the N100 | 126 s (`g-house-app ... built in 126s`): `cargo build --release --locked -p coach-server -p coach-analysis` 2 min 01 s with 2 jobs at `nice 10` (the workspace crates recompiled, dependencies from the build cache) |
| Image | `iracing-coach:7d38242bb24b623a01b353c9405b14d680418950`, 147 MB (manifest `sha256:e2ed0aad...`, config `sha256:d818049a...`, the running container's image ID) |
| Previous image | `iracing-coach:dccfadf1f40c9654b8d8de41feb2ef260a96c518`, kept on the host |
| Stack | revision `5b0e5ac51ce08983` (Compose file changed: `mem_limit`); `deploys.log`: start 22:46:57Z, recovery point 22:46:57Z (`sqlite ok .../db/coach.sqlite3 7270400`, `/srv/g-house/recovery/iracing-coach/iracing-coach-20261003T224657Z.tar`), healthy 22:47:30Z; container started 22:47:28Z, 0 restarts, `OOMKilled false` |
| Limits in effect | `HostConfig.Memory` 3,221,225,472, `MemorySwap` 3,221,225,472; cgroup `memory.max` 3221225472; `memory.peak` 20.0 MB after start |
| Environment | `COACH_PYTHON=/usr/local/bin/python3.12`; no `COACH_NATIVE_*` variable (the default); `/app/deploy` holds `nas_probe.py` only |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"` (from the release):

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=5b0e5ac51ce08983
recorded=5b0e5ac51ce08983
iracing-coach-app-1   Up 2 seconds (healthy)   iracing-coach:7d38242bb24b623a01b353c9405b14d680418950
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`. `/api/v1/status` right after the release (collector `last_seen_at` 22:47:34Z): `nas.state ok` (`last_ok_at` 22:47:28Z, `pending_archive 0`), `queue {analysis 0, deferred 8, light 0}`, **`python_version 3.12.15`** (the plain interpreter, run with `-V`), `worker_profile_version post-race-foundations-v23`, the sim collector online. After the work below (22:54:17Z): `nas.state ok` (`last_ok_at` 22:53:28Z), `queue {analysis 0, deferred 1, light 0}`, `oom_kill 0`, `memory.peak` 2.373 GiB, no error or warning line in the server log since start.

**Native stages in effect.** Neither `/api/v1/status` nor the server log shows them: the status has no such field and the start-up log line carries only bind, version and dev (W4 follow-up in `g1-native.md`). They are shown by the workers themselves: `/proc/<pid>/environ` of each of the three server-run analysis workers (pids 554, 620, 864, read inside the container while the jobs ran) held `IRACING_COACH_NATIVE_STAGES=decode,kernels,writer` and `IRACING_COACH_NATIVE_BIN=/usr/local/bin/coach-analysis`, and the sampler saw their `coach-analysis decode`, `merge`, `kernels` and `write` children. The admission model is shown by the jobs' `estimate_bytes` (5.0 x bytes + 0.15 GiB, x 1.5 for groups) and by sessions 18, 105 and 123 now being admitted.

Exercised on this release (`g1-native.md`): gate G1, writer-stage run, PASS (7.23 and 7.15 s, worker 0.28 GiB, tree 0.75 GiB, container peak 1.17 GiB, byte parity 12 of 12 and `analysis.json` strict, 17 kernel calls count for count, 3 native writes per analysis, 0 writer fallbacks); sessions 18, 105 and 123 re-requested through the API and analysed by the server (jobs 3540 to 3542 succeeded in 8, 11 and 16 s, worker plus largest child 0.744, 1.320 and 1.614 GiB against estimates of 1.410, 1.604 and 1.817 GiB); session 152 (Kansas, 2,093,549,028 B) still `deferred` with `exceeds_memory_budget` (estimate 9.899 GiB). Repository verification (`tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>`) before and after the change: exit 0, 1,434 tests, 1,416 passed, 18 skipped.

Backups unchanged: the recovery points stay on the mini PC; the off-host backup of `/srv/g-house/apps/iracing-coach` is as `first-release.md` states.
