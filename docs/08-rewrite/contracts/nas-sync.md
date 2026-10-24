# Automatic NAS library sync (v1)

Owner request 2026-10-24: automatic telemetry/replay uploads and bidirectional STO sync.
Approved browse root: `\\192.168.1.82\apps\iracing-coach\library`.
Owner refinement: no duplicate durable payload copies; sim deletions never delete library
files; a deliberate deletion inside the NAS library must not be undone by synchronization.
HTTP chunk/resume and the dedicated NAS archiver remain the transport. No rig NAS credentials.

## 1. One canonical NAS payload

The library is the durable archive, not an additional export beside raw/ or setups/.
A durable catalog identifies each object by full (kind, SHA-256), with canonical relative path,
size, library identity, legacy path if any, transition state and deletion/error metadata.
Repeated names, occurrences or later driver attribution reuse that same canonical object.
Only one durable NAS payload is created for each full identity. Distinct versions with changed
bytes remain distinct objects. SQLite metadata and STO revision history remain application data;
there is no second durable IBT/replay cache on the mini PC.

```
library/telemetry/<customer_id>/<stem40>--<sha256>.ibt
library/replays/<customer_id-or-unassigned>/<stem40>--<sha256>.rpy
library/setups/<customer_id>/<car>/sources/<stem40>--<sha256>.sto
library/setups/<customer_id>/<car>/coach/<file_id>/<stem40>--v<version>--<sha256>.sto
library/setups/<customer_id>/<car>/inbox/<user-file>.sto
```

A shared hash has one cataloged location, even if another driver/name/revision refers to it.
No second name/path is published for such an alias. Setup revision metadata retains all names
and versions. Inbound files are user-supplied staging: adopt a new quiet inbox file by verified
same-filesystem relocation into its canonical source path, never copy it beside the source.
If the full hash already has a canonical payload, record the new occurrence without creating
another payload or deleting the user's duplicate input. The app never creates an extra copy.

The catalog states distinguish legacy, rename-pending, canonical-verified, conflict and deleted.
Migrate existing raw/setup payloads by hash-verified same-filesystem no-replace rename, with a
durable intent before relocation and commit after verification. No bulk copy. Preserve legacy
manifests/metadata as needed. Recovery checks both planned locations and completes interrupted
renames; a transition is never mistaken for a user deletion. Restage understands catalog paths
and unmigrated legacy paths, but a tombstone prevents all legacy fallback/recreation.

New payload writes use exclusive temp, flush, reopen/hash and atomic no-replace publication.
No permanent hard-link/symlink mirror. Existing unequal content is preserved and reported as
conflict, never overwritten or classified as deleted. Temporary dual directory entries during
a no-replace relocation are permitted only if they name the same physical payload and recovery
removes the old name after verifying the canonical object. Migration cannot leave two durable
independent payload copies. Keep all operations on the archiver thread.

The upload/analysis spool is temporary. Release archived IBT/replay bytes after any active
analysis no longer needs them; queued/deferred work must restage when actually admitted instead
of retaining a second durable copy. Setup working data may be retained by SQLite for versioning;
source setup spool caches must be reconstructible, not a second NAS copy. Never delete original
sim files or the retired desktop app's historical source archive as part of sync.

## 2. Explicit NAS deletion, one-way retention

Deleting any sim-PC IBT/replay/STO never sends a delete to the NAS. A manually removed managed
rig copy stays removed for that exact revision (durable installation receipt); a new revision
can still arrive. No mirror-deletion protocol exists.

For a canonical-verified object only, infer a deliberate library deletion after a successful
NAS identity check and an explicit missing-file result. Check the existing FORMAT plus a unique
library identity marker pinned in SQLite, and the expected mounted root/accessible parent. A
missing or mismatched marker, offline mount, permission/I/O error, partial scan, rename-pending
state or hash mismatch is NOT deletion. Never initialize a replacement library marker when the
catalog already has one. If the entire folder/marker disappears, pause and report instead of
recreating it. Store deletion tombstone durably for full (kind,SHA), covering all aliases and
owners of that one physical payload. This is what deleting a shared payload means.

Return explicit upload state `deleted` for a tombstoned hash, without requesting payload bytes
or claiming it is archived. Existing open/ready uploads are reconciled to this state, and their
owned temporary copies are released once no active reader requires them. Future queued or
re-analysis jobs fail clearly with `library_file_deleted`; they never restage another copy or
silently recreate the deleted NAS object. Existing derived analysis/notes remain. Setup pull
manifests omit deleted payloads, including a working revision whose bytes still exist in SQLite.
Repeated sync/restart/backfill cannot undo deletion. A user can restore a file by placing the
exact matching bytes back at its recorded canonical share path; verify hash before clearing the
tombstone. There is no automatic restoration or in-app destructive library action.

## 3. Quiet NAS setup inbox

Scan only `library/setups/<existing_customer>/<explicit_car>/inbox/`, at most eight levels.
Regular .sto files must be quiet >=10 seconds, 1..16777216 bytes, with size/mtime unchanged
before/after a bounded read and matching SHA. Reject symlinks/reparse points and unsafe paths.
Unknown or ambiguous driver/car is reported; never guess the sole driver. Each changed full
hash is a new locked source occurrence under car-relative `NAS/<inbox-relative-path>`.
Durable receipt (customer,car,normalized inbox path,full SHA) commits with the blob/occurrence;
repeating it is a no-op across retry/restart. Save As creates editable Coach lineage; inbound
edits never silently mutate a Coach revision. Inbox adoption obeys section 1, source purchases
remain locked, and previous distinct bytes remain retained as versions/locked occurrences.

## 4. Replay uploads

UploadKind adds rpy. Scan native `Documents\iRacing\replay` recursively, read-only. Require ten
seconds quiet plus stable size/mtime, background hash, shared read, chunk/resume and durable
acknowledgement. Replays are archive-only: no IBT parser, session, setup or analysis job.
Legacy archive path, for compatibility only: replays/<aa>/<sha>.rpy; new durable payloads go
directly to library/replays. Manifest kind is replay. Other kinds retain their semantics.
Replay limit is min(16 GiB,configured spool cap), other kinds <=4 GiB. Schema envelope16 GiB.
Spool default20 GiB. Transactional cap admission counts ready bytes plus full sizes of all open uploads (already-written incoming bytes plus their remaining reservations), including the new upload. Disk-free admission counts only not-yet-allocated remaining reservations against currently free disk and the configured free-space reserve. Held/resumed hashes reserve no duplicate space. Oversize is
terminal refusal; unavailable space/server/NAS retries. Deleted is an explicit terminal outcome.

## 5. Collector setup pull

GET /api/v1/collectors/{collector_id}/sync?cursor=<opaque> returns CollectorSync:
- schema_version1, replay_upload:true, customer_id integer|null, next_cursor string|null.
- files max128: current Coach working revisions and present NAS imports for persisted last
  driver, excluding deleted/conflicted/unavailable objects. No caller-selected customer id.
- File fields: id,version,full sha256,size1..16777216,relative_path,download_url (existing
  driver-scoped version-specific STO download). Required values validated, unknown fields kept.
- Cursor binds last driver+last stable key. Driver change returns409 Error, collector resets.
  Unknown driver returns empty/null. Failed page never advances; poll60s resets a finished pass.

Exact destinations:
`<car>/Coach Synced/<stem32>--<id12>--v<version>--<hash16>.sto`
`<car>/Coach Synced/NAS/<stem32>--<id12>--<hash16>.sto` for NAS imports.
id12 is first12 hex of SHA256(full file id); hash16 is first16 hex of payload SHA.
Receipts retain full id/version/hash; truncated collisions refuse/report instead of aliasing.
Server authority stays fixed; validate exact driver-scoped download route, no host/redirection.
Bounded downloads are gate-checked during reads and writes, full-hash verified, then atomically
published without replacement. Existing equal bytes no-op; unequal bytes preserved/reported.
Only the collector's own temp may be discarded. A managed file edited by iRacing uploads as a
new locked source; no silent working-revision replacement. Never download sim-origin originals.

## 6. Path budgets and race gate

Car <=64 UTF-16 code units; reject longer rather than redirecting. Export stem40/managed stem32
are deterministic sanitized truncations without splitting surrogate pairs. Reject empty/dot/
dotdot, controls, slash/backslash/colon, trailing dot/space and Windows device names including
names before an extension. Check every ancestor for symlink/reparse traversal. Managed relative
path <=180 UTF-16 units and rig absolute <=240; longer base defers with explicit error. NAS
library relative path <=240 and each component<=200 UTF-16 units; longest synthetic path tested.
Full identities prevent truncation collisions from being treated as equivalent. No overwrite.

All bulk scan/hash/upload/download requires fresh off-track evidence and known non-Race
session, or iRacing closed. Race including garage, unknown and stale pause. Abort in-flight
network work within100ms and resume. Live/capture unchanged. Additive source-folder write
exception is ONLY new files under setups/<car>/Coach Synced; no original/purchased/source file
is renamed/replaced/deleted. Existing source-folder protection otherwise remains authoritative.
Library reconciliation runs bounded every60s, without copying big archive payloads. Errors in
logs/status persist until a later successful pass; unverified/missed files never count as synced.

## 7. Verification

D15 review before dependent code. Test legacy migration/rename crash recovery without copied
payloads, restart/resume, archive-only replay and limits, spool reservations, parent/mount/marker
failure vs deliberate deletion, tombstone suppression including open/ready/queued cases and
aliases, manual restoration hash check, NAS inbox adoption/idempotency, driver/page isolation,
path/symlink/conflict safety, rig delete retention, Race/stale/on-track interruption and recovery.
Use synthetic fixtures; live acceptance only new copies. Do not delete originals for testing.
