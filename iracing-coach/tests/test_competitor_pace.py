"""Cases for observed competitor pace and gaps.

`COMPETITOR-PACE-001`.

Cars are synthesized at exact lap times, so the correct pace is known before
the module runs. The adversarial cases cover the ways a field measurement can
mislead: counting a pit or caution lap as green pace, reporting a car that has
barely run, and getting the sign of a gap wrong across the start/finish line -
which would turn a car a tenth behind into one nearly a lap ahead.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import competitor_pace as cp  # noqa: E402

HZ = 10.0


def field(lap_times, *, duration_s: float = 600.0, offsets=None):
    """Build CarIdxLapDistPct rows for cars circulating at fixed lap times."""

    samples = int(duration_s * HZ)
    times = [index / HZ for index in range(samples)]
    starts = offsets or [0.0] * len(lap_times)
    rows = []
    for index in range(samples):
        clock = times[index]
        row = []
        for car, lap_time in enumerate(lap_times):
            if lap_time is None:
                row.append(-1.0)
                continue
            row.append(((clock / lap_time) + starts[car]) % 1.0)
        rows.append(row)
    return rows, times


class PaceTests(unittest.TestCase):
    def test_measured_pace_matches_the_synthesized_lap_time(self) -> None:
        rows, times = field([100.0, 105.0, 95.0])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        self.assertEqual(report.status, cp.STATUS_USABLE)
        by_index = {car.car_index: car for car in report.cars}
        self.assertAlmostEqual(by_index[0].median_lap_s, 100.0, delta=0.3)
        self.assertAlmostEqual(by_index[1].median_lap_s, 105.0, delta=0.3)
        self.assertAlmostEqual(by_index[2].median_lap_s, 95.0, delta=0.3)

    def test_relative_pace_gets_the_direction_right(self) -> None:
        rows, times = field([100.0, 105.0, 95.0])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        by_index = {item.car_index: item for item in report.relative}
        self.assertFalse(by_index[1].faster_than_player)
        self.assertTrue(by_index[2].faster_than_player)
        self.assertAlmostEqual(by_index[1].lap_delta_s, 5.0, delta=0.3)
        self.assertAlmostEqual(by_index[2].lap_delta_s, -5.0, delta=0.3)

    def test_player_pace_is_reported(self) -> None:
        rows, times = field([100.0, 105.0])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        self.assertAlmostEqual(report.player_median_lap_s, 100.0, delta=0.3)

    def test_a_car_with_too_few_laps_is_not_given_a_pace(self) -> None:
        # Long enough that the first two cars clear the three-lap floor and
        # the third, at four times the lap time, cannot.
        rows, times = field([100.0, 100.0, 400.0], duration_s=800.0)
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        by_index = {car.car_index: car for car in report.cars}
        self.assertEqual(by_index[2].status, cp.STATUS_UNAVAILABLE)
        self.assertEqual(by_index[2].reason, "insufficient_green_laps")
        self.assertIsNone(by_index[2].median_lap_s)
        self.assertEqual(report.status, cp.STATUS_LIMITED)

    def test_an_absent_car_is_not_counted(self) -> None:
        rows, times = field([100.0, 100.0, None])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        by_index = {car.car_index: car for car in report.cars}
        self.assertEqual(by_index[2].completed_laps, 0)
        self.assertEqual(by_index[2].status, cp.STATUS_UNAVAILABLE)


class OutlierTests(unittest.TestCase):
    def test_a_slow_lap_does_not_drag_the_pace_estimate(self) -> None:
        """A pit or caution lap is not this car's green pace."""

        rows, times = field([100.0, 100.0, 100.0], duration_s=1200.0)
        # Freeze car 1 for two minutes mid-race, as a stop or a tow would.
        freeze_from, freeze_to = int(300 * HZ), int(420 * HZ)
        held = rows[freeze_from][1]
        for index in range(freeze_from, freeze_to):
            rows[index][1] = held
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        by_index = {car.car_index: car for car in report.cars}
        self.assertAlmostEqual(
            by_index[1].median_lap_s,
            100.0,
            delta=1.0,
            msg="the stopped lap must be excluded from green pace",
        )
        self.assertLess(by_index[1].counted_laps, by_index[1].completed_laps)


class GapTests(unittest.TestCase):
    def test_a_car_just_behind_is_not_reported_as_nearly_a_lap_ahead(self) -> None:
        """The wrap case: 0.98 of a lap ahead is 0.02 of a lap behind."""

        rows, times = field([100.0, 100.0], offsets=[0.0, -0.02])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        gap = report.relative[0]
        self.assertLess(gap.final_gap_laps, 0.0)
        self.assertGreater(gap.final_gap_laps, -0.1)
        self.assertAlmostEqual(gap.final_gap_s, -2.0, delta=0.5)

    def test_a_car_just_ahead_has_a_positive_gap(self) -> None:
        rows, times = field([100.0, 100.0], offsets=[0.0, 0.03])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        )
        gap = report.relative[0]
        self.assertGreater(gap.final_gap_laps, 0.0)
        self.assertAlmostEqual(gap.final_gap_s, 3.0, delta=0.5)


class RefusalTests(unittest.TestCase):
    def test_no_recorded_positions_is_unavailable(self) -> None:
        report = cp.analyze_field(
            lap_dist_pct_by_car=[], session_time_s=[], player_car_index=0
        )
        self.assertEqual(report.status, cp.STATUS_UNAVAILABLE)
        self.assertEqual(report.reason, "no_recorded_competitor_positions")

    def test_scalar_rows_are_refused_not_misread(self) -> None:
        report = cp.analyze_field(
            lap_dist_pct_by_car=[0.1, 0.2, 0.3],
            session_time_s=[0.0, 1.0, 2.0],
            player_car_index=0,
        )
        self.assertEqual(report.status, cp.STATUS_UNAVAILABLE)
        self.assertEqual(report.reason, "competitor_position_rows_are_not_arrays")

    def test_an_unknown_player_still_yields_field_pace(self) -> None:
        rows, times = field([100.0, 105.0])
        report = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=99
        )
        self.assertEqual(report.status, cp.STATUS_LIMITED)
        self.assertEqual(report.reason, "player_pace_unavailable")
        self.assertEqual(report.relative, ())
        self.assertIsNotNone(report.field_median_lap_s)


class BoundaryTests(unittest.TestCase):
    def test_payload_states_the_privacy_boundary(self) -> None:
        rows, times = field([100.0, 105.0])
        payload = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        ).to_payload()
        json.dumps(payload)
        excluded = " ".join(payload["excluded_by_boundary"])
        self.assertIn("fuel, tires, setup and damage", excluded)
        self.assertIn("intent", excluded)

    def test_no_competitor_field_beyond_position_is_emitted(self) -> None:
        rows, times = field([100.0, 105.0])
        payload = cp.analyze_field(
            lap_dist_pct_by_car=rows, session_time_s=times, player_car_index=0
        ).to_payload()
        allowed = {
            "car_index",
            "completed_laps",
            "counted_laps",
            "median_lap_s",
            "best_lap_s",
            "status",
            "reason",
        }
        for car in payload["cars"]:
            self.assertEqual(set(car) - allowed, set())


if __name__ == "__main__":
    unittest.main()
