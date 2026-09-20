from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/analyze-iracing-race/scripts"
sys.path.insert(0, str(SCRIPTS))
from event_planning import build_event_plan
from race_plan_decision import decide, from_payload, RacePlanDecisionError, LITRES_PER_GALLON
import mcp_server
import workflow


class EventPlanningTests(unittest.TestCase):
    def test_manual_estimate_keeps_one_lap_reserve_and_decides_stops(self):
        result = build_event_plan(distance=50, green_burn_gallons=.4, start_fuel_gallons=20)
        plan = from_payload(result["race_plan_decision"])
        self.assertEqual(result["evidence"], "Estimate")
        self.assertTrue(result["estimated"])
        self.assertAlmostEqual(plan.all_green_range_laps, 49)
        self.assertEqual(plan.minimum_stops, 1)
        self.assertFalse(plan.no_stop_language_permitted)
        self.assertEqual(plan.equal_stint_pit_targets, (25,))
        self.assertIn("User-entered", " ".join(result["assumptions"]))

    def test_different_distance_gets_a_new_authoritative_decision(self):
        source = decide(scheduled_laps=50, green_burn_l_per_lap=1, maximum_start_fuel_l=50.95)
        result = build_event_plan(distance=49, analysis={"strategy": {"forecast": {"race_plan_decision": source.to_payload()}}})
        plan = from_payload(result["race_plan_decision"])
        self.assertEqual(source.minimum_stops, 1)
        self.assertEqual(plan.minimum_stops, 0)
        self.assertEqual(plan.scheduled_laps, 49)
        self.assertAlmostEqual(plan.all_green_range_laps, 49.95)

    def test_unrecorded_event_without_fuel_is_preparation_not_zero_stops(self):
        result = build_event_plan(distance=75)
        self.assertEqual(result["evidence"], "Preparation")
        self.assertIsNone(result["race_plan_decision"]["minimum_stops"])
        self.assertFalse(result["race_plan_decision"]["no_stop_language_permitted"])
        self.assertEqual(len(result["missing"]), 2)

    def test_timed_estimate_requires_pace(self):
        missing = build_event_plan(distance=30, distance_mode="Minutes", green_burn_gallons=.4, start_fuel_gallons=20)
        self.assertIsNone(missing["scheduled_laps"])
        result = build_event_plan(distance=30, distance_mode="Minutes", lap_seconds=30, green_burn_gallons=.4, start_fuel_gallons=20)
        self.assertEqual(result["scheduled_laps"], 60)
        self.assertEqual(result["evidence"], "Estimate")

    def test_timed_pace_uses_only_backend_eligible_clean_laps(self):
        analysis = {"runs": [{"valid_green_lap_numbers": [2, 3]}],
                    "laps": [{"lap": 1, "lap_time_s": 2}, {"lap": 2, "lap_time_s": 29}, {"lap": 3, "lap_time_s": 31}, {"lap": 4, "lap_time_s": 180}]}
        result = build_event_plan(distance=30, distance_mode="Minutes", analysis=analysis)
        self.assertEqual(result["scheduled_laps"], 60)

    def test_malformed_authority_is_not_silently_replaced(self):
        with self.assertRaises(RacePlanDecisionError):
            build_event_plan(distance=40, analysis={"strategy": {"forecast": {"race_plan_decision": {"decision_version": 999}}}})

    def test_invalid_inputs_fail_clearly(self):
        for invalid in (float("nan"), float("inf"), -1, 0, True, "50", 1001):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_event_plan(distance=invalid)
        with self.assertRaises(ValueError): build_event_plan(distance=2.5)
        with self.assertRaises(ValueError): build_event_plan(distance=50, green_burn_gallons=-2)

    def test_mcp_planning_dispatch_is_offline_and_returns_decision(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root, mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", Path(root)):
            result = mcp_server.call_tool("plan_iracing_race", {"archive_root": root, "distance": 40, "green_burn_gallons": .4, "start_fuel_gallons": 20})
            self.assertTrue(result["race_plan_decision"]["no_stop_language_permitted"])

    def test_post_race_mcp_default_requests_native_rate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root, mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", Path(root)), mock.patch.object(mcp_server, "DEFAULT_IRACING_ROOT", Path(root)), mock.patch.object(mcp_server, "_workflow_function") as workflow_function:
            mcp_server.call_tool("analyze_iracing_race", {"archive_root": root, "iracing_root": root})
            self.assertIsNone(workflow_function.return_value.call_args.kwargs["target_hz"])

    def test_native_analysis_load_retains_records_lost_at_twenty_hz(self):
        fixture = Path(__file__).resolve().parents[2] / "test-data/ibt/synthetic-race.ibt"
        native = workflow._load_analysis_telemetry([str(fixture)], None)
        reduced = workflow._load_analysis_telemetry([str(fixture)], 20)
        self.assertEqual(native["sample_rate_hz"], 60)
        self.assertEqual(native["sample_count"], native["source_record_count"])
        self.assertGreater(native["sample_count"], reduced["sample_count"])
        self.assertNotEqual(workflow._analysis_cache_identity([], None, "pipeline", {}), workflow._analysis_cache_identity([], 20, "pipeline", {}))


if __name__ == "__main__": unittest.main()
