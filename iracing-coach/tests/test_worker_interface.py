"""The rules coach-server relies on when it runs this backend as its worker.

`docs/08-rewrite/contracts/worker-interface.md`: section 3 (job roots made of
links, stable selectors) and section 5 (raw recordings archived outside the
worker, Garage61 token file, deterministic directory order).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import ibt_reader  # noqa: E402
import setup_catalog  # noqa: E402
import workflow  # noqa: E402
from test_ibt_reader import build_ibt  # noqa: E402

SYNTHETIC_RACE = WORKSPACE / "test-data" / "ibt" / "synthetic-race.ibt"


def _worker_corpus() -> Any:
    spec = importlib.util.spec_from_file_location(
        "worker_corpus_for_tests", WORKSPACE / "tools" / "parity" / "worker_corpus.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sessions(root: Path) -> list[dict[str, Any]]:
    return [item for item in ibt_reader.discover_sessions(root) if item["kind"] == "session"]


class StableSelectorTests(unittest.TestCase):
    """Job roots differ per job; a group key that names a path is not stable."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _job(self, job: str, source: Path) -> Path:
        telemetry = self.base / "jobs" / job / "telemetry"
        telemetry.mkdir(parents=True)
        shutil.copy2(source, telemetry / source.name)
        return telemetry.parent

    def test_a_group_with_server_ids_has_the_same_path_free_key_in_every_job_root(self) -> None:
        source = self.base / "spool" / "race.ibt"
        source.parent.mkdir()
        build_ibt(source, subsession_id=4100, session_id=4000, session_num=1)
        keys = [_sessions(self._job(job, source))[0]["group_id"] for job in ("1", "2")]
        self.assertEqual(keys, ["subsession:4100:1", "subsession:4100:1"])

    def test_an_offline_recording_with_a_start_time_keeps_a_path_free_key(self) -> None:
        source = self.base / "spool" / "offline.ibt"
        source.parent.mkdir()
        build_ibt(source, subsession_id=0, session_id=0)
        groups = [_sessions(self._job(job, source))[0] for job in ("1", "2")]
        self.assertEqual(groups[0]["group_id"], groups[1]["group_id"])
        self.assertEqual(groups[0]["time_source"], "disk_header")
        self.assertNotIn(str(self.base), groups[0]["group_id"])

    def test_a_recording_without_server_ids_or_start_time_is_keyed_by_its_path(self) -> None:
        source = self.base / "spool" / "unknown.ibt"
        source.parent.mkdir()
        build_ibt(source, base_epoch=0, subsession_id=0, session_id=0)
        roots = [self._job(job, source) for job in ("1", "2")]
        groups = [_sessions(root)[0] for root in roots]
        self.assertEqual([group["time_source"] for group in groups], ["mtime", "mtime"])
        self.assertEqual([group["file_count"] for group in groups], [1, 1])
        for root, group in zip(roots, groups):
            self.assertIn(str((root / "telemetry" / "unknown.ibt").resolve()), group["group_id"])
        self.assertNotEqual(groups[0]["group_id"], groups[1]["group_id"])
        # The server therefore stores local-file:<sha256> and selects the
        # recording by its path in the analysis job root.
        recording = roots[1] / "telemetry" / "unknown.ibt"
        selection = workflow._resolve_session_selection(str(recording), roots[1])
        self.assertEqual(selection["selector_type"], "file")
        self.assertEqual(selection["files"], [str(recording.resolve())])

    def test_a_two_file_reconnect_is_one_group_selected_by_its_key(self) -> None:
        root = self.base / "jobs" / "analysis"
        telemetry = root / "telemetry"
        telemetry.mkdir(parents=True)
        build_ibt(telemetry / "b-rejoin.ibt", start_offset=60.0, subsession_id=4200, session_id=4000)
        build_ibt(telemetry / "a-start.ibt", start_offset=0.0, subsession_id=4200, session_id=4000)
        groups = _sessions(root)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["group_id"], "subsession:4200:1")
        self.assertEqual(groups[0]["file_count"], 2)
        selection = workflow._resolve_session_selection("subsession:4200:1", root)
        self.assertEqual(selection["selector_type"], "group_id")
        self.assertEqual(
            [Path(path).name for path in selection["files"]], ["a-start.ibt", "b-rejoin.ibt"]
        )


class DirectoryOrderTests(unittest.TestCase):
    """Names equal but for case sort the same on every file system (NTFS readdir is sorted, ext4 is not)."""

    def test_recordings_are_walked_in_one_order_whatever_the_readdir_order(self) -> None:
        orders = (["b.ibt", "B.ibt", "a.ibt"], ["a.ibt", "B.ibt", "b.ibt"], ["B.ibt", "a.ibt", "b.ibt"])
        walked = []
        for order in orders:
            with mock.patch.object(ibt_reader.os, "walk", return_value=[("root", ["Y", "y"], list(order))]):
                walked.append([path.name for path in ibt_reader._iter_ibt_paths(Path("root-directory"))])
        self.assertEqual(walked, [["a.ibt", "B.ibt", "b.ibt"]] * 3)

    def test_setup_files_differing_only_in_case_are_catalogued_in_one_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Probe").write_text("", encoding="utf-8")
            if (root / "probe").exists():
                self.skipTest("case-insensitive file system")
            car = root / "supra"
            car.mkdir()
            for name in ("race.sto", "Race.sto"):
                (car / name).write_bytes(b"not decoded")
            listed = []
            real_walk = os.walk
            for reverse in (False, True):
                def walk(top: Any, followlinks: bool = False, reverse: bool = reverse) -> Any:
                    for directory, names, files in real_walk(top, followlinks=followlinks):
                        files.sort(reverse=reverse)
                        yield directory, names, files

                with mock.patch.object(setup_catalog.os, "walk", walk):
                    catalog = setup_catalog.catalog_setups(root)
                listed.append([
                    (entry["stem"], [Path(source["path"]).name for source in entry["sources"]["sto"]])
                    for entry in catalog["entries"]
                ])
            self.assertEqual(listed[0], listed[1])
            self.assertEqual(listed[0], [("Race", ["Race.sto", "race.sto"])])


class RawArchiveSwitchTests(unittest.TestCase):
    """IRACING_COACH_RAW_ARCHIVE=external: raw recordings live on the NAS, never in the archive."""

    def _analyze(self, base: Path, name: str, environ: dict[str, str]) -> dict[str, Any]:
        root = base / name / "iracing"
        telemetry = root / "telemetry"
        telemetry.mkdir(parents=True)
        recording = telemetry / SYNTHETIC_RACE.name
        shutil.copy2(SYNTHETIC_RACE, recording)
        os.utime(recording, (1_780_000_000, 1_780_000_000))
        archive = base / name / "archive"
        with mock.patch.dict(os.environ, environ):
            result = workflow.analyze_race_workflow(
                selector=str(recording), iracing_root=str(root), archive_root=str(archive)
            )
        result["_archive"] = archive
        return result

    def test_external_skips_the_raw_copy_and_records_the_policy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            copied = self._analyze(base, "copy", {"IRACING_COACH_RAW_ARCHIVE": ""})
            external = self._analyze(base, "external", {"IRACING_COACH_RAW_ARCHIVE": "external"})

            self.assertEqual(copied["raw_archive"]["status"], "verified")
            self.assertEqual(external["raw_archive"]["status"], "external")
            raw = external["_archive"] / "telemetry-traces" / "raw"
            self.assertEqual([path for path in raw.rglob("*") if path.is_file()], [])
            analysis = json.loads(Path(external["analysis_path"]).read_text(encoding="utf-8"))
            policy = analysis["source"]["raw_source_policy"]
            self.assertEqual(policy["mode"], "external")
            self.assertFalse(policy["durably_copied"])
            self.assertEqual(policy["archive"]["items"], [])
            # The switch changes where raw files live, never what was analysed.
            self.assertEqual(copied["analysis_id"], external["analysis_id"])

    def test_an_unknown_switch_value_fails_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(workflow.WorkflowError, "IRACING_COACH_RAW_ARCHIVE"):
                self._analyze(Path(folder), "bad", {"IRACING_COACH_RAW_ARCHIVE": "nas"})
            self.assertFalse((Path(folder) / "bad" / "archive" / "reports").exists())


class CompatibilityContractTests(unittest.TestCase):
    """`contracts/compatibility.json` describes the rewrite, not the retired desktop app."""

    def setUp(self) -> None:
        self.compatibility = json.loads(
            (WORKSPACE / "contracts" / "compatibility.json").read_text(encoding="utf-8")
        )

    def test_the_worker_python_is_the_interpreter_the_server_launches(self) -> None:
        contracts = WORKSPACE / "docs" / "08-rewrite" / "contracts"
        interface = (contracts / "worker-interface.md").read_text(encoding="utf-8")
        config = (contracts / "server-config.md").read_text(encoding="utf-8")
        launched = re.search(r"\bpython(\d+\.\d+) -X utf8 -u /app/iracing-coach/", interface)
        configured = re.search(r"\| `COACH_PYTHON` \| `/usr/local/bin/python(\d+\.\d+)`", config)
        self.assertIsNotNone(launched)
        self.assertIsNotNone(configured)
        runtime = self.compatibility["runtime"]
        self.assertEqual(runtime["worker"]["python"], launched.group(1))
        self.assertEqual(runtime["worker"]["python"], configured.group(1))
        self.assertEqual(runtime["oracle"]["python"], runtime["worker"]["python"])
        self.assertEqual(runtime["python_runtime_dependencies"], [])

    def test_the_ui_is_the_web_app_and_names_no_retired_host(self) -> None:
        ui = self.compatibility["ui"]
        theme = json.loads((WORKSPACE / "config" / "theme.dark.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["application_language"], "TypeScript")
        self.assertEqual(ui["default_theme"], theme["name"])
        self.assertEqual(
            sorted(self.compatibility["application"]), ["collector", "server", "web", "worker"]
        )
        text = json.dumps(self.compatibility)
        for retired in ("WPF", "Blazor", ".NET 10", "native_host", "webview_runtime_policy"):
            self.assertNotIn(retired, text)


class WorkerProcessTests(unittest.TestCase):
    """mcp_server.py driven exactly as worker-interface.md sections 1-4 drive it."""

    def _call(self, job_root: Path, archive: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        environ = {
            "IRACING_COACH_IRACING_ROOT": str(job_root),
            "IRACING_COACH_DATA": str(archive),
            "IRACING_COACH_RAW_ARCHIVE": "external",
            "IRACING_COACH_NATIVE_STAGES": "",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "HOME": str(archive.parent / "tmp"),
            "TMPDIR": str(archive.parent / "tmp"),
            "PATH": os.environ.get("PATH", ""),
        }
        if os.name == "nt":  # Windows spells the interpreter's needs and the home differently
            environ.update({key: os.environ[key] for key in ("SYSTEMROOT", "TEMP", "TMP") if key in os.environ})
            environ["USERPROFILE"] = environ["HOME"]
        (archive.parent / "tmp").mkdir(parents=True, exist_ok=True)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "coach-server", "version": "test"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
        ]
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-u", str(SCRIPTS / "mcp_server.py")],
            input="".join(json.dumps(item) + "\n" for item in requests).encode("utf-8"),
            capture_output=True,
            env=environ,
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        responses = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines() if line.strip()]
        result = next(item for item in responses if item.get("id") == 2)["result"]
        self.assertFalse(result.get("isError"), result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def test_discovery_then_analysis_of_a_reconnect_and_a_local_file(self) -> None:
        corpus = _worker_corpus()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / "py" / "1" / "archive"
            archive.mkdir(parents=True)
            for case, expected_files in (("reconnect", 2), ("local-file", 1)):
                spool = base / "spool" / case
                recordings = corpus.build_case(case, spool)
                job_root = base / "py" / "1" / "jobs" / f"analysis-{case}"
                (job_root / "telemetry").mkdir(parents=True)
                for recording in recordings:
                    os.link(recording, job_root / "telemetry" / recording.name)
                discovered = self._call(job_root, archive, "discover_iracing_sessions", {
                    "root": str(job_root), "races_only": False, "limit": 50})
                self.assertEqual(discovered["session_count"], 1)
                group = discovered["sessions"][0]
                self.assertEqual(group["file_count"], expected_files)
                selector = (
                    str(job_root / "telemetry" / recordings[0].name)
                    if group["time_source"] == "mtime"
                    else group["group_id"]
                )
                self.assertEqual(group["time_source"] == "mtime", case == "local-file")
                result = self._call(job_root, archive, "analyze_iracing_race", {
                    "selector": selector, "iracing_root": str(job_root),
                    "archive_root": str(archive), "target_hz": None})
                self.assertTrue(result["ok"])
                self.assertEqual(len(result["source_files"]), expected_files)
                self.assertEqual(result["raw_archive"]["status"], "external")
                self.assertTrue(Path(result["analysis_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
