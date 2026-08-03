from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import coach_cli  # noqa: E402


class CoachCLITests(unittest.TestCase):
    def test_inventory_forwards_read_only_scope(self) -> None:
        inventory = mock.Mock(return_value={"read_only": True, "counts": {}})
        fake_workflow = SimpleNamespace(inventory_iracing_data_workflow=inventory)
        stdout = io.StringIO()

        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    ["inventory", "--root", "iracing", "--recent-limit", "7", "--documents-only"]
                )

        self.assertEqual(status, 0)
        inventory.assert_called_once_with(
            root="iracing", recent_limit=7, include_known_roots=False
        )
        self.assertTrue(json.loads(stdout.getvalue())["read_only"])

    def test_dashboard_forwards_companion_snapshot_scope(self) -> None:
        dashboard = mock.Mock(return_value={"contract_version": 1, "races": []})
        fake_workflow = SimpleNamespace(companion_dashboard_workflow=dashboard)
        stdout = io.StringIO()
        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "dashboard",
                        "--root",
                        "iracing",
                        "--archive-root",
                        "archive",
                        "--limit",
                        "12",
                    ]
                )
        self.assertEqual(status, 0)
        dashboard.assert_called_once_with(
            root="iracing", archive_root="archive", limit=12
        )
        self.assertEqual(json.loads(stdout.getvalue())["contract_version"], 1)

    def test_analyze_forwards_the_documented_session_selector(self) -> None:
        analyze = mock.Mock(return_value={"artifacts": {"analysis": "analysis.json"}})
        fake_workflow = SimpleNamespace(analyze_race_workflow=analyze)
        stdout = io.StringIO()

        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "analyze",
                        "--session",
                        "latest",
                        "--iracing-root",
                        "telemetry",
                        "--archive-root",
                        "archive",
                        "--target-hz",
                        "10",
                    ]
                )

        self.assertEqual(status, 0)
        analyze.assert_called_once_with(
            selector="latest",
            iracing_root="telemetry",
            archive_root="archive",
            target_hz=10.0,
        )
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"artifacts": {"analysis": "analysis.json"}},
        )

    def test_telemetry_query_forwards_native_bounded_slice(self) -> None:
        query = mock.Mock(return_value={"ok": True, "mode": "slice"})
        fake_workflow = SimpleNamespace(telemetry_query_workflow=query)
        stdout = io.StringIO()

        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "telemetry-query",
                        "--session",
                        "12345",
                        "--iracing-root",
                        "telemetry",
                        "--archive-root",
                        "archive",
                        "--mode",
                        "slice",
                        "--channels",
                        "Brake,SteeringWheelAngle",
                        "--channel",
                        "SteeringWheelTorque_ST",
                        "--native",
                        "--start-record",
                        "120",
                        "--end-record",
                        "360",
                        "--max-samples",
                        "240",
                    ]
                )

        self.assertEqual(status, 0)
        query.assert_called_once_with(
            selector="12345",
            iracing_root="telemetry",
            archive_root="archive",
            mode="slice",
            channels=["Brake", "SteeringWheelAngle", "SteeringWheelTorque_ST"],
            search=None,
            target_hz=None,
            start_record=120,
            end_record=360,
            max_samples=240,
        )
        self.assertTrue(json.loads(stdout.getvalue())["ok"])

    def test_telemetry_query_rejects_more_than_twelve_channels(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = coach_cli.main(
                [
                    "telemetry-query",
                    "--mode",
                    "profile",
                    "--channels",
                    ",".join(f"Channel{index}" for index in range(13)),
                ]
            )

        self.assertEqual(status, 1)
        self.assertIn("at most 12", json.loads(stderr.getvalue())["message"])

    def test_telemetry_query_parser_rejects_invalid_rate_and_sample_limit(self) -> None:
        parser = coach_cli.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["telemetry-query", "--target-hz", "61"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["telemetry-query", "--max-samples", "2001"])

    def test_telemetry_events_forwards_native_context_filters(self) -> None:
        find_events = mock.Mock(
            return_value={"ok": True, "summary": {"returned_event_count": 2}}
        )
        fake_workflow = SimpleNamespace(native_event_search_workflow=find_events)
        stdout = io.StringIO()

        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "telemetry-events",
                        "--session",
                        "12345",
                        "--iracing-root",
                        "telemetry",
                        "--archive-root",
                        "archive",
                        "--event-types",
                        "brake_onset,brake_release",
                        "--event",
                        "steering_torque_peak",
                        "--selection-mode",
                        "severity",
                        "--start-record",
                        "120",
                        "--end-record",
                        "960",
                        "--max-events",
                        "20",
                        "--lap",
                        "14",
                        "--session-time-start",
                        "300.5",
                        "--session-time-end",
                        "340.5",
                        "--lap-distance-start",
                        "0.9",
                        "--lap-distance-end",
                        "0.1",
                    ]
                )

        self.assertEqual(status, 0)
        find_events.assert_called_once_with(
            selector="12345",
            iracing_root="telemetry",
            archive_root="archive",
            event_types=[
                "brake_onset",
                "brake_release",
                "steering_torque_peak",
            ],
            selection_mode="severity",
            start_record=120,
            end_record=960,
            max_events=20,
            lap=14,
            session_time_start=300.5,
            session_time_end=340.5,
            lap_distance_start=0.9,
            lap_distance_end=0.1,
        )
        self.assertEqual(
            json.loads(stdout.getvalue())["summary"]["returned_event_count"], 2
        )

    def test_telemetry_events_rejects_unknown_event_type(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = coach_cli.main(
                ["telemetry-events", "--event", "definitely_not_an_event"]
            )
        self.assertEqual(status, 1)
        self.assertIn("may contain only", json.loads(stderr.getvalue())["message"])

    def test_configure_auth_uses_only_the_interactive_secure_store(self) -> None:
        stdout = io.StringIO()
        credential = Path("encrypted-garage61.dpapi")

        with mock.patch.object(
            coach_cli.secure_store,
            "configure_interactively",
            return_value=credential,
        ) as configure:
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    ["configure-auth", "--credential-path", str(credential)]
                )

        self.assertEqual(status, 0)
        configure.assert_called_once_with(path=str(credential))
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["configured"])
        self.assertNotIn("token", stdout.getvalue().lower())

    def test_output_redacts_nested_credential_fields(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            coach_cli._emit_json(
                {
                    "ok": True,
                    "token": "do-not-print",
                    "headers": {"Authorization": "Bearer do-not-print"},
                }
            )

        output = stdout.getvalue()
        self.assertNotIn("do-not-print", output)
        self.assertEqual(json.loads(output)["token"], "[REDACTED]")

    def test_cache_status_accepts_inline_context_json(self) -> None:
        context = {
            "season_key": "2026-s3",
            "car_key": "car",
            "track_key": "track",
            "setup_type": "fixed",
            "race_length_key": "100-laps",
        }
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "cache-status",
                        "--archive-root",
                        temporary,
                        "--context",
                        json.dumps(context),
                    ]
                )

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "missing")

    def test_operational_errors_are_json_and_nonzero(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = coach_cli.main(
                ["cache-status", "--analysis", "missing-analysis.json"]
            )

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["error"], "CLIError")
        self.assertIn("does not exist", error["message"])

    def test_setup_package_accepts_new_week_context(self) -> None:
        build = mock.Mock(return_value={"status": "exact-track-baseline"})
        fake_workflow = SimpleNamespace(build_open_setup_package_workflow=build)
        stdout = io.StringIO()
        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "setup-package",
                        "--season",
                        "2026S3",
                        "--car",
                        "O'Reilly Toyota Supra",
                        "--track",
                        "Iowa",
                        "--track-characteristics",
                        '{"shape":"compact moderate-banked oval"}',
                    ]
                )
        self.assertEqual(status, 0)
        build.assert_called_once_with(
            analysis_path=None,
            iracing_root=None,
            archive_root=None,
            season="2026S3",
            car="O'Reilly Toyota Supra",
            track="Iowa",
            track_characteristics={"shape": "compact moderate-banked oval"},
        )

    def test_setup_recommend_forwards_driver_symptoms(self) -> None:
        recommend = mock.Mock(return_value={"status": "planned"})
        fake_workflow = SimpleNamespace(recommend_open_setup_tuning_workflow=recommend)
        stdout = io.StringIO()
        with mock.patch.object(coach_cli, "_workflow_module", return_value=fake_workflow):
            with redirect_stdout(stdout):
                status = coach_cli.main(
                    [
                        "setup-recommend",
                        "--analysis",
                        "analysis.json",
                        "--symptoms",
                        "tight center after lap 12",
                        "--package-id",
                        "pkg-1",
                    ]
                )
        self.assertEqual(status, 0)
        recommend.assert_called_once_with(
            analysis_path="analysis.json",
            symptoms="tight center after lap 12",
            archive_root=None,
            package_id="pkg-1",
            maximum_changes=3,
        )


if __name__ == "__main__":
    unittest.main()
