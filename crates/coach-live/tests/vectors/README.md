# coach-live vectors

`engine.json` holds 35 scenarios (about 1,070 engine steps, 2,900 tile readings) whose expected values come from the C# live monitor, not from this crate.

## How it was made

The committed `engine.json` is the record; it cannot be regenerated from the current tree. The C# desktop app was deleted in rewrite wave 4, and with it the throwaway .NET 10 harness that built against it (`tools/csharp-oracle/`). Both are in Git history: the last commit containing them is `3ff5e87`. `origin` paths name files in that commit. The harness did this per scenario:

1. Writes a synthetic iRacing SDK shared-memory map under `Local\IRSDKMemMapFileName`: header, variable table, session-info YAML, three sample buffers.
2. Reads each tick through the real `IRacingSdkTelemetrySource` (the reader normalisations: `Positive`, `NonNegative`, `Percentage`, lap offset, sentinel laps, float `SameLapGap`, the unfiltered class-position behind search, session types and steering ratio from the YAML).
3. Feeds the sample, with a scripted timestamp, to the real `LiveTelemetryService` (private `Poll` through reflection), which applies `ObserveSessionBoundary`, resets the engine, runs `LiveTelemetryEngine` and keeps the history.
4. Records the snapshot, the cue message, the epoch, and, on some steps, `LiveTelemetryCatalog.Read` for every tile in every unit with its trend values and display range.

To regenerate from history (byte-identical output was expected), check out `3ff5e87` in a separate worktree and run there:

```powershell
dotnet build crates\coach-live\tools\csharp-oracle\Oracle.csproj -c Release --artifacts-path <scratch>
dotnet <scratch>\bin\Oracle\release\iRacingCoach.Tests.dll crates\coach-live\tests\vectors\engine.json <git-commit-of-companion-app>
```

Scenarios named in `origin` are converted from `CoordinatorTests.cs:2421-2808` (the twelve `LiveTelemetry_*` engine tests and the service epoch test), `LiveMonitorTests.cs` (the laps-remaining sentinels, catalog ranges, stepped metrics) and `SteeringBasisTests.cs` (live ratio, road wheel, tiles, engine). Eight `race_sim_seed_N` scenarios are generated laps with caution, pit road, repair, tow, black flag, overshoot episodes, a disconnect, a pause and a lap-counter reset. The rest probe one rule each (missing flags, missing scoring arrays, odd class positions, cue order, fuel cues, pace outside the band, large braking ratios).

## Format

```
{ "format": 1, "scenarios": [ {
    "name", "origin", "safe_glance",
    "session": { "subsession_id", "session_types": [[num, type]...], "steering_ratio", "info_text" },
    "omitted_variables": [SDK variables absent from the map's table],
    "steps": [ { "t": ms, "connected", "paused", "tick", "tick_rate",
                 "session": {...}?,      // a new session context from this step on
                 "set":    {...},        // Sample fields that changed (null = missing; "NaN"/"Infinity" as strings)
                 "expect": {...},        // expected LiveState keys that changed
                 "tiles":  [...]? } ] } ] }
```

`set` and `expect` are cumulative deltas: the reader keeps a running map. `expect` keys are LiveState field names; `session_epoch` and `first_of_epoch` follow the contract (crate-api.md 4.4), which differs from C# in one way: a disconnect does not change the epoch, the next connected sample does. The harness derives them from the C# epoch by counting only increments on connected steps. Values the contract makes absent (non-finite, or outside the channel's integer type) are expected as absent. `expect.cue` carries the id, priority, evidence and the exact C# message; the Rust test compares `cue_text`.

`catalog` (top level) lists the 33 C# `LiveTelemetryCatalog` definitions (`id`, `label`, wire `units`, `default_style`, `precision`, `min`, `max`, `step`); `the_tile_table_matches_the_csharp_catalog` compares `live-channels.json` tiles to it.

A tile entry is `{ id, unit (wire id or null), primary, secondary, state, value, range, trend }`. `primary` is C# `DisplayValue` plus a space and the unit label when C# shows one; `range` is `[Minimum, Maximum]` after C# `ApplyRange` (null when it does not apply); `trend` is C# `TrendValues` (the caller's history for `display_range`).

## Known, accepted differences (asserted by `engine_vectors.rs`)

- **Pace range, 1 ms.** C# derives the pace band in double precision; the wire channels 33 and 34 are f32, and lap times are truncated to milliseconds, so a bound that sits on a whole millisecond can read 1 ms low (about 0.2 % of tile readings). The test allows exactly that and nothing more.

- **One lap to green outside a Race (W2, 2026-10-04).** The C# monitor reads `SessionFlags` `0x200` as a caution in every session. iRacing holds that bit through the whole of an Offline Testing session and a Lone Qualify out-lap with no caution, so the G4 bot run showed "Caution" for 50 laps and, with every lap a caution lap, never produced a fuel-use sample for "Laps to pit". The engine follows the backend (`analysis_engine._caution_flag_series`): `0x200` alone is a caution only in a Race or when the session type is unknown; yellow, yellow waving, caution and caution waving count in every session. One step of this file is affected, `critical_cues_and_repair` step 11 (`0x200` in Practice); the test rewrites its expectation (flag state racing, the cue of the next step) and asserts it did so once. The new vectors are `solo-test-session.json` (44 flag cases from the real Test Drive capture's words across session types, and the first lap with a fuel estimate), driven by `tests/solo_test_session.rs`.

`serde_json` runs with `float_roundtrip` in this crate's dev-dependencies: its default float parser is not correctly rounded and the vectors compare `f64` values exactly.

Frame vectors are in `docs/08-rewrite/contracts/vectors/live-frame/` (`tools/gen_frame_vectors.py`).
