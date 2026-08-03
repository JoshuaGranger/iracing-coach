from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from setup_catalog import (  # noqa: E402
    SetupLimitError,
    catalog_setups,
    compare_setups,
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


if __name__ == "__main__":
    unittest.main()
