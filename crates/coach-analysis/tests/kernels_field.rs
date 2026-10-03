//! Unit field: replays every recorded oracle call of the unit against its native entries
//! (`docs/08-rewrite/port/phase2-plan.md` section 3).

mod support;

#[test]
fn field_vectors_replay_exactly() {
    support::vectors::replay_unit("field");
}
