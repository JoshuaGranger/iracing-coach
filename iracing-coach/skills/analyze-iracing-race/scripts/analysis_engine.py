"""Evidence-first post-race analysis for iRacing telemetry.

This module intentionally separates measurements from coaching language.  The
returned JSON records exact observations, proxy metrics, and uncertainty so a
Codex skill can combine it with Garage61 and web references without presenting
inferences as tire-wear ground truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

try:  # Package import and direct script loading are both supported.
    from .groove_analysis import analyze_groove_evolution
    from .race_foundations import (
        build_race_replay,
        build_tire_learning,
        build_track_geometry,
    )
    from . import competitor_pace
    from . import lap_reference
    from . import pit_loss
    from . import race_plan_decision
    from . import strategy_model
    from . import time_loss
    from . import tire_energy
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from groove_analysis import analyze_groove_evolution
    from race_foundations import build_race_replay, build_tire_learning, build_track_geometry
    import competitor_pace
    import lap_reference
    import pit_loss
    import race_plan_decision
    import strategy_model
    import time_loss
    import tire_energy


CAUTION_FLAGS = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000
CHECKERED_FLAG = 0x0001
WHITE_FLAG = 0x0002
GREEN_FLAG = 0x0004
BLACK_FLAG = 0x00010000
REPAIR_REQUIRED_FLAG = 0x00100000
TIRES = ("LF", "RF", "LR", "RR")
POSITIONS = ("L", "M", "R")
PIT_SERVICE_BITS = {
    "LF_tire_change_requested": 0x01,
    "RF_tire_change_requested": 0x02,
    "LR_tire_change_requested": 0x04,
    "RR_tire_change_requested": 0x08,
    "fuel_fill_requested": 0x10,
    "windshield_tearoff_requested": 0x20,
    "fast_repair_requested": 0x40,
}
METERS_TO_INCHES = 39.37007874015748
KPA_TO_PSI = 0.14503773773020923
ANALYSIS_SCHEMA_VERSION = 2
ANALYSIS_PROFILE_VERSION = "post-race-foundations-v13"
ANALYZER_SOURCE_FILES = (
    "analysis_engine.py",
    "competitor_pace.py",
    "groove_analysis.py",
    "ibt_reader.py",
    "lap_reference.py",
    "pit_loss.py",
    "race_foundations.py",
    "strategy_model.py",
    "time_loss.py",
    "tire_energy.py",
)
RACE_GRADE_RUBRIC_VERSION = "race-execution-v2"
RACE_GRADE_CATEGORY_WEIGHTS = {
    "pace": 30,
    "consistency": 20,
    "tire_management": 20,
    "strategy": 15,
    "racecraft": 15,
}

# These are recorded SDK states, not a vehicle-health model.  In particular,
# incident points and pace loss never prove physical damage.  Repair timers,
# tow time, and a fast-repair counter increment are the only evidence used by
# the damage/repair analyzer below.
DAMAGE_REPAIR_CHANNEL_ALIASES = {
    "tow_timer": ("PlayerCarTowTime",),
    "mandatory_repair_timer": ("PitRepairLeft",),
    "optional_repair_timer": ("PitOptRepairLeft",),
    "fast_repairs_used": ("PlayerFastRepairsUsed", "FastRepairUsed"),
    "fast_repairs_available": ("FastRepairAvailable",),
    "incident_points": (
        "PlayerCarMyIncidentCount",
        "PlayerCarDriverIncidentCount",
        "PlayerCarTeamIncidentCount",
    ),
}
PIT_SERVICE_STATUS_LABELS = {
    0: "none",
    1: "in_progress",
    2: "complete",
    100: "too_far_left",
    101: "too_far_right",
    102: "too_far_forward",
    103: "too_far_back",
    104: "bad_angle",
    105: "cant_fix_that",
}
TRACK_LOCATION_LABELS = {
    -1: "Not in world",
    0: "Off track",
    1: "In pit stall",
    2: "Approaching pits",
    3: "On track",
}

PLATFORM_CHANNEL_ALIASES = {
    "center_front_splitter": (
        "CFSRrideHeight",
        "CFSRRideHeight",
        "CenterFrontSplitterRideHeight",
        "CenterFrontRideHeight",
        "SplitterRideHeight",
    ),
    **{
        tire: (f"{tire}rideHeight", f"{tire}RideHeight")
        for tire in TIRES
    },
}


def _shock_channel_aliases(tire: str, measurement: str) -> tuple[str, ...]:
    if measurement == "deflection":
        suffixes = ("shockDefl", "ShockDefl", "shockDeflection", "ShockDeflection")
        damper_suffixes = ("damperDefl", "DamperDefl", "damperDeflection", "DamperDeflection")
    else:
        suffixes = ("shockVel", "ShockVel", "shockVelocity", "ShockVelocity")
        damper_suffixes = ("damperVel", "DamperVel", "damperVelocity", "DamperVelocity")
    return tuple(
        dict.fromkeys(
            (
                *(f"{tire}SH{suffix}" for suffix in suffixes),
                *(f"{tire}{suffix}" for suffix in suffixes),
                *(f"{tire}{suffix}" for suffix in damper_suffixes),
            )
        )
    )


def _tire_pressure_aliases(tire: str, *, cold: bool = False) -> tuple[str, ...]:
    if cold:
        return (
            f"{tire}coldPressure",
            f"{tire}ColdPressure",
            f"{tire}tireColdPressure",
            f"{tire}TireColdPressure",
        )
    return (
        f"{tire}pressure",
        f"{tire}Pressure",
        f"{tire}tirePressure",
        f"{tire}TirePressure",
        f"{tire}hotPressure",
        f"{tire}HotPressure",
    )


def _tire_temperature_aliases(tire: str, position: str) -> tuple[str, ...]:
    return (
        f"{tire}temp{position}",
        f"{tire}Temp{position}",
        f"{tire}tireTemp{position}",
        f"{tire}TireTemp{position}",
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vehicle_sideslip_degrees(
    longitudinal_velocity_m_s: Any,
    lateral_velocity_m_s: Any,
) -> float | None:
    """Return chassis-frame vehicle sideslip, or ``None`` when undefined.

    Recorded iRacing ``VelocityX`` and ``VelocityY`` are respectively the
    forward and lateral velocity components in the car's frame.  Their planar
    magnitude matches the recorded ``Speed`` channel.  ``Yaw`` and
    ``YawNorth`` are orientation channels and are deliberately not used here.

    Sideslip is unstable at very low speed and wraps toward 180 degrees while
    travelling backwards, so those samples are gaps rather than invented
    driving data.
    """

    longitudinal = _finite(longitudinal_velocity_m_s)
    lateral = _finite(lateral_velocity_m_s)
    if longitudinal is None or lateral is None:
        return None
    if math.hypot(longitudinal, lateral) < 5.0 or longitudinal <= 0.5:
        return None
    angle = math.degrees(math.atan2(lateral, longitudinal))
    return angle if math.isfinite(angle) else None


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _finite(value)) is not None]
    return statistics.fmean(numbers) if numbers else None


def _median(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _finite(value)) is not None]
    return statistics.median(numbers) if numbers else None


def _percentile(values: Iterable[Any], percentile: float) -> float | None:
    numbers = sorted(number for value in values if (number := _finite(value)) is not None)
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    position = max(0.0, min(1.0, percentile)) * (len(numbers) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return numbers[lower]
    fraction = position - lower
    return numbers[lower] * (1.0 - fraction) + numbers[upper] * fraction


def _round(value: Any, digits: int = 3) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _fraction(value: Any) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    if abs(number) > 1.5:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _deep_find(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        for child in value.values():
            found = _deep_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found = _deep_find(child, key)
            if found is not None:
                return found
    return None


def _leaf_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(_leaf_count(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_leaf_count(child) for child in value)
    return 1 if value is not None else 0


def _path_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _numeric_text(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return _finite(value)
    if not isinstance(value, str):
        return None
    token = value.strip().split()[0] if value.strip() else ""
    try:
        return float(token)
    except ValueError:
        return None


_IRSDK_UNLIMITED_LAPS = 32767


def _lap_limit(value: Any) -> int | None:
    """Return a declared finite lap limit, excluding SDK sentinel values."""

    if isinstance(value, bool):
        return None
    number = _numeric_text(value)
    if number is None or not math.isfinite(number) or not number.is_integer():
        return None
    laps = int(number)
    return laps if 0 < laps < _IRSDK_UNLIMITED_LAPS else None


def _scheduled_race_laps(
    table: "TelemetryTable", race_session: Mapping[str, Any]
) -> int | None:
    """Resolve the configured race distance without mistaking progress for it."""

    configured = race_session.get("SessionLaps")
    if (
        isinstance(configured, str)
        and configured.strip()
        and configured.strip().split()[0].casefold() == "unlimited"
    ):
        return None
    totals = table.get("SessionLapsTotal", default=None) if table.has("SessionLapsTotal") else ()
    telemetry_total = next(
        (laps for raw in reversed(totals) if (laps := _lap_limit(raw)) is not None),
        None,
    )
    return telemetry_total if telemetry_total is not None else _lap_limit(configured)


def _duration_minutes(value: Any) -> float | None:
    """Normalize iRacing session-duration values to minutes.

    SessionInfo normally emits ``SessionTime`` in seconds (often as text such
    as ``"1800.0000 sec"``).  Honor an explicit minute/hour suffix while
    treating bare values and other suffixes as seconds.
    """

    number = _numeric_text(value)
    if number is None or not math.isfinite(number) or number <= 0.0:
        return None
    unit = str(value).strip().lower() if isinstance(value, str) else ""
    if "hour" in unit or re.search(r"\bhrs?\b", unit):
        return number * 60.0
    if "min" in unit:
        return number
    return number / 60.0


def _first_not_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


class TelemetryTable:
    """Normalize column-oriented or row-oriented parser output."""

    def __init__(self, telemetry: Mapping[str, Any]) -> None:
        raw_channels = telemetry.get("channels") or telemetry.get("data") or telemetry.get("samples") or {}
        row_count = 0
        if isinstance(raw_channels, Mapping):
            self.channels = {
                str(name): list(values) if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)) else [values]
                for name, values in raw_channels.items()
            }
        else:
            rows = telemetry.get("samples") or raw_channels
            columns: MutableMapping[str, list[Any]] = defaultdict(list)
            for row in rows or []:
                for values in columns.values():
                    values.append(None)
                if isinstance(row, Mapping):
                    normalized_row = {str(name): value for name, value in row.items()}
                    for name, value in normalized_row.items():
                        if name in columns:
                            columns[name][-1] = value
                        else:
                            columns[name] = [None] * row_count + [value]
                row_count += 1
            self.channels = dict(columns)
        self.accessed_channels: set[str] = set()
        self.variables = telemetry.get("variables", {})
        self.available_variables = telemetry.get("available_variables") or self.variables
        self.channel_units: dict[str, str] = {}
        if isinstance(self.variables, Mapping):
            variable_items = self.variables.items()
        elif isinstance(self.variables, Sequence) and not isinstance(
            self.variables, (str, bytes, bytearray)
        ):
            variable_items = ((None, variable) for variable in self.variables)
        else:
            variable_items = ()
        for fallback_name, variable in variable_items:
            if not isinstance(variable, Mapping):
                continue
            name = variable.get("name") or fallback_name
            unit = variable.get("unit") or variable.get("units")
            if name and unit:
                self.channel_units[str(name)] = str(unit)
        self.session_info = telemetry.get("session_info") or telemetry.get("metadata", {}).get("session_info") or {}
        self.metadata = dict(telemetry.get("metadata") or {})
        if telemetry.get("channel_selection") is not None:
            self.metadata["channel_selection"] = telemetry.get("channel_selection")
        if telemetry.get("sample_rate_hz") is not None:
            self.metadata["sample_rate"] = telemetry.get("sample_rate_hz")
        if telemetry.get("native_tick_rate_hz") is not None:
            self.metadata["tick_rate"] = telemetry.get("native_tick_rate_hz")
        lengths = [len(values) for values in self.channels.values()]
        self.length = max([row_count, *lengths], default=0)
        for values in self.channels.values():
            if len(values) < self.length:
                values.extend([None] * (self.length - len(values)))

    def available_catalog(self) -> list[dict[str, Any]]:
        """Return the complete available-channel catalogue when supplied."""

        raw = self.available_variables
        if isinstance(raw, Mapping):
            items = raw.items()
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            items = ((None, item) for item in raw)
        else:
            items = ()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for fallback_name, variable in items:
            if not isinstance(variable, Mapping):
                continue
            name = variable.get("name") or fallback_name
            if not name or str(name) in seen:
                continue
            item = dict(variable)
            item["name"] = str(name)
            result.append(item)
            seen.add(str(name))
        return result

    def get(self, *aliases: str, default: Any = None) -> list[Any]:
        for alias in aliases:
            if alias in self.channels:
                self.accessed_channels.add(alias)
                values = self.channels[alias]
                if len(values) < self.length:
                    values = values + [None] * (self.length - len(values))
                return values
        return [default] * self.length

    def has(self, *aliases: str) -> bool:
        for alias in aliases:
            if alias in self.channels:
                self.accessed_channels.add(alias)
                return True
        return False

    def resolve(self, *aliases: str, default: Any = None) -> tuple[str | None, list[Any]]:
        for alias in aliases:
            if alias in self.channels:
                self.accessed_channels.add(alias)
                return alias, self.get(alias, default=default)
        return None, [default] * self.length

    def unit(self, channel: str | None) -> str | None:
        return self.channel_units.get(channel) if channel else None


@lru_cache(maxsize=128)
def _normalized_unit(value: str | None) -> str:
    return str(value or "").strip().lower().replace("°", "").replace(" ", "")


def _convert_setup_value(value: Any, quantity: str, source_unit: str | None) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    unit = _normalized_unit(source_unit)
    if quantity == "distance":
        if unit in {"in", "inch", "inches", '"'}:
            return number
        if unit in {"ft", "foot", "feet"}:
            return number * 12.0
        if unit in {"mm", "millimeter", "millimeters"}:
            return number / 25.4
        if unit in {"cm", "centimeter", "centimeters"}:
            return number / 2.54
        return number * METERS_TO_INCHES
    if quantity == "velocity":
        if unit in {"in/s", "inch/s", "inches/s", "ips"}:
            return number
        if unit in {"ft/s", "fps"}:
            return number * 12.0
        if unit in {"mm/s"}:
            return number / 25.4
        if unit in {"cm/s"}:
            return number / 2.54
        return number * METERS_TO_INCHES
    if quantity == "pressure":
        if unit in {"psi", "lb/in2", "lbf/in2"}:
            return number
        if unit in {"pa", "pascal", "pascals"}:
            return number * KPA_TO_PSI / 1000.0
        if unit in {"bar"}:
            return number * 100.0 * KPA_TO_PSI
        return number * KPA_TO_PSI
    if quantity == "temperature":
        if unit in {"f", "fahrenheit"}:
            return number
        if unit in {"k", "kelvin"}:
            return (number - 273.15) * 9.0 / 5.0 + 32.0
        return number * 9.0 / 5.0 + 32.0
    return number


def _resolved_setup_series(
    table: TelemetryTable,
    aliases: Sequence[str],
    quantity: str,
) -> dict[str, Any] | None:
    channel, raw_values = table.resolve(*aliases, default=None)
    if channel is None:
        return None
    source_unit = table.unit(channel) or {
        "distance": "m",
        "velocity": "m/s",
        "pressure": "kPa",
        "temperature": "C",
    }.get(quantity)
    return {
        "channel": channel,
        "source_unit": source_unit,
        "values": [
            _convert_setup_value(value, quantity, source_unit)
            for value in raw_values
        ],
    }


def _dt_series(times: Sequence[Any], fallback_hz: float) -> list[float]:
    fallback = 1.0 / max(fallback_hz, 1.0)
    result = [fallback] * len(times)
    for index in range(1, len(times)):
        previous = _finite(times[index - 1])
        current = _finite(times[index])
        if previous is not None and current is not None and 0 < current - previous < 2.0:
            result[index] = current - previous
    return result


def _linear_slope(points: Sequence[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator <= 1e-12:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def _count_corrections(values: Sequence[Any], threshold: float = 0.025) -> int:
    signs: list[int] = []
    previous = None
    for raw in values:
        current = _finite(raw)
        if current is None:
            continue
        if previous is not None:
            delta = current - previous
            if abs(delta) >= threshold:
                signs.append(1 if delta > 0 else -1)
        previous = current
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def _positive_distance_delta(values: Sequence[Any], indices: Sequence[int]) -> float | None:
    total = 0.0
    previous: float | None = None
    observed = False
    for index in indices:
        current = _finite(values[index]) if index < len(values) else None
        if current is None:
            continue
        if previous is not None and current >= previous:
            total += current - previous
            observed = True
        previous = current
    return total if observed else None


def _absolute_series_summary(
    values: Sequence[Any],
    indices: Sequence[int],
    *,
    scale: float = 1.0,
    digits: int = 3,
) -> dict[str, Any] | None:
    numbers = [
        abs(number) * scale
        for index in indices
        if index < len(values) and (number := _finite(values[index])) is not None
    ]
    if not numbers:
        return None
    return {
        "abs_mean": _round(_mean(numbers), digits),
        "abs_p95": _round(_percentile(numbers, 0.95), digits),
        "abs_max": _round(max(numbers), digits),
    }


def _vehicle_dynamics_summary(
    table: TelemetryTable,
    indices: Sequence[int],
    dts: Sequence[float],
    speed: Sequence[Any],
    throttle: Sequence[Any],
    brake: Sequence[Any],
) -> dict[str, Any]:
    """Summarize measured motion plus conservative tire-load proxies."""

    result: dict[str, Any] = {}
    wheel_speeds = {
        tire: table.get(f"{tire}speed", default=None)
        for tire in TIRES
        if table.has(f"{tire}speed")
    }
    wheel_odometers = {
        tire: table.get(f"{tire}odometer", default=None)
        for tire in TIRES
        if table.has(f"{tire}odometer")
    }
    ratio_samples: dict[str, list[float]] = {tire: [] for tire in wheel_speeds}
    lock_time = 0.0
    front_lock_time = 0.0
    rear_spin_time = 0.0
    valid_ratio_samples = 0
    for index in indices:
        vehicle_speed = _finite(speed[index]) if index < len(speed) else None
        if vehicle_speed is None or vehicle_speed < 10.0:
            continue
        ratios: dict[str, float] = {}
        for tire, values in wheel_speeds.items():
            wheel_speed = _finite(values[index]) if index < len(values) else None
            if wheel_speed is None:
                continue
            ratio = (wheel_speed - vehicle_speed) / max(vehicle_speed, 1e-9)
            # Reject corrupt/transient values while retaining meaningful lock
            # and spin divergence.
            if abs(ratio) <= 2.0:
                ratios[tire] = ratio
                ratio_samples[tire].append(ratio)
        if not ratios:
            continue
        valid_ratio_samples += 1
        dt = dts[index] if index < len(dts) else 0.0
        brake_value = _fraction(brake[index]) if index < len(brake) else None
        throttle_value = _fraction(throttle[index]) if index < len(throttle) else None
        if (brake_value or 0.0) >= 0.15 and min(ratios.values()) <= -0.08:
            lock_time += dt
        front_ratios = [ratios[tire] for tire in ("LF", "RF") if tire in ratios]
        if (brake_value or 0.0) >= 0.15 and front_ratios and min(front_ratios) <= -0.08:
            front_lock_time += dt
        rear_ratios = [ratios[tire] for tire in ("LR", "RR") if tire in ratios]
        if (
            (throttle_value or 0.0) >= 0.35
            and rear_ratios
            and _mean(rear_ratios) is not None
            and (_mean(rear_ratios) or 0.0) >= 0.08
        ):
            rear_spin_time += dt

    if wheel_speeds:
        ratio_summary: dict[str, Any] = {}
        for tire, values in ratio_samples.items():
            if not values:
                continue
            ratio_summary[tire] = {
                "p05": _round(_percentile(values, 0.05), 4),
                "median": _round(_median(values), 4),
                "p95": _round(_percentile(values, 0.95), 4),
            }
        distance = {
            tire: _round(_positive_distance_delta(values, indices), 1)
            for tire, values in wheel_odometers.items()
        }
        result["wheel_speed"] = {
            "channels": sorted(f"{tire}speed" for tire in wheel_speeds),
            "valid_ratio_samples": valid_ratio_samples,
            "ratio_vs_vehicle_speed": ratio_summary,
            "tire_distance_m": {key: value for key, value in distance.items() if value is not None},
            "braking_wheel_lock_proxy_s": _round(lock_time),
            "front_wheel_lock_proxy_s": _round(front_lock_time),
            "rear_wheelspin_proxy_s": _round(rear_spin_time),
        }

    angular_specs = (
        ("YawRate", "yaw_rate_deg_s"),
        ("PitchRate", "pitch_rate_deg_s"),
        ("RollRate", "roll_rate_deg_s"),
    )
    motion: dict[str, Any] = {}
    for channel, label in angular_specs:
        if not table.has(channel):
            continue
        summary = _absolute_series_summary(
            table.get(channel, default=None), indices, scale=180.0 / math.pi
        )
        if summary:
            motion[label] = summary
    if table.has("SteeringWheelTorque"):
        torque = _absolute_series_summary(
            table.get("SteeringWheelTorque", default=None), indices
        )
        if torque:
            motion["steering_torque_nm"] = torque
    if motion:
        result["motion"] = motion

    abs_active = table.get("BrakeABSactive", default=False)
    abs_cut = table.get("BrakeABScutPct", default=None)
    abs_time = sum(
        dts[index]
        for index in indices
        if index < len(abs_active) and _bool(abs_active[index])
    )
    cut_values = [
        value
        for index in indices
        if index < len(abs_cut) and (value := _fraction(abs_cut[index])) is not None
    ]
    if table.has("BrakeABSactive", "BrakeABScutPct"):
        result["abs"] = {
            "active_s": _round(abs_time),
            "cut_mean": _round(_mean(cut_values)),
            "cut_max": _round(max(cut_values) if cut_values else None),
        }

    if table.has("dcBrakeBias"):
        brake_bias_values = table.get("dcBrakeBias", default=None)
        values = [
            value
            for index in indices
            if index < table.length
            and (value := _finite(brake_bias_values[index])) is not None
        ]
        if values:
            result["brake_bias_setting"] = {
                "minimum": _round(min(values), 4),
                "median": _round(_median(values), 4),
                "maximum": _round(max(values), 4),
            }
    return result


def _identity(table: TelemetryTable) -> dict[str, Any]:
    info = table.session_info
    weekend = _path_get(info, "WeekendInfo") or {}
    driver_info = _path_get(info, "DriverInfo") or {}
    options = _path_get(weekend, "WeekendOptions") or {}
    driver_car_idx = _deep_find(driver_info, "DriverCarIdx")
    selected_driver: Mapping[str, Any] = {}
    drivers = driver_info.get("Drivers", []) if isinstance(driver_info, Mapping) else []
    if isinstance(drivers, Sequence):
        for driver in drivers:
            if isinstance(driver, Mapping) and str(driver.get("CarIdx")) == str(driver_car_idx):
                selected_driver = driver
                break
    car_setup = _path_get(info, "CarSetup") or {}
    setup_fingerprint = None
    if car_setup:
        setup_fingerprint = hashlib.sha256(
            json.dumps(car_setup, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:16]
    fixed_value = options.get("IsFixedSetup")
    if fixed_value is None:
        fixed_value = weekend.get("IsFixedSetup")
    is_fixed = None if fixed_value is None else _bool(fixed_value)
    tire_compound = _first_not_none(
        _deep_find(car_setup, "TireCompound"),
        _deep_find(car_setup, "Compound"),
        selected_driver.get("TireCompound"),
    )
    conditions = {
        "air_temp_c": _round(_median(table.get("AirTemp", default=None)), 2),
        "track_temp_c": _round(_median(table.get("TrackTempCrew", "TrackTemp", default=None)), 2),
        "track_wetness": _round(_median(table.get("TrackWetness", default=None)), 3),
        "track_usage": _round(_median(table.get("TrackUsage", default=None)), 3),
    }
    return {
        "session_id": weekend.get("SessionID") or _deep_find(info, "SessionID"),
        "subsession_id": weekend.get("SubSessionID") or _deep_find(info, "SubSessionID"),
        "session_unique_id": weekend.get("SessionUniqueID") or _deep_find(info, "SessionUniqueID"),
        "session_start": (
            weekend.get("SessionStartDate")
            or weekend.get("Date")
            or table.metadata.get("session_start")
            or table.metadata.get("start_time_utc")
            or table.metadata.get("mtime_utc")
        ),
        "series_id": weekend.get("SeriesID"),
        "season_id": weekend.get("SeasonID"),
        "season_year": weekend.get("SeasonYear") or table.metadata.get("season_year"),
        "season_quarter": weekend.get("SeasonQuarter") or table.metadata.get("season_quarter"),
        "race_week": weekend.get("RaceWeek"),
        "event_type": weekend.get("EventType") or table.metadata.get("event_type"),
        "category": weekend.get("Category"),
        "sim_build": weekend.get("SimMode") or weekend.get("BuildType") or table.metadata.get("sim_build"),
        "track_id": weekend.get("TrackID"),
        "track_name": weekend.get("TrackDisplayName") or weekend.get("TrackName"),
        "track_config": weekend.get("TrackConfigName") or weekend.get("TrackDisplayShortName"),
        "track_type": weekend.get("TrackType"),
        "track_length": weekend.get("TrackLength"),
        "car_id": selected_driver.get("CarID") or driver_info.get("DriverCarID"),
        "car_name": selected_driver.get("CarScreenName") or selected_driver.get("CarScreenNameShort") or driver_info.get("DriverCarName"),
        "car_path": selected_driver.get("CarPath") or driver_info.get("DriverCarPath"),
        "driver_name": selected_driver.get("UserName"),
        "customer_id": selected_driver.get("UserID"),
        "driver_irating": _numeric_text(_first_not_none(
            selected_driver.get("IRating"), selected_driver.get("iRating")
        )),
        "weight_penalty_kg": _numeric_text(_first_not_none(
            selected_driver.get("CarClassWeightPenalty"), selected_driver.get("WeightPenalty")
        )),
        "power_adjust_percent": _numeric_text(_first_not_none(
            selected_driver.get("CarClassPowerAdjust"), selected_driver.get("PowerAdjust")
        )),
        "max_fuel_percent": _numeric_text(selected_driver.get("CarClassMaxFuelPct")),
        "conditions": conditions,
        "is_fixed_setup": is_fixed,
        "setup_name": driver_info.get("DriverSetupName") or selected_driver.get("CarSetupName"),
        "setup_load_type": driver_info.get("DriverSetupLoadType"),
        "setup_modified": driver_info.get("DriverSetupIsModified"),
        "setup_fingerprint": setup_fingerprint,
        "setup_parameter_count": _leaf_count(car_setup),
        "tire_compound": tire_compound,
        "setup": car_setup,
    }


def _find_race_session(
    info: Mapping[str, Any], sim_session_num: Any = None
) -> Mapping[str, Any]:
    sessions = _path_get(info, "SessionInfo", "Sessions") or []
    if isinstance(sessions, Sequence):
        race_sessions = [
            item for item in sessions
            if isinstance(item, Mapping) and str(item.get("SessionType", "")).lower() == "race"
        ]
        if race_sessions:
            requested_session_num = (
                None
                if isinstance(sim_session_num, bool)
                else _numeric_text(sim_session_num)
            )
            if (
                requested_session_num is not None
                and math.isfinite(requested_session_num)
                and requested_session_num.is_integer()
            ):
                for item in race_sessions:
                    item_session_num = _numeric_text(item.get("SessionNum"))
                    if (
                        item_session_num is not None
                        and math.isfinite(item_session_num)
                        and item_session_num == requested_session_num
                    ):
                        return item
            return race_sessions[-1]
    return {}


def _session_track_usage(table: TelemetryTable) -> str | None:
    """Resolve an inherited race rubber state without inventing track buildup."""

    sessions = _path_get(table.session_info, "SessionInfo", "Sessions") or []
    if not isinstance(sessions, Sequence):
        return None
    race_index = next(
        (
            index
            for index in range(len(sessions) - 1, -1, -1)
            if isinstance(sessions[index], Mapping)
            and str(sessions[index].get("SessionType", "")).lower() == "race"
        ),
        len(sessions) - 1,
    )
    for index in range(race_index, -1, -1):
        session = sessions[index]
        if not isinstance(session, Mapping):
            continue
        state = str(session.get("SessionTrackRubberState") or "").strip()
        if state and state.lower() != "carry over":
            return state
    return None


def _lap_conditions(table: TelemetryTable, indices: Sequence[int]) -> dict[str, Any]:
    def lap_values(*aliases: str) -> list[float]:
        series = table.get(*aliases, default=None)
        return [
            value
            for index in indices
            if index < len(series) and (value := _finite(series[index])) is not None
        ]

    def median(*aliases: str) -> float | None:
        return _median(lap_values(*aliases))

    sky_value = median("Skies")
    sky_labels = {0: "Clear", 1: "Partly cloudy", 2: "Mostly cloudy", 3: "Overcast"}
    sky_label = sky_labels.get(int(round(sky_value))) if sky_value is not None else None
    if sky_label is None:
        recorded_sky = str(_deep_find(table.session_info, "TrackSkies") or "").strip()
        if recorded_sky and recorded_sky.lower() != "dynamic":
            sky_label = recorded_sky

    track_temp_c = median("TrackTempCrew", "TrackTemp")
    air_temp_c = median("AirTemp")
    wind_speed_mps = median("WindVel")
    wind_direction_rad = median("WindDir")
    humidity = _fraction(median("RelativeHumidity"))
    fog = _fraction(median("FogLevel"))
    precipitation = _fraction(median("Precipitation"))
    air_pressure_pa = median("AirPressure")
    air_density_kg_m3 = median("AirDensity")
    usage = median("TrackUsage")
    usage_percent = _fraction(usage) * 100.0 if usage is not None else None
    declared_wet_values = table.get("WeatherDeclaredWet", default=None)
    declared_wet = (
        any(_bool(declared_wet_values[index]) for index in indices if index < len(declared_wet_values))
        if table.has("WeatherDeclaredWet")
        else None
    )
    return {
        "sky": sky_label,
        "track_temperature_f": _round(track_temp_c * 9.0 / 5.0 + 32.0 if track_temp_c is not None else None, 1),
        "air_temperature_f": _round(air_temp_c * 9.0 / 5.0 + 32.0 if air_temp_c is not None else None, 1),
        "wind_speed_mph": _round(wind_speed_mps * 2.236936 if wind_speed_mps is not None else None, 1),
        "wind_direction_degrees": _round(math.degrees(wind_direction_rad) % 360.0 if wind_direction_rad is not None else None, 1),
        "relative_humidity_percent": _round(humidity * 100.0 if humidity is not None else None, 1),
        "fog_percent": _round(fog * 100.0 if fog is not None else None, 1),
        "air_pressure_inhg": _round(air_pressure_pa * 0.00029529983071445 if air_pressure_pa is not None else None, 2),
        "air_density_lb_ft3": _round(air_density_kg_m3 * 0.0624279606 if air_density_kg_m3 is not None else None, 3),
        "precipitation_percent": _round(precipitation * 100.0 if precipitation is not None else None, 1),
        "track_wetness_state": _round(median("TrackWetness"), 0),
        "track_usage_percent": _round(usage_percent, 1),
        "track_usage": _session_track_usage(table),
        "weather_declared_wet": declared_wet,
    }


def _lap_summaries(table: TelemetryTable) -> list[dict[str, Any]]:
    n = table.length
    times = table.get("SessionTime", "SessionTimeOfDay", default=None)
    laps = table.get("Lap", default=None)
    has_completed_laps = table.has("LapCompleted")
    completed_laps = table.get("LapCompleted", default=None) if has_completed_laps else ()
    max_completed_lap = max(
        (
            number
            for value in completed_laps
            if (number := _finite(value)) is not None
        ),
        default=None,
    )
    lap_pct = table.get("LapDistPct", default=None)
    speed = table.get("Speed", default=None)
    throttle = table.get("Throttle", "ThrottleRaw", default=None)
    brake = table.get("Brake", "BrakeRaw", default=None)
    # SteeringWheelAngleMax is the configured range/limit, not a measured
    # steering trace, so it must never stand in for SteeringWheelAngle.
    steering = table.get("SteeringWheelAngle", default=None)
    lat_accel = table.get("LatAccel", default=None)
    long_accel = table.get("LongAccel", default=None)
    fuel = table.get("FuelLevel", default=None)
    flags = table.get("SessionFlags", default=0)
    session_state = table.get("SessionState", default=None)
    has_session_state = table.has("SessionState")
    pit = table.get("OnPitRoad", default=False)
    track_location = table.get("PlayerTrackSurface", default=None)
    has_track_location = table.has("PlayerTrackSurface")
    car_distance_ahead = table.get("CarDistAhead", default=None)
    car_distance_behind = table.get("CarDistBehind", default=None)
    has_traffic_distance = table.has("CarDistAhead", "CarDistBehind")
    overall_position = table.get("PlayerCarPosition", default=None)
    class_position = table.get("PlayerCarClassPosition", default=None)
    sample_rate = _finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0
    dts = _dt_series(times, sample_rate)
    groups: MutableMapping[int, list[int]] = defaultdict(list)
    for index, raw_lap in enumerate(laps):
        lap_value = _finite(raw_lap)
        if lap_value is not None and lap_value >= 0:
            groups[int(lap_value)].append(index)
    summaries: list[dict[str, Any]] = []
    for lap_number in sorted(groups):
        indices = groups[lap_number]
        if len(indices) < 2:
            continue
        start, end = indices[0], indices[-1]
        start_time, end_time = _finite(times[start]), _finite(times[end])
        duration = end_time - start_time if start_time is not None and end_time is not None else sum(dts[i] for i in indices)
        caution_time = 0.0
        green_time = 0.0
        pit_time = 0.0
        observed_flags = 0
        racing_state_time = 0.0
        on_track_time = 0.0
        track_location_time = 0.0
        traffic_proximity_time = 0.0
        traffic_observed_time = 0.0
        for index in indices:
            flag_value = int(_finite(flags[index]) or 0)
            observed_flags |= flag_value
            if flag_value & CAUTION_FLAGS:
                caution_time += dts[index]
            else:
                # SessionFlags does not keep the short-lived green flag bit
                # asserted for an entire green run.  In a Race, samples with
                # no yellow/caution state are green-running samples.
                green_time += dts[index]
            if _bool(pit[index]):
                pit_time += dts[index]
            if has_session_state and int(_finite(session_state[index]) or -1) == 4:
                racing_state_time += dts[index]
            if has_track_location:
                location = _finite(track_location[index])
                if location is not None:
                    track_location_time += dts[index]
                    if int(location) == 3:  # irsdk_TrkLoc::OnTrack
                        on_track_time += dts[index]
            if has_traffic_distance:
                ahead = _finite(car_distance_ahead[index])
                behind = _finite(car_distance_behind[index])
                traffic_observed_time += dts[index]
                speed_mps = max(0.0, _finite(speed[index]) or 0.0)
                ahead_near = (
                    ahead is not None
                    and ahead > 0.0
                    and ahead <= max(25.0, speed_mps * 0.75)
                )
                behind_near = (
                    behind is not None
                    and behind > 0.0
                    and behind <= max(15.0, speed_mps * 0.40)
                )
                if ahead_near or behind_near:
                    traffic_proximity_time += dts[index]
        classified = "mixed"
        running_time = max(caution_time + green_time, 1e-9)
        if caution_time / running_time >= 0.5:
            classified = "caution"
        elif green_time / running_time >= 0.5:
            classified = "green"
        pct_values = [_finite(lap_pct[index]) for index in indices]
        pct_values = [value for value in pct_values if value is not None]
        distance_complete = bool(
            pct_values and min(pct_values) <= 0.15 and max(pct_values) >= 0.85
        )
        if has_completed_laps and lap_number > 0:
            complete = bool(
                distance_complete
                and max_completed_lap is not None
                and max_completed_lap >= lap_number
            )
        else:
            complete = distance_complete
        speed_values = [_finite(speed[index]) for index in indices]
        speed_values = [value for value in speed_values if value is not None]
        brake_values = [_fraction(brake[index]) for index in indices]
        brake_values = [value for value in brake_values if value is not None]
        throttle_values = [_fraction(throttle[index]) for index in indices]
        throttle_values = [value for value in throttle_values if value is not None]
        steering_values = [_finite(steering[index]) for index in indices]
        steering_values = [value for value in steering_values if value is not None]
        fuel_start, fuel_end = _finite(fuel[start]), _finite(fuel[end])
        position_values = [
            int(value)
            for index in indices
            if (value := _finite(overall_position[index])) is not None and value > 0
        ]
        class_position_values = [
            int(value)
            for index in indices
            if (value := _finite(class_position[index])) is not None and value > 0
        ]
        fuel_used = None
        if fuel_start is not None and fuel_end is not None:
            negative_drops = 0.0
            previous = fuel_start
            for index in indices[1:]:
                current = _finite(fuel[index])
                if current is not None and previous is not None and current < previous:
                    negative_drops += previous - current
                if current is not None:
                    previous = current
            fuel_used = negative_drops
        pit_entry = any(
            _bool(pit[index]) and (index == 0 or not _bool(pit[index - 1]))
            for index in indices
        )
        pit_exit = any(
            not _bool(pit[index]) and index > 0 and _bool(pit[index - 1])
            for index in indices
        )
        flag_states: list[str] = []
        if green_time > 0.0:
            flag_states.append("green")
        if caution_time > 0.0:
            flag_states.append("yellow")
        if observed_flags & BLACK_FLAG:
            flag_states.append("black")
        if observed_flags & WHITE_FLAG:
            flag_states.append("white")
        if observed_flags & CHECKERED_FLAG:
            flag_states.append("checkered")
        brake_energy = 0.0
        steering_work = 0.0
        overlap_time = 0.0
        lateral_exposure = 0.0
        for index in indices:
            v = max(_finite(speed[index]) or 0.0, 0.0)
            b = _fraction(brake[index]) or 0.0
            s = abs(_finite(steering[index]) or 0.0)
            la = abs(_finite(lat_accel[index]) or 0.0)
            brake_energy += b * v * dts[index]
            steering_work += s * v * dts[index]
            lateral_exposure += la * dts[index]
            if b >= 0.08 and s >= 0.08:
                overlap_time += dts[index]
        dynamics = _vehicle_dynamics_summary(
            table, indices, dts, speed, throttle, brake
        )
        summaries.append(
            {
                "lap": lap_number,
                "start_index": start,
                "end_index": end,
                "start_time": _round(start_time),
                "end_time": _round(end_time),
                "lap_time_s": _round(duration),
                "complete": complete,
                "flag_state": classified,
                "flag_states": flag_states,
                "green_fraction": _round(green_time / running_time),
                "caution_fraction": _round(caution_time / running_time),
                "pit_time_s": _round(pit_time),
                "pit_entry": pit_entry,
                "pit_exit": pit_exit,
                "racing_state_fraction": _round(
                    racing_state_time / running_time if has_session_state else None,
                    4,
                ),
                "clean_context": {
                    "on_track_fraction": _round(
                        on_track_time / track_location_time
                        if track_location_time > 0.0 else None,
                        4,
                    ),
                    "traffic_proximity_fraction": _round(
                        traffic_proximity_time / traffic_observed_time
                        if traffic_observed_time > 0.0 else None,
                        4,
                    ),
                    "traffic_screened": has_traffic_distance,
                    "traffic_rule": (
                        "car ahead within max(25 m, 0.75 s) or behind within "
                        "max(15 m, 0.40 s) for at least 10% of observed lap time"
                    ),
                },
                "speed": {
                    "average_mph": _round((_mean(speed_values) or 0.0) * 2.236936),
                    "minimum_mph": _round((min(speed_values) if speed_values else 0.0) * 2.236936),
                    "maximum_mph": _round((max(speed_values) if speed_values else 0.0) * 2.236936),
                },
                "controls": {
                    "brake_mean": _round(_mean(brake_values)),
                    "brake_max": _round(max(brake_values) if brake_values else None),
                    "throttle_mean": _round(_mean(throttle_values)),
                    "steering_abs_mean_rad": _round(_mean(abs(value) for value in steering_values)),
                    "brake_energy_proxy": _round(brake_energy),
                    "steering_work_proxy": _round(steering_work),
                    "brake_steer_overlap_s": _round(overlap_time),
                    "steering_corrections": _count_corrections(steering_values),
                    "lateral_g_exposure_proxy": _round(lateral_exposure / 9.80665),
                    "long_accel_mean_mps2": _round(_mean(long_accel[index] for index in indices)),
                },
                "vehicle_dynamics": dynamics,
                "fuel": {
                    "start_l": _round(fuel_start),
                    "end_l": _round(fuel_end),
                    "used_l": _round(fuel_used),
                    "used_gal": _round(fuel_used / 3.785411784 if fuel_used is not None else None),
                },
                "conditions": _lap_conditions(table, indices),
                "position": {
                    "start": position_values[0] if position_values else None,
                    "end": position_values[-1] if position_values else None,
                    "best": min(position_values) if position_values else None,
                    "worst": max(position_values) if position_values else None,
                    "class_start": class_position_values[0] if class_position_values else None,
                    "class_end": class_position_values[-1] if class_position_values else None,
                },
            }
        )
    return summaries


def _additional_trace_sources(
    table: TelemetryTable,
) -> tuple[list[dict[str, Any]], dict[str, list[float | None]], dict[str, str], dict[str, int]]:
    """Resolve optional distance-domain signals without manufacturing gaps.

    The catalog contains only channels that are actually present and contain at
    least one finite value. Derived wheel slip is published only when both the
    individual wheel-speed channel and vehicle speed were recorded.
    """

    catalog: list[dict[str, Any]] = []
    series: dict[str, list[float | None]] = {}
    reducers: dict[str, str] = {}
    digits: dict[str, int] = {}

    def register(
        signal_id: str,
        name: str,
        unit: str,
        category: str,
        evidence_type: str,
        description: str,
        source_channels: Sequence[str],
        values: Sequence[Any],
        *,
        reducer: str = "median",
        precision: int = 3,
    ) -> None:
        normalized = [_finite(value) for value in values]
        if not any(value is not None for value in normalized):
            return
        series[signal_id] = normalized
        reducers[signal_id] = reducer
        digits[signal_id] = precision
        catalog.append(
            {
                "id": signal_id,
                "name": name,
                "unit": unit,
                "category": category,
                "evidence_type": evidence_type,
                "description": description,
                "source_channels": list(source_channels),
            }
        )

    def add(
        signal_id: str,
        name: str,
        unit: str,
        category: str,
        aliases: Sequence[str],
        transform: Any = None,
        *,
        reducer: str = "median",
        precision: int = 3,
        description: str = "Recorded iRacing telemetry.",
    ) -> tuple[str | None, list[Any]]:
        channel, raw = table.resolve(*aliases, default=None)
        if channel is None:
            return None, raw
        converted = [
            transform(value, channel) if transform is not None else _finite(value)
            for value in raw
        ]
        register(
            signal_id,
            name,
            unit,
            category,
            "measured",
            description,
            [channel],
            converted,
            reducer=reducer,
            precision=precision,
        )
        return channel, raw

    def average_channels(
        signal_id: str,
        name: str,
        unit: str,
        category: str,
        alias_groups: Sequence[Sequence[str]],
        transform: Any,
        description: str,
    ) -> None:
        resolved = [table.resolve(*aliases, default=None) for aliases in alias_groups]
        available = [(channel, values) for channel, values in resolved if channel is not None]
        if not available:
            return
        values: list[float | None] = []
        for index in range(table.length):
            samples = [
                converted
                for channel, raw in available
                if index < len(raw)
                and (converted := transform(raw[index], channel)) is not None
            ]
            values.append(_mean(samples))
        register(
            signal_id,
            name,
            unit,
            category,
            "measured",
            description,
            [channel for channel, _ in available],
            values,
            precision=2,
        )

    percent = lambda value, _channel: (
        fraction * 100.0 if (fraction := _fraction(value)) is not None else None
    )
    binary = lambda value, _channel: (
        None if value is None else (1.0 if _bool(value) else 0.0)
    )
    temperature_f = lambda value, channel: _convert_setup_value(
        value, "temperature", table.unit(channel)
    )
    pressure_psi = lambda value, channel: _convert_setup_value(
        value, "pressure", table.unit(channel)
    )
    distance_in = lambda value, channel: _convert_setup_value(
        value, "distance", table.unit(channel)
    )
    velocity_in_s = lambda value, channel: _convert_setup_value(
        value, "velocity", table.unit(channel)
    )

    def degrees(value: Any, channel: str) -> float | None:
        number = _finite(value)
        if number is None:
            return None
        return number if "deg" in _normalized_unit(table.unit(channel)) else math.degrees(number)

    def speed_mph(value: Any, channel: str) -> float | None:
        number = _finite(value)
        if number is None:
            return None
        unit = _normalized_unit(table.unit(channel))
        if unit in {"mph", "mi/h"}:
            return number
        if unit in {"km/h", "kph"}:
            return number / 1.609344
        return number * 2.236936

    def gallons(value: Any, channel: str) -> float | None:
        number = _finite(value)
        if number is None:
            return None
        return number if "gal" in _normalized_unit(table.unit(channel)) else number / 3.785411784

    def acceleration_g(value: Any, channel: str) -> float | None:
        number = _finite(value)
        if number is None:
            return None
        return number if _normalized_unit(table.unit(channel)) == "g" else number / 9.80665

    add("clutch", "Clutch", "%", "Controls", ("Clutch",), percent, description="Recorded clutch position.")
    add("abs-active", "ABS active", "on / off", "Controls", ("BrakeABSactive",), binary, reducer="max", precision=0, description="Recorded ABS-active state.")
    add("abs-cut", "ABS intervention", "%", "Controls", ("BrakeABScutPct",), percent, reducer="max", precision=2, description="Recorded ABS brake-cut percentage.")
    add("brake-bias", "Brake bias", "%", "Controls", ("dcBrakeBias",), percent, precision=2, description="Recorded in-car brake-bias setting.")
    add("steering-torque", "Steering torque", "Nm", "Controls", ("SteeringWheelTorque",), reducer="signed_peak", description="Recorded steering-wheel torque.")

    add("vertical-g", "Vertical G", "g", "Vehicle", ("VertAccel",), acceleration_g, reducer="signed_peak", description="Recorded vertical acceleration.")
    add("pitch", "Pitch", "deg", "Vehicle", ("Pitch",), degrees, reducer="signed_peak", description="Recorded chassis pitch angle.")
    add("roll", "Roll", "deg", "Vehicle", ("Roll",), degrees, reducer="signed_peak", description="Recorded chassis roll angle.")
    add("pitch-rate", "Pitch rate", "deg/s", "Vehicle", ("PitchRate",), degrees, reducer="signed_peak", description="Recorded chassis pitch rate.")
    add("roll-rate", "Roll rate", "deg/s", "Vehicle", ("RollRate",), degrees, reducer="signed_peak", description="Recorded chassis roll rate.")

    speed_channel, vehicle_speed = table.resolve("Speed", default=None)
    for tire in TIRES:
        wheel_channel, wheel_speed = add(
            f"{tire.lower()}-wheel-speed",
            f"{tire} wheel speed",
            "mph",
            "Tires",
            (f"{tire}speed",),
            speed_mph,
            description="Recorded individual wheel speed.",
        )
        if wheel_channel is not None and speed_channel is not None:
            slip: list[float | None] = []
            for index in range(table.length):
                car = _finite(vehicle_speed[index]) if index < len(vehicle_speed) else None
                wheel = _finite(wheel_speed[index]) if index < len(wheel_speed) else None
                if car is None or wheel is None or car < 10.0:
                    slip.append(None)
                    continue
                ratio = (wheel - car) / max(car, 1e-9)
                slip.append(ratio * 100.0 if abs(ratio) <= 2.0 else None)
            register(
                f"{tire.lower()}-wheel-slip",
                f"{tire} wheel slip",
                "%",
                "Tires",
                "derived",
                "Wheel speed relative to recorded vehicle speed; positive is spin and negative is lock.",
                [wheel_channel, speed_channel],
                slip,
                reducer="signed_peak",
                precision=2,
            )
        add(
            f"{tire.lower()}-pressure",
            f"{tire} pressure",
            "psi",
            "Tires",
            _tire_pressure_aliases(tire),
            pressure_psi,
            precision=2,
            description="Recorded live tire pressure; it does not establish tire wear.",
        )
        average_channels(
            f"{tire.lower()}-carcass-temp",
            f"{tire} carcass temperature",
            "deg F",
            "Tires",
            [_tire_temperature_aliases(tire, position) for position in ("CL", "CM", "CR")],
            temperature_f,
            "Average of the recorded inner, middle, and outer carcass temperatures.",
        )
        average_channels(
            f"{tire.lower()}-surface-temp",
            f"{tire} surface temperature",
            "deg F",
            "Tires",
            [_tire_temperature_aliases(tire, position) for position in POSITIONS],
            temperature_f,
            "Average of the recorded inner, middle, and outer surface temperatures.",
        )

    add("fuel-level", "Fuel level", "gal", "Fuel", ("FuelLevel",), gallons, precision=3, description="Recorded fuel level.")
    add("fuel-use-rate", "Fuel use rate", "kg/h", "Fuel", ("FuelUsePerHour",), precision=3, description="Recorded instantaneous engine fuel-use mass rate.")

    add("center-front-ride-height", "Center-front ride height", "in", "Chassis", PLATFORM_CHANNEL_ALIASES["center_front_splitter"], distance_in, reducer="signed_peak", description="Recorded center-front or splitter ride height.")
    for tire in TIRES:
        add(f"{tire.lower()}-ride-height", f"{tire} ride height", "in", "Chassis", PLATFORM_CHANNEL_ALIASES[tire], distance_in, reducer="signed_peak", description="Recorded corner ride height.")
        add(f"{tire.lower()}-shock-deflection", f"{tire} shock deflection", "in", "Chassis", _shock_channel_aliases(tire, "deflection"), distance_in, reducer="signed_peak", description="Recorded shock or damper deflection.")
        add(f"{tire.lower()}-shock-velocity", f"{tire} shock velocity", "in/s", "Chassis", _shock_channel_aliases(tire, "velocity"), velocity_in_s, reducer="signed_peak", description="Recorded shock or damper velocity.")

    add("track-temperature", "Track temperature", "deg F", "Conditions", ("TrackTempCrew", "TrackTemp"), temperature_f, precision=1, description="Recorded track temperature.")
    add("air-temperature", "Air temperature", "deg F", "Conditions", ("AirTemp",), temperature_f, precision=1, description="Recorded air temperature.")
    add("wind-speed", "Wind speed", "mph", "Conditions", ("WindVel",), speed_mph, precision=1, description="Recorded wind speed.")
    add("humidity", "Relative humidity", "%", "Conditions", ("RelativeHumidity",), percent, precision=1, description="Recorded relative humidity.")
    add("fog", "Fog", "%", "Conditions", ("FogLevel",), percent, precision=1, description="Recorded fog level.")
    add("precipitation", "Precipitation", "%", "Conditions", ("Precipitation",), percent, precision=2, description="Recorded precipitation level.")
    add("air-pressure", "Air pressure", "inHg", "Conditions", ("AirPressure",), lambda value, _channel: ((_finite(value) or 0.0) * 0.00029529983071445 if _finite(value) is not None else None), precision=2, description="Recorded ambient air pressure.")
    add("air-density", "Air density", "lb/ft3", "Conditions", ("AirDensity",), lambda value, _channel: ((_finite(value) or 0.0) * 0.0624279606 if _finite(value) is not None else None), precision=4, description="Recorded ambient air density.")
    add("track-wetness", "Track wetness", "state", "Conditions", ("TrackWetness",), precision=0, reducer="last", description="Recorded iRacing categorical track-wetness state.")
    add("track-usage", "Track usage", "%", "Conditions", ("TrackUsage",), percent, precision=1, description="Recorded track-usage percentage.")
    add("weather-wet", "Wet declared", "on / off", "Conditions", ("WeatherDeclaredWet",), binary, precision=0, reducer="max", description="Recorded weather-declared-wet state.")

    add("overall-position", "Overall position", "position", "Race", ("PlayerCarPosition",), precision=0, reducer="last", description="Recorded overall race position.")
    add("class-position", "Class position", "position", "Race", ("PlayerCarClassPosition",), precision=0, reducer="last", description="Recorded class position.")
    add("distance-ahead", "Distance ahead", "ft", "Race", ("CarDistAhead",), lambda value, _channel: ((_finite(value) or 0.0) * 3.280839895 if _finite(value) is not None else None), precision=1, description="Recorded distance to the car ahead.")
    add("distance-behind", "Distance behind", "ft", "Race", ("CarDistBehind",), lambda value, _channel: ((_finite(value) or 0.0) * 3.280839895 if _finite(value) is not None else None), precision=1, description="Recorded distance to the car behind.")
    add("on-pit-road", "On pit road", "on / off", "Race", ("OnPitRoad",), binary, precision=0, reducer="max", description="Recorded pit-road state.")
    add("track-surface", "Track surface", "state", "Race", ("PlayerTrackSurface",), precision=0, reducer="last", description="Recorded iRacing player track-location state.")

    return catalog, series, reducers, digits


def _lap_trace_payload(
    table: TelemetryTable,
    laps: Sequence[Mapping[str, Any]],
    *,
    maximum_bins: int = 160,
) -> dict[str, Any]:
    """Build bounded distance-domain traces for local interactive rendering.

    Each bin retains event extrema as well as its representative value so a
    short brake application, throttle lift, or steering correction is not
    erased by screen-oriented downsampling.  This is presentation data; the
    full recorded source remains available for bounded native-rate queries.
    """

    lap_distance = table.get("LapDistPct", default=None)
    speed = table.get("Speed", default=None)
    throttle = table.get("Throttle", "ThrottleRaw", default=None)
    brake = table.get("Brake", "BrakeRaw", default=None)
    steering = table.get("SteeringWheelAngle", default=None)
    gear = table.get("Gear", default=None)
    rpm = table.get("RPM", default=None)
    yaw_rate = table.get("YawRate", default=None)
    velocity_x = table.get("VelocityX", default=None)
    velocity_y = table.get("VelocityY", default=None)
    lateral_accel = table.get("LatAccel", default=None)
    longitudinal_accel = table.get("LongAccel", default=None)
    latitude = table.get("Lat", default=None)
    longitude = table.get("Lon", default=None)
    session_time = table.get("SessionTime", "SessionTimeOfDay", default=None)
    raw_sectors = _path_get(table.session_info, "SplitTimeInfo", "Sectors") or []
    sector_start_pcts = sorted({
        value
        for item in raw_sectors
        if isinstance(item, Mapping)
        and (value := _finite(item.get("SectorStartPct"))) is not None
        and 0.0 <= value < 1.0
    })

    def values(indices: Sequence[int], series: Sequence[Any]) -> list[float]:
        return [
            value
            for index in indices
            if index < len(series) and (value := _finite(series[index])) is not None
        ]

    def signed_peak(items: Sequence[float]) -> float | None:
        return max(items, key=abs) if items else None

    additional_catalog, additional_series, additional_reducers, additional_digits = (
        _additional_trace_sources(table)
    )
    traces: list[dict[str, Any]] = []
    for lap in laps:
        start = int(_finite(lap.get("start_index")) or 0)
        end = int(_finite(lap.get("end_index")) or -1)
        if end < start:
            continue
        buckets: MutableMapping[int, list[int]] = defaultdict(list)
        for index in range(start, min(end + 1, table.length)):
            pct = _finite(lap_distance[index])
            if pct is None or pct < -0.01 or pct > 1.01:
                continue
            pct = pct % 1.0
            bucket = min(maximum_bins - 1, max(0, int(pct * maximum_bins)))
            buckets[bucket].append(index)
        points: list[dict[str, Any]] = []
        for bucket in sorted(buckets):
            indices = buckets[bucket]
            speeds = values(indices, speed)
            throttles = [_fraction(value) for value in values(indices, throttle)]
            throttles = [value for value in throttles if value is not None]
            brakes = [_fraction(value) for value in values(indices, brake)]
            brakes = [value for value in brakes if value is not None]
            steerings = values(indices, steering)
            gears = values(indices, gear)
            rpms = values(indices, rpm)
            yaws = [value * 180.0 / math.pi for value in values(indices, yaw_rate)]
            sideslips: list[float] = []
            for index in indices:
                if index >= len(velocity_x) or index >= len(velocity_y):
                    continue
                sideslip = _vehicle_sideslip_degrees(
                    velocity_x[index], velocity_y[index]
                )
                if sideslip is not None:
                    sideslips.append(sideslip)
            lateral_g = [value / 9.80665 for value in values(indices, lateral_accel)]
            longitudinal_g = [value / 9.80665 for value in values(indices, longitudinal_accel)]
            latitudes = values(indices, latitude)
            longitudes = values(indices, longitude)
            session_times = values(indices, session_time)
            additional_signals: dict[str, float] = {}
            for signal_id, signal_series in additional_series.items():
                samples = values(indices, signal_series)
                if not samples:
                    continue
                reducer = additional_reducers[signal_id]
                if reducer == "max":
                    representative = max(samples)
                elif reducer == "signed_peak":
                    representative = signed_peak(samples)
                elif reducer == "last":
                    representative = samples[-1]
                else:
                    representative = _median(samples)
                rounded = _round(representative, additional_digits[signal_id])
                if rounded is not None:
                    additional_signals[signal_id] = rounded

            representative_speed = _median(speeds)
            representative_steering = signed_peak(steerings)
            representative_lateral_g = signed_peak(lateral_g)
            brake_peak = max(brakes) if brakes else None
            throttle_minimum = min(throttles) if throttles else None
            speed_scale = min(1.5, max(0.0, (representative_speed or 0.0) / 80.0))
            stress_terms = [
                min(1.0, (brake_peak or 0.0) * speed_scale),
                min(1.0, abs(representative_steering or 0.0) / 0.35 * speed_scale),
                min(1.0, abs(representative_lateral_g or 0.0) / 2.0),
            ]
            points.append(
                {
                    "lap_pct": round((bucket + 0.5) / maximum_bins, 6),
                    "session_time_s": _round(_median(session_times), 6),
                    "speed_mph": _round((representative_speed or 0.0) * 2.236936, 3),
                    "speed_min_mph": _round(min(speeds) * 2.236936 if speeds else None, 3),
                    "speed_max_mph": _round(max(speeds) * 2.236936 if speeds else None, 3),
                    "throttle": _round(_median(throttles), 4),
                    "throttle_min": _round(throttle_minimum, 4),
                    "brake": _round(brake_peak, 4),
                    "brake_mean": _round(_mean(brakes), 4),
                    "steering_rad": _round(representative_steering, 5),
                    "steering_abs_peak_rad": _round(
                        max((abs(value) for value in steerings), default=None), 5
                    ),
                    "gear": int(round(_median(gears))) if gears else None,
                    "rpm": _round(_median(rpms), 0),
                    "slip_angle_deg": _round(signed_peak(sideslips), 3),
                    "yaw_rate_deg_s": _round(signed_peak(yaws), 3),
                    "lateral_g": _round(representative_lateral_g, 4),
                    "longitudinal_g": _round(signed_peak(longitudinal_g), 4),
                    "latitude": _round(_median(latitudes), 8),
                    "longitude": _round(_median(longitudes), 8),
                    "tire_stress_proxy": _round(
                        0.35 * stress_terms[0]
                        + 0.35 * stress_terms[1]
                        + 0.30 * stress_terms[2],
                        4,
                    ),
                    "additional_signals": additional_signals,
                    "samples": len(indices),
                }
            )
        if points:
            traces.append(
                {
                    "lap": lap.get("lap"),
                    "lap_time_s": lap.get("lap_time_s"),
                    "complete": bool(lap.get("complete")),
                    "flag_state": lap.get("flag_state"),
                    "flag_states": list(lap.get("flag_states") or []),
                    "green_fraction": lap.get("green_fraction"),
                    "caution_fraction": lap.get("caution_fraction"),
                    "pit_time_s": lap.get("pit_time_s"),
                    "pit_entry": bool(lap.get("pit_entry")),
                    "pit_exit": bool(lap.get("pit_exit")),
                    "fuel_used_gal": _path_get(lap, "fuel", "used_gal"),
                    "conditions": dict(lap.get("conditions") or {}),
                    "points": points,
                }
            )
    return {
        "schema_version": 2,
        "additional_signal_catalog": additional_catalog,
        "distance_bins_per_lap": maximum_bins,
        "sector_start_pcts": [_round(value, 6) for value in sector_start_pcts],
        "trace_count": len(traces),
        "downsampling": (
            "lap-distance bins retain representative values plus speed range, "
            "minimum throttle, peak brake, and peak absolute steering"
        ),
        "tire_stress": {
            "evidence_class": "proxy",
            "version": "controls-load-proxy-v1",
            "definition": (
                "bounded blend of brake fraction at speed, steering magnitude at "
                "speed, and lateral acceleration; it is not per-lap tread wear"
            ),
        },
        "traces": traces,
    }


def _service_events(table: TelemetryTable) -> list[dict[str, Any]]:
    times = table.get("SessionTime", default=None)
    on_pit = table.get("OnPitRoad", default=False)
    in_stall = table.get("PlayerCarInPitStall", default=False)
    active = table.get("PitstopActive", default=False)
    service_flags = table.get("PitSvFlags", default=0)
    fuel = table.get("FuelLevel", default=None)
    requested_fuel = table.get("PitSvFuel", default=None)
    tire_use = {
        tire: table.get(f"{tire}TiresUsed", default=None)
        for tire in TIRES
        if table.has(f"{tire}TiresUsed")
    }
    tire_odometers = {
        tire: table.get(f"{tire}odometer", default=None)
        for tire in TIRES
        if table.has(f"{tire}odometer")
    }
    evidence: list[bool] = []
    for index in range(table.length):
        fuel_jump = False
        if index:
            before, current = _finite(fuel[index - 1]), _finite(fuel[index])
            fuel_jump = before is not None and current is not None and current - before > 0.10
        evidence.append(
            _bool(in_stall[index]) or _bool(active[index]) or (fuel_jump and _bool(on_pit[index]))
        )
    sample_rate = _finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0
    bridge = max(1, int(sample_rate * 2.5))
    hit_indices = [index for index, value in enumerate(evidence) if value]
    if not hit_indices:
        return []
    groups: list[list[int]] = [[hit_indices[0]]]
    for index in hit_indices[1:]:
        if index - groups[-1][-1] <= bridge:
            groups[-1].append(index)
        else:
            groups.append([index])
    events = []
    for group in groups:
        start, end = group[0], group[-1]
        start_time, end_time = _finite(times[start]), _finite(times[end])
        fuel_before = _finite(fuel[max(0, start - 1)])
        lookahead = min(table.length - 1, end + max(1, int(sample_rate * 5)))
        fuel_after = _finite(fuel[lookahead])
        tire_counters: dict[str, Any] = {}
        odometer_evidence: dict[str, Any] = {}
        observed_tire_changes: list[str] = []
        for tire, values in tire_use.items():
            before_count = _finite(values[max(0, start - 1)])
            after_count = _finite(values[lookahead])
            delta = (
                after_count - before_count
                if before_count is not None and after_count is not None
                else None
            )
            tire_counters[tire] = {
                "before": _round(before_count, 0),
                "after": _round(after_count, 0),
                "delta": _round(delta, 0),
            }
            if delta is not None and delta >= 1.0:
                observed_tire_changes.append(tire)
        for tire, values in tire_odometers.items():
            before_distance = _finite(values[max(0, start - 1)])
            after_distance = _finite(values[lookahead])
            reset = bool(
                before_distance is not None
                and after_distance is not None
                and after_distance < before_distance - 10.0
            )
            odometer_evidence[tire] = {
                "before_m": _round(before_distance, 1),
                "after_m": _round(after_distance, 1),
                "reset_observed": reset,
            }
            if reset and tire not in observed_tire_changes:
                observed_tire_changes.append(tire)
        flag_start = max(0, start - max(1, int(sample_rate * 2)))
        requested_fuel_values = [
            value
            for index in range(flag_start, lookahead + 1)
            if (value := _finite(requested_fuel[index])) is not None
        ]
        combined_service_flags = 0
        for raw_flag in service_flags[flag_start : lookahead + 1]:
            combined_service_flags |= int(_finite(raw_flag) or 0)
        events.append(
            {
                "start_index": start,
                "end_index": end,
                "start_time": _round(start_time),
                "end_time": _round(end_time),
                "fuel_added_l": _round(
                    max(0.0, fuel_after - fuel_before)
                    if fuel_before is not None and fuel_after is not None else None
                ),
                "requested_fuel_add_l": _round(
                    max(requested_fuel_values) if requested_fuel_values else None
                ),
                "pit_service_flags": combined_service_flags,
                "requested_service": {
                    name: bool(combined_service_flags & bit)
                    for name, bit in PIT_SERVICE_BITS.items()
                },
                "tire_use_counters": tire_counters,
                "tire_odometer_evidence": odometer_evidence,
                "tires_changed_observed": sorted(observed_tire_changes),
                "tire_change_confirmation": (
                    "counter_and_or_odometer_reset"
                    if observed_tire_changes
                    else "not_observed"
                ),
            }
        )
    return events


def _damage_repair_summary(
    table: TelemetryTable,
    laps: Sequence[dict[str, Any]],
    runs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize recorded tow, repair, and pit-state evidence.

    This analyzer deliberately does not estimate damage from speed, handling,
    incident points, or setup traces.  It also keeps its episode boundaries
    independent from ``_service_events`` so adding disruption evidence cannot
    change the established run/service segmentation contract.
    """

    times = table.get("SessionTime", default=None)
    lap_trace = table.get("Lap", default=None)
    sample_rate = (
        _finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate"))
        or 20.0
    )
    dts = _dt_series(times, sample_rate)
    sampling_resolution_s = 1.0 / max(sample_rate, 1.0)
    timer_epsilon_s = max(0.01, sampling_resolution_s * 0.25)
    completion_tolerance_s = max(0.25, sampling_resolution_s * 1.5)
    mapped_race_laps = [
        lap
        for lap in laps
        if (_finite(lap.get("lap")) or 0.0) > 0.0
        and _finite(lap.get("start_index")) is not None
        and _finite(lap.get("end_index")) is not None
    ]
    race_window_start = min(
        (int(_finite(lap.get("start_index")) or 0) for lap in mapped_race_laps),
        default=None,
    )
    race_window_end = max(
        (int(_finite(lap.get("end_index")) or 0) for lap in mapped_race_laps),
        default=None,
    )

    def resolved(
        logical_name: str,
        aliases: Sequence[str],
        *,
        default: Any = None,
    ) -> tuple[str | None, list[Any]]:
        channel, values = table.resolve(*aliases, default=default)
        return channel, values

    tow_channel, tow = resolved(
        "tow_timer", DAMAGE_REPAIR_CHANNEL_ALIASES["tow_timer"]
    )
    mandatory_channel, mandatory = resolved(
        "mandatory_repair_timer",
        DAMAGE_REPAIR_CHANNEL_ALIASES["mandatory_repair_timer"],
    )
    optional_channel, optional = resolved(
        "optional_repair_timer",
        DAMAGE_REPAIR_CHANNEL_ALIASES["optional_repair_timer"],
    )
    fast_used_channel, fast_used = resolved(
        "fast_repairs_used", DAMAGE_REPAIR_CHANNEL_ALIASES["fast_repairs_used"]
    )
    fast_available_channel, fast_available = resolved(
        "fast_repairs_available",
        DAMAGE_REPAIR_CHANNEL_ALIASES["fast_repairs_available"],
    )
    incident_channel, incident_points = resolved(
        "incident_points", DAMAGE_REPAIR_CHANNEL_ALIASES["incident_points"]
    )
    track_location_channel, track_location = table.resolve(
        "PlayerTrackSurface", default=None
    )
    speed_channel, incident_speed = table.resolve("Speed", default=None)
    yaw_channel, incident_yaw = table.resolve("YawRate", default=None)
    velocity_x_channel, incident_velocity_x = table.resolve(
        "VelocityX", default=None
    )
    velocity_y_channel, incident_velocity_y = table.resolve(
        "VelocityY", default=None
    )
    session_flags_channel, session_flags = resolved(
        "repair_required_flag", ("SessionFlags",), default=0
    )
    on_pit_channel, on_pit = resolved("pit_road", ("OnPitRoad",), default=False)
    in_stall_channel, in_stall = resolved(
        "pit_stall", ("PlayerCarInPitStall",), default=False
    )
    service_active_channel, service_active = resolved(
        "pit_service_active", ("PitstopActive",), default=False
    )
    pit_status_channel, pit_status = resolved(
        "pit_service_status", ("PlayerCarPitSvStatus",), default=None
    )
    pit_flags_channel, pit_flags = resolved(
        "pit_service_request_flags", ("PitSvFlags",), default=0
    )
    dp_fast_repair_channel, dp_fast_repair = resolved(
        "driver_fast_repair_request", ("dpFastRepair",), default=None
    )

    coverage_channels = {
        "tow_timer": tow_channel,
        "mandatory_repair_timer": mandatory_channel,
        "optional_repair_timer": optional_channel,
        "pit_road": on_pit_channel,
        "pit_stall": in_stall_channel,
        "pit_service_active": service_active_channel,
        "pit_service_status": pit_status_channel,
        "pit_service_request_flags": pit_flags_channel,
        "driver_fast_repair_request": dp_fast_repair_channel,
        "fast_repairs_used": fast_used_channel,
        "fast_repairs_available": fast_available_channel,
        "incident_points": incident_channel,
        "repair_required_flag": session_flags_channel,
    }
    channel_coverage = {
        name: {
            "status": "recorded" if channel else "unavailable",
            "channel": channel,
            "unit": table.unit(channel) if channel else None,
        }
        for name, channel in coverage_channels.items()
    }
    unavailable_measurements = [
        {
            "measurement": name,
            "reason": f"None of the expected channels were recorded: {', '.join(DAMAGE_REPAIR_CHANNEL_ALIASES[name])}.",
        }
        for name in (
            "tow_timer",
            "mandatory_repair_timer",
            "optional_repair_timer",
        )
        if coverage_channels[name] is None
    ]

    incident_events: list[dict[str, Any]] = []
    if incident_channel:
        for index in range(1, table.length):
            previous = _finite(incident_points[index - 1])
            current = _finite(incident_points[index])
            if previous is None or current is None or current <= previous:
                continue
            lap_number = _finite(lap_trace[index])
            event = {
                "event_id": f"incident-points-{len(incident_events) + 1:03d}",
                "sample_index": index,
                "session_time_s": _round(times[index]),
                "candidate_lap": int(lap_number) if lap_number is not None else None,
                "points_added": _round(current - previous, 0),
                "count_before": _round(previous, 0),
                "count_after": _round(current, 0),
                "source_channel": incident_channel,
                "damage_proof": False,
            }
            if track_location_channel and index < len(track_location):
                location = _finite(track_location[index])
                if location is not None and int(location) in TRACK_LOCATION_LABELS:
                    event["track_location"] = TRACK_LOCATION_LABELS[int(location)]
            if on_pit_channel and index < len(on_pit):
                event["on_pit_road"] = _bool(on_pit[index])
            if speed_channel and index < len(incident_speed):
                speed_m_s = _finite(incident_speed[index])
                if speed_m_s is not None:
                    event["speed_mph"] = _round(speed_m_s * 2.236936, 1)
            if yaw_channel and index < len(incident_yaw):
                yaw_rad_s = _finite(incident_yaw[index])
                if yaw_rad_s is not None:
                    event["yaw_rate_deg_s"] = _round(math.degrees(yaw_rad_s), 1)
            if (
                velocity_x_channel
                and velocity_y_channel
                and index < len(incident_velocity_x)
                and index < len(incident_velocity_y)
            ):
                slip = _vehicle_sideslip_degrees(
                    incident_velocity_x[index], incident_velocity_y[index]
                )
                if slip is not None:
                    event["slip_angle_deg"] = _round(slip, 1)
            incident_events.append(event)

    fast_increment_indices: set[int] = set()
    if fast_used_channel:
        for index in range(1, table.length):
            before = _finite(fast_used[index - 1])
            after = _finite(fast_used[index])
            if before is not None and after is not None and after > before:
                fast_increment_indices.add(index)

    state = []
    for index in range(table.length):
        tow_active = (_finite(tow[index]) or 0.0) > timer_epsilon_s
        mandatory_active = (
            (_finite(mandatory[index]) or 0.0) > timer_epsilon_s
            if mandatory_channel
            else False
        )
        optional_active = (
            (_finite(optional[index]) or 0.0) > timer_epsilon_s
            if optional_channel
            else False
        )
        repair_required_active = bool(
            session_flags_channel
            and int(_finite(session_flags[index]) or 0) & REPAIR_REQUIRED_FLAG
        )
        state.append(
            _bool(on_pit[index])
            or _bool(in_stall[index])
            or _bool(service_active[index])
            or tow_active
            or mandatory_active
            or optional_active
            or repair_required_active
            or index in fast_increment_indices
        )

    hit_indices = [index for index, active_state in enumerate(state) if active_state]
    bridge_samples = max(1, int(round(sample_rate * 1.5)))
    groups: list[list[int]] = []
    for index in hit_indices:
        if not groups or index - groups[-1][-1] - 1 > bridge_samples:
            groups.append([index])
        else:
            groups[-1].append(index)

    def state_time(values: Sequence[Any], indices: Sequence[int]) -> float:
        return sum(dts[index] for index in indices if _bool(values[index]))

    def positive_time(values: Sequence[Any], indices: Sequence[int]) -> float:
        return sum(
            dts[index]
            for index in indices
            if (_finite(values[index]) or 0.0) > timer_epsilon_s
        )

    def countdown_progress(
        values: Sequence[Any], indices: Sequence[int]
    ) -> list[tuple[int, float]]:
        """Return timer reductions observed while stall/service state is active."""

        progress: list[tuple[int, float]] = []
        for index in indices:
            if index <= 0 or not (
                _bool(in_stall[index]) or _bool(service_active[index])
            ):
                continue
            previous = _finite(values[index - 1])
            current = _finite(values[index])
            if previous is None or current is None or previous <= timer_epsilon_s:
                continue
            # Timer channels commonly reset to zero as service ends, even
            # when substantial optional repair time remains. Count only an
            # adjacent positive-to-positive reduction, or a final near-zero
            # completion inside the sampling tolerance.
            if current > timer_epsilon_s:
                reduction = previous - current
            elif previous <= completion_tolerance_s:
                reduction = previous
            else:
                continue
            if reduction > 1e-5:
                progress.append((index, reduction))
        return progress

    def timer_summary(
        channel: str | None,
        values: Sequence[Any],
        indices: Sequence[int],
        stall_indices: Sequence[int],
    ) -> dict[str, Any]:
        if channel is None:
            return {
                "status": "unavailable",
                "source_channel": None,
                "source_unit": None,
                "peak_remaining_s": None,
                "countdown_observed_s": None,
                "remaining_at_stall_exit_s": None,
                "timer_positive_observed_s": None,
                "countdown_progress_elapsed_s": None,
                "repair_work_completed_s": None,
                "completion_status": "unavailable",
                "countdown_progress_sample_count": 0,
                "countdown_progress_first_sample_index": None,
                "countdown_progress_last_sample_index": None,
                "_progress_indices": [],
            }
        positive = [
            (index, number)
            for index in indices
            if (number := _finite(values[index])) is not None
            and number > timer_epsilon_s
        ]
        raw_stall_exit_value = (
            _finite(values[stall_indices[-1]]) if stall_indices else None
        )
        if not positive:
            return {
                "status": "recorded_zero",
                "source_channel": channel,
                "source_unit": table.unit(channel) or "s",
                "peak_remaining_s": 0.0,
                "first_positive_remaining_s": None,
                "last_positive_remaining_s": None,
                "minimum_positive_remaining_s": None,
                "countdown_observed_s": 0.0,
                "remaining_at_stall_exit_s": _round(raw_stall_exit_value),
                "timer_positive_observed_s": 0.0,
                "countdown_progress_elapsed_s": 0.0,
                "repair_work_completed_s": 0.0,
                "completion_status": "not_active",
                "countdown_progress_sample_count": 0,
                "countdown_progress_first_sample_index": None,
                "countdown_progress_last_sample_index": None,
                "_progress_indices": [],
            }
        first_index, first_value = positive[0]
        last_index, last_value = positive[-1]
        peak_value = max(value for _, value in positive)
        minimum_value = min(value for _, value in positive)
        progress = countdown_progress(values, indices)
        repair_work_completed_s = sum(reduction for _, reduction in progress)
        stall_positive = [
            number
            for index in stall_indices
            if (number := _finite(values[index])) is not None
            and number > timer_epsilon_s
        ]
        last_positive_in_stall = stall_positive[-1] if stall_positive else None
        if (
            (raw_stall_exit_value is None or raw_stall_exit_value <= timer_epsilon_s)
            and last_positive_in_stall is not None
            and last_positive_in_stall > completion_tolerance_s
        ):
            # Preserve the last positive value before an abrupt SDK reset; the
            # reset itself is not evidence that those repairs were served.
            stall_exit_value = last_positive_in_stall
        else:
            stall_exit_value = raw_stall_exit_value
        later_zero_observed = any(
            (_finite(values[index]) or 0.0) <= timer_epsilon_s
            for index in range(last_index + 1, min(table.length, indices[-1] + 2))
        )
        if last_index >= table.length - 1 and last_value > timer_epsilon_s:
            completion = "active_at_recording_end"
        elif later_zero_observed and last_value <= completion_tolerance_s:
            completion = "counted_down_to_zero"
        elif stall_exit_value is not None and stall_exit_value > completion_tolerance_s:
            completion = "remaining_at_stall_exit"
        else:
            completion = "interrupted_or_unconfirmed"
        return {
            "status": "recorded_positive_timer",
            "source_channel": channel,
            "source_unit": table.unit(channel) or "s",
            "peak_remaining_s": _round(peak_value),
            "first_positive_remaining_s": _round(first_value),
            "last_positive_remaining_s": _round(last_value),
            "minimum_positive_remaining_s": _round(minimum_value),
            "countdown_observed_s": _round(repair_work_completed_s),
            "remaining_at_stall_exit_s": _round(stall_exit_value),
            "timer_positive_observed_s": _round(positive_time(values, indices)),
            "countdown_progress_elapsed_s": _round(
                sum(dts[index] for index, _ in progress)
            ),
            "repair_work_completed_s": _round(repair_work_completed_s),
            "completion_status": completion,
            "completion_note": (
                "A timer reaching zero only confirms that recorded timer countdown; "
                "it does not certify that the car was fully undamaged."
            ),
            "first_active_sample_index": first_index,
            "last_active_sample_index": last_index,
            "countdown_progress_sample_count": len(progress),
            "countdown_progress_first_sample_index": progress[0][0] if progress else None,
            "countdown_progress_last_sample_index": progress[-1][0] if progress else None,
            "_progress_indices": [index for index, _ in progress],
        }

    def tow_timer_summary(indices: Sequence[int]) -> dict[str, Any]:
        if tow_channel is None:
            return {
                "status": "unavailable",
                "source_channel": None,
                "peak_remaining_s": None,
                "last_remaining_s": None,
                "active_time_s": None,
                "completion_status": "unavailable",
            }
        positive = [
            (index, number)
            for index in indices
            if (number := _finite(tow[index])) is not None
            and number > timer_epsilon_s
        ]
        if not positive:
            return {
                "status": "recorded_zero",
                "source_channel": tow_channel,
                "source_unit": table.unit(tow_channel) or "s",
                "peak_remaining_s": 0.0,
                "first_remaining_s": None,
                "last_remaining_s": None,
                "active_time_s": 0.0,
                "completion_status": "not_active",
            }
        first_index, first_value = positive[0]
        last_index, last_value = positive[-1]
        later_zero_observed = any(
            (_finite(tow[index]) or 0.0) <= timer_epsilon_s
            for index in range(last_index + 1, min(table.length, indices[-1] + 2))
        )
        if last_index >= table.length - 1 and last_value > timer_epsilon_s:
            completion_status = "active_at_recording_end"
        elif later_zero_observed and last_value <= completion_tolerance_s:
            completion_status = "counted_down_to_zero"
        else:
            completion_status = "interrupted_or_unconfirmed"
        return {
            "status": "recorded_active",
            "source_channel": tow_channel,
            "source_unit": table.unit(tow_channel) or "s",
            "peak_remaining_s": _round(max(value for _, value in positive)),
            "first_remaining_s": _round(first_value),
            "last_remaining_s": _round(last_value),
            "active_time_s": _round(sum(dts[index] for index, _ in positive)),
            "completion_status": completion_status,
            "first_active_sample_index": first_index,
            "last_active_sample_index": last_index,
        }

    def run_context(
        start_index: int,
        end_index: int,
        affected_laps: Sequence[int],
    ) -> dict[str, Any]:
        affected_set = set(affected_laps)
        overlapping = [
            int(run.get("run_number"))
            for run in runs
            if _finite(run.get("run_number")) is not None
            and affected_set.intersection(
                int(number)
                for number in (run.get("lap_numbers") or ())
                if _finite(number) is not None
            )
        ]
        start_time = _finite(times[start_index])
        end_time = _finite(times[end_index])
        before_candidates = [
            run
            for run in runs
            if _finite(run.get("run_number")) is not None
            and _finite(run.get("end_time_s")) is not None
            and start_time is not None
            and (_finite(run.get("end_time_s")) or 0.0) <= start_time
        ]
        after_candidates = [
            run
            for run in runs
            if _finite(run.get("run_number")) is not None
            and _finite(run.get("start_time_s")) is not None
            and end_time is not None
            and (_finite(run.get("start_time_s")) or 0.0) >= end_time
        ]
        before_run = (
            int(max(before_candidates, key=lambda item: _finite(item.get("end_time_s")) or -1.0)["run_number"])
            if before_candidates
            else None
        )
        after_run = (
            int(min(after_candidates, key=lambda item: _finite(item.get("start_time_s")) or float("inf"))["run_number"])
            if after_candidates
            else None
        )
        if overlapping:
            relationship = "overlaps_recorded_run_laps"
        elif before_run is not None and after_run is not None and before_run != after_run:
            relationship = "between_runs"
        elif before_run is not None:
            relationship = "after_run"
        elif after_run is not None:
            relationship = "before_run"
        else:
            relationship = "outside_mapped_runs"
        return {
            "relationship": relationship,
            "overlapping_run_numbers": sorted(set(overlapping)),
            "preceding_run_number": before_run,
            "following_run_number": after_run,
        }

    episodes: list[dict[str, Any]] = []
    previous_evidence_episode_end_index = -1
    for group in groups:
        start_index, end_index = group[0], group[-1]
        indices = list(range(start_index, end_index + 1))
        stall_indices = [index for index in indices if _bool(in_stall[index])]
        tow_indices = [
            index
            for index in indices
            if (_finite(tow[index]) or 0.0) > timer_epsilon_s
        ]
        mandatory_timer = timer_summary(
            mandatory_channel, mandatory, indices, stall_indices
        )
        optional_timer = timer_summary(
            optional_channel, optional, indices, stall_indices
        )
        tow_timer = tow_timer_summary(indices)
        mandatory_progress_indices = mandatory_timer.pop("_progress_indices", [])
        optional_progress_indices = optional_timer.pop("_progress_indices", [])
        repair_timer_positive_indices = [
            index
            for index in indices
            if (
                (mandatory_channel and (_finite(mandatory[index]) or 0.0) > timer_epsilon_s)
                or (optional_channel and (_finite(optional[index]) or 0.0) > timer_epsilon_s)
            )
        ]
        repair_progress_indices = sorted(
            set(mandatory_progress_indices) | set(optional_progress_indices)
        )
        repair_required_indices = [
            index
            for index in indices
            if session_flags_channel
            and int(_finite(session_flags[index]) or 0) & REPAIR_REQUIRED_FLAG
        ]
        candidate_laps = sorted(
            {
                int(number)
                for index in indices
                if (number := _finite(lap_trace[index])) is not None and number >= 0
            }
        )
        affected_completed_laps = sorted(
            {
                int(number)
                for lap in laps
                if _finite(lap.get("lap")) is not None
                and int(_finite(lap.get("start_index")) or -1) <= end_index
                and int(_finite(lap.get("end_index")) or -1) >= start_index
                for number in [_finite(lap.get("lap"))]
                if number is not None
            }
        )
        context = run_context(start_index, end_index, affected_completed_laps)
        if race_window_start is not None and end_index < race_window_start:
            session_phase = "pre_race_or_grid"
        elif race_window_end is not None and start_index > race_window_end:
            session_phase = "post_race_recording"
        elif race_window_start is not None and race_window_end is not None:
            session_phase = "recorded_race_window"
        else:
            session_phase = "race_window_unavailable"
        evidence_window_start = max(0, start_index - int(round(sample_rate * 5.0)))
        evidence_window_end = min(
            table.length - 1, end_index + int(round(sample_rate * 2.0))
        )
        nearby_incidents = [
            event
            for event in incident_events
            if evidence_window_start <= int(event["sample_index"]) <= evidence_window_end
        ]
        requested_by_flags = bool(
            pit_flags_channel
            and any(
                int(_finite(pit_flags[index]) or 0) & PIT_SERVICE_BITS["fast_repair_requested"]
                for index in indices
            )
        )
        requested_by_driver_trace = bool(
            dp_fast_repair_channel
            and any((_finite(dp_fast_repair[index]) or 0.0) >= 0.5 for index in indices)
        )
        counter_start_index = max(0, start_index - 1)
        counter_end_index = min(
            table.length - 1, end_index + max(1, int(round(sample_rate * 2.0)))
        )
        fast_before = _finite(fast_used[counter_start_index]) if fast_used_channel else None
        fast_after = _finite(fast_used[counter_end_index]) if fast_used_channel else None
        fast_delta = (
            max(0.0, fast_after - fast_before)
            if fast_before is not None and fast_after is not None
            else None
        )
        fast_confirmed = fast_delta is not None and fast_delta >= 1.0
        has_tow = bool(tow_indices)
        has_mandatory = mandatory_timer["status"] == "recorded_positive_timer"
        has_optional = optional_timer["status"] == "recorded_positive_timer"
        has_repair = has_mandatory or has_optional
        has_repair_required_flag = bool(repair_required_indices)
        has_pit = any(
            _bool(on_pit[index])
            or _bool(in_stall[index])
            or _bool(service_active[index])
            for index in indices
        )
        if has_tow and has_repair:
            classification = "tow_and_recorded_repair_timer"
        elif has_tow and has_repair_required_flag:
            classification = "tow_with_recorded_repair_required_state"
        elif has_tow:
            classification = "tow_episode_damage_unproven"
        elif has_repair:
            classification = "recorded_repair_timer_episode"
        elif has_repair_required_flag:
            classification = "recorded_repair_required_state"
        elif fast_confirmed:
            classification = "confirmed_fast_repair_use"
        elif has_pit and session_phase == "pre_race_or_grid":
            classification = "pre_race_pit_state_no_recorded_damage"
        elif has_pit:
            classification = "pit_visit_no_recorded_repair"
        else:
            classification = "recorded_disruption"
        if has_repair:
            damage_evidence_status = "recorded_repair_timer_positive"
        elif has_repair_required_flag:
            damage_evidence_status = "recorded_repair_required_flag_active"
        elif fast_confirmed:
            damage_evidence_status = "confirmed_fast_repair_counter_increment"
        elif has_tow:
            damage_evidence_status = "tow_recorded_physical_damage_unproven"
        else:
            damage_evidence_status = "no_recorded_damage_evidence_in_episode"

        is_repair_or_tow_evidence = bool(
            has_tow or has_repair or has_repair_required_flag or fast_confirmed
        )
        repair_correlated_incident = None
        if is_repair_or_tow_evidence:
            candidates = [
                event
                for event in incident_events
                if previous_evidence_episode_end_index
                < int(event["sample_index"])
                <= start_index
                and _finite(event.get("candidate_lap")) is not None
                and int(_finite(event.get("candidate_lap")) or 0) > 0
            ]
            if candidates:
                repair_correlated_incident = max(
                    candidates, key=lambda item: int(item["sample_index"])
                )
        candidate_boundary_lap = (
            int(repair_correlated_incident["candidate_lap"])
            if repair_correlated_incident
            else None
        )
        correlated_end_lap = max(
            [number for number in candidate_laps if number > 0]
            + affected_completed_laps,
            default=None,
        )
        repair_correlated_laps = (
            sorted(
                {
                    int(number)
                    for lap in laps
                    if (number := _finite(lap.get("lap"))) is not None
                    and candidate_boundary_lap <= int(number) <= correlated_end_lap
                }
            )
            if candidate_boundary_lap is not None and correlated_end_lap is not None
            else []
        )

        observed_pit_status_values: list[int] = []
        if pit_status_channel:
            observed_pit_status_values = sorted(
                {
                    int(value)
                    for index in indices
                    if (value := _finite(pit_status[index])) is not None
                }
            )

        episode_id = f"damage-repair-{len(episodes) + 1:03d}"
        episode = {
            "episode_id": episode_id,
            "classification": classification,
            "start_sample_index": start_index,
            "end_sample_index": end_index,
            "start_session_time_s": _round(times[start_index]),
            "end_session_time_s": _round(times[end_index]),
            "episode_elapsed_s": _round(sum(dts[index] for index in indices)),
            "candidate_lap_numbers": candidate_laps,
            "affected_completed_lap_numbers": affected_completed_laps,
            "session_phase": session_phase,
            "run_context": context,
            "timing": {
                "pit_road_time_s": _round(state_time(on_pit, indices)) if on_pit_channel else None,
                "pit_stall_time_s": _round(state_time(in_stall, indices)) if in_stall_channel else None,
                "pitstop_service_active_time_s": (
                    _round(state_time(service_active, indices))
                    if service_active_channel
                    else None
                ),
                "tow_active_time_s": tow_timer.get("active_time_s"),
                "tow_timer_peak_s": tow_timer.get("peak_remaining_s"),
                "repair_active_time_s": (
                    _round(sum(dts[index] for index in repair_progress_indices))
                    if mandatory_channel or optional_channel
                    else None
                ),
                "repair_timer_positive_observed_s": (
                    _round(sum(dts[index] for index in repair_timer_positive_indices))
                    if mandatory_channel or optional_channel
                    else None
                ),
                "repair_work_completed_s": (
                    _round(
                        (_finite(mandatory_timer.get("repair_work_completed_s")) or 0.0)
                        + (_finite(optional_timer.get("repair_work_completed_s")) or 0.0)
                    )
                    if mandatory_channel or optional_channel
                    else None
                ),
                "repair_required_flag_active_time_s": (
                    _round(sum(dts[index] for index in repair_required_indices))
                    if session_flags_channel
                    else None
                ),
                "nonexclusive_note": (
                    "Pit-road, stall, pit-service, and timer-countdown durations overlap; "
                    "stall time is not attributed exclusively to repairs. Repair-active "
                    "time counts sampled intervals where a recorded repair timer decreased."
                ),
            },
            "tow": tow_timer,
            "mandatory_repair": mandatory_timer,
            "optional_repair": optional_timer,
            "repair_required_state": {
                "status": (
                    "recorded_active"
                    if has_repair_required_flag
                    else "recorded_inactive"
                    if session_flags_channel
                    else "unavailable"
                ),
                "active_time_s": (
                    _round(sum(dts[index] for index in repair_required_indices))
                    if session_flags_channel
                    else None
                ),
                "source_channel": session_flags_channel,
                "source_bit_hex": "0x00100000",
                "interpretation": (
                    "Recorded iRacing repair-required state; it does not identify a "
                    "damaged component or quantify severity."
                ),
            },
            "pit_service_status": {
                "source_channel": pit_status_channel,
                "observed": [
                    {
                        "value": value,
                        "label": PIT_SERVICE_STATUS_LABELS.get(value, "unknown"),
                    }
                    for value in observed_pit_status_values
                ] if pit_status_channel else [],
                "known_error_values": [
                    value
                    for value in observed_pit_status_values
                    if value >= 100
                ] if pit_status_channel else [],
                "unavailable_reason": (
                    None
                    if pit_status_channel
                    else "PlayerCarPitSvStatus was not recorded."
                ),
            },
            "fast_repair": {
                "requested": requested_by_flags or requested_by_driver_trace,
                "request_confirmed_as_use": fast_confirmed,
                "requested_by_pit_service_flags": requested_by_flags,
                "requested_by_driver_trace": requested_by_driver_trace,
                "request_source_channels": [
                    channel
                    for channel in (pit_flags_channel, dp_fast_repair_channel)
                    if channel
                ],
                "used_counter_channel": fast_used_channel,
                "used_count_before": _round(fast_before, 0),
                "used_count_after": _round(fast_after, 0),
                "used_count_delta": _round(fast_delta, 0),
                "available_counter_channel": fast_available_channel,
                "available_at_episode_start": (
                    _round(fast_available[start_index], 0)
                    if fast_available_channel
                    else None
                ),
                "available_at_episode_end": (
                    _round(fast_available[end_index], 0)
                    if fast_available_channel
                    else None
                ),
                "confirmation_rule": (
                    "A request flag is not proof of use; only a recorded used-counter "
                    "increment confirms fast-repair use."
                ),
            },
            "incident_points_context": {
                "events_near_episode": [event["event_id"] for event in nearby_incidents],
                "points_added_near_episode": _round(
                    sum(_finite(event.get("points_added")) or 0.0 for event in nearby_incidents),
                    0,
                ),
                "damage_proof": False,
                "note": "Incident points are context only and do not establish physical damage.",
                "repair_correlated_candidate": {
                    "status": (
                        "inferred_candidate_boundary"
                        if repair_correlated_incident
                        else "unavailable"
                    ),
                    "incident_event_id": (
                        repair_correlated_incident.get("event_id")
                        if repair_correlated_incident
                        else None
                    ),
                    "candidate_start_lap": candidate_boundary_lap,
                    "candidate_through_lap": correlated_end_lap,
                    "candidate_lap_numbers": repair_correlated_laps,
                    "damage_onset_confirmed": False,
                    "note": (
                        "Latest preceding incident-count increase since the prior "
                        "repair/tow episode; this is a conservative candidate boundary, "
                        "not confirmed damage onset."
                    ),
                },
            },
            "damage_evidence": {
                "status": damage_evidence_status,
                "severity": None,
                "location": None,
                "note": (
                    "Recorded timers/use can establish tow or repair activity, but do not "
                    "measure damage location or severity."
                ),
            },
            "source_channels": sorted(
                {
                    channel
                    for channel in (
                        tow_channel,
                        mandatory_channel,
                        optional_channel,
                        on_pit_channel,
                        in_stall_channel,
                        service_active_channel,
                        pit_status_channel,
                        pit_flags_channel,
                        dp_fast_repair_channel,
                        fast_used_channel,
                        fast_available_channel,
                        incident_channel,
                        session_flags_channel,
                    )
                    if channel
                }
            ),
        }
        episodes.append(episode)
        if is_repair_or_tow_evidence:
            previous_evidence_episode_end_index = end_index

    lap_impacts: list[dict[str, Any]] = []
    for lap in laps:
        lap_number_value = _finite(lap.get("lap"))
        if lap_number_value is None:
            continue
        lap_number = int(lap_number_value)
        related = [
            episode
            for episode in episodes
            if lap_number in episode["affected_completed_lap_numbers"]
            or lap_number in episode["candidate_lap_numbers"]
            or lap_number
            in episode["incident_points_context"]["repair_correlated_candidate"].get(
                "candidate_lap_numbers", ()
            )
        ]
        if not related:
            continue
        overlaps_episode = any(
            lap_number in episode["affected_completed_lap_numbers"]
            or lap_number in episode["candidate_lap_numbers"]
            for episode in related
        )
        correlated_candidate = any(
            lap_number
            in episode["incident_points_context"]["repair_correlated_candidate"].get(
                "candidate_lap_numbers", ()
            )
            for episode in related
        )
        reason_codes = []
        if overlaps_episode:
            reason_codes.append("pit_tow_or_repair_episode_overlap")
        if correlated_candidate:
            reason_codes.append("repair_correlated_candidate")
        if any("tow" in str(episode["classification"]) for episode in related):
            reason_codes.append("tow_state_recorded")
        if any(
            episode["damage_evidence"]["status"]
            in {
                "recorded_repair_timer_positive",
                "recorded_repair_required_flag_active",
                "confirmed_fast_repair_counter_increment",
            }
            for episode in related
        ):
            reason_codes.append("recorded_repair_evidence")
        impact = {
            "lap": lap_number,
            "episode_ids": [episode["episode_id"] for episode in related],
            "automatic_coaching_reference_eligible": False,
            "exclusion_reason_codes": sorted(set(reason_codes)),
            "note": (
                "Excluded from automatic coaching/reference because the lap overlaps "
                "a recorded episode or lies inside an inferred incident-to-repair "
                "candidate window; the candidate boundary is not confirmed damage onset."
            ),
        }
        lap_impacts.append(impact)
        lap["damage_repair_context"] = dict(impact)

    impact_by_lap = {int(item["lap"]): item for item in lap_impacts}
    run_impacts: list[dict[str, Any]] = []
    for run in runs:
        run_number = int(_finite(run.get("run_number")) or 0)
        original_candidates = [
            int(number)
            for number in (run.get("valid_green_lap_numbers") or ())
            if _finite(number) is not None
        ]
        excluded_laps = sorted(number for number in original_candidates if number in impact_by_lap)
        prior_episodes = [
            episode
            for episode in episodes
            if episode["run_context"].get("following_run_number") == run_number
        ]
        overlapping_episodes = [
            episode
            for episode in episodes
            if run_number in episode["run_context"].get("overlapping_run_numbers", ())
        ]
        optional_remaining = max(
            (
                _finite(episode["optional_repair"].get("remaining_at_stall_exit_s"))
                or 0.0
                for episode in prior_episodes
            ),
            default=0.0,
        )
        mandatory_remaining = max(
            (
                _finite(episode["mandatory_repair"].get("remaining_at_stall_exit_s"))
                or 0.0
                for episode in prior_episodes
            ),
            default=0.0,
        )
        prior_recorded_repair = any(
            episode["damage_evidence"]["status"]
            in {
                "recorded_repair_timer_positive",
                "recorded_repair_required_flag_active",
                "confirmed_fast_repair_counter_increment",
            }
            for episode in prior_episodes
        )
        prior_tow = any("tow" in str(episode["classification"]) for episode in prior_episodes)
        overlapping_evidence_episodes = [
            episode
            for episode in overlapping_episodes
            if episode["damage_evidence"]["status"]
            != "no_recorded_damage_evidence_in_episode"
            or "tow" in str(episode["classification"])
        ]
        overlap_without_candidate_boundary = any(
            episode["incident_points_context"]["repair_correlated_candidate"].get(
                "status"
            )
            != "inferred_candidate_boundary"
            for episode in overlapping_evidence_episodes
        )
        reason_codes: list[str] = []
        if optional_remaining > completion_tolerance_s:
            reason_codes.append("optional_repair_remaining_at_prior_stall_exit")
        if mandatory_remaining > completion_tolerance_s:
            reason_codes.append("mandatory_repair_remaining_at_prior_stall_exit")
        if overlap_without_candidate_boundary:
            reason_codes.append("run_overlaps_disruption_without_candidate_boundary")
        if excluded_laps:
            reason_codes.append("repair_correlated_or_episode_laps_excluded")
        if not reason_codes and (prior_recorded_repair or prior_tow):
            reason_codes.append("manual_review_after_tow_or_repair")
        if optional_remaining > completion_tolerance_s or mandatory_remaining > completion_tolerance_s:
            reference_status = "excluded_recorded_repair_remaining"
            automatic_eligible = False
        elif overlap_without_candidate_boundary or prior_recorded_repair or prior_tow:
            reference_status = "manual_review_required_after_recorded_disruption"
            automatic_eligible = False
        else:
            reference_status = (
                "eligible_with_candidate_laps_excluded"
                if excluded_laps
                else "no_recorded_damage_disruption_exclusion"
            )
            automatic_eligible = True
        retained_candidates = (
            [number for number in original_candidates if number not in excluded_laps]
            if automatic_eligible
            else []
        )
        if automatic_eligible and original_candidates and not retained_candidates:
            automatic_eligible = False
            reference_status = "excluded_all_candidates_by_recorded_disruption_window"
            reason_codes.append("all_candidate_laps_excluded")
        impact = {
            "run_number": run_number,
            "automatic_coaching_reference_eligible": automatic_eligible,
            "status": reference_status,
            "reason_codes": sorted(set(reason_codes)),
            "related_episode_ids": sorted(
                {
                    episode["episode_id"]
                    for episode in prior_episodes + overlapping_episodes
                }
            ),
            "candidate_lap_numbers_before_damage_filter": original_candidates,
            "episode_overlap_excluded_lap_numbers": excluded_laps,
            "coaching_reference_lap_numbers": retained_candidates,
            "post_stop_context": {
                "prior_episode_ids": [episode["episode_id"] for episode in prior_episodes],
                "optional_repair_remaining_at_stall_exit_s": _round(optional_remaining),
                "mandatory_repair_remaining_at_stall_exit_s": _round(mandatory_remaining),
                "recorded_repair_before_run": prior_recorded_repair,
                "tow_before_run": prior_tow,
            },
            "scope_note": (
                "This screens only recorded pit/tow/repair evidence and never certifies "
                "that the car was fully undamaged; all normal clean-lap filters still apply."
            ),
        }
        run_impacts.append(impact)
        run["damage_repair_context"] = impact
        run["coaching_reference_lap_numbers"] = retained_candidates
        for lap in laps:
            number = _finite(lap.get("lap"))
            if number is None or int(number) not in set(run.get("lap_numbers") or ()):
                continue
            existing = lap.get("damage_repair_context") or {
                "lap": int(number),
                "episode_ids": [],
                "exclusion_reason_codes": [],
            }
            lap["damage_repair_context"] = {
                **existing,
                "automatic_coaching_reference_eligible": (
                    automatic_eligible and int(number) not in excluded_laps
                ),
                "run_reference_status": reference_status,
            }

    race_window_episodes = [
        episode
        for episode in episodes
        if episode.get("session_phase") == "recorded_race_window"
    ]
    pre_race_episodes = [
        episode
        for episode in episodes
        if episode.get("session_phase") == "pre_race_or_grid"
    ]
    repair_episodes = [
        episode
        for episode in race_window_episodes
        if episode["damage_evidence"]["status"]
        in {
            "recorded_repair_timer_positive",
            "recorded_repair_required_flag_active",
            "confirmed_fast_repair_counter_increment",
        }
    ]
    tow_episodes = [
        episode
        for episode in race_window_episodes
        if "tow" in str(episode["classification"])
    ]
    critical_recorded = sum(
        channel_coverage[name]["status"] == "recorded"
        for name in (
            "tow_timer",
            "mandatory_repair_timer",
            "optional_repair_timer",
            "pit_road",
            "pit_stall",
            "pit_service_active",
        )
    )
    status = "usable" if critical_recorded == 6 else "partial" if critical_recorded else "unavailable"
    incident_start = next(
        (number for value in incident_points if (number := _finite(value)) is not None),
        None,
    ) if incident_channel else None
    incident_end = next(
        (number for value in reversed(incident_points) if (number := _finite(value)) is not None),
        None,
    ) if incident_channel else None
    return {
        "schema_version": 1,
        "status": status,
        "time_basis": "sampled SessionTime; durations are sums of routine-analysis sample intervals",
        "sampling_resolution_s": _round(sampling_resolution_s, 4),
        "channel_coverage": channel_coverage,
        "summary": {
            "episodes": len(episodes),
            "race_window_episodes": len(race_window_episodes),
            "pre_race_or_grid_episodes": len(pre_race_episodes),
            "pit_road_episodes": sum(
                1
                for episode in race_window_episodes
                if (_finite(episode["timing"].get("pit_road_time_s")) or 0.0) > 0.0
            ),
            "tow_episodes": len(tow_episodes),
            "recorded_repair_episodes": len(repair_episodes),
            "repair_required_flag_episodes": sum(
                episode["repair_required_state"]["status"] == "recorded_active"
                for episode in race_window_episodes
            ),
            "mandatory_repair_episodes": sum(
                episode["mandatory_repair"]["status"] == "recorded_positive_timer"
                for episode in race_window_episodes
            ),
            "optional_repair_episodes": sum(
                episode["optional_repair"]["status"] == "recorded_positive_timer"
                for episode in race_window_episodes
            ),
            "confirmed_fast_repair_uses": sum(
                int(_finite(episode["fast_repair"].get("used_count_delta")) or 0.0)
                for episode in race_window_episodes
            ),
            "total_pit_road_time_s": _round(
                sum(_finite(episode["timing"].get("pit_road_time_s")) or 0.0 for episode in race_window_episodes)
            ) if on_pit_channel else None,
            "total_pit_stall_time_s": _round(
                sum(_finite(episode["timing"].get("pit_stall_time_s")) or 0.0 for episode in race_window_episodes)
            ) if in_stall_channel else None,
            "total_pitstop_service_active_time_s": _round(
                sum(_finite(episode["timing"].get("pitstop_service_active_time_s")) or 0.0 for episode in race_window_episodes)
            ) if service_active_channel else None,
            "total_tow_active_time_s": _round(
                sum(_finite(episode["timing"].get("tow_active_time_s")) or 0.0 for episode in race_window_episodes)
            ) if tow_channel else None,
            "total_repair_active_time_s": _round(
                sum(_finite(episode["timing"].get("repair_active_time_s")) or 0.0 for episode in race_window_episodes)
            ) if mandatory_channel or optional_channel else None,
            "total_repair_timer_positive_observed_s": _round(
                sum(
                    _finite(episode["timing"].get("repair_timer_positive_observed_s"))
                    or 0.0
                    for episode in race_window_episodes
                )
            ) if mandatory_channel or optional_channel else None,
            "total_repair_work_completed_s": _round(
                sum(
                    _finite(episode["timing"].get("repair_work_completed_s"))
                    or 0.0
                    for episode in race_window_episodes
                )
            ) if mandatory_channel or optional_channel else None,
            "total_repair_required_flag_active_time_s": _round(
                sum(
                    _finite(episode["timing"].get("repair_required_flag_active_time_s"))
                    or 0.0
                    for episode in race_window_episodes
                )
            ) if session_flags_channel else None,
            "all_recording_pit_road_time_s": _round(
                sum(
                    _finite(episode["timing"].get("pit_road_time_s")) or 0.0
                    for episode in episodes
                )
            ) if on_pit_channel else None,
            "pre_race_or_grid_pit_road_time_s": _round(
                sum(
                    _finite(episode["timing"].get("pit_road_time_s")) or 0.0
                    for episode in pre_race_episodes
                )
            ) if on_pit_channel else None,
            "totals_scope_note": (
                "Unsuffixed totals cover recorded-race-window episodes only; "
                "pre-race/grid pit state is reported separately and is not race pit loss."
            ),
        },
        "incident_points": {
            "status": "recorded_context_only" if incident_channel else "unavailable",
            "source_channel": incident_channel,
            "start_count": _round(incident_start, 0),
            "end_count": _round(incident_end, 0),
            "positive_delta": _round(
                sum(_finite(event.get("points_added")) or 0.0 for event in incident_events),
                0,
            ) if incident_channel else None,
            "events": incident_events,
            "damage_proof": False,
            "note": "Incident points are never treated as proof of physical damage.",
        },
        "episodes": episodes,
        "lap_impacts": lap_impacts,
        "run_impacts": run_impacts,
        "unavailable_measurements": unavailable_measurements,
        "limitations": [
            "No iRacing telemetry channel used here reports damage location or severity.",
            "Pace loss, handling change, and incident points are never used to infer physical damage.",
            "Pit-road, stall, service-active, and repair-countdown time overlap; stall time is not repair-exclusive time loss.",
            "A positive repair timer is repair demand/availability, not proof that repair work was progressing; repair-active time uses intervals where the timer decreased.",
            "A repair timer reaching zero does not certify that the car was fully undamaged.",
            "Tow time confirms a tow/reset state but does not by itself prove physical damage.",
            "Routine analysis is downsampled; use a bounded native-rate query for exact transition timing when needed.",
        ],
    }


def _tire_channels(table: TelemetryTable) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for tire in TIRES:
        for position in POSITIONS:
            aliases = (
                f"{tire}wear{position}", f"{tire}Wear{position}",
                f"{tire}tireWear{position}", f"{tire}TireWear{position}",
            )
            if table.has(*aliases):
                result[f"{tire}_{position}"] = table.get(*aliases, default=None)
        for position in ("CL", "CM", "CR"):
            aliases = (
                f"{tire}temp{position}", f"{tire}Temp{position}",
                f"{tire}tireTemp{position}", f"{tire}TireTemp{position}",
            )
            if table.has(*aliases):
                result[f"{tire}_{position}_temp"] = table.get(*aliases, default=None)
        for position in POSITIONS:
            aliases = _tire_temperature_aliases(tire, position)
            if table.has(*aliases):
                result[f"{tire}_{position}_surface_temp"] = table.get(
                    *aliases, default=None
                )
    return result


def _has_measured_tire_wear(table: TelemetryTable) -> bool:
    """Return true only for actual wear channels, not temperature/pressure."""

    return any(
        table.has(
            f"{tire}wear{position}",
            f"{tire}Wear{position}",
            f"{tire}tireWear{position}",
            f"{tire}TireWear{position}",
        )
        for tire in TIRES
        for position in POSITIONS
    )


def _tire_observation(table: TelemetryTable, index: int) -> dict[str, Any] | None:
    channels = _tire_channels(table)
    if not channels:
        return None
    observation: dict[str, Any] = {"sample_index": index, "tires": {}}
    measured_wear_points = 0
    for tire in TIRES:
        wear_values = []
        wear_by_position: dict[str, float] = {}
        for position in POSITIONS:
            channel = channels.get(f"{tire}_{position}")
            value = _fraction(channel[index]) if channel and index < len(channel) else None
            if value is not None:
                remaining = value * 100.0
                wear_by_position[position] = round(remaining, 2)
                wear_values.append(remaining)
                measured_wear_points += 1
        temps: dict[str, float] = {}
        for position in ("CL", "CM", "CR"):
            channel_name, channel = table.resolve(
                f"{tire}temp{position}", f"{tire}Temp{position}",
                f"{tire}tireTemp{position}", f"{tire}TireTemp{position}",
                default=None,
            )
            value = _finite(channel[index]) if channel and index < len(channel) else None
            if value is not None:
                temps[position] = round(
                    _convert_setup_value(value, "temperature", table.unit(channel_name)), 1
                )
        surface_temps: dict[str, float] = {}
        for position in POSITIONS:
            channel_name, channel = table.resolve(
                *_tire_temperature_aliases(tire, position), default=None
            )
            value = _finite(channel[index]) if channel and index < len(channel) else None
            if value is not None:
                surface_temps[position] = round(
                    _convert_setup_value(value, "temperature", table.unit(channel_name)), 1
                )
        pressure_kind: str | None = None
        pressure_name, pressure_channel = table.resolve(
            *_tire_pressure_aliases(tire), default=None
        )
        if pressure_name:
            pressure_kind = "live"
        else:
            pressure_name, pressure_channel = table.resolve(
                *_tire_pressure_aliases(tire, cold=True), default=None
            )
            if pressure_name:
                pressure_kind = "cold"
        pressure_raw = (
            _finite(pressure_channel[index])
            if pressure_name and index < len(pressure_channel)
            else None
        )
        pressure_psi = _convert_setup_value(
            pressure_raw, "pressure", table.unit(pressure_name)
        )
        if wear_values:
            minimum_band = min(wear_by_position, key=wear_by_position.get)
            observation["tires"][tire] = {
                "remaining_percent": wear_by_position or None,
                "average_remaining_percent": _round(_mean(wear_values), 2),
                "minimum_remaining_percent": _round(wear_by_position[minimum_band], 2),
                "most_worn_band": minimum_band,
                "temperature_f": temps or None,
                "carcass_temperature_f": temps or None,
                "surface_temperature_f": surface_temps or None,
                "pressure": {
                    "kind": pressure_kind,
                    "channel": pressure_name,
                    "source_unit": table.unit(pressure_name),
                    "psi": _round(pressure_psi, 2),
                } if pressure_name else None,
            }
    # Temperature and pressure channels can update continuously and do not prove
    # that iRacing exposed a fresh, post-service tire-wear reading.  Never turn
    # those channels alone into a measured wear claim.
    if not observation["tires"] or measured_wear_points == 0:
        return None
    observation["measured_wear_points"] = measured_wear_points
    averages = {
        tire: details.get("average_remaining_percent")
        for tire, details in observation["tires"].items()
        if details.get("average_remaining_percent") is not None
    }
    if averages:
        lowest_tire = min(averages, key=averages.get)
        observation["lowest_remaining_tire"] = lowest_tire
        observation["lowest_remaining_percent"] = averages[lowest_tire]
        observation["spread_percent"] = _round(max(averages.values()) - min(averages.values()), 2)
    observation["measurement_note"] = (
        "iRacing updates tire readings during pit service; this observation belongs to the preceding run."
    )
    return observation


def _observation_wear_values(observation: Mapping[str, Any] | None) -> dict[str, float]:
    values: dict[str, float] = {}
    if not observation:
        return values
    tires = observation.get("tires")
    if not isinstance(tires, Mapping):
        return values
    for tire, details in tires.items():
        if not isinstance(details, Mapping):
            continue
        positions = details.get("remaining_percent")
        if not isinstance(positions, Mapping):
            continue
        for position, raw_value in positions.items():
            value = _finite(raw_value)
            if value is not None:
                values[f"{tire}_{position}"] = value
    return values


def _wear_reading_changed(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    minimum_change_percent: float = 0.05,
) -> bool:
    """Return true only when a pit/service event exposed a new wear reading."""

    before_values = _observation_wear_values(before)
    after_values = _observation_wear_values(after)
    common = set(before_values).intersection(after_values)
    if not common:
        return False
    return any(
        abs(after_values[key] - before_values[key]) >= minimum_change_percent
        for key in common
    )


def _runs(table: TelemetryTable, laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    race_laps = [
        lap for lap in laps
        if lap.get("complete") and (_finite(lap.get("lap")) or 0.0) > 0.0
    ]
    if not race_laps:
        race_laps = [lap for lap in laps if (_finite(lap.get("lap")) or 0.0) > 0.0]
    if not race_laps:
        return []
    events = _service_events(table)
    first_index = race_laps[0]["start_index"]
    last_index = race_laps[-1]["end_index"]
    boundaries: list[tuple[int, int, dict[str, Any] | None]] = []
    cursor = first_index
    post_race_event: dict[str, Any] | None = None
    for event in events:
        if event["end_index"] < first_index:
            continue
        if event["start_index"] >= last_index:
            post_race_event = {**event, "post_run_service": True}
            break
        if event["start_index"] <= cursor:
            cursor = max(cursor, event["end_index"] + 1)
            continue
        boundaries.append((cursor, event["start_index"] - 1, event))
        cursor = event["end_index"] + 1
    if cursor <= last_index:
        boundaries.append((cursor, last_index, post_race_event))
    if not boundaries:
        boundaries = [(first_index, last_index, post_race_event)]
    fuel = table.get("FuelLevel", default=None)
    flags = table.get("SessionFlags", default=0)
    result = []
    for start, end, ending_event in boundaries:
        run_laps = [
            lap for lap in race_laps
            if lap["start_index"] >= start and lap["end_index"] <= end
        ]
        if not run_laps:
            continue
        # A service boundary can occur before the driver completes another lap
        # (for example, a return to pit road after a short failed restart).  Such
        # a boundary remains useful for splitting the telemetry, but it is not a
        # user-facing run.  Number only the runs that contain laps so reports and
        # archived history stay contiguous.
        run_number = len(result) + 1
        green_laps = sum(
            1.0 if lap["flag_state"] == "green" else (
                0.0 if lap["flag_state"] == "caution" else lap.get("green_fraction") or 0.0
            )
            for lap in run_laps
        )
        caution_laps = sum(
            1.0 if lap["flag_state"] == "caution" else (
                0.0 if lap["flag_state"] == "green" else lap.get("caution_fraction") or 0.0
            )
            for lap in run_laps
        )
        green_exposure = sum(lap.get("green_fraction") or 0.0 for lap in run_laps)
        caution_exposure = sum(lap.get("caution_fraction") or 0.0 for lap in run_laps)
        fuel_used = sum(
            lap["fuel"]["used_l"] for lap in run_laps if lap.get("fuel", {}).get("used_l") is not None
        )
        green_fuel = [
            lap["fuel"]["used_l"] for lap in run_laps
            if lap["flag_state"] == "green" and lap.get("fuel", {}).get("used_l") is not None
        ]
        caution_fuel = [
            lap["fuel"]["used_l"] for lap in run_laps
            if lap["flag_state"] == "caution" and lap.get("fuel", {}).get("used_l") is not None
        ]
        valid_green: list[dict[str, Any]] = []
        previous_flag: str | None = None
        for lap in run_laps:
            racing_fraction = _finite(lap.get("racing_state_fraction"))
            clean_context = lap.get("clean_context") or {}
            on_track_fraction = _finite(clean_context.get("on_track_fraction"))
            traffic_fraction = _finite(
                clean_context.get("traffic_proximity_fraction")
            )
            is_restart_lap = previous_flag == "caution" and lap["flag_state"] == "green"
            if (
                lap["flag_state"] == "green"
                and lap["complete"]
                and (lap.get("pit_time_s") or 0.0) < 1.0
                and (racing_fraction is None or racing_fraction >= 0.98)
                and (on_track_fraction is None or on_track_fraction >= 0.98)
                and (traffic_fraction is None or traffic_fraction < 0.10)
                and not is_restart_lap
            ):
                valid_green.append(lap)
            previous_flag = str(lap.get("flag_state") or "")
        pace_points = [
            (float(position), float(lap["lap_time_s"]))
            for position, lap in enumerate(valid_green, 1)
            if _finite(lap.get("lap_time_s")) is not None
        ]
        slope = _linear_slope(pace_points[1:]) if len(pace_points) > 4 else _linear_slope(pace_points)
        first_third_count = max(1, len(valid_green) // 3) if valid_green else 0
        last_third_count = first_third_count
        early = valid_green[:first_third_count]
        late = valid_green[-last_third_count:] if last_third_count else []
        early_lap_time = _mean(lap["lap_time_s"] for lap in early)
        late_lap_time = _mean(lap["lap_time_s"] for lap in late)
        early_brake = _mean(lap["controls"]["brake_energy_proxy"] for lap in early)
        late_brake = _mean(lap["controls"]["brake_energy_proxy"] for lap in late)
        early_steer = _mean(lap["controls"]["steering_work_proxy"] for lap in early)
        late_steer = _mean(lap["controls"]["steering_work_proxy"] for lap in late)
        dynamics_laps = valid_green or run_laps

        def dynamics_values(*path: str) -> list[float]:
            return [
                value
                for lap in dynamics_laps
                if (value := _finite(_path_get(lap, "vehicle_dynamics", *path))) is not None
            ]

        wheel_lock = dynamics_values("wheel_speed", "braking_wheel_lock_proxy_s")
        front_lock = dynamics_values("wheel_speed", "front_wheel_lock_proxy_s")
        rear_spin = dynamics_values("wheel_speed", "rear_wheelspin_proxy_s")
        abs_active = dynamics_values("abs", "active_s")
        yaw_p95 = dynamics_values("motion", "yaw_rate_deg_s", "abs_p95")
        tire_distance: dict[str, float] = {}
        for tire in TIRES:
            distance_values = dynamics_values("wheel_speed", "tire_distance_m", tire)
            if distance_values:
                tire_distance[tire] = sum(distance_values)

        def summed(values: Sequence[float]) -> float | None:
            return sum(values) if values else None
        observation = None
        tire_measurement_status = "unmeasured_final_run" if ending_event is None else "unavailable_at_stop"
        if ending_event is not None:
            sample_rate = _finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0
            before_index = max(
                0,
                ending_event["start_index"] - max(1, int(sample_rate * 2)),
            )
            observation_index = min(
                table.length - 1,
                ending_event["end_index"] + max(1, int(sample_rate * 3)),
            )
            before_observation = _tire_observation(table, before_index)
            candidate_observation = _tire_observation(table, observation_index)
            if candidate_observation is not None and _wear_reading_changed(
                before_observation,
                candidate_observation,
            ):
                observation = candidate_observation
                tire_measurement_status = "measured_at_stop"
            elif candidate_observation is not None:
                tire_measurement_status = "stale_or_unconfirmed_at_stop"
        start_fuel, end_fuel = _finite(fuel[start]), _finite(fuel[end])
        ended_under_caution = bool(int(_finite(flags[end]) or 0) & CAUTION_FLAGS)
        result.append(
            {
                "run_number": run_number,
                "start_lap": run_laps[0]["lap"],
                "end_lap": run_laps[-1]["lap"],
                "start_time_s": run_laps[0].get("start_time"),
                "end_time_s": run_laps[-1].get("end_time"),
                "total_laps": len(run_laps),
                "green_laps": _round(green_laps, 2),
                "caution_laps": _round(caution_laps, 2),
                "green_lap_equivalents": _round(green_exposure, 2),
                "caution_lap_equivalents": _round(caution_exposure, 2),
                "lap_numbers": [lap["lap"] for lap in run_laps],
                "valid_green_lap_numbers": [lap["lap"] for lap in valid_green],
                "valid_green_lap_rule": (
                    "complete green laps with less than 1.0 s on pit road, at least "
                    "98% racing-state exposure when SessionState exists, excluding "
                    "the first green lap immediately after a caution, off-track laps, "
                    "and laps with close traffic for at least 10% of observed time"
                ),
                "position": {
                    "start": run_laps[0].get("position", {}).get("start"),
                    "end": run_laps[-1].get("position", {}).get("end"),
                    "gained": (
                        run_laps[0].get("position", {}).get("start")
                        - run_laps[-1].get("position", {}).get("end")
                        if run_laps[0].get("position", {}).get("start") is not None
                        and run_laps[-1].get("position", {}).get("end") is not None
                        else None
                    ),
                    "class_start": run_laps[0].get("position", {}).get("class_start"),
                    "class_end": run_laps[-1].get("position", {}).get("class_end"),
                },
                "ended_with_pit_stop": bool(
                    ending_event is not None and not ending_event.get("post_run_service")
                ),
                "ended_with_post_run_service": bool(
                    ending_event is not None and ending_event.get("post_run_service")
                ),
                "ended_under_caution": ended_under_caution,
                "pit_service": ending_event,
                "fuel": {
                    "start_l": _round(start_fuel),
                    "end_l": _round(end_fuel),
                    "used_l": _round(fuel_used),
                    "used_gal": _round(fuel_used / 3.785411784),
                    "green_l_per_lap": _round(_mean(green_fuel)),
                    "green_gal_per_lap": _round((_mean(green_fuel) or 0.0) / 3.785411784) if green_fuel else None,
                    "caution_l_per_lap": _round(_mean(caution_fuel)),
                    "caution_gal_per_lap": _round((_mean(caution_fuel) or 0.0) / 3.785411784) if caution_fuel else None,
                },
                "pace": {
                    "green_laps_used": len(valid_green),
                    "early_average_lap_s": _round(early_lap_time),
                    "late_average_lap_s": _round(late_lap_time),
                    "early_to_late_delta_s": _round(
                        late_lap_time - early_lap_time
                        if early_lap_time is not None and late_lap_time is not None else None
                    ),
                    "green_lap_time_slope_s_per_lap": _round(slope, 4),
                },
                "driving_load": {
                    "early_brake_energy_proxy": _round(early_brake),
                    "late_brake_energy_proxy": _round(late_brake),
                    "early_steering_work_proxy": _round(early_steer),
                    "late_steering_work_proxy": _round(late_steer),
                    "early_brake_vs_late_percent": _round(
                        (early_brake / late_brake - 1.0) * 100.0
                        if early_brake not in (None, 0.0) and late_brake not in (None, 0.0) else None,
                        1,
                    ),
                    "early_steer_vs_late_percent": _round(
                        (early_steer / late_steer - 1.0) * 100.0
                        if early_steer not in (None, 0.0) and late_steer not in (None, 0.0) else None,
                        1,
                    ),
                },
                "vehicle_dynamics": {
                    "scope": "green complete laps when available",
                    "braking_wheel_lock_proxy_s": _round(summed(wheel_lock)),
                    "front_wheel_lock_proxy_s": _round(summed(front_lock)),
                    "rear_wheelspin_proxy_s": _round(summed(rear_spin)),
                    "abs_active_s": _round(summed(abs_active)),
                    "yaw_rate_abs_p95_deg_s_mean": _round(_mean(yaw_p95), 2),
                    "tire_distance_m": {
                        tire: _round(distance, 1)
                        for tire, distance in tire_distance.items()
                    },
                    "proxy_note": (
                        "Wheel-speed divergence is a conservative tire-load proxy; "
                        "corner-radius differences, banking, bumps, and sensor behavior can contribute."
                    ),
                },
                "tire_observation": observation,
                "tire_measurement_status": tire_measurement_status,
            }
        )
    return result


def _annotate_tire_set_lifecycle(runs: Sequence[dict[str, Any]]) -> None:
    """Attach session-local tire-set age and confirmed change evidence."""

    set_number = {tire: 1 for tire in TIRES}
    cumulative_distance = {tire: 0.0 for tire in TIRES}
    cumulative_green = {tire: 0.0 for tire in TIRES}
    cumulative_caution = {tire: 0.0 for tire in TIRES}
    heat_cycles = {tire: 0 for tire in TIRES}
    for run in runs:
        dynamics = run.get("vehicle_dynamics") or {}
        distances = dynamics.get("tire_distance_m") or {}
        observation = run.get("tire_observation") or {}
        observed_tires = observation.get("tires") or {}
        corners: dict[str, Any] = {}
        for tire in TIRES:
            cumulative_distance[tire] += _finite(distances.get(tire)) or 0.0
            cumulative_green[tire] += _finite(run.get("green_laps")) or 0.0
            cumulative_caution[tire] += _finite(run.get("caution_laps")) or 0.0
            if (_finite(run.get("green_laps")) or 0.0) > 0:
                heat_cycles[tire] += 1
            measured = observed_tires.get(tire) or {}
            corners[tire] = {
                "session_set_number": set_number[tire],
                "distance_m_on_set": _round(cumulative_distance[tire], 1),
                "green_laps_on_set": _round(cumulative_green[tire], 2),
                "caution_laps_on_set": _round(cumulative_caution[tire], 2),
                "green_run_cycles": heat_cycles[tire],
                "remaining_percent_at_run_end": measured.get(
                    "average_remaining_percent"
                ),
                "minimum_band_remaining_percent": measured.get(
                    "minimum_remaining_percent"
                ),
            }
        service = run.get("pit_service") or {}
        changed = [
            str(tire).upper()
            for tire in service.get("tires_changed_observed", ()) or ()
            if str(tire).upper() in TIRES
        ]
        run["tire_set_lifecycle"] = {
            "scope": "session-local; initial pre-session tire history is unknown",
            "corners": corners,
            "confirmed_changed_at_run_end": sorted(set(changed)),
            "confirmation": service.get("tire_change_confirmation") or "not_observed",
        }
        for tire in changed:
            set_number[tire] += 1
            cumulative_distance[tire] = 0.0
            cumulative_green[tire] = 0.0
            cumulative_caution[tire] = 0.0
            heat_cycles[tire] = 0


def _derived_mean_series(series: Sequence[Mapping[str, Any]], unit: str) -> dict[str, Any] | None:
    sources = [item for item in series if item]
    if not sources:
        return None
    length = max((len(item.get("values") or ()) for item in sources), default=0)
    values: list[float | None] = []
    for index in range(length):
        sample = [
            number
            for item in sources
            if index < len(item.get("values") or ())
            and (number := _finite(item["values"][index])) is not None
        ]
        values.append(statistics.fmean(sample) if sample else None)
    return {
        "channels": [str(item["channel"]) for item in sources if item.get("channel")],
        "source_unit": unit,
        "values": values,
        "derived": "per-sample arithmetic mean of available source channels",
    }


def _setup_channel_series(table: TelemetryTable) -> dict[str, Any]:
    platform: dict[str, Any] = {}
    for name, aliases in PLATFORM_CHANNEL_ALIASES.items():
        resolved = _resolved_setup_series(table, aliases, "distance")
        if resolved:
            platform[name] = resolved
    dynamic_rear = _derived_mean_series(
        [platform[name] for name in ("LR", "RR") if name in platform],
        "in",
    )
    if dynamic_rear:
        platform["dynamic_rear"] = dynamic_rear

    shocks: dict[str, Any] = {}
    tires: dict[str, Any] = {}
    for tire in TIRES:
        shock: dict[str, Any] = {}
        deflection = _resolved_setup_series(
            table, _shock_channel_aliases(tire, "deflection"), "distance"
        )
        velocity = _resolved_setup_series(
            table, _shock_channel_aliases(tire, "velocity"), "velocity"
        )
        if deflection:
            shock["deflection"] = deflection
        if velocity:
            shock["velocity"] = velocity
        if shock:
            shocks[tire] = shock

        tire_series: dict[str, Any] = {}
        live_pressure = _resolved_setup_series(
            table, _tire_pressure_aliases(tire), "pressure"
        )
        cold_pressure = _resolved_setup_series(
            table, _tire_pressure_aliases(tire, cold=True), "pressure"
        )
        if live_pressure:
            tire_series["live_pressure"] = live_pressure
        if cold_pressure:
            tire_series["cold_pressure"] = cold_pressure
        temperatures: dict[str, Any] = {}
        for position in ("CL", "CM", "CR"):
            resolved = _resolved_setup_series(
                table, _tire_temperature_aliases(tire, position), "temperature"
            )
            if resolved:
                temperatures[position] = resolved
        if temperatures:
            tire_series["temperatures"] = temperatures
            tire_series["carcass_average"] = _derived_mean_series(
                list(temperatures.values()), "F"
            )
        if tire_series:
            tires[tire] = tire_series
    return {"platform": platform, "shocks": shocks, "tires": tires}


def _setup_sample_indices(table: TelemetryTable) -> list[int]:
    pit = table.get("OnPitRoad", "PlayerCarInPitStall", "PitstopActive", default=False)
    flags = table.get("SessionFlags", default=0)
    has_distance = table.has("LapDistPct")
    distance = table.get("LapDistPct", default=None)
    result = []
    for index in range(table.length):
        if _bool(pit[index]):
            continue
        if int(_finite(flags[index]) or 0) & CAUTION_FLAGS:
            continue
        position = _finite(distance[index])
        if has_distance and (position is None or position < 0.0):
            continue
        result.append(index)
    return result


def _series_values(series: Mapping[str, Any], indices: Sequence[int]) -> list[float]:
    raw_values = series.get("values") or ()
    return [
        number
        for index in indices
        if index < len(raw_values)
        and (number := _finite(raw_values[index])) is not None
    ]


def _series_metadata(series: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if series.get("channel"):
        result["channel"] = series["channel"]
    if series.get("channels"):
        result["channels"] = list(series["channels"])
    if series.get("source_unit"):
        result["source_unit"] = series["source_unit"]
    if series.get("derived"):
        result["derived"] = series["derived"]
    return result


def _range_summary(
    series: Mapping[str, Any],
    indices: Sequence[int],
    unit_suffix: str,
    digits: int,
) -> dict[str, Any] | None:
    values = _series_values(series, indices)
    if not values:
        return None
    return {
        **_series_metadata(series),
        "sample_count": len(values),
        f"min_{unit_suffix}": _round(min(values), digits),
        f"p05_{unit_suffix}": _round(_percentile(values, 0.05), digits),
        f"median_{unit_suffix}": _round(_median(values), digits),
        f"max_{unit_suffix}": _round(max(values), digits),
    }


def _shock_summary(
    shock: Mapping[str, Any], indices: Sequence[int]
) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    deflection = shock.get("deflection")
    if isinstance(deflection, Mapping):
        summary = _range_summary(deflection, indices, "in", 4)
        if summary:
            summary["range_in"] = _round(
                (summary["max_in"] or 0.0) - (summary["min_in"] or 0.0), 4
            )
            result["deflection"] = summary
            result["deflection_range_in"] = summary["range_in"]
    velocity = shock.get("velocity")
    if isinstance(velocity, Mapping):
        values = [abs(value) for value in _series_values(velocity, indices)]
        if values:
            summary = {
                **_series_metadata(velocity),
                "sample_count": len(values),
                "abs_velocity_p90_in_s": _round(_percentile(values, 0.90), 3),
                "abs_velocity_p99_in_s": _round(_percentile(values, 0.99), 3),
            }
            result["velocity"] = summary
            result["abs_velocity_p90_in_s"] = summary["abs_velocity_p90_in_s"]
            result["abs_velocity_p99_in_s"] = summary["abs_velocity_p99_in_s"]
    return result or None


def _setup_telemetry(
    table: TelemetryTable, series: Mapping[str, Any]
) -> dict[str, Any]:
    indices = _setup_sample_indices(table)
    available: set[str] = set()

    def register(item: Mapping[str, Any] | None) -> None:
        if not item:
            return
        if item.get("channel"):
            available.add(str(item["channel"]))
        available.update(str(channel) for channel in item.get("channels") or ())

    platform: dict[str, Any] = {}
    platform_series = series.get("platform") or {}
    center = platform_series.get("center_front_splitter")
    if isinstance(center, Mapping):
        register(center)
        summary = _range_summary(center, indices, "in", 4)
        if summary:
            platform["center_front_splitter"] = summary
            for statistic in ("min", "p05", "median", "max"):
                platform[f"center_front_splitter_{statistic}_in"] = summary[
                    f"{statistic}_in"
                ]
    corners: dict[str, Any] = {}
    for tire in TIRES:
        item = platform_series.get(tire)
        if not isinstance(item, Mapping):
            continue
        register(item)
        summary = _range_summary(item, indices, "in", 4)
        if summary:
            corners[tire] = summary
    if corners:
        platform["corners"] = corners
    dynamic_rear = platform_series.get("dynamic_rear")
    if isinstance(dynamic_rear, Mapping):
        register(dynamic_rear)
        summary = _range_summary(dynamic_rear, indices, "in", 4)
        if summary:
            platform["dynamic_rear"] = summary
            for statistic in ("min", "p05", "median", "max"):
                platform[f"dynamic_rear_{statistic}_in"] = summary[f"{statistic}_in"]

    shocks: dict[str, Any] = {}
    for tire, shock in (series.get("shocks") or {}).items():
        if not isinstance(shock, Mapping):
            continue
        for item in shock.values():
            if isinstance(item, Mapping):
                register(item)
        summary = _shock_summary(shock, indices)
        if summary:
            shocks[str(tire)] = summary

    tires: dict[str, Any] = {}
    for tire, tire_series in (series.get("tires") or {}).items():
        if not isinstance(tire_series, Mapping):
            continue
        summary: dict[str, Any] = {}
        for pressure_name in ("live_pressure", "cold_pressure"):
            item = tire_series.get(pressure_name)
            if not isinstance(item, Mapping):
                continue
            register(item)
            pressure_summary = _range_summary(item, indices, "psi", 2)
            if pressure_summary:
                summary[pressure_name] = pressure_summary
        temperature_summaries: dict[str, Any] = {}
        for position, item in (tire_series.get("temperatures") or {}).items():
            if not isinstance(item, Mapping):
                continue
            register(item)
            temperature_summary = _range_summary(item, indices, "f", 1)
            if temperature_summary:
                temperature_summaries[str(position)] = temperature_summary
        if temperature_summaries:
            summary["temperatures"] = temperature_summaries
        carcass_average = tire_series.get("carcass_average")
        if isinstance(carcass_average, Mapping):
            average_summary = _range_summary(carcass_average, indices, "f", 1)
            if average_summary:
                summary["carcass_average"] = average_summary
        if summary:
            tires[str(tire)] = summary

    return {
        "available_channels": sorted(available),
        "sample_count": len(indices),
        "sample_scope": "non-pit, non-caution samples with valid lap distance when available",
        "platform": platform,
        "shocks": shocks,
        "tires": tires,
        "limits": [
            "Dynamic platform, shock, pressure, and temperature traces are observational correlations; telemetry cannot uniquely identify which setup parameter caused a change.",
            "Attribute setup effects only after controlled A/B runs with one change at a time and matched fuel, tires, weather, track state, line, and driver inputs.",
            "Observed ride-height minima are not universal setup targets; legal and effective clearances depend on the car, track, ruleset, and complete setup.",
            "Pressure and temperature traces do not establish tire wear; wear remains a discrete pit-service observation under the tire-wear rule.",
        ],
    }


def _track_profile(
    table: TelemetryTable,
    bins: int = 200,
    setup_series: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not table.has("LapDistPct") or table.length == 0:
        return None
    pct = table.get("LapDistPct", default=None)
    speed = table.get("Speed", default=None)
    brake = table.get("Brake", "BrakeRaw", default=None)
    throttle = table.get("Throttle", "ThrottleRaw", default=None)
    steering = table.get("SteeringWheelAngle", default=None)
    lat_accel = table.get("LatAccel", default=None)
    flags = table.get("SessionFlags", default=0)
    pit = table.get("OnPitRoad", default=False)
    latitude = table.get("Lat", default=None)
    longitude = table.get("Lon", default=None)
    fields: dict[str, list[list[float]]] = {
        "speed": [[] for _ in range(bins)],
        "brake": [[] for _ in range(bins)],
        "throttle": [[] for _ in range(bins)],
        "steering": [[] for _ in range(bins)],
        "lat_accel": [[] for _ in range(bins)],
        "lat": [[] for _ in range(bins)],
        "lon": [[] for _ in range(bins)],
    }
    setup_trace_specs: list[tuple[str, str, str, Sequence[Any]]] = []

    def add_setup_trace(kind: str, prefix: str, item: Any) -> None:
        if not isinstance(item, Mapping):
            return
        values = item.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return
        storage_key = f"setup_trace_{len(setup_trace_specs)}"
        fields[storage_key] = [[] for _ in range(bins)]
        setup_trace_specs.append((storage_key, kind, prefix, values))

    setup_series = setup_series or {}
    platform_series = setup_series.get("platform") or {}
    add_setup_trace(
        "height", "center_front_splitter", platform_series.get("center_front_splitter")
    )
    for tire in TIRES:
        add_setup_trace("height", f"{tire.lower()}_ride_height", platform_series.get(tire))
    add_setup_trace("height", "dynamic_rear", platform_series.get("dynamic_rear"))
    for tire, shock in (setup_series.get("shocks") or {}).items():
        if isinstance(shock, Mapping):
            prefix = str(tire).lower()
            add_setup_trace("shock_deflection", f"{prefix}_shock", shock.get("deflection"))
            add_setup_trace("shock_velocity", f"{prefix}_shock", shock.get("velocity"))
    for tire, tire_series in (setup_series.get("tires") or {}).items():
        if not isinstance(tire_series, Mapping):
            continue
        prefix = str(tire).lower()
        add_setup_trace("pressure", f"{prefix}_tire_pressure", tire_series.get("live_pressure"))
        for position, item in (tire_series.get("temperatures") or {}).items():
            add_setup_trace(
                "temperature",
                f"{prefix}_tire_temp_{str(position).lower()}",
                item,
            )
    for index in range(table.length):
        position = _finite(pct[index])
        if position is None or position < 0 or _bool(pit[index]):
            continue
        flag = int(_finite(flags[index]) or 0)
        if flag & CAUTION_FLAGS:
            continue
        bin_index = min(bins - 1, max(0, int((position % 1.0) * bins)))
        values = {
            "speed": (_finite(speed[index]) or 0.0) * 2.236936,
            "brake": _fraction(brake[index]),
            "throttle": _fraction(throttle[index]),
            "steering": abs(_finite(steering[index]) or 0.0),
            "lat_accel": abs(_finite(lat_accel[index]) or 0.0) / 9.80665,
            "lat": _finite(latitude[index]),
            "lon": _finite(longitude[index]),
        }
        for name, value in values.items():
            if value is not None:
                fields[name][bin_index].append(value)
        for storage_key, _kind, _prefix, setup_values in setup_trace_specs:
            if index >= len(setup_values):
                continue
            value = _finite(setup_values[index])
            if value is not None:
                fields[storage_key][bin_index].append(value)
    profile = []
    setup_trace_fields: set[str] = set()
    for index in range(bins):
        if not any(fields[name][index] for name in ("speed", "steering", "lat_accel")):
            continue
        record = {
            "bin": index,
            "lap_pct": round((index + 0.5) / bins, 5),
            "samples": len(fields["speed"][index]),
            "speed_mph": _round(_median(fields["speed"][index])),
            "brake": _round(_median(fields["brake"][index])),
            "throttle": _round(_median(fields["throttle"][index])),
            "steering_abs_rad": _round(_median(fields["steering"][index])),
            "lateral_g": _round(_median(fields["lat_accel"][index])),
            "lat": _round(_median(fields["lat"][index]), 6),
            "lon": _round(_median(fields["lon"][index]), 6),
        }
        for storage_key, kind, prefix, _setup_values in setup_trace_specs:
            values_in_bin = fields[storage_key][index]
            if not values_in_bin:
                continue
            if kind == "height":
                additions = {
                    f"{prefix}_min_in": _round(min(values_in_bin), 4),
                    f"{prefix}_p05_in": _round(_percentile(values_in_bin, 0.05), 4),
                    f"{prefix}_median_in": _round(_median(values_in_bin), 4),
                    f"{prefix}_max_in": _round(max(values_in_bin), 4),
                }
            elif kind == "shock_deflection":
                additions = {
                    f"{prefix}_deflection_min_in": _round(min(values_in_bin), 4),
                    f"{prefix}_deflection_max_in": _round(max(values_in_bin), 4),
                    f"{prefix}_deflection_range_in": _round(
                        max(values_in_bin) - min(values_in_bin), 4
                    ),
                }
            elif kind == "shock_velocity":
                absolute = [abs(value) for value in values_in_bin]
                additions = {
                    f"{prefix}_abs_velocity_p90_in_s": _round(
                        _percentile(absolute, 0.90), 3
                    ),
                    f"{prefix}_abs_velocity_p99_in_s": _round(
                        _percentile(absolute, 0.99), 3
                    ),
                }
            else:
                unit = "psi" if kind == "pressure" else "f"
                digits = 2 if kind == "pressure" else 1
                additions = {
                    f"{prefix}_min_{unit}": _round(min(values_in_bin), digits),
                    f"{prefix}_p05_{unit}": _round(
                        _percentile(values_in_bin, 0.05), digits
                    ),
                    f"{prefix}_median_{unit}": _round(_median(values_in_bin), digits),
                    f"{prefix}_max_{unit}": _round(max(values_in_bin), digits),
                }
            record.update(additions)
            setup_trace_fields.update(additions)
        profile.append(record)
    if not profile:
        return None
    steer_threshold = _percentile((item["steering_abs_rad"] for item in profile), 0.55) or 0.0
    lat_threshold = _percentile((item["lateral_g"] for item in profile), 0.55) or 0.0
    brake_threshold = max(0.04, _percentile((item["brake"] for item in profile), 0.65) or 0.04)
    active_bins = {
        item["bin"] for item in profile
        if (item["steering_abs_rad"] or 0.0) >= steer_threshold
        and ((item["lateral_g"] or 0.0) >= lat_threshold or (item["brake"] or 0.0) >= brake_threshold)
    }
    segments: list[list[int]] = []
    for bin_index in sorted(active_bins):
        if segments and bin_index == segments[-1][-1] + 1:
            segments[-1].append(bin_index)
        else:
            segments.append([bin_index])
    if len(segments) > 1 and segments[0][0] == 0 and segments[-1][-1] == bins - 1:
        segments[0] = segments[-1] + segments[0]
        segments.pop()
    segment_summaries = []
    profile_by_bin = {item["bin"]: item for item in profile}
    for raw_segment in segments:
        if len(raw_segment) < max(2, bins // 100):
            continue
        records = [profile_by_bin[index] for index in raw_segment if index in profile_by_bin]
        if not records:
            continue
        minimum = min(records, key=lambda item: item["speed_mph"] if item["speed_mph"] is not None else math.inf)
        segment_summaries.append(
            {
                "segment": len(segment_summaries) + 1,
                "start_pct": _round((raw_segment[0] % bins) / bins, 4),
                "end_pct": _round(((raw_segment[-1] + 1) % bins) / bins, 4),
                "wraps_start_finish": raw_segment[0] > raw_segment[-1],
                "minimum_speed_mph": minimum["speed_mph"],
                "minimum_speed_pct": minimum["lap_pct"],
                "median_brake": _round(_median(item["brake"] for item in records)),
                "median_steering_rad": _round(_median(item["steering_abs_rad"] for item in records)),
                "median_lateral_g": _round(_median(item["lateral_g"] for item in records)),
            }
        )
    shape = [
        {"lap_pct": item["lap_pct"], "x": item["lon"], "y": item["lat"]}
        for item in profile if item["lat"] is not None and item["lon"] is not None
    ]
    return {
        "bins": bins,
        "profile": profile,
        "detected_corner_segments": segment_summaries,
        "shape": shape or None,
        "setup_trace_fields": sorted(setup_trace_fields),
        "segment_note": "Segments are telemetry-derived load zones; cached track references may supply official corner names.",
    }


def _corner_lap_exclusion_reasons(
    lap: Mapping[str, Any], previous_flag: str | None
) -> list[str]:
    """Return deterministic reasons a lap cannot support phase coaching."""

    reasons: list[str] = []
    flag = str(lap.get("flag_state") or "")
    if not lap.get("complete"):
        reasons.append("partial")
    if flag != "green":
        reasons.append("caution_or_mixed")
    if (_finite(lap.get("pit_time_s")) or 0.0) >= 1.0:
        reasons.append("pit")
    racing_fraction = _finite(lap.get("racing_state_fraction"))
    if racing_fraction is not None and racing_fraction < 0.98:
        reasons.append("not_racing_state")
    clean_context = lap.get("clean_context") or {}
    on_track_fraction = _finite(clean_context.get("on_track_fraction"))
    if on_track_fraction is not None and on_track_fraction < 0.98:
        reasons.append("off_track")
    traffic_fraction = _finite(clean_context.get("traffic_proximity_fraction"))
    if traffic_fraction is not None and traffic_fraction >= 0.10:
        reasons.append("close_traffic")
    if previous_flag == "caution" and flag == "green":
        reasons.append("restart")
    damage_context = lap.get("damage_repair_context")
    if isinstance(damage_context, Mapping):
        raw_reason_codes = damage_context.get("exclusion_reason_codes") or ()
        if isinstance(raw_reason_codes, str):
            raw_reason_codes = (raw_reason_codes,)
        elif not isinstance(raw_reason_codes, Sequence):
            raw_reason_codes = (raw_reason_codes,) if raw_reason_codes else ()
        reason_codes = [str(item) for item in raw_reason_codes if str(item).strip()]
        if (
            damage_context.get("automatic_coaching_reference_eligible") is False
            or reason_codes
        ):
            reasons.extend(reason_codes or ["damage_repair_context"])
    return reasons


def _initial_tire_zero_evidence(
    table: TelemetryTable,
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Confirm an initial zero-age boundary only with odometer and reset evidence."""

    first_lap = next(
        (
            lap
            for lap in sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0)
            if (_finite(lap.get("lap")) or 0.0) > 0.0
        ),
        None,
    )
    threshold_m = 250.0
    values: dict[str, float] = {}
    if first_lap is not None:
        first_index = int(first_lap.get("start_index") or 0)
        for tire in TIRES:
            channel = f"{tire}odometer"
            if not table.has(channel):
                continue
            samples = table.get(channel, default=None)
            value = _finite(samples[first_index]) if first_index < len(samples) else None
            if value is not None:
                values[tire] = value
    reset_validated = {
        tire: any(
            bool(
                ((run.get("pit_service") or {}).get("tire_odometer_evidence") or {})
                .get(tire, {})
                .get("reset_observed")
            )
            for run in runs
        )
        for tire in TIRES
    }
    near_zero = len(values) == len(TIRES) and all(
        0.0 <= value <= threshold_m for value in values.values()
    )
    resets_confirmed = all(reset_validated.values())
    confirmed = near_zero and resets_confirmed
    return {
        "status": (
            "confirmed"
            if confirmed
            else "suggestive_not_confirmed"
            if near_zero
            else "unavailable"
        ),
        "confirmed": confirmed,
        "initial_odometer_m": {tire: _round(value, 1) for tire, value in values.items()},
        "near_zero_threshold_m": threshold_m,
        "later_reset_semantics_validated": reset_validated,
        "rule": "All four tire odometers must begin near zero and later demonstrate a reset in the same recording.",
    }


def _run_tire_age_context(
    run: Mapping[str, Any],
    initial_zero_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover conservative per-corner green-lap age bounds from lifecycle data."""

    lifecycle = run.get("tire_set_lifecycle") or {}
    corners = lifecycle.get("corners") or {}
    run_green = _finite(run.get("green_laps")) or 0.0
    by_tire: dict[str, Any] = {}
    confirmed_all = True
    for tire in TIRES:
        corner = corners.get(tire) if isinstance(corners, Mapping) else None
        corner = corner if isinstance(corner, Mapping) else {}
        set_number = _finite(corner.get("session_set_number"))
        end_age = _finite(corner.get("green_laps_on_set"))
        if set_number is None or end_age is None:
            confirmed_all = False
            continue
        # Set 1 begins before the recorded session and therefore does not prove
        # zero-age tires. Set 2+ requires a telemetry-confirmed prior change.
        initial_zero_confirmed = bool((initial_zero_evidence or {}).get("confirmed"))
        confirmed_zero = set_number >= 2.0 or (
            set_number == 1.0 and initial_zero_confirmed
        )
        confirmed_all = confirmed_all and confirmed_zero
        by_tire[tire] = {
            "session_set_number": int(set_number),
            "start_green_laps_on_set": _round(max(0.0, end_age - run_green), 2),
            "end_green_laps_on_set": _round(end_age, 2),
            "zero_age_confirmed_by_prior_change": confirmed_zero,
        }
    confirmed_all = confirmed_all and len(by_tire) == len(TIRES)
    return {
        "basis": (
            "confirmed_tire_odometer_and_change_lifecycle"
            if confirmed_all
            else "session_relative_age_with_unconfirmed_initial_or_mixed_tire_sets"
        ),
        "confirmed_zero_age_for_all_corners": confirmed_all,
        "by_tire": by_tire,
        "initial_zero_evidence": dict(initial_zero_evidence or {}),
        "limitation": (
            "Green-lap age starts at a telemetry-confirmed all-corner tire change."
            if confirmed_all
            else "At least one tire lacks a confirmed zero-age boundary; early/middle/late are observational run phases, not proof of fresh or worn tires."
        ),
    }


def _phase_lap_groups(
    eligible_laps: Sequence[Mapping[str, Any]],
    run_laps: Sequence[Mapping[str, Any]],
    age_context: Mapping[str, Any],
) -> tuple[str, dict[str, list[Mapping[str, Any]]], dict[int, dict[str, Any]]]:
    """Assign clean laps to disclosed run thirds and retain tire-age bounds.

    A confirmed zero-age boundary makes the green-lap-on-set values usable, but
    it does not by itself prove where a tire becomes settled or worn.  Until a
    session-derived change-point model supports those boundaries, the temporal
    cohorts therefore remain early/middle/late observations.
    """

    eligible_numbers = {
        int(number)
        for lap in eligible_laps
        if (number := _finite(lap.get("lap"))) is not None
    }
    age_by_lap: dict[int, dict[str, Any]] = {}
    current_age = {
        tire: _finite(details.get("start_green_laps_on_set")) or 0.0
        for tire, details in (age_context.get("by_tire") or {}).items()
        if isinstance(details, Mapping)
    }
    set_numbers = {
        tire: details.get("session_set_number")
        for tire, details in (age_context.get("by_tire") or {}).items()
        if isinstance(details, Mapping)
    }
    for lap in sorted(run_laps, key=lambda item: _finite(item.get("lap")) or -1.0):
        lap_number_value = _finite(lap.get("lap"))
        if lap_number_value is None:
            continue
        lap_number = int(lap_number_value)
        green_increment = max(0.0, _finite(lap.get("green_fraction")) or 0.0)
        bounds: dict[str, Any] = {}
        for tire, start_age in current_age.items():
            end_age = start_age + green_increment
            bounds[tire] = {
                "session_set_number": set_numbers.get(tire),
                "start": _round(start_age, 2),
                "end": _round(end_age, 2),
            }
            current_age[tire] = end_age
        if lap_number in eligible_numbers:
            age_by_lap[lap_number] = bounds

    groups = {"early": [], "middle": [], "late": []}
    ordered = sorted(eligible_laps, key=lambda item: _finite(item.get("lap")) or -1.0)
    phase_names = ("early", "middle", "late")
    for index, lap in enumerate(ordered):
        phase_index = min(2, (index * 3) // max(1, len(ordered)))
        groups[phase_names[phase_index]].append(lap)
    phase_model = (
        "confirmed_age_run_thirds_proxy"
        if age_context.get("confirmed_zero_age_for_all_corners")
        else "run_thirds_proxy"
    )
    return phase_model, groups, age_by_lap


def _zone_lap_metric(
    *,
    anchor_lap: Mapping[str, Any],
    next_lap: Mapping[str, Any] | None,
    zone: Mapping[str, Any],
    series: Mapping[str, Sequence[Any]],
    dts: Sequence[float],
    require_next_lap: bool,
) -> dict[str, Any] | None:
    """Summarize one clean lap through a telemetry-derived load zone."""

    start = _finite(zone.get("start_pct"))
    end = _finite(zone.get("end_pct"))
    if start is None or end is None:
        return None
    wraps = bool(zone.get("wraps_start_finish")) or start > end
    span = (end - start) % 1.0 if wraps else end - start
    if span <= 0.0 or span >= 0.75:
        return None
    # Load zones begin where sustained corner demand becomes visible, which can
    # be after initial brake/steering input.  Keep enough pre-zone context to
    # observe those crossings without letting a whole preceding corner leak in.
    entry_margin = min(0.08, max(0.025, span * 0.40))
    exit_margin = entry_margin
    pct = series["pct"]

    indexed: list[tuple[int, bool]] = [
        (index, False)
        for index in range(
            int(anchor_lap.get("start_index") or 0),
            int(anchor_lap.get("end_index") or -1) + 1,
        )
    ]
    if wraps:
        if require_next_lap and next_lap is None:
            return None
        if next_lap is not None:
            indexed.extend(
                (index, True)
                for index in range(
                    int(next_lap.get("start_index") or 0),
                    int(next_lap.get("end_index") or -1) + 1,
                )
            )

    records: list[dict[str, Any]] = []
    for index, from_next_lap in indexed:
        position = _finite(pct[index]) if index < len(pct) else None
        if position is None or position < 0.0:
            continue
        position %= 1.0
        if wraps:
            if not from_next_lap and position < max(0.0, start - entry_margin):
                continue
            if from_next_lap and position > min(1.0, end + exit_margin):
                continue
        relative = (position - start) % 1.0
        if relative >= 1.0 - entry_margin:
            relative -= 1.0
        if relative < -entry_margin or relative > span + exit_margin:
            continue
        flags = series["flags"]
        pit = series["pit"]
        if index < len(flags) and int(_finite(flags[index]) or 0) & CAUTION_FLAGS:
            continue
        if index < len(pit) and _bool(pit[index]):
            continue
        track_location = series.get("track_location") or ()
        if track_location and index < len(track_location):
            location = _finite(track_location[index])
            if location is not None and int(location) != 3:
                continue
        session_state = series.get("session_state") or ()
        if session_state and index < len(session_state):
            state = _finite(session_state[index])
            if state is not None and int(state) != 4:
                continue
        speed_mps = _finite(series["speed"][index]) if index < len(series["speed"]) else None
        steering_value = (
            _finite(series["steering"][index])
            if index < len(series["steering"])
            else None
        )
        ahead_series = series.get("car_distance_ahead") or ()
        behind_series = series.get("car_distance_behind") or ()
        ahead = _finite(ahead_series[index]) if index < len(ahead_series) else None
        behind = _finite(behind_series[index]) if index < len(behind_series) else None
        traffic_near = (
            ahead is not None
            and ahead > 0.0
            and ahead <= max(25.0, (speed_mps or 0.0) * 0.75)
        ) or (
            behind is not None
            and behind > 0.0
            and behind <= max(15.0, (speed_mps or 0.0) * 0.40)
        )
        records.append(
            {
                "index": index,
                "relative": relative,
                "lap_pct": position,
                "speed_mps": speed_mps,
                "speed_mph": speed_mps * 2.236936 if speed_mps is not None else None,
                "brake": _fraction(series["brake"][index]) if index < len(series["brake"]) else None,
                "throttle": _fraction(series["throttle"][index]) if index < len(series["throttle"]) else None,
                "steering_rad": steering_value,
                "steering_abs_rad": abs(steering_value) if steering_value is not None else None,
                "dt": dts[index] if index < len(dts) else 0.0,
                "traffic_near": traffic_near,
            }
        )
    records.sort(key=lambda item: item["relative"])
    core = [item for item in records if 0.0 <= item["relative"] <= span]
    speed_core = [item for item in core if item["speed_mph"] is not None]
    if len(core) < 3 or not speed_core:
        return None
    if sum(bool(item.get("traffic_near")) for item in core) / len(core) >= 0.10:
        return None

    entry_record = speed_core[0]
    exit_record = speed_core[-1]
    minimum_record = min(speed_core, key=lambda item: item["speed_mph"])
    brakes = [item["brake"] for item in core if item["brake"] is not None]
    steering = [item["steering_abs_rad"] for item in core if item["steering_abs_rad"] is not None]
    steering_signed = [item["steering_rad"] for item in core if item["steering_rad"] is not None]
    brake_energy = sum(
        item["brake"] * item["speed_mps"] * item["dt"]
        for item in core
        if item["brake"] is not None and item["speed_mps"] is not None
    )
    steering_work = sum(
        item["steering_abs_rad"] * item["speed_mps"] * item["dt"]
        for item in core
        if item["steering_abs_rad"] is not None and item["speed_mps"] is not None
    )

    brake_records = [item for item in records if item["brake"] is not None]
    brake_onset_boundary_censored = bool(
        brake_records and brake_records[0]["brake"] >= 0.05
    )
    brake_onset: dict[str, Any] | None = None
    if not brake_onset_boundary_censored:
        for previous, current in zip(brake_records, brake_records[1:]):
            if previous["brake"] < 0.05 <= current["brake"] and current["relative"] <= span:
                brake_onset = current
                break
    brake_release: dict[str, Any] | None = None
    if brake_records and max(item["brake"] for item in brake_records) >= 0.05:
        peak_index = max(
            range(len(brake_records)), key=lambda index: brake_records[index]["brake"]
        )
        for current in brake_records[peak_index + 1 :]:
            if current["brake"] < 0.05:
                brake_release = current
                break

    throttle_records = [
        item
        for item in records
        if item["throttle"] is not None
        and item["relative"] >= minimum_record["relative"]
    ]
    throttle_pickup_boundary_censored = bool(
        throttle_records and throttle_records[0]["throttle"] >= 0.75
    )
    throttle_pickup: dict[str, Any] | None = None
    if not throttle_pickup_boundary_censored:
        for previous, current in zip(throttle_records, throttle_records[1:]):
            if previous["throttle"] < 0.75 <= current["throttle"]:
                throttle_pickup = current
                break

    steering_records = [item for item in records if item["steering_abs_rad"] is not None]
    baseline_records = [
        item
        for item in steering_records
        if item["relative"] <= -max(0.01, entry_margin * 0.35)
    ]
    steering_baseline = _median(item.get("steering_rad") for item in baseline_records)
    turn_threshold_rad = 0.05
    turn_records = [
        {
            **item,
            "turn_demand_rad": abs(
                (item.get("steering_rad") or 0.0) - steering_baseline
            )
            if steering_baseline is not None
            else item["steering_abs_rad"],
        }
        for item in steering_records
    ]
    turn_in_boundary_censored = bool(
        turn_records and turn_records[0]["turn_demand_rad"] >= turn_threshold_rad
    )
    turn_in: dict[str, Any] | None = None
    if not turn_in_boundary_censored:
        for previous, current in zip(turn_records, turn_records[1:]):
            if (
                previous["turn_demand_rad"] < turn_threshold_rad
                <= current["turn_demand_rad"]
                and current["relative"] <= span
            ):
                turn_in = current
                break
        if (
            turn_in is None
            and steering_records
            and (steering_records[0].get("steering_abs_rad") or 0.0) >= 0.08
            and (abs(steering_baseline) if steering_baseline is not None else 0.0)
            >= 0.08
        ):
            turn_in_boundary_censored = True

    return {
        "lap": int(_finite(anchor_lap.get("lap")) or 0),
        "sample_count": len(core),
        "entry_speed_mph": _round(entry_record["speed_mph"], 2),
        "minimum_speed_mph": _round(minimum_record["speed_mph"], 2),
        "exit_speed_mph": _round(exit_record["speed_mph"], 2),
        "brake_average_fraction": _round(_mean(brakes), 4),
        "brake_peak_fraction": _round(max(brakes) if brakes else None, 4),
        "brake_energy_proxy": _round(brake_energy, 3) if brakes else None,
        "brake_onset_lap_pct": _round(brake_onset.get("lap_pct"), 5) if brake_onset else None,
        "brake_onset_offset_lap_pct": _round(brake_onset.get("relative"), 5) if brake_onset else None,
        "brake_onset_boundary_censored": brake_onset_boundary_censored,
        "brake_release_lap_pct": _round(brake_release.get("lap_pct"), 5) if brake_release else None,
        "brake_release_offset_lap_pct": _round(brake_release.get("relative"), 5) if brake_release else None,
        "steering_average_abs_rad": _round(_mean(steering), 4),
        "steering_work_proxy": _round(steering_work, 3) if steering else None,
        "steering_corrections": _count_corrections(steering_signed),
        "turn_in_lap_pct": _round(turn_in.get("lap_pct"), 5) if turn_in else None,
        "turn_in_offset_lap_pct": _round(turn_in.get("relative"), 5) if turn_in else None,
        "turn_in_boundary_censored": turn_in_boundary_censored,
        "turn_in_steering_baseline_rad": _round(steering_baseline, 5),
        "turn_in_demand_threshold_rad": turn_threshold_rad,
        "throttle_pickup_lap_pct": _round(throttle_pickup.get("lap_pct"), 5) if throttle_pickup else None,
        "throttle_pickup_offset_lap_pct": _round(throttle_pickup.get("relative"), 5) if throttle_pickup else None,
        "throttle_pickup_boundary_censored": throttle_pickup_boundary_censored,
        "exit_throttle_fraction": _round(exit_record.get("throttle"), 4),
    }


_CORNER_PHASE_METRICS = (
    "entry_speed_mph",
    "minimum_speed_mph",
    "exit_speed_mph",
    "brake_average_fraction",
    "brake_peak_fraction",
    "brake_energy_proxy",
    "brake_onset_lap_pct",
    "brake_onset_offset_lap_pct",
    "brake_release_lap_pct",
    "brake_release_offset_lap_pct",
    "steering_average_abs_rad",
    "steering_work_proxy",
    "steering_corrections",
    "turn_in_lap_pct",
    "turn_in_offset_lap_pct",
    "throttle_pickup_lap_pct",
    "throttle_pickup_offset_lap_pct",
    "exit_throttle_fraction",
)


def _phase_green_age_bounds(
    lap_numbers: Sequence[int], age_by_lap: Mapping[int, Mapping[str, Any]]
) -> dict[str, Any]:
    by_tire: dict[str, Any] = {}
    for tire in TIRES:
        starts: list[float] = []
        ends: list[float] = []
        set_numbers: set[int] = set()
        for lap_number in lap_numbers:
            details = (age_by_lap.get(lap_number) or {}).get(tire) or {}
            if (value := _finite(details.get("start"))) is not None:
                starts.append(value)
            if (value := _finite(details.get("end"))) is not None:
                ends.append(value)
            if (value := _finite(details.get("session_set_number"))) is not None:
                set_numbers.add(int(value))
        if starts or ends:
            by_tire[tire] = {
                "session_set_numbers": sorted(set_numbers),
                "start": _round(min(starts) if starts else None, 2),
                "end": _round(max(ends) if ends else None, 2),
            }
    return {
        "unit": "green-lap equivalents on the session-local tire set",
        "by_tire": by_tire,
    }


def _phase_summary(
    phase: str,
    assigned_laps: Sequence[Mapping[str, Any]],
    lap_metrics: Sequence[Mapping[str, Any]],
    age_by_lap: Mapping[int, Mapping[str, Any]],
    *,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    assigned_numbers = [
        int(number)
        for lap in assigned_laps
        if (number := _finite(lap.get("lap"))) is not None
    ]
    included_numbers = [
        int(number)
        for metric in lap_metrics
        if (number := _finite(metric.get("lap"))) is not None
    ]
    metrics = {
        name: _round(_median(item.get(name) for item in lap_metrics), 5)
        for name in _CORNER_PHASE_METRICS
    }
    metric_observation_counts = {
        name: sum(_finite(item.get(name)) is not None for item in lap_metrics)
        for name in _CORNER_PHASE_METRICS
    }
    event_counts = {
        "brake_onset_laps": sum(
            item.get("brake_onset_lap_pct") is not None for item in lap_metrics
        ),
        "brake_onset_boundary_censored_laps": sum(
            bool(item.get("brake_onset_boundary_censored")) for item in lap_metrics
        ),
        "brake_release_laps": sum(
            item.get("brake_release_lap_pct") is not None for item in lap_metrics
        ),
        "throttle_pickup_laps": sum(
            item.get("throttle_pickup_lap_pct") is not None for item in lap_metrics
        ),
        "throttle_pickup_boundary_censored_laps": sum(
            bool(item.get("throttle_pickup_boundary_censored")) for item in lap_metrics
        ),
        "turn_in_laps": sum(
            item.get("turn_in_lap_pct") is not None for item in lap_metrics
        ),
        "turn_in_boundary_censored_laps": sum(
            bool(item.get("turn_in_boundary_censored")) for item in lap_metrics
        ),
    }
    if unavailable_reason is not None:
        status = "unavailable"
    elif len(lap_metrics) >= 2:
        status = "usable"
    elif lap_metrics:
        status = "limited"
    else:
        status = "unavailable"
        unavailable_reason = "No eligible lap supplied enough samples in this load zone."
    return {
        "phase": phase,
        "status": status,
        "reason": unavailable_reason,
        "assigned_lap_count": len(assigned_numbers),
        "lap_count": len(included_numbers),
        "sample_count": sum(int(item.get("sample_count") or 0) for item in lap_metrics),
        "lap_numbers": included_numbers,
        "green_lap_on_set_bounds": _phase_green_age_bounds(
            included_numbers or assigned_numbers, age_by_lap
        ),
        "event_availability": event_counts,
        "metric_observation_counts": metric_observation_counts,
        "metrics": metrics,
    }


def _phase_comparison(
    baseline: Mapping[str, Any], comparison: Mapping[str, Any]
) -> dict[str, Any]:
    baseline_metrics = baseline.get("metrics") or {}
    comparison_metrics = comparison.get("metrics") or {}
    baseline_counts = baseline.get("metric_observation_counts") or {}
    comparison_counts = comparison.get("metric_observation_counts") or {}
    status = (
        "usable"
        if baseline.get("status") == "usable" and comparison.get("status") == "usable"
        else "limited"
        if baseline.get("status") in {"usable", "limited"}
        and comparison.get("status") in {"usable", "limited"}
        else "unavailable"
    )
    additive = (
        "entry_speed_mph",
        "minimum_speed_mph",
        "exit_speed_mph",
        "brake_average_fraction",
        "brake_peak_fraction",
        "brake_onset_offset_lap_pct",
        "brake_release_offset_lap_pct",
        "steering_average_abs_rad",
        "steering_corrections",
        "turn_in_offset_lap_pct",
        "throttle_pickup_offset_lap_pct",
        "exit_throttle_fraction",
    )
    deltas: dict[str, Any] = {}
    metric_status: dict[str, str] = {}
    for name in additive:
        before = _finite(baseline_metrics.get(name))
        after = _finite(comparison_metrics.get(name))
        deltas[name] = _round(after - before, 5) if before is not None and after is not None else None
        before_count = int(_finite(baseline_counts.get(name)) or 0)
        after_count = int(_finite(comparison_counts.get(name)) or 0)
        metric_status[name] = (
            "usable"
            if before_count >= 2 and after_count >= 2
            else "limited"
            if before_count >= 1 and after_count >= 1
            else "unavailable"
        )
    for name in ("brake_energy_proxy", "steering_work_proxy"):
        before = _finite(baseline_metrics.get(name))
        after = _finite(comparison_metrics.get(name))
        deltas[f"{name}_percent"] = _round(
            (after / before - 1.0) * 100.0,
            1,
        ) if before not in (None, 0.0) and after is not None else None
        before_count = int(_finite(baseline_counts.get(name)) or 0)
        after_count = int(_finite(comparison_counts.get(name)) or 0)
        metric_status[f"{name}_percent"] = (
            "usable"
            if before_count >= 2 and after_count >= 2
            else "limited"
            if before_count >= 1 and after_count >= 1
            else "unavailable"
        )
    return {
        "baseline_phase": baseline.get("phase"),
        "comparison_phase": comparison.get("phase"),
        "status": status,
        "minimum_laps_per_phase_for_usable": 2,
        "delta_definition": "comparison phase minus baseline phase",
        "metric_status": metric_status,
        "deltas": deltas,
    }


def _corner_phase_coaching(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Create bounded local coaching without inventing a benchmark target."""

    status = str(comparison.get("status") or "unavailable")
    baseline = str(comparison.get("baseline_phase") or "baseline")
    compared = str(comparison.get("comparison_phase") or "comparison")
    if status != "usable":
        return {
            "evidence_class": "inferred",
            "status": "insufficient_comparison",
            "finding": "Fewer than two usable laps were available in one or both phases.",
            "action": "Collect two clean laps in each phase before changing technique.",
            "exact_target_emitted": False,
        }
    deltas = comparison.get("deltas") or {}
    metric_status = comparison.get("metric_status") or {}

    def usable_delta(name: str) -> float | None:
        return (
            _finite(deltas.get(name))
            if metric_status.get(name) == "usable"
            else None
        )

    entry = usable_delta("entry_speed_mph")
    minimum = usable_delta("minimum_speed_mph")
    exit_speed = usable_delta("exit_speed_mph")
    brake_energy = usable_delta("brake_energy_proxy_percent")
    steer_work = usable_delta("steering_work_proxy_percent")
    steering_corrections = usable_delta("steering_corrections")
    turn_in_timing = usable_delta("turn_in_offset_lap_pct")
    throttle_timing = usable_delta("throttle_pickup_offset_lap_pct")
    release_timing = usable_delta("brake_release_offset_lap_pct")

    evidence: list[str] = []
    actions: list[str] = []
    if entry is not None and entry > 1.0 and steer_work is not None and steer_work > 10.0:
        evidence.append(f"entry speed rose {entry:.1f} mph and steering work rose {steer_work:.0f}%")
        actions.append("reduce combined entry speed and steering load late in the run")
    if brake_energy is not None and brake_energy > 10.0 and minimum is not None and minimum < -1.0:
        evidence.append(f"brake-energy proxy rose {brake_energy:.0f}% while minimum speed fell {abs(minimum):.1f} mph")
        actions.append("brake smoothly and finish releasing before adding steering")
    if release_timing is not None and release_timing > 0.005:
        evidence.append(f"brake release moved {release_timing * 100:.1f}% of a lap later")
        actions.append("release the brake earlier in the loaded phase")
    if (
        turn_in_timing is not None
        and turn_in_timing < -0.005
        and steer_work is not None
        and steer_work > 10.0
    ):
        evidence.append(
            f"turn-in moved {abs(turn_in_timing) * 100:.1f}% of a lap earlier while steering work rose {steer_work:.0f}%"
        )
        actions.append("turn in later, then build steering angle once")
    if steering_corrections is not None and steering_corrections >= 1.0:
        evidence.append(
            f"median steering corrections increased by {steering_corrections:.0f}"
        )
        actions.append("use one clean steering arc and remove the extra correction")
    if throttle_timing is not None and throttle_timing > 0.005 and exit_speed is not None and exit_speed < -1.0:
        evidence.append(f"75% throttle pickup moved {throttle_timing * 100:.1f}% of a lap later and exit speed fell {abs(exit_speed):.1f} mph")
        actions.append("prioritize rotation and clean throttle pickup before more exit commitment")
    if not evidence:
        finding = f"No strong {baseline}-to-{compared} control shift crossed the deterministic coaching thresholds."
        action = "Repeat the same line; use an aligned lap before exact targets."
    else:
        finding = "; ".join(evidence).capitalize() + "."
        action = "; ".join(dict.fromkeys(actions)).capitalize() + "."
    return {
        "evidence_class": "inferred_from_derived_phase_comparison",
        "status": "usable",
        "finding": finding,
        "action": action,
        "exact_target_emitted": False,
        "target_limitation": "Exact corner targets require a valid aligned representative-lap comparison.",
    }


def _corner_tire_age_summary(
    table: TelemetryTable,
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    track_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare clean corner behavior by age-aware observational run phase."""

    segments = [
        segment
        for segment in ((track_profile or {}).get("detected_corner_segments") or ())
        if isinstance(segment, Mapping)
        and _finite(segment.get("start_pct")) is not None
        and _finite(segment.get("end_pct")) is not None
    ]
    exclusion_counts: dict[str, int] = defaultdict(int)
    previous_flag: str | None = None
    for lap in sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0):
        for reason in _corner_lap_exclusion_reasons(lap, previous_flag):
            exclusion_counts[reason] += 1
        previous_flag = str(lap.get("flag_state") or "")

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "unavailable",
        "confirmed_tire_age_status": "unavailable",
        "tire_state_phase_status": "unavailable",
        "observational_run_phase_status": "unavailable",
        "phase_definition": {
            "confirmed_model": (
                "chronological early/middle/late thirds of this run's eligible laps with exact per-tire "
                "green-lap-on-set bounds after every tire has a confirmed zero-age boundary"
            ),
            "fallback_model": (
                "chronological early/middle/late thirds with session-relative age bounds when at least one "
                "tire lacks a confirmed zero-age boundary"
            ),
            "minimum_laps_per_phase_for_usable_comparison": 2,
            "tire_state_labels": (
                "fresh/settled/worn remain unavailable until session- or history-derived pace/control/tire "
                "change points support inclusive green-lap-age boundaries"
            ),
            "late_semantics": (
                "older-set/late-run proxy when green-lap age is confirmed; late-run proxy otherwise; "
                "never measured worn tread state"
            ),
        },
        "eligibility": {
            "rule": "complete clean green laps only; pit, caution/mixed, restart, non-racing-state, off-track, close-traffic, and partial laps are excluded when the required channels exist",
            "candidate_lap_count": len(laps),
            "excluded_lap_counts": dict(sorted(exclusion_counts.items())),
            "screening": {
                "track_location_available": table.has("PlayerTrackSurface"),
                "traffic_distance_available": table.has(
                    "CarDistAhead", "CarDistBehind"
                ),
                "on_track_minimum_fraction": 0.98,
                "close_traffic_maximum_fraction": 0.10,
            },
        },
        "metric_evidence": {
            "entry_minimum_exit_speed_mph": {"evidence_class": "derived_from_measured_Speed", "aggregation": "median of per-lap zone observations"},
            "brake_average_peak": {"evidence_class": "derived_from_measured_Brake", "aggregation": "median of per-lap average/peak fractions"},
            "brake_energy_proxy": {"evidence_class": "derived_proxy", "definition": "integral of brake fraction x vehicle speed through the zone"},
            "brake_onset_release": {"evidence_class": "derived_routine_sample_threshold", "definition": "0.05 brake-fraction crossings; omitted when the transition is not observed"},
            "steering_amount_work": {"evidence_class": "derived_from_measured_SteeringWheelAngle", "definition": "mean absolute angle, sign-change corrections, and integral of absolute angle x speed"},
            "turn_in": {"evidence_class": "derived_routine_sample_threshold", "definition": "first SteeringWheelAngle demand crossing 0.05 rad above the pre-zone signed steering baseline; absolute-angle fallback when no baseline exists; omitted and boundary-censored when the crossing predates the analysis window"},
            "throttle_pickup_exit": {"evidence_class": "derived_from_measured_Throttle", "definition": "first 0.75 throttle sample after minimum speed plus zone-exit throttle"},
            "coaching": {"evidence_class": "inferred", "target_rule": "no exact target without a valid aligned representative-lap comparison"},
        },
        "runs": [],
        "limitations": [
            "Load-zone names are provisional telemetry labels unless sourced knowledge supplies an official name.",
            "Routine sampled onset/release and throttle timing are coarser than a native-rate event query.",
            "Within-run phase changes can also reflect traffic, line, damage, weather, track state, fuel mass, and driver adaptation.",
            "Tire wear remains a discrete pit-service observation; this artifact does not interpolate tread remaining within a run.",
            "No session-derived tire-state change-point model is applied, so fresh/settled/worn phase labels remain unavailable.",
        ],
    }
    if not table.has("CarDistAhead", "CarDistBehind"):
        result["limitations"].append(
            "CarDistAhead/CarDistBehind were unavailable, so traffic and dirty-air exposure could not be screened."
        )
    if not table.has("PlayerTrackSurface"):
        result["limitations"].append(
            "PlayerTrackSurface was unavailable, so off-track samples could not be screened."
        )
    if not segments:
        result["reason"] = "No telemetry-derived load zones were detected."
        return result
    if not runs:
        result["reason"] = "No complete run boundaries were detected."
        return result

    times = table.get("SessionTime", "SessionTimeOfDay", default=None)
    sample_rate = _finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0
    dts = _dt_series(times, sample_rate)
    series: dict[str, Sequence[Any]] = {
        "pct": table.get("LapDistPct", default=None),
        "speed": table.get("Speed", default=None),
        "brake": table.get("Brake", "BrakeRaw", default=None),
        "throttle": table.get("Throttle", "ThrottleRaw", default=None),
        "steering": table.get("SteeringWheelAngle", default=None),
        "flags": table.get("SessionFlags", default=0),
        "pit": table.get("OnPitRoad", default=False),
        "track_location": table.get("PlayerTrackSurface", default=None) if table.has("PlayerTrackSurface") else (),
        "car_distance_ahead": table.get("CarDistAhead", default=None) if table.has("CarDistAhead") else (),
        "car_distance_behind": table.get("CarDistBehind", default=None) if table.has("CarDistBehind") else (),
        "session_state": table.get("SessionState", default=None) if table.has("SessionState") else (),
    }
    initial_zero_evidence = _initial_tire_zero_evidence(table, laps, runs)
    any_phase_data = False
    any_usable_comparison = False
    any_usable_confirmed_age_comparison = False
    any_usable_observational_comparison = False
    for run in runs:
        start_lap = _finite(run.get("start_lap"))
        end_lap = _finite(run.get("end_lap"))
        if start_lap is None or end_lap is None:
            continue
        run_laps = [
            lap
            for lap in laps
            if (number := _finite(lap.get("lap"))) is not None
            and start_lap <= number <= end_lap
        ]
        reference_numbers = run.get("coaching_reference_lap_numbers")
        if reference_numbers is None:
            reference_numbers = run.get("valid_green_lap_numbers") or ()
        valid_numbers = {
            int(number)
            for number in reference_numbers
            if _finite(number) is not None
        }
        eligible_laps = [
            lap
            for lap in run_laps
            if int(_finite(lap.get("lap")) or -1) in valid_numbers
        ]
        run_exclusions: dict[str, int] = defaultdict(int)
        previous: str | None = None
        for lap in sorted(run_laps, key=lambda item: _finite(item.get("lap")) or -1.0):
            for reason in _corner_lap_exclusion_reasons(lap, previous):
                run_exclusions[reason] += 1
            previous = str(lap.get("flag_state") or "")

        age_context = _run_tire_age_context(run, initial_zero_evidence)
        phase_model, phase_groups, age_by_lap = _phase_lap_groups(
            eligible_laps, run_laps, age_context
        )
        first_eligible_number = min(age_by_lap) if age_by_lap else None
        first_eligible_age = (
            age_by_lap.get(first_eligible_number, {})
            if first_eligible_number is not None
            else {}
        )
        first_starts = [
            _finite((first_eligible_age.get(tire) or {}).get("start"))
            for tire in TIRES
        ]
        new_set_confirmed = bool(
            age_context.get("confirmed_zero_age_for_all_corners")
            and len(first_starts) == len(TIRES)
            and all(value is not None and value <= 0.25 for value in first_starts)
        )
        run_item: dict[str, Any] = {
            "run_number": run.get("run_number"),
            "phase_model": phase_model,
            "tire_age_basis": age_context,
            "new_set_confirmed": new_set_confirmed,
            "new_set_confirmation_rule": (
                "all four tire ages are lifecycle-confirmed and the first eligible lap starts at no more than 0.25 green-lap equivalents"
            ),
            "eligible_lap_count": len(eligible_laps),
            "eligible_lap_numbers": [lap.get("lap") for lap in eligible_laps],
            "excluded_lap_counts": dict(sorted(run_exclusions.items())),
            "tire_age_phase_availability": {
                "fresh": "unavailable_without_session_derived_change_point",
                "settled": "unavailable_without_session_derived_change_point",
                "worn": "unavailable_without_session_derived_change_point",
            },
            "observational_phase_semantics": {
                "early": (
                    "new-set/early-run proxy"
                    if new_set_confirmed
                    else "early-run proxy"
                ),
                "middle": (
                    "confirmed-age middle-run proxy"
                    if phase_model == "confirmed_age_run_thirds_proxy"
                    else "middle-run proxy"
                ),
                "late": (
                    "older-set/late-run proxy"
                    if phase_model == "confirmed_age_run_thirds_proxy"
                    else "late-run proxy"
                ),
            },
            "zones": [],
        }
        eligible_by_number = {
            int(_finite(lap.get("lap")) or -1): lap for lap in eligible_laps
        }
        for segment_index, segment in enumerate(segments, 1):
            segment_number = int(_finite(segment.get("segment")) or segment_index)
            wraps = bool(segment.get("wraps_start_finish")) or (
                (_finite(segment.get("start_pct")) or 0.0)
                > (_finite(segment.get("end_pct")) or 0.0)
            )
            metrics_by_lap: dict[int, dict[str, Any]] = {}
            for lap_number, lap in eligible_by_number.items():
                next_lap = eligible_by_number.get(lap_number + 1)
                metric = _zone_lap_metric(
                    anchor_lap=lap,
                    next_lap=next_lap,
                    zone=segment,
                    series=series,
                    dts=dts,
                    require_next_lap=wraps,
                )
                if metric is not None:
                    metrics_by_lap[lap_number] = metric

            summaries = [
                _phase_summary(
                    phase,
                    [],
                    [],
                    age_by_lap,
                    unavailable_reason=(
                        "No session- or history-derived pace/control/tire change points support "
                        f"a {phase} green-lap-age boundary."
                    ),
                )
                for phase in ("fresh", "settled", "worn")
            ]
            observational = [
                _phase_summary(
                    phase,
                    assigned,
                    [
                        metrics_by_lap[int(_finite(lap.get("lap")) or -1)]
                        for lap in assigned
                        if int(_finite(lap.get("lap")) or -1) in metrics_by_lap
                    ],
                    age_by_lap,
                )
                for phase, assigned in (
                    ("early", phase_groups.get("early", [])),
                    ("middle", phase_groups.get("middle", [])),
                    ("late", phase_groups.get("late", [])),
                )
            ]

            comparison_source = observational
            comparisons = [
                _phase_comparison(comparison_source[index], comparison_source[index + 1])
                for index in range(max(0, len(comparison_source) - 1))
            ]
            coaching_comparison = comparisons[-1] if comparisons else {
                "status": "unavailable",
                "baseline_phase": "middle",
                "comparison_phase": "late",
            }
            if any(item.get("lap_count") for item in comparison_source):
                any_phase_data = True
            if coaching_comparison.get("status") == "usable":
                any_usable_comparison = True
                if phase_model == "confirmed_age_run_thirds_proxy":
                    any_usable_confirmed_age_comparison = True
                any_usable_observational_comparison = True
            run_item["zones"].append(
                {
                    "zone_id": f"load-zone-{segment_number}",
                    "zone_label": f"Load zone {segment_number}",
                    "corner_name_status": "provisional_telemetry_load_zone",
                    "start_pct": _round(segment.get("start_pct"), 5),
                    "end_pct": _round(segment.get("end_pct"), 5),
                    "wraps_start_finish": wraps,
                    "tire_age_phases": summaries,
                    "observational_run_phases": observational,
                    "comparisons": comparisons,
                    "coaching": _corner_phase_coaching(coaching_comparison),
                }
            )
        result["runs"].append(run_item)

    result["status"] = (
        "usable" if any_usable_comparison else "limited" if any_phase_data else "unavailable"
    )
    result["confirmed_tire_age_status"] = (
        "usable" if any_usable_confirmed_age_comparison else "unavailable"
    )
    result["observational_run_phase_status"] = (
        "usable" if any_usable_observational_comparison else "unavailable"
    )
    if not any_phase_data:
        result["reason"] = "No run had enough eligible samples in a detected load zone."
    return result


def _channel_range(
    table: TelemetryTable,
    aliases: Sequence[str],
    *,
    transform: Any = None,
    digits: int = 3,
) -> dict[str, Any] | None:
    channel, raw_values = table.resolve(*aliases, default=None)
    if channel is None:
        return None
    values: list[float] = []
    for raw in raw_values:
        value = transform(raw) if transform is not None else _finite(raw)
        if value is not None:
            values.append(float(value))
    if not values:
        return None
    return {
        "channel": channel,
        "source_unit": table.unit(channel),
        "start": _round(values[0], digits),
        "end": _round(values[-1], digits),
        "minimum": _round(min(values), digits),
        "median": _round(_median(values), digits),
        "maximum": _round(max(values), digits),
        "changed": (max(values) - min(values)) > 10 ** (-digits),
    }


def _conditions_summary(table: TelemetryTable) -> dict[str, Any]:
    temperature = lambda value: _convert_setup_value(value, "temperature", "C")
    percent = lambda value: (
        fraction * 100.0 if (fraction := _fraction(value)) is not None else None
    )
    fields: tuple[tuple[str, tuple[str, ...], Any, int], ...] = (
        ("track_temperature_f", ("TrackTempCrew", "TrackTemp"), temperature, 1),
        ("air_temperature_f", ("AirTemp",), temperature, 1),
        # TrackWetness is an SDK categorical enum, not a percentage.
        ("track_wetness_state", ("TrackWetness",), None, 0),
        ("relative_humidity_percent", ("RelativeHumidity",), percent, 1),
        ("wind_speed_mph", ("WindVel",), lambda value: (_finite(value) * 2.236936 if _finite(value) is not None else None), 1),
        ("air_density_kg_m3", ("AirDensity",), None, 4),
        ("air_pressure_pa", ("AirPressure",), None, 0),
        ("precipitation_percent", ("Precipitation",), percent, 2),
    )
    result: dict[str, Any] = {}
    for name, aliases, transform, digits in fields:
        summary = _channel_range(
            table, aliases, transform=transform, digits=digits
        )
        if summary:
            if name == "track_wetness_state":
                summary["semantics"] = "iRacing TrackWetness categorical state"
            result[name] = summary
    if table.has("WeatherDeclaredWet"):
        declared = table.get("WeatherDeclaredWet", default=False)
        result["weather_declared_wet"] = any(_bool(value) for value in declared)
    if table.has("PlayerTireCompound"):
        compounds = [
            int(value)
            for raw in table.get("PlayerTireCompound", default=None)
            if (value := _finite(raw)) is not None
        ]
        if compounds:
            result["player_tire_compound"] = compounds[-1]
    return result


def _driver_adjustments_summary(table: TelemetryTable) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for channel in (
        "dcBrakeBias",
        "dpQTape",
        "dpWeightJackerLeft",
        "dpWeightJackerRight",
        "dpFuelAddKg",
    ):
        summary = _channel_range(table, (channel,), digits=4)
        if summary:
            summary["semantics"] = (
                "requested pit adjustment; not proof that service was completed"
                if channel.startswith("dp")
                else "in-car driver control setting"
            )
            result[channel] = summary
    return result


def _exact_scheduled_laps(race: Mapping[str, Any]) -> float | None:
    """Return a race distance only when the configured lap cap governs alone."""

    scheduled_laps = _finite(race.get("scheduled_laps"))
    scheduled_minutes = _finite(race.get("scheduled_minutes"))
    if scheduled_laps is None or scheduled_laps <= 0.0:
        return None
    if scheduled_minutes is not None:
        return None
    return scheduled_laps


def _strategy(runs: list[dict[str, Any]], race: Mapping[str, Any]) -> dict[str, Any]:
    green_burns = [
        run["fuel"]["green_l_per_lap"] for run in runs
        if _finite(run.get("fuel", {}).get("green_l_per_lap")) not in (None, 0.0)
    ]
    caution_burns = [
        run["fuel"]["caution_l_per_lap"] for run in runs
        if _finite(run.get("fuel", {}).get("caution_l_per_lap")) not in (None, 0.0)
    ]
    green_burn = _median(green_burns)
    caution_burn = _median(caution_burns)
    start_fuel_values = [
        value
        for run in runs
        if (value := _finite(run.get("fuel", {}).get("start_l"))) is not None
        and value > 0.0
    ]
    measured_tire_runs = [run for run in runs if run.get("tire_observation")]
    degradation = [
        run["pace"]["green_lap_time_slope_s_per_lap"] for run in runs
        if _finite(run.get("pace", {}).get("green_lap_time_slope_s_per_lap")) is not None
    ]
    pit_assessments = []
    configured_laps = _finite(race.get("scheduled_laps"))
    configured_minutes = _finite(race.get("scheduled_minutes"))
    hybrid_limits = (
        configured_laps is not None
        and configured_laps > 0.0
        and configured_minutes is not None
    )
    scheduled_laps = _exact_scheduled_laps(race)
    for run_index, run in enumerate(runs):
        fuel_end = _finite(run.get("fuel", {}).get("end_l"))
        reserve_laps = fuel_end / green_burn if fuel_end is not None and green_burn not in (None, 0.0) else None
        run_end_lap = _finite(run.get("end_lap"))
        race_laps_remaining = (
            max(0.0, scheduled_laps - run_end_lap)
            if scheduled_laps is not None and run_end_lap is not None
            else None
        )
        all_green_surplus_laps = (
            reserve_laps - race_laps_remaining
            if reserve_laps is not None and race_laps_remaining is not None
            else None
        )
        tire = run.get("tire_observation") or {}
        lowest = _finite(tire.get("lowest_remaining_percent"))
        next_run = runs[run_index + 1] if run_index + 1 < len(runs) else None
        post_stop_fuel_l = (
            _finite((next_run.get("fuel") or {}).get("start_l"))
            if next_run is not None and run.get("ended_with_pit_stop")
            else None
        )
        post_stop_range_laps = (
            post_stop_fuel_l / green_burn
            if post_stop_fuel_l is not None and green_burn not in (None, 0.0)
            else None
        )
        next_run_start_lap = (
            _finite(next_run.get("start_lap")) if next_run is not None else None
        )
        race_laps_remaining_after_stop = (
            max(0.0, scheduled_laps - next_run_start_lap)
            if scheduled_laps is not None and next_run_start_lap is not None
            else None
        )
        post_stop_all_green_surplus_laps = (
            post_stop_range_laps - race_laps_remaining_after_stop
            if post_stop_range_laps is not None
            and race_laps_remaining_after_stop is not None
            else None
        )
        position_before_stop = _finite((run.get("position") or {}).get("end"))
        position_after_stop = (
            _finite((next_run.get("position") or {}).get("start"))
            if next_run is not None and run.get("ended_with_pit_stop")
            else None
        )
        pit_assessments.append(
            {
                "run_number": run["run_number"],
                "was_pit_stop": bool(run.get("ended_with_pit_stop")),
                "was_post_run_service": bool(run.get("ended_with_post_run_service")),
                "ended_under_caution": run.get("ended_under_caution"),
                "position_at_end": run.get("position", {}).get("end"),
                "fuel_laps_remaining_at_end": _round(reserve_laps, 2),
                "scheduled_race_laps_remaining": _round(race_laps_remaining, 2),
                "all_green_fuel_surplus_laps": _round(all_green_surplus_laps, 2),
                "post_stop_fuel_l": _round(post_stop_fuel_l),
                "post_stop_all_green_range_laps": _round(post_stop_range_laps, 2),
                "scheduled_race_laps_remaining_after_stop": _round(
                    race_laps_remaining_after_stop, 2
                ),
                "post_stop_all_green_surplus_laps": _round(
                    post_stop_all_green_surplus_laps, 2
                ),
                "position_before_stop": _round(position_before_stop, 0),
                "position_after_stop": _round(position_after_stop, 0),
                "pit_cycle_position_change": _round(
                    position_before_stop - position_after_stop
                    if position_before_stop is not None
                    and position_after_stop is not None
                    else None,
                    0,
                ),
                "lowest_tire_remaining_percent": _round(lowest, 2),
                "evidence": "complete" if fuel_end is not None and lowest is not None else "partial",
            }
        )
    limitations = [
        "An optimal pit call also depends on track position, pit-loss timing, stage/race rules, and future cautions."
    ]
    if any(run.get("tire_observation") is None for run in runs):
        limitations.append(
            "Final-run tire wear is unknown unless the car returned for a tire-reading update."
        )
    if hybrid_limits:
        limitations.append(
            "Lap-and-time limits are both configured; exact distance-dependent fuel and stop forecasts are withheld until the governing finish constraint can be established."
        )
    green_equivalents = _finite(race.get("green_lap_equivalents"))
    caution_equivalents = _finite(race.get("caution_lap_equivalents"))
    exposure_total = (green_equivalents or 0.0) + (caution_equivalents or 0.0)
    caution_fraction = (
        (caution_equivalents or 0.0) / exposure_total if exposure_total > 0.0 else 0.0
    )
    maximum_start_fuel = max(start_fuel_values) if start_fuel_values else None

    # One decision, made once, from exact quantities. The rounded fields below
    # are projections of it for display and for readers written against the
    # older shape; they are never re-derived from each other, which is the
    # defect FUEL-CONSISTENCY-001 records.
    decision = race_plan_decision.decide(
        scheduled_laps=scheduled_laps,
        green_burn_l_per_lap=green_burn,
        maximum_start_fuel_l=maximum_start_fuel,
        caution_burn_l_per_lap=caution_burn,
        observed_caution_fraction=caution_fraction,
        hybrid_limits=bool(hybrid_limits),
    )
    scenario = decision.caution_scenario
    forecast = {
        "status": decision.status,
        "scheduled_laps": _round(decision.scheduled_laps, 0),
        "maximum_recorded_run_start_fuel_l": _round(maximum_start_fuel),
        "operational_reserve_green_laps": decision.reserve_green_laps,
        "operational_reserve_fuel_l": _round(decision.reserve_fuel_l),
        "all_green_range_laps": _round(decision.all_green_range_laps, 1),
        "observed_caution_fraction": _round(caution_fraction, 4),
        "observed_mix_range_laps": _round(scenario.range_laps, 1) if scenario else None,
        "minimum_stops_all_green": decision.minimum_stops,
        "minimum_stops_at_observed_mix": scenario.minimum_stops if scenario else None,
        "equal_stint_pit_targets_all_green": [
            round(target, 1) for target in decision.equal_stint_pit_targets
        ],
        "classification": "fuel-feasibility forecast, not an optimal-pit-call claim",
        "assumptions": [
            "Uses the maximum fuel observed at a run start as available capacity; it may be below the car's legal tank maximum.",
            "Holds measured green/caution burn rates constant and reserves exactly one green lap for operational uncertainty.",
            "Equal-stint targets ignore live track position, stage breaks, pit loss, tire rules, damage, and future cautions.",
        ],
        # The authority itself, carried beside its projections so a consumer can
        # stop reparsing the rounded ones. Codex maps this record; nothing
        # downstream needs to recompute a stop count from a display scalar.
        "race_plan_decision": decision.to_payload(),
    }
    return {
        "measured_green_fuel_l_per_lap": _round(green_burn),
        "measured_green_fuel_gal_per_lap": _round(green_burn / 3.785411784 if green_burn else None),
        "measured_caution_fuel_l_per_lap": _round(caution_burn),
        "measured_caution_fuel_gal_per_lap": _round(caution_burn / 3.785411784 if caution_burn else None),
        "median_green_lap_degradation_s_per_lap": _round(_median(degradation), 4),
        "pit_assessments": pit_assessments,
        "forecast": forecast,
        "historical_context_required": True,
        "confidence": "medium" if green_burn is not None and measured_tire_runs else "low",
        "limitations": limitations,
    }


def build_technical_insights(
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    race: Mapping[str, Any],
    strategy: Mapping[str, Any],
    damage: Mapping[str, Any],
    tire_learning: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build compact, event-specific findings from recorded or bounded evidence."""

    def metric(
        label: str,
        value: Any,
        unit: str = "",
        evidence: str = "measured",
        *,
        signed: bool = False,
        detail: str = "",
        action: str = "",
        tone: str = "neutral",
        group: str = "",
    ) -> dict[str, Any]:
        if isinstance(value, float):
            rendered = f"{value:.3f}".rstrip("0").rstrip(".")
            if signed and value > 0:
                rendered = f"+{rendered}"
        elif isinstance(value, int) and signed and value > 0:
            rendered = f"+{value}"
        else:
            rendered = str(value)
        payload = {
            "label": label,
            "value": f"{rendered}{(' ' + unit) if unit else ''}",
            "numeric_value": value if isinstance(value, (int, float)) else None,
            "unit": unit or None,
            "evidence_type": evidence,
        }
        if detail:
            payload["detail"] = detail
        if action:
            payload["action"] = action
        if tone != "neutral":
            payload["tone"] = tone
        if group:
            payload["group"] = group
        return payload

    indexed_pit_runs = [
        (index, run)
        for index, run in enumerate(runs)
        if run.get("ended_with_pit_stop")
    ]
    pit_runs = [run for _, run in indexed_pit_runs]
    service_seconds: list[float] = []
    penalty_seconds: list[float] = []
    for run in pit_runs:
        service = run.get("pit_service") or {}
        start, end = _finite(service.get("start_time")), _finite(service.get("end_time"))
        if start is not None and end is not None and end >= start:
            service_seconds.append(end - start)
        if (penalty := _finite(service.get("penalty_served_s"))) is not None and penalty > 0:
            penalty_seconds.append(penalty)
    damage_summary = damage.get("summary") or {}
    pit_assessments = [
        item for item in (strategy.get("pit_assessments") or ())
        if isinstance(item, Mapping) and item.get("was_pit_stop")
    ]
    cycle_changes = [
        value for item in pit_assessments
        if (value := _finite(item.get("pit_cycle_position_change"))) is not None
    ]
    post_stop_margins = [
        value for item in pit_assessments
        if (value := _finite(item.get("post_stop_all_green_surplus_laps"))) is not None
    ]
    assessment_by_run = {
        int(run_number): item
        for item in pit_assessments
        if (run_number := _finite(item.get("run_number"))) is not None
    }
    pit_profiles: list[dict[str, Any]] = []
    for ordinal, (run_index, run) in enumerate(indexed_pit_runs):
        service = run.get("pit_service") or {}
        changed = sorted({
            str(value).upper()
            for value in (service.get("tires_changed_observed") or ())
            if str(value).upper() in TIRES
        })
        tire_call = "four" if len(changed) == 4 else "two" if len(changed) == 2 else "other"
        tire_call_side = (
            "four"
            if len(changed) == 4
            else "right_side"
            if changed == ["RF", "RR"]
            else "left_side"
            if changed == ["LF", "LR"]
            else "diagonal"
            if len(changed) == 2
            else "partial_or_unknown"
        )
        run_number = int(_finite(run.get("run_number")) or 0)
        assessment = assessment_by_run.get(run_number)
        if assessment is None and ordinal < len(pit_assessments):
            assessment = pit_assessments[ordinal]
        assessment = assessment or {}
        next_run = runs[run_index + 1] if run_index + 1 < len(runs) else None
        start = _finite(service.get("start_time"))
        end = _finite(service.get("end_time"))
        outgoing_tires = (run.get("tire_observation") or {}).get("tires") or {}
        wear_by_corner = {
            str(corner).upper(): 100.0 - remaining
            for corner, details in outgoing_tires.items()
            if isinstance(details, Mapping)
            and (remaining := _finite(details.get("average_remaining_percent"))) is not None
        }
        next_green_laps = None
        if next_run is not None:
            next_green_laps = _finite((next_run.get("pace") or {}).get("green_laps_used"))
            if next_green_laps is None:
                next_green_laps = _finite(next_run.get("green_laps"))
        pit_profiles.append({
            "run_number": run_number,
            "tire_call": tire_call,
            "tire_call_side": tire_call_side,
            "changed": changed,
            "confirmation": service.get("tire_change_confirmation"),
            "service_s": end - start if start is not None and end is not None and end >= start else None,
            "next_early_pace_s": (
                _finite((next_run.get("pace") or {}).get("early_average_lap_s"))
                if next_run is not None else None
            ),
            "next_green_laps": next_green_laps,
            "cycle_change": _finite(assessment.get("pit_cycle_position_change")),
            "fuel_margin": _finite(assessment.get("post_stop_all_green_surplus_laps")),
            "under_caution": run.get("ended_under_caution") is True,
            "wear_by_corner": wear_by_corner,
            "mean_outgoing_wear_pct": _mean(wear_by_corner.values()),
        })

    def profile_mean(group: Sequence[Mapping[str, Any]], field: str) -> float | None:
        values = [value for item in group if (value := _finite(item.get(field))) is not None]
        return _mean(values)

    two_tire_profiles = [item for item in pit_profiles if item["tire_call"] == "two"]
    four_tire_profiles = [item for item in pit_profiles if item["tire_call"] == "four"]
    pit_metrics: list[dict[str, Any]] = []
    strategy_forecast = strategy.get("forecast") or {}
    scheduled_distance = _exact_scheduled_laps(race)
    recorded_distance = _finite(race.get("recorded_laps"))
    all_green_range = _finite(strategy_forecast.get("all_green_range_laps"))
    completed_distance = recorded_distance or (
        max(
            (
                value
                for run in runs
                if (value := _finite(run.get("end_lap"))) is not None
            ),
            default=None,
        )
        if runs
        else None
    )
    if cycle_changes:
        cycle_total = int(round(sum(cycle_changes)))
        pit_metrics.append(
            metric(
                "Pit-cycle net",
                cycle_total,
                "positions",
                "derived",
                signed=True,
                detail="Position immediately before each stop compared with the start of the following run.",
                action="Review stops that lost positions for entry, service, exit, and restart execution.",
                tone="positive" if cycle_total > 0 else "attention" if cycle_total < 0 else "neutral",
                group="outcome",
            )
        )
    if pit_runs:
        caution_stops = sum(item["under_caution"] for item in pit_profiles)
        pit_metrics.append(
            metric(
                "Stops",
                len(pit_runs),
                detail=f"{caution_stops} ended under caution; {len(pit_runs) - caution_stops} ended under green or an unknown flag state.",
                group="execution",
            )
        )
    elif runs:
        pit_metrics.append(
            metric(
                "Stops completed",
                0,
                detail="The race was completed without a pit stop.",
                action="Keep the no-stop plan when the selected race distance remains inside the measured fuel range.",
                tone="positive",
                group="outcome",
            )
        )
        if completed_distance is not None:
            pit_metrics.append(
                metric(
                    "Distance completed",
                    int(round(completed_distance)),
                    "laps",
                    detail="Completed recorded race distance.",
                    group="outcome",
                )
            )
        if scheduled_distance is not None and all_green_range is not None:
            no_stop_headroom = all_green_range - scheduled_distance
            pit_metrics.append(
                metric(
                    "No-stop headroom",
                    round(no_stop_headroom, 1),
                    "green laps",
                    "derived",
                    signed=True,
                    detail="Measured all-green range after the operational reserve minus the scheduled race distance.",
                    action=(
                        "The available fuel range covered the scheduled distance; verify the same capacity and burn before the next race."
                        if no_stop_headroom >= 0.0
                        else "The selected distance exceeds the measured no-stop range and needs a fuel stop."
                    ),
                    tone="positive" if no_stop_headroom >= 0.0 else "attention",
                    group="strategy",
                )
            )
    if service_seconds:
        pit_metrics.append(
            metric(
                "Total service",
                round(sum(service_seconds), 2),
                "s",
                detail=f"Longest timed service was {max(service_seconds):.2f} s.",
                action="Open the stop selector to compare tire, fuel, repair, and penalty work.",
                group="execution",
            )
        )
    repair = _finite(damage_summary.get("total_repair_work_completed_s"))
    if repair is not None and repair > 0:
        pit_metrics.append(
            metric(
                "Repair work",
                round(repair, 2),
                "s",
                detail="Completed repair time is separated from routine fuel and tire service.",
                tone="attention",
                group="exceptions",
            )
        )
    if penalty_seconds:
        pit_metrics.append(
            metric(
                "Penalty service",
                round(sum(penalty_seconds), 2),
                "s",
                detail="Time served under a confirmed penalty state.",
                action="Use the incident and pit timeline to identify the avoidable trigger.",
                tone="attention",
                group="exceptions",
            )
        )
    if post_stop_margins:
        margin = round(min(post_stop_margins), 1)
        pit_metrics.append(
            metric(
                "Tightest fuel margin",
                margin,
                "green laps",
                "derived",
                signed=True,
                detail="Post-stop fuel range minus the green laps remaining to the scheduled finish.",
                action="A negative margin means that stop could not reach the scheduled finish at the measured green-lap burn.",
                tone="attention" if margin < 0 else "positive",
                group="strategy",
            )
        )

    if two_tire_profiles or four_tire_profiles:
        pit_metrics.append(
            metric(
                "Tire calls",
                f"{len(two_tire_profiles)} two / {len(four_tire_profiles)} four",
                detail="Only confirmed corner changes are counted; unknown or partial service is excluded.",
                group="tires",
            )
        )
        for profile in pit_profiles:
            if not profile["changed"]:
                continue
            changed_label = " + ".join(profile["changed"])
            call_label = {
                "right_side": "Right-side tire call",
                "left_side": "Left-side tire call",
                "diagonal": "Two-tire call",
                "four": "Four-tire call",
            }.get(str(profile["tire_call_side"]), "Confirmed tire call")
            pit_metrics.append(
                metric(
                    f"Run {profile['run_number']} service",
                    changed_label,
                    detail=f"{call_label}: only confirmed tire changes are listed.",
                    action=(
                        "Compare its shorter service against the following run's pace, position, and unchanged-side wear before repeating it."
                        if profile["tire_call"] == "two"
                        else "Use this as the four-tire baseline when a comparable two-tire call is available."
                    ),
                    group="tires",
                )
            )
    two_service = profile_mean(two_tire_profiles, "service_s")
    four_service = profile_mean(four_tire_profiles, "service_s")
    two_pace = profile_mean(two_tire_profiles, "next_early_pace_s")
    four_pace = profile_mean(four_tire_profiles, "next_early_pace_s")
    two_cycle = profile_mean(two_tire_profiles, "cycle_change")
    four_cycle = profile_mean(four_tire_profiles, "cycle_change")
    two_wear = profile_mean(two_tire_profiles, "mean_outgoing_wear_pct")
    four_wear = profile_mean(four_tire_profiles, "mean_outgoing_wear_pct")
    if two_service is not None and four_service is not None:
        service_delta = round(two_service - four_service, 2)
        pit_metrics.append(
            metric(
                "2 vs 4 service",
                service_delta,
                "s",
                "derived",
                signed=True,
                detail="Average two-tire service minus average four-tire service in this race; negative is shorter.",
                action="Balance this time difference against the following-run pace and position results.",
                tone="positive" if service_delta < 0 else "attention" if service_delta > 0 else "neutral",
                group="tires",
            )
        )
    if two_pace is not None and four_pace is not None:
        pace_comparison = round(two_pace - four_pace, 3)
        pit_metrics.append(
            metric(
                "2 vs 4 next-run pace",
                pace_comparison,
                "s",
                "derived",
                signed=True,
                detail="Average early clean pace after two tires minus early clean pace after four; negative is faster.",
                action="Treat this as a retrospective association unless fuel, traffic, weather, and flag context also match.",
                tone="positive" if pace_comparison < 0 else "attention" if pace_comparison > 0 else "neutral",
                group="tires",
            )
        )
    if two_cycle is not None and four_cycle is not None:
        position_comparison = round(two_cycle - four_cycle, 1)
        pit_metrics.append(
            metric(
                "2 vs 4 cycle",
                position_comparison,
                "positions",
                "derived",
                signed=True,
                detail="Average pit-cycle position change after two tires minus the four-tire average; positive favored two tires.",
                action="Use with service and next-run pace; it does not isolate the tire call from traffic or caution timing.",
                tone="positive" if position_comparison > 0 else "attention" if position_comparison < 0 else "neutral",
                group="tires",
            )
        )
    if two_wear is not None and four_wear is not None:
        wear_comparison = round(two_wear - four_wear, 1)
        pit_metrics.append(
            metric(
                "2 vs 4 outgoing wear",
                wear_comparison,
                "points",
                "derived",
                signed=True,
                detail="Average confirmed O/M/I wear before two-tire stops minus the four-tire-stop average.",
                action="Use this with the service, cycle, and following-run results; unlike-for-like wear is essential.",
                group="tires",
            )
        )
    if penalty_seconds:
        pit_takeaway = f"Penalty service cost {sum(penalty_seconds):.1f} s; start with the stop timeline."
    elif cycle_changes and sum(cycle_changes) < 0:
        pit_takeaway = f"Pit cycles returned the car {abs(int(round(sum(cycle_changes))))} position(s) worse overall."
    elif two_service is not None and four_service is not None and two_pace is not None and four_pace is not None:
        pit_takeaway = (
            f"Two tires were {abs(two_service - four_service):.2f} s "
            f"{'shorter' if two_service < four_service else 'longer'} in service and the next-run early pace was "
            f"{abs(two_pace - four_pace):.3f} s {'faster' if two_pace < four_pace else 'slower'}."
        )
    elif cycle_changes and sum(cycle_changes) > 0:
        pit_takeaway = f"Pit cycles returned the car {int(round(sum(cycle_changes)))} position(s) better overall."
    elif post_stop_margins and min(post_stop_margins) < 0:
        pit_takeaway = f"The tightest stop was {abs(min(post_stop_margins)):.1f} all-green lap(s) short of scheduled distance."
    elif service_seconds:
        pit_takeaway = f"Service totaled {sum(service_seconds):.1f} s across {len(pit_runs)} stop(s)."
    elif runs and scheduled_distance is not None and all_green_range is not None:
        no_stop_headroom = all_green_range - scheduled_distance
        pit_takeaway = (
            f"No fuel stop was needed for {scheduled_distance:.0f} laps; all-green range retained "
            f"{no_stop_headroom:.1f} lap(s) beyond the scheduled distance."
            if no_stop_headroom >= 0.0
            else f"No stop occurred, but all-green range was {abs(no_stop_headroom):.1f} lap(s) short of the scheduled distance."
        )
    elif runs:
        pit_takeaway = "The race used a no-stop plan; stop execution is not part of this debrief."
    else:
        pit_takeaway = "No complete pit-cycle comparison is available."
    pit_rating = (
        "attention" if penalty_seconds or (cycle_changes and sum(cycle_changes) < 0)
        else "gain" if cycle_changes and sum(cycle_changes) > 0
        else "review"
    )
    pit_unavailable: list[str] = []
    if pit_runs and not (two_tire_profiles and four_tire_profiles):
        pit_unavailable.append("Both confirmed two-tire and four-tire stops are required for a direct retrospective comparison.")
    if pit_runs:
        pit_unavailable.append("Race tire-service rules were not present, so legality and mandatory-service conclusions are withheld.")
        pit_unavailable.append("Comparable historical two-tire and four-tire strategy outcomes were not available in the local tire model.")
    observed_tire_calls = [
        {
            "run_number": profile["run_number"],
            "tires_changed": list(profile["changed"]),
            "call_type": profile["tire_call"],
            "side": profile["tire_call_side"],
            "service_s": _round(profile["service_s"], 2),
            "next_run_early_pace_s": _round(profile["next_early_pace_s"], 3),
            "pit_cycle_position_change": _round(profile["cycle_change"], 1),
            "confirmation": profile["confirmation"],
        }
        for profile in pit_profiles
        if profile["changed"]
    ]
    pit_insight = {
        "key": "pit",
        "label": "Pit strategy",
        "status": "available" if runs else "unavailable",
        "rating": pit_rating if pit_runs else "no-stop" if runs else "unavailable",
        "takeaway": pit_takeaway,
        "metrics": pit_metrics,
        "tire_strategy": {
            "observed_calls": observed_tire_calls,
            "right_side_calls": sum(item["side"] == "right_side" for item in observed_tire_calls),
            "left_side_calls": sum(item["side"] == "left_side" for item in observed_tire_calls),
            "four_tire_calls": len(four_tire_profiles),
            "direct_two_vs_four_comparison": {
                "status": "usable" if two_tire_profiles and four_tire_profiles else "unavailable",
                "two_tire_samples": len(two_tire_profiles),
                "four_tire_samples": len(four_tire_profiles),
                "rule": "A direct comparison is emitted only when both confirmed call types occurred.",
            },
        },
        "evidence": (
            (["pit-road state"] if pit_runs else [])
            + (["position before and after pit cycle"] if cycle_changes else [])
            + (["recorded service timing"] if service_seconds else [])
            + (["repair timers"] if repair is not None and repair > 0 else [])
            + (["recorded post-stop fuel level"] if post_stop_margins else [])
        ),
        "unavailable_reasons": pit_unavailable,
    }

    measured_tire_runs = [run for run in runs if isinstance(run.get("tire_observation"), Mapping)]

    def omi_band(corner: str, raw_band: str) -> str | None:
        band = raw_band.upper()
        if band == "M":
            return "middle"
        if corner.upper().startswith("L"):
            return {"L": "outer", "R": "inner"}.get(band)
        return {"L": "inner", "R": "outer"}.get(band)

    measured_bands: list[tuple[float, str, str, int | None]] = []
    for run in measured_tire_runs:
        observation = run.get("tire_observation") or {}
        for corner, details in (observation.get("tires") or {}).items():
            if not isinstance(details, Mapping):
                continue
            for raw_band, raw_value in (details.get("remaining_percent") or {}).items():
                value = _finite(raw_value)
                band = omi_band(str(corner), str(raw_band))
                if value is not None and band:
                    run_number = _finite(run.get("run_number"))
                    measured_bands.append(
                        (value, str(corner).upper(), band, int(run_number) if run_number is not None else None)
                    )

    pace_runs = [
        run for run in runs
        if _finite((run.get("pace") or {}).get("early_to_late_delta_s")) is not None
    ]
    representative_run = max(
        pace_runs,
        key=lambda item: (
            _finite((item.get("pace") or {}).get("green_laps_used"))
            or _finite(item.get("green_laps"))
            or 0.0
        ),
        default=None,
    )
    pace_delta = (
        _finite((representative_run.get("pace") or {}).get("early_to_late_delta_s"))
        if representative_run is not None else None
    )
    representative_run_number = (
        int(_finite(representative_run.get("run_number")) or 0)
        if representative_run is not None else None
    )
    dynamics_run = max(
        (
            run
            for run in runs
            if isinstance(run.get("vehicle_dynamics"), Mapping)
            and any(
                _finite((run.get("vehicle_dynamics") or {}).get(key)) is not None
                for key in (
                    "front_wheel_lock_proxy_s",
                    "rear_wheelspin_proxy_s",
                    "abs_active_s",
                    "yaw_rate_abs_p95_deg_s_mean",
                )
            )
        ),
        key=lambda item: (
            _finite(item.get("green_laps"))
            or _finite(item.get("total_laps"))
            or 0.0
        ),
        default=None,
    )

    load_changes: list[tuple[float, str]] = []
    if representative_run is not None:
        load = representative_run.get("driving_load") or {}
        for label, early_key, late_key in (
            ("brake load", "early_brake_energy_proxy", "late_brake_energy_proxy"),
            ("steering load", "early_steering_work_proxy", "late_steering_work_proxy"),
        ):
            early, late = _finite(load.get(early_key)), _finite(load.get(late_key))
            if early not in (None, 0.0) and late is not None:
                load_changes.append(((late / early - 1.0) * 100.0, label))
    strongest_load = max(load_changes, key=lambda item: abs(item[0]), default=None)
    material_load = (
        strongest_load
        if strongest_load is not None and abs(strongest_load[0]) >= 0.5
        else None
    )
    latest_measured_run = max(
        measured_tire_runs,
        key=lambda item: _finite(item.get("run_number")) or -1.0,
        default=None,
    )
    latest_corner_wear: dict[str, float] = {}
    if latest_measured_run is not None:
        for corner, details in ((latest_measured_run.get("tire_observation") or {}).get("tires") or {}).items():
            if not isinstance(details, Mapping):
                continue
            remaining = _finite(details.get("average_remaining_percent"))
            if remaining is not None:
                latest_corner_wear[str(corner).upper()] = 100.0 - remaining

    tire_metrics: list[dict[str, Any]] = []
    tire_prediction = tire_learning.get("prediction") or {}
    lowest_remaining: float | None = None
    if measured_bands:
        lowest_remaining, corner, band, run_number = min(measured_bands, key=lambda item: item[0])
        run_suffix = f" - run {run_number}" if run_number else ""
        tire_metrics.append(
            metric(
                f"Most wear: {corner} {band}{run_suffix}",
                round(100.0 - lowest_remaining, 1),
                "%",
                detail=f"{lowest_remaining:.1f}% remained in the most-used confirmed O/M/I band.",
                action="Compare this corner and band with steering load and the late-run pace change.",
                tone="attention" if lowest_remaining < 45.0 else "neutral",
                group="condition",
            )
        )
    if pace_delta is not None:
        tire_metrics.append(
            metric(
                f"Run {representative_run_number} early-to-late" if representative_run_number else "Early-to-late pace",
                round(pace_delta, 3),
                "s",
                "derived",
                signed=True,
                detail="Average late clean pace minus average early clean pace in the longest comparable run.",
                action="A positive value is falloff; compare the same run's tire condition and driving load.",
                tone="attention" if pace_delta > 0.0 else "positive" if pace_delta < 0.0 else "neutral",
                group="pace",
            )
        )
    for change, label in load_changes:
        tire_metrics.append(
            metric(
                f"Late {label}",
                round(change, 1),
                "% vs early",
                "derived",
                signed=True,
                detail=f"Late-run {label} proxy compared with the early segment of the same run.",
                action=(
                    "More steering work can indicate added correction or scrub; confirm on the trace before changing technique."
                    if label == "steering load"
                    else "More braking work can add front-tire load; confirm braking points and traffic context on the trace."
                ),
                tone="attention" if change > 10.0 else "neutral",
                group="driver load",
            )
        )
    if len(latest_corner_wear) >= 4:
        front_wear = _mean(latest_corner_wear.get(corner) for corner in ("LF", "RF"))
        rear_wear = _mean(latest_corner_wear.get(corner) for corner in ("LR", "RR"))
        left_wear = _mean(latest_corner_wear.get(corner) for corner in ("LF", "LR"))
        right_wear = _mean(latest_corner_wear.get(corner) for corner in ("RF", "RR"))
        if front_wear is not None and rear_wear is not None:
            tire_metrics.append(
                metric(
                    "Front vs rear wear",
                    round(front_wear - rear_wear, 1),
                    "points",
                    "derived",
                    signed=True,
                    detail="Average front wear minus average rear wear at the latest confirmed tire reading.",
                    action="Positive means the fronts wore more; inspect entry speed, steering work, and balance together.",
                    group="balance",
                )
            )
        if right_wear is not None and left_wear is not None:
            tire_metrics.append(
                metric(
                    "Right vs left wear",
                    round(right_wear - left_wear, 1),
                    "points",
                    "derived",
                    signed=True,
                    detail="Average right-side wear minus average left-side wear at the latest confirmed tire reading.",
                    action="Use the per-corner O/M/I cards to see which tire and band created the difference.",
                    group="balance",
                )
            )

    dynamics_source = representative_run or dynamics_run
    dynamics = (
        dynamics_source.get("vehicle_dynamics") or {}
        if dynamics_source is not None
        else {}
    )
    dynamics_run_number = (
        int(_finite(dynamics_source.get("run_number")) or 0)
        if dynamics_source is not None
        else None
    )
    dynamics_green_laps = (
        _finite(dynamics_source.get("green_laps"))
        if dynamics_source is not None
        else None
    )
    for label, key, action in (
        ("Front lock proxy", "front_wheel_lock_proxy_s", "Review brake release and pressure only where the wheel-speed trace confirms front lock."),
        ("Rear wheelspin proxy", "rear_wheelspin_proxy_s", "Review throttle pickup only where the wheel-speed trace confirms rear spin."),
        ("ABS active", "abs_active_s", "Review the brake trace with ABS highlighting to see where intervention occurred."),
    ):
        value = _finite(dynamics.get(key))
        if value is not None and value > 0.0:
            per_green_lap = (
                value / dynamics_green_laps
                if dynamics_green_laps not in (None, 0.0)
                else None
            )
            tire_metrics.append(
                metric(
                    label,
                    round(value, 2),
                    "s",
                    detail=(
                        f"Run {dynamics_run_number}: {per_green_lap:.3f} s per green lap across {dynamics_green_laps:.0f} green laps."
                        if per_green_lap is not None
                        else f"Run {dynamics_run_number}: accumulated event-proxy time."
                    ),
                    action=action,
                    tone="attention",
                    group="tire events",
                )
            )
    yaw_rate_p95 = _finite(dynamics.get("yaw_rate_abs_p95_deg_s_mean"))
    if yaw_rate_p95 is not None:
        tire_metrics.append(
            metric(
                "Corner rotation",
                round(yaw_rate_p95, 1),
                "deg/s p95",
                "derived",
                detail=f"Run {dynamics_run_number}: mean lap-level 95th-percentile absolute yaw rate.",
                action="Compare rotation with steering and throttle traces at the load zones; this value describes motion and does not diagnose balance by itself.",
                group="driver load",
            )
        )

    predicted_life = _finite(tire_prediction.get("laps_remaining"))
    predicted_pace = _finite(tire_prediction.get("capability_pace_s"))
    predicted_cost = _finite(tire_prediction.get("pace_cost_s"))
    eligible_observations = _finite(tire_prediction.get("eligible_observations"))
    if tire_prediction.get("status") == "predicted" and predicted_life is not None:
        tire_metrics.append(
            metric(
                "Estimated life",
                round(predicted_life, 1),
                "green laps",
                "predicted",
                detail="Estimated green laps until the first modeled O/M/I band reaches zero; not a safety limit.",
                action="Use as a planning boundary and retain margin; it is not a recommendation to run the tire to zero.",
                group="local model",
            )
        )
    if tire_prediction.get("status") == "predicted" and predicted_pace is not None:
        tire_metrics.append(
            metric(
                "Capability pace",
                round(predicted_pace, 3),
                "s",
                "predicted",
                detail=f"Condition-matched local estimate from {int(eligible_observations or 0)} eligible observation(s).",
                action="Compare actual clean pace with this target; traffic and line still affect the result.",
                group="local model",
            )
        )
    if tire_prediction.get("status") == "predicted" and predicted_cost is not None:
        tire_metrics.append(
            metric(
                "Estimated tire-age cost",
                round(predicted_cost, 3),
                "s",
                "predicted",
                detail="Condition-matched pace cost associated with the modeled tire state.",
                action="Separate expected tire-age cost from avoidable execution loss when reviewing a lap.",
                group="local model",
            )
        )

    tire_takeaway_parts: list[str] = []
    if pace_delta is not None and representative_run_number:
        if abs(pace_delta) < 0.01:
            tire_takeaway_parts.append(
                f"Run {representative_run_number} pace was stable early-to-late"
            )
        else:
            direction = "slower" if pace_delta > 0 else "faster"
            tire_takeaway_parts.append(f"Run {representative_run_number} was {abs(pace_delta):.3f} s {direction} late")
    if material_load is not None:
        direction = "higher" if material_load[0] > 0 else "lower"
        tire_takeaway_parts.append(
            f"late {material_load[1]} was {abs(material_load[0]):.1f}% {direction}"
        )
    if predicted_life is not None and tire_prediction.get("status") == "predicted":
        tire_takeaway_parts.append(f"matched local life is {predicted_life:.1f} green laps")
    tire_takeaway = "; ".join(tire_takeaway_parts)
    if tire_takeaway:
        tire_takeaway = tire_takeaway[0].upper() + tire_takeaway[1:] + "."
    elif measured_bands:
        lowest_value, corner, band, _ = min(measured_bands, key=lambda item: item[0])
        tire_takeaway = f"The most-worn band was {corner} {band}, with {lowest_value:.1f}% remaining."
    elif (_finite(dynamics.get("front_wheel_lock_proxy_s")) or 0.0) > 0.0:
        front_lock_total = float(_finite(dynamics.get("front_wheel_lock_proxy_s")) or 0.0)
        tire_takeaway = (
            f"Run {dynamics_run_number} showed {front_lock_total:.2f} s of front wheel-speed divergence; inspect the highlighted brake zones before changing technique."
        )
    elif (_finite(dynamics.get("rear_wheelspin_proxy_s")) or 0.0) > 0.0:
        rear_spin_total = float(_finite(dynamics.get("rear_wheelspin_proxy_s")) or 0.0)
        tire_takeaway = (
            f"Run {dynamics_run_number} showed {rear_spin_total:.2f} s of rear wheel-speed divergence; inspect the highlighted throttle zones before changing technique."
        )
    else:
        tire_takeaway = "No tire-condition endpoint or comparable run trend is available."
    tire_attention = (
        (lowest_remaining is not None and lowest_remaining < 45.0)
        or (pace_delta is not None and pace_delta > 0.15)
        or any((_finite(dynamics.get(key)) or 0.0) > 0.0 for key in ("front_wheel_lock_proxy_s", "rear_wheelspin_proxy_s"))
    )
    tire_insight = {
        "key": "tires",
        "label": "Tires",
        "status": "available" if tire_metrics else "unavailable",
        "rating": "attention" if tire_attention else "stable" if tire_metrics else "unavailable",
        "takeaway": tire_takeaway,
        "metrics": tire_metrics,
        "evidence": (
            (["measured pit-service O/M/I wear"] if measured_bands else [])
            + (["clean-run early and late pace"] if pace_delta is not None else [])
            + (["recorded controls/load"] if strongest_load is not None else [])
            + (["wheel-speed and chassis motion"] if dynamics else [])
            + (["matched local tire observations"] if tire_prediction.get("status") == "predicted" else [])
        ),
        "unavailable_reasons": (
            [] if measured_bands else ["No confirmed measured O/M/I tire endpoint was recorded."]
        ) + (
            [str(tire_prediction.get("reason"))]
            if tire_prediction.get("status") == "unavailable" and tire_prediction.get("reason") else []
        ),
    }

    forecast = strategy.get("forecast") or {}
    green_burn = _finite(strategy.get("measured_green_fuel_gal_per_lap"))
    caution_burn = _finite(strategy.get("measured_caution_fuel_gal_per_lap"))
    range_laps = _finite(forecast.get("all_green_range_laps"))
    fuel_metrics: list[dict[str, Any]] = []
    if green_burn is not None:
        fuel_metrics.append(
            metric(
                "Green-lap use",
                round(green_burn, 4),
                "gal/lap",
                detail="Median fuel use across comparable green-flag run segments.",
                action="Use this rate for green-run range and reserve checks.",
                group="burn",
            )
        )
    if caution_burn is not None:
        fuel_metrics.append(
            metric(
                "Caution-lap use",
                round(caution_burn, 4),
                "gal/lap",
                detail="Median fuel use across caution-lap segments.",
                action="Use the observed caution mix only for retrospective context, not a future-caution forecast.",
                group="burn",
            )
        )
    if range_laps is not None:
        fuel_metrics.append(
            metric(
                "All-green range",
                round(range_laps, 1),
                "laps",
                "derived",
                detail="Range using observed run-start capacity, measured green burn, and the configured two-lap reserve.",
                action="Treat this as a fuel-feasibility boundary, not an optimal pit-call claim.",
                group="range",
            )
        )
    minimum_stops = _finite(forecast.get("minimum_stops_all_green"))
    if minimum_stops is not None:
        fuel_metrics.append(
            metric(
                "All-green minimum",
                int(round(minimum_stops)),
                "stops",
                "derived",
                detail="Minimum scheduled stops supported by observed capacity and green-lap burn.",
                action="Rules, tires, stage breaks, pit loss, traffic, and future cautions can require a different call.",
                group="strategy",
            )
        )
    observed_mix_range = _finite(forecast.get("observed_mix_range_laps"))
    if observed_mix_range is not None:
        fuel_metrics.append(
            metric(
                "Observed-mix range",
                round(observed_mix_range, 1),
                "laps",
                "derived",
                detail="Range if this race's measured green/caution mix repeated.",
                action="Do not treat this as a prediction of future cautions.",
                group="range",
            )
        )
    final_run = runs[-1] if runs else None
    scheduled_laps = _exact_scheduled_laps(race)
    final_run_end_lap = _finite(final_run.get("end_lap")) if final_run else None
    final_fuel_l = _finite((final_run.get("fuel") or {}).get("end_l")) if final_run else None
    green_burn_l = _finite(strategy.get("measured_green_fuel_l_per_lap"))
    finish_reserve_laps = (
        final_fuel_l / green_burn_l
        if final_fuel_l is not None
        and green_burn_l not in (None, 0.0)
        and scheduled_laps is not None
        and final_run_end_lap is not None
        and final_run_end_lap >= scheduled_laps
        else None
    )
    if finish_reserve_laps is not None:
        fuel_metrics.append(
            metric(
                "Finish reserve",
                round(finish_reserve_laps, 1),
                "green laps",
                "derived",
                detail="Finish fuel divided by measured green-lap burn.",
                action="Review whether the reserve was intentional before treating it as excess fuel.",
                tone="attention" if finish_reserve_laps > 3.0 else "positive",
                group="outcome",
            )
        )
    elif post_stop_margins:
        fuel_metrics.append(
            metric(
                "Tightest stop margin",
                round(min(post_stop_margins), 1),
                "green laps",
                "derived",
                signed=True,
                detail="Smallest projected post-stop range minus scheduled green laps remaining.",
                action="A negative value identifies a stop that could not reach scheduled distance at the measured green rate.",
                tone="attention" if min(post_stop_margins) < 0 else "positive",
                group="outcome",
            )
        )
    run_burns = [
        value / 3.785411784
        for run in runs
        if (value := _finite((run.get("fuel") or {}).get("green_l_per_lap"))) is not None
        and value > 0.0
    ]
    if len(run_burns) >= 2:
        burn_spread = max(run_burns) - min(run_burns)
        fuel_metrics.append(
            metric(
                "Run-to-run burn spread",
                round(burn_spread, 4),
                "gal/lap",
                "derived",
                detail="Highest comparable run burn minus the lowest.",
                action="A large spread warrants checking caution mix, traffic, and throttle behavior before changing the plan.",
                group="consistency",
            )
        )
    total_used = sum(
        value
        for run in runs
        if (value := _finite((run.get("fuel") or {}).get("used_gal"))) is not None
        and value > 0.0
    )
    if total_used > 0.0:
        fuel_metrics.append(
            metric(
                "Race fuel used",
                round(total_used, 2),
                "gal",
                detail="Sum of positive fuel-level change across analyzed runs.",
                group="outcome",
            )
        )
    total_added_l = sum(
        value
        for run in pit_runs
        if (value := _finite((run.get("pit_service") or {}).get("fuel_added_l"))) is not None
        and value > 0.0
    )
    if total_added_l > 0.0:
        fuel_metrics.append(
            metric(
                "Fuel added",
                round(total_added_l / 3.785411784, 2),
                "gal",
                detail="Confirmed positive fuel change during pit service.",
                action="Compare each stop's added fuel with its post-stop margin.",
                group="stops",
            )
        )
    if post_stop_margins and min(post_stop_margins) < 0:
        fuel_takeaway = f"Tightest post-stop all-green projection was {abs(min(post_stop_margins)):.1f} lap(s) short."
    elif finish_reserve_laps is not None:
        fuel_takeaway = f"Finish fuel equaled {finish_reserve_laps:.1f} green lap(s) at the measured burn rate."
    elif post_stop_margins:
        fuel_takeaway = f"Every post-stop all-green projection retained at least {min(post_stop_margins):.1f} lap(s)."
    elif not pit_runs and scheduled_laps is not None and range_laps is not None:
        headroom = range_laps - scheduled_laps
        fuel_takeaway = (
            f"The {scheduled_laps:.0f}-lap distance fit inside measured all-green range with {headroom:.1f} lap(s) of headroom."
            if headroom >= 0.0
            else f"The {scheduled_laps:.0f}-lap distance exceeded measured all-green range by {abs(headroom):.1f} lap(s)."
        )
    elif fuel_metrics:
        fuel_takeaway = "Fuel burn supports a range estimate, but no complete post-stop margin is available."
    else:
        fuel_takeaway = "Fuel use could not be measured."
    fuel_insight = {
        "key": "fuel",
        "label": "Fuel",
        "status": "available" if fuel_metrics else "unavailable",
        "rating": (
            "short" if post_stop_margins and min(post_stop_margins) < 0
            else "tight" if finish_reserve_laps is not None and finish_reserve_laps < 1.0
            else "safe" if finish_reserve_laps is not None or (post_stop_margins and min(post_stop_margins) >= 0)
            else "baseline" if fuel_metrics
            else "unavailable"
        ),
        "takeaway": fuel_takeaway,
        "metrics": fuel_metrics,
        "evidence": (
            (["recorded fuel-level change"] if fuel_metrics else [])
            + (["recorded post-stop fuel level"] if post_stop_margins else [])
            + (["recorded finish fuel"] if finish_reserve_laps is not None else [])
        ),
        "unavailable_reasons": [] if fuel_metrics else ["Fuel level or comparable lap exposure was unavailable."],
    }

    completed = sorted(
        [lap for lap in laps if lap.get("complete")],
        key=lambda item: _finite(item.get("lap")) or -1.0,
    )
    valid_reference_numbers = {
        int(number)
        for run in runs
        for raw_number in (run.get("valid_green_lap_numbers") or ())
        if (number := _finite(raw_number)) is not None
    }
    green_timed_laps = [
        lap
        for lap in completed
        if lap.get("flag_state") == "green"
        and (_finite(lap.get("lap_time_s")) or 0.0) > 0.0
        and (_finite(lap.get("pit_time_s")) or 0.0) < 1.0
    ]
    raw_race_times = [
        float(value)
        for lap in green_timed_laps
        if (value := _finite(lap.get("lap_time_s"))) is not None
    ]
    race_time_median = _median(raw_race_times)
    race_time_mad = (
        _median(abs(value - race_time_median) for value in raw_race_times)
        if race_time_median is not None
        else None
    )
    race_time_limit = max(0.75, 6.0 * (race_time_mad or 0.0))
    race_pace_laps = [
        lap
        for lap in green_timed_laps
        if (
            (value := _finite(lap.get("lap_time_s"))) is not None
            and race_time_median is not None
            and abs(value - race_time_median) <= race_time_limit
        )
    ]
    reference_laps = [
        lap
        for lap in green_timed_laps
        if int(_finite(lap.get("lap")) or -1) in valid_reference_numbers
    ]
    pace_laps = reference_laps if len(reference_laps) >= 2 else race_pace_laps
    pace_scope = "clean reference" if len(reference_laps) >= 2 else "representative race pace"
    usable_times = [
        float(value)
        for lap in pace_laps
        if (value := _finite(lap.get("lap_time_s"))) is not None
    ]
    start_position = _finite(race.get("starting_position"))
    finish_position = _finite(race.get("final_recorded_position"))
    racecraft_metrics: list[dict[str, Any]] = []
    position_evidence = start_position is not None and finish_position is not None
    if position_evidence:
        net_positions = int(round(start_position - finish_position))
        racecraft_metrics.append(
            metric(
                "Net positions",
                net_positions,
                "positions",
                signed=True,
                detail="Starting position minus final classified position.",
                action="Use the phase results below to identify where the net change occurred.",
                tone="positive" if net_positions > 0 else "attention" if net_positions < 0 else "neutral",
                group="result",
            )
        )

    positioned_laps = [
        lap for lap in completed
        if _finite((lap.get("position") or {}).get("start")) is not None
        and _finite((lap.get("position") or {}).get("end")) is not None
    ]
    restart_laps: list[Mapping[str, Any]] = []
    regular_green_laps: list[Mapping[str, Any]] = []
    previous_flag: str | None = None
    for lap in positioned_laps:
        flag = str(lap.get("flag_state") or "")
        if flag == "green":
            if previous_flag == "caution":
                restart_laps.append(lap)
            else:
                regular_green_laps.append(lap)
        previous_flag = flag

    phases: list[tuple[str, float]] = []
    if restart_laps:
        restart_change = sum(
            float(_finite((lap.get("position") or {}).get("start")) or 0.0)
            - float(_finite((lap.get("position") or {}).get("end")) or 0.0)
            for lap in restart_laps
        )
        phases.append(("Restarts", restart_change))
    if regular_green_laps:
        buckets: dict[str, list[Mapping[str, Any]]] = {"Early": [], "Middle": [], "Late": []}
        labels = tuple(buckets)
        for index, lap in enumerate(regular_green_laps):
            bucket_index = min(2, index * 3 // len(regular_green_laps))
            buckets[labels[bucket_index]].append(lap)
        for label in labels:
            phase_laps = buckets[label]
            if not phase_laps:
                continue
            first_position = _finite((phase_laps[0].get("position") or {}).get("start"))
            last_position = _finite((phase_laps[-1].get("position") or {}).get("end"))
            if first_position is not None and last_position is not None:
                phases.append((label, first_position - last_position))
    for phase_label, phase_change in phases:
        racecraft_metrics.append(
            metric(
                phase_label,
                int(round(phase_change)),
                "positions",
                "derived",
                signed=True,
                detail="Position change across this race phase; positive means positions gained.",
                action=(
                    "Compare the relevant laps in Telemetry to preserve what worked."
                    if phase_change > 0
                    else "Compare these laps with the strongest phase for traffic, line, and execution differences."
                    if phase_change < 0
                    else "No net position change was detected in this phase."
                ),
                tone="positive" if phase_change > 0 else "attention" if phase_change < 0 else "neutral",
                group="phases",
            )
        )
    strongest_phase = max(phases, key=lambda item: item[1], default=None)
    weakest_phase = min(phases, key=lambda item: item[1], default=None)
    if usable_times:
        racecraft_metrics.append(
            metric(
                "Fastest clean" if pace_scope == "clean reference" else "Fastest race pace",
                round(min(usable_times), 3),
                "s",
                detail=(
                    "Fastest damage-, traffic-, restart-, off-track-, and pit-screened green lap."
                    if pace_scope == "clean reference"
                    else "Fastest lap inside the robust green-race pace band; traffic context may remain."
                ),
                action="Use this lap as the personal execution reference for this race." if pace_scope == "clean reference" else "Compare its controls with the median race-pace lap before copying it.",
                group="pace",
            )
        )
        racecraft_metrics.append(
            metric(
                "Median clean" if pace_scope == "clean reference" else "Median race pace",
                round(float(_median(usable_times) or 0.0), 3),
                "s",
                detail=f"Middle {pace_scope} lap across {len(usable_times)} lap(s).",
                action="The gap to fastest indicates how consistently peak pace was reproduced.",
                group="pace",
            )
        )
    if len(usable_times) >= 2:
        pace_spread = statistics.pstdev(usable_times)
        racecraft_metrics.append(
            metric(
                "Clean-lap variation" if pace_scope == "clean reference" else "Race-pace variation",
                round(pace_spread, 3),
                "s",
                "derived",
                detail=f"Population standard deviation across {len(usable_times)} {pace_scope} laps.",
                action="Lower variation means the available pace was reproduced more consistently.",
                tone="attention" if pace_spread > 0.5 else "positive",
                group="consistency",
            )
        )
    control_laps = pace_laps or race_pace_laps

    def control_values(key: str) -> list[float]:
        return [
            value
            for lap in control_laps
            if (value := _finite((lap.get("controls") or {}).get(key))) is not None
        ]

    throttle_means = control_values("throttle_mean")
    brake_peaks = control_values("brake_max")
    brake_steer_overlap = control_values("brake_steer_overlap_s")
    steering_corrections = control_values("steering_corrections")
    steering_angles = control_values("steering_abs_mean_rad")
    top_speeds = [
        value
        for lap in control_laps
        if (value := _finite((lap.get("speed") or {}).get("maximum_mph"))) is not None
    ]
    minimum_speeds = [
        value
        for lap in control_laps
        if (value := _finite((lap.get("speed") or {}).get("minimum_mph"))) is not None
    ]
    if throttle_means:
        racecraft_metrics.append(
            metric(
                "Throttle commitment",
                round(float(_median(throttle_means) or 0.0) * 100.0, 1),
                "% average",
                "derived",
                detail=f"Median throttle fraction across {len(throttle_means)} {pace_scope} lap(s).",
                action="Compare laps above and below this value with lap time; more throttle time is useful only when the lap is also controlled and faster.",
                group="driver execution",
            )
        )
    if brake_peaks:
        racecraft_metrics.append(
            metric(
                "Brake peak",
                round(float(_median(brake_peaks) or 0.0) * 100.0, 1),
                "%",
                "derived",
                detail=f"Median lap-level peak brake input across {len(brake_peaks)} {pace_scope} lap(s).",
                action="Use the aligned brake trace to identify where peak pressure or release differs from the fastest reference.",
                group="driver execution",
            )
        )
    if brake_steer_overlap:
        racecraft_metrics.append(
            metric(
                "Brake / steer overlap",
                round(float(_median(brake_steer_overlap) or 0.0), 2),
                "s/lap",
                "derived",
                detail="Median time per lap with simultaneous braking and material steering input.",
                action="Compare overlap locations with the faster laps; the measurement describes technique and does not prove that overlap is harmful.",
                group="driver execution",
            )
        )
    if steering_corrections:
        racecraft_metrics.append(
            metric(
                "Steering corrections",
                round(float(_median(steering_corrections) or 0.0), 1),
                "/ lap",
                "derived",
                detail="Median threshold-crossing steering reversals per lap.",
                action="Use the steering trace to find repeatable correction clusters, then compare those zones with the fastest stable lap.",
                group="driver execution",
            )
        )
    if steering_angles:
        racecraft_metrics.append(
            metric(
                "Average steering load",
                round(math.degrees(float(_median(steering_angles) or 0.0)), 1),
                "deg",
                "derived",
                detail="Median lap-level mean absolute steering-wheel angle.",
                action="Compare this value by race phase; an increase can reflect line, traffic, or correction changes and is not a balance diagnosis by itself.",
                group="driver execution",
            )
        )
    if top_speeds and minimum_speeds:
        racecraft_metrics.append(
            metric(
                "Speed envelope",
                f"{float(_median(minimum_speeds) or 0.0):.1f}-{max(top_speeds):.1f}",
                "mph",
                "derived",
                detail="Median lap minimum to highest recorded maximum across the selected pace laps.",
                action="Use corner minimum speed with entry and exit traces; top speed alone does not identify where lap time was gained.",
                group="driver execution",
            )
        )
    long_run_delta = (
        _finite((representative_run.get("pace") or {}).get("early_to_late_delta_s"))
        if representative_run is not None else None
    )
    if long_run_delta is not None:
        racecraft_metrics.append(
            metric(
                "Long-run change",
                round(long_run_delta, 3),
                "s",
                "derived",
                signed=True,
                detail=f"Early-to-late pace change in run {representative_run_number}, the longest comparable run.",
                action="Use with tire and driving-load findings before attributing the change to technique.",
                tone="attention" if long_run_delta > 0.0 else "positive" if long_run_delta < 0.0 else "neutral",
                group="pace",
            )
        )
    incident_points = _finite((damage.get("incident_points") or {}).get("positive_delta"))
    if incident_points is not None:
        racecraft_metrics.append(
            metric(
                "Incident points",
                int(round(incident_points)),
                detail="Total positive incident-count change during the race.",
                action="Use the incident timeline for context; points alone do not identify contact, damage, or fault.",
                tone="attention" if incident_points > 0 else "positive",
                group="execution",
            )
        )
    if weakest_phase is not None and weakest_phase[1] < 0:
        racecraft_takeaway = f"{weakest_phase[0]} running lost {abs(int(round(weakest_phase[1])))} position(s); compare those laps with the strongest phase."
    elif strongest_phase is not None and strongest_phase[1] > 0:
        racecraft_takeaway = f"{strongest_phase[0]} running gained {int(round(strongest_phase[1]))} position(s), the strongest phase of the race."
    elif position_evidence:
        net = int(round(start_position - finish_position))
        racecraft_takeaway = f"Start-to-finish position change was {net:+d}."
    elif usable_times:
        racecraft_takeaway = f"Fastest clean green lap was {min(usable_times):.3f} s."
    else:
        racecraft_takeaway = "No position progression or clean pace is available."
    net_result = int(round(start_position - finish_position)) if position_evidence else None
    racecraft_insight = {
        "key": "racecraft",
        "label": "Racecraft & pace",
        "status": "available" if racecraft_metrics else "unavailable",
        "rating": (
            "gain" if net_result is not None and net_result > 0
            else "loss" if net_result is not None and net_result < 0
            else "stable" if position_evidence
            else "pace-only" if racecraft_metrics
            else "unavailable"
        ),
        "takeaway": racecraft_takeaway,
        "metrics": racecraft_metrics,
        "pace_sample": {
            "scope": pace_scope if usable_times else "unavailable",
            "lap_numbers": [lap.get("lap") for lap in pace_laps],
            "lap_count": len(pace_laps),
            "raw_green_timed_laps": len(green_timed_laps),
            "robust_race_pace_laps": len(race_pace_laps),
            "valid_reference_laps": len(reference_laps),
        },
        "evidence": (
            (["player position endpoints"] if position_evidence else [])
            + (["lap-by-lap player position and flags"] if phases else [])
            + ([f"{pace_scope} timing and controls"] if usable_times else [])
        ),
        "unavailable_reasons": (
            ([] if position_evidence else ["Start and finish position endpoints were not both recorded."])
            + ([] if phases else ["Lap-by-lap position phases were unavailable."])
        ),
    }
    return [pit_insight, tire_insight, fuel_insight, racecraft_insight]


def _race_evidence_timeline(
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a compact, companion-app-friendly race-control timeline.

    The timeline preserves the distinction between recorded state, derived
    grouping, requested service, and service confirmed by fuel/tire evidence.
    It intentionally uses routine-analysis SessionTime rather than pretending
    that downsampled lap indices are native IBT record indices.
    """

    race_laps = [
        lap
        for lap in laps
        if (_finite(lap.get("lap")) or 0.0) > 0.0
        and _finite(lap.get("start_time")) is not None
    ]
    events: list[dict[str, Any]] = []
    if race_laps:
        first = race_laps[0]
        events.append(
            {
                "event_type": "race_start",
                "session_time_s": _round(first.get("start_time")),
                "start_lap": first.get("lap"),
                "end_lap": first.get("lap"),
                "evidence_class": "measured",
                "source_channels": ["SessionTime", "Lap"],
            }
        )

    caution_laps = [
        lap
        for lap in race_laps
        if (_finite(lap.get("caution_fraction")) or 0.0) >= 0.02
    ]
    caution_groups: list[list[Mapping[str, Any]]] = []
    for lap in caution_laps:
        lap_number = int(_finite(lap.get("lap")) or 0)
        if not caution_groups:
            caution_groups.append([lap])
            continue
        previous_lap = int(_finite(caution_groups[-1][-1].get("lap")) or 0)
        if lap_number <= previous_lap + 1:
            caution_groups[-1].append(lap)
        else:
            caution_groups.append([lap])
    for group in caution_groups:
        start_lap = int(_finite(group[0].get("lap")) or 0)
        end_lap = int(_finite(group[-1].get("lap")) or start_lap)
        equivalents = sum(
            _finite(lap.get("caution_fraction")) or 0.0 for lap in group
        )
        events.append(
            {
                "event_type": "caution_period",
                "session_time_s": _round(group[0].get("start_time")),
                "end_session_time_s": _round(group[-1].get("end_time")),
                "start_lap": start_lap,
                "end_lap": end_lap,
                "caution_lap_equivalents": _round(equivalents, 2),
                "evidence_class": "derived_from_recorded_state",
                "source_channels": ["SessionFlags", "SessionTime", "Lap"],
                "note": "Consecutive sampled caution exposure grouped into one period; official scoring remains a separate cross-check.",
            }
        )

    service_count = 0
    confirmed_service_count = 0
    for run in runs:
        service = run.get("pit_service")
        if not isinstance(service, Mapping) or not service:
            continue
        service_count += 1
        confirmed_tires = sorted(
            {
                str(tire).upper()
                for tire in service.get("tires_changed_observed", ()) or ()
                if str(tire).upper() in TIRES
            }
        )
        requested = service.get("requested_service")
        requested = requested if isinstance(requested, Mapping) else {}
        requested_tires = [
            tire
            for tire in TIRES
            if requested.get(f"{tire}_tire_change_requested") is True
        ]
        fuel_added = _finite(service.get("fuel_added_l"))
        requested_fuel = _finite(service.get("requested_fuel_add_l"))
        has_confirmed = bool(confirmed_tires) or (
            fuel_added is not None and fuel_added > 0.01
        )
        has_request = bool(requested_tires) or (
            requested_fuel is not None and requested_fuel > 0.01
        )
        if has_confirmed:
            confirmation = "confirmed_consumable_service"
            evidence_class = "measured_confirmed"
            confirmed_service_count += 1
        elif has_request:
            confirmation = "request_only"
            evidence_class = "measured_request_unconfirmed"
        else:
            confirmation = "service_detected_consumables_unconfirmed"
            evidence_class = "measured_partial"
        post_run = bool(run.get("ended_with_post_run_service"))
        events.append(
            {
                "event_type": "post_run_service" if post_run else "pit_service",
                "session_time_s": _round(
                    _first_not_none(
                        service.get("start_time"), run.get("end_time_s")
                    )
                ),
                "end_session_time_s": _round(service.get("end_time")),
                "start_lap": run.get("end_lap"),
                "end_lap": run.get("end_lap"),
                "after_run": run.get("run_number"),
                "under_caution_at_run_end": bool(run.get("ended_under_caution")),
                "confirmed_tires": confirmed_tires,
                "requested_tires": requested_tires,
                "confirmed_fuel_added_l": _round(fuel_added),
                "requested_fuel_add_l": _round(requested_fuel),
                "confirmation": confirmation,
                "evidence_class": evidence_class,
                "source_channels": [
                    "OnPitRoad",
                    "PlayerCarInPitStall",
                    "PitstopActive",
                    "FuelLevel",
                    "PitSvFuel",
                    "PitSvFlags",
                    "*TiresUsed",
                    "*odometer",
                ],
            }
        )

    if race_laps:
        last = race_laps[-1]
        events.append(
            {
                "event_type": "race_end",
                "session_time_s": _round(last.get("end_time")),
                "start_lap": last.get("lap"),
                "end_lap": last.get("lap"),
                "evidence_class": "measured_recording_end",
                "source_channels": ["SessionTime", "Lap"],
                "note": "This marks the end of the recorded race telemetry, which may differ from official classification time.",
            }
        )

    order = {
        "race_start": 0,
        "caution_period": 1,
        "pit_service": 2,
        "post_run_service": 3,
        "race_end": 4,
    }
    events.sort(
        key=lambda item: (
            _finite(item.get("session_time_s"))
            if _finite(item.get("session_time_s")) is not None
            else float("inf"),
            order.get(str(item.get("event_type")), 99),
        )
    )
    for index, event in enumerate(events, 1):
        event["event_id"] = f"race-event-{index:03d}"

    return {
        "schema_version": 1,
        "time_basis": "recorded SessionTime seconds from the routine analysis table",
        "index_semantics": "Timeline events do not claim native IBT record indices; use native event search for exact source records.",
        "summary": {
            "event_count": len(events),
            "caution_periods_from_sampled_flags": len(caution_groups),
            "service_events": service_count,
            "confirmed_consumable_service_events": confirmed_service_count,
        },
        "events": events,
        "limitations": [
            "Caution periods are derived from sampled SessionFlags and must be reconciled with official results.",
            "Requested pit service is reported separately from fuel or tire service confirmed by telemetry.",
            "Recording start/end and player position do not replace official classification data.",
        ],
    }


def _coaching_signals(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    for run in runs:
        slope = _finite(run.get("pace", {}).get("green_lap_time_slope_s_per_lap"))
        delta = _finite(run.get("pace", {}).get("early_to_late_delta_s"))
        brake_delta = _finite(run.get("driving_load", {}).get("early_brake_vs_late_percent"))
        steer_delta = _finite(run.get("driving_load", {}).get("early_steer_vs_late_percent"))
        dynamics = run.get("vehicle_dynamics") or {}
        wheel_lock = _finite(dynamics.get("braking_wheel_lock_proxy_s"))
        front_lock = _finite(dynamics.get("front_wheel_lock_proxy_s"))
        rear_spin = _finite(dynamics.get("rear_wheelspin_proxy_s"))
        abs_active = _finite(dynamics.get("abs_active_s"))
        tire = run.get("tire_observation") or {}
        lowest_tire = tire.get("lowest_remaining_tire")
        if slope is not None and slope > 0.06:
            evidence = [f"green-lap trend +{slope:.3f} s/lap"]
            corroborated_load = False
            if delta is not None:
                evidence.append(f"late-run average {delta:+.2f} s versus early run")
            if brake_delta is not None and brake_delta > 8:
                evidence.append(f"early brake-energy proxy {brake_delta:.0f}% above late run")
                corroborated_load = True
            if steer_delta is not None and steer_delta > 8:
                evidence.append(f"early steering-work proxy {steer_delta:.0f}% above late run")
                corroborated_load = True
            if wheel_lock is not None and wheel_lock >= 0.10:
                evidence.append(f"wheel-lock proxy present for {wheel_lock:.2f} s")
            if rear_spin is not None and rear_spin >= 0.10:
                evidence.append(f"rear-wheelspin proxy present for {rear_spin:.2f} s")
            if abs_active is not None and abs_active >= 0.10:
                evidence.append(f"ABS active for {abs_active:.2f} s")
            signals.append(
                {
                    "priority": "high",
                    "run_number": run["run_number"],
                    "finding": "Pace degraded materially through the green-flag run.",
                    "coaching": (
                        "Reduce the corroborated early-run tire load, then use aligned Garage61 entry and brake-release targets after sync."
                        if corroborated_load
                        else "Review traffic, incidents, line, damage, and changing conditions before changing technique; the pace trend alone does not identify a tire-management cause."
                    ),
                    "evidence": evidence,
                    "inference": (
                        "corroborated tire/load-management issue is plausible, but not proven"
                        if corroborated_load
                        else "cause is unresolved; tire management is only one candidate"
                    ),
                }
            )
        if lowest_tire:
            remaining = tire.get("lowest_remaining_percent")
            evidence = ["discrete tire reading captured after pit service"]
            if str(lowest_tire).upper() in {"LF", "RF"} and front_lock is not None and front_lock >= 0.10:
                evidence.append(
                    f"uncalibrated front wheel-speed divergence proxy present for {front_lock:.2f} s"
                )
            if str(lowest_tire).upper() in {"LR", "RR"} and rear_spin is not None and rear_spin >= 0.10:
                evidence.append(
                    f"uncalibrated rear wheel-speed divergence proxy present for {rear_spin:.2f} s"
                )
            signals.append(
                {
                    "priority": "medium",
                    "run_number": run["run_number"],
                    "finding": f"{lowest_tire} was the most worn measured tire ({remaining}% remaining).",
                    "coaching": "Match this wear pattern against corner-entry braking, steering work, and load-zone deltas before assigning cause.",
                    "evidence": evidence,
                    "inference": (
                        "wear timing and cause within the run are inferred, not directly measured; "
                        "wheel-speed divergence remains diagnostic until calibrated for oval stagger and corner radius"
                    ),
                }
            )
    if not signals:
        signals.append(
            {
                "priority": "medium",
                "finding": "Telemetry did not expose enough measured degradation or post-stop tire evidence for a strong tire-management conclusion.",
                "coaching": "Keep disk telemetry enabled and return cleanly to pit service after the run to capture tire readings.",
                "evidence": [],
                "inference": None,
            }
        )
    return signals


def _fallback_telemetry_digest(table: TelemetryTable) -> str:
    """Hash normalized decoded content when no authoritative source SHA exists."""

    digest = hashlib.sha256()
    for name in sorted(table.channels):
        digest.update(name.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\x00")
        digest.update(
            json.dumps(
                table.channels[name],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8", errors="surrogatepass")
        )
        digest.update(b"\x1e")
    digest.update(
        json.dumps(
            table.session_info,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8", errors="surrogatepass")
    )
    return digest.hexdigest()


def _race_grades(
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    race_summary: Mapping[str, Any],
    damage_repair: Mapping[str, Any],
) -> dict[str, Any]:
    """Create strict, race-specific execution grades from recorded evidence.

    The local grade deliberately blocks A+ without comparable field-strength
    evidence.  It also leaves strategy and racecraft unavailable rather than
    grading outcomes that the current telemetry cannot attribute to the driver.
    """

    def letter(score: float) -> str:
        thresholds = (
            (97, "A+"), (94, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"),
            (77, "C+"), (73, "C"), (70, "C-"), (67, "D+"), (63, "D"), (60, "D-"),
        )
        return next((grade for minimum, grade in thresholds if score >= minimum), "F")

    def damage_context_excluded(context: Any) -> bool:
        if not isinstance(context, Mapping):
            return False
        raw_reason_codes = (
            context.get("exclusion_reason_codes")
            or context.get("reason_codes")
            or ()
        )
        if isinstance(raw_reason_codes, str):
            reason_codes: Sequence[Any] = (raw_reason_codes,)
        elif isinstance(raw_reason_codes, Sequence):
            reason_codes = raw_reason_codes
        else:
            # A malformed non-empty reason field remains conservative evidence
            # for exclusion, but is never iterated as a string or mapping.
            reason_codes = (raw_reason_codes,) if raw_reason_codes else ()
        return (
            context.get("automatic_coaching_reference_eligible") is False
            or any(str(reason).strip() for reason in reason_codes)
        )

    # The deterministic post-race analyzer does not currently receive a usable
    # comparable field-strength cohort.  Keep this gate explicit so A+ cannot
    # appear accidentally if a component formula changes later.
    a_plus_gate = {
        "eligible": False,
        "status": "blocked_missing_external_comparable_field_strength",
        "required_evidence": (
            "A usable condition- and setup-comparable field-strength cohort plus "
            "high-confidence evidence across every material category."
        ),
        "available_evidence": "Local recorded telemetry only.",
    }

    unavailable_categories: dict[str, dict[str, Any]] = {
        "pace": {
            "key": "pace",
            "label": "Pace execution",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["pace"],
            "reason": "At least three complete, clean green laps are required.",
        },
        "consistency": {
            "key": "consistency",
            "label": "Consistency and execution",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["consistency"],
            "reason": "At least three complete, clean green laps are required.",
        },
        "tire_management": {
            "key": "tire_management",
            "label": "Tire management",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["tire_management"],
            "reason": "No damage-screened run exposed a usable clean-lap pace trend.",
        },
        "strategy": {
            "key": "strategy",
            "label": "Pit and strategy execution",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["strategy"],
            "reason": (
                "Pit count, fuel arithmetic, tow, and repair records do not establish "
                "whether a pit decision was well executed."
            ),
        },
        "racecraft": {
            "key": "racecraft",
            "label": "Racecraft and adaptability",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["racecraft"],
            "reason": (
                "Start-to-finish position and incident records do not establish field "
                "strength, avoidability, traffic context, or driver-controlled racecraft."
            ),
        },
    }

    clean = []
    previous_flag: str | None = None
    for lap in sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0):
        exclusion_reasons = _corner_lap_exclusion_reasons(lap, previous_flag)
        if not exclusion_reasons and (_finite(lap.get("lap_time_s")) or 0) > 0:
            clean.append(lap)
        previous_flag = str(lap.get("flag_state") or "")
    times = [float(lap["lap_time_s"]) for lap in clean]
    categories: list[dict[str, Any]] = []
    if len(times) >= 3:
        best = min(times)
        median = statistics.median(times)
        pace_gap = max(0.0, median / best - 1.0)
        pace_score = min(96.0, max(45.0, 96.0 - pace_gap * 900.0))
        categories.append({
            "key": "pace", "label": "Pace execution", "score": _round(pace_score, 1),
            "grade": letter(pace_score), "evidence_type": "derived",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["pace"],
            "explanation": f"The median clean lap was {median - best:.3f} s from the fastest clean lap across {len(times)} usable laps.",
            "improvement": "Reduce the repeatable losses shown in the slowest load zones before chasing a more aggressive target.",
            "limitations": "Local telemetry has no field-strength or external reference, so pace is capped below A+.",
        })
        unavailable_categories.pop("pace", None)
        mean = statistics.fmean(times)
        deviation = statistics.pstdev(times) / mean if mean > 0 else 1.0
        consistency_score = min(97.0, max(45.0, 98.0 - deviation * 1200.0))
        if not a_plus_gate["eligible"]:
            consistency_score = min(consistency_score, 96.9)
        categories.append({
            "key": "consistency", "label": "Consistency and execution", "score": _round(consistency_score, 1),
            "grade": letter(consistency_score), "evidence_type": "derived",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["consistency"],
            "explanation": f"Clean-lap variation was {deviation * 100:.2f}% across {len(times)} usable laps.",
            "improvement": "Make brake release and throttle pickup repeatable at the high-variation sections.",
            "limitations": (
                "Caution, pit, traffic-screened, and recorded repair-confounded laps are "
                "excluded when the channels support that screening; A+ is blocked without "
                "external comparable field-strength evidence."
            ),
        })
        unavailable_categories.pop("consistency", None)

    eligible_runs = [
        run
        for run in runs
        if not damage_context_excluded(run.get("damage_repair_context"))
    ]
    slopes = [
        _finite((run.get("pace") or {}).get("green_lap_time_slope_s_per_lap"))
        for run in eligible_runs
    ]
    slopes = [value for value in slopes if value is not None]
    if slopes:
        slope = statistics.median(slopes)
        management_score = min(94.0, max(45.0, 92.0 - max(0.0, slope) * 45.0 + min(0.0, slope) * -12.0))
        categories.append({
            "key": "tire_management", "label": "Tire management", "score": _round(management_score, 1),
            "grade": letter(management_score), "evidence_type": "proxy",
            "weight_percent": RACE_GRADE_CATEGORY_WEIGHTS["tire_management"],
            "explanation": (
                f"The median damage-screened clean-run pace trend was {slope:+.3f} s per "
                f"lap across {len(slopes)} eligible run(s)."
            ),
            "improvement": "Use the tire-stress map to reduce early-run braking and steering load where pace fell away.",
            "limitations": "Pace trend and control load are proxies; tire wear is measured only at recorded service endpoints.",
        })
        unavailable_categories.pop("tire_management", None)

    # Keep these parameters in the stable call contract.  Their current fields
    # provide useful race context, but not enough attributable evidence to score
    # strategy or racecraft.  In particular, tow/repair is never a strategy
    # penalty and raw finishing-position change is never a racecraft grade.
    _ = race_summary, damage_repair

    def rubric_payload() -> dict[str, Any]:
        available_weight = sum(
            RACE_GRADE_CATEGORY_WEIGHTS[item["key"]]
            for item in categories
        )
        normalized = {
            item["key"]: _round(
                RACE_GRADE_CATEGORY_WEIGHTS[item["key"]] / available_weight,
                6,
            )
            for item in categories
        } if available_weight else {}
        return {
            "version": RACE_GRADE_RUBRIC_VERSION,
            "category_weights_percent": dict(RACE_GRADE_CATEGORY_WEIGHTS),
            "normalization": "Configured weights are renormalized across available categories only; unavailable categories contribute no neutral score.",
            "available_weight_percent": available_weight,
            "normalized_available_weights": normalized,
            "a_plus_gate": dict(a_plus_gate),
        }

    if len(categories) < 2:
        return {
            "status": "insufficient_comparable_laps", "overall_grade": None,
            "categories": categories,
            "message": "At least three usable green laps and two supported categories are required for a race grade.",
            "rubric_version": RACE_GRADE_RUBRIC_VERSION,
            "rubric": rubric_payload(),
            "unavailable_categories": list(unavailable_categories.values()),
        }

    rubric = rubric_payload()
    effective_weights = rubric["normalized_available_weights"]
    for item in categories:
        item["effective_weight"] = effective_weights[item["key"]]
    weighted_score = sum(
        float(item["score"]) * effective_weights[item["key"]]
        for item in categories
    )
    overall_score = weighted_score
    applied_gates: list[dict[str, Any]] = []
    if len(times) < 10:
        overall_score = min(overall_score, 89.9)
        applied_gates.append({
            "gate": "minimum_usable_laps_for_A_range",
            "threshold": 10,
            "observed": len(times),
            "maximum_score": 89.9,
        })
    if not a_plus_gate["eligible"]:
        overall_score = min(overall_score, 96.9)
        applied_gates.append({
            "gate": "external_comparable_field_strength_required_for_A_plus",
            "maximum_score": 96.9,
            "status": a_plus_gate["status"],
        })
    return {
        "status": "graded", "overall_score": _round(overall_score, 1), "overall_grade": letter(overall_score),
        "categories": categories,
        "rubric_version": RACE_GRADE_RUBRIC_VERSION,
        "rubric": rubric,
        "weighted_score_before_gates": _round(weighted_score, 1),
        "applied_gates": applied_gates,
        "unavailable_categories": list(unavailable_categories.values()),
        "standard": (
            "Strict race-specific execution grade using versioned 30/20/20/15/15 "
            "category weights; only available categories are normalized into the total, "
            "and A+ requires external comparable field-strength evidence."
        ),
    }


def analyzer_bundle_sha256() -> str:
    """Hash every local module whose logic contributes to the core analysis.

    This keeps both the artifact identity and the persistent workflow cache from
    silently reusing results after a helper module changes.
    """

    digest = hashlib.sha256()
    module_dir = Path(__file__).resolve().parent
    for name in ANALYZER_SOURCE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update((module_dir / name).read_bytes())
        digest.update(b"\x1e")
    return digest.hexdigest()


def _analysis_fingerprint(
    table: TelemetryTable,
    source_fingerprints: Sequence[Mapping[str, Any]],
    analysis_profile: Mapping[str, Any],
) -> str:
    hashes = [
        str(item.get("sha256")).lower()
        for item in source_fingerprints
        if isinstance(item, Mapping) and len(str(item.get("sha256") or "")) == 64
    ]
    content_identity: Mapping[str, Any] = (
        {"source_sha256": hashes}
        if hashes
        else {"decoded_telemetry_sha256": _fallback_telemetry_digest(table)}
    )
    return hashlib.sha256(
        json.dumps(
            {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "profile_version": ANALYSIS_PROFILE_VERSION,
                "analyzer_sha256": analyzer_bundle_sha256(),
                "profile": dict(analysis_profile),
                **content_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:24]


PACE_ATTRIBUTION_SCHEMA_VERSION = 1

#: Corners fall back to even distance slices when the track profile detected no
#: load zones. Twelve is a compromise: fine enough to localize a loss to a part
#: of the track a driver can recognize, coarse enough that each slice still
#: holds enough samples to time reliably.
FALLBACK_SEGMENT_COUNT = 12


def _field_pace(table: TelemetryTable) -> dict[str, Any]:
    """Observed pace and gaps for the rest of the field.

    `COMPETITOR-PACE-001`. Every other number in this analysis describes a
    driver racing alone; this is the only one that measures anybody else, and
    it is what a finishing-position claim would have to rest on.
    """

    if not table.has("CarIdxLapDistPct"):
        return {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "car_position_channel_not_recorded",
            "cars": [],
            "relative": [],
        }
    player_index = None
    for value in table.get("PlayerCarIdx", default=None):
        candidate = _finite(value)
        if candidate is not None:
            player_index = candidate
            break
    report = competitor_pace.analyze_field(
        lap_dist_pct_by_car=table.get("CarIdxLapDistPct", default=None),
        session_time_s=table.get("SessionTime", "SessionTimeOfDay", default=None),
        player_car_index=player_index,
    )
    payload = report.to_payload()
    payload["schema_version"] = 1
    return payload


def _clean_lap_traces(
    table: TelemetryTable, laps: Sequence[Mapping[str, Any]]
) -> tuple[list[Any], list[int]]:
    """Resample every clean green racing lap onto the shared distance grid.

    Shared by pace attribution and strategy planning so both reason about the
    same set of laps; a pit-loss reference drawn from a different lap pool than
    the coaching would make the two quietly incomparable.
    """

    if not table.has("LapDistPct") or not table.has("SessionTime") or not table.has("Lap"):
        return [], []

    previous_flag: str | None = None
    eligible_numbers: set[int] = set()
    for lap in sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0):
        number = _finite(lap.get("lap"))
        reasons = _corner_lap_exclusion_reasons(lap, previous_flag)
        previous_flag = str(lap.get("flag_state") or "")
        if not reasons and number is not None and number >= 0:
            eligible_numbers.add(int(number))
    if not eligible_numbers:
        return [], []

    lap_channel = table.get("Lap", default=None)
    groups: MutableMapping[int, list[int]] = defaultdict(list)
    for index, raw_lap in enumerate(lap_channel):
        value = _finite(raw_lap)
        if value is not None and int(value) in eligible_numbers:
            groups[int(value)].append(index)

    distance = table.get("LapDistPct", default=None)
    times = table.get("SessionTime", "SessionTimeOfDay", default=None)
    speed = table.get("Speed", default=None)
    traces = [
        lap_reference.build_lap_trace(
            distance, times, indices, speed_mps=speed, lap_number=number
        )
        for number, indices in sorted(groups.items())
    ]
    return traces, sorted(eligible_numbers)


def _strategy_planning(
    table: TelemetryTable,
    laps: Sequence[Mapping[str, Any]],
    race_summary: Mapping[str, Any],
    strategy: Mapping[str, Any],
    field_pace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What a stop costs this driver, and which plan is quickest.

    `PIT-LOSS-001`, `STRATEGY-SIM-001`. The existing strategy block answers
    whether the fuel reaches; this answers which plan wins, over measured
    quantities only. Every input is something this session recorded: green
    pace and the degradation slope from the laps, burn and capacity from the
    fuel trace, and the cost of a stop from the driver's own stops.
    """

    unavailable = {
        "schema_version": 1,
        "status": "unavailable",
        "pit_loss": None,
        "plan_comparison": None,
    }
    traces, _eligible = _clean_lap_traces(table, laps)
    usable = [
        trace
        for trace in traces
        if trace.status == lap_reference.STATUS_USABLE
        and trace.covered_time_s is not None
    ]
    if not usable:
        return {**unavailable, "reason": "no_clean_green_reference_lap"}

    reference = min(usable, key=lambda trace: trace.covered_time_s)
    pit_report = pit_loss.measure_pit_loss(
        session_time_s=table.get("SessionTime", "SessionTimeOfDay", default=None),
        on_pit_road=table.get("OnPitRoad", default=False),
        lap_dist_pct=table.get("LapDistPct", default=None),
        speed_m_s=table.get("Speed", default=None),
        lap_numbers=table.get("Lap", default=None),
        reference=reference,
    )

    base_lap_s = _median([trace.covered_time_s for trace in usable])
    forecast = strategy.get("forecast") or {}
    comparison = strategy_model.compare_strategies(
        race_laps=race_summary.get("scheduled_laps"),
        base_lap_s=base_lap_s,
        degradation_s_per_lap=strategy.get("median_green_lap_degradation_s_per_lap"),
        pit_loss_s=pit_report.median_loss_s,
        fuel_capacity_l=forecast.get("maximum_recorded_run_start_fuel_l"),
        green_burn_l_per_lap=strategy.get("measured_green_fuel_l_per_lap"),
    )

    position_context = None
    if comparison.margin_s is not None:
        gaps = [
            item.get("final_gap_s")
            for item in ((field_pace or {}).get("relative") or ())
            if isinstance(item, Mapping)
        ]
        priced = strategy_model.positions_from_margin(comparison.margin_s, gaps)
        position_context = priced.to_payload() if priced is not None else None

    return {
        "schema_version": 1,
        "status": comparison.status,
        "reason": comparison.reason,
        "reference_lap": reference.lap_number,
        "position_context": position_context,
        "representative_green_lap_s": _round(base_lap_s, 3),
        "pit_loss": pit_report.to_payload(),
        "plan_comparison": comparison.to_payload(),
        "limitations": [
            "The ranking is a race-time comparison, not a finishing-position "
            "prediction: the field's pace and the driver's track position are "
            "not modelled.",
            "Capacity is the largest fuel load actually observed at a run start, "
            "which may be below the car's legal maximum.",
            "Degradation is the measured green-lap slope extended linearly; a "
            "calibrated tire model would replace it.",
        ],
    }


def _pace_attribution(
    table: TelemetryTable,
    laps: Sequence[Mapping[str, Any]],
    track_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Where time is being lost, and what each corner costs the tires.

    `REFERENCE-DELTA-001`, `TIME-LOSS-RANK-001`, `TIRE-ENERGY-001`.

    This is the join that makes the two producers a coaching answer rather than
    two tables. A corner that costs half a second is interesting; a corner that
    costs half a second *and* consumes a fifth of the lap's tire energy is a
    decision, because spending more there has a price that shows up later in
    the stint. Neither number implies the other and neither is derived from the
    other, so both travel with every priority.

    Lap eligibility reuses `_corner_lap_exclusion_reasons`, the same screen the
    existing corner coaching uses: complete, green, racing, on track, out of
    traffic and away from pit service. Comparing a lap spent behind a slower
    car against a clean one would attribute the traffic to the driver.
    """

    unavailable = {
        "schema_version": PACE_ATTRIBUTION_SCHEMA_VERSION,
        "status": "unavailable",
        "priorities": [],
        "eligible_lap_numbers": [],
    }
    if not table.has("LapDistPct") or not table.has("SessionTime") or not table.has("Lap"):
        return {**unavailable, "reason": "distance_time_or_lap_channel_missing"}

    previous_flag: str | None = None
    eligible_numbers: set[int] = set()
    for lap in sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0):
        number = _finite(lap.get("lap"))
        reasons = _corner_lap_exclusion_reasons(lap, previous_flag)
        previous_flag = str(lap.get("flag_state") or "")
        if not reasons and number is not None and number >= 0:
            eligible_numbers.add(int(number))
    if not eligible_numbers:
        return {**unavailable, "reason": "no_clean_green_racing_laps"}

    lap_channel = table.get("Lap", default=None)
    groups: MutableMapping[int, list[int]] = defaultdict(list)
    for index, raw_lap in enumerate(lap_channel):
        value = _finite(raw_lap)
        if value is not None and int(value) in eligible_numbers:
            groups[int(value)].append(index)

    distance = table.get("LapDistPct", default=None)
    times = table.get("SessionTime", "SessionTimeOfDay", default=None)
    speed = table.get("Speed", default=None)
    traces = [
        lap_reference.build_lap_trace(
            distance,
            times,
            indices,
            speed_mps=speed,
            lap_number=number,
        )
        for number, indices in sorted(groups.items())
    ]

    detected = [
        segment
        for segment in ((track_profile or {}).get("detected_corner_segments") or ())
        if isinstance(segment, Mapping)
    ]
    if detected:
        segments: list[Mapping[str, Any]] = detected
        segment_source = "detected_corner_segments"
    else:
        segments = lap_reference.uniform_segments(FALLBACK_SEGMENT_COUNT)
        segment_source = "uniform_fallback"

    loss = time_loss.analyze_time_loss(traces, segments)

    missing_energy_channels = [
        name
        for name, present in (
            ("Speed", table.has("Speed")),
            ("LatAccel", table.has("LatAccel")),
            ("LongAccel", table.has("LongAccel")),
        )
        if not present
    ]
    energy_indices = [index for indices in groups.values() for index in indices]
    energy = tire_energy.segment_energy(
        lap_dist_pct=distance,
        session_time_s=times,
        speed_m_s=speed,
        lat_accel_m_s2=table.get("LatAccel", default=None),
        long_accel_m_s2=table.get("LongAccel", default=None),
        segments=segments,
        indices=sorted(energy_indices),
        velocity_x_m_s=table.get("VelocityX", default=None) if table.has("VelocityX") else None,
        velocity_y_m_s=table.get("VelocityY", default=None) if table.has("VelocityY") else None,
        sample_rate_hz=_finite(table.metadata.get("sample_rate")) or 60.0,
        missing_channels=missing_energy_channels,
    )

    energy_by_name = {segment.name: segment for segment in energy.segments}
    priorities = []
    for segment in loss.segments:
        if segment.status != time_loss.STATUS_USABLE:
            continue
        matched = energy_by_name.get(segment.name)
        priorities.append(
            {
                "name": segment.name,
                "start_pct": round(segment.start_pct, 6),
                "end_pct": round(segment.end_pct, 6),
                "recoverable_s": _round(segment.recoverable_s, 3),
                "best_s": _round(segment.best_s, 3),
                "median_s": _round(segment.median_s, 3),
                "best_lap": segment.best_lap,
                "lap_count": segment.lap_count,
                "near_best_lap_count": segment.near_best_lap_count,
                "tire_energy_share": (
                    _round(matched.share_of_lap, 4)
                    if matched is not None and matched.status == tire_energy.STATUS_USABLE
                    else None
                ),
                "tire_energy_grade": (
                    matched.grade
                    if matched is not None and matched.status == tire_energy.STATUS_USABLE
                    else None
                ),
                "peak_lateral_g": (
                    _round(matched.peak_lateral_g, 3) if matched is not None else None
                ),
            }
        )

    lap_count = len(traces)
    limitations = [
        "Recoverable time is this driver's own median minus his own best in each "
        "segment. It is proven achievable in isolation; the total is a ceiling "
        "composed from different laps, not a lap time.",
        "Tire energy is a specific-energy proxy from vehicle acceleration and "
        "speed, not a wear measurement.",
    ]
    if segment_source == "uniform_fallback":
        limitations.append(
            "No load zones were detected, so segments are even distance slices "
            "and do not correspond to named corners."
        )
    if loss.excluded_segment_count:
        limitations.append(
            f"{loss.excluded_segment_count} segment(s) were excluded from ranking; "
            "each carries its own reason."
        )

    return {
        "schema_version": PACE_ATTRIBUTION_SCHEMA_VERSION,
        "status": loss.status,
        "reason": None,
        "segment_source": segment_source,
        "eligible_lap_numbers": sorted(eligible_numbers),
        "eligible_lap_count": lap_count,
        "priorities": priorities,
        "total_recoverable_s": _round(loss.total_recoverable_s, 3),
        "time_loss": loss.to_payload(),
        "tire_energy": energy.to_payload(),
        "limitations": limitations,
    }


def analyze_telemetry(
    telemetry: Mapping[str, Any],
    *,
    source_paths: Sequence[str | Path] = (),
    source_fingerprints: Sequence[Mapping[str, Any]] = (),
    analysis_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    table = TelemetryTable(telemetry)
    if table.length < 2:
        raise ValueError("Telemetry contains fewer than two samples.")
    identity = _identity(table)
    race_session = _find_race_session(
        table.session_info, table.metadata.get("sim_session_num")
    )
    laps = _lap_summaries(table)
    runs = _runs(table, laps)
    _annotate_tire_set_lifecycle(runs)
    damage_repair = _damage_repair_summary(table, laps, runs)
    setup_series = _setup_channel_series(table)
    setup_telemetry = _setup_telemetry(table, setup_series)
    track_profile = _track_profile(table, setup_series=setup_series)
    lap_traces = _lap_trace_payload(table, laps)
    groove_channel_names = (
        "Lat",
        "Lon",
        "LapDistPct",
        "Lap",
        "SessionTime",
        "SessionFlags",
        "OnPitRoad",
        "Speed",
        "LFodometer",
        "RFodometer",
        "LRodometer",
        "RRodometer",
    )
    groove_channels = {
        name: table.get(name, default=None)
        for name in groove_channel_names
        if table.has(name)
    }
    groove_evolution = analyze_groove_evolution(
        groove_channels,
        runs=runs,
        laps=laps,
        load_zones=(track_profile or {}).get("detected_corner_segments") or (),
        channel_units=table.channel_units,
        track_type=identity.get("track_type"),
    )
    corner_tire_age = _corner_tire_age_summary(table, laps, runs, track_profile)
    scheduled_laps = _scheduled_race_laps(table, race_session)
    scheduled_minutes = _duration_minutes(race_session.get("SessionTime"))
    completed_race_laps = [
        lap for lap in laps
        if lap.get("complete") and (_finite(lap.get("lap")) or 0.0) > 0.0
    ]
    green_laps = sum(
        1.0 if lap["flag_state"] == "green" else (
            0.0 if lap["flag_state"] == "caution" else lap.get("green_fraction") or 0.0
        )
        for lap in completed_race_laps
    )
    caution_laps = sum(
        1.0 if lap["flag_state"] == "caution" else (
            0.0 if lap["flag_state"] == "green" else lap.get("caution_fraction") or 0.0
        )
        for lap in completed_race_laps
    )
    green_exposure = sum(
        lap.get("green_fraction") or 0.0 for lap in completed_race_laps
    )
    caution_exposure = sum(
        lap.get("caution_fraction") or 0.0 for lap in completed_race_laps
    )
    fuel_used = sum(
        lap["fuel"]["used_l"]
        for lap in completed_race_laps
        if lap.get("fuel", {}).get("used_l") is not None
    )
    source_strings = [str(Path(path).resolve()) for path in source_paths]
    profile = {
        "target_hz": _finite(table.metadata.get("sample_rate")),
        **dict(analysis_profile or {}),
    }
    fingerprints = [dict(item) for item in source_fingerprints if isinstance(item, Mapping)]
    fingerprint = _analysis_fingerprint(table, fingerprints, profile)
    track_geometry = build_track_geometry(
        table.channels,
        table.metadata,
        identity,
        fingerprints,
    )
    race_replay = build_race_replay(
        table.channels,
        table.session_info,
        table.metadata,
    )
    tire_learning = build_tire_learning(identity, runs, laps, fingerprint)
    required = {
        "time": table.has("SessionTime"),
        "lap": table.has("Lap"),
        "distance": table.has("LapDistPct"),
        "speed": table.has("Speed"),
        "brake": table.has("Brake", "BrakeRaw"),
        "steering": table.has("SteeringWheelAngle"),
        "fuel": table.has("FuelLevel"),
        "flags": table.has("SessionFlags"),
        "pit": table.has("OnPitRoad"),
        "repair_context": damage_repair.get("status") != "unavailable",
        "tire_wear": _has_measured_tire_wear(table),
        "groove_path": groove_evolution.get("status") != "unavailable",
        "setup": bool(identity.get("setup")),
    }
    official_cautions = race_session.get("ResultsNumCautionFlags")
    official_caution_laps = race_session.get("ResultsNumCautionLaps")
    official_caution_laps_number = _finite(official_caution_laps)
    caution_difference = (
        caution_laps - official_caution_laps_number
        if official_caution_laps_number is not None
        else None
    )
    race_summary = {
        "scheduled_laps": _round(scheduled_laps, 0),
        "scheduled_minutes": _round(scheduled_minutes, 1),
        "recorded_laps": len(completed_race_laps),
        "green_laps_estimated": _round(green_laps, 2),
        "caution_laps_estimated": _round(caution_laps, 2),
        "green_lap_equivalents": _round(green_exposure, 2),
        "caution_lap_equivalents": _round(caution_exposure, 2),
        "official_cautions": official_cautions,
        "official_caution_laps": official_caution_laps,
        "caution_reconciliation": {
            "status": (
                "mismatch"
                if caution_difference is not None and abs(caution_difference) > 1.0
                else "consistent"
                if caution_difference is not None
                else "official_unavailable"
            ),
            "telemetry_estimated_laps": _round(caution_laps, 2),
            "official_laps": _round(official_caution_laps_number, 2),
            "difference_laps": _round(caution_difference, 2),
            "note": "Telemetry classification uses sampled caution flags; official metadata remains a separate cross-check.",
        },
        "pit_stops_detected": max(0, len(runs) - 1),
        "runs_detected": len(runs),
        "starting_position": (
            completed_race_laps[0].get("position", {}).get("start")
            if completed_race_laps else None
        ),
        "final_recorded_position": (
            completed_race_laps[-1].get("position", {}).get("end")
            if completed_race_laps else None
        ),
        "fuel_used_l": _round(fuel_used),
        "fuel_used_gal": _round(fuel_used / 3.785411784),
        "damage_repair": {
            "status": damage_repair.get("status"),
            "recorded_repair_episodes": _path_get(
                damage_repair, "summary", "recorded_repair_episodes"
            ),
            "repair_required_flag_episodes": _path_get(
                damage_repair, "summary", "repair_required_flag_episodes"
            ),
            "tow_episodes": _path_get(damage_repair, "summary", "tow_episodes"),
            "confirmed_fast_repair_uses": _path_get(
                damage_repair, "summary", "confirmed_fast_repair_uses"
            ),
            "incident_points_added": _path_get(
                damage_repair, "incident_points", "positive_delta"
            ),
        },
    }
    conditions = _conditions_summary(table)
    driver_adjustments = _driver_adjustments_summary(table)
    coaching_signals = _coaching_signals(runs)
    pace_attribution = _pace_attribution(table, laps, track_profile)
    strategy = _strategy(runs, race_summary)
    field_pace = _field_pace(table)
    strategy_planning = _strategy_planning(
        table, laps, race_summary, strategy, field_pace
    )
    technical_insights = build_technical_insights(
        laps,
        runs,
        race_summary,
        strategy,
        damage_repair,
        tire_learning,
    )
    catalog = table.available_catalog()
    loaded_names = sorted(table.channels)
    catalog_names = sorted(
        {str(item.get("name")) for item in catalog if item.get("name")}
    )
    recorded_names = catalog_names or loaded_names
    analyzed_names = sorted(table.accessed_channels)
    source_catalogs = [
        dict(item)
        for item in (telemetry.get("source_catalogs") or ())
        if isinstance(item, Mapping)
    ]
    catalog_summary = (
        dict(telemetry.get("catalog_summary") or {})
        if isinstance(telemetry.get("catalog_summary"), Mapping)
        else {}
    )
    selection = table.metadata.get("channel_selection") or {}
    expected_catalog_count = _finite(
        selection.get("available_count") if isinstance(selection, Mapping) else None
    )
    catalog_complete = bool(catalog) and (
        expected_catalog_count is None or int(expected_catalog_count) == len(catalog)
    )
    analysis = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_profile_version": ANALYSIS_PROFILE_VERSION,
        "analysis_profile": profile,
        "analysis_id": fingerprint,
        "analyzed_at": _now(),
        "source": {
            "telemetry_files": source_strings,
            "fingerprints": fingerprints,
            "sample_count": table.length,
            # Backward-compatible name: available means recorded in the raw
            # source, not necessarily materialized for the routine pass.
            "available_channels": recorded_names,
            "loaded_channels": loaded_names,
            "analyzed_channels": analyzed_names,
            "unloaded_channels": sorted(set(recorded_names) - set(loaded_names)),
            "recorded_channel_catalog": catalog,
            "per_source_channel_catalogs": source_catalogs,
            "catalog_summary": catalog_summary,
            "channel_coverage": {
                "catalog_complete": catalog_complete,
                "recorded_count": len(recorded_names),
                "loaded_count": len(loaded_names),
                "analyzed_count": len(analyzed_names),
                "unloaded_count": len(set(recorded_names) - set(loaded_names)),
                "native_tick_rate_hz": _finite(table.metadata.get("tick_rate")),
                "analysis_sample_rate_hz": _finite(table.metadata.get("sample_rate")),
                "sampling_policy": "selective routine analysis; native/chunked passes available on demand",
            },
            "raw_source_policy": {
                "mode": "portable-copy-pending",
                "durably_copied": False,
                "note": "The workflow must verify a content-addressed portable copy before report persistence; originals remain untouched.",
            },
        },
        "identity": identity,
        "race_summary": race_summary,
        "race_grades": _race_grades(laps, runs, race_summary, damage_repair),
        "race_timeline": _race_evidence_timeline(laps, runs),
        "damage_repair": damage_repair,
        "runs": runs,
        "laps": laps,
        "lap_traces": lap_traces,
        "track_profile": track_profile,
        "track_geometry": track_geometry,
        "race_replay": race_replay,
        "tire_learning": tire_learning,
        "groove_evolution": groove_evolution,
        "corner_tire_age": corner_tire_age,
        "setup_telemetry": setup_telemetry,
        "conditions": conditions,
        "driver_adjustments": driver_adjustments,
        "strategy": strategy,
        "strategy_planning": strategy_planning,
        "field_pace": field_pace,
        "technical_insights": technical_insights,
        "coaching_signals": coaching_signals,
        "pace_attribution": pace_attribution,
        "data_quality": {
            "channels": required,
            "missing": [name for name, available in required.items() if not available],
            "confidence": "high" if sum(required.values()) >= 8 else (
                "medium" if sum(required.values()) >= 5 else "low"
            ),
            "tire_wear_rule": "Tire readings are discrete pit-service observations assigned to the preceding run.",
            "causality_rule": "Brake/steering/load traces support likely causes, not exact within-run tire-wear timing.",
            "damage_rule": "Tow/repair timers and confirmed fast-repair counters are recorded evidence; pace loss and incident points never prove damage.",
            "telemetry_completeness": {
                "catalog_complete": catalog_complete,
                "recorded_channels": len(recorded_names),
                "loaded_channels": len(loaded_names),
                "analyzed_channels": len(analyzed_names),
                "raw_source_sha256_verified": bool(fingerprints),
            },
        },
    }
    return analysis


def analysis_as_json(analysis: Mapping[str, Any]) -> str:
    return json.dumps(analysis, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
