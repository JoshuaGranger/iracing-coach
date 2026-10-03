"""D12 step 5: completeness figures for docs/08-rewrite/acceptance/migration.md (stdlib only).

Reads the live service over HTTP as a browser would (no writes) and the NAS archive read-only over
SMB, and compares them with the legacy inventories:

- every session of the driver, with its recordings (`GET /api/v1/sessions/{id}`), analysis state and type;
- every recording hash checked against `$NAS/raw/<aa>/<sha256>.manifest.json` (nas-layout.md);
- the legacy raw archive (`--archive`: build-time recordings map, folder sha256 -> original name) and
  the backfill log (`--backfill-log`, from Backfill-RawArchive.ps1): each hash held, with a manifest;
- the sim's telemetry folder (`--telemetry-names`: one file name per line, listed at the end of the
  run): each name matched to a held recording by its original name;
- the setup library (count of setups, names, unlinked files);
- the old-to-new map for every `legacy_analyses` entry of the ImportBundle (`--bundle`), resolved the
  way the server's import does (recording hash, then subsession and session number).

    python tools/migrate/migration_report.py --server http://192.168.1.81:8210 --customer-id 1507810
        --nas //192.168.1.82/apps/iracing-coach --archive archive-recordings.json
        --backfill-log backfill-raw.jsonl --telemetry-names telemetry-names.txt --bundle import-bundle.json
        --out report.json

Writes the full JSON to --out (outside the repository: it names private recordings) and prints a summary.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def get(server: str, path: str) -> dict:
    with urllib.request.urlopen(server + path, timeout=30) as response:
        return json.loads(response.read())


def pages(server: str, path: str, key: str) -> list[dict]:
    out, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}limit=200" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        body = get(server, url)
        out += body.get(key) or []
        cursor = body.get("next_cursor")
        if not cursor:
            return out


def read_lines_json(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().lstrip("﻿")
        if line:
            rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://192.168.1.81:8210")
    p.add_argument("--customer-id", type=int, required=True)
    p.add_argument("--nas", default="//192.168.1.82/apps/iracing-coach")
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--backfill-log", type=Path)
    p.add_argument("--telemetry-names", type=Path)
    p.add_argument("--bundle", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    server = args.server.rstrip("/")
    nas = Path(args.nas)

    sessions = pages(server, f"/api/v1/drivers/{args.customer_id}/sessions", "sessions")
    recordings: dict[str, dict] = {}
    by_session: dict[int, dict] = {}
    for s in sessions:
        detail = get(server, f"/api/v1/sessions/{s['session_id']}")
        by_session[s["session_id"]] = detail
        for r in detail.get("recordings") or []:
            entry = recordings.setdefault(r["sha256"], {**r, "session_ids": []})
            entry["session_ids"].append(s["session_id"])

    def manifest(sha: str) -> bool:
        return (nas / "raw" / sha[:2] / f"{sha}.manifest.json").is_file()

    nas_ok = {sha: manifest(sha) for sha in recordings}

    archive = json.loads(args.archive.read_text(encoding="utf-8"))["files"]
    archive_rows = []
    for f in archive:
        sha = f["sha256"]
        archive_rows.append({"sha256": sha, "name": Path(f["path"]).name, "held": sha in recordings,
                             "nas_manifest": manifest(sha), "session_ids": recordings.get(sha, {}).get("session_ids", [])})
    backfill = read_lines_json(args.backfill_log) if args.backfill_log else []

    names_held = collections.defaultdict(list)
    for sha, r in recordings.items():
        names_held[r["file_name"].casefold()].append(sha)
    telemetry_rows = []
    if args.telemetry_names:
        for name in args.telemetry_names.read_text(encoding="utf-8-sig").splitlines():
            name = name.strip()
            if not name:
                continue
            shas = names_held.get(name.casefold(), [])
            telemetry_rows.append({"name": name, "sha256": shas, "held": bool(shas),
                                   "nas_manifest": bool(shas) and all(nas_ok.get(s, False) for s in shas)})

    setups = pages(server, f"/api/v1/drivers/{args.customer_id}/setups", "setups")
    first = get(server, f"/api/v1/drivers/{args.customer_id}/setups?limit=1")

    legacy_map = []
    if args.bundle:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        for entry in bundle.get("legacy_analyses") or []:
            session_id, via = None, None
            for sha in entry.get("recording_sha256") or []:
                if sha in recordings:
                    session_id, via = recordings[sha]["session_ids"][0], "recording_sha256"
                    break
            if session_id is None and entry.get("subsession_id"):
                for s in sessions:
                    if s.get("subsession_id") == entry["subsession_id"] and (
                        entry.get("sim_session_num") is None or s.get("sim_session_num") == entry["sim_session_num"]
                    ):
                        session_id, via = s["session_id"], "subsession"
                        break
            s = by_session.get(session_id, {}).get("session", {}) if session_id else {}
            legacy_map.append({
                "legacy_analysis_id": entry["legacy_analysis_id"],
                "subsession_id": entry.get("subsession_id"),
                "sim_session_num": entry.get("sim_session_num"),
                "recording_sha256": entry.get("recording_sha256") or [],
                "session_id": session_id,
                "resolved_by": via,
                "analysis_id": s.get("analysis_id"),
                "analysis_state": s.get("analysis_state"),
                "track": s.get("track"),
                "start_time_utc": s.get("start_time_utc"),
            })

    report = {
        "sessions": len(sessions),
        "sessions_by_type": dict(collections.Counter(s.get("session_type") for s in sessions)),
        "sessions_by_analysis_state": dict(collections.Counter(s.get("analysis_state") for s in sessions)),
        "sessions_by_reason": dict(collections.Counter(s.get("analysis_reason") for s in sessions if s.get("analysis_reason"))),
        "recordings": len(recordings),
        "recordings_bytes": sum(r["size"] for r in recordings.values()),
        "recordings_by_nas_state": dict(collections.Counter(r.get("nas_state") for r in recordings.values())),
        "recordings_by_ingest_state": dict(collections.Counter(r.get("ingest_state") for r in recordings.values())),
        "recordings_with_nas_manifest": sum(nas_ok.values()),
        "recordings_without_nas_manifest": sorted(s for s, ok in nas_ok.items() if not ok),
        "archive": {"count": len(archive_rows), "held": sum(r["held"] for r in archive_rows),
                    "nas_manifest": sum(r["nas_manifest"] for r in archive_rows), "rows": archive_rows},
        "backfill_log": {"count": len(backfill), "folder_matches": sum(1 for b in backfill if b.get("folder_matches")),
                         "results": dict(collections.Counter(b.get("result") for b in backfill))},
        "telemetry_folder": {"count": len(telemetry_rows), "held": sum(r["held"] for r in telemetry_rows),
                             "nas_manifest": sum(r["nas_manifest"] for r in telemetry_rows),
                             "missing": [r["name"] for r in telemetry_rows if not r["held"]]},
        "setups": {"count": len(setups), "named": sum(1 for s in setups if s.get("names")),
                   "raced": sum(1 for s in setups if s.get("raced")),
                   "by_source": dict(collections.Counter(src for s in setups for src in s.get("sources") or [])),
                   "unlinked_files": len(first.get("unlinked_files") or [])},
        "legacy_map": legacy_map,
        "legacy_resolved": sum(1 for m in legacy_map if m["session_id"]),
        "legacy_with_current_analysis": sum(1 for m in legacy_map if m["analysis_id"]),
        "deferred_or_failed_sessions": [
            {"session_id": s["session_id"], "state": s["analysis_state"], "reason": s.get("analysis_reason"),
             "track": s.get("track"), "type": s.get("session_type"), "start": s.get("start_time_utc"),
             "recordings": [(r["file_name"], r["size"]) for r in by_session[s["session_id"]].get("recordings") or []]}
            for s in sessions if s.get("analysis_state") in ("deferred", "failed", "none")
        ],
    }
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("legacy_map", "deferred_or_failed_sessions", "archive", "recordings_without_nas_manifest")}
    summary["archive"] = {k: v for k, v in report["archive"].items() if k != "rows"}
    summary["recordings_without_nas_manifest"] = len(report["recordings_without_nas_manifest"])
    summary["deferred_or_failed_sessions"] = len(report["deferred_or_failed_sessions"])
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
