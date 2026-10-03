# Codex plan review

Model: gpt-6.1-sol (Codex Sol 6.1), reasoning effort: high (codex-cli 0.159.0-alpha.12.1, codex exec, non-interactive)
Date: 2026-10-02
Sandbox note: the read-only sandbox failed on this Windows host (helper_unknown_error: apply deny-read ACLs; a first read-only attempt could read nothing and returned no review). The review below ran with -s workspace-write plus an explicit instruction not to modify files; git status before and after was identical and no file under docs/08-rewrite changed during the run.
Courier note: the text below is Codex output verbatim.

---

No files changed. Coverage limitation: I read the full P1, P3 and P4 reports; P2 and P5 were checked against your supplied measurements and `prove/verdicts.md`. Directory searches failed with a sandbox ACL error, preventing discovery of their individual filenames.

Paths below are relative to `docs/08-rewrite/` unless stated otherwise.

1. **Blocker — Upload recovery is not a complete transaction protocol.**  
   **File/section:** `contracts/collector-protocol.md` §4; `contracts/db-schema.sql`, `uploads`.  
   **Failure:** A crash after appending but before fsync leaves a `.part` length greater than the acknowledged durable offset. A crash after the final rename but before the database transaction leaves an unindexed ready file. Recovery equates length with durability and specifies neither reconciliation nor completion of a fully received, uncommitted upload.  
   **Smallest fix:** Specify persisted-offset ordering, truncation/revalidation on restart, per-hash serialization, and recovery transitions for every file/DB combination.

2. **Blocker — Garage capture has no durable delivery acknowledgement.**  
   **File/section:** `contracts/collector-protocol.md` §§3, 8; `api.openapi.yaml`, collector messages; `db-schema.sql`, `setup_observations`.  
   **Failure:** A WebSocket send succeeds locally, then the service crashes before committing. Online events need not be journaled, so values disappear. Replayed events can instead create duplicate observations because no event uniqueness constraint or acknowledgement exists.  
   **Smallest fix:** Journal every event before transmission, identify it independently of connection state, acknowledge after the DB commit, and enforce event uniqueness.

3. **Blocker — The boundaries required for parallel implementation remain partly code-defined.**  
   **File/section:** `workstreams.md`, Step 0; `contracts/api.openapi.yaml`, view and tuning schemas.  
   **Failure:** W0 must invent the shapes of `Sample`, `LiveState`, parser results and `ProjectionSummary`. W5/W7/W8 must independently interpret opaque objects such as tuning identity/map and backend view subtrees. Yet workstreams are forbidden to read one another’s code. Generated types cannot reconcile different interpretations of unspecified objects.  
   **Smallest fix:** Contract the complete public signatures and shared types, the investigation component interface, and consumed backend fields before wave 1. Include unknown-field round-trip requirements.

4. **Major — The light-lane memory allowance contradicts the existing tuning implementation.**  
   **File/section:** `architecture.md` §§4, 7; `contracts/worker-interface.md` §6.  
   **Failure:** The allowance assumes one report load, approximately 0.9 GiB. `tuning_workflow.py:1114` and `:1116` simultaneously load driving and target analyses. Two large reports can require roughly 1.6 GiB before evidence construction. Together with a 2.27–3 GiB analysis worker, the server and charged file cache, this can exceed 4 GiB. Projection separately permits 600 MB.  
   **Smallest fix:** Admit memory-intensive stages through one shared budget; initially serialize large tuning, analysis and projection. Measure whole-cgroup peak during overlapping uploads and browser requests.

5. **Major — The admission formula is extrapolated from the wrong workload for reconnected sessions.**  
   **File/section:** `architecture.md` §7; `contracts/worker-interface.md` §6.  
   **Failure:** P1 measured one file. `_load_analysis_telemetry` retains decoded tables while constructing merged columns, sample-reference tuples and provenance for multiple files. Equal total bytes do not guarantee the single-file peak ratio. A group admitted below 3 GiB can exceed it.  
   **Smallest fix:** Measure representative multi-file and overlapping groups; use a conservative group-specific estimate until those measurements exist.

6. **Major — The on-track prohibition does not cover all collector work.**  
   **File/section:** `architecture.md` §2.1; `contracts/collector-protocol.md` §§4, 8–9.  
   **Failure:** Startup scanning, a whole-file hash or setup lookup can continue after the car goes on track. Garage capture is “driven by the reader thread” and includes hashing/searching setup files at exit. Chunk cancellation alone does not prevent this disk and CPU load, or reader stalls. Unknown/stale `IsOnTrack` handling is also unspecified.  
   **Smallest fix:** Put scanning and hashing on cancellable background work, gate each bounded operation on fresh affirmative off-track evidence, and keep filesystem work off the SDK thread. Define how the 100 ms cancellation allowance relates to G4’s literal zero-upload-bytes requirement.

7. **Major — Spool capacity is checked without reserving space or protecting the database.**  
   **File/section:** `contracts/collector-protocol.md` §4; `architecture.md` §§5, 7.  
   **Failure:** Multiple uploads can pass the “currently under 20 GB” check and collectively exceed the cap. Restaging and derived files consume the same filesystem. ENOSPC during fsync, SQLite WAL growth or snapshot creation has no specified response or recovery behavior.  
   **Smallest fix:** Reserve remaining upload bytes, account for restaging, retain filesystem headroom for DB/snapshots, and specify retryable disk-full behavior without acknowledging uncommitted data.

8. **Major — Content deduplication discards occurrence metadata and setup ownership.**  
   **File/section:** `api.openapi.yaml`, `UploadCreate`; `db-schema.sql`, `blobs`, `setup_files`; `nas-layout.md` §2.  
   **Failure:** The same `.sto` bytes under another name, path or driver receive “already verified,” after which the collector drops the entry. There is no independent occurrence-registration operation. Setup uploads contain no driver identity; attributing delayed uploads through the collector’s current driver can assign them incorrectly.  
   **Smallest fix:** Separate immutable blob identity from occurrences carrying captured driver, path, name and source version. Register occurrences even when the bytes already exist.

9. **Major — Offline session identity changes with temporary job paths.**  
   **File/section:** `contracts/worker-interface.md` §3; `db-schema.sql`, `sessions.group_key`.  
   **Failure:** The existing Python fallback uses the source path when recording time comes from mtime (`ibt_reader.py:1828`). Discovery and analysis use different job directories, so rediscovery can produce another group key or fail to resolve the stored selector. Clamping source mtime does not solve this.  
   **Smallest fix:** Preserve the worker’s complete grouping semantics using stable source identities/materialized paths, and test recordings without usable server IDs or timestamps.

10. **Major — Analysis idempotency omits recording membership.**  
    **File/section:** `db-schema.sql`, `jobs.idempotency_key`; `architecture.md` §3, recording flow.  
    **Failure:** After recording A is analyzed, reconnect recording B arrives. The example key `analyze:<session_id>:<profile>` collides with the completed job. If A is still running, its result can become current despite newer membership.  
    **Smallest fix:** Key jobs by an immutable ordered membership digest plus analyzer/profile identity; publish as current only if that generation still matches the session.

11. **Major — NAS isolation fails during startup and shutdown.**  
    **File/section:** `architecture.md` §§2.2, 6, 10; `risks.md`, R3/Q1.  
    **Failure:** A NAS outage prevents the entire container from starting, including review of NVMe-resident data. While running, a dedicated thread blocked on a hard NFS mount can also obstruct orderly process shutdown and release recovery. Declaring it `stalled` does not cancel the kernel operation.  
    **Smallest fix:** Resolve the mount dependency before acceptance and isolate blocking NAS work from the API process’s lifecycle. Do not promise NAS-independent availability with the present Compose volume.

12. **Major — Snapshots lack a coherent generation boundary.**  
    **File/section:** `architecture.md` §§2.2, 5; `nas-layout.md` §5; `db-schema.sql`, `snapshots`.  
    **Failure:** Separate DB backups and directory copies can capture a tuning experiment’s file and index at different revisions. The archiver can copy `latest` while the scheduler replaces it. `PRAGMA data_version` is connection-relative and does not detect that connection’s own commits; it also misses Python-file-only changes. An incomplete daily folder is subsequently left alone.  
    **Smallest fix:** Track an explicit user-data generation, quiesce archive mutations while capturing it, publish immutable snapshot generations, and recognize completion by a verified manifest rather than folder existence.

13. **Major — Worker cleanup can delete another active worker’s temporary files.**  
    **File/section:** `contracts/worker-interface.md` §6, Orphans; `nas-layout.md` §7.  
    **Failure:** Analysis and light workers share a driver archive. Killing one triggers deletion of `.write-*` and `.copy-*` throughout that archive while the other may be persisting a tuning draft or feedback. SQLite recovery cannot restore the deleted pending file.  
    **Smallest fix:** Record temp-file ownership per job or clean only while all writers to that driver archive are stopped.

14. **Major — The browser outbox cannot safely replay resource creation.**  
    **File/section:** `architecture.md` §2.5; `api.openapi.yaml`, note/layout creation and asynchronous mutation endpoints.  
    **Failure:** A note is committed but its response is lost. The pending outbox retries POST and creates another note. `If-Match` protects updates, not initial creation, and no request-id contract exists.  
    **Smallest fix:** Give every queued mutation a persisted client operation ID and replay its original result on retries.

15. **Major — The JSON writer parity gate does not compare writer bytes.**  
    **File/section:** `contracts/worker-interface.md` §8; `architecture.md` §12; existing `tools/analysis_parity.py`, `run_once`.  
    **Failure:** The harness parses persisted JSON and reserializes it before comparison. A Rust writer changing exponent spelling, whitespace, escaping or formatting can pass despite violating byte parity. Its broad timestamp/elapsed normalization can also conceal changes outside performance metadata.  
    **Smallest fix:** Compare original artifact bytes after narrowly allowlisted substitutions; retain semantic comparisons as a separate diagnostic.

16. **Major — Production identity and production-shaped inputs are outside the demonstrated parity gate.**  
    **File/section:** `architecture.md` §11; `contracts/worker-interface.md` §8; existing `tools/analysis_parity.py`.  
    **Failure:** The harness pins the analyzer digest, analyzes one explicit file and requires identical archive paths. This does not prove unpinned production IDs, grouped selectors, job-root provenance, reconnect overlap handling or Windows-versus-Linux output with different absolute roots.  
    **Smallest fix:** Add unpinned identity checks and real worker-interface captures for multi-file groups, restaging and fallback identities. Define narrowly scoped path normalization explicitly.

17. **Major — The native column boundary does not specify the representation the remaining Python requires.**  
    **File/section:** `contracts/worker-interface.md` §7; `architecture.md` §12.  
    **Failure:** “Buffer protocol” does not define nullable columns, scalar types, array-valued SDK variables, ownership or provenance. Existing `TelemetryTable` copies sequences into lists; normalization and unit conversion create more lists; multi-file merging builds Python lists too. Decode alone therefore does not automatically eliminate the allocation attributed jointly to decode and column materialization.  
    **Smallest fix:** Define and prototype the column interface against unchanged downstream stages, inventory necessary Python changes, and measure phase 1 independently before claiming its memory benefit.

18. **Major — D5’s streaming latch is undefined across reconnects and resets.**  
    **File/section:** `contracts/collector-protocol.md` §§3, 6; `live-frame.md`, epoch rules.  
    **Failure:** A viewer leaves, then the service restarts before the session ends. `welcome.stream_wanted` can be false from viewer count alone, stopping streaming early. Conversely, an uncleared latch can stream indefinitely. The inherited engine epoch also changes on lap/time resets, which are not necessarily session termination.  
    **Smallest fix:** Contract separate viewer demand, session-latched streaming and engine-reset identity, with explicit reconnect, restart, driver-change and real session-end transitions.

19. **Major — The live wire documents disagree at implementation-critical points.**  
    **File/section:** `contracts/live-frame.md`, Rules/Browser socket; `collector-protocol.md` §6; `api.openapi.yaml`, `LiveHello`.  
    **Failure:** The frame document specifies a hello containing `presence`; OpenAPI requires `type`, `live` and `rate`. One document streams every SDK tick; another caps at 60 Hz. The server supposedly reads only bytes 0–12, yet must reject `table_version` at offset 34.  
    **Smallest fix:** Establish one normative hello and rate rule; require full fixed-header validation and shared codec/control golden vectors.

20. **Major — CCOL lacks a canonical signal mapping and numerical encoding policy.**  
    **File/section:** `api.openapi.yaml`, `/traces`, `TraceSignal`, `TraceBlobHeader`; `workstreams.md`, W5/W8.  
    **Failure:** Projector and browser can disagree about signal IDs, source fields, units, ordering, integer scaling or missing sentinels. Only f32/i16/u8 are available, although backend values are Python doubles; conversion silently changes values without a projection-equivalence gate.  
    **Smallest fix:** Define the channel mapping and encoding rules, retain f64 where necessary, and test decoded values and missingness against authoritative backend traces.

21. **Major — A modified garage setup can be falsely attributed to saved `.sto` contents.**  
    **File/section:** `collector-protocol.md` §8; `db-schema.sql`, setup observations; `api.openapi.yaml`, `linked_setup_id`.  
    **Failure:** P5’s unsaved edit yields modified values, the last-loaded name and the hash of the unchanged file. Unconditionally linking that observation makes those edited values appear to describe the saved file.  
    **Smallest fix:** Store the loaded-file association separately from verified file-value equivalence. A modified observation must not establish the `.sto`’s values.

22. **Major — Multi-phase recording support is asserted beyond what discovery supplies.**  
    **File/section:** `db-schema.sql`, session comments; `contracts/worker-interface.md` §§3–4.  
    **Failure:** The plan allows one recording to belong to several phases, but existing discovery chooses the final usable `SessionNum` (`ibt_reader.py:666`) and returns one group per file. Earlier phases in a continuous recording will not acquire the promised rows merely by invoking discovery.  
    **Smallest fix:** State the actual v1 behavior, or add an authoritative Python phase-partition result with regression tests before implementing multi-phase session membership.

23. **Major — D12 preserves objects without contracting how their references survive migration.**  
    **File/section:** `workstreams.md`, D12 migration; `api.openapi.yaml`, `ImportBundle`.  
    **Failure:** Imported notes use old `subject_id` values; setup/session IDs are allocated anew, and analyzer changes can rotate analysis IDs. Counts can reconcile while notes, drafts and experiment references point nowhere or to the wrong object.  
    **Smallest fix:** Include stable source identities and an explicit old-to-new reference mapping; verify attachments and experiment references, not just counts.

24. **Major — Executable wave instructions conflict with the sim-PC access pause.**  
    **File/section:** `workstreams.md`, W3/W9, Waves 1–4 and migration; owner decisions, Still binding.  
    **Failure:** Workers following the wave checklist can inventory, copy, install or run rig gates under D14’s autonomy even though the later owner instruction forbids touching DESKTOP-SIM until explicitly lifted.  
    **Smallest fix:** Mark every sim-dependent step blocked by that instruction and make the written lifting of the pause a prerequisite.

25. **Minor — Path ownership conflicts with assigned deliverables.**  
    **File/section:** `workstreams.md`, ownership table, W9 and Wave 4.  
    **Failure:** W9 must create root `.dockerignore` and delete `companion-app/` and its CI, but owns neither root files, that directory nor scaffold-owned CI. W6’s ownership of existing tool scripts also overlaps the scaffold restriction. Implementers must violate ownership or stop for an unplanned handoff.  
    **Smallest fix:** Assign these paths and retirement changes explicitly, including the scaffold-owner handoff.

## VERDICTS

(1) **Undecidable with these numbers:** Python meets wall-time, RSS and HA limits; the Frigate failure does not establish causation, and swap activity was unmeasured. No staged-Rust versus optimized-Python comparison exists. D2 remains an owner directive, not a measured superiority result.  
(2) **Justified:** The measured Razor/WASM reuse route takes 74–78 seconds to become interactive and transfers about 3.8 MB despite substantial reusable code. This supports replacing that route; it does not prove the SPA meets G2, especially the shared cursor’s 55 fps target.  
(3) **Undecidable with these numbers:** P3 rejects WPF’s footprint, not stripped C#. Non-WPF C# measured 31–35 MB only in emulation; Rust’s rig prototype omitted the engine, and neither route literally passed all thresholds. Equivalent production collectors were not compared while driving.
---

## Disposition (architect, Opus 5.5, 2026-10-02)

Each finding was checked against the documents and, where it rested on a fact, the code or proof reports. Paths are relative to `docs/08-rewrite/`.

| # | Finding | Decision | Reason | Files changed |
|---|---|---|---|---|
| 1 | Upload recovery incomplete | Accept | A crash between `ready/` rename and the blob insert left an unindexed file, and offset had two sources (`.part` length, `durable_offset`); a torn tail was already caught by the whole-file hash | `contracts/collector-protocol.md` s4 (fsync-then-record order, per-hash lock, one-transaction commit, reconciliation table), `workstreams.md` W4 |
| 2 | Garage events lack durable ack | Accept | Confirmed: online events were not journaled, no ack message existed, no uniqueness on `setup_observations` | `contracts/collector-protocol.md` s8, `contracts/api.openapi.yaml` (`GarageAck`, `GarageCommit.update_count`), `contracts/db-schema.sql` (partial unique indexes), `architecture.md` 2.1 |
| 3 | Shared types code-defined | Accept | W0 stubs named `Sample`, `LiveState`, `ProjectionSummary` with no fields; tuning identity/map were opaque | `workstreams.md` new step 0a (`contracts/crate-api.md`, explicit tuning schemas, `x-consumed-fields`) before wave 1; `contracts/README.md` |
| 4 | Light-lane memory contradicts tuning | Accept | Confirmed `tuning_workflow.py:1114-1116` loads two analyses; 2.27 + 1.6 + 0.3 GiB can exceed 4 GiB | `contracts/worker-interface.md` s6 (shared budget), `architecture.md` s4, s7, s13, `workstreams.md` G1 |
| 5 | Admission extrapolated to multi-file groups | Accept | Confirmed `workflow.py:1376-1500` keeps decoded tables while building merged lists and provenance; 1.5x until G1 measures | `contracts/worker-interface.md` s6, `architecture.md` s7, `workstreams.md` G1, `risks.md` R20 |
| 6 | On-track rule misses hashing, scans, garage file work | Accept | Confirmed: gate covered upload chunks only; garage hashing ran on the reader thread; stale `IsOnTrack` undefined | `contracts/collector-protocol.md` s4, s8, s9, `architecture.md` 2.1, s7, `workstreams.md` G4 |
| 7 | Spool cap without reservation | Accept | Check-at-create let concurrent uploads exceed 20 GB; ENOSPC unspecified | `contracts/collector-protocol.md` s4 |
| 8 | Dedup discards occurrences | Accept | Confirmed: an already-held `.sto` under a new name never reached `setup_files`; no driver on setup uploads | `contracts/api.openapi.yaml` (`UploadCreate.customer_id`), `contracts/db-schema.sql` (`blob_occurrences`), `contracts/collector-protocol.md` s4 |
| 9 | Offline group key depends on job path | Accept | Confirmed `ibt_reader.py:1828-1829` uses `path` when `time_source == "mtime"`; such groups have one member | `contracts/worker-interface.md` s3, `contracts/db-schema.sql` sessions comment |
| 10 | Analysis idempotency omits membership | Accept | Example key `analyze:<session_id>:<profile>` under `UNIQUE` blocks the reconnect re-analysis | `contracts/db-schema.sql` (`jobs`, `analyses.membership_sha256`), `architecture.md` s3, `workstreams.md` W4 |
| 11 | NAS isolation at start and shutdown | Accept (partly) | A `hard` mount can hang shutdown; fixed with `soft` (safe under hash-after-flush). Start-up dependency was already owner Q1 and stays there | `contracts/nas-layout.md` s1, s6, `architecture.md` 2.2, s6, s10, `risks.md` R3, R19 |
| 12 | Snapshots lack a generation boundary | Accept | `PRAGMA data_version` misses the same connection's commits and worker files; day folder judged by existence | `contracts/nas-layout.md` s5, s7, `contracts/db-schema.sql` (`server_counters`, `snapshots`), `architecture.md` 2.2 |
| 13 | Orphan cleanup hits another worker | Accept | `.write-*`/`.copy-*` names are random (`storage.py:668,710`), not per process | `contracts/worker-interface.md` s6, `contracts/nas-layout.md` s7 (also corrected the stale `py/<id>/iracing/` path) |
| 14 | Outbox replays create resources twice | Accept | `NoteCreate` and outcome POSTs had no request id | `contracts/api.openapi.yaml` conventions, `contracts/db-schema.sql` (`request_log`), `architecture.md` 2.5, `workstreams.md` W4 |
| 15 | JSON writer gate does not compare bytes | Accept | Confirmed `tools/analysis_parity.py:355` parses and re-serializes persisted JSON | `contracts/worker-interface.md` s8 (raw mode), `architecture.md` s11, `workstreams.md` W6 |
| 16 | Parity misses production identity and shapes | Accept | Harness pins the digest and runs one explicit file | `contracts/worker-interface.md` s8, `workstreams.md` W6 |
| 17 | Column boundary unspecified | Accept | Wave 5 work, but the memory claim was stated as fact; now an entry deliverable and measured | `contracts/worker-interface.md` s7, `architecture.md` s12 |
| 18 | Streaming latch undefined across reconnects | Accept | Confirmed epoch also bumps on lap decrease and time going backwards (`spikes/live-stack.md` s1); welcome could end the latch | `contracts/collector-protocol.md` s6, `architecture.md` s3 |
| 19 | Live wire documents disagree | Accept | Confirmed: hello fields, every-tick vs 60 Hz cap, bytes 0-12 vs `table_version` at 34 | `contracts/live-frame.md`, `contracts/collector-protocol.md` s6, `contracts/README.md` |
| 20 | CCOL lacks mapping and numeric policy | Accept | No signal-to-backend-key mapping; f32 cannot copy Python doubles | `contracts/api.openapi.yaml` (`source_key`, `f64`, encoding rule), `workstreams.md` W5 |
| 21 | Modified setup attributed to the `.sto` | Accept | Linking rule existed only as a description hint | `contracts/collector-protocol.md` s8, `contracts/api.openapi.yaml`, `contracts/db-schema.sql`, `architecture.md` 2.1 |
| 22 | Multi-phase recordings asserted | Accept | Confirmed discovery uses the final `SessionNum` (`ibt_reader.py:678-679`); v1 behaviour now stated | `contracts/db-schema.sql`, `contracts/worker-interface.md` s3, `risks.md` |
| 23 | D12 references do not survive | Accept | Notes carried old `subject_id`s; import ran before re-ingest | `contracts/api.openapi.yaml` (`ImportBundle.notes`), `workstreams.md` D12 |
| 24 | Waves conflict with sim-PC pause | Accept | Owner's pause was not reflected in the executable plan | `workstreams.md` (pause rule, [sim: blocked] marks), `risks.md` R21, Q4 |
| 25 | Path ownership conflicts | Accept | `.dockerignore`, `companion-app/` and CI were unowned by W9; `tools/check.ps1` matched W6's glob | `workstreams.md` header and W9 row |
| V1 | D2 verdict: undecidable | Recorded, decision unchanged | Owner ruled B; disagreement and numbers in `risks.md` R17, owner question Q2 | `risks.md` |
| V2 | D3 verdict: justified | Agrees | Matches `prove/verdicts.md` (SPA confirmed; G2 must measure 55 fps) | none |
| V3 | D5 verdict: undecidable | Recorded, decision unchanged | Rust stands on footprint; disagreement and numbers in `risks.md` R18, owner question Q3 | `risks.md` |

No finding conflicted with an owner decision, so none was rejected on that ground; none was found factually wrong.
