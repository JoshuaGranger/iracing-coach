"""The model gate closure: diagnose, gate, beat baselines, represent honestly.

`MODEL-UTILITY-001`, `MODEL-GATE-001`, `MODEL-GATE-BLOCKED-001`,
`MODEL-REPRESENTATION-001`.

The accepted closure asks for synthetic cohort, feature and ablation tests, and
for the rule that inadequate data changes the scope to collection rather than
producing a smaller fabricated model. The classes below are grouped by the four
ordered obligations, plus one that exists purely to keep the diagnostic safe to
run over a private archive.

Every fixture here is synthetic. No test reads a real archive, and the module
under test performs no I/O at all.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import tire_model_gate as gate  # noqa: E402


def observations(count, cohort="car-a|track-a", **features):
    """`count` synthetic observations in one cohort, with the given features."""
    rows = []
    for index in range(count):
        row = {"context_key": cohort}
        for name, value in features.items():
            row[name] = value(index) if callable(value) else value
        rows.append(row)
    return rows


def baselines(events, laps_only_mae, cohort_median_mae):
    return [
        gate.BaselineScore(
            name=gate.BASELINE_LAPS_ONLY,
            held_out_events=events,
            mean_absolute_error=laps_only_mae,
        ),
        gate.BaselineScore(
            name=gate.BASELINE_COHORT_MEDIAN,
            held_out_events=events,
            mean_absolute_error=cohort_median_mae,
        ),
    ]


class CohortDiagnosticTests(unittest.TestCase):
    """Step one: counts and coverage, computed rather than assumed."""

    def test_observations_are_grouped_into_cohorts(self):
        rows = observations(6, cohort="car-a|track-a") + observations(
            7, cohort="car-b|track-b"
        )
        summary = gate.summarize_cohorts(rows)
        self.assertEqual(summary.total_observations, 13)
        self.assertEqual(len(summary.cohorts), 2)

    def test_feature_coverage_is_measured_not_assumed(self):
        # Half the cohort records the feature; coverage must say so rather than
        # inheriting the channel vocabulary's claim that it exists.
        rows = observations(10, wear=lambda index: 1.0 if index % 2 == 0 else None)
        summary = gate.summarize_cohorts(rows)
        self.assertAlmostEqual(summary.cohorts[0].feature_coverage["wear"], 0.5)

    def test_a_feature_present_everywhere_reports_full_coverage(self):
        summary = gate.summarize_cohorts(observations(10, wear=1.0))
        self.assertAlmostEqual(summary.cohorts[0].feature_coverage["wear"], 1.0)

    def test_the_counts_must_add_up(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.AggregateDiagnostics(
                total_observations=99,
                cohorts=(gate.CohortSummary(cohort_key="a", observations=1),),
            )

    def test_an_observation_without_a_cohort_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.summarize_cohorts([{"wear": 1.0}])

    def test_a_non_mapping_observation_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.summarize_cohorts([["context_key", "a"]])

    def test_coverage_outside_zero_to_one_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.CohortSummary(
                cohort_key="a", observations=3, feature_coverage={"wear": 1.5}
            )


class NonSensitiveOutputTests(unittest.TestCase):
    """The diagnostic must be safe to carry back from a private archive."""

    def test_no_observed_value_appears_in_the_payload(self):
        # Distinctive sentinels: if any of them reaches the payload, the
        # diagnostic is exporting data rather than a summary.
        rows = observations(
            8,
            wear=lambda index: 1234.5678 + index,
            note=lambda index: f"secret-value-{index}",
        )
        payload = json.dumps(gate.summarize_cohorts(rows).to_payload())
        self.assertNotIn("1234.5678", payload)
        self.assertNotIn("secret-value", payload)

    def test_the_payload_still_names_the_features_it_counted(self):
        # Names are schema, not data, and the gate is unusable without them.
        payload = gate.summarize_cohorts(observations(8, wear=1.0)).to_payload()
        self.assertIn("wear", payload["features_seen"])

    def test_a_small_cohort_is_counted_but_not_detailed(self):
        rows = observations(2, cohort="tiny", wear=1.0) + observations(
            9, cohort="big", wear=1.0
        )
        summary = gate.summarize_cohorts(rows)
        tiny = next(item for item in summary.cohorts if item.cohort_key == "tiny")
        self.assertFalse(tiny.disclosed)
        self.assertEqual(tiny.observations, 2)
        self.assertEqual(dict(tiny.feature_coverage), {})

    def test_suppressed_cohorts_are_reported_as_a_count(self):
        rows = observations(2, cohort="tiny") + observations(9, cohort="big")
        self.assertEqual(gate.summarize_cohorts(rows).suppressed_cohorts, 1)

    def test_a_suppressed_cohort_cannot_carry_coverage(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.CohortSummary(
                cohort_key="tiny",
                observations=2,
                feature_coverage={"wear": 1.0},
                disclosed=False,
            )

    def test_the_diagnostic_reports_no_extreme_values(self):
        payload = gate.summarize_cohorts(observations(8, wear=1.0)).to_payload()
        serialized = json.dumps(payload)
        for leaked in ("min", "max", "mean", "sum"):
            self.assertNotIn(f'"{leaked}"', serialized)


class GateBlockedTests(unittest.TestCase):
    """`MODEL-GATE-BLOCKED-001`: not run is not the same as passed."""

    def test_an_unrun_aggregate_is_blocked_rather_than_failed(self):
        decision = gate.gate_decision(None)
        self.assertEqual(decision.state, gate.GATE_BLOCKED)
        self.assertFalse(decision.may_attempt_model)

    def test_the_blocked_state_asks_for_authorization_rather_than_a_model(self):
        self.assertEqual(
            gate.gate_decision(None).recommended_action,
            "request_aggregate_authorization",
        )

    def test_a_blocked_gate_never_permits_a_model_attempt(self):
        self.assertFalse(gate.gate_decision(None).may_attempt_model)

    def test_a_gate_that_does_not_open_must_give_a_reason(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.GateDecision(state=gate.GATE_INSUFFICIENT)

    def test_an_unknown_gate_state_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.GateDecision(state="probably_fine", reasons=("x",))


class GateSufficiencyTests(unittest.TestCase):
    """Step two: does the measured data support attempting anything."""

    def test_a_healthy_cohort_opens_the_gate(self):
        summary = gate.summarize_cohorts(observations(12, wear=1.0))
        decision = gate.gate_decision(summary, required_features=("wear",))
        self.assertEqual(decision.state, gate.GATE_READY_FOR_BASELINE)
        self.assertTrue(decision.may_attempt_model)

    def test_too_few_observations_closes_the_gate(self):
        summary = gate.summarize_cohorts(observations(6, wear=1.0))
        decision = gate.gate_decision(summary, minimum_cohort=8)
        self.assertEqual(decision.state, gate.GATE_INSUFFICIENT)

    def test_insufficient_data_plans_collection_rather_than_a_smaller_model(self):
        # The clarification is explicit that this is the only correct response.
        summary = gate.summarize_cohorts(observations(3, wear=1.0))
        decision = gate.gate_decision(summary)
        self.assertEqual(decision.recommended_action, "plan_data_collection")

    def test_a_required_feature_that_is_rarely_recorded_closes_the_gate(self):
        rows = observations(12, wear=lambda index: 1.0 if index < 3 else None)
        decision = gate.gate_decision(
            gate.summarize_cohorts(rows), required_features=("wear",)
        )
        self.assertEqual(decision.state, gate.GATE_INSUFFICIENT)
        self.assertTrue(any("wear" in reason for reason in decision.reasons))

    def test_a_suppressed_cohort_cannot_satisfy_the_gate(self):
        # Four observations are below both the disclosure floor and any honest
        # held-out comparison.
        summary = gate.summarize_cohorts(observations(4, wear=1.0))
        self.assertEqual(gate.gate_decision(summary).state, gate.GATE_INSUFFICIENT)

    def test_the_gate_refuses_a_non_diagnostic_argument(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.gate_decision({"total_observations": 100})


class AblationTests(unittest.TestCase):
    """Removing a feature must change the answer, or the gate measures nothing."""

    def test_removing_a_feature_lowers_its_coverage(self):
        full = gate.summarize_cohorts(observations(10, wear=1.0, camber=1.0))
        ablated = gate.summarize_cohorts(observations(10, wear=1.0, camber=None))
        self.assertAlmostEqual(full.cohorts[0].feature_coverage["camber"], 1.0)
        self.assertEqual(ablated.cohorts[0].feature_coverage.get("camber", 0.0), 0.0)

    def test_ablating_a_required_feature_flips_the_gate(self):
        full = gate.summarize_cohorts(observations(12, wear=1.0, camber=1.0))
        ablated = gate.summarize_cohorts(observations(12, wear=1.0, camber=None))
        self.assertTrue(
            gate.gate_decision(full, required_features=("camber",)).may_attempt_model
        )
        self.assertFalse(
            gate.gate_decision(ablated, required_features=("camber",)).may_attempt_model
        )

    def test_ablating_an_unrequired_feature_does_not_flip_the_gate(self):
        ablated = gate.summarize_cohorts(observations(12, wear=1.0, camber=None))
        self.assertTrue(
            gate.gate_decision(ablated, required_features=("wear",)).may_attempt_model
        )


class BaselineComparisonTests(unittest.TestCase):
    """Step three: beat honest baselines, on the same events, with honesty."""

    def test_a_clearly_better_calibrated_candidate_is_adopted(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.5, interval_coverage=0.8
            ),
            baselines(20, 1.0, 0.9),
        )
        self.assertTrue(verdict.adopt)

    def test_a_candidate_that_only_ties_is_refused(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.9, interval_coverage=0.8
            ),
            baselines(20, 1.0, 0.9),
        )
        self.assertFalse(verdict.adopt)
        self.assertEqual(verdict.representation, gate.REPRESENTATION_UNAVAILABLE)

    def test_a_better_but_miscalibrated_candidate_is_refused(self):
        # Lower error and dishonest intervals. The intervals are what the
        # surface renders as confidence, so this must not ship.
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.3, interval_coverage=0.35
            ),
            baselines(20, 1.0, 0.9),
        )
        self.assertFalse(verdict.adopt)
        self.assertTrue(any("interval" in reason for reason in verdict.reasons))

    def test_too_few_held_out_events_refuses_the_comparison(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=3, mean_absolute_error=0.1, interval_coverage=0.8
            ),
            baselines(3, 1.0, 0.9),
        )
        self.assertFalse(verdict.adopt)

    def test_baselines_scored_on_different_events_are_refused(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.3, interval_coverage=0.8
            ),
            baselines(9, 1.0, 0.9),
        )
        self.assertFalse(verdict.adopt)
        self.assertTrue(any("rather than" in reason for reason in verdict.reasons))

    def test_both_honest_baselines_are_mandatory(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.evaluate_candidate(
                gate.CandidateScore(
                    held_out_events=20, mean_absolute_error=0.3, interval_coverage=0.8
                ),
                [
                    gate.BaselineScore(
                        name=gate.BASELINE_LAPS_ONLY,
                        held_out_events=20,
                        mean_absolute_error=1.0,
                    )
                ],
            )

    def test_the_comparison_uses_the_strongest_baseline(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.5, interval_coverage=0.8
            ),
            baselines(20, 5.0, 0.9),
        )
        self.assertEqual(verdict.best_baseline, gate.BASELINE_COHORT_MEDIAN)

    def test_a_negative_error_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=-1.0, interval_coverage=0.8
            )

    def test_a_non_finite_error_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.CandidateScore(
                held_out_events=20,
                mean_absolute_error=float("inf"),
                interval_coverage=0.8,
            )

    def test_a_coverage_outside_zero_to_one_is_refused(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.3, interval_coverage=1.4
            )

    def test_the_cohort_median_baseline_is_computed_consistently(self):
        rows = [{"context_key": "a", "wear": value} for value in (1.0, 3.0, 2.0)]
        self.assertEqual(gate.cohort_median_baseline(rows, target_field="wear"), 2.0)

    def test_the_cohort_median_of_nothing_is_none(self):
        self.assertIsNone(gate.cohort_median_baseline([], target_field="wear"))


class RepresentationTests(unittest.TestCase):
    """Step four: say only what the evidence supports."""

    def test_no_verdict_at_all_is_unavailable_rather_than_blank(self):
        self.assertEqual(
            gate.model_representation(None), gate.REPRESENTATION_UNAVAILABLE
        )

    def test_a_thin_but_valid_result_is_low_confidence(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=10, mean_absolute_error=0.5, interval_coverage=0.75
            ),
            baselines(10, 1.0, 0.9),
        )
        self.assertTrue(verdict.adopt)
        self.assertEqual(verdict.representation, gate.REPRESENTATION_LOW_CONFIDENCE)

    def test_a_strong_well_calibrated_result_is_high_confidence(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=40, mean_absolute_error=0.4, interval_coverage=0.8
            ),
            baselines(40, 1.0, 0.9),
        )
        self.assertEqual(verdict.representation, gate.REPRESENTATION_HIGH_CONFIDENCE)

    def test_a_rejected_candidate_can_never_be_shown_confidently(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.ModelVerdict(
                adopt=False,
                representation=gate.REPRESENTATION_HIGH_CONFIDENCE,
                reasons=("x",),
            )

    def test_a_verdict_must_say_what_decided_it(self):
        with self.assertRaises(gate.TireModelGateError):
            gate.ModelVerdict(adopt=True, representation=gate.REPRESENTATION_LOW_CONFIDENCE)

    def test_every_refusal_still_produces_a_renderable_state(self):
        verdict = gate.evaluate_candidate(
            gate.CandidateScore(
                held_out_events=20, mean_absolute_error=0.95, interval_coverage=0.8
            ),
            baselines(20, 1.0, 0.9),
        )
        self.assertIn(verdict.representation, gate.REPRESENTATION_STATES)
        self.assertTrue(verdict.reasons)


class NoIoTests(unittest.TestCase):
    """The module decides; it does not read the archive."""

    def test_the_module_touches_no_filesystem_or_clock(self):
        source = (SCRIPTS / "tire_model_gate.py").read_text(encoding="utf-8")
        for forbidden in ("open(", "Path(", "time.", "datetime", "requests", "urllib"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
