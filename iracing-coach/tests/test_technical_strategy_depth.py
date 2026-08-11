from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analysis_engine import build_technical_insights  # noqa: E402
from race_card import build_race_card  # noqa: E402


def _controls() -> dict:
    return {
        "throttle_mean": 0.73,
        "brake_max": 0.48,
        "brake_steer_overlap_s": 1.25,
        "steering_corrections": 8,
        "steering_abs_mean_rad": 0.12,
    }


def _lap(number: int, lap_time: float, start: int = 8, end: int = 8) -> dict:
    return {
        "lap": number,
        "complete": True,
        "flag_state": "green",
        "lap_time_s": lap_time,
        "pit_time_s": 0.0,
        "position": {"start": start, "end": end},
        "controls": _controls(),
        "speed": {"minimum_mph": 91.0, "maximum_mph": 158.0},
    }


def _run(number: int, start_lap: int, end_lap: int) -> dict:
    return {
        "run_number": number,
        "start_lap": start_lap,
        "end_lap": end_lap,
        "total_laps": end_lap - start_lap + 1,
        "green_laps": end_lap - start_lap + 1,
        "valid_green_lap_numbers": [],
        "fuel": {"start_l": 70.0, "end_l": 42.0, "used_gal": 7.4},
        "pace": {},
        "position": {"start": 8, "end": 8},
        "vehicle_dynamics": {
            "front_wheel_lock_proxy_s": 6.0,
            "rear_wheelspin_proxy_s": 1.5,
            "abs_active_s": 0.0,
            "yaw_rate_abs_p95_deg_s_mean": 24.0,
        },
    }


class TechnicalStrategyDepthTests(unittest.TestCase):
    def test_no_stop_race_is_a_contextual_strategy_result_not_an_empty_card(self) -> None:
        runs = [_run(1, 1, 15)]
        strategy = {
            "forecast": {
                "status": "usable",
                "all_green_range_laps": 34.7,
                "minimum_stops_all_green": 0,
            }
        }

        pit = build_technical_insights(
            [_lap(index, 82.0 + index / 10) for index in range(1, 16)],
            runs,
            {"scheduled_laps": 15, "recorded_laps": 15},
            strategy,
            {},
            {},
        )[0]

        self.assertEqual(pit["status"], "available")
        self.assertEqual(pit["rating"], "no-stop")
        self.assertIn("No fuel stop was needed for 15 laps", pit["takeaway"])
        metrics = {item["label"]: item for item in pit["metrics"]}
        self.assertEqual(metrics["Stops completed"]["numeric_value"], 0)
        self.assertEqual(metrics["No-stop headroom"]["numeric_value"], 19.7)

    def test_zero_clean_reference_laps_still_surfaces_recorded_dynamics(self) -> None:
        run = _run(1, 1, 50)
        run["vehicle_dynamics"].update(
            front_wheel_lock_proxy_s=43.35,
            rear_wheelspin_proxy_s=5.75,
        )

        tires = build_technical_insights(
            [_lap(index, 25.0 + (index % 4) / 10) for index in range(1, 51)],
            [run],
            {"scheduled_laps": 50, "recorded_laps": 50},
            {"forecast": {}},
            {},
            {},
        )[1]

        self.assertEqual(tires["status"], "available")
        metrics = {item["label"]: item for item in tires["metrics"]}
        self.assertEqual(metrics["Front lock proxy"]["numeric_value"], 43.35)
        self.assertIn("0.867 s per green lap", metrics["Front lock proxy"]["detail"])
        self.assertIn("wheel-speed divergence", tires["takeaway"])

    def test_race_pace_fallback_rejects_extreme_green_timing_outlier(self) -> None:
        run = _run(1, 1, 4)
        insights = build_technical_insights(
            [_lap(1, 223.15), _lap(2, 81.5), _lap(3, 82.0), _lap(4, 82.5)],
            [run],
            {"starting_position": 8, "final_recorded_position": 8},
            {"forecast": {}},
            {},
            {},
        )
        racecraft = insights[3]
        metrics = {item["label"]: item for item in racecraft["metrics"]}

        self.assertEqual(racecraft["pace_sample"]["scope"], "representative race pace")
        self.assertEqual(racecraft["pace_sample"]["lap_count"], 3)
        self.assertLess(metrics["Race-pace variation"]["numeric_value"], 1.0)
        self.assertEqual(metrics["Fastest race pace"]["numeric_value"], 81.5)

    def test_right_side_call_is_explicit_and_two_vs_four_requires_both_calls(self) -> None:
        two = _run(1, 1, 20)
        two.update(
            ended_with_pit_stop=True,
            pit_service={
                "start_time": 100.0,
                "end_time": 108.0,
                "tires_changed_observed": ["RF", "RR"],
                "tire_change_confirmation": "confirmed_by_tire_counter_or_odometer",
            },
        )
        after_two = _run(2, 21, 40)
        after_two["pace"] = {"early_average_lap_s": 25.0, "green_laps_used": 18}
        after_two.update(
            ended_with_pit_stop=True,
            pit_service={
                "start_time": 200.0,
                "end_time": 214.0,
                "tires_changed_observed": ["LF", "RF", "LR", "RR"],
                "tire_change_confirmation": "confirmed_by_tire_counter_or_odometer",
            },
        )
        after_four = _run(3, 41, 60)
        after_four["pace"] = {"early_average_lap_s": 24.8, "green_laps_used": 18}
        strategy = {
            "forecast": {},
            "pit_assessments": [
                {"run_number": 1, "was_pit_stop": True, "pit_cycle_position_change": 2},
                {"run_number": 2, "was_pit_stop": True, "pit_cycle_position_change": -1},
            ],
        }

        pit = build_technical_insights([], [two, after_two, after_four], {}, strategy, {}, {})[0]
        calls = pit["tire_strategy"]["observed_calls"]

        self.assertEqual(calls[0]["side"], "right_side")
        self.assertEqual(calls[0]["tires_changed"], ["RF", "RR"])
        self.assertEqual(
            pit["tire_strategy"]["direct_two_vs_four_comparison"]["status"],
            "usable",
        )
        self.assertTrue(
            any(item["value"] == "RF + RR" for item in pit["metrics"])
        )

        only_right = build_technical_insights([], [two, after_four], {}, strategy, {}, {})[0]
        self.assertEqual(
            only_right["tire_strategy"]["direct_two_vs_four_comparison"]["status"],
            "unavailable",
        )
        self.assertFalse(
            any(item["label"].startswith("2 vs 4") for item in only_right["metrics"])
        )

    def test_race_card_recomputes_fuel_plan_for_requested_distance(self) -> None:
        analysis = {
            "identity": {
                "track_name": "Portland International Raceway",
                "track_config": "Full Circuit",
                "car_name": "Toyota Supra Class B",
                "is_fixed_setup": True,
            },
            "race_summary": {"scheduled_laps": 100, "recorded_laps": 15},
            "strategy": {
                "forecast": {
                    "status": "usable",
                    "all_green_range_laps": 34.7,
                    "minimum_stops_all_green": 2,
                    "equal_stint_pit_targets_all_green": [33, 67],
                    "operational_reserve_green_laps": 2,
                }
            },
            "technical_insights": [],
            "coaching_signals": [
                {
                    "priority": "medium",
                    "finding": "Telemetry did not expose a tire endpoint.",
                    "coaching": "Keep disk telemetry enabled and return to pit service.",
                }
            ],
        }

        short = build_race_card(analysis, race_distance_laps=15)
        strategy = next(item for item in short["actions"] if item["label"] == "Strategy")
        start = next(item for item in short["actions"] if item["label"] == "Start")

        self.assertIn("No fuel stop needed for 15 laps", strategy["text"])
        self.assertIn("15 laps", short["title"])
        self.assertNotIn("disk telemetry", start["text"].lower())
        self.assertEqual(short["actions"][1]["label"], "Race pace")
        self.assertEqual(
            [item["label"] for item in short["race_triggers"]],
            ["Balance checkpoint", "Fuel response", "Balance response"],
        )

        long = build_race_card(analysis, race_distance_laps=100)
        strategy = next(item for item in long["actions"] if item["label"] == "Strategy")
        self.assertIn("Plan 2 fuel stops for 100 laps", strategy["text"])
        self.assertIn("Lap 33/67", strategy["text"])

    def test_race_card_never_promotes_a_metric_definition_to_start_priority(self) -> None:
        analysis = {
            "identity": {
                "track_name": "Iowa Speedway",
                "track_config": "Oval",
                "car_name": "Toyota Supra Class B",
                "is_fixed_setup": True,
            },
            "race_summary": {"scheduled_laps": 55, "recorded_laps": 55},
            "runs": [
                {
                    "run_number": 3,
                    "tire_observation": {
                        "lowest_remaining_tire": "RF",
                        "lowest_remaining_percent": 97.79,
                    },
                }
            ],
            "strategy": {
                "forecast": {
                    "status": "usable",
                    "all_green_range_laps": 92.8,
                    "minimum_stops_all_green": 0,
                    "equal_stint_pit_targets_all_green": [],
                }
            },
            "technical_insights": [
                {
                    "key": "tires",
                    "metrics": [
                        {
                            "tone": "attention",
                            "action": "A positive value is falloff; compare the same run's tire condition and driving load.",
                        }
                    ],
                }
            ],
            "coaching_signals": [],
        }

        card = build_race_card(analysis, race_distance_laps=55)
        priorities = {item["label"]: item["text"] for item in card["actions"]}
        triggers = {item["label"]: item["text"] for item in card["race_triggers"]}

        self.assertEqual(
            priorities["Start"],
            "Protect the RF from the start: finish brake release before adding steering",
        )
        self.assertIn("As the run ages", priorities["Long run"])
        self.assertIn("No fuel stop needed for 55 laps", priorities["Strategy"])
        self.assertIn("55-lap finish", triggers["Fuel response"])
        self.assertIn("undo it if pace or stability worsens", triggers["Balance response"])
        self.assertNotIn(
            "positive value is",
            " ".join([*priorities.values(), *triggers.values()]).lower(),
        )


if __name__ == "__main__":
    unittest.main()
