"""One cold analysis inside the coach-server container, driven exactly as the server drives it.

Gate G1 needs two cold runs. The API re-queues an analysis but the worker then answers from its
per-driver analysis cache, and no API call clears that cache, so the second cold run uses a fresh
archive and job root in the container's /tmp (its own scratch; nothing under /data is written).
Same image, interpreter, script, environment (``server/src/worker.rs`` ``worker_env``), ``nice
10``, ``oom_score_adj 1000`` and the same three JSON-RPC lines. The recording is copied from a
read-only source (the spool or the NAS archive) and given the committed mtime the server gives it.

    docker exec -i iracing-coach-app-1 python3.12 - prepare SOURCE NAME MTIME < g1_cold_run.py
    docker exec -i iracing-coach-app-1 python3.12 - run SELECTOR < g1_cold_run.py
    docker exec -i iracing-coach-app-1 python3.12 - clean < g1_cold_run.py

Prints one JSON object per step.
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

BASE = Path("/tmp/g1-cold")
SCRIPT = os.environ.get(
    "COACH_WORKER_SCRIPT", "/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py"
)
PYTHON = os.environ.get("COACH_PYTHON", "/usr/local/bin/python3.12")


def prepare(source: str, name: str, mtime: int) -> dict:
    if BASE.exists():
        raise SystemExit(f"{BASE} exists: remove the previous run first")
    root, archive, home = BASE / "root", BASE / "archive", BASE / "home"
    for directory in (root / "telemetry", archive, home):
        directory.mkdir(parents=True)
    target = root / "telemetry" / name
    started = time.perf_counter()
    shutil.copyfile(source, target)
    os.utime(target, (mtime, mtime))
    return {"step": "prepare", "copy_s": round(time.perf_counter() - started, 3), "bytes": target.stat().st_size}


def _oom_score() -> None:
    os.nice(10)
    with open("/proc/self/oom_score_adj", "w") as handle:
        handle.write("1000")


def run(selector: str) -> dict:
    root, archive, home = BASE / "root", BASE / "archive", BASE / "home"
    environment = {
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
    peak = {"vm_hwm_kib": 0}
    started = time.perf_counter()
    process = subprocess.Popen(
        [PYTHON, "-X", "utf8", "-u", SCRIPT],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, preexec_fn=_oom_score,
    )

    def sample() -> None:
        status = Path(f"/proc/{process.pid}/status")
        while process.poll() is None:
            try:
                for line in status.read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        peak["vm_hwm_kib"] = max(peak["vm_hwm_kib"], int(line.split()[1]))
            except OSError:
                break
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
    return {
        "step": "run",
        "exit_code": process.returncode,
        "wall_s": round(wall, 3),
        "ru_maxrss_kib": usage.ru_maxrss,
        "ru_utime_s": round(usage.ru_utime, 3),
        "ru_stime_s": round(usage.ru_stime, 3),
        "vm_hwm_kib_sampled": peak["vm_hwm_kib"],
        "stderr_tail": stderr.decode("utf-8", "replace")[-2000:],
        **result,
    }


def main(argv: list[str]) -> int:
    command = argv[0]
    if command == "prepare":
        print(json.dumps(prepare(argv[1], argv[2], int(argv[3]))))
    elif command == "run":
        print(json.dumps(run(argv[1])))
    elif command == "clean":
        shutil.rmtree(BASE, ignore_errors=True)
        print(json.dumps({"step": "clean", "exists": BASE.exists()}))
    else:
        raise SystemExit(f"unknown step {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
