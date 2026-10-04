"""Stints of one offline sim session are analysed as one session.

iRacing writes a new file each time the driver gets back in the car; discovery
keys an offline file by its own start, so one Test Drive is one discovery group
per stint. coach-server joins the stints into one library session
(``server/src/offline_sessions.rs``: same driver, car, track and configuration,
session number, SessionUniqueID and type, session start within 2 s, no overlap,
the same setup, and a lap counter that still counts) and runs
``analyze_iracing_race`` with one stint's group ID. The worker answers with
every stint of that session (worker-interface.md section 3).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import ibt_reader  # noqa: E402
import workflow  # noqa: E402
import offline_stints_support  # noqa: E402
import test_worker_interface  # noqa: E402
from test_ibt_reader import build_ibt  # noqa: E402

#: build_ibt writes 6 samples at 60 Hz: a stint lasts 5/60 s of session time.
_BASE = 1_780_000_000


def _sessions(root: Path) -> list[dict[str, Any]]:
    return [item for item in ibt_reader.discover_sessions(root) if item["kind"] == "session"]


def _stint(root: Path, name: str, *, date: int, clock: float, **overrides: Any) -> Path:
    path = root / "telemetry" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    options = {"subsession_id": 0, "session_id": 0, "session_num": 0}
    options.update(overrides)
    build_ibt(path, base_epoch=date, start_offset=clock, **options)
    os.utime(path, (_BASE - 600, _BASE - 600))
    return path


class OfflineSessionRuleTests(unittest.TestCase):
    """The server's rule, file for file."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _select(self, path: Path) -> dict[str, Any]:
        group = next(item for item in _sessions(self.root) if str(path.resolve()) in item["files"])
        return workflow._resolve_session_selection(group["group_id"], self.root)

    def test_two_stints_of_one_session_are_one_selection_whichever_stint_names_it(self) -> None:
        first = _stint(self.root, "a.ibt", date=_BASE, clock=10.0)
        second = _stint(self.root, "b.ibt", date=_BASE + 100, clock=110.4)
        groups = _sessions(self.root)
        self.assertEqual(len(groups), 2, "discovery keeps one group per stint")
        by_first, by_second = self._select(first), self._select(second)
        self.assertEqual(by_first, {**by_second, "selector": by_first["selector"]})
        self.assertEqual(by_first["files"], [str(first.resolve()), str(second.resolve())])
        self.assertEqual(by_first["file_count"], 2)
        first_key = next(item["group_id"] for item in groups if item["files"] == [str(first.resolve())])
        second_key = next(item["group_id"] for item in groups if item["files"] == [str(second.resolve())])
        self.assertEqual(by_second["group_id"], first_key, "the first stint names the session")
        self.assertEqual(by_second["joined_group_ids"], [first_key, second_key])
        self.assertEqual(by_second["selector_type"], "group_id")
        self.assertEqual(by_second["start_time_unix"], float(_BASE))
        self.assertAlmostEqual(by_second["end_time_unix"], _BASE + 100 + 5 / 60)
        self.assertEqual(by_second["start_time_utc"], ibt_reader._safe_iso(float(_BASE)))
        self.assertFalse([key for key in by_second if key.startswith("test_day_")])
        # The analysis identity does not depend on which stint the server stored first.
        self.assertEqual(
            workflow._selection_archive_identity(by_first), workflow._selection_archive_identity(by_second)
        )

    def test_a_lone_offline_stint_selects_exactly_its_discovery_group(self) -> None:
        only = _stint(self.root, "a.ibt", date=_BASE, clock=10.0)
        group = _sessions(self.root)[0]
        selection = self._select(only)
        self.assertEqual(selection, {**group, "selector_type": "group_id", "selector": group["group_id"]})
        self.assertNotIn("joined_group_ids", selection)

    def test_a_session_start_more_than_two_seconds_apart_is_another_session(self) -> None:
        first = _stint(self.root, "a.ibt", date=_BASE, clock=10.0)
        _stint(self.root, "b.ibt", date=_BASE + 100, clock=107.5)  # starts 2.5 s apart
        self.assertEqual(self._select(first)["files"], [str(first.resolve())])

    def test_the_tolerance_is_two_seconds_as_the_server_has_it(self) -> None:
        self.assertEqual(workflow.OFFLINE_SESSION_START_TOLERANCE_S, 2.0)
        self.assertEqual(workflow.OFFLINE_STINT_OVERLAP_TOLERANCE_S, 1.0)
        first = _stint(self.root, "a.ibt", date=_BASE, clock=10.0)
        _stint(self.root, "b.ibt", date=_BASE + 100, clock=108.1)  # 1.9 s apart
        self.assertEqual(self._select(first)["file_count"], 2)

    def test_overlapping_recordings_are_not_stints_of_one_session(self) -> None:
        first = _stint(self.root, "a.ibt", date=_BASE, clock=10.0, record_count=600)  # 10 s long
        _stint(self.root, "b.ibt", date=_BASE + 5, clock=15.0)
        self.assertEqual(self._select(first)["files"], [str(first.resolve())])

    def test_another_session_number_unique_id_or_session_type_is_another_session(self) -> None:
        for name, overrides in (
            ("unique", {"session_unique_id": 9002}),
            ("number", {"session_num": 1}),  # SessionNum 1 is the Race of the fixture's session info
        ):
            with self.subTest(name):
                root = self.root / name
                first = _stint(root, "a.ibt", date=_BASE, clock=10.0)
                _stint(root, "b.ibt", date=_BASE + 100, clock=110.0, **overrides)
                group = next(item for item in _sessions(root) if item["files"] == [str(first.resolve())])
                selection = workflow._resolve_session_selection(group["group_id"], root)
                self.assertEqual(selection["files"], [str(first.resolve())])

    def test_hosted_and_file_timed_recordings_are_never_joined(self) -> None:
        hosted = self.root / "hosted"
        first = _stint(hosted, "a.ibt", date=_BASE, clock=10.0, session_id=4000)
        _stint(hosted, "b.ibt", date=_BASE + 100, clock=110.0, session_id=4000)
        # A server session keys both files together already (one group, two files).
        self.assertEqual([item["file_count"] for item in _sessions(hosted)], [2])
        timed = self.root / "timed"
        _stint(timed, "a.ibt", date=0, clock=10.0)
        _stint(timed, "b.ibt", date=0, clock=110.0)
        # A path-keyed group is selected by its file (local-file:<sha256> on the server).
        groups = _sessions(timed)
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(group["time_source"], "mtime")
            self.assertIsNone(workflow._offline_stint(ibt_reader.scan_ibt(group["files"][0])))
            self.assertEqual(workflow._offline_session_groups(group, groups), [group])
        self.assertTrue(first.is_file())

    def test_an_unreadable_sibling_leaves_the_selection_as_discovered(self) -> None:
        first = _stint(self.root, "a.ibt", date=_BASE, clock=10.0)
        _stint(self.root, "b.ibt", date=_BASE + 100, clock=110.4)
        real_scan = workflow.scan_ibt

        def failing(path: Any) -> dict[str, Any]:
            if Path(path).name == "b.ibt":
                raise ibt_reader.IbtError("unreadable")
            return real_scan(path)

        group = next(item for item in _sessions(self.root) if item["files"] == [str(first.resolve())])
        with mock.patch.object(workflow, "scan_ibt", failing):
            selection = workflow._resolve_session_selection(group["group_id"], self.root)
        self.assertEqual(selection["files"], [str(first.resolve())])


class OfflineStintsAnalysisTests(unittest.TestCase):
    """The synthetic two-stint Test Drive analysed through the worker as coach-server runs it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.spool = cls.base / "spool"
        cls.recordings = offline_stints_support.build_offline_stints(cls.spool)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_the_fixture_is_one_sim_session_in_two_files(self) -> None:
        stints = [workflow._offline_stint(ibt_reader.scan_ibt(path)) for path in self.recordings]
        self.assertTrue(all(stints))
        self.assertTrue(workflow._same_offline_session(stints[0], stints[1]))
        self.assertLess(
            abs(stints[0]["session_zero"] - offline_stints_support.SESSION_ZERO), 1.0
        )
        self.assertNotEqual(*(ibt_reader.scan_ibt(path)["disk"]["session_start_date"] for path in self.recordings))

    def test_the_fixture_bytes_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            again = offline_stints_support.build_offline_stints(Path(folder))
            self.assertEqual(
                [path.read_bytes() for path in again], [path.read_bytes() for path in self.recordings]
            )

    def test_a_joined_session_is_analysed_with_the_laps_of_both_stints(self) -> None:
        worker = test_worker_interface.WorkerProcessTests("run")
        archive = self.base / "py" / "1" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        job_root = self.base / "py" / "1" / "jobs" / "analysis"
        (job_root / "telemetry").mkdir(parents=True)
        for recording in self.recordings:
            os.link(recording, job_root / "telemetry" / recording.name)
        discovered = worker._call(
            job_root, archive, "discover_iracing_sessions", {"root": str(job_root), "races_only": False, "limit": 50}
        )
        keys = {
            Path(group["files"][0]).name: group["group_id"] for group in discovered["sessions"]
        }
        self.assertEqual(len(keys), 2, "discovery keeps one group per stint")
        results = []
        # The server stores the key of whichever stint it saw first: try the second stint's first.
        for recording in reversed(self.recordings):
            results.append(
                worker._call(job_root, archive, "analyze_iracing_race", {
                    "selector": keys[recording.name], "iracing_root": str(job_root),
                    "archive_root": str(archive), "target_hz": None})
            )
        first, second = results
        self.assertEqual(first["analysis_id"], second["analysis_id"])
        self.assertTrue(second["analysis_cache"]["hit"])
        self.assertEqual(
            [Path(path).name for path in first["source_files"]], [path.name for path in self.recordings]
        )
        self.assertEqual(first["selection"]["group_id"], keys[self.recordings[0].name])
        self.assertEqual(
            first["selection"]["joined_group_ids"], [keys[path.name] for path in self.recordings]
        )
        view_laps = {lap["lap"]: lap for lap in first["analysis_view"]["laps"]}
        self.assertEqual(sorted(view_laps), list(range(0, 11)))
        stint_1_end = max(lap["end_time"] for lap in view_laps.values() if lap["lap"] <= 4)
        stint_2_start = min(lap["start_time"] for lap in view_laps.values() if lap["lap"] >= 6)
        self.assertGreater(stint_2_start - stint_1_end, offline_stints_support.GARAGE_GAP_S)
        summary = first["race_summary"]
        self.assertEqual(summary["runs_detected"], 2)
        runs = [(run["start_lap"], run["end_lap"]) for run in first["analysis_view"]["runs"]]
        self.assertEqual(runs, [(1, 4), (6, 9)])
        self.assertIsNotNone(summary["best_clean_lap_s"])

    def test_one_stint_alone_is_analysed_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "telemetry").mkdir()
            shutil.copy2(self.recordings[0], root / "telemetry" / self.recordings[0].name)
            group = _sessions(root)[0]
            selection = workflow._resolve_session_selection(group["group_id"], root)
            self.assertEqual(selection, {**group, "selector_type": "group_id", "selector": group["group_id"]})


class SetupIdentityTests(unittest.TestCase):
    """The setup fingerprint the server stores for a recording (``coach_ibt``)."""

    def test_the_fingerprint_is_the_servers(self) -> None:
        # coach_ibt gate.rs fingerprint_hashes_the_kept_pairs_in_key_order
        values = {
            "Tires.LeftFront.ColdPressure": " 25.0 psi ",
            "Chassis.LeftFront.CornerWeight": "2,431 N",
            "Chassis.LeftFront.Camber": "-2.8 deg",
            "UpdateCount": "3",
        }
        self.assertEqual(workflow.setup_values_fingerprint(values), "3e1c4e820482b9ce36769799d5db81e8")
        self.assertEqual(workflow.setup_values_fingerprint({}), "e3b0c44298fc1c149afbf4c8996fb924")

    def test_the_exclusions_are_the_contracts(self) -> None:
        contract = PLUGIN_ROOT.parent / "docs" / "08-rewrite" / "contracts" / "setup-keys.json"
        if not contract.is_file():
            self.skipTest("the contracts are not beside this package")
        patterns = [item["pattern"] for item in json.loads(contract.read_text(encoding="utf-8"))["excluded_leaf_patterns"]]
        self.assertEqual(list(workflow.SETUP_IDENTITY_EXCLUDED_LEAF_PATTERNS), patterns)
        # coach_ibt gate.rs: last segment, case folded, '_' ignored
        for key in ("Chassis.LeftFront.CornerWeight", "chassis.left_front.corner_weight", "Chassis.FrontArb.Preload",
                    "Chassis.RightRear.TruckArmPreload", "Tires.RightRear.LastHotPressure", "UpdateCount"):
            self.assertTrue(workflow.setup_identity_excluded(key), key)
        for key in ("Chassis.Front.ArbPreloadOffset", "Chassis.LeftFront.CornerWeightBias", "CornerWeight.Camber",
                    "Tires.LeftFront.ColdPressure", "Chassis.Rear.DiffPreload"):
            self.assertFalse(workflow.setup_identity_excluded(key), key)

    def test_values_are_the_sims_text_not_parsed_numbers(self) -> None:
        # coach_ibt extract.rs extracts_follow_python_conversions, without its flow list
        text = (
            "WeekendInfo:\n TrackName: irp\nCarSetup:\n UpdateCount: 3\n Tires:\n  LeftFront:\n"
            "   ColdPressure: 25.0 psi\n   Quoted: \"1.50\"\n   Missing: ~\n Chassis:\n  Front:\n"
            "   ArbBlades: 1.50\n  RightRear:\n   TravelToCoilBind: OFF\n   ArmAsymmetry: None\n"
            "DriverInfo:\n DriverCarIdx: 1\n"
        )
        self.assertEqual(
            workflow._raw_car_setup_values(text),
            {
                "UpdateCount": "3",
                "Tires.LeftFront.ColdPressure": "25.0 psi",
                "Tires.LeftFront.Quoted": "1.50",
                "Chassis.Front.ArbBlades": "1.50",
                "Chassis.RightRear.TravelToCoilBind": "OFF",
                "Chassis.RightRear.ArmAsymmetry": "None",
            },
        )
        self.assertIsNone(workflow._raw_car_setup_values("WeekendInfo:\n TrackName: irp\n"))
        self.assertIsNone(workflow._raw_car_setup_values("CarSetup:\nDriverInfo:\n DriverCarIdx: 1\n"))
        with self.assertRaises(workflow._UnreadableSetup):
            workflow._raw_car_setup_values("CarSetup:\n Tires:\n  Flow: [1, 'x', None]\n")


class OfflineSetupRuleTests(unittest.TestCase):
    """A setup change between stints starts another session; a stopped lap counter joins nothing."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, **options: Any) -> list[Path]:
        return offline_stints_support.build_offline_stints(self.root / "telemetry", **options)

    def _select(self, path: Path) -> dict[str, Any]:
        group = next(item for item in _sessions(self.root) if str(path.resolve()) in item["files"])
        return workflow._resolve_session_selection(group["group_id"], self.root)

    def test_a_setup_change_between_stints_starts_another_session(self) -> None:
        first, second = self._build(
            second_session_info=offline_stints_support.session_info_with(lf_cold="152.0 kPa", update_count=4)
        )
        stints = [workflow._offline_stint(ibt_reader.scan_ibt(path)) for path in (first, second)]
        self.assertTrue(all(stints))
        self.assertEqual(stints[0]["key"][:-1], stints[1]["key"][:-1], "one sim session")
        self.assertNotEqual(stints[0]["key"][-1], stints[1]["key"][-1], "two setups")
        self.assertFalse(workflow._same_offline_session(stints[0], stints[1]))
        self.assertEqual(self._select(first)["files"], [str(first.resolve())])
        self.assertEqual(self._select(second)["files"], [str(second.resolve())])
        self.assertNotIn("joined_group_ids", self._select(second))

    def test_derived_values_and_the_update_count_are_no_setup_change(self) -> None:
        first, second = self._build(
            second_session_info=offline_stints_support.session_info_with(update_count=9, corner_weight="4012 N")
        )
        self.assertEqual(self._select(second)["files"], [str(first.resolve()), str(second.resolve())])

    def test_a_stint_after_the_lap_counter_stopped_is_neither_joined_nor_analysed(self) -> None:
        # The first stint ends on lap 1020, the second on 1025: the sim's counter stopped at 1024.
        first, second = self._build(first_lap=1015)
        first_meta, second_meta = ibt_reader.scan_ibt(first), ibt_reader.scan_ibt(second)
        self.assertLess(workflow._last_lap(first_meta), workflow.OFFLINE_LAP_COUNTER_LIMIT)
        self.assertGreaterEqual(workflow._last_lap(second_meta), workflow.OFFLINE_LAP_COUNTER_LIMIT)
        self.assertIsNotNone(workflow._offline_stint(first_meta))
        self.assertIsNone(workflow._offline_stint(second_meta))
        self.assertEqual(self._select(first)["files"], [str(first.resolve())])
        self.assertEqual(self._select(second)["files"], [str(second.resolve())])
        group = next(item for item in _sessions(self.root) if item["files"] == [str(second.resolve())])
        archive = self.root / "archive"
        with self.assertRaises(workflow.LapCounterStoppedError) as raised:
            workflow.analyze_race_workflow(
                selector=group["group_id"], iracing_root=self.root, archive_root=archive, target_hz=None
            )
        self.assertIn("lap counter had stopped at lap 1024", str(raised.exception))
        self.assertIn(second.name, str(raised.exception))
        self.assertFalse((archive / "analysis-cache").exists(), "nothing written")

    def test_the_defaults_still_write_the_fixture_the_module_describes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plain = offline_stints_support.build_offline_stints(Path(folder))
            same = self._build(second_session_info=offline_stints_support.session_info_with())
            self.assertEqual([path.read_bytes() for path in plain], [path.read_bytes() for path in same])
            self.assertEqual(workflow._last_lap(ibt_reader.scan_ibt(plain[1])), 10)


if __name__ == "__main__":
    unittest.main()
