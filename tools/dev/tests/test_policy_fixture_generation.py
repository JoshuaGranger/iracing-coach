"""The policy-fixture generator must refuse a stale fixture, not just write one.

A generator whose `--check` mode cannot fail is not a gate. These mutate each
committed fixture inside a sandbox and require the check to refuse it, and they
assert the separation that keeps the registry gate independent: the fixtures
are not produced by `export_contracts.py`, whose sandbox does not copy
`test-data/`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = (
    "live-truth-conformance-v1.json",
    "starting-tune-matrix-v1.json",
)


class PolicyFixtureGenerationTests(unittest.TestCase):
    def _check_with(self, mutate) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="policy-fixtures-") as raw:
            sandbox = Path(raw)
            shutil.copytree(
                ROOT / "iracing-coach" / "skills",
                sandbox / "iracing-coach" / "skills",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            (sandbox / "tools").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                ROOT / "tools" / "generate_policy_fixtures.py",
                sandbox / "tools" / "generate_policy_fixtures.py",
            )
            (sandbox / "test-data").mkdir(parents=True, exist_ok=True)
            for name in FIXTURES:
                shutil.copy2(ROOT / "test-data" / name, sandbox / "test-data" / name)
            mutate(sandbox / "test-data")
            return subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(sandbox / "tools" / "generate_policy_fixtures.py"),
                    "--check",
                ],
                cwd=sandbox,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

    def test_the_committed_fixtures_pass_check(self) -> None:
        self.assertEqual(self._check_with(lambda root: None).returncode, 0)

    def test_a_mutated_expectation_in_each_fixture_is_detected(self) -> None:
        for name in FIXTURES:
            with self.subTest(name):

                def mutate(root, name=name):
                    path = root / name
                    document = json.loads(path.read_text(encoding="utf-8"))
                    key = "flag_vectors" if "live-truth" in name else "rows"
                    document[key][0]["case" if key == "flag_vectors" else "requested_purpose"] = "tampered"
                    path.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )

                completed = self._check_with(mutate)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(name, completed.stdout)

    def test_a_deleted_fixture_is_detected_rather_than_silently_regenerated(self) -> None:
        for name in FIXTURES:
            with self.subTest(name):
                completed = self._check_with(lambda root, name=name: (root / name).unlink())
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(name, completed.stdout)

    def test_the_contract_exporter_does_not_emit_these_fixtures(self) -> None:
        """The registry gate sandboxes contracts and tools, never test-data."""
        source = (ROOT / "tools" / "export_contracts.py").read_text(encoding="utf-8")
        self.assertNotIn("test-data", source)
        for name in FIXTURES:
            self.assertNotIn(name, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
