"""Removing older report copies: eligibility, re-verification and the move.

GAP-025 / PORT-007. A plan changes nothing; only a confirmed plan moves
anything, and only into removed-reports, never outside the reports tree.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report_reclaim  # noqa: E402
import storage  # noqa: E402
from storage import ArchiveStore  # noqa: E402


SECOND = 1_000_000_000
# Ten days ago: well outside the quiet period that protects fresh writes.
OLD = time.time_ns() - 10 * 86_400 * SECOND


def _analysis(
    identifier: str,
    *,
    subsession: int = 88628993,
    files: tuple[str, ...] = ("race.ibt",),
    sha256: str | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "telemetry_files": [f"C:\\iRacing\\telemetry\\{name}" for name in files],
        "selection": {
            "group_id": f"subsession:{subsession}:2",
            "subsession_id": subsession,
            "sim_session_num": 2,
            "sim_session_type": "Race",
        },
    }
    if sha256 is not None:
        source["fingerprints"] = [{"path": source["telemetry_files"][0], "sha256": sha256}]
    return {
        "analysis_id": identifier,
        "analyzed_at": "2026-09-21T00:00:00+00:00",
        "identity": {
            "session_start": "2026-09-20T23:00:00+00:00",
            "subsession_id": subsession,
            "session_id": 84,
            "season_year": 2026,
            "season_quarter": 3,
            "car_name": "Toyota Supra",
            "track_name": "Bristol",
            "is_fixed_setup": True,
        },
        "race_summary": {"scheduled_laps": 250, "recorded_laps": 250},
        "source": source,
        "runs": [{"run_number": 1, "green_laps": 10}],
    }


def _age(path: Path, modified_ns: int) -> None:
    for item in sorted(path.rglob("*"), reverse=True):
        os.utime(item, ns=(modified_ns, modified_ns))
    os.utime(path, ns=(modified_ns, modified_ns))


def _copy(store: ArchiveStore, session: str, leaf: str, analysis: dict[str, Any] | None, *,
          modified_ns: int, padding: int = 1000, raw_text: str | None = None) -> Path:
    directory = store.reports_dir / "season-6079" / session / leaf
    (directory / "visuals").mkdir(parents=True)
    (directory / "analysis.json").write_text(
        raw_text if raw_text is not None else storage._json_text(analysis), encoding="utf-8"
    )
    (directory / "report.md").write_text("#" * padding, encoding="utf-8")
    (directory / "race-card.md").write_text("card", encoding="utf-8")
    (directory / "visuals" / "lap-trend.svg").write_text("<svg/>", encoding="utf-8")
    _age(directory, modified_ns)
    return directory


def _bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _snapshot(root: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, tuple[bytes, int]]:
    result: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in exclude:
            continue
        if path.is_file():
            result[str(relative)] = (path.read_bytes(), path.stat().st_mtime_ns)
        else:
            result[str(relative)] = (b"<dir>", 0)
    return result


def _rel(store: ArchiveStore, directory: Path) -> str:
    return os.path.relpath(directory, store.reports_dir)


class _Archive:
    """One archive holding every kind of copy the plan must tell apart."""

    def __init__(self, root: Path) -> None:
        self.root = root
        store = self.store = ArchiveStore(root)
        # Bristol: four copies of one analysis written before reports were keyed
        # by analysis_id. The index names t2; a saved test names t0.
        bristol = _analysis("bristol-v21")
        self.t0 = _copy(store, "bristol", "t0", bristol, modified_ns=OLD - SECOND)
        self.t1 = _copy(store, "bristol", "t1", bristol, modified_ns=OLD, padding=4000)
        self.t2 = _copy(store, "bristol", "t2", bristol, modified_ns=OLD + SECOND)
        self.t3 = _copy(store, "bristol", "t3", bristol, modified_ns=OLD + 2 * SECOND)
        store.record_analysis(bristol, str(self.t2 / "report.md"))
        _age(self.t2, OLD + SECOND)
        # Iowa: the index names the current revision; older revisions remain.
        iowa = 77777777
        current = _analysis("iowa-v21", subsession=iowa, files=("iowa.ibt",), sha256="a" * 64)
        self.current = _copy(store, "iowa", "iowa-v21", current, modified_ns=OLD + 100 * SECOND)
        store.record_analysis(current, str(self.current / "report.md"))
        _age(self.current, OLD + 100 * SECOND)
        older = _analysis("iowa-v19", subsession=iowa, files=("iowa.ibt",))
        self.o1 = _copy(store, "iowa", "o1", older, modified_ns=OLD, padding=3000)
        self.o2 = _copy(store, "iowa", "o2", older, modified_ns=OLD + SECOND, padding=2000)
        self.by_tuning = _copy(store, "iowa", "v18", _analysis("iowa-v18", subsession=iowa, files=("iowa.ibt",)),
                               modified_ns=OLD)
        # Same session, but made from another recording: the latest analysis of it.
        self.other_recording = _copy(
            store, "iowa", "v17", _analysis("iowa-v17", subsession=iowa, files=("iowa-restart.ibt",)),
            modified_ns=OLD,
        )
        # Kansas: the only copy of its analysis, never indexed.
        self.kansas = _copy(store, "kansas", "k1", _analysis("kansas-v21", subsession=55555555),
                            modified_ns=OLD)
        self.unknown = _copy(store, "legacy", "unknown", None, modified_ns=OLD, raw_text="{}")

        (root / "trials").mkdir()
        (root / "trials" / "trial-1.json").write_text(
            json.dumps({"baseline": {"analysis_path": str(self.t0 / "analysis.json")}}), encoding="utf-8"
        )
        (store.tuning_dir / "experiments").mkdir(parents=True)
        (store.tuning_dir / "experiments" / "e1.json").write_text(
            json.dumps({"result_analysis_path": str(self.by_tuning / "analysis.json")}), encoding="utf-8"
        )
        raw = store.telemetry_traces_dir / "raw" / ("b" * 64)
        raw.mkdir(parents=True)
        (raw / "race.ibt").write_bytes(b"raw telemetry bytes")
        self.removable = [self.t1, self.o1, self.o2]

    def plan(self, **kwargs: Any) -> dict[str, Any]:
        return report_reclaim.plan_report_reclaim(self.store, **kwargs)

    def execute(self, plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return report_reclaim.execute_report_reclaim(
            self.store,
            plan_id=plan["plan_id"],
            directories=[item["directory"] for item in plan["remove"]["directories"]],
            **kwargs,
        )


class ReportReclaimPlanTests(unittest.TestCase):
    def test_the_plan_offers_only_older_copies_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            before = _snapshot(archive.root)

            plan = archive.plan()

            self.assertEqual(_snapshot(archive.root), before, "A plan creates, changes and deletes nothing.")
            self.assertFalse((archive.root / "removed-reports").exists())
            self.assertEqual(plan["contract"], "report-storage-reclaim-v1")
            self.assertEqual((plan["mode"], plan["status"], plan["read_only"]), ("plan", "ready", True))
            listed = {item["directory"] for item in plan["remove"]["directories"]}
            self.assertEqual(listed, {_rel(archive.store, item) for item in archive.removable})
            remove = plan["remove"]
            self.assertEqual(remove["count"], 3)
            self.assertEqual(remove["bytes"], sum(_bytes(item) for item in archive.removable))
            self.assertEqual((remove["duplicate_count"], remove["superseded_count"]), (1, 2))
            self.assertEqual(remove["duplicate_bytes"], _bytes(archive.t1))
            self.assertEqual(remove["superseded_bytes"], _bytes(archive.o1) + _bytes(archive.o2))
            self.assertEqual(remove["oldest_utc"], report_reclaim._utc(OLD))
            self.assertEqual(remove["newest_utc"], report_reclaim._utc(OLD + SECOND))
            kept_copy = {item["directory"]: item["kept_copy"] for item in remove["directories"]}
            self.assertEqual(kept_copy[_rel(archive.store, archive.t1)], _rel(archive.store, archive.t2))
            self.assertEqual(kept_copy[_rel(archive.store, archive.o1)], _rel(archive.store, archive.current))

            kept = plan["kept"]
            self.assertEqual(kept["count"], 8)
            self.assertEqual(kept["by_reason"], {
                "history-index": 1, "saved-test": 1, "tuning": 1, "open-in-app": 0,
                "latest-analysis": 2, "only-copy": 2, "recent": 0, "unverified": 1, "deferred": 0,
            })
            self.assertEqual(sum(kept["by_reason"].values()), kept["count"])
            self.assertEqual(plan["recovery"]["bytes"], 0)
            self.assertEqual(plan["plan_id"], report_reclaim._plan_id(listed))

    def test_every_kind_of_reference_keeps_its_copy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan(keep_paths=[str(archive.o2 / "analysis.json")])
            listed = {item["directory"] for item in plan["remove"]["directories"]}
            for kept in (archive.t0, archive.t2, archive.t3, archive.current, archive.by_tuning,
                         archive.other_recording, archive.kansas, archive.unknown, archive.o2):
                self.assertNotIn(_rel(archive.store, kept), listed)
            self.assertEqual(plan["kept"]["by_reason"]["open-in-app"], 1, "The copy the app has open stays.")

    def test_a_copy_written_moments_ago_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            (archive.o1 / "report.md").write_text("rewritten", encoding="utf-8")

            plan = archive.plan()

            self.assertNotIn(_rel(archive.store, archive.o1), {item["directory"] for item in plan["remove"]["directories"]})
            self.assertEqual(plan["kept"]["by_reason"]["recent"], 1)

    def test_a_new_revision_saved_but_not_yet_indexed_is_never_offered(self) -> None:
        # save_report_artifacts writes the report before record_analysis names
        # it. In that window the index still names the older revision, and the
        # new copy is not "superseded" by it: it is the recording's latest.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            old = _analysis("revision-old")
            old_dir = _copy(store, "bristol", "revision-old", old, modified_ns=OLD)
            store.record_analysis(old, str(old_dir / "report.md"))
            _age(old_dir, OLD)
            _copy(store, "bristol", "revision-new", _analysis("revision-new"), modified_ns=OLD + 50 * SECOND)

            plan = report_reclaim.plan_report_reclaim(store)

            self.assertEqual(plan["status"], "nothing")
            self.assertEqual(plan["remove"]["directories"], [])
            self.assertEqual(plan["kept"]["by_reason"]["latest-analysis"], 1)

    def test_a_directory_holding_a_link_is_never_offered(self) -> None:
        try:
            import _winapi
            create = _winapi.CreateJunction
        except (ImportError, AttributeError):
            self.skipTest("junctions need Windows")
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            outside = Path(folder) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("not a report", encoding="utf-8")
            create(str(outside), str(archive.o1 / "linked"))
            _age(archive.o1, OLD)

            plan = archive.plan()
            listed = {item["directory"] for item in plan["remove"]["directories"]}
            self.assertNotIn(_rel(archive.store, archive.o1), listed)
            result = archive.execute(plan)
            self.assertEqual(result["moved"]["count"], 2)
            self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "not a report")

    def test_nothing_reclaimable_offers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            only = _analysis("only")
            directory = _copy(store, "bristol", "only", only, modified_ns=OLD)
            store.record_analysis(only, str(directory / "report.md"))
            plan = report_reclaim.plan_report_reclaim(store)
            self.assertEqual((plan["status"], plan["remove"]["count"], plan["remove"]["bytes"]), ("nothing", 0, 0))
            self.assertIsNone(plan["remove"]["oldest_utc"])
            with self.assertRaises(report_reclaim.ReclaimRequestError):
                report_reclaim.execute_report_reclaim(store, plan_id=plan["plan_id"], directories=[])
            empty = report_reclaim.plan_report_reclaim(ArchiveStore(root / "absent"))
            self.assertEqual(empty["status"], "empty")
            self.assertFalse((root / "absent").exists())


class ReportReclaimExecuteTests(unittest.TestCase):
    def test_confirmed_plan_moves_exactly_its_copies_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            outside = _snapshot(archive.root, exclude=("reports", "removed-reports"))
            kept = {path: _snapshot(path) for path in (archive.t0, archive.t2, archive.t3, archive.current,
                                                        archive.by_tuning, archive.other_recording, archive.kansas)}
            moved_before = {item: _snapshot(item) for item in archive.removable}

            result = archive.execute(plan)

            self.assertEqual((result["mode"], result["status"]), ("execute", "complete"))
            self.assertEqual(result["plan_id"], plan["plan_id"], "The plan at execution equals the reviewed plan.")
            self.assertEqual(result["confirmed_plan_id"], plan["plan_id"])
            self.assertEqual(
                {item["directory"] for item in result["moved"]["directories"]},
                {item["directory"] for item in plan["remove"]["directories"]},
            )
            self.assertEqual(result["moved"]["bytes"], plan["remove"]["bytes"])
            self.assertEqual(result["skipped"]["count"], 0)
            batch = Path(result["batch_directory"])
            self.assertEqual(batch.parent, archive.root / "removed-reports")
            for original, contents in moved_before.items():
                self.assertFalse(original.exists())
                self.assertEqual(_snapshot(batch / _rel(archive.store, original)), contents, "Moved whole and unchanged.")
            for path, contents in kept.items():
                self.assertEqual(_snapshot(path), contents)
            self.assertEqual(_snapshot(archive.root, exclude=("reports", "removed-reports")), outside,
                             "Raw telemetry, the index, trials and tuning are untouched.")
            manifest = json.loads((batch / "removal.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(item["directory"] for item in manifest["moved"]),
                             sorted(_rel(archive.store, item) for item in archive.removable))
            for item in manifest["moved"]:
                self.assertTrue((batch / item["directory"] / "analysis.json").is_file(),
                                "Each moved folder keeps its path below reports, so it can be moved back.")
            self.assertEqual(result["recovery"]["bytes"], plan["remove"]["bytes"] + (batch / "removal.json").stat().st_size)

            after = archive.plan()
            self.assertEqual(after["status"], "nothing")
            self.assertEqual(after["kept"]["count"], plan["kept"]["count"])

    def test_a_second_confirmation_of_the_same_plan_moves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            first = archive.execute(plan)
            recovery = _snapshot(archive.root / "removed-reports")

            second = archive.execute(plan)

            self.assertEqual(first["moved"]["count"], 3)
            self.assertEqual((second["status"], second["moved"]["count"]), ("nothing", 0))
            self.assertEqual({item["reason"] for item in second["skipped"]["directories"]}, {"missing"})
            self.assertIsNone(second["batch_directory"])
            self.assertEqual(_snapshot(archive.root / "removed-reports"), recovery)

    def test_a_copy_the_index_names_after_review_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            # A re-analysis between review and confirmation now names t1.
            archive.store.record_analysis(_analysis("bristol-v21"), str(archive.t1 / "report.md"))

            result = archive.execute(plan)

            self.assertTrue(archive.t1.exists())
            self.assertIn({"directory": _rel(archive.store, archive.t1), "reason": "referenced"},
                          result["skipped"]["directories"])
            self.assertEqual(result["moved"]["count"], 2)
            self.assertEqual(result["status"], "partial")
            self.assertNotEqual(result["plan_id"], plan["plan_id"])

    def test_the_index_is_rechecked_immediately_before_each_move(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            original = report_reclaim._open_history_lock
            calls = []

            def index_changes_first(db_path):
                if not calls:
                    # Another backend process names t1 after the execution's own
                    # plan was rebuilt, before its move.
                    writer = sqlite3.connect(db_path)
                    writer.execute("UPDATE sessions SET report_path = ? WHERE analysis_id = ?",
                                   (str(archive.t1 / "report.md"), "bristol-v21"))
                    writer.commit()
                    writer.close()
                calls.append(db_path)
                return original(db_path)

            with mock.patch.object(report_reclaim, "_open_history_lock", side_effect=index_changes_first):
                result = archive.execute(plan)

            self.assertEqual(result["plan_id"], plan["plan_id"], "The rebuilt plan still offered t1.")
            self.assertTrue((archive.t1 / "analysis.json").is_file())
            self.assertIn({"directory": _rel(archive.store, archive.t1), "reason": "referenced"},
                          result["skipped"]["directories"])
            self.assertEqual(result["moved"]["count"], 2)
            self.assertEqual(len(calls), 3, "The index is held once per directory.")

    def test_a_superseding_analysis_replaced_after_review_stops_the_move(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            original = report_reclaim._open_history_lock

            def current_row_removed(db_path):
                writer = sqlite3.connect(db_path)
                writer.execute("DELETE FROM sessions WHERE analysis_id = ?", ("iowa-v21",))
                writer.commit()
                writer.close()
                return original(db_path)

            with mock.patch.object(report_reclaim, "_open_history_lock", side_effect=current_row_removed):
                result = archive.execute(plan)

            reasons = {item["directory"]: item["reason"] for item in result["skipped"]["directories"]}
            self.assertEqual(reasons, {_rel(archive.store, archive.o1): "changed", _rel(archive.store, archive.o2): "changed"})
            self.assertTrue(archive.o1.exists() and archive.o2.exists())

    def test_a_copy_changed_after_review_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            (archive.o2 / "report.md").write_text("rewritten", encoding="utf-8")

            result = archive.execute(plan)

            self.assertIn({"directory": _rel(archive.store, archive.o2), "reason": "changed"},
                          result["skipped"]["directories"])
            self.assertTrue((archive.o2 / "analysis.json").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows refuses to rename a directory holding an open file")
    def test_a_directory_with_an_open_file_is_skipped_whole(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            contents = _snapshot(archive.o1)
            opened, release = threading.Event(), threading.Event()

            def hold() -> None:
                with open(archive.o1 / "visuals" / "lap-trend.svg", "rb") as handle:
                    handle.read(1)
                    opened.set()
                    release.wait(30)

            reader = threading.Thread(target=hold, daemon=True)
            reader.start()
            self.assertTrue(opened.wait(10))
            try:
                result = archive.execute(plan)
            finally:
                release.set()
                reader.join(10)

            self.assertIn({"directory": _rel(archive.store, archive.o1), "reason": "in-use"},
                          result["skipped"]["directories"])
            self.assertEqual(_snapshot(archive.o1), contents, "Nothing of the locked copy moved.")
            self.assertEqual(result["moved"]["count"], 2)
            retry = archive.plan()
            self.assertEqual([item["directory"] for item in retry["remove"]["directories"]],
                             [_rel(archive.store, archive.o1)])
            self.assertEqual(archive.execute(retry)["moved"]["count"], 1)

    @unittest.skipUnless(os.name == "nt", "Win32 MAX_PATH")
    def test_copies_past_the_windows_path_limit_are_planned_and_moved(self) -> None:
        # GAP-020: with LongPathsEnabled=0 ordinary Win32 calls fail past 260
        # characters; every check and the rename use extended-length paths.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            self.addCleanup(shutil.rmtree, storage._extended_path(folder), True)
            store = ArchiveStore(Path(folder))
            same = _analysis("long-path-analysis")
            base = store.reports_dir / "season-6079" / ("s" * 120) / ("t" * 60)
            copies = []
            for leaf, modified in (("older", OLD), ("newer", OLD + SECOND)):
                directory = base / leaf
                os.makedirs(storage._extended_path(directory / "visuals"))
                with open(storage._extended_path(directory / "analysis.json"), "w", encoding="utf-8") as handle:
                    handle.write(storage._json_text(same))
                with open(storage._extended_path(directory / "report.md"), "w", encoding="utf-8") as handle:
                    handle.write("#" * 100)
                for item in (directory / "analysis.json", directory / "report.md", directory / "visuals", directory):
                    os.utime(storage._extended_path(item), ns=(modified, modified))
                copies.append(directory)
            self.assertGreater(len(str(copies[0] / "analysis.json")), 260)
            store.record_analysis(same, str(copies[1] / "report.md"))

            plan = report_reclaim.plan_report_reclaim(store)
            self.assertEqual([item["directory"] for item in plan["remove"]["directories"]], [_rel(store, copies[0])])
            result = report_reclaim.execute_report_reclaim(
                store, plan_id=plan["plan_id"], directories=[_rel(store, copies[0])]
            )

            self.assertEqual(result["moved"]["count"], 1)
            self.assertFalse(os.path.isdir(storage._extended_path(copies[0])))
            moved = Path(result["batch_directory"]) / _rel(store, copies[0]) / "analysis.json"
            self.assertTrue(os.path.isfile(storage._extended_path(moved)))
            self.assertTrue(os.path.isfile(storage._extended_path(copies[1] / "analysis.json")))

    def test_a_failed_move_leaves_the_copy_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()

            def refuse(source: str, destination: str) -> None:
                raise OSError(errno.EXDEV, "not the same device")

            result = archive.execute(plan, rename=refuse)

            self.assertEqual((result["status"], result["moved"]["count"]), ("nothing", 0))
            self.assertEqual({item["reason"] for item in result["skipped"]["directories"]}, {"move-failed"})
            for directory in archive.removable:
                self.assertTrue((directory / "analysis.json").is_file())
            self.assertFalse((archive.root / "removed-reports").exists(), "No empty recovery folder is left.")

    def test_a_confirmation_must_match_a_reviewed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            plan = archive.plan()
            directories = [item["directory"] for item in plan["remove"]["directories"]]
            before = _snapshot(archive.root)
            for plan_id, listed in (
                ("0" * 32, directories),
                (plan["plan_id"], directories[:1]),
                (report_reclaim._plan_id(["..\\trials"]), ["..\\trials"]),
                (report_reclaim._plan_id([str(archive.o1)]), [str(archive.o1)]),
                (report_reclaim._plan_id(["season-6079\\iowa\\o1:stream"]), ["season-6079\\iowa\\o1:stream"]),
                (plan["plan_id"], directories + directories[:1]),
            ):
                with self.subTest(listed=listed), self.assertRaises(report_reclaim.ReclaimRequestError):
                    report_reclaim.execute_report_reclaim(archive.store, plan_id=plan_id, directories=listed)
            self.assertEqual(_snapshot(archive.root), before)
            # A listed directory the backend never offered is not moved.
            listed = [_rel(archive.store, archive.current)]
            result = report_reclaim.execute_report_reclaim(
                archive.store, plan_id=report_reclaim._plan_id(listed), directories=listed
            )
            self.assertEqual(result["skipped"]["directories"], [{"directory": listed[0], "reason": "not-eligible"}])
            self.assertTrue(archive.current.exists())


class ReportReclaimToolTests(unittest.TestCase):
    def test_the_tool_is_a_write_tool_the_coach_never_reaches(self) -> None:
        import coach_tool_authority as authority
        import mcp_server

        tool = next(item for item in mcp_server.TOOLS if item["name"] == "reclaim_report_storage")
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(authority.TOOL_CAPABILITIES["reclaim_report_storage"], authority.CAPABILITY_WRITE)
        for workflow in authority.COACH_WORKFLOWS:
            self.assertFalse(authority.authorize(workflow, "reclaim_report_storage").allowed)
        host = (WORKSPACE / "companion-app" / "src" / "iRacingCoach.Coordinator" / "CompanionHostProfile.cs").read_text(encoding="utf-8")
        self.assertIn('"reclaim_report_storage"', host, "The isolated profile permits confined writes.")
        snapshot = json.loads((WORKSPACE / "contracts" / "mcp-tools.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["tools"], mcp_server.TOOLS)
        compatibility = json.loads((WORKSPACE / "contracts" / "compatibility.json").read_text(encoding="utf-8"))
        self.assertEqual(compatibility["backend"]["tool_count"], len(mcp_server.TOOLS))

    def test_the_tool_plans_then_moves_only_on_confirmation(self) -> None:
        import mcp_server

        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            with mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", archive.root):
                plan = mcp_server.call_tool("reclaim_report_storage", {
                    "keep_analysis_paths": [str(archive.o2 / "analysis.json")],
                })
                self.assertEqual(plan["remove"]["count"], 2)
                self.assertTrue(all(path.exists() for path in archive.removable))
                with self.assertRaisesRegex(ValueError, "must stay within"):
                    mcp_server.call_tool("reclaim_report_storage", {
                        "keep_analysis_paths": [str(Path(folder) / "analysis.json")],
                    })
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    mcp_server.call_tool("reclaim_report_storage", {"delete": True})
                with self.assertRaisesRegex(ValueError, "confirm"):
                    mcp_server.call_tool("reclaim_report_storage", {"confirm": "yes"})
                with self.assertRaisesRegex(ValueError, "only accepted with confirm"):
                    mcp_server.call_tool("reclaim_report_storage", {"plan_id": plan["plan_id"]})
                result = mcp_server.call_tool("reclaim_report_storage", {
                    "confirm": True,
                    "plan_id": plan["plan_id"],
                    "directories": [item["directory"] for item in plan["remove"]["directories"]],
                    "keep_analysis_paths": [str(archive.o2 / "analysis.json")],
                })
            self.assertEqual(result["moved"]["count"], 2)
            self.assertTrue(archive.o2.exists())
            self.assertFalse(archive.o1.exists())


def _load_tool_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReportReclaimContractTests(unittest.TestCase):
    def test_fixtures_are_current_and_every_result_validates(self) -> None:
        validation = _load_tool_module("contract_validation_for_reclaim", WORKSPACE / "tools" / "contract_validation.py")
        schema = json.loads((WORKSPACE / "contracts" / "report-storage-reclaim-v1.schema.json").read_text(encoding="utf-8"))
        plan_fixture = json.loads((WORKSPACE / "test-data" / "report-storage-reclaim-plan.json").read_text(encoding="utf-8"))
        result_fixture = json.loads((WORKSPACE / "test-data" / "report-storage-reclaim-result.json").read_text(encoding="utf-8"))
        generator = _load_tool_module("generate_fixtures_for_reclaim", WORKSPACE / "tools" / "generate_fixtures.py")
        plan, result = generator._report_reclaim_fixtures()
        self.assertEqual(json.loads(json.dumps(plan)), plan_fixture)
        self.assertEqual(json.loads(json.dumps(result)), result_fixture)
        with tempfile.TemporaryDirectory() as folder:
            archive = _Archive(Path(folder))
            outputs = [archive.plan()]
            outputs.append(archive.execute(outputs[0]))
            outputs.append(archive.execute(outputs[0]))
            outputs.append(archive.plan())
            outputs.append(report_reclaim.plan_report_reclaim(ArchiveStore(Path(folder) / "absent")))
        for output in (plan_fixture, result_fixture, *outputs):
            validation.assert_valid(json.loads(json.dumps(output)), schema, "reclaim result")
        broken = json.loads(json.dumps(plan_fixture))
        del broken["remove"]["directories"][0]["kept_copy"]
        with self.assertRaises(Exception):
            validation.assert_valid(broken, schema, "reclaim plan without kept_copy")
        broken = json.loads(json.dumps(result_fixture))
        broken["skipped"]["directories"] = [{"directory": "x", "reason": "deleted"}]
        with self.assertRaises(Exception):
            validation.assert_valid(broken, schema, "reclaim result with an unknown skip reason")


if __name__ == "__main__":
    unittest.main()
