//! Unit I0 (kernel skeleton): the codec against the Python codec's own output, the vector-store
//! and document validation, the entry table, the session, and the replay comparison
//! (`docs/08-rewrite/port/phase2-plan.md` sections 3 and 6).
//!
//! `tests/vectors/codec/<platform>/` is written by `tools/emit_codec_vectors.py` (CPython 3.12).
//! Every call document under the kernel vector roots (committed, and `COACH_KERNEL_VECTORS`)
//! must also re-encode to its own bytes.

mod support;

use std::collections::BTreeSet;
use std::path::PathBuf;

use coach_analysis::kernels::codec::{self, ColumnSink, VectorStore};
use coach_analysis::kernels::dispatch::{self, Call, Slot};
use coach_analysis::kernels::{
    Answer, Dict, KernelError, Outcome, PyValue, Session, TableMeta, UNITS, same,
};
use support::vectors;

fn codec_dir() -> PathBuf {
    vectors::crate_dir()
        .join("tests")
        .join("vectors")
        .join("codec")
        .join(vectors::platform())
}

fn read(name: &str) -> (String, Vec<u8>) {
    let path = codec_dir().join(name);
    let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    (String::from_utf8(bytes.clone()).unwrap_or_default(), bytes)
}

fn conformance() -> (PyValue, PyValue) {
    let (text, _) = read("codec.json");
    let (_, store) = read("codec.ccs");
    let store = VectorStore::parse(&store).expect("codec.ccs");
    let stored = codec::decode(&text, Some(&store)).expect("codec.json");
    let (inline_text, _) = read("codec.inline.json");
    let inline = codec::decode(&inline_text, None).expect("codec.inline.json");
    (stored, inline)
}

#[test]
fn conformance_document_reencodes_byte_for_byte() {
    let (text, _) = read("codec.json");
    let (_, store_bytes) = read("codec.ccs");
    let (inline_text, _) = read("codec.inline.json");
    let (stored, inline) = conformance();
    if let Some(difference) = same(&inline, &stored) {
        panic!("column form and inline form differ {difference}");
    }
    let mut sink = ColumnSink::new();
    let again = codec::encode(&stored, Some(&mut sink)).unwrap();
    assert_eq!(again, text.trim_end_matches('\n'), "codec.json text");
    let written = sink.to_bytes("emit_codec_vectors.py").unwrap().unwrap();
    assert_eq!(written, store_bytes, "codec.ccs bytes");
    assert_eq!(
        codec::to_json(&inline).unwrap(),
        inline_text.trim_end_matches('\n')
    );
    assert_eq!(
        codec::to_json(&stored).unwrap(),
        inline_text.trim_end_matches('\n')
    );
}

#[test]
fn conformance_values_have_python_types_and_bits() {
    let (doc, _) = conformance();
    let scalars = doc.get("scalars").unwrap();
    assert!(matches!(scalars.get("none"), Some(PyValue::None)));
    assert!(matches!(scalars.get("true"), Some(PyValue::Bool(true))));
    let ints = scalars.get("ints").unwrap().as_list().unwrap();
    assert!(matches!(ints[4], PyValue::Int(i64::MAX)));
    assert!(matches!(ints[5], PyValue::Int(i64::MIN)));
    let big = scalars.get("big_ints").unwrap().as_list().unwrap();
    assert!(matches!(&big[0], PyValue::BigInt(t) if t == "9223372036854775808"));
    assert!(matches!(&big[1], PyValue::BigInt(t) if t == "-9223372036854775809"));
    let floats = scalars.get("floats").unwrap().as_list().unwrap();
    assert_eq!(floats[1].as_float().unwrap().to_bits(), (-0.0f64).to_bits());
    assert_eq!(floats[6].as_float().unwrap().to_bits(), 1);
    assert_eq!(floats[17].as_float().unwrap(), 9_007_199_254_740_992.0);
    assert_eq!(floats[20].as_float().unwrap(), f64::from(0.1f32));
    let nonfinite = scalars.get("nonfinite").unwrap().as_list().unwrap();
    assert!(nonfinite[0].as_float().unwrap().is_nan());
    assert_eq!(nonfinite[2].as_float().unwrap(), f64::NEG_INFINITY);
    let strings = scalars.get("strings").unwrap().as_list().unwrap();
    assert_eq!(strings[4].as_str(), Some("🏁"));
    assert_eq!(strings[5].as_str(), Some("\u{0}\u{1}\u{1f}"));
    assert_eq!(strings[7].as_str(), Some("\u{8}\u{c}\n\r\t"));
    let containers = doc.get("containers").unwrap();
    assert!(matches!(containers.get("tuple"), Some(PyValue::Tuple(items)) if items.len() == 4));
    let set = containers.get("set_numbers").unwrap();
    let PyValue::Set(numbers) = set else {
        panic!("set_numbers")
    };
    assert!(matches!(numbers[0], PyValue::Int(-1)));
    assert!(matches!(numbers[1], PyValue::Float(x) if x == 0.5));
    let keys = containers
        .get("dict_dollar_key")
        .unwrap()
        .as_dict()
        .unwrap();
    assert_eq!(
        keys.get("$f").and_then(PyValue::as_str),
        Some("not a marker")
    );
    let int_keys = containers.get("dict_int_keys").unwrap().as_dict().unwrap();
    assert_eq!(
        int_keys
            .get_key(&PyValue::Float(3.0))
            .and_then(PyValue::as_str),
        Some("c")
    );
    assert_eq!(
        int_keys
            .get_key(&PyValue::Float(2.5))
            .and_then(PyValue::as_str),
        Some("f")
    );
    let bool_keys = containers.get("dict_bool_key").unwrap().as_dict().unwrap();
    assert_eq!(
        bool_keys
            .get_key(&PyValue::Int(1))
            .and_then(PyValue::as_str),
        Some("t")
    );
    let basis = containers.get("dataclass").unwrap().as_dataclass().unwrap();
    assert_eq!(basis.class, "steering_basis.SteeringBasis");
    assert_eq!(basis.field("ratio").and_then(PyValue::as_float), Some(14.5));
    let references = doc.get("references").unwrap().as_list().unwrap();
    assert!(matches!(references[0], PyValue::Table));
    assert!(matches!(references[1], PyValue::TableChannels));
    assert!(matches!(&references[2], PyValue::TableColumn(name) if name == "Speed"));
    let columns = doc.get("columns").unwrap().as_list().unwrap();
    let with_none = columns[2].as_list().unwrap();
    assert_eq!(with_none.len(), 300);
    assert!(with_none.iter().any(PyValue::is_none));
    assert!(columns[4].as_list().unwrap()[3].is_none());
    assert!(matches!(columns[6].as_list().unwrap()[1], PyValue::Bool(_)));
}

/// A vector store with one column, built from header JSON and data bytes.
fn store(columns_json: &str, data: &[u8]) -> Vec<u8> {
    let header = format!(
        "{{\"format\":\"coach-column-store\",\"version\":1,\"stage\":\"vector\",\"producer\":\"t\",\"codec\":1,\"columns\":[{columns_json}]}}"
    );
    let mut out = b"COACHCS\0".to_vec();
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(header.len() as u32).to_le_bytes());
    out.extend_from_slice(header.as_bytes());
    out.resize(out.len().div_ceil(8) * 8, 0);
    out.extend_from_slice(data);
    out
}

#[test]
fn vector_store_refuses_malformed_blocks() {
    let f64_two: Vec<u8> = [1.5f64, -0.0]
        .iter()
        .flat_map(|x| x.to_le_bytes())
        .collect();
    let good = r#"{"name":"c0","element":"f64","length":2,"data":{"offset":0,"length":16}}"#;
    let parsed = VectorStore::parse(&store(good, &f64_two)).unwrap();
    assert!(matches!(parsed.values("c0").unwrap(), PyValue::List(v) if v.len() == 2));
    assert!(parsed.values("c1").is_err());
    let text = |offsets: &[u64], data: &[u8]| {
        let mut bytes = data.to_vec();
        bytes.resize(8, 0);
        bytes.extend(offsets.iter().flat_map(|o| o.to_le_bytes()));
        bytes
    };
    let cases: Vec<(&str, String, Vec<u8>)> = vec![
        ("short data", r#"{"name":"c0","element":"f64","length":2,"data":{"offset":0,"length":15}}"#.into(), f64_two.clone()),
        ("past the end", r#"{"name":"c0","element":"f64","length":3,"data":{"offset":0,"length":24}}"#.into(), f64_two.clone()),
        ("unaligned", r#"{"name":"c0","element":"bool","length":2,"data":{"offset":4,"length":2}}"#.into(), vec![0; 8]),
        ("negative offset", r#"{"name":"c0","element":"bool","length":1,"data":{"offset":-8,"length":1}}"#.into(), vec![0; 8]),
        ("present length", r#"{"name":"c0","element":"bool","length":2,"present":{"offset":0,"length":1},"data":{"offset":8,"length":2}}"#.into(), vec![1, 0, 0, 0, 0, 0, 0, 0, 1, 1]),
        ("present byte", r#"{"name":"c0","element":"bool","length":2,"present":{"offset":0,"length":2},"data":{"offset":8,"length":2}}"#.into(), vec![1, 2, 0, 0, 0, 0, 0, 0, 1, 1]),
        ("bool byte", r#"{"name":"c0","element":"bool","length":1,"data":{"offset":0,"length":1}}"#.into(), vec![2]),
        ("unknown element", r#"{"name":"c0","element":"f32","length":1,"data":{"offset":0,"length":4}}"#.into(), vec![0; 8]),
        ("utf8 without offsets", r#"{"name":"c0","element":"utf8","length":1,"data":{"offset":0,"length":1}}"#.into(), vec![b'a']),
        ("offsets on f64", r#"{"name":"c0","element":"f64","length":1,"data":{"offset":0,"length":8},"text_offsets":{"offset":8,"length":16}}"#.into(), vec![0; 24]),
        ("offsets past text", r#"{"name":"c0","element":"utf8","length":1,"data":{"offset":0,"length":2},"text_offsets":{"offset":8,"length":16}}"#.into(), text(&[0, 3], b"ab")),
        ("offsets descending", r#"{"name":"c0","element":"utf8","length":2,"data":{"offset":0,"length":2},"text_offsets":{"offset":8,"length":24}}"#.into(), text(&[0, 2, 1], b"ab")),
        ("offsets not from 0", r#"{"name":"c0","element":"utf8","length":1,"data":{"offset":0,"length":2},"text_offsets":{"offset":8,"length":16}}"#.into(), text(&[1, 2], b"ab")),
        ("bad utf8", r#"{"name":"c0","element":"utf8","length":1,"data":{"offset":0,"length":1},"text_offsets":{"offset":8,"length":16}}"#.into(), text(&[0, 1], &[0xff])),
        ("repeated name", format!("{good},{good}"), f64_two.clone()),
        ("no name", r#"{"element":"f64","length":0,"data":{"offset":0,"length":0}}"#.into(), vec![]),
    ];
    for (what, columns, data) in cases {
        assert!(
            VectorStore::parse(&store(&columns, &data)).is_err(),
            "accepted: {what}"
        );
    }
    let mut bad = store(good, &f64_two);
    bad[0] = b'X';
    assert!(VectorStore::parse(&bad).is_err(), "magic");
    let mut bad = store(good, &f64_two);
    bad[8] = 2;
    assert!(VectorStore::parse(&bad).is_err(), "version");
    for (from, to) in [("\"vector\"", "\"decode\""), ("\"codec\":1", "\"codec\":2")] {
        let text = String::from_utf8(store(good, &f64_two)).unwrap_or_default();
        assert!(
            VectorStore::parse(text.replace(from, to).as_bytes()).is_err(),
            "{to}"
        );
    }
    let truncated = store(good, &f64_two);
    assert!(
        VectorStore::parse(&truncated[..truncated.len() - 1]).is_err(),
        "truncated"
    );
}

#[test]
fn decoder_refuses_what_the_codec_never_writes() {
    for text in [
        "NaN",
        "Infinity",
        "[1,]",
        "{\"a\":1,}",
        "01",
        "1.",
        ".5",
        "+1",
        "1e400",
        "9223372036854775808",
        "{\"a\":1,\"a\":2}",
        "{\"$f\":\"NaN\"}",
        "{\"$int\":\"012\"}",
        "{\"$int\":\"-0\"}",
        "{\"$int\":\"1e3\"}",
        "{\"$column\":\"c0\"}",
        "{\"$unknown\":1}",
        "{\"$table\":2}",
        "{\"$tuple\":1}",
        "{\"$dict\":[[1]]}",
        "{\"$dataclass\":\"m.C\",\"fields\":{},\"x\":1}",
        "{\"$dataclass\":\"m.C\",\"fields\":[]}",
        "{\"$f\":\"nan\",\"$int\":\"1\"}",
        "\"\\ud800\"",
        "\"\\ud800\\u0041\"",
        "\"a\u{1}\"",
        "\"\\x\"",
        "[1] 2",
        "",
    ] {
        assert!(codec::decode(text, None).is_err(), "accepted {text:?}");
    }
    let deep = "[".repeat(300) + &"]".repeat(300);
    assert!(codec::decode(&deep, None).is_err(), "nesting");
    let fine = "[".repeat(200) + &"]".repeat(200);
    assert!(codec::decode(&fine, None).is_ok());
    assert!(matches!(
        codec::decode("{\"$int\":\"5\"}", None),
        Ok(PyValue::Int(5))
    ));
    assert!(
        matches!(codec::decode(" \"\\ud83c\\udfc1\" ", None), Ok(PyValue::Str(s)) if s == "🏁")
    );
    assert!(
        matches!(codec::decode("-0.0", None), Ok(PyValue::Float(x)) if x.to_bits() == (-0.0f64).to_bits())
    );
    assert!(matches!(codec::decode("-0", None), Ok(PyValue::Int(0))));
}

#[test]
fn encoder_follows_the_python_rules() {
    let set = PyValue::set(vec![
        PyValue::Float(2.0),
        PyValue::Int(-1),
        PyValue::Int(2),
        PyValue::Bool(true),
        PyValue::Float(0.5),
    ])
    .unwrap();
    // 2.0 and 2 are one element (the first kept), True is 1.
    assert_eq!(
        codec::to_json(&set).unwrap(),
        "{\"$set\":[-1,0.5,true,2.0]}"
    );
    let unsorted = PyValue::Set(vec![PyValue::Str("b".into()), PyValue::Str("a".into())]);
    assert_eq!(
        codec::to_json(&unsorted).unwrap(),
        "{\"$set\":[\"a\",\"b\"]}"
    );
    assert!(PyValue::set(vec![PyValue::Int(1), PyValue::Str("a".into())]).is_err());
    assert!(codec::to_json(&PyValue::Set(vec![PyValue::None, PyValue::Int(1)])).is_err());
    let dict = Dict::new()
        .with(1i64, "a")
        .with(1.0, "b")
        .with(PyValue::None, PyValue::None);
    assert_eq!(dict.len(), 2);
    assert_eq!(
        codec::to_json(&dict.into()).unwrap(),
        "{\"$dict\":[[1,\"b\"],[null,null]]}"
    );
    let dict = Dict::new()
        .with("z", 1i64)
        .with("a", PyValue::Tuple(vec![]))
        .with("z", 2i64);
    assert_eq!(
        codec::to_json(&dict.into()).unwrap(),
        "{\"z\":2,\"a\":{\"$tuple\":[]}}"
    );
    let mut sink = ColumnSink::new();
    let list = PyValue::List((0..256).map(|i| PyValue::Int(i * 3)).collect());
    let short = PyValue::List((0..255).map(|i| PyValue::Int(i * 3)).collect());
    let text = codec::encode(
        &PyValue::List(vec![short, list.clone(), list]),
        Some(&mut sink),
    )
    .unwrap();
    assert!(text.ends_with(",{\"$column\":\"c0\"},{\"$column\":\"c1\"}]"));
    let bytes = sink.to_bytes("t").unwrap().unwrap();
    let back = VectorStore::parse(&bytes).unwrap();
    assert!(
        matches!(back.values("c1").unwrap(), PyValue::List(v) if matches!(v[255], PyValue::Int(765)))
    );
    assert!(ColumnSink::new().to_bytes("t").unwrap().is_none());
    let floats = PyValue::List(vec![
        PyValue::Float(1e16),
        PyValue::Float(1e-5),
        PyValue::Float(0.1),
        PyValue::Float(f64::NAN),
        PyValue::Float(-f64::INFINITY),
        PyValue::BigInt("-9223372036854775809".into()),
    ]);
    assert_eq!(
        codec::to_json(&floats).unwrap(),
        "[1e+16,1e-05,0.1,{\"$f\":\"nan\"},{\"$f\":\"-inf\"},{\"$int\":\"-9223372036854775809\"}]"
    );
}

#[test]
fn values_follow_python_semantics() {
    assert_eq!(PyValue::Str(" 1_0.5 ".into()).finite().unwrap(), Some(10.5));
    assert_eq!(PyValue::Str("nan".into()).finite().unwrap(), None);
    assert_eq!(PyValue::Str("x".into()).finite().unwrap(), None);
    assert_eq!(PyValue::Bool(true).finite().unwrap(), Some(1.0));
    assert_eq!(PyValue::Float(f64::INFINITY).finite().unwrap(), None);
    assert_eq!(PyValue::List(vec![]).finite().unwrap(), None);
    assert_eq!(
        PyValue::BigInt("10000000000000000000".into())
            .finite()
            .unwrap(),
        Some(1e19)
    );
    let huge = PyValue::BigInt(format!("1{}", "0".repeat(400)));
    assert!(
        matches!(huge.finite(), Err(KernelError::Raises { class, .. }) if class == "OverflowError")
    );
    assert!(!PyValue::Float(-0.0).truthy().unwrap());
    assert!(PyValue::Float(f64::NAN).truthy().unwrap());
    assert!(!PyValue::Str(String::new()).truthy().unwrap());
    assert!(PyValue::TableColumn("Speed".into()).truthy().is_err());
    // same(): types and bits, any NaN equals any NaN, key order.
    assert!(same(&PyValue::Int(1), &PyValue::Float(1.0)).is_some());
    assert!(same(&PyValue::Int(1), &PyValue::Bool(true)).is_some());
    assert!(same(&PyValue::Float(0.0), &PyValue::Float(-0.0)).is_some());
    assert!(same(&PyValue::Float(f64::NAN), &PyValue::Float(-f64::NAN)).is_none());
    let a: PyValue = Dict::new().with("x", 1i64).with("y", 2i64).into();
    let b: PyValue = Dict::new().with("y", 2i64).with("x", 1i64).into();
    assert!(same(&a, &b).is_some());
    let nested_a: PyValue = Dict::new().with("laps", vec![a.clone(), a.clone()]).into();
    let nested_b: PyValue = Dict::new()
        .with(
            "laps",
            vec![a.clone(), Dict::new().with("x", 1i64).with("y", 2.0).into()],
        )
        .into();
    let difference = same(&nested_a, &nested_b).unwrap();
    assert_eq!(difference.path, "[\"laps\"][1][\"y\"]");
}

#[test]
fn call_binding_follows_python() {
    let call = Call {
        args: vec![PyValue::Table, PyValue::Float(0.5)],
        kwargs: vec![("mandatory_recorded".into(), PyValue::Bool(true))],
    };
    let bound = call
        .bind(
            "e",
            &["table", "timer_epsilon_s"],
            &["mandatory_recorded", "optional_recorded"],
        )
        .unwrap();
    bound.require_table(&call, "table").unwrap();
    assert_eq!(bound.slot("mandatory_recorded"), Some(Slot::Keyword(0)));
    assert!(bound.get(&call, "optional_recorded").is_none());
    assert!(bound.require(&call, "optional_recorded").is_err());
    assert!(bound.require_table(&call, "timer_epsilon_s").is_err());
    assert!(
        call.bind("e", &["table"], &["mandatory_recorded"]).is_err(),
        "too many positional"
    );
    assert!(
        call.bind("e", &["table", "x"], &[]).is_err(),
        "unknown keyword"
    );
    let twice = Call {
        args: vec![PyValue::Table],
        kwargs: vec![("table".into(), PyValue::Table)],
    };
    assert!(twice.bind("e", &["table"], &[]).is_err(), "given twice");
    assert_eq!(
        call.argument("args.1").and_then(PyValue::as_float),
        Some(0.5)
    );
    assert!(call.argument("kwargs.mandatory_recorded").is_some());
    assert!(call.argument("args.2").is_none());
}

#[test]
fn entry_table_is_consistent() {
    let units: Vec<&str> = UNITS.iter().map(|(name, _)| *name).collect();
    assert_eq!(units, ["table", "laps", "traces", "sections", "field"]);
    let mut seen = BTreeSet::new();
    for entry in dispatch::entries() {
        let (module, name) = entry.name.split_once('.').expect("module.name");
        assert!(
            !module.is_empty() && !name.is_empty() && !name.contains('.'),
            "{}",
            entry.name
        );
        assert!(seen.insert(entry.name), "{} registered twice", entry.name);
        assert!(dispatch::find(entry.name).is_some());
    }
    assert!(dispatch::find("analysis_engine.no_such_entry").is_none());
}

/// A session over the committed fixture, decoded natively (the decode stage is
/// byte-identical to the oracle's, phase 1).
fn fixture_session() -> Session {
    let ibt = vectors::repository()
        .join("test-data")
        .join("ibt")
        .join("synthetic-race.ibt");
    let decoded = coach_analysis::decode(&ibt, None, None).unwrap();
    let out = std::env::temp_dir().join(format!("coach-kernels-i0-{}.ccs", std::process::id()));
    coach_analysis::store::write(&decoded, &out).unwrap();
    let store = coach_analysis::ColumnStore::read(&out).unwrap();
    let _ = std::fs::remove_file(&out);
    let names: Vec<PyValue> = store
        .header
        .columns
        .iter()
        .map(|c| PyValue::from(c.name.as_str()))
        .collect();
    let length = store.header.sampling.sample_count as i64;
    let meta = Dict::new()
        .with("metadata", Dict::new())
        .with("session_info", Dict::new())
        .with("variables", PyValue::List(vec![]))
        .with("available_variables", PyValue::List(vec![]))
        .with("channel_units", Dict::new().with("Speed", "m/s"))
        .with("length", length)
        .with("channel_names", names.clone());
    let table = TableMeta::from_value(&meta.clone().into()).unwrap();
    assert_eq!(
        table.channel_units,
        [("Speed".to_owned(), "m/s".to_owned())]
    );
    let mut wrong = TableMeta::from_value(&meta.into()).unwrap();
    wrong.channel_names.reverse();
    let session = Session::new(store.clone(), table.clone(), PyValue::None).unwrap();
    if names.len() > 1 {
        assert!(
            Session::new(store.clone(), wrong, PyValue::None).is_err(),
            "column order"
        );
    }
    let mut short = table;
    short.length += 1;
    assert!(Session::new(store, short, PyValue::None).is_err(), "length");
    session
}

#[test]
fn session_caches_handles_and_access() {
    let session = fixture_session();
    let builds = std::cell::Cell::new(0);
    let first = session
        .cached(|_| {
            builds.set(builds.get() + 1);
            Ok(vec![1u8, 2])
        })
        .unwrap();
    let again = session.cached::<Vec<u8>>(|_| unreachable!()).unwrap();
    assert!(std::rc::Rc::ptr_eq(&first, &again));
    assert_eq!(builds.get(), 1);
    let nested = session
        .cached(|s| Ok(*s.cached(|_| Ok(7u64))? as u32 + 1))
        .unwrap();
    assert_eq!(*nested, 8);
    assert!(
        session
            .cached::<u16>(|_| Err(KernelError::internal("no")))
            .is_err()
    );
    assert_eq!(*session.cached(|_| Ok(3u16)).unwrap(), 3);
    let handle = session.put_handle(String::from("setup series"));
    assert!(matches!(handle, PyValue::Handle(0)));
    assert_eq!(
        session.handle::<String>(&handle).unwrap().as_str(),
        "setup series"
    );
    assert!(session.handle::<u8>(&handle).is_err());
    assert!(session.handle::<String>(&PyValue::Handle(9)).is_err());
    assert_eq!(codec::to_json(&handle).unwrap(), "{\"$handle\":0}");
    assert!(matches!(
        codec::decode("{\"$handle\":0}", None),
        Ok(PyValue::Handle(0))
    ));
    let record = session.access().clone();
    record.record("Speed");
    record.record("Lap");
    record.record("Speed");
    let mut call = Call::default();
    let answer = session.call("analysis_engine.no_such_entry", &mut call);
    assert!(matches!(answer.outcome, Outcome::Error(ref e) if e.contains("not provided")));
    assert!(
        answer.accessed.is_empty(),
        "the record is cleared at the start of a call"
    );
    record.record("Speed");
    record.record("Lap");
    assert_eq!(
        session.access().take().into_iter().collect::<Vec<_>>(),
        ["Lap", "Speed"]
    );
}

#[test]
fn every_recorded_document_reencodes_and_replays_against_itself() {
    let mut documents = 0usize;
    for folder in vectors::recordings() {
        let recording = folder.file_name().unwrap().to_string_lossy().into_owned();
        Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
        for unit in std::fs::read_dir(&folder)
            .unwrap()
            .filter_map(Result::ok)
            .map(|e| e.path())
        {
            if !unit.is_dir() {
                continue;
            }
            vectors::verify_index(&unit).unwrap_or_else(|e| panic!("{e}"));
            for (entry, file) in vectors::call_files(&unit) {
                let document = vectors::load_document(&file).unwrap_or_else(|e| panic!("{e}"));
                let mut sink = ColumnSink::new();
                let text = codec::encode(&document.value, Some(&mut sink)).unwrap();
                assert_eq!(
                    text,
                    document.text.trim_end_matches('\n'),
                    "{}",
                    file.display()
                );
                let bytes = sink.to_bytes("emit_kernel_vectors.py").unwrap();
                assert_eq!(
                    bytes,
                    document.store,
                    "{}",
                    file.with_extension("ccs").display()
                );
                let vector =
                    vectors::load_call(&recording, &entry, &file).unwrap_or_else(|e| panic!("{e}"));
                self_check(&vector);
                documents += 1;
            }
        }
    }
    println!(
        "{documents} recorded call documents re-encoded byte for byte ({})",
        vectors::platform()
    );
}

/// The replay comparison accepts the record itself and rejects each kind of difference.
fn self_check(vector: &vectors::CallVector) {
    let outcome = match &vector.recorded {
        vectors::Recorded::Value(value) => Outcome::Value(value.clone()),
        vectors::Recorded::Raises { class, message } => Outcome::Raises {
            class: class.clone(),
            message: message.clone(),
        },
    };
    let mut after = vector.call.clone();
    for (key, value) in &vector.mutated {
        let index = key
            .strip_prefix("args.")
            .and_then(|i| i.parse::<usize>().ok());
        let slot = match index {
            Some(i) => Slot::Positional(i),
            None => Slot::Keyword(
                after
                    .kwargs
                    .iter()
                    .position(|(k, _)| Some(k.as_str()) == key.strip_prefix("kwargs."))
                    .unwrap(),
            ),
        };
        *after.value_mut(slot) = value.clone();
    }
    let answer = Answer {
        outcome: outcome.clone(),
        accessed: vector.accessed.clone(),
    };
    vectors::check(vector, &after, &answer).unwrap_or_else(|e| panic!("self-check: {e}"));
    let mut extra = vector.accessed.clone();
    extra.insert("NoSuchChannel".into());
    assert!(
        vectors::check(
            vector,
            &after,
            &Answer {
                outcome: outcome.clone(),
                accessed: extra
            }
        )
        .is_err()
    );
    if let Outcome::Value(value) = &outcome {
        let changed = Answer {
            outcome: Outcome::Value(PyValue::List(vec![value.clone()])),
            accessed: vector.accessed.clone(),
        };
        assert!(vectors::check(vector, &after, &changed).is_err());
    }
    if !vector.mutated.is_empty() {
        assert!(
            vectors::check(vector, &vector.call, &answer).is_err(),
            "a missing mutation passed"
        );
    }
    let error = Answer {
        outcome: Outcome::Error("x".into()),
        accessed: vector.accessed.clone(),
    };
    assert!(vectors::check(vector, &after, &error).is_err());
}
