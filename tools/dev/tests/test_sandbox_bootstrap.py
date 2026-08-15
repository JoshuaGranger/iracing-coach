"""Assertion-level tests for tools/dev/sandbox_bootstrap.py.

Every case is synthetic and offline. The negative cases assert that the marker
target was never entered, because an exit code alone cannot distinguish a
refusal from a target that ran and failed.
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
BOOTSTRAP = TOOLS_DEV / "sandbox_bootstrap.py"
MARKER_TARGET = TOOLS_DEV / "_marker_target.py"
FIXTURE_ROOT = WORKTREE / "test-data" / "ibt"
PYTHON = os.environ.get("IRACING_COACH_PYTHON") or sys.executable

EXIT_ASSERTION_FAILED = 3

SANDBOX_CHILDREN = (
    "archive",
    "home",
    "temp",
    "install",
    "iracing",
    os.path.join("home", "AppData", "Local"),
    os.path.join("home", "AppData", "Roaming"),
)


def _prepare(sandbox: Path) -> None:
    for child in SANDBOX_CHILDREN:
        (sandbox / child).mkdir(parents=True, exist_ok=True)


def _base_environment(sandbox: Path, marker: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("HOME", None)
    environment.update(
        {
            "IRACING_COACH_DATA": str(sandbox / "archive"),
            "IRACING_COACH_INSTALL_ROOT": str(sandbox / "install"),
            "IRACING_COACH_IRACING_ROOT": str(sandbox / "iracing"),
            "USERPROFILE": str(sandbox / "home"),
            "LOCALAPPDATA": str(sandbox / "home" / "AppData" / "Local"),
            "APPDATA": str(sandbox / "home" / "AppData" / "Roaming"),
            "TEMP": str(sandbox / "temp"),
            "TMP": str(sandbox / "temp"),
            "PYTHONUTF8": "1",
            "G0_DEV_MARKER": str(marker),
        }
    )
    return environment


def _run(environment: dict[str, str], sandbox: Path, fixture_root: Path | None = None):
    command = [PYTHON, "-X", "utf8", str(BOOTSTRAP), "--expect-sandbox", str(sandbox)]
    if fixture_root is not None:
        command += ["--allow-fixture-root", str(fixture_root)]
    command += ["--script", str(MARKER_TARGET)]
    return subprocess.run(
        command,
        env=environment,
        cwd=str(WORKTREE),
        capture_output=True,
        text=True,
        timeout=180,
    )


class SandboxBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="g0-bootstrap-test-")
        self.addCleanup(self._temporary.cleanup)
        self.sandbox = Path(self._temporary.name) / "sandbox"
        self.marker = Path(self._temporary.name) / "marker.txt"
        _prepare(self.sandbox)
        self.environment = _base_environment(self.sandbox, self.marker)

    def assertRefused(self, completed, expected_class: str) -> None:
        self.assertEqual(completed.returncode, EXIT_ASSERTION_FAILED, completed.stderr)
        self.assertFalse(self.marker.exists(), "target ran despite a refused environment")
        self.assertIn("G0 SANDBOX ASSERTION FAILED", completed.stderr)
        self.assertIn(expected_class, completed.stderr)

    # Case 1 - a correct environment passes and launches the target.
    def test_valid_environment_dispatches_target(self) -> None:
        completed = _run(self.environment, self.sandbox)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("G0 sandbox assertion passed", completed.stdout)
        self.assertIn("marker target entered", completed.stdout)
        self.assertTrue(self.marker.exists())
        self.assertLess(
            completed.stdout.index("G0 sandbox assertion passed"),
            completed.stdout.index("marker target entered"),
            "the assertion must be reported before the target runs",
        )

    # Case 1 - the tracked fixture root is the one permitted outside-sandbox root.
    def test_tracked_fixture_root_is_accepted(self) -> None:
        self.environment["IRACING_COACH_IRACING_ROOT"] = str(FIXTURE_ROOT)
        completed = _run(self.environment, self.sandbox, fixture_root=FIXTURE_ROOT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(self.marker.exists())

    # Case 2 - missing or outside archive root.
    def test_missing_data_root_fails_closed(self) -> None:
        del self.environment["IRACING_COACH_DATA"]
        self.assertRefused(_run(self.environment, self.sandbox), "missing")

    def test_outside_data_root_fails_closed(self) -> None:
        outside = Path(self._temporary.name) / "outside-archive"
        outside.mkdir()
        self.environment["IRACING_COACH_DATA"] = str(outside)
        self.assertRefused(_run(self.environment, self.sandbox), "outside-sandbox")

    # Case 3 - ambient home or temp.
    def test_ambient_userprofile_fails(self) -> None:
        self.environment["USERPROFILE"] = str(Path(self._temporary.name))
        self.assertRefused(_run(self.environment, self.sandbox), "outside-sandbox")

    def test_ambient_temp_fails(self) -> None:
        self.environment["TEMP"] = str(Path(self._temporary.name))
        self.assertRefused(_run(self.environment, self.sandbox), "outside-sandbox")

    def test_ambient_tmp_fails(self) -> None:
        self.environment["TMP"] = str(Path(self._temporary.name))
        self.assertRefused(_run(self.environment, self.sandbox), "outside-sandbox")

    # Case 4 - unexpected fixture root, traversal, UNC, drive mismatch.
    def test_unexpected_fixture_root_fails(self) -> None:
        stray = Path(self._temporary.name) / "not-the-fixture-root"
        stray.mkdir()
        self.environment["IRACING_COACH_IRACING_ROOT"] = str(stray)
        self.assertRefused(_run(self.environment, self.sandbox, fixture_root=FIXTURE_ROOT), "unexpected-fixture-root")

    def test_traversal_escape_fails(self) -> None:
        self.environment["IRACING_COACH_DATA"] = str(self.sandbox / "archive" / ".." / ".." / "..")
        self.assertRefused(_run(self.environment, self.sandbox), "outside-sandbox")

    def test_unc_root_is_rejected(self) -> None:
        self.environment["IRACING_COACH_DATA"] = r"\\example-host\share\archive"
        self.assertRefused(_run(self.environment, self.sandbox), "unc-or-device")

    def test_device_namespace_root_is_rejected(self) -> None:
        self.environment["IRACING_COACH_DATA"] = r"\\?\C:\archive"
        self.assertRefused(_run(self.environment, self.sandbox), "unc-or-device")

    def test_incompatible_drive_is_rejected(self) -> None:
        sandbox_drive = os.path.splitdrive(str(self.sandbox))[0].upper()
        other = next(
            (
                f"{letter}:"
                for letter in "DEFGHIJKLMNOPQRSTUVWXYZ"
                if f"{letter}:" != sandbox_drive and os.path.exists(f"{letter}:\\")
            ),
            None,
        )
        if other is None:
            # No second volume is mounted. Per the G0d-py assignment this becomes
            # a deterministic canonicalisation check rather than a weakened rule
            # or a request for extra privilege.
            crafted = os.path.realpath(str(self.sandbox / "archive" / ".." / ".." / "elsewhere"))
            self.assertFalse(
                crafted.lower().startswith(str(self.sandbox).lower() + os.sep),
                "crafted path must canonicalise outside the sandbox",
            )
            self.skipTest("no second volume mounted; verified by canonicalisation instead")
        self.environment["IRACING_COACH_DATA"] = other + "\\archive"
        completed = _run(self.environment, self.sandbox)
        self.assertEqual(completed.returncode, EXIT_ASSERTION_FAILED, completed.stderr)
        self.assertFalse(self.marker.exists())

    # Clarification 9 - failure output must never echo an ambient private root.
    def test_failure_message_omits_the_path_value(self) -> None:
        outside = Path(self._temporary.name) / "private-looking-root"
        outside.mkdir()
        self.environment["IRACING_COACH_DATA"] = str(outside)
        completed = _run(self.environment, self.sandbox)
        self.assertEqual(completed.returncode, EXIT_ASSERTION_FAILED)
        self.assertNotIn(str(outside), completed.stderr)
        self.assertNotIn(str(outside), completed.stdout)
        self.assertIn("IRACING_COACH_DATA", completed.stderr)


if __name__ == "__main__":
    unittest.main()
