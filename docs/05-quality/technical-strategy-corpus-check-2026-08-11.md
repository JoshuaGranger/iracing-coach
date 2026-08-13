# Technical analysis and race-plan corpus check (2026-08-11)

## Scope

- Read-only corpus: `\\192.168.1.82\Joshua\iRacing Temp\development-test-data\20260811T025605Z-last-20-races`
- 12 race bundles: Portland, Iowa, Kentucky, New Hampshire, and Daytona.
- The updated `build_technical_insights` contract was replayed over the latest saved deterministic analysis in every bundle. Raw IBT files were not rewritten.
- The pass describes recorded outcomes and associations. It does not assign causes to tire wear, pace change, incidents, position change, or pit results.

## Concrete outcomes

### Portland no-stop race

- The old contract produced an empty, unavailable pit-strategy card.
- The updated contract classifies the no-stop plan as an available result and shows stops completed, race distance, and distance-specific no-stop fuel headroom.
- The saved race reports 16 scheduled laps, 15 recorded laps, and 34.7 laps of measured all-green range after reserve. The resulting conclusion is that no fuel stop was needed and the scheduled-distance range margin was 18.7 green laps.
- Race planning can now pass `race_distance_laps=15` to `build_race_card`; the plan then says no stop is needed for 15 laps and uses the 19.7-lap margin. It no longer presents 34.7 laps as a context-free strategy for a 15-lap race.

### Iowa race with no clean reference laps

- `03-iowa-2025-oval-20260806T010953` contains zero laps that pass every clean-reference screen, so the previous technical card discarded its dynamics conclusion.
- The run still contains 50 complete green timed laps, 43.35 seconds of front wheel-speed divergence, 5.75 seconds of rear wheel-speed divergence, and a mean lap-level yaw-rate p95 of 20.98 deg/s.
- The updated card keeps the clean-reference gate intact but uses a robust representative-race-pace band for descriptive pace and controls. It reports the dynamics explicitly and directs the user to the highlighted brake/throttle zones before changing technique.
- The front-divergence detail is normalized to 0.867 seconds per green lap so a long race is not made to look worse merely because it has more laps.

### Pace outliers

- Green-labeled timing alone included start/restart anomalies and produced implausible variation: 34.8 seconds at Portland and more than 15 seconds in some New Hampshire reports.
- The updated contract first uses the analyzer's clean-reference lap numbers when at least two exist. Otherwise it uses a median/MAD-bounded representative race-pace set.
- Portland now uses 13 representative race-pace laps and excludes the 223.15-second start lap. Iowa clean-reference races continue to use their stricter screened sets.

### Two-tire and four-tire strategy evidence

- The 12-race corpus contains confirmed four-tire calls, but no confirmed two-tire or right-side-only call.
- Direct 2-vs-4 conclusions therefore remain unavailable for every real race in this corpus. No benefit or penalty is invented.
- Each confirmed service now carries the exact changed corners, side classification, service time, following-run early pace when available, and pit-cycle position change when available.
- A focused synthetic contract test covers a confirmed RF+RR call and a confirmed four-tire call. The 2-vs-4 comparison appears only when both observed call types exist; requested service flags alone never create a tire-change conclusion.

## Real-corpus maximum metric counts

These are the maximum detailed metric rows emitted by a single card after this pass:

| Card | Maximum rows |
| --- | ---: |
| Pit strategy | 8 |
| Tires | 9 |
| Fuel | 9 |
| Racecraft and pace | 16 |

The overview should select a compact subset and reserve all rows for the opened detail view. Rendering all 16 racecraft rows in a half-height overview card cannot meet the 1280x720 no-scroll target without harming readability.

## Driver-improvement coverage added

- No-stop plan result and scheduled-distance fuel headroom.
- Strictly confirmed four-, right-side-, left-side-, diagonal-, and other partial tire-call descriptions.
- Clean-reference pace when available; bounded representative race pace when it is not.
- Throttle commitment, brake peak, brake/steer overlap, steering corrections, mean steering load, and speed envelope for the chosen pace sample.
- Front wheel-speed divergence, rear wheel-speed divergence, ABS-active time, yaw-rate envelope, and per-green-lap normalization even when no tire endpoint or clean pace trend exists.
- Contextual race-card actions and fuel decisions for the requested race distance; short races use a race-pace priority instead of a generic long-run instruction.

## Limits retained deliberately

- Wheel-speed divergence is a diagnostic proxy until the aligned trace confirms lock or wheelspin; oval radius, stagger, banking, and bumps can contribute.
- Position-phase changes describe where positions changed, not why.
- Incident points do not identify contact type, fault, or damage.
- A direct tire-call comparison requires confirmed examples of both service choices.
- Tire-service legality and mandatory-service conclusions still require race rules.
