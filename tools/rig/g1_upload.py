"""Upload one recording to coach-server as the collector does (collector-protocol.md section 4).

Standard library only. Hashes the file, creates or resumes the upload with ``POST
/api/v1/uploads``, sends 4 MiB chunks with ``PUT /api/v1/uploads/{sha256}`` from the server's
durable offset, follows ``409 {offset}`` and ``503 Retry-After``, and stops at ``verified`` (or
any later state the server already holds). The file is opened read-only. Prints one JSON object.

    python g1_upload.py --server http://192.168.1.81:8210 --file RACE.ibt
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import http.client
import json
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

CHUNK = 4 * 1024 * 1024
# A fixed collector identity for rig tooling, so its occurrences are recognisable.
TOOL_COLLECTOR_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "iracing-coach/tools/rig/g1_upload"))
DONE = {"verified", "parsed", "discovered", "archived", "analysis_queued", "analyzed", "deferred"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


class Server:
    def __init__(self, base: str) -> None:
        parts = urllib.parse.urlsplit(base)
        self.host, self.port = parts.hostname or "127.0.0.1", parts.port or 80

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=120)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()


def upload(server: Server, path: Path, sha: str, size: int, customer_id: int | None) -> dict:
    mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc).replace(microsecond=0)
    create = {
        "sha256": sha,
        "size": size,
        "kind": "ibt",
        "file_name": path.name,
        "relative_path": None,
        "source_mtime": mtime.isoformat().replace("+00:00", "Z"),
        "collector_id": TOOL_COLLECTOR_ID,
        "customer_id": customer_id,
    }
    counts = {"chunks": 0, "conflicts": 0, "retries": 0}
    while True:
        status, headers, body = server.request(
            "POST", "/api/v1/uploads", json.dumps(create).encode(), {"Content-Type": "application/json"}
        )
        if status == 503:
            counts["retries"] += 1
            time.sleep(int(headers.get("retry-after", "5")))
            continue
        if status not in (200, 201):
            raise SystemExit(f"create: HTTP {status} {body[:400]!r}")
        state = json.loads(body)
        break
    create_status = status
    offset = int(state["offset"])
    with path.open("rb") as handle:
        while state["state"] == "open":
            handle.seek(offset)
            data = handle.read(min(CHUNK, size - offset))
            end = offset + len(data) - 1
            status, headers, body = server.request(
                "PUT",
                f"/api/v1/uploads/{sha}",
                data,
                {"Content-Type": "application/octet-stream", "Content-Range": f"bytes {offset}-{end}/{size}"},
            )
            if status == 200:
                counts["chunks"] += 1
                state = json.loads(body)
                offset = int(state["offset"])
            elif status == 409:
                counts["conflicts"] += 1
                offset = int(json.loads(body)["offset"])
            elif status == 503:
                counts["retries"] += 1
                time.sleep(int(headers.get("retry-after", "5")))
            elif status == 422:
                raise SystemExit(f"hash mismatch reported by the server: {body[:400]!r}")
            else:
                raise SystemExit(f"chunk at {offset}: HTTP {status} {body[:400]!r}")
    if state["state"] not in DONE:
        raise SystemExit(f"upload ended in state {state['state']!r}")
    return {"create_status": create_status, "state": state["state"], "source_mtime": create["source_mtime"], **counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--customer-id", type=int)
    args = parser.parse_args(argv)
    path = Path(args.file)
    size = path.stat().st_size
    started = time.perf_counter()
    sha = sha256_file(path)
    hashed = time.perf_counter()
    result = upload(Server(args.server), path, sha, size, args.customer_id)
    finished = time.perf_counter()
    print(json.dumps({
        "file_name": path.name,
        "sha256": sha,
        "size": size,
        "hash_s": round(hashed - started, 3),
        "upload_s": round(finished - hashed, 3),
        "upload_mb_s": round(size / 1e6 / max(finished - hashed, 1e-9), 2),
        **result,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
