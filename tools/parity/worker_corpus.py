#!/usr/bin/env python3
"""Synthetic recordings for the worker-interface captures (worker-interface.md section 8).

Each case is one or more small, valid Race IBTs written by the same builder as
``test-data/ibt/synthetic-race.ibt``. They are generated, never committed: the
builder is deterministic, so every platform writes the same bytes.

``synthetic``   the committed fixture itself.
``reconnect``   one race recorded as two files (a reconnect): the same
                SubSessionID and session number, the second file's samples
                overlapping the last 20 of the first.
``local-file``  a recording with no server ids and no usable start time, so
                discovery keys its group by the file's path and the server
                selects it by path (``local-file:<sha256>``).

Every file gets a fixed mtime well in the past, so the 2 s quiet rule passes
and ``modified_ns`` is the same everywhere.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

REPOSITORY = Path(__file__).resolve().parents[2]
SYNTHETIC_RACE = REPOSITORY / "test-data" / "ibt" / "synthetic-race.ibt"
#: 2026-05-28T21:46:40Z, the synthetic race's own disk start time.
FIXED_MTIME = 1_780_000_000
CASES = ("synthetic", "reconnect", "local-file")


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_fixtures_for_worker_corpus", REPOSITORY / "tools" / "generate_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixed_time(path: Path) -> None:
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def build_case(case: str, directory: Path) -> list[Path]:
    """Write the case's recordings into ``directory``; return them in recording order."""

    directory.mkdir(parents=True, exist_ok=True)
    if case == "synthetic":
        target = directory / SYNTHETIC_RACE.name
        shutil.copyfile(SYNTHETIC_RACE, target)
        _fixed_time(target)
        return [target]
    build = _generator()._build_tiny_ibt
    if case == "reconnect":
        parts = [
            (directory / "synthetic reconnect 2026-05-28 21-46-40.ibt", 0, 80, 1_780_000_000),
            (directory / "synthetic reconnect 2026-05-28 21-46-41.ibt", 60, 80, 1_780_000_001),
        ]
        for path, first, count, epoch in parts:
            build(
                path,
                subsession_id=8101,
                session_id=7101,
                disk_start_epoch=epoch,
                first_record=first,
                record_count=count,
                race_records=140,
                disk_session_times=(first / 60.0, (first + count - 1) / 60.0),
            )
            _fixed_time(path)
        return [path for path, *_ in parts]
    if case == "local-file":
        target = directory / "synthetic local 2026-05-28.ibt"
        build(target, subsession_id=0, session_id=0, disk_start_epoch=0)
        _fixed_time(target)
        return [target]
    raise ValueError(f"Unknown worker corpus case {case!r}; expected one of {', '.join(CASES)}.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
