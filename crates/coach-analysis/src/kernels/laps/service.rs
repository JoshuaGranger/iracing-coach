//! `analysis_engine._service_events(table)`.

use coach_numerics as num;

use super::super::error::KernelError;
use super::super::table::{Raw, Table};
use super::super::value::{Dict, PyValue};
use super::{int_or, int_saturating, py_max, py_max2, round, round3, sample_rate};

const TIRES: [&str; 4] = ["LF", "RF", "LR", "RR"];
const PIT_SERVICE_BITS: [(&str, i64); 7] = [
    ("LF_tire_change_requested", 0x01),
    ("RF_tire_change_requested", 0x02),
    ("LR_tire_change_requested", 0x04),
    ("RR_tire_change_requested", 0x08),
    ("fuel_fill_requested", 0x10),
    ("windshield_tearoff_requested", 0x20),
    ("fast_repair_requested", 0x40),
];

/// `max(1, int(sample_rate * factor))`.
fn samples_for(rate: f64, factor: f64) -> Result<i64, KernelError> {
    Ok(int_saturating(rate * factor)?.max(1))
}

/// `_finite(values[index])` of a column read with `table.get`; every index here is in range
/// (the oracle never indexes from the end).
fn at(raw: &Raw<'_>, index: i64) -> Result<Option<f64>, KernelError> {
    let index = usize::try_from(index)
        .ok()
        .filter(|&i| i < raw.len())
        .ok_or_else(|| KernelError::internal(format!("sample index {index} out of range")))?;
    raw.finite(index)
}

pub fn service_events(table: &Table) -> Result<PyValue, KernelError> {
    // table.get records each alias it finds; single samples are read from the raw columns as
    // the oracle reads them, the fuel rises from the finite view it builds.
    let times = table.get(&["SessionTime"], PyValue::None);
    table.get(&["OnPitRoad"], PyValue::Bool(false));
    table.get(&["PlayerCarInPitStall"], PyValue::Bool(false));
    table.get(&["PitstopActive"], PyValue::Bool(false));
    let service_flags = table.get(&["PitSvFlags"], PyValue::Int(0));
    let fuel = table.get(&["FuelLevel"], PyValue::None);
    let requested_fuel = table.get(&["PitSvFuel"], PyValue::None);
    let mut tire_use = Vec::new();
    for tire in TIRES {
        let channel = format!("{tire}TiresUsed");
        if table.has(&[&channel]) {
            tire_use.push((tire, table.get(&[&channel], PyValue::None)));
        }
    }
    let mut tire_odometers = Vec::new();
    for tire in TIRES {
        let channel = format!("{tire}odometer");
        if table.has(&[&channel]) {
            tire_odometers.push((tire, table.get(&[&channel], PyValue::None)));
        }
    }
    let rate = sample_rate(&table.meta().metadata)?;
    let bridge = samples_for(rate, 2.5)?;
    let stalled = table.booleans_for(&table.key(&["PlayerCarInPitStall"], PyValue::Bool(false)));
    let servicing = table.booleans_for(&table.key(&["PitstopActive"], PyValue::Bool(false)));
    let pitting = table.booleans_for(&table.key(&["OnPitRoad"], PyValue::Bool(false)));
    let fuel_levels = table.finite_for(&table.key(&["FuelLevel"], PyValue::None));
    // Per sample: in stall, or service active, or a >0.10 fuel rise from the previous sample
    // while on pit road.
    let mut groups: Vec<Vec<i64>> = Vec::new();
    for index in 0..table.len() {
        let fuel_jump = index > 0
            && matches!((fuel_levels.get(index - 1), fuel_levels.get(index)), (Some(before), Some(current)) if current - before > 0.10);
        if !(stalled[index] || servicing[index] || (fuel_jump && pitting[index])) {
            continue;
        }
        let index = index as i64;
        match groups.last_mut() {
            Some(group) if index - group[group.len() - 1] <= bridge => group.push(index),
            _ => groups.push(vec![index]),
        }
    }
    // `if not hit_indices: return []` comes before the oracle evaluates the lookahead and flag
    // windows, which can overflow where the bridge did not.
    if groups.is_empty() {
        table.check()?;
        return Ok(PyValue::List(Vec::new()));
    }
    let last_sample = table.len() as i64 - 1;
    let lookahead_samples = samples_for(rate, 5.0)?;
    let flag_samples = samples_for(rate, 2.0)?;
    let mut events = Vec::with_capacity(groups.len());
    for group in &groups {
        let (start, end) = (group[0], group[group.len() - 1]);
        let (start_time, end_time) = (at(&times, start)?, at(&times, end)?);
        let before = 0.max(start - 1);
        let fuel_before = at(&fuel, before)?;
        let lookahead = last_sample.min(end.saturating_add(lookahead_samples));
        let fuel_after = at(&fuel, lookahead)?;
        let mut tire_counters = Dict::new();
        let mut odometer_evidence = Dict::new();
        let mut observed_tire_changes: Vec<&str> = Vec::new();
        for (tire, values) in &tire_use {
            let before_count = at(values, before)?;
            let after_count = at(values, lookahead)?;
            let delta = match (before_count, after_count) {
                (Some(b), Some(a)) => Some(a - b),
                _ => None,
            };
            tire_counters.insert(
                *tire,
                Dict::new()
                    .with("before", round(before_count, 0)?)
                    .with("after", round(after_count, 0)?)
                    .with("delta", round(delta, 0)?),
            );
            if delta.is_some_and(|d| d >= 1.0) {
                observed_tire_changes.push(tire);
            }
        }
        for (tire, values) in &tire_odometers {
            let before_distance = at(values, before)?;
            let after_distance = at(values, lookahead)?;
            let reset =
                matches!((before_distance, after_distance), (Some(b), Some(a)) if a < b - 10.0);
            odometer_evidence.insert(
                *tire,
                Dict::new()
                    .with("before_m", round(before_distance, 1)?)
                    .with("after_m", round(after_distance, 1)?)
                    .with("reset_observed", reset),
            );
            if reset && !observed_tire_changes.contains(tire) {
                observed_tire_changes.push(tire);
            }
        }
        let flag_start = 0.max(start.saturating_sub(flag_samples));
        let mut requested_fuel_values = Vec::new();
        // The bits from the low 64 bits (exact for these masks), the value from every flag.
        let mut combined_service_flags: i64 = 0;
        let mut raw_flags: Vec<f64> = Vec::new();
        // range(flag_start, lookahead + 1) and service_flags[flag_start : lookahead + 1]
        for index in flag_start..=lookahead {
            requested_fuel_values.extend(at(&requested_fuel, index)?);
            // int(_finite(raw_flag) or 0)
            if let Some(flag) = at(&service_flags, index)?
                && flag != 0.0
            {
                combined_service_flags |= num::int_from_float_low64(flag)?;
                raw_flags.push(flag);
            }
        }
        let fuel_added = match (fuel_before, fuel_after) {
            (Some(b), Some(a)) => Some(py_max2(0.0, a - b)),
            _ => None,
        };
        let requested_service: Dict = PIT_SERVICE_BITS
            .iter()
            .map(|&(name, bit)| {
                (
                    PyValue::from(name),
                    PyValue::Bool(combined_service_flags & bit != 0),
                )
            })
            .collect();
        observed_tire_changes.sort_unstable();
        let confirmation = if observed_tire_changes.is_empty() {
            "not_observed"
        } else {
            "counter_and_or_odometer_reset"
        };
        events.push(PyValue::Dict(
            Dict::new()
                .with("start_index", start)
                .with("end_index", end)
                .with("start_time", round3(start_time)?)
                .with("end_time", round3(end_time)?)
                .with("fuel_added_l", round3(fuel_added)?)
                .with(
                    "requested_fuel_add_l",
                    round3(py_max(&requested_fuel_values))?,
                )
                .with("pit_service_flags", int_or(&raw_flags))
                .with("requested_service", requested_service)
                .with("tire_use_counters", tire_counters)
                .with("tire_odometer_evidence", odometer_evidence)
                .with(
                    "tires_changed_observed",
                    PyValue::List(
                        observed_tire_changes
                            .into_iter()
                            .map(PyValue::from)
                            .collect(),
                    ),
                )
                .with("tire_change_confirmation", confirmation),
        ));
    }
    table.check()?;
    Ok(PyValue::List(events))
}
