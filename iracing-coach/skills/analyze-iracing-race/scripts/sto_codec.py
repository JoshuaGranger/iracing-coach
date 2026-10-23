"""Lossless native STO v3 editing, with private material from one verified build.

This is a file codec, not a garage model: native values carry no claim about car
compatibility, displayed geometry, allowed adjustment steps or technical legality.
The caller owns immutable source files and revision history. No operation here
writes a setup file. Crypto material is provisioned privately, never in source.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
from typing import Any

import sto_garage


SCHEMA_VERSION = 1
MAX_STO_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_NOTES_BYTES = 0x4000  # Native v3 writer's limit, including the UTF-16 terminator.
MAX_FIELDS = 8192
MAX_NUMBER_CHARS = 64
MAX_NATIVE_MAGNITUDE = Decimal("1e12")  # Codec abuse bound; not a car adjustment limit.
BINARY_SHA256 = "6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362"
MATERIAL_SHA256 = "233027744ac572091fa862faa36d5813d67680a57bf0a8018906d1a513bd2999"
KEY_RVA = 0x1191C748
TRANSLATION_RVA = 0x1FCA2D0
ENCODING_RVA = 0x1FCA250
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUMBER = re.compile(NUMBER_PATTERN, re.ASCII)
RECORD = re.compile(r"^([ \t]*)(" + NUMBER_PATTERN + r")([ \t]+)(\S+)([ \t]+![ \t]*)$", re.ASCII)
CAR = re.compile(r"<c>([^<>\r\n]+)</c>")
WARNINGS = [
    "Native file values may differ from garage values. After loading, check the garage and run Tech in iRacing.",
]


class StoError(ValueError):
    """A safe error with a stable machine code and no private data in its message."""

    def __init__(self, message: str, code: str = "invalid_sto", field: str | None = None,
                 representation: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.representation = representation


@dataclass(frozen=True)
class Material:
    outer_key: bytes
    translation: bytes
    encoding: bytes

    def validate(self) -> None:
        if len(self.outer_key) != 32 or len(self.translation) != 256 or len(self.encoding) != 128:
            raise StoError("Invalid STO material profile.", "material_unavailable")
        if any(((self.translation[self.encoding[i]] ^ 0xDA) & 0x7F) != i for i in range(128)):
            raise StoError("STO profile encoding tables disagree.", "material_unavailable")
        if ((self.translation[0] ^ 0xDA) & 0x7F) != 0:
            raise StoError("STO profile terminator mapping disagrees.", "material_unavailable")


@dataclass(frozen=True)
class _Container:
    original: bytes
    encrypted: bytes
    file_key: bytes
    note_bytes: bytes
    trailer: bytes
    text: str
    # (name, raw value, number start offset, number end offset), in original order.
    records: tuple[tuple[str, str, int, int], ...]
    car: str
    notes: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapped_bytes(binary: bytes, rva: int, count: int) -> bytes:
    """Map observed PE RVAs to complete original file bytes, never live memory."""
    try:
        if binary[:2] != b"MZ":
            raise StoError("The copied simulator executable is not PE.", "material_unavailable")
        pe = struct.unpack_from("<I", binary, 0x3C)[0]
        if binary[pe:pe + 4] != b"PE\0\0":
            raise StoError("Invalid PE signature.", "material_unavailable")
        machine, sections = struct.unpack_from("<HH", binary, pe + 4)
        optional_size = struct.unpack_from("<H", binary, pe + 20)[0]
        if machine != 0x8664 or sections > 128:
            raise StoError("Unsupported PE layout.", "material_unavailable")
        for index in range(sections):
            entry = pe + 24 + optional_size + index * 40
            _, start, raw_size, raw = struct.unpack_from("<IIII", binary, entry + 8)
            offset = rva - start
            if 0 <= offset and offset + count <= raw_size:
                result = binary[raw + offset:raw + offset + count]
                if len(result) == count:
                    return result
    except struct.error as exc:
        raise StoError("Truncated PE header.", "material_unavailable") from exc
    raise StoError("Required STO material has no complete file mapping.", "material_unavailable")


def _offline_copy(path: Path) -> Path:
    path = path.resolve()
    parts = [part.casefold() for part in path.parts]
    if any(parts[i:i + 2] == ["documents", "iracing"] for i in range(len(parts) - 1)):
        raise StoError("Use an offline executable copy outside Documents/iRacing.", "material_unavailable")
    return path


def material_from_binary(path: Path) -> Material:
    path = _offline_copy(path)
    if not 1024 <= path.stat().st_size <= 512 * 1024 * 1024:
        raise StoError("Copied executable has an unsupported size.", "material_unavailable")
    binary = path.read_bytes()
    if _sha(binary) != BINARY_SHA256:
        raise StoError("Unverified simulator build. Install a profile from the verified offline build.", "material_unavailable")
    material = Material(_mapped_bytes(binary, KEY_RVA, 32),
                        _mapped_bytes(binary, TRANSLATION_RVA, 256),
                        _mapped_bytes(binary, ENCODING_RVA, 128))
    material.validate()
    if _sha(material.outer_key + material.translation + material.encoding) != MATERIAL_SHA256:
        raise StoError("Verified build material digest disagrees.", "material_unavailable")
    return material


def profile_for_material(material: Material) -> dict[str, Any]:
    """Private provisioning payload. Never include it in logs, fixtures or Git."""
    material.validate()
    digest = _sha(material.outer_key + material.translation + material.encoding)
    if digest != MATERIAL_SHA256:
        raise StoError("Only verified build material can be provisioned.", "material_unavailable")
    return {"schema": SCHEMA_VERSION, "binary_sha256": BINARY_SHA256,
            "material_sha256": digest,
            "outer_key_base64": base64.b64encode(material.outer_key).decode("ascii"),
            "translation_base64": base64.b64encode(material.translation).decode("ascii"),
            "encoding_base64": base64.b64encode(material.encoding).decode("ascii")}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StoError("Duplicate JSON member.", "invalid_request")
        result[key] = value
    return result


def _json(text: str) -> Any:
    def bad_constant(_: str) -> None:
        raise StoError("Non-finite JSON number.", "invalid_request")
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=bad_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StoError("Invalid JSON request.", "invalid_request") from exc


def material_from_profile(path: Path) -> Material:
    try:
        if path.stat().st_size > 4096:
            raise StoError("Invalid STO material profile.", "material_unavailable")
        profile = _json(path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or profile.get("schema") != SCHEMA_VERSION:
            raise StoError("Unsupported STO profile version.", "material_unavailable")
        if profile.get("binary_sha256") != BINARY_SHA256 or profile.get("material_sha256") != MATERIAL_SHA256:
            raise StoError("Unverified STO material profile.", "material_unavailable")
        values = [base64.b64decode(profile[key], validate=True)
                  for key in ("outer_key_base64", "translation_base64", "encoding_base64")]
        if _sha(b"".join(values)) != MATERIAL_SHA256:
            raise StoError("STO material profile failed its integrity check.", "material_unavailable")
        material = Material(*values)
        material.validate()
        return material
    except (OSError, UnicodeError, KeyError, TypeError, binascii.Error) as exc:
        raise StoError("STO decoding material is unavailable. Install the verified private profile.", "material_unavailable") from exc


def _aes() -> Any:
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise StoError("STO support needs the Python pycryptodome dependency.", "dependency_unavailable") from exc
    return AES


def _records(text: str) -> tuple[str, tuple[tuple[str, str, int, int], ...]]:
    if any(ord(c) < 32 and c not in "\t\r\n" for c in text) or "\x7f" in text:
        raise StoError("Decoded settings contain unsupported control characters.")
    cars = CAR.findall(text)
    if len(cars) != 1 or len(cars[0]) > 256:
        raise StoError("Decoded settings need one unambiguous car identifier.")
    result: list[tuple[str, str, int, int]] = []
    names: set[str] = set()
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.strip() and not body.lstrip().startswith("//"):
            match = RECORD.fullmatch(body)
            if match is None:
                raise StoError("Unrecognized native setting record; no partial result returned.")
            name, value = match.group(4), match.group(2)
            if len(name) > 128 or "!" in name or name in names:
                raise StoError("Ambiguous, repeated or unsupported native setting name.")
            if len(value) > MAX_NUMBER_CHARS:
                raise StoError("Native numeric spelling exceeds the supported size.")
            try:
                number = Decimal(value)
                if not number.is_finite():
                    raise StoError("Native setting is not finite.")
            except InvalidOperation as exc:
                raise StoError("Invalid native numeric setting.") from exc
            names.add(name)
            result.append((name, value, offset + match.start(2), offset + match.end(2)))
            if len(result) > MAX_FIELDS:
                raise StoError("Too many native setting records.")
        offset += len(line)
    if not result:
        raise StoError("Decoded settings have no native parameter records.")
    return cars[0], tuple(result)


def _unpack(data: bytes, material: Material) -> _Container:
    if not isinstance(data, bytes) or not 56 <= len(data) <= MAX_STO_BYTES:
        raise StoError("STO must be between 56 bytes and 16 MiB.")
    version, main_length, encrypted_length, notes_length = struct.unpack_from("<4I", data)
    if version != 3:
        raise StoError("Unsupported STO version; only verified v3 files are supported.", "unsupported_version")
    if not encrypted_length or encrypted_length % 16:
        raise StoError("Invalid encrypted settings length.")
    if notes_length % 2 or notes_length > MAX_NOTES_BYTES:
        raise StoError("Invalid native UTF-16 notes length.")
    if main_length != encrypted_length + notes_length + 40:
        raise StoError("Inconsistent v3 container lengths.")
    notes_start = 48 + encrypted_length
    notes_end = notes_start + notes_length
    if notes_end + 8 > len(data):
        raise StoError("Truncated settings, notes or v3 trailer.")
    material.validate()
    aes = _aes()
    encrypted = data[48:notes_start]
    intermediate = aes.new(material.outer_key, aes.MODE_CBC, iv=bytes(16)).decrypt(encrypted)
    encoded = aes.new(data[16:48], aes.MODE_CBC, iv=bytes(16)).decrypt(intermediate)
    padding = encoded[-1]
    if not 1 <= padding <= 16 or encoded[-padding:] != bytes([padding]) * padding:
        raise StoError("Invalid decrypted padding; file or build may be incompatible.")
    translated = bytes((material.translation[c] ^ 0xDA) & 0x7F for c in encoded[:-padding])
    if not translated.endswith(b"\0") or b"\0" in translated[:-1]:
        raise StoError("Decoded setting stream is not one terminated text record.")
    text = translated[:-1].decode("ascii")
    car, records = _records(text)
    note_bytes = data[notes_start:notes_end]
    if note_bytes and not note_bytes.endswith(b"\0\0"):
        raise StoError("Notes lack their UTF-16 terminator.")
    try:
        notes = note_bytes[:-2].decode("utf-16le") if note_bytes else ""
    except UnicodeDecodeError as exc:
        raise StoError("Invalid UTF-16 notes.") from exc
    if "\0" in notes:
        raise StoError("Notes contain an embedded terminator.")
    return _Container(data, encrypted, data[16:48], note_bytes, data[notes_end:], text, records, car, notes)


def _descriptor(name: str, value: str) -> dict[str, Any]:
    group = "General"
    for prefix, label in (("lf", "Left front"), ("rf", "Right front"),
                          ("lr", "Left rear"), ("rr", "Right rear")):
        if name.startswith(prefix) and len(name) > 2 and name[2].isupper():
            group = label
            break
    if group == "General" and re.match(r"[fr][A-Z]", name):
        group = "Front" if name[0] == "f" else "Rear"
    unit = next((unit for suffix, unit in (("Pa", "Pa"), ("Npm", "N/m"), ("ToKg", "kg"))
                 if name.endswith(suffix)), None)
    if name == "steeringWheelOffset":
        unit = "rad"
    elif name == "fBrakeBias":
        unit = "%"
    number = Decimal(value)
    return {"name": name, "raw_value": value, "label": name, "unit": unit,
            "editable": number.copy_abs() <= MAX_NATIVE_MAGNITUDE, "group": group}


def _garage_profile(container: _Container, material: Material) -> sto_garage.Profile | None:
    material_verified = _sha(material.outer_key + material.translation + material.encoding) == MATERIAL_SHA256
    return sto_garage.select_profile(container.car, [name for name, _, _, _ in container.records], material_verified)


def _document(container: _Container, material: Material) -> dict[str, Any]:
    document = {"sto_version": 3, "sha256": _sha(container.original),
            "internal_car_path": container.car, "notes": container.notes,
            "settings": [_descriptor(name, value) for name, value, _, _ in container.records],
            "warnings": list(WARNINGS), "trailer_sha256": _sha(container.trailer),
            "notes_limit_bytes": MAX_NOTES_BYTES, "native_values": True}
    return sto_garage.decorate(document, _garage_profile(container, material))


def decode(data: bytes, material: Material) -> dict[str, Any]:
    """Return all original values with backend-authored native labels and units."""
    return _document(_unpack(data, material), material)


def _validated_changes(changes: Any, records: tuple[tuple[str, str, int, int], ...]) -> dict[str, str]:
    if not isinstance(changes, dict) or len(changes) > MAX_FIELDS:
        raise StoError("Changes must be an object of native names and decimal strings.", "invalid_edit")
    originals = {name: value for name, value, _, _ in records}
    result: dict[str, str] = {}
    for name, raw in changes.items():
        if name not in originals:
            raise StoError("A changed native parameter is absent from this file.", "invalid_edit", name, "native")
        if not isinstance(raw, str) or len(raw) > MAX_NUMBER_CHARS or NUMBER.fullmatch(raw) is None:
            raise StoError("Use a finite decimal string for each changed native value.", "invalid_edit", name, "native")
        try:
            number = Decimal(raw)
            if number == Decimal(originals[name]):
                continue
            if not number.is_finite() or number.copy_abs() > MAX_NATIVE_MAGNITUDE:
                raise StoError("Changed native number exceeds the codec safety bound.", "invalid_edit", name, "native")
            if Decimal(originals[name]).copy_abs() > MAX_NATIVE_MAGNITUDE:
                raise StoError("This native value is outside the supported editing bound.", "invalid_edit", name, "native")
        except InvalidOperation as exc:
            raise StoError("Invalid native numeric edit.", "invalid_edit", name, "native") from exc
        if raw != originals[name]:
            result[name] = raw
    return result


def encode(data: bytes, material: Material, changes: dict[str, str] | None = None,
           notes: str | None = None, *, garage_changes: dict[str, str] | None = None,
           garage_profile_id: str | None = None, descriptor_version: int | None = None) -> bytes:
    """Patch present number tokens and optional notes; preserve all other content.

    Native encoding randomly chooses among equivalent bytes. The verified native
    base table supplies one valid deterministic choice for every ASCII byte; its
    decoder equivalence is checked before use. The NUL is the native raw zero.
    """
    container = _unpack(data, material)
    native_changes = changes if changes is not None else {}
    _validated_changes(native_changes, container.records)
    try:
        edits = sto_garage.merge_changes(_garage_profile(container, material),
                                        {name: raw for name, raw, _, _ in container.records},
                                        native_changes, garage_changes, garage_profile_id, descriptor_version)
    except sto_garage.GarageError as exc:
        raise StoError(str(exc), exc.code, exc.field, exc.representation) from exc
    edits = _validated_changes(edits, container.records)
    note_bytes = container.note_bytes
    if notes is not None:
        if not isinstance(notes, str) or "\0" in notes:
            raise StoError("Notes must be text without embedded terminators.", "invalid_edit")
        try:
            replacement = notes.encode("utf-16le") + b"\0\0"
        except UnicodeEncodeError as exc:
            raise StoError("Notes contain invalid Unicode.", "invalid_edit") from exc
        if len(replacement) > MAX_NOTES_BYTES:
            raise StoError("Notes exceed the native 16 KiB UTF-16 limit.", "invalid_edit")
        if notes != container.notes:
            note_bytes = replacement
    if not edits and note_bytes == container.note_bytes:
        return data
    encrypted = container.encrypted
    if edits:
        pieces: list[str] = []
        position = 0
        for name, _, start, end in container.records:
            if name in edits:
                pieces.extend((container.text[position:start], edits[name]))
                position = end
        pieces.append(container.text[position:])
        text = "".join(pieces)
        encoded = bytes(material.encoding[c] for c in text.encode("ascii")) + b"\0"
        pad = 16 - len(encoded) % 16
        encoded += bytes([pad]) * pad
        aes = _aes()
        inner = aes.new(container.file_key, aes.MODE_CBC, iv=bytes(16)).encrypt(encoded)
        encrypted = aes.new(material.outer_key, aes.MODE_CBC, iv=bytes(16)).encrypt(inner)
    header = struct.pack("<4I", 3, len(encrypted) + len(note_bytes) + 40,
                         len(encrypted), len(note_bytes))
    result = header + container.file_key + encrypted + note_bytes + container.trailer
    if len(result) > MAX_STO_BYTES:
        raise StoError("Edited STO exceeds the 16 MiB limit.", "invalid_edit")
    # The writer does not return a container that fails the complete reader.
    _unpack(result, material)
    return result


def process_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise StoError("Unsupported STO request schema.", "invalid_request")
    if request.get("operation") not in ("decode", "encode"):
        raise StoError("Unsupported STO operation.", "invalid_request")
    descriptor_version = request.get("descriptor_version")
    if descriptor_version is not None and (type(descriptor_version) is not int or descriptor_version != sto_garage.DESCRIPTOR_VERSION):
        raise StoError("Garage descriptors changed. Reopen the setup before saving.", "garage_profile_mismatch", representation="garage")
    profile = request.get("profile_path") or os.environ.get("COACH_STO_MATERIAL")
    if not isinstance(profile, str) or not profile:
        raise StoError("STO decoding material is not installed.", "material_unavailable")
    material = material_from_profile(Path(profile))
    source = request.get("data_base64")
    if not isinstance(source, str) or len(source) > (MAX_STO_BYTES + 2) // 3 * 4:
        raise StoError("Missing or oversized STO bytes.", "invalid_request")
    try:
        data = base64.b64decode(source, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StoError("STO bytes are not valid base64.", "invalid_request") from exc
    if request["operation"] == "decode":
        return {"schema": SCHEMA_VERSION, "ok": True, "document": decode(data, material)}
    result = encode(data, material, request.get("changes", {}), request.get("notes"),
                    garage_changes=request.get("garage_changes"),
                    garage_profile_id=request.get("garage_profile_id"), descriptor_version=descriptor_version)
    return {"schema": SCHEMA_VERSION, "ok": True, "document": decode(result, material),
            "data_base64": base64.b64encode(result).decode("ascii")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extract-profile", type=Path, help="Verified offline simulator executable copy")
    parser.add_argument("--output", type=Path, help="New private material profile outside the repository")
    args = parser.parse_args(argv)
    try:
        if args.extract_profile is not None:
            if args.output is None:
                raise StoError("Profile extraction needs a private output path.", "invalid_request")
            output = args.output.resolve()
            repository = Path(__file__).resolve().parents[4]
            if output.is_relative_to(repository):
                raise StoError("Material profiles must stay outside the source repository.", "invalid_request")
            payload = profile_for_material(material_from_binary(args.extract_profile))
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.write("\n")
            print(json.dumps({"schema": SCHEMA_VERSION, "ok": True, "binary_sha256": BINARY_SHA256}))
            return 0
        if args.output is not None:
            raise StoError("Output is only used for private profile extraction.", "invalid_request")
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise StoError("STO request exceeds the 24 MiB limit.", "invalid_request")
        result = process_request(_json(raw.decode("utf-8")))
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except StoError as exc:
        result = {"schema": SCHEMA_VERSION, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        if exc.field is not None:
            result["error"]["field"] = exc.field
        if exc.representation is not None:
            result["error"]["representation"] = exc.representation
    except (OSError, UnicodeError) as exc:
        result = {"schema": SCHEMA_VERSION, "ok": False,
                  "error": {"code": "io_error", "message": "STO input or private profile could not be read or created."}}
    print(json.dumps(result, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
