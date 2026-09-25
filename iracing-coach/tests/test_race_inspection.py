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
from race_foundations import track_configuration_key  # noqa: E402
from race_inspection import (  # noqa: E402
    MAX_SECTIONS, SECTION_CATALOG, comparison_inspection, comparison_sections, configuration_key, review_sections,
    run_views, section_elapsed,
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

    def test_every_shipped_catalog_record_validates_and_loads_whole(self) -> None:
        catalog = json.loads(SECTION_CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], 1)
        keys = [record["track_configuration_key"] for record in catalog["records"]]
        self.assertEqual(len(keys), len(set(keys)), "one record per exact configuration")
        self.assertIn("232-irp", keys)
        for record in catalog["records"]:
            key = record["track_configuration_key"]
            with self.subTest(key=key):
                self.assertTrue(record["version"].startswith(f"{key}/"))
                self.assertTrue(record["provenance"].strip())
                sections = record["sections"]
                self.assertTrue(0 < len(sections) <= MAX_SECTIONS)
                self.assertEqual(len({s["id"] for s in sections}), len(sections))
                for section in sections:
                    self.assertTrue(section["label"].strip() and section["provenance"].strip() and section["boundary_status"].strip())
                    self.assertTrue(0 <= section["start_pct"] < section["end_pct"] <= 1, section)
                ordered = sorted(sections, key=lambda s: s["start_pct"])
                self.assertEqual(ordered, sections, "records list sections in lap order")
                for left, right in zip(ordered, ordered[1:]):
                    self.assertLessEqual(left["end_pct"], right["start_pct"], f"{left['id']} overlaps {right['id']}")
                # Nothing is silently dropped when the record is served.
                served = review_sections({"track_geometry": {"track_configuration_key": key}})
                self.assertEqual((served["source"], served["version"]), ("configuration_catalog", record["version"]))
                self.assertEqual([(s["id"], s["start_pct"], s["end_pct"]) for s in served["sections"]],
                                 [(s["id"], s["start_pct"], s["end_pct"]) for s in sections])
                self.assertIsNone(served["unavailable_reason"])

    def test_irp_loads_by_track_id_and_short_name_configuration(self) -> None:
        # IRP recordings carry TrackID 232 and no TrackConfigName; the engine
        # falls back to TrackDisplayShortName "IRP" for the configuration.
        identity = {"track_id": 232, "track_config": "IRP"}
        self.assertEqual(track_configuration_key(identity), "232-irp")
        from_identity = review_sections({"identity": identity})
        from_geometry = review_sections({"track_geometry": {"track_configuration_key": "232-irp"}, "identity": identity})
        for result in (from_identity, from_geometry):
            self.assertEqual(result["configuration_key"], "232-irp")
            self.assertEqual(result["source"], "configuration_catalog")
            self.assertEqual(result["version"], "232-irp/2026-09-24.1")
        # A detected region in this recording never replaces the catalog bounds.
        detected = review_sections({"track_geometry": {"track_configuration_key": "232-irp"},
                                    "track_profile": {"detected_corner_segments": [{"start_pct": 0.235, "end_pct": 0.245}]}})
        self.assertEqual(len(detected["sections"]), 7)
        # Another configuration of the same track is not IRP's oval.
        self.assertEqual(review_sections({"identity": {"track_id": 232, "track_config": "Road Course"}})["source"], "detected_in_recording")

    def test_irp_sections_tile_the_lap_in_named_review_sections(self) -> None:
        result = review_sections({"track_geometry": {"track_configuration_key": "232-irp"}})
        sections = result["sections"]
        self.assertEqual([(s["id"], s["name"], s["start_pct"], s["end_pct"]) for s in sections], [
            ("A", "Frontstretch, from the line", 0.005, 0.045),
            ("B", "Turns 1-2 entry", 0.045, 0.25),
            ("C", "Turns 1-2 exit", 0.25, 0.455),
            ("D", "Backstretch", 0.455, 0.535),
            ("E", "Turns 3-4 entry", 0.535, 0.75),
            ("F", "Turns 3-4 exit", 0.75, 0.955),
            ("G", "Frontstretch, to the line", 0.955, 0.995),
        ])
        # Contiguous: no gap and no overlap between neighbours.
        for left, right in zip(sections, sections[1:]):
            self.assertEqual(left["end_pct"], right["start_pct"])
        covered = sum(s["end_pct"] - s["start_pct"] for s in sections)
        self.assertAlmostEqual(covered, 0.99, places=9)
        for section in sections:
            self.assertEqual(section["label"], section["id"], "the short label stays a single letter for map markers")
            self.assertEqual(section["boundary_status"], "geometry_derived_section")
            self.assertTrue(section["provenance"].startswith(section["name"] + ":"))
            self.assertIn("not an official turn boundary", section["provenance"])
        self.assertIn("not an official turn boundary", result["provenance"])

    def test_irp_bounds_are_measurable_on_distance_binned_traces(self) -> None:
        # Traces carry bin centres, (i + 0.5) / bins. A section may not touch 0
        # or 1, which is why the catalog leaves 0.5% either side of the line.
        sections = review_sections({"track_geometry": {"track_configuration_key": "232-irp"}})["sections"]
        for bins in (100, 160, 400):
            lap_trace = {"lap": 1, "points": [{"lap_pct": (i + 0.5) / bins, "session_time_s": 22.5 * (i + 0.5) / bins}
                                              for i in range(bins)]}
            times = [section_elapsed(lap_trace, s["start_pct"], s["end_pct"]) for s in sections]
            self.assertTrue(all(t is not None for t in times), (bins, times))
            self.assertAlmostEqual(sum(times), 22.5 * 0.99, places=6)
            self.assertIsNone(section_elapsed(lap_trace, 0.0, 0.045))

    def test_named_sections_carry_their_name_into_comparison_text(self) -> None:
        named = [{"id": "B", "label": "B", "name": "Turns 1-2 entry", "start_pct": 0.4, "end_pct": 0.6, "boundary_status": "geometry_derived_section"}]
        traces = {1: trace(1), 2: trace(2), 3: trace(3, loss_inside=0.2), 4: trace(4, loss_inside=0.2)}
        corner = comparison_sections([1, 2], [3, 4], traces, named)[0]
        self.assertEqual(corner["label"], "Section B · Turns 1-2 entry (40.0–60.0% lap)")
        self.assertEqual((corner["short_label"], corner["name"]), ("B", "Turns 1-2 entry"))
        unnamed = comparison_sections([1, 2], [3, 4], traces, [{k: v for k, v in named[0].items() if k != "name"}])[0]
        self.assertEqual(unnamed["label"], "Section B (40.0–60.0% lap)")
        self.assertNotIn("name", unnamed)


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
        # A 20% section gets the maximum 8% lead-in, inside the 0.3-0.4 straight.
        self.assertEqual(pair["context_start_pct"], 0.32)
        self.assertTrue(any(abs(row["x"] - 0.4) < 1e-9 for row in pair["rows"]))
        self.assertAlmostEqual(pair["entry_gap_s"], 0.3 * (0.4 - 0.32) / 0.4, places=3)
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
        traces[4]["points"] = [p for p in traces[4]["points"] if not 0.33 < p["lap_pct"] < 0.39]
        traces[3] = copy.deepcopy(traces[4]); traces[3]["lap"] = 3
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, self.sections())["sections"]["B"]
        self.assertFalse(pair["timing_complete"])
        self.assertIsNone(pair["entry_gap_s"])
        self.assertTrue(all(row["gap"] is None for row in pair["rows"]))
        self.assertTrue(any(row["b"]["speed"] is not None for row in pair["rows"]))

    def test_approach_is_a_bounded_lead_in_not_the_straight_before_it(self) -> None:
        # IRP's detected sections once had E at 30.5-40% and F at 64.5-72.5%;
        # F was timed from E's start, so "Entry" covered 34% of the lap and F
        # filled only the right fifth of every chart.
        sections = [{"id": "E", "label": "E", "start_pct": 0.305, "end_pct": 0.40, "boundary_status": "detected_section"},
                    {"id": "F", "label": "F", "start_pct": 0.645, "end_pct": 0.725, "boundary_status": "detected_section"}]
        traces = {lap: trace(lap, loss_before=0.3 if lap > 2 else 0.0, loss_inside=0.2 if lap > 2 else 0.0,
                             start=0.645, end=0.725) for lap in (1, 2, 3, 4)}
        exposure = {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, sections)["sections"]["F"]
        self.assertAlmostEqual(pair["context_start_pct"], 0.565)
        self.assertEqual(pair["context_label"], "Lead-in: 8.0% lap before the section")
        # The section fills at least half of the charted range.
        self.assertGreaterEqual((pair["end_pct"] - pair["start_pct"]) / (pair["end_pct"] - pair["context_start_pct"]), 0.5 - 1e-9)
        self.assertEqual(pair["rows"][0]["x"], 0.565)
        self.assertEqual(pair["rows"][-1]["x"], 0.725)
        # Only the loss accrued inside the lead-in counts as entry time (1% bins
        # blur the boundary by half a bin); from E's start it was about 0.158 s.
        self.assertAlmostEqual(pair["entry_gap_s"], 0.3 * 0.08 / 0.645, delta=0.01)
        # Tiled catalog sections (IRP 232-irp/2026-09-24.1): F's approach is the
        # last 8% of E, not all of E.
        tiled = review_sections({"track_geometry": {"track_configuration_key": "232-irp"}})["sections"]
        traces = {lap: trace(lap, loss_inside=0.2 if lap > 2 else 0.0, start=0.75, end=0.955) for lap in (1, 2, 3, 4)}
        pairs = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, traces, tiled)["sections"]
        self.assertAlmostEqual(pairs["F"]["context_start_pct"], 0.67)
        self.assertEqual(pairs["F"]["context_label"], "Lead-in: 8.0% lap before the section")
        # A short previous section bounds the lead-in and names it.
        short = [{"id": "A", "label": "A", "start_pct": 0.35, "end_pct": 0.37, "boundary_status": "detected_section"},
                 {"id": "B", "label": "B", "start_pct": 0.4, "end_pct": 0.6, "boundary_status": "detected_section"}]
        pair = comparison_inspection(self.comparison(), {}, exposure, {}, CLEAN, self.traces(), short)["sections"]["B"]
        self.assertEqual((pair["context_start_pct"], pair["context_label"]), (0.35, "From section A"))

    def test_bounded_approach_sets_the_trial_metric_start(self) -> None:
        # A "carry more speed" trial times approach and section together; the
        # approach it saves is the bounded lead-in, not the previous section.
        sections = [{"id": "A", "label": "A", "start_pct": 0.1, "end_pct": 0.2, "boundary_status": "detected_section"},
                    {"id": "B", "label": "B", "start_pct": 0.5, "end_pct": 0.7, "boundary_status": "detected_section"}]
        traces = {lap: trace(lap, loss_before=0.4 if lap > 2 else 0.0, loss_inside=0.2 if lap > 2 else 0.0,
                             speed=94.0 if lap > 2 else 100.0, start=0.5, end=0.7) for lap in (1, 2, 3, 4)}
        comparison = {"baseline": {"lap_numbers": [1, 2], "run_number": 1}, "compared": {"lap_numbers": [3, 4], "run_number": 1},
                      "eligibility": "eligible", "pace_comparable": True, "summary": "", "corners": comparison_sections([1, 2], [3, 4], traces, sections)}
        comparison["inspection"] = comparison_inspection(comparison, {}, {1: 20.0, 2: 21.0, 3: 21.5, 4: 22.0}, {}, CLEAN, traces, sections)
        from race_investigation import _actions
        action = next(item for item in _actions(comparison, {"sections": sections}, traces) if item["section_id"] == "B")
        self.assertEqual(action["title"], "Carry more speed into section B")
        self.assertIn("measured from 42.0% lap", action["instruction"])
        self.assertEqual((action["trial"]["metric"]["key"], action["trial"]["metric"]["start_pct"]), ("approach_and_section_s", 0.42))

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
        # Traffic-affected finish laps can be inspected but never become a test baseline.
        self.assertIsNone(action["trial"])
        analysis = investigation_fixture()
        for lap in analysis["laps"]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.0
        analysis["damage_repair"]["incident_points"]["events"] = []
        comparison = next(item for item in self.build(analysis)["comparisons"] if item["id"] == "run-2-clean")
        self.assertEqual(comparison["eligibility"], "eligible")
        trial = comparison["actions"][0]["trial"]
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

    def test_cached_investigation_adopts_a_catalog_record_added_after_caching(self) -> None:
        # An IRP analysis cached before the IRP record existed holds this
        # recording's detected regions under a current projection version.
        analysis = investigation_fixture()
        analysis["race_investigation"] = self.build(analysis)
        self.assertEqual(analysis["race_investigation"]["sections"]["source"], "detected_in_recording")
        analysis["track_geometry"] = {"track_configuration_key": "232-irp"}
        self.assertTrue(refresh_race_investigation(analysis))
        sections = analysis["race_investigation"]["sections"]
        self.assertEqual((sections["source"], sections["version"]), ("configuration_catalog", "232-irp/2026-09-24.1"))
        self.assertFalse(refresh_race_investigation(analysis))
        # Named sections reach the comparison text the driver reads.
        comparison = next(item for item in analysis["race_investigation"]["comparisons"] if item["id"] == "run-2-finish")
        self.assertTrue(comparison["corners"])
        self.assertTrue(all(" · " in corner["label"] and corner["name"] for corner in comparison["corners"]))
        named = [action for action in comparison["actions"] if action.get("section_id")]
        self.assertTrue(named)
        for action in named:
            corner = next(item for item in comparison["corners"] if item["id"] == action["section_id"])
            self.assertIn(f"({corner['name']})", action["title"] + action["instruction"])
        # A re-versioned record is stale too.
        analysis["race_investigation"]["sections"]["version"] = "232-irp/2026-09-01.1"
        self.assertTrue(refresh_race_investigation(analysis))
        json.dumps(analysis["race_investigation"], allow_nan=False)


if __name__ == "__main__":
    unittest.main()
