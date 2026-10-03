//! `analysis_engine._corner_tire_age_summary` with `_initial_tire_zero_evidence`: clean corner
//! behaviour per detected load zone, compared across observational run phases.

use super::phases::{
    AgeByLap, TIRES, corner_phase_coaching, exclusion_reasons, flag_state, phase_comparison,
    phase_lap_groups, phase_summary, run_tire_age_context, sorted_all,
};
use super::zone_metric::{Basis, Series, zone_lap_metric};
use super::{
    KResult, Seq, ae_round, ae_round_value, at, finite, finite_value, get, get_or_empty, int_of,
    items, py_int,
};
use crate::kernels::dispatch::Call;
use crate::kernels::error::KernelError;
use crate::kernels::session::Session;
use crate::kernels::table::{SteeringBasis, Table};
use crate::kernels::value::{Dict, PyValue};

/// `_dt_series(times, fallback_hz)` over the raw times column (`_normalize_column` is `_finite`
/// per sample).
pub fn dt_series(times: &dyn Seq, fallback_hz: f64) -> KResult<Vec<f64>> {
    let rate = if 1.0 > fallback_hz { 1.0 } else { fallback_hz };
    let fallback = 1.0 / rate;
    if times.is_empty() {
        return Ok(Vec::new());
    }
    let mut result = Vec::with_capacity(times.len());
    result.push(fallback);
    let mut previous = finite(times.cell(0))?;
    for index in 1..times.len() {
        let current = finite(times.cell(index))?;
        let delta = match (previous, current) {
            (Some(previous), Some(current)) => {
                let delta = current - previous;
                if 0.0 < delta && delta < 2.0 {
                    delta
                } else {
                    fallback
                }
            }
            _ => fallback,
        };
        result.push(delta);
        previous = current;
    }
    Ok(result)
}

/// `(run.get("pit_service") or {}).get("tire_odometer_evidence") or {}).get(tire, {})
/// .get("reset_observed")`, as a truth value.
fn reset_observed(run: &PyValue, tire: &str) -> KResult<bool> {
    let service = get(run, "pit_service")?;
    let evidence = get_or_empty(service, "tire_odometer_evidence")?;
    if !evidence.truthy()? {
        return Ok(false);
    }
    let PyValue::Dict(evidence) = evidence else {
        return Err(KernelError::internal(
            "tire_odometer_evidence is not a dict",
        ));
    };
    match evidence.get(tire) {
        None => Ok(false),
        Some(details @ PyValue::Dict(_)) => get(details, "reset_observed")?.truthy(),
        Some(other) => Err(KernelError::internal(format!(
            "tire_odometer_evidence[{tire:?}] is a {}",
            other.type_name()
        ))),
    }
}

/// `_initial_tire_zero_evidence(table, laps, runs)`.
pub fn initial_tire_zero_evidence(
    table: &Table,
    laps: &[PyValue],
    runs: &[PyValue],
) -> KResult<Dict> {
    const THRESHOLD_M: f64 = 250.0;
    let mut first_lap = None;
    for lap in sorted_all(laps)? {
        let number = match finite_value(get(lap, "lap")?)? {
            Some(x) if x != 0.0 => x,
            _ => 0.0,
        };
        if number > 0.0 {
            first_lap = Some(lap);
            break;
        }
    }
    let mut values: Vec<(&str, f64)> = Vec::new();
    if let Some(first_lap) = first_lap {
        let start = get(first_lap, "start_index")?;
        let first_index = if start.truthy()? { py_int(start)? } else { 0 };
        for tire in TIRES {
            let channel = format!("{tire}odometer");
            if !table.has(&[channel.as_str()]) {
                continue;
            }
            let samples = table.get(&[channel.as_str()], PyValue::None);
            if first_index < samples.len() as i64
                && let Some(value) = finite(at(&samples, first_index)?)?
            {
                values.push((tire, value));
            }
        }
    }
    let mut reset_validated = Dict::new();
    let mut resets_confirmed = true;
    for tire in TIRES {
        let mut observed = false;
        for run in runs {
            if reset_observed(run, tire)? {
                observed = true;
                break;
            }
        }
        resets_confirmed &= observed;
        reset_validated.insert(tire, observed);
    }
    let near_zero = values.len() == TIRES.len()
        && values
            .iter()
            .all(|(_, value)| 0.0 <= *value && *value <= THRESHOLD_M);
    let confirmed = near_zero && resets_confirmed;
    let mut odometer = Dict::new();
    for (tire, value) in &values {
        odometer.insert(*tire, ae_round(Some(*value), 1)?);
    }
    Ok(Dict::new()
        .with(
            "status",
            if confirmed {
                "confirmed"
            } else if near_zero {
                "suggestive_not_confirmed"
            } else {
                "unavailable"
            },
        )
        .with("confirmed", confirmed)
        .with("initial_odometer_m", odometer)
        .with("near_zero_threshold_m", THRESHOLD_M)
        .with("later_reset_semantics_validated", reset_validated)
        .with(
            "rule",
            "All four tire odometers must begin near zero and later demonstrate a reset in the \
             same recording.",
        ))
}

/// `defaultdict(int)` of exclusion reasons over `laps` visited in lap order, as
/// `dict(sorted(counts.items()))`.
fn exclusion_counts(laps: &[&PyValue]) -> KResult<Dict> {
    let mut counts: Vec<(String, i64)> = Vec::new();
    let mut previous: Option<String> = None;
    for lap in sorted_refs(laps)? {
        for reason in exclusion_reasons(lap, previous.as_deref())? {
            match counts.iter_mut().find(|(key, _)| *key == reason) {
                Some((_, count)) => *count += 1,
                None => counts.push((reason, 1)),
            }
        }
        previous = Some(flag_state(lap)?);
    }
    counts.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(counts
        .into_iter()
        .map(|(key, count)| (PyValue::Str(key), PyValue::Int(count)))
        .collect())
}

fn sorted_refs<'a>(laps: &[&'a PyValue]) -> KResult<Vec<&'a PyValue>> {
    let keys = laps
        .iter()
        .map(|lap| super::lap_sort_key(lap))
        .collect::<KResult<Vec<f64>>>()?;
    let order = coach_numerics::sort_permutation(laps.len(), &mut |i, j| keys[i] < keys[j], false);
    Ok(order.into_iter().map(|index| laps[index]).collect())
}

/// `int(_finite(lap.get("lap")) or -1)`.
fn lap_key(lap: &PyValue) -> KResult<i64> {
    match finite_value(get(lap, "lap")?)? {
        Some(x) if x != 0.0 => int_of(x),
        _ => Ok(-1),
    }
}

const PHASE_DEFINITION_CONFIRMED: &str = "chronological early/middle/late thirds of this run's \
    eligible laps with exact per-tire green-lap-on-set bounds after every tire has a confirmed \
    zero-age boundary";
const PHASE_DEFINITION_FALLBACK: &str = "chronological early/middle/late thirds with \
    session-relative age bounds when at least one tire lacks a confirmed zero-age boundary";
const TIRE_STATE_LABELS: &str = "fresh/settled/worn remain unavailable until session- or \
    history-derived pace/control/tire change points support inclusive green-lap-age boundaries";
const LATE_SEMANTICS: &str = "older-set/late-run proxy when green-lap age is confirmed; \
    late-run proxy otherwise; never measured worn tread state";
const ELIGIBILITY_RULE: &str = "complete clean green laps only; pit, caution/mixed, restart, \
    non-racing-state, off-track, close-traffic, and partial laps are excluded when the \
    required channels exist";

fn evidence(class: &str, key: &str, text: &str) -> Dict {
    Dict::new().with("evidence_class", class).with(key, text)
}

fn metric_evidence() -> Dict {
    Dict::new()
        .with(
            "entry_minimum_exit_speed_mph",
            evidence(
                "derived_from_measured_Speed",
                "aggregation",
                "median of per-lap zone observations",
            ),
        )
        .with(
            "brake_average_peak",
            evidence(
                "derived_from_measured_Brake",
                "aggregation",
                "median of per-lap average/peak fractions",
            ),
        )
        .with(
            "brake_energy_proxy",
            evidence(
                "derived_proxy",
                "definition",
                "integral of brake fraction x vehicle speed through the zone",
            ),
        )
        .with(
            "brake_onset_release",
            evidence(
                "derived_routine_sample_threshold",
                "definition",
                "0.05 brake-fraction crossings; omitted when the transition is not observed",
            ),
        )
        .with(
            "steering_amount_work",
            evidence(
                "derived_from_measured_SteeringWheelAngle",
                "definition",
                "mean absolute angle, sign-change corrections, and integral of absolute angle x \
                 speed, on the recording's steering basis (lap_traces.steering: road-wheel angle \
                 when the setup states the steering ratio)",
            ),
        )
        .with(
            "turn_in",
            evidence(
                "derived_routine_sample_threshold",
                "definition",
                "first steering demand crossing 0.05 rad of steering-wheel angle (0.05/12 rad of \
                 road-wheel angle) above the pre-zone signed steering baseline; absolute-angle \
                 fallback when no baseline exists; omitted and boundary-censored when the \
                 crossing predates the analysis window",
            ),
        )
        .with(
            "throttle_pickup_exit",
            evidence(
                "derived_from_measured_Throttle",
                "definition",
                "first 0.75 throttle sample after minimum speed plus zone-exit throttle",
            ),
        )
        .with(
            "coaching",
            evidence(
                "inferred",
                "target_rule",
                "no exact target without a valid aligned representative-lap comparison",
            ),
        )
}

/// `_corner_tire_age_summary(table, laps, runs, track_profile)`.
pub fn corner_tire_age_summary(
    table: &Table,
    basis: &SteeringBasis,
    laps: &[PyValue],
    runs: &[PyValue],
    track_profile: &PyValue,
) -> KResult<PyValue> {
    let listed = get_or_empty(track_profile, "detected_corner_segments")?;
    let mut segments: Vec<&PyValue> = Vec::new();
    if listed.truthy()? {
        for segment in items(listed, "detected_corner_segments")? {
            if let PyValue::Dict(dict) = segment
                && finite_value(dict.get("start_pct").unwrap_or(&PyValue::None))?.is_some()
                && finite_value(dict.get("end_pct").unwrap_or(&PyValue::None))?.is_some()
            {
                segments.push(segment);
            }
        }
    }
    let all_laps: Vec<&PyValue> = laps.iter().collect();
    let excluded = exclusion_counts(&all_laps)?;

    let track_location_available = table.has(&["PlayerTrackSurface"]);
    let traffic_available = table.has(&["CarDistAhead", "CarDistBehind"]);
    let mut limitations: Vec<PyValue> = [
        "Load-zone names are provisional telemetry labels unless sourced knowledge supplies an \
         official name.",
        "Routine sampled onset/release and throttle timing are coarser than a native-rate event \
         query.",
        "Within-run phase changes can also reflect traffic, line, damage, weather, track state, \
         fuel mass, and driver adaptation.",
        "Tire wear remains a discrete pit-service observation; this artifact does not \
         interpolate tread remaining within a run.",
        "No session-derived tire-state change-point model is applied, so fresh/settled/worn \
         phase labels remain unavailable.",
    ]
    .into_iter()
    .map(PyValue::from)
    .collect();
    if !table.has(&["CarDistAhead", "CarDistBehind"]) {
        limitations.push(PyValue::from(
            "CarDistAhead/CarDistBehind were unavailable, so traffic and dirty-air exposure \
             could not be screened.",
        ));
    }
    if !table.has(&["PlayerTrackSurface"]) {
        limitations.push(PyValue::from(
            "PlayerTrackSurface was unavailable, so off-track samples could not be screened.",
        ));
    }
    let mut result = Dict::new()
        .with("schema_version", 1i64)
        .with("status", "unavailable")
        .with("confirmed_tire_age_status", "unavailable")
        .with("tire_state_phase_status", "unavailable")
        .with("observational_run_phase_status", "unavailable")
        .with(
            "phase_definition",
            Dict::new()
                .with("confirmed_model", PHASE_DEFINITION_CONFIRMED)
                .with("fallback_model", PHASE_DEFINITION_FALLBACK)
                .with("minimum_laps_per_phase_for_usable_comparison", 2i64)
                .with("tire_state_labels", TIRE_STATE_LABELS)
                .with("late_semantics", LATE_SEMANTICS),
        )
        .with(
            "eligibility",
            Dict::new()
                .with("rule", ELIGIBILITY_RULE)
                .with("candidate_lap_count", laps.len() as i64)
                .with("excluded_lap_counts", excluded)
                .with(
                    "screening",
                    Dict::new()
                        .with("track_location_available", track_location_available)
                        .with("traffic_distance_available", traffic_available)
                        .with("on_track_minimum_fraction", 0.98)
                        .with("close_traffic_maximum_fraction", 0.10),
                ),
        )
        .with("metric_evidence", metric_evidence())
        .with("runs", Vec::<PyValue>::new())
        .with("limitations", limitations);
    if segments.is_empty() {
        result.insert("reason", "No telemetry-derived load zones were detected.");
        return Ok(PyValue::Dict(result));
    }
    if runs.is_empty() {
        result.insert("reason", "No complete run boundaries were detected.");
        return Ok(PyValue::Dict(result));
    }

    let times = table.get(&["SessionTime", "SessionTimeOfDay"], PyValue::None);
    let rate_value = match table.metadata("sample_rate") {
        Some(value) if value.truthy()? => value,
        _ => table.metadata("tick_rate").unwrap_or(&PyValue::None),
    };
    let sample_rate = match finite_value(rate_value)? {
        Some(rate) if rate != 0.0 => rate,
        _ => 20.0,
    };
    let dts = dt_series(&times, sample_rate)?;
    let zone_basis = Basis {
        road_wheel: basis.road_wheel(),
    };
    let pct = table.get(&["LapDistPct"], PyValue::None);
    let speed = table.get(&["Speed"], PyValue::None);
    let brake = table.get(&["Brake", "BrakeRaw"], PyValue::None);
    let throttle = table.get(&["Throttle", "ThrottleRaw"], PyValue::None);
    let steering = table.steering_column(basis);
    let flags = crate::kernels::table::caution_flag_series(table);
    let pit = table.get(&["OnPitRoad"], PyValue::Bool(false));
    let optional = |name: &str| {
        table
            .has(&[name])
            .then(|| table.get(&[name], PyValue::None))
    };
    let track_location = optional("PlayerTrackSurface");
    let ahead = optional("CarDistAhead");
    let behind = optional("CarDistBehind");
    let session_state = optional("SessionState");
    let series = Series {
        pct: &pct,
        speed: &speed,
        brake: &brake,
        throttle: &throttle,
        steering: &*steering,
        flags: &*flags,
        pit: &pit,
        track_location: track_location.as_ref().map(|raw| raw as &dyn Seq),
        car_distance_ahead: ahead.as_ref().map(|raw| raw as &dyn Seq),
        car_distance_behind: behind.as_ref().map(|raw| raw as &dyn Seq),
        session_state: session_state.as_ref().map(|raw| raw as &dyn Seq),
    };
    let initial_zero_evidence = initial_tire_zero_evidence(table, laps, runs)?;

    let mut run_items: Vec<PyValue> = Vec::new();
    let mut any_phase_data = false;
    let mut any_usable = false;
    let mut any_usable_confirmed = false;
    let mut any_usable_observational = false;
    for run in runs {
        let (Some(start_lap), Some(end_lap)) = (
            finite_value(get(run, "start_lap")?)?,
            finite_value(get(run, "end_lap")?)?,
        ) else {
            continue;
        };
        let mut run_laps: Vec<&PyValue> = Vec::new();
        for lap in laps {
            if let Some(number) = finite_value(get(lap, "lap")?)?
                && start_lap <= number
                && number <= end_lap
            {
                run_laps.push(lap);
            }
        }
        let mut reference = get(run, "coaching_reference_lap_numbers")?;
        if reference.is_none() {
            reference = get(run, "valid_green_lap_numbers")?;
        }
        let mut valid_numbers: Vec<i64> = Vec::new();
        if reference.truthy()? {
            let numbers = match reference {
                PyValue::List(items) | PyValue::Tuple(items) | PyValue::Set(items) => items,
                other => {
                    return Err(KernelError::internal(format!(
                        "reference lap numbers are a {}",
                        other.type_name()
                    )));
                }
            };
            for number in numbers {
                if finite_value(number)?.is_some() {
                    valid_numbers.push(py_int(number)?);
                }
            }
        }
        let mut eligible_laps: Vec<&PyValue> = Vec::new();
        for lap in &run_laps {
            if valid_numbers.contains(&lap_key(lap)?) {
                eligible_laps.push(lap);
            }
        }
        let run_exclusions = exclusion_counts(&run_laps)?;
        let age_context = run_tire_age_context(run, &initial_zero_evidence)?;
        let (phase_model, phase_groups, age_by_lap) =
            phase_lap_groups(&eligible_laps, &run_laps, &age_context)?;
        let new_set_confirmed = new_set_confirmed(&age_context, &age_by_lap)?;
        let confirmed_model = phase_model == "confirmed_age_run_thirds_proxy";

        let mut eligible_by_number: Vec<(i64, &PyValue)> = Vec::new();
        for lap in &eligible_laps {
            let key = lap_key(lap)?;
            match eligible_by_number
                .iter_mut()
                .find(|(number, _)| *number == key)
            {
                Some((_, slot)) => *slot = lap,
                None => eligible_by_number.push((key, lap)),
            }
        }
        let mut zones: Vec<PyValue> = Vec::new();
        for (position, segment) in segments.iter().enumerate() {
            let segment_index = position as i64 + 1;
            let segment_number = match finite_value(get(segment, "segment")?)? {
                Some(x) if x != 0.0 => int_of(x)?,
                _ => segment_index,
            };
            let start = finite_value(get(segment, "start_pct")?)?;
            let end = finite_value(get(segment, "end_pct")?)?;
            let or_zero = |value: Option<f64>| match value {
                Some(x) if x != 0.0 => x,
                _ => 0.0,
            };
            let wraps =
                get(segment, "wraps_start_finish")?.truthy()? || or_zero(start) > or_zero(end);
            let mut metrics_by_lap: Vec<(i64, Dict)> = Vec::new();
            for &(lap_number, lap) in &eligible_by_number {
                let next_lap = lap_number.checked_add(1).and_then(|next| {
                    eligible_by_number
                        .iter()
                        .find(|(number, _)| *number == next)
                        .map(|(_, lap)| *lap)
                });
                if let Some(metric) =
                    zone_lap_metric(lap, next_lap, segment, &series, &dts, wraps, zone_basis)?
                {
                    match metrics_by_lap
                        .iter_mut()
                        .find(|(number, _)| *number == lap_number)
                    {
                        Some((_, slot)) => *slot = metric,
                        None => metrics_by_lap.push((lap_number, metric)),
                    }
                }
            }
            let mut tire_age_phases = Vec::new();
            for phase in ["fresh", "settled", "worn"] {
                tire_age_phases.push(PyValue::Dict(phase_summary(
                    phase,
                    &[],
                    &[],
                    &age_by_lap,
                    Some(format!(
                        "No session- or history-derived pace/control/tire change points support \
                         a {phase} green-lap-age boundary."
                    )),
                )?));
            }
            let mut observational: Vec<Dict> = Vec::new();
            for (phase, assigned) in ["early", "middle", "late"].iter().zip(&phase_groups) {
                let mut lap_metrics: Vec<&Dict> = Vec::new();
                for lap in assigned {
                    let key = lap_key(lap)?;
                    if let Some((_, metric)) =
                        metrics_by_lap.iter().find(|(number, _)| *number == key)
                    {
                        lap_metrics.push(metric);
                    }
                }
                observational.push(phase_summary(
                    phase,
                    assigned,
                    &lap_metrics,
                    &age_by_lap,
                    None,
                )?);
            }
            let comparisons = vec![
                phase_comparison(&observational[0], &observational[1])?,
                phase_comparison(&observational[1], &observational[2])?,
            ];
            let coaching_comparison = &comparisons[1];
            for item in &observational {
                if item.get("lap_count").unwrap_or(&PyValue::None).truthy()? {
                    any_phase_data = true;
                }
            }
            if coaching_comparison.get("status").and_then(PyValue::as_str) == Some("usable") {
                any_usable = true;
                if confirmed_model {
                    any_usable_confirmed = true;
                }
                any_usable_observational = true;
            }
            let coaching = corner_phase_coaching(coaching_comparison)?;
            zones.push(PyValue::Dict(
                Dict::new()
                    .with("zone_id", format!("load-zone-{segment_number}"))
                    .with("zone_label", format!("Load zone {segment_number}"))
                    .with("corner_name_status", "provisional_telemetry_load_zone")
                    .with("start_pct", ae_round_value(get(segment, "start_pct")?, 5)?)
                    .with("end_pct", ae_round_value(get(segment, "end_pct")?, 5)?)
                    .with("wraps_start_finish", wraps)
                    .with("tire_age_phases", tire_age_phases)
                    .with(
                        "observational_run_phases",
                        observational
                            .into_iter()
                            .map(PyValue::Dict)
                            .collect::<Vec<_>>(),
                    )
                    .with(
                        "comparisons",
                        comparisons
                            .into_iter()
                            .map(PyValue::Dict)
                            .collect::<Vec<_>>(),
                    )
                    .with("coaching", coaching),
            ));
        }
        let early = if new_set_confirmed {
            "new-set/early-run proxy"
        } else {
            "early-run proxy"
        };
        let middle = if confirmed_model {
            "confirmed-age middle-run proxy"
        } else {
            "middle-run proxy"
        };
        let late = if confirmed_model {
            "older-set/late-run proxy"
        } else {
            "late-run proxy"
        };
        let unavailable = "unavailable_without_session_derived_change_point";
        run_items.push(PyValue::Dict(
            Dict::new()
                .with("run_number", get(run, "run_number")?.clone())
                .with("phase_model", phase_model)
                .with("tire_age_basis", age_context)
                .with("new_set_confirmed", new_set_confirmed)
                .with(
                    "new_set_confirmation_rule",
                    "all four tire ages are lifecycle-confirmed and the first eligible lap \
                     starts at no more than 0.25 green-lap equivalents",
                )
                .with("eligible_lap_count", eligible_laps.len() as i64)
                .with(
                    "eligible_lap_numbers",
                    eligible_laps
                        .iter()
                        .map(|lap| get(lap, "lap").cloned())
                        .collect::<KResult<Vec<_>>>()?,
                )
                .with("excluded_lap_counts", run_exclusions)
                .with(
                    "tire_age_phase_availability",
                    Dict::new()
                        .with("fresh", unavailable)
                        .with("settled", unavailable)
                        .with("worn", unavailable),
                )
                .with(
                    "observational_phase_semantics",
                    Dict::new()
                        .with("early", early)
                        .with("middle", middle)
                        .with("late", late),
                )
                .with("zones", zones),
        ));
    }

    let set = |result: &mut Dict, key: &str, value: &str| {
        result.insert(key, value);
    };
    set(
        &mut result,
        "status",
        if any_usable {
            "usable"
        } else if any_phase_data {
            "limited"
        } else {
            "unavailable"
        },
    );
    set(
        &mut result,
        "confirmed_tire_age_status",
        if any_usable_confirmed {
            "usable"
        } else {
            "unavailable"
        },
    );
    set(
        &mut result,
        "observational_run_phase_status",
        if any_usable_observational {
            "usable"
        } else {
            "unavailable"
        },
    );
    result.insert("runs", run_items);
    if !any_phase_data {
        result.insert(
            "reason",
            "No run had enough eligible samples in a detected load zone.",
        );
    }
    Ok(PyValue::Dict(result))
}

/// `new_set_confirmed` of one run: every tyre's age confirmed and the first eligible lap's
/// start at most 0.25 green-lap equivalents on all four.
fn new_set_confirmed(age_context: &Dict, age_by_lap: &AgeByLap) -> KResult<bool> {
    let confirmed = match age_context.get("confirmed_zero_age_for_all_corners") {
        Some(value) => value.truthy()?,
        None => false,
    };
    if !confirmed {
        return Ok(false);
    }
    let first = age_by_lap.first_lap().and_then(|lap| age_by_lap.get(lap));
    for tire in TIRES {
        let start = match first.and_then(|bounds| bounds.get(tire)) {
            Some(details) if details.truthy()? => finite_value(get(details, "start")?)?,
            _ => None,
        };
        if !start.is_some_and(|value| value <= 0.25) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// The native `analysis_engine._corner_tire_age_summary(table, laps, runs, track_profile)`.
pub fn corner_tire_age_summary_entry(
    session: &Session,
    call: &mut Call,
) -> Result<PyValue, KernelError> {
    const ENTRY: &str = "analysis_engine._corner_tire_age_summary";
    let bound = call.bind(ENTRY, &["table", "laps", "runs", "track_profile"], &[])?;
    bound.require_table(call, "table")?;
    let laps = items(bound.require(call, "laps")?, "laps")?;
    let runs = items(bound.require(call, "runs")?, "runs")?;
    let track_profile = bound.require(call, "track_profile")?;
    let table = Table::of(session)?;
    let basis = SteeringBasis::from_value(session.steering_basis())?;
    let result = corner_tire_age_summary(&table, &basis, laps, runs, track_profile)?;
    table.check()?;
    Ok(result)
}
