from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import groove_analysis  # noqa: E402


def _geographic_migration() -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    samples_per_lap = 120
    lap_count = 6
    latitude_origin = 42.0
    longitude_origin = -71.0
    radius_m = 100.0
    circumference_m = 2.0 * math.pi * radius_m
    earth_radius_m = groove_analysis.EARTH_RADIUS_M
    channels = {
        name: []
        for name in (
            "Lat",
            "Lon",
            "LapDistPct",
            "Lap",
            "SessionTime",
            "SessionFlags",
            "OnPitRoad",
            "Speed",
            "LFodometer",
            "RFodometer",
            "LRodometer",
            "RRodometer",
        )
    }
    for lap in range(1, lap_count + 1):
        # A sustained two-metre move to the left of travel starts on lap four.
        lap_radius = radius_m if lap <= 3 else radius_m - 2.0
        for sample in range(samples_per_lap):
            pct = sample / samples_per_lap
            angle = 2.0 * math.pi * pct
            x = lap_radius * math.cos(angle)
            y = lap_radius * math.sin(angle)
            latitude = latitude_origin + math.degrees(y / earth_radius_m)
            longitude = longitude_origin + math.degrees(
                x / (earth_radius_m * math.cos(math.radians(latitude_origin)))
            )
            tire_distance = ((lap - 1) + pct) * circumference_m
            channels["Lat"].append(latitude)
            channels["Lon"].append(longitude)
            channels["LapDistPct"].append(pct)
            channels["Lap"].append(lap)
            channels["SessionTime"].append(((lap - 1) * samples_per_lap + sample) / 20.0)
            channels["SessionFlags"].append(0x0004)
            channels["OnPitRoad"].append(False)
            channels["Speed"].append(45.0)
            for tire in ("LF", "RF", "LR", "RR"):
                channels[f"{tire}odometer"].append(tire_distance)
    laps = [
        {"lap": lap, "complete": True, "flag_state": "green"}
        for lap in range(1, lap_count + 1)
    ]
    runs = [
        {
            "run_number": 1,
            "lap_numbers": list(range(1, lap_count + 1)),
            "tire_measurement_status": "measured_at_stop",
            "tire_observation": {
                "lowest_remaining_percent": 91.0,
                "lowest_remaining_tire": "RF",
            },
        }
    ]
    zones = [
        {
            "segment": 1,
            "start_pct": 0.10,
            "end_pct": 0.40,
            "wraps_start_finish": False,
        }
    ]
    units = {"Lat": "deg", "Lon": "deg"}
    units.update({f"{tire}odometer": "m" for tire in ("LF", "RF", "LR", "RR")})
    return channels, runs, laps, zones, units


class GrooveAnalysisTests(unittest.TestCase):
    def test_valid_green_selection_excludes_partial_pit_and_first_restart_lap(self) -> None:
        laps = [
            {"lap": 1, "complete": True, "flag_state": "green", "pit_time_s": 0.0, "racing_state_fraction": 1.0},
            {"lap": 2, "complete": True, "flag_state": "caution", "pit_time_s": 0.0, "racing_state_fraction": 0.0},
            {"lap": 3, "complete": True, "flag_state": "green", "pit_time_s": 0.0, "racing_state_fraction": 1.0},
            {"lap": 4, "complete": False, "flag_state": "green", "pit_time_s": 0.0, "racing_state_fraction": 1.0},
            {"lap": 5, "complete": True, "flag_state": "green", "pit_time_s": 1.2, "racing_state_fraction": 1.0},
            {"lap": 6, "complete": True, "flag_state": "green", "pit_time_s": 0.0, "racing_state_fraction": 0.9},
            {"lap": 7, "complete": True, "flag_state": "green", "pit_time_s": 0.0, "racing_state_fraction": 1.0},
        ]
        valid = groove_analysis._valid_green_lap_numbers(
            [{"run_number": 1, "lap_numbers": list(range(1, 8))}], laps
        )
        self.assertEqual(valid, {1, 7})

    def test_detects_observed_leftward_path_migration_without_best_groove_claim(self) -> None:
        channels, runs, laps, zones, units = _geographic_migration()

        result = groove_analysis.analyze_groove_evolution(
            channels,
            runs=runs,
            laps=laps,
            load_zones=zones,
            channel_units=units,
            track_type="oval",
        )

        self.assertEqual(result["status"], "available")
        self.assertFalse(result["coordinate_evidence"]["absolute_groove_claimed"])
        self.assertFalse(result["performance_claim"]["best_groove_claimed"])
        run = result["zones"][0]["runs"][0]
        self.assertTrue(run["fresh_vs_late_available"])
        self.assertEqual(run["fresh_tire_status"], "low-recorded-distance-initial-history-unknown")
        self.assertGreater(run["lateral_delta_late_minus_early_m"], 1.5)
        self.assertEqual(run["migration"]["status"], "detected")
        self.assertFalse(run["migration"]["traffic_and_clean_air_screened"])
        self.assertEqual(
            run["migration"]["direction"], "left-relative-to-session-reference"
        )
        calibration = result["zones"][0]["inside_outside_calibration"]
        self.assertEqual(calibration["status"], "calibrated")
        self.assertEqual(calibration["travel_turn_direction"], "left")
        self.assertGreaterEqual(
            calibration["curvature_sign_agreement_fraction"], 0.85
        )
        self.assertGreaterEqual(abs(calibration["net_heading_change_deg"]), 12.0)
        self.assertEqual(
            run["migration"]["inside_outside_direction"], "toward-inside"
        )
        self.assertEqual(
            run["migration"]["oval_low_high_direction"], "toward-low-side"
        )
        self.assertFalse(run["migration"]["recommendation_emitted"])
        self.assertFalse(calibration["absolute_lane_position_available"])
        self.assertEqual(run["migration"]["first_sustained_lap"], 4)
        self.assertEqual(run["late_tire_wear_context"]["status"], "measured-at-service")
        self.assertEqual(run["late_tire_wear_context"]["lowest_remaining_percent"], 91.0)

    def test_lap_distance_without_lat_lon_explicitly_returns_unavailable(self) -> None:
        result = groove_analysis.analyze_groove_evolution(
            {
                "LapDistPct": [0.0, 0.5] * 100,
                "Lap": [1, 1] * 100,
                "LFodometer": list(range(200)),
                "RFodometer": list(range(200)),
            },
            runs=[],
            laps=[],
            load_zones=[{"segment": 1, "start_pct": 0.1, "end_pct": 0.4}],
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("Lat", result["reason"])
        self.assertFalse(result["performance_claim"]["best_groove_claimed"])

    def test_position_without_tire_distance_does_not_infer_fresh_or_worn(self) -> None:
        channels, runs, laps, zones, units = _geographic_migration()
        for name in groove_analysis.TIRE_ODOMETER_CHANNELS:
            channels.pop(name)

        result = groove_analysis.analyze_groove_evolution(
            channels,
            runs=runs,
            laps=laps,
            load_zones=zones,
            channel_units=units,
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("tire odometer", result["reason"].lower())

    def test_signed_curvature_handles_right_turn_and_rejects_straight_zone(self) -> None:
        bins = 120
        clockwise = [
            (
                100.0 * math.cos(-2.0 * math.pi * index / bins),
                100.0 * math.sin(-2.0 * math.pi * index / bins),
            )
            for index in range(bins)
        ]
        zone = {"start_pct": 0.10, "end_pct": 0.40}
        right = groove_analysis._zone_curvature_calibration(
            clockwise,
            zone,
            observed_reference_bins=set(range(bins)),
            track_type="oval",
        )

        self.assertEqual(right["status"], "calibrated")
        self.assertEqual(right["travel_turn_direction"], "right")
        self.assertEqual(right["inside_lateral_offset_sign_value"], -1)
        self.assertEqual(
            right["oval_direction_mapping"]["toward_inside"], "toward-low-side"
        )

        straight = [(float(index), 0.0) for index in range(bins)]
        rejected = groove_analysis._zone_curvature_calibration(
            straight,
            zone,
            observed_reference_bins=set(range(bins)),
            track_type="oval",
        )
        self.assertEqual(rejected["status"], "unavailable")
        self.assertIn("net heading change", rejected["unavailable_reason"])


if __name__ == "__main__":
    unittest.main()
