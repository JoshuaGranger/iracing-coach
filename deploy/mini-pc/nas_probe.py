"""NAS write probe, run inside the released container (risk R2, nas-layout.md section 6).

    docker exec <container> python3.12 /app/deploy/nas_probe.py [--nas /nas/apps/iracing-coach]

Proves, from inside the container and as its user, that the NFS volume is mounted
with the contract's options, readable, and writable with the archiver's protocol:
temp name under `.probe/` with O_CREAT|O_EXCL, write, fsync, drop the client cache,
read back, SHA-256 equal. Creates `$NAS` and `$NAS/.probe/` when absent (the server
does the same at first start) and deletes only the probe file it wrote. Prints one
JSON object; the exit status is 0 only for `state: ok`.

States follow `NasState`: ok, readonly (mounted and readable, writes refused),
unreachable (not mounted, or a call failed), stalled (a call made no progress
within the time limit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

MOUNT_POINT = "/nas/apps"
DEFAULT_NAS = "/nas/apps/iracing-coach"
REQUIRED_OPTIONS = ("vers=4.1", "soft", "timeo=150", "retrans=3", "noatime")
PROBE_BYTES = 4 * 1024 * 1024
STALL_SECONDS = 120.0


def mount_entry(mount_point: str) -> dict[str, Any] | None:
    with open("/proc/self/mounts", encoding="utf-8") as mounts:
        for line in mounts:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == mount_point:
                return {"source": fields[0], "type": fields[2], "options": fields[3].split(",")}
    return None


def stat_record(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {"path": str(path), "uid": info.st_uid, "gid": info.st_gid, "mode": oct(info.st_mode & 0o7777)}


def write_probe(probe_dir: Path) -> dict[str, Any]:
    payload = secrets.token_bytes(PROBE_BYTES)
    expected = hashlib.sha256(payload).hexdigest()
    name = probe_dir / f"probe.{secrets.token_hex(8)}.tmp"
    began = time.monotonic()
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    wrote = time.monotonic() - began
    fd = os.open(name, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(fd)
    actual = digest.hexdigest()
    owner = stat_record(name)
    name.unlink()
    return {
        "bytes": PROBE_BYTES,
        "sha256_equal": actual == expected,
        "write_fsync_seconds": round(wrote, 3),
        "file_owner": owner,
    }


def probe(nas: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"nas": str(nas), "uid": os.getuid(), "gid": os.getgid()}
    entry = mount_entry(MOUNT_POINT)
    report["mount"] = entry
    if entry is None or not entry["type"].startswith("nfs"):
        report["state"] = "unreachable"
        report["reason"] = f"{MOUNT_POINT} is not an NFS mount"
        return report
    report["missing_options"] = [item for item in REQUIRED_OPTIONS if item not in entry["options"]]
    try:
        report["share_entries"] = len(os.listdir(MOUNT_POINT))
        report["share_root"] = stat_record(Path(MOUNT_POINT))
    except OSError as error:
        report["state"] = "unreachable"
        report["reason"] = f"listing {MOUNT_POINT}: {error}"
        return report
    report["nas_existed"] = nas.is_dir()
    try:
        (nas / ".probe").mkdir(parents=True, exist_ok=True)
        report["nas_dir"] = stat_record(nas)
        report["write"] = write_probe(nas / ".probe")
    except PermissionError as error:
        report["state"] = "readonly"
        report["reason"] = str(error)
        return report
    except OSError as error:
        report["state"] = "readonly" if error.errno == 30 else "unreachable"
        report["reason"] = str(error)
        return report
    report["listing"] = sorted(os.listdir(nas))
    report["state"] = "ok" if report["write"]["sha256_equal"] else "unreachable"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nas", default=DEFAULT_NAS)
    args = parser.parse_args()
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result.update(probe(Path(args.nas)))
        except Exception as error:  # noqa: BLE001 - reported, not raised
            result.update({"nas": args.nas, "state": "unreachable", "reason": repr(error)})

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(STALL_SECONDS)
    if worker.is_alive():
        result.update({"nas": args.nas, "state": "stalled", "reason": f"no result in {STALL_SECONDS:.0f} s"})
    result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("state") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
