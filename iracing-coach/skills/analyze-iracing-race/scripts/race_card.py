"""Deterministic, bounded Race Card contract for companion and Markdown use.

This module intentionally does not perform I/O or network work.  It reduces an
existing local analysis plus validated cached knowledge to a small structured
object, then renders that object as ASCII-safe Markdown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
import statistics
import unicodedata
from typing import Any


CONTRACT_VERSION = 1
OVAL_WORD_LIMIT = 300
ROAD_WORD_LIMIT = 420
MAX_CORNER_ROWS = 20
MAX_EVIDENCE_ITEMS = 6

EVIDENCE_TAGS = {
    "measured": "[M]",
    "derived": "[D]",
    "inferred": "[I]",
    "proxy": "[P]",
    "unavailable": "[U]",
}

__all__ = [
    "CONTRACT_VERSION",
    "EVIDENCE_TAGS",
    "OVAL_WORD_LIMIT",
    "ROAD_WORD_LIMIT",
    "build_race_card",
    "race_card_word_count",
    "render_race_card",
]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ascii_text(value: Any) -> str:
    text = str(value or "")
    for old, new in (
        ("â€”", "-"), ("â€“", "-"), ("â€¦", "..."), ("Â·", "|"),
        ("\u2014", "-"), ("\u2013", "-"), ("\u2026", "..."), ("\u00b7", "|"),
        ("\u00a0", " "), ("Â", ""),
    ):
        text = text.replace(old, new)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("\ufffd", "").split()).strip()


def _compact(value: Any, *, words: int, chars: int) -> str:
    text = _ascii_text(value)
    tokens = text.split()
    if len(tokens) > words:
        text = " ".join(tokens[:words]).rstrip(".,;:") + "..."
    if len(text) > chars:
        text = text[: max(1, chars - 3)].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
    return text


def _claim(
    text: Any,
    evidence_type: str,
    *,
    words: int = 14,
    chars: int = 100,
) -> dict[str, str]:
    normalized_type = evidence_type if evidence_type in EVIDENCE_TAGS else "unavailable"
    rendered = _compact(text, words=words, chars=chars)
    if not rendered:
        normalized_type = "unavailable"
        rendered = "Unavailable"
    return {
        "evidence_type": normalized_type,
        "tag": EVIDENCE_TAGS[normalized_type],
        "text": rendered,
    }


def _render_claim(value: Mapping[str, Any]) -> str:
    return f"{value.get('tag') or '[U]'} {value.get('text') or 'Unavailable'}"


def _render_compact_claim(value: Mapping[str, Any]) -> str:
    tag = str(value.get("tag") or "[U]")
    evidence_type = str(value.get("evidence_type") or "unavailable")
    text = _ascii_text(value.get("text"))
    if evidence_type == "unavailable":
        return "[U-NA]"
    if text.startswith("Obs E"):
        compact = text.removeprefix("Obs ")
        compact = compact.replace("/M", "-M").replace("/X", "-X")
        compact = compact.replace(" mph; ", "-").replace("; ", "-")
        compact = compact.replace("TI ", "TI").replace("% lap", "")
        compact = compact.replace(" unavailable", "NA").replace(" limited n=1", "L1")
        compact = compact.replace("/", "-").replace("%", "").replace(".", "p")
        compact = compact.replace("BO-", "BOx").replace("BR-", "BRx").replace("TI-", "TIx")
        while "--" in compact:
            compact = compact.replace("--", "-")
        return f"{tag} {compact.replace(' ', '')}"
    if text.startswith("Target "):
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        numbers = [number.replace(".", "p") for number in numbers]
        return f"{tag} T{'-'.join(numbers[:2])}" if numbers else f"{tag} target"
    if text.startswith("Observed ") and "migration" in text:
        direction = text[len("Observed "):].split(" migration", 1)[0].replace(" ", "-")
        lap = re.search(r"\bLap (\d+(?:\.\d+)?)", text)
        return f"{tag} Mig-{direction}" + (f"-L{lap.group(1)}" if lap else "") + "-effect-NA"
    return f"{tag} {_compact(text, words=7, chars=58)}"


def _contains_number(text: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text))


def _safe_coaching(value: Any, *, numeric_targets_usable: bool) -> str | None:
    text = _ascii_text(value)
    if not text:
        return None
    if not numeric_targets_usable and _contains_number(text):
        return None
    if not numeric_targets_usable and re.search(r"\b(?:reference|target)\b", text, re.I):
        return None
    return text


def _driver_instruction(value: Any, *, numeric_targets_usable: bool) -> str | None:
    """Return only copy that tells the driver what to do on track.

    Technical metric actions also contain definitions such as ``a positive
    value is falloff``.  Those are useful beside the metric, but they are not
    opening-lap instructions and must never be promoted into the Race Card's
    Start priority.
    """

    text = _safe_coaching(value, numeric_targets_usable=numeric_targets_usable)
    if not text:
        return None
    if re.search(
        r"\b(?:positive|negative) (?:value )?(?:is|means)|"
        r"\b(?:higher|lower) (?:means|indicates)|"
        r"\bthis (?:value|metric|measurement) (?:means|describes)|"
        r"\bcompare the same run(?:'s)? tire condition\b",
        text,
        re.I,
    ):
        return None
    opening_verb = text.split(" ", 1)[0].lower().rstrip(":")
    if opening_verb not in {
        "avoid", "brake", "build", "carry", "enter", "exit", "feed",
        "finish", "hold", "keep", "open", "protect", "reduce", "release",
        "reset", "roll", "stabilize", "turn", "unwind", "validate",
    }:
        return None
    return text


def _is_oval(identity: Mapping[str, Any]) -> bool:
    config = _ascii_text(identity.get("track_config")).lower()
    return "oval" in config and "road" not in config


def _fmt_number(value: Any, digits: int = 1) -> str | None:
    number = _number(value)
    if number is None:
        return None
    if digits == 0 or abs(number - round(number)) < 0.005:
        return f"{number:.0f}"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _distance_label(
    race: Mapping[str, Any], planned_laps: float | None = None
) -> str:
    scheduled = _fmt_number(
        planned_laps if planned_laps is not None else race.get("scheduled_laps"),
        0,
    )
    recorded = _fmt_number(race.get("recorded_laps"), 0)
    if scheduled:
        return f"{scheduled} laps"
    if recorded:
        return f"{recorded} recorded laps"
    return "distance unavailable"


def _comparison_components(knowledge: Mapping[str, Any]) -> tuple[bool, list[Mapping[str, Any]], Mapping[str, Any]]:
    containers = [knowledge, _mapping(knowledge.get("facts")), _mapping(knowledge.get("garage61"))]
    quality: Mapping[str, Any] = {}
    targets: list[Mapping[str, Any]] = []
    for container in containers:
        if not quality and _mapping(container.get("comparison_quality")):
            quality = _mapping(container.get("comparison_quality"))
        for key in ("coaching_targets", "corner_targets", "targets"):
            targets.extend(
                item for item in _sequence(container.get(key)) if isinstance(item, Mapping)
            )
    return str(quality.get("status") or "").lower() == "usable", targets[:32], quality


def _intervals(start: float, end: float, wraps: bool = False) -> list[tuple[float, float]]:
    start %= 1.0
    end %= 1.0
    if wraps or start > end:
        return [(start, 1.0), (0.0, end)]
    return [(start, end)]


def _range_values(item: Mapping[str, Any]) -> tuple[float, float, bool] | None:
    start = _number(item.get("start_pct"))
    if start is None:
        start = _number(item.get("lap_pct_start"))
    end = _number(item.get("end_pct"))
    if end is None:
        end = _number(item.get("lap_pct_end"))
    raw_range = _sequence(item.get("lap_distance_range")) or _sequence(item.get("range"))
    if (start is None or end is None) and len(raw_range) >= 2:
        start, end = _number(raw_range[0]), _number(raw_range[1])
    if start is None or end is None:
        range_text = _ascii_text(item.get("lap_distance_pct"))
        # Lap-distance ranges are non-negative.  Treat the dash in ``14-36``
        # as a separator, not the sign on the second value.
        values = re.findall(r"\d+(?:\.\d+)?", range_text)
        if len(values) >= 2:
            start, end = float(values[0]) / 100.0, float(values[1]) / 100.0
    if start is None or end is None:
        return None
    if abs(start) > 1.5 or abs(end) > 1.5:
        start, end = start / 100.0, end / 100.0
    return start, end, bool(item.get("wraps_start_finish")) or start > end


def _ranges_align(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_range, right_range = _range_values(left), _range_values(right)
    if left_range is None or right_range is None:
        return False
    left_parts = _intervals(*left_range)
    right_parts = _intervals(*right_range)
    overlap = sum(
        max(0.0, min(a1, b1) - max(a0, b0))
        for a0, a1 in left_parts
        for b0, b1 in right_parts
    )
    left_length = sum(end - start for start, end in left_parts)
    right_length = sum(end - start for start, end in right_parts)
    return overlap >= 0.7 * max(left_length, right_length, 1e-9)


def _knowledge_zones(knowledge: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []

    def visit(value: Any, depth: int) -> None:
        if depth > 4 or len(found) >= 64:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() == "telemetry_load_zones":
                    found.extend(
                        item for item in _sequence(child) if isinstance(item, Mapping)
                    )
                elif isinstance(child, Mapping):
                    visit(child, depth + 1)

    visit(knowledge, 0)
    return found[:64]


def _corner_label(zone: Mapping[str, Any], knowledge_zones: Sequence[Mapping[str, Any]]) -> tuple[str, str, str | None]:
    fallback = _compact(
        zone.get("zone_label") or zone.get("name") or zone.get("corner_id") or zone.get("zone_id") or "Load zone",
        words=5,
        chars=36,
    ) or "Load zone"
    for cached in knowledge_zones:
        if not _ranges_align(zone, cached):
            continue
        label = _ascii_text(
            cached.get("provisional_corner_group")
            or cached.get("corner_group")
            or cached.get("corner_name")
            or cached.get("label")
            or cached.get("name")
        )
        if not label or "\ufffd" in label or "â" in label:
            continue
        status = _ascii_text(
            cached.get("corner_name_status") or cached.get("name_status") or cached.get("status")
        ) or "provisional_cached_alignment"
        source = _ascii_text(
            cached.get("name_source") or cached.get("source") or cached.get("source_url")
        ) or None
        suffix = "" if "official" in status.lower() else " (provisional)"
        return _compact(label + suffix, words=5, chars=36), status, source
    return fallback, _ascii_text(zone.get("corner_name_status")) or "telemetry_load_zone", None


def _damage_run_impacts(analysis: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    damage = _mapping(analysis.get("damage_repair"))
    return {
        int(number): item
        for item in _sequence(damage.get("run_impacts"))
        if isinstance(item, Mapping)
        and (number := _number(item.get("run_number"))) is not None
    }


def _selected_corner_run(
    corner: Mapping[str, Any], analysis: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    runs = [item for item in _sequence(corner.get("runs")) if isinstance(item, Mapping)]
    if not runs:
        return {}
    impacts = _damage_run_impacts(analysis or {})

    def rank(item: Mapping[str, Any]) -> tuple[int, bool, bool, int]:
        eligible = int(_number(item.get("eligible_lap_count")) or 0)
        zones = [zone for zone in _sequence(item.get("zones")) if isinstance(zone, Mapping)]
        usable = eligible > 0 and any(
            phase.get("status") in {"usable", "limited"}
            for zone in zones
            for phase in (
                _sequence(zone.get("tire_age_phases"))
                + _sequence(zone.get("observational_run_phases"))
            )
            if isinstance(phase, Mapping)
        )
        confirmed_age = str(item.get("phase_model")) in {
            "session_derived_tire_age_bands",
            "confirmed_age_run_thirds_proxy",
        }
        run_number = int(_number(item.get("run_number")) or 0)
        impact = _mapping(impacts.get(run_number))
        damage_eligible = impact.get("automatic_coaching_reference_eligible") is not False
        damage_rank = (
            2
            if damage_eligible and impact.get("status") != "partial_pre_incident_proxy"
            else 1
            if damage_eligible
            else 0
        )
        return damage_rank, usable, confirmed_age, eligible

    return max(runs, key=rank)


def _phase_contract(run: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
    model = str(run.get("phase_model") or "")
    if model == "session_derived_tire_age_bands":
        return "tire_age_phases", [
            {"key": "phase_1", "phase": "fresh", "label": "Fresh tires"},
            {"key": "phase_2", "phase": "settled", "label": "Settled tires"},
            {"key": "phase_3", "phase": "worn", "label": "Worn proxy"},
        ]
    early_label = "Early/new-set" if run.get("new_set_confirmed") is True else "Early"
    return "observational_run_phases", [
        {"key": "phase_1", "phase": "early", "label": early_label},
        {"key": "phase_2", "phase": "middle", "label": "Middle"},
        {"key": "phase_3", "phase": "late", "label": "Late/older-set proxy"},
    ]


def _phase_map(zone: Mapping[str, Any], source_key: str) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("phase") or "").lower(): item
        for item in _sequence(zone.get(source_key))
        if isinstance(item, Mapping)
    }


def _observed_baseline(summary: Mapping[str, Any]) -> dict[str, str]:
    if str(summary.get("status") or "") not in {"usable", "limited"}:
        return _claim(
            summary.get("reason") or "Phase guidance unavailable",
            "unavailable",
            words=8,
            chars=68,
        )
    metrics = _mapping(summary.get("metrics"))
    counts = _mapping(summary.get("metric_observation_counts"))
    lap_count = int(_number(summary.get("lap_count")) or 0)

    def observed(name: str, event_count_key: str | None = None) -> float | None:
        count = _number(counts.get(name))
        if count is None and event_count_key:
            count = _number(_mapping(summary.get("event_availability")).get(event_count_key))
        if count is None:
            count = lap_count
        return _number(metrics.get(name)) if count >= 2 else None

    entry = _fmt_number(observed("entry_speed_mph"))
    minimum = _fmt_number(observed("minimum_speed_mph"))
    exit_speed = _fmt_number(observed("exit_speed_mph"))
    peak_fraction = observed("brake_peak_fraction")
    peak = _fmt_number(peak_fraction * 100.0, 0) if peak_fraction is not None else None
    brake_onset_pct = observed("brake_onset_lap_pct", "brake_onset_laps")
    brake_release_pct = observed("brake_release_lap_pct", "brake_release_laps")
    turn_in_pct = observed("turn_in_lap_pct", "turn_in_laps")
    if not any((entry, minimum, exit_speed, peak, brake_onset_pct, brake_release_pct, turn_in_pct)):
        return _claim("Phase metrics unavailable", "unavailable", words=8, chars=68)
    speed = f"E{entry or '-'}/M{minimum or '-'}/X{exit_speed or '-'}"
    brake = f"B{peak}" if peak is not None else "B-"
    events = _mapping(summary.get("event_availability"))

    def timing(
        label: str,
        value: float | None,
        count_key: str,
        censored_key: str | None = None,
    ) -> str:
        if value is not None:
            return f"{label}{_fmt_number(value * 100.0)}"
        count = _number(events.get(count_key)) or 0.0
        if count == 1:
            return f"{label}n1"
        if censored_key and (_number(events.get(censored_key)) or 0.0) > 0.0:
            return f"{label}c"
        return f"{label}-"

    timing_text = "/".join(
        (
            timing("BO", brake_onset_pct, "brake_onset_laps", "brake_onset_boundary_censored_laps"),
            timing("BR", brake_release_pct, "brake_release_laps"),
            timing("TI", turn_in_pct, "turn_in_laps", "turn_in_boundary_censored_laps"),
        )
    )
    text = f"Obs {speed}; {brake}; {timing_text}"
    return _claim(text, "derived", words=11, chars=68)


def _target_for_zone(zone: Mapping[str, Any], targets: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    zone_tokens = {
        _ascii_text(zone.get(key)).lower()
        for key in ("zone_id", "zone_label", "name")
        if zone.get(key)
    }
    for target in targets:
        if _ranges_align(zone, target):
            return target
        target_name = _ascii_text(
            target.get("name") or target.get("corner") or target.get("label")
        ).lower()
        if target_name and target_name in zone_tokens:
            return target
    return {}


def _exact_target_cell(target: Mapping[str, Any]) -> dict[str, str] | None:
    entry = _fmt_number(target.get("entry_speed_mph"))
    minimum = _fmt_number(target.get("minimum_speed_mph"))
    if entry and minimum:
        return _claim(f"Target {entry} entry / {minimum} min mph", "inferred", words=11, chars=68)
    if entry:
        return _claim(f"Target {entry} mph entry", "inferred", words=11, chars=68)
    instruction = _safe_coaching(
        target.get("coaching") or target.get("instruction"), numeric_targets_usable=True
    )
    return _claim(instruction, "inferred", words=11, chars=68) if instruction else None


def _groove_cell(
    analysis: Mapping[str, Any],
    zone: Mapping[str, Any],
    selected_run_number: Any = None,
) -> dict[str, str]:
    def compact_direction(value: str) -> str:
        normalized = value.lower().strip()
        replacements = {
            "toward-low-side (inside)": "low/inside",
            "toward-high-side (outside)": "high/outside",
            "toward-low-side": "low/inside",
            "toward-high-side": "high/outside",
            "toward-inside": "inside",
            "toward-outside": "outside",
        }
        return replacements.get(normalized, normalized.replace("toward-", ""))

    candidates = _sequence(analysis.get("groove_model"))
    if isinstance(analysis.get("groove_model"), Mapping):
        candidates = _sequence(_mapping(analysis.get("groove_model")).get("zones"))
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        if not (_ranges_align(zone, item) or str(item.get("zone_id")) == str(zone.get("zone_id"))):
            continue
        calibration = _mapping(item.get("sign_calibration"))
        calibrated = bool(item.get("inside_outside_sign_calibrated")) or str(
            calibration.get("status") or item.get("calibration_status") or ""
        ).lower() == "calibrated"
        direction = _ascii_text(item.get("migration_direction") or item.get("direction")).lower()
        if calibrated and any(token in direction for token in ("inside", "outside", "higher", "lower", "high", "low")):
            trigger = _fmt_number(
                item.get("migration_trigger_green_lap") or item.get("first_sustained_green_lap"), 0
            )
            text = f"Observed {compact_direction(direction)} migration"
            text += f" after Lap {trigger}" if trigger else ""
            text += "; pace benefit unproven"
            return _claim(text, "derived", words=11, chars=68)
    evolution = _mapping(analysis.get("groove_evolution"))
    for groove_zone in _sequence(evolution.get("zones")):
        if not isinstance(groove_zone, Mapping) or not _ranges_align(zone, groove_zone):
            continue
        calibration = _mapping(groove_zone.get("inside_outside_calibration"))
        calibrated = bool(groove_zone.get("inside_outside_sign_calibrated")) or str(
            calibration.get("status") or ""
        ).lower() == "calibrated"
        runs = [item for item in _sequence(groove_zone.get("runs")) if isinstance(item, Mapping)]
        selected_candidates = [
            item for item in runs
            if selected_run_number is not None
            and str(item.get("run_number")) == str(selected_run_number)
        ]
        selected_candidates.extend(
            item for item in reversed(runs) if item not in selected_candidates
        )
        selected: Mapping[str, Any] = {}
        for candidate in selected_candidates:
            candidate_migration = _mapping(candidate.get("migration"))
            candidate_direction = _ascii_text(
                candidate_migration.get("display_direction")
                or candidate_migration.get("oval_low_high_direction")
                or candidate_migration.get("inside_outside_direction")
            ).lower()
            if candidate_migration.get("status") == "detected" and any(
                token in candidate_direction
                for token in ("inside", "outside", "higher", "lower", "high", "low")
            ):
                selected = candidate
                break
        migration = _mapping(selected.get("migration"))
        direction = _ascii_text(
            migration.get("display_direction")
            or migration.get("oval_low_high_direction")
            or migration.get("inside_outside_direction")
        ).lower()
        if calibrated and migration.get("status") == "detected" and any(
            token in direction for token in ("inside", "outside", "higher", "lower", "high", "low")
        ):
            trigger = _fmt_number(
                migration.get("first_sustained_green_lap")
                or migration.get("first_sustained_lap"),
                0,
            )
            run_label = _fmt_number(selected.get("run_number"), 0)
            text = "Observed " + (f"R{run_label} " if run_label else "") + f"{compact_direction(direction)} migration"
            text += f" after Lap {trigger}" if trigger else ""
            text += "; pace benefit unproven"
            return _claim(text, "derived", words=11, chars=68)
    return _claim("Groove direction unavailable", "unavailable", words=8, chars=68)


def _has_groove_calibration(analysis: Mapping[str, Any]) -> bool:
    model = analysis.get("groove_model")
    candidates = _sequence(model)
    if isinstance(model, Mapping):
        candidates = _sequence(_mapping(model).get("zones"))
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        calibration = _mapping(item.get("sign_calibration"))
        if item.get("inside_outside_sign_calibrated") or str(
            calibration.get("status") or item.get("calibration_status") or ""
        ).lower() == "calibrated":
            return True
    evolution = _mapping(analysis.get("groove_evolution"))
    return any(
        bool(zone.get("inside_outside_sign_calibrated"))
        or str(_mapping(zone.get("inside_outside_calibration")).get("status") or "").lower()
        == "calibrated"
        for zone in _sequence(evolution.get("zones"))
        if isinstance(zone, Mapping)
    )


def _fallback_zones(analysis: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    profile = _mapping(analysis.get("track_profile"))
    return [
        {
            "zone_id": f"load-zone-{item.get('segment') or index}",
            "zone_label": f"Load zone {item.get('segment') or index}",
            "corner_name_status": "provisional_telemetry_load_zone",
            **dict(item),
        }
        for index, item in enumerate(_sequence(profile.get("detected_corner_segments")), 1)
        if isinstance(item, Mapping)
    ]


def _corner_rows(
    analysis: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    *,
    numeric_targets_usable: bool,
    targets: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    corner = _mapping(analysis.get("corner_tire_age"))
    selected_run = _selected_corner_run(corner, analysis)
    zones = [item for item in _sequence(selected_run.get("zones")) if isinstance(item, Mapping)]
    source = "corner_tire_age" if zones else "track_profile_fallback"
    if not zones:
        zones = _fallback_zones(analysis)
    knowledge_zones = _knowledge_zones(knowledge)
    selected_run_number = selected_run.get("run_number")
    run_impact = _mapping(
        _damage_run_impacts(analysis).get(int(_number(selected_run_number) or 0))
    )
    selected_run_damage_eligible = (
        run_impact.get("automatic_coaching_reference_eligible") is not False
    )
    phase_source, phase_columns = _phase_contract(selected_run)
    rows: list[dict[str, Any]] = []
    for zone in zones[:MAX_CORNER_ROWS]:
        phases = (
            _phase_map(zone, phase_source)
            if selected_run_damage_eligible
            else {}
        )
        cells = {
            column["key"]: _observed_baseline(_mapping(phases.get(column["phase"])))
            if phases
            else _claim(f"{column['label']} unavailable", "unavailable", words=8, chars=68)
            for column in phase_columns
        }
        target = (
            _target_for_zone(zone, targets)
            if numeric_targets_usable and selected_run_damage_eligible
            else {}
        )
        if target and (target_cell := _exact_target_cell(target)) is not None:
            cells["phase_2"] = target_cell
        coaching = _mapping(zone.get("coaching")) if selected_run_damage_eligible else {}
        action = _safe_coaching(coaching.get("action"), numeric_targets_usable=False)
        if action:
            # A card cue is one complete instruction, not a clipped list of
            # every inferred adjustment in the deep-dive report.
            action = action.split(";", 1)[0].rstrip(". ") + "."
        phase_cue = (
            _claim(action, "inferred", words=11, chars=68)
            if action and coaching.get("status") == "usable"
            else None
        )
        label, label_status, label_source = _corner_label(zone, knowledge_zones)
        rows.append(
            {
                "corner_phase": label,
                "corner_name_status": label_status,
                "corner_name_source": label_source,
                "zone_id": zone.get("zone_id"),
                "start_pct": zone.get("start_pct"),
                "end_pct": zone.get("end_pct"),
                **cells,
                "groove": (
                    _groove_cell(analysis, zone, selected_run_number)
                    if selected_run_damage_eligible
                    else _claim(
                        "Repair-affected baseline; groove unavailable",
                        "unavailable",
                        words=8,
                        chars=68,
                    )
                ),
                "phase_cue": phase_cue,
            }
        )
    omitted = max(0, len(zones) - len(rows))
    return rows, {
        "source": source,
        "status": corner.get("status") or ("limited" if zones else "unavailable"),
        "selected_run_number": selected_run.get("run_number"),
        "phase_model": selected_run.get("phase_model"),
        "phase_source": phase_source,
        "phase_columns": phase_columns,
        "new_set_confirmed": selected_run.get("new_set_confirmed"),
        "selected_run_damage_repair_context": dict(run_impact),
        "selected_run_automatic_coaching_reference_eligible": selected_run_damage_eligible,
        "omitted_row_count": omitted,
    }


def _tire_focus(analysis: Mapping[str, Any]) -> tuple[str | None, float | None, int | None]:
    candidates: list[tuple[str, float, int | None]] = []
    for run in _sequence(analysis.get("runs")):
        if not isinstance(run, Mapping):
            continue
        observation = _mapping(run.get("tire_observation"))
        tire = _ascii_text(observation.get("lowest_remaining_tire")).upper()
        remaining = _number(observation.get("lowest_remaining_percent"))
        if tire and remaining is not None:
            candidates.append((tire, remaining, int(_number(run.get("run_number")) or 0) or None))
    return min(candidates, key=lambda item: item[1]) if candidates else (None, None, None)


def _damage_repair_card_context(
    analysis: Mapping[str, Any], selected_run_number: Any = None
) -> dict[str, Any]:
    damage = _mapping(analysis.get("damage_repair"))
    summary = _mapping(damage.get("summary"))
    repair_episodes = int(_number(summary.get("recorded_repair_episodes")) or 0)
    repair_flag_episodes = int(
        _number(summary.get("repair_required_flag_episodes")) or 0
    )
    tow_episodes = int(_number(summary.get("tow_episodes")) or 0)
    fast_repairs = int(_number(summary.get("confirmed_fast_repair_uses")) or 0)
    material = bool(repair_episodes or repair_flag_episodes or tow_episodes or fast_repairs)
    run_number = int(_number(selected_run_number) or 0)
    run_impact = _mapping(_damage_run_impacts(analysis).get(run_number))
    selected_eligible = (
        run_impact.get("automatic_coaching_reference_eligible") is not False
    )
    post_stop = _mapping(run_impact.get("post_stop_context"))
    optional_remaining = _number(
        post_stop.get("optional_repair_remaining_at_stall_exit_s")
    )
    mandatory_remaining = _number(
        post_stop.get("mandatory_repair_remaining_at_stall_exit_s")
    )
    total_stall = _number(summary.get("total_pit_stall_time_s"))
    total_tow = _number(summary.get("total_tow_active_time_s"))
    countdown = 0.0
    countdown_available = False
    for episode in _sequence(damage.get("episodes")):
        if not isinstance(episode, Mapping):
            continue
        for key in ("mandatory_repair", "optional_repair"):
            value = _number(_mapping(episode.get(key)).get("countdown_observed_s"))
            if value is not None:
                countdown += max(0.0, value)
                countdown_available = True
    return {
        "status": damage.get("status") or "unavailable",
        "material": material,
        "selected_run_number": run_number or None,
        "selected_run_eligible": selected_eligible,
        "selected_run_status": run_impact.get("status"),
        "optional_repair_remaining_s": optional_remaining,
        "mandatory_repair_remaining_s": mandatory_remaining,
        "recorded_repair_episodes": repair_episodes,
        "repair_required_flag_episodes": repair_flag_episodes,
        "tow_episodes": tow_episodes,
        "confirmed_fast_repair_uses": fast_repairs,
        "total_stall_time_s": total_stall,
        "total_tow_active_time_s": total_tow,
        "repair_countdown_observed_s": countdown if countdown_available else None,
    }


def _damage_repair_evidence_claim(context: Mapping[str, Any]) -> dict[str, str] | None:
    if not context.get("material"):
        return None
    parts: list[str] = []
    repair_episodes = int(_number(context.get("recorded_repair_episodes")) or 0)
    repair_flag_episodes = int(
        _number(context.get("repair_required_flag_episodes")) or 0
    )
    tow_episodes = int(_number(context.get("tow_episodes")) or 0)
    countdown = _number(context.get("repair_countdown_observed_s"))
    stall = _number(context.get("total_stall_time_s"))
    tow = _number(context.get("total_tow_active_time_s"))
    if repair_episodes:
        parts.append(f"{repair_episodes} recorded repair episode(s)")
    elif repair_flag_episodes:
        parts.append(f"{repair_flag_episodes} repair-required state episode(s)")
    if countdown is not None and countdown > 0.05:
        parts.append(f"{countdown:.1f}s repair countdown consumed")
    if tow_episodes:
        parts.append(f"{tow_episodes} tow episode(s), {tow or 0.0:.1f}s observed")
    if stall is not None and stall > 0.05:
        parts.append(f"{stall:.1f}s in stall")
    parts.append("overlapping clocks prevent isolating repair-only time")
    return _claim("; ".join(parts), "derived", words=28, chars=190)


def _ordered_signals(analysis: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    priority = {"high": 0, "medium": 1, "low": 2}
    signals = [item for item in _sequence(analysis.get("coaching_signals")) if isinstance(item, Mapping)]
    return [
        item for _, item in sorted(
            enumerate(signals),
            key=lambda pair: (priority.get(str(pair[1].get("priority", "medium")).lower(), 1), pair[0]),
        )
    ]


def _history_strategy_baseline(
    historical_runs: Sequence[Mapping[str, Any]],
) -> tuple[float | None, float | None]:
    green_laps: list[float] = []
    burns: list[float] = []
    for row in historical_runs:
        if not isinstance(row, Mapping):
            continue
        metrics = _mapping(row.get("metrics"))
        repair_context = _mapping(metrics.get("damage_repair_context"))
        if repair_context.get("automatic_coaching_reference_eligible") is False:
            continue
        fuel = _mapping(metrics.get("fuel"))
        green = _number(row.get("green_laps"))
        if green is None:
            green = _number(metrics.get("green_laps"))
        burn = _number(fuel.get("green_gal_per_lap"))
        if burn is None:
            used_l = _number(row.get("fuel_used_l"))
            burn = used_l / 3.785411784 / green if used_l is not None and green else None
        if green is not None and green > 0:
            green_laps.append(green)
        if burn is not None and burn > 0:
            burns.append(burn)
    return (
        statistics.median(green_laps) if green_laps else None,
        statistics.median(burns) if burns else None,
    )


def _strategy_claim(
    analysis: Mapping[str, Any],
    historical_runs: Sequence[Mapping[str, Any]] = (),
    *,
    words: int = 14,
    planned_laps: float | None = None,
) -> dict[str, str]:
    eligible_history = [
        row
        for row in historical_runs
        if not (
            isinstance(row, Mapping)
            and _mapping(_mapping(row.get("metrics")).get("damage_repair_context")).get(
                "automatic_coaching_reference_eligible"
            ) is False
        )
    ]
    strategy = _mapping(analysis.get("strategy"))
    forecast = _mapping(strategy.get("forecast"))
    if forecast.get("status") == "usable":
        race = _mapping(analysis.get("race_summary"))
        distance = planned_laps
        if distance is None:
            distance = _number(race.get("scheduled_laps"))
        range_number = _number(forecast.get("all_green_range_laps"))
        stop_count = _number(forecast.get("minimum_stops_all_green"))
        target_numbers = [
            value
            for raw in _sequence(forecast.get("equal_stint_pit_targets_all_green"))[:3]
            if (value := _number(raw)) is not None
        ]
        if distance is not None and distance > 0.0 and range_number not in (None, 0.0):
            stop_count = max(0, math.ceil(distance / float(range_number) - 1e-9) - 1)
            stint_count = stop_count + 1
            target_numbers = [
                distance * index / stint_count
                for index in range(1, stint_count)
            ][:3]
        stops = _fmt_number(stop_count, 0)
        targets = [
            rendered
            for value in target_numbers
            if (rendered := _fmt_number(value, 0))
        ]
        reserve = _fmt_number(forecast.get("operational_reserve_green_laps"), 0)
        if stop_count == 0 and distance is not None and range_number is not None:
            margin = range_number - distance
            text = (
                f"No fuel stop needed for {distance:.0f} laps; {margin:.1f}-lap range margin"
                if margin >= 0.0
                else f"Measured range is {abs(margin):.1f} laps short for {distance:.0f} laps"
            )
        elif stops and targets:
            stop_word = "stop" if stops == "1" else "stops"
            distance_part = f" for {distance:.0f} laps" if distance is not None else ""
            text = f"Plan {stops} fuel {stop_word}{distance_part}; target Lap {'/'.join(targets)}"
        else:
            range_laps = _fmt_number(range_number)
            text = f"Measured all-green range is {range_laps} laps" if range_laps else "Fuel range is available"
        if reserve and stop_count != 0 and len(text.split()) < words - 3:
            text += f" with {reserve}-lap reserve"
        if eligible_history and len(text.split()) < words - 3:
            text += f"; {len(eligible_history)} screened prior runs"
        return _claim(text, "derived", words=words, chars=100)
    burn = _fmt_number(strategy.get("measured_green_fuel_gal_per_lap"), 3)
    if burn:
        return _claim(f"Observed burn is {burn} gal/green lap", "derived", words=words, chars=100)
    historical_stint, historical_burn = _history_strategy_baseline(eligible_history)
    if historical_stint is not None or historical_burn is not None:
        parts = ["Prior same-context median"]
        if historical_stint is not None:
            parts.append(f"{historical_stint:.1f} green laps")
        if historical_burn is not None:
            parts.append(f"{historical_burn:.3f} gal/green lap")
        return _claim("; ".join(parts), "derived", words=words, chars=100)
    return _claim("Fuel and pit window unavailable", "unavailable", words=words, chars=100)


def _fuel_response_claim(
    analysis: Mapping[str, Any],
    *,
    planned_laps: float | None = None,
) -> dict[str, str]:
    """Turn fuel feasibility into an explicit in-race decision rule."""

    forecast = _mapping(_mapping(analysis.get("strategy")).get("forecast"))
    race = _mapping(analysis.get("race_summary"))
    distance = planned_laps
    if distance is None:
        distance = _number(race.get("scheduled_laps"))
    range_laps = _number(forecast.get("all_green_range_laps"))
    if forecast.get("status") != "usable" or distance is None or range_laps in (None, 0.0):
        return _claim(
            "Update the fuel call only after live burn establishes a finish margin",
            "inferred",
            words=16,
            chars=116,
        )

    stops = max(0, math.ceil(distance / float(range_laps) - 1e-9) - 1)
    if stops == 0:
        return _claim(
            f"Stay out while projected range clears the {distance:.0f}-lap finish; reconsider only if the margin disappears",
            "derived",
            words=18,
            chars=126,
        )

    targets = [
        max(1, int(round(distance * index / (stops + 1))))
        for index in range(1, stops + 1)
    ]
    target_text = "/".join(str(value) for value in targets[:3])
    return _claim(
        f"Target Lap {target_text}; move the stop only when live burn no longer supports the next stint",
        "derived",
        words=18,
        chars=126,
    )


def _phase_trigger(
    corner_meta: Mapping[str, Any], selected_run: Mapping[str, Any]
) -> dict[str, str]:
    phase_source = str(corner_meta.get("phase_source") or "observational_run_phases")
    columns = [item for item in _sequence(corner_meta.get("phase_columns")) if isinstance(item, Mapping)]
    bounds: dict[str, tuple[float, float]] = {}
    for zone in _sequence(selected_run.get("zones")):
        if not isinstance(zone, Mapping):
            continue
        for summary in _sequence(zone.get(phase_source)):
            if not isinstance(summary, Mapping) or summary.get("status") == "unavailable":
                continue
            starts: list[float] = []
            ends: list[float] = []
            for detail in _mapping(_mapping(summary.get("green_lap_on_set_bounds")).get("by_tire")).values():
                if not isinstance(detail, Mapping):
                    continue
                if (value := _number(detail.get("start"))) is not None:
                    starts.append(value)
                if (value := _number(detail.get("end"))) is not None:
                    ends.append(value)
            if starts and ends:
                bounds[str(summary.get("phase"))] = (min(starts), max(ends))
    parts = []
    for column in columns:
        value = bounds.get(str(column.get("phase")))
        if value is not None:
            label = _ascii_text(column.get("label")).replace("/older-set proxy", "").replace("/new-set", "")
            parts.append(f"{label} {value[0]:.1f}-{value[1]:.1f}")
    return _claim(
        ("Recheck balance at " + ", ".join(parts) + " green laps") if parts else "Phase green-lap bounds unavailable",
        "derived" if parts else "unavailable",
        words=16,
        chars=116,
    )


def _evidence_appendix(
    analysis: Mapping[str, Any],
    corner_meta: Mapping[str, Any],
    comparison_quality: Mapping[str, Any],
    historical_run_count: int,
) -> list[dict[str, str]]:
    identity = _mapping(analysis.get("identity"))
    source = _mapping(analysis.get("source"))
    quality = _mapping(analysis.get("data_quality"))
    evidence = [
        _claim(
            f"Analysis {analysis.get('analysis_id') or 'unknown'}; subsession {identity.get('subsession_id') or 'unknown'}; {len(_sequence(source.get('telemetry_files')))} finalized IBT source(s)",
            "measured",
            words=24,
            chars=160,
        ),
        _claim(
            f"Local data confidence: {quality.get('confidence') or 'unknown'}; corner source: {corner_meta.get('source')}",
            "derived",
            words=24,
            chars=160,
        ),
    ]
    damage_context = _damage_repair_card_context(
        analysis, corner_meta.get("selected_run_number")
    )
    damage_claim = _damage_repair_evidence_claim(damage_context)
    if damage_claim is not None:
        evidence.append(damage_claim)
    tire, remaining, run_number = _tire_focus(analysis)
    if tire and remaining is not None:
        evidence.append(
            _claim(
                f"Run {run_number or '?'} post-service reading: {tire} lowest at {remaining:.1f}% remaining",
                "measured",
                words=24,
                chars=160,
            )
        )
    else:
        evidence.append(_claim("Tire wear endpoint not measured", "unavailable", words=24, chars=160))
    comparison_status = _ascii_text(comparison_quality.get("status")) or "unavailable"
    evidence.append(
        _claim(
            f"Aligned reference comparison: {comparison_status}; setup scope: {comparison_quality.get('setup_scope') or 'unavailable'}",
            "derived" if comparison_status == "usable" else "unavailable",
            words=24,
            chars=160,
        )
    )
    if not _has_groove_calibration(analysis):
        evidence.append(
            _claim("Inside/outside groove direction is not calibrated", "unavailable", words=24, chars=160)
        )
    forecast = _mapping(_mapping(analysis.get("strategy")).get("forecast"))
    if forecast:
        evidence.append(
            _claim(
                f"Fuel forecast is feasibility only; {historical_run_count} prior runs reviewed; position, pit loss, rules, and future cautions remain inputs",
                "derived",
                words=24,
                chars=160,
            )
        )
    return evidence[:MAX_EVIDENCE_ITEMS]


def build_race_card(
    analysis: Mapping[str, Any],
    *,
    historical_runs: Sequence[Mapping[str, Any]] | None = None,
    knowledge: Mapping[str, Any] | None = None,
    race_distance_laps: float | int | None = None,
) -> dict[str, Any]:
    """Build a bounded, JSON-safe Race Card object without performing I/O."""

    if not isinstance(analysis, Mapping):
        raise TypeError("analysis must be a mapping")
    history_rows = tuple(
        item for item in (historical_runs or ()) if isinstance(item, Mapping)
    )
    cached = _mapping(knowledge)
    identity = _mapping(analysis.get("identity"))
    race = _mapping(analysis.get("race_summary"))
    planned_laps = _number(race_distance_laps)
    if planned_laps is not None and planned_laps <= 0.0:
        raise ValueError("race_distance_laps must be greater than zero")
    oval = _is_oval(identity)
    exact_usable, targets, comparison_quality = _comparison_components(cached)
    rows, corner_meta = _corner_rows(
        analysis,
        cached,
        numeric_targets_usable=exact_usable,
        targets=targets,
    )
    corner = _mapping(analysis.get("corner_tire_age"))
    selected_run = next(
        (
            item
            for item in _sequence(corner.get("runs"))
            if isinstance(item, Mapping)
            and str(item.get("run_number")) == str(corner_meta.get("selected_run_number"))
        ),
        _selected_corner_run(corner, analysis),
    )
    damage_context = _damage_repair_card_context(
        analysis, corner_meta.get("selected_run_number")
    )
    tire, _, _ = _tire_focus(analysis)
    signals = _ordered_signals(analysis)
    corner_primary = next(
        (
            text
            for row in rows
            if (
                text := _driver_instruction(
                    _mapping(row.get("phase_cue")).get("text"),
                    numeric_targets_usable=False,
                )
            )
        ),
        None,
    )
    signal_primary = next(
        (
            text
            for signal in signals
            if signal.get("run_number") is not None
            and (text := _driver_instruction(signal.get("coaching"), numeric_targets_usable=exact_usable))
        ),
        None,
    )
    technical_primary = next(
        (
            text
            for insight in _sequence(analysis.get("technical_insights"))
            if isinstance(insight, Mapping)
            for item in _sequence(insight.get("metrics"))
            if isinstance(item, Mapping)
            and str(item.get("tone") or "").lower() == "attention"
            and (text := _driver_instruction(item.get("action"), numeric_targets_usable=False))
        ),
        None,
    )
    primary = corner_primary or signal_primary or technical_primary
    if not primary:
        primary = (
            f"Protect the {tire} from the start: finish brake release before adding steering"
            if tire
            else "Open conservatively: finish brake release before adding steering, then build throttle"
        )
    primary = _compact(primary.split(";", 1)[0], words=12, chars=82)
    strategy = _strategy_claim(
        analysis,
        history_rows,
        planned_laps=planned_laps,
    )
    effective_distance = planned_laps
    if effective_distance is None:
        effective_distance = _number(race.get("scheduled_laps"))
    short_race = effective_distance is not None and effective_distance <= 25.0
    long_run_label = "Race pace" if short_race else "Long run"
    long_run_text = (
        "Reset the baseline after repairs; judge pace only on a clean repaired-car run"
        if damage_context.get("selected_run_eligible") is False
        else (
            f"For {effective_distance:.0f} laps, protect the {tire} with repeatable entries and progressive brake release"
            if short_race and tire
            else f"As the run ages, protect the {tire} with repeatable entries and progressive brake release"
        )
        if tire
        else f"For {effective_distance:.0f} laps, keep brake release, throttle pickup, and steering corrections repeatable"
        if short_race
        else "Keep entries, throttle pickup, and steering corrections repeatable as the run ages"
    )
    actions = [
        {"label": "Start", **_claim(primary, "inferred", words=12, chars=82)},
        {
            "label": long_run_label,
            **_claim(long_run_text, "inferred", words=14, chars=96),
        },
        {"label": "Strategy", **strategy},
    ]
    phase_trigger = _phase_trigger(corner_meta, selected_run)
    setup_open = identity.get("is_fixed_setup") is False
    if damage_context.get("selected_run_eligible") is False:
        respond = _claim(
            "Validate a clean repaired-car run before changing setup or target traces",
            "inferred",
            words=16,
            chars=116,
        )
    else:
        respond = _claim(
            "If balance changes, alter one driving input at a time; undo it if pace or stability worsens",
            "inferred",
            words=18,
            chars=126,
        )
    car = _ascii_text(identity.get("car_name") or identity.get("car_path") or "Unknown car")
    track = _ascii_text(identity.get("track_name") or "Unknown track")
    setup = "Fixed" if identity.get("is_fixed_setup") is True else "Open" if setup_open else "Setup unknown"
    title = _compact(
        f"{track} Race Card - {car} | {setup} | {_distance_label(race, planned_laps)}",
        words=18,
        chars=140,
    )
    card: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "analysis_id": analysis.get("analysis_id"),
        "title": title,
        "discipline": "oval" if oval else "road_or_other",
        "word_limit_before_evidence": OVAL_WORD_LIMIT if oval else ROAD_WORD_LIMIT,
        "bottom_line": (
            _claim(
                f"Run {corner_meta.get('selected_run_number') or '?'} is repair-affected; use only screened clean laps for coaching",
                "derived",
                words=18,
                chars=126,
            )
            if damage_context.get("selected_run_eligible") is False
            else _claim(
                f"{tire} was the lowest measured tire; use the phase plan and fuel window"
                if tire
                else "Use the strongest race phase as the execution baseline and follow the distance-specific fuel plan",
                "inferred",
                words=18,
                chars=126,
            )
        ),
        "actions": actions,
        "corner_playbook": {**corner_meta, "rows": rows},
        "data_context": {
            "damage_repair": damage_context,
            "alert_active": damage_context.get("selected_run_eligible") is False,
            "alert_rule": "Repair/tow context screens automatic corner and target-lap evidence",
        },
        "race_triggers": [
            {"label": "Balance checkpoint", **phase_trigger},
            {
                "label": "Fuel response",
                **_fuel_response_claim(
                    analysis,
                    planned_laps=planned_laps,
                ),
            },
            {"label": "Balance response", **respond},
        ],
        "evidence_appendix": _evidence_appendix(
            analysis, corner_meta, comparison_quality, len(history_rows)
        ),
        "summary": {
            "scheduled_laps": race.get("scheduled_laps"),
            "planned_laps": int(round(planned_laps)) if planned_laps is not None else None,
            "recorded_laps": race.get("recorded_laps"),
            "green_laps": race.get("green_laps_estimated"),
            "caution_laps": race.get("caution_laps_estimated"),
            "runs": race.get("runs_detected"),
            "pit_stops": race.get("pit_stops_detected"),
            "historical_runs_considered": len(history_rows),
            "damage_repair": damage_context,
        },
        "target_policy": {
            "comparison_quality_status": comparison_quality.get("status") or "unavailable",
            "exact_numeric_targets_emitted": bool(
                exact_usable
                and damage_context.get("selected_run_eligible") is not False
                and any(
                    str(_mapping(row.get("phase_2")).get("text") or "").startswith("Target ")
                    for row in rows
                )
            ),
            "rule": "Exact numeric coaching targets require a usable comparison and an automatically eligible local run",
        },
        "tag_legend": dict(EVIDENCE_TAGS),
    }
    markdown = render_race_card(card)
    if race_card_word_count(markdown) > card["word_limit_before_evidence"]:
        card["compact_mode"] = True
        markdown = render_race_card(card)
    card["word_count_before_evidence"] = race_card_word_count(markdown)
    card["within_word_limit"] = (
        card["word_count_before_evidence"] <= card["word_limit_before_evidence"]
    )
    return card


def render_race_card(card: Mapping[str, Any]) -> str:
    """Render a built Race Card as deterministic ASCII-safe Markdown."""

    if not isinstance(card, Mapping):
        raise TypeError("card must be a mapping")
    playbook = _mapping(card.get("corner_playbook"))
    rows = _sequence(playbook.get("rows"))
    dense = bool(card.get("compact_mode")) or len(rows) > 4

    def render_claim(value: Mapping[str, Any]) -> str:
        return _render_compact_claim(value) if dense else _render_claim(value)

    def visible_claim(value: Mapping[str, Any]) -> bool:
        return bool(_ascii_text(value.get("text"))) and str(
            value.get("evidence_type") or ""
        ).lower() != "unavailable"

    lines = [f"# {_ascii_text(card.get('title'))}", ""]
    bottom_line = _mapping(card.get("bottom_line"))
    if visible_claim(bottom_line):
        lines.append(f"Bottom line: {render_claim(bottom_line)}")
        lines.append("")
    for action in _sequence(card.get("actions"))[:3]:
        if isinstance(action, Mapping) and visible_claim(action):
            lines.append(f"- {_ascii_text(action.get('label'))}: {render_claim(action)}")
    phase_columns = [
        item for item in _sequence(playbook.get("phase_columns")) if isinstance(item, Mapping)
    ]
    if len(phase_columns) != 3:
        phase_columns = [
            {"key": "phase_1", "label": "Early"},
            {"key": "phase_2", "label": "Middle"},
            {"key": "phase_3", "label": "Late/older-set proxy"},
        ]
    visible_rows = []
    for row_index, row in enumerate(rows[:MAX_CORNER_ROWS], 1):
        if not isinstance(row, Mapping):
            continue
        claims = []
        for column in phase_columns:
            claim = _mapping(row.get(str(column.get("key"))))
            if visible_claim(claim):
                claims.append((_ascii_text(column.get("label")), claim))
        groove = _mapping(row.get("groove"))
        if visible_claim(groove):
            claims.append(("Driving line", groove))
        cue = _mapping(row.get("phase_cue"))
        if visible_claim(cue) and not dense:
            claims.append(("Cue", cue))
        if claims:
            row_label = _ascii_text(row.get("corner_phase")) or "Load zone"
            if card.get("compact_mode"):
                zone_match = re.search(r"(\d+)$", _ascii_text(row.get("zone_id")))
                row_label = f"Z{zone_match.group(1)}" if zone_match else f"Z{row_index}"
            visible_rows.append((row_label, claims))
    if visible_rows:
        lines.extend(["", "## Corner playbook", ""])
        displayed_rows = visible_rows[:4] if dense else visible_rows
        for row_label, claims in displayed_rows:
            lines.append(f"### {row_label}")
            for label, claim in claims:
                lines.append(f"- {label}: {render_claim(claim)}")
            lines.append("")
        if len(displayed_rows) < len(visible_rows):
            lines.append("Additional measured load zones remain in the full analysis.")

    visible_triggers = [
        trigger for trigger in _sequence(card.get("race_triggers"))[:3]
        if isinstance(trigger, Mapping) and visible_claim(trigger)
    ]
    if visible_triggers:
        lines.extend(["", "## Race triggers", ""])
        for trigger in visible_triggers:
            lines.append(f"- {_ascii_text(trigger.get('label'))}: {render_claim(trigger)}")

    visible_appendix = [
        item for item in _sequence(card.get("evidence_appendix"))[:MAX_EVIDENCE_ITEMS]
        if isinstance(item, Mapping) and visible_claim(item)
    ]
    if visible_appendix:
        lines.extend(["", "## Evidence appendix", "", "Tags: [M] measured; [D] calculated; [I] coaching inference; [P] proxy.", ""])
        for item in visible_appendix:
            lines.append(f"- {_render_claim(item)}")
    return "\n".join(_ascii_text(line) for line in lines).strip() + "\n"


def race_card_word_count(markdown: str) -> int:
    """Count words before the evidence appendix for the visible-card budget."""

    before = str(markdown).split("## Evidence appendix", 1)[0]
    # Treat compact telemetry tokens and decimal numbers as single visible
    # words (for example E121.5/M97.4/X111.1 and BO7.9/BR17.9/TI9.3).
    return len(
        re.findall(
            r"[A-Za-z0-9]+(?:[./%'<>=>-]+[A-Za-z0-9]+)*",
            before,
        )
    )
