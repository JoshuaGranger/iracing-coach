from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from storage import ArchiveStore  # noqa: E402
import storage  # noqa: E402


CONTEXT = {
    "season_key": "2026s3",
    "car_key": "123-nascar-truck",
    "track_key": "63-synthetic-speedway-oval",
    "setup_type": "fixed",
    "race_length_key": "40-laps",
}


class StorageCacheIntegrityTests(unittest.TestCase):
    def test_copy_does_not_overwrite_a_destination_created_during_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "source.sto", root / "copy.sto"
            source.write_bytes(b"immutable original")
            def competing_copy(temporary, target):
                Path(target).write_bytes(b"new user copy")
                raise FileExistsError()
            with mock.patch.object(storage.os, "link", side_effect=competing_copy):
                with self.assertRaises(storage.ArchiveVerificationFailed):
                    storage._atomic_copy_verified(source, destination, storage.file_sha256(source), replace_existing=False)
            self.assertEqual(source.read_bytes(), b"immutable original")
            self.assertEqual(destination.read_bytes(), b"new user copy")
            self.assertEqual(set(root.iterdir()), {source, destination})

    def test_atomic_copy_and_write_do_not_repeat_long_destination_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.ibt"
            source.write_bytes(b"immutable telemetry")
            # The final name fits the filesystem's component limit; the old
            # temporary prefix repeated it and exceeded that limit.
            name_length = min(249, 258 - len(str(root)) - 1)
            destination = root / ("x" * (name_length - 4) + ".ibt")
            digest = storage.file_sha256(source)
            storage._atomic_copy_verified(source, destination, digest)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(storage.file_sha256(source), digest)
            storage._atomic_write_text(destination, "derived content")
            self.assertEqual(destination.read_text(), "derived content")
            self.assertEqual(set(root.iterdir()), {source, destination})

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
    def test_hybrid_limits_use_distinct_history_keys(self) -> None:
        base = {
            "identity": {
                "season_year": 2026,
                "season_quarter": 3,
                "car_name": "Test Car",
                "track_name": "Test Track",
                "is_fixed_setup": True,
            },
            "race_summary": {"scheduled_laps": 500, "scheduled_minutes": 30},
        }

        thirty = ArchiveStore.context_from_analysis(base)
        sixty = ArchiveStore.context_from_analysis(
            {
                **base,
                "race_summary": {"scheduled_laps": 500, "scheduled_minutes": 60},
            }
        )

        self.assertEqual(thirty["race_length_key"], "declared-v1-500-laps-or-30-minutes")
        self.assertEqual(sixty["race_length_key"], "declared-v1-500-laps-or-60-minutes")
        self.assertNotEqual(thirty["race_length_key"], sixty["race_length_key"])
        self.assertEqual(
            ArchiveStore.context_from_analysis(
                {**base, "race_summary": {"scheduled_laps": 80}}
            )["race_length_key"],
            "declared-v1-80-laps",
        )
        self.assertEqual(
            ArchiveStore.context_from_analysis(
                {**base, "race_summary": {"scheduled_minutes": 30.5}}
            )["race_length_key"],
            "declared-v1-30.5-minutes",
        )

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

    def test_offline_recordings_selected_by_file_never_supersede_each_other(self) -> None:
        # The 9/24 IRP test day: ten Offline Testing recordings analysed by file path
        # all record SubSessionID 0 and SessionNum 0. As an identity that made each
        # analysis delete every other offline row, so the index kept one recording
        # and the setup files the other nine loaded could not be linked.
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)

            def analysis(identifier: str, start: str) -> tuple[dict, Path]:
                source = Path(directory) / f"stockcars2 supra2019_irp {identifier}.ibt"
                source.write_bytes(identifier.encode("utf-8"))
                return {
                    "analysis_id": identifier,
                    "analyzed_at": start,
                    "identity": {
                        "session_start": start, "subsession_id": 0, "session_id": 0, "event_type": "Test",
                        "car_name": "Toyota Supra Class B", "track_name": "Lucas Oil Indianapolis Raceway Park",
                        "track_config": "IRP", "is_fixed_setup": False,
                    },
                    "race_summary": {"recorded_laps": 20},
                    "source": {
                        "telemetry_files": [str(source)],
                        "selection": {"group_id": None, "subsession_id": 0, "sim_session_num": 0,
                                      "sim_session_type": "Offline Testing", "selector_type": "file"},
                    },
                    "runs": [{"run_number": 1, "green_laps": 19}],
                }, source

            baseline, _ = analysis("00-19-25", "2026-09-25T05:19:25+00:00")
            camber, camber_source = analysis("00-32-47", "2026-09-25T05:32:47+00:00")
            directory_before = store.report_directory(camber)
            store.record_analysis(baseline, str(Path(directory) / "a" / "report.md"))
            store.record_analysis(camber, str(Path(directory) / "b" / "report.md"))

            recent = store.recent_analyses(limit=10)
            self.assertEqual({row["analysis_id"] for row in recent}, {"00-19-25", "00-32-47"})
            self.assertTrue(all(row["subsession_id"] is None and row["session_group_id"] is None for row in recent))
            history = store.historical_runs(store.context_from_analysis(camber))
            self.assertEqual({row["analysis_id"] for row in history}, {"00-19-25", "00-32-47"},
                             "Each recording's run is its own history row.")
            self.assertEqual(directory_before.parent.name, "subsession-0-0",
                             "Report directories keep their established name.")

            # Re-analysing one recording still supersedes only that recording.
            again = dict(camber, analysis_id="00-32-47-v2", analyzed_at="2026-09-25T06:00:00+00:00")
            store.record_analysis(again, str(Path(directory) / "c" / "report.md"))
            self.assertEqual({row["analysis_id"] for row in store.recent_analyses(limit=10)}, {"00-19-25", "00-32-47-v2"})
            self.assertTrue(camber_source.is_file())
            self.assertFalse(storage.is_session_identifier(0))
            self.assertFalse(storage.is_session_identifier("0"))
            self.assertTrue(storage.is_session_identifier(88866363))

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


def _report_analysis(
    identifier: str,
    analyzed_at: str,
    *,
    green_laps: int = 10,
    subsession: int = 88628993,
    selection: dict | None = None,
) -> dict:
    return {
        "analysis_id": identifier,
        "analyzed_at": analyzed_at,
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
        "source": {
            "telemetry_files": ["race.ibt"],
            "selection": (
                selection
                if selection is not None
                else {
                    "group_id": f"subsession:{subsession}:2",
                    "subsession_id": subsession,
                    "sim_session_num": 2,
                    "sim_session_type": "Race",
                }
            ),
        },
        "runs": [{"run_number": 1, "green_laps": green_laps}],
    }


class StorageReportDeduplicationTests(unittest.TestCase):
    def test_reanalysis_of_one_recording_replaces_its_report_in_place(self) -> None:
        # The app re-analyzes a recording on every open. Each run used to add a
        # full report directory named by analyzed_at: 20 copies of one Bristol
        # race (~42 MB each) accumulated on 2026-09-21.
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            first = _report_analysis("ae7b1748175fa9e9f5c6ae52", "2026-09-21T00:07:38+00:00")
            second = _report_analysis("ae7b1748175fa9e9f5c6ae52", "2026-09-21T00:07:43+00:00")

            one = store.save_report_artifacts(first, "# first", {"race-card.md": "card one"})
            store.record_analysis(first, one["report"])
            two = store.save_report_artifacts(second, "# second", {"race-card.md": "card two"})
            store.record_analysis(second, two["report"])

            self.assertEqual(one, two)
            session_dir = Path(one["analysis"]).parent.parent
            self.assertEqual([item.name for item in session_dir.iterdir()], ["ae7b1748175fa9e9f5c6ae52"])
            self.assertEqual(Path(two["report"]).read_text(encoding="utf-8"), "# second\n")
            self.assertEqual(Path(two["race-card.md"]).read_text(encoding="utf-8"), "card two")
            saved = json.loads(Path(two["analysis"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["analyzed_at"], "2026-09-21T00:07:43+00:00")
            self.assertEqual(
                [
                    path.name
                    for path in session_dir.rglob("*")
                    if path.is_file() and path.name.startswith(".")
                ],
                [],
            )
            rows = store.recent_analyses(limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["report_path"], two["report"])
            self.assertTrue(rows[0]["report_available"])

    def test_a_new_analyzer_revision_keeps_its_own_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            old = store.save_report_artifacts(
                _report_analysis("revision-old", "2026-09-21T00:07:38+00:00"), "# old"
            )
            new = store.save_report_artifacts(
                _report_analysis("revision-new", "2026-09-22T00:07:38+00:00"), "# new"
            )
            self.assertNotEqual(Path(old["analysis"]).parent, Path(new["analysis"]).parent)
            self.assertEqual(Path(old["report"]).read_text(encoding="utf-8"), "# old\n")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class StorageReportUsageTests(unittest.TestCase):
    """Read-only measure of duplicate report copies written before the fix."""

    def _legacy_copy(self, store: ArchiveStore, session: str, stamp: str, analysis: dict,
                     *, modified: int, padding: int = 1000, raw_text: str | None = None) -> Path:
        directory = store.reports_dir / "season-6079" / session / stamp
        (directory / "visuals").mkdir(parents=True)
        (directory / "analysis.json").write_text(
            raw_text if raw_text is not None else storage._json_text(analysis),
            encoding="utf-8",
        )
        (directory / "report.md").write_text("#" * padding, encoding="utf-8")
        (directory / "visuals" / "lap-trend.svg").write_text("<svg/>", encoding="utf-8")
        os.utime(directory / "analysis.json", ns=(modified, modified))
        return directory

    @staticmethod
    def _bytes(directory: Path) -> int:
        return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())

    def test_duplicate_copies_are_measured_without_touching_referenced_ones(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            bristol = "subsession-88628993-2"
            same = _report_analysis("ae7b1748175fa9e9f5c6ae52", "2026-09-21T00:07:38+00:00")
            base_ns = 1_790_000_000_000_000_000
            by_trial = self._legacy_copy(store, bristol, "2026-09-21t00-07-38-00-00", same, modified=base_ns)
            indexed = self._legacy_copy(store, bristol, "2026-09-21t00-07-43-00-00", same, modified=base_ns + 1)
            middle = self._legacy_copy(store, bristol, "2026-09-21t01-01-46-00-00", same, modified=base_ns + 2)
            newest = self._legacy_copy(store, bristol, "2026-09-21t03-54-20-00-00", same, modified=base_ns + 3,
                                       padding=5000)
            # Another session's only copy; nothing in the index supersedes it.
            single = self._legacy_copy(
                store, "subsession-88667162-2", "2026-09-21t02-00-00-00-00",
                _report_analysis("single-analysis", "2026-09-21T02:00:00+00:00", subsession=88667162),
                modified=base_ns,
            )
            # A report written without sorted keys is identified by parsing it.
            unsorted = json.dumps({"runs": [], "analysis_id": "unsorted-analysis"})
            older_unsorted = self._legacy_copy(store, "legacy", "one", {}, modified=base_ns, raw_text=unsorted)
            newer_unsorted = self._legacy_copy(store, "legacy", "two", {}, modified=base_ns + 5, raw_text=unsorted)
            unknown = self._legacy_copy(store, "legacy", "unknown", {}, modified=base_ns, raw_text="{}")

            store.record_analysis(same, str(indexed / "report.md"))
            trials = root / "trials"
            trials.mkdir()
            (trials / "trial-1.json").write_text(
                json.dumps({"source": {"analysis_path": str(by_trial / "analysis.json")}}),
                encoding="utf-8",
            )
            before = _snapshot(root / "reports")
            db_before = (root / "history.sqlite3").read_bytes()

            usage = store.report_storage_usage()

            self.assertEqual(_snapshot(root / "reports"), before)
            self.assertEqual((root / "history.sqlite3").read_bytes(), db_before)
            self.assertTrue(usage["read_only"])
            self.assertEqual(usage["status"], "measured")
            self.assertEqual(usage["history_index"], "read")
            self.assertEqual(usage["report_directory_count"], 8)
            self.assertEqual(usage["unidentified_directory_count"], 1)
            all_dirs = [by_trial, indexed, middle, newest, single, older_unsorted, newer_unsorted, unknown]
            self.assertEqual(usage["report_bytes"], sum(self._bytes(item) for item in all_dirs))

            groups = {group["analysis_id"]: group for group in usage["groups"]}
            self.assertEqual(set(groups), {"ae7b1748175fa9e9f5c6ae52", "unsorted-analysis"})
            bristol_group = groups["ae7b1748175fa9e9f5c6ae52"]
            self.assertEqual(bristol_group["copies"], 4)
            # The indexed copy is kept even though a newer copy exists.
            self.assertEqual(bristol_group["retained"]["path"], str(indexed))
            self.assertEqual(bristol_group["retained"]["reason"], "history-index")
            self.assertEqual(
                bristol_group["kept"],
                [{"path": str(by_trial), "bytes": self._bytes(by_trial), "reason": "trials"}],
            )
            self.assertEqual(
                sorted(item["path"] for item in bristol_group["reclaimable"]),
                sorted([str(middle), str(newest)]),
            )
            unsorted_group = groups["unsorted-analysis"]
            self.assertEqual(unsorted_group["retained"]["path"], str(newer_unsorted))
            self.assertEqual(unsorted_group["reclaimable"], [
                {"path": str(older_unsorted), "bytes": self._bytes(older_unsorted)}
            ])
            expected = self._bytes(middle) + self._bytes(newest) + self._bytes(older_unsorted)
            self.assertEqual(usage["reclaimable_bytes"], expected)
            self.assertEqual(usage["reclaimable_directory_count"], 3)
            self.assertEqual(usage["duplicate_group_count"], 2)
            self.assertEqual(usage["groups"][0]["analysis_id"], "ae7b1748175fa9e9f5c6ae52")

    @unittest.skipUnless(os.name == "nt", "a moved Windows Documents home; D12 re-ingests on Linux")
    def test_references_still_protect_copies_after_the_documents_home_moves(self) -> None:
        # A copied or migrated Documents home keeps absolute paths to the old
        # location in its history index and saved records (31 of 34 index rows
        # in the DESKTOP-SIM QA profile). They must still identify the copy.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "moved" / "data"
            store = ArchiveStore(root)
            old_reports = r"C:\Users\someone\Documents\iRacing Coach\data\reports\season-6079\bristol"
            same = _report_analysis("moved-analysis", "2026-09-21T00:07:38+00:00")
            base_ns = 1_790_000_000_000_000_000
            by_trial = self._legacy_copy(store, "bristol", "t1", same, modified=base_ns)
            indexed = self._legacy_copy(store, "bristol", "t2", same, modified=base_ns + 1)
            newest = self._legacy_copy(store, "bristol", "t3", same, modified=base_ns + 2)
            store.record_analysis(same, old_reports + r"\T2\report.md")
            (root / "trials").mkdir()
            (root / "trials" / "trial-1.json").write_text(
                json.dumps({"source": {"analysis_path": old_reports + r"\t1\analysis.json"}}),
                encoding="utf-8",
            )

            group = store.report_storage_usage()["groups"][0]

            self.assertEqual(group["retained"], {
                "path": str(indexed), "bytes": self._bytes(indexed), "reason": "history-index",
            })
            self.assertEqual([item["path"] for item in group["kept"]], [str(by_trial)])
            self.assertEqual([item["path"] for item in group["reclaimable"]], [str(newest)])

    def test_unreadable_history_index_offers_nothing_for_removal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            same = _report_analysis("same-analysis", "2026-09-21T00:07:38+00:00")
            self._legacy_copy(store, "s", "a", same, modified=1_790_000_000_000_000_000)
            self._legacy_copy(store, "s", "b", same, modified=1_790_000_000_000_000_001)
            (root / "history.sqlite3").write_bytes(b"not a database")

            usage = store.report_storage_usage()

            self.assertEqual(usage["status"], "index-unreadable")
            self.assertEqual(usage["reclaimable_bytes"], 0)
            self.assertEqual(usage["groups"][0]["kept"][0]["reason"], "history-index-unreadable")

    def test_missing_archive_is_reported_empty_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "not-created"
            usage = ArchiveStore(root).report_storage_usage()
            self.assertEqual(usage["status"], "empty")
            self.assertEqual(usage["reclaimable_bytes"], 0)
            self.assertFalse(root.exists())

    @staticmethod
    def _names(root: Path) -> list[str]:
        return sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    def test_measuring_creates_no_file_beside_the_history_index(self) -> None:
        # history.sqlite3 is in WAL mode. Opening it with ?mode=ro when no
        # connection is open created history.sqlite3-shm and -wal in the
        # user's archive, so the "read-only" measure was not read-only.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            same = _report_analysis("same-analysis", "2026-09-21T00:07:38+00:00")
            first = self._legacy_copy(store, "s", "a", same, modified=1_790_000_000_000_000_000)
            self._legacy_copy(store, "s", "b", same, modified=1_790_000_000_000_000_001)
            store.record_analysis(same, str(first / "report.md"))
            before = self._names(root)
            self.assertNotIn("history.sqlite3-wal", before)
            self.assertNotIn("history.sqlite3-shm", before)
            database_before = (root / "history.sqlite3").read_bytes()

            usage = store.report_storage_usage()

            self.assertEqual(self._names(root), before)
            self.assertEqual((root / "history.sqlite3").read_bytes(), database_before)
            self.assertEqual(usage["history_index"], "read")
            self.assertEqual(usage["groups"][0]["retained"]["path"], str(first))

    def test_index_rows_still_in_the_write_ahead_log_are_read_without_creating_files(self) -> None:
        # While the backend holds the index open, its newest rows live only in
        # history.sqlite3-wal. Reading the main file alone would miss them and
        # could offer the copy the index now names.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            same = _report_analysis("same-analysis", "2026-09-21T00:07:38+00:00")
            first = self._legacy_copy(store, "s", "a", same, modified=1_790_000_000_000_000_001)
            second = self._legacy_copy(store, "s", "b", same, modified=1_790_000_000_000_000_000)
            store.record_analysis(same, str(first / "report.md"))
            writer = sqlite3.connect(root / "history.sqlite3")
            try:
                writer.execute(
                    "UPDATE sessions SET report_path = ? WHERE analysis_id = ?",
                    (str(second / "report.md"), "same-analysis"),
                )
                writer.commit()
                self.assertGreater((root / "history.sqlite3-wal").stat().st_size, 0)
                before = self._names(root)

                usage = store.report_storage_usage()

                self.assertEqual(self._names(root), before)
            finally:
                writer.close()
            self.assertEqual(usage["history_index"], "read")
            group = usage["groups"][0]
            self.assertEqual(group["retained"], {
                "path": str(second), "bytes": self._bytes(second), "reason": "history-index",
            })
            self.assertEqual([item["path"] for item in group["reclaimable"]], [str(first)])

    def test_copies_from_an_older_analyzer_revision_are_counted_reclaimable(self) -> None:
        # Re-analysis under a new analyzer revision drops the older revision's
        # index row (record_analysis), but its report copies stayed on disk and
        # were not counted: 2.14 GB of the DESKTOP-SIM QA profile.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            base_ns = 1_790_000_000_000_000_000
            session = "subsession-88628993-2"
            current = _report_analysis("revision-current", "2026-09-22T00:00:00+00:00")
            current_dir = self._legacy_copy(store, session, "revision-current", current, modified=base_ns + 10)
            store.record_analysis(current, str(current_dir / "report.md"))
            old = _report_analysis("revision-old", "2026-09-21T00:00:00+00:00")
            old_a = self._legacy_copy(store, session, "2026-09-21t00-00-00-00-00", old, modified=base_ns)
            old_b = self._legacy_copy(store, session, "2026-09-21t01-00-00-00-00", old, modified=base_ns + 1)
            # An August report: bare-subsession directory, indented JSON, and a
            # selection written before group ids existed.
            august = _report_analysis(
                "revision-august", "2026-08-01T00:00:00+00:00",
                selection={"subsession_id": 88628993, "sim_session_num": 2, "sim_session_type": "Race"},
            )
            august_dir = self._legacy_copy(
                store, "88628993", "2026-08-01t00-00-00-00-00", august, modified=base_ns - 5,
                raw_text=json.dumps(august, indent=2, sort_keys=True),
            )
            by_trial = self._legacy_copy(
                store, session, "2026-09-20t00-00-00-00-00",
                _report_analysis("revision-trial", "2026-09-20T00:00:00+00:00"), modified=base_ns - 1,
            )
            (root / "trials").mkdir()
            (root / "trials" / "trial-1.json").write_text(
                json.dumps({"analysis_path": str(by_trial / "analysis.json")}), encoding="utf-8"
            )
            # Neither is provably an older analysis of the indexed session.
            anonymous = _report_analysis("revision-anonymous", "2026-08-01T00:00:00+00:00")
            anonymous["source"].pop("selection")
            anonymous_dir = self._legacy_copy(store, session, "anonymous", anonymous, modified=base_ns)
            other_dir = self._legacy_copy(
                store, "subsession-77777777-2", "t1",
                _report_analysis("other-session", "2026-09-21T00:00:00+00:00", subsession=77777777),
                modified=base_ns,
            )
            before = _snapshot(root)

            usage = store.report_storage_usage()

            self.assertEqual(_snapshot(root), before)
            superseded = {
                group["analysis_id"]: group for group in usage["groups"] if group["kind"] == "superseded"
            }
            self.assertEqual(set(superseded), {"revision-old", "revision-august", "revision-trial"})
            for group in superseded.values():
                self.assertIsNone(group["retained"])
                self.assertEqual(
                    group["superseded_by"],
                    {"analysis_id": "revision-current", "path": str(current_dir)},
                )
            self.assertEqual(
                sorted(item["path"] for item in superseded["revision-old"]["reclaimable"]),
                sorted([str(old_a), str(old_b)]),
            )
            self.assertEqual(
                [item["path"] for item in superseded["revision-august"]["reclaimable"]],
                [str(august_dir)],
            )
            self.assertEqual(superseded["revision-trial"]["reclaimable"], [])
            self.assertEqual(
                superseded["revision-trial"]["kept"],
                [{"path": str(by_trial), "bytes": self._bytes(by_trial), "reason": "trials"}],
            )
            expected = self._bytes(old_a) + self._bytes(old_b) + self._bytes(august_dir)
            self.assertEqual(usage["superseded_bytes"], expected)
            self.assertEqual(usage["superseded_directory_count"], 3)
            self.assertEqual(usage["superseded_group_count"], 2)
            self.assertEqual(usage["duplicate_group_count"], 0)
            self.assertEqual(usage["reclaimable_bytes"], expected)
            self.assertEqual(usage["reclaimable_directory_count"], 3)
            offered = {item["path"] for group in usage["groups"] for item in group["reclaimable"]}
            for kept in (current_dir, anonymous_dir, other_dir, by_trial):
                self.assertNotIn(str(kept), offered)

    def test_an_older_revision_is_kept_when_the_current_report_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArchiveStore(root)
            current = _report_analysis("revision-current", "2026-09-22T00:00:00+00:00")
            store.record_analysis(
                current, str(store.reports_dir / "season-6079" / "gone" / "report.md")
            )
            self._legacy_copy(
                store, "subsession-88628993-2", "t1",
                _report_analysis("revision-old", "2026-09-21T00:00:00+00:00"),
                modified=1_790_000_000_000_000_000,
            )

            usage = store.report_storage_usage()

            self.assertEqual(usage["reclaimable_bytes"], 0)
            self.assertEqual(usage["superseded_directory_count"], 0)
            self.assertEqual(usage["groups"], [])


def _hold_open(path: Path, opened: threading.Event, release: threading.Event,
               seconds: float | None) -> None:
    # CPython opens files on Windows without FILE_SHARE_DELETE, exactly like
    # .NET File.OpenRead/ReadAllBytes/ReadAllTextAsync in the app: while this
    # handle is open the file can be neither replaced nor deleted.
    with open(path, "rb") as handle:
        handle.read(1)
        opened.set()
        release.wait(60 if seconds is None else seconds)


@unittest.skipUnless(os.name == "nt", "Windows file-sharing semantics")
class StorageReportSaveUnderReaderContentionTests(unittest.TestCase):
    """A save must never fail because the app is reading the previous report."""

    RECORDING = "ae7b1748175fa9e9f5c6ae52"

    def _reader(self, path: Path, seconds: float | None = None) -> tuple[threading.Thread, threading.Event]:
        opened, release = threading.Event(), threading.Event()
        thread = threading.Thread(
            target=_hold_open, args=(path, opened, release, seconds), daemon=True
        )
        thread.start()
        self.assertTrue(opened.wait(10))
        return thread, release

    def _save(self, store: ArchiveStore, stamp: str, label: str) -> dict[str, str]:
        analysis = _report_analysis(self.RECORDING, stamp)
        artifacts = store.save_report_artifacts(
            analysis, f"# {label}", {"race-card.md": f"card {label}", "visuals/lap.svg": f"<svg>{label}</svg>"}
        )
        store.record_analysis(analysis, artifacts["report"])
        return artifacts

    def _assert_report(self, artifacts: dict[str, str], stamp: str, label: str) -> None:
        saved = json.loads(Path(artifacts["analysis"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["analyzed_at"], stamp)
        self.assertEqual(Path(artifacts["report"]).read_text(encoding="utf-8"), f"# {label}\n")
        self.assertEqual(Path(artifacts["race-card.md"]).read_text(encoding="utf-8"), f"card {label}")
        self.assertEqual(Path(artifacts["visuals/lap.svg"]).read_text(encoding="utf-8"), f"<svg>{label}</svg>")

    def test_a_read_of_the_previous_report_is_waited_out_and_replaced_in_place(self) -> None:
        # Verifier probe: the save failed with AtomicReplaceExhausted after
        # about 0.2-0.27 s whenever the app held analysis.json that long.
        for hold in (0.1, 0.4, 2.0):
            with self.subTest(hold_s=hold), tempfile.TemporaryDirectory() as directory:
                store = ArchiveStore(directory)
                one = self._save(store, "2026-09-21T00:07:38+00:00", "first")
                reader, _release = self._reader(Path(one["analysis"]), hold)
                started = time.monotonic()
                two = self._save(store, "2026-09-21T00:07:43+00:00", "second")
                elapsed = time.monotonic() - started
                reader.join(10)

                self.assertEqual(two, one)
                self._assert_report(two, "2026-09-21T00:07:43+00:00", "second")
                session_dir = Path(one["analysis"]).parent.parent
                self.assertEqual([item.name for item in session_dir.iterdir()], [self.RECORDING])
                self.assertEqual([item.name for item in session_dir.rglob(".*")], [])
                self.assertLess(elapsed, hold + 2.0)
                self.assertEqual(store.recent_analyses()[0]["report_path"], one["report"])

    def test_a_read_that_outlasts_the_wait_gets_a_complete_new_copy(self) -> None:
        # A reader that never lets go during the save stands in for the 30 s
        # probe; the bounded wait is shortened so the test stays quick.
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(storage, "_REPORT_REPLACE_WAIT_S", 0.5):
            store = ArchiveStore(directory)
            one = self._save(store, "2026-09-21T00:07:38+00:00", "first")
            canonical = Path(one["analysis"]).parent
            reader, release = self._reader(Path(one["analysis"]))
            try:
                started = time.monotonic()
                two = self._save(store, "2026-09-21T00:07:43+00:00", "second")
                elapsed = time.monotonic() - started

                self.assertLess(elapsed, 5.0)
                fallback = Path(two["analysis"]).parent
                self.assertEqual(fallback.parent, canonical.parent)
                self.assertTrue(fallback.name.startswith(canonical.name + "."))
                self._assert_report(two, "2026-09-21T00:07:43+00:00", "second")
                # The copy being read is complete and untouched.
                self._assert_report(one, "2026-09-21T00:07:38+00:00", "first")
                row = store.recent_analyses()[0]
                self.assertEqual(row["report_path"], two["report"])
                self.assertTrue(row["report_available"])
                self.assertEqual([item.name for item in canonical.parent.rglob(".*")], [])
            finally:
                release.set()
                reader.join(10)

            # Once the read ends, the next save returns to the stable path and
            # removes the copy it superseded.
            three = self._save(store, "2026-09-21T00:07:50+00:00", "third")
            self.assertEqual(three, one)
            self._assert_report(three, "2026-09-21T00:07:50+00:00", "third")
            self.assertEqual([item.name for item in canonical.parent.iterdir()], [canonical.name])
            self.assertEqual(store.recent_analyses()[0]["report_path"], one["report"])

    def test_a_superseded_copy_still_being_read_is_removed_on_a_later_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(storage, "_REPORT_REPLACE_WAIT_S", 0.3):
            store = ArchiveStore(directory)
            one = self._save(store, "2026-09-21T00:07:38+00:00", "first")
            canonical = Path(one["analysis"]).parent
            reader, release = self._reader(Path(one["analysis"]))
            try:
                two = self._save(store, "2026-09-21T00:07:43+00:00", "second")
            finally:
                release.set()
                reader.join(10)
            fallback = Path(two["analysis"]).parent
            self.assertNotEqual(fallback, canonical)

            reader, release = self._reader(Path(two["analysis"]))
            try:
                three = self._save(store, "2026-09-21T00:07:50+00:00", "third")
                self.assertEqual(three, one)
                # Nothing was removed from under the reader.
                self._assert_report(two, "2026-09-21T00:07:43+00:00", "second")
            finally:
                release.set()
                reader.join(10)

            four = self._save(store, "2026-09-21T00:07:55+00:00", "fourth")
            self.assertEqual(four, one)
            self.assertFalse(fallback.exists())
            self.assertEqual([item.name for item in canonical.parent.iterdir()], [canonical.name])

    def test_a_superseded_copy_named_by_a_trial_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(storage, "_REPORT_REPLACE_WAIT_S", 0.3):
            store = ArchiveStore(directory)
            one = self._save(store, "2026-09-21T00:07:38+00:00", "first")
            reader, release = self._reader(Path(one["analysis"]))
            try:
                two = self._save(store, "2026-09-21T00:07:43+00:00", "second")
            finally:
                release.set()
                reader.join(10)
            trials = Path(directory) / "trials"
            trials.mkdir()
            (trials / "trial-1.json").write_text(
                json.dumps({"source": {"analysis_path": two["analysis"]}}), encoding="utf-8"
            )

            three = self._save(store, "2026-09-21T00:07:50+00:00", "third")

            self.assertEqual(three, one)
            self._assert_report(two, "2026-09-21T00:07:43+00:00", "second")

    def test_a_read_of_another_report_file_also_never_fails_the_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(storage, "_REPORT_REPLACE_WAIT_S", 0.3):
            store = ArchiveStore(directory)
            one = self._save(store, "2026-09-21T00:07:38+00:00", "first")
            reader, release = self._reader(Path(one["visuals/lap.svg"]))
            try:
                two = self._save(store, "2026-09-21T00:07:43+00:00", "second")
                self._assert_report(two, "2026-09-21T00:07:43+00:00", "second")
                self.assertEqual(store.recent_analyses()[0]["report_path"], two["report"])
            finally:
                release.set()
                reader.join(10)


if __name__ == "__main__":
    unittest.main()
