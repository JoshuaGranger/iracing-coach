//! Collector WebSocket and live relay over real sockets: handshake and versions, garage replay
//! idempotence, stream control by viewer count, backlog, decimation and header validation.

mod support;

use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

use coach_server::api;
use serde_json::{Value, json};
use support::*;
use tungstenite::client::IntoClientRequest;
use tungstenite::protocol::WebSocket;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, connect};

const TABLE: u16 = coach_wire::live_channels::TABLE_VERSION;

async fn serve(env: &Env) -> SocketAddr {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let router = api::router(env.app.clone());
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

struct Client {
    ws: WebSocket<MaybeTlsStream<TcpStream>>,
}

impl Client {
    fn connect(addr: SocketAddr, path: &str, subprotocol: Option<&str>) -> Client {
        let mut request = format!("ws://{addr}{path}").into_client_request().unwrap();
        if let Some(p) = subprotocol {
            request
                .headers_mut()
                .insert("Sec-WebSocket-Protocol", p.parse().unwrap());
        }
        let (ws, _) = connect(request).expect("websocket handshake");
        if let MaybeTlsStream::Plain(stream) = ws.get_ref() {
            stream
                .set_read_timeout(Some(Duration::from_millis(200)))
                .unwrap();
        }
        Client { ws }
    }

    fn collector(addr: SocketAddr) -> Client {
        Client::connect(addr, "/ws/collector", Some("coach-collector.1"))
    }

    fn send(&mut self, value: Value) {
        self.ws
            .send(Message::Text(value.to_string().into()))
            .unwrap();
    }

    fn send_binary(&mut self, bytes: Vec<u8>) {
        self.ws.send(Message::Binary(bytes.into())).unwrap();
    }

    /// Next message within `limit`, or None.
    fn next(&mut self, limit: Duration) -> Option<Message> {
        let end = Instant::now() + limit;
        while Instant::now() < end {
            match self.ws.read() {
                Ok(m) => return Some(m),
                Err(tungstenite::Error::Io(e))
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                    ) =>
                {
                    continue;
                }
                Err(_) => return None,
            }
        }
        None
    }

    /// Reads JSON text messages until `pred` accepts one.
    fn until(&mut self, limit: Duration, pred: impl Fn(&Value) -> bool) -> Option<Value> {
        let end = Instant::now() + limit;
        while Instant::now() < end {
            if let Some(Message::Text(t)) = self.next(Duration::from_millis(400)) {
                let v: Value = serde_json::from_str(&t).unwrap();
                // every server message must satisfy its contract schema
                let schema = match v["type"].as_str().unwrap_or("") {
                    "welcome" => Some("CollectorWelcome"),
                    "ping" => Some("CollectorPing"),
                    "stream.start" => Some("StreamStart"),
                    "stream.release" => Some("StreamRelease"),
                    "viewers" => Some("ViewersMessage"),
                    "layout.set" => Some("LayoutSet"),
                    "garage.ack" => Some("GarageAck"),
                    "error" => Some("CollectorError"),
                    "live.hello" => Some("LiveHello"),
                    _ => None,
                };
                if let Some(schema) = schema {
                    let checked = coach_server::schema::Schemas::global()
                        .check(schema, &v)
                        .unwrap();
                    assert!(checked.is_ok(), "{schema} violated: {checked:?} in {v}");
                }
                if pred(&v) {
                    return Some(v);
                }
            }
        }
        None
    }

    fn hello(&mut self, id: &str) -> Value {
        self.send(json!({"type": "hello", "protocol": 1, "collector_id": id, "machine": "DESKTOP-SIM",
                         "collector_version": "0.19.0", "live_table_version": TABLE, "os": "windows"}));
        self.until(Duration::from_secs(5), |v| v["type"] == "welcome")
            .expect("welcome")
    }

    fn status(&mut self, streaming: bool) {
        self.send(json!({"type": "status", "sim": "in_session", "connected": true, "on_track": true, "in_garage": false,
                         "on_pit_road": false, "streaming": streaming,
                         "driver": {"customer_id": CUSTOMER, "name": "Joshua Granger"},
                         "session": {"session_epoch": 1, "subsession_id": 9900, "session_num": 0},
                         "queue": {"uploads_pending": 0, "bytes_pending": 0}}));
    }

    fn close_code(&mut self) -> Option<u16> {
        let end = Instant::now() + Duration::from_secs(5);
        while Instant::now() < end {
            match self.ws.read() {
                Ok(Message::Close(Some(frame))) => return Some(frame.code.into()),
                Ok(_) => continue,
                Err(tungstenite::Error::Io(_)) => continue,
                Err(_) => return None,
            }
        }
        None
    }
}

fn frame(table: u16, channels: u16, ms: u64, seq: u32) -> Vec<u8> {
    let mut f = Vec::new();
    f.extend_from_slice(b"LF");
    f.push(1);
    f.push(0b0000_0011);
    f.extend_from_slice(&seq.to_le_bytes());
    f.extend_from_slice(&1u32.to_le_bytes());
    f.extend_from_slice(&ms.to_le_bytes());
    f.extend_from_slice(&12.5f64.to_le_bytes());
    f.extend_from_slice(&900u32.to_le_bytes());
    f.extend_from_slice(&60u16.to_le_bytes());
    f.extend_from_slice(&table.to_le_bytes());
    f.extend_from_slice(&channels.to_le_bytes());
    f.extend(std::iter::repeat_n(0u8, (channels as usize).div_ceil(8)));
    f.extend_from_slice(&[0, 0, 0, 0]); // no cue
    f
}

fn count(app: &coach_server::AppState, sql: &str) -> i64 {
    let sql = sql.to_string();
    app.store
        .read_blocking(move |c| Ok(c.query_row(&sql, [], |r| r.get(0))?))
        .unwrap()
}

fn garage(visit: &str, count_: i64, values: Value) -> (Value, Value) {
    let snapshot = json!({"type": "garage.snapshot", "visit_id": visit, "update_count": count_, "observed_at": "2026-10-01T10:00:00Z",
        "car_path": "stockcars chevyss2020", "driver": {"customer_id": CUSTOMER, "name": "Joshua Granger"}, "values": values});
    let commit = json!({"type": "garage.commit", "visit_id": visit, "update_count": count_, "entered_at": "2026-10-01T09:59:00Z",
        "exited_at": "2026-10-01T10:00:30Z", "car_path": "stockcars chevyss2020", "driver": {"customer_id": CUSTOMER},
        "setup_name": "garage.sto", "sto_sha256": "c".repeat(64), "is_modified": false, "load_type": "user", "values": values});
    (snapshot, commit)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn garage_events_are_acknowledged_once_and_replays_insert_nothing() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    let app = env.app.clone();
    tokio::task::spawn_blocking(move || {
        let values = json!({"Chassis.Front.ArbSetting": "7", "Chassis.LeftFront.CornerWeight": "1700 N", "UpdateCount": "5"});
        let (snapshot, commit) = garage("11111111-aaaa-4bbb-8ccc-000000000001", 5, values.clone());

        // First delivery: the collector sends and the socket dies before it reads any acknowledgement.
        let mut c = Client::collector(addr);
        c.hello(COLLECTOR);
        c.status(false);
        c.send(snapshot.clone());
        c.send(commit.clone());
        drop(c);
        std::thread::sleep(Duration::from_millis(500)); // the server may have committed or not

        // Reconnect and resend until acknowledged: one row each, one ack per send.
        let mut c = Client::collector(addr);
        c.hello(COLLECTOR);
        c.status(false);
        for round in 0..2 {
            c.send(snapshot.clone());
            let ack = c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack" && v["kind"] == "snapshot").expect("snapshot ack");
            assert_eq!(ack["update_count"], 5, "round {round}");
            c.send(commit.clone());
            let ack = c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack" && v["kind"] == "commit").expect("commit ack");
            assert_eq!(ack["visit_id"], "11111111-aaaa-4bbb-8ccc-000000000001");
            assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_snapshot'"), 1, "round {round}");
            assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_commit'"), 1, "round {round}");
            assert_eq!(count(&app, "SELECT COUNT(*) FROM garage_visits"), 1);
            assert_eq!(count(&app, "SELECT COUNT(*) FROM setups"), 1, "same values, one setup");
        }
        assert_eq!(count(&app, "SELECT COUNT(*) FROM garage_visits WHERE committed_name = 'garage.sto' AND committed_sto_sha256 IS NOT NULL"), 1);

        // A later snapshot of the visit whose values equal no setup is stored and acknowledged but
        // makes no setup and no observation (only a commit creates a setup); snapshots stay nameless.
        let (second, _) = garage("11111111-aaaa-4bbb-8ccc-000000000001", 6, json!({"Chassis.Front.ArbSetting": "8", "UpdateCount": "6"}));
        c.send(second);
        c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack" && v["update_count"] == 6).expect("second ack");
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_snapshot' AND setup_name IS NULL"), 1);
        assert_eq!(count(&app, "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NOT NULL"), 2);
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setups"), 1);
    })
    .await
    .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_modified_commit_never_links_the_named_file() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    // a driver and the .sto itself exist first
    let bytes = fake_ibt(race_meta(9901, 0, "driver"), 2_000);
    env.upload(&bytes, "ibt", "d.ibt", None, 2000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    let sto = b"setup file bytes".to_vec();
    let sto_sha = coach_server::util::sha256_hex(&sto);
    env.upload(
        &sto,
        "sto",
        "garage.sto",
        Some("stockcars chevyss2020/garage.sto"),
        4096,
    )
    .await;
    let app = env.app.clone();
    let sha = sto_sha.clone();
    tokio::task::spawn_blocking(move || {
        let mut c = Client::collector(addr);
        c.hello(COLLECTOR);
        c.status(false);
        let (_, mut commit) = garage("22222222-aaaa-4bbb-8ccc-000000000002", 4, json!({"Chassis.Front.ArbSetting": "11"}));
        commit["sto_sha256"] = json!(sha);
        commit["is_modified"] = json!(true);
        c.send(commit.clone());
        c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack").expect("ack");
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_commit' AND is_modified = 1"), 1);
    })
    .await
    .unwrap();
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    assert!(
        library["unlinked_files"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f["sha256"] == sto_sha.as_str() && f["linked_setup_id"].is_null()),
        "a modified load leaves the file's values unknown"
    );
    // the same commit unmodified does link it
    let app = env.app.clone();
    let addr2 = addr;
    tokio::task::spawn_blocking(move || {
        let mut c = Client::collector(addr2);
        c.hello(COLLECTOR);
        c.status(false);
        let (_, mut commit) = garage("33333333-aaaa-4bbb-8ccc-000000000003", 4, json!({"Chassis.Front.ArbSetting": "12"}));
        commit["sto_sha256"] = json!(coach_server::util::sha256_hex(b"setup file bytes"));
        c.send(commit);
        c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack").expect("ack");
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_commit' AND is_modified = 0"), 1);
    })
    .await
    .unwrap();
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    assert!(
        library["unlinked_files"]
            .as_array()
            .unwrap()
            .iter()
            .all(|f| f["sha256"] != sto_sha.as_str())
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn handshake_versions_duplicates_and_bad_messages() {
    let env = Env::new().await;
    let mut events = env.app.events.subscribe();
    let addr = serve(&env).await;
    tokio::task::spawn_blocking(move || {
        // an incompatible major version is closed with 4001
        let mut c = Client::collector(addr);
        c.send(json!({"type": "hello", "protocol": 2, "collector_id": "aaaaaaaa-0000-4000-8000-000000000001", "machine": "M",
                      "collector_version": "9", "live_table_version": 1}));
        assert_eq!(c.close_code(), Some(4001));

        // a compatible collector gets a welcome with its stream demand
        let mut a = Client::collector(addr);
        let welcome = a.hello(COLLECTOR);
        assert_eq!(welcome["protocol"], 1);
        assert_eq!(welcome["stream_wanted"], false);
        assert_eq!(welcome["viewers"], 0);

        // a second connection with the same id wins; the older one is closed with 4002
        let mut b = Client::collector(addr);
        b.hello(COLLECTOR);
        assert_eq!(a.close_code(), Some(4002));

        // malformed and unknown messages
        b.send(json!({"type": "status", "sim": "bogus"}));
        let err = b.until(Duration::from_secs(5), |v| v["type"] == "error").expect("error reply");
        assert_eq!(err["code"], "bad_message");
        b.send(json!({"type": "mystery"}));
        b.send(json!({"type": "pong", "nonce": 1}));
        b.status(false);
        // still connected after the ignored message
        assert!(b.until(Duration::from_millis(800), |v| v["type"] == "error").is_none());
    })
    .await
    .unwrap();
    let mut presence = 0;
    while let Ok(event) = events.try_recv() {
        if event.kind == "presence" {
            let checked = coach_server::schema::Schemas::global()
                .check("PresenceEvent", &event.payload)
                .unwrap();
            assert!(
                checked.is_ok(),
                "presence event violates the contract: {checked:?} in {}",
                event.payload
            );
            presence += 1;
        }
    }
    assert!(
        presence >= 2,
        "presence events were published, got {presence}"
    );
    let status = env.get("/api/v1/status").await.json();
    let checked = coach_server::schema::Schemas::global()
        .check("ServerStatus", &status)
        .unwrap();
    assert!(checked.is_ok(), "status violates the contract: {checked:?}");
    let list = status["collectors"].as_array().unwrap();
    assert!(list.iter().any(
        |c| c["collector_id"] == "aaaaaaaa-0000-4000-8000-000000000001" && c["compatible"] == false
    ));
    assert!(
        list.iter()
            .any(|c| c["collector_id"] == COLLECTOR && c["compatible"] == true)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn viewers_start_and_release_the_stream_and_receive_decimated_frames_with_backlog() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    let app = env.app.clone();
    tokio::task::spawn_blocking(move || {
        let mut collector = Client::collector(addr);
        collector.hello(COLLECTOR);
        collector.status(false);
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "layout.set")
            .expect("the driver's layout is pushed");
        assert!(
            app.hub.is_live(CUSTOMER),
            "a connected collector reporting the driver in a session is live"
        );

        // viewer 1 at 10 Hz: demand 0 -> 1 starts the stream
        let mut viewer = Client::connect(addr, &format!("/ws/live/{CUSTOMER}?rate=10"), None);
        let hello = viewer
            .until(Duration::from_secs(5), |v| v["type"] == "live.hello")
            .expect("live.hello");
        assert_eq!(hello["live"], true);
        assert_eq!(hello["rate"], 10);
        assert_eq!(hello["backlog_frames"], 0);
        assert_eq!(hello["table_version"], TABLE);
        let viewers = collector
            .until(Duration::from_secs(5), |v| v["type"] == "viewers")
            .expect("viewers message");
        assert_eq!(viewers["count"], 1);
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.start")
            .expect("stream.start");

        // 60 Hz frames: the 10 Hz viewer receives about every sixth
        collector.status(true);
        let base = 1_000_000u64;
        for i in 0..40u32 {
            collector.send_binary(frame(TABLE, 45, base + i as u64 * 17, i));
        }
        let mut received = Vec::new();
        while let Some(m) = viewer.next(Duration::from_millis(600)) {
            if let Message::Binary(b) = m {
                received.push(b.to_vec());
            }
        }
        assert!(
            (3..=9).contains(&received.len()),
            "decimated to about 10 Hz, got {}",
            received.len()
        );
        assert!(
            received.iter().all(|f| f[3] & 0x40 == 0),
            "live frames are not flagged as backlog"
        );
        assert_eq!(&received[0][..2], b"LF");

        // a header that does not validate is dropped, not relayed
        let mut bad = frame(TABLE, 45, base + 5_000, 99);
        bad[0] = b'X';
        collector.send_binary(bad);
        // a frame from a newer table is not relayed either
        collector.send_binary(frame(TABLE + 1, 45, base + 5_100, 100));
        assert!(
            viewer.next(Duration::from_millis(500)).is_none(),
            "nothing relayed for invalid frames"
        );
        assert!(app.hub.collector_flags(COLLECTOR).unwrap().1 >= 1);

        // a late viewer replays the 10 Hz backlog with the backlog flag, then continues live
        assert!(app.hub.backlog_len(CUSTOMER) >= 3);
        let mut late = Client::connect(addr, &format!("/ws/live/{CUSTOMER}?rate=60"), None);
        let hello = late
            .until(Duration::from_secs(5), |v| v["type"] == "live.hello")
            .expect("hello");
        let n = hello["backlog_frames"].as_u64().unwrap() as usize;
        assert!(n >= 3, "backlog frames {n}");
        for _ in 0..n {
            match late.next(Duration::from_secs(2)) {
                Some(Message::Binary(b)) => {
                    assert_eq!(b[3] & 0x40, 0x40, "backlog frames carry bit 6")
                }
                other => panic!("expected a backlog frame, got {other:?}"),
            }
        }
        let viewers = collector.until(Duration::from_secs(5), |v| {
            v["type"] == "viewers" && v["count"] == 2
        });
        assert!(viewers.is_some());

        // changing rate over the socket
        viewer.send(json!({"type": "rate", "rate": 1}));

        // viewers drop to zero: the collector is told to release
        drop(viewer);
        drop(late);
        collector
            .until(Duration::from_secs(5), |v| {
                v["type"] == "viewers" && v["count"] == 0
            })
            .expect("viewers 0");
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.release")
            .expect("stream.release");

        // when the collector stops streaming the backlog is dropped
        collector.status(false);
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(app.hub.backlog_len(CUSTOMER), 0);

        // a reconnecting collector learns the demand from welcome
        let mut watcher = Client::connect(addr, &format!("/ws/live/{CUSTOMER}"), None);
        watcher
            .until(Duration::from_secs(5), |v| v["type"] == "live.hello")
            .unwrap();
        drop(collector);
        let mut again = Client::collector(addr);
        // the driver is not known until its first status, so demand is reported then
        again.hello(COLLECTOR);
        again.status(false);
        again
            .until(Duration::from_secs(5), |v| v["type"] == "stream.start")
            .expect("demand present at status");
        drop(watcher);
    })
    .await
    .unwrap();
    // the browser socket refuses an unsupported rate before upgrading
    let reply = env.get("/ws/live/1001?rate=25").await;
    assert_eq!(reply.status, axum::http::StatusCode::BAD_REQUEST);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_silent_collector_goes_offline() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| {
            c.collector_idle = Duration::from_secs(2);
            c.collector_ping = Duration::from_secs(1);
        }),
        ..Default::default()
    })
    .await;
    let addr = serve(&env).await;
    tokio::task::spawn_blocking(move || {
        let mut c = Client::collector(addr);
        c.hello(COLLECTOR);
        c.status(false);
        // the server pings; the client never answers; the socket is closed after the idle limit
        let ping = c.until(Duration::from_secs(5), |v| v["type"] == "ping");
        assert!(ping.is_some(), "the server pings");
        let end = Instant::now() + Duration::from_secs(10);
        let mut closed = false;
        while Instant::now() < end {
            match c.ws.read() {
                Ok(Message::Close(_))
                | Err(tungstenite::Error::ConnectionClosed)
                | Err(tungstenite::Error::AlreadyClosed) => {
                    closed = true;
                    break;
                }
                Ok(_) => {}
                Err(tungstenite::Error::Io(_)) => {}
                Err(_) => {
                    closed = true;
                    break;
                }
            }
        }
        assert!(closed, "an idle collector is closed");
    })
    .await
    .unwrap();
    let status = env.get("/api/v1/status").await.json();
    assert_eq!(status["collectors"][0]["online"], false);
}

// ------------------------------------------- review follow-ups (wave 1)

fn ack_for(c: &mut Client, kind: &str, update_count: i64) -> Option<Value> {
    c.until(Duration::from_millis(1500), |v| {
        v["type"] == "garage.ack" && v["kind"] == kind && v["update_count"] == update_count
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn garage_events_are_acknowledged_only_once_they_are_attributed_and_stored() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    let app = env.app.clone();
    tokio::task::spawn_blocking(move || {
        let values = json!({"Chassis.Front.ArbSetting": "7", "UpdateCount": "5"});

        // No driver in the event and none reported by this collector: stored nowhere, not
        // acknowledged, so the collector keeps it in its journal.
        let visit = "44444444-aaaa-4bbb-8ccc-000000000001";
        let (mut snapshot, mut commit) = garage(visit, 5, values.clone());
        snapshot["driver"] = Value::Null;
        commit["driver"] = Value::Null;
        let mut c = Client::collector(addr);
        c.hello("bbbbbbbb-0000-4000-8000-000000000001");
        c.send(snapshot.clone());
        assert!(ack_for(&mut c, "snapshot", 5).is_none(), "no driver, no acknowledgement");
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setup_observations"), 0);
        assert_eq!(count(&app, "SELECT COUNT(*) FROM garage_visits"), 1, "the visit itself is known");

        // Once the collector reports its driver, the resend is attributed to it and acknowledged.
        c.status(false);
        c.send(snapshot.clone());
        assert!(ack_for(&mut c, "snapshot", 5).is_some());
        assert_eq!(
            count(
                &app,
                &format!("SELECT COUNT(*) FROM garage_snapshots WHERE customer_id = {CUSTOMER} AND resolved_at IS NULL")
            ),
            1,
            "attributed to the collector's last reported driver; waits for the visit's commit"
        );
        assert_eq!(count(&app, "SELECT COUNT(*) FROM setups"), 0, "a snapshot makes no setup");

        // A commit that names no car uses the car its visit already carries.
        commit["car_path"] = Value::Null;
        c.send(commit.clone());
        assert!(ack_for(&mut c, "commit", 5).is_some());
        assert_eq!(
            count(&app, "SELECT COUNT(*) FROM setup_observations WHERE source = 'garage_commit' AND setup_id IN (SELECT setup_id FROM setups WHERE car_path = 'stockcars chevyss2020')"),
            1
        );
        // the commit resolved the waiting snapshot: an observation of the setup it created
        assert_eq!(
            count(&app, "SELECT COUNT(DISTINCT setup_id) FROM setup_observations WHERE visit_id = '44444444-aaaa-4bbb-8ccc-000000000001'"),
            1
        );
        assert_eq!(count(&app, "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NULL"), 0);

        // No driver and no car anywhere: a commit for a visit nothing is known about waits too.
        let (_, mut orphan) = garage("44444444-aaaa-4bbb-8ccc-000000000002", 9, values.clone());
        orphan["car_path"] = Value::Null;
        c.send(orphan.clone());
        assert!(ack_for(&mut c, "commit", 9).is_none(), "no car, no acknowledgement");
        assert_eq!(
            count(&app, "SELECT COUNT(*) FROM setup_observations WHERE visit_id = '44444444-aaaa-4bbb-8ccc-000000000002'"),
            0
        );
        // its car arrives with a later event of the visit; the resend then goes through
        let (late_snapshot, _) = garage("44444444-aaaa-4bbb-8ccc-000000000002", 9, values.clone());
        c.send(late_snapshot);
        assert!(ack_for(&mut c, "snapshot", 9).is_some());
        c.send(orphan);
        assert!(ack_for(&mut c, "commit", 9).is_some());

        // A commit whose snapshot is not stored yet (values referenced, not repeated) waits.
        let visit = "44444444-aaaa-4bbb-8ccc-000000000003";
        let (snapshot, mut commit) = garage(visit, 7, values.clone());
        commit["values"] = json!({});
        c.send(commit.clone());
        assert!(ack_for(&mut c, "commit", 7).is_none(), "its values are not stored");
        assert_eq!(
            count(&app, &format!("SELECT COUNT(*) FROM setup_observations WHERE visit_id = '{visit}'")),
            0
        );
        c.send(snapshot);
        assert!(ack_for(&mut c, "snapshot", 7).is_some());
        c.send(commit);
        assert!(ack_for(&mut c, "commit", 7).is_some());
        assert_eq!(
            count(&app, &format!("SELECT COUNT(*) FROM setup_observations WHERE visit_id = '{visit}' AND source = 'garage_commit'")),
            1
        );
    })
    .await
    .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn an_ambiguous_commit_never_links_a_file_by_name() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    let bytes = fake_ibt(race_meta(9910, 0, "driver"), 2_000);
    env.upload(&bytes, "ibt", "d.ibt", None, 2000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    // two same-named files in one car folder: the collector cannot tell which one was loaded
    for (rel, content) in [
        ("stockcars chevyss2020/garage.sto", b"first copy".to_vec()),
        (
            "stockcars chevyss2020/old/garage.sto",
            b"second copy".to_vec(),
        ),
    ] {
        env.upload(&content, "sto", "garage.sto", Some(rel), 4096)
            .await;
    }
    let sto_shas: Vec<String> = [b"first copy".to_vec(), b"second copy".to_vec()]
        .iter()
        .map(|b| coach_server::util::sha256_hex(b))
        .collect();
    let send = |visit: &'static str, values: Value, ambiguous: bool| {
        let (_, mut commit) = garage(visit, 4, values);
        commit["sto_sha256"] = Value::Null;
        commit["ambiguous"] = json!(ambiguous);
        commit["setup_name"] = json!("garage.sto");
        commit["is_modified"] = json!(false);
        tokio::task::spawn_blocking(move || {
            let mut c = Client::collector(addr);
            c.hello(COLLECTOR);
            c.status(false);
            c.send(commit);
            c.until(Duration::from_secs(5), |v| v["type"] == "garage.ack")
                .expect("ack");
        })
    };
    send(
        "55555555-aaaa-4bbb-8ccc-000000000001",
        json!({"Chassis.Front.ArbSetting": "13"}),
        true,
    )
    .await
    .unwrap();
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    let unlinked: Vec<&str> = library["unlinked_files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["sha256"].as_str().unwrap())
        .collect();
    for sha in &sto_shas {
        assert!(
            unlinked.contains(&sha.as_str()),
            "an ambiguous visit proves nothing about either file"
        );
    }
    // the same commit from a visit that could name its file links it by name
    send(
        "55555555-aaaa-4bbb-8ccc-000000000002",
        json!({"Chassis.Front.ArbSetting": "14"}),
        false,
    )
    .await
    .unwrap();
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    let unlinked: Vec<&str> = library["unlinked_files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["sha256"].as_str().unwrap())
        .collect();
    assert!(
        sto_shas.iter().any(|sha| !unlinked.contains(&sha.as_str())),
        "an unambiguous name does link"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_driver_change_recounts_demand_for_the_new_driver() {
    let env = Env::new().await;
    let addr = serve(&env).await;
    tokio::task::spawn_blocking(move || {
        let mut collector = Client::collector(addr);
        collector.hello(COLLECTOR);
        collector.status(false);
        // a viewer of this driver starts the stream
        let viewer = Client::connect(addr, &format!("/ws/live/{CUSTOMER}?rate=10"), None);
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.start")
            .expect("stream.start for the first driver");

        // the collector now reports another driver nobody watches: the previous driver's demand
        // must not carry over
        collector.send(json!({"type": "status", "sim": "in_session", "connected": true, "on_track": true, "in_garage": false,
                              "on_pit_road": false, "streaming": true,
                              "driver": {"customer_id": 2002, "name": "Guest Driver"},
                              "session": {"session_epoch": 1, "subsession_id": 9901, "session_num": 0},
                              "queue": {"uploads_pending": 0, "bytes_pending": 0}}));
        let zero = collector
            .until(Duration::from_secs(5), |v| v["type"] == "viewers" && v["count"] == 0)
            .expect("viewers 0 for the new driver");
        assert_eq!(zero["count"], 0);
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.release")
            .expect("stream.release for the new driver");

        // and back to the watched driver: demand is recounted and the stream restarts
        collector.status(true);
        let back = collector
            .until(Duration::from_secs(5), |v| v["type"] == "viewers" && v["count"] == 1)
            .expect("viewers 1 for the first driver again");
        assert_eq!(back["count"], 1);
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.start")
            .expect("stream.start again");
        drop(viewer);
    })
    .await
    .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_browser_that_cannot_keep_up_is_closed_and_stops_counting_as_a_viewer() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.slow_browser = Duration::from_millis(800)),
        ..Default::default()
    })
    .await;
    let addr = serve(&env).await;
    let app = env.app.clone();
    tokio::task::spawn_blocking(move || {
        let mut collector = Client::collector(addr);
        collector.hello(COLLECTOR);
        collector.status(true);
        // A viewer that never reads: its socket buffers fill with large frames, the send stalls.
        let viewer = Client::connect(addr, &format!("/ws/live/{CUSTOMER}?rate=60"), None);
        collector
            .until(Duration::from_secs(5), |v| {
                v["type"] == "viewers" && v["count"] == 1
            })
            .expect("one viewer");
        assert_eq!(app.hub.viewer_count(CUSTOMER), 1);
        let big = |seq: u32, ms: u64| {
            let mut f = frame(TABLE, 45, ms, seq);
            f.extend(std::iter::repeat_n(0u8, 512 * 1024));
            f
        };
        let end = Instant::now() + Duration::from_secs(20);
        let mut seq = 0u32;
        while app.hub.viewer_count(CUSTOMER) > 0 && Instant::now() < end {
            collector.send_binary(big(seq, 5_000_000 + seq as u64 * 17));
            seq += 1;
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            app.hub.viewer_count(CUSTOMER),
            0,
            "the stalled viewer was dropped within the limit (sent {seq} frames)"
        );
        collector
            .until(Duration::from_secs(5), |v| v["type"] == "stream.release")
            .expect("the collector is told demand ended");
        drop(viewer);
    })
    .await
    .unwrap();
}

fn presence_events(
    events: &mut tokio::sync::broadcast::Receiver<coach_server::events::Event>,
) -> Vec<Value> {
    let mut list = Vec::new();
    while let Ok(event) = events.try_recv() {
        if event.kind == "presence" {
            list.push(event.payload["collector"].clone());
        }
    }
    list
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn an_unchanged_status_is_not_forwarded_as_presence() {
    let env = Env::new().await;
    let mut events = env.app.events.subscribe();
    let addr = serve(&env).await;
    let seen = tokio::task::spawn_blocking(move || {
        let mut c = Client::collector(addr);
        c.hello(COLLECTOR);
        c.status(false);
        // the same status again, in a later second: only last_seen_at differs
        std::thread::sleep(Duration::from_millis(1_200));
        c.status(false);
        c.status(false);
        std::thread::sleep(Duration::from_millis(300));
        // a change in the streaming flag is forwarded
        c.status(true);
        std::thread::sleep(Duration::from_millis(300));
        // a change in the queue is forwarded
        c.send(json!({"type": "status", "sim": "in_session", "connected": true, "on_track": true, "in_garage": false,
                      "on_pit_road": false, "streaming": true,
                      "driver": {"customer_id": CUSTOMER, "name": "Joshua Granger"},
                      "session": {"session_epoch": 1, "subsession_id": 9900, "session_num": 0},
                      "queue": {"uploads_pending": 2, "bytes_pending": 4096}}));
        std::thread::sleep(Duration::from_millis(300));
        // the sim leaving the session is forwarded
        c.send(json!({"type": "status", "sim": "not_running", "connected": false, "on_track": false, "in_garage": false,
                      "on_pit_road": false, "streaming": false,
                      "driver": {"customer_id": CUSTOMER, "name": "Joshua Granger"}, "session": null,
                      "queue": {"uploads_pending": 2, "bytes_pending": 4096}}));
        std::thread::sleep(Duration::from_millis(300));
        // closing the socket marks the collector offline, which is forwarded
        drop(c);
    })
    .await;
    seen.unwrap();
    // let the server notice the close
    tokio::time::sleep(Duration::from_millis(500)).await;
    let list = presence_events(&mut events);
    let summary: Vec<(bool, Value, Value, Value)> = list
        .iter()
        .map(|p| {
            (
                p["online"].as_bool().unwrap(),
                p["status"]["sim"].clone(),
                p["status"]["streaming"].clone(),
                p["status"]["queue"]["uploads_pending"].clone(),
            )
        })
        .collect();
    assert_eq!(
        summary,
        vec![
            (true, Value::Null, Value::Null, Value::Null), // hello
            (true, json!("in_session"), json!(false), json!(0)),
            (true, json!("in_session"), json!(true), json!(0)),
            (true, json!("in_session"), json!(true), json!(2)),
            (true, json!("not_running"), json!(false), json!(2)),
            (false, json!("not_running"), json!(false), json!(2)),
        ],
        "presence follows changes only: {list:?}"
    );
}
