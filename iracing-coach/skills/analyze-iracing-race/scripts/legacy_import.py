"""Import legacy tuning experiments into a driver's worker archive (D12 migration).

The retired desktop app kept tuning experiments in its own archive: one row per
experiment in ``history.sqlite3`` and the full record as
``tuning/experiments/<season>/<car>/<track>/<id>.json``. Recordings are
re-ingested and re-analysed, so every analysis an experiment names has a new
``analysis_id`` and ``analysis_path``. The migration supplies the old-to-new
map; this module rewrites the experiment's analysis references through it and
records the experiment in the target archive with its original id and times.

Only the reference fields the tuning workflow itself writes are rewritten:
``analysis_id``/``analysis_path``, ``open_target_analysis_id``/
``open_target_analysis_path`` and the feedback's ``result_analysis_id``/
``result_analysis_path``. ``structured_evidence`` is the evidence as it was when
the recommendation was made (its hash names the experiment) and is kept as is.
A reference the map does not resolve is never guessed: its new value is null
and the legacy value is listed in ``legacy_import.unresolved``.

The legacy archive is read only: its database is copied to a private temporary
folder before it is opened.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .storage import ArchiveStore, safe_slug, utc_now
except ImportError:  # pragma: no cover - normal path for CLI/MCP script loading.
    from storage import ArchiveStore, safe_slug, utc_now

LEGACY_IMPORT_VERSION = 1
MAX_MAP_ENTRIES = 5000
MAX_EXPERIMENT_BYTES = 16 * 1024 * 1024

#: (id field, path field, where): the references the tuning workflow writes.
REFERENCE_FIELDS = (
    ("analysis_id", "analysis_path", "experiment"),
    ("open_target_analysis_id", "open_target_analysis_path", "experiment"),
    ("result_analysis_id", "result_analysis_path", "feedback"),
)

_ROW_COLUMNS = (
    "experiment_id",
    "created_at",
    "updated_at",
    "analysis_id",
    "package_id",
    "season_key",
    "car_key",
    "track_key",
    "setup_type",
    "setup_fingerprint",
    "symptoms_json",
    "recommendation_json",
    "feedback_json",
    "outcome",
    "experiment_path",
)


class LegacyImportError(ValueError):
    """The legacy archive or the reference map cannot be imported as given."""


def _analysis_map(entries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str | None]]:
    if len(entries) > MAX_MAP_ENTRIES:
        raise LegacyImportError(f"analysis_map holds more than {MAX_MAP_ENTRIES} entries.")
    result: dict[str, dict[str, str | None]] = {}
    for entry in entries:
        legacy = str(entry.get("legacy_analysis_id") or "").strip()
        if not legacy:
            raise LegacyImportError("Every analysis_map entry needs legacy_analysis_id.")
        if legacy in result:
            raise LegacyImportError(f"analysis_map names legacy analysis {legacy!r} twice.")
        new_id = entry.get("analysis_id")
        new_path = entry.get("analysis_path")
        result[legacy] = {
            "analysis_id": str(new_id) if new_id not in (None, "") else None,
            "analysis_path": str(new_path) if new_path not in (None, "") else None,
        }
    return result


def _legacy_rows(database: Path) -> list[dict[str, Any]]:
    if not database.is_file():
        raise LegacyImportError("The legacy archive has no history.sqlite3.")
    with tempfile.TemporaryDirectory(prefix="coach-legacy-") as scratch:
        copy = Path(scratch) / "history.sqlite3"
        log = database.with_name(database.name + "-wal")
        if log.is_file():  # the log first: a checkpoint between copies moves only pages it holds
            shutil.copyfile(log, copy.with_name(copy.name + "-wal"))
        shutil.copyfile(database, copy)
        connection = sqlite3.connect(copy)
        try:
            connection.row_factory = sqlite3.Row
            present = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tuning_experiments)").fetchall()
            }
            if not present:
                return []
            selected = ", ".join(name if name in present else f"NULL AS {name}" for name in _ROW_COLUMNS)
            rows = connection.execute(
                f"SELECT {selected} FROM tuning_experiments ORDER BY created_at, experiment_id"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()


def _legacy_record_path(legacy_root: Path, row: Mapping[str, Any]) -> Path:
    """The record's place in the copied archive (its stored path names the old home)."""

    return (
        legacy_root
        / "tuning"
        / "experiments"
        / safe_slug(row.get("season_key"))
        / safe_slug(row.get("car_key"))
        / safe_slug(row.get("track_key"))
        / f"{safe_slug(row.get('experiment_id'))}.json"
    )


def _json_or_none(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _payload(legacy_root: Path, row: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    path = _legacy_record_path(legacy_root, row)
    if path.is_file() and path.stat().st_size <= MAX_EXPERIMENT_BYTES:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if isinstance(value, dict) and value.get("experiment_id") == row.get("experiment_id"):
            return value, "record"
    # The index row alone: enough for tuning history, without the evidence.
    return {
        "schema_version": 1,
        "experiment_id": row.get("experiment_id"),
        "created_at": row.get("created_at"),
        "analysis_id": row.get("analysis_id"),
        "package_id": row.get("package_id"),
        "context": {field: row.get(field) for field in ("season_key", "car_key", "track_key", "setup_type")},
        "setup": {"fingerprint": row.get("setup_fingerprint")},
        "symptoms": _json_or_none(row.get("symptoms_json")) or [],
        "recommendation": _json_or_none(row.get("recommendation_json")) or {},
        "feedback": _json_or_none(row.get("feedback_json")),
        "outcome": row.get("outcome"),
    }, "index-row"


def _rewrite(
    payload: dict[str, Any], mapping: Mapping[str, Mapping[str, str | None]]
) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []
    for id_field, path_field, where in REFERENCE_FIELDS:
        holder = payload if where == "experiment" else payload.get("feedback")
        if not isinstance(holder, dict) or (id_field not in holder and path_field not in holder):
            continue
        legacy_id = holder.get(id_field)
        legacy_path = holder.get(path_field)
        if legacy_id in (None, "") and legacy_path in (None, ""):
            continue
        target = mapping.get(str(legacy_id)) if legacy_id not in (None, "") else None
        if target is not None and target.get("analysis_id"):
            holder[id_field] = target["analysis_id"]
            if path_field in holder or target.get("analysis_path"):
                holder[path_field] = target.get("analysis_path")
            continue
        holder[id_field] = None
        if path_field in holder:
            holder[path_field] = None
        unresolved.append(
            {"field": f"{where}.{id_field}", "legacy_analysis_id": legacy_id, "legacy_analysis_path": legacy_path}
        )
    return unresolved


def import_legacy_tuning_experiments_workflow(
    *,
    legacy_archive_root: str | os.PathLike[str],
    analysis_map: Sequence[Mapping[str, Any]],
    archive_root: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record every legacy tuning experiment in the target archive, references rewritten."""

    legacy_root = Path(legacy_archive_root).resolve(strict=True)
    mapping = _analysis_map(analysis_map)
    store = ArchiveStore(archive_root)
    if legacy_root == store.root.resolve():
        raise LegacyImportError("The legacy archive and the target archive are the same directory.")
    rows = _legacy_rows(legacy_root / "history.sqlite3")
    imported_at = utc_now()
    results: list[dict[str, Any]] = []
    for row in rows:
        payload, source = _payload(legacy_root, row)
        unresolved = _rewrite(payload, mapping)
        payload["legacy_import"] = {
            "version": LEGACY_IMPORT_VERSION,
            "imported_at": imported_at,
            "source": source,
            "legacy_experiment_path": row.get("experiment_path"),
            "unresolved": unresolved,
        }
        item: dict[str, Any] = {
            "experiment_id": row.get("experiment_id"),
            "source": source,
            "analysis_id": payload.get("analysis_id"),
            "status": "unresolved" if unresolved else "resolved",
            "unresolved": unresolved,
        }
        if not dry_run:
            saved = store.record_tuning_experiment(payload, updated_at=row.get("updated_at") or None)
            item["path"] = saved["path"]
        results.append(item)
    return {
        "ok": True,
        "contract": "legacy-tuning-import-v1",
        "dry_run": bool(dry_run),
        "archive_root": str(store.root),
        "experiment_count": len(results),
        "resolved_count": sum(1 for item in results if item["status"] == "resolved"),
        "unresolved_count": sum(1 for item in results if item["status"] == "unresolved"),
        "experiments": results,
    }


__all__ = [
    "LEGACY_IMPORT_VERSION",
    "LegacyImportError",
    "import_legacy_tuning_experiments_workflow",
]
