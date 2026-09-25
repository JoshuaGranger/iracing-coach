#!/usr/bin/env python3
"""Capture and compare complete cold-analysis outputs and stage timings.

A performance change to the deterministic backend must not change a single
emitted value. This tool makes that claim checkable on real recordings:

``capture``
    Runs the production ``analyze_race_workflow`` on one ``.ibt`` recording,
    cold, in a fresh process and a fresh harness-owned archive. One untimed
    run writes the normalised outputs: the engine's in-memory result in its
    own key order, the cached analysis, the workflow result, and every
    text/JSON/SQLite artifact the workflow persisted. ``--runs`` further cold
    runs record workflow and per-stage timings and their medians.

``compare``
    Byte-compares two capture directories and prints the differing paths.
    Exit status 0 means every normalised output file is byte-identical.

Normalisation is deliberately narrow; the rules are recorded in each
capture's ``timings.json`` and the per-rule counts in
``outputs/normalisation.json``:

* wall-clock timestamps (``analyzed_at``/``created_at``/... keys, any
  ``*_at`` key holding an ISO-8601 instant, ISO-8601 instants inside persisted
  text, and the timestamp slug that names report directories) become
  ``<timestamp>``; the analysis-cache envelope's hash of the timestamped
  analysis is replaced too (the analysis itself is compared in full);
* SQLite archives are compared as normalised row dumps, not raw bytes;
* elapsed-millisecond values (``*_ms`` keys, ``elapsed_seconds``) become
  ``<elapsed-ms>``;
* the analyzer *source* digest is pinned to a constant before the run, so
  analysis/run IDs derived from it do not rotate merely because a source file
  was edited. That digest is an input to IDs only - never to a measured value.

Everything else - every lap, trace point, rounded float, catalog entry and
channel list - must match byte for byte. Raw recordings are only read; the
workflow copies them into the harness archive, which is wiped between runs.

Examples::

    python tools/analysis_parity.py capture --source RACE.ibt --out before/nh --runs 3
    python tools/analysis_parity.py compare before/nh after/nh

Use the same ``--archive`` path for both captures: archive paths appear in the
workflow result, so a different scratch location is a (legitimate) difference.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
ARCHIVE_MARKER = ".analysis-parity-archive"
PINNED_ANALYZER_SHA256 = "0" * 64

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
ISO_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".svg", ".html", ".jsonl"}

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
# Normalisation and comparison
# --------------------------------------------------------------------------


def normalise(value: Any, counts: dict[str, int], key: str | None = None) -> Any:
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
        return {k: normalise(v, counts, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(item, counts) for item in value]
    if isinstance(value, str) and TIMESTAMP_SLUG.search(value):
        counts["timestamp_slugs"] = counts.get("timestamp_slugs", 0) + 1
        return TIMESTAMP_SLUG.sub("<timestamp>", value)
    return value


def normalise_text(text: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        counts["timestamp_text"] = counts.get("timestamp_text", 0) + 1
        return "<timestamp>"

    return TIMESTAMP_SLUG.sub(replace, ISO_INSTANT.sub(replace, text))


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


# --------------------------------------------------------------------------
# One cold run (executed in its own process)
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


def _prepare_archive(archive: Path) -> None:
    if archive.exists():
        if not (archive / ARCHIVE_MARKER).is_file():
            if any(archive.iterdir()):
                raise SystemExit(
                    f"Refusing to wipe {archive}: it is not a harness-owned archive "
                    f"(missing {ARCHIVE_MARKER})."
                )
        shutil.rmtree(archive)
    archive.mkdir(parents=True)
    (archive / ARCHIVE_MARKER).write_text("analysis_parity harness archive\n", encoding="utf-8")


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


def run_once(source: Path, archive: Path, iracing_root: Path, out: Path | None, timing_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import analysis_engine  # noqa: PLC0415
    import workflow  # noqa: PLC0415

    # IDs only: see the module docstring.
    analysis_engine.analyzer_bundle_sha256 = lambda: PINNED_ANALYZER_SHA256  # type: ignore[assignment]
    stages: dict[str, float] = {}
    for name in ENGINE_STAGES:
        _instrument(analysis_engine, name, stages, name)
    for name in WORKFLOW_STAGES:
        _instrument(workflow, name, stages, f"workflow.{name}")

    engine_output: list[str] = []
    if out is not None:
        # Output runs are never timed. Serialise the engine's in-memory result
        # at once, before the workflow copies or decorates it, so the capture
        # is exactly what analyze_telemetry returned, in insertion order.
        produce = workflow.analyze_telemetry

        def capture_engine_output(*args: Any, **kwargs: Any) -> Any:
            produced = produce(*args, **kwargs)
            engine_output.append(canonical_json(normalise(produced, {})))
            return produced

        workflow.analyze_telemetry = capture_engine_output  # type: ignore[assignment]

    _prepare_archive(archive)
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

    counts: dict[str, int] = {}
    files: dict[str, str] = {}
    out.mkdir(parents=True, exist_ok=True)
    cache_files = sorted((archive / "analysis-cache").glob("*.json"))
    if len(cache_files) != 1:
        raise SystemExit(f"Expected one analysis-cache entry, found {len(cache_files)}.")
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    analysis = normalise(cached["analysis"], counts)
    (out / "analysis.json").write_text(canonical_json(analysis, sort_keys=True), encoding="utf-8")
    if len(engine_output) != 1:
        raise SystemExit(f"Expected one engine output, captured {len(engine_output)}.")
    (out / "engine-output.insertion-order.json").write_text(engine_output[0], encoding="utf-8")
    (out / "workflow-result.json").write_text(canonical_json(normalise(result, counts)), encoding="utf-8")

    artifacts = out / "archive"
    for path in sorted(archive.rglob("*")):
        if not path.is_file() or path.name == ARCHIVE_MARKER:
            continue
        relative = TIMESTAMP_SLUG.sub("_timestamp_", path.relative_to(archive).as_posix())
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() in {".json"}:
                try:
                    text = canonical_json(normalise(json.loads(text), counts))
                except ValueError:
                    text = normalise_text(text, counts)
            elif path.suffix.lower() == ".jsonl":
                lines = [
                    json.dumps(normalise(json.loads(line), counts), sort_keys=True, ensure_ascii=False)
                    for line in text.splitlines()
                    if line.strip()
                ]
                text = "\n".join(lines) + "\n"
            else:
                text = normalise_text(text, counts)
            target = artifacts / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            files[relative] = "normalised-text"
        elif path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}:
            target = artifacts / f"{relative}.dump.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(canonical_json(normalise(_sqlite_dump(path), counts), sort_keys=True), encoding="utf-8")
            files[relative] = "normalised-sqlite-dump"
        else:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            files[relative] = f"sha256:{digest.hexdigest()}"
    (out / "binary-and-file-index.json").write_text(canonical_json(files, sort_keys=True), encoding="utf-8")
    (out / "normalisation.json").write_text(canonical_json(counts, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _python() -> str:
    return os.environ.get("IRACING_COACH_PYTHON") or sys.executable


def _default_archive() -> Path:
    return Path(tempfile.gettempdir()) / "iracing-coach-analysis-parity" / "archive"


def command_capture(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise SystemExit(f"Recording not found: {source}")
    iracing_root = Path(args.iracing_root).resolve() if args.iracing_root else (
        source.parent.parent if source.parent.name.lower() == "telemetry" else source.parent
    )
    out = Path(args.out).resolve()
    archive = Path(args.archive).resolve() if args.archive else _default_archive()
    out.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []

    def cold_run(label: str, timing_path: Path, outputs: Path | None) -> None:
        command = [
            _python(),
            str(Path(__file__).resolve()),
            "_run-once",
            "--source", str(source),
            "--archive", str(archive),
            "--iracing-root", str(iracing_root),
            "--timing", str(timing_path),
        ]
        if outputs is not None:
            command += ["--out", str(outputs)]
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise SystemExit(f"{label} failed with exit code {completed.returncode}.")

    if not args.timing_only:
        outputs = out / "outputs"
        if outputs.exists():
            shutil.rmtree(outputs)
        cold_run("Output run", out / "timings" / "output-run.json", outputs)
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
        "normalisation": {
            "timestamp_keys": sorted(TIMESTAMP_KEYS),
            "elapsed_key_pattern": ELAPSED_KEY.pattern,
            "iso_instant_pattern": ISO_INSTANT.pattern,
            "timestamp_slug_pattern": TIMESTAMP_SLUG.pattern,
            "timestamp_derived_keys": sorted(TIMESTAMP_DERIVED_KEYS),
            "pinned_analyzer_sha256": PINNED_ANALYZER_SHA256,
        },
    }
    (out / "timings.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["median_ms"], indent=2))
    return 0


def command_run_once(args: argparse.Namespace) -> int:
    run_once(
        Path(args.source),
        Path(args.archive),
        Path(args.iracing_root),
        Path(args.out) if args.out else None,
        Path(args.timing),
    )
    return 0


def command_compare(args: argparse.Namespace) -> int:
    left_root = Path(args.left).resolve() / "outputs"
    right_root = Path(args.right).resolve() / "outputs"
    left_files = {p.relative_to(left_root).as_posix() for p in left_root.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right_root).as_posix() for p in right_root.rglob("*") if p.is_file()}
    ignored = {"normalisation.json"} if args.ignore_normalisation_counts else set()
    problems: list[str] = []
    for name in sorted(left_files ^ right_files):
        problems.append(f"{name}: present only in {'left' if name in left_files else 'right'}")
    identical = 0
    for name in sorted((left_files & right_files) - ignored):
        a = (left_root / name).read_bytes()
        b = (right_root / name).read_bytes()
        if a == b:
            identical += 1
            continue
        problems.append(f"{name}: bytes differ")
        if name.endswith(".json"):
            for line in json_differences(json.loads(a), json.loads(b))[:20]:
                problems.append(f"    {line}")
    report = {
        "left": str(left_root),
        "right": str(right_root),
        "files_compared": len((left_files & right_files) - ignored),
        "files_identical": identical,
        "identical": not problems,
        "analysis_sha256": {
            side: hashlib.sha256((root / "analysis.json").read_bytes()).hexdigest()
            for side, root in (("left", left_root), ("right", right_root))
            if (root / "analysis.json").is_file()
        },
    }
    print(json.dumps(report, indent=2))
    for line in problems[:200]:
        print(line)
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

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
    capture.set_defaults(handler=command_capture)

    once = commands.add_parser("_run-once", help=argparse.SUPPRESS)
    once.add_argument("--source", required=True)
    once.add_argument("--archive", required=True)
    once.add_argument("--iracing-root", required=True)
    once.add_argument("--timing", required=True)
    once.add_argument("--out")
    once.set_defaults(handler=command_run_once)

    compare = commands.add_parser("compare", help="Byte-compare two capture directories.")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument(
        "--ignore-normalisation-counts",
        action="store_true",
        help="Do not compare the count of normalised fields.",
    )
    compare.set_defaults(handler=command_compare)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
