# Tire and fuel telemetry semantics

Measured against real recordings, not inferred. Source:
`trucks toyotatundra2022_richmond 2025 2026-08-16 14-24-19.ibt` (60 Hz, 121,949
samples, 58 laps, three pit services), cross-checked against the other Richmond
and Portland recordings.

This file exists because two of these facts are counter-intuitive and one of
them will silently corrupt coaching output if it is assumed rather than checked.

## Update cadence differs by channel family

| Family | Channels | Behaviour in a 121,949-sample race |
| --- | --- | --- |
| Tread remaining | `LFwearL/M/R`, `RFwearL/M/R`, `LRwearL/M/R`, `RRwearL/M/R` | **3 distinct values, 2 changes** - discrete |
| Tire temperature | `LFtempL/M/R`, `LFtempCL/CM/CR`, and the other three corners | 107,568 distinct values, 113,958 changes - continuous |
| Fuel level | `FuelLevel`, `FuelLevelPct` | 121,745 distinct values - continuous |

Tread is **not** a continuous signal. It updates only at pit service. Any
attempt to plot wear against lap distance, differentiate it, or treat it as a
per-lap series is reading a step function that has at most one step per stop.

`tire_energy.py` states this in its own docstring - "real wear arrives only as a
discrete reading after pit service" - and it is correct. The continuous
energy proxy plus discrete calibration readings is the right architecture, and
the discrete readings are the dependent variable a wear model would need.

## Which stint a tread reading describes

The trap. Measured transitions against `OnPitRoad`:

```
pit segments : laps 0 -> 0, 22 -> 23, 52 -> 53
RFwearM      : 0.9740  --(lap 23 service)-->  0.9465  --(lap 53 service)-->  0.8445
```

The value changes *during* service, and it reports **the tires that just came
off**. So:

- The reading taken at the service that ends stint N describes stint N's tires.
- The value visible *during* stint N describes stint N-1's tires.
- **The final stint has no reading**, because no service follows it.

A panel that carries the last known value forward will therefore attribute the
previous stint's wear to the final stint. Report the final stint as not
measured. This is also why Garage61's equivalent panel is headed "at end of
run" - the same constraint applies to any consumer of this data.

Wear *during* a stint additionally requires the tread at the stint's start,
which is only known when tires were changed at the preceding service. Emit the
measured reading and the service context; derive per-stint wear only when both
ends are known, and label it when they are not.

Note the pre-race value is not necessarily 1.0 (this recording starts at
`LFwearM` 0.9899, `RFwearM` 0.9740) because the fitted set may be scrubbed. Do
not assume a fresh set reads full.

## What this supports

- **Per-run tire snapshot** - four tires by three bands, tread and temperature,
  one snapshot per completed service. Directly reproduces the Garage61 layout.
- **Continuous temperature history** - temps are per-sample, so temperature over
  a stint is available and is strictly richer than a per-run snapshot.
- **Continuous fuel history** - per-sample level, and per-lap consumption is
  already computed (`lap["fuel"]["used_l"]`).

## Channel coverage

The recording exposes 274 channels. `workflow.ANALYSIS_CHANNELS` requests 415,
of which 167 are present here - the request list is a superset spanning cars.
**107 channels present in the recording are never requested at all**, and the 12
tread channels, while requested and decoded, had no reader anywhere in the
backend as of this writing.
