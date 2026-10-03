"""Stub MCP worker for coach-server tests (JSON-RPC over stdio, one tool call per process).

Recordings are fake files whose first line is a JSON header; the stub groups them the way the real
discovery does (SubSessionID + session number) and behaves as the header's "behavior" says:
  hang        sleep for an hour (timeout and cancel tests), leaving a child process behind
  block       wait until <archive>/release-<n> exists, where n is the number of files in the group
  oom         kill itself with SIGKILL
  crash       exit with status 3
  iserror     answer with isError true
  orphans     leave a .write-x file and a <pid>.tmp file, then hang
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time


def read_header(path):
    with open(path, "rb") as handle:
        line = handle.readline()
    try:
        return json.loads(line.decode("utf-8"))
    except ValueError:
        return {}


def recordings(root):
    folder = os.path.join(root, "telemetry")
    items = []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".ibt"):
                path = os.path.join(folder, name)
                header = read_header(path)
                items.append((name, path, header))
    return items


def group_id(header, path):
    sub = header.get("subsession_id")
    num = header.get("sim_session_num", 0)
    if sub:
        return "subsession:%s:%s" % (sub, num), "disk_header"
    return "local:%s:%s" % (path, num), "mtime"


def discover(arguments):
    root = os.environ["IRACING_COACH_IRACING_ROOT"]
    groups = {}
    errors = []
    for name, path, header in recordings(root):
        if header.get("behavior") == "discover_error":
            errors.append({"kind": "error", "valid": False, "path": path,
                           "error_type": "IbtTruncatedError", "error": "stub discovery error"})
            continue
        gid, source = group_id(header, path)
        group = groups.setdefault(gid, {"header": header, "source": source, "files": []})
        group["files"].append(path)
    sessions = []
    for gid, group in groups.items():
        header = group["header"]
        sessions.append({
            "kind": "session", "valid": True, "group_id": gid,
            "subsession_id": header.get("subsession_id"),
            "session_id": header.get("session_id"),
            "sim_session_num": header.get("sim_session_num", 0),
            "sim_session_type": header.get("session_type", "Race"),
            "event_type": header.get("session_type", "Race"),
            "is_race": header.get("session_type", "Race") == "Race",
            "track_name": header.get("track", "Stub Speedway"),
            "track_config_name": header.get("track_config"),
            "car_path": header.get("car_path", "stub car"),
            "car_name": header.get("car_name"),
            "start_time_utc": header.get("start", "2026-08-01T12:00:00Z"),
            "time_source": group["source"],
            "file_count": len(group["files"]),
            "files": group["files"],
        })
    return {"root": root, "selection_policy": "stub", "latest_race": sessions[0] if sessions else None,
            "session_count": len(sessions), "returned_session_count": len(sessions),
            "error_count": len(errors), "sessions": sessions, "errors": errors}


def hang(archive, header):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    with open(os.path.join(archive, "child.pid"), "w") as handle:
        handle.write(str(child.pid))
    time.sleep(3600)


def analyze(arguments):
    root = os.environ["IRACING_COACH_IRACING_ROOT"]
    archive = os.environ["IRACING_COACH_DATA"]
    selector = arguments.get("selector", "")
    members = []
    for name, path, header in recordings(root):
        gid, _ = group_id(header, path)
        if selector in (gid, path):
            members.append((name, path, header))
    if not members:
        return None, {"error": "NoSession", "message": "no recording matches " + selector, "tool": "analyze_iracing_race"}
    for _, _, header in members:
        behavior = header.get("behavior")
        if behavior == "hang":
            hang(archive, header)
        elif behavior == "orphans":
            os.makedirs(archive, exist_ok=True)
            open(os.path.join(archive, ".write-stray"), "w").close()
            open(os.path.join(archive, "partial.%d.tmp" % os.getpid()), "w").close()
            hang(archive, header)
        elif behavior == "oom":
            os.kill(os.getpid(), getattr(signal, "SIGKILL", 9))
        elif behavior == "crash":
            sys.stderr.write("stub crash\n")
            sys.exit(3)
        elif behavior == "iserror":
            return None, {"error": "StubToolError", "message": "the stub failed on purpose", "tool": "analyze_iracing_race"}
        elif behavior == "block":
            release = os.path.join(archive, "release-%d" % len(members))
            open(os.path.join(archive, "started-%d" % len(members)), "w").close()
            deadline = time.time() + 60
            while not os.path.exists(release) and time.time() < deadline:
                time.sleep(0.05)
    names = sorted(m[0] for m in members)
    digest = hashlib.sha256((selector + "|" + "|".join(names)).encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(archive, "reports", digest)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "analysis.json")
    body = {"analysis_id": digest, "files": names, "lap_count": 2}
    with open(path, "w") as handle:
        json.dump(body, handle)
    return {
        "ok": True, "analysis_id": digest, "selector": selector, "analysis_path": path,
        "analysis_view": {"schema_version": 14, "analysis_profile_version": "stub-v1", "laps": [{}, {}]},
    }, None


def compare(arguments):
    left, right = arguments["left"], arguments["right"]
    rows = []
    for key in list(left) + [k for k in right if k not in left]:
        a, b = left.get(key), right.get(key)
        status = "unknown" if a is None or b is None else ("identical" if a == b else "changed")
        rows.append({"key": key, "field": key, "readout": key.endswith("CornerWeight"), "status": status,
                     "left": a, "right": b, "unit": None})
    counts = {s: sum(1 for r in rows if r["status"] == s and not r["readout"]) for s in ("changed", "unknown", "identical")}
    return {"schema_version": 1, "scheme": arguments.get("scheme", "recording"), "counts": counts,
            "rows": rows, "equality_rule": "stub", "readout_rule": "stub"}


def catalog(arguments):
    root = os.environ["IRACING_COACH_IRACING_ROOT"]
    entries = []
    base = os.path.join(root, "setups")
    for folder, _, names in os.walk(base):
        for name in sorted(names):
            if name.lower().endswith(".htm"):
                car = os.path.relpath(folder, base).replace("\\", "/").split("/")[0]
                entries.append({
                    "stem": name[:-4], "pair_status": "html_only", "car_folder": car,
                    "sources": {"html": [{"name": name}], "sto": []},
                    "parsed_html": {"fields": {"Chassis.Front.ArbSetting": {"raw": "4"},
                                               "Chassis.Rear.ArbSetting": {"raw": "5"}}},
                    "parse_error": None,
                })
    return {"schema_version": 1, "root": root, "entries": entries, "error_count": 0, "errors": []}


def main():
    lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
    call = None
    for line in lines:
        message = json.loads(line)
        if message.get("method") == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "stub", "version": "0"}}}), flush=True)
        elif message.get("method") == "tools/call":
            call = message
    if call is None:
        return
    name = call["params"]["name"]
    arguments = call["params"].get("arguments", {})
    error = None
    if name == "discover_iracing_sessions":
        result = discover(arguments)
    elif name == "analyze_iracing_race":
        result, error = analyze(arguments)
    elif name == "compare_iracing_setups":
        result = compare(arguments)
    elif name == "catalog_iracing_setups":
        result = catalog(arguments)
    else:
        result = {"ok": True, "tool": name, "arguments": arguments}
    if error is not None:
        payload = {"jsonrpc": "2.0", "id": call["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(error)}], "isError": True}}
    else:
        payload = {"jsonrpc": "2.0", "id": call["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(result)}], "isError": False}}
    print(json.dumps(payload), flush=True)


if __name__ == "__main__":
    main()
