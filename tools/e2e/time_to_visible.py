"""Uploads one recording through the real upload API and times it until it is analysed and visible.

Runs `tools/rig/g1_upload.py` (the collector-protocol section 4 client), then polls the server as the
web app does: the jobs the upload caused (discover, archive, analyze, project), the session row's
`analysis_state`, and the projected views of the current analysis. Prints one JSON report: upload
duration, each job's times, NAS state from `/api/v1/status`, and seconds from `verified` to the first
moment the session lists `ready` with its race view served.

Standard library only; the recording is opened read-only.

    python time_to_visible.py --server http://192.168.1.81:8210 --file RECORDING.ibt
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPLOAD = HERE.parent / "rig" / "g1_upload.py"


def get(base: str, path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            body = r.read()
            return r.status, json.loads(body) if body[:1] in (b"{", b"[") else len(body)
    except urllib.error.HTTPError as e:
        return e.code, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args()
    base = a.server.rstrip("/")

    jobs_before = {j["job_id"] for j in get(base, "/api/v1/jobs")[1]["jobs"]}
    t_start = time.time()
    run = subprocess.run([sys.executable, str(UPLOAD), "--server", base, "--file", a.file], capture_output=True, text=True, check=False)
    t_verified = time.time()
    try:
        upload = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        print(json.dumps({"error": "upload failed", "stdout": run.stdout[-2000:], "stderr": run.stderr[-2000:]}))
        return 1
    sha = upload.get("sha256")

    report: dict = {"upload": upload, "upload_wall_s": round(t_verified - t_start, 2)}
    session_id = None
    analysis_id = None
    visible_at = None
    while time.time() - t_verified < a.timeout:
        jobs = [j for j in get(base, "/api/v1/jobs")[1]["jobs"] if j["job_id"] not in jobs_before]
        for j in jobs:
            subject = j.get("subject") or {}
            if j["kind"] == "analyze" and any(r.get("sha256") == sha for r in subject.get("recordings", [])):
                session_id = subject.get("session_id")
        if session_id is not None:
            customer = next((j["customer_id"] for j in jobs if j.get("customer_id")), None)
            sessions = get(base, f"/api/v1/drivers/{customer}/sessions")[1]["sessions"]
            row = next((s for s in sessions if s["session_id"] == session_id), None)
            if row and row["analysis_state"] in ("failed", "deferred"):
                report["session"] = row
                break
            if row and row["analysis_state"] == "ready" and row["analysis_id"]:
                analysis_id = row["analysis_id"]
                status, _ = get(base, f"/api/v1/analyses/{analysis_id}/race")
                if status == 200:
                    visible_at = time.time()
                    report["session"] = {k: row[k] for k in ("session_id", "session_type", "track", "analysis_state", "analysis_id", "recording_count")}
                    break
        time.sleep(0.25)

    jobs = [j for j in get(base, "/api/v1/jobs")[1]["jobs"] if j["job_id"] not in jobs_before]
    report["jobs"] = [{k: j[k] for k in ("job_id", "kind", "state", "created_at", "started_at", "finished_at", "error_code")} for j in sorted(jobs, key=lambda j: j["job_id"])]
    if analysis_id:
        sizes = {}
        for view in ("race", "runs", "investigation", "traces/catalog", "track"):
            t = time.time()
            status, body = get(base, f"/api/v1/analyses/{analysis_id}/{view}")
            sizes[view] = {"status": status, "ms": round((time.time() - t) * 1000)}
        report["views"] = sizes
    report["status"] = get(base, "/api/v1/status")[1]
    report["verified_to_visible_s"] = round(visible_at - t_verified, 2) if visible_at else None
    print(json.dumps(report, indent=1))
    return 0 if visible_at else 1


if __name__ == "__main__":
    sys.exit(main())
