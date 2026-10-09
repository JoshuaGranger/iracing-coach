//! Bounded passthrough of the producer's median-minus-best section priorities. Computed provenance
//! is declared in the wire contract, as for ObservedTrend; no evidence keys are fabricated here.

use serde_json::{Value, json};

use crate::ProjectError;
use crate::val::{Obj, arr, int_of, num, obj, text};

fn invalid(path: String, message: &str) -> ProjectError {
    ProjectError::Json {
        path,
        message: message.into(),
    }
}

/// Lap-fraction slack for the half-overlap test, far below a trace bin (about 0.0016 lap).
const OVERLAP_TOLERANCE: f64 = 1e-9;

/// No name or index fallback: exactly one section must cover at least half the priority's span.
fn section_id(priority: &Obj, sections: &[Value]) -> Option<String> {
    let start = num(priority, "start_pct")?;
    let end = num(priority, "end_pct")?;
    if !(0.0 <= start && start < end && end <= 1.0) {
        return None;
    }
    let mut matched = None;
    for section in sections.iter().filter_map(Value::as_object) {
        let (Some(id), Some(a), Some(b)) = (
            text(section, "id"),
            num(section, "start_pct"),
            num(section, "end_pct"),
        ) else {
            continue;
        };
        if id.is_empty() || !(0.0 <= a && a < b && b <= 1.0) {
            continue;
        }
        let overlap = (end.min(b) - start.max(a)).max(0.0);
        // Lap fractions are decimals, so an exact half can round either side of it (0.5 - 0.3 is
        // 0.19999999999999998): a half within OVERLAP_TOLERANCE counts as a half.
        if overlap >= (end - start) * 0.5 - OVERLAP_TOLERANCE {
            if matched.is_some() {
                return None;
            }
            matched = Some(id.to_owned());
        }
    }
    matched
}

pub(crate) fn project(raw: Value, investigation: Option<&Obj>) -> Result<Value, ProjectError> {
    if raw.is_null() {
        return Ok(Value::Null);
    }
    let mut payload = raw
        .as_object()
        .cloned()
        .ok_or_else(|| invalid("pace_attribution".into(), "expected an object or null"))?;
    let version = payload
        .get("schema_version")
        .ok_or_else(|| ProjectError::MissingField("pace_attribution.schema_version".into()))?;
    if int_of(version) != Some(1) {
        return Err(ProjectError::IncompatibleVersion {
            field: "pace_attribution.schema_version",
            found: version.to_string(),
            supported: "1".into(),
        });
    }
    let raw_priorities = payload
        .remove("priorities")
        .ok_or_else(|| ProjectError::MissingField("pace_attribution.priorities".into()))?;
    let priorities = raw_priorities
        .as_array()
        .ok_or_else(|| invalid("pace_attribution.priorities".into(), "expected an array"))?;
    let sections = investigation
        .and_then(|i| obj(i, "sections"))
        .map_or(&[][..], |s| arr(s, "sections"));
    let mut projected = Vec::with_capacity(priorities.len());
    for (index, item) in priorities.iter().enumerate() {
        let path = format!("pace_attribution.priorities[{index}]");
        let mut priority = item
            .as_object()
            .cloned()
            .ok_or_else(|| invalid(path.clone(), "expected an object"))?;
        for key in [
            "start_pct",
            "end_pct",
            "recoverable_s",
            "best_lap",
            "tire_energy_share",
            "tire_energy_grade",
        ] {
            if !priority.contains_key(key) {
                return Err(ProjectError::MissingField(format!("{path}.{key}")));
            }
        }
        for key in ["start_pct", "end_pct", "recoverable_s"] {
            if num(&priority, key).is_none() {
                return Err(invalid(format!("{path}.{key}"), "expected a finite number"));
            }
        }
        if num(&priority, "recoverable_s").is_none_or(|v| v < 0.0) {
            return Err(invalid(
                format!("{path}.recoverable_s"),
                "expected nonnegative seconds",
            ));
        }
        if priority
            .get("best_lap")
            .and_then(int_of)
            .is_none_or(|lap| lap < 0)
        {
            return Err(invalid(
                format!("{path}.best_lap"),
                "expected a nonnegative lap integer",
            ));
        }
        let share = &priority["tire_energy_share"];
        if !share.is_null()
            && num(&priority, "tire_energy_share").is_none_or(|v| !(0.0..=1.0).contains(&v))
        {
            return Err(invalid(
                format!("{path}.tire_energy_share"),
                "expected a fraction or null",
            ));
        }
        let grade = &priority["tire_energy_grade"];
        if !grade.is_null() && !matches!(grade.as_str(), Some("slip_weighted" | "load_only")) {
            return Err(invalid(
                format!("{path}.tire_energy_grade"),
                "expected slip_weighted, load_only or null",
            ));
        }
        priority.insert("section_id".into(), json!(section_id(&priority, sections)));
        projected.push(priority);
    }
    projected.sort_by(|a, b| {
        num(b, "recoverable_s")
            .partial_cmp(&num(a, "recoverable_s"))
            .unwrap()
    });
    projected.truncate(32);
    payload.insert(
        "priorities".into(),
        Value::Array(projected.into_iter().map(Value::Object).collect()),
    );
    Ok(Value::Object(payload))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn priority(start: f64, end: f64, loss: f64) -> Value {
        json!({"start_pct":start,"end_pct":end,"recoverable_s":loss,"best_lap":17,
            "tire_energy_share":0.18,"tire_energy_grade":"slip_weighted","future":{"keep":true}})
    }

    #[test]
    fn overlap_is_unique_half_of_priority_span_and_never_a_name_match() {
        let sections = json!([
            {"id":"a","start_pct":0.0,"end_pct":0.25},
            {"id":"b","start_pct":0.25,"end_pct":0.5}
        ]);
        let attach = |a, b| {
            section_id(
                priority(a, b, 0.31).as_object().unwrap(),
                sections.as_array().unwrap(),
            )
        };
        assert_eq!(attach(0.0, 0.5), None); // two exact half overlaps
        assert_eq!(attach(0.0, 1.0), None); // uniform fallback: neither covers half
        assert_eq!(attach(0.125, 0.25), Some("a".into()));
        assert_eq!(attach(0.375, 0.625), Some("b".into())); // exact half, unique
        assert_eq!(attach(0.25, 0.25), None);
        assert_eq!(attach(0.5, 0.25), None);
        assert_eq!(attach(-0.1, 0.1), None);
        assert_eq!(attach(0.75, 1.0), None);
        // Equal halves that floating point rounds apart (0.19999999999999998 vs 0.2) stay ambiguous.
        let decimal = json!([
            {"id":"c","start_pct":0.0,"end_pct":0.3},
            {"id":"d","start_pct":0.3,"end_pct":0.6}
        ]);
        let p = priority(0.1, 0.5, 0.31);
        assert_eq!(
            section_id(p.as_object().unwrap(), decimal.as_array().unwrap()),
            None
        );
        let p = priority(0.1, 0.45, 0.31);
        assert_eq!(
            section_id(p.as_object().unwrap(), decimal.as_array().unwrap()),
            Some("c".into())
        );
    }

    #[test]
    fn passthrough_is_stable_bounded_and_preserves_optional_fields() {
        let priorities: Vec<_> = (0..40)
            .map(|i| priority(0.0, 0.1, (i / 2) as f64))
            .collect();
        let raw = json!({"schema_version":1,"priorities":priorities,"future":{"keep":true}});
        let result = project(raw.clone(), None).unwrap();
        assert_eq!(result["future"], raw["future"]);
        let rows = result["priorities"].as_array().unwrap();
        assert_eq!(rows.len(), 32);
        for (i, row) in rows.iter().enumerate() {
            let source_index = 38 - (i / 2) * 2 + i % 2;
            let mut expected = raw["priorities"][source_index].clone();
            expected["section_id"] = Value::Null;
            assert_eq!(row, &expected);
        }
    }

    #[test]
    fn optional_absence_and_null_work_but_missing_required_and_versions_fail() {
        assert_eq!(project(Value::Null, None).unwrap(), Value::Null);
        assert!(matches!(
            project(json!({"priorities":[]}), None),
            Err(ProjectError::MissingField(_))
        ));
        assert!(matches!(
            project(json!({"schema_version":2,"priorities":[]}), None),
            Err(ProjectError::IncompatibleVersion { .. })
        ));
        let mut raw = json!({"schema_version":1,"priorities":[priority(0.0,0.1,0.31)]});
        raw["priorities"][0]
            .as_object_mut()
            .unwrap()
            .remove("best_lap");
        assert!(matches!(
            project(raw, None),
            Err(ProjectError::MissingField(_))
        ));
    }
}
