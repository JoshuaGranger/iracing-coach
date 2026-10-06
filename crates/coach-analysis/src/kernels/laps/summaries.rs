//! `analysis_engine._lap_summaries(table)`.

use std::collections::BTreeMap;

use coach_numerics as num;

use super::super::error::KernelError;
use super::super::table::{SteeringBasis, Table, caution_flag_series, dt_series};
use super::super::value::{Dict, PyValue};
use super::conditions::LapConditions;
use super::dynamics::VehicleDynamics;
use super::{int_value, mean, py_max, py_max2, py_min, round, round3, sample_rate};

const CAUTION_FLAGS: i64 = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000;
const CHECKERED_FLAG: i64 = 0x0001;
const WHITE_FLAG: i64 = 0x0002;
const BLACK_FLAG: i64 = 0x0001_0000;
const TRAFFIC_RULE: &str = "car ahead within max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) for at least 10% of observed lap time";
/// `2**53`: below it `lap + 1` of an integral float is exact.
const EXACT_INTEGERS: f64 = 9_007_199_254_740_992.0;

/// Python truthiness of a normalised sample (`x or 0.0`).
fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    }
}

/// `_racing_state(number)`: `int(number or -1) == 4`.
fn racing_state(number: Option<f64>) -> bool {
    matches!(number, Some(x) if x != 0.0 && x.trunc() == 4.0)
}

/// `_on_track(location)`: `location is not None and int(location) == 3`.
fn on_track(location: Option<f64>) -> bool {
    matches!(location, Some(x) if x.trunc() == 3.0)
}

/// `CLOSE_TRAFFIC_FRACTION`: the close-traffic screen's share of a lap.
const CLOSE_TRAFFIC_FRACTION: f64 = 0.10;

/// What `_traffic_sides` adds to `clean_context`: each side's fraction of the observed lap and the
/// side the traffic was on.
#[derive(Debug, PartialEq)]
struct TrafficSides {
    ahead: Option<f64>,
    behind: Option<f64>,
    side: Option<&'static str>,
}

/// `_traffic_sides(near_time, ahead_time, behind_time, observed_time)`: each time over the
/// observed time, `_round(.., 4)`; the side from the rounded fractions (`None` without traffic,
/// `"both"` when each side alone reaches the screen's 10 %, else the side that does, else the
/// larger share, `"both"` when the shares are equal).
fn traffic_sides(
    near_time: f64,
    ahead_time: f64,
    behind_time: f64,
    observed_time: f64,
) -> Result<TrafficSides, KernelError> {
    // _round(time / observed_time if observed_time > 0.0 else None, 4)
    let fraction = |time: f64| -> Result<Option<f64>, KernelError> {
        if observed_time > 0.0 {
            let share = time / observed_time;
            if share.is_finite() {
                return Ok(Some(num::round_ndigits(share, 4)?));
            }
        }
        Ok(None)
    };
    let (near, ahead, behind) = (
        fraction(near_time)?,
        fraction(ahead_time)?,
        fraction(behind_time)?,
    );
    let side = match (near, ahead, behind) {
        // `if near and ...`: a zero or missing fraction names no side.
        (Some(near), Some(ahead), Some(behind)) if near != 0.0 => {
            let ahead_close = ahead >= CLOSE_TRAFFIC_FRACTION;
            let behind_close = behind >= CLOSE_TRAFFIC_FRACTION;
            Some(if ahead_close && behind_close {
                "both"
            } else if ahead_close || behind_close {
                if ahead_close { "ahead" } else { "behind" }
            } else if ahead != behind {
                if ahead > behind { "ahead" } else { "behind" }
            } else {
                "both"
            })
        }
        _ => None,
    };
    Ok(TrafficSides {
        ahead,
        behind,
        side,
    })
}

/// `_count_corrections(values, threshold)` over finite values.
fn count_corrections(values: &[f64], threshold: f64) -> i64 {
    let mut signs: Vec<i8> = Vec::new();
    let mut previous: Option<f64> = None;
    for &current in values {
        if let Some(before) = previous {
            let delta = current - before;
            if delta.abs() >= threshold {
                signs.push(if delta > 0.0 { 1 } else { -1 });
            }
        }
        previous = Some(current);
    }
    signs.windows(2).filter(|pair| pair[0] != pair[1]).count() as i64
}

/// A lap number: `int(lap_value)` of a finite non-negative sample, held as the integral float
/// (exact, ordered as the ints are); `-0.0` is lap 0.
fn lap_key(value: f64) -> u64 {
    // Non-negative integral floats order as their bit patterns do.
    (value.trunc() + 0.0).to_bits()
}

fn key_value(key: u64) -> f64 {
    f64::from_bits(key)
}

pub fn lap_summaries(table: &Table, basis: &SteeringBasis) -> Result<PyValue, KernelError> {
    // Each accessor records what its Python namesake records, in the oracle's order.
    let times_aliases: &[&str] = &["SessionTime", "SessionTimeOfDay"];
    table.get(times_aliases, PyValue::None);
    let session_times = table.normalized(times_aliases, PyValue::None);
    let laps = table.normalized(&["Lap"], PyValue::None);
    let has_completed_laps = table.has(&["LapCompleted"]);
    let max_completed_lap = if has_completed_laps {
        let completed = table.normalized(&["LapCompleted"], PyValue::None);
        let values: Vec<f64> = (0..completed.len())
            .filter_map(|i| completed.get(i))
            .collect();
        py_max(&values)
    } else {
        None
    };
    let lap_pct = table.normalized(&["LapDistPct"], PyValue::None);
    table.get(&["Speed"], PyValue::None);
    let speed = table.normalized(&["Speed"], PyValue::None);
    table.get(&["Throttle", "ThrottleRaw"], PyValue::None);
    let throttle = table.fractions(&["Throttle", "ThrottleRaw"], PyValue::None);
    table.get(&["Brake", "BrakeRaw"], PyValue::None);
    let brake = table.fractions(&["Brake", "BrakeRaw"], PyValue::None);
    let steering = table.steering_column(basis);
    let material_steering = basis.threshold(0.08);
    let correction_step = basis.threshold(0.025);
    let steering_mean_digits: i64 = if basis.road_wheel() { 4 } else { 3 };
    let lat_accel = table.normalized(&["LatAccel"], PyValue::None);
    let long_accel = table.normalized(&["LongAccel"], PyValue::None);
    let fuel = table.normalized(&["FuelLevel"], PyValue::None);
    let flags = caution_flag_series(table);
    // Read one sample per lap, as the oracle reads it from table.get.
    let last_lap_time = table.get(&["LapLastLapTime"], PyValue::None);
    let session_state = table.normalized(&["SessionState"], PyValue::None);
    let has_session_state = table.has(&["SessionState"]);
    let pit = table.booleans(&["OnPitRoad"], PyValue::Bool(false));
    let track_location = table.normalized(&["PlayerTrackSurface"], PyValue::None);
    let has_track_location = table.has(&["PlayerTrackSurface"]);
    let car_distance_ahead = table.normalized(&["CarDistAhead"], PyValue::None);
    let car_distance_behind = table.normalized(&["CarDistBehind"], PyValue::None);
    let has_traffic_distance = table.has(&["CarDistAhead", "CarDistBehind"]);
    let overall_position = table.normalized(&["PlayerCarPosition"], PyValue::None);
    let class_position = table.normalized(&["PlayerCarClassPosition"], PyValue::None);
    let rate = sample_rate(&table.meta().metadata)?;
    let dts = dt_series(&session_times, rate);

    // groups[int(lap_value)], visited by sorted(groups).
    let mut groups: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
    for index in 0..laps.len() {
        if let Some(value) = laps.get(index)
            && value >= 0.0
        {
            groups.entry(lap_key(value)).or_default().push(index);
        }
    }
    if !groups.values().any(|indices| indices.len() >= 2) {
        table.check()?;
        return Ok(PyValue::List(Vec::new()));
    }

    // _sdk_lap_time(lap_number, derived)
    let sdk_lap_time = |lap: f64, derived: f64| -> Result<Option<f64>, KernelError> {
        if lap.abs() >= EXACT_INTEGERS {
            return Ok(None); // lap + 1 is not an integral float: no such group
        }
        let Some(&last) = groups
            .get(&lap_key(lap + 1.0))
            .and_then(|following| following.last())
        else {
            return Ok(None);
        };
        Ok(last_lap_time
            .finite(last)?
            .filter(|&scored| scored > 0.0 && (scored - derived).abs() <= 1.0))
    };

    let dynamics = VehicleDynamics::new(table, &brake, &throttle);
    let conditions = LapConditions::new(table)?;

    let mut summaries = Vec::new();
    for (&key, indices) in &groups {
        if indices.len() < 2 {
            continue;
        }
        let lap_number = key_value(key);
        let (start, end) = (indices[0], indices[indices.len() - 1]);
        let (start_time, end_time) = (session_times.get(start), session_times.get(end));
        let lap_dts: Vec<f64> = indices.iter().map(|&i| dts[i]).collect();
        let duration = match (start_time, end_time) {
            (Some(a), Some(b)) => b - a,
            _ => num::sum_floats_from_int_zero(&lap_dts).to_f64(),
        };
        let scored_lap_time = sdk_lap_time(lap_number, duration)?;
        let lap_time_value = scored_lap_time.unwrap_or(duration);
        let lap_time_source = if scored_lap_time.is_some() {
            "sdk_scored"
        } else {
            "sample_derived"
        };

        // Sequential left folds in sample order: the same additions as `+=`.
        let mut caution_time = 0.0;
        let mut green_time = 0.0;
        let mut observed_flags: i64 = 0;
        let mut pit_time = 0.0;
        let mut racing_state_time = 0.0;
        let mut on_track_time = 0.0;
        let mut track_location_time = 0.0;
        let mut traffic_observed_time = 0.0;
        let mut traffic_proximity_time = 0.0;
        let mut traffic_ahead_time = 0.0;
        let mut traffic_behind_time = 0.0;
        let mut pit_entry = false;
        let mut pit_exit = false;
        let mut brake_energy = 0.0;
        let mut steering_work = 0.0;
        let mut lateral_exposure = 0.0;
        let mut overlap_time = 0.0;
        let mut pct_values = Vec::new();
        let mut speed_values = Vec::new();
        let mut brake_values = Vec::new();
        let mut throttle_values = Vec::new();
        let mut steering_values = Vec::new();
        let mut long_values = Vec::new();
        let mut position_values = Vec::new();
        let mut class_position_values = Vec::new();
        for (&i, &dt) in indices.iter().zip(&lap_dts) {
            let flag = flags[i];
            if flag & CAUTION_FLAGS != 0 {
                caution_time += dt;
            } else {
                green_time += dt;
            }
            observed_flags |= flag;
            if pit[i] {
                pit_time += dt;
            }
            if has_session_state && racing_state(session_state.get(i)) {
                racing_state_time += dt;
            }
            if has_track_location {
                let location = track_location.get(i);
                if location.is_some() {
                    track_location_time += dt;
                }
                if on_track(location) {
                    on_track_time += dt;
                }
            }
            // max(0.0, _finite(v) or 0.0)
            let positive_speed = match speed.get(i) {
                Some(v) if v > 0.0 => v,
                _ => 0.0,
            };
            if has_traffic_distance {
                traffic_observed_time += dt;
                // traffic_ahead[i], traffic_behind[i]; traffic_near[i] is either.
                let ahead = matches!(car_distance_ahead.get(i), Some(a) if a > 0.0 && a <= py_max2(25.0, positive_speed * 0.75));
                let behind = matches!(car_distance_behind.get(i), Some(b) if b > 0.0 && b <= py_max2(15.0, positive_speed * 0.40));
                if ahead || behind {
                    traffic_proximity_time += dt;
                }
                if ahead {
                    traffic_ahead_time += dt;
                }
                if behind {
                    traffic_behind_time += dt;
                }
            }
            let entering = if i == 0 {
                pit[0]
            } else {
                pit[i] && !pit[i - 1]
            };
            pit_entry |= entering;
            pit_exit |= i > 0 && pit[i - 1] && !pit[i];
            let brake_or_zero = or_zero(brake.get(i));
            let steering_abs = or_zero(steering.get(i)).abs();
            let lateral_abs = or_zero(lat_accel.get(i)).abs();
            // Per sample: b * v * dt, s * v * dt and la * dt, folded in order.
            brake_energy += (brake_or_zero * positive_speed) * dt;
            steering_work += (steering_abs * positive_speed) * dt;
            lateral_exposure += lateral_abs * dt;
            if brake_or_zero >= 0.08 && steering_abs >= material_steering {
                overlap_time += dt;
            }
            pct_values.extend(lap_pct.get(i));
            speed_values.extend(speed.get(i));
            brake_values.extend(brake.get(i));
            throttle_values.extend(throttle.get(i));
            steering_values.extend(steering.get(i));
            long_values.extend(long_accel.get(i));
            if let Some(v) = overall_position.get(i)
                && v > 0.0
            {
                position_values.push(v.trunc());
            }
            if let Some(v) = class_position.get(i)
                && v > 0.0
            {
                class_position_values.push(v.trunc());
            }
        }
        let running_time = py_max2(caution_time + green_time, 1e-9);
        let classified = if caution_time / running_time >= 0.5 {
            "caution"
        } else if green_time / running_time >= 0.5 {
            "green"
        } else {
            "mixed"
        };
        let distance_complete = match (py_min(&pct_values), py_max(&pct_values)) {
            (Some(low), Some(high)) => low <= 0.15 && high >= 0.85,
            _ => false,
        };
        let complete = if has_completed_laps && lap_number > 0.0 {
            // max_completed_lap >= lap_number: both integral or float, compared exactly.
            distance_complete && matches!(max_completed_lap, Some(m) if m >= lap_number)
        } else {
            distance_complete
        };
        let (fuel_start, fuel_end) = (fuel.get(start), fuel.get(end));
        let fuel_used = match (fuel_start, fuel_end) {
            (Some(first), Some(_)) => {
                let mut negative_drops = 0.0;
                let mut previous = first;
                for &i in &indices[1..] {
                    if let Some(current) = fuel.get(i) {
                        if current < previous {
                            negative_drops += previous - current;
                        }
                        previous = current;
                    }
                }
                Some(negative_drops)
            }
            _ => None,
        };
        let mut flag_states: Vec<PyValue> = Vec::new();
        if green_time > 0.0 {
            flag_states.push("green".into());
        }
        if caution_time > 0.0 {
            flag_states.push("yellow".into());
        }
        if observed_flags & BLACK_FLAG != 0 {
            flag_states.push("black".into());
        }
        if observed_flags & WHITE_FLAG != 0 {
            flag_states.push("white".into());
        }
        if observed_flags & CHECKERED_FLAG != 0 {
            flag_states.push("checkered".into());
        }
        let dynamics_summary = dynamics.summary(indices, &dts)?;
        let abs_steering: Vec<f64> = steering_values.iter().map(|v| v.abs()).collect();
        let position =
            |values: &[f64], pick: fn(&[f64]) -> Option<f64>| -> Result<PyValue, KernelError> {
                match pick(values) {
                    Some(v) => int_value(v),
                    None => Ok(PyValue::None),
                }
            };
        let first = |values: &[f64]| values.first().copied();
        let last = |values: &[f64]| values.last().copied();
        let sides = traffic_sides(
            traffic_proximity_time,
            traffic_ahead_time,
            traffic_behind_time,
            traffic_observed_time,
        )?;
        let clean_context = Dict::new()
            .with(
                "on_track_fraction",
                round(
                    (track_location_time > 0.0).then(|| on_track_time / track_location_time),
                    4,
                )?,
            )
            .with(
                "traffic_proximity_fraction",
                round(
                    (traffic_observed_time > 0.0)
                        .then(|| traffic_proximity_time / traffic_observed_time),
                    4,
                )?,
            )
            .with("traffic_screened", has_traffic_distance)
            .with("traffic_rule", TRAFFIC_RULE)
            .with("traffic_ahead_fraction", sides.ahead)
            .with("traffic_behind_fraction", sides.behind)
            .with("traffic_side", sides.side);
        let mph = 2.236936;
        let speed_summary = Dict::new()
            .with(
                "average_mph",
                round3(Some(or_zero(mean(&speed_values)?) * mph))?,
            )
            .with(
                "minimum_mph",
                round3(Some(py_min(&speed_values).unwrap_or(0.0) * mph))?,
            )
            .with(
                "maximum_mph",
                round3(Some(py_max(&speed_values).unwrap_or(0.0) * mph))?,
            );
        let controls = Dict::new()
            .with("brake_mean", round3(mean(&brake_values)?)?)
            .with("brake_max", round3(py_max(&brake_values))?)
            .with("throttle_mean", round3(mean(&throttle_values)?)?)
            .with(
                "steering_abs_mean_rad",
                round(mean(&abs_steering)?, steering_mean_digits)?,
            )
            .with("brake_energy_proxy", round3(Some(brake_energy))?)
            .with("steering_work_proxy", round3(Some(steering_work))?)
            .with("brake_steer_overlap_s", round3(Some(overlap_time))?)
            .with(
                "steering_corrections",
                count_corrections(&steering_values, correction_step),
            )
            .with(
                "lateral_g_exposure_proxy",
                round3(Some(lateral_exposure / 9.80665))?,
            )
            .with("long_accel_mean_mps2", round3(mean(&long_values)?)?);
        let fuel_summary = Dict::new()
            .with("start_l", round3(fuel_start)?)
            .with("end_l", round3(fuel_end)?)
            .with("used_l", round3(fuel_used)?)
            .with(
                "used_gal",
                round3(fuel_used.map(|used| used / 3.785411784))?,
            );
        let position_summary = Dict::new()
            .with("start", position(&position_values, first)?)
            .with("end", position(&position_values, last)?)
            .with("best", position(&position_values, py_min)?)
            .with("worst", position(&position_values, py_max)?)
            .with("class_start", position(&class_position_values, first)?)
            .with("class_end", position(&class_position_values, last)?);
        let summary = Dict::new()
            .with("lap", int_value(lap_number)?)
            .with("start_index", start as i64)
            .with("end_index", end as i64)
            .with("start_time", round3(start_time)?)
            .with("end_time", round3(end_time)?)
            .with("lap_time_s", round3(Some(lap_time_value))?)
            .with("lap_time_source", lap_time_source)
            .with("complete", complete)
            .with("flag_state", classified)
            .with("flag_states", PyValue::List(flag_states))
            .with("green_fraction", round3(Some(green_time / running_time))?)
            .with(
                "caution_fraction",
                round3(Some(caution_time / running_time))?,
            )
            .with("pit_time_s", round3(Some(pit_time))?)
            .with("pit_entry", pit_entry)
            .with("pit_exit", pit_exit)
            .with(
                "racing_state_fraction",
                round(
                    has_session_state.then(|| racing_state_time / running_time),
                    4,
                )?,
            )
            .with("clean_context", clean_context)
            .with("speed", speed_summary)
            .with("controls", controls)
            .with("vehicle_dynamics", dynamics_summary)
            .with("fuel", fuel_summary)
            .with("conditions", conditions.summary(indices)?)
            .with("position", position_summary);
        summaries.push(PyValue::Dict(summary));
    }
    table.check()?;
    Ok(PyValue::List(summaries))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corrections_count_sign_changes_of_material_steps() {
        assert_eq!(count_corrections(&[0.0, 0.1, 0.0, 0.1, 0.11], 0.025), 2);
        assert_eq!(count_corrections(&[], 0.025), 0);
    }

    #[test]
    fn racing_and_on_track_truncate() {
        assert!(racing_state(Some(4.7)));
        assert!(!racing_state(Some(0.0)));
        assert!(!racing_state(None));
        assert!(on_track(Some(3.2)));
        assert!(!on_track(Some(-3.0)));
    }

    #[test]
    fn traffic_sides_name_the_side_from_the_rounded_fractions() {
        let sides = |near: f64, ahead: f64, behind: f64| {
            traffic_sides(near, ahead, behind, 100.0).expect("finite fractions")
        };
        // No distance channels (nothing observed) or no traffic: no side.
        assert_eq!(
            traffic_sides(0.0, 0.0, 0.0, 0.0).unwrap(),
            TrafficSides {
                ahead: None,
                behind: None,
                side: None
            }
        );
        assert_eq!(
            sides(0.0, 0.0, 0.0),
            TrafficSides {
                ahead: Some(0.0),
                behind: Some(0.0),
                side: None
            }
        );
        // One side alone, both sides at the screen's 10 % (overlapping or not), the boundary.
        assert_eq!(sides(30.0, 30.0, 0.0).side, Some("ahead"));
        assert_eq!(sides(25.0, 0.0, 25.0).side, Some("behind"));
        assert_eq!(sides(35.0, 20.0, 15.0).side, Some("both"));
        assert_eq!(sides(12.0, 12.0, 12.0).side, Some("both"));
        assert_eq!(sides(10.0, 10.0, 10.0).side, Some("both"));
        assert_eq!(sides(14.0, 10.0, 9.0).side, Some("ahead"));
        // Only the union reaches 10 %, or the lap is under it: the larger share; equal is both.
        assert_eq!(sides(11.0, 6.0, 5.0).side, Some("ahead"));
        assert_eq!(sides(11.0, 4.0, 7.0).side, Some("behind"));
        assert_eq!(sides(3.0, 3.0, 0.0).side, Some("ahead"));
        assert_eq!(sides(8.0, 4.0, 4.0).side, Some("both"));
        // Rounded to four digits before comparing: 0.099996 is 0.1 and reaches the screen.
        let rounded = sides(19.9996, 9.9996, 9.9996);
        assert_eq!(
            (rounded.ahead, rounded.behind, rounded.side),
            (Some(0.1), Some(0.1), Some("both"))
        );
    }

    #[test]
    fn lap_keys_order_as_ints() {
        let mut keys = [lap_key(10.0), lap_key(2.7), lap_key(-0.0), lap_key(1e300)];
        keys.sort_unstable();
        let values: Vec<f64> = keys.iter().map(|&k| key_value(k)).collect();
        assert_eq!(values, [0.0, 2.0, 10.0, 1e300]);
        assert!(key_value(lap_key(-0.0)).is_sign_positive());
    }
}
