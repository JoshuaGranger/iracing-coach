"""Runner-level tests for tools/dev/Invoke-SandboxedDev.ps1.

These cover sandbox lifetime, cleanup containment, interpreter validation, and
the worktree-cleanliness regression: an early revision of the runner redirected
USERPROFILE without creating home\\AppData\\Local, so Windows resolved a shell
folder to an empty string and a cache file was written relative to the working
directory, which is the worktree.
"""

from __future__ import annotations

import os
import shutil
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


def _worktree_status() -> str:
    """Recursive worktree state, for before/after comparison.

    Git status is preferred because it sees modifications as well as additions,
    at any depth. The snapshot may legitimately be non-empty; only the
    difference between two snapshots matters.
    """

    git = shutil.which("git")
    if git:
        completed = subprocess.run(
            [git, "-C", str(WORKTREE), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode == 0:
            return completed.stdout
    # Fallback when Git is unavailable: recursive path/size/mtime listing.
    entries = []
    for root, directories, files in os.walk(WORKTREE):
        if ".git" in directories:
            directories.remove(".git")
        for name in files:
            path = Path(root) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(f"{path.relative_to(WORKTREE)}|{stat.st_size}|{int(stat.st_mtime)}")
    return "\n".join(sorted(entries))


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

    # Review finding 1 - a sandbox parent inside the worktree must be refused
    # before any sandbox is created or any target is entered.
    def test_sandbox_parent_inside_the_worktree_is_refused(self) -> None:
        inside = WORKTREE / "g0-dev-adversarial-parent"
        inside.mkdir(exist_ok=True)
        try:
            before = _worktree_status()
            completed = _invoke(
                ["-Script", str(MARKER_TARGET), "-SandboxParent", str(inside)],
                self.environment,
            )
            self.assertNotEqual(completed.returncode, 0)
            # Either sandbox-parent guard is a correct refusal. On this layout
            # the temporary-root guard fires first because the worktree is not
            # under %TEMP%; the disjointness guard covers the case where it is.
            self.assertIn("Sandbox parent", completed.stdout + completed.stderr)
            self.assertEqual(_sandboxes(inside), [], "no sandbox may be created inside the worktree")
            self.assertFalse(self.marker.is_file(), "the target must not run")
            self.assertEqual(before, _worktree_status(), "worktree state must be unchanged")
        finally:
            inside.rmdir()

    # Review finding 1 - a parent outside the OS temporary root is refused.
    def test_sandbox_parent_outside_the_temp_root_is_refused(self) -> None:
        completed = _invoke(
            ["-Script", str(MARKER_TARGET), "-SandboxParent", str(WORKTREE.parent)],
            self.environment,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("temporary root", completed.stdout + completed.stderr)
        self.assertFalse(self.marker.is_file())

    # Review finding 2 - the runner refuses an out-of-worktree script.
    def test_script_outside_the_worktree_is_refused(self) -> None:
        outside = self.parent / "outside_target.py"
        outside.write_text("raise SystemExit(0)\n", encoding="utf-8")
        completed = _invoke(
            ["-Script", str(outside), "-SandboxParent", str(self.parent)],
            self.environment,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("inside the current worktree", completed.stdout + completed.stderr)
        self.assertEqual(_sandboxes(self.parent), [], "refusal must precede sandbox creation")

    # Review finding 4a - an interpreter that is not Python must be refused.
    def test_interpreter_mismatch_is_refused(self) -> None:
        command = [
            POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(RUNNER),
            "-PythonPath", POWERSHELL,
            "-Script", str(MARKER_TARGET),
            "-SandboxParent", str(self.parent),
        ]
        completed = subprocess.run(
            command, cwd=str(WORKTREE), capture_output=True, text=True, timeout=180
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Interpreter did not execute", completed.stdout + completed.stderr)
        self.assertEqual(_sandboxes(self.parent), [], "no sandbox before interpreter validation")
        self.assertFalse(self.marker.is_file())

    # Case 7 and case 8 - a real gate runs, and the worktree is untouched.
    # Review finding 4b - compare recursive Git status, not top-level names: the
    # old assertion could not see a modified file or a file nested below an
    # existing directory.
    def test_mcp_smoke_runs_and_leaves_the_worktree_unchanged(self) -> None:
        before = _worktree_status()
        completed = _invoke(
            ["-Target", "mcp-smoke", "-FixtureIracingRoot", "-SandboxParent", str(self.parent)],
            self.environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"subsession_id": 8001', completed.stdout)
        self.assertEqual(before, _worktree_status(), "the run must not change the worktree")
        self.assertEqual(_sandboxes(self.parent), [])


if __name__ == "__main__":
    unittest.main()
