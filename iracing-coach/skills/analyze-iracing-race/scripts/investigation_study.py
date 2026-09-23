"""Offline design-study projections; not yet wired into the application contract.

Keep fitting, trace alignment and segment selection in the deterministic backend.
Inputs must be the existing screened investigation points and exact lap traces.
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_left

from analysis_engine import _linear_slope
from race_investigation import _corner_lap, _crossing, _number


def run_fits(points: list[dict]) -> list[dict]:
    result = []
    for run in sorted({p['run_number'] for p in points}):
        selected = [p for p in points if p['run_number'] == run and p.get('eligible') is True
                    and _number(p.get('green_age')) is not None
                    and (_number(p.get('lap_time_s')) or 0) > 0]
        pairs = [(p['green_age'], p['lap_time_s']) for p in selected]
        slope = _linear_slope(pairs)
        if slope is None:
            continue
        intercept = statistics.fmean(y for _, y in pairs) - slope * statistics.fmean(x for x, _ in pairs)
        low, high = min(x for x, _ in pairs), max(x for x, _ in pairs)
        result.append({'run_number': run, 'count': len(pairs), 'laps': [p['lap'] for p in selected],
                       'slope': slope, 'method': 'ordinary_least_squares',
                       'points': [{'x': x, 'y': intercept + slope*x} for x in (low, high)]})
    return result


def _points(trace: dict) -> list[dict]:
    points = [p for p in trace.get('points', []) if _number(p.get('lap_pct')) is not None]
    # Never sort corrupt/reversed telemetry into a plausible drive.
    if any(a['lap_pct'] >= b['lap_pct'] for a, b in zip(points, points[1:])):
        return []
    return points


def _covered(points: list[dict], start: float, end: float) -> bool:
    if _crossing(points, start) is None or _crossing(points, end) is None:
        return False
    for left, right in zip(points, points[1:]):
        if right['lap_pct'] <= start or left['lap_pct'] >= end:
            continue
        t0, t1 = _number(left.get('session_time_s')), _number(right.get('session_time_s'))
        if t0 is None or t1 is None or not 0 < t1-t0 <= 2 or right['lap_pct']-left['lap_pct'] > .03:
            return False
    return True


def _sample(points: list[dict], x: float, key: str) -> float | None:
    i = bisect_left([p['lap_pct'] for p in points], x)
    if i < len(points) and abs(points[i]['lap_pct']-x) < 1e-9:
        return _number(points[i].get(key))
    if i == 0 or i >= len(points):
        return None
    left, right = points[i-1:i+1]
    if not _covered([left, right], left['lap_pct'], right['lap_pct']):
        return None
    a, b = _number(left.get(key)), _number(right.get(key))
    if a is None or b is None:
        return None
    return a + (b-a)*(x-left['lap_pct'])/(right['lap_pct']-left['lap_pct'])


def segment_context(traces: list[dict], candidates: list[dict], areas: list[dict]) -> dict:
    """Fastest/slowest actual segments within the caller's comparable lap pool.

    Cumulative delta is relative to context_start, not race start. It describes
    where separation accumulated; it does not apportion the physical cause.
    """
    by_lap = {t['lap']: t for t in traces}
    eligible = {p['lap']: p for p in candidates if p.get('eligible') is True
                and _number(p.get('green_age')) is not None}
    # The study pool is already chosen by the backend's matched comparison.
    # Reject broad pools instead of calling their extremes age-comparable.
    ages = [p['green_age'] for p in eligible.values()]
    if not ages or max(ages)-min(ages) > 3:
        return {}
    ordered = sorted(areas, key=lambda a: a['start_pct'])
    result = {}
    for index, area in enumerate(ordered):
        start, end = area['start_pct'], area['end_pct']
        ranked = []
        for lap, candidate in eligible.items():
            trace = by_lap.get(lap, {})
            points = _points(trace)
            if not _covered(points, start, end):
                continue
            metric = _corner_lap(trace, start, end)
            if metric and metric['elapsed'] is not None:
                ranked.append((metric['elapsed'], lap, points, candidate))
        ranked.sort(key=lambda v: (v[0], v[1]))
        if len(ranked) < 2 or ranked[-1][0]-ranked[0][0] < 1e-9:
            continue
        fast, slow = ranked[0], ranked[-1]
        context_start = ordered[index-1]['start_pct'] if index else max(fast[2][0]['lap_pct'], slow[2][0]['lap_pct'])
        if context_start >= start:
            continue
        valid_context = all(_covered(item[2], context_start, end) for item in (fast, slow))
        grid = sorted({context_start, start, end} | {p['lap_pct'] for item in (fast, slow)
                      for p in item[2] if context_start <= p['lap_pct'] <= end})
        origins = [_crossing(item[2], context_start) for item in (fast, slow)]
        rows = []
        for x in grid:
            row = {'x': x, 'fast': {}, 'slow': {}}
            for name, item in [('fast', fast), ('slow', slow)]:
                for source, target, multiplier in [('speed_mph', 'speed', 1), ('throttle', 'throttle', 100),
                        ('brake_mean', 'brake', 100), ('steering_rad', 'steering', 180/math.pi),
                        ('slip_angle_deg', 'slip', 1)]:
                    value = _sample(item[2], x, source)
                    row[name][target] = value*multiplier if value is not None else None
            times = [_crossing(item[2], x) for item in (fast, slow)]
            row['delta'] = (times[1]-origins[1])-(times[0]-origins[0]) if valid_context and None not in times+origins else None
            rows.append(row)
        entry = next(row for row in rows if row['x'] == start)
        exit_row = rows[-1]
        result[area['id']] = {
            'start': start, 'end': end, 'context_start': context_start,
            'context_label': 'Previous area and approach' if index else 'Recorded start of this lap',
            'context_complete': valid_context, 'candidate_count': len(ranked),
            'fast': {'lap': fast[1], 'seconds': fast[0], 'green_age': fast[3]['green_age']},
            'slow': {'lap': slow[1], 'seconds': slow[0], 'green_age': slow[3]['green_age']},
            'segment_delta': slow[0]-fast[0], 'entry_delta': entry['delta'], 'exit_delta': exit_row['delta'],
            'entry_speed_difference': (entry['slow']['speed']-entry['fast']['speed'])
                if None not in (entry['slow']['speed'], entry['fast']['speed']) else None,
            'rows': rows,
        }
        # Signed chassis sideslip from the existing backend trace, not tire slip
        # or a grip-loss verdict. Keep unavailable values unavailable.
        result[area['id']]['slip'] = {'basis': 'chassis_velocity_angle', 'unit': 'deg'}
        for name, item in [('fast', fast), ('slow', slow)]:
            core_slip = [abs(v) for p in item[2] if start <= p['lap_pct'] <= end
                         and (v := _number(p.get('slip_angle_deg'))) is not None]
            core_count = sum(start <= p['lap_pct'] <= end for p in item[2])
            coverage = len(core_slip)/core_count if core_count else 0
            summarized = len(core_slip) >= 2 and coverage >= .8
            result[area['id']]['slip'][name] = {
                'coverage': coverage,
                'median_abs': statistics.median(core_slip) if summarized else None,
                'peak_abs': max(core_slip) if summarized else None,
            }
    return result
