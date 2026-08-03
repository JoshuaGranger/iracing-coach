"""Concise Markdown and dependency-free SVG reporting for iRacing analysis.

The analysis engine deliberately records measurements separately from causal
interpretation.  This module preserves that distinction in the human-facing
report: telemetry facts are labelled as measured, while coaching and strategy
judgements are labelled as inferences.  In particular, tire readings are never
presented as a continuous wear trace because iRacing only refreshes them at a
pit-service/inspection boundary.
"""

from __future__ import annotations

import html
import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


TIRES = ("LF", "RF", "LR", "RR")
TIRE_COLORS = {
    "LF": "#43c6ac",
    "RF": "#4f8cff",
    "LR": "#f5b942",
    "RR": "#ef6a74",
}
BACKGROUND = "#101820"
PANEL = "#18242f"
GRID = "#324452"
TEXT = "#edf4f7"
MUTED = "#9fb0bd"
ACCENT = "#55d6be"
WARNING = "#f5b942"

__all__ = ["render_report", "render_visuals"]


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


def _median(values: Sequence[Any]) -> float | None:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return statistics.median(numbers) if numbers else None


def _percentile(values: Sequence[Any], fraction: float) -> float | None:
    numbers = sorted(number for value in values if (number := _number(value)) is not None)
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    position = max(0.0, min(1.0, fraction)) * (len(numbers) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return numbers[lower]
    weight = position - lower
    return numbers[lower] * (1.0 - weight) + numbers[upper] * weight


def _first_number(*values: Any) -> float | None:
    for value in values:
        if (number := _number(value)) is not None:
            return number
    return None


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    if digits == 0:
        rendered = f"{number:.0f}"
    else:
        rendered = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _lap_count(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:.0f}" if abs(number - round(number)) < 0.005 else f"{number:.1f}"


def _session_clock(value: Any) -> str:
    seconds = _number(value)
    if seconds is None:
        return "Not recorded"
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return (
        f"{hours:d}:{minutes:02d}:{remainder:04.1f}"
        if hours
        else f"{minutes:d}:{remainder:04.1f}"
    )


def _timeline_rows(timeline: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for event in _sequence(timeline.get("events"))[:30]:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("event_type") or "event")
        start_lap = event.get("start_lap")
        end_lap = event.get("end_lap")
        lap_text = str(start_lap) if start_lap == end_lap else f"{start_lap}-{end_lap}"
        if event_type == "caution_period":
            label = "Sampled caution period"
            detail = (
                f"{_fmt(event.get('caution_lap_equivalents'), 2)} caution-lap equivalents; "
                "derived grouping, official scoring cross-check required"
            )
        elif event_type in {"pit_service", "post_run_service"}:
            label = "Post-run service" if event_type == "post_run_service" else "Race pit service"
            facts: list[str] = []
            confirmed_tires = [str(item) for item in _sequence(event.get("confirmed_tires"))]
            requested_tires = [str(item) for item in _sequence(event.get("requested_tires"))]
            if confirmed_tires:
                facts.append("confirmed tires " + "/".join(confirmed_tires))
            if requested_tires:
                facts.append("requested tires " + "/".join(requested_tires))
            fuel_added = _number(event.get("confirmed_fuel_added_l"))
            if fuel_added is not None and fuel_added > 0.01:
                facts.append(f"confirmed fuel {fuel_added / 3.785411784:.2f} gal")
            requested_fuel = _number(event.get("requested_fuel_add_l"))
            if requested_fuel is not None and requested_fuel > 0.01:
                facts.append(f"requested fuel {requested_fuel / 3.785411784:.2f} gal")
            facts.append(str(event.get("confirmation") or "service evidence partial").replace("_", " "))
            detail = "; ".join(facts)
        elif event_type == "race_start":
            label, detail = "Recorded race start", "Measured SessionTime/Lap boundary"
        elif event_type == "race_end":
            label, detail = "Recorded race end", "End of local telemetry; not official classification time"
        else:
            label = event_type.replace("_", " ").title()
            detail = str(event.get("evidence_class") or "")
        rows.append([
            _session_clock(event.get("session_time_s")),
            lap_text,
            label,
            detail,
        ])
    return rows


def _repair_timer_text(value: Mapping[str, Any]) -> str:
    status = str(value.get("status") or "unavailable")
    if status == "unavailable":
        return "No recorded timer"
    if status == "recorded_zero":
        return "Recorded zero"
    peak = _number(value.get("peak_remaining_s"))
    remaining = _first_number(
        value.get("remaining_at_stall_exit_s"),
        value.get("last_positive_remaining_s"),
    )
    reduction = _first_number(
        value.get("repair_work_completed_s"),
        value.get("timer_reduction_s"),
        value.get("countdown_observed_s"),
    )
    text = f"peak {_fmt(peak, 1, ' s')}"
    if remaining is not None:
        text += f"; {_fmt(remaining, 1, ' s')} remained"
    if reduction is not None:
        text += f"; {_fmt(reduction, 1, ' s')} countdown consumed"
    completion = str(value.get("completion_status") or "").replace("_", " ")
    if completion:
        text += f"; {completion}"
    return text


def _damage_repair_rows(damage: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for episode in _sequence(damage.get("episodes"))[:30]:
        if not isinstance(episode, Mapping):
            continue
        timing = _mapping(episode.get("timing"))
        laps = [str(item) for item in _sequence(episode.get("candidate_lap_numbers"))]
        lap_text = (
            laps[0]
            if len(laps) == 1
            else f"{laps[0]}-{laps[-1]}"
            if laps
            else "Recorded event"
        )
        pit_parts = []
        for key, label in (
            ("pit_road_time_s", "road"),
            ("pit_stall_time_s", "stall"),
            ("pitstop_service_active_time_s", "service"),
        ):
            if (number := _number(timing.get(key))) is not None and number > 0.01:
                pit_parts.append(f"{label} {number:.1f} s")
        tow_parts = []
        tow = _mapping(episode.get("tow"))
        if (number := _first_number(tow.get("active_time_s"), timing.get("tow_active_time_s"))) is not None and number > 0.01:
            tow_parts.append(f"observed {number:.1f} s")
        if (number := _first_number(tow.get("peak_remaining_s"), timing.get("tow_timer_peak_s"))) is not None and number > 0.01:
            tow_parts.append(f"timer peak {number:.1f} s")
        if (number := _number(tow.get("last_remaining_s"))) is not None and number > 0.01:
            tow_parts.append(f"{number:.1f} s remained")
        tow_status = str(tow.get("completion_status") or "").replace("_", " ")
        if tow_status:
            tow_parts.append(tow_status)
        run_context = _mapping(episode.get("run_context"))
        affected_runs = [
            str(item) for item in _sequence(run_context.get("overlapping_run_numbers"))
        ]
        preceding = run_context.get("preceding_run_number")
        following = run_context.get("following_run_number")
        impact_parts = []
        if preceding is not None:
            impact_parts.append(f"preceded by Run {preceding}")
        if following is not None:
            impact_parts.append(f"followed by Run {following}")
        if affected_runs:
            impact_parts.append("overlap Run " + "/".join(affected_runs))
        status_values = [
            str(item.get("label"))
            for item in _sequence(_mapping(episode.get("pit_service_status")).get("observed"))
            if isinstance(item, Mapping) and item.get("label")
        ]
        fast = _mapping(episode.get("fast_repair"))
        context_text = str(episode.get("classification") or "episode").replace("_", " ")
        if status_values:
            context_text += "; pit status " + "/".join(status_values)
        if fast.get("requested"):
            context_text += "; fast repair requested"
        if fast.get("request_confirmed_as_use"):
            context_text += "/confirmed used"
        rows.append(
            [
                f"{_session_clock(episode.get('start_session_time_s'))}; L{lap_text}",
                context_text,
                "; ".join(pit_parts) or "None recorded",
                "; ".join(tow_parts) or "None recorded",
                _repair_timer_text(_mapping(episode.get("mandatory_repair"))),
                _repair_timer_text(_mapping(episode.get("optional_repair"))),
                "; ".join(impact_parts) or "Outside mapped runs",
            ]
        )
    return rows


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    if not rows:
        return []
    result = [
        "| " + " | ".join(_markdown_cell(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend(
        "| " + " | ".join(_markdown_cell(item) for item in row) + " |"
        for row in rows
    )
    return result


def _identity_title(identity: Mapping[str, Any]) -> tuple[str, str]:
    car = identity.get("car_name") or identity.get("car_path") or "Unknown car"
    track = identity.get("track_name") or "Unknown track"
    config = identity.get("track_config")
    if config and str(config).lower() not in str(track).lower():
        track = f"{track} — {config}"
    fixed = identity.get("is_fixed_setup")
    setup_type = "fixed setup" if fixed is True else "open setup" if fixed is False else "setup type unknown"
    return f"{car} at {track}", setup_type


def _run_tire_summary(run: Mapping[str, Any]) -> str:
    observation = _mapping(run.get("tire_observation"))
    if observation:
        tire = observation.get("lowest_remaining_tire")
        remaining = _number(observation.get("lowest_remaining_percent"))
        reading_label = (
            "post-run service reading"
            if run.get("ended_with_post_run_service")
            else "pit-service reading"
        )
        if tire and remaining is not None:
            return f"{tire} lowest, {remaining:.1f}% remaining ({reading_label})"
        return f"Measured at {reading_label}"
    status = str(run.get("tire_measurement_status") or "")
    if status == "unmeasured_final_run":
        return "Not measured after final run"
    if status == "unavailable_at_stop":
        return "Stop detected without a changed wear reading"
    if status == "stale_or_unconfirmed_at_stop":
        return "Stop detected; wear reading did not change"
    return "No tire reading"


def _pace_summary(run: Mapping[str, Any]) -> str:
    pace = _mapping(run.get("pace"))
    early = _number(pace.get("early_average_lap_s"))
    late = _number(pace.get("late_average_lap_s"))
    slope = _number(pace.get("green_lap_time_slope_s_per_lap"))
    parts: list[str] = []
    if early is not None and late is not None:
        parts.append(f"{early:.2f} to {late:.2f} s ({late - early:+.2f})")
    if slope is not None:
        parts.append(f"{slope:+.3f} s/lap")
    return "; ".join(parts) or "Not enough clean green laps"


def _fuel_summary(run: Mapping[str, Any]) -> str:
    fuel = _mapping(run.get("fuel"))
    used = _number(fuel.get("used_gal"))
    end_l = _number(fuel.get("end_l"))
    burn = _number(fuel.get("green_gal_per_lap"))
    parts: list[str] = []
    if used is not None:
        parts.append(f"{used:.2f} gal used")
    if end_l is not None:
        parts.append(f"{end_l / 3.785411784:.2f} gal left")
    if burn is not None:
        parts.append(f"{burn:.3f} gal/green lap")
    return "; ".join(parts) or "Fuel not recorded"


def _position_summary(run: Mapping[str, Any]) -> str:
    position = _mapping(run.get("position"))
    start = _number(position.get("start"))
    end = _number(position.get("end"))
    if start is None and end is None:
        return "Position not recorded"
    if start is not None and end is not None:
        gained = int(start - end)
        delta = f"+{gained}" if gained > 0 else str(gained)
        return f"P{int(start)} to P{int(end)} ({delta})"
    return f"P{int(end if end is not None else start)}"


def _pit_service_summary(run: Mapping[str, Any]) -> str:
    event = _mapping(run.get("pit_service"))
    if not event:
        return "No confirmed service"
    parts = [
        "post-run service"
        if run.get("ended_with_post_run_service")
        else "race pit service"
    ]
    requested = _mapping(event.get("requested_service"))
    requested_tires = [
        tire
        for tire in TIRES
        if requested.get(f"{tire}_tire_change_requested") is True
    ]
    observed_tires = [
        str(tire)
        for tire in _sequence(event.get("tires_changed_observed"))
        if str(tire) in TIRES
    ]
    tire_counters = _mapping(event.get("tire_use_counters"))
    if observed_tires:
        parts.append("confirmed tires " + "/".join(observed_tires))
    if requested_tires:
        parts.append("requested tires " + "/".join(requested_tires))
        unconfirmed = [tire for tire in requested_tires if tire not in observed_tires]
        if unconfirmed and tire_counters:
            parts.append("not confirmed " + "/".join(unconfirmed))
        elif unconfirmed:
            parts.append("tire change not confirmed")
    fuel_added_l = _number(event.get("fuel_added_l"))
    if fuel_added_l is not None and fuel_added_l > 0.01:
        parts.append(f"confirmed fuel {fuel_added_l / 3.785411784:.2f} gal added")
    requested_fuel_l = _number(event.get("requested_fuel_add_l"))
    if requested_fuel_l is not None and requested_fuel_l > 0.01:
        parts.append(f"requested fuel {requested_fuel_l / 3.785411784:.2f} gal")
        if fuel_added_l is None or fuel_added_l <= 0.01:
            parts.append("fuel addition not confirmed")
    return "; ".join(parts)


def _tire_set_summary(run: Mapping[str, Any]) -> str | None:
    lifecycle = _mapping(run.get("tire_set_lifecycle"))
    corners = _mapping(lifecycle.get("corners"))
    if not corners:
        return None
    set_parts: list[str] = []
    distances_mi: list[float] = []
    for tire in TIRES:
        detail = _mapping(corners.get(tire))
        number = _number(detail.get("session_set_number"))
        if number is not None:
            set_parts.append(f"{tire}#{int(number)}")
        distance_m = _number(detail.get("distance_m_on_set"))
        if distance_m is not None:
            distances_mi.append(distance_m / 1609.344)
    pieces = ["/".join(set_parts)] if set_parts else []
    if distances_mi:
        low, high = min(distances_mi), max(distances_mi)
        pieces.append(
            f"{low:.1f} mi on set"
            if abs(high - low) < 0.05
            else f"{low:.1f}–{high:.1f} mi by corner"
        )
    changed = [
        str(tire)
        for tire in _sequence(lifecycle.get("confirmed_changed_at_run_end"))
        if str(tire) in TIRES
    ]
    if changed:
        pieces.append("confirmed changed after run: " + "/".join(changed))
    return f"Run {run.get('run_number', '—')}: " + "; ".join(pieces) if pieces else None


def _summary_range(
    summary: Mapping[str, Any],
    *,
    digits: int = 1,
    suffix: str = "",
) -> tuple[str, str]:
    start = _number(summary.get("start"))
    end = _number(summary.get("end"))
    minimum = _number(summary.get("minimum"))
    maximum = _number(summary.get("maximum"))
    start_end = "Not recorded"
    if start is not None or end is not None:
        start_end = (
            f"{_fmt(start, digits, suffix)} to {_fmt(end, digits, suffix)}"
            if start is not None and end is not None
            else _fmt(end if end is not None else start, digits, suffix)
        )
    observed_range = "Not recorded"
    if minimum is not None or maximum is not None:
        observed_range = (
            f"{_fmt(minimum, digits, suffix)} to {_fmt(maximum, digits, suffix)}"
            if minimum is not None and maximum is not None
            else _fmt(maximum if maximum is not None else minimum, digits, suffix)
        )
        observed_range += "; changed" if summary.get("changed") else "; stable"
    return start_end, observed_range


def _condition_rows(conditions: Mapping[str, Any]) -> list[list[str]]:
    specifications = (
        ("track_temperature_f", "Track temperature", 1, " °F"),
        ("air_temperature_f", "Air temperature", 1, " °F"),
        ("track_wetness_state", "Track wetness state (categorical)", 0, ""),
        ("relative_humidity_percent", "Relative humidity", 1, "%"),
        ("wind_speed_mph", "Wind speed", 1, " mph"),
        ("air_density_kg_m3", "Air density", 4, " kg/m³"),
        ("air_pressure_pa", "Air pressure", 0, " Pa"),
        ("precipitation_percent", "Precipitation", 2, "%"),
    )
    rows: list[list[str]] = []
    for key, label, digits, suffix in specifications:
        summary = _mapping(conditions.get(key))
        if not summary:
            continue
        start_end, observed_range = _summary_range(
            summary, digits=digits, suffix=suffix
        )
        rows.append([label, start_end, observed_range])
    if "weather_declared_wet" in conditions:
        rows.append(
            [
                "Wet-tire declaration",
                "Yes" if conditions.get("weather_declared_wet") else "No",
                "iRacing steward state",
            ]
        )
    if conditions.get("player_tire_compound") is not None:
        rows.append(
            [
                "Recorded tire compound",
                str(conditions.get("player_tire_compound")),
                "iRacing compound identifier",
            ]
        )
    return rows


def _adjustment_rows(adjustments: Mapping[str, Any]) -> list[list[str]]:
    labels = {
        "dcBrakeBias": "In-car brake bias",
        "dpQTape": "Requested pit tape",
        "dpWeightJackerLeft": "Requested left weight jacker",
        "dpWeightJackerRight": "Requested right weight jacker",
        "dpFuelAddKg": "Requested pit fuel",
    }
    rows: list[list[str]] = []
    for channel, label in labels.items():
        summary = _mapping(adjustments.get(channel))
        if not summary:
            continue
        unit = str(summary.get("source_unit") or "").strip()
        suffix = f" {unit}" if unit else ""
        start_end, observed_range = _summary_range(
            summary, digits=2, suffix=suffix
        )
        if channel.startswith("dp"):
            meaning = "Requested only; telemetry does not confirm service completion"
        else:
            meaning = "Measured in-car control setting"
        rows.append([label, start_end, observed_range, meaning])
    return rows


def _run_dynamics_row(run: Mapping[str, Any]) -> list[str] | None:
    dynamics = _mapping(run.get("vehicle_dynamics"))
    if not dynamics:
        return None
    lock = _number(dynamics.get("braking_wheel_lock_proxy_s"))
    front_lock = _number(dynamics.get("front_wheel_lock_proxy_s"))
    spin = _number(dynamics.get("rear_wheelspin_proxy_s"))
    abs_active = _number(dynamics.get("abs_active_s"))
    yaw = _number(dynamics.get("yaw_rate_abs_p95_deg_s_mean"))
    if all(value is None for value in (lock, front_lock, spin, abs_active, yaw)):
        return None
    lock_text = _fmt(lock, 2, " s")
    if front_lock is not None:
        lock_text += f" ({_fmt(front_lock, 2, ' s')} front)"
    return [
        str(run.get("run_number") or "—"),
        lock_text,
        _fmt(spin, 2, " s"),
        _fmt(abs_active, 2, " s"),
        _fmt(yaw, 1, " deg/s"),
    ]


def _tire_cell(observation: Mapping[str, Any], tire: str) -> str:
    details = _mapping(_mapping(observation.get("tires")).get(tire))
    average = _number(details.get("average_remaining_percent"))
    positions = _mapping(details.get("remaining_percent"))
    if average is None and not positions:
        return "—"
    position_values = [
        _fmt(positions.get(position), 1) for position in ("L", "M", "R")
    ]
    if all(value == "—" for value in position_values):
        return f"{_fmt(average, 1, '%')} avg"
    return f"{_fmt(average, 1, '%')} avg ({'/'.join(position_values)})"


def _historical_summary(
    historical_runs: Sequence[Mapping[str, Any]], current_analysis_id: Any,
    current_season_key: str | None = None,
) -> dict[str, Any]:
    rows = [
        row for row in historical_runs
        if isinstance(row, Mapping) and str(row.get("analysis_id")) != str(current_analysis_id)
    ]
    explicitly_affected = [
        row
        for row in rows
        if _mapping(_mapping(row.get("metrics")).get("damage_repair_context")).get(
            "automatic_coaching_reference_eligible"
        ) is False
    ]
    legacy_unscreened = [
        row
        for row in rows
        if "damage_repair_context" not in _mapping(row.get("metrics"))
    ]
    eligible_rows = [row for row in rows if row not in explicitly_affected]
    same_season_rows = [
        row for row in eligible_rows
        if current_season_key and str(row.get("season_key")) == current_season_key
    ]
    baseline_rows = same_season_rows or eligible_rows
    session_ids = {str(row.get("analysis_id")) for row in rows if row.get("analysis_id") is not None}
    green_laps: list[Any] = []
    burns: list[Any] = []
    slopes: list[Any] = []
    tires: list[Any] = []
    for row in baseline_rows:
        metrics = _mapping(row.get("metrics"))
        fuel = _mapping(metrics.get("fuel"))
        tire = _mapping(row.get("tire")) or _mapping(metrics.get("tire_observation"))
        pace = _mapping(metrics.get("pace"))
        green_laps.append(row.get("green_laps", metrics.get("green_laps")))
        burns.append(
            _first_number(
                fuel.get("green_gal_per_lap"),
                row.get("green_gal_per_lap"),
            )
        )
        slopes.append(
            _first_number(
                row.get("lap_time_slope"),
                pace.get("green_lap_time_slope_s_per_lap"),
            )
        )
        tires.append(tire.get("lowest_remaining_percent"))
    return {
        "runs": len(rows),
        "baseline_eligible_runs": len(eligible_rows),
        "damage_screened_out_runs": len(explicitly_affected),
        "legacy_not_damage_screened_runs": len(legacy_unscreened),
        "sessions": len(session_ids),
        "same_season_runs": len(same_season_rows),
        "earlier_season_runs": len(rows) - len(same_season_rows),
        "baseline_scope": "same-season" if same_season_rows else "all-seasons-fallback",
        "median_green_laps": _median(green_laps),
        "median_green_burn_gal": _median(burns),
        "median_slope": _median(slopes),
        "median_lowest_tire": _median(tires),
    }


def _pit_inference(assessment: Mapping[str, Any]) -> str:
    if not assessment.get("was_pit_stop"):
        return (
            "This was a post-run service reading, not a race pit call. It establishes "
            "a consumable baseline but cannot be judged as a pit decision."
        )
    reserve = _number(assessment.get("fuel_laps_remaining_at_end"))
    tire = _number(assessment.get("lowest_tire_remaining_percent"))
    under_caution = bool(assessment.get("ended_under_caution"))
    if reserve is None or tire is None:
        return "Consumable evidence is incomplete, so this stop cannot be rated as optimal or late."
    if reserve <= 1.5 or tire <= 35.0:
        judgement = "The stop was close to a measured fuel or tire limit."
    elif reserve >= 4.0 and tire >= 65.0:
        judgement = "Fuel and measured tread alone did not force this stop."
    else:
        judgement = "The stop was inside a plausible consumable window."
    if under_caution:
        judgement += " The caution may still have made the timing strategically attractive."
    else:
        judgement += " Track position, pit loss, and the next caution remain decisive."
    return judgement


def _next_race_baseline(
    analysis: Mapping[str, Any], history: Mapping[str, Any]
) -> str | None:
    strategy = _mapping(analysis.get("strategy"))
    race = _mapping(analysis.get("race_summary"))
    runs = [_mapping(item) for item in _sequence(analysis.get("runs")) if isinstance(item, Mapping)]
    current_burn = _number(strategy.get("measured_green_fuel_gal_per_lap"))
    historical_burn = _number(history.get("median_green_burn_gal"))
    burn_candidates = [value for value in (current_burn, historical_burn) if value not in (None, 0.0)]
    if not burn_candidates:
        return None
    # Use the more conservative observed rate, then add a small operational buffer.
    planning_burn = max(burn_candidates) * 1.03
    start_gallons = [
        value / 3.785411784
        for run in runs
        if (value := _number(_mapping(run.get("fuel")).get("start_l"))) is not None
    ]
    tank_range = max(start_gallons) / planning_burn if start_gallons and planning_burn > 0 else None
    historical_stint = _number(history.get("median_green_laps"))
    current_stint = _median([run.get("green_laps") for run in runs])
    proven_stint = historical_stint if historical_stint is not None else current_stint
    parts = [f"budget {planning_burn:.3f} gal per green lap (observed rate plus 3% reserve)"]
    if tank_range is not None:
        parts.append(f"treat about {max(0.0, tank_range):.1f} all-green laps as the fuel ceiling")
        scheduled = _number(race.get("scheduled_laps"))
        if scheduled is not None and tank_range > 0:
            minimum_stops = max(0, math.ceil(scheduled / tank_range - 1e-9) - 1)
            if minimum_stops == 0:
                parts.append(
                    f"fuel alone can cover the scheduled {scheduled:.0f} all-green laps without a stop"
                )
            else:
                parts.append(f"that implies at least {minimum_stops} fuel stop{'s' if minimum_stops != 1 else ''} over {scheduled:.0f} all-green laps")
    if proven_stint is not None:
        parts.append(f"use {proven_stint:.1f} green laps as the proven stint reference")
    return "; ".join(parts) + ". Recalculate after cautions and stage constraints."


def _find_knowledge_values(knowledge: Mapping[str, Any]) -> tuple[list[str], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    containers = [knowledge]
    for key in ("facts", "knowledge", "garage61"):
        child = _mapping(knowledge.get(key))
        if child:
            containers.append(child)
    notes: list[str] = []
    targets: list[Mapping[str, Any]] = []
    references: list[Mapping[str, Any]] = []
    for container in containers:
        for key in ("summary", "notes", "track_notes", "car_notes"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                compact = " ".join(value.split())
                notes.append(compact[:280] + ("…" if len(compact) > 280 else ""))
        for key in ("corner_targets", "coaching_targets", "targets"):
            targets.extend(item for item in _sequence(container.get(key)) if isinstance(item, Mapping))
        for key in ("representative_laps", "reference_laps", "selected_laps", "laps"):
            references.extend(item for item in _sequence(container.get(key)) if isinstance(item, Mapping))
    return notes[:4], targets[:8], references[:3]


def _knowledge_target_line(target: Mapping[str, Any]) -> str:
    name = target.get("name") or target.get("corner") or target.get("label") or "Reference zone"
    details: list[str] = []
    start = _first_number(target.get("start_pct"), target.get("lap_pct_start"))
    end = _first_number(target.get("end_pct"), target.get("lap_pct_end"))
    if start is not None and end is not None:
        if abs(start) <= 1.5 and abs(end) <= 1.5:
            start, end = start * 100.0, end * 100.0
        details.append(f"{start:.0f}–{end:.0f}% lap distance")
    entry = _first_number(target.get("entry_speed_mph"), target.get("entry_mph"))
    minimum = _first_number(target.get("minimum_speed_mph"), target.get("min_speed_mph"))
    if entry is not None:
        details.append(f"{entry:.1f} mph entry")
    if minimum is not None:
        details.append(f"{minimum:.1f} mph minimum")
    entry_delta = _number(target.get("entry_speed_delta_mph"))
    minimum_delta = _number(target.get("minimum_speed_delta_mph"))
    brake_delta = _number(target.get("peak_brake_delta"))
    steering_delta = _number(target.get("steering_delta_rad"))
    throttle_delta = _number(target.get("throttle_pickup_delta_lap_pct"))
    onset_delta = _number(target.get("brake_onset_delta_lap_pct"))
    release_delta = _number(target.get("brake_release_delta_lap_pct"))
    if entry_delta is not None:
        details.append(f"entry {entry_delta:+.1f} mph local-reference")
    if minimum_delta is not None:
        details.append(f"minimum {minimum_delta:+.1f} mph local-reference")
    if brake_delta is not None:
        details.append(f"peak brake {brake_delta * 100:+.1f} points")
    if steering_delta is not None:
        details.append(f"steering {steering_delta:+.3f} rad")
    if throttle_delta is not None:
        timing = "later" if throttle_delta > 0 else "earlier" if throttle_delta < 0 else "matched"
        details.append(
            f"75% throttle {abs(throttle_delta) * 100:.1f}% lap {timing}"
            if timing != "matched"
            else "75% throttle pickup matched"
        )
    if onset_delta is not None:
        timing = "later" if onset_delta > 0 else "earlier" if onset_delta < 0 else "matched"
        details.append(
            f"brake onset {abs(onset_delta) * 100:.1f}% lap {timing}"
            if timing != "matched"
            else "brake onset matched"
        )
    if release_delta is not None:
        timing = "later" if release_delta > 0 else "earlier" if release_delta < 0 else "matched"
        details.append(
            f"brake release {abs(release_delta) * 100:.1f}% lap {timing}"
            if timing != "matched"
            else "brake release matched"
        )
    instruction = target.get("coaching") or target.get("instruction") or target.get("brake_note")
    if instruction:
        details.append(str(instruction).strip())
    return f"**{name}:** " + ("; ".join(details) if details else "Use the cached reference trace for the exact target.")


def _reference_line(reference: Mapping[str, Any]) -> str:
    raw = _mapping(reference.get("lap")) or reference
    lap_time = _first_number(raw.get("lap_time_s"), raw.get("lapTime"), raw.get("lap_time"))
    setup = raw.get("setup_type") or raw.get("_comparisonSetupType") or raw.get("setup")
    driver = raw.get("driver_name") or raw.get("driver") or raw.get("userName")
    parts = []
    if lap_time is not None:
        minutes = int(lap_time // 60)
        seconds = lap_time - minutes * 60
        parts.append(f"{minutes}:{seconds:06.3f}" if minutes else f"{seconds:.3f} s")
    if setup:
        parts.append(str(setup))
    if driver:
        parts.append(str(driver))
    return ", ".join(parts) or "Comparable Garage61 reference selected"


def _load_zone_action(segment: Mapping[str, Any]) -> str:
    brake = _number(segment.get("median_brake")) or 0.0
    steering = _number(segment.get("median_steering_rad")) or 0.0
    lateral = _number(segment.get("median_lateral_g")) or 0.0
    if brake >= 0.12:
        return "Complete the main brake release before peak steering; avoid carrying extra pedal into the loaded phase."
    if steering >= 0.18 or lateral >= 0.75:
        return "Use one clean steering arc and unwind before adding throttle; corrections add tire load without speed."
    return "Prioritize a stable entry and an early, smooth throttle pickup."


def _corner_phase_zone_label(
    zone: Mapping[str, Any], knowledge: Mapping[str, Any]
) -> tuple[str, str]:
    """Use an official corner name only when knowledge marks it as sourced."""

    fallback = str(zone.get("zone_label") or zone.get("zone_id") or "Load zone")
    _, targets, _ = _find_knowledge_values(knowledge) if knowledge else ([], [], [])
    accepted = {
        "official",
        "sourced",
        "sourced_official",
        "official_sourced",
        "sourced_track_map",
        "official_corner_name",
    }
    zone_start = _number(zone.get("start_pct"))
    zone_end = _number(zone.get("end_pct"))
    zone_number = str(zone.get("zone_id") or "").rsplit("-", 1)[-1]
    for target in targets:
        quality = _mapping(target.get("quality"))
        name_status = str(
            target.get("corner_name_status")
            or quality.get("corner_name_status")
            or ""
        ).strip().lower()
        if name_status not in accepted:
            continue
        target_name = target.get("corner") or target.get("official_name") or target.get("name")
        if not target_name or str(target_name).lower().startswith("load zone"):
            continue
        target_segment = target.get("segment") or target.get("zone")
        segment_matches = target_segment is not None and str(target_segment) == zone_number
        target_start = _first_number(target.get("start_pct"), target.get("lap_pct_start"))
        target_end = _first_number(target.get("end_pct"), target.get("lap_pct_end"))
        distance_matches = (
            zone_start is not None
            and zone_end is not None
            and target_start is not None
            and target_end is not None
            and abs(zone_start - target_start) <= 0.01
            and abs(zone_end - target_end) <= 0.01
        )
        if segment_matches or distance_matches:
            return str(target_name), "sourced official corner name"
    return f"{fallback} (provisional)", "telemetry-derived provisional load zone"


def _corner_phase_age_text(phase: Mapping[str, Any]) -> str:
    by_tire = _mapping(_mapping(phase.get("green_lap_on_set_bounds")).get("by_tire"))
    values: list[tuple[str, float, float]] = []
    for tire in TIRES:
        details = _mapping(by_tire.get(tire))
        start = _number(details.get("start"))
        end = _number(details.get("end"))
        if start is not None and end is not None:
            values.append((tire, start, end))
    if not values:
        return "phase not observed"
    if len(values) == len(TIRES) and len({(round(start, 2), round(end, 2)) for _, start, end in values}) == 1:
        _, start, end = values[0]
        return f"{start:.1f}-{end:.1f} green laps/set (all tires)"
    return "; ".join(f"{tire} {start:.1f}-{end:.1f}" for tire, start, end in values)


def _corner_phase_timing(
    metrics: Mapping[str, Any], availability: Mapping[str, Any]
) -> str:
    turn_in = _number(metrics.get("turn_in_lap_pct"))
    onset = _number(metrics.get("brake_onset_lap_pct"))
    release = _number(metrics.get("brake_release_lap_pct"))
    pickup = _number(metrics.get("throttle_pickup_lap_pct"))
    if turn_in is None and (_number(availability.get("turn_in_boundary_censored_laps")) or 0) > 0:
        turn_text = "turn-in boundary-censored"
    else:
        turn_text = f"turn {_fmt(turn_in * 100.0 if turn_in is not None else None, 1, '% lap')}"
    if onset is None and (_number(availability.get("brake_onset_boundary_censored_laps")) or 0) > 0:
        brake_text = "brake onset boundary-censored"
    else:
        brake_text = f"brake {_fmt(onset * 100.0 if onset is not None else None, 1, '%')}"
    brake_text += f"->{_fmt(release * 100.0 if release is not None else None, 1, '% lap')}"
    if pickup is None and (_number(availability.get("throttle_pickup_boundary_censored_laps")) or 0) > 0:
        pickup_text = "75% throttle pickup boundary-censored"
    else:
        pickup_text = f"75% throttle {_fmt(pickup * 100.0 if pickup is not None else None, 1, '% lap')}"
    return "; ".join((turn_text, brake_text, pickup_text))


def render_report(
    analysis: Mapping[str, Any],
    historical_runs: Sequence[Mapping[str, Any]] | None = None,
    knowledge: Mapping[str, Any] | None = None,
) -> str:
    """Render a concise evidence-first Markdown coaching report."""

    if not isinstance(analysis, Mapping):
        raise TypeError("analysis must be a mapping")
    identity = _mapping(analysis.get("identity"))
    race = _mapping(analysis.get("race_summary"))
    strategy = _mapping(analysis.get("strategy"))
    quality = _mapping(analysis.get("data_quality"))
    source = _mapping(analysis.get("source"))
    related_local_files = _mapping(source.get("related_local_files"))
    runs = [_mapping(item) for item in _sequence(analysis.get("runs")) if isinstance(item, Mapping)]
    signals = [_mapping(item) for item in _sequence(analysis.get("coaching_signals")) if isinstance(item, Mapping)]
    history_rows = [item for item in _sequence(historical_runs) if isinstance(item, Mapping)]
    if identity.get("season_year") and identity.get("season_quarter"):
        current_season_key = f"{identity['season_year']}s{identity['season_quarter']}".lower()
    elif identity.get("season_id") not in (None, "", 0, "0"):
        current_season_key = f"season-{identity['season_id']}".lower()
    else:
        current_season_key = None
    history = _historical_summary(
        history_rows, analysis.get("analysis_id"), current_season_key
    )
    title, setup_type = _identity_title(identity)
    lines = [f"# Post-race coaching — {title}", ""]

    session_bits = [setup_type]
    if identity.get("subsession_id") is not None:
        session_bits.append(f"subsession {identity['subsession_id']}")
    if identity.get("season_year") and identity.get("season_quarter"):
        session_bits.append(f"{identity['season_year']} S{identity['season_quarter']}")
    lines.extend([" · ".join(session_bits), ""])

    lines.extend(["## Coaching priorities", ""])
    priority_order = {"high": 0, "medium": 1, "low": 2}
    ordered_signals = sorted(
        enumerate(signals),
        key=lambda pair: (priority_order.get(str(pair[1].get("priority", "medium")).lower(), 1), pair[0]),
    )
    if ordered_signals:
        for position, (_, signal) in enumerate(ordered_signals[:3], 1):
            run_label = f"Run {signal.get('run_number')}: " if signal.get("run_number") is not None else ""
            action = str(signal.get("coaching") or "Review the measured trace before changing technique.").strip()
            finding = str(signal.get("finding") or "No quantified finding supplied.").strip()
            evidence = [str(item).strip() for item in _sequence(signal.get("evidence")) if str(item).strip()]
            measured = "; ".join(
                item.rstrip(" .;") for item in [finding, *evidence] if item.rstrip(" .;")
            )
            inference = signal.get("inference")
            lines.append(f"{position}. **{run_label}{action}**")
            lines.append(f"   - Measured: {measured}.")
            lines.append(
                f"   - Inference: {inference}"
                if inference
                else "   - Inference: No causal claim is made beyond the measurements above."
            )
    else:
        lines.append("- No coaching signals were generated; inspect data quality before changing driving technique.")
    lines.append("")

    lines.extend(["## Race summary", ""])
    summary_rows: list[list[str]] = []
    recorded = _lap_count(race.get("recorded_laps"))
    scheduled = _lap_count(race.get("scheduled_laps"))
    distance = f"{recorded} recorded laps"
    if scheduled != "—":
        distance += f" / {scheduled} scheduled"
    summary_rows.append(["Distance", distance])
    official_cautions = _number(race.get("official_cautions"))
    official_caution_laps = _number(race.get("official_caution_laps"))
    caution_reconciliation = _mapping(race.get("caution_reconciliation"))
    telemetry_caution_laps = _number(
        caution_reconciliation.get("telemetry_estimated_laps")
        if caution_reconciliation
        else race.get("caution_laps_estimated")
    )
    if official_cautions is not None or official_caution_laps is not None:
        flags = f"{_fmt(official_cautions, 0)} cautions, {_lap_count(official_caution_laps)} caution laps (official)"
        if caution_reconciliation.get("status") == "mismatch":
            flags += (
                f"; {_lap_count(telemetry_caution_laps)} caution laps from sampled flags "
                "(mismatch—review race-control timeline)"
            )
    else:
        flags = (
            f"{_lap_count(race.get('green_laps_estimated'))} green / "
            f"{_lap_count(race.get('caution_laps_estimated'))} caution laps (telemetry estimate)"
        )
    summary_rows.append(["Flags", flags])
    summary_rows.append([
        "Runs / stops",
        f"{_fmt(race.get('runs_detected'), 0)} runs / {_fmt(race.get('pit_stops_detected'), 0)} detected stops",
    ])
    total_fuel_gal = _number(race.get("fuel_used_gal"))
    total_fuel_l = _number(race.get("fuel_used_l"))
    if total_fuel_gal is not None:
        fuel_text = f"{total_fuel_gal:.2f} gal"
        if total_fuel_l is not None:
            fuel_text += f" ({total_fuel_l:.1f} L)"
        summary_rows.append(["Fuel consumed", fuel_text])
    summary_rows.append(["Data confidence", str(quality.get("confidence") or "unknown")])
    start_position = _number(race.get("starting_position"))
    final_position = _number(race.get("final_recorded_position"))
    if start_position is not None or final_position is not None:
        if start_position is not None and final_position is not None:
            position_text = f"P{int(start_position)} to P{int(final_position)}"
        else:
            position_text = f"P{int(final_position if final_position is not None else start_position)}"
        summary_rows.append(["Recorded position", position_text])
    lines.extend(_table(("Metric", "Result"), summary_rows))
    lines.append("")

    timeline_rows = _timeline_rows(_mapping(analysis.get("race_timeline")))
    if timeline_rows:
        lines.extend(["## Race-control and service timeline", ""])
        lines.extend(_table(("Session time", "Lap(s)", "Event", "Evidence"), timeline_rows))
        lines.append("")
        lines.append(
            "Timeline caution periods are derived from sampled flags. Requested pit work remains separate from service confirmed by fuel, tire counters, or odometer resets."
        )
        lines.append("")

    damage = _mapping(analysis.get("damage_repair"))
    damage_rows = _damage_repair_rows(damage)
    damage_summary = _mapping(damage.get("summary"))
    material_damage_context = any(
        (_number(damage_summary.get(key)) or 0.0) > 0.0
        for key in (
            "tow_episodes",
            "recorded_repair_episodes",
            "repair_required_flag_episodes",
            "confirmed_fast_repair_uses",
        )
    )
    if damage_rows and material_damage_context:
        lines.extend(["## Damage, tow, and repair context", ""])
        incident = _mapping(damage.get("incident_points"))
        lines.append(
            "- Measured context: "
            f"{_fmt(incident.get('positive_delta'), 0)} incident points added; "
            f"{_fmt(damage_summary.get('tow_episodes'), 0)} tow episode(s); "
            f"{_fmt(damage_summary.get('recorded_repair_episodes'), 0)} recorded repair episode(s)."
        )
        lines.extend(
            _table(
                (
                    "Session/lap",
                    "Context",
                    "Pit / stall / service",
                    "Tow",
                    "Mandatory timer",
                    "Optional timer",
                    "Run/reference impact",
                ),
                damage_rows,
            )
        )
        lines.append("")
        lines.append(
            "Derived timing rule: repair countdown consumed is calculated from recorded timer decreases. Pit-road, stall, service, tow, and repair intervals can overlap and are not additive, so this report does not isolate repair-only time loss."
        )
        lines.append(
            "Evidence limit: incident points do not prove physical damage, and repair/tow state does not identify the component or quantify its exact pace cost. A zero timer does not certify an identical-to-undamaged car."
        )
        lines.append("")

    condition_rows = _condition_rows(_mapping(analysis.get("conditions")))
    if condition_rows:
        lines.extend(["## Recorded conditions", ""])
        lines.extend(_table(("Condition", "Start to end", "Session range"), condition_rows))
        lines.append("")
        lines.append(
            "Measured condition traces establish comparability; they do not by themselves explain a pace or handling change."
        )
        lines.append("")

    setup_rows: list[list[str]] = []
    if identity.get("is_fixed_setup") is not None:
        setup_rows.append(["Session type", setup_type])
    if identity.get("setup_name"):
        setup_rows.append(["Setup name", str(identity.get("setup_name"))])
    if identity.get("setup_parameter_count") is not None:
        setup_rows.append(["Embedded parameters", str(identity.get("setup_parameter_count"))])
    if identity.get("setup_fingerprint"):
        setup_rows.append(["Fingerprint", str(identity.get("setup_fingerprint"))])
    saved_setup_matches = [
        _mapping(item)
        for item in _sequence(related_local_files.get("saved_setup_matches"))
        if isinstance(item, Mapping)
    ]
    if saved_setup_matches:
        setup_rows.append(["Saved .sto match", str(saved_setup_matches[0].get("path"))])
    if setup_rows:
        lines.extend(["## Setup context", ""])
        lines.extend(_table(("Setup field", "Recorded value"), setup_rows))
        lines.append("")
        if identity.get("is_fixed_setup") is True:
            lines.append("The embedded fixed setup is archived for traceability; driving comparisons remain in the fixed-setup cohort.")
        elif identity.get("is_fixed_setup") is False:
            lines.append("The complete embedded open setup is preserved in analysis.json. Attribute a delta to driving only after checking the comparison setup and conditions.")
        lines.append("")

    adjustment_rows = _adjustment_rows(
        _mapping(analysis.get("driver_adjustments"))
    )
    if adjustment_rows:
        lines.extend(["## Driver and requested pit adjustments", ""])
        lines.extend(
            _table(
                ("Control", "Start to end", "Session range", "Evidence meaning"),
                adjustment_rows,
            )
        )
        lines.append("")
        lines.append(
            "Measured fact: dp* channels record pit requests only. They are not confirmation that the crew completed an adjustment."
        )
        lines.append("")

    setup_telemetry = _mapping(analysis.get("setup_telemetry"))
    if identity.get("is_fixed_setup") is False and setup_telemetry:
        lines.extend(["## Open-setup telemetry evidence", ""])
        platform = _mapping(setup_telemetry.get("platform"))
        platform_rows: list[list[str]] = []
        splitter = _mapping(platform.get("center_front_splitter"))
        if splitter:
            platform_rows.append([
                "Center-front splitter",
                _fmt(splitter.get("min_in"), 3, " in"),
                _fmt(splitter.get("p05_in"), 3, " in"),
                _fmt(splitter.get("median_in"), 3, " in"),
                _fmt(splitter.get("max_in"), 3, " in"),
            ])
        dynamic_rear = _mapping(platform.get("dynamic_rear"))
        if dynamic_rear:
            platform_rows.append([
                "Dynamic rear (LR/RR mean)",
                _fmt(dynamic_rear.get("min_in"), 3, " in"),
                _fmt(dynamic_rear.get("p05_in"), 3, " in"),
                _fmt(dynamic_rear.get("median_in"), 3, " in"),
                _fmt(dynamic_rear.get("max_in"), 3, " in"),
            ])
        if platform_rows:
            lines.extend(_table(("Measured platform", "Minimum", "P05", "Median", "Maximum"), platform_rows))
            lines.append("")

        shock_rows: list[list[str]] = []
        for corner in TIRES:
            shock = _mapping(_mapping(setup_telemetry.get("shocks")).get(corner))
            if not shock:
                continue
            shock_rows.append([
                corner,
                _fmt(shock.get("deflection_range_in"), 3, " in"),
                _fmt(shock.get("abs_velocity_p90_in_s"), 2, " in/s"),
                _fmt(shock.get("abs_velocity_p99_in_s"), 2, " in/s"),
            ])
        if shock_rows:
            lines.extend(_table(("Corner", "Deflection range", "Abs velocity P90", "Abs velocity P99"), shock_rows))
            lines.append("")

        tire_rows_live: list[list[str]] = []
        for corner in TIRES:
            tire = _mapping(_mapping(setup_telemetry.get("tires")).get(corner))
            if not tire:
                continue
            live = _mapping(tire.get("live_pressure"))
            carcass = _mapping(tire.get("carcass_average"))
            tire_rows_live.append([
                corner,
                _fmt(live.get("median_psi"), 1, " psi"),
                _fmt(live.get("max_psi"), 1, " psi"),
                _fmt(carcass.get("median_f"), 1, " °F"),
            ])
        if tire_rows_live:
            lines.extend(_table(("Tire", "Median live pressure", "Maximum live pressure", "Median carcass"), tire_rows_live))
            lines.append("")
        lines.append(
            "Measured setup traces can locate platform, damper, pressure, and temperature behavior. They do not uniquely prove which garage parameter caused a handling symptom; provide entry/center/exit feedback and use a one-change controlled A/B test before keeping an adjustment."
        )
        lines.append("")

    lines.extend(["## Runs", ""])
    run_rows: list[list[str]] = []
    for run in runs:
        start_lap, end_lap = run.get("start_lap"), run.get("end_lap")
        lap_range = str(start_lap) if start_lap == end_lap else f"{start_lap}–{end_lap}"
        run_rows.append([
            str(run.get("run_number") or "—"),
            f"{lap_range} ({_lap_count(run.get('total_laps'))})",
            f"{_lap_count(run.get('green_laps'))} / {_lap_count(run.get('caution_laps'))}",
            _pace_summary(run),
            _fuel_summary(run),
            _position_summary(run),
            _pit_service_summary(run),
            _run_tire_summary(run),
        ])
    if run_rows:
        lines.extend(_table(("Run", "Laps", "Green / caution", "Green pace", "Fuel", "Position", "Service", "Tire evidence"), run_rows))
    else:
        lines.append("No complete run boundaries were detected.")
    lines.append("")

    dynamics_rows = [
        row for run in runs if (row := _run_dynamics_row(run)) is not None
    ]
    if dynamics_rows:
        lines.extend(["## Vehicle dynamics diagnostics", ""])
        lines.extend(
            _table(
                (
                    "Run",
                    "Wheel-lock proxy",
                    "Rear-spin proxy",
                    "ABS active",
                    "Yaw-rate P95",
                ),
                dynamics_rows,
            )
        )
        lines.append("")
        lines.append(
            "Measured diagnostic durations are screening evidence, not proof of tire slip or a setup fault. Tire stagger, corner radius, banking, bumps, and sensor behavior can contribute to wheel-speed divergence."
        )
        lines.append("")

    tire_rows: list[list[str]] = []
    for run in runs:
        observation = _mapping(run.get("tire_observation"))
        if observation:
            lowest = observation.get("lowest_remaining_tire")
            lowest_pct = _number(observation.get("lowest_remaining_percent"))
            lowest_text = (
                f"{lowest} {_fmt(lowest_pct, 1, '%')}"
                if lowest and lowest_pct is not None else "—"
            )
            tire_rows.append([
                str(run.get("run_number") or "—"),
                *[_tire_cell(observation, tire) for tire in TIRES],
                lowest_text,
            ])
    tire_set_lines = [
        summary for run in runs if (summary := _tire_set_summary(run)) is not None
    ]
    if tire_rows or tire_set_lines:
        lines.extend(["## Tire wear", ""])
        if tire_rows:
            lines.extend(_table(("Run", "LF avg (L/M/R)", "RF avg (L/M/R)", "LR avg (L/M/R)", "RR avg (L/M/R)", "Lowest"), tire_rows))
            lines.append("")
            lines.append("Measured fact: values are percent tread remaining from a discrete pit-service reading and are assigned to the run that just ended.")
        if tire_set_lines:
            lines.append("Session-local tire-set lifecycle: " + " | ".join(tire_set_lines) + ".")
        lines.append("")

    corner_tire_age = _mapping(analysis.get("corner_tire_age"))
    corner_runs = [
        _mapping(item)
        for item in _sequence(corner_tire_age.get("runs"))
        if isinstance(item, Mapping)
    ]
    if any(_sequence(item.get("zones")) for item in corner_runs):
        lines.extend(["## Corner behavior by tire-age phase", ""])
        lines.append(
            "[derived] Rows contain medians of complete clean green laps only. "
            "Every run is split into explicitly observational early/middle/late thirds. "
            "When an all-corner zero-age boundary is confirmed, each row retains exact per-tire green-lap-on-set bounds."
        )
        lines.append(
            "[proxy] A confirmed-age late row is an older-set/late-run proxy, never a measured worn-tread state."
        )
        lines.append("")
        phase_rows: list[list[str]] = []
        coaching_rows: list[str] = []
        knowledge_map_for_zones = _mapping(knowledge)
        for run_item in corner_runs:
            phase_model = str(run_item.get("phase_model") or "")
            if phase_model == "confirmed_age_run_thirds_proxy":
                new_set_text = (
                    "the first eligible phase starts on a confirmed new set"
                    if run_item.get("new_set_confirmed")
                    else "the tire-set age is confirmed, but the first eligible phase does not start near zero age"
                )
                lines.append(
                    f"[derived] Run {run_item.get('run_number', '-')} has confirmed green-lap-on-set bounds; "
                    f"{new_set_text}. Its late row remains an older-set/late-run proxy."
                )
            phase_semantics = _mapping(run_item.get("observational_phase_semantics"))
            for raw_zone in _sequence(run_item.get("zones")):
                if not isinstance(raw_zone, Mapping):
                    continue
                zone = _mapping(raw_zone)
                zone_label, _ = _corner_phase_zone_label(
                    zone, knowledge_map_for_zones
                )
                raw_phases = zone.get("observational_run_phases")
                for raw_phase in _sequence(raw_phases):
                    if not isinstance(raw_phase, Mapping):
                        continue
                    phase = _mapping(raw_phase)
                    metrics = _mapping(phase.get("metrics"))
                    availability = _mapping(phase.get("event_availability"))
                    phase_label = str(phase.get("phase") or "").capitalize()
                    semantic = phase_semantics.get(str(phase.get("phase") or ""))
                    phase_label += f" ({semantic})" if semantic else " (run-phase proxy)"
                    status = str(phase.get("status") or "unavailable")
                    if status == "unavailable":
                        continue
                    brake_average = _number(metrics.get("brake_average_fraction"))
                    brake_peak = _number(metrics.get("brake_peak_fraction"))
                    exit_throttle = _number(metrics.get("exit_throttle_fraction"))
                    phase_rows.append(
                        [
                            str(run_item.get("run_number") or "-"),
                            zone_label,
                            phase_label,
                            f"{phase.get('lap_count', 0)} laps / {phase.get('sample_count', 0)} samples",
                            _corner_phase_age_text(phase),
                            (
                                f"{_fmt(metrics.get('entry_speed_mph'), 1)}/"
                                f"{_fmt(metrics.get('minimum_speed_mph'), 1)}/"
                                f"{_fmt(metrics.get('exit_speed_mph'), 1)} mph"
                            ),
                            (
                                f"{_fmt(brake_average * 100.0 if brake_average is not None else None, 0)}/"
                                f"{_fmt(brake_peak * 100.0 if brake_peak is not None else None, 0)}%; "
                                f"energy {_fmt(metrics.get('brake_energy_proxy'), 1)}"
                            ),
                            _corner_phase_timing(metrics, availability),
                            (
                                f"steer {_fmt(metrics.get('steering_average_abs_rad'), 3, ' rad')}; "
                                f"work {_fmt(metrics.get('steering_work_proxy'), 1)}; "
                                f"corrections {_fmt(metrics.get('steering_corrections'), 1)}; "
                                f"exit throttle {_fmt(exit_throttle * 100.0 if exit_throttle is not None else None, 0, '%')}"
                            ),
                        ]
                    )
                coaching = _mapping(zone.get("coaching"))
                if coaching.get("finding") or coaching.get("action"):
                    coaching_rows.append(
                        f"- [inferred] **Run {run_item.get('run_number', '-')}, {zone_label}:** "
                        f"{coaching.get('finding', '')} {coaching.get('action', '')}"
                    )
        if phase_rows:
            lines.extend(
                _table(
                    (
                        "Run",
                        "Zone",
                        "Phase",
                        "Evidence",
                        "Green-lap age on set",
                        "Entry/min/exit",
                        "Brake avg/peak",
                        "Turn/brake/throttle timing",
                        "Steering / exit",
                    ),
                    phase_rows,
                )
            )
            lines.append("")
        lines.extend(coaching_rows[:12])
        if coaching_rows:
            lines.append("")

    fuel_section_start = len(lines)
    lines.extend(["## Fuel and pit strategy", ""])
    green_burn = _number(strategy.get("measured_green_fuel_gal_per_lap"))
    caution_burn = _number(strategy.get("measured_caution_fuel_gal_per_lap"))
    burn_parts = []
    if green_burn is not None:
        burn_parts.append(f"{green_burn:.3f} gal/green lap")
    if caution_burn is not None:
        burn_parts.append(f"{caution_burn:.3f} gal/caution lap")
    if burn_parts:
        lines.append("Measured: " + "; ".join(burn_parts))
    assessments = [_mapping(item) for item in _sequence(strategy.get("pit_assessments")) if isinstance(item, Mapping)]
    for assessment in assessments:
        facts: list[str] = []
        reserve = _number(assessment.get("fuel_laps_remaining_at_end"))
        tire = _number(assessment.get("lowest_tire_remaining_percent"))
        if reserve is not None:
            facts.append(f"{reserve:.1f} green-lap equivalents of fuel remained")
        if tire is not None:
            facts.append(f"lowest measured tire was {tire:.1f}%")
        position_at_end = _number(assessment.get("position_at_end"))
        if position_at_end is not None:
            facts.append(f"recorded position was P{int(position_at_end)} at run end")
        if assessment.get("was_post_run_service"):
            facts.append("post-run service supplied the tire reading")
        else:
            facts.append("ended under caution" if assessment.get("ended_under_caution") else "ended under green or an unclassified flag state")
        run_number = assessment.get("run_number", "—")
        lines.append(f"- **Run {run_number} measured:** " + "; ".join(facts) + ".")
        lines.append(f"  **Inference:** {_pit_inference(assessment)}")

    if history.get("runs"):
        history_parts = [
            f"{history['runs']} prior comparable runs across {history['sessions']} sessions",
            f"{history['same_season_runs']} current-season and {history['earlier_season_runs']} earlier-season runs",
        ]
        if history.get("median_green_laps") is not None:
            history_parts.append(f"median {history['median_green_laps']:.1f} green laps/run")
        if history.get("median_green_burn_gal") is not None:
            history_parts.append(f"median {history['median_green_burn_gal']:.3f} gal/green lap")
        if history.get("median_lowest_tire") is not None:
            history_parts.append(f"median lowest tire {history['median_lowest_tire']:.1f}%")
        if history.get("median_slope") is not None:
            history_parts.append(f"median pace trend {history['median_slope']:+.3f} s/lap")
        history_parts.append(
            "medians use current-season runs"
            if history.get("baseline_scope") == "same-season"
            else "medians use all seasons because no current-season prior run exists"
        )
        lines.append("- **Historical measured baseline:** " + "; ".join(history_parts) + ".")
    else:
        lines.append("- **Historical measured baseline:** no prior same-key runs were supplied; use this race as the initial baseline.")
    if baseline := _next_race_baseline(analysis, history):
        lines.append(f"- **Next-race strategy inference:** {baseline}")
    forecast = _mapping(strategy.get("forecast"))
    if forecast.get("status") == "usable":
        all_green_range = _number(forecast.get("all_green_range_laps"))
        mixed_range = _number(forecast.get("observed_mix_range_laps"))
        all_green_stops = _number(forecast.get("minimum_stops_all_green"))
        mixed_stops = _number(forecast.get("minimum_stops_at_observed_mix"))
        targets = [
            _number(item)
            for item in _sequence(forecast.get("equal_stint_pit_targets_all_green"))
        ]
        targets = [item for item in targets if item is not None]
        forecast_parts = []
        if all_green_range is not None:
            forecast_parts.append(f"{all_green_range:.1f}-lap all-green range")
        if all_green_stops is not None:
            forecast_parts.append(f"at least {int(all_green_stops)} all-green fuel stops")
        if mixed_range is not None:
            forecast_parts.append(f"{mixed_range:.1f}-lap range at this race's sampled caution mix")
        if mixed_stops is not None:
            forecast_parts.append(f"{int(mixed_stops)} fuel stops at that same mix")
        if targets:
            forecast_parts.append(
                "all-green equal-stint targets near laps "
                + "/".join(f"{target:.0f}" for target in targets)
            )
        lines.append(
            "- **Fuel-feasibility forecast:** "
            + "; ".join(forecast_parts)
            + ". This is not an optimal-pit-call claim; live cautions, stages, pit loss, tires, and position still control the decision."
        )
    lines.append("")
    if not (burn_parts or assessments or history.get("runs") or baseline or forecast.get("status") == "usable"):
        del lines[fuel_section_start:]

    track_profile = _mapping(analysis.get("track_profile"))
    segments = [_mapping(item) for item in _sequence(track_profile.get("detected_corner_segments")) if isinstance(item, Mapping)]
    if segments:
        lines.extend(["## Track load-zone targets", ""])
        lines.append("These are telemetry-derived load zones, not official corner names. Actions below are coaching inferences.")
        lines.append("")
        zone_rows: list[list[str]] = []
        for segment in segments[:8]:
            start = _number(segment.get("start_pct"))
            end = _number(segment.get("end_pct"))
            median_brake = _number(segment.get("median_brake"))
            distance = "—"
            if start is not None and end is not None:
                distance = f"{start * 100:.0f}–{end * 100:.0f}%"
                if segment.get("wraps_start_finish"):
                    distance += " (wraps S/F)"
            zone_rows.append([
                str(segment.get("segment") or "—"),
                distance,
                _fmt(segment.get("minimum_speed_mph"), 1, " mph"),
                _fmt(median_brake * 100.0 if median_brake is not None else None, 0, "%"),
                _fmt(segment.get("median_lateral_g"), 2, " g"),
                _load_zone_action(segment),
            ])
        lines.extend(_table(("Zone", "Lap distance", "Minimum speed", "Median brake", "Load", "Action (inference)"), zone_rows))
        lines.append("")

    knowledge_map = _mapping(knowledge)
    notes, targets, references = _find_knowledge_values(knowledge_map) if knowledge_map else ([], [], [])
    if notes or targets or references:
        lines.extend(["## Cached car/track and Garage61 context", ""])
        for note in notes:
            lines.append(f"- {note}")
        for target in targets:
            lines.append(f"- {_knowledge_target_line(target)}")
        for reference in references:
            lines.append(f"- **Representative lap:** {_reference_line(reference)}")
        lines.append("")

    visuals = render_visuals(analysis)
    if visuals:
        lines.extend(["## Visuals", ""])
        labels = {
            "visuals/lap-trend.svg": "Green-flag lap-time trend",
            "visuals/tire-remaining.svg": "Measured tire remaining",
            "visuals/track-load-profile.svg": "Track shape and load profile",
        }
        for path in visuals:
            label = labels.get(path, path.rsplit("/", 1)[-1])
            lines.append(f"![{label}]({path})")
            lines.append("")

    lines.extend(["## Evidence and limits", ""])
    channels = _mapping(quality.get("channels"))
    available = [name for name, present in channels.items() if present]
    missing = list(quality.get("missing") or [name for name, present in channels.items() if not present])
    if available:
        lines.append("- Measured telemetry used: " + ", ".join(str(item) for item in available) + ".")
    if missing:
        lines.append("- Missing evidence: " + ", ".join(str(item) for item in missing) + ".")
    coverage = _mapping(source.get("channel_coverage"))
    recorded_count = _first_number(
        coverage.get("recorded_count"),
        len(_sequence(source.get("available_channels"))) or None,
    )
    loaded_count = _first_number(
        coverage.get("loaded_count"),
        len(_sequence(source.get("loaded_channels"))) or None,
    )
    analyzed_count = _first_number(
        coverage.get("analyzed_count"),
        len(_sequence(source.get("analyzed_channels"))) or None,
    )
    coverage_parts: list[str] = []
    if recorded_count is not None:
        coverage_parts.append(f"{recorded_count:.0f} recorded")
    if loaded_count is not None:
        coverage_parts.append(f"{loaded_count:.0f} loaded")
    if analyzed_count is not None:
        coverage_parts.append(f"{analyzed_count:.0f} analyzed")
    native_hz = _number(coverage.get("native_tick_rate_hz"))
    analysis_hz = _number(coverage.get("analysis_sample_rate_hz"))
    if native_hz is not None and analysis_hz is not None:
        coverage_parts.append(
            f"{analysis_hz:g} Hz routine analysis from {native_hz:g} Hz raw telemetry"
        )
    elif analysis_hz is not None:
        coverage_parts.append(f"{analysis_hz:g} Hz routine analysis")
    elif native_hz is not None:
        coverage_parts.append(f"{native_hz:g} Hz native telemetry")
    if coverage.get("catalog_complete") is True:
        coverage_parts.append("recorded-channel catalog complete")
    elif coverage.get("catalog_complete") is False:
        coverage_parts.append("recorded-channel catalog incomplete")
    if coverage_parts:
        lines.append("- Telemetry coverage: " + " / ".join(coverage_parts) + ".")
    raw_policy = _mapping(source.get("raw_source_policy"))
    raw_note = str(raw_policy.get("note") or "").strip()
    if raw_note:
        mode = str(raw_policy.get("mode") or "reference-originals")
        lines.append(f"- Raw-source policy ({mode}): {raw_note}")
    replay_matches = [
        _mapping(item)
        for item in _sequence(related_local_files.get("replay_matches"))
        if isinstance(item, Mapping)
    ]
    if replay_matches:
        lines.append(
            "- Local join: exact SubSessionID replay found at "
            + str(replay_matches[0].get("path"))
            + "."
        )
    lines.append(
        "- Tire rule: "
        + str(quality.get("tire_wear_rule") or "Tire readings are discrete pit-service observations assigned to the preceding run.")
    )
    lines.append(
        "- Causality rule: "
        + str(quality.get("causality_rule") or "Brake, steering, and load traces support likely causes, not exact within-run tire-wear timing.")
    )
    for limitation in _sequence(strategy.get("limitations")):
        text = str(limitation).strip()
        if text:
            lines.append(f"- Strategy limit: {text}")
    return "\n".join(lines).rstrip() + "\n"


def _xml(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _svg_document(title: str, description: str, width: int, height: int, content: Sequence[str]) -> str:
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-labelledby="chart-title chart-desc" viewBox="0 0 {width} {height}">',
            f"  <title id=\"chart-title\">{_xml(title)}</title>",
            f"  <desc id=\"chart-desc\">{_xml(description)}</desc>",
            "  <style>",
            "    text { font-family: Segoe UI, Arial, sans-serif; fill: #edf4f7; }",
            "    .title { font-size: 22px; font-weight: 700; }",
            "    .subtitle { font-size: 12px; fill: #9fb0bd; }",
            "    .axis { font-size: 11px; fill: #9fb0bd; }",
            "    .label { font-size: 12px; }",
            "  </style>",
            f'  <rect width="{width}" height="{height}" rx="14" fill="{BACKGROUND}"/>',
            f'  <text class="title" x="32" y="38">{_xml(title)}</text>',
            f'  <text class="subtitle" x="32" y="59">{_xml(description)}</text>',
            *content,
            "</svg>",
            "",
        ]
    )


def _scale(value: float, domain_min: float, domain_max: float, range_min: float, range_max: float) -> float:
    if abs(domain_max - domain_min) < 1e-12:
        return (range_min + range_max) / 2.0
    fraction = (value - domain_min) / (domain_max - domain_min)
    return range_min + fraction * (range_max - range_min)


def _line_path(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _linear_fit(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    mean_x = statistics.fmean(x for x, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator <= 1e-12:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    return slope, mean_y - slope * mean_x


def _lap_trend_svg(analysis: Mapping[str, Any]) -> str | None:
    raw_laps = [_mapping(item) for item in _sequence(analysis.get("laps")) if isinstance(item, Mapping)]
    usable: list[tuple[float, float]] = []
    repair_screened: list[tuple[float, float]] = []
    for lap in raw_laps:
        lap_number = _number(lap.get("lap"))
        lap_time = _number(lap.get("lap_time_s"))
        if lap_number is None or lap_time is None or not lap.get("complete", True):
            continue
        if (_number(lap.get("pit_time_s")) or 0.0) >= 1.0:
            continue
        if str(lap.get("flag_state") or "").lower() == "green":
            context = _mapping(lap.get("damage_repair_context"))
            target = (
                repair_screened
                if context.get("automatic_coaching_reference_eligible") is False
                else usable
            )
            target.append((lap_number, lap_time))
    if not usable and not repair_screened:
        for lap in raw_laps:
            lap_number = _number(lap.get("lap"))
            lap_time = _number(lap.get("lap_time_s"))
            if lap_number is not None and lap_time is not None and lap.get("complete", True):
                usable.append((lap_number, lap_time))
    plot_values = usable + repair_screened
    if not plot_values:
        return None
    usable.sort()
    repair_screened.sort()
    plot_values.sort()
    width, height = 960, 400
    left, right, top, bottom = 76.0, 28.0, 86.0, 56.0
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [point[0] for point in plot_values]
    ys = [point[1] for point in plot_values]
    x_min, x_max = min(xs), max(xs)
    if x_min == x_max:
        x_min, x_max = x_min - 1.0, x_max + 1.0
    y_min, y_max = min(ys), max(ys)
    padding = max(0.25, (y_max - y_min) * 0.12)
    y_min, y_max = y_min - padding, y_max + padding
    points = [
        (
            _scale(x, x_min, x_max, left, left + plot_w),
            _scale(y, y_min, y_max, top + plot_h, top),
        )
        for x, y in usable
    ]
    screened_points = [
        (
            _scale(x, x_min, x_max, left, left + plot_w),
            _scale(y, y_min, y_max, top + plot_h, top),
        )
        for x, y in repair_screened
    ]
    content = [f'  <rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" rx="8" fill="{PANEL}"/>']
    for tick in range(6):
        fraction = tick / 5
        y = top + plot_h * fraction
        value = y_max - (y_max - y_min) * fraction
        content.append(f'  <line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="{GRID}" stroke-width="1"/>')
        content.append(f'  <text class="axis" x="{left - 10}" y="{y + 4:.2f}" text-anchor="end">{value:.2f}</text>')
    for tick in range(6):
        fraction = tick / 5
        x = left + plot_w * fraction
        value = x_min + (x_max - x_min) * fraction
        content.append(f'  <text class="axis" x="{x:.2f}" y="{top + plot_h + 24}" text-anchor="middle">{value:.0f}</text>')
    if len(points) > 1:
        content.append(f'  <polyline points="{_line_path(points)}" fill="none" stroke="{ACCENT}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    for x, y in points:
        content.append(f'  <circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{ACCENT}" stroke="{BACKGROUND}" stroke-width="2"/>')
    for x, y in screened_points:
        content.append(f'  <circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" fill="{WARNING}" stroke="{BACKGROUND}" stroke-width="2"/>')
    if fit := _linear_fit(usable):
        slope, intercept = fit
        fit_points = []
        for raw_x in (x_min, x_max):
            raw_y = slope * raw_x + intercept
            fit_points.append((
                _scale(raw_x, x_min, x_max, left, left + plot_w),
                _scale(raw_y, y_min, y_max, top + plot_h, top),
            ))
        content.append(f'  <polyline points="{_line_path(fit_points)}" fill="none" stroke="{WARNING}" stroke-width="2" stroke-dasharray="7 6"/>')
        content.append(f'  <text class="label" x="{left + plot_w - 8}" y="{top + 19}" text-anchor="end" fill="{WARNING}">Trend {slope:+.3f} s/lap</text>')
    content.extend([
        f'  <text class="axis" x="{left + plot_w / 2}" y="{height - 14}" text-anchor="middle">Lap</text>',
        f'  <text class="axis" transform="translate(18 {top + plot_h / 2}) rotate(-90)" text-anchor="middle">Lap time (s)</text>',
    ])
    if repair_screened:
        content.append(
            f'  <text class="axis" x="{left}" y="{top - 10}" fill="{WARNING}">Amber = repair/tow-screened; excluded from trend</text>'
        )
    return _svg_document(
        "Green-flag lap-time trend",
        (
            "Clean complete non-pit green laps; repair/tow-screened laps remain visible but do not drive the trend."
            if repair_screened
            else "Complete, non-pit green laps; dashed line is the least-squares pace trend."
        ),
        width,
        height,
        content,
    )


def _tire_svg(analysis: Mapping[str, Any]) -> str | None:
    observations = []
    for raw_run in _sequence(analysis.get("runs")):
        run = _mapping(raw_run)
        observation = _mapping(run.get("tire_observation"))
        values = {
            tire: _number(_mapping(_mapping(observation.get("tires")).get(tire)).get("average_remaining_percent"))
            for tire in TIRES
        }
        if observation and any(value is not None for value in values.values()):
            observations.append((run.get("run_number"), values))
    if not observations:
        return None
    observations = observations[-16:]
    width = max(760, min(1800, 170 + len(observations) * 112))
    height = 430
    left, right, top, bottom = 68.0, 24.0, 90.0, 78.0
    plot_w, plot_h = width - left - right, height - top - bottom
    content = [f'  <rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" rx="8" fill="{PANEL}"/>']
    for tick in range(5):
        value = tick * 25
        y = _scale(value, 0, 100, top + plot_h, top)
        content.append(f'  <line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="{GRID}" stroke-width="1"/>')
        content.append(f'  <text class="axis" x="{left - 9}" y="{y + 4:.2f}" text-anchor="end">{value}%</text>')
    group_w = plot_w / len(observations)
    bar_gap = 3.0
    bar_w = min(18.0, max(7.0, (group_w - 18.0) / 4.0 - bar_gap))
    for group_index, (run_number, values) in enumerate(observations):
        center = left + group_w * (group_index + 0.5)
        total_w = 4 * bar_w + 3 * bar_gap
        start_x = center - total_w / 2
        for tire_index, tire in enumerate(TIRES):
            value = values[tire]
            if value is None:
                continue
            x = start_x + tire_index * (bar_w + bar_gap)
            y = _scale(value, 0, 100, top + plot_h, top)
            height_px = top + plot_h - y
            content.append(f'  <rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{height_px:.2f}" rx="2" fill="{TIRE_COLORS[tire]}"/>')
            if bar_w >= 11:
                content.append(f'  <text class="axis" x="{x + bar_w / 2:.2f}" y="{max(top + 11, y - 5):.2f}" text-anchor="middle">{value:.0f}</text>')
        content.append(f'  <text class="label" x="{center:.2f}" y="{top + plot_h + 25}" text-anchor="middle">Run {_xml(run_number)}</text>')
    legend_x = left
    for tire in TIRES:
        content.append(f'  <rect x="{legend_x}" y="{height - 31}" width="12" height="12" rx="2" fill="{TIRE_COLORS[tire]}"/>')
        content.append(f'  <text class="axis" x="{legend_x + 18}" y="{height - 20}">{tire}</text>')
        legend_x += 56
    return _svg_document(
        "Measured tire remaining",
        "Average percent tread remaining; each group is a discrete post-run pit-service reading.",
        width,
        height,
        content,
    )


def _heat_color(value: float) -> str:
    value = max(0.0, min(1.0, value))
    if value <= 0.5:
        fraction = value * 2.0
        start, end = (63, 197, 190), (245, 185, 66)
    else:
        fraction = (value - 0.5) * 2.0
        start, end = (245, 185, 66), (239, 84, 102)
    rgb = tuple(round(a + (b - a) * fraction) for a, b in zip(start, end))
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def _profile_loads(profile: Sequence[Mapping[str, Any]]) -> list[float]:
    lateral_scale = _percentile([item.get("lateral_g") for item in profile], 0.90) or 1.0
    steering_scale = _percentile([item.get("steering_abs_rad") for item in profile], 0.90) or 1.0
    result = []
    for item in profile:
        lateral = (_number(item.get("lateral_g")) or 0.0) / max(lateral_scale, 1e-9)
        steering = (_number(item.get("steering_abs_rad")) or 0.0) / max(steering_scale, 1e-9)
        brake = _number(item.get("brake")) or 0.0
        result.append(max(0.0, min(1.0, max(lateral, steering * 0.85, brake))))
    return result


def _track_profile_svg(analysis: Mapping[str, Any]) -> str | None:
    track = _mapping(analysis.get("track_profile"))
    profile = [
        _mapping(item) for item in _sequence(track.get("profile"))
        if isinstance(item, Mapping)
        and _number(item.get("lap_pct")) is not None
    ]
    if len(profile) < 2:
        return None
    profile.sort(key=lambda item: _number(item.get("lap_pct")) or 0.0)
    loads = _profile_loads(profile)
    shape = [
        _mapping(item) for item in _sequence(track.get("shape"))
        if isinstance(item, Mapping)
        and _number(item.get("x")) is not None
        and _number(item.get("y")) is not None
    ]
    has_shape = len(shape) >= 3
    width, height = 1100, 480
    top, bottom = 92.0, 58.0
    plot_h = height - top - bottom
    content: list[str] = []
    if has_shape:
        shape_left, shape_width = 42.0, 410.0
        profile_left, profile_width = 510.0, 550.0
        content.append(f'  <rect x="{shape_left}" y="{top}" width="{shape_width}" height="{plot_h}" rx="8" fill="{PANEL}"/>')
        raw_x = [_number(item.get("x")) or 0.0 for item in shape]
        raw_y = [_number(item.get("y")) or 0.0 for item in shape]
        x_min, x_max = min(raw_x), max(raw_x)
        y_min, y_max = min(raw_y), max(raw_y)
        x_span, y_span = max(x_max - x_min, 1e-9), max(y_max - y_min, 1e-9)
        scale = min((shape_width - 46) / x_span, (plot_h - 46) / y_span)
        rendered_w, rendered_h = x_span * scale, y_span * scale
        x_pad = shape_left + (shape_width - rendered_w) / 2
        y_pad = top + (plot_h - rendered_h) / 2
        profile_pcts = [_number(item.get("lap_pct")) or 0.0 for item in profile]
        mapped_shape: list[tuple[float, float, float]] = []
        for item, x_value, y_value in zip(shape, raw_x, raw_y):
            px = x_pad + (x_value - x_min) * scale
            py = y_pad + rendered_h - (y_value - y_min) * scale
            lap_pct = _number(item.get("lap_pct")) or 0.0
            nearest = min(range(len(profile_pcts)), key=lambda index: abs(profile_pcts[index] - lap_pct))
            mapped_shape.append((px, py, loads[nearest]))
        for before, after in zip(mapped_shape, mapped_shape[1:]):
            x1, y1, load1 = before
            x2, y2, load2 = after
            content.append(f'  <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{_heat_color((load1 + load2) / 2)}" stroke-width="6" stroke-linecap="round"/>')
        content.append(f'  <circle cx="{mapped_shape[0][0]:.2f}" cy="{mapped_shape[0][1]:.2f}" r="6" fill="{TEXT}" stroke="{BACKGROUND}" stroke-width="2"/>')
        content.append(f'  <text class="axis" x="{shape_left + shape_width / 2}" y="{height - 18}" text-anchor="middle">Track shape · dot = start/finish</text>')
    else:
        profile_left, profile_width = 74.0, 986.0
    content.append(f'  <rect x="{profile_left}" y="{top}" width="{profile_width}" height="{plot_h}" rx="8" fill="{PANEL}"/>')
    for tick in range(6):
        x = profile_left + profile_width * tick / 5
        content.append(f'  <line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" stroke="{GRID}" stroke-width="1"/>')
        content.append(f'  <text class="axis" x="{x:.2f}" y="{top + plot_h + 23}" text-anchor="middle">{tick * 20}%</text>')
    speeds = [_number(item.get("speed_mph")) for item in profile]
    finite_speeds = [value for value in speeds if value is not None]
    speed_min, speed_max = (min(finite_speeds), max(finite_speeds)) if finite_speeds else (0.0, 1.0)
    speed_padding = max(2.0, (speed_max - speed_min) * 0.08)
    speed_min, speed_max = speed_min - speed_padding, speed_max + speed_padding
    speed_points: list[tuple[float, float]] = []
    load_points: list[tuple[float, float]] = []
    for item, speed, load in zip(profile, speeds, loads):
        pct = _number(item.get("lap_pct")) or 0.0
        x = _scale(pct, 0, 1, profile_left, profile_left + profile_width)
        if speed is not None:
            speed_points.append((x, _scale(speed, speed_min, speed_max, top + plot_h, top)))
        load_points.append((x, _scale(load, 0, 1, top + plot_h, top)))
    if speed_points:
        content.append(f'  <polyline points="{_line_path(speed_points)}" fill="none" stroke="{ACCENT}" stroke-width="3" stroke-linejoin="round"/>')
    content.append(f'  <polyline points="{_line_path(load_points)}" fill="none" stroke="{WARNING}" stroke-width="2" stroke-linejoin="round" opacity="0.9"/>')
    content.extend([
        f'  <text class="axis" x="{profile_left + 8}" y="{top + 18}">Speed {speed_max:.0f} mph</text>',
        f'  <text class="axis" x="{profile_left + 8}" y="{top + plot_h - 8}">{speed_min:.0f} mph</text>',
        f'  <line x1="{profile_left + profile_width - 142}" y1="{top + 17}" x2="{profile_left + profile_width - 118}" y2="{top + 17}" stroke="{ACCENT}" stroke-width="3"/>',
        f'  <text class="axis" x="{profile_left + profile_width - 111}" y="{top + 21}">speed</text>',
        f'  <line x1="{profile_left + profile_width - 62}" y1="{top + 17}" x2="{profile_left + profile_width - 38}" y2="{top + 17}" stroke="{WARNING}" stroke-width="3"/>',
        f'  <text class="axis" x="{profile_left + profile_width - 31}" y="{top + 21}">load</text>',
    ])
    title = "Track shape and load profile" if has_shape else "Track load profile"
    description = "Green-running median speed and normalized brake/steering/lateral-load intensity by lap distance."
    if not has_shape:
        description += " Only the load profile is shown."
    return _svg_document(title, description, width, height, content)


def render_visuals(analysis: Mapping[str, Any]) -> dict[str, str]:
    """Return standalone SVG report artifacts keyed by safe relative paths."""

    if not isinstance(analysis, Mapping):
        raise TypeError("analysis must be a mapping")
    result: dict[str, str] = {}
    if lap_chart := _lap_trend_svg(analysis):
        result["visuals/lap-trend.svg"] = lap_chart
    if tire_chart := _tire_svg(analysis):
        result["visuals/tire-remaining.svg"] = tire_chart
    if track_chart := _track_profile_svg(analysis):
        result["visuals/track-load-profile.svg"] = track_chart
    return result
