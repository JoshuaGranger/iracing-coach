//! The view endpoints over the real `coach-views` projector: the traces endpoint serves any channel
//! the catalog lists, not only the default set, straight from the stored CCOL.

mod support;

use std::path::Path;
use std::sync::Arc;

use axum::http::StatusCode;
use coach_server::adapters::ViewsProjector;
use serde_json::Value;
use support::*;

const ANALYSIS: &str = "g2-analysis";
const DEFAULTS: [&str; 8] = [
    "speed",
    "session-time",
    "throttle",
    "brake",
    "steering",
    "slip",
    "latitude",
    "longitude",
];

fn g2(file: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../test-data/g2")
        .join(file)
}

/// The committed G2 view files (an 82-lap race, 77 channels) served under a ready analysis.
async fn served() -> Env {
    let env = Env::with_projector(Arc::new(ViewsProjector)).await;
    let bytes = fake_ibt(race_meta(9900, 0, "g2"), 4_000);
    let reply = env.upload(&bytes, "ibt", "rec.ibt", None, 4096).await;
    assert_eq!(reply.status, StatusCode::OK);
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    let views = env
        .app
        .cfg
        .views_dir(ANALYSIS, coach_views::PROJECTION_VERSION);
    std::fs::create_dir_all(&views).unwrap();
    for file in ["traces.ccol", "traces-catalog.json"] {
        std::fs::copy(g2(file), views.join(file)).unwrap();
    }
    let version = i64::from(coach_views::PROJECTION_VERSION);
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "INSERT INTO sessions(customer_id, subsession_id, sim_session_num, group_key, session_type, track,
                   car_path, start_time_utc, discovered_at)
                 VALUES (1001, 9900, 0, 'subsession:9900:0', 'Race', 'Stub Speedway', 'stockcars chevyss2020',
                         '2026-08-01T12:00:00Z', '2026-08-01T13:00:00Z')",
                [],
            )?;
            let session: i64 = c.query_row("SELECT session_id FROM sessions", [], |r| r.get(0))?;
            c.execute(
                "INSERT INTO analyses(analysis_id, session_id, recordings_sha256, membership_sha256, schema_version,
                   profile_version, analysis_path, analysis_bytes, created_at, projection_version, projection_state)
                 VALUES (?1, ?2, '[]', 'x', 1, 'p', '/none', 1, '2026-01-01T00:00:00Z', ?3, 'ready')",
                rusqlite::params![ANALYSIS, session, version],
            )?;
            Ok(())
        })
        .unwrap();
    env
}

/// (lap numbers, channel ids in the container's header) of a CCOL body.
fn header(body: &[u8]) -> (Vec<i64>, Vec<String>) {
    assert_eq!(&body[..4], b"CCOL");
    let length = u32::from_le_bytes(body[8..12].try_into().unwrap()) as usize;
    let header: Value = serde_json::from_slice(&body[12..12 + length]).unwrap();
    let laps = header["laps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_i64().unwrap())
        .collect();
    let channels = header["channels"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["id"].as_str().unwrap().to_string())
        .collect();
    (laps, channels)
}

#[tokio::test]
async fn channels_outside_the_default_set_are_served_on_request() {
    let env = served().await;
    let path = format!("/api/v1/analyses/{ANALYSIS}/traces");

    // no `channels`: the catalog's default eight
    let r = env.get(&path).await;
    assert_eq!(r.status, StatusCode::OK);
    let (laps, channels) = header(&r.body);
    assert_eq!(laps.len(), 83);
    assert_eq!(channels, DEFAULTS, "in the catalog's order");

    // the five rows of the old layout, the tire load and a corner channel are all in the CCOL
    let extra = "gear,rpm,yaw,lateral-g,longitudinal-g,tire-stress,lf-pressure";
    let r = env
        .get(&format!("{path}?laps=2,6-7&channels={extra}"))
        .await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    assert_eq!(
        r.header("content-type").as_deref(),
        Some("application/vnd.coach.columns")
    );
    assert_eq!(r.header("x-coach-trace-laps").as_deref(), Some("3"));
    let (laps, mut channels) = header(&r.body);
    assert_eq!(laps, [2, 6, 7]);
    channels.sort();
    let mut want: Vec<&str> = extra.split(',').collect();
    want.sort_unstable();
    assert_eq!(channels, want);

    // defaults and extras together, every lap
    let r = env
        .get(&format!("{path}?channels=speed,session-time,{extra}"))
        .await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(header(&r.body).1.len(), 9);

    // every channel the catalog lists is servable
    let catalog: Value =
        serde_json::from_slice(&std::fs::read(g2("traces-catalog.json")).unwrap()).unwrap();
    let all: Vec<String> = catalog["signals"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["id"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(all.len(), 77);
    let r = env
        .get(&format!("{path}?laps=1&channels={}", all.join(",")))
        .await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(header(&r.body).1.len(), 77);

    // a name the catalog does not list is a 400 that names it
    let r = env.get(&format!("{path}?channels=speed,nonsense")).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    assert!(r.json()["message"].as_str().unwrap().contains("nonsense"));
}
