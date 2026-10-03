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
    Every worker process runs through ``worker_trace.py``, which records each
    ``native_stages.load_telemetry`` call; ``capture.json`` keeps the record.

``corpus``
    The port gate in one command (``docs/08-rewrite/port/phase1-decode.md``):
    for every recording of the fixtures (``test-data/ibt``) and, when
    ``COACH_PRIVATE_CORPUS`` names directories or files (``os.pathsep``
    separated), every ``.ibt`` under them (deduplicated by SHA-256), it
    compares the decoded-matrix golden of ``emit_ibt_golden.py`` with
    ``coach-analysis golden``, captures the oracle (no native stage) and the
    ``--native-stages`` path, and compares them in raw mode (the gate) and
    parsed mode; for the fixtures it also runs the four worker-interface
    cases both ways. Exit status 0 only when every comparison is identical,
    the native decode demonstrably ran on the native side only (in-process
    stage timings; one successful traced call per worker recording), and a
    recording rejected by the oracle is rejected natively with the same
    exception class and stated facts (never a crash or an unrelated failure).

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
appear in raw mode as row dumps (``raw/<name>.dump.json``) with only that
allowlist applied to each value: an ISO instant in a ``*_at`` column, a
``*_ms`` / ``elapsed_seconds`` number, an ``analysis_sha256`` hash, and the
allowlist's text rules inside every other string. Other binaries by SHA-256.

Every capture applies ``IRACING_COACH_RAW_ARCHIVE=external`` (the production
setting) unless ``--raw-archive copy`` is given, and passes
``--native-stages`` to the worker as ``IRACING_COACH_NATIVE_STAGES``; a
non-empty list requires the ``coach-analysis`` executable (``--native-bin``,
else ``IRACING_COACH_NATIVE_BIN``, else ``PATH``), passed on as
``IRACING_COACH_NATIVE_BIN``.

Examples::

    python tools/parity/analysis_parity.py capture --source RACE.ibt --out before/nh --runs 3
    python tools/parity/analysis_parity.py compare before/nh after/nh
    python tools/parity/analysis_parity.py compare --raw before/nh after/nh
    python tools/parity/analysis_parity.py capture-worker --case reconnect --out win/reconnect
    python tools/parity/analysis_parity.py compare --raw --cross-platform win/reconnect linux/reconnect
    python tools/parity/analysis_parity.py corpus --native-stages decode --native-bin target/release/coach-analysis.exe --out gate
"""

from __future__ import annotations

import argparse
import functools
import hashlib
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
CORPUS_MARKER = ".analysis-parity-corpus"
FIXTURES = REPOSITORY / "test-data" / "ibt"
NATIVE_BIN_ENV = "IRACING_COACH_NATIVE_BIN"
WORKER_CASES = ("synthetic", "reconnect", "restaged", "local-file")
NATIVE_DECODE_STAGE = "native_stages.load_telemetry"
WORKER_TRACE = Path(__file__).resolve().parent / "worker_trace.py"
EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\b.*")
PINNED_ANALYZER_SHA256 = "0" * 64
NATIVE_STAGES = ("decode", "kernels", "json_writer")
CAPTURE_FORMAT = 3  # 3: raw SQLite row dumps, worker native-decode trace

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
# The same allowlist for a SQLite column holding the whole value.
RAW_ELAPSED_COLUMN = re.compile(r"(?:.*_)?(?:ms|elapsed_seconds)")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
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


def _blob_or_value(column: str, value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"blob-sha256:{hashlib.sha256(value).hexdigest()}"
    return value


def raw_sqlite_value(column: str, value: Any, counts: dict[str, int], roots: Roots) -> Any:
    """One SQLite value with the raw-mode allowlist applied and nothing else."""

    def counted(rule: str, token: str) -> str:
        counts[rule] = counts.get(rule, 0) + 1
        return token

    if isinstance(value, str):
        if column.endswith("_at") and ISO_INSTANT.fullmatch(value):
            return counted("at_values", "<timestamp>")
        if column == "analysis_sha256" and SHA256_HEX.fullmatch(value):
            return counted("analysis_sha256", "<hash-of-timestamped-payload>")
        return raw_substitute(value, counts, roots, json_like=True)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and RAW_ELAPSED_COLUMN.fullmatch(column):
        return counted("elapsed_values", "<elapsed-ms>")
    return _blob_or_value(column, value)


def _sqlite_dump(path: Path, value: Callable[[str, Any], Any] = _blob_or_value) -> dict[str, Any]:
    """Every table's schema and rows (each value through ``value``), in a stable order, read-only."""

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
            rows = [{column: value(column, item) for column, item in zip(columns, row)} for row in cursor.fetchall()]
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
            raw_target = raw / f"{relative}.dump.json"
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            raw_target.write_text(
                canonical_json(
                    _sqlite_dump(path, lambda column, item: raw_sqlite_value(column, item, raw_counts, roots)),
                    sort_keys=True,
                ),
                encoding="utf-8",
                newline="\n",
            )
            raw_files[relative] = "raw-allowlist-row-dump"
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


def _require_native(stages: str, native_bin: str | None = None) -> None:
    """Fail unless every named stage is provided; export the executable for child processes."""

    names = [item.strip() for item in stages.split(",") if item.strip()]
    unknown = sorted(set(names) - set(NATIVE_STAGES))
    if unknown:
        raise SystemExit(f"Unknown native stage(s) {', '.join(unknown)}; expected {', '.join(NATIVE_STAGES)}.")
    if not names:
        return
    if native_bin:
        os.environ[NATIVE_BIN_ENV] = str(Path(native_bin).resolve())
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import native_stages  # noqa: PLC0415

    try:
        native_stages.requested_stages({native_stages.STAGES_ENV: stages})
        os.environ[NATIVE_BIN_ENV] = native_stages.native_binary()
    except native_stages.NativeStageError as exc:
        raise SystemExit(f"{exc} A capture without them would compare Python with Python.") from exc


def _native_identity(stages: str) -> dict[str, Any] | None:
    """The executable a native capture used, by path and SHA-256."""

    binary = os.environ.get(NATIVE_BIN_ENV) if stages.strip() else None
    if not binary:
        return None
    return {"path": binary, "sha256": _sha256_file(Path(binary))}


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
    import native_stages  # noqa: PLC0415

    # Present in stages_ms only when the native decode ran: the corpus gate
    # checks it, so a native capture can never silently be Python.
    _instrument(native_stages, "load_telemetry", stages, NATIVE_DECODE_STAGE)

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
    _require_native(args.native_stages, args.native_bin)
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
        native_binary=_native_identity(args.native_stages),
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
        # One line per native_stages.load_telemetry call in any worker process.
        self.trace = base / "native-decode-trace.jsonl"

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
        if self.stages.strip() and os.environ.get(NATIVE_BIN_ENV):
            environment[NATIVE_BIN_ENV] = os.environ[NATIVE_BIN_ENV]
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
            [_python(), "-X", "utf8", "-u", str(WORKER_TRACE), str(self.trace)],
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
    _require_native(args.native_stages, args.native_bin)
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
    native_calls = (
        [json.loads(line) for line in job.trace.read_text(encoding="utf-8").splitlines() if line.strip()]
        if job.trace.is_file()
        else []
    )
    _capture_metadata(
        out,
        "worker",
        case=case,
        recordings=[{"name": name, "sha256": spooled.name} for name, spooled in members],
        server_group_key=server_key,
        native_decode_calls=native_calls,
        analysis_id=result.get("analysis_id"),
        analyzer_digest="unpinned",
        native_stages=args.native_stages,
        native_binary=_native_identity(args.native_stages),
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
    report, problems = compare_captures(
        Path(args.left), Path(args.right),
        raw=args.raw, cross_platform=args.cross_platform,
        ignore_normalisation_counts=args.ignore_normalisation_counts,
    )
    print(json.dumps(report, indent=2))
    for line in problems[:200]:
        print(line)
    return 0 if not problems else 1


def compare_captures(
    left: Path,
    right: Path,
    *,
    raw: bool,
    cross_platform: bool = False,
    ignore_normalisation_counts: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Compare two captures: the report and the differences (none means identical)."""

    left_capture, right_capture = left.resolve(), right.resolve()
    mode = "raw" if raw else "outputs"
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
    ignored = {"normalisation.json", "substitutions.json"} if ignore_normalisation_counts else set()
    for name in sorted(left_files ^ right_files):
        problems.append(f"{name}: present only in {'left' if name in left_files else 'right'}")
    identical = 0
    for name in sorted((left_files & right_files) - ignored):
        a = (left_root / name).read_bytes()
        b = (right_root / name).read_bytes()
        if cross_platform:
            json_like = name.endswith((".json", ".jsonl"))
            a = _cross_platform_text(a.decode("utf-8"), json_like).encode("utf-8")
            b = _cross_platform_text(b.decode("utf-8"), json_like).encode("utf-8")
        if a == b:
            identical += 1
            continue
        problems.append(f"{name}: bytes differ")
        if name.endswith(".json") and not raw:
            for line in json_differences(json.loads(a), json.loads(b))[:20]:
                problems.append(f"    {line}")
        else:
            for line in text_differences(a.decode("utf-8", "replace"), b.decode("utf-8", "replace")):
                problems.append(f"    {line}")
    report = {
        "mode": mode,
        "cross_platform": bool(cross_platform),
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
    if left_meta.get("analysis_id") != right_meta.get("analysis_id"):
        problems.append(
            f"capture.json analysis_id: {left_meta.get('analysis_id')!r} != {right_meta.get('analysis_id')!r}"
        )
    report["identical"] = not problems
    return report, problems


# --------------------------------------------------------------------------
# Corpus gate (oracle against native stages, every recording)
# --------------------------------------------------------------------------


def _corpus_recordings(include_private: bool) -> tuple[list[tuple[str, Path, str]], int]:
    """(kind, path, sha256) for the fixtures and COACH_PRIVATE_CORPUS, first path per hash."""

    candidates = [("fixture", path) for path in sorted(FIXTURES.glob("*.ibt"))]
    private = os.environ.get("COACH_PRIVATE_CORPUS", "") if include_private else ""
    for entry in (item for item in private.split(os.pathsep) if item.strip()):
        root = Path(entry).resolve()
        if root.is_file():
            candidates.append(("private", root))
        elif root.is_dir():
            candidates.extend(("private", path) for path in sorted(root.rglob("*.ibt")) if path.is_file())
        else:
            raise SystemExit(f"COACH_PRIVATE_CORPUS entry not found: {root}")
    seen: set[str] = set()
    recordings: list[tuple[str, Path, str]] = []
    duplicates = 0
    for kind, path in candidates:
        sha = _sha256_file(path)
        if sha in seen:
            duplicates += 1
            continue
        seen.add(sha)
        recordings.append((kind, path, sha))
    return recordings, duplicates


# Python's IbtTruncatedError text; coach_ibt words it differently but states the same facts.
TRUNCATION_TEXT = re.compile(
    r"(?P<region>[^:]+) requires bytes \[(?P<start>\d+), (?P<end>\d+)\), but the file contains only (?P<available>\d+) bytes$"
)
# Oracle exceptions whose message coach-analysis reproduces character for character.
EXACT_MESSAGE_CLASSES = frozenset({"IbtChannelError", "ValueError", "OverflowError"})


def native_rejection(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any] | None:
    """The rejection ``coach-analysis golden`` recorded, or None (a crash or any other failure)."""

    if completed.returncode != 1:
        return None
    try:
        record = json.loads(completed.stdout)["error"]
    except (ValueError, KeyError, TypeError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("message"), str) else None


def rejection_differences(oracle: BaseException, native: dict[str, Any] | None) -> list[str]:
    """Why a native rejection is not the oracle's: by exception class, then by stated facts.

    The class must be the oracle's (coach-ibt's mapping: an IbtYamlError also stands for the
    TypeError or RecursionError that escapes literal_eval, OSError for any OSError subclass).
    Messages coach-analysis writes itself must be identical; a truncation must name the same
    region and byte range; coach-ibt's other messages are its own wording and compare by class
    (as in ``crates/coach-ibt/tests/parity.rs``).
    """

    if native is None:
        return ["native did not record a rejection (a crash or an unrelated failure)"]
    oracle_class, native_class = type(oracle).__name__, native.get("class")
    same_class = (
        native_class == oracle_class
        or (native_class == "IbtYamlError" and oracle_class in ("TypeError", "RecursionError"))
        or (native_class == "OSError" and isinstance(oracle, OSError))
    )
    if not same_class:
        return [f"class {oracle_class} != {native_class!r} ({oracle} / {native.get('message')})"]
    if oracle_class in EXACT_MESSAGE_CLASSES and str(oracle) != native["message"]:
        return [f"{oracle_class} message {str(oracle)!r} != {native['message']!r}"]
    if oracle_class == "IbtTruncatedError":
        match = TRUNCATION_TEXT.search(str(oracle))
        stated = native.get("truncated")
        expected = None if match is None else {
            "region": match["region"].strip(),
            **{key: int(match[key]) for key in ("start", "end", "available")},
        }
        if expected is None or stated != expected:
            return [f"truncation {expected} != {stated}"]
    return []


def _golden_rate_label(rate: float | None) -> str:
    return "native" if rate is None else f"{rate:g}hz"


def _decode_golden_parity(path: Path, rates: list[float | None]) -> dict[str, Any]:
    """emit_ibt_golden.py (the oracle) against ``coach-analysis golden``, all channels."""

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import emit_ibt_golden  # noqa: PLC0415

    results: dict[str, Any] = {}
    for rate in rates:
        label = _golden_rate_label(rate)
        oracle_exception: BaseException | None = None
        try:
            oracle = emit_ibt_golden.emit(str(path), rate)
            oracle.pop("spot_values", None)
        except Exception as exc:  # noqa: BLE001 - the oracle's rejection is a result
            oracle, oracle_exception = None, exc
        command = [os.environ[NATIVE_BIN_ENV], "golden", str(path)]
        if rate is not None:
            command += ["--target-hz", repr(float(rate))]
        completed = subprocess.run(command, capture_output=True, check=False)
        native = json.loads(completed.stdout) if completed.returncode == 0 else None
        native_error = (
            f"exit {completed.returncode}: {completed.stderr.decode('utf-8', 'replace').strip()}"
            if completed.returncode
            else None
        )
        if oracle is None or native is None:
            differences = (
                rejection_differences(oracle_exception, native_rejection(completed))
                if oracle_exception is not None and native is None
                else [f"only {'the oracle' if oracle is None else 'native'} rejected the recording"]
            )
            results[label] = {
                "identical": not differences,
                "rejected_by_oracle": (
                    None if oracle_exception is None else f"{type(oracle_exception).__name__}: {oracle_exception}"
                ),
                "rejected_by_native": native_error,
                "rejection_differences": differences,
            }
            continue
        differing = sorted(
            key for key in oracle.keys() | native.keys()
            if key != "target_hz"
            and (oracle.get(key) != native.get(key) or type(oracle.get(key)) is not type(native.get(key)))
        )
        results[label] = {
            "identical": not differing,
            "differing_fields": differing,
            "matrix_sha256": oracle["matrix_sha256"],
            "sample_count": oracle["sample_count"],
            "variables": len(oracle["variables"]),
        }
    return results


def _final_error_line(stderr: bytes, roots: dict[str, Path]) -> str:
    """The last exception line of a failed capture (its own exit message follows it)."""

    lines = [line for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    exceptions = [line for line in lines if EXCEPTION_LINE.match(line)]
    text = exceptions[-1] if exceptions else (lines[-1] if lines else "")
    for token, root in roots.items():
        text = text.replace(str(root), token)
    return text


def _capture_side(command: list[str], roots: dict[str, Path]) -> tuple[bool, str, float]:
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, check=False)
    elapsed = time.perf_counter() - started
    if completed.returncode == 0:
        return True, "", elapsed
    return False, _final_error_line(completed.stderr, roots), elapsed


def _output_run(capture: Path) -> dict[str, Any]:
    path = capture / "timings" / "output-run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _analysis_wall_ms(capture: Path, key: str) -> float | None:
    return _output_run(capture).get(key)


def _pair_result(left: Path, right: Path, ok: tuple[bool, bool], errors: tuple[str, str]) -> dict[str, Any]:
    if not all(ok):
        identical = not any(ok) and errors[0] == errors[1]
        return {"identical": identical, "oracle_error": errors[0] or None, "native_error": errors[1] or None}
    raw_report, raw_problems = compare_captures(left, right, raw=True)
    parsed_report, parsed_problems = compare_captures(left, right, raw=False)
    return {
        "identical": not raw_problems and not parsed_problems,
        "raw_identical": not raw_problems,
        "parsed_identical": not parsed_problems,
        "files_compared": raw_report["files_compared"] + parsed_report["files_compared"],
        "differences": (raw_problems + parsed_problems)[:40],
    }


def _analysis_parity(path: Path, dest: Path, stages: str, raw_archive: str) -> dict[str, Any]:
    sides = {"oracle": "", "native": stages}
    ok: list[bool] = []
    errors: list[str] = []
    walls: dict[str, Any] = {}
    for side, side_stages in sides.items():
        capture, archive = dest / side, dest / f"{side}-archive"
        command = [
            _python(), "-X", "utf8", str(Path(__file__).resolve()), "capture",
            "--source", str(path), "--out", str(capture), "--archive", str(archive),
            "--runs", "0", "--native-stages", side_stages, "--raw-archive", raw_archive,
        ]
        success, error, elapsed = _capture_side(command, {"<archive_root>": archive, "<source>": path})
        ok.append(success)
        errors.append(error)
        walls[side] = {"capture_s": round(elapsed, 2), "analysis_wall_ms": _analysis_wall_ms(capture, "wall_ms")}
    result = _pair_result(dest / "oracle", dest / "native", (ok[0], ok[1]), (errors[0], errors[1]))
    result["timing"] = walls
    if all(ok):
        ran = {side: NATIVE_DECODE_STAGE in (_output_run(dest / side).get("stages_ms") or {}) for side in sides}
        result["native_decode_ran"] = ran
        expected = {"oracle": False, "native": "decode" in stages}
        if ran != expected:
            result["identical"] = False
            result.setdefault("differences", []).append(f"native decode ran {ran}, expected {expected}")
    return result


def _worker_parity(case: str, dest: Path, stages: str, raw_archive: str) -> dict[str, Any]:
    sides = {"oracle": "", "native": stages}
    ok: list[bool] = []
    errors: list[str] = []
    walls: dict[str, Any] = {}
    for side, side_stages in sides.items():
        capture, base = dest / side, dest / f"{side}-base"
        command = [
            _python(), "-X", "utf8", str(Path(__file__).resolve()), "capture-worker",
            "--case", case, "--out", str(capture), "--base", str(base),
            "--native-stages", side_stages, "--raw-archive", raw_archive,
        ]
        success, error, elapsed = _capture_side(command, {"<base>": base})
        ok.append(success)
        errors.append(error)
        walls[side] = {"capture_s": round(elapsed, 2), "analyze_wall_ms": _analysis_wall_ms(capture, "analyze_wall_ms")}
    result = _pair_result(dest / "oracle", dest / "native", (ok[0], ok[1]), (errors[0], errors[1]))
    result["timing"] = walls
    if all(ok):
        metadata = {side: _load_metadata(dest / side) for side in sides}
        result["analysis_id"] = metadata["native"].get("analysis_id")
        result["native_decode_calls"] = {side: len(meta.get("native_decode_calls") or []) for side, meta in metadata.items()}
        problems = worker_native_decode_problems(metadata["oracle"], metadata["native"], "decode" in stages)
        if problems:
            result["identical"] = False
            result.setdefault("differences", []).extend(problems)
    return result


def worker_native_decode_problems(oracle: dict[str, Any], native: dict[str, Any], decode: bool) -> list[str]:
    """The worker captures' trace must show the native decode on the native side only.

    The oracle side makes no ``native_stages.load_telemetry`` call; with ``decode``
    the native side makes exactly one successful call per recording of the case.
    """

    problems: list[str] = []
    for side, meta in (("oracle", oracle), ("native", native)):
        if meta.get("capture_format") != CAPTURE_FORMAT or not isinstance(meta.get("native_decode_calls"), list):
            problems.append(f"{side} capture has no native-decode trace (capture format {meta.get('capture_format')})")
    if problems:
        return problems
    if oracle["native_decode_calls"]:
        problems.append(f"oracle worker made {len(oracle['native_decode_calls'])} native decode call(s)")
    if decode:
        expected = sorted(item["name"] for item in native.get("recordings") or [])
        calls = native["native_decode_calls"]
        failed = [call for call in calls if call.get("error") is not None]
        if failed:
            problems.append(f"native decode call(s) failed: {failed}")
        if sorted(call.get("source") for call in calls) != expected:
            problems.append(
                f"native worker decoded {sorted(call.get('source') for call in calls)}, expected one call for each of {expected}"
            )
    elif native["native_decode_calls"]:
        problems.append("native side made native decode calls without the decode stage")
    return problems


def command_corpus(args: argparse.Namespace) -> int:
    stages = args.native_stages
    if not stages.strip():
        raise SystemExit("corpus compares the oracle with native stages: pass --native-stages.")
    _require_native(stages, args.native_bin)
    out = Path(args.out).resolve()
    _prepare_owned(out, CORPUS_MARKER)
    rates: list[float | None] = [None if item == "native" else float(item) for item in args.golden_rates.split(",") if item]
    recordings, duplicates = _corpus_recordings(not args.fixtures_only)
    print(f"{len(recordings)} recordings ({duplicates} duplicates skipped); native {os.environ[NATIVE_BIN_ENV]}", flush=True)
    results: list[dict[str, Any]] = []
    for number, (kind, path, sha) in enumerate(recordings, start=1):
        entry: dict[str, Any] = {"kind": kind, "name": path.name, "sha256": sha, "size_bytes": path.stat().st_size}
        dest = out / f"{number:03d}-{sha[:12]}"
        entry["decode_golden"] = _decode_golden_parity(path, rates)
        entry["analysis"] = _analysis_parity(path, dest, stages, args.raw_archive)
        entry["identical"] = entry["analysis"]["identical"] and all(
            item["identical"] for item in entry["decode_golden"].values()
        )
        if entry["identical"] and not args.keep:
            shutil.rmtree(dest, ignore_errors=True)
        results.append(entry)
        timing = entry["analysis"]["timing"]
        print(
            f"[{number}/{len(recordings)}] {'IDENTICAL' if entry['identical'] else 'DIFFERENT'} "
            f"{kind} {sha[:12]} {entry['size_bytes'] / 1e6:.1f} MB "
            f"golden {'/'.join(label for label in entry['decode_golden'])} "
            f"analysis oracle {timing['oracle']['analysis_wall_ms']} ms native {timing['native']['analysis_wall_ms']} ms"
            + ("" if entry["analysis"].get("oracle_error") is None else f" (both: {entry['analysis']['oracle_error'][:100]})"),
            flush=True,
        )
    workers: list[dict[str, Any]] = []
    for case in () if args.no_worker_cases else WORKER_CASES:
        dest = out / f"worker-{case}"
        entry = {"case": case, **_worker_parity(case, dest, stages, args.raw_archive)}
        if entry["identical"] and not args.keep:
            shutil.rmtree(dest, ignore_errors=True)
        workers.append(entry)
        print(f"[worker {case}] {'IDENTICAL' if entry['identical'] else 'DIFFERENT'} analysis_id {entry.get('analysis_id')}", flush=True)
    identical = all(item["identical"] for item in results + workers)
    report = {
        "identical": identical,
        "native_stages": stages,
        "native_binary": _native_identity(stages),
        "python": platform.python_version(),
        "platform": sys.platform,
        "raw_archive": args.raw_archive,
        "golden_rates": [_golden_rate_label(rate) for rate in rates],
        "private_corpus": bool(os.environ.get("COACH_PRIVATE_CORPUS")) and not args.fixtures_only,
        "recordings": results,
        "duplicates_skipped": duplicates,
        "worker_cases": workers,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    failed = [item.get("name") or item.get("case") for item in results + workers if not item["identical"]]
    print(
        f"{'PASS' if identical else 'FAIL'}: {len(results) - len([r for r in results if not r['identical']])}/{len(results)} recordings and "
        f"{len(workers) - len([w for w in workers if not w['identical']])}/{len(workers)} worker cases identical"
        + (f"; different: {', '.join(failed)}" if failed else "")
        + f"; report {out / 'report.json'}",
        flush=True,
    )
    return 0 if identical else 1


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
        command.add_argument(
            "--native-bin",
            help=f"coach-analysis executable for native stages (default {NATIVE_BIN_ENV}, else PATH).",
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

    corpus = commands.add_parser("corpus", help="Oracle against native stages on the fixtures and the private corpus.")
    corpus.add_argument("--out", required=True, help="Harness-owned scratch for captures and report.json.")
    corpus.add_argument(
        "--golden-rates",
        default="native,20",
        help="Comma list of decode-golden rates: native or a target Hz (default native,20).",
    )
    corpus.add_argument("--fixtures-only", action="store_true", help="Ignore COACH_PRIVATE_CORPUS.")
    corpus.add_argument("--no-worker-cases", action="store_true", help="Skip the four worker-interface cases.")
    corpus.add_argument("--keep", action="store_true", help="Keep identical captures (differing ones are always kept).")
    worker_options(corpus)
    corpus.set_defaults(handler=command_corpus)

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
