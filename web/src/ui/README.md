# Shared UI (web round 2, task S)

The components and helpers every page codes against. Specification: `docs/08-rewrite/web-round-2-plan.md`. Tokens come from `config/theme.dark.json` through `pnpm gen:theme`; a page never writes a colour literal.

## FilterBar (`filter-bar.tsx`) and MultiSelect (`multi-select.tsx`)

The page heading and its filters, one rule for every page (round 3, #34-#36).

```tsx
<FilterBar
  title="Sessions"                      // string -> h1; or a node (h1 plus a Live now link)
  controls={[
    { id: "car", label: "Car", node: <MultiSelect label="Car" noun="cars" options={cars} selected={sel.car} onChange={...} /> },
    { id: "name", label: "Name", minRem: 12, node: <input .../> },
  ]}
  activeCount={selectedValues}          // reads on the collapsed button: Filters · N
/>
```

- Props: `{ title, controls: FilterControl[], activeCount }`. `FilterControl = { id, label, node, minRem? }`. No `onClear`, no children.
- The bar renders its own `<header class="page-head fb-row">`; do not also render `PageHead`.
- **Nothing in the heading moves when a filter is set or cleared.** There is no Clear button and no row of chips. Every inline control has a fixed width (a `MultiSelect` pill is 10 rem, `DEFAULT_CONTROL_REM`; set `minRem` for a wider control such as a search field) and the collapsed `Filters · N` button is 7.5 rem whatever N is. A control that appears or disappears must not move its neighbours.
- **Each dropdown carries its own selection**: `pillText(label, options, selected)` reads `Car` (nothing chosen), `Car · Supra` (one) or `Car · 2` (several); only values the options still offer count; long text ellipsizes inside the fixed pill.
- **Clear lives inside the dropdown**: the first row of the list, always present and disabled while nothing is chosen, so the options never shift when the first one is chosen. Tab from the search field reaches it; focus leaving the control closes the list. The footer reads `n of m` and nothing else. A `Segmented` clears by choosing All; a search field clears itself.
- **Inline** when the title and every control at its width, with the gaps, fit the heading row. The decision is a `ResizeObserver` measurement (`fitsInline`, pure and unit-tested), never a device test.
- **Collapsed** otherwise: one `Filters` button (`Filters · N` while `activeCount` is N) opening a popover under the heading (pointer devices) or a bottom sheet with a backdrop (`(pointer: coarse)`). Each control shows under its `label`; `Done` is in the surface footer. The same rule holds inside: each dropdown carries and clears its own selection. Escape, outside press and Done close it; focus returns to the button.
- A `MultiSelect` inside the surface opens in the flow (full width), so the surface scrolls and never clips it. Pressing inside an open list keeps focus on its search field, so the surrounding dialog never counts as leaving.
- The bar holds no selection state. The page owns it and saves it per page through the driver's prefs (`session_filters`, `setup_filters`) so Back and navigation keep it.
- **A filter offers only values that exist in what it filters.** Setups' Car list is the cars that have setups in the setups response (a car with only unlinked files is not offered, a name search does not shrink the list); `carsWithSetups(groups)` in `setups/library.ts`.
- Test hooks: `.fb[data-layout="inline" | "collapsed"]`, `.fb-trigger`, `.fb-pop`, `.fb-sheet`, `.ms-clear-row`, dialog named `Filters`, group named `<Label> filter`.

## The library (`core/library.ts`)

There is no Library control and no mention of Lab in the interface. `main` is the default everywhere; `lab` is reached only by `?library=lab` in the page's URL (`#/d/1507810/sessions?library=lab`, `#/d/1507810/setups?library=lab`). `useLibraryChoice(): Library` reads the hash query on every render (`libraryOfQuery`); nothing is stored. A link to another page drops the query, so the lab is asked for page by page.

API (`core/api.ts`): `type Library = "main" | "lab"`. `paths.sessions(c, { library })`, `paths.setups(c, { library })` (and so `api.sessions`, `api.setups`) take it; only `lab` is sent (`?library=lab`), `main` keeps the plain URL. Resource keys include the query, so the two libraries cache apart; the shell's prefix invalidation covers both.

Mock (`web/mock`): `library=lab` on sessions and setups returns the lab fixture (sessions 900 and 901, IRP practice with the NASCAR car; setup 90 `bot sweep.sto`); the main list never contains them.

## Absence is silence

A field the backend does not report is a dash or nothing. No sentence says what the software did not do or could not do (`not extracted`, `not measured`, `unavailable`, `the server`, `the backend`); a tooltip does not either. State a fact that changes a decision (`No values`, `Queued`, `Failed`), not the pipeline's failure to produce one.

## Chip rule (`kit.tsx`)

`<Chip variant="exception" | "constraint" | "action" ...>`. No status-normal chip exists: a normal state draws nothing (no Values known, Recording, Analyzed, file name).

| Variant | Use | Look |
|---|---|---|
| `exception` | Queued, Failed, Deferred: explains missing results. `level="fail"` (default, loss red) or `"wait"` (muted, dashed). Optional `action={{ label: "Retry", onClick }}` adds a Retry segment inside the chip. | squared tag |
| `constraint` | A fact that changes a decision: Fixed/Open, Not used in pace fit. | neutral outline |
| `action` | The chip is the button (`onClick`). | interaction colour, underlined |

A chip is squared (`--radius-control`) and never a pill; pills with counts and crosses are filter controls. `chipClass(variant, level)` is the tested class model. `Tone` stays for `Notice` only.

## One control recipe (round 5, #78 #79 #80)

Every chip, pill, badge, tab, segment, select and button follows one recipe; a page does not write its own.

- **Height from the tokens.** Buttons, selects, tabs and segments are `--control-height` (`--touch-target` on touch); chips, badges and count tags are `--chip-height` (`styles.css` `:root`). `display: inline-flex; align-items: center; line-height: 1`; the label (display caps), the value (body text) and the chevron sit on one centre line.
- **Text is centred by its capitals.** A line box centres a font's ascent and descent, so capitals ride a pixel or two high or low whatever the face (Bahnschrift 1.6 px high at 15 px). `text-box: trim-both cap alphabetic` trims a text run to its capitals and the control's `align-items: center` does the rest, in any font. It applies to block containers and inline boxes, not to the anonymous box around bare text in a flex container, so `control-text.ts` gives the bare text of every control (a `button`, `summary`, tab or button role, or an element whose class has a `btn`, `chip`, `badge`, `pill`, `tab`, `toggle`, `seg` or `tag` token) a `<span class="ctl-t">` as the element is created (`installControlText()` in `main.tsx`; neighbouring text pieces become one span; a class naming the text itself, `-text` or `-label`, is not a control). A text-only span anywhere inside a control is trimmed by the stylesheet. A control that holds its text in a flex or grid element of its own wraps it in `ctl-t`; an ellipsized value pads its box and pulls it back (`.ss-val`) so trimming clips no descender. A browser without `text-box` (Firefox) falls back to the line-box centring, a pixel or two off. Do not add a `label > span` caption rule inside a control: the global `label > span:first-child` caption style applies to any label.
- **Focus shows for the keyboard only.** `control-behaviour.ts` sets `html[data-input="pointer" | "keyboard"]` from the last device used. In `pointer` mode `styles.css` draws no ring, outline or focus shadow on a control (a text field keeps its focus border), and once a click is over, the control that holds focus lets go of it (`blurAfterPointer`: not a text field, not a native select, which blurs on change, not a `tabindex="-1"` target such as an open list, not a keyboard-driven chart surface or a scrolling region, not anything inside an open dialog; a chart mark (lap dot, pit tag, run line) keeps its focus because the focus is what shows its readout on a tap, and draws no ring for the pointer). A component sheet may write `:focus-visible` rules; they only ever show for the keyboard.
- **One list, one look.** `SingleSelect` (`single-select.tsx`) is the single-choice twin of `MultiSelect`: a pill reading `LABEL value` and a chevron, a list under it with the current option marked, Up/Down/Home/End, Enter or Space to choose, Escape to close and return to the pill. No option is lit until the pointer or a key touches the list (`MultiSelect` the same). It replaces a native `<select>` where the page needs the value text and the list styled and centred (the Traces Spotlight and Layout, and the map colour beside the track map, whose list hangs over the traces card); a native select keeps the same recipe through the global `select` rules and the pointer-mode focus rule.
- **Checked** by `e2e/app/controls-u1.spec.ts` at 1440x900, 1024x768 and 390x844: on Sessions, Setups, a setup detail, Live and its editor, Race, Runs, Sections and Traces, (1) every control child's centre (text by its capitals) is within 1 px of its control's, (2) every interactive element is clicked and nothing is left with a ring, an outline, `:focus-visible` or a lit class that is not the real state, (3) the Spotlight list is the drawn laps, (4) the rail's key and heading gutters.

## Selection has one state (`selectable.ts`, `segmented.tsx`, `styles.css`)

The selected item of any control ignores hover; hover changes only unselected items. The global rules `button:hover`, `.btn:hover`, `.seg button:hover` and `.ms-opt.hl` skip anything marked `.on`, `.selected`, `[aria-pressed="true"]`, `[aria-selected="true"]` or `[aria-current]`, so a tab or toggle needs no `:not(.on)` of its own. Mark selectables with one of those, using `selectableClass(selected, ...base)` or `segmentClass`. A page that writes its own hover rule excludes `SELECTED_SELECTOR` (`.row:hover:not(:where(...))`). A selected multi-select option keeps its tint under the pointer; the keyboard cursor on it is a ring.

## Colour helpers (`core/palette.ts`)

- `runColour(n)`: run `n` (1-based) as `var(--run-1)` to `var(--run-8)`, cycling after eight; `runHex(n)` for canvas and SVG attributes; `runDash(n)`: the `stroke-dasharray` second cue (solid, dashed, dotted, dash-dot, short dash). Run-level marks only: trend lines, run cards, the sticky run row. The palette keeps clear of green flag, caution, fastest lap and loss.
- `raceProgressColour(i, n)`: `#rrggbb` of lap `i` (0-based) of `n` laps, a start-to-finish gradient through `identity.progress`. Pass the phase's full lap count so a filter never recolours a lap. Lap-level marks only.

## Sticky offset

`--toolbar-height` (3 rem, from `layout.toolbarPx`): a row that sticks under a page toolbar sets `top: var(--toolbar-height)` and gives its panel no top margin inside the scroll parent, so pixel 0 of scrolling moves nothing.

## Gallery

`#/__kit` shows every shared component (marks, chips, selectors, run and progress colours, the filter bar). The round's e2e checks (`e2e/app/round2-shared.spec.ts`, viewports from `e2e/app/round2-shared.ts`: 1440x900, 1024x768, 390x844) run against it.
