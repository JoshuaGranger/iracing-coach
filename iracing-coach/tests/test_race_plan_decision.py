"""Frozen vectors and adversarial cases for the one race-plan decision.

`FUEL-CONSISTENCY-001`. The exact/epsilon/exact-multiple/caution table below is
the frozen contract: the same table is what a C# consumer must reproduce, so it
is declared once here as data rather than restated inside assertions.

The cases that matter most are the two that produced a full-stop contradiction
in the shipped product, and they are asserted end to end - through the decision,
through the race card sentence, and through the report sentence - because the
defect was never in the arithmetic alone. It was in a consumer re-deriving a
count from a rounded display scalar, and only an end-to-end assertion can prove
that re-derivation is gone.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import race_card  # noqa: E402
import race_plan_decision as rpd  # noqa: E402
import reporting  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "contract_validation_for_race_plan", WORKSPACE_ROOT / "tools" / "contract_validation.py"
)
assert _SPEC is not None and _SPEC.loader is not None
contract_validation = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = contract_validation
_SPEC.loader.exec_module(contract_validation)


#: The frozen decision table. Each row is (label, scheduled laps, exact range,
#: expected stops). `rounded_stops` records what a consumer would have decided
#: from the one-decimal display value, so a row where the two differ is a row
#: that used to render a contradiction.
FROZEN_VECTORS = (
    # label, scheduled, exact range, expected stops, stops from rounded range
    ("epsilon-below-one-stint", 50.0, 49.96, 1, 0),
    ("epsilon-above-three-stints", 200.0, 66.66, 3, 2),
    ("exact-multiple-four-stints", 200.0, 50.0, 3, 3),
    ("exact-multiple-one-stint", 50.0, 50.0, 0, 0),
    ("comfortably-inside-one-stint", 40.0, 55.25, 0, 0),
    ("just-over-two-stints", 100.0, 49.999, 2, 1),
    ("long-race-many-stops", 500.0, 62.5, 7, 7),
)


def _rounded_stops(scheduled: float, exact_range: float) -> int:
    """What the removed consumer arithmetic produced, for comparison only."""
    displayed = round(exact_range, 1)
    return max(0, math.ceil(scheduled / displayed - 1e-9) - 1)


class FrozenVectorTests(unittest.TestCase):
    def test_the_table_decides_the_declared_stop_count(self):
        for label, scheduled, exact_range, expected, _ in FROZEN_VECTORS:
            with self.subTest(label):
                decision = rpd.decide_from_range(
                    scheduled_laps=scheduled, all_green_range_laps=exact_range
                )
                self.assertTrue(decision.usable)
                self.assertEqual(decision.minimum_stops, expected)
                self.assertEqual(decision.stints, expected + 1)

    def test_the_declared_rounded_counts_are_what_rounding_actually_produces(self):
        """Guard the table itself, so a stale row cannot make the suite agree."""
        for label, scheduled, exact_range, _, rounded in FROZEN_VECTORS:
            with self.subTest(label):
                self.assertEqual(_rounded_stops(scheduled, exact_range), rounded)

    def test_at_least_two_rows_prove_rounding_and_exactness_disagree(self):
        disagreeing = [row[0] for row in FROZEN_VECTORS if row[3] != row[4]]
        self.assertGreaterEqual(len(disagreeing), 2, disagreeing)

    def test_every_row_covers_the_distance_it_decided(self):
        for label, scheduled, exact_range, expected, _ in FROZEN_VECTORS:
            with self.subTest(label):
                self.assertGreaterEqual((expected + 1) * exact_range, scheduled)
                # One fewer stint must be genuinely short, or the count is
                # padded rather than minimal.
                if expected > 0:
                    self.assertLess(expected * exact_range, scheduled)

    def test_the_margin_is_the_slack_on_the_final_stint(self):
        for label, scheduled, exact_range, expected, _ in FROZEN_VECTORS:
            with self.subTest(label):
                decision = rpd.decide_from_range(
                    scheduled_laps=scheduled, all_green_range_laps=exact_range
                )
                self.assertAlmostEqual(
                    decision.final_stint_margin_laps,
                    (expected + 1) * exact_range - scheduled,
                    places=9,
                )
                self.assertGreaterEqual(decision.final_stint_margin_laps, 0.0)


class NoStopLanguageTests(unittest.TestCase):
    def test_no_stop_language_is_refused_whenever_a_stop_is_decided(self):
        for label, scheduled, exact_range, expected, _ in FROZEN_VECTORS:
            with self.subTest(label):
                decision = rpd.decide_from_range(
                    scheduled_laps=scheduled, all_green_range_laps=exact_range
                )
                self.assertEqual(decision.no_stop_language_permitted, expected == 0)

    def test_an_unusable_decision_permits_no_no_stop_language(self):
        decision = rpd.decide(
            scheduled_laps=None, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        self.assertFalse(decision.usable)
        self.assertFalse(decision.no_stop_language_permitted)

    def test_a_decision_cannot_be_constructed_with_contradictory_counts(self):
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.RacePlanDecision(
                status=rpd.STATUS_USABLE,
                scheduled_laps=50.0,
                all_green_range_laps=49.96,
                minimum_stops=1,
                stints=3,
                equal_stint_pit_targets=(25.0,),
            )

    def test_a_decision_needs_one_target_per_decided_stop(self):
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.RacePlanDecision(
                status=rpd.STATUS_USABLE,
                scheduled_laps=50.0,
                all_green_range_laps=49.96,
                minimum_stops=1,
                stints=2,
                equal_stint_pit_targets=(),
            )

    def test_an_unusable_decision_may_not_carry_decided_numbers(self):
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.RacePlanDecision(
                status=rpd.STATUS_INSUFFICIENT_EVIDENCE, minimum_stops=0
            )


class ReserveTests(unittest.TestCase):
    def test_the_reserve_is_exactly_one_green_lap(self):
        decision = rpd.decide(
            scheduled_laps=50.0, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        self.assertEqual(decision.reserve_green_laps, 1.0)
        self.assertAlmostEqual(decision.reserve_fuel_l, 2.0)
        self.assertAlmostEqual(decision.usable_fuel_l, 98.0)
        self.assertAlmostEqual(decision.all_green_range_laps, 49.0)

    def test_capacity_at_or_below_the_reserve_is_absent_rather_than_zero_range(self):
        for capacity in (2.0, 1.5, 0.0):
            with self.subTest(capacity=capacity):
                decision = rpd.decide(
                    scheduled_laps=50.0,
                    green_burn_l_per_lap=2.0,
                    maximum_start_fuel_l=capacity,
                )
                self.assertEqual(decision.status, rpd.STATUS_INSUFFICIENT_EVIDENCE)
                self.assertIsNone(decision.all_green_range_laps)

    def test_the_reserve_is_withheld_before_the_range_is_computed(self):
        """A 49.0-lap range over 50 laps is one stop, not zero."""
        decision = rpd.decide(
            scheduled_laps=50.0, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        self.assertEqual(decision.minimum_stops, 1)


class RejectedInputTests(unittest.TestCase):
    def test_a_boolean_burn_rate_is_not_one_litre_per_lap(self):
        decision = rpd.decide(
            scheduled_laps=50.0, green_burn_l_per_lap=True, maximum_start_fuel_l=100.0
        )
        self.assertEqual(decision.status, rpd.STATUS_INSUFFICIENT_EVIDENCE)

    def test_non_finite_inputs_do_not_decide(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                decision = rpd.decide(
                    scheduled_laps=50.0,
                    green_burn_l_per_lap=value,
                    maximum_start_fuel_l=100.0,
                )
                self.assertEqual(decision.status, rpd.STATUS_INSUFFICIENT_EVIDENCE)

    def test_a_hybrid_finish_constraint_decides_nothing_even_with_full_evidence(self):
        decision = rpd.decide(
            scheduled_laps=50.0,
            green_burn_l_per_lap=2.0,
            maximum_start_fuel_l=100.0,
            hybrid_limits=True,
        )
        self.assertEqual(decision.status, rpd.STATUS_HYBRID_UNRESOLVED)
        self.assertIsNone(decision.minimum_stops)

    def test_a_negative_or_zero_distance_decides_nothing(self):
        for distance in (0.0, -10.0):
            with self.subTest(distance=distance):
                decision = rpd.decide_from_range(
                    scheduled_laps=distance, all_green_range_laps=50.0
                )
                self.assertEqual(decision.status, rpd.STATUS_INSUFFICIENT_EVIDENCE)

    def test_stint_count_refuses_a_non_positive_range_instead_of_guessing(self):
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.stint_count(50.0, 0.0)


class CautionScenarioTests(unittest.TestCase):
    def test_the_caution_mix_is_scenario_evidence_and_says_so(self):
        decision = rpd.decide(
            scheduled_laps=50.0,
            green_burn_l_per_lap=2.0,
            maximum_start_fuel_l=100.0,
            caution_burn_l_per_lap=1.0,
            observed_caution_fraction=0.25,
        )
        scenario = decision.caution_scenario
        self.assertIsNotNone(scenario)
        self.assertEqual(scenario.evidence_class, "scenario")
        self.assertIn("not established", scenario.limitation)

    def test_a_lighter_caution_burn_extends_the_scenario_range(self):
        decision = rpd.decide(
            scheduled_laps=50.0,
            green_burn_l_per_lap=2.0,
            maximum_start_fuel_l=100.0,
            caution_burn_l_per_lap=1.0,
            observed_caution_fraction=0.5,
        )
        self.assertAlmostEqual(decision.caution_scenario.mixed_burn_l_per_lap, 1.5)
        self.assertAlmostEqual(decision.caution_scenario.range_laps, 98.0 / 1.5)
        self.assertGreater(
            decision.caution_scenario.range_laps, decision.all_green_range_laps
        )

    def test_a_zero_caution_fraction_produces_no_scenario_at_all(self):
        decision = rpd.decide(
            scheduled_laps=50.0,
            green_burn_l_per_lap=2.0,
            maximum_start_fuel_l=100.0,
            caution_burn_l_per_lap=1.0,
            observed_caution_fraction=0.0,
        )
        self.assertIsNone(decision.caution_scenario)

    def test_an_out_of_range_caution_fraction_produces_no_scenario(self):
        for fraction in (-0.1, 1.5, float("nan"), True):
            with self.subTest(fraction=fraction):
                decision = rpd.decide(
                    scheduled_laps=50.0,
                    green_burn_l_per_lap=2.0,
                    maximum_start_fuel_l=100.0,
                    observed_caution_fraction=fraction,
                )
                self.assertIsNone(decision.caution_scenario)

    def test_the_scenario_never_changes_the_decided_stop_count(self):
        without = rpd.decide(
            scheduled_laps=50.0, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        with_scenario = rpd.decide(
            scheduled_laps=50.0,
            green_burn_l_per_lap=2.0,
            maximum_start_fuel_l=100.0,
            caution_burn_l_per_lap=0.5,
            observed_caution_fraction=0.9,
        )
        self.assertEqual(without.minimum_stops, with_scenario.minimum_stops)
        self.assertEqual(
            without.all_green_range_laps, with_scenario.all_green_range_laps
        )


class ReplanTests(unittest.TestCase):
    def test_replanning_uses_the_exact_range_not_a_rounded_one(self):
        decision = rpd.decide_from_range(
            scheduled_laps=10.0, all_green_range_laps=49.96
        )
        replanned = decision.replan(50.0)
        self.assertEqual(replanned.minimum_stops, 1)
        self.assertAlmostEqual(replanned.all_green_range_laps, 49.96)

    def test_replanning_carries_capacity_and_reserve_forward(self):
        decision = rpd.decide(
            scheduled_laps=50.0, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        replanned = decision.replan(120.0)
        self.assertAlmostEqual(replanned.reserve_fuel_l, decision.reserve_fuel_l)
        self.assertAlmostEqual(replanned.usable_fuel_l, decision.usable_fuel_l)
        self.assertEqual(replanned.minimum_stops, 2)

    def test_an_unusable_decision_cannot_be_replanned(self):
        decision = rpd.decide(
            scheduled_laps=None, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        )
        with self.assertRaises(rpd.RacePlanDecisionError):
            decision.replan(50.0)


class PayloadTests(unittest.TestCase):
    def test_a_round_trip_preserves_the_decision(self):
        decision = rpd.decide(
            scheduled_laps=200.0,
            green_burn_l_per_lap=3.0,
            maximum_start_fuel_l=203.0,
            caution_burn_l_per_lap=1.5,
            observed_caution_fraction=0.2,
        )
        restored = rpd.from_payload(decision.to_payload())
        self.assertEqual(restored.minimum_stops, decision.minimum_stops)
        self.assertAlmostEqual(
            restored.all_green_range_laps, decision.all_green_range_laps
        )
        self.assertEqual(
            restored.caution_scenario.minimum_stops,
            decision.caution_scenario.minimum_stops,
        )

    def test_a_future_decision_version_is_refused_rather_than_partially_read(self):
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        payload["decision_version"] = rpd.RACE_PLAN_DECISION_VERSION + 1
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_a_missing_or_non_integer_version_is_refused(self):
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        for value in (None, "1", 1.0, True):
            with self.subTest(value=value):
                broken = dict(payload)
                broken["decision_version"] = value
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_a_payload_whose_count_contradicts_its_range_is_refused(self):
        """A contradictory record is rejected, not quietly re-decided.

        This test previously asserted that the reader silently replaced the
        transported count with one derived from the transported range. That is
        an improvement on adopting the tampered count and still wrong: it
        overrides the producer's decision on the reader's own authority and
        returns a decision no producer ever made. A record that disagrees with
        itself has no readable content.
        """
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        payload["minimum_stops"] = 0
        payload["stints"] = 1
        payload["equal_stint_pit_targets"] = []
        payload["no_stop_language_permitted"] = True
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_a_stop_count_that_its_own_range_cannot_produce_is_refused(self):
        payload = rpd.decide_from_range(
            scheduled_laps=200.0, all_green_range_laps=66.66
        ).to_payload()
        self.assertEqual(payload["minimum_stops"], 3)
        # 200 laps on a 66.66-lap range needs four stints. Claiming three while
        # keeping every other field internally consistent is the understated
        # stop count from the module docstring, transported instead of derived.
        payload["minimum_stops"] = 2
        payload["stints"] = 3
        payload["equal_stint_pit_targets"] = [66.0, 133.0]
        payload["final_stint_margin_laps"] = 3 * 66.66 - 200.0
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_no_stop_language_must_agree_with_the_transported_count(self):
        payload = rpd.decide_from_range(
            scheduled_laps=40.0, all_green_range_laps=50.0
        ).to_payload()
        self.assertTrue(payload["no_stop_language_permitted"])
        payload["no_stop_language_permitted"] = False
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_every_required_key_must_be_present(self):
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        for key in rpd.REQUIRED_PAYLOAD_KEYS:
            with self.subTest(key=key):
                broken = {name: value for name, value in payload.items() if name != key}
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_wrong_types_in_decided_fields_are_refused(self):
        base = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        for key, value in (
            ("scheduled_laps", "50.0"),
            ("scheduled_laps", None),
            ("scheduled_laps", float("inf")),
            ("all_green_range_laps", True),
            ("all_green_range_laps", -1.0),
            ("minimum_stops", 1.0),
            ("minimum_stops", True),
            ("minimum_stops", "1"),
            ("stints", None),
            ("re_decidable", "true"),
            ("no_stop_language_permitted", "false"),
            ("final_stint_margin_laps", "0.04"),
            ("reserve_green_laps", None),
            ("equal_stint_pit_targets", {}),
            ("equal_stint_pit_targets", ["25.0"]),
            ("limitations", "not a list"),
            ("caution_scenario", 5),
            ("green_burn_l_per_lap", "2.0"),
        ):
            with self.subTest(key=key, value=value):
                broken = dict(base)
                broken[key] = value
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_a_transported_margin_that_its_own_numbers_deny_is_refused(self):
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        payload["final_stint_margin_laps"] = 25.0
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_pit_targets_must_be_ordered_within_the_distance(self):
        payload = rpd.decide_from_range(
            scheduled_laps=200.0, all_green_range_laps=66.66
        ).to_payload()
        self.assertEqual(len(payload["equal_stint_pit_targets"]), 3)
        for targets in (
            [100.0, 50.0, 150.0],
            [0.0, 100.0, 150.0],
            [50.0, 100.0, 200.0],
            [50.0, 100.0],
        ):
            with self.subTest(targets=targets):
                broken = dict(payload)
                broken["equal_stint_pit_targets"] = targets
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_a_non_usable_payload_carrying_decided_numbers_is_refused(self):
        payload = rpd.decide(
            scheduled_laps=None, green_burn_l_per_lap=2.0, maximum_start_fuel_l=100.0
        ).to_payload()
        self.assertNotEqual(payload["status"], rpd.STATUS_USABLE)
        for key, value in (
            ("minimum_stops", 0),
            ("all_green_range_laps", 50.0),
            ("stints", 1),
            ("no_stop_language_permitted", True),
            ("equal_stint_pit_targets", [25.0]),
        ):
            with self.subTest(key=key):
                broken = dict(payload)
                broken[key] = value
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_a_malformed_caution_scenario_is_refused_rather_than_dropped(self):
        payload = rpd.decide(
            scheduled_laps=200.0,
            green_burn_l_per_lap=3.0,
            maximum_start_fuel_l=203.0,
            caution_burn_l_per_lap=1.5,
            observed_caution_fraction=0.2,
        ).to_payload()
        self.assertIsNotNone(payload["caution_scenario"])
        for key, value in (
            ("observed_caution_fraction", 0.0),
            ("observed_caution_fraction", 1.5),
            ("observed_caution_fraction", "0.2"),
            ("mixed_burn_l_per_lap", -1.0),
            ("range_laps", None),
            ("minimum_stops", 1.5),
        ):
            with self.subTest(key=key, value=value):
                broken = dict(payload)
                scenario = dict(broken["caution_scenario"])
                scenario[key] = value
                broken["caution_scenario"] = scenario
                with self.assertRaises(rpd.RacePlanDecisionError):
                    rpd.from_payload(broken)

    def test_a_legacy_decision_keeps_a_count_its_rounded_range_could_produce(self):
        """The rounded-range interval, not an exact equality, bounds a legacy count."""
        decision = rpd.from_legacy_forecast(
            {
                "status": rpd.STATUS_USABLE,
                "minimum_stops_all_green": 1,
                "all_green_range_laps": 50.0,
                "scheduled_laps": 50.0,
                "equal_stint_pit_targets_all_green": [25.0],
            }
        )
        self.assertFalse(decision.re_decidable)
        restored = rpd.from_payload(decision.to_payload())
        self.assertEqual(restored.minimum_stops, 1)
        self.assertFalse(restored.re_decidable)

    def test_a_legacy_count_outside_the_rounding_interval_is_refused(self):
        payload = rpd.from_legacy_forecast(
            {
                "status": rpd.STATUS_USABLE,
                "minimum_stops_all_green": 1,
                "all_green_range_laps": 50.0,
                "scheduled_laps": 50.0,
                "equal_stint_pit_targets_all_green": [25.0],
            }
        ).to_payload()
        # 50 laps on a range near 50 needs one or two stints, never five.
        payload["minimum_stops"] = 4
        payload["stints"] = 5
        payload["equal_stint_pit_targets"] = [10.0, 20.0, 30.0, 40.0]
        payload["final_stint_margin_laps"] = max(0.0, 5 * 50.0 - 50.0)
        with self.assertRaises(rpd.RacePlanDecisionError):
            rpd.from_payload(payload)

    def test_every_declared_key_is_present_in_the_payload(self):
        payload = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        ).to_payload()
        for key in (
            "all_green_range_laps",
            "caution_scenario",
            "decision_version",
            "equal_stint_pit_targets",
            "final_stint_margin_laps",
            "minimum_stops",
            "no_stop_language_permitted",
            "re_decidable",
            "reserve_green_laps",
            "scheduled_laps",
            "status",
            "stints",
        ):
            self.assertIn(key, payload)


class LegacyForecastTests(unittest.TestCase):
    def _legacy(self, stops: int, targets: list[float]) -> dict:
        return {
            "status": "usable",
            "scheduled_laps": 50.0,
            "all_green_range_laps": 50.0,  # rounded from an exact 49.96
            "minimum_stops_all_green": stops,
            "equal_stint_pit_targets_all_green": targets,
            "operational_reserve_green_laps": 2.0,
            "operational_reserve_fuel_l": 4.0,
        }

    def test_the_stored_exact_count_is_adopted_over_the_rounded_range(self):
        decision = rpd.from_legacy_forecast(self._legacy(1, [25.0]))
        self.assertEqual(decision.minimum_stops, 1)
        self.assertFalse(decision.no_stop_language_permitted)

    def test_a_legacy_decision_refuses_to_be_replanned_through_replan(self):
        decision = rpd.from_legacy_forecast(self._legacy(1, [25.0]))
        self.assertFalse(decision.re_decidable)
        with self.assertRaises(rpd.RacePlanDecisionError):
            decision.replan(80.0)

    def test_a_new_distance_the_rounding_cannot_obscure_is_still_decided(self):
        forecast = self._legacy(2, [33.0, 67.0])
        forecast["scheduled_laps"] = 100.0
        forecast["all_green_range_laps"] = 34.7
        decision = rpd.from_legacy_forecast(forecast, scheduled_laps=15.0)
        self.assertTrue(decision.usable)
        self.assertEqual(decision.minimum_stops, 0)
        self.assertIn("every range it could have been", decision.limitations[0])

    def test_a_new_distance_the_rounding_straddles_decides_nothing(self):
        # A stored range of 50.0 is anything in [49.95, 50.05]. Over 100 laps
        # that is two stints at one end and three at the other.
        decision = rpd.from_legacy_forecast(self._legacy(1, [25.0]), scheduled_laps=100.0)
        self.assertEqual(decision.status, rpd.STATUS_ROUNDED_RANGE_UNDECIDABLE)
        self.assertFalse(decision.no_stop_language_permitted)
        self.assertIn("Re-analyze", decision.limitations[0])

    def test_the_straddle_test_uses_both_ends_of_the_rounding_interval(self):
        """A range of 50.0 could be 49.95 (two stints) or 50.05 (one)."""
        self.assertEqual(rpd.stint_count(50.0, 49.95), 2)
        self.assertEqual(rpd.stint_count(50.0, 50.05), 1)

    def test_a_legacy_margin_is_never_negative_beside_a_zero_stop_count(self):
        forecast = self._legacy(0, [])
        forecast["all_green_range_laps"] = 49.9
        decision = rpd.from_legacy_forecast(forecast)
        self.assertEqual(decision.minimum_stops, 0)
        self.assertGreaterEqual(decision.final_stint_margin_laps, 0.0)

    def test_a_target_count_that_disagrees_with_the_stop_count_is_refused(self):
        self.assertIsNone(rpd.from_legacy_forecast(self._legacy(2, [25.0])))

    def test_a_boolean_stop_count_is_refused(self):
        self.assertIsNone(rpd.from_legacy_forecast(self._legacy(True, [])))

    def test_an_unusable_legacy_forecast_yields_nothing(self):
        forecast = self._legacy(1, [25.0])
        forecast["status"] = "insufficient_fuel_or_distance_evidence"
        self.assertIsNone(rpd.from_legacy_forecast(forecast))


def _analysis_with(decision: rpd.RacePlanDecision) -> dict:
    return {
        "race_summary": {"scheduled_laps": decision.scheduled_laps},
        "strategy": {
            "measured_green_fuel_gal_per_lap": 0.5,
            "forecast": {
                "status": decision.status,
                "scheduled_laps": decision.scheduled_laps,
                "all_green_range_laps": round(decision.all_green_range_laps, 1),
                "minimum_stops_all_green": decision.minimum_stops,
                "equal_stint_pit_targets_all_green": [
                    round(value, 1) for value in decision.equal_stint_pit_targets
                ],
                "operational_reserve_green_laps": decision.reserve_green_laps,
                "race_plan_decision": decision.to_payload(),
            },
        },
    }


class ConsumerContradictionTests(unittest.TestCase):
    """The end-to-end proof: no surface may reparse the rounded scalar."""

    def test_the_card_never_says_no_stop_when_one_stop_is_decided(self):
        for label, scheduled, exact_range, expected, rounded in FROZEN_VECTORS:
            if expected == rounded:
                continue
            with self.subTest(label):
                decision = rpd.decide_from_range(
                    scheduled_laps=scheduled, all_green_range_laps=exact_range
                )
                claim = race_card._strategy_claim(
                    _analysis_with(decision), [], words=24
                )
                self.assertNotIn("No fuel stop needed", claim["text"])
                self.assertIn(f"Plan {expected} fuel stop", claim["text"])

    def test_the_in_race_rule_never_says_stay_out_when_a_stop_is_decided(self):
        decision = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        )
        claim = race_card._fuel_response_claim(_analysis_with(decision))
        self.assertNotIn("Stay out", claim["text"])
        self.assertIn("Target Lap", claim["text"])

    def test_a_genuine_no_stop_race_still_says_no_stop(self):
        decision = rpd.decide_from_range(
            scheduled_laps=40.0, all_green_range_laps=55.25
        )
        claim = race_card._strategy_claim(_analysis_with(decision), [], words=24)
        self.assertIn("No fuel stop needed", claim["text"])

    def test_the_card_withholds_a_plan_it_cannot_re_decide(self):
        legacy = _analysis_with(
            rpd.decide_from_range(scheduled_laps=50.0, all_green_range_laps=49.96)
        )
        del legacy["strategy"]["forecast"]["race_plan_decision"]
        claim = race_card._strategy_claim(legacy, [], words=24)
        self.assertIn("Plan 1 fuel stop", claim["text"])
        # At 80 laps the rounding cannot change the answer, so the plan is
        # still decided from the legacy evidence.
        wider = race_card._strategy_claim(legacy, [], words=24, planned_laps=80.0)
        self.assertIn("Plan 1 fuel stop", wider["text"])
        # At 100 laps the stored range straddles a stint boundary, so the card
        # falls back to the observed burn rather than inventing a plan.
        straddling = race_card._strategy_claim(
            legacy, [], words=24, planned_laps=100.0
        )
        self.assertIn("Observed burn", straddling["text"])

    def test_the_report_sentence_agrees_with_the_decision(self):
        decision = rpd.decide_from_range(
            scheduled_laps=50.0, all_green_range_laps=49.96
        )
        analysis = _analysis_with(decision)
        analysis["runs"] = [{"fuel": {"start_l": 100.0}, "green_laps": 20}]
        text = reporting._next_race_baseline(analysis, {})
        self.assertIsNotNone(text)
        self.assertNotIn("without a stop", text)
        self.assertIn("at least 1 fuel stop", text)


class GeneratedContractTests(unittest.TestCase):
    """Every decision this producer can emit must satisfy the shipped schema."""

    @staticmethod
    def _schema() -> dict:
        return json.loads(
            (WORKSPACE_ROOT / "contracts" / "race-plan-decision-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )

    def test_every_frozen_vector_conforms(self):
        schema = self._schema()
        for label, scheduled, exact_range, _, _ in FROZEN_VECTORS:
            with self.subTest(label):
                payload = rpd.decide_from_range(
                    scheduled_laps=scheduled, all_green_range_laps=exact_range
                ).to_payload()
                contract_validation.assert_valid(payload, schema, label)

    def test_every_status_conforms_and_is_declared(self):
        schema = self._schema()
        declared = set(schema["properties"]["status"]["enum"])
        self.assertEqual(declared, set(rpd.PLAN_STATUSES))
        for status in rpd.PLAN_STATUSES:
            with self.subTest(status):
                payload = rpd.RacePlanDecision(status=status).to_payload() if status != (
                    rpd.STATUS_USABLE
                ) else rpd.decide_from_range(
                    scheduled_laps=50.0, all_green_range_laps=49.96
                ).to_payload()
                contract_validation.assert_valid(payload, schema, status)

    def test_a_decision_carrying_a_caution_scenario_conforms(self):
        payload = rpd.decide(
            scheduled_laps=200.0,
            green_burn_l_per_lap=3.0,
            maximum_start_fuel_l=203.0,
            caution_burn_l_per_lap=1.5,
            observed_caution_fraction=0.2,
        ).to_payload()
        contract_validation.assert_valid(payload, self._schema(), "caution scenario")

    def test_the_schema_declares_the_producer_version(self):
        self.assertEqual(
            self._schema()["properties"]["decision_version"]["const"],
            rpd.RACE_PLAN_DECISION_VERSION,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
