"""Gate G4 statistics (workstreams.md G4; thresholds in docs/08-rewrite/acceptance/g4-performance.md).

Standard library only. Inputs: the 1 Hz window CSV from ``g4-perf.ps1 -Mode windows``, the viewer log
from ``g4_viewers.mjs`` (viewers.jsonl) and, optionally, the fast CSV from ``g4-perf.ps1 -Mode fast``
for the abort check. Prints one JSON document with every window figure and the criterion verdicts.

    python tools/rig/g4_analyze.py --windows g4.csv --viewers viewers.jsonl [--fast abort.csv]
        [--clock-offset-s <sim minus dev-PC seconds>]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import statistics
import sys


def parse_utc(text: str) -> dt.datetime:
    """ISO time with any number of fractional digits (.NET writes 7) and Z or an offset."""
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", text.strip())
    if not m:
        raise ValueError(f"not an ISO time: {text!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = m.group(3) or "Z"
    tz = "+00:00" if tz == "Z" else tz
    return dt.datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    s = sorted(values)
    if not s:
        return math.nan
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def num(row: dict, key: str) -> float | None:
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def windows(path: str) -> tuple[list[dict], dict[str, list[dict]]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["label"], []).append(row)
    return rows, groups


def window_stats(rows: list[dict], label: str, before: dict | None) -> dict:
    fr = [num(r, "FrameRate") for r in rows if num(r, "FrameRate") is not None]
    fg = [num(r, "CpuUsageFG") for r in rows if num(r, "CpuUsageFG") is not None]
    bg = [num(r, "CpuUsageBG") for r in rows if num(r, "CpuUsageBG") is not None]
    gpu = [num(r, "GpuUsage") for r in rows if num(r, "GpuUsage") is not None]
    on_track = [num(r, "IsOnTrack") for r in rows]
    out = {
        "label": label,
        "samples": len(rows),
        "start": rows[0]["utc"],
        "end": rows[-1]["utc"],
        "frame_rate_median": statistics.median(fr) if fr else None,
        "frame_rate_p1": percentile(fr, 0.01) if fr else None,
        "cpu_fg_median": statistics.median(fg) if fg else None,
        "cpu_bg_median": statistics.median(bg) if bg else None,
        "gpu_median": statistics.median(gpu) if gpu else None,
        "on_track_samples": sum(1 for v in on_track if v == 1),
        "speed_max": max((num(r, "Speed") or 0.0) for r in rows),
        "speed_min": min((num(r, "Speed") or 0.0) for r in rows),
        "speed_median": statistics.median((num(r, "Speed") or 0.0) for r in rows),
        # bot-driven run (W): every sample on track, off pit road, at >= 15 m/s
        "on_pit_road_samples": sum(1 for r in rows if num(r, "OnPitRoad") == 1),
        "laps": sorted({int(num(r, "Lap")) for r in rows if num(r, "Lap") is not None}),
        "collector_samples": sum(1 for r in rows if r.get("pid")),
    }
    if label.startswith("C"):
        first = before if before and before.get("pid") else rows[0]
        last = rows[-1]
        wall_s = (parse_utc(last["utc"]) - parse_utc(first["utc"])).total_seconds()
        delta = lambda k: (num(last, k) or 0) - (num(first, k) or 0)  # noqa: E731
        out.update({
            "collector_pid": sorted({r["pid"] for r in rows if r.get("pid")}),
            "collector_cpu_ms": delta("cpu_ms"),
            "collector_wall_s": wall_s,
            "collector_cpu_pct_one_core": 100.0 * delta("cpu_ms") / 1000.0 / wall_s if wall_s else None,
            "collector_ws_max": max(num(r, "ws") or 0 for r in rows),
            "collector_private_max": max(num(r, "private") or 0 for r in rows),
            "io_read_bytes": delta("io_r"),
            "io_write_bytes": delta("io_w"),
            "io_other_bytes": delta("io_o"),
            "io_read_ops": delta("ops_r"),
            "io_write_ops": delta("ops_w"),
            "io_other_ops": delta("ops_o"),
        })
    return out


def viewer_rates(path: str, start: dt.datetime, end: dt.datetime, offset_s: float) -> dict:
    a, b, spool, pending = [], [], [], []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            t = parse_utc(row["utc"]) + dt.timedelta(seconds=offset_s)
            # a viewer row counts the second that ended at its time stamp
            if start + dt.timedelta(seconds=1) <= t <= end:
                a.append(row["A"])
                b.append(row["B"])
                if "spool_bytes" in row:
                    spool.append(row["spool_bytes"])
                for c in row.get("collectors") or []:
                    pass
    return {
        "seconds": len(a),
        "A_median": statistics.median(a) if a else None,
        "B_median": statistics.median(b) if b else None,
        "A_min": min(a) if a else None,
        "B_min": min(b) if b else None,
        "spool_first": spool[0] if spool else None,
        "spool_last": spool[-1] if spool else None,
        "spool_max": max(spool) if spool else None,
    }


def abort(path: str) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    on = next((r for r in rows if r["IsOnTrack"] == "1"), None)
    if on is None:
        return {"error": "IsOnTrack never 1 in the fast capture"}
    t_on = float(on["ms"])
    prev_read = None
    last_increase = None
    read_before = []
    for r in rows:
        v = num(r, "io_r")
        if v is None:
            continue
        if prev_read is not None and v > prev_read:
            last_increase = float(r["ms"])
            if float(r["ms"]) <= t_on:
                read_before.append(float(r["ms"]))
        prev_read = v
    reads_in_last_s = [t for t in read_before if t_on - 1000 <= t <= t_on]
    after = [r for r in rows if float(r["ms"]) > t_on]
    other_after = [(float(r["ms"]), num(r, "io_other")) for r in after]
    return {
        "on_track_ms": t_on,
        "on_track_utc": on["utc"],
        "read_increases_in_second_before": len(reads_in_last_s),
        "last_read_increase_ms": last_increase,
        "abort_latency_ms": (last_increase - t_on) if last_increase is not None and last_increase > t_on else 0.0,
        "rows": len(rows),
        "loop_ms_median": statistics.median([float(rows[i + 1]["ms"]) - float(rows[i]["ms"]) for i in range(len(rows) - 1)]) if len(rows) > 1 else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--windows", required=True)
    p.add_argument("--viewers")
    p.add_argument("--fast")
    p.add_argument("--clock-offset-s", type=float, default=0.0, help="sim clock minus dev-PC clock")
    args = p.parse_args(argv)

    rows, groups = windows(args.windows)
    labels = [l for l in dict.fromkeys(r["label"] for r in rows) if "-settle" not in l]
    stats = {}
    for label in labels:
        settle = groups.get(f"{label}-settle")
        stats[label] = window_stats(groups[label], label, settle[-1] if settle else None)
        if args.viewers and label.startswith("C"):
            stats[label]["viewers"] = viewer_rates(
                args.viewers, parse_utc(stats[label]["start"]), parse_utc(stats[label]["end"]), args.clock_offset_s
            )
    bs = [stats[l] for l in labels if l.startswith("B")]
    cs = [stats[l] for l in labels if l.startswith("C")]
    verdict = {}

    g41 = []
    for c in cs:
        k = int(c["label"][1:])
        adj = [stats.get(f"B{k}"), stats.get(f"B{k + 1}")]
        adj = [a for a in adj if a]
        mean = statistics.fmean(a["frame_rate_median"] for a in adj)
        dev = 100.0 * (c["frame_rate_median"] - mean) / mean
        g41.append({"window": c["label"], "median": c["frame_rate_median"], "adjacent_mean": mean, "deviation_pct": dev, "pass": abs(dev) <= 1.0})
    verdict["G4-1"] = {"pass": all(x["pass"] for x in g41), "windows": g41}

    g42 = {}
    for key in ("frame_rate_p1", "cpu_fg_median", "gpu_median"):
        lo, hi = min(b[key] for b in bs), max(b[key] for b in bs)
        cmed = statistics.median(c[key] for c in cs)
        g42[key] = {"collector_median": cmed, "baseline_min": lo, "baseline_max": hi, "pass": lo <= cmed <= hi,
                    "per_window_inside": [lo <= c[key] <= hi for c in cs]}
    verdict["G4-2"] = {"pass": all(v["pass"] for v in g42.values()), **g42}

    cpu_ms = sum(c["collector_cpu_ms"] for c in cs)
    wall = sum(c["collector_wall_s"] for c in cs)
    verdict["G4-3"] = {"cpu_pct_one_core": 100.0 * cpu_ms / 1000.0 / wall, "pass": 100.0 * cpu_ms / 1000.0 / wall <= 1.5}
    ws = max(c["collector_ws_max"] for c in cs)
    verdict["G4-4"] = {"ws_max_bytes": ws, "pass": ws <= 33_554_432}
    writes = sum(c["io_write_bytes"] for c in cs)
    verdict["G4-5"] = {"write_bytes": writes, "pass": writes <= 65_536}
    reads = sum(c["io_read_bytes"] for c in cs)
    all_on = all(s["on_track_samples"] == s["samples"] for s in stats.values())
    verdict["G4-6"] = {"read_bytes": reads, "all_samples_on_track": all_on, "pass_collector_side": reads == 0 and all_on}
    driven = {}
    for label, st in stats.items():
        rows_w = groups[label]
        ok = all(num(r, "IsOnTrack") == 1 and num(r, "OnPitRoad") == 0 and (num(r, "Speed") or 0.0) >= 15.0 for r in rows_w)
        driven[label] = {"pass": ok, "speed_min": st["speed_min"], "speed_median": st["speed_median"],
                         "on_pit_road_samples": st["on_pit_road_samples"], "laps": st["laps"]}
    verdict["W"] = {"pass": all(d["pass"] for d in driven.values()), "windows": driven}
    if args.viewers:
        g48 = [{"window": c["label"], "A": c["viewers"]["A_median"], "B": c["viewers"]["B_median"]} for c in cs]
        verdict["G4-8"] = {"pass": all(x["A"] is not None and x["A"] >= 59 and x["B"] is not None and x["B"] >= 59 for x in g48), "windows": g48}
    if args.fast:
        verdict["G4-7"] = abort(args.fast)
    print(json.dumps({"windows": stats, "verdict": verdict}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
