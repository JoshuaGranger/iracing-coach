# Track traces and telemetry positions

Offline study, 2026-10-27. Geometry, recordings, fitted transforms and private
decoding material remain outside Git at
`J:/Codex/all-track-correlation-20261027/`. Open `report.html` for the maps,
coverage table, source receipts and reproduction scripts.

## Implementation and handoff status

Reviewed 2026-10-28. The bounded offline study is complete for its stated
corpus; acquisition of every current sim-PC track is not complete.

| Layer | Implemented or recorded | Still open |
|---|---|---|
| Research | Available archive inventory, six-layout correspondence, four-layout fresh-file confirmation, native SDK formula and limitations below. | Missing current geometry, version qualification and native bit parity. |
| Public parser source | [Conversion/projection helpers](coach_parsers/telemetry_registration.py) and [synthetic regression tests](../iracing-coach/tests/test_telemetry_registration.py). | Automatic per-track calibration and a production consumer. |
| Private evidence | Decoded geometry, maps, source receipts, fitted transforms and reproduction scripts under the path above. | These artifacts are machine-local; they are not distributed with a Git checkout or app image. |
| App and release | No production worker, server, collector or web caller uses this new mapping. | Integration, version-qualified data provisioning, product validation and a recorded release. No released parser wheel is established by this study. |

The current Docker build does not install this parser package. A documentation
update, successful offline fit or passing helper tests must not be reported as
a deployed map change. Existing app map behavior was not changed by this work.

Future agents should read this document before reopening coordinate or trace
semantics investigations. Use [route semantics](NATIVE-ROUTE-SEMANTICS.md) for
authored-family meaning and [native acquisition](NATIVE-ACQUISITION.md) for the
separately unqualified live reader. Earlier three-track inventory limits are
historical; this study expands coverage without establishing physical edges.

## Result and coverage

The available corpus yields **1,348 decoded RL curves and 178 RLC caches** in
85 route-bearing groups. Of those curves, 1,306 have supported closed geometry;
42 flags=0 curves retain their decoded bytes and explicit geometry refusal.
They are not silently closed. The extraction covers 106 local DAT archives in
27 track directories, plus retained current IRP, Martinsville and Laguna Seca
ranges. It is not an inventory of every current sim-PC installation: another
driving campaign occupied the rig, so no sim assets were scanned or transferred.

103 of 106 local DAT directory indexes passed complete framing. Three base archives have an
unexplained eight-byte directory suffix and remain refused; their separately
parsed declared records contain no RL/RLC. This exception is retained in the
inventory rather than presented as successful full-directory validation.
Source checks re-read 1,685 bounded ranges (about 6.75 MB), verified 1,526
decoded hashes and 170 bundle/map receipt pairs, and rechecked all 106 local
archive identities. Original installed assets and raw data were unchanged.

The expanded corpus includes ordinary edge families and `raceopt`, as well as
wet and parade variants. Their presence expands the earlier three-track
inventory, not the physical or legal claims in
[NATIVE-ROUTE-SEMANTICS.md](NATIVE-ROUTE-SEMANTICS.md).

The telemetry scan inspected 1,846 paths (1,659 NAS and 187 local, possibly
duplicated). It selected 50 unique hashes across 20 track IDs and 21 versioned
identities for compact analysis, then seven additional unique hashes for fresh
confirmation. All seven confirmation sources passed complete-lap screening
and exact raw-channel spot checks. Six layouts had corresponding accessible
geometry; Oulton Fosters and Limaland had only one recording each.

## Mapping

Four separate quantities must remain explicit: geographic conversion, axis
rotation, translation, and route station phase.

1. Convert decimal-degree latitude/longitude to local east/north. A single
   spherical radius produced repeatable decimetre-to-metre station distortion.
   Separate WGS84 prime-vertical and meridional radii removed most of it.
   The helper `gps_to_local_wgs84_en` implements this local approximation at a
   caller-specified origin and zero ellipsoid height. The constants are
   [NGA's WGS84 definition](https://earth-info.nga.mil/?action=wgs84&dir=wgs84),
   not fitted scale factors.
2. `wrap(Yaw + YawNorth)` supplies a precise rotation prior. Across the first
   50 selected recordings, maximum within-source deviation was 5.97e-7 rad;
   agreement with `TrackNorthOffset` was limited by its four-decimal rounding.
   The source-level relation alone does not prove the geographic converter;
   the native axis transform has separate static evidence.
3. Fit one east/north translation and one cyclic phase using training
   `LapDistPct` and native route station. Lateral offset remains free: this
   does not assume the car drives on the authored `race` curve. Keep the
   geographic origin fixed and preserve its units and provenance. The engine
   geographic origin has not been established as equal to WeekendInfo's
   rounded metadata origin.
4. Use the native span parameter convention when comparing station. Each
   cleaned cubic segment gets its 20-chord sampled length; station varies
   linearly with cubic parameter within that span. Dense plotted arc length
   is different. The study projects against 80 samples per segment and uses
   the recovered span station. It does not reproduce checkpoint alignment and
   the engine's processed three-metre sections bit-for-bit.

With theta and the fitted translation T in local east/north:

```text
X = sin(theta) * (east - T_east) + cos(theta) * (north - T_north)
Y = -cos(theta) * (east - T_east) + sin(theta) * (north - T_north)

raw_native_station ~= L * ((LapDistPct + fitted_phase) mod 1)
```

`telemetry_registration.RigidFrame` supplies the explicit inverse rotation and
translation. `CyclicPolyline` supplies nearest-chord diagnostics and ambiguity
flags; its station is ordinary chord arc length, **not** the native span
station. Neither helper fits or qualifies a transform. Public tests use only
synthetic data and have no dependency on the private study or iDriver.

## Recovered native SDK conversion

A subsequent bounded, offline review traced the converter through both SDK
callers to the registered `Lat`, `Lon` and `Alt` publication objects. The owned
executable SHA256 was
`6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362`.
Exact constants, disassembly receipts and a reference reconstruction remain in
the private study's `extraction/gps-converter/` directory.

The SDK producer uses these local harmonic coefficients, rather than the
separate WGS84/UTM conversion family also present in that executable:

```text
phi = native_origin_latitude_degrees * 0.0174532925
north_metres_per_degree = 111132.09 - 566.05*cos(2*phi) + 1.2*cos(4*phi)
east_metres_per_degree  = 111415.13*cos(phi) - 94.55*cos(3*phi)

east  = sin(native_yaw)*X - cos(native_yaw)*Y
north = cos(native_yaw)*X + sin(native_yaw)*Y
Lat = native_origin_latitude  + quantized_north / north_metres_per_degree
Lon = native_origin_longitude + quantized_east  / east_metres_per_degree
```

Both traced SDK callers quantize east and north independently to approximately
one centimetre using float32 arithmetic and nearest-step rounding, ties away
from zero. The stored step is `0.009999999776482582` metres. Rotation is float32;
the harmonic coefficients and geographic addition/division are float64.
Altitude is quantized at ten times that step. The stored radians multiplier
is the float64 encoding of `0.0174532925`, not exactly pi/180.

This establishes static producer semantics for the pinned executable. The
reference reconstruction uses standard trigonometric functions instead of the
native approximations, so native bit parity remains unproven. Native origin
metadata equality and linkage to the heading-channel producers remain separate
questions. The frozen WGS84 model below is a validated approximation; it was
not refitted after this discovery. Its original receipts remain immutable.
`confirmation/model-metadata-errata.json` corrects inherited spherical/arc
wording in those receipts without changing their hashes or numeric models.

## Independent confirmation

Earlier held-out files were inspected during model development. They are
reported as development comparisons, not untouched confirmation. After choosing
the WGS84/native-span model, seven further NAS recordings were selected under
a frozen plan, excluding 68 previously considered/historical hashes. No
transform was refitted or model reselected after scoring those seven files.

| Layout | Fresh files / points | Worst per-file station p95 | Largest station residual |
|---|---:|---:|---:|
| IRP | 2 / 2,762 | 8.4 mm | 15.9 mm |
| Martinsville | 2 / 2,529 | 7.5 mm | 10.6 mm |
| Lanier | 1 / 325 | 17.3 mm | 27.0 mm |
| Charlotte 2025 oval | 2 / 5,207 | 11.6 mm | 21.3 mm |

These are along-track station-consistency measurements, **not two-dimensional
position-error bounds or absolute lateral calibration**. GPS and lap station
come from the same simulator. Lanier and Charlotte use older local assets;
the close correspondence does not establish version identity. IRP and
Martinsville use retained current extraction context, not a fresh inspection
of the occupied rig. Oulton and Limaland remain training-only fits.

## Min/max comparisons

At the frozen transform, intersect each child with the same race-parent normal
cross-section. Do not compare equal child fractions or unrelated nearest points.
The development comparison retained 13,357 of 13,500 sampled positions after
excluding 143 ambiguous parent projections. All retained child intersections
were unique and ordered. Doubling parent and child density from 80 to 160
changed no interval classifications among 13,272 jointly usable positions; maximum
relative-margin change was 3.35 mm.

The selected positions outside the authored interval ranged from 6.87% at IRP
to 38.61% at Lanier. These are descriptive relationships under the fitted
frame. They are not infringements, wall penetrations or a measured safe lane.
The min/max families remain planning constraints, not established walls or
legal limits. Earlier spherical-frame wall comparisons retain their stated
conditional scope; the new geodetic evidence does not silently recalibrate
their historical coordinates or establish a physical wall identity.

## Remaining work

- Obtain current assets for the uncovered telemetry layouts when the sim is
  available. The private report identifies every missing layout/version.
- Recover/check exact installed layout metadata, native geographic origin and
  checkpoint phase before making a version-qualified product mapping.
- Keep native SDK geographic quantization and processed-section interpolation
  distinct from the local approximation used in this study.
- Require independent evidence for lateral physical edges, collision body,
  legal limits, actual ground height or an optimal racing line.
- Before app integration, bind geometry and transforms to exact track/layout,
  asset and executable identities; keep calibration and evidence decisions in
  the Python backend. Unknown or mismatched versions need explicit refusal.
- Provision purchased geometry and fitted private data outside Git, add the
  required consumer and validation, and record a release before calling this
  app functionality. No new UI or server contract is specified by this study.

The 14 uncovered telemetry track IDs are Las Vegas (103), Talladega (116),
New Hampshire (131), Indianapolis (133), Daytona (191), Kansas (214), Auto Club
(225), Bristol (365, two telemetry versions), Kentucky (371), Phoenix (419),
Portland (536), Iowa (559), World Wide Technology Raceway (560) and Richmond
(561). Local Daytona and Phoenix archives are different layout generations
and were not substituted. Four compared layouts also need current assets:
Lanier, Oulton Fosters, Limaland and Charlotte 2025 oval.

## Private evidence and resuming the work

All paths in this table are relative to
`J:/Codex/all-track-correlation-20261027/`. They are a local evidence index,
not public package dependencies or an assertion of an off-machine backup.

| Path | Purpose |
|---|---|
| `report.html`, `summary.json` | Human-readable maps, coverage and aggregate results. |
| `extraction/index.html`, `extraction/inventory-summary.json`, `extraction/layout-inventory.csv` | Every extracted group, supported geometry and archive exceptions. |
| `extraction/verification.json` | Source-range, decoded-member and bundle/map verification. |
| `extraction/gps-converter/` | Pinned executable evidence, exact constants, call chain and reference formula; no native bit-parity claim. |
| `telemetry/coverage.json`, `telemetry/selections.json` | Versioned telemetry coverage and initial source selection. |
| `registration-config.json`, `native-wgs84-correlation/` | Exact sources/routes, split membership and frozen numeric models for all six layouts. |
| `confirmation/frozen-models.json`, `confirmation/results.json`, `confirmation/model-metadata-errata.json` | Original model hashes, seven-file scores and metadata corrections that preserve the original hashes. |
| `telemetry/confirmation-v1/receipt.json` | Fresh selection provenance and original-channel checks. |
| `envelopes/results-v2/` | Normal-cross-section min/max comparison and density checks. |
| `check-windows-final.log`, `check-linux-final.log` | Successful full repository gates at the end of the implementation turn, before later unrelated workspace changes. |

For a rerun, inspect the private script arguments and recorded source identities
first: `extraction/batch-extract.py` and
`extraction/rebuild-current-range-sets.py` acquire
decoded copies; `register.py` and `phase-register.py --native-parameter --wgs84`
implement the measured approximation; `validate-frozen.py` scores frozen
models; `build-report.py` and `finalize-report.py` assemble and check the report.
Use new private output directories and preserve existing models and receipts.
These research scripts rely on the recorded local dependencies and paths;
they are not a portable public extraction CLI.

If the private directory is unavailable, retain the documented findings and
report that their underlying artifacts are unavailable on that machine. Do not
recreate receipts, infer fitted transforms from screenshots, put private assets
in Git or introduce an iDriver dependency to make tests pass. New models need
new confirmation sources; the seven files already scored are no longer fresh
confirmation for a revised model.

Public verification uses `tools/check.ps1`; `tools/check.ps1 -Linux` covers
the Linux gate. The focused helper tests are
`iracing-coach/tests/test_telemetry_registration.py`, and the portable Windows
image-path regression is in `iracing-coach/tests/test_native_process.py`.

The public package contains explicit conversion/projection helpers only. No
private fitted transform, purchased geometry, telemetry, archive key material,
live reader qualification or driving admission is added by this study.
