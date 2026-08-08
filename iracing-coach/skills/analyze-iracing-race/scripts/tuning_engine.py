"""Evidence-based open-setup tuning decisions for iRacing Coach.

This module deliberately produces *testable recommendations*, not modified
``.sto`` files.  A setup change is an experiment whose result must be linked
to the exact setup fingerprint, telemetry, conditions, and driver symptom.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TUNING_SCHEMA_VERSION = 1
TUNING_EVIDENCE_SCHEMA_VERSION = 2
TUNING_EVIDENCE_CONTRACT = "tuning_evidence_v2"
STRUCTURED_TUNING_RULESET_ID = "nascar-oreilly-xfinity-2026s3-v1"
_NATIVE_EVENT_SAMPLE_LIMIT = 40
_STRUCTURED_MINIMUM_PHASE_LAPS = 2
_STRUCTURED_MINIMUM_RUN_LAPS = _STRUCTURED_MINIMUM_PHASE_LAPS * 3
_AI_REQUEST_MAX_BYTES = 64 * 1024
_TUNING_GOALS = {"long-run-pace", "tire-life", "restart-pace", "stability"}

_RUN_PHASES = {"early", "middle", "late"}
_CORNER_PHASES = {"entry", "center", "exit", "whole"}
_SYMPTOM_ALIASES = {
    "good": "good",
    "neutral": "good",
    "tight": "tight",
    "understeer": "tight",
    "loose": "loose",
    "oversteer": "loose",
    "unstable": "unstable-braking",
    "unstable-braking": "unstable-braking",
    "wheel-hop": "wheel-hop-lock",
    "wheel-lock": "wheel-hop-lock",
    "wheel-hop-lock": "wheel-hop-lock",
    "wheelspin": "wheelspin",
    "cant-take-throttle": "cant-take-throttle",
    "cannot-take-throttle": "cant-take-throttle",
    "bottoming": "bottoming",
    "harsh": "harsh-skating",
    "skating": "harsh-skating",
    "harsh-skating": "harsh-skating",
    "low-grip": "low-grip",
    "other": "other",
}
_PROBLEM_SEVERITIES = {"mild", "moderate", "severe"}
_MAP_SOURCE_ALIASES = {
    "iracing-official": "iracing-official",
    "official-iracing": "iracing-official",
    "iracing-game": "iracing-official",
    "iracing-hud-capture": "iracing-hud-capture",
    "nascar-official": "nascar-official",
    "official-nascar": "nascar-official",
    "venue-official": "venue-official",
    "official-track": "venue-official",
    "verified-manual": "verified-manual",
    "user-confirmed": "verified-manual",
    "telemetry-derived": "telemetry-derived",
}


_PHASE_TERMS = {
    "entry": ("entry", "turn in", "turn-in", "braking", "brake zone", "corner in"),
    "center": ("center", "centre", "middle", "mid corner", "mid-corner", "apex"),
    "exit": (
        "exit", "drive off", "drive-off", "power down", "throttle", "on power",
        "loose off", "tight off", "free off", "push off", "off the corner",
    ),
    "long_run": (
        "long run", "long-run", "after ", "from lap", "since lap", "late run", "late-run", "wears out",
        "falls off", "heat cycle", "over a run",
    ),
    "bumps": ("bump", "curb", "kerb", "skips", "hops", "porpoise", "oscillat"),
}

_BALANCE_TERMS = {
    "tight": ("tight", "understeer", "understeering", "push", "plow", "won't rotate", "wont rotate"),
    "loose": ("loose", "oversteer", "oversteering", "free", "snap", "rear steps", "rear stepped"),
    "wheelspin": ("wheelspin", "wheel spin", "spins the rear", "lights up the rear"),
    "bottoming": ("bottom", "splitter hit", "splitter strike", "scrape", "ground strike"),
    "unstable": ("unstable", "nervous", "twitch", "wanders"),
}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _onset_lap(text: str) -> int | None:
    match = re.search(
        r"(?:after|by|around|from|since|starting(?:\s+(?:at|on))?)\s+"
        r"(?:lap\s*)?(\d{1,3})|\blap\s*(\d{1,3})\s*(?:onward|on\b)",
        text,
    )
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _has_balance_term(text: str) -> bool:
    lowered = text.casefold()
    return any(_contains(lowered, terms) for terms in _BALANCE_TERMS.values())


def _split_compound_symptoms(segment: str) -> list[str]:
    """Split conjunctions only when both sides describe a balance symptom."""

    parts: list[str] = []
    remainder = segment.strip()
    while remainder:
        split_at: tuple[int, int] | None = None
        for match in re.finditer(r"\s+\b(?:and|but|while|then)\b\s+", remainder, re.IGNORECASE):
            left = remainder[: match.start()].strip()
            right = remainder[match.end() :].strip()
            if _has_balance_term(left) and _has_balance_term(right):
                split_at = (match.start(), match.end())
                break
        if split_at is None:
            parts.append(remainder)
            break
        start, end = split_at
        parts.append(remainder[:start].strip())
        remainder = remainder[end:].strip()
    return [part for part in parts if part]


def parse_handling_symptoms(value: Any) -> list[dict[str, Any]]:
    """Normalize free-form driver feedback without pretending it is telemetry.

    The original wording is retained because distinctions such as "loose in"
    and "loose off" matter and because the agent may need to resolve ambiguity.
    """

    raw = _text(value)
    if not raw:
        return []
    result: list[dict[str, Any]] = []
    segments = [item.strip() for item in re.split(r"[;\n]+", raw) if item.strip()]
    for segment in segments:
        segment_onset = _onset_lap(segment.casefold())
        for chunk in _split_compound_symptoms(segment):
            lowered = chunk.casefold()
            phases = [name for name, terms in _PHASE_TERMS.items() if _contains(lowered, terms)]
            balances = [name for name, terms in _BALANCE_TERMS.items() if _contains(lowered, terms)]
            onset = _onset_lap(lowered)
            if onset is None:
                onset = segment_onset
            if onset is not None and "long_run" not in phases:
                phases.append("long_run")
            result.append(
                {
                    "reported": chunk,
                    "phases": phases or ["unspecified"],
                    "balances": balances or ["unspecified"],
                    "onset_lap": onset,
                    "source": "driver-report",
                }
            )
    return result


_DONOR_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "donor": "Atlanta",
        "family": "pack-superspeedway",
        "terms": ("pack", "restricted", "superspeedway", "draft", "dirty air", "yaw"),
        "reason": "pack racing, drag, yaw stability, and dirty-air platform control",
    },
    {
        "donor": "Indianapolis",
        "family": "flat-high-speed-discrete-corners",
        "terms": ("flat high speed", "flat-high-speed", "discrete corner", "long straight", "pocono", "rectangle"),
        "reason": "separate high-speed brake/turn/exit events followed by long straights",
    },
    {
        "donor": "New Hampshire",
        "family": "flat-brake-and-drive",
        "terms": ("paperclip", "heavy braking", "brake and drive", "brake-and-drive", "very flat", "traction track"),
        "reason": "flat braking zones, patient centers, and drive-off traction",
    },
    {
        "donor": "Iowa",
        "family": "compact-moderate-banked",
        "terms": ("compact", "short oval", "moderate bank", "high corner duty", "progressive bank", "iowa"),
        "reason": "moderate banking with a high percentage of the lap spent cornering",
    },
    {
        "donor": "Chicagoland",
        "family": "flowing-intermediate",
        "terms": ("flowing", "1.5 mile", "1.5-mile", "intermediate", "continuous load", "sustained lateral"),
        "reason": "persistent lateral load with different transient demands at each end",
    },
    {
        "donor": "Michigan",
        "family": "smooth-wide-high-speed",
        "terms": ("smooth", "wide", "unrestricted", "high speed oval", "high-speed oval", "multi groove", "multi-groove"),
        "reason": "smooth, wide, sustained high-speed aero load",
    },
    {
        "donor": "Coronado",
        "family": "bumpy-street-road",
        "terms": ("street", "bumpy", "90 degree", "90-degree", "chicane", "runway", "rough"),
        "reason": "bumps, curbs, sharp direction changes, and repeated traction zones",
    },
    {
        "donor": "Sonoma",
        "family": "flowing-elevated-road",
        "terms": ("road course", "road-course", "elevation", "crest", "compression", "flowing road", "loaded medium"),
        "reason": "elevation and sustained medium-speed left/right load",
    },
)


def choose_oreilly_donor(track_characteristics: Any) -> dict[str, Any]:
    """Choose a 26S3 O'Reilly Supra donor family from supplied track facts.

    This is a fallback for a new track.  An exact current-season setup always
    outranks a donor, and the caller must still verify body/package legality.
    """

    if isinstance(track_characteristics, Mapping):
        text = " ".join(
            f"{key} {value}" for key, value in track_characteristics.items()
            if value not in (None, "", [], {})
        ).casefold()
    else:
        text = _text(track_characteristics).casefold()
    scored: list[tuple[int, int, dict[str, Any], list[str]]] = []
    for index, family in enumerate(_DONOR_FAMILIES):
        matches = [term for term in family["terms"] if term in text]
        scored.append((len(matches), -index, family, matches))
    score, _, family, matches = max(scored, key=lambda item: (item[0], item[1]))
    if score == 0:
        return {
            "status": "needs-track-classification",
            "donor": None,
            "family": None,
            "reason": (
                "No donor is defensible until banking, peak/minimum speed, corner duty, "
                "surface/curbs, and left-only versus left/right are known."
            ),
            "matched_characteristics": [],
        }
    return {
        "status": "classified",
        "donor": family["donor"],
        "family": family["family"],
        "reason": family["reason"],
        "matched_characteristics": matches,
        "warning": "Transfer the tuning logic, not an assumed tech-legal .sto file.",
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _wear_summary(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for run in analysis.get("runs") or []:
        if not isinstance(run, Mapping):
            continue
        observation = run.get("tire_observation")
        if not isinstance(observation, Mapping):
            continue
        remaining = observation.get("remaining") or observation.get("remaining_percent") or {}
        if not isinstance(remaining, Mapping):
            remaining = {}
        numeric = {str(key).upper(): _number(value) for key, value in remaining.items()}
        numeric = {key: value for key, value in numeric.items() if value is not None}
        if not numeric:
            continue
        most_worn = min(numeric, key=numeric.get)
        result.append(
            {
                "run_number": run.get("run_number"),
                "most_worn_tire": most_worn,
                "remaining_percent": numeric[most_worn],
                "measurement": "discrete-post-service",
            }
        )
    return result


def _bounded_native_event_evidence(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep cached event context compact and explicit about evidentiary limits."""

    if not isinstance(value, Mapping):
        return {
            "status": "not-searched",
            "cache_only": True,
            "detector_invoked_by_tuning": False,
            "event_count": 0,
            "counts_by_type": {},
            "event_samples": [],
            "limitations": [
                "Run a bounded native telemetry event search when exact-record A/B alignment would help the diagnosis."
            ],
        }
    raw_counts = value.get("counts_by_type")
    counts: dict[str, int] = {}
    if isinstance(raw_counts, Mapping):
        for key, count in raw_counts.items():
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                counts[str(key)] = count
    samples: list[dict[str, Any]] = []
    for raw in value.get("event_samples") or ():
        if not isinstance(raw, Mapping) or len(samples) >= _NATIVE_EVENT_SAMPLE_LIMIT:
            continue
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else {}
        measurements = raw.get("measurements") if isinstance(raw.get("measurements"), Mapping) else {}
        compact_measurements = {
            str(key): item
            for key, item in measurements.items()
            if str(key) in {
                "channel",
                "value",
                "absolute_value",
                "threshold",
                "unit",
                "direction",
                "vehicle_speed_mps",
                "brake",
                "on_pit_state",
                "lap_distance_bin",
                "peak_threshold_score",
            }
            and not isinstance(item, (Mapping, list, tuple, set))
        }
        per_wheel = measurements.get("per_wheel")
        if isinstance(per_wheel, Mapping):
            compact_measurements["per_wheel"] = {
                str(wheel): {
                    str(key): item
                    for key, item in values.items()
                    if str(key) in {
                        "delta", "threshold", "baseline_lap_count", "ratio_vs_vehicle_speed"
                    }
                    and not isinstance(item, (Mapping, list, tuple, set))
                }
                for wheel, values in list(per_wheel.items())[:4]
                if isinstance(values, Mapping)
            }
        samples.append(
            {
                "event_type": raw.get("event_type"),
                "source_sha256": raw.get("source_sha256"),
                "source_record_index": raw.get("source_record_index"),
                "session_time_s": raw.get("session_time_s"),
                "lap": raw.get("lap"),
                "lap_distance_fraction": raw.get("lap_distance_fraction"),
                "evidence": {
                    "label": evidence.get("label"),
                    "measured_channels": list(evidence.get("measured_channels") or ())[:12],
                    "method": evidence.get("method"),
                    "limitation": evidence.get("limitation"),
                },
                "measurements": compact_measurements,
            }
        )
    limitations = [
        str(item)[:500] for item in value.get("limitations") or ()
        if str(item).strip()
    ][:10]
    raw_sources = value.get("source_sha256")
    if isinstance(raw_sources, str):
        source_sha256 = [raw_sources]
    elif isinstance(raw_sources, Sequence):
        source_sha256 = [str(item) for item in list(raw_sources)[:8]]
    else:
        source_sha256 = []
    reported_count = value.get("event_count")
    event_count = (
        reported_count
        if isinstance(reported_count, int) and not isinstance(reported_count, bool) and reported_count >= 0
        else sum(counts.values())
    )
    return {
        "status": str(value.get("status") or "not-searched"),
        "cache_only": bool(value.get("cache_only", True)),
        "detector_invoked_by_tuning": False,
        "source_sha256": source_sha256,
        "cache_files_used": list(value.get("cache_files_used") or ())[:12],
        "queries": list(value.get("queries") or ())[:12],
        "event_count": event_count,
        "counts_by_type": counts,
        "event_samples": samples,
        "event_samples_truncated": bool(value.get("event_samples_truncated")),
        "limitations": limitations,
    }


def setup_telemetry_evidence(
    analysis: Mapping[str, Any],
    native_event_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    evidence = analysis.get("setup_telemetry")
    if not isinstance(evidence, Mapping):
        evidence = {}
    damage_repair = analysis.get("damage_repair")
    damage_repair = damage_repair if isinstance(damage_repair, Mapping) else {}
    return {
        "setup_name": identity.get("setup_name"),
        "setup_fingerprint": identity.get("setup_fingerprint"),
        "embedded_setup_available": bool(identity.get("setup")),
        "dynamic_channels_available": list(evidence.get("available_channels") or []),
        "platform": dict(evidence.get("platform") or {}),
        "shocks": dict(evidence.get("shocks") or {}),
        "tires": dict(evidence.get("tires") or {}),
        "tire_wear": _wear_summary(analysis),
        "damage_repair": {
            "status": damage_repair.get("status"),
            "summary": dict(damage_repair.get("summary") or {}),
            "incident_points": dict(damage_repair.get("incident_points") or {}),
            "limitation": (
                "Repair/tow state can invalidate setup A/B evidence but does not identify the damaged component or exact pace cost."
            ),
        },
        "native_events": _bounded_native_event_evidence(native_event_evidence),
        "limits": list(evidence.get("limits") or [
            "Telemetry cannot uniquely identify which setup parameter caused a handling symptom.",
            "Setup effects are strongest when compared in controlled A/B runs with matched fuel, tires, weather, and line.",
        ]),
    }


def _native_marker_evidence(
    telemetry: Mapping[str, Any], *event_types: str
) -> list[str]:
    native = telemetry.get("native_events")
    if not isinstance(native, Mapping) or native.get("status") != "available":
        return []
    counts = native.get("counts_by_type") if isinstance(native.get("counts_by_type"), Mapping) else {}
    present = [(name, int(counts.get(name) or 0)) for name in event_types if counts.get(name)]
    if not present:
        return []
    labels = ", ".join(f"{name} ({count})" for name, count in present)
    result = [
        f"cached native-rate event markers available for exact-record A/B alignment: {labels}"
    ]
    if any(name == "wheel_speed_divergence" for name, _ in present):
        result.append(
            "wheel-speed divergence is a calibrated proxy, not proof of lock, spin, tire wear, or setup cause"
        )
    return result


def _native_verify_steps(
    telemetry: Mapping[str, Any], *event_types: str
) -> tuple[str, ...]:
    markers = _native_marker_evidence(telemetry, *event_types)
    if not markers:
        return ()
    requested = set(event_types)
    steps: list[str] = []
    if requested & {"brake_onset", "brake_release"}:
        steps.append("align raw brake/steering traces at cached native event records")
    if "steering_torque_peak" in requested:
        steps.append("compare raw steering-torque and angle traces around cached peaks")
    if "shock_velocity_peak" in requested:
        steps.append("compare same-location raw shock-velocity traces around cached peaks")
    if "wheel_speed_divergence" in requested:
        steps.append("inspect raw wheel speeds around proxy events before labeling lock or wheelspin")
    return tuple(steps)


def _builder_direction(notes: str, direction: str) -> str | None:
    if not notes:
        return None
    normalized = re.sub(r"\s+", " ", notes).strip()
    pattern = re.compile(
        rf"(?:to\s+{re.escape(direction)}|{re.escape(direction)}(?:er|en)?)[,:\s-]+(.+?)(?=(?:to\s+(?:tighten|loosen)|\*\*|$))",
        flags=re.I,
    )
    match = pattern.search(normalized)
    return match.group(0).strip(" -") if match else None


def _proposal(
    *,
    system: str,
    change: str,
    predicted_effect: str,
    evidence: Sequence[str],
    risk: str,
    verify: Sequence[str],
    source: str,
    confidence: str,
) -> dict[str, Any]:
    return {
        "system": system,
        "change": change,
        "predicted_effect": predicted_effect,
        "evidence": list(evidence),
        "risk": risk,
        "verify": list(verify),
        "source": source,
        "confidence": confidence,
    }


def _symptom_keys(symptoms: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for symptom in symptoms:
        for phase in symptom.get("phases") or ("unspecified",):
            for balance in symptom.get("balances") or ("unspecified",):
                keys.add((str(phase), str(balance)))
    return keys


def recommend_tuning(
    analysis: Mapping[str, Any],
    symptoms: Any,
    *,
    builder_notes: str = "",
    setup_comparison: Mapping[str, Any] | None = None,
    previous_experiments: Sequence[Mapping[str, Any]] = (),
    maximum_changes: int = 3,
    native_event_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative, ordered tuning experiment for an open setup."""

    maximum_changes = max(1, min(int(maximum_changes), 5))
    parsed = parse_handling_symptoms(symptoms)
    keys = _symptom_keys(parsed)
    telemetry = setup_telemetry_evidence(analysis, native_event_evidence)
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    is_fixed = identity.get("is_fixed_setup")
    blockers: list[str] = []
    if is_fixed is True:
        blockers.append("The analyzed session is fixed setup; garage tuning recommendations are advisory for a later open session only.")
    elif is_fixed is not False:
        blockers.append("The analyzed session is not confirmed as open setup; do not issue a garage change until setup type is known.")
    if not parsed:
        blockers.append("No driver handling symptom was supplied; do not change the setup from telemetry alone.")
    damage_summary = telemetry.get("damage_repair", {}).get("summary", {})
    repair_blocks = any(
        (_number(damage_summary.get(key)) or 0.0) > 0.0
        for key in (
            "tow_episodes",
            "recorded_repair_episodes",
            "repair_required_flag_episodes",
            "confirmed_fast_repair_uses",
        )
    )
    if repair_blocks:
        blockers.append(
            "Recorded tow/repair context makes this session unsuitable for a controlled setup conclusion; validate the symptom in a finalized clean run after repair."
        )

    proposals: list[dict[str, Any]] = []
    evidence_base = [
        f"driver report: {item['reported']}" for item in parsed
    ]
    platform = telemetry.get("platform") or {}
    splitter_min = _number(platform.get("center_front_splitter_min_in"))

    if any(balance == "bottoming" or phase == "bumps" and balance == "bottoming" for phase, balance in keys):
        proposals.append(_proposal(
            system="aero-platform",
            change="Add one equal front-packer step, then re-pass tech and restore the intended static heights/preload.",
            predicted_effect="Engage front support earlier and increase minimum dynamic splitter clearance.",
            evidence=(
                evidence_base
                + ([f"recorded splitter minimum {splitter_min:.3f} in"] if splitter_min is not None else [])
                + _native_marker_evidence(telemetry, "shock_velocity_peak")
            ),
            risk="Too much packer can hold the front too high, transfer load through the ARB, and create understeer.",
            verify=(
                "minimum center-front splitter height",
                "LF/RF dynamic heights",
                "normal-braking versus panic-braking contact",
                *_native_verify_steps(telemetry, "shock_velocity_peak"),
            ),
            source="vehicle-family tuning rule",
            confidence="high" if splitter_min is not None else "medium",
        ))

    if ("bumps", "unspecified") in keys or any(phase == "bumps" and balance != "bottoming" for phase, balance in keys):
        proposals.append(_proposal(
            system="high-speed-damping",
            change="Reduce high-speed compression one click at the affected axle; leave slope unchanged for the first A/B run.",
            predicted_effect="Let the tire follow a curb or sharp bump instead of skipping across it.",
            evidence=evidence_base + _native_marker_evidence(telemetry, "shock_velocity_peak"),
            risk="Less high-speed compression can increase travel and cause bottoming on the largest event.",
            verify=(
                "wheel contact/steering kick at the same bump",
                "shock-velocity peak",
                "splitter and rear-height minima",
                *_native_verify_steps(telemetry, "shock_velocity_peak"),
            ),
            source="vehicle-family tuning rule",
            confidence="medium",
        ))

    # Entry comes before center and exit in the diagnosis sequence.
    if ("entry", "loose") in keys or ("entry", "unstable") in keys:
        proposals.append(_proposal(
            system="braking-balance",
            change="Move front brake bias forward by one garage step for the first test.",
            predicted_effect="Stabilize the rear during brake application and release.",
            evidence=evidence_base + _native_marker_evidence(
                telemetry, "brake_onset", "brake_release", "wheel_speed_divergence"
            ),
            risk="Front locking, longer stopping distance, or a tighter center.",
            verify=(
                "rear wheel lock/wheel hop",
                "brake-release point",
                "center rotation",
                *_native_verify_steps(
                    telemetry, "brake_onset", "brake_release", "wheel_speed_divergence"
                ),
            ),
            source="vehicle-family tuning rule",
            confidence="medium",
        ))
    elif ("entry", "tight") in keys:
        proposals.append(_proposal(
            system="braking-balance",
            change="After confirming the brake release is not the cause, move front brake bias rearward by one garage step.",
            predicted_effect="Add rotation under braking without changing the steady-state platform.",
            evidence=evidence_base + _native_marker_evidence(
                telemetry, "brake_onset", "brake_release", "wheel_speed_divergence"
            ),
            risk="Rear lock or wheel hop; reverse immediately if either appears.",
            verify=(
                "brake-release overlap",
                "rear wheel lock/wheel hop",
                "entry yaw and minimum speed",
                *_native_verify_steps(
                    telemetry, "brake_onset", "brake_release", "wheel_speed_divergence"
                ),
            ),
            source="vehicle-family tuning rule",
            confidence="low" if not telemetry.get("dynamic_channels_available") else "medium",
        ))

    if ("center", "tight") in keys:
        note = _builder_direction(builder_notes, "loosen")
        proposals.append(_proposal(
            system="static-balance",
            change=(
                f"Use the setup builder's loosen sequence one garage step at a time: {note}"
                if note else
                "Reduce crossweight by one small garage step; keep ride heights and front preload at the baseline values."
            ),
            predicted_effect="Free center rotation and reduce sustained RF scrub.",
            evidence=evidence_base + _native_marker_evidence(telemetry, "steering_torque_peak"),
            risk="A nervous entry or reduced drive-off stability; do not combine with a track-bar change in the same test.",
            verify=(
                "center steering angle",
                "RF versus RR temperature/wear",
                "initial-throttle stability",
                "dynamic heights and tech",
                *_native_verify_steps(telemetry, "steering_torque_peak"),
            ),
            source="setup-builder-note" if note else "vehicle-family tuning rule",
            confidence="high" if note else "medium",
        ))
    elif ("center", "loose") in keys:
        note = _builder_direction(builder_notes, "tighten")
        proposals.append(_proposal(
            system="static-balance",
            change=(
                f"Use the setup builder's tighten sequence one garage step at a time: {note}"
                if note else
                "Increase crossweight by one small garage step; keep ride heights and front preload at the baseline values."
            ),
            predicted_effect="Add center and initial-throttle stability.",
            evidence=evidence_base + _native_marker_evidence(telemetry, "steering_torque_peak"),
            risk="Creating a center push and increasing RF scrub.",
            verify=(
                "center steering angle",
                "RF versus RR temperature/wear",
                "minimum speed",
                "dynamic heights and tech",
                *_native_verify_steps(telemetry, "steering_torque_peak"),
            ),
            source="setup-builder-note" if note else "vehicle-family tuning rule",
            confidence="high" if note else "medium",
        ))

    if ("exit", "loose") in keys or ("exit", "wheelspin") in keys:
        proposals.append(_proposal(
            system="rear-roll-geometry",
            change="Lower both rear track-bar ends one small equal step; preserve rake for the first test.",
            predicted_effect="Add lateral rear grip and drive-off security.",
            evidence=evidence_base + _native_marker_evidence(
                telemetry, "wheel_speed_divergence", "steering_torque_peak"
            ),
            risk="Over-tightening the center or raising rear tire load/heat elsewhere.",
            verify=(
                "throttle pickup",
                "exit steering corrections",
                "RR temperature/wear",
                "center balance",
                *_native_verify_steps(
                    telemetry, "wheel_speed_divergence", "steering_torque_peak"
                ),
            ),
            source="vehicle-family tuning rule",
            confidence="medium",
        ))
    elif ("exit", "tight") in keys:
        proposals.append(_proposal(
            system="static-balance",
            change="Reduce crossweight one small step before adding track-bar rake.",
            predicted_effect="Let the car finish rotation as steering unwinds.",
            evidence=evidence_base + _native_marker_evidence(telemetry, "steering_torque_peak"),
            risk="Entry nervousness or throttle oversteer if the apparent exit push is actually a center push caused by line/inputs.",
            verify=(
                "center balance first",
                "steering unwind",
                "throttle pickup",
                "RF temperature/wear",
                *_native_verify_steps(telemetry, "steering_torque_peak"),
            ),
            source="vehicle-family tuning rule",
            confidence="medium",
        ))

    # Long-run/RF evidence is diagnostic; driver load can be the primary cause.
    wears = telemetry.get("tire_wear") or []
    rf_worn = any(str(item.get("most_worn_tire")).upper() == "RF" for item in wears if isinstance(item, Mapping))
    if any(phase == "long_run" for phase, _ in keys) and rf_worn and not any(item["system"] == "static-balance" for item in proposals):
        proposals.append(_proposal(
            system="long-run-balance",
            change="Run one controlled stint with gentler brake release and less combined brake/steering load before changing hardware; if the RF trend remains, reduce crossweight one small step.",
            predicted_effect="Separate driver-induced RF overload from a setup-induced center push, then free the car only if the evidence persists.",
            evidence=(
                evidence_base
                + ["RF was the most worn tire at a discrete post-service reading"]
                + _native_marker_evidence(telemetry, "brake_onset", "brake_release")
            ),
            risk="Changing the setup first can hide an entry technique problem and make exit balance worse.",
            verify=(
                "early versus late brake-steer overlap",
                "pace falloff",
                "post-service RF remaining",
                "center steering demand",
                *_native_verify_steps(telemetry, "brake_onset", "brake_release"),
            ),
            source="race telemetry plus tuning rule",
            confidence="medium",
        ))

    # Deduplicate systems while preserving diagnosis order.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in proposals:
        key = (item["system"], item["change"])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    selected = [] if repair_blocks else unique[:maximum_changes]
    comparison = dict(setup_comparison or {})
    successful_history = [
        dict(item) for item in previous_experiments
        if isinstance(item, Mapping) and str(item.get("outcome", "")).casefold() in {"improved", "better", "success"}
    ]
    if not parsed:
        status = "needs-driver-feedback"
    elif is_fixed is True:
        status = "advisory-fixed-session"
    elif is_fixed is not False:
        status = "needs-open-setup-confirmation"
    elif repair_blocks:
        status = "needs-clean-repaired-run"
    elif selected:
        status = "ready"
    else:
        status = "needs-more-specific-driver-feedback"
    return {
        "schema_version": TUNING_SCHEMA_VERSION,
        "status": status,
        "symptoms": parsed,
        "setup": {
            "name": identity.get("setup_name"),
            "fingerprint": identity.get("setup_fingerprint"),
            "is_fixed_setup": is_fixed,
            "parameter_count": identity.get("setup_parameter_count"),
        },
        "setup_comparison": comparison,
        "telemetry_evidence": telemetry,
        "recommendations": selected,
        "prior_successes_considered": successful_history[:5],
        "blockers": blockers,
        "test_protocol": {
            "control": "Match setup baseline, fuel, tires, weather, track state, and intended line.",
            "sequence": [
                "Record a clean baseline run before changing the garage.",
                "Apply only the first recommended change or explicitly coupled builder sequence.",
                "Run 5 laps for a transient issue or 10-15 laps for race balance/tire life.",
                "Compare setup fingerprints, dynamic platform, controls, pace, and post-service tires.",
                "Keep the change only if the named symptom improves without triggering the listed risk; otherwise roll back.",
            ],
            "one_change_rule": True,
            "native_event_alignment": {
                "status": telemetry["native_events"]["status"],
                "event_count": telemetry["native_events"]["event_count"],
                "rule": (
                    "Use cached event records to align raw A/B traces; the detector locates events but does not diagnose a setup cause."
                ),
            },
        },
        "causality": (
            "Driver feedback identifies the symptom; telemetry can corroborate where and when it occurs, "
            "but no telemetry trace uniquely proves a setup parameter is the cause. Native threshold events "
            "and calibrated proxies are alignment markers, not causal classifications."
        ),
    }


# ---------------------------------------------------------------------------
# Progressive Tuning evidence contract v2
# ---------------------------------------------------------------------------


class StructuredTuningError(ValueError):
    """A bounded structured-tuning request is malformed."""


def stable_evidence_hash(value: Any, length: int = 64) -> str:
    """Return a deterministic JSON hash used for immutable evidence links."""

    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[: max(8, min(int(length), 64))]


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalized_identity_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def embedded_setup_fingerprint(setup: Mapping[str, Any] | None) -> str | None:
    if not isinstance(setup, Mapping) or not setup:
        return None
    # Match analysis_engine._identity exactly, including JSON's default ASCII
    # escaping, so this gate verifies the recorded fingerprint rather than a
    # merely similar serialization.
    return hashlib.sha256(
        json.dumps(
            setup, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def _analysis_track_key(analysis: Mapping[str, Any]) -> str:
    geometry = analysis.get("track_geometry")
    if isinstance(geometry, Mapping) and geometry.get("track_configuration_key"):
        return str(geometry["track_configuration_key"])
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    return _normalized_identity_token(
        f"{identity.get('track_id') or 'track'}-{identity.get('track_config') or identity.get('track_name')}"
    )


def track_geometry_hash(analysis: Mapping[str, Any]) -> str | None:
    """Return the analysis-owned geometry identity; legacy inference is forbidden."""

    geometry = analysis.get("track_geometry")
    if not isinstance(geometry, Mapping):
        return None
    value = str(geometry.get("geometry_hash") or "").strip().casefold()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _normalized_corner(raw: Mapping[str, Any]) -> dict[str, Any]:
    corner_id = str(raw.get("corner_id") or raw.get("id") or "").strip()
    label = str(raw.get("label") or raw.get("corner_label") or "").strip()
    start = _finite_number(raw.get("start_pct"))
    apex = _finite_number(raw.get("apex_pct"))
    end = _finite_number(raw.get("end_pct"))
    if not corner_id or not label or None in (start, apex, end):
        raise StructuredTuningError(
            "Every verified turn requires corner_id, label, start_pct, apex_pct, and end_pct."
        )
    assert start is not None and apex is not None and end is not None
    if not all(0.0 <= value < 1.0 for value in (start, apex, end)):
        raise StructuredTuningError("Turn lap fractions must be finite values in [0, 1).")
    epsilon = 0.000001
    forward_arc = (end - start) % 1.0
    apex_distance = (apex - start) % 1.0
    if (
        forward_arc <= epsilon
        or apex_distance <= epsilon
        or apex_distance >= forward_arc - epsilon
    ):
        raise StructuredTuningError(
            "Turn bounds must form a non-degenerate forward start-to-apex-to-end arc."
        )
    return {
        "corner_id": corner_id,
        "label": label,
        "start_pct": round(start, 6),
        "apex_pct": round(apex, 6),
        "end_pct": round(end, 6),
        "is_official": bool(raw.get("is_official")),
        "user_verified": bool(raw.get("user_verified", raw.get("verified", False))),
    }


def map_annotation_hash(map_identity: Mapping[str, Any]) -> str:
    corners = sorted([
        _normalized_corner(item)
        for item in map_identity.get("corners", map_identity.get("turns", ()))
        if isinstance(item, Mapping)
    ], key=lambda item: (item["start_pct"], item["apex_pct"], item["end_pct"], item["corner_id"]))
    canonical = {
        "track_configuration_key": str(map_identity.get("track_configuration_key") or ""),
        "geometry_hash": str(map_identity.get("geometry_hash") or "").casefold(),
        "source_type": _MAP_SOURCE_ALIASES.get(
            str(map_identity.get("source_type") or "").strip().casefold(),
            str(map_identity.get("source_type") or "").strip().casefold(),
        ),
        "corners": corners,
    }
    return stable_evidence_hash(canonical, 64)


def validate_map_identity(
    analysis: Mapping[str, Any], map_identity: Mapping[str, Any] | None
) -> tuple[dict[str, Any], list[str]]:
    missing: list[str] = []
    if not isinstance(map_identity, Mapping):
        return {}, ["verified-map-identity-required"]
    geometry = analysis.get("track_geometry")
    geometry = geometry if isinstance(geometry, Mapping) else {}
    expected_key = _analysis_track_key(analysis)
    supplied_key = str(map_identity.get("track_configuration_key") or "")
    expected_geometry_hash = track_geometry_hash(analysis)
    supplied_geometry_hash = str(map_identity.get("geometry_hash") or "").casefold()
    source_raw = str(map_identity.get("source_type") or "").strip().casefold()
    source_type = _MAP_SOURCE_ALIASES.get(source_raw)
    try:
        corners = sorted([
            _normalized_corner(item)
            for item in map_identity.get("corners", map_identity.get("turns", ()))
            if isinstance(item, Mapping)
        ], key=lambda item: (item["start_pct"], item["apex_pct"], item["end_pct"], item["corner_id"]))
    except StructuredTuningError as exc:
        corners = []
        missing.append(f"invalid-corner-annotation:{exc}")
    quality = geometry.get("quality") if isinstance(geometry.get("quality"), Mapping) else {}
    if (
        geometry.get("status") != "usable"
        or not geometry.get("main_path")
        or quality.get("main_loop_complete") is not True
    ):
        missing.append("complete-telemetry-track-geometry-required")
    if not supplied_key or supplied_key != expected_key:
        missing.append("exact-track-configuration-map-mismatch")
    if not expected_geometry_hash or supplied_geometry_hash != expected_geometry_hash:
        missing.append("exact-track-geometry-hash-mismatch")
    if source_type is None:
        missing.append("verified-map-source-type-required")
    if not bool(map_identity.get("verified", map_identity.get("is_verified", False))):
        missing.append("map-verification-required")
    if not corners:
        missing.append("verified-corner-annotations-required")
    if len({item["corner_id"] for item in corners}) != len(corners):
        missing.append("corner-identifiers-must-be-unique")
    supplied_annotation_hash = str(map_identity.get("annotation_hash") or "").casefold()
    expected_annotation_hash = map_annotation_hash(
        {**dict(map_identity), "source_type": source_type or source_raw, "corners": corners}
    )
    if supplied_annotation_hash != expected_annotation_hash:
        missing.append("corner-annotation-hash-mismatch")
    return {
        "map_identity": str(map_identity.get("map_identity") or expected_annotation_hash[:20]),
        "track_configuration_key": expected_key,
        "geometry_hash": expected_geometry_hash,
        "annotation_hash": expected_annotation_hash,
        "source_type": source_type,
        "source_label": str(map_identity.get("source_label") or "")[:200],
        "source_url": str(map_identity.get("source_url") or "")[:2000] or None,
        "verified": not missing,
        "corners": corners,
    }, missing


def _casefold_setup_path(
    setup: Mapping[str, Any], dotted_path: str
) -> tuple[bool, str, Any]:
    value: Any = setup
    resolved: list[str] = []
    for segment in dotted_path.split("."):
        if not isinstance(value, Mapping):
            return False, dotted_path, None
        match = next((key for key in value if str(key).casefold() == segment.casefold()), None)
        if match is None:
            return False, dotted_path, None
        resolved.append(str(match))
        value = value[match]
    return True, ".".join(resolved), value


def _setup_gate(analysis: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    missing: list[str] = []
    setup = identity.get("setup")
    calculated = embedded_setup_fingerprint(setup if isinstance(setup, Mapping) else None)
    declared = str(identity.get("setup_fingerprint") or "").casefold()
    if identity.get("is_fixed_setup") is not False:
        missing.append("open-setup-target-required")
    if not calculated:
        missing.append("embedded-open-setup-required")
    if not declared:
        missing.append("open-setup-fingerprint-required")
    elif calculated and declared not in {calculated, calculated[:16]}:
        missing.append("open-setup-fingerprint-mismatch")
    return {
        "name": identity.get("setup_name"),
        "fingerprint": declared or calculated,
        "calculated_fingerprint": calculated,
        "embedded_setup_available": bool(calculated),
        "manual_sto_boundary": True,
        "setup": dict(setup) if isinstance(setup, Mapping) else {},
    }, missing


def validate_open_target(
    driving_analysis: Mapping[str, Any], open_target_analysis: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    source = driving_analysis.get("identity") if isinstance(driving_analysis.get("identity"), Mapping) else {}
    target = open_target_analysis.get("identity") if isinstance(open_target_analysis.get("identity"), Mapping) else {}
    setup, missing = _setup_gate(open_target_analysis)
    source_car = _normalized_identity_token(source.get("car_path"))
    target_car = _normalized_identity_token(target.get("car_path"))
    source_track = _analysis_track_key(driving_analysis)
    target_track = _analysis_track_key(open_target_analysis)
    target_geometry = open_target_analysis.get("track_geometry")
    target_geometry = target_geometry if isinstance(target_geometry, Mapping) else {}
    if not open_target_analysis.get("analysis_id"):
        missing.append("open-target-analysis-id-required")
    if not target_geometry.get("track_configuration_key"):
        missing.append("open-target-track-configuration-key-required")
    if not source_car or source_car != target_car:
        missing.append("open-target-exact-car-path-mismatch")
    if not source_track or source_track != target_track:
        missing.append("open-target-exact-track-configuration-mismatch")
    if source.get("is_fixed_setup") is True:
        source_id = str(driving_analysis.get("analysis_id") or "")
        target_id = str(open_target_analysis.get("analysis_id") or "")
        if not source_id or not target_id or source_id == target_id:
            missing.append("fixed-evidence-requires-distinct-open-target")
    return {
        "analysis_id": open_target_analysis.get("analysis_id"),
        "car_id": target.get("car_id"),
        "car_path": target.get("car_path"),
        "track_id": target.get("track_id"),
        "track_config": target.get("track_config"),
        "track_configuration_key": target_track,
        "is_fixed_setup": target.get("is_fixed_setup"),
        "setup": setup,
        "compatible": not missing,
    }, missing


def load_structured_tuning_rules(
    ruleset_id: str = STRUCTURED_TUNING_RULESET_ID,
) -> dict[str, Any]:
    if ruleset_id != STRUCTURED_TUNING_RULESET_ID:
        raise StructuredTuningError(
            f"Unsupported tuning ruleset {ruleset_id!r}; only {STRUCTURED_TUNING_RULESET_ID!r} is installed."
        )
    path = Path(__file__).resolve().parents[3] / "config" / "tuning-rules" / f"{ruleset_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructuredTuningError(f"Tuning ruleset is unavailable: {exc}") from exc
    if not isinstance(value, dict) or value.get("ruleset_id") != ruleset_id:
        raise StructuredTuningError("Tuning ruleset identity is invalid.")
    value["catalog_sha256"] = stable_evidence_hash(value, 64)
    return value


def _ruleset_supports_car(rules: Mapping[str, Any], car_path: Any) -> bool:
    normalized = " ".join(str(car_path or "").casefold().split())
    return any(
        normalized.startswith(" ".join(str(prefix).casefold().split()))
        for prefix in rules.get("applicable_car_path_prefixes") or ()
    )


def normalize_structured_feedback(
    feedback: Sequence[Mapping[str, Any]], map_reference: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(feedback, (str, bytes)) or not isinstance(feedback, Sequence):
        raise StructuredTuningError("feedback must be an array of structured turn observations.")
    if not feedback or len(feedback) > 100:
        raise StructuredTuningError("feedback must contain 1-100 items.")
    corners = {
        str(item.get("corner_id")): item
        for item in map_reference.get("corners") or ()
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    missing: list[str] = []
    seen_feedback_ids: set[str] = set()
    for index, raw in enumerate(feedback):
        if not isinstance(raw, Mapping):
            raise StructuredTuningError(f"feedback[{index}] must be an object.")
        corner_id = str(raw.get("corner_id") or "").strip()
        corner = corners.get(corner_id)
        if corner is None:
            missing.append(f"feedback-{index + 1}-corner-not-in-verified-map")
            continue
        run_phase = str(raw.get("run_phase") or "middle").casefold().strip()
        if run_phase == "mid":
            run_phase = "middle"
        if run_phase not in _RUN_PHASES:
            raise StructuredTuningError(f"feedback[{index}].run_phase must be early, middle, or late.")
        phase_values = raw.get("corner_phases") or raw.get("corner_phase") or []
        if isinstance(phase_values, str):
            phase_values = [phase_values]
        phases = []
        for phase in phase_values:
            normalized = str(phase).casefold().strip().replace("mid", "center")
            if normalized not in _CORNER_PHASES:
                raise StructuredTuningError(
                    f"feedback[{index}].corner_phases contains unsupported value {phase!r}."
                )
            if normalized not in phases:
                phases.append(normalized)
        if not phases:
            phases = ["whole"]
        symptom_raw = str(raw.get("symptom_id") or "").casefold().strip().replace("_", "-")
        symptom = _SYMPTOM_ALIASES.get(symptom_raw)
        if symptom is None:
            raise StructuredTuningError(f"feedback[{index}].symptom_id is unsupported.")
        severity = raw.get("severity", 3)
        confidence = raw.get("driver_confidence", raw.get("confidence", 3))
        priority = raw.get("priority", 3)
        for name, value in (("severity", severity), ("driver_confidence", confidence), ("priority", priority)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise StructuredTuningError(f"feedback[{index}].{name} must be an integer from 1 to 5.")
        note = str(raw.get("note") or "").strip()
        if len(note) > 2_000:
            raise StructuredTuningError(f"feedback[{index}].note must be 2,000 characters or fewer.")
        # Corner ids can survive a map-catalog revision while their telemetry
        # bounds change. Require the exact three bounds captured with the report
        # so stale feedback cannot silently attach to a newer annotation.
        if any(raw.get(field) is None for field in ("start_pct", "apex_pct", "end_pct")):
            missing.append(f"feedback-{index + 1}-corner-bounds-required")
            continue
        bounds_mismatch = False
        for field in ("start_pct", "apex_pct", "end_pct"):
            supplied = _finite_number(raw.get(field))
            if (
                supplied is None
                or not 0.0 <= supplied < 1.0
                or abs(supplied - float(corner[field])) > 0.000001
            ):
                bounds_mismatch = True
                break
        if bounds_mismatch:
            missing.append(f"feedback-{index + 1}-corner-bounds-mismatch")
            continue
        stable = {
            "corner_id": corner_id,
            "run_phase": run_phase,
            "corner_phases": phases,
            "symptom_id": symptom,
            "severity": severity,
            "driver_confidence": confidence,
            "priority": priority,
            "note": note,
        }
        feedback_id = str(raw.get("feedback_id") or f"feedback-{stable_evidence_hash(stable, 16)}").strip()
        if not feedback_id or len(feedback_id) > 160:
            raise StructuredTuningError(f"feedback[{index}].feedback_id must contain 1-160 characters.")
        if feedback_id in seen_feedback_ids:
            raise StructuredTuningError("feedback_id values must be unique within a request.")
        seen_feedback_ids.add(feedback_id)
        result.append(
            {
                "feedback_id": feedback_id,
                "corner_id": corner_id,
                "corner_label": corner["label"],
                "start_pct": corner["start_pct"],
                "apex_pct": corner["apex_pct"],
                "end_pct": corner["end_pct"],
                **{key: value for key, value in stable.items() if key != "corner_id"},
                "source": "driver-report",
            }
        )
    return result, missing


def select_representative_runs(
    analysis: Mapping[str, Any], requested_run_ids: Sequence[Any] = ()
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    limitations: list[str] = []
    global_repair = analysis.get("damage_repair")
    for raw in analysis.get("runs") or ():
        if not isinstance(raw, Mapping):
            continue
        run_number = raw.get("run_number")
        run_id = str(
            raw.get("run_id")
            or (f"run-{run_number}" if run_number not in (None, "") else "")
        )
        if run_id.isdigit():
            run_id = f"run-{run_id}"
        references = raw.get("coaching_reference_lap_numbers")
        if references is None:
            references = raw.get("valid_green_lap_numbers") or ()
        laps = sorted(
            {
                int(number)
                for value in references
                if (number := _finite_number(value)) is not None
                and number.is_integer()
                and number >= 0
            }
        )
        damage = raw.get("damage_repair_context")
        damage = damage if isinstance(damage, Mapping) else {}
        damage_eligible = damage.get("automatic_coaching_reference_eligible")
        reasons: list[str] = []
        if len(laps) < _STRUCTURED_MINIMUM_RUN_LAPS:
            reasons.append("fewer-than-six-strict-clean-green-laps")
        if damage_eligible is False:
            reasons.append("repair-or-tow-contaminated")
        elif damage_eligible is None and isinstance(global_repair, Mapping):
            summary = global_repair.get("summary") if isinstance(global_repair.get("summary"), Mapping) else {}
            if any(_finite_number(summary.get(name)) not in (None, 0.0) for name in ("repair_time_s", "tow_time_s")):
                reasons.append("repair-eligibility-not-proven")
        item = {
            "run_id": run_id,
            "run_number": run_number,
            "eligible_lap_numbers": laps,
            "eligible_lap_count": len(laps),
            "phase_lap_numbers": {
                "early": laps[: len(laps) // 3],
                "middle": laps[len(laps) // 3 : (2 * len(laps)) // 3],
                "late": laps[(2 * len(laps)) // 3 :],
            },
            "selection_basis": "strict coaching-reference clean-green laps",
        }
        if reasons or any(len(values) < _STRUCTURED_MINIMUM_PHASE_LAPS for values in item["phase_lap_numbers"].values()):
            reasons = reasons or ["fewer-than-two-laps-in-each-run-phase"]
            rejected.append({**item, "reasons": reasons})
        else:
            eligible.append(item)
    requested = [
        f"run-{str(value).strip()}" if str(value).strip().isdigit() else str(value).strip()
        for value in requested_run_ids
        if str(value).strip()
    ]
    if requested:
        if len(requested) > 3:
            limitations.append("At most three representative runs may be selected.")
        by_id = {item["run_id"]: item for item in eligible}
        selected = [by_id[value] for value in requested[:3] if value in by_id]
        absent = [value for value in requested[:3] if value not in by_id]
        limitations.extend(f"Requested run {value} is not eligible." for value in absent)
        if len(requested) > 3 or absent or len(set(requested)) != len(requested):
            if len(set(requested)) != len(requested):
                limitations.append("Representative run overrides must be unique.")
            selected = []
        for item in selected:
            item["selection_mode"] = "driver-override"
    else:
        selected = sorted(
            eligible,
            key=lambda item: (-item["eligible_lap_count"], str(item["run_id"])),
        )[:1]
        for item in selected:
            item["selection_mode"] = "automatic-longest-clean-run"
    if not selected:
        limitations.append("No representative run has two strict clean green laps in every run phase.")
    return selected, rejected, limitations


def _circular_intervals(start: float, end: float) -> list[tuple[float, float]]:
    return [(start, end)] if start <= end else [(start, 1.0), (0.0, end)]


def _corner_zone_overlap(corner: Mapping[str, Any], zone: Mapping[str, Any]) -> float:
    start_a, end_a = float(corner["start_pct"]), float(corner["end_pct"])
    start_b = _finite_number(zone.get("start_pct"))
    end_b = _finite_number(zone.get("end_pct"))
    if start_b is None or end_b is None:
        return 0.0
    return sum(
        max(0.0, min(a1, b1) - max(a0, b0))
        for a0, a1 in _circular_intervals(start_a, end_a)
        for b0, b1 in _circular_intervals(start_b, end_b)
    )


_PHASE_METRICS = {
    "entry": ("entry_speed_mph", "brake_peak_fraction", "brake_energy_proxy", "brake_onset_lap_pct", "turn_in_lap_pct"),
    "center": ("minimum_speed_mph", "steering_average_abs_rad", "steering_work_proxy", "steering_corrections"),
    "exit": ("exit_speed_mph", "exit_throttle_fraction", "throttle_pickup_lap_pct", "brake_release_lap_pct"),
    "whole": ("entry_speed_mph", "minimum_speed_mph", "exit_speed_mph", "steering_work_proxy"),
}


def build_feedback_observations(
    analysis: Mapping[str, Any],
    feedback: Sequence[Mapping[str, Any]],
    representative_runs: Sequence[Mapping[str, Any]],
    map_reference: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    tire = analysis.get("corner_tire_age")
    tire = tire if isinstance(tire, Mapping) else {}
    run_lookup = {
        str(item.get("run_number")): item
        for item in tire.get("runs") or ()
        if isinstance(item, Mapping)
    }
    corner_lookup = {item["corner_id"]: item for item in map_reference.get("corners") or ()}
    observations: list[dict[str, Any]] = []
    limitations: list[str] = []
    for report in feedback:
        corner = corner_lookup.get(report["corner_id"])
        for selected in representative_runs:
            source_run = run_lookup.get(str(selected.get("run_number")))
            zones = source_run.get("zones") if isinstance(source_run, Mapping) else ()
            zone = max(
                (item for item in zones or () if isinstance(item, Mapping)),
                key=lambda item: _corner_zone_overlap(corner, item),
                default=None,
            )
            if not zone or _corner_zone_overlap(corner, zone) <= 0.0:
                limitations.append(
                    f"{report['corner_label']} has no overlapping telemetry load-zone summary for run {selected['run_id']}."
                )
                continue
            phase = next(
                (
                    item
                    for item in zone.get("observational_run_phases") or ()
                    if isinstance(item, Mapping) and item.get("phase") == report["run_phase"]
                ),
                None,
            )
            if not phase or phase.get("status") == "unavailable":
                limitations.append(
                    f"{report['corner_label']} {report['run_phase']} telemetry summary is unavailable for run {selected['run_id']}."
                )
                continue
            metrics = phase.get("metrics") if isinstance(phase.get("metrics"), Mapping) else {}
            requested_metrics: list[str] = []
            for corner_phase in report["corner_phases"]:
                requested_metrics.extend(_PHASE_METRICS[corner_phase])
            compact = {
                name: metrics.get(name)
                for name in dict.fromkeys(requested_metrics)
                if _finite_number(metrics.get(name)) is not None
            }
            evidence_id = "evidence-" + stable_evidence_hash(
                {
                    "feedback_id": report["feedback_id"],
                    "run_id": selected["run_id"],
                    "zone_id": zone.get("zone_id"),
                    "phase": report["run_phase"],
                    "metrics": compact,
                },
                20,
            )
            observations.append(
                {
                    "evidence_id": evidence_id,
                    "feedback_id": report["feedback_id"],
                    "run_id": selected["run_id"],
                    "corner_id": report["corner_id"],
                    "corner_label": report["corner_label"],
                    "run_phase": report["run_phase"],
                    "corner_phases": report["corner_phases"],
                    "lap_numbers": list(phase.get("lap_numbers") or ()),
                    "lap_count": phase.get("lap_count"),
                    "metrics": compact,
                    "source": "derived-from-recorded-telemetry",
                    "causal_claim": False,
                    "limitation": "These measurements locate and describe the reported symptom; they do not prove a setup cause.",
                }
            )
    return observations, list(dict.fromkeys(limitations))


def _feedback_conflicts(feedback: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for item in feedback:
        for phase in item["corner_phases"]:
            groups.setdefault((item["corner_id"], item["run_phase"], phase), []).append(item)
    result: list[dict[str, Any]] = []
    for key, items in groups.items():
        max_priority = max(int(item["priority"]) for item in items)
        top = [item for item in items if int(item["priority"]) == max_priority]
        symptoms = {str(item["symptom_id"]) for item in top}
        conflicting = bool("good" in symptoms and len(symptoms) > 1) or bool({"tight", "loose"}.issubset(symptoms))
        if conflicting:
            result.append(
                {
                    "conflict_id": "conflict-" + stable_evidence_hash({"key": key, "feedback": [item["feedback_id"] for item in top]}, 16),
                    "scope": {"corner_id": key[0], "run_phase": key[1], "corner_phase": key[2]},
                    "feedback_ids": [item["feedback_id"] for item in top],
                    "reason": "Opposing observations have the same highest priority; choose which one should drive this test.",
                    "resolved": False,
                }
            )
    return result


def _prior_outcome_signatures(previous_experiments: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in previous_experiments:
        if not isinstance(item, Mapping):
            continue
        outcome = str(item.get("outcome") or "").casefold()
        recommendation = item.get("recommendation") if isinstance(item.get("recommendation"), Mapping) else {}
        candidate = item.get("primary_recommendation")
        if not isinstance(candidate, Mapping):
            candidate = recommendation.get("selected_candidate") if isinstance(recommendation.get("selected_candidate"), Mapping) else {}
        signature = str(candidate.get("history_signature") or "")
        if signature and outcome:
            result.setdefault(signature, []).append(outcome)
    return result


def build_candidate_whitelist(
    target_analysis: Mapping[str, Any],
    rules: Mapping[str, Any],
    feedback: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    previous_experiments: Sequence[Mapping[str, Any]] = (),
    goal: str = "long-run-pace",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    target_identity = target_analysis.get("identity") if isinstance(target_analysis.get("identity"), Mapping) else {}
    setup = target_identity.get("setup") if isinstance(target_identity.get("setup"), Mapping) else {}
    evidence_by_feedback: dict[str, list[str]] = {}
    for item in observations:
        evidence_by_feedback.setdefault(str(item.get("feedback_id")), []).append(str(item.get("evidence_id")))
    history = _prior_outcome_signatures(previous_experiments)
    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    limitations: list[str] = []
    for report in feedback:
        if report["symptom_id"] in {"good", "other"}:
            continue
        for corner_phase in report["corner_phases"]:
            matching: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
            for setting in rules.get("settings") or ():
                if not isinstance(setting, Mapping):
                    continue
                for trigger in setting.get("triggers") or ():
                    if not isinstance(trigger, Mapping):
                        continue
                    if (
                        trigger.get("symptom_id") == report["symptom_id"]
                        and trigger.get("run_phase") in {"*", report["run_phase"]}
                        and trigger.get("corner_phase") in {"*", corner_phase}
                    ):
                        matching.append((setting, trigger))
            for setting, trigger in matching:
                paths: list[dict[str, Any]] = []
                for path in setting.get("paths") or ():
                    found, resolved, current = _casefold_setup_path(setup, str(path))
                    usable = bool(
                        found
                        and current not in (None, "")
                        and not isinstance(current, (Mapping, list, tuple, set))
                    )
                    paths.append({"requested_path": path, "resolved_path": resolved if usable else None, "current_value": current if usable else None})
                required_mode = str(setting.get("required_path_mode") or "all")
                available = all(item["resolved_path"] for item in paths) if required_mode == "all" else any(item["resolved_path"] for item in paths)
                if not available:
                    limitations.append(
                        f"{setting.get('system')} is unavailable because required settings are absent from the exact embedded open setup."
                    )
                    continue
                direction = str(trigger.get("direction"))
                direction_rule = setting.get("directions", {}).get(direction)
                if not isinstance(direction_rule, Mapping):
                    continue
                history_signature = stable_evidence_hash(
                    {
                        "ruleset_id": rules.get("ruleset_id"),
                        "setting_id": setting.get("setting_id"),
                        "direction": direction,
                        "car_path": _normalized_identity_token(target_identity.get("car_path")),
                        "track_configuration_key": _analysis_track_key(target_analysis),
                        "corner_id": report["corner_id"],
                        "run_phase": report["run_phase"],
                        "corner_phase": corner_phase,
                        "symptom_id": report["symptom_id"],
                    },
                    32,
                )
                outcomes = history.get(history_signature, [])
                if goal in {"long-run-pace", "tire-life"}:
                    goal_relevance = {"late": 1.0, "middle": 0.6, "early": 0.3}[report["run_phase"]]
                    goal_reason = (
                        f"{report['run_phase'].title()}-run feedback is prioritized for the {goal} goal."
                    )
                elif goal == "restart-pace":
                    goal_relevance = {"early": 1.0, "middle": 0.5, "late": 0.2}[report["run_phase"]]
                    goal_reason = (
                        f"{report['run_phase'].title()}-run feedback is prioritized for restart pace."
                    )
                else:
                    stability_symptoms = {"unstable-braking", "wheel-hop-lock", "loose"}
                    goal_relevance = 1.0 if report["symptom_id"] in stability_symptoms else 0.4
                    goal_reason = (
                        f"{report['symptom_id']} is directly stability-related."
                        if goal_relevance == 1.0
                        else "This report is retained for stability but is not a direct instability label."
                    )
                candidate_id = "candidate-" + stable_evidence_hash(
                    {"signature": history_signature, "feedback_id": report["feedback_id"]}, 20
                )
                candidate = {
                    "candidate_id": candidate_id,
                    "setting_id": setting.get("setting_id"),
                    "system": setting.get("system"),
                    "direction": direction,
                    "change": direction_rule.get("instruction"),
                    "predicted_effect": direction_rule.get("expected_effect"),
                    "risk": direction_rule.get("risk"),
                    "current_values": paths,
                    "proposed_values": None,
                    "manual_application_only": True,
                    "adjustability": "confirmed-present-manual-step-unverified",
                    "legality": "requires-iracing-garage-tech-check",
                    "feedback_ids": [report["feedback_id"]],
                    "evidence_ids": evidence_by_feedback.get(report["feedback_id"], []),
                    "conflicts": [],
                    "verify": list(setting.get("verify") or ()),
                    "priority": report["priority"],
                    "confidence": {
                        "driver_report": report["driver_confidence"] / 5.0,
                        "telemetry_context": 1.0 if evidence_by_feedback.get(report["feedback_id"]) else 0.25,
                        "verified_map": 1.0,
                        "exact_setup_setting_present": 1.0,
                        "versioned_rule_direction": 1.0,
                        "prior_success": 1.0 if "improved" in outcomes else None,
                    },
                    "history_signature": history_signature,
                    "goal": goal,
                    "goal_role": "workflow-priority-only-not-causal-evidence",
                    "goal_relevance": {
                        "score": goal_relevance,
                        "reason": goal_reason,
                        "causal_evidence": False,
                    },
                    "prior_outcomes": outcomes,
                    "rollback": {
                        "setup_name": target_identity.get("setup_name"),
                        "setup_fingerprint": target_identity.get("setup_fingerprint"),
                        "instruction": "Reload or restore this exact baseline before another test or immediately if the named risk appears.",
                    },
                    "test_protocol": {
                        "one_change_rule": True,
                        "control": "Match fuel, tire age, weather, track state, traffic exposure, and intended line.",
                        "minimum_run": "Repeat the same representative-run length when practical.",
                        "rollback": "Restore the exact baseline setup before trying another system or if the named risk appears.",
                    },
                }
                confidence_values = [
                    float(value)
                    for value in candidate["confidence"].values()
                    if value is not None
                ]
                candidate["confidence"]["overall"] = round(
                    sum(confidence_values) / len(confidence_values), 3
                )
                if any(outcome in {"worse", "no-change"} for outcome in outcomes):
                    suppressed.append({**candidate, "suppression_reason": "The same scoped change previously produced worse or no-change feedback."})
                else:
                    candidates.append(candidate)
    # A candidate is one logical setup system. Merge duplicate reports into it.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (str(item["setting_id"]), str(item["direction"]))
        if key not in merged:
            merged[key] = item
            continue
        existing = merged[key]
        existing["feedback_ids"] = list(dict.fromkeys(existing["feedback_ids"] + item["feedback_ids"]))
        existing["evidence_ids"] = list(dict.fromkeys(existing["evidence_ids"] + item["evidence_ids"]))
        if item["priority"] > existing["priority"]:
            existing["priority"] = item["priority"]
            existing["goal_relevance"] = item["goal_relevance"]
        elif (
            item["priority"] == existing["priority"]
            and item["goal_relevance"]["score"] > existing["goal_relevance"]["score"]
        ):
            existing["goal_relevance"] = item["goal_relevance"]
    # Opposing directions for the same setting need a unique priority winner.
    by_setting: dict[str, list[dict[str, Any]]] = {}
    for item in merged.values():
        by_setting.setdefault(str(item["setting_id"]), []).append(item)
    resolved: list[dict[str, Any]] = []
    for items in by_setting.values():
        if len({item["direction"] for item in items}) == 1:
            resolved.extend(items)
            continue
        highest = max(item["priority"] for item in items)
        winners = [item for item in items if item["priority"] == highest]
        if len(winners) == 1:
            winner = winners[0]
            winner["conflicts"].append("Opposing lower-priority driver feedback was recorded and did not drive this test.")
            resolved.append(winner)
        else:
            for item in items:
                suppressed.append({**item, "suppression_reason": "Opposing candidate direction has the same highest priority."})
    resolved.sort(
        key=lambda item: (
            -int(item["priority"]),
            -float(item["goal_relevance"]["score"]),
            -float(item["confidence"]["overall"]),
            0 if item["system"] == "aero-platform" else 1,
            str(item["candidate_id"]),
        )
    )
    return resolved, suppressed, list(dict.fromkeys(limitations))


def build_structured_tuning_evidence(
    driving_analysis: Mapping[str, Any],
    feedback: Sequence[Mapping[str, Any]],
    map_identity: Mapping[str, Any] | None,
    *,
    open_target_analysis: Mapping[str, Any] | None = None,
    representative_run_ids: Sequence[Any] = (),
    previous_experiments: Sequence[Mapping[str, Any]] = (),
    generic_note: str = "",
    goal: str = "long-run-pace",
    ruleset_id: str = STRUCTURED_TUNING_RULESET_ID,
) -> dict[str, Any]:
    if len(str(generic_note)) > 8_000:
        raise StructuredTuningError("generic_note must be 8,000 characters or fewer.")
    goal = str(goal or "long-run-pace").strip().casefold()
    if goal not in _TUNING_GOALS:
        raise StructuredTuningError(
            "goal must be long-run-pace, tire-life, restart-pace, or stability."
        )
    driving_identity = driving_analysis.get("identity") if isinstance(driving_analysis.get("identity"), Mapping) else {}
    target_analysis = open_target_analysis or driving_analysis
    target_identity = target_analysis.get("identity") if isinstance(target_analysis.get("identity"), Mapping) else {}
    rules = load_structured_tuning_rules(ruleset_id)
    map_ref, map_missing = validate_map_identity(driving_analysis, map_identity)
    target_ref, target_missing = validate_open_target(driving_analysis, target_analysis)
    run_selection, rejected_runs, run_limitations = select_representative_runs(
        driving_analysis, representative_run_ids
    )
    feedback_normalized, feedback_missing = normalize_structured_feedback(feedback, map_ref)
    conflicts = _feedback_conflicts(feedback_normalized)
    telemetry_observations, observation_limitations = build_feedback_observations(
        driving_analysis, feedback_normalized, run_selection, map_ref
    )
    driver_observations = [
        {
            "evidence_id": "evidence-driver-" + stable_evidence_hash(item, 16),
            "feedback_id": item["feedback_id"],
            "corner_id": item["corner_id"],
            "corner_label": item["corner_label"],
            "run_phase": item["run_phase"],
            "corner_phases": item["corner_phases"],
            "symptom_id": item["symptom_id"],
            "severity": item["severity"],
            "driver_confidence": item["driver_confidence"],
            "source": "driver-report",
            "causal_claim": False,
            "limitation": "A driver report identifies a symptom, not its setup cause.",
        }
        for item in feedback_normalized
    ]
    observations = driver_observations + telemetry_observations
    missing = list(map_missing) + list(target_missing) + list(feedback_missing)
    if not str(driving_identity.get("event_type") or "").casefold() == "race":
        missing.append("representative-race-session-required")
    if not _ruleset_supports_car(rules, target_identity.get("car_path")):
        missing.append("unsupported-car-ruleset")
    if not run_selection:
        missing.append("representative-clean-run-required")
    if conflicts:
        missing.append("unresolved-feedback-conflict")
    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    candidate_limitations: list[str] = []
    if not missing:
        candidates, suppressed, candidate_limitations = build_candidate_whitelist(
            target_analysis, rules, feedback_normalized, observations, previous_experiments, goal
        )
        if not candidates:
            missing.append("no-supported-single-change-candidate")
    limitations = list(
        dict.fromkeys(
            run_limitations
            + observation_limitations
            + candidate_limitations
            + [
                "Driver feedback names the symptom; telemetry only supplies location and context, never setup causality.",
                "The app never edits or writes .sto files. Apply one whitelisted change manually and re-pass iRacing garage tech.",
                "Exact numeric steps and ranges are intentionally omitted until verified for this car/build.",
            ]
        )
    )
    driving_ref = {
        "analysis_id": driving_analysis.get("analysis_id"),
        "event_type": driving_identity.get("event_type"),
        "car_id": driving_identity.get("car_id"),
        "car_path": driving_identity.get("car_path"),
        "track_id": driving_identity.get("track_id"),
        "track_config": driving_identity.get("track_config"),
        "track_configuration_key": _analysis_track_key(driving_analysis),
        "is_fixed_setup": driving_identity.get("is_fixed_setup"),
        "setup_fingerprint": driving_identity.get("setup_fingerprint"),
    }
    body: dict[str, Any] = {
        "schema_version": TUNING_EVIDENCE_SCHEMA_VERSION,
        "contract": TUNING_EVIDENCE_CONTRACT,
        "ruleset": {
            "ruleset_id": rules["ruleset_id"],
            "catalog_sha256": rules["catalog_sha256"],
            "provenance": rules.get("provenance") or [],
            "manual_application_only": True,
        },
        "goal": goal,
        "driving_evidence_ref": driving_ref,
        "open_target_ref": target_ref,
        "garage_target_gate": {
            "source_may_be_fixed": True,
            "distinct_open_target_required_for_fixed_source": True,
            "compatible": not target_missing,
        },
        "map_ref": map_ref,
        "representative_runs": run_selection,
        "rejected_runs": rejected_runs,
        "feedback": feedback_normalized,
        "generic_note": str(generic_note).strip(),
        "observations": observations,
        "candidate_whitelist": candidates,
        "suppressed_candidates": suppressed,
        "conflicts": conflicts,
        "limitations": limitations,
        "missing_required": list(dict.fromkeys(missing)),
        "eligibility": {
            "can_use_as_driving_evidence": bool(
                str(driving_identity.get("event_type") or "").casefold() == "race"
                and run_selection
                and feedback_normalized
                and not map_missing
            ),
            "can_receive_garage_recommendation": not missing and bool(candidates),
            "exact_map_identity": not map_missing,
            "exact_open_setup_identity": not target_missing,
            "one_change_rule": True,
        },
    }
    body["evidence_hash"] = stable_evidence_hash(body, 64)
    return body


def build_bounded_tuning_ai_request(evidence: Mapping[str, Any]) -> dict[str, Any]:
    compact_candidates: list[dict[str, Any]] = []
    included_evidence_ids: set[str] = set()
    for raw in evidence.get("candidate_whitelist") or ():
        if not isinstance(raw, Mapping) or len(compact_candidates) >= 20:
            continue
        candidate_evidence = [
            str(item) for item in raw.get("evidence_ids") or () if str(item).strip()
        ][:24]
        included_evidence_ids.update(candidate_evidence)
        compact_candidates.append(
            {
                "candidate_id": raw.get("candidate_id"),
                "system": raw.get("system"),
                "direction": raw.get("direction"),
                "change": raw.get("change"),
                "predicted_effect": raw.get("predicted_effect"),
                "risk": raw.get("risk"),
                "evidence_ids": candidate_evidence,
                "conflicts": list(raw.get("conflicts") or ())[:12],
                "confidence": raw.get("confidence"),
                "goal_relevance": raw.get("goal_relevance"),
                "manual_application_only": True,
            }
        )
    compact_evidence: list[dict[str, Any]] = []
    included_feedback_ids: set[str] = set()
    for raw in evidence.get("observations") or ():
        if not isinstance(raw, Mapping) or str(raw.get("evidence_id")) not in included_evidence_ids:
            continue
        feedback_id = str(raw.get("feedback_id") or "")
        if feedback_id:
            included_feedback_ids.add(feedback_id)
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), Mapping) else {}
        compact_evidence.append(
            {
                "evidence_id": raw.get("evidence_id"),
                "feedback_id": raw.get("feedback_id"),
                "corner_id": raw.get("corner_id"),
                "corner_label": raw.get("corner_label"),
                "run_id": raw.get("run_id"),
                "run_phase": raw.get("run_phase"),
                "corner_phases": raw.get("corner_phases"),
                "symptom_id": raw.get("symptom_id"),
                "severity": raw.get("severity"),
                "driver_confidence": raw.get("driver_confidence"),
                "lap_count": raw.get("lap_count"),
                "metrics": dict(list(metrics.items())[:16]),
                "source": raw.get("source"),
                "causal_claim": False,
            }
        )
    compact_feedback = [
        {
            "feedback_id": raw.get("feedback_id"),
            "corner_id": raw.get("corner_id"),
            "corner_label": raw.get("corner_label"),
            "run_phase": raw.get("run_phase"),
            "corner_phases": raw.get("corner_phases"),
            "symptom_id": raw.get("symptom_id"),
            "severity": raw.get("severity"),
            "driver_confidence": raw.get("driver_confidence"),
            "priority": raw.get("priority"),
            "note": str(raw.get("note") or "")[:300],
        }
        for raw in evidence.get("feedback") or ()
        if isinstance(raw, Mapping) and str(raw.get("feedback_id")) in included_feedback_ids
    ]
    request = {
        "contract": "tuning_ai_request_v1",
        "workflow_key": evidence.get("evidence_hash"),
        "instruction": (
            "Select at most one candidate from candidate_whitelist. Use only supplied evidence IDs. "
            "Do not invent setup values, telemetry, causes, or legality."
        ),
        "goal": evidence.get("goal"),
        "generic_note": str(evidence.get("generic_note") or "")[:2000],
        "eligibility": evidence.get("eligibility"),
        "feedback": compact_feedback,
        "evidence": compact_evidence,
        "candidate_whitelist": compact_candidates,
        "conflicts": list(evidence.get("conflicts") or ())[:20],
        "limitations": list(evidence.get("limitations") or ())[:30],
        "response_contract": {
            "additional_properties": False,
            "required": ["selected_candidate_id", "summary", "evidence_ids", "conflicts", "confidence_reasons"],
        },
    }
    encoded_size = len(json.dumps(request, separators=(",", ":"), default=str).encode("utf-8"))
    if encoded_size > _AI_REQUEST_MAX_BYTES:
        # Notes are useful but never identity or measurement evidence. Drop
        # them before refusing a valid deterministic plan.
        request["generic_note"] = ""
        for item in request["feedback"]:
            item["note"] = ""
        request["truncation"] = "Driver note text was omitted to keep the optional AI request under 64 KiB."
        encoded_size = len(json.dumps(request, separators=(",", ":"), default=str).encode("utf-8"))
    if encoded_size > _AI_REQUEST_MAX_BYTES:
        raise StructuredTuningError(
            "Bounded AI request exceeds 64 KiB after optional note removal; deterministic selection remains required."
        )
    return request


def validate_tuning_ai_response(
    response: Any, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(response, Mapping):
        return {"valid": False, "selection": None, "errors": ["AI response must be an object."]}
    allowed = {"selected_candidate_id", "summary", "evidence_ids", "conflicts", "confidence_reasons"}
    unknown = sorted(set(response).difference(allowed))
    if unknown:
        errors.append("Unknown fields: " + ", ".join(unknown))
    for field in allowed:
        if field not in response:
            errors.append(f"Missing field: {field}")
    candidates = {
        str(item.get("candidate_id")): item
        for item in evidence.get("candidate_whitelist") or ()
        if isinstance(item, Mapping) and item.get("candidate_id")
    }
    candidate_ids = set(candidates)
    try:
        ai_request = build_bounded_tuning_ai_request(evidence)
    except StructuredTuningError as exc:
        errors.append(str(exc))
        ai_request = {"evidence": []}
    supplied_evidence_ids = {
        str(item.get("evidence_id"))
        for item in ai_request.get("evidence") or ()
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    selected = str(response.get("selected_candidate_id") or "")
    selected_candidate = candidates.get(selected)
    candidate_evidence_ids = {
        str(item)
        for item in (selected_candidate or {}).get("evidence_ids") or ()
        if str(item)
    }
    evidence_ids = supplied_evidence_ids.intersection(candidate_evidence_ids)
    summary = str(response.get("summary") or "")
    if selected not in candidate_ids:
        errors.append("selected_candidate_id is not in candidate_whitelist.")
    if not 1 <= len(summary) <= 1200:
        errors.append("summary must contain 1-1200 characters.")
    normalized_lists: dict[str, list[str]] = {}
    bounds = {"evidence_ids": (1, 24, 160), "conflicts": (0, 12, 500), "confidence_reasons": (1, 12, 500)}
    for field, (minimum, maximum, text_limit) in bounds.items():
        raw = response.get(field)
        if not isinstance(raw, list) or not minimum <= len(raw) <= maximum or any(not isinstance(item, str) or not item.strip() or len(item) > text_limit for item in raw):
            errors.append(f"{field} has invalid type, count, or text length.")
            normalized_lists[field] = []
        else:
            normalized_lists[field] = list(dict.fromkeys(item.strip() for item in raw))
            if len(normalized_lists[field]) != len(raw):
                errors.append(f"{field} must not contain duplicate values.")
    if any(item not in evidence_ids for item in normalized_lists.get("evidence_ids", [])):
        errors.append("evidence_ids contains an ID not linked to the selected candidate.")
    selection = None if errors else {
        "selected_candidate_id": selected,
        "summary": summary.strip(),
        **normalized_lists,
    }
    return {"valid": not errors, "selection": selection, "errors": errors}


def select_structured_recommendation(
    evidence: Mapping[str, Any], ai_response: Any = None
) -> dict[str, Any]:
    candidates = [item for item in evidence.get("candidate_whitelist") or () if isinstance(item, Mapping)]
    if not evidence.get("eligibility", {}).get("can_receive_garage_recommendation") or not candidates:
        return {
            "status": "blocked",
            "selected_candidate_id": "",
            "summary": "A garage recommendation is unavailable until the listed evidence and identity requirements are satisfied.",
            "evidence_ids": [],
            "conflicts": [str(item.get("reason")) for item in evidence.get("conflicts") or () if isinstance(item, Mapping)],
            "confidence_reasons": [],
            "selection_source": "deterministic-gate",
            "ai_validation": None,
        }
    validation = validate_tuning_ai_response(ai_response, evidence) if ai_response is not None else None
    if validation and validation["valid"]:
        return {"status": "ready", **validation["selection"], "selection_source": "validated-bounded-ai", "ai_validation": validation}
    candidate = candidates[0]
    confidence = candidate.get("confidence") if isinstance(candidate.get("confidence"), Mapping) else {}
    reasons = [
        "The exact setting exists in the embedded open setup.",
        "The direction comes from the installed versioned rules catalog.",
        "Only one logical setup system is included in this test.",
    ]
    goal_relevance = candidate.get("goal_relevance")
    if isinstance(goal_relevance, Mapping) and goal_relevance.get("reason"):
        reasons.append(str(goal_relevance["reason"]))
    if candidate.get("evidence_ids"):
        reasons.append("Strict clean-run telemetry supplies location and run-phase context without asserting cause.")
    return {
        "status": "ready",
        "selected_candidate_id": candidate["candidate_id"],
        "summary": f"For the {candidate.get('goal', 'long-run-pace')} goal, test one reversible {candidate['system']} change: {candidate['change']}",
        "evidence_ids": list(candidate.get("evidence_ids") or ()),
        "conflicts": list(candidate.get("conflicts") or ()),
        "confidence_reasons": reasons,
        "confidence": confidence,
        "selection_source": "deterministic-fallback" if validation else "deterministic",
        "ai_validation": validation,
    }


__all__ = [
    "STRUCTURED_TUNING_RULESET_ID",
    "StructuredTuningError",
    "TUNING_EVIDENCE_CONTRACT",
    "TUNING_EVIDENCE_SCHEMA_VERSION",
    "build_bounded_tuning_ai_request",
    "build_candidate_whitelist",
    "build_structured_tuning_evidence",
    "choose_oreilly_donor",
    "embedded_setup_fingerprint",
    "load_structured_tuning_rules",
    "map_annotation_hash",
    "normalize_structured_feedback",
    "parse_handling_symptoms",
    "recommend_tuning",
    "select_representative_runs",
    "select_structured_recommendation",
    "stable_evidence_hash",
    "track_geometry_hash",
    "validate_map_identity",
    "validate_open_target",
    "validate_tuning_ai_response",
]
