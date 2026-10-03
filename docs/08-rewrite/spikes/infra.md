# Spike: infrastructure facts (2026-10-02)

Scope: mini PC, its release tooling, AI key handling, NAS from the dev PC, dev PC toolchain. Sim PC excluded. Machine facts observed 2026-10-02 (~15:56 mini PC clock) unless marked *inferred*. `GH` = `C:/Users/joshu/source/repos/g-house/ops/host`.

## A. Mini PC (`ssh g-house-pc`)

| Fact | Observation (command) |
|---|---|
| OS / kernel | Debian 12 bookworm, 6.1.0-22-amd64 (`cat /etc/os-release; uname -r`) |
| Ports 8200-8299 | None listening; all 100 free (`ss -ltnH`). Taken: 21, 22, 111, 1880, 1883, 3000, 5000, 8091, 8123, 8554, 8555, 9001, 18555, loopback 8080/8081/18554/5432/631, tailnet 443 |
| `g-house-app list` | Empty output: no application installed yet (`/usr/local/sbin/g-house-app list`) |
| Builder stack | `g-house-builder-buildkitd-1  Up 9 days (healthy)`, `moby/buildkit:v0.33.0-rootless`; revision 8b9838eae0cc2ef5 equals recorded (`sudo -n g-house-stack status builder`) |
| Docker | client 27.0.3, server 27.0.3 (`docker version --format ...`) |
| NFS kernel | `nfs`, `nfs4` in /proc/filesystems; `nfsv4` and `nfs` modules loaded |
| CIFS kernel | NOT in /proc/filesystems, not loaded. Module file exists: `/lib/modules/6.1.0-22-amd64/kernel/fs/smb/client/cifs.ko` (`find /lib/modules/...`). Loading it is a host change (not tried) |
| Userland | `nfs-common 1:2.6.2-4+deb12u1` installed; `/sbin/mount.nfs` (setuid) and `/usr/sbin/showmount` present (showmount is not on the publisher's PATH; use full path). `mount.cifs` / `cifs-utils` ABSENT (`ls /sbin/mount.cifs`; `dpkg -l`). Only `libsmbclient` present |
| NAS reachability | 192.168.1.82 ports 445, 2049, 111 all open (`bash -c 'echo > /dev/tcp/192.168.1.82/PORT'`) |
| `showmount -e 192.168.1.82` | `/volume1/apps 192.168.1.81` and `/volume1/Backups 192.168.1.81`. **The apps share is already NFS-exported to the mini PC IP only** |
| Existing NAS mount precedent | `/mnt/nas-backups` is a systemd mount+automount: NFSv4.1 `192.168.1.82:/volume1/Backups`, opts `vers=4.1,hard,timeo=150,retrans=3,noatime,_netdev`, idle timeout 600 s (`GH/systemd/mnt-nas-backups.mount:7-10`, `.automount:17-19`; `mount \| grep nas` shows autofs). Mounting `/volume1/apps` the same way is *inferred* feasible; not tried (a mount is a host change) |
| Load | `uptime`: load 0.96 / 1.47 / 1.53; `free -m`: 15767 total, 7201 used, 8565 available, swap 975/976 used (full); package temp 67 C (`/sys/class/thermal/thermal_zone0`, type x86_pkg_temp, 67000); `sensors` absent. Top consumers: frigate and ffmpeg processes (about 6% CPU each) |
| Disk | `/dev/nvme0n1p2` 467G, 84G used, 360G free (`df -h /srv/g-house`); Frigate media on separate `/dev/sda1` |
| Tool provenance | `/usr/local/sbin/g-house-app` (283 lines, dated Oct 2 13:59) is **not in the g-house git repo** (`git ls-files` shows no such file under `ops/host`); the skill describes it. Installed `g-house-stack` equals `GH/g-house-stack` except the position of `set -Eeuo pipefail` (diff) |

## B. Release tooling: what a Compose file may and may not contain

Sources: `g-house-app` read from the host (`ssh g-house-pc 'cat -n /usr/local/sbin/g-house-app'`, cited `app:LINE`) and `GH/g-house-stack` (`stack:LINE`). The repo has no tests for g-house-app (`ops/tests` holds only `app-data-cutover.test.sh`, `restore-drill.sh`).

**Key finding: the tools do not inspect the Compose file's content.** The only gate is `docker compose config` succeeding (`stack:135`). `activate` copies the committed file verbatim, stripping CR only (`app:202`). Limits below are skill convention unless a line is cited.

| Question | Answer and deciding code |
|---|---|
| CIFS/NFS named volume in Compose | No code rejects it (`stack:135`, `app:202-204`). Compose runs as root (`stack:60-63`). NFS: kernel nfs + `mount.nfs` present, so a `driver_opts` volume (`type: nfs`, `o: addr=192.168.1.82,nfsvers=4.1,...`, `device: :/volume1/apps/iracing-coach`) is *inferred* workable, untested. CIFS: no `mount.cifs`, module not loaded, so **will not work today**. A bad mount fails at `compose up` (`stack:141`) |
| Extra bind mounts outside `APP_DATA` | Not blocked by code. Skill: APP_DATA is "the only place the app may keep state" (convention). Recovery copies only `RECOVERY_PATHS` under APP_DATA (`app:181-185`), so a NAS mount is outside recovery |
| `cpus`, `cpu_shares`, `mem_limit` | Not validated or required; `mem_limit` is skill convention. Standard Compose keys. N100 has 4 cores shared with Frigate |
| More than one service | Allowed. Health-gated services are those in `HEALTH_SERVICES` (`app:179-180`, regex `^[a-z0-9_ -]*$`); others are not gated. Only one image is built per app (`app:108-146`); other services must use a published image (skill: pinned by digest). `compose pull --quiet` runs on every apply (`stack:140`), so the built image needs `pull_policy: never`. `up --detach --no-build` (`stack:141`) means a `build:` section never runs |
| Multi-stage Dockerfile | Fine: `buildctl build --frontend dockerfile.v0 ... --opt platform=linux/amd64` (`app:136-144`); DOCKERFILE and CONTEXT come from app.env as plain relative paths `^[A-Za-z0-9._/-]+$`, no `..` (`app:86-89`) |
| Build arguments | **Not supported**: no `--opt build-arg:` is passed (`app:136-145`). Only revision and created labels are set. Build config must live in the repo or Dockerfile |
| Build context | Committed tree at that SHA only (`git archive`, `app:129`); untracked and ignored files never reach the build. Builds serialize on `build.lock` (`app:123-124`); platform fixed to linux/amd64 |
| Healthcheck requirements | None mandatory. Each HEALTH_SERVICE must be running with health `healthy` or `none` (`stack:148-150`), polled 60 x 2 s = **120 s budget** (`stack:143-154`), else failure (`stack:155`). A slow start (large migration) must fit in 120 s. Empty HEALTH_SERVICES passes trivially |
| Secrets to env vars | `set` appends `KEY=value` to root-only `/etc/g-house/stacks/<app>/stack.env` (0600) from stdin (`app:244-252`); key `^[A-Z][A-Z0-9_]{0,63}$` (`app:241`); managed keys PROJECT_NAME, PROJECT_DIR, APP_IMAGE, APP_DATA, HEALTH_SERVICES, RECOVERY_PATHS, RECOVERY_SQLITE cannot be set (`app:53,242`); single line, no CR (`app:244-245`). Compose reads `--env-file stack.env` (`stack:61-62`), so a container sees a secret only if the Compose file references `${KEY}` under `environment:`. Applies on `sudo -n g-house-stack apply <app>`. Log keeps key + 12-char sha256 prefix (`app:254-256`). Requires an existing stack (`app:243`): release once, set, apply |
| Recovery point contents | `stack:80-127`: tar of each `RECOVERY_PATHS` entry (must exist, absolute; `stack:88`) excluding `*.db-wal/-shm`, `*.sqlite-wal/-shm` and listed SQLite files; each `RECOVERY_SQLITE` file through the sqlite3 backup API with `integrity_check` (`stack:96-113`); plus compose file, container list, image digests (`stack:115-119`); sha256 manifest; uncompressed 0600 tar in `/srv/g-house/recovery/<app>/`; **last three kept** (`stack:125`). Default path `.` = whole data dir (`app:181-185`), so a multi-GB data dir is tarred on every release: keep `RECOVERY_PATHS` small (config + SQLite) and derived data outside it. NAS recordings are not covered |
| Failed release rollback | `activate` snapshots previous compose + stack.env (`app:195-199`); if `g-house-stack apply` fails it restores both and re-applies (`app:221-227`). With no earlier release it only fails (`app:229`) and leaves the failed containers. **Data is not rolled back**; the recovery tar is a manual restore. One-way schema migrations on start are therefore a risk. Images kept: current + previous (`app:209-216`) |
| App name | `^[a-z][a-z0-9-]{1,30}$`, not `g-house*`, `home-automation`, `builder`, `docker` (`app:63-69`); `iracing-coach` valid |
| Locking | host-wide `deploy.lock`, `flock -n`: fails rather than waits (`stack:131-133`) |

## C. How g-house applications hold an AI API key

- Provider: **OpenAI** (`OPENAI_PROVIDER_KEY = 'openai'`, `packages/providers/src/ai/openai.ts:84`; header `authorization: Bearer ${apiKey}`, `openai.ts:253`). No Anthropic adapter; `ANTHROPIC_API_KEY` appears only in a log-redaction test list (`packages/observability/src/__tests__/redaction.test.ts:43`).
- **No environment variable holds the AI key, by design**: `.env.example:109-112` says there is no operator AI key and provider credentials do not belong in the environment.
- A household adult enters it in the web UI (Household > Connections). It is encrypted with `encryptConnectionSecret` into DB column `aiServiceConnections.encryptedSecret` (`packages/application/src/connections/ai-service.ts:609-629`) and decrypted per use (`ai-service.ts:883,912`). The encryption master secret is env `SAGE_CONNECTIONS_SECRET` (min 32 chars, required in production; `apps/api/src/config.ts:44,114-115`; `.env.example:68`).
- Implication for D10: "the way g-house apps do" means an in-app, DB-stored, encrypted key plus a root-only master env secret. The simpler option the tooling supports is one env var set with `g-house-app set iracing-coach <NAME>`. Architect to choose. (No secret value was read or printed.)

## D. NAS `\\192.168.1.82\apps` from this PC

- `Test-Path` True; `Get-ChildItem -Force`: **0 items, the share is empty**. `iracing-coach` does not exist.
- Free space: 4.27 TB free of 7.0 TB, volume-wide (`fsutil volume diskfree \\192.168.1.82\apps`; quota free equal). No per-share quota seen.
- ACL (`Get-Acl`): owner `S-1-22-1-0` (Unix uid 0); one inheritable ACE (OI/CI), `S-1-5-21-479224438-975903738-3019451733-1203`, mask 0x1301ff = Modify. SID not resolvable from here. A stored Windows credential for 192.168.1.82 exists (`cmdkey /list`) and shares K/L/N/O map to it (currently Disconnected). **Inferred**: the current user can create a folder if that SID is the stored account; not proven (nothing created).
- NFS export of `/volume1/apps` is limited to 192.168.1.81, so the sim PC reaches the share through SMB (*inferred*).
- Not checked: how files written over SMB appear (uid/mode) to the mini PC's NFS client. Risk for the collector to mini PC path; needs a test with a real file.

## E. Dev PC (DESKTOP-JOSHUA)

| Item | Result |
|---|---|
| cargo | `cargo 1.99.0 (5f94df478 2026-08-27)` (`C:/Users/joshu/.cargo/bin/cargo.exe --version`) |
| rustc | `rustc 1.99.0 (b940084d7 2026-09-28)` |
| node / pnpm | v24.19.0 / 9.15.4 |
| WSL Ubuntu | Ubuntu 24.04.1 LTS, kernel 5.15.167.4-microsoft-standard-WSL2, Python 3.12.3 |
| In WSL | `gcc`, `cc`, `make` present; **`cargo`, `rustc`, `pkg-config` missing** (no `~/.cargo`) |
| Not checked | MSVC target and Linux targets (musl) were not verified. Docker exists only on the mini PC builder |

## Surprises and notes

- `/usr/local/sbin/g-house-app` is not in the g-house repo; its source of truth is the host.
- Mini PC is under pressure: swap full, 8.5 GB available, 67 C package, Frigate busy. A 2.1-2.3 GiB peak analysis worker needs a sized `mem_limit` and one job at a time.
- Housekeeping disclosure: while diffing I ran `cat > /tmp/g-stack-installed` on the mini PC, leaving an empty file in `/tmp`. Nothing else was changed on any machine.
