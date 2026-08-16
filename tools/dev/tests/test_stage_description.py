"""The analyze tool must not advertise stage timings it does not produce.

The tool description claimed the analyze result returns "stage timings". In a
racing tool that reads as race stages. The only stage timing in the codebase is
`workflow.py`'s bounded monotonic pipeline timing for companion diagnostics,
which is not a caller-facing analysis capability and is not what the phrase
would be understood to mean.

These are source and generated-contract assertions. They prove the claim is
absent from the description; they prove nothing about stage behaviour, and no
stage behaviour was changed.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER = ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts" / "mcp_server.py"
TOOLS_CONTRACT = ROOT / "contracts" / "mcp-tools.v1.json"
WORKFLOW = ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts" / "workflow.py"

REMOVED_CLAIM = "with stage timings"


def _analyze_tool() -> dict:
    contract = json.loads(TOOLS_CONTRACT.read_text(encoding="utf-8"))
    for tool in contract["tools"]:
        if tool["name"] == "analyze_iracing_race":
            return tool
    raise AssertionError("analyze_iracing_race is missing from the tools contract")


class StageTimingClaimTests(unittest.TestCase):
    def test_the_claim_is_absent_from_the_source_of_truth(self) -> None:
        self.assertNotIn(REMOVED_CLAIM, MCP_SERVER.read_text(encoding="utf-8"))

    def test_the_claim_is_absent_from_the_generated_contract(self) -> None:
        # The generated file regenerates from mcp_server.TOOLS, so a stale
        # contract would still carry the claim after the source was corrected.
        self.assertNotIn(REMOVED_CLAIM, TOOLS_CONTRACT.read_text(encoding="utf-8"))
        self.assertNotIn("stage timings", _analyze_tool()["description"])

    def test_the_description_still_describes_what_the_tool_does(self) -> None:
        # Removing a false claim must not remove the true ones around it.
        description = _analyze_tool()["description"]
        for retained in ("Race Card", "damage", "tow", "repair", "pit", "tire",
                         "fuel", "strategy", "coaching context", "archive"):
            with self.subTest(term=retained):
                self.assertIn(retained, description)

    def test_no_race_stage_capability_was_added(self) -> None:
        description = _analyze_tool()["description"]
        for invented in ("stage 1", "stage 2", "race stage", "stage results"):
            with self.subTest(term=invented):
                self.assertNotIn(invented, description.lower())

    def test_the_internal_diagnostic_timing_is_untouched_and_unadvertised(self) -> None:
        # The pipeline timing still exists for diagnostics. The point of this
        # slice is that it is not advertised as an analysis capability, not that
        # it was removed.
        self.assertIn("bounded monotonic stage timing", WORKFLOW.read_text(encoding="utf-8"))
        self.assertNotIn("stage timing", _analyze_tool()["description"].lower())


if __name__ == "__main__":
    unittest.main()
