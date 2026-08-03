from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_server  # noqa: E402


class McpFullTelemetryTests(unittest.TestCase):
    def test_tool_is_advertised_with_bounded_schema_and_new_version(self) -> None:
        self.assertEqual(mcp_server.SERVER_VERSION, "0.3.0")
        tool = next(
            item for item in mcp_server.TOOLS
            if item["name"] == "query_iracing_telemetry"
        )
        schema = tool["inputSchema"]
        properties = schema["properties"]

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            properties["mode"]["enum"], ["catalog", "profile", "slice"]
        )
        self.assertEqual(properties["channels"]["maxItems"], 12)
        self.assertEqual(properties["search"]["maxLength"], 100)
        self.assertEqual(properties["target_hz"]["oneOf"][0]["minimum"], 1)
        self.assertEqual(properties["target_hz"]["oneOf"][0]["maximum"], 60)
        self.assertEqual(properties["start_record"]["minimum"], 0)
        self.assertEqual(properties["end_record"]["minimum"], 1)
        self.assertEqual(properties["max_samples"]["maximum"], 2000)

    def test_dispatch_defaults_to_catalog_and_native_rate(self) -> None:
        workflow = mock.Mock(return_value={"ok": True})
        with mock.patch.object(
            mcp_server, "_workflow_function", return_value=workflow
        ) as resolver:
            result = mcp_server.call_tool("query_iracing_telemetry", {})

        self.assertEqual(result, {"ok": True})
        resolver.assert_called_once_with("telemetry_query_workflow")
        workflow.assert_called_once_with(
            selector="latest",
            iracing_root=mcp_server.DEFAULT_IRACING_ROOT,
            archive_root=str(mcp_server.DEFAULT_ARCHIVE_ROOT),
            mode="catalog",
            channels=None,
            search=None,
            target_hz=None,
            start_record=0,
            end_record=None,
            max_samples=1000,
        )

    def test_dispatch_forwards_only_normalized_bounded_arguments(self) -> None:
        workflow = mock.Mock(return_value={"mode": "slice"})
        selector = mcp_server.DEFAULT_IRACING_ROOT / "telemetry" / "race.ibt"
        telemetry_root = mcp_server.DEFAULT_IRACING_ROOT / "telemetry"
        archive_root = mcp_server.DEFAULT_ARCHIVE_ROOT / "query-tests"
        with mock.patch.object(
            mcp_server, "_workflow_function", return_value=workflow
        ):
            result = mcp_server.call_tool(
                "query_iracing_telemetry",
                {
                    "selector": str(selector),
                    "iracing_root": str(telemetry_root),
                    "archive_root": str(archive_root),
                    "mode": "slice",
                    "channels": ["SessionTime", "Speed"],
                    "search": "wheel",
                    "target_hz": 60,
                    "start_record": 10,
                    "end_record": 30,
                    "max_samples": 20,
                },
            )

        self.assertEqual(result, {"mode": "slice"})
        workflow.assert_called_once_with(
            selector=str(selector.resolve()),
            iracing_root=telemetry_root.resolve(),
            archive_root=str(archive_root.resolve()),
            mode="slice",
            channels=["SessionTime", "Speed"],
            search="wheel",
            target_hz=60.0,
            start_record=10,
            end_record=30,
            max_samples=20,
        )

    def test_runtime_validation_enforces_all_payload_bounds(self) -> None:
        cases = (
            ({"mode": "raw"}, "mode"),
            ({"channels": "Speed"}, "channels"),
            ({"channels": [f"Channel{index}" for index in range(13)]}, "12"),
            ({"channels": ["Speed", "Speed"]}, "duplicate"),
            ({"channels": [7]}, "channel name"),
            ({"search": "x" * 101}, "100"),
            ({"search": 7}, "search"),
            ({"target_hz": 0}, "target_hz"),
            ({"target_hz": 61}, "target_hz"),
            ({"target_hz": math.inf}, "target_hz"),
            ({"target_hz": True}, "target_hz"),
            ({"target_hz": "20"}, "target_hz"),
            ({"start_record": -1}, "start_record"),
            ({"start_record": True}, "start_record"),
            ({"end_record": 0}, "end_record"),
            ({"start_record": 10, "end_record": 10}, "greater"),
            ({"max_samples": 0}, "max_samples"),
            ({"max_samples": 2001}, "max_samples"),
            ({"max_samples": True}, "max_samples"),
            ({"unbounded_path": r"C:\Windows"}, "unsupported"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, message):
                    mcp_server.call_tool("query_iracing_telemetry", arguments)

    def test_selector_and_root_cannot_escape_configured_iracing_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "query_iracing_telemetry",
                {"selector": r"C:\Windows\outside.ibt"},
            )
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "query_iracing_telemetry",
                {"iracing_root": r"C:\Windows"},
            )
        with self.assertRaisesRegex(ValueError, "UNC"):
            mcp_server.call_tool(
                "query_iracing_telemetry",
                {"selector": r"\\attacker.example\race.ibt"},
            )


if __name__ == "__main__":
    unittest.main()
