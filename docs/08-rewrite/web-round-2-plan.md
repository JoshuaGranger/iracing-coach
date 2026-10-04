# Web round 2: the intuitive app

Source: Joshua's comments of 2026-10-04 (`backlog.md` #17–#30) and the Claude-vs-Codex debate that resolved them (`.debate/how-should-the-iracing-coach-web-app-be--20261004T143238Z/report.html`; 92 % agreement, 13 of 14 items settled). This file is the specification for the round; where it is silent, `design/README.md` and AGENTS.md rule.

## Order (the gate)

1. Correctness and the things that mislead: the bug-fix wave already in flight (phantom setups from garage snapshots, split offline stints, the false Caution cue, Laps-to-pit, collector CPU); the Lab library; "Not used in pace fit" wording and the fuel-cell misuse; one comparison selector; Sections state resets; the sticky-row jump; the selected-tab hover.
2. Dense Sessions and ten-at-a-time Setups, the chip rule, one tuning entry, parameter-only Versions.
3. The comparison picture, the drawn fit, caution lap dots, run identity colour, chronological Traces colour.
4. The direct Live editor, checked on the rig screen and the tablet.
5. The field stream and Crew Chief, after the G4 gate on the rig.
6. The Driver page.

Tranches 1–4 are this round's wave A; 5 and 6 are wave B, after wave A is released and its acceptance passes. Every release record carries screenshots at the three viewports (desktop 1440, tablet 1024, phone 390) of the exact thing each acceptance line names.

## Principles the round adds

- **Continuity.** Back restores filters and scroll; a selection survives a view change; the same action has one entry.
- **Honest absence.** A value the backend did not report draws nothing; one accurate line in the detail surface says why, once. Never "None", never a figure from a guess.
- **Colour budget.** Colour is data: run identity on run-level marks, race progress on lap-level marks, gain/loss on position change, amber on a caution count above zero. No saturated panel fills. At most three coloured figures per row.
- **Chips.** A chip marks an exception, a constraint that changes a decision (Fixed/Open), or an action (Retry). Status-normal chips (Values known, Recording, the file name, Analyzed) are removed. Queued and Failed appear only while they explain missing results. A chip never looks like a filter.
- **Selection has one state.** The selected item of any control ignores hover; hover changes only unselected items.
- **Filters by width.** Filters sit at the heading's right when the title and the complete controls fit at their minimum widths; otherwise one `Filters · N` button opening a popover (desktop) or a sheet (touch). Same rule on every page; selections survive navigation; Clear lives inside the surface.

## Sessions (#17, #19, #18)

- One compact row per event: track, config, date, car, Fixed/Open; phases as equal inline cells on wide screens, stacked phase rows on phone; nothing behind a disclosure.
- Each phase cell has seven labelled slots: position or finish; green · caution laps; best; green pace with its supported trend; stops; fuel with unit; longest green run. A missing value shows `—`.
- Type from the tokens: 0.875 rem primary, 0.75 rem secondary; row about 5.5 rem on desktop, growing for wrapping and zoom; silhouette about 1.5 rem.
- Colour: the gain/loss colours on a supported position change; amber on a caution count above zero; nothing else. Finish is a plain number. The fastest lap is not coloured across phases. Phase header keeps its one categorical hue, unsaturated.
- Filters in the heading per the width rule, with a `Library: Joshua | Lab` control inside the filter surface, default Joshua, not saved across visits.
- Acceptance: compare stint length, pace and fuel between two events without opening either; the large colourful tile treatment is gone; the filters sit in the heading at 1440 and collapse to a button at 390.

## Lab library (#18, resolves #14)

- A recording belongs to a library: `main` (default) or `lab`. The driver stays who drove it. Raw files are never moved or edited.
- The utility marks a lab window before it drives (`POST /api/v1/admin/library/windows` with from, until, car, track, note); recordings whose session start falls in a window and whose car and track match are filed `lab` at ingest.
- Historical: an admin repair classifies existing recordings from the lab-note windows (`iRacing-utility/lab-notes/*.md`, the bot's run logs) with the same corroboration; an overlap with Joshua's own driving without a matching car and track stays `main`. Dry run first, idempotent, logged.
- Every session, setup-usage, statistics and Driver-page query defaults to `main`; `library=lab` is explicit. Lab never enters Joshua's counts.
- Acceptance: the bot's 2026-10-04 IRP sessions (658/659 and the 08:39 stub), the 2026-10-03 harvest visits and the 2026-09-25 bot sessions are absent from Joshua's list and present under Lab; a race Joshua drove against AI stays in his list.

## Setups (#20–#24)

- Car sections collapse (state remembered per device); each shows 10 rows, `Show 10 more` appends 10, the page scrolls (no inner scroll region); the revealed count survives collapse; the car header is sticky within its section.
- Row: name, `⚑ Raced N×` (only when supported; an unknown count is not 0), last date. Seen and Values known go. `No values` appears only when a file has no decoded values and it explains a limitation.
- Toolbar: Car, Name, All · Raced · Not raced. `Find a baseline` stays as the car-scoped entry to the baseline workflow; `Progressive tuning` is removed from the heading; `Tune this setup` on the detail is the one entry.
- Detail header: the name once, `⚑ Raced N×`, first seen; no Recording chip, no file-name chip; the file name is secondary metadata lower down where it identifies the artifact.
- Versions: a collapsed section listing versions by date with the changed-parameter count; expanding one shows changed key, old value, new value, unit; `Recorded runs` links to the runs driven on that version. No pace or tire-life effect is claimed; tire life is not shown.
- Acceptance: find a raced setup for a car with more than ten setups and reveal row eleven at 1440 and 390 with one scrollbar; the detail shows the name once; expanding a version shows its changed keys.

## Investigation (#28, #29, #30)

- Screened laps: the word EXCL is gone everywhere. A hollow dot is `Not used in pace fit`; hover, focus or tap shows the backend reason and names the fit. In Traces the status lives on the lap chip, never in the fuel cell, which shows the figure or `—`.
- One comparison selector: the toolbar Compare dropdown is removed; the Comparisons list selects, rows named by their groups (`Run 2 → Run 3`, `Run 3: L61–65 → L85–89`). Selecting updates the brackets on the Race chart, the rail marks, the comparison panel and the comparison context in Traces, and preserves cursor, scroll and view.
- Brackets: two labelled brackets under the lap groups (`Earlier L61–65`, `Later L85–89`) in the comparison's colour, split around omitted laps; the double-ring dots and callouts go.
- The comparison panel is drawn: Earlier and Later as two aligned rows of lap dots on one lap-time scale, backend median marks, the signed backend difference large with its direction, green/total counts; beside them compact paired bars for the backend's starting fuel, average throttle and close-traffic exposure, each with its own labelled scale, units and zero baseline; fuel and temperature ranges stay ranges; no sentence of explanation. With no pace result: one line, nothing else.
- Caution laps: actual lap times as subdued dots inside the caution band; the dash row goes.
- Runs: an ordered run palette (tokens `run-1` … `run-n`) on run-level marks (trend lines, run cards, the sticky run row in the rail) with line style as the secondary cue; the observed trend and the screened fit drawn separately; the fit confined to its supported lap span and labelled with the backend slope (`+0.041 s/lap · 18 laps`) and eligible count; the paragraph is replaced by run cards (sparkline, slope, laps used and screened, fuel band, tire age).
- Traces: lap rows and traces coloured by race progress, a start-to-finish gradient computed on the full phase and never recoloured by a filter; endpoints shown in the rail.
- Sections: selecting a section is a state change (no remount, no route change); cursor, scroll and comparison survive; a diverging gain/loss colour for supported time against the selected comparison, neutral when unsupported; names stay detected-section identifiers.
- Pit markers: compact; repair and penalty fields draw nothing until extracted; the stop's detail surface carries one line, `Repairs and penalties: not extracted`.
- Sticky run row: `top` equals the toolbar height from the tokens, no top margin inside the scroll parent; pixel 0 of scrolling moves nothing, at all three viewports.
- Tab strip and every segmented control: the selected segment ignores hover.
- Acceptance: identify why a lap is absent from the fit from its hover; select one comparison and see its exact groups change on the chart and the rail; identify the faster group and the unequal exposure from the picture with the cursor and inputs still visible; click a section without losing the cursor; scroll without a jump.

## Live editor (#25)

- Edit mode shows drag handles; tiles snap to the grid, swap when dropped on an equal-sized occupied slot, show a valid-placement preview during a resize; a palette supports drag-in and tap-to-add; selecting a tile opens one style strip with the representations valid for that metric (Text; Trend; Bar for bounded metrics; Gauge for fuel where capacity is known; Position is Text only; fuel laps defaults to Text), a colour choice that cannot erase caution, warning or evidence semantics, and size.
- Changes preview immediately, completed gestures autosave, Undo is one tap; touch has select-then-place beside drag; nothing depends on hover or long-press discovery.
- Rows/Columns steppers and `Save as new layout` leave the normal flow; layouts keep their names and the Default (on rig) choice.
- Acceptance on the rig screen and the tablet: move, resize, change a representation and a colour, Undo, reload and find it persisted.

## Wave B (after wave A's acceptance)

- Field stream and Crew Chief (#26): a per-car stream at about 5 Hz, on demand, beside the 60 Hz player frame (contract change in `live-frame.md`); Driver / Crew Chief switch remembered per device, new devices default to Driver; Crew Chief shows the track map with every car, a position-ordered field board (gap, last lap, pit state, last-stop lap, laps since it, observed stall duration, known compound) and the player's telemetry strip; stale state is explicit; no fuel figure for any car but the player's. Released only after the G4 gate on the rig.
- Driver page (#27): the top-bar name opens it; tabs All plus one per racing style present in the library, Series as a filter; from recordings only: starts, finishes and wins with coverage denominators, backend green pace versus the identified field cohort, green-lap consistency, incidents, trends within comparable car/track contexts, `Recorded rating snapshots` with dates; no single score, no career totals the recordings cannot support.
