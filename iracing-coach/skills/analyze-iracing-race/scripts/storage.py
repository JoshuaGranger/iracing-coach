"""Durable storage for iRacing Coach reports, history, and seasonal knowledge.

The archive is deliberately separate from the installed plugin. Updating or
reinstalling the plugin must never remove race history or cached references.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:  # Package import and direct script execution are both supported.
    from .live_replay_v2 import (
        LiveReplayV2Error,
        decode_live_replay_v2,
    )
    from .path_security import local_path
    from .race_foundations import (
        TIRE_MODEL_VERSION,
        _main_loop_quality,
        _sanitize_geometry_layers,
        build_tire_prediction,
        model_file_name,
        track_geometry_sha256,
    )
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from live_replay_v2 import (
        LiveReplayV2Error,
        decode_live_replay_v2,
    )
    from path_security import local_path
    from race_foundations import (
        TIRE_MODEL_VERSION,
        _main_loop_quality,
        _sanitize_geometry_layers,
        build_tire_prediction,
        model_file_name,
        track_geometry_sha256,
    )


SCHEMA_VERSION = 2

_CACHE_CONTEXT_FIELDS = ("season_key", "car_key", "track_key", "setup_type")
_REQUIRED_CONTEXT_FIELDS = _CACHE_CONTEXT_FIELDS + ("race_length_key",)
_REQUIRED_BUNDLE_FILES = {
    "facts": "facts.json",
    "sources": "sources.json",
}
_OPTIONAL_BUNDLE_FILES = {
    "garage61": "garage61/index.json",
    "track_shape": "track/shape.json",
    "notes": "knowledge.md",
}
_SOURCE_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Replays remain append-only at up to 60 Hz on disk.  The MCP/runtime payload
# is intentionally smaller: short sessions retain up to 20 Hz, while longer
# sessions adapt to a fixed budget and let the UI interpolate at refresh rate.
# Exact event/flag/state/pit keyframes are kept outside the routine-motion
# budget, with a hard ceiling sized for far more transitions than a real race.
_REPLAY_DISPLAY_BASE_INTERVAL_S = 1.0 / 20.0
_REPLAY_DISPLAY_ROUTINE_FRAME_BUDGET = 8_000
_REPLAY_DISPLAY_HARD_FRAME_BUDGET = 10_000
_REPLAY_LEGACY_CHUNK_MAX_BYTES = 128 * 1024 * 1024
_REPLAY_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
_REPLAY_CAR_COLUMNS = (
    "car_index",
    "lap_pct",
    "lap",
    "completed_laps",
    "overall_position",
    "class_position",
    "on_pit_road",
    "track_surface",
    "pace_flags",
    "last_lap_time_s",
    "best_lap_time_s",
)


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _replay_frame_score(frame: Mapping[str, Any]) -> tuple[int, int, int, int]:
    cars = [car for car in frame.get("cars") or () if isinstance(car, Mapping)]
    car_rows = [row for row in frame.get("car_rows") or () if isinstance(row, Sequence)]
    car_count = len(cars) if cars else len(car_rows)
    field_count = sum(len(car) for car in cars) if cars else sum(len(row) for row in car_rows)
    return (
        len(frame.get("events") or ()),
        1 if isinstance(frame.get("player_telemetry"), Mapping) else 0,
        car_count,
        field_count,
    )


def _merge_replay_frames(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose the richest duplicate while retaining every explicit event."""

    selected = dict(current if prior is None or _replay_frame_score(current) >= _replay_frame_score(prior) else prior)
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, Any]] = set()
    for source in (prior, current):
        for event in (source or {}).get("events") or ():
            if not isinstance(event, Mapping):
                continue
            normalized = dict(event)
            key = (
                str(normalized.get("kind") or ""),
                str(normalized.get("label") or ""),
                str(normalized.get("sourceChannel") or normalized.get("source_channel") or ""),
                normalized.get("delta"),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append(normalized)
    selected["events"] = events
    reasons = {
        str(reason)
        for source in (prior, current)
        for reason in (source or {}).get("_keyframe_reasons") or ()
        if str(reason)
    }
    if reasons:
        selected["_keyframe_reasons"] = sorted(reasons)
    return selected


class _ReplayDisplaySampler:
    """Bounded deterministic replay materializer with exact keyframe retention."""

    def __init__(self, duration_hint_s: float | None = None):
        hinted_interval = (
            duration_hint_s / max(1, _REPLAY_DISPLAY_ROUTINE_FRAME_BUDGET - 1)
            if duration_hint_s is not None and duration_hint_s > 0
            else 0.0
        )
        self.interval_s = max(_REPLAY_DISPLAY_BASE_INTERVAL_S, hinted_interval)
        self._origin: float | None = None
        self._routine: dict[int, dict[str, Any]] = {}
        self._keyframes: dict[int, dict[str, Any]] = {}
        self._keyframe_priorities: dict[int, int] = {}
        self._keyframe_heaps: tuple[
            list[tuple[int, int]],
            list[tuple[int, int]],
            list[tuple[int, int]],
        ] = ([], [], [])
        self.keyframes_preserved = True
        self.dropped_keyframe_count = 0

    @staticmethod
    def _time_key(session_time_s: float) -> int:
        return int(round(session_time_s * 1_000_000))

    @staticmethod
    def _sort_time(frame: Mapping[str, Any]) -> float:
        value = _finite_number(frame.get("session_time_s"))
        return value if value is not None else float("-inf")

    @staticmethod
    def _reservoir_rank(time_key: int) -> int:
        """Stable SplitMix64 rank used for bounded, repeatable event sampling."""

        value = time_key & 0xFFFFFFFFFFFFFFFF
        value = (value + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return value ^ (value >> 31)

    @staticmethod
    def _keyframe_priority(frame: Mapping[str, Any]) -> int:
        reasons = set(frame.get("_keyframe_reasons") or ())
        if reasons & {"gap", "boundary"}:
            return 2
        if frame.get("events"):
            return 1
        return 0

    def _store_keyframe(self, time_key: int, frame: dict[str, Any]) -> None:
        priority = self._keyframe_priority(frame)
        self._keyframes[time_key] = frame
        self._keyframe_priorities[time_key] = priority
        # heapq is a min-heap; negative rank puts the least desirable
        # (largest stable rank) retained frame at the root.
        heapq.heappush(
            self._keyframe_heaps[priority],
            (-self._reservoir_rank(time_key), time_key),
        )

    def _worst_keyframe(self, priority: int) -> tuple[int, int] | None:
        heap = self._keyframe_heaps[priority]
        while heap:
            negative_rank, time_key = heap[0]
            if (
                time_key in self._keyframes
                and self._keyframe_priorities.get(time_key) == priority
                and -negative_rank == self._reservoir_rank(time_key)
            ):
                return time_key, -negative_rank
            heapq.heappop(heap)
        return None

    def _retain_keyframe(self, time_key: int, frame: dict[str, Any]) -> None:
        existing = self._keyframes.get(time_key)
        if existing is not None:
            merged = _merge_replay_frames(existing, frame)
            old_priority = self._keyframe_priorities[time_key]
            new_priority = self._keyframe_priority(merged)
            self._keyframes[time_key] = merged
            if new_priority != old_priority:
                self._keyframe_priorities[time_key] = new_priority
                heapq.heappush(
                    self._keyframe_heaps[new_priority],
                    (-self._reservoir_rank(time_key), time_key),
                )
            return

        if len(self._keyframes) < _REPLAY_DISPLAY_HARD_FRAME_BUDGET:
            self._store_keyframe(time_key, frame)
            return

        incoming_priority = self._keyframe_priority(frame)
        lowest_priority = next(
            (
                priority
                for priority in range(3)
                if self._worst_keyframe(priority) is not None
            ),
            None,
        )
        worst = (
            self._worst_keyframe(lowest_priority)
            if lowest_priority is not None
            else None
        )
        incoming_rank = self._reservoir_rank(time_key)
        if (
            worst is None
            or incoming_priority < lowest_priority
            or (incoming_priority == lowest_priority and incoming_rank >= worst[1])
        ):
            self.keyframes_preserved = False
            self.dropped_keyframe_count += 1
            return

        evicted_time_key = worst[0]
        self._keyframes.pop(evicted_time_key, None)
        self._keyframe_priorities.pop(evicted_time_key, None)
        self.keyframes_preserved = False
        self.dropped_keyframe_count += 1
        self._store_keyframe(time_key, frame)

    def observe(
        self,
        frame: Mapping[str, Any],
        *,
        keyframe: bool = False,
        keyframe_reason: str | None = None,
    ) -> None:
        session_time = _finite_number(frame.get("session_time_s"))
        if session_time is None:
            return
        if self._origin is None:
            self._origin = session_time
            keyframe = True
            keyframe_reason = keyframe_reason or "boundary"
        time_key = self._time_key(session_time)
        if keyframe:
            materialized = dict(frame)
            if keyframe_reason:
                materialized["_keyframe_reasons"] = [keyframe_reason]
            self._retain_keyframe(time_key, materialized)
            return
        bucket = int(math.floor((session_time - self._origin) / self.interval_s + 1e-9))
        self._routine[bucket] = _merge_replay_frames(self._routine.get(bucket), frame)
        if len(self._routine) > _REPLAY_DISPLAY_ROUTINE_FRAME_BUDGET:
            self._compact()

    def promote(self, frame: Mapping[str, Any], reason: str = "transition") -> None:
        self.observe(frame, keyframe=True, keyframe_reason=reason)

    def _compact(self) -> None:
        if self._origin is None:
            return
        self.interval_s *= 2
        compacted: dict[int, dict[str, Any]] = {}
        for frame in self._routine.values():
            session_time = _finite_number(frame.get("session_time_s"))
            if session_time is None:
                continue
            bucket = int(math.floor((session_time - self._origin) / self.interval_s + 1e-9))
            compacted[bucket] = _merge_replay_frames(compacted.get(bucket), frame)
        self._routine = compacted

    @staticmethod
    def _evenly_spaced(values: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        if count <= 0 or not values:
            return []
        if len(values) <= count:
            return list(values)
        if count == 1:
            return [values[0]]
        indexes = {
            int(round(index * (len(values) - 1) / (count - 1)))
            for index in range(count)
        }
        return [values[index] for index in sorted(indexes)]

    def finish(self) -> list[dict[str, Any]]:
        keyframes = sorted(
            self._keyframes.values(),
            key=self._sort_time,
        )
        key_times = {
            self._time_key(_finite_number(frame.get("session_time_s")) or 0.0)
            for frame in keyframes
        }
        routine = sorted(
            (
                frame
                for frame in self._routine.values()
                if self._time_key(_finite_number(frame.get("session_time_s")) or 0.0) not in key_times
            ),
            key=self._sort_time,
        )
        routine_limit = min(
            _REPLAY_DISPLAY_ROUTINE_FRAME_BUDGET,
            max(0, _REPLAY_DISPLAY_HARD_FRAME_BUDGET - len(keyframes)),
        )
        selected = keyframes + self._evenly_spaced(routine, routine_limit)
        deduplicated: dict[int, dict[str, Any]] = {}
        for frame in selected:
            session_time = _finite_number(frame.get("session_time_s"))
            if session_time is None:
                continue
            key = self._time_key(session_time)
            deduplicated[key] = _merge_replay_frames(deduplicated.get(key), frame)
        result = sorted(
            deduplicated.values(),
            key=self._sort_time,
        )
        for frame in result:
            frame.pop("_keyframe_reasons", None)
        return result


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_archive_root() -> Path:
    override = os.environ.get("IRACING_COACH_DATA")
    if override:
        return local_path(override, "IRACING_COACH_DATA")
    return local_path(
        Path.home() / "Documents" / "iRacing Coach" / "data",
        "archive_root",
    )


def safe_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80] or fallback


def stable_hash(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def session_phase(value: Any) -> str:
    """Normalize a simulator session label without inventing a phase."""

    normalized = str(value or "").strip().casefold()
    if "qual" in normalized:
        return "qualifying"
    if normalized == "race" or normalized.startswith("race "):
        return "race"
    return normalized or "unknown"


def _analysis_session_identity(analysis: Mapping[str, Any]) -> dict[str, str | None]:
    identity = analysis.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    source = analysis.get("source")
    source = source if isinstance(source, Mapping) else {}
    selection = source.get("selection")
    selection = selection if isinstance(selection, Mapping) else {}

    subsession_value = selection.get("subsession_id")
    if subsession_value in (None, ""):
        subsession_value = identity.get("subsession_id")
    subsession_id = (
        str(subsession_value).strip()
        if subsession_value not in (None, "")
        else None
    )
    sim_session_value = selection.get("sim_session_num")
    sim_session_num = (
        str(sim_session_value).strip()
        if sim_session_value not in (None, "")
        else None
    )
    sim_session_type_value = selection.get("sim_session_type")
    sim_session_type = (
        str(sim_session_type_value).strip()
        if sim_session_type_value not in (None, "")
        else None
    )
    group_value = selection.get("group_id")
    session_group_id = (
        str(group_value).strip() if group_value not in (None, "") else None
    )
    if session_group_id is None and subsession_id is not None and sim_session_num is not None:
        session_group_id = f"subsession:{subsession_id}:{sim_session_num}"
    return {
        "session_group_id": session_group_id,
        "subsession_id": subsession_id,
        "sim_session_num": sim_session_num,
        "sim_session_type": sim_session_type,
        "session_phase": session_phase(sim_session_type),
    }


def file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _atomic_copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    """Copy one immutable source without ever moving or modifying the original."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and file_sha256(destination) == expected_sha256:
        actual_source_sha256 = file_sha256(source)
        if actual_source_sha256 != expected_sha256:
            raise OSError(
                f"Source telemetry SHA-256 changed before archival for {source}: "
                f"expected {expected_sha256}, got {actual_source_sha256}"
            )
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with source.open("rb") as source_handle, os.fdopen(fd, "wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        temporary = Path(temp_name)
        actual_sha256 = file_sha256(temporary)
        if actual_sha256 != expected_sha256:
            raise OSError(
                f"Archived telemetry SHA-256 mismatch for {source}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def _validated_context(context: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in _REQUIRED_CONTEXT_FIELDS:
        value = context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Cache context field {field!r} must be a non-empty string.")
        result[field] = value
    return result


def _normalized_fingerprint(value: Any) -> str | dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("sim_physics_fingerprint cannot be empty.")
        return normalized
    if isinstance(value, Mapping):
        normalized = dict(value)
        if not normalized:
            raise ValueError("sim_physics_fingerprint cannot be empty.")
        return normalized
    raise TypeError("sim_physics_fingerprint must be a non-empty string or object.")


def _bundle_source_hash(
    *,
    sources: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any],
    garage61: Mapping[str, Any] | None,
    track_shape: Mapping[str, Any] | None,
    notes_markdown: str | None,
    sim_physics_fingerprint: str | Mapping[str, Any] | None,
) -> str:
    return stable_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "sources": list(sources),
            "facts": dict(facts),
            "garage61": dict(garage61) if garage61 is not None else None,
            "track_shape": dict(track_shape) if track_shape is not None else None,
            "notes_markdown": notes_markdown,
            "sim_physics_fingerprint": sim_physics_fingerprint,
        },
        64,
    )


def _research_completeness(
    sources: Sequence[Mapping[str, Any]], facts: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not sources:
        missing.append("sources")
    if not facts:
        missing.append("facts")
    return not missing, missing


def _remove_optional_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class ArchiveStore:
    """Own the stable on-disk contract used by both the CLI and MCP server."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = local_path(root, "archive_root") if root else default_archive_root()
        self.reports_dir = self.root / "reports"
        self.cache_dir = self.root / "season-cache"
        self.tuning_dir = self.root / "tuning"
        self.auth_dir = self.root / "auth"
        self.track_geometry_dir = self.root / "track-geometry"
        self.telemetry_traces_dir = self.root / "telemetry-traces"
        self.tire_models_dir = self.root / "tire-models"
        self.target_laps_dir = self.root / "target-laps"
        self.db_path = self.root / "history.sqlite3"

    def initialize(self) -> None:
        for directory in (
            self.root,
            self.reports_dir,
            self.cache_dir,
            self.tuning_dir,
            self.auth_dir,
            self.track_geometry_dir,
            self.telemetry_traces_dir,
            self.tire_models_dir,
            self.target_laps_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_db(self) -> None:
        deadline = time.monotonic() + 30.0
        while True:
            try:
                self._initialize_db_once()
                return
            except sqlite3.OperationalError as exc:
                if (
                    not any(token in str(exc).casefold() for token in ("locked", "busy"))
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.05)

    def _initialize_db_once(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    analysis_id TEXT PRIMARY KEY,
                    analyzed_at TEXT NOT NULL,
                    session_start TEXT,
                    subsession_id TEXT,
                    session_id TEXT,
                    session_group_id TEXT,
                    sim_session_num TEXT,
                    sim_session_type TEXT,
                    session_phase TEXT,
                    season_key TEXT NOT NULL,
                    car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    race_length_key TEXT NOT NULL,
                    source_path TEXT,
                    report_path TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_context
                    ON sessions(season_key, car_key, track_key, setup_type, race_length_key);

                CREATE TABLE IF NOT EXISTS runs (
                    analysis_id TEXT NOT NULL,
                    run_number INTEGER NOT NULL,
                    green_laps REAL,
                    caution_laps REAL,
                    total_laps REAL,
                    fuel_used_l REAL,
                    fuel_end_l REAL,
                    lap_time_slope REAL,
                    tire_json TEXT,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY (analysis_id, run_number),
                    FOREIGN KEY (analysis_id) REFERENCES sessions(analysis_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS knowledge_bundles (
                    cache_key TEXT PRIMARY KEY,
                    season_key TEXT NOT NULL,
                    car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    expires_after_season TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tuning_packages (
                    package_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    season_key TEXT NOT NULL,
                    car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    car_path TEXT,
                    track_name TEXT,
                    source_fingerprint TEXT,
                    status TEXT NOT NULL,
                    package_path TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tuning_packages_context
                    ON tuning_packages(season_key, car_key, track_key, setup_type, updated_at);

                CREATE TABLE IF NOT EXISTS tuning_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    analysis_id TEXT,
                    package_id TEXT,
                    season_key TEXT NOT NULL,
                    car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    setup_fingerprint TEXT,
                    symptoms_json TEXT NOT NULL,
                    recommendation_json TEXT NOT NULL,
                    feedback_json TEXT,
                    outcome TEXT,
                    experiment_path TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tuning_experiments_context
                    ON tuning_experiments(
                        season_key, car_key, track_key, setup_type, created_at
                    );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._migrate_session_identity(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def _migrate_session_identity(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        additions = {
            "session_group_id": "TEXT",
            "sim_session_num": "TEXT",
            "sim_session_type": "TEXT",
            "session_phase": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} {column_type}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_group ON sessions(session_group_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_phase ON sessions(subsession_id, session_phase)"
        )

        rows = connection.execute(
            """
            SELECT analysis_id, subsession_id, report_path
            FROM sessions
            WHERE session_group_id IS NULL
              AND sim_session_num IS NULL
              AND sim_session_type IS NULL
              AND session_phase IS NULL
            """
        ).fetchall()
        for row in rows:
            details: dict[str, str | None] = {
                "session_group_id": None,
                "subsession_id": str(row["subsession_id"]) if row["subsession_id"] not in (None, "") else None,
                "sim_session_num": None,
                "sim_session_type": None,
                "session_phase": "unknown",
            }
            try:
                analysis_path = Path(str(row["report_path"])).with_name("analysis.json")
                decoded = json.loads(analysis_path.read_text(encoding="utf-8"))
                if isinstance(decoded, Mapping):
                    details = _analysis_session_identity(decoded)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            connection.execute(
                """
                UPDATE sessions
                SET session_group_id = ?, sim_session_num = ?,
                    sim_session_type = ?, session_phase = ?
                WHERE analysis_id = ?
                """,
                (
                    details["session_group_id"],
                    details["sim_session_num"],
                    details["sim_session_type"],
                    details["session_phase"] or "unknown",
                    row["analysis_id"],
                ),
            )

    @staticmethod
    def context_from_analysis(analysis: Mapping[str, Any]) -> dict[str, str]:
        identity = analysis.get("identity", {})
        race = analysis.get("race_summary", {})
        season_year = identity.get("season_year")
        season_quarter = identity.get("season_quarter")
        season_id = identity.get("season_id")
        if season_year and season_quarter:
            season_key = f"{season_year}S{season_quarter}"
        elif season_id not in (None, "", 0, "0"):
            season_key = f"season-{season_id}"
        else:
            season_key = "season-unknown"
        car_id = identity.get("car_id")
        car_name = identity.get("car_name") or identity.get("car_path")
        track_id = identity.get("track_id")
        track_name = identity.get("track_name")
        track_config = identity.get("track_config")
        car_key = safe_slug(f"{car_id or 'x'}-{car_name}", "car-unknown")
        track_key = safe_slug(
            f"{track_id or 'x'}-{track_name}-{track_config}", "track-unknown"
        )
        setup_type = "fixed" if identity.get("is_fixed_setup") is True else (
            "open" if identity.get("is_fixed_setup") is False else "setup-unknown"
        )
        scheduled_laps = race.get("scheduled_laps")
        scheduled_minutes = race.get("scheduled_minutes")
        if scheduled_laps and scheduled_minutes:
            race_length_key = (
                f"declared-v1-{int(float(scheduled_laps))}-laps-or-"
                f"{float(scheduled_minutes):g}-minutes"
            )
        elif scheduled_laps:
            race_length_key = f"declared-v1-{int(float(scheduled_laps))}-laps"
        elif scheduled_minutes:
            race_length_key = f"declared-v1-{float(scheduled_minutes):g}-minutes"
        else:
            race_length_key = "declared-v1-length-unknown"
        return {
            "season_key": safe_slug(season_key, "season-unknown"),
            "car_key": car_key,
            "track_key": track_key,
            "setup_type": setup_type,
            "race_length_key": race_length_key,
            "session_phase": _analysis_session_identity(analysis)["session_phase"] or "unknown",
        }

    def cache_key(self, context: Mapping[str, str]) -> str:
        parts = (
            context["season_key"],
            context["car_key"],
            context["track_key"],
            context["setup_type"],
        )
        return "/".join(safe_slug(part) for part in parts)

    def cache_path(self, context: Mapping[str, str]) -> Path:
        return self.cache_dir.joinpath(*self.cache_key(context).split("/"))

    def cache_status(
        self,
        context: Mapping[str, str],
        *,
        sim_physics_fingerprint: str | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        expected_context = _validated_context(context)
        expected_cache_key = self.cache_key(expected_context)
        path = self.cache_path(context)
        manifest_path = path / "manifest.json"

        def result(
            state: str,
            reason: str | None,
            *,
            manifest: Mapping[str, Any] | None = None,
            **extra: Any,
        ) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "cache_key": expected_cache_key,
                "state": state,
                "path": str(path),
                "reason": reason,
            }
            if manifest is not None:
                payload["manifest"] = dict(manifest)
            payload.update(extra)
            return payload

        if not manifest_path.exists():
            return result(
                "missing", "No seasonal car/track knowledge bundle exists."
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return result("invalid", f"Manifest is unreadable: {exc}")
        if not isinstance(manifest, Mapping):
            return result("invalid", "Manifest root must be a JSON object.")

        if manifest.get("schema_version") != SCHEMA_VERSION:
            return result(
                "invalid",
                f"Unsupported cache schema version {manifest.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION}.",
                manifest=manifest,
            )
        if manifest.get("cache_key") != expected_cache_key:
            return result(
                "invalid",
                "Manifest cache_key does not match its cache location and context.",
                manifest=manifest,
            )
        for field in _CACHE_CONTEXT_FIELDS:
            if manifest.get(field) != expected_context[field]:
                return result(
                    "invalid",
                    f"Manifest context field {field!r} does not match the requested context.",
                    manifest=manifest,
                )
        if not isinstance(manifest.get("race_length_key"), str) or not str(
            manifest.get("race_length_key")
        ).strip():
            return result(
                "invalid",
                "Manifest race_length_key must be a non-empty string.",
                manifest=manifest,
            )
        if manifest.get("expires_after_season") != expected_context["season_key"]:
            return result(
                "invalid",
                "Manifest expiry season does not match its season context.",
                manifest=manifest,
            )
        for field in ("created_at", "refreshed_at"):
            if not isinstance(manifest.get(field), str) or not str(manifest[field]).strip():
                return result(
                    "invalid",
                    f"Manifest {field} must be a non-empty string.",
                    manifest=manifest,
                )
        if not isinstance(manifest.get("research_complete"), bool):
            return result(
                "invalid",
                "Manifest research_complete must be a boolean.",
                manifest=manifest,
            )
        if "invalidated" in manifest and not isinstance(manifest.get("invalidated"), bool):
            return result(
                "invalid", "Manifest invalidated must be a boolean.", manifest=manifest
            )
        if "invalidation_reason" in manifest and not isinstance(
            manifest.get("invalidation_reason"), str
        ):
            return result(
                "invalid",
                "Manifest invalidation_reason must be a string.",
                manifest=manifest,
            )

        files = manifest.get("files")
        expected_file_keys = set(_REQUIRED_BUNDLE_FILES) | set(_OPTIONAL_BUNDLE_FILES)
        if not isinstance(files, Mapping) or set(files) != expected_file_keys:
            return result(
                "invalid",
                "Manifest files must declare exactly the supported bundle components.",
                manifest=manifest,
            )
        for name, relative in _REQUIRED_BUNDLE_FILES.items():
            if files.get(name) != relative:
                return result(
                    "invalid",
                    f"Manifest required file declaration for {name!r} is invalid.",
                    manifest=manifest,
                )
        for name, relative in _OPTIONAL_BUNDLE_FILES.items():
            if files.get(name) not in (None, relative):
                return result(
                    "invalid",
                    f"Manifest optional file declaration for {name!r} is invalid.",
                    manifest=manifest,
                )

        components: dict[str, Any] = {}
        for name, relative in _REQUIRED_BUNDLE_FILES.items():
            component_path = path / relative
            if not component_path.is_file():
                return result(
                    "invalid",
                    f"Required cache component is missing: {relative}.",
                    manifest=manifest,
                )
            try:
                components[name] = json.loads(component_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return result(
                    "invalid",
                    f"Required cache component {relative} is unreadable: {exc}",
                    manifest=manifest,
                )
        if not isinstance(components["facts"], dict):
            return result(
                "invalid", "facts.json must contain a JSON object.", manifest=manifest
            )
        if not isinstance(components["sources"], list) or not all(
            isinstance(item, dict) for item in components["sources"]
        ):
            return result(
                "invalid",
                "sources.json must contain an array of JSON objects.",
                manifest=manifest,
            )

        for name in ("garage61", "track_shape"):
            relative = _OPTIONAL_BUNDLE_FILES[name]
            component_path = path / relative
            if files[name] is None:
                if component_path.exists():
                    return result(
                        "invalid",
                        f"Undeclared stale optional cache component exists: {relative}.",
                        manifest=manifest,
                    )
                components[name] = None
                continue
            if not component_path.is_file():
                return result(
                    "invalid",
                    f"Declared optional cache component is missing: {relative}.",
                    manifest=manifest,
                )
            try:
                value = json.loads(component_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return result(
                    "invalid",
                    f"Optional cache component {relative} is unreadable: {exc}",
                    manifest=manifest,
                )
            if not isinstance(value, dict):
                return result(
                    "invalid",
                    f"Optional cache component {relative} must contain a JSON object.",
                    manifest=manifest,
                )
            components[name] = value

        notes_path = path / _OPTIONAL_BUNDLE_FILES["notes"]
        if files["notes"] is None:
            if notes_path.exists():
                return result(
                    "invalid",
                    "Undeclared stale optional cache component exists: knowledge.md.",
                    manifest=manifest,
                )
            components["notes"] = None
        elif not notes_path.is_file():
            return result(
                "invalid",
                "Declared optional cache component is missing: knowledge.md.",
                manifest=manifest,
            )
        else:
            try:
                components["notes"] = notes_path.read_text(encoding="utf-8")
            except OSError as exc:
                return result(
                    "invalid",
                    f"Optional cache component knowledge.md is unreadable: {exc}",
                    manifest=manifest,
                )

        try:
            stored_fingerprint = _normalized_fingerprint(
                manifest.get("sim_physics_fingerprint")
            )
        except (TypeError, ValueError) as exc:
            return result("invalid", str(exc), manifest=manifest)
        source_hash = manifest.get("source_hash")
        if not isinstance(source_hash, str) or not _SOURCE_HASH_PATTERN.fullmatch(
            source_hash
        ):
            return result(
                "invalid", "Manifest source_hash must be a SHA-256 hex digest.", manifest=manifest
            )
        computed_hash = _bundle_source_hash(
            sources=components["sources"],
            facts=components["facts"],
            garage61=components["garage61"],
            track_shape=components["track_shape"],
            notes_markdown=components["notes"],
            sim_physics_fingerprint=stored_fingerprint,
        )
        if source_hash != computed_hash:
            return result(
                "invalid",
                "Bundle component content does not match manifest source_hash.",
                manifest=manifest,
            )

        complete, missing_research = _research_completeness(
            components["sources"], components["facts"]
        )
        if manifest["research_complete"] != complete:
            return result(
                "invalid",
                "Manifest research_complete does not match the persisted components.",
                manifest=manifest,
            )
        if manifest.get("invalidated") is True:
            return result(
                "stale",
                manifest.get("invalidation_reason")
                or "Bundle was manually invalidated.",
                manifest=manifest,
            )
        if sim_physics_fingerprint is not None:
            try:
                current_fingerprint = _normalized_fingerprint(sim_physics_fingerprint)
            except (TypeError, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            if stored_fingerprint != current_fingerprint:
                return result(
                    "stale",
                    "Bundle sim/physics fingerprint does not match the current context.",
                    manifest=manifest,
                )
        if not complete:
            return result(
                "incomplete",
                "Seasonal research is incomplete; required research content is empty.",
                manifest=manifest,
                missing_research=missing_research,
            )
        return result("fresh", None, manifest=manifest)

    def write_knowledge_bundle(
        self,
        context: Mapping[str, str],
        *,
        sources: Sequence[Mapping[str, Any]],
        facts: Mapping[str, Any],
        garage61: Mapping[str, Any] | None = None,
        track_shape: Mapping[str, Any] | None = None,
        notes_markdown: str | None = None,
        sim_physics_fingerprint: str | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        validated_context = _validated_context(context)
        normalized_fingerprint = _normalized_fingerprint(sim_physics_fingerprint)
        normalized_sources = [dict(item) for item in sources]
        normalized_facts = dict(facts)
        normalized_garage61 = dict(garage61) if garage61 is not None else None
        normalized_track_shape = dict(track_shape) if track_shape is not None else None
        if notes_markdown is not None and not isinstance(notes_markdown, str):
            raise TypeError("notes_markdown must be a string or None.")
        normalized_notes = (
            notes_markdown.rstrip() + "\n" if notes_markdown is not None else None
        )
        research_complete, _ = _research_completeness(
            normalized_sources, normalized_facts
        )

        self.initialize()
        path = self.cache_path(validated_context)
        path.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        previous_created = now
        manifest_path = path / "manifest.json"
        if manifest_path.exists():
            try:
                previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(previous_manifest, Mapping) and isinstance(
                    previous_manifest.get("created_at"), str
                ):
                    previous_created = previous_manifest["created_at"]
            except (OSError, json.JSONDecodeError):
                pass
        source_hash = _bundle_source_hash(
            sources=normalized_sources,
            facts=normalized_facts,
            garage61=normalized_garage61,
            track_shape=normalized_track_shape,
            notes_markdown=normalized_notes,
            sim_physics_fingerprint=normalized_fingerprint,
        )
        manifest = {
            **dict(context),
            **validated_context,
            "schema_version": SCHEMA_VERSION,
            "cache_key": self.cache_key(validated_context),
            "created_at": previous_created,
            "refreshed_at": now,
            "expires_after_season": validated_context["season_key"],
            "source_hash": source_hash,
            "research_complete": research_complete,
            "sim_physics_fingerprint": normalized_fingerprint,
            "files": {
                "facts": "facts.json",
                "sources": "sources.json",
                "garage61": "garage61/index.json" if normalized_garage61 is not None else None,
                "track_shape": "track/shape.json" if normalized_track_shape is not None else None,
                "notes": "knowledge.md" if normalized_notes is not None else None,
            },
        }
        _atomic_write_text(path / "facts.json", _json_text(normalized_facts))
        _atomic_write_text(path / "sources.json", _json_text(normalized_sources))
        if normalized_garage61 is not None:
            _atomic_write_text(
                path / "garage61" / "index.json", _json_text(normalized_garage61)
            )
        else:
            _remove_optional_file(path / "garage61" / "index.json")
        if normalized_track_shape is not None:
            _atomic_write_text(
                path / "track" / "shape.json", _json_text(normalized_track_shape)
            )
        else:
            _remove_optional_file(path / "track" / "shape.json")
        if normalized_notes is not None:
            _atomic_write_text(path / "knowledge.md", normalized_notes)
        else:
            _remove_optional_file(path / "knowledge.md")
        _atomic_write_text(manifest_path, _json_text(manifest))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_bundles(
                    cache_key, season_key, car_key, track_key, setup_type,
                    created_at, refreshed_at, expires_after_season,
                    manifest_path, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    refreshed_at=excluded.refreshed_at,
                    expires_after_season=excluded.expires_after_season,
                    manifest_path=excluded.manifest_path,
                    source_hash=excluded.source_hash
                """,
                (
                    manifest["cache_key"], validated_context["season_key"],
                    validated_context["car_key"], validated_context["track_key"],
                    validated_context["setup_type"], previous_created, now,
                    validated_context["season_key"], str(manifest_path), source_hash,
                ),
            )
        return manifest

    def report_directory(self, analysis: Mapping[str, Any]) -> Path:
        identity = analysis.get("identity", {})
        context = self.context_from_analysis(analysis)
        session_identity = _analysis_session_identity(analysis)
        subsession = session_identity["subsession_id"] or identity.get("session_id") or "offline"
        session_key = session_identity["session_group_id"] or (
            f"{subsession}-{session_identity['session_phase']}"
            if session_identity["session_phase"] not in (None, "", "unknown")
            else str(subsession)
        )
        stamp = safe_slug(analysis.get("analyzed_at") or utc_now())
        return self.reports_dir / context["season_key"] / safe_slug(session_key) / stamp

    def save_report_artifacts(
        self,
        analysis: Mapping[str, Any],
        report_markdown: str,
        extra_files: Mapping[str, str | bytes] | None = None,
    ) -> dict[str, str]:
        self.initialize()
        report_dir = self.report_directory(analysis)
        report_dir.mkdir(parents=True, exist_ok=True)
        analysis_path = report_dir / "analysis.json"
        report_path = report_dir / "report.md"
        _atomic_write_text(analysis_path, _json_text(dict(analysis)))
        _atomic_write_text(report_path, report_markdown.rstrip() + "\n")
        artifacts = {"analysis": str(analysis_path), "report": str(report_path)}
        for relative, payload in (extra_files or {}).items():
            target = (report_dir / relative).resolve()
            if report_dir not in target.parents:
                raise ValueError(f"Unsafe report artifact path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, bytes):
                target.write_bytes(payload)
            else:
                _atomic_write_text(target, payload)
            artifacts[relative] = str(target)
        return artifacts

    def record_analysis(self, analysis: Mapping[str, Any], report_path: str) -> None:
        self.initialize()
        context = self.context_from_analysis(analysis)
        identity = analysis.get("identity", {})
        session_identity = _analysis_session_identity(analysis)
        race = analysis.get("race_summary", {})
        analysis_id = str(analysis["analysis_id"])
        summary_json = json.dumps(race, separators=(",", ":"), default=str)
        source_paths = analysis.get("source", {}).get("telemetry_files", [])
        source_path = str(source_paths[0]) if source_paths else None
        with self.connect() as connection:
            # One recorded session may be re-analyzed after the deterministic
            # engine changes.  Supersede its old index rows so a code revision
            # cannot masquerade as additional historical races or stints.
            if session_identity["session_group_id"] is not None:
                connection.execute(
                    "DELETE FROM sessions WHERE analysis_id <> ? AND session_group_id = ?",
                    (analysis_id, session_identity["session_group_id"]),
                )
            elif (
                session_identity["subsession_id"] is not None
                and session_identity["session_phase"] not in (None, "", "unknown")
            ):
                connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE analysis_id <> ? AND subsession_id = ? AND session_phase = ?
                    """,
                    (
                        analysis_id,
                        session_identity["subsession_id"],
                        session_identity["session_phase"],
                    ),
                )
            elif source_path:
                connection.execute(
                    "DELETE FROM sessions WHERE analysis_id <> ? AND source_path = ?",
                    (analysis_id, source_path),
                )
            connection.execute(
                """
                INSERT INTO sessions(
                    analysis_id, analyzed_at, session_start, subsession_id, session_id,
                    session_group_id, sim_session_num, sim_session_type, session_phase,
                    season_key, car_key, track_key, setup_type, race_length_key,
                    source_path, report_path, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    analyzed_at=excluded.analyzed_at,
                    session_start=excluded.session_start,
                    subsession_id=excluded.subsession_id,
                    session_id=excluded.session_id,
                    session_group_id=excluded.session_group_id,
                    sim_session_num=excluded.sim_session_num,
                    sim_session_type=excluded.sim_session_type,
                    session_phase=excluded.session_phase,
                    source_path=excluded.source_path,
                    report_path=excluded.report_path,
                    summary_json=excluded.summary_json
                """,
                (
                    analysis_id, analysis.get("analyzed_at", utc_now()),
                    identity.get("session_start"), session_identity["subsession_id"],
                    identity.get("session_id"), session_identity["session_group_id"],
                    session_identity["sim_session_num"], session_identity["sim_session_type"],
                    session_identity["session_phase"], context["season_key"],
                    context["car_key"], context["track_key"], context["setup_type"],
                    context["race_length_key"], source_path, report_path, summary_json,
                ),
            )
            connection.execute("DELETE FROM runs WHERE analysis_id = ?", (analysis_id,))
            for run in analysis.get("runs", []):
                tire = run.get("tire_observation")
                connection.execute(
                    """
                    INSERT INTO runs(
                        analysis_id, run_number, green_laps, caution_laps, total_laps,
                        fuel_used_l, fuel_end_l, lap_time_slope, tire_json, metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id, run.get("run_number"), run.get("green_laps"),
                        run.get("caution_laps"), run.get("total_laps"),
                        run.get("fuel", {}).get("used_l"), run.get("fuel", {}).get("end_l"),
                        run.get("pace", {}).get("green_lap_time_slope_s_per_lap"),
                        json.dumps(tire, separators=(",", ":"), default=str) if tire else None,
                        json.dumps(run, separators=(",", ":"), default=str),
                    ),
                )

    def recent_analyses(
        self,
        *,
        limit: int = 20,
        phase: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return compact recent-session records for dashboards and selectors."""

        self.initialize()
        bounded_limit = max(1, min(int(limit), 200))
        normalized_phase = session_phase(phase) if phase is not None else None
        where = "WHERE session_phase = ?" if normalized_phase not in (None, "unknown") else ""
        arguments: tuple[Any, ...] = (
            (normalized_phase, bounded_limit)
            if where
            else (bounded_limit,)
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT analysis_id, analyzed_at, session_start, subsession_id,
                       session_id, session_group_id, sim_session_num,
                       sim_session_type, session_phase,
                       season_key, car_key, track_key, setup_type,
                       race_length_key, source_path, report_path, summary_json
                FROM sessions
                {where}
                ORDER BY COALESCE(session_start, analyzed_at) DESC, analyzed_at DESC
                LIMIT ?
                """,
                arguments,
            ).fetchall()
        result: list[dict[str, Any]] = []
        root = self.root.resolve()
        for row in rows:
            item = dict(row)
            raw_summary = item.pop("summary_json", None)
            try:
                summary = json.loads(raw_summary) if raw_summary else {}
            except json.JSONDecodeError:
                summary = {}
            report_path = Path(str(item.get("report_path") or "")).expanduser()
            analysis_path = report_path.with_name("analysis.json")
            race_card_path = report_path.with_name("race-card.md")
            report_safe = False
            analysis_safe = False
            race_card_safe = False
            try:
                report_path.resolve().relative_to(root)
                report_safe = True
                analysis_path.resolve().relative_to(root)
                analysis_safe = True
                race_card_path.resolve().relative_to(root)
                race_card_safe = True
            except (OSError, ValueError):
                pass
            item["summary"] = summary if isinstance(summary, Mapping) else {}
            item["report_available"] = bool(report_safe and report_path.is_file())
            item["analysis_available"] = bool(
                analysis_safe and analysis_path.is_file()
            )
            item["analysis_path"] = str(analysis_path) if analysis_safe else None
            item["race_card_available"] = bool(
                race_card_safe and race_card_path.is_file()
            )
            item["race_card_path"] = str(race_card_path) if race_card_safe else None
            source_path = Path(str(item.get("source_path") or ""))
            item["source_available"] = bool(item.get("source_path") and source_path.is_file())
            result.append(item)
        return result

    def historical_runs(
        self,
        context: Mapping[str, str],
        *,
        limit: int = 200,
        include_other_seasons: bool = False,
    ) -> list[dict[str, Any]]:
        where = ["s.car_key = ?", "s.track_key = ?", "s.setup_type = ?", "s.race_length_key = ?"]
        args: list[Any] = [
            context["car_key"], context["track_key"], context["setup_type"],
            context["race_length_key"],
        ]
        if not include_other_seasons:
            where.append("s.season_key = ?")
            args.append(context["season_key"])
        phase = session_phase(context.get("session_phase"))
        if phase != "unknown":
            where.append("s.session_phase = ?")
            args.append(phase)
        args.append(max(1, min(int(limit), 1000)))
        query = f"""
            SELECT s.analysis_id, s.analyzed_at, s.session_start,
                   s.subsession_id, s.session_id, s.session_group_id,
                   s.sim_session_num, s.sim_session_type, s.session_phase, s.source_path,
                   s.season_key, s.report_path,
                   r.run_number, r.green_laps, r.caution_laps, r.total_laps,
                   r.fuel_used_l, r.fuel_end_l, r.lap_time_slope,
                   r.tire_json, r.metrics_json
            FROM runs r JOIN sessions s ON s.analysis_id = r.analysis_id
            WHERE {' AND '.join(where)}
            ORDER BY s.analyzed_at DESC, r.run_number ASC
            LIMIT ?
        """
        with self.connect() as connection:
            rows = connection.execute(query, args).fetchall()
        result = []
        seen_recorded_runs: set[tuple[str, int]] = set()
        for row in rows:
            item = dict(row)
            recording_key = (
                f"group:{item.get('session_group_id')}"
                if item.get("session_group_id")
                else f"subsession:{item.get('subsession_id')}:{item.get('session_phase') or 'unknown'}"
                if item.get("subsession_id") not in (None, "")
                else f"source:{str(item.get('source_path') or '').casefold()}"
                if item.get("source_path")
                else f"session:{item.get('session_id')}:{item.get('session_start')}"
            )
            run_key = (recording_key, int(item.get("run_number") or 0))
            if run_key in seen_recorded_runs:
                continue
            seen_recorded_runs.add(run_key)
            for field in ("tire_json", "metrics_json"):
                raw = item.pop(field)
                item[field.removesuffix("_json")] = json.loads(raw) if raw else None
            result.append(item)
        return result

    @staticmethod
    def _tuning_context(context: Mapping[str, Any]) -> dict[str, str]:
        """Validate the season/car/track/setup subset used by tuning history."""

        result: dict[str, str] = {}
        for field in _CACHE_CONTEXT_FIELDS:
            value = context.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Tuning context field {field!r} must be non-empty.")
            result[field] = safe_slug(value)
        return result

    def tuning_catalog_path(self) -> Path:
        return self.tuning_dir / "catalog" / "index.json"

    def tuning_package_path(
        self, context: Mapping[str, Any], package_id: str
    ) -> Path:
        normalized = self._tuning_context(context)
        identifier = safe_slug(package_id)
        return (
            self.tuning_dir
            / "packages"
            / normalized["season_key"]
            / normalized["car_key"]
            / normalized["track_key"]
            / f"{identifier}.json"
        )

    def save_tuning_catalog(self, catalog: Mapping[str, Any]) -> Path:
        """Archive normalized metadata only; source setup files stay read-only."""

        self.initialize()
        path = self.tuning_catalog_path()
        _atomic_write_text(path, _json_text(dict(catalog)))
        return path

    def save_tuning_package(self, package: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(package)
        package_id = str(payload.get("package_id") or "").strip()
        if not package_id:
            raise ValueError("A tuning package requires package_id.")
        context = payload.get("context")
        if not isinstance(context, Mapping):
            raise ValueError("A tuning package requires a context object.")
        normalized = self._tuning_context(context)
        now = utc_now()
        payload.setdefault("schema_version", 1)
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        payload["package_id"] = package_id
        payload["context"] = {**dict(context), **normalized}
        path = self.tuning_package_path(normalized, package_id)
        _atomic_write_text(path, _json_text(payload))
        identity = payload.get("identity") if isinstance(payload.get("identity"), Mapping) else {}
        baseline = payload.get("baseline") if isinstance(payload.get("baseline"), Mapping) else {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tuning_packages(
                    package_id, created_at, updated_at, season_key, car_key,
                    track_key, setup_type, car_path, track_name,
                    source_fingerprint, status, package_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    source_fingerprint=excluded.source_fingerprint,
                    status=excluded.status,
                    package_path=excluded.package_path
                """,
                (
                    package_id,
                    payload["created_at"],
                    now,
                    normalized["season_key"],
                    normalized["car_key"],
                    normalized["track_key"],
                    normalized["setup_type"],
                    identity.get("car_path"),
                    identity.get("track_name"),
                    baseline.get("fingerprint") or baseline.get("sha256"),
                    str(payload.get("status") or "active"),
                    str(path),
                ),
            )
        return {"package": payload, "path": str(path)}

    def load_tuning_package(self, package_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT package_path FROM tuning_packages WHERE package_id = ?",
                (str(package_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown tuning package: {package_id}")
        path = Path(row["package_path"]).resolve()
        try:
            path.relative_to(self.tuning_dir.resolve())
        except ValueError as exc:
            raise ValueError("Stored tuning package path escaped the tuning archive.") from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Tuning package is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Tuning package root must be an object.")
        return payload

    def list_tuning_packages(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if context is not None:
            normalized = self._tuning_context(context)
            for field in _CACHE_CONTEXT_FIELDS:
                where.append(f"{field} = ?")
                args.append(normalized[field])
        args.append(max(1, min(int(limit), 500)))
        query = "SELECT * FROM tuning_packages"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY updated_at DESC LIMIT ?"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, args).fetchall()]

    def tuning_experiment_path(
        self, context: Mapping[str, Any], experiment_id: str
    ) -> Path:
        normalized = self._tuning_context(context)
        return (
            self.tuning_dir
            / "experiments"
            / normalized["season_key"]
            / normalized["car_key"]
            / normalized["track_key"]
            / f"{safe_slug(experiment_id)}.json"
        )

    def record_tuning_experiment(self, experiment: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(experiment)
        context = payload.get("context")
        if not isinstance(context, Mapping):
            raise ValueError("A tuning experiment requires a context object.")
        normalized = self._tuning_context(context)
        experiment_id = str(payload.get("experiment_id") or "").strip()
        if not experiment_id:
            experiment_id = "tune-" + stable_hash(
                {
                    "analysis_id": payload.get("analysis_id"),
                    "package_id": payload.get("package_id"),
                    "setup": payload.get("setup"),
                    "symptoms": payload.get("symptoms"),
                    "recommendation": payload.get("recommendation"),
                    "created_at": payload.get("created_at") or utc_now(),
                },
                20,
            )
        now = utc_now()
        payload.setdefault("schema_version", 1)
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        payload["experiment_id"] = experiment_id
        payload["context"] = {**dict(context), **normalized}
        payload.setdefault("feedback", None)
        payload.setdefault("outcome", None)
        path = self.tuning_experiment_path(normalized, experiment_id)
        _atomic_write_text(path, _json_text(payload))
        setup = payload.get("setup") if isinstance(payload.get("setup"), Mapping) else {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tuning_experiments(
                    experiment_id, created_at, updated_at, analysis_id,
                    package_id, season_key, car_key, track_key, setup_type,
                    setup_fingerprint, symptoms_json, recommendation_json,
                    feedback_json, outcome, experiment_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    symptoms_json=excluded.symptoms_json,
                    recommendation_json=excluded.recommendation_json,
                    feedback_json=excluded.feedback_json,
                    outcome=excluded.outcome,
                    experiment_path=excluded.experiment_path
                """,
                (
                    experiment_id,
                    payload["created_at"],
                    now,
                    payload.get("analysis_id"),
                    payload.get("package_id"),
                    normalized["season_key"],
                    normalized["car_key"],
                    normalized["track_key"],
                    normalized["setup_type"],
                    setup.get("fingerprint"),
                    json.dumps(payload.get("symptoms") or [], separators=(",", ":"), default=str),
                    json.dumps(payload.get("recommendation") or {}, separators=(",", ":"), default=str),
                    json.dumps(payload.get("feedback"), separators=(",", ":"), default=str)
                    if payload.get("feedback") is not None else None,
                    payload.get("outcome"),
                    str(path),
                ),
            )
        return {"experiment": payload, "path": str(path)}

    def record_tuning_feedback(
        self,
        experiment_id: str,
        feedback: Mapping[str, Any],
        *,
        outcome: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT experiment_path FROM tuning_experiments WHERE experiment_id = ?",
                (str(experiment_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown tuning experiment: {experiment_id}")
        path = Path(row["experiment_path"]).resolve()
        try:
            path.relative_to(self.tuning_dir.resolve())
        except ValueError as exc:
            raise ValueError("Stored tuning experiment path escaped the tuning archive.") from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Tuning experiment is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Tuning experiment root must be an object.")
        payload["feedback"] = dict(feedback)
        payload["outcome"] = outcome or feedback.get("outcome")
        payload["updated_at"] = utc_now()
        _atomic_write_text(path, _json_text(payload))
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tuning_experiments
                SET updated_at = ?, feedback_json = ?, outcome = ?
                WHERE experiment_id = ?
                """,
                (
                    payload["updated_at"],
                    json.dumps(payload["feedback"], separators=(",", ":"), default=str),
                    payload.get("outcome"),
                    str(experiment_id),
                ),
            )
        return {"experiment": payload, "path": str(path)}

    def load_tuning_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT experiment_path FROM tuning_experiments WHERE experiment_id = ?",
                (str(experiment_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown tuning experiment: {experiment_id}")
        path = Path(row["experiment_path"]).resolve()
        try:
            path.relative_to(self.tuning_dir.resolve())
        except ValueError as exc:
            raise ValueError("Stored tuning experiment path escaped the tuning archive.") from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Tuning experiment is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Tuning experiment root must be an object.")
        return payload

    def tuning_history(
        self,
        context: Mapping[str, Any],
        *,
        limit: int = 100,
        include_other_seasons: bool = False,
        include_other_tracks: bool = False,
    ) -> list[dict[str, Any]]:
        normalized = self._tuning_context(context)
        where = ["car_key = ?", "setup_type = ?"]
        args: list[Any] = [normalized["car_key"], normalized["setup_type"]]
        if not include_other_tracks:
            where.append("track_key = ?")
            args.append(normalized["track_key"])
        if not include_other_seasons:
            where.append("season_key = ?")
            args.append(normalized["season_key"])
        args.append(max(1, min(int(limit), 1000)))
        query = f"""
            SELECT experiment_id, created_at, updated_at, analysis_id, package_id,
                   season_key, car_key, track_key, setup_type, setup_fingerprint,
                   symptoms_json, recommendation_json, feedback_json, outcome,
                   experiment_path
            FROM tuning_experiments
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
        """
        with self.connect() as connection:
            rows = connection.execute(query, args).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in ("symptoms_json", "recommendation_json", "feedback_json"):
                raw = item.pop(field)
                item[field.removesuffix("_json")] = json.loads(raw) if raw else None
            result.append(item)
        return result

    def tuning_draft_path(self, draft_id: str) -> Path:
        """Return the durable portable path for a structured tuning draft."""

        raw_identifier = str(draft_id or "").strip()
        if not raw_identifier:
            raise ValueError("A tuning draft requires draft_id.")
        identifier = f"{safe_slug(raw_identifier, 'draft')}-{stable_hash(raw_identifier, 12)}"
        return self.tuning_dir / "drafts" / f"{identifier}.json"

    def save_tuning_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically persist feedback even when recommendation gates are blocked."""

        payload = dict(draft)
        draft_id = str(payload.get("draft_id") or "").strip()
        if not draft_id:
            raise ValueError("A tuning draft requires draft_id.")
        now = utc_now()
        payload.setdefault("schema_version", 2)
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        payload["draft_id"] = draft_id
        path = self.tuning_draft_path(draft_id)
        if path.is_file():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prior = None
            if isinstance(prior, Mapping) and prior.get("created_at"):
                payload["created_at"] = prior["created_at"]
        _atomic_write_text(path, _json_text(payload))
        return {"draft": payload, "path": str(path)}

    def load_tuning_draft(self, draft_id: str) -> dict[str, Any]:
        path = self.tuning_draft_path(draft_id).resolve()
        try:
            path.relative_to(self.tuning_dir.resolve())
        except ValueError as exc:
            raise ValueError("Stored tuning draft path escaped the tuning archive.") from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"Unknown tuning draft: {draft_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Tuning draft is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Tuning draft root must be an object.")
        return payload

    def source_fingerprints(self, paths: Iterable[str | os.PathLike[str]]) -> list[dict[str, Any]]:
        result = []
        for raw_path in paths:
            path = Path(raw_path).resolve()
            before = path.stat()
            sha256 = file_sha256(path)
            after = path.stat()
            before_signature = (
                before.st_size,
                before.st_mtime_ns,
                getattr(before, "st_dev", None),
                getattr(before, "st_ino", None),
            )
            after_signature = (
                after.st_size,
                after.st_mtime_ns,
                getattr(after, "st_dev", None),
                getattr(after, "st_ino", None),
            )
            if after_signature != before_signature:
                raise OSError(
                    f"Source changed while SHA-256 was being computed: {path}"
                )
            result.append(
                {
                    "path": str(path),
                    "size": after.st_size,
                    "modified_ns": after.st_mtime_ns,
                    "sha256": sha256,
                }
            )
        return result

    def archive_raw_telemetry(
        self, fingerprints: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Create content-addressed portable copies while preserving originals."""

        self.initialize()
        items: list[dict[str, Any]] = []
        for record in fingerprints:
            source = Path(str(record.get("path") or "")).resolve(strict=True)
            sha256 = str(record.get("sha256") or "").strip().lower()
            if not _SOURCE_HASH_PATTERN.fullmatch(sha256):
                raise ValueError(f"Invalid source SHA-256 for {source}")
            source_before = source.stat()
            expected_size = int(record.get("size") or source_before.st_size)
            expected_modified_ns = int(
                record.get("modified_ns") or source_before.st_mtime_ns
            )
            if (
                source_before.st_size != expected_size
                or source_before.st_mtime_ns != expected_modified_ns
            ):
                raise OSError(
                    f"Source changed after fingerprinting and before archival: {source}"
                )
            archive_dir = self.telemetry_traces_dir / "raw" / sha256
            manifest_path = archive_dir / "manifest.json"
            prior: Mapping[str, Any] = {}
            if manifest_path.is_file():
                try:
                    decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(decoded, Mapping) and decoded.get("sha256") == sha256:
                        prior = decoded
                except (OSError, json.JSONDecodeError):
                    prior = {}

            prior_archived_name = str(prior.get("archived_file") or "").strip()
            if prior_archived_name and Path(prior_archived_name).name == prior_archived_name:
                archived_name = prior_archived_name
            else:
                suffix = source.suffix.casefold() if source.suffix else ".bin"
                archived_name = f"{safe_slug(source.stem, 'telemetry')}{suffix}"
            destination = archive_dir / archived_name
            _atomic_copy_verified(source, destination, sha256)

            source_stat = source.stat()
            if (
                source_stat.st_size != source_before.st_size
                or source_stat.st_mtime_ns != source_before.st_mtime_ns
            ):
                raise OSError(f"Source changed while it was being archived: {source}")
            archived_size = destination.stat().st_size
            modified_ns = expected_modified_ns
            discovery_id = stable_hash(
                {
                    "sha256": sha256,
                    "source_path": str(source),
                    "size": archived_size,
                    "modified_ns": modified_ns,
                },
                64,
            )
            discoveries: list[dict[str, Any]] = []
            for item in prior.get("source_discoveries") or ():
                if not isinstance(item, Mapping) or not item.get("discovery_id"):
                    continue
                discoveries.append(dict(item))
            if not discoveries and prior.get("original_path"):
                legacy_modified_ns = prior.get("modified_ns")
                legacy_discovery_id = stable_hash(
                    {
                        "sha256": sha256,
                        "source_path": str(prior.get("original_path")),
                        "size": int(prior.get("size") or archived_size),
                        "modified_ns": legacy_modified_ns,
                    },
                    64,
                )
                discoveries.append(
                    {
                        "discovery_id": legacy_discovery_id,
                        "source_path": str(prior.get("original_path")),
                        "original_file_name": str(
                            prior.get("original_file_name")
                            or Path(str(prior.get("original_path"))).name
                        ),
                        "size": int(prior.get("size") or archived_size),
                        "modified_ns": legacy_modified_ns,
                        "discovered_at": prior.get("verified_at") or utc_now(),
                    }
                )
            is_new_discovery = not any(
                str(item.get("discovery_id")) == discovery_id for item in discoveries
            )
            verified_at = utc_now()
            if is_new_discovery:
                discoveries.append(
                    {
                        "discovery_id": discovery_id,
                        "source_path": str(source),
                        "original_file_name": source.name,
                        "size": archived_size,
                        "modified_ns": modified_ns,
                        "discovered_at": verified_at,
                    }
                )
            first_discovery = discoveries[0]
            manifest = {
                "schema_version": 2,
                "sha256": sha256,
                "size": archived_size,
                "archived_file": archived_name,
                # Retain the original contract-v1 fields as the first known
                # discovery.  New readers use source_discoveries so a later
                # identical file never erases earlier provenance.
                "original_file_name": first_discovery.get("original_file_name"),
                "original_path": first_discovery.get("source_path"),
                "verified_at": verified_at,
                "first_archived_at": prior.get("first_archived_at")
                or prior.get("verified_at")
                or verified_at,
                "source_discovery_count": len(discoveries),
                "source_discoveries": discoveries,
                "retention": "append-only; source and archive are never deleted by analysis",
            }
            _atomic_write_text(manifest_path, _json_text(manifest))
            items.append(
                {
                    "sha256": sha256,
                    "size": manifest["size"],
                    "source_path": str(source),
                    "archive_path": str(destination),
                    "discovery_id": discovery_id,
                    "new_discovery": is_new_discovery,
                    "status": "verified",
                }
            )
        return {
            "schema_version": 2,
            "status": "verified" if items else "empty",
            "durably_copied": bool(items),
            "items": items,
        }

    @staticmethod
    def _verified_geometry_quality(value: Mapping[str, Any]) -> dict[str, Any]:
        """Return conservative quality, migrating legacy cache entries in memory."""

        quality = value.get("quality")
        quality = dict(quality) if isinstance(quality, Mapping) else {}
        raw_main_path = value.get("main_path")
        main_path = (
            raw_main_path
            if isinstance(raw_main_path, Sequence)
            and not isinstance(raw_main_path, (str, bytes, bytearray))
            else ()
        )
        measured = _main_loop_quality(main_path)
        explicit_complete = quality.get("main_loop_complete")

        def bounded(field: str, fallback: float, minimum: float, maximum: float) -> float:
            try:
                number = float(quality.get(field))
            except (TypeError, ValueError, OverflowError):
                return fallback
            return max(minimum, min(maximum, number)) if math.isfinite(number) else fallback

        if explicit_complete is True:
            # A producer claim can only be downgraded by the actual cached path,
            # never promoted beyond what its normalized points verify.
            complete = bool(measured["main_loop_complete"])
            coverage = min(
                bounded("lap_percent_coverage", float(measured["lap_percent_coverage"]), 0.0, 1.0),
                float(measured["lap_percent_coverage"]),
            )
            maximum_gap = max(
                bounded("maximum_lap_percent_gap", float(measured["maximum_lap_percent_gap"]), 0.0, 1.0),
                float(measured["maximum_lap_percent_gap"]),
            )
            closure_distance = max(
                bounded("closure_distance", float(measured["closure_distance"]), 0.0, math.sqrt(2.0)),
                float(measured["closure_distance"]),
            )
        elif explicit_complete is False:
            # Rejected builders intentionally clear main_path.  Preserve their
            # measured pre-rejection coverage for provenance and ranking only.
            complete = False
            coverage = bounded("lap_percent_coverage", float(measured["lap_percent_coverage"]), 0.0, 1.0)
            maximum_gap = bounded(
                "maximum_lap_percent_gap", float(measured["maximum_lap_percent_gap"]), 0.0, 1.0
            )
            closure_distance = bounded(
                "closure_distance", float(measured["closure_distance"]), 0.0, math.sqrt(2.0)
            )
        else:
            # Legacy entries receive no trust based on point count or status;
            # recompute all four completeness fields from the recorded path.
            complete = bool(measured["main_loop_complete"])
            coverage = float(measured["lap_percent_coverage"])
            maximum_gap = float(measured["maximum_lap_percent_gap"])
            closure_distance = float(measured["closure_distance"])

        try:
            observed_points = max(
                0,
                int(
                    quality.get("observed_main_path_points")
                    or quality.get("main_path_points")
                    or len(main_path)
                ),
            )
        except (TypeError, ValueError, OverflowError):
            observed_points = len(main_path)
        return {
            **quality,
            "main_loop_complete": complete,
            "lap_percent_coverage": round(coverage, 6),
            "maximum_lap_percent_gap": round(maximum_gap, 6),
            "closure_distance": round(closure_distance, 8),
            "geometry_plausible": bool(measured.get("geometry_plausible")),
            "main_path_span": float(measured.get("main_path_span") or 0.0),
            "maximum_segment_distance": measured.get("maximum_segment_distance"),
            "maximum_relative_segment_distance": measured.get("maximum_relative_segment_distance"),
            "main_path_points": len(main_path) if complete else 0,
            "observed_main_path_points": observed_points,
        }

    @classmethod
    def _geometry_score(cls, value: Mapping[str, Any]) -> tuple[int, int, int, int, int, int, int, int]:
        quality = cls._verified_geometry_quality(value)
        return (
            1 if quality.get("geometry_plausible") else 0,
            1 if quality["main_loop_complete"] else 0,
            int(round(float(quality["lap_percent_coverage"]) * 1_000_000)),
            -int(round(float(quality["maximum_lap_percent_gap"]) * 1_000_000)),
            -int(round(float(quality["closure_distance"]) * 1_000_000)),
            int(quality.get("observed_main_path_points") or quality.get("main_path_points") or 0),
            int(quality.get("pit_lane_points") or 0),
            int(quality.get("pit_entry_observations") or 0)
            + int(quality.get("pit_exit_observations") or 0),
        )

    def cache_track_geometry(self, geometry: Mapping[str, Any]) -> dict[str, Any]:
        self.initialize()
        exact_key = str(geometry.get("track_configuration_key") or "track-unknown").strip()
        exact_identity = {
            "track_configuration_key": exact_key,
            "track_id": geometry.get("track_id"),
            "track_name": geometry.get("track_name"),
            "track_config": geometry.get("track_config"),
        }
        key = safe_slug(exact_key, "track-unknown")
        path = self.track_geometry_dir / f"{key}-{stable_hash(exact_identity, 12)}.json"
        legacy_path = self.track_geometry_dir / f"{key}.json"
        prior: Mapping[str, Any] = {}
        read_path = path if path.is_file() else legacy_path
        if read_path.is_file():
            try:
                decoded = json.loads(read_path.read_text(encoding="utf-8"))
                prior = decoded if isinstance(decoded, Mapping) else {}
            except (OSError, json.JSONDecodeError):
                prior = {}
        prior_identity = {
            "track_configuration_key": str(prior.get("track_configuration_key") or "").strip(),
            "track_id": prior.get("track_id"),
            "track_name": prior.get("track_name"),
            "track_config": prior.get("track_config"),
        }
        if prior and prior_identity != exact_identity:
            # A durable file is selected only for the exact configuration named
            # by its payload, even if a stale or manually moved file shares the path.
            prior = {}
        incoming = dict(geometry)
        prior_is_chosen = bool(
            prior and self._geometry_score(prior) > self._geometry_score(incoming)
        )
        chosen = dict(prior if prior_is_chosen else incoming)

        def source_hashes(value: Mapping[str, Any], field: str) -> list[str]:
            raw_values = value.get(field) or ()
            if isinstance(raw_values, str):
                raw_values = [raw_values]
            return sorted(
                {
                    str(item).lower()
                    for item in raw_values
                    if _SOURCE_HASH_PATTERN.fullmatch(str(item).lower())
                }
            )

        def contributing_hashes(value: Mapping[str, Any]) -> list[str]:
            return source_hashes(value, "contributing_source_sha256") or source_hashes(
                value, "source_sha256"
            )

        def geometry_observation(value: Mapping[str, Any]) -> dict[str, Any]:
            contributors = contributing_hashes(value)
            transform = dict(value.get("transform") or {})
            geometry_fingerprint = stable_hash(
                {
                    "track_configuration_key": value.get("track_configuration_key"),
                    "track_id": value.get("track_id"),
                    "track_name": value.get("track_name"),
                    "track_config": value.get("track_config"),
                    "main_path": value.get("main_path") or [],
                    "pit_lane": value.get("pit_lane") or [],
                    "pit_entry_path": value.get("pit_entry_path") or [],
                    "pit_exit_path": value.get("pit_exit_path") or [],
                    "transform": transform,
                },
                64,
            )
            observation_id = stable_hash(
                {
                    "source_sha256": contributors,
                    "geometry_fingerprint": geometry_fingerprint,
                },
                64,
            )
            return {
                "observation_id": observation_id,
                "source_sha256": contributors,
                "quality": self._verified_geometry_quality(value),
                "transform": transform,
                "geometry_fingerprint": geometry_fingerprint,
                "observed_at": utc_now(),
            }

        prior_provenance = prior.get("geometry_provenance")
        prior_provenance = (
            prior_provenance if isinstance(prior_provenance, Mapping) else {}
        )
        observations: dict[str, dict[str, Any]] = {}
        for item in prior_provenance.get("observations") or ():
            if isinstance(item, Mapping) and item.get("observation_id"):
                observations[str(item["observation_id"])] = dict(item)
        prior_observation = geometry_observation(prior) if prior else None
        incoming_observation = geometry_observation(incoming)
        if prior_observation is not None:
            observations.setdefault(
                str(prior_observation["observation_id"]), prior_observation
            )
        observations.setdefault(
            str(incoming_observation["observation_id"]), incoming_observation
        )

        chosen_observation = prior_observation if prior_is_chosen else incoming_observation
        assert chosen_observation is not None
        contributing = contributing_hashes(chosen)
        observed = sorted(
            {
                *source_hashes(prior, "observed_source_sha256"),
                *source_hashes(prior, "source_sha256"),
                *source_hashes(incoming, "observed_source_sha256"),
                *source_hashes(incoming, "source_sha256"),
            }
        )
        verified_quality = self._verified_geometry_quality(chosen)
        chosen["quality"] = verified_quality
        if not verified_quality["main_loop_complete"]:
            # Never publish an open polyline in the canonical main-loop slot and
            # never retain a synthetic start/finish line for unknown geometry.
            chosen["status"] = "unavailable"
            chosen["main_path"] = []
            chosen["pit_lane"] = []
            chosen["pit_entry_path"] = []
            chosen["pit_exit_path"] = []
            chosen["start_finish_line"] = None
            chosen["pit_commitment_line"] = None
            chosen["pit_merge_line"] = None
            reasons = [str(item) for item in chosen.get("unavailable_reasons") or () if str(item).strip()]
            completeness_reason = "A complete closed main circuit loop could not be verified."
            if completeness_reason not in reasons:
                reasons.append(completeness_reason)
            chosen["unavailable_reasons"] = reasons
            chosen["geometry_hash"] = None
        else:
            chosen, rejected_layers = _sanitize_geometry_layers(chosen)
            if rejected_layers:
                reasons = [str(item) for item in chosen.get("unavailable_reasons") or () if str(item).strip()]
                reason = "Implausible cached track overlays were omitted: " + ", ".join(sorted(rejected_layers)) + "."
                if reason not in reasons:
                    reasons.append(reason)
                chosen["unavailable_reasons"] = reasons
                chosen["status"] = "partial"
                chosen["quality"] = {
                    **verified_quality,
                    "rejected_geometry_layers": sorted(rejected_layers),
                }
            # A re-analysis upgrades legacy cache entries even when their
            # recorded point coverage still wins canonical selection.
            chosen["geometry_hash"] = track_geometry_sha256(chosen)
        # source_sha256 remains the contract-v1 observed-source union.  The
        # additive fields make selection provenance unambiguous.
        chosen["source_sha256"] = observed
        chosen["contributing_source_sha256"] = contributing
        chosen["observed_source_sha256"] = observed
        chosen["geometry_provenance"] = {
            "selected_observation_id": chosen_observation["observation_id"],
            "normalization_transform": dict(chosen.get("transform") or {}),
            "observations": sorted(
                observations.values(), key=lambda item: str(item["observation_id"])
            ),
        }
        chosen["cache"] = {
            "path": str(path),
            "updated_at": utc_now(),
            "source_recordings": len(observed),
            "contributing_source_recordings": len(contributing),
            "selection": "complete closed loop first, then lap-percent coverage and recorded detail",
        }
        _atomic_write_text(path, _json_text(chosen))
        return dict(chosen)

    def update_tire_learning(self, package: Mapping[str, Any]) -> dict[str, Any]:
        """Merge immutable observations and calculate an evidence-bounded model."""

        self.initialize()
        context_key = str(package.get("context_key") or "").strip()
        path = self.tire_models_dir / model_file_name(context_key or "unsupported")
        prior: Mapping[str, Any] = {}
        if path.is_file():
            try:
                decoded = json.loads(path.read_text(encoding="utf-8"))
                prior = decoded if isinstance(decoded, Mapping) else {}
            except (OSError, json.JSONDecodeError):
                prior = {}
        observations: dict[str, dict[str, Any]] = {}
        for raw in list(prior.get("observations") or ()) + list(package.get("observations") or ()):
            if not isinstance(raw, Mapping) or not raw.get("observation_id"):
                continue
            observations[str(raw["observation_id"])] = dict(raw)
        current_age = package.get("current_tire_age") or {}
        prediction_context = package.get("prediction_context") or {}
        prediction = build_tire_prediction(list(observations.values()), current_age, prediction_context)
        observation_set_fingerprint = stable_hash(
            sorted(
                observations.values(),
                key=lambda item: str(item.get("observation_id") or ""),
            ),
            64,
        )
        model = {
            "schema_version": 1,
            "model_version": TIRE_MODEL_VERSION,
            "observation_set_fingerprint": observation_set_fingerprint,
            "context_key": context_key,
            "context": dict(package.get("context") or prior.get("context") or {}),
            "family": package.get("family") or prior.get("family"),
            "supported_families": list(package.get("supported_families") or ()),
            "updated_at": utc_now(),
            "prediction_context": dict(prediction_context),
            "observations": sorted(
                observations.values(), key=lambda item: str(item.get("observation_id"))
            ),
            "prediction": prediction,
        }
        _atomic_write_text(path, _json_text(model))
        return {
            "status": package.get("status"),
            "model_path": str(path),
            "observation_count": len(observations),
            "model_version": TIRE_MODEL_VERSION,
            "observation_set_fingerprint": observation_set_fingerprint,
            "prediction": prediction,
        }

    def cache_garage61_target_laps(
        self, context: Mapping[str, str], index: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.initialize()
        path = self.target_laps_dir.joinpath(*self.cache_key(context).split("/")) / "garage61.json"
        payload = {
            "schema_version": 1,
            "target_derivation_version": index.get("target_derivation_version"),
            "cached_at": utc_now(),
            "cache_key": self.cache_key(context),
            "comparison_scope": index.get("comparison_scope"),
            "target": dict(index.get("target") or {}),
            "representative_laps": list(index.get("representative_laps") or ()),
            "reference_comparisons": list(index.get("reference_comparisons") or ()),
            "comparison_quality": dict(index.get("comparison_quality") or {}),
        }
        _atomic_write_text(path, _json_text(payload))
        return {"status": "cached", "path": str(path), "count": len(payload["representative_laps"])}

    def live_replay_for_analysis(self, analysis: Mapping[str, Any]) -> dict[str, Any] | None:
        """Load SHA-verified live chunks for the exact subsession/session phase."""

        session_identity = _analysis_session_identity(analysis)
        subsession = session_identity.get("subsession_id")
        session_number = session_identity.get("sim_session_num")
        expected_phase = session_identity.get("session_phase") or "unknown"
        if subsession in (None, ""):
            return None
        root = self.telemetry_traces_dir / "live-replay"
        if not root.is_dir():
            return None
        matches: list[tuple[Path, Mapping[str, Any]]] = []
        for manifest_path in root.glob("*/manifest.json"):
            try:
                if manifest_path.stat().st_size > _REPLAY_MANIFEST_MAX_BYTES:
                    continue
                decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(decoded, Mapping):
                continue
            if str(decoded.get("subsessionId") or "") != str(subsession):
                continue
            recorded_session = decoded.get("sessionNumber")
            if session_number not in (None, "") and recorded_session not in (None, "") and str(recorded_session) != str(session_number):
                continue
            recorded_phase = session_phase(decoded.get("sessionType"))
            if expected_phase != "unknown" and recorded_phase != "unknown" and recorded_phase != expected_phase:
                continue
            if (
                expected_phase != "unknown"
                and recorded_phase == "unknown"
                and (session_number in (None, "") or recorded_session in (None, ""))
            ):
                # Subsession alone cannot distinguish practice/qualifying/race.
                continue
            matches.append((manifest_path, decoded))
        if not matches:
            return None

        hinted_times = [
            value
            for _, manifest in matches
            for chunk in manifest.get("chunks") or ()
            if isinstance(chunk, Mapping)
            for key in ("startSessionTimeSeconds", "endSessionTimeSeconds")
            if (value := _finite_number(chunk.get(key))) is not None
        ]
        duration_hint_s = (
            max(hinted_times) - min(hinted_times)
            if len(hinted_times) >= 2 and max(hinted_times) > min(hinted_times)
            else None
        )
        display_sampler = _ReplayDisplaySampler(duration_hint_s)
        participants_by_index: dict[int, dict[str, Any]] = {}
        participant_segments: dict[int, set[int]] = {}
        participant_frame_counts: dict[int, int] = {}
        participant_first_times: dict[int, float] = {}
        participant_last_times: dict[int, float] = {}
        finite_time_keys: set[int] = set()
        raw_frame_count = 0
        manifests: list[str] = []
        capture_segments: list[dict[str, Any]] = []
        manifest_sample_rates: list[float] = []
        player_index: int | None = None
        session_states = {0: "invalid", 1: "get_in_car", 2: "warmup", 3: "parade_laps", 4: "racing", 5: "checkered", 6: "cooldown"}

        def integer(value: Any) -> int | None:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return int(round(number)) if math.isfinite(number) else None

        def flag_labels(raw: int) -> list[str]:
            result: list[str] = []
            for label, mask in (("green", 0x0004), ("yellow", 0x0008 | 0x0100 | 0x4000 | 0x8000), ("white", 0x0002), ("checkered", 0x0001), ("black", 0x00010000)):
                if raw & mask:
                    result.append(label)
            return result

        for segment_index, (manifest_path, manifest) in enumerate(matches):
            manifests.append(str(manifest_path))
            capture_metrics = manifest.get("captureMetrics")
            observed_sample_rate = (
                capture_metrics.get("observedSampleRateHz")
                if isinstance(capture_metrics, Mapping)
                else None
            )
            segment_sample_rate: float | None = None
            for candidate in (manifest.get("sampleRateHz"), observed_sample_rate):
                try:
                    parsed_rate = float(candidate)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(parsed_rate) and parsed_rate > 0:
                    segment_sample_rate = parsed_rate
                    manifest_sample_rates.append(parsed_rate)
                    break
            if player_index is None:
                player_index = integer(manifest.get("playerCarIndex"))
            segment_channels: dict[str, dict[str, Any]] = {}
            for item in manifest.get("coverage") or ():
                if not isinstance(item, Mapping) or not item.get("channel"):
                    continue
                channel = str(item["channel"])
                segment_channels[channel] = {
                    "recorded": item.get("recorded") is True,
                    "reason": item.get("unavailableReason"),
                }
            for item in manifest.get("participants") or ():
                if not isinstance(item, Mapping) or (car_index := integer(item.get("carIndex"))) is None:
                    continue
                participants_by_index[car_index] = {
                    "car_index": car_index,
                    "car_number": item.get("carNumber"),
                    "class_id": integer(item.get("classId")),
                    "class_name": item.get("className"),
                    "car_name": item.get("carName"),
                    "driver_name": item.get("driverName"),
                    "team_name": item.get("teamName"),
                    "is_player": car_index == player_index,
                    "is_spectator": item.get("isSpectator") is True,
                }
            verified_chunk_count = 0
            segment_frame_count = 0
            previous_segment_frame: dict[str, Any] | None = None
            previous_segment_signature: tuple[Any, ...] | None = None
            previous_segment_time: float | None = None
            for chunk in manifest.get("chunks") or ():
                if not isinstance(chunk, Mapping) or not chunk.get("file") or not chunk.get("sha256"):
                    continue
                chunk_path = (manifest_path.parent / str(chunk["file"])).resolve()
                if manifest_path.parent.resolve() not in chunk_path.parents or not chunk_path.is_file():
                    continue
                if (
                    chunk_path.suffix.lower() != ".ircr2"
                    and chunk_path.stat().st_size > _REPLAY_LEGACY_CHUNK_MAX_BYTES
                ):
                    continue
                expected = str(chunk["sha256"]).lower()
                if not _SOURCE_HASH_PATTERN.fullmatch(expected) or file_sha256(chunk_path) != expected:
                    continue
                try:
                    decoded_chunk = (
                        decode_live_replay_v2(chunk_path)
                        if chunk_path.suffix.lower() == ".ircr2"
                        else json.loads(chunk_path.read_text(encoding="utf-8"))
                    )
                except (
                    OSError,
                    json.JSONDecodeError,
                    LiveReplayV2Error,
                ):
                    continue
                verified_chunk_count += 1
                for raw_frame in (decoded_chunk.get("frames") or ()) if isinstance(decoded_chunk, Mapping) else ():
                    if not isinstance(raw_frame, Mapping):
                        continue
                    segment_frame_count += 1
                    raw_frame_count += 1
                    raw_flags = integer(raw_frame.get("sessionFlags")) or 0
                    cars: list[dict[str, Any]] = []
                    observed_cars: set[int] = set()
                    for raw_car in raw_frame.get("cars") or ():
                        if not isinstance(raw_car, Mapping) or (car_index := integer(raw_car.get("carIndex"))) is None:
                            continue
                        if car_index in observed_cars:
                            continue
                        observed_cars.add(car_index)
                        participant_segments.setdefault(car_index, set()).add(segment_index)
                        participants_by_index.setdefault(
                            car_index,
                            {
                                "car_index": car_index,
                                "car_number": None,
                                "class_id": None,
                                "class_name": None,
                                "car_name": None,
                                "driver_name": None,
                                "team_name": None,
                                "is_player": car_index == player_index,
                                "is_spectator": False,
                            },
                        )
                        lap_pct = _finite_number(raw_car.get("lapDistancePercent"))
                        if lap_pct is not None and not 0 <= lap_pct <= 1:
                            # iRacing uses negative sentinels for cars whose
                            # world position is unavailable.  Keep that as
                            # missing; never draw it at the start/finish line.
                            lap_pct = None
                        cars.append({
                            "car_index": car_index,
                            "lap_pct": lap_pct,
                            "lap": integer(raw_car.get("lap")),
                            "completed_laps": integer(raw_car.get("completedLaps")),
                            "overall_position": integer(raw_car.get("overallPosition")),
                            "class_position": integer(raw_car.get("classPosition")),
                            "on_pit_road": raw_car.get("onPitRoad") if isinstance(raw_car.get("onPitRoad"), bool) else None,
                            "track_surface": integer(raw_car.get("trackSurface")),
                            "pace_flags": integer(raw_car.get("paceFlags")),
                            "last_lap_time_s": raw_car.get("lastLapSeconds"),
                            "best_lap_time_s": raw_car.get("bestLapSeconds"),
                        })
                    session_time = _finite_number(raw_frame.get("sessionTimeSeconds"))
                    if session_time is not None:
                        finite_time_keys.add(int(round(session_time * 1_000_000)))
                    for car_index in observed_cars:
                        participant_frame_counts[car_index] = participant_frame_counts.get(car_index, 0) + 1
                        if session_time is not None:
                            participant_first_times[car_index] = min(
                                participant_first_times.get(car_index, session_time),
                                session_time,
                            )
                            participant_last_times[car_index] = max(
                                participant_last_times.get(car_index, session_time),
                                session_time,
                            )
                    player_telemetry = (
                        dict(raw_frame["playerTelemetry"])
                        if isinstance(raw_frame.get("playerTelemetry"), Mapping)
                        else None
                    )
                    events = [
                        dict(event)
                        for event in raw_frame.get("events") or ()
                        if isinstance(event, Mapping)
                    ]
                    frame = {
                        "session_time_s": session_time,
                        "session_state": session_states.get(integer(raw_frame.get("sessionState")) or -1, "unknown"),
                        "global_flags": raw_flags,
                        "global_flag_labels": flag_labels(raw_flags),
                        "car_rows": [
                            [car.get(column) for column in _REPLAY_CAR_COLUMNS]
                            for car in cars
                        ],
                        "captured_at": raw_frame.get("capturedAt"),
                        "player_telemetry": player_telemetry,
                        "events": events,
                        "_capture_segment": segment_index,
                    }
                    player_car = next(
                        (car for car in cars if car.get("car_index") == player_index),
                        None,
                    )
                    signature = (
                        raw_flags,
                        frame["session_state"],
                        (player_telemetry or {}).get("onPitRoad"),
                        (player_telemetry or {}).get("towing"),
                        (player_telemetry or {}).get("repairRequired"),
                        (player_telemetry or {}).get("trackSurface"),
                        (player_car or {}).get("on_pit_road"),
                        (player_car or {}).get("track_surface"),
                    )
                    keyframe = previous_segment_frame is None or bool(events) or (
                        previous_segment_signature is not None
                        and signature != previous_segment_signature
                    )
                    keyframe_reason = (
                        "boundary"
                        if previous_segment_frame is None
                        else "event"
                        if events
                        else "transition"
                        if keyframe
                        else None
                    )
                    if (
                        session_time is not None
                        and previous_segment_time is not None
                        and session_time - previous_segment_time
                        > 3.0 / max(segment_sample_rate or 2.0, 0.001) + 1e-9
                    ):
                        # Retain both sides of a real source gap so the player
                        # never interpolates cars through missing time.
                        if previous_segment_frame is not None:
                            display_sampler.promote(previous_segment_frame, "gap")
                        keyframe = True
                        keyframe_reason = "gap"
                    display_sampler.observe(
                        frame,
                        keyframe=keyframe,
                        keyframe_reason=keyframe_reason,
                    )
                    previous_segment_frame = frame
                    previous_segment_signature = signature
                    previous_segment_time = session_time
            if previous_segment_frame is not None:
                display_sampler.promote(previous_segment_frame, "boundary")
            capture_segments.append(
                {
                    "index": segment_index,
                    "manifest": str(manifest_path),
                    "channels": segment_channels,
                    "verified_chunk_count": verified_chunk_count,
                    "frame_count": segment_frame_count,
                    "sample_rate_hz": segment_sample_rate,
                }
            )
        def finite_session_time(frame: Mapping[str, Any]) -> float | None:
            return _finite_number(frame.get("session_time_s"))

        deduplicated = display_sampler.finish()
        for frame in deduplicated:
            frame.pop("captured_at", None)
        finite_times = [key / 1_000_000.0 for key in sorted(finite_time_keys)]
        positive_deltas = [
            later - earlier
            for earlier, later in zip(finite_times, finite_times[1:])
            if math.isfinite(later - earlier) and later - earlier > 0
        ]
        if manifest_sample_rates:
            sample_rate_hz = float(statistics.median(manifest_sample_rates))
        elif len(positive_deltas) >= 2:
            sample_rate_hz = min(
                60.0,
                max(1.0, 1.0 / float(statistics.median(positive_deltas))),
            )
        else:
            sample_rate_hz = 2.0
        expected_interval_s = 1.0 / sample_rate_hz
        start_time = finite_times[0] if finite_times else None
        end_time = finite_times[-1] if finite_times else None
        expected_frame_count = (
            max(
                len(finite_times),
                int(round((end_time - start_time) * sample_rate_hz)) + 1,
            )
            if start_time is not None and end_time is not None
            else len(finite_times)
        )
        frame_fraction = (
            min(1.0, len(finite_times) / expected_frame_count)
            if expected_frame_count > 0
            else 0.0
        )
        gap_boundaries = [
            (earlier, later)
            for earlier, later in zip(finite_times, finite_times[1:])
            if later - earlier > expected_interval_s * 3.0 + 1e-9
        ]
        gaps = [later - earlier for earlier, later in gap_boundaries]
        gap_index = 0
        previous_display_time: float | None = None
        for frame in deduplicated:
            current_display_time = finite_session_time(frame)
            frame["gap_before"] = False
            if current_display_time is None:
                continue
            while gap_index < len(gap_boundaries) and gap_boundaries[gap_index][1] <= current_display_time + 1e-9:
                earlier, later = gap_boundaries[gap_index]
                if previous_display_time is not None and previous_display_time <= earlier + 1e-9:
                    frame["gap_before"] = True
                gap_index += 1
            previous_display_time = current_display_time
        display_deltas = []
        for prior, current in zip(deduplicated, deduplicated[1:]):
            earlier = finite_session_time(prior)
            later = finite_session_time(current)
            if (
                earlier is not None
                and later is not None
                and later > earlier
                and current.get("gap_before") is not True
            ):
                display_deltas.append(later - earlier)
        display_sample_rate_hz = (
            min(20.0, 1.0 / float(statistics.median(display_deltas)))
            if display_deltas
            else min(20.0, sample_rate_hz)
        )
        temporal_status = (
            "unavailable"
            if not finite_times
            else "partial"
            if gaps or frame_fraction < 0.9995
            else "recorded"
        )
        temporal_coverage = {
            "status": temporal_status,
            "recorded_frame_count": len(finite_times),
            "expected_frame_count": expected_frame_count,
            "recorded_fraction": round(frame_fraction, 4),
            "gap_count": len(gaps),
            "largest_gap_s": round(max(gaps), 3) if gaps else 0.0,
            "start_session_time_s": round(start_time, 3) if start_time is not None else None,
            "end_session_time_s": round(end_time, 3) if end_time is not None else None,
        }

        required = ("SessionTime", "SessionState", "CarIdxLapDistPct")
        channel_names = sorted(
            {
                *required,
                *(
                    channel
                    for segment in capture_segments
                    for channel in segment["channels"]
                ),
            }
        )
        segment_count = len(capture_segments)
        coverage_by_channel: dict[str, dict[str, Any]] = {}
        for channel in channel_names:
            recorded_segments = [
                segment
                for segment in capture_segments
                if segment["frame_count"] > 0
                and (segment["channels"].get(channel) or {}).get("recorded") is True
            ]
            recorded_segment_count = len(recorded_segments)
            segment_fraction = (
                recorded_segment_count / segment_count if segment_count else 0.0
            )
            recorded_fraction = min(segment_fraction, frame_fraction)
            all_segments_recorded = (
                segment_count > 0
                and recorded_segment_count == segment_count
                and temporal_status == "recorded"
            )
            status = (
                "recorded"
                if all_segments_recorded
                else "partial"
                if recorded_segment_count > 0
                else "unavailable"
            )
            reasons = [
                str((segment["channels"].get(channel) or {}).get("reason") or "").strip()
                for segment in capture_segments
                if (segment["channels"].get(channel) or {}).get("recorded") is not True
            ]
            reasons = [reason for reason in reasons if reason]
            if recorded_segment_count < segment_count:
                reasons.append(
                    f"{channel} was not recorded in "
                    f"{segment_count - recorded_segment_count} of {segment_count} capture segments."
                )
            if gaps:
                reasons.append(
                    f"The merged capture has {len(gaps)} session-time gap(s); "
                    "continuous channel coverage is not claimed."
                )
            coverage_by_channel[channel] = {
                "channel": channel,
                "status": status,
                "reason": "; ".join(dict.fromkeys(reasons)) or None,
                "recorded_segment_count": recorded_segment_count,
                "segment_count": segment_count,
                "recorded_fraction": round(recorded_fraction, 4),
                "all_segments_recorded": all_segments_recorded,
                "temporal_gap_count": len(gaps),
            }

        unavailable = [
            str(coverage_by_channel.get(channel, {}).get("reason") or f"{channel} was not captured.")
            for channel in required
            if coverage_by_channel.get(channel, {}).get("status") != "recorded"
        ]
        has_cars = any(frame.get("cars") or frame.get("car_rows") for frame in deduplicated)
        required_unavailable = any(
            coverage_by_channel.get(channel, {}).get("status") == "unavailable"
            for channel in required
        )
        status = "unavailable" if required_unavailable or not has_cars else (
            "partial"
            if temporal_status != "recorded"
            or not display_sampler.keyframes_preserved
            or any(item.get("status") != "recorded" for item in coverage_by_channel.values())
            else "usable"
        )
        if not has_cars:
            unavailable.append("No recorded competitor lap-distance rows were captured.")

        participant_coverage: list[dict[str, Any]] = []
        total_frame_count = raw_frame_count
        for car_index in sorted(participants_by_index):
            participant_frame_count = participant_frame_counts.get(car_index, 0)
            participant_segment_count = len(participant_segments.get(car_index, set()))
            frame_coverage_fraction = (
                participant_frame_count / total_frame_count if total_frame_count else 0.0
            )
            segment_coverage_fraction = (
                participant_segment_count / segment_count if segment_count else 0.0
            )
            participant_fraction = min(
                frame_coverage_fraction,
                segment_coverage_fraction,
                frame_fraction,
            )
            participant_status = (
                "recorded"
                if participant_frame_count
                and participant_frame_count == total_frame_count
                and participant_segment_count == segment_count
                and temporal_status == "recorded"
                else "partial"
                if participant_frame_count
                else "unavailable"
            )
            participant_coverage.append(
                {
                    "car_index": car_index,
                    "status": participant_status,
                    "recorded_frame_count": participant_frame_count,
                    "total_frame_count": total_frame_count,
                    "recorded_fraction": round(participant_fraction, 4),
                    "recorded_segment_count": participant_segment_count,
                    "segment_count": segment_count,
                    "first_session_time_s": (
                        round(participant_first_times[car_index], 3)
                        if car_index in participant_first_times
                        else None
                    ),
                    "last_session_time_s": (
                        round(participant_last_times[car_index], 3)
                        if car_index in participant_last_times
                        else None
                    ),
                }
            )

        for frame in deduplicated:
            frame.pop("_capture_segment", None)
        return {
            "schema_version": 1,
            "source": "durable_live_sdk_capture",
            "status": status,
            "unavailable_reasons": list(dict.fromkeys(unavailable)),
            "coverage": sorted(coverage_by_channel.values(), key=lambda item: item["channel"]),
            "temporal_coverage": temporal_coverage,
            "sample_rate_hz": sample_rate_hz,
            "representation": {
                "source_frame_count": len(finite_times),
                "display_frame_count": len(deduplicated) if status != "unavailable" else 0,
                "source_sample_rate_hz": sample_rate_hz,
                "display_sample_rate_hz": round(display_sample_rate_hz, 3),
                "frame_budget": _REPLAY_DISPLAY_HARD_FRAME_BUDGET,
                "decimated": len(deduplicated) < len(finite_times),
                "routine_interval_s": round(display_sampler.interval_s, 6),
                "keyframes_preserved": display_sampler.keyframes_preserved,
                "dropped_keyframe_count": display_sampler.dropped_keyframe_count,
            },
            "interpolation": "linear lap-distance interpolation between contiguous display frames; source gaps are never interpolated",
            "participant_count": len(participants_by_index),
            "player_car_index": player_index,
            "participants": [participants_by_index[index] for index in sorted(participants_by_index)],
            "car_columns": list(_REPLAY_CAR_COLUMNS),
            "participant_coverage": participant_coverage,
            "frames": deduplicated if status != "unavailable" else [],
            "frame_count": len(deduplicated) if status != "unavailable" else 0,
            "capture_manifests": manifests,
            "limitations": [
                "Only SHA-verified recorded values are present; reconnect and time-gap coverage is quantified explicitly.",
                "Routine motion is adaptively sampled for display; recorded events and flag, session-state, pit, surface, repair, tow, and gap-boundary keyframes are retained.",
                "Competitor fuel, tire wear, tire temperature, setup, and private penalties are not inferred.",
            ],
        }
