"""Gate G1 helpers for tools/rig/g1-analysis.sh (standard library; dev PC).

    wait     poll /api/v1/jobs until every job after a given id is terminal; cancel on abort
    capture  turn a container archive (pulled read-only) into a tools/parity capture
    calls    the in-container trace of a native run against the oracle capture's entry-point calls
             (worker-interface.md section 8: one native decode per recording, one kernel call per
             oracle call, count for count, and no Python entry point on the native side)
    strict   compare two analysis.json files with only roots, separators and newlines folded;
             differing `*_at` run instants are listed apart from every other difference
    report   per-window statistics from the sampler files and the gate verdict

The thresholds are the gate's (docs/08-rewrite/workstreams.md, G1) and are fixed here and in
docs/08-rewrite/acceptance/g1-analysis.md before any measurement.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TERMINAL = {"succeeded", "failed", "cancelled", "deferred"}

# Gate G1 thresholds (written before measuring).
WALL_LIMIT_S = 60.0
WORKER_PEAK_LIMIT_KIB = 3 * 1024 * 1024  # 3.0 GiB
HA_ALLOWANCE_S = 0.100  # p95 <= baseline p95 + 100 ms, no failed request
FRIGATE_RATE_TOLERANCE = 0.05  # camera_fps and process_fps window mean within +/-5 % of baseline
FRIGATE_SKIPPED_ALLOWANCE = 0.1  # skipped_fps window mean <= baseline mean + 0.1 fps


# ---------------------------------------------------------------- wait


def _get(server: str, path: str) -> Any:
    with urllib.request.urlopen(server + path, timeout=30) as response:
        return json.loads(response.read())


def _post(server: str, path: str) -> int:
    request = urllib.request.Request(server + path, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _aborted(ssh: str, remote_dir: str) -> str | None:
    probe = subprocess.run(["ssh", ssh, f"cat {remote_dir}/abort 2>/dev/null"], capture_output=True, text=True)
    return probe.stdout.strip() or None


def command_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    stable = 0
    while True:
        jobs = [job for job in _get(args.server, "/api/v1/jobs?limit=200")["jobs"] if job["job_id"] > args.after]
        reason = _aborted(args.ssh, args.remote_dir) if args.remote_dir else None
        if reason:
            for job in jobs:
                if job["state"] in ("queued", "running"):
                    _post(args.server, f"/api/v1/jobs/{job['job_id']}/cancel")
            print(json.dumps({"aborted": reason, "jobs": jobs}))
            return 3
        kinds = {job["kind"] for job in jobs}
        done = bool(jobs) and all(job["state"] in TERMINAL for job in jobs) and args.need <= kinds
        stable = stable + 1 if done else 0
        if stable >= 2:
            detailed = [_get(args.server, f"/api/v1/jobs/{job['job_id']}") for job in sorted(jobs, key=lambda j: j["job_id"])]
            print(json.dumps({"jobs": detailed}))
            return 0
        if time.monotonic() > deadline:
            print(json.dumps({"timeout": True, "jobs": jobs}))
            return 4
        time.sleep(1.0)


# ---------------------------------------------------------------- capture


def _parity() -> Any:
    sys.path.insert(0, str(REPO / "tools" / "parity"))
    import analysis_parity  # noqa: PLC0415

    return analysis_parity


def command_capture(args: argparse.Namespace) -> int:
    parity = _parity()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    roots = parity.Roots((("<archive_root>", args.archive_root), ("<iracing_root>", args.iracing_root)))
    result = json.loads(Path(args.result).read_text(encoding="utf-8")) if args.result else {}
    parity.write_archive_capture(Path(args.archive), out, roots, workflow_result=result, engine_output=None)
    parity._capture_metadata(
        out,
        "worker",
        case="g1-container",
        analysis_id=args.analysis_id,
        analyzer_digest="unpinned",
        native_stages=args.native_stages,
        raw_archive="external",
        roots={"<archive_root>": args.archive_root, "<iracing_root>": args.iracing_root},
        platform_note="linux container, pulled read-only",
    )
    return 0


# ---------------------------------------------------------------- calls


def command_calls(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"))
    import native_stages  # noqa: PLC0415

    oracle = json.loads((Path(args.oracle) / "capture.json").read_text(encoding="utf-8"))
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    trace = run.get("trace") or {}
    decodes = trace.get("native_decode_calls") or []
    native = trace.get("kernel_calls") or {"native": {}, "oracle": {}}
    problems = list(native_stages.kernel_call_problems(oracle.get("kernel_calls") or {}, native, True))
    expected = sorted(item["name"] for item in oracle.get("recordings") or [])
    if sorted(call.get("source") for call in decodes) != expected or any(call.get("error") for call in decodes):
        problems.append(f"native decode calls {decodes}, expected one successful call for each of {expected}")
    kinds = sorted({p["kind"] for p in run.get("processes") or []})
    if "coach-analysis kernels" not in kinds:
        problems.append(f"no coach-analysis kernels process was sampled (processes: {kinds})")
    report = {
        "native_decode_calls": decodes,
        "kernel_calls_native": native.get("native"),
        "entry_point_calls_native_side": native.get("oracle"),
        "oracle_entry_point_calls": (oracle.get("kernel_calls") or {}).get("oracle"),
        "kernel_call_total": sum((native.get("native") or {}).values()),
        "processes": kinds,
        "problems": problems,
        "pass": not problems,
    }
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


# ---------------------------------------------------------------- strict


def _strict_text(text: str, roots: list[tuple[str, str]]) -> str:
    text = text.replace("\r\n", "\n")
    for token, prefix in sorted(roots, key=lambda item: len(item[1]), reverse=True):
        for form in (prefix.replace("\\", "\\\\"), prefix):
            text = text.replace(form, token)
    run = re.compile(r"(<archive_root>|<iracing_root>)([^\"]*)")
    return run.sub(lambda m: m.group(1) + m.group(2).replace("\\\\", "/").replace("\\", "/"), text)


def _diff_paths(a: Any, b: Any, where: str, out: list[str], limit: int = 60) -> None:
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        out.append(f"{where}: {a!r:.120} != {b!r:.120}")
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{where}.{key}: present on one side only")
            else:
                _diff_paths(a[key], b[key], f"{where}.{key}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{where}: length {len(a)} != {len(b)}")
        for index, (x, y) in enumerate(zip(a, b)):
            _diff_paths(x, y, f"{where}[{index}]", out, limit)
    elif a != b:
        out.append(f"{where}: {a!r:.120} != {b!r:.120}")


def command_strict(args: argparse.Namespace) -> int:
    left = _strict_text(Path(args.left).read_bytes().decode("utf-8"), [("<archive_root>", args.left_archive), ("<iracing_root>", args.left_iracing)])
    right = _strict_text(Path(args.right).read_bytes().decode("utf-8"), [("<archive_root>", args.right_archive), ("<iracing_root>", args.right_iracing)])
    report: dict[str, Any] = {"left_bytes": Path(args.left).stat().st_size, "right_bytes": Path(args.right).stat().st_size,
                              "identical_after_roots_and_newlines": left == right}
    other: list[str] = []
    if left != right:
        paths: list[str] = []
        _diff_paths(json.loads(left), json.loads(right), "$", paths, limit=10_000)
        # Wall-clock instants written by the run itself (`*_at` keys); everything else is a defect.
        instant = re.compile(r"^\$[^:]*_at: '\d{4}-\d{2}-\d{2}T[^']*' != '\d{4}-\d{2}-\d{2}T[^']*'$")
        stamps = [line for line in paths if instant.match(line)]
        other = [line for line in paths if not instant.match(line)]
        report["run_timestamps"] = stamps
        report["other_differences"] = other[:60]
        report["identical_except_run_timestamps"] = not other
    print(json.dumps(report, indent=2))
    return 0 if not other else 1


# ---------------------------------------------------------------- report


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(0.95 * (len(ordered) - 1))]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _markers(path: Path) -> dict[str, float]:
    marks: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            label, stamp = line.split()[:2]
            marks[label] = float(stamp)
    return marks


def _windows(marks: dict[str, float]) -> dict[str, tuple[float, float]]:
    if "run1_start" not in marks:  # g1-deferred.sh: one window per `<label>_start`/`<label>_end` pair
        starts = sorted((t, k[: -len("_start")]) for k, t in marks.items() if k.endswith("_start") and k != "sampler_start")
        windows = {"baseline": (starts[0][0] - 60.0, starts[0][0])} if starts else {}
        for t, label in starts:
            if f"{label}_end" in marks:
                windows[label] = (t, marks[f"{label}_end"])
        return windows
    windows = {"baseline": (marks["run1_start"] - 60.0, marks["run1_start"])}
    windows["run1"] = (marks["run1_start"], marks["run1_end"])
    if "run2_start" in marks:
        windows["gap"] = (marks["run1_end"], marks["run2_start"])
        windows["run2"] = (marks["run2_start"], marks["run2_end"])
        windows["tail"] = (marks["run2_end"], marks["run2_end"] + 30.0)
    if "overlap_start" in marks:
        windows["overlap"] = (marks["overlap_start"], marks["overlap_end"])
        windows["overlap_tail"] = (marks["overlap_end"], marks["overlap_end"] + 30.0)
    return windows


def _frigate_sums(stats: Any) -> dict[str, float] | None:
    if not isinstance(stats, dict):
        return None
    cameras = stats.get("cameras") if isinstance(stats.get("cameras"), dict) else {
        k: v for k, v in stats.items() if isinstance(v, dict) and "camera_fps" in v
    }
    if not cameras:
        return None
    keys = ("camera_fps", "process_fps", "skipped_fps", "detection_fps")
    return {key: float(sum(float(c.get(key) or 0.0) for c in cameras.values())) for key in keys}


def command_report(args: argparse.Namespace) -> int:
    data = Path(args.data)
    marks = _markers(data / "markers.log")
    windows = _windows(marks)
    ha = []
    for line in (data / "ha.csv").read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if len(parts) == 3:
            ha.append((float(parts[0]), float(parts[1]), parts[2]))
    frigate = []
    for line in (data / "frigate.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        sums = _frigate_sums(item.get("stats"))
        frigate.append((float(item["t"]), sums))
    memory = []
    for line in (data / "memory.csv").read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if len(parts) == 3 and parts[1]:
            memory.append((float(parts[0]), int(parts[1]), int(parts[2]) if parts[2] else None))
    workers: dict[str, dict[str, Any]] = {}
    worker_file = data / "worker.csv"
    for line in (worker_file.read_text(encoding="utf-8").splitlines() if worker_file.exists() else []):
        parts = line.split(",", 4)
        if len(parts) < 4:
            continue
        t, pid, hwm, rss = float(parts[0]), parts[1], int(parts[2]), int(parts[3])
        cmd = parts[4].strip() if len(parts) > 4 else ""
        kind = "coach-analysis " + cmd.split()[1] if "coach-analysis" in cmd and len(cmd.split()) > 1 else "worker"
        entry = workers.setdefault(pid, {"pid": pid, "kind": kind, "first": t, "last": t, "vm_hwm_kib": 0, "vm_rss_max_kib": 0})
        entry["last"] = t
        entry["vm_hwm_kib"] = max(entry["vm_hwm_kib"], hwm)
        entry["vm_rss_max_kib"] = max(entry["vm_rss_max_kib"], rss)

    result: dict[str, Any] = {"marks": marks, "windows": {}}
    for name, (start, end) in windows.items():
        inside = lambda t: start <= t < end  # noqa: E731
        times = [s for t, s, code in ha if inside(t) and code == "200"]
        failed = [code for t, s, code in ha if inside(t) and code != "200"]
        fr = [s for t, s in frigate if inside(t) and s is not None]
        mem = [m for t, m, p in memory if inside(t)]
        window: dict[str, Any] = {
            "start": start, "end": end, "seconds": round(end - start, 2),
            "ha": {"n": len(times) + len(failed), "failed": len(failed),
                   "mean_ms": None if not times else round(_mean(times) * 1000, 3),
                   "p50_ms": None if not times else round(sorted(times)[round(0.5 * (len(times) - 1))] * 1000, 3),
                   "p95_ms": None if not times else round(_p95(times) * 1000, 3),
                   "max_ms": None if not times else round(max(times) * 1000, 3)},
            "frigate": {"n": len(fr), **{key: None if not fr else round(_mean([s[key] for s in fr]), 3)
                                         for key in ("camera_fps", "process_fps", "skipped_fps", "detection_fps")},
                        "skipped_fps_max": None if not fr else max(s["skipped_fps"] for s in fr)},
            "memory_current_max_bytes": max(mem) if mem else None,
            "workers": [w for w in workers.values() if start <= w["first"] < end],
        }
        result["windows"][name] = window
    peaks = [p for t, m, p in memory if p is not None]
    result["memory_peak_bytes_final"] = peaks[-1] if peaks else None

    base = result["windows"]["baseline"]
    verdicts: dict[str, Any] = {}
    for run in ("run1", "run2"):
        if run not in result["windows"]:
            continue
        window = result["windows"][run]
        checks: dict[str, Any] = {}
        limit = base["ha"]["p95_ms"] + HA_ALLOWANCE_S * 1000
        checks["ha"] = {"p95_ms": window["ha"]["p95_ms"], "limit_ms": round(limit, 3), "failed": window["ha"]["failed"],
                        "pass": window["ha"]["failed"] == 0 and window["ha"]["p95_ms"] is not None and window["ha"]["p95_ms"] <= limit}
        frigate_ok = True
        for key in ("camera_fps", "process_fps"):
            b, r = base["frigate"][key], window["frigate"][key]
            ok = b is not None and r is not None and abs(r - b) <= FRIGATE_RATE_TOLERANCE * b
            checks[f"frigate_{key}"] = {"baseline": b, "run": r, "pass": ok}
            frigate_ok &= ok
        b, r = base["frigate"]["skipped_fps"], window["frigate"]["skipped_fps"]
        ok = b is not None and r is not None and r <= b + FRIGATE_SKIPPED_ALLOWANCE
        checks["frigate_skipped_fps"] = {"baseline": b, "run": r, "pass": ok}
        checks["frigate_detection_fps_recorded"] = {"baseline": base["frigate"]["detection_fps"], "run": window["frigate"]["detection_fps"]}
        verdicts[run] = checks
    result["verdicts"] = verdicts
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    wait = commands.add_parser("wait")
    wait.add_argument("--server", required=True)
    wait.add_argument("--after", type=int, required=True, help="Highest job id before the run.")
    wait.add_argument("--need", nargs="*", default=["analyze"], type=str, help="Job kinds that must appear.")
    wait.add_argument("--ssh", default="g-house-pc")
    wait.add_argument("--remote-dir", help="Sampler directory holding the abort marker.")
    wait.add_argument("--timeout", type=float, default=900)

    capture = commands.add_parser("capture")
    capture.add_argument("--archive", required=True)
    capture.add_argument("--out", required=True)
    capture.add_argument("--archive-root", required=True, help="The archive's path inside the container.")
    capture.add_argument("--iracing-root", required=True, help="The job root's path inside the container.")
    capture.add_argument("--analysis-id")
    capture.add_argument("--result", help="JSON file with the worker or job result.")
    capture.add_argument("--native-stages", default="", help="The stages the container's worker ran.")

    calls = commands.add_parser("calls")
    calls.add_argument("--oracle", required=True, help="The oracle capture directory (capture-worker).")
    calls.add_argument("--run", required=True, help="g1_cold_run.py's run output, traced.")

    strict = commands.add_parser("strict")
    for side in ("left", "right"):
        strict.add_argument(side)
        strict.add_argument(f"--{side}-archive", required=True)
        strict.add_argument(f"--{side}-iracing", required=True)

    report = commands.add_parser("report")
    report.add_argument("--data", required=True)
    report.add_argument("--out")

    args = parser.parse_args(argv)
    if args.command == "wait":
        args.need = set(args.need)
    return {"wait": command_wait, "capture": command_capture, "calls": command_calls, "strict": command_strict,
            "report": command_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
