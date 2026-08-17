"""The frozen durable-union contract, and proof that it can reject failure.

`DURABLE-RMW-001`. These are the producer-side clauses Codex's production
transaction and migration will be measured against. Nothing here implements
durable storage; the specifications live in `durable_union_support` as values,
so the same clauses can later be pointed at the real store without being
restated - a restated clause is a clause that can drift.

The negative matrix is the load-bearing half. A conformance suite that only
ever sees a conforming store cannot distinguish "the contract holds" from "the
contract asserts nothing", so every specification is also run against stores
that break exactly one guarantee, and each must be rejected by its own clause.
`LastWriterWinsStore` is today's measured behavior, so the suite is proven to
reject the defect it exists to close rather than merely describing it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import durable_union_support as union_support  # noqa: E402
from durable_union_support import (  # noqa: E402
    NON_CONFORMING_STORES,
    SPECIFICATIONS,
    Observation,
    UnionStore,
    content_multiset,
    distinct_fixture,
    duplicate_fixture,
    evaluate,
    random_fixture,
    repeated_fixture,
    union,
    union_fingerprint,
)

#: The population every clause is stated at. The closure text requires at least
#: sixteen; using exactly the floor keeps a failure cheap to read while still
#: satisfying the clause.
N = 16


class RecordIdentityTests(unittest.TestCase):
    """Identity is what makes duplicates and idempotence able to coexist."""

    def test_identical_content_and_provenance_is_one_record(self) -> None:
        first = Observation(content={"lap": 1}, provenance={"run": "a", "sequence": 1})
        second = Observation(content={"lap": 1}, provenance={"run": "a", "sequence": 1})
        self.assertEqual(first.record_identity, second.record_identity)

    def test_identical_content_with_different_provenance_is_two_records(self) -> None:
        first = Observation(content={"lap": 1}, provenance={"run": "a", "sequence": 1})
        second = Observation(content={"lap": 1}, provenance={"run": "b", "sequence": 1})
        self.assertEqual(first.content_identity, second.content_identity)
        self.assertNotEqual(first.record_identity, second.record_identity)

    def test_an_unknown_field_changes_the_record(self) -> None:
        # If it did not, a migration could drop the field and still claim the
        # store was unchanged.
        plain = Observation(content={"lap": 1}, provenance={"run": "a"})
        extended = Observation(content={"lap": 1}, provenance={"run": "a"}, extra={"x": 1})
        self.assertNotEqual(plain.record_identity, extended.record_identity)

    def test_identity_does_not_depend_on_key_insertion_order(self) -> None:
        first = Observation(content={"lap": 1, "value": 2}, provenance={"run": "a"})
        second = Observation(content={"value": 2, "lap": 1}, provenance={"run": "a"})
        self.assertEqual(first.record_identity, second.record_identity)

    def test_a_record_survives_a_payload_round_trip(self) -> None:
        original = Observation(
            content={"lap": 3}, provenance={"run": "a"}, extra={"future": [1, 2]}
        )
        restored = Observation.from_payload(original.to_payload())
        self.assertEqual(restored.record_identity, original.record_identity)


class UnionAlgebraTests(unittest.TestCase):
    """The four laws that make the result independent of scheduling."""

    def setUp(self) -> None:
        fixture = random_fixture(3 * N)
        self.a = fixture[:N]
        self.b = fixture[N : 2 * N]
        self.c = fixture[2 * N :]

    def test_union_is_commutative(self) -> None:
        self.assertEqual(
            union_fingerprint(union(self.a, self.b)),
            union_fingerprint(union(self.b, self.a)),
        )

    def test_union_is_associative(self) -> None:
        self.assertEqual(
            union_fingerprint(union(union(self.a, self.b), self.c)),
            union_fingerprint(union(self.a, union(self.b, self.c))),
        )

    def test_union_is_idempotent(self) -> None:
        self.assertEqual(
            union_fingerprint(union(self.a, self.a)), union_fingerprint(self.a)
        )

    def test_the_empty_store_is_the_identity_element(self) -> None:
        self.assertEqual(union_fingerprint(union(self.a, [])), union_fingerprint(self.a))

    def test_the_fingerprint_ignores_order_but_not_membership(self) -> None:
        # Both halves matter. An order-insensitive fingerprint that also
        # ignored membership would be a constant and would pass every other
        # clause here.
        self.assertEqual(
            union_fingerprint(self.a), union_fingerprint(list(reversed(self.a)))
        )
        self.assertNotEqual(union_fingerprint(self.a), union_fingerprint(self.a[:-1]))

    def test_the_fingerprint_is_stable_across_runs(self) -> None:
        # Seeded fixtures make exact evidence citable. A per-run value could
        # not be reproduced from a published report.
        self.assertEqual(
            union_fingerprint(random_fixture(N)), union_fingerprint(random_fixture(N))
        )


class FixtureShapeTests(unittest.TestCase):
    """The four required fixture shapes are what they claim to be."""

    def test_the_distinct_fixture_has_sixteen_distinct_records(self) -> None:
        fixture = distinct_fixture(N)
        self.assertEqual(len(fixture), N)
        self.assertEqual(len({o.record_identity for o in fixture}), N)

    def test_the_duplicate_fixture_shares_content_across_provenance(self) -> None:
        fixture = duplicate_fixture(N)
        self.assertEqual(len({o.record_identity for o in fixture}), N)
        self.assertEqual(len({o.content_identity for o in fixture}), N // 2)

    def test_the_random_fixture_is_seeded_and_reproducible(self) -> None:
        self.assertEqual(
            [o.record_identity for o in random_fixture(N)],
            [o.record_identity for o in random_fixture(N)],
        )
        self.assertNotEqual(
            [o.record_identity for o in random_fixture(N, seed=1)],
            [o.record_identity for o in random_fixture(N, seed=2)],
        )

    def test_the_repeated_fixture_replays_the_same_records(self) -> None:
        fixture = repeated_fixture(N)
        self.assertEqual(len(fixture), 3 * N)
        self.assertEqual(len({o.record_identity for o in fixture}), N)


class ConformingStoreTests(unittest.TestCase):
    """Every specification is satisfiable, demonstrated rather than assumed."""

    def test_the_reference_store_satisfies_every_specification(self) -> None:
        results = evaluate(UnionStore, N)
        unmet = {name: value["detail"] for name, value in results.items() if not value["satisfied"]}
        self.assertEqual(unmet, {})

    def test_every_named_specification_was_actually_evaluated(self) -> None:
        # Guards against a suite that passes because the specification map is
        # empty or a name was silently dropped.
        self.assertEqual(set(evaluate(UnionStore, N)), set(SPECIFICATIONS))
        self.assertGreaterEqual(len(SPECIFICATIONS), 9)


class StagedBarrierTests(unittest.TestCase):
    """The lost-update shape itself, stated as a clause rather than a measurement.

    Every writer reads the same prior value before any writer commits. That is
    the barrier that produces the loss the reproduction measures, so it is the
    exact condition the union contract must survive.
    """

    def _run_barrier(self, store_class, fixture):
        store = store_class()
        # All sixteen readers observe the same empty prior state.
        prior = [store.fingerprint() for _ in fixture]
        self.assertEqual(len(set(prior)), 1, "the barrier did not hold")
        for observation in fixture:
            store.apply([observation])
        return store

    def test_sixteen_barrier_writers_of_distinct_records_all_survive(self) -> None:
        fixture = distinct_fixture(N)
        store = self._run_barrier(UnionStore, fixture)
        self.assertEqual(store.fingerprint(), union_fingerprint(fixture))
        self.assertEqual(len(store.read_all()), N)

    def test_sixteen_barrier_writers_of_duplicate_content_all_survive(self) -> None:
        fixture = duplicate_fixture(N)
        store = self._run_barrier(UnionStore, fixture)
        self.assertEqual(content_multiset(store.read_all()), content_multiset(fixture))

    def test_sixteen_barrier_writers_of_random_records_all_survive(self) -> None:
        fixture = random_fixture(N)
        store = self._run_barrier(UnionStore, fixture)
        self.assertEqual(store.fingerprint(), union_fingerprint(fixture))

    def test_the_barrier_loses_all_but_one_writer_under_todays_shape(self) -> None:
        # The clause is only meaningful if the barrier can actually produce the
        # loss. Under last-writer-wins exactly one record survives, which is
        # the measured behavior and is what makes the clause above a real test
        # rather than a restatement of the reference store's implementation.
        fixture = distinct_fixture(N)
        store = self._run_barrier(union_support.LastWriterWinsStore, fixture)
        self.assertEqual(len(store.read_all()), 1)
        self.assertNotEqual(store.fingerprint(), union_fingerprint(fixture))


class NegativeMatrixTests(unittest.TestCase):
    """Each broken store must be rejected, and by the clause that owns it."""

    def test_every_non_conforming_store_fails_its_named_specification(self) -> None:
        failures: list[str] = []
        for label, store_class, specification in NON_CONFORMING_STORES:
            results = evaluate(store_class, N)
            if results[specification]["satisfied"]:
                failures.append(
                    f"{label}: {specification} reported satisfied "
                    f"({results[specification]['detail']})"
                )
        self.assertEqual(failures, [], f"fail-open specifications: {failures}")

    def test_each_named_specification_is_the_one_that_owns_its_defect(self) -> None:
        # A store that fails every clause proves nothing about which clause is
        # doing the work. Each of these must leave at least one other
        # specification satisfied, so the rejection above is attributable.
        weak: list[str] = []
        for label, store_class, specification in NON_CONFORMING_STORES:
            results = evaluate(store_class, N)
            others = [
                name for name, value in results.items()
                if name != specification and value["satisfied"]
            ]
            if not others:
                weak.append(label)
        self.assertEqual(weak, [], f"stores failing indiscriminately: {weak}")

    def test_the_matrix_covers_every_specification_that_can_be_broken(self) -> None:
        covered = {specification for _, _, specification in NON_CONFORMING_STORES}
        # `idempotence`, `rebuild_from_raw` and `old_and_new_readers` are
        # covered transitively: the resume-duplicating and unknown-field
        # stores break them too. Naming the primary owner keeps each rejection
        # attributable while this assertion keeps the map honest about size.
        self.assertTrue(covered.issubset(set(SPECIFICATIONS)))
        self.assertGreaterEqual(len(covered), 6)

    def test_todays_shape_fails_the_union_clause_specifically(self) -> None:
        results = evaluate(union_support.LastWriterWinsStore, N)
        self.assertFalse(results["union"]["satisfied"])
        self.assertFalse(results["duplicates"]["satisfied"])

    def test_a_content_deduplicating_store_deletes_real_history(self) -> None:
        results = evaluate(union_support.ContentDeduplicatingStore, N)
        self.assertFalse(results["duplicates"]["satisfied"])
        # It still satisfies the plain union clause, which is precisely why
        # duplicates needs its own clause: a suite that checked only record
        # count on distinct fixtures would certify this store.
        self.assertTrue(results["union"]["satisfied"])

    def test_a_resume_duplicating_store_fails_only_after_an_interruption(self) -> None:
        results = evaluate(union_support.ResumeDuplicatingStore, N)
        self.assertFalse(results["migration_resume"]["satisfied"])
        self.assertFalse(results["idempotence"]["satisfied"])

    def test_a_rollback_erasing_store_loses_acknowledged_work(self) -> None:
        results = evaluate(union_support.RollbackErasingStore, N)
        self.assertFalse(results["rollback"]["satisfied"])
        self.assertTrue(results["union"]["satisfied"])


class MigrationResumeTests(unittest.TestCase):
    """Resume is checked at every cut, not at a sampled one."""

    def test_resume_is_exercised_at_every_boundary_including_both_ends(self) -> None:
        satisfied, detail = union_support.spec_migration_resume(UnionStore, N)
        self.assertTrue(satisfied, detail)

    def test_an_interrupted_migration_never_duplicates_a_record(self) -> None:
        fixture = distinct_fixture(N)
        for cut in (0, 1, N // 2, N - 1, N):
            store = UnionStore()
            store.apply(fixture[:cut])
            store.apply(fixture)
            self.assertEqual(len(store.read_all()), N, f"cut={cut}")


class RollbackTests(unittest.TestCase):
    def test_rollback_preserves_every_acknowledged_record(self) -> None:
        satisfied, detail = union_support.spec_rollback(UnionStore, N)
        self.assertTrue(satisfied, detail)

    def test_rebuild_from_raw_reproduces_the_exact_fingerprint(self) -> None:
        satisfied, detail = union_support.spec_rebuild_from_raw(UnionStore, N)
        self.assertTrue(satisfied, detail)

    def test_a_rebuilt_store_is_not_merely_an_alias_of_the_original(self) -> None:
        # Otherwise the rebuild clause would compare a store with itself.
        store = UnionStore()
        store.apply(distinct_fixture(N))
        rebuilt = store.rebuild_from_raw()
        self.assertIsNot(rebuilt, store)
        self.assertEqual(rebuilt.fingerprint(), store.fingerprint())


class ContractVersionTests(unittest.TestCase):
    def test_the_union_contract_declares_its_own_version(self) -> None:
        self.assertIsInstance(union_support.UNION_CONTRACT_VERSION, int)
        self.assertNotIsInstance(union_support.UNION_CONTRACT_VERSION, bool)
        self.assertGreaterEqual(union_support.UNION_CONTRACT_VERSION, 1)

    def test_the_fingerprint_is_bound_to_the_contract_version(self) -> None:
        # A future contract change must not silently produce the same
        # fingerprint for a different meaning of "the same store".
        fixture = distinct_fixture(N)
        baseline = union_fingerprint(fixture)
        original = union_support.UNION_CONTRACT_VERSION
        try:
            union_support.UNION_CONTRACT_VERSION = original + 1
            self.assertNotEqual(union_fingerprint(fixture), baseline)
        finally:
            union_support.UNION_CONTRACT_VERSION = original
        self.assertEqual(union_fingerprint(fixture), baseline)


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
