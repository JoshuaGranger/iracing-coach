"""Local-filesystem path validation shared by CLI, MCP, and workflows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def local_path(value: Any, label: str, *, strict: bool = False) -> Path:
    """Resolve a non-empty local path while rejecting UNC and device namespaces."""

    raw = os.fspath(value).strip()
    if not raw:
        raise ValueError(f"{label} must be a non-empty local path.")
    normalized = raw.replace("/", "\\")
    if (
        normalized.startswith("\\\\")
        or normalized.startswith("\\?\\")
        or normalized.startswith("\\.\\")
    ):
        raise ValueError(f"{label} must be a local path; UNC and device paths are not allowed.")
    return Path(raw).expanduser().resolve(strict=strict)


def path_is_within(candidate: Path, base: Path) -> bool:
    """Return whether candidate is within base, including Windows path aliases."""

    resolved_candidate = candidate.resolve()
    resolved_base = base.resolve()
    try:
        resolved_candidate.relative_to(resolved_base)
        return True
    except ValueError:
        pass

    # Windows runners can expose one directory through both its long name and
    # an 8.3 alias (for example runneradmin and RUNNER~1). Compare filesystem
    # identities so a valid local alias is not mistaken for a boundary escape.
    if not resolved_base.exists():
        return False
    for ancestor in (resolved_candidate, *resolved_candidate.parents):
        if not ancestor.exists():
            continue
        try:
            if os.path.samefile(ancestor, resolved_base):
                return True
        except OSError:
            continue
    return False
