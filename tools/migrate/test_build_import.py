"""Tests for build_import.py on a synthetic legacy Coach root (no private data).

    python -m unittest discover -s tools/migrate -p "test_*.py"
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_import  # noqa: E402

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64

SETTINGS = {
    "settingsSchemaVersion": 5,
    "themeColor": "mint",
    "useReducedMotion": True,
    "garage61ApiKey": "never-copied",
    "coachThreadIds": {"race:1": "thread-1"},
    "liveMonitor": {
        "safeGlanceEnabled": False,
        "userLayouts": [{
            "id": "layout-1", "name": "Race", "rows": 2, "columns": 3,
            "tiles": [
                {"id": "t1", "metricId": "track-temperature", "row": 0, "column": 0, "rowSpan": 1, "columnSpan": 1,
                 "displayStyle": 3, "unit": "\u00b0F", "precision": 1, "trendDuration": 1, "accent": "default",
                 "highlightAbsIntervention": False},
                {"id": "t2", "metricId": "fuel-laps", "row": 0, "column": 1, "displayStyle": 0, "unit": "laps",
                 "precision": 5, "trendDuration": 4, "accent": "blue"},
                {"id": "t3", "metricId": "lateral-acceleration", "row": 0, "column": 2, "displayStyle": "Gauge",
                 "unit": "m/s\u00b2", "trendDuration": "OneLap", "accent": "teal", "highlightAbsIntervention": True},
                {"id": "t4", "metricId": "last-lap", "row": 1, "column": 0, "unit": "time"},
                {"id": "t5", "metricId": "air-temperature", "row": 1, "column": 1, "unit": "\u00b0C"},
                {"id": "t6", "metricId": "speed", "row": 1, "column": 2, "unit": ""},
                {"id": "t7", "metricId": "retired-metric", "row": 1, "column": 2},
            ],
        }],
    },
    "raceAnalysisTraceLayouts": {
        "activeLayoutId": "factory-default",
        "legacyLayoutImportCompleted": True,
        "userLayouts": [{"id": "analysis-layout-1", "name": "Inputs", "layout": {"rows": [
            {"id": "r1", "primarySignalId": "speed", "secondarySignalId": ""},
            {"id": "r2", "primarySignalId": "throttle", "secondarySignalId": "brake"},
        ]}}],
    },
    "raceAnalysisTraces": {"rows": [{"id": "old", "primarySignalId": "speed", "secondarySignalId": ""}]},
}

REPRESENTATIVE = {
    "analysisId": "legacy-analysis-1", "subsessionId": "71234567", "sessionId": "2",
    "sourceSha256": [SHA_C.upper()], "trackConfigurationKey": "irp|oval", "carPath": "stockcars2 supra2019",
}
DRAFT = {
    "schemaVersion": 2,
    "draftId": "d1",
    "representativeSession": REPRESENTATIVE,
    "openSetupTarget": {"targetId": "t", "baseline": {"analysisId": "legacy-analysis-9", "sourceSha256": []}},
    "representativeRunIds": ["1", "2", "3", "4"],
    "mapIdentity": "irp-v1",
    "rulesetId": "nascar-oreilly-xfinity-2026s3-v1",
    "goal": "tire-life",
    "generalNote": "Loose on exit after lap 20.",
    "feedback": [{
        "feedbackId": "f1", "cornerId": "t1", "cornerLabel": "Turn 1", "startPct": 0.1, "apexPct": None,
        "endPct": 0.2, "runPhase": "late", "cornerPhases": ["exit"], "symptomId": "loose-exit",
        "severity": 4, "driverConfidence": 3, "note": "Worse on the outside lane", "priority": 5,
    }],
    "turnCorrections": [],
    "updatedUtc": "2026-09-20T22:00:00+00:00",
}
ANNOTATIONS = {
    "schemaVersion": 1, "trackConfigurationKey": "irp|oval", "mapIdentity": "irp-v1",
    "corrections": [{"cornerId": "t1", "label": "Turn 1", "startPct": 0.1, "apexPct": 0.15, "endPct": 0.2,
                     "note": "Apex later", "geometryHash": "f" * 64, "mapIdentity": "irp-v1",
                     "verifiedAt": "2026-09-20T22:00:00+00:00"}],
    "updatedUtc": "2026-09-20T22:00:00+00:00",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def create_history(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (analysis_id TEXT PRIMARY KEY, subsession_id TEXT, sim_session_num TEXT,
                               source_path TEXT);
        CREATE TABLE tuning_experiments (experiment_id TEXT PRIMARY KEY, analysis_id TEXT);
        INSERT INTO sessions VALUES ('legacy-analysis-1', '71234567', '2', 'C:\\Users\\x\\Documents\\iRacing\\telemetry\\race.ibt');
        INSERT INTO sessions VALUES ('legacy-analysis-2', '0', NULL, 'C:\\Users\\x\\Documents\\iRacing\\telemetry\\test.ibt');
        INSERT INTO tuning_experiments VALUES ('e1', 'legacy-analysis-3');
        """
    )
    connection.commit()
    return connection


def make_root(base: Path) -> Path:
    root = base / "copy"
    write_json(root / "settings.json", SETTINGS)
    write_json(root / "portable-settings" / "tuning-drafts" / ("ABC" + "0" * 61 + ".json"), DRAFT)
    write_json(root / "portable-settings" / "tuning-turn-annotations" / ("1" * 64 + ".json"), ANNOTATIONS)
    create_history(root / "data" / "history.sqlite3").close()
    write_json(root / "data" / "tuning" / "experiments" / "s" / "c" / "t" / "e1.json",
               {"experiment_id": "e1", "analysis_id": "legacy-analysis-3",
                "feedback": {"result_analysis_id": "legacy-analysis-4"}})
    write_json(root / "data" / "tuning" / "inputs" / "irp-2026s3.json", {"banking": "low"})
    write_json(root / "data" / "telemetry-traces" / "raw" / SHA_A / "manifest.json", {
        "sha256": SHA_A, "original_path": "C:\\Users\\x\\Documents\\iRacing\\telemetry\\race.ibt",
        "source_discoveries": [{"source_path": "C:\\Users\\x\\Documents\\iRacing\\telemetry\\RACE.ibt"}],
    })
    write_json(root / "data" / "trials" / "trial-1.json", {"schema_version": 1, "trial_id": "trial-1"})
    write_json(root / "data" / "activity-history" / "a.json", {"schemaVersion": 1, "action": "x"})
    write_json(root / "data" / "event-plans" / "plan-1.json", [1, 2])
    (root / "data" / "strategy-history").mkdir(parents=True)
    (root / "data" / "strategy-history" / "readme.txt").write_text("not json", encoding="utf-8")
    write_json(root / "data" / "auth" / "auth.json", {"token": "never-read"})
    write_json(root / "data" / "test-artifacts" / "x" / "a.json", {})
    return root


class BuildImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path(tempfile.mkdtemp(prefix="coach-build-import-test-"))
        self.root = make_root(self.scratch)
        recordings = self.scratch / "recordings.json"
        write_json(recordings, {"files": [
            {"kind": "telemetry", "path": "test.ibt", "sha256": SHA_B},
            {"kind": "setup", "path": "x.sto", "sha256": "d" * 64},
        ]})
        self.bundle, self.summary = build_import.build(self.root, 123456, "DESKTOP-SIM", recordings)

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_live_layout_units_become_ids(self) -> None:
        (layout,) = self.bundle["live_layouts"]
        tiles = {tile["tile_id"]: tile for tile in layout["body"]["tiles"]}
        self.assertEqual(tiles["t1"]["unit"], "F")
        self.assertEqual(tiles["t2"]["unit"], "lap")
        self.assertEqual(tiles["t3"]["unit"], "m/s2")
        self.assertEqual(tiles["t5"]["unit"], "C")
        self.assertIsNone(tiles["t4"]["unit"])  # a lap-time tile lists no unit
        self.assertIsNone(tiles["t6"]["unit"])
        self.assertEqual(tiles["t1"]["style"], "trend")
        self.assertEqual(tiles["t1"]["trend_window"], "s30")
        self.assertEqual(tiles["t2"]["trend_window"], "three_laps")
        self.assertEqual(tiles["t2"]["precision"], 3)
        self.assertEqual((tiles["t3"]["style"], tiles["t3"]["trend_window"]), ("gauge", "one_lap"))
        self.assertIsNone(tiles["t3"]["accent"])
        self.assertTrue(tiles["t3"]["highlight_abs"])
        self.assertNotIn("t7", tiles)
        self.assertEqual(self.summary["dropped_tiles"],
                         [{"layout": "Race", "metric": "retired-metric", "reason": "not in the tile catalog"}])
        self.assertEqual(self.bundle["prefs"], {"theme_color": "mint", "reduced_motion": True, "safe_glance": False})

    def test_unit_ids_come_from_the_contract_table(self) -> None:
        units, label_to_id = build_import.tile_catalog()
        self.assertEqual(label_to_id, {"\u00b0C": "C", "\u00b0F": "F", "m/s\u00b2": "m/s2", "laps": "lap"})
        for unit in label_to_id.values():
            self.assertTrue(any(unit in listed for listed in units.values()), unit)

    def test_trace_layouts(self) -> None:
        self.assertEqual(self.bundle["trace_layouts"], [{"name": "Inputs", "body": {"rows": [
            {"row_id": "r1", "label": None, "signals": ["speed"]},
            {"row_id": "r2", "label": None, "signals": ["throttle", "brake"]},
        ]}}])

    def test_draft_maps_to_tuning_draft_body(self) -> None:
        (draft,) = self.bundle["tuning_drafts"]
        self.assertEqual(draft["identity_key"], "abc" + "0" * 61)
        self.assertEqual(draft["legacy_analysis_id"], "legacy-analysis-1")
        body = draft["body"]
        self.assertEqual(body["goal"], "tire-life")
        self.assertEqual(body["general_note"], "Loose on exit after lap 20.")
        self.assertEqual(body["representative_run_ids"], ["1", "2", "3"])
        self.assertEqual(body["open_target_analysis_id"], "legacy-analysis-9")
        self.assertEqual(body["feedback"], [{
            "feedback_id": "f1", "corner_id": "t1", "corner_label": "Turn 1", "start_pct": 0.1, "end_pct": 0.2,
            "run_phase": "late", "corner_phases": ["exit"], "symptom_id": "loose-exit", "severity": 4,
            "driver_confidence": 3, "note": "Worse on the outside lane", "priority": 5,
        }])
        self.assertEqual(body["legacy"]["representativeSession"], REPRESENTATIVE)
        self.assertEqual(body["legacy"]["rulesetId"], "nascar-oreilly-xfinity-2026s3-v1")
        for mapped in ("goal", "generalNote", "representativeRunIds", "feedback"):
            self.assertNotIn(mapped, body["legacy"])

    def test_drafts_name_a_listed_legacy_analysis(self) -> None:
        orphan = {key: value for key, value in DRAFT.items() if key != "representativeSession"}
        write_json(self.root / "portable-settings" / "tuning-drafts" / ("D" * 64 + ".json"), orphan)
        bundle, summary = build_import.build(self.root, 123456, "DESKTOP-SIM")
        drafts = {draft["identity_key"]: draft for draft in bundle["tuning_drafts"]}
        self.assertEqual(drafts["abc" + "0" * 61]["legacy_analysis_id"], "legacy-analysis-1")
        self.assertIsNone(drafts["d" * 64]["legacy_analysis_id"])
        listed = {entry["legacy_analysis_id"] for entry in bundle["legacy_analyses"]}
        for draft in bundle["tuning_drafts"]:
            self.assertIn("legacy_analysis_id", draft)
            if draft["legacy_analysis_id"] is not None:
                self.assertIn(draft["legacy_analysis_id"], listed)
        self.assertEqual(summary["tuning_drafts_without_analysis"], 1)
        self.assertEqual(self.summary["tuning_drafts_without_analysis"], 0)

    def test_turn_annotations(self) -> None:
        (annotation,) = self.bundle["turn_annotations"]
        self.assertEqual((annotation["track_config_key"], annotation["map_identity"]), ("irp|oval", "irp-v1"))
        corner = annotation["body"]["corners"][0]
        self.assertEqual((corner["corner_id"], corner["start_pct"], corner["note"]), ("t1", 0.1, "Apex later"))

    def test_legacy_analyses_resolve_recordings(self) -> None:
        analyses = {entry["legacy_analysis_id"]: entry for entry in self.bundle["legacy_analyses"]}
        self.assertEqual(sorted(analyses), [f"legacy-analysis-{n}" for n in (1, 2, 3, 4, 9)])
        self.assertEqual(analyses["legacy-analysis-1"],
                         {"legacy_analysis_id": "legacy-analysis-1", "recording_sha256": [SHA_A, SHA_C],
                          "subsession_id": 71234567, "sim_session_num": 2})
        self.assertEqual(analyses["legacy-analysis-2"]["recording_sha256"], [SHA_B])  # by file name
        self.assertIsNone(analyses["legacy-analysis-2"]["subsession_id"])  # 0 is no subsession
        self.assertEqual(analyses["legacy-analysis-4"]["recording_sha256"], [])

    def test_legacy_archive_replaces_tuning_experiments(self) -> None:
        self.assertNotIn("tuning_experiments", self.bundle)
        files = {item["relative_path"]: item for item in self.bundle["legacy_archive"]["files"]}
        self.assertEqual(sorted(files), ["history.sqlite3", "tuning/experiments/s/c/t/e1.json",
                                         "tuning/inputs/irp-2026s3.json"])
        for relative, item in files.items():
            content = base64.b64decode(item["content_base64"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
        history = self.root / "data" / "history.sqlite3"
        self.assertEqual(base64.b64decode(files["history.sqlite3"]["content_base64"]), history.read_bytes())
        self.assertFalse(self.summary["history_consolidated_from_wal"])

    def test_write_ahead_log_is_consolidated(self) -> None:
        source = self.scratch / "live" / "history.sqlite3"
        connection = create_history(source)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT INTO sessions VALUES ('in-the-log', '5', '0', NULL)")
        connection.commit()
        target = self.root / "data" / "history.sqlite3"
        shutil.copyfile(source.with_name("history.sqlite3-wal"), target.with_name("history.sqlite3-wal"))
        shutil.copyfile(source, target)
        connection.close()
        bundle, summary = build_import.build(self.root, 123456, "DESKTOP-SIM")
        self.assertTrue(summary["history_consolidated_from_wal"])
        content = next(item for item in bundle["legacy_archive"]["files"] if item["relative_path"] == "history.sqlite3")
        database = self.scratch / "consolidated.sqlite3"
        database.write_bytes(base64.b64decode(content["content_base64"]))
        check = sqlite3.connect(database)
        try:
            ids = {row[0] for row in check.execute("SELECT analysis_id FROM sessions")}
        finally:
            check.close()
        self.assertIn("in-the-log", ids)
        self.assertIn("in-the-log", {entry["legacy_analysis_id"] for entry in bundle["legacy_analyses"]})

    def test_legacy_items_and_secrets(self) -> None:
        items = {(item["kind"], item["source_path"]) for item in self.bundle["legacy_items"]}
        self.assertEqual(items, {("setting", "settings.json"), ("saved_test", "data/trials/trial-1.json"),
                                 ("activity", "data/activity-history/a.json"),
                                 ("plan", "data/event-plans/plan-1.json")})
        plan = next(item for item in self.bundle["legacy_items"] if item["kind"] == "plan")
        self.assertEqual(plan["body"], {"value": [1, 2]})
        text = json.dumps(self.bundle)
        self.assertNotIn("never-copied", text)
        self.assertNotIn("never-read", text)
        reasons = {entry["path"]: entry["reason"] for entry in self.summary["not_converted"]}
        self.assertEqual(reasons["data/strategy-history/readme.txt"], "not JSON")
        self.assertIn("data/test-artifacts", reasons)

    def test_main_is_deterministic_and_refuses_the_repository(self) -> None:
        outputs = []
        for name in ("one.json", "two.json"):
            out = self.scratch / name
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build_import.main([str(self.root), "--customer-id", "123456",
                                                    "--machine", "DESKTOP-SIM", "--out", str(out)]), 0)
            outputs.append(out.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(json.loads(outputs[0])["bundle_version"], 1)
        inside = build_import.REPOSITORY / "bundle.json"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_import.main([str(self.root), "--customer-id", "1", "--machine", "m", "--out", str(inside)])
        self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
