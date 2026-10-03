"""Moved to ``iracing-coach/skills/analyze-iracing-race/scripts/native_codec.py``.

The vectors and the ``kernels`` hand-off share one codec
(``docs/08-rewrite/port/phase2-plan.md`` section 4.3), so it lives next to
``native_stages.py``, outside the analyzer bundle. This module re-exports it
under its old name; add codec rules in ``native_codec.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[3] / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import native_codec as _codec  # noqa: E402

globals().update({name: value for name, value in vars(_codec).items() if not name.startswith("__")})
__all__ = list(_codec.__all__)
