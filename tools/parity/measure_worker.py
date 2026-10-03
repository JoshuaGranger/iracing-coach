#!/usr/bin/env python3
"""Wall time and peak memory of one worker analysis, with and without native stages.

Drives ``mcp_server.py`` as coach-server does (``worker-interface.md``
sections 2 to 4: complete environment, a job root holding the recording, the
discovered group id as selector, ``target_hz: null``), one cold process per
run in a fresh archive, alternating baseline and native runs (the baseline is
pure Python unless ``--baseline-stages`` names stages, such as ``decode`` to
measure the Python kernels against ``decode,kernels``). Peak memory
(``peak_kind`` in the report): on Windows the worker process's own peak
working set (``GetProcessMemoryInfo``); on Linux ``wait4``'s ``ru_maxrss``,
which is the largest peak RSS of the worker *and of every child it waited
for* (the decode, kernels, merge and write children), not the worker's own
and not a simultaneous tree peak (Codex phase-3 review, finding 6). With
native stages the worker starts ``coach-analysis`` children: on Windows each
run is a job object, whose accounting adds the tree's peak committed memory
(every process of the run at the same moment) and its process count; the
decoder is also measured alone on the same request.

    python tools/parity/measure_worker.py --source RACE.ibt --native-bin target/release/coach-analysis.exe --runs 3 --base SCRATCH --out report.json
    python tools/parity/measure_worker.py --source RACE.ibt --native-bin ... --baseline-stages decode --native-stages decode,kernels --runs 5 ...
    python tools/parity/measure_worker.py --source RACE.ibt --native-bin NEW --baseline-bin OLD --baseline-stages decode,kernels --native-stages decode,kernels ...

``--baseline-bin`` gives the baseline side its own executable (default: the
native side's), so two builds of the same stages can be compared (phase 3:
the store read whole by the phase-2 kernels child against the mapped store).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis_parity as parity  # noqa: E402

sys.path.insert(0, str(parity.SCRIPTS))
import ibt_reader  # noqa: E402
import workflow  # noqa: E402


PEAK_KIND = (
    "worker peak working set (GetProcessMemoryInfo, the worker process only)"
    if os.name == "nt"
    else "largest peak RSS of the worker and each child it waited for (wait4 ru_maxrss; not worker-only, not simultaneous)"
)
PEAK_LABEL = "worker peak" if os.name == "nt" else "worker-or-child peak RSS"


def _peak_after_exit(process: subprocess.Popen[bytes]) -> int:
    """Peak resident bytes of a process that has exited (its handle still open)."""

    if os.name == "nt":
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)
    raise RuntimeError("use _run_linux on POSIX")


class _Job:
    """A Windows job object around one worker and every process it starts.

    Its accounting gives the tree's peak committed memory (the worker and its
    ``coach-analysis`` children together, at the same moment) and the number
    of processes that ran in it. The worker is assigned right after it starts,
    before it can start a child (its interpreter is still importing).
    """

    def __init__(self) -> None:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        self.ctypes, self.wintypes = ctypes, wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        handle = self.wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not self.kernel32.AssignProcessToJobObject(self.handle, handle):
            raise OSError(self.ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def accounting(self) -> dict[str, int]:
        ctypes, wintypes = self.ctypes, self.wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        extended, accounting = Extended(), Accounting()
        for kind, value in ((9, extended), (1, accounting)):  # JobObjectExtendedLimitInformation, BasicAccounting
            if not self.kernel32.QueryInformationJobObject(self.handle, kind, ctypes.byref(value), ctypes.sizeof(value), None):
                raise OSError(ctypes.get_last_error(), "QueryInformationJobObject failed")
        return {
            "tree_peak_commit_bytes": int(extended.PeakJobMemoryUsed),
            "process_peak_commit_bytes": int(extended.PeakProcessMemoryUsed),
            "processes": int(accounting.TotalProcesses),
        }

    def close(self) -> None:
        self.kernel32.CloseHandle(self.handle)


def _run(command: list[str], environment: dict[str, str], stdin: bytes, tree: dict[str, int] | None = None) -> tuple[bytes, bytes, int, float, int]:
    """Run one process; ``tree`` (Windows) receives its process tree's job accounting."""

    started = time.perf_counter()
    if os.name == "nt":
        job = _Job() if tree is not None else None
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            if job is not None:
                job.assign(process)
            out, err = process.communicate(stdin)
            wall = time.perf_counter() - started
            if job is not None:
                tree.update(job.accounting())  # type: ignore[union-attr]
            return out, err, process.returncode, wall, _peak_after_exit(process)
        finally:
            if job is not None:
                job.close()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    assert process.stdin and process.stdout and process.stderr
    process.stdin.write(stdin)
    process.stdin.close()
    out, err = process.stdout.read(), process.stderr.read()
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    wall = time.perf_counter() - started
    return out, err, process.returncode, wall, int(usage.ru_maxrss) * 1024


def _requests(tool: str, arguments: dict[str, Any]) -> bytes:
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "coach-server", "version": "measure-worker"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
    ]
    return "".join(json.dumps(line) + "\n" for line in lines).encode("utf-8")


def _result(stdout: bytes, label: str) -> dict[str, Any]:
    for line in stdout.decode("utf-8").splitlines():
        if line.strip():
            response = json.loads(line)
            if response.get("id") == 2:
                result = response["result"]
                payload = json.loads(result["content"][0]["text"])
                if result.get("isError"):
                    raise SystemExit(f"{label}: tool_error {payload}")
                return payload
    raise SystemExit(f"{label}: no response")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--native-bin", required=True)
    parser.add_argument("--native-stages", default="decode")
    parser.add_argument(
        "--baseline-stages",
        default="",
        help="Stages of the comparison side (default empty: pure Python; 'decode' measures the Python kernels).",
    )
    parser.add_argument("--baseline-bin", default=None, help="Executable of the baseline side (default: --native-bin).")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base", required=True, help="Harness-owned scratch (wiped).")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    native_bin = str(Path(args.native_bin).resolve())
    baseline_bin = str(Path(args.baseline_bin).resolve()) if args.baseline_bin else native_bin
    os.environ[parity.NATIVE_BIN_ENV] = native_bin
    base = Path(args.base).resolve()
    parity._prepare_owned(base, parity.BASE_MARKER)
    spool = base / "spool"
    spool.mkdir()
    spooled = parity._stage(spool, source)
    root = parity._job_root(base, "analysis", [(source.name, spooled)], restage=False)
    recording = root / "telemetry" / source.name

    def job(stages: str) -> parity.WorkerJob:
        worker = parity.WorkerJob(base, stages, "external")
        worker.archive.mkdir(parents=True, exist_ok=True)
        worker.tmp.mkdir(parents=True, exist_ok=True)
        return worker

    discovered = job("").call(root, "discover_iracing_sessions", {"root": str(root), "races_only": False, "limit": 50}, 120)
    group = discovered["sessions"][0]
    selector = str(recording) if group.get("time_source") == "mtime" else str(group["group_id"])
    command = [parity._python(), "-X", "utf8", "-u", str(parity.SCRIPTS / "mcp_server.py")]

    runs: list[dict[str, Any]] = []
    modes = (("python", args.baseline_stages), ("native", args.native_stages))
    for index in range(args.runs):
        for mode, stages in modes:
            worker = job(stages)
            if worker.archive.exists():
                shutil.rmtree(worker.archive)
            worker.archive.mkdir(parents=True)
            stdin = _requests(
                "analyze_iracing_race",
                {"selector": selector, "iracing_root": str(root), "archive_root": str(worker.archive), "target_hz": None},
            )
            tree: dict[str, int] | None = {} if os.name == "nt" else None
            environment = worker.environment(root)
            if stages.strip():
                environment[parity.NATIVE_BIN_ENV] = baseline_bin if mode == "python" else native_bin
            out, err, code, wall, peak = _run(command, environment, stdin, tree)
            if code != 0:
                raise SystemExit(f"{mode} run {index + 1}: exit {code}\n{err.decode('utf-8', 'replace')[-2000:]}")
            payload = _result(out, f"{mode} run {index + 1}")
            runs.append({"mode": mode, "stages": stages, "run": index + 1, "wall_s": round(wall, 3), "peak_bytes": peak,
                         **(tree or {}), "analysis_id": payload.get("analysis_id")})
            print(
                f"{mode} ({stages or 'python'}) run {index + 1}: wall {wall:.2f} s, {PEAK_LABEL} {peak / 2**30:.3f} GiB"
                + (f", tree peak commit {tree['tree_peak_commit_bytes'] / 2**30:.3f} GiB in {tree['processes']} processes" if tree else "")
                + f", {payload.get('analysis_id')}",
                flush=True,
            )

    available = {variable["name"] for variable in ibt_reader.scan_ibt(recording)["variables"]}
    channels = [name for name in workflow.ANALYSIS_CHANNELS if name in available]
    store = base / "decode.ccs"
    request = json.dumps({"version": 1, "source": str(recording), "out": str(store), "channels": channels, "target_hz": None})
    decoder: list[dict[str, Any]] = []
    for index in range(args.runs):
        out, err, code, wall, peak = _run([native_bin, "decode"], dict(os.environ), request.encode("utf-8"))
        if code != 0:
            raise SystemExit(f"decoder: {err.decode('utf-8', 'replace')}")
        decoder.append({"run": index + 1, "wall_s": round(wall, 3), "peak_bytes": peak, **json.loads(out)})
        print(f"decoder run {index + 1}: wall {wall:.3f} s, peak {peak / 2**20:.1f} MiB", flush=True)

    def median(mode: str, key: str) -> float:
        return statistics.median(run[key] for run in runs if run["mode"] == mode)

    def span(mode: str, key: str, scale: float = 1.0) -> list[float]:
        values = [run[key] / scale for run in runs if run["mode"] == mode]
        return [round(min(values), 3), round(max(values), 3)]

    def summary(mode: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "stages": dict(modes)[mode],
            "wall_s": median(mode, "wall_s"),
            "wall_s_range": span(mode, "wall_s"),
            "peak_gib": round(median(mode, "peak_bytes") / 2**30, 3),
            "peak_gib_range": span(mode, "peak_bytes", 2**30),
        }
        if os.name == "nt":
            out["tree_peak_commit_gib"] = round(median(mode, "tree_peak_commit_bytes") / 2**30, 3)
            out["tree_peak_commit_gib_range"] = span(mode, "tree_peak_commit_bytes", 2**30)
            out["processes"] = sorted({run["processes"] for run in runs if run["mode"] == mode})
            out["largest_process_peak_commit_gib"] = round(median(mode, "process_peak_commit_bytes") / 2**30, 3)
        return out

    report = {
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "python": parity.platform.python_version(),
        "platform": sys.platform,
        "native_binary": {"path": native_bin, "sha256": parity._sha256_file(Path(native_bin))},
        "baseline_binary": {"path": baseline_bin, "sha256": parity._sha256_file(Path(baseline_bin))},
        "selector_kind": "path" if selector == str(recording) else "group_id",
        "channels": len(channels),
        "peak_kind": PEAK_KIND,
        "runs": runs,
        "decoder_alone": decoder,
        "median": {mode: summary(mode) for mode in ("python", "native")},
        "decoder_median": {
            "wall_s": statistics.median(item["wall_s"] for item in decoder),
            "peak_mib": round(statistics.median(item["peak_bytes"] for item in decoder) / 2**20, 1),
            "store_bytes": decoder[0]["bytes"],
        },
        "analysis_ids_equal": len({run["analysis_id"] for run in runs}) == 1,
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["median"], indent=2), json.dumps(report["decoder_median"]))
    shutil.rmtree(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
