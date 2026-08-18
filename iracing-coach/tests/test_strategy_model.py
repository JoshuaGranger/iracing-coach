"""Cases for pit-loss measurement and deterministic strategy ranking.

`PIT-LOSS-001`, `STRATEGY-SIM-001`.

The strategy cases are built so the correct answer is derivable by hand: with
no degradation an extra stop can only cost time, with heavy degradation it must
eventually pay, and the break-even between them is a number the test computes
independently of the implementation and then checks the ranking actually flips
around.
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

import lap_reference as lr  # noqa: E402
import pit_loss  # noqa: E402
import strategy_model as sm  # noqa: E402


def green_reference(lap_time: float = 100.0, samples: int = 2000) -> lr.LapTrace:
    distance = [index / samples for index in range(samples + 1)]
    times = [lap_time * pct for pct in distance]
    return lr.build_lap_trace(distance, times, range(samples + 1), lap_number=4, bins=100)


class PitLossTests(unittest.TestCase):
    @staticmethod
    def _stop(
        *,
        entry_pct: float = 0.80,
        exit_pct: float = 0.95,
        pit_duration: float = 40.0,
        stationary: float = 12.0,
        hz: float = 20.0,
    ):
        """One lap ending in a pit visit across the given distance span."""

        times: list[float] = []
        pit: list[bool] = []
        pct: list[float] = []
        speed: list[float] = []
        laps: list[int] = []
        clock = 0.0
        # Approach at racing speed.
        approach = int(entry_pct * 100)
        for step in range(approach):
            times.append(clock)
            clock += 1.0 / hz
            pit.append(False)
            pct.append(step / 100.0)
            speed.append(50.0)
            laps.append(6)
        # On pit road: distance advances from entry to exit over pit_duration.
        steps = int(pit_duration * hz)
        stationary_steps = int(stationary * hz)
        for step in range(steps):
            times.append(clock)
            clock += 1.0 / hz
            pit.append(True)
            fraction = step / max(1, steps - 1)
            pct.append(entry_pct + (exit_pct - entry_pct) * fraction)
            middle = steps // 2
            in_box = middle <= step < middle + stationary_steps
            speed.append(0.0 if in_box else 20.0)
            laps.append(6)
        return dict(
            session_time_s=times,
            on_pit_road=pit,
            lap_dist_pct=pct,
            speed_m_s=speed,
            lap_numbers=laps,
        )

    def test_loss_excludes_the_track_the_car_had_to_cover_anyway(self) -> None:
        # A 15% span of a 100 s lap is 15 s the car would have spent anyway.
        report = pit_loss.measure_pit_loss(
            **self._stop(entry_pct=0.80, exit_pct=0.95, pit_duration=40.0),
            reference=green_reference(100.0),
        )
        self.assertEqual(report.status, pit_loss.STATUS_USABLE)
        self.assertEqual(report.measured_stop_count, 1)
        self.assertAlmostEqual(report.median_loss_s, 25.0, delta=1.0)

    def test_stationary_time_is_reported_separately(self) -> None:
        report = pit_loss.measure_pit_loss(
            **self._stop(pit_duration=40.0, stationary=12.0),
            reference=green_reference(100.0),
        )
        self.assertAlmostEqual(report.median_stationary_s, 12.0, delta=0.5)
        self.assertAlmostEqual(
            report.median_travel_loss_s,
            report.median_loss_s - report.median_stationary_s,
            places=6,
        )

    def test_a_pit_lane_spanning_start_finish_is_stitched(self) -> None:
        report = pit_loss.measure_pit_loss(
            **self._stop(entry_pct=0.90, exit_pct=0.05, pit_duration=40.0),
            reference=green_reference(100.0),
        )
        episode = report.episodes[0]
        self.assertTrue(episode.wrapped_start_finish)
        # 15% of the lap again, split either side of the line.
        self.assertAlmostEqual(episode.green_equivalent_s, 15.0, delta=1.0)
        self.assertAlmostEqual(episode.loss_s, 25.0, delta=1.0)

    def test_without_a_reference_lap_the_loss_is_unavailable_not_the_duration(
        self,
    ) -> None:
        report = pit_loss.measure_pit_loss(**self._stop(), reference=None)
        self.assertEqual(report.status, pit_loss.STATUS_UNAVAILABLE)
        self.assertIsNone(report.median_loss_s)
        self.assertEqual(report.episodes[0].reason, "no_green_reference_lap")
        self.assertGreater(report.episodes[0].duration_s, 0.0)

    def test_a_brush_of_pit_road_is_not_a_stop(self) -> None:
        data = self._stop(pit_duration=1.0, stationary=0.0)
        report = pit_loss.measure_pit_loss(**data, reference=green_reference(100.0))
        self.assertEqual(report.episodes, ())
        self.assertEqual(report.status, pit_loss.STATUS_UNAVAILABLE)


class StrategyRankingTests(unittest.TestCase):
    BASE = dict(
        race_laps=60,
        base_lap_s=100.0,
        pit_loss_s=30.0,
        fuel_capacity_l=60.0,
        green_burn_l_per_lap=2.0,
    )

    def test_without_degradation_fewer_stops_always_wins(self) -> None:
        result = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.0)
        self.assertEqual(result.status, sm.STATUS_USABLE)
        # 60 L less a one-lap reserve at 2 L/lap is a 29 lap stint, so 60 laps
        # cannot be done without stopping: two stops is the fewest feasible.
        self.assertEqual(result.best_stop_count, 2)
        feasible = [item for item in result.strategies if item.feasible]
        self.assertEqual(feasible[0].stop_count, 2)
        for item in feasible[1:]:
            self.assertGreater(item.total_time_s, feasible[0].total_time_s)

    def test_fuel_range_makes_low_stop_plans_infeasible(self) -> None:
        result = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.0)
        by_count = {item.stop_count: item for item in result.strategies}
        self.assertFalse(by_count[0].feasible)
        self.assertFalse(by_count[1].feasible)
        self.assertEqual(by_count[0].infeasible_reason, "stint_exceeds_fuel_range")
        self.assertTrue(by_count[2].feasible)

    def test_heavy_degradation_eventually_pays_for_an_extra_stop(self) -> None:
        light = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.01)
        heavy = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.5)
        self.assertEqual(light.best_stop_count, 2)
        self.assertGreater(
            heavy.best_stop_count,
            light.best_stop_count,
            "fresh tires must become worth the stop at some degradation",
        )

    def test_the_degradation_break_even_actually_flips_the_ranking(self) -> None:
        """Solve for the threshold, then check reality either side of it."""

        result = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.01)
        thresholds = [
            item for item in result.break_evens
            if item.quantity == "degradation_s_per_lap"
        ]
        self.assertTrue(thresholds)
        crossing = thresholds[0].threshold_value
        self.assertIsNotNone(crossing)
        below = sm.compare_strategies(
            **self.BASE, degradation_s_per_lap=crossing * 0.9
        )
        above = sm.compare_strategies(
            **self.BASE, degradation_s_per_lap=crossing * 1.1
        )
        self.assertEqual(below.best_stop_count, result.best_stop_count)
        self.assertNotEqual(
            above.best_stop_count,
            result.best_stop_count,
            "past the solved break-even the plan must change",
        )

    def test_a_close_call_is_not_presented_as_decisive(self) -> None:
        result = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.01)
        thresholds = [
            item for item in result.break_evens
            if item.quantity == "degradation_s_per_lap"
        ]
        crossing = thresholds[0].threshold_value
        near = sm.compare_strategies(
            **self.BASE, degradation_s_per_lap=crossing * 0.999
        )
        self.assertLess(near.margin_s, sm.DECISIVE_MARGIN_S)
        self.assertFalse(near.decisive)

    def test_stints_cover_the_race_exactly(self) -> None:
        result = sm.compare_strategies(**self.BASE, degradation_s_per_lap=0.05)
        for item in result.strategies:
            if item.feasible:
                self.assertEqual(sum(item.stint_laps), self.BASE["race_laps"])
                self.assertEqual(len(item.stint_laps), item.stop_count + 1)

    def test_reserve_shortens_the_usable_stint(self) -> None:
        with_reserve = sm.compare_strategies(
            **self.BASE, degradation_s_per_lap=0.0, reserve_green_laps=1.0
        )
        without = sm.compare_strategies(
            **self.BASE, degradation_s_per_lap=0.0, reserve_green_laps=0.0
        )
        longest_with = max(
            max(item.stint_laps) for item in with_reserve.strategies if item.feasible
        )
        longest_without = max(
            max(item.stint_laps) for item in without.strategies if item.feasible
        )
        self.assertLess(longest_with, longest_without)


class StrategyRefusalTests(unittest.TestCase):
    def test_missing_inputs_are_named(self) -> None:
        result = sm.compare_strategies(
            race_laps=60,
            base_lap_s=None,
            degradation_s_per_lap=0.02,
            pit_loss_s=30.0,
            fuel_capacity_l=60.0,
            green_burn_l_per_lap=2.0,
        )
        self.assertEqual(result.status, sm.STATUS_UNAVAILABLE)
        self.assertIn("base_lap_s", result.reason)
        self.assertIsNone(result.best_stop_count)

    def test_reserve_larger_than_the_tank_is_refused(self) -> None:
        result = sm.compare_strategies(
            race_laps=60,
            base_lap_s=100.0,
            degradation_s_per_lap=0.02,
            pit_loss_s=30.0,
            fuel_capacity_l=2.0,
            green_burn_l_per_lap=2.0,
            reserve_green_laps=1.0,
        )
        self.assertEqual(result.status, sm.STATUS_UNAVAILABLE)
        self.assertEqual(result.reason, "reserve_exceeds_capacity")

    def test_a_race_longer_than_the_stop_bound_allows_is_refused(self) -> None:
        result = sm.compare_strategies(
            race_laps=500,
            base_lap_s=100.0,
            degradation_s_per_lap=0.02,
            pit_loss_s=30.0,
            fuel_capacity_l=20.0,
            green_burn_l_per_lap=2.0,
        )
        self.assertEqual(result.status, sm.STATUS_UNAVAILABLE)
        self.assertEqual(result.reason, "no_feasible_strategy_within_fuel_range")


class PayloadTests(unittest.TestCase):
    def test_payload_states_what_is_outside_the_model(self) -> None:
        payload = sm.compare_strategies(
            race_laps=60,
            base_lap_s=100.0,
            degradation_s_per_lap=0.02,
            pit_loss_s=30.0,
            fuel_capacity_l=60.0,
            green_burn_l_per_lap=2.0,
        ).to_payload()
        json.dumps(payload)
        excluded = " ".join(payload["excluded_from_model"])
        self.assertIn("track position", excluded)
        self.assertIn("not modelled and not simulated", excluded)

    def test_pit_loss_payload_declares_its_definition(self) -> None:
        payload = pit_loss.measure_pit_loss(
            session_time_s=[], on_pit_road=[], lap_dist_pct=[]
        ).to_payload()
        json.dumps(payload)
        self.assertIn("minus the time the same stretch", payload["definition"])


if __name__ == "__main__":
    unittest.main()
