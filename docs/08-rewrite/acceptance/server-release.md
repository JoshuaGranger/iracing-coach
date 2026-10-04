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

## Release of `4c3c5f0`: the NAS folder is a host automount (evening of 2026-10-03 local)

Released under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`ae862bf` to `2d0c0cb`, "Raise the type scale and add a per-driver text size"), with Joshua's permission of 2026-10-03 for the host change (backlog 15; `risks.md` R3 and Q1). Nothing was pushed to GitHub. One commit on top of `main`:

- `4c3c5f0` "The Coach container binds the host's NAS automount, so the stack starts while the NAS is down": the Compose volume `nas` (`type: nfs`) is gone and `/nas/apps` is a bind of the host directory `/mnt/nas-iracing-coach` with `propagation: rslave` and `create_host_path: false`; `COACH_NAS_DIR=/nas/apps/iracing-coach` is unchanged. `nas_probe.py` looks inside `/nas/apps/iracing-coach` first (that is what makes the host mount it) and reads the last mount-table entry, because the NFS sits over its autofs trigger.

### The host change

Host mounts are kept in the g-house repository, `ops/host/systemd/`, as bootstrap-only units (a release refreshes only six named units; `mnt-nas-backups` is the precedent). The pair is committed there as `a15e066d` on branch `claude/platform-coach-nas-automount-20261003` (worktree `C:\Users\joshu\source\worktrees\g-house-coach-nas-automount`, cut from `origin/main` `d718d576`; `install-household-host.sh` and `ops/README.md` updated). **Not pushed and not merged.** It was applied to the host by hand with `sudo -n`, the way that README installs `imagebuilder.slice`, at 23:34 CDT (04:34Z): the committed files streamed into `/etc/systemd/system/` (sha256 `42a70708...` and `fc75496d...`, equal to the repository files), `daemon-reload`, `enable --now` of the automount. No reboot and no Docker restart: `docker.service` has run since 2026-09-01 and the host has been up 4 weeks.

| Item | Value |
|---|---|
| Parent directory | `/mnt/nas-iracing-coach/` (plain, root 0755): what the stack binds |
| Mount point | `/mnt/nas-iracing-coach/iracing-coach`, `chattr +i` underneath: a write while no automount is active fails with `EPERM` instead of landing on the NVMe |
| Mount unit | `mnt-nas\x2diracing\x2dcoach-iracing\x2dcoach.mount`: `192.168.1.82:/volume1/apps/iracing-coach`, `nfs4`, `vers=4.1,soft,timeo=150,retrans=3,noatime,_netdev`; no `[Install]` section, so it cannot be enabled to mount at boot |
| Automount unit | `...automount`, enabled, `TimeoutIdleSec=120`: twice the server's 60 s probe, so the mount is not remade every minute while the server runs |
| What the container sees | its own folder only, not the backup sets that `/mnt/nas-apps` (g-house's hard mount of the whole share) carries |
| Superblocks | Coach `0:207` `soft`; g-house backup `0:182` `hard`: separate, as they must be |

Why not `/mnt/nas-apps`, which g-house installed at 18:52 CDT the same day: it is `hard` on purpose (the backup must not end in a short write), the contract needs `soft`, and it is the whole share. Observed and not changed: its automount unit never started (`Path /mnt/nas-apps is already a mount point, refusing start`), because the mount was started directly at 18:52:38, so it has been a plain permanent mount since. A reboot, or stopping the mount and starting the automount, fixes it; that is the g-house session's to decide.

**Measured on the host before the Compose change**, in throwaway mount namespaces built the way runc builds a container's (`unshare --mount --propagation slave`, a recursive bind, `--make-rslave`):

| Case | Result |
|---|---|
| Listing the parent | mounts nothing; shows the mount point (`iracing-coach`, size 0) |
| Bind the **automount point itself** | the NFS is mounted at bind time (journal: `triggered by ... (mount)`), so a container start would need the NAS |
| Bind the parent, Docker's default `rprivate` | the first look inside fails with `ELOOP` ("Too many levels of symbolic links") after 40 automount requests; the mount never reaches the namespace |
| Bind the parent, `rslave`, mount idle | no request at bind time; the first look inside triggers it (`triggered by ... (ls)`), the NFS propagates in and the data reads |
| `rslave`, no automount yet, then the host starts it | a write into the bare mount point is refused (`Operation not permitted`); the automount appears in the namespace that predates it and the first look triggers the NFS |
| `rslave`, host mount already present | the autofs trigger and the NFS arrive together and read at once (the case of every later release) |

### The release

| Item | Value |
|---|---|
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`, background), exit 0 |
| Started, finished | 04:41:10Z, 04:43:41Z (2 min 31 s) |
| Build on the N100 | 115 s (`g-house-app ... built in 115s`): the web and server layers rebuilt because `main` had moved; dependencies cached |
| Image | `iracing-coach:4c3c5f02676203ca9dfe32e8f0648e38bc95822e`, 147 MB (config `sha256:04ac4ca070b4...`) |
| Previous image | `iracing-coach:7d38242bb24b623a01b353c9405b14d680418950`, kept on the host |
| Stack | revision `32c416e42e2d722c` (Compose file changed; previous `5b0e5ac51ce08983`); `deploys.log`: start 04:43:06Z, recovery point 04:43:07Z (`sqlite ok .../db/coach.sqlite3 7294976`, `/srv/g-house/recovery/iracing-coach/iracing-coach-20261004T044306Z.tar`), healthy 04:43:40Z; container started 04:43:38Z, 0 restarts, `OOMKilled false`. Start to container start was 32 s (31 s in the previous release, 22:46:57Z to 22:47:28Z) |
| Docker's record of the mount | `bind /mnt/nas-iracing-coach -> /nas/apps rw=true propagation=rslave` (the other two binds are `rprivate`) |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"` (from the release):

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=32c416e42e2d722c
recorded=32c416e42e2d722c
iracing-coach-app-1   Up 2 seconds (healthy)   iracing-coach:4c3c5f02676203ca9dfe32e8f0648e38bc95822e
```

`curl -s http://127.0.0.1:8210/health`: 200 `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`. `/api/v1/status` right after the release, before the server's first scheduled probe: **`nas.state ok`** (`last_ok_at` 04:43:39Z, `pending_archive 0`), `python_version 3.12.15`, `queue {analysis 0, deferred 1, light 0}`. The container log has no error or warning line. About 6 minutes after the start: 28.7 MiB against the 3 GiB limit, CPU 0.10 %.

### The proof: start with the automount idle

Before the release (04:41:02Z, 23:41:02 CDT): the automount `waiting`, no NFS mount at the Coach path (only the autofs trigger), the old container healthy on the Docker NFS volume, `nas.state ok`. The old container did not use the new mount, so it stayed idle through the build and the swap. From then to the end of the release the two units' journal holds **no** automount request until this one:

```
23:43:38 ... automount: Got automount request for /mnt/nas-iracing-coach/iracing-coach, triggered by 3539332 (archiver)
23:43:38 ... Mounting ... iRacing Coach's folder on the NAS apps share (NFS, soft, ...)
23:43:39 ... Mounted ...
```

The container started at 23:43:38 CDT and the request came from the server's `archiver` thread in the same second. Nothing from Docker or runc touched the NAS while the container was built, created or started, so the start did not depend on it. The container's own mount table (`/proc/3539294/mountinfo`) held the bind (`master:1`), the autofs trigger (`timeout=120`) and the soft `nfs4` mount of `192.168.1.82:/volume1/apps/iracing-coach`, propagated in; `nas.state` was `ok` one second after the start.

In the running container: at 23:44:24 stopping only the mount unit emptied the NFS mount on the host and in the container (0 and 0; the automount `waiting`, the trigger still in the container), and the server's next probe at 23:44:39 triggered it again from inside (`triggered by ... (archiver)`), `last_ok_at` 04:44:39Z. From 23:46:41 to 23:46:45 the automount unit itself was stopped and started with the container running: the container regained it through propagation, and `last_ok_at` was 04:47:39Z at the next probe. `nas_probe.py` in the container at 04:49:49Z: `state: ok`, exit 0, `missing_options []`, mount `192.168.1.82:/volume1/apps/iracing-coach` `nfs4` `soft`, folder root uid 1024 gid 100 mode 0777, a 4 MiB write with fsync in 0.054 s read back with an equal SHA-256, listing `.probe .tmp FORMAT backups quarantine raw setups`. The archive reads through the new mount: 668 recordings (20.29 GB) with 668 manifests, 859 setup files, daily folders under `backups/`.

**`g-house-stack apply` alone does not restart the service.** With the automount idle (mount unit stopped at 23:48:41 CDT) `sudo -n g-house-stack apply iracing-coach` printed `Container iracing-coach-app-1  Running` and `applied, revision 32c416e42e2d722c`: the same container (`ccac5bc07611`, started 04:43:38Z), healthy at 04:48:42Z, no automount request during it (the next one was the server's probe at 23:49:39). `compose up` recreates a service only when its definition or image changed, so the restart under an idle automount is the release above, and another needs a changed definition or image.

### Not proved, and left

- **A real NAS outage.** Nothing on the NAS or the network was changed to simulate one. Not exercised: how long the automount's mount attempt takes to fail against an unreachable NAS (systemd's default mount timeout is 90 s), the server's `unreachable` and `stalled` states through this mount, and the recovery once the NAS answers. The mechanism that lets the container start without it is measured above; the outage behaviour is as `nas-layout.md` section 6 designs it.
- **A reboot.** The start-order case (Docker before the automount unit) is shown with a throwaway namespace and by restarting the automount under the live container, not by a reboot.
- **The old Docker volume `iracing-coach_nas`** is still defined, unmounted, and nothing uses it. Removing it is a separate request.
- **The g-house change is a branch, not on `main`.** `install-household-host.sh` could only be syntax-checked here (`bash -n`); ShellCheck, which CI runs, was not available locally. The g-house deploy-automation tests pass (101, 4 skipped).
- The folder must exist on the NAS for the mount to succeed (it does). The server no longer creates `$NAS` itself, only the layout beneath it.

## Release of `932bdc4`: web round 1 and the setup-identity fix (2026-10-04)

Released under D14 from branch `w9/deploy` after a fast-forward merge of local `main` (`15791a9` to `932bdc4`, "Web round 1: the owner's backlog, the semantics audit and the Broadcast look"; `main` already held `w9/deploy` through `06c96eb`, so after the fast-forward the two are equal). Nothing was pushed to GitHub. No commit on top of `main` was needed for the release: the Compose file, `app.env` and the Dockerfile did not change. The server side of the commit: setup identity (`setup-keys.json` version 2 drops the derived `Chassis.FrontArb.Preload` leaves from the fingerprint; `server/src/setup_identity.rs` re-fingerprints and merges at every start; `.htm` exports named from the file name), projection version 2 (`coach-views`: sector times, pit stops), series and fixed/open on sessions, the new prefs fields (`text_size`, `session_filters`, `setup_filters`). The verification as a user is `round1-release.md`.

| Item | Value |
|---|---|
| Command | `bash ~/.claude/skills/mini-pc/release.sh iracing-coach .` (Git bash, in `C:\Users\joshu\source\worktrees\iracing-coach-w9`, background), exit 0 |
| Started, finished | 06:58:25Z, 07:01:18Z (2 min 53 s) |
| Build on the N100 | 136 s (`g-house-app ... built in 136s`): `cargo build --release` 2 min 11 s (workspace crates recompiled, dependencies cached); `vite build` 626 ms, `index-C5vhDvSw.js` 52.61 kB (19.03 kB gzip), `index-CgGoADq7.css` 51.28 kB (10.40 kB gzip) |
| Image | `iracing-coach:932bdc4919f4ba351780970b08b341199c24fe3a`, 147,198,326 B (config `sha256:b044aee2cce9...`, the running container's image ID) |
| Previous image | `iracing-coach:4c3c5f02676203ca9dfe32e8f0648e38bc95822e`, kept on the host |
| Stack | revision `32c416e42e2d722c` (Compose file unchanged); `deploys.log`: start 07:00:43Z, recovery point 07:00:44Z (`sqlite ok .../db/coach.sqlite3 7294976`, `/srv/g-house/recovery/iracing-coach/iracing-coach-20261004T070043Z.tar`, 120,760,320 B: the database before the setup merge), healthy 07:01:17Z; container started 07:01:15Z, 0 restarts, `OOMKilled false` |

`ssh g-house-pc "sudo -n g-house-stack status iracing-coach"` (from the release):

```
stack=iracing-coach project=iracing-coach compose=/etc/g-house/stacks/iracing-coach/docker-compose.yml revision=32c416e42e2d722c
recorded=32c416e42e2d722c
iracing-coach-app-1   Up 2 seconds (healthy)   iracing-coach:932bdc4919f4ba351780970b08b341199c24fe3a
```

`curl -s http://192.168.1.81:8210/health`: `{"schema_version":1,"server_version":"0.19.0","status":"ok"}`. `/api/v1/status` at 07:01:30Z: **`nas.state ok`** (`last_ok_at` 07:01:15Z, `pending_archive 0`), `queue {analysis 0, deferred 1, light 0}`, **`projection_version 2`** (1 before), `python_version 3.12.15`, `worker_profile_version post-race-foundations-v23`, the sim collector `fc450983-...` online (`sim not_running`). At 07:14:15Z, after the checks: `nas.state ok`, the same queues, container `Up 13 minutes (healthy)`.

**Setup-identity migration at start-up.** Before `coach-server listening` (07:01:15.538Z) the server log has `WARN coach_server::setup_identity: setup identity reconciled`, and `run/setup-identity.jsonl` in the data volume (new, 703 B) holds the same report as one line:

| Field | Value |
|---|---|
| `at`, `setup_keys_version` | 2026-10-04T07:01:15Z, 2 |
| `setups_checked` | 67 |
| `fingerprints_rewritten` | 63 |
| merges | setup **69 into 10** (`claude LV R CAM.sto`), setup **70 into 71** (`NOAPS_MaconiSetupShop AtlantaSS 26S3 Q.sto`), setup **2 into 1** (`NOAPS_MaconiSetupShop NHMS 26S3 R.sto`); 1 observation moved each, 0 notes moved |
| `removed_without_observations`, `kept_for_notes`, `unreadable` | none, none, none |

The backlog (#16) expected 69 and 70 both into 71; the values put capture 69 (04:13:52Z, 29 s after the last `claude LV R CAM.sto` sighting) with setup 10 and capture 70 (04:16:55Z, 31 s before the AtlantaSS commit) with setup 71. The third merge (2 into 1) is a recording pair of the NHMS race file that differed only in the derived preload. No note moved, so `user_data_generation` did not change.

Projection version 2 re-projects lazily: an analysis's views are rebuilt on the first request for any of them (`409 projection_pending`, retry on `analysis_ready`). The first browser run met that (`round1-release.md`); every analysis was then requested once and 649 of 651 answer 200 (two pit-only recordings, sessions 132 and 249, have no track view, as `migration.md` recorded). Container memory after that burst: cgroup `memory.peak` 2,589,077,504 B against the 3 GiB limit, `oom_kill 0`; 382 MiB in `docker stats` once it settled.
