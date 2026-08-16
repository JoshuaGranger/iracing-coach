"""The adversarial ordering matrix for the four request identities.

`REQUEST-IDENTITY-001`, `IDENTITY-SPLIT-001`, `ANALYSIS-ORDER-001`,
`PLAN-STALE-001`, `TUNE-STALE-001`. The closure clause names deterministic
barriers for both completion orders, Back/open-next, same-key reuse, every
input mutation, and cancel/fault, with two invariants that must hold together:
no stale result publishes, and no *current* result is refused. Both directions
matter - a rule that refused everything would satisfy the first alone.

Ordering is expressed as an explicit sequence of completions rather than by
timing. A test that relied on one task genuinely being slower would be
measuring the host, and would pass or fail for reasons unrelated to the rule
under test.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY_ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import identity_contract as identity  # noqa: E402
from identity_contract import (  # noqa: E402
    ContentIdentity,
    IdentityContractError,
    OperationKey,
    PublicationKey,
    PublicationDecision,
    ResultProvenance,
    StagedResult,
    decide,
    may_join,
)

PRODUCER = "analysis-v13"


def operation(selection=None, options=None, workflow="analyze", producer=PRODUCER):
    return OperationKey(
        workflow=workflow,
        selection=selection if selection is not None else {"subsession_id": 8001, "sim_session_num": 2},
        options=options if options is not None else {"include_replay": False},
        producer_version=producer,
    )


def staged(
    *,
    op=None,
    publication=None,
    digest="content-a",
    complete=True,
    sequence=1,
    canceled=False,
    faulted=False,
):
    op = op if op is not None else operation()
    publication = publication if publication is not None else PublicationKey("race-analysis", 0)
    content = ContentIdentity(digest=digest, complete=complete)
    return StagedResult(
        operation=op,
        publication=publication,
        content=content,
        provenance=ResultProvenance(
            operation_key=op.value,
            content_identity=content.value,
            producer_version=PRODUCER,
            completed_sequence=sequence,
        ),
        canceled=canceled,
        faulted=faulted,
    )


class OperationKeyTests(unittest.TestCase):
    def test_the_same_work_produces_the_same_key(self) -> None:
        self.assertEqual(operation().value, operation().value)

    def test_key_order_within_the_selection_is_irrelevant(self) -> None:
        first = operation(selection={"subsession_id": 8001, "sim_session_num": 2})
        second = operation(selection={"sim_session_num": 2, "subsession_id": 8001})
        self.assertEqual(first.value, second.value)

    def test_every_material_input_change_changes_the_key(self) -> None:
        baseline = operation().value
        mutations = {
            "workflow": operation(workflow="plan"),
            "selection": operation(selection={"subsession_id": 8002, "sim_session_num": 2}),
            "sim_session": operation(selection={"subsession_id": 8001, "sim_session_num": 3}),
            "options": operation(options={"include_replay": True}),
            "producer_version": operation(producer="analysis-v14"),
        }
        unchanged = [name for name, value in mutations.items() if value.value == baseline]
        self.assertEqual(unchanged, [], f"mutations that did not change the key: {unchanged}")

    def test_the_epoch_is_not_part_of_the_operation_key(self) -> None:
        # If it were, two surfaces asking for the same analysis at different
        # epochs would never join, and the split would buy nothing.
        self.assertTrue(may_join(operation(), operation()))

    def test_a_missing_field_fails_closed(self) -> None:
        with self.assertRaises(IdentityContractError):
            OperationKey.from_mapping({"workflow": "analyze", "selection": {}, "options": {}})

    def test_a_key_can_be_rebuilt_from_its_declared_fields(self) -> None:
        rebuilt = OperationKey.from_mapping(
            {
                "workflow": "analyze",
                "selection": {"subsession_id": 8001, "sim_session_num": 2},
                "options": {"include_replay": False},
                "producer_version": PRODUCER,
            }
        )
        self.assertEqual(rebuilt.value, operation().value)

    def test_the_declared_field_list_matches_the_dataclass(self) -> None:
        # The C# consumer is generated from the declared list, so a field added
        # to one and not the other would produce a key that means two different
        # things in two languages.
        self.assertEqual(
            set(identity.OPERATION_KEY_FIELDS),
            {"workflow", "selection", "options", "producer_version"},
        )


class PublicationKeyTests(unittest.TestCase):
    def test_advancing_the_epoch_changes_the_key(self) -> None:
        first = PublicationKey("race-analysis", 0)
        self.assertNotEqual(first.value, first.advanced().value)

    def test_the_same_epoch_on_different_surfaces_is_a_different_key(self) -> None:
        self.assertNotEqual(
            PublicationKey("race-analysis", 0).value, PublicationKey("planning", 0).value
        )

    def test_a_boolean_epoch_is_refused(self) -> None:
        # `isinstance(True, int)` is True in Python, so a boolean would
        # otherwise silently become epoch 1.
        with self.assertRaises(IdentityContractError):
            PublicationKey("race-analysis", True)

    def test_a_negative_epoch_is_refused(self) -> None:
        with self.assertRaises(IdentityContractError):
            PublicationKey("race-analysis", -1)


class ContentIdentityTests(unittest.TestCase):
    def test_an_empty_digest_is_refused(self) -> None:
        with self.assertRaises(IdentityContractError):
            ContentIdentity(digest="")

    def test_incompleteness_is_representable_rather_than_implied(self) -> None:
        partial = ContentIdentity(digest="content-a", complete=False)
        self.assertEqual(partial.value, "content-a")
        self.assertFalse(partial.complete)


class CompletionOrderTests(unittest.TestCase):
    """Both completion orders, stated deterministically."""

    def test_slow_first_request_cannot_publish_after_a_newer_one(self) -> None:
        current = PublicationKey("race-analysis", 0)
        slow = staged(publication=current, digest="content-a", sequence=1)
        current = current.advanced()  # the user opened race B
        fast = staged(publication=current, digest="content-b", sequence=2)

        # B completes first and publishes; A completes second and must not.
        self.assertTrue(decide(fast, current).may_publish)
        stale = decide(slow, current)
        self.assertFalse(stale.may_publish)
        self.assertEqual(stale.refusal, identity.REFUSAL_SUPERSEDED)

    def test_the_newer_request_still_publishes_when_it_finishes_last(self) -> None:
        current = PublicationKey("race-analysis", 0)
        slow = staged(publication=current, sequence=1)
        current = current.advanced()
        fast = staged(publication=current, digest="content-b", sequence=2)

        # A completes first and is refused; B completes second and must succeed.
        self.assertFalse(decide(slow, current).may_publish)
        self.assertTrue(decide(fast, current).may_publish)

    def test_a_current_result_is_never_refused(self) -> None:
        current = PublicationKey("race-analysis", 3)
        decision = decide(staged(publication=current), current)
        self.assertEqual(decision, PublicationDecision(True, True, None))


class NavigationTests(unittest.TestCase):
    def test_back_then_forward_does_not_resurrect_the_earlier_result(self) -> None:
        # Returning to the same surface advances the epoch rather than
        # restoring the old one. An epoch that could be reused would make a
        # stale result publishable again simply because the user navigated in
        # a circle.
        original = PublicationKey("race-analysis", 0)
        result = staged(publication=original)
        after_back = original.advanced()
        after_forward = after_back.advanced()
        self.assertFalse(decide(result, after_forward).may_publish)

    def test_a_result_cannot_publish_onto_a_different_surface(self) -> None:
        result = staged(publication=PublicationKey("race-analysis", 0))
        decision = decide(result, PublicationKey("planning", 0))
        self.assertFalse(decision.may_publish)
        self.assertEqual(decision.refusal, identity.REFUSAL_DIFFERENT_SURFACE)

    def test_planning_and_tuning_staleness_use_the_same_rule(self) -> None:
        # PLAN-STALE-001 and TUNE-STALE-001 are the same defect on two
        # surfaces; one rule covers both, so neither can drift.
        for surface in ("planning", "starting-tune"):
            current = PublicationKey(surface, 4)
            result = staged(publication=PublicationKey(surface, 3))
            with self.subTest(surface=surface):
                self.assertFalse(decide(result, current).may_publish)
                self.assertTrue(decide(staged(publication=current), current).may_publish)


class CacheTests(unittest.TestCase):
    """Valid stale work may cache; invalid work may not."""

    def test_valid_stale_work_may_still_be_cached(self) -> None:
        current = PublicationKey("race-analysis", 0)
        result = staged(publication=current, digest="content-a")
        decision = decide(result, current.advanced())
        self.assertFalse(decision.may_publish)
        self.assertTrue(decision.may_cache)

    def test_work_with_an_incomplete_content_identity_may_not_be_cached(self) -> None:
        current = PublicationKey("race-analysis", 0)
        decision = decide(staged(publication=current, complete=False), current)
        self.assertFalse(decision.may_cache)
        self.assertFalse(decision.may_publish)
        self.assertEqual(decision.refusal, identity.REFUSAL_INCOMPLETE_CONTENT)

    def test_an_incomplete_result_is_refused_even_when_perfectly_current(self) -> None:
        # The dangerous case: nothing about the request is stale, so a
        # publication-only rule would accept it.
        current = PublicationKey("race-analysis", 7)
        decision = decide(staged(publication=current, complete=False), current)
        self.assertFalse(decision.may_publish)

    def test_the_cache_key_is_the_content_identity_not_the_operation_key(self) -> None:
        # Two different operations that compute the same content must share a
        # cache entry, and one operation whose inputs changed must not.
        first = staged(op=operation(options={"include_replay": False}), digest="content-a")
        second = staged(op=operation(options={"include_replay": True}), digest="content-a")
        third = staged(op=operation(), digest="content-b")
        self.assertNotEqual(first.operation.value, second.operation.value)
        self.assertEqual(first.content.value, second.content.value)
        self.assertNotEqual(first.content.value, third.content.value)


class CancelAndFaultTests(unittest.TestCase):
    def test_a_canceled_result_never_publishes_or_caches(self) -> None:
        current = PublicationKey("race-analysis", 0)
        decision = decide(staged(publication=current, canceled=True), current)
        self.assertFalse(decision.may_publish)
        self.assertFalse(decision.may_cache)
        self.assertEqual(decision.refusal, identity.REFUSAL_CANCELED)

    def test_a_faulted_result_never_publishes_or_caches(self) -> None:
        current = PublicationKey("race-analysis", 0)
        decision = decide(staged(publication=current, faulted=True), current)
        self.assertFalse(decision.may_publish)
        self.assertFalse(decision.may_cache)
        self.assertEqual(decision.refusal, identity.REFUSAL_FAULTED)

    def test_cancellation_is_reported_before_staleness(self) -> None:
        # A canceled operation reported as merely superseded would tell the
        # consumer to retry work the user deliberately stopped.
        current = PublicationKey("race-analysis", 0)
        result = staged(publication=current, canceled=True)
        self.assertEqual(decide(result, current.advanced()).refusal, identity.REFUSAL_CANCELED)

    def test_a_fault_outranks_cancellation(self) -> None:
        current = PublicationKey("race-analysis", 0)
        result = staged(publication=current, canceled=True, faulted=True)
        self.assertEqual(decide(result, current).refusal, identity.REFUSAL_FAULTED)


class SameKeyReuseTests(unittest.TestCase):
    def test_identical_requests_may_share_one_execution(self) -> None:
        self.assertTrue(may_join(operation(), operation()))

    def test_requests_differing_in_any_input_may_not_share_one_execution(self) -> None:
        self.assertFalse(may_join(operation(), operation(options={"include_replay": True})))

    def test_one_execution_serves_two_surfaces_at_different_epochs(self) -> None:
        # The joined result publishes to whichever surface is still current and
        # is refused by the other, from a single computation.
        shared = operation()
        analysis_now = PublicationKey("race-analysis", 2)
        planning_now = PublicationKey("planning", 5)
        for_analysis = staged(op=shared, publication=analysis_now)
        for_planning = staged(op=shared, publication=PublicationKey("planning", 4))
        self.assertTrue(may_join(for_analysis.operation, for_planning.operation))
        self.assertTrue(decide(for_analysis, analysis_now).may_publish)
        self.assertFalse(decide(for_planning, planning_now).may_publish)


class ProvenanceTests(unittest.TestCase):
    def test_provenance_carries_every_declared_field(self) -> None:
        payload = staged().provenance.to_payload()
        self.assertEqual(set(payload), set(identity.RESULT_PROVENANCE_FIELDS))

    def test_provenance_binds_the_result_to_its_operation_and_content(self) -> None:
        result = staged()
        self.assertEqual(result.provenance.operation_key, result.operation.value)
        self.assertEqual(result.provenance.content_identity, result.content.value)


class RefusalVocabularyTests(unittest.TestCase):
    def test_every_declared_refusal_is_reachable(self) -> None:
        current = PublicationKey("race-analysis", 1)
        observed = {
            decide(staged(publication=current, faulted=True), current).refusal,
            decide(staged(publication=current, canceled=True), current).refusal,
            decide(staged(publication=current, complete=False), current).refusal,
            decide(staged(publication=PublicationKey("planning", 1)), current).refusal,
            decide(staged(publication=PublicationKey("race-analysis", 0)), current).refusal,
        }
        self.assertEqual(observed, set(identity.PUBLICATION_REFUSALS))

    def test_a_permitted_publication_carries_no_refusal(self) -> None:
        current = PublicationKey("race-analysis", 1)
        self.assertIsNone(decide(staged(publication=current), current).refusal)

    def test_the_contract_declares_its_own_version(self) -> None:
        self.assertIsInstance(identity.IDENTITY_CONTRACT_VERSION, int)
        self.assertNotIsInstance(identity.IDENTITY_CONTRACT_VERSION, bool)


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
