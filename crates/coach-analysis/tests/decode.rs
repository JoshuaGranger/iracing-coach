//! The decode stage against the Python oracle.
//!
//! - `test-data/ibt/synthetic-race.ibt` against its committed decode golden
//!   (`synthetic-race.decode-golden.json`, written by `tools/parity/emit_ibt_golden.py`).
//! - An edge-type recording built here (every SDK type, arrays, char text with a BOM and with
//!   cp1252 bytes, NaN and signalling-NaN floats, bool bytes other than 0/1) against values
//!   stated by the reader's rules, and against `emit_ibt_golden.emit` itself when a Python 3.12
//!   is found (`COACH_PYTHON`, the bundled Windows oracle, or `python3.12` on PATH).
//! - The column store round trip and its version checks.
//! - The sampling plan against CPython vectors (`tests/vectors/sampling.json`).

use std::path::{Path, PathBuf};
use std::process::Command;

use coach_analysis::golden::{golden, matrix_sha256};
use coach_analysis::store::{self, ColumnStore};
use coach_analysis::{DecodeError, DecodeRequest, Element, SamplingPlan, Value, decode};
use coach_ibt::{IbtError, IbtFile};
use serde_json::Value as Json;
use sha2::{Digest, Sha256};

const WINDOWS_ORACLE: &str =
    r"C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe";

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
}

fn fixture(name: &str) -> PathBuf {
    repository().join("test-data").join("ibt").join(name)
}

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("coach-analysis-tests-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

fn read_json(path: &Path) -> Json {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn units_of(path: &Path) -> impl Fn(&str) -> String {
    let vars = IbtFile::open(path).unwrap().vars;
    move |name: &str| {
        vars.get(name)
            .map(|var| var.unit.clone())
            .unwrap_or_default()
    }
}

/// Compares a native golden with a Python one field by field; float text by value.
fn assert_golden_matches(native: &Json, oracle: &Json, label: &str) {
    for key in [
        "native_tick_rate_hz",
        "sample_rate_hz",
        "source_record_count",
        "sample_count",
        "sample_indices",
        "variables",
        "matrix_sha256",
    ] {
        assert_eq!(native[key], oracle[key], "{label}: {key}");
    }
    assert!(
        oracle["sample_rate_hz"].is_f64(),
        "{label}: Python rate is a float"
    );
    assert!(
        native["sample_rate_hz"].is_f64(),
        "{label}: native rate is a float"
    );
}

// ---- committed fixture ----

#[test]
fn synthetic_race_matches_the_committed_golden() {
    let path = fixture("synthetic-race.ibt");
    let expected = read_json(&fixture("synthetic-race.decode-golden.json"));
    for (key, target) in [("full_rate", None), ("downsampled_20hz", Some(20.0))] {
        let decoded = decode(&path, None, target).unwrap();
        let native = serde_json::to_value(golden(&decoded, &units_of(&path))).unwrap();
        assert_golden_matches(&native, &expected[key], key);
        // Spot values: Python repr text for floats, plain JSON otherwise.
        for (name, spots) in expected[key]["spot_values"].as_object().unwrap() {
            let column = decoded.columns.iter().find(|c| &c.name == name).unwrap();
            for spot in spots.as_array().unwrap() {
                let index = spot[0].as_u64().unwrap() as usize;
                let value = column.value(index, 0).unwrap();
                match (&spot[1], value) {
                    (Json::String(text), Value::Float32(v)) => {
                        assert_eq!(
                            text.parse::<f64>().unwrap().to_bits(),
                            f64::from(v).to_bits(),
                            "{name}[{index}]"
                        );
                    }
                    (Json::Number(n), Value::Float(v)) => assert_eq!(n.as_f64().unwrap(), v),
                    (Json::Number(n), Value::Int(v)) => assert_eq!(n.as_i64().unwrap(), v),
                    (Json::Bool(b), Value::Bool(v)) => assert_eq!(*b, v),
                    (other, value) => panic!("{name}[{index}]: {other} vs {value:?}"),
                }
            }
        }
    }
}

#[test]
fn truncated_recording_is_rejected_by_the_shared_layout_parser() {
    let error = decode(&fixture("truncated-race.ibt"), None, None).unwrap_err();
    assert!(
        matches!(error, DecodeError::Ibt(IbtError::Truncated { .. })),
        "{error}"
    );
}

#[test]
fn channel_selection_follows_normalise_channels() {
    let path = fixture("synthetic-race.ibt");
    let wanted: Vec<String> = ["Speed", "Lap", "Speed", "OnPitRoad"]
        .map(String::from)
        .to_vec();
    let decoded = decode(&path, Some(&wanted), None).unwrap();
    let names: Vec<&str> = decoded.columns.iter().map(|c| c.name.as_str()).collect();
    assert_eq!(names, ["Speed", "Lap", "OnPitRoad"]);
    assert!(!decoded.all_channels);

    let none = decode(&path, Some(&[]), None).unwrap();
    assert!(none.columns.is_empty());
    assert_eq!(none.sample_indices.len(), 120);

    let missing: Vec<String> = ["Nope", "Speed", "Nope"].map(String::from).to_vec();
    let error = decode(&path, Some(&missing), None).unwrap_err().to_string();
    assert_eq!(
        error,
        "requested channel(s) not present: Nope, Nope; available channels begin: \
         Brake, Lap, LapDistPct, OnPitRoad, SessionNum, SessionTime, Speed, Throttle"
    );
    let empty = decode(&path, Some(&[String::new()]), None).unwrap_err();
    assert!(matches!(empty, DecodeError::EmptyChannelName));
    assert!(matches!(
        decode(&path, None, Some(0.0)).unwrap_err(),
        DecodeError::TargetHz
    ));
}

// ---- edge types ----

struct Var {
    type_code: i32,
    offset: i32,
    count: i32,
    name: &'static str,
    unit: &'static str,
}

const EDGE_VARS: &[Var] = &[
    Var {
        type_code: 1,
        offset: 0,
        count: 1,
        name: "B",
        unit: "",
    },
    Var {
        type_code: 1,
        offset: 1,
        count: 3,
        name: "BArr",
        unit: "",
    },
    Var {
        type_code: 2,
        offset: 4,
        count: 1,
        name: "I",
        unit: "",
    },
    Var {
        type_code: 3,
        offset: 8,
        count: 1,
        name: "Bits",
        unit: "irsdk_Flags",
    },
    Var {
        type_code: 4,
        offset: 12,
        count: 1,
        name: "F",
        unit: "m/s",
    },
    Var {
        type_code: 4,
        offset: 16,
        count: 3,
        name: "FArr",
        unit: "",
    },
    Var {
        type_code: 5,
        offset: 32,
        count: 1,
        name: "D",
        unit: "s",
    },
    Var {
        type_code: 0,
        offset: 40,
        count: 16,
        name: "Text",
        unit: "",
    },
    Var {
        type_code: 2,
        offset: 56,
        count: 2,
        name: "IArr",
        unit: "",
    },
    Var {
        type_code: 5,
        offset: 64,
        count: 2,
        name: "DArr",
        unit: "",
    },
];
const EDGE_ROW: usize = 80;
const EDGE_RECORDS: usize = 13;
const QUIET_NAN: u32 = 0x7FC0_0001;
const SIGNALLING_NAN: u32 = 0x7F80_0001;
const FLOAT_BITS: [u32; 9] = [
    0x3F00_0000, // 0.5
    0x8000_0000, // -0.0
    QUIET_NAN,
    SIGNALLING_NAN,
    0x7F80_0000, // inf
    0xFF80_0000, // -inf
    0x0000_0001, // smallest subnormal
    0x7F7F_FFFF, // f32::MAX
    0x4053_3333, // 3.3
];
const TEXTS: [&[u8]; 6] = [
    b"hello\0garbage!!!",
    b"\xEF\xBB\xBFbom",
    b"caf\xE9 \x80\x81",
    b"",
    b"\xC3\xA9t\xC3\xA9",
    b"abcdefghijklmnop",
];

fn put(out: &mut [u8], at: usize, bytes: &[u8]) {
    out[at..at + bytes.len()].copy_from_slice(bytes);
}

fn fixed(text: &str, len: usize) -> Vec<u8> {
    let mut bytes = text.as_bytes().to_vec();
    bytes.resize(len, 0);
    bytes
}

/// A valid IBT in the layout `tools/generate_fixtures.py` writes.
fn build_edge_ibt(path: &Path) {
    let yaml = b"WeekendInfo:\n  TrackName: Edge Types\n  EventType: Test\n\0";
    let info_offset = 144usize;
    let var_offset = (info_offset + yaml.len()).div_ceil(16) * 16;
    let sample_offset = (var_offset + EDGE_VARS.len() * 144).div_ceil(16) * 16;
    let mut out = vec![0u8; sample_offset + EDGE_RECORDS * EDGE_ROW];
    let header: [i32; 16] = [
        2,
        1,
        60,
        1,
        yaml.len() as i32,
        info_offset as i32,
        EDGE_VARS.len() as i32,
        var_offset as i32,
        1,
        EDGE_ROW as i32,
        0,
        0,
        EDGE_RECORDS as i32 - 1,
        sample_offset as i32,
        0,
        0,
    ];
    for (index, value) in header.iter().enumerate() {
        put(&mut out, index * 4, &value.to_le_bytes());
    }
    put(&mut out, 112, &1_780_000_000_i64.to_le_bytes());
    put(&mut out, 120, &0.0_f64.to_le_bytes());
    put(&mut out, 128, &0.2_f64.to_le_bytes());
    put(&mut out, 136, &1_i32.to_le_bytes());
    put(&mut out, 140, &(EDGE_RECORDS as i32).to_le_bytes());
    put(&mut out, info_offset, yaml);
    for (index, var) in EDGE_VARS.iter().enumerate() {
        let at = var_offset + index * 144;
        put(&mut out, at, &var.type_code.to_le_bytes());
        put(&mut out, at + 4, &var.offset.to_le_bytes());
        put(&mut out, at + 8, &var.count.to_le_bytes());
        put(&mut out, at + 16, &fixed(var.name, 32));
        put(&mut out, at + 48, &fixed("edge", 64));
        put(&mut out, at + 112, &fixed(var.unit, 32));
    }
    for record in 0..EDGE_RECORDS {
        let row = sample_offset + record * EDGE_ROW;
        let r = record as i32;
        out[row] = [0u8, 1, 2, 255][record % 4];
        put(&mut out, row + 1, &[(record % 3) as u8, 0, 200]);
        put(&mut out, row + 4, &(-r * 1000 - 7).to_le_bytes());
        put(&mut out, row + 8, &(u32::MAX - record as u32).to_le_bytes());
        put(
            &mut out,
            row + 12,
            &FLOAT_BITS[record % FLOAT_BITS.len()].to_le_bytes(),
        );
        put(&mut out, row + 16, &(record as f32).to_le_bytes());
        put(&mut out, row + 20, &0xFFC0_0000_u32.to_le_bytes());
        put(&mut out, row + 24, &(1.0_f32 / 3.0).to_le_bytes());
        let d = if record == 5 {
            f64::NAN
        } else {
            f64::from(r) / 7.0
        };
        put(&mut out, row + 32, &d.to_le_bytes());
        put(
            &mut out,
            row + 40,
            &fixed_bytes(TEXTS[record % TEXTS.len()], 16),
        );
        put(&mut out, row + 56, &r.to_le_bytes());
        put(&mut out, row + 60, &(-r).to_le_bytes());
        put(&mut out, row + 64, &(f64::from(r) * 0.1).to_le_bytes());
        put(&mut out, row + 72, &(-1e300_f64).to_le_bytes());
    }
    std::fs::write(path, out).unwrap();
}

fn fixed_bytes(bytes: &[u8], len: usize) -> Vec<u8> {
    let mut out = bytes.to_vec();
    out.resize(len, 0);
    out
}

fn column<'a>(decoded: &'a coach_analysis::Decoded, name: &str) -> &'a coach_analysis::Column {
    decoded.columns.iter().find(|c| c.name == name).unwrap()
}

#[test]
fn edge_types_decode_as_python_reads_them() {
    let path = scratch("edge-types.ibt");
    build_edge_ibt(&path);
    let decoded = decode(&path, None, None).unwrap();
    assert_eq!(
        decoded.sample_indices,
        (0..EDGE_RECORDS as u32).collect::<Vec<_>>()
    );

    // bool: any nonzero byte is True (struct "?"), elements stay 0/1 in the store.
    let b = column(&decoded, "B");
    let bools: Vec<bool> = (0..EDGE_RECORDS)
        .map(|i| matches!(b.value(i, 0), Some(Value::Bool(true))))
        .collect();
    assert_eq!(&bools[..4], &[false, true, true, true]);
    assert!(b.data.iter().all(|&byte| byte <= 1));
    let barr = column(&decoded, "BArr");
    assert_eq!(barr.value(1, 0), Some(Value::Bool(true)));
    assert_eq!(barr.value(1, 2), Some(Value::Bool(true)));
    assert_eq!(barr.value(3, 0), Some(Value::Bool(false)));

    assert_eq!(column(&decoded, "I").value(2, 0), Some(Value::Int(-2007)));
    assert_eq!(
        column(&decoded, "Bits").value(0, 0),
        Some(Value::Int(i64::from(u32::MAX)))
    );

    // floats keep their stored bits, NaN payloads and signalling NaNs included.
    let f = column(&decoded, "F");
    for (record, bits) in FLOAT_BITS.iter().enumerate() {
        match f.value(record, 0) {
            Some(Value::Float32(v)) => assert_eq!(v.to_bits(), *bits),
            other => panic!("F[{record}]: {other:?}"),
        }
    }
    match column(&decoded, "D").value(5, 0) {
        Some(Value::Float(v)) => assert!(v.is_nan()),
        other => panic!("D[5]: {other:?}"),
    }
    assert_eq!(column(&decoded, "IArr").value(4, 1), Some(Value::Int(-4)));
    assert_eq!(
        column(&decoded, "DArr").value(4, 1),
        Some(Value::Float(-1e300))
    );
    assert_eq!(column(&decoded, "FArr").count, 3);

    // char: bytes before the first NUL; UTF-8 (a BOM kept), else cp1252 with U+FFFD.
    let text = column(&decoded, "Text");
    assert_eq!(text.element, Element::Utf8);
    let texts: Vec<&str> = (0..6).map(|i| text.text(i).unwrap()).collect();
    assert_eq!(
        texts,
        [
            "hello",
            "\u{FEFF}bom",
            "caf\u{E9} \u{20AC}\u{FFFD}",
            "",
            "\u{E9}t\u{E9}",
            "abcdefghijklmnop"
        ]
    );

    // A 7 Hz plan (step 60/7) and a plan at the tick rate.
    let seven = decode(&path, None, Some(7.0)).unwrap();
    assert_eq!(seven.sample_indices, [0, 8]);
    assert_eq!(seven.plan.output_rate, 7.0);
    let at_tick = decode(&path, None, Some(60.0)).unwrap();
    assert_eq!(at_tick.sample_indices.len(), EDGE_RECORDS);
}

fn python() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("COACH_PYTHON") {
        return Some(PathBuf::from(path));
    }
    if cfg!(windows) && Path::new(WINDOWS_ORACLE).is_file() {
        return Some(PathBuf::from(WINDOWS_ORACLE));
    }
    let found = Command::new("python3.12").arg("--version").output().ok()?;
    found.status.success().then(|| PathBuf::from("python3.12"))
}

#[test]
fn edge_types_match_the_python_oracle_when_available() {
    let Some(python) = python() else {
        eprintln!("skipped: no Python 3.12 (set COACH_PYTHON)");
        return;
    };
    let path = scratch("edge-oracle.ibt");
    build_edge_ibt(&path);
    let rates: [Option<f64>; 5] = [None, Some(20.0), Some(7.0), Some(59.94), Some(61.0)];
    let script = "import sys, json\n\
        sys.path.insert(0, sys.argv[1])\n\
        import emit_ibt_golden as e\n\
        rates = json.loads(sys.argv[3])\n\
        out = []\n\
        for rate in rates:\n\
        \x20   g = e.emit(sys.argv[2], rate)\n\
        \x20   g.pop('spot_values')\n\
        \x20   out.append(g)\n\
        print(json.dumps(out))\n";
    let output = Command::new(&python)
        .args(["-X", "utf8", "-c", script])
        .arg(repository().join("tools").join("parity"))
        .arg(&path)
        .arg(serde_json::to_string(&rates).unwrap())
        .output()
        .expect("run the Python oracle");
    assert!(
        output.status.success(),
        "oracle failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let oracle: Json = serde_json::from_slice(&output.stdout).unwrap();
    let units = units_of(&path);
    for (index, rate) in rates.iter().enumerate() {
        let decoded = decode(&path, None, *rate).unwrap();
        let native = serde_json::to_value(golden(&decoded, &units)).unwrap();
        assert_golden_matches(&native, &oracle[index], &format!("{rate:?}"));
    }
}

// ---- store ----

#[test]
fn store_round_trips_every_column() {
    let path = scratch("edge-store.ibt");
    build_edge_ibt(&path);
    let decoded = decode(&path, None, Some(20.0)).unwrap();
    let out = scratch("edge-store.ccs");
    let bytes = store::write(&decoded, &out).unwrap();
    assert_eq!(bytes, std::fs::metadata(&out).unwrap().len());
    assert_eq!(bytes % 8, 0);
    let read = ColumnStore::read(&out).unwrap();
    assert_eq!(read.sample_indices(), decoded.sample_indices);
    assert_eq!(read.header.columns.len(), decoded.columns.len());
    for (index, column) in decoded.columns.iter().enumerate() {
        assert_eq!(read.header.columns[index].data.offset % 8, 0);
        assert_eq!(&read.column(index).unwrap(), column);
    }
    assert_eq!(read.header.sampling.sample_rate_hz, 20.0);
    assert_eq!(read.header.sampling.step, 3.0);
    assert_eq!(read.header.channel_mode, "all");
    assert_eq!(read.header.source.record_count, EDGE_RECORDS as u32);

    let mut wrong_version = std::fs::read(&out).unwrap();
    wrong_version[8] = 2;
    assert!(
        ColumnStore::parse(wrong_version)
            .unwrap_err()
            .to_string()
            .contains("version 2")
    );
    let mut wrong_magic = std::fs::read(&out).unwrap();
    wrong_magic[0] = b'X';
    assert!(ColumnStore::parse(wrong_magic).is_err());
    let truncated = std::fs::read(&out).unwrap()[..64].to_vec();
    assert!(ColumnStore::parse(truncated).is_err());
}

/// An edit of a store header (JSON).
type HeaderEdit = Box<dyn FnOnce(&mut Json)>;

/// The store with its header JSON edited; the data section is unchanged (offsets are relative).
fn with_header(store: &[u8], edit: impl FnOnce(&mut Json)) -> Vec<u8> {
    let length = u32::from_le_bytes(store[12..16].try_into().unwrap()) as usize;
    let mut header: Json = serde_json::from_slice(&store[16..16 + length]).unwrap();
    edit(&mut header);
    let text = serde_json::to_vec(&header).unwrap();
    let mut out = store[..12].to_vec();
    out.extend((text.len() as u32).to_le_bytes());
    out.extend(&text);
    out.resize(out.len().div_ceil(8) * 8, 0);
    out.extend(&store[(16 + length).div_ceil(8) * 8..]);
    out
}

/// Review findings 7 and 12 (Codex Sol phase-1 review): a block that is not exactly the length
/// its header implies (a partial index or offset word, a short column, a wrong sample count),
/// an unaligned block, another stage, a type stored as another element, a zero count, or
/// misplaced or broken text offsets are rejected at parse, never silently truncated.
#[test]
fn store_rejects_inexact_blocks_and_shapes() {
    let path = scratch("edge-shape.ibt");
    build_edge_ibt(&path);
    let decoded = decode(&path, None, Some(20.0)).unwrap();
    let out = scratch("edge-shape.ccs");
    store::write(&decoded, &out).unwrap();
    let good = std::fs::read(&out).unwrap();
    let unedited = with_header(&good, |_| {});
    assert!(ColumnStore::parse(unedited).is_ok());
    let header: Json =
        serde_json::to_value(&ColumnStore::parse(good.clone()).unwrap().header).unwrap();
    let columns = header["columns"].as_array().unwrap();
    let position = |name: &str| columns.iter().position(|c| c["name"] == name).unwrap();
    let (text, float, array) = (position("Text"), position("F"), position("IArr"));
    let samples = header["sampling"]["sample_count"].as_u64().unwrap();
    assert!(samples >= 2);

    let cases: Vec<(&str, HeaderEdit)> = vec![
        (
            "index block of",
            Box::new(move |h| h["sample_indices"]["length"] = (samples * 4 + 1).into()),
        ),
        (
            "index block of",
            Box::new(move |h| h["sampling"]["sample_count"] = (samples - 1).into()),
        ),
        (
            "text offset block of",
            Box::new(move |h| {
                h["columns"][text]["text_offsets"]["length"] = 9.into();
            }),
        ),
        (
            "data block of",
            Box::new(move |h| {
                let length = h["columns"][array]["data"]["length"].as_u64().unwrap();
                h["columns"][array]["data"]["length"] = (length - 4).into();
            }),
        ),
        (
            "data block of",
            Box::new(move |h| h["columns"][array]["count"] = 1.into()),
        ),
        (
            "not a multiple of 8",
            Box::new(move |h| {
                let offset = h["columns"][float]["data"]["offset"].as_u64().unwrap();
                h["columns"][float]["data"]["offset"] = (offset + 4).into();
            }),
        ),
        ("is not decode", Box::new(|h| h["stage"] = "kernels".into())),
        (
            "is not stored as i32",
            Box::new(move |h| h["columns"][float]["element"] = "i32".into()),
        ),
        (
            "count 0",
            Box::new(move |h| h["columns"][float]["count"] = 0.into()),
        ),
        (
            "without text_offsets",
            Box::new(move |h| {
                h["columns"][text]
                    .as_object_mut()
                    .unwrap()
                    .remove("text_offsets");
            }),
        ),
        (
            "text_offsets on a f32",
            Box::new(move |h| {
                let offsets = h["columns"][text]["text_offsets"].clone();
                h["columns"][float]["text_offsets"] = offsets;
            }),
        ),
        (
            "text offsets must rise",
            Box::new(move |h| {
                let length = h["columns"][text]["data"]["length"].as_u64().unwrap();
                h["columns"][text]["data"]["length"] = (length - 1).into();
            }),
        ),
        (
            "repeated name",
            Box::new(move |h| h["columns"][float]["name"] = "Text".into()),
        ),
    ];
    for (message, edit) in cases {
        let error = ColumnStore::parse(with_header(&good, edit))
            .unwrap_err()
            .to_string();
        assert!(
            error.contains(message),
            "expected {message:?}, got {error:?}"
        );
    }

    // Descending text offsets in the data itself.
    let parsed = ColumnStore::parse(good.clone()).unwrap();
    let offsets = parsed.header.columns[text].text_offsets.unwrap();
    let length = u32::from_le_bytes(good[12..16].try_into().unwrap()) as u64;
    let base = (16 + length).div_ceil(8) * 8;
    let mut descending = good.clone();
    let second = (base + offsets.offset + 8) as usize;
    descending[second..second + 8].copy_from_slice(&u64::MAX.to_le_bytes());
    let error = ColumnStore::parse(descending).unwrap_err().to_string();
    assert!(error.contains("text offsets must rise"), "{error}");
}

#[test]
fn decode_request_runs_end_to_end() {
    let source = fixture("synthetic-race.ibt");
    let out = scratch("request.ccs");
    let text = serde_json::json!({
        "version": 1,
        "source": source,
        "out": out,
        "channels": ["Speed", "OnPitRoad"],
        "target_hz": "20.0",
        "unknown_optional": true,
    })
    .to_string();
    let summary = DecodeRequest::parse(&text).unwrap().run().unwrap();
    assert_eq!((summary.sample_count, summary.columns), (40, 2));
    let read = ColumnStore::read(&out).unwrap();
    assert_eq!(read.header.channel_mode, "selected");
    assert_eq!(read.header.sampling.target_hz, Some(20.0));
    assert!(DecodeRequest::parse(r#"{"version": 2, "source": "a", "out": "b"}"#).is_err());
    assert!(DecodeRequest::parse(r#"{"version": 1, "out": "b"}"#).is_err());
    let bad_rate = DecodeRequest::parse(
        &serde_json::json!({"version": 1, "source": source, "out": out, "target_hz": "fast"})
            .to_string(),
    )
    .unwrap();
    assert!(matches!(bad_rate.run().unwrap_err(), DecodeError::TargetHz));
    // Review finding 2: a positive subnormal target overflows the step; CPython raises
    // OverflowError, so the stage fails and writes no store.
    let overflow_out = scratch("overflow.ccs");
    let overflow = DecodeRequest::parse(
        &serde_json::json!({"version": 1, "source": source, "out": overflow_out,
                            "channels": ["Speed"], "target_hz": "5e-324"})
        .to_string(),
    )
    .unwrap();
    let error = overflow.run().unwrap_err();
    assert!(matches!(error, DecodeError::SampleIndexOverflow), "{error}");
    assert!(!overflow_out.exists());
}

#[test]
fn matrix_hash_quiets_signalling_nan_as_python_packs_it() {
    let path = scratch("edge-hash.ibt");
    build_edge_ibt(&path);
    let decoded = decode(&path, Some(&["F".to_owned()]), None).unwrap();
    let mut digest = Sha256::new();
    digest.update(b"F");
    for value in [4i32, 1, EDGE_RECORDS as i32] {
        digest.update(value.to_le_bytes());
    }
    for record in 0..EDGE_RECORDS {
        let bits = FLOAT_BITS[record % FLOAT_BITS.len()];
        let packed = if bits == SIGNALLING_NAN {
            bits | 0x0040_0000
        } else {
            bits
        };
        digest.update(packed.to_le_bytes());
    }
    let expected: String = digest
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    assert_eq!(matrix_sha256(&decoded), expected);
}

// ---- sampling vectors from CPython ----

#[test]
fn sampling_plan_matches_cpython_vectors() {
    let vectors = read_json(
        &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("vectors")
            .join("sampling.json"),
    );
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() >= 50);
    assert!(cases.iter().any(|case| case["error"].is_string()));
    for case in cases {
        let tick = case["tick_rate"].as_i64().unwrap() as i32;
        let records = case["record_count"].as_u64().unwrap() as u32;
        let target = case["target_hz"]
            .as_str()
            .map(|text| text.parse::<f64>().unwrap());
        let label = format!("{tick} Hz, {target:?}, {records} records");
        let Some(step_text) = case["step"].as_str() else {
            assert!(
                SamplingPlan::new(tick, target).is_err(),
                "{label}: Python rejected it"
            );
            continue;
        };
        let plan = SamplingPlan::new(tick, target).unwrap();
        assert_eq!(
            plan.step.to_bits(),
            step_text.parse::<f64>().unwrap().to_bits(),
            "{label}: step"
        );
        assert_eq!(
            plan.output_rate.to_bits(),
            case["output_rate"]
                .as_str()
                .unwrap()
                .parse::<f64>()
                .unwrap()
                .to_bits(),
            "{label}: output rate"
        );
        if let Some(error) = case["error"].as_str() {
            // Review finding 2: an infinite step is Python's OverflowError, not success.
            let native = plan.sample_indices(records).unwrap_err();
            assert_eq!(format!("OverflowError: {native}"), error, "{label}: error");
            continue;
        }
        let indices = plan.sample_indices(records).unwrap();
        assert_eq!(
            indices.len() as u64,
            case["sample_count"].as_u64().unwrap(),
            "{label}: count"
        );
        let mut digest = Sha256::new();
        for index in &indices {
            digest.update(index.to_le_bytes());
        }
        let hash: String = digest
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(
            hash,
            case["indices_sha256"].as_str().unwrap(),
            "{label}: indices"
        );
    }
}
