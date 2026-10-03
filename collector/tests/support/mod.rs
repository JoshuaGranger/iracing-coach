//! Test support: an in-process HTTP and WebSocket mock of the server's collector surface
//! (`collector-protocol.md`), synthetic SDK maps and recordings, and temp folders.
#![allow(dead_code)]

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tungstenite::handshake::server::{Request, Response as WsResponse};
use tungstenite::protocol::CloseFrame;
use tungstenite::protocol::frame::coding::CloseCode;
use tungstenite::{Message, accept_hdr};

use coach_collector::util::uuid_v4;

pub fn temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("coach-test-{label}-{}", uuid_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

pub fn wait_until(timeout: Duration, mut condition: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + timeout;
    while Instant::now() < end {
        if condition() {
            return true;
        }
        thread::sleep(Duration::from_millis(10));
    }
    condition()
}

// ---- schema validation of what the collector sends ----

const WIRE_SCHEMA: &str = include_str!("../../../crates/coach-wire/schema/wire.schema.json");

pub fn validator_for(def: &str) -> jsonschema::Validator {
    let mut root: Value = serde_json::from_str(WIRE_SCHEMA).unwrap();
    root["$ref"] = json!(format!("#/$defs/{def}"));
    jsonschema::validator_for(&root).unwrap()
}

// ---- mock server ----

#[derive(Debug, Clone)]
pub struct Upload {
    pub size: u64,
    pub kind: String,
    pub file_name: String,
    pub relative_path: Option<String>,
    pub part: Vec<u8>,
    pub verified: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Put {
    pub sha: String,
    pub start: u64,
    pub end: u64,
    pub total: u64,
    pub len: usize,
    pub content_length: usize,
}

#[derive(Debug, Clone)]
pub enum PutFail {
    /// Answer with this status and an optional `Retry-After`; the body is read first.
    Status(u16, Option<u32>),
    /// Close the connection after reading this many body bytes.
    DropAfter(usize),
    /// Read the body, then answer 409 with the current offset and append nothing.
    Conflict,
}

pub type BodyHook = Box<dyn FnMut(&str, usize) + Send>;

#[derive(Default)]
pub struct State {
    pub uploads: HashMap<String, Upload>,
    pub creates: Vec<Value>,
    pub puts: Vec<Put>,
    pub aborted_puts: usize,
    pub create_failures: VecDeque<u16>,
    pub put_failures: VecDeque<PutFail>,
    /// Called with `(sha, body bytes read so far)` while a chunk body arrives.
    pub body_hook: Option<BodyHook>,
    /// Slows body reads to this many bytes per second (0 = no limit).
    pub body_rate: usize,
    /// After this many body bytes the server stops reading altogether (a stalled link) until the
    /// field is cleared or five seconds pass.
    pub stall_after: Option<usize>,
    /// Set while the server is stalled that way.
    pub stalled: bool,

    pub ws_connections: usize,
    pub ws_text: Vec<(usize, Value)>,
    pub ws_binary: Vec<(usize, Vec<u8>)>,
    pub schema_errors: Vec<String>,
    pub subprotocols: Vec<String>,
    pub welcome_stream_wanted: bool,
    pub welcome_viewers: i64,
    pub welcome_layout: Option<Value>,
    pub ws_outbox: VecDeque<Value>,
    pub auto_ack_garage: bool,
    pub close_with: Option<u16>,
    pub drop_ws: bool,
    pub ping_every: Option<Duration>,
    pub protocol_in_welcome: i64,
}

pub struct Mock {
    pub addr: SocketAddr,
    state: Arc<Mutex<State>>,
    stop: Arc<AtomicBool>,
    accept: Option<JoinHandle<()>>,
}

impl Mock {
    pub fn start() -> Mock {
        Mock::start_on("127.0.0.1:0")
    }

    pub fn start_on(addr: &str) -> Mock {
        let listener = TcpListener::bind(addr).unwrap();
        listener.set_nonblocking(true).unwrap();
        let addr = listener.local_addr().unwrap();
        let state = Arc::new(Mutex::new(State {
            auto_ack_garage: true,
            protocol_in_welcome: 1,
            ..State::default()
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let accept = {
            let (state, stop) = (state.clone(), stop.clone());
            thread::spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            stream.set_nonblocking(false).ok();
                            let (state, stop) = (state.clone(), stop.clone());
                            thread::spawn(move || handle(stream, state, stop));
                        }
                        Err(_) => thread::sleep(Duration::from_millis(5)),
                    }
                }
            })
        };
        Mock {
            addr,
            state,
            stop,
            accept: Some(accept),
        }
    }

    pub fn url(&self) -> String {
        format!("http://{}", self.addr)
    }

    /// The shared state, for a helper thread that has to watch it while a test thread is blocked.
    pub fn state_handle(&self) -> Arc<Mutex<State>> {
        self.state.clone()
    }

    pub fn with<R>(&self, f: impl FnOnce(&mut State) -> R) -> R {
        f(&mut self.state.lock().unwrap_or_else(|e| e.into_inner()))
    }

    pub fn texts(&self, kind: &str) -> Vec<Value> {
        self.with(|s| {
            s.ws_text
                .iter()
                .filter(|(_, v)| v["type"] == kind)
                .map(|(_, v)| v.clone())
                .collect()
        })
    }

    pub fn shutdown(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(h) = self.accept.take() {
            let _ = h.join();
        }
    }
}

impl Drop for Mock {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

fn handle(stream: TcpStream, state: Arc<Mutex<State>>, stop: Arc<AtomicBool>) {
    let mut peek = [0u8; 24];
    let n = stream.peek(&mut peek).unwrap_or(0);
    if peek[..n].starts_with(b"GET /ws/collector") {
        ws(stream, state, stop);
    } else {
        http(stream, state);
    }
}

fn lock(state: &Arc<Mutex<State>>) -> std::sync::MutexGuard<'_, State> {
    state.lock().unwrap_or_else(|e| e.into_inner())
}

fn reply(stream: &mut TcpStream, status: u16, body: &Value, extra: &[(&str, String)]) {
    let reason = match status {
        200 => "OK",
        201 => "Created",
        400 => "Bad Request",
        404 => "Not Found",
        409 => "Conflict",
        413 => "Payload Too Large",
        422 => "Unprocessable Entity",
        _ => "Service Unavailable",
    };
    let body = body.to_string();
    let mut head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
        body.len()
    );
    for (k, v) in extra {
        head.push_str(&format!("{k}: {v}\r\n"));
    }
    head.push_str("\r\n");
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body.as_bytes());
    let _ = stream.flush();
}

fn http(mut stream: TcpStream, state: Arc<Mutex<State>>) {
    stream.set_read_timeout(Some(Duration::from_secs(10))).ok();
    let mut reader = BufReader::new(stream.try_clone().unwrap());
    let mut line = String::new();
    if reader.read_line(&mut line).unwrap_or(0) == 0 {
        return;
    }
    let mut parts = line.split_whitespace();
    let (method, path) = (
        parts.next().unwrap_or("").to_string(),
        parts.next().unwrap_or("").to_string(),
    );
    let mut headers = HashMap::new();
    loop {
        line.clear();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            return;
        }
        let t = line.trim_end();
        if t.is_empty() {
            break;
        }
        if let Some((k, v)) = t.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }
    let content_length: usize = headers
        .get("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let sha_in_path = path.strip_prefix("/api/v1/uploads/").map(str::to_string);

    match (method.as_str(), path.as_str()) {
        ("POST", "/api/v1/uploads") => {
            let mut body = vec![0u8; content_length];
            if reader.read_exact(&mut body).is_err() {
                return;
            }
            create(&mut stream, &state, &body);
        }
        ("PUT", _) if sha_in_path.is_some() => {
            put(
                &mut stream,
                &mut reader,
                &state,
                &sha_in_path.unwrap(),
                &headers,
                content_length,
            );
        }
        ("HEAD", _) if sha_in_path.is_some() => {
            let sha = sha_in_path.unwrap();
            let s = lock(&state);
            match s.uploads.get(&sha) {
                Some(u) => {
                    let head = format!(
                        "HTTP/1.1 200 OK\r\nUpload-Offset: {}\r\nUpload-State: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                        u.part.len(),
                        if u.verified { "verified" } else { "open" }
                    );
                    let _ = stream.write_all(head.as_bytes());
                }
                None => reply(&mut stream, 404, &json!({"error": "not_found"}), &[]),
            }
        }
        _ => reply(&mut stream, 404, &json!({"error": "not_found"}), &[]),
    }
}

fn create(stream: &mut TcpStream, state: &Arc<Mutex<State>>, body: &[u8]) {
    let Ok(v) = serde_json::from_slice::<Value>(body) else {
        return reply(stream, 400, &json!({"error": "invalid_request"}), &[]);
    };
    let mut s = lock(state);
    if let Some(code) = s.create_failures.pop_front() {
        let extra: Vec<(&str, String)> = if code == 503 {
            vec![("Retry-After", "1".into())]
        } else {
            vec![]
        };
        return reply(stream, code, &json!({"error": "refused"}), &extra);
    }
    let validator = validator_for("UploadCreate");
    if !validator.is_valid(&v) {
        s.schema_errors.push(format!("UploadCreate: {v}"));
        return reply(stream, 400, &json!({"error": "invalid_request"}), &[]);
    }
    s.creates.push(v.clone());
    let sha = v["sha256"].as_str().unwrap().to_string();
    let size = v["size"].as_u64().unwrap();
    if size > 4 * 1024 * 1024 * 1024 {
        return reply(stream, 413, &json!({"error": "too_large"}), &[]);
    }
    if let Some(u) = s.uploads.get(&sha) {
        let state_name = if u.verified { "verified" } else { "open" };
        return reply(
            stream,
            200,
            &json!({"sha256": sha, "state": state_name, "offset": u.part.len(), "size": size}),
            &[],
        );
    }
    s.uploads.insert(
        sha.clone(),
        Upload {
            size,
            kind: v["kind"].as_str().unwrap().to_string(),
            file_name: v["file_name"].as_str().unwrap().to_string(),
            relative_path: v["relative_path"].as_str().map(str::to_string),
            part: Vec::new(),
            verified: false,
        },
    );
    reply(
        stream,
        201,
        &json!({"sha256": sha, "state": "open", "offset": 0, "size": size}),
        &[],
    );
}

fn put(
    stream: &mut TcpStream,
    reader: &mut BufReader<TcpStream>,
    state: &Arc<Mutex<State>>,
    sha: &str,
    headers: &HashMap<String, String>,
    content_length: usize,
) {
    let range = headers.get("content-range").cloned().unwrap_or_default();
    let parsed = range.strip_prefix("bytes ").and_then(|r| {
        let (span, total) = r.split_once('/')?;
        let (a, b) = span.split_once('-')?;
        Some((
            a.parse::<u64>().ok()?,
            b.parse::<u64>().ok()?,
            total.parse::<u64>().ok()?,
        ))
    });
    let Some((start, end, total)) = parsed else {
        return reply(stream, 400, &json!({"error": "invalid_request"}), &[]);
    };
    let fail = lock(state).put_failures.pop_front();
    let drop_after = match &fail {
        Some(PutFail::DropAfter(n)) => Some(*n),
        _ => None,
    };
    // Read the body piecewise so a client that abandons the request is noticed and the hook can
    // flip the on-track gate part way through.
    let mut body = Vec::with_capacity(content_length);
    let mut buf = [0u8; 4096];
    while body.len() < content_length {
        let want = buf.len().min(content_length - body.len());
        match reader.read(&mut buf[..want]) {
            Ok(0) | Err(_) => {
                lock(state).aborted_puts += 1;
                return;
            }
            Ok(n) => body.extend_from_slice(&buf[..n]),
        }
        let rate = lock(state).body_rate;
        if rate > 0 {
            thread::sleep(Duration::from_secs_f64(buf.len() as f64 / rate as f64));
        }
        let mut hook = lock(state).body_hook.take();
        if let Some(h) = hook.as_mut() {
            h(sha, body.len());
        }
        if hook.is_some() {
            let mut s = lock(state);
            if s.body_hook.is_none() {
                s.body_hook = hook;
            }
        }
        if drop_after.is_some_and(|n| body.len() >= n) {
            lock(state).aborted_puts += 1;
            return;
        }
        if lock(state).stall_after.is_some_and(|n| body.len() >= n) {
            lock(state).stalled = true;
            let until = Instant::now() + Duration::from_secs(5);
            while lock(state).stall_after.is_some() && Instant::now() < until {
                thread::sleep(Duration::from_millis(5));
            }
            let mut s = lock(state);
            s.stalled = false;
            s.aborted_puts += 1;
            return;
        }
    }
    let mut s = lock(state);
    s.puts.push(Put {
        sha: sha.to_string(),
        start,
        end,
        total,
        len: body.len(),
        content_length,
    });
    if matches!(fail, Some(PutFail::Conflict)) {
        let (offset, size) = s
            .uploads
            .get(sha)
            .map_or((0, 0), |u| (u.part.len(), u.size));
        return reply(
            stream,
            409,
            &json!({"sha256": sha, "state": "open", "offset": offset, "size": size}),
            &[],
        );
    }
    if let Some(PutFail::Status(code, retry)) = fail {
        let extra: Vec<(&str, String)> = retry
            .map(|r| ("Retry-After", r.to_string()))
            .into_iter()
            .collect();
        return reply(stream, code, &json!({"error": "refused"}), &extra);
    }
    let Some(up) = s.uploads.get_mut(sha) else {
        return reply(stream, 404, &json!({"error": "not_found"}), &[]);
    };
    let offset = up.part.len() as u64;
    if start != offset || end - start + 1 != body.len() as u64 {
        return reply(
            stream,
            409,
            &json!({"sha256": sha, "state": "open", "offset": offset, "size": up.size}),
            &[],
        );
    }
    up.part.extend_from_slice(&body);
    if up.part.len() as u64 == up.size {
        if sha256_hex(&up.part) == sha {
            up.verified = true;
            let size = up.size;
            return reply(
                stream,
                200,
                &json!({"sha256": sha, "state": "verified", "offset": size, "size": size}),
                &[],
            );
        }
        up.part.clear();
        return reply(stream, 422, &json!({"error": "hash_mismatch"}), &[]);
    }
    let (offset, size) = (up.part.len(), up.size);
    reply(
        stream,
        200,
        &json!({"sha256": sha, "state": "open", "offset": offset, "size": size}),
        &[],
    );
}

#[allow(clippy::result_large_err)] // the handshake callback's error type is tungstenite's
fn ws(stream: TcpStream, state: Arc<Mutex<State>>, stop: Arc<AtomicBool>) {
    let sub = state.clone();
    let callback = move |req: &Request, mut resp: WsResponse| {
        let protocols = req
            .headers()
            .get("Sec-WebSocket-Protocol")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        lock(&sub).subprotocols.push(protocols.clone());
        if protocols
            .split(',')
            .any(|p| p.trim() == "coach-collector.1")
        {
            resp.headers_mut().insert(
                "Sec-WebSocket-Protocol",
                "coach-collector.1".parse().unwrap(),
            );
        }
        Ok(resp)
    };
    let Ok(mut ws) = accept_hdr(stream, callback) else {
        return;
    };
    let conn = {
        let mut s = lock(&state);
        s.ws_connections += 1;
        s.ws_connections
    };
    let validator = validator_for("CollectorClientMessage");
    ws.get_ref()
        .set_read_timeout(Some(Duration::from_millis(20)))
        .ok();
    let mut welcomed = false;
    let mut last_ping = Instant::now();
    let mut nonce = 0;
    loop {
        if stop.load(Ordering::SeqCst) {
            return;
        }
        {
            let mut s = lock(&state);
            if s.drop_ws {
                s.drop_ws = false;
                return;
            }
            if welcomed {
                while let Some(m) = s.ws_outbox.pop_front() {
                    if ws.send(Message::text(m.to_string())).is_err() {
                        return;
                    }
                }
                if s.ping_every.is_some_and(|d| last_ping.elapsed() >= d) {
                    last_ping = Instant::now();
                    nonce += 1;
                    let _ = ws.send(Message::text(
                        json!({"type": "ping", "nonce": nonce}).to_string(),
                    ));
                }
            }
        }
        match ws.read() {
            Ok(Message::Text(t)) => {
                let Ok(v) = serde_json::from_str::<Value>(t.as_str()) else {
                    continue;
                };
                let mut s = lock(&state);
                if !validator.is_valid(&v) {
                    s.schema_errors.push(format!("CollectorClientMessage: {v}"));
                }
                s.ws_text.push((conn, v.clone()));
                match v["type"].as_str() {
                    Some("hello") if !welcomed => {
                        welcomed = true;
                        if let Some(code) = s.close_with {
                            let _ = ws.close(Some(CloseFrame {
                                code: CloseCode::Library(code),
                                reason: "".into(),
                            }));
                            let _ = ws.flush();
                            return;
                        }
                        let mut welcome = json!({
                            "type": "welcome",
                            "protocol": s.protocol_in_welcome,
                            "server_version": "0.19.0-test",
                            "stream_wanted": s.welcome_stream_wanted,
                            "viewers": s.welcome_viewers,
                        });
                        if let Some(layout) = s.welcome_layout.clone() {
                            welcome["layout"] = layout;
                        }
                        let _ = ws.send(Message::text(welcome.to_string()));
                    }
                    Some("garage.snapshot") if s.auto_ack_garage => {
                        let ack = json!({"type": "garage.ack", "visit_id": v["visit_id"], "kind": "snapshot", "update_count": v["update_count"]});
                        let _ = ws.send(Message::text(ack.to_string()));
                    }
                    Some("garage.commit") if s.auto_ack_garage => {
                        let ack = json!({"type": "garage.ack", "visit_id": v["visit_id"], "kind": "commit", "update_count": v["update_count"]});
                        let _ = ws.send(Message::text(ack.to_string()));
                    }
                    _ => {}
                }
            }
            Ok(Message::Binary(b)) => lock(&state).ws_binary.push((conn, b.to_vec())),
            Ok(Message::Close(_)) => return,
            Ok(_) => {}
            Err(tungstenite::Error::Io(e))
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) => {}
            Err(_) => return,
        }
    }
}

// ---- synthetic recordings ----

pub fn write_file(path: &Path, bytes: &[u8]) {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).unwrap();
    }
    std::fs::write(path, bytes).unwrap();
}

/// Sets a file's modification time to `secs` seconds before now.
pub fn age_file(path: &Path, secs: u64) {
    let f = std::fs::OpenOptions::new().write(true).open(path).unwrap();
    f.set_modified(std::time::SystemTime::now() - Duration::from_secs(secs))
        .unwrap();
}

// ---- replay harness ----

use std::sync::mpsc::{Receiver, channel};

use coach_collector::fixture::{MapBuilder, Pacing, ReplaySource, Row};
use coach_collector::garage::GarageAction;
use coach_collector::hub::Hub;
use coach_collector::journal::Journal;
use coach_collector::pipeline::Pipeline;
use coach_collector::sdk::SdkReader;
use coach_collector::util::{Clock, ManualClock};

/// Drives the real reader, engine and pipeline over a fixture one record at a time, on a virtual
/// clock the replay advances, collecting every frame the pipeline queues.
pub struct Harness {
    pub hub: Arc<Hub>,
    pub clock: Arc<ManualClock>,
    pub reader: SdkReader,
    pub pipeline: Pipeline,
    pub garage_rx: Receiver<GarageAction>,
    pub frames: Vec<Vec<u8>>,
    pub done: bool,
}

impl Harness {
    pub fn new(fixture: &[u8]) -> Harness {
        let clock = Arc::new(ManualClock::new(1_790_000_000_000));
        let hub = Hub::new(clock.clone(), Journal::in_memory(), true);
        let source = ReplaySource::from_bytes(fixture, Pacing::Virtual(clock.clone())).unwrap();
        let mut reader = SdkReader::new(Box::new(source));
        let (tx, garage_rx) = channel();
        let pipeline = Pipeline::new(hub.clone(), Some(tx), None);
        // Opens the map (no sleeping: the first wait would otherwise pace the probe).
        reader.poll(clock.mono_ms(), clock.unix_ms());
        Harness {
            hub,
            clock,
            reader,
            pipeline,
            garage_rx,
            frames: Vec::new(),
            done: false,
        }
    }

    /// One replayed record. False once the replay has been consumed.
    pub fn step(&mut self) -> bool {
        if self.done {
            return false;
        }
        self.reader.wait(Duration::from_millis(100));
        let poll = self.reader.poll(self.clock.mono_ms(), self.clock.unix_ms());
        self.hub.gate.observe(self.reader.evidence());
        self.pipeline.on_poll(poll, &self.reader);
        while let Some(f) = self.hub.frames.pop() {
            self.frames.push(f);
        }
        if self.reader.at_end() {
            self.done = true;
        }
        !self.done
    }

    pub fn run(&mut self) {
        while self.step() {}
    }

    pub fn garage_actions(&self) -> Vec<GarageAction> {
        self.garage_rx.try_iter().collect()
    }
}

/// A row with the engine's common variables set for a car on track at `speed` m/s.
pub fn driving_row(b: &MapBuilder, speed: f64) -> Row {
    let mut row = b.row();
    row.set_f64("Speed", speed)
        .set_f64("SessionTime", 100.0)
        .set_i32("SessionNum", 0)
        .set_i32("SessionUniqueID", 1)
        .set_i32("PlayerCarIdx", 0)
        .set_i32("PlayerCarPosition", 1)
        .set_i32("Lap", 3)
        .set_f64("LapDistPct", 0.25)
        .set_f64("Throttle", 0.5)
        .set_bool("IsOnTrack", true)
        .set_bits("SessionFlags", 0x4);
    row
}

// ---- the P5 garage fixture ----

use coach_collector::fixture::{SessionSpec, Step};

pub const CAR: &str = "stockcars2 supra2019";
pub const STO_NAME: &str = "claude LV R CAM.sto";

pub fn session(count: i64, name: &str, modified: bool, pressure: &str) -> String {
    SessionSpec {
        update_count: count,
        setup_name: name.to_string(),
        setup_modified: modified,
        setup_leaves: vec![
            (
                "Tires.LeftFront".into(),
                "ColdPressure".into(),
                pressure.into(),
            ),
            (
                "Chassis.Front".into(),
                "SteeringRatio".into(),
                "16.5:1".into(),
            ),
        ],
        ..SessionSpec::default()
    }
    .yaml()
}

pub fn tick_steps(
    b: &MapBuilder,
    t0: u32,
    tick0: i32,
    count: usize,
    garage: bool,
    on_track: bool,
) -> Vec<Step> {
    (0..count)
        .map(|i| {
            let mut row = driving_row(b, 0.0);
            row.set_bool("IsInGarage", garage)
                .set_bool("IsOnTrack", on_track);
            Step::Tick {
                t_ms: t0 + i as u32 * 100,
                tick: tick0 + i as i32,
                row,
            }
        })
        .collect()
}

/// Session start (name R, count 2), a garage visit with loads and applies (counts 3 to 9, the name
/// still R), Apply and Close (IsInGarage drops, then count 10 and the new name arrive), then
/// `after_ms` of quiet time off track.
pub fn p5_fixture(b: &MapBuilder, exit_name: &str, after_ms: u32) -> Vec<u8> {
    let mut steps = vec![Step::Static {
        status: 1,
        update: 1,
        session_info: session(2, "R.sto", false, "152 kPa"),
    }];
    steps.extend(tick_steps(b, 0, 100, 5, false, false));
    // In the garage; every load and apply bumps UpdateCount.
    let mut t = 1_000;
    let mut tick = 200;
    steps.extend(tick_steps(b, t, tick, 3, true, false));
    t += 300;
    tick += 3;
    for count in 3..=9 {
        steps.push(Step::Static {
            status: 1,
            update: count as i32,
            session_info: session(count, "R.sto", false, &format!("{} kPa", 150 + count)),
        });
        steps.extend(tick_steps(b, t, tick, 3, true, false));
        t += 300;
        tick += 3;
    }
    // Apply and Close: IsInGarage drops first, the name and count 10 follow.
    steps.extend(tick_steps(b, t, tick, 2, false, false));
    t += 200;
    tick += 2;
    steps.push(Step::Static {
        status: 1,
        update: 10,
        session_info: session(10, exit_name, true, "155 kPa"),
    });
    let quiet = (after_ms / 100) as usize;
    steps.extend(tick_steps(b, t, tick, quiet, false, false));
    let mut all = Vec::new();
    all.extend(steps);
    b.write_steps(Vec::new(), &all).unwrap()
}

pub fn setups_with_sto() -> (PathBuf, Vec<u8>) {
    let root = temp_dir("garage");
    let setups = root.join("setups");
    let bytes = b"encrypted setup payload that only iRacing can read".to_vec();
    write_file(&setups.join(CAR).join(STO_NAME), &bytes);
    write_file(&setups.join(CAR).join("R.sto"), b"the session-start setup");
    (setups, bytes)
}

pub fn tree_hash(dir: &Path) -> Vec<(String, String)> {
    fn walk(dir: &Path, out: &mut Vec<(String, String)>) {
        for e in std::fs::read_dir(dir).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else {
                out.push((
                    p.to_string_lossy().into_owned(),
                    sha256_hex(&std::fs::read(&p).unwrap()),
                ));
            }
        }
    }
    let mut v = Vec::new();
    walk(dir, &mut v);
    v.sort();
    v
}
