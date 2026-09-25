"""Remove older copies of saved analyses, and only when the driver confirms.

GAP-025 and PORT-007. Before reports were keyed by analysis_id every
re-analysis wrote a full new copy, and a later analyzer revision leaves its
predecessor's copies behind. ``ArchiveStore.report_storage_usage`` measures
those copies read-only. This module turns that measure into a plan the driver
reviews and, after an explicit confirmation, moves exactly the reviewed copies
that are still eligible into ``<archive>/removed-reports/<batch>/``. Nothing is
deleted here: emptying that folder is the driver's own, separate choice.

What is never moved, re-checked for every directory immediately before it
moves:

* a copy the history index names, a copy a saved driving test or tuning record
  names, or a copy the app has open (its open recording and test day);
* the latest analysis of each recording. A duplicate moves only while an
  identical analysis (same analysis_id) at least as new stays; a superseded
  copy moves only while the history index names exactly one newer analysis of
  the same session, made from the same recording, whose report exists;
* a directory written in the last few minutes, one holding another report or
  a link, and one whose files changed since the plan;
* anything outside the ``reports`` tree. Raw telemetry, setups and every other
  archive folder are never read for removal or touched.

Each directory moves by one same-volume rename, so it is moved whole or not at
all; Windows refuses the rename while any file inside is open, and that copy is
skipped as in use. There is no copy-and-delete fallback. The history index is
held for writing (``BEGIN IMMEDIATE``) while one directory is verified and
moved, so an analysis finishing in another backend process cannot start
naming it in between.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from storage import (
    ArchiveStore,
    _analysis_session_identity,
    _atomic_write_text,
    _extended_path,
    _history_index_rows,
    _json_text,
    _ordinary_path,
    _report_analysis_id,
    _report_directory_keys,
    _report_source_objects,
    _session_match_keys,
)


CONTRACT = "report-storage-reclaim-v1"
SCHEMA_VERSION = 1
RECOVERY_DIRECTORY = "removed-reports"
RECOVERY_MANIFEST = "removal.json"

#: A copy whose files changed this recently may belong to an analysis that is
#: still being saved or indexed; it is kept.
QUIET_PERIOD_S = 300.0
MAX_DIRECTORIES = 5000
MAX_KEEP_PATHS = 500
_LOCK_TIMEOUT_S = 10.0
_IN_USE_WINERRORS = (5, 32, 33)

KEPT_REASONS = (
    "history-index",
    "saved-test",
    "tuning",
    "open-in-app",
    "latest-analysis",
    "only-copy",
    "recent",
    "unverified",
    "deferred",
)
SKIP_REASONS = (
    "referenced",
    "in-use",
    "changed",
    "missing",
    "not-eligible",
    "index-unavailable",
    "move-failed",
)
_REFERENCE_REASONS = frozenset({"history-index", "saved-test", "tuning", "open-in-app"})
_USAGE_REASON = {
    "history-index": "history-index",
    "trials": "saved-test",
    "tuning": "tuning",
    "newest": "latest-analysis",
    "history-index-unreadable": "unverified",
}


class ReclaimRequestError(ValueError):
    """The confirmation does not describe a plan this backend produced."""


@dataclass(frozen=True)
class _CopyState:
    analysis_id: str | None
    size: int
    modified_ns: int
    newest_ns: int
    bytes: int
    holds_other_content: bool


@dataclass(frozen=True)
class _Candidate:
    directory: str
    path: str
    kind: str
    analysis_id: str
    bytes: int
    state: _CopyState
    anchor_path: str
    anchor_id: str
    session_keys: frozenset[str]


# ---------------------------------------------------------------- file state


def _is_link(entry_stat: os.stat_result) -> bool:
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(entry_stat.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _copy_state(path: str) -> _CopyState | None:
    """The report directory's identity and newest file, or None if it is not one.

    Links are never followed. A link or a second analysis.json anywhere below
    marks the directory as holding content that is not its own report.
    """

    directory = _extended_path(path)
    try:
        own = os.lstat(directory)
        analysis = os.lstat(os.path.join(directory, "analysis.json"))
    except OSError:
        return None
    if not stat.S_ISDIR(own.st_mode) or _is_link(own) or not stat.S_ISREG(analysis.st_mode) or _is_link(analysis):
        return None
    total = 0
    newest = analysis.st_mtime_ns
    foreign = False
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            foreign = True
            continue
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                foreign = True
                continue
            if _is_link(entry_stat):
                foreign = True
            elif stat.S_ISDIR(entry_stat.st_mode):
                newest = max(newest, entry_stat.st_mtime_ns)
                pending.append(entry.path)
            else:
                total += entry_stat.st_size
                newest = max(newest, entry_stat.st_mtime_ns)
                if current != directory and entry.name.casefold() == "analysis.json":
                    foreign = True
    return _CopyState(
        analysis_id=_report_analysis_id(os.path.join(directory, "analysis.json"), analysis.st_size),
        size=analysis.st_size,
        modified_ns=analysis.st_mtime_ns,
        newest_ns=newest,
        bytes=total,
        holds_other_content=foreign,
    )


def _relative_below(root: Path, path: str) -> str | None:
    """``path`` below ``root`` in its own spelling, or None unless strictly inside."""

    base = os.path.abspath(_ordinary_path(os.fspath(root)))
    target = os.path.abspath(_ordinary_path(os.fspath(path)))
    try:
        relative = os.path.relpath(target, base)
    except ValueError:
        return None
    parts = relative.split(os.sep)
    if relative in ("", os.curdir) or os.path.isabs(relative) or os.pardir in parts:
        return None
    if os.path.normcase(os.path.join(base, relative)) != os.path.normcase(target):
        return None
    return relative


def _inside_without_links(root: Path, path: str) -> bool:
    """Whether ``path`` is strictly below ``root`` with no link on the way down."""

    relative = _relative_below(root, path)
    if relative is None:
        return False
    current = os.path.abspath(_ordinary_path(os.fspath(root)))
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        try:
            if _is_link(os.lstat(_extended_path(current))):
                return False
        except OSError:
            return False
    try:
        # realpath may keep or drop the extended-length prefix depending on the
        # length, so both sides are compared in their ordinary spelling.
        real_root = os.path.normcase(_ordinary_path(os.path.realpath(_extended_path(root))))
        real_path = os.path.normcase(_ordinary_path(os.path.realpath(_extended_path(path))))
    except (OSError, ValueError):
        return False
    return real_path.startswith(real_root.rstrip(os.sep) + os.sep)


def _utc(value_ns: int | None) -> str | None:
    if value_ns is None:
        return None
    return datetime.fromtimestamp(value_ns / 1e9, timezone.utc).replace(microsecond=0).isoformat()


def _plan_id(directories: Iterable[str]) -> str:
    text = "\n".join(sorted(item.casefold() for item in directories))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------- recording identity


def _session_keys(sources: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    identities = {
        tuple(sorted(_analysis_session_identity({"source": source}).items())) for source in sources
    }
    if len(identities) != 1:
        return frozenset()
    identity = dict(next(iter(identities)))
    return frozenset(
        _session_match_keys(
            identity["session_group_id"], identity["subsession_id"], identity["session_phase"]
        )
    )


def _recording(source: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """What a report was made from: its IBT content hashes and its file names."""

    found: dict[str, frozenset[str]] = {}
    fingerprints = source.get("fingerprints")
    if isinstance(fingerprints, list) and fingerprints:
        hashes = {
            str(item.get("sha256") or "").strip().casefold() if isinstance(item, Mapping) else ""
            for item in fingerprints
        }
        if all(len(value) == 64 for value in hashes):
            found["sha256"] = frozenset(hashes)
    files = source.get("telemetry_files")
    if isinstance(files, list) and files:
        names = {
            os.path.basename(str(item).replace("\\", "/")).casefold() if isinstance(item, str) else ""
            for item in files
        }
        if all(names):
            found["file"] = frozenset(names)
    return found


def _same_recording(first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]]) -> bool:
    """True only when both reports provably analyzed the same recording.

    Content hashes decide when both reports carry them; older reports carry
    only the file names, which then decide. Every source object in a report
    must agree, or nothing is proven.
    """

    def identities(sources: Sequence[Mapping[str, Any]]) -> dict[str, frozenset[str]] | None:
        found: dict[str, set[frozenset[str]]] = {}
        for source in sources:
            for kind, value in _recording(source).items():
                found.setdefault(kind, set()).add(value)
        if not found or any(len(values) != 1 for values in found.values()):
            return None
        return {kind: next(iter(values)) for kind, values in found.items()}

    left, right = identities(first), identities(second)
    if left is None or right is None:
        return False
    for kind in ("sha256", "file"):
        if kind in left and kind in right:
            return left[kind] == right[kind]
    return False


# ----------------------------------------------------------------- references


class _References:
    """Report directories named by saved tests and tuning records, kept fresh."""

    def __init__(self, store: ArchiveStore) -> None:
        self._store = store
        self._signature: tuple[Any, ...] | None = None
        self._keys: Mapping[str, str] = {}

    def _tree_signature(self) -> tuple[Any, ...]:
        found: list[tuple[str, int, int]] = []
        for base in (self._store.root / "trials", self._store.tuning_dir):
            base_io = _extended_path(base)
            if not os.path.isdir(base_io):
                continue
            for current, _directories, names in os.walk(base_io):
                for name in names:
                    if not name.casefold().endswith(".json"):
                        continue
                    try:
                        item = os.stat(os.path.join(current, name))
                    except OSError:
                        continue
                    found.append((os.path.join(current, name), item.st_size, item.st_mtime_ns))
        return tuple(sorted(found))

    def current(self) -> Mapping[str, str]:
        signature = self._tree_signature()
        if signature != self._signature:
            self._keys = self._store._report_references()
            self._signature = signature
        return self._keys


def _keep_keys(keep_paths: Sequence[str]) -> frozenset[str]:
    keys: set[str] = set()
    for path in keep_paths:
        keys.update(_report_directory_keys(os.path.dirname(path)))
    return frozenset(keys)


def _row_directory_keys(row: Mapping[str, Any]) -> set[str]:
    report_path = row.get("report_path")
    if not report_path:
        return set()
    try:
        return _report_directory_keys(os.path.dirname(str(report_path)))
    except (OSError, ValueError):
        return set()


# ----------------------------------------------------------------------- plan


def _recovery_summary(store: ArchiveStore) -> dict[str, Any]:
    root = store.root / RECOVERY_DIRECTORY
    total = 0
    batches = 0
    root_io = _extended_path(root)
    if os.path.isdir(root_io):
        pending = [root_io]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as iterator:
                    entries = list(iterator)
            except OSError:
                continue
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link(entry_stat):
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    if current == root_io:
                        batches += 1
                    pending.append(entry.path)
                else:
                    total += entry_stat.st_size
    return {"path": str(root), "bytes": total, "batch_count": batches}


def _build_plan(
    store: ArchiveStore,
    keep_keys: frozenset[str],
    now_ns: int,
) -> tuple[dict[str, Any], dict[str, _Candidate], dict[str, str]]:
    """The plan, its candidates by casefolded directory, and every known reason to keep."""

    usage = store.report_storage_usage()
    reports_root = store.reports_dir
    references = store._report_references()
    candidates: dict[str, _Candidate] = {}
    reasons: dict[str, str] = {}
    counts = {reason: 0 for reason in KEPT_REASONS}
    sources: dict[str, list[Mapping[str, Any]]] = {}

    def report_sources(path: str) -> list[Mapping[str, Any]]:
        if path not in sources:
            sources[path] = _report_source_objects(
                os.path.join(_extended_path(path), "analysis.json")
            )
        return sources[path]

    def keep(path: str, reason: str) -> None:
        counts[reason] += 1
        relative = _relative_below(reports_root, path)
        if relative is not None:
            reasons[relative.casefold()] = reason

    grouped = 0
    for group in usage["groups"]:
        grouped += int(group["copies"])
        if group.get("retained"):
            keep(group["retained"]["path"], _USAGE_REASON.get(group["retained"]["reason"], "unverified"))
        for item in group["kept"]:
            keep(item["path"], _USAGE_REASON.get(item["reason"], "unverified"))
        if not group["reclaimable"]:
            continue
        kind = str(group["kind"])
        if kind == "duplicate":
            anchor_path, anchor_id = group["retained"]["path"], str(group["analysis_id"])
        else:
            anchor_path = group["superseded_by"]["path"]
            anchor_id = str(group["superseded_by"]["analysis_id"])
        anchor = _copy_state(anchor_path)
        anchor_ok = (
            anchor is not None
            and anchor.analysis_id == anchor_id
            and _inside_without_links(reports_root, anchor_path)
        )
        session_keys: frozenset[str] = frozenset()
        same_recording = kind == "duplicate"
        if kind == "superseded" and anchor_ok:
            copy_sources = report_sources(group["reclaimable"][0]["path"])
            session_keys = _session_keys(copy_sources)
            same_recording = bool(session_keys) and _same_recording(
                copy_sources, report_sources(anchor_path)
            )
        for item in group["reclaimable"]:
            path = item["path"]
            relative = _relative_below(reports_root, path)
            copy_keys = _report_directory_keys(path)
            state = _copy_state(path)
            if relative is None or state is None or not _inside_without_links(reports_root, path):
                keep(path, "unverified")
            elif copy_keys & keep_keys:
                keep(path, "open-in-app")
            elif copy_keys & references.keys():
                keep(path, "saved-test" if "trials" in {references[key] for key in copy_keys & references.keys()} else "tuning")
            elif state.analysis_id != str(group["analysis_id"]) or state.holds_other_content or not anchor_ok:
                keep(path, "unverified")
            elif state.newest_ns > now_ns - int(QUIET_PERIOD_S * 1e9):
                keep(path, "recent")
            elif (
                not same_recording
                or anchor.modified_ns < state.modified_ns
                or (kind == "superseded" and anchor.modified_ns == state.modified_ns)
            ):
                keep(path, "latest-analysis")
            else:
                candidates[relative.casefold()] = _Candidate(
                    directory=relative,
                    path=os.path.abspath(_ordinary_path(path)),
                    kind=kind,
                    analysis_id=str(group["analysis_id"]),
                    bytes=int(item["bytes"]),
                    state=state,
                    anchor_path=os.path.abspath(_ordinary_path(anchor_path)),
                    anchor_id=anchor_id,
                    session_keys=session_keys,
                )
    unidentified = int(usage["unidentified_directory_count"])
    counts["unverified"] += unidentified
    counts["only-copy"] += max(0, int(usage["report_directory_count"]) - grouped - unidentified)

    # One confirmation lists at most MAX_DIRECTORIES copies; any others are
    # offered by the next review.
    chosen = sorted(candidates.values(), key=lambda item: item.directory.casefold())[:MAX_DIRECTORIES]
    for extra in set(candidates) - {item.directory.casefold() for item in chosen}:
        del candidates[extra]
        counts["deferred"] += 1
    remove_bytes = sum(item.bytes for item in chosen)
    modified = [item.state.modified_ns for item in chosen]

    def kind_total(kind: str) -> tuple[int, int]:
        items = [item for item in chosen if item.kind == kind]
        return len(items), sum(item.bytes for item in items)

    duplicate_count, duplicate_bytes = kind_total("duplicate")
    superseded_count, superseded_bytes = kind_total("superseded")
    status = (
        "empty"
        if usage["status"] == "empty"
        else "index-unreadable"
        if usage["status"] == "index-unreadable"
        else "ready"
        if chosen
        else "nothing"
    )
    plan = {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "mode": "plan",
        "status": status,
        "read_only": True,
        "reports_root": str(reports_root),
        "recovery_root": str(store.root / RECOVERY_DIRECTORY),
        "plan_id": _plan_id(item.directory for item in chosen),
        "remove": {
            "count": len(chosen),
            "bytes": remove_bytes,
            "duplicate_count": duplicate_count,
            "duplicate_bytes": duplicate_bytes,
            "superseded_count": superseded_count,
            "superseded_bytes": superseded_bytes,
            "oldest_utc": _utc(min(modified)) if modified else None,
            "newest_utc": _utc(max(modified)) if modified else None,
            "directories": [
                {
                    "directory": item.directory,
                    "bytes": item.bytes,
                    "kind": item.kind,
                    "analysis_id": item.analysis_id,
                    "modified_utc": _utc(item.state.modified_ns),
                    "kept_copy": _relative_below(reports_root, item.anchor_path),
                }
                for item in chosen
            ],
        },
        "kept": {
            "count": int(usage["report_directory_count"]) - len(chosen),
            "bytes": max(0, int(usage["report_bytes"]) - remove_bytes),
            "by_reason": counts,
        },
        "recovery": _recovery_summary(store),
    }
    return plan, candidates, reasons


# --------------------------------------------------------------------- execute


def _validated_directories(directories: Any) -> list[str]:
    if not isinstance(directories, list) or not 1 <= len(directories) <= MAX_DIRECTORIES:
        raise ReclaimRequestError(
            f"directories must list 1-{MAX_DIRECTORIES} report directories from the reviewed plan."
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in directories:
        if not isinstance(item, str) or not item.strip() or len(item) > 1024:
            raise ReclaimRequestError("Each directory must be a report directory from the reviewed plan.")
        text = item.strip().replace("/", os.sep).replace("\\", os.sep)
        parts = text.split(os.sep)
        if (
            os.path.isabs(text)
            or os.path.splitdrive(text)[0]
            or any(part in ("", os.curdir, os.pardir) for part in parts)
            or any(":" in part for part in parts)
        ):
            raise ReclaimRequestError(f"{item!r} is not a directory below reports.")
        if text.casefold() in seen:
            raise ReclaimRequestError(f"{item!r} is listed twice.")
        seen.add(text.casefold())
        result.append(text)
    return result


def _open_history_lock(db_path: Path) -> tuple[sqlite3.Connection | None, list[dict[str, Any]] | None]:
    """Hold the history index for writing and read its rows; (None, None) when absent."""

    if not os.path.isfile(_extended_path(db_path)):
        return None, None
    # mode=rw: an index that vanished since the check is an error, never a new file.
    connection = sqlite3.connect(
        f"{Path(db_path).resolve().as_uri()}?mode=rw",
        uri=True,
        timeout=_LOCK_TIMEOUT_S,
        isolation_level=None,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(_LOCK_TIMEOUT_S * 1000)}")
        connection.execute("BEGIN IMMEDIATE")
        return connection, _history_index_rows(connection)
    except BaseException:
        _release(connection)
        raise


def _release(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass


def _verification_failure(
    store: ArchiveStore,
    candidate: _Candidate,
    rows: list[dict[str, Any]] | None,
    references: Mapping[str, str],
    keep_keys: frozenset[str],
    now_ns: int,
) -> str | None:
    """Why ``candidate`` may not move now, or None. Runs under the index lock."""

    keys = _report_directory_keys(candidate.path)
    if keys & keep_keys or keys & references.keys():
        return "referenced"
    if rows is not None and any(keys & _row_directory_keys(row) for row in rows):
        return "referenced"
    if not os.path.isdir(_extended_path(candidate.path)):
        return "missing"
    if not _inside_without_links(store.reports_dir, candidate.path):
        return "changed"
    state = _copy_state(candidate.path)
    if (
        state is None
        or state.analysis_id != candidate.analysis_id
        or (state.size, state.modified_ns, state.newest_ns)
        != (candidate.state.size, candidate.state.modified_ns, candidate.state.newest_ns)
        or state.holds_other_content
        or state.newest_ns > now_ns - int(QUIET_PERIOD_S * 1e9)
    ):
        return "changed"
    anchor = _copy_state(candidate.anchor_path)
    if (
        anchor is None
        or anchor.analysis_id != candidate.anchor_id
        or not _inside_without_links(store.reports_dir, candidate.anchor_path)
        or anchor.modified_ns < state.modified_ns
    ):
        return "changed"
    if candidate.kind == "superseded":
        if rows is None or anchor.modified_ns == state.modified_ns or not candidate.session_keys:
            return "changed"
        same_session = [
            row
            for row in rows
            if candidate.session_keys
            & set(
                _session_match_keys(
                    row.get("session_group_id"), row.get("subsession_id"), row.get("session_phase")
                )
            )
        ]
        anchor_keys = _report_directory_keys(candidate.anchor_path)
        if {str(row.get("analysis_id")) for row in same_session} != {candidate.anchor_id} or not any(
            anchor_keys & _row_directory_keys(row) for row in same_session
        ):
            return "changed"
    return None


def _move_candidate(
    store: ArchiveStore,
    candidate: _Candidate,
    batch: Path,
    references: _References,
    keep_keys: frozenset[str],
    rename: Callable[[str, str], None],
    clock_ns: Callable[[], int],
) -> str | None:
    """Verify and move one directory while the index is held; the skip reason or None."""

    try:
        connection, rows = _open_history_lock(store.db_path)
    except sqlite3.Error:
        return "index-unavailable"
    try:
        reason = _verification_failure(
            store, candidate, rows, references.current(), keep_keys, clock_ns()
        )
        if reason is not None:
            return reason
        destination = batch / candidate.directory
        try:
            os.makedirs(_extended_path(destination.parent), exist_ok=True)
            rename(_extended_path(candidate.path), _extended_path(destination))
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if isinstance(exc, PermissionError) or (
                isinstance(winerror, int) and winerror in _IN_USE_WINERRORS
            ):
                return "in-use"
            return "move-failed"
        return None
    finally:
        _release(connection)


def _remove_empty_directories(top: Path) -> None:
    """Remove empty directories this call created under ``top``, bottom-up."""

    top_io = _extended_path(top)
    if not os.path.isdir(top_io):
        return
    for current, _directories, _files in os.walk(top_io, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            pass


def _new_batch(store: ArchiveStore, now_ns: int, suffix: Callable[[], str]) -> Path:
    stamp = datetime.fromtimestamp(now_ns / 1e9, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = store.root / RECOVERY_DIRECTORY
    for _attempt in range(100):
        candidate = root / f"{stamp}-{suffix()}"
        try:
            os.makedirs(_extended_path(candidate))
        except FileExistsError:
            continue
        return candidate
    raise OSError("no free removed-reports folder name")


def _random_suffix() -> str:
    return os.urandom(3).hex()


def execute_report_reclaim(
    store: ArchiveStore,
    *,
    plan_id: str,
    directories: Any,
    keep_paths: Sequence[str] = (),
    rename: Callable[[str, str], None] = os.rename,
    clock_ns: Callable[[], int] = time.time_ns,
    batch_suffix: Callable[[], str] = _random_suffix,
) -> dict[str, Any]:
    """Move the confirmed directories that are still eligible; report every outcome."""

    confirmed = _validated_directories(directories)
    if not isinstance(plan_id, str) or plan_id != _plan_id(confirmed):
        raise ReclaimRequestError(
            "plan_id does not match the listed directories; review the copies again before removing them."
        )
    keep_keys = _keep_keys(keep_paths)
    plan, candidates, reasons = _build_plan(store, keep_keys, clock_ns())
    references = _References(store)
    reports_root = store.reports_dir
    recovery_root = store.root / RECOVERY_DIRECTORY
    recovery_existed = os.path.isdir(_extended_path(recovery_root))
    batch: Path | None = None
    moved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    for directory in sorted(confirmed, key=str.casefold):
        key = directory.casefold()
        candidate = candidates.get(key)
        if candidate is None:
            if not os.path.isdir(_extended_path(os.path.join(reports_root, directory))):
                reason = "missing"
            elif reasons.get(key) in _REFERENCE_REASONS:
                reason = "referenced"
            elif key in reasons:
                reason = "changed"
            else:
                reason = "not-eligible"
            skipped.append({"directory": directory, "reason": reason})
            continue
        if batch is None:
            now_ns = clock_ns()
            batch = _new_batch(store, now_ns, batch_suffix)
            manifest = {
                "contract": CONTRACT,
                "schema_version": SCHEMA_VERSION,
                "created_utc": _utc(now_ns),
                "note": (
                    "Older copies of saved analyses that iRacing Coach moved out of reports "
                    "after the driver confirmed. Nothing here is used by the app. Each "
                    "folder keeps its path below reports; to restore a copy, move it back "
                    "to that place. Delete this folder to free the space."
                ),
                "moved": [],
            }
        reason = _move_candidate(
            store, candidate, batch, references, keep_keys, rename, clock_ns
        )
        if reason is not None:
            skipped.append({"directory": directory, "reason": reason})
            continue
        entry = {
            "directory": directory,
            "bytes": candidate.bytes,
            "kind": candidate.kind,
            "analysis_id": candidate.analysis_id,
            "kept_copy": _relative_below(reports_root, candidate.anchor_path),
        }
        moved.append(entry)
        manifest["moved"].append(dict(entry))
        try:
            _atomic_write_text(batch / RECOVERY_MANIFEST, _json_text(manifest))
        except OSError:
            pass
    if batch is not None:
        _remove_empty_directories(batch)
    if not recovery_existed:
        try:
            os.rmdir(_extended_path(recovery_root))
        except OSError:
            pass
    status = "complete" if moved and not skipped else "partial" if moved else "nothing"
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "mode": "execute",
        "status": status,
        "read_only": False,
        "reports_root": str(reports_root),
        "recovery_root": str(recovery_root),
        "plan_id": plan["plan_id"],
        "confirmed_plan_id": plan_id,
        "batch_directory": str(batch) if batch is not None and os.path.isdir(_extended_path(batch)) else None,
        "moved": {
            "count": len(moved),
            "bytes": sum(item["bytes"] for item in moved),
            "directories": moved,
        },
        "skipped": {
            "count": len(skipped),
            "directories": skipped,
        },
        "recovery": _recovery_summary(store),
    }


def plan_report_reclaim(
    store: ArchiveStore,
    *,
    keep_paths: Sequence[str] = (),
    clock_ns: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    """What a confirmed removal would move now; creates, changes and deletes nothing."""

    plan, _candidates, _reasons = _build_plan(store, _keep_keys(keep_paths), clock_ns())
    return plan


def reclaim_report_storage_workflow(
    *,
    archive_root: str,
    confirm: bool = False,
    plan_id: str | None = None,
    directories: Any = None,
    keep_analysis_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """MCP entry point: a read-only plan, or the confirmed move of that plan."""

    store = ArchiveStore(archive_root)
    if len(keep_analysis_paths) > MAX_KEEP_PATHS:
        raise ReclaimRequestError(f"keep_analysis_paths may list at most {MAX_KEEP_PATHS} paths.")
    if not confirm:
        if plan_id is not None or directories is not None:
            raise ReclaimRequestError("plan_id and directories are only accepted with confirm: true.")
        return plan_report_reclaim(store, keep_paths=keep_analysis_paths)
    return execute_report_reclaim(
        store,
        plan_id=str(plan_id or ""),
        directories=directories,
        keep_paths=keep_analysis_paths,
    )


__all__ = [
    "CONTRACT",
    "KEPT_REASONS",
    "QUIET_PERIOD_S",
    "RECOVERY_DIRECTORY",
    "ReclaimRequestError",
    "SKIP_REASONS",
    "execute_report_reclaim",
    "plan_report_reclaim",
    "reclaim_report_storage_workflow",
]
