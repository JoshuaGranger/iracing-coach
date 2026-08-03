from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
TESTS = PLUGIN_ROOT / "tests"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import analysis_engine  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402


class GrooveAnalysisIntegrationTests(unittest.TestCase):
    def test_analysis_exposes_groove_result_and_passes_detected_load_zones(self) -> None:
        sentinel = {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "integration sentinel",
            "zones": [],
        }
        with mock.patch.object(
            analysis_engine,
            "analyze_groove_evolution",
            return_value=sentinel,
        ) as groove:
            result = analysis_engine.analyze_telemetry(synthetic_telemetry())

        self.assertEqual(result["groove_evolution"], sentinel)
        kwargs = groove.call_args.kwargs
        self.assertIn("load_zones", kwargs)
        self.assertIsInstance(kwargs["load_zones"], (list, tuple))
        self.assertTrue(kwargs["runs"])
        self.assertTrue(kwargs["laps"])
        self.assertEqual(kwargs["track_type"], "oval")
        self.assertIn("Lat", groove.call_args.args[0])
        self.assertIn("Lon", groove.call_args.args[0])
        self.assertFalse(result["data_quality"]["channels"]["groove_path"])


if __name__ == "__main__":
    unittest.main()
