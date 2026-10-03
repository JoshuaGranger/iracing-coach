#!/usr/bin/env python3
"""Record port-phase-2 kernel vectors from the Python oracle.

For every recording, the oracle decodes the analysis table exactly as the
worker does (``workflow._load_analysis_telemetry``, one file, ``target_hz``
null unless ``--target-hz``) and runs ``analysis_engine.analyze_telemetry``
once with each selected entry point wrapped. Every call is recorded at call
time: the arguments as they are on entry, the result (or the raised
exception), the arguments a call mutated as they are on return, and the
table channels the call itself accessed. Values are written by
``kernel_codec`` (floats as ``repr``, column-valued lists to a per-call
vector store); the analysis table goes once per recording to ``table.ccs``,
a decode-stage store ``coach_analysis::store::ColumnStore`` reads.

Layout under ``--out`` (``docs/08-rewrite/port/phase2-plan.md`` section 3)::

    <platform>/<recording>[-<rate>hz]/table.ccs, table.json
    <platform>/<recording>[-<rate>hz]/<unit>/<module>.<entry>/<call>.json [.ccs]
    <platform>/<recording>[-<rate>hz]/<unit>/index.json

``<recording>`` is the first 12 hex digits of the recording's SHA-256 (of
``synthetic:<name>`` for a synthetic table). Each unit agent adds its entry
points to ``UNITS``, and where needed a probe to ``PROBES`` (vectors that are
not calls) or a builder to ``SYNTHETIC`` (a table for a path no recording
reaches); nothing else here should need to change. ``--entry module:name``
records any function ad hoc.

Run with CPython 3.12 (the bundled runtime on Windows, ``python3.12`` in WSL;
the Linux interpreter is the binding oracle)::

    python -X utf8 crates/coach-analysis/tools/emit_kernel_vectors.py --unit laps --fixtures \
        --out crates/coach-analysis/tests/vectors/kernels
    python -X utf8 crates/coach-analysis/tools/emit_kernel_vectors.py --unit laps \
        --corpus "$COACH_PRIVATE_CORPUS" --out target/kernel-vectors
    ... --check      regenerate into a temporary directory and compare with --out
    ... --selftest   decode every recorded value and compare it with the live object

Private recordings and their vectors never enter the repository: corpus
output goes under ``target/`` (ignored).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import filecmp
import gc
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

TOOLS = Path(__file__).resolve().parent
CRATE = TOOLS.parent
REPO = CRATE.parents[1]
SCRIPTS = REPO / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
FIXTURES = (REPO / "test-data" / "ibt" / "synthetic-race.ibt",)
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(SCRIPTS))

import kernel_codec as codec  # noqa: E402

PRODUCER = "emit_kernel_vectors.py"


@dataclasses.dataclass(frozen=True)
class Entry:
    """One oracle function whose calls are recorded: ``module.name``, looked up at call time."""

    module: str
    name: str
    max_calls: int | None = None

    @property
    def label(self) -> str:
        return f"{self.module}.{self.name}"


# Unit -> the oracle entry points its native port replaces. Each unit agent
# fills in its own tuple from phase2-plan.md section 2 (entry points column).
UNITS: dict[str, tuple[Entry, ...]] = {
    "table": (
        Entry("analysis_engine", "_setup_channel_series"),
        Entry("analysis_engine", "_setup_telemetry"),
        Entry("analysis_engine", "_conditions_summary"),
        Entry("analysis_engine", "_driver_adjustments_summary"),
        Entry("analysis_engine", "_caution_flag_series"),
        Entry("analysis_engine", "_dt_series"),
    ),
    "laps": (
        Entry("analysis_engine", "_lap_summaries"),
        Entry("analysis_engine", "_service_events"),
        Entry("analysis_engine", "_disrupted_sample_indices"),
    ),
    "traces": (
        Entry("analysis_engine", "_lap_trace_payload"),
        Entry("analysis_engine", "_track_profile"),
    ),
    "sections": (
        Entry("analysis_engine", "_corner_tire_age_summary"),
        Entry("analysis_engine", "_pace_attribution"),
        Entry("analysis_engine", "_clean_lap_traces"),
        Entry("pit_loss", "measure_pit_loss"),
    ),
    "field": (
        Entry("analysis_engine", "analyze_groove_evolution"),
        Entry("analysis_engine", "build_track_geometry"),
    ),
}

# Unit -> probes for vectors that are not recorded calls (for example the
# table unit's per-channel views). A probe receives the recording's
# ProbeContext after the oracle run and yields (name, document) pairs; a
# document is encoded like a call result.
PROBES: dict[str, tuple[Callable[["ProbeContext"], Iterable[tuple[str, Any]]], ...]] = {
    unit: () for unit in UNITS
}


# Unit -> synthetic tables for oracle paths no recording reaches (a text or
# NaN-bearing channel, a unit string the corpus lacks, a steering ratio
# change). Each builder receives a deep copy of the fixture's decoded table
# (the mapping workflow._load_analysis_telemetry returns) and returns the
# edited mapping; a new channel needs its entry in "variables" (name,
# type_code, count, offset, unit). Recorded with --fixtures.
SYNTHETIC: dict[str, tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...]] = {
    unit: () for unit in UNITS
}


import synthetic_laps  # noqa: E402  (unit U2's builders)

SYNTHETIC["laps"] = synthetic_laps.BUILDERS

import synthetic_traces  # noqa: E402  (unit U3's builders)

SYNTHETIC["traces"] = synthetic_traces.BUILDERS

import synthetic_field  # noqa: E402  (unit U5's builders)

SYNTHETIC["field"] = synthetic_field.BUILDERS

import synthetic_sections  # noqa: E402  (unit U4's builders)

SYNTHETIC["sections"] = synthetic_sections.BUILDERS

import table_vectors  # noqa: E402  (unit U1: view probe, synthetic tables, merge-stage table.ccs)

PROBES["table"] = table_vectors.PROBES
SYNTHETIC["table"] = table_vectors.SYNTHETIC
table_vectors.install()

import review_vectors  # noqa: E402  (regressions for the Codex phase-2 review)

SYNTHETIC["table"] = SYNTHETIC["table"] + review_vectors.BUILDERS

# Synthetic table name -> direct oracle calls made after the analysis, while the
# recorder is installed (for an argument no analysis path passes): receives the
# analysis table and the analysis result.
EXTRA_CALLS: dict[str, Callable[[Any, dict[str, Any]], None]] = dict(review_vectors.EXTRA_CALLS)


@dataclasses.dataclass
class ProbeContext:
    recording: str
    telemetry: dict[str, Any]
    table: Any
    analysis: dict[str, Any]


@dataclasses.dataclass
class Call:
    entry: Entry
    number: int
    document: dict[str, Any]
    sink: codec.ColumnSink
    live: dict[str, Any]


def _platform() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def _require_oracle() -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"the oracle is CPython 3.12; this is {sys.version.split()[0]}")
    if os.environ.get("IRACING_COACH_NATIVE_STAGES"):
        raise SystemExit("unset IRACING_COACH_NATIVE_STAGES: vectors come from the Python oracle only")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recordings(arguments: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if arguments.fixtures:
        paths.extend(FIXTURES)
    for path in arguments.recording or ():
        paths.append(Path(path))
    corpus = arguments.corpus
    if corpus:
        # Path.rglob does not return files whose full path exceeds 260
        # characters on Windows; copy such recordings to short paths.
        paths.extend(sorted(Path(corpus).rglob("*.ibt")))
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(_sha256(path), path)
    return [unique[key] for key in sorted(unique, key=lambda key: str(unique[key]))]


class Recorder:
    """Wraps entry points for one oracle run and keeps their encoded calls."""

    def __init__(
        self,
        entries: Iterable[tuple[str, Entry]],
        *,
        max_calls: int | None,
        selftest: bool,
        strings: dict[str, str],
    ) -> None:
        self.entries = list(entries)
        self.strings = strings
        self.max_calls = max_calls
        self.selftest = selftest
        self.context = codec.Context(strings=strings)
        self.calls: dict[str, list[Call]] = {}
        self.counts: dict[str, int] = {}
        self.restore: list[tuple[Any, str, Any]] = []

    def install(self, analysis_engine: Any) -> None:
        original_init = analysis_engine.TelemetryTable.__init__
        recorder = self

        def init(table: Any, *args: Any, **kwargs: Any) -> None:
            original_init(table, *args, **kwargs)
            recorder.context = codec.Context(table, recorder.strings)

        self.restore.append((analysis_engine.TelemetryTable, "__init__", original_init))
        analysis_engine.TelemetryTable.__init__ = init
        for unit, entry in self.entries:
            module = importlib.import_module(entry.module)
            original = getattr(module, entry.name)
            self.restore.append((module, entry.name, original))
            setattr(module, entry.name, self._wrap(unit, entry, original))

    def uninstall(self) -> None:
        while self.restore:
            owner, name, original = self.restore.pop()
            setattr(owner, name, original)

    @staticmethod
    def _snapshot(live: dict[str, Any], context: codec.Context) -> dict[str, Any]:
        """A deep copy that keeps the table and its channel lists by reference."""

        memo: dict[int, Any] = {}
        table = context.table
        if table is not None:
            memo[id(table)] = table
            memo[id(table.channels)] = table.channels
            memo.update((id(values), values) for values in table.channels.values())
        return copy.deepcopy(live, memo)

    @staticmethod
    def _fingerprints(args: tuple[Any, ...], kwargs: dict[str, Any], context: codec.Context) -> dict[str, str]:
        """Each argument's encoding with columns named by content: equal exactly when unchanged."""

        named = [(f"args.{index}", value) for index, value in enumerate(args)]
        named += [(f"kwargs.{key}", value) for key, value in kwargs.items()]
        return {key: codec.dumps(codec.encode(value, context, codec.DigestSink())) for key, value in named}

    def _wrap(self, unit: str, entry: Entry, original: Callable[..., Any]) -> Callable[..., Any]:
        limit = entry.max_calls if entry.max_calls is not None else self.max_calls

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            number = self.counts.get(entry.label, 0)
            self.counts[entry.label] = number + 1
            if limit is not None and number >= limit:
                return original(*args, **kwargs)
            context = self.context
            sink = codec.ColumnSink()
            before = codec.encode({"args": list(args), "kwargs": kwargs}, context, sink)
            fingerprint = self._fingerprints(args, kwargs, context)
            table = context.table
            saved = table.accessed_channels if table is not None else None
            if table is not None:
                table.accessed_channels = set()
            document: dict[str, Any] = {"codec": codec.CODEC_VERSION, "entry": entry.label, "call": number}
            document.update(before)
            live: dict[str, Any] = {"args": args, "kwargs": kwargs}
            try:
                result = original(*args, **kwargs)
            except Exception as exc:  # recorded, then re-raised unchanged
                document["raises"] = {"class": type(exc).__name__, "message": str(exc)}
                raise
            else:
                document["result"] = codec.encode(result, context, sink)
                live["result"] = result
                return result
            finally:
                if table is not None:
                    accessed = table.accessed_channels
                    saved.update(accessed)
                    table.accessed_channels = saved
                    document["accessed"] = sorted(accessed)
                after = self._fingerprints(args, kwargs, context)
                mutated = {
                    key: codec.encode(
                        args[int(key[5:])] if key.startswith("args.") else kwargs[key[7:]], context, sink
                    )
                    for key, text in fingerprint.items()
                    if after[key] != text
                }
                if mutated:
                    document["mutated"] = mutated
                    live["mutated"] = {
                        key: (args[int(key[5:])] if key.startswith("args.") else kwargs[key[7:]])
                        for key in mutated
                    }
                # Later oracle code mutates results and arguments (laps gain
                # damage_repair_context after _lap_summaries returns), so the
                # self-test compares against a snapshot taken now.
                self.calls.setdefault(unit, []).append(
                    Call(entry, number, document, sink, self._snapshot(live, context) if self.selftest else {})
                )

        wrapper.__wrapped__ = original  # type: ignore[attr-defined]
        return wrapper


def _check_roundtrip(call: Call, context: codec.Context, columns: dict[str, list[Any]]) -> list[str]:
    """Decode the stored document and compare it with the live objects of the call."""

    problems = []
    document = call.document
    if "result" in document and not codec.same(codec.decode(document["result"], context, columns), call.live["result"]):
        problems.append("result")
    for key, value in (document.get("mutated") or {}).items():
        if not codec.same(codec.decode(value, context, columns), call.live["mutated"][key]):
            problems.append(key)
    return problems


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _folder_name(sha: str, target: float | None) -> str:
    return sha[:12] if target is None else f"{sha[:12]}-{target!r}hz"


def emit_recording(path: Path, units: dict[str, tuple[Entry, ...]], arguments: argparse.Namespace, out: Path) -> dict[str, Any]:
    """Decode one recording as the worker does, then record its calls."""

    import workflow

    sha = _sha256(path)
    target = arguments.target_hz
    try:
        telemetry = workflow._load_analysis_telemetry([str(path)], target)
    except Exception as exc:  # a recording the oracle rejects has no vectors
        return {"recording": path.name, "sha256": sha, "units": {}, "rejected": f"{type(exc).__name__}: {exc}"}
    # The recording's absolute path is machine-dependent text: a stable token in every vector.
    token = f"<recording>/{path.name}"
    strings = {text: token for text in {str(path), str(path.resolve()), str(telemetry["metadata"].get("path"))}}
    return _emit(path.name, sha, telemetry, token, strings, units, arguments, out, size=path.stat().st_size)


def emit_synthetic(name: str, build: Callable[[dict[str, Any]], dict[str, Any]], units: dict[str, tuple[Entry, ...]],
                   arguments: argparse.Namespace, out: Path) -> dict[str, Any]:
    """Record the calls on a table a unit built from the fixture's decoded table."""

    import workflow

    base = workflow._load_analysis_telemetry([str(FIXTURES[0])], arguments.target_hz)
    telemetry = build(copy.deepcopy(base))
    sha = hashlib.sha256(f"synthetic:{name}".encode("utf-8")).hexdigest()
    token = f"<synthetic>/{name}"
    strings = {str(FIXTURES[0]), str(FIXTURES[0].resolve()), str(base["metadata"].get("path"))}
    return _emit(f"synthetic:{name}", sha, telemetry, token, {text: token for text in strings}, units, arguments, out,
                 size=0, extra=EXTRA_CALLS.get(name))


def _emit(
    name: str,
    sha: str,
    telemetry: dict[str, Any],
    token: str,
    strings: dict[str, str],
    units: dict[str, tuple[Entry, ...]],
    arguments: argparse.Namespace,
    out: Path,
    *,
    size: int,
    extra: Callable[[Any, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    import analysis_engine
    import ibt_reader
    import workflow

    target = arguments.target_hz
    folder = out / _platform() / _folder_name(sha, target)
    summary: dict[str, Any] = {"recording": name, "sha256": sha, "target_hz": target, "units": {}}
    recorder = Recorder(
        ((unit, entry) for unit, entries in units.items() for entry in entries),
        max_calls=arguments.max_calls,
        selftest=arguments.selftest,
        strings=strings,
    )
    fingerprints = [{"path": token, "sha256": sha}]
    profile = {"target_hz": target, "pipeline_sha256": workflow._analysis_pipeline_sha256()}
    recorder.install(analysis_engine)
    gc.disable()  # the worker pauses automatic collection (workflow._automatic_gc_paused)
    try:
        analysis = analysis_engine.analyze_telemetry(
            telemetry, source_paths=[token], source_fingerprints=fingerprints, analysis_profile=profile
        )
        if extra is not None:
            extra(recorder.context.table, analysis)
    finally:
        gc.enable()
        recorder.uninstall()
    context = recorder.context
    table = context.table
    meta = {
        "codec": codec.CODEC_VERSION,
        "recording": name,
        "sha256": sha,
        "size": size,
        "python": sys.version.split()[0],
        "platform": _platform(),
        "target_hz": target,
        "length": table.length,
        "channel_names": list(table.channels),
        "metadata": codec.encode(table.metadata, codec.Context(strings=strings), None),
        "session_info": codec.encode(table.session_info, codec.Context(strings=strings), None),
        "variables": codec.encode(table.variables, codec.Context(strings=strings), None),
        "available_variables": codec.encode(table.available_variables, codec.Context(strings=strings), None),
        "channel_units": table.channel_units,
        "analysis_id": analysis.get("analysis_id"),
        # What the kernels session receives in its start request (phase2-plan.md section 4.2):
        # the basis the analysis cached on the table (computed now if it never asked).
        "steering_basis": codec.encode(
            analysis_engine._steering_basis(table), codec.Context(strings=strings), None
        ),
    }
    codec.write_decode_store(
        folder / "table.ccs", telemetry=telemetry, sampling_plan=ibt_reader._sampling_plan,
        target_hz=target, producer=PRODUCER,
    )
    _write_text(folder / "table.json", codec.dumps(meta))
    probe_context = ProbeContext(name, telemetry, table, analysis)
    for unit in units:
        unit_dir = folder / unit
        files: dict[str, str] = {}
        failures: list[str] = []
        for call in recorder.calls.get(unit, ()):
            stem = unit_dir / call.entry.label / f"{call.number:05d}"
            _write_text(stem.with_suffix(".json"), codec.dumps(call.document))
            written = call.sink.write(stem.with_suffix(".ccs"), PRODUCER)
            if arguments.selftest:
                columns = codec.read_vector_store(stem.with_suffix(".ccs")) if written else {}
                failures.extend(f"{call.entry.label}#{call.number}: {item}" for item in _check_roundtrip(call, context, columns))
        for probe in PROBES.get(unit, ()):
            for probe_name, document in probe(probe_context):
                sink = codec.ColumnSink()
                encoded = {"codec": codec.CODEC_VERSION, "probe": probe_name, "result": codec.encode(document, context, sink)}
                stem = unit_dir / "probes" / probe_name
                _write_text(stem.with_suffix(".json"), codec.dumps(encoded))
                sink.write(stem.with_suffix(".ccs"), PRODUCER)
        if unit_dir.exists():
            for file in sorted(unit_dir.rglob("*")):
                if file.is_file() and file.name != "index.json":
                    files[file.relative_to(unit_dir).as_posix()] = _sha256(file)
        counts = {label: sum(1 for call in recorder.calls.get(unit, ()) if call.entry.label == label)
                  for label in sorted({entry.label for entry in units[unit]})}
        index = {"recording": name, "sha256": sha, "python": meta["python"], "platform": meta["platform"],
                 "target_hz": target, "calls": counts, "files": files}
        if files or counts:
            _write_text(unit_dir / "index.json", json.dumps(index, indent=1, sort_keys=True))
        summary["units"][unit] = {"calls": counts, "files": len(files), "selftest_failures": failures}
    return summary


def _compare_trees(expected: Path, actual: Path, units: Iterable[str]) -> list[str]:
    """Differences between ``expected`` and the regenerated ``actual``, limited to what was regenerated."""

    def listing(root: Path, folder: Path) -> set[str]:
        return {p.relative_to(root).as_posix() for p in folder.rglob("*") if p.is_file()} if folder.is_dir() else set()

    differences = []
    actual_files: set[str] = set()
    expected_files: set[str] = set()
    for recording in sorted(p for p in actual.glob("*/*") if p.is_dir()):
        relative = recording.relative_to(actual)
        for name in ("table.ccs", "table.json"):
            actual_files.add((relative / name).as_posix())
            if (expected / relative / name).is_file():
                expected_files.add((relative / name).as_posix())
        for unit in units:
            actual_files |= listing(actual, recording / unit)
            expected_files |= listing(expected, expected / relative / unit)
    for name in sorted(expected_files | actual_files):
        if name not in expected_files:
            differences.append(f"new: {name}")
        elif name not in actual_files:
            differences.append(f"missing: {name}")
        elif not filecmp.cmp(expected / name, actual / name, shallow=False):
            differences.append(f"differs: {name}")
    return differences


def _units(arguments: argparse.Namespace) -> dict[str, tuple[Entry, ...]]:
    if arguments.entry:
        adhoc = []
        for spec in arguments.entry:
            module, _, name = spec.partition(":")
            if not module or not name:
                raise SystemExit(f"--entry takes module:name, not {spec!r}")
            adhoc.append(Entry(module, name))
        return {"adhoc": tuple(adhoc)}
    names = arguments.unit or [unit for unit, entries in UNITS.items() if entries or PROBES.get(unit) or SYNTHETIC.get(unit)]
    unknown = sorted(set(names) - set(UNITS))
    if unknown:
        raise SystemExit(f"unknown unit(s) {', '.join(unknown)}; units: {', '.join(UNITS)}")
    selected = {unit: UNITS[unit] for unit in names}
    if not any(selected.values()) and not any(PROBES.get(unit) or SYNTHETIC.get(unit) for unit in selected):
        raise SystemExit("no entry points registered for the selected unit(s); add them to UNITS")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unit", action="append", help="unit to record (repeatable; default: every unit with entries)")
    parser.add_argument("--entry", action="append", help="record module:name ad hoc instead of the units")
    parser.add_argument("--fixtures", action="store_true", help="include test-data/ibt/synthetic-race.ibt and the SYNTHETIC tables")
    parser.add_argument("--corpus", default=None, help="folder of private recordings (searched recursively)")
    parser.add_argument("--recording", action="append", help="one recording (repeatable)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target-hz", type=float, default=None, help="decode rate; default native (the worker's)")
    parser.add_argument("--max-calls", type=int, default=None, help="record at most this many calls per entry")
    parser.add_argument("--check", action="store_true", help="regenerate into a temporary folder and compare with --out")
    parser.add_argument("--selftest", action="store_true", help="decode each stored value and compare it with the live object")
    arguments = parser.parse_args(argv)
    _require_oracle()
    units = _units(arguments)
    recordings = _recordings(arguments)
    if not recordings:
        raise SystemExit("no recordings: pass --fixtures, --recording or --corpus")
    out = arguments.out
    work = Path(tempfile.mkdtemp(prefix="kernel-vectors-")) if arguments.check else out
    failed = False
    try:
        jobs: list[Callable[[], dict[str, Any]]] = [
            (lambda path=path: emit_recording(path, units, arguments, work)) for path in recordings
        ]
        if arguments.fixtures:
            for unit in units:
                for name, build in SYNTHETIC.get(unit, ()):
                    jobs.append(lambda name=name, build=build: emit_synthetic(name, build, units, arguments, work))
        for job in jobs:
            try:
                summary = job()
            except Exception:  # one recording's failure is reported, the rest still run
                traceback.print_exc()
                summary = {"error": True}
                failed = True
            for unit in summary.get("units", {}).values():
                failed = failed or bool(unit["selftest_failures"])
            print(json.dumps(summary, sort_keys=True))
        if arguments.check:
            differences = _compare_trees(out, work, units)
            for line in differences:
                print(line)
            print(f"check: {'identical' if not differences else f'{len(differences)} difference(s)'}")
            failed = failed or bool(differences)
    finally:
        if arguments.check:
            shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
