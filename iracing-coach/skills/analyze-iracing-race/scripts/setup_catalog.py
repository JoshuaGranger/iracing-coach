from __future__ import annotations

"""Read-only catalog and comparison helpers for iRacing setup artifacts.

iRacing ``.sto`` files are opaque binary artifacts.  The simulator's HTML
export is therefore the parseable authority for a saved setup, while the STO
is paired and hashed for identity/provenance.  All public functions return
JSON-serializable dictionaries and never write to the setup tree.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import hashlib
import html
import math
import os
import re


SCHEMA_VERSION = 1
DEFAULT_MAX_FILES = 2_000
HARD_MAX_FILES = 10_000
DEFAULT_MAX_ENTRIES = 1_000
HARD_MAX_ENTRIES = 2_000
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
HARD_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_FIELDS = 512
HARD_MAX_FIELDS = 4_096
DEFAULT_MAX_OUTPUT = 250
HARD_MAX_OUTPUT = 2_000
MAX_ERRORS = 100
MAX_NOTES_CHARS = 16_000

_CORNER_NAMES = {"left_front", "right_front", "left_rear", "right_rear"}
_TIRE_FIELD_HINTS = {
    "cold_pressure",
    "last_hot_pressure",
    "last_temps_omi",
    "last_temps_imo",
    "tread_remaining",
}
_VARIANT_WORDS = {
    "oval",
    "road",
    "course",
    "long",
    "short",
    "full",
    "cup",
    "nascar",
    "boot",
    "chute",
    "legacy",
    "north",
    "south",
    "east",
    "west",
    "inner",
    "outer",
    "combined",
    "ss",
}
_TRACK_NOISE = {
    "motor",
    "motorspeedway",
    "international",
    "speedway",
    "raceway",
    "circuit",
    "track",
    "course",
    "oval",
    "road",
}
_TRACK_ALIASES = {
    "nhms": "newhampshire",
    "newhampshiremotorspeedway": "newhampshire",
    "indy": "indianapolis",
    "ims": "indianapolis",
    "indianapolismotorspeedway": "indianapolis",
    "atlantass": "atlanta",
    "atlantamotorspeedway": "atlanta",
    "mis": "michigan",
}
_LEAF_ALIASES = {
    "shock_spring_rate": "spring_rate",
    "packer_shim": "packer",
    "front_arb_diameter": "diameter",
}
_UNIT_ABS_TOLERANCE = {
    None: 0.001,
    "%": 0.051,
    "C": 0.76,
    "N": 3.0,
    "N/mm": 0.76,
    "Nm": 0.16,
    "clicks": 0.0,
    "deg": 0.051,
    "kPa": 0.76,
    "mm": 0.76,
    "ratio": 0.001,
}


class SetupCatalogError(ValueError):
    """Base error for invalid setup-catalog input or content."""


class SetupLimitError(SetupCatalogError):
    """Raised when a single requested operation exceeds a hard safety bound."""


class SetupParseError(SetupCatalogError):
    """Raised when an HTML export cannot be parsed as an iRacing setup."""


def _bounded_int(value: Any, name: str, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SetupLimitError(f"{name} must be an integer between 1 and {maximum}.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SetupLimitError(
            f"{name} must be an integer between 1 and {maximum}."
        ) from exc
    if result < 1 or result > maximum:
        raise SetupLimitError(f"{name} must be between 1 and {maximum}.")
    return result


def _clean_text(value: Any, *, preserve_lines: bool = False) -> str:
    text = html.unescape(str(value or "")).replace("\r", "")
    if preserve_lines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        compact: list[str] = []
        for line in lines:
            if line or (compact and compact[-1]):
                compact.append(line)
        return "\n".join(compact).strip()
    return re.sub(r"\s+", " ", text).strip()


def _snake(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    text = re.sub(r"_(o_m_i|i_m_o)$", lambda m: "_" + m.group(1).replace("_", ""), text)
    return text


def _number_token(value: str) -> float | None:
    text = value.strip()
    sign = -1.0 if text.startswith("-") else 1.0
    unsigned = text.lstrip("+-").strip()
    if "/" in unsigned:
        pieces = unsigned.split()
        whole = 0.0
        fraction = pieces[-1]
        if len(pieces) > 1:
            try:
                whole = float(pieces[0])
            except ValueError:
                return None
        numerator, separator, denominator = fraction.partition("/")
        if not separator:
            return None
        try:
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            return sign * (whole + float(numerator) / denominator_value)
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


_VALUE_RE = re.compile(
    r"^\s*([+-]?(?:(?:\d+\s+)?\d+/\d+|(?:\d+(?:\.\d*)?|\.\d+)))\s*(.*?)\s*$",
    re.I,
)


def _numeric_record(raw: str) -> dict[str, Any] | None:
    ratio = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*",
        raw,
    )
    if ratio:
        denominator = float(ratio.group(2))
        if denominator == 0:
            return None
        return {
            "raw": raw,
            "kind": "number",
            "value": float(ratio.group(1)) / denominator,
            "unit": "ratio",
        }

    match = _VALUE_RE.fullmatch(raw)
    if not match:
        return None
    value = _number_token(match.group(1))
    if value is None:
        return None
    unit_raw = match.group(2).strip()
    unit_key = re.sub(r"[\s_]+", "", unit_raw.casefold())
    unit_key = unit_key.replace("°", "").replace("·", "-")
    unit_key = unit_key.removesuffix("shim")

    if unit_key in {"", "x"}:
        unit, canonical = None, value
    elif unit_key in {'"', "in", "inch", "inches"}:
        unit, canonical = "mm", value * 25.4
    elif unit_key in {"mm", "millimeter", "millimeters"}:
        unit, canonical = "mm", value
    elif unit_key == "psi":
        unit, canonical = "kPa", value * 6.894757293168
    elif unit_key == "kpa":
        unit, canonical = "kPa", value
    elif unit_key in {"lbs/in", "lb/in", "lbf/in"}:
        unit, canonical = "N/mm", value * 0.175126835246
    elif unit_key in {"n/mm", "npermm"}:
        unit, canonical = "N/mm", value
    elif unit_key in {"lbs", "lb", "lbf"}:
        unit, canonical = "N", value * 4.448221615261
    elif unit_key == "n":
        unit, canonical = "N", value
    elif unit_key in {"ft-lbs", "ft-lb", "ftlbs", "ftlb"}:
        unit, canonical = "Nm", value * 1.355817948331
    elif unit_key in {"nm", "n-m"}:
        unit, canonical = "Nm", value
    elif unit_key in {"f", "degf"}:
        unit, canonical = "C", (value - 32.0) * 5.0 / 9.0
    elif unit_key in {"c", "degc"}:
        unit, canonical = "C", value
    elif unit_key in {"deg", "degree", "degrees"}:
        unit, canonical = "deg", value
    elif unit_key in {"%", "percent", "pct"}:
        unit, canonical = "%", value
    elif unit_key in {"click", "clicks"}:
        unit, canonical = "clicks", value
    else:
        return None
    return {"raw": raw, "kind": "number", "value": canonical, "unit": unit}


def _canonicalize(raw: Any, *, field_key: str | None = None) -> dict[str, Any]:
    if isinstance(raw, bool):
        return {"raw": raw, "kind": "boolean", "value": raw, "unit": None}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        if field_key and field_key.endswith(".attach") and value in {0.0, 1.0}:
            return {
                "raw": raw,
                "kind": "boolean",
                "value": bool(value),
                "unit": None,
            }
        return {"raw": raw, "kind": "number", "value": value, "unit": None}
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        items = [_canonicalize(item, field_key=field_key) for item in raw]
        if items and all(item["kind"] == "number" for item in items):
            units = {item.get("unit") for item in items}
            if len(units) == 1:
                return {
                    "raw": list(raw),
                    "kind": "number_list",
                    "value": [item["value"] for item in items],
                    "unit": next(iter(units)),
                }
        return {"raw": list(raw), "kind": "list", "value": items, "unit": None}

    text = _clean_text(raw)
    if "," in text:
        pieces = [_clean_text(piece) for piece in text.split(",")]
        if len(pieces) > 1 and all(pieces):
            candidate = _canonicalize(pieces, field_key=field_key)
            if candidate["kind"] == "number_list":
                candidate["raw"] = text
                return candidate
    lowered = text.casefold()
    if lowered in {"yes", "true", "on"}:
        return {"raw": text, "kind": "boolean", "value": True, "unit": None}
    if lowered in {"no", "false", "off"}:
        return {"raw": text, "kind": "boolean", "value": False, "unit": None}
    numeric = _numeric_record(text)
    if numeric is not None:
        return numeric
    return {"raw": text, "kind": "text", "value": lowered, "unit": None}


def _canonical_leaf(section: str, label: Any) -> str:
    leaf = _snake(label)
    leaf = _LEAF_ALIASES.get(leaf, leaf)
    if section.rsplit(".", 1)[-1] in _CORNER_NAMES:
        corner = section.rsplit(".", 1)[-1]
        prefix = corner + "_"
        if leaf.startswith(prefix):
            leaf = leaf[len(prefix) :]
    return leaf


class _SetupHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.header_blocks: list[str] = []
        self.sections: list[dict[str, Any]] = []
        self._in_h2 = False
        self._h2_had_u = False
        self._h2_parts: list[str] = []
        self._in_u = False
        self._u_parts: list[str] = []

    @property
    def _current(self) -> dict[str, Any] | None:
        return self.sections[-1] if self.sections else None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.casefold()
        if tag == "h2":
            self._in_h2 = True
            self._h2_had_u = False
            self._h2_parts = []
            return
        if self._in_h2:
            if tag == "br":
                self._h2_parts.append("\n")
            elif tag == "u":
                self._h2_had_u = True
            return
        if self._current is None:
            return
        if tag == "br":
            self._current["events"].append(("break", ""))
        elif tag == "u":
            self._in_u = True
            self._u_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "h2" and self._in_h2:
            text = _clean_text("".join(self._h2_parts), preserve_lines=True)
            self._in_h2 = False
            if self._h2_had_u:
                heading = text.rstrip(":").strip()
                if heading:
                    self.sections.append({"heading": heading, "events": []})
            elif text:
                self.header_blocks.append(text)
            return
        if tag == "u" and self._in_u and self._current is not None:
            self._current["events"].append(
                ("value", _clean_text("".join(self._u_parts)))
            )
            self._in_u = False
            self._u_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self._h2_parts.append(data)
        elif self._in_u:
            self._u_parts.append(data)
        elif self._current is not None:
            self._current["events"].append(("text", data))


def _header_identity(blocks: Sequence[str]) -> dict[str, str | None]:
    combined = "\n".join(blocks)
    lines = [_clean_text(line) for line in combined.splitlines() if _clean_text(line)]
    car: str | None = None
    setup_name: str | None = None
    track_header: str | None = None
    for line in lines:
        match = re.search(r"(?i)(.*?)\s+setup\s*:\s*(.*?)(?:\s+track\s*:.*)?$", line)
        if match:
            car = match.group(1).strip()
            car = re.sub(
                r"(?i)^.*?iRacing(?:\.com)?\s+Motorsport\s+Simulations\s*",
                "",
                car,
            ).strip()
            setup_name = re.split(r"(?i)\s+track\s*:", match.group(2), maxsplit=1)[0].strip()
        track = re.search(r"(?i)\btrack\s*:\s*(.+)$", line)
        if track:
            track_header = track.group(1).strip()
    if setup_name is None:
        setup = re.search(
            r"(?is)([A-Za-z0-9_. -]+?)\s+setup\s*:\s*(.*?)\s+track\s*:\s*(.+)",
            combined,
        )
        if setup:
            car = _clean_text(setup.group(1))
            setup_name = _clean_text(setup.group(2))
            track_header = _clean_text(setup.group(3))
    return {
        "car_header": car or None,
        "setup_name": setup_name or None,
        "track_header": track_header or None,
    }


def _events_to_fields(events: Sequence[tuple[str, str]]) -> list[tuple[str, Any]]:
    values: dict[str, list[str]] = {}
    order: list[str] = []
    pending: list[str] = []
    current_label: str | None = None
    for kind, value in events:
        if kind == "text":
            pending.append(value)
            continue
        if kind == "break":
            if _clean_text("".join(pending)):
                pending = []
            continue
        if kind != "value":
            continue
        label_text = _clean_text("".join(pending)).rstrip(":").strip()
        pending = []
        if label_text:
            current_label = label_text
            if current_label not in values:
                values[current_label] = []
                order.append(current_label)
        if current_label is not None and value:
            values[current_label].append(value)
    return [
        (label, raw_values[0] if len(raw_values) == 1 else raw_values)
        for label in order
        if (raw_values := values[label])
    ]


def _events_to_notes(events: Sequence[tuple[str, str]], max_chars: int) -> str:
    pieces: list[str] = []
    for kind, value in events:
        if kind == "break":
            pieces.append("\n")
        else:
            pieces.append(value)
    return _clean_text("".join(pieces), preserve_lines=True)[:max_chars]


def _section_path(heading: str, occurrence: int, labels: Sequence[str]) -> str:
    explicit = [_snake(piece) for piece in re.split(r"\s*/\s*", heading) if _snake(piece)]
    heading_key = explicit[-1] if explicit else _snake(heading)
    if heading_key in {"notes", "note"}:
        return "notes"
    prefix = explicit[0] if len(explicit) > 1 and explicit[0] in {"tires", "chassis"} else None
    if heading_key in _CORNER_NAMES:
        label_keys = {_canonical_leaf("", label) for label in labels}
        if prefix is None:
            prefix = "tires" if label_keys.intersection(_TIRE_FIELD_HINTS) else "chassis"
            if not labels:
                prefix = "tires" if occurrence == 1 else "chassis"
        return f"{prefix}.{heading_key}"
    if heading_key in {"front", "front_arb", "rear"}:
        return f"chassis.{heading_key}"
    if prefix:
        return ".".join([prefix, *explicit[1:]])
    return "setup." + heading_key


def _filename_tokens(stem: str) -> list[str]:
    return [token for token in re.split(r"[\s_-]+", stem.strip()) if token]


def _infer_filename_identity(stem: str) -> dict[str, Any]:
    tokens = _filename_tokens(stem)
    season_index: int | None = None
    season_key: str | None = None
    for index, token in enumerate(tokens):
        match = re.fullmatch(r"(?i)(\d{2}|\d{4})S([1-4])", token)
        if not match:
            continue
        year = int(match.group(1))
        if len(match.group(1)) == 2:
            year += 2000
        season_key = f"{year:04d}S{int(match.group(2))}"
        season_index = index
        break

    role_code: str | None = None
    role_index: int | None = None
    role_names = {
        "q": ("Q", "qualifying"),
        "qual": ("Q", "qualifying"),
        "qualifying": ("Q", "qualifying"),
        "r": ("R", "race"),
        "race": ("R", "race"),
        "e": ("E", "endurance"),
        "endurance": ("E", "endurance"),
    }
    role: str | None = None
    for index in range(len(tokens) - 1, -1, -1):
        mapped = role_names.get(tokens[index].casefold())
        if mapped is not None:
            role_code, role = mapped
            role_index = index
            break

    identity_tokens = [
        token
        for index, token in enumerate(tokens)
        if index not in {season_index, role_index}
    ]
    vendor_end = 0
    for index, token in enumerate(identity_tokens):
        lowered = token.casefold()
        if "setup" in lowered or "shop" in lowered:
            vendor_end = index + 1
            break
    if vendor_end == 0 and identity_tokens and identity_tokens[0].casefold() in {
        "vrs",
        "maconi",
        "noaps",
        "ryco",
        "apex",
        "majors",
    }:
        vendor_end = 1
    vendor_tokens = identity_tokens[:vendor_end]
    track_tokens = identity_tokens[vendor_end:]
    variant_tokens = [
        token
        for token in track_tokens
        if token.casefold() in _VARIANT_WORDS
    ]
    if track_tokens and track_tokens[-1].casefold().endswith("ss") and len(track_tokens[-1]) > 2:
        variant_tokens.append("SS")
    return {
        "stem": stem,
        "vendor": " ".join(vendor_tokens) or None,
        "vendor_tokens": vendor_tokens,
        "track_hint": " ".join(track_tokens) or None,
        "track_tokens": track_tokens,
        "variant": " ".join(dict.fromkeys(token.casefold() for token in variant_tokens)) or None,
        "variant_tokens": list(dict.fromkeys(variant_tokens)),
        "season_key": season_key,
        "role_code": role_code,
        "role": role,
    }


def _track_signature(value: Any) -> str:
    raw = re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
    if raw in _TRACK_ALIASES:
        return _TRACK_ALIASES[raw]
    for alias, replacement in _TRACK_ALIASES.items():
        if raw.startswith(alias) and len(alias) >= 4:
            raw = replacement + raw[len(alias) :]
            break
    for noise in sorted(_TRACK_NOISE | _VARIANT_WORDS, key=len, reverse=True):
        raw = raw.replace(noise, "")
    return _TRACK_ALIASES.get(raw, raw)


def _identity_mismatches(
    filename: Mapping[str, Any], exported: Mapping[str, Any], car_folder: str | None
) -> dict[str, bool | None]:
    setup_name = exported.get("setup_name")
    setup_mismatch = (
        None
        if not setup_name
        else _snake(setup_name) != _snake(filename.get("stem"))
    )
    intended_track = filename.get("track_hint")
    exported_track = exported.get("track_header")
    track_mismatch: bool | None = None
    if intended_track and exported_track:
        left, right = _track_signature(intended_track), _track_signature(exported_track)
        track_mismatch = not (
            left == right
            or (len(left) >= 4 and left in right)
            or (len(right) >= 4 and right in left)
        )
    exported_car = exported.get("car_header")
    car_mismatch: bool | None = None
    if car_folder and exported_car:
        left, right = _snake(car_folder), _snake(exported_car)
        car_mismatch = not (left == right or left in right or right in left)
    flags = {
        "setup_name_mismatch": setup_mismatch,
        "track_header_mismatch": track_mismatch,
        "car_folder_header_mismatch": car_mismatch,
    }
    flags["has_mismatch"] = any(value is True for value in flags.values())
    return flags


def _resolved_file(path: str | os.PathLike[str], root: Path | None = None) -> Path:
    candidate = Path(path).expanduser().resolve(strict=True)
    if not candidate.is_file():
        raise SetupCatalogError(f"Setup source is not a regular file: {candidate}")
    if root is not None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SetupCatalogError(f"Setup source must remain beneath {root}.") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _source_info(path: Path, root: Path, max_file_bytes: int) -> dict[str, Any]:
    stat = path.stat()
    if stat.st_size > max_file_bytes:
        raise SetupLimitError(
            f"Setup source exceeds max_file_bytes ({stat.st_size} > {max_file_bytes}): {path}"
        )
    return {
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "name": path.name,
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "sha256": _sha256(path),
    }


def _infer_car_folder(root: Path | None, path: Path) -> str | None:
    parts = list(path.parts)
    lowered = [part.casefold() for part in parts]
    if "setups" in lowered:
        index = len(lowered) - 1 - lowered[::-1].index("setups")
        if index + 1 < len(parts):
            return parts[index + 1]
    if root is None:
        return path.parent.name or None
    try:
        relative_parent = path.parent.relative_to(root)
    except ValueError:
        return path.parent.name or None
    if relative_parent.parts:
        return relative_parent.parts[0]
    return root.name or None


def parse_setup_html(
    path: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_fields: int = DEFAULT_MAX_FIELDS,
    max_notes_chars: int = MAX_NOTES_CHARS,
) -> dict[str, Any]:
    """Parse one iRacing HTML setup export without modifying any source file."""

    file_limit = _bounded_int(
        max_file_bytes, "max_file_bytes", DEFAULT_MAX_FILE_BYTES, HARD_MAX_FILE_BYTES
    )
    field_limit = _bounded_int(max_fields, "max_fields", DEFAULT_MAX_FIELDS, HARD_MAX_FIELDS)
    notes_limit = _bounded_int(
        max_notes_chars, "max_notes_chars", MAX_NOTES_CHARS, MAX_NOTES_CHARS
    )
    resolved_root = Path(root).expanduser().resolve(strict=True) if root is not None else None
    if resolved_root is not None and not resolved_root.is_dir():
        raise SetupCatalogError(f"Setup root is not a directory: {resolved_root}")
    target = _resolved_file(path, resolved_root)
    if target.suffix.casefold() not in {".htm", ".html"}:
        raise SetupParseError("parse_setup_html requires an .htm or .html file.")
    stat = target.stat()
    if stat.st_size > file_limit:
        raise SetupLimitError(
            f"HTML setup exceeds max_file_bytes ({stat.st_size} > {file_limit}): {target}"
        )
    raw = target.read_text(encoding="utf-8-sig", errors="replace")
    parser = _SetupHTMLParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:
        raise SetupParseError(f"Unable to parse setup HTML {target}: {exc}") from exc

    exported = _header_identity(parser.header_blocks)
    if not exported.get("setup_name") and not parser.sections:
        raise SetupParseError(f"HTML does not contain a recognizable iRacing setup: {target}")
    filename = _infer_filename_identity(target.stem)
    car_folder = _infer_car_folder(resolved_root, target)
    fields: dict[str, dict[str, Any]] = {}
    sections: list[dict[str, Any]] = []
    notes: list[str] = []
    warnings: list[str] = []
    occurrences: dict[str, int] = {}
    truncated = False
    for raw_section in parser.sections:
        heading = _clean_text(raw_section["heading"])
        heading_key = _snake(heading.rsplit("/", 1)[-1])
        occurrences[heading_key] = occurrences.get(heading_key, 0) + 1
        if heading_key in {"notes", "note"}:
            note = _events_to_notes(raw_section["events"], notes_limit)
            if note:
                notes.append(note)
            sections.append({"heading": heading, "path": "notes", "field_count": 0})
            continue
        parsed_fields = _events_to_fields(raw_section["events"])
        section_path = _section_path(
            heading, occurrences[heading_key], [label for label, _ in parsed_fields]
        )
        added = 0
        for label, value in parsed_fields:
            if len(fields) >= field_limit:
                truncated = True
                break
            key = f"{section_path}.{_canonical_leaf(section_path, label)}"
            if key in fields:
                warnings.append(f"Duplicate normalized field ignored: {key}")
                continue
            fields[key] = {
                **_canonicalize(value, field_key=key),
                "label": label,
                "section": heading,
            }
            added += 1
        sections.append(
            {
                "heading": heading,
                "path": section_path,
                "occurrence": occurrences[heading_key],
                "field_count": added,
            }
        )
        if truncated:
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "path": str(target),
            "name": target.name,
            "size_bytes": stat.st_size,
            "sha256": _sha256(target),
        },
        "identity": {
            "car_folder": car_folder,
            "filename": filename,
            "exported": exported,
            "mismatches": _identity_mismatches(filename, exported, car_folder),
        },
        "sections": sections,
        "fields": fields,
        "field_count": len(fields),
        "fields_truncated": truncated,
        "notes": "\n\n".join(notes)[:notes_limit],
        "warnings": warnings[:MAX_ERRORS],
    }


def _unwrap_embedded_setup(value: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = value.get("CarSetup")
    if isinstance(direct, Mapping):
        return direct
    identity = value.get("identity")
    if isinstance(identity, Mapping) and isinstance(identity.get("setup"), Mapping):
        return identity["setup"]
    session_info = value.get("session_info")
    if isinstance(session_info, Mapping) and isinstance(session_info.get("CarSetup"), Mapping):
        return session_info["CarSetup"]
    if isinstance(value.get("setup"), Mapping):
        return value["setup"]
    return value


def _normalize_embedded_path(parts: Sequence[str]) -> str:
    normalized = [_snake(part) for part in parts if _snake(part)]
    if not normalized:
        return ""
    leaf = normalized[-1]
    if leaf == "update_count" and len(normalized) == 1:
        return ""
    if normalized[0] not in {"chassis", "tires"}:
        normalized.insert(0, "setup")
    section = ".".join(normalized[:-1])
    leaf = _canonical_leaf(section, leaf)
    return ".".join([*normalized[:-1], leaf])


def normalize_embedded_setup(
    car_setup: Mapping[str, Any], *, max_fields: int = DEFAULT_MAX_FIELDS
) -> dict[str, Any]:
    """Flatten embedded telemetry ``CarSetup`` into comparable canonical fields."""

    if not isinstance(car_setup, Mapping):
        raise SetupCatalogError("car_setup must be a mapping.")
    field_limit = _bounded_int(max_fields, "max_fields", DEFAULT_MAX_FIELDS, HARD_MAX_FIELDS)
    root = _unwrap_embedded_setup(car_setup)
    fields: dict[str, dict[str, Any]] = {}
    truncated = False

    def walk(value: Any, path: tuple[str, ...]) -> None:
        nonlocal truncated
        if truncated:
            return
        if isinstance(value, Mapping):
            for key in sorted(value, key=lambda item: str(item).casefold()):
                walk(value[key], (*path, str(key)))
                if truncated:
                    return
            return
        if len(fields) >= field_limit:
            truncated = True
            return
        key = _normalize_embedded_path(path)
        if not key:
            return
        fields[key] = _canonicalize(value, field_key=key)

    walk(root, ())
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "embedded_car_setup",
        "fields": fields,
        "field_count": len(fields),
        "fields_truncated": truncated,
    }


def _comparison_fields(value: Mapping[str, Any], max_fields: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SetupCatalogError("Each setup supplied to compare_setups must be a mapping.")
    if isinstance(value.get("fields"), Mapping):
        return dict(list(value["fields"].items())[:max_fields])
    parsed = value.get("parsed_html")
    if isinstance(parsed, Mapping) and isinstance(parsed.get("fields"), Mapping):
        return dict(list(parsed["fields"].items())[:max_fields])
    return normalize_embedded_setup(value, max_fields=max_fields)["fields"]


def _records_equal(
    left: Mapping[str, Any], right: Mapping[str, Any], rel_tolerance: float
) -> tuple[bool, float | None]:
    left_kind, right_kind = left.get("kind"), right.get("kind")
    if left_kind == right_kind == "number":
        if left.get("unit") != right.get("unit"):
            return False, None
        left_value, right_value = float(left["value"]), float(right["value"])
        absolute = _UNIT_ABS_TOLERANCE.get(left.get("unit"), 0.001)
        return (
            math.isclose(left_value, right_value, rel_tol=rel_tolerance, abs_tol=absolute),
            left_value - right_value,
        )
    if left_kind == right_kind == "number_list":
        if left.get("unit") != right.get("unit"):
            return False, None
        left_values, right_values = list(left.get("value") or ()), list(right.get("value") or ())
        if len(left_values) != len(right_values):
            return False, None
        absolute = _UNIT_ABS_TOLERANCE.get(left.get("unit"), 0.001)
        return all(
            math.isclose(float(a), float(b), rel_tol=rel_tolerance, abs_tol=absolute)
            for a, b in zip(left_values, right_values)
        ), None
    return (
        left_kind == right_kind and left.get("value") == right.get("value"),
        None,
    )


def compare_setups(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    max_fields: int = DEFAULT_MAX_FIELDS,
    max_output: int = DEFAULT_MAX_OUTPUT,
    rel_tolerance: float = 0.002,
) -> dict[str, Any]:
    """Compare parsed HTML or embedded setup mappings in canonical units."""

    field_limit = _bounded_int(max_fields, "max_fields", DEFAULT_MAX_FIELDS, HARD_MAX_FIELDS)
    output_limit = _bounded_int(max_output, "max_output", DEFAULT_MAX_OUTPUT, HARD_MAX_OUTPUT)
    try:
        relative = float(rel_tolerance)
    except (TypeError, ValueError) as exc:
        raise SetupCatalogError("rel_tolerance must be a non-negative finite number.") from exc
    if not math.isfinite(relative) or relative < 0:
        raise SetupCatalogError("rel_tolerance must be a non-negative finite number.")
    left_fields = _comparison_fields(left, field_limit)
    right_fields = _comparison_fields(right, field_limit)
    left_keys, right_keys = set(left_fields), set(right_fields)
    common = sorted(left_keys.intersection(right_keys))
    differences: list[dict[str, Any]] = []
    matching = 0
    for key in common:
        equal, delta = _records_equal(left_fields[key], right_fields[key], relative)
        if equal:
            matching += 1
            continue
        item = {
            "field": key,
            "left": left_fields[key],
            "right": right_fields[key],
        }
        if delta is not None:
            item["delta_left_minus_right"] = delta
        differences.append(item)
    only_left_all = sorted(left_keys - right_keys)
    only_right_all = sorted(right_keys - left_keys)
    budget = output_limit
    shown_differences = differences[:budget]
    budget -= len(shown_differences)
    shown_left = only_left_all[:budget]
    budget -= len(shown_left)
    shown_right = only_right_all[:budget]
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "left_fields": len(left_fields),
            "right_fields": len(right_fields),
            "common_fields": len(common),
            "matching_fields": matching,
            "different_fields": len(differences),
            "only_left_fields": len(only_left_all),
            "only_right_fields": len(only_right_all),
        },
        "differences": shown_differences,
        "only_left": shown_left,
        "only_right": shown_right,
        "output_truncated": (
            len(shown_differences) < len(differences)
            or len(shown_left) < len(only_left_all)
            or len(shown_right) < len(only_right_all)
        ),
        "delta_definition": "left minus right in the reported canonical unit",
    }


def catalog_setups(
    setup_root: str | os.PathLike[str],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_fields: int = DEFAULT_MAX_FIELDS,
) -> dict[str, Any]:
    """Recursively catalog bounded HTML/STO setup groups beneath ``setup_root``."""

    file_limit = _bounded_int(max_files, "max_files", DEFAULT_MAX_FILES, HARD_MAX_FILES)
    entry_limit = _bounded_int(
        max_entries, "max_entries", DEFAULT_MAX_ENTRIES, HARD_MAX_ENTRIES
    )
    byte_limit = _bounded_int(
        max_file_bytes, "max_file_bytes", DEFAULT_MAX_FILE_BYTES, HARD_MAX_FILE_BYTES
    )
    field_limit = _bounded_int(max_fields, "max_fields", DEFAULT_MAX_FIELDS, HARD_MAX_FIELDS)
    root = Path(setup_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SetupCatalogError(f"Setup root is not a directory: {root}")

    grouped: dict[tuple[str, str], dict[str, list[Path]]] = {}
    errors: list[dict[str, str]] = []
    matching_seen = 0
    scan_truncated = False
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        directory_names[:] = sorted(
            [name for name in directory_names if not (base / name).is_symlink()],
            key=str.casefold,
        )
        for name in sorted(file_names, key=str.casefold):
            suffix = Path(name).suffix.casefold()
            if suffix not in {".sto", ".htm", ".html"}:
                continue
            if matching_seen >= file_limit:
                scan_truncated = True
                break
            matching_seen += 1
            unresolved = base / name
            try:
                path = unresolved.resolve(strict=True)
                path.relative_to(root)
                if not path.is_file():
                    raise SetupCatalogError("not a regular file")
                if path.stat().st_size > byte_limit:
                    raise SetupLimitError(
                        f"source exceeds max_file_bytes ({path.stat().st_size} > {byte_limit})"
                    )
            except (OSError, ValueError, SetupCatalogError) as exc:
                if len(errors) < MAX_ERRORS:
                    errors.append(
                        {
                            "path": str(unresolved),
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
                continue
            relative_parent = path.parent.relative_to(root).as_posix().casefold()
            key = (relative_parent, path.stem.casefold())
            bucket = grouped.setdefault(key, {"html": [], "sto": []})
            bucket["sto" if suffix == ".sto" else "html"].append(path)
        if scan_truncated:
            break

    entries: list[dict[str, Any]] = []
    for _, bucket in sorted(grouped.items(), key=lambda item: item[0]):
        if len(entries) >= entry_limit:
            break
        html_paths = sorted(bucket["html"], key=lambda path: str(path).casefold())
        sto_paths = sorted(bucket["sto"], key=lambda path: str(path).casefold())
        all_paths = [*html_paths, *sto_paths]
        try:
            sources = {
                "html": [_source_info(path, root, byte_limit) for path in html_paths],
                "sto": [_source_info(path, root, byte_limit) for path in sto_paths],
            }
        except (OSError, SetupCatalogError) as exc:
            if len(errors) < MAX_ERRORS:
                errors.append(
                    {
                        "path": str(all_paths[0]) if all_paths else str(root),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            continue
        if len(html_paths) == 1 and len(sto_paths) == 1:
            pair_status = "paired"
        elif html_paths and not sto_paths:
            pair_status = "html_only" if len(html_paths) == 1 else "ambiguous"
        elif sto_paths and not html_paths:
            pair_status = "sto_only" if len(sto_paths) == 1 else "ambiguous"
        else:
            pair_status = "ambiguous"
        primary = html_paths[0] if html_paths else sto_paths[0]
        parsed: dict[str, Any] | None = None
        parse_error: dict[str, str] | None = None
        if html_paths:
            try:
                parsed = parse_setup_html(
                    html_paths[0],
                    root=root,
                    max_file_bytes=byte_limit,
                    max_fields=field_limit,
                )
            except SetupCatalogError as exc:
                parse_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
        filename_identity = (
            parsed["identity"]["filename"]
            if parsed is not None
            else _infer_filename_identity(primary.stem)
        )
        entries.append(
            {
                "stem": primary.stem,
                "pair_status": pair_status,
                "car_folder": _infer_car_folder(root, primary),
                "filename_identity": filename_identity,
                "sources": sources,
                "parsed_html": parsed,
                "parse_error": parse_error,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "read_only": True,
        "matching_files_seen": matching_seen,
        "source_file_count": sum(
            len(entry["sources"][kind]) for entry in entries for kind in ("html", "sto")
        ),
        "group_count": len(grouped),
        "returned_entry_count": len(entries),
        "scan_truncated": scan_truncated,
        "entries_truncated": len(entries) < len(grouped),
        "entries": entries,
        "error_count": len(errors),
        "errors": errors,
    }


__all__ = [
    "SetupCatalogError",
    "SetupLimitError",
    "SetupParseError",
    "catalog_setups",
    "compare_setups",
    "normalize_embedded_setup",
    "parse_setup_html",
]
