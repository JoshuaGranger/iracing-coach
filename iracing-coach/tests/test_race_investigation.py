from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Callable, Sequence

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analysis_engine import TelemetryTable, _track_usage_context, investigation_lap_exclusions  # noqa: E402
from race_investigation import TEST_DAY_RUN_LAPS, build_race_investigation, trial_reachability  # noqa: E402


def investigation_fixture() -> dict:
    laps, traces = [], []
    for number in range(1, 21):
        age = (number - 1) % 10 + 1
        late = number >= 18
        lap_time = 30.0 + age * 0.02 + (1.5 if late else 0.0)
        laps.append({
            "lap": number, "complete": True, "flag_state": "green", "green_fraction": 1.0,
            "pit_time_s": 0.0, "racing_state_fraction": 1.0, "lap_time_s": lap_time,
            "start_index": number * 100, "end_index": number * 100 + 99,
            "position": {"end": 4 if late else 1},
            "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.3 if late else 0.0},
            "conditions": {"track_temperature_f": 80.0, "track_usage": "moderately high usage", "track_usage_percent": None},
            "controls": {"brake_mean": 0.2 if late else 0.1, "throttle_mean": 0.65 if late else 0.85,
                         "steering_abs_mean_rad": 0.3 if late else 0.2, "steering_corrections": 3 if late else 1},
            "fuel": {"start_l": 40.0 - age},
            "vehicle_dynamics": {"brake_bias_setting": {"median": -1.0 if number >= 19 else 0.0}},
        })
        traces.append({"lap": number, "points": [
            {"lap_pct": index / 40, "session_time_s": number * 50 + index / 40 * lap_time,
             "speed_mph": 95.0 if late else 100.0, "brake": 0.2 if late else 0.1,
             "throttle": 0.65 if late else 0.85, "steering_rad": 0.3 if late else 0.2}
            for index in range(41)
        ]})
    return {
        "laps": laps, "lap_traces": {"traces": traces},
        "runs": [
            {"run_number": 1, "start_lap": 1, "end_lap": 10,
             "pit_service": {"tires_changed_observed": ["LF", "RF", "LR", "RR"]},
             "tire_observation": {"lowest_remaining_percent": 75.0}},
            {"run_number": 2, "start_lap": 11, "end_lap": 20},
        ],
        "track_profile": {"detected_corner_segments": [{"start_pct": 0.1, "end_pct": 0.4}, {"start_pct": 0.6, "end_pct": 0.9}]},
        "damage_repair": {"status": "available", "summary": {"recorded_repair_episodes": 0},
                          "incident_points": {"events": [{"candidate_lap": 18, "points_added": 4}]}},
        "race_timeline": {"events": [{"event_type": "pit_service", "start_lap": 10, "end_lap": 10,
                                      "confirmed_tires": ["LF", "RF", "LR", "RR"], "confirmed_fuel_added_l": 4.0}]},
        "track_usage_context": {"dynamic_track_enabled": True, "session_states": [
            {"session_type": "Practice", "state": "moderately high usage"},
            {"session_type": "Qualify", "state": "carry over"},
            {"session_type": "Race", "state": "carry over"},
        ]},
    }


class RaceInvestigationTests(unittest.TestCase):
    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    def test_observed_finish_retains_fade_laps_and_exact_exclusion_reasons(self) -> None:
        result = self.build(investigation_fixture())
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [11, 12, 13])
        self.assertEqual(comparison["compared"]["lap_numbers"], [18, 19, 20])
        self.assertEqual(comparison["delta_s"], 1.64)
        self.assertEqual(comparison["eligibility"], "mixed")
        self.assertEqual(comparison["compared"]["eligible_lap_numbers"], [])
        self.assertIn("incident_points", comparison["compared"]["excluded_laps"][0]["reasons"])
        self.assertIn("close_traffic", comparison["compared"]["excluded_laps"][0]["reasons"])
        self.assertEqual(len(result["laps"]), 20)
        self.assertTrue(result["laps"][-1]["plot_pace"])

    def test_corner_loss_generates_specific_conditional_trial_and_exact_inspection_laps(self) -> None:
        result = self.build(investigation_fixture())
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertGreater(comparison["corners"][0]["delta_s"], 0.4)
        action = comparison["actions"][0]
        self.assertEqual(action["title"], "Test a smoother brake release")
        self.assertEqual(action["lap_numbers"], [11, 12, 13, 18, 19, 20])
        self.assertIn(action["start_pct"], (0.1, 0.6))
        self.assertIn("brake", action["signal_keys"])
        self.assertIn("excluded laps", action["limitation"])
        self.assertIn("minimum speed", action["instruction"])

    def test_static_rubber_inheritance_is_not_repeated_as_measured_evolution(self) -> None:
        result = self.build(investigation_fixture())
        track = result["track_evolution"]
        self.assertEqual(track["status"], "starting_state_only")
        self.assertIsNone(track["usage_source"])
        self.assertTrue(all(point["usage_value"] is None for point in track["points"]))
        self.assertIn("Race: carry over", track["provenance"])
        self.assertIn("Dynamic track was enabled", track["provenance"])
        self.assertIn("not measured", track["summary"])
        hypothesis = next(item for item in result["comparisons"][-1]["hypotheses"] if item["key"] == "track")
        self.assertEqual(hypothesis["status"], "unresolved")
        self.assertTrue(hypothesis["references"])
        self.assertIn("not proof", hypothesis["general_context"])

    def test_numeric_usage_uses_actual_per_lap_values_and_preserves_missing_points(self) -> None:
        analysis = investigation_fixture()
        for index, lap in enumerate(analysis["laps"]):
            lap["conditions"]["track_usage_percent"] = None if index == 5 else 25.0 + index
        track = self.build(analysis)["track_evolution"]
        self.assertEqual(track["status"], "measured_series")
        self.assertEqual(track["usage_source"], "TrackUsage")
        self.assertEqual(track["points"][0]["usage_value"], 25.0)
        self.assertIsNone(track["points"][5]["usage_value"])
        self.assertEqual(track["points"][-1]["usage_value"], 44.0)

    def test_cross_run_match_requires_supported_exposure_and_discloses_uncontrolled_factors(self) -> None:
        analysis = investigation_fixture()
        result = self.build(analysis)
        comparison = next(item for item in result["comparisons"] if item["kind"] == "cross_run")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [1, 2, 3, 4, 5])
        self.assertEqual(comparison["compared"]["lap_numbers"], [11, 12, 13, 14, 15])
        self.assertIn("not controlled", comparison["summary"])
        analysis["laps"][10]["complete"] = False
        result = self.build(analysis)
        self.assertFalse(any(item["kind"] == "cross_run" for item in result["comparisons"]))

    def test_missing_trace_channels_do_not_become_zero_or_control_faults(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"]:
            lap["controls"] = {}
        for trace in analysis["lap_traces"]["traces"]:
            for point in trace["points"]:
                for key in ("brake", "throttle", "steering_rad", "session_time_s"):
                    point.pop(key, None)
        result = self.build(analysis)
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertFalse(any(metric["key"] in {"brake", "throttle", "steering"} for metric in comparison["metrics"]))
        self.assertTrue(all(corner["delta_s"] is None for corner in comparison["corners"]))
        self.assertEqual(comparison["actions"][0]["title"], "Locate the repeatable pace loss")

    def test_metadata_incidents_and_absent_repairs_do_not_prove_damage(self) -> None:
        result = self.build(investigation_fixture())
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        damage = next(item for item in comparison["hypotheses"] if item["key"] == "damage")
        self.assertEqual(damage["status"], "unresolved")
        self.assertTrue(any("does not establish an undamaged car" in text for text in damage["unknown"]))
        events = result["timeline"]
        self.assertTrue(any(event["kind"] == "incident" and event["lap"] == 18 for event in events))
        self.assertTrue(any(event["kind"] == "brake_bias" and event["lap"] == 19 for event in events))
        self.assertTrue(any(event["kind"] == "pit_service" and "confirmed tires" in event["detail"] for event in events))

    def test_scored_checkered_transition_remains_observed_but_not_clean(self) -> None:
        analysis = investigation_fixture()
        analysis["laps"][-1].update({"racing_state_fraction": 0.51, "flag_states": ["green", "white", "checkered"], "lap_time_source": "sdk_scored"})
        result = self.build(analysis)
        last = result["laps"][-1]
        self.assertTrue(last["plot_pace"])
        self.assertFalse(last["eligible"])
        self.assertIn("not_racing_state", last["exclusion_reasons"])
        final = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertIn(20, final["compared"]["lap_numbers"])
        analysis["laps"][-1]["racing_state_fraction"] = 0.0
        self.assertFalse(self.build(analysis)["laps"][-1]["plot_pace"])

    def test_warmup_pace_is_not_plotted_but_recorded_lap_remains_in_timeline(self) -> None:
        analysis = investigation_fixture()
        analysis["laps"][0]["racing_state_fraction"] = 0.5
        analysis["laps"][0]["lap_time_s"] = 90.0
        result = self.build(analysis)
        self.assertEqual(result["laps"][0]["lap_time_s"], 90.0)
        self.assertFalse(result["laps"][0]["plot_pace"])
        self.assertFalse(result["laps"][0]["eligible"])

    def test_no_comparisons_remains_explicit_and_never_fabricates_samples(self) -> None:
        result = self.build({"laps": [], "runs": []})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["comparisons"], [])
        self.assertEqual(result["laps"], [])
        self.assertEqual(result["track_evolution"]["status"], "unavailable")

    def test_null_optional_trace_points_and_segments_withhold_area_metrics(self) -> None:
        for missing in ("points", "segments"):
            analysis = investigation_fixture()
            if missing == "points":
                for trace in analysis["lap_traces"]["traces"]:
                    trace["points"] = None
            else:
                analysis["track_profile"]["detected_corner_segments"] = None
            result = self.build(analysis)
            self.assertTrue(result["comparisons"])
            self.assertTrue(all(not item["corners"] for item in result["comparisons"]))

    def test_missing_one_window_traffic_or_all_controls_does_not_rule_out_explanations(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"]:
            lap["controls"] = None
            lap["vehicle_dynamics"] = None
            lap["clean_context"] = {"on_track_fraction": 1.0}
            if lap["lap"] > 10:
                lap["clean_context"]["traffic_proximity_fraction"] = 0.0
        result = self.build(analysis)
        cross = next(item for item in result["comparisons"] if item["kind"] == "cross_run")
        traffic = next(item for item in cross["hypotheses"] if item["key"] == "traffic")
        driving = next(item for item in cross["hypotheses"] if item["key"] == "driving")
        self.assertEqual(traffic["status"], "unresolved")
        self.assertFalse(traffic["contradicting"])
        self.assertEqual(driving["status"], "unresolved")
        self.assertFalse(driving["contradicting"])
        self.assertIn("not available", driving["unknown"][0])

    def test_unknown_numeric_flags_do_not_get_treated_as_green_or_checkered(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"]:
            lap.update(flag_state=999, flag_states=999, lap_time_source="sdk_scored")
        result = self.build(analysis)
        self.assertFalse(result["comparisons"])
        self.assertEqual(len(result["laps"]), 20)
        self.assertFalse(any(lap["plot_pace"] for lap in result["laps"]))

    def test_damage_and_incident_final_laps_remain_visible_without_becoming_references(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"][-3:]:
            lap["damage_repair_context"] = {"automatic_coaching_reference_eligible": False, "exclusion_reason_codes": ["recorded_repair_disruption"]}
        result = self.build(analysis)
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertEqual(comparison["compared"]["lap_numbers"], [18, 19, 20])
        self.assertFalse(comparison["compared"]["eligible_lap_numbers"])
        self.assertTrue(all("recorded_repair_disruption" in lap["reasons"] for lap in comparison["compared"]["excluded_laps"]))
        self.assertTrue(all(lap["plot_pace"] for lap in result["laps"][-3:]))

    def test_large_race_bounds_comparisons_preserving_finish_and_largest_change(self) -> None:
        seed = investigation_fixture()
        analysis = {"laps": [], "runs": [], "track_profile": None, "lap_traces": None}
        for index in range(1000):
            lap = copy.deepcopy(seed["laps"][index % 10])
            lap["lap"] = index + 1
            if index + 1 in (504, 505):
                lap["lap_time_s"] += 50
            analysis["laps"].append(lap)
        analysis["runs"] = [{"run_number": index + 1, "start_lap": index * 5 + 1, "end_lap": index * 5 + 5} for index in range(200)]
        result = self.build(analysis)
        self.assertLessEqual(len(result["comparisons"]), 24)
        self.assertGreater(result["comparison_coverage"]["candidate_count"], 300)
        self.assertEqual(result["comparison_coverage"]["generated_count"], len(result["comparisons"]))
        default = next(item for item in result["comparisons"] if item["id"] == result["default_comparison_id"])
        self.assertIn(1000, default["compared"]["lap_numbers"])
        self.assertTrue(any(item["delta_s"] > 40 and 505 in item["compared"]["lap_numbers"] for item in result["comparisons"]))
        self.assertEqual(len(result["laps"]), 1000)
        self.assertTrue(any("Showing 24 of" in value for value in result["limitations"]))
        self.assertLess(len(json.dumps(result)), 600_000)

    def test_output_is_deterministic_finite_and_does_not_mutate_inputs(self) -> None:
        analysis = investigation_fixture()
        original = copy.deepcopy(analysis)
        first = self.build(analysis)
        self.assertEqual(first, self.build(analysis))
        self.assertEqual(analysis, original)
        json.dumps(first, allow_nan=False)

    def test_session_metadata_preserves_carry_over_chain(self) -> None:
        sessions = [{"SessionNum": 0, "SessionType": "Practice", "SessionTrackRubberState": "high usage"},
                    {"SessionNum": 1, "SessionType": "Race", "SessionTrackRubberState": "carry over"}]
        table = TelemetryTable({"channels": {"SessionTime": [0, 1]}, "session_info": {
            "WeekendInfo": {"TrackDynamicTrack": 1}, "SessionInfo": {"Sessions": sessions}}})
        context = _track_usage_context(table, sessions[1])
        self.assertEqual([item["state"] for item in context["session_states"]], ["high usage", "carry over"])
        self.assertTrue(context["dynamic_track_enabled"])
        self.assertFalse(context["recorded_usage_channel"])


def caution_run_fixture(cautions: set[int], lap_count: int, traffic: float) -> dict:
    """One run with the given caution laps; every other lap is a complete green lap."""
    laps = []
    for number in range(1, lap_count + 1):
        caution = number in cautions
        laps.append({
            "lap": number, "complete": True, "flag_state": "caution" if caution else "green",
            "green_fraction": 0.0 if caution else 1.0, "caution_fraction": 1.0 if caution else 0.0,
            "pit_time_s": 0.0, "racing_state_fraction": 1.0,
            "lap_time_s": 80.0 if caution else 31.4 + number * 0.01,
            "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": traffic},
        })
    return {"laps": laps, "runs": [{"run_number": 1, "start_lap": 1, "end_lap": lap_count}]}


class UnusablePaceComparisonTests(unittest.TestCase):
    """Kansas 9/21: "Laps 30–42 were 3.121 s/lap slower" compared laps 30, 38 and
    42 across two cautions, with all six selected laps excluded."""

    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    def test_window_spanning_cautions_with_all_laps_excluded_has_no_pace_headline(self) -> None:
        # Green 1-5, caution 6-8, green 9-10, caution 11-17, green 18,
        # caution 19-21, green 22; pack traffic excludes every lap.
        cautions = {6, 7, 8, *range(11, 18), 19, 20, 21}
        result = self.build(caution_run_fixture(cautions, 22, traffic=0.3))
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-1-finish")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [1, 2, 3])
        self.assertEqual(comparison["compared"]["lap_numbers"], [10, 18, 22])
        self.assertEqual(comparison["eligibility"], "observed")
        self.assertIsNone(comparison["delta_s"])
        self.assertFalse(comparison["pace_comparable"])
        self.assertEqual(comparison["pace_unusable_reasons"], ["spans_caution", "separated_by_caution", "all_laps_excluded"])
        self.assertEqual(
            comparison["summary"],
            "Not a usable pace comparison: laps 10, 18 and 22 span caution laps 11–17 and 19–21; "
            "caution laps 6–8 separate laps 1–3 from laps 10, 18 and 22; "
            "all 6 selected laps are excluded from clean coaching.",
        )
        self.assertNotIn("median lap", comparison["summary"])
        self.assertEqual(result["default_comparison_id"], comparison["id"])
        self.assertEqual(result["summary"], comparison["summary"])
        # Section losses inherit the same confound and are not turned into corrections.
        self.assertEqual(len(comparison["actions"]), 1)
        self.assertIsNone(comparison["actions"][0]["section_id"])
        self.assertIsNone(comparison["actions"][0]["trial"])
        self.assertEqual(comparison["actions"][0]["evidence"], [comparison["summary"]])
        # The cohort medians remain visible as observations.
        self.assertIsNotNone(comparison["compared"]["median_lap_s"])

    def test_all_excluded_comparison_without_a_caution_states_why(self) -> None:
        result = self.build(caution_run_fixture(set(), 12, traffic=0.3))
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-1-finish")
        self.assertIsNone(comparison["delta_s"])
        self.assertEqual(comparison["pace_unusable_reasons"], ["all_laps_excluded"])
        self.assertEqual(comparison["summary"],
                         "Not a usable pace comparison: all 8 selected laps are excluded from clean coaching.")

    def test_screened_window_spanning_a_caution_is_not_a_pace_result(self) -> None:
        # Lap 3 is the restart after the lap-2 caution, so the clean laps are
        # 1, 4, 5, 6, 7, 8 and the earlier clean window is laps 1 and 4.
        result = self.build(caution_run_fixture({2}, 8, traffic=0.0))
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-1-clean")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [1, 4])
        self.assertEqual(comparison["eligibility"], "eligible")
        self.assertIsNone(comparison["delta_s"])
        self.assertEqual(comparison["pace_unusable_reasons"], ["spans_caution"])
        self.assertEqual(comparison["summary"], "Not a usable pace comparison: laps 1 and 4 span caution lap 2.")

    def test_headline_prefers_the_latest_usable_comparison(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"][10:]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.3
        result = self.build(analysis)
        finish = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertFalse(finish["pace_comparable"])
        self.assertEqual(result["default_comparison_id"], "run-1-clean")
        default = next(item for item in result["comparisons"] if item["id"] == "run-1-clean")
        self.assertTrue(default["pace_comparable"])
        self.assertIsNotNone(default["delta_s"])
        self.assertEqual(result["summary"], default["summary"])
        self.assertIn("median lap", result["summary"])

    def test_usable_comparisons_keep_their_pace_headline(self) -> None:
        result = self.build(investigation_fixture())
        self.assertTrue(all(item["pace_comparable"] for item in result["comparisons"]))
        self.assertTrue(all(item["pace_unusable_reasons"] == [] for item in result["comparisons"]))
        finish = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertEqual(finish["delta_s"], 1.64)
        self.assertIn("s slower than", finish["summary"])


class DefaultComparisonTests(unittest.TestCase):
    """IRP 9/23 opened on "Run 1: earlier to final green laps" (laps 106-110,
    mixed, nothing to save) while "Run 1: clean early versus late" (laps
    105-109, the same +0.730 s/lap, two saveable tests) sat one row above."""

    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    @staticmethod
    def saveable(comparison: dict) -> bool:
        return any(action["trial"] for action in comparison["actions"])

    def test_saveable_clean_comparison_leads_a_later_finish_with_nothing_to_save(self) -> None:
        result = self.build(investigation_fixture())
        comparisons = {item["id"]: item for item in result["comparisons"]}
        clean, finish = comparisons["run-2-clean"], comparisons["run-2-finish"]
        # The IRP shape: both usable, the finish window ends later but keeps
        # excluded laps and offers no test; the screened window offers one.
        self.assertTrue(clean["pace_comparable"] and finish["pace_comparable"])
        self.assertGreater(max(finish["compared"]["lap_numbers"]), max(clean["compared"]["lap_numbers"]))
        self.assertEqual((clean["eligibility"], finish["eligibility"]), ("eligible", "mixed"))
        self.assertTrue(self.saveable(clean))
        self.assertFalse(self.saveable(finish))
        self.assertEqual(result["default_comparison_id"], "run-2-clean")
        self.assertEqual(result["summary"], clean["summary"])

    def test_latest_usable_comparison_leads_when_none_is_saveable(self) -> None:
        analysis = investigation_fixture()
        analysis["lap_traces"] = {"traces": []}
        result = self.build(analysis)
        self.assertFalse(any(self.saveable(item) for item in result["comparisons"]))
        self.assertEqual(result["default_comparison_id"], "run-2-finish")

    def test_a_saveable_test_never_lifts_an_unusable_comparison(self) -> None:
        cautions = {6, 7, 8, *range(11, 18), 19, 20, 21}
        result = self.build(caution_run_fixture(cautions, 22, traffic=0.3))
        self.assertFalse(any(self.saveable(item) for item in result["comparisons"] if not item["pace_comparable"]))


class TireServiceExplanationTests(unittest.TestCase):
    """Kansas 9/21 run 2 (laps 19-42) started on four tires changed at the lap-17
    stop, but its comparison read "Tires and service: Unresolved": only run 2's
    own end-of-run service, which never happened, was consulted."""

    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    @staticmethod
    def tires(result: dict, comparison_id: str) -> dict:
        comparison = next(item for item in result["comparisons"] if item["id"] == comparison_id)
        return next(item for item in comparison["hypotheses"] if item["key"] == "tires")

    def test_a_run_that_started_on_a_confirmed_four_tire_change_is_not_unresolved(self) -> None:
        tires = self.tires(self.build(investigation_fixture()), "run-2-clean")
        self.assertEqual(tires["status"], "possible")
        self.assertEqual(tires["supporting"], ["Run 2 started on four tires fitted at the lap 10 stop (confirmed: LF, RF, LR, RR)."])
        # A known fresh set removes the unknown initial tire age.
        self.assertNotIn("Initial tire age", " ".join(tires["unknown"]))

    def test_a_stop_after_the_windows_is_not_evidence_about_their_tires(self) -> None:
        tires = self.tires(self.build(investigation_fixture()), "run-1-clean")
        self.assertEqual(tires["supporting"], ["Run 1 ended with a lowest measured tire reading of 75.0% remaining."])
        self.assertIn("Initial tire age", " ".join(tires["unknown"]))
        cross = self.tires(self.build(investigation_fixture()), "runs-1-2-age")
        self.assertEqual(cross["supporting"], ["Run 1 ended with a lowest measured tire reading of 75.0% remaining.",
                                               "Run 2 started on four tires fitted at the lap 10 stop (confirmed: LF, RF, LR, RR)."])

    def test_unresolved_remains_when_no_service_or_reading_was_recorded(self) -> None:
        analysis = investigation_fixture()
        for run in analysis["runs"]:
            run.pop("pit_service", None)
            run.pop("tire_observation", None)
        tires = self.tires(self.build(analysis), "run-2-clean")
        self.assertEqual((tires["status"], tires["supporting"]), ("unresolved", []))
        analysis["runs"][0]["pit_service"] = {"tires_changed_observed": []}
        self.assertEqual(self.tires(self.build(analysis), "run-2-clean")["status"], "unresolved")


def multi_run_fixture(caution_runs: Sequence[bool]) -> dict:
    """Eight-lap runs. A caution run has a caution on its second lap and a
    restart on its third, so its earlier clean window (laps 1 and 4) spans the
    caution; its last two laps are 50 s slower than its first."""
    laps, runs, number = [], [], 0
    for run_number, caution in enumerate(caution_runs, 1):
        start = number + 1
        for position in range(1, 9):
            number += 1
            is_caution = caution and position == 2
            laps.append({
                "lap": number, "complete": True, "flag_state": "caution" if is_caution else "green",
                "green_fraction": 0.0 if is_caution else 1.0, "caution_fraction": 1.0 if is_caution else 0.0,
                "pit_time_s": 0.0, "racing_state_fraction": 1.0,
                "lap_time_s": 80.0 if is_caution else 31.4 + position * 0.01 + (50.0 if caution and position >= 7 else 0.0),
                "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0},
            })
        runs.append({"run_number": run_number, "start_lap": start, "end_lap": number})
    return {"laps": laps, "runs": runs}


class GreenRunComparisonTests(unittest.TestCase):
    """A pace comparison claims a change within one green run. Iowa 8/03 run 3
    compared laps 23-24 with 32-33 across caution laps 25-30."""

    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    def test_windows_separated_by_a_caution_have_no_pace_headline(self) -> None:
        # Green 1-10, caution 11-13, green 14-25 once gave "Laps 21–25 were
        # 0.200 s/lap slower than laps 1–5".
        result = self.build(caution_run_fixture({11, 12, 13}, 25, traffic=0.0))
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-1-clean")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [1, 2, 3, 4, 5])
        self.assertEqual(comparison["compared"]["lap_numbers"], [21, 22, 23, 24, 25])
        self.assertEqual(comparison["eligibility"], "eligible")
        self.assertIsNone(comparison["delta_s"])
        self.assertFalse(comparison["pace_comparable"])
        self.assertEqual(comparison["pace_unusable_reasons"], ["separated_by_caution"])
        self.assertEqual(comparison["summary"],
                         "Not a usable pace comparison: caution laps 11–13 separate laps 1–5 from laps 21–25.")
        self.assertEqual(result["default_comparison_id"], comparison["id"])
        self.assertEqual(result["summary"], comparison["summary"])
        self.assertNotIn("median lap", result["summary"])
        self.assertIsNone(comparison["actions"][0]["trial"])
        self.assertEqual(comparison["actions"][0]["title"], "Compare laps from one green run")

    def test_iowa_shaped_run_across_a_caution_has_no_pace_headline_once_laps_are_clean(self) -> None:
        # Iowa 8/03 run 3: laps 23-37, caution laps 25-30 and 34-37, lap 31 the restart.
        fixture = caution_run_fixture({*range(25, 31), *range(34, 38)}, 37, traffic=0.0)
        fixture["laps"] = [lap for lap in fixture["laps"] if lap["lap"] >= 23]
        fixture["runs"] = [{"run_number": 3, "start_lap": 23, "end_lap": 37}]
        result = self.build(fixture)
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-3-clean")
        self.assertEqual(comparison["baseline"]["lap_numbers"], [23, 24])
        self.assertEqual(comparison["compared"]["lap_numbers"], [32, 33])
        self.assertIsNone(comparison["delta_s"])
        self.assertEqual(comparison["pace_unusable_reasons"], ["separated_by_caution"])
        self.assertEqual(comparison["summary"],
                         "Not a usable pace comparison: caution laps 25–30 separate laps 23–24 from laps 32–33.")
        self.assertNotIn("median lap", result["summary"])

    def test_one_green_run_keeps_its_pace_headline(self) -> None:
        # The lap-1 caution precedes both windows, so they share one green run.
        result = self.build(caution_run_fixture({1}, 16, traffic=0.0))
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-1-clean")
        self.assertTrue(comparison["pace_comparable"])
        self.assertIsNotNone(comparison["delta_s"])
        self.assertIn("s slower than", comparison["summary"])

    def test_cross_run_exposure_match_is_not_a_single_green_run_claim(self) -> None:
        # Runs are compared at matched green exposure across the stop by
        # design; a caution between the runs is disclosed, not disqualifying.
        analysis = investigation_fixture()
        analysis["laps"][9].update(flag_state="caution", green_fraction=0.0, caution_fraction=1.0)
        result = self.build(analysis)
        cross = next(item for item in result["comparisons"] if item["kind"] == "cross_run")
        self.assertTrue(cross["pace_comparable"])
        self.assertIn("cautions and conditions are not controlled", cross["summary"])

    def test_unusable_comparisons_carry_no_explanations(self) -> None:
        cautions = {6, 7, 8, *range(11, 18), 19, 20, 21}
        result = self.build(caution_run_fixture(cautions, 22, traffic=0.3))
        unusable = next(item for item in result["comparisons"] if item["id"] == "run-1-finish")
        self.assertFalse(unusable["pace_comparable"])
        self.assertEqual(unusable["hypotheses"], [])
        usable = self.build(investigation_fixture())["comparisons"]
        self.assertTrue(all(item["pace_comparable"] and item["hypotheses"] for item in usable))

    def test_retention_prefers_usable_comparisons_over_larger_unusable_changes(self) -> None:
        analysis = multi_run_fixture([False] * 16 + [True] * 16)
        result = self.build(analysis)
        coverage = result["comparison_coverage"]
        self.assertGreater(coverage["candidate_count"], 24)
        comparisons = result["comparisons"]
        self.assertEqual(len(comparisons), 24)
        # The first and last four candidates are always kept for context; every
        # ranked slot goes to a usable comparison before a larger unusable change.
        ranked = comparisons[4:-4]
        self.assertTrue(all(item["pace_comparable"] for item in ranked), [item["id"] for item in ranked])
        self.assertEqual(result["default_comparison_id"], "run-16-clean")
        self.assertIn("usable", coverage["selection_policy"])


def long_run_fixture(extra_a: Callable[[int], float] = lambda age: 0.0,
                     extra_b: Callable[[int], float] = lambda age: 0.02 * age,
                     lap_count: int = 90) -> dict:
    """One clean green run shaped like IRP 9/23 run 1: every lap screened.

    Sections A (10-40%) and B (60-90%) take extra_a(age) and extra_b(age)
    seconds more than the base 30 s pace, where age is the lap of the run; the
    rest of the lap never changes. The default fades section B by 0.02 s a lap.
    """
    laps, traces = [], []
    for number in range(1, lap_count + 1):
        a, b = extra_a(number), extra_b(number)
        lap_time = 30.0 + a + b
        laps.append({
            "lap": number, "complete": True, "flag_state": "green", "green_fraction": 1.0,
            "pit_time_s": 0.0, "racing_state_fraction": 1.0, "lap_time_s": lap_time,
            "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0},
            "conditions": {"track_temperature_f": 80.0},
            "fuel": {"start_l": 80.0 - number * 0.4},
        })
        def elapsed(pct: float, a: float = a, b: float = b) -> float:
            return 30.0 * pct + a * min(1.0, max(0.0, (pct - 0.1) / 0.3)) + b * min(1.0, max(0.0, (pct - 0.6) / 0.3))
        traces.append({"lap": number, "points": [
            {"lap_pct": index / 40, "session_time_s": number * 50 + elapsed(index / 40),
             "speed_mph": 100.0, "brake": 0.1, "throttle": 0.85, "steering_rad": 0.2}
            for index in range(41)
        ]})
    return {
        "laps": laps, "lap_traces": {"traces": traces},
        "runs": [{"run_number": 1, "start_lap": 1, "end_lap": lap_count}],
        "track_profile": {"detected_corner_segments": [{"start_pct": 0.1, "end_pct": 0.4}, {"start_pct": 0.6, "end_pct": 0.9}]},
        "damage_repair": {"status": "available", "summary": {"recorded_repair_episodes": 0}, "incident_points": {"events": []}},
    }


class TestDayReachabilityTests(unittest.TestCase):
    """IRP 9/23: a test saved from the race needed three clear laps at 77.5-81.5
    green laps into a run, so only an 80-lap run could check it; the 20-lap
    runs of a test day waited on it with no explanation."""

    def build(self, analysis: dict) -> dict:
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    @staticmethod
    def comparison(result: dict, identifier: str) -> dict:
        return next(item for item in result["comparisons"] if item["id"] == identifier)

    def test_reachability_is_the_run_length_the_window_needs(self) -> None:
        late = trial_reachability([77.5, 81.5])
        self.assertEqual(late, {"run_laps_needed": 78, "test_day_run_laps": TEST_DAY_RUN_LAPS, "test_day_reachable": False})
        self.assertEqual(TEST_DAY_RUN_LAPS, 20)
        self.assertEqual(trial_reachability([16.0, 20.0])["run_laps_needed"], 16)
        self.assertEqual(trial_reachability([1.0, 5.0])["run_laps_needed"], 3)
        # Boundaries: laps 18-20 reach a window from 20.0; one from 20.01 needs lap 21.
        self.assertTrue(trial_reachability([20.0, 22.0])["test_day_reachable"])
        self.assertFalse(trial_reachability([20.01, 22.0])["test_day_reachable"])
        self.assertEqual(trial_reachability(None)["run_laps_needed"], 3, "Any run age needs only the laps themselves.")
        self.assertEqual(trial_reachability([30.0, 34.0], minimum_laps=5)["run_laps_needed"], 32)

    def test_every_offered_test_states_how_long_a_run_it_needs(self) -> None:
        result = self.build(long_run_fixture())
        late = self.comparison(result, "run-1-clean")
        self.assertEqual(late["compared"]["lap_numbers"], [86, 87, 88, 89, 90])
        trial = late["actions"][0]["trial"]
        self.assertEqual(trial["exposure_window"], [86.0, 90.0])
        self.assertEqual(trial["reachability"], {"run_laps_needed": 86, "test_day_run_laps": 20, "test_day_reachable": False})

    def test_a_late_run_fade_offers_its_saveable_early_run_version(self) -> None:
        result = self.build(long_run_fixture())
        early = self.comparison(result, "run-1-early")
        self.assertEqual(early["kind"], "early_run")
        self.assertEqual(early["title"], "Run 1: first 20 green laps")
        self.assertEqual(early["baseline"]["lap_numbers"], [1, 2, 3, 4, 5])
        self.assertEqual(early["compared"]["lap_numbers"], [16, 17, 18, 19, 20])
        self.assertEqual(early["eligibility"], "eligible")
        self.assertTrue(early["pace_comparable"])
        early_action = next(item for item in early["actions"] if item["section_id"] == "B")
        self.assertEqual(early_action["trial"]["exposure_window"], [16.0, 20.0])
        self.assertTrue(early_action["trial"]["reachability"]["test_day_reachable"])
        # The early test carries its own loss and threshold, not the late window's.
        self.assertAlmostEqual(early_action["trial"]["baseline"]["median_s"] - early_action["trial"]["reference"]["median_s"], 0.3, places=3)
        self.assertIsNone(early_action["early_run_version"])
        late = self.comparison(result, "run-1-clean")
        self.assertEqual(late["actions"][0]["section_id"], "B")
        self.assertEqual(late["actions"][0]["early_run_version"], {"comparison_id": "run-1-early", "action_priority": early_action["priority"]})
        # The late window stays the investigation's lead finding.
        self.assertEqual(result["default_comparison_id"], "run-1-clean")

    def test_no_early_run_version_when_the_early_laps_show_no_loss(self) -> None:
        # The fade starts after lap 30, so laps 16-20 are no slower than laps 1-5.
        result = self.build(long_run_fixture(extra_b=lambda age: 0.03 * max(0, age - 30)))
        early = self.comparison(result, "run-1-early")
        self.assertFalse(any(action["trial"] for action in early["actions"]), "No test is invented without an early loss.")
        late = self.comparison(result, "run-1-clean")
        self.assertIsNotNone(late["actions"][0]["trial"], "The late window keeps its own test.")
        self.assertIsNone(late["actions"][0]["early_run_version"])

    def test_no_early_run_version_for_a_different_section(self) -> None:
        # Early laps lose time in section A; the late fade is in section B.
        result = self.build(long_run_fixture(extra_a=lambda age: 0.01 * min(age, 20),
                                             extra_b=lambda age: 0.03 * max(0, age - 30)))
        early = self.comparison(result, "run-1-early")
        self.assertEqual([action["section_id"] for action in early["actions"] if action["trial"]], ["A"])
        late = self.comparison(result, "run-1-clean")
        self.assertEqual(late["actions"][0]["section_id"], "B")
        self.assertIsNone(late["actions"][0]["early_run_version"])

    def test_early_runs_change_nothing_when_the_late_window_is_already_reachable(self) -> None:
        # Ten- and twenty-lap runs: every late window is inside a test-day run.
        for analysis in (investigation_fixture(), long_run_fixture(lap_count=20)):
            result = self.build(analysis)
            self.assertFalse(any(item["kind"] == "early_run" for item in result["comparisons"]))
            actions = [action for item in result["comparisons"] for action in item["actions"]]
            self.assertTrue(all(action["early_run_version"] is None for action in actions))
            trials = [action["trial"] for action in actions if action["trial"]]
            self.assertTrue(trials)
            self.assertTrue(all(trial["reachability"]["test_day_reachable"] for trial in trials))
        ids = [item["id"] for item in self.build(investigation_fixture())["comparisons"]]
        self.assertEqual(ids, ["run-1-clean", "run-2-clean", "run-2-finish", "runs-1-2-age"])

    def test_a_retained_late_window_keeps_its_early_run_window(self) -> None:
        # Over 24 candidates: fifteen short runs, then one long faded run.
        analysis = multi_run_fixture([False] * 15)
        long = long_run_fixture()
        offset = len(analysis["laps"])
        for lap, trace in zip(long["laps"], long["lap_traces"]["traces"]):
            lap["lap"] += offset
            trace["lap"] += offset
            for point in trace["points"]:
                point["session_time_s"] += offset * 50
        analysis["laps"] += long["laps"]
        analysis["runs"].append({"run_number": 16, "start_lap": offset + 1, "end_lap": offset + 90})
        analysis["lap_traces"] = long["lap_traces"]
        analysis["track_profile"] = long["track_profile"]
        result = self.build(analysis)
        self.assertGreater(result["comparison_coverage"]["candidate_count"], 24)
        ids = {item["id"] for item in result["comparisons"]}
        self.assertIn("run-16-clean", ids)
        self.assertIn("run-16-early", ids)
        late = self.comparison(result, "run-16-clean")
        self.assertEqual(late["actions"][0]["early_run_version"]["comparison_id"], "run-16-early")


if __name__ == "__main__":
    unittest.main()
