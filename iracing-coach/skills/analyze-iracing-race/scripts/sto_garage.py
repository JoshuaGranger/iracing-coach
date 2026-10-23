"""Evidence-scoped garage descriptors and exact validation for native STO edits.

This registry contains no setup values. A profile requires the verified simulator
material, an exact native car identity and an ordered parameter-name signature.
Unverified limits are omitted; missing inverse evidence means display-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import math
import re
import struct
from typing import Any


DESCRIPTOR_VERSION = 2
REGISTRY_VERSION = 5  # Increment when any production mapping/evidence changes.
VERIFIED_BINARY = "6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362"
XFINITY_LAYOUT = "fad6f45bbadb29c91b99ab28114beb0eec8deb0d85609a001981271c6daeab05"
NATIVE_PA_PER_PSI = Decimal("6891.16015625")
NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", re.ASCII)


class GarageError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None,
                 representation: str = "garage") -> None:
        super().__init__(message)
        self.code, self.field, self.representation = code, field, representation


def decimal_text(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 80
        return format(value.normalize(), "f") if value else "0"


def _number(raw: Any, field: str, representation: str) -> Decimal:
    if not isinstance(raw, str) or len(raw) > 64 or NUMBER.fullmatch(raw) is None:
        raise GarageError("invalid_garage_value", "Use a finite decimal value.", field, representation)
    try:
        value = Decimal(raw)
        if not value.is_finite() or value.copy_abs() > Decimal("1e12") or (value and value.adjusted() < -30):
            raise InvalidOperation
        return value
    except InvalidOperation as exc:
        raise GarageError("invalid_garage_value", "Value exceeds the supported numeric precision.", field, representation) from exc


def _f32(value: Decimal | float) -> float:
    result = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if not math.isfinite(result):
        raise ValueError("Native value cannot be displayed as a finite float32")
    return result


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    unit: str | None
    scale: Decimal = Decimal(1)
    offset: Decimal = Decimal(0)
    native_per_display: Decimal | None = None
    editable: bool = True
    display_places: int | None = None
    float32_display: bool = False
    # Native storage serialization is independent of garage adjustment rounding.
    native_float32: bool = False
    native_places: int | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    step: Decimal | None = None
    step_base: Decimal | None = None
    choices: tuple[tuple[Decimal, str], ...] = ()
    note: str = "Adjustment limits and increments are not verified for this car and track."

    def __post_init__(self) -> None:
        if not self.scale.is_finite() or not self.scale or not self.offset.is_finite():
            raise ValueError("Garage mappings need a finite nonzero scale and finite offset")
        if self.native_per_display is not None and (not self.native_per_display.is_finite() or not self.native_per_display):
            raise ValueError("Native-per-display factor must be finite and nonzero")
        if (self.step is None) != (self.step_base is None):
            raise ValueError("A garage step needs its verified grid origin")
        if self.step is not None and (not self.step.is_finite() or self.step <= 0):
            raise ValueError("A garage step must be positive")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Garage bounds are inverted")
        if any(value is not None and not value.is_finite()
               for value in (self.minimum, self.maximum, self.step_base)):
            raise ValueError("Garage constraints must be finite")
        if len({value for value, _ in self.choices}) != len(self.choices):
            raise ValueError("Garage choice values must be unique")

    def converted(self, raw: str) -> Decimal:
        native = Decimal(raw)
        with localcontext() as context:
            context.prec = 80
            if self.float32_display:
                converted = (_f32(native) / _f32(self.native_per_display) if self.native_per_display is not None
                             else _f32(native) * _f32(self.scale))
                value = Decimal.from_float(_f32(converted)) + self.offset
            else:
                value = (native / self.native_per_display if self.native_per_display is not None
                         else native * self.scale) + self.offset
            return value

    def display(self, raw: str) -> Decimal:
        value = self.converted(raw)
        if self.display_places is not None:
            return Decimal(format(value, f".{self.display_places}f"))
        return value

    def validate(self, value: Decimal, representation: str) -> None:
        if self.minimum is not None and value < self.minimum or self.maximum is not None and value > self.maximum:
            raise GarageError("garage_out_of_range", "Value is outside the verified garage limits.", self.name, representation)
        if self.choices and value not in {choice[0] for choice in self.choices}:
            raise GarageError("garage_unknown_choice", "Choose a verified garage option.", self.name, representation)
        if self.step is not None and self.step_base is not None:
            if ((Fraction(value) - Fraction(self.step_base)) / Fraction(self.step)).denominator != 1:
                raise GarageError("garage_off_grid", "Value does not match the verified garage increment.", self.name, representation)

    def inverse(self, value: Decimal) -> str:
        if not self.editable:
            raise GarageError("garage_unmapped_field", "This garage value has no verified editable inverse.", self.name)
        with localcontext() as context:
            context.prec = 80
            result = ((value - self.offset) * self.native_per_display if self.native_per_display is not None
                      else (value - self.offset) / self.scale)
            if (not result.is_finite() or result.copy_abs() > Decimal("1e12")
                    or (result and result.adjusted() < -30)):
                raise GarageError("invalid_garage_value", "Converted value exceeds the supported native numeric precision.", self.name)
            if self.native_float32:
                result = Decimal.from_float(_f32(result))
            raw = format(result, f".{self.native_places}f") if self.native_places is not None else decimal_text(result)
            if self.display(raw) != value:
                raise GarageError("invalid_garage_value", "Value cannot be represented by the verified native storage format.", self.name)
            return raw

    def descriptor(self, raw: str) -> dict[str, Any]:
        value = self.display(raw)
        result: dict[str, Any] = {"label": self.label, "value": decimal_text(value),
                                  "unit": self.unit, "editable": self.editable,
                                  "constraint_note": self.note}
        for key, quantity in (("min", self.minimum), ("max", self.maximum),
                              ("step", self.step), ("step_base", self.step_base)):
            if quantity is not None:
                result[key] = decimal_text(quantity)
        if self.choices:
            result["choices"] = [{"value": decimal_text(number), "label": label} for number, label in self.choices]
        return result

    def manifest(self) -> dict[str, Any]:
        return {key: str(value) if isinstance(value, Decimal) else value
                for key, value in self.__dict__.items() if key != "choices"} | {
                    "choices": [[str(number), label] for number, label in self.choices]}


@dataclass(frozen=True)
class Profile:
    key: str
    version: int
    label: str
    native_cars: tuple[str, ...]
    layout_sha256: str
    fields: tuple[Field, ...]
    evidence: tuple[str, ...]
    binary_sha256: str = VERIFIED_BINARY

    @property
    def profile_id(self) -> str:
        manifest = {"key": self.key, "version": self.version, "label": self.label,
                    "native_cars": self.native_cars, "layout_sha256": self.layout_sha256,
                    "binary_sha256": self.binary_sha256,
                    "fields": [field.manifest() for field in self.fields], "evidence": self.evidence}
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return f"{self.key}:v{self.version}:{digest[:20]}"


def layout_signature(names: list[str]) -> str:
    return hashlib.sha256(json.dumps(names, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


# No purchased values appear here. Camaro-header originals were loaded in the
# tested Supra session; native Save As produced the Supra header with this same
# layout. No separate Camaro garage session is implied by these two file IDs.
PROFILES = (Profile(
    "xfinity-2019-native", REGISTRY_VERSION, "Xfinity 2019 · verified in Supra",
    ("stockcars2\\camaro2019", "stockcars2\\supra2019"), XFINITY_LAYOUT,
    (Field("fBrakeBias", "Front brake bias", "%", step=Decimal("0.5"), step_base=Decimal(0),
           note="Limits unverified."),) + tuple(
        Field(f"{corner}TireColdPressPa", "Cold pressure", "psi", native_per_display=NATIVE_PA_PER_PSI,
              display_places=1, float32_display=True,
              native_float32=True, native_places=6,
              step=Decimal("0.5"), step_base=Decimal(0),
              note="Limits unverified.")
        for corner in ("lf", "rf", "lr", "rr")) + (
        Field("steeringBoxRatio", "Steering ratio", ":1", note="Steps and limits unverified."),
        Field("lfLowSpeedCompDampSetting", "LS compression", "clicks", minimum=Decimal(1),
              maximum=Decimal(10), step=Decimal(1), step_base=Decimal(1),
              note="Verified in Supra garage.")),
    ("2026-10-23 paired native/HTM fields; official NASCAR Xfinity manual v5 labels",
     "2026-10-23 offline Supra load, front brake bias edit, native Save As and reload",
     "2026-10-23 native one-click pressure and bias save: exact float32 pressure inverse and 0.5 grids",
     "2026-10-23 native steering ratio and LF LS compression clicks; LF tooltip minimum 1 maximum 10",
     "2026-10-23 separate RF/LR/RR pressure clicks reproduce the same exact native inverse",
     "Camaro-header source and Supra-header native resave were verified in Supra; no separate Camaro garage session")),)


def select_profile(car: str, names: list[str], material_verified: bool,
                   profiles: tuple[Profile, ...] | None = None) -> Profile | None:
    if not material_verified:
        return None
    signature = layout_signature(names)
    candidates = [profile for profile in (PROFILES if profiles is None else profiles)
                  if car in profile.native_cars and signature == profile.layout_sha256]
    return candidates[0] if len(candidates) == 1 else None


def decorate(document: dict[str, Any], profile: Profile | None) -> dict[str, Any]:
    document["descriptor_version"] = DESCRIPTOR_VERSION
    document["garage_profile_id"] = profile.profile_id if profile else None
    if profile is None:
        return document
    document["garage_profile_label"] = profile.label
    fields = {field.name: field for field in profile.fields}
    for setting in document["settings"]:
        field = fields.get(setting["name"])
        if field:
            try:
                _number(setting["raw_value"], setting["name"], "native")
                setting["garage"] = field.descriptor(setting["raw_value"])
            except (ValueError, OverflowError, InvalidOperation):
                continue  # Keep malformed/unrepresentable optional garage values native-only.
            if not setting["editable"]:
                setting["garage"]["editable"] = False
    return document


def merge_changes(profile: Profile | None, originals: dict[str, str], native_changes: dict[str, str],
                  garage_changes: dict[str, str] | None = None, profile_id: str | None = None,
                  descriptor_version: int | None = None) -> dict[str, str]:
    if descriptor_version is not None and (type(descriptor_version) is not int or descriptor_version != DESCRIPTOR_VERSION):
        raise GarageError("garage_profile_mismatch", "Garage descriptors changed. Reopen the setup before saving.")
    using_garage = garage_changes is not None or profile_id is not None
    if using_garage and (profile is None or profile_id != profile.profile_id):
        raise GarageError("garage_profile_mismatch", "Garage profile changed or is unavailable. Reopen the setup before saving.")
    if garage_changes is not None and not isinstance(garage_changes, dict):
        raise GarageError("invalid_garage_value", "Garage changes must be a map of native names to decimal strings.")
    garage_changes = {} if garage_changes is None else garage_changes
    overlap = native_changes.keys() & garage_changes.keys()
    if overlap:
        raise GarageError("ambiguous_change", "A field cannot be changed in both representations.", sorted(overlap)[0])
    fields = {field.name: field for field in profile.fields} if profile else {}
    result = dict(native_changes)
    for name, raw in native_changes.items():
        field = fields.get(name)
        if field is None or name not in originals or Decimal(raw) == Decimal(originals[name]):
            continue
        if any((field.minimum is not None, field.maximum is not None, field.step is not None, bool(field.choices))):
            _number(raw, name, "native")
            value = field.display(raw) if field.step is not None or field.choices else field.converted(raw)
            field.validate(value, "native")
            if (field.step is not None or field.choices) and field.editable and Decimal(field.inverse(value)) != Decimal(raw):
                raise GarageError("garage_off_grid", "Native value does not represent a verified garage setting.", name, "native")
    for name, raw in garage_changes.items():
        field = fields.get(name)
        if field is None or name not in originals:
            raise GarageError("garage_unmapped_field", "This field has no garage mapping for the selected profile.", name)
        value = _number(raw, name, "garage")
        # A display roundtrip never rewrites an existing native spelling/value.
        if value == field.display(originals[name]):
            continue
        if not field.editable:
            raise GarageError("garage_unmapped_field", "This garage field is display-only until its inverse is verified.", name)
        field.validate(value, "garage")
        result[name] = field.inverse(value)
    return result
