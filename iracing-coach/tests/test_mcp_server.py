from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_server  # noqa: E402


class McpServerSafetyTests(unittest.TestCase):
    def test_stdio_server_uses_one_json_rpc_object_per_line(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        ]
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-u", str(SCRIPTS / "mcp_server.py")],
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3])
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual(
            responses[2]["result"]["tools"],
            mcp_server.TOOLS,
        )

    def test_rejects_unc_before_workflow_or_archive_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "UNC"):
            mcp_server.call_tool(
                "discover_iracing_sessions",
                {"root": r"\\attacker.example\telemetry"},
            )
        with self.assertRaisesRegex(ValueError, "UNC"):
            mcp_server.call_tool(
                "garage61_auth_status",
                {"archive_root": r"\\attacker.example\archive"},
            )

    def test_rejects_paths_outside_configured_roots(self) -> None:
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "analyze_iracing_race",
                {"selector": r"C:\Windows\outside.ibt"},
            )
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "sync_garage61_references",
                {"analysis_path": r"C:\Windows\analysis.json"},
            )
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "catalog_iracing_setups",
                {"root": r"C:\Windows"},
            )
        with self.assertRaisesRegex(ValueError, "must stay within"):
            mcp_server.call_tool(
                "recommend_open_setup_tuning",
                {
                    "analysis_path": r"C:\Windows\analysis.json",
                    "symptoms": "tight center",
                },
            )

    def test_setup_tools_are_bounded_and_do_not_expose_a_writer(self) -> None:
        names = {item["name"] for item in mcp_server.TOOLS}
        self.assertTrue(
            {
                "catalog_iracing_setups",
                "build_open_setup_package",
                "recommend_open_setup_tuning",
                "record_open_setup_feedback",
                "iracing_setup_history",
            }.issubset(names)
        )
        self.assertNotIn("write_setup", names)
        with self.assertRaisesRegex(ValueError, "package_id"):
            mcp_server.call_tool(
                "recommend_open_setup_tuning",
                {
                    "analysis_path": str(mcp_server.DEFAULT_ARCHIVE_ROOT / "reports" / "x" / "analysis.json"),
                    "package_id": "../escape",
                    "symptoms": "tight center",
                },
            )

    def test_companion_dashboard_is_exposed_as_a_bounded_read_only_snapshot(self) -> None:
        dashboard = next(
            item
            for item in mcp_server.TOOLS
            if item["name"] == "iracing_companion_dashboard"
        )
        limit = dashboard["inputSchema"]["properties"]["limit"]
        self.assertEqual(limit["maximum"], 100)
        self.assertNotIn("write", dashboard["name"])

    def test_native_event_finder_is_bounded_and_forwards_exact_context(self) -> None:
        tool = next(
            item
            for item in mcp_server.TOOLS
            if item["name"] == "find_iracing_telemetry_events"
        )
        schema = tool["inputSchema"]
        self.assertEqual(schema["properties"]["max_events"]["maximum"], 500)
        self.assertEqual(
            schema["properties"]["selection_mode"]["enum"],
            list(mcp_server.SUPPORTED_EVENT_SELECTION_MODES),
        )
        self.assertEqual(
            schema["properties"]["event_types"]["items"]["enum"],
            list(mcp_server.SUPPORTED_EVENT_TYPES),
        )
        find_events = mock.Mock(return_value={"ok": True, "events": []})
        with mock.patch.object(
            mcp_server, "_workflow_function", return_value=find_events
        ):
            result = mcp_server.call_tool(
                "find_iracing_telemetry_events",
                {
                    "selector": "12345",
                    "event_types": ["brake_onset", "steering_torque_peak"],
                    "selection_mode": "severity",
                    "start_record": 120,
                    "end_record": 960,
                    "max_events": 25,
                    "lap": 14,
                    "session_time_start": 300,
                    "session_time_end": 340.5,
                    "lap_distance_start": 0.9,
                    "lap_distance_end": 0.1,
                },
            )
        self.assertTrue(result["ok"])
        find_events.assert_called_once_with(
            selector="12345",
            iracing_root=mcp_server.DEFAULT_IRACING_ROOT,
            archive_root=str(mcp_server.DEFAULT_ARCHIVE_ROOT),
            event_types=["brake_onset", "steering_torque_peak"],
            selection_mode="severity",
            start_record=120,
            end_record=960,
            max_events=25,
            lap=14,
            session_time_start=300.0,
            session_time_end=340.5,
            lap_distance_start=0.9,
            lap_distance_end=0.1,
        )

    def test_native_event_finder_rejects_invalid_or_oversized_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "event_types"):
            mcp_server._native_event_arguments({"event_types": []})
        with self.assertRaisesRegex(ValueError, "event_types"):
            mcp_server._native_event_arguments({"event_types": ["wheel_lock"]})
        with self.assertRaisesRegex(ValueError, "max_events"):
            mcp_server._native_event_arguments({"max_events": 501})
        with self.assertRaisesRegex(ValueError, "selection_mode"):
            mcp_server._native_event_arguments({"selection_mode": "strongest-ish"})
        with self.assertRaisesRegex(ValueError, "session_time_end"):
            mcp_server._native_event_arguments(
                {"session_time_start": 20.0, "session_time_end": 19.0}
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            mcp_server._native_event_arguments({"write": True})

    def test_initialize_never_echoes_an_unsupported_protocol(self) -> None:
        response = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "unsupported-x"},
            }
        )
        self.assertIn(response["result"]["protocolVersion"], mcp_server.SUPPORTED_PROTOCOL_VERSIONS)
        self.assertNotEqual(response["result"]["protocolVersion"], "unsupported-x")

    def test_rejects_non_json_rpc_request(self) -> None:
        response = mcp_server.handle({"id": 7, "method": "ping"})
        self.assertEqual(response["error"]["code"], -32600)


if __name__ == "__main__":
    unittest.main()
