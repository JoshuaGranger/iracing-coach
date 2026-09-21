"""Regression: an infeasible Bristol strategy must not blank the desktop race."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/analyze-iracing-race/scripts"))
import mcp_server
import workflow
from test_ibt_reader import build_ibt


class AnalysisJsonTests(unittest.TestCase):
    def test_old_cache_with_valid_digest_but_nonfinite_strategy_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "race.ibt"
            build_ibt(source)
            os.utime(source, (1_780_000_000, 1_780_000_000))
            arguments = dict(selector=str(source), iracing_root=root, archive_root=root / "archive")
            first = workflow.analyze_race_workflow(**arguments)
            cache = Path(first["analysis_cache"]["path"])
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["analysis"]["strategy_planning"] = {
                "plan_comparison": {"strategies": [{"feasible": False, "total_time_s": float("inf")}]}
            }
            payload["analysis_sha256"] = workflow.stable_hash(payload["analysis"], 64)
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(workflow, "analyze_telemetry", wraps=workflow.analyze_telemetry) as analyze:
                repaired = workflow.analyze_race_workflow(**arguments)
                self.assertFalse(repaired["analysis_cache"]["hit"])
                analyze.assert_called_once()
                json.dumps(repaired, allow_nan=False)
                cache.read_text(encoding="utf-8")
                again = workflow.analyze_race_workflow(**arguments)
                self.assertTrue(again["analysis_cache"]["hit"])
                analyze.assert_called_once()

    def test_transport_returns_actionable_error_instead_of_invalid_json(self) -> None:
        with mock.patch.object(mcp_server, "call_tool", return_value={"value": float("inf")}):
            response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze_iracing_race"}})
        json.dumps(response, allow_nan=False)
        self.assertTrue(response["result"]["isError"])
        error = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(error["error"], "ValueError")
        self.assertEqual(error["tool"], "analyze_iracing_race")


if __name__ == "__main__":
    unittest.main()
