"""The desktop app (`companion-app/`) was deleted in rewrite wave 4; nothing may still need it.

Its source is in Git history (last present at 3ff5e87). These checks keep the gates, CI and the
evidence registry from drifting back onto the deleted tree.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "tools" / "evidence" / "evidence-declarations.json"
REPORT_SCRIPT = ROOT / "tools" / "New-EvidenceReport.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

#: Files that run or define the repository gates.
GATE_FILES = (
    ".github/workflows/ci.yml",
    "tools/check.ps1",
    "tools/verify-repository.ps1",
    "tools/verify_repository.py",
    "tools/wsl/check.sh",
)
#: Tracked paths that existed only for the desktop app.
DELETED_PATHS = (
    "companion-app",
    "crates/coach-live/tools/csharp-oracle",
    "tools/Invoke-JavaScriptSyntaxGate.ps1",
    "tools/dev/tests/test_javascript_syntax_gate.py",
)


class DesktopAppRetiredTests(unittest.TestCase):
    def test_no_tracked_file_remains_under_the_deleted_paths(self) -> None:
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            self.skipTest("not a Git checkout")
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--", *DELETED_PATHS],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(completed.stdout.strip(), "", "tracked files remain under a deleted path")

    def test_gates_and_ci_name_neither_the_app_nor_its_javascript_gate(self) -> None:
        for relative in GATE_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                self.assertNotIn("companion-app", text)
                self.assertNotIn("Invoke-JavaScriptSyntaxGate", text)
                self.assertNotIn("iRacingCoach.sln", text)
                self.assertNotRegex(text, r"-Family\s+'?javascript")

    def test_registry_has_no_dotnet_family_and_every_fixture_source_exists(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
        families = {declaration["family"] for declaration in registry["declarations"]}
        self.assertNotIn("dotnet", families, "the dotnet tests were deleted with the app")
        for declaration in registry["declarations"]:
            self.assertFalse(declaration["id"].startswith("test_javascript_syntax_gate."), declaration["id"])
            for source in declaration.get("fixtureSources", []):
                relative = source["value"] if source["kind"] == "path" else source["sourcePath"]
                with self.subTest(declaration=declaration["id"], source=relative):
                    self.assertFalse(relative.startswith("companion-app/"))
                    self.assertTrue((ROOT / relative).is_file(), f"{relative} is not in the tree")

    def test_evidence_report_refuses_the_retired_javascript_family(self) -> None:
        if not POWERSHELL.exists():
            self.skipTest("Windows PowerShell is not available")
        with tempfile.TemporaryDirectory(prefix="retired-js-family-") as raw:
            output = Path(raw) / "normalized.json"
            completed = subprocess.run(
                [
                    str(POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(REPORT_SCRIPT),
                    "-RepositoryRoot", str(ROOT),
                    "-SourceSha", "0" * 40,
                    "-Family", "javascript",
                    "-InputPath", str(Path(raw) / "raw.json"),
                    "-Authority", "local-diagnostic",
                    "-ToolchainProvenance", "{}",
                    "-OutputPath", str(output),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("javascript", completed.stderr + completed.stdout)
            self.assertFalse(output.exists(), "a refused family must not write a report")


if __name__ == "__main__":
    unittest.main()
