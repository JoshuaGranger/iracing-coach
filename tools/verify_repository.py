#!/usr/bin/env python3
"""Verify repository contracts, fixtures, safeguards, and backend behavior."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = WORKSPACE_ROOT / "iracing-coach"
SCRIPT_ROOT = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
CONTRACT_ROOT = WORKSPACE_ROOT / "contracts"
FIXTURE_ROOT = WORKSPACE_ROOT / "test-data"
CONFIG_ROOT = WORKSPACE_ROOT / "config"
TOOL_ROOT = WORKSPACE_ROOT / "tools"

REQUIRED_FILES = (
    WORKSPACE_ROOT / "README.md",
    WORKSPACE_ROOT / "AGENTS.md",
    WORKSPACE_ROOT / "docs" / "README.md",
    CONTRACT_ROOT / "compatibility.json",
    CONTRACT_ROOT / "mcp-tools.v1.json",
    CONTRACT_ROOT / "dashboard-v1.schema.json",
    CONTRACT_ROOT / "discovery-v1.schema.json",
    CONTRACT_ROOT / "analyze-result-v1.schema.json",
    CONTRACT_ROOT / "race-card-v1.schema.json",
    CONTRACT_ROOT / "damage-repair-v1.schema.json",
    CONTRACT_ROOT / "track-phase-visualization-v1.schema.json",
    CONTRACT_ROOT / "ai-coaching-output.schema.json",
    CONTRACT_ROOT / "ai-tuning-output.schema.json",
    CONTRACT_ROOT / "telemetry-events-v1.schema.json",
    CONTRACT_ROOT / "setup-package-v1.schema.json",
    CONTRACT_ROOT / "tuning-recommendation-v1.schema.json",
    CONTRACT_ROOT / "garage61-auth-status-v1.schema.json",
    CONTRACT_ROOT / "theme-v1.schema.json",
    CONFIG_ROOT / "theme.dark.json",
    PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
    SCRIPT_ROOT / "mcp_server.py",
    SCRIPT_ROOT / "coach_cli.py",
    TOOL_ROOT / "mcp_e2e_smoke.py",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_fixture_shape(path: Path, required: tuple[str, ...]) -> None:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"Fixture must be an object: {path}")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"Fixture {path.name} lacks: {', '.join(missing)}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _theme_rgb(value: str) -> tuple[float, float, float]:
    _require(
        isinstance(value, str)
        and len(value) in {7, 9}
        and value.startswith("#"),
        f"Invalid theme color: {value!r}",
    )
    try:
        return tuple(int(value[index : index + 2], 16) / 255.0 for index in (1, 3, 5))
    except ValueError as exc:
        raise ValueError(f"Invalid theme color: {value!r}") from exc


def _theme_luminance(value: str) -> float:
    channels = tuple(
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in _theme_rgb(value)
    )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _theme_contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_theme_luminance(foreground), _theme_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _validate_theme_contract() -> None:
    theme = _load_json(CONFIG_ROOT / "theme.dark.json")
    _require(theme.get("schemaVersion") == 1, "theme schemaVersion must be 1")
    _require(theme.get("name") == "mineral-glass-dark", "unexpected theme name")
    _require(theme.get("mode") == "dark", "default theme must be dark")
    colors = theme.get("colors") or {}
    required = {
        "app",
        "navigation",
        "surface1",
        "surface2",
        "surface3",
        "textPrimary",
        "textSecondary",
        "textMuted",
        "accent",
        "accentFill",
        "focus",
        "chartBackground",
        "chartGrid",
    }
    _require(required.issubset(colors), "theme lacks required semantic colors")
    for name, value in colors.items():
        _theme_rgb(value)
        if name != "shadow":
            _require(value.upper() not in {"#000000", "#FFFFFF", "#000000FF", "#FFFFFFFF"}, f"theme token {name} uses forbidden pure black/white")
    for text_token in ("textPrimary", "textSecondary", "textMuted"):
        for surface_token in ("app", "surface1"):
            ratio = _theme_contrast(colors[text_token], colors[surface_token])
            _require(ratio >= 4.5, f"{text_token} contrast on {surface_token} is only {ratio:.2f}:1")
    button_ratio = _theme_contrast(colors["textPrimary"], colors["accentFill"])
    _require(button_ratio >= 4.5, f"primary button contrast is only {button_ratio:.2f}:1")
    motion = theme.get("motionMs") or {}
    _require(motion.get("honorReducedMotion") is True, "theme must honor reduced motion")
    _require(motion.get("continuousStatusAnimationAllowed") is False, "continuous status animation must remain disabled")
    _require(0 <= int(motion.get("hover", -1)) <= 160, "hover motion must stay gentle and short")
    _require(int(motion.get("structure", -1)) == 500, "structural motion must use the global 500 ms duration")
    _require("panel" not in motion, "structural motion must have one authoritative duration token")
    behavior = theme.get("behavior") or {}
    for false_key in (
        "pureBlackOrWhiteSurfacesAllowed",
        "colorMayBeSoleInformationCarrier",
        "globalBlockingSpinnerAllowed",
    ):
        _require(behavior.get(false_key) is False, f"theme behavior {false_key} must be false")
    for true_key in (
        "targetTraceRequiresUsableComparison",
        "continuousTireAgeRequiresBackendEvidence",
    ):
        _require(behavior.get(true_key) is True, f"theme behavior {true_key} must be true")


def _validate_damage_repair_truth_boundaries(path: Path) -> None:
    analyze = _load_json(path)
    damage = analyze.get("damage_repair") if isinstance(analyze, dict) else None
    _require(isinstance(damage, dict), f"{path.name} lacks damage_repair object")
    assert isinstance(damage, dict)
    _require(damage.get("schema_version") == 1, "damage fixture schema_version must be 1")

    summary = damage.get("summary")
    _require(isinstance(summary, dict), "damage fixture lacks summary")
    assert isinstance(summary, dict)
    scope_note = str(summary.get("totals_scope_note") or "").lower()
    _require("race-window" in scope_note and "pre-race" in scope_note, "damage totals must state race-window/pre-grid scope")
    _require(
        float(summary.get("all_recording_pit_road_time_s") or 0.0)
        >= float(summary.get("total_pit_road_time_s") or 0.0),
        "all-recording pit-road time cannot be below race-window pit-road time",
    )

    limitations = " ".join(str(item).lower() for item in damage.get("limitations") or ())
    _require("overlap" in limitations and "not additive" in limitations, "damage fixture must state that overlapping clocks are non-additive")
    _require("incident points" in limitations and "infer physical damage" in limitations, "incident points/pace must not become damage proof")

    incident = damage.get("incident_points")
    _require(isinstance(incident, dict) and incident.get("damage_proof") is False, "incident points must explicitly set damage_proof=false")
    for event in (incident or {}).get("events") or ():
        _require(event.get("damage_proof") is False, "incident event must explicitly set damage_proof=false")

    episodes = damage.get("episodes")
    _require(isinstance(episodes, list), "damage episodes must be an array")
    race_window_count = 0
    for episode in episodes:
        phase = episode.get("session_phase")
        _require(
            phase in {"recorded_race_window", "pre_race_or_grid", "post_race_recording", "race_window_unavailable"},
            "every damage episode must declare a supported session_phase",
        )
        race_window_count += phase == "recorded_race_window"
        timing = episode.get("timing") or {}
        for key in (
            "repair_active_time_s",
            "repair_timer_positive_observed_s",
            "repair_work_completed_s",
            "nonexclusive_note",
        ):
            _require(key in timing, f"damage episode timing lacks {key}")
        timer_positive = timing.get("repair_timer_positive_observed_s")
        progress_elapsed = timing.get("repair_active_time_s")
        if timer_positive is not None and progress_elapsed is not None:
            _require(float(timer_positive) >= float(progress_elapsed), "repair timer-positive elapsed must not be below countdown-progress elapsed")
        timing_note = str(timing.get("nonexclusive_note") or "").lower()
        _require("overlap" in timing_note and "not additive" in timing_note, "episode timing must state non-additivity")

        repair_parts = []
        for timer_name in ("mandatory_repair", "optional_repair"):
            timer = episode.get(timer_name) or {}
            for key in (
                "timer_positive_observed_s",
                "countdown_progress_elapsed_s",
                "repair_work_completed_s",
                "remaining_at_stall_exit_s",
                "completion_status",
            ):
                _require(key in timer, f"{timer_name} lacks {key}")
            if timer.get("repair_work_completed_s") is not None:
                repair_parts.append(float(timer["repair_work_completed_s"]))
        total_work = timing.get("repair_work_completed_s")
        if total_work is not None:
            _require(abs(float(total_work) - sum(repair_parts)) < 1e-6, "episode repair work must equal valid mandatory+optional countdown reduction")

        fast = episode.get("fast_repair") or {}
        if fast.get("request_confirmed_as_use"):
            _require(float(fast.get("used_count_delta") or 0.0) > 0.0, "fast-repair use requires a positive used-counter delta")
        evidence = episode.get("damage_evidence") or {}
        _require(evidence.get("severity") is None and evidence.get("location") is None, "recorded repair activity must not fabricate damage severity/location")
        context = episode.get("incident_points_context") or {}
        candidate = context.get("repair_correlated_candidate") or {}
        _require(context.get("damage_proof") is False, "episode incident context must not prove damage")
        _require(candidate.get("damage_onset_confirmed") is False, "candidate incident boundary must not claim exact damage onset")

    _require(summary.get("race_window_episodes") == race_window_count, "race-window episode count must match episode phases")


def _validate_track_visualization_truth_boundaries(path: Path) -> None:
    value = _load_json(path)
    _require(isinstance(value, dict), f"{path.name} must be an object")
    assert isinstance(value, dict)
    _require(value.get("schema_version") == 1, f"{path.name} schema_version must be 1")
    metadata = value.get("fixture_metadata") or {}
    _require(
        metadata.get("synthetic") is True
        and metadata.get("real_driver_or_setup_data") is False
        and metadata.get("production_use_allowed") is False,
        f"{path.name} must remain synthetic-only and production-disabled",
    )
    _require(metadata.get("visible_watermark") == "SYNTHETIC UI FIXTURE - NOT DRIVING ADVICE", f"{path.name} lacks mandatory synthetic watermark")

    slider = value.get("phase_slider") or {}
    phases = slider.get("values") or []
    _require(bool(phases), f"{path.name} must expose at least one supported phase")
    if slider.get("continuous_interpolation_supported") is False:
        _require(slider.get("mode") == "snap-to-supported-phases", "unsupported interpolation must force snap mode")
    phase_keys = {phase.get("key") for phase in phases}
    for phase in phases:
        laps = phase.get("source_lap_numbers") or []
        bounds = phase.get("green_lap_bounds") or []
        _require(bool(laps) and len(bounds) == 2, "phase provenance requires source laps and exact bounds")
        _require(min(laps) == bounds[0] and max(laps) == bounds[1], "phase source laps must match declared green-lap bounds")
        _require(phase.get("source_run_id") and phase.get("tire_set_id"), "phase must identify source run and tire set")

    traces = value.get("phase_traces") or []
    _require(isinstance(traces, list) and traces, f"{path.name} phase_traces must be a non-empty array")
    target_traces = [trace for trace in traces if trace.get("trace_role") == "best_supported_target"]
    for trace in traces:
        _require(trace.get("phase_key") in phase_keys, "trace phase_key must reference a supported slider phase")
        _require(bool(trace.get("source_reference_ids")), "every trace requires source provenance")
        _require(trace.get("source_run_id") and trace.get("tire_set_id") and trace.get("setup_fingerprint"), "every trace requires run/tire/setup identity")
        screening = trace.get("screening") or {}
        _require(screening.get("eligible") is True, "fixture traces must explicitly pass the clean-reference gate")
        for sample in trace.get("samples") or ():
            _require("steering_work_abs_rad" in sample and "steering_abs_rad" not in sample, "steering samples must use magnitude-only safe naming")

    comparison = value.get("comparison_quality") or {}
    target_policy = value.get("target_policy") or {}
    exact_allowed = (
        comparison.get("status") == "usable"
        and comparison.get("alignment_status") == "aligned"
        and comparison.get("representative_lap_status") == "usable"
        and comparison.get("setup_fingerprint_match") is True
        and bool(comparison.get("clean_reference_run_ids"))
        and target_policy.get("local_reference_run_eligible") is True
    )
    _require(comparison.get("optimality_claim_allowed") is False, "visualization may not claim an optimal lap")
    _require(target_policy.get("optimality_claim_allowed") is False, "target policy may not claim an optimal lap")
    _require(target_policy.get("exact_numeric_target_supported") is exact_allowed, "exact-target flag must equal the complete comparison/evidence gate")
    if exact_allowed:
        _require(bool(target_traces), "an exact-target case requires best-supported target traces")
        _require(target_policy.get("label") == "Best-supported target", "usable targets must be labeled best-supported, not optimal")
    else:
        _require(not target_traces, "an unusable comparison must not include target traces")
        _require(target_policy.get("label") == "Exact target unavailable", "unusable comparison must label exact target unavailable")
        for gate in (target_policy.get("metric_gates") or {}).values():
            _require(gate.get("exact_numeric_target_supported") is False, "unusable comparison must close every metric target gate")

    steering = value.get("steering_presentation") or {}
    steering_gate = (target_policy.get("metric_gates") or {}).get("steering") or {}
    if not (
        steering.get("signed_direction_available") is True
        and steering.get("steering_ratio_normalized") is True
    ):
        _require(steering.get("exact_steering_angle_target_supported") is False, "unsigned or unnormalized steering cannot become an exact angle target")
        _require(steering_gate.get("exact_numeric_target_supported") is False, "steering metric gate must remain closed")

    profile = value.get("track_profile") or {}
    geometry = profile.get("inside_outside_geometry") or {}
    groove = value.get("groove") or {}
    directional_supported = (
        geometry.get("status") == "calibrated"
        and geometry.get("directional_labels_supported") is True
        and groove.get("geometry_calibration_status") == "calibrated"
    )
    _require(groove.get("optimality_claim_allowed") is False, "groove payload may not claim optimality")
    if groove.get("status") == "directional_supported":
        _require(directional_supported and groove.get("directional_labels_supported") is True, "directional groove requires calibrated inside/outside geometry")
        _require(bool(groove.get("recommendations")), "supported groove case requires evidence-backed phase recommendations")
    else:
        _require(groove.get("directional_labels_supported") is False and not groove.get("recommendations"), "uncalibrated groove must expose no directional recommendations")
    if profile.get("geometry_source") == "normalized_distance_strip":
        _require(profile.get("display_mode") == "normalized_distance_strip" and not profile.get("shape"), "missing coordinates must render as an empty-shape distance strip")

    kinds = [item.get("kind") for item in value.get("interruptions") or ()]
    _require("repair-countdown" not in kinds, "ambiguous repair-countdown span is forbidden")
    if "repair-timer-positive" in kinds or "repair-countdown-progress" in kinds:
        _require("repair-timer-positive" in kinds and "repair-countdown-progress" in kinds, "repair timer-positive and countdown-progress spans must be distinct")
    for item in value.get("interruptions") or ():
        elapsed = float(item.get("elapsed_s") or 0.0)
        _require(abs(elapsed - (float(item.get("end_s")) - float(item.get("start_s")))) < 1e-6, "interruption elapsed_s must match its interval")
        if item.get("kind") == "repair-countdown-progress":
            _require("countdown_reduction_s" in item and item.get("timer_kind") in {"mandatory", "optional"}, "countdown-progress span requires timer identity and reduction")
        if item.get("kind") == "repair-timer-positive":
            _require("countdown_reduction_s" not in item, "timer-positive span must not imply repair work completed")


def _validate_backend_fixture_shapes() -> None:
    dashboard = _load_json(FIXTURE_ROOT / "dashboard-populated.json")
    races = dashboard.get("races") or []
    _require(bool(races), "populated dashboard must contain Race sessions")
    _require(dashboard.get("latest_race") == races[0], "dashboard latest_race must equal races[0]")
    for race in races:
        _require("car_path" in race and "analysis" in race, "dashboard Race lacks backend session/analysis fields")
        _require("car_name" not in race and "repair_affected" not in race, "dashboard fixture invents non-backend Race fields")
    summary = ((races[0].get("analysis") or {}).get("summary") or {})
    _require("damage_repair" not in summary, "dashboard analysis index does not currently return damage_repair")

    discovery = _load_json(FIXTURE_ROOT / "discovery.json")
    sessions = discovery.get("sessions") or []
    _require(bool(sessions), "discovery fixture must include the documented grouped Race")
    _require(discovery.get("latest_race") == sessions[0], "discovery latest_race must equal its first Race")
    for error in discovery.get("errors") or []:
        _require(all(key in error for key in ("kind", "valid", "path", "error_type", "error")), "discovery error lacks backend diagnostic fields")

    analyze = _load_json(FIXTURE_ROOT / "analyze-repair-heavy.json")
    _require("source_channel_coverage" in analyze, "analyze fixture lacks source_channel_coverage")
    _require((analyze.get("race_card") or {}).get("timing") == analyze.get("timing"), "inline Race Card must carry analyze timing")

    events = _load_json(FIXTURE_ROOT / "telemetry-events.json")
    event_summary = events.get("summary") or {}
    _require(all(key in event_summary for key in ("scan_complete", "candidate_event_count", "returned_event_count", "omitted_event_count")), "telemetry scan metadata must be nested under summary")
    _require("scan_complete" not in events and "status" not in events, "telemetry fixture contains invented top-level fields")
    for event in events.get("events") or []:
        _require("lap_distance_pct" in event and "lap_distance_fraction" in event, "telemetry event lacks backend lap-distance fields")
        _require("lap_dist_pct" not in event, "telemetry event uses non-backend lap_dist_pct")

    package = _load_json(FIXTURE_ROOT / "setup-package.json")
    _require(all(key in package for key in ("ok", "status", "package_id", "package_path", "context", "baseline", "baseline_confirmation", "qualifying", "donor", "simulator_loadable_setup_produced", "source_setup_files_modified")), "setup-package fixture is not a build_open_setup_package result")
    _require("schema_version" not in package and "identity" not in package, "setup-package fixture must not substitute the persisted package document")

    ready = _load_json(FIXTURE_ROOT / "setup-recommendation.json")
    blocked = _load_json(FIXTURE_ROOT / "setup-recommendation-damage-blocked.json")
    _require(ready.get("ok") is True and ready.get("status") == "planned" and ready.get("persisted") is True, "ready tuning fixture lacks persisted outer result")
    _require((ready.get("recommendation") or {}).get("status") == "ready", "ready tuning fixture lacks inner backend recommendation")
    _require(blocked.get("ok") is False and blocked.get("persisted") is False, "blocked tuning fixture must be non-persisted")
    _require((blocked.get("recommendation") or {}).get("status") == "needs-clean-repaired-run", "blocked tuning fixture lacks damage blocker")

    garage = _load_json(FIXTURE_ROOT / "garage61-auth-status-states.json")
    for state in garage.values():
        _require(all(key in state for key in ("ok", "configured", "status", "credential_storage", "archive_root", "api_request")), "Garage61 auth fixture lacks backend fields")

    jobs = _load_json(FIXTURE_ROOT / "ui-job-states.json")
    _require(jobs.get("fixture_kind") == "companion-ui-projection" and isinstance(jobs.get("states"), dict), "job-state projection must be explicitly marked as UI-only")


def _scan_tracked_files_for_secret_values() -> tuple[list[str], int]:
    findings: list[str] = []
    patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"Bearer\s+[A-Za-z0-9._~-]{20,}", re.IGNORECASE),
        re.compile(
            r'"(?:token|apiKey|password)"\s*:\s*"'
            r'(?!<|REDACTED|null|do-not-print|example|test|fake|dummy|placeholder|\[REDACTED\])'
            r'[^"\s]{12,}"',
            re.IGNORECASE,
        ),
    )
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=WORKSPACE_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Unable to enumerate Git-tracked files for secret scanning: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )
    files = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    workspace = WORKSPACE_ROOT.resolve()
    scanned = 0
    for relative in files:
        if not relative:
            continue
        path = (WORKSPACE_ROOT / relative).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"Tracked path escapes workspace: {relative}") from exc
        if not path.is_file() or path.suffix.lower() not in {
            ".cs", ".css", ".js", ".json", ".md", ".ps1", ".py",
            ".razor", ".txt", ".xaml", ".xml", ".yaml", ".yml",
        }:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in patterns):
            findings.append(str(path.relative_to(WORKSPACE_ROOT)))
    return findings, scanned


def _run_mcp_e2e_smoke() -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(TOOL_ROOT / "mcp_e2e_smoke.py"),
        ],
        cwd=WORKSPACE_ROOT,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Real MCP synthetic dashboard/analysis smoke failed: "
            + completed.stderr.strip()
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Real MCP smoke returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(f"Real MCP smoke returned an unexpected result: {value}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run the complete backend test suite.")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")

    missing = [str(path.relative_to(WORKSPACE_ROOT)) for path in REQUIRED_FILES if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required repository files: " + ", ".join(missing))

    for path in CONTRACT_ROOT.glob("*.json"):
        _load_json(path)
    for path in FIXTURE_ROOT.rglob("*.json") if FIXTURE_ROOT.is_dir() else ():
        _load_json(path)

    sys.path.insert(0, str(SCRIPT_ROOT))
    import mcp_server  # noqa: E402

    snapshot = _load_json(CONTRACT_ROOT / "mcp-tools.v1.json")
    if snapshot.get("tools") != mcp_server.TOOLS:
        raise ValueError("mcp-tools.v1.json is out of date")
    initialized = mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    ping = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
    )
    tools_list = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    )
    if not initialized or "result" not in initialized:
        raise RuntimeError("MCP initialize smoke failed")
    if ping != {"jsonrpc": "2.0", "id": 2, "result": {}}:
        raise RuntimeError("MCP ping smoke failed")
    listed = ((tools_list or {}).get("result") or {}).get("tools")
    if listed != mcp_server.TOOLS:
        raise RuntimeError("MCP tools/list smoke failed")

    fixture_expectations = {
        "dashboard-populated.json": ("ok", "contract_version", "races", "capabilities"),
        "dashboard-empty.json": ("ok", "contract_version", "races", "capabilities"),
        "discovery.json": ("selection_policy", "sessions", "session_count", "errors"),
        "analyze-repair-heavy.json": ("ok", "analysis_id", "damage_repair", "race_card", "timing"),
        "race-card.json": ("contract_version", "summary", "corner_playbook", "evidence_appendix"),
        "track-phase-visualization.json": ("track_profile", "phase_slider", "phase_traces", "interruptions"),
        "track-phase-visualization-unavailable.json": ("track_profile", "phase_slider", "phase_traces", "interruptions"),
        "setup-package.json": ("ok", "status", "package_id", "package_path", "context", "baseline"),
        "setup-recommendation.json": ("ok", "status", "persisted", "recommendation"),
        "setup-recommendation-damage-blocked.json": ("ok", "status", "persisted", "recommendation"),
        "telemetry-events.json": ("ok", "events", "selection_mode", "summary", "sources"),
        "garage61-auth-status-states.json": ("unconfigured", "pending", "offline", "permission_error"),
        "ui-job-states.json": ("fixture_kind", "projection", "states"),
        "mcp-tool-error.json": ("jsonrpc", "id", "result"),
    }
    for name, required in fixture_expectations.items():
        _validate_fixture_shape(FIXTURE_ROOT / name, required)

    _validate_damage_repair_truth_boundaries(FIXTURE_ROOT / "analyze-repair-heavy.json")
    _validate_track_visualization_truth_boundaries(FIXTURE_ROOT / "track-phase-visualization.json")
    _validate_track_visualization_truth_boundaries(
        FIXTURE_ROOT / "track-phase-visualization-unavailable.json"
    )
    _validate_backend_fixture_shapes()
    _validate_theme_contract()

    mcp_e2e = _run_mcp_e2e_smoke()

    secrets, tracked_files_scanned = _scan_tracked_files_for_secret_values()
    if secrets:
        raise RuntimeError("Potential secret values in tracked files: " + ", ".join(secrets))

    tests: dict[str, Any] = {"run": False}
    if args.full:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(PLUGIN_ROOT / "tests"),
                "-p",
                "test_*.py",
            ],
            cwd=WORKSPACE_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
        )
        tests = {
            "run": True,
            "exit_code": completed.returncode,
            "summary": completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "",
        }
        if completed.returncode != 0:
            print(completed.stdout, file=sys.stderr)
            raise RuntimeError("Backend unit suite failed")

    result = {
        "ok": True,
        "python": sys.version.split()[0],
        "plugin_version": _load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json").get("version"),
        "mcp_server_version": mcp_server.SERVER_VERSION,
        "mcp_tool_count": len(mcp_server.TOOLS),
        "contracts_loaded": len(list(CONTRACT_ROOT.glob("*.json"))),
        "fixtures_loaded": len(list(FIXTURE_ROOT.rglob("*.json"))),
        "tracked_files_scanned": tracked_files_scanned,
        "mcp_e2e": mcp_e2e,
        "tests": tests,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
