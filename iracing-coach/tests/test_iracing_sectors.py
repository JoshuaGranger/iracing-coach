from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"))
from iracing_sectors import attach_sector_boundaries, iracing_sectors
from race_inspection import review_sections, section_elapsed
from race_investigation import build_race_investigation


class IRacingSectorTests(unittest.TestCase):
    def test_recorded_sectors_follow_lap_distance_and_cover_whole_lap(self):
        sectors = iracing_sectors({"SplitTimeInfo": {"Sectors": [
            {"SectorNum": 2, "SectorStartPct": 0.7}, {"SectorNum": 0, "SectorStartPct": 0.0},
            {"SectorNum": 1, "SectorStartPct": 0.3}]}})
        self.assertEqual([(s["id"], s["start_pct"], s["end_pct"]) for s in sectors["sections"]],
                         [("S1", 0.0, 0.3), ("S2", 0.3, 0.7), ("S3", 0.7, 1.0)])

    def test_missing_or_invalid_sectors_never_substitute_detected_regions(self):
        for raw in ([], [{"SectorStartPct": 0.1}], [{"SectorStartPct": 0}, {"SectorStartPct": 0}],
                    [{"SectorStartPct": 0}, {"SectorStartPct": float("nan")}], [{"SectorStartPct": True}]):
            definition = iracing_sectors({"SplitTimeInfo": {"Sectors": raw}})
            self.assertEqual(definition["sections"], [])
            self.assertTrue(definition["unavailable_reason"])
        result = review_sections({"track_geometry": {"track_configuration_key": "232-irp"},
                                  "track_profile": {"detected_corner_segments": [{"start_pct": 0.1, "end_pct": 0.4}]}})
        self.assertEqual(result["sections"], [])

    def test_practice_qualifying_and_race_use_same_recorded_sector_boundaries(self):
        from test_race_investigation import investigation_fixture
        for session_type in ("Practice", "Qualify", "Race"):
            with self.subTest(session_type=session_type):
                analysis = investigation_fixture()
                analysis["identity"] = {"session_type": session_type}
                result = build_race_investigation(analysis, lap_exclusions={lap["lap"]: [] for lap in analysis["laps"]})
                self.assertEqual([s["id"] for s in result["sections"]["sections"]], ["S1", "S2"])
                self.assertTrue(result["comparisons"][0]["corners"])

    def test_engine_records_full_sector_crossings_for_practice_qualifying_and_race(self):
        from analysis_engine import analyze_telemetry
        from test_analysis_engine import synthetic_telemetry
        for session_type in ("Practice", "Qualify", "Race"):
            with self.subTest(session_type=session_type):
                telemetry = synthetic_telemetry(lap_count=4)
                telemetry["session_info"]["SessionInfo"]["Sessions"][0]["SessionType"] = session_type
                analysis = analyze_telemetry(telemetry)
                sections = analysis["race_investigation"]["sections"]["sections"]
                self.assertEqual([(s["id"], s["start_pct"], s["end_pct"]) for s in sections],
                                 [("S1", 0.0, 0.333333), ("S2", 0.333333, 0.666667), ("S3", 0.666667, 1.0)])
                complete = next(trace for trace in analysis["lap_traces"]["traces"] if trace["lap"] == 2)
                times = [section_elapsed(complete, s["start_pct"], s["end_pct"]) for s in sections]
                self.assertTrue(all(time is not None for time in times), times)
                self.assertAlmostEqual(sum(times), 4.0, places=6)
                self.assertEqual([p["lap_pct"] for p in complete["sector_boundaries"]], [0, 1])

    def test_raw_crossings_time_full_sectors_without_extrapolating_truncated_laps(self):
        distances = [0.99] + [(i + 0.5) / 100 for i in range(100)] + [0.01]
        times = [-0.3] + [30 * (i + 0.5) / 100 for i in range(100)] + [30.3]
        traces = {"traces": [{"lap": 1, "points": [
            {"lap_pct": distances[i], "session_time_s": times[i]} for i in range(1, 101)]}]}
        attach_sector_boundaries(traces, [{"lap": 1, "start_index": 1, "end_index": 100}], times, distances)
        trace = traces["traces"][0]
        self.assertAlmostEqual(section_elapsed(trace, 0, 0.5), 15)
        self.assertAlmostEqual(section_elapsed(trace, 0.5, 1), 15)
        trace.pop("sector_boundaries")
        attach_sector_boundaries(traces, [{"lap": 1, "start_index": 0, "end_index": 101}], times, distances)
        self.assertIsNone(section_elapsed(trace, 0, 0.5))
        self.assertIsNone(section_elapsed(trace, 0.5, 1))
