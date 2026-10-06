//! `lap_trace_columns` (backlog #77): the traces at chart density. `traces.ccol` and the catalog's
//! channels come from them; the catalog's laps and sector times and the section times stay on the
//! 160-bin `lap_traces`, the traces the backend's own investigation reads.

mod common;

use std::fs;

use coach_views::{ProjectError, project, select_traces};
use common::*;
use serde_json::{Map, Value, json};

const DENSE: usize = 640;

fn r6(value: f64) -> f64 {
    (value * 1e6).round() / 1e6
}

/// Each 160-bin point becomes the four 640-bin points inside it, each value moved by its index so
/// the four differ (integers stay integers, missing stays missing).
fn with_columns(mut doc: Value) -> Value {
    let traces = doc["lap_traces"]["traces"].as_array().unwrap().clone();
    let keys = [
        "lap_pct",
        "session_time_s",
        "speed_mph",
        "throttle",
        "brake",
        "steering_rad",
        "steering_abs_peak_rad",
        "slip_angle_deg",
        "yaw_rate_deg_s",
        "lateral_g",
        "longitudinal_g",
        "gear",
        "rpm",
        "latitude",
        "longitude",
        "tire_stress_proxy",
    ];
    let shifted = |value: &Value, k: usize| match value {
        Value::Number(n) if n.is_i64() => json!(n.as_i64().unwrap() + k as i64),
        Value::Number(n) => json!(n.as_f64().unwrap() + k as f64 * 0.25),
        other => other.clone(),
    };
    let dense: Vec<Value> = traces
        .iter()
        .map(|trace| {
            let points = trace["points"].as_array().unwrap();
            let mut columns: Map<String, Value> = Map::new();
            let mut extras: Map<String, Value> = Map::new();
            let ids: Vec<String> = points
                .iter()
                .flat_map(|p| p["additional_signals"].as_object().unwrap().keys().cloned())
                .fold(Vec::new(), |mut acc, id| {
                    if !acc.contains(&id) {
                        acc.push(id);
                    }
                    acc
                });
            for key in keys {
                let mut column = Vec::new();
                for point in points {
                    let bin = (point["lap_pct"].as_f64().unwrap() * 160.0).floor() as usize;
                    for k in 0..4 {
                        column.push(if key == "lap_pct" {
                            json!(r6((bin as f64 * 4.0 + k as f64 + 0.5) / DENSE as f64))
                        } else {
                            shifted(&point[key], k)
                        });
                    }
                }
                columns.insert(key.into(), Value::Array(column));
            }
            for id in &ids {
                let mut column = Vec::new();
                for point in points {
                    for k in 0..4 {
                        column.push(
                            point["additional_signals"]
                                .get(id)
                                .map_or(Value::Null, |v| shifted(v, k)),
                        );
                    }
                }
                extras.insert(id.clone(), Value::Array(column));
            }
            json!({
                "lap": trace["lap"],
                "points": points.len() * 4,
                "columns": columns,
                "additional_signals": extras,
            })
        })
        .collect();
    doc["lap_trace_columns"] = json!({
        "schema_version": 1,
        "distance_bins_per_lap": DENSE,
        "definition": "x",
        "traces": dense,
        "unknown_columns_field": {"ignored": true},
    });
    doc
}

/// The dense columns as a `lap_traces` document, for `assert_ccol_matches`.
fn columns_as_points(doc: &Value) -> Value {
    let traces: Vec<Value> = doc["lap_trace_columns"]["traces"]
        .as_array()
        .unwrap()
        .iter()
        .map(|trace| {
            let columns = trace["columns"].as_object().unwrap();
            let extras = trace["additional_signals"].as_object().unwrap();
            let count = columns["lap_pct"].as_array().unwrap().len();
            let points: Vec<Value> = (0..count)
                .map(|i| {
                    let mut point: Map<String, Value> = columns
                        .iter()
                        .map(|(key, values)| (key.clone(), values[i].clone()))
                        .collect();
                    let additional: Map<String, Value> = extras
                        .iter()
                        .filter(|(_, values)| !values[i].is_null())
                        .map(|(id, values)| (id.clone(), values[i].clone()))
                        .collect();
                    point.insert("additional_signals".into(), Value::Object(additional));
                    Value::Object(point)
                })
                .collect();
            json!({"lap": trace["lap"], "points": points})
        })
        .collect();
    json!({"lap_traces": {"distance_bins_per_lap": DENSE, "traces": traces}})
}

fn project_into(doc: &Value, name: &str) -> (std::path::PathBuf, coach_views::ProjectionSummary) {
    let dir = scratch(name);
    let input = write_doc(&dir, doc);
    let out = dir.join("views").join("a").join("p4");
    let summary = project(&input, &out).unwrap();
    (out, summary)
}

fn options(laps: usize) -> Options {
    Options {
        laps,
        ..Options::default()
    }
}

#[test]
fn the_ccol_and_its_channels_come_from_the_dense_columns() {
    let base = analysis_doc("dense", &options(14));
    let doc = with_columns(base.clone());
    let (out, summary) = project_into(&doc, "dense");
    assert_eq!(summary.bins_per_lap, DENSE as u32);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["bins_per_lap"], json!(DENSE));
    assert_valid("TraceCatalog", &catalog);
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    assert_eq!(ccol.header["bins"], json!(DENSE));
    assert_ccol_matches(&ccol, &catalog, &columns_as_points(&doc), None);
    // gear moved by an integer stays an integer channel; a float channel stays f64
    let dtype = |id: &str| {
        catalog["signals"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["id"] == id)
            .unwrap()["dtype"]
            .clone()
    };
    assert_eq!(dtype("gear"), json!("u8"));
    assert_eq!(dtype("speed"), json!("f64"));
}

#[test]
fn laps_sector_times_and_section_times_stay_on_the_160_bin_traces() {
    let base = analysis_doc("same", &options(14));
    let (plain, _) = project_into(&base, "plain");
    let (dense, _) = project_into(&with_columns(base), "dense-same");
    let a = view(&plain, "traces-catalog.json");
    let b = view(&dense, "traces-catalog.json");
    assert_eq!(a["laps"], b["laps"]);
    assert!(
        a["laps"][0]["sector_times_s"]
            .as_array()
            .is_some_and(|t| !t.is_empty())
    );
    assert_eq!(a["sector_start_pcts"], b["sector_start_pcts"]);
    assert_eq!(
        a["signals"].as_array().unwrap().len(),
        b["signals"].as_array().unwrap().len()
    );
    assert_eq!(a["bins_per_lap"], json!(160));
    assert_eq!(
        view(&plain, "investigation.json")["section_laps"],
        view(&dense, "investigation.json")["section_laps"]
    );
    for file in ["race.json", "runs.json", "track.json", "tuning.json"] {
        assert_eq!(view(&plain, file), view(&dense, file), "{file}");
    }
}

#[test]
fn six_laps_of_the_eight_default_channels_are_a_quarter_megabyte() {
    let doc = with_columns(analysis_doc("payload", &options(30)));
    let (out, _) = project_into(&doc, "payload");
    let catalog = view(&out, "traces-catalog.json");
    let defaults: Vec<&str> = catalog["signals"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| s["default"] == true)
        .map(|s| s["id"].as_str().unwrap())
        .collect();
    assert_eq!(defaults.len(), 8, "{defaults:?}");
    let body = select_traces(&out, Some("3-8"), Some(&defaults.join(",")), 16 << 20).unwrap();
    let ccol = decode_ccol(&body);
    assert_eq!(ccol.header["laps"], json!([3, 4, 5, 6, 7, 8]));
    // 6 laps x 640 bins x 8 channels of f64, plus the header: 245,760 bytes of values.
    let values = 6 * DENSE * 8 * 8;
    assert!(
        body.len() >= values && body.len() < values + 4096,
        "{} bytes",
        body.len()
    );
    let keep = |lap: i64| (3..=8).contains(&lap);
    assert_ccol_matches(&ccol, &catalog, &columns_as_points(&doc), Some(&keep));
}

#[test]
fn columns_whose_laps_differ_from_the_traces_fail() {
    let mut doc = with_columns(analysis_doc("laps", &options(6)));
    doc["lap_trace_columns"]["traces"]
        .as_array_mut()
        .unwrap()
        .pop();
    let dir = scratch("laps");
    let input = write_doc(&dir, &doc);
    let error = project(&input, &dir.join("views")).unwrap_err();
    assert!(
        matches!(&error, ProjectError::Json { path, .. } if path == "lap_trace_columns.traces"),
        "{error:?}"
    );
    assert!(!dir.join("views").exists());
}

#[test]
fn a_column_shorter_than_lap_pct_fails() {
    let mut doc = with_columns(analysis_doc("short", &options(4)));
    doc["lap_trace_columns"]["traces"][1]["columns"]["speed_mph"]
        .as_array_mut()
        .unwrap()
        .pop();
    let dir = scratch("short");
    let input = write_doc(&dir, &doc);
    let error = project(&input, &dir.join("views")).unwrap_err();
    let text = error.to_string();
    assert!(
        text.contains("lap 2") && text.contains("values for"),
        "{text}"
    );
}

#[test]
fn a_lap_fraction_outside_its_bin_grid_fails_naming_the_columns() {
    let mut doc = with_columns(analysis_doc("grid", &options(4)));
    let column = doc["lap_trace_columns"]["traces"][0]["columns"]["lap_pct"]
        .as_array_mut()
        .unwrap();
    column[1] = column[0].clone();
    let dir = scratch("grid");
    let input = write_doc(&dir, &doc);
    let error = project(&input, &dir.join("views")).unwrap_err();
    assert!(
        matches!(&error, ProjectError::Json { path, .. } if path.starts_with("lap_trace_columns.traces[0]")),
        "{error:?}"
    );
}

#[test]
fn null_columns_read_as_no_dense_traces() {
    let mut doc = analysis_doc("null", &options(4));
    doc["lap_trace_columns"] = Value::Null;
    let (out, summary) = project_into(&doc, "null");
    assert_eq!(summary.bins_per_lap, 160);
    assert_eq!(
        view(&out, "traces-catalog.json")["bins_per_lap"],
        json!(160)
    );
}
