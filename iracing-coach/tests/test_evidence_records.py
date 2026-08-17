"""Evidence identity, absence-is-not-zero, and refusal of unsupported causes.

`UI-EVIDENCE-LOSS-001`, `INCIDENT-ABSENCE-001`, `EVIDENCE-DEAD-001`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evidence_records as ev  # noqa: E402
import race_card  # noqa: E402


def _measured(subject="pace", observations=12, **overrides):
    values = {
        "subject": subject,
        "evidence_class": "measured",
        "source": "telemetry.LapLastLapTime",
        "coverage": "complete",
        "confidence": "high",
        "observations": observations,
    }
    values.update(overrides)
    return ev.EvidenceRecord(**values)


class StableIdentityTests(unittest.TestCase):
    def test_the_same_support_produces_the_same_id(self):
        self.assertEqual(_measured().evidence_id, _measured().evidence_id)

    def test_different_coverage_produces_a_different_id(self):
        complete = _measured()
        partial = _measured(coverage="partial")
        self.assertNotEqual(complete.evidence_id, partial.evidence_id)

    def test_different_observation_counts_produce_different_ids(self):
        self.assertNotEqual(
            _measured(observations=12).evidence_id,
            _measured(observations=13).evidence_id,
        )

    def test_different_limitations_produce_different_ids(self):
        self.assertNotEqual(
            _measured().evidence_id,
            _measured(limitations=("one wet lap included",)).evidence_id,
        )

    def test_the_id_is_stable_regardless_of_detail_key_order(self):
        first = _measured(detail={"a": 1, "b": 2})
        second = _measured(detail={"b": 2, "a": 1})
        self.assertEqual(first.evidence_id, second.evidence_id)

    def test_the_id_carries_the_contract_version(self):
        """A version bump must not let an old id resolve to a new record."""
        at_current_version = _measured().evidence_id
        original = ev.EVIDENCE_RECORD_VERSION
        try:
            ev.EVIDENCE_RECORD_VERSION = original + 1
            self.assertNotEqual(at_current_version, _measured().evidence_id)
        finally:
            ev.EVIDENCE_RECORD_VERSION = original
        self.assertEqual(at_current_version, _measured().evidence_id)

    def test_every_record_carries_class_source_coverage_and_confidence(self):
        payload = _measured().to_payload()
        for key in (
            "confidence",
            "coverage",
            "evidence_class",
            "evidence_id",
            "limitations",
            "source",
            "subject",
        ):
            self.assertIn(key, payload)


class ContradictoryRecordTests(unittest.TestCase):
    def test_an_undeclared_class_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(evidence_class="vibes")

    def test_an_undeclared_coverage_state_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(coverage="mostly")

    def test_a_record_without_a_source_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(source="")

    def test_a_record_without_a_subject_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(subject="")

    def test_a_boolean_observation_count_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(observations=True)

    def test_a_negative_observation_count_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(observations=-1)

    def test_unknown_coverage_cannot_carry_a_count(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(coverage="unknown", observations=0)

    def test_complete_coverage_with_no_confidence_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(confidence="none")

    def test_unavailable_evidence_cannot_report_coverage_of_a_subject(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(evidence_class="unavailable", coverage="complete", confidence="none")

    def test_unavailable_evidence_cannot_carry_confidence(self):
        with self.assertRaises(ev.EvidenceError):
            _measured(evidence_class="unavailable", coverage="unknown", confidence="high")


class IncidentAbsenceTests(unittest.TestCase):
    """`INCIDENT-ABSENCE-001`: an unrecorded count is unknown, not none."""

    def test_a_recorded_zero_is_a_measured_fact(self):
        record = ev.count_record(
            subject="incidents",
            source="telemetry.PlayerCarMyIncidentCount",
            count=0,
            channel_recorded=True,
        )
        self.assertEqual(record.evidence_class, "measured")
        self.assertEqual(record.observations, 0)
        self.assertEqual(record.coverage, "complete")

    def test_an_unrecorded_channel_is_unavailable_and_carries_no_number(self):
        record = ev.count_record(
            subject="incidents",
            source="telemetry.PlayerCarMyIncidentCount",
            count=None,
            channel_recorded=False,
        )
        self.assertEqual(record.evidence_class, "unavailable")
        self.assertEqual(record.coverage, "unknown")
        self.assertIsNone(record.observations)

    def test_the_two_are_distinguishable_rather_than_both_rendering_none(self):
        observed = ev.count_record(
            subject="incidents", source="s", count=0, channel_recorded=True
        )
        unobserved = ev.count_record(
            subject="incidents", source="s", count=None, channel_recorded=False
        )
        self.assertNotEqual(observed.evidence_id, unobserved.evidence_id)
        self.assertNotEqual(observed.evidence_class, unobserved.evidence_class)

    def test_the_unavailable_record_states_why_it_is_unknown(self):
        record = ev.count_record(
            subject="incidents", source="s", count=None, channel_recorded=False
        )
        self.assertTrue(
            any("unknown rather than zero" in item for item in record.limitations),
            record.limitations,
        )

    def test_a_recorded_channel_must_supply_a_count(self):
        with self.assertRaises(ev.EvidenceError):
            ev.count_record(
                subject="incidents", source="s", count=None, channel_recorded=True
            )

    def test_a_boolean_count_is_not_an_integer_count(self):
        with self.assertRaises(ev.EvidenceError):
            ev.count_record(
                subject="incidents", source="s", count=True, channel_recorded=True
            )

    def test_a_negative_count_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            ev.count_record(
                subject="incidents", source="s", count=-2, channel_recorded=True
            )

    def test_an_unavailable_count_is_still_displayable(self):
        record = ev.count_record(
            subject="incidents", source="s", count=None, channel_recorded=False
        )
        self.assertTrue(record.displayable)


class UnsupportedCauseTests(unittest.TestCase):
    """`EVIDENCE-DEAD-001` mirrored: a claim its evidence cannot bear is refused."""

    def test_a_measured_complete_record_supports_a_cause(self):
        claim = ev.link("cause", "The rear locked under trail braking.", _measured())
        self.assertEqual(claim.claim_kind, "cause")
        self.assertEqual(claim.evidence_id, _measured().evidence_id)

    def test_a_proxy_record_cannot_support_a_cause(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link("cause", "Tire wear caused the drop.", _measured(evidence_class="proxy"))

    def test_a_scenario_record_cannot_support_a_cause(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link(
                "cause",
                "The caution mix caused the shortfall.",
                _measured(evidence_class="scenario"),
            )

    def test_partial_coverage_cannot_support_a_cause(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link("cause", "It happened because of X.", _measured(coverage="partial"))

    def test_low_confidence_cannot_support_a_cause(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link("cause", "It happened because of X.", _measured(confidence="low"))

    def test_the_same_record_still_supports_a_plain_fact(self):
        claim = ev.link("fact", "Twelve green laps were recorded.", _measured(coverage="partial"))
        self.assertEqual(claim.claim_kind, "fact")

    def test_unavailable_evidence_supports_only_a_statement_of_absence(self):
        """The kind is the guarantee; the wording of the claim is not inspected.

        This test used to permit a `fact` claim on unavailable evidence and
        pass a sentence that happened to be honest. That proved nothing: `link`
        cannot read prose, so the identical call with "Zero incidents occurred."
        was equally accepted, and an unobserved count reached a surface as an
        observed zero. Only a typed unavailability claim is permitted now.
        """
        record = ev.unavailable_record("incidents", "telemetry", "channel not recorded")
        self.assertEqual(
            ev.link(
                ev.CLAIM_UNAVAILABLE, "Incident count is unknown.", record
            ).claim_kind,
            ev.CLAIM_UNAVAILABLE,
        )
        for kind in ("fact", "comparison", "cause", "recommendation"):
            with self.subTest(kind):
                with self.assertRaises(ev.EvidenceError):
                    ev.link(kind, "Something stronger.", record)

    def test_an_unobserved_count_cannot_be_published_as_an_observed_zero(self):
        """`INCIDENT-ABSENCE-001`, at the link rather than in the sentence."""
        unobserved = ev.count_record(
            subject="incidents", source="telemetry", count=None, channel_recorded=False
        )
        self.assertEqual(unobserved.evidence_class, ev.CLASS_UNAVAILABLE)
        self.assertIsNone(unobserved.observations)
        with self.assertRaises(ev.EvidenceError):
            ev.link("fact", "Zero incidents occurred.", unobserved)

    def test_an_observed_zero_is_still_a_fact(self):
        """The rule must not cost the honest case its voice."""
        observed = ev.count_record(
            subject="incidents", source="telemetry", count=0, channel_recorded=True
        )
        claim = ev.link("fact", "Zero incidents occurred.", observed)
        self.assertEqual(claim.claim_kind, "fact")
        self.assertEqual(claim.evidence_class, ev.CLASS_MEASURED)

    def test_an_unavailability_claim_needs_unavailable_evidence(self):
        """Complete observation may not be reported as an absence of observation."""
        with self.assertRaises(ev.EvidenceError):
            ev.link(ev.CLAIM_UNAVAILABLE, "Nothing was recorded.", _measured())

    def test_a_comparison_needs_evidence_of_known_coverage(self):
        record = ev.EvidenceRecord(
            subject="pace",
            evidence_class="derived",
            source="garage61",
            coverage="unknown",
            confidence="low",
        )
        with self.assertRaises(ev.EvidenceError):
            ev.link("comparison", "Two tenths off the reference.", record)

    def test_an_undeclared_claim_kind_is_refused(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link("vibe", "Something.", _measured())

    def test_an_empty_claim_is_refused(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                with self.assertRaises(ev.EvidenceError):
                    ev.link("fact", text, _measured())

    def test_a_link_carries_the_class_so_a_surface_need_not_resolve_it(self):
        claim = ev.link("fact", "Twelve laps.", _measured(evidence_class="proxy"))
        self.assertEqual(claim.evidence_class, "proxy")


class VocabularyTests(unittest.TestCase):
    def test_the_classes_extend_the_race_card_vocabulary_without_dropping_any(self):
        for tag in race_card.EVIDENCE_TAGS:
            self.assertIn(tag, ev.EVIDENCE_CLASSES)

    def test_the_only_addition_is_the_scenario_class(self):
        self.assertEqual(
            set(ev.EVIDENCE_CLASSES) - set(race_card.EVIDENCE_TAGS), {"scenario"}
        )

    def test_only_measured_and_derived_evidence_may_carry_a_cause(self):
        self.assertEqual(set(ev.CLASSES_SUPPORTING_CAUSE), {"measured", "derived"})

    def test_absent_and_unknown_coverage_are_separate_states(self):
        self.assertIn("absent", ev.COVERAGE_STATES)
        self.assertIn("unknown", ev.COVERAGE_STATES)
        self.assertNotEqual("absent", "unknown")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
