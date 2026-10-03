# SDK byte fixtures (`.sdkfix`)

Replay input for `coach-collector run --replay FILE` and for the collector tests. The format is in `collector/README.md` ("Replay, dry run, recording").

| File | What it is |
|---|---|
| `g3-garage-pit-2026-10-03.sdkfix` | Gate G3: a **real** shared-memory recording of the rig (solo Test Drive, Toyota Supra, Las Vegas oval, 2026-10-03), sanitized and minimized. 2,186,962 bytes, SHA-256 `ee8a4e8e2d2da3af12bf2e94d92e2ad816da02fcff0b5dc0daf3f9db9f3d0fcc`. |

This is the one fixture in `test-data/` that is not synthetic in origin: its tick flags, timings and session-info structure are what iRacing published. It holds no name, customer id, setup file name or setup number of the owner (see below), and `collector/tests/rig_g3.rs` fails if any of those strings reappears.

## Content of `g3-garage-pit-2026-10-03.sdkfix`

885 s of the session, 1,529 of the 52,502 recorded ticks, 39 versions of the session info, 277 of 322 variables:

1. Sim loading, then the session-start garage (tech inspection, `UpdateCount` 1, the placeholder `INVALID_LOADTYPE` before the first setup name).
2. A 209 s garage visit: two setup files loaded, four applies, one unsaved edit, Apply and Close (`UpdateCount` 2 to 8). `IsInGarage` falls to 0 for 40 to 83 ms before each load or apply, 14 times; the name changes 50 ms after the real exit.
3. The car in its pit stall on track (`IsOnTrack` 1, `PlayerCarInPitStall` 1, `OnPitRoad` 1; speed never above 0.08 m/s, the car did not move), then back to the session screen.
4. Three more visits: leave without Apply (counts 10 to 12), Revert (13 to 18), reload of the file already loaded (19 to 21); a second stay in the pit stall; the sim quits (status 0).

## Sanitizer

`sanitize_sdkfix.py RAW OUT` (Python 3, standard library only) rebuilt the fixture from the raw capture, which stays outside the repository (`coach-migration/fixtures/g3-rig-2026-10-03.sdkfix`, 474,923,725 bytes, SHA-256 `24C9D515AFE13BF5301240AB6C15F411B79B1DCD8C7D1A3E9392F0768B1DBB5B`). It:

- replaces the driver name (`Joshua Granger`, also the team name) with `Test Driver` and the customer id with `100001`;
- renames the two setup files (`Base LV R.sto`, `Test LV R CAM.sto`) and blanks the recording's own path (`TelemetryDiskFile`);
- scales every number in the `CarSetup` section by a factor fixed per key, so equal values stay equal and distinct values stay distinct but the numbers of the purchased setup are not in the repository; `UpdateCount` is kept;
- drops the array variables (CarIdx and shock arrays), repacks the row from 8,585 to 1,088 bytes, and thins the ticks to one per 800 ms except within 3 ticks of any change in status, session-info counter, `IsInGarage`, `IsOnTrack`, `OnPitRoad` or `PlayerCarInPitStall`;
- refuses to write the output if `Joshua`, `Granger`, `1507810`, `Maconi`, `NOAPS`, `claude` or `joshu` occurs in it.

Chat is not part of the SDK map, so a capture never contains it.

A test using the fixture creates its own `.sto` files under the two neutral names; the fixture itself contains no setup payload.
