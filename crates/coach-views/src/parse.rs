//! Streaming read of one `analysis.json`: only the subtrees the views need are kept. `lap_traces`
//! points go straight into columns; every other subtree is skipped without being built.

use std::collections::HashMap;
use std::fmt;

use serde::Deserialize;
use serde::de::{
    DeserializeSeed, Deserializer, Error as _, IgnoredAny, MapAccess, SeqAccess, Visitor,
};
use serde_json::{Map, Value};

use crate::ProjectError;

/// Backend point keys copied as fixed channels, in `traces::CORE` order.
pub(crate) const CORE_COUNT: usize = 15;

/// JSON kind of a view field; the large unread ones are only probed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Kind {
    Object,
    Array,
    Null,
    Other,
}

impl Kind {
    pub(crate) fn of(value: &Value) -> Kind {
        match value {
            Value::Object(_) => Kind::Object,
            Value::Array(_) => Kind::Array,
            Value::Null => Kind::Null,
            _ => Kind::Other,
        }
    }
}

/// Keys whose subtree a view carries.
const RETAINED: &[&str] = &[
    "identity",
    "race_summary",
    "race_grades",
    "runs",
    "laps",
    "track_geometry",
    "tire_learning",
    "corner_tire_age",
    "race_investigation",
    "strategy",
    "damage_repair",
    "field_pace",
    "conditions",
    "data_quality",
    "tuning_map",
    "pace_attribution",
];

/// Keys that are only checked for presence and kind.
const PROBED: &[&str] = &[
    "race_replay",
    "garage61_representative_laps",
    "technical_insights",
    "groove_evolution",
    "setup_telemetry",
    "strategy_planning",
];

#[derive(Default)]
pub(crate) struct Doc {
    pub analysis_id: Option<Value>,
    pub schema_version: Option<Value>,
    pub profile: Option<Value>,
    pub values: HashMap<String, Value>,
    pub kinds: HashMap<String, Kind>,
    pub lap_traces: Option<LapTraces>,
    /// `lap_trace_columns`: the same traces at chart density (backlog #77), absent before profile v25.
    pub lap_trace_columns: Option<LapTraces>,
    pub nested: Option<Box<Doc>>,
}

#[derive(Default)]
pub(crate) struct LapTraces {
    pub bins: Option<Value>,
    pub sector_start_pcts: Option<Value>,
    pub steering: Option<Value>,
    pub signal_catalog: Option<Value>,
    pub traces: Vec<RawTrace>,
    /// Additional signal ids in discovery order.
    pub extra_ids: Vec<String>,
    extra_index: HashMap<String, usize>,
}

impl LapTraces {
    fn intern(&mut self, id: &str) -> usize {
        if let Some(index) = self.extra_index.get(id) {
            return *index;
        }
        let index = self.extra_ids.len();
        self.extra_ids.push(id.to_string());
        self.extra_index.insert(id.to_string(), index);
        index
    }
}

/// One `lap_traces.traces[]` entry: its other fields as read, its points as columns. Missing is NaN
/// (a present value is never NaN: non-finite numbers were nulled before parsing).
pub(crate) struct RawTrace {
    pub meta: Map<String, Value>,
    pub lap_pct: Vec<f64>,
    pub core: Vec<Vec<f64>>,
    pub extras: Vec<Vec<f64>>,
    pub points: usize,
}

impl RawTrace {
    fn new() -> RawTrace {
        RawTrace {
            meta: Map::new(),
            lap_pct: Vec::new(),
            core: vec![Vec::new(); CORE_COUNT],
            extras: Vec::new(),
            points: 0,
        }
    }

    fn set_extra(&mut self, channel: usize, point: usize, value: f64) {
        if self.extras.len() <= channel {
            self.extras.resize_with(channel + 1, Vec::new);
        }
        let column = &mut self.extras[channel];
        column.resize(point + 1, f64::NAN);
        column[point] = value;
    }

    /// Pads every extra column to the point count.
    pub(crate) fn seal(&mut self, extra_count: usize) {
        self.extras.resize_with(extra_count, Vec::new);
        for column in &mut self.extras {
            column.resize(self.points, f64::NAN);
        }
    }
}

/// Parses `bytes` (UTF-8 JSON without bare non-finite tokens).
pub(crate) fn parse_doc(bytes: &[u8]) -> Result<Doc, ProjectError> {
    let mut de = serde_json::Deserializer::from_slice(bytes);
    let doc = DocSeed { allow_nested: true }
        .deserialize(&mut de)
        .map_err(json_error)?;
    de.end().map_err(json_error)?;
    doc.ok_or_else(|| ProjectError::Json {
        path: "$".into(),
        message: "analysis.json must be a JSON object".into(),
    })
}

pub(crate) fn json_error(error: serde_json::Error) -> ProjectError {
    ProjectError::Json {
        path: format!("line {} column {}", error.line(), error.column()),
        message: error.to_string(),
    }
}

/// Replaces bare `NaN`, `Infinity` and `-Infinity` value tokens (older analyzers wrote Python's) with
/// `null`, never touching text inside strings. `None` when there is nothing to replace.
pub(crate) fn null_bare_nonfinite(input: &[u8]) -> (Option<Vec<u8>>, u64) {
    if let Ok(text) = std::str::from_utf8(input)
        && !text.contains("Infinity")
        && !text.contains("NaN")
    {
        return (None, 0);
    }
    let mut out: Vec<u8> = Vec::new();
    let mut copied = 0;
    let mut replaced = 0u64;
    let mut in_string = false;
    let mut escaped = false;
    let mut previous = b'[';
    let mut i = 0;
    while i < input.len() {
        let b = input[i];
        if in_string {
            if escaped {
                escaped = false;
            } else if b == b'\\' {
                escaped = true;
            } else if b == b'"' {
                in_string = false;
                previous = b;
            }
            i += 1;
            continue;
        }
        if b == b'"' {
            in_string = true;
            i += 1;
            continue;
        }
        if matches!(b, b' ' | b'\t' | b'\r' | b'\n') {
            i += 1;
            continue;
        }
        if matches!(b, b'-' | b'I' | b'N')
            && matches!(previous, b':' | b',' | b'[')
            && let Some(length) = match_token(&input[i..])
        {
            out.extend_from_slice(&input[copied..i]);
            out.extend_from_slice(b"null");
            i += length;
            copied = i;
            replaced += 1;
            previous = b'l';
            continue;
        }
        previous = b;
        i += 1;
    }
    if replaced == 0 {
        return (None, 0);
    }
    out.extend_from_slice(&input[copied..]);
    (Some(out), replaced)
}

fn match_token(rest: &[u8]) -> Option<usize> {
    for token in [&b"-Infinity"[..], b"Infinity", b"NaN"] {
        if rest.starts_with(token) {
            let end = token.len();
            if rest.len() == end
                || matches!(rest[end], b' ' | b'\t' | b'\r' | b'\n' | b',' | b']' | b'}')
            {
                return Some(end);
            }
        }
    }
    None
}

// ---- document ----

struct DocSeed {
    allow_nested: bool,
}

impl<'de> DeserializeSeed<'de> for DocSeed {
    type Value = Option<Doc>;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Option<Doc>, D::Error> {
        deserializer.deserialize_any(DocVisitor {
            allow_nested: self.allow_nested,
        })
    }
}

struct DocVisitor {
    allow_nested: bool,
}

impl<'de> Visitor<'de> for DocVisitor {
    type Value = Option<Doc>;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON object")
    }

    fn visit_unit<E>(self) -> Result<Option<Doc>, E> {
        Ok(None)
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Option<Doc>, A::Error> {
        let mut doc = Doc::default();
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "analysis_id" => doc.analysis_id = Some(map.next_value()?),
                "schema_version" => doc.schema_version = Some(map.next_value()?),
                "analysis_profile_version" => doc.profile = Some(map.next_value()?),
                "analysis_view" if self.allow_nested => {
                    doc.nested = map
                        .next_value_seed(DocSeed {
                            allow_nested: false,
                        })?
                        .map(Box::new);
                }
                "lap_traces" => {
                    let traces = map.next_value_seed(LapTracesSeed)?;
                    let kind = if traces.is_some() {
                        Kind::Object
                    } else {
                        Kind::Null
                    };
                    doc.kinds.insert(key, kind);
                    doc.lap_traces = traces;
                }
                "lap_trace_columns" => {
                    doc.lap_trace_columns = map.next_value_seed(TraceColumnsSeed)?;
                }
                "track_profile" => {
                    let (kind, value) =
                        map.next_value_seed(PickSeed(&["shape", "detected_corner_segments"]))?;
                    doc.kinds.insert(key.clone(), kind);
                    doc.values.insert(key, value);
                }
                "source" => {
                    let (kind, value) =
                        map.next_value_seed(PickSeed(&["selection", "fingerprints"]))?;
                    doc.kinds.insert(key.clone(), kind);
                    doc.values.insert(key, value);
                }
                name if RETAINED.contains(&name) => {
                    let value: Value = map.next_value()?;
                    doc.kinds.insert(key.clone(), Kind::of(&value));
                    doc.values.insert(key, value);
                }
                name if PROBED.contains(&name) => {
                    let kind: Kind = map.next_value()?;
                    doc.kinds.insert(key, kind);
                }
                _ => {
                    map.next_value::<IgnoredAny>()?;
                }
            }
        }
        Ok(Some(doc))
    }
}

impl<'de> Deserialize<'de> for Kind {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Kind, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Kind;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("any JSON value")
            }
            fn visit_bool<E>(self, _: bool) -> Result<Kind, E> {
                Ok(Kind::Other)
            }
            fn visit_i64<E>(self, _: i64) -> Result<Kind, E> {
                Ok(Kind::Other)
            }
            fn visit_u64<E>(self, _: u64) -> Result<Kind, E> {
                Ok(Kind::Other)
            }
            fn visit_f64<E>(self, _: f64) -> Result<Kind, E> {
                Ok(Kind::Other)
            }
            fn visit_str<E>(self, _: &str) -> Result<Kind, E> {
                Ok(Kind::Other)
            }
            fn visit_unit<E>(self) -> Result<Kind, E> {
                Ok(Kind::Null)
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Kind, A::Error> {
                while map.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
                Ok(Kind::Object)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Kind, A::Error> {
                while seq.next_element::<IgnoredAny>()?.is_some() {}
                Ok(Kind::Array)
            }
        }
        deserializer.deserialize_any(V)
    }
}

/// An object reduced to the listed keys; everything else is skipped.
struct PickSeed(&'static [&'static str]);

impl<'de> DeserializeSeed<'de> for PickSeed {
    type Value = (Kind, Value);

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(Kind, Value), D::Error> {
        struct V(&'static [&'static str]);
        impl<'de> Visitor<'de> for V {
            type Value = (Kind, Value);
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("any JSON value")
            }
            fn visit_bool<E>(self, _: bool) -> Result<Self::Value, E> {
                Ok((Kind::Other, Value::Null))
            }
            fn visit_i64<E>(self, _: i64) -> Result<Self::Value, E> {
                Ok((Kind::Other, Value::Null))
            }
            fn visit_u64<E>(self, _: u64) -> Result<Self::Value, E> {
                Ok((Kind::Other, Value::Null))
            }
            fn visit_f64<E>(self, _: f64) -> Result<Self::Value, E> {
                Ok((Kind::Other, Value::Null))
            }
            fn visit_str<E>(self, _: &str) -> Result<Self::Value, E> {
                Ok((Kind::Other, Value::Null))
            }
            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok((Kind::Null, Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                while seq.next_element::<IgnoredAny>()?.is_some() {}
                Ok((Kind::Array, Value::Array(Vec::new())))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut picked = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if self.0.contains(&key.as_str()) {
                        picked.insert(key, map.next_value()?);
                    } else {
                        map.next_value::<IgnoredAny>()?;
                    }
                }
                Ok((Kind::Object, Value::Object(picked)))
            }
        }
        deserializer.deserialize_any(V(self.0))
    }
}

// ---- lap_traces ----

struct LapTracesSeed;

impl<'de> DeserializeSeed<'de> for LapTracesSeed {
    type Value = Option<LapTraces>;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Self::Value, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Option<LapTraces>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("lap_traces: a JSON object")
            }
            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(None)
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut lt = LapTraces::default();
                while let Some(key) = map.next_key::<String>()? {
                    match key.as_str() {
                        "distance_bins_per_lap" => lt.bins = Some(map.next_value()?),
                        "sector_start_pcts" => lt.sector_start_pcts = Some(map.next_value()?),
                        "steering" => lt.steering = Some(map.next_value()?),
                        "additional_signal_catalog" => lt.signal_catalog = Some(map.next_value()?),
                        "traces" => {
                            let mut traces = std::mem::take(&mut lt.traces);
                            map.next_value_seed(TracesSeed {
                                traces: &mut traces,
                                lt: &mut lt,
                            })?;
                            lt.traces = traces;
                        }
                        _ => {
                            map.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                Ok(Some(lt))
            }
        }
        deserializer.deserialize_any(V)
    }
}

struct TracesSeed<'a> {
    traces: &'a mut Vec<RawTrace>,
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for TracesSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut Vec<RawTrace>, &'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("lap_traces.traces: an array")
            }
            fn visit_unit<E>(self) -> Result<(), E> {
                Ok(())
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
                while let Some(trace) = seq.next_element_seed(TraceSeed { lt: &mut *self.1 })? {
                    self.0.push(trace);
                }
                Ok(())
            }
        }
        deserializer.deserialize_any(V(self.traces, self.lt))
    }
}

struct TraceSeed<'a> {
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for TraceSeed<'_> {
    type Value = RawTrace;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<RawTrace, D::Error> {
        struct V<'a>(&'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = RawTrace;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a lap_traces.traces[] object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<RawTrace, A::Error> {
                let mut trace = RawTrace::new();
                while let Some(key) = map.next_key::<String>()? {
                    if key == "points" {
                        map.next_value_seed(PointsSeed {
                            trace: &mut trace,
                            lt: &mut *self.0,
                        })?;
                    } else {
                        trace.meta.insert(key, map.next_value()?);
                    }
                }
                Ok(trace)
            }
        }
        deserializer.deserialize_map(V(self.lt))
    }
}

struct PointsSeed<'a> {
    trace: &'a mut RawTrace,
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for PointsSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut RawTrace, &'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("points: an array")
            }
            fn visit_unit<E>(self) -> Result<(), E> {
                Ok(())
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
                while seq
                    .next_element_seed(PointSeed {
                        trace: &mut *self.0,
                        lt: &mut *self.1,
                    })?
                    .is_some()
                {}
                Ok(())
            }
        }
        deserializer.deserialize_any(V(self.trace, self.lt))
    }
}

struct PointSeed<'a> {
    trace: &'a mut RawTrace,
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for PointSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut RawTrace, &'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a trace point object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
                let trace = self.0;
                let point = trace.points;
                let mut values = [f64::NAN; CORE_COUNT];
                let mut lap_pct = f64::NAN;
                while let Some(key) = map.next_key_seed(PointKeySeed)? {
                    match key {
                        PointKey::Core(index) => values[index] = map.next_value::<Num>()?.0,
                        PointKey::LapPct => lap_pct = map.next_value::<Num>()?.0,
                        PointKey::Additional => {
                            map.next_value_seed(AdditionalSeed {
                                trace: &mut *trace,
                                lt: &mut *self.1,
                                point,
                            })?;
                        }
                        PointKey::Other => {
                            map.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                trace.lap_pct.push(lap_pct);
                for (column, value) in trace.core.iter_mut().zip(values) {
                    column.push(value);
                }
                trace.points += 1;
                Ok(())
            }
        }
        deserializer.deserialize_map(V(self.trace, self.lt))
    }
}

// ---- lap_trace_columns ----

/// `lap_trace_columns`: `{distance_bins_per_lap, traces: [{lap, points, columns: {point key: [..]},
/// additional_signals: {id: [..]}}]}`, read into the shape `lap_traces` is read into. Every column of
/// a trace has one value per point (`lap_pct`'s length); a key the trace lacks is all missing.
struct TraceColumnsSeed;

impl<'de> DeserializeSeed<'de> for TraceColumnsSeed {
    type Value = Option<LapTraces>;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Self::Value, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Option<LapTraces>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("lap_trace_columns: a JSON object")
            }
            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(None)
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut lt = LapTraces::default();
                while let Some(key) = map.next_key::<String>()? {
                    match key.as_str() {
                        "distance_bins_per_lap" => lt.bins = Some(map.next_value()?),
                        "traces" => {
                            let mut traces = Vec::new();
                            map.next_value_seed(ColumnTracesSeed {
                                traces: &mut traces,
                                lt: &mut lt,
                            })?;
                            lt.traces = traces;
                        }
                        _ => {
                            map.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                Ok(Some(lt))
            }
        }
        deserializer.deserialize_any(V)
    }
}

struct ColumnTracesSeed<'a> {
    traces: &'a mut Vec<RawTrace>,
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for ColumnTracesSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut Vec<RawTrace>, &'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("lap_trace_columns.traces: an array")
            }
            fn visit_unit<E>(self) -> Result<(), E> {
                Ok(())
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
                while let Some(trace) =
                    seq.next_element_seed(ColumnTraceSeed { lt: &mut *self.1 })?
                {
                    self.0.push(trace);
                }
                Ok(())
            }
        }
        deserializer.deserialize_any(V(self.traces, self.lt))
    }
}

struct ColumnTraceSeed<'a> {
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for ColumnTraceSeed<'_> {
    type Value = RawTrace;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<RawTrace, D::Error> {
        struct V<'a>(&'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = RawTrace;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a lap_trace_columns.traces[] object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<RawTrace, A::Error> {
                let mut trace = RawTrace::new();
                let mut lap_pct: Option<Vec<f64>> = None;
                while let Some(key) = map.next_key::<String>()? {
                    match key.as_str() {
                        "lap" => {
                            trace.meta.insert(key, map.next_value()?);
                        }
                        "columns" => {
                            map.next_value_seed(KeyColumnsSeed {
                                trace: &mut trace,
                                lap_pct: &mut lap_pct,
                            })?;
                        }
                        "additional_signals" => {
                            map.next_value_seed(SignalColumnsSeed {
                                trace: &mut trace,
                                lt: &mut *self.0,
                            })?;
                        }
                        _ => {
                            map.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                let lap = trace
                    .meta
                    .get("lap")
                    .map_or_else(|| "?".to_string(), Value::to_string);
                let lap_pct = lap_pct
                    .ok_or_else(|| A::Error::custom(format!("lap {lap}: no lap_pct column")))?;
                let points = lap_pct.len();
                let mismatch = |what: &str, found: usize| {
                    A::Error::custom(format!(
                        "lap {lap}: {what} has {found} values for {points} points"
                    ))
                };
                for (index, column) in trace.core.iter_mut().enumerate() {
                    if column.is_empty() {
                        column.resize(points, f64::NAN);
                    } else if column.len() != points {
                        return Err(mismatch(&format!("core column {index}"), column.len()));
                    }
                }
                for column in &trace.extras {
                    if !column.is_empty() && column.len() != points {
                        return Err(mismatch("an additional signal", column.len()));
                    }
                }
                trace.lap_pct = lap_pct;
                trace.points = points;
                Ok(trace)
            }
        }
        deserializer.deserialize_map(V(self.lt))
    }
}

/// A column of numbers: missing (NaN) where an item is not a finite number.
struct NumColumn(Vec<f64>);

impl<'de> Deserialize<'de> for NumColumn {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<NumColumn, D::Error> {
        let items = Vec::<Num>::deserialize(deserializer)?;
        Ok(NumColumn(items.into_iter().map(|n| n.0).collect()))
    }
}

struct KeyColumnsSeed<'a> {
    trace: &'a mut RawTrace,
    lap_pct: &'a mut Option<Vec<f64>>,
}

impl<'de> DeserializeSeed<'de> for KeyColumnsSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut RawTrace, &'a mut Option<Vec<f64>>);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("columns: an object of arrays")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
                while let Some(key) = map.next_key_seed(PointKeySeed)? {
                    match key {
                        PointKey::Core(index) => {
                            self.0.core[index] = map.next_value::<NumColumn>()?.0;
                        }
                        PointKey::LapPct => *self.1 = Some(map.next_value::<NumColumn>()?.0),
                        PointKey::Additional | PointKey::Other => {
                            map.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                Ok(())
            }
        }
        deserializer.deserialize_map(V(self.trace, self.lap_pct))
    }
}

struct SignalColumnsSeed<'a> {
    trace: &'a mut RawTrace,
    lt: &'a mut LapTraces,
}

impl<'de> DeserializeSeed<'de> for SignalColumnsSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut RawTrace, &'a mut LapTraces);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("additional_signals: an object of arrays")
            }
            fn visit_unit<E>(self) -> Result<(), E> {
                Ok(())
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
                while let Some(channel) = map.next_key_seed(InternSeed(&mut *self.1))? {
                    let values = map.next_value::<NumColumn>()?.0;
                    if self.0.extras.len() <= channel {
                        self.0.extras.resize_with(channel + 1, Vec::new);
                    }
                    self.0.extras[channel] = values;
                }
                Ok(())
            }
        }
        deserializer.deserialize_any(V(self.trace, self.lt))
    }
}

enum PointKey {
    Core(usize),
    LapPct,
    Additional,
    Other,
}

struct PointKeySeed;

impl<'de> DeserializeSeed<'de> for PointKeySeed {
    type Value = PointKey;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<PointKey, D::Error> {
        struct V;
        impl Visitor<'_> for V {
            type Value = PointKey;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a point key")
            }
            fn visit_str<E>(self, key: &str) -> Result<PointKey, E> {
                Ok(match key {
                    "speed_mph" => PointKey::Core(0),
                    "session_time_s" => PointKey::Core(1),
                    "throttle" => PointKey::Core(2),
                    "brake" => PointKey::Core(3),
                    "steering_rad" => PointKey::Core(4),
                    "steering_abs_peak_rad" => PointKey::Core(5),
                    "slip_angle_deg" => PointKey::Core(6),
                    "yaw_rate_deg_s" => PointKey::Core(7),
                    "lateral_g" => PointKey::Core(8),
                    "longitudinal_g" => PointKey::Core(9),
                    "gear" => PointKey::Core(10),
                    "rpm" => PointKey::Core(11),
                    "latitude" => PointKey::Core(12),
                    "longitude" => PointKey::Core(13),
                    "tire_stress_proxy" => PointKey::Core(14),
                    "lap_pct" => PointKey::LapPct,
                    "additional_signals" => PointKey::Additional,
                    _ => PointKey::Other,
                })
            }
        }
        deserializer.deserialize_str(V)
    }
}

struct AdditionalSeed<'a> {
    trace: &'a mut RawTrace,
    lt: &'a mut LapTraces,
    point: usize,
}

impl<'de> DeserializeSeed<'de> for AdditionalSeed<'_> {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        struct V<'a>(&'a mut RawTrace, &'a mut LapTraces, usize);
        impl<'de> Visitor<'de> for V<'_> {
            type Value = ();
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("additional_signals: an object")
            }
            fn visit_unit<E>(self) -> Result<(), E> {
                Ok(())
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
                while let Some(channel) = map.next_key_seed(InternSeed(&mut *self.1))? {
                    let value = map.next_value::<Num>()?.0;
                    self.0.set_extra(channel, self.2, value);
                }
                Ok(())
            }
        }
        deserializer.deserialize_any(V(self.trace, self.lt, self.point))
    }
}

struct InternSeed<'a>(&'a mut LapTraces);

impl<'de> DeserializeSeed<'de> for InternSeed<'_> {
    type Value = usize;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<usize, D::Error> {
        struct V<'a>(&'a mut LapTraces);
        impl Visitor<'_> for V<'_> {
            type Value = usize;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a signal id")
            }
            fn visit_str<E>(self, id: &str) -> Result<usize, E> {
                Ok(self.0.intern(id))
            }
        }
        deserializer.deserialize_str(V(self.0))
    }
}

/// A finite number, else missing (NaN): null, text, booleans and containers are not values.
struct Num(f64);

impl<'de> Deserialize<'de> for Num {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Num, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Num;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a number or null")
            }
            fn visit_f64<E>(self, v: f64) -> Result<Num, E> {
                Ok(Num(if v.is_finite() { v } else { f64::NAN }))
            }
            fn visit_i64<E>(self, v: i64) -> Result<Num, E> {
                Ok(Num(v as f64))
            }
            fn visit_u64<E>(self, v: u64) -> Result<Num, E> {
                Ok(Num(v as f64))
            }
            fn visit_bool<E>(self, _: bool) -> Result<Num, E> {
                Ok(Num(f64::NAN))
            }
            fn visit_str<E>(self, _: &str) -> Result<Num, E> {
                Ok(Num(f64::NAN))
            }
            fn visit_unit<E>(self) -> Result<Num, E> {
                Ok(Num(f64::NAN))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Num, A::Error> {
                while map.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
                Ok(Num(f64::NAN))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Num, A::Error> {
                while seq.next_element::<IgnoredAny>()?.is_some() {}
                Ok(Num(f64::NAN))
            }
        }
        deserializer.deserialize_any(V)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_tokens_become_null_outside_strings_only() {
        let input =
            br#"{"a": NaN, "b": [Infinity, -Infinity, 1], "s": "NaN and Infinity", "c":-Infinity}"#;
        let (out, count) = null_bare_nonfinite(input);
        assert_eq!(count, 4);
        let value: Value = serde_json::from_slice(&out.unwrap()).unwrap();
        assert_eq!(
            value,
            serde_json::json!({"a": null, "b": [null, null, 1], "s": "NaN and Infinity", "c": null})
        );
    }

    #[test]
    fn clean_input_is_not_copied() {
        let (out, count) = null_bare_nonfinite(br#"{"a": "Infinity", "b": 1}"#);
        assert!(out.is_none());
        assert_eq!(count, 0);
    }

    #[test]
    fn tokens_that_only_start_like_infinity_stay() {
        let (out, count) = null_bare_nonfinite(br#"{"a": [Infinityx]}"#);
        assert!(out.is_none());
        assert_eq!(count, 0);
    }
}
