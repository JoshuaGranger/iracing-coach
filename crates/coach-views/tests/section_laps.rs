//! `InvestigationView.section_laps` (web round 3, R4): per detected section, the clean laps' section
//! times, their median and the three fastest and three slowest, on a projected synthetic race.

mod common;

use coach_views::project;
use common::*;
use serde_json::{Value, json};

/// Section-time scale per lap: lap `n` drives a lap of `30 + PACE[n]` seconds at an even pace, so a
/// section of fraction `f` takes `f * (30 + PACE[n])`.
/// (Three decimals ending in neither 0 nor 5, so no section time sits on a rounding tie.)
const PACE: [f64; 13] = [
    0.0, 0.401, 0.102, 0.903, 0.304, 0.707, 0.208, 0.601, 0.052, 0.809, 0.356, 0.503, 0.157,
];

fn race(sections: Value, eligible: &[bool; 12]) -> Value {
    let mut doc = analysis_doc("section-laps", &Options::default());
    for trace in doc["lap_traces"]["traces"].as_array_mut().unwrap() {
        let lap = trace["lap"].as_u64().unwrap() as usize;
        for point in trace["points"].as_array_mut().unwrap() {
            let pct = point["lap_pct"].as_f64().unwrap();
            point["session_time_s"] = json!(lap as f64 * 40.0 + pct * (30.0 + PACE[lap]));
        }
    }
    let ri = &mut doc["race_investigation"];
    // These vectors exercise legacy projector timing; current fixtures already carry
    // Python timings, which must be removed before substituting synthetic traces.
    ri.as_object_mut().unwrap().remove("section_laps");
    for lap in ri["laps"].as_array_mut().unwrap() {
        let n = lap["lap"].as_u64().unwrap() as usize;
        lap["eligible"] = json!(eligible[n - 1]);
    }
    ri["sections"]["sections"] = sections;
    doc
}

fn projected(doc: &Value) -> Value {
    let dir = scratch("section-laps");
    let input = write_doc(&dir, doc);
    let out = dir.join("out");
    project(&input, &out).unwrap();
    let inv = view(&out, "investigation.json");
    assert_valid("InvestigationView", &inv);
    inv
}

fn expected(lap: usize, fraction: f64) -> f64 {
    let seconds = fraction * (30.0 + PACE[lap]);
    format!("{seconds:.3}").parse().unwrap()
}

fn section(id: &str, start: f64, end: f64) -> Value {
    json!({"id": id, "label": id, "start_pct": start, "end_pct": end, "boundary_status": "detected_section", "provenance": "Detected."})
}

#[test]
fn recorded_iracing_sectors_time_the_complete_lap_for_each_session_type() {
    for session_type in ["Practice", "Qualify", "Race"] {
        let mut doc = race(
            json!([section("S1", 0.0, 0.4), section("S2", 0.4, 1.0)]),
            &[true; 12],
        );
        doc["identity"]["session_type"] = json!(session_type);
        for trace in doc["lap_traces"]["traces"].as_array_mut().unwrap() {
            let lap = trace["lap"].as_u64().unwrap() as usize;
            let start = lap as f64 * 40.0;
            trace["sector_boundaries"] = json!([
                {"lap_pct": 0.0, "session_time_s": start},
                {"lap_pct": 1.0, "session_time_s": start + 30.0 + PACE[lap]},
            ]);
        }
        let inv = projected(&doc);
        for entry in inv["section_laps"]["sections"].as_array().unwrap() {
            assert_eq!(entry["clean_lap_count"], 12, "{session_type}");
            for lap_entry in entry["fastest"].as_array().unwrap() {
                let lap = lap_entry["lap"].as_u64().unwrap() as usize;
                let fraction = if entry["section_id"] == "S1" {
                    0.4
                } else {
                    0.6
                };
                assert_eq!(
                    lap_entry["section_s"].as_f64(),
                    Some(expected(lap, fraction))
                );
            }
        }
    }
}

#[test]
fn section_laps_rank_the_clean_laps_around_their_median() {
    // Laps 10 to 12 are screened out by the backend; 1 to 9 are clean.
    let mut eligible = [true; 12];
    eligible[9..].fill(false);
    let inv = projected(&race(
        json!([section("A", 0.1, 0.4), section("B", 0.5, 0.75)]),
        &eligible,
    ));
    let sections = inv["section_laps"]["sections"].as_array().unwrap();
    assert_eq!(sections.len(), 2);
    let a = &sections[0];
    assert_eq!(a["section_id"], "A");
    assert_eq!(a["clean_lap_count"], 9);
    // Clean laps by pace: 8, 2, 6, 4, 1, 7, 5, 9, 3.
    let median = expected(1, 0.3);
    assert_eq!(a["median_s"].as_f64().unwrap(), median);
    let laps = |key: &str| -> Vec<u64> {
        a[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| e["lap"].as_u64().unwrap())
            .collect()
    };
    assert_eq!(laps("fastest"), [8, 2, 6]);
    assert_eq!(laps("slowest"), [3, 9, 5]);
    for entry in a["fastest"]
        .as_array()
        .unwrap()
        .iter()
        .chain(a["slowest"].as_array().unwrap())
    {
        let lap = entry["lap"].as_u64().unwrap() as usize;
        let seconds = expected(lap, 0.3);
        assert_eq!(entry["section_s"].as_f64().unwrap(), seconds, "lap {lap}");
        let delta: f64 = format!("{:.3}", seconds - median).parse().unwrap();
        assert_eq!(entry["delta_s"].as_f64().unwrap(), delta, "lap {lap}");
    }
    assert!(a["fastest"][0]["delta_s"].as_f64().unwrap() < 0.0);
    assert!(a["slowest"][0]["delta_s"].as_f64().unwrap() > 0.0);
    // A screened lap never appears, however fast (lap 12 would be third fastest).
    assert!(!laps("fastest").contains(&12) && !laps("slowest").contains(&10));
    assert_eq!(sections[1]["section_id"], "B");
    assert_eq!(sections[1]["fastest"].as_array().unwrap().len(), 3);
}

#[test]
fn few_clean_laps_split_without_overlap_and_bad_bounds_are_skipped() {
    let mut eligible = [false; 12];
    // Laps 1 to 5 clean; by pace 2, 4, 1, 5, 3: two fastest, two slowest, lap 1 the median.
    eligible[..5].fill(true);
    let inv = projected(&race(
        json!([
            section("A", 0.1, 0.4),
            section("X", 0.6, 0.5),
            section("C", 0.2, 0.3)
        ]),
        &eligible,
    ));
    let sections = inv["section_laps"]["sections"].as_array().unwrap();
    let ids: Vec<&str> = sections
        .iter()
        .map(|s| s["section_id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, ["A", "C"]);
    let a = &sections[0];
    assert_eq!(a["clean_lap_count"], 5);
    assert_eq!(a["fastest"].as_array().unwrap().len(), 2);
    assert_eq!(a["slowest"].as_array().unwrap().len(), 2);
    assert_eq!(a["median_s"].as_f64().unwrap(), expected(1, 0.3));
}

#[test]
fn no_clean_lap_gives_a_section_with_no_median() {
    let inv = projected(&race(json!([section("A", 0.1, 0.4)]), &[false; 12]));
    let a = &inv["section_laps"]["sections"][0];
    assert_eq!(a["clean_lap_count"], 0);
    assert!(a["median_s"].is_null());
    assert_eq!(a["fastest"], json!([]));
    assert_eq!(a["slowest"], json!([]));
}

#[test]
fn backend_observed_population_passes_through_without_retiming_or_screening() {
    let mut analysis = race(json!([section("A", 0.1, 0.4)]), &[false; 12]);
    let backend = json!({"population":"observed_green_racing_laps","future_optional":{"source":"python"},
        "sections":[{"section_id":"A","observed_lap_count":1,"clean_lap_count":0,"lap_numbers":[10],
        "median_s":123.456,"median_lap":10,"fastest":[],"slowest":[]}]});
    analysis["race_investigation"]["section_laps"] = backend.clone();
    let served = projected(&analysis);
    assert_eq!(served["section_laps"], backend);
}
