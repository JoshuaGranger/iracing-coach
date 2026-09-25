"""Test day: garage-setup identity, stint grouping, and the one-screen review."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis_engine  # noqa: E402
import ibt_reader  # noqa: E402
import setup_catalog  # noqa: E402
import test_day  # noqa: E402
import tuning_engine  # noqa: E402
import tuning_workflow  # noqa: E402
from test_ibt_reader import build_ibt  # noqa: E402

HOUR = 3600.0
DAY_START = 1_785_808_781.0  # 2026-08-04 01:59:41 UTC, a real offline Iowa stint.


def car_setup(*, cross_weight: str = "50.0%", brake_bias: str = "56.0%", update_count: int = 3,
              lf_temps: str = "80C, 64C, 41C", rf_temps: str = "107C, 99C, 87C",
              lf_tread: str = "97%, 98%, 100%", rf_tread: str = "95%, 95%, 98%",
              lf_hot: str = "139 kPa", rf_hot: str = "231 kPa") -> dict:
    """An O'Reilly-shaped embedded CarSetup with a real sheet's tire readings."""

    return {
        "UpdateCount": update_count,
        "Tires": {
            "LeftFront": {"ColdPressure": "117 kPa", "LastHotPressure": lf_hot,
                          "LastTempsOMI": lf_temps, "TreadRemaining": lf_tread},
            "RightFront": {"ColdPressure": "193 kPa", "LastHotPressure": rf_hot,
                           "LastTempsIMO": rf_temps, "TreadRemaining": rf_tread},
            "LeftRear": {"ColdPressure": "117 kPa", "LastHotPressure": "146 kPa",
                         "LastTempsOMI": "79C, 76C, 70C", "TreadRemaining": "99%, 99%, 100%"},
            "RightRear": {"ColdPressure": "193 kPa", "LastHotPressure": "219 kPa",
                          "LastTempsIMO": "74C, 72C, 69C", "TreadRemaining": "99%, 99%, 99%"},
        },
        "Chassis": {
            "Front": {"BallastForward": "711 mm", "CrossWeight": cross_weight, "FrontBrakeBias": brake_bias, "NoseWeight": "51.0%"},
            "LeftFront": {"CornerWeight": "3928 N", "Packer": "0.25 in", "RideHeight": "4.25 in"},
            "RightFront": {"CornerWeight": "4109 N", "Packer": "0.25 in", "RideHeight": "4.30 in"},
            "LeftRear": {"CornerWeight": "4224 N", "RideHeight": "141 mm", "SpringPerchOffset": "-72 mm", "TrackBarHeight": "10.00 in"},
            "RightRear": {"CornerWeight": "3747 N", "TrackBarHeight": "10.00 in", "TruckArmPreload": "5.6 Nm"},
        },
    }


def wedge(setup: dict, *, lr_perch: str = "-70 mm", cross_weight: str = "50.4%") -> dict:
    """One left-rear spring-perch adjustment and the readouts iRacing recomputes from it.

    Cross weight, the four corner weights and the left-rear ride height are
    garage readouts: a real sheet changes them together with the perch offset.
    """

    chassis = setup["Chassis"]
    chassis["LeftRear"]["SpringPerchOffset"] = lr_perch
    chassis["Front"]["CrossWeight"] = cross_weight
    chassis["LeftFront"]["CornerWeight"] = "3916 N"
    chassis["RightFront"]["CornerWeight"] = "4121 N"
    chassis["LeftRear"]["CornerWeight"] = "4236 N"
    chassis["RightRear"]["CornerWeight"] = "3735 N"
    chassis["LeftRear"]["RideHeight"] = "142 mm"
    return setup


class GarageSetupIdentityTests(unittest.TestCase):
    """`Also approved` 1: identical garage setups are recognized as one."""

    def test_tire_readings_and_update_count_are_not_garage_settings(self) -> None:
        first = car_setup()
        repeat = car_setup(update_count=9, lf_temps="60C, 48C, 38C", rf_temps="87C, 80C, 72C",
                           lf_tread="99%, 100%, 100%", rf_tread="99%, 99%, 99%", lf_hot="133 kPa", rf_hot="219 kPa")
        comparison = setup_catalog.compare_setups(setup_catalog.garage_setup_fields(first),
                                                  setup_catalog.garage_setup_fields(repeat))
        self.assertEqual(comparison["summary"]["different_fields"], 0)
        self.assertEqual(comparison["summary"]["only_left_fields"], 0)
        self.assertEqual(comparison["summary"]["only_right_fields"], 0)
        fields = setup_catalog.garage_setup_fields(first)
        self.assertIn("tires.left_front.cold_pressure", fields["fields"])
        self.assertNotIn("tires.left_front.last_temps_omi", fields["fields"])
        self.assertIn("tires.right_front.last_temps_imo", fields["excluded_fields"])
        self.assertTrue(all(not setup_catalog.is_transient_setup_field(key) for key in fields["fields"]))
        self.assertIn("update_count", setup_catalog.TRANSIENT_FIELD_TERMS)

    def test_a_real_setting_change_is_the_only_difference(self) -> None:
        comparison = setup_catalog.compare_setups(
            setup_catalog.garage_setup_fields(car_setup(update_count=4)),
            setup_catalog.garage_setup_fields(car_setup(cross_weight="50.4%", lf_temps="61C, 50C, 40C", update_count=7)))
        self.assertEqual([item["field"] for item in comparison["differences"]], ["chassis.front.cross_weight"])

    def test_garage_fingerprint_ignores_readings_but_not_settings(self) -> None:
        first = car_setup()
        repeat = car_setup(update_count=12, lf_temps="60C, 48C, 38C", rf_tread="90%, 91%, 93%")
        changed = car_setup(brake_bias="56.5%")
        self.assertEqual(setup_catalog.garage_setup_fingerprint(first), setup_catalog.garage_setup_fingerprint(repeat))
        self.assertNotEqual(setup_catalog.garage_setup_fingerprint(first), setup_catalog.garage_setup_fingerprint(changed))
        self.assertIsNone(setup_catalog.garage_setup_fingerprint({}))
        self.assertIsNone(setup_catalog.garage_setup_fingerprint(None))
        self.assertIsNone(setup_catalog.garage_setup_fingerprint({"UpdateCount": 3}))
        self.assertEqual(tuning_engine.embedded_garage_setup_fingerprint(first),
                         setup_catalog.garage_setup_fingerprint(first))
        # The whole-sheet fingerprint keeps its meaning: it is what recorded
        # tuning evidence is verified against.
        self.assertNotEqual(tuning_engine.embedded_setup_fingerprint(first),
                            tuning_engine.embedded_setup_fingerprint(repeat))

    def test_analysis_identity_carries_both_fingerprints(self) -> None:
        def identity(setup: dict) -> dict:
            table = analysis_engine.TelemetryTable({"channels": {"SessionTime": [0.0, 1.0]},
                                                    "session_info": {"CarSetup": setup}})
            return analysis_engine._identity(table)

        first, repeat = identity(car_setup()), identity(car_setup(update_count=8, lf_temps="60C, 48C, 38C"))
        self.assertNotEqual(first["setup_fingerprint"], repeat["setup_fingerprint"])
        self.assertEqual(first["garage_setup_fingerprint"], repeat["garage_setup_fingerprint"])
        self.assertEqual(first["garage_setup_fingerprint"], setup_catalog.garage_setup_fingerprint(car_setup())[:16])
        self.assertEqual(len(first["setup_fingerprint"]), 16)
        self.assertIsNone(identity({})["garage_setup_fingerprint"])

    def test_computed_readouts_are_named_and_left_out_of_the_fingerprint(self) -> None:
        fields = setup_catalog.garage_setup_fields(car_setup())
        self.assertEqual(fields["readout_fields"], [
            "chassis.front.cross_weight", "chassis.front.nose_weight",
            "chassis.left_front.corner_weight", "chassis.left_front.ride_height",
            "chassis.left_rear.corner_weight", "chassis.left_rear.ride_height",
            "chassis.right_front.corner_weight", "chassis.right_front.ride_height",
            "chassis.right_rear.corner_weight", "chassis.right_rear.truck_arm_preload"])
        self.assertIn("chassis.front.cross_weight", fields["fields"])  # still shown, never counted
        self.assertFalse(setup_catalog.is_garage_readout_field("chassis.left_rear.spring_perch_offset"))
        # One .sto loaded on two days reads a unit apart (recorded New Hampshire sessions).
        drifted = car_setup()
        drifted["Chassis"]["LeftFront"]["CornerWeight"] = "3929 N"
        drifted["Chassis"]["RightRear"]["TruckArmPreload"] = "5.5 Nm"
        self.assertEqual(setup_catalog.garage_setup_fingerprint(car_setup()), setup_catalog.garage_setup_fingerprint(drifted))
        self.assertNotEqual(setup_catalog.garage_setup_fingerprint(car_setup()),
                            setup_catalog.garage_setup_fingerprint(wedge(car_setup())))

    def test_tuning_comparison_uses_the_shared_transient_list(self) -> None:
        snapshot = tuning_workflow._comparable_snapshot(setup_catalog.normalize_embedded_setup(car_setup()))
        self.assertTrue(snapshot["fields"])
        self.assertFalse([key for key in snapshot["fields"] if setup_catalog.is_transient_setup_field(key)])


class GarageSheetReadingTests(unittest.TestCase):
    def test_left_tires_read_outside_first_and_right_tires_inside_first(self) -> None:
        readings = setup_catalog.tire_readings_from_setup(car_setup())
        lf, rf = readings["LF"], readings["RF"]
        self.assertEqual(lf["band_order"], "OMI")
        self.assertEqual(rf["band_order"], "IMO")
        # 80C, 64C, 41C listed O-M-I: the outside is the hot edge.
        self.assertEqual(lf["temperature_f"], {"outside": 176.0, "middle": 147.2, "inside": 105.8})
        # 107C, 99C, 87C listed I-M-O: the inside is the hot edge.
        self.assertEqual(rf["temperature_f"], {"inside": 224.6, "middle": 210.2, "outside": 188.6})
        # Tread follows the same listing order as that corner's temperatures.
        self.assertEqual(lf["tread_percent"], {"outside": 97.0, "middle": 98.0, "inside": 100.0})
        self.assertEqual(rf["tread_percent"], {"inside": 95.0, "middle": 95.0, "outside": 98.0})
        self.assertAlmostEqual(lf["hot_pressure_psi"], 20.16, places=2)
        self.assertAlmostEqual(rf["cold_pressure_psi"], 27.99, places=2)
        self.assertEqual(lf["band_order_basis"], "sheet_label")
        self.assertTrue(all(key.startswith("tires.left_front.") for key in lf["source_fields"]))

    def test_missing_or_malformed_readings_are_absent_never_zero(self) -> None:
        setup = car_setup()
        del setup["Tires"]["LeftFront"]["LastTempsOMI"]
        setup["Tires"]["RightFront"]["TreadRemaining"] = "95%, 95%"
        del setup["Tires"]["LeftRear"]
        readings = setup_catalog.tire_readings_from_setup(setup)
        self.assertIsNone(readings["LF"]["temperature_f"])
        self.assertEqual(readings["LF"]["band_order_basis"], "corner_side")
        self.assertEqual(readings["LF"]["tread_percent"]["outside"], 97.0)
        self.assertIsNone(readings["RF"]["tread_percent"])
        self.assertIsNone(readings["LR"])
        fahrenheit = copy.deepcopy(car_setup())
        fahrenheit["Tires"]["LeftFront"]["LastTempsOMI"] = "176F, 147F, 106F"
        self.assertEqual(setup_catalog.tire_readings_from_setup(fahrenheit)["LF"]["temperature_f"],
                         {"outside": 176.0, "middle": 147.0, "inside": 106.0})


def stint(start: float, minutes: float = 10.0, *, kind: str = "Offline Testing", car: str = "stockcars2 supra2019",
          track_id: int | None = 559, config: str | None = "Oval", group_prefix: str = "local", time_source: str = "disk_header",
          is_race: bool = False) -> dict:
    return {
        "kind": "session", "valid": True, "is_race": is_race,
        "group_id": f"{group_prefix}:{start}:0:1:{track_id}:{config}:116:{car}",
        "sim_session_type": kind, "event_type": "Test" if kind == "Offline Testing" else kind,
        "track_id": track_id, "track_name": "Iowa Speedway", "track_config_name": config, "car_path": car,
        "start_time_unix": start, "end_time_unix": start + minutes * 60.0, "time_source": time_source,
    }


class TestDayGroupingTests(unittest.TestCase):
    """Backend 1: offline stints of one car and track within two hours form a day."""

    def test_same_car_and_track_within_two_hours_join_in_time_order(self) -> None:
        first = stint(DAY_START)
        second = stint(DAY_START + 1.5 * HOUR)            # 80 minutes after the first ended
        third = stint(DAY_START + 1.5 * HOUR + 10 * 60 + 2 * HOUR)  # exactly two hours after the second ended
        groups = [third, first, second]
        ibt_reader._assign_test_days(groups)
        self.assertEqual({group["test_day_id"] for group in groups}, {first["test_day_id"]})
        self.assertRegex(first["test_day_id"], r"^test-day-[0-9a-f]{16}$")
        self.assertEqual([first["test_day_run"], second["test_day_run"], third["test_day_run"]], [1, 2, 3])
        self.assertTrue(all(group["test_day_run_count"] == 3 for group in groups))
        # Stints are tagged, never merged: each keeps its own group identity.
        self.assertEqual(len({group["group_id"] for group in groups}), 3)
        # A later stint never renumbers or renames the earlier ones.
        again = [stint(DAY_START), stint(DAY_START + 1.5 * HOUR)]
        ibt_reader._assign_test_days(again)
        self.assertEqual(again[0]["test_day_id"], first["test_day_id"])
        self.assertEqual(again[1]["test_day_run"], 2)

    def test_a_gap_over_two_hours_starts_another_day(self) -> None:
        groups = [stint(DAY_START), stint(DAY_START + 10 * 60 + 2 * HOUR + 1), stint(DAY_START + 3 * HOUR)]
        ibt_reader._assign_test_days(groups)
        self.assertNotIn("test_day_id", groups[0])
        self.assertEqual(groups[1]["test_day_id"], groups[2]["test_day_id"])
        self.assertEqual([groups[1]["test_day_run"], groups[2]["test_day_run"]], [1, 2])

    def test_another_day_track_configuration_or_car_stays_separate(self) -> None:
        groups = [
            stint(DAY_START),
            stint(DAY_START + 86400.0),
            stint(DAY_START + 20 * 60, track_id=131),
            stint(DAY_START + 40 * 60, config="Road"),
            stint(DAY_START + 60 * 60, car="stockcars2 mustang2019"),
        ]
        ibt_reader._assign_test_days(groups)
        self.assertTrue(all("test_day_id" not in group for group in groups))

    def test_races_qualifying_event_practice_and_undated_sessions_are_never_tagged(self) -> None:
        groups = [
            stint(DAY_START),
            stint(DAY_START + 5 * 60, kind="Race", is_race=True, group_prefix="subsession"),
            stint(DAY_START + 10 * 60, kind="Lone Qualify"),
            stint(DAY_START + 15 * 60, kind="Practice", group_prefix="subsession"),
            stint(DAY_START + 20 * 60, time_source="mtime"),
            stint(DAY_START + 25 * 60, kind=None),
            stint(DAY_START + 30 * 60),
        ]
        # The practice belongs to the recorded race event (one subsession) and stays with it.
        groups[1]["subsession_id"] = groups[3]["subsession_id"] = 9001
        groups[5]["event_type"] = None
        ibt_reader._assign_test_days(groups)
        tagged = [index for index, group in enumerate(groups) if "test_day_id" in group]
        self.assertEqual(tagged, [0, 6])
        self.assertEqual([groups[0]["test_day_run"], groups[6]["test_day_run"]], [1, 2])

    def test_a_standalone_hosted_practice_is_a_test_day_recording(self) -> None:
        # Addendum 4: an official or hosted practice without a recorded race joins the day like an offline stint.
        practice = stint(DAY_START, kind="Practice", group_prefix="subsession")
        practice["subsession_id"] = 77001
        offline = stint(DAY_START + 70 * 60)
        ibt_reader._assign_test_days([practice, offline])
        self.assertEqual(practice["test_day_id"], offline["test_day_id"])
        self.assertEqual([practice["test_day_run"], offline["test_day_run"]], [1, 2])

    def test_discovery_tags_offline_stints_and_leaves_races_untouched(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = 1_780_000_000
            build_ibt(root / "stint-1.ibt", base_epoch=base, subsession_id=0, session_id=0, session_num=0)
            build_ibt(root / "stint-2.ibt", base_epoch=base + 1800, subsession_id=0, session_id=0, session_num=0)
            build_ibt(root / "later.ibt", base_epoch=base + 1800 + 7300, subsession_id=0, session_id=0, session_num=0)
            build_ibt(root / "offline-race.ibt", base_epoch=base + 900, subsession_id=0, session_id=0, session_num=1)
            sessions = [item for item in ibt_reader.discover_sessions(root) if item["kind"] == "session"]
            by_file = {Path(item["files"][0]).name: item for item in sessions}
            self.assertEqual(len(sessions), 4)
            self.assertEqual(by_file["stint-1.ibt"]["test_day_run"], 1)
            self.assertEqual(by_file["stint-2.ibt"]["test_day_run"], 2)
            self.assertEqual(by_file["stint-1.ibt"]["test_day_id"], by_file["stint-2.ibt"]["test_day_id"])
            self.assertNotIn("test_day_id", by_file["later.ibt"])
            self.assertNotIn("test_day_id", by_file["offline-race.ibt"])
            self.assertTrue(by_file["offline-race.ibt"]["is_race"])


RULES = tuning_engine.load_structured_tuning_rules()


def stall_observation(*, lf_outside: float = 176.0) -> dict:
    """Pit-stall telemetry equal to `car_setup()`'s sheet: bands in car-left order."""

    return {"tires": {
        "LF": {"remaining_percent": {"L": 97.0, "M": 98.0, "R": 100.0},
               "carcass_temperature_f": {"CL": lf_outside, "CM": 147.2, "CR": 105.8},
               "pressure": {"psi": 17.0, "kind": "live"}},
        "RF": {"remaining_percent": {"L": 95.0, "M": 95.0, "R": 98.0},
               "carcass_temperature_f": {"CL": 224.6, "CM": 210.2, "CR": 188.6},
               "pressure": {"psi": 28.0, "kind": "live"}},
        "LR": {"remaining_percent": {"L": 99.0, "M": 99.0, "R": 100.0},
               "carcass_temperature_f": {"CL": 174.2, "CM": 168.8, "CR": 158.0}},
        "RR": {"remaining_percent": {"L": 99.0, "M": 99.0, "R": 99.0},
               "carcass_temperature_f": {"CL": 165.2, "CM": 161.6, "CR": 156.2}},
    }}


def stint_analysis(identifier: str, hour: float, *, laps: int = 20, base: float = 24.0, slope: float = 0.01,
                   noise: float = 0.0, setup: dict | None = None, fixed: bool | None = False,
                   track_temp: float | None = 80.0, fuel_l: float | None = 60.0, car_path: str = "stockcars2 supra2019",
                   track_id: int = 559, config: str = "Oval", compound: str | None = None,
                   session_type: str = "Offline Testing", flag_state: str = "caution",
                   stop_status: str = "measured_at_stop", observation: dict | None = None) -> dict:
    """One synthetic stint shaped like a real Offline Testing analysis.

    Lap time is `base + slope * run_lap`, alternately +/- `noise`, so every
    median, fit and scatter below is known exactly. Laps are "caution" by
    default because Offline Testing recordings are classified that way.
    """

    moment = f"2026-08-04T{int(hour):02d}:{int(round((hour % 1) * 60)):02d}:00+00:00"
    rows = []
    for number in range(1, laps + 1):
        rows.append({
            "lap": number, "complete": True, "flag_state": flag_state, "green_fraction": 0.0 if flag_state == "caution" else 1.0,
            "pit_time_s": 0.0, "racing_state_fraction": 1.0,
            "lap_time_s": round(base + slope * number + (noise if number % 2 else -noise), 3),
            "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0},
            "conditions": {"track_temperature_f": track_temp},
            "fuel": {"start_l": fuel_l - 0.75 * (number - 1) if fuel_l is not None else None},
            "speed": {"minimum_mph": 118.0},
        })
    return {
        "analysis_id": identifier,
        "identity": {"session_start": moment, "event_type": "Test" if session_type == "Offline Testing" else session_type,
                     "track_id": track_id, "track_name": "Iowa Speedway", "track_config": config,
                     "car_id": 116, "car_name": "Toyota Supra", "car_path": car_path, "is_fixed_setup": fixed,
                     "setup_name": f"{identifier}.sto", "tire_compound": compound,
                     "setup": setup if setup is not None else car_setup()},
        "source": {"selection": {"sim_session_type": session_type}},
        "laps": rows,
        "runs": [{"run_number": 1, "start_lap": 1, "end_lap": laps, "fuel": {"start_l": fuel_l},
                  "tire_measurement_status": stop_status,
                  "tire_observation": observation if observation is not None else (stall_observation() if stop_status == "measured_at_stop" else None)}],
        "damage_repair": {"incident_points": {"events": []}},
    }


def review(*analyses: dict) -> dict:
    return test_day.build_test_day_review(list(analyses), RULES)


class TestDayWindowTests(unittest.TestCase):
    """Backend 3: equal-age windows, fall-off and the screen behind them."""

    def test_twenty_and_fifteen_lap_runs_share_laps_eleven_to_fifteen(self) -> None:
        result = review(stint_analysis("a", 9, laps=20), stint_analysis("b", 10, laps=15))
        self.assertEqual(result["windows"]["late"]["label"], "Laps 11–15")
        self.assertEqual(result["windows"]["fall_off"]["last_run_lap"], 15)
        first, second = result["runs"]
        for row in (first, second):
            self.assertEqual(row["early"]["laps"], [2, 3, 4, 5])  # lap 1 is never a pace sample
            self.assertEqual(row["late"]["laps"], [11, 12, 13, 14, 15])
            self.assertEqual(row["late"]["status"], "measured")
            self.assertEqual(row["laps"][0]["exclusion_reasons"], ["first_lap_of_run"])
            self.assertFalse(row["laps"][0]["eligible"])
            self.assertEqual(row["fall_off"]["laps"], list(range(2, 16)))
        self.assertEqual(first["early"]["median_s"], 24.035)
        self.assertEqual(first["late"]["median_s"], 24.13)
        self.assertEqual(first["fall_off"]["slope_s_per_lap"], 0.01)
        self.assertEqual(first["laps"][13]["label"], "Run 1 · lap 14")
        self.assertEqual(first["trend"]["x_axis"], "run_lap")
        # The chart's line is the table's fall-off (laps 2..15), not a fit over all 20 plotted laps.
        fit = first["trend"]["fit"]
        self.assertEqual((fit["eligible_count"], fit["domain"], fit["slope_s_per_green_lap"]), (14, [2.0, 15.0], 0.01))
        self.assertEqual(fit["points"], [{"x": 2.0, "y": 24.02}, {"x": 15.0, "y": 24.15}])
        self.assertEqual(first["trend"]["fit_basis"], "fall_off")

    def test_late_window_is_withheld_when_no_run_reaches_eight_laps(self) -> None:
        result = review(stint_analysis("a", 9, laps=7), stint_analysis("b", 10, laps=6))
        self.assertEqual(result["windows"]["late"]["status"], "withheld")
        self.assertEqual(result["windows"]["fall_off"]["label"], "Laps 2–5")
        second = result["runs"][1]
        self.assertEqual(second["late"]["status"], "not_measured")
        self.assertIsNone(second["late"]["median_s"])
        self.assertEqual(second["comparison"]["basis"], "early_window")

    def test_the_late_window_never_shares_a_lap_with_laps_two_to_five(self) -> None:
        for laps, label, members in ((8, "Laps 6–8", [6, 7, 8]), (9, "Laps 6–9", [6, 7, 8, 9]), (10, "Laps 6–10", [6, 7, 8, 9, 10])):
            with self.subTest(laps=laps):
                result = review(stint_analysis("a", 9, laps=laps), stint_analysis("b", 10, laps=20))
                self.assertEqual(result["windows"]["late"]["label"], label)
                for row in result["runs"]:
                    self.assertEqual(row["late"]["laps"], members)
                    self.assertFalse(set(row["early"]["laps"]) & set(row["late"]["laps"]))
        withheld = review(stint_analysis("a", 9, laps=7), stint_analysis("b", 10, laps=7))["windows"]["late"]
        self.assertEqual(withheld["reason"], "No run reached 8 laps, so there are not 3 laps after laps 2–5 for a late window.")

    def test_a_short_run_does_not_shorten_the_late_window_for_everyone(self) -> None:
        result = review(stint_analysis("a", 9, laps=20), stint_analysis("b", 10, laps=6), stint_analysis("c", 11, laps=18))
        self.assertEqual(result["windows"]["late"]["label"], "Laps 14–18")
        short = result["runs"][1]
        self.assertEqual(short["late"]["reason"], "The run ended at lap 6, before lap 18.")
        self.assertFalse(short["fall_off"]["equal_age"])
        self.assertEqual(result["runs"][2]["comparison"]["basis"], "early_window")

    def test_a_window_needs_three_clean_laps(self) -> None:
        analysis = stint_analysis("b", 10)
        for lap in analysis["laps"][2:4]:  # laps 3 and 4
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        row = review(stint_analysis("a", 9), analysis)["runs"][1]
        self.assertEqual(row["early"]["laps"], [2, 5])
        self.assertEqual(row["early"]["status"], "not_measured")
        self.assertIsNone(row["early"]["median_s"])
        self.assertEqual(row["early"]["reason"], "2 clean laps in laps 2–5; 3 are needed.")

    def test_offline_testing_caution_classification_is_ignored_but_not_elsewhere(self) -> None:
        offline = review(stint_analysis("a", 9), stint_analysis("b", 10))
        self.assertEqual(offline["screening"]["offline_flag_rule_runs"], [1, 2])
        self.assertEqual(offline["runs"][0]["early"]["status"], "measured")
        self.assertIn("one-lap-to-green", offline["screening"]["offline_flag_rule"])
        hosted = review(stint_analysis("a", 9, session_type="Practice"), stint_analysis("b", 10, session_type="Practice"))
        self.assertEqual(hosted["screening"]["offline_flag_rule_runs"], [])
        self.assertEqual(hosted["runs"][0]["early"]["status"], "not_measured")
        self.assertIn("caution_or_mixed", hosted["runs"][0]["laps"][3]["exclusion_reasons"])
        green = review(stint_analysis("a", 9, session_type="Practice", flag_state="green"),
                       stint_analysis("b", 10, session_type="Practice", flag_state="green"))
        self.assertEqual(green["runs"][0]["early"]["status"], "measured")

    def test_a_car_stopped_on_track_is_not_a_pace_sample(self) -> None:
        analysis = stint_analysis("b", 10)
        analysis["laps"][3]["speed"]["minimum_mph"] = 0.0
        analysis["laps"][3]["lap_time_s"] = 138.419
        row = review(stint_analysis("a", 9), analysis)["runs"][1]
        self.assertIn("stopped_on_track", row["laps"][3]["exclusion_reasons"])
        self.assertFalse(row["laps"][3]["plotted"])
        self.assertTrue(row["laps"][4]["plotted"])
        self.assertEqual(row["early"]["laps"], [2, 3, 5])
        self.assertNotIn(4, row["fall_off"]["laps"])

    def test_missing_conditions_are_not_measured_never_zero(self) -> None:
        result = review(stint_analysis("a", 9), stint_analysis("b", 10, track_temp=None, fuel_l=None))
        conditions = result["runs"][1]["conditions"]
        self.assertIsNone(conditions["track_temp_f"])
        self.assertIsNone(conditions["start_fuel_gal"])
        self.assertIsNone(result["runs"][1]["comparison"]["delta_s"])


class TestDayVerdictTests(unittest.TestCase):
    def test_faster_slower_and_the_threshold_edge(self) -> None:
        # Perfectly linear laps have no scatter, so the threshold is the 0.03 s floor.
        edge = review(stint_analysis("a", 9), stint_analysis("b", 10, base=23.97))["runs"][1]["comparison"]
        self.assertEqual((edge["delta_s"], edge["threshold_s"]), (-0.03, 0.03))
        self.assertEqual(edge["verdict"], "within_scatter")
        self.assertEqual(edge["basis"], "late_window")
        self.assertEqual(edge["window_label"], "Laps 16–20")
        past = review(stint_analysis("a", 9), stint_analysis("b", 10, base=23.969))["runs"][1]["comparison"]
        self.assertEqual((past["verdict"], past["delta_s"]), ("faster", -0.031))
        slower = review(stint_analysis("a", 9), stint_analysis("b", 10, base=24.031))["runs"][1]["comparison"]
        self.assertEqual((slower["verdict"], slower["delta_s"]), ("slower", 0.031))
        self.assertIn("0.031 s slower than run 1", slower["summary"])
        verdicts = {row["comparison"]["verdict"] for row in review(
            stint_analysis("a", 9), stint_analysis("b", 10), stint_analysis("c", 11, base=23.8),
            stint_analysis("d", 12, base=24.2), stint_analysis("e", 13, fuel_l=40.0))["runs"][1:]}
        self.assertEqual(verdicts, {"within_scatter", "faster", "slower", "not_comparable"})

    def test_medians_are_compared_at_the_same_laps_of_the_run(self) -> None:
        # Identical pace with 0.05 s/lap fall-off; run 2 lost laps 16 and 17 to traffic.
        second = stint_analysis("b", 10, slope=0.05, setup=wedge(car_setup()))
        for lap in second["laps"][15:17]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        result = review(stint_analysis("a", 9, slope=0.05), second)
        comparison = result["runs"][1]["comparison"]
        self.assertEqual(result["runs"][0]["late"]["laps"], [16, 17, 18, 19, 20])  # each window still shows its own clean laps
        self.assertEqual(result["runs"][1]["late"]["laps"], [18, 19, 20])
        self.assertEqual((comparison["verdict"], comparison["delta_s"]), ("within_scatter", 0.0))
        self.assertEqual((comparison["run_laps"], comparison["previous_laps"], comparison["current_laps"]),
                         ([18, 19, 20], [18, 19, 20], [18, 19, 20]))
        self.assertEqual((comparison["previous_median_s"], comparison["current_median_s"]), (24.95, 24.95))
        self.assertIn("3 laps clean in both runs", comparison["summary"])
        self.assertEqual(result["next_change"]["kind"], "repeat")
        # Fewer than three shared clean ages in the late window falls back to laps 2-5.
        first = stint_analysis("a", 9, slope=0.05)
        for lap in first["laps"][18:20]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        fallback = review(first, second)["runs"][1]["comparison"]
        self.assertEqual((fallback["basis"], fallback["run_laps"]), ("early_window", [2, 3, 4, 5]))
        for lap in second["laps"][1:3]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        for lap in first["laps"][3:5]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        blocked = review(first, second)["runs"][1]["comparison"]
        self.assertEqual((blocked["verdict"], blocked["blocked_by"]), ("not_comparable", "measurement"))
        self.assertEqual(blocked["reasons"], ["Only 1 lap of laps 16–20 is clean in both runs; 3 are needed at the same laps of the run."])

    def test_scatter_widens_the_threshold(self) -> None:
        result = review(stint_analysis("a", 9, noise=0.1), stint_analysis("b", 10, base=23.9, noise=0.1))
        comparison = result["runs"][1]["comparison"]
        scatter = result["runs"][0]["fall_off"]["scatter_s"]
        self.assertGreater(scatter, 0.09)
        expected = round(2 * ((test_day.MEDIAN_STANDARD_ERROR_FACTOR * scatter / 5 ** 0.5) ** 2 * 2) ** 0.5, 3)
        self.assertAlmostEqual(comparison["threshold_s"], expected, places=2)
        self.assertEqual(comparison["verdict"], "within_scatter")
        self.assertGreater(comparison["threshold_s"], 0.1)

    def test_every_not_comparable_reason_is_stated(self) -> None:
        cases = {
            "different car": ({"car_path": "stockcars2 camaro2019"}, "different car"),
            "configuration": ({"config": "Road"}, "another track configuration"),
            "fixed setup": ({"fixed": True}, "not recorded on an open setup"),
            "unknown setup type": ({"fixed": None}, "does not record whether its setup was open"),
            "track temperature": ({"track_temp": 86.0}, "Track temperature 80 → 86 °F"),
            "track temperature missing": ({"track_temp": None}, "Track temperature was not recorded for run 2"),
            "fuel": ({"fuel_l": 52.0}, "Starting fuel 15.8 → 13.7 gal"),
            "fuel missing": ({"fuel_l": None}, "Starting fuel was not recorded for run 2"),
            "compound": ({"compound": "wet"}, "Tire compound dry → wet"),
        }
        for name, (changes, expected) in cases.items():
            with self.subTest(name):
                first = stint_analysis("a", 9, compound="dry" if name == "compound" else None)
                comparison = review(first, stint_analysis("b", 10, **changes))["runs"][1]["comparison"]
                self.assertEqual(comparison["verdict"], "not_comparable")
                self.assertEqual(comparison["blocked_by"], "conditions")
                self.assertTrue(any(expected in reason for reason in comparison["reasons"]), comparison["reasons"])
                self.assertIsNone(comparison["delta_s"])
        # Just inside both limits is comparable.
        near = review(stint_analysis("a", 9), stint_analysis("b", 10, track_temp=84.9, fuel_l=53.0))["runs"][1]["comparison"]
        self.assertNotEqual(near["verdict"], "not_comparable")
        short = review(stint_analysis("a", 9, laps=4), stint_analysis("b", 10))["runs"][1]["comparison"]
        self.assertEqual((short["verdict"], short["blocked_by"]), ("not_comparable", "measurement"))

    def test_unrecorded_compound_and_used_tires_are_flags_not_blocks(self) -> None:
        second = stint_analysis("b", 10)
        second["corner_tire_age"] = {"runs": [{"tire_age_basis": {"initial_zero_evidence": {
            "initial_odometer_m": {"LF": 1400.0, "RF": 1400.0, "LR": 1400.0, "RR": 1400.0}}}}]}
        comparison = review(stint_analysis("a", 9), second)["runs"][1]["comparison"]
        self.assertNotEqual(comparison["verdict"], "not_comparable")
        self.assertTrue(any("compound is not recorded" in flag for flag in comparison["flags"]))
        self.assertTrue(any("may have started on used tires" in flag and "1,400 m" in flag for flag in comparison["flags"]))

    def test_stints_are_ordered_by_recording_time(self) -> None:
        result = review(stint_analysis("late", 11), stint_analysis("early", 9))
        self.assertEqual([row["analysis_id"] for row in result["runs"]], ["early", "late"])
        self.assertIsNone(result["runs"][0]["comparison"])
        self.assertEqual(result["runs"][0]["setup_change"]["status"], "first_run")


def json_text(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class TestDaySetupChangeTests(unittest.TestCase):
    def test_new_sheet_readings_on_the_same_setup_are_no_change(self) -> None:
        repeat = car_setup(update_count=9, lf_temps="60C, 48C, 38C", rf_tread="99%, 99%, 99%")
        change = review(stint_analysis("a", 9), stint_analysis("b", 10, setup=repeat))["runs"][1]["setup_change"]
        self.assertEqual(change["status"], "unchanged")
        self.assertEqual(change["changes"], [])

    def test_a_changed_setting_is_listed_with_both_values(self) -> None:
        change = review(stint_analysis("a", 9), stint_analysis("b", 10, setup=car_setup(brake_bias="56.5%")))["runs"][1]["setup_change"]
        self.assertEqual(change["status"], "changed")
        self.assertEqual(change["change_count"], 1)
        item = change["changes"][0]
        self.assertEqual((item["field"], item["previous"], item["current"]), ("chassis.front.front_brake_bias", "56.0%", "56.5%"))
        self.assertAlmostEqual(item["delta"], 0.5, places=4)
        self.assertEqual(item["label"], "Front brake bias")
        self.assertEqual((change["readout_count"], change["readouts"]), (0, []))

    def test_one_wedge_adjustment_is_one_change_with_its_readouts_listed_apart(self) -> None:
        change = review(stint_analysis("a", 9), stint_analysis("b", 10, setup=wedge(car_setup())))["runs"][1]["setup_change"]
        self.assertEqual(change["status"], "changed")
        self.assertEqual(change["change_count"], 1)
        self.assertEqual([(item["field"], item["previous"], item["current"]) for item in change["changes"]],
                         [("chassis.left_rear.spring_perch_offset", "-72 mm", "-70 mm")])
        self.assertEqual(change["readout_count"], 6)
        self.assertEqual({item["field"] for item in change["readouts"]}, {
            "chassis.front.cross_weight", "chassis.left_front.corner_weight", "chassis.right_front.corner_weight",
            "chassis.left_rear.corner_weight", "chassis.right_rear.corner_weight", "chassis.left_rear.ride_height"})

    def test_readouts_that_move_without_an_adjustment_are_no_change(self) -> None:
        drifted = car_setup()
        drifted["Chassis"]["LeftFront"]["CornerWeight"] = "3929 N"
        drifted["Chassis"]["RightRear"]["TruckArmPreload"] = "5.5 Nm"
        drifted["Chassis"]["LeftRear"]["RideHeight"] = "145 mm"
        result = review(stint_analysis("a", 9), stint_analysis("b", 10, base=23.99, setup=drifted))
        change = result["runs"][1]["setup_change"]
        self.assertEqual((change["status"], change["change_count"], change["readout_count"]), ("unchanged", 0, 1))
        self.assertEqual(change["reason"], "No adjustment changed; only computed garage readouts differ (left rear ride height 141 mm → 145 mm).")
        self.assertEqual(result["runs"][0]["setup"]["garage_fingerprint"], result["runs"][1]["setup"]["garage_fingerprint"])
        self.assertEqual(result["next_change"]["kind"], "repeat")
        self.assertIn(change["reason"], result["next_change"]["evidence"])

    def test_a_setting_in_only_one_run_is_unknown_not_a_change(self) -> None:
        setup = car_setup()
        setup["Chassis"]["Rear"] = {"RearEndRatio": "4.11"}
        change = review(stint_analysis("a", 9), stint_analysis("b", 10, setup=setup))["runs"][1]["setup_change"]
        self.assertEqual(change["status"], "unknown")
        self.assertEqual(change["unknown_fields"], ["chassis.rear.rear_end_ratio"])
        missing = stint_analysis("b", 10)
        missing["identity"]["setup"] = {}
        change = review(stint_analysis("a", 9), missing)["runs"][1]["setup_change"]
        self.assertEqual(change["status"], "unknown")
        self.assertIn("run 2", change["reason"])


class TestDayTireTests(unittest.TestCase):
    def test_stall_readings_are_oriented_and_agree_with_a_matching_sheet(self) -> None:
        corners = review(stint_analysis("a", 9), stint_analysis("b", 10))["runs"][1]["tires"]["corners"]
        lf, rf = corners["LF"], corners["RF"]
        self.assertEqual(lf["stop"]["temperature_f"], {"inside": 105.8, "middle": 147.2, "outside": 176.0})
        self.assertEqual(rf["stop"]["temperature_f"], {"inside": 224.6, "middle": 210.2, "outside": 188.6})
        self.assertEqual(lf["stop"]["tread_percent"], {"inside": 100.0, "middle": 98.0, "outside": 97.0})
        self.assertEqual(rf["stop"]["tread_percent"], {"inside": 95.0, "middle": 95.0, "outside": 98.0})
        self.assertEqual(lf["stop"]["spread_f"], -70.2)
        self.assertEqual(rf["stop"]["minimum_tread_percent"], 95.0)
        self.assertEqual(lf["sheet_vs_stop"]["status"], "agree")
        self.assertEqual(rf["sheet_vs_stop"]["status"], "agree")
        self.assertEqual(lf["change_from_previous_stop"]["temperature_f"], {"inside": 0.0, "middle": 0.0, "outside": 0.0})

    def test_a_sheet_that_disagrees_is_shown_as_disagreeing_and_never_attributed(self) -> None:
        result = review(stint_analysis("a", 9), stint_analysis("b", 10, observation=stall_observation(lf_outside=150.0)))
        tires = result["runs"][1]["tires"]
        self.assertEqual(tires["corners"]["LF"]["sheet_vs_stop"],
                         {"status": "disagree", "disagrees_on": ["temperature"], "max_temperature_delta_f": 26.0, "max_tread_delta_percent": 0.0})
        self.assertEqual(tires["stop_reading"]["temperature"], "carcass")
        self.assertFalse(tires["sheet_reading"]["uniform"])
        self.assertEqual(tires["corners"]["LF"]["change_from_previous_stop"]["temperature_f"]["outside"], -26.0)
        self.assertEqual(tires["sheet_reading"]["timing"], "unverified")
        self.assertEqual(tires["sheet_reading"]["source"], "garage_sheet")
        self.assertEqual(tires["stop_reading"]["source"], "telemetry_pit_stall")

    def test_a_tread_only_difference_is_named_as_tread(self) -> None:
        observation = stall_observation()
        observation["tires"]["LF"]["remaining_percent"] = {"L": 94.0, "M": 98.0, "R": 100.0}
        agreement = review(stint_analysis("a", 9), stint_analysis("b", 10, observation=observation))["runs"][1]["tires"]["corners"]["LF"]["sheet_vs_stop"]
        self.assertEqual(agreement, {"status": "disagree", "disagrees_on": ["tread"], "max_temperature_delta_f": 0.0, "max_tread_delta_percent": 3.0})

    def test_an_untouched_garage_sheet_is_marked_uniform(self) -> None:
        setup = car_setup(lf_temps="39C, 39C, 39C", rf_temps="39C, 39C, 39C")
        for corner in ("LeftRear", "RightRear"):
            key = "LastTempsOMI" if corner == "LeftRear" else "LastTempsIMO"
            setup["Tires"][corner][key] = "39C, 39C, 39C"
        tires = review(stint_analysis("a", 9), stint_analysis("b", 10, setup=setup))["runs"][1]["tires"]
        self.assertTrue(tires["sheet_reading"]["uniform"])

    def test_a_run_ending_on_track_is_not_measured(self) -> None:
        result = review(stint_analysis("a", 9), stint_analysis("b", 10, stop_status="unmeasured_final_run"))
        tires = result["runs"][1]["tires"]
        self.assertEqual(tires["stop_reading"]["status"], "not_measured")
        self.assertEqual(tires["stop_reading"]["reason"], "The run ended on track; there is no stall reading.")
        self.assertTrue(all(corner["stop"] is None for corner in tires["corners"].values()))
        self.assertEqual(tires["corners"]["LF"]["sheet_vs_stop"]["status"], "not_compared")
        self.assertIsNone(tires["corners"]["LF"]["change_from_previous_stop"])
        stale = review(stint_analysis("a", 9), stint_analysis("b", 10, stop_status="stale_or_unconfirmed_at_stop"))
        self.assertEqual(stale["runs"][1]["tires"]["stop_reading"]["status"], "not_measured")


class TestDayNextChangeTests(unittest.TestCase):
    """Exactly one card, by the approved priority order."""

    def card(self, *analyses: dict) -> dict:
        result = review(*analyses)
        self.assertIsInstance(result["next_change"], dict)
        self.assertIn("text", result["next_change"]["success_rule"])
        return result["next_change"]

    def test_a_slower_change_is_reverted_to_the_exact_previous_value(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=24.2, setup=wedge(car_setup())))
        self.assertEqual(card["kind"], "revert")
        # The adjustment is reverted; the readouts it moved follow it and are never listed as targets.
        self.assertEqual(card["changes"], [{"field": "chassis.left_rear.spring_perch_offset", "label": "Left rear spring perch offset",
                                            "from": "-70 mm", "to": "-72 mm"}])
        self.assertEqual(card["title"], "Revert left rear spring perch offset to -72 mm")
        self.assertIn("Left rear spring perch offset -72 mm → -70 mm moved front cross weight 50.0% → 50.4%.", card["evidence"])
        self.assertEqual(card["success_rule"]["text"], "Beat 24.380 s median on laps 16–20 by at least 0.030 s with 3+ clean laps.")

    def test_an_earlier_best_setup_is_returned_to(self) -> None:
        card = self.card(stint_analysis("a", 9),
                         stint_analysis("b", 10, base=24.3, setup=wedge(car_setup())),
                         stint_analysis("c", 11, base=24.2, setup=wedge(car_setup(brake_bias="56.5%"))))
        self.assertEqual(card["kind"], "return_to_setup")
        self.assertEqual(card["based_on_runs"], [1, 3])
        self.assertEqual({item["field"]: item["to"] for item in card["changes"]},
                         {"chassis.left_rear.spring_perch_offset": "-72 mm", "chassis.front.front_brake_bias": "56.0%"})
        self.assertIn("Run 1 has the best late pace of the runs comparable with run 3", card["evidence"][0])

    def test_one_faster_ruleset_change_is_kept_with_one_more_quoted_step(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=car_setup(brake_bias="56.5%")))
        self.assertEqual(card["kind"], "keep_and_step")
        step = card["ruleset_step"]
        rule = next(item for item in RULES["settings"] if item["setting_id"] == "front-brake-bias")["directions"]["forward"]
        self.assertEqual((step["setting_id"], step["direction"]), ("front-brake-bias", "forward"))
        self.assertEqual((step["instruction"], step["expected_effect"], step["risk"]),
                         (rule["instruction"], rule["expected_effect"], rule["risk"]))
        self.assertFalse(step["step_verified"])
        self.assertEqual(card["title"], "Keep front brake bias at 56.5%; try one more step")
        self.assertEqual(card["success_rule"]["direction"], "faster")

    def test_a_faster_change_outside_the_ruleset_is_kept_without_a_step(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=ballast(car_setup(), "700 mm")))
        self.assertEqual(card["kind"], "keep")
        self.assertEqual(card["title"], "Keep front ballast forward at 700 mm")
        self.assertIsNone(card["ruleset_step"])
        self.assertEqual(card["success_rule"]["direction"], "within")
        # A rear track-bar pair raised together is one setting; the ruleset has no raise direction.
        raised = car_setup()
        raised["Chassis"]["LeftRear"]["TrackBarHeight"] = "10.25 in"
        raised["Chassis"]["RightRear"]["TrackBarHeight"] = "10.25 in"
        pair = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=raised))
        self.assertEqual((pair["kind"], pair["ruleset_step"]), ("keep", None))
        lowered = car_setup()
        lowered["Chassis"]["LeftRear"]["TrackBarHeight"] = "9.75 in"
        lowered["Chassis"]["RightRear"]["TrackBarHeight"] = "9.75 in"
        step = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=lowered))
        self.assertEqual((step["kind"], step["ruleset_step"]["direction"]), ("keep_and_step", "lower-equal"))

    def test_several_changes_are_isolated_one_at_a_time(self) -> None:
        both = ballast(wedge(car_setup()), "700 mm")
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=both))
        self.assertEqual(card["kind"], "isolate")
        # The wedge adjustment ranks as the ruleset's cross weight; its readouts are not settings.
        self.assertEqual(card["title"], "Test left rear spring perch offset alone")
        self.assertIn("run 2 changed 2 settings at once", card["instruction"])
        self.assertEqual(card["changes"], [{"field": "chassis.front.ballast_forward", "label": "Front ballast forward", "from": "700 mm", "to": "711 mm"}])
        self.assertEqual(card["success_rule"]["reference_run"], 1)

    def test_a_ruleset_pair_is_isolated_whole_and_counted_once(self) -> None:
        paired = car_setup(brake_bias="56.5%")
        paired["Chassis"]["LeftRear"]["TrackBarHeight"] = "9.75 in"
        paired["Chassis"]["RightRear"]["TrackBarHeight"] = "9.75 in"
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=paired))
        self.assertEqual(card["kind"], "isolate")
        self.assertIn("run 2 changed 2 settings at once", card["instruction"])
        # Brake bias ranks first in the ruleset, so it is kept; both track-bar ends go back together.
        self.assertEqual(card["title"], "Test front brake bias alone")
        self.assertEqual({item["field"]: item["to"] for item in card["changes"]},
                         {"chassis.left_rear.track_bar_height": "10.00 in", "chassis.right_rear.track_bar_height": "10.00 in"})
        both_pairs = copy.deepcopy(paired)
        both_pairs["Chassis"]["Front"]["FrontBrakeBias"] = "56.0%"
        both_pairs["Chassis"]["LeftFront"]["Packer"] = "0.50 in"
        both_pairs["Chassis"]["RightFront"]["Packer"] = "0.50 in"
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=both_pairs))
        self.assertEqual(card["title"], "Test rear track bar pair alone")
        self.assertIn("with only rear track bar pair at 9.75 in; run 2 changed 2 settings at once", card["instruction"])
        self.assertEqual({item["field"] for item in card["changes"]},
                         {"chassis.left_front.packer", "chassis.right_front.packer"})

    def test_a_faster_wedge_keeps_cross_weight_with_the_rulesets_step(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=wedge(car_setup())))
        self.assertEqual(card["kind"], "keep_and_step")
        self.assertEqual(card["title"], "Keep front cross weight at 50.4%; try one more step")
        self.assertEqual((card["ruleset_step"]["setting_id"], card["ruleset_step"]["direction"]), ("cross-weight", "raise"))
        self.assertEqual(card["changes"], [{"field": "chassis.left_rear.spring_perch_offset", "label": "Left rear spring perch offset",
                                            "from": "-70 mm", "to": None}])
        self.assertIn("Left rear spring perch offset -72 mm → -70 mm moved front cross weight 50.0% → 50.4%.", card["evidence"])

    def test_within_scatter_repeats_unchanged(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.99))
        self.assertEqual(card["kind"], "repeat")
        self.assertEqual(card["success_rule"]["text"], "Repeat 24.170 s median on laps 16–20 within ±0.030 s with 3+ clean laps.")
        self.assertTrue(any("No garage setting changed" in item for item in card["evidence"]))

    def test_not_comparable_asks_for_a_comparable_repeat(self) -> None:
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, fuel_l=45.0, setup=wedge(car_setup())))
        self.assertEqual(card["kind"], "repeat_comparable")
        self.assertEqual(card["title"], "Repeat run 2 with run 1's fuel")
        self.assertIn("starting with 15.8 gal (within 2 gal of run 1)", card["instruction"])
        self.assertEqual(card["success_rule"]["direction"], "measure")
        self.assertIn("within 2 gal", card["success_rule"]["text"])
        short = self.card(stint_analysis("a", 9, laps=3), stint_analysis("b", 10))
        self.assertEqual((short["kind"], short["success_rule"]["reference_run"]), ("repeat_comparable", 2))
        other = self.card(stint_analysis("a", 9), stint_analysis("b", 10, fixed=True))
        self.assertEqual(other["title"], "Repeat run 2 under comparable conditions")

    def test_a_track_that_moved_asks_to_rerun_the_earlier_setup_now(self) -> None:
        result = review(stint_analysis("a", 9, track_temp=95.0), stint_analysis("b", 10, track_temp=86.0, fuel_l=50.0,
                                                                                setup=car_setup(brake_bias="56.5%")))
        self.assertEqual(result["runs"][1]["comparison"]["reason_codes"], ["track_temperature", "fuel"])
        card = result["next_change"]
        self.assertEqual(card["kind"], "rebaseline")
        self.assertEqual(card["title"], "Re-run run 1's setup now")
        self.assertEqual(card["instruction"], "Track temperature moved 95 → 86 °F since run 1 and will not come back. Drive run 1's "
                                              "setup again now, starting with 13.2 gal, so it can be compared with run 2 under the same conditions.")
        # The comparison it asks for is against run 2's conditions, which the driver can still meet.
        self.assertEqual((card["success_rule"]["reference_run"], card["success_rule"]["direction"]), (2, "measure"))
        self.assertIn("within 5 °F of run 2", card["success_rule"]["text"])
        self.assertEqual(card["changes"], [{"field": "chassis.front.front_brake_bias", "label": "Front brake bias", "from": "56.5%", "to": "56.0%"}])
        # With one garage setup there is nothing to re-run: the latest run is the new baseline.
        same = review(stint_analysis("a", 9, track_temp=95.0), stint_analysis("b", 10, track_temp=86.0))["next_change"]
        self.assertEqual((same["kind"], same["title"]), ("rebaseline", "Make run 2 the baseline for today's conditions"))
        self.assertIn("used the same garage setup and track temperature moved 95 → 86 °F", same["instruction"])
        self.assertEqual((same["changes"], same["success_rule"]["reference_run"]), ([], 2))

    def test_a_blocked_measurement_asks_for_the_laps_its_own_rule_needs(self) -> None:
        # Run 2 stopped after 4 laps: laps 2-5 are the quickest window to compare, and the rule checks exactly them.
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, laps=4))
        self.assertEqual(card["instruction"], "Drive run 2's setup again, unchanged, for at least 5 laps so laps 2–5 can be compared with run 1.")
        self.assertEqual((card["success_rule"]["window_label"], card["success_rule"]["last_run_lap"]), ("Laps 2–5", 5))
        # Run 1 measured only its late window, so the repeat must reach it.
        first = stint_analysis("a", 9)
        for lap in first["laps"][1:4]:
            lap["clean_context"]["traffic_proximity_fraction"] = 0.4
        card = self.card(first, stint_analysis("b", 10, laps=6))
        self.assertEqual(card["instruction"], "Drive run 2's setup again, unchanged, for at least 20 laps so laps 16–20 can be compared with run 1.")
        self.assertEqual((card["success_rule"]["window_label"], card["success_rule"]["last_run_lap"]), ("Laps 16–20", 20))

    def test_a_blocked_measurement_names_the_run_that_lacked_laps(self) -> None:
        card = self.card(stint_analysis("a", 9, laps=2), stint_analysis("b", 10, laps=6))
        self.assertEqual(card["title"], "Run 1 has too few clean laps; make run 2 the baseline")
        self.assertIn("Run 1 has no pace window with 3+ clean laps", card["instruction"])
        self.assertEqual(card["success_rule"]["reference_run"], 2)
        neither = self.card(stint_analysis("a", 9, laps=2), stint_analysis("b", 10, laps=2))
        self.assertEqual(neither["title"], "Runs 1 and 2 have too few clean laps")

    def test_a_faster_run_that_is_not_comparable_is_named_not_skipped(self) -> None:
        card = self.card(stint_analysis("a", 9, base=23.8, fuel_l=60.0),
                         stint_analysis("b", 10, base=24.0, fuel_l=53.0, setup=car_setup(brake_bias="56.5%")),
                         stint_analysis("c", 11, base=24.25, fuel_l=52.0, setup=ballast(car_setup(brake_bias="56.5%"), "700 mm")),
                         stint_analysis("d", 12, base=24.25, fuel_l=52.0, setup=ballast(car_setup(brake_bias="56.5%"), "700 mm")))
        self.assertEqual((card["kind"], card["based_on_runs"]), ("return_to_setup", [2, 4]))
        self.assertTrue(card["evidence"][0].startswith("Run 2 has the best late pace of the runs comparable with run 4."))
        self.assertEqual(card["evidence"][1], "Run 1 was faster late (23.980 s) but is not comparable with run 4: "
                                              "Starting fuel 15.8 → 13.7 gal (2 gal or more apart).")

    def test_no_card_is_derived_from_tire_temperatures(self) -> None:
        hot = stall_observation(lf_outside=260.0)
        card = self.card(stint_analysis("a", 9), stint_analysis("b", 10, base=23.99, observation=hot))
        self.assertEqual(card["kind"], "repeat")
        self.assertIn("No setup direction is inferred from tire temperatures", card["note"])


def ballast(setup: dict, value: str) -> dict:
    setup["Chassis"]["Front"]["BallastForward"] = value
    return setup


def split_runs(analysis: dict, *ends: int) -> dict:
    """Split one recording into runs ending at `ends` (pit visits), each starting at lap 1 of its run."""

    laps = analysis["laps"]
    template = analysis["runs"][0]
    runs, start = [], 1
    for number, end in enumerate((*ends, len(laps)), start=1):
        fuel = laps[start - 1]["fuel"]["start_l"]
        observation = template["tire_observation"] if end == len(laps) else stall_observation()
        runs.append({**template, "run_number": number, "start_lap": start, "end_lap": end, "fuel": {"start_l": fuel},
                     "tire_measurement_status": template["tire_measurement_status"] if end == len(laps) else "measured_at_stop",
                     "tire_observation": observation, "tire_set_lifecycle": {"confirmed_changed_at_run_end": []}})
        start = end + 1
    for lap in laps:
        run = next(item for item in runs if item["start_lap"] <= lap["lap"] <= item["end_lap"])
        base = lap["lap_time_s"] - analysis_slope(analysis) * lap["lap"]
        lap["lap_time_s"] = round(base + analysis_slope(analysis) * (lap["lap"] - run["start_lap"] + 1), 3)
    analysis["runs"] = runs
    return analysis


def analysis_slope(analysis: dict) -> float:
    laps = analysis["laps"]
    return round((laps[1]["lap_time_s"] - laps[0]["lap_time_s"]), 6) if len(laps) > 1 else 0.0


class TestDayRunsInsideARecordingTests(unittest.TestCase):
    """Addendum 1: runs inside one recording are rows, split by pit visits."""

    def test_one_recording_with_two_runs_is_a_test_day_of_two_rows(self) -> None:
        practice = split_runs(stint_analysis("practice", 20, laps=30, session_type="Practice", flag_state="green"), 12)
        result = review(practice)
        self.assertEqual((result["run_count"], result["recording_count"]), (2, 1))
        first, second = result["runs"]
        self.assertEqual((first["run"], second["run"]), (1, 2))
        self.assertEqual(first["recording"], {"number": 1, "analysis_id": "practice", "analysis_path": None,
                                              "recorded_at": "2026-08-04T20:00:00+00:00", "run_in_recording": 1, "runs_in_recording": 2})
        self.assertEqual(second["recording"]["run_in_recording"], 2)
        self.assertEqual((first["measured_run"]["first_lap"], first["measured_run"]["last_lap"]), (1, 12))
        self.assertEqual((second["measured_run"]["first_lap"], second["measured_run"]["last_lap"], second["measured_run"]["run_laps"]), (13, 30, 18))
        self.assertEqual(second["laps"][0]["label"], "Run 2 · lap 1")
        self.assertEqual(second["early"]["laps"], [14, 15, 16, 17])  # laps 2-5 of the second run
        # The recording keeps one garage sheet: a change between its runs is not recorded, never inferred.
        change = second["setup_change"]
        self.assertEqual(change["status"], "not_recorded")
        self.assertIn("one recording, which keeps one garage sheet", change["reason"])
        self.assertEqual(second["conditions"]["tire_start"]["status"], "carried_over")
        self.assertEqual(second["conditions"]["start_fuel_gal"], round((60.0 - 0.75 * 12) / 3.785411784, 2))
        self.assertTrue(first["tires"]["stop_reading"]["status"] == "measured")
        self.assertIsNotNone(second["comparison"])
        self.assertNotEqual(result["next_change"]["kind"], "revert", "No setup change is attributed inside one recording.")

    def test_the_next_recordings_setup_is_compared_with_a_caveat_when_the_last_held_several_runs(self) -> None:
        first = split_runs(stint_analysis("a", 9, laps=24), 12)
        result = review(first, stint_analysis("b", 10, setup=car_setup(brake_bias="56.5%")))
        self.assertEqual(result["run_count"], 3)
        change = result["runs"][2]["setup_change"]
        self.assertEqual((change["status"], change["compared_with_run"]), ("changed", 2))
        self.assertIn("one garage sheet of run 2's recording, which holds 2 runs", change["reason"])
        self.assertEqual([row["recording"]["number"] for row in result["runs"]], [1, 1, 2])

    def test_a_long_practice_keeps_its_latest_runs(self) -> None:
        many = split_runs(stint_analysis("many", 9, laps=60), *range(2, 60, 2))
        result = review(many)
        self.assertEqual(result["run_count"], test_day.MAXIMUM_ROWS)
        self.assertEqual(result["runs"][0]["measured_run"]["first_lap"], 13)
        self.assertIn(f"Showing the latest {test_day.MAXIMUM_ROWS} runs; 6 earlier runs are left out.", result["limitations"])


class TestDayInputTests(unittest.TestCase):
    def test_the_review_needs_two_runs_in_one_to_twelve_distinct_recordings(self) -> None:
        with self.assertRaisesRegex(test_day.TestDayError, "at least 2 runs; this recording holds 1"):
            review(stint_analysis("a", 9))
        with self.assertRaisesRegex(test_day.TestDayError, "1–12 recordings; 13"):
            review(*[stint_analysis(f"s{index}", 1 + index * 0.5) for index in range(13)])
        with self.assertRaisesRegex(test_day.TestDayError, "1–12 recordings; 0"):
            test_day.build_test_day_review([], RULES)
        with self.assertRaisesRegex(test_day.TestDayError, "more than once"):
            review(stint_analysis("a", 9), stint_analysis("a", 10))

    def test_missing_required_fields_fail_clearly(self) -> None:
        for field, message in (("identity", "no identity"), ("laps", "no laps list"), ("runs", "no runs list")):
            with self.subTest(field):
                broken = stint_analysis("b", 10)
                del broken[field]
                with self.assertRaisesRegex(test_day.TestDayError, f"Recording 2 has {message}"):
                    review(stint_analysis("a", 9), broken)
        undated = stint_analysis("b", 10)
        undated["identity"]["session_start"] = None
        with self.assertRaisesRegex(test_day.TestDayError, "no recording time"):
            review(stint_analysis("a", 9), undated)

    def test_unknown_optional_fields_do_not_disturb_the_review(self) -> None:
        extended = stint_analysis("b", 10)
        extended["future_block"] = {"anything": [1, 2, 3]}
        extended["laps"][4]["future_lap_field"] = "kept"
        self.assertEqual(review(stint_analysis("a", 9), extended)["runs"][1]["early"]["status"], "measured")

    def test_the_result_is_strict_json(self) -> None:
        result = review(stint_analysis("a", 9), stint_analysis("b", 10, base=23.9, setup=car_setup(brake_bias="56.5%")))
        self.assertEqual(result["contract"], "test-day-review-v1")
        self.assertTrue(result["read_only"])
        json_text(result)


class TestDayWorkflowTests(unittest.TestCase):
    """Backend 4: read-only and confined to archived analyses."""

    def setUp(self) -> None:
        import json
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name) / "archive"
        self.paths = []
        for name, hour in (("stint-b", 10), ("stint-a", 9)):
            path = self.archive / "reports" / name / "analysis.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(stint_analysis(name, hour)), encoding="utf-8")
            self.paths.append(str(path))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def snapshot(self) -> dict:
        return {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in Path(self.temporary.name).rglob("*")}

    def test_the_workflow_reviews_archived_stints_without_writing(self) -> None:
        before = self.snapshot()
        result = test_day.review_test_day_workflow(analysis_paths=self.paths, archive_root=str(self.archive))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([row["analysis_id"] for row in result["runs"]], ["stint-a", "stint-b"])
        self.assertEqual(result["runs"][0]["analysis_path"], str(Path(self.paths[1]).resolve()))
        self.assertNotIn("The tuning ruleset is unavailable", " ".join(result["limitations"]))

    def test_paths_must_be_distinct_archived_analysis_files(self) -> None:
        outside = Path(self.temporary.name) / "elsewhere" / "analysis.json"
        outside.parent.mkdir()
        outside.write_text("{}", encoding="utf-8")
        renamed = self.archive / "reports" / "stint-a" / "copy.json"
        renamed.write_text("{}", encoding="utf-8")
        broken = self.archive / "reports" / "broken" / "analysis.json"
        broken.parent.mkdir()
        broken.write_text("{not json", encoding="utf-8")
        cases = (
            ([self.paths[0], str(outside)], "within"),
            ([self.paths[0], str(renamed)], "within"),
            ([self.paths[0], self.paths[0]], "more than once"),
            ([self.paths[0]], "at least 2 runs; this recording holds 1"),
            (self.paths[0], "must be a list"),
            ([self.paths[0], str(broken)], "could not be read"),
        )
        for paths, message in cases:
            with self.subTest(message):
                with self.assertRaisesRegex(test_day.TestDayError, message):
                    test_day.review_test_day_workflow(analysis_paths=paths, archive_root=str(self.archive))


WORKSPACE = Path(__file__).resolve().parents[2]


def _load_tool_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestDayToolContractTests(unittest.TestCase):
    """Backend 4: one more read-only tool, its contract and its fixture."""

    def test_the_server_exposes_twenty_four_tools_including_the_review(self) -> None:
        import mcp_server

        self.assertEqual(len(mcp_server.TOOLS), 24)
        tool = next(item for item in mcp_server.TOOLS if item["name"] == "review_test_day")
        paths = tool["inputSchema"]["properties"]["analysis_paths"]
        self.assertEqual((paths["minItems"], paths["maxItems"]), (1, 12))
        self.assertEqual(tool["inputSchema"]["required"], ["analysis_paths"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        snapshot = json_load(WORKSPACE / "contracts" / "mcp-tools.v1.json")
        self.assertEqual(snapshot["tools"], mcp_server.TOOLS)
        self.assertEqual(json_load(WORKSPACE / "contracts" / "compatibility.json")["backend"]["tool_count"], 24)

    def test_the_review_is_classified_read_only_everywhere_it_is_allowed(self) -> None:
        import coach_tool_authority as authority

        self.assertEqual(authority.TOOL_CAPABILITIES["review_test_day"], authority.CAPABILITY_READ_ANALYSIS)
        host = (WORKSPACE / "companion-app" / "src" / "iRacingCoach.Coordinator" / "CompanionHostProfile.cs").read_text(encoding="utf-8")
        self.assertIn('"review_test_day"', host)

    def test_the_tool_confines_paths_to_archived_analyses(self) -> None:
        import json
        import tempfile
        from unittest import mock

        import mcp_server

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive"
            paths = []
            for name, hour in (("one", 9), ("two", 10)):
                path = archive / "reports" / name / "analysis.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(stint_analysis(name, hour)), encoding="utf-8")
                paths.append(str(path))
            with mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", archive):
                result = mcp_server.call_tool("review_test_day", {"analysis_paths": paths})
                self.assertEqual(result["run_count"], 2)
                with self.assertRaisesRegex(ValueError, "must stay within"):
                    mcp_server.call_tool("review_test_day", {"analysis_paths": [paths[0], str(Path(directory) / "analysis.json")]})
                with self.assertRaisesRegex(ValueError, "1-12"):
                    mcp_server.call_tool("review_test_day", {"analysis_paths": paths[0]})
                with self.assertRaisesRegex(ValueError, "analysis.json"):
                    mcp_server.call_tool("review_test_day", {"analysis_paths": [paths[0], str(archive / "reports" / "two" / "other.json")]})

    def test_the_fixture_is_current_and_validates_against_the_contract(self) -> None:
        validation = _load_tool_module("contract_validation_for_test_day", WORKSPACE / "tools" / "contract_validation.py")
        schema = json_load(WORKSPACE / "contracts" / "test-day-review-v1.schema.json")
        fixture = json_load(WORKSPACE / "test-data" / "test-day-review.json")
        validation.assert_valid(fixture, schema, "test-day-review fixture")
        self.assertTrue(fixture["fixture_metadata"]["synthetic"])
        self.assertEqual(fixture["next_change"]["kind"], "keep_and_step")
        generator = _load_tool_module("generate_fixtures_for_test_day", WORKSPACE / "tools" / "generate_fixtures.py")
        self.assertEqual(json_load_text(json_text(generator._test_day_fixture())), fixture)
        for payload in (review(stint_analysis("a", 9), stint_analysis("b", 10, fuel_l=45.0)),
                        review(stint_analysis("a", 9, laps=4), stint_analysis("b", 10, stop_status="unmeasured_final_run")),
                        review(split_runs(stint_analysis("p", 9, laps=24), 12), stint_analysis("b", 10))):
            validation.assert_valid(json_load_text(json_text(payload)), schema, "review")

    def test_the_contract_rejects_a_missing_field_and_an_unchanged_verdict(self) -> None:
        validation = _load_tool_module("contract_validation_for_test_day", WORKSPACE / "tools" / "contract_validation.py")
        schema = json_load(WORKSPACE / "contracts" / "test-day-review-v1.schema.json")
        payload = json_load_text(json_text(review(stint_analysis("a", 9), stint_analysis("b", 10))))
        missing = copy.deepcopy(payload)
        del missing["runs"][1]["tires"]
        self.assertTrue(any("tires" in error for error in validation.validate(missing, schema)))
        unchanged = copy.deepcopy(payload)
        unchanged["runs"][1]["comparison"]["verdict"] = "unchanged"
        self.assertTrue(validation.validate(unchanged, schema))
        extended = copy.deepcopy(payload)
        extended["runs"][1]["future_optional_field"] = {"kept": True}
        self.assertEqual(validation.validate(extended, schema), [])


def json_load(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def json_load_text(text: str) -> dict:
    import json

    return json.loads(text)


if __name__ == "__main__":
    unittest.main()
