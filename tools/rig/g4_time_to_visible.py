"""Gate G4-9: watch the server for the rig session's recording to appear analysed (stdlib only).

Polls ``GET /api/v1/drivers/{id}/sessions`` once a second from before the sim exits. Every session
not present at start is followed: first seen, each ``analysis_state`` or ``recording_count`` change, and
the first time it is visible: ``analysis_state`` ``ready`` with an ``analysis_id``, at least
--min-recordings recordings, and ``GET /api/v1/analyses/{analysis_id}/race`` answering 200 (workstreams.md
G4; the contract has no GET on ``/sessions/{id}/analysis``, which is POST only).
Prints JSON lines (dev-PC clock, UTC) and exits when a new session is visible, or after --timeout seconds.

    python tools/rig/g4_time_to_visible.py --server http://192.168.1.81:8210 --timeout 900 --start-after 2026-10-03T14:40:00Z
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None


def sessions(server: str, customer: int) -> list[dict]:
    out, cursor = [], None
    for _ in range(1):  # newest first: the first page holds the rig session
        url = f"{server}/api/v1/drivers/{customer}/sessions?limit=200" + (f"&cursor={cursor}" if cursor else "")
        status, body = get(url)
        if status != 200 or not isinstance(body, dict):
            return out
        out += body.get("sessions") or []
        cursor = body.get("next_cursor")
        if not cursor:
            return out
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://192.168.1.81:8210")
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--start-after", default="", help="only sessions whose start_time_utc is at or after this ISO time")
    p.add_argument("--min-recordings", type=int, default=1,
                   help="visible only once the session holds at least this many recordings")
    args = p.parse_args(argv)
    server = args.server.rstrip("/")
    _, drivers = get(f"{server}/api/v1/drivers")
    customer = drivers["drivers"][0]["customer_id"]
    known = {s["session_id"] for s in sessions(server, customer)}
    print(json.dumps({"at": now(), "event": "start", "customer_id": customer, "known_sessions": len(known)}), flush=True)
    states: dict[int, str] = {}
    started = time.monotonic()
    while time.monotonic() - started < args.timeout:
        for s in sessions(server, customer):
            sid = s["session_id"]
            if sid in known or (args.start_after and (s.get("start_time_utc") or "") < args.start_after):
                continue
            state = s.get("analysis_state")
            key = f"{state}|{s.get('recording_count')}|{s.get('analysis_id')}"
            if states.get(sid) != key:
                states[sid] = key
                print(json.dumps({"at": now(), "event": "session", "session_id": sid, "state": state,
                                  "type": s.get("session_type"), "track": s.get("track"), "start": s.get("start_time_utc"),
                                  "recordings": s.get("recording_count"), "analysis_id": s.get("analysis_id"),
                                  "reason": s.get("analysis_reason")}), flush=True)
            aid = s.get("analysis_id")
            if state == "ready" and aid and (s.get("recording_count") or 0) >= args.min_recordings:
                status, _ = get(f"{server}/api/v1/analyses/{aid}/race")
                if status == 200:
                    print(json.dumps({"at": now(), "event": "visible", "session_id": sid, "analysis_id": aid,
                                      "recordings": s.get("recording_count")}), flush=True)
                    known.add(sid)
                    return 0
            if state in ("failed", "deferred"):
                known.add(sid)
        time.sleep(1)
    print(json.dumps({"at": now(), "event": "timeout"}), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
