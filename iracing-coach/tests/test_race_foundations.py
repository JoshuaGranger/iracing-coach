from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from race_foundations import (  # noqa: E402
    build_race_replay,
    build_tire_prediction,
    build_track_geometry,
)
from analysis_engine import build_technical_insights  # noqa: E402
from storage import ArchiveStore, file_sha256  # noqa: E402


class RaceFoundationTests(unittest.TestCase):
    def test_geometry_preserves_main_pit_and_line_primitives(self) -> None:
        count = 360
        pct = [(index % 120) / 120 for index in range(count)]
        on_pit = [210 <= index < 228 for index in range(count)]
        radius = [0.72 if value else 1.0 for value in on_pit]
        channels = {
            "LapDistPct": pct,
            "Lat": [radius[index] * math.sin(2 * math.pi * pct[index]) for index in range(count)],
            "Lon": [radius[index] * math.cos(2 * math.pi * pct[index]) for index in range(count)],
            "OnPitRoad": on_pit,
        }
        result = build_track_geometry(
            channels,
            {"sample_rate": 20},
            {"track_id": 7, "track_name": "Test Oval", "track_config": "Oval"},
            [{"sha256": "a" * 64}],
            main_bins=100,
        )
        self.assertIn(result["status"], {"usable", "partial"})
        self.assertGreater(len(result["main_path"]), 50)
        self.assertGreater(len(result["pit_lane"]), 5)
        self.assertIsNotNone(result["start_finish_line"])
        self.assertIsNotNone(result["pit_commitment_line"])
        self.assertIsNotNone(result["pit_merge_line"])
        self.assertEqual(result["source_sha256"], ["a" * 64])
        self.assertTrue(result["quality"]["main_loop_complete"])
        self.assertRegex(result["geometry_hash"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(result["quality"]["lap_percent_coverage"], 0.95)
        self.assertLessEqual(result["quality"]["maximum_lap_percent_gap"], 0.05)
        self.assertLessEqual(result["quality"]["closure_distance"], 0.15)
        main_only = build_track_geometry(
            {key: value for key, value in channels.items() if key != "OnPitRoad"},
            {"sample_rate": 20},
            {"track_id": 7, "track_name": "Test Oval", "track_config": "Oval"},
            main_bins=100,
        )
        self.assertEqual(main_only["status"], "partial")
        self.assertRegex(main_only["geometry_hash"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(main_only["geometry_hash"], result["geometry_hash"])
        self.assertGreater(len(main_only["main_path"]), 50)
        self.assertTrue(any("Pit-road state" in reason for reason in main_only["unavailable_reasons"]))

    def test_incomplete_main_loop_is_unavailable_and_never_gets_a_start_line(self) -> None:
        pct = [0.18 + index * 0.004 for index in range(120)]
        result = build_track_geometry(
            {
                "LapDistPct": pct,
                "Lat": [math.sin(2 * math.pi * value) for value in pct],
                "Lon": [math.cos(2 * math.pi * value) for value in pct],
                "OnPitRoad": [False] * len(pct),
            },
            {"sample_rate": 60},
            {"track_id": 7, "track_name": "Test Oval", "track_config": "Oval"},
            [{"sha256": "f" * 64}],
            main_bins=200,
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["main_path"], [])
        self.assertIsNone(result["start_finish_line"])
        self.assertIsNone(result["geometry_hash"])
        self.assertFalse(result["quality"]["main_loop_complete"])
        self.assertLess(result["quality"]["lap_percent_coverage"], 0.95)
        self.assertGreater(result["quality"]["maximum_lap_percent_gap"], 0.05)
        self.assertGreater(result["quality"]["observed_main_path_points"], 20)

    def test_geometry_cache_isolated_by_exact_configuration_and_keeps_best_coverage(self) -> None:
        def geometry(key: str, count: int, source_hash: str, offset: float = 0.0) -> dict:
            points = [
                {
                    "x": offset + 0.5 + 0.5 * math.cos(2 * math.pi * index / count),
                    "y": 0.5 + 0.5 * math.sin(2 * math.pi * index / count),
                    "lap_pct": index / count,
                }
                for index in range(count)
            ]
            return {
                "schema_version": 1,
                "status": "usable",
                "track_configuration_key": key,
                "coordinate_system": "normalized_local_vector",
                "main_path": points,
                "pit_lane": [],
                "pit_entry_path": [],
                "pit_exit_path": [],
                "start_finish_line": None,
                "pit_commitment_line": None,
                "pit_merge_line": None,
                "unavailable_reasons": [],
                "source_sha256": [source_hash],
                "contributing_source_sha256": [source_hash],
                "observed_source_sha256": [source_hash],
                "transform": {
                    "source_bounds": {
                        "minimum_x": offset,
                        "maximum_x": offset + 1.0,
                        "minimum_y": 0.5,
                        "maximum_y": 0.5,
                    },
                    "normalization_scale": 1.0,
                },
                "quality": {
                    "main_loop_complete": True,
                    "lap_percent_coverage": 1.0 - 1.0 / count,
                    "maximum_lap_percent_gap": 1.0 / count,
                    "closure_distance": math.hypot(
                        points[-1]["x"] - points[0]["x"],
                        points[-1]["y"] - points[0]["y"],
                    ),
                    "main_path_points": count,
                    "observed_main_path_points": count,
                    "pit_lane_points": 0,
                    "pit_entry_observations": 0,
                    "pit_exit_observations": 0,
                },
            }

        with tempfile.TemporaryDirectory() as folder:
            store = ArchiveStore(Path(folder) / "portable")
            oval_low = store.cache_track_geometry(geometry("7-oval", 40, "a" * 64, 1.0))
            oval_high = store.cache_track_geometry(geometry("7-oval", 100, "b" * 64, 2.0))
            road = store.cache_track_geometry(geometry("7-road", 60, "c" * 64))
            reused = ArchiveStore(Path(folder) / "portable").cache_track_geometry(
                geometry("7-oval", 30, "c" * 64, 3.0)
            )
            oval_payload = json.loads(Path(oval_high["cache"]["path"]).read_text(encoding="utf-8"))
            road_payload = json.loads(Path(road["cache"]["path"]).read_text(encoding="utf-8"))

            self.assertEqual(oval_low["cache"]["path"], oval_high["cache"]["path"])
            self.assertNotEqual(oval_high["cache"]["path"], road["cache"]["path"])
            self.assertEqual(oval_payload["track_configuration_key"], "7-oval")
            self.assertEqual(len(oval_payload["main_path"]), 100)
            self.assertEqual(oval_payload["source_sha256"], ["a" * 64, "b" * 64, "c" * 64])
            self.assertEqual(oval_payload["contributing_source_sha256"], ["b" * 64])
            self.assertEqual(oval_payload["observed_source_sha256"], ["a" * 64, "b" * 64, "c" * 64])
            self.assertEqual(reused["transform"]["source_bounds"]["minimum_x"], 2.0)
            self.assertRegex(reused["geometry_hash"], r"^[0-9a-f]{64}$")
            provenance = reused["geometry_provenance"]
            self.assertEqual(provenance["normalization_transform"], reused["transform"])
            self.assertEqual(len(provenance["observations"]), 3)
            selected = next(
                item
                for item in provenance["observations"]
                if item["observation_id"] == provenance["selected_observation_id"]
            )
            self.assertEqual(selected["source_sha256"], ["b" * 64])
            self.assertEqual(selected["transform"], reused["transform"])
            self.assertEqual(road_payload["track_configuration_key"], "7-road")

            legacy_partial = geometry("7-oval", 1000, "d" * 64, 4.0)
            legacy_partial["main_path"] = legacy_partial["main_path"][:500]
            legacy_partial["quality"] = {
                "main_path_points": 500,
                "pit_lane_points": 0,
                "pit_entry_observations": 0,
                "pit_exit_observations": 0,
            }
            protected = store.cache_track_geometry(legacy_partial)
            self.assertTrue(protected["quality"]["main_loop_complete"])
            self.assertEqual(len(protected["main_path"]), 100)
            self.assertEqual(protected["contributing_source_sha256"], ["b" * 64])
            self.assertIn("d" * 64, protected["observed_source_sha256"])

    def test_legacy_open_geometry_is_sanitized_when_it_is_the_only_cache_observation(self) -> None:
        partial_path = [
            {
                "x": 0.5 + 0.5 * math.cos(2 * math.pi * index / 1000),
                "y": 0.5 + 0.5 * math.sin(2 * math.pi * index / 1000),
                "lap_pct": index / 1000,
            }
            for index in range(500)
        ]
        legacy = {
            "schema_version": 1,
            "status": "usable",
            "track_configuration_key": "7-oval",
            "track_id": 7,
            "track_name": "Test Oval",
            "track_config": "Oval",
            "coordinate_system": "normalized_local_vector",
            "main_path": partial_path,
            "pit_lane": [],
            "pit_entry_path": [],
            "pit_exit_path": [],
            "start_finish_line": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 1.0, "y": 1.0}},
            "unavailable_reasons": [],
            "source_sha256": ["e" * 64],
            "quality": {"main_path_points": len(partial_path)},
        }

        with tempfile.TemporaryDirectory() as folder:
            cached = ArchiveStore(Path(folder) / "portable").cache_track_geometry(legacy)

        self.assertEqual(cached["status"], "unavailable")
        self.assertEqual(cached["main_path"], [])
        self.assertIsNone(cached["start_finish_line"])
        self.assertIsNone(cached["geometry_hash"])
        self.assertFalse(cached["quality"]["main_loop_complete"])
        self.assertEqual(cached["quality"]["main_path_points"], 0)
        self.assertEqual(cached["quality"]["observed_main_path_points"], len(partial_path))

    def test_old_recording_reports_full_field_replay_unavailable(self) -> None:
        result = build_race_replay(
            {"SessionTime": [0.0, 0.1], "SessionState": [3, 4]},
            {"DriverInfo": {"DriverCarIdx": 0, "Drivers": [{"CarIdx": 0}]}},
            {"sample_rate": 10},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("CarIdxLapDistPct" in reason for reason in result["unavailable_reasons"]))
        self.assertEqual(result["frames"], [])
        coverage = {item["channel"]: item for item in result["coverage"]}
        self.assertEqual(coverage["SessionTime"]["status"], "recorded")
        self.assertEqual(coverage["CarIdxLapDistPct"]["status"], "unavailable")
        self.assertIn("place competitors", coverage["CarIdxLapDistPct"]["reason"])
        self.assertTrue(
            any("Competitor fuel, tire wear, tire temperature, and setup" in item for item in result["limitations"])
        )

    def test_recorded_full_field_arrays_build_ordered_replay_frames(self) -> None:
        channels = {
            "SessionTime": [0.0, 0.5, 1.0],
            "SessionState": [3, 4, 5],
            "SessionFlags": [4, 4, 1],
            "CarIdxLapDistPct": [[0.9, 0.8], [0.1, 0.95], [0.3, 0.2]],
            "CarIdxLap": [[0, 0], [1, 0], [1, 1]],
            "CarIdxPosition": [[1, 2], [1, 2], [1, 2]],
            "CarIdxClassPosition": [[1, 2], [1, 2], [1, 2]],
            "CarIdxOnPitRoad": [[False, False], [False, True], [False, False]],
            "CarIdxLastLapTime": [[None, None], [24.8, 25.1], [24.7, 25.0]],
            "CarIdxBestLapTime": [[None, None], [24.8, 25.1], [24.7, 25.0]],
        }
        session_info = {
            "DriverInfo": {
                "DriverCarIdx": 0,
                "Drivers": [
                    {"CarIdx": 0, "CarNumber": "7", "CarClassID": 1, "UserName": "Player"},
                    {"CarIdx": 1, "CarNumber": "12", "CarClassID": 1, "UserName": "Rival"},
                ],
            }
        }
        result = build_race_replay(channels, session_info, {"sample_rate": 2}, output_hz=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["participant_count"], 2)
        self.assertEqual(result["frames"][1]["cars"][1]["on_pit_road"], True)
        self.assertEqual(result["frames"][1]["cars"][0]["last_lap_time_s"], 24.8)
        self.assertEqual(result["frames"][-1]["cars"][1]["best_lap_time_s"], 25.0)
        self.assertEqual(result["frames"][-1]["global_flag_labels"], ["checkered"])
        forbidden_competitor_metrics = {
            "fuel",
            "fuel_level",
            "throttle",
            "brake",
            "steering",
            "setup",
            "tire_wear",
            "tire_temperature",
        }
        for frame in result["frames"]:
            for car in frame["cars"]:
                self.assertTrue(forbidden_competitor_metrics.isdisjoint(car))

    def test_tire_prediction_requires_three_sessions_and_keeps_omi(self) -> None:
        def observation(identifier: str, wear: float) -> dict:
            return {
                "observation_id": identifier,
                "analysis_id": identifier,
                "eligible_for_rate_model": True,
                "tires": {
                    corner: {
                        "green_laps_on_set": 20,
                        "remaining_percent_omi": {
                            "outer": 100 - wear,
                            "middle": 100 - wear * 0.8,
                            "inner": 100 - wear * 0.6,
                        },
                    }
                    for corner in ("LF", "RF", "LR", "RR")
                },
                "pace": {"capability_lap_s": 30.0, "capability_tire_age_green_laps": 5, "pace_cost_s": 0.4, "slope_s_per_green_lap": 0.02},
                "conditions": {"track_temp_c": 30 + wear / 10, "air_temp_c": 22, "caution_fraction": 0.1, "tire_compound": 0},
                "load": {"early_brake_energy_proxy": 10, "late_brake_energy_proxy": 12, "early_steering_work_proxy": 8, "late_steering_work_proxy": 9},
                "vehicle_dynamics": {"braking_wheel_lock_proxy_s": 0.1, "rear_wheelspin_proxy_s": 0.2, "yaw_rate_abs_p95_deg_s_mean": 12},
            }

        current = {corner: {"green_laps": 10} for corner in ("LF", "RF", "LR", "RR")}
        unavailable = build_tire_prediction([observation("one", 20)], current)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertNotIn("tires", unavailable)
        self.assertNotIn("laps_remaining", unavailable)
        self.assertEqual(unavailable["eligible_observations"], 1)
        self.assertEqual(unavailable["matching_sessions"], 1)
        predicted = build_tire_prediction(
            [observation("one", 20), observation("two", 24), observation("three", 22)],
            current,
            {"conditions": {"track_temp_c": 32.2, "air_temp_c": 22, "caution_fraction": 0.1, "tire_compound": 0}, "load": {"early_brake_energy_proxy": 10, "late_brake_energy_proxy": 12, "early_steering_work_proxy": 8, "late_steering_work_proxy": 9}, "vehicle_dynamics": {"braking_wheel_lock_proxy_s": 0.1, "rear_wheelspin_proxy_s": 0.2, "yaw_rate_abs_p95_deg_s_mean": 12}},
        )
        self.assertEqual(predicted["status"], "predicted")
        self.assertEqual(set(predicted["tires"]["RF"]), {"outer", "middle", "inner"})
        self.assertGreater(predicted["laps_remaining"], 0)
        self.assertEqual(predicted["capability_status"], "predicted")
        self.assertIn("track_temp_c", predicted["feature_match"])
        self.assertEqual(predicted["model_version"], "nascar-tire-condition-load-match-v1")
        self.assertEqual(len(predicted["observation_set_fingerprint"]), 64)
        reversed_prediction = build_tire_prediction(
            [observation("three", 22), observation("two", 24), observation("one", 20)],
            current,
            {"conditions": {"track_temp_c": 32.2, "air_temp_c": 22, "caution_fraction": 0.1, "tire_compound": 0}, "load": {"early_brake_energy_proxy": 10, "late_brake_energy_proxy": 12, "early_steering_work_proxy": 8, "late_steering_work_proxy": 9}, "vehicle_dynamics": {"braking_wheel_lock_proxy_s": 0.1, "rear_wheelspin_proxy_s": 0.2, "yaw_rate_abs_p95_deg_s_mean": 12}},
        )
        self.assertEqual(
            predicted["observation_set_fingerprint"],
            reversed_prediction["observation_set_fingerprint"],
        )

    def test_replay_tolerates_a_short_optional_global_flag_series(self) -> None:
        result = build_race_replay(
            {
                "SessionTime": [0.0, 0.5, 1.0],
                "SessionState": [3, 4, 5],
                "SessionFlags": [4],
                "CarIdxLapDistPct": [[0.0], [0.5], [0.9]],
            },
            {"DriverInfo": {"DriverCarIdx": 0, "Drivers": [{"CarIdx": 0}]}},
            {"sample_rate": 2},
            output_hz=2,
        )

        self.assertEqual(len(result["frames"]), 3)
        self.assertEqual(result["frames"][0]["global_flags"], 4)
        self.assertEqual(result["frames"][-1]["global_flags"], 0)

    def test_tire_prediction_confidence_is_based_on_distinct_matching_sessions(self) -> None:
        def observation(identifier: str, *, with_features: bool = False) -> dict:
            value = {
                "observation_id": identifier,
                "analysis_id": identifier,
                "eligible_for_rate_model": True,
                "tires": {
                    corner: {
                        "green_laps_on_set": 20,
                        "remaining_percent_omi": {"outer": 80, "middle": 82, "inner": 84},
                    }
                    for corner in ("LF", "RF", "LR", "RR")
                },
                "pace": {},
            }
            if with_features:
                value.update({
                    "conditions": {
                        "track_temp_c": 32.0,
                        "air_temp_c": 22.0,
                        "caution_fraction": 0.1,
                        "tire_compound": 0,
                    },
                    "load": {
                        "early_brake_energy_proxy": 10.0,
                        "late_brake_energy_proxy": 11.0,
                        "early_steering_work_proxy": 8.0,
                        "late_steering_work_proxy": 9.0,
                    },
                    "vehicle_dynamics": {
                        "braking_wheel_lock_proxy_s": 0.1,
                        "rear_wheelspin_proxy_s": 0.2,
                    },
                })
            return value

        age = {corner: {"green_laps": 10} for corner in ("LF", "RF", "LR", "RR")}
        two = build_tire_prediction([observation(str(index)) for index in range(2)], age)
        self.assertEqual(two["status"], "unavailable")
        self.assertEqual(two["confidence"], "low")
        five_sparse = build_tire_prediction([observation(str(index)) for index in range(5)], age)
        ten_sparse = build_tire_prediction([observation(str(index)) for index in range(10)], age)
        self.assertEqual(five_sparse["confidence"], "low")
        self.assertEqual(ten_sparse["confidence"], "low")
        self.assertEqual(five_sparse["comparable_feature_count"], 0)
        context = {
            "conditions": {
                "track_temp_c": 32.0,
                "air_temp_c": 22.0,
                "caution_fraction": 0.1,
                "tire_compound": 0,
            },
            "load": {
                "early_brake_energy_proxy": 10.0,
                "late_brake_energy_proxy": 11.0,
                "early_steering_work_proxy": 8.0,
                "late_steering_work_proxy": 9.0,
            },
            "vehicle_dynamics": {
                "braking_wheel_lock_proxy_s": 0.1,
                "rear_wheelspin_proxy_s": 0.2,
            },
        }
        five_matched = build_tire_prediction(
            [observation(str(index), with_features=True) for index in range(5)],
            age,
            context,
        )
        ten_matched = build_tire_prediction(
            [observation(str(index), with_features=True) for index in range(10)],
            age,
            context,
        )
        self.assertEqual(five_matched["confidence"], "medium")
        self.assertEqual(ten_matched["confidence"], "high")
        self.assertGreaterEqual(five_matched["comparable_feature_count"], 4)

    def test_technical_insights_use_event_specific_recorded_evidence(self) -> None:
        laps = [
            {
                "lap": number,
                "complete": True,
                "flag_state": flag,
                "lap_time_s": 30.0 + number / 10,
                "pit_time_s": 0.0,
                "position": {"start": start, "end": end},
            }
            for number, flag, start, end in (
                (1, "green", 10, 9),
                (2, "caution", 9, 9),
                (3, "green", 9, 7),
                (4, "green", 7, 8),
                (5, "green", 8, 8),
                (6, "green", 8, 9),
            )
        ]
        tire_observation = {
            "tires": {
                "RF": {
                    "remaining_percent": {"L": 82.0, "M": 78.0, "R": 74.0}
                }
            }
        }
        runs = [
            {
                "run_number": 1,
                "start_lap": 1,
                "end_lap": 3,
                "ended_with_pit_stop": True,
                "position": {"start": 10, "end": 7},
                "pit_service": {"start_time": 100.0, "end_time": 112.5},
                "fuel": {"start_l": 40.0, "end_l": 5.0},
                "pace": {
                    "green_laps_used": 8,
                    "early_to_late_delta_s": 0.45,
                },
                "driving_load": {
                    "early_brake_energy_proxy": 10.0,
                    "late_brake_energy_proxy": 12.0,
                    "early_steering_work_proxy": 8.0,
                    "late_steering_work_proxy": 8.4,
                },
                "tire_observation": tire_observation,
            },
            {
                "run_number": 2,
                "start_lap": 4,
                "end_lap": 6,
                "ended_with_pit_stop": False,
                "position": {"start": 9, "end": 9},
                "fuel": {"start_l": 35.0, "end_l": 4.0},
                "pace": {},
            },
        ]
        strategy = {
            "measured_green_fuel_l_per_lap": 2.0,
            "measured_green_fuel_gal_per_lap": 0.5283,
            "forecast": {"all_green_range_laps": 18.0},
            "pit_assessments": [{
                "was_pit_stop": True,
                "pit_cycle_position_change": -2,
                "post_stop_all_green_surplus_laps": 1.5,
            }],
        }
        tire_learning = {
            "prediction": {
                "status": "predicted",
                "laps_remaining": 14.2,
                "confidence": "medium",
            }
        }
        insights = build_technical_insights(
            laps,
            runs,
            {"scheduled_laps": 6, "starting_position": 10, "final_recorded_position": 9},
            strategy,
            {"status": "available", "summary": {"total_repair_work_completed_s": 4.5}},
            tire_learning,
        )
        by_key = {item["key"]: item for item in insights}

        self.assertIn("2 position(s) worse", by_key["pit"]["takeaway"])
        self.assertEqual(by_key["pit"]["metrics"][0]["numeric_value"], -2)
        self.assertIn("RF outer", by_key["tires"]["metrics"][0]["label"])
        self.assertIn("0.450 s slower late", by_key["tires"]["takeaway"])
        self.assertIn("14.2 green laps", by_key["tires"]["takeaway"])
        self.assertIn("Finish reserve", [item["label"] for item in by_key["fuel"]["metrics"]])
        self.assertTrue(
            any(
                item["label"].startswith("Weakest phase")
                for item in by_key["racecraft"]["metrics"]
            )
        )
        self.assertTrue(all(len(item["metrics"]) <= 3 for item in insights))

    def test_raw_archive_is_verified_deduplicated_and_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "race.ibt"
            source.write_bytes(b"recorded telemetry")
            fingerprint = {
                "path": str(source),
                "size": source.stat().st_size,
                "modified_ns": source.stat().st_mtime_ns,
                "sha256": file_sha256(source),
            }
            store = ArchiveStore(root / "portable")
            first = store.archive_raw_telemetry([fingerprint])
            second = store.archive_raw_telemetry([fingerprint])
            archived = Path(first["items"][0]["archive_path"])
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), b"recorded telemetry")
            self.assertEqual(archived.read_bytes(), source.read_bytes())
            self.assertEqual(second["items"][0]["archive_path"], str(archived))
            manifest = json.loads((archived.parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["sha256"], fingerprint["sha256"])
            self.assertEqual(archived.parent.name, fingerprint["sha256"])
            source.unlink()
            self.assertEqual(archived.read_bytes(), b"recorded telemetry")
            self.assertIn("never deleted by analysis", manifest["retention"])

    def test_raw_archive_preserves_duplicate_content_discovery_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_source = root / "first" / "race.ibt"
            second_source = root / "second" / "copied-race.ibt"
            first_source.parent.mkdir()
            second_source.parent.mkdir()
            first_source.write_bytes(b"same immutable telemetry bytes")
            second_source.write_bytes(first_source.read_bytes())
            store = ArchiveStore(root / "portable")

            first_fingerprint = store.source_fingerprints([first_source])[0]
            first = store.archive_raw_telemetry([first_fingerprint])
            second_fingerprint = store.source_fingerprints([second_source])[0]
            second = store.archive_raw_telemetry([second_fingerprint])
            later_ns = first_source.stat().st_mtime_ns + 2_000_000_000
            os.utime(first_source, ns=(later_ns, later_ns))
            later_fingerprint = store.source_fingerprints([first_source])[0]
            later = store.archive_raw_telemetry([later_fingerprint])
            repeated = store.archive_raw_telemetry([later_fingerprint])

            archive_paths = {
                result["items"][0]["archive_path"]
                for result in (first, second, later, repeated)
            }
            self.assertEqual(len(archive_paths), 1)
            archived = Path(archive_paths.pop())
            manifest = json.loads((archived.parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["source_discovery_count"], 3)
            self.assertEqual(len(manifest["source_discoveries"]), 3)
            self.assertEqual(
                {item["source_path"] for item in manifest["source_discoveries"]},
                {str(first_source.resolve()), str(second_source.resolve())},
            )
            first_discoveries = [
                item
                for item in manifest["source_discoveries"]
                if item["source_path"] == str(first_source.resolve())
            ]
            self.assertEqual(len(first_discoveries), 2)
            self.assertNotEqual(
                first_discoveries[0]["modified_ns"], first_discoveries[1]["modified_ns"]
            )
            self.assertFalse(repeated["items"][0]["new_discovery"])
            self.assertEqual(first_source.read_bytes(), archived.read_bytes())
            self.assertEqual(second_source.read_bytes(), archived.read_bytes())

    def test_offline_workflow_durably_archives_before_cache_or_analysis_use(self) -> None:
        workflow = (SCRIPTS / "workflow.py").read_text(encoding="utf-8")
        archive_call = workflow.index("raw_archive = store.archive_raw_telemetry(source_fingerprints)")
        cache_lookup = workflow.index("if analysis_cache_path.is_file()")
        analysis_call = workflow.index("analysis = analyze_telemetry(")

        self.assertLess(archive_call, cache_lookup)
        self.assertLess(archive_call, analysis_call)
        self.assertIn('"mode": "content-addressed-portable-copy"', workflow)
        self.assertIn('"durably_copied": raw_archive.get("durably_copied") is True', workflow)

    def test_live_capture_chunks_merge_only_into_exact_session(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            capture = root / "telemetry-traces" / "live-replay" / "session"
            capture.mkdir(parents=True)
            chunk = {
                "schemaVersion": 1,
                "frames": [{
                    "capturedAt": "2026-08-07T12:00:00Z",
                    "sessionTimeSeconds": 10.5,
                    "sessionState": 4,
                    "sessionFlags": 4,
                    "cars": [{"carIndex": 0, "lapDistancePercent": 0.25, "lap": 2, "overallPosition": 1, "classPosition": 1, "onPitRoad": False, "trackSurface": 3, "lastLapSeconds": 24.7, "bestLapSeconds": 24.5}],
                }],
            }
            chunk_path = capture / "chunk-000000.json"
            chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
            manifest = {
                "schemaVersion": 1,
                "status": "finalized",
                "sessionKey": "exact",
                "subsessionId": 123,
                "sessionNumber": 0,
                "playerCarIndex": 0,
                "coverage": [
                    {"channel": "SessionTime", "recorded": True},
                    {"channel": "SessionState", "recorded": True},
                    {"channel": "CarIdxLapDistPct", "recorded": True},
                ],
                "participants": [{"carIndex": 0, "carNumber": "7", "classId": 1, "driverName": "Player"}],
                "chunks": [{"file": chunk_path.name, "sha256": file_sha256(chunk_path), "frameCount": 1}],
            }
            (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            analysis = {
                "identity": {"subsession_id": 123},
                "source": {"selection": {"subsession_id": "123", "sim_session_num": "0", "sim_session_type": "Race"}},
            }
            replay = store.live_replay_for_analysis(analysis)
            self.assertIsNotNone(replay)
            self.assertEqual(replay["status"], "usable")
            self.assertEqual(replay["player_car_index"], 0)
            self.assertEqual(replay["frames"][0]["cars"][0]["overall_position"], 1)
            self.assertEqual(replay["frames"][0]["cars"][0]["last_lap_time_s"], 24.7)
            self.assertEqual(replay["frames"][0]["cars"][0]["best_lap_time_s"], 24.5)
            self.assertIsNone(store.live_replay_for_analysis({"identity": {"subsession_id": 999}}))

    def test_live_replay_merge_requires_phase_and_deduplicates_reconnect_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            capture_root = root / "telemetry-traces" / "live-replay"

            def write_capture(name: str, session_type: str, cars: list[dict]) -> None:
                capture = capture_root / name
                capture.mkdir(parents=True)
                chunk = {
                    "schemaVersion": 1,
                    "frames": [{
                        "capturedAt": f"2026-08-07T12:00:0{len(cars)}Z",
                        "sessionTimeSeconds": 10.5,
                        "sessionState": 4,
                        "sessionFlags": 4,
                        "cars": cars,
                    }],
                }
                chunk_path = capture / "chunk-000000.json"
                chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
                manifest = {
                    "schemaVersion": 1,
                    "status": "finalized",
                    "sessionKey": name,
                    "subsessionId": 123,
                    "sessionType": session_type,
                    "playerCarIndex": 0,
                    "coverage": [
                        {"channel": channel, "recorded": True}
                        for channel in ("SessionTime", "SessionState", "CarIdxLapDistPct")
                    ],
                    "participants": [{"carIndex": 0, "carNumber": "7"}],
                    "chunks": [{"file": chunk_path.name, "sha256": file_sha256(chunk_path), "frameCount": 1}],
                }
                (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            player = {"carIndex": 0, "lapDistancePercent": 0.25, "overallPosition": 2}
            rival = {"carIndex": 1, "lapDistancePercent": 0.30, "overallPosition": 1}
            write_capture("race-first", "Race", [player])
            write_capture("race-reconnect", "Race", [player, rival])
            write_capture("qualifying", "Lone Qualify", [{**player, "overallPosition": 1}])
            replay = store.live_replay_for_analysis({
                "identity": {"subsession_id": 123},
                "source": {"selection": {"sim_session_type": "Race"}},
            })

            self.assertIsNotNone(replay)
            self.assertEqual(replay["frame_count"], 1)
            self.assertEqual(len(replay["frames"][0]["cars"]), 2)
            self.assertEqual(len(replay["capture_manifests"]), 2)
            self.assertTrue(all("qualifying" not in path for path in replay["capture_manifests"]))

    def test_live_replay_mixed_manifest_coverage_is_never_promoted_to_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            capture_root = root / "telemetry-traces" / "live-replay"

            def write_capture(name: str, session_time: float, pit_recorded: bool) -> None:
                capture = capture_root / name
                capture.mkdir(parents=True)
                car = {
                    "carIndex": 0,
                    "lapDistancePercent": session_time / 10.0,
                    "overallPosition": 1,
                }
                if pit_recorded:
                    car["onPitRoad"] = False
                chunk = {
                    "schemaVersion": 1,
                    "frames": [{
                        "capturedAt": f"2026-08-07T12:00:0{int(session_time)}Z",
                        "sessionTimeSeconds": session_time,
                        "sessionState": 4,
                        "sessionFlags": 4,
                        "cars": [car],
                    }],
                }
                chunk_path = capture / "chunk-000000.json"
                chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
                coverage = [
                    {"channel": channel, "recorded": True}
                    for channel in ("SessionTime", "SessionState", "CarIdxLapDistPct")
                ]
                coverage.append({
                    "channel": "CarIdxOnPitRoad",
                    "recorded": pit_recorded,
                    "unavailableReason": None if pit_recorded else "Pit-road state was absent.",
                })
                manifest = {
                    "schemaVersion": 1,
                    "status": "finalized",
                    "sessionKey": name,
                    "subsessionId": 456,
                    "sessionType": "Race",
                    "playerCarIndex": 0,
                    "coverage": coverage,
                    "participants": [{"carIndex": 0, "carNumber": "7"}],
                    "chunks": [{
                        "file": chunk_path.name,
                        "sha256": file_sha256(chunk_path),
                        "frameCount": 1,
                    }],
                }
                (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            write_capture("race-first", 0.0, True)
            write_capture("race-reconnect", 0.5, False)
            replay = store.live_replay_for_analysis({
                "identity": {"subsession_id": 456},
                "source": {"selection": {"sim_session_type": "Race"}},
            })

            self.assertIsNotNone(replay)
            self.assertEqual(replay["status"], "partial")
            coverage = {item["channel"]: item for item in replay["coverage"]}
            pit = coverage["CarIdxOnPitRoad"]
            self.assertEqual(pit["status"], "partial")
            self.assertEqual(pit["recorded_segment_count"], 1)
            self.assertEqual(pit["segment_count"], 2)
            self.assertEqual(pit["recorded_fraction"], 0.5)
            self.assertFalse(pit["all_segments_recorded"])
            self.assertEqual(coverage["CarIdxLapDistPct"]["status"], "recorded")
            self.assertEqual(replay["temporal_coverage"]["gap_count"], 0)

    def test_live_replay_time_gap_and_participant_coverage_are_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            capture_root = root / "telemetry-traces" / "live-replay"

            def write_capture(name: str, session_time: float, cars: list[dict]) -> None:
                capture = capture_root / name
                capture.mkdir(parents=True)
                chunk = {
                    "schemaVersion": 1,
                    "frames": [{
                        "capturedAt": "2026-08-07T12:00:00Z",
                        "sessionTimeSeconds": session_time,
                        "sessionState": 4,
                        "sessionFlags": 4,
                        "cars": cars,
                    }],
                }
                chunk_path = capture / "chunk-000000.json"
                chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
                manifest = {
                    "schemaVersion": 1,
                    "status": "finalized",
                    "sessionKey": name,
                    "subsessionId": 789,
                    "sessionType": "Race",
                    "playerCarIndex": 0,
                    "coverage": [
                        {"channel": channel, "recorded": True}
                        for channel in ("SessionTime", "SessionState", "CarIdxLapDistPct")
                    ],
                    "participants": [
                        {"carIndex": 0, "carNumber": "7"},
                        {"carIndex": 1, "carNumber": "12"},
                    ],
                    "chunks": [{
                        "file": chunk_path.name,
                        "sha256": file_sha256(chunk_path),
                        "frameCount": 1,
                    }],
                }
                (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            player = {"carIndex": 0, "lapDistancePercent": 0.1, "overallPosition": 1}
            rival = {"carIndex": 1, "lapDistancePercent": 0.05, "overallPosition": 2}
            write_capture("race-first", 0.0, [player])
            write_capture("race-reconnect", 10.0, [player, rival])
            replay = store.live_replay_for_analysis({
                "identity": {"subsession_id": 789},
                "source": {"selection": {"sim_session_type": "Race"}},
            })

            self.assertIsNotNone(replay)
            self.assertEqual(replay["status"], "partial")
            temporal = replay["temporal_coverage"]
            self.assertEqual(temporal["gap_count"], 1)
            self.assertEqual(temporal["recorded_frame_count"], 2)
            self.assertEqual(temporal["expected_frame_count"], 21)
            self.assertLess(temporal["recorded_fraction"], 0.1)
            required = {
                item["channel"]: item
                for item in replay["coverage"]
                if item["channel"] in {"SessionTime", "SessionState", "CarIdxLapDistPct"}
            }
            self.assertTrue(all(item["status"] == "partial" for item in required.values()))
            self.assertTrue(all(item["recorded_segment_count"] == 2 for item in required.values()))
            self.assertTrue(all(item["temporal_gap_count"] == 1 for item in required.values()))
            participant = {item["car_index"]: item for item in replay["participant_coverage"]}
            self.assertEqual(participant[0]["recorded_segment_count"], 2)
            self.assertEqual(participant[0]["recorded_frame_count"], 2)
            self.assertEqual(participant[0]["status"], "partial")
            self.assertEqual(participant[1]["recorded_segment_count"], 1)
            self.assertEqual(participant[1]["recorded_frame_count"], 1)
            self.assertEqual(participant[1]["status"], "partial")

    def test_garage61_reference_cache_is_context_scoped_and_preserves_declared_scope(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = ArchiveStore(Path(folder) / "portable")
            context = {
                "car_key": "NASCAR Truck",
                "track_key": "Iowa Speedway Oval",
                "setup_type": "fixed",
                "season_key": "2026 S3",
            }
            reference = {
                "comparison_scope": "own/team",
                "target": {"car": "NASCAR Truck", "track": "Iowa Speedway"},
                "representative_laps": [
                    {
                        "comparison_role": "representative",
                        "lap": {"id": "42", "driverName": "Recorded Driver"},
                        "telemetry": {"status": "cached"},
                    }
                ],
                "reference_comparisons": [],
                "comparison_quality": {"status": "scope_bounded"},
            }

            result = store.cache_garage61_target_laps(context, reference)
            payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "cached")
            self.assertEqual(result["count"], 1)
            self.assertEqual(payload["comparison_scope"], "own/team")
            self.assertEqual(payload["representative_laps"][0]["lap"]["id"], "42")
            self.assertEqual(payload["cache_key"], store.cache_key(context))
            self.assertTrue(Path(result["path"]).parent.as_posix().endswith(store.cache_key(context)))


if __name__ == "__main__":
    unittest.main()
