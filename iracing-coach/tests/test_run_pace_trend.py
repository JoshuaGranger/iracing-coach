from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analysis_engine import TelemetryTable, _runs  # noqa: E402


class RunPaceTrendTests(unittest.TestCase):
    def make_laps(self, fractions: list[float], flags: list[str] | None = None) -> list[dict]:
        age = 0.0
        laps = []
        for index, fraction in enumerate(fractions):
            age += fraction
            laps.append({
                "lap": index + 1,
                "start_index": index * 4,
                "end_index": index * 4 + 3,
                "start_time": float(index * 4),
                "end_time": float(index * 4 + 3),
                "complete": True,
                "flag_state": flags[index] if flags else "green",
                "green_fraction": fraction,
                "caution_fraction": 1.0 - fraction,
                "racing_state_fraction": 1.0,
                "pit_time_s": 0.0,
                "lap_time_s": 30.0 + age * 0.02,
                "clean_context": {},
                "fuel": {"used_l": 0.1},
                "controls": {"brake_energy_proxy": 1.0, "steering_work_proxy": 1.0},
            })
        return laps

    def run_for(self, laps: list[dict]) -> dict:
        count = max(lap["end_index"] for lap in laps) + 1
        table = TelemetryTable({
            "channels": {
                "SessionTime": list(range(count)),
                "FuelLevel": [50.0 - index * 0.01 for index in range(count)],
                "SessionFlags": [0x0004] * count,
                "OnPitRoad": [False] * count,
            },
            "metadata": {"sample_rate": 1},
        })
        runs = _runs(table, laps)
        self.assertEqual(len(runs), 1)
        return runs[0]

    def test_sparse_clean_laps_keep_excluded_green_lap_exposure(self) -> None:
        laps = self.make_laps([1.0] * 10)
        for lap in laps:
            if lap["lap"] not in {1, 2, 5, 9, 10}:
                lap["clean_context"]["traffic_proximity_fraction"] = 0.5
        # Preserve the existing first-point exclusion for more than four timed laps.
        laps[0]["lap_time_s"] = 99.0
        run = self.run_for(laps)
        self.assertEqual(run["valid_green_lap_numbers"], [1, 2, 5, 9, 10])
        self.assertEqual(run["pace"]["green_lap_time_slope_s_per_lap"], 0.02)
        self.assertEqual(run["pace"]["slope_basis"], "cumulative_recorded_green_lap_equivalents")

    def test_cautions_add_no_age_but_mixed_and_restart_laps_keep_their_exposure(self) -> None:
        fractions = [1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.25, 1.0, 1.0, 1.0]
        flags = ["green", "green", "caution", "caution", "green", "green", "mixed", "green", "green", "green"]
        run = self.run_for(self.make_laps(fractions, flags))
        self.assertEqual(run["valid_green_lap_numbers"], [1, 2, 6, 8, 9, 10])
        self.assertEqual(run["pace"]["green_lap_time_slope_s_per_lap"], 0.02)

    def test_incomplete_interior_lap_withholds_rate_even_if_observed_samples_are_green(self) -> None:
        laps = self.make_laps([1.0] * 10)
        laps[4]["complete"] = False
        run = self.run_for(laps)
        self.assertNotIn(5, run["valid_green_lap_numbers"])
        self.assertIsNone(run["pace"]["green_lap_time_slope_s_per_lap"])
        self.assertEqual(run["pace"]["slope_unavailable_reason"], "incomplete_lap_exposure_between_observations")

    def test_missing_interior_lap_number_withholds_rate(self) -> None:
        laps = self.make_laps([1.0] * 10)
        del laps[4]
        run = self.run_for(laps)
        self.assertIsNone(run["pace"]["green_lap_time_slope_s_per_lap"])
        self.assertEqual(run["pace"]["slope_unavailable_reason"], "missing_lap_exposure_between_observations")

    def test_unknown_exposure_outside_retained_observation_span_does_not_hide_slope(self) -> None:
        laps = self.make_laps([1.0] * 12)
        for index in (0, 11):
            laps[index]["complete"] = False
            laps[index]["green_fraction"] = None
        # Lap 2 is the first timed observation and is dropped by the existing rule.
        laps[1]["green_fraction"] = None
        run = self.run_for(laps)
        self.assertEqual(run["pace"]["green_lap_time_slope_s_per_lap"], 0.02)
        self.assertIsNone(run["pace"]["slope_unavailable_reason"])

    def test_missing_number_before_first_retained_observation_does_not_hide_slope(self) -> None:
        laps = self.make_laps([1.0] * 10)
        del laps[1]
        # Lap 1 is dropped; the retained observations start at lap 3.
        run = self.run_for(laps)
        self.assertEqual(run["pace"]["green_lap_time_slope_s_per_lap"], 0.02)

    def test_contiguous_green_laps_retain_existing_slope_and_first_point_rule(self) -> None:
        laps = self.make_laps([1.0] * 8)
        laps[0]["lap_time_s"] = 99.0
        run = self.run_for(laps)
        self.assertEqual(run["pace"]["green_lap_time_slope_s_per_lap"], 0.02)
        self.assertIsNone(run["pace"]["slope_unavailable_reason"])

    def test_missing_or_invalid_exposure_withholds_slope_without_hiding_pace_means(self) -> None:
        for value in (None, float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(fraction=value):
                laps = self.make_laps([1.0] * 8)
                laps[3]["green_fraction"] = value
                laps[3]["clean_context"]["traffic_proximity_fraction"] = 0.5
                run = self.run_for(laps)
                self.assertIsNone(run["pace"]["green_lap_time_slope_s_per_lap"])
                self.assertEqual(run["pace"]["slope_unavailable_reason"], "green_lap_exposure_missing_or_invalid")
                self.assertIsNotNone(run["pace"]["early_average_lap_s"])
                self.assertIsNotNone(run["pace"]["late_average_lap_s"])

    def test_insufficient_timing_and_zero_exposure_do_not_emit_a_rate(self) -> None:
        run = self.run_for(self.make_laps([1.0, 1.0]))
        self.assertIsNone(run["pace"]["green_lap_time_slope_s_per_lap"])
        self.assertEqual(run["pace"]["slope_unavailable_reason"], "insufficient_timed_clean_laps")
        run = self.run_for(self.make_laps([0.0] * 6))
        self.assertIsNone(run["pace"]["green_lap_time_slope_s_per_lap"])
        self.assertEqual(run["pace"]["slope_unavailable_reason"], "insufficient_green_lap_exposure")


if __name__ == "__main__":
    unittest.main()
