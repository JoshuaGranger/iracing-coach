"""Deterministic tests for the first-party JavaScript syntax-only gate."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "tools" / "Invoke-JavaScriptSyntaxGate.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class JavaScriptSyntaxGateTests(unittest.TestCase):
    def setUp(self) -> None:
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required")
        self._temporary = tempfile.TemporaryDirectory(prefix="ws13b-js-")
        self.synthetic = Path(self._temporary.name).resolve()
        self.node_log = self.synthetic / "fake-node-invocations.txt"
        self.node = self.synthetic / "fake-node.cmd"
        self.node.write_text(
            "@echo off\r\n"
            f"echo %*>>\"{self.node_log}\"\r\n"
            'if "%~1"=="--version" goto version\r\n'
            'if "%~1"=="--check" goto check\r\n'
            "exit /b 3\r\n"
            ":version\r\n"
            "echo v99.1.0\r\n"
            "exit /b 0\r\n"
            ":check\r\n"
            'findstr /C:"SYNTAX_ERROR" "%~2" >nul\r\n'
            "if errorlevel 1 exit /b 0\r\n"
            "exit /b 1\r\n",
            encoding="ascii",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _candidate(self, relative: str, text: str = "export const ok = true;\n") -> Path:
        path = self.synthetic / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def _run_synthetic(
        self,
        candidates: list[Path],
        *,
        node: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None, Path]:
        output = self.synthetic / f"result-{uuid.uuid4().hex}.json"
        candidate_expression = "@(" + ",".join(_quote(path) for path in candidates) + ")"
        command = (
            f"& {_quote(GATE)} -RepositoryRoot {_quote(ROOT)} "
            f"-CandidatePath {candidate_expression} -SyntheticRoot {_quote(self.synthetic)} "
            f"-NodePath {_quote(node or self.node)} -Authority local-diagnostic "
            f"-OutputPath {_quote(output)}"
        )
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        record = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else None
        return completed, record, output

    def _init_tracked_repository(self) -> tuple[Path, Path, str]:
        repository = self.synthetic / "tracked"
        repository.mkdir()
        relative = Path(
            "companion-app/src/iRacingCoach.UI/wwwroot/nested/tracked-module.js"
        )
        script = repository / relative
        script.parent.mkdir(parents=True)
        script.write_text("export const tracked = true;\n", encoding="utf-8", newline="\n")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "WS13B Test"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "ws13b@example.invalid"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repository), "add", "--", str(relative)], check=True)
        subprocess.run(
            ["git", "-c", "gc.auto=0", "-C", str(repository), "commit", "-q", "-m", "fixture"],
            check=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        return repository, script, sha

    def _run_tracked(
        self, repository: Path, sha: str
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
        output = self.synthetic / f"tracked-{uuid.uuid4().hex}.json"
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(GATE),
                "-RepositoryRoot",
                str(repository),
                "-SourceSha",
                sha,
                "-NodePath",
                str(self.node),
                "-Authority",
                "local-diagnostic",
                "-OutputPath",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        record = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else None
        return completed, record

    def test_nested_first_party_is_discovered_and_nested_vendor_is_excluded(self) -> None:
        first_party = self._candidate(
            "companion-app/src/iRacingCoach.UI/wwwroot/nested/first-party.js"
        )
        vendor = self._candidate(
            "companion-app/src/iRacingCoach.UI/wwwroot/nested/wwwroot/lib/vendor.js",
            "SYNTAX_ERROR vendor is excluded\n",
        )
        completed, record, _ = self._run_synthetic([vendor, first_party])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["totals"]["run"], 1)
        self.assertEqual(
            [item["path"] for item in record["results"]],
            ["companion-app/src/iRacingCoach.UI/wwwroot/nested/first-party.js"],
        )

    def test_dependency_build_and_generated_paths_are_excluded(self) -> None:
        first_party = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/ok.js")
        excluded = [
            self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/node_modules/a.js"),
            self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/bin/a.js"),
            self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/obj/a.js"),
            self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/generated/a.js"),
        ]
        completed, record, _ = self._run_synthetic([*excluded, first_party])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["totals"]["run"], 1)

    def test_add_move_and_order_change_manifest_dynamically_and_deterministically(self) -> None:
        alpha = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/z/alpha.js")
        beta = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/a/beta.js")
        first, record_a, output_a = self._run_synthetic([alpha, beta])
        second, record_b, output_b = self._run_synthetic([beta, alpha])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(output_a.read_bytes(), output_b.read_bytes())
        self.assertEqual(record_a["totals"]["run"], 2)

        moved = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/moved/beta.js")
        beta.unlink()
        third, record_c, _ = self._run_synthetic([moved, alpha])
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertNotEqual(
            [item["path"] for item in record_a["results"]],
            [item["path"] for item in record_c["results"]],
        )

        added = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/new.js")
        fourth, record_d, _ = self._run_synthetic([moved, alpha, added])
        self.assertEqual(fourth.returncode, 0, fourth.stderr)
        self.assertEqual(record_d["totals"]["run"], 3)

    def test_deleted_candidate_fails_without_node_execution(self) -> None:
        missing = self.synthetic / "companion-app/src/iRacingCoach.UI/wwwroot/missing.js"
        completed, record, _ = self._run_synthetic([missing])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["results"][0]["outcome"], "notRun")
        self.assertEqual(record["results"][0]["reason"], "candidate-missing")
        self.assertFalse(self.node_log.exists(), "Node must not run after candidate validation fails")

    def test_syntax_error_fails_and_never_echoes_source(self) -> None:
        secret = "SYNTHETIC_SECRET_MUST_NOT_APPEAR"
        invalid = self._candidate(
            "companion-app/src/iRacingCoach.UI/wwwroot/invalid.js",
            f"SYNTAX_ERROR {secret}\n",
        )
        completed, record, output = self._run_synthetic([invalid])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["totals"]["failed"], 1)
        self.assertNotIn(secret, output.read_text(encoding="utf-8"))
        self.assertNotIn(secret, completed.stdout + completed.stderr)

    def test_zero_first_party_files_fails(self) -> None:
        vendor = self._candidate(
            "companion-app/src/iRacingCoach.Preview/wwwroot/lib/vendor.js"
        )
        completed, record, _ = self._run_synthetic([vendor])
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(record["discoveryComplete"])
        self.assertEqual(record["totals"]["run"], 0)

    def test_explicit_node_is_recorded_and_missing_explicit_node_never_falls_back(self) -> None:
        candidate = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/ok.js")
        completed, record, _ = self._run_synthetic([candidate])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(Path(record["node"]["path"]), self.node)
        self.assertEqual(record["node"]["rule"], "parameter")
        self.assertEqual(record["node"]["version"], "v99.1.0")

        missing = self.synthetic / "missing-node.cmd"
        failed, failed_record, _ = self._run_synthetic([candidate], node=missing)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIsNone(failed_record["node"])
        self.assertEqual(failed_record["totals"]["run"], 0)

    def test_duplicate_and_case_collision_fail(self) -> None:
        candidate = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/same.js")
        completed, record, _ = self._run_synthetic([candidate, candidate])
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(record["discoveryComplete"])

        case_variant = Path(str(candidate).replace("same.js", "SAME.js"))
        case_completed, case_record, _ = self._run_synthetic([candidate, case_variant])
        self.assertNotEqual(case_completed.returncode, 0)
        self.assertFalse(case_record["discoveryComplete"])

    def test_outside_synthetic_root_fails(self) -> None:
        outside_parent = tempfile.TemporaryDirectory(prefix="ws13b-outside-")
        try:
            outside = Path(outside_parent.name) / "companion-app/src/iRacingCoach.UI/wwwroot/out.js"
            outside.parent.mkdir(parents=True)
            outside.write_text("export const out = true;\n", encoding="utf-8")
            completed, record, _ = self._run_synthetic([outside])
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(record["discoveryComplete"])
        finally:
            outside_parent.cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_locked_file_is_refused_as_unreadable(self) -> None:
        candidate = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/locked.js")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateFileW(
            str(candidate),
            0x80000000,
            0,
            None,
            3,
            0x80,
            None,
        )
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            completed, record, _ = self._run_synthetic([candidate])
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(record["results"][0]["reason"], "candidate-unreadable")
        finally:
            kernel32.CloseHandle(handle)

    def test_synthetic_result_has_no_sha_and_truthful_limitations(self) -> None:
        candidate = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/ok.js")
        completed, record, _ = self._run_synthetic([candidate])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["mode"], "synthetic")
        self.assertIsNone(record["exactSha"])
        self.assertEqual(record["tier"], "SourceContract")
        self.assertEqual(record["proves"], "JavaScript syntax-only")
        self.assertIn("rendering", record["doesNotProve"])
        self.assertIn("package", record["doesNotProve"])

    def test_tracked_source_binds_working_bytes_to_exact_sha(self) -> None:
        repository, script, sha = self._init_tracked_repository()
        clean, clean_record = self._run_tracked(repository, sha)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(clean_record["exactSha"], sha)
        before = self.node_log.read_text(encoding="ascii")

        script.write_text("export const tracked = false;\n", encoding="utf-8", newline="\n")
        dirty, dirty_record = self._run_tracked(repository, sha)
        self.assertNotEqual(dirty.returncode, 0)
        self.assertEqual(dirty_record["results"][0]["reason"], "source-byte-mismatch")
        self.assertEqual(
            self.node_log.read_text(encoding="ascii"),
            before,
            "Node must not run when exact-byte binding fails",
        )

    def test_ci_uses_the_reusable_gate_without_a_magic_count(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Invoke-JavaScriptSyntaxGate.ps1", workflow)
        self.assertIn("-Authority ci-source-gate", workflow)
        self.assertIn("github.event_name", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("if: always()", workflow)
        self.assertNotIn("Expected 9 first-party JavaScript files", workflow)
        self.assertNotIn("node --check", workflow)

    def test_reparse_point_is_refused_when_host_can_create_one(self) -> None:
        target = self._candidate("companion-app/src/iRacingCoach.UI/wwwroot/target.js")
        link = self.synthetic / "companion-app/src/iRacingCoach.UI/wwwroot/link.js"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"host policy does not permit a synthetic symlink: {error}")
        completed, record, _ = self._run_synthetic([link])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["results"][0]["reason"], "reparse-point-refused")


if __name__ == "__main__":
    unittest.main()
