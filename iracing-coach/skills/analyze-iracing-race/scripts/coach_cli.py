"""Command-line interface for the local iRacing Coach workflow.

The CLI intentionally emits JSON for every successful command so it can be
used both by a person and as a deterministic fallback when the MCP server is
unavailable.  Garage61 credentials are configured only through the interactive
DPAPI prompt; this module never accepts a token as an argument or prints one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import secure_store  # noqa: E402
from native_events import (  # noqa: E402
    SUPPORTED_EVENT_SELECTION_MODES,
    SUPPORTED_EVENT_TYPES,
)
from storage import ArchiveStore  # noqa: E402


CommandHandler = Callable[[argparse.Namespace], Any]
CONTEXT_KEYS = (
    "season_key",
    "car_key",
    "track_key",
    "setup_type",
    "race_length_key",
)
SECRET_KEYS = {
    "authorization",
    "password",
    "pat",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


class CLIError(RuntimeError):
    """An actionable command-line input or workflow error."""


def _workflow_module() -> Any:
    """Import the workflow lazily so ``--help`` remains diagnostic-safe."""

    try:
        import workflow
    except ImportError as exc:
        raise CLIError(f"iRacing workflow module could not load: {exc}") from exc
    return workflow


def _bounded_int(minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return number

    return parse


def _bounded_float(minimum: float, maximum: float) -> Callable[[str], float]:
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a number") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum:g} and {maximum:g}"
            )
        return number

    return parse


def _bounded_text(minimum: int, maximum: int) -> Callable[[str], str]:
    def parse(value: str) -> str:
        text = str(value).strip()
        if not minimum <= len(text) <= maximum:
            raise argparse.ArgumentTypeError(
                f"must contain between {minimum} and {maximum} characters"
            )
        return text

    return parse


def _archive_store(args: argparse.Namespace) -> ArchiveStore:
    archive_root = getattr(args, "archive_root", None)
    return ArchiveStore(archive_root) if archive_root else ArchiveStore()


def _read_json_object(path: str, *, description: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise CLIError(f"{description} does not exist or is not a file: {target}")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError(f"Could not read {description}: {target}") from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            f"{description} is not valid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {target}"
        ) from exc
    if not isinstance(value, dict):
        raise CLIError(f"{description} must contain a JSON object: {target}")
    return value


def _read_context(value: str) -> dict[str, str]:
    """Read a context object from inline JSON or a JSON file."""

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = _read_json_object(value, description="Context file")
    if not isinstance(decoded, Mapping):
        raise CLIError("Context must be a JSON object.")
    missing = [key for key in CONTEXT_KEYS if key not in decoded]
    if missing:
        raise CLIError(f"Context is missing: {', '.join(missing)}")
    context: dict[str, str] = {}
    for key in CONTEXT_KEYS:
        normalized = str(decoded[key]).strip()
        if not normalized:
            raise CLIError(f"Context field {key!r} cannot be empty.")
        context[key] = normalized
    return context


def _context_from_args(
    args: argparse.Namespace, store: ArchiveStore
) -> dict[str, str]:
    analysis_path = getattr(args, "analysis", None)
    if analysis_path:
        analysis = _read_json_object(
            analysis_path, description="Analysis artifact"
        )
        return store.context_from_analysis(analysis)
    context = getattr(args, "context", None)
    if context:
        return _read_context(context)
    raise CLIError("Provide --analysis or --context.")


def _redact_secrets(value: Any) -> Any:
    """Remove credential-shaped fields before serializing command output."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.strip().lower().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith("_token"):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_secrets(child)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_secrets(child) for child in value]
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value, key=str)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _emit_json(value: Any, *, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    json.dump(
        _redact_secrets(value),
        stream,
        indent=2,
        ensure_ascii=False,
        default=_json_default,
    )
    stream.write("\n")
    stream.flush()


def _discover(args: argparse.Namespace) -> Any:
    return _workflow_module().discover_sessions_workflow(
        root=args.root,
        limit=args.limit,
        races_only=args.races_only,
    )


def _inventory(args: argparse.Namespace) -> Any:
    return _workflow_module().inventory_iracing_data_workflow(
        root=args.root,
        recent_limit=args.recent_limit,
        include_known_roots=args.include_known_roots,
    )


def _dashboard(args: argparse.Namespace) -> Any:
    return _workflow_module().companion_dashboard_workflow(
        root=args.root,
        archive_root=args.archive_root,
        limit=args.limit,
    )


def _analyze(args: argparse.Namespace) -> Any:
    return _workflow_module().analyze_race_workflow(
        selector=args.session,
        iracing_root=args.iracing_root,
        archive_root=args.archive_root,
        target_hz=args.target_hz,
    )


def _telemetry_channels(values: Sequence[str] | None) -> list[str]:
    channels: list[str] = []
    for value in values or ():
        for raw_name in str(value).split(","):
            name = raw_name.strip()
            if not name:
                continue
            if len(name) > 64:
                raise CLIError("Telemetry channel names may contain at most 64 characters.")
            if name not in channels:
                channels.append(name)
    if len(channels) > 12:
        raise CLIError("Telemetry queries may request at most 12 unique channels.")
    return channels


def _telemetry_query(args: argparse.Namespace) -> Any:
    return _workflow_module().telemetry_query_workflow(
        selector=args.session,
        iracing_root=args.iracing_root,
        archive_root=args.archive_root,
        mode=args.mode,
        channels=_telemetry_channels(args.channels),
        search=args.search,
        target_hz=args.target_hz,
        start_record=args.start_record,
        end_record=args.end_record,
        max_samples=args.max_samples,
    )


def _telemetry_event_types(values: Sequence[str] | None) -> list[str] | None:
    if not values:
        return None
    event_types: list[str] = []
    for value in values:
        for raw_name in str(value).split(","):
            name = raw_name.strip()
            if not name:
                continue
            if name not in SUPPORTED_EVENT_TYPES:
                raise CLIError(
                    "Telemetry event types may contain only: "
                    + ", ".join(SUPPORTED_EVENT_TYPES)
                    + "."
                )
            if name not in event_types:
                event_types.append(name)
    if not event_types:
        raise CLIError("Provide at least one telemetry event type.")
    return event_types


def _telemetry_events(args: argparse.Namespace) -> Any:
    return _workflow_module().native_event_search_workflow(
        selector=args.session,
        iracing_root=args.iracing_root,
        archive_root=args.archive_root,
        event_types=_telemetry_event_types(args.event_types),
        selection_mode=args.selection_mode,
        start_record=args.start_record,
        end_record=args.end_record,
        max_events=args.max_events,
        lap=args.lap,
        session_time_start=args.session_time_start,
        session_time_end=args.session_time_end,
        lap_distance_start=args.lap_distance_start,
        lap_distance_end=args.lap_distance_end,
    )


def _auth_status(args: argparse.Namespace) -> Any:
    return _workflow_module().garage61_status_workflow(
        archive_root=args.archive_root
    )


def _configure_auth(args: argparse.Namespace) -> dict[str, Any]:
    credential_path = secure_store.configure_interactively(
        path=args.credential_path
    )
    return {
        "configured": True,
        "credential_path": str(credential_path),
        "message": "Garage61 credential stored with Windows user-bound DPAPI.",
    }


def _garage61_sync(args: argparse.Namespace) -> Any:
    return _workflow_module().garage61_sync_workflow(
        analysis_path=args.analysis,
        archive_root=args.archive_root,
        maximum_laps=args.maximum_laps,
        download_telemetry=args.download_telemetry,
    )


def _cache_status(args: argparse.Namespace) -> Any:
    store = _archive_store(args)
    return store.cache_status(_context_from_args(args, store))


def _history(args: argparse.Namespace) -> Any:
    store = _archive_store(args)
    context = _context_from_args(args, store)
    return store.historical_runs(
        context,
        limit=args.limit,
        include_other_seasons=args.include_other_seasons,
    )


def _inline_json_object(value: str | None, *, description: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = _read_json_object(value, description=description)
    if not isinstance(decoded, Mapping):
        raise CLIError(f"{description} must be a JSON object.")
    return dict(decoded)


def _setup_catalog(args: argparse.Namespace) -> Any:
    return _workflow_module().catalog_iracing_setups_workflow(
        root=args.root,
        archive_root=args.archive_root,
        car=args.car,
        track=args.track,
        season=args.season,
        role=args.role,
        maximum_entries=args.maximum_entries,
    )


def _setup_package(args: argparse.Namespace) -> Any:
    return _workflow_module().build_open_setup_package_workflow(
        analysis_path=args.analysis,
        iracing_root=args.iracing_root,
        archive_root=args.archive_root,
        season=args.season,
        car=args.car,
        track=args.track,
        track_characteristics=_inline_json_object(
            args.track_characteristics,
            description="Track-characteristics JSON",
        ),
    )


def _setup_recommend(args: argparse.Namespace) -> Any:
    return _workflow_module().recommend_open_setup_tuning_workflow(
        analysis_path=args.analysis,
        symptoms=args.symptoms,
        archive_root=args.archive_root,
        package_id=args.package_id,
        maximum_changes=args.maximum_changes,
    )


def _setup_feedback(args: argparse.Namespace) -> Any:
    return _workflow_module().record_open_setup_feedback_workflow(
        experiment_id=args.experiment_id,
        outcome=args.outcome,
        notes=args.notes,
        archive_root=args.archive_root,
        result_analysis_path=args.result_analysis,
    )


def _setup_history(args: argparse.Namespace) -> Any:
    context = _read_context(args.context) if args.context else None
    return _workflow_module().iracing_setup_history_workflow(
        archive_root=args.archive_root,
        analysis_path=args.analysis,
        package_id=args.package_id,
        context=context,
        include_other_seasons=args.include_other_seasons,
        include_other_tracks=args.include_other_tracks,
        limit=args.limit,
    )


def _add_archive_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--archive-root",
        default=argparse.SUPPRESS,
        help="Override the durable report, cache, and history directory.",
    )


def _add_context_source(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--analysis",
        "--analysis-path",
        dest="analysis",
        metavar="PATH",
        help="Path to an archived analysis.json artifact.",
    )
    source.add_argument(
        "--context",
        "--context-json",
        "--context-file",
        dest="context",
        metavar="JSON_OR_PATH",
        help="Inline context JSON or a path to a context JSON file.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coach_cli.py",
        description=(
            "Discover and analyze local iRacing races, maintain coaching "
            "history, and manage Garage61 comparisons."
        ),
    )
    parser.add_argument(
        "--archive-root",
        help="Override the durable report, cache, and history directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory",
        help="Inventory useful local iRacing data and safe logging settings read-only.",
    )
    inventory.add_argument(
        "--root",
        help="Optional iRacing Documents root.",
    )
    inventory.add_argument(
        "--recent-limit",
        type=_bounded_int(1, 200),
        default=20,
        help="Recent references retained per data type (default: 20).",
    )
    inventory.set_defaults(include_known_roots=True)
    known_roots = inventory.add_mutually_exclusive_group()
    known_roots.add_argument(
        "--include-known-roots",
        dest="include_known_roots",
        action="store_true",
        help="Also stat-count known install and LocalAppData roots (default).",
    )
    known_roots.add_argument(
        "--documents-only",
        dest="include_known_roots",
        action="store_false",
        help="Limit inventory to the Documents iRacing root.",
    )
    inventory.set_defaults(handler=_inventory)

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Return the compact read-only backend snapshot used by a companion UI.",
    )
    _add_archive_root(dashboard)
    dashboard.add_argument(
        "--root",
        help="Optional iRacing Documents root or telemetry directory.",
    )
    dashboard.add_argument(
        "--limit",
        type=_bounded_int(1, 100),
        default=20,
        help="Maximum recent races, analyses, and tuning packages (default: 20).",
    )
    dashboard.set_defaults(handler=_dashboard)

    discover = subparsers.add_parser(
        "discover",
        help="Discover recorded sessions from embedded IBT metadata.",
    )
    discover.add_argument(
        "--root",
        help="Optional iRacing root or telemetry directory.",
    )
    discover.add_argument(
        "--limit",
        type=_bounded_int(1, 200),
        default=20,
        help="Maximum sessions to return (default: 20).",
    )
    discovery_filter = discover.add_mutually_exclusive_group()
    discovery_filter.add_argument(
        "--races-only",
        dest="races_only",
        action="store_true",
        help="Return only Race sessions (default).",
    )
    discovery_filter.add_argument(
        "--all-sessions",
        "--all",
        dest="races_only",
        action="store_false",
        help="Include practice, qualifying, and other session types.",
    )
    discover.set_defaults(handler=_discover, races_only=True)

    analyze = subparsers.add_parser(
        "analyze",
        help="Analyze and archive a Race session.",
    )
    _add_archive_root(analyze)
    analyze.add_argument(
        "--session",
        "--selector",
        dest="session",
        default="latest",
        help="'latest', an IBT path, or a SubSessionID (default: latest).",
    )
    analyze.add_argument(
        "--iracing-root",
        "--root",
        dest="iracing_root",
        help="Optional iRacing root or telemetry directory.",
    )
    analyze.add_argument(
        "--target-hz",
        type=_bounded_float(1, 60),
        default=20.0,
        help="Telemetry sampling frequency from 1 to 60 Hz (default: 20).",
    )
    analyze.set_defaults(handler=_analyze)

    telemetry_query = subparsers.add_parser(
        "telemetry-query",
        help="Search the complete IBT catalog, profile channels, or return a bounded slice.",
    )
    _add_archive_root(telemetry_query)
    telemetry_query.add_argument(
        "--session",
        "--selector",
        dest="session",
        default="latest",
        help="'latest', an IBT path, or a SubSessionID (default: latest).",
    )
    telemetry_query.add_argument(
        "--iracing-root",
        "--root",
        dest="iracing_root",
        help="Optional iRacing root or telemetry directory.",
    )
    telemetry_query.add_argument(
        "--mode",
        choices=("catalog", "profile", "slice"),
        default="catalog",
    )
    telemetry_query.add_argument(
        "--channel",
        "--channels",
        dest="channels",
        action="append",
        default=[],
        metavar="NAME[,NAME...]",
        help="Channel name; repeat or provide a comma-separated list (maximum 12).",
    )
    telemetry_query.add_argument(
        "--search",
        type=_bounded_text(1, 100),
        help="Catalog search text (maximum 100 characters).",
    )
    query_rate = telemetry_query.add_mutually_exclusive_group()
    query_rate.add_argument(
        "--target-hz",
        type=_bounded_float(1, 60),
        help="Decode at 1-60 Hz.",
    )
    query_rate.add_argument(
        "--native",
        dest="target_hz",
        action="store_const",
        const=None,
        help="Retain the source's native record rate (default).",
    )
    telemetry_query.add_argument(
        "--start-record",
        type=_bounded_int(0, 2_147_483_647),
        default=0,
        help="Per-source inclusive native record index (default: 0).",
    )
    telemetry_query.add_argument(
        "--end-record",
        type=_bounded_int(1, 2_147_483_647),
        help="Per-source exclusive native record index.",
    )
    telemetry_query.add_argument(
        "--max-samples",
        type=_bounded_int(1, 2000),
        default=1000,
        help="Maximum returned slice samples (default: 1000).",
    )
    telemetry_query.set_defaults(handler=_telemetry_query, target_hz=None)

    telemetry_events = subparsers.add_parser(
        "telemetry-events",
        help="Find native-rate telemetry events with exact lap, time, track position, and source-record context.",
    )
    _add_archive_root(telemetry_events)
    telemetry_events.add_argument(
        "--session",
        "--selector",
        dest="session",
        default="latest",
        help="'latest', an IBT path, or a SubSessionID (default: latest).",
    )
    telemetry_events.add_argument(
        "--iracing-root",
        "--root",
        dest="iracing_root",
        help="Optional iRacing root or telemetry directory.",
    )
    telemetry_events.add_argument(
        "--event",
        "--event-types",
        dest="event_types",
        action="append",
        default=[],
        metavar="TYPE[,TYPE...]",
        help=(
            "Event type; repeat or comma-separate values. Omit for all detectors. "
            "Supported: " + ", ".join(SUPPORTED_EVENT_TYPES)
        ),
    )
    telemetry_events.add_argument(
        "--selection-mode",
        choices=SUPPORTED_EVENT_SELECTION_MODES,
        default="chronological",
        help=(
            "Return earliest matches, or scan the full window and keep a "
            "balanced strongest subset (default: chronological)."
        ),
    )
    telemetry_events.add_argument(
        "--start-record",
        type=_bounded_int(0, 2_147_483_647),
        default=0,
        help="Per-source inclusive native record index (default: 0).",
    )
    telemetry_events.add_argument(
        "--end-record",
        type=_bounded_int(1, 2_147_483_647),
        help="Per-source exclusive native record index.",
    )
    telemetry_events.add_argument(
        "--max-events",
        type=_bounded_int(1, 500),
        default=200,
        help="Maximum events returned across selected source files (default: 200).",
    )
    telemetry_events.add_argument(
        "--lap",
        type=_bounded_int(0, 2_147_483_647),
        help="Return only events on this recorded lap number.",
    )
    telemetry_events.add_argument(
        "--session-time-start",
        type=_bounded_float(0, 1_000_000_000),
        help="Inclusive SessionTime lower bound in seconds.",
    )
    telemetry_events.add_argument(
        "--session-time-end",
        type=_bounded_float(0, 1_000_000_000),
        help="Exclusive SessionTime upper bound in seconds.",
    )
    telemetry_events.add_argument(
        "--lap-distance-start",
        type=_bounded_float(0, 1),
        help="Inclusive normalized lap-distance lower bound (0-1).",
    )
    telemetry_events.add_argument(
        "--lap-distance-end",
        type=_bounded_float(0, 1),
        help="Inclusive normalized lap-distance upper bound (0-1); values may wrap across start/finish.",
    )
    telemetry_events.set_defaults(handler=_telemetry_events)

    auth_status = subparsers.add_parser(
        "auth-status",
        help="Check the stored Garage61 credential and API access.",
    )
    _add_archive_root(auth_status)
    auth_status.set_defaults(handler=_auth_status)

    configure_auth = subparsers.add_parser(
        "configure-auth",
        help="Securely enter and store a Garage61 token with Windows DPAPI.",
        description=(
            "Open a no-echo interactive prompt and store the Garage61 token "
            "with Windows user-bound DPAPI. Tokens are never accepted as "
            "command arguments or included in output."
        ),
    )
    configure_auth.add_argument(
        "--credential-path",
        metavar="PATH",
        help="Override the encrypted credential file location.",
    )
    configure_auth.set_defaults(handler=_configure_auth)

    garage61_sync = subparsers.add_parser(
        "garage61-sync",
        help="Rank and archive comparable Garage61 laps.",
    )
    _add_archive_root(garage61_sync)
    garage61_sync.add_argument(
        "--analysis",
        "--analysis-path",
        dest="analysis",
        required=True,
        metavar="PATH",
        help="Path to the local analysis.json artifact.",
    )
    garage61_sync.add_argument(
        "--maximum-laps",
        "--max-laps",
        dest="maximum_laps",
        type=_bounded_int(1, 50),
        default=8,
        help="Maximum comparable laps to archive (default: 8).",
    )
    garage61_sync.set_defaults(download_telemetry=True)
    garage61_download = garage61_sync.add_mutually_exclusive_group()
    garage61_download.add_argument(
        "--download-telemetry",
        dest="download_telemetry",
        action="store_true",
        help="Download available comparison telemetry (default).",
    )
    garage61_download.add_argument(
        "--no-download-telemetry",
        "--no-download",
        dest="download_telemetry",
        action="store_false",
        help="Archive comparison metadata without telemetry CSV files.",
    )
    garage61_sync.set_defaults(handler=_garage61_sync)

    cache_status = subparsers.add_parser(
        "cache-status",
        help="Check the season/car/track knowledge cache.",
    )
    _add_archive_root(cache_status)
    _add_context_source(cache_status)
    cache_status.set_defaults(handler=_cache_status)

    history = subparsers.add_parser(
        "history",
        help="Return comparable archived race runs.",
    )
    _add_archive_root(history)
    _add_context_source(history)
    history.add_argument(
        "--limit",
        type=_bounded_int(1, 1000),
        default=200,
        help="Maximum historical runs to return (default: 200).",
    )
    history.add_argument(
        "--include-other-seasons",
        action="store_true",
        help="Include matching races from earlier seasons.",
    )
    history.set_defaults(handler=_history)

    setup_catalog = subparsers.add_parser(
        "setup-catalog",
        help="Catalog local STO and HTML setup exports read-only.",
    )
    _add_archive_root(setup_catalog)
    setup_catalog.add_argument("--root", help="iRacing Documents root or setups directory.")
    setup_catalog.add_argument("--car", help="Optional car name or iRacing car-folder filter.")
    setup_catalog.add_argument("--track", help="Optional intended-track filter.")
    setup_catalog.add_argument("--season", help="Optional season filter, such as 2026S3.")
    setup_catalog.add_argument(
        "--role", choices=("race", "qualifying", "endurance", "R", "Q", "E")
    )
    setup_catalog.add_argument(
        "--maximum-entries",
        type=_bounded_int(1, 1000),
        default=200,
    )
    setup_catalog.set_defaults(handler=_setup_catalog)

    setup_package = subparsers.add_parser(
        "setup-package",
        help="Build a season-scoped open-setup baseline package for a new week.",
    )
    _add_archive_root(setup_package)
    setup_package.add_argument("--analysis", help="Optional archived analysis.json for car/track/setup context.")
    setup_package.add_argument("--iracing-root", "--root", dest="iracing_root")
    setup_package.add_argument("--season", help="Current iRacing season, such as 2026S3.")
    setup_package.add_argument("--car", help="Required without --analysis.")
    setup_package.add_argument("--track", help="Required without --analysis.")
    setup_package.add_argument(
        "--track-characteristics",
        help="Inline JSON or JSON-file path with banking, speed, surface, and corner-pattern facts.",
    )
    setup_package.set_defaults(handler=_setup_package)

    setup_recommend = subparsers.add_parser(
        "setup-recommend",
        help="Plan and archive one open-setup tuning experiment after a race.",
    )
    _add_archive_root(setup_recommend)
    setup_recommend.add_argument("--analysis", required=True, help="Archived analysis.json from the open session.")
    setup_recommend.add_argument("--symptoms", required=True, help="Driver-reported entry/center/exit handling symptoms.")
    setup_recommend.add_argument("--package-id", help="Optional new-week baseline package ID.")
    setup_recommend.add_argument(
        "--maximum-changes",
        type=_bounded_int(1, 5),
        default=3,
        help="Maximum ranked alternatives returned; the experiment still applies one logical change.",
    )
    setup_recommend.set_defaults(handler=_setup_recommend)

    setup_feedback = subparsers.add_parser(
        "setup-feedback",
        help="Record the observed result of a setup experiment.",
    )
    _add_archive_root(setup_feedback)
    setup_feedback.add_argument("--experiment-id", required=True)
    setup_feedback.add_argument(
        "--outcome",
        required=True,
        choices=("improved", "worse", "no-change", "inconclusive"),
    )
    setup_feedback.add_argument("--notes", required=True)
    setup_feedback.add_argument(
        "--result-analysis",
        help="Optional analysis.json from the controlled result run/race.",
    )
    setup_feedback.set_defaults(handler=_setup_feedback)

    setup_history = subparsers.add_parser(
        "setup-history",
        help="Return season/car/track-scoped setup experiments and outcomes.",
    )
    _add_archive_root(setup_history)
    setup_history_source = setup_history.add_mutually_exclusive_group(required=True)
    setup_history_source.add_argument("--analysis", help="Archived analysis.json.")
    setup_history_source.add_argument("--package-id", help="Open-setup package ID.")
    setup_history_source.add_argument("--context", help="Inline context JSON or JSON-file path.")
    setup_history.add_argument("--include-other-seasons", action="store_true")
    setup_history.add_argument("--include-other-tracks", action="store_true")
    setup_history.add_argument("--limit", type=_bounded_int(1, 1000), default=100)
    setup_history.set_defaults(handler=_setup_history)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.handler
    try:
        result = handler(args)
        _emit_json(result)
    except KeyboardInterrupt:
        _emit_json(
            {"error": "Interrupted", "message": "Command cancelled."},
            stream=sys.stderr,
        )
        return 130
    except Exception as exc:
        message = str(exc).strip() or "The command failed without a diagnostic."
        _emit_json(
            {"error": type(exc).__name__, "message": message},
            stream=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
