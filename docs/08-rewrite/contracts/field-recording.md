# Durable field recording, version 1

The companion records opponent race history independently of live-page viewers.
This improves comparing pace and pit timing; it does not provide opponents' private fuel or tire service.

## Capture and format

Upload kind `field` uses the existing hash-verified resumable upload protocol and fresh off-track gate. It is never a player IBT or setup file. Immutable `.field.jsonl` segments contain at most 60 seconds and 64 MiB. SDK sampling is at most 10 Hz, with a bounded nonblocking queue to a dedicated disk writer. Missing samples remain gaps. The SDK thread performs no file or network I/O. Local storage is bounded; unacknowledged recordings are never evicted to make space. Capture errors/gaps are logged and surfaced in collector diagnostics. Restart recovers complete lines from interrupted segments while preserving the original interrupted bytes.

Each JSON line is an object with `schema_version: 1`, integer `captured_unix_ms`, integer `tick`, integer `session_info_update`, and object `channels`. `session_info` is SDK YAML text, mandatory on each segment's first row and supplied again when updated. Unknown optional properties survive archival; incompatible versions and malformed required fields fail explicitly.

Channels: SessionTime, SessionNum, SessionUniqueID, SessionState, SessionFlags, PlayerCarIdx, CarIdxLap, CarIdxLapCompleted, CarIdxLapDistPct, CarIdxPosition, CarIdxClassPosition, CarIdxOnPitRoad, CarIdxTrackSurface, CarIdxLastLapTime, CarIdxBestLapTime. Scalars or arrays retain SDK units. Missing/nonfinite values are null. Per-car arrays have at most 64 entries. Rows are bounded to 1 MiB. Collector-provided driver_id and subsession_id may appear as optional hints; server identity comes from the recorded SDK session info.

SessionTime is required, finite and nonnegative (seconds); SessionNum is a required nonnegative integer. Other scalars are optional integer/null. CarIdxLapDistPct/LastLapTime/BestLapTime are arrays of finite number/null; CarIdxOnPitRoad is boolean/null; other CarIdx arrays are integer/null. A missing channel may be absent or null. Arrays may be shorter than 64; an absent index means missing, never another car. No array position is compacted. Local retained storage is capped at 2 GiB; reaching that cap stops capture and reports the gap, preserving unacknowledged bytes.

## Ingest and association

The server validates the segment, takes player UserID from DriverInfo.DriverCarIdx's driver, and associates by that player, positive WeekendInfo.SubSessionID and sample SessionNum. Samples with changed identity belong in another segment. Offline recordings without a positive SubSessionID are archived but are not guessed into sessions. Segments can arrive before or after an IBT. Matching field segment hashes participate in analysis membership and cache identity; a late segment requeues affected analyses and stale in-flight results cannot replace the latest membership. Duplicate uploads are idempotent.

Raw segments and manifests are archived under `field/<sha-prefix>/<sha>.field`; manifest kind is `field_recording` with `field_recording` metadata {customer_id, subsession_id, session_num, sample_count}. Rebuild-index restores their index and matching behavior. Existing upload, blob and occurrence tables accept kind field; SQLite migration preserves existing rows and checks foreign keys before commit.

## Worker

The server stages only identity-matched segments in the job root's `field/` directory. Python streams these files and calculates competitor summaries from raw samples, independent of player IBT sample indices. Ordered samples are scoped to the recording's phase, deduplicated, and gaps/reset time do not create invented laps or pit visits. Player tire/fuel evidence continues to come only from the IBT. No field segments means unavailable competitor progression, not zero laps. The screen offers comparisons only for cars with usable progression. Exact final order requires a recorded results snapshot; absent that, positions remain recorded positions.

Ordering uses captured_unix_ms with segment hash/line index as deterministic tie breakers, not tick alone. Duplicate complete samples require equal session identity, captured time, tick and channel contents. Conflicting samples at the same captured time are discarded with a discontinuity. SDK SessionTime moving backward or an observation gap breaks timing/pit continuity; no line or lap crosses that break. Tick reset never merges sessions or erases distinct samples.

Validation must cover no viewer/network, restart, disk/queue failure, on-track upload pause, identity separation, duplicate/late arrival, cache invalidation, gaps, and a collector-fixture-to-upload-to-Python-to-view comparison. Real rig impact remains subject to G4.
