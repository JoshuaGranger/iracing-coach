# Collector protocol (version 1)

The one direct connection from the sim-PC collector to the server, plus the HTTP upload it uses for bulk files. Message payloads are the `Collector*` schemas in `api.openapi.yaml` (generated into Rust and TS); this document fixes behaviour. Live frames: `live-frame.md`.

## 1. Transport

| Purpose | Endpoint |
|---|---|
| Control, presence, acknowledgements, live frames | `ws://<server>/ws/collector`, subprotocol `coach-collector.1` |
| Bulk upload | `http://<server>/api/v1/uploads...` (section 4) |

`<server>` is the only pairing (D7): `http://192.168.1.81:8210` by default, set at install or from the tray. The collector always dials out; the server never connects to the sim PC. JSON messages are WebSocket text frames, one object each, with a `type` field. Live frames are binary frames. No authentication, no TLS (D7).

## 2. Identity

- `collector_id`: a UUID v4 generated at install and kept in `collector.json`. Identifies the machine, not the person.
- Driver: from the live session info, `DriverInfo.Drivers[DriverCarIdx].UserID` (iRacing customer id) and `UserName`, sent in `status`. For recordings the authoritative driver is the one the server parses from the IBT at ingest. Data is keyed by customer id (D7).

## 3. Connection lifecycle

1. Connect. Client sends `hello {protocol: 1, collector_id, machine, collector_version, live_table_version, os}` within 5 s (`CollectorHello` in `api.openapi.yaml` is normative).
2. Server replies `welcome {protocol: 1, server_version, stream_wanted, viewers, layout}`; or closes with code 4001 `incompatible_protocol` when the major differs. On 4001 the collector keeps capturing, keeps the overlay and journal, shows "Collector update needed" in the tray, and retries every 10 min.
3. Client sends its current `status`, then replays journal garage events (oldest first). Pending uploads restart over HTTP independently.
4. Steady state: client `status` on every change and at least every 5 s; server `ping` every 15 s, client answers `pong`. The server marks the collector offline after 15 s without any message and closes the socket.
5. Reconnect after any close or error: backoff 1, 2, 4, 8, 16, 30 s (cap), +/- 20 % jitter; reset after 60 s connected.

## 4. Bulk upload (D4 ruling: resumable, hash-verified, acknowledged, idempotent)

Applies to `.ibt` recordings and to `.sto` / `.htm` setup files.

1. **Gate (collector).** A recording is ready when: record count > 0, size equals buffer offset + record count x buffer length, unchanged for 2 s, opens with read sharing. Uploads, hashing and folder scans run only while the collector holds **fresh off-track evidence**: iRacing not running (no map, meaning the open fails because the mapping does not exist; any other open or header-read failure is unknown evidence; or status bit 0 clear for at least 2 s), or live data (status bit 0 set, tick advanced within the last 1 s) with `IsOnTrack == 0`. Anything else, including stale or unknown `IsOnTrack`, counts as on track. The uploader and hasher check before each 4 MiB chunk or block and abort an in-flight chunk within 100 ms when the evidence lapses (the server keeps what was fsynced); a paused hash restarts later from the gate. All file work runs on the uploader and watcher threads, never on the SDK reader thread. One upload at a time, newest recording first, setup files after recordings.
2. **Hash.** SHA-256 of the whole file at background I/O priority, under the same off-track rule. If size or mtime changes while hashing, restart the gate.
3. **Create** `POST /api/v1/uploads` with `UploadCreate {sha256, size, kind, file_name, relative_path, source_mtime, collector_id, customer_id}`. Idempotent by `sha256` for the bytes; **every** create also records an occurrence (`blob_occurrences`: sha256, kind, file name, relative path, source mtime, collector, `customer_id` as the collector last saw it, or the collector's last driver when null), including when the bytes are already held, so a renamed or moved `.sto` still updates `setup_files`. Responses:
   - `201 {state: "open", offset: 0}` new upload;
   - `200 {state: "open", offset: N}` resume (N bytes durable);
   - `200 {state: "verified" | "parsed" | "archived" | ...}` server already has it: the collector drops the journal entry;
   - `503` with `Retry-After` when the reservation below cannot be made; `413` when `size` exceeds 4 GiB.
   **Reservation.** A create (or resume) reserves the upload's remaining bytes. It is refused with 503 when reserved bytes + `ready/` bytes + restaged bytes would exceed 20 GB, or when the `/data` filesystem would keep less than 4 GiB free (headroom for SQLite, WAL, snapshots and view files). The reservation is released on verify, on hash mismatch, and when an `.part` is idle for 24 h.
4. **Chunks** `PUT /api/v1/uploads/{sha256}` with `Content-Range: bytes start-end/size`, body at most 8 MiB (collector uses 4 MiB). Chunks for one `sha256` are serialized by a per-hash lock (a second concurrent request gets `409 {offset}`). The server appends to `incoming/<sha256>.part` only if `start` equals the `.part` length, fsyncs the file, then updates `uploads.durable_offset`, and only then returns `200 {state: "open", offset}`. A chunk at an already-durable range with identical bytes is a no-op returning the current offset; different bytes or a gap returns `409 {offset}` and the collector continues from `offset`. A write or fsync failure (including `ENOSPC`) truncates the `.part` back to `durable_offset` and returns `503` with `Retry-After`; nothing unacknowledged is ever reported durable.
5. **Commit.** On the chunk that completes `size` bytes the server hashes the `.part` from disk, compares with `sha256`, sets the file's mtime to `min(source_mtime, server now - 10 s)` (the worker's 2 s quiet rule reads mtime; the clamp absorbs clock skew between the PCs), renames it to `ready/<sha256>` (same filesystem), fsyncs the directory, then in one SQLite transaction inserts the blob and marks the upload `verified`, and returns `200 {state: "verified"}`. Mismatch: the `.part` is deleted, `422 {error: "hash_mismatch"}`, the collector re-hashes and starts again from 0.
6. **Acknowledgement.** `verified` means durable on the server NVMe; the collector drops the journal entry then. Later states arrive as `ingest.state` messages for the tray and are not required (`parsed`, `archived`, `analysis_queued`, `analyzed`, `deferred`, `failed`).

`HEAD /api/v1/uploads/{sha256}` returns `Upload-Offset` and `Upload-State` headers for a resume without a create.

**Restart reconciliation** (server start, before the HTTP listener opens). Every file/DB combination has one outcome. Any recovery that ends in `verified` first fsyncs the file (after setting its mtime) and the directories of both rename ends, exactly as step 5 does:

| On disk | SQLite | Action |
|---|---|---|
| `ready/<sha>` | blob row | nothing |
| `ready/<sha>` | no blob row (crash between rename and transaction) | re-hash; equal: insert blob, mark upload `verified`, queue ingest; different: move to `incoming/<sha>.bad` and delete it, upload back to `open` at 0 |
| `incoming/<sha>.part` | upload `open` | offset = `.part` length; set `durable_offset` to it (a torn tail is caught by the whole-file hash at commit, which restarts from 0) |
| `incoming/<sha>.part` of exactly `size` bytes | upload `open` | run step 5 |
| `incoming/<sha>.part` | no upload row | delete the `.part` |
| none | upload `open` with offset > 0 | reset to 0 |
| none | blob row with `spool_present = 1` | set `spool_present = 0` (restage from the NAS when needed) |

## 5. Presence

`status` carries `{sim: "not_running" | "menus" | "in_session", connected, on_track, in_garage, on_pit_road, session: {subsession_id, session_num, session_type, track, track_config, car_path, session_epoch} | null, driver: {customer_id, name} | null, streaming, queue: {uploads_pending, bytes_pending}}`. `sim` is `not_running` when there is no map, status bit 0 is clear, or the data is stale (tick not advanced within 2 s; `crate-api.md` 4.2 `Sample.connected`); `in_session` when live data has `PlayerCarIdx >= 0` and session info with `DriverInfo.DriverCarIdx`; `menus` for live data without them. The web UI shows "Live" for a driver whenever a connected collector reports that driver with `sim = "in_session"` (D5). The server forwards presence to browsers as SSE `presence` events.

## 6. Live stream control (D5, D6)

- The server keeps `viewers` = number of open `/ws/live/{driverId}` browser sockets for the collector's current driver.
Three separate states, each with one owner:

| State | Owner | Meaning |
|---|---|---|
| viewer demand | server | `viewers` > 0 for the collector's current driver |
| session latch | collector | streaming was wanted at some time during the current **stream session** |
| engine epoch | collector | `session_epoch` of `live-frame.md`; resets the engine (it also changes on lap or time resets) and never controls streaming |

A **stream session** ends only when the sim disconnects, or `SubSessionID`, `SessionNum` or `SessionUniqueID` changes, or the player's `UserID` changes (driver change). Epoch changes from lap or time resets do not end it.

- `viewers` goes 0 to 1: server sends `stream.start {reason: "viewer"}`. The collector sets the latch and streams (at most 60 frames/s, `live-frame.md` Rules) while connected to the sim.
- `viewers` goes to 0: server sends `stream.release`. The collector keeps streaming while the latch is set and clears it, then stops, when the stream session ends. A `stream.start` before that cancels the release.
- While viewer demand stands, every new stream session (after a boundary or a sim reconnect) starts with the latch set, so a viewer who leaves mid-session still gets the rest of that session.
- Viewers are counted for the collector's current driver. When the driver in `status` changes, the server recounts for the new driver and sends `viewers {count}` and `stream.start` or `stream.release` to match; demand belonging to the previous driver never carries over.
- Reconnect or server restart: `welcome.stream_wanted` reports viewer demand only. `true` acts as `stream.start`; `false` acts as `stream.release` (an existing latch is kept until the stream session ends). A collector restart starts with the latch clear.
- The server also sends `viewers {count}` on every change; the tray shows the count.
- Rates per browser (1, 10, 30, 60 Hz) are chosen by the browser and applied by the server by decimation; the collector always sends at the wire rate of `live-frame.md`.

## 7. Backpressure

- Collector: frames go through a bounded queue of 30 (0.5 s at 60 Hz). On overflow the oldest frame is dropped and `dropped_frames` increments; the reader thread never blocks. Every frame is self-contained, so drops lose time resolution, not state.
- Server: per-browser queue of 1 s of frames at that browser's rate; on overflow it discards the queue and sends the newest frame. A browser that stays behind for 10 s is closed with 4008 and reconnects.
- The collector sends `stream.stats {sent, dropped, since}` every 10 s while streaming.
- Uploads never run while streaming on track (the on-track gate covers it).

## 8. Garage capture (D9, P5)

State per garage visit. The reader thread detects the events and hands them to the net thread; hashing and file search run on the uploader thread under the section 4 off-track rule (a `.sto` is a few KB):

| Event | Collector action |
|---|---|
| `IsInGarage` 0 to 1 (status bit 0 set, tick advancing) | open a visit: `visit_id` UUID, remember `DriverSetupName` at entry |
| `CarSetup.UpdateCount` changes while in the garage | send `garage.snapshot {visit_id, update_count, observed_at, car_path, values}` (all `CarSetup` leaf keys as published strings) |
| `IsInGarage` 1 to 0, then `DriverSetupName` changes within 5 s | hash `setups\<car_path>\<name>` (search subfolders if absent; several matches = `sto_sha256: null, ambiguous: true`), send `garage.commit {visit_id, update_count, setup_name, sto_sha256, is_modified: DriverSetupIsModified, values}` with the values and `update_count` of the last snapshot; queue the `.sto` upload |
| `IsInGarage` 1 to 0 and the name does not change within 5 s | send `garage.commit` with `setup_name: null` (values only; the session-start name is not attached) |

Snapshots before the last in a visit stay "values seen, name unknown"; the server never attaches the final name to them. `DriverSetupIsModified` is read only at exit.

Edge rules (wave 1, from P5 and the collector):
- A `CarSetup.UpdateCount` change seen up to 5 s after `IsInGarage` went 1 to 0 belongs to the visit (P5: Apply & Close published the last count after the exit): it is sent as a snapshot of that visit and becomes the commit's values.
- A visit with no `UpdateCount` change and no name change sends no commit (it has no values to record).
- A name change with no `UpdateCount` change in the visit sends one snapshot at the current count first, so the commit carries values.
- Every commit with a `setup_name` queues that `.sto` for upload (section 9 rules).
- Re-entering the garage inside the 5 s window closes the open visit first, before any snapshot is taken: its commit uses its own last snapshot and is decided as if the window had ended; then a new visit opens, and its first `UpdateCount` change is that visit's snapshot.

**Delivery.** Every snapshot and commit is appended to the journal before it is sent, online or offline (snapshots are written while the car is in the garage; a commit's journal entry references its last snapshot by `(visit_id, update_count)` instead of repeating the values, so it stays under 1 KB). The server inserts the observation and sends `garage.ack {visit_id, kind, update_count}` only after the SQLite transaction commits; the collector drops the journal entry on that ack. Unacknowledged entries are resent in order after every `welcome`. The server deduplicates by `(visit_id, update_count)` for snapshots and by `visit_id` for commits (unique indexes in `db-schema.sql`) and acknowledges a duplicate without inserting it.

**Modified setups.** A commit links its values to the named `.sto` file only when `is_modified` is `false`. The link is not stored: the server derives `SetupFileEntry.linked_setup_id` from `setup_observations` when it answers, from the first observation that is an unmodified (`is_modified = 0`) garage commit or recording whose `sto_sha256` equals the file's hash, or, when it has no hash, whose `setup_name` equals the file's name and whose car equals the file's car folder (both ASCII case-insensitive), or an `htm_export` of that blob. The name-only fallback never applies to a garage commit of an ambiguous visit (`ambiguous: true`, `garage_visits.ambiguous_name = 1`): the collector withheld the hash because it could not tell which file was loaded.

**Attribution.** A garage event is acknowledged only after its values are stored as an observation. Its driver is the event's `driver`, else the collector's last reported driver (`collectors.last_customer_id`); its car is the event's `car_path`, else the visit's. An event that still has no driver or car is not acknowledged and stays in the collector's journal until a later `welcome` resend can be attributed. With `is_modified` `true` or `null`, the observation records the name and hash as the file that was loaded, and the values as a setup in their own right; it never establishes the `.sto`'s contents. The same rule applies to recording observations (`DriverSetupIsModified` from the IBT session info).

## 9. Setup files and layouts

- Setup files: at start and after directory events (off track only, section 4 rule), the collector lists `setups\**\*.sto|*.htm`, hashes changed files, and uploads unknown ones (kind `sto` / `htm`, `relative_path` under `setups\`). It never writes the setups folder.
- Layouts: the server sends `layout.set {layout_id, version, layout}` on `welcome` and whenever the active live layout changes; the collector applies it, caches it in `collector.json`, and answers `layout.ack {layout_id, version}`. Placement, scale and monitor stay machine-local.

## 10. Versioning

- WebSocket: `protocol` integer in `hello` and `welcome`; additive optional fields keep the number; anything else increments it and the server supports the previous number for one release.
- HTTP: path `/api/v1`; same additive rule.
- Live frames: own version byte (`live-frame.md`).
- Unknown message `type`: ignored and logged once per type. Missing required field: `error {code: "bad_message"}` and the message is dropped.

## 11. Close codes

| Code | Meaning | Collector reaction |
|---|---|---|
| 1000 | normal | reconnect with backoff |
| 4001 | incompatible protocol | keep capturing; uploads continue while `POST /api/v1/uploads` succeeds; retry the WebSocket every 10 min |
| 4002 | duplicate `collector_id` connected (newest wins) | the older socket stops reconnecting for 60 s |
| 4008 | too slow (browser sockets only) | n/a |
