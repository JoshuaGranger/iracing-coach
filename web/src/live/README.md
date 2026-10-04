# Live (web round 2, task A4)

The Live page: the 60 Hz tile board and its direct-manipulation editor. Specification: `docs/08-rewrite/web-round-2-plan.md` (Live editor), backlog #25.

| File | What |
|---|---|
| `connection.ts`, `frame.ts`, `history.ts`, `rate.ts` | socket, frame decode, trend history, frame-time governor (60, 30, 10 Hz) |
| `tiles.ts`, `text.ts` | the overlay twin of tile text (parity vectors); not changed by the editor |
| `tile-view.tsx`, `board.tsx`, `grid.tsx` | one tile, the board on its own render loop, the grid with its edit layer |
| `layout.ts` | layout body, validation (the server's wider rule), grid helpers |
| `representation.ts` | what the style strip offers per metric, colour rules, the waiting shape |
| `edit-model.ts` | snapping, move or swap, resize preview, drag-in, select-then-place, Undo stack |
| `editor.tsx` | edit bar, Layout surface, style strip, palette |
| `live.css` | editor and waiting-shape styles (tokens only) |

## Editor

- **Edit layout** turns the board into the editor. Each tile shows a grip (top right) and a resize corner (bottom right); empty cells show as dashed slots. Both handles are 44 px on a coarse pointer.
- **Move**: drag the grip (a mouse may drag the whole tile). The target snaps to whole cells inside the grid. Dropping on exactly one equal-sized tile swaps the two; anything else that overlaps is refused. **Resize**: drag the corner; the preview reads `W×H` when valid and `No room` when not, and a refused release changes nothing.
- **Touch** has select-then-place beside drag: tap a tile, tap **Move**, tap a `Here` cell or a `Swap` tile. Nothing depends on hover or long-press.
- **Palette** (`Add`): metrics not on the board. Tap adds at the first free spot (a full grid grows a row, then a column, to 8 x 8); drag drops on a cell with the same preview. A drag near the top or bottom edge scrolls the page.
- **Style strip** for the selected tile: Show, Colour, Size (Width, Height), Unit, Decimals, Window (trend), Mark ABS (brake trend), Move, Remove.
  - Show offers Text; Trend for continuous metrics; Bar for bounded metrics (both bounds known: throttle, brake, brake bias; fuel once its capacity is known); Gauge for fuel once its capacity is known. Position, lap times, counts, states and the cue are Text only. A new fuel-laps tile starts as Text. Text is stored as `number` (or `status` for the four status metrics), so the contract enum is unchanged.
  - Colour (`accent`) paints the value of a neutral tile and the line, bar or arc. It is not offered on flag, pit-road, run phase, coach cue and mandatory repair, whose colour is a state; tone (good, caution, warning) and the waiting, not-measured and insufficient-evidence states always override it. A swatch identical to the metric's own colour is offered once, as Default.
- **Autosave**: every completed gesture (drop, release, tap in the strip, palette add, Rows/Columns) writes `PUT live-layouts/{id}` through the outbox with a coalescing key. Pointer movement writes nothing. Editing the built-in Default saves `Default copy` on the first change and switches to it.
- **Undo** (one tap) restores the layout before the last gesture and saves it; the stack holds 50.
- **Layout** surface (out of the normal flow): Name, Rows, Columns, Save as new layout, Delete layout. **Done** closes the editor.
- The layout this device shows is remembered per device (`localStorage coach.live.view.{customer}`); `(on rig)` marks the rig's active layout and **Show on rig** sets it.

## Waiting

A tile with no fresh frame keeps its shape: its format in dashes (`P–`, `–:––.–––`, `–.–– s`), its unit, its bar track, gauge arc or trend frame, and `Waiting` underneath.

## Cost

The board renders on every frame; the edit layer is memoised and redraws only when the layout, the selection or the preview changes. `e2e/app/live.spec.ts` (editor idle cost) samples 4 s at 4x CPU throttle with the board, the open editor and a selected tile, writes `web/target/round2/A4/idle-cost.json`, and requires no governor step-down and a mean frame under 25 ms.
