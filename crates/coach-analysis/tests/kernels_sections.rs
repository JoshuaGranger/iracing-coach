//! Unit sections: replays every recorded oracle call of the unit against its native entries
//! (`docs/08-rewrite/port/phase2-plan.md` section 3).

mod support;

#[test]
fn sections_vectors_replay_exactly() {
    support::vectors::replay_unit("sections");
}
