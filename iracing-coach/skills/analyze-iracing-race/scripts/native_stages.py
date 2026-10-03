"""Native (Rust) stage substitution for the analysis worker.

``IRACING_COACH_NATIVE_STAGES`` is a comma list of stage names
(``docs/08-rewrite/contracts/worker-interface.md`` section 7). Unset or empty,
every stage runs in Python and nothing here is called. A listed stage that
this build does not provide fails the job: a silent Python fallback would let
a parity run compare Python with Python.

``decode`` (phase 1): :func:`load_telemetry` is ``ibt_reader.load_telemetry``
with the sample decode done by ``coach-analysis decode`` (crate
``crates/coach-analysis``): the recording is still scanned here (header,
session info, variable catalogue, summary), the native stage writes the
selected columns to a coach column store, and this module turns each column
into exactly the Python values the reader returns.

``kernels`` (phase 2, requires ``decode``): ``load_telemetry(...,
columns="store")`` returns :class:`StoreColumn` sequences over the mapped
store instead of lists, a multi-file group is merged by ``coach-analysis
merge``, and :func:`kernel_session` runs one analysis with
``analysis_engine.TelemetryTable`` rebound to :class:`StoreTable` and every
ported entry point (:data:`KERNEL_ENTRIES`) rebound to a shim that calls the
``coach-analysis kernels`` child process through :func:`kernel_call`. No file
of the analyzer bundle changes, so ``analysis_id`` is unchanged.

The executable is ``IRACING_COACH_NATIVE_BIN`` or ``coach-analysis`` on
``PATH``.
"""

from __future__ import annotations

import builtins
import contextlib
import itertools
import json
import mmap
import operator
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import weakref
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

try:  # Package import and direct script execution are both supported.
    from . import analysis_engine
    from . import native_codec
    from .ibt_reader import _iter_sample_indices, _sampling_plan, scan_ibt
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    import analysis_engine
    import native_codec
    from ibt_reader import _iter_sample_indices, _sampling_plan, scan_ibt


STAGES_ENV = "IRACING_COACH_NATIVE_STAGES"
BINARY_ENV = "IRACING_COACH_NATIVE_BIN"
BINARY_NAME = "coach-analysis"
KNOWN_STAGES = ("decode", "kernels", "json_writer")
AVAILABLE_STAGES = frozenset({"decode", "kernels"})
# A stage that only runs on another stage's output.
REQUIRES = {"kernels": "decode"}
REQUEST_VERSION = 1
KERNELS_VERSION = 1
STORE_MAGIC = b"COACHCS\x00"
STORE_FORMAT = "coach-column-store"
STORE_VERSION = 1

# The entry points the kernels replace (docs/08-rewrite/port/phase2-plan.md
# section 2): (owner, name), the owner being analysis_engine itself ("") or a
# module it imports. Each is looked up at call time by the oracle, so the
# session rebinds it on its owner for the duration of one analysis.
KERNEL_ENTRIES: tuple[tuple[str, str], ...] = (
    ("", "_setup_channel_series"),
    ("", "_setup_telemetry"),
    ("", "_conditions_summary"),
    ("", "_driver_adjustments_summary"),
    ("", "_caution_flag_series"),
    ("", "_dt_series"),
    ("", "_lap_summaries"),
    ("", "_service_events"),
    ("", "_disrupted_sample_indices"),
    ("", "_lap_trace_payload"),
    ("", "_track_profile"),
    ("", "_corner_tire_age_summary"),
    ("", "_pace_attribution"),
    ("", "_clean_lap_traces"),
    ("pit_loss", "measure_pit_loss"),
    ("", "analyze_groove_evolution"),
    ("", "build_track_geometry"),
)


def entry_label(owner: str, name: str) -> str:
    """``analysis_engine._lap_summaries``, ``pit_loss.measure_pit_loss``: the vector and protocol name."""

    return f"{owner or 'analysis_engine'}.{name}"


KERNEL_LABELS = tuple(entry_label(owner, name) for owner, name in KERNEL_ENTRIES)

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
_FORMAT_SIZES = {"B": 1, "H": 2, "i": 4, "I": 4, "f": 4, "d": 8, "Q": 8}
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
    for stage, needed in REQUIRES.items():
        if stage in names and needed not in names:
            raise NativeStageError(
                f"{STAGES_ENV} names {stage} without {needed}: {stage} reads the {needed} stage's store."
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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Coach column store: mapped, validated, read as sequences
# ---------------------------------------------------------------------------


class StoreColumn(native_codec.LazySequence):
    """One store column as a read-only sequence of exactly the reader's values.

    ``len``, indexing and slicing convert only the items asked for (a slice is
    a new ``list``, as for a list); iteration walks the mapped data. Items are
    ``read_store``'s: ``bool``, ``int``, ``float`` widened from the stored
    width, ``str``, a ``list`` of ``count`` items for an array variable, and
    ``None`` for a cell a merge store marks absent.
    """

    __slots__ = ("name", "element", "count", "_values", "_text", "_offsets", "_present", "_length", "_store")

    def __init__(
        self,
        name: str,
        element: str,
        count: int,
        length: int,
        values: memoryview | None,
        *,
        text: memoryview | None = None,
        offsets: memoryview | None = None,
        present: memoryview | None = None,
        store: StoreFile | None = None,
    ) -> None:
        self._store = store  # the mapping stays open while a column is reachable
        self.name = name
        self.element = element
        self.count = count
        self._length = length
        self._values = values
        self._text = text
        self._offsets = offsets
        self._present = present

    def __len__(self) -> int:
        return self._length

    def __repr__(self) -> str:
        return f"StoreColumn({self.name!r}, {self.element}, count={self.count}, length={self._length})"

    def _item(self, index: int) -> Any:
        if self._present is not None and not self._present[index]:
            return None
        if self._text is not None:
            offsets = self._offsets
            return str(self._text[offsets[index] : offsets[index + 1]], "utf-8")
        if self.count != 1:
            start = index * self.count
            with self._values[start : start + self.count] as row:
                return list(map(bool, row)) if self.element == "bool" else row.tolist()
        value = self._values[index]
        return bool(value) if self.element == "bool" else value

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            positions = range(*index.indices(self._length))
            if self._text is None and self.count == 1 and self._present is None:
                with self._values[index] as part:
                    return list(map(bool, part)) if self.element == "bool" else part.tolist()
            return [self._item(position) for position in positions]
        position = operator.index(index)
        if position < 0:
            position += self._length
        if not 0 <= position < self._length:
            raise IndexError("list index out of range")
        return self._item(position)

    def __iter__(self) -> Iterator[Any]:
        if self._text is None and self.count == 1 and self._present is None:
            return map(bool, self._values) if self.element == "bool" else iter(self._values)
        return map(self._item, range(self._length))

    def tolist(self) -> list[Any]:
        """The whole column as ``read_store`` returns it (array rows rebuilt in batches)."""

        if self._text is not None or self._present is not None:
            return [self._item(position) for position in range(self._length)]
        if self.count == 1:
            return list(map(bool, self._values)) if self.element == "bool" else self._values.tolist()
        rows: list[Any] = []
        batch = _ROW_BATCH * self.count
        for start in range(0, len(self._values), batch):
            with self._values[start : start + batch] as part:
                flat = list(map(bool, part)) if self.element == "bool" else part.tolist()
            rows.extend(flat[at : at + self.count] for at in range(0, len(flat), self.count))
        return rows


class ProvenanceSequence(native_codec.LazySequence):
    """``sample_provenance`` computed on access from the store's sample record indices.

    Item ``i`` is ``{"source_file_index", "source_path", "native_record_index"}``
    as ``workflow._load_analysis_telemetry`` builds it; nothing in the analysis
    reads it (phase1-decode.md section 5). A merged store keeps no
    ``contributors`` list for samples that filled a duplicate's empty cells.
    """

    __slots__ = ("_files", "_records", "_paths")

    def __init__(self, records: Sequence[int], paths: Sequence[str], files: Sequence[int] | None = None) -> None:
        self._records = records
        self._paths = list(paths)
        self._files = files

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        file_index = 0 if self._files is None else self._files[index]
        return {
            "source_file_index": file_index,
            "source_path": self._paths[file_index],
            "native_record_index": int(self._records[index]),
        }


def _close_resources(resources: dict[str, Any]) -> None:
    """Release every view, unmap, then delete the owned directory (the mapping must be gone first on Windows)."""

    for view in reversed(resources["views"]):
        with contextlib.suppress(BufferError, ValueError):
            view.release()
    resources["views"].clear()
    mapped, resources["map"] = resources["map"], None
    if mapped is not None:
        with contextlib.suppress(BufferError, ValueError):
            mapped.close()
    directory, resources["directory"] = resources["directory"], None
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)


class StoreFile:
    """A coach column store mapped read-only and validated before any column is read.

    ``stages`` names the store stages this caller accepts (``decode``, and
    ``merge`` for a merged group). ``directory``, when given, is a temporary
    directory this object owns and deletes on :meth:`close`, or when neither it
    nor any of its columns is reachable any more, or at exit.
    """

    def __init__(self, path: str | os.PathLike[str], *, stages: Iterable[str] = ("decode",), directory: str | None = None) -> None:
        _check(sys.byteorder == "little", "native-endian casts need a little-endian host")
        self.path = str(path)
        self._resources: dict[str, Any] = {"views": [], "map": None, "directory": directory}
        self._finalizer = weakref.finalize(self, _close_resources, self._resources)
        try:
            with open(path, "rb") as handle:  # the mapping keeps its own handle
                self._open(handle, set(stages))
        except BaseException:
            self.close()
            raise

    def _view(self, view: memoryview) -> memoryview:
        self._resources["views"].append(view)
        return view

    def _open(self, handle: Any, stages: set[str]) -> None:
        prefix = handle.read(16)
        _check(len(prefix) == 16 and prefix[:8] == STORE_MAGIC, "not a coach column store")
        version, header_length = struct.unpack("<II", prefix[8:])
        _check(version == STORE_VERSION, f"format version {version} is not supported (expected {STORE_VERSION})")
        header = json.loads(handle.read(header_length).decode("utf-8"))
        _check(
            header.get("format") == STORE_FORMAT and header.get("version") == STORE_VERSION,
            "header format or version does not match the file",
        )
        stage = header.get("stage")
        _check(stage in stages, f"stage {stage!r} is not {' or '.join(sorted(stages))}")
        self.header: dict[str, Any] = header
        self.stage: str = stage
        base = (16 + header_length + 7) // 8 * 8
        file_size = os.fstat(handle.fileno()).st_size
        mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        self._resources["map"] = mapped
        whole = self._view(memoryview(mapped))

        def block(spec: Any, code: str = "B") -> memoryview:
            # Whole, aligned and inside the data section: a negative offset must
            # never reach the header, a fractional one never be truncated.
            _check(isinstance(spec, Mapping), "missing block")
            offset, length = spec.get("offset"), spec.get("length")
            _check(_is_int(offset) and _is_int(length) and offset >= 0 and length >= 0, f"bad block {dict(spec)!r}")
            _check(offset % 8 == 0, f"block offset {offset} is not a multiple of 8")
            _check(base + offset + length <= file_size, "block runs past the end of the file")
            _check(length % _FORMAT_SIZES[code] == 0, f"block of {length} bytes is not whole {code!r} items")
            raw = self._view(whole[base + offset : base + offset + length])
            return raw if code == "B" else self._view(raw.cast(code))

        sampling = header.get("sampling") or {}
        count = sampling.get("sample_count")
        _check(_is_int(count) and count >= 0, f"bad sample_count {count!r}")
        self.sample_count: int = count
        # sample_indices: each sample's native record index (in its source file, for a merge).
        indices = block(header.get("sample_indices"), "I")
        _check(len(indices) == count, f"{len(indices)} sample indices for {count} samples")
        self.sample_indices = StoreColumn("sample_indices", "u32", 1, count, indices, store=self)
        self.file_indices: memoryview | None = None
        if stage == "merge":
            provenance = header.get("provenance")
            _check(isinstance(provenance, Mapping), "a merge store needs provenance")
            files = block(provenance.get("file_index"), "H")
            _check(len(files) == count, f"provenance does not cover {count} samples")
            self.file_indices = files
        self.columns: dict[str, StoreColumn] = {}
        for column in header.get("columns") or ():
            name = column.get("name")
            _check(isinstance(name, str) and name and name not in self.columns, f"bad column name {name!r}")
            self.columns[name] = self._column(column, block, count)

    def _column(self, column: Mapping[str, Any], block: Callable[..., memoryview], count: int) -> StoreColumn:
        name = column.get("name")
        element = column.get("element")
        _check(element in _ELEMENTS, f"column {name!r} has unknown element {element!r}")
        type_code, code = _ELEMENTS[element]
        _check(column.get("type_code") == type_code, f"column {name!r} type does not match {element}")
        items = column.get("count")
        _check(_is_int(items) and items > 0, f"column {name!r} has invalid count {items!r}")
        present = None
        if self.stage == "merge" and "present" in column:
            present = block(column.get("present"))
            _check(len(present) == count, f"column {name!r} has {len(present)} presence bytes for {count} samples")
            _check(not present.tobytes().translate(None, b"\x00\x01"), f"column {name!r} presence bytes must be 0 or 1")
        else:
            _check("present" not in column, f"column {name!r} has a presence block in a {self.stage} store")
        if code is None:  # char: one decoded string per sample
            offsets = block(column.get("text_offsets"), "Q")
            text = block(column.get("data"))
            _check(len(offsets) == count + 1, f"column {name!r} has {len(offsets) - 1} texts")
            _check(
                offsets[0] == 0 and offsets[-1] == len(text) and all(a <= b for a, b in itertools.pairwise(offsets)),
                f"column {name!r} text offsets must rise from 0 to the text length {len(text)}",
            )
            for position in range(count):  # validated once; decoded again on access
                try:
                    str(text[offsets[position] : offsets[position + 1]], "utf-8")
                except UnicodeDecodeError as exc:
                    raise NativeStageError(f"coach column store: column {name!r} text is not UTF-8: {exc}") from None
            return StoreColumn(name, element, items, count, None, text=text, offsets=offsets, present=present, store=self)
        _check("text_offsets" not in column, f"column {name!r} has text_offsets on a {element} column")
        values = block(column.get("data"), code)
        _check(
            len(values) == count * items,
            f"column {name!r} has {len(values) * _FORMAT_SIZES[code]} bytes for {count} samples of {items} {element}",
        )
        return StoreColumn(name, element, items, count, values, present=present, store=self)

    def close(self) -> None:
        """Release every view, unmap, close and delete the owned directory (idempotent)."""

        self._finalizer()

    def __enter__(self) -> StoreFile:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_store(path: str | os.PathLike[str]) -> tuple[dict[str, Any], list[int], dict[str, list[Any]]]:
    """Read a decode-stage column store: its header, ``sample_indices`` and ``samples``."""

    with StoreFile(path) as store:
        return store.header, store.sample_indices.tolist(), {name: column.tolist() for name, column in store.columns.items()}


def _run_native(binary: str, command: str, request: Mapping[str, Any], what: str) -> None:
    completed = subprocess.run(
        [binary, command],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip() or f"exit status {completed.returncode}"
        raise NativeStageError(f"native {command} of {what} failed: {message}")


def _decode_store(binary: str, source: str, channels: list[str] | None, target_text: str | None) -> StoreFile:
    """Run ``coach-analysis decode`` into a ``coach-decode-*`` directory the returned store owns."""

    work = tempfile.mkdtemp(prefix="coach-decode-")
    try:
        out = Path(work) / "decode.ccs"
        request = {
            "version": REQUEST_VERSION,
            "source": source,
            "out": str(out),
            "channels": channels,
            "target_hz": target_text,
        }
        _run_native(binary, "decode", request, source)
        return StoreFile(out, directory=work)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _decode(binary: str, source: str, channels: list[str] | None, target_text: str | None) -> tuple[dict[str, Any], Sequence[int], Mapping[str, Sequence[Any]]]:
    """The decoded store as lists (``decode``); the store file is gone on return."""

    with _decode_store(binary, source, channels, target_text) as store:
        return store.header, store.sample_indices.tolist(), {name: column.tolist() for name, column in store.columns.items()}


class StoreSamples(dict):
    """The ``samples`` mapping of a store-backed table: channel name to :class:`StoreColumn`, plus its store."""

    __slots__ = ("store",)

    def __init__(self, store: StoreFile) -> None:
        super().__init__(store.columns)
        self.store = store


def _decode_columns(binary: str, source: str, channels: list[str] | None, target_text: str | None) -> tuple[dict[str, Any], Sequence[int], Mapping[str, Sequence[Any]]]:
    """The decoded store as :class:`StoreColumn` sequences; the store stays mapped."""

    store = _decode_store(binary, source, channels, target_text)
    return store.header, store.sample_indices, StoreSamples(store)


def store_of(samples: Any) -> StoreFile | None:
    """The store behind a ``samples`` mapping :func:`load_telemetry` returned with ``columns="store"``."""

    return samples.store if isinstance(samples, StoreSamples) else None


def _same_float(value: Any, expected: float) -> bool:
    return isinstance(value, float) and struct.pack("<d", value) == struct.pack("<d", expected)


def _check_sampling(
    sampling: Mapping[str, Any], indices: Sequence[int], tick_rate: int, record_count: int, target_hz: Any
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
    *,
    columns: str = "lists",
) -> dict[str, Any]:
    """``ibt_reader.load_telemetry`` with the samples decoded by the native stage.

    Returns the same mapping, key for key and value for value. Channel names are
    validated by the native stage with the reader's rules and message. The store
    must match this scan's source layout and variables, the exact ordered channel
    selection, and the reader's own sampling plan index for index; anything else
    is ``NativeStageError``.

    ``columns="store"`` (the ``kernels`` stage) returns each channel and
    ``sample_indices`` as a :class:`StoreColumn` over the mapped store, which
    stays in its ``coach-decode-*`` directory until :func:`release` (or
    :func:`kernel_session`'s end) closes it.
    """

    if columns not in ("lists", "store"):
        raise ValueError(f"columns must be 'lists' or 'store', not {columns!r}")
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
    decode = _decode_columns if columns == "store" else _decode
    header, indices, samples = decode(native_binary(), str(public["path"]), requested, target_text)
    try:
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
    except BaseException:
        release(samples)
        raise

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


def release(samples: Any) -> None:
    """Close the store behind a store-backed ``samples`` mapping (no-op for lists)."""

    store = store_of(samples)
    if store is not None:
        store.close()


def merge_stores(
    tables: Sequence[Mapping[str, Any]], channel_names: Sequence[str], source_paths: Sequence[str]
) -> tuple[StoreSamples, ProvenanceSequence]:
    """Merge store-backed per-file tables with ``coach-analysis merge``.

    The column merge of ``workflow._load_analysis_telemetry`` (samples ordered
    by ``(has_no_time, time, file_index, sample_index)``; a sample within
    1e-9 s of the previous kept time fills only that row's empty cells, first
    non-null wins), written as a ``merge`` store. Returns the merged columns
    (``None`` where a file lacks a channel) and the provenance sequence; the
    caller releases the per-file stores.
    """

    stores = [store_of(table.get("samples")) for table in tables]
    if any(store is None for store in stores):
        raise NativeStageError("the kernels merge needs every file decoded into a store")
    work = tempfile.mkdtemp(prefix="coach-merge-")
    try:
        out = Path(work) / "merge.ccs"
        request = {
            "version": REQUEST_VERSION,
            "inputs": [store.path for store in stores],  # type: ignore[union-attr]
            "channels": list(channel_names),
            "out": str(out),
        }
        _run_native(native_binary(), "merge", request, f"{len(stores)} recordings")
        merged = StoreFile(out, stages=("merge",), directory=work)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    _check(list(merged.columns) == list(channel_names), "merge columns are not the requested channel order")
    _check(
        all(index < len(stores) for index in (merged.file_indices or ())),
        "merge provenance names a file that is not an input",
    )
    return StoreSamples(merged), ProvenanceSequence(merged.sample_indices, source_paths, merged.file_indices)


# ---------------------------------------------------------------------------
# Kernels: the store-backed table, the child process and the shims
# ---------------------------------------------------------------------------


class StoreTable(analysis_engine.TelemetryTable):
    """``TelemetryTable`` over :class:`StoreColumn` sequences instead of list copies.

    Every attribute is derived by the oracle's own ``__init__`` (run on the
    mapping with empty ``samples``); only ``channels`` and ``length`` are then
    set from the store. Every inherited method is the oracle's code over the
    sequences, so the Python-remaining callers get the oracle's values.
    """

    def __init__(self, telemetry: Mapping[str, Any]) -> None:
        samples = telemetry.get("samples")
        if not isinstance(samples, Mapping) or not all(isinstance(values, StoreColumn) for values in samples.values()):
            raise NativeStageError("the kernels stage needs the store-backed table (decode with columns='store')")
        if telemetry.get("channels") or telemetry.get("data"):
            raise NativeStageError("the kernels stage reads samples only, not channels or data")
        super().__init__({**telemetry, "samples": {}})
        lengths = {len(values) for values in samples.values()}
        if len(lengths) > 1:
            raise NativeStageError(f"store columns have different lengths {sorted(lengths)}")
        self.channels = {str(name): values for name, values in samples.items()}
        self.length = lengths.pop() if lengths else 0
        session = _ACTIVE
        if session is not None:
            session.attach(self, telemetry)


_ACTIVE: _KernelSession | None = None


def kernel_call(label: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Run one entry point in the ``kernels`` child: every shim goes through here.

    The parity gate wraps this function to count native calls against the
    oracle's calls of the same entry points (worker-interface.md section 8).
    """

    session = _ACTIVE
    if session is None:
        raise NativeStageError(f"{label} was called outside a kernel session")
    return session.call(label, args, kwargs)


def _shim(label: str) -> Callable[..., Any]:
    def shim(*args: Any, **kwargs: Any) -> Any:
        return kernel_call(label, args, kwargs)

    shim.__name__ = shim.__qualname__ = label.rpartition(".")[2]
    shim.__native_kernel__ = label  # type: ignore[attr-defined]
    return shim


def _materialised_digest(original: Callable[..., Any]) -> Callable[..., Any]:
    """``_fallback_telemetry_digest`` over plain lists: ``json.dumps(default=str)`` would stringify a column."""

    def digest(table: Any, *args: Any, **kwargs: Any) -> Any:
        columns = table.channels
        table.channels = {name: values.tolist() if isinstance(values, StoreColumn) else values for name, values in columns.items()}
        try:
            return original(table, *args, **kwargs)
        finally:
            table.channels = columns

    return digest


def _exception(name: Any, message: Any, label: str) -> BaseException:
    kind = getattr(builtins, name, None) if isinstance(name, str) else None
    if not (isinstance(kind, type) and issubclass(kind, Exception)):
        return NativeStageError(f"kernel {label} raised an unknown exception class {name!r}: {message}")
    return kind(message)


class _KernelSession:
    """One ``coach-analysis kernels`` child for one analysis, started at the first call."""

    def __init__(self, command: Sequence[str], store: StoreFile) -> None:
        self.command = list(command)
        self.store = store
        self.table: StoreTable | None = None
        self.context: native_codec.Context | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.stderr: Any = None
        self.entries: frozenset[str] = frozenset()
        self.calls = 0

    def attach(self, table: StoreTable, telemetry: Mapping[str, Any]) -> None:
        if self.table is not None:
            raise NativeStageError("one kernel session serves one analysis table")
        if store_of(telemetry.get("samples")) is not self.store:
            raise NativeStageError("the analysis table is not the session's store")
        self.table = table
        self.context = native_codec.Context(table)

    def _start(self) -> None:
        table = self.table
        if table is None:
            raise NativeStageError("a kernel was called before the analysis table exists")
        # The oracle computes and caches the basis before its first ported call
        # (analyze_telemetry); this returns that cached value.
        basis = analysis_engine._steering_basis(table)
        meta_context = native_codec.Context()
        start = {
            "version": KERNELS_VERSION,
            "codec": native_codec.CODEC_VERSION,
            "store": self.store.path,
            "table": {
                "codec": native_codec.CODEC_VERSION,
                "length": table.length,
                "channel_names": list(table.channels),
                "metadata": native_codec.encode(table.metadata, meta_context, None),
                "session_info": native_codec.encode(table.session_info, meta_context, None),
                "variables": native_codec.encode(table.variables, meta_context, None),
                "available_variables": native_codec.encode(table.available_variables, meta_context, None),
                "channel_units": table.channel_units,
            },
            "steering_basis": native_codec.encode(basis, meta_context, None),
        }
        self.stderr = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr
            )
        except OSError as exc:
            raise NativeStageError(f"cannot start {self.command[0]} kernels: {exc}") from exc
        answer = self._exchange(start, "start")
        if answer.get("ok") is not True or answer.get("length") != table.length:
            raise NativeStageError(f"kernels start answered {answer!r} for {table.length} samples")
        provided = answer.get("entries")
        if not isinstance(provided, list) or not all(isinstance(item, str) for item in provided):
            raise NativeStageError("kernels start did not list its entry points")
        missing = [label for label in KERNEL_LABELS if label not in provided]
        if missing:
            raise NativeStageError(f"this coach-analysis build does not provide kernel(s) {', '.join(missing)}")
        self.entries = frozenset(provided)

    def _failure(self) -> str:
        process, stderr = self.process, self.stderr
        if process is not None and process.poll() is None:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
        text = ""
        if stderr is not None:
            stderr.seek(0)
            text = stderr.read().decode("utf-8", "replace").strip()
        status = process.returncode if process is not None else None
        return text[-2000:] or f"exit status {status}"

    def _exchange(self, request: Mapping[str, Any], what: str) -> dict[str, Any]:
        process = self.process
        assert process is not None and process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(native_codec.dumps(request).encode("utf-8") + b"\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except OSError as exc:
            raise NativeStageError(f"kernels {what}: the process failed: {self._failure()}") from exc
        if not line.endswith(b"\n"):
            raise NativeStageError(f"kernels {what}: the process ended: {self._failure()}")
        try:
            answer = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            raise NativeStageError(f"kernels {what}: unreadable answer: {exc}") from exc
        if not isinstance(answer, dict):
            raise NativeStageError(f"kernels {what}: answer is not an object")
        if "error" in answer:
            raise NativeStageError(f"kernels {what}: {answer['error']}")
        return answer

    def call(self, label: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if self.process is None:
            self._start()
        if label not in self.entries:
            raise NativeStageError(f"kernel {label} is not provided by this build")
        table, context = self.table, self.context
        assert table is not None and context is not None
        request = {
            "entry": label,
            "args": native_codec.encode(list(args), context, None),
            "kwargs": native_codec.encode(kwargs, context, None),
        }
        self.calls += 1
        answer = self._exchange(request, label)
        # worker-interface.md section 8: exactly one outcome, and always the access record.
        if ("result" in answer) == ("raises" in answer):
            raise NativeStageError(f"kernel {label} must answer exactly one of result and raises")
        if "accessed" not in answer:
            raise NativeStageError(f"kernel {label} answered without its accessed channels")
        accessed = answer["accessed"]
        if not isinstance(accessed, list) or not all(isinstance(name, str) and name in table.channels for name in accessed):
            raise NativeStageError(f"kernel {label} returned bad accessed channels {accessed!r}")
        if "raises" in answer:
            raised = answer["raises"]
            if not isinstance(raised, dict):
                raise NativeStageError(f"kernel {label} returned a bad exception {raised!r}")
            table.accessed_channels.update(accessed)
            raise _exception(raised.get("class"), raised.get("message"), label)
        try:
            result = native_codec.decode(answer["result"], context, {})
        except (native_codec.CodecError, KeyError, TypeError, ValueError, ImportError, AttributeError) as exc:
            raise NativeStageError(f"kernel {label} returned an undecodable result: {exc}") from exc
        table.accessed_channels.update(accessed)
        return result

    def close(self, failed: bool) -> None:
        """Close standard input and wait; a non-zero exit fails a job that had not already failed."""

        process = self.process
        try:
            if process is None:
                return
            with contextlib.suppress(OSError):
                process.stdin.close()  # type: ignore[union-attr]
            try:
                status = process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                status = None
            if status != 0 and not failed:
                raise NativeStageError(f"kernels did not exit cleanly: {self._failure()}")
            if status is None:
                self._failure()
        finally:
            self.process = None
            if process is not None and process.stdout is not None:
                process.stdout.close()
            if self.stderr is not None:
                self.stderr.close()
                self.stderr = None


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite token {token}")


def _owner(name: str) -> Any:
    return analysis_engine if not name else getattr(analysis_engine, name)


@contextlib.contextmanager
def kernel_session(telemetry: Mapping[str, Any], *, command: Sequence[str] | None = None) -> Iterator[_KernelSession]:
    """Run one analysis of ``telemetry`` with the ported entry points in the ``kernels`` child.

    ``telemetry`` must come from ``load_telemetry(..., columns="store")`` (or
    :func:`merge_stores`). For the duration of the block,
    ``analysis_engine.TelemetryTable`` is :class:`StoreTable` and each of
    :data:`KERNEL_ENTRIES` is a shim calling :func:`kernel_call`; everything is
    restored, the child stopped and the store closed in ``finally``.
    ``command`` replaces ``[coach-analysis, "kernels"]`` (tests).
    """

    global _ACTIVE
    store = store_of(telemetry.get("samples"))
    if store is None:
        raise NativeStageError("kernels needs the decode stage's store-backed table; it never converts lists")
    if _ACTIVE is not None:
        raise NativeStageError("a kernel session is already running in this process")
    session = _KernelSession(command or [native_binary(), "kernels"], store)
    restore: list[tuple[Any, str, Any]] = []
    failed = True
    try:
        restore.append((analysis_engine, "TelemetryTable", analysis_engine.TelemetryTable))
        analysis_engine.TelemetryTable = StoreTable  # type: ignore[misc]
        for owner_name, name in KERNEL_ENTRIES:
            owner = _owner(owner_name)
            restore.append((owner, name, getattr(owner, name)))
            setattr(owner, name, _shim(entry_label(owner_name, name)))
        digest = analysis_engine._fallback_telemetry_digest
        restore.append((analysis_engine, "_fallback_telemetry_digest", digest))
        analysis_engine._fallback_telemetry_digest = _materialised_digest(digest)
        _ACTIVE = session
        yield session
        failed = False
    finally:
        _ACTIVE = None
        while restore:
            owner, name, original = restore.pop()
            setattr(owner, name, original)
        try:
            session.close(failed)
        finally:
            release(telemetry.get("samples"))


def install_call_counters() -> tuple[dict[str, dict[str, int]], Callable[[], None]]:
    """Count kernel calls for the parity gate (worker-interface.md section 8).

    Returns ``(counts, uninstall)``. ``counts["native"]`` counts
    :func:`kernel_call` by entry; ``counts["oracle"]`` counts calls of the
    Python entry points themselves, outermost only: an entry called inside
    another entry runs inside that entry's kernel, so the native side never
    sees it. A native capture must show ``native`` equal to the oracle
    capture's ``oracle``, count for count, and no ``oracle`` call of its own
    (a kernel session rebinds the counted entries to shims while it runs).
    """

    counts: dict[str, dict[str, int]] = {"native": {}, "oracle": {}}
    depth = [0]
    restore: list[tuple[Any, str, Any]] = []

    def counted_entry(label: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def entry(*args: Any, **kwargs: Any) -> Any:
            if depth[0] == 0:
                counts["oracle"][label] = counts["oracle"].get(label, 0) + 1
            depth[0] += 1
            try:
                return original(*args, **kwargs)
            finally:
                depth[0] -= 1

        entry.__wrapped__ = original  # type: ignore[attr-defined]
        return entry

    for owner_name, name in KERNEL_ENTRIES:
        owner = _owner(owner_name)
        original = getattr(owner, name)
        restore.append((owner, name, original))
        setattr(owner, name, counted_entry(entry_label(owner_name, name), original))
    module = sys.modules[__name__]
    native_call = module.kernel_call

    def counted_call(label: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        counts["native"][label] = counts["native"].get(label, 0) + 1
        return native_call(label, args, kwargs)

    restore.append((module, "kernel_call", native_call))
    module.kernel_call = counted_call  # type: ignore[attr-defined]

    def uninstall() -> None:
        while restore:
            owner, name, original = restore.pop()
            setattr(owner, name, original)

    return counts, uninstall


def kernel_call_problems(oracle: Mapping[str, Mapping[str, int]], native: Mapping[str, Mapping[str, int]], kernels: bool) -> list[str]:
    """Why two captures' call counts do not show the kernels ran natively, count for count."""

    problems: list[str] = []
    if oracle.get("native"):
        problems.append(f"the oracle capture made kernel calls {dict(oracle['native'])}")
    if not kernels:
        if native.get("native"):
            problems.append(f"the native capture made kernel calls without the kernels stage {dict(native['native'])}")
        return problems
    if native.get("oracle"):
        problems.append(f"the native capture ran Python entry points {dict(native['oracle'])}")
    expected, actual = dict(oracle.get("oracle") or {}), dict(native.get("native") or {})
    if not expected:
        problems.append("the oracle capture shows no entry-point call to compare")
    for label in sorted(expected.keys() | actual.keys()):
        if expected.get(label, 0) != actual.get(label, 0):
            problems.append(f"{label}: {actual.get(label, 0)} kernel call(s), the oracle made {expected.get(label, 0)}")
    return problems


__all__ = [
    "AVAILABLE_STAGES",
    "BINARY_ENV",
    "KERNEL_ENTRIES",
    "KERNEL_LABELS",
    "KNOWN_STAGES",
    "NativeStageError",
    "ProvenanceSequence",
    "STAGES_ENV",
    "StoreColumn",
    "StoreFile",
    "StoreSamples",
    "StoreTable",
    "entry_label",
    "install_call_counters",
    "kernel_call",
    "kernel_call_problems",
    "kernel_session",
    "load_telemetry",
    "merge_stores",
    "native_binary",
    "read_store",
    "release",
    "requested_stages",
]
