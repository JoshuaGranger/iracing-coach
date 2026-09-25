"""Verbatim pre-optimisation reference for ``analysis_engine`` (generated; do not edit).

These are the analysis_engine.py function bodies exactly as they were at commit
9077917, before the column-at-a-time rewrite. The equivalence tests run them
beside the current functions on the same synthetic telemetry and require
identical output. Every other name they use is imported from the current
module; the generator verified each of those is unchanged (identical AST).

Regenerate only from the pre-change source (``git show 9077917:<path>``).
"""
# ruff: noqa
# fmt: off
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

import sys as _sys

_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
if str(_SCRIPTS) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS))

from analysis_engine import (  # noqa: E402
    BLACK_FLAG,
    CAUTION_FLAGS,
    CHECKERED_FLAG,
    KPA_TO_PSI,
    METERS_TO_INCHES,
    PIT_SERVICE_BITS,
    PLATFORM_CHANNEL_ALIASES,
    POSITIONS,
    TIRES,
    WHITE_FLAG,
    _bool,
    _count_corrections,
    _deep_find,
    _finite,
    _fraction,
    _normalized_unit,
    _path_get,
    _round,
    _series_metadata,
    _session_track_usage,
    _shock_channel_aliases,
    _tire_pressure_aliases,
    _tire_temperature_aliases,
    _vehicle_sideslip_degrees,
)


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

def _unit_scale(quantity: str, unit: str) -> Callable[[float], float] | None:
    """The exact arithmetic for one quantity/unit pair; ``None`` means unchanged."""

    if quantity == "distance":
        if unit in {"in", "inch", "inches", '"'}:
            return None
        if unit in {"ft", "foot", "feet"}:
            return lambda number: number * 12.0
        if unit in {"mm", "millimeter", "millimeters"}:
            return lambda number: number / 25.4
        if unit in {"cm", "centimeter", "centimeters"}:
            return lambda number: number / 2.54
        return lambda number: number * METERS_TO_INCHES
    if quantity == "velocity":
        if unit in {"in/s", "inch/s", "inches/s", "ips"}:
            return None
        if unit in {"ft/s", "fps"}:
            return lambda number: number * 12.0
        if unit in {"mm/s"}:
            return lambda number: number / 25.4
        if unit in {"cm/s"}:
            return lambda number: number / 2.54
        return lambda number: number * METERS_TO_INCHES
    if quantity == "pressure":
        if unit in {"psi", "lb/in2", "lbf/in2"}:
            return None
        if unit in {"pa", "pascal", "pascals"}:
            return lambda number: number * KPA_TO_PSI / 1000.0
        if unit in {"bar"}:
            return lambda number: number * 100.0 * KPA_TO_PSI
        return lambda number: number * KPA_TO_PSI
    if quantity == "temperature":
        if unit in {"f", "fahrenheit"}:
            return None
        if unit in {"k", "kelvin"}:
            return lambda number: (number - 273.15) * 9.0 / 5.0 + 32.0
        return lambda number: number * 9.0 / 5.0 + 32.0
    return None

@lru_cache(maxsize=256)
def _setup_value_converter(quantity: str, source_unit: str | None) -> Callable[[Any], float | None]:
    """Resolve a channel's unit once; convert each sample with the same arithmetic."""

    scale = _unit_scale(quantity, _normalized_unit(source_unit))
    if scale is None:
        return _finite

    def convert(value: Any) -> float | None:
        number = _finite(value)
        return None if number is None else scale(number)

    return convert

def _convert_setup_value(value: Any, quantity: str, source_unit: str | None) -> float | None:
    return _setup_value_converter(quantity, source_unit)(value)

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
    convert = _setup_value_converter(quantity, source_unit)
    return {
        "channel": channel,
        "source_unit": source_unit,
        "values": [convert(value) for value in raw_values],
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
    # iRacing publishes the finished lap's own scored time in LapLastLapTime.
    # Deriving a lap time from sample timestamps is short by one sample interval
    # by construction (the last sample of a lap precedes the start/finish
    # crossing), a bias measured at 10-31 ms per lap on real recordings and
    # independent of sample rate. Every other car already uses the SDK's
    # authoritative timing; only the player's lap was guessed. Read it here and
    # prefer it, falling back to the derived duration when it is absent.
    last_lap_time = table.get("LapLastLapTime", default=None)
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
    def _sdk_lap_time(lap_number: int, derived: float) -> float | None:
        # LapLastLapTime reports the lap that just ended, updating about 1.5 s
        # INTO the next lap and holding until the lap after, so lap N's scored
        # time is the value held late in lap N+1 - reading it at the boundary
        # returns lap N-1 instead. Take the value at the last sample of lap N+1,
        # by when it has certainly settled and has not yet rolled forward. Guard
        # against the off-by-one-lap trap by rejecting a reading that disagrees
        # with the derived duration by more than a second; a real lap and its
        # own sample span never differ by that much.
        if last_lap_time is None:
            return None
        following = groups.get(lap_number + 1)
        if not following:
            return None
        scored = _finite(last_lap_time[following[-1]])
        if scored is None or scored <= 0.0:
            return None
        if abs(scored - derived) > 1.0:
            return None
        return scored

    summaries: list[dict[str, Any]] = []
    for lap_number in sorted(groups):
        indices = groups[lap_number]
        if len(indices) < 2:
            continue
        start, end = indices[0], indices[-1]
        start_time, end_time = _finite(times[start]), _finite(times[end])
        duration = end_time - start_time if start_time is not None and end_time is not None else sum(dts[i] for i in indices)
        scored_lap_time = _sdk_lap_time(lap_number, duration)
        lap_time_value = scored_lap_time if scored_lap_time is not None else duration
        lap_time_source = "sdk_scored" if scored_lap_time is not None else "sample_derived"
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
                "lap_time_s": _round(lap_time_value),
                "lap_time_source": lap_time_source,
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
        # register is the normalization boundary; untransformed channels do
        # not need a second complete conversion pass before reaching it.
        converted = (
            [transform(value, channel) for value in raw]
            if transform is not None else raw
        )
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

    def unit_transform(quantity: str) -> Any:
        # Resolve each channel's unit once instead of once per sample.
        by_channel: dict[str, Any] = {}

        def transform(value: Any, channel: str) -> float | None:
            convert = by_channel.get(channel)
            if convert is None:
                convert = by_channel[channel] = _setup_value_converter(quantity, table.unit(channel))
            return convert(value)

        return transform

    temperature_f = unit_transform("temperature")
    pressure_psi = unit_transform("pressure")
    distance_in = unit_transform("distance")
    velocity_in_s = unit_transform("velocity")

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
        # Bucket indices ascend, so one bound check covers the whole bucket.
        if indices and indices[-1] < len(series):
            return [value for index in indices if (value := _finite(series[index])) is not None]
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
                # register already guarantees finite floats or None. Keep
                # gaps, but avoid converting every native sample again.
                samples = (
                    [value for index in indices if (value := signal_series[index]) is not None]
                    if indices[-1] < len(signal_series)
                    else [
                        value
                        for index in indices
                        if index < len(signal_series)
                        and (value := signal_series[index]) is not None
                    ]
                )
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
                    representative = statistics.median(samples)
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
