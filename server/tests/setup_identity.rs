//! Setup identity at start-up (`setup-keys.json` version 2, `src/setup_identity.rs`): setups stored
//! under the version-1 fingerprints are merged when the new rule calls them one, into the named
//! setup; observations, names, file links and notes follow; setups without an observation go; the
//! log says what happened, and a second start changes nothing. Also the name of a setup known only
//! from a garage `.htm` export.

mod support;

use std::collections::{BTreeMap, BTreeSet};

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};
use support::*;

const SUPRA: &str = "stockcars2 supra2019";
const OTHER_CAR: &str = "stockcars chevyss2020";
const PREVIOUS: &str = "claude LV R CAM.sto";
const ATLANTA: &str = "NOAPS_MaconiSetupShop AtlantaSS 26S3 Q.sto";
const PILOT: &str = "c8c4f683-2128-4488-9094-ff9b1a605523";

fn sha(tag: &str) -> String {
    coach_server::util::sha256_hex(tag.as_bytes())
}

fn map(values: &Value) -> BTreeMap<String, String> {
    values
        .as_object()
        .expect("an object")
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().expect("a string").to_string()))
        .collect()
}

/// `values` with some keys replaced.
fn with(values: &Value, changes: &[(&str, &str)]) -> Value {
    let mut out = values.clone();
    for (k, v) in changes {
        out[*k] = json!(v);
    }
    out
}

/// The version-1 rule: the bare `Preload` leaf counted, and a key's underscores were kept.
fn old_fingerprint(values: &BTreeMap<String, String>) -> String {
    let excluded = |leaf: &str| {
        let leaf = leaf.to_ascii_lowercase();
        matches!(
            leaf.as_str(),
            "cornerweight" | "rideheight" | "arbpreload" | "truckarmpreload" | "updatecount"
        ) || ["lasthotpressure", "lasttemps", "treadremaining"]
            .iter()
            .any(|p| leaf.starts_with(p))
    };
    let mut text = String::new();
    for (k, v) in values {
        if !excluded(k.rsplit('.').next().unwrap_or(k)) {
            text.push_str(&format!("{k}\u{1f}{}\u{1e}", v.trim()));
        }
    }
    coach_server::util::sha256_hex(text.as_bytes())[..32].to_string()
}

// ------------------------------------------------------------------ seeding

/// One setup row as the version-1 server stored it.
struct Seed<'a> {
    id: i64,
    car: &'a str,
    values: &'a Value,
    first: &'a str,
    last: &'a str,
}

fn setup(c: &Connection, s: Seed<'_>) -> rusqlite::Result<()> {
    let values = map(s.values);
    c.execute(
        "INSERT INTO setups(setup_id, customer_id, car_path, values_fingerprint, values_json, first_seen_at, last_seen_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            s.id,
            CUSTOMER,
            s.car,
            old_fingerprint(&values),
            serde_json::to_string(&values).unwrap(),
            s.first,
            s.last
        ],
    )?;
    Ok(())
}

fn supra<'a>(id: i64, values: &'a Value, first: &'a str, last: &'a str) -> Seed<'a> {
    Seed {
        id,
        car: SUPRA,
        values,
        first,
        last,
    }
}

fn visit(c: &Connection, id: &str, committed: Option<(&str, &str)>) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO garage_visits(visit_id, collector_id, customer_id, car_path, committed_name, committed_sto_sha256)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            id,
            COLLECTOR,
            CUSTOMER,
            SUPRA,
            committed.map(|c| c.0),
            committed.map(|c| c.1)
        ],
    )?;
    Ok(())
}

fn snapshot(c: &Connection, setup: i64, visit: &str, count: i64, at: &str) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO setup_observations(setup_id, customer_id, source, visit_id, update_count, observed_at)
         VALUES (?1, ?2, 'garage_snapshot', ?3, ?4, ?5)",
        params![setup, CUSTOMER, visit, count, at],
    )?;
    Ok(())
}

/// An unmodified garage commit naming `name` with its hash.
fn commit(
    c: &Connection,
    setup: i64,
    visit: &str,
    count: i64,
    file: (&str, &str),
    at: &str,
) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO setup_observations(setup_id, customer_id, source, visit_id, setup_name, sto_sha256, is_modified,
           load_type, update_count, observed_at)
         VALUES (?1, ?2, 'garage_commit', ?3, ?4, ?5, 0, 'user', ?6, ?7)",
        params![setup, CUSTOMER, visit, file.0, file.1, count, at],
    )?;
    Ok(())
}

/// A recording observation of a modified load (setup 11's shape: the P5 and G3 garage tests).
fn modified_recording(c: &Connection, setup: i64, tag: &str, at: &str) -> rusqlite::Result<()> {
    let recording = sha(tag);
    c.execute(
        "INSERT INTO blobs(sha256, size, kind, first_name, received_at) VALUES (?1, 100, 'ibt', ?2, ?3)",
        params![recording, format!("{tag}.ibt"), at],
    )?;
    c.execute(
        "INSERT INTO recordings(sha256, customer_id, ingest_state, car_path) VALUES (?1, ?2, 'parsed', ?3)",
        params![recording, CUSTOMER, SUPRA],
    )?;
    c.execute(
        "INSERT INTO setup_observations(setup_id, customer_id, source, recording_sha256, setup_name, is_modified,
           load_type, update_count, observed_at)
         VALUES (?1, ?2, 'recording', ?3, ?4, 1, 'user', 10, ?5)",
        params![setup, CUSTOMER, recording, PREVIOUS, at],
    )?;
    Ok(())
}

/// A file entry of the Supra folder and its blob.
fn file(c: &Connection, blob: &str, relative_path: &str, format: &str) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO blobs(sha256, size, kind, first_name, received_at) VALUES (?1, 100, ?2, ?3, '2026-10-03T15:41:00Z')",
        params![blob, format, relative_path.rsplit('/').next().unwrap()],
    )?;
    c.execute(
        "INSERT INTO setup_files(customer_id, blob_sha256, car_folder, relative_path, format, first_seen_at, last_seen_at)
         VALUES (?1, ?2, ?3, ?4, ?5, '2026-10-03T15:41:00Z', '2026-10-03T15:41:00Z')",
        params![CUSTOMER, blob, SUPRA, relative_path, format],
    )?;
    Ok(())
}

fn export(c: &Connection, setup: i64, blob: &str, at: &str) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO setup_observations(setup_id, customer_id, source, blob_sha256, observed_at)
         VALUES (?1, ?2, 'htm_export', ?3, ?4)",
        params![setup, CUSTOMER, blob, at],
    )?;
    Ok(())
}

fn note(c: &Connection, setup: i64, body: &str) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO notes(customer_id, subject_kind, subject_id, body, created_at, updated_at)
         VALUES (?1, 'setup', ?2, ?3, '2026-10-04T05:00:00Z', '2026-10-04T05:00:00Z')",
        params![CUSTOMER, setup.to_string(), body],
    )?;
    Ok(())
}

fn seed(env: &Env, f: impl FnOnce(&Connection) -> rusqlite::Result<()> + Send + 'static) {
    env.app
        .store
        .write_blocking(move |c| {
            let tx = c.transaction()?;
            tx.execute(
                "INSERT OR IGNORE INTO drivers(customer_id, display_name, first_seen_at, last_seen_at)
                 VALUES (?1, 'Test Driver', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z')",
                [CUSTOMER],
            )?;
            tx.execute(
                "INSERT OR IGNORE INTO collectors(collector_id, machine_name, collector_version, protocol, first_seen_at, last_seen_at)
                 VALUES (?1, 'TEST', '0', 1, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z')",
                [COLLECTOR],
            )?;
            f(&tx)?;
            tx.commit()?;
            Ok(())
        })
        .expect("seed");
}

// ------------------------------------------------------------------ reading

/// A setup as stored, with its observations (`source:update_count`, in insertion order).
#[derive(Debug, Clone, PartialEq)]
struct Row {
    id: i64,
    fingerprint: String,
    values: Value,
    first_seen_at: String,
    last_seen_at: String,
    observations: Vec<String>,
}

fn rows(env: &Env) -> Vec<Row> {
    env.query(|c| {
        let mut out: Vec<Row> = c
            .prepare(
                "SELECT setup_id, values_fingerprint, values_json, first_seen_at, last_seen_at FROM setups
                 ORDER BY setup_id",
            )?
            .query_map([], |r| {
                Ok(Row {
                    id: r.get(0)?,
                    fingerprint: r.get(1)?,
                    values: serde_json::from_str(&r.get::<_, String>(2)?).unwrap_or(Value::Null),
                    first_seen_at: r.get(3)?,
                    last_seen_at: r.get(4)?,
                    observations: Vec::new(),
                })
            })?
            .collect::<rusqlite::Result<_>>()?;
        for row in &mut out {
            row.observations = c
                .prepare(
                    "SELECT source || ':' || COALESCE(update_count, '') FROM setup_observations
                     WHERE setup_id = ?1 ORDER BY observation_id",
                )?
                .query_map([row.id], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
        }
        Ok(out)
    })
}

fn row(env: &Env, id: i64) -> Row {
    rows(env)
        .into_iter()
        .find(|r| r.id == id)
        .unwrap_or_else(|| panic!("setup {id}"))
}

fn ids(env: &Env) -> Vec<i64> {
    rows(env).into_iter().map(|r| r.id).collect()
}

fn sorted_observations(env: &Env, id: i64) -> Vec<String> {
    let mut observations = row(env, id).observations;
    observations.sort();
    observations
}

fn count(env: &Env, sql: &str) -> i64 {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

fn log_lines(env: &Env) -> Vec<Value> {
    std::fs::read_to_string(env.data().join("run").join("setup-identity.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).expect("a JSON line"))
        .collect()
}

async fn library(env: &Env) -> Vec<Value> {
    let page = env
        .get("/api/v1/drivers/1001/setups?car_path=stockcars2%20supra2019")
        .await
        .json();
    page["setups"].as_array().expect("setups").clone()
}

fn by_id(setups: &[Value], id: i64) -> &Value {
    setups
        .iter()
        .find(|s| s["setup_id"] == id)
        .unwrap_or_else(|| panic!("setup {id} in {setups:?}"))
}

// -------------------------------------------------------------------- tests

#[tokio::test]
async fn garage_snapshots_join_the_setup_their_visit_named_and_a_second_start_changes_nothing() {
    let env = Env::new().await;
    // The car's previous setup (10, named by two unmodified commits) and the harvest pilot's visit:
    // its garage-entry snapshot (69: the previous values republished), its load snapshot (70) and
    // its commit (71, with the visit's last snapshot). The snapshots differ from the committed
    // values only in the bar preload and a corner weight.
    let previous = json!({
        "Chassis.FrontArb.LinkSlack": "-2 mm", "Chassis.FrontArb.Preload": "-7.6 Nm",
        "Chassis.LeftFront.CornerWeight": "4510 N", "Chassis.RightRear.TruckArmPreload": "16.2 Nm",
        "Tires.LeftFront.ColdPressure": "152 kPa", "UpdateCount": "8",
    });
    let entry = with(
        &previous,
        &[
            ("Chassis.FrontArb.Preload", "-7.4 Nm"),
            ("Chassis.LeftFront.CornerWeight", "4509 N"),
            ("UpdateCount", "3"),
        ],
    );
    let atlanta = json!({
        "Chassis.FrontArb.LinkSlack": "-0 mm", "Chassis.FrontArb.Preload": "-7.0 Nm",
        "Chassis.LeftFront.CornerWeight": "4602 N", "Chassis.RightRear.TruckArmPreload": "12.0 Nm",
        "Tires.LeftFront.ColdPressure": "207 kPa", "UpdateCount": "5",
    });
    let load = with(
        &atlanta,
        &[
            ("Chassis.FrontArb.Preload", "-7.1 Nm"),
            ("UpdateCount", "4"),
        ],
    );
    // The P5 recording: the previous file with an unsaved pressure edit, a setup of its own.
    let edited = with(
        &previous,
        &[
            ("Tires.LeftFront.ColdPressure", "155 kPa"),
            ("Chassis.FrontArb.Preload", "-7.5 Nm"),
        ],
    );
    let other_car = json!({
        "Chassis.Front.ArbSetting": "4", "Chassis.LeftFront.CornerWeight": "1700 N", "UpdateCount": "3",
    });
    for (snapshot, committed) in [(&entry, &previous), (&load, &atlanta)] {
        let (a, b) = (
            old_fingerprint(&map(snapshot)),
            old_fingerprint(&map(committed)),
        );
        assert_ne!(a, b, "version 1 kept them apart");
        assert_eq!(
            fake_fingerprint(&map(snapshot)),
            fake_fingerprint(&map(committed))
        );
    }

    let (prev_sto, atlanta_sto) = (sha("previous sto"), sha("atlanta sto"));
    let (p, e, a, l, ed, o) = (
        previous.clone(),
        entry.clone(),
        atlanta.clone(),
        load,
        edited,
        other_car,
    );
    let (ps, at) = (prev_sto.clone(), atlanta_sto.clone());
    seed(&env, move |c| {
        let (prev_file, atlanta_file) = ((PREVIOUS, ps.as_str()), (ATLANTA, at.as_str()));
        file(c, &ps, PREVIOUS, "sto")?;
        file(c, &at, ATLANTA, "sto")?;
        setup(
            c,
            supra(10, &p, "2026-09-28T15:23:02Z", "2026-10-04T04:13:23Z"),
        )?;
        visit(c, "v-earlier", Some(prev_file))?;
        commit(c, 10, "v-earlier", 8, prev_file, "2026-09-28T15:23:02Z")?;
        visit(c, "v-session-start", Some(prev_file))?;
        commit(
            c,
            10,
            "v-session-start",
            2,
            prev_file,
            "2026-10-04T04:13:23Z",
        )?;
        setup(
            c,
            supra(11, &ed, "2026-10-02T23:18:02Z", "2026-10-02T23:18:02Z"),
        )?;
        modified_recording(c, 11, "p5 recording", "2026-10-02T23:18:02Z")?;
        visit(c, PILOT, Some(atlanta_file))?;
        let entered = "2026-10-04T04:13:52.457Z";
        setup(c, supra(69, &e, entered, entered))?;
        snapshot(c, 69, PILOT, 3, entered)?;
        let loaded = "2026-10-04T04:16:55.465Z";
        setup(c, supra(70, &l, loaded, loaded))?;
        snapshot(c, 70, PILOT, 4, loaded)?;
        setup(
            c,
            supra(
                71,
                &a,
                "2026-10-04T04:17:26.921Z",
                "2026-10-04T04:17:27.500Z",
            ),
        )?;
        commit(c, 71, PILOT, 5, atlanta_file, "2026-10-04T04:17:26.921Z")?;
        snapshot(c, 71, PILOT, 5, "2026-10-04T04:17:27.500Z")?;
        // another car, whose fingerprint the new rule leaves as it was
        let august = "2026-08-01T12:00:00Z";
        let other = Seed {
            id: 80,
            car: OTHER_CAR,
            values: &o,
            first: august,
            last: august,
        };
        setup(c, other)?;
        visit(c, "v-other", None)?;
        snapshot(c, 80, "v-other", 3, august)?;
        // setups without an observation: one goes, the one with a note stays
        let october = "2026-10-01T00:00:00Z";
        let (slack5, slack6) = (
            json!({"Chassis.FrontArb.LinkSlack": "-5 mm"}),
            json!({"Chassis.FrontArb.LinkSlack": "-6 mm"}),
        );
        setup(c, supra(90, &slack5, october, october))?;
        setup(c, supra(91, &slack6, october, october))?;
        note(c, 69, "entry snapshot note")?;
        note(c, 70, "load snapshot note")?;
        note(c, 91, "a setup I want to keep")?;
        Ok(())
    });
    let before = rows(&env);
    let observations = count(&env, "SELECT COUNT(*) FROM setup_observations");
    let generation_sql = "SELECT value FROM server_counters WHERE name = 'user_data_generation'";
    let generation = count(&env, generation_sql);

    let env = env.restart().await;
    assert_eq!(
        ids(&env),
        vec![10, 11, 71, 80, 91],
        "69 joined 10, 70 joined 71, 90 had nothing"
    );
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setup_observations"),
        observations,
        "no observation lost"
    );
    assert_eq!(
        sorted_observations(&env, 10),
        vec!["garage_commit:2", "garage_commit:8", "garage_snapshot:3"]
    );
    assert_eq!(
        sorted_observations(&env, 71),
        vec!["garage_commit:5", "garage_snapshot:4", "garage_snapshot:5"]
    );
    assert_eq!(
        sorted_observations(&env, 11),
        vec!["recording:10"],
        "a modified load stays a setup of its own"
    );

    // fingerprints follow the version-2 rule; the other car's row did not change at all
    for r in rows(&env) {
        assert_eq!(
            r.fingerprint,
            fake_fingerprint(&map(&r.values)),
            "setup {}",
            r.id
        );
    }
    let untouched = before.iter().find(|r| r.id == 80).cloned();
    assert_eq!(Some(row(&env, 80)), untouched);

    // a survivor spans its group and keeps the latest values
    let survivor = row(&env, 71);
    assert_eq!(
        (
            survivor.first_seen_at.as_str(),
            survivor.last_seen_at.as_str()
        ),
        ("2026-10-04T04:16:55.465Z", "2026-10-04T04:17:27.500Z")
    );
    assert_eq!(survivor.values, atlanta);
    let previous_row = row(&env, 10);
    assert_eq!(
        (
            previous_row.first_seen_at.as_str(),
            previous_row.last_seen_at.as_str()
        ),
        ("2026-09-28T15:23:02Z", "2026-10-04T04:13:52.457Z")
    );
    assert_eq!(
        previous_row.values, entry,
        "the garage-entry snapshot was seen last"
    );

    // notes follow their setup; the kept setup keeps its note
    let notes: Vec<(String, String)> = env.query(|c| {
        c.prepare("SELECT subject_id, body FROM notes ORDER BY note_id")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect()
    });
    let expected: Vec<(String, String)> = [
        ("10", "entry snapshot note"),
        ("71", "load snapshot note"),
        ("91", "a setup I want to keep"),
    ]
    .iter()
    .map(|(s, b)| (s.to_string(), b.to_string()))
    .collect();
    assert_eq!(notes, expected);
    assert_eq!(
        count(&env, generation_sql),
        generation + 1,
        "moving a note is a user-data change"
    );

    // the library: the named setups carry their names, sightings and files; no snapshot row left
    let setups = library(&env).await;
    assert_eq!(by_id(&setups, 71)["names"], json!([ATLANTA]));
    assert_eq!(by_id(&setups, 71)["observation_count"], 3);
    assert_eq!(
        by_id(&setups, 71)["sto_files"][0]["sha256"],
        atlanta_sto.as_str()
    );
    assert_eq!(by_id(&setups, 10)["names"], json!([PREVIOUS]));
    assert_eq!(by_id(&setups, 10)["observation_count"], 3);
    assert_eq!(
        by_id(&setups, 10)["sto_files"][0]["sha256"],
        prev_sto.as_str()
    );
    assert_eq!(by_id(&setups, 11)["names"], json!([]));
    assert!(
        !setups
            .iter()
            .any(|s| s["setup_id"] == 69 || s["setup_id"] == 70)
    );

    // the log line says what was merged
    let lines = log_lines(&env);
    assert_eq!(lines.len(), 1);
    assert_eq!(lines[0]["setup_keys_version"], 2);
    let report = &lines[0]["report"];
    let merges = report["merges"].as_array().unwrap();
    assert_eq!(merges.len(), 2);
    let merge_of = |survivor: i64| {
        merges
            .iter()
            .find(|m| m["survivor"] == survivor)
            .expect("a merge")
    };
    assert_eq!(merge_of(10)["merged"], json!([69]));
    assert_eq!(merge_of(10)["observations_moved"], 1);
    assert_eq!(merge_of(10)["notes_moved"], 1);
    assert_eq!(merge_of(71)["merged"], json!([70]));
    assert_eq!(merge_of(71)["names"], json!([ATLANTA]));
    assert_eq!(report["removed_without_observations"], json!([90]));
    assert_eq!(report["kept_for_notes"], json!([91]));

    // a second start finds nothing to do
    let after = rows(&env);
    let env = env.restart().await;
    assert_eq!(rows(&env), after);
    assert_eq!(
        log_lines(&env).len(),
        1,
        "an unchanged start writes no line"
    );

    // the next harvest visit: its load snapshot is the committed setup (once the visit commits)
    let next = with(
        &atlanta,
        &[
            ("Chassis.FrontArb.Preload", "-6.9 Nm"),
            ("UpdateCount", "9"),
        ],
    );
    let setups_before = count(&env, "SELECT COUNT(*) FROM setups");
    env.app
        .store
        .write_blocking(move |c| {
            let msg = json!({"type": "garage.snapshot", "visit_id": "d1d1d1d1-0000-4000-8000-000000000001",
                "update_count": 9, "observed_at": "2026-10-05T04:00:00Z", "car_path": SUPRA,
                "driver": {"customer_id": CUSTOMER}, "values": next});
            coach_server::setups::record_garage_snapshot(c, &msg, COLLECTOR, &fake_fingerprint)?;
            Ok(())
        })
        .expect("snapshot");
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups"),
        setups_before,
        "no new setup"
    );
    env.app
        .store
        .write_blocking(move |c| {
            let msg = json!({"type": "garage.commit", "visit_id": "d1d1d1d1-0000-4000-8000-000000000001",
                "update_count": 9, "entered_at": "2026-10-05T03:59:00Z", "exited_at": "2026-10-05T04:00:01Z",
                "car_path": SUPRA, "driver": {"customer_id": CUSTOMER}, "setup_name": ATLANTA,
                "sto_sha256": sha("atlanta sto"), "is_modified": false, "load_type": "user", "values": {}});
            coach_server::setups::record_garage_commit(c, &msg, COLLECTOR, &fake_fingerprint)?;
            Ok(())
        })
        .expect("commit");
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups"),
        setups_before,
        "the commit is setup 71 again"
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setup_observations WHERE setup_id = 71 AND update_count = 9 AND source = 'garage_snapshot'"
        ),
        1,
        "it joined setup 71"
    );
}

#[tokio::test]
async fn exports_of_one_setup_merge_and_take_the_names_of_their_files() {
    let env = Env::new().await;
    // The migration's .htm exports (setups 53 to 68): canonical snake_case keys, values as the
    // garage showed them. Two exports of one setup after different runs differ in run readings and
    // derived values only; a third is another setup. A recording (CarSetup keys) never joins one.
    let sonoma = json!({
        "chassis.front_arb.link_slack": "-2 mm", "chassis.front_arb.preload": "-5.2 ft-lbs",
        "chassis.left_front.camber": "+4.6 deg", "chassis.left_front.corner_weight": "1014 lbs",
        "chassis.left_front.ride_height": "4.252 in", "tires.left_front.cold_pressure": "22.0 psi",
        "tires.left_front.last_hot_pressure": "24.5 psi",
        "tires.left_front.last_temps_omi": "[\"99F\", \"100F\", \"101F\"]",
        "tires.left_front.tread_remaining": "[\"99%\", \"98%\", \"97%\"]",
    });
    let sonoma_again = with(
        &sonoma,
        &[
            ("chassis.front_arb.preload", "-5.3 ft-lbs"),
            ("chassis.left_front.corner_weight", "1013 lbs"),
            ("tires.left_front.last_hot_pressure", "26.0 psi"),
            (
                "tires.left_front.last_temps_omi",
                "[\"151F\", \"149F\", \"147F\"]",
            ),
            (
                "tires.left_front.tread_remaining",
                "[\"96%\", \"95%\", \"95%\"]",
            ),
        ],
    );
    let phoenix = with(&sonoma, &[("chassis.left_front.camber", "+3.9 deg")]);
    let recording = json!({
        "Chassis.FrontArb.LinkSlack": "-2 mm", "Chassis.LeftFront.Camber": "+4.6 deg", "UpdateCount": "1",
    });
    assert_ne!(
        old_fingerprint(&map(&sonoma)),
        old_fingerprint(&map(&sonoma_again)),
        "version 1 kept them apart"
    );

    let (h1, h2, h3) = (
        sha("sonoma htm"),
        sha("sonoma again htm"),
        sha("phoenix htm"),
    );
    let (s1, s2, s3, rec) = (sonoma, sonoma_again, phoenix, recording);
    seed(&env, move |c| {
        file(c, &h1, "NOAPS_MaconiSetupShop Sonoma 26S3 R.htm", "htm")?;
        file(
            c,
            &h2,
            "NOAPS_MaconiSetupShop Sonoma 26S3 R after practice.htm",
            "htm",
        )?;
        file(
            c,
            &h3,
            "exports/NOAPS_MaconiSetupShop Phoenix 26S3 Q.HTM",
            "htm",
        )?;
        let at = [
            "2026-10-03T15:41:00Z",
            "2026-10-03T15:41:01Z",
            "2026-10-03T15:41:02Z",
        ];
        setup(c, supra(53, &s1, at[0], at[0]))?;
        export(c, 53, &h1, at[0])?;
        setup(c, supra(54, &s2, at[1], at[1]))?;
        export(c, 54, &h2, at[1])?;
        setup(c, supra(55, &s3, at[2], at[2]))?;
        export(c, 55, &h3, at[2])?;
        let g3 = "2026-10-03T14:01:00Z";
        setup(c, supra(11, &rec, g3, g3))?;
        modified_recording(c, 11, "g3 recording", g3)?;
        Ok(())
    });
    let observations = count(&env, "SELECT COUNT(*) FROM setup_observations");

    let env = env.restart().await;
    assert_eq!(
        ids(&env),
        vec![11, 53, 55],
        "54 joined 53, the earlier export"
    );
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setup_observations"),
        observations
    );
    assert_eq!(
        sorted_observations(&env, 53),
        vec!["htm_export:", "htm_export:"]
    );

    let setups = library(&env).await;
    assert_eq!(
        by_id(&setups, 53)["names"],
        json!([
            "NOAPS_MaconiSetupShop Sonoma 26S3 R",
            "NOAPS_MaconiSetupShop Sonoma 26S3 R after practice"
        ]),
        "named after both exports' files"
    );
    assert_eq!(by_id(&setups, 53)["sources"], json!(["htm_export"]));
    assert_eq!(
        by_id(&setups, 55)["names"],
        json!(["NOAPS_MaconiSetupShop Phoenix 26S3 Q"])
    );
    assert_eq!(
        by_id(&setups, 11)["names"],
        json!([]),
        "a modified load has no name to take"
    );

    // the export's readouts are listed apart; its settings are not
    let detail = env.get("/api/v1/setups/53").await.json();
    let excluded: Vec<&str> = detail["identity_excluded_keys"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .collect();
    for key in [
        "chassis.front_arb.preload",
        "chassis.left_front.corner_weight",
        "chassis.left_front.ride_height",
        "tires.left_front.last_hot_pressure",
        "tires.left_front.last_temps_omi",
        "tires.left_front.tread_remaining",
    ] {
        assert!(excluded.contains(&key), "{key} in {excluded:?}");
    }
    assert!(!excluded.contains(&"chassis.front_arb.link_slack"));
    assert!(!excluded.contains(&"tires.left_front.cold_pressure"));
    assert_eq!(
        log_lines(&env)[0]["report"]["merges"][0]["merged"],
        json!([54])
    );
}

// ------------------------------------------- schema version 2: only a commit makes a setup

const CAMBER: &str = "claude G5 R RF camber -2.0.sto";
const RESUME: &str = "9a7b35e9-a907-4c1e-93e5-3628afe3e9e4";
const RESTORE: &str = "89505213-48f4-42dd-9ae0-d61c36edfe26";

/// The resume run's file as committed (+5.8 deg left-front camber at Apply & Close) and as the
/// garage read it while loaded and at the next entry (+5.9 deg), the 2026-10-04 shape of setups 73
/// and 72; the car's previous file (`claude LV R CAM`, setup 10).
fn camber_values() -> (Value, Value, Value) {
    let committed = json!({
        "Chassis.LeftFront.Camber": "+5.8 deg", "Chassis.RightFront.Camber": "-2.0 deg",
        "Chassis.LeftFront.Caster": "+11.9 deg", "Chassis.FrontArb.LinkSlack": "-2 mm",
        "Chassis.LeftFront.CornerWeight": "4509 N", "Tires.LeftFront.ColdPressure": "152 kPa",
        "UpdateCount": "4",
    });
    let in_garage = with(
        &committed,
        &[
            ("Chassis.LeftFront.Camber", "+5.9 deg"),
            ("Chassis.LeftFront.CornerWeight", "4511 N"),
            ("UpdateCount", "3"),
        ],
    );
    let previous = with(
        &committed,
        &[
            ("Chassis.RightFront.Camber", "-3.4 deg"),
            ("Chassis.LeftFront.Camber", "+5.2 deg"),
            ("UpdateCount", "7"),
        ],
    );
    (committed, in_garage, previous)
}

#[test]
fn the_sims_rounding_of_camber_and_pressure_is_one_setup_and_nothing_else_is() {
    use coach_server::setups::same_setup_within_rounding as same;
    let (committed, in_garage, previous) = camber_values();
    let (c, g, p) = (map(&committed), map(&in_garage), map(&previous));
    assert!(
        same(&c, &g),
        "camber 0.1 deg apart; corner weight is outside identity"
    );
    assert!(!same(&c, &p), "another file");
    let changed = |key: &str, value: &str| map(&with(&committed, &[(key, value)]));
    assert!(
        !same(&c, &changed("Chassis.LeftFront.Camber", "+6.0 deg")),
        "0.2 deg"
    );
    assert!(
        !same(&c, &changed("Chassis.LeftFront.Caster", "+12.0 deg")),
        "caster is not rounded"
    );
    assert!(
        same(&c, &changed("Tires.LeftFront.ColdPressure", "152.6 kPa")),
        "under 0.1 psi"
    );
    assert!(
        !same(&c, &changed("Tires.LeftFront.ColdPressure", "153 kPa")),
        "1 kPa is 0.145 psi"
    );
    let psi = |v: &str| map(&json!({"Tires.LeftRear.ColdPressure": v}));
    assert!(same(&psi("22.0 psi"), &psi("22.1 psi")));
    assert!(!same(&psi("22.0 psi"), &psi("22.2 psi")));
    assert!(
        !same(&psi("22.0 psi"), &psi("22.0 kPa")),
        "units must agree"
    );
    assert!(
        !same(&c, &changed("Chassis.FrontArb.LinkSlack", "-1 mm")),
        "a setting"
    );
    let mut extra = c.clone();
    extra.insert("Chassis.Rear.RearEndRatio".into(), "4.10".into());
    assert!(!same(&c, &extra), "a key more is another setup");
}

#[tokio::test]
async fn a_snapshot_only_setup_folds_into_its_visits_commit_and_a_second_start_changes_nothing() {
    let env = Env::new().await;
    let (committed, in_garage, previous) = camber_values();
    // A version-1 server: setup 73 is the resume visit's commit, setup 72 its load snapshot and the
    // restore visit's entry snapshot (both +5.9 deg), setup 74 an intermediate snapshot of the
    // resume visit that equals no setup, setup 10 the previous file the restore visit committed,
    // setup 75 a snapshot of a visit whose commit has not arrived.
    let intermediate = with(&committed, &[("Chassis.RightFront.Camber", "-1.0 deg")]);
    let waiting = with(&committed, &[("Chassis.FrontArb.LinkSlack", "-4 mm")]);
    let (c73, g72, p10, i74, w75) = (
        committed.clone(),
        in_garage.clone(),
        previous.clone(),
        intermediate,
        waiting,
    );
    let camber_sto = sha("camber sto");
    let (cs, ps) = (camber_sto.clone(), sha("previous sto"));
    seed(&env, move |c| {
        let (camber_file, prev_file) = ((CAMBER, cs.as_str()), (PREVIOUS, ps.as_str()));
        file(c, &cs, CAMBER, "sto")?;
        setup(
            c,
            supra(10, &p10, "2026-09-28T15:23:02Z", "2026-10-04T12:51:26.275Z"),
        )?;
        visit(c, "v-earlier", Some(prev_file))?;
        commit(c, 10, "v-earlier", 8, prev_file, "2026-09-28T15:23:02Z")?;
        visit(c, RESUME, Some(camber_file))?;
        let early = "2026-10-04T12:51:15.000Z";
        setup(c, supra(74, &i74, early, early))?;
        snapshot(c, 74, RESUME, 2, early)?;
        setup(
            c,
            supra(
                72,
                &g72,
                "2026-10-04T12:51:16.762Z",
                "2026-10-04T12:51:22.763Z",
            ),
        )?;
        snapshot(c, 72, RESUME, 3, "2026-10-04T12:51:16.762Z")?;
        let applied = "2026-10-04T12:51:18.325Z";
        setup(c, supra(73, &c73, applied, applied))?;
        commit(c, 73, RESUME, 4, camber_file, applied)?;
        visit(c, RESTORE, Some(prev_file))?;
        snapshot(c, 72, RESTORE, 5, "2026-10-04T12:51:22.763Z")?;
        commit(c, 10, RESTORE, 7, prev_file, "2026-10-04T12:51:26.275Z")?;
        visit(c, "v-in-the-journal", None)?;
        let later = "2026-10-04T13:00:00Z";
        setup(c, supra(75, &w75, later, later))?;
        snapshot(c, 75, "v-in-the-journal", 9, later)?;
        note(c, 72, "camber reads high in the garage")?;
        Ok(())
    });
    let env = env.restart().await;
    assert_eq!(
        ids(&env),
        vec![10, 73, 75],
        "72 folded into 73, 74 equals no setup, 75 waits for its commit"
    );
    assert_eq!(
        sorted_observations(&env, 73),
        vec!["garage_commit:4", "garage_snapshot:3", "garage_snapshot:5"],
        "the load snapshot (its visit's commit) and the restore visit's entry snapshot (within \
         rounding of 73, not of its own commit)"
    );
    assert_eq!(
        sorted_observations(&env, 10),
        vec!["garage_commit:7", "garage_commit:8"]
    );
    assert_eq!(
        row(&env, 73).values,
        committed,
        "the commit's values stay the setup's"
    );
    assert_eq!(sorted_observations(&env, 75), vec!["garage_snapshot:9"]);
    // the folded snapshots keep their values
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NOT NULL"
        ),
        3
    );
    let kept: String = env.query(|c| {
        c.query_row(
            "SELECT values_json FROM garage_snapshots WHERE visit_id = ?1 AND update_count = 2",
            [RESUME],
            |r| r.get(0),
        )
    });
    assert!(kept.contains("-1.0 deg"), "{kept}");
    let notes: Vec<String> = env.query(|c| {
        c.prepare("SELECT subject_id FROM notes")?
            .query_map([], |r| r.get(0))?
            .collect()
    });
    assert_eq!(notes, vec!["73"], "the note followed the setup");

    let setups = library(&env).await;
    assert_eq!(by_id(&setups, 73)["names"], json!([CAMBER]));
    assert_eq!(by_id(&setups, 73)["observation_count"], 3);
    assert_eq!(
        by_id(&setups, 73)["sto_files"][0]["sha256"],
        camber_sto.as_str()
    );

    let lines = log_lines(&env);
    assert_eq!(lines.len(), 1);
    let folds = lines[0]["report"]["folds"].as_array().unwrap().clone();
    let fold = |setup: i64| folds.iter().find(|f| f["setup"] == setup).cloned();
    let f72 = fold(72).expect("72 folded");
    assert_eq!(f72["into"], json!([73]));
    assert_eq!(f72["observations_moved"], 2);
    assert_eq!(f72["removed"], true);
    let f74 = fold(74).expect("74 folded");
    assert_eq!(f74["into"], json!([]));
    assert_eq!(f74["observations_without_setup"], 1);
    assert!(
        fold(75).is_none(),
        "a visit without its commit is left alone"
    );

    let after = rows(&env);
    let env = env.restart().await;
    assert_eq!(rows(&env), after, "a second start changes nothing");
    assert_eq!(log_lines(&env).len(), 1);
}

/// Sends a visit's events through the server as the collector would: its snapshots, then the
/// commit, which references its last snapshot's values by `update_count`.
fn visit_events(
    env: &Env,
    visit: &str,
    snapshots: Vec<(i64, Value)>,
    commit: Option<(Option<&str>, i64)>,
) {
    let visit = visit.to_string();
    let commit = commit.map(|(name, count)| (name.map(str::to_string), count));
    env.app
        .store
        .write_blocking(move |c| {
            let fp = |v: &BTreeMap<String, String>| fake_fingerprint(v);
            for (count, values) in &snapshots {
                let msg = json!({"type": "garage.snapshot", "visit_id": visit, "update_count": count,
                    "observed_at": format!("2026-10-04T12:51:{:02}.000Z", 10 + count), "car_path": SUPRA,
                    "driver": {"customer_id": CUSTOMER}, "values": values});
                let outcome =
                    coach_server::setups::record_garage_snapshot(c, &msg, COLLECTOR, &fp).unwrap();
                assert!(matches!(
                    outcome,
                    coach_server::setups::GarageOutcome::Stored(_)
                ));
            }
            if let Some((name, count)) = &commit {
                let msg = json!({"type": "garage.commit", "visit_id": visit, "update_count": count,
                    "entered_at": "2026-10-04T12:51:10.000Z",
                    "exited_at": format!("2026-10-04T12:51:{:02}.500Z", 10 + count),
                    "car_path": SUPRA, "driver": {"customer_id": CUSTOMER}, "setup_name": name,
                    "sto_sha256": name.as_ref().map(|n| sha(n)), "is_modified": name.is_none(),
                    "load_type": "user", "values": {}});
                let outcome =
                    coach_server::setups::record_garage_commit(c, &msg, COLLECTOR, &fp).unwrap();
                assert!(matches!(
                    outcome,
                    coach_server::setups::GarageOutcome::Stored(_)
                ));
            }
            Ok(())
        })
        .expect("garage events");
}

#[tokio::test]
async fn only_the_commit_creates_a_setup_and_the_visits_snapshots_observe_the_setup_they_equal() {
    let env = Env::new().await;
    let (committed, in_garage, previous) = camber_values();
    seed(&env, |_| Ok(()));
    // the car's previous file, committed by an earlier visit
    visit_events(
        &env,
        "v-earlier",
        vec![(1, previous.clone())],
        Some((Some(PREVIOUS), 1)),
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setups"), 1);

    // The resume visit: entry (the previous file, corner weight recomputed), load (+5.9 deg) and
    // Apply & Close (+5.8 deg, the commit's values). Before the commit nothing is a setup.
    let entry = with(
        &previous,
        &[
            ("Chassis.LeftFront.CornerWeight", "4512 N"),
            ("UpdateCount", "2"),
        ],
    );
    let at_exit = with(&committed, &[("UpdateCount", "4")]);
    visit_events(
        &env,
        RESUME,
        vec![(2, entry), (3, in_garage.clone()), (4, at_exit.clone())],
        None,
    );
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups"),
        1,
        "snapshots wait for the commit"
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NULL"
        ),
        3
    );
    visit_events(&env, RESUME, vec![], Some((Some(CAMBER), 4)));
    let ids_now = ids(&env);
    assert_eq!(ids_now.len(), 2, "the commit made one setup: {ids_now:?}");
    let (prev_id, camber_id) = (ids_now[0], ids_now[1]);
    assert_eq!(
        sorted_observations(&env, camber_id),
        vec!["garage_commit:4", "garage_snapshot:3", "garage_snapshot:4"]
    );
    assert_eq!(
        sorted_observations(&env, prev_id),
        vec!["garage_commit:1", "garage_snapshot:1", "garage_snapshot:2"],
        "the entry snapshot is the setup the car had"
    );

    // The restore visit: entry reads the camber file at +5.9 deg (within rounding of that setup,
    // not of this visit's commit), then the previous file is loaded back and committed.
    let restore_entry = with(&in_garage, &[("UpdateCount", "5")]);
    let reload = with(&previous, &[("UpdateCount", "6")]);
    visit_events(
        &env,
        RESTORE,
        vec![(5, restore_entry.clone()), (6, reload)],
        Some((Some(PREVIOUS), 6)),
    );
    assert_eq!(ids(&env), ids_now, "no setup from the restore visit");
    assert_eq!(
        sorted_observations(&env, camber_id),
        vec![
            "garage_commit:4",
            "garage_snapshot:3",
            "garage_snapshot:4",
            "garage_snapshot:5"
        ]
    );
    assert_eq!(row(&env, camber_id).values, at_exit);

    // A snapshot replayed after its commit is acknowledged and changes nothing.
    let before = rows(&env);
    visit_events(&env, RESTORE, vec![(5, restore_entry)], None);
    assert_eq!(rows(&env), before);

    // A modified exit makes the values-only setup; its snapshot within rounding observes it.
    let edited = with(
        &committed,
        &[
            ("Tires.LeftFront.ColdPressure", "160 kPa"),
            ("UpdateCount", "8"),
        ],
    );
    let edited_garage = with(
        &edited,
        &[
            ("Chassis.LeftFront.Camber", "+5.9 deg"),
            ("UpdateCount", "7"),
        ],
    );
    visit_events(
        &env,
        "v-modified",
        vec![(7, edited_garage), (8, edited)],
        Some((None, 8)),
    );
    let ids_after = ids(&env);
    assert_eq!(ids_after.len(), 3);
    assert_eq!(
        sorted_observations(&env, ids_after[2]),
        vec!["garage_commit:8", "garage_snapshot:7", "garage_snapshot:8"]
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NULL"
        ),
        0
    );
}

// ------------------------------------------------------------- the G3 capture

/// Every `CarSetup` the G3 rig capture published, with its `UpdateCount`, in publish order
/// (`test-data/sdk/g3-garage-pit-2026-10-03.sdkfix`, read as `crates/coach-ibt/tests` reads it).
fn g3_publishes() -> Vec<(i64, BTreeMap<String, String>)> {
    let le_u32 =
        |b: &[u8], at: usize| u32::from_le_bytes(b[at..at + 4].try_into().unwrap()) as usize;
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../test-data/sdk/g3-garage-pit-2026-10-03.sdkfix");
    let bytes = std::fs::read(&path).expect("the G3 fixture");
    assert_eq!(&bytes[..8], b"IRCSDKF1");
    let mut region_map: Vec<u8> = Vec::new();
    let mut out: Vec<(i64, BTreeMap<String, String>)> = Vec::new();
    let mut at = 8;
    while at + 5 <= bytes.len() {
        let (kind, len) = (bytes[at], le_u32(&bytes, at + 1));
        let payload = &bytes[at + 5..at + 5 + len];
        at += 5 + len;
        if kind != 1 {
            continue;
        }
        let region = &payload[4..];
        if region_map.len() < region.len() {
            region_map.resize(region.len(), 0);
        }
        region_map[..region.len()].copy_from_slice(region);
        let (text_len, text_at) = (le_u32(&region_map, 16), le_u32(&region_map, 20));
        let text = coach_ibt::decode_session_info(&region_map[text_at..text_at + text_len]);
        let info = coach_ibt::SessionInfo::parse(&text).expect("session info parses");
        if let Some(setup) = info.car_setup()
            && let Some(count) = setup.update_count
            && out.last().is_none_or(|(c, _)| *c != count)
        {
            out.push((count, setup.values));
        }
    }
    out
}

#[tokio::test]
async fn the_g3_captures_visits_make_a_setup_per_commit_and_none_from_snapshots() {
    let publishes = g3_publishes();
    let in_range = |lo: i64, hi: i64| -> Vec<(i64, Value)> {
        publishes
            .iter()
            .filter(|(c, _)| (lo..=hi).contains(c))
            .map(|(c, v)| (*c, json!(v)))
            .collect()
    };
    // The 209 s visit (counts 2 to 8: two files loaded, four applies, an unsaved edit, Apply and
    // Close) and the later visits (leave without Apply 10 to 12, Revert 13 to 18, reload 19 to 21;
    // `test-data/sdk/README.md`).
    let visits: [(&str, i64, i64, Option<&str>); 4] = [
        ("g3-visit-1", 2, 8, Some("Test LV R CAM.sto")),
        ("g3-visit-2", 10, 12, None),
        ("g3-visit-3", 13, 18, Some("Test LV R CAM.sto")),
        ("g3-visit-4", 19, 21, Some("Test LV R CAM.sto")),
    ];
    // A version-1 server made a setup of every distinct fingerprint of the first visit.
    let first = in_range(2, 8);
    assert!(
        first.len() >= 5,
        "{} publishes in the first visit",
        first.len()
    );
    let distinct: BTreeSet<String> = first
        .iter()
        .map(|(_, v)| fake_fingerprint(&map(v)))
        .collect();
    assert!(
        distinct.len() >= 3,
        "{} distinct snapshots in the first visit",
        distinct.len()
    );

    let env = Env::new().await;
    seed(&env, |_| Ok(()));
    let mut commits = BTreeSet::new();
    for (visit, lo, hi, name) in visits {
        let snapshots = in_range(lo, hi);
        let (last, values) = snapshots.last().cloned().expect("a snapshot");
        commits.insert(fake_fingerprint(&map(&values)));
        visit_events(&env, visit, snapshots, Some((name, last)));
    }
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups") as usize,
        commits.len(),
        "one setup per distinct commit, none from a snapshot"
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setups s WHERE NOT EXISTS (SELECT 1 FROM setup_observations o
               WHERE o.setup_id = s.setup_id AND o.source = 'garage_commit')"
        ),
        0
    );
    // The last snapshot before Apply & Close (count 7) differs from the exit (count 8) only in
    // derived values: both observe the first visit's commit.
    let snapshot_of = |n: i64| -> Option<i64> {
        env.query(move |c| {
            c.query_row(
                "SELECT setup_id FROM setup_observations WHERE visit_id = 'g3-visit-1'
                   AND update_count = ?1 AND source = 'garage_snapshot'",
                [n],
                |r| r.get(0),
            )
            .optional()
        })
    };
    let committed: i64 = env.query(|c| {
        c.query_row(
            "SELECT setup_id FROM setup_observations WHERE visit_id = 'g3-visit-1'
               AND source = 'garage_commit'",
            [],
            |r| r.get(0),
        )
    });
    assert_eq!(snapshot_of(7), Some(committed));
    assert_eq!(snapshot_of(8), Some(committed));
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM garage_snapshots WHERE resolved_at IS NULL"
        ),
        0
    );
}
