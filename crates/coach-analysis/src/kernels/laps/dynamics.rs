//! `analysis_engine._vehicle_dynamics_summary` with `_dynamics_samples`, `_absolute_summary`
//! and `_positive_increase`, as `_lap_summaries` calls them (the per-table `_samples` always
//! given: the wheel-speed channels it tests with `in table.channels` are the ones
//! `_vehicle_dynamics_summary` finds with `table.has`).

use std::rc::Rc;

use coach_numerics as num;

use super::super::error::KernelError;
use super::super::table::{Table, View};
use super::super::value::{Dict, PyValue};
use super::{
    mean, median, median_of_sorted, percentile, percentile_of_sorted, py_max, py_min, round,
    round_num, round3,
};

const TIRES: [&str; 4] = ["LF", "RF", "LR", "RR"];
const ANGULAR_SPECS: [(&str, &str); 3] = [
    ("YawRate", "yaw_rate_deg_s"),
    ("PitchRate", "pitch_rate_deg_s"),
    ("RollRate", "roll_rate_deg_s"),
];

/// The table's columns `_vehicle_dynamics_summary` reads, resolved once (each accessor records
/// what the per-lap Python call records; `_lap_summaries` calls it for at least one lap).
pub(super) struct VehicleDynamics {
    /// Tyres with a wheel-speed channel, in TIRES order, and their wheel/vehicle ratios.
    ratios: Vec<(&'static str, Rc<View>)>,
    odometers: Vec<(&'static str, Rc<View>)>,
    brake: Rc<View>,
    throttle: Rc<View>,
    angular: Vec<(&'static str, Rc<View>)>,
    torque: Option<Rc<View>>,
    abs_active: Rc<Vec<bool>>,
    abs_cut: Rc<View>,
    has_abs: bool,
    brake_bias: Option<Rc<View>>,
}

impl VehicleDynamics {
    pub(super) fn new(table: &Table, brake: &Rc<View>, throttle: &Rc<View>) -> VehicleDynamics {
        let speed_key = table.key(&["Speed"], PyValue::None);
        let mut ratios = Vec::new();
        for tire in TIRES {
            let channel = format!("{tire}speed");
            if table.has(&[&channel]) {
                table.get(&[&channel], PyValue::None);
                let wheel_key = table.key(&[&channel], PyValue::None);
                ratios.push((tire, table.wheel_ratios(&speed_key, &wheel_key)));
            }
        }
        let mut odometers = Vec::new();
        for tire in TIRES {
            let channel = format!("{tire}odometer");
            if table.has(&[&channel]) {
                odometers.push((tire, table.normalized(&[&channel], PyValue::None)));
            }
        }
        let mut angular = Vec::new();
        for (channel, label) in ANGULAR_SPECS {
            if table.has(&[channel]) {
                angular.push((label, table.normalized(&[channel], PyValue::None)));
            }
        }
        let torque = table
            .has(&["SteeringWheelTorque"])
            .then(|| table.normalized(&["SteeringWheelTorque"], PyValue::None));
        let abs_active = table.booleans(&["BrakeABSactive"], PyValue::Bool(false));
        let abs_cut = table.fractions(&["BrakeABScutPct"], PyValue::None);
        let has_abs = table.has(&["BrakeABSactive", "BrakeABScutPct"]);
        let brake_bias = table
            .has(&["dcBrakeBias"])
            .then(|| table.normalized(&["dcBrakeBias"], PyValue::None));
        VehicleDynamics {
            ratios,
            odometers,
            brake: Rc::clone(brake),
            throttle: Rc::clone(throttle),
            angular,
            torque,
            abs_active,
            abs_cut,
            has_abs,
            brake_bias,
        }
    }

    /// `_vehicle_dynamics_summary(table, indices, dts, ..., _samples=...)` for one lap.
    pub(super) fn summary(&self, indices: &[usize], dts: &[f64]) -> Result<Dict, KernelError> {
        let mut result = Dict::new();
        let values_of =
            |view: &View| -> Vec<f64> { indices.iter().filter_map(|&i| view.get(i)).collect() };

        if !self.ratios.is_empty() {
            let mut ratio_summary = Dict::new();
            for (tire, view) in &self.ratios {
                let values = values_of(view);
                if values.is_empty() {
                    continue;
                }
                let ordered = num::sorted_floats(&values, false);
                ratio_summary.insert(
                    *tire,
                    Dict::new()
                        .with("p05", round(percentile_of_sorted(&ordered, 0.05)?, 4)?)
                        .with("median", round(Some(median_of_sorted(&ordered)), 4)?)
                        .with("p95", round(percentile_of_sorted(&ordered, 0.95)?, 4)?),
                );
            }
            let mut distance = Dict::new();
            for (tire, view) in &self.odometers {
                let value = round(positive_increase(&values_of(view)), 1)?;
                if !value.is_none() {
                    distance.insert(*tire, value);
                }
            }
            let mut valid_samples: i64 = 0;
            let (mut lock, mut front_lock, mut rear_spin) = (0.0, 0.0, 0.0);
            let mut present = Vec::with_capacity(4);
            let mut front = Vec::with_capacity(2);
            let mut rear = Vec::with_capacity(2);
            for &i in indices {
                present.clear();
                front.clear();
                rear.clear();
                for (tire, view) in &self.ratios {
                    if let Some(ratio) = view.get(i) {
                        present.push(ratio);
                        if matches!(*tire, "LF" | "RF") {
                            front.push(ratio);
                        } else {
                            rear.push(ratio);
                        }
                    }
                }
                if present.is_empty() {
                    continue;
                }
                valid_samples += 1;
                let dt = dts[i];
                let braking = or_zero(self.brake.get(i)) >= 0.15;
                if braking && py_min(&present).is_some_and(|m| m <= -0.08) {
                    lock += dt;
                }
                if braking && py_min(&front).is_some_and(|m| m <= -0.08) {
                    front_lock += dt;
                }
                // _mean of finite ratios: statistics.fmean(list) is fsum(list) / len(list).
                if or_zero(self.throttle.get(i)) >= 0.35
                    && !rear.is_empty()
                    && num::fsum(&rear)? / rear.len() as f64 >= 0.08
                {
                    rear_spin += dt;
                }
            }
            let mut channels: Vec<String> = self
                .ratios
                .iter()
                .map(|(tire, _)| format!("{tire}speed"))
                .collect();
            channels.sort();
            result.insert(
                "wheel_speed",
                Dict::new()
                    .with(
                        "channels",
                        PyValue::List(channels.into_iter().map(PyValue::Str).collect()),
                    )
                    .with("valid_ratio_samples", valid_samples)
                    .with("ratio_vs_vehicle_speed", ratio_summary)
                    .with("tire_distance_m", distance)
                    .with("braking_wheel_lock_proxy_s", round3(Some(lock))?)
                    .with("front_wheel_lock_proxy_s", round3(Some(front_lock))?)
                    .with("rear_wheelspin_proxy_s", round3(Some(rear_spin))?),
            );
        }

        let mut motion = Dict::new();
        let degrees_per_radian = 180.0 / std::f64::consts::PI;
        for (label, view) in &self.angular {
            if let Some(summary) = absolute_summary(&values_of(view), degrees_per_radian, 3)? {
                motion.insert(*label, summary);
            }
        }
        if let Some(view) = &self.torque
            && let Some(summary) = absolute_summary(&values_of(view), 1.0, 3)?
        {
            motion.insert("steering_torque_nm", summary);
        }
        if !motion.is_empty() {
            result.insert("motion", motion);
        }

        // The original summed with the builtin sum(); so does this.
        let active_dts: Vec<f64> = indices
            .iter()
            .filter(|&&i| self.abs_active[i])
            .map(|&i| dts[i])
            .collect();
        let abs_time = num::sum_floats_from_int_zero(&active_dts);
        let cut_values = values_of(&self.abs_cut);
        if self.has_abs {
            result.insert(
                "abs",
                Dict::new()
                    .with("active_s", round_num(abs_time, 3)?)
                    .with("cut_mean", round3(mean(&cut_values)?)?)
                    .with("cut_max", round3(py_max(&cut_values))?),
            );
        }

        if let Some(view) = &self.brake_bias {
            let values = values_of(view);
            if !values.is_empty() {
                result.insert(
                    "brake_bias_setting",
                    Dict::new()
                        .with("minimum", round(py_min(&values), 4)?)
                        .with("median", round(median(&values)?, 4)?)
                        .with("maximum", round(py_max(&values), 4)?),
                );
            }
        }
        Ok(result)
    }
}

/// Python truthiness of a normalised sample (`x or 0.0`).
fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    }
}

/// `_positive_increase(numbers)` over finite numbers in sample order.
fn positive_increase(numbers: &[f64]) -> Option<f64> {
    let mut total: Option<f64> = None;
    for pair in numbers.windows(2) {
        let (previous, current) = (pair[0], pair[1]);
        if current >= previous {
            // reduce(add, increases, 0.0): the fold starts at 0.0.
            total = Some(total.unwrap_or(0.0) + (current - previous));
        }
    }
    total
}

/// `_absolute_summary(finite_numbers, scale=scale, digits=digits)`.
fn absolute_summary(
    finite_numbers: &[f64],
    scale: f64,
    digits: i64,
) -> Result<Option<Dict>, KernelError> {
    let numbers: Vec<f64> = finite_numbers.iter().map(|x| x.abs() * scale).collect();
    if numbers.is_empty() {
        return Ok(None);
    }
    Ok(Some(
        Dict::new()
            .with("abs_mean", round(mean(&numbers)?, digits)?)
            .with("abs_p95", round(percentile(&numbers, 0.95)?, digits)?)
            .with("abs_max", round(py_max(&numbers), digits)?),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn positive_increase_folds_rises_from_zero() {
        assert_eq!(positive_increase(&[1.0, 3.0, 2.0, 2.0, 5.0]), Some(5.0));
        assert_eq!(positive_increase(&[3.0, 2.0]), None);
        assert_eq!(positive_increase(&[4.0]), None);
    }
}
