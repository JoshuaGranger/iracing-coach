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
            "Front": {"CrossWeight": cross_weight, "FrontBrakeBias": brake_bias, "NoseWeight": "51.0%"},
            "LeftFront": {"Packer": "0.25 in", "RideHeight": "4.25 in"},
            "RightFront": {"Packer": "0.25 in", "RideHeight": "4.30 in"},
            "LeftRear": {"TrackBarHeight": "10.00 in"},
            "RightRear": {"TrackBarHeight": "10.00 in"},
        },
    }


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

    def test_races_qualifying_hosted_and_undated_sessions_are_never_tagged(self) -> None:
        groups = [
            stint(DAY_START),
            stint(DAY_START + 5 * 60, kind="Race", is_race=True),
            stint(DAY_START + 10 * 60, kind="Lone Qualify"),
            stint(DAY_START + 15 * 60, kind="Practice", group_prefix="subsession"),
            stint(DAY_START + 20 * 60, time_source="mtime"),
            stint(DAY_START + 25 * 60, kind=None),
            stint(DAY_START + 30 * 60),
        ]
        groups[5]["event_type"] = None
        ibt_reader._assign_test_days(groups)
        tagged = [index for index, group in enumerate(groups) if "test_day_id" in group]
        self.assertEqual(tagged, [0, 6])
        self.assertEqual([groups[0]["test_day_run"], groups[6]["test_day_run"]], [1, 2])

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


if __name__ == "__main__":
    unittest.main()
