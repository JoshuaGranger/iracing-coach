//! Port phase 3: the native writer against the oracle's `json.dumps` outcomes
//! (`tests/vectors/writer/cases.json`, `tools/emit_writer_vectors.py`). Every case's pickle is
//! the seam's own; the answer must be the oracle's text (or SHA-256), the oracle's exception
//! (class and message; for `UnicodeEncodeError` start, end and characters), a
//! `python_exception` naming an exception the pickler recorded with the oracle's class and
//! message, or `fallback` for the hand-written cases outside the writer's model.

use std::path::PathBuf;

use coach_analysis::writer;
use serde_json::Value;

fn cases() -> Vec<Value> {
    let committed =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/writer/cases.json");
    // COACH_WRITER_VECTORS: a stress file from `emit_writer_vectors.py --stress` (not committed).
    let mut paths = vec![committed];
    paths.extend(std::env::var_os("COACH_WRITER_VECTORS").map(PathBuf::from));
    let mut cases = Vec::new();
    for path in paths {
        let text = std::fs::read_to_string(&path).expect("writer vectors");
        let document: Value = serde_json::from_str(&text).expect("vectors JSON");
        cases.extend(document["cases"].as_array().expect("cases").iter().cloned());
    }
    cases
}

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|at| u8::from_str_radix(&text[at..at + 2], 16).expect("hex"))
        .collect()
}

#[test]
fn writer_matches_the_oracle_on_every_vector() {
    let cases = cases();
    assert!(cases.len() > 900, "{} cases", cases.len());
    let mut kinds = std::collections::BTreeMap::<&str, usize>::new();
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let mut input = serde_json::to_vec(&case["request"]).unwrap();
        input.push(b'\n');
        input.extend(hex(case["pickle"].as_str().unwrap()));
        let answer = writer::run(&input).unwrap_or_else(|error| panic!("{name}: {error}"));
        let line = &answer.line;
        let expect = &case["expect"];
        if expect.get("fallback").is_some() {
            assert!(line.get("fallback").is_some(), "{name}: {line}");
            *kinds.entry("fallback").or_default() += 1;
            continue;
        }
        if let Some(raises) = expect.get("raises") {
            if let Some(index) = line.get("python_exception") {
                let recorded = &case["recorded"][index.as_u64().unwrap() as usize];
                assert_eq!(recorded[0], raises["class"], "{name}");
                assert_eq!(recorded[1], raises["message"], "{name}");
                *kinds.entry("python_exception").or_default() += 1;
                continue;
            }
            let got = &line["raises"];
            assert_eq!(got["class"], raises["class"], "{name}: {line}");
            if raises["class"] == "UnicodeEncodeError" {
                for field in ["start", "end", "characters"] {
                    assert_eq!(got[field], raises[field], "{name}: {field}");
                }
            } else {
                assert_eq!(got["message"], raises["message"], "{name}");
            }
            assert!(answer.payload.is_empty(), "{name}");
            *kinds.entry("raises").or_default() += 1;
            continue;
        }
        assert_eq!(line["ok"], true, "{name}: {line}");
        assert_eq!(line["length"], expect["length"], "{name}");
        if let Some(digest) = expect.get("sha256") {
            assert_eq!(line["sha256"], *digest, "{name}");
            *kinds.entry("sha256").or_default() += 1;
        } else {
            let text = expect["text"].as_str().unwrap();
            if answer.payload != text.as_bytes() {
                let at = answer
                    .payload
                    .iter()
                    .zip(text.as_bytes())
                    .position(|(a, b)| a != b)
                    .unwrap_or(text.len().min(answer.payload.len()));
                panic!(
                    "{name}: bytes differ at {at}: native {:?} oracle {:?}",
                    String::from_utf8_lossy(
                        &answer.payload[at.saturating_sub(20)..(at + 20).min(answer.payload.len())]
                    ),
                    &text[at.saturating_sub(20).min(text.len())..(at + 20).min(text.len())]
                );
            }
            *kinds.entry("bytes").or_default() += 1;
        }
    }
    // Every outcome kind is exercised.
    for kind in ["bytes", "sha256", "raises", "python_exception", "fallback"] {
        assert!(
            kinds.get(kind).copied().unwrap_or(0) > 0,
            "no {kind} case: {kinds:?}"
        );
    }
}

#[test]
fn writer_refuses_requests_the_seam_never_sends() {
    let request = |edit: &dyn Fn(&mut Value)| {
        let mut value = serde_json::json!({
            "version": 1, "ensure_ascii": false, "allow_nan": false, "sort_keys": true,
            "separators": [",", ":"], "indent": null, "terminator": "\n", "output": "bytes",
            "marker": ["native_stages", "_writer_marker"], "max_str_digits": 4300,
        });
        edit(&mut value);
        let mut input = serde_json::to_vec(&value).unwrap();
        input.extend(b"\n\x80\x05N.");
        writer::run(&input)
    };
    assert!(request(&|_| {}).is_ok());
    for edit in [
        &(|v: &mut Value| v["version"] = 2.into()) as &dyn Fn(&mut Value),
        &|v| v["sort_keys"] = false.into(),
        &|v| v["separators"] = serde_json::json!([", ", ": "]),
        &|v| v["indent"] = 2.into(),
        &|v| v["terminator"] = "\r".into(),
        &|v| v["output"] = "file".into(),
        &|v| v["unknown"] = 1.into(),
    ] {
        assert!(request(edit).is_err());
    }
    // A global other than the marker, and a truncated pickle.
    let mut input = br#"{"version":1,"ensure_ascii":false,"allow_nan":false,"sort_keys":true,"separators":[",",":"],"indent":null,"terminator":"","output":"bytes","marker":["m","f"],"max_str_digits":0}"#.to_vec();
    input.extend(b"\n\x80\x05\x8c\x02os\x8c\x06system\x93.");
    assert!(writer::run(&input).is_err());
}
