# Port phase 2: per-sample analysis kernels (plan)

Date 2026-10-03. Status: plan; no kernel code exists yet. Authority: owner decision D2 and its ruling (port the stages the profile shows dominate, each gated on byte-identical output; an unexplained difference is a failure), `architecture.md` section 12 (phase 2), `contracts/worker-interface.md` sections 7 and 8. Builds on `phase1-decode.md` (the decode stage and the coach column store) and `numerics.md` (CPython-3.12-exact primitives in `crates/coach-numerics`). Review: Codex Sol 6.1 reviews this plan and the contract change in section 4.7 before the integration code lands (D15).

What phase 2 delivers: every function of `analysis_engine.analyze_telemetry` that reads per-sample columns runs in Rust, behind the existing `IRACING_COACH_NATIVE_STAGES` seam, with `analysis.json` byte-identical and `analysis_id` unchanged; Python keeps the orchestration and everything that reads only laps, runs and the analysis dict. Five units (section 2) port in parallel after a half-day skeleton; each is tested alone against vectors recorded from the oracle (section 3); one integration step composes them (section 4).

## 1. Where the time and memory go

### Method

- Driver (scratch, not committed): decode with the worker's own `workflow._load_analysis_telemetry([path], None)` (Python decode, native rate, as the server runs it), then measure `analysis_engine.analyze_telemetry` alone with automatic GC paused, as `workflow._automatic_gc_paused` does. Modes: plain (3 runs, `time.perf_counter`), cProfile (one run), tracemalloc (one run, started after decode, so the decode lists are the base; one frame; every function below wrapped to record the traced peak above its entry and the bytes still allocated when it returns; a snapshot at the last call of the analysis, the second `build_technical_insights`, when every cached view is still alive).
- DESKTOP-JOSHUA (i5-11400, Windows 11), bundled CPython 3.12.14; CPU load from other work 26 % before and 30 % after the race runs. cProfile adds overhead (race 11.44 s profiled against 7.0 to 7.7 s plain); shares are of the profiled time and rank, they do not measure.
- Recordings (scratch copies, never committed):

| Name | Recording | Bytes | Samples | Session | Plain analyze / Python decode |
|---|---|---|---|---|---|
| race | `582b11e2` New Hampshire, Supra, 2026-08-01 15:09:02 | 203,246,134 | 189,857, 167 channels, 80 laps, 4 runs | Race | 7.71, 7.14, 7.00 s / 1.78 s |
| short | `527f9457` New Hampshire, Supra, 2026-08-01 15:03:46 | 9,916,548 | 9,182, 5 laps | Lone Qualify | 0.445, 0.434, 0.436 s / 0.09 s |
| practice (supplementary, cProfile only) | `c4ebf025` New Hampshire, Supra, 2026-08-01 13:44:55 | 65,654,141 | 61,281 | Practice | 4.97 s profiled |

The practice session was added because on the race no lap passes the clean-lap screen (recorded repairs exclude every candidate lap: 68 laps carry a `damage_repair_context` reason, 50 close traffic), so the section and groove paths do almost nothing there. The race's `analysis.json` is 27.0 MB compact; `lap_traces` is 25.4 MB of it (94 %).

### Call graph and shares

`analyze_telemetry` calls, in order, the functions below (a row starting with `.` is called inside the nearest row above it without one, and its time is included in that row's). Time is cProfile cumulative time as a share of `analyze_telemetry`; allocation is tracemalloc on the race: the peak above the function's entry and the bytes it left allocated on return (a cached view stays allocated; a nested function's bytes are included in its caller's). Unit is the section 2 owner; Py stays Python.

| Function (module) | Unit | Race s | Race % | Short % | Practice % | Race alloc peak / retained MB |
|---|---|---|---|---|---|---|
| `TelemetryTable(telemetry)` | U1 | 0.09 | 0.8 | 0.7 | 0.6 | 253.7 / 253.7 |
| `_identity` (session info; 4 whole-column medians) | Py | 0.09 | 0.8 | 2.5 | 0.8 | 7.7 / 0.0 |
| `_find_race_session` | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_steering_basis` (`steering_basis.resolve_steering_basis`, `_lock_changed` over `SteeringWheelAngleMax`) | Py | 0.13 | 1.1 | 0.7 | 0.8 | 3.3 / 1.6 |
| `_lap_summaries` | U2 | 2.27 | 19.8 | 13.0 | 14.2 | 205.9 / 153.4 |
| . `_dynamics_samples` | U2 | 0.36 | 3.1 | 2.0 | 2.0 | 6.5 / 6.5 |
| . `_vehicle_dynamics_summary` (x80) | U2 | 0.46 | 4.0 | 2.7 | 3.0 | 20.0 / 19.9 |
| . `_lap_conditions` (x80) | U2 | 0.19 | 1.7 | 1.2 | 1.4 | 30.4 / 30.2 |
| `_runs` | Py | 0.06 | 0.5 | 0.7 | 0.8 | 5.3 / 3.3 |
| . `_service_events` | U2 | 0.05 | 0.5 | 0.2 | 0.6 | 5.3 / 3.3 |
| `_annotate_tire_set_lifecycle` | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_damage_repair_summary` | Py | 0.36 | 3.1 | 1.2 | 3.7 | 30.5 / 17.4 |
| . `_disrupted_sample_indices` | U2 | 0.10 | 0.8 | 0.5 | 0.6 | 12.0 / 5.5 |
| `_setup_channel_series` | U1 | 0.67 | 5.8 | 3.2 | 3.5 | 293.3 / 288.5 |
| `_setup_telemetry` | U1 | 0.74 | 6.5 | 4.8 | 4.3 | 9.6 / 0.0 |
| `_track_profile` | U3 | 1.12 | 9.7 | 13.9 | 7.2 | 115.6 / 5.9 |
| `_lap_trace_payload` | U3 | 4.06 | 35.5 | 22.0 | 28.3 | 457.2 / 102.2 |
| . `_additional_trace_sources` | U3 | 1.33 | 11.6 | 6.5 | 7.5 | 366.4 / 366.4 |
| `analyze_groove_evolution` (groove_analysis) | U5 | 0.00 | 0.0 | 9.7 | 11.6 | 0 / 0 |
| `_corner_tire_age_summary` | U4 | 0.06 | 0.5 | 7.2 | 4.5 | 9.3 / 0.1 |
| . `_zone_lap_metric` (race 0, short 4, practice 14 calls) | U4 | 0.00 | 0.0 | 6.1 | 4.0 | 0 / 0 |
| `_scheduled_race_laps`, `_duration_minutes`, race sums | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_analysis_fingerprint` | Py | 0.00 | 0.0 | 0.5 | 0.1 | 1.2 / 0.0 |
| `build_track_geometry` (race_foundations) | U5 | 0.87 | 7.6 | 7.0 | 5.5 | 75.7 / 0.4 |
| `build_race_replay` (race_foundations; no `CarIdx*` channel in these recordings) | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `build_tire_learning` | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_conditions_summary` | U1 | 0.20 | 1.8 | 0.9 | 1.1 | 11.7 / 6.2 |
| `_driver_adjustments_summary` | U1 | 0.10 | 0.9 | 0.5 | 0.5 | 11.7 / 6.5 |
| `_coaching_signals`, `_strategy` | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_pace_attribution` | U4 | 0.00 | 0.0 | 7.0 | 5.9 | 0 / 0 |
| . `lap_reference.build_lap_trace` | U4 | 0.00 | 0.0 | 3.0 | 2.0 | 0 / 0 |
| . `tire_energy.segment_energy` | U4 | 0.00 | 0.0 | 4.7 | 4.1 | 0 / 0 |
| . `time_loss.analyze_time_loss` | U4 | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_field_pace` (`competitor_pace`; no `CarIdxLapDistPct` in these recordings) | Py | 0.00 | 0.0 | 0.0 | 0.0 | 0 / 0 |
| `_strategy_planning` | Py | 0.00 | 0.0 | 2.7 | 3.1 | 0 / 0 |
| . `_clean_lap_traces` | U4 | 0.00 | 0.0 | 2.3 | 1.8 | 0 / 0 |
| . `pit_loss.measure_pit_loss` | U4 | 0.00 | 0.0 | 0.4 | 1.3 | 0 / 0 |
| `build_technical_insights` (x2), `available_catalog`, `_race_grades`, `_race_evidence_timeline`, `_track_usage_context`, `investigation_lap_exclusions` | Py | 0.01 | 0.1 | 0.1 | 0.1 | 0 / 0.1 |
| `build_race_investigation` (race_investigation, race_inspection) | Py | 0.46 | 4.0 | 0.1 | 0.0 | 0.4 / 0.4 |

Shared helpers, counted above in whichever function first needs them (cached column views are built on first use):

| Helper | Unit | Calls (race) | Race % | Short % | Practice % |
|---|---|---|---|---|---|
| `TelemetryTable._cached` (building every cached view) | U1 | 2,138 | 13.2 | 7.6 | 8.2 |
| `_normalize_column` | U1 | 157 | 7.7 | 4.0 | 4.4 |
| `_apply_steps` | U1 | 98 | 6.9 | 3.8 | 4.2 |
| `_fraction_column` | U1 | 8 | 5.0 | 2.8 | 3.3 |
| `_row_means` | U1 | 13 | 3.0 | 1.9 | 2.1 |
| `_dt_series` | U1 | 3 | 1.3 | 2.4 | 2.9 |
| `_caution_flag_series` | U1 | 6 | 0.2 | 3.0 | 0.2 |

Self time on the race (cProfile, 11.44 s profiled): `analysis_engine.py` 6.43 s, C builtins 3.96 s (`sorted` 99,660 calls, `max` 1.27 million, `round` 1.08 million, `abs` 4.6 million), `race_foundations.py` 0.59 s, `race_investigation.py` 0.25 s.

Share by unit (entry points' cumulative time, each nested call counted once, in its caller; views count in the unit that first builds them, so U1's own row understates it):

| | Race % | Short % | Practice % |
|---|---|---|---|
| U1 table (entry points only; plus the views above) | 15.0 | 9.4 | 9.4 |
| U2 laps | 21.1 | 13.7 | 15.4 |
| U3 traces | 45.2 | 35.9 | 35.5 |
| U4 sections | 0.5 | 16.9 | 13.5 |
| U5 field | 7.6 | 16.7 | 17.1 |
| stays Python | 10.6 | 7.4 | 9.1 |

### Memory (race)

Traced allocation above the decode lists (850 MB in P1) reaches 839.9 MB, at the end of the analysis with every cached view alive, and is 605.0 MB after it returns. By allocation site, live at the last call:

| Site | MB | What |
|---|---|---|
| `TelemetryTable.__init__`, `list(values)` (line 666) | 253.7 | a pointer copy of each of 167 decoded columns |
| `_normalize_column`, `list(map(float, values))` (257) | 237.9 | finite views |
| `_apply_steps` (360, 372, 363) | 210.2 | converted views: setup series, unit conversions, trace derivations |
| `_lap_trace_payload`, per-bin optional-signal values (2420) | 38.1 | 160 bins x 80 laps x up to 51 signals |
| `_row_means` (400) | 30.9 | tire temperature means |
| `_wheel_ratio_column` (488, 490) | 22.9 | four wheel ratios |
| `_fraction_column` (310) | 13.0 | eight fraction views |
| `_bool_column` (319) | 8.1 | five boolean views |

The table holds 31.7 million decoded values (189,857 x 167); the analysis builds 130 cached views (111 finite, 8 fraction, 5 boolean, 4 wheel ratio, 1 flag bits, 1 road-wheel steering) with 24.7 million elements and reads 138 channels. Every per-sample element is a boxed Python value (24-byte float or a shared small int) behind an 8-byte list slot. Together with the decode lists this is the 1.9 GiB traced, 2.3 GiB working set P1 measured.

## 2. Units

### Rules

1. **A unit ports the oracle's entry points that read per-sample columns.** Code that reads only laps, runs, load zones or the analysis dict stays Python in phase 2 (10.6 % of the race, mostly `build_race_investigation`, which walks the 160-bin traces, and the orchestration of `_damage_repair_summary`). It runs unchanged on the native results.
2. **An entry point is a module-level function the oracle looks up at call time** (`analyze_telemetry` and its callers reach every one through a module global or a module attribute such as `pit_loss.measure_pit_loss`). The seam rebinds it for the duration of one analysis without changing a file of the analyzer bundle (section 4.4). Its native replacement takes the same arguments (the table by reference, Python values through the codec) and returns the same Python objects: types, float bits, dict insertion order.
3. **No shared mutable state.** Each native entry is a pure function of the table and its arguments. The table's view cache is memoisation, and the record of accessed channels is a set union (the analysis emits it sorted), so neither couples two entries. The oracle mutates arguments in two places: `_annotate_tire_set_lifecycle` adds `tire_set_lifecycle` to runs, and `_damage_repair_summary` adds `damage_repair_context` to laps and runs and `coaching_reference_lap_numbers` to runs. Both stay Python (their per-sample helpers are ported), so no native code reproduces a mutation or an alias (`damage_repair.run_impacts[k]` is the same object as `runs[k]["damage_repair_context"]`).
4. **Every unit is tested alone.** Its vectors hold the arguments exactly as the oracle passed them (laps, runs, load zones and setup series included), so no unit needs another unit's Rust to run its tests. The only shared code is the skeleton (section 6, I0) and the table's milestone M1 (U1), whose API is fixed below.

| Unit | Title | Entry points (rebound) | Python lines ported (approx.) |
|---|---|---|---|
| U1 | Table, normalisation and whole-column summaries | `TelemetryTable` as the Rust `Table` every unit reads (M1; in Python `StoreTable`, section 4.3), `_setup_channel_series`, `_setup_telemetry`, `_conditions_summary`, `_driver_adjustments_summary`, `_caution_flag_series`, `_dt_series`; the merged store for multi-file groups | 1,250 |
| U2 | Lap segmentation, summaries and disruption scans | `_lap_summaries`, `_service_events`, `_disrupted_sample_indices` | 800 |
| U3 | Trace payload, downsampling and track profile | `_lap_trace_payload`, `_track_profile` | 790 |
| U4 | Sections and clean-lap screening | `_corner_tire_age_summary`, `_pace_attribution`, `_clean_lap_traces`, `pit_loss.measure_pit_loss` | 2,300 |
| U5 | Field geometry and groove | `analyze_groove_evolution`, `build_track_geometry` | 1,700 |

Stays Python in phase 2: the body of `analyze_telemetry`, `_identity`, `_find_race_session`, `_steering_basis`, `_runs` (with `_tire_observation`, `_wear_reading_changed`, `_pace_slope_by_green_exposure`), `_annotate_tire_set_lifecycle`, `_damage_repair_summary` (with `_increase_indices`), `_scheduled_race_laps`, `_analysis_fingerprint` (with `_fallback_telemetry_digest`), `build_race_replay`, `build_tire_learning`, `_coaching_signals`, `_strategy`, `_field_pace`, `_strategy_planning` (with `strategy_model`), `build_technical_insights`, `available_catalog`, `_race_grades`, `_race_evidence_timeline`, `_track_usage_context`, `build_race_investigation`, `investigation_lap_exclusions`, `_screened_clean_laps`. `build_race_replay` and `_field_pace` read `CarIdx*` arrays, which none of the recordings profiled here carries (the race decodes 167 of the 415 analysis channels, none `CarIdx*`), so their per-sample paths cannot be parity-tested on them and they read no sample; if the corpus vectors show a recording with `CarIdx*` channels, both move to U5.

Their per-sample reads after phase 2 go to the store-backed table (section 4.3): `_identity` reads `AirTemp`, `TrackTempCrew`/`TrackTemp`, `TrackWetness`, `TrackUsage` whole (four transient lists); `_steering_basis` builds one finite view (`SteeringWheelAngleMax`); `_runs`, `_tire_observation` and `_damage_repair_summary` read single samples and, in damage, build the finite views of the incident and fast-repair counters and the boolean and flag views of the pit and repair channels; `_scheduled_race_laps` scans `SessionLapsTotal` backwards; `_fallback_telemetry_digest` (only when no SHA-256 is known, never in the worker) materialises every column.

### Shared conventions

- `_finite(v)` on a dict value (`lap.get("lap")`, a zone's `start_pct`): `float` and `int` and `bool` to `f64` and finite or `None`, `str` through `parse_float`, anything else `None`. One helper in the skeleton (`PyValue::finite`), used by every unit.
- Truthiness (`if lap.get("complete")`, `x or 0.0`) is Python's: `0`, `0.0`, `-0.0`, `""`, `None`, empty containers are false. `x or 0.0` maps `-0.0` to `0.0`.
- `_round(v, d)` is `round_ndigits(_finite(v), d)` and `None` for a non-finite or missing `v`; `round(x, d)` of a float is `round_ndigits`; `int(round(x))` is `round_to_int`; `int(x)` of a float is `int_from_float` (truncation).
- `_mean` is `fmean` of `_finite_values` (`fsum / len`); `_median` and `statistics.median` are `sorted_floats` then the middle or `(a + b) / 2`; `_percentile` and `_percentile_of_sorted` are a sort, `floor`, `ceil` and `lower * (1.0 - f) + upper * f`.
- `reduce(add, xs, 0.0)` and `+=` loops are `fold_add`; `sum(floats)` with no start is `sum_floats_from_int_zero` (Neumaier, and `Int(0)` when empty, which `_round` turns into `0.0`); `sum(bools)` is an int count; `sum(values, 0.0)` is `sum_floats`.
- `min`, `max`, `min(key=)`, `max(key=)`, `max(key=abs)` keep the first of equal items (`min_index_by`, `max_index_by`): this decides `-0.0` against `0.0` and `-x` against `x`.
- Output dicts are built in the oracle's key order, including keys added conditionally; a value's type is the oracle's (`int` lap numbers and indices, `bool` flags, `float` everything `_round` returns, `None`).

### U1. Table, normalisation and whole-column summaries

Inputs: the decode store (or the merged store, below) and the table metadata `TelemetryTable.__init__` derives (`metadata` with `sample_rate`, `tick_rate`, `channel_selection`, `sim_session_type`, `sim_session_num`; `session_info`; `variables`; `available_variables`; `channel_units`; `length`).

Milestone M1, the API every unit codes against (Rust, `crates/coach-analysis/src/kernels/table/`), lands first:

```rust
pub struct Table { /* raw columns at SDK width, presence for a merged store, meta, view cache, access record */ }
pub struct TableMeta { pub metadata: PyValue, pub session_info: PyValue, pub variables: PyValue,
                       pub available_variables: PyValue, pub channel_units: Vec<(String, String)>, pub length: usize }
pub enum ColumnKey { Channel(String), Default(PyValue) }          // _column_key: first present alias, else the default
pub enum Raw<'a> { F32(&'a [f32]), F64(&'a [f64]), I32(&'a [i32]), U32(&'a [u32]), Bool(&'a [u8]),
                   Text(TextColumn<'a>), Array(ArrayColumn<'a>), Repeat(PyValue) }  // + presence when merged
pub struct View { /* f64 values + presence bits */ }              // [_finite(v) for v in column], and `complete`
impl View { pub fn get(&self, i: usize) -> Option<f64>; pub fn len(&self) -> usize; pub fn complete(&self) -> bool; }
pub struct SteeringBasis { /* kind, status, ratio, ... from the codec's steering_basis.SteeringBasis */ }
impl SteeringBasis { pub fn road_wheel(&self) -> bool; pub fn threshold(&self, wheel_value: f64) -> f64; }
pub struct SetupSeries { /* the _setup_channel_series structure with its converted columns */ }

impl Table {
    pub fn from_store(store: ColumnStore, meta: TableMeta) -> Result<Table, KernelError>;
    pub fn len(&self) -> usize;
    pub fn meta(&self) -> &TableMeta;
    pub fn unit(&self, channel: Option<&str>) -> Option<&str>;
    // Recording accessors: each records exactly what its Python namesake adds to accessed_channels.
    pub fn has(&self, aliases: &[&str]) -> bool;
    pub fn get(&self, aliases: &[&str], default: PyValue) -> Raw<'_>;
    pub fn resolve(&self, aliases: &[&str], default: PyValue) -> (Option<&str>, Raw<'_>);
    pub fn normalized(&self, aliases: &[&str], default: PyValue) -> Rc<View>;
    pub fn fractions(&self, aliases: &[&str], default: PyValue) -> Rc<View>;
    pub fn booleans(&self, aliases: &[&str], default: PyValue) -> Rc<Vec<bool>>;
    pub fn flag_bits(&self, aliases: &[&str], default: PyValue) -> Rc<Vec<i64>>;
    // Non-recording helpers (_column_key, _finite_for, ...), cached by key like _cached.
    pub fn key(&self, aliases: &[&str], default: PyValue) -> ColumnKey;
    pub fn finite_for(&self, key: &ColumnKey) -> Rc<View>;
    pub fn fractions_for(&self, key: &ColumnKey) -> Rc<View>;
    pub fn booleans_for(&self, key: &ColumnKey) -> Rc<Vec<bool>>;
    pub fn flag_bits_for(&self, key: &ColumnKey) -> Rc<Vec<i64>>;
    pub fn wheel_ratios(&self, speed: &ColumnKey, wheel: &ColumnKey) -> Rc<View>;
    pub fn steering_column(&self, basis: &SteeringBasis) -> Rc<View>;   // _steering_column (records SteeringWheelAngle)
    pub fn take_accessed(&self) -> BTreeSet<String>;
}
pub fn caution_flag_series(table: &Table) -> Rc<Vec<i64>>;
pub fn dt_series(times: &View, fallback_hz: f64) -> Vec<f64>;
pub fn unit_steps(quantity: &str, unit: &str) -> Option<&'static [(Op, f64)]>;
pub fn apply_steps(values: &View, steps: &[(Op, f64)]) -> View;
pub fn row_means(columns: &[&View]) -> View;
```

Python entry points and helpers: `TelemetryTable` (`_column_key`, `_record_access`, `_raw_column`, `_cached`, `_finite_for`, `_fractions_for`, `_wheel_ratios`, `normalized`, `fractions`, `_booleans_for`, `_flag_bits_for`, `booleans`, `flag_bits`, `get`, `has`, `resolve`, `unit`), `_finite`, `_normalize_column`, `_finite_values`, `_finite_after_transform`, `_fraction_column`, `_bool_column` (`_bool`), `_binary_column`, `_distinct_map` with `_flag_bits`, `_apply_steps`, `_unit_steps`, `_apply_unit_steps`, `_normalized_unit`, `_row_means`, `_wheel_ratio_column`, `_steering_column`, `_dt_series`, `_caution_flag_series` (`_numeric_text`), `_setup_channel_series` (`_resolved_setup_series`, `_derived_mean_series`), `_setup_telemetry` (`_setup_sample_indices`, `_series_values`, `_series_metadata`, `_range_summary`, `_shock_summary`), `_conditions_summary` and `_driver_adjustments_summary` (`_channel_range`). Outputs: the `setup_telemetry`, `conditions`, `driver_adjustments` subtrees; `_setup_channel_series` returns a `SetupSeries` that only `_setup_telemetry` and `_track_profile` consume (in production a handle, section 4.3); `_caution_flag_series` and `_dt_series` return per-sample lists to Python callers (`_runs`, `_damage_repair_summary`, the groove channel map).

Merged store (multi-file groups, the `reconnect` worker case): the column merge of `workflow._load_analysis_telemetry` (references sorted by `(has_no_time, time, file_index, sample_index)`; a sample within 1e-9 s of the previous kept time fills only the previous row's `None` cells, first non-null wins) as `coach-analysis merge`, writing a store with stage `merge`: the decode layout plus a per-column `present` block (u8 per sample) and a provenance block (file index u16, record index u32 per sample). The metadata merge (representative session info by `json.dumps` length, variable catalogues, rates) stays Python. Contract change, section 4.7.

Primitives: `float()` widening (f32 to f64 exact), `parse_float` for text cells, `f64::is_finite`, `int_from_float` (flag bits `int(n or 0)`), plain IEEE `+ - * /` in `_apply_steps` order, `fsum` (row means), `sorted_floats`, `floor`, `ceil`, `round_ndigits`, `min_num`/`max_num`, `float_pow` for `10 ** -digits` in `_channel_range` (a C library `pow` call: platform family).

Ordering hazards:
- `_normalize_column`'s `isfinite(sum(converted, 0.0))` is only a shortcut: both paths give the same values and `complete` flag, so Rust computes the definition (non-finite to `None`, `complete` = no `None`). A text cell goes through Python `float()` (whitespace, underscores, `"inf"` and `"nan"` to `None`); `bool` to `0.0`/`1.0`.
- `_fraction_column`: divide by 100.0 only when `abs(n) > 1.5`; the clamps are `n if n < 1.0 else 1.0` then `n if n > 0.0 else 0.0`, so `-0.0` becomes `0.0` and exactly `1.0` stays the constant.
- `_bool_column`: `bool(v)`, so a NaN float is `True`; a text column uses `str.strip().lower() in {"1", "true", "yes", "on"}`.
- `_column_key` and `_record_access`: the first alias present in the table, else a default key; `has` records the first present alias and nothing else; `get` and `resolve` record. The access record must equal the oracle's per entry (vectors carry it).
- `_apply_steps` operation order (`n * 9.0 / 5.0 + 32.0` is `((n * 9.0) / 5.0) + 32.0`), no finite check after it; `_finite_after_transform` re-checks only where the oracle does.
- `_caution_flag_series`: the one-lap-to-green bit is cleared only outside a Race, per sample by `SessionNum` through the session-info map (`_numeric_text`: strip, first token, `float()`, `is_integer`).
- `_channel_range`: `changed` is `(max - min) > 10 ** (-digits)`, an int power with a negative exponent, which CPython evaluates as `pow(10.0, -digits)`.
- `_setup_telemetry` iterates `series["shocks"]` and `series["tires"]` in insertion order (TIRES order as built), `_range_summary` sorts once for the percentile and median.

### U2. Lap segmentation, summaries and disruption scans

Entry points: `_lap_summaries(table)` (with `_vehicle_dynamics_summary`, `_dynamics_samples`, `_lap_conditions`, `_count_corrections`, `_absolute_summary`, `_positive_increase`, `_ascending_taker`, `_racing_state`, `_on_track`, `_session_track_usage`), `_service_events(table)`, `_disrupted_sample_indices(table, timer_epsilon_s, *, mandatory_recorded, optional_recorded, session_flags_channel, fast_increment_indices)`. Inputs: the table and the steering basis (from the table, as `_steering_basis` caches it; the integration passes the Python-computed basis, section 4.3). Outputs: the list of lap dicts (before `_damage_repair_summary` adds its key), the service events (dicts, consumed by Python `_runs`), the disrupted sample indices (list of int).

Primitives: `fold_add` (every `reduce(add, compress(...), 0.0)`, the fuel-drop loop), int `|` fold for `observed_flags`, `sum_floats_from_int_zero` (the duration fallback `sum(lap_dts)`, `sum(compress(dts, abs_active))`), `fmean`, `sorted_floats` with `_percentile_of_sorted`/`_median_of_sorted`, `round_ndigits`, `round_to_int` (`int(round(sky))`), `int_from_float` (`int(lap)`, positions, `int(sample_rate * 2.5)`), `degrees` and `float_mod` (`degrees(wind) % 360.0`), `min_num`/`max_num`.

Ordering hazards:
- Laps are `groups` keyed by `int(lap_value)` for `lap_value >= 0` (lap 0 included), visited by `sorted(groups)`, a lap needing two samples; `_sdk_lap_time` reads `LapLastLapTime` at the last sample of lap N+1 and rejects a difference over 1.0 s.
- Each lap folds its own slice of the per-sample predicates in sample order; `(b * v) * dt` and `(s * v) * dt` are multiplied in that order; `lateral_exposure / 9.80665` after the fold.
- `running_time = max(caution + green, 1e-9)`; the classification compares `caution / running >= 0.5` first.
- `min(pct_values)`, `max(...)`, `min(speed_values)` keep the first of equal values (signed zeros).
- `vehicle_dynamics` keys: `wheel_speed` (only when a wheel-speed channel exists; tires in TIRES order filtered by `has`; `channels` sorted), then `motion` (YawRate, PitchRate, RollRate, then steering torque), `abs`, `brake_bias_setting`; `valid_ratio_samples` is an int; `abs.active_s` comes from builtin `sum` with an int start.
- `_absolute_summary` scales by the constant `180.0 / math.pi` computed once, after `abs`.
- `_service_events`: hits are stall, service active, or a fuel rise over 0.10 while on pit road; groups bridge `max(1, int(sample_rate * 2.5))` samples; `combined |= int(_finite(v) or 0)` over the flag window; `requested_service` in `PIT_SERVICE_BITS` order; `tires_changed_observed` sorted.
- `_disrupted_sample_indices`: terms in the oracle's order, a sample kept when any term holds or its index is in `fast_increment_indices` (a set argument).

### U3. Trace payload, downsampling and track profile

Entry points: `_lap_trace_payload(table, laps, *, maximum_bins=160, steering_basis)` (with `_additional_trace_sources`, `_present_values`, `_binary_column`, `_sideslip_column`), `_track_profile(table, bins=200, setup_series)`. Inputs: the table, the laps as Python passes them (after `_damage_repair_summary`; the payload copies `lap`, `lap_time_s`, `complete`, `flag_state`, `flag_states`, fractions, pit fields, `fuel.used_gal`, `conditions`), the steering basis, the setup series (vectors: values; production: U1's `SetupSeries`). Outputs: the `lap_traces` subtree (25.4 MB of the race's 27.0 MB) and `track_profile` (whose `detected_corner_segments` are the load zones U4 and U5 read).

Primitives: `float_mod` (`pct % 1.0` for pct in [-0.01, 1.01]), `int_from_float` (`int(pct * bins)`), `sorted_floats` and the median rule (`samples.sort()` in place, then the middle or the mean of the middle two), `max_index_by` with `abs` (signed peak), `min_num`/`max_num`, `fmean`, `round_ndigits` (including `round((bucket + 0.5) / maximum_bins, 6)` and `round(representative, precision)`), `round_to_int` (`int(round(statistics.median(gears)))`, half to even), `hypot` (CPython `vector_norm`), `atan2` (C library: platform family), `degrees`, `fsum` through U1's row means.

Ordering hazards:
- Bins are `defaultdict(list)` filled in sample order and visited by `sorted(buckets)`; a bin's samples are a run (slice) or an index list; `complete` columns skip the `None` filter.
- Each bin's dict is built in the order written, then `additional_signals` is filled signal by signal in catalog order, so its keys follow `_additional_trace_sources`' registration order, skipping a signal with no sample in that bin or a non-finite representative.
- The catalog registers only channels present with at least one finite value; `steering-wheel-angle` only on a road-wheel basis; slip only when wheel and vehicle speed exist; carcass and surface temperatures are U1 row means at precision 2.
- `tire_stress_proxy` is `0.35 * a + 0.35 * b + 0.30 * c`, left to right; `speed_scale = min(1.5, max(0.0, (rep or 0.0) / 80.0))`.
- `sector_start_pcts` is `sorted` of a set of finite values in [0, 1).
- `_track_profile`: a sample counts when its lap distance is present and `>= 0`, not on pit road and with no caution bit; bins by `int((position % 1.0) * bins)`; setup traces in the order `add_setup_trace` registers them (platform, shocks, tires by dict order); `record.update(additions)` order; thresholds are `_percentile(..., 0.55/0.65)` over the profile records; segments merge across start/finish; `min(records, key=speed or inf)` keeps the first; `setup_trace_fields` sorted.

### U4. Sections and clean-lap screening

Entry points: `_corner_tire_age_summary(table, laps, runs, track_profile)` (with `_corner_lap_exclusion_reasons`, `_initial_tire_zero_evidence`, `_run_tire_age_context`, `_phase_lap_groups`, `_zone_lap_metric`, `_phase_green_age_bounds`, `_phase_summary`, `_phase_comparison`, `_corner_phase_coaching`), `_pace_attribution(table, laps, track_profile)` (with `lap_reference.build_lap_trace`, `lap_reference.uniform_segments`, `time_loss.analyze_time_loss`, `tire_energy.segment_energy`), `_clean_lap_traces(table, laps)` (returns `(list[lap_reference.LapTrace], list[int])`), `pit_loss.measure_pit_loss(session_time_s, on_pit_road, lap_dist_pct, speed_m_s, lap_numbers, reference)` (returns `pit_loss.PitLossReport`). `_zone_lap_metric` is not an entry point: Python would pass it whole columns on every call (20 MB of vectors for 14 calls on the practice session). Inputs: the table, laps and runs as Python passes them (with `damage_repair_context`, `tire_set_lifecycle`, `coaching_reference_lap_numbers`), the track profile's load zones, the reference `LapTrace`. Outputs: the `corner_tire_age` and `pace_attribution` subtrees, the clean-lap traces and the pit-loss report (dataclasses, rebuilt by the codec), which Python `_strategy_planning` consumes.

Primitives: `sum_floats_from_int_zero` (`brake_energy` and `steering_work` in a zone: builtin `sum` over a generator), `sort_by` (`records.sort(key=relative)`: CPython's stable timsort on float keys), `min_index_by`/`max_index_by` (`min(speed_core, key=...)`, `max(range(...), key=brake)`), `float_mod`, `sorted_floats` and the median rule, `round_ndigits`, `floordiv` (`(index * 3) // max(1, n)`), `format_float` (`{entry:.1f}`, `{steer_work:.0f}`, `{release_timing * 100:.1f}` in the coaching text), and inside the modules: `atan2` and `sin` (`tire_energy`: C library, platform family), `statistics.median` (`time_loss`).

Ordering hazards:
- Exclusion counts are a `defaultdict(int)` emitted as `dict(sorted(...))`; laps visited by `sorted(laps, key=lap or -1.0)` (stable).
- `_zone_lap_metric` reads raw values with `_finite` per sample (not cached views), filters by wrap, margins, caution, pit, track surface, session state; records sorted by `relative` (stable, ties in index order); the brake onset, release, throttle pickup and turn-in scans run over those sorted records.
- The coaching text: `"; ".join(evidence).capitalize()` (Python `str.capitalize` lower-cases the rest of the string) and `dict.fromkeys(actions)` (first occurrence order).
- `_phase_lap_groups` assigns thirds by `min(2, (index * 3) // max(1, n))` over laps sorted by number.
- `_pace_attribution` builds traces for `sorted(groups.items())`, energy over `sorted(energy_indices)`, priorities in `time_loss` segment order; `round(segment.start_pct, 6)`.
- `LapTrace` and `PitLossReport` field order and types are the dataclasses' own.

### U5. Field geometry and groove

Entry points: `analyze_groove_evolution(channels, *, runs, laps, load_zones, channel_units, track_type)` (groove_analysis, reached through the `analysis_engine` global), `build_track_geometry(channels, metadata, identity, source_fingerprints, *, main_bins=500)` (race_foundations, the same way). Inputs: the table's raw channels (the oracle passes `table.channels` and a map of `table.get` columns; `SessionFlags` replaced by U1's `_caution_flag_series`), runs, laps, the load zones, units, metadata, identity, fingerprints. Outputs: the `groove_evolution` and `track_geometry` subtrees.

Primitives: `race_foundations._finite_column` (same semantics as `_normalize_column`), `statistics.median`, `hypot`, `round_ndigits` (`round(float(_median(...) or 0.0), 8)`), `float_mod`, `int_from_float`, `radians`, `cos`, `sin`, `atan2` (C library: platform family; groove projects latitude and longitude with `cos(radians(lat0))` and measures headings with `atan2`), `json_float` and a JSON writer for `track_geometry_sha256` (the hash of the geometry subtree's `json.dumps` text; the U5 agent reads its flags from `race_foundations.py:470`).

Ordering hazards: geometry candidates in sample order, the GPS cluster from medians and the MAD, bins of 500, pit entry and exit windows of `max(3, int(sample_rate * 4.0))`, `_average_paths`, `_normalize_geometry`, `_sanitize_geometry_layers` (rejected layers sorted), the hash over the final dict; groove's zone calibration, window summaries and run order as `analyze_groove_evolution` builds them. The U5 agent completes this list from the module while porting it, in the same form as the units above.

## 3. Parity per unit: vectors

### Generator

`crates/coach-analysis/tools/emit_kernel_vectors.py` (CLI) and `kernel_codec.py` (codec and store writers, standard library only), written with this plan. For each recording the generator decodes the table exactly as the worker does (`workflow._load_analysis_telemetry`, one file, native rate, or `--target-hz`), runs `analysis_engine.analyze_telemetry` once with each selected entry point wrapped (the same module-global wrapping the parity harness uses), and records every call at call time:

- `args`, `kwargs`: encoded on entry, before the call or later oracle code can mutate them (laps gain `damage_repair_context` after `_lap_summaries` returns; recording at the end would capture the wrong laps);
- `result`, or `raises` (`class`, `message`): encoded on return;
- `mutated`: each argument whose content changed during the call, as it is on return (content compared through SHA-256-named columns, so a mutated column-valued list is caught);
- `accessed`: the table channels the call itself recorded (the table's set is swapped for an empty one during the call and merged back after, so nested calls stay correct).

The table goes once per recording to `table.ccs`, a decode-stage store (`coach_analysis::store::ColumnStore::parse` reads it; on the qualifying and practice recordings its sample indices, source layout, sampling and every column's data block are byte-identical to `coach-analysis decode` with the same channel selection), and `table.json` (the `TelemetryTable` metadata: `metadata`, `session_info`, `variables`, `available_variables`, `channel_units`, `length`, `channel_names`, `analysis_id`).

Codec, version 1 (the full table is the `kernel_codec.py` docstring): JSON with floats written by `float.__repr__`; non-finite floats, out-of-range ints, tuples, sets, dicts with non-string keys and dataclasses as `$` markers; the table, its channel mapping and its channel lists by reference (`$table`, `$table_channels`, `$table_column`); any list of 256 or more `float`, `int`, `bool` or `str` items, optionally with `None`, as `{"$column": id}` into the call's vector store (`<call>.ccs`: the `.ccs` container with stage `vector`, per column an element `f64`, `i64`, `bool` or `utf8`, a data block and an optional `present` block of one byte per item). A value with no rule fails the generator (`CodecError`); a unit agent adds a rule rather than losing information. The recording's absolute path is replaced by `<recording>/<name>` everywhere, so vectors from two machines and both platforms compare equal.

Layout under `--out`: `<platform>/<sha256[:12]>[-<rate>hz]/table.ccs`, `table.json`, `<unit>/<module>.<entry>/<call>.json` and `.ccs`, `<unit>/index.json` (counts and the SHA-256 of every file). What a unit agent adds, and nothing else: its entries in `UNITS` (`Entry(module, name, max_calls=None)`); where needed a probe in `PROBES` (vectors that are not calls: U1 records, per recording, every channel's finite, fraction, boolean and flag-bit view, and the views of every alias list the engine uses, as SHA-256 over presence byte plus value bits, with each channel's count of non-finite samples so the corpus's blind spots are visible); and builders in `SYNTHETIC` for paths no recording reaches (each receives a deep copy of the fixture's decoded table and returns an edited one: a NaN-bearing or text channel, a unit string the corpus lacks, a steering ratio change, a merged group with missing cells).

Checked while writing it (2026-10-03), with 17 ad hoc entry points (`--entry`: every U2, U3 and U4 entry, `_setup_channel_series`, `_setup_telemetry`, `_caution_flag_series`, `_dt_series`, `analyze_groove_evolution`, `build_track_geometry`, and `_damage_repair_summary`, `_annotate_tire_set_lifecycle` and `_zone_lap_metric` for the mutation and size cases): `--selftest` (decode every stored value and compare it with a snapshot of the live object, type for type and bit for bit) passes on the fixture (62 calls), the qualifying (27) and the practice (37) recordings, and on a synthetic table with NaN speeds and at `--target-hz 20`; the fixture's 62 call files and `table.ccs` are byte-identical from Windows CPython 3.12.14 and WSL CPython 3.12.3 (`table.json` differs in the interpreter version only); `--check` reports identical; `coach-analysis inspect` reads every `table.ccs`. Sizes: fixture 1.2 MB with all 17 entries (committable); practice recording 126 MB (`table.ccs` 41 MB); the corpus is several GB and stays under `target/`.

Corpus and platforms:

- Committed: fixture and `SYNTHETIC` vectors under `crates/coach-analysis/tests/vectors/kernels/{windows,linux}/` (generated with `--fixtures` by the bundled Windows interpreter and by `python3.12` in WSL).
- Local only: the private corpus (`COACH_PRIVATE_CORPUS`, at least the 50 unique recordings of phase 1, short paths: `Path.rglob` skips paths over 260 characters on Windows) at native rate and at 20 Hz (`--target-hz 20`), under `target/kernel-vectors`, never committed.
- The binding oracle is Linux CPython 3.12. Each unit's vectors are generated on both platforms. A call whose Windows and Linux vectors differ is listed in the unit's report with the primitive that explains it (C library `sin`, `cos`, `atan2`, `pow`; C `long` in `sum`, `numerics.md` section 4); any other difference between the platforms is a finding to explain before the unit passes, never a tolerance. The Rust test compares against its own platform's vectors.

### Rust replay test

One test file per unit, `crates/coach-analysis/tests/kernels_<unit>.rs`, calling the skeleton's support (`tests/support/vectors.rs`): for every recording folder under `tests/vectors/kernels/<platform>/` and, when `COACH_KERNEL_VECTORS` is set, under `$COACH_KERNEL_VECTORS/<platform>/`: build the table once (`ColumnStore::parse` of `table.ccs`, `TableMeta` from `table.json`, `Table::from_store`); for each call of the unit decode `args` and `kwargs` (vector store read with the same validation as `ColumnStore`: aligned, inside the file, exact lengths, `present` exactly one byte per item), run the native entry through the dispatch table, and compare `result` (or `raises` class and message), every `mutated` argument and `accessed` exactly: same variant (`Int` is not `Float` is not `Bool`), same float bits (any NaN equals any NaN, as `numerics.md` section 5), same key order, same strings. The first difference fails with the recording, entry, call number and value path. A call whose arguments the entry does not accept is a failure (`KernelError::Internal`), never a default.

Blind spots to close per unit with `SYNTHETIC` tables (the U1 probe reports which channels carry non-finite samples): text channels (`char`), NaN and infinity in float channels, a finite column whose compensated sum overflows, unit strings other than the corpus's (mm, kPa, Pa, bar, K, ft), road-wheel and steering-wheel bases and a ratio change mid-recording, Offline Testing sessions (one-lap-to-green clearing), a merged group with missing cells (also the `reconnect` worker case), a lap 0 and laps with one sample, no load zones (uniform fallback segments).

## 4. Integration

### 4.1 Stage

Stage name `kernels` (already in `native_stages.KNOWN_STAGES`). `IRACING_COACH_NATIVE_STAGES=decode,kernels`; `kernels` without `decode` fails the job (`NativeStageError`): the kernels read the store, and converting Python lists into one would rebuild the memory phase 2 removes. An entry point a build does not provide fails the job; nothing falls back to Python.

### 4.2 Process and hand-off

The kernels run in a child process, `coach-analysis kernels`, started once per analysis job by the worker and kept until the analysis returns; one JSON object per line on standard input and output, diagnostics on standard error.

- Start: `{"version": 1, "codec": 1, "store": "<path>", "table": <TableMeta>, "steering_basis": <SteeringBasis>}`; the process reads (or maps) the store and answers `{"ok": true, "length": n}`.
- Call: `{"entry": "analysis_engine._lap_summaries", "args": [...], "kwargs": {...}}`, values in the vector codec, the table as `{"$table": 1}`; answer `{"result": ..., "accessed": [...]}` or `{"raises": {"class", "message"}}` for an exception the oracle raises on the same input (the shim raises that class), or `{"error": "..."}` for anything else (`NativeStageError`).
- Exit on end of input; a crash or a non-zero exit is `NativeStageError`.
- Column-valued results travel as JSON arrays; `_setup_channel_series` returns `{"$handle": n}`, which the shims pass back to `_setup_telemetry` and `_track_profile` (nothing else reads the setup series).

Cost, measured on the race: the largest result, `lap_traces`, is 23.3 MB of compact JSON, which Python's `json.loads` builds in 0.24 s (`json.dumps` of the same objects: 0.35 s); every other result is under 1 MB. The analysis has to hold these objects anyway: the hand-off adds the transient text, not a second copy of the samples.

Why a process and not PyO3, which `contracts/worker-interface.md` section 7 still names for boundary B: the reasons phase 1 recorded hold (no Python ABI in the build, plain `cargo build` on both platforms, the child's memory released when it exits), the measured transfer is 0.3 s against about 6 s of kernels on the race, and the units produce an ordered value tree (`PyValue`) that the codec serialises, so changing the transport later touches the skeleton only.

### 4.3 What Python does and skips

`workflow.py` (outside the analyzer bundle), when `kernels` is listed:

1. `_load_analysis_telemetry` calls `native_stages.load_telemetry(..., columns="store")`: the same scan, decode and validation as phase 1, but `samples` maps each channel to a `StoreColumn` (a read-only sequence over the mapped store: `len`, indexing and slicing convert only the elements asked for, exactly as `read_store` converts them; iteration walks the `memoryview`) instead of a list; `sample_provenance` is a sequence computed on access (nothing in the analysis reads it; only the merge and `test_full_telemetry_contract.py`'s length check do). A multi-file group is merged natively (U1) into one `merge` store. The store's temporary directory lives until the analysis returns.
2. Around the existing call, `with native_stages.kernel_session(telemetry): analysis = analyze_telemetry(...)`. The parity harness keeps capturing `workflow.analyze_telemetry` unchanged.

`kernel_session` starts the child, then for the duration of the call rebinds `analysis_engine.TelemetryTable` to `native_stages.StoreTable` and every section 2 entry point (module attribute) to a shim, restoring all of them in `finally` (one analysis per worker process, `worker-interface.md` section 1, so the rebinding is never shared). `StoreTable` subclasses `TelemetryTable` and overrides only `__init__`: it runs the oracle's `__init__` on the mapping with an empty `samples` (so every attribute is derived by the oracle's code), then sets `channels` to the `StoreColumn` map and `length` to the sample count. Every inherited method is the oracle's own code over `StoreColumn`s, so Python-remaining callers (section 2) get the oracle's values with no list copy. Before the first call the session computes `_steering_basis(table)` in Python (cached on the table as the oracle does; the oracle calls it at the same point) and sends it in the start request. A shim encodes its arguments, calls the child, adds the returned `accessed` names to `table.accessed_channels`, and decodes the result. `_fallback_telemetry_digest` gets a shim that materialises plain lists and calls the original, because `json.dumps(..., default=str)` would silently stringify a `StoreColumn`.

Python skips: the decode lists, the `TelemetryTable` copies, every view a ported entry builds, and all per-sample work of the section 2 units. It still runs the scan, file checks and fingerprints, the body of `analyze_telemetry`, the functions that stay Python, the analysis-cache write and the report files (phase 3 is the JSON writer).

`kernel_codec.py` moves to `iracing-coach/skills/analyze-iracing-race/scripts/native_codec.py` (outside `ANALYZER_SOURCE_FILES`) in the integration commit, and the generator imports it from there; the shims and the vectors then share one codec.

### 4.4 `analysis_id`

No file in `analysis_engine.ANALYZER_SOURCE_FILES` changes: the seam is `workflow.py`, `native_stages.py` and `native_codec.py`, and the entry points are rebound at run time, not edited. `_analysis_fingerprint` still runs in Python over the same bundle, so `analysis_id` is unchanged and `test_analysis_pipeline_identity.py`'s recorded digest stays valid. The server's analyze idempotency key hashes every `*.py` in the scripts folder (`worker-interface.md` section 4) and changes, as intended. Gate: the production-identity capture pair (digest unpinned) has equal `analysis_id` and run ids.

### 4.5 Memory plan

- Rust holds each raw column once at its SDK width (f32 stays 4 bytes a sample), read or memory-mapped from the store; no boxed value per sample anywhere.
- A view is computed on first use and cached like `_cached`: `f64` values plus a presence bit per sample (8.125 bytes a sample against 32 for a Python float in a list); an untransformed finite view of an `f64` column borrows the raw data and stores only the presence bits.
- Per-bin and per-lap work streams over index runs: medians sort one reused scratch buffer; a bin never copies its samples into a new vector unless the oracle's reducer sorts them; signals are reduced as they are read.
- Python holds no decode list and no `TelemetryTable` copy; residual lists come only from the functions that stay Python (section 2 lists them), about 10 to 20 columns on the race.
- Estimate for the race (to be measured, not assumed): Python under 0.6 GiB (interpreter, session info, the analysis objects, residual columns), the child under 0.5 GiB (store 127 MB, views at most about 130 x 1.5 MB), against 2.29 GiB today. `measure_worker.py` reports the measured peak; the admission formula (`worker-interface.md` section 6) and `mem_limit` change only on the N100 G1 measurement.

### 4.6 Native execution in the gate

The parity harness wraps `native_stages.kernel_call` (the one function every shim goes through). A native capture must show, for every recording, one kernel call per oracle call of each ported entry point, count for count against the oracle capture (which records the same entry points by wrapping them); the oracle capture must show none. The same holds for worker cases through `worker_trace.py`.

### 4.7 Contract change (made by the integration owner, reviewed before the code lands)

`contracts/worker-interface.md` section 7: boundary B becomes the `coach-analysis kernels` child process with the JSON-lines protocol and codec of section 4.2 instead of PyO3 in process; `kernels` requires `decode`; the `merge` store (decode layout plus `present` and provenance blocks; stage `merge`, so a decode-only reader rejects it); `StoreColumn` tables instead of lists when `kernels` is listed. Section 8: the kernel-call count rule of section 4.6. One entry in `contracts/README.md`; Codex Sol 6.1 review (D15).

## 5. Acceptance

Per unit (U1 to U5), with `CARGO_TARGET_DIR=<repo>/target/<unit id>`, `CARGO_BUILD_JOBS=4`:

| Command | Pass |
|---|---|
| `python -X utf8 crates/coach-analysis/tools/emit_kernel_vectors.py --unit <unit> --fixtures --out crates/coach-analysis/tests/vectors/kernels` (bundled 3.12.14) and the same with `python3.12` in WSL | writes the committed fixture and synthetic vectors; `--check` then reports identical, twice |
| `... --unit <unit> --corpus "$COACH_PRIVATE_CORPUS" --out target/kernel-vectors --selftest` and again with `--target-hz 20`, on Windows and in WSL | every recording recorded or rejected as the oracle rejects it; no self-test failure |
| `cargo test -p coach-analysis --test kernels_<unit>` (Windows; WSL) | every fixture and synthetic call identical |
| the same with `COACH_KERNEL_VECTORS=target/kernel-vectors` (Windows; WSL) | every corpus call identical on its own platform |
| `cargo clippy -p coach-analysis --all-targets -- -D warnings`; `cargo fmt -p coach-analysis --check` | clean |
| unit report | calls compared per entry and recording; every Windows/Linux vector difference with its primitive; the oracle branches no recording reaches and the synthetic table that covers each |

Integration (after all units):

| Command | Pass |
|---|---|
| `python tools/parity/analysis_parity.py corpus --native-stages decode,kernels --native-bin target/<id>/release/coach-analysis.exe --out <dir>` with `COACH_PRIVATE_CORPUS` | every recording identical in raw and parsed mode, the four worker cases identical with equal `analysis_id`, kernel calls count for count (section 4.6), none on the oracle side |
| the same with `--fixtures-only` in WSL (Linux build, `python3.12`) | identical, same `analysis_id` values as Windows |
| `test_native_stages` and the new kernel-session tests with `IRACING_COACH_NATIVE_BIN` (Windows; WSL) | pass |
| `tools/verify-repository.ps1 -PythonPath <bundled 3.12.14>` | exit 0 |
| `python tools/parity/measure_worker.py` on the race, `decode` against `decode,kernels`, 5 alternating cold runs | wall time and peak working set reported (medians and ranges); the N100 G1 measurement follows in the deploy step |
| Codex Sol 6.1 review of the kernels and of the section 4.7 contract change | findings resolved and recorded in `reviews/` |

## 6. Ownership

Each agent edits only its own paths; the generator's `UNITS`, `PROBES` and `SYNTHETIC` entries for a unit belong to that unit's agent. Nothing is committed without the parity gate. Agent model per D15: Opus 5.5 at high effort for every unit and the integration (analysis parity); Codex Sol 6.1 reviews.

| Unit | Crate module paths | Tests and vectors | Other paths | Agent |
|---|---|---|---|---|
| I0 skeleton (first, about half a day) | `crates/coach-analysis/src/kernels/{mod.rs, value.rs, codec.rs, dispatch.rs, session.rs, error.rs}`; empty `kernels/{table,laps,traces,sections,field}/mod.rs` each with `pub const ENTRIES: &[Entry] = &[];`; `src/lib.rs` (`pub mod kernels`); `Cargo.toml` (`coach-numerics` dependency) | `tests/support/{mod.rs, vectors.rs}`; one `tests/kernels_<unit>.rs` per unit calling the support | | port-integration |
| U1 table | `src/kernels/table/` (`mod.rs`, `views.rs`, `units.rs`, `steering.rs`, `setup.rs`, `summaries.rs`, `merge.rs`); `src/store.rs` (`merge` stage reader and writer) | `tests/kernels_table.rs`; `tests/vectors/kernels/*/**/table/` | `tools/emit_kernel_vectors.py` (`UNITS["table"]`, U1 probe, U1 synthetic tables) | port-u1-table |
| U2 laps | `src/kernels/laps/` (`mod.rs`, `summaries.rs`, `dynamics.rs`, `conditions.rs`, `service.rs`, `disruption.rs`) | `tests/kernels_laps.rs`; `.../laps/` | `UNITS["laps"]`, `SYNTHETIC["laps"]` | port-u2-laps |
| U3 traces | `src/kernels/traces/` (`mod.rs`, `payload.rs`, `signals.rs`, `profile.rs`) | `tests/kernels_traces.rs`; `.../traces/` | `UNITS["traces"]`, `SYNTHETIC["traces"]` | port-u3-traces |
| U4 sections | `src/kernels/sections/` (`mod.rs`, `corner_age.rs`, `zone_metric.rs`, `phases.rs`, `lap_reference.rs`, `time_loss.rs`, `tire_energy.rs`, `pit_loss.rs`) | `tests/kernels_sections.rs`; `.../sections/` | `UNITS["sections"]`, `SYNTHETIC["sections"]` | port-u4-sections |
| U5 field | `src/kernels/field/` (`mod.rs`, `groove.rs`, `geometry.rs`, `geometry_hash.rs`) | `tests/kernels_field.rs`; `.../field/` | `UNITS["field"]`, `SYNTHETIC["field"]` | port-u5-field |
| I integration (after U1 to U5 pass) | `src/kernels/serve.rs`, `src/main.rs` (`kernels`, `merge` subcommands) | `iracing-coach/tests/test_native_stages.py` | `scripts/native_stages.py`, `scripts/workflow.py`, `scripts/native_codec.py` (moved from `tools/kernel_codec.py`), `tools/parity/analysis_parity.py`, `tools/parity/worker_trace.py`, `contracts/worker-interface.md`, `contracts/README.md`, `port/phase2-*.md` results | port-integration |

Order: I0 and U1's milestone M1 (the API in section 2, U1) land first; U2 to U5 start at the same time, read and port against the fixed signatures, and run their vector tests as soon as M1 is in; U1 continues with its summaries and the merge in parallel. Integration starts when every unit passes its acceptance.

## 7. What the phase 1 review changes here

`reviews/port-phase1-review-codex-sol.md` found three kinds of defect; the plan answers each before code exists:

- **Bounds.** Every input a kernel reads is validated before use: the store by `ColumnStore::parse` (exact block lengths, alignment, stage), the per-call vector stores with the same rules plus `present` lengths, every decoded argument against the shape the entry expects (`KernelError::Internal`, never a default); the codec refuses a value it has no rule for instead of degrading it. `StoreColumn` indexing checks bounds like a list.
- **Seam bypass.** A timer that merely exists proved nothing for the worker cases. Here the gate counts kernel calls against the oracle's calls of the same entry points, per recording and per worker case, and requires none on the oracle side (section 4.6); `kernels` without `decode`, an unknown entry or an unavailable stage fails the job.
- **Vector blind spots.** Vectors are recorded from the real call arguments of every corpus recording at two rates, on both platforms, plus synthetic tables for the paths no recording reaches (section 3 lists them); the U1 probe reports which channels carry non-finite samples; each unit reports the oracle branches its vectors do not reach. A Windows-only difference is explained by a named primitive or the unit fails.
