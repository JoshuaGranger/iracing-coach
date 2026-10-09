//! Compile-time check that the public signatures match `contracts/crate-api.md` section 5.
//! Nothing is called: the bodies are stubs until W5 implements them.

#![allow(clippy::type_complexity)]

use std::path::Path;

use coach_views::*;

#[test]
fn signatures_match_the_contract() {
    let _: fn(&Path, &Path) -> Result<ProjectionSummary, ProjectError> = project;
    let _: fn(&Path, Option<&str>, Option<&str>, u64) -> Result<Vec<u8>, SelectError> =
        select_traces;
    assert_eq!(PROJECTION_VERSION, 6);
}
