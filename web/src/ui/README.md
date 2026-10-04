# Shared UI (web round 2, task S)

The components and helpers every page codes against. Specification: `docs/08-rewrite/web-round-2-plan.md`. Tokens come from `config/theme.dark.json` through `pnpm gen:theme`; a page never writes a colour literal.

## FilterBar (`filter-bar.tsx`)

The page heading and its filters, one rule for every page.

```tsx
<FilterBar
  title="Sessions"                      // string -> h1; or a node (h1 plus a Live now link)
  controls={[
    { id: "car", label: "Car", node: <MultiSelect .../> },
    { id: "library", label: "Library", minRem: LIBRARY_MIN_REM, node: <LibraryControl value={library} onChange={setLibrary} /> },
  ]}
  activeCount={selectedValues + (library === "lab" ? 1 : 0)}
  onClear={clearAll}
>
  {chips}                               // optional: removable chips, rendered under the heading in both layouts
</FilterBar>
```

- Props: `{ title, controls: FilterControl[], activeCount, onClear, children? }`. `FilterControl = { id, label, node, minRem? }`.
- The bar renders its own `<header class="page-head fb-row">`; do not also render `PageHead`.
- **Inline** when the title and every control, each at `minRem` (default 7.5, a multi-select pill), with their gaps and the Clear button (while `activeCount > 0`) fit the heading row. The decision is a `ResizeObserver` measurement (`fitsInline`, pure and unit-tested), never a device test. Set `minRem` for a wider control (`LIBRARY_MIN_REM` is 15; a search field is wider).
- **Collapsed** otherwise: one `Filters` button (`Filters · N` while `activeCount` is N) opening a popover under the heading (pointer devices) or a bottom sheet with a backdrop (`(pointer: coarse)`). Each control shows under its `label`; `Clear` and `Done` are in the surface footer. Escape, outside press and Done close it; focus returns to the button.
- A `MultiSelect` inside the surface opens in the flow (full width), so the surface scrolls and never clips it.
- The bar holds no selection state. The page owns it and saves it per page through the driver's prefs (`session_filters`, `setup_filters`) so Back and navigation keep it. `activeCount` counts what is in force, a non-default library included.
- Test hooks: `.fb[data-layout="inline" | "collapsed"]`, `.fb-trigger`, `.fb-pop`, `.fb-sheet`, dialog named `Filters`.

## LibraryControl (`library-control.tsx`) and `core/library.ts`

`Library: Joshua | Lab`. Props `{ value: Library, onChange, mainLabel? }`; place it as a `FilterControl` (`minRem: LIBRARY_MIN_REM`). `useLibraryChoice()` returns `[library, setLibrary]`: default `"main"`, shared across pages for the visit, **never saved** (a reload is back on `main`).

API (`core/api.ts`): `type Library = "main" | "lab"`. `paths.sessions(c, { library })`, `paths.setups(c, { library })` (and so `api.sessions`, `api.setups`) take it; only `lab` is sent (`?library=lab`), `main` keeps the plain URL. Resource keys include the query, so the two libraries cache apart; the shell's prefix invalidation covers both.


Mock (`web/mock`): `library=lab` on sessions and setups returns the lab fixture (sessions 900 and 901, IRP practice with the NASCAR car; setup 90 `bot sweep.sto`); the main list never contains them.

## Chip rule (`kit.tsx`)

`<Chip variant="exception" | "constraint" | "action" ...>`. No status-normal chip exists: a normal state draws nothing (no Values known, Recording, Analyzed, file name).

| Variant | Use | Look |
|---|---|---|
| `exception` | Queued, Failed, Deferred: explains missing results. `level="fail"` (default, loss red) or `"wait"` (muted, dashed). Optional `action={{ label: "Retry", onClick }}` adds a Retry segment inside the chip. | squared tag |
| `constraint` | A fact that changes a decision: Fixed/Open, Not used in pace fit. | neutral outline |
| `action` | The chip is the button (`onClick`). | interaction colour, underlined |

A chip is squared (`--radius-control`) and never a pill; pills with counts and crosses are filter controls. `chipClass(variant, level)` is the tested class model. `Tone` stays for `Notice` only.

## Selection has one state (`selectable.ts`, `segmented.tsx`, `styles.css`)

The selected item of any control ignores hover; hover changes only unselected items. The global rules `button:hover`, `.btn:hover`, `.seg button:hover` and `.ms-opt.hl` skip anything marked `.on`, `.selected`, `[aria-pressed="true"]`, `[aria-selected="true"]` or `[aria-current]`, so a tab or toggle needs no `:not(.on)` of its own. Mark selectables with one of those, using `selectableClass(selected, ...base)` or `segmentClass`. A page that writes its own hover rule excludes `SELECTED_SELECTOR` (`.row:hover:not(:where(...))`). A selected multi-select option keeps its tint under the pointer; the keyboard cursor on it is a ring.

## Colour helpers (`core/palette.ts`)

- `runColour(n)`: run `n` (1-based) as `var(--run-1)` to `var(--run-8)`, cycling after eight; `runHex(n)` for canvas and SVG attributes; `runDash(n)`: the `stroke-dasharray` second cue (solid, dashed, dotted, dash-dot, short dash). Run-level marks only: trend lines, run cards, the sticky run row. The palette keeps clear of green flag, caution, fastest lap and loss.
- `raceProgressColour(i, n)`: `#rrggbb` of lap `i` (0-based) of `n` laps, a start-to-finish gradient through `identity.progress`. Pass the phase's full lap count so a filter never recolours a lap. Lap-level marks only.

## Sticky offset

`--toolbar-height` (3 rem, from `layout.toolbarPx`): a row that sticks under a page toolbar sets `top: var(--toolbar-height)` and gives its panel no top margin inside the scroll parent, so pixel 0 of scrolling moves nothing.

## Gallery

`#/__kit` shows every shared component (marks, chips, selectors, run and progress colours, the filter bar). The round's e2e checks (`e2e/app/round2-shared.spec.ts`, viewports from `e2e/app/round2-shared.ts`: 1440x900, 1024x768, 390x844) run against it.
