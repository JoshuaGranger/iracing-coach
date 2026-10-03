"""Native (Rust) stage substitution for the analysis worker.

``IRACING_COACH_NATIVE_STAGES`` is a comma list of stage names
(``docs/08-rewrite/contracts/worker-interface.md`` section 7). Unset or empty,
every stage runs in Python and nothing here is called. A listed stage that
this build does not provide fails the job: a silent Python fallback would let
a parity run compare Python with Python.

Phase 1 provides ``decode``. :func:`load_telemetry` is
``ibt_reader.load_telemetry`` with the sample decode done by
``coach-analysis decode`` (crate ``crates/coach-analysis``): the recording is
still scanned here (header, session info, variable catalogue, summary), the
native stage writes the selected columns to a coach column store, and this
module turns each column into exactly the Python values the reader returns.
The executable is ``IRACING_COACH_NATIVE_BIN`` or ``coach-analysis`` on
``PATH``.
"""

from __future__ import annotations

import itertools
import json
import operator
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:  # Package import and direct script execution are both supported.
    from .ibt_reader import _iter_sample_indices, _sampling_plan, scan_ibt
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from ibt_reader import _iter_sample_indices, _sampling_plan, scan_ibt


STAGES_ENV = "IRACING_COACH_NATIVE_STAGES"
BINARY_ENV = "IRACING_COACH_NATIVE_BIN"
BINARY_NAME = "coach-analysis"
KNOWN_STAGES = ("decode", "kernels", "json_writer")
AVAILABLE_STAGES = frozenset({"decode"})
REQUEST_VERSION = 1
STORE_MAGIC = b"COACHCS\x00"
STORE_FORMAT = "coach-column-store"
STORE_VERSION = 1

# Store element -> (SDK type code, memoryview format). Native little-endian
# formats of these sizes are the struct formats ibt_reader decodes with.
_ELEMENTS = {
    "bool": (1, "B"),
    "i32": (2, "i"),
    "u32": (3, "I"),
    "f32": (4, "f"),
    "f64": (5, "d"),
    "utf8": (0, None),
}
_FORMAT_SIZES = {"B": 1, "i": 4, "I": 4, "f": 4, "d": 8, "Q": 8}
# Array columns are rebuilt this many samples at a time, so no flat list of the
# whole column is alive beside its per-sample lists.
_ROW_BATCH = 4096


class NativeStageError(RuntimeError):
    """A requested native stage is unavailable, failed, or returned an unusable result."""


def requested_stages(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """The stages ``IRACING_COACH_NATIVE_STAGES`` names; empty means pure Python."""

    raw = (os.environ if environ is None else environ).get(STAGES_ENV, "")
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(names) - set(KNOWN_STAGES))
    if unknown:
        raise NativeStageError(
            f"{STAGES_ENV} names unknown stage(s) {', '.join(unknown)}; "
            f"known stages: {', '.join(KNOWN_STAGES)}."
        )
    unavailable = sorted(set(names) - AVAILABLE_STAGES)
    if unavailable:
        raise NativeStageError(
            f"{STAGES_ENV} names stage(s) {', '.join(unavailable)} that this build does not "
            f"provide; available: {', '.join(sorted(AVAILABLE_STAGES))}."
        )
    return frozenset(names)


def native_binary(environ: Mapping[str, str] | None = None) -> str:
    """Path of the ``coach-analysis`` executable."""

    configured = (os.environ if environ is None else environ).get(BINARY_ENV, "")
    if configured:
        if not Path(configured).is_file():
            raise NativeStageError(f"{BINARY_ENV} does not name a file: {configured}")
        return configured
    found = shutil.which(BINARY_NAME)
    if not found:
        raise NativeStageError(
            f"A native stage was requested but {BINARY_NAME} is not on PATH; set {BINARY_ENV}."
        )
    return found


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise NativeStageError(f"coach column store: {message}")


def _cast(data: bytes, code: str) -> list[Any]:
    _check(len(data) % _FORMAT_SIZES[code] == 0, f"block of {len(data)} bytes is not whole {code!r} items")
    with memoryview(data) as view, view.cast(code) as typed:
        return typed.tolist()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _texts(name: Any, text: bytes, offsets: list[int], sample_count: int) -> list[str]:
    """One string per sample; the offsets must rise from 0 to the end of the text."""

    _check(len(offsets) == sample_count + 1, f"column {name!r} has {len(offsets) - 1} texts")
    _check(
        offsets[0] == 0 and offsets[-1] == len(text) and all(a <= b for a, b in itertools.pairwise(offsets)),
        f"column {name!r} text offsets must rise from 0 to the text length {len(text)}",
    )
    try:
        return [text[offsets[index] : offsets[index + 1]].decode("utf-8") for index in range(sample_count)]
    except UnicodeDecodeError as exc:
        raise NativeStageError(f"coach column store: column {name!r} text is not UTF-8: {exc}") from None


def _column_values(
    column: Mapping[str, Any], block: Callable[[Any], bytes], sample_count: int
) -> list[Any]:
    """One column as ``load_telemetry`` returns it."""

    name = column.get("name")
    element = column.get("element")
    _check(element in _ELEMENTS, f"column {name!r} has unknown element {element!r}")
    type_code, code = _ELEMENTS[element]
    _check(column.get("type_code") == type_code, f"column {name!r} type does not match {element}")
    count = column.get("count")
    _check(_is_int(count) and count > 0, f"column {name!r} has invalid count {count!r}")
    if code is None:  # char: one decoded string per sample
        offsets = _cast(block(column.get("text_offsets")), "Q")
        return _texts(name, block(column.get("data")), offsets, sample_count)
    _check("text_offsets" not in column, f"column {name!r} has text_offsets on a {element} column")
    data = block(column.get("data"))
    _check(
        len(data) == sample_count * count * _FORMAT_SIZES[code],
        f"column {name!r} has {len(data)} bytes for {sample_count} samples of {count} {element}",
    )
    as_bool = element == "bool"  # struct "?" and the typed-view path both give bool objects
    with memoryview(data) as view, view.cast(code) as typed:
        if count == 1:
            return list(map(bool, typed)) if as_bool else typed.tolist()
        rows: list[Any] = []
        batch = _ROW_BATCH * count
        for start in range(0, len(typed), batch):
            with typed[start : start + batch] as part:
                flat = list(map(bool, part)) if as_bool else part.tolist()
            rows.extend(flat[at : at + count] for at in range(0, len(flat), count))
        return rows


def read_store(path: str | os.PathLike[str]) -> tuple[dict[str, Any], list[int], dict[str, list[Any]]]:
    """Read a decode-stage column store: its header, ``sample_indices`` and ``samples``."""

    _check(sys.byteorder == "little", "native-endian casts need a little-endian host")
    with open(path, "rb") as handle:
        prefix = handle.read(16)
        _check(len(prefix) == 16 and prefix[:8] == STORE_MAGIC, "not a coach column store")
        version, header_length = struct.unpack("<II", prefix[8:])
        _check(version == STORE_VERSION, f"format version {version} is not supported (expected {STORE_VERSION})")
        header = json.loads(handle.read(header_length).decode("utf-8"))
        _check(
            header.get("format") == STORE_FORMAT and header.get("version") == STORE_VERSION,
            "header format or version does not match the file",
        )
        _check(header.get("stage") == "decode", f"stage {header.get('stage')!r} is not decode")
        base = (16 + header_length + 7) // 8 * 8
        file_size = os.fstat(handle.fileno()).st_size

        def block(spec: Any) -> bytes:
            # Whole, aligned and inside the data section: a negative offset must
            # never reach the header, a fractional one never be truncated.
            _check(isinstance(spec, Mapping), "missing block")
            offset, length = spec.get("offset"), spec.get("length")
            _check(_is_int(offset) and _is_int(length) and offset >= 0 and length >= 0, f"bad block {dict(spec)!r}")
            _check(offset % 8 == 0, f"block offset {offset} is not a multiple of 8")
            _check(base + offset + length <= file_size, "block runs past the end of the file")
            handle.seek(base + offset)
            data = handle.read(length)
            _check(len(data) == length, "block runs past the end of the file")
            return data

        sampling = header.get("sampling") or {}
        sample_count = sampling.get("sample_count")
        _check(_is_int(sample_count) and sample_count >= 0, f"bad sample_count {sample_count!r}")
        indices = _cast(block(header.get("sample_indices")), "I")
        _check(len(indices) == sample_count, f"{len(indices)} sample indices for {sample_count} samples")
        samples: dict[str, list[Any]] = {}
        for column in header.get("columns") or ():
            name = column.get("name")
            _check(isinstance(name, str) and name and name not in samples, f"bad column name {name!r}")
            samples[name] = _column_values(column, block, sample_count)
    return header, indices, samples


def _decode(binary: str, source: str, channels: list[str] | None, target_text: str | None) -> tuple[dict[str, Any], list[int], dict[str, list[Any]]]:
    with tempfile.TemporaryDirectory(prefix="coach-decode-") as work:
        out = Path(work) / "decode.ccs"
        request = {
            "version": REQUEST_VERSION,
            "source": source,
            "out": str(out),
            "channels": channels,
            "target_hz": target_text,
        }
        completed = subprocess.run(
            [binary, "decode"],
            input=json.dumps(request).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", "replace").strip() or f"exit status {completed.returncode}"
            raise NativeStageError(f"native decode of {source} failed: {message}")
        return read_store(out)


def _same_float(value: Any, expected: float) -> bool:
    return isinstance(value, float) and struct.pack("<d", value) == struct.pack("<d", expected)


def _check_sampling(
    sampling: Mapping[str, Any], indices: list[int], tick_rate: int, record_count: int, target_hz: Any
) -> float:
    """The store's sampling is the oracle's plan for this request, index for index.

    Returns the plan's output rate, the reader's ``sample_rate_hz``.
    """

    step, output_rate = _sampling_plan(tick_rate, target_hz)
    target = None if target_hz is None else float(target_hz)
    stored_target = sampling.get("target_hz")
    _check(
        stored_target is None if target is None else _same_float(stored_target, target),
        f"sampling target_hz {stored_target!r} is not {target!r}",
    )
    _check(_same_float(sampling.get("step"), step), f"sampling step {sampling.get('step')!r} is not {step!r}")
    _check(sampling.get("integral_step") is step.is_integer(), "sampling integral_step does not match the step")
    _check(
        _same_float(sampling.get("sample_rate_hz"), output_rate),
        f"sampling sample_rate_hz {sampling.get('sample_rate_hz')!r} is not {output_rate!r}",
    )
    # Streamed, so no second index list; a missing or extra index is a mismatch.
    def pairs() -> Iterable[tuple[Any, Any]]:
        return itertools.zip_longest(indices, _iter_sample_indices(record_count, step), fillvalue=None)

    if not all(itertools.starmap(operator.eq, pairs())):
        number, (stored, planned) = next(
            (number, pair) for number, pair in enumerate(pairs()) if pair[0] != pair[1]
        )
        _check(False, f"sample {number} has record index {stored!r}, the plan's is {planned!r}")
    return output_rate


def load_telemetry(
    path: os.PathLike[str] | str,
    channels: Iterable[str] | str | None = None,
    target_hz: float | int | None = 20,
) -> dict[str, Any]:
    """``ibt_reader.load_telemetry`` with the samples decoded by the native stage.

    Returns the same mapping, key for key and value for value. Channel names are
    validated by the native stage with the reader's rules and message. The store
    must match this scan's source layout and variables, the exact ordered channel
    selection, and the reader's own sampling plan index for index; anything else
    is ``NativeStageError``.
    """

    if target_hz is None:
        target_text = None
    else:
        if isinstance(target_hz, bool):
            raise ValueError("target_hz must be a positive finite number or None")
        try:
            target_text = repr(float(target_hz))
        except (TypeError, ValueError) as exc:
            raise ValueError("target_hz must be a positive finite number or None") from exc
    requested = None if channels is None else ([channels] if isinstance(channels, str) else list(channels))

    public = scan_ibt(path)
    session_info = public.pop("session_info")
    available = public.pop("variables")
    header, indices, samples = _decode(native_binary(), str(public["path"]), requested, target_text)

    source = header.get("source") or {}
    expected = {
        "file_size": public["file_size"],
        "record_count": public["record_count"],
        "tick_rate": public["header"]["tick_rate"],
        "buffer_offset": public["buffer_offset"],
        "buffer_length": public["header"]["buffer_length"],
        "num_variables": len(available),
    }
    for key, value in expected.items():
        _check(source.get(key) == value, f"source {key} {source.get(key)!r} differs from the scan's {value!r}")
    by_name = {variable["name"]: variable for variable in available}
    for column in header["columns"]:
        variable = by_name.get(column["name"])
        _check(
            variable is not None
            and variable["type_code"] == column["type_code"]
            and variable["count"] == column["count"]
            and variable["offset"] == column["var_offset"],
            f"column {column['name']!r} does not match the scanned variable",
        )
    mode = "all" if requested is None else "selected"
    _check(header.get("channel_mode") == mode, f"channel mode {header.get('channel_mode')!r} is not {mode!r}")
    # The exact ordered selection of _normalise_channels: the whole catalogue in
    # file order, or the requested names in caller order without repeats.
    expected_names = (
        [variable["name"] for variable in available] if requested is None else list(dict.fromkeys(requested))
    )
    _check(
        list(samples) == expected_names,
        f"columns {list(samples)[:8]!r} are not the selection {expected_names[:8]!r}"
        f" ({len(samples)} and {len(expected_names)} names)",
    )
    output_rate = _check_sampling(
        header.get("sampling") or {}, indices, public["header"]["tick_rate"], public["record_count"], target_hz
    )

    return {
        "metadata": public,
        "session_info": session_info,
        "available_variables": available,
        "variables": [dict(by_name[name]) for name in samples],
        "samples": samples,
        "sample_indices": indices,
        "native_tick_rate_hz": public["header"]["tick_rate"],
        "sample_rate_hz": output_rate,
        "source_record_count": public["record_count"],
        "sample_count": len(indices),
        "channel_selection": {
            "mode": mode,
            "available_count": len(available),
            "decoded_count": len(samples),
        },
    }


__all__ = [
    "AVAILABLE_STAGES",
    "BINARY_ENV",
    "KNOWN_STAGES",
    "NativeStageError",
    "STAGES_ENV",
    "load_telemetry",
    "native_binary",
    "read_store",
    "requested_stages",
]
