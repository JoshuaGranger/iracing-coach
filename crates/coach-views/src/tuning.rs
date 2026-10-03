//! `tuning.json`: the analysis identity, map and eligible runs Progressive Tuning needs (port of
//! `RuntimeMapper.MapTuningIdentity` and `MapTuningMap`, `ProgressiveTuningCoordinator.Candidate`
//! and `TuningPage.EligibleRuns`). No math: every value is a backend field or a mapping rule.

use serde_json::{Value, json};

use crate::geometry::points;
use crate::laps::context_eligible;
use crate::val::{Obj, arr, flag, hashes, int, int_of, nonblank, num, obj, text, text_of};

pub(crate) struct TuningInputs<'a> {
    pub analysis_id: &'a str,
    pub identity: &'a Obj,
    /// `source`, reduced to `selection` and `fingerprints`.
    pub source: Option<&'a Obj>,
    pub geometry: Option<&'a Obj>,
    /// `track_profile`, reduced to `shape` and `detected_corner_segments`.
    pub profile: Option<&'a Obj>,
    pub tuning_map: Option<&'a Obj>,
    pub runs: &'a [Value],
}

const MISSING_EMBEDDED: &str = "The recording does not contain an embedded setup tree.";
const MISSING_FINGERPRINT: &str = "The embedded setup fingerprint is unavailable.";
const MISSING_IDENTITY: &str = "The exact analysis or track-configuration identity is unavailable.";
const NOT_OPEN: &str = "This fixed-setup recording may supply driving evidence, but it cannot be the target of a garage recommendation.";
const DETECTED_HINT: &str = "Verify the label and three bounds against the official track map before using this as a named corner.";
const UNVERIFIED_MESSAGE: &str =
    "Detected turn labels and bounds are not official until the driver verifies them.";

pub(crate) fn projection(input: &TuningInputs) -> Value {
    let (identity, facts) = identity(input);
    let open = facts.setup_type.eq_ignore_ascii_case("open");
    let exact_identity =
        !input.analysis_id.trim().is_empty() && (facts.has_configuration_key || facts.has_layout);
    let embedded = facts.embedded;
    let fingerprint = facts.fingerprint;
    let mut reasons: Vec<&str> = Vec::new();
    if !embedded {
        reasons.push(MISSING_EMBEDDED);
    }
    if !fingerprint {
        reasons.push(MISSING_FINGERPRINT);
    }
    if !exact_identity {
        reasons.push(MISSING_IDENTITY);
    }
    if !open {
        reasons.push(NOT_OPEN);
    }
    json!({
        "analysis_id": input.analysis_id,
        "eligible": open && embedded && fingerprint && exact_identity,
        "can_use_as_evidence": true,
        "reasons": reasons,
        "identity": identity,
        "map": map_view(input, facts.configuration_key.as_deref()),
        "representative_runs": representative_runs(input.runs),
    })
}

struct IdentityFacts {
    setup_type: String,
    embedded: bool,
    fingerprint: bool,
    has_configuration_key: bool,
    has_layout: bool,
    configuration_key: Option<String>,
}

fn identity(input: &TuningInputs) -> (Value, IdentityFacts) {
    let id = input.identity;
    let selection = input.source.and_then(|s| obj(s, "selection"));
    let geometry = input.geometry;

    let session_text = |map: &Obj, key: &str| -> Option<String> {
        let value = map.get(key)?;
        match value {
            Value::String(s) if !s.trim().is_empty() => Some(s.clone()),
            Value::Number(_) => int_of(value)
                .map(|i| i.to_string())
                .or_else(|| text_of(value)),
            _ => None,
        }
    };
    let iracing_session_id = session_text(id, "session_id")
        .or_else(|| selection.and_then(|s| session_text(s, "sim_session_num")));
    let session_unique_id = int(id, "session_unique_id");
    let subsession_id =
        int(id, "subsession_id").or_else(|| selection.and_then(|s| int(s, "subsession_id")));
    let session_type = selection
        .and_then(|s| nonblank(s, "sim_session_type"))
        .or_else(|| nonblank(id, "event_type"));
    let configuration_key = geometry
        .and_then(|g| nonblank(g, "track_configuration_key"))
        .or_else(|| nonblank(id, "track_configuration_key"));
    let track = display_track(
        nonblank(id, "track_name")
            .or_else(|| nonblank(id, "track_path"))
            .as_deref(),
    );
    let layout =
        Some(display_layout(text(id, "track_config").as_deref())).filter(|l| !l.is_empty());
    let car_path = nonblank(id, "car_path");
    let car = display_car(
        nonblank(id, "car_name")
            .or_else(|| car_path.clone())
            .as_deref(),
    );
    let setup_type = match flag(id, "is_fixed_setup") {
        Some(true) => "Fixed".to_string(),
        Some(false) => "Open".to_string(),
        None => nonblank(id, "setup_type")
            .and_then(|s| humanize(&s))
            .unwrap_or_else(|| "Unknown".into()),
    };
    let setup_fingerprint = nonblank(id, "setup_fingerprint");
    let embedded = flag(id, "embedded_setup_available") == Some(true)
        || int(id, "setup_parameter_count").is_some_and(|n| n > 0)
        || obj(id, "setup").is_some_and(|setup| !setup.is_empty());

    let legacy = geometry
        .map(|g| hashes(arr(g, "source_sha256")))
        .unwrap_or_default();
    let contributing = geometry
        .map(|g| hashes(arr(g, "contributing_source_sha256")))
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| legacy.clone());
    let observed = geometry
        .map(|g| hashes(arr(g, "observed_source_sha256")))
        .filter(|h| !h.is_empty())
        .unwrap_or(legacy);
    let fingerprints = input.source.map_or_else(Vec::new, |s| {
        hashes(
            arr(s, "fingerprints")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|f| f.get("sha256")),
        )
    });
    let mut source_sha256: Vec<String> = contributing
        .into_iter()
        .chain(observed)
        .chain(fingerprints)
        .collect();
    source_sha256.sort();
    source_sha256.dedup();

    let value = json!({
        "analysis_id": input.analysis_id,
        "iracing_session_id": iracing_session_id,
        "session_unique_id": session_unique_id,
        "subsession_id": subsession_id,
        "session_type": session_type,
        "track_configuration_key": configuration_key,
        "track_id": int(id, "track_id"),
        "track": track,
        "layout": layout,
        "car_id": int(id, "car_id"),
        "car_path": car_path,
        "car": car,
        "setup_type": setup_type,
        "setup_fingerprint": setup_fingerprint,
        "embedded_setup_available": embedded,
        "source_sha256": source_sha256,
    });
    let facts = IdentityFacts {
        setup_type,
        embedded,
        fingerprint: setup_fingerprint.is_some(),
        has_configuration_key: configuration_key.is_some(),
        has_layout: layout.is_some(),
        configuration_key,
    };
    (value, facts)
}

fn midpoint(start: f64, end: f64) -> f64 {
    let wrap = |x: f64| ((x % 1.0) + 1.0) % 1.0;
    let (start, end) = (wrap(start), wrap(end));
    let distance = if end >= start {
        end - start
    } else {
        1.0 - start + end
    };
    (start + distance / 2.0) % 1.0
}

fn supplied_points(map: &Obj, key: &str) -> Vec<Value> {
    arr(map, key)
        .iter()
        .filter_map(Value::as_object)
        .filter_map(|p| {
            let x = num(p, "x")?;
            let y = num(p, "y")?;
            let pct = num(p, "lap_pct")
                .or_else(|| num(p, "lap_percent"))
                .unwrap_or(0.0);
            Some(json!({"lap_pct": pct, "x": x, "y": y}))
        })
        .collect()
}

fn map_view(input: &TuningInputs, configuration_key: Option<&str>) -> Value {
    let supplied = input.tuning_map;
    let mut supplied_path = supplied.map_or_else(Vec::new, |m| supplied_points(m, "path"));
    if let Some(m) = supplied {
        supplied_path.extend(supplied_points(m, "main_path"));
    }
    let geometry_path = input
        .geometry
        .map(|g| points(g, "main_path"))
        .unwrap_or_default();
    let path: Vec<Value> = if supplied_path.len() > 1 {
        supplied_path.clone()
    } else if geometry_path.len() > 1 {
        let last = (geometry_path.len() - 1) as f64;
        geometry_path
            .iter()
            .enumerate()
            .map(|(i, p)| json!({"lap_pct": p.pct.unwrap_or(i as f64 / last), "x": p.x, "y": p.y}))
            .collect()
    } else {
        input
            .profile
            .map_or_else(Vec::new, |p| supplied_points(p, "shape"))
    };

    let mut turns: Vec<Value> = Vec::new();
    if let Some(m) = supplied {
        for (index, turn) in arr(m, "turns").iter().chain(arr(m, "corners")).enumerate() {
            let Some(turn) = turn.as_object() else {
                continue;
            };
            let start = num(turn, "start_pct").unwrap_or(0.0);
            let end = num(turn, "end_pct").unwrap_or(0.0);
            turns.push(json!({
                "corner_id": nonblank(turn, "corner_id").or_else(|| nonblank(turn, "id")).unwrap_or_else(|| format!("turn-{}", index + 1)),
                "label": nonblank(turn, "label").unwrap_or_else(|| format!("Turn {}", index + 1)),
                "start_pct": start,
                "apex_pct": num(turn, "apex_pct").unwrap_or_else(|| midpoint(start, end)),
                "end_pct": end,
                "is_official": flag(turn, "is_official") == Some(true),
                "confidence": nonblank(turn, "confidence").unwrap_or_else(|| "Unknown".into()),
                "correction_hint": nonblank(turn, "correction_hint"),
                "user_verified": false,
                "verification_source": "",
            }));
        }
    }
    if turns.is_empty()
        && let Some(profile) = input.profile
    {
        for segment in arr(profile, "detected_corner_segments")
            .iter()
            .filter_map(Value::as_object)
        {
            let number = int(segment, "segment").unwrap_or(0);
            let start = num(segment, "start_pct").unwrap_or(0.0);
            let end = num(segment, "end_pct").unwrap_or(0.0);
            turns.push(json!({
                "corner_id": format!("detected-{number}"),
                "label": nonblank(segment, "label").unwrap_or_else(|| format!("Load zone {number}")),
                "start_pct": start,
                "apex_pct": midpoint(start, end),
                "end_pct": end,
                "is_official": false,
                "confidence": "Detected",
                "correction_hint": DETECTED_HINT,
                "user_verified": false,
                "verification_source": "",
            }));
        }
    }

    let has_supplied_map = supplied.is_some() && supplied_path.len() > 1;
    let field = |key: &str| supplied.and_then(|m| nonblank(m, key));
    let map_identity = field("map_identity")
        .or_else(|| field("map_id"))
        .or_else(|| configuration_key.map(str::to_string))
        .unwrap_or_else(|| format!("analysis:{}", input.analysis_id));
    json!({
        "map_identity": map_identity,
        "source_type": field("source_type").unwrap_or_else(|| "telemetry-derived".into()),
        "source_label": field("source_label").unwrap_or_else(|| {
            if has_supplied_map { "Verified track map" } else { "Recorded racing geometry" }.into()
        }),
        "source_url": field("source_url"),
        "confidence": field("confidence").unwrap_or_else(|| {
            if path.len() > 1 { "Recorded" } else { "Unavailable" }.into()
        }),
        "verification_message": field("verification_message")
            .or_else(|| (!has_supplied_map).then(|| UNVERIFIED_MESSAGE.to_string())),
        "is_verified": supplied.and_then(|m| flag(m, "is_verified")) == Some(true),
        "path": path,
        "turns": turns,
        "geometry_hash": field("geometry_hash").or_else(|| input.geometry.and_then(|g| nonblank(g, "geometry_hash"))),
    })
}

/// Runs a driver may pick: damage-repair context eligible and at least six non-negative
/// `coaching_reference_lap_numbers`, ascending `run_number`.
fn representative_runs(runs: &[Value]) -> Vec<Value> {
    let mut eligible: Vec<(i64, &Value)> = runs
        .iter()
        .filter_map(|value| {
            let run = value.as_object()?;
            let references = arr(run, "coaching_reference_lap_numbers")
                .iter()
                .filter_map(int_of)
                .filter(|n| *n >= 0)
                .count();
            (context_eligible(obj(run, "damage_repair_context")) && references >= 6)
                .then(|| (int(run, "run_number").unwrap_or(0), value))
        })
        .collect();
    eligible.sort_by_key(|(number, _)| *number);
    eligible.into_iter().map(|(_, run)| run.clone()).collect()
}

// ---- display names (RuntimeMapper.DisplayTrack, DisplayLayout, DisplayCar, Humanize) ----

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// .NET `ToTitleCase`: each word is capitalized and lowered, except all-uppercase words (acronyms).
fn title_case(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut word = String::new();
    let flush = |word: &mut String, out: &mut String| {
        if word.is_empty() {
            return;
        }
        let has_lower = word.chars().any(char::is_lowercase);
        let has_upper = word.chars().any(char::is_uppercase);
        if !has_lower && has_upper {
            out.push_str(word);
        } else {
            let mut chars = word.chars();
            if let Some(first) = chars.next() {
                out.extend(first.to_uppercase());
                out.extend(chars.flat_map(char::to_lowercase));
            }
        }
        word.clear();
    };
    for c in text.chars() {
        if c.is_alphanumeric() || c == '\'' {
            word.push(c);
        } else {
            flush(&mut word, &mut out);
            out.push(c);
        }
    }
    flush(&mut word, &mut out);
    out
}

pub(crate) fn humanize(value: &str) -> Option<String> {
    if value.trim().is_empty() {
        return None;
    }
    let mut spaced = String::with_capacity(value.len() + 4);
    let mut previous: Option<char> = None;
    for c in value
        .chars()
        .map(|c| if c == '_' || c == '-' { ' ' } else { c })
    {
        if let Some(p) = previous
            && (p.is_ascii_lowercase() || p.is_ascii_digit())
            && c.is_ascii_uppercase()
        {
            spaced.push(' ');
        }
        spaced.push(c);
        previous = Some(c);
    }
    Some(title_case(&spaced))
}

fn alphanumeric_lower(value: &str) -> String {
    value
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .map(|c| c.to_ascii_lowercase())
        .collect()
}

/// Removes `\b(19|20)\d{2}\b` year tokens.
fn strip_years(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut word = String::new();
    let flush = |word: &mut String, out: &mut String| {
        let year = word.len() == 4
            && word.bytes().all(|b| b.is_ascii_digit())
            && (word.starts_with("19") || word.starts_with("20"));
        if !year {
            out.push_str(word);
        }
        word.clear();
    };
    for c in value.chars() {
        if is_word(c) {
            word.push(c);
        } else {
            flush(&mut word, &mut out);
            out.push(c);
        }
    }
    flush(&mut word, &mut out);
    out
}

pub(crate) fn display_track(value: Option<&str>) -> Option<String> {
    let value = value.filter(|v| !v.trim().is_empty())?;
    let normalized = alphanumeric_lower(value);
    if normalized.contains("kentucky") {
        return Some("Kentucky Speedway".into());
    }
    if normalized.contains("newhampshire") {
        return Some("New Hampshire Motor Speedway".into());
    }
    let mut friendly = strip_years(value).trim().to_string();
    let tail_start = friendly.rfind(|c: char| !is_word(c)).map_or(0, |i| {
        i + friendly[i..].chars().next().map_or(1, char::len_utf8)
    });
    let tail = friendly[tail_start..].to_lowercase();
    if matches!(tail.as_str(), "oval" | "road" | "short" | "full") {
        friendly.truncate(tail_start);
    }
    humanize(friendly.trim())
}

pub(crate) fn display_layout(value: Option<&str>) -> String {
    match value.filter(|v| !v.trim().is_empty()) {
        None => String::new(),
        Some(value) => humanize(strip_years(value).trim()).unwrap_or_default(),
    }
}

pub(crate) fn display_car(value: Option<&str>) -> Option<String> {
    let value = value.filter(|v| !v.trim().is_empty())?;
    let normalized = alphanumeric_lower(value);
    for (needle, name) in [
        ("toyotatundra2022", "Toyota Tundra TRD Pro"),
        ("supra2019", "Toyota Supra Class B"),
        ("acuraarx06gtp", "Acura ARX-06 GTP"),
        ("dallarap217", "Dallara P217"),
        ("porsche992rgt3", "Porsche 911 GT3 R (992)"),
    ] {
        if normalized.contains(needle) {
            return Some(name.into());
        }
    }
    let unified = value.replace('\\', "/");
    let mut leaf = unified
        .split('/')
        .rfind(|s| !s.is_empty())
        .unwrap_or(value)
        .to_string();
    for prefix in ["trucks", "stockcars2", "stockcars", "road", "dirt"] {
        if leaf
            .get(..prefix.len())
            .is_some_and(|head| head.eq_ignore_ascii_case(prefix))
        {
            let rest = &leaf[prefix.len()..];
            let trimmed = rest.trim_start();
            if trimmed.len() < rest.len() {
                leaf = trimmed.to_string();
                break;
            }
        }
    }
    let bytes = leaf.as_bytes();
    if bytes.len() >= 5 {
        let split = bytes.len() - 4;
        if bytes[split..].iter().all(u8::is_ascii_digit) && bytes[split - 1].is_ascii_alphabetic() {
            leaf.insert(split, ' ');
        }
    }
    humanize(&leaf)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn display_names_follow_the_c_sharp_rules() {
        assert_eq!(
            display_track(Some("kentucky 2020 oval")).as_deref(),
            Some("Kentucky Speedway")
        );
        assert_eq!(
            display_track(Some("newhampshire")).as_deref(),
            Some("New Hampshire Motor Speedway")
        );
        assert_eq!(
            display_track(Some("synthetic_speedway oval")).as_deref(),
            Some("Synthetic Speedway")
        );
        assert_eq!(display_track(Some("iowa 2021")).as_deref(), Some("Iowa"));
        assert_eq!(display_track(Some("  ")), None);
        assert_eq!(display_layout(Some("Oval 2022")), "Oval");
        assert_eq!(display_layout(None), "");
        assert_eq!(
            display_car(Some("stockcars2 toyotagr86")).as_deref(),
            Some("Toyotagr86")
        );
        assert_eq!(
            display_car(Some("Toyota Supra 2019 Class B")).as_deref(),
            Some("Toyota Supra Class B")
        );
        assert_eq!(
            display_car(Some("stockcars chevyCamaro2022")).as_deref(),
            Some("Chevy Camaro 2022")
        );
        assert_eq!(
            humanize("pit_lane-entry").as_deref(),
            Some("Pit Lane Entry")
        );
        assert_eq!(humanize("GT3 racer").as_deref(), Some("GT3 Racer"));
    }

    fn inputs<'a>(identity: &'a Obj, runs: &'a [Value]) -> TuningInputs<'a> {
        TuningInputs {
            analysis_id: "abc",
            identity,
            source: None,
            geometry: None,
            profile: None,
            tuning_map: None,
            runs,
        }
    }

    #[test]
    fn open_setup_with_identity_is_eligible() {
        let id =
            json!({"is_fixed_setup": false, "setup_fingerprint": "f", "setup_parameter_count": 3,
            "track_config": "Oval", "track_name": "iowa"})
            .as_object()
            .unwrap()
            .clone();
        let view = projection(&inputs(&id, &[]));
        assert_eq!(view["eligible"], true);
        assert_eq!(view["reasons"], json!([]));
        assert_eq!(view["identity"]["setup_type"], "Open");
        assert_eq!(view["identity"]["layout"], "Oval");
    }

    #[test]
    fn fixed_setup_is_evidence_not_a_target() {
        let id = json!({"is_fixed_setup": true, "setup_fingerprint": "f", "embedded_setup_available": true, "track_config": "Oval"})
            .as_object()
            .unwrap()
            .clone();
        let view = projection(&inputs(&id, &[]));
        assert_eq!(view["eligible"], false);
        assert_eq!(view["can_use_as_evidence"], true);
        assert_eq!(view["reasons"], json!([NOT_OPEN]));
    }

    #[test]
    fn missing_identity_lists_every_gap_in_order() {
        let id = Obj::new();
        let view = projection(&inputs(&id, &[]));
        assert_eq!(
            view["reasons"],
            json!([
                MISSING_EMBEDDED,
                MISSING_FINGERPRINT,
                MISSING_IDENTITY,
                NOT_OPEN
            ])
        );
        assert_eq!(view["identity"]["setup_type"], "Unknown");
        assert_eq!(view["map"]["map_identity"], "analysis:abc");
        assert_eq!(view["map"]["confidence"], "Unavailable");
    }

    #[test]
    fn source_hashes_are_the_sorted_union() {
        let id = Obj::new();
        let a = "a".repeat(64);
        let b = "B".repeat(64);
        let geometry = json!({"source_sha256": [a], "observed_source_sha256": [b], "track_configuration_key": "k"})
            .as_object()
            .unwrap()
            .clone();
        let source = json!({"fingerprints": [{"sha256": "c".repeat(64)}, {"sha256": "short"}]})
            .as_object()
            .unwrap()
            .clone();
        let mut input = inputs(&id, &[]);
        input.geometry = Some(&geometry);
        input.source = Some(&source);
        let view = projection(&input);
        assert_eq!(
            view["identity"]["source_sha256"],
            json!(["a".repeat(64), "b".repeat(64), "c".repeat(64)])
        );
        assert_eq!(view["identity"]["track_configuration_key"], "k");
    }

    #[test]
    fn detected_segments_become_unverified_turns() {
        let id = Obj::new();
        let profile =
            json!({"detected_corner_segments": [{"segment": 2, "start_pct": 0.9, "end_pct": 0.1}],
            "shape": [{"lap_pct": 0.0, "x": 1.0, "y": 2.0}, {"lap_pct": 0.5, "x": 2.0, "y": 2.0}]})
            .as_object()
            .unwrap()
            .clone();
        let mut input = inputs(&id, &[]);
        input.profile = Some(&profile);
        let view = projection(&input);
        let turn = &view["map"]["turns"][0];
        assert_eq!(turn["corner_id"], "detected-2");
        assert_eq!(turn["label"], "Load zone 2");
        assert!((turn["apex_pct"].as_f64().unwrap() - 0.0).abs() < 1e-9);
        assert_eq!(turn["confidence"], "Detected");
        assert_eq!(view["map"]["path"].as_array().unwrap().len(), 2);
        assert_eq!(view["map"]["verification_message"], UNVERIFIED_MESSAGE);
    }

    #[test]
    fn a_supplied_tuning_map_wins_over_the_recorded_geometry() {
        let id = Obj::new();
        let supplied = json!({
            "map_id": "official-oval", "source_type": "official-iracing", "source_label": "iRacing map",
            "confidence": "High", "is_verified": true, "geometry_hash": "h",
            "path": [{"lap_percent": 0.25, "x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}, {"x": null, "y": 1.0}],
            "corners": [{"id": "t1", "start_pct": 0.1, "end_pct": 0.3, "is_official": true, "confidence": "High"}]
        })
        .as_object()
        .unwrap()
        .clone();
        let geometry = json!({"main_path": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}], "geometry_hash": "g"})
            .as_object()
            .unwrap()
            .clone();
        let mut input = inputs(&id, &[]);
        input.tuning_map = Some(&supplied);
        input.geometry = Some(&geometry);
        let map = projection(&input)["map"].clone();
        assert_eq!(map["map_identity"], "official-oval");
        assert_eq!(map["source_label"], "iRacing map");
        assert_eq!(map["is_verified"], true);
        assert_eq!(map["geometry_hash"], "h");
        assert_eq!(
            map["path"],
            json!([{"lap_pct": 0.25, "x": 1.0, "y": 2.0}, {"lap_pct": 0.0, "x": 3.0, "y": 4.0}])
        );
        assert!(map["verification_message"].is_null());
        let turn = &map["turns"][0];
        assert_eq!(
            (turn["corner_id"].as_str(), turn["label"].as_str()),
            (Some("t1"), Some("Turn 1"))
        );
        assert!((turn["apex_pct"].as_f64().unwrap() - 0.2).abs() < 1e-12);
        assert_eq!(turn["is_official"], true);
        assert_eq!(turn["user_verified"], false);
    }

    #[test]
    fn representative_runs_need_six_references_and_a_clean_context() {
        let runs = vec![
            json!({"run_number": 3, "coaching_reference_lap_numbers": [1, 2, 3, 4, 5, 6]}),
            json!({"run_number": 1, "coaching_reference_lap_numbers": [1, 2, 3, 4, 5, 6, -1]}),
            json!({"run_number": 2, "coaching_reference_lap_numbers": [1, 2, 3, 4, 5]}),
            json!({"run_number": 4, "coaching_reference_lap_numbers": [1, 2, 3, 4, 5, 6],
                "damage_repair_context": {"reason_codes": ["tow"]}}),
        ];
        let picked = representative_runs(&runs);
        let numbers: Vec<i64> = picked
            .iter()
            .map(|r| r["run_number"].as_i64().unwrap())
            .collect();
        assert_eq!(numbers, [1, 3]);
    }
}
