from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "analyze-iracing-race"
    / "scripts"
)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import path_security  # noqa: E402
import storage  # noqa: E402
import tuning_workflow  # noqa: E402
import workflow  # noqa: E402


class LocalPathSecurityTests(unittest.TestCase):
    def test_rejects_unc_and_device_namespaces(self) -> None:
        for value in (
            r"\\server\share\iRacing",
            r"\\?\C:\Users\driver\Documents\iRacing",
            r"\\.\PhysicalDrive0",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "local path"):
                path_security.local_path(value, "test path")

    def test_environment_roots_reject_unc_paths(self) -> None:
        with mock.patch.dict(os.environ, {"IRACING_COACH_DATA": r"\\server\archive"}):
            with self.assertRaisesRegex(ValueError, "IRACING_COACH_DATA"):
                storage.default_archive_root()
        with mock.patch.dict(
            os.environ, {"IRACING_COACH_IRACING_ROOT": r"\\server\iRacing"}
        ):
            with self.assertRaisesRegex(ValueError, "IRACING_COACH_IRACING_ROOT"):
                workflow._default_iracing_root()
            with self.assertRaisesRegex(ValueError, "IRACING_COACH_IRACING_ROOT"):
                tuning_workflow._default_iracing_root()

    def test_local_existing_root_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory).resolve()
            self.assertEqual(path_security.local_path(directory, "root", strict=True), expected)
            self.assertEqual(workflow._resolved_root(directory), expected)


if __name__ == "__main__":
    unittest.main()
