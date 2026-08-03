from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from analysis_engine import analyze_telemetry  # noqa: E402
from reporting import render_report  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402
from test_ibt_reader import build_ibt  # noqa: E402
import workflow  # noqa: E402


class FullTelemetryAnalysisContractTests(unittest.TestCase):
    def test_analysis_identity_changes_with_content_and_profile(self) -> None:
        first = synthetic_telemetry()
        changed = copy.deepcopy(first)
        changed["channels"]["Speed"] = [
            value + 5.0 for value in changed["channels"]["Speed"]
        ]

        first_analysis = analyze_telemetry(first, source_paths=["same.ibt"])
        changed_analysis = analyze_telemetry(changed, source_paths=["same.ibt"])
        profile_analysis = analyze_telemetry(
            first,
            source_paths=["same.ibt"],
            analysis_profile={"target_hz": 10},
        )

        self.assertNotEqual(first_analysis["analysis_id"], changed_analysis["analysis_id"])
        self.assertNotEqual(first_analysis["analysis_id"], profile_analysis["analysis_id"])

    def test_catalog_distinguishes_recorded_loaded_and_analyzed(self) -> None:
        telemetry = synthetic_telemetry()
        telemetry["available_variables"] = [
            *telemetry["variables"],
            {
                "name": "FutureUnknownChannel",
                "type": "float",
                "type_code": 4,
                "count": 1,
                "count_as_time": False,
                "unit": "widgets",
                "byte_size": 4,
            },
        ]
        telemetry["channel_selection"] = {
            "mode": "selected",
            "available_count": len(telemetry["available_variables"]),
            "decoded_count": len(telemetry["channels"]),
        }

        analysis = analyze_telemetry(telemetry)
        source = analysis["source"]

        self.assertIn("FutureUnknownChannel", source["available_channels"])
        self.assertIn("FutureUnknownChannel", source["unloaded_channels"])
        self.assertNotIn("FutureUnknownChannel", source["loaded_channels"])
        self.assertTrue(source["channel_coverage"]["catalog_complete"])
        self.assertLessEqual(
            source["channel_coverage"]["analyzed_count"],
            source["channel_coverage"]["loaded_count"],
        )

    def test_session_seconds_and_steering_limit_are_not_misinterpreted(self) -> None:
        telemetry = synthetic_telemetry()
        telemetry["session_info"]["SessionInfo"]["Sessions"][0][
            "SessionTime"
        ] = "1800.0000 sec"
        telemetry["channels"].pop("SteeringWheelAngle")
        telemetry["channels"]["SteeringWheelAngleMax"] = [
            7.85
        ] * len(telemetry["channels"]["SessionTime"])

        analysis = analyze_telemetry(telemetry)

        self.assertEqual(analysis["race_summary"]["scheduled_minutes"], 30.0)
        self.assertTrue(analysis["laps"])
        self.assertIsNone(analysis["laps"][0]["controls"]["steering_abs_mean_rad"])

    def test_tire_pressure_preserves_live_provenance_and_converts_to_psi(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        pressure = analysis["runs"][0]["tire_observation"]["tires"]["LF"][
            "pressure"
        ]

        self.assertEqual(pressure["kind"], "live")
        self.assertEqual(pressure["source_unit"], "kPa")
        self.assertGreater(pressure["psi"], 15.0)
        self.assertLess(pressure["psi"], 25.0)

    def test_partial_parade_lap_is_excluded_from_green_pace_baseline(self) -> None:
        telemetry = synthetic_telemetry()
        count = len(telemetry["channels"]["SessionTime"])
        telemetry["channels"]["SessionState"] = [
            3 if index < 20 else 4 for index in range(count)
        ]

        analysis = analyze_telemetry(telemetry)

        self.assertLess(analysis["laps"][0]["racing_state_fraction"], 0.98)
        self.assertNotIn(1, [
            lap["lap"]
            for lap in analysis["laps"]
            if lap.get("racing_state_fraction") is not None
            and lap["racing_state_fraction"] >= 0.98
        ])

    def test_full_session_range_marks_return_to_start_adjustment_as_changed(self) -> None:
        telemetry = synthetic_telemetry()
        count = len(telemetry["channels"]["SessionTime"])
        telemetry["channels"]["dpFuelAddKg"] = [0.0] * count
        telemetry["channels"]["dpFuelAddKg"][count // 2] = 25.0

        analysis = analyze_telemetry(telemetry)

        self.assertTrue(analysis["driver_adjustments"]["dpFuelAddKg"]["changed"])

    def test_report_exposes_session_local_tire_set_lifecycle(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())

        report = render_report(analysis)

        self.assertIn("Session-local tire-set lifecycle", report)


class FullTelemetryWorkflowContractTests(unittest.TestCase):
    def test_selected_load_retains_complete_per_source_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "race.ibt"
            build_ibt(source)
            old = time.time() - 10
            os.utime(source, (old, old))

            telemetry = workflow._load_analysis_telemetry([str(source)], 20)
            catalog = telemetry["source_catalogs"][0]

            self.assertEqual(len(catalog["recorded_channel_catalog"]), 8)
            self.assertIn("DriverName", catalog["unloaded_channels"])
            self.assertEqual(telemetry["catalog_summary"]["conflict_count"], 0)
            self.assertEqual(
                len(telemetry["sample_provenance"]), telemetry["sample_count"]
            )

    def test_recent_and_trailing_partial_recordings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "race.ibt"
            build_ibt(source)

            with self.assertRaisesRegex(workflow.WorkflowError, "modified too recently"):
                workflow._load_analysis_telemetry([str(source)], 20)

            old = time.time() - 10
            os.utime(source, (old, old))
            with source.open("ab") as handle:
                handle.write(b"partial-next-record")
            os.utime(source, (old, old))

            with self.assertRaisesRegex(workflow.WorkflowError, "not finalized"):
                workflow._load_analysis_telemetry([str(source)], 20)

    def test_on_demand_catalog_profile_and_bounded_slice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "race.ibt"
            build_ibt(source)
            old = time.time() - 10
            os.utime(source, (old, old))

            catalog = workflow.telemetry_query_workflow(
                selector=str(source), mode="catalog", search="tire"
            )
            profile = workflow.telemetry_query_workflow(
                selector=str(source),
                archive_root=root / "archive",
                mode="profile",
                channels=["Speed", "TireTemps"],
                start_record=1,
                end_record=5,
            )
            cached_profile = workflow.telemetry_query_workflow(
                selector=str(source),
                archive_root=root / "archive",
                mode="profile",
                channels=["Speed", "TireTemps"],
                start_record=1,
                end_record=5,
            )
            sliced = workflow.telemetry_query_workflow(
                selector=str(source),
                archive_root=root / "archive",
                mode="slice",
                channels=["DriverName", "TireTemps"],
                start_record=1,
                end_record=5,
                max_samples=3,
            )

            self.assertEqual(catalog["sources"][0]["matching_channel_count"], 1)
            self.assertEqual(profile["sources"][0]["profile"]["sample_count"], 4)
            self.assertFalse(profile["sources"][0]["cache_hit"])
            self.assertTrue(cached_profile["sources"][0]["cache_hit"])
            self.assertEqual(sliced["sources"][0]["sample_indices"], [1, 2, 3])
            self.assertEqual(
                sliced["sources"][0]["samples"]["DriverName"],
                ["Driver 1", "Driver 2", "Driver 3"],
            )
            self.assertTrue(sliced["truncated"])

    def test_repeated_analysis_reuses_verified_core_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "race.ibt"
            build_ibt(source)
            old = time.time() - 10
            os.utime(source, (old, old))
            archive = root / "archive"

            first = workflow.analyze_race_workflow(
                selector=str(source), archive_root=archive, target_hz=20
            )
            second = workflow.analyze_race_workflow(
                selector=str(source), archive_root=archive, target_hz=20
            )

            self.assertFalse(first["analysis_cache"]["hit"])
            self.assertTrue(second["analysis_cache"]["hit"])
            self.assertEqual(first["analysis_id"], second["analysis_id"])


if __name__ == "__main__":
    unittest.main()
