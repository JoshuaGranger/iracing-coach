from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from setup_catalog import (  # noqa: E402
    SetupLimitError,
    catalog_setups,
    compare_displayed_setups,
    compare_setups,
    is_garage_readout_field,
    normalize_embedded_setup,
    parse_setup_html,
)


def setup_html(name: str, track: str = "newhampshire oval") -> str:
    return f"""<!doctype html>
<html><body>
<H2 align="center">iRacing.com Motorsport Simulations<br>
stockcars2 supra2019 setup: {name}<br>
track: {track}</H2><br>
<H2><U>LEFT FRONT:</U></H2>
Cold pressure:<U>17.0 psi</U><br>
Last temps O M I:<U>99F</U><br><U>100F</U><br><U>101F</U><br>
Tread remaining:<U>99%</U><br><U>98%</U><br><U>97%</U><br>
<H2><U>FRONT:</U></H2>
Cross weight:<U>54.8%</U><br>
Steering ratio:<U>12:1</U><br>
<H2><U>LEFT FRONT:</U></H2>
Corner weight:<U>883 lbs</U><br>
Ride height:<U>4.252 in</U><br>
Shock spring rate:<U>9000 lbs/in</U><br>
Packer:<U>0.937&quot; shim</U><br>
<H2><U>Notes:</U></H2>
Protect the platform.<br><br>Use one-click changes.
</body></html>"""


class SetupCatalogTests(unittest.TestCase):
    def test_parses_repeated_sections_identity_notes_and_canonical_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "setups" / "stockcars2 supra2019"
            root.mkdir(parents=True)
            name = "NOAPS_MaconiSetupShop AtlantaSS 26S3 R"
            path = root / f"{name}.htm"
            path.write_text(setup_html(name), encoding="utf-8")

            parsed = parse_setup_html(path, root=root)
            identity = parsed["identity"]
            self.assertEqual(identity["filename"]["season_key"], "2026S3")
            self.assertEqual(identity["filename"]["role"], "race")
            self.assertEqual(identity["filename"]["vendor"], "NOAPS MaconiSetupShop")
            self.assertEqual(identity["filename"]["track_tokens"], ["AtlantaSS"])
            self.assertEqual(identity["filename"]["variant"], "ss")
            self.assertTrue(identity["mismatches"]["track_header_mismatch"])
            self.assertFalse(identity["mismatches"]["setup_name_mismatch"])
            self.assertEqual(identity["car_folder"], "stockcars2 supra2019")

            fields = parsed["fields"]
            self.assertAlmostEqual(
                fields["tires.left_front.cold_pressure"]["value"], 117.210874, places=5
            )
            self.assertEqual(fields["tires.left_front.cold_pressure"]["unit"], "kPa")
            self.assertEqual(
                fields["tires.left_front.last_temps_omi"]["kind"], "number_list"
            )
            self.assertEqual(
                len(fields["tires.left_front.tread_remaining"]["value"]), 3
            )
            self.assertAlmostEqual(
                fields["chassis.left_front.ride_height"]["value"], 108.0008, places=4
            )
            self.assertAlmostEqual(
                fields["chassis.left_front.spring_rate"]["value"], 1576.141517, places=5
            )
            self.assertIn("Protect the platform", parsed["notes"])

    def test_track_alias_avoids_false_nhms_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "VendorSetupShop NHMS 26S3 Q.html"
            name = path.stem
            path.write_text(setup_html(name), encoding="utf-8")
            parsed = parse_setup_html(path)
            self.assertFalse(parsed["identity"]["mismatches"]["track_header_mismatch"])
            self.assertEqual(parsed["identity"]["filename"]["role_code"], "Q")

    def test_catalog_pairs_recursively_hashes_sources_and_bounds_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "setups"
            car = root / "stockcars2 supra2019"
            nested = car / "pack"
            nested.mkdir(parents=True)
            name = "NOAPS_MaconiSetupShop NHMS 26S3 Q"
            (nested / f"{name}.htm").write_text(
                setup_html(name, "newhampshire oval"), encoding="utf-8"
            )
            (nested / f"{name}.sto").write_bytes(b"opaque-sto")
            (car / "Solo Iowa 26S3 E.sto").write_bytes(b"solo")

            catalog = catalog_setups(root, max_entries=2)
            self.assertTrue(catalog["read_only"])
            self.assertEqual(catalog["returned_entry_count"], 2)
            entry = next(
                item for item in catalog["entries"] if item["pair_status"] == "paired"
            )
            self.assertEqual(entry["pair_status"], "paired")
            self.assertEqual(entry["car_folder"], "stockcars2 supra2019")
            self.assertRegex(entry["sources"]["html"][0]["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(entry["sources"]["sto"][0]["sha256"], r"^[0-9a-f]{64}$")

            bounded = catalog_setups(root, max_entries=1)
            self.assertEqual(bounded["returned_entry_count"], 1)
            self.assertTrue(bounded["entries_truncated"])

            limited = catalog_setups(root, max_files=1)
            self.assertTrue(limited["scan_truncated"])
            self.assertLessEqual(limited["matching_files_seen"], 1)

    def test_normalizes_embedded_metric_setup_and_matches_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            name = "NOAPS_MaconiSetupShop NHMS 26S3 R"
            path = Path(directory) / f"{name}.htm"
            path.write_text(setup_html(name), encoding="utf-8")
            parsed = parse_setup_html(path)
            embedded = {
                "CarSetup": {
                    "Chassis": {
                        "Front": {"CrossWeight": "54.8%", "SteeringRatio": "12:1"},
                        "LeftFront": {
                            "CornerWeight": "3927 N",
                            "RideHeight": "108 mm",
                            "SpringRate": "1576 N/mm",
                            "PackerShim": "23.8 mm",
                        },
                    },
                    "Tires": {
                        "LeftFront": {
                            "ColdPressure": "117 kPa",
                            "LastTempsOMI": "37C, 38C, 38C",
                            "TreadRemaining": "99%, 98%, 97%",
                        }
                    },
                    "UpdateCount": 8,
                }
            }
            normalized = normalize_embedded_setup(embedded)
            self.assertNotIn("update_count", normalized["fields"])
            comparison = compare_setups(parsed, normalized)
            self.assertGreaterEqual(comparison["summary"]["common_fields"], 8)
            self.assertEqual(comparison["summary"]["different_fields"], 0)

    def test_compare_reports_real_difference_and_respects_output_limit(self) -> None:
        left = normalize_embedded_setup(
            {"Chassis": {"Front": {"CrossWeight": "54.8%", "NoseWeight": "50.0%"}}}
        )
        right = normalize_embedded_setup(
            {"Chassis": {"Front": {"CrossWeight": "52.0%", "NoseWeight": "51.0%"}}}
        )
        result = compare_setups(left, right, max_output=1)
        self.assertEqual(result["summary"]["different_fields"], 2)
        self.assertEqual(len(result["differences"]), 1)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(result["delta_definition"], "left minus right in the reported canonical unit")

    def test_rejects_html_over_requested_size_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Large 26S3 R.htm"
            path.write_text(setup_html(path.stem), encoding="utf-8")
            with self.assertRaises(SetupLimitError):
                parse_setup_html(path, max_file_bytes=16)


# The IRP Supra test day of 2026-09-24, as the Setups page flattens each
# recording's CarSetup (trimmed to the fields that moved plus neighbours).
IRP_BASELINE = {
    "Chassis.Front.CrossWeight": "50.9%",
    "Chassis.Front.SteeringRatio": "12:1",
    "Chassis.FrontArb.LinkSlack": "0 mm",
    "Chassis.FrontArb.Preload": "0.0 Nm",
    "Chassis.LeftFront.Camber": "+6.0 deg",
    "Chassis.LeftFront.CornerWeight": "4014 N",
    "Chassis.LeftFront.SpringPerchOffset": "-95 mm",
    "Chassis.LeftRear.CornerWeight": "4126 N",
    "Chassis.LeftRear.RideHeight": "146 mm",
    "Chassis.RightRear.CornerWeight": "3844 N",
    "Chassis.RightRear.TruckArmPreload": "-0.4 Nm",
    "Chassis.Rear.RearEndRatio": "5.57",
    "Tires.LeftFront.ColdPressure": "96 kPa",
    "Tires.LeftFront.LastHotPressure": "115 kPa",
    "Tires.LeftFront.TreadRemaining": "98%, 98%, 100%",
}
IRP_V2_LF_CAMBER = {
    **IRP_BASELINE,
    "Chassis.Front.CrossWeight": "51.1%",
    "Chassis.FrontArb.LinkSlack": "-0 mm",
    "Chassis.FrontArb.Preload": "-12.7 Nm",
    "Chassis.LeftFront.Camber": "+5.2 deg",
    "Chassis.LeftFront.CornerWeight": "3999 N",
    "Chassis.LeftFront.SpringPerchOffset": "-98 mm",
    "Chassis.LeftRear.CornerWeight": "4142 N",
    "Chassis.LeftRear.RideHeight": "145 mm",
    "Chassis.RightRear.CornerWeight": "3826 N",
    "Chassis.RightRear.TruckArmPreload": "8.6 Nm",
    "Tires.LeftFront.LastHotPressure": "117 kPa",
}
IRP_V3_STEER10 = {
    **IRP_BASELINE,
    "Chassis.Front.SteeringRatio": "10:1",
    "Chassis.LeftRear.CornerWeight": "4127 N",
    "Chassis.RightRear.CornerWeight": "3843 N",
}


class DisplayedSetupComparisonTests(unittest.TestCase):
    """Setups › Compare counted garage readouts as changes: v3 against the baseline
    showed "3 changed" (the steering ratio plus two corner weights a newton apart)
    and v2 "13 changed" with two real changes, including "Front Arb · Link Slack
    −0 mm vs 0 mm"."""

    @staticmethod
    def _rows(result: dict) -> dict[str, dict]:
        return {row["key"]: row for row in result["rows"]}

    def test_only_adjustments_count_as_changes_and_readouts_are_listed_apart(self) -> None:
        result = compare_displayed_setups(IRP_V3_STEER10, IRP_BASELINE)
        rows = self._rows(result)
        self.assertEqual(result["counts"]["changed"], 1)
        self.assertEqual(rows["Chassis.Front.SteeringRatio"]["status"], "changed")
        self.assertEqual(result["counts"]["readouts_changed"], 2)
        for key in ("Chassis.LeftRear.CornerWeight", "Chassis.RightRear.CornerWeight"):
            self.assertTrue(rows[key]["readout"], key)
            self.assertEqual(rows[key]["status"], "changed", "A newton is one display step: shown, not hidden.")
        self.assertNotIn("Tires.LeftFront.LastHotPressure", rows, "Tire readings are not setup values.")
        self.assertNotIn("Tires.LeftFront.TreadRemaining", rows)
        self.assertEqual(result["counts"]["readings_excluded"], 2)
        self.assertEqual([row["key"] for row in result["rows"][:1]], ["Chassis.Front.SteeringRatio"],
                         "Changed settings lead, readouts follow.")

    def test_negative_zero_equals_zero_and_the_camber_test_reads_as_its_adjustments(self) -> None:
        result = compare_displayed_setups(IRP_V2_LF_CAMBER, IRP_BASELINE)
        rows = self._rows(result)
        self.assertEqual(rows["Chassis.FrontArb.LinkSlack"]["status"], "identical", "-0 mm is 0 mm.")
        changed = {key for key, row in rows.items() if row["status"] == "changed" and not row["readout"]}
        self.assertTrue({"Chassis.LeftFront.Camber", "Chassis.LeftFront.SpringPerchOffset"} <= changed)
        for key in ("Chassis.Front.CrossWeight", "Chassis.LeftFront.CornerWeight", "Chassis.LeftRear.RideHeight",
                    "Chassis.RightRear.TruckArmPreload"):
            self.assertTrue(rows[key]["readout"] and rows[key]["status"] == "changed", key)
        # The front anti-roll-bar preload moved with the perch; whether it is a
        # readout is the shared classification's call, never a copy of it.
        preload = rows["Chassis.FrontArb.Preload"]
        self.assertEqual(preload["readout"], is_garage_readout_field("chassis.front_arb.preload"))
        self.assertEqual(result["counts"]["changed"], 2 + (0 if preload["readout"] else 1))
        self.assertEqual(rows["Chassis.LeftFront.Camber"]["delta_left_minus_right"], -0.8)

    def test_readout_classification_is_the_shared_set_not_a_copy(self) -> None:
        import setup_catalog
        with mock.patch.object(setup_catalog, "GARAGE_READOUT_LEAVES",
                               setup_catalog.GARAGE_READOUT_LEAVES | {"steering_ratio"}):
            result = compare_displayed_setups(IRP_V3_STEER10, IRP_BASELINE)
        self.assertEqual(result["counts"]["changed"], 0)
        self.assertEqual(result["counts"]["readouts_changed"], 3)

    def test_tolerance_only_bridges_display_units_and_missing_values_stay_unknown(self) -> None:
        sheet = {"chassis.left_front.ride_height": "4.252 in", "chassis.left_front.cold_pressure": "14.0 psi",
                 "chassis.front.cross_weight": "50.9%", "chassis.rear.rear_end_ratio": "5.57"}
        recording = {"chassis.left_front.ride_height": "108 mm", "chassis.left_front.cold_pressure": "97 kPa",
                     "chassis.front.cross_weight": "51.0%"}
        rows = self._rows(compare_displayed_setups(sheet, recording, scheme="export"))
        # 4.252 in is 108.0 mm; 14.0 psi is 96.5 kPa, within half a 1 kPa step.
        self.assertEqual(rows["chassis.left_front.ride_height"]["status"], "identical")
        self.assertEqual(rows["chassis.left_front.cold_pressure"]["status"], "identical")
        self.assertEqual(rows["chassis.front.cross_weight"]["status"], "changed", "Same unit: no tolerance.")
        self.assertEqual(rows["chassis.rear.rear_end_ratio"]["status"], "unknown", "Missing is never equal.")
        same_unit = self._rows(compare_displayed_setups({"Chassis.LeftFront.RideHeight": "108 mm"},
                                                        {"Chassis.LeftFront.RideHeight": "109 mm"}))
        self.assertEqual(same_unit["Chassis.LeftFront.RideHeight"]["status"], "changed")
        converted = self._rows(compare_displayed_setups({"Chassis.LeftFront.RideHeight": "4.29 in"},
                                                        {"Chassis.LeftFront.RideHeight": "108 mm"}))
        self.assertEqual(converted["Chassis.LeftFront.RideHeight"]["status"], "changed", "109.0 mm is a step away.")
        unreadable = self._rows(compare_displayed_setups({"Chassis.Front.CrossWeight": "—"}, {"Chassis.Front.CrossWeight": "—"}))
        self.assertEqual(unreadable["Chassis.Front.CrossWeight"]["status"], "unknown")

    def test_personal_file_names_name_no_track(self) -> None:
        from setup_catalog import _infer_filename_identity
        self.assertIsNone(_infer_filename_identity("claude IRP test v2 LFcamber")["track_hint"],
                          "The Setups page showed the track as 'Claude IRP Test V2 Lfcamber'.")
        self.assertIsNone(_infer_filename_identity("josh test")["track_hint"])
        shop = _infer_filename_identity("NOAPS_MaconiSetupShop IRP 26S4 R")
        self.assertEqual((shop["track_hint"], shop["track_basis"]), ("IRP", "file_name_convention"))
        self.assertEqual(_infer_filename_identity("Bristol 26S4 Q")["track_hint"], "Bristol")


if __name__ == "__main__":
    unittest.main()
