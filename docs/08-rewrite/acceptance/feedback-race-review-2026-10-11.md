# Race review feedback, 2026-10-11

Racing decisions: compare sustained pace and pit timing against another car, inspect
the tires removed at a stop, and find the setup last used at a track. Comparison
uses recorded lap progression with aligned scales and a shared cursor; it does
not suggest copying another driver's inputs or infer their fuel/tire service.

## Behavior

- Run cards keep run metrics. Their outgoing tire graphic lives in the following
  pit card; hover or keyboard focus opens the four-tire detail with wear percentages.
- Car selection sits between the view tabs and phase tabs. Only cars with actual
  progression can be selected. Comparison places the player above the competitor,
  sharing lap/time/position scales, zoom and cursor without pointer-triggered requests.
- Results omit roster-only rows. Absent data stays unknown, including green lap
  counts. Explanatory paragraphs are replaced with concise labels and tooltips.
- An observed run trend can use green laps with traffic or ongoing repair context.
  Incident candidates, pit transitions, restarts and other exclusions still apply.
  Python owns this trend; the projector copies it. Coaching-reference fits retain
  their existing stricter eligibility rules and their supported endpoints.
- Setups open a recent car with visible setup rows grouped by track. Actual linked
  sessions provide last-used dates and session counts. A mobile car selector replaces
  the desktop sidebar. Baseline search receives the current car/track context.

## Recording and integrity

The affected historical IBTs contain no CarIdx telemetry channels. A roster in SDK
session information is not competitor progression. Those races cannot be backfilled
from the existing recordings.

The collector now records SDK field channels independently of browser viewers, at
most 10 Hz, through a bounded nonblocking queue and separate disk worker. Immutable
segments rotate within 60 seconds. Local storage is capped at 2 GiB without deleting
unacknowledged segments; failures/drop counts appear in diagnostics. Crash recovery
preserves the original partial file. Verified segments use the existing off-track
upload gate and are archived on the NAS.

Association requires player UserID, positive SubSessionID and SessionNum. Segment
hashes join analysis membership and cache identity, so late arrival schedules a new
analysis and an older in-flight result cannot become current. Database migration 4
preserves existing blobs, references and resumable upload offsets. Rebuild-index
restores field metadata from manifests and rejects missing identity/version fields.

Python checks file identity before and after reading, merges exact duplicate samples,
breaks continuity on gaps/conflicts, and omits car slots that changed driver. Later
IBT result snapshots retain their positions over earlier field positions. Fuel and
tire-service measurements remain player-only. At most 256 field segments enter one
analysis; exceeding that bound fails explicitly instead of publishing partial results.

## Verification

- Real collector-generated sanitized segments pass through HTTP upload, archive/index,
  the production Python worker and Rust projector. The rival has 50/60-second laps
  and one pit visit; current membership covers all three segments.
- Regressions cover identity mismatch, partial input, gaps, conflicting samples,
  duplicate segments, changed driver slots, changed files, late uploads, migration
  preservation, NAS restaging, and observed-vs-screened trend eligibility.
- Responsive browser checks cover desktop, tablet and phone tire details, comparison,
  setup selection and search. Web unit tests, typecheck and production build pass.
- The Python/native parity corpus has byte-identical output for both sanitized
  recordings (including the expected truncated rejection) and all four worker cases.

## Rig acceptance still required

Publishing the installer does not install it on DESKTOP-SIM. This chat has no callable
remote-engineering tools, so installation and the G4 sim-impact measurement remain
unverified. Existing companion installations do not gain field capture until updated.
No private race data or raw iRacing source files were changed or committed.
