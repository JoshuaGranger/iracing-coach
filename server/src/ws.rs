//! WebSocket endpoints: `/ws/collector` (sim-PC collector) and `/ws/live/{customer_id}` (browsers).

use std::collections::{HashMap, HashSet};
use std::sync::atomic::Ordering;
use std::time::Duration;

use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};
use tokio::sync::mpsc;

use crate::app::AppState;
use crate::error::Error;
use crate::hub::{CollectorConn, FrameVerdict, Outgoing};
use crate::schema::Schemas;
use crate::setups::GarageOutcome;
use crate::util::{self, iso_now};

pub const COLLECTOR_SUBPROTOCOL: &str = "coach-collector.1";

pub async fn collector_ws(State(app): State<AppState>, ws: WebSocketUpgrade) -> Response {
    ws.protocols([COLLECTOR_SUBPROTOCOL])
        .on_upgrade(move |socket| collector_session(app, socket))
}

fn text(value: &Value) -> Message {
    Message::Text(value.to_string().into())
}

fn close_message(code: u16, reason: &'static str) -> Message {
    Message::Close(Some(CloseFrame {
        code,
        reason: reason.into(),
    }))
}

async fn collector_session(app: AppState, mut socket: WebSocket) {
    // 1. hello within the timeout
    let first = tokio::time::timeout(app.cfg.collector_hello_timeout, async {
        loop {
            match socket.recv().await {
                Some(Ok(Message::Text(t))) => return Some(t.to_string()),
                Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => continue,
                _ => return None,
            }
        }
    })
    .await;
    let Ok(Some(first)) = first else {
        let _ = socket.send(close_message(1008, "hello_required")).await;
        return;
    };
    let hello: Value = serde_json::from_str(&first).unwrap_or(Value::Null);
    if hello["type"] != "hello" {
        let _ = socket
            .send(text(
                &json!({"type": "error", "code": "bad_message", "message": "hello first"}),
            ))
            .await;
        let _ = socket.send(close_message(1008, "hello_required")).await;
        return;
    }
    let collector_id = hello["collector_id"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let protocol = hello["protocol"].as_i64().unwrap_or(0);
    let machine = hello["machine"].as_str().unwrap_or("unknown").to_string();
    let version = hello["collector_version"]
        .as_str()
        .unwrap_or("unknown")
        .to_string();
    if collector_id.is_empty() {
        let _ = socket
            .send(text(
                &json!({"type": "error", "code": "bad_message", "message": "collector_id"}),
            ))
            .await;
        return;
    }
    let (tx, mut rx) = mpsc::unbounded_channel::<Outgoing>();
    let conn_id = app.hub.next_id();
    let compatible = protocol == 1;
    {
        let (id, machine, version) = (collector_id.clone(), machine.clone(), version.clone());
        let now = iso_now();
        let _ = app
            .store
            .write(move |conn| {
                conn.execute(
                    "INSERT INTO collectors(collector_id, machine_name, collector_version, protocol, first_seen_at, last_seen_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?5)
                     ON CONFLICT(collector_id) DO UPDATE SET machine_name = ?2, collector_version = ?3,
                       protocol = ?4, last_seen_at = ?5",
                    rusqlite::params![id, machine, version, protocol, now],
                )?;
                Ok(())
            })
            .await;
    }
    let replaced = app.hub.register(
        &collector_id,
        CollectorConn {
            conn_id,
            tx: tx.clone(),
            machine,
            version,
            protocol,
            compatible,
            online: compatible,
            last_seen: util::unix_now(),
            status: None,
            driver: None,
            streaming: false,
            newer_than_server: false,
            bad_frame_logged: false,
            frames_relayed: 0,
            frames_invalid: 0,
        },
    );
    let _ = replaced;
    if !compatible {
        // Presence stays visible (offline, incompatible) so the tray and status show "update needed".
        publish_presence(&app, &collector_id);
        let _ = socket
            .send(close_message(4001, "incompatible_protocol"))
            .await;
        return;
    }
    if Schemas::global()
        .check("CollectorHello", &hello)
        .ok()
        .and_then(|r| r.err())
        .is_some()
    {
        tracing::warn!(collector = %collector_id, "hello does not match CollectorHello; accepted");
    }
    let welcome = json!({
        "type": "welcome",
        "protocol": 1,
        "server_version": app.cfg.app_version,
        "stream_wanted": false,
        "viewers": 0,
        "layout": null,
    });
    if socket.send(text(&welcome)).await.is_err() {
        app.hub.disconnect(&collector_id, conn_id);
        return;
    }
    publish_presence(&app, &collector_id);

    let mut ping = tokio::time::interval(app.cfg.collector_ping);
    ping.tick().await;
    let mut idle = tokio::time::interval(Duration::from_secs(1));
    let mut nonce = 0i64;
    let mut unknown_seen: HashSet<String> = HashSet::new();
    loop {
        tokio::select! {
            incoming = socket.recv() => {
                let Some(Ok(message)) = incoming else { break };
                match message {
                    Message::Text(t) => {
                        app.hub.touch(&collector_id, conn_id);
                        let replies = handle_text(&app, &collector_id, conn_id, &t, &mut unknown_seen).await;
                        let mut failed = false;
                        for reply in replies {
                            if socket.send(text(&reply)).await.is_err() {
                                failed = true;
                                break;
                            }
                        }
                        if failed { break; }
                    }
                    Message::Binary(frame) => {
                        app.hub.touch(&collector_id, conn_id);
                        let verdict = app.hub.relay(&collector_id, conn_id, frame);
                        if verdict == FrameVerdict::NewerTable {
                            publish_presence(&app, &collector_id);
                        }
                    }
                    Message::Ping(_) | Message::Pong(_) => app.hub.touch(&collector_id, conn_id),
                    Message::Close(_) => break,
                }
            }
            outgoing = rx.recv() => {
                match outgoing {
                    Some(Outgoing::Json(v)) => {
                        if socket.send(text(&v)).await.is_err() { break; }
                    }
                    Some(Outgoing::Close(code, reason)) => {
                        let _ = socket.send(close_message(code, reason)).await;
                        // A replaced connection must not mark the new one offline.
                        return;
                    }
                    None => break,
                }
            }
            _ = ping.tick() => {
                nonce += 1;
                if socket.send(text(&json!({"type": "ping", "nonce": nonce}))).await.is_err() { break; }
            }
            _ = idle.tick() => {
                let idle_secs = app.cfg.collector_idle.as_secs() as i64;
                if app.hub.stale(idle_secs).iter().any(|(id, c)| id == &collector_id && *c == conn_id) {
                    let _ = socket.send(close_message(1001, "idle")).await;
                    break;
                }
            }
        }
    }
    if let Some(presence) = app.hub.disconnect(&collector_id, conn_id) {
        app.events
            .publish("presence", None, json!({"collector": presence}));
    }
}

fn publish_presence(app: &AppState, collector_id: &str) {
    if let Some(presence) = app
        .hub
        .presence_list()
        .into_iter()
        .find(|p| p["collector_id"] == collector_id)
    {
        let customer = presence["status"]["driver"]["customer_id"].as_i64();
        app.events
            .publish("presence", customer, json!({"collector": presence}));
    }
}

fn error_reply(code: &str, message: &str) -> Value {
    json!({"type": "error", "code": code, "message": message})
}

async fn handle_text(
    app: &AppState,
    collector_id: &str,
    conn_id: u64,
    raw: &str,
    unknown_seen: &mut HashSet<String>,
) -> Vec<Value> {
    let Ok(message) = serde_json::from_str::<Value>(raw) else {
        return vec![error_reply("bad_message", "not valid JSON")];
    };
    let kind = message["type"].as_str().unwrap_or_default().to_string();
    let schema = match kind.as_str() {
        "status" => Some("CollectorStatus"),
        "garage.snapshot" => Some("GarageSnapshot"),
        "garage.commit" => Some("GarageCommit"),
        "stream.stats" => Some("StreamStats"),
        "layout.ack" => Some("LayoutAck"),
        "pong" => Some("CollectorPong"),
        _ => None,
    };
    if let Some(schema) = schema
        && let Ok(Err(errors)) = Schemas::global().check(schema, &message)
    {
        return vec![error_reply("bad_message", &errors.join("; "))];
    }
    match kind.as_str() {
        "pong" | "hello" | "layout.ack" | "stream.stats" => Vec::new(),
        "status" => handle_status(app, collector_id, conn_id, message).await,
        "garage.snapshot" | "garage.commit" => {
            let is_commit = kind == "garage.commit";
            let (id, msg) = (collector_id.to_string(), message.clone());
            let parser = app.parser.clone();
            let result = app
                .store
                .write(move |conn| {
                    let tx = conn.transaction()?;
                    let fp = |values: &std::collections::BTreeMap<String, String>| {
                        parser.fingerprint(values)
                    };
                    let outcome = if is_commit {
                        crate::setups::record_garage_commit(&tx, &msg, &id, &fp)?
                    } else {
                        crate::setups::record_garage_snapshot(&tx, &msg, &id, &fp)?
                    };
                    tx.commit()?;
                    Ok(outcome)
                })
                .await;
            match result {
                Ok(GarageOutcome::Stored(customer)) => {
                    app.events.library_changed(customer, "setups");
                    vec![json!({
                        "type": "garage.ack",
                        "visit_id": message["visit_id"],
                        "kind": if is_commit { "commit" } else { "snapshot" },
                        "update_count": message["update_count"],
                    })]
                }
                Ok(GarageOutcome::Unattributed(missing)) => {
                    // Not acknowledged: the collector keeps it and resends after a later welcome.
                    tracing::warn!(visit_id = %message["visit_id"], missing, "garage event cannot be attributed yet; not acknowledged");
                    Vec::new()
                }
                Err(e) => {
                    tracing::error!(error = %e, "garage event failed; not acknowledged");
                    vec![error_reply("internal", "garage event could not be stored")]
                }
            }
        }
        other => {
            if unknown_seen.insert(other.to_string()) {
                tracing::warn!(kind = other, "unknown collector message type ignored");
            }
            Vec::new()
        }
    }
}

async fn handle_status(
    app: &AppState,
    collector_id: &str,
    conn_id: u64,
    message: Value,
) -> Vec<Value> {
    let mut status = message.clone();
    if let Some(map) = status.as_object_mut() {
        map.remove("type");
    }
    let Some(update) = app.hub.update_status(collector_id, conn_id, status) else {
        return Vec::new();
    };
    if let Some(driver) = update.driver {
        let name = message["driver"]["name"].as_str().map(str::to_string);
        let id = collector_id.to_string();
        let _ = app
            .store
            .write(move |conn| {
                crate::db::upsert_driver(conn, driver, name.as_deref(), None)?;
                conn.execute(
                    "UPDATE collectors SET last_customer_id = ?2, last_seen_at = ?3 WHERE collector_id = ?1",
                    rusqlite::params![id, driver, iso_now()],
                )?;
                Ok(())
            })
            .await;
    }
    app.events.publish(
        "presence",
        update.driver,
        json!({"collector": update.presence}),
    );
    let mut replies = Vec::new();
    if update.driver_changed {
        if let Some(driver) = update.driver
            && let Ok(layout) = crate::userdata::active_layout_message(app, driver).await
        {
            replies.push(layout);
        }
        // Demand belongs to a driver: recount for the new one and tell the collector, so a
        // viewer of the previous driver never keeps the new driver's stream running.
        if update.viewers > 0 {
            replies.push(json!({"type": "viewers", "count": update.viewers}));
            replies.push(json!({"type": "stream.start", "reason": "viewer"}));
        } else if update.previous_driver.is_some() {
            replies.push(json!({"type": "viewers", "count": 0}));
            replies.push(json!({"type": "stream.release"}));
        }
    }
    replies
}

// ------------------------------------------------------------------ browsers

pub async fn live_ws(
    State(app): State<AppState>,
    Path(customer_id): Path<i64>,
    Query(query): Query<HashMap<String, String>>,
    ws: WebSocketUpgrade,
) -> Response {
    let rate: u32 = match query.get("rate") {
        None => 30,
        Some(r) => match r.parse::<u32>() {
            Ok(v) if [1, 10, 30, 60].contains(&v) => v,
            _ => return Error::invalid("rate must be 1, 10, 30 or 60").into_response(),
        },
    };
    ws.on_upgrade(move |socket| viewer_session(app, socket, customer_id, rate))
}

async fn viewer_session(app: AppState, mut socket: WebSocket, customer: i64, rate: u32) {
    let limit = app.cfg.slow_browser;
    let (viewer, backlog, count) = app.hub.add_viewer(customer, rate);
    let _ = count;
    publish_driver_presence(&app, customer);
    let hello = json!({
        "type": "live.hello",
        "table_version": coach_wire::live_channels::TABLE_VERSION,
        "live": app.hub.is_live(customer),
        "backlog_frames": backlog.len(),
        "rate": rate,
    });
    let mut ok = socket.send(text(&hello)).await.is_ok();
    if ok {
        // A stalled socket holds neither the backlog nor the viewer count past the limit.
        ok = send_all(&mut socket, backlog.into_iter(), limit).await;
        if !ok {
            close_slow(&mut socket).await;
        }
    }
    while ok {
        tokio::select! {
            _ = viewer.notify.notified() => {
                let frames = viewer.drain();
                let sent = send_all(&mut socket, frames.into_iter(), limit).await;
                if !sent || viewer.behind_for().is_some_and(|d| d > limit) {
                    close_slow(&mut socket).await;
                    ok = false;
                }
            }
            incoming = socket.recv() => {
                match incoming {
                    Some(Ok(Message::Text(t))) => {
                        if let Ok(v) = serde_json::from_str::<Value>(&t)
                            && v["type"] == "rate"
                            && let Some(r) = v["rate"].as_u64().filter(|r| [1, 10, 30, 60].contains(r))
                        {
                            viewer.rate.store(r as u32, Ordering::Relaxed);
                        }
                    }
                    Some(Ok(Message::Close(_))) | None | Some(Err(_)) => ok = false,
                    _ => {}
                }
            }
        }
    }
    viewer.closed.store(true, Ordering::Relaxed);
    app.hub.remove_viewer(customer, viewer.id);
    publish_driver_presence(&app, customer);
}

/// Sends binary frames; false when the whole batch did not go out within `limit` or a send failed.
async fn send_all(
    socket: &mut WebSocket,
    frames: impl Iterator<Item = axum::body::Bytes>,
    limit: Duration,
) -> bool {
    let batch = async {
        for frame in frames {
            if socket.send(Message::Binary(frame)).await.is_err() {
                return false;
            }
        }
        true
    };
    matches!(tokio::time::timeout(limit, batch).await, Ok(true))
}

/// Closes a viewer that cannot keep up with 4008; a socket that will not even take the close
/// frame is dropped after two seconds.
async fn close_slow(socket: &mut WebSocket) {
    let _ = tokio::time::timeout(
        Duration::from_secs(2),
        socket.send(close_message(4008, "too_slow")),
    )
    .await;
}

/// Presence of the driver's collectors (their viewer count changed).
fn publish_driver_presence(app: &AppState, customer: i64) {
    for id in app.hub.collectors_for(customer) {
        publish_presence(app, &id);
    }
}
