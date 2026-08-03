#!/usr/bin/env python3
"""Generate or verify the clean companion-app build-input manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = HANDOFF_ROOT.parent
MANIFEST_PATH = HANDOFF_ROOT / "manifest.json"
CHECKSUM_PATH = HANDOFF_ROOT / "SHA256SUMS.txt"
ROOT_FILES = (WORKSPACE_ROOT / "README.md", WORKSPACE_ROOT / "AGENTS.md")
INCLUDED_DIRECTORIES = (WORKSPACE_ROOT / "iracing-coach", HANDOFF_ROOT)
GENERATED_RELATIVE_PATHS = {
    MANIFEST_PATH.relative_to(WORKSPACE_ROOT).as_posix(),
    CHECKSUM_PATH.relative_to(WORKSPACE_ROOT).as_posix(),
}
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".validation-deps",
    "__pycache__",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _relative(path: Path) -> str:
    return path.relative_to(WORKSPACE_ROOT).as_posix()


def _included(path: Path) -> bool:
    relative = _relative(path)
    if relative in GENERATED_RELATIVE_PATHS:
        return False
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.relative_to(WORKSPACE_ROOT).parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def iter_build_inputs() -> Iterator[Path]:
    seen: set[Path] = set()
    for path in ROOT_FILES:
        if _included(path):
            resolved = path.resolve()
            seen.add(resolved)
            yield path
    for directory in INCLUDED_DIRECTORIES:
        for path in sorted(directory.rglob("*"), key=lambda item: _relative(item).lower()):
            resolved = path.resolve()
            if resolved not in seen and _included(path):
                seen.add(resolved)
                yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries() -> list[dict[str, object]]:
    return [
        {
            "path": _relative(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in iter_build_inputs()
    ]


def generate() -> dict[str, object]:
    entries = _entries()
    manifest: dict[str, object] = {
        "manifest_version": 1,
        "bundle": "iracing-coach-companion-app-build-inputs",
        "scope": "clean build inputs; private data and credentials excluded",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "excluded": [
            "data/ (optional private regression corpus)",
            ".git/",
            ".validation-deps/",
            "data/test-artifacts/",
            "__pycache__/ and compiled Python files",
            "all credentials and browser/Codex authentication state",
        ],
        "files": entries,
        "totals": {
            "file_count": len(entries),
            "bytes": sum(int(item["bytes"]) for item in entries),
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    CHECKSUM_PATH.write_text(
        "".join(f'{item["sha256"]}  {item["path"]}\n' for item in entries),
        encoding="utf-8",
    )
    return manifest


def verify() -> dict[str, object]:
    if not MANIFEST_PATH.is_file() or not CHECKSUM_PATH.is_file():
        raise FileNotFoundError("Generate manifest.json and SHA256SUMS.txt first")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    committed = manifest.get("files")
    if not isinstance(committed, list):
        raise ValueError("manifest.json files must be an array")
    expected = _entries()
    if committed != expected:
        committed_by_path = {
            str(item.get("path")): item for item in committed if isinstance(item, dict)
        }
        expected_by_path = {str(item["path"]): item for item in expected}
        missing = sorted(set(expected_by_path) - set(committed_by_path))
        extra = sorted(set(committed_by_path) - set(expected_by_path))
        changed = sorted(
            path
            for path in set(expected_by_path) & set(committed_by_path)
            if expected_by_path[path] != committed_by_path[path]
        )
        raise ValueError(
            f"Manifest is stale; missing={missing}, extra={extra}, changed={changed}"
        )
    expected_checksums = "".join(
        f'{item["sha256"]}  {item["path"]}\n' for item in expected
    )
    if CHECKSUM_PATH.read_text(encoding="utf-8") != expected_checksums:
        raise ValueError("SHA256SUMS.txt is stale")
    totals = manifest.get("totals") or {}
    if totals.get("file_count") != len(expected) or totals.get("bytes") != sum(
        int(item["bytes"]) for item in expected
    ):
        raise ValueError("Manifest totals are stale")
    return {
        "ok": True,
        "file_count": len(expected),
        "bytes": sum(int(item["bytes"]) for item in expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = verify() if args.check else generate()
    if args.check:
        print(json.dumps(result, indent=2))
    else:
        totals = result["totals"]
        print(
            json.dumps(
                {
                    "ok": True,
                    "manifest": str(MANIFEST_PATH),
                    "checksums": str(CHECKSUM_PATH),
                    **totals,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                indent=2,
            )
        )
        raise SystemExit(1)
