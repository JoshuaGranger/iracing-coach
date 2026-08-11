# Simulator PC prompt: collect official iRacing track maps

Copy everything below the divider into ChatGPT/Codex on Joshua's simulator PC.

---

You are operating Joshua's Windows simulator PC with his explicit permission to inspect the locally installed iRacing content and use the visible iRacing UI. Your task is to build a resumable evidence set of official track-map screenshots for development of Joshua's local iRacing Coach app.

## Outcome

For every locally installed track configuration that the iRacing UI can display, capture the official **Track Info → Track Map** view at native resolution. Preserve the official turn numbers, turn names, pit entry/exit markings, start/finish marking, configuration name, track identity, and the Track Info facts visible in the dialog.

Write all output beneath:

`\\192.168.1.82\Joshua\iRacing Temp\track-map-capture`

Create one new UTC-stamped run folder. Never overwrite an earlier run. Use a structure like:

```text
track-map-capture/
  20260810T180000Z/
    control/
      run.json
      questions.md
      progress.json
    tracks/
      <track-id-or-safe-name>/
        <configuration-id-or-safe-name>/
          full-dialog.png
          map-panel.png
          metadata.json
    manifest.jsonl
    COMPLETE.json
```

## Safety and scope

- Read and navigate only. Do not purchase content, join a session, launch the simulator, modify setups, delete files, change account settings, or edit iRacing installation data.
- Do not reverse-engineer `.dat` archives, scrape process memory, or claim that an inferred label is official.
- Screenshots and visible UI metadata are authorized. Keep account name, email, customer ID, notification content, and other personal UI outside the cropped map image when practical.
- If the network share is temporarily unavailable, stage under a new local temporary folder and copy only after it returns. Never silently discard a capture.
- Ask Joshua a concise question only when you cannot safely discover how to reach the next track/configuration. Record every question and answer in `control/questions.md` so the run can resume.

## First: learn the UI

1. Open the iRacing desktop UI if it is not already open.
2. Find one installed track, open **Track Info**, and select the **Track Map** tab.
3. Verify that the dialog resembles the supplied example: configuration selector at lower left, official map at right, and visible track facts.
4. Determine a repeatable path for changing venue and configuration. Prefer normal visible UI navigation. Do not rely on fixed screen coordinates when a label or accessible control can be used.
5. Capture one pilot configuration and inspect the images before beginning the batch. Confirm that all turn labels and the entire course are readable and that no menu or cursor obscures the map.

If you cannot reach Track Info after reasonable inspection, ask Joshua to demonstrate the clicks once. Observe that demonstration, write the learned sequence into `control/run.json`, and continue autonomously.

## Build the work list

Use the read-only installed-content information available on the PC and the iRacing UI to enumerate installed venues and configurations. The UI is authoritative for the display name. Record, when visible or safely available:

- iRacing track number or stable local track/configuration key
- venue name
- exact configuration name
- installed/not installed
- road/oval/dirt category
- configuration length
- night-lighting and AI availability

Include only configurations whose official map can actually be opened. Record skipped entries with a human reason; do not fabricate a screenshot.

## Capture each configuration

For each work-list item:

1. Open its Track Info dialog and Track Map tab.
2. Select the exact configuration and wait until the title, facts, and map have visibly settled.
3. Move the pointer outside the map and labels.
4. Capture `full-dialog.png`, containing the configuration title, facts, configuration selector, and full official map.
5. Capture `map-panel.png`, tightly containing the official map, turn numbers/names, pit arrows/lines, and start/finish. Do not resize, stretch, enhance, redraw, or AI-fill the source image.
6. Write `metadata.json` with:
   - captured UTC time
   - venue and exact configuration display names
   - any stable track/configuration key and track number actually observed
   - visible Track Info facts
   - ordered list of every turn number/name that is visibly readable
   - whether pit entry, pit exit, direction arrows, and start/finish are visible
   - source window title and app version if visible
   - full image pixel dimensions and the crop rectangle used for `map-panel.png`
   - `label_status`: `official-visible`, `no-labels-visible`, or `partially-visible`
   - a concise issue list for clipped, overlapped, blurry, or ambiguous content
   - SHA-256 of both PNGs
7. Append one JSON object to `manifest.jsonl` only after both images and metadata have been flushed successfully.
8. Update `control/progress.json` atomically after every configuration. It must contain totals for discovered, completed, skipped, failed, and remaining items plus the last completed identity.

Use safe filesystem names, but preserve exact official names inside metadata. If two configurations appear visually identical, capture both and record their separate identities; do not assume they share geometry.

## Quality checks

Before accepting any item, verify visually:

- the complete course is inside the image;
- text is not clipped or covered;
- the selected configuration in the UI matches metadata;
- map orientation and aspect ratio were not altered;
- no loading skeleton, animation, tooltip, cursor, or dropdown obscures the source;
- screenshots are nonzero PNGs and their SHA-256 values match the saved files.

Retry a failed capture up to three times. Then record a failure with screenshots/log evidence and move on.

## Resume behavior

If interrupted, load the newest incomplete `control/progress.json` and `manifest.jsonl`, verify existing hashes, and continue with the first unfinished item. Never recapture or overwrite a completed item unless its stored file fails validation; in that case preserve the old item under a `superseded` subfolder.

## Finish

At completion, write `COMPLETE.json` atomically with:

- run ID and UTC start/end;
- counts for discovered/completed/skipped/failed;
- manifest SHA-256;
- list of unresolved questions;
- list of configurations needing a manual recapture;
- a statement that no map label was inferred or invented.

Then give Joshua a short summary: completed count, skipped/failed count, output folder, and any action he needs to take. Do not upload the collection to a cloud service or send it anywhere else.

