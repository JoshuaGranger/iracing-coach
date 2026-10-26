# What the extracted track traces mean

Verified knowledge from the 2026-10-24–26 REA investigation. Coach owns these
parsers and their semantics; iDriver consumes them for driving experiments.

The curves are authored geometric references and lateral constraints. Native
consumers use min/max to bound a padded spatial sampling grid. **A min/max
curve is not an established collision wall or official track limit.** At the
two inspected counterclockwise ovals, `racemin` is toward the outside and
`racemax` toward the infield. Calling `racemax` the outer wall would reverse
the observed sides.

## Established roles

These are static dataflow findings from the shipped executable, supported by
decoded assets. They do not depend on guessing what a filename means.

| Family | Established use | Interpretation for a consumer |
|---|---|---|
| `race` | Base curve for checkpoint-aligned processed sections and the spatial grid. | A geometric reference with direction; a candidate path, with speed and vehicle feasibility still to be determined. |
| `racecenter` | Separately authored curve projected relative to `race`; bound as the grid wrapper's center reference. | Preserve it independently. It is not calculated by averaging min/max. |
| `racehigh`, `racelow` | Side-constrained derivatives processed before ordinary min/max. | Alternative authored curves. Exact runtime driving-policy selection is not established. |
| `racemin`, `racemax` | Ordered parent-relative lateral constraints. Query wrappers supply lower/upper scalars to a grid builder that adds padding. | A planning/sampling interval, retaining its parent and direction. |
| `raceedgemin`, `raceedgemax` | Separate kind-2 categories. Derived offsets are clamped against margin-bearing parent extrema. | Distinct edge constraints; neither their names nor the clamp proves physical or legal edges. No such assets occurred in the three complete inventories below. |
| `racepanicmin/max` | Later ordered constraints than ordinary min/max. | Preserve the category; when a vehicle selects it remains untraced. |
| `raceopt` | Independent base category with its own center/min/max children and possible separate grid wrapper. | Catalog support exists; no matching asset occurred in the three inspected inventories. The name does not prove a time optimum. |
| `pit0`, center/min/max, and `pitb` equivalents | Separate parent families. Native wrappers can splice ordinary and alternate pit curves by station, then trim using ground-query class transitions. | Pit geometry/planning references. They do not supply pit-speed enforcement lines, official entry/exit rules or stall ownership. |
| `pit0parade` | Kind-10 category, excluded from the ordinary kind-less-than-10 extrema update. | A distinct processing category; do not silently apply the ordinary cleanup/extraction policy. |

The catalog has 43 non-pit catalog categories plus 14 categories per pit. Important race
slots are base 0, center 1, high 6, min 8, max 11, panic min/max 14/15,
edge min/max 16/17 and independent `raceopt` base 22. Pit 0 uses base/center/
min/max 43/44/45/46; alternate pit base/center/min/max use 50/51/52/53.

Min/max sides are relative to the parent's forward direction. The recovered
projection tests `cross(direction, point - parent)`: min uses the negative
side, max the positive side. In a right-handed XY plot those are right and
left. Inner/outer depends on track direction and local turn geometry; it is
not a universal property of the filename.

The pattern of high/low, defend, pace, limp, panic and parade categories suggests
authored driving/planning behavior. A direct vehicle/AI policy consumer has not
been established. That attribution remains an inference, not a recovered
control algorithm.

## Measurements that settle the earlier chart questions

These measurements use decoded, cleaned RL curves. At each sampled parent
point, intersect its normal line with each same-direction child curve. These
are geometric measurements, **not native processed-section parity**: native
section normals are chord-scaled and interpolated after checkpoint alignment
and three-metre resampling; raw RL fractions with unit normals differ.

At 80 samples per prepared segment, all tested race and pit min/max/center
intersections were unique and forward; all min/max intervals were ordered.
There were no missing intersections or reversed intervals at 1,920 Martinsville
race, 2,960 Martinsville pit, 1,840 IRP race or
3,520 IRP pit sample locations. Counts describe samples, not independent
observations or equal-distance weighting.

| Geometric result | Martinsville | IRP |
|---|---:|---:|
| `racemin` signed offset from `race`, metres, positive left | −7.02 to +0.04 | −2.85 to +0.01 |
| `racemax` signed offset from `race`, metres, positive left | +0.65 to +6.51 | +4.11 to +8.75 |
| Min/max normal-section separation, metres | 6.13–8.47 | 5.53–10.98 |
| `racecenter` minus arithmetic min/max midpoint, metres | −3.10 to +2.97 | −4.27 to −1.20 |

Both race loops have positive signed area in native XY, consistent with the
observed counterclockwise direction: left is toward the infield. This supports
the side interpretation without needing a GPS translation.

- At Martinsville, `race` approaches the outer constraint on the straights and
  the inner constraint through the turns, consistent with an out-in-out line.
  RL supplies no speed/time or car-specific performance model, so this does not
  establish a fastest momentum line.
- At IRP, the complete decoded bytes of `racehigh.rl` and `racemin.rl` have the
  same SHA-256. Categories can share geometry; they need not represent distinct
  physical features.
- `racecenter` is demonstrably not the geometric midpoint, especially at IRP.
  Middle traces should not be relabeled “optimal line” from appearance.
- Pit min/max intervals can be shifted entirely to one side of their parent.
  Both ovals contain such sections. Do not force zero into an ordered interval
  or classify it as a missing corridor merely because it excludes the parent.
- RL loops close cyclically. The entire `pit0` loop is not an observed
  speed-limited pit-road segment; native trimming/splicing is separate.
- Laguna Seca's wet and dry min/max assets differ. This supports driving/
  planning choices; it is not evidence of physical walls moving with weather.

### IRP full outer-envelope comparison

The saved full-lap outer profile is `testdriver/model/irp_track.json`:
`stations_s` plus `e_out_observed`, **1,077 populated stations**, approximately
one metre apart, built from 38 recordings. The canonical model and checked
archived copies have SHA-256
`c064d37d8b48c9349dbe5c86e7c764a2d91ddc50bc7eaa22bca988c2f1016cdb`.
This is the historical measured outer envelope; it is much more coverage than
the 88 separately stored impact candidates.

Its producer, `trackmodel.py`, keeps on-track, off-pit car positions above
15 m/s with absolute body slip below 0.12 rad, then takes the minimum lateral
offset in each station bin. All saved bins are populated; no filling is needed.
The outer envelope is not asphalt-filtered. The asphalt filter applies to the
separate inner envelope. The saved optimizer's `e_min` is also different: it
adds a neighborhood window, an inward margin and a shift cap. Do not substitute
that conservative planning bound for the recorded outer profile.

The offline comparison reconstructs the entire outer profile using
`BaseLine.at(stations_s, e_out_observed)`. Every REA race-family curve receives
the same frozen clean-recording-derived rigid transform, described below. The
outer profile and impact events are **not** used to fit that transform. At each
of the 1,077 original stations, intersect the baseline normal with the forward
REA curve, then compare the two lateral positions at that same cross-section.
All 11 race-family curves had unique forward intersections at every station.

Under the SDK-angle, non-reflected `race` alignment:

| REA curve | Median absolute separation from saved outer profile | p95 absolute separation | Stations within 1 m |
|---|---:|---:|---:|
| `racepanicmin` | **0.54 m** | 2.49 m | 65% |
| `racemin` = `racehigh` | 0.75 m | 2.72 m | 61% |
| `racecenter` | 1.45 m | 4.54 m | 32% |
| `race` | 2.07 m | 4.82 m | 30% |
| `racemax` | 7.89 m | 11.31 m | 0% |

The result is not just one chosen alignment: across the four non-reflected
race/racecenter and zero/180-degree alternatives, `racepanicmin` is the closest
by median absolute separation (0.54–0.70 m), followed by `racemin`/`racehigh`
(0.75–1.05 m); `racemax` remains 7.73–7.94 m away. The signed gap of `racemax`
is toward the infield at every station in all four fits.

**Interpretation:** `racepanicmin` is the strongest correspondence to the old
outer envelope among the extracted race-family curves; ordinary min/high lies
near it. `racemax` corresponds to the inner side, not the outer boundary.
This supports an outer envelope/constraint role for the minimum-side curves.
It does not prove that either curve is the collision-wall face: the recorded
profile measures car centres, and translation/lane choice remains uncertain.
Hypothetical ±2 m translations are retained separately; they worsen apparent
agreement and demonstrate why the sub-metre medians are not wall-clearance
accuracy claims.

The comparison includes a full-track overlay, full-lap lateral profiles,
separation profiles, all-curve ranking, alternative fits, translation sensitivity
and a per-station CSV. No simulator, new driving or rig access is used by this
comparison. Private reproduction artifacts are listed below.

### Registration and separate impact-candidate comparison

The old model contains 88 records named `wall_impacts`. Their producer selects
acceleration-threshold **car-centre positions**, not surveyed wall coordinates:
speed above 3 m/s, absolute lateral or longitudinal acceleration above 28 m/s²,
separation and an outer-side filter. The separate `e_out_observed` envelope is
also an outermost observed moving car centre.

The comparison used IRP assets and recordings with matching track version
`2026.08.31.03`. A fixed-scale rigid registration was fitted to 1,000 selected
clean samples from two recordings. A third whole recording supplied 500
held-out samples. **The 88 impact candidates were not used to fit the
registration.** Reflection, 180-degree rotation and using `racecenter` instead
of `race` were checked as alternative assumptions.

Under the SDK-angle, non-reflected `race` fit:

- Held-out car-to-race distance p95 was 3.25 m. This includes the human's lane
  choice and is not a calibrated coordinate-error bound.
- Impact-candidate signed-left distance to `racemin` had p05/median/p95
  −1.90/−0.61/+0.30 m; 62% were within 1 m and 93% within 2 m.
- Corresponding distance to `racemax` had p05/median/p95
  −10.22/−7.10/−5.66 m; none was within 2 m.
- Fitting to `racecenter` instead left `racemin` much nearer than `racemax`,
  but changed the signed median to −0.89 m.
- Hypothetical ±2 m east/north translation perturbations changed the fraction
  within 1 m of `racemin` to 10–81%. These are sensitivity tests, not confidence
  bounds. A 180-degree rotation also remained plausible for this oval's shape/
  heading test; reflection reversed the matched directions.

**Conclusion:** the outer-side candidates are consistent with `racemin` being
the nearer outer planning constraint. They do not validate wall coincidence.
Lane choice and absolute translation remain confounded; vehicle footprint,
contact timing and which object caused an acceleration event are not recovered.
A wall-clearance claim needs an independently anchored coordinate transform
and confirmed contact/vehicle geometry. Fitting contacts and then “validating”
the same contacts would be circular.

## Reproduce extraction and interpretation without an agent

1. Identify an owned track archive/layout and record its version, size and
   stable file identity. Work read-only with the simulator closed or confirmed
   off track. Record executable identity separately; do not execute a derived
   binary or attach to the live simulator for offline extraction.
2. Use the build-pinned offline DAT adapter to read the 50-byte header, validate
   framing/check, then read the bounded directory. Validate complete record
   consumption, member ranges, duplicate names, lengths and codec modes.
   Select route names by explicit layout prefix and native catalog policy.
3. Read only selected RL/RLC stored ranges. The verified adapter supports
   ordinary member codecs 0/7. Fail explicitly on unsupported context-dependent
   members; do not invent data from `track.ini` or track-surface assets that
   were not decoded. Check source identity again and hash every copied range.
4. Parse decoded RL/RLC copies using `coach_parsers.native_route` and
   `native_route_cache`; validate cache checksums. Build a new private bundle
   with `native_route_bundle`, preserving source hashes, header/version/flags,
   parent/category, cleanup choice and sample density. Existing output must not
   be overwritten. These steps are deterministic and need zero model calls.
5. For geometric side/width checks, use the parent normal at each sample and
   solve each child segment's line intersection. Retain the forward local
   branch; record absent/multiple intersections instead of guessing. Compute
   signed cross-product offsets. Compare min/max at the **same parent
   cross-section**, not equal child fractions or unrelated nearest points.
   Repeat at a finer sampling density and retain the measurement method.
6. For a recorded-car overlay, establish frame, metres and phase separately.
   Use clean training recordings, held-out recordings and explicit transform
   alternatives. Freeze the transform before examining contacts. Preserve
   uncertainty; a good-looking overlay is not a wall calibration.
7. Store assets/maps/recordings outside Git. Publish the method, aggregate
   findings, provenance and unresolved claim, rather than installed geometry,
   raw telemetry or decoding material.

On IRP this process inspected a 1,985,986,324-byte archive by transferring only
the first 33,220 bytes and its 20,112-byte directory. The adapter itself read
52,326 bytes across 19 requests in about 0.53 s locally. The source remained
stat-stable and the directory passed validation. This measures local offline
extraction, not end-to-end remote/UI timing.

Complete inventories: Martinsville 14 RL + 2 RLC, Laguna Seca 17 RL + 2 RLC,
IRP 16 RL + 2 RLC. All three lacked edge and `raceopt` members. IRP's ordinary
bundle contains 15 RL + 2 RLC: parade is deliberately excluded, so its count
must not be mistaken for full archive inventory.

The reusable RL/RLC readers and bundle writer ship in Coach. The build-pinned
installed-DAT acquisition adapter remains a private research prototype, not a
released Coach archive-import command. Runtime native acquisition has separate
qualification limits in [NATIVE-ACQUISITION.md](NATIVE-ACQUISITION.md). This
document does not upgrade that status or grant driving admission.

## Native evidence and retained research

Executable SHA-256:
`6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362`.
Addresses are original virtual addresses for this build, not portable symbols.
Static conclusions must be rechecked for another build.

| Procedure | Evidence |
|---|---|
| Family loader `0x141d58c10` | `ev_b413275d54c00028bc2bd98011a7abdadacf2bbb748f2fa197c4ded488a31cd2` |
| Derived constraints/extrema `0x141d4ce00` | `ev_64b25dd8bca3a95f8f3df2b2a3e240e13b386a0fa8533d6275f819022d2b7240` |
| Grid family binding `0x141dc8ab0` | `ev_4902b80bcdb5b511d359a8f5c975e521293ac9163788ac817e7aa4a854cce08d` |
| Lower query wrapper `0x141dd16e0` | `ev_76aaa07808d95c9d36c0fb4338908bb1272e2a6eb53c20c6950fcd7902aa7e8c` |
| Upper query wrapper `0x141dd15f0` | `ev_f4fb7617e63028d19b93476697a91a9b07050d8090938d3fa400b96d29bd6f39` |
| Padded grid bounds `0x141dc0970` | `ev_4cb3543741600cb164916a79d5c7b08f8d66de28f834e2d31046b662e7dd8807` |

The 2026-10-25 Opus study was recovered from a private partial handoff; its
standalone documents had never been written. This document incorporates its
findings, rechecks complete inventories, adds the downstream grid consumer,
recomputes cross-sections and performs the IRP comparison.

Retained private research in iDriver (historical local name `iRacing-utility`):

- `out/rea-route-consumer-review-20261026/consumer-semantics-review.json`:
  native code excerpts, evidence identifiers and source hashes. SHA-256
  `4b27d5db4f6c270bde0f092bb07b885fa0f89a7be733b68d0424160fd7509f54`.
- `out/rea-track-data-20261024/irp-semantics-20261026/`: range hashes, complete
  directory, extraction receipt, private bundle and `measure-route-sections.py`
  / `normal-cross-sections.json` (20/80 sampling).
- `out/track-trace-validation-20261026/`: clean-recording registration,
  impact comparison and translation-sensitivity scripts/results.
- `J:/Codex/irp-rea-comparison-20261026/`: `compare-full-outer.py`,
  `comparison.json`, `station-comparison.csv` and
  `IRP-outer-envelope-vs-REA.png` / `.svg`. This complete-envelope comparison
  uses the frozen registration above. J: also holds its isolated plotting
  dependencies; they do not alter Coach or the global Python installation.

These locations preserve the audit trail. Coach has no runtime or test
dependency on iDriver or the private artifacts.
