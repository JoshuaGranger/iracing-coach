# IRP: width and alignment test of the wall hypothesis

Follow-up to [the route-semantics study](NATIVE-ROUTE-SEMANTICS.md),
2026-10-26. Existing recordings, saved references and decoded assets only;
no simulator, test drive or new rig access.

**The width-aware evidence argues against treating `racepanicmin` as the
physical outer wall.** Its earlier close match was to recorded car-reference
positions. Even an alignment optimized specifically to favor wall containment
cannot enclose the retained clean positions and a plausible footprint at the
existing metre scale. This is a conditional geometric exclusion, not a recovered
collision-mesh identity or an exact calibrated wall location.

## What changed in the evidence

The original comparison used one rigid fit from clean driving paths. It found
`racepanicmin` closest to the full 1,077-station outer envelope, with a 0.54 m
median absolute separation. That result remains valid as a position-envelope
comparison; it does not include body width.

This follow-up adds:

- Nine existing source IBTs, matching the track version, with 222,718 raw GPS
  positions meeting the original moving/on-track/off-pit/slip filters.
- A stricter subset of 169,096 positions from 113 complete observed laps:
  incident counter unchanged and absolute lateral/longitudinal acceleration
  below 28 m/s² throughout each lap, in addition to the original filters.
  This reduces dependence on possible crash or contact excursions; it does not
  certify undamaged cars or absence of 0x touches.
- Independent saved pit exits to discriminate orientation.
- A whole-loop, width-aware containment test over rotation and translation,
  rather than another nearest-line median fit.

The 1,077-station profile retains extrema from 38 recordings but not the
winning source/frame/yaw at each station. Only nine of those raw source files
were recovered in the local search. Consequently an exact per-station,
body-oriented correction of the entire historical profile is unavailable.
The raw-position check is separate from the rounded/binned reconstruction.

## Width: known assumption and missing calibration

The existing survey tools use **0.97 m half-width**, equivalent to 1.94 m full
width (`testdriver/survey.py`, `surveybuild.py`, `tools/wallgeo.py`). This is a
survey assumption, not an independently measured Supra collision body. The
1.56 m nominal wheel track in the banking study is not body width.

A complete planar box correction would use support
`h(n) = a*abs(n·forward) + b*abs(n·left)`, where `a` is half-length, `b` is
half-width and `n` the wall normal. Roll/pitch require projected body axes;
the SDK reference-to-body-centre offset also matters. Unknown overhangs mean
that simply moving every point sideways by 0.97 m is not an exact correction.

The test therefore uses hypothetical **minimum footprint radii** of
0.75–1.05 m around the recorded reference point. If the actual projected
body contains such a disk, a wall must contain the disk at every retained
position, regardless of yaw. This is conservative for that footprint
assumption, but neither the radius nor the SDK-to-body offset is verified.

Forty of the 88 old acceleration candidates were re-identified in three
accessible recordings and examined before the threshold. They form 30
descriptive episodes; only two episodes have a nearby positive incident-counter
change. These do not identify the struck object, and the incident-correlated
events do not provide confirmed contacts on opposing sides. They were not
treated as wall-contact equalities or used to invent a measured width.

## Better orientation evidence without using the boundary

Twenty saved pit exits supply 1,514 moving on-pit points from seven recordings.
None overlaps the three recordings used in the earlier clean-path registration.
Every exit favors the original orientation over the 180-degree alternative.

| Frozen race alignment | Pit0 position distance, median / p95 | Heading error, median / p95 |
|---|---:|---:|
| Original orientation | 6.31 / 7.94 m | 3.64 / 9.46° |
| 180-degree alternative | 26.21 / 31.34 m | 21.41 / 31.21° |

`pit0center` and the racecenter-based transforms give the same preference.
The remaining 6–8 m route/lane offsets mean this resolves orientation, not
metre-level wall translation. No pit-specific translation was fitted.

## Give the wall hypothesis its best possible alignment

Take the convex hull of the observed positions and the convex hull of the
continuous `racepanicmin` curve. Replacing a proposed wall with its convex hull
enlarges its enclosed region, deliberately favoring wall containment. If even
this relaxation cannot contain a footprint, the original enclosed curve cannot.

For each rotation, let `n_j` be outward unit normals of the candidate hull,
`b_j` their supporting-plane bounds and `s_j` the rotated observation hull's
support. Optimize translation `T` and common signed clearance `h`:

```text
maximize h
subject to -n_j·T + h <= b_j - s_j, for every candidate hull face j
```

Positive `h` is the largest common isotropic footprint radius that fits.
Negative `h` is a signed containment deficit, not a physical negative radius.
The test searches all proper rotations over 0–360 degrees and translations;
there is no reflection, scale fitting or separate adjustment by track sector.
Thus an unchosen translation cannot explain away the result.

At each angle a three-variable linear program supplies a feasible lower value
and a nonnegative-dual-multiplier numerical upper certificate. An angular
Lipschitz bound covers unsampled rotations to 0.02 m. The explicit 1,000 m
variable box covers every competitive placement, including negative clearances.
Every position set has a demonstrated feasible value above −10 m. For any
contender with `h >= -10`, averaging the centered observations places **−T**
inside the candidate's supporting half-planes expanded by 10 m. Four additional
linear programs bound both translation coordinates by 224.21 m in absolute
value. The candidate's largest inscribed disk bounds `h` above by 114.42 m.
Thus the box excludes no contender capable of improving the reported results.

The continuous Hermite curve is also covered: at 100 samples per prepared
segment its chord error is at most 0.0000853 m, added to the reported upper
bound. This is numerical certification with stated error allowances, not a
formal interval-arithmetic proof. Independent LP recomputation agreed within
7.1e-15 m; the conclusion is not sensitive to that numerical difference.

| Position set | Best sampled signed clearance | Upper allowance over all rotations, including curve error |
|---|---:|---:|
| Full saved 1,077-station envelope | −0.892 m | −0.873 m |
| Nine raw files, original filters | −0.411 m | −0.393 m |
| Nine raw files, strict complete-lap subset | −0.129 m | **−0.110 m** |

At fixed metre scale, even the strict raw reference points cannot all fit inside
the proposed wall. Adding a 0.75 m minimum footprint requires at least 0.860 m
more clearance than the strict-set upper bound permits; a nominal 0.97 m
footprint requires at least 1.080 m more. These are best-placement containment
deficits, not observed vehicle penetrations or calibrated per-lap clearances.

The strict-set best placement changes orientation by about −0.137° relative
to the SDK-angle prior. It is a wall-favoring adversarial alignment, not a new
independently calibrated native-to-GPS transform. No fitted score is presented
as held-out proof that a contact occurred.

## Scale sensitivity and what remains unresolved

A limited GPS-displacement/SDK-speed diagnostic found a ratio near 1.0027.
Its one-second result has only four windows from one recording. It is not an
independent RL-to-GPS metre calibration. An area-only contradiction for the
original nine-file hull reverses under a change of that size, so area alone
must not be used to reject the wall hypothesis.

The stronger shape/containment result was tested separately:

| Diagnostic change to GPS linear scale | Strict-set upper possible footprint clearance |
|---|---:|
| −0.50% | about 0.469 m |
| −0.2685%, illustrative inverse of the observed ratio | about 0.201 m |
| No change | −0.110 m |
| +0.50% | about −0.836 m |

None accommodates even the tested 0.75 m minimum radius. Approximately 0.77%
GPS shrink permits a demonstrated 0.75 m fit in the convex relaxation; about
0.96% permits 0.97 m. Those are diagnostic changes, **not adopted corrections**;
actual nonconvex-curve/body containment could require more. A one-percent scale
change can materially change the conclusion and is not independently excluded
for this IRP asset by these data alone.

The defensible conclusion is stronger than the original overlay: **do not use
`racepanicmin` as a calibrated outer collision wall.** Under fixed-scale rigid
registration and the tested footprint assumptions, it is incompatible with the
recorded clean positions. Its close position-envelope correspondence is more
consistent with an authored car-position/planning constraint. Actual physical
identity remains unproven until collision-body/reference-point geometry and
independent IRP native/GPS scale or an absolute geometric anchor are established.

## Reproduction and provenance

All private outputs are under `J:/Codex/irp-wall-width-20261026/`:

- `global-containment-v2.py` / `.json`: numerical dual certificates, angular
  bounds, source hashes and all position-set results.
- `scale-sensitivity-v2.py` / `.json`: diagnostic scale changes; v1 results are
  retained separately and are not overwritten.
- `worker-contact/`: raw source hashes/hulls, original and strict filters,
  contact reconstruction and GPS/SDK diagnostic. Use `contact-evidence-v2.json`
  and `gps-scale-diagnostic-v2.json`; the first drafts were incomplete/empty.
- `worker-pit/`: independent pit-exit check/source-overlap audit and
  `analytic-containment-audit.json`, including the continuous-cubic bound and
  independent linear-program verification.
- `IRP-width-aware-wall-test.png` / `.svg`: best-placement overlay, opposing
  side examples and scale sensitivity. `solver-validation.json` checks known
  synthetic geometries with positive, zero and negative signed clearance.
- `translation-box-audit.json`: bounds covering all competitive negative as
  well as nonnegative clearances, independent of rotation.

Source executable and route-parser provenance remain those in the parent
semantics document. No raw recordings, private geometry or plotting dependencies
are committed. The reusable package is unchanged, and no driving admission is
created by this analysis.
