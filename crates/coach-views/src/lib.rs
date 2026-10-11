//! Reads one `analysis.json` and writes the bounded view files the server serves. Copies backend
//! values; computes no telemetry math. Public API: `docs/08-rewrite/contracts/crate-api.md` section 5.
//!
//! Input is the analysis document as persisted by the worker (flat: `analysis_id`, `schema_version`
//! 2, the `analysis_view` fields at the top level) or the analyze result's nested form
//! (`{"analysis_id", "analysis_view": {"schema_version": 1, ...}}`).
#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Instant;

use serde_json::{Map, Value, json};

#[doc(hidden)]
pub mod consumed;
mod geometry;
mod laps;
mod output;
mod pace_attribution;
mod parse;
mod prune;
mod section_laps;
mod traces;
#[doc(hidden)]
pub mod trend;
mod tuning;
mod val;

use parse::{Doc, Kind};
use prune::Spec;
use val::{Obj, int_of};

pub const PROJECTION_VERSION: u32 = 7;

/// `analysis_view.schema_version` this crate reads, and the analysis document's own.
const VIEW_SCHEMA_VERSION: i64 = 1;
const DOCUMENT_SCHEMA_VERSION: i64 = 2;
/// `race_investigation.schema_version`.
const INVESTIGATION_SCHEMA_VERSION: i64 = 2;

const RACE_BOUND: u64 = 2 * 1024 * 1024;
const RUNS_BOUND: u64 = 1024 * 1024;
const INVESTIGATION_BOUND: u64 = 4 * 1024 * 1024;
const CATALOG_BOUND: u64 = 512 * 1024;
const TRACK_BOUND: u64 = 512 * 1024;
const TUNING_BOUND: u64 = 1024 * 1024;

/// Writes every view file into `out_dir` (`/data/views/<analysis_id>/p<PROJECTION_VERSION>/`),
/// which must not exist: files go to a sibling `<out_dir>.tmp-<random>` that is fsynced and
/// renamed; on error the temp directory is removed and nothing is left at `out_dir`.
pub fn project(analysis_json: &Path, out_dir: &Path) -> Result<ProjectionSummary, ProjectError> {
    let started = Instant::now();
    if out_dir.exists() {
        return Err(ProjectError::OutputExists(out_dir.to_path_buf()));
    }
    let (doc, nulled) = {
        let raw = fs::read(analysis_json)?;
        let start = if raw.starts_with(&[0xEF, 0xBB, 0xBF]) {
            3
        } else {
            0
        };
        match parse::null_bare_nonfinite(&raw[start..]) {
            (Some(clean), count) => {
                drop(raw);
                (parse::parse_doc(&clean)?, count)
            }
            (None, _) => (parse::parse_doc(&raw[start..])?, 0),
        }
    };
    let built = build(doc)?;
    let files = output::write_all(out_dir, &built)?;
    Ok(ProjectionSummary {
        analysis_id: built.envelope.analysis_id,
        projection_version: PROJECTION_VERSION,
        schema_version: built.envelope.schema_version,
        profile_version: built.envelope.profile_version,
        lap_count: built.lap_count,
        bins_per_lap: built.prepared.bins as u32,
        trace_channels: built
            .prepared
            .signals
            .iter()
            .map(|s| s.id.clone())
            .collect(),
        files,
        nonfinite_nulled: nulled,
        elapsed_ms: started.elapsed().as_millis() as u64,
    })
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ProjectionSummary {
    /// `analysis.json` `analysis_id`.
    pub analysis_id: String,
    pub projection_version: u32,
    /// `analysis_view.schema_version`.
    pub schema_version: i64,
    /// `analysis_view.analysis_profile_version`; empty when the backend left it null.
    pub profile_version: String,
    /// `laps[]` length.
    pub lap_count: u32,
    /// Distance bins of `traces.ccol`: `lap_trace_columns` when the analysis has them (640), else
    /// `lap_traces` (160).
    pub bins_per_lap: u32,
    /// `TraceSignal` ids in catalog order.
    pub trace_channels: Vec<String>,
    /// Files actually written.
    pub files: Vec<ViewFile>,
    /// Numbers replaced by null.
    pub nonfinite_nulled: u64,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ViewFile {
    pub kind: ViewKind,
    pub file_name: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ViewKind {
    Race,
    Runs,
    Investigation,
    TraceCatalog,
    Traces,
    Track,
    Tuning,
}

/// Serves `GET /api/v1/analyses/{id}/traces`: reads `traces.ccol` and `traces-catalog.json` from
/// `view_dir`, parses the query values exactly as received (`laps` "1-40,52", `channels` comma
/// ids; None = all laps / the catalog's default set) and returns a CCOL body holding only the
/// selection.
pub fn select_traces(
    view_dir: &Path,
    laps: Option<&str>,
    channels: Option<&str>,
    max_bytes: u64,
) -> Result<Vec<u8>, SelectError> {
    traces::select(view_dir, laps, channels, max_bytes)
}

#[derive(Debug, thiserror::Error)]
pub enum ProjectError {
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("malformed analysis.json at {path}: {message}")]
    Json { path: String, message: String },
    #[error("missing required field {0}")]
    MissingField(String),
    #[error("{field} {found} is not supported (supported: {supported})")]
    IncompatibleVersion {
        field: &'static str,
        found: String,
        supported: String,
    },
    #[error("{kind:?} view is {bytes} bytes, bound {limit}")]
    TooLarge {
        kind: ViewKind,
        bytes: u64,
        limit: u64,
    },
    #[error("{0} already exists")]
    OutputExists(PathBuf),
}

#[derive(Debug, thiserror::Error)]
pub enum SelectError {
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Server: 400 `invalid_request`.
    #[error("invalid laps selection: {0}")]
    BadLaps(String),
    /// 400 `invalid_request`.
    #[error("unknown channel {0}")]
    UnknownChannel(String),
    /// 413.
    #[error("selection is {bytes} bytes, bound {limit}")]
    TooLarge { bytes: u64, limit: u64 },
    /// 500; the server re-projects.
    #[error("corrupt view file: {0}")]
    Corrupt(String),
}

// ---- assembly ----

/// `ViewEnvelope`.
pub(crate) struct Envelope {
    pub analysis_id: String,
    pub schema_version: i64,
    pub profile_version: String,
}

impl Envelope {
    pub(crate) fn apply(&self, view: &mut Obj) {
        view.insert("analysis_id".into(), json!(self.analysis_id));
        view.insert("projection_version".into(), json!(PROJECTION_VERSION));
        view.insert("schema_version".into(), json!(self.schema_version));
        view.insert("profile_version".into(), json!(self.profile_version));
    }
}

pub(crate) struct Built {
    pub envelope: Envelope,
    pub lap_count: u32,
    /// JSON views in write order.
    pub json_views: Vec<(ViewKind, &'static str, Vec<u8>)>,
    pub prepared: traces::Prepared,
    pub traces: Vec<parse::RawTrace>,
}

/// View fields every analysis carries, with their JSON kind.
const REQUIRED: &[(&str, Kind)] = &[
    ("identity", Kind::Object),
    ("race_summary", Kind::Object),
    ("race_grades", Kind::Object),
    ("runs", Kind::Array),
    ("laps", Kind::Array),
    ("lap_traces", Kind::Object),
    ("track_geometry", Kind::Object),
    ("race_replay", Kind::Object),
    ("tire_learning", Kind::Object),
    ("garage61_representative_laps", Kind::Object),
    ("technical_insights", Kind::Array),
    ("corner_tire_age", Kind::Object),
    ("groove_evolution", Kind::Object),
    ("strategy", Kind::Object),
    ("damage_repair", Kind::Object),
    ("setup_telemetry", Kind::Object),
    ("conditions", Kind::Object),
    ("data_quality", Kind::Object),
];

/// Absent or null is fine, another type is not: fields added to the envelope after its first release,
/// and `track_profile`, which the analyzer leaves null when no sample is usable (a recording spent on
/// pit road or under caution, as Offline Testing sessions can be); the track and tuning views then
/// come from `track_geometry` alone.
const OPTIONAL_OBJECTS: &[&str] = &[
    "track_profile",
    "race_investigation",
    "strategy_planning",
    "field_pace",
    "pace_attribution",
    "tuning_map",
];

fn describe(value: Option<&Value>) -> String {
    value.map_or_else(|| "missing".to_string(), Value::to_string)
}

fn kind_name(kind: Kind) -> &'static str {
    match kind {
        Kind::Object => "an object",
        Kind::Array => "an array",
        Kind::Null => "null",
        Kind::Other => "a scalar",
    }
}

fn build(mut root: Doc) -> Result<Built, ProjectError> {
    // Which document is it, and is its version one this crate reads.
    let nested = root.nested.take();
    let root_columns = root.lap_trace_columns.take();
    let mut analysis_id = root.analysis_id.take();
    let (mut doc, version_field, supported, expected) = match nested {
        Some(inner) => (
            *inner,
            "analysis_view.schema_version",
            "1",
            VIEW_SCHEMA_VERSION,
        ),
        None => (root, "schema_version", "2", DOCUMENT_SCHEMA_VERSION),
    };
    analysis_id = analysis_id.or_else(|| doc.analysis_id.take());
    let analysis_id = match analysis_id {
        Some(Value::String(id)) if !id.trim().is_empty() => id,
        _ => return Err(ProjectError::MissingField("analysis_id".into())),
    };
    let Some(version) = doc.schema_version.take() else {
        return Err(ProjectError::MissingField(version_field.into()));
    };
    if int_of(&version) != Some(expected) {
        return Err(ProjectError::IncompatibleVersion {
            field: version_field,
            found: describe(Some(&version)),
            supported: supported.into(),
        });
    }
    let profile_version = match doc.profile.take() {
        None => {
            return Err(ProjectError::MissingField(
                "analysis_profile_version".into(),
            ));
        }
        Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s,
        Some(_) => {
            return Err(ProjectError::Json {
                path: "analysis_profile_version".into(),
                message: "expected text or null".into(),
            });
        }
    };
    for (name, kind) in REQUIRED {
        match doc.kinds.get(*name) {
            None | Some(Kind::Null) => return Err(ProjectError::MissingField((*name).into())),
            Some(found) if found != kind => {
                return Err(ProjectError::Json {
                    path: (*name).into(),
                    message: format!("expected {}, found {}", kind_name(*kind), kind_name(*found)),
                });
            }
            Some(_) => {}
        }
    }
    for name in OPTIONAL_OBJECTS {
        if let Some(found) = doc.kinds.get(*name)
            && !matches!(found, Kind::Object | Kind::Null)
        {
            return Err(ProjectError::Json {
                path: (*name).into(),
                message: format!("expected an object, found {}", kind_name(*found)),
            });
        }
    }

    let envelope = Envelope {
        analysis_id,
        schema_version: VIEW_SCHEMA_VERSION,
        profile_version,
    };
    let mut take = |name: &str| doc.values.remove(name).unwrap_or(Value::Null);
    let identity = take("identity");
    let mut runs = take("runs");
    let lap_values = take("laps");
    let geometry = take("track_geometry");
    let profile = take("track_profile");
    let source = take("source");
    let investigation = take("race_investigation");
    let tuning_map = take("tuning_map");
    let race_summary = take("race_summary");
    let race_grades = take("race_grades");
    let strategy = take("strategy");
    let damage_repair = take("damage_repair");
    let field_pace = take("field_pace");
    let conditions = take("conditions");
    let data_quality = take("data_quality");
    let tire_learning = take("tire_learning");
    let corner_tire_age = take("corner_tire_age");
    let pace_attribution = take("pace_attribution");

    // The investigation is absent when the backend has none (an empty object counts as none).
    let investigation_map = investigation.as_object().filter(|m| !m.is_empty());
    if let Some(inv) = investigation_map {
        match inv.get("schema_version") {
            None => {
                return Err(ProjectError::MissingField(
                    "race_investigation.schema_version".into(),
                ));
            }
            Some(v) if int_of(v) != Some(INVESTIGATION_SCHEMA_VERSION) => {
                return Err(ProjectError::IncompatibleVersion {
                    field: "race_investigation.schema_version",
                    found: describe(Some(v)),
                    supported: "2".into(),
                });
            }
            Some(_) => {}
        }
    }

    let pace_attribution = pace_attribution::project(pace_attribution, investigation_map)?;
    let empty = Obj::new();
    let run_items = runs.as_array().map_or(&[][..], Vec::as_slice);
    let lap_items = lap_values.as_array().map_or(&[][..], Vec::as_slice);
    let (lap_rows, lap_records) = laps::build(lap_items, run_items, investigation_map)?;

    // Traces.
    let mut lap_traces = doc.lap_traces.take().unwrap_or_default();
    let (steering, road_wheel) = geometry::steering_basis(lap_traces.steering.as_ref());
    let steering_digits = if road_wheel { 2 } else { 1 };
    let prepared = traces::prepare(&mut lap_traces, road_wheel, steering_digits)?;
    // The chart's channels come from the dense traces when the analysis has them; the catalog's laps,
    // sector times and the section times stay on `lap_traces`, the traces the backend's own
    // investigation reads.
    let dense = match doc.lap_trace_columns.take().or(root_columns) {
        Some(mut columns) => {
            columns.signal_catalog = lap_traces.signal_catalog.clone();
            let served = traces::prepare_columns(&mut columns, road_wheel, steering_digits)?;
            if served.laps != prepared.laps {
                return Err(ProjectError::Json {
                    path: "lap_trace_columns.traces".into(),
                    message: format!(
                        "laps {:?} differ from lap_traces laps {:?}",
                        served.laps, prepared.laps
                    ),
                });
            }
            Some((served, columns.traces))
        }
        None => None,
    };
    let catalog = traces::catalog_json(&traces::CatalogInputs {
        envelope: &envelope,
        prepared: &prepared,
        served: dense.as_ref().map_or(&prepared, |(served, _)| served),
        traces: &lap_traces.traces,
        records: &lap_records,
        sector_start_pcts: lap_traces.sector_start_pcts.as_ref(),
        steering_basis: steering,
    });

    let mut views: Vec<(ViewKind, &'static str, Vec<u8>)> = Vec::new();

    // Tuning and track read the geometry and profile before they are moved.
    let identity_map = identity.as_object().unwrap_or(&empty);
    let tuning = tuning::projection(&tuning::TuningInputs {
        analysis_id: &envelope.analysis_id,
        identity: identity_map,
        source: source.as_object(),
        geometry: geometry.as_object(),
        profile: profile.as_object(),
        tuning_map: tuning_map.as_object(),
        runs: run_items,
    });
    let track = geometry
        .as_object()
        .and_then(|g| geometry::track_view(g, &envelope));

    // Each backend run gains its observed trend over the projected lap rows (backlog #90).
    let pit_stops = laps::pit_stops(run_items);
    trend::annotate(&mut runs, &lap_rows, &damage_repair);

    // Race.
    let mut race = Map::new();
    envelope.apply(&mut race);
    race.insert("identity".into(), identity);
    race.insert("race_summary".into(), race_summary);
    race.insert("race_grades".into(), race_grades);
    race.insert("laps".into(), Value::Array(lap_rows));
    race.insert("pit_stops".into(), Value::Array(pit_stops));
    race.insert("strategy".into(), strategy);
    race.insert("damage_repair".into(), damage_repair);
    race.insert("field_pace".into(), field_pace);
    race.insert("conditions".into(), conditions);
    race.insert("data_quality".into(), data_quality);
    views.push((
        ViewKind::Race,
        "race.json",
        fit(
            ViewKind::Race,
            race,
            RACE_BOUND,
            &[
                &|v| {
                    set_null(v, &["conditions"]);
                    let results = v.get("field_pace").and_then(|p| p.get("results")).cloned();
                    v.insert(
                        "field_pace".into(),
                        results.map_or(Value::Null, |r| json!({"results": r})),
                    );
                },
                &|v| prune_items(v, "laps", &specs().lap),
                &|v| {
                    prune_fields(
                        v,
                        &[
                            ("identity", &specs().race_identity),
                            ("race_summary", &specs().race_summary),
                            ("race_grades", &specs().race_grades),
                            ("strategy", &specs().race_strategy),
                            ("damage_repair", &specs().race_damage_repair),
                            ("data_quality", &specs().race_data_quality),
                        ],
                    )
                },
                &|v| {
                    if let Some(cars) = v
                        .get_mut("field_pace")
                        .and_then(|field| field.pointer_mut("/results/cars"))
                        .and_then(Value::as_array_mut)
                    {
                        for car in cars {
                            if let Some(car) = car.as_object_mut() {
                                car.remove("laps");
                                car.insert(
                                    "progression_unavailable_reason".into(),
                                    json!("payload_budget"),
                                );
                            }
                        }
                    }
                },
            ],
        )?,
    ));

    // Runs.
    let mut runs_view = Map::new();
    envelope.apply(&mut runs_view);
    runs_view.insert("runs".into(), runs);
    runs_view.insert("corner_tire_age".into(), corner_tire_age);
    runs_view.insert("tire_learning".into(), tire_learning);
    views.push((
        ViewKind::Runs,
        "runs.json",
        fit(
            ViewKind::Runs,
            runs_view,
            RUNS_BOUND,
            &[
                &|v| set_null(v, &["corner_tire_age", "tire_learning"]),
                &|v| prune_items(v, "runs", &specs().backend_run),
            ],
        )?,
    ));

    // Investigation, with the projector's per-section clean-lap times beside the backend's subtree.
    let section_laps = investigation_map.map(|inv| section_laps::build(inv, &lap_traces.traces));
    if let Some(section_laps) = section_laps {
        let mut view = Map::new();
        envelope.apply(&mut view);
        view.insert("race_investigation".into(), investigation);
        view.insert("section_laps".into(), section_laps);
        view.insert("pace_attribution".into(), pace_attribution);
        views.push((
            ViewKind::Investigation,
            "investigation.json",
            fit(
                ViewKind::Investigation,
                view,
                INVESTIGATION_BOUND,
                &[&|v| {
                    prune_fields(
                        v,
                        &[
                            ("race_investigation", &specs().investigation),
                            ("pace_attribution", &specs().pace_attribution),
                        ],
                    )
                }],
            )?,
        ));
    }

    // Trace catalog.
    let Value::Object(catalog) = catalog else {
        unreachable!("catalog_json builds an object");
    };
    views.push((
        ViewKind::TraceCatalog,
        "traces-catalog.json",
        fit(
            ViewKind::TraceCatalog,
            catalog,
            CATALOG_BOUND,
            &[&|v| prune_items(v, "laps", &specs().trace_lap)],
        )?,
    ));

    // Track.
    if let Some(Value::Object(track)) = track {
        views.push((
            ViewKind::Track,
            "track.json",
            fit(
                ViewKind::Track,
                track,
                TRACK_BOUND,
                &[
                    &|v| {
                        v.insert("provenance".into(), json!({}));
                    },
                    &|v| prune_fields(v, &[("quality", &specs().track_quality)]),
                ],
            )?,
        ));
    }

    // Tuning.
    let Value::Object(tuning) = tuning else {
        unreachable!("projection builds an object");
    };
    views.push((
        ViewKind::Tuning,
        "tuning.json",
        fit(
            ViewKind::Tuning,
            tuning,
            TUNING_BOUND,
            &[&|v| prune_items(v, "representative_runs", &specs().backend_run)],
        )?,
    ));

    let (prepared, traces) = match dense {
        Some((served, traces)) => (served, traces),
        None => (prepared, lap_traces.traces),
    };
    Ok(Built {
        lap_count: lap_records.len() as u32,
        envelope,
        json_views: views,
        prepared,
        traces,
    })
}

// ---- shrink ladder ----

type Step<'a> = &'a dyn Fn(&mut Obj);

/// Serializes `view`; while it exceeds `limit`, applies the next shrink step. Only fields outside the
/// `x-consumed-fields` lists are ever dropped.
fn fit(kind: ViewKind, mut view: Obj, limit: u64, steps: &[Step]) -> Result<Vec<u8>, ProjectError> {
    let mut bytes = serde_json::to_vec(&view).map_err(parse::json_error)?;
    for step in steps {
        if bytes.len() as u64 <= limit {
            break;
        }
        step(&mut view);
        bytes = serde_json::to_vec(&view).map_err(parse::json_error)?;
    }
    if bytes.len() as u64 > limit {
        return Err(ProjectError::TooLarge {
            kind,
            bytes: bytes.len() as u64,
            limit,
        });
    }
    Ok(bytes)
}

fn set_null(view: &mut Obj, keys: &[&str]) {
    for key in keys {
        if view.contains_key(*key) {
            view.insert((*key).into(), Value::Null);
        }
    }
}

fn prune_fields(view: &mut Obj, fields: &[(&str, &Spec)]) {
    for (key, spec) in fields {
        if let Some(value) = view.get_mut(*key)
            && !value.is_null()
        {
            *value = spec.apply(value);
        }
    }
}

fn prune_items(view: &mut Obj, key: &str, spec: &Spec) {
    if let Some(Value::Array(items)) = view.get_mut(key) {
        for item in items {
            *item = spec.apply(item);
        }
    }
}

struct Specs {
    lap: Spec,
    trace_lap: Spec,
    backend_run: Spec,
    investigation: Spec,
    pace_attribution: Spec,
    race_identity: Spec,
    race_summary: Spec,
    race_grades: Spec,
    race_strategy: Spec,
    race_damage_repair: Spec,
    race_data_quality: Spec,
    track_quality: Spec,
}

fn specs() -> &'static Specs {
    static SPECS: OnceLock<Specs> = OnceLock::new();
    SPECS.get_or_init(|| {
        // Fields the projector writes beside the backend's.
        let lap_paths: Vec<&str> = consumed::LAP
            .iter()
            .copied()
            .chain([
                "comparison_eligible",
                "exclusion_reasons",
                "run_number",
                "run_age_laps",
                "pit_in",
                "pit_out",
            ])
            .collect();
        let conditions: Vec<String> = consumed::TRACE_LAP_CONDITIONS
            .iter()
            .map(|c| format!("conditions.{c}"))
            .collect();
        let trace_paths: Vec<&str> = consumed::TRACE_LAP
            .iter()
            .copied()
            .chain([
                "comparison_eligible",
                "exclusion_reasons",
                "run_number",
                "run_age_laps",
                "sector_times_s",
            ])
            .chain(conditions.iter().map(String::as_str))
            .collect();
        Specs {
            lap: Spec::parse(&lap_paths),
            trace_lap: Spec::parse(&trace_paths),
            backend_run: Spec::parse(consumed::BACKEND_RUN),
            investigation: Spec::parse(consumed::INVESTIGATION),
            pace_attribution: Spec::parse(consumed::PACE_ATTRIBUTION),
            race_identity: Spec::parse(consumed::RACE_IDENTITY),
            race_summary: Spec::parse(consumed::RACE_RACE_SUMMARY),
            race_grades: Spec::parse(consumed::RACE_RACE_GRADES),
            race_strategy: Spec::parse(consumed::RACE_STRATEGY),
            race_damage_repair: Spec::parse(consumed::RACE_DAMAGE_REPAIR),
            race_data_quality: Spec::parse(consumed::RACE_DATA_QUALITY),
            track_quality: Spec::parse(consumed::TRACK_QUALITY),
        }
    })
}
