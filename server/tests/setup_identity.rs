//! Setup identity at start-up (`setup-keys.json` version 2, `src/setup_identity.rs`): setups stored
//! under the version-1 fingerprints are merged when the new rule calls them one, into the named
//! setup; observations, names, file links and notes follow; setups without an observation go; the
//! log says what happened, and a second start changes nothing. Also the name of a setup known only
//! from a garage `.htm` export.

mod support;

use std::collections::BTreeMap;

use rusqlite::{Connection, params};
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

    // the next harvest visit: its load snapshot is the committed setup
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
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setup_observations WHERE setup_id = 71 AND update_count = 9"
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
