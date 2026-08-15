"""Runner-level tests for tools/dev/Invoke-SandboxedDev.ps1.

These cover sandbox lifetime, cleanup containment, interpreter validation, and
the worktree-cleanliness regression: an early revision of the runner redirected
USERPROFILE without creating home\\AppData\\Local, so Windows resolved a shell
folder to an empty string and a cache file was written relative to the working
directory, which is the worktree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DEV = Path(__file__).resolve().parents[1]
WORKTREE = TOOLS_DEV.parents[1]
RUNNER = TOOLS_DEV / "Invoke-SandboxedDev.ps1"
MARKER_TARGET = TOOLS_DEV / "_marker_target.py"
PYTHON = os.environ.get("IRACING_COACH_PYTHON") or sys.executable
POWERSHELL = os.path.join(
    os.environ.get("SystemRoot", r"C:\WINDOWS"),
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)
SANDBOX_GLOB = "iracing-coach-dev-*"


def _invoke(arguments: list[str], environment: dict[str, str] | None = None):
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RUNNER),
        "-PythonPath",
        PYTHON,
        *arguments,
    ]
    return subprocess.run(
        command,
        env=environment if environment is not None else os.environ.copy(),
        cwd=str(WORKTREE),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _sandboxes(parent: Path) -> list[Path]:
    return sorted(parent.glob(SANDBOX_GLOB))


@unittest.skipUnless(os.name == "nt", "the runner is Windows-only development tooling")
class SandboxedDevRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="g0-runner-test-")
        self.addCleanup(self._temporary.cleanup)
        self.parent = Path(self._temporary.name)
        self.sentinel = self.parent / "outside-sentinel.txt"
        self.sentinel.write_text("must survive cleanup\n", encoding="utf-8")
        self.marker = self.parent / "marker.txt"
        self.environment = os.environ.copy()
        self.environment["G0_DEV_MARKER"] = str(self.marker)

    # Case 6 - success removes only the runner's own sandbox.
    def test_success_removes_only_its_own_sandbox(self) -> None:
        completed = _invoke(
            ["-Script", str(MARKER_TARGET), "-SandboxParent", str(self.parent)],
            self.environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("sandbox       : removed", completed.stdout)
        self.assertEqual(_sandboxes(self.parent), [])
        self.assertTrue(self.parent.is_dir(), "the sandbox parent must survive")
        self.assertTrue(self.sentinel.is_file(), "an outside sentinel must survive cleanup")
        self.assertTrue(self.marker.is_file(), "the target should have run")

    # Case 6 - -KeepSandbox retains and reports the path.
    def test_keep_sandbox_retains(self) -> None:
        completed = _invoke(
            ["-Script", str(MARKER_TARGET), "-SandboxParent", str(self.parent), "-KeepSandbox"],
            self.environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        retained = _sandboxes(self.parent)
        self.assertEqual(len(retained), 1)
        self.assertIn(retained[0].name, completed.stdout)
        self.assertTrue(self.sentinel.is_file())

    # Case 6 - a failing target retains the sandbox for diagnosis.
    def test_failure_retains_sandbox(self) -> None:
        environment = os.environ.copy()
        environment.pop("G0_DEV_MARKER", None)
        completed = _invoke(
            ["-Script", str(MARKER_TARGET), "-SandboxParent", str(self.parent)],
            environment,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(len(_sandboxes(self.parent)), 1, "a failed run must retain its sandbox")
        self.assertIn("retained", completed.stdout)
        self.assertTrue(self.sentinel.is_file())

    # Case 5 - interpreter validation.
    def test_missing_interpreter_fails_clearly(self) -> None:
        command = [
            POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(RUNNER),
            "-PythonPath", str(self.parent / "no-such-python.exe"),
            "-Script", str(MARKER_TARGET),
            "-SandboxParent", str(self.parent),
        ]
        completed = subprocess.run(
            command, cwd=str(WORKTREE), capture_output=True, text=True, timeout=120
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Interpreter not found", completed.stdout + completed.stderr)
        self.assertEqual(_sandboxes(self.parent), [], "no sandbox before interpreter validation")

    def test_exactly_one_target_selector_is_required(self) -> None:
        completed = _invoke(["-SandboxParent", str(self.parent)], self.environment)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exactly one of -Target", completed.stdout + completed.stderr)

    # Case 7 and case 8 - a real gate runs, and the worktree gains nothing.
    def test_mcp_smoke_runs_and_leaves_the_worktree_unchanged(self) -> None:
        before = sorted(item.name for item in WORKTREE.iterdir())
        completed = _invoke(
            ["-Target", "mcp-smoke", "-FixtureIracingRoot", "-SandboxParent", str(self.parent)],
            self.environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"subsession_id": 8001', completed.stdout)
        after = sorted(item.name for item in WORKTREE.iterdir())
        self.assertEqual(
            before,
            after,
            "the run must not create anything in the worktree root",
        )
        self.assertEqual(_sandboxes(self.parent), [])


if __name__ == "__main__":
    unittest.main()
