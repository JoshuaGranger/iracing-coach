# P4: can the NAS carry recordings the way D4 assumes?

Run 2026-10-02. Written in stages: this Thresholds section was fixed before any measurement.

## Thresholds (written before measuring, not adjusted afterwards)

PASS requires all of:
- (a) A temp-name-then-rename drop is atomic as seen from a second client: across 20 trials the observer never sees the final name with a partial size or unreadable content.
- (b) A 200 MB copy from the sim PC to the share completes at >= 50 MB/s.
- (c) When the share mapping disappears mid-copy, the final name never appears, the copier receives an error, and the partial temp file is identifiable and removable afterwards.
- (d) The mini PC can enumerate the NAS exports or shares without credentials, or a documented account is the only missing piece.

Any of (a), (b), (c) failing is FAIL for D4 as designed (the fallback design becomes the bulk path). (d) failing is "needs owner", not a fail.

## Method (commands)

Scripts and scratch data (throwaway): `C:/Users/joshu/AppData/Local/Temp/claude/C--Users-joshu-source-repos-iRacing-Coach/478b474b-a1ef-4041-b6b1-2d2558dc1b5f/scratchpad/prove/P4/` (observer2.ps1, observer3.ps1, writer2.ps1, copier.ps1, copier_wt.ps1, speed.ps1). NAS scratch folder `\\192.168.1.82\apps\iracing-coach-spike` was created from the dev PC as the logged-in user `joshu` (no extra credentials; the write worked). Sim PC: iRacingSim64DX11 was not running at start (`Get-Process iRacingSim64DX11` through `remote_job`), Ethernet link 1 Gbps, session `DESKTOP-SIM\joshu`. Dev PC CPU was 44 to 56 % busy during the run (other proofs running); mini PC load average 0.5 to 1.4.

1. Mini PC, no credentials: `ssh g-house-pc '/usr/sbin/showmount -e 192.168.1.82'`; `which smbclient nc`; `timeout 3 bash -c "echo > /dev/tcp/192.168.1.82/PORT"` for 445 and 2049.
2. Create scratch: `New-Item -ItemType Directory '\\192.168.1.82\apps\iracing-coach-spike'` (PowerShell tool).
3. Atomicity: writer on the dev PC (`writer2.ps1`) writes 50 MB (1 MB blocks, `Flush($true)`) to `trial_N.bin.tmp`, then `[IO.File]::Move` to `trial_N.bin`, 2 s gap, 20 trials, recording the SHA-256 of what it wrote. Observer on the sim PC (a second client, started through `remote_job`, script loaded from the share) polls every 50 ms.
   - Run A (`observer2.ps1`): directory enumeration (`DirectoryInfo.GetFiles`) each poll; at first sight of the final name records size plus head and tail 4 KB; full SHA-256 pass at the end.
   - Run B (`observer3.ps1`): each poll opens `trial_N.bin.tmp` and `trial_N.bin` by name (server round trip for existing files) and records handle length, head and tail 4 KB; full SHA-256 pass at the end.
   - Run 0 (first attempt, discarded): the observer hashed inside its polling loop with 4 KB reads and starved its own polling (12 of 20 trials seen). Not counted.
4. Speed: `speed.ps1`, 1 MB-block stream copy of a 200 MB random file (209715200 bytes) to `speed_<tag>_N.bin.tmp`, `Flush($true)`, rename; Stopwatch around the whole copy including flush and rename; 3 runs. From the sim PC through `remote_job` (`powershell.exe -NoProfile -Command ... speed.ps1 -Src C:\...\r200.bin -Dir \\192.168.1.82\apps\iracing-coach-spike -Runs 3 -Tag sim`), and from the dev PC. The file is random data, not a real recording. Link check (emulation of a direct push, not HTTP): `ssh g-house-pc 'cat > /dev/null' < r200.bin`, 3 runs, timed with `date +%s%N`.
5. Disappearance: `net use Y: \\192.168.1.82\apps /persistent:no`; a separate hidden PowerShell process (`copier.ps1` or `copier_wt.ps1`) copies 200 MB to `Y:\iracing-coach-spike\disap*.bin.tmp`, renaming on success; after 6 s `net use Y: /delete /y`. Two variants: default buffered writes, and `FileOptions.WriteThrough`. Afterwards the share was inspected from the sim PC (`Get-ChildItem \\192.168.1.82\apps\iracing-coach-spike disap*`) and from the dev PC.
7. Cleanup: `Remove-Item '\\192.168.1.82\apps\iracing-coach-spike' -Recurse -Force`; `net use Y: /delete /y`; sim scratch `C:\Users\joshu\AppData\Local\Temp\p4-scratch` removed through a job.

## Numbers

(d) Mini PC without credentials
- `showmount -e 192.168.1.82` worked with no credentials: `/volume1/apps 192.168.1.81` and `/volume1/Backups 192.168.1.81`. The apps export is limited to the mini PC's IP; no account is needed for NFS (access is by IP).
- `smbclient`: not installed (only `showmount` and `nc` exist), so SMB share enumeration was not tested. Ports 445 and 2049 are open (`/dev/tcp` check).
- No mount was attempted (a mount is a host change). UID, mode and ownership of files written over SMB, as seen by the mini PC's NFS client, remain UNKNOWN (the open risk already noted in infra.md section D).

(a) Atomicity, 20 trials (observer on the sim PC, writer on the dev PC; observer hashes compared with the writer's hashes)
- Run B (open-by-name probes, 20 of 20 trials): the final name was first seen with length 52428800 every time, head and tail 4 KB non-zero (4075 of 4096 bytes, as expected for the data), 0 anomalies. Full-file SHA-256 of all 20 files read on the sim PC matched the writer's: 20 of 20 equal, 0 mismatches.
- Run A (enumeration; 14 of 20 trials fell inside its 150 s polling window): final name first seen at size 52428800 in 14 of 14, head and tail readable, 14 of 14 hashes equal. Run A did see partial `.tmp` sizes (for example 21364736, 28966912, 34668544 bytes), so the in-flight state is visible through directory listing; no final name ever carried a partial size.
- Caveat on sensitivity: the sim's SMB client caches lookups (listing updated in steps of about 10 s; missing-name answers also cached), so run B's probes never caught a partially written `.tmp` (4 `.tmp` opens, all at full length just before the rename), and run A saw in-flight sizes only coarsely. "Never saw the final name with a partial size" is true, but the observer was not shown to be fast enough to notice one at 50 ms resolution. A stronger test (observer on the mini PC over NFS, or SMB cache lifetimes set to 0) needs a host or system setting change, which was not allowed.
- Writer rename latency (20 trials, run B): 4 to 153 ms.

(b) 200 MB copy to the share (209715200 bytes, temp name, flush, rename; timing includes flush and rename)
- Sim PC (the required measurement), three runs: 27.5 s = 7.26 MB/s; 35.8 s = 5.58 MB/s; 32.6 s = 6.14 MB/s. Best 7.26 MB/s against the 50 MB/s threshold.
- Dev PC, three runs: 29.9 s = 6.69 MB/s; 25.4 s = 7.88 MB/s; 27.2 s = 7.34 MB/s.
- Dev PC to mini PC over SSH (`cat > /dev/null`; emulation of a direct push, encrypted): 33.5 s, 29.4 s, 32.0 s, about 6.0, 6.8, 6.2 MB/s. Same order as the NAS numbers with the NAS not involved, so the ceiling is not the NAS alone. (SSH is not a pure network test, but an N100 encrypts far faster than 6 MB/s.)
- Network facts: sim Ethernet 1 Gbps; dev PC Ethernet 1 Gbps full duplex (idle traffic 0 MB/s when checked); `ping -n 8 192.168.1.82` from the dev PC 0 to 18 ms, average 6 ms (mini PC average 2 ms); tracert shows the NAS one hop away. Cause of the 6 to 8 MB/s ceiling is UNKNOWN (candidates: a slow or congested link segment or switch port, NAS disk or CPU; other proofs ran concurrently and could compete on the LAN).
- Read on the sim PC: 50 MB in 5.1 s (about 10 MB/s) while the observer was also reading.

(c) Share mapping removed mid-copy (dev PC, drive Y:)
- Variant 1, default buffered writes: the copier wrote 200 MB into the local redirector cache in 0.3 s (apparent 690 MB/s); `net use Y: /delete /y` reported "Y: was deleted successfully" at 6 s and the copy still COMPLETED (the flush took 29 s, then the rename). The copier received no error and the final name appeared with the full data. Meanwhile the dev PC reported the full 209715200-byte size for the `.tmp` through UNC while data was still flushing: the size seen on the writing machine is not evidence of what reached the NAS. The threshold's "copier receives an error" does not hold here; the final file was valid, so this is a missed interruption, not corruption.
- Variant 2, write-through: at 6 s `net use Y: /delete /y` printed "There are open files and/or incomplete directory searches pending" then "Y: was deleted successfully". The copier received `System.Management.Automation.MethodInvocationException ... "An unexpected network error occurred."` after 54525952 bytes written; the final name `disap2.bin` did not exist; `disap2.bin.tmp` (55574528 bytes) remained, seen from the sim PC as an identifiable `.tmp` name; it was deleted from the dev PC and absence of both names confirmed (`Test-Path` False).
- So (c) passes only for a write-through copier.

## Result

FAIL for D4 as designed, on threshold (b).
- (a) passes (0 anomalies in 20 trials), with the sensitivity caveat.
- (b) FAILS: best 7.26 MB/s from the sim PC against 50 MB/s (range 5.58 to 7.26). A 146 to 203 MB recording takes about 20 to 36 s.
- (c) passes only with write-through (or hash verification after the rename); a default buffered copy survives the mapping loss without any error.
- (d) PASS: `showmount -e` works with no credentials (apps export IP-limited to the mini PC), so no account is missing for NFS. SMB listing from the mini PC was not tested (no `smbclient`).

## Decision impact

- D4: by the pre-set rule this is FAIL, so the direct hash-verified upload becomes the bulk path. The numbers also show the whole LAN path from both Windows PCs to the NAS and to the mini PC delivering about 6 to 8 MB/s, and a push to the mini PC measured the same order, so the fallback would not by itself be faster. The architect should find the cause of the ceiling (re-measure on a quiet LAN, check link speeds on the NAS and switch ports, `iperf3` or equivalent, none installed) and re-run (b). If the ceiling is removed, the mount design stands (a and c pass with write-through plus hash verification). If not, both transports are bound by it; about 20 to 36 s per recording is acceptable for a background hand-off after the session but fails the stated threshold.
- Collector rule for either transport: never trust a buffered-write acknowledgement or the size on the writing machine; write-through (or reopen and hash after flush) before the rename, and treat the rename as the commit; the mini PC's hash manifest check (already in D4) is the backstop.
- D5 (no bulk transfer while the car is on track) is unaffected and reinforced: a 200 MB copy occupies the link for 20 to 36 s.
- D2, D3, D9: unaffected.

## Direct HTTP upload fallback design (control protocol unchanged)

Goal: the collector-to-server control protocol does not change if the mount proves unreliable; only the bulk path differs.

Control plane (identical in both designs), over the existing direct connection:
1. The collector copies the finalized recording to its own outbox (never touching iRacing's folder) and computes SHA-256 and size.
2. It sends `recording.announce {driver, recordingId, fileName, size, sha256, sessionMeta}`; `recordingId` is derived from SHA-256 plus size, so the same file always has the same id (the idempotency key).
3. The service replies `accepted` with a `transport` field: `nas` (copy to the share, the mini PC ingests from its mount) or `http` (upload). It can also reply `already-have`.
4. The service reports `recording.status {recordingId, state: received | verified | ingested | failed(reason)}` over the same status channel. The collector keeps the outbox file until `ingested`.

Bulk plane, `http` branch only (the `nas` branch is D4: temp name, rename, hash manifest):
- Resumable: `HEAD /v1/uploads/{recordingId}` returns `Upload-Offset` (bytes durable on the server); `PUT /v1/uploads/{recordingId}` with `Content-Range` sends chunks (default 8 MiB). The server appends to `incoming/{recordingId}.part`, fsyncs each chunk and returns the new offset. The collector resumes from the returned offset after any failure. A `.part` idle for 24 h is dropped by the server.
- Hash-verified: the full SHA-256 and size are announced first; the server hashes the assembled `.part`, compares, and only then renames to the final name (atomic on its local disk, so the partial-visibility question of the share does not arise). The whole-file hash is the commit gate.
- Acknowledged: the final request returns `200 {state: verified}` only after the rename; ingest follows on the status channel. The collector deletes its outbox copy only on `ingested`.
- Idempotent: a retry of a finished upload returns `200 already-have`; a repeated chunk at an offset already durable is a no-op if the bytes match (otherwise rejected with the true offset), never appended twice; a second announce for the same id returns the current state.
- Live priority: no uploads while the car is on track (D5); a rate cap (for example 4 MB/s) and a pause while the live page streams; one concurrent upload per driver.
- Storage: the server may write the raw recording onward to the NAS itself (only the mini PC mounts the NAS, NFS, already exported to its IP), keeping "raw recordings live on the NAS" without any NAS account on the sim PC; SQLite and derived data stay off the share.
- Failure behavior: server down only stops chunks (backoff, outbox retained); a corrupt `.part` is dropped and re-requested from offset 0.

## Notes

- Mistake and leftover: my first folder creation ran through the Bash tool, whose double-quoted backslashes turned the UNC path into a local path, so empty folders `C:\192.168.1.82\apps\iracing-coach-spike` were created on the dev PC (nothing on the NAS). I tried to remove them; the tool refuses to delete `C:\192.168.1.82` (protected path) and I did not work around it. needs owner: delete the empty folder tree `C:\192.168.1.82` on the dev PC (it holds no files). The NAS `apps` share was empty before and after (0 items). I then created the real folder with the PowerShell tool.
- The first observer run and its hash slowness were discarded as described; thresholds were written before measuring and not changed.
- The dev PC had other proofs running (CPU 44 to 56 %); contention from them on the LAN cannot be excluded. The sim copy runs happened while the dev PC was idle on the network.
- Cleanup done: NAS scratch folder deleted; drive Y: removed; sim scratch folder removed; no desktop lease was taken (jobs, files and one download only); stored-credential drives K, L, N, O on the dev PC were left as found. One `remote_copy` upload (observer.ps1) returned `uncertain/io_error`; its destination folder did not exist, nothing was written, and I wrote the file with `remote_file` instead (staging expires after 24 idle hours, so a staged transfer may linger on the parent bridge).
- UNKNOWN / not done: mini PC mount (not permitted); uid/mode of SMB-written files as seen over NFS; SMB share listing without credentials from the mini PC; cause of the 6 to 8 MB/s ceiling; behavior with a real `.ibt` instead of random data (content does not matter to the transport).
