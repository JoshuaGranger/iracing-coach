//! Shared test harness: temporary data and NAS directories, fake IBT files, a fake recording
//! parser and view projector (the library crates are exercised by their own tests), the stub
//! worker command, an in-process HTTP client and the OpenAPI contract checker.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::{Body, Bytes};
use axum::http::{HeaderMap, Method, Request, StatusCode};
use coach_server::adapters::{
    ParsedRecording, ParsedSetup, ProjectionInfo, Projector, RecordingParser, SelectFailure,
};
use coach_server::faults::Faults;
use coach_server::{App, AppState, Config, Parts, api};
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

pub const CUSTOMER: i64 = 1001;

// ------------------------------------------------------------------ fake IBT

/// A fake recording: one JSON header line, then deterministic padding up to `len` bytes.
pub fn fake_ibt(meta: Value, len: usize) -> Vec<u8> {
    let mut bytes = format!("{meta}\n").into_bytes();
    let mut seed = coach_server::util::sha256_hex(meta.to_string().as_bytes()).into_bytes();
    while bytes.len() < len {
        bytes.extend_from_slice(&seed);
        seed = coach_server::util::sha256_hex(&seed).into_bytes();
    }
    bytes.truncate(len.max(1));
    bytes
}

pub fn race_meta(subsession: i64, num: i64, name: &str) -> Value {
    json!({
        "customer_id": CUSTOMER, "user_name": "Joshua Granger", "irating": 1500,
        "subsession_id": subsession, "sim_session_num": num, "session_type": "Race",
        "track": "Stub Speedway", "car_path": "stockcars chevyss2020", "car_name": "Stock Car",
        "start": "2026-08-01T12:00:00Z", "name": name,
        "setup": {"values": {"Chassis.Front.ArbSetting": "4", "Chassis.LeftFront.CornerWeight": "1700 N", "UpdateCount": "3"},
                  "name": "baseline.sto", "is_modified": false, "load_type": "user", "update_count": 3}
    })
}

pub struct FakeParser;

pub fn fake_fingerprint(values: &BTreeMap<String, String>) -> String {
    let mut text = String::new();
    for (k, v) in values {
        if coach_server::setups::is_identity_excluded(k) {
            continue;
        }
        text.push_str(&format!("{k}\u{1f}{}\u{1e}", v.trim()));
    }
    coach_server::util::sha256_hex(text.as_bytes())[..32].to_string()
}

impl RecordingParser for FakeParser {
    fn parse(&self, path: &Path) -> Result<ParsedRecording, String> {
        let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
        let line = bytes.split(|b| *b == b'\n').next().unwrap_or_default();
        let meta: Value =
            serde_json::from_slice(line).map_err(|e| format!("not a fake ibt: {e}"))?;
        if let Some(message) = meta["fail"].as_str() {
            return Err(message.to_string());
        }
        let setup = meta["setup"].as_object().map(|s| {
            let values: BTreeMap<String, String> = s["values"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
                        .collect()
                })
                .unwrap_or_default();
            ParsedSetup {
                fingerprint: fake_fingerprint(&values),
                values,
                setup_name: s["name"].as_str().map(str::to_string),
                is_modified: s["is_modified"].as_bool(),
                load_type: s["load_type"].as_str().map(str::to_string),
                update_count: s["update_count"].as_i64(),
            }
        });
        Ok(ParsedRecording {
            customer_id: meta["customer_id"].as_i64(),
            user_name: meta["user_name"].as_str().map(str::to_string),
            irating: meta["irating"].as_i64(),
            record_count: 100,
            tick_rate: 60,
            buffer_length: 512,
            variable_count: 12,
            session_info_sha256: coach_server::util::sha256_hex(line),
            subsession_id: meta["subsession_id"].as_i64(),
            sim_session_num: meta["sim_session_num"].as_i64(),
            session_types: vec![meta["session_type"].as_str().unwrap_or("Race").to_string()],
            sim_session_type: meta["session_type"].as_str().map(str::to_string),
            is_race: meta["session_type"] == "Race",
            track: meta["track"].as_str().map(str::to_string),
            track_config: meta["track_config"].as_str().map(str::to_string),
            car_path: meta["car_path"].as_str().map(str::to_string),
            car_name: meta["car_name"].as_str().map(str::to_string),
            series_name: Some("Stub Series".into()),
            season_name: None,
            start_time_utc: meta["start"].as_str().map(str::to_string),
            header_json: json!({"buffer_length": 512, "variable_count": 12}),
            setup,
        })
    }

    fn fingerprint(&self, values: &BTreeMap<String, String>) -> String {
        fake_fingerprint(values)
    }
}

// ------------------------------------------------------------ fake projector

pub const PROJECTION: u32 = 7;

pub struct FakeProjector {
    pub fail: Mutex<bool>,
}

impl Default for FakeProjector {
    fn default() -> Self {
        FakeProjector {
            fail: Mutex::new(false),
        }
    }
}

fn envelope(analysis_id: &str) -> Value {
    json!({"analysis_id": analysis_id, "projection_version": PROJECTION, "schema_version": 14, "profile_version": "stub-v1"})
}

fn with(mut base: Value, extra: Value) -> Value {
    if let (Some(b), Some(e)) = (base.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            b.insert(k.clone(), v.clone());
        }
    }
    base
}

pub fn ccol(analysis_id: &str, laps: &[i64]) -> Vec<u8> {
    let bins = 4usize;
    let mut data = Vec::new();
    let mut channels = Vec::new();
    for id in ["speed", "throttle"] {
        let offset = data.len();
        for lap in laps {
            for bin in 0..bins {
                data.extend_from_slice(&((*lap as f64) * 10.0 + bin as f64).to_le_bytes());
            }
        }
        channels.push(json!({"id": id, "dtype": "f64", "offset": offset, "byte_length": data.len() - offset, "missing": null}));
    }
    let header = json!({"format": "CCOL", "version": 1, "analysis_id": analysis_id, "projection_version": PROJECTION,
                        "bins": bins, "laps": laps, "channels": channels})
    .to_string()
    .into_bytes();
    let mut out = b"CCOL".to_vec();
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&(header.len() as u32).to_le_bytes());
    out.extend_from_slice(&header);
    while !out.len().is_multiple_of(8) {
        out.push(0);
    }
    out.extend_from_slice(&data);
    out
}

pub fn tuning_json(analysis_id: &str, setup_type: &str, fingerprint: &str) -> Value {
    json!({
        "analysis_id": analysis_id, "eligible": true, "can_use_as_evidence": true, "reasons": [],
        "identity": {
            "analysis_id": analysis_id, "iracing_session_id": "7001", "session_unique_id": 3, "subsession_id": 8001,
            "session_type": "Race", "track_configuration_key": "stub-speedway:oval", "track_id": 9001, "track": "Stub Speedway",
            "layout": "Oval", "car_id": 9002, "car_path": "stockcars chevyss2020", "car": "Stock Car",
            "setup_type": setup_type, "setup_fingerprint": fingerprint, "embedded_setup_available": true,
            "source_sha256": ["a".repeat(64)],
        },
        "map": {
            "map_identity": "stub-speedway:oval", "source_type": "telemetry-derived", "source_label": "Recorded racing geometry",
            "confidence": "Recorded", "is_verified": false,
            "path": [{"lap_pct": 0.0, "x": 0.1, "y": 0.2}, {"lap_pct": 0.5, "x": 0.8, "y": 0.2}],
            "turns": [], "geometry_hash": "b".repeat(64),
        },
        "representative_runs": [],
    })
}

impl Projector for FakeProjector {
    fn version(&self) -> u32 {
        PROJECTION
    }

    fn project(&self, analysis_json: &Path, out_dir: &Path) -> Result<ProjectionInfo, String> {
        if *self.fail.lock().unwrap() {
            return Err("projection failed on purpose".into());
        }
        let analysis: Value =
            serde_json::from_slice(&std::fs::read(analysis_json).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        let id = analysis["analysis_id"]
            .as_str()
            .ok_or("no analysis_id")?
            .to_string();
        let tmp = out_dir.with_extension("tmp-fake");
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).map_err(|e| e.to_string())?;
        let write = |name: &str, value: Value| {
            std::fs::write(tmp.join(name), value.to_string()).map_err(|e| e.to_string())
        };
        write(
            "race.json",
            with(
                envelope(&id),
                json!({
                    "identity": {"track_name": "Stub Speedway"},
                    "race_summary": {"recorded_laps": 2, "starting_position": 5, "final_recorded_position": 3, "pit_stops_detected": 1,
                                     "green_laps_estimated": 2.0, "fuel_used_gal": 3.5},
                    "laps": [],
                }),
            ),
        )?;
        write("runs.json", with(envelope(&id), json!({"runs": []})))?;
        write(
            "traces-catalog.json",
            with(
                envelope(&id),
                json!({
                    "bins_per_lap": 4, "laps": [], "signals": [],
                    "steering_basis": {"basis": "unknown"},
                    "budgets": {"vertex_budget_per_signal": 48000, "min_points_per_trace": 48, "max_color_groups": 20,
                                "cursor_detail_traces": 24, "max_trace_rows": 10},
                }),
            ),
        )?;
        std::fs::write(tmp.join("traces.ccol"), ccol(&id, &[1, 2, 3]))
            .map_err(|e| e.to_string())?;
        write(
            "track.json",
            with(
                envelope(&id),
                json!({
                    "coordinate_space": "normalized_local_vector", "status": "usable",
                    "main_path": {"x": [0.0, 1.0], "y": [0.0, 1.0], "pct": [0.0, 0.5]}, "provenance": {},
                }),
            ),
        )?;
        let setup_type = analysis["setup_type"].as_str().unwrap_or("Open");
        write("tuning.json", tuning_json(&id, setup_type, "fp-1"))?;
        if analysis["no_investigation"] != true {
            write(
                "investigation.json",
                with(
                    envelope(&id),
                    json!({"race_investigation": {"schema_version": 2}}),
                ),
            )?;
        }
        std::fs::rename(&tmp, out_dir).map_err(|e| e.to_string())?;
        Ok(ProjectionInfo {
            analysis_id: id,
            projection_version: PROJECTION,
            schema_version: 14,
            profile_version: "stub-v1".into(),
            lap_count: 2,
        })
    }

    fn select_traces(
        &self,
        view_dir: &Path,
        laps: Option<&str>,
        channels: Option<&str>,
        max_bytes: u64,
    ) -> Result<Vec<u8>, SelectFailure> {
        let _ = channels;
        let all = std::fs::read(view_dir.join("traces.ccol"))
            .map_err(|e| SelectFailure::Io(e.to_string()))?;
        if let Some(text) = laps {
            if text.contains("bad") {
                return Err(SelectFailure::BadLaps(text.to_string()));
            }
            if text == "toolarge" {
                return Err(SelectFailure::TooLarge {
                    bytes: 20 << 20,
                    limit: max_bytes,
                });
            }
        }
        if all.len() as u64 > max_bytes {
            return Err(SelectFailure::TooLarge {
                bytes: all.len() as u64,
                limit: max_bytes,
            });
        }
        Ok(all)
    }
}

// --------------------------------------------------------------- python/stub

/// A working Python 3 interpreter, if any: `COACH_TEST_PYTHON`, then `python3`, `python`.
pub fn python() -> Option<String> {
    let mut candidates: Vec<String> = Vec::new();
    if let Ok(p) = std::env::var("COACH_TEST_PYTHON") {
        candidates.push(p);
    }
    candidates.push("python3".into());
    candidates.push("python".into());
    for candidate in candidates {
        let child = std::process::Command::new(&candidate)
            .args(["-c", "import sys; print(sys.version_info[0])"])
            .stdin(std::process::Stdio::null())
            .output();
        if let Ok(out) = child
            && String::from_utf8_lossy(&out.stdout).trim() == "3"
        {
            return Some(candidate);
        }
    }
    None
}

pub fn stub_script() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("support")
        .join("stub_worker.py")
}

// ---------------------------------------------------------------------- env

pub struct Env {
    pub dir: Arc<TempDir>,
    pub app: AppState,
    pub router: Router,
    pub faults: Arc<Faults>,
    pub projector: Arc<FakeProjector>,
    pub python: Option<String>,
}

pub struct Opts {
    pub tweak: Box<dyn Fn(&mut Config)>,
    pub background: bool,
}

impl Default for Opts {
    fn default() -> Self {
        Opts {
            tweak: Box::new(|_| {}),
            background: false,
        }
    }
}

pub fn base_config(dir: &Path, python: Option<&str>) -> Config {
    let command = match python {
        Some(p) => vec![
            p.to_string(),
            "-X".into(),
            "utf8".into(),
            "-u".into(),
            stub_script().to_string_lossy().into_owned(),
        ],
        None => vec!["no-python-available".into()],
    };
    let mut cfg = Config::base(dir.join("data"), dir.join("nas"), command);
    cfg.background = false;
    cfg.worker.set_oom_score = false;
    cfg.worker.kill_grace = std::time::Duration::from_secs(2);
    cfg.worker.analyze_timeout = std::time::Duration::from_secs(60);
    cfg.worker.discover_timeout = std::time::Duration::from_secs(60);
    cfg.worker.light_timeout = std::time::Duration::from_secs(60);
    cfg.min_free_bytes = 0;
    cfg
}

impl Env {
    pub async fn new() -> Env {
        Env::with(Opts::default()).await
    }

    pub async fn with(opts: Opts) -> Env {
        let dir = Arc::new(TempDir::new().expect("tempdir"));
        let python = python();
        let mut cfg = base_config(dir.path(), python.as_deref());
        cfg.background = opts.background;
        (opts.tweak)(&mut cfg);
        Env::start(dir, cfg, python).await
    }

    async fn start(dir: Arc<TempDir>, cfg: Config, python: Option<String>) -> Env {
        let faults = Arc::new(Faults::default());
        let projector = Arc::new(FakeProjector::default());
        let parts = Parts {
            parser: Arc::new(FakeParser),
            projector: projector.clone(),
            disk: Arc::new(coach_server::app::SystemDisk),
            faults: faults.clone(),
            worker_identity: Some(coach_server::app::WorkerIdentity {
                bundle_digest: "stubbundle".into(),
                profile_version: Some("stub-v1".into()),
            }),
        };
        let app = App::start(cfg, parts).await.expect("app starts");
        let router = api::router(app.clone());
        Env {
            dir,
            app,
            router,
            faults,
            projector,
            python,
        }
    }

    /// Simulates a process restart over the same directories (new in-memory state, new faults).
    pub async fn restart(self) -> Env {
        let Env {
            dir,
            app,
            router,
            python,
            ..
        } = self;
        let cfg = app.cfg.clone();
        drop(router);
        // Background tasks may hold clones; tests that restart run with background off.
        drop(app);
        Env::start(dir, cfg, python).await
    }

    pub fn nas(&self) -> PathBuf {
        self.dir.path().join("nas")
    }

    pub fn data(&self) -> PathBuf {
        self.dir.path().join("data")
    }

    pub async fn call(
        &self,
        method: Method,
        path: &str,
        body: Option<Bytes>,
        headers: &[(&str, &str)],
    ) -> Reply {
        let mut request = Request::builder().method(method).uri(path);
        for (k, v) in headers {
            request = request.header(*k, *v);
        }
        let request = request
            .body(body.map(Body::from).unwrap_or_else(Body::empty))
            .expect("request");
        let response = self
            .router
            .clone()
            .oneshot(request)
            .await
            .expect("router answers");
        let (parts, body) = response.into_parts();
        let bytes = axum::body::to_bytes(body, 256 << 20).await.expect("body");
        Reply {
            status: parts.status,
            headers: parts.headers,
            body: bytes,
        }
    }

    /// Sends a request and returns the response before its body is read (server-sent events).
    pub async fn call_stream(&self, path: &str) -> axum::http::Response<Body> {
        let request = Request::builder()
            .method(Method::GET)
            .uri(path)
            .body(Body::empty())
            .expect("request");
        self.router
            .clone()
            .oneshot(request)
            .await
            .expect("router answers")
    }

    pub async fn get(&self, path: &str) -> Reply {
        self.call(Method::GET, path, None, &[]).await
    }

    pub async fn post(&self, path: &str, body: Value) -> Reply {
        self.call(
            Method::POST,
            path,
            Some(Bytes::from(body.to_string())),
            &[("content-type", "application/json")],
        )
        .await
    }

    pub async fn put(&self, path: &str, body: Value, if_match: Option<i64>) -> Reply {
        let version = if_match.map(|v| v.to_string());
        let mut headers = vec![("content-type", "application/json")];
        if let Some(v) = &version {
            headers.push(("if-match", v.as_str()));
        }
        self.call(
            Method::PUT,
            path,
            Some(Bytes::from(body.to_string())),
            &headers,
        )
        .await
    }

    pub async fn delete(&self, path: &str, if_match: Option<i64>) -> Reply {
        let version = if_match.map(|v| v.to_string());
        let mut headers = vec![];
        if let Some(v) = &version {
            headers.push(("if-match", v.as_str()));
        }
        self.call(Method::DELETE, path, None, &headers).await
    }

    /// Uploads `bytes` with the collector's protocol; returns the final reply.
    pub async fn upload(
        &self,
        bytes: &[u8],
        kind: &str,
        name: &str,
        rel: Option<&str>,
        chunk: usize,
    ) -> Reply {
        let sha = coach_server::util::sha256_hex(bytes);
        let create = self
            .create_upload(&sha, bytes.len(), kind, name, rel, None)
            .await;
        if create.status != StatusCode::CREATED && create.status != StatusCode::OK {
            return create;
        }
        let j = create.json();
        if j["state"] != "open" {
            return create;
        }
        let mut offset = j["offset"].as_u64().unwrap_or(0) as usize;
        let mut last = create;
        while offset < bytes.len() {
            let end = (offset + chunk).min(bytes.len());
            last = self
                .put_chunk(&sha, bytes.len(), offset, &bytes[offset..end])
                .await;
            if last.status != StatusCode::OK {
                return last;
            }
            offset = end;
        }
        last
    }

    pub async fn create_upload(
        &self,
        sha: &str,
        size: usize,
        kind: &str,
        name: &str,
        rel: Option<&str>,
        customer: Option<i64>,
    ) -> Reply {
        self.post(
            "/api/v1/uploads",
            json!({"sha256": sha, "size": size, "kind": kind, "file_name": name, "relative_path": rel,
                   "source_mtime": "2026-08-01T12:30:00Z", "collector_id": COLLECTOR, "customer_id": customer}),
        )
        .await
    }

    pub async fn put_chunk(&self, sha: &str, size: usize, start: usize, bytes: &[u8]) -> Reply {
        let range = format!("bytes {}-{}/{}", start, start + bytes.len() - 1, size);
        self.call(
            Method::PUT,
            &format!("/api/v1/uploads/{sha}"),
            Some(Bytes::copy_from_slice(bytes)),
            &[
                ("content-range", range.as_str()),
                ("content-type", "application/octet-stream"),
            ],
        )
        .await
    }

    /// Parse every verified recording and run every queued job.
    pub async fn settle(&self) {
        coach_server::ingest::ingest_pending(&self.app)
            .await
            .expect("ingest");
        coach_server::jobs::drain(&self.app).await.expect("drain");
        coach_server::ingest::ingest_pending(&self.app)
            .await
            .expect("ingest again");
        coach_server::jobs::drain(&self.app)
            .await
            .expect("drain again");
    }

    pub fn query<T: Send + 'static>(
        &self,
        f: impl FnOnce(&rusqlite::Connection) -> rusqlite::Result<T>,
    ) -> T {
        self.app
            .store
            .read_blocking(|c| f(c).map_err(coach_server::error::Error::from))
            .expect("query")
    }
}

pub const COLLECTOR: &str = "11111111-2222-4333-8444-555555555555";

pub struct Reply {
    pub status: StatusCode,
    pub headers: HeaderMap,
    pub body: Bytes,
}

impl Reply {
    pub fn json(&self) -> Value {
        serde_json::from_slice(&self.body)
            .unwrap_or_else(|e| panic!("not JSON ({e}): {}", String::from_utf8_lossy(&self.body)))
    }
    pub fn header(&self, name: &str) -> Option<String> {
        self.headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .map(str::to_string)
    }
}

// ----------------------------------------------------------------- contract

/// The OpenAPI document with response lookup and JSON Schema validation.
pub struct Contract {
    doc: Value,
    pub seen: Mutex<std::collections::BTreeSet<String>>,
}

impl Contract {
    pub fn load() -> Contract {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../docs/08-rewrite/contracts/api.openapi.yaml");
        let text = std::fs::read_to_string(&path).expect("read api.openapi.yaml");
        let value: serde_yaml_ng::Value =
            serde_yaml_ng::from_str(&text).expect("parse api.openapi.yaml");
        let doc: Value = serde_json::to_value(value).expect("yaml to json");
        Contract {
            doc,
            seen: Mutex::new(Default::default()),
        }
    }

    /// Every `METHOD path` of the document except WebSocket upgrades.
    pub fn operations(&self) -> Vec<String> {
        let mut ops = Vec::new();
        for (path, item) in self.doc["paths"].as_object().expect("paths") {
            for (method, op) in item.as_object().expect("item") {
                if op.get("responses").is_some() && !op.get("x-websocket").is_some() {
                    ops.push(format!("{} {}", method.to_uppercase(), path));
                }
            }
        }
        ops.sort();
        ops
    }

    fn resolve<'a>(&'a self, node: &'a Value) -> &'a Value {
        match node["$ref"].as_str() {
            Some(r) => {
                let mut cur = &self.doc;
                for part in r.trim_start_matches("#/").split('/') {
                    cur = &cur[part];
                }
                cur
            }
            None => node,
        }
    }

    /// Validates a response against the schema the contract gives for `method template status`.
    pub fn check(
        &self,
        method: &str,
        template: &str,
        status: u16,
        content_type: &str,
        body: &Value,
    ) {
        let op = &self.doc["paths"][template][method.to_lowercase()];
        assert!(op.is_object(), "{method} {template} is not in the contract");
        let response = self.resolve(&op["responses"][status.to_string()]);
        assert!(
            response.is_object(),
            "{method} {template} has no {status} response in the contract"
        );
        self.seen
            .lock()
            .unwrap()
            .insert(format!("{method} {template}"));
        let schema = &response["content"][content_type]["schema"];
        if !schema.is_object() {
            return; // no body declared (204, 404 without content, ...)
        }
        let root = json!({"$schema": "https://json-schema.org/draft/2020-12/schema",
                          "components": self.doc["components"], "allOf": [schema]});
        let validator = jsonschema::validator_for(&root).expect("compile contract schema");
        let errors: Vec<String> = validator
            .iter_errors(body)
            .take(5)
            .map(|e| format!("{}: {e}", e.instance_path()))
            .collect();
        assert!(
            errors.is_empty(),
            "{method} {template} {status} violates the contract: {errors:?}\nbody: {body}"
        );
    }
}
