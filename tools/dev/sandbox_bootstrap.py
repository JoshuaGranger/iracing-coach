#!/usr/bin/env python3
"""Assert development environment confinement, then dispatch a target.

This is development tooling. It is never imported by production code and it does
not change backend path resolution. Read ``tools/dev/README.md`` for the exact
guarantee and, more importantly, for what it does not guarantee.

The ordering is the point: every confinement check completes before the target
module or script is imported or executed. When a target is dispatched through
``--script`` or ``--module`` the dispatch happens in this same process, so the
target cannot run unless the assertion passed.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
import tempfile
from pathlib import Path

EXIT_ASSERTION_FAILED = 3

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SCRIPTS = WORKTREE_ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"

REQUIRED_VARIABLES = (
    "IRACING_COACH_DATA",
    "IRACING_COACH_INSTALL_ROOT",
    "IRACING_COACH_IRACING_ROOT",
    "USERPROFILE",
    "TEMP",
    "TMP",
)


class ConfinementFailure(Exception):
    """A confinement check failed.

    Carries only the offending name and a failure class. The resolved value is
    deliberately never included: evidence files must not echo ambient private
    roots. Use the runner's -KeepSandbox switch to diagnose locally instead.
    """

    def __init__(self, name: str, failure_class: str) -> None:
        super().__init__(f"{name} ({failure_class})")
        self.name = name
        self.failure_class = failure_class


def _reject_network_or_device(raw: str, name: str) -> str:
    normalized = raw.replace("/", "\\")
    if (
        normalized.startswith("\\\\")
        or normalized.startswith("\\?\\")
        or normalized.startswith("\\.\\")
    ):
        raise ConfinementFailure(name, "unc-or-device")
    return raw


def _canonical(raw: object, name: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ConfinementFailure(name, "missing")
    _reject_network_or_device(text, name)
    return os.path.realpath(text)


def _require_within(candidate: str, base: str, name: str) -> None:
    try:
        common = os.path.commonpath([candidate, base])
    except ValueError as exc:  # different drives, or mixed path flavours
        raise ConfinementFailure(name, "drive-mismatch") from exc
    if os.path.normcase(common) != os.path.normcase(base):
        raise ConfinementFailure(name, "outside-sandbox")


def assert_confined(sandbox: str, fixture_root: str | None) -> None:
    """Raise ConfinementFailure unless every development root is confined.

    The single permitted exception is the iRacing root, which may equal the
    exact tracked ``test-data/ibt`` fixture directory supplied by the caller.
    """

    sandbox_real = _canonical(sandbox, "--expect-sandbox")
    fixture_real = _canonical(fixture_root, "--allow-fixture-root") if fixture_root else None

    for name in REQUIRED_VARIABLES:
        if not str(os.environ.get(name) or "").strip():
            raise ConfinementFailure(name, "missing")

    for name in ("IRACING_COACH_DATA", "IRACING_COACH_INSTALL_ROOT", "USERPROFILE", "TEMP", "TMP"):
        _require_within(_canonical(os.environ[name], name), sandbox_real, name)

    # The backend modules are imported here on purpose: importing them is what
    # materialises the module level defaults that have to be checked.
    if str(BACKEND_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(BACKEND_SCRIPTS))
    import mcp_server  # noqa: E402
    import storage  # noqa: E402

    _require_within(
        _canonical(storage.default_archive_root(), "storage.default_archive_root()"),
        sandbox_real,
        "storage.default_archive_root()",
    )
    _require_within(
        _canonical(mcp_server.DEFAULT_ARCHIVE_ROOT, "mcp_server.DEFAULT_ARCHIVE_ROOT"),
        sandbox_real,
        "mcp_server.DEFAULT_ARCHIVE_ROOT",
    )
    _require_within(_canonical(Path.home(), "Path.home()"), sandbox_real, "Path.home()")
    _require_within(
        _canonical(tempfile.gettempdir(), "tempfile.gettempdir()"),
        sandbox_real,
        "tempfile.gettempdir()",
    )

    iracing_real = _canonical(mcp_server.DEFAULT_IRACING_ROOT, "mcp_server.DEFAULT_IRACING_ROOT")
    if fixture_real is not None and os.path.normcase(iracing_real) == os.path.normcase(fixture_real):
        return
    try:
        _require_within(iracing_real, sandbox_real, "mcp_server.DEFAULT_IRACING_ROOT")
    except ConfinementFailure as failure:
        if failure.failure_class == "outside-sandbox":
            raise ConfinementFailure(
                "mcp_server.DEFAULT_IRACING_ROOT", "unexpected-fixture-root"
            ) from failure
        raise


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-sandbox", required=True)
    parser.add_argument("--allow-fixture-root", default=None)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--assert-only", action="store_true")
    group.add_argument("--script")
    group.add_argument("--module")
    parser.add_argument("target_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(list(sys.argv[1:] if argv is None else argv))

    try:
        assert_confined(args.expect_sandbox, args.allow_fixture_root)
    except ConfinementFailure as failure:
        print(
            f"G0 SANDBOX ASSERTION FAILED: {failure.name} ({failure.failure_class})",
            file=sys.stderr,
        )
        return EXIT_ASSERTION_FAILED

    print("G0 sandbox assertion passed", flush=True)
    if args.assert_only:
        return 0

    forwarded = [item for item in args.target_args if item != "--"]
    if args.script:
        # The runner checks this too. It is repeated here so the contract holds
        # even when the bootstrap is invoked directly, which the tests do.
        script = os.path.realpath(args.script)
        worktree = os.path.realpath(str(WORKTREE_ROOT))
        try:
            inside = os.path.normcase(os.path.commonpath([script, worktree])) == os.path.normcase(worktree)
        except ValueError:
            inside = False  # different drives
        if not os.path.isfile(script) or not inside:
            print(
                "G0 SANDBOX ASSERTION FAILED: --script (outside-worktree)",
                file=sys.stderr,
            )
            return EXIT_ASSERTION_FAILED
        sys.argv = [script, *forwarded]
        runpy.run_path(script, run_name="__main__")
        return 0

    sys.argv = [args.module, *forwarded]
    runpy.run_module(args.module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    # A dispatched target that raises SystemExit propagates out of main() with
    # its own code, which is the behaviour we want.
    raise SystemExit(main())
