# NAS layout and write protocol (v1)

P4 failed on throughput, so by the D4 ruling the bulk path is the collector's HTTP upload to the server (`collector-protocol.md` section 4) and the **server is the only writer of the NAS folder**. The sim PC never mounts or writes the share. The measured ceiling (about 6 to 8 MB/s) bounds archiving speed; nothing waits on it except archiving, restaging and NAS backups.

## 1. Mount

NFSv4.1 export `192.168.1.82:/volume1/apps` (admits 192.168.1.81 only, no credentials), mounted by the Compose volume `nas` at `/nas/apps`, options `soft,timeo=150,retrans=3,noatime`. `soft` makes a call to an unreachable NAS fail with `EIO`/`ETIMEDOUT` after about 45 s instead of blocking the archiver thread uninterruptibly, so the process can still shut down and a release can still recover. A soft mount can report a short or failed write; that is safe here only because every write is verified after flush (section 3 step 5) and committed by rename with the manifest last, and every read is hash-checked. The archiver treats those errors as retryable. Root of this contract: `/nas/apps/iracing-coach/` (`$NAS`). The server creates `$NAS` at first start if absent.

## 2. Layout

```
$NAS/FORMAT                               "iracing-coach-nas 1\n" (written once)
$NAS/raw/<aa>/<sha256>.ibt                recording, immutable
$NAS/raw/<aa>/<sha256>.manifest.json      manifest (manifest.schema.json, kind=recording)
$NAS/setups/<aa>/<sha256>.sto|.htm        setup file, immutable
$NAS/setups/<aa>/<sha256>.manifest.json   manifest (kind=setup_file)
$NAS/backups/<yyyy-mm-dd>/                daily user-data snapshot (kind=backup manifest inside)
$NAS/.tmp/                                in-flight temp files
$NAS/.probe/                              startup write probe
$NAS/quarantine/                          final-name files that failed verification (never deleted by the server)
```

`<sha256>` is lowercase hex of the whole file; `<aa>` its first two characters. Content addressing makes every write idempotent and lets the same file arriving twice (rename on the sim, re-upload, migration) collapse to one object. Original names live in manifests and SQLite, never in paths.

## 3. Atomic write (archiver thread)

For each object (data file, then its manifest):

1. If the final name exists and its manifest exists with equal `sha256` and `size`, the object is archived: stop.
2. If the final data name exists without a manifest (crash between steps 6 and 7), re-hash it. Equal: go to step 7. Different: rename it to `quarantine/<sha256>.<utc>.ibt`, continue at step 3.
3. Create `$NAS/.tmp/<sha256>.<uuid>.tmp` with `O_CREAT|O_EXCL`.
4. Write all bytes from the spool, `fsync`, close.
5. **Hash after flush:** reopen, `posix_fadvise(POSIX_FADV_DONTNEED)` to drop the client cache, read back, SHA-256. Mismatch: delete this temp file, count a failure, retry later (backoff 1 min to 1 h).
6. `mkdir -p raw/<aa>`, `rename` temp to the final name, `fsync` the directory.
7. Write the manifest the same way (temp, fsync, rename).
8. In SQLite set `blobs.nas_state = 'archived'`, `nas_archived_at`.

The rename is the commit; a reader trusts a data file only when its manifest exists. Buffered writes are never trusted (P4 (c): a buffered copy reported success after the share vanished).

## 4. Reads

| Reader | Reads | When |
|---|---|---|
| Archiver (restage) | `raw/<aa>/<sha>.ibt` to `/data/spool/ready/<sha>.ibt` with hash check; the bytes are reserved first through the upload admission (`collector-protocol.md` section 4: restaged bytes count toward 20 GB and the 4 GiB free rule; a refused restage waits), and the file gets the blob's `source_mtime` with the commit clamp `min(source_mtime, now - 10 s)` and is fsynced before it is renamed into `ready/` | re-analysis of a recording whose spool copy is gone |
| Archiver (rebuild) | all `*.manifest.json` | disaster recovery: rebuild `blobs` and `recordings` rows from manifests (`coach-server rebuild-index`) |
| Migration check | manifests | D12 completeness report |

Nothing else reads the NAS. The Python worker never sees `/nas` (its roots are on NVMe).

## 5. Backups

Hourly when `user_data_generation` (`db-schema.sql`, `server_counters`) has changed, daily at 04:00 local, and on demand, the scheduler takes one **snapshot generation**:

1. Hold the light lane (wait for the running light job; queue new ones) and read `user_data_generation` in the same transaction that starts the SQLite backup. User-data writes through the API stay possible; they bump the generation and are caught by the next snapshot.
2. Write a new immutable folder `/data/snapshots/g<generation>/` (SQLite backup API for `db/coach.sqlite3` and each `py/<customer_id>/archive/history.sqlite3`, plus copies of `py/<customer_id>/archive/{tuning,trials}/`), then its `manifest.json` (kind `backup`, file hashes) last, then release the light lane. `latest` is a symlink switched by rename after the manifest exists; the archiver copies a generation folder, never `latest`.
3. The archiver copies the generation to `$NAS/.tmp/backup-<yyyy-mm-dd>-<uuid>/` with the section 3 steps per file (manifest last), then renames the folder to `$NAS/backups/<yyyy-mm-dd>/`. The day is complete only when that folder exists with a manifest whose hashes match; a crash leaves only a `.tmp` folder, which start-up deletes and the next run repeats. An existing complete day folder is left alone.

Backups are a few MB; none are pruned in v1. Local generations other than the newest 48 are deleted.

## 6. Crash recovery

At start the archiver:
- deletes `$NAS/.tmp/*.tmp`, `$NAS/.tmp/backup-*/` and `$NAS/.probe/*` (its own temp files; their content still exists in the spool or a local snapshot generation, or is a probe);
- writes and reads back a probe file; reports `nas: ok | readonly | unreachable | stalled` in `/api/v1/status` (`stalled` = an NAS call made no progress for 30 s);
- requeues every `blobs` row with `nas_state` in (`pending`, `failed`) whose spool file exists.

## 7. Deletion rules

| Who | May delete | Never deletes |
|---|---|---|
| Server, NAS | its own `.tmp/` and `.probe/` files and folders | anything under `raw/`, `setups/`, `backups/`, `quarantine/` |
| Server, spool (`/data/spool`) | `incoming/*.part` idle 24 h or failing hash verification; `ready/<sha>` once archived and no queued or running job references it | a `ready/` file whose blob is not `archived`; **setup files** (`.sto`, `.htm`: every blob whose kind is not `ibt`) at all in v1, see the note below |
| Server, Python roots | the job root `py/<id>/jobs/<job_id>/` (hard links only) after the job; after a killed job, its `*.<pid>.tmp` files at once, and `.write-*` / `.copy-*` under that driver's archive only when no worker process for that driver is running (cleanup deferred until both lanes are idle for the driver) | reports, `history.sqlite3`, `tuning/`, `trials/` |
| Server, snapshots | local generations beyond the newest 48 | the newest complete generation |
| Collector (sim) | its own journal entries after `verified`, its own rotated logs, its own temp config files | anything under `Documents\iRacing` (telemetry, setups, replays), and the old app's `Documents\iRacing Coach` data root |

Setup-file exception (`server/src/handlers.rs` `release_spool`, which releases only `kind = 'ibt'` blobs). A setup blob stays in `/data/spool/ready/<sha>` after its `nas_state` is `archived`, and `blobs.spool_present` stays 1. Why: `worker-interface.md` section 3 builds each job root's `setups/` tree by hard-linking the spool file of every present setup file (the query requires `spool_present = 1`), and only recordings have a restage path from the NAS (section 4); a released setup blob would silently vanish from every later job root. Cost: a few KB per file, counted in spool bytes. The NAS copy is the durable one; the spool copy is the working copy. A setup spool file may be released only after the server can restage setup blobs from `$NAS/setups/` into a job root on demand (a contract change; not in v1). The `incoming/*.part` rule applies to setup uploads as to recordings. Start-up still clears `spool_present` for a spool file that is missing.

The collector keeps no outbox copies; it uploads from iRacing's file in place.
