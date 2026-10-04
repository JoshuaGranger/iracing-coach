//! Lap rows: the comparison-exclusion rule (`RuntimeMapper.LapComparisonExclusionReasons`) and the
//! joins that give each lap its run.

use std::collections::HashMap;

use serde_json::{Value, json};

use crate::ProjectError;
use crate::val::{Obj, arr, flag, int, int_of, num, obj, text};

#[derive(Debug)]
pub(crate) struct LapRecord {
    pub lap: i64,
    pub eligible: bool,
    pub reasons: Vec<String>,
    pub run_number: Option<i64>,
    pub run_age: Option<f64>,
}

/// `RuntimeMapper.ReasonCodes`: a string, or an array's non-null items, blanks dropped; any other
/// non-null value is one code, its JSON text.
pub(crate) fn reason_codes(context: Option<&Obj>, key: &str) -> Vec<String> {
    let Some(value) = context.and_then(|c| c.get(key)) else {
        return Vec::new();
    };
    match value {
        Value::Null => Vec::new(),
        Value::String(s) => {
            if s.trim().is_empty() {
                Vec::new()
            } else {
                vec![s.clone()]
            }
        }
        Value::Array(items) => items
            .iter()
            .filter_map(|item| match item {
                Value::Null => None,
                Value::String(s) => (!s.trim().is_empty()).then(|| s.clone()),
                other => Some(other.to_string()),
            })
            .collect(),
        other => vec![other.to_string()],
    }
}

/// `RuntimeMapper.ComparisonContextEligible`.
pub(crate) fn context_eligible(context: Option<&Obj>) -> bool {
    context.and_then(|c| flag(c, "automatic_coaching_reference_eligible")) != Some(false)
        && reason_codes(context, "exclusion_reason_codes").is_empty()
        && reason_codes(context, "reason_codes").is_empty()
}

/// `previous_flag` is the preceding lap's `flag_state`, trimmed and lowercased.
pub(crate) fn exclusion_reasons(lap: &Obj, previous_flag: Option<&str>) -> Vec<String> {
    let mut reasons: Vec<String> = Vec::new();
    let flag_state = text(lap, "flag_state")
        .map(|s| s.trim().to_lowercase())
        .unwrap_or_default();
    if flag(lap, "complete") != Some(true) {
        reasons.push("partial".into());
    }
    if flag_state != "green" {
        reasons.push("caution_or_mixed".into());
    }
    if num(lap, "pit_time_s").unwrap_or(0.0) >= 1.0 {
        reasons.push("pit".into());
    }
    if num(lap, "racing_state_fraction").is_some_and(|x| x < 0.98) {
        reasons.push("not_racing_state".into());
    }
    let clean = obj(lap, "clean_context");
    if clean
        .and_then(|c| num(c, "on_track_fraction"))
        .is_some_and(|x| x < 0.98)
    {
        reasons.push("off_track".into());
    }
    if clean
        .and_then(|c| num(c, "traffic_proximity_fraction"))
        .is_some_and(|x| x >= 0.10)
    {
        reasons.push("close_traffic".into());
    }
    if previous_flag == Some("caution") && flag_state == "green" {
        reasons.push("restart".into());
    }
    let context = obj(lap, "damage_repair_context");
    let codes = reason_codes(context, "exclusion_reason_codes");
    if context.and_then(|c| flag(c, "automatic_coaching_reference_eligible")) == Some(false)
        || !codes.is_empty()
    {
        if codes.is_empty() {
            reasons.push("damage_repair_context".into());
        } else {
            reasons.extend(codes);
        }
    }
    let mut seen: Vec<String> = Vec::new();
    reasons.retain(|reason| {
        let lower = reason.to_lowercase();
        if seen.contains(&lower) {
            false
        } else {
            seen.push(lower);
            true
        }
    });
    reasons
}

/// Laps in ascending `lap` order (stable) with the projector-computed fields, and their records.
pub(crate) fn build(
    laps: &[Value],
    runs: &[Value],
    investigation: Option<&Obj>,
) -> Result<(Vec<Value>, Vec<LapRecord>), ProjectError> {
    let mut rows: Vec<(i64, &Obj)> = Vec::with_capacity(laps.len());
    for (index, value) in laps.iter().enumerate() {
        let Some(map) = value.as_object() else {
            return Err(ProjectError::Json {
                path: format!("laps[{index}]"),
                message: "expected an object".into(),
            });
        };
        let Some(lap) = int(map, "lap") else {
            return Err(ProjectError::Json {
                path: format!("laps[{index}].lap"),
                message: "expected an integer".into(),
            });
        };
        rows.push((lap, map));
    }
    rows.sort_by_key(|(lap, _)| *lap);

    let mut run_of: HashMap<i64, i64> = HashMap::new();
    for run in runs.iter().filter_map(Value::as_object) {
        let Some(number) = int(run, "run_number") else {
            continue;
        };
        for lap in arr(run, "lap_numbers")
            .iter()
            .filter_map(int_of)
            .filter(|n| *n > 0)
        {
            run_of.entry(lap).or_insert(number);
        }
    }
    let mut age_of: HashMap<i64, f64> = HashMap::new();
    if let Some(investigation) = investigation {
        for entry in arr(investigation, "laps")
            .iter()
            .filter_map(Value::as_object)
        {
            if let (Some(lap), Some(age)) = (int(entry, "lap"), num(entry, "green_exposure")) {
                age_of.entry(lap).or_insert(age);
            }
        }
    }

    let mut out = Vec::with_capacity(rows.len());
    let mut records = Vec::with_capacity(rows.len());
    let mut previous: Option<String> = None;
    for (lap, map) in rows {
        let reasons = exclusion_reasons(map, previous.as_deref());
        previous = text(map, "flag_state").map(|s| s.trim().to_lowercase());
        let mut row = map.clone();
        row.insert("lap".into(), json!(lap));
        row.insert("comparison_eligible".into(), json!(reasons.is_empty()));
        row.insert("exclusion_reasons".into(), json!(reasons));
        let run_number = run_of.get(&lap).copied();
        let run_age = age_of.get(&lap).copied();
        if let Some(number) = run_number {
            row.entry("run_number").or_insert(json!(number));
        }
        if let Some(age) = run_age {
            row.entry("run_age_laps").or_insert(json!(age));
        }
        for (from, to) in [("pit_entry", "pit_in"), ("pit_exit", "pit_out")] {
            if let Some(value) = flag(map, from) {
                row.entry(to).or_insert(json!(value));
            }
        }
        records.push(LapRecord {
            lap,
            eligible: reasons.is_empty(),
            reasons,
            run_number,
            run_age,
        });
        out.push(Value::Object(row));
    }
    Ok((out, records))
}

/// Litres per US gallon, as the analyzer converts (`fuel.used_gal`).
const LITRES_PER_GALLON: f64 = 3.785411784;

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

/// One entry per run that ends in a pit service (`runs[].pit_service`), in run order: the lap the
/// stop belongs to (the run's `end_lap`), the service window, the tires the backend observed
/// changed, and the fuel added in litres and gallons. `tires_changed` is null when the backend did
/// not report the list; an empty list means none observed changed. `fuel_added_gal` is the
/// backend's litres over 3.785411784, rounded to 3 decimals as the analyzer rounds.
pub(crate) fn pit_stops(runs: &[Value]) -> Vec<Value> {
    let mut stops: Vec<(i64, Value)> = Vec::new();
    for run in runs.iter().filter_map(Value::as_object) {
        let Some(service) = obj(run, "pit_service").filter(|s| !s.is_empty()) else {
            continue;
        };
        let (start, end) = (num(service, "start_time"), num(service, "end_time"));
        let tires = service
            .get("tires_changed_observed")
            .and_then(Value::as_array)
            .map(|items| {
                let mut tires: Vec<String> = items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|t| t.trim().to_ascii_uppercase())
                    .filter(|t| matches!(t.as_str(), "LF" | "LR" | "RF" | "RR"))
                    .collect();
                tires.sort();
                tires.dedup();
                tires
            });
        let litres = num(service, "fuel_added_l");
        let number = int(run, "run_number");
        stops.push((
            number.unwrap_or(i64::MAX),
            json!({
                "run_number": number,
                "lap": int(run, "end_lap"),
                "start_time_s": start,
                "end_time_s": end,
                "service_s": start.zip(end).filter(|(a, b)| b >= a).map(|(a, b)| round3(b - a)),
                "tires_changed": tires,
                "fuel_added_l": litres,
                "fuel_added_gal": litres.map(|l| round3(l / LITRES_PER_GALLON)),
                "post_run_service": flag(run, "ended_with_post_run_service") == Some(true),
            }),
        ));
    }
    stops.sort_by_key(|(number, _)| *number);
    stops.into_iter().map(|(_, stop)| stop).collect()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn lap(value: Value) -> Obj {
        value.as_object().unwrap().clone()
    }

    #[test]
    fn green_complete_lap_has_no_reasons() {
        let l = lap(
            json!({"lap": 3, "complete": true, "flag_state": " Green ", "pit_time_s": 0.0,
            "racing_state_fraction": 1.0, "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0}}),
        );
        assert!(exclusion_reasons(&l, None).is_empty());
    }

    #[test]
    fn reasons_follow_the_documented_order() {
        let l = lap(
            json!({"lap": 3, "complete": false, "flag_state": "green", "pit_time_s": 1.0,
            "racing_state_fraction": 0.5,
            "clean_context": {"on_track_fraction": 0.9, "traffic_proximity_fraction": 0.1},
            "damage_repair_context": {"automatic_coaching_reference_eligible": false, "exclusion_reason_codes": ["tow", "TOW", null, " "]}}),
        );
        assert_eq!(
            exclusion_reasons(&l, Some("caution")),
            [
                "partial",
                "pit",
                "not_racing_state",
                "off_track",
                "close_traffic",
                "restart",
                "tow"
            ]
        );
    }

    #[test]
    fn damage_context_without_codes_is_one_reason() {
        let l = lap(json!({"complete": true, "flag_state": "green",
            "damage_repair_context": {"automatic_coaching_reference_eligible": false}}));
        assert_eq!(exclusion_reasons(&l, None), ["damage_repair_context"]);
    }

    #[test]
    fn missing_numbers_skip_their_tests() {
        let l =
            lap(json!({"complete": true, "flag_state": "green", "racing_state_fraction": null}));
        assert!(exclusion_reasons(&l, None).is_empty());
    }

    #[test]
    fn other_reason_values_are_their_json_text() {
        let ctx = lap(json!({"exclusion_reason_codes": {"a": 1}}));
        assert_eq!(
            reason_codes(Some(&ctx), "exclusion_reason_codes"),
            ["{\"a\":1}"]
        );
    }

    #[test]
    fn restart_needs_a_caution_before_and_green_now() {
        let l = lap(json!({"complete": true, "flag_state": "green"}));
        assert_eq!(exclusion_reasons(&l, Some("caution")), ["restart"]);
        assert!(exclusion_reasons(&l, Some("green")).is_empty());
    }

    #[test]
    fn build_sorts_stably_and_joins_runs() {
        let laps = vec![
            json!({"lap": 2, "complete": true, "flag_state": "caution", "pit_entry": true}),
            json!({"lap": 1, "complete": true, "flag_state": "green"}),
            json!({"lap": 3, "complete": true, "flag_state": "green"}),
        ];
        let runs = vec![json!({"run_number": 7, "lap_numbers": [1, 2, 3]})];
        let investigation = lap(json!({"laps": [{"lap": 1, "green_exposure": 1.0}]}));
        let (rows, records) = build(&laps, &runs, Some(&investigation)).unwrap();
        let numbers: Vec<i64> = records.iter().map(|r| r.lap).collect();
        assert_eq!(numbers, [1, 2, 3]);
        assert_eq!(rows[0]["run_number"], 7);
        assert_eq!(rows[0]["run_age_laps"], 1.0);
        assert_eq!(rows[1]["pit_in"], true);
        assert_eq!(rows[2]["exclusion_reasons"], json!(["restart"]));
    }

    #[test]
    fn a_lap_without_a_number_is_malformed() {
        let err = build(&[json!({"complete": true})], &[], None).unwrap_err();
        assert!(matches!(err, ProjectError::Json { .. }));
    }

    #[test]
    fn pit_stops_list_each_service_with_its_lap_tires_and_fuel() {
        let runs = vec![
            json!({"run_number": 2, "end_lap": 80, "ended_with_post_run_service": true,
                "pit_service": {"start_time": 3000.0, "end_time": 3004.5, "fuel_added_l": 0.0,
                    "tires_changed_observed": []}}),
            json!({"run_number": 1, "end_lap": 41,
                "pit_service": {"start_time": 2022.85, "end_time": 2035.85, "fuel_added_l": 45.402,
                    "tires_changed_observed": ["rr", "LF", "LR", "RF", "RF", "XX"]}}),
            json!({"run_number": 3, "end_lap": 90, "pit_service": {}}),
            json!({"run_number": 4, "end_lap": 99}),
            json!({"run_number": 5, "end_lap": 120,
                "pit_service": {"start_time": 5.0, "fuel_added_l": null}}),
        ];
        let stops = pit_stops(&runs);
        assert_eq!(stops.len(), 3, "an empty or absent service is not a stop");
        assert_eq!(stops[0]["run_number"], 1);
        assert_eq!(stops[0]["lap"], 41);
        assert_eq!(stops[0]["service_s"], 13.0);
        assert_eq!(stops[0]["tires_changed"], json!(["LF", "LR", "RF", "RR"]));
        assert_eq!(stops[0]["fuel_added_l"], 45.402);
        assert_eq!(stops[0]["fuel_added_gal"], 11.994);
        assert_eq!(stops[0]["post_run_service"], false);
        assert_eq!(stops[1]["run_number"], 2);
        assert_eq!(stops[1]["tires_changed"], json!([]));
        assert_eq!(stops[1]["fuel_added_gal"], 0.0);
        assert_eq!(stops[1]["post_run_service"], true);
        // missing values stay null, never zero
        assert_eq!(stops[2]["run_number"], 5);
        assert_eq!(stops[2]["tires_changed"], Value::Null);
        assert_eq!(stops[2]["fuel_added_l"], Value::Null);
        assert_eq!(stops[2]["fuel_added_gal"], Value::Null);
        assert_eq!(stops[2]["service_s"], Value::Null);
    }
}
