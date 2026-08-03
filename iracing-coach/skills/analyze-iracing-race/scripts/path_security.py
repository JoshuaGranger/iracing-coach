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
