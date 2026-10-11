"""Recorded iRacing sector definitions and start/finish timing coverage."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def iracing_sectors(session_info: Mapping[str, Any]) -> dict[str, Any]:
    """Use only SplitTimeInfo.Sectors; never infer sectors from driving inputs."""
    split = session_info.get("SplitTimeInfo")
    raw = split.get("Sectors") or [] if isinstance(split, Mapping) else []
    result: dict[str, Any] = {
        "source": "iracing_split_time_info", "version": "iracing-sectors/1",
        "provenance": "iRacing SplitTimeInfo.Sectors recorded with this session.",
        "sections": [], "unavailable_reason": "iRacing sector definitions were not recorded for this session.",
    }
    if not isinstance(raw, list) or not raw:
        return result
    starts = []
    for item in raw:
        start = _number(item.get("SectorStartPct")) if isinstance(item, Mapping) else None
        if start is None or not 0 <= start < 1:
            result["unavailable_reason"] = "Recorded iRacing sector definitions are invalid."
            return result
        starts.append(start)
    starts.sort()
    if starts[0] != 0 or len(set(starts)) != len(starts):
        result["unavailable_reason"] = "Recorded iRacing sectors do not define a complete lap."
        return result
    result["version"] = "iracing-sectors/1/" + hashlib.sha256(json.dumps(starts).encode()).hexdigest()[:12]
    result["sections"] = [
        {"id": f"S{i + 1}", "label": f"S{i + 1}", "start_pct": start,
         "end_pct": starts[i + 1] if i + 1 < len(starts) else 1.0,
         "boundary_status": "iracing_sector", "provenance": result["provenance"]}
        for i, start in enumerate(starts)
    ]
    result["unavailable_reason"] = None
    return result


def attach_sector_boundaries(lap_traces: dict[str, Any], laps: Sequence[Mapping[str, Any]],
                             session_times: Sequence[Any], lap_distances: Sequence[Any]) -> None:
    """Attach recorded start/finish crossings, without changing native binned traces.

    Interpolate only a small distance/time step around the lap-distance wrap.
    A truncated lap or a missing channel keeps its boundary absent.
    """
    by_lap = {lap.get("lap"): lap for lap in laps}
    size = min(len(session_times), len(lap_distances))

    def crossing(index: int) -> float | None:
        if not 0 < index < size:
            return None
        x0, x1 = _number(lap_distances[index - 1]), _number(lap_distances[index])
        t0, t1 = _number(session_times[index - 1]), _number(session_times[index])
        if None in (x0, x1, t0, t1):
            return None
        if not (0 <= x1 < 0.03 and 0.97 < x0 <= 1 and 0 < t1 - t0 <= 2):
            return None
        step = 1 - x0 + x1
        if not 0 < step <= 0.03:
            return None
        return t0 + (1 - x0) / step * (t1 - t0)

    for trace in lap_traces.get("traces") or []:
        lap = by_lap.get(trace.get("lap")) or {}
        boundaries = []
        for fraction, key, offset in ((0.0, "start_index", 0), (1.0, "end_index", 1)):
            index = _number(lap.get(key))
            if index is None or index != int(index):
                continue
            time = crossing(int(index) + offset)
            if time is not None:
                boundaries.append({"lap_pct": fraction, "session_time_s": time})
        if boundaries:
            trace["sector_boundaries"] = boundaries
