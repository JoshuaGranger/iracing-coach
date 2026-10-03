//! The server link against an in-test WebSocket mock (`collector-protocol.md` sections 3 to 9, 11).

mod support;

use std::collections::BTreeMap;
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use coach_collector::hub::{Hub, SimKind, StatusModel};
use coach_collector::journal::{CommitEntry, Journal, SnapshotEntry};
use coach_collector::link::{Link, LinkSettings};
use coach_collector::util::SystemClock;
use coach_wire::live_channels::TABLE_VERSION;
use serde_json::json;
use support::*;

const ID: &str = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

struct Rig {
    hub: Arc<Hub>,
    thread: Option<JoinHandle<()>>,
}

impl Rig {
    fn start(mock: &Mock, tweak: impl FnOnce(&mut LinkSettings)) -> Rig {
        let hub = Hub::new(Arc::new(SystemClock::new()), Journal::in_memory(), true);
        Rig::start_with(mock, hub, tweak)
    }

    fn start_with(mock: &Mock, hub: Arc<Hub>, tweak: impl FnOnce(&mut LinkSettings)) -> Rig {
        let mut settings = LinkSettings::new(&mock.url(), ID);
        settings.backoff_ms = vec![60, 120];
        settings.jitter_fraction = 0.0;
        settings.status_interval_ms = 150;
        settings.stats_interval_ms = 150;
        settings.idle_read_ms = 10;
        settings.welcome_timeout_ms = 2_000;
        tweak(&mut settings);
        let mut link = Link::new(hub.clone(), settings);
        let thread = Some(thread::spawn(move || link.run()));
        Rig { hub, thread }
    }

    fn stop(mut self) {
        self.hub.request_shutdown();
        if let Some(t) = self.thread.take() {
            t.join().unwrap();
        }
    }
}

fn snapshot(visit: &str, n: i64, value: &str) -> SnapshotEntry {
    SnapshotEntry {
        visit_id: visit.into(),
        update_count: n,
        observed_at: "2026-10-02T23:15:47.000Z".into(),
        car_path: Some("stockcars2 supra2019".into()),
        driver: Some(json!({"customer_id": 123456, "name": "Joshua Granger"})),
        session: None,
        values: BTreeMap::from([(
            "Tires.LeftFront.ColdPressure".to_string(),
            value.to_string(),
        )]),
    }
}

fn commit(visit: &str, n: i64) -> CommitEntry {
    CommitEntry {
        visit_id: visit.into(),
        update_count: Some(n),
        entered_at: "2026-10-02T23:15:17.000Z".into(),
        exited_at: "2026-10-02T23:17:43.000Z".into(),
        car_path: Some("stockcars2 supra2019".into()),
        driver: None,
        session: None,
        setup_name: Some("claude LV R CAM.sto".into()),
        sto_sha256: Some("ab".repeat(32)),
        ambiguous: false,
        is_modified: Some(true),
        load_type: Some("user".into()),
        hash_pending: false,
    }
}

#[test]
fn hello_welcome_status_and_layout_sync() {
    let mock = Mock::start();
    mock.with(|s| {
        s.welcome_stream_wanted = true;
        s.welcome_viewers = 2;
        s.welcome_layout = Some(json!({
            "type": "layout.set", "layout_id": "factory-race", "version": 7,
            "layout": {"rows": 1, "columns": 1, "tiles": [
                {"tile_id": "t", "metric": "speed", "row": 0, "column": 0, "row_span": 1, "column_span": 1, "style": "number"}
            ]}
        }));
    });
    let rig = Rig::start(&mock, |_| {});
    assert!(wait_until(Duration::from_secs(3), || {
        !mock.texts("status").is_empty() && !mock.texts("layout.ack").is_empty()
    }));

    let hello = &mock.texts("hello")[0];
    assert_eq!(hello["protocol"], 1);
    assert_eq!(hello["collector_id"], ID);
    assert_eq!(hello["live_table_version"], TABLE_VERSION);
    assert!(
        hello["collector_version"]
            .as_str()
            .unwrap()
            .starts_with("0.")
    );
    assert!(hello["machine"].as_str().unwrap().len() <= 64);
    assert_eq!(
        mock.with(|s| s.subprotocols[0].clone()),
        "coach-collector.1"
    );

    let status = &mock.texts("status")[0];
    assert_eq!(status["sim"], "not_running");
    assert_eq!(status["streaming"], false);
    assert_eq!(status["queue"]["uploads_pending"], 0);

    let ack = &mock.texts("layout.ack")[0];
    assert_eq!(
        (ack["layout_id"].as_str(), ack["version"].as_i64()),
        (Some("factory-race"), Some(7))
    );
    let layout = rig.hub.layout().unwrap();
    assert_eq!(
        (layout.layout_id.as_str(), layout.version),
        ("factory-race", 7)
    );
    assert!(
        rig.hub.stream().demand(),
        "welcome.stream_wanted acts as stream.start"
    );
    let info = rig.hub.link_info();
    assert!(info.connected);
    assert_eq!(info.viewers, 2);
    assert_eq!(info.server_version.as_deref(), Some("0.19.0-test"));
    assert!(mock.with(|s| s.schema_errors.clone()).is_empty());
    rig.stop();
}

#[test]
fn status_is_sent_on_change_and_periodically() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |_| {});
    assert!(wait_until(Duration::from_secs(3), || !mock
        .texts("status")
        .is_empty()));
    let before = mock.texts("status").len();
    rig.hub.set_status(StatusModel {
        sim: SimKind::InSession,
        connected: true,
        on_track: true,
        streaming: true,
        session: Some(json!({"session_epoch": 3, "subsession_id": 0, "session_num": 0, "session_type": "Offline Testing", "track": "Las Vegas", "track_config": null, "car_path": "stockcars2 supra2019"})),
        driver: Some(json!({"customer_id": 123456, "name": "Joshua Granger"})),
        ..StatusModel::default()
    });
    assert!(wait_until(Duration::from_secs(2), || {
        mock.texts("status")
            .iter()
            .any(|s| s["sim"] == "in_session")
    }));
    let last = mock
        .texts("status")
        .into_iter()
        .rfind(|s| s["sim"] == "in_session")
        .unwrap();
    assert_eq!(last["session"]["track"], "Las Vegas");
    assert_eq!(last["driver"]["customer_id"], 123456);
    assert_eq!(last["on_track"], true);
    // With no change at all a status still goes out at the interval.
    let n = mock.texts("status").len();
    assert!(n > before);
    assert!(wait_until(Duration::from_secs(2), || mock
        .texts("status")
        .len()
        > n + 1));
    assert!(
        mock.with(|s| s.schema_errors.clone()).is_empty(),
        "{:?}",
        mock.with(|s| s.schema_errors.clone())
    );
    rig.stop();
}

#[test]
fn ping_is_answered_and_stream_control_and_viewers_are_applied() {
    let mock = Mock::start();
    mock.with(|s| s.ping_every = Some(Duration::from_millis(40)));
    let rig = Rig::start(&mock, |_| {});
    assert!(wait_until(Duration::from_secs(3), || mock
        .texts("pong")
        .len()
        >= 2));
    let pong = &mock.texts("pong")[0];
    assert_eq!(pong["nonce"], 1);

    mock.with(|s| {
        s.ws_outbox
            .push_back(json!({"type": "stream.start", "reason": "viewer"}));
        s.ws_outbox
            .push_back(json!({"type": "viewers", "count": 3}));
        s.ws_outbox
            .push_back(json!({"type": "mystery.message", "x": 1}));
        s.ws_outbox
            .push_back(json!({"type": "ingest.state", "sha256": "aa", "state": "parsed"}));
    });
    assert!(wait_until(Duration::from_secs(2), || rig
        .hub
        .stream()
        .demand()
        && rig.hub.link_info().viewers == 3));
    assert!(wait_until(Duration::from_secs(2), || {
        rig.hub
            .link_info()
            .ingest_states
            .get("aa")
            .map(String::as_str)
            == Some("parsed")
    }));
    mock.with(|s| s.ws_outbox.push_back(json!({"type": "stream.release"})));
    assert!(wait_until(Duration::from_secs(2), || !rig
        .hub
        .stream()
        .demand()));
    assert!(
        rig.hub.stream().latch(),
        "the latch stays until the stream session ends"
    );
    assert!(
        rig.hub.link_info().connected,
        "an unknown type does not break the link"
    );
    rig.stop();
}

#[test]
fn queued_frames_are_sent_unchanged_and_stream_stats_follow() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |_| {});
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .link_info()
        .connected));
    rig.hub.set_status(StatusModel {
        connected: true,
        streaming: true,
        sim: SimKind::InSession,
        ..StatusModel::default()
    });
    for i in 0..10u8 {
        rig.hub.frames.push(vec![b'L', b'F', i, 9, 9]);
    }
    assert!(wait_until(Duration::from_secs(3), || mock
        .with(|s| s.ws_binary.len())
        == 10));
    let frames = mock.with(|s| {
        s.ws_binary
            .iter()
            .map(|(_, f)| f.clone())
            .collect::<Vec<_>>()
    });
    assert_eq!(frames[3], vec![b'L', b'F', 3, 9, 9]);
    assert_eq!(rig.hub.frames.sent(), 10);
    assert!(wait_until(Duration::from_secs(3), || !mock
        .texts("stream.stats")
        .is_empty()));
    let stats = &mock.texts("stream.stats")[0];
    assert!(stats["sent"].as_u64().unwrap() <= 10);
    assert_eq!(stats["dropped"], 0);
    assert!(stats["since"].as_str().unwrap().ends_with('Z'));
    assert!(mock.with(|s| s.schema_errors.clone()).is_empty());
    rig.stop();
}

#[test]
fn garage_events_go_out_in_order_and_the_ack_drops_them() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |_| {});
    {
        let mut j = rig.hub.journal();
        j.add_snapshot(snapshot("v1", 4, "152 kPa")).unwrap();
        j.add_snapshot(snapshot("v1", 5, "155 kPa")).unwrap();
        j.add_commit(commit("v1", 5)).unwrap();
    }
    assert!(wait_until(Duration::from_secs(3), || {
        mock.texts("garage.snapshot").len() == 2 && mock.texts("garage.commit").len() == 1
    }));
    let commit_msg = &mock.texts("garage.commit")[0];
    assert_eq!(
        commit_msg["values"]["Tires.LeftFront.ColdPressure"],
        "155 kPa"
    );
    assert_eq!(commit_msg["setup_name"], "claude LV R CAM.sto");
    assert_eq!(commit_msg["is_modified"], true);
    assert_eq!(commit_msg["sto_sha256"], "ab".repeat(32));
    // Order on the wire: both snapshots before the commit.
    let order: Vec<String> = mock.with(|s| {
        s.ws_text
            .iter()
            .filter(|(_, v)| v["type"].as_str().is_some_and(|t| t.starts_with("garage.")))
            .map(|(_, v)| v["type"].as_str().unwrap().to_string())
            .collect()
    });
    assert_eq!(
        order,
        ["garage.snapshot", "garage.snapshot", "garage.commit"]
    );
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .journal()
        .garage_pending()
        == 0));
    assert!(
        mock.with(|s| s.schema_errors.clone()).is_empty(),
        "{:?}",
        mock.with(|s| s.schema_errors.clone())
    );
    rig.stop();
}

#[test]
fn unacknowledged_garage_events_are_resent_after_a_reconnect() {
    let mock = Mock::start();
    mock.with(|s| s.auto_ack_garage = false);
    let rig = Rig::start(&mock, |_| {});
    {
        let mut j = rig.hub.journal();
        j.add_snapshot(snapshot("v9", 1, "a")).unwrap();
        j.add_commit(commit("v9", 1)).unwrap();
    }
    assert!(wait_until(Duration::from_secs(3), || mock.with(|s| s
        .ws_text
        .iter()
        .filter(|(c, v)| *c == 1 && v["type"] == "garage.commit")
        .count())
        == 1));
    mock.with(|s| s.drop_ws = true);
    assert!(wait_until(Duration::from_secs(3), || mock
        .with(|s| s.ws_connections)
        == 2));
    assert!(wait_until(Duration::from_secs(3), || mock.with(|s| {
        s.ws_text
            .iter()
            .filter(|(c, v)| {
                *c == 2 && v["type"].as_str().is_some_and(|t| t.starts_with("garage."))
            })
            .count()
    }) == 2));
    let again: Vec<String> = mock.with(|s| {
        s.ws_text
            .iter()
            .filter(|(c, v)| {
                *c == 2 && v["type"].as_str().is_some_and(|t| t.starts_with("garage."))
            })
            .map(|(_, v)| v["type"].as_str().unwrap().to_string())
            .collect()
    });
    assert_eq!(again, ["garage.snapshot", "garage.commit"], "oldest first");
    assert_eq!(rig.hub.journal().garage_pending(), 2);
    mock.with(|s| {
        s.ws_outbox.push_back(
            json!({"type": "garage.ack", "visit_id": "v9", "kind": "snapshot", "update_count": 1}),
        );
        s.ws_outbox.push_back(
            json!({"type": "garage.ack", "visit_id": "v9", "kind": "commit", "update_count": 1}),
        );
    });
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .journal()
        .garage_pending()
        == 0));
    rig.stop();
}

#[test]
fn reconnects_with_backoff_after_a_drop() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |s| s.backoff_ms = vec![200]);
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .link_info()
        .connected));
    let dropped_at = Instant::now();
    mock.with(|s| s.drop_ws = true);
    assert!(wait_until(Duration::from_secs(5), || mock
        .with(|s| s.ws_connections)
        == 2));
    assert!(
        dropped_at.elapsed() >= Duration::from_millis(190),
        "backoff of 200 ms was honoured: {:?}",
        dropped_at.elapsed()
    );
    rig.stop();
}

#[test]
fn an_incompatible_protocol_keeps_the_collector_running_and_retries_slowly() {
    let mock = Mock::start();
    mock.with(|s| s.close_with = Some(4001));
    let rig = Rig::start(&mock, |s| s.incompatible_retry_ms = 250);
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .link_info()
        .update_needed));
    assert!(!rig.hub.link_info().connected);
    assert!(
        wait_until(Duration::from_secs(3), || mock.with(|s| s.ws_connections)
            >= 2),
        "retried"
    );
    // The server is fixed: the next attempt connects and clears the notice.
    mock.with(|s| s.close_with = None);
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .link_info()
        .connected));
    assert!(!rig.hub.link_info().update_needed);
    rig.stop();
}

#[test]
fn a_duplicate_collector_id_pauses_reconnecting() {
    let mock = Mock::start();
    mock.with(|s| s.close_with = Some(4002));
    let rig = Rig::start(&mock, |s| s.duplicate_pause_ms = 600);
    assert!(wait_until(Duration::from_secs(3), || mock
        .with(|s| s.ws_connections)
        == 1));
    thread::sleep(Duration::from_millis(400));
    assert_eq!(mock.with(|s| s.ws_connections), 1, "paused");
    assert!(wait_until(Duration::from_secs(3), || mock
        .with(|s| s.ws_connections)
        >= 2));
    rig.stop();
}

#[test]
fn a_silent_server_is_given_up_on() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |s| s.silence_limit_ms = 300);
    assert!(wait_until(Duration::from_secs(5), || mock
        .with(|s| s.ws_connections)
        >= 2));
    rig.stop();
}

#[test]
fn shutdown_closes_the_socket_promptly() {
    let mock = Mock::start();
    let rig = Rig::start(&mock, |_| {});
    assert!(wait_until(Duration::from_secs(3), || rig
        .hub
        .link_info()
        .connected));
    let t = Instant::now();
    rig.stop();
    assert!(t.elapsed() < Duration::from_secs(2));
}
