from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sto_garage as garage  # noqa: E402


NAMES = ["bias", "pressure", "readout", "gear", "future"]
FIELDS = (
    garage.Field("bias", "Synthetic bias", "%", minimum=Decimal(0), maximum=Decimal(100),
                 step=Decimal("0.5"), step_base=Decimal(0)),
    garage.Field("pressure", "Synthetic pressure", "unit", scale=Decimal("0.5"),
                 offset=Decimal(-1), step=Decimal("0.25"), step_base=Decimal(0)),
    garage.Field("readout", "Synthetic readout", None, editable=False),
    garage.Field("gear", "Synthetic gear", None,
                 choices=((Decimal(0), "Low"), (Decimal(1), "High"))),
)
PROFILE = garage.Profile("synthetic", 1, "Synthetic profile", ("synthetic\\car",),
                         garage.layout_signature(NAMES), FIELDS, ("Synthetic test evidence",))
VALUES = {"bias": "55.000000", "pressure": "12.000000", "readout": "5", "gear": "0", "future": "1"}


class StoGarageTests(unittest.TestCase):
    def merge(self, native=None, changes=None, profile_id=None, **kwargs):
        return garage.merge_changes(PROFILE, VALUES, {} if native is None else native,
                                    {} if changes is None else changes,
                                    PROFILE.profile_id if profile_id is None else profile_id, **kwargs)

    def assert_code(self, code, action, field=None, representation=None):
        with self.assertRaises(garage.GarageError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        if field is not None:
            self.assertEqual(raised.exception.field, field)
        if representation is not None:
            self.assertEqual(raised.exception.representation, representation)

    def test_profile_needs_material_native_car_and_exact_ordered_layout(self):
        self.assertIs(garage.select_profile("synthetic\\car", NAMES, True, (PROFILE,)), PROFILE)
        for car, names, verified in (("unknown\\car", NAMES, True),
                                     ("synthetic\\car", NAMES, False),
                                     ("synthetic\\car", list(reversed(NAMES)), True),
                                     ("synthetic\\car", NAMES + ["new"], True)):
            self.assertIsNone(garage.select_profile(car, names, verified, (PROFILE,)))

    def test_profile_id_changes_with_mapping_version_constraints_and_evidence(self):
        for profile in (replace(PROFILE, version=2), replace(PROFILE, evidence=("new evidence",)),
                        replace(PROFILE, fields=(replace(FIELDS[0], maximum=Decimal(90)),))):
            self.assertNotEqual(PROFILE.profile_id, profile.profile_id)

    def test_descriptor_has_only_verified_constraints_and_preserves_native_metadata(self):
        original = [{"name": name, "raw_value": value, "unit": "native", "editable": True}
                    for name, value in VALUES.items()]
        document = garage.decorate({"settings": original}, PROFILE)
        self.assertEqual(document["descriptor_version"], 2)
        self.assertEqual(document["garage_profile_id"], PROFILE.profile_id)
        bias = document["settings"][0]
        self.assertEqual(bias["raw_value"], "55.000000")
        self.assertEqual(bias["unit"], "native")
        self.assertEqual(bias["garage"]["value"], "55")
        self.assertEqual(bias["garage"]["step_base"], "0")
        self.assertNotIn("min", document["settings"][1]["garage"])
        self.assertNotIn("garage", document["settings"][-1])

    def test_unknown_profile_retains_native_fallback(self):
        result = garage.decorate({"settings": [{"name": "unknown", "raw_value": "1"}]}, None)
        self.assertIsNone(result["garage_profile_id"])
        self.assertNotIn("garage", result["settings"][0])

    def test_identity_and_affine_inverse_are_backend_authored(self):
        result = self.merge(changes={"bias": "55.5", "pressure": "5.25"})
        self.assertEqual(result, {"bias": "55.5", "pressure": "12.5"})
        self.assertEqual(FIELDS[1].display(result["pressure"]), Decimal("5.25"))

    def test_unchanged_display_numeric_spelling_never_rewrites_native_token(self):
        self.assertEqual(self.merge(changes={"bias": "+55.000", "pressure": "5.00000"}), {})
        self.assertEqual(self.merge(changes={"readout": "+5.00"}), {})

    def test_unchanged_existing_value_can_be_outside_current_verified_range(self):
        changed = replace(PROFILE, fields=(replace(FIELDS[0], maximum=Decimal(50)),))
        self.assertEqual(garage.merge_changes(changed, VALUES, {}, {"bias": "55"}, changed.profile_id), {})

    def test_input_is_not_clamped_or_rounded_onto_a_grid(self):
        for value in ("55.1", "55.4999999999", "0.000000000000001"):
            self.assert_code("garage_off_grid", lambda: self.merge(changes={"bias": value}), "bias")
        self.assert_code("garage_out_of_range", lambda: self.merge(changes={"bias": "100.5"}), "bias")
        self.assert_code("garage_out_of_range", lambda: self.merge(changes={"bias": "-0.5"}), "bias")

    def test_exact_decimal_step_does_not_use_binary_float_modulo(self):
        profile = replace(PROFILE, fields=(replace(FIELDS[0], step=Decimal("0.1")),))
        result = garage.merge_changes(profile, VALUES, {}, {"bias": "0.3"}, profile.profile_id)
        self.assertEqual(result["bias"], "0.3")

    def test_raw_route_cannot_bypass_verified_grid_bounds_or_choices(self):
        self.assert_code("garage_off_grid", lambda: self.merge(native={"bias": "55.1"}), "bias", "native")
        self.assert_code("garage_out_of_range", lambda: self.merge(native={"bias": "101"}), "bias", "native")
        self.assert_code("garage_unknown_choice", lambda: self.merge(native={"gear": "2"}), "gear", "native")
        self.assertEqual(self.merge(native={"bias": "55.500000"}), {"bias": "55.500000"})

    def test_changed_fields_only_are_validated(self):
        profile = replace(PROFILE, fields=(replace(FIELDS[0], maximum=Decimal(50)), FIELDS[1]))
        result = garage.merge_changes(profile, VALUES, {"pressure": "12.5"}, None)
        self.assertEqual(result, {"pressure": "12.5"})

    def test_choice_mapping_and_unknown_choice(self):
        self.assertEqual(self.merge(changes={"gear": "1"})["gear"], "1")
        self.assert_code("garage_unknown_choice", lambda: self.merge(changes={"gear": "2"}), "gear")
        descriptor = FIELDS[3].descriptor("0")
        self.assertEqual(descriptor["choices"][1], {"value": "1", "label": "High"})

    def test_missing_stale_and_unsupported_profile_fail_closed(self):
        for profile, profile_id in ((PROFILE, None), (PROFILE, "old"), (None, PROFILE.profile_id)):
            self.assert_code("garage_profile_mismatch",
                             lambda: garage.merge_changes(profile, VALUES, {}, {"bias": "56"}, profile_id))
        self.assert_code("garage_profile_mismatch", lambda: self.merge(descriptor_version=1))
        self.assert_code("garage_profile_mismatch", lambda: self.merge(descriptor_version=True))

    def test_ambiguous_representation_is_rejected_even_if_native_value_is_unchanged(self):
        self.assert_code("ambiguous_change", lambda: self.merge(native={"bias": "55.000000"},
                                                               changes={"bias": "56"}), "bias")

    def test_unknown_and_display_only_fields_are_not_editable_in_garage_mode(self):
        for name in ("future", "absent", "readout"):
            self.assert_code("garage_unmapped_field", lambda: self.merge(changes={name: "1"}), name)

    def test_invalid_numeric_inputs_are_rejected_with_field_identity(self):
        for value in ("NaN", "inf", "1e9999999", "1e-9999999", " 56", "56 psi", 56, None, True):
            self.assert_code("invalid_garage_value", lambda: self.merge(changes={"bias": value}), "bias", "garage")

    def test_legacy_raw_tiny_exponent_is_rejected_before_exact_grid_arithmetic(self):
        self.assert_code("invalid_garage_value", lambda: self.merge(native={"bias": "1e-999999999999"}),
                         "bias", "native")

    def test_huge_native_exponent_remains_native_only_without_expanding_decimal_text(self):
        document = garage.decorate({"settings": [{"name": "bias", "raw_value": "1e999999999999", "editable": False}]}, PROFILE)
        self.assertNotIn("garage", document["settings"][0])

    def test_profile_step_requires_verified_origin(self):
        with self.assertRaises(ValueError):
            garage.Field("test", "Test", None, step=Decimal(1))

    def test_inexact_inverse_does_not_silently_change_requested_value(self):
        field = garage.Field("test", "Test", None, native_places=2)
        self.assert_code("invalid_garage_value", lambda: field.inverse(Decimal("1.001")), "test")

    def test_inverse_rejects_unbounded_native_result_before_formatting(self):
        field = garage.Field("test", "Test", None, scale=Decimal("1e-10000"))
        self.assert_code("invalid_garage_value", lambda: field.inverse(Decimal(1)), "test")

    def test_raw_range_validation_does_not_round_outside_value_inside_range(self):
        field = garage.Field("bias", "Synthetic rounded display", None, display_places=0,
                             minimum=Decimal(0), maximum=Decimal(1))
        profile = replace(PROFILE, fields=(field,))
        self.assert_code("garage_out_of_range", lambda: garage.merge_changes(profile, VALUES, {"bias": "1.01"}),
                         "bias", "native")

    def test_unrepresentable_optional_garage_value_preserves_native_fallback(self):
        field = garage.Field("pressure", "Synthetic display", None, float32_display=True)
        profile = replace(PROFILE, fields=(field,))
        result = garage.decorate({"settings": [{"name": "pressure", "raw_value": "1e1000", "editable": False}]}, profile)
        self.assertNotIn("garage", result["settings"][0])

    def test_all_four_pressure_mappings_have_verified_inverse_and_no_invented_limits(self):
        for corner in ("lf", "rf", "lr", "rr"):
            pressure = next(field for field in garage.PROFILES[0].fields if field.name == f"{corner}TireColdPressPa")
            descriptor = pressure.descriptor("100000.000000")  # Synthetic, not a source value.
            self.assertEqual(descriptor["unit"], "psi")
            self.assertTrue(descriptor["editable"])
            self.assertEqual(descriptor["step"], "0.5")
            self.assertNotIn("min", descriptor)
            self.assertNotIn("max", descriptor)
            self.assertEqual(pressure.inverse(Decimal(100)), "689116.000000")

    def test_verified_pressure_inverse_obeys_float32_six_decimal_native_storage(self):
        pressure = next(field for field in garage.PROFILES[0].fields if field.name == "lfTireColdPressPa")
        # A synthetic target, not a copied setup or acceptance-file value.
        raw = pressure.inverse(Decimal("100"))
        self.assertEqual(raw, "689116.000000")
        self.assertEqual(pressure.display(raw), Decimal("100"))
        self.assertEqual(pressure.descriptor(raw)["step"], "0.5")

    def test_only_measured_lf_compression_has_click_limits(self):
        fields = {field.name: field for field in garage.PROFILES[0].fields}
        compression = fields["lfLowSpeedCompDampSetting"]
        self.assertEqual(compression.inverse(Decimal(4)), "4")
        self.assertEqual(compression.descriptor("4")["min"], "1")
        self.assertEqual(compression.descriptor("4")["max"], "10")
        self.assert_code("garage_out_of_range", lambda: compression.validate(Decimal(0), "garage"))
        self.assert_code("garage_off_grid", lambda: compression.validate(Decimal("4.5"), "garage"))
        self.assertNotIn("rfLowSpeedCompDampSetting", fields)
        ratio = fields["steeringBoxRatio"].descriptor("20")  # Synthetic ratio.
        self.assertEqual(ratio["unit"], ":1")
        self.assertNotIn("step", ratio)
        self.assertNotIn("min", ratio)


if __name__ == "__main__":
    unittest.main()
