//! Unit table (U1): replays every recorded oracle call of the unit against its native entries,
//! checks every cached view against the oracle's view probe, and the native multi-file merge
//! against the oracle's merge (`docs/08-rewrite/port/phase2-plan.md` sections 2 and 3).

mod support;

use std::path::PathBuf;

use coach_analysis::kernels::table::{
    ColumnKey, SteeringBasis, Table, View, merge, setup_channel_series_handle,
};
use coach_analysis::kernels::{Outcome, PyValue, Session, TableMeta, VectorStore, codec, same};
use coach_analysis::store::{self, ColumnStore};
use sha2::{Digest, Sha256};

#[test]
fn table_vectors_replay_exactly() {
    let counts = support::vectors::replay_unit("table");
    assert!(
        !counts.is_empty(),
        "no table vectors under tests/vectors/kernels/{}",
        support::vectors::platform()
    );
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// `table_vectors.view_digest` of a float view: every sample's presence byte, then every
/// sample's bits (zeros when absent).
fn float_digest(view: &View) -> String {
    let mut digest = Sha256::new();
    let presence: Vec<u8> = view.iter().map(|value| u8::from(value.is_some())).collect();
    digest.update(&presence);
    for value in view.iter() {
        digest.update(value.unwrap_or(0.0).to_le_bytes());
    }
    hex(&digest.finalize())
}

fn bool_digest(values: &[bool]) -> String {
    let bytes: Vec<u8> = values.iter().map(|&b| u8::from(b)).collect();
    hex(&Sha256::digest(&bytes))
}

fn flag_digest(values: &[i64]) -> String {
    let mut digest = Sha256::new();
    for value in values {
        digest.update(value.to_le_bytes());
    }
    hex(&digest.finalize())
}

/// Compares the four cached views of `key` with a probe entry.
fn check_views(table: &Table, key: &ColumnKey, expected: &PyValue, label: &str) {
    let text = |name: &str| {
        expected
            .get(name)
            .and_then(PyValue::as_str)
            .unwrap_or_else(|| panic!("{label}: probe has no {name}"))
            .to_owned()
    };
    let flag = |name: &str| {
        expected
            .get(name)
            .and_then(PyValue::as_bool)
            .unwrap_or_else(|| panic!("{label}: probe has no {name}"))
    };
    let finite = table.finite_for(key);
    assert_eq!(
        float_digest(&finite),
        text("finite"),
        "{label}: finite view"
    );
    assert_eq!(
        finite.complete(),
        flag("complete"),
        "{label}: finite complete"
    );
    let fraction = table.fractions_for(key);
    assert_eq!(
        float_digest(&fraction),
        text("fraction"),
        "{label}: fraction view"
    );
    assert_eq!(
        fraction.complete(),
        flag("fraction_complete"),
        "{label}: fraction complete"
    );
    assert_eq!(
        bool_digest(&table.booleans_for(key)),
        text("boolean"),
        "{label}: boolean view"
    );
    if flag("flags_beyond_i64") {
        // The native view holds each int's low 64 bits (review finding 4); the exact ints are
        // checked where they leave the kernel, in the `_caution_flag_series` replay.
        assert_eq!(
            flag_digest(&table.flag_bits_for(key)),
            text("flags_low64"),
            "{label}: flag view (low 64 bits)"
        );
    } else {
        assert_eq!(
            flag_digest(&table.flag_bits_for(key)),
            text("flags"),
            "{label}: flag view"
        );
    }
    table.check().unwrap_or_else(|e| panic!("{label}: {e}"));
}

/// Every channel's views, every alias list's key and views, the wheel ratios and the steering
/// column, against the oracle's `views` probe of each recording.
#[test]
fn table_views_match_the_oracle_probe() {
    let mut checked = 0usize;
    for folder in support::vectors::recordings() {
        let probe = folder.join("table").join("probes").join("views.json");
        if !probe.is_file() {
            continue;
        }
        let recording = folder.file_name().unwrap().to_string_lossy().into_owned();
        let document = support::vectors::load_document(&probe).unwrap_or_else(|e| panic!("{e}"));
        let result = document.value.get("result").cloned().expect("probe result");
        let session = Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
        let table = Table::of(&session).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(
            result.get("length").and_then(PyValue::as_int),
            Some(table.len() as i64),
            "{recording}: length"
        );
        let channels = result
            .get("channels")
            .and_then(PyValue::as_dict)
            .expect("channels");
        assert_eq!(
            channels.len(),
            table.meta().channel_names.len(),
            "{recording}: channels"
        );
        for (name, expected) in channels.iter() {
            let name = name.as_str().expect("channel name");
            let label = format!("{recording} channel {name}");
            check_views(
                table.as_ref(),
                &ColumnKey::Channel(name.to_owned()),
                expected,
                &label,
            );
            checked += 1;
        }
        for expected in result
            .get("aliases")
            .and_then(PyValue::as_list)
            .expect("aliases")
        {
            let aliases: Vec<String> = expected
                .get("aliases")
                .and_then(PyValue::as_list)
                .expect("alias list")
                .iter()
                .map(|a| a.as_str().expect("alias").to_owned())
                .collect();
            let names: Vec<&str> = aliases.iter().map(String::as_str).collect();
            let default = expected.get("default").cloned().expect("default");
            let label = format!("{recording} aliases {names:?} default {default:?}");
            let key = table.key(&names, default);
            let expected_key = expected.get("key").and_then(PyValue::as_str);
            match (&key, expected_key) {
                (ColumnKey::Channel(name), Some(want)) => assert_eq!(name, want, "{label}: key"),
                (ColumnKey::Default(_), None) => {}
                _ => panic!("{label}: key {key:?}, oracle {expected_key:?}"),
            }
            check_views(table.as_ref(), &key, expected, &label);
            checked += 1;
        }
        for expected in result
            .get("wheel_ratios")
            .and_then(PyValue::as_list)
            .expect("ratios")
        {
            let speed = expected
                .get("speed")
                .and_then(PyValue::as_str)
                .expect("speed");
            let wheel = expected
                .get("wheel")
                .and_then(PyValue::as_str)
                .expect("wheel");
            let view = table.wheel_ratios(
                &ColumnKey::Channel(speed.to_owned()),
                &ColumnKey::Channel(wheel.to_owned()),
            );
            assert_eq!(
                Some(float_digest(&view).as_str()),
                expected.get("digest").and_then(PyValue::as_str),
                "{recording}: wheel ratios {speed}/{wheel}"
            );
            checked += 1;
        }
        let steering = result.get("steering").expect("steering");
        let basis = SteeringBasis::of(&session).unwrap_or_else(|e| panic!("{recording}: {e}"));
        let view = table.steering_column(&basis);
        assert_eq!(
            Some(float_digest(&view).as_str()),
            steering.get("digest").and_then(PyValue::as_str),
            "{recording}: steering column"
        );
        assert_eq!(
            Some(view.complete()),
            steering.get("complete").and_then(PyValue::as_bool),
            "{recording}: steering complete"
        );
        session.access().take();
        checked += 1;
    }
    println!(
        "table views: {checked} views identical ({})",
        support::vectors::platform()
    );
    assert!(checked > 0, "no view probes found");
}

fn merge_cases() -> Vec<PathBuf> {
    let root = support::vectors::crate_dir()
        .join("tests")
        .join("vectors")
        .join("table")
        .join("merge")
        .join(support::vectors::platform());
    let mut cases: Vec<PathBuf> = std::fs::read_dir(&root)
        .unwrap_or_else(|e| panic!("{}: {e}", root.display()))
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect();
    cases.sort();
    cases
}

/// The merge case with missing cells (`reconnect-gaps`).
fn gaps_case() -> PathBuf {
    merge_cases()
        .into_iter()
        .find(|case| case.ends_with("reconnect-gaps"))
        .expect("the reconnect-gaps merge case")
}

/// A table over a merged store with only the metadata the raw columns need.
fn bare_table(store: ColumnStore) -> Table {
    let names: Vec<String> = store
        .header
        .columns
        .iter()
        .map(|c| c.name.clone())
        .collect();
    let length = store.header.sampling.sample_count as usize;
    let meta = TableMeta {
        metadata: PyValue::None,
        session_info: PyValue::None,
        variables: PyValue::None,
        available_variables: PyValue::None,
        channel_units: Vec::new(),
        length,
        channel_names: names,
    };
    Table::from_store(store, meta).unwrap_or_else(|e| panic!("{e}"))
}

/// The merged table's columns as the Python lists they stand for.
fn columns_of(table: &Table) -> Vec<(String, PyValue)> {
    table
        .meta()
        .channel_names
        .iter()
        .map(|name| {
            let raw = table
                .raw(&ColumnKey::Channel(name.clone()))
                .expect("column");
            (
                name.clone(),
                PyValue::List((0..raw.len()).map(|i| raw.value(i)).collect()),
            )
        })
        .collect()
}

fn ints(value: Option<&PyValue>) -> Vec<i64> {
    value
        .and_then(PyValue::as_list)
        .expect("int list")
        .iter()
        .map(|v| v.as_int().expect("int"))
        .collect()
}

/// `coach-analysis merge`'s core against `workflow._load_analysis_telemetry` on synthetic
/// groups with missing cells, duplicate times, a NaN time and a file timed by
/// `SessionTimeOfDay`; also reads the oracle-side merge store the vectors' `table.ccs` uses.
#[test]
fn merge_matches_the_oracle() {
    let cases = merge_cases();
    assert!(!cases.is_empty(), "no merge vectors");
    for case in cases {
        let name = case.file_name().unwrap().to_string_lossy().into_owned();
        let text = std::fs::read_to_string(case.join("merged.json")).expect("merged.json");
        let columns = VectorStore::read(&case.join("merged.vectors.ccs")).expect("vector store");
        let expected = codec::decode(text.trim_end(), Some(&columns)).expect("decode");
        let inputs: Vec<ColumnStore> = expected
            .get("inputs")
            .and_then(PyValue::as_list)
            .expect("inputs")
            .iter()
            .map(|file| ColumnStore::read(&case.join(file.as_str().expect("file"))).expect("input"))
            .collect();
        let preferred: Vec<String> = expected
            .get("preferred")
            .and_then(PyValue::as_list)
            .expect("preferred")
            .iter()
            .map(|n| n.as_str().expect("name").to_owned())
            .collect();
        let refs: Vec<&ColumnStore> = inputs.iter().collect();
        let merged = merge::merge(&refs, &preferred).unwrap_or_else(|e| panic!("{name}: {e}"));
        let out = std::env::temp_dir().join(format!("u1-merge-{}-{name}.ccs", std::process::id()));
        store::write_merged(&merged, &out).expect("write merged");
        let native = ColumnStore::read(&out).expect("read merged");
        let _ = std::fs::remove_file(&out);
        assert_eq!(native.stage(), store::STAGE_MERGE);
        let file_index: Vec<i64> = native
            .file_indices()
            .unwrap()
            .iter()
            .map(|&i| i64::from(i))
            .collect();
        assert_eq!(
            file_index,
            ints(expected.get("file_index")),
            "{name}: file index"
        );
        let records: Vec<i64> = native
            .sample_indices()
            .iter()
            .map(|&i| i64::from(i))
            .collect();
        assert_eq!(
            records,
            ints(expected.get("record_index")),
            "{name}: record index"
        );
        assert_eq!(
            Some(native.header.sampling.sample_count as i64),
            expected.get("sample_count").and_then(PyValue::as_int),
            "{name}: sample count"
        );
        let samples = expected
            .get("samples")
            .and_then(PyValue::as_dict)
            .expect("samples");
        let oracle = ColumnStore::read(&case.join("merged.ccs")).expect("oracle merge store");
        for (which, store) in [("native", native), ("oracle store", oracle)] {
            let table = bare_table(store);
            let actual = columns_of(&table);
            let names: Vec<&str> = actual.iter().map(|(n, _)| n.as_str()).collect();
            let want: Vec<&str> = samples.keys().map(|k| k.as_str().expect("name")).collect();
            assert_eq!(names, want, "{name} ({which}): channel order");
            for (channel, values) in &actual {
                let expected = samples.get(channel).expect("channel");
                if let Some(difference) = same(expected, values) {
                    panic!("{name} ({which}) {channel}: {difference}");
                }
            }
        }
        println!("merge {name}: identical");
    }
}

/// A store's bytes with its JSON header replaced by `edit(header)`.
fn with_header(bytes: &[u8], edit: impl FnOnce(&mut serde_json::Value)) -> Vec<u8> {
    let length = u32::from_le_bytes(bytes[12..16].try_into().unwrap()) as usize;
    let mut header: serde_json::Value = serde_json::from_slice(&bytes[16..16 + length]).unwrap();
    let data_start = (16 + length).div_ceil(8) * 8;
    edit(&mut header);
    let text = serde_json::to_vec(&header).unwrap();
    let mut out = bytes[..8].to_vec();
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(text.len() as u32).to_le_bytes());
    out.extend_from_slice(&text);
    out.resize((16 + text.len()).div_ceil(8) * 8, 0);
    out.extend_from_slice(&bytes[data_start..]);
    out
}

/// The merge stage's validation: provenance required on a merge store and refused on a decode
/// store, presence only on a merge store, exactly one 0/1 byte per sample.
#[test]
fn merge_store_validation() {
    let case = gaps_case();
    let merged = std::fs::read(case.join("merged.ccs")).unwrap();
    let decoded = std::fs::read(case.join("file0.ccs")).unwrap();
    assert!(ColumnStore::parse(merged.clone()).is_ok());
    let cases: Vec<(&str, Vec<u8>)> = vec![
        (
            "without provenance",
            with_header(&merged, |h| {
                h.as_object_mut().unwrap().remove("provenance");
            }),
        ),
        (
            "present block of the wrong length",
            with_header(&merged, |h| {
                for column in h["columns"].as_array_mut().unwrap() {
                    if let Some(present) = column.get_mut("present") {
                        present["length"] = (present["length"].as_u64().unwrap() - 1).into();
                    }
                }
            }),
        ),
        (
            "provenance in a decode store",
            with_header(&decoded, |h| {
                h["provenance"] = serde_json::json!({"file_index": h["sample_indices"].clone()});
            }),
        ),
        (
            "present block in a decode store",
            with_header(&decoded, |h| {
                let data = h["columns"][0]["data"].clone();
                h["columns"][0]["present"] = data;
            }),
        ),
        (
            "another stage",
            with_header(&merged, |h| h["stage"] = "kernels".into()),
        ),
    ];
    for (what, bytes) in cases {
        let error = ColumnStore::parse(bytes).err();
        assert!(error.is_some(), "{what}: accepted");
    }
    // A present byte other than 0 or 1.
    let store = ColumnStore::parse(merged.clone()).unwrap();
    let (index, column) = store
        .header
        .columns
        .iter()
        .enumerate()
        .find(|(_, c)| c.present.is_some())
        .unwrap();
    let length = u32::from_le_bytes(merged[12..16].try_into().unwrap()) as usize;
    let data_start = (16 + length).div_ceil(8) * 8;
    let mut corrupt = merged.clone();
    let at = data_start + column.present.unwrap().offset as usize;
    let first_absent = store
        .present(index)
        .unwrap()
        .iter()
        .position(|&b| b == 0)
        .unwrap();
    corrupt[at + first_absent] = 2;
    assert!(
        ColumnStore::parse(corrupt).is_err(),
        "present byte 2 accepted"
    );
}

/// Missing cells read as Python's `None` through every view: `_finite` and `_fraction` give
/// `None`, `_bool` false, flags 0, and the raw value `None`.
#[test]
fn merged_missing_cells_are_none_in_every_view() {
    let case = gaps_case();
    let table = bare_table(ColumnStore::read(&case.join("merged.ccs")).unwrap());
    let store = ColumnStore::read(&case.join("merged.ccs")).unwrap();
    let mut seen = 0;
    for (index, header) in store.header.columns.iter().enumerate() {
        let Some(present) = store.present(index) else {
            continue;
        };
        let key = ColumnKey::Channel(header.name.clone());
        let raw = table.raw(&key).unwrap();
        let finite = table.finite_for(&key);
        let fraction = table.fractions_for(&key);
        let booleans = table.booleans_for(&key);
        let flags = table.flag_bits_for(&key);
        for (sample, &flag) in present.iter().enumerate() {
            if flag == 0 {
                assert!(raw.value(sample).is_none());
                assert_eq!(finite.get(sample), None);
                assert_eq!(fraction.get(sample), None);
                assert!(!booleans[sample]);
                assert_eq!(flags[sample], 0);
                seen += 1;
            }
        }
        assert!(!finite.complete());
    }
    assert!(seen > 0);
}

/// The `kernels` hand-off keeps the setup series behind a handle: `_setup_telemetry` on the
/// handle returns exactly what the oracle returned on the dict.
#[test]
fn setup_telemetry_from_a_handle_matches_the_oracle() {
    let mut checked = 0;
    for folder in support::vectors::recordings() {
        let entry_dir = folder
            .join("table")
            .join("analysis_engine._setup_telemetry");
        let Ok(files) = std::fs::read_dir(&entry_dir) else {
            continue;
        };
        let recording = folder.file_name().unwrap().to_string_lossy().into_owned();
        let mut files: Vec<PathBuf> = files
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|x| x == "json"))
            .collect();
        files.sort();
        for file in files {
            let vector =
                support::vectors::load_call(&recording, "analysis_engine._setup_telemetry", &file)
                    .unwrap_or_else(|e| panic!("{e}"));
            let session = Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
            let handle = setup_channel_series_handle(&session).unwrap_or_else(|e| panic!("{e}"));
            let mut call = vector.call.clone();
            call.args[1] = handle;
            let answer = session.call("analysis_engine._setup_telemetry", &mut call);
            let support::vectors::Recorded::Value(expected) = &vector.recorded else {
                panic!("{}: the oracle raised", vector.label());
            };
            match &answer.outcome {
                Outcome::Value(actual) => {
                    if let Some(difference) = same(expected, actual) {
                        panic!("{}: {difference}", vector.label());
                    }
                }
                other => panic!("{}: {other:?}", vector.label()),
            }
            checked += 1;
        }
    }
    assert!(checked > 0);
}
