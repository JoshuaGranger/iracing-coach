#!/usr/bin/env python3
"""Wall time and peak memory of one worker analysis, with and without native stages.

Drives ``mcp_server.py`` as coach-server does (``worker-interface.md``
sections 2 to 4: complete environment, a job root holding the recording, the
discovered group id as selector, ``target_hz: null``), one cold process per
run in a fresh archive, alternating Python and native runs. Peak memory is the
worker process's own peak working set (Windows, ``GetProcessMemoryInfo``) or
``ru_maxrss`` (Linux, ``wait4``). With native decode the worker starts
``coach-analysis decode`` as a child, whose peak is not part of the worker's;
the decoder is therefore also measured alone on the same request.

    python tools/parity/measure_worker.py --source RACE.ibt --native-bin target/release/coach-analysis.exe --runs 3 --base SCRATCH --out report.json
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


def _run(command: list[str], environment: dict[str, str], stdin: bytes) -> tuple[bytes, bytes, int, float, int]:
    started = time.perf_counter()
    if os.name == "nt":
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        out, err = process.communicate(stdin)
        wall = time.perf_counter() - started
        return out, err, process.returncode, wall, _peak_after_exit(process)
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
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base", required=True, help="Harness-owned scratch (wiped).")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    native_bin = str(Path(args.native_bin).resolve())
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
    for index in range(args.runs):
        for mode, stages in (("python", ""), ("native", args.native_stages)):
            worker = job(stages)
            if worker.archive.exists():
                shutil.rmtree(worker.archive)
            worker.archive.mkdir(parents=True)
            stdin = _requests(
                "analyze_iracing_race",
                {"selector": selector, "iracing_root": str(root), "archive_root": str(worker.archive), "target_hz": None},
            )
            out, err, code, wall, peak = _run(command, worker.environment(root), stdin)
            if code != 0:
                raise SystemExit(f"{mode} run {index + 1}: exit {code}\n{err.decode('utf-8', 'replace')[-2000:]}")
            payload = _result(out, f"{mode} run {index + 1}")
            runs.append({"mode": mode, "run": index + 1, "wall_s": round(wall, 3), "peak_bytes": peak,
                         "analysis_id": payload.get("analysis_id")})
            print(f"{mode} run {index + 1}: wall {wall:.2f} s, peak {peak / 2**30:.3f} GiB, {payload.get('analysis_id')}", flush=True)

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

    report = {
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "python": parity.platform.python_version(),
        "platform": sys.platform,
        "native_binary": {"path": native_bin, "sha256": parity._sha256_file(Path(native_bin))},
        "selector_kind": "path" if selector == str(recording) else "group_id",
        "channels": len(channels),
        "runs": runs,
        "decoder_alone": decoder,
        "median": {
            mode: {"wall_s": median(mode, "wall_s"), "peak_gib": round(median(mode, "peak_bytes") / 2**30, 3)}
            for mode in ("python", "native")
        },
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
