# iRacing Coach 0.17.4 development: real-data QA fixes

Date: September 21, 2026. This follows the [0.17.3 simulator-PC test](implementation-snapshot-0.17.3-development.md). The installed production app remains 0.17.0; development copies run in disposable profiles.

## Setup library and copying

The previous UI's Save a copy button called an absent backend operation. The new bounded MCP operation accepts only a local `.sto` under the configured iRacing setups directory and writes a verified copy under the Coach archive at `data/setup-copies/<sha256>/setup.sto`. A manifest retains the original filename, source path, size and digest. Matching content is deduplicated. A changed existing copy is rejected, including a destination created during commit; the original is never modified. The copy is independent of the source. The action is classified as a write and remains unavailable to autonomous Coach workflows.

The app sends its configured archive root, validates the copy response, and exposes failures in the library. Setup filtering and selection are computed once per render instead of filtering and allocating the full library for every row. The pagination fix from 0.17.3 remains in place.

## Analysis and archive behavior

Optional trace channels now normalize at one boundary. Bin reducers reuse those finite values, and absent values skip exception-based conversion. Native sample rate, reducer math, evidence screening and output fields are unchanged.

Two complete real-recording comparisons produced byte-equivalent JSON with a fixed clock and provenance. The measurements below are analysis-only calls on this development PC, after loading native-rate telemetry; they exclude discovery, decode, archive writes and UI updates.

| Recording | Before | After | Reduction |
|---|---:|---:|---:|
| Iowa, August 3 | 22.990 s | 21.027 s | 8.5% |
| New Hampshire, August 1 | 33.684 s | 31.762 s | 5.7% |

These are observations, not a guaranteed latency target. Cold analysis still exceeds the requested few seconds. Analysis schema 2 and profile v17 remain valid; only the reviewed source digest changed, preserving valid caches.

A synthetic 270-character archive destination reproduced Windows error 3. New raw copies use `telemetry.ibt` inside their existing full-hash directory instead of repeating the simulator filename. Provenance retains the original name and existing manifest filenames remain unchanged. This fixes the observed filename expansion; it is not a claim of arbitrary-length Windows path support.

## Planning and race-card readability

Known observed-value notation now displays labeled entry, minimum and exit speeds in mph, peak brake percentage, and brake-onset/release/turn-in lap positions. Missing, censored and single-observation values remain explicitly unavailable or limited. Unknown text and target claims pass through unchanged; the UI does not infer telemetry targets or recalculate backend evidence.

## Verification

- 386 application tests passed, none failed or skipped.
- 1,028 backend tests passed; repository and generated contract checks passed.
- Regressions cover missing/nonfinite trace values, conversion/reducer overflow, long raw filenames, legacy archive filenames, bounded setup-copy dispatch, source and destination protection, concurrent-copy conflict, UI failure/retry, and readable evidence without invented values.
- A .NET self-contained Release win-x64 development publish completed without warnings or errors.
- One intermediate application run hit a test cleanup file lock in PowerShell startup profiling; the final full run passed. An intermediate backend run caught missing write-authority classification for the new copy operation; it was added and the full suite passed.

## Development artifact

Source commit: `82f35a883c5f2f33e29d2909e0bd41cdf489f81e`.

The private development transport contains 668 manifested files, is 87,252,637 bytes, and has SHA-256 `75d3f162b64c1dff0c658533465cc18652d51fc7ea4cde7134b35e167c710e30`. It includes the .NET runtime and tracked backend source. Python and Coach Engine use existing installed runtimes through junctions in the disposable application directory. It is not an installer or a complete portable release.

## Simulator-PC follow-up

The private transport and all 668 manifest files were verified on DESKTOP-SIM before an isolated native run. Completed checks:

- Save a copy succeeded for a real Bristol qualifying setup. Original, disposable source, and destination hashes matched. A repeated native click deduplicated the existing copy and retained the original filename in the manifest.
- The native catalog showed 1,079 local groups plus two recorded entries. Bristol search returned 20 local plus two recorded entries.
- Blue theme Save/restart persisted correctly; Mint was restored and saved. Production settings were untouched.
- A fresh archive in the longer user-TEMP profile succeeded. The former destination would have been 261 characters; the new path was 214. Content and original-filename provenance matched.
- Native-rate Bristol analysis passed strict JSON. A controlled comparison against 0.17.3 measured 18.671/1.709 seconds cold/cached before and 18.662/1.802 seconds after. This single-run comparison is effectively flat. The 160-lap plan matched exactly; 26 top-level analysis sections matched, including traces, strategy and technical insights. Other differences were run identity/provenance and tire-learning observation IDs, with unchanged numeric/evidence values.

The matching later Bristol replay archive contains 946 authentic frames in two hash-verified chunks. Its final workflow response and saved final report both include partial replay: 324 display frames and three session-time gaps. A warm workflow took 0.914 seconds. The raw compute cache precedes replay enrichment, so its unavailable replay field is expected, not an integration defect. Native reload and playback controls were not verified.

Native testing found another real setup-library bug: identical-content Chevy, Ford and Toyota setup files shared a hash-based row ID, causing multiple selected borders and the wrong details after a click. The [0.17.5 follow-up](implementation-snapshot-0.17.5-development.md) fixes this.

Windows input failed again after the copy and selection checks. Refreshing the window then returned `GetCursorPos: Access is denied (0x80070005)`; native input stopped after the bounded recovery attempt. The app remained responsive. Corner-text wrapping, replay controls, saved trace-layout editing, remaining filters, and active simulator telemetry remain unverified. No complete every-control acceptance is claimed. Compact reports are retained under `companion-app/artifacts/remote-qa-20260921`; private screenshots and source recordings remain on DESKTOP-SIM.
