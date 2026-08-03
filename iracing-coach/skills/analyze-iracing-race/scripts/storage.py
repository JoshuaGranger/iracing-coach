"""Durable storage for iRacing Coach reports, history, and seasonal knowledge.

The archive is deliberately separate from the installed plugin. Updating or
reinstalling the plugin must never remove race history or cached references.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:  # Package import and direct script execution are both supported.
    from .path_security import local_path
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from path_security import local_path


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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_archive_root() -> Path:
    override = os.environ.get("IRACING_COACH_DATA")
    if override:
        return local_path(override, "IRACING_COACH_DATA")
    return local_path(r"C:\Users\joshu\Documents\iRacing Coach\data", "archive_root")


def safe_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80] or fallback


def stable_hash(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


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
        self.db_path = self.root / "history.sqlite3"

    def initialize(self) -> None:
        for directory in (
            self.root,
            self.reports_dir,
            self.cache_dir,
            self.tuning_dir,
            self.auth_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_db(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    analysis_id TEXT PRIMARY KEY,
                    analyzed_at TEXT NOT NULL,
                    session_start TEXT,
                    subsession_id TEXT,
                    session_id TEXT,
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
            connection.commit()
        finally:
            connection.close()

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
        if scheduled_laps:
            race_length_key = f"{int(float(scheduled_laps))}-laps"
        elif scheduled_minutes:
            race_length_key = f"{int(float(scheduled_minutes))}-minutes"
        else:
            race_length_key = "length-unknown"
        return {
            "season_key": safe_slug(season_key, "season-unknown"),
            "car_key": car_key,
            "track_key": track_key,
            "setup_type": setup_type,
            "race_length_key": race_length_key,
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
        subsession = identity.get("subsession_id") or identity.get("session_id") or "offline"
        stamp = safe_slug(analysis.get("analyzed_at") or utc_now())
        return self.reports_dir / context["season_key"] / safe_slug(subsession) / stamp

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
        race = analysis.get("race_summary", {})
        analysis_id = str(analysis["analysis_id"])
        summary_json = json.dumps(race, separators=(",", ":"), default=str)
        source_paths = analysis.get("source", {}).get("telemetry_files", [])
        source_path = str(source_paths[0]) if source_paths else None
        with self.connect() as connection:
            # One recorded session may be re-analyzed after the deterministic
            # engine changes.  Supersede its old index rows so a code revision
            # cannot masquerade as additional historical races or stints.
            if identity.get("subsession_id") is not None:
                connection.execute(
                    "DELETE FROM sessions WHERE analysis_id <> ? AND subsession_id = ?",
                    (analysis_id, str(identity.get("subsession_id"))),
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
                    season_key, car_key, track_key, setup_type, race_length_key,
                    source_path, report_path, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    analyzed_at=excluded.analyzed_at,
                    report_path=excluded.report_path,
                    summary_json=excluded.summary_json
                """,
                (
                    analysis_id, analysis.get("analyzed_at", utc_now()),
                    identity.get("session_start"), identity.get("subsession_id"),
                    identity.get("session_id"), context["season_key"],
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

    def recent_analyses(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return compact recent-session records for dashboards and selectors."""

        self.initialize()
        bounded_limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT analysis_id, analyzed_at, session_start, subsession_id,
                       session_id, season_key, car_key, track_key, setup_type,
                       race_length_key, source_path, report_path, summary_json
                FROM sessions
                ORDER BY COALESCE(session_start, analyzed_at) DESC, analyzed_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
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
        args.append(max(1, min(int(limit), 1000)))
        query = f"""
            SELECT s.analysis_id, s.analyzed_at, s.session_start,
                   s.subsession_id, s.session_id, s.source_path,
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
                f"subsession:{item.get('subsession_id')}"
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
