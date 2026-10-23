from __future__ import annotations

import base64
from dataclasses import replace
from decimal import Decimal
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sto_codec as codec  # noqa: E402

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None


# Entirely synthetic: no simulator keys, table extracts, purchased values or cars.
MATERIAL = codec.Material(bytes(range(32)), bytes((i % 128) ^ 0xDA for i in range(256)), bytes(range(128)))
TEXT = ("// <i>\r\n// <v>0</v>\r\n// <c>synthetic\\testcar</c>\r\n// </i>\r\n"
        "// future comment stays byte-for-byte\r\n  +12.500000\tfuelFillToKg !  \r\n"
        "55.000000 fBrakeBias !\n1e+2 unknownFutureParameter !\r\n"
        "120000.000000 lfTireColdPressPa !\r\n")


def container(text: str = TEXT, notes: str = "Synthetic notes 🏁", trailer: bytes = b"opaque\x00metadata" + bytes(8),
              note_bytes: bytes | None = None) -> bytes:
    if AES is None:
        raise RuntimeError("Tests require pycryptodome")
    encoded = bytes(MATERIAL.encoding[c] for c in text.encode("ascii")) + b"\0"
    pad = 16 - len(encoded) % 16
    encoded += bytes([pad]) * pad
    file_key = bytes(range(32, 64))
    inner = AES.new(file_key, AES.MODE_CBC, iv=bytes(16)).encrypt(encoded)
    encrypted = AES.new(MATERIAL.outer_key, AES.MODE_CBC, iv=bytes(16)).encrypt(inner)
    note_bytes = notes.encode("utf-16le") + b"\0\0" if note_bytes is None else note_bytes
    return (struct.pack("<4I", 3, len(encrypted) + len(note_bytes) + 40, len(encrypted), len(note_bytes))
            + file_key + encrypted + note_bytes + trailer)


@unittest.skipIf(AES is None, "STO codec tests require pycryptodome")
class StoCodecTests(unittest.TestCase):
    def garage_profile(self):
        garage = codec.sto_garage
        names = ["fuelFillToKg", "fBrakeBias", "unknownFutureParameter", "lfTireColdPressPa"]
        return garage.Profile("synthetic-codec", 1, "Synthetic codec profile", ("synthetic\\testcar",),
                              garage.layout_signature(names),
                              (garage.Field("fBrakeBias", "Synthetic bias", "%", minimum=Decimal(0),
                                            maximum=Decimal(100), step=Decimal("0.5"), step_base=Decimal(0)),),
                              ("Synthetic test evidence",))

    def test_garage_noop_preserves_original_container_and_decimal_spelling(self) -> None:
        source, profile = container(), self.garage_profile()
        with mock.patch.object(codec, "_garage_profile", return_value=profile):
            self.assertEqual(codec.encode(source, MATERIAL, garage_changes={"fBrakeBias": "55.00"},
                                          garage_profile_id=profile.profile_id), source)

    def test_garage_edits_convert_in_python_and_preserve_other_native_tokens(self) -> None:
        source, profile = container(), self.garage_profile()
        with mock.patch.object(codec, "_garage_profile", return_value=profile):
            result = codec.encode(source, MATERIAL, garage_changes={"fBrakeBias": "55.5"},
                                  garage_profile_id=profile.profile_id, descriptor_version=2)
        self.assertEqual(codec._unpack(result, MATERIAL).text, TEXT.replace("55.000000", "55.500000"))

    def test_legacy_native_route_cannot_bypass_verified_constraints(self) -> None:
        profile = self.garage_profile()
        with mock.patch.object(codec, "_garage_profile", return_value=profile), self.assertRaises(codec.StoError) as raised:
            codec.encode(container(), MATERIAL, {"fBrakeBias": "55.1"})
        self.assertEqual(raised.exception.code, "garage_off_grid")
        self.assertEqual(raised.exception.field, "fBrakeBias")
        self.assertEqual(raised.exception.representation, "native")

    def test_native_and_garage_overlap_cannot_hide_in_unchanged_native_values(self) -> None:
        profile = self.garage_profile()
        with mock.patch.object(codec, "_garage_profile", return_value=profile), self.assertRaises(codec.StoError) as raised:
            codec.encode(container(), MATERIAL, {"fBrakeBias": "55.000000"},
                         garage_changes={"fBrakeBias": "55.5"}, garage_profile_id=profile.profile_id)
        self.assertEqual(raised.exception.code, "ambiguous_change")

    def test_unknown_native_car_and_unverified_material_have_native_fallback(self) -> None:
        document = codec.decode(container(), MATERIAL)
        self.assertEqual(document["descriptor_version"], 2)
        self.assertIsNone(document["garage_profile_id"])
        self.assertTrue(all("garage" not in setting for setting in document["settings"]))
        with self.assertRaises(codec.StoError) as raised:
            codec.encode(container(), MATERIAL, garage_changes={"fBrakeBias": "55.5"}, garage_profile_id="unknown")
        self.assertEqual(raised.exception.code, "garage_profile_mismatch")

    def test_cli_propagates_garage_profile_error_and_field_details(self) -> None:
        profile = self.garage_profile()
        request = {"operation": "encode", "profile_path": "private-profile.json",
                   "data_base64": base64.b64encode(container()).decode(), "descriptor_version": 2,
                   "garage_profile_id": profile.profile_id, "garage_changes": {"fBrakeBias": "55.1"}}
        stdin, stdout = mock.Mock(buffer=io.BytesIO(json.dumps(request).encode())), io.StringIO()
        with mock.patch.object(codec, "material_from_profile", return_value=MATERIAL), mock.patch.object(codec, "_garage_profile", return_value=profile), mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
            self.assertEqual(codec.main([]), 2)
        error = json.loads(stdout.getvalue())["error"]
        self.assertEqual(error["code"], "garage_off_grid")
        self.assertEqual(error["field"], "fBrakeBias")
        self.assertEqual(error["representation"], "garage")

    def test_all_native_records_are_displayed_without_garage_renaming(self) -> None:
        doc = codec.decode(container(), MATERIAL)
        self.assertEqual(doc["internal_car_path"], "synthetic\\testcar")
        self.assertEqual(doc["notes"], "Synthetic notes 🏁")
        self.assertEqual(len(doc["settings"]), 4)
        self.assertEqual(doc["settings"][0]["raw_value"], "+12.500000")
        self.assertEqual(doc["settings"][2]["label"], "unknownFutureParameter")
        self.assertIsNone(doc["settings"][2]["unit"])
        self.assertEqual(doc["settings"][3]["unit"], "Pa")
        self.assertEqual(doc["settings"][3]["group"], "Left front")
        self.assertTrue(doc["native_values"])

    def test_no_edits_return_identical_original_bytes(self) -> None:
        source = container()
        self.assertEqual(codec.encode(source, MATERIAL), source)
        self.assertEqual(codec.encode(source, MATERIAL, {"fuelFillToKg": "+12.500000"}, "Synthetic notes 🏁"), source)

    def test_one_numeric_token_changes_preserving_all_other_text_and_opaque_bytes(self) -> None:
        source = container()
        updated = codec.encode(source, MATERIAL, {"fuelFillToKg": "+13.75"})
        original, edited = codec._unpack(source, MATERIAL), codec._unpack(updated, MATERIAL)
        self.assertEqual(edited.text, TEXT.replace("+12.500000", "13.750000"))
        self.assertEqual(edited.file_key, original.file_key)
        self.assertEqual(edited.trailer, original.trailer)
        self.assertEqual(edited.note_bytes, original.note_bytes)
        self.assertEqual(edited.car, original.car)

    def test_multiple_replacements_can_grow_and_shrink_blocks_without_touching_unknowns(self) -> None:
        source = container()
        updated = codec.encode(source, MATERIAL, {"fuelFillToKg": "1", "fBrakeBias": "55.123456789"})
        result = codec._unpack(updated, MATERIAL)
        self.assertEqual(result.text, TEXT.replace("+12.500000", "1.000000").replace("55.000000", "55.123456789"))
        self.assertIn("1e+2 unknownFutureParameter", result.text)
        _, main_length, encrypted_length, notes_length = struct.unpack_from("<4I", updated)
        self.assertEqual(main_length, encrypted_length + notes_length + 40)

    def test_notes_only_keep_encrypted_settings_exact(self) -> None:
        source = container()
        updated = codec.encode(source, MATERIAL, notes="Changed\r\nUnicode 🏎")
        original, edited = codec._unpack(source, MATERIAL), codec._unpack(updated, MATERIAL)
        self.assertEqual(edited.encrypted, original.encrypted)
        self.assertEqual(edited.trailer, original.trailer)
        self.assertEqual(edited.notes, "Changed\r\nUnicode 🏎")

    def test_empty_notes_and_original_zero_length_notes(self) -> None:
        source = container(note_bytes=b"")
        self.assertEqual(codec.decode(source, MATERIAL)["notes"], "")
        self.assertEqual(codec.encode(source, MATERIAL, notes=""), source)
        self.assertEqual(codec.decode(codec.encode(container(), MATERIAL, notes=""), MATERIAL)["notes"], "")

    def test_unknown_present_parameter_can_be_preserved_or_explicitly_edited(self) -> None:
        result = codec.encode(container(), MATERIAL, {"unknownFutureParameter": "101"})
        self.assertEqual(codec.decode(result, MATERIAL)["settings"][2]["raw_value"], "101.000000")

    def test_changed_integers_and_exponents_use_native_float_grammar_without_rounding(self) -> None:
        # Native assignment starts only for a floating token. Bare integer tokens
        # leave parameters at their native defaults; this oracle tests that grammar.
        cases = (("4", "4.000000"), ("-4", "-4.000000"), ("+4e1", "40.000000"),
                 ("1e-7", "0.0000001"), ("4.123456789", "4.123456789"),
                 ("0e-9999999", "0.000000"))
        for requested, expected in cases:
            with self.subTest(requested=requested):
                result = codec.encode(container(), MATERIAL, {"fuelFillToKg": requested})
                unpacked = codec._unpack(result, MATERIAL)
                raw = unpacked.records[0][1]
                self.assertEqual(raw, expected)
                self.assertRegex(raw, r"^-?\d+\.\d+$")
                self.assertEqual(Decimal(raw), Decimal(requested))
                self.assertEqual(unpacked.text, TEXT.replace("+12.500000", expected))

    def test_native_float_expansion_is_bounded_before_serialization(self) -> None:
        for value in ("1e-9999999", "1e9999999", "1e-100", "-1e-100"):
            with self.subTest(value=value), self.assertRaises(codec.StoError) as raised:
                codec.encode(container(), MATERIAL, {"fuelFillToKg": value})
            self.assertEqual(raised.exception.code, "invalid_edit")
            self.assertEqual(raised.exception.field, "fuelFillToKg")
        precise = "0." + "0" * 59 + "1"
        result = codec.encode(container(), MATERIAL, {"fuelFillToKg": precise})
        self.assertEqual(codec._unpack(result, MATERIAL).records[0][1], precise)

    def test_numeric_noops_preserve_original_integer_exponent_and_float_tokens(self) -> None:
        source = container(TEXT.replace("55.000000", "55"))
        self.assertEqual(codec.encode(source, MATERIAL, {"fBrakeBias": "55.0",
                                                       "unknownFutureParameter": "100.000000",
                                                       "fuelFillToKg": "12.5"}), source)

    def test_unsupported_original_notation_has_native_fallback_warning_and_exact_noop(self) -> None:
        profile = replace(self.garage_profile(), native_float_syntax_required=True)
        for token in ("55", "5.5e1"):
            source = container(TEXT.replace("55.000000", token))
            with self.subTest(token=token), mock.patch.object(codec, "_garage_profile", return_value=profile):
                document = codec.decode(source, MATERIAL)
                setting = document["settings"][1]
                self.assertNotIn("garage", setting)
                self.assertEqual(setting["raw_value"], token)
                self.assertTrue(setting["editable"])
                self.assertEqual(document["warnings"].count(codec.sto_garage.NATIVE_SYNTAX_WARNING), 1)
                self.assertEqual(codec.encode(source, MATERIAL, {"fBrakeBias": "55.000000"}), source)

    def test_garage_and_legacy_integer_routes_have_identical_native_float_output(self) -> None:
        source, profile = container(), self.garage_profile()
        with mock.patch.object(codec, "_garage_profile", return_value=profile):
            garage_result = codec.encode(source, MATERIAL, garage_changes={"fBrakeBias": "56"},
                                         garage_profile_id=profile.profile_id)
            native_result = codec.encode(source, MATERIAL, {"fBrakeBias": "56"})
        self.assertEqual(garage_result, native_result)
        self.assertEqual(codec._unpack(native_result, MATERIAL).records[1][1], "56.000000")

    def test_absent_parameter_is_not_injected(self) -> None:
        with self.assertRaises(codec.StoError) as raised:
            codec.encode(container(), MATERIAL, {"notInThisFile": "1"})
        self.assertEqual(raised.exception.code, "invalid_edit")

    def test_unsafe_numeric_edits_are_rejected(self) -> None:
        for value in ("NaN", "inf", "1e9999999", "1\nmalicious !", "1,5", " 1", 1, None, True, "9" * 65):
            with self.subTest(value=value), self.assertRaises(codec.StoError):
                codec.encode(container(), MATERIAL, {"fuelFillToKg": value})

    def test_invalid_change_shape_is_rejected(self) -> None:
        for changes in ([], 1, "text"):
            with self.subTest(changes=changes), self.assertRaises(codec.StoError):
                codec.encode(container(), MATERIAL, changes)

    def test_native_number_above_edit_bound_is_visible_but_read_only(self) -> None:
        source = container(TEXT.replace("1e+2", "1e+20"))
        self.assertFalse(codec.decode(source, MATERIAL)["settings"][2]["editable"])
        with self.assertRaises(codec.StoError):
            codec.encode(source, MATERIAL, {"unknownFutureParameter": "1"})

    def test_notes_native_limit_counts_utf16_bytes_including_terminator(self) -> None:
        source = container()
        limit = "A" * (codec.MAX_NOTES_BYTES // 2 - 1)
        self.assertEqual(codec.decode(codec.encode(source, MATERIAL, notes=limit), MATERIAL)["notes"], limit)
        for notes in (limit + "A", "🏁" * (codec.MAX_NOTES_BYTES // 4), "embedded\0terminator", "\ud800", 42):
            with self.subTest(notes=repr(notes)[:30]), self.assertRaises(codec.StoError):
                codec.encode(source, MATERIAL, notes=notes)

    def test_duplicate_native_parameters_fail_instead_of_choosing_one(self) -> None:
        with self.assertRaises(codec.StoError):
            codec.decode(container(TEXT + "1 fuelFillToKg !\n"), MATERIAL)

    def test_unknown_noncomment_record_fails_without_partial_values(self) -> None:
        for extra in ("native garbage\n", "NaN future !\n", "1e9999999999999999999999999 future !\n", "\x01"):
            with self.subTest(extra=extra), self.assertRaises(codec.StoError):
                codec.decode(container(TEXT + extra), MATERIAL)

    def test_ambiguous_or_absent_car_fails(self) -> None:
        for text in (TEXT.replace("<c>", "<x>"), TEXT + "// <c>another</c>\n", "// <c>synthetic</c>\n"):
            with self.subTest(text=text[:20]), self.assertRaises(codec.StoError):
                codec.decode(container(text), MATERIAL)

    def test_bad_padding_and_wrong_material_fail(self) -> None:
        source = container()
        corrupted = bytearray(source)
        encrypted_length = struct.unpack_from("<I", source, 8)[0]
        corrupted[48 + encrypted_length - 1] ^= 0x80
        with self.assertRaises(codec.StoError):
            codec.decode(bytes(corrupted), MATERIAL)
        wrong = codec.Material(bytes(reversed(range(32))), MATERIAL.translation, MATERIAL.encoding)
        with self.assertRaises(codec.StoError):
            codec.decode(source, wrong)

    def test_wrong_versions_lengths_and_truncation_fail(self) -> None:
        source = container()
        for offset, value in ((0, 2), (4, 0), (8, 1), (8, 0), (8, 0xFFFFFFF0), (12, 1), (12, 0x4002)):
            broken = bytearray(source)
            struct.pack_into("<I", broken, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(codec.StoError):
                codec.decode(bytes(broken), MATERIAL)
        for size in (0, 55, 64, len(source) - 20):
            with self.subTest(size=size), self.assertRaises(codec.StoError):
                codec.decode(source[:size], MATERIAL)

    def test_invalid_utf16_and_embedded_note_terminator_fail(self) -> None:
        for note_bytes in (b"x\x00", b"\x00\xd8\x00\x00", "a\0b\0".encode("utf-16le")):
            with self.subTest(note_bytes=note_bytes), self.assertRaises(codec.StoError):
                codec.decode(container(note_bytes=note_bytes), MATERIAL)

    def test_profile_tables_validate_both_encoding_and_native_nul(self) -> None:
        for material in (codec.Material(b"short", MATERIAL.translation, MATERIAL.encoding),
                         codec.Material(MATERIAL.outer_key, bytes(256), MATERIAL.encoding),
                         codec.Material(MATERIAL.outer_key, MATERIAL.translation, bytes(128))):
            with self.assertRaises(codec.StoError):
                codec.decode(container(), material)

    def test_duplicate_json_edits_and_nonfinite_constants_fail(self) -> None:
        for text in ('{"changes":{"fuel":"1","fuel":"2"}}', '{"value":NaN}', '{"value":Infinity}', '{'):
            with self.subTest(text=text), self.assertRaises(codec.StoError):
                codec._json(text)

    def test_profile_rejects_unverified_build_and_modified_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"schema": 1, "binary_sha256": "0" * 64}))
            with self.assertRaises(codec.StoError):
                codec.material_from_profile(path)
            path.write_text(json.dumps({"schema": 1, "binary_sha256": codec.BINARY_SHA256,
                                        "material_sha256": codec.MATERIAL_SHA256,
                                        "outer_key_base64": base64.b64encode(MATERIAL.outer_key).decode(),
                                        "translation_base64": base64.b64encode(MATERIAL.translation).decode(),
                                        "encoding_base64": base64.b64encode(MATERIAL.encoding).decode()}))
            with self.assertRaises(codec.StoError):
                codec.material_from_profile(path)
            with self.assertRaises(codec.StoError):
                codec.profile_for_material(MATERIAL)

    def test_binary_is_hash_pinned_before_material_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sim.exe"
            path.write_bytes(b"MZ" + bytes(2048))
            with self.assertRaises(codec.StoError) as raised:
                codec.material_from_binary(path)
            self.assertEqual(raised.exception.code, "material_unavailable")

    def test_cli_request_returns_document_and_new_bytes_without_writing_files(self) -> None:
        data = container()
        request = {"schema": 1, "operation": "encode", "profile_path": "private-profile.json",
                   "data_base64": base64.b64encode(data).decode(), "changes": {"fBrakeBias": "56"}, "notes": "Revision"}
        with mock.patch.object(codec, "material_from_profile", return_value=MATERIAL):
            response = codec.process_request(request)
        self.assertTrue(response["ok"])
        self.assertEqual(response["schema"], 1)
        updated = base64.b64decode(response["data_base64"])
        self.assertNotEqual(updated, data)
        self.assertEqual(response["document"]["settings"][1]["raw_value"], "56.000000")
        self.assertEqual(response["document"]["notes"], "Revision")

    def test_cli_rejects_invalid_request_or_base64(self) -> None:
        for request in ([], {"schema": 2}, {"operation": "other"}, {"operation": "decode"},
                        {"operation": "decode", "profile_path": "private", "data_base64": "!"}):
            with self.subTest(request=request), mock.patch.object(codec, "material_from_profile", return_value=MATERIAL), self.assertRaises(codec.StoError):
                codec.process_request(request)

    def test_cli_errors_are_json_and_do_not_include_private_path_or_value(self) -> None:
        request = b'{"operation":"decode","profile_path":"C:/private/secret.json","data_base64":""}'
        fake_stdin = mock.Mock(buffer=io.BytesIO(request))
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", fake_stdin), mock.patch.object(sys, "stdout", stdout):
            code = codec.main([])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "material_unavailable")
        self.assertNotIn("secret.json", stdout.getvalue())

    def test_profile_extraction_never_overwrites_existing_profile_or_writes_into_repo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text("existing")
            with mock.patch.object(codec, "material_from_binary", return_value=MATERIAL), mock.patch.object(codec, "profile_for_material", return_value={"schema": 1}), mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(codec.main(["--extract-profile", "offline.exe", "--output", str(profile)]), 2)
            self.assertEqual(profile.read_text(), "existing")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(codec.main(["--extract-profile", "offline.exe", "--output", str(SCRIPTS / "never-created.json")]), 2)
        self.assertFalse((SCRIPTS / "never-created.json").exists())


if __name__ == "__main__":
    unittest.main()
