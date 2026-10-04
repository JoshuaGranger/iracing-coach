//! The server link (`collector-protocol.md` sections 1, 3, 5, 6, 7, 8, 9, 11): one WebSocket that
//! carries hello, presence, stream control, layout sync, garage events, ingest states and the
//! binary live frames. The collector always dials out; every close is followed by a reconnect with
//! backoff. All state it shares lives in the `Hub`.

use std::collections::BTreeSet;
use std::net::TcpStream;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use coach_wire::live_channels::TABLE_VERSION;

use crate::config::server_authority;
use crate::hub::{Hub, LayoutState};
use crate::journal::GarageOut;
use crate::util::{iso_utc, jitter};
use crate::ws::{Incoming, ReadError, WebSocket};

/// While frames flow, status, garage and stats housekeeping runs this often.
const CHORES_EVERY: Duration = Duration::from_millis(50);
/// Longest the link sleeps for the next frame before looking at the rest of its work.
const FRAME_WAIT: Duration = Duration::from_millis(20);

pub const SUBPROTOCOL: &str = "coach-collector.1";
pub const PROTOCOL: i64 = 1;

#[derive(Debug, Clone)]
pub struct LinkSettings {
    pub server: String,
    pub collector_id: String,
    pub machine: String,
    pub version: String,
    pub os: String,
    /// Reconnect delays in ms (the last repeats), jittered by +/- `jitter_fraction`.
    pub backoff_ms: Vec<u64>,
    pub jitter_fraction: f64,
    /// A session that stayed connected this long resets the backoff.
    pub reset_after_ms: u64,
    pub connect_timeout_ms: u64,
    /// Welcome must arrive within this time of the hello.
    pub welcome_timeout_ms: u64,
    pub status_interval_ms: u64,
    pub stats_interval_ms: u64,
    /// No message from the server for this long: the connection is dead.
    pub silence_limit_ms: u64,
    pub incompatible_retry_ms: u64,
    pub duplicate_pause_ms: u64,
    /// Socket read timeout while idle, and while frames are flowing.
    pub idle_read_ms: u64,
    pub streaming_read_ms: u64,
    /// While frames flow the link sleeps until the next frame is queued and reads the socket only
    /// this often (the server's messages are few and need no faster answer).
    pub stream_poll_ms: u64,
}

impl LinkSettings {
    pub fn new(server: &str, collector_id: &str) -> LinkSettings {
        LinkSettings {
            server: server.to_string(),
            collector_id: collector_id.to_string(),
            machine: std::env::var("COMPUTERNAME").unwrap_or_else(|_| "unknown".into()),
            version: env!("CARGO_PKG_VERSION").to_string(),
            os: format!("{} {}", std::env::consts::OS, std::env::consts::ARCH),
            backoff_ms: vec![1_000, 2_000, 4_000, 8_000, 16_000, 30_000],
            jitter_fraction: 0.2,
            reset_after_ms: 60_000,
            connect_timeout_ms: 5_000,
            welcome_timeout_ms: 5_000,
            status_interval_ms: 5_000,
            stats_interval_ms: 10_000,
            silence_limit_ms: 45_000,
            incompatible_retry_ms: 10 * 60_000,
            duplicate_pause_ms: 60_000,
            idle_read_ms: 50,
            streaming_read_ms: 2,
            stream_poll_ms: 50,
        }
    }
}

/// Why a connection ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEnd {
    Shutdown,
    /// Close code 4001: keep capturing, retry slowly.
    Incompatible,
    /// Close code 4002: another connection with this collector id won.
    Duplicate,
    /// Any other close or error.
    Dropped(String),
}

pub struct Link {
    hub: Arc<Hub>,
    settings: LinkSettings,
    conn_id: u64,
    warned_types: BTreeSet<String>,
}

impl Link {
    pub fn new(hub: Arc<Hub>, settings: LinkSettings) -> Link {
        Link {
            hub,
            settings,
            conn_id: 0,
            warned_types: BTreeSet::new(),
        }
    }

    /// Thread body: connect, serve, back off, repeat until shutdown.
    pub fn run(&mut self) {
        let mut attempt = 0usize;
        while !self.hub.is_shutdown() {
            let started = Instant::now();
            let end = self.session();
            self.hub.update_link(|l| {
                l.connected = false;
                l.viewers = 0;
            });
            let connected_ms = started.elapsed().as_millis() as u64;
            let wait_ms = match &end {
                SessionEnd::Shutdown => return,
                SessionEnd::Incompatible => {
                    self.hub.update_link(|l| l.update_needed = true);
                    self.settings.incompatible_retry_ms
                }
                SessionEnd::Duplicate => self.settings.duplicate_pause_ms,
                SessionEnd::Dropped(why) => {
                    crate::debug!("link dropped: {why}");
                    self.hub.update_link(|l| l.last_error = Some(why.clone()));
                    if connected_ms >= self.settings.reset_after_ms {
                        attempt = 0;
                    }
                    let steps = &self.settings.backoff_ms;
                    let base = steps[attempt.min(steps.len() - 1)];
                    attempt += 1;
                    jitter(Duration::from_millis(base), self.settings.jitter_fraction).as_millis()
                        as u64
                }
            };
            self.sleep_unless_shutdown(wait_ms);
        }
    }

    fn sleep_unless_shutdown(&self, ms: u64) {
        let deadline = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < deadline && !self.hub.is_shutdown() {
            std::thread::sleep(Duration::from_millis(20).min(deadline - Instant::now()));
        }
    }

    /// One connection, from dial to close.
    pub fn session(&mut self) -> SessionEnd {
        match self.session_inner() {
            Ok(end) => end,
            Err(why) => SessionEnd::Dropped(why),
        }
    }

    fn session_inner(&mut self) -> Result<SessionEnd, String> {
        let authority = server_authority(&self.settings.server);
        let addr = std::net::ToSocketAddrs::to_socket_addrs(&authority)
            .map_err(|e| format!("resolve {authority}: {e}"))?
            .next()
            .ok_or_else(|| format!("no address for {authority}"))?;
        let stream = TcpStream::connect_timeout(
            &addr,
            Duration::from_millis(self.settings.connect_timeout_ms),
        )
        .map_err(|e| format!("connect {authority}: {e}"))?;
        stream.set_nodelay(true).ok();
        stream
            .set_read_timeout(Some(Duration::from_millis(
                self.settings.welcome_timeout_ms,
            )))
            .ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

        let mut ws = WebSocket::handshake(stream, &authority, "/ws/collector", SUBPROTOCOL)
            .map_err(|e| format!("handshake: {e}"))?;

        self.conn_id += 1;
        let conn = self.conn_id;
        send_json(&mut ws, &self.hello())?;
        let welcome = self.await_welcome(&mut ws)?;
        let mut state = ConnState::new(&self.settings);
        match welcome {
            Welcomed::Ok(w) => self.apply_welcome(&mut ws, &w)?,
            Welcomed::Closed(end) => return Ok(end),
        }
        self.hub.update_link(|l| {
            l.connected = true;
            l.last_error = None;
        });
        // Frames queued before this connection are stale.
        self.hub.frames.clear();
        self.send_status(&mut ws, &mut state)?;
        self.send_garage(&mut ws, conn)?;
        ws.flush().map_err(|e| e.to_string())?;

        let mut read_timeout_ms = 0u64;
        let mut last_poll = Instant::now();
        let mut last_chores = Instant::now();
        loop {
            if self.hub.is_shutdown() {
                ws.queue_close(1000);
                let _ = ws.flush();
                return Ok(SessionEnd::Shutdown);
            }
            let streaming = self.send_frames(&mut ws)?;
            // While frames flow, the housekeeping runs 20 times a second, not once per frame.
            if !streaming || last_chores.elapsed() >= CHORES_EVERY {
                last_chores = Instant::now();
                self.send_garage(&mut ws, conn)?;
                self.maybe_send_status(&mut ws, &mut state)?;
                self.maybe_send_stats(&mut ws, &mut state)?;
            }
            let poll_every = Duration::from_millis(self.settings.stream_poll_ms.max(1));
            if streaming && last_poll.elapsed() < poll_every {
                // Nothing to read yet: sleep until the next frame is queued (the queue wakes us)
                // or until the socket is due, instead of timing out on the socket every few ms.
                let until_due = poll_every.saturating_sub(last_poll.elapsed());
                self.hub.frames.wait(until_due.min(FRAME_WAIT));
                continue;
            }
            last_poll = Instant::now();
            let want_ms = if streaming {
                self.settings.streaming_read_ms
            } else {
                self.settings.idle_read_ms
            }
            .max(1);
            if want_ms != read_timeout_ms {
                ws.stream()
                    .set_read_timeout(Some(Duration::from_millis(want_ms)))
                    .ok();
                read_timeout_ms = want_ms;
            }
            match ws.read() {
                Ok(message) => {
                    state.last_heard = Instant::now();
                    if let Some(end) = self.handle_message(&mut ws, message)? {
                        return Ok(end);
                    }
                }
                Err(ReadError::Timeout) => {}
                Err(ReadError::Closed) => return Ok(SessionEnd::Dropped("closed".into())),
                Err(e) => return Err(e.to_string()),
            }
            if state.last_heard.elapsed() > Duration::from_millis(self.settings.silence_limit_ms) {
                return Err("server silent".into());
            }
        }
    }

    fn hello(&self) -> Value {
        json!({
            "type": "hello",
            "protocol": PROTOCOL,
            "collector_id": self.settings.collector_id,
            "machine": self.settings.machine.chars().take(64).collect::<String>(),
            "collector_version": self.settings.version,
            "live_table_version": TABLE_VERSION,
            "os": self.settings.os,
        })
    }

    fn await_welcome(&mut self, ws: &mut WebSocket) -> Result<Welcomed, String> {
        let deadline = Instant::now() + Duration::from_millis(self.settings.welcome_timeout_ms);
        ws.stream()
            .set_read_timeout(Some(Duration::from_millis(100)))
            .ok();
        loop {
            if self.hub.is_shutdown() {
                return Ok(Welcomed::Closed(SessionEnd::Shutdown));
            }
            match ws.read() {
                Ok(Incoming::Text(text)) => {
                    let value: Value =
                        serde_json::from_str(&text).map_err(|e| format!("welcome: {e}"))?;
                    if value.get("type").and_then(Value::as_str) == Some("welcome") {
                        return Ok(Welcomed::Ok(value));
                    }
                    // Anything before the welcome is out of order; ignore it.
                }
                Ok(Incoming::Close(code)) => return Ok(Welcomed::Closed(close_end(code))),
                Ok(Incoming::Binary(_)) => {}
                Err(ReadError::Timeout) => {}
                Err(e) => return Err(format!("waiting for welcome: {e}")),
            }
            if Instant::now() > deadline {
                return Err("no welcome".into());
            }
        }
    }

    fn apply_welcome(&mut self, ws: &mut WebSocket, w: &Value) -> Result<(), String> {
        // Required by `CollectorWelcome` in the contract; a welcome without them is a bad message.
        let (Some(stream_wanted), Some(viewers), Some(server_version)) = (
            w.get("stream_wanted").and_then(Value::as_bool),
            w.get("viewers").and_then(Value::as_i64),
            w.get("server_version").and_then(Value::as_str),
        ) else {
            return Err("welcome without stream_wanted, viewers or server_version".into());
        };
        self.hub.stream().on_welcome(stream_wanted);
        self.hub.update_link(|l| {
            l.viewers = viewers;
            l.server_version = Some(server_version.to_string());
            l.update_needed = false;
        });
        if let Some(layout) = w.get("layout").filter(|v| !v.is_null()) {
            self.apply_layout(ws, layout)?;
        }
        Ok(())
    }

    fn apply_layout(&mut self, ws: &mut WebSocket, value: &Value) -> Result<(), String> {
        let (Some(id), Some(version), Some(layout)) = (
            value.get("layout_id").and_then(Value::as_str),
            value.get("version").and_then(Value::as_i64),
            value.get("layout"),
        ) else {
            crate::warn!("layout.set without layout_id, version or layout");
            return Ok(());
        };
        self.hub.set_layout(LayoutState {
            layout_id: id.to_string(),
            version,
            layout: layout.clone(),
        });
        send_json(
            ws,
            &json!({"type": "layout.ack", "layout_id": id, "version": version}),
        )
    }

    fn handle_message(
        &mut self,
        ws: &mut WebSocket,
        message: Incoming,
    ) -> Result<Option<SessionEnd>, String> {
        let text = match message {
            Incoming::Text(t) => t,
            Incoming::Close(code) => return Ok(Some(close_end(code))),
            Incoming::Binary(_) => return Ok(None),
        };
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            crate::warn!("server sent text that is not JSON");
            return Ok(None);
        };
        let Some(kind) = value.get("type").and_then(Value::as_str) else {
            crate::warn!("server message without a type");
            return Ok(None);
        };
        match kind {
            "ping" => {
                let nonce = value.get("nonce").cloned().unwrap_or(Value::Null);
                send_json(ws, &json!({"type": "pong", "nonce": nonce}))?;
            }
            "stream.start" => self.hub.stream().on_start(),
            "stream.release" => self.hub.stream().on_release(),
            "viewers" => {
                let count = value.get("count").and_then(Value::as_i64).unwrap_or(0);
                self.hub.update_link(|l| l.viewers = count);
            }
            "layout.set" => self.apply_layout(ws, &value)?,
            "ingest.state" => {
                if let (Some(sha), Some(state)) = (
                    value.get("sha256").and_then(Value::as_str),
                    value.get("state").and_then(Value::as_str),
                ) {
                    self.hub.update_link(|l| {
                        l.ingest_states.insert(sha.to_string(), state.to_string());
                    });
                }
            }
            "garage.ack" => {
                let visit = value.get("visit_id").and_then(Value::as_str);
                let ack_kind = value.get("kind").and_then(Value::as_str);
                if let (Some(visit), Some(ack_kind)) = (visit, ack_kind) {
                    let mut journal = self.hub.journal();
                    let result = match ack_kind {
                        "snapshot" => value
                            .get("update_count")
                            .and_then(Value::as_i64)
                            .map_or(Ok(()), |n| journal.ack_snapshot(visit, n)),
                        "commit" => journal.ack_commit(visit),
                        _ => Ok(()),
                    };
                    if let Err(e) = result {
                        crate::error!("journal append failed: {e}");
                    }
                }
            }
            "error" => crate::warn!("server error: {value}"),
            "welcome" => {}
            other => {
                if self.warned_types.insert(other.to_string()) {
                    crate::warn!("ignoring server message of unknown type {other:?}");
                }
            }
        }
        Ok(None)
    }

    fn status_message(&self) -> Value {
        let s = self.hub.status();
        let mut m = json!({
            "type": "status",
            "sim": s.sim.as_str(),
            "connected": s.connected,
            "on_track": s.on_track,
            "in_garage": s.in_garage,
            "on_pit_road": s.on_pit_road,
            "streaming": s.streaming,
            "queue": {
                "uploads_pending": self.hub.uploads.uploads_pending.load(Ordering::Relaxed),
                "bytes_pending": self.hub.uploads.bytes_pending.load(Ordering::Relaxed),
            },
        });
        if let Some(session) = s.session {
            m["session"] = session;
        }
        if let Some(driver) = s.driver {
            m["driver"] = driver;
        }
        m
    }

    fn send_status(&self, ws: &mut WebSocket, state: &mut ConnState) -> Result<(), String> {
        let message = self.status_message();
        state.last_status_sent = Instant::now();
        state.last_status_gen = self.hub.status_gen();
        self.hub.note_status_sent(state.last_status_gen);
        state.last_queue = (
            self.hub.uploads.uploads_pending.load(Ordering::Relaxed),
            self.hub.uploads.bytes_pending.load(Ordering::Relaxed),
        );
        send_json(ws, &message)
    }

    fn maybe_send_status(&self, ws: &mut WebSocket, state: &mut ConnState) -> Result<(), String> {
        let queue = (
            self.hub.uploads.uploads_pending.load(Ordering::Relaxed),
            self.hub.uploads.bytes_pending.load(Ordering::Relaxed),
        );
        let due = state.last_status_sent.elapsed()
            >= Duration::from_millis(self.settings.status_interval_ms)
            || state.last_status_gen != self.hub.status_gen()
            || queue != state.last_queue;
        if due {
            self.send_status(ws, state)?;
        }
        Ok(())
    }

    fn maybe_send_stats(&self, ws: &mut WebSocket, state: &mut ConnState) -> Result<(), String> {
        let streaming = self.hub.is_streaming();
        if !streaming {
            state.stats_base = None;
            return Ok(());
        }
        let now_sent = self.hub.frames.sent();
        let now_dropped = self.hub.frames.dropped();
        let (base_at, base_unix, base_sent, base_dropped) = *state.stats_base.get_or_insert((
            Instant::now(),
            self.hub.clock.unix_ms(),
            now_sent,
            now_dropped,
        ));
        if base_at.elapsed() < Duration::from_millis(self.settings.stats_interval_ms) {
            return Ok(());
        }
        send_json(
            ws,
            &json!({
                "type": "stream.stats",
                "sent": now_sent - base_sent,
                "dropped": now_dropped - base_dropped,
                "since": iso_utc(base_unix),
            }),
        )?;
        state.stats_base = Some((
            Instant::now(),
            self.hub.clock.unix_ms(),
            now_sent,
            now_dropped,
        ));
        Ok(())
    }

    fn send_garage(&self, ws: &mut WebSocket, conn: u64) -> Result<(), String> {
        let out = self.hub.journal().take_unsent(conn);
        for item in out {
            send_json(ws, &garage_message(&item))?;
        }
        Ok(())
    }

    /// Sends queued frames; returns whether the collector is streaming.
    fn send_frames(&self, ws: &mut WebSocket) -> Result<bool, String> {
        let streaming = self.hub.is_streaming();
        let mut sent_any = false;
        while let Some(frame) = self.hub.frames.pop() {
            ws.queue_binary(&frame);
            self.hub.frames.note_sent();
            sent_any = true;
        }
        if sent_any {
            ws.flush().map_err(|e| e.to_string())?;
        }
        Ok(streaming || sent_any)
    }
}

enum Welcomed {
    Ok(Value),
    Closed(SessionEnd),
}

struct ConnState {
    last_heard: Instant,
    last_status_sent: Instant,
    last_status_gen: u64,
    last_queue: (i64, i64),
    stats_base: Option<(Instant, u64, u64, u64)>,
}

impl ConnState {
    fn new(_settings: &LinkSettings) -> ConnState {
        ConnState {
            last_heard: Instant::now(),
            last_status_sent: Instant::now(),
            last_status_gen: u64::MAX,
            last_queue: (-1, -1),
            stats_base: None,
        }
    }
}

fn close_end(code: Option<u16>) -> SessionEnd {
    match code {
        Some(4001) => SessionEnd::Incompatible,
        Some(4002) => SessionEnd::Duplicate,
        Some(code) => SessionEnd::Dropped(format!("closed with code {code}")),
        None => SessionEnd::Dropped("closed".into()),
    }
}

fn send_json(ws: &mut WebSocket, value: &Value) -> Result<(), String> {
    ws.send_text(&value.to_string()).map_err(|e| e.to_string())
}

/// The wire message for a journal entry.
pub fn garage_message(item: &GarageOut) -> Value {
    match item {
        GarageOut::Snapshot(s) => json!({
            "type": "garage.snapshot",
            "visit_id": s.visit_id,
            "update_count": s.update_count,
            "observed_at": s.observed_at,
            "car_path": s.car_path,
            "driver": s.driver,
            "session": s.session,
            "values": s.values,
        }),
        GarageOut::Commit { entry: c, values } => json!({
            "type": "garage.commit",
            "visit_id": c.visit_id,
            "update_count": c.update_count,
            "entered_at": c.entered_at,
            "exited_at": c.exited_at,
            "car_path": c.car_path,
            "driver": c.driver,
            "session": c.session,
            "setup_name": c.setup_name,
            "sto_sha256": c.sto_sha256,
            "ambiguous": c.ambiguous,
            "is_modified": c.is_modified,
            "load_type": c.load_type,
            "values": values,
        }),
    }
}
