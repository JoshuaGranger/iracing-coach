#!/usr/bin/env python3
"""Capture and compare complete cold-analysis outputs and stage timings.

A change to the deterministic backend, or a Rust stage that replaces a Python
stage, must not change a single emitted value. This tool makes that claim
checkable (``docs/08-rewrite/contracts/worker-interface.md`` section 8):

``capture``
    Runs the production ``analyze_race_workflow`` on one ``.ibt`` recording,
    cold, in a fresh process and a fresh harness-owned archive. One untimed
    run writes the outputs: the engine's in-memory result in its own key
    order, the cached analysis, the workflow result, and every artifact the
    workflow persisted, both parsed (``outputs/``) and as persisted bytes
    (``raw/``). ``--runs`` further cold runs record workflow and per-stage
    timings and their medians.

``capture-worker``
    Drives ``mcp_server.py`` exactly as coach-server does: one process per
    job, the section 2 environment and nothing inherited, section 3 job roots
    made of hard links to a spool, discovery in a one-recording job root, then
    ``analyze_iracing_race`` over the group's recordings selected by the
    discovered group id (or by path for a ``local-file`` group). Cases come
    from ``worker_corpus.py`` (``synthetic``, ``reconnect``, ``local-file``,
    and ``restaged``: the synthetic race copied, not linked, into the job
    root with its original mtime) or from ``--source`` recordings, which form
    one group. The analyzer digest is never pinned here (production identity).

``compare``
    Byte-compares two capture directories and prints the differing paths.
    Exit status 0 means every compared file is byte-identical. Parsed mode
    (default) compares ``outputs/``; ``--raw`` compares ``raw/``, the gate
    for the JSON writer. ``--cross-platform`` also folds CRLF to LF and the
    path separators that follow a root token, the only differences allowed
    between the Windows oracle and the Linux container.

Parsed-mode normalisation, recorded in each capture's ``capture.json``:

* wall-clock timestamps (``analyzed_at``/``created_at``/... keys, any
  ``*_at`` key holding an ISO-8601 instant, ISO-8601 instants inside persisted
  text, and the timestamp slug that names report directories) become
  ``<timestamp>``; the analysis-cache envelope's hash of the timestamped
  analysis is replaced too (the analysis itself is compared in full);
* SQLite archives are compared as normalised row dumps, not raw bytes;
* elapsed-millisecond values (``*_ms`` keys, ``elapsed_seconds``) become
  ``<elapsed-ms>``;
* the archive root and the iRacing (job) root become ``<archive_root>`` and
  ``<iracing_root>``, and a string that starts with one uses ``/`` after it;
* ``capture`` pins the analyzer *source* digest to a constant unless
  ``--unpinned-identity``, so analysis and run ids do not rotate merely
  because a source file was edited. That digest is an input to ids only.

Raw mode keeps the persisted bytes and substitutes only this allowlist: ISO
instants in ``*_at`` values, the report-directory timestamp slug (in contents
and file names), ``*_ms`` / ``elapsed_seconds`` values, the
timestamp-derived ``analysis_sha256``, and the two root prefixes. SQLite files
appear in raw mode only as ``<name>: sqlite`` in the index (their rows are
compared in parsed mode); other binaries by SHA-256.

Every capture applies ``IRACING_COACH_RAW_ARCHIVE=external`` (the production
setting) unless ``--raw-archive copy`` is given, and passes
``--native-stages`` to the worker as ``IRACING_COACH_NATIVE_STAGES``; a
non-empty list requires the ``coach_native`` module.

Examples::

    python tools/parity/analysis_parity.py capture --source RACE.ibt --out before/nh --runs 3
    python tools/parity/analysis_parity.py compare before/nh after/nh
    python tools/parity/analysis_parity.py compare --raw before/nh after/nh
    python tools/parity/analysis_parity.py capture-worker --case reconnect --out win/reconnect
    python tools/parity/analysis_parity.py compare --raw --cross-platform win/reconnect linux/reconnect
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
ARCHIVE_MARKER = ".analysis-parity-archive"
BASE_MARKER = ".analysis-parity-worker-base"
PINNED_ANALYZER_SHA256 = "0" * 64
NATIVE_STAGES = ("decode", "kernels", "json_writer")
CAPTURE_FORMAT = 2

TIMESTAMP_KEYS = frozenset(
    {
        "analyzed_at",
        "archived_at",
        "created_at",
        "generated_at",
        "imported_at",
        "recorded_at",
        "saved_at",
        "updated_at",
        "written_at",
        "cached_at",
        "last_updated",
        "observed_at",
    }
)
# Hashes computed over a payload that itself embeds a wall-clock timestamp
# (the analysis cache envelope hashes the analysis including analyzed_at).
TIMESTAMP_DERIVED_KEYS = frozenset({"analysis_sha256"})
# Report directories are named from the analysis timestamp, e.g.
# ``2026-09-25t02-09-54-00-00``; the slug appears in paths and file names.
TIMESTAMP_SLUG = re.compile(r"\d{4}-\d{2}-\d{2}t\d{2}-\d{2}-\d{2}-\d{2}-\d{2}")
ELAPSED_KEY = re.compile(r"(^|_)(ms|elapsed_seconds|elapsed_ms)$")
ISO_PATTERN = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
ISO_INSTANT = re.compile(ISO_PATTERN)
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".svg", ".html", ".jsonl"}
SQLITE_SUFFIXES = {".sqlite3", ".sqlite", ".db"}

# Raw-mode allowlist (worker-interface.md section 8). Each rule applies to the
# persisted text exactly as written, so the writer's own bytes stay visible.
RAW_AT_VALUE = re.compile(r'("[^"\\]*_at"\s*:\s*")' + ISO_PATTERN + r'(")')
RAW_ELAPSED_VALUE = re.compile(
    r'("(?:[^"\\]*_)?(?:ms|elapsed_seconds)"\s*:\s*)-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'
)
RAW_ANALYSIS_SHA256 = re.compile(r'("analysis_sha256"\s*:\s*")[0-9a-f]{64}(")')
RAW_RULES = {
    "at_values": "ISO-8601 instants in *_at values",
    "timestamp_slug": TIMESTAMP_SLUG.pattern,
    "elapsed_values": "*_ms and elapsed_seconds values",
    "analysis_sha256": "the analysis-cache envelope hash of the timestamped analysis",
    "root_prefixes": "<archive_root>, <iracing_root>",
}
ROOT_TOKENS = ("<archive_root>", "<iracing_root>")

# Stage timers: module attribute -> label. Wrapping a module global is enough
# because analyze_telemetry and the workflow look these names up at call time.
ENGINE_STAGES = (
    "_identity",
    "_lap_summaries",
    "_runs",
    "_damage_repair_summary",
    "_setup_channel_series",
    "_setup_telemetry",
    "_track_profile",
    "_lap_trace_payload",
    "_additional_trace_sources",
    "analyze_groove_evolution",
    "_corner_tire_age_summary",
    "build_track_geometry",
    "build_race_replay",
    "build_tire_learning",
    "_conditions_summary",
    "_driver_adjustments_summary",
    "_coaching_signals",
    "_pace_attribution",
    "_strategy",
    "_field_pace",
    "_strategy_planning",
    "_race_grades",
    "_race_evidence_timeline",
    "build_race_investigation",
    "_analysis_fingerprint",
)
WORKFLOW_STAGES = (
    "_load_analysis_telemetry",
    "analyze_telemetry",
)


# --------------------------------------------------------------------------
# Roots
# --------------------------------------------------------------------------


class Roots:
    """The root prefixes a capture replaces by tokens, longest first."""

    def __init__(self, pairs: Iterable[tuple[str, Path]]) -> None:
        self.pairs = sorted(
            ((token, str(path)) for token, path in pairs), key=lambda item: len(item[1]), reverse=True
        )

    def as_dict(self) -> dict[str, str]:
        return {token: prefix for token, prefix in self.pairs}

    def in_string(self, value: str, counts: dict[str, int]) -> str:
        for token, prefix in self.pairs:
            if value == prefix or value.startswith((prefix + "\\", prefix + "/")):
                counts["root_prefixes"] = counts.get("root_prefixes", 0) + 1
                return token + value[len(prefix):].replace("\\", "/")
        return value

    def in_text(self, text: str, counts: dict[str, int], *, json_escaped: bool) -> str:
        for token, prefix in self.pairs:
            forms = [prefix]
            if json_escaped and "\\" in prefix:
                forms.insert(0, prefix.replace("\\", "\\\\"))
            for form in forms:
                found = text.count(form)
                if found:
                    counts["root_prefixes"] = counts.get("root_prefixes", 0) + found
                    text = text.replace(form, token)
        return text


# --------------------------------------------------------------------------
# Normalisation and comparison
# --------------------------------------------------------------------------


def normalise(value: Any, counts: dict[str, int], key: str | None = None, roots: Roots | None = None) -> Any:
    if (
        key is not None
        and isinstance(value, str)
        and (key in TIMESTAMP_KEYS or (key.endswith("_at") and ISO_INSTANT.fullmatch(value)))
    ):
        counts["timestamp_keys"] = counts.get("timestamp_keys", 0) + 1
        return "<timestamp>"
    if key is not None and key in TIMESTAMP_DERIVED_KEYS and isinstance(value, str):
        counts["timestamp_derived_hashes"] = counts.get("timestamp_derived_hashes", 0) + 1
        return "<hash-of-timestamped-payload>"
    if (
        key is not None
        and ELAPSED_KEY.search(key)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        counts["elapsed_keys"] = counts.get("elapsed_keys", 0) + 1
        return "<elapsed-ms>"
    if isinstance(value, dict):
        return {k: normalise(v, counts, str(k), roots) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(item, counts, None, roots) for item in value]
    if isinstance(value, str):
        if roots is not None:
            value = roots.in_string(value, counts)
        if TIMESTAMP_SLUG.search(value):
            counts["timestamp_slugs"] = counts.get("timestamp_slugs", 0) + 1
            return TIMESTAMP_SLUG.sub("<timestamp>", value)
    return value


def normalise_text(text: str, counts: dict[str, int], roots: Roots | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        counts["timestamp_text"] = counts.get("timestamp_text", 0) + 1
        return "<timestamp>"

    if roots is not None:
        text = roots.in_text(text, counts, json_escaped=False)
    return TIMESTAMP_SLUG.sub(replace, ISO_INSTANT.sub(replace, text))


def raw_substitute(text: str, counts: dict[str, int], roots: Roots, *, json_like: bool) -> str:
    """Apply the raw-mode allowlist and nothing else."""

    def counted(rule: str, replacement: str) -> Callable[[re.Match[str]], str]:
        def apply(match: re.Match[str]) -> str:
            counts[rule] = counts.get(rule, 0) + 1
            return match.expand(replacement)

        return apply

    text = roots.in_text(text, counts, json_escaped=json_like)
    text = RAW_AT_VALUE.sub(counted("at_values", r"\1<timestamp>\2"), text)
    text = RAW_ELAPSED_VALUE.sub(counted("elapsed_values", r'\1"<elapsed-ms>"'), text)
    text = RAW_ANALYSIS_SHA256.sub(counted("analysis_sha256", r"\1<hash-of-timestamped-payload>\2"), text)
    return TIMESTAMP_SLUG.sub(counted("timestamp_slug", "<timestamp>"), text)


def canonical_json(value: Any, *, sort_keys: bool = False) -> str:
    """Stable text for byte comparison.

    Key order is preserved by default: the engine's own insertion order and the
    order the application wrote to disk are part of what must not change.
    """

    return json.dumps(value, indent=1, sort_keys=sort_keys, ensure_ascii=False, allow_nan=True) + "\n"


def json_differences(left: Any, right: Any, path: str = "$", limit: int = 40) -> list[str]:
    found: list[str] = []

    def walk(a: Any, b: Any, where: str) -> None:
        if len(found) >= limit:
            return
        if type(a) is not type(b):
            found.append(f"{where}: type {type(a).__name__} != {type(b).__name__}: {str(a)[:80]!r} / {str(b)[:80]!r}")
        elif isinstance(a, dict):
            if set(a) == set(b) and list(a) != list(b):
                found.append(f"{where}: key order differs")
            for key in sorted(set(a) | set(b), key=str):
                if key not in a or key not in b:
                    found.append(f"{where}.{key}: present only on the {'right' if key not in a else 'left'}")
                else:
                    walk(a[key], b[key], f"{where}.{key}")
        elif isinstance(a, list):
            if len(a) != len(b):
                found.append(f"{where}: length {len(a)} != {len(b)}")
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{where}[{index}]")
        elif a != b or repr(a) != repr(b):
            found.append(f"{where}: {a!r} != {b!r}")

    walk(left, right, path)
    return found


def text_differences(left: str, right: str, limit: int = 6) -> list[str]:
    found: list[str] = []
    left_lines, right_lines = left.splitlines(), right.splitlines()
    if len(left_lines) != len(right_lines):
        found.append(f"line count {len(left_lines)} != {len(right_lines)}")
    for number, (a, b) in enumerate(zip(left_lines, right_lines), start=1):
        if a != b:
            found.append(f"line {number}: {a[:120]!r} != {b[:120]!r}")
            if len(found) >= limit:
                break
    return found


# --------------------------------------------------------------------------
# Archive capture (shared by capture and capture-worker)
# --------------------------------------------------------------------------


def _sqlite_dump(path: Path) -> dict[str, Any]:
    """Every table's schema and rows, in a stable order, opened read-only."""

    import sqlite3  # noqa: PLC0415

    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dump: dict[str, Any] = {}
        tables = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for name, sql in tables:
            cursor = connection.execute(f'SELECT * FROM "{name}"')
            columns = [item[0] for item in cursor.description]
            rows = [
                {
                    column: (
                        f"blob-sha256:{hashlib.sha256(value).hexdigest()}"
                        if isinstance(value, (bytes, bytearray, memoryview))
                        else value
                    )
                    for column, value in zip(columns, row)
                }
                for row in cursor.fetchall()
            ]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
            dump[name] = {"schema": sql, "rows": rows}
        return dump
    finally:
        connection.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_archive_capture(
    archive: Path,
    out: Path,
    roots: Roots,
    *,
    workflow_result: Any,
    engine_output: str | None,
) -> None:
    """Write ``out/outputs`` (parsed) and ``out/raw`` (persisted bytes) for one archive."""

    outputs, raw = out / "outputs", out / "raw"
    for directory in (outputs, raw):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    files: dict[str, str] = {}
    raw_files: dict[str, str] = {}

    cache_files = sorted((archive / "analysis-cache").glob("*.json"))
    if len(cache_files) != 1:
        raise SystemExit(f"Expected one analysis-cache entry, found {len(cache_files)}.")
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    analysis = normalise(cached["analysis"], counts, roots=roots)
    (outputs / "analysis.json").write_text(canonical_json(analysis, sort_keys=True), encoding="utf-8", newline="\n")
    if engine_output is not None:
        (outputs / "engine-output.insertion-order.json").write_text(engine_output, encoding="utf-8", newline="\n")
    (outputs / "workflow-result.json").write_text(
        canonical_json(normalise(workflow_result, counts, roots=roots)), encoding="utf-8", newline="\n"
    )

    for path in sorted(archive.rglob("*")):
        if not path.is_file() or path.name == ARCHIVE_MARKER:
            continue
        relative = TIMESTAMP_SLUG.sub("_timestamp_", path.relative_to(archive).as_posix())
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            data = path.read_bytes()
            text = data.decode("utf-8")
            raw_target = raw / relative
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            raw_target.write_bytes(
                raw_substitute(text, raw_counts, roots, json_like=suffix in {".json", ".jsonl"}).encode("utf-8")
            )
            raw_files[relative] = "substituted-bytes"
            if suffix == ".json":
                try:
                    text = canonical_json(normalise(json.loads(text), counts, roots=roots))
                except ValueError:
                    text = normalise_text(text, counts, roots)
            elif suffix == ".jsonl":
                lines = [
                    json.dumps(normalise(json.loads(line), counts, roots=roots), sort_keys=True, ensure_ascii=False)
                    for line in text.splitlines()
                    if line.strip()
                ]
                text = "\n".join(lines) + "\n"
            else:
                text = normalise_text(text.replace("\r\n", "\n"), counts, roots)
            target = outputs / "archive" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            files[relative] = "normalised-text"
        elif suffix in SQLITE_SUFFIXES:
            target = outputs / "archive" / f"{relative}.dump.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                canonical_json(normalise(_sqlite_dump(path), counts, roots=roots), sort_keys=True),
                encoding="utf-8",
                newline="\n",
            )
            files[relative] = "normalised-sqlite-dump"
            raw_files[relative] = "sqlite"
        else:
            files[relative] = raw_files[relative] = f"sha256:{_sha256_file(path)}"
    (outputs / "binary-and-file-index.json").write_text(canonical_json(files, sort_keys=True), encoding="utf-8", newline="\n")
    (outputs / "normalisation.json").write_text(canonical_json(counts, sort_keys=True), encoding="utf-8", newline="\n")
    (raw / "index.json").write_text(canonical_json(raw_files, sort_keys=True), encoding="utf-8", newline="\n")
    (raw / "substitutions.json").write_text(canonical_json(raw_counts, sort_keys=True), encoding="utf-8", newline="\n")


def _capture_metadata(out: Path, kind: str, **fields: Any) -> None:
    metadata = {
        "capture_format": CAPTURE_FORMAT,
        "kind": kind,
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": _python(),
        "normalisation": {
            "timestamp_keys": sorted(TIMESTAMP_KEYS),
            "elapsed_key_pattern": ELAPSED_KEY.pattern,
            "iso_instant_pattern": ISO_PATTERN,
            "timestamp_slug_pattern": TIMESTAMP_SLUG.pattern,
            "timestamp_derived_keys": sorted(TIMESTAMP_DERIVED_KEYS),
            "raw_allowlist": RAW_RULES,
        },
        **fields,
    }
    (out / "capture.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# One cold in-process run (executed in its own process)
# --------------------------------------------------------------------------


def _instrument(module: Any, name: str, sink: dict[str, float], label: str) -> None:
    original = getattr(module, name, None)
    if original is None or getattr(original, "__analysis_parity_timed__", False):
        return

    @functools.wraps(original)
    def timed(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            sink[label] = sink.get(label, 0.0) + (time.perf_counter() - started) * 1000.0

    timed.__analysis_parity_timed__ = True  # type: ignore[attr-defined]
    setattr(module, name, timed)


def _prepare_owned(directory: Path, marker: str) -> None:
    if directory.exists():
        if not (directory / marker).is_file() and any(directory.iterdir()):
            raise SystemExit(
                f"Refusing to wipe {directory}: it is not a harness-owned directory (missing {marker})."
            )
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / marker).write_text("analysis_parity harness scratch\n", encoding="utf-8")


def _require_native(stages: str) -> None:
    names = [item.strip() for item in stages.split(",") if item.strip()]
    unknown = sorted(set(names) - set(NATIVE_STAGES))
    if unknown:
        raise SystemExit(f"Unknown native stage(s) {', '.join(unknown)}; expected {', '.join(NATIVE_STAGES)}.")
    if names and importlib.util.find_spec("coach_native") is None:
        raise SystemExit(
            "IRACING_COACH_NATIVE_STAGES names native stages but coach_native is not installed; "
            "a capture without them would compare Python with Python."
        )


def run_once(
    source: Path,
    archive: Path,
    iracing_root: Path,
    out: Path | None,
    timing_path: Path,
    *,
    pinned: bool,
) -> None:
    _require_native(os.environ.get("IRACING_COACH_NATIVE_STAGES", ""))
    sys.path.insert(0, str(SCRIPTS))
    import analysis_engine  # noqa: PLC0415
    import workflow  # noqa: PLC0415

    if pinned:  # ids only: see the module docstring
        analysis_engine.analyzer_bundle_sha256 = lambda: PINNED_ANALYZER_SHA256  # type: ignore[assignment]
    stages: dict[str, float] = {}
    for name in ENGINE_STAGES:
        _instrument(analysis_engine, name, stages, name)
    for name in WORKFLOW_STAGES:
        _instrument(workflow, name, stages, f"workflow.{name}")

    engine_output: list[str] = []
    roots = Roots((("<archive_root>", archive.resolve()), ("<iracing_root>", iracing_root.resolve())))
    if out is not None:
        # Output runs are never timed. Serialise the engine's in-memory result
        # at once, before the workflow copies or decorates it, so the capture
        # is exactly what analyze_telemetry returned, in insertion order.
        produce = workflow.analyze_telemetry

        def capture_engine_output(*args: Any, **kwargs: Any) -> Any:
            produced = produce(*args, **kwargs)
            engine_output.append(canonical_json(normalise(produced, {}, roots=roots)))
            return produced

        workflow.analyze_telemetry = capture_engine_output  # type: ignore[assignment]

    _prepare_owned(archive, ARCHIVE_MARKER)
    started = time.perf_counter()
    result = workflow.analyze_race_workflow(
        selector=str(source),
        iracing_root=str(iracing_root),
        archive_root=str(archive),
        target_hz=None,
    )
    wall_ms = (time.perf_counter() - started) * 1000.0
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path.write_text(
        json.dumps(
            {
                "wall_ms": round(wall_ms, 1),
                "workflow_timing": result.get("timing"),
                "stages_ms": {key: round(value, 1) for key, value in stages.items()},
                "analysis_cache_hit": (result.get("analysis_cache") or {}).get("hit"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if out is None:
        return
    if len(engine_output) != 1:
        raise SystemExit(f"Expected one engine output, captured {len(engine_output)}.")
    write_archive_capture(archive, out, roots, workflow_result=result, engine_output=engine_output[0])


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _python() -> str:
    return os.environ.get("IRACING_COACH_PYTHON") or sys.executable


def _scratch(name: str) -> Path:
    return Path(tempfile.gettempdir()) / "iracing-coach-analysis-parity" / name


def _worker_environment(stages: str, raw_archive: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["IRACING_COACH_NATIVE_STAGES"] = stages
    if raw_archive == "external":
        environment["IRACING_COACH_RAW_ARCHIVE"] = "external"
    else:
        environment.pop("IRACING_COACH_RAW_ARCHIVE", None)
    return environment


def command_capture(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise SystemExit(f"Recording not found: {source}")
    iracing_root = Path(args.iracing_root).resolve() if args.iracing_root else (
        source.parent.parent if source.parent.name.lower() == "telemetry" else source.parent
    )
    out = Path(args.out).resolve()
    archive = Path(args.archive).resolve() if args.archive else _scratch("archive")
    out.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    environment = _worker_environment(args.native_stages, args.raw_archive)

    def cold_run(label: str, timing_path: Path, outputs: Path | None) -> None:
        command = [
            _python(), "-X", "utf8",
            str(Path(__file__).resolve()),
            "_run-once",
            "--source", str(source),
            "--archive", str(archive),
            "--iracing-root", str(iracing_root),
            "--timing", str(timing_path),
        ]
        if args.unpinned_identity:
            command.append("--unpinned-identity")
        if outputs is not None:
            command += ["--out", str(outputs)]
        completed = subprocess.run(command, check=False, env=environment)
        if completed.returncode != 0:
            raise SystemExit(f"{label} failed with exit code {completed.returncode}.")

    if not args.timing_only:
        cold_run("Output run", out / "timings" / "output-run.json", out)
        print("outputs captured (untimed run)", flush=True)
    for index in range(args.runs):
        timing_path = out / "timings" / f"run-{index + 1}.json"
        cold_run(f"Run {index + 1}", timing_path, None)
        run = json.loads(timing_path.read_text(encoding="utf-8"))
        runs.append(run)
        print(
            f"run {index + 1}/{args.runs}: wall {run['wall_ms'] / 1000:.2f} s, "
            f"decode+analysis {(run['workflow_timing'] or {}).get('decode_analysis_ms', 0) / 1000:.2f} s",
            flush=True,
        )
    if archive.exists() and (archive / ARCHIVE_MARKER).is_file():
        shutil.rmtree(archive)

    def median_of(extract: Callable[[dict[str, Any]], Any]) -> float | None:
        values = [value for run in runs if isinstance(value := extract(run), (int, float))]
        return round(statistics.median(values), 1) if values else None

    stage_names = sorted({name for run in runs for name in run["stages_ms"]})
    summary = {
        "source": str(source),
        "source_size_bytes": source.stat().st_size,
        "runs": len(runs),
        "python": _python(),
        "median_ms": {
            "wall": median_of(lambda run: run["wall_ms"]),
            **{
                f"workflow.{key}": median_of(lambda run, key=key: (run["workflow_timing"] or {}).get(key))
                for key in ("selection_verification_ms", "decode_analysis_ms", "report_persist_ms", "total_ms")
            },
            **{name: median_of(lambda run, name=name: run["stages_ms"].get(name)) for name in stage_names},
        },
        "per_run": runs,
    }
    (out / "timings.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _capture_metadata(
        out,
        "in-process",
        source=source.name,
        source_sha256=_sha256_file(source),
        analyzer_digest="unpinned" if args.unpinned_identity else PINNED_ANALYZER_SHA256,
        native_stages=args.native_stages,
        raw_archive=args.raw_archive,
        roots={"<archive_root>": str(archive), "<iracing_root>": str(iracing_root)},
    )
    if runs:
        print(json.dumps(summary["median_ms"], indent=2))
    return 0


def command_run_once(args: argparse.Namespace) -> int:
    run_once(
        Path(args.source),
        Path(args.archive),
        Path(args.iracing_root),
        Path(args.out) if args.out else None,
        Path(args.timing),
        pinned=not args.unpinned_identity,
    )
    return 0


class WorkerJob:
    """One worker process for one job, driven as coach-server drives it."""

    def __init__(self, base: Path, stages: str, raw_archive: str) -> None:
        self.base = base
        self.archive = base / "py" / "1" / "archive"
        self.tmp = base / "py" / "1" / "tmp"
        self.stages = stages
        self.raw_archive = raw_archive

    def environment(self, job_root: Path) -> dict[str, str]:
        environment = {
            "IRACING_COACH_IRACING_ROOT": str(job_root),
            "IRACING_COACH_DATA": str(self.archive),
            "IRACING_COACH_NATIVE_STAGES": self.stages,
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmp),
            "PATH": "/usr/local/bin:/usr/bin:/bin" if os.name != "nt" else os.environ.get("PATH", ""),
        }
        if self.raw_archive == "external":
            environment["IRACING_COACH_RAW_ARCHIVE"] = "external"
        if os.name == "nt":  # Windows needs these to start an interpreter and to name a home
            environment.update({key: os.environ[key] for key in ("SYSTEMROOT", "TEMP", "TMP") if key in os.environ})
            environment["USERPROFILE"] = str(self.tmp)
        return environment

    def call(self, job_root: Path, tool: str, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "coach-server", "version": "analysis-parity"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
        ]
        completed = subprocess.run(
            [_python(), "-X", "utf8", "-u", str(SCRIPTS / "mcp_server.py")],
            input="".join(json.dumps(item) + "\n" for item in requests).encode("utf-8"),
            capture_output=True,
            env=self.environment(job_root),
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"{tool}: worker_crash (exit {completed.returncode})\n"
                + completed.stderr.decode("utf-8", "replace")[-4000:]
            )
        for line in completed.stdout.decode("utf-8").splitlines():
            if not line.strip():
                continue
            response = json.loads(line)
            if response.get("id") != 2:
                continue
            if "error" in response:
                raise SystemExit(f"{tool}: protocol error {response['error']}")
            result = response["result"]
            payload = json.loads(result["content"][0]["text"])
            if result.get("isError"):
                raise SystemExit(f"{tool}: tool_error {payload}")
            return payload
        raise SystemExit(f"{tool}: worker_crash (no response with id 2)")


def _stage(spool: Path, recording: Path) -> Path:
    """Commit a recording to the spool by SHA-256, keeping its mtime, as ingest does."""

    sha = _sha256_file(recording)
    target = spool / sha
    if not target.exists():
        shutil.copyfile(recording, target)
        status = recording.stat()
        os.utime(target, ns=(status.st_mtime_ns, status.st_mtime_ns))
    return target


def _job_root(base: Path, job: str, members: list[tuple[str, Path]], *, restage: bool) -> Path:
    root = base / "py" / "1" / "jobs" / job
    telemetry = root / "telemetry"
    telemetry.mkdir(parents=True)
    for name, spooled in members:
        target = telemetry / name
        if restage:  # restaged from the NAS: a copy with the original mtime, not a link
            shutil.copyfile(spooled, target)
            status = spooled.stat()
            os.utime(target, ns=(status.st_mtime_ns, status.st_mtime_ns))
        else:
            os.link(spooled, target)
    return root


def _server_group(group: dict[str, Any], sha256: str) -> dict[str, Any]:
    """A group as the server keys it: a path-keyed group becomes ``local-file:<sha256>``."""

    if group.get("time_source") != "mtime":
        return group
    return {**group, "group_id": f"local-file:{sha256}"}


def command_capture_worker(args: argparse.Namespace) -> int:
    _require_native(args.native_stages)
    out = Path(args.out).resolve()
    base = Path(args.base).resolve() if args.base else _scratch("worker")
    _prepare_owned(base, BASE_MARKER)
    out.mkdir(parents=True, exist_ok=True)
    spool = base / "spool" / "ready"
    spool.mkdir(parents=True)
    job = WorkerJob(base, args.native_stages, args.raw_archive)
    job.archive.mkdir(parents=True)
    job.tmp.mkdir(parents=True)

    if args.source:
        recordings = [Path(item).resolve() for item in args.source]
        case = "sources"
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import worker_corpus  # noqa: PLC0415

        case = args.case
        recordings = worker_corpus.build_case(
            "synthetic" if case == "restaged" else case, base / "corpus"
        )
    members = [(recording.name, _stage(spool, recording)) for recording in recordings]

    discovered: list[dict[str, Any]] = []
    for index, member in enumerate(members, start=1):
        root = _job_root(base, f"discover-{index}", [member], restage=False)
        result = job.call(root, "discover_iracing_sessions", {"root": str(root), "races_only": False, "limit": 50}, 120)
        if result.get("session_count") != 1:
            raise SystemExit(f"Discovery of {member[0]} found {result.get('session_count')} groups, expected 1.")
        discovered.append(
            normalise(_server_group(result["sessions"][0], member[1].name), {}, roots=Roots((("<iracing_root>", root),)))
        )
        shutil.rmtree(root)

    analysis_root = _job_root(base, "analysis-1", members, restage=case == "restaged")
    grouped = job.call(analysis_root, "discover_iracing_sessions", {"root": str(analysis_root), "races_only": False, "limit": 50}, 120)
    if grouped.get("session_count") != 1:
        raise SystemExit(f"The analysis job root holds {grouped.get('session_count')} groups, expected 1.")
    group = grouped["sessions"][0]
    if group.get("time_source") == "mtime":
        if len(members) != 1:
            raise SystemExit("A group keyed by path must have exactly one recording.")
        selector = str(analysis_root / "telemetry" / members[0][0])
        server_key = f"local-file:{members[0][1].name}"
    else:
        selector = str(group["group_id"])
        server_key = selector
    started = time.perf_counter()
    result = job.call(
        analysis_root,
        "analyze_iracing_race",
        {"selector": selector, "iracing_root": str(analysis_root), "archive_root": str(job.archive), "target_hz": None},
        600,
    )
    wall_ms = (time.perf_counter() - started) * 1000.0
    roots = Roots((("<archive_root>", job.archive), ("<iracing_root>", analysis_root)))
    write_archive_capture(job.archive, out, roots, workflow_result=result, engine_output=None)
    counts: dict[str, int] = {}
    grouped["sessions"] = [_server_group(group, members[0][1].name)]
    if isinstance(grouped.get("latest_race"), dict):
        grouped["latest_race"] = _server_group(grouped["latest_race"], members[0][1].name)
    (out / "outputs" / "discovery.json").write_text(
        canonical_json(normalise({"per_recording": discovered, "analysis_root": grouped}, counts, roots=roots)),
        encoding="utf-8",
        newline="\n",
    )
    (out / "timings").mkdir(exist_ok=True)
    (out / "timings" / "output-run.json").write_text(json.dumps({"analyze_wall_ms": round(wall_ms, 1)}, indent=2), encoding="utf-8")
    _capture_metadata(
        out,
        "worker",
        case=case,
        recordings=[{"name": name, "sha256": spooled.name} for name, spooled in members],
        server_group_key=server_key,
        analysis_id=result.get("analysis_id"),
        analyzer_digest="unpinned",
        native_stages=args.native_stages,
        raw_archive=args.raw_archive,
        roots={"<archive_root>": str(job.archive), "<iracing_root>": str(analysis_root)},
    )
    print(json.dumps({"case": case, "selector": TIMESTAMP_SLUG.sub("<timestamp>", roots.in_string(selector, {})),
                      "analysis_id": result.get("analysis_id"), "analyze_wall_ms": round(wall_ms, 1)}, indent=2))
    if not args.keep_base:
        shutil.rmtree(base)
    return 0


def _cross_platform_text(text: str, json_like: bool) -> str:
    text = text.replace("\r\n", "\n")
    run = r'[^"]*' if json_like else r"[^\s\"'`)\]>]*"
    pattern = re.compile("(" + "|".join(re.escape(token) for token in ROOT_TOKENS) + ")(" + run + ")")
    return pattern.sub(lambda match: match.group(1) + match.group(2).replace("\\\\", "/").replace("\\", "/"), text)


def _load_metadata(root: Path) -> dict[str, Any]:
    path = root / "capture.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def command_compare(args: argparse.Namespace) -> int:
    left_capture, right_capture = Path(args.left).resolve(), Path(args.right).resolve()
    mode = "raw" if args.raw else "outputs"
    left_root, right_root = left_capture / mode, right_capture / mode
    for root in (left_root, right_root):
        if not root.is_dir():
            raise SystemExit(f"Missing {root}: capture with this version of the harness first.")
    problems: list[str] = []
    left_meta, right_meta = _load_metadata(left_capture), _load_metadata(right_capture)
    for key in ("analyzer_digest", "raw_archive"):
        if left_meta.get(key) != right_meta.get(key):
            problems.append(f"capture.json {key}: {left_meta.get(key)!r} != {right_meta.get(key)!r} (not comparable)")
    left_files = {p.relative_to(left_root).as_posix() for p in left_root.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right_root).as_posix() for p in right_root.rglob("*") if p.is_file()}
    ignored = {"normalisation.json", "substitutions.json"} if args.ignore_normalisation_counts else set()
    for name in sorted(left_files ^ right_files):
        problems.append(f"{name}: present only in {'left' if name in left_files else 'right'}")
    identical = 0
    for name in sorted((left_files & right_files) - ignored):
        a = (left_root / name).read_bytes()
        b = (right_root / name).read_bytes()
        if args.cross_platform:
            json_like = name.endswith((".json", ".jsonl"))
            a = _cross_platform_text(a.decode("utf-8"), json_like).encode("utf-8")
            b = _cross_platform_text(b.decode("utf-8"), json_like).encode("utf-8")
        if a == b:
            identical += 1
            continue
        problems.append(f"{name}: bytes differ")
        if name.endswith(".json") and not args.raw:
            for line in json_differences(json.loads(a), json.loads(b))[:20]:
                problems.append(f"    {line}")
        else:
            for line in text_differences(a.decode("utf-8", "replace"), b.decode("utf-8", "replace")):
                problems.append(f"    {line}")
    report = {
        "mode": mode,
        "cross_platform": bool(args.cross_platform),
        "left": str(left_root),
        "right": str(right_root),
        "left_platform": left_meta.get("platform"),
        "right_platform": right_meta.get("platform"),
        "files_compared": len((left_files & right_files) - ignored),
        "files_identical": identical,
        "identical": not problems,
        "analysis_id": {
            side: meta.get("analysis_id") for side, meta in (("left", left_meta), ("right", right_meta))
            if meta.get("analysis_id")
        },
    }
    print(json.dumps(report, indent=2))
    for line in problems[:200]:
        print(line)
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def worker_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--native-stages",
            default="",
            help=f"Comma list passed as IRACING_COACH_NATIVE_STAGES ({', '.join(NATIVE_STAGES)}); empty in v1.",
        )
        command.add_argument(
            "--raw-archive",
            choices=("external", "copy"),
            default="external",
            help="external (default, production): IRACING_COACH_RAW_ARCHIVE=external; copy: the legacy raw copy.",
        )

    capture = commands.add_parser("capture", help="Capture outputs and timings for one recording.")
    capture.add_argument("--source", required=True, help="Path to an .ibt recording (read only).")
    capture.add_argument("--out", required=True, help="Capture directory to write.")
    capture.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Timed cold runs, each in a fresh process (default 3). Outputs come from one extra untimed run.",
    )
    capture.add_argument("--iracing-root", help="iRacing documents root (default: parent of telemetry/).")
    capture.add_argument("--archive", help="Harness-owned scratch archive (wiped per run).")
    capture.add_argument("--timing-only", action="store_true", help="Skip writing outputs.")
    capture.add_argument(
        "--unpinned-identity",
        action="store_true",
        help="Do not pin the analyzer digest: analysis and run ids are production ids.",
    )
    worker_options(capture)
    capture.set_defaults(handler=command_capture)

    once = commands.add_parser("_run-once", help=argparse.SUPPRESS)
    once.add_argument("--source", required=True)
    once.add_argument("--archive", required=True)
    once.add_argument("--iracing-root", required=True)
    once.add_argument("--timing", required=True)
    once.add_argument("--out")
    once.add_argument("--unpinned-identity", action="store_true")
    once.set_defaults(handler=command_run_once)

    worker = commands.add_parser("capture-worker", help="Capture one analysis driven through mcp_server.py.")
    selection = worker.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case", choices=("synthetic", "reconnect", "restaged", "local-file"))
    selection.add_argument("--source", nargs="+", help="Recordings that form one group (read only).")
    worker.add_argument("--out", required=True, help="Capture directory to write.")
    worker.add_argument("--base", help="Harness-owned scratch for spool, job roots and archive.")
    worker.add_argument("--keep-base", action="store_true", help="Leave the scratch in place for inspection.")
    worker_options(worker)
    worker.set_defaults(handler=command_capture_worker)

    compare = commands.add_parser("compare", help="Byte-compare two capture directories.")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument("--raw", action="store_true", help="Compare persisted bytes (raw/), the writer gate.")
    compare.add_argument(
        "--cross-platform",
        action="store_true",
        help="Fold CRLF to LF and separators after root tokens (Windows oracle vs Linux).",
    )
    compare.add_argument(
        "--ignore-normalisation-counts",
        action="store_true",
        help="Do not compare the count of normalised fields or raw substitutions.",
    )
    compare.set_defaults(handler=command_compare)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
