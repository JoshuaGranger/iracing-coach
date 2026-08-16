"""The real producer must satisfy the declared `analysis_view` contract.

Validating hand-authored vectors only proves the fixtures agree with the
schema. This runs the actual analysis over synthetic telemetry, builds the
envelope through the shipping producer, and validates that output against
`contracts/analysis-view-v1.schema.json`.

Producer evidence only. It does not prove any C# consumer refuses a defective
envelope; that closure belongs to the consumer phase.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
CONTRACTS = WORKSPACE_ROOT / "contracts"
sys.path.insert(0, str(SCRIPTS))

import workflow  # noqa: E402
from analysis_engine import analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "contract_validation_for_backend", WORKSPACE_ROOT / "tools" / "contract_validation.py"
)
assert _SPEC is not None and _SPEC.loader is not None
contract_validation = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = contract_validation
_SPEC.loader.exec_module(contract_validation)


def _schema() -> dict:
    return json.loads((CONTRACTS / "analysis-view-v1.schema.json").read_text(encoding="utf-8"))


class AnalysisViewProducerContractTests(unittest.TestCase):
    """Bind the shipping producer to the declared envelope."""

    @classmethod
    def setUpClass(cls) -> None:
        # Synthetic telemetry only. No archive, no private data, no external call.
        cls.analysis = analyze_telemetry(synthetic_telemetry(lap_count=8))
        cls.view = workflow.build_analysis_view(cls.analysis)

    def test_real_producer_output_conforms_to_the_declared_envelope(self) -> None:
        self.assertEqual(contract_validation.validate(self.view, _schema()), [])

    def test_emitted_key_set_equals_the_declared_field_authority(self) -> None:
        declared = {name for name, _kind, _default in workflow.ANALYSIS_VIEW_FIELDS}
        declared.add("schema_version")
        self.assertEqual(
            set(self.view),
            declared,
            "a field added to the producer without updating ANALYSIS_VIEW_FIELDS must fail here",
        )

    def test_emitted_version_is_the_single_authority(self) -> None:
        self.assertEqual(self.view["schema_version"], workflow.ANALYSIS_VIEW_SCHEMA_VERSION)
        self.assertNotIsInstance(self.view["schema_version"], bool)

    def test_a_populated_analysis_carries_real_sections(self) -> None:
        # Guards against the envelope conforming only because every section is
        # empty; an all-empty pass would make the contract check vacuous.
        self.assertTrue(self.view["identity"])
        self.assertTrue(self.view["race_summary"])
        self.assertTrue(self.view["laps"])

    def test_every_declared_field_is_present_even_with_an_empty_analysis(self) -> None:
        empty = workflow.build_analysis_view({})
        for name, _kind, _default in workflow.ANALYSIS_VIEW_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, empty)
        self.assertEqual(contract_validation.validate(empty, _schema()), [])

    def test_the_envelope_is_reachable_from_the_analyze_result_contract(self) -> None:
        parent = json.loads((CONTRACTS / "analyze-result-v1.schema.json").read_text(encoding="utf-8"))
        self.assertIn("analysis_view", parent["required"])


if __name__ == "__main__":
    unittest.main()
