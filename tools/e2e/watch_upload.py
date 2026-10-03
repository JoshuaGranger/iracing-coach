"""Polls `HEAD /api/v1/uploads/{sha256}` and prints the offset and state timeline as JSON lines.

Used by the dev-PC end to end (`docs/08-rewrite/acceptance/e2e-dev-pc.md`):
- `--kill-pid PID --kill-at BYTES` stops a collector process this run started once the server's
  durable offset reaches BYTES (the restart-mid-upload check);
- `--until-state verified` ends when the server holds the file.

Standard library only; read-only against the server.

    python watch_upload.py --server http://127.0.0.1:8291 --sha 582b... --until-state verified
"""

from __future__ import annotations

import argparse
import http.client
import json
import subprocess
import sys
import time
import urllib.parse


def head(host: str, port: int, sha: str) -> tuple[int, int | None, str | None]:
    c = http.client.HTTPConnection(host, port, timeout=5)
    try:
        c.request("HEAD", f"/api/v1/uploads/{sha}")
        r = c.getresponse()
        r.read()
        offset = r.getheader("Upload-Offset")
        return r.status, int(offset) if offset is not None else None, r.getheader("Upload-State")
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--interval", type=float, default=0.02)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--kill-pid", type=int)
    ap.add_argument("--kill-at", type=int, default=0)
    ap.add_argument("--until-state", default="verified")
    a = ap.parse_args()
    parts = urllib.parse.urlsplit(a.server)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    start = time.monotonic()
    last = None
    killed = False
    done_states = {a.until_state, "parsed", "discovered", "archived", "analysis_queued", "analyzed", "deferred"}
    while time.monotonic() - start < a.timeout:
        try:
            status, offset, state = head(host, port, a.sha)
        except OSError as error:
            status, offset, state = -1, None, str(error)
        now = round(time.monotonic() - start, 3)
        if (status, offset, state) != last:
            print(json.dumps({"t": now, "wall": time.strftime("%H:%M:%S"), "status": status, "offset": offset, "state": state}), flush=True)
            last = (status, offset, state)
        if a.kill_pid and not killed and offset is not None and offset >= a.kill_at:
            subprocess.run(["taskkill", "/F", "/PID", str(a.kill_pid)], capture_output=True, check=False)
            killed = True
            print(json.dumps({"t": now, "event": "killed", "pid": a.kill_pid, "offset": offset}), flush=True)
            if a.until_state == "killed":
                return 0
        if state in done_states and a.until_state != "killed":
            return 0
        time.sleep(a.interval)
    print(json.dumps({"event": "timeout"}), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
