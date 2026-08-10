# UI design system and technology decision

## Product character

Build a focused telemetry workstation: modern, precise, vibrant enough to feel alive, and comfortable for long sessions on a second monitor. Its layout, icons, components, and racing language must be original.

Use layered mineral green-charcoal surfaces rather than black, restrained saturation rather than neon, and contrast where it communicates hierarchy or driving information rather than around every panel. Subtle translucent glass and low-saturation color are welcome in app chrome and background layers; dense working charts remain solid and legible. The user's global theme color influences interaction accents without recoloring semantic telemetry. Avoid pure black backgrounds, pure white body text, decorative excess, oversized warnings, and race imagery that competes with coaching.

Default body copy uses `textSecondary`. Reserve the brighter `textPrimary` token for headings, selected controls, direct coaching, and key metrics; this keeps long reading surfaces gentle even though every token remains accessible.

The machine-readable source of truth is `config/theme.dark.json`. Do not scatter literal colors, spacing, radii, or motion durations through components.

## Technology decision

Release one uses C# on .NET 10 with a WPF host and Blazor Hybrid views.

- WPF owns the native window, title bar integration, tray, folder/file pickers, process supervision, shutdown, and Windows accessibility hooks.
- Razor components and CSS own navigation, forms, cards, tables, telemetry layouts, responsive behavior, and the component system.
- SVG owns track geometry, labels, cursors, and low-density interactive overlays.
- Canvas owns dense high-frequency telemetry traces. Downsample to screen-pixel buckets while retaining minima and maxima so short brake or steering events are not visually erased.
- The existing Python MCP/CLI backend remains the deterministic analysis engine and is bundled with a pinned runtime.
- There is no local web server. Blazor Hybrid components run in the native .NET process and render through the embedded WebView.

This is the fastest route to a refined UI while preserving reliable Windows integration and the already-tested analysis backend. Do not rewrite the backend in C#, Rust, or C++ without a profiler proving a user-visible bottleneck and regression tests demonstrating equivalent results.

Native WPF is the fallback if a measured Blazor/WebView blocker appears. WinUI 3 is an acceptable reconsideration before substantial UI code exists, but it adds Windows App SDK deployment work while the telemetry view still needs custom SVG/canvas-style rendering. Avalonia and MAUI add cross-platform scope that is not required. Tauri/Rust and Electron add another application toolchain; Python desktop UI weakens packaging and styling. None provides a meaningful speed advantage for Garage61, web research, or AI latency.

The app must verify the WebView2 Runtime at install/startup and provide the Evergreen bootstrapper or an explicit packaged-runtime policy. See Microsoft's [.NET 10 WPF Blazor Hybrid tutorial](https://learn.microsoft.com/en-us/aspnet/core/blazor/hybrid/tutorials/wpf?view=aspnetcore-10.0) and [WebView2 distribution guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).

## Typography and density

- UI family: `Segoe UI Variable Text`, then `Segoe UI`, then sans-serif.
- Metrics, hashes, paths, and timing values: `Cascadia Mono`, then `Consolas`, with tabular numerals.
- Body: 14 px / 20 px.
- Compact rows and metadata: 12-13 px / 16-18 px; never smaller than 12 px.
- Section title: 16 px semibold.
- Page title: 24-28 px semibold.
- Primary metric: 20-24 px semibold with tabular numerals.
- Use weights 400, 500, and 600. Do not make every label bold.

Use a four-pixel grid. Default card padding is 16 px, major section spacing is 24 px, and dense rows are 40-44 px tall. Prefer 6 px control corners, 8 px cards, and 10-12 px major panels. Use flat grouping and whitespace before adding another card.

## Surfaces and controls

- Expanded navigation is 216-232 px; collapsed navigation is 60-64 px.
- The selected navigation item uses a slightly raised gray plus a two-pixel accent edge, not a bright filled block.
- Normal cards use `surface1`, a one-pixel subtle border, and no visible shadow.
- Menus/dialogs use `surface3` and one short soft shadow.
- Primary buttons use `accentFill`; secondary buttons use a surface and border.
- Pointer targets are at least 36 px, preferably 40 px.
- Keyboard focus is always visible as a two-pixel ring with a two-pixel offset.
- Unavailable is a neutral gray state. It is not an error.
- Inline alerts stay beside the affected evidence. Toasts are only for background completion or failure.
- Do not block navigation with a global spinner. Use the persistent job tray.

Required reusable components include `AppShell`, `NavRail`, `CommandBar`, `SurfaceCard`, `MetricTile`, `StatusChip`, `EvidenceBadge`, `ClaimBlock`, `InlineAlert`, `JobTray`, `StageProgress`, `PhaseStepper`, `TrackMap`, `TelemetryTraceStack`, `TimelineLane`, `RunRow`, `TireCornerGrid`, `SetupDiff`, `ExperimentCard`, and `RollbackBanner`.

## Evidence and status presentation

Always pair color with the literal evidence tag `[M]`, `[D]`, `[I]`, `[P]`, or `[U]`. Preserve the tag in visible text, tooltips, exports, and accessible names. Do not communicate evidence class, faster/slower, pass/fail, or warning state using color or opacity alone.

Use the telemetry palette only for traces and meaningful state. Add line style, icon, label, or hatch:

- Caution: amber diagonal hatch.
- Pit/service: blue-gray dotted band.
- Traffic screening: violet low-opacity band.
- Repair/confounded: coral cross-hatch.
- Tow: violet dashed boundary.
- Target/comparison: explicit legend label and a distinct dash/weight.

For tire plots, use hue for left/right and line style for front/rear instead of four unrelated colors.

## Screen composition

### Home

Use one quiet health strip, the four workflow actions, and compact recent-race rows. Backend health is primary; Codex and Garage61 are independent secondary chips. Keep interruption context beside the affected race.

### Race Analysis

The deterministic Race Card is the first and strongest surface. Put Bottom line, Start, Long run, and Strategy above detail. Corner coaching rows use `corner/zone -> tire phase -> action -> evidence`, with longer explanations collapsed by default. Exact targets and relative coaching must have visibly different components.

### Track and tire-age view

Use an approximately 55/45 track-to-traces split and one synchronized cursor. Place the phase control below both. When only observed phases exist, show a stepped Early/Middle/Late selector with exact green-lap-on-set bounds; never draw a continuous wear slider. Remove target traces from the legend when comparison quality is unusable and show `Exact target unavailable`. Uncalibrated groove movement uses neutral `path shifted` language.

### Runs, tires, and interruptions

Use one row per run with green/caution laps, fuel, pace slope, tire endpoint status, service, and clean/confounded eligibility. Use a four-corner tire grid and distinguish measured-at-stop, stale/unconfirmed, and unmeasured-final-run states. Interruptions use parallel timeline lanes sharing one time axis; pit road, stall, service, mandatory repair, optional repair, and tow are never stacked into an additive total.

### Planning and tuning

Race Planning shows fuel feasibility first, then all-green, observed-caution, and reserve scenarios as peers. Starting Tune uses `Context -> Source -> Package -> Baseline Run`. Progressive Tuning keeps the driver's wording and telemetry corroboration in separate panels and keeps the exact rollback fingerprint visible throughout an experiment.

## Motion, scaling, and accessibility

- Hover/color feedback is immediate or 120-140 ms and MUST NOT delay pointer tracking, telemetry paint, cursor motion, scrolling, or drag feedback.
- `motionMs.structure` is the single app-wide duration for structural motion. It is 500 ms for panel expansion/collapse, drawers, and post-drop reflow; change this one token if later observation proves the app should feel faster or slower.
- Every element participating in one structural change MUST begin and finish on that same token. Do not combine delayed visibility, independent cleanup timers, or multiple layout durations.
- Maximum movement: 4-6 px using `cubic-bezier(.2, 0, 0, 1)`.
- Never continuously animate telemetry, health, or status indicators.
- Honor Windows reduced-motion and high-contrast preferences.
- Meet WCAG 4.5:1 for normal text and 3:1 for large text and meaningful UI boundaries.
- Support 100%, 125%, 150%, 175%, and 200% Windows scaling.
- Support keyboard navigation, screen-reader names, logical focus order, and visible focus.
- Tooltips may explain a control but never contain the only copy of essential information.

Default window size is 1440 x 900; minimum supported size is 1100 x 720. At smaller widths, collapse navigation and move secondary detail below the primary work surface rather than shrinking telemetry or text into unreadability.

## Visual release gate

Before release, capture and review screenshots of every primary screen at 100%, 150%, and 200% scaling, plus empty, loading, unavailable, warning, repair-confounded, and long-content states. Run automated contrast checks against the tokens, keyboard-only navigation tests, reduced-motion tests, and a color-vision simulation. A functionally correct screen that violates this design system is not release-ready.
