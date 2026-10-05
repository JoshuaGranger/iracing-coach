//! Track geometry (copied in the backend's `normalized_local_vector` space, never reprojected) and the
//! steering declaration (`SteeringBasisMapper`).

use serde_json::{Map, Value, json};

use crate::Envelope;
use crate::val::{Obj, arr, nonblank, num, obj, text};

pub(crate) const COORDINATE_SPACE: &str = "normalized_local_vector";
pub(crate) const ROAD_WHEEL_LABEL: &str = "Steering (road wheel)";
pub(crate) const STEERING_WHEEL_LABEL: &str = "Steering wheel angle";
const UNDECLARED_LABEL: &str = "Steering wheel angle \u{2014} ratio not recorded";
const UNDECLARED_REASON: &str =
    "This analysis predates steering-ratio normalization; reanalyze it for road-wheel steering.";
const UNDECLARED_DEFINITION: &str =
    "Recorded SteeringWheelAngle; the steering ratio was not applied.";

#[derive(Debug, Clone, Copy)]
pub(crate) struct Point {
    pub x: f64,
    pub y: f64,
    pub pct: Option<f64>,
}

fn point(value: &Value) -> Option<Point> {
    let map = value.as_object()?;
    Some(Point {
        x: num(map, "x")?,
        y: num(map, "y")?,
        pct: num(map, "lap_pct"),
    })
}

/// The path's points with finite `x` and `y`.
pub(crate) fn points(map: &Obj, key: &str) -> Vec<Point> {
    arr(map, key).iter().filter_map(point).collect()
}

fn series(points: &[Point]) -> Value {
    if points.is_empty() {
        return Value::Null;
    }
    json!({
        "pct": points.iter().map(|p| p.pct).collect::<Vec<_>>(),
        "x": points.iter().map(|p| p.x).collect::<Vec<_>>(),
        "y": points.iter().map(|p| p.y).collect::<Vec<_>>(),
    })
}

fn track_point(value: &Value) -> Option<Value> {
    let p = point(value)?;
    Some(json!({"x": p.x, "y": p.y, "lap_pct": p.pct}))
}

fn line(value: Option<&Value>) -> Value {
    let Some(map) = value.and_then(Value::as_object) else {
        return Value::Null;
    };
    match (
        map.get("a").and_then(track_point),
        map.get("b").and_then(track_point),
    ) {
        (Some(a), Some(b)) => json!({"a": a, "b": b}),
        _ => Value::Null,
    }
}

/// `AnalysisGeometryTransform.IsUsable`: finite bounds with max >= min and a positive finite scale.
/// `longitude_scale` (profile v24: the geometry's x is longitude times this factor) is carried
/// when positive and finite; without it the geometry predates the factor and x is longitude.
pub(crate) fn transform(value: Option<&Value>) -> Option<Value> {
    let map = value?.as_object()?;
    let bounds = obj(map, "source_bounds")?;
    let (min_x, max_x) = (num(bounds, "minimum_x")?, num(bounds, "maximum_x")?);
    let (min_y, max_y) = (num(bounds, "minimum_y")?, num(bounds, "maximum_y")?);
    let scale = num(map, "normalization_scale")?;
    let longitude_scale = num(map, "longitude_scale").filter(|k| *k > 0.0);
    (scale > 0.0 && max_x >= min_x && max_y >= min_y).then(|| {
        let mut out = json!({
            "source_bounds": {"minimum_x": min_x, "maximum_x": max_x, "minimum_y": min_y, "maximum_y": max_y},
            "normalization_scale": scale
        });
        if let Some(k) = longitude_scale {
            out["longitude_scale"] = json!(k);
        }
        out
    })
}

/// The `TrackGeometry` view, or `None` when the backend geometry has no usable main path (status
/// "unavailable", another coordinate system, or fewer than three finite points).
pub(crate) fn track_view(geometry: &Obj, envelope: &Envelope) -> Option<Value> {
    let status = text(geometry, "status").unwrap_or_else(|| "unavailable".into());
    if status.eq_ignore_ascii_case("unavailable")
        || text(geometry, "coordinate_system").as_deref() != Some(COORDINATE_SPACE)
    {
        return None;
    }
    let main = points(geometry, "main_path");
    if main.len() < 3 {
        return None;
    }
    let provenance = obj(geometry, "geometry_provenance");
    let transform = transform(geometry.get("transform"))
        .or_else(|| transform(provenance.and_then(|p| p.get("normalization_transform"))))
        .unwrap_or(Value::Null);
    let quality = obj(geometry, "quality")
        .filter(|q| {
            [
                "main_loop_complete",
                "lap_percent_coverage",
                "maximum_lap_percent_gap",
                "closure_distance",
            ]
            .iter()
            .any(|key| q.get(*key).is_some_and(|v| !v.is_null()))
        })
        .map_or(Value::Null, |q| Value::Object(q.clone()));
    let mut view = Map::new();
    envelope.apply(&mut view);
    view.insert("coordinate_space".into(), json!(COORDINATE_SPACE));
    view.insert("status".into(), json!(status));
    view.insert(
        "track_configuration_key".into(),
        json!(nonblank(geometry, "track_configuration_key")),
    );
    view.insert(
        "geometry_hash".into(),
        json!(nonblank(geometry, "geometry_hash")),
    );
    view.insert("main_path".into(), series(&main));
    view.insert("pit_lane".into(), series(&points(geometry, "pit_lane")));
    view.insert(
        "pit_entry".into(),
        series(&points(geometry, "pit_entry_path")),
    );
    view.insert(
        "pit_exit".into(),
        series(&points(geometry, "pit_exit_path")),
    );
    view.insert(
        "start_finish".into(),
        line(geometry.get("start_finish_line")),
    );
    view.insert(
        "pit_commitment_line".into(),
        line(geometry.get("pit_commitment_line")),
    );
    view.insert(
        "pit_merge_line".into(),
        line(geometry.get("pit_merge_line")),
    );
    view.insert("transform".into(), transform);
    view.insert("quality".into(), quality);
    view.insert(
        "provenance".into(),
        Value::Object(provenance.cloned().unwrap_or_default()),
    );
    Some(Value::Object(view))
}

/// The catalog's `steering_basis` and whether it is road-wheel. A declaration that is not basis
/// `road_wheel` with `steering_ratio` > 0 is read as steering-wheel angle, whatever else it claims; no
/// declaration is `unknown`. Other backend fields are kept.
pub(crate) fn steering_basis(declared: Option<&Value>) -> (Value, bool) {
    let Some(map) = declared.and_then(Value::as_object) else {
        return (
            json!({
                "basis": "unknown",
                "status": "not_recorded",
                "label": UNDECLARED_LABEL,
                "steering_ratio": null,
                "digits": 1,
                "reason": UNDECLARED_REASON,
                "definition": UNDECLARED_DEFINITION,
            }),
            false,
        );
    };
    let ratio = num(map, "steering_ratio").filter(|r| *r > 0.0);
    let road_wheel = text(map, "basis").as_deref() == Some("road_wheel") && ratio.is_some();
    let mut out = map.clone();
    out.entry("status").or_insert(json!("not_recorded"));
    if road_wheel {
        out.insert("basis".into(), json!("road_wheel"));
        out.insert("steering_ratio".into(), json!(ratio));
        out.insert("label".into(), json!(ROAD_WHEEL_LABEL));
        out.insert("digits".into(), json!(2));
    } else {
        let label = text(map, "label")
            .filter(|l| l.starts_with(STEERING_WHEEL_LABEL))
            .unwrap_or_else(|| UNDECLARED_LABEL.into());
        out.insert("basis".into(), json!("steering_wheel"));
        out.insert("steering_ratio".into(), Value::Null);
        out.insert("label".into(), json!(label));
        out.insert("digits".into(), json!(1));
        out.insert(
            "reason".into(),
            json!(text(map, "reason").unwrap_or_else(|| UNDECLARED_REASON.into())),
        );
    }
    (Value::Object(out), road_wheel)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn road_wheel_needs_a_positive_ratio() {
        let (basis, road) = steering_basis(Some(
            &json!({"basis": "road_wheel", "steering_ratio": 12.0, "x": 1}),
        ));
        assert!(road);
        assert_eq!(basis["basis"], "road_wheel");
        assert_eq!(basis["digits"], 2);
        assert_eq!(basis["x"], 1);
        let (basis, road) = steering_basis(Some(
            &json!({"basis": "road_wheel", "steering_ratio": null, "label": "Steering (road wheel)"}),
        ));
        assert!(!road);
        assert_eq!(basis["basis"], "steering_wheel");
        assert_eq!(basis["label"], UNDECLARED_LABEL);
        assert!(basis["steering_ratio"].is_null());
    }

    #[test]
    fn missing_declaration_is_unknown() {
        let (basis, road) = steering_basis(None);
        assert!(!road);
        assert_eq!(basis["basis"], "unknown");
    }

    #[test]
    fn transform_must_be_usable() {
        let ok = json!({"source_bounds": {"minimum_x": 0.0, "maximum_x": 2.0, "minimum_y": 1.0, "maximum_y": 3.0}, "normalization_scale": 2.0});
        assert!(transform(Some(&ok)).is_some());
        assert!(
            transform(Some(&ok))
                .unwrap()
                .get("longitude_scale")
                .is_none()
        );
        let scaled = json!({"source_bounds": {"minimum_x": 0.0, "maximum_x": 2.0, "minimum_y": 1.0, "maximum_y": 3.0}, "normalization_scale": 2.0, "longitude_scale": 0.81});
        assert_eq!(transform(Some(&scaled)).unwrap()["longitude_scale"], 0.81);
        let zero = json!({"source_bounds": {"minimum_x": 0.0, "maximum_x": 2.0, "minimum_y": 1.0, "maximum_y": 3.0}, "normalization_scale": 2.0, "longitude_scale": 0.0});
        assert!(
            transform(Some(&zero))
                .unwrap()
                .get("longitude_scale")
                .is_none()
        );
        let bad = json!({"source_bounds": {"minimum_x": 3.0, "maximum_x": 2.0, "minimum_y": 1.0, "maximum_y": 3.0}, "normalization_scale": 2.0});
        assert!(transform(Some(&bad)).is_none());
        assert!(transform(Some(&json!({}))).is_none());
    }
}
