"""End-to-end wiring for pit loss and strategy planning.

`PIT-LOSS-001`, `STRATEGY-SIM-001`.

The shared synthetic fixture's pit visit lasts about a second, which is
correctly below the floor that separates a stop from brushing pit road, so
these cases lengthen it into a realistic service before asserting anything.
That the short visit yields nothing is itself asserted, because a one-second
"stop" silently costing thirty seconds of modelled strategy would be worse
than no strategy at all.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_engine import ANALYZER_SOURCE_FILES, analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402

HZ = 20.0


def lengthen_pit_stop(telemetry: dict, *, seconds: float = 25.0) -> dict:
    """Stretch the fixture's pit visit into a realistic stationary service."""

    channels = telemetry["channels"]
    pit_indices = [
        index for index, value in enumerate(channels["OnPitRoad"]) if bool(value)
    ]
    if not pit_indices:
        raise AssertionError("fixture has no pit road samples")
    insert_at = pit_indices[len(pit_indices) // 2]
    extra = int(seconds * HZ)
    for name, values in list(channels.items()):
        template = values[insert_at]
        channels[name] = values[:insert_at] + [template] * extra + values[insert_at:]
    # The car is stopped in the box for the inserted stretch, and the clock
    # must stay monotonic across the whole session.
    for offset in range(extra):
        channels["Speed"][insert_at + offset] = 0.0
    channels["SessionTime"] = [
        index / HZ for index in range(len(channels["SessionTime"]))
    ]
    return telemetry


class PitLossWiringTests(unittest.TestCase):
    def test_a_realistic_stop_is_measured(self) -> None:
        payload = analyze_telemetry(
            lengthen_pit_stop(synthetic_telemetry(lap_count=8))
        )["strategy_planning"]
        report = payload["pit_loss"]
        self.assertEqual(report["status"], "usable")
        self.assertEqual(report["measured_stop_count"], 1)
        self.assertGreater(report["median_loss_s"], 0.0)
        self.assertGreater(report["median_stationary_s"], 20.0)

    def test_loss_is_less_than_the_raw_pit_road_duration(self) -> None:
        """The track the car had to cover anyway is not part of the cost."""

        payload = analyze_telemetry(
            lengthen_pit_stop(synthetic_telemetry(lap_count=8))
        )["strategy_planning"]
        episode = payload["pit_loss"]["episodes"][0]
        self.assertIsNotNone(episode["green_equivalent_s"])
        self.assertGreater(episode["green_equivalent_s"], 0.0)
        self.assertAlmostEqual(
            episode["loss_s"],
            episode["duration_s"] - episode["green_equivalent_s"],
            places=3,
        )
        self.assertLess(episode["loss_s"], episode["duration_s"])

    def test_a_one_second_pit_brush_is_not_a_stop(self) -> None:
        payload = analyze_telemetry(synthetic_telemetry(lap_count=8))[
            "strategy_planning"
        ]
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["pit_loss"]["measured_stop_count"], 0)
        self.assertIn("pit_loss_s", payload["reason"])


class StrategyWiringTests(unittest.TestCase):
    def _payload(self) -> dict:
        telemetry = lengthen_pit_stop(synthetic_telemetry(lap_count=8))
        telemetry["channels"]["SessionLapsTotal"] = [60] * len(
            telemetry["channels"]["SessionTime"]
        )
        telemetry["session_info"]["SessionInfo"]["Sessions"][0]["SessionLaps"] = "60"
        return analyze_telemetry(telemetry)["strategy_planning"]

    def test_plans_are_ranked_with_a_stated_margin(self) -> None:
        comparison = self._payload()["plan_comparison"]
        self.assertIn(comparison["status"], {"usable", "limited"})
        self.assertIsNotNone(comparison["best_stop_count"])
        self.assertTrue(comparison["strategies"])
        feasible = [item for item in comparison["strategies"] if item["feasible"]]
        self.assertTrue(feasible)
        self.assertEqual(feasible[0]["stop_count"], comparison["best_stop_count"])

    def test_every_feasible_plan_covers_the_whole_race(self) -> None:
        comparison = self._payload()["plan_comparison"]
        for item in comparison["strategies"]:
            if item["feasible"]:
                self.assertEqual(sum(item["stint_laps"]), 60)

    def test_the_model_states_what_it_excludes(self) -> None:
        payload = self._payload()
        excluded = " ".join(payload["plan_comparison"]["excluded_from_model"])
        self.assertIn("not modelled and not simulated", excluded)
        joined = " ".join(payload["limitations"])
        self.assertIn("not a finishing-position", joined)

    def test_producers_are_in_the_analyzer_bundle(self) -> None:
        for name in ("pit_loss.py", "strategy_model.py"):
            self.assertIn(name, ANALYZER_SOURCE_FILES)
            self.assertTrue((SCRIPTS / name).is_file())


if __name__ == "__main__":
    unittest.main()
