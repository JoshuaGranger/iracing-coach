"""Value codec and column-store writers shared by the kernel vectors and the ``kernels`` stage.

Standard library only. ``crates/coach-analysis/tools/emit_kernel_vectors.py``
records the oracle's calls with it, and ``native_stages`` encodes the
``kernels`` hand-off with it (``docs/08-rewrite/port/phase2-plan.md``
sections 3 and 4, ``contracts/worker-interface.md`` section 7). It sits next
to ``native_stages.py``, outside ``analysis_engine.ANALYZER_SOURCE_FILES``,
so it never changes ``analysis_id``. Codec version 1.

A Python value becomes JSON as follows; every other value is a ``CodecError``,
so a unit agent extends the rules here instead of losing information:

=====================================  ==========================================
Python                                 JSON
=====================================  ==========================================
``None``, ``bool``, ``str``            ``null``, ``true``/``false``, string
``int`` within i64                     integer token
``int`` beyond i64                     ``{"$int": "<decimal>"}``
finite ``float``                       number token written by ``float.__repr__``
non-finite ``float``                   ``{"$f": "nan" | "inf" | "-inf"}``
``list``                               array, or ``{"$column": id}`` (below)
``tuple``                              ``{"$tuple": [...]}``
``set``, ``frozenset``                 ``{"$set": [...]}`` (sorted elements)
``dict`` with ``str`` keys, none       object, keys in insertion order
starting with ``$``
any other ``dict``                     ``{"$dict": [[key, value], ...]}``
dataclass instance                     ``{"$dataclass": "module.Class",
                                       "fields": {...}}`` (declared field order)
the analysis ``TelemetryTable``        ``{"$table": 1}``
its ``channels`` mapping               ``{"$table_channels": 1}``
one of its channel lists (identity)    ``{"$table_column": "<name>"}``
a ``LazySequence`` (a store column or  array, exactly as ``list(value)``
the provenance sequence of
``native_stages``)
a ``Handle`` (a value the ``kernels``  ``{"$handle": n}``
process keeps, such as the setup
series)
=====================================  ==========================================

A list of at least ``COLUMN_MIN`` elements that are all ``float``, all
``int``, all ``bool`` or all ``str``, optionally mixed with ``None``, is
written to the call's vector store instead of the JSON text and referenced
as ``{"$column": id}``. It decodes to the same list.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import math
import struct
from collections import abc
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CODEC_VERSION = 1
COLUMN_MIN = 256
STORE_MAGIC = b"COACHCS\x00"
STORE_FORMAT = "coach-column-store"
STORE_VERSION = 1
I64_MIN, I64_MAX = -(2**63), 2**63 - 1
# SDK type code -> decode-stage store element (worker-interface.md section 7).
DECODE_ELEMENTS = {0: "utf8", 1: "bool", 2: "i32", 3: "u32", 4: "f32", 5: "f64"}
_PACK = {"bool": "B", "i32": "i", "u32": "I", "f32": "f", "f64": "d", "i64": "q"}


class CodecError(ValueError):
    """A value the codec has no lossless rule for, or a malformed encoding."""


def _align(value: int) -> int:
    return (value + 7) // 8 * 8


class _Blocks:
    """Data-section blocks, each placed at an 8-byte aligned offset."""

    def __init__(self) -> None:
        self.cursor = 0
        self.parts: list[tuple[int, bytes]] = []

    def place(self, data: bytes) -> dict[str, int]:
        block = {"offset": self.cursor, "length": len(data)}
        self.parts.append((self.cursor, data))
        self.cursor = _align(self.cursor + len(data))
        return block


def _write_store(path: Path, header: Mapping[str, Any], blocks: _Blocks) -> int:
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    data_start = _align(16 + len(header_bytes))
    out = bytearray(STORE_MAGIC)
    out += struct.pack("<II", STORE_VERSION, len(header_bytes))
    out += header_bytes
    out += bytes(data_start - len(out))
    for offset, data in blocks.parts:
        out += bytes(data_start + offset - len(out))
        out += data
    out += bytes(_align(len(out)) - len(out))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return len(out)


def _text_block(values: Sequence[str | None]) -> tuple[bytes, bytes]:
    data = bytearray()
    offsets = [0]
    for value in values:
        if value is not None:
            data += value.encode("utf-8")
        offsets.append(len(data))
    return bytes(data), struct.pack(f"<{len(offsets)}Q", *offsets)


# ---------------------------------------------------------------------------
# Decode-stage store of a recording's analysis table (table.ccs)
# ---------------------------------------------------------------------------


def write_decode_store(
    path: Path,
    *,
    telemetry: Mapping[str, Any],
    sampling_plan: Callable[[Any, Any], tuple[float, float]],
    target_hz: float | None,
    producer: str,
) -> int:
    """Write ``telemetry`` (one decoded recording) as a format-1 decode-stage store.

    The layout is the one ``coach-analysis decode`` writes, so
    ``coach_analysis::store::ColumnStore::parse`` reads it. Floats are packed
    from the decoded Python values: an f32 widened to a double packs back to
    its stored bits, except that a signalling NaN comes back quieted (the
    analysis maps every NaN to ``None``, so no result depends on it).
    """

    metadata = telemetry["metadata"]
    header_info = metadata["header"]
    samples: Mapping[str, list[Any]] = telemetry["samples"]
    variables = {variable["name"]: variable for variable in telemetry["variables"]}
    indices = list(telemetry["sample_indices"])
    count = len(indices)
    step, rate = sampling_plan(header_info["tick_rate"], target_hz)
    blocks = _Blocks()
    sample_block = blocks.place(struct.pack(f"<{count}I", *indices))
    columns = []
    for name, values in samples.items():
        variable = variables[name]
        element = DECODE_ELEMENTS[variable["type_code"]]
        if len(values) != count:
            raise CodecError(f"column {name!r} has {len(values)} values for {count} samples")
        if any(value is None for value in values):
            raise CodecError(
                f"column {name!r} has missing values: a merged group needs the merged store (unit U1)"
            )
        entry: dict[str, Any] = {
            "name": name,
            "type_code": variable["type_code"],
            "count": variable["count"],
            "var_offset": variable["offset"],
            "element": element,
        }
        if element == "utf8":
            data, offsets = _text_block(values)
            entry["data"] = blocks.place(data)
            entry["text_offsets"] = blocks.place(offsets)
        else:
            flat = values if variable["count"] == 1 else [item for row in values for item in row]
            if element == "bool":
                flat = [1 if item else 0 for item in flat]
            entry["data"] = blocks.place(struct.pack(f"<{len(flat)}{_PACK[element]}", *flat))
        columns.append(entry)
    header = {
        "format": STORE_FORMAT,
        "version": STORE_VERSION,
        "stage": "decode",
        "producer": producer,
        "source": {
            "file_size": metadata["file_size"],
            "record_count": metadata["record_count"],
            "tick_rate": header_info["tick_rate"],
            "buffer_offset": metadata["buffer_offset"],
            "buffer_length": header_info["buffer_length"],
            "num_variables": len(telemetry["available_variables"]),
        },
        "sampling": {
            "target_hz": None if target_hz is None else float(target_hz),
            "step": step,
            "integral_step": step.is_integer(),
            "sample_rate_hz": rate,
            "sample_count": count,
        },
        "channel_mode": (telemetry.get("channel_selection") or {}).get("mode") or "selected",
        "sample_indices": sample_block,
        "columns": columns,
    }
    return _write_store(path, header, blocks)


# ---------------------------------------------------------------------------
# Vector store: the column-valued arguments and results of one call
# ---------------------------------------------------------------------------


def _column_element(values: list[Any]) -> tuple[str, bool] | None:
    kinds = set(map(type, values))
    present = type(None) in kinds
    kinds.discard(type(None))
    if len(kinds) != 1:
        return None
    (kind,) = kinds
    if kind is float:
        return "f64", present
    if kind is bool:
        return "bool", present
    if kind is int:
        if all(I64_MIN <= value <= I64_MAX for value in values if value is not None):
            return "i64", present
        return None
    if kind is str:
        return "utf8", present
    return None


class ColumnSink:
    """Column-valued lists of one call, written as one ``stage: "vector"`` store."""

    def __init__(self) -> None:
        self.columns: list[tuple[str, str, bool, list[Any]]] = []

    def add(self, values: list[Any]) -> str | None:
        if len(values) < COLUMN_MIN:
            return None
        shape = _column_element(values)
        if shape is None:
            return None
        column_id = f"c{len(self.columns)}"
        self.columns.append((column_id, shape[0], shape[1], values))
        return column_id

    def write(self, path: Path, producer: str) -> int | None:
        if not self.columns:
            return None
        blocks = _Blocks()
        entries = []
        for column_id, element, has_missing, values in self.columns:
            entry: dict[str, Any] = {"name": column_id, "element": element, "length": len(values)}
            if has_missing:
                entry["present"] = blocks.place(bytes(0 if value is None else 1 for value in values))
            if element == "utf8":
                data, offsets = _text_block(values)
                entry["data"] = blocks.place(data)
                entry["text_offsets"] = blocks.place(offsets)
            else:
                filler = False if element == "bool" else (0 if element == "i64" else 0.0)
                flat = [filler if value is None else value for value in values]
                if element == "bool":
                    flat = [1 if value else 0 for value in flat]
                entry["data"] = blocks.place(struct.pack(f"<{len(flat)}{_PACK[element]}", *flat))
            entries.append(entry)
        header = {
            "format": STORE_FORMAT,
            "version": STORE_VERSION,
            "stage": "vector",
            "producer": producer,
            "codec": CODEC_VERSION,
            "columns": entries,
        }
        return _write_store(path, header, blocks)


class DigestSink(ColumnSink):
    """Names each column by a SHA-256 of its values and stores nothing.

    Two encodings of the same object under this sink are equal exactly when
    its contents are, column-valued lists included (mutation detection).
    """

    def add(self, values: list[Any]) -> str | None:
        if len(values) < COLUMN_MIN or _column_element(values) is None:
            return None
        return "sha256:" + hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def read_vector_store(path: Path) -> dict[str, list[Any]]:
    """Read a ``stage: "vector"`` store back into lists (self-test and tooling)."""

    raw = path.read_bytes()
    if raw[:8] != STORE_MAGIC:
        raise CodecError(f"{path}: not a coach column store")
    version, header_length = struct.unpack("<II", raw[8:16])
    header = json.loads(raw[16 : 16 + header_length].decode("utf-8"))
    if version != STORE_VERSION or header.get("stage") != "vector":
        raise CodecError(f"{path}: not a version-1 vector store")
    base = _align(16 + header_length)

    def block(spec: Mapping[str, int]) -> bytes:
        start = base + spec["offset"]
        data = raw[start : start + spec["length"]]
        if len(data) != spec["length"] or spec["offset"] % 8:
            raise CodecError(f"{path}: bad block {dict(spec)!r}")
        return data

    columns: dict[str, list[Any]] = {}
    for entry in header["columns"]:
        length, element = entry["length"], entry["element"]
        if element == "utf8":
            data = block(entry["data"])
            offsets = struct.unpack(f"<{length + 1}Q", block(entry["text_offsets"]))
            values: list[Any] = [data[a:b].decode("utf-8") for a, b in zip(offsets, offsets[1:])]
        else:
            values = list(struct.unpack(f"<{length}{_PACK[element]}", block(entry["data"])))
            if element == "bool":
                values = [bool(value) for value in values]
        if "present" in entry:
            present = block(entry["present"])
            values = [value if flag else None for value, flag in zip(values, present)]
        columns[entry["name"]] = values
    return columns


# ---------------------------------------------------------------------------
# Value codec
# ---------------------------------------------------------------------------


class LazySequence(abc.Sequence):
    """A read-only sequence that stands in for a list and encodes as one (``native_stages``)."""

    __slots__ = ()


class Handle:
    """A value that stays in the ``kernels`` process, named by number (``{"$handle": n}``).

    ``_setup_channel_series`` returns one; the shims pass it back to
    ``_setup_telemetry`` and ``_track_profile``, the only readers of the setup
    series. A handle is valid for the session that issued it.
    """

    __slots__ = ("number",)

    def __init__(self, number: int) -> None:
        if type(number) is not int or number < 0:
            raise CodecError(f"bad handle {number!r}")
        self.number = number

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Handle) and other.number == self.number

    def __hash__(self) -> int:
        return hash(("handle", self.number))

    def __repr__(self) -> str:
        return f"Handle({self.number})"


class Context:
    """Identity of the objects that encode by reference: the table and its columns.

    ``strings`` maps machine-dependent text (the recording's absolute path) to
    a stable token, so vectors from two machines or platforms compare equal;
    decoding maps the token back. ``handles`` resolves ``{"$handle": n}`` to
    the value a process keeps; without it a handle decodes to :class:`Handle`.
    """

    def __init__(
        self,
        table: Any = None,
        strings: Mapping[str, str] | None = None,
        handles: Mapping[int, Any] | None = None,
    ) -> None:
        self.table = table
        self.handles = handles
        self.strings = dict(strings or {})
        self.tokens = {token: text for text, token in self.strings.items()}
        self.channels_id = id(table.channels) if table is not None else None
        self.column_names: dict[int, str] = (
            {id(values): name for name, values in table.channels.items()} if table is not None else {}
        )


def _qualified(kind: type) -> str:
    return f"{kind.__module__}.{kind.__qualname__}"


def encode(value: Any, context: Context, sink: ColumnSink | None) -> Any:
    """``value`` as JSON-ready data under the rules in the module docstring."""

    if value is None or value is True or value is False:
        return value
    if isinstance(value, str):
        return context.strings.get(value, value)
    kind = type(value)
    if kind is float:
        if math.isfinite(value):
            return value
        return {"$f": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if kind is int:
        return value if I64_MIN <= value <= I64_MAX else {"$int": str(value)}
    if context.table is not None:
        if value is context.table:
            return {"$table": 1}
        if id(value) == context.channels_id:
            return {"$table_channels": 1}
        name = context.column_names.get(id(value))
        if name is not None and context.table.channels.get(name) is value:
            return {"$table_column": name}
    if isinstance(value, Handle):
        return {"$handle": value.number}
    if isinstance(value, LazySequence):
        value = list(value)  # stands in for a list: exactly its items
    if isinstance(value, list):
        if sink is not None:
            column_id = sink.add(value)
            if column_id is not None:
                return {"$column": column_id}
        return [encode(item, context, sink) for item in value]
    if isinstance(value, tuple) and not hasattr(value, "_fields"):
        return {"$tuple": [encode(item, context, sink) for item in value]}
    if isinstance(value, (set, frozenset)):
        try:
            ordered = sorted(value)
        except TypeError as exc:
            raise CodecError(f"unsortable set {value!r}") from exc
        return {"$set": [encode(item, context, sink) for item in ordered]}
    if isinstance(value, dict):
        if all(isinstance(key, str) and not key.startswith("$") for key in value):
            return {key: encode(item, context, sink) for key, item in value.items()}
        return {"$dict": [[encode(key, context, sink), encode(item, context, sink)] for key, item in value.items()]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$dataclass": _qualified(kind),
            "fields": {
                field.name: encode(getattr(value, field.name), context, sink)
                for field in dataclasses.fields(value)
            },
        }
    raise CodecError(f"no codec rule for {_qualified(kind)}; add one in native_codec.py")


def dumps(document: Any) -> str:
    """Canonical text of an encoded document: compact, UTF-8, no NaN tokens."""

    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


_NONFINITE = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}


def decode(data: Any, context: Context, columns: Mapping[str, list[Any]]) -> Any:
    """The inverse of :func:`encode` (dataclasses are rebuilt by import)."""

    if isinstance(data, list):
        return [decode(item, context, columns) for item in data]
    if isinstance(data, str):
        return context.tokens.get(data, data)
    if not isinstance(data, dict):
        return data
    if len(data) == 1:
        ((key, payload),) = data.items()
        if key == "$f":
            return _NONFINITE[payload]
        if key == "$int":
            return int(payload)
        if key == "$column":
            return list(columns[payload])
        if key == "$tuple":
            return tuple(decode(item, context, columns) for item in payload)
        if key == "$set":
            return {decode(item, context, columns) for item in payload}
        if key == "$dict":
            return {decode(k, context, columns): decode(v, context, columns) for k, v in payload}
        if key == "$table":
            return context.table
        if key == "$table_channels":
            return context.table.channels
        if key == "$table_column":
            return context.table.channels[payload]
        if key == "$handle":
            if context.handles is None:
                return Handle(payload)
            if payload not in context.handles:
                raise CodecError(f"unknown handle {payload!r}")
            return context.handles[payload]
    if set(data) == {"$dataclass", "fields"}:
        module_name, _, class_name = data["$dataclass"].rpartition(".")
        kind = getattr(importlib.import_module(module_name), class_name)
        return kind(**{name: decode(item, context, columns) for name, item in data["fields"].items()})
    if any(key.startswith("$") for key in data):
        raise CodecError(f"unknown marker in {sorted(data)!r}")
    return {key: decode(item, context, columns) for key, item in data.items()}


def same(left: Any, right: Any) -> bool:
    """Exact equality: same types, same float bits (any NaN equals any NaN), same key order."""

    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        if math.isnan(left):
            return math.isnan(right)
        return struct.pack("<d", left) == struct.pack("<d", right)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(map(same, left, right))
    if isinstance(left, dict):
        return list(left) == list(right) and all(same(left[key], right[key]) for key in left)
    if isinstance(left, (set, frozenset)):
        return left == right
    if dataclasses.is_dataclass(left) and not isinstance(left, type):
        return all(
            same(getattr(left, field.name), getattr(right, field.name)) for field in dataclasses.fields(left)
        )
    return left == right


__all__ = [
    "CODEC_VERSION",
    "COLUMN_MIN",
    "CodecError",
    "ColumnSink",
    "Context",
    "DigestSink",
    "Handle",
    "LazySequence",
    "decode",
    "dumps",
    "encode",
    "read_vector_store",
    "same",
    "write_decode_store",
]
