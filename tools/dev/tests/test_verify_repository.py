"""Verify that the repository verifier reports structured backend counts.

The verifier used to derive its reported summary from the backend child's merged
stdout, taking the last line. `unittest` writes its summary to stderr, so any
test printing to stdout after the run supplied the "summary" instead. The real
payload observed in this repository is a benchmark line from the replay tests.

The exit code was always checked separately, so the gate was never fooled; what
was wrong was the emitted evidence. These tests hold that line: counts come only
from a complete, unfiltered, backend-family structured document, and no field is
ever derived from child output.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools" / "verify_repository.py"

SPEC = importlib.util.spec_from_file_location("verify_repository_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
verify_repository = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_repository
SPEC.loader.exec_module(verify_repository)

BENCHMARK_LINE = (
    "hour replay: 216,000 source -> 8,012 display frames, 512,768 car rows, 23.4 MiB JSON, 12.75s"
)


def _result(identity: str, outcome: str = "passed") -> dict[str, object]:
    return {
        "id": identity,
        "displayId": identity,
        "outcome": outcome,
        "durationMs": 1.5,
        "skipReason": None,
    }


def _document(results: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schemaVersion": 1,
        "family": "backend",
        "discoveryComplete": True,
        "runState": "complete",
        "filter": None,
        "results": results,
        "failure": None,
    }
    document.update(overrides)
    return document


class BackendSuiteReportingTests(unittest.TestCase):
    """Drive `_run_backend_suite` through a seam instead of a real backend run."""

    def setUp(self) -> None:
        self.observed_output: Path | None = None
        self.observed_command: list[str] = []

    def _runner(self, document: object | str | None, returncode: int = 0,
                stdout: str = "", stderr: str = ""):
        """Build a subprocess seam that writes `document` to the requested --out."""

        def run(command, **kwargs):
            self.observed_command = [str(part) for part in command]
            out = Path(self.observed_command[self.observed_command.index("--out") + 1])
            self.observed_output = out
            if document is not None:
                payload = document if isinstance(document, str) else json.dumps(document)
                out.write_text(payload, encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        return run

    # Case 1 - the actual poisoning mechanism.
    def test_trailing_benchmark_output_cannot_reach_the_reported_counts(self) -> None:
        runner = self._runner(
            _document([_result("t.C.a"), _result("t.C.b"), _result("t.C.c", "skipped")]),
            stdout=f"irrelevant\n{BENCHMARK_LINE}\n",
            stderr=f"Ran 3 tests\nOK\n{BENCHMARK_LINE}\n",
        )
        tests = verify_repository._run_backend_suite(runner=runner)
        self.assertEqual(
            tests,
            {"run": True, "exit_code": 0, "total": 3, "passed": 2, "failed": 0, "skipped": 1},
        )
        self.assertNotIn("summary", tests, "the scraped summary field must be gone")
        for value in tests.values():
            self.assertNotIn(
                "hour replay", str(value), "no reported field may carry child output"
            )

    # Case 2 - non-zero exit fails even with a complete-looking document.
    def test_producer_failure_fails_even_when_the_document_looks_complete(self) -> None:
        runner = self._runner(_document([_result("t.C.a")]), returncode=1)
        with self.assertRaisesRegex(RuntimeError, "Backend unit suite failed"):
            verify_repository._run_backend_suite(runner=runner)

    # Case 3 - clean exit with a missing or malformed document fails.
    def test_missing_document_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "was not produced"):
            verify_repository._run_backend_suite(runner=self._runner(None))

    def test_malformed_document_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            verify_repository._run_backend_suite(runner=self._runner("{ not json"))

    # Case 4 - every invalid shape refuses, and never reports totals.
    def test_invalid_documents_fail_without_totals(self) -> None:
        cases = {
            "schemaVersion": _document([_result("t.C.a")], schemaVersion=2),
            "family": _document([_result("t.C.a")], family="devtools"),
            "discoveryComplete": _document([_result("t.C.a")], discoveryComplete=False),
            "runState-partial": _document([_result("t.C.a")], runState="partial"),
            "runState-invalid": _document([_result("t.C.a")], runState="invalid"),
            "filter": _document([_result("t.C.a")], filter="t.C.a"),
            "failure": _document([_result("t.C.a")], failure="runner-error"),
            "empty-results": _document([]),
            "duplicate-identity": _document([_result("t.C.a"), _result("t.C.a")]),
            "unknown-outcome": _document([_result("t.C.a", "notRun")]),
            "empty-identity": _document([_result("   ")]),
            "missing-field": _document([{"id": "t.C.a", "outcome": "passed"}]),
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(RuntimeError) as caught:
                    verify_repository._run_backend_suite(runner=self._runner(document))
                self.assertNotIn("total", str(caught.exception))

    # Case 5 - deterministic counts from valid records.
    def test_valid_records_produce_exact_counts(self) -> None:
        results = [_result(f"t.C.t{index}") for index in range(7)]
        results.append(_result("t.C.skipped", "skipped"))
        tests = verify_repository._run_backend_suite(runner=self._runner(_document(results)))
        self.assertEqual(tests["total"], 8)
        self.assertEqual(tests["passed"], 7)
        self.assertEqual(tests["skipped"], 1)
        self.assertEqual(tests["failed"], 0)
        self.assertEqual(tests["total"], tests["passed"] + tests["failed"] + tests["skipped"])

    def test_a_failed_record_raises_rather_than_reporting_a_passing_record(self) -> None:
        document = _document([_result("t.C.a"), _result("t.C.b", "failed")])
        with self.assertRaisesRegex(RuntimeError, "Backend unit suite failed"):
            verify_repository._run_backend_suite(runner=self._runner(document))

    # Case 7 - the exact temporary output is removed on success and on failure.
    def test_temporary_output_is_removed_on_success_and_on_failure(self) -> None:
        runner = self._runner(_document([_result("t.C.a")]))
        verify_repository._run_backend_suite(runner=runner)
        self.assertIsNotNone(self.observed_output)
        assert self.observed_output is not None
        self.assertFalse(self.observed_output.exists())
        self.assertFalse(self.observed_output.parent.exists())

        failing = self._runner(_document([]), returncode=0)
        with self.assertRaises(RuntimeError):
            verify_repository._run_backend_suite(runner=failing)
        assert self.observed_output is not None
        self.assertFalse(self.observed_output.parent.exists())

    def test_producer_output_stays_under_the_os_temporary_root(self) -> None:
        verify_repository._run_backend_suite(runner=self._runner(_document([_result("t.C.a")])))
        assert self.observed_output is not None
        temporary_root = Path(tempfile.gettempdir()).resolve()
        self.assertEqual(
            temporary_root,
            Path(os.path.commonpath([temporary_root, self.observed_output.resolve()])),
            "evidence must never be written into the repository or a profile root",
        )

    def test_the_accepted_producer_is_invoked_with_the_declared_contract(self) -> None:
        verify_repository._run_backend_suite(runner=self._runner(_document([_result("t.C.a")])))
        self.assertEqual(self.observed_command[0], sys.executable)
        self.assertIn("-X", self.observed_command)
        self.assertIn("utf8", self.observed_command)
        self.assertIn(str(verify_repository.BACKEND_PRODUCER), self.observed_command)
        self.assertEqual(
            self.observed_command[self.observed_command.index("--family") + 1], "backend"
        )
        self.assertEqual(
            self.observed_command[self.observed_command.index("--pattern") + 1], "test_*.py"
        )


class VerifierSourceContractTests(unittest.TestCase):
    """The scraped-summary construct must not come back."""

    def test_the_verifier_never_derives_a_reported_field_from_child_output(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        # Scoped to the backend reporting path. `summary` is a legitimate key in
        # several fixture-shape checks elsewhere in this file, so asserting on
        # the bare word would be a false positive rather than a guard.
        start = source.index("def _run_backend_suite(")
        end = source.index("\ndef main(", start)
        reporting = source[start:end]
        self.assertNotIn("stderr=subprocess.STDOUT", reporting)
        self.assertNotIn("splitlines()", reporting)
        self.assertNotIn('"summary"', reporting)
        self.assertNotIn("completed.stdout", reporting)

    def test_the_backend_producer_runs_only_under_full_verification(self) -> None:
        """Case 6 of the accepted matrix, checked statically.

        Executing it would mean running the whole verifier, which is slower and
        no more truthful than reading the guard, so this is a source contract
        rather than a behavioural test and is declared as one.
        """
        source = MODULE_PATH.read_text(encoding="utf-8")
        default = source.index('tests: dict[str, Any] = {"run": False}')
        guard = source.index("if args.full:", default)
        call = source.index("_run_backend_suite()", guard)
        self.assertLess(default, guard)
        self.assertLess(guard, call)
        self.assertEqual(
            1,
            source.count("_run_backend_suite()"),
            "the producer must have exactly one call site, inside the --full guard",
        )


if __name__ == "__main__":
    unittest.main()
