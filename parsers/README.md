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

The installed package has no runtime dependencies and needs neither a Coach
checkout, internet access, simulator, nor device. Console commands `coach-route`,
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
acquisition is unverified.

Route XY is local native geometry with no verified world/GPS transform or
LapDist phase. RL's third component is a vertical query seed; RLC planning
heights and envelope fields do not establish physical ground or collision
walls. Legal track limits, official pit lines and driving admission remain
unavailable. A plotted route is not permission for iDriver to drive it.

Only synthetic, de-identified tests are committed in
`iracing-coach/tests/test_native_route*.py`; `tools/check.ps1` runs them with the
backend suite. No installed track assets, private geometry, research captures,
setups or decoding material belong in Git.

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
