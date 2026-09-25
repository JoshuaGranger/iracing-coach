# IRP review sections: derivation

Status: catalog record `232-irp/2026-09-24.1` in
`iracing-coach/skills/analyze-iracing-race/scripts/review_sections.v1.json`,
regression-tested in `iracing-coach/tests/test_race_inspection.py`.

These are **review sections** for comparing recordings over identical
distances. They are not official turn numbering and not verified turn
boundaries. "Turns 1-2" and "Turns 3-4" name the two corner complexes that the
geometry below shows. The split between entry and exit is each complex's
geometric mid-point. It is not a Turn 1/Turn 2 or Turn 3/Turn 4 boundary.

## Problem

Without a catalog record, each IRP recording got its own detected regions,
lettered from A:

| Recording | Detected regions | Widths | Lap covered |
|---|---:|---|---:|
| Race 2026-09-23 21:09 | 10 | 1–8% lap | 31% |
| Practice 2026-09-23 20:34 | 8 | 1–19.5% lap | 35% |
| Practice 2026-09-23 20:45 | 10 | 1–20.5% lap | 35.5% |

Letters with the same name covered different distances in each recording, so
no section of a practice could be compared with the same section of the race.

## Identity

The session info in all eight recordings is `TrackID` 232, `TrackName` `irp`,
`TrackDisplayName` "Lucas Oil Indianapolis Raceway Park",
`TrackDisplayShortName` "IRP", `TrackType` short oval, `TrackNumTurns` 4 and
`TrackLength` 1.0834 km. There is no `TrackConfigName`. The engine then takes
the display short name as the configuration (`track_config` "IRP"), so the
exact configuration key is **`232-irp`**. The backend reported that key for the
race and both practices.

## Data

These are read-only copies of the sim PC recordings
(`stockcars2 supra2019_irp *.ibt`, Toyota Supra Class B), read with
`ibt_reader.load_telemetry` at the native 60 Hz. The raw files were not
modified.

| Recording | Session recorded | SHA-256 prefix | Line-to-line laps | Clean laps used |
|---|---|---|---:|---:|
| 2026-09-20 16-16-47 | Offline testing | `f68ac402` | 2 | 2 |
| 2026-09-20 22-47-07 | Offline testing | `48b1e236` | 0 | 0 |
| 2026-09-23 20-34-12 | Practice | `5ab05bfb` | 18 | 14 |
| 2026-09-23 20-45-56 | Practice | `9a2a711e` | 32 | 18 |
| 2026-09-23 21-02-03 | Race event, practice session | `10297971` | 3 | 2 |
| 2026-09-23 21-03-55 | Race event, lone qualifying | `34075666` | 0 | 0 |
| 2026-09-23 21-04-24 | Race event, lone qualifying | `ff73e493` | 2 | 2 |
| 2026-09-23 21-09-03 | Race | `86504f48` | 111 | 24 |

To count as clean, a lap had to run from the line to the line, stay on track,
keep off pit road, keep LapDistPct rising, stay above 15 m/s, and finish within
3% of that recording's best lap. That gave **62 laps from six recordings**.

## Method

1. Convert Lat/Lon to local east/north metres (equirectangular about the track
   centre).
2. Resample each lap onto 1,000 LapDistPct bins. Smooth circularly over 11
   bins (1.1% lap).
3. Compute curvature per lap as the change in heading over the change in
   distance. Take the lap median in each bin.
4. Cross-check against the chassis: the median YawRate/Speed curvature
   correlates with the GPS curvature at **r = 0.985**.
5. Use lift (throttle < 90%), brake onset (> 5%), brake release, minimum speed
   and full-throttle return (≥ 95% held) on each lap as supporting evidence.

## Geometry results (62 laps pooled)

- The median racing line is 1,068 m long. Its total heading change is
  **+360.0°**: one counter-clockwise lap with left turns only.
- It has two corner complexes of about 180° each. Their curvature plateau is
  0.0103 1/m (radius **96–98 m**) at 18–28% and 68–79% lap.
- Between them are straights that turn less than 3°: the backstretch, measured
  at 45.2–53.7%, turns +2.1° over 92 m. The frontstretch turns +1.9° over 53 m
  from 95.1% to the line, and +0.8° over 52 m from the line to 4.8%.
- The start/finish line is in the middle of the frontstretch.
- Altitude varies by 1.4 m over the lap.

Racing-line curvature against LapDistPct, as crossing points of the pooled
median. The range covers race, practice and offline-test laps taken
separately.

| Curvature threshold | Turns 1-2 complex | Turns 3-4 complex |
|---|---|---|
| > 0.0010 1/m (R < 1000 m) | 0.040–0.463 | 0.534–0.962 |
| > 0.0015 1/m (R < 667 m) | **0.051–0.457** (race 0.048–0.457, practice 0.054–0.459, tests 0.049–0.455) | **0.545–0.954** (race 0.544–0.955, practice 0.549–0.957, tests 0.540–0.951) |
| > 0.0025 1/m (R < 400 m) | 0.069–0.444 | 0.565–0.944 |
| > 0.0050 1/m (R < 200 m) | 0.095–0.405 | 0.593–0.908 |

The 0.0015 1/m threshold is about 15% of the plateau. It is the lowest
threshold whose exit crossing is stable across sources. At 0.0010 1/m the
Turns 3-4 exit wanders between 0.957 and 0.999, because cars drift towards the
wall across the line.

Heading of the median line, relative to the frontstretch at the line:

| Heading | 5° | 90° | 175° | 185° | 270° | 355° |
|---|---|---|---|---|---|---|
| LapDistPct | 0.072 | **0.248** | 0.431 | 0.573 | **0.749** | 0.929 |

## Supporting driving evidence

Values are medians, with the 10th–90th percentile range, over 62 laps. Race
and practice medians agree within 0.011 for every event.

| Event | Turns 1-2 | Turns 3-4 |
|---|---|---|
| Lift | 0.051 (0.043–0.059) | 0.544 (0.535–0.552) |
| Brake onset | 0.054 (0.050–0.065) | 0.547 (0.540–0.558) |
| Brake release | 0.192 (0.141–0.212) | 0.690 (0.652–0.733) |
| Minimum speed | 0.238 (0.207–0.270), 87.5 mph | 0.757 (0.717–0.778), 87.5 mph |
| Full throttle again | 0.398 (0.363–0.416) | 0.903 (0.869–0.936) |

## Boundaries

| Boundary | Value | Rule and evidence |
|---|---:|---|
| Start of A | 0.005 | Traces are distance-binned at bin centres, so a section that touches 0 or 1 cannot be timed. A section also cannot wrap the line, so 0.5% on each side of the line (about 5 m each) is left uncovered. |
| A → B | 0.045 | Just before the Turns 1-2 curvature onset (0.051) and the lift and brake medians. 85% of laps lift and 97% brake at or after 0.045. |
| B → C | 0.25 | Geometric mid-point of the complex (heading 90° at 0.248). The minimum speed straddles it: 71% of laps reach it before 0.25. |
| C → D | 0.455 | Curvature falls below 0.0015 1/m at 0.457 (0.455–0.459 by source). Full throttle returns inside C on 98% of laps. |
| D → E | 0.535 | Before the Turns 3-4 curvature onset (0.545) and the lift and brake medians. 89% of laps lift and 100% brake at or after 0.535. |
| E → F | 0.75 | Geometric mid-point (heading 270° at 0.749). The minimum speed straddles it: 37% of laps reach it before 0.75. |
| F → G | 0.955 | Curvature falls below 0.0015 1/m at 0.954 (0.951–0.957). Full throttle returns inside F on 94% of laps. |
| End of G | 0.995 | Same bin-centre margin as the start of A. |

## Sections

| ID | Name | Start–end (% lap) |
|---|---|---|
| A | Frontstretch, from the line | 0.5–4.5 |
| B | Turns 1-2 entry | 4.5–25.0 |
| C | Turns 1-2 exit | 25.0–45.5 |
| D | Backstretch | 45.5–53.5 |
| E | Turns 3-4 entry | 53.5–75.0 |
| F | Turns 3-4 exit | 75.0–95.5 |
| G | Frontstretch, to the line | 95.5–99.5 |

The sections are contiguous and do not overlap. Together they cover 99% of the
lap. `boundary_status` is `geometry_derived_section`. The single-letter
`label` is kept because the app draws it in 20 px badges and map markers. The
`name` goes into the section provenance (the app's tooltip), into
comparison labels such as "Section B · Turns 1-2 entry (4.5–25.0% lap)", and
into action text such as "section B (Turns 1-2 entry)".

## Verification on the real recordings

The backend analysis workflow (`analyze_race_workflow`, the path behind MCP
`analyze_iracing_race`, native rate) was run on the race and both practices.
All three now report source `configuration_catalog` with version
`232-irp/2026-09-24.1` and the identical sections A–G. These are the medians
of the timed passes over each recording's screened laps:

| Section | Race 21:09 (34 laps) | Practice 20:34 (7 laps) | Practice 20:45 (3 laps) |
|---|---:|---:|---:|
| A Frontstretch, from the line | 0.769 s | 0.761 s | 0.747 s |
| B Turns 1-2 entry | 4.943 s | 4.710 s | 4.673 s |
| C Turns 1-2 exit | 5.129 s | 4.923 s | 4.858 s |
| D Backstretch | 1.606 s | 1.563 s | 1.549 s |
| E Turns 3-4 entry | 5.136 s | 5.004 s | 4.880 s |
| F Turns 3-4 exit | 5.105 s | 4.854 s | 4.939 s |
| G Frontstretch, to the line | 0.831 s | 0.806 s | 0.890 s |

Parity checks:

- **Cached analyses.** Analyses cached by the previous backend held detected
  regions. On the next open they were rebuilt with catalog sections, without
  decoding the recording again: `refresh_race_investigation` now also treats a
  section version that differs from the catalog's as stale.
- **Rebuilt against cold.** Each rebuilt investigation was identical to one
  from a cold analysis.
- **Before against after.** Outside `race_investigation`, cold analyses before
  and after the change differed only in archive paths, timestamps and
  archive-derived observation ids.

## Limits

- The data is one car (Toyota Supra Class B) and one driver. The bounds come
  from the racing line, not from a surveyed centreline.
- Entry and exit split at the geometric mid-point. Minimum-speed metrics
  therefore report the lowest speed inside the section, which is not
  necessarily the apex.
- The application keeps its own cache of analysis responses and trusts it
  while the investigation's schema and projection versions are current. An IRP
  recording opened in the application before this record existed can keep
  showing detected regions until that cache entry is replaced.
