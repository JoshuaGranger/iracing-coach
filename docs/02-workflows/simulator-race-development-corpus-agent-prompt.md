# Simulator race development corpus agent prompt

Copy everything below the divider into ChatGPT/Codex on Joshua's simulator PC.

---

You are operating Joshua's Windows simulator PC with explicit permission to collect a **read-only development test corpus** for his local iRacing Coach application. Do not change, move, rename, or delete any source file. Do not expose credentials, authentication state, browser profiles, cookies, product keys, serial numbers, MAC addresses, or unrelated personal data.

## Destination

Create one new timestamped folder under:

`\\192.168.1.82\Joshua\iRacing Temp\development-test-data`

Name it like `YYYYMMDDTHHMMSSZ-last-20-races`. Never overwrite a previous collection. Write each output through a temporary file and atomically rename it when complete so an interrupted upload is obvious and resumable.

## Race selection

Select the latest 20 distinct **Race** sessions available on this PC, ordered by embedded session start time rather than file timestamp. Do not substitute practice, qualifying, time trial, or test sessions. If fewer than 20 races exist, collect every available race and state the exact count.

Ensure Iowa, Daytona, Portland, and New Hampshire are included when available, even if that requires adding them as separately identified diagnostic races. Record the car, exact track configuration, setup type, session/subsession ID, start time, lap count, source paths, and the reason each race was selected.

## Copy the evidence needed for development

For each selected race, preserve the relative relationships among files and copy only relevant artifacts that actually exist:

1. Original `.ibt` telemetry and matching `.rpy` replay, if present.
2. iRacing Coach analysis JSON, race card, workflow/package output, raw-copy metadata, and source SHA records.
3. Track-geometry cache and full provenance/observation records for the exact configuration.
4. Live replay manifests and every referenced chunk, including disconnected/reconnected segments.
5. Telemetry event/profile artifacts, logs, and diagnostics linked to that session.
6. Application settings needed to reproduce layouts, theme, and UI state, but with all tokens, secrets, account identifiers, and protected connection material removed.
7. Existing screenshots or performance evidence linked to that race.

Do not silently ignore a referenced file that is missing. Record the attempted path, race/session linkage, and error in the manifest.

## Required structure

Use these top-level folders:

- `00-manifest`
- `01-system`
- `02-app`
- `03-races`
- `04-track-geometry`
- `05-replay`
- `06-analysis`
- `07-performance`
- `08-screenshots`
- `09-errors`

Give every race a stable folder containing a small `race.json` identity record and its copied artifacts.

## Manifests and integrity

Produce:

- `00-manifest/collection.json` and `collection.md`
- `00-manifest/sessions.json`
- `00-manifest/files.json`, with relative path, byte length, UTC modified time, SHA-256, source category, and linked session
- `00-manifest/errors.json`
- `00-manifest/redactions.json`
- `00-manifest/README.md`

The collection summary must state requested and selected race counts, represented tracks/configurations, largest real lap count, special-track coverage, deliberate exclusions, total files/bytes, and whether the collection completed.

After copying, re-read every destination file and verify its SHA-256. A file is not complete merely because its name exists. Resume safely if interrupted and never duplicate a completed verified file.

## Performance and screenshots

If safe UI automation is available, capture the installed iRacing Coach at its actual simulator resolution for Iowa, Daytona, Portland, and New Hampshire:

- Race Analysis with 1, 3, 20, and all available laps selected
- Iowa/Daytona track geometry and cursor movement
- Technical overview and each detail page
- Race Replay at the first playable instant and during playback
- Live Telemetry full page and popout

Record viewport, display scale, refresh rate, selected lap count, console/runtime errors, and interaction latency measurements. Do not invent measurements. If safe automation is unavailable, record that limitation and continue the file collection.

Do not synthesize a fake 500-lap race unless explicitly asked. Report the largest real race; the development PC will generate a bounded synthetic 500-lap stress fixture separately.

## Completion

When the verified collection is finished, tell Joshua:

- the exact destination path;
- selected race count and track/configuration list;
- total files and bytes;
- largest lap count;
- missing/replay/redaction errors;
- whether the upload is complete and hash-verified.

Do not compress the corpus unless Joshua explicitly asks. Do not upload it anywhere except the stated local network share.

