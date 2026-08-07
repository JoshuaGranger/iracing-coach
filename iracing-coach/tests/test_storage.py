from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from storage import ArchiveStore  # noqa: E402


CONTEXT = {
    "season_key": "2026s3",
    "car_key": "123-nascar-truck",
    "track_key": "63-synthetic-speedway-oval",
    "setup_type": "fixed",
    "race_length_key": "40-laps",
}


class StorageCacheIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ArchiveStore(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_complete(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "sources": [
                {
                    "url": "https://example.test/manual",
                    "title": "Official manual",
                }
            ],
            "facts": {"track": "Synthetic Speedway"},
        }
        values.update(overrides)
        return self.store.write_knowledge_bundle(CONTEXT, **values)

    def manifest_path(self) -> Path:
        return self.store.cache_path(CONTEXT) / "manifest.json"

    def test_complete_bundle_validates_hash_and_optional_physics_fingerprint(self) -> None:
        fingerprint = {"sim_build": "2026.07.31", "tire_model": "v7"}
        manifest = self.write_complete(sim_physics_fingerprint=fingerprint)

        self.assertEqual(manifest["schema_version"], 2)
        self.assertTrue(manifest["research_complete"])
        self.assertEqual(manifest["sim_physics_fingerprint"], fingerprint)
        self.assertEqual(
            self.store.cache_status(
                CONTEXT, sim_physics_fingerprint=fingerprint
            )["state"],
            "fresh",
        )
        stale = self.store.cache_status(
            CONTEXT,
            sim_physics_fingerprint={"sim_build": "2026.08.01", "tire_model": "v7"},
        )
        self.assertEqual(stale["state"], "stale")
        self.assertIn("fingerprint", stale["reason"])

        # Race length is deliberately not part of the seasonal knowledge key.
        other_length = {**CONTEXT, "race_length_key": "80-laps"}
        self.assertEqual(self.store.cache_status(other_length)["state"], "fresh")

    def test_empty_required_research_is_explicitly_incomplete(self) -> None:
        manifest = self.store.write_knowledge_bundle(
            CONTEXT,
            sources=[],
            facts={},
            garage61={"representative_laps": []},
        )

        self.assertFalse(manifest["research_complete"])
        status = self.store.cache_status(CONTEXT)
        self.assertEqual(status["state"], "incomplete")
        self.assertEqual(set(status["missing_research"]), {"sources", "facts"})

    def test_manifest_and_component_corruption_are_rejected(self) -> None:
        def mutate_manifest(change: Callable[[dict[str, Any]], None]) -> None:
            manifest_path = self.manifest_path()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            change(manifest)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        cases: list[tuple[str, Callable[[], None], str]] = [
            (
                "schema",
                lambda: mutate_manifest(
                    lambda manifest: manifest.__setitem__("schema_version", 999)
                ),
                "schema version",
            ),
            (
                "context",
                lambda: mutate_manifest(
                    lambda manifest: manifest.__setitem__("car_key", "another-car")
                ),
                "context field",
            ),
            (
                "cache-key",
                lambda: mutate_manifest(
                    lambda manifest: manifest.__setitem__("cache_key", "wrong/key")
                ),
                "cache_key",
            ),
            (
                "required-file",
                lambda: (self.store.cache_path(CONTEXT) / "facts.json").unlink(),
                "missing",
            ),
            (
                "required-type",
                lambda: (self.store.cache_path(CONTEXT) / "sources.json").write_text(
                    json.dumps(["not-an-object"]), encoding="utf-8"
                ),
                "array of JSON objects",
            ),
            (
                "source-hash",
                lambda: (self.store.cache_path(CONTEXT) / "facts.json").write_text(
                    json.dumps({"track": "Tampered Speedway"}), encoding="utf-8"
                ),
                "source_hash",
            ),
            (
                "completeness-flag",
                lambda: mutate_manifest(
                    lambda manifest: manifest.__setitem__("research_complete", False)
                ),
                "research_complete",
            ),
        ]

        for label, mutate, reason_fragment in cases:
            with self.subTest(label=label):
                self.write_complete()
                mutate()
                status = self.store.cache_status(CONTEXT)
                self.assertEqual(status["state"], "invalid")
                self.assertIn(reason_fragment, status["reason"])

    def test_rewrite_removes_omitted_optional_components(self) -> None:
        self.write_complete(
            garage61={"representative_laps": [{"id": "lap-1"}]},
            track_shape={"points": [[0, 0], [1, 1]]},
            notes_markdown="Use the high line.",
        )
        bundle = self.store.cache_path(CONTEXT)
        optional_paths = (
            bundle / "garage61" / "index.json",
            bundle / "track" / "shape.json",
            bundle / "knowledge.md",
        )
        self.assertTrue(all(path.is_file() for path in optional_paths))

        manifest = self.write_complete()

        self.assertIsNone(manifest["files"]["garage61"])
        self.assertIsNone(manifest["files"]["track_shape"])
        self.assertIsNone(manifest["files"]["notes"])
        self.assertTrue(all(not path.exists() for path in optional_paths))
        self.assertEqual(self.store.cache_status(CONTEXT)["state"], "fresh")

        # A stale file that appears without a declaration must not be consumed.
        stale_index = optional_paths[0]
        stale_index.parent.mkdir(parents=True, exist_ok=True)
        stale_index.write_text(json.dumps({"stale": True}), encoding="utf-8")
        status = self.store.cache_status(CONTEXT)
        self.assertEqual(status["state"], "invalid")
        self.assertIn("stale optional", status["reason"])


class StorageTuningHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ArchiveStore(self.temporary.name)
        self.context = {
            "season_key": "2026S3",
            "car_key": "stockcars2-supra2019",
            "track_key": "iowa-oval",
            "setup_type": "open",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_package_and_experiment_survive_feedback_round_trip(self) -> None:
        saved = self.store.save_tuning_package(
            {
                "package_id": "pkg-iowa-26s3",
                "context": self.context,
                "identity": {
                    "car_path": "stockcars2 supra2019",
                    "track_name": "Iowa Speedway",
                },
                "baseline": {"fingerprint": "baseline-sha"},
                "status": "active",
            }
        )
        self.assertTrue(Path(saved["path"]).is_file())
        self.assertEqual(
            self.store.load_tuning_package("pkg-iowa-26s3")["baseline"]["fingerprint"],
            "baseline-sha",
        )

        experiment = self.store.record_tuning_experiment(
            {
                "experiment_id": "experiment-1",
                "analysis_id": "analysis-1",
                "package_id": "pkg-iowa-26s3",
                "context": self.context,
                "setup": {"fingerprint": "setup-1"},
                "symptoms": [{"reported": "tight center"}],
                "recommendation": {"change": "one step"},
            }
        )
        self.assertTrue(Path(experiment["path"]).is_file())
        updated = self.store.record_tuning_feedback(
            "experiment-1",
            {"outcome": "improved", "notes": "better after lap 10"},
        )
        self.assertEqual(updated["experiment"]["outcome"], "improved")

        history = self.store.tuning_history(self.context)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["setup_fingerprint"], "setup-1")
        self.assertEqual(history[0]["feedback"]["notes"], "better after lap 10")
        self.assertEqual(history[0]["outcome"], "improved")
        self.assertEqual(len(self.store.list_tuning_packages(self.context)), 1)

    def test_history_is_season_and_track_scoped_by_default(self) -> None:
        for index, context in enumerate(
            (
                self.context,
                {**self.context, "season_key": "2026S2"},
                {**self.context, "track_key": "michigan-oval"},
            ),
            1,
        ):
            self.store.record_tuning_experiment(
                {
                    "experiment_id": f"experiment-{index}",
                    "context": context,
                    "setup": {"fingerprint": f"setup-{index}"},
                    "symptoms": [],
                    "recommendation": {},
                }
            )
        self.assertEqual(len(self.store.tuning_history(self.context)), 1)
        self.assertEqual(
            len(
                self.store.tuning_history(
                    self.context,
                    include_other_seasons=True,
                    include_other_tracks=True,
                )
            ),
            3,
        )


class StorageRecentAnalysesTests(unittest.TestCase):
    def test_reanalysis_supersedes_same_recorded_session_in_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            source = Path(directory) / "race.ibt"
            source.write_bytes(b"recorded")

            def analysis(identifier: str, analyzed_at: str, green_laps: int) -> dict:
                return {
                    "analysis_id": identifier,
                    "analyzed_at": analyzed_at,
                    "identity": {
                        "session_start": "2026-08-01T11:00:00+00:00",
                        "subsession_id": 42,
                        "session_id": 84,
                        "season_year": 2026,
                        "season_quarter": 3,
                        "car_id": 116,
                        "car_name": "NASCAR O'Reilly Toyota",
                        "track_id": 131,
                        "track_name": "New Hampshire",
                        "track_config": "Oval",
                        "is_fixed_setup": True,
                    },
                    "race_summary": {"scheduled_laps": 100, "recorded_laps": 80},
                    "source": {
                        "telemetry_files": [str(source)],
                        "selection": {
                            "group_id": "subsession:42:1",
                            "subsession_id": 42,
                            "sim_session_num": 1,
                            "sim_session_type": "Race",
                        },
                    },
                    "runs": [{"run_number": 1, "green_laps": green_laps}],
                }

            first = analysis("analysis-old", "2026-08-01T12:00:00+00:00", 10)
            second = analysis("analysis-new", "2026-08-01T13:00:00+00:00", 12)
            store.record_analysis(first, str(Path(directory) / "old" / "report.md"))
            store.record_analysis(second, str(Path(directory) / "new" / "report.md"))

            recent = store.recent_analyses(limit=10)
            history = store.historical_runs(store.context_from_analysis(second))
            self.assertEqual([row["analysis_id"] for row in recent], ["analysis-new"])
            self.assertEqual([row["analysis_id"] for row in history], ["analysis-new"])
            self.assertEqual(history[0]["green_laps"], 12)

    def test_recent_analyses_returns_compact_availability_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            report_dir = Path(directory) / "reports" / "one"
            report_dir.mkdir(parents=True)
            report = report_dir / "report.md"
            analysis_path = report_dir / "analysis.json"
            race_card_path = report_dir / "race-card.md"
            report.write_text("# report\n", encoding="utf-8")
            analysis_path.write_text("{}\n", encoding="utf-8")
            race_card_path.write_text("# race card\n", encoding="utf-8")
            source = Path(directory) / "race.ibt"
            source.write_bytes(b"recorded")
            analysis = {
                "analysis_id": "analysis-dashboard-1",
                "analyzed_at": "2026-08-01T12:00:00+00:00",
                "identity": {
                    "session_start": "2026-08-01T11:00:00+00:00",
                    "subsession_id": 42,
                    "session_id": 84,
                    "season_year": 2026,
                    "season_quarter": 3,
                    "car_id": 116,
                    "car_name": "NASCAR O'Reilly Toyota",
                    "track_id": 131,
                    "track_name": "New Hampshire",
                    "track_config": "Oval",
                    "is_fixed_setup": True,
                },
                "race_summary": {"scheduled_laps": 100, "recorded_laps": 80},
                "source": {
                    "telemetry_files": [str(source)],
                    "selection": {
                        "group_id": "subsession:42:1",
                        "subsession_id": 42,
                        "sim_session_num": 1,
                        "sim_session_type": "Race",
                    },
                },
                "runs": [],
            }
            store.record_analysis(analysis, str(report))

            rows = store.recent_analyses(limit=10)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["subsession_id"], "42")
            self.assertEqual(rows[0]["summary"]["recorded_laps"], 80)
            self.assertTrue(rows[0]["report_available"])
            self.assertTrue(rows[0]["analysis_available"])
            self.assertTrue(rows[0]["race_card_available"])
            self.assertEqual(rows[0]["race_card_path"], str(race_card_path))
            self.assertTrue(rows[0]["source_available"])

    def test_qualifying_and_race_are_distinct_durable_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)

            def analysis(identifier: str, phase: str, sim_session_num: int) -> dict:
                source = Path(directory) / f"{phase.casefold()}.ibt"
                source.write_bytes(phase.encode("utf-8"))
                return {
                    "analysis_id": identifier,
                    "analyzed_at": f"2026-08-01T12:0{sim_session_num}:00+00:00",
                    "identity": {
                        "session_start": "2026-08-01T11:00:00+00:00",
                        "subsession_id": 42,
                        "session_id": 84,
                        # Weekend EventType is intentionally not the exact phase.
                        "event_type": "Race",
                        "season_year": 2026,
                        "season_quarter": 3,
                        "car_name": "Test Car",
                        "track_name": "Test Track",
                        "is_fixed_setup": True,
                    },
                    "race_summary": {"scheduled_laps": 40, "recorded_laps": 10},
                    "source": {
                        "telemetry_files": [str(source)],
                        "selection": {
                            "group_id": f"subsession:42:{sim_session_num}",
                            "subsession_id": 42,
                            "sim_session_num": sim_session_num,
                            "sim_session_type": phase,
                        },
                    },
                    "runs": [{"run_number": 1, "green_laps": sim_session_num + 1}],
                }

            qualifying = analysis("analysis-qualifying", "Qualify", 0)
            race = analysis("analysis-race", "Race", 1)
            qualifying["analyzed_at"] = "2026-08-01T12:59:00+00:00"
            self.assertNotEqual(
                store.report_directory(qualifying),
                store.report_directory(race),
                "Two phases in one subsession must never share an artifact directory.",
            )
            store.record_analysis(qualifying, str(Path(directory) / "qualifying" / "report.md"))
            store.record_analysis(race, str(Path(directory) / "race" / "report.md"))

            recent = {row["session_phase"]: row for row in store.recent_analyses(limit=10)}
            self.assertEqual(set(recent), {"qualifying", "race"})
            self.assertEqual(recent["qualifying"]["session_group_id"], "subsession:42:0")
            self.assertEqual(recent["race"]["session_group_id"], "subsession:42:1")
            race_only = store.recent_analyses(limit=1, phase="race")
            self.assertEqual(
                [row["analysis_id"] for row in race_only],
                ["analysis-race"],
                "The phase predicate must run before LIMIT so newer qualifying rows cannot hide races.",
            )
            self.assertEqual(
                [row["analysis_id"] for row in store.historical_runs(store.context_from_analysis(qualifying))],
                ["analysis-qualifying"],
            )
            self.assertEqual(
                [row["analysis_id"] for row in store.historical_runs(store.context_from_analysis(race))],
                ["analysis-race"],
            )

    def test_existing_history_database_backfills_phase_identity_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "reports" / "legacy"
            report_dir.mkdir(parents=True)
            report_path = report_dir / "report.md"
            report_path.write_text("# legacy\n", encoding="utf-8")
            (report_dir / "analysis.json").write_text(
                json.dumps({
                    "identity": {"subsession_id": 42, "event_type": "Race"},
                    "source": {"selection": {
                        "group_id": "subsession:42:0",
                        "subsession_id": 42,
                        "sim_session_num": 0,
                        "sim_session_type": "Qualify",
                    }},
                }),
                encoding="utf-8",
            )
            database = sqlite3.connect(root / "history.sqlite3")
            database.execute("""
                CREATE TABLE sessions (
                    analysis_id TEXT PRIMARY KEY, analyzed_at TEXT NOT NULL,
                    session_start TEXT, subsession_id TEXT, session_id TEXT,
                    season_key TEXT NOT NULL, car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL, setup_type TEXT NOT NULL,
                    race_length_key TEXT NOT NULL, source_path TEXT,
                    report_path TEXT NOT NULL, summary_json TEXT NOT NULL
                )
            """)
            database.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-analysis", "2026-08-01T12:00:00+00:00", None, "42", "84",
                    "2026s3", "test-car", "test-track", "fixed", "40-laps", None,
                    str(report_path), "{}",
                ),
            )
            database.commit()
            database.close()

            rows = ArchiveStore(root).recent_analyses(limit=10)

            self.assertEqual(rows[0]["session_group_id"], "subsession:42:0")
            self.assertEqual(rows[0]["sim_session_num"], "0")
            self.assertEqual(rows[0]["sim_session_type"], "Qualify")
            self.assertEqual(rows[0]["session_phase"], "qualifying")

    def test_existing_history_database_migration_is_concurrent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "reports" / "legacy"
            report_dir.mkdir(parents=True)
            report_path = report_dir / "report.md"
            report_path.write_text("# legacy\n", encoding="utf-8")
            (report_dir / "analysis.json").write_text(
                json.dumps({
                    "identity": {"subsession_id": 42},
                    "source": {"selection": {
                        "group_id": "subsession:42:1",
                        "subsession_id": 42,
                        "sim_session_num": 1,
                        "sim_session_type": "Race",
                    }},
                }),
                encoding="utf-8",
            )
            database = sqlite3.connect(root / "history.sqlite3")
            database.execute("""
                CREATE TABLE sessions (
                    analysis_id TEXT PRIMARY KEY, analyzed_at TEXT NOT NULL,
                    session_start TEXT, subsession_id TEXT, session_id TEXT,
                    season_key TEXT NOT NULL, car_key TEXT NOT NULL,
                    track_key TEXT NOT NULL, setup_type TEXT NOT NULL,
                    race_length_key TEXT NOT NULL, source_path TEXT,
                    report_path TEXT NOT NULL, summary_json TEXT NOT NULL
                )
            """)
            database.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-analysis", "2026-08-01T12:00:00+00:00", None, "42", "84",
                    "2026s3", "test-car", "test-track", "fixed", "40-laps", None,
                    str(report_path), "{}",
                ),
            )
            database.commit()
            database.close()

            workers = 12
            barrier = threading.Barrier(workers)

            def initialize(_: int) -> None:
                barrier.wait()
                ArchiveStore(root).initialize()

            with ThreadPoolExecutor(max_workers=workers) as executor:
                for future in [executor.submit(initialize, index) for index in range(workers)]:
                    future.result(timeout=35)

            store = ArchiveStore(root)
            store.initialize()
            rows = store.recent_analyses(limit=10, phase="race")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["analysis_id"], "legacy-analysis")
            self.assertEqual(rows[0]["session_group_id"], "subsession:42:1")


if __name__ == "__main__":
    unittest.main()
