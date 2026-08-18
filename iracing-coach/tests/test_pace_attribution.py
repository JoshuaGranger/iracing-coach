"""End-to-end wiring for pace attribution through `analyze_telemetry`.

`REFERENCE-DELTA-001`, `TIME-LOSS-RANK-001`, `TIRE-ENERGY-001`.

The producers are unit-tested against closed-form fixtures elsewhere. What
these cases prove is that the engine actually reaches them: that lap
eligibility, segment selection, channel discovery and the join into one
priority list all work on a telemetry payload of the shape the engine really
receives, and that a lap genuinely driven slower in one place is ranked there.
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

from analysis_engine import (  # noqa: E402
    ANALYZER_SOURCE_FILES,
    analyze_telemetry,
    analyzer_bundle_sha256,
)
from test_analysis_engine import synthetic_telemetry  # noqa: E402


def delay_within_lap(
    telemetry: dict, *, lap: int, start_pct: float, end_pct: float, seconds: float
) -> dict:
    """Make one lap genuinely slower across one stretch of track.

    Time is inserted inside the window and every later sample is shifted by the
    full amount, which is what a driver actually does to the clock when he
    loses time: the rest of the session happens later, it does not compress.
    """

    channels = telemetry["channels"]
    times = channels["SessionTime"]
    laps = channels["Lap"]
    pcts = channels["LapDistPct"]
    inside = [
        index
        for index in range(len(times))
        if int(laps[index]) == lap and start_pct <= pcts[index] < end_pct
    ]
    if not inside:
        raise AssertionError("no samples matched the requested window")
    first, last = inside[0], inside[-1]
    span = max(1, last - first)
    for index in range(first, len(times)):
        if index <= last:
            times[index] += seconds * (index - first) / span
        else:
            times[index] += seconds
    return telemetry


class WiringTests(unittest.TestCase):
    def test_analysis_carries_pace_attribution(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry(lap_count=8))
        payload = analysis["pace_attribution"]
        self.assertIn(payload["status"], {"usable", "limited"})
        self.assertGreaterEqual(len(payload["eligible_lap_numbers"]), 3)
        self.assertTrue(payload["priorities"])
        self.assertIn("time_loss", payload)
        self.assertIn("tire_energy", payload)

    def test_every_priority_carries_both_independent_measures(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry(lap_count=8))
        for priority in analysis["pace_attribution"]["priorities"]:
            self.assertIsNotNone(priority["recoverable_s"])
            self.assertIsNotNone(priority["tire_energy_share"])
            self.assertIsNotNone(priority["best_lap"])
            self.assertGreaterEqual(priority["lap_count"], 3)

    def test_tire_energy_concentrates_in_the_corners(self) -> None:
        """The load account must find the load, not spread it evenly."""

        analysis = analyze_telemetry(synthetic_telemetry(lap_count=8))
        shares = [
            priority["tire_energy_share"]
            for priority in analysis["pace_attribution"]["priorities"]
        ]
        self.assertGreater(max(shares), 4.0 * min(shares))
        peaks = [
            priority["peak_lateral_g"]
            for priority in analysis["pace_attribution"]["priorities"]
        ]
        self.assertGreater(max(peaks), 1.0)

    def test_a_metronomic_driver_has_nothing_to_recover(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry(lap_count=8))
        self.assertAlmostEqual(
            analysis["pace_attribution"]["total_recoverable_s"], 0.0, places=3
        )


class RankingThroughTheEngineTests(unittest.TestCase):
    """Recoverable time is a *typical* loss, which shapes both cases here.

    Eligible laps in this fixture are 1, 2, 3 and 6. Slowing a single one of
    them cannot move the median and must therefore produce nothing; slowing
    the majority and leaving one clean is the situation a driver is actually
    in when a corner is worth coaching, and that must rank.
    """

    SLOW_LAPS = (1, 2, 6)
    CLEAN_LAP = 3
    WINDOW = (0.60, 0.70)
    LOSS_S = 0.8

    def _telemetry_with_a_habitually_slow_corner(self) -> dict:
        telemetry = synthetic_telemetry(lap_count=8)
        for lap in self.SLOW_LAPS:
            delay_within_lap(
                telemetry,
                lap=lap,
                start_pct=self.WINDOW[0],
                end_pct=self.WINDOW[1],
                seconds=self.LOSS_S,
            )
        return telemetry

    def test_a_habitually_slow_stretch_is_ranked_first(self) -> None:
        payload = analyze_telemetry(
            self._telemetry_with_a_habitually_slow_corner()
        )["pace_attribution"]
        priorities = payload["priorities"]
        self.assertTrue(priorities)
        top = priorities[0]
        self.assertGreater(top["recoverable_s"], 0.5)
        self.assertLessEqual(top["start_pct"], self.WINDOW[1])
        self.assertGreaterEqual(top["end_pct"], self.WINDOW[0])
        self.assertGreater(
            top["recoverable_s"],
            max((item["recoverable_s"] for item in priorities[1:]), default=0.0),
            "the affected stretch must outrank every other",
        )

    def test_the_one_clean_lap_is_credited_as_the_best(self) -> None:
        payload = analyze_telemetry(
            self._telemetry_with_a_habitually_slow_corner()
        )["pace_attribution"]
        self.assertEqual(payload["priorities"][0]["best_lap"], self.CLEAN_LAP)
        self.assertEqual(
            payload["priorities"][0]["near_best_lap_count"],
            1,
            "only the clean lap should sit near the best time",
        )

    def test_a_single_slow_lap_is_not_treated_as_a_habit(self) -> None:
        telemetry = delay_within_lap(
            synthetic_telemetry(lap_count=8),
            lap=self.CLEAN_LAP,
            start_pct=self.WINDOW[0],
            end_pct=self.WINDOW[1],
            seconds=self.LOSS_S,
        )
        payload = analyze_telemetry(telemetry)["pace_attribution"]
        self.assertAlmostEqual(payload["total_recoverable_s"], 0.0, places=3)


class UnavailableTests(unittest.TestCase):
    def test_missing_distance_channel_is_unavailable_with_a_reason(self) -> None:
        telemetry = synthetic_telemetry(lap_count=8)
        del telemetry["channels"]["LapDistPct"]
        payload = analyze_telemetry(telemetry)["pace_attribution"]
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "distance_time_or_lap_channel_missing")
        self.assertEqual(payload["priorities"], [])

    def test_missing_acceleration_leaves_time_loss_intact(self) -> None:
        """Losing the energy account must not cost the driver his ranking."""

        telemetry = synthetic_telemetry(lap_count=8)
        del telemetry["channels"]["LatAccel"]
        del telemetry["channels"]["LongAccel"]
        payload = analyze_telemetry(telemetry)["pace_attribution"]
        self.assertIn(payload["status"], {"usable", "limited"})
        self.assertTrue(payload["priorities"])
        self.assertEqual(payload["tire_energy"]["status"], "unavailable")
        for priority in payload["priorities"]:
            self.assertIsNone(priority["tire_energy_share"])
            self.assertIsNotNone(priority["recoverable_s"])

    def test_limitations_are_always_stated(self) -> None:
        payload = analyze_telemetry(synthetic_telemetry(lap_count=8))["pace_attribution"]
        joined = " ".join(payload["limitations"])
        self.assertIn("ceiling", joined)
        self.assertIn("not a wear measurement", joined)


class BundleIdentityTests(unittest.TestCase):
    def test_new_producers_invalidate_the_analyzer_bundle(self) -> None:
        """A cached analysis must not survive a change to these modules."""

        for name in ("lap_reference.py", "time_loss.py", "tire_energy.py"):
            self.assertIn(name, ANALYZER_SOURCE_FILES)
        self.assertEqual(len(analyzer_bundle_sha256()), 64)

    def test_every_listed_source_file_exists(self) -> None:
        for name in ANALYZER_SOURCE_FILES:
            self.assertTrue((SCRIPTS / name).is_file(), msg=name)


if __name__ == "__main__":
    unittest.main()
