from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_engine import analyze_telemetry  # noqa: E402
from reporting import render_report, render_visuals  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402


class ReportingTests(unittest.TestCase):
    def test_report_separates_post_run_service_from_race_pit_call(self) -> None:
        telemetry = synthetic_telemetry()
        analysis = analyze_telemetry(telemetry)
        final_run = analysis["runs"][-1]
        # Synthetic telemetry has a mid-race stop but no post-run service.
        # Model a final reading to verify the report's decision language.
        final_run["ended_with_pit_stop"] = False
        final_run["ended_with_post_run_service"] = True
        analysis["strategy"] = __import__("analysis_engine")._strategy(
            analysis["runs"], analysis["race_summary"]
        )
        report = render_report(analysis)
        self.assertIn("post-run service reading, not a race pit call", report)

    def test_visuals_are_valid_svg_documents(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        visuals = render_visuals(analysis)
        self.assertTrue(visuals)
        for payload in visuals.values():
            self.assertTrue(payload.lstrip().startswith("<svg"))
            self.assertTrue(payload.rstrip().endswith("</svg>"))

    def test_open_report_exposes_setup_telemetry_without_claiming_cause(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        analysis["identity"]["is_fixed_setup"] = False
        report = render_report(analysis)
        self.assertIn("## Open-setup telemetry evidence", report)
        self.assertIn("Center-front splitter", report)
        self.assertIn("do not uniquely prove", report)

    def test_report_renders_damage_repair_context_and_nonadditive_clock_warning(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        analysis["damage_repair"] = {
            "status": "recorded",
            "summary": {
                "tow_episodes": 1,
                "recorded_repair_episodes": 1,
                "repair_required_flag_episodes": 1,
                "confirmed_fast_repair_uses": 0,
            },
            "incident_points": {"positive_delta": 4},
            "episodes": [
                {
                    "start_session_time_s": 615.0,
                    "candidate_lap_numbers": [18],
                    "classification": "tow_and_recorded_repair",
                    "timing": {
                        "pit_road_time_s": 140.0,
                        "pit_stall_time_s": 80.0,
                        "pitstop_service_active_time_s": 55.0,
                        "tow_active_time_s": 40.0,
                    },
                    "tow": {
                        "active_time_s": 40.0,
                        "peak_remaining_s": 39.9,
                        "last_remaining_s": 0.0,
                        "completion_status": "completed_in_recording",
                    },
                    "mandatory_repair": {
                        "status": "recorded_positive_timer",
                        "peak_remaining_s": 23.2,
                        "remaining_at_stall_exit_s": 0.0,
                        "repair_work_completed_s": 23.2,
                        "completion_status": "completed_in_recording",
                    },
                    "optional_repair": {
                        "status": "recorded_positive_timer",
                        "peak_remaining_s": 306.7,
                        "remaining_at_stall_exit_s": 250.0,
                        "repair_work_completed_s": 56.7,
                        "completion_status": "remaining_at_exit",
                    },
                    "run_context": {
                        "preceding_run_number": 1,
                        "following_run_number": 2,
                        "overlapping_run_numbers": [],
                    },
                    "pit_service_status": {"observed": []},
                    "fast_repair": {
                        "requested": False,
                        "request_confirmed_as_use": False,
                    },
                }
            ],
        }

        report = render_report(analysis)

        self.assertIn("## Damage, tow, and repair context", report)
        self.assertIn("tow and recorded repair", report)
        self.assertIn("56.7 s countdown consumed", report)
        self.assertIn("can overlap and are not additive", report)
        self.assertIn("does not isolate repair-only time loss", report)
        self.assertNotIn("unavailable", report.lower())


if __name__ == "__main__":
    unittest.main()
