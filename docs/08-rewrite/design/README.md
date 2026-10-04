# Design directions (backlog #1)

Two visual directions for Joshua's choice. Both show the same mock data and the same colour meanings; they differ in character, density and type. Nothing in `web/src` or `config/theme.dark.json` changed. The fix wave (#1a to #1k) is built in the chosen direction.

| | Direction A, Pit wall | Direction B, Broadcast |
|---|---|---|
| Mockup (static, self-contained) | `direction-a.html` | `direction-b.html` |
| Renders, 1536 x 864 | `direction-a-{sessions,rail,race,setups}.png` | `direction-b-{sessions,rail,race,setups}.png` |

Renders live outside the repository in `C:\Users\joshu\source\coach-migration\screens-design\`. Each HTML file holds four frames: Sessions (one filter open), Traces with the lap rail (pit stop hover on the rail), Race progression (pit marker hover), Setups.

## Shared rules (both directions)

### Colour is racing data

Hue carries a racing meaning and nothing else. Interaction (selected, current, focus, primary action, cursor) is light, not a hue. Colour is never the only carrier: every coloured mark also has a number, letter, word or shape.

| Proposed token | Hex | Means | Where |
|---|---|---|---|
| `race.green` | `#3DDC84` | green flag; positions or time gained; personal-best sector; tire changed | green-lap dot, gain chip ▲, pace improving, PB sector, lit tire |
| `race.caution` | `#FFD43B` | caution (yellow flag) | caution-lap dot, caution band on charts, run composition bar |
| `race.pit` | `#4DA3FF` | pit stop | stops count, pit road on the map, pit marker, pit pictogram frame, stop duration |
| `race.best` | `#F05CDB` | fastest lap; session-best sector; fastest lap of the event | lap time, trace, swatch, sector, the event's best figure on Sessions |
| `race.loss` | `#FF5A6E` | cost: positions or time lost, pace fall-off, incident points | loss chip ▼, pace > +0.020 s/lap, `xN` chips; also a Failed analysis |
| `race.fuel` | `#FF9F43` | fuel | fuel used, fuel gauge of a stop |
| `interaction` | `#EAF2F6` | selected, current, focus, primary action, chart and map cursor | never on data |
| car colours | `#A98BFF` `#2CCFC1` `#D9B27C` | which car (stable per `car_path`, first-seen order) | car stripe, car chips, Setups groups, row edge (A) |
| lap colours | trace palette, fastest = `race.best` | which lap is on the chart | line swatch, trace, map line, readout bar |
| phase | P `#5F748B`, Q `#AFBCC8`, R `#F2F5F7` | session phase, a luminance ramp (race brightest) | B tile bands and P·Q·R segments; A uses letters only |
| flags | white `#F4F6F8`, checkered pattern | last lap; finish; a win | rail flag column, win mark, B result band |

Telemetry channel colours (`telemetry.speed/throttle/brake/steering`) still identify channels in single-lap channel plots (Sections, Live); they never mark state. Pages not mocked (Runs, Sections, Live, setup detail, baseline, tuning) use the same tokens, shapes and states.

### Shapes

| Shape | Means |
|---|---|
| dot ● | lap flag state: counts on Sessions, one per lap on the rail. Hollow ○ = zero |
| flag glyph | white flag (last lap), checkered (finish, win) |
| line swatch ▬ | a lap's trace colour; thick = spotlight; hollow grey = not on the chart |
| stripe ▌ | car identity |
| chip | exception on a lap or session: `Excl`, `Partial`, `xN` |
| badge | session property: Fixed (solid, lock) or Open (dashed, wrench) |
| band | B only: phase (top of tile), result (left edge of the race tile) |
| car from above | a pit stop |

### One state model

| State | Look |
|---|---|
| hover | surface hover colour only |
| selected | 3-4 px lap-colour edge, lap-colour tint, swatch lit (`aria-pressed`) |
| spotlight (one or none) | selected plus a 1 px lap-colour outline and glow, thick swatch; B also puts the lap number on a lap-colour plate |
| focus-visible | 2 px interaction outline, keyboard only (`:focus-visible`); never after a click. That residual box is backlog #1c(a) |
| current tab, current phase | interaction colour (A: outlined tab, filled phase; B: filled tab, phase filled in its band colour) |
| not comparable | time dimmed, status chip |

### Copy

Labels are nouns, values carry units, no sentence explains a number. Reasons sit in a chip's tooltip as words (`caution`, `pit`, `restart`). Examples: incident `L50 · x2`; `Failed · worker crash`; `Analyzing`; `Not analyzed` with an Analyze button. No disclaimer lines under charts.

### Type

Theme scale unchanged: body 16 px, labels and secondary text 14 px minimum, numbers 15 px minimum, controls 36 px minimum (B pills 38-40 px). Every frame was measured against these minimums. A: Segoe UI Variable, Cascadia Mono numerals, uppercase 14 px column heads. B: Segoe UI Variable body, Bahnschrift display (`font-stretch: semi-condensed` numerals, `condensed` uppercase labels); Bahnschrift is Windows-only, so B ships a bundled OFL fallback (Barlow Semi Condensed).

### Components

- **Filters** (Sessions: Car, Track, Series, Session, Setup; Setups: Car). Each is a searchable multi-select dropdown. The trigger shows the selection count; the panel has a search field focused on open (typed text underlined in matches), checkbox options with their identity mark (car stripe, phase letter, Fixed/Open) and session counts, footer `n of m` and Clear. Selections apply at once and show as removable chips (the whole 36 px chip is the control), then Clear. No Tests filter, no type tab strip, no separate search box on Sessions.
- **Fixed/Open** on the event header; a session that differs from its event carries its own badge.
- **Exceptions only.** No Analyzed chip, no Resume banner. Shown: Analyzing, Queued, Deferred · reason, Failed · reason + Retry, Not analyzed + Analyze. A normal row shows nothing in that column.
- **P · Q · R phase selector** in the investigation header, replacing the session dropdown: one segment per phase the event has, in time order, absent phases not shown, the current one lit. A phase that cannot open (not analyzed, failed, deferred) is disabled and names its state on hover (audit S2).
- **Pit stop pictogram**, one component in three places (rail cell, chart-marker hover, details): car from above, nose up; changed tires lit `race.green`, unchanged grey; a vertical fuel gauge filled in proportion to gallons added over the car's fuel capacity (session info `DriverCarFuelMaxLtr`), omitted when nothing was added; lap number (`L37`) and stop duration (`13.8 s`). The hover adds the tire list (`RF RR`, `4 tires`) and `+11.2 gal`. No sentence. On charts the pit marker is a pit-blue tag with the car glyph; on the rail the pictogram replaces the "Pit entry / Pit exit" text badges.
- **Rail flag column**: green dot, caution dot with glow, white flag, checkered flag. A partial lap shows no flag and no Δ (audit T9).
- **Sector blocks**, one per `sector_start_pcts` entry: session best `race.best`; personal best when driven (faster than every earlier clean lap) `race.green`; slower grey; lap not clean dark, no value; not recorded dashed. Clean = complete, green, no pit time, no incident points, one definition for Clean filter, zero lap, fastest and sectors (audit T1, T2, T6).
- **Incident chips**: `x1` outline, `x2`-`x3` tinted (A) or filled (B), `x4+` solid; shown whenever an event exists, `x0` included (audit T11).
- **Status chips**: `Excl` (outline, reasons in the tooltip), `Partial` (dashed). One per lap.
- **Event best**: the fastest best lap among one event's phases turns `race.best` on Sessions. Computed within the loaded event only.
- **Track silhouette** from the analysis geometry (`main_path`), checkered tick at the line; no geometry, no silhouette. The map cursor is the interaction colour, not yellow.
- **Runs** are identity in the white family with dashes (run 1 solid, 2 dashed, 3 dotted). Today run 2 is warning yellow, which reads as caution.

### Lap rail

Strict CSS grid: one track per field, the header row uses the same template, rows never wrap, about 1.9x today's width. Laps join the latest run that started before them (audit T4). Header: Laps, n selected, Green and Clean toggles. Footer: Fastest, Best three, Clear, All.

| Field | A (px) | B (px) | Content |
|---|---|---|---|
| swatch | 20 | 22 | line swatch |
| lap | 30 | 32 | lap number |
| flag | 18 | 20 | dot or flag |
| time | 80 | 86 | lap time; fastest `race.best`; not comparable dimmed |
| Δ | 58 | 62 | lap minus the zero lap, named in the header (`Δ L41`, audit T1); 1 decimal from 10 s; partial `—` |
| sectors | 3 x 50 | 92 | A cells with the gap to the session-best sector (`+.031`, `best`); B blocks only, value in the tooltip |
| fuel | 50 | 52 | gal used, 3 decimals |
| status | 58 | 62 | `Excl` or `Partial` |
| inc | 38 | 40 | `xN` |
| pit | 66 | 72 | pit pictogram and duration |

Run header: `Run n · L-range · ● green ● caution` (B adds a green/caution composition bar).

### Sessions figures

From `SessionOverview`, in fixed positions: result `P12 → P4` with a ▲8 / ▼2 chip (a win adds the checkered flag; Q and P show the classification only); laps; green ● and caution ● counts; best (`race.best` when it is the event best); stops (`race.pit` glyph); long run (`longest_green_run`); pace (`pace_slope_s_per_lap`: above +0.020 `race.loss`, below −0.005 `race.green`, else plain); fuel (`race.fuel`). Missing `—`, zero dot hollow. Phases in time order P, Q, R, the selector's order. Local date and time. Series is omitted where it only repeats the car name.

### Setups

Grouped by car (stripe, name, `17 setups · 4 raced`). Name prominent; values known solid (`148 values`), unknown dashed (`Values unknown`); raced = checkered flag; seen count and last date secondary; source only in the detail view (no Export chip). Unnamed garage captures collapse into one stacked entry with a count; one file name with other values shows `2 versions`. Filters: Car, Name, Raced (All, Raced, Not raced).

### Data the design needs

| Need | Status |
|---|---|
| `setup_type` (Fixed/Open) on `SessionSummary` | missing (only in the tuning identity): contract change |
| per-lap sector durations | not in the trace catalog (only `sector_start_pcts`): backend field; the UI does not integrate traces |
| per stop: tires changed, fuel added, duration | in the backend pit data the old app read; expose on the race and traces views |
| car fuel capacity | recording session info `DriverCarFuelMaxLtr` |

### Removed

Resume banner; Analyzed chip; Tests filter and the type tab strip; the Sessions search box; the Live update-rate selector (always 60 Hz, decimate on measured frame time); the Export chip in the setup list; mint accent on anything that is not interaction; disclaimer sentences.

## Direction A, Pit wall

A timing board: one dense table per screen, monospace numerals, uppercase column heads, near-black panels with a 1 px lit top edge, colour only in the data.

- Surfaces: app `#070B0F`, panels `#0F171D` to `#0C1318`, row rule `#141E25`, lit edge white 7 %. Radius 6-8 px.
- Sessions: one board. Event header row: track glyph, track, config, car stripe and car, series, Fixed/Open, date, with a car-colour wash and lit top edge. Session rows 44 px with a 3 px car-colour left edge. Columns (px): phase 56, session 200, result 160, laps 60, green 76, caution 84, best 100, stops 76, long run 96, pace 112, fuel 100, status (rest), chevron 28. Numbers right-aligned, mono 16 px.
- Phase tiles: the letter in an outlined square; the race tile outlined white and its row semibold.
- Investigation: map strip with cursor readout above the rail; sector cells carry the gap; trace readouts in a fixed column right of the charts with lap numbers.
- Charts: white run trends and dots; colour only for caution, pit, incidents and the fastest lap.

## Direction B, Broadcast

TV graphics: event cards, bold condensed numerals, roomier spacing, the glowing track silhouette as the signature, bands for phase and result.

- Surfaces: app `#0A1016` with a faint top glow, cards `#141E26` to `#10181F`, radius 14 px (tiles 10 px), soft shadow, lit top edge.
- Sessions: one 214 px card per event. Identity block 372 px: car-colour edge and wash, track 26 px, config and date, silhouette 118 x 78, car stripe and car, series, Fixed/Open. Then one tile per phase in time order: phase band 32 px with the time; hero = position 44 px (race adds the ▲/▼ chip and `from P12`, a win adds the checkered flag and WIN); 6 px result band on the race tile's left edge (checkered win, green gain, red loss); eight figures in a fixed 4 x 2 grid (Green, Caution, Best, Pace / Stops, Long run, Fuel, Laps) so every tile reads in the same place.
- Investigation: the rail is a full-height left card with 42 px rows, chunky sector blocks and the spotlight plate; the map strip with large readouts sits top right, traces below.
- Setups: one card per car, setup tiles in a four-column grid; unknown values as dashed tiles; unnamed captures as a stacked tile.

## Theme changes once a direction is chosen

Add to `config/theme.dark.json` and regenerate: the `race.*` tokens above, `interaction`, `identity.cars`, `phase.p/q/r` (B). Point `telemetry.caution`, `telemetry.pitService` and `telemetry.fuel` at the race tokens (today's values are too muted to read as colour), runs at the white family, the map cursor at `interaction`. Drop the trace-palette colours that imitate caution or the fastest lap (`#FFD45B`, `#F5F56B`, `#FF62D5`, `#E78BFF`). Interaction defaults to `interaction`; a driver-chosen accent applies to interaction only and may not be a race hue or sit next to one (today's mint is next to `race.green`). B also adds a display font family.
