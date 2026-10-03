"""Stub MCP worker for coach-server tests (JSON-RPC over stdio, one tool call per process).

Recordings are fake files whose first line is a JSON header; the stub groups them the way the real
discovery does (SubSessionID + session number) and behaves as the header's "behavior" says:
  hang        sleep for an hour (timeout and cancel tests), leaving a child process behind
  block       wait until <archive>/release-<n> exists, where n is the number of files in the group
  oom         kill itself with SIGKILL
  crash       exit with status 3
  iserror     answer with isError true
  orphans     leave a .write-x file and a <pid>.tmp file, then hang
  closeout    close stdout without exiting, then hang
  ignoreterm  leave a child that ignores SIGTERM in the process group, then hang
  badresult   answer an analysis without its analysis_view (breaks analyze-result-v1)

Results of analyze_iracing_race, build_open_setup_package and
recommend_structured_open_setup_tuning are built from the sanitized fixtures in test-data/ so
they meet their Python contracts; an argument "stub_invalid" breaks the other two.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "test-data")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


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


def stubborn_child(archive):
    code = (
        "import os, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    subprocess.Popen([sys.executable, "-c", code, os.path.join(archive, "stubborn.pid")])
    time.sleep(0.5)


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
    for _, path, _ in members:
        # the real worker refuses a recording written in the last 2 seconds (finalization)
        if time.time() - os.path.getmtime(path) < 2:
            return None, {"error": "IbtNotFinalized", "message": "recording changed in the last 2 seconds",
                          "tool": "analyze_iracing_race"}
    for _, _, header in members:
        behavior = header.get("behavior")
        if behavior == "hang":
            hang(archive, header)
        elif behavior == "orphans":
            os.makedirs(archive, exist_ok=True)
            open(os.path.join(archive, ".write-stray"), "w").close()
            open(os.path.join(archive, "partial.%d.tmp" % os.getpid()), "w").close()
            hang(archive, header)
        elif behavior == "ignoreterm":
            os.makedirs(archive, exist_ok=True)
            stubborn_child(archive)
            time.sleep(3600)
        elif behavior == "closeout":
            os.makedirs(archive, exist_ok=True)
            open(os.path.join(archive, "closed"), "w").close()
            sys.stdout.close()
            os.close(1)
            time.sleep(3600)
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
    result = fixture("analyze-repair-heavy.json")
    view = fixture("analysis-view-valid.json")
    view["analysis_profile_version"] = "stub-v1"
    view["laps"] = [{"lap": 1, "lap_time_s": 30.5}, {"lap": 2, "lap_time_s": 30.1}]
    result.update({
        "analysis_id": digest, "selector": selector, "analysis_path": path,
        "source_files": names, "analysis_view": view,
    })
    if any(header.get("behavior") == "badresult" for _, _, header in members):
        del result["analysis_view"]
    return result, None


def package(arguments):
    result = fixture("setup-package.json")
    if arguments.get("stub_invalid"):
        result["status"] = "not-a-status"
    return result


def recommendation(arguments):
    result = fixture("setup-recommendation.json")
    if arguments.get("stub_invalid"):
        del result["recommendation"]
    return result


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


def legacy_import(arguments):
    iracing = os.path.abspath(os.environ["IRACING_COACH_IRACING_ROOT"])
    root = os.path.abspath(arguments["legacy_archive_root"])
    if not root.startswith(iracing + os.sep):
        return None, {"error": "ValueError", "message": "legacy_archive_root is outside the iRacing root",
                      "tool": "import_legacy_tuning_experiments"}
    mapping = {m["legacy_analysis_id"]: m for m in arguments["analysis_map"]}
    found, files = [], []
    for folder, _, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(folder, name)
            files.append(os.path.relpath(path, root).replace(os.sep, "/"))
            if name.endswith(".json") and os.sep + "experiments" + os.sep in path:
                with open(path, "r", encoding="utf-8") as handle:
                    found.append(json.load(handle))
    resolved = sum(1 for e in found if (mapping.get(e.get("analysis_id")) or {}).get("analysis_id"))
    archive = os.environ["IRACING_COACH_DATA"]
    os.makedirs(archive, exist_ok=True)
    with open(os.path.join(archive, "import-call.json"), "w") as handle:
        json.dump({"files": sorted(files), "analysis_map": arguments["analysis_map"], "root": root}, handle)
    return {"ok": True, "contract": "legacy-tuning-import-v1", "dry_run": False, "archive_root": archive,
            "experiment_count": len(found), "resolved_count": resolved,
            "unresolved_count": len(found) - resolved, "experiments": []}, None


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
    elif name == "import_legacy_tuning_experiments":
        result, error = legacy_import(arguments)
    elif name == "build_open_setup_package":
        result = package(arguments)
    elif name == "recommend_structured_open_setup_tuning":
        result = recommendation(arguments)
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
