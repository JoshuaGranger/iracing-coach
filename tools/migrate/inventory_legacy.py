"""Inventory a copied legacy iRacing Coach data root for the D12 migration (stdlib only).

    python inventory_legacy.py <copied Coach root> --out <file outside the repository> [--hash-raw]

Reads a copy of `Documents\\iRacing Coach` (made on the sim by
`tools/rig/Export-LegacyCoachData.ps1`, or any other copy) and writes one JSON
document: the stores that hold user-authored data (notes, saved Tests, plans,
tuning drafts, turn annotations, tuning experiments), every SQLite database with
its tables, columns and row counts, JSON files with their top-level keys, and
the archived raw recordings with sizes and, with `--hash-raw`, SHA-256.

Never reads `data/auth/` or anything named like a credential. Never prints a
setting's value: only key names. SQLite files are copied to a temporary folder
(with their `-wal`/`-shm`) and opened there read-only, so the input is never
opened for writing. The output names private files, so it must live outside
the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

FORMAT = "iracing-coach-legacy-inventory 1"

# Folders under the Coach root the old app treats as durable (DurableArchive.cs:124-147),
# grouped by what D12 does with them.
USER_AUTHORED = (
    "settings.json",
    "portable-settings",
    "user-library",
    "data/history.sqlite3",
    "data/tuning",
    "data/tuning-experiments",
    "data/activity-history",
    "data/test-artifacts",
    "data/strategy-history",
    "data/setup-history",
    "data/target-laps",
)
RAW_RECORDINGS = "data/telemetry-traces/raw"
REDERIVED = (
    "data/reports",
    "data/race-index",
    "data/analysis-cache",
    "data/season-cache",
    "data/garage61",
    "data/track-geometry",
    "data/tire-models",
    "data/driver-models",
    "data/ai-coaching",
    "data/telemetry-events",
    "data/telemetry-profiles",
    "data/ui-analysis-cache",
    "data/archive",
)
NEVER_READ = ("data/auth",)
CREDENTIAL_MARKERS = ("credential", "auth.json", "token", "secret", ".key", ".pfx", ".p12")
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")


def is_private_name(relative: str) -> bool:
    lowered = relative.lower()
    if any(lowered == item or lowered.startswith(item + "/") for item in NEVER_READ):
        return True
    return any(marker in Path(lowered).name for marker in CREDENTIAL_MARKERS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def json_shape(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {"readable": False, "error": type(error).__name__}
    if isinstance(value, dict):
        return {"readable": True, "kind": "object", "keys": sorted(value)}
    if isinstance(value, list):
        first = value[0] if value else None
        keys = sorted(first) if isinstance(first, dict) else None
        return {"readable": True, "kind": "array", "length": len(value), "item_keys": keys}
    return {"readable": True, "kind": type(value).__name__}


def sqlite_shape(path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="coach-inventory-") as scratch:
        copy = Path(scratch) / path.name
        shutil.copy2(path, copy)
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, copy.with_name(copy.name + suffix))
        connection = sqlite3.connect(copy)
        try:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            tables = []
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")]
                rows = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                tables.append({"table": name, "rows": rows, "columns": columns})
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
    return {"user_version": user_version, "tables": tables}


def describe(root: Path, path: Path, hash_files: bool) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    info = path.stat()
    entry: dict[str, Any] = {
        "path": relative,
        "bytes": info.st_size,
        "modified_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(info.st_mtime)),
    }
    if hash_files:
        entry["sha256"] = sha256_file(path)
    lowered = path.name.lower()
    if lowered.endswith(".json"):
        entry["json"] = json_shape(path)
    elif lowered.endswith(SQLITE_SUFFIXES):
        try:
            entry["sqlite"] = sqlite_shape(path)
        except sqlite3.DatabaseError as error:
            entry["sqlite"] = {"error": str(error)}
    return entry


def files_under(root: Path, relative: str) -> list[Path]:
    target = root / relative
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    return sorted(
        path for path in target.rglob("*")
        if path.is_file() and not is_private_name(path.relative_to(root).as_posix())
    )


def summarize(root: Path, relative: str) -> dict[str, Any]:
    paths = files_under(root, relative)
    return {"path": relative, "present": (root / relative).exists(), "files": len(paths),
            "bytes": sum(path.stat().st_size for path in paths)}


def inventory(root: Path, hash_raw: bool) -> dict[str, Any]:
    user = []
    for relative in USER_AUTHORED:
        paths = files_under(root, relative)
        user.append({
            "path": relative,
            "present": (root / relative).exists(),
            "files": [describe(root, path, hash_files=True) for path in paths],
        })
    raw = [describe(root, path, hash_files=hash_raw) for path in files_under(root, RAW_RECORDINGS)]
    known = set(USER_AUTHORED) | {RAW_RECORDINGS} | set(REDERIVED) | set(NEVER_READ)
    unclassified = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or is_private_name(relative):
            continue
        if any(relative == item or relative.startswith(item + "/") for item in known):
            continue
        unclassified.append({"path": relative, "bytes": path.stat().st_size})
    return {
        "format": FORMAT,
        "root": str(root),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "not_read": list(NEVER_READ),
        "user_authored": user,
        "raw_recordings": {
            "path": RAW_RECORDINGS,
            "files": len(raw),
            "bytes": sum(item["bytes"] for item in raw),
            "hashed": hash_raw,
            "items": raw,
        },
        "rederived": [summarize(root, relative) for relative in REDERIVED],
        "unclassified": unclassified,
    }


def inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="copied Coach data root")
    parser.add_argument("--out", type=Path, required=True, help="output JSON (outside the repository)")
    parser.add_argument("--hash-raw", action="store_true", help="SHA-256 every archived raw recording")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out = args.out.resolve()
    repository = Path(__file__).resolve().parents[2]
    if not root.is_dir():
        parser.error(f"{root} is not a folder")
    if inside(out, repository):
        parser.error("the inventory names private files; write it outside the repository")
    if inside(out, root):
        parser.error("write the inventory outside the copied root")

    report = inventory(root, args.hash_raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(out)
    user_files = sum(len(store["files"]) for store in report["user_authored"])
    print(f"{out}: {user_files} user-authored files, {report['raw_recordings']['files']} raw recordings, "
          f"{len(report['unclassified'])} unclassified files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
