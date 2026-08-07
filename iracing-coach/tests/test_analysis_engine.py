from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analysis_engine import (  # noqa: E402
    TelemetryTable,
    _corner_phase_coaching,
    _corner_tire_age_summary,
    _phase_comparison,
    _race_grades,
    _runs,
    _vehicle_sideslip_degrees,
    analyze_telemetry,
    analyzer_bundle_sha256,
)
from reporting import render_report  # noqa: E402
from storage import ArchiveStore  # noqa: E402
from workflow import ANALYSIS_CHANNELS  # noqa: E402


def synthetic_telemetry(lap_count: int = 8) -> dict:
    samples_per_lap = 80
    count = samples_per_lap * lap_count
    channels: dict[str, list] = {
        "SessionTime": [],
        "Lap": [],
        "LapDistPct": [],
        "Speed": [],
        "Throttle": [],
        "Brake": [],
        "SteeringWheelAngle": [],
        "LatAccel": [],
        "LongAccel": [],
        "FuelLevel": [],
        "AirTemp": [], "TrackTempCrew": [],
        "WindVel": [], "WindDir": [],
        "RelativeHumidity": [], "FogLevel": [],
        "AirPressure": [], "AirDensity": [],
        "Precipitation": [], "WeatherDeclaredWet": [],
        "Skies": [], "TrackWetness": [],
        "SessionFlags": [],
        "OnPitRoad": [],
        "PlayerCarInPitStall": [],
        "PitstopActive": [],
        "PitSvFlags": [],
        "RaceLaps": [],
        "LFwearL": [], "LFwearM": [], "LFwearR": [],
        "RFwearL": [], "RFwearM": [], "RFwearR": [],
        "LRwearL": [], "LRwearM": [], "LRwearR": [],
        "RRwearL": [], "RRwearM": [], "RRwearR": [],
        "Lat": [], "Lon": [],
        "CFSRrideHeight": [],
    }
    for tire in ("LF", "RF", "LR", "RR"):
        channels[f"{tire}TiresUsed"] = []
        channels[f"{tire}rideHeight"] = []
        channels[f"{tire}SHshockDefl"] = []
        channels[f"{tire}SHshockVel"] = []
        channels[f"{tire}pressure"] = []
        channels[f"{tire}coldPressure"] = []
        for position in ("CL", "CM", "CR"):
            channels[f"{tire}temp{position}"] = []
    fuel = 60.0
    stop_start = samples_per_lap * 4 - 8
    stop_end = samples_per_lap * 4 + 12
    for index in range(count):
        lap = index // samples_per_lap + 1
        pct = (index % samples_per_lap) / samples_per_lap
        angle = 2.0 * 3.14159265 * pct
        in_corner = 0.12 <= pct <= 0.38 or 0.62 <= pct <= 0.88
        brake = 0.28 if (0.10 <= pct <= 0.18 or 0.60 <= pct <= 0.68) else 0.0
        speed = 73.0 - (8.0 if in_corner else 0.0) - brake * 8.0
        in_service = stop_start <= index <= stop_end
        if in_service and index == stop_start + 2:
            fuel += 18.0
        elif not in_service:
            fuel -= 0.009
        channels["SessionTime"].append(index / 20.0)
        channels["Lap"].append(lap)
        channels["LapDistPct"].append(pct)
        channels["Speed"].append(speed)
        channels["Throttle"].append(0.35 if brake else 0.92)
        channels["Brake"].append(brake)
        channels["SteeringWheelAngle"].append(0.17 if in_corner else 0.01)
        channels["LatAccel"].append(10.5 if in_corner else 0.8)
        channels["LongAccel"].append(-2.5 if brake else 0.4)
        channels["FuelLevel"].append(fuel)
        channels["AirTemp"].append(25.0 + lap * 0.1)
        channels["TrackTempCrew"].append(40.0 + lap * 0.2)
        channels["WindVel"].append(6.25)
        channels["WindDir"].append(3.14159265)
        channels["RelativeHumidity"].append(0.83)
        channels["FogLevel"].append(0.0)
        channels["AirPressure"].append(97700.0)
        channels["AirDensity"].append(1.13)
        channels["Precipitation"].append(0.0)
        channels["WeatherDeclaredWet"].append(False)
        channels["Skies"].append(1)
        channels["TrackWetness"].append(1)
        channels["SessionFlags"].append(0x4000 if lap == 7 else 0x0004)
        channels["OnPitRoad"].append(in_service)
        channels["PlayerCarInPitStall"].append(in_service)
        channels["PitstopActive"].append(in_service)
        channels["PitSvFlags"].append(0x1F if in_service else 0)
        channels["RaceLaps"].append(lap_count)
        post_stop = index > stop_end
        wear = {
            "LF": 0.90 if post_stop else 1.0,
            "RF": 0.78 if post_stop else 1.0,
            "LR": 0.93 if post_stop else 1.0,
            "RR": 0.87 if post_stop else 1.0,
        }
        for tire in ("LF", "RF", "LR", "RR"):
            channels[f"{tire}TiresUsed"].append(1 if index > stop_end else 0)
            for position in ("L", "M", "R"):
                channels[f"{tire}wear{position}"].append(wear[tire])
        wave = math_sin(angle)
        velocity_wave = math_cos(angle)
        channels["CFSRrideHeight"].append(0.0052 - brake * 0.002 + 0.0003 * wave)
        ride_height = {"LF": 0.090, "RF": 0.092, "LR": 0.151, "RR": 0.149}
        pressure_kpa = {"LF": 117.0, "RF": 193.0, "LR": 117.0, "RR": 193.0}
        temperature_c = {"LF": 82.0, "RF": 88.0, "LR": 79.0, "RR": 85.0}
        for tire_index, tire in enumerate(("LF", "RF", "LR", "RR")):
            channels[f"{tire}rideHeight"].append(
                ride_height[tire] + 0.0015 * wave + brake * (0.002 if tire[0] == "R" else -0.002)
            )
            channels[f"{tire}SHshockDefl"].append(
                0.030 + 0.003 * wave + tire_index * 0.0005
            )
            channels[f"{tire}SHshockVel"].append(
                (0.18 + tire_index * 0.01) * velocity_wave
            )
            channels[f"{tire}pressure"].append(pressure_kpa[tire] + 2.0 * pct)
            channels[f"{tire}coldPressure"].append(pressure_kpa[tire])
            for position_index, position in enumerate(("CL", "CM", "CR")):
                channels[f"{tire}temp{position}"].append(
                    temperature_c[tire] + position_index * 2.0 + 1.5 * pct
                )
        channels["Lat"].append(math_sin(angle))
        channels["Lon"].append(math_cos(angle))
    variables = []
    for name in channels:
        if "rideHeight" in name or "shockDefl" in name:
            unit = "m"
        elif "shockVel" in name:
            unit = "m/s"
        elif "pressure" in name or "Pressure" in name:
            unit = "kPa"
        elif "tempC" in name:
            unit = "C"
        else:
            continue
        variables.append({"name": name, "unit": unit})
    return {
        "channels": channels,
        "variables": variables,
        "metadata": {"sample_rate": 20},
        "session_info": {
            "WeekendInfo": {
                "SessionID": 44,
                "SubSessionID": 55,
                "SeriesID": 164,
                "SeasonID": 6358,
                "SeasonYear": 2026,
                "SeasonQuarter": 3,
                "EventType": "Race",
                "TrackID": 63,
                "TrackDisplayName": "Synthetic Speedway",
                "TrackConfigName": "Oval",
                "TrackType": "oval",
                "WeekendOptions": {"IsFixedSetup": 1},
            },
            "DriverInfo": {
                "DriverCarIdx": 0,
                "DriverSetupName": "fixed.sto",
                "Drivers": [{"CarIdx": 0, "CarID": 123, "CarScreenName": "NASCAR Truck", "CarPath": "truck"}],
            },
            "CarSetup": {"Chassis": {"Front": {"Cross Weight": "50.0 %"}}},
            "SplitTimeInfo": {"Sectors": [
                {"SectorNum": 0, "SectorStartPct": 0.0},
                {"SectorNum": 1, "SectorStartPct": 0.333333},
                {"SectorNum": 2, "SectorStartPct": 0.666667},
            ]},
            "SessionInfo": {"Sessions": [{"SessionType": "Race", "SessionLaps": str(lap_count), "SessionTrackRubberState": "moderately high usage"}]},
        },
    }


def math_sin(value: float) -> float:
    import math
    return math.sin(value)


def math_cos(value: float) -> float:
    import math
    return math.cos(value)


class AnalysisEngineTests(unittest.TestCase):
    def test_analyzer_bundle_fingerprint_is_deterministic_sha256(self) -> None:
        first = analyzer_bundle_sha256()
        self.assertEqual(first, analyzer_bundle_sha256())
        self.assertEqual(len(first), 64)
        int(first, 16)

    def test_corner_tire_age_uses_confirmed_age_and_complete_clean_laps(self) -> None:
        telemetry = synthetic_telemetry(lap_count=20)
        analysis = analyze_telemetry(telemetry)
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {
                        "segment": 1,
                        "start_pct": 0.10,
                        "end_pct": 0.40,
                        "wraps_start_finish": False,
                    },
                    {
                        "segment": 2,
                        "start_pct": 0.20,
                        "end_pct": 0.35,
                        "wraps_start_finish": False,
                    },
                ]
            },
        )

        self.assertEqual(summary["status"], "usable")
        self.assertEqual(summary["runs"][0]["phase_model"], "run_thirds_proxy")
        run = summary["runs"][1]
        self.assertEqual(run["phase_model"], "confirmed_age_run_thirds_proxy")
        self.assertTrue(run["new_set_confirmed"])
        self.assertNotIn(7, run["eligible_lap_numbers"])
        self.assertNotIn(8, run["eligible_lap_numbers"])
        self.assertEqual(run["excluded_lap_counts"]["caution_or_mixed"], 1)
        self.assertEqual(run["excluded_lap_counts"]["restart"], 1)

        zone = run["zones"][0]
        tire_state_phases = {
            item["phase"]: item for item in zone["tire_age_phases"]
        }
        self.assertTrue(
            all(item["status"] == "unavailable" for item in tire_state_phases.values())
        )
        phases = {
            item["phase"]: item for item in zone["observational_run_phases"]
        }
        self.assertGreater(
            phases["late"]["green_lap_on_set_bounds"]["by_tire"]["RF"]["start"],
            phases["middle"]["green_lap_on_set_bounds"]["by_tire"]["RF"]["start"],
        )
        metrics = phases["middle"]["metrics"]
        for name in (
            "entry_speed_mph",
            "brake_peak_fraction",
            "brake_energy_proxy",
            "brake_onset_lap_pct",
            "brake_release_lap_pct",
            "minimum_speed_mph",
            "steering_average_abs_rad",
            "steering_work_proxy",
            "steering_corrections",
            "turn_in_lap_pct",
            "throttle_pickup_lap_pct",
            "exit_throttle_fraction",
            "exit_speed_mph",
        ):
            self.assertIn(name, metrics)
        self.assertGreater(
            phases["middle"]["event_availability"]["turn_in_laps"], 0
        )
        self.assertEqual(
            phases["middle"]["event_availability"]["turn_in_boundary_censored_laps"],
            0,
        )
        self.assertEqual(zone["comparisons"][-1]["status"], "usable")
        self.assertFalse(zone["coaching"]["exact_target_emitted"])
        censored_phases = {
            item["phase"]: item
            for item in run["zones"][1]["observational_run_phases"]
        }
        self.assertIsNone(censored_phases["middle"]["metrics"]["turn_in_lap_pct"])
        self.assertGreater(
            censored_phases["middle"]["event_availability"][
                "turn_in_boundary_censored_laps"
            ],
            0,
        )
        self.assertGreater(
            censored_phases["middle"]["event_availability"][
                "brake_onset_boundary_censored_laps"
            ],
            0,
        )

        analysis["corner_tire_age"] = summary
        report = render_report(analysis)
        self.assertIn("## Corner behavior by tire-age phase", report)
        self.assertIn("Load zone 1 (provisional)", report)
        self.assertIn("Turn/brake/throttle timing", report)
        self.assertIn("corrections", report)
        self.assertIn("brake onset boundary-censored", report)
        self.assertNotIn("Exact target unavailable", report)
        self.assertIn("older-set/late-run proxy", report)
        self.assertNotIn("Fresh/settled/worn boundaries", report)
        sourced_report = render_report(
            analysis,
            knowledge={
                "facts": {
                    "corner_targets": [
                        {
                            "segment": 1,
                            "corner": "Turn 1",
                            "corner_name_status": "sourced_official",
                        }
                    ]
                }
            },
        )
        self.assertIn("Turn 1", sourced_report)
        self.assertNotIn("Load zone 1 (provisional)", sourced_report)

    def test_turn_in_uses_prezone_baseline_with_oval_steering_bias(self) -> None:
        telemetry = synthetic_telemetry()
        telemetry["channels"]["SteeringWheelAngle"] = [
            value + 0.10 for value in telemetry["channels"]["SteeringWheelAngle"]
        ]
        analysis = analyze_telemetry(telemetry)
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {
                        "segment": 1,
                        "start_pct": 0.10,
                        "end_pct": 0.40,
                        "wraps_start_finish": False,
                    }
                ]
            },
        )
        phases = {
            item["phase"]: item
            for item in summary["runs"][1]["zones"][0][
                "observational_run_phases"
            ]
        }
        self.assertGreater(
            phases["early"]["event_availability"]["turn_in_laps"], 0
        )
        self.assertEqual(
            phases["early"]["event_availability"][
                "turn_in_boundary_censored_laps"
            ],
            0,
        )

    def test_short_unknown_age_run_uses_proxy_and_leaves_worn_unavailable(self) -> None:
        telemetry = synthetic_telemetry()
        last_lap = max(telemetry["channels"]["Lap"])
        telemetry["channels"]["LapDistPct"] = [
            0.5 if lap == last_lap else pct
            for lap, pct in zip(
                telemetry["channels"]["Lap"], telemetry["channels"]["LapDistPct"]
            )
        ]
        analysis = analyze_telemetry(telemetry)
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {"segment": 1, "start_pct": 0.10, "end_pct": 0.40}
                ]
            },
        )

        first_run = summary["runs"][0]
        self.assertEqual(first_run["phase_model"], "run_thirds_proxy")
        self.assertEqual(
            first_run["tire_age_phase_availability"]["worn"],
            "unavailable_without_session_derived_change_point",
        )
        zone = first_run["zones"][0]
        worn = next(
            item for item in zone["tire_age_phases"] if item["phase"] == "worn"
        )
        self.assertEqual(worn["status"], "unavailable")
        self.assertEqual(
            [item["phase"] for item in zone["observational_run_phases"]],
            ["early", "middle", "late"],
        )
        self.assertGreaterEqual(summary["eligibility"]["excluded_lap_counts"]["partial"], 1)

        analysis["corner_tire_age"] = summary
        report = render_report(analysis)
        self.assertIn("Late (late-run proxy)", report)
        self.assertIn("never a measured worn-tread state", report)
        self.assertNotIn("unavailable", report.lower())

    def test_initial_tire_age_requires_near_zero_odometers_and_later_resets(self) -> None:
        telemetry = synthetic_telemetry(lap_count=20)
        count = len(telemetry["channels"]["SessionTime"])
        stop_end = 80 * 4 + 12
        odometer = [
            index * 10.0
            if index <= stop_end
            else (index - stop_end - 1) * 10.0
            for index in range(count)
        ]
        for tire in ("LF", "RF", "LR", "RR"):
            telemetry["channels"][f"{tire}odometer"] = list(odometer)

        analysis = analyze_telemetry(telemetry)
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {"segment": 1, "start_pct": 0.10, "end_pct": 0.40}
                ]
            },
        )

        first = summary["runs"][0]
        evidence = first["tire_age_basis"]["initial_zero_evidence"]
        self.assertEqual(evidence["status"], "confirmed")
        self.assertTrue(all(evidence["later_reset_semantics_validated"].values()))
        self.assertEqual(first["phase_model"], "confirmed_age_run_thirds_proxy")
        self.assertTrue(first["new_set_confirmed"])

    def test_corner_phases_exclude_offtrack_and_close_traffic_laps(self) -> None:
        telemetry = synthetic_telemetry(lap_count=20)
        count = len(telemetry["channels"]["Lap"])
        telemetry["channels"]["PlayerTrackSurface"] = [3] * count
        telemetry["channels"]["CarDistAhead"] = [1000.0] * count
        telemetry["channels"]["CarDistBehind"] = [1000.0] * count
        for index, lap in enumerate(telemetry["channels"]["Lap"]):
            if lap == 10:
                telemetry["channels"]["PlayerTrackSurface"][index] = 0
            if lap == 11:
                telemetry["channels"]["CarDistAhead"][index] = 10.0

        analysis = analyze_telemetry(telemetry)
        run = analysis["runs"][1]
        self.assertNotIn(10, run["valid_green_lap_numbers"])
        self.assertNotIn(11, run["valid_green_lap_numbers"])
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {"segment": 1, "start_pct": 0.10, "end_pct": 0.40}
                ]
            },
        )
        exclusions = summary["runs"][1]["excluded_lap_counts"]
        self.assertEqual(exclusions["off_track"], 1)
        self.assertEqual(exclusions["close_traffic"], 1)
        self.assertTrue(summary["eligibility"]["screening"]["track_location_available"])
        self.assertTrue(summary["eligibility"]["screening"]["traffic_distance_available"])

    def test_flat_throttle_is_boundary_censored_not_false_pickup(self) -> None:
        telemetry = synthetic_telemetry(lap_count=20)
        telemetry["channels"]["Throttle"] = [0.92] * len(
            telemetry["channels"]["Throttle"]
        )
        analysis = analyze_telemetry(telemetry)
        summary = _corner_tire_age_summary(
            TelemetryTable(telemetry),
            analysis["laps"],
            analysis["runs"],
            {
                "detected_corner_segments": [
                    {"segment": 1, "start_pct": 0.10, "end_pct": 0.40}
                ]
            },
        )
        phase = summary["runs"][1]["zones"][0]["observational_run_phases"][1]
        self.assertIsNone(phase["metrics"]["throttle_pickup_lap_pct"])
        self.assertGreater(
            phase["event_availability"][
                "throttle_pickup_boundary_censored_laps"
            ],
            0,
        )

    def test_coaching_requires_two_observations_for_timing_metric(self) -> None:
        baseline = {
            "phase": "settled",
            "status": "usable",
            "metrics": {
                "entry_speed_mph": 100.0,
                "brake_release_offset_lap_pct": 0.20,
            },
            "metric_observation_counts": {
                "entry_speed_mph": 2,
                "brake_release_offset_lap_pct": 1,
            },
        }
        worn = {
            "phase": "worn",
            "status": "usable",
            "metrics": {
                "entry_speed_mph": 100.0,
                "brake_release_offset_lap_pct": 0.22,
            },
            "metric_observation_counts": {
                "entry_speed_mph": 2,
                "brake_release_offset_lap_pct": 1,
            },
        }
        comparison = _phase_comparison(baseline, worn)
        self.assertEqual(
            comparison["metric_status"]["brake_release_offset_lap_pct"],
            "limited",
        )
        coaching = _corner_phase_coaching(comparison)
        self.assertNotIn("brake release moved", coaching["finding"].lower())

    def test_run_numbers_ignore_empty_service_boundaries(self) -> None:
        count = 90
        service_indices = set(range(10, 13)) | set(range(30, 33))
        service_indices |= set(range(50, 53)) | set(range(60, 63))
        table = TelemetryTable(
            {
                "channels": {
                    "SessionTime": list(range(count)),
                    "FuelLevel": [50.0 - index * 0.01 for index in range(count)],
                    "SessionFlags": [0x0004] * count,
                    "OnPitRoad": [index in service_indices for index in range(count)],
                    "PlayerCarInPitStall": [
                        index in service_indices for index in range(count)
                    ],
                    "PitstopActive": [
                        index in service_indices for index in range(count)
                    ],
                    "PitSvFlags": [0] * count,
                },
                "metadata": {"sample_rate": 1},
            }
        )

        def lap(number: int, start: int, end: int) -> dict:
            return {
                "lap": number,
                "start_index": start,
                "end_index": end,
                "start_time": float(start),
                "end_time": float(end),
                "complete": True,
                "flag_state": "green",
                "green_fraction": 1.0,
                "caution_fraction": 0.0,
                "racing_state_fraction": 1.0,
                "pit_time_s": 0.0,
                "lap_time_s": float(end - start + 1),
                "fuel": {"used_l": 1.0},
                "controls": {
                    "brake_energy_proxy": 1.0,
                    "steering_work_proxy": 1.0,
                },
                "position": {
                    "start": number,
                    "end": number,
                    "class_start": number,
                    "class_end": number,
                },
                "vehicle_dynamics": {},
            }

        # The fourth source boundary (indices 53-59) contains no complete lap.
        # The following lap must still be displayed and archived as run 4.
        laps = [
            lap(1, 0, 9),
            lap(2, 20, 29),
            lap(3, 40, 49),
            lap(4, 80, 89),
        ]
        runs = _runs(table, laps)

        self.assertEqual([run["run_number"] for run in runs], [1, 2, 3, 4])
        self.assertEqual([run["lap_numbers"] for run in runs], [[1], [2], [3], [4]])

        analysis = {
            "analysis_id": "empty-service-boundary-regression",
            "identity": {
                "season_year": 2026,
                "season_quarter": 3,
                "car_id": 123,
                "car_name": "NASCAR Truck",
                "track_id": 63,
                "track_name": "Synthetic Speedway",
                "track_config": "Oval",
                "is_fixed_setup": True,
            },
            "race_summary": {"recorded_laps": 4, "scheduled_laps": 4},
            "runs": runs,
        }
        report = render_report(analysis)
        self.assertIn("| 4 | 4 (1) |", report)
        self.assertNotIn("| 5 |", report)

        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            context = store.context_from_analysis(analysis)
            store.record_analysis(analysis, str(Path(directory) / "report.md"))
            history = store.historical_runs(context)
        self.assertEqual([row["run_number"] for row in history], [1, 2, 3, 4])

    def test_builds_runs_tires_strategy_and_track_profile(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry(), source_paths=["synthetic.ibt"])
        self.assertEqual(analysis["identity"]["subsession_id"], 55)
        self.assertGreaterEqual(len(analysis["runs"]), 2)
        self.assertEqual(analysis["runs"][0]["tire_observation"]["lowest_remaining_tire"], "RF")
        self.assertTrue(
            analysis["runs"][0]["pit_service"]["requested_service"]["RF_tire_change_requested"]
        )
        self.assertIsNotNone(analysis["strategy"]["measured_green_fuel_l_per_lap"])
        self.assertTrue(analysis["track_profile"]["shape"])
        traces = analysis["lap_traces"]
        self.assertEqual(traces["schema_version"], 1)
        self.assertGreaterEqual(traces["trace_count"], 1)
        self.assertLessEqual(
            max(len(trace["points"]) for trace in traces["traces"]),
            traces["distance_bins_per_lap"],
        )
        first_point = traces["traces"][0]["points"][0]
        self.assertIn("speed_mph", first_point)
        self.assertIn("session_time_s", first_point)
        self.assertIn("brake", first_point)
        self.assertIn("slip_angle_deg", first_point)
        self.assertIsNone(first_point["slip_angle_deg"])
        self.assertIn("tire_stress_proxy", first_point)
        self.assertEqual(traces["tire_stress"]["evidence_class"], "proxy")
        self.assertIn("not per-lap tread wear", traces["tire_stress"]["definition"])
        self.assertEqual(traces["sector_start_pcts"], [0.0, 0.333333, 0.666667])
        self.assertIsNotNone(traces["traces"][0]["fuel_used_gal"])

        conditions = traces["traces"][0]["conditions"]
        self.assertEqual(conditions["sky"], "Partly cloudy")
        self.assertAlmostEqual(conditions["track_temperature_f"], 104.4, places=1)
        self.assertAlmostEqual(conditions["wind_speed_mph"], 14.0, places=1)
        self.assertEqual(conditions["relative_humidity_percent"], 83.0)
        self.assertEqual(conditions["track_usage"], "moderately high usage")
        timeline = analysis["race_timeline"]
        event_types = [event["event_type"] for event in timeline["events"]]
        self.assertEqual(event_types[0], "race_start")
        self.assertIn("caution_period", event_types)
        self.assertIn("pit_service", event_types)
        self.assertEqual(event_types[-1], "race_end")
        service = next(
            event for event in timeline["events"] if event["event_type"] == "pit_service"
        )
        self.assertEqual(service["confirmation"], "confirmed_consumable_service")
        self.assertIn("RF", service["requested_tires"])
        self.assertIn("native IBT", timeline["index_semantics"])
        forecast = analysis["strategy"]["forecast"]
        self.assertEqual(forecast["status"], "usable")
        self.assertGreater(forecast["all_green_range_laps"], 0)
        self.assertIn("not an optimal", forecast["classification"])
        grades = analysis["race_grades"]
        self.assertEqual(grades["status"], "graded")
        self.assertIn(grades["overall_grade"], {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"})
        self.assertGreaterEqual(len(grades["categories"]), 2)
        self.assertTrue(all(item["explanation"] and item["improvement"] for item in grades["categories"]))
        self.assertTrue(any("capped below A+" in item.get("limitations", "") for item in grades["categories"] if item["key"] == "pace"))

    def test_tire_temperature_respects_source_unit(self) -> None:
        telemetry = synthetic_telemetry()
        telemetry["channels"]["LFtempCL"] = [212.0] * len(
            telemetry["channels"]["LFtempCL"]
        )
        for variable in telemetry["variables"]:
            if variable["name"] == "LFtempCL":
                variable["unit"] = "F"

        analysis = analyze_telemetry(telemetry, source_paths=["synthetic.ibt"])

        self.assertEqual(
            analysis["analysis_profile_version"],
            "post-race-damage-repair-corner-phase-v9",
        )
        observed = analysis["runs"][0]["tire_observation"]["tires"]["LF"]
        self.assertEqual(observed["carcass_temperature_f"]["CL"], 212.0)

    def test_race_grades_exclude_damage_confounded_run_slopes(self) -> None:
        laps = [
            {
                "lap": number,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
                "lap_time_s": 60.0 + (number % 3) * 0.1,
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": True,
                    "exclusion_reason_codes": [],
                },
            }
            for number in range(1, 13)
        ]
        # Even an explicitly eligible flag cannot override an exclusion reason.
        laps.append({
            **laps[-1],
            "lap": 13,
            "lap_time_s": 10.0,
            "damage_repair_context": {
                "automatic_coaching_reference_eligible": True,
                "exclusion_reason_codes": ["repair_correlated_candidate"],
            },
        })
        runs = [
            {
                "pace": {"green_lap_time_slope_s_per_lap": 0.2},
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": True,
                    "reason_codes": [],
                },
            },
            {
                "pace": {"green_lap_time_slope_s_per_lap": -1.5},
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": False,
                    "reason_codes": [],
                },
            },
            {
                "pace": {"green_lap_time_slope_s_per_lap": -2.0},
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": True,
                    "reason_codes": ["manual_review_after_tow_or_repair"],
                },
            },
            {
                "pace": {"green_lap_time_slope_s_per_lap": -3.0},
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": True,
                    "reason_codes": "malformed_but_nonempty_reason",
                },
            },
        ]

        result = _race_grades(laps, runs, {}, {})
        categories = {item["key"]: item for item in result["categories"]}

        self.assertIn("across 12 usable laps", categories["pace"]["explanation"])
        self.assertIn("+0.200 s per lap across 1 eligible run(s)", categories["tire_management"]["explanation"])
        self.assertEqual(categories["tire_management"]["score"], 83.0)

    def test_race_grades_exclude_restart_non_racing_off_track_and_traffic_laps(self) -> None:
        def lap(number: int, time: float, **extra: object) -> dict:
            result = {
                "lap": number,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
                "lap_time_s": time,
                "racing_state_fraction": 1.0,
                "clean_context": {
                    "on_track_fraction": 1.0,
                    "traffic_proximity_fraction": 0.0,
                },
                "damage_repair_context": {
                    "automatic_coaching_reference_eligible": True,
                    "exclusion_reason_codes": [],
                },
            }
            result.update(extra)
            return result

        laps = [
            lap(1, 200.0, flag_state="caution"),
            lap(2, 10.0),  # Restart lap after caution.
            lap(3, 60.0),
            lap(4, 11.0, racing_state_fraction=0.5),
            lap(
                5,
                12.0,
                clean_context={
                    "on_track_fraction": 0.5,
                    "traffic_proximity_fraction": 0.0,
                },
            ),
            lap(
                6,
                13.0,
                clean_context={
                    "on_track_fraction": 1.0,
                    "traffic_proximity_fraction": 0.2,
                },
            ),
            lap(7, 60.1),
            lap(8, 60.2),
            lap(
                9,
                9.0,
                damage_repair_context={
                    "automatic_coaching_reference_eligible": True,
                    "exclusion_reason_codes": ["repair_correlated_candidate"],
                },
            ),
        ]

        result = _race_grades(laps, [], {}, {})
        categories = {item["key"]: item for item in result["categories"]}

        self.assertIn("across 3 usable laps", categories["pace"]["explanation"])
        self.assertIn("across 3 usable laps", categories["consistency"]["explanation"])

    def test_race_grades_leave_strategy_and_racecraft_unavailable(self) -> None:
        laps = [
            {
                "lap": number,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
                "lap_time_s": 60.0 + number * 0.02,
            }
            for number in range(1, 13)
        ]
        race_summary = {
            "starting_position": 30,
            "final_recorded_position": 1,
            "pit_stops_detected": 4,
        }
        damage_repair = {
            "summary": {"tow_episodes": 3, "recorded_repair_episodes": 2},
        }

        result = _race_grades(laps, [], race_summary, damage_repair)
        available = {item["key"] for item in result["categories"]}
        unavailable = {item["key"]: item for item in result["unavailable_categories"]}

        self.assertNotIn("strategy", available)
        self.assertNotIn("racecraft", available)
        self.assertIn("strategy", unavailable)
        self.assertIn("racecraft", unavailable)
        self.assertIn("do not establish", unavailable["strategy"]["reason"])
        self.assertIn("do not establish", unavailable["racecraft"]["reason"])

    def test_race_grades_normalize_versioned_weights_across_available_categories(self) -> None:
        laps = [
            {
                "lap": number,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
                "lap_time_s": 60.0 + (number % 4) * 0.4,
            }
            for number in range(1, 13)
        ]

        result = _race_grades(laps, [], {}, {})
        categories = {item["key"]: item for item in result["categories"]}
        rubric = result["rubric"]

        self.assertEqual(result["rubric_version"], "race-execution-v2")
        self.assertEqual(
            rubric["category_weights_percent"],
            {"pace": 30, "consistency": 20, "tire_management": 20, "strategy": 15, "racecraft": 15},
        )
        self.assertEqual(rubric["available_weight_percent"], 50)
        self.assertEqual(rubric["normalized_available_weights"], {"pace": 0.6, "consistency": 0.4})
        expected = categories["pace"]["score"] * 0.6 + categories["consistency"]["score"] * 0.4
        self.assertAlmostEqual(result["overall_score"], round(expected, 1), places=1)

    def test_race_grades_explicitly_gate_a_plus_without_external_cohort(self) -> None:
        laps = [
            {
                "lap": number,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
                "lap_time_s": 60.0,
            }
            for number in range(1, 13)
        ]
        runs = [{"pace": {"green_lap_time_slope_s_per_lap": -1.0}}]

        result = _race_grades(laps, runs, {}, {})

        self.assertFalse(result["rubric"]["a_plus_gate"]["eligible"])
        self.assertEqual(
            result["rubric"]["a_plus_gate"]["status"],
            "blocked_missing_external_comparable_field_strength",
        )
        self.assertNotEqual(result["overall_grade"], "A+")
        self.assertTrue(all(item["grade"] != "A+" for item in result["categories"]))
        self.assertTrue(any(
            gate["gate"] == "external_comparable_field_strength_required_for_A_plus"
            for gate in result["applied_gates"]
        ))

    def test_lap_traces_preserve_mixed_flags_pit_direction_and_fuel(self) -> None:
        telemetry = synthetic_telemetry(lap_count=3)
        telemetry["session_info"]["SessionInfo"]["Sessions"] = [
            {"SessionType": "Practice", "SessionTrackRubberState": "moderately high usage"},
            {"SessionType": "Race", "SessionLaps": "3", "SessionTrackRubberState": "carry over"},
        ]
        flags = telemetry["channels"]["SessionFlags"]
        on_pit = telemetry["channels"]["OnPitRoad"]
        for index in range(80, 120):
            flags[index] = 0x0004
        for index in range(120, 160):
            flags[index] = 0x4000
        flags[100] |= 0x00010000
        flags[101] |= 0x0002
        flags[102] |= 0x0001
        for index in range(145, 171):
            on_pit[index] = True

        analysis = analyze_telemetry(telemetry, source_paths=["flags.ibt"])
        traces = {item["lap"]: item for item in analysis["lap_traces"]["traces"]}

        self.assertEqual(
            traces[2]["flag_states"],
            ["green", "yellow", "black", "white", "checkered"],
        )
        self.assertTrue(traces[2]["pit_entry"])
        self.assertTrue(traces[3]["pit_exit"])
        self.assertGreater(traces[2]["fuel_used_gal"], 0)
        self.assertEqual(traces[2]["conditions"]["track_usage"], "moderately high usage")

    def test_lap_traces_derive_vehicle_sideslip_from_paired_velocity_channels(self) -> None:
        self.assertAlmostEqual(
            _vehicle_sideslip_degrees(50.0, 50.0 * math.tan(math.radians(4.0))),
            4.0,
            places=6,
        )
        self.assertIsNone(_vehicle_sideslip_degrees(3.0, 0.0))
        self.assertIsNone(_vehicle_sideslip_degrees(-20.0, 0.0))
        self.assertIsNone(_vehicle_sideslip_degrees(float("nan"), 1.0))

        telemetry = synthetic_telemetry(lap_count=2)
        speed = telemetry["channels"]["Speed"]
        sideslip_radians = math.radians(-4.0)
        telemetry["channels"]["VelocityX"] = [
            value * math.cos(sideslip_radians) for value in speed
        ]
        telemetry["channels"]["VelocityY"] = [
            value * math.sin(sideslip_radians) for value in speed
        ]
        telemetry["channels"]["VelocityX"][10] = 3.0
        telemetry["channels"]["VelocityY"][10] = 0.0
        telemetry["channels"]["VelocityX"][11] = -20.0
        telemetry["channels"]["VelocityY"][11] = 0.0

        analysis = analyze_telemetry(telemetry, source_paths=["sideslip.ibt"])
        points = analysis["lap_traces"]["traces"][0]["points"]
        available = [point["slip_angle_deg"] for point in points if point["slip_angle_deg"] is not None]
        gaps = [point for point in points if point["slip_angle_deg"] is None]

        self.assertTrue(available)
        self.assertTrue(all(abs(value + 4.0) < 0.001 for value in available))
        self.assertGreaterEqual(len(gaps), 2)

    def test_summarizes_setup_telemetry_in_engineering_units(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        setup = analysis["setup_telemetry"]
        self.assertIn("CFSRrideHeight", setup["available_channels"])

        platform = setup["platform"]
        splitter = platform["center_front_splitter"]
        self.assertGreater(splitter["min_in"], 0.1)
        self.assertLess(splitter["max_in"], 0.3)
        self.assertEqual(platform["center_front_splitter_min_in"], splitter["min_in"])
        self.assertAlmostEqual(platform["dynamic_rear"]["median_in"], 5.91, delta=0.1)
        self.assertEqual(set(platform["corners"]), {"LF", "RF", "LR", "RR"})

        lf_shock = setup["shocks"]["LF"]
        self.assertGreater(lf_shock["abs_velocity_p90_in_s"], 0.0)
        self.assertGreater(lf_shock["abs_velocity_p99_in_s"], lf_shock["abs_velocity_p90_in_s"])
        self.assertGreater(lf_shock["deflection_range_in"], 0.0)

        lf_tire = setup["tires"]["LF"]
        self.assertAlmostEqual(lf_tire["live_pressure"]["median_psi"], 17.1, delta=0.2)
        self.assertGreater(lf_tire["temperatures"]["CL"]["median_f"], 175.0)
        self.assertIn("carcass_average", lf_tire)

        profile = analysis["track_profile"]
        self.assertIn("center_front_splitter_min_in", profile["setup_trace_fields"])
        self.assertIn("lf_shock_abs_velocity_p99_in_s", profile["setup_trace_fields"])
        self.assertIn("lf_tire_pressure_median_psi", profile["setup_trace_fields"])
        self.assertTrue(
            any("center_front_splitter_min_in" in item for item in profile["profile"])
        )
        limits = " ".join(setup["limits"]).lower()
        self.assertIn("caused", limits)
        self.assertIn("a/b", limits)
        self.assertIn("not universal setup targets", limits)

    def test_supports_short_and_full_word_shock_aliases(self) -> None:
        telemetry = synthetic_telemetry()
        channels = telemetry["channels"]
        channels["LFshockDefl"] = channels.pop("LFSHshockDefl")
        channels["LFShockVelocity"] = channels.pop("LFSHshockVel")
        analysis = analyze_telemetry(telemetry)
        shock = analysis["setup_telemetry"]["shocks"]["LF"]
        self.assertEqual(shock["deflection"]["channel"], "LFshockDefl")
        self.assertEqual(shock["velocity"]["channel"], "LFShockVelocity")

    def test_workflow_requests_setup_tuning_channels(self) -> None:
        for channel in (
            "CFSRrideHeight",
            "LFrideHeight",
            "LFSHshockDefl",
            "LFSHshockVel",
            "LFshockDeflection",
            "LFshockVelocity",
            "LFpressure",
            "LFtempCL",
        ):
            self.assertIn(channel, ANALYSIS_CHANNELS)

    def test_archive_is_season_scoped_and_records_history(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            context = store.context_from_analysis(analysis)
            self.assertEqual(context["season_key"], "2026s3")
            self.assertEqual(store.cache_status(context)["state"], "missing")
            store.write_knowledge_bundle(
                context,
                sources=[{"url": "https://example.test/manual", "title": "Manual"}],
                facts={"track": "Synthetic Speedway"},
            )
            self.assertEqual(store.cache_status(context)["state"], "fresh")
            artifacts = store.save_report_artifacts(analysis, "# Test report")
            store.record_analysis(analysis, artifacts["report"])
            self.assertGreaterEqual(len(store.historical_runs(context)), 1)

    def test_green_running_does_not_require_transient_green_flag_bit(self) -> None:
        telemetry = synthetic_telemetry()
        telemetry["channels"]["SessionFlags"] = [
            0x4000 if lap == 7 else 0x10040000
            for lap in telemetry["channels"]["Lap"]
        ]
        analysis = analyze_telemetry(telemetry)
        self.assertEqual(analysis["race_summary"]["recorded_laps"], 8)
        self.assertGreater(analysis["race_summary"]["green_laps_estimated"], 6)
        self.assertEqual(analysis["race_summary"]["caution_laps_estimated"], 1)

    def test_unchanged_wear_is_not_attributed_to_the_preceding_run(self) -> None:
        telemetry = synthetic_telemetry()
        for tire in ("LF", "RF", "LR", "RR"):
            for position in ("L", "M", "R"):
                telemetry["channels"][f"{tire}wear{position}"] = [1.0] * len(
                    telemetry["channels"]["SessionTime"]
                )
        analysis = analyze_telemetry(telemetry)
        self.assertIsNone(analysis["runs"][0]["tire_observation"])
        self.assertEqual(
            analysis["runs"][0]["tire_measurement_status"],
            "stale_or_unconfirmed_at_stop",
        )

    def test_temperature_only_channels_do_not_become_tire_wear(self) -> None:
        telemetry = synthetic_telemetry()
        for tire in ("LF", "RF", "LR", "RR"):
            for position in ("L", "M", "R"):
                telemetry["channels"].pop(f"{tire}wear{position}")
            for position in ("CL", "CM", "CR"):
                telemetry["channels"][f"{tire}temp{position}"] = [80.0] * len(
                    telemetry["channels"]["SessionTime"]
                )
        analysis = analyze_telemetry(telemetry)
        self.assertIsNone(analysis["runs"][0]["tire_observation"])
        self.assertEqual(
            analysis["runs"][0]["tire_measurement_status"],
            "unavailable_at_stop",
        )


if __name__ == "__main__":
    unittest.main()
