"""One cold analysis inside the coach-server container, driven exactly as the server drives it.

Gate G1 needs cold runs. The API re-queues an analysis but the worker then answers from its
per-driver analysis cache, and no API call clears that cache, so a cold run uses a fresh archive,
job root and worker home of its own. Same image, interpreter command (``COACH_PYTHON``: since the
native release the image's ``/app/deploy/python3.12-native``, which turns on the native stages
exactly as for the server's workers), script, environment (``server/src/worker.rs``
``worker_env``), ``nice 10``, ``oom_score_adj 1000`` and the same three JSON-RPC lines. The
recordings are copied from read-only sources (the spool or the NAS archive) and given the
committed mtime the server gives them.

The scratch base is ``G1_BASE`` (default ``/data/run/g1-cold``: the data volume's transient
``run/`` directory, on the NVMe as the server's own job roots and worker home are; the 256 MB
``/tmp`` tmpfs cannot hold a 194 MiB recording and its 121 MiB decode store, and would count both
as memory). ``clean`` removes the base and nothing else.

    docker exec -i iracing-coach-app-1 python3.12 - prepare SOURCE NAME MTIME [SOURCE NAME MTIME ...] < g1_cold_run.py
    docker exec -i iracing-coach-app-1 python3.12 - run SELECTOR [--trace] < g1_cold_run.py
    docker exec -i iracing-coach-app-1 python3.12 - clean < g1_cold_run.py

``--trace`` starts the worker through the parity harness's trace shim (the logic of
``tools/parity/worker_trace.py``, written into the base): one line per native decode and the
kernel-call counts of ``native_stages.install_call_counters``, returned under ``trace``.

``run`` reports the worker's own peak (``VmHWM``), the peak of every process of the worker's
tree (decode, merge and kernels children by name), the largest sampled sum of the tree's
``VmRSS`` (processes alive at the same moment) and the container cgroup's ``memory.current``
maximum while it ran. Prints one JSON object per step.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(os.environ.get("G1_BASE", "/data/run/g1-cold"))
SCRIPT = os.environ.get(
    "COACH_WORKER_SCRIPT", "/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py"
)
PYTHON = os.environ.get("COACH_PYTHON", "/usr/local/bin/python3.12")
CGROUP_CURRENT = Path("/sys/fs/cgroup/memory.current")

TRACE_SHIM = '''\
import json, runpy, sys
from pathlib import Path
SCRIPTS = Path(%(scripts)r)
trace = Path(sys.argv[1])
server = SCRIPTS / "mcp_server.py"
sys.argv = [str(server)]
sys.path[0] = str(SCRIPTS)
import native_stages
original = native_stages.load_telemetry
def record(path, error):
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"source": Path(path).name, "error": error}) + "\\n")
def traced(path, *args, **kwargs):
    try:
        result = original(path, *args, **kwargs)
    except BaseException as exc:
        record(path, type(exc).__name__)
        raise
    record(path, None)
    return result
native_stages.load_telemetry = traced
counts, _uninstall = native_stages.install_call_counters()
try:
    runpy.run_path(str(server), run_name="__main__")
finally:
    if counts["native"] or counts["oracle"]:
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kernel_calls": counts}) + "\\n")
'''


def prepare(triples: list[str]) -> dict:
    if not triples or len(triples) % 3:
        raise SystemExit("prepare takes SOURCE NAME MTIME, once per recording")
    if BASE.exists():
        raise SystemExit(f"{BASE} exists: remove the previous run first")
    root, archive, home = BASE / "root", BASE / "archive", BASE / "home"
    for directory in (root / "telemetry", archive, home):
        directory.mkdir(parents=True)
    started = time.perf_counter()
    total = 0
    for index in range(0, len(triples), 3):
        source, name, mtime = triples[index], triples[index + 1], int(triples[index + 2])
        target = root / "telemetry" / name
        shutil.copyfile(source, target)
        os.utime(target, (mtime, mtime))
        total += target.stat().st_size
    return {"step": "prepare", "base": str(BASE), "copy_s": round(time.perf_counter() - started, 3),
            "recordings": len(triples) // 3, "bytes": total}


def _oom_score() -> None:
    os.nice(10)
    with open("/proc/self/oom_score_adj", "w") as handle:
        handle.write("1000")


def _status(pid: int) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("PPid", "VmHWM", "VmRSS"):
            values[key] = int(rest.split()[0])
    return values


def _kind(pid: int) -> str:
    words = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    text = [w.decode("utf-8", "replace") for w in words if w]
    for index, word in enumerate(text):
        if word.endswith("coach-analysis"):
            return "coach-analysis " + (text[index + 1] if index + 1 < len(text) else "")
    return "worker" if any(w.endswith(("mcp_server.py", "worker_trace.py")) for w in text) else (text[0] if text else "?")


def run(selector: str, trace: bool) -> dict:
    root, archive, home = BASE / "root", BASE / "archive", BASE / "home"
    environment = {  # worker_env, as the server passes it; COACH_PYTHON sets the native stages
        "IRACING_COACH_IRACING_ROOT": str(root),
        "IRACING_COACH_DATA": str(archive),
        "IRACING_COACH_RAW_ARCHIVE": "external",
        "IRACING_COACH_NATIVE_STAGES": "",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    arguments = {"selector": selector, "iracing_root": str(root), "archive_root": str(archive), "target_hz": None}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "coach-server", "version": "g1-cold-run"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analyze_iracing_race", "arguments": arguments}},
    ]
    payload = "".join(json.dumps(item) + "\n" for item in requests).encode()
    command = [PYTHON, "-X", "utf8", "-u", SCRIPT]
    trace_file = BASE / "trace.jsonl"
    if trace:
        shim = BASE / "worker_trace.py"
        shim.write_text(TRACE_SHIM % {"scripts": str(Path(SCRIPT).parent)}, encoding="utf-8")
        command = [PYTHON, "-X", "utf8", "-u", str(shim), str(trace_file)]
    peaks: dict[str, dict] = {}
    tree = {"rss_sum_max_kib": 0, "cgroup_current_max_bytes": 0}
    started = time.perf_counter()
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, preexec_fn=_oom_score,
    )

    def sample() -> None:
        while process.poll() is None:
            try:
                tree["cgroup_current_max_bytes"] = max(tree["cgroup_current_max_bytes"], int(CGROUP_CURRENT.read_text()))
            except (OSError, ValueError):
                pass
            members = {process.pid}
            parents: dict[int, int] = {}
            statuses: dict[int, dict[str, int]] = {}
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    try:
                        statuses[int(entry)] = _status(int(entry))
                        parents[int(entry)] = statuses[int(entry)].get("PPid", 0)
                    except OSError:
                        continue
            grew = True
            while grew:
                grew = False
                for pid, parent in parents.items():
                    if parent in members and pid not in members:
                        members.add(pid)
                        grew = True
            rss = 0
            for pid in members:
                status = statuses.get(pid)
                if not status or "VmHWM" not in status:
                    continue
                rss += status.get("VmRSS", 0)
                try:
                    kind = _kind(pid)
                except OSError:
                    continue
                entry = peaks.setdefault(str(pid), {"pid": pid, "kind": kind, "vm_hwm_kib": 0})
                entry["kind"] = kind
                entry["vm_hwm_kib"] = max(entry["vm_hwm_kib"], status["VmHWM"])
            tree["rss_sum_max_kib"] = max(tree["rss_sum_max_kib"], rss)
            time.sleep(0.1)

    (BASE / "worker.pid").write_text(str(process.pid))
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    stdout, stderr = process.communicate(payload, timeout=600)
    wall = time.perf_counter() - started
    sampler.join(timeout=1)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result: dict = {}
    for line in stdout.decode("utf-8").splitlines():
        if line.strip():
            message = json.loads(line)
            if message.get("id") == 2:
                content = message["result"]
                answer = json.loads(content["content"][0]["text"])
                result = {
                    "is_error": bool(content.get("isError")),
                    "analysis_id": answer.get("analysis_id"),
                    "analysis_path": answer.get("analysis_path"),
                }
                if result["is_error"]:
                    result["error"] = answer
    worker = [p for p in peaks.values() if p["pid"] == process.pid]
    out = {
        "step": "run",
        "command": command[0],
        "exit_code": process.returncode,
        "wall_s": round(wall, 3),
        "ru_maxrss_kib": usage.ru_maxrss,
        "ru_utime_s": round(usage.ru_utime, 3),
        "ru_stime_s": round(usage.ru_stime, 3),
        "vm_hwm_kib_sampled": worker[0]["vm_hwm_kib"] if worker else 0,
        "processes": sorted(peaks.values(), key=lambda p: p["pid"]),
        "tree_rss_sum_max_kib": tree["rss_sum_max_kib"],
        "cgroup_memory_current_max_bytes": tree["cgroup_current_max_bytes"],
        "leftover_stores": sorted(p.name for p in home.iterdir() if p.name.startswith(("coach-decode-", "coach-merge-"))),
        "stderr_tail": stderr.decode("utf-8", "replace")[-2000:],
        **result,
    }
    if trace:
        lines = trace_file.read_text(encoding="utf-8").splitlines() if trace_file.exists() else []
        records = [json.loads(line) for line in lines if line.strip()]
        out["trace"] = {
            "native_decode_calls": [r for r in records if "source" in r],
            "kernel_calls": next((r["kernel_calls"] for r in records if "kernel_calls" in r), {"native": {}, "oracle": {}}),
        }
    return out


def main(argv: list[str]) -> int:
    command = argv[0]
    if command == "prepare":
        print(json.dumps(prepare(argv[1:])))
    elif command == "run":
        print(json.dumps(run(argv[1], "--trace" in argv[2:])))
    elif command == "clean":
        shutil.rmtree(BASE, ignore_errors=True)
        print(json.dumps({"step": "clean", "base": str(BASE), "exists": BASE.exists()}))
    else:
        raise SystemExit(f"unknown step {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
