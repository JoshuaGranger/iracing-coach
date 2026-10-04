//! The Lab library (`src/library.rs`, `web-round-2-plan.md` "Lab library"): a recording inside a
//! lab window with the window's car and track, from a solo offline test, is filed lab at ingest;
//! every session, setup-usage and statistics query defaults to main and takes `library=lab`; the
//! classify repair applies the windows to stored recordings (dry run first, idempotent, logged);
//! a recording whose start falls in a window without the matching car and track, and a race
//! against AI, stay main.

mod support;

use axum::http::StatusCode;
use coach_server::util::sha256_hex;
use serde_json::{Value, json};
use support::*;

macro_rules! require_python {
    ($env:expr) => {
        if $env.python.is_none() {
            eprintln!("skipped: no Python 3 on PATH (set COACH_TEST_PYTHON)");
            return;
        }
    };
}

const SUPRA: &str = "stockcars2 supra2019";
const IRP: &str = "Lucas Oil Indianapolis Raceway Park";

/// The bot G4 run's window: IRP, 2026-10-04 08:00 to 08:52 local (UTC-5).
fn g4_window() -> Value {
    json!({"from": "2026-10-04T08:00:00-05:00", "until": "2026-10-04T08:52:00-05:00",
           "car": SUPRA, "track": "lucas oil indianapolis raceway park",
           "note": "G4 bot run (sessions 658, 659)", "source": "testdriver"})
}

/// One file of a solo offline Test Drive starting at `start` (UTC): a sim session of its own.
fn offline(start: &str, car: &str, track: &str, name: &str) -> Value {
    let date = coach_server::util::parse_iso(start).unwrap();
    json!({
        "customer_id": CUSTOMER, "user_name": "Joshua Granger", "irating": 1,
        "subsession_id": 0, "session_id": 0, "session_unique_id": 1, "sim_session_num": 0,
        "session_type": "Offline Testing", "track": track, "track_config": "Oval",
        "car_path": car, "car_name": "Toyota Supra", "start": start, "name": name,
        "session_start_date": date, "session_start_time": 0.0, "session_end_time": 600.0,
        "setup": {"values": {"Chassis.Front.ArbSetting": name, "UpdateCount": "1"},
                  "name": format!("{name}.sto"), "is_modified": false, "load_type": "user", "update_count": 1},
    })
}

async fn upload(env: &Env, meta: Value) -> String {
    let bytes = fake_ibt(meta, 4_000);
    let reply = env.upload(&bytes, "ibt", "rec.ibt", None, 4096).await;
    assert!(
        reply.status.is_success(),
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    sha256_hex(&bytes)
}

fn library_of(env: &Env, sha: &str) -> (String, Option<i64>) {
    let sha = sha.to_string();
    env.query(move |c| {
        c.query_row(
            "SELECT library, library_window_id FROM recordings WHERE sha256 = ?1",
            [sha],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    })
}

fn check(contract: &Contract, method: &str, template: &str, reply: &Reply) {
    contract.check(
        method,
        template,
        reply.status.as_u16(),
        "application/json",
        &reply.json(),
    );
}

async fn post_window(env: &Env, contract: &Contract, body: Value) -> Value {
    let r = env.post("/api/v1/admin/library/windows", body).await;
    assert!(
        r.status == StatusCode::CREATED || r.status == StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(contract, "POST", "/api/v1/admin/library/windows", &r);
    r.json()
}

/// The recordings of the acceptance: the bot run and its in-stall stub (lab), a Tundra test in
/// the same window (main, overlap), Joshua's race against AI in it (main), Joshua's own test a
/// day later (main).
struct Fixture {
    bot: String,
    stub: String,
    tundra: String,
    ai_race: String,
    own: String,
}

async fn fixture(env: &Env) -> Fixture {
    let bot = upload(env, offline("2026-10-04T13:11:20Z", SUPRA, IRP, "bot")).await;
    let stub = upload(env, offline("2026-10-04T13:39:52Z", SUPRA, IRP, "stub")).await;
    let tundra = upload(
        env,
        offline(
            "2026-10-04T13:20:00Z",
            "trucks toyotatundra2022",
            IRP,
            "tundra",
        ),
    )
    .await;
    let mut race = offline("2026-10-04T13:30:00Z", SUPRA, IRP, "airace");
    race["session_type"] = json!("Race");
    let ai_race = upload(env, race).await;
    let own = upload(env, offline("2026-10-05T01:00:00Z", SUPRA, IRP, "own")).await;
    Fixture {
        bot,
        stub,
        tundra,
        ai_race,
        own,
    }
}

#[tokio::test]
async fn a_recording_in_a_matching_window_is_filed_lab_at_ingest() {
    let env = Env::new().await;
    let contract = Contract::load();
    let window = post_window(&env, &contract, g4_window()).await;
    let id = window["window_id"].as_i64().unwrap();
    assert_eq!(window["from"], "2026-10-04T13:00:00Z", "stored in UTC");
    assert_eq!(window["until"], "2026-10-04T13:52:00Z");
    let f = fixture(&env).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    assert_eq!(library_of(&env, &f.bot), ("lab".into(), Some(id)));
    assert_eq!(library_of(&env, &f.stub), ("lab".into(), Some(id)));
    assert_eq!(
        library_of(&env, &f.tundra),
        ("main".into(), None),
        "another car in the window"
    );
    assert_eq!(
        library_of(&env, &f.ai_race),
        ("main".into(), None),
        "a race against AI"
    );
    assert_eq!(
        library_of(&env, &f.own),
        ("main".into(), None),
        "outside the window"
    );
    let r = env.get("/api/v1/admin/library/windows").await;
    assert_eq!(r.status, StatusCode::OK);
    check(&contract, "GET", "/api/v1/admin/library/windows", &r);
    assert_eq!(r.json()["windows"][0]["recording_count"], 2);
}

#[tokio::test]
async fn posting_the_same_start_car_and_track_closes_the_window() {
    let env = Env::new().await;
    let contract = Contract::load();
    let r = env.post("/api/v1/admin/library/windows", g4_window()).await;
    assert_eq!(r.status, StatusCode::CREATED);
    let id = r.json()["window_id"].clone();
    let mut close = g4_window();
    close["until"] = json!("2026-10-04T13:45:00Z");
    close["car"] = json!("STOCKCARS2 SUPRA2019");
    let r = env.post("/api/v1/admin/library/windows", close).await;
    assert_eq!(r.status, StatusCode::OK, "the same window");
    check(&contract, "POST", "/api/v1/admin/library/windows", &r);
    assert_eq!(r.json()["window_id"], id);
    assert_eq!(r.json()["until"], "2026-10-04T13:45:00Z");
    let r = env.get("/api/v1/admin/library/windows").await;
    assert_eq!(r.json()["windows"].as_array().unwrap().len(), 1);
    let log = std::fs::read_to_string(env.data().join("run").join("library.jsonl")).unwrap();
    assert_eq!(log.lines().count(), 2, "both posts are logged");

    for bad in [
        json!({"from": "2026-10-04T13:00:00Z", "until": "2026-10-04T12:00:00Z", "car": SUPRA, "track": "irp"}),
        json!({"from": "2026-10-01T00:00:00Z", "until": "2026-10-04T12:00:00Z", "car": SUPRA, "track": "irp"}),
        json!({"from": "yesterday", "until": "2026-10-04T12:00:00Z", "car": SUPRA, "track": "irp"}),
        json!({"from": "2026-10-04T13:00:00Z", "until": "2026-10-04T14:00:00Z", "car": " ", "track": "irp"}),
        json!({"from": "2026-10-04T13:00:00Z", "until": "2026-10-04T14:00:00Z", "car": SUPRA}),
    ] {
        let r = env.post("/api/v1/admin/library/windows", bad.clone()).await;
        assert_eq!(r.status, StatusCode::BAD_REQUEST, "{bad}");
        check(&contract, "POST", "/api/v1/admin/library/windows", &r);
    }
}

/// `(session_id, library)` of the sessions a list query returns.
async fn listed(env: &Env, contract: &Contract, query: &str) -> Vec<(i64, String)> {
    let r = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}/sessions{query}"))
        .await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(
        contract,
        "GET",
        "/api/v1/drivers/{customer_id}/sessions",
        &r,
    );
    r.json()["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            (
                s["session_id"].as_i64().unwrap(),
                s["library"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

fn session_of(env: &Env, sha: &str) -> i64 {
    let sha = sha.to_string();
    env.query(move |c| {
        c.query_row(
            "SELECT session_id FROM session_recordings WHERE recording_sha256 = ?1",
            [sha],
            |r| r.get(0),
        )
    })
}

#[tokio::test]
async fn every_list_defaults_to_main_and_lab_is_explicit() {
    let env = Env::new().await;
    require_python!(env);
    let contract = Contract::load();
    post_window(&env, &contract, g4_window()).await;
    let f = fixture(&env).await;
    // a hosted race of Joshua's in the same car, before the window
    let mut hosted = race_meta(9701, 0, "hosted");
    hosted["car_path"] = json!(SUPRA);
    hosted["start"] = json!("2026-10-03T23:00:00Z");
    upload(&env, hosted).await;
    env.settle().await;

    let (bot, stub) = (session_of(&env, &f.bot), session_of(&env, &f.stub));
    assert_ne!(bot, stub, "two sim sessions here");
    let main = listed(&env, &contract, "").await;
    let lab = listed(&env, &contract, "?library=lab").await;
    assert_eq!(main.len(), 4, "{main:?}");
    assert!(main.iter().all(|(_, l)| l == "main"));
    assert!(!main.iter().any(|(id, _)| *id == bot || *id == stub));
    for sha in [&f.tundra, &f.ai_race, &f.own] {
        let id = session_of(&env, sha);
        assert!(main.iter().any(|(s, _)| *s == id), "{sha} in main");
    }
    assert_eq!(lab, vec![(stub, "lab".into()), (bot, "lab".into())]);
    assert_eq!(listed(&env, &contract, "?library=main").await, main);
    let r = env
        .get(&format!(
            "/api/v1/drivers/{CUSTOMER}/sessions?library=joshua"
        ))
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/sessions",
        &r,
    );
    // the session detail says which library
    let r = env.get(&format!("/api/v1/sessions/{bot}")).await;
    assert_eq!(r.json()["session"]["library"], "lab", "{}", r.json());

    // statistics: the driver's counts are main's unless asked
    let r = env.get(&format!("/api/v1/drivers/{CUSTOMER}")).await;
    check(&contract, "GET", "/api/v1/drivers/{customer_id}", &r);
    assert_eq!(r.json()["session_count"], 4);
    assert_eq!(r.json()["last_session_at"], "2026-10-05T01:00:00Z");
    let r = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}?library=lab"))
        .await;
    assert_eq!(r.json()["session_count"], 2);
    assert_eq!(r.json()["last_session_at"], "2026-10-04T13:39:52Z");
    let r = env.get("/api/v1/drivers").await;
    check(&contract, "GET", "/api/v1/drivers", &r);
    assert_eq!(r.json()["drivers"][0]["session_count"], 4);
    let r = env.get("/api/v1/drivers?library=lab").await;
    assert_eq!(r.json()["drivers"][0]["session_count"], 2);
    let r = env.get("/api/v1/drivers?library=x").await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    check(&contract, "GET", "/api/v1/drivers", &r);

    // filter choices: the Tundra was only ever driven... by Joshua, in main
    let r = env
        .get(&format!(
            "/api/v1/drivers/{CUSTOMER}/cars-and-tracks?library=lab"
        ))
        .await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/cars-and-tracks",
        &r,
    );
    let cars: Vec<String> = r.json()["cars"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["car_path"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(cars, vec![SUPRA.to_string()]);
    let r = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}/cars-and-tracks"))
        .await;
    assert_eq!(r.json()["cars"].as_array().unwrap().len(), 2);

    // setup usage: the bot's setups are lab usage only
    let names = |page: &Value| -> Vec<(String, i64)> {
        let mut v: Vec<(String, i64)> = page["setups"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| {
                (
                    s["names"][0].as_str().unwrap_or("").to_string(),
                    s["observation_count"].as_i64().unwrap(),
                )
            })
            .collect();
        v.sort();
        v
    };
    let r = env.get(&format!("/api/v1/drivers/{CUSTOMER}/setups")).await;
    check(&contract, "GET", "/api/v1/drivers/{customer_id}/setups", &r);
    assert_eq!(
        names(&r.json()),
        vec![
            ("airace.sto".to_string(), 1),
            ("baseline.sto".to_string(), 1),
            ("own.sto".to_string(), 1),
            ("tundra.sto".to_string(), 1)
        ]
    );
    let r = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}/setups?library=lab"))
        .await;
    check(&contract, "GET", "/api/v1/drivers/{customer_id}/setups", &r);
    assert_eq!(
        names(&r.json()),
        vec![("bot.sto".to_string(), 1), ("stub.sto".to_string(), 1)]
    );
    assert_eq!(r.json()["unlinked_files"], json!([]));
}

#[tokio::test]
async fn the_classify_repair_files_stored_recordings_once() {
    let env = Env::new().await;
    let contract = Contract::load();
    // recorded before any window: all main
    let f = fixture(&env).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    assert_eq!(library_of(&env, &f.bot).0, "main");
    let window = post_window(&env, &contract, g4_window()).await;
    let id = window["window_id"].as_i64().unwrap();
    assert_eq!(
        window["recording_count"], 0,
        "a window refiles nothing itself"
    );

    // dry run: the plan, nothing changes, nothing logged
    let r = env.post("/api/v1/admin/library/classify", json!({})).await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(&contract, "POST", "/api/v1/admin/library/classify", &r);
    let plan = r.json();
    assert_eq!(plan["dry_run"], true);
    assert_eq!(plan["recordings_checked"], 5);
    assert_eq!(plan["lab_recordings"], 2);
    let mut changed: Vec<&str> = plan["changes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["sha256"].as_str().unwrap())
        .collect();
    changed.sort();
    let mut want = vec![f.bot.as_str(), f.stub.as_str()];
    want.sort();
    assert_eq!(changed, want);
    assert_eq!(plan["changes"][0]["from"], "main");
    assert_eq!(plan["changes"][0]["to"], "lab");
    assert_eq!(plan["changes"][0]["window_id"], id);
    // the overlap rule: in the window without the matching car or session kind, main
    let overlaps: Vec<(String, Value)> = plan["overlaps_kept_main"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| {
            (
                o["sha256"].as_str().unwrap().to_string(),
                o["reasons"].clone(),
            )
        })
        .collect();
    assert_eq!(overlaps.len(), 2, "{overlaps:?}");
    assert!(overlaps.contains(&(f.tundra.clone(), json!(["car"]))));
    assert!(overlaps.contains(&(f.ai_race.clone(), json!(["not_solo_offline_test"]))));
    assert_eq!(plan["log"], Value::Null);
    assert_eq!(
        library_of(&env, &f.bot).0,
        "main",
        "a dry run changes nothing"
    );
    assert!(
        !env.data().join("run").join("library.jsonl").exists() || {
            let log =
                std::fs::read_to_string(env.data().join("run").join("library.jsonl")).unwrap();
            !log.contains("\"classify\"")
        }
    );

    // the real call applies the plan and logs it
    let r = env
        .post("/api/v1/admin/library/classify", json!({"dry_run": false}))
        .await;
    check(&contract, "POST", "/api/v1/admin/library/classify", &r);
    let done = r.json();
    assert_eq!(done["changes"], plan["changes"]);
    assert!(done["log"].as_str().unwrap().ends_with("library.jsonl"));
    assert_eq!(library_of(&env, &f.bot), ("lab".into(), Some(id)));
    assert_eq!(library_of(&env, &f.stub), ("lab".into(), Some(id)));
    for sha in [&f.tundra, &f.ai_race, &f.own] {
        assert_eq!(library_of(&env, sha), ("main".into(), None));
    }

    // idempotent: the second call changes nothing
    let r = env
        .post("/api/v1/admin/library/classify", json!({"dry_run": false}))
        .await;
    assert_eq!(r.json()["changes"], json!([]));
    assert_eq!(r.json()["lab_recordings"], 2);
    let log = std::fs::read_to_string(env.data().join("run").join("library.jsonl")).unwrap();
    let classify: Vec<Value> = log
        .lines()
        .map(|l| serde_json::from_str::<Value>(l).unwrap())
        .filter(|l| l["action"] == "classify")
        .collect();
    assert_eq!(classify.len(), 2, "each real call is logged");
    assert_eq!(
        classify[0]["report"]["changes"].as_array().unwrap().len(),
        2
    );

    // one driver only, and a malformed body
    let r = env
        .post(
            "/api/v1/admin/library/classify",
            json!({"customer_id": 4242}),
        )
        .await;
    assert_eq!(r.json()["recordings_checked"], 0);
    let r = env
        .post("/api/v1/admin/library/classify", json!({"dry_run": "no"}))
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    check(&contract, "POST", "/api/v1/admin/library/classify", &r);

    // a window moved away: the repair files them main again
    let mut moved = g4_window();
    moved["until"] = json!("2026-10-04T08:05:00-05:00");
    post_window(&env, &contract, moved).await;
    let r = env
        .post("/api/v1/admin/library/classify", json!({"dry_run": false}))
        .await;
    assert_eq!(r.json()["changes"].as_array().unwrap().len(), 2);
    assert_eq!(r.json()["changes"][0]["to"], "main");
    assert_eq!(library_of(&env, &f.bot), ("main".into(), None));
}

/// The historical windows of `acceptance/lab-library.md` (its `lab-windows` JSON block).
fn historical_windows() -> Vec<coach_server::library::Window> {
    let doc = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../docs/08-rewrite/acceptance/lab-library.md"),
    )
    .unwrap();
    let block = doc
        .split("```json lab-windows")
        .nth(1)
        .and_then(|rest| rest.split("```").next())
        .expect("the lab-windows block");
    let list: Vec<Value> = serde_json::from_str(block).expect("the block is JSON");
    list.iter()
        .enumerate()
        .map(|(i, w)| {
            // each one is a valid post
            let parsed = coach_server::library::parse_window(w.to_string().as_bytes())
                .unwrap_or_else(|e| panic!("window {}: {e}", i + 1));
            coach_server::library::Window {
                window_id: i as i64 + 1,
                from: parsed.from,
                until: parsed.until,
                car: parsed.car,
                track: parsed.track,
            }
        })
        .collect()
}

#[test]
fn the_historical_windows_are_valid_and_cover_the_acceptance_examples() {
    use coach_server::library::{RecordingFacts, Verdict, classify};
    let windows = historical_windows();
    assert_eq!(windows.len(), 9);
    let rec = |start: &str, track: &str, subsession: i64, event: &str| {
        let header = json!({"summary": {"track_name": track, "event_type": event,
                                        "sim_session_type": "Offline Testing"}});
        RecordingFacts::new(
            "x".into(),
            Some(1_507_810),
            Some(start.into()),
            Some(SUPRA.into()),
            None,
            None,
            Some(subsession),
            &header.to_string(),
        )
    };
    // sessions 658 and 659, the 08:39 stub
    assert_eq!(
        classify(&rec("2026-10-04T13:11:20Z", "irp", 0, "Test"), &windows),
        Verdict::Lab { window_id: 9 }
    );
    assert_eq!(
        classify(&rec("2026-10-04T13:39:52Z", "irp", 0, "Test"), &windows),
        Verdict::Lab { window_id: 9 }
    );
    // a 2026-09-25 bot session (`2026-09-25 17-56-25`, local) and a harvest-day G3 file
    assert_eq!(
        classify(&rec("2026-09-25T22:56:25Z", "irp", 0, "Test"), &windows),
        Verdict::Lab { window_id: 2 }
    );
    assert_eq!(
        classify(
            &rec("2026-10-03T14:01:49Z", "vegas oval", 0, "Test"),
            &windows
        ),
        Verdict::Lab { window_id: 6 }
    );
    // Joshua: his 2026-09-23 IRP race, his 2026-10-02 Vegas race, a race against AI in a window
    assert_eq!(
        classify(&rec("2026-09-24T02:09:03Z", "irp", 0, "Test"), &windows),
        Verdict::Main
    );
    assert_eq!(
        classify(
            &rec("2026-10-03T01:08:50Z", "vegas oval", 87_000_000, "Race"),
            &windows
        ),
        Verdict::Main
    );
    assert!(matches!(
        classify(&rec("2026-09-26T12:00:00Z", "irp", 0, "Race"), &windows),
        Verdict::Overlap { .. }
    ));
}

/// The dry run over real recordings: every `.ibt` under the directories in `COACH_LAB_CORPUS`
/// (separated by `;`), read with the real parser and filed by the historical windows. Prints each
/// lab file and each overlap and the totals; skipped when the variable is not set (private data).
#[test]
fn corpus_dry_run() {
    let Ok(dirs) = std::env::var("COACH_LAB_CORPUS") else {
        eprintln!("skipped: COACH_LAB_CORPUS not set");
        return;
    };
    use coach_server::adapters::RecordingParser;
    use coach_server::library::{RecordingFacts, Verdict, classify};
    let windows = historical_windows();
    let parser = coach_server::adapters::IbtParser;
    let (mut files, mut failed, mut lab, mut overlaps) = (0, 0, 0, 0);
    let mut per_window = std::collections::BTreeMap::<i64, usize>::new();
    for dir in dirs.split(';').filter(|d| !d.is_empty()) {
        let mut paths: Vec<_> = std::fs::read_dir(dir)
            .expect("a directory")
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("ibt")))
            .collect();
        paths.sort();
        for path in paths {
            files += 1;
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let Ok(p) = parser.parse(&path) else {
                failed += 1;
                eprintln!("unreadable  {name}");
                continue;
            };
            let facts = RecordingFacts::new(
                name.clone(),
                p.customer_id,
                p.start_time_utc.clone(),
                p.car_path.clone(),
                p.car_name.clone(),
                p.track.clone(),
                p.subsession_id,
                &p.header_json.to_string(),
            );
            match classify(&facts, &windows) {
                Verdict::Lab { window_id } => {
                    lab += 1;
                    *per_window.entry(window_id).or_default() += 1;
                    eprintln!("lab (w{window_id})  {name}");
                }
                Verdict::Overlap { window_id, reasons } => {
                    overlaps += 1;
                    eprintln!("main, overlap w{window_id} {reasons:?}  {name}");
                }
                Verdict::Main => {}
            }
        }
    }
    eprintln!(
        "{files} recordings, {failed} unreadable: {lab} lab {per_window:?}, {overlaps} overlaps kept main, {} main",
        files - failed - lab
    );
}
