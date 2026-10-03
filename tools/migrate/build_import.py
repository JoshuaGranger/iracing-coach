"""Build the D12 ImportBundle from a copied legacy iRacing Coach data root (stdlib only).

    python build_import.py <copied Coach root> --customer-id <id> --machine <name>
        --out <file outside the repository> [--recordings <recording inventory.json>]

Reads a copy of `Documents\\iRacing Coach` (made by `tools/rig/Export-LegacyCoachData.ps1`)
and writes one `ImportBundle` (`contracts/api.openapi.yaml`) for `POST /api/v1/admin/import`:

- `live_layouts`, `trace_layouts`, `prefs` from `settings.json`; legacy unit labels become
  `live-channels.json` unit ids, and a tile whose metric is not in the tile catalog is dropped
  (the whole settings file stays in `legacy_items`).
- `tuning_drafts` from `portable-settings/tuning-drafts/`, mapped to `TuningDraftBody` fields;
  every other legacy draft field is kept verbatim under `legacy`.
- `turn_annotations` from `portable-settings/tuning-turn-annotations/`.
- `legacy_analyses`: every analysis in `data/history.sqlite3` `sessions`, the tuning experiments
  and the drafts, with its recording hashes (raw-archive manifests, the draft's
  `sourceSha256`, or `--recordings` from `tools/rig/Get-SimRecordingInventory.ps1 -Hash`).
- `legacy_archive`: `data/history.sqlite3` and `data/tuning/`, byte for byte; a database with a
  write-ahead log is first consolidated into one file through SQLite's backup.
- `legacy_items`: parked features' files (saved Tests, plans, activity, strategy, Ask threads)
  and `settings.json` without credential-like keys.

The copy is read only; SQLite files are opened from a temporary copy. The bundle names private
data, so it must be written outside the repository. Output is deterministic: the same copy
gives the same bytes, so the server's bundle-hash idempotency holds across reruns.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from inventory_legacy import inside, is_private_name

REPOSITORY = Path(__file__).resolve().parents[2]
LIVE_CHANNELS = REPOSITORY / "docs" / "08-rewrite" / "contracts" / "live-channels.json"
MAX_LIST = 5000

# C# enums are written as integers (JsonSerializerDefaults.Web, no string converter).
DISPLAY_STYLES = ("number", "gauge", "bar", "trend", "status")
TREND_WINDOWS = ("s15", "s30", "s60", "one_lap", "three_laps")
TREND_NAMES = {"seconds15": "s15", "seconds30": "s30", "seconds60": "s60",
               "onelap": "one_lap", "threelaps": "three_laps"}
ACCENTS = ("default", "blue", "green", "amber", "coral", "violet")

# (folder under the Coach root, legacy_items kind); db-schema.sql legacy_items.kind.
LEGACY_ITEM_FOLDERS = (
    ("data/trials", "saved_test"),
    ("data/event-plans", "plan"),
    ("data/activity-history", "activity"),
    ("data/strategy-history", "other"),
    ("data/ai-coaching", "other"),
    ("data/tuning-experiments", "other"),
    ("data/setup-history", "other"),
    ("data/target-laps", "other"),
    ("user-library", "other"),
)
SECRET_KEY_MARKERS = ("apikey", "api_key", "token", "secret", "password", "credential")

# C# ProgressiveTuningFeedback (camelCase) -> TuningFeedbackItem; other keys keep their names.
FEEDBACK_FIELDS = {
    "feedbackId": "feedback_id", "cornerId": "corner_id", "cornerLabel": "corner_label",
    "startPct": "start_pct", "apexPct": "apex_pct", "endPct": "end_pct", "runPhase": "run_phase",
    "cornerPhases": "corner_phases", "symptomId": "symptom_id", "driverConfidence": "driver_confidence",
}
DRAFT_MAPPED = ("goal", "generalNote", "representativeRunIds", "feedback")


class BundleError(ValueError):
    """The copied root cannot be converted as given."""


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def user_files(folder: Path, root: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (path for path in folder.rglob("*")
         if path.is_file() and not is_private_name(path.relative_to(root).as_posix())),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def optional_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def positive_int(value: Any) -> int | None:
    number = optional_int(value)
    return number if number is not None and number > 0 else None


def clamp(value: Any, low: int, high: int, default: int) -> int:
    number = value if isinstance(value, int) and not isinstance(value, bool) else default
    return max(low, min(high, number))


def enum_value(value: Any, names: tuple[str, ...], aliases: dict[str, str] | None = None) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return names[value] if 0 <= value < len(names) else None
    if isinstance(value, str):
        key = value.strip().lower()
        key = (aliases or {}).get(key, key)
        return key if key in names else None
    return None


def without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_secrets(item) for key, item in value.items()
                if not any(marker in key.lower() for marker in SECRET_KEY_MARKERS)}
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


# ---- settings.json ----

def tile_catalog(path: Path = LIVE_CHANNELS) -> tuple[dict[str, list[str]], dict[str, str]]:
    table = read_json(path)
    units = {tile["id"]: list(tile.get("units") or []) for tile in table["tiles"]}
    label_to_id = {label: unit for unit, label in (table.get("unit_labels") or {}).items()}
    return units, label_to_id


def unit_id(legacy: Any, allowed: list[str], label_to_id: dict[str, str]) -> str | None:
    """A legacy label (`°C`, `laps`) or id becomes the tile's unit id; anything else is the default."""

    label = text(legacy)
    unit = label_to_id.get(label, label)
    return unit if unit in allowed else None


def live_layouts(settings: dict[str, Any], catalog: tuple[dict[str, list[str]], dict[str, str]],
                 dropped: list[dict[str, str]]) -> list[dict[str, Any]]:
    units, label_to_id = catalog
    monitor = settings.get("liveMonitor") or {}
    layouts = []
    for layout in monitor.get("userLayouts") or []:
        if not isinstance(layout, dict):
            continue
        name = text(layout.get("name"))[:80] or "Imported"
        tiles = []
        for tile in layout.get("tiles") or []:
            metric = text(tile.get("metricId")) if isinstance(tile, dict) else ""
            if metric not in units:
                dropped.append({"layout": name, "metric": metric, "reason": "not in the tile catalog"})
                continue
            precision = tile.get("precision")
            tiles.append({
                "tile_id": text(tile.get("id"))[:64] or f"tile-{len(tiles) + 1}",
                "metric": metric,
                "row": clamp(tile.get("row"), 0, 7, 0),
                "column": clamp(tile.get("column"), 0, 7, 0),
                "row_span": clamp(tile.get("rowSpan"), 1, 8, 1),
                "column_span": clamp(tile.get("columnSpan"), 1, 8, 1),
                "style": enum_value(tile.get("displayStyle"), DISPLAY_STYLES) or "number",
                "unit": unit_id(tile.get("unit"), units[metric], label_to_id),
                "precision": clamp(precision, 0, 3, 1) if precision is not None else None,
                "trend_window": enum_value(tile.get("trendDuration"), TREND_WINDOWS, TREND_NAMES),
                "accent": enum_value(tile.get("accent"), ACCENTS),
                "highlight_abs": tile.get("highlightAbsIntervention") is True,
            })
        layouts.append({
            "name": name,
            "body": {
                "rows": clamp(layout.get("rows"), 1, 8, 2),
                "columns": clamp(layout.get("columns"), 1, 8, 3),
                "tiles": tiles[:64],
            },
        })
    return layouts[:64]


def trace_body(layout: Any) -> dict[str, Any]:
    rows = []
    for index, row in enumerate((layout or {}).get("rows") or []):
        if not isinstance(row, dict):
            continue
        signals = [signal for signal in (text(row.get("primarySignalId")), text(row.get("secondarySignalId")))
                   if signal]
        if signals:
            rows.append({"row_id": text(row.get("id")) or f"row-{index + 1}", "label": None, "signals": signals})
    return {"rows": rows[:10]}


def trace_layouts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    named = settings.get("raceAnalysisTraceLayouts") or {}
    layouts = []
    for layout in named.get("userLayouts") or []:
        if isinstance(layout, dict):
            body = trace_body(layout.get("layout"))
            if body["rows"]:
                layouts.append({"name": text(layout.get("name"))[:80] or "Imported", "body": body})
    # The pre-named single layout, when the old app never converted it (AnalysisTraceLayoutSet).
    if not named.get("legacyLayoutImportCompleted"):
        body = trace_body(settings.get("raceAnalysisTraces"))
        if body["rows"]:
            layouts.append({"name": "Saved traces", "body": body})
    return layouts[:64]


def prefs(settings: dict[str, Any]) -> dict[str, Any] | None:
    if not settings:
        return None
    monitor = settings.get("liveMonitor") or {}
    theme = text(settings.get("themeColor"))[:16] or None
    reduced = settings.get("useReducedMotion")
    safe = monitor.get("safeGlanceEnabled")
    return {
        "theme_color": theme,
        "reduced_motion": reduced if isinstance(reduced, bool) else None,
        "safe_glance": safe if isinstance(safe, bool) else None,
    }


# ---- tuning drafts and turn annotations ----

def feedback_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    mapped: dict[str, Any] = {}
    for key, value in item.items():
        if value is not None:  # a pct is omitted rather than sent as null (TuningFeedbackItem)
            mapped[FEEDBACK_FIELDS.get(key, key)] = value
    return mapped


def draft_body(draft: dict[str, Any]) -> dict[str, Any]:
    """C# ProgressiveTuningDraft (schemaVersion 2) as TuningDraftBody."""

    target = draft.get("openSetupTarget") or {}
    baseline = target.get("baseline") if isinstance(target, dict) else None
    goal = text(draft.get("goal")) or None
    runs = [str(run).strip()[:80] for run in draft.get("representativeRunIds") or [] if str(run).strip()]
    feedback = [mapped for mapped in map(feedback_item, draft.get("feedback") or []) if mapped]
    return {
        "goal": goal,
        "general_note": note[:4000] if isinstance(note := draft.get("generalNote"), str) else "",
        "representative_run_ids": runs[:3],
        "open_target_analysis_id": text((baseline or {}).get("analysisId")) or None,
        "feedback": feedback[:100],
        "legacy": {key: value for key, value in draft.items() if key not in DRAFT_MAPPED},
    }


def identity_reference(identity: Any) -> dict[str, Any] | None:
    if not isinstance(identity, dict) or not text(identity.get("analysisId")):
        return None
    return {
        "legacy_analysis_id": text(identity.get("analysisId")),
        "recording_sha256": [text(value).lower() for value in identity.get("sourceSha256") or [] if text(value)],
        "subsession_id": positive_int(identity.get("subsessionId")),
        "sim_session_num": None,
    }


def tuning_drafts(root: Path, references: list[dict[str, Any]],
                  skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    drafts = []
    for path in user_files(root / "portable-settings" / "tuning-drafts", root):
        relative = path.relative_to(root).as_posix()
        try:
            draft = read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            skipped.append({"path": relative, "reason": f"unreadable: {type(error).__name__}"})
            continue
        if not isinstance(draft, dict):
            skipped.append({"path": relative, "reason": "not a JSON object"})
            continue
        drafts.append({"identity_key": path.stem.lower(), "body": draft_body(draft)})
        target = draft.get("openSetupTarget") or {}
        for identity in (draft.get("representativeSession"), target.get("baseline") if isinstance(target, dict) else None):
            reference = identity_reference(identity)
            if reference:
                references.append(reference)
    return drafts


def turn_annotations(root: Path, skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    annotations = []
    for path in user_files(root / "portable-settings" / "tuning-turn-annotations", root):
        relative = path.relative_to(root).as_posix()
        try:
            data = read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            skipped.append({"path": relative, "reason": f"unreadable: {type(error).__name__}"})
            continue
        track = text(data.get("trackConfigurationKey")) if isinstance(data, dict) else ""
        if not track:
            skipped.append({"path": relative, "reason": "no trackConfigurationKey"})
            continue
        corners = []
        for correction in data.get("corrections") or []:
            if not isinstance(correction, dict):
                continue
            corners.append({
                "corner_id": text(correction.get("cornerId")),
                "label": text(correction.get("label")),
                "start_pct": correction.get("startPct"),
                "apex_pct": correction.get("apexPct"),
                "end_pct": correction.get("endPct"),
                "note": correction.get("note") or "",
                "geometry_hash": text(correction.get("geometryHash")),
                "map_identity": text(correction.get("mapIdentity")),
                "verified_at": correction.get("verifiedAt"),
            })
        annotations.append({
            "track_config_key": track,
            "map_identity": text(data.get("mapIdentity")),
            "body": {
                "corners": corners[:100],
                "legacy": {key: value for key, value in data.items() if key != "corrections"},
            },
        })
    return annotations


# ---- history.sqlite3, raw manifests, legacy analyses ----

def open_copy(database: Path, scratch: Path) -> sqlite3.Connection:
    copy = scratch / database.name
    log = database.with_name(database.name + "-wal")
    if log.is_file():
        shutil.copyfile(log, copy.with_name(copy.name + "-wal"))
    shutil.copyfile(database, copy)
    connection = sqlite3.connect(copy)
    connection.row_factory = sqlite3.Row
    return connection


def table_rows(connection: sqlite3.Connection, table: str, columns: Iterable[str]) -> list[dict[str, Any]]:
    present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if not present:
        return []
    selected = ", ".join(name if name in present else f"NULL AS {name}" for name in columns)
    return [dict(row) for row in connection.execute(f"SELECT {selected} FROM {table}")]


def raw_manifest_hashes(root: Path) -> dict[str, str]:
    """Source path (case-folded, backslashes) -> recording SHA-256, from the raw-archive manifests."""

    hashes: dict[str, str] = {}
    raw = root / "data" / "telemetry-traces" / "raw"
    for path in sorted(raw.glob("*/manifest.json")) if raw.is_dir() else []:
        try:
            manifest = read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        sha = text(manifest.get("sha256")).lower() or path.parent.name.lower()
        sources = [manifest.get("original_path")]
        sources += [item.get("source_path") for item in manifest.get("source_discoveries") or []
                    if isinstance(item, dict)]
        for source in sources:
            if text(source):
                hashes[text(source).replace("/", "\\").casefold()] = sha
    return hashes


def recording_name_hashes(inventory: Path | None) -> dict[str, str]:
    """Recording file name (case-folded) -> SHA-256, from Get-SimRecordingInventory.ps1 -Hash."""

    if inventory is None:
        return {}
    report = read_json(inventory)
    hashes = {}
    for entry in report.get("files") or []:
        if entry.get("kind") in ("telemetry", "coach_raw_archive") and text(entry.get("sha256")):
            hashes[PurePosixPath(text(entry.get("path"))).name.casefold()] = text(entry["sha256"]).lower()
    return hashes


def experiment_references(root: Path) -> list[dict[str, Any]]:
    references = []
    for path in user_files(root / "data" / "tuning" / "experiments", root):
        try:
            record = read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        feedback = record.get("feedback") if isinstance(record.get("feedback"), dict) else {}
        for value in (record.get("analysis_id"), record.get("open_target_analysis_id"),
                      feedback.get("result_analysis_id")):
            if text(value):
                references.append({"legacy_analysis_id": text(value), "recording_sha256": [],
                                   "subsession_id": None, "sim_session_num": None})
    return references


def legacy_analyses(root: Path, recordings: Path | None,
                    draft_references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = raw_manifest_hashes(root)
    by_name = recording_name_hashes(recordings)
    references: list[dict[str, Any]] = []
    database = root / "data" / "history.sqlite3"
    if database.is_file():
        with tempfile.TemporaryDirectory(prefix="coach-import-") as scratch:
            connection = open_copy(database, Path(scratch))
            try:
                sessions = table_rows(connection, "sessions",
                                      ("analysis_id", "subsession_id", "sim_session_num", "source_path"))
                experiments = table_rows(connection, "tuning_experiments", ("analysis_id",))
            finally:
                connection.close()
        for row in sessions:
            source = text(row["source_path"]).replace("/", "\\")
            sha = by_path.get(source.casefold()) or by_name.get(PurePosixPath(source.replace("\\", "/")).name.casefold())
            references.append({
                "legacy_analysis_id": text(row["analysis_id"]),
                "recording_sha256": [sha] if sha else [],
                "subsession_id": positive_int(row["subsession_id"]),
                "sim_session_num": optional_int(row["sim_session_num"]),
            })
        references += [{"legacy_analysis_id": text(row["analysis_id"]), "recording_sha256": [],
                        "subsession_id": None, "sim_session_num": None} for row in experiments]
    references += experiment_references(root) + draft_references

    merged: dict[str, dict[str, Any]] = {}
    for reference in references:
        key = reference["legacy_analysis_id"]
        if not key:
            continue
        entry = merged.setdefault(key, {"legacy_analysis_id": key[:128], "recording_sha256": [],
                                        "subsession_id": None, "sim_session_num": None})
        entry["recording_sha256"] = sorted(set(entry["recording_sha256"]) | set(reference["recording_sha256"]))
        for field in ("subsession_id", "sim_session_num"):
            if entry[field] is None:
                entry[field] = reference[field]
    if len(merged) > MAX_LIST:
        raise BundleError(f"more than {MAX_LIST} legacy analyses")
    return [merged[key] for key in sorted(merged)]


def sqlite_bytes(database: Path) -> tuple[bytes, bool]:
    """The database file as stored; with a write-ahead log, one consolidated file (backup API)."""

    log = database.with_name(database.name + "-wal")
    if not log.is_file() or log.stat().st_size == 0:
        return database.read_bytes(), False
    with tempfile.TemporaryDirectory(prefix="coach-import-") as scratch:
        source = open_copy(database, Path(scratch))
        target_path = Path(scratch) / "consolidated.sqlite3"
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
            source.close()
        return target_path.read_bytes(), True


def archive_file(relative: str, content: bytes) -> dict[str, str]:
    return {
        "relative_path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def legacy_archive(root: Path, notes: dict[str, Any]) -> dict[str, Any] | None:
    data = root / "data"
    files = []
    database = data / "history.sqlite3"
    if database.is_file():
        content, consolidated = sqlite_bytes(database)
        notes["history_consolidated_from_wal"] = consolidated
        files.append(archive_file("history.sqlite3", content))
    for path in user_files(data / "tuning", root):
        relative = path.relative_to(data).as_posix()
        files.append(archive_file(relative, path.read_bytes()))
    if not files:
        return None
    if len(files) > MAX_LIST:
        raise BundleError(f"more than {MAX_LIST} legacy archive files")
    return {"files": files}


# ---- parked data ----

def legacy_items(root: Path, settings: dict[str, Any], skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    items = []
    if settings:
        items.append({"kind": "setting", "source_path": "settings.json", "body": without_secrets(settings)})
    for folder, kind in LEGACY_ITEM_FOLDERS:
        for path in user_files(root / folder, root):
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() != ".json":
                skipped.append({"path": relative, "reason": "not JSON"})
                continue
            try:
                body = read_json(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                skipped.append({"path": relative, "reason": f"unreadable: {type(error).__name__}"})
                continue
            items.append({"kind": kind, "source_path": relative,
                          "body": without_secrets(body if isinstance(body, dict) else {"value": body})})
    return items


def build(root: Path, customer_id: int, machine: str, recordings: Path | None = None,
          catalog_path: Path = LIVE_CHANNELS) -> tuple[dict[str, Any], dict[str, Any]]:
    """The bundle and a summary (counts, dropped tiles, files not converted)."""

    settings_path = root / "settings.json"
    settings = read_json(settings_path) if settings_path.is_file() else {}
    if not isinstance(settings, dict):
        raise BundleError("settings.json is not a JSON object")
    dropped: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    notes: dict[str, Any] = {}
    draft_references: list[dict[str, Any]] = []

    drafts = tuning_drafts(root, draft_references, skipped)
    bundle = {
        "bundle_version": 1,
        "customer_id": customer_id,
        "source": {"machine": machine, "root": str(root), "app_version": None, "archive_id": None},
        "notes": [],
        "trace_layouts": trace_layouts(settings),
        "live_layouts": live_layouts(settings, tile_catalog(catalog_path), dropped),
        "prefs": prefs(settings),
        "tuning_drafts": drafts,
        "turn_annotations": turn_annotations(root, skipped),
        "legacy_analyses": legacy_analyses(root, recordings, draft_references),
        "legacy_archive": legacy_archive(root, notes),
        "legacy_items": legacy_items(root, settings, skipped),
    }
    if (root / "data" / "test-artifacts").is_dir():
        skipped.append({"path": "data/test-artifacts", "reason": "test output, not user-authored"})
    summary = {
        "counts": {key: len(value) for key, value in bundle.items() if isinstance(value, list)},
        "legacy_archive_files": len((bundle["legacy_archive"] or {}).get("files", [])),
        "analyses_with_recording_hash": sum(1 for entry in bundle["legacy_analyses"] if entry["recording_sha256"]),
        "dropped_tiles": dropped,
        "not_converted": skipped,
        **notes,
    }
    return bundle, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="copied Coach data root")
    parser.add_argument("--customer-id", type=int, required=True, help="iRacing customer id of the driver")
    parser.add_argument("--machine", required=True, help="machine the copy came from, e.g. DESKTOP-SIM")
    parser.add_argument("--out", type=Path, required=True, help="bundle JSON (outside the repository)")
    parser.add_argument("--recordings", type=Path, help="Get-SimRecordingInventory.ps1 -Hash output")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out = args.out.resolve()
    if not root.is_dir():
        parser.error(f"{root} is not a folder")
    if inside(out, REPOSITORY):
        parser.error("the bundle holds private data; write it outside the repository")
    if inside(out, root):
        parser.error("write the bundle outside the copied root")
    if args.customer_id <= 0:
        parser.error("--customer-id must be positive")

    try:
        bundle, summary = build(root, args.customer_id, args.machine, args.recordings)
    except BundleError as error:
        print(f"build_import: {error}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")
    temporary.replace(out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
