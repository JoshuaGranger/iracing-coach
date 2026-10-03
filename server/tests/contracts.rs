//! The Python MCP contracts that worker results are validated against (`contracts/README.md`):
//! the sanitized fixtures meet them, broken results do not, and the relative references between
//! contract files resolve from the contracts directory.

use std::path::{Path, PathBuf};

use coach_server::schema::{WorkerContracts, worker_contract_for};
use serde_json::{Value, json};

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn fixture(name: &str) -> Value {
    let text = std::fs::read_to_string(repo().join("test-data").join(name)).expect("fixture");
    serde_json::from_str(&text).expect("fixture is JSON")
}

fn contracts() -> WorkerContracts {
    WorkerContracts::new(repo().join("contracts"))
}

/// The analyze fixture predates `analysis_view`; the envelope fixture completes it.
fn analyze_result() -> Value {
    let mut result = fixture("analyze-repair-heavy.json");
    result["analysis_view"] = fixture("analysis-view-valid.json");
    result
}

#[test]
fn the_three_validated_tools_map_to_their_contracts() {
    assert_eq!(
        worker_contract_for("analyze_iracing_race"),
        Some("analyze-result-v1")
    );
    assert_eq!(
        worker_contract_for("build_open_setup_package"),
        Some("setup-package-v1")
    );
    assert_eq!(
        worker_contract_for("recommend_structured_open_setup_tuning"),
        Some("tuning-recommendation-v1")
    );
    assert_eq!(worker_contract_for("discover_iracing_sessions"), None);
}

#[test]
fn valid_results_pass() {
    let c = contracts();
    c.check("analyze-result-v1", &analyze_result()).unwrap();
    c.check("setup-package-v1", &fixture("setup-package.json"))
        .unwrap();
    c.check(
        "tuning-recommendation-v1",
        &fixture("setup-recommendation.json"),
    )
    .unwrap();
    c.check(
        "tuning-recommendation-v1",
        &fixture("setup-recommendation-damage-blocked.json"),
    )
    .unwrap();
}

#[test]
fn a_result_that_breaks_its_contract_names_the_mismatch() {
    let c = contracts();
    let mut result = analyze_result();
    result.as_object_mut().unwrap().remove("analysis_view");
    let message = c.check("analyze-result-v1", &result).unwrap_err();
    assert!(message.contains("analyze-result-v1"), "{message}");
    assert!(message.contains("analysis_view"), "{message}");

    // a mismatch inside a referenced contract file (analysis-view-v1 is a relative $ref)
    let mut result = analyze_result();
    result["analysis_view"]["schema_version"] = json!(99);
    assert!(c.check("analyze-result-v1", &result).is_err());

    let mut package = fixture("setup-package.json");
    package["status"] = json!("not-a-status");
    assert!(c.check("setup-package-v1", &package).is_err());

    let mut recommendation = fixture("setup-recommendation.json");
    recommendation
        .as_object_mut()
        .unwrap()
        .remove("recommendation");
    assert!(
        c.check("tuning-recommendation-v1", &recommendation)
            .is_err()
    );
}

#[test]
fn a_missing_contract_directory_fails_clearly() {
    let c = WorkerContracts::new(repo().join("no-such-contracts"));
    let message = c.check("analyze-result-v1", &analyze_result()).unwrap_err();
    assert!(message.contains("cannot be read"), "{message}");
}
