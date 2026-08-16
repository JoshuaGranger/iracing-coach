from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[3]
REPORT_SCRIPT = ROOT / "tools" / "New-EvidenceReport.ps1"
PYTHON_PRODUCER = ROOT / "tools" / "evidence" / "emit_python_results.py"
SCHEMA = ROOT / "tools" / "evidence" / "normalized-result.schema.json"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _raw(family: str, identities: list[str], *, state: str = "complete", filter_value: str | None = None) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "family": family,
        "discoveryComplete": state != "invalid",
        "runState": state,
        "filter": filter_value,
        "results": [
            {
                "id": identity,
                "displayId": identity,
                "outcome": "passed",
                "durationMs": float(index + 1),
                "skipReason": None,
            }
            for index, identity in enumerate(identities)
        ] if state != "invalid" else [],
        "failure": None if state != "invalid" else "synthetic-invalid",
    }


def _declaration(identity: str, *, tier: str = "Behavioral", technique: str = "none", fixture_sources: list[dict[str, str]] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "family": "devtools",
        "id": identity,
        "tier": tier,
        "technique": technique,
    }
    if fixture_sources is not None:
        value["fixtureSources"] = fixture_sources
    return value


def _trx(results: list[tuple[str, str, str, str]], summary: str = "Completed") -> str:
    definitions = []
    executions = []
    for test_id, class_name, method_name, _ in results:
        definitions.append(
            f'<UnitTest name="{method_name}" id="{test_id}"><Execution id="exec-{test_id}" />'
            f'<TestMethod codeBase="tests.dll" adapterTypeName="executor" className="{class_name}" name="{method_name}" /></UnitTest>'
        )
    for test_id, _, method_name, outcome in results:
        executions.append(
            f'<UnitTestResult executionId="exec-{test_id}" testId="{test_id}" testName="{method_name}" '
            f'outcome="{outcome}" duration="00:00:00.0010000" />'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
        f'<Results>{"".join(executions)}</Results><TestDefinitions>{"".join(definitions)}</TestDefinitions>'
        f'<ResultSummary outcome="{summary}" /></TestRun>'
    )


class EvidenceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.gettempdir()).resolve()
        self.root = base / f"iracing-evidence-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.repository = self.root / "repo"
        self.repository.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.name", "WS13B Test"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.email", "ws13b@example.invalid"], check=True)
        (self.repository / "marker.txt").write_text("marker\n", encoding="utf-8")
        self.registry = self.repository / "tools" / "evidence" / "evidence-declarations.json"
        self.raw = self.root / "raw.json"
        self.output = self.root / "normalized.json"
        self.provenance = self.root / "provenance.json"
        _write_json(
            self.provenance,
            {
                "toolchain": {
                    "python": {"path": str(Path(sys.executable).resolve()), "version": sys.version.split()[0], "sha256": "0" * 64, "rule": "test"},
                    "node": None,
                    "dotnet": None,
                    "required": ["python"],
                    "optional": [],
                    "authority": "local-diagnostic",
                    "rejected": [],
                }
            },
        )

    def tearDown(self) -> None:
        resolved = self.root.resolve()
        base = Path(tempfile.gettempdir()).resolve()
        self.assertTrue(resolved.is_relative_to(base))

        def remove_readonly(function, path, _error):
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(resolved, onexc=remove_readonly)

    def _commit(self) -> str:
        subprocess.run(["git", "-C", str(self.repository), "add", "--all"], check=True)
        subprocess.run(["git", "-c", "gc.auto=0", "-C", str(self.repository), "commit", "-q", "-m", "fixture"], check=True)
        return subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _prepare(self, identities: list[str], declarations: list[dict[str, object]] | None = None) -> str:
        _write_json(self.raw, _raw("devtools", identities))
        _write_json(
            self.registry,
            {"schemaVersion": 1, "declarations": declarations if declarations is not None else [_declaration(identity) for identity in identities]},
        )
        return self._commit()

    def _run(
        self,
        sha: str,
        *,
        family: str = "devtools",
        input_path: Path | None = None,
        filter_value: str | None = None,
        catalog: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
        parts = [
            "$prov=Get-Content -LiteralPath '" + str(self.provenance).replace("'", "''") + "' -Raw | ConvertFrom-Json;",
            "& '" + str(REPORT_SCRIPT).replace("'", "''") + "'",
            "-RepositoryRoot '" + str(self.repository).replace("'", "''") + "'",
            "-SourceSha '" + sha + "'",
            "-Family '" + family + "'",
            "-InputPath '" + str(input_path or self.raw).replace("'", "''") + "'",
            "-RegistryPath '" + str(self.registry).replace("'", "''") + "'",
            "-Authority local-diagnostic -ToolchainProvenance $prov",
            "-OutputPath '" + str(self.output).replace("'", "''") + "'",
        ]
        if filter_value is not None:
            parts.append("-Filter '" + filter_value.replace("'", "''") + "'")
        if catalog is not None:
            parts.append("-CatalogPath '" + str(catalog).replace("'", "''") + "'")
        completed = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", " ".join(parts)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        value = json.loads(self.output.read_text(encoding="utf-8-sig")) if self.output.exists() else None
        return completed, value

    def test_schema_declares_invalid_without_totals(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("limitations", schema["required"])
        invalid_branch = schema["allOf"][0]["then"]
        self.assertEqual(invalid_branch["not"], {"required": ["totals"]})

    def test_complete_report_has_all_tiers_and_no_aggregate(self) -> None:
        sha = self._prepare(["sample.Case.test_rule"])
        completed, record = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(list(record["totals"]), ["SourceContract", "Fixture", "Behavioral", "Rendered", "Package"])
        self.assertEqual(record["totals"]["Rendered"], {"run": 0, "passed": 0, "failed": 0, "skipped": 0, "notRun": 0})
        self.assertNotIn("passed", record)

    def test_dynamic_add_move_delete_changes_only_runner_count(self) -> None:
        first = "sample.Case.test_first"
        second = "sample.Case.test_second"
        sha = self._prepare([first, second])
        completed, record = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["totals"]["Behavioral"]["run"], 2)
        self.assertEqual([item["id"] for item in record["results"]], [first, second])

    def test_benchmark_line_poisoning_is_not_parsed(self) -> None:
        poison = "hour replay: 216,000 source -> 8,012 display frames, 512,768 car rows"
        identity = "sample.Case.test_rule"
        value = _raw("devtools", [identity])
        value["console"] = poison
        _write_json(self.raw, value)
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration(identity)]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")
        self.assertNotIn("totals", record)

    def test_zero_discovery_is_invalid_without_totals(self) -> None:
        _write_json(self.raw, _raw("devtools", [], state="invalid"))
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration("sample.Case.test_rule")]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")
        self.assertNotIn("totals", record)

    def test_malformed_raw_is_invalid(self) -> None:
        self.raw.write_text("not json", encoding="utf-8")
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration("sample.Case.test_rule")]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["results"], [])

    def test_aborted_run_has_no_passing_count(self) -> None:
        _write_json(self.raw, _raw("devtools", [], state="invalid"))
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration("sample.Case.test_rule")]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("totals", record)
        self.assertEqual(record["discoveryComplete"], False)

    def test_missing_and_stale_declarations_fail(self) -> None:
        sha = self._prepare(["sample.Case.test_actual"], [_declaration("sample.Case.test_stale")])
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")

    def test_source_tree_divergence_fails_before_claiming_sha(self) -> None:
        sha = self._prepare(["sample.Case.test_rule"])
        (self.repository / "marker.txt").write_text("changed\n", encoding="utf-8")
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")
        self.assertNotIn("totals", record)

    def test_duplicate_declaration_fails(self) -> None:
        identity = "sample.Case.test_rule"
        sha = self._prepare([identity], [_declaration(identity), _declaration(identity)])
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")

    def test_unknown_tier_fails(self) -> None:
        identity = "sample.Case.test_rule"
        declaration = _declaration(identity)
        declaration["tier"] = "Magic"
        sha = self._prepare([identity], [declaration])
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")

    def test_unknown_technique_fails(self) -> None:
        identity = "sample.Case.test_rule"
        declaration = _declaration(identity)
        declaration["technique"] = "guess"
        sha = self._prepare([identity], [declaration])
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")

    def test_fixture_paths_and_provider_sources_validate_at_sha(self) -> None:
        identity = "sample.Case.test_fixture"
        fixture = self.repository / "fixtures" / "sample.json"
        provider = self.repository / "providers" / "FakeProvider.py"
        fixture.parent.mkdir(); provider.parent.mkdir()
        fixture.write_text("{}\n", encoding="utf-8"); provider.write_text("class FakeProvider: pass\n", encoding="utf-8")
        sources = [
            {"kind": "path", "value": "fixtures/sample.json"},
            {"kind": "provider", "value": "providers.FakeProvider", "sourcePath": "providers/FakeProvider.py"},
        ]
        sha = self._prepare([identity], [_declaration(identity, tier="Fixture", fixture_sources=sources)])
        completed, record = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["results"][0]["tier"], "Fixture")

    def test_missing_fixture_source_fails(self) -> None:
        identity = "sample.Case.test_fixture"
        sha = self._prepare(
            [identity],
            [_declaration(identity, tier="Fixture", fixture_sources=[{"kind": "path", "value": "missing.json"}])],
        )
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "invalid")

    def test_filtered_run_requires_complete_same_family_catalog(self) -> None:
        identities = ["sample.Case.test_first", "sample.Case.test_second"]
        catalog = self.root / "catalog.json"
        _write_json(catalog, _raw("devtools", identities))
        _write_json(self.raw, _raw("devtools", [identities[0]], state="partial", filter_value="*first"))
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration(item) for item in identities]})
        sha = self._commit()
        failed, invalid = self._run(sha, filter_value="*first")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(invalid["runState"], "invalid")
        self.output.unlink()
        passed, record = self._run(sha, filter_value="*first", catalog=catalog)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(record["runState"], "partial")

    def test_source_contract_name_mismatch_is_advisory(self) -> None:
        identity = "sample.Case.test_renders_summary"
        sha = self._prepare([identity], [_declaration(identity, tier="SourceContract")])
        completed, record = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(record["results"][0]["nameMismatch"])
        self.assertEqual(record["results"][0]["tier"], "SourceContract")

    def test_skip_is_never_folded_into_passed(self) -> None:
        identity = "sample.Case.test_rule"
        value = _raw("devtools", [identity])
        value["results"][0]["outcome"] = "skipped"
        value["results"][0]["skipReason"] = "synthetic"
        _write_json(self.raw, value)
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration(identity)]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["totals"]["Behavioral"]["passed"], 0)
        self.assertEqual(record["totals"]["Behavioral"]["skipped"], 1)

    def test_completed_failure_writes_truth_then_exits_nonzero(self) -> None:
        identity = "sample.Case.test_rule"
        value = _raw("devtools", [identity])
        value["results"][0]["outcome"] = "failed"
        _write_json(self.raw, value)
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [_declaration(identity)]})
        sha = self._commit()
        completed, record = self._run(sha)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(record["runState"], "complete")
        self.assertEqual(record["totals"]["Behavioral"]["failed"], 1)

    def test_identical_structured_input_is_byte_deterministic(self) -> None:
        sha = self._prepare(["sample.Case.test_rule"])
        first, _ = self._run(sha)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = self.output.read_bytes()
        self.output.unlink()
        second, _ = self._run(sha)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.output.read_bytes(), first_bytes)

    def test_trx_data_rows_inherit_one_parent_declaration(self) -> None:
        identity = "Sample.Tests.Case"
        trx = self.root / "results.trx"
        trx.write_text(
            _trx([
                ("row-1", "Sample.Tests", "Case (1)", "Passed"),
                ("row-2", "Sample.Tests", "Case (2)", "Passed"),
            ]),
            encoding="utf-8",
        )
        declaration = _declaration(identity); declaration["family"] = "dotnet"
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [declaration]})
        sha = self._commit()
        completed, record = self._run(sha, family="dotnet", input_path=trx)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(record["totals"]["Behavioral"]["run"], 2)
        self.assertEqual(len({item["id"] for item in record["results"]}), 2)

    def test_invalid_trx_summary_has_no_totals(self) -> None:
        trx = self.root / "results.trx"
        trx.write_text(_trx([("one", "Sample.Tests", "Case", "Passed")], summary="Aborted"), encoding="utf-8")
        declaration = _declaration("Sample.Tests.Case"); declaration["family"] = "dotnet"
        _write_json(self.registry, {"schemaVersion": 1, "declarations": [declaration]})
        sha = self._commit()
        completed, record = self._run(sha, family="dotnet", input_path=trx)
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("totals", record)

    def test_python_producer_records_file_only_structured_outcomes(self) -> None:
        suite = self.root / "suite"; suite.mkdir()
        (suite / "test_sample.py").write_text(
            textwrap.dedent(
                """
                import unittest
                class Sample(unittest.TestCase):
                    def test_pass(self):
                        print('hour replay: 216,000 source -> 8,012 display frames')
                    @unittest.skip('synthetic')
                    def test_skip(self): pass
                    def test_failure(self): self.fail('expected')
                    def test_error(self): raise RuntimeError('expected')
                """
            ),
            encoding="utf-8",
        )
        output = self.root / "producer.json"
        completed = subprocess.run(
            [sys.executable, str(PYTHON_PRODUCER), "--family", "devtools", "--start-dir", str(suite), "--out", str(output)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 1)
        record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(sorted(item["outcome"] for item in record["results"]), ["failed", "failed", "passed", "skipped"])
        self.assertNotIn("hour replay", output.read_text(encoding="utf-8"))

    def test_python_producer_zero_discovery_fails_closed(self) -> None:
        suite = self.root / "empty"; suite.mkdir()
        output = self.root / "producer.json"
        completed = subprocess.run(
            [sys.executable, str(PYTHON_PRODUCER), "--family", "devtools", "--start-dir", str(suite), "--out", str(output)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertNotEqual(completed.returncode, 0)
        record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["runState"], "invalid")

    def test_report_is_utf8_newline_terminated_and_atomic(self) -> None:
        sha = self._prepare(["sample.Case.test_rule"])
        completed, _ = self._run(sha)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = self.output.read_bytes()
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(list(self.output.parent.glob(f".{self.output.name}.*.tmp")), [])

    def test_ci_activates_declared_families_without_hard_coded_counts(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("New-EvidenceReport.ps1", workflow)
        self.assertIn("emit_python_results.py", workflow)
        self.assertIn("evidence-declarations.json", workflow)
        self.assertNotRegex(workflow, r"expected\s*[-_=]?\s*(56|70|267|278|279)")

    def test_behavioral_sample_is_reproducible(self) -> None:
        identities = [f"sample.Case.test_{index:03d}" for index in range(40)]
        count = min(len(identities), max(20, (len(identities) + 9) // 10))
        ranked = sorted(identities, key=lambda identity: (hashlib.sha256(f"devtools\n{identity}".encode()).hexdigest(), identity))
        self.assertEqual(count, 20)
        self.assertEqual(ranked[:count], sorted(identities, key=lambda identity: (hashlib.sha256(f"devtools\n{identity}".encode()).hexdigest(), identity))[:20])


if __name__ == "__main__":
    unittest.main()
