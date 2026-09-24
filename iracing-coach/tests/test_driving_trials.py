from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import driving_trials  # noqa: E402
from analysis_engine import investigation_lap_exclusions  # noqa: E402
from race_investigation import build_race_investigation  # noqa: E402
from test_race_investigation import investigation_fixture  # noqa: E402


def baseline_analysis() -> dict:
    """The fixture's late fade, but with every lap screened so it can become a test."""
    analysis = investigation_fixture()
    for lap in analysis["laps"]:
        lap["clean_context"]["traffic_proximity_fraction"] = 0.0
    analysis["damage_repair"]["incident_points"]["events"] = []
    analysis["analysis_id"] = "baseline"
    analysis["identity"] = {"session_start": "2026-09-20T20:09:09+00:00", "track_name": "Test Oval", "track_config": "Oval",
                            "track_id": 7, "car_id": 116, "car_name": "Test Car", "car_path": "testcar",
                            "setup_fingerprint": "abc", "subsession_id": 10, "event_type": "Race"}
    analysis["race_investigation"] = build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))
    return analysis


def later_analysis(section_gain: float = 0.0, *, clean: bool = True, fuel_l: float | None = None) -> dict:
    """A later practice: one ten-lap run whose laps 8-10 sit in the trial window."""
    analysis = baseline_analysis()
    analysis["analysis_id"] = "later"
    analysis["identity"] = dict(analysis["identity"], session_start="2026-09-24T18:00:00+00:00", subsession_id=11, event_type="Practice")
    analysis["runs"] = [{"run_number": 1, "start_lap": 1, "end_lap": 10}]
    analysis["laps"] = analysis["laps"][:10]
    analysis["lap_traces"]["traces"] = analysis["lap_traces"]["traces"][:10]
    for lap in analysis["laps"]:
        lap["clean_context"]["traffic_proximity_fraction"] = 0.0 if clean else 0.3
        if fuel_l is not None:
            lap["fuel"] = {"start_l": fuel_l}
    for lap, trace in zip(analysis["laps"], analysis["lap_traces"]["traces"]):
        # Every lap starts as slow as the baseline's late laps; each 30%-lap
        # section then gains `section_gain` seconds.
        pace = lap["lap_time_s"] + 1.5 - section_gain / 0.3
        lap["lap_time_s"] = pace
        for point in trace["points"]:
            point["session_time_s"] = trace["lap"] * 50 + point["lap_pct"] * pace
    analysis["race_investigation"] = build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))
    return analysis


class DrivingTrialTests(unittest.TestCase):
    def trial(self) -> dict:
        analysis = baseline_analysis()
        comparison = next(item for item in analysis["race_investigation"]["comparisons"] if item["id"] == "run-2-clean")
        action = next(item for item in comparison["actions"] if item.get("trial"))
        return driving_trials.create_trial(analysis, analysis_path="a", comparison_id=comparison["id"],
                                           action_priority=action["priority"], created_at="2026-09-21T00:00:00Z")

    def test_trial_fixes_bounds_rule_baseline_pass_and_identity(self) -> None:
        trial = self.trial()
        self.assertRegex(trial["trial_id"], r"^trial-[0-9a-f]{20}$")
        self.assertEqual(trial["trial_id"], self.trial()["trial_id"])
        self.assertEqual(trial["source"]["configuration_key"], "7-oval")
        self.assertEqual(trial["source"]["setup_fingerprint"], "abc")
        self.assertEqual(trial["baseline"]["laps"], [18, 19, 20])
        self.assertIn(trial["baseline_pass"]["lap"], (18, 19, 20))
        self.assertTrue(trial["baseline_pass"]["points"])
        self.assertEqual(trial["success"]["minimum_laps"], 3)
        self.assertEqual(trial["evaluations"], [])
        json.dumps(trial, allow_nan=False)

    def test_a_comparison_with_excluded_laps_offers_no_saveable_test(self) -> None:
        analysis = investigation_fixture()
        analysis["race_investigation"] = build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))
        finish = next(item for item in analysis["race_investigation"]["comparisons"] if item["id"] == "run-2-finish")
        self.assertEqual(finish["eligibility"], "mixed")
        self.assertTrue(finish["actions"])
        self.assertTrue(all(action["trial"] is None for action in finish["actions"]))
        with self.assertRaisesRegex(driving_trials.TrialError, "no measurable section"):
            driving_trials.create_trial(analysis, analysis_path=None, comparison_id="run-2-finish", action_priority=1)

    def test_trial_requires_a_current_investigation_and_a_measurable_action(self) -> None:
        analysis = baseline_analysis()
        analysis["race_investigation"]["schema_version"] = 1
        with self.assertRaisesRegex(driving_trials.TrialError, "predates"):
            driving_trials.create_trial(analysis, analysis_path=None, comparison_id="run-2-clean", action_priority=1)
        analysis = baseline_analysis()
        with self.assertRaisesRegex(driving_trials.TrialError, "not in this recording"):
            driving_trials.create_trial(analysis, analysis_path=None, comparison_id="missing", action_priority=1)

    def test_not_comparable_is_distinct_from_not_established(self) -> None:
        trial = self.trial()
        own = driving_trials.evaluate_trial(trial, baseline_analysis())
        self.assertEqual(own["verdict"], "not_comparable")
        self.assertIn("recording the test came from", own["reasons"][0])
        other_car = later_analysis(); other_car["identity"]["car_path"] = "other"
        self.assertIn("same car", driving_trials.evaluate_trial(trial, other_car)["reasons"][0])
        other_track = later_analysis(); other_track["identity"]["track_config"] = "Road"
        self.assertIn("same track configuration", driving_trials.evaluate_trial(trial, other_track)["reasons"][0])
        earlier = later_analysis(); earlier["identity"]["session_start"] = "2026-09-19T00:00:00+00:00"
        self.assertIn("before", driving_trials.evaluate_trial(trial, earlier)["reasons"][0])
        short = later_analysis(); short["runs"] = [{"run_number": 1, "start_lap": 1, "end_lap": 5}]
        short["laps"] = short["laps"][:5]
        result = driving_trials.evaluate_trial(trial, short)
        self.assertEqual(result["verdict"], "not_comparable")
        self.assertIn("green laps into a run", result["reasons"][0])

    def test_improved_worse_and_within_threshold_verdicts(self) -> None:
        trial = self.trial()
        threshold = trial["success"]["threshold_s"]
        improved = driving_trials.evaluate_trial(trial, later_analysis(section_gain=threshold + 0.05))
        self.assertEqual(improved["verdict"], "improved", improved["reasons"])
        self.assertGreaterEqual(improved["count"], 3)
        self.assertEqual(improved["pair"]["a"]["role"], "before")
        self.assertEqual(improved["pair"]["b"]["role"], "after")
        self.assertTrue(improved["pair"]["rows"])
        unchanged = driving_trials.evaluate_trial(trial, later_analysis(section_gain=0.0))
        self.assertEqual(unchanged["verdict"], "not_established")
        self.assertNotIn("unchanged", json.dumps(unchanged).lower())
        worse = driving_trials.evaluate_trial(trial, later_analysis(section_gain=-threshold - 0.05))
        self.assertEqual(worse["verdict"], "worse", worse["reasons"])

    def test_too_few_screened_laps_is_not_established_and_confounds_stay_visible(self) -> None:
        trial = self.trial()
        analysis = later_analysis(section_gain=1.0, fuel_l=80.0)
        for lap in analysis["laps"]:
            if lap["lap"] in (8, 9, 10):
                lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        analysis["race_investigation"] = build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))
        result = driving_trials.evaluate_trial(trial, analysis)
        self.assertEqual(result["verdict"], "not_established")
        self.assertIn("are needed", result["reasons"][0])
        self.assertTrue(any("Starting fuel" in item for item in result["confounds"]))

    def test_workflows_persist_every_check_and_replace_only_a_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            reports.mkdir()
            base_path = reports / "base-analysis.json"
            base_path.write_text(json.dumps(baseline_analysis()), encoding="utf-8")
            comparison = next(item for item in baseline_analysis()["race_investigation"]["comparisons"] if item["id"] == "run-2-clean")
            priority = next(item for item in comparison["actions"] if item.get("trial"))["priority"]
            saved = driving_trials.save_driving_trial_workflow(analysis_path=str(base_path), comparison_id="run-2-clean",
                                                               action_priority=priority, archive_root=str(root))
            self.assertTrue(saved["created"])
            trial_id = saved["trial"]["trial_id"]
            again = driving_trials.save_driving_trial_workflow(analysis_path=str(base_path), comparison_id="run-2-clean",
                                                               action_priority=priority, archive_root=str(root))
            self.assertFalse(again["created"])
            for name, gain in (("one", 0.0), ("two", 2.0)):
                path = reports / f"{name}.json"
                later = later_analysis(section_gain=gain); later["analysis_id"] = name
                path.write_text(json.dumps(later), encoding="utf-8")
                driving_trials.evaluate_driving_trial_workflow(trial_id=trial_id, analysis_path=str(path), archive_root=str(root))
            driving_trials.evaluate_driving_trial_workflow(trial_id=trial_id, analysis_path=str(reports / "one.json"), archive_root=str(root))
            loaded = driving_trials.load_driving_trial_workflow(trial_id=trial_id, archive_root=str(root))
            self.assertEqual([item["analysis_id"] for item in loaded["evaluations"]], ["two", "one"])
            listed = driving_trials.list_driving_trials_workflow(archive_root=str(root))
            self.assertEqual(listed["trials"][0]["evaluation_count"], 2)
            self.assertNotIn("pair", listed["trials"][0]["latest_evaluation"])
            self.assertNotIn("baseline_pass", listed["trials"][0])
            with self.assertRaises(driving_trials.TrialError):
                driving_trials.load_trial(root, "../escape")
            (root / "trials" / "trial-00000000000000000000.json").write_text("{", encoding="utf-8")
            self.assertEqual(driving_trials.list_driving_trials_workflow(archive_root=str(root))["unreadable_count"], 1)

    def test_evaluation_never_mutates_the_trial_or_the_analysis(self) -> None:
        trial = self.trial()
        analysis = later_analysis(section_gain=1.0)
        before_trial, before_analysis = copy.deepcopy(trial), copy.deepcopy(analysis)
        driving_trials.evaluate_trial(trial, analysis)
        self.assertEqual(trial, before_trial)
        self.assertEqual(analysis, before_analysis)


if __name__ == "__main__":
    unittest.main()
