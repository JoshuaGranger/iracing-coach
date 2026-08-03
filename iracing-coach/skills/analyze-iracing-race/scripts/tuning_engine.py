"""Evidence-based open-setup tuning decisions for iRacing Coach.

This module deliberately produces *testable recommendations*, not modified
``.sto`` files.  A setup change is an experiment whose result must be linked
to the exact setup fingerprint, telemetry, conditions, and driver symptom.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


TUNING_SCHEMA_VERSION = 1
_NATIVE_EVENT_SAMPLE_LIMIT = 40


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
