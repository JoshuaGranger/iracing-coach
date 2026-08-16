"""The `analysis_view` envelope contract and its refusal behaviour.

`contracts/analyze-result-v1.schema.json` previously omitted `analysis_view`
entirely, so nothing validated the payload the C# mapper reads. These tests
cover the generated envelope contract against sanitized vectors.

Scope, stated so it is not overread: this is producer evidence. It proves the
declared contract accepts and refuses the right shapes. It does not prove any
C# consumer rejects them; that remains Codex's consumer phase.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "contracts"
VECTORS = ROOT / "test-data"
SCHEMA_PATH = CONTRACTS / "analysis-view-v1.schema.json"
PARENT_PATH = CONTRACTS / "analyze-result-v1.schema.json"

SPEC = importlib.util.spec_from_file_location(
    "contract_validation_for_envelope", ROOT / "tools" / "contract_validation.py"
)
assert SPEC is not None and SPEC.loader is not None
cv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cv
SPEC.loader.exec_module(cv)

SCRIPT_ROOT = ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    # workflow.py imports its siblings by bare name, as the backend does.
    sys.path.insert(0, str(SCRIPT_ROOT))

WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "workflow_for_envelope", SCRIPT_ROOT / "workflow.py"
)
assert WORKFLOW_SPEC is not None and WORKFLOW_SPEC.loader is not None
workflow = importlib.util.module_from_spec(WORKFLOW_SPEC)
sys.modules[WORKFLOW_SPEC.name] = workflow
WORKFLOW_SPEC.loader.exec_module(workflow)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _vector(name: str) -> dict:
    return json.loads((VECTORS / f"analysis-view-{name}.json").read_text(encoding="utf-8"))


class EnvelopeAcceptanceTests(unittest.TestCase):
    def test_valid_vectors_conform(self) -> None:
        for name in ("valid", "valid-empty-sections"):
            with self.subTest(vector=name):
                self.assertEqual(cv.validate(_vector(name), _schema()), [])

    def test_unknown_optional_fields_are_accepted(self) -> None:
        # A newer producer must stay readable; unknown-field preservation in the
        # C# consumer is a separate, later obligation.
        self.assertEqual(cv.validate(_vector("unknown-field"), _schema()), [])
        self.assertTrue(_schema()["additionalProperties"])

    def test_empty_sections_are_legal_and_not_treated_as_absent(self) -> None:
        vector = _vector("valid-empty-sections")
        self.assertEqual(vector["identity"], {})
        self.assertEqual(vector["laps"], [])
        self.assertEqual(cv.validate(vector, _schema()), [])


class EnvelopeRefusalTests(unittest.TestCase):
    def test_each_defective_vector_is_refused(self) -> None:
        for name in ("missing-required", "null-required", "wrong-type",
                     "wrong-version", "future-version", "boolean-version"):
            with self.subTest(vector=name):
                self.assertTrue(cv.validate(_vector(name), _schema()))

    def test_a_boolean_version_does_not_satisfy_the_const(self) -> None:
        errors = cv.validate(_vector("boolean-version"), _schema())
        self.assertTrue(any("schema_version" in error for error in errors))

    def test_future_versions_fail_rather_than_being_accepted_forward(self) -> None:
        errors = cv.validate(_vector("future-version"), _schema())
        self.assertTrue(any("const" in error for error in errors))


class EnvelopeAuthorityTests(unittest.TestCase):
    def test_the_schema_matches_the_producer_field_declaration(self) -> None:
        declared = {name for name, _kind, _default in workflow.ANALYSIS_VIEW_FIELDS}
        declared.add("schema_version")
        schema = _schema()
        self.assertEqual(set(schema["properties"]), declared)
        self.assertEqual(set(schema["required"]), declared)

    def test_the_producer_emits_exactly_the_declared_key_set(self) -> None:
        emitted = set(workflow.build_analysis_view({}))
        declared = {name for name, _kind, _default in workflow.ANALYSIS_VIEW_FIELDS}
        declared.add("schema_version")
        self.assertEqual(emitted, declared)

    def test_the_schema_version_const_matches_the_producer_constant(self) -> None:
        self.assertEqual(
            _schema()["properties"]["schema_version"]["const"],
            workflow.ANALYSIS_VIEW_SCHEMA_VERSION,
        )


class ParentContractTests(unittest.TestCase):
    def test_analysis_view_is_required_and_referenced(self) -> None:
        parent = json.loads(PARENT_PATH.read_text(encoding="utf-8"))
        self.assertIn("analysis_view", parent["required"])
        self.assertEqual(
            parent["properties"]["analysis_view"]["$ref"], "analysis-view-v1.schema.json"
        )


if __name__ == "__main__":
    unittest.main()
