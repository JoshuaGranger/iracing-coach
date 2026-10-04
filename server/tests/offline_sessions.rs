//! Offline stints of one iRacing session become one library session (`src/offline_sessions.rs`):
//! the two recordings of the 2026-10-04 bot run (Test Drive, SubSessionID 0, sessions 658 and 659)
//! join at discovery; sessions an older server split are merged once by the repair; a new Test
//! Drive, a hosted session and a recording timed by its file only stay apart.

mod support;

use coach_server::offline_sessions::{self, Stint, StintKey};
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

/// 13:11:20Z, the bot run's first file (`stockcars2 supra2019_irp 2026-10-04 08-11-20.ibt`).
const BOT_RUN: i64 = 1_791_126_680;
/// The sim session's clock started 4.7 s before that file.
const ZERO: f64 = BOT_RUN as f64 - 4.7;

/// A stint of an offline Test Drive: the file starts `clock` seconds into the sim session that
/// started at `zero` and lasts `length` seconds.
fn stint(zero: f64, clock: f64, length: f64, name: &str) -> Value {
    let date = (zero + clock).floor() as i64; // sessionStartDate has whole seconds
    json!({
        "customer_id": CUSTOMER, "user_name": "Joshua Granger", "irating": 1,
        "subsession_id": 0, "session_id": 0, "session_unique_id": 1, "sim_session_num": 0,
        "session_type": "Offline Testing", "track": "Lucas Oil Indianapolis Raceway Park", "track_id": 157,
        "track_config": "Oval", "car_path": "stockcars2 supra2019", "car_name": "Toyota Supra",
        "start": coach_server::util::iso_from_unix(date), "name": name,
        "session_start_date": date, "session_start_time": clock, "session_end_time": clock + length,
    })
}

/// `meta` driven on a Supra setup whose right-front camber is `camber` (the derived corner weight
/// and `UpdateCount` differ per file, as they do between stints of one setup).
fn on_setup(mut meta: Value, camber: &str, update_count: i64) -> Value {
    meta["setup"] = json!({
        "values": {"Chassis.RightFront.Camber": camber, "Chassis.LeftFront.CornerWeight": format!("{} N", 4000 + update_count),
                   "UpdateCount": update_count.to_string()},
        "name": "NOAPS_MaconiSetupShop IRP 26S4 R.sto", "is_modified": camber != "-3.3 deg",
        "load_type": "user", "update_count": update_count,
    });
    meta
}

/// `meta` whose last `Lap` sample is `lap`.
fn last_lap(mut meta: Value, lap: i64) -> Value {
    meta["lap_last"] = json!(lap);
    meta
}

async fn upload(env: &Env, meta: Value, file: &str) -> String {
    let bytes = fake_ibt(meta, 4_000);
    let reply = env.upload(&bytes, "ibt", file, None, 4096).await;
    assert!(
        reply.status.is_success(),
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    sha256_hex(&bytes)
}

fn count(env: &Env, sql: &str) -> i64 {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

/// `(session_id, recordings)` of every session, in id order.
fn sessions(env: &Env) -> Vec<(i64, i64)> {
    env.query(|c| {
        c.prepare(
            "SELECT s.session_id, (SELECT COUNT(*) FROM session_recordings sr WHERE sr.session_id = s.session_id)
             FROM sessions s ORDER BY s.session_id",
        )?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect()
    })
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
async fn two_stints_of_one_test_drive_join_one_session_in_either_order() {
    let env = Env::new().await;
    require_python!(env);
    // The bot run (28 min) and the in-stall stint 38 s after it, uploaded newest first as the
    // collector does; a later Test Drive of the same car and track; a hosted race.
    let in_stall = upload(&env, stint(ZERO, 1716.9, 38.0, "in stall"), "b.ibt").await;
    env.settle().await;
    let bot = upload(&env, stint(ZERO, 4.7, 1693.0, "bot run"), "a.ibt").await;
    env.settle().await;
    let next_drive = upload(
        &env,
        stint(ZERO + 7200.0, 30.0, 300.0, "next test drive"),
        "c.ibt",
    )
    .await;
    let race = upload(&env, race_meta(9811, 2, "race"), "d.ibt").await;
    env.settle().await;

    assert_eq!(
        session_of(&env, &bot),
        session_of(&env, &in_stall),
        "one sim session"
    );
    assert_ne!(
        session_of(&env, &next_drive),
        session_of(&env, &bot),
        "a new Test Drive"
    );
    assert_ne!(session_of(&env, &race), session_of(&env, &bot));
    assert_eq!(sessions(&env).len(), 3);
    let joined = session_of(&env, &bot);
    let start: String = env.query(move |c| {
        c.query_row(
            "SELECT start_time_utc FROM sessions WHERE session_id = ?1",
            [joined],
            |r| r.get(0),
        )
    });
    assert_eq!(
        start,
        coach_server::util::iso_from_unix(BOT_RUN),
        "the earlier stint's start"
    );

    // the session's analysis is of both recordings
    let members: Value = env.query(move |c| {
        c.query_row(
            "SELECT subject_json FROM jobs WHERE kind = 'analyze' AND json_extract(subject_json, '$.session_id') = ?1
             ORDER BY job_id DESC LIMIT 1",
            [joined],
            |r| Ok(serde_json::from_str::<Value>(&r.get::<_, String>(0)?).unwrap()),
        )
    });
    assert_eq!(members["recordings"].as_array().unwrap().len(), 2);
    let library = env.get("/api/v1/drivers/1001/sessions").await.json();
    let row = library["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == joined)
        .cloned()
        .expect("the joined session is listed");
    assert_eq!(row["recording_count"], 2, "{row}");

    // a recording discovered again stays where it is (its finished discovery goes back to failed
    // first: a finished job is not requeued)
    let before = sessions(&env);
    let again = bot.clone();
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "UPDATE jobs SET state = 'failed' WHERE idempotency_key = ?1",
                [format!("discover:{again}")],
            )?;
            coach_server::jobs::enqueue_tx(
                c,
                &coach_server::jobs::NewJob::discover(CUSTOMER, &again),
                true,
            )?;
            Ok(())
        })
        .unwrap();
    env.settle().await;
    assert_eq!(sessions(&env), before);
}

#[tokio::test]
async fn the_repair_merges_split_stints_once_and_reports_what_it_would_do() {
    let env = Env::new().await;
    require_python!(env);
    // An older server: the two stints were stored without the disk header's session time and
    // became two sessions (658 and 659); the repair first reads the time back from the archive.
    let bot = upload(&env, stint(ZERO, 4.7, 1693.0, "bot run"), "a.ibt").await;
    let in_stall = upload(&env, stint(ZERO, 1716.9, 38.0, "in stall"), "b.ibt").await;
    let other = upload(
        &env,
        stint(ZERO + 7200.0, 30.0, 300.0, "next test drive"),
        "c.ibt",
    )
    .await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE recordings SET header_json = json_remove(header_json, '$.session_start_time', '$.session_end_time')",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    env.settle().await;
    let (first, second) = (session_of(&env, &bot), session_of(&env, &in_stall));
    assert_ne!(first, second, "split as an older server split them");
    // a note on the second session, and the driver's last investigation there
    let analysis: String = env.query(move |c| {
        c.query_row(
            "SELECT current_analysis_id FROM sessions WHERE session_id = ?1",
            [second],
            |r| r.get(0),
        )
    });
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "INSERT INTO notes(customer_id, subject_kind, subject_id, body, created_at, updated_at)
                 VALUES (?1, 'session', ?2, 'stalled in the box', '2026-10-04T14:00:00Z', '2026-10-04T14:00:00Z')",
                rusqlite::params![CUSTOMER, second.to_string()],
            )?;
            c.execute(
                "INSERT INTO driver_prefs(customer_id, last_investigation_json, updated_at) VALUES (?1, ?2, '2026-10-04T14:00:00Z')",
                rusqlite::params![CUSTOMER, json!({"session_id": second, "analysis_id": analysis, "view": "race"}).to_string()],
            )?;
            Ok(())
        })
        .unwrap();

    let queue = coach_server::jobs::QueueParams::of(&env.app);
    let q = queue.clone();
    let report = env
        .app
        .store
        .write_blocking(move |c| Ok(offline_sessions::repair(c, &q, true)?))
        .unwrap();
    assert_eq!(report.recordings_without_session_time, 3);
    assert!(
        report.merges.is_empty(),
        "nothing to judge without the session time"
    );

    assert_eq!(offline_sessions::backfill(&env.app).await.unwrap(), 3);
    assert_eq!(
        offline_sessions::backfill(&env.app).await.unwrap(),
        0,
        "once"
    );
    let q = queue.clone();
    let dry = env
        .app
        .store
        .write_blocking(move |c| Ok(offline_sessions::repair(c, &q, true)?))
        .unwrap();
    assert_eq!(dry.recordings_checked, 3);
    assert_eq!(dry.recordings_without_session_time, 0);
    assert_eq!(dry.sessions_merged(), 1, "{dry:?}");
    assert_eq!(dry.merges[0].survivor, first);
    assert_eq!(dry.merges[0].merged, vec![second]);
    assert_eq!(sessions(&env).len(), 3, "a dry run changes nothing");

    offline_sessions::repair_on_start(&env.app).await;
    assert_eq!(session_of(&env, &in_stall), first);
    assert_ne!(session_of(&env, &other), first);
    assert_eq!(sessions(&env).len(), 2);
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM sessions WHERE session_id = {second}")
        ),
        0
    );
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM analyses WHERE session_id = {second}")
        ),
        0,
        "its analyses moved"
    );
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM notes WHERE subject_kind = 'session' AND subject_id = '{first}'"
            )
        ),
        1
    );
    let prefs: Value = env.query(|c| {
        c.query_row(
            "SELECT last_investigation_json FROM driver_prefs",
            [],
            |r| Ok(serde_json::from_str::<Value>(&r.get::<_, String>(0)?).unwrap()),
        )
    });
    assert_eq!(prefs["session_id"], first);
    let log =
        std::fs::read_to_string(env.data().join("run").join("offline-sessions.jsonl")).unwrap();
    assert_eq!(log.lines().count(), 1);

    // the survivor's analysis is queued for both recordings; the stub, like the worker, analyses
    // the one discovery group its key names and the job says so
    env.settle().await;
    let result: Value = env.query(move |c| {
        c.query_row(
            "SELECT result_json FROM jobs WHERE kind = 'analyze' AND state = 'succeeded'
               AND json_extract(subject_json, '$.session_id') = ?1
               AND json_array_length(json_extract(subject_json, '$.recordings')) = 2",
            [first],
            |r| Ok(serde_json::from_str::<Value>(&r.get::<_, String>(0)?).unwrap()),
        )
    });
    assert_eq!(result["recordings_not_analysed"], 1, "{result}");

    // idempotent
    let q = queue.clone();
    let again = env
        .app
        .store
        .write_blocking(move |c| Ok(offline_sessions::repair(c, &q, false)?))
        .unwrap();
    assert!(again.merges.is_empty(), "{again:?}");
    offline_sessions::repair_on_start(&env.app).await;
    let log =
        std::fs::read_to_string(env.data().join("run").join("offline-sessions.jsonl")).unwrap();
    assert_eq!(log.lines().count(), 1, "an unchanged start writes no line");
}

#[tokio::test]
async fn a_setup_change_between_stints_starts_a_session_of_its_own() {
    let env = Env::new().await;
    require_python!(env);
    // One Test Drive: a baseline stint, a stint on a camber change, then the baseline again,
    // uploaded newest first.
    let back = upload(
        &env,
        on_setup(stint(ZERO, 3600.0, 600.0, "baseline again"), "-3.3 deg", 9),
        "c.ibt",
    )
    .await;
    env.settle().await;
    let change = upload(
        &env,
        on_setup(stint(ZERO, 1800.0, 600.0, "camber"), "-3.8 deg", 5),
        "b.ibt",
    )
    .await;
    env.settle().await;
    let base = upload(
        &env,
        on_setup(stint(ZERO, 4.7, 600.0, "baseline"), "-3.3 deg", 1),
        "a.ibt",
    )
    .await;
    env.settle().await;

    assert_eq!(
        session_of(&env, &base),
        session_of(&env, &back),
        "the baseline's stints"
    );
    assert_ne!(
        session_of(&env, &change),
        session_of(&env, &base),
        "a setup change"
    );
    assert_eq!(sessions(&env).len(), 2);
    // the camber session is still an offline stint of the Test Drive: a later stint on that setup
    // joins it
    let change_again = upload(
        &env,
        on_setup(stint(ZERO, 5000.0, 300.0, "camber again"), "-3.8 deg", 11),
        "d.ibt",
    )
    .await;
    env.settle().await;
    assert_eq!(session_of(&env, &change_again), session_of(&env, &change));
    assert_eq!(sessions(&env).len(), 2);
    let setups = count(
        &env,
        "SELECT COUNT(DISTINCT setup_id) FROM setup_observations WHERE source = 'recording'",
    );
    assert_eq!(
        setups, 2,
        "the corner weight and UpdateCount are no setup change"
    );
}

#[tokio::test]
async fn a_recording_whose_lap_counter_stopped_joins_nothing_and_its_analysis_is_deferred() {
    let env = Env::new().await;
    require_python!(env);
    // The 2026-09-25 IRP Test Drive: a stint on lap 145, then, 11 h later on the same setup, a
    // stint the sim recorded with Lap 1024 throughout.
    let counted = upload(
        &env,
        last_lap(
            on_setup(stint(ZERO, 11_484.3, 280.3, "lap 145"), "-3.3 deg", 1),
            145,
        ),
        "a.ibt",
    )
    .await;
    env.settle().await;
    let stopped = upload(
        &env,
        last_lap(
            on_setup(stint(ZERO, 52_783.4, 449.7, "lap 1024"), "-3.3 deg", 5),
            1024,
        ),
        "b.ibt",
    )
    .await;
    env.settle().await;
    let (good, bad) = (session_of(&env, &counted), session_of(&env, &stopped));
    assert_ne!(good, bad, "never joined");
    let state = |id: i64| -> (String, Option<String>) {
        env.query(move |c| {
            c.query_row(
                "SELECT analysis_state, analysis_reason FROM sessions WHERE session_id = ?1",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
        })
    };
    assert_eq!(
        state(bad),
        (
            "deferred".to_string(),
            Some("lap_counter_stopped".to_string())
        )
    );
    assert_eq!(state(good).0, "ready");
    let message: String = env.query(move |c| {
        c.query_row(
            "SELECT error_message FROM jobs WHERE kind = 'analyze' AND state = 'deferred'
               AND json_extract(subject_json, '$.session_id') = ?1",
            [bad],
            |r| r.get(0),
        )
    });
    assert!(
        message.contains("lap counter had stopped at lap 1024") && message.contains("b.ibt"),
        "{message}"
    );
    // nothing joins it either: a later stint of the same setup and sim session joins the counted
    // stint's session
    let later = upload(
        &env,
        last_lap(
            on_setup(stint(ZERO, 12_000.0, 200.0, "lap 160"), "-3.3 deg", 2),
            160,
        ),
        "c.ibt",
    )
    .await;
    env.settle().await;
    assert_eq!(session_of(&env, &later), good);
    // the repair counts it and merges nothing
    let queue = coach_server::jobs::QueueParams::of(&env.app);
    let report = env
        .app
        .store
        .write_blocking(move |c| Ok(offline_sessions::repair(c, &queue, true)?))
        .unwrap();
    assert_eq!(report.recordings_lap_counter_stopped, 1);
    assert!(report.merges.is_empty(), "{report:?}");
}

#[tokio::test]
async fn the_repair_merges_stints_of_one_setup_only_and_reports_a_mixed_session() {
    let env = Env::new().await;
    require_python!(env);
    // An older server split all three stints; the middle one was driven on another setup.
    let base = upload(
        &env,
        on_setup(stint(ZERO, 4.7, 600.0, "baseline"), "-3.3 deg", 1),
        "a.ibt",
    )
    .await;
    let change = upload(
        &env,
        on_setup(stint(ZERO, 1800.0, 600.0, "camber"), "-3.8 deg", 5),
        "b.ibt",
    )
    .await;
    let back = upload(
        &env,
        on_setup(stint(ZERO, 3600.0, 600.0, "baseline again"), "-3.3 deg", 9),
        "c.ibt",
    )
    .await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE recordings SET header_json = json_remove(header_json, '$.session_start_time', '$.session_end_time')",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    env.settle().await;
    let (a, b, c) = (
        session_of(&env, &base),
        session_of(&env, &change),
        session_of(&env, &back),
    );
    assert_eq!(sessions(&env).len(), 3);
    assert_eq!(offline_sessions::backfill(&env.app).await.unwrap(), 3);

    let queue = coach_server::jobs::QueueParams::of(&env.app);
    let q = queue.clone();
    let dry = env
        .app
        .store
        .write_blocking(move |conn| Ok(offline_sessions::repair(conn, &q, true)?))
        .unwrap();
    assert_eq!(dry.merges.len(), 1, "{dry:?}");
    assert_eq!(
        (dry.merges[0].survivor, dry.merges[0].merged.clone()),
        (a, vec![c])
    );
    assert!(dry.merges[0].setup_fingerprint.is_some());
    assert!(dry.sessions_with_mixed_stints.is_empty());
    offline_sessions::repair_on_start(&env.app).await;
    assert_eq!(session_of(&env, &back), a);
    assert_eq!(session_of(&env, &change), b, "another setup stays apart");

    // A session the rule before setups joined across the setup change (the camber stint moved
    // into the baseline's session) is reported and left alone: a later baseline stint does not
    // join it at discovery, and the repair merges nothing into it.
    let change_sha = change.clone();
    env.app
        .store
        .write_blocking(move |conn| {
            conn.execute(
                "UPDATE session_recordings SET session_id = ?1 WHERE recording_sha256 = ?2",
                rusqlite::params![a, change_sha],
            )?;
            Ok(())
        })
        .unwrap();
    let extra = upload(
        &env,
        on_setup(stint(ZERO, 5000.0, 300.0, "baseline later"), "-3.3 deg", 12),
        "d.ibt",
    )
    .await;
    env.settle().await;
    let extra_session = session_of(&env, &extra);
    assert_ne!(
        extra_session, a,
        "no stint joins a session holding two setups"
    );
    let q = queue.clone();
    let report = env
        .app
        .store
        .write_blocking(move |conn| Ok(offline_sessions::repair(conn, &q, false)?))
        .unwrap();
    assert_eq!(report.sessions_with_mixed_stints, vec![a], "{report:?}");
    assert!(report.merges.is_empty(), "{report:?}");
    assert_eq!(session_of(&env, &extra), extra_session);
}

fn key() -> StintKey {
    StintKey {
        customer_id: 1_507_810,
        car_path: "stockcars2 supra2019".into(),
        track: "1".into(),
        track_config: "Oval".into(),
        session_num: Some(0),
        session_unique_id: Some(1),
        session_type: "Offline Testing".into(),
    }
}

fn s(sha: &str, date: f64, clock: f64, end_clock: f64) -> Stint {
    Stint {
        sha256: sha.into(),
        key: key(),
        session_zero: date - clock,
        start: date,
        end: date + (end_clock - clock),
        setup: Some("baseline".into()),
    }
}

/// A stint driven on setup `setup`.
fn on(setup: &str, mut stint: Stint) -> Stint {
    stint.setup = Some(setup.into());
    stint
}

#[test]
fn the_dev_pc_recordings_group_as_their_sim_sessions() {
    // Disk headers of the dev PC's offline recordings (sessionStartDate, sessionStartTime,
    // sessionEndTime): the two rig stints of 2026-10-03 at Las Vegas are one Test Drive; the two
    // Iowa test drives of 2026-08-03 are two.
    let vegas_a = s("vegas 09-01-49", 1_791_036_109.0, 401.28, 474.77);
    let vegas_b = s("vegas 09-08-36", 1_791_036_516.0, 807.67, 837.82);
    assert!(offline_sessions::same_session(&vegas_a, &vegas_b));
    let iowa_a = s("iowa 20-59-41", 1_785_808_781.0, 26.58, 233.02);
    let iowa_b = s("iowa 22-27-43", 1_785_814_063.0, 55.52, 738.48);
    assert!(!offline_sessions::same_session(&iowa_a, &iowa_b));
    let groups = offline_sessions::group(vec![iowa_b, vegas_b, iowa_a, vegas_a]);
    let sizes: Vec<usize> = groups.iter().map(Vec::len).collect();
    assert_eq!(sizes, vec![1, 1, 2]);
    // another car, track configuration or session type is another session
    let mut other = s("other", 1_791_036_516.0, 807.67, 837.82);
    other.key.track_config = "Road".into();
    assert!(!offline_sessions::same_session(
        &s("x", 1_791_036_109.0, 401.28, 474.77),
        &other
    ));
    // overlapping files are not stints of one session
    assert!(!offline_sessions::same_session(
        &s("x", 1_791_036_109.0, 401.28, 474.77),
        &s("y", 1_791_036_110.0, 402.28, 475.77)
    ));
}

#[test]
fn stints_join_only_on_one_setup() {
    // The dev PC's copies, disk headers and setup fingerprints (`coach_ibt`): the IRP Test Drive's
    // three stints of 2026-09-25 (laps 93-99, 111-114, 137-145; one setup, the corner weights and
    // UpdateCount aside) are one session.
    let irp = |name: &str, date: f64, clock: f64, end: f64| {
        on(
            "f25d5d1ddaa54a97976ac74166c12848",
            s(name, date, clock, end),
        )
    };
    let irp_a = irp("irp 17-56-25", 1_790_376_985.0, 8_115.10, 8_312.27);
    let irp_b = irp("irp 18-21-40", 1_790_378_500.0, 9_630.28, 9_760.77);
    let irp_c = irp("irp 18-52-34", 1_790_380_354.0, 11_484.28, 11_764.60);
    // The Las Vegas rig stints of 2026-10-03: one sim session, but the garage test changed the
    // left-front cold pressure from 155 to 152 kPa between them (`acceptance/g3-garage.md`).
    let vegas_a = on(
        "40b93ec4f126145312fdb45e3b820c44",
        s("vegas 09-01-49", 1_791_036_109.0, 401.28, 474.77),
    );
    let vegas_b = on(
        "b6362bcd8c6973cea37067f10659e33a",
        s("vegas 09-08-36", 1_791_036_516.0, 807.67, 837.82),
    );
    assert!(offline_sessions::same_session(&vegas_a, &vegas_b));
    assert!(!offline_sessions::joins(&vegas_a, &vegas_b));
    assert!(offline_sessions::joins(&irp_a, &irp_c));
    let groups = offline_sessions::group(vec![
        vegas_b.clone(),
        irp_c.clone(),
        vegas_a.clone(),
        irp_a.clone(),
        irp_b.clone(),
    ]);
    let names: Vec<Vec<&str>> = groups
        .iter()
        .map(|g| g.iter().map(|s| s.sha256.as_str()).collect())
        .collect();
    assert_eq!(
        names,
        vec![
            vec!["irp 17-56-25", "irp 18-21-40", "irp 18-52-34"],
            vec!["vegas 09-01-49"],
            vec!["vegas 09-08-36"],
        ]
    );
    // A setup, another setup, the first again: the first setup's stints are one session, the
    // other its own; neither a recording without a setup.
    let one = on("x", s("1", 1_000_000.0, 10.0, 100.0));
    let two = on("y", s("2", 1_000_100.0, 110.0, 200.0));
    let three = on("x", s("3", 1_000_200.0, 210.0, 300.0));
    let mut unknown = s("4", 1_000_300.0, 310.0, 400.0);
    unknown.setup = None;
    let groups: Vec<Vec<String>> = offline_sessions::group(vec![one, two, three, unknown])
        .iter()
        .map(|g| g.iter().map(|s| s.sha256.clone()).collect())
        .collect();
    assert_eq!(groups, vec![vec!["1", "3"], vec!["2"], vec!["4"]]);
}

#[test]
fn a_recording_whose_lap_counter_stopped_is_no_stint() {
    let header = |lap: Value| {
        json!({"session_start_date": 1_790_421_653i64, "session_start_time": 52_783.43, "lap_last": lap,
            "summary": {"subsession_id": 0, "session_id": 0, "time_source": "disk_header",
                        "start_time_unix": 1_790_421_653.0, "end_time_unix": 1_790_422_102.7,
                        "track_id": 232, "car_path": "stockcars2 supra2019",
                        "sim_session_num": 0, "session_unique_id": 1, "sim_session_type": "Offline Testing"}})
    };
    for (lap, stint) in [
        (json!(1024), false),
        (json!(1025), false),
        (json!(1023), true),
        (Value::Null, true),
    ] {
        let h = header(lap.clone());
        assert_eq!(
            offline_sessions::lap_counter_stopped(&h),
            !stint,
            "Lap {lap}"
        );
        assert_eq!(
            offline_sessions::stint_of("a", 1, &h, Some("f25d")).is_some(),
            stint,
            "Lap {lap}"
        );
    }
    assert_eq!(offline_sessions::LAP_COUNTER_LIMIT, 1024);
}

#[test]
fn hosted_and_file_timed_recordings_are_not_stints() {
    let offline = json!({"session_start_date": 1_791_036_109i64, "session_start_time": 401.28,
        "summary": {"subsession_id": 0, "session_id": 0, "time_source": "disk_header",
                    "start_time_unix": 1_791_036_109.0, "end_time_unix": 1_791_036_182.5,
                    "track_id": 1, "car_path": "stockcars2 supra2019", "track_config_name": "Oval",
                    "sim_session_num": 0, "session_unique_id": 1, "sim_session_type": "Offline Testing"}});
    let stint = offline_sessions::stint_of("a", 1, &offline, None).expect("a stint");
    assert!((stint.session_zero - (1_791_036_109.0 - 401.28)).abs() < 1e-6);
    let mut hosted = offline.clone();
    hosted["summary"]["subsession_id"] = json!(87_697_310);
    assert!(offline_sessions::stint_of("a", 1, &hosted, None).is_none());
    let mut server_session = offline.clone();
    server_session["summary"]["session_id"] = json!(317_639_925);
    assert!(offline_sessions::stint_of("a", 1, &server_session, None).is_none());
    let mut by_mtime = offline.clone();
    by_mtime["summary"]["time_source"] = json!("mtime");
    assert!(offline_sessions::stint_of("a", 1, &by_mtime, None).is_none());
    let mut old_row = offline.clone();
    old_row
        .as_object_mut()
        .unwrap()
        .remove("session_start_time");
    assert!(offline_sessions::stint_of("a", 1, &old_row, None).is_none());
}

/// How many sessions the repair would merge over real recordings: every `.ibt` under the
/// directories in `COACH_OFFLINE_CORPUS` (separated by `;`), read with the real parser. Prints the
/// groups; skipped when the variable is not set (the recordings are private).
#[test]
fn corpus_report() {
    let Ok(dirs) = std::env::var("COACH_OFFLINE_CORPUS") else {
        eprintln!("skipped: COACH_OFFLINE_CORPUS not set");
        return;
    };
    use coach_server::adapters::RecordingParser;
    let parser = coach_server::adapters::IbtParser;
    let (mut files, mut stopped, mut stints) = (0, 0, Vec::new());
    for dir in dirs.split(';').filter(|d| !d.is_empty()) {
        for entry in std::fs::read_dir(dir).expect("a directory").flatten() {
            let path = entry.path();
            if !path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("ibt"))
            {
                continue;
            }
            files += 1;
            let Ok(parsed) = parser.parse(&path) else {
                continue;
            };
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let setup = parsed.setup.as_ref().map(|s| s.fingerprint.clone());
            if offline_sessions::lap_counter_stopped(&parsed.header_json) {
                stopped += 1;
                eprintln!(
                    "lap counter stopped (Lap {}): {name} setup {setup:?}",
                    parsed.header_json["lap_last"]
                );
            }
            if let Some(stint) = offline_sessions::stint_of(
                &name,
                parsed.customer_id.unwrap_or(0),
                &parsed.header_json,
                setup.as_deref(),
            ) {
                stints.push(stint);
            }
        }
    }
    let offline = stints.len();
    // sim sessions: the same grouping with the setup left out
    let sim_sessions = offline_sessions::group(
        stints
            .iter()
            .cloned()
            .map(|mut s| {
                s.setup = None;
                s
            })
            .collect(),
    )
    .len();
    let groups = offline_sessions::group(stints);
    let merged: usize = groups.iter().map(|g| g.len() - 1).sum();
    for g in groups.iter().filter(|g| g.len() > 1) {
        let names: Vec<&str> = g.iter().map(|s| s.sha256.as_str()).collect();
        eprintln!("one session (setup {:?}): {names:?}", g[0].setup);
    }
    for g in groups.iter().filter(|g| g.len() == 1) {
        eprintln!("alone (setup {:?}): {:?}", g[0].setup, g[0].sha256.as_str());
    }
    eprintln!(
        "corpus: {files} recordings, {offline} offline stints, {stopped} with a stopped lap counter, {sim_sessions} sim sessions, {} library sessions, {merged} sessions would merge",
        groups.len()
    );
}
