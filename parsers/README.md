# Coach file parsers

iRacing Coach owns reusable file parsing. `iracing-coach-parsers` provides the
offline track readers; the existing Python worker owns IBT/session analysis and
the authoritative [STO codec](../iracing-coach/skills/analyze-iracing-race/scripts/sto_codec.py)
and [garage descriptors](../iracing-coach/skills/analyze-iracing-race/scripts/sto_garage.py).
STO editing uses the [versioned server API](../docs/08-rewrite/contracts/sto-editor.md).
Private decoding material is provisioned outside Git.

## Install and use

With Python 3.12 or newer, from a Coach checkout:

```powershell
python -m pip install ./parsers
python -m coach_parsers.native_route <decoded-copy.rl> --sample
python -m coach_parsers.native_route_cache <decoded-copy.rlc> --records
python -m coach_parsers.native_route_bundle <decoded-copy.rl> --output <new-folder>
```

The offline readers have no runtime dependencies and need neither a Coach
checkout, internet access, simulator, nor device. The optional process reader
requires an explicitly selected running simulator on 64-bit Windows. Console commands `coach-route`,
`coach-route-cache` and `coach-route-bundle` expose the same entry points.
Consumers pin an immutable Coach commit when installing from Git; a wheel built
from that commit can be installed offline with `pip install --no-index <wheel>`.

The Python APIs are `coach_parsers.native_route.parse(bytes) -> Route`,
`coach_parsers.native_route_cache.parse(bytes) -> Cache`, and
`coach_parsers.native_route_bundle.build(paths, steps=50, cleanup=False,
input_evidence=None) -> dict`. Schema 1 summaries retain source hashes, unknown
header/flag information and evidence limitations. Invalid framing, unsupported
versions, nonfinite values, checksums and allocation budgets fail explicitly.
Bundle output requires a new directory and never replaces an existing source.

## Scope

Inputs must already be decoded offline copies: RL versions 1/2 and RLC version 5.
This package does not extract installed DAT archives. The private extraction
adapter and its evidence are outside this repository. Full installed-DAT
acquisition is not a public package capability. The bounded private archive
study and its explicit framing exceptions are recorded in
[TELEMETRY-REGISTRATION.md](TELEMETRY-REGISTRATION.md); inventorying every
current sim-PC track remains open.

Standalone RL/RLC decoding does not supply a qualified world/GPS transform or
LapDist phase. The separate [registration study](TELEMETRY-REGISTRATION.md)
measures correspondence on six layouts, with fresh-file confirmation on four;
those private fits are not automatically applied by the parser or app.
RL's third component is a vertical query seed; RLC planning
heights and envelope fields do not establish physical ground or collision
walls. Legal track limits, official pit lines and driving admission remain
unavailable. A plotted route is not permission for iDriver to drive it.

Only synthetic, de-identified tests are committed in
`iracing-coach/tests/test_native_route*.py`; `tools/check.ps1` runs them with the
backend suite. No installed track assets, private geometry, research captures,
setups or decoding material belong in Git.

## Meaning of the track traces

[TELEMETRY-REGISTRATION.md](TELEMETRY-REGISTRATION.md) records the expanded
2026-10-27 corpus and position correlation: 1,348 decoded curves, 178 caches,
six compared layouts and fresh-recording station checks on four layouts.
`coach_parsers.telemetry_registration` provides explicit spherical/WGS84 local
conversion, rigid frames, cyclic chord projection and circular statistics.
These helpers do not fit or qualify a transform; private maps stay outside Git.

[NATIVE-ROUTE-SEMANTICS.md](NATIVE-ROUTE-SEMANTICS.md) records the recovered
native consumers, Martinsville and IRP measurements, comparison with old IRP
impact candidates, and the repeatable extraction/validation process. Min/max
bound a padded sampling grid; they are not verified collision walls. Center is
separately authored, not the arithmetic midpoint or a proven fastest line.

## Build-pinned native acquisition (1.1.0)

`coach_parsers.native_process.open_native_process(pid)` is a context-managed,
read-only Windows reader. It binds the PID, executable path and creation time to
an opened handle, verifies the pinned executable SHA-256 and image size, and
revalidates identity before exposing memory reads. Unsupported builds and access
denials fail explicitly. It never injects, writes process memory, changes
privileges or sends simulator inputs. No process is opened at import time.

`native_snapshot.decode_snapshot(reader.read, reader.image_base,
reader.executable_sha256)` decodes processed race and available pit routes,
including checkpoint stations and interpolation coefficients. It also retains
the native geographic origin and axis rotation. `decode_player_pose` takes the
same arguments and separately reads the selected player position, rotation and
station. All reads are bounded; unknown geometry and physical limits remain
unknown. Inputs and outputs belong outside Git.

This layout is statically verified for one executable hash. Runtime acquisition,
moving-pose coherence and performance are **unqualified**. Tick bracketing does
not establish an atomic snapshot. These APIs do not grant driving admission or
establish surface edges, walls, pit speed boundaries or a fastest line. The
standalone offline RL/RLC readers retain their earlier coordinate/phase limits.
The native reader has no dependency on iDriver, the worker, a service or an AI.

Synthetic tests are part of the normal backend suite in
`iracing-coach/tests/test_native_snapshot.py` and `test_native_process.py`,
including wrong-build refusal, pointer and allocation limits, adjacent-field
sentinels, process identity changes, exact reads and handle cleanup.

## Provenance

These three implementations and their synthetic tests were transferred from
iDriver (formerly `iRacing-utility`) at commit
`b19952b631169507271286153ebfe05a9044717e` on 2026-10-24. The original history
remains intact in that private repository; no private repository history was
merged into this public one. Implementation files preserve their original bytes,
including historical `lab-notes/` references in source docstrings, so existing
bundle reader hashes still match. This README supplies the current public scope
and provenance. Test imports changed; iDriver retains module-name adapters.

Release wheels are built from a clean committed archive, and every packaged
Python module is compared byte-for-byte with that commit before publication.
Version 1.0.1 corrects stale build output in the first 1.0.0 wheel; calculations
are unchanged and original reader-source hashes are preserved in 1.0.1.

Original Git blob IDs, for checking the transfer against the retained history:

| Source path | Blob |
|---|---|
| `testdriver/native_route.py` | `c1433bdbd83910e8cee05e670ac44bb3c1af7725` |
| `testdriver/native_route_bundle.py` | `1af79f9e5eed78a2806232f3898de1bda0906092` |
| `testdriver/native_route_cache.py` | `e892130f562924188efdc1205a20f6623eff64e5` |
| `testdriver/tests/test_native_route.py` | `3525d599d7bf052efc1f4b205df38e0e9bf80006` |
| `testdriver/tests/test_native_route_bundle.py` | `026c7ac2aed5a52b7bce3f1764e1abe83db81c80` |
| `testdriver/tests/test_native_route_cache.py` | `872706fda800da7670d74cf8c2bacd7ee564c310` |
