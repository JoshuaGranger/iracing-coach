"""Tests for tools/Resolve-Toolchain.ps1.

Synthetic and offline. Nothing is downloaded, installed, restored, or requested
from a package feed, and no external service is contacted.

These follow the existing tools/dev pattern of driving a PowerShell entry point
from Python, so they are discovered by the same
``unittest discover -s tools\\dev\\tests`` invocation as the sandbox tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DEV = Path(__file__).resolve().parents[1]
WORKTREE = TOOLS_DEV.parents[1]
RESOLVER = WORKTREE / "tools" / "Resolve-Toolchain.ps1"
CI_WORKFLOW = WORKTREE / ".github" / "workflows" / "ci.yml"
GLOBAL_JSON_DIR = WORKTREE / "companion-app"
POWERSHELL = os.path.join(
    os.environ.get("SystemRoot", r"C:\WINDOWS"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
)
AGENT_CACHE_MARKER = "codex-runtimes"


def _run(script: str, environment: dict[str, str] | None = None):
    """Dot-source the resolver and run a snippet, returning the completed process."""

    command = [
        POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command", f". '{RESOLVER}'; {script}",
    ]
    return subprocess.run(
        command,
        env=environment if environment is not None else os.environ.copy(),
        cwd=str(WORKTREE),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _resolve_python(environment=None, extra=""):
    completed = _run(
        f"$r = Resolve-CoachPython {extra}; "
        "$p = Get-CoachToolchainProvenance -Python $r -Required @('python'); "
        "Write-CoachToolchainProvenance -Provenance $p",
        environment,
    )
    return completed


@unittest.skipUnless(os.name == "nt", "the resolver is Windows-only development tooling")
class ResolveToolchainTests(unittest.TestCase):
    # Case 1 - explicit compatible override is honoured and labelled.
    def test_explicit_python_override_is_selected(self) -> None:
        completed = _resolve_python(extra=f"-PythonPath '{sys.executable}'")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertEqual(record["rule"], "parameter")
        self.assertEqual(Path(record["path"]).resolve(), Path(sys.executable).resolve())
        self.assertIsNotNone(record["version"])

    # Case 2 / 2b - the agent-runtime cache is never an implicit candidate,
    # whether or not it is present. This is TOOLCHAIN-COUPLING-001's regression.
    def test_agent_cache_is_never_implicitly_selected(self) -> None:
        environment = os.environ.copy()
        environment.pop("IRACING_COACH_PYTHON", None)
        completed = _resolve_python(environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertNotIn(AGENT_CACHE_MARKER, record["path"].lower())
        self.assertIn(record["rule"], {"declared", "path"})

    def test_agent_cache_is_usable_when_named_explicitly(self) -> None:
        cache = Path(os.environ["USERPROFILE"]) / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
        if not cache.is_file():
            self.skipTest("agent runtime not present on this machine")
        completed = _resolve_python(extra=f"-PythonPath '{cache}'")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertEqual(record["rule"], "parameter")
        self.assertIn(AGENT_CACHE_MARKER, record["path"].lower())

    # Case 3 - wrong executable and missing candidate are rejected, and an
    # app-execution alias is NOT rejected merely for being zero length.
    def test_non_python_executable_is_rejected_without_falling_back(self) -> None:
        """An explicitly named interpreter that fails validation is a hard stop.

        Falling through to a different interpreter would silently run a tool the
        caller did not ask for.
        """
        completed = _resolve_python(extra=f"-PythonPath '{POWERSHELL}'")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not fall back", completed.stdout + completed.stderr)

    def test_missing_explicit_path_is_rejected_without_falling_back(self) -> None:
        missing = Path(tempfile.gettempdir()) / "no-such-python-xyz.exe"
        completed = _resolve_python(extra=f"-PythonPath '{missing}'")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not fall back", completed.stdout + completed.stderr)

    def test_bad_environment_override_is_rejected_without_falling_back(self) -> None:
        environment = os.environ.copy()
        environment["IRACING_COACH_PYTHON"] = str(Path(tempfile.gettempdir()) / "no-such-python-abc.exe")
        completed = _resolve_python(environment)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not fall back", completed.stdout + completed.stderr)

    def test_zero_length_execution_alias_is_not_rejected_for_size(self) -> None:
        """A 0-byte python.exe on Windows is normally a reparse-point alias that
        runs a real interpreter. Size is not evidence of breakage; execution is."""
        alias = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WindowsApps/python.exe"
        if not alias.is_file() or alias.stat().st_size != 0:
            self.skipTest("no zero-length execution alias on this machine")
        probe = subprocess.run([str(alias), "-c", "print(1)"], capture_output=True, text=True, timeout=120)
        if probe.returncode != 0:
            self.skipTest("the alias does not resolve to an installed interpreter here")
        completed = _resolve_python(extra=f"-PythonPath '{alias}'")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertIsNotNone(record["version"], "a working alias must not be rejected for its size")

    # Case 3b - declared compatibility is 3.10; do not silently narrow it.
    def test_minimum_is_three_ten_not_ci_selection(self) -> None:
        completed = _resolve_python()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertEqual(record["minimum"], "3.10")

    # Case 5 - environment override tier.
    def test_environment_override_tier(self) -> None:
        environment = os.environ.copy()
        environment["IRACING_COACH_PYTHON"] = sys.executable
        completed = _resolve_python(environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)["toolchain"]["python"]
        self.assertEqual(record["rule"], "environment")

    # Case 6 - .NET constrained by global.json, with executable hash.
    def test_dotnet_is_constrained_by_global_json(self) -> None:
        completed = _run(
            f"$d = Resolve-CoachDotnet -GlobalJsonDirectory '{GLOBAL_JSON_DIR}'; "
            "$p = Get-CoachToolchainProvenance -Dotnet $d -Required @('dotnet'); "
            "Write-CoachToolchainProvenance -Provenance $p"
        )
        if completed.returncode != 0:
            self.skipTest("no dotnet on this machine")
        record = json.loads(completed.stdout)["toolchain"]["dotnet"]
        self.assertIsNotNone(record["sha256"], "the resolved dotnet executable must be hashed")
        self.assertIsNotNone(record["sdkVersion"])
        self.assertEqual(record["globalJsonVersion"], "10.0.300")
        self.assertEqual(record["rollForward"], "latestPatch")
        self.assertTrue(record["satisfiesPin"])

    # Case 8 - a tool path containing spaces must resolve and hash correctly.
    # The default dotnet location is "C:\\Program Files\\dotnet", so this uses a
    # real spaced path rather than assuming the worktree has one.
    def test_tool_path_containing_spaces(self) -> None:
        completed = _run(
            f"$d = Resolve-CoachDotnet -GlobalJsonDirectory '{GLOBAL_JSON_DIR}'; "
            "$p = Get-CoachToolchainProvenance -Dotnet $d; Write-CoachToolchainProvenance -Provenance $p"
        )
        if completed.returncode != 0:
            self.skipTest("no dotnet on this machine")
        record = json.loads(completed.stdout)["toolchain"]["dotnet"]
        if " " not in record["path"]:
            self.skipTest("dotnet is not installed under a path containing a space here")
        self.assertIsNotNone(record["sha256"], "a spaced path must still hash")
        self.assertIsNotNone(record["sdkVersion"], "a spaced path must still execute")

    # Case 9 - CI must not drift from the declared toolchain contract.
    def test_ci_declares_the_toolchain_contract(self) -> None:
        text = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Resolve-Toolchain.ps1", text, "CI must validate provisioned tools through the shared resolver")
        self.assertNotIn(AGENT_CACHE_MARKER, text, "CI must never reference a private agent-runtime cache")

    # Case 10 - provenance is complete and leaks nothing unrelated.
    def test_provenance_fields_without_leakage(self) -> None:
        environment = os.environ.copy()
        environment["COACH_UNRELATED_SECRETish"] = "must-not-appear"
        completed = _resolve_python(environment, extra=f"-PythonPath '{sys.executable}'")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("must-not-appear", completed.stdout)
        toolchain = json.loads(completed.stdout)["toolchain"]
        self.assertEqual(
            set(toolchain.keys()),
            {"python", "node", "dotnet", "required", "optional", "authority", "rejected"},
        )

    # Case 11 - required versus optional is a property of the caller.
    def test_optional_absence_passes_and_required_absence_fails(self) -> None:
        optional = _run(
            "$n = Resolve-CoachNode -NodePath 'C:\\no-such-node.exe'; "
            "$p = Get-CoachToolchainProvenance -Node $n -Optional @('node'); "
            "Assert-CoachToolchain -Provenance $p -Required @(); "
            "Write-CoachToolchainProvenance -Provenance $p"
        )
        self.assertEqual(optional.returncode, 0, optional.stderr)
        toolchain = json.loads(optional.stdout)["toolchain"]
        self.assertIsNone(toolchain["node"], "an absent optional tool is recorded as null")
        self.assertTrue(any(item["tool"] == "node" for item in toolchain["rejected"]))

        required = _run(
            "$n = Resolve-CoachNode -NodePath 'C:\\no-such-node.exe'; "
            "$p = Get-CoachToolchainProvenance -Node $n -Required @('node'); "
            "Assert-CoachToolchain -Provenance $p -Required @('node')"
        )
        self.assertNotEqual(required.returncode, 0, "a required tool may never be null on a passing result")


if __name__ == "__main__":
    unittest.main()
