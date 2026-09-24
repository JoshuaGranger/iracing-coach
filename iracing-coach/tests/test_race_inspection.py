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

from analysis_engine import investigation_lap_exclusions  # noqa: E402
from race_inspection import (  # noqa: E402
    comparison_inspection, comparison_sections, configuration_key, review_sections, run_views,
)
from race_investigation import PROJECTION_VERSION, SCHEMA_VERSION, build_race_investigation  # noqa: E402
from test_race_investigation import investigation_fixture  # noqa: E402
from workflow import refresh_race_investigation  # noqa: E402


def trace(lap: int, *, loss_before: float = 0.0, loss_inside: float = 0.0, speed: float = 100.0, slip: float | None = None,
          start: float = 0.4, end: float = 0.6) -> dict:
    """A 1% distance-binned trace. Loss accrues linearly before and inside [start, end]."""
    points = []
    for index in range(101):
        x = index / 100
        extra = loss_before * min(x, start) / start + loss_inside * max(0.0, min(x, end) - start) / (end - start)
        point = {"lap_pct": x, "session_time_s": lap * 100 + x * 20 + extra, "speed_mph": speed,
                 "throttle": 0.5, "brake_mean": 0.0, "brake": 0.0, "steering_rad": 0.2}
        if slip is not None:
            point["slip_angle_deg"] = slip
        points.append(point)
    return {"lap": lap, "points": points}


class ReviewSectionTests(unittest.TestCase):
    def catalog(self, directory: str, records: list) -> Path:
        path = Path(directory) / "catalog.json"
        path.write_text(json.dumps({"schema_version": 1, "records": records}), encoding="utf-8")
        return path

    def test_exact_configuration_record_fixes_bounds_and_provenance(self) -> None:
        analysis = {"track_geometry": {"track_configuration_key": "7-oval"},
                    "track_profile": {"detected_corner_segments": [{"start_pct": 0.1, "end_pct": 0.2}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = self.catalog(directory, [{"track_configuration_key": "7-oval", "version": "7-oval/1", "provenance": "driver",
                                              "sections": [{"id": "E", "start_pct": 0.775, "end_pct": 0.86, "boundary_status": "driver_confirmed_grouping"}]}])
            result = review_sections(analysis, catalog_path=path)
        self.assertEqual(result["source"], "configuration_catalog")
        self.assertEqual(result["version"], "7-oval/1")
        self.assertEqual([(s["id"], s["start_pct"], s["end_pct"], s["boundary_status"]) for s in result["sections"]],
                         [("E", 0.775, 0.86, "driver_confirmed_grouping")])

    def test_detected_regions_get_neutral_letters_and_a_bounds_version(self) -> None:
        analysis = {"identity": {"track_id": 9, "track_config": "Road Course"},
                    "track_profile": {"detected_corner_segments": [
                        {"start_pct": 0.6, "end_pct": 0.7}, {"start_pct": 0.1, "end_pct": 0.2},
                        {"start_pct": 0.95, "end_pct": 0.05, "wraps_start_finish": True}, {"start_pct": 0.3, "end_pct": 0.3}]}}
        with tempfile.TemporaryDirectory() as directory:
            result = review_sections(analysis, catalog_path=self.catalog(directory, []))
        self.assertEqual(configuration_key(analysis), "9-road-course")
        self.assertEqual(result["source"], "detected_in_recording")
        self.assertEqual([(s["label"], s["start_pct"]) for s in result["sections"]], [("A", 0.1), ("B", 0.6)])
        self.assertTrue(result["version"].startswith("detected/"))
        self.assertTrue(all("not a verified turn" in s["provenance"] for s in result["sections"]))

    def test_overlapping_catalog_bounds_are_rejected_not_repaired(self) -> None:
        analysis = {"track_geometry": {"track_configuration_key": "7-oval"}}
        with tempfile.TemporaryDirectory() as directory:
            path = self.catalog(directory, [{"track_configuration_key": "7-oval", "sections": [
                {"id": "A", "start_pct": 0.1, "end_pct": 0.3}, {"id": "B", "start_pct": 0.25, "end_pct": 0.4}]}])
            result = review_sections(analysis, catalog_path=path)
        self.assertEqual(result["sections"], [])
        self.assertIn("overlap", result["unavailable_reason"])

    def test_bristol_single_pit_road_merges_former_regions_five_to_seven(self) -> None:
        result = review_sections({"track_geometry": {"track_configuration_key": "365-single-pit-road"}})
        self.assertEqual([s["id"] for s in result["sections"]], ["A", "B", "C", "D", "E"])
        merged = result["sections"][-1]
        self.assertEqual((merged["start_pct"], merged["end_pct"], merged["boundary_status"]), (0.775, 0.86, "driver_confirmed_grouping"))
        self.assertIn("No official Bristol turn boundary", result["provenance"])


class RunViewTests(unittest.TestCase):
    def points(self, laps, run=1, eligible=True, base=20.0):
        return [{"lap": lap, "run_number": run, "eligible": eligible, "green_exposure": float(index + 1),
                 "lap_time_s": base + index * 0.1} for index, lap in enumerate(laps)]

    def test_trend_breaks_at_cautions_and_names_the_gap(self) -> None:
        points = self.points([1, 2, 3]) + [dict(p, lap=p["lap"] + 5, green_exposure=p["green_exposure"] + 4) for p in self.points([1, 2, 3])]
        laps = [{"lap": n, "flag_state": "caution" if n in (4, 5) else "green", "pit_time_s": 0} for n in range(1, 9)]
        view = run_views(points, laps)[0]
        self.assertEqual([[p["lap"] for p in segment] for segment in view["trend"]["segments"]], [[1, 2, 3], [6, 7, 8]])
        self.assertEqual(view["trend"]["gaps"], [{"after_lap": 3, "before_lap": 6, "reason": "caution"}])
        self.assertTrue(view["trend"]["includes_reference_exclusions"])

    def test_short_sequence_stays_dots_and_zero_exposure_is_never_invented(self) -> None:
        view = run_views(self.points([1, 2]))[0]
        self.assertEqual(view["trend"]["segments"], [])
        self.assertIsNone(view["fit"])
        self.assertIn("Fewer than three", view["fit_unavailable_reason"])
        view = run_views(self.points([1, 2, 3]))[0]
        self.assertEqual(view["trend"]["segments"][0][0]["x"], 1.0)

    def test_fit_uses_screened_laps_and_is_confined_to_their_domain(self) -> None:
        points = self.points([1, 2, 3, 4]) + [{"lap": 5, "run_number": 1, "eligible": False, "green_exposure": 5.0, "lap_time_s": 90.0}]
        view = run_views(points)[0]
        self.assertAlmostEqual(view["fit"]["slope_s_per_green_lap"], 0.1)
        self.assertEqual(view["fit"]["domain"], [1.0, 4.0])
        self.assertEqual(view["fit"]["eligible_count"], 4)
        # The observed trend still includes the traffic-affected lap.
        self.assertEqual(view["trend"]["segments"][0][-1]["lap"], 5)


CLEAN = {1: [], 2: [], 3: [], 4: []}


class SectionAndPairTests(unittest.TestCase):
    def traces(self):
        # Laps 1-2 reference, 3-4 slower: 0.3 s lost before the section and 0.2 s inside.
        return {1: trace(1), 2: trace(2), 3: trace(3, loss_before=0.3, loss_inside=0.2, speed=94.0),
                4: trace(4, loss_before=0.3, loss_inside=0.2, speed=94.0)}

    def sections(self):
        return [{"id": "A", "label": "A", "start_pct": 0.1, "end_pct": 0.3, "boundary_status": "detected_section"},
                {"id": "B", "label": "B", "start_pct": 0.4, "end_pct": 0.6, "boundary_status": "detected_section"}]

    def comparison(self, baseline=(1, 2), compared=(3, 4)):
        return {"baseline": {"lap_numbers": list(baseline)}, "compared": {"lap_numbers": list(compared)}}

    def test_group_medians_recompute_the_full_interval_and_reject_gaps(self) -> None:
        traces = self.traces()
        result = {item["id"]: item for item in comparison_sections([1, 2], [3, 4], traces, self.sections())}
        self.assertAlmostEqual(result["B"]["delta_s"], 0.2, places=3)
        self.assertEqual(result["B"]["label"], "Section B (40.0–60.0% lap)")
        traces[4]["points"] = [p for p in traces[4]["points"] if not 0.45 < p["lap_pct"] < 0.55]
        result = {item["id"]: item for item in comparison_sections([1, 2], [3, 4], traces, self.sections())}
        self.assertNotIn("B", result)

    def test_matched_pool_compares_actual_extremes_with_entry_boundary_timing(self) -> None:
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        inspection = comparison_inspection(self.comparison(), {}, exposure, {1: 1, 2: 1, 3: 2, 4: 2}, CLEAN, self.traces(), self.sections())
        self.assertEqual(inspection["mode"], "matched_pool_extremes")
        pair = inspection["sections"]["B"]
        self.assertEqual((pair["a"]["role"], pair["b"]["role"]), ("fastest", "slowest"))
        self.assertEqual(pair["context_start_pct"], 0.1)
        self.assertTrue(any(abs(row["x"] - 0.4) < 1e-9 for row in pair["rows"]))
        self.assertAlmostEqual(pair["entry_gap_s"], 0.3 * (0.4 - 0.1) / 0.4, places=3)
        self.assertAlmostEqual(pair["section_delta_s"], 0.2, places=3)
        self.assertEqual(pair["entry_speed_difference_mph"], -6.0)
        self.assertEqual(pair["b"]["run_number"], 2)

    def test_wide_or_excluded_pool_uses_each_cohorts_median_pass(self) -> None:
        exposure = {1: 5.0, 2: 6.0, 3: 40.0, 4: 41.0}
        inspection = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, self.traces(), self.sections())
        self.assertEqual(inspection["mode"], "cohort_median_passes")
        pair = inspection["sections"]["B"]
        self.assertEqual((pair["a"]["role"], pair["b"]["role"]), ("baseline_median", "compared_median"))
        self.assertIn(pair["b"]["lap"], (3, 4))
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        inspection = comparison_inspection(self.comparison(), {}, exposure, {}, {**CLEAN, 3: ["close_traffic"]}, self.traces(), self.sections())
        self.assertEqual(inspection["mode"], "cohort_median_passes")
        pair = inspection["sections"]["B"]
        self.assertEqual(pair["b"]["lap"], 3)
        self.assertFalse(pair["b"]["eligible"])
        self.assertEqual(pair["b"]["exclusion_reasons"], ["close_traffic"])

    def test_missing_approach_withholds_gap_but_keeps_channels(self) -> None:
        traces = self.traces()
        traces[4]["points"] = [p for p in traces[4]["points"] if not 0.2 < p["lap_pct"] < 0.3]
        traces[3] = copy.deepcopy(traces[4]); traces[3]["lap"] = 3
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, self.sections())["sections"]["B"]
        self.assertFalse(pair["timing_complete"])
        self.assertIsNone(pair["entry_gap_s"])
        self.assertTrue(all(row["gap"] is None for row in pair["rows"]))
        self.assertTrue(any(row["b"]["speed"] is not None for row in pair["rows"]))

    def test_body_slip_is_never_inferred_and_sparse_slip_is_not_summarized(self) -> None:
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, self.traces(), self.sections())["sections"]["B"]
        self.assertIsNone(pair["slip"]["a"]["median_abs"])
        self.assertTrue(all(row["a"]["slip"] is None for row in pair["rows"]))
        traces = self.traces()
        traces[1] = trace(1, slip=-3.0)
        traces[2] = trace(2, slip=-3.0)
        traces[3]["points"][50]["slip_angle_deg"] = 5.0
        traces[4]["points"][50]["slip_angle_deg"] = 5.0
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, self.sections())["sections"]["B"]
        self.assertEqual(pair["slip"]["a"]["median_abs"], 3.0)
        self.assertIsNone(pair["slip"]["b"]["median_abs"])
        self.assertLess(pair["slip"]["b"]["coverage"], 0.8)

    def test_reversed_trace_never_supplies_a_pass(self) -> None:
        traces = self.traces()
        traces[4]["points"].reverse()
        traces[3]["points"].reverse()
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        inspection = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, self.sections())
        self.assertTrue(all(pair["b"]["lap"] not in (3, 4) and pair["a"]["lap"] not in (3, 4)
                            for pair in inspection["sections"].values()))


class InvestigationProjectionTests(unittest.TestCase):
    def build(self, analysis):
        return build_race_investigation(analysis, lap_exclusions=investigation_lap_exclusions(analysis))

    def test_v2_projection_adds_exposure_runs_sections_inspection_and_trials(self) -> None:
        result = self.build(investigation_fixture())
        self.assertEqual((result["schema_version"], result["projection_version"]), (SCHEMA_VERSION, PROJECTION_VERSION))
        self.assertEqual(result["laps"][0]["green_exposure"], 1.0)
        self.assertEqual([run["run_number"] for run in result["runs"]], [1, 2])
        self.assertEqual([s["id"] for s in result["sections"]["sections"]], ["A", "B"])
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        self.assertEqual(comparison["corners"][0]["short_label"] in ("A", "B"), True)
        self.assertEqual(comparison["inspection"]["mode"], "cohort_median_passes")
        action = comparison["actions"][0]
        self.assertEqual(action["section_id"], comparison["corners"][0]["id"])
        trial = action["trial"]
        self.assertEqual(trial["baseline"]["laps"], [18, 19, 20])
        self.assertEqual(trial["reference"]["laps"], [11, 12, 13])
        self.assertGreater(trial["baseline"]["median_s"], trial["reference"]["median_s"])
        self.assertGreaterEqual(trial["success"]["threshold_s"], 0.02)
        self.assertEqual(trial["success"]["minimum_laps"], 3)
        self.assertEqual(trial["annotation"]["source"], "detected_in_recording")
        json.dumps(result, allow_nan=False)

    def test_no_measurable_loss_offers_a_discriminating_repeat_not_a_review(self) -> None:
        analysis = investigation_fixture()
        for lap in analysis["laps"]:
            lap["controls"] = {}
        result = self.build(analysis)
        comparison = next(item for item in result["comparisons"] if item["id"] == "run-2-finish")
        titles = [action["title"] for action in comparison["actions"]]
        self.assertFalse(any("Inspect these exact laps" in action["instruction"] for action in comparison["actions"]))
        self.assertTrue(titles)

    def test_cached_legacy_investigation_is_rebuilt_without_decoding(self) -> None:
        analysis = investigation_fixture()
        analysis["race_investigation"] = {"schema_version": 1, "comparisons": []}
        self.assertTrue(refresh_race_investigation(analysis))
        self.assertEqual(analysis["race_investigation"]["schema_version"], SCHEMA_VERSION)
        self.assertFalse(refresh_race_investigation(analysis))
        analysis["race_investigation"]["projection_version"] = "older"
        self.assertTrue(refresh_race_investigation(analysis))


if __name__ == "__main__":
    unittest.main()
