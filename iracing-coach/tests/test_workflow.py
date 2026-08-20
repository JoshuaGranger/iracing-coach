from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import garage61_client  # noqa: E402
import reporting  # noqa: E402
import workflow  # noqa: E402
from analysis_engine import analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402


def _analysis() -> dict:
    profile = []
    for index in range(20):
        in_zone = 4 <= index <= 11
        profile.append(
            {
                "bin": index,
                "lap_pct": (index + 0.5) / 20,
                "speed_mph": 118.0 - (14.0 if in_zone else 0.0),
                "brake": 0.32 if 4 <= index <= 7 else 0.0,
                "throttle": 0.35 if 4 <= index <= 9 else 0.9,
                "steering_abs_rad": 0.21 if in_zone else 0.01,
                "lateral_g": 1.1 if in_zone else 0.1,
            }
        )
    return {
        "schema_version": 1,
        "analysis_profile_version": "post-race-foundations-v15",
        "analysis_id": "workflow-test-analysis",
        "analyzed_at": "2026-08-01T12:00:00+00:00",
        "source": {
            "telemetry_files": [],
            "sample_count": 2,
            "available_channels": [],
            "channel_coverage": {
                "catalog_complete": True,
                "recorded_count": 2,
                "loaded_count": 2,
                "analyzed_count": 2,
            },
        },
        "identity": {
            "session_id": 44,
            "subsession_id": 55,
            "session_start": "2026-07-15T18:00:00+00:00",
            "season_id": 6358,
            "season_year": 2026,
            "season_quarter": 3,
            "track_id": 88,
            "track_name": "Iowa Speedway",
            "track_config": "Oval",
            "car_id": 99,
            "car_name": "NASCAR Truck",
            "car_path": "truck",
            "is_fixed_setup": True,
            "driver_irating": 2450,
            "weight_penalty_kg": 5.0,
            "power_adjust_percent": -1.0,
            "max_fuel_percent": 75.0,
        },
        "conditions": {
            "track_temp": 34.0,
            "air_temp": 25.0,
            "track_usage": 65.0,
            "track_wetness": 0.0,
            "tire_compound": 0,
        },
        "race_summary": {
            "scheduled_laps": 100,
            "recorded_laps": 2,
            "runs_detected": 0,
            "pit_stops_detected": 0,
        },
        "laps": [
            {
                "lap": 1,
                "lap_time_s": 30.0,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
            },
            {
                "lap": 2,
                "lap_time_s": 30.4,
                "complete": True,
                "flag_state": "green",
                "pit_time_s": 0.0,
            },
        ],
        "runs": [{"run_number": 1, "fuel": {"start_l": 55.0}}],
        "track_profile": {
            "bins": 20,
            "profile": profile,
            "detected_corner_segments": [
                {
                    "segment": 1,
                    "start_pct": 0.15,
                    "end_pct": 0.6,
                    "wraps_start_finish": False,
                }
            ],
        },
        "strategy": {"limitations": []},
        "coaching_signals": [],
        "data_quality": {"channels": {}, "missing": [], "confidence": "low"},
    }


class WorkflowLocalTests(unittest.TestCase):
    def test_knowledge_omits_legacy_garage61_targets_but_preserves_other_facts(self) -> None:
        components = {
            "facts": {"track": "verified"},
            "sources": [],
            "garage61": {"representative_laps": [{"lap_id": "legacy"}]},
            "track_shape": {"corners": 4},
            "notes_markdown": "notes",
        }
        with mock.patch.object(workflow, "_bundle_components", return_value=components):
            knowledge = workflow._knowledge_for_report(
                mock.Mock(), {}, {"state": "fresh"}
            )

        self.assertEqual(knowledge["facts"], {"track": "verified"})
        self.assertEqual(knowledge["track_shape"], {"corners": 4})
        self.assertEqual(knowledge["garage61"], {})

        components["garage61"]["target_derivation_version"] = (
            workflow._GARAGE61_TARGET_DERIVATION_VERSION
        )
        with mock.patch.object(workflow, "_bundle_components", return_value=components):
            knowledge = workflow._knowledge_for_report(
                mock.Mock(), {}, {"state": "incomplete"}
            )
        self.assertEqual(
            knowledge["garage61"]["representative_laps"], [{"lap_id": "legacy"}]
        )

    def test_iracing_root_environment_override_supports_portable_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(
                os.environ,
                {"IRACING_COACH_IRACING_ROOT": directory},
            ):
                self.assertTrue(
                    os.path.samefile(workflow._default_iracing_root(), Path(directory)),
                    "The configured iRacing root should resolve to the same directory even when Windows uses an 8.3 alias.",
                )

    def test_analysis_pipeline_fingerprint_is_stable_sha256(self) -> None:
        first = workflow._analysis_pipeline_sha256()
        self.assertEqual(first, workflow._analysis_pipeline_sha256())
        self.assertEqual(len(first), 64)
        int(first, 16)

    def test_group_selector_distinguishes_qualifying_from_race_in_one_subsession(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = [
                {
                    "kind": "session",
                    "group_id": "subsession:8001:0",
                    "subsession_id": 8001,
                    "sim_session_num": 0,
                    "sim_session_type": "Qualify",
                    "is_race": False,
                    "files": [str(Path(directory) / "qualify.ibt")],
                },
                {
                    "kind": "session",
                    "group_id": "subsession:8001:1",
                    "subsession_id": 8001,
                    "sim_session_num": 1,
                    "sim_session_type": "Race",
                    "is_race": True,
                    "files": [str(Path(directory) / "race.ibt")],
                },
            ]
            with mock.patch.object(workflow, "discover_sessions", return_value=sessions):
                qualifying = workflow._resolve_session_selection(
                    "subsession:8001:0", directory
                )
                race = workflow._resolve_session_selection("subsession:8001:1", directory)
                numeric = workflow._resolve_session_selection("8001", directory)

            self.assertEqual(qualifying["selector_type"], "group_id")
            self.assertEqual(qualifying["sim_session_type"], "Qualify")
            self.assertFalse(qualifying["is_race"])
            self.assertEqual(race["selector_type"], "group_id")
            self.assertEqual(race["sim_session_type"], "Race")
            self.assertTrue(race["is_race"])
            self.assertEqual(numeric["selector_type"], "subsession_id")
            self.assertTrue(numeric["is_race"], "Legacy numeric selectors must continue to prefer Race.")

    def test_shared_source_analysis_identity_is_qualified_by_exact_event_phase(self) -> None:
        qualifying = {
            "group_id": "subsession:8001:0",
            "subsession_id": 8001,
            "sim_session_num": 0,
            "sim_session_type": "Qualify",
        }
        qualifying_file_selector = {
            "group_id": None,
            "subsession_id": 8001,
            "sim_session_num": 0,
            "sim_session_type": "Qualify",
        }
        race = {
            "group_id": "subsession:8001:1",
            "subsession_id": 8001,
            "sim_session_num": 1,
            "sim_session_type": "Race",
        }

        qualifying_id = workflow._phase_qualified_analysis_id("source-analysis", qualifying)
        race_id = workflow._phase_qualified_analysis_id("source-analysis", race)

        self.assertNotEqual(qualifying_id, race_id)
        self.assertEqual(
            qualifying_id,
            workflow._phase_qualified_analysis_id("source-analysis", qualifying_file_selector),
            "Selecting the same phase by group or file must retain one durable identity.",
        )

    def test_shared_source_cache_identity_is_qualified_by_exact_event_phase(self) -> None:
        fingerprints = [{"sha256": "a" * 64}]
        qualifying = {
            "group_id": "subsession:8001:0",
            "subsession_id": 8001,
            "sim_session_num": 0,
            "sim_session_type": "Qualify",
        }
        race = {
            "group_id": "subsession:8001:1",
            "subsession_id": 8001,
            "sim_session_num": 1,
            "sim_session_type": "Race",
        }

        self.assertNotEqual(
            workflow._analysis_cache_identity(fingerprints, 20.0, "pipeline", qualifying),
            workflow._analysis_cache_identity(fingerprints, 20.0, "pipeline", race),
        )

    def test_saved_setup_prefers_exact_identity_car_path_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup_name = "NOAPS Iowa 26S3 R.sto"
            camaro = root / "setups" / "stockcars2 camaro2019" / setup_name
            supra = root / "setups" / "stockcars2 supra2019" / setup_name
            camaro.parent.mkdir(parents=True)
            supra.parent.mkdir(parents=True)
            camaro.write_bytes(b"camaro-artifact")
            supra.write_bytes(b"supra-artifact")
            analysis = _analysis()
            analysis["identity"].update(
                {
                    "car_name": "NASCAR O'Reilly Toyota Supra",
                    "car_path": "stockcars2 supra2019",
                    "setup_name": setup_name,
                    "setup": {"Chassis": {"Front": {"CrossWeight": "49.7%"}}},
                }
            )

            result = workflow._local_race_file_references(
                root,
                analysis,
                workflow.ArchiveStore(root / "archive"),
            )

            self.assertTrue(result["embedded_setup_is_authority"])
            self.assertEqual(
                result["saved_setup_status"],
                "matched_by_exact_filename_and_car_path",
            )
            self.assertEqual(
                Path(result["preferred_saved_setup"]["path"]), supra.resolve()
            )
            self.assertEqual(Path(result["saved_setup_matches"][0]["path"]), supra.resolve())
            self.assertTrue(result["saved_setup_matches"][0]["identity_car_path_match"])
            self.assertEqual(
                result["saved_setup_matches"][1]["match"],
                "exact-filename-other-car-directory",
            )

    def test_discovery_and_analyze_default_to_latest_metadata_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            practice_file = root / "newer-practice.ibt"
            race_file = root / "older-race.ibt"
            practice_file.write_bytes(b"practice")
            race_file.write_bytes(b"race")
            discovered = [
                {
                    "kind": "session",
                    "valid": True,
                    "group_id": "practice",
                    "subsession_id": 60,
                    "is_race": False,
                    "end_time_unix": 200,
                    "files": [str(practice_file)],
                },
                {
                    "kind": "session",
                    "valid": True,
                    "group_id": "race",
                    "subsession_id": 55,
                    "is_race": True,
                    "end_time_unix": 100,
                    "files": [str(race_file)],
                },
            ]
            loaded = {
                "metadata": {},
                "session_info": {},
                "variables": [],
                "samples": {"SessionTime": [0.0, 0.05], "Lap": [1, 1]},
                "sample_rate_hz": 20.0,
                "native_tick_rate_hz": 60,
                "sample_count": 2,
                "source_record_count": 2,
            }
            analysis = _analysis()
            analysis["damage_repair"] = {
                "status": "recorded",
                "summary": {
                    "tow_episodes": 0,
                    "recorded_repair_episodes": 1,
                    "repair_required_flag_episodes": 1,
                    "confirmed_fast_repair_uses": 0,
                },
                "episodes": [],
                "run_impacts": [],
            }
            with (
                mock.patch.object(workflow, "discover_sessions", return_value=discovered),
                mock.patch.object(
                    workflow,
                    "scan_ibt",
                    return_value={
                        "variables": [
                            {"name": "SessionTime"},
                            {"name": "Lap"},
                        ]
                    },
                ),
                mock.patch.object(workflow, "load_telemetry", return_value=loaded),
                mock.patch.object(workflow, "analyze_telemetry", return_value=analysis),
                mock.patch.object(workflow, "render_report", return_value="# Local report\n"),
                mock.patch.object(workflow, "render_visuals", return_value={}),
            ):
                discovery_result = workflow.discover_sessions_workflow(
                    root=root, races_only=False
                )
                result = workflow.analyze_race_workflow(
                    selector="latest",
                    iracing_root=root,
                    archive_root=root / "archive",
                )

            self.assertEqual(discovery_result["latest_race"]["subsession_id"], 55)
            self.assertEqual(result["selection"]["group_id"], "race")
            self.assertEqual(
                result["analysis_id"],
                workflow._phase_qualified_analysis_id(
                    "workflow-test-analysis", result["selection"]
                ),
            )
            self.assertEqual(result["source_files"], [str(race_file.resolve())])
            self.assertTrue(result["source_channel_coverage"]["catalog_complete"])
            self.assertTrue(Path(result["analysis_path"]).is_file())
            self.assertTrue(Path(result["report_path"]).is_file())
            self.assertTrue(Path(result["race_card_path"]).is_file())
            self.assertEqual(result["race_card"]["path"], result["race_card_path"])
            self.assertIn("## Race triggers", result["race_card"]["markdown"])
            self.assertNotIn("unavailable", result["race_card"]["markdown"].lower())
            self.assertLessEqual(result["race_card"]["word_count_before_evidence"], 300)
            self.assertEqual(result["timing"]["contract_version"], 1)
            self.assertGreaterEqual(result["timing"]["total_ms"], 0.0)
            self.assertEqual(result["damage_repair"], analysis["damage_repair"])
            self.assertEqual(result["analysis_view"]["schema_version"], 1)
            self.assertEqual(
                result["analysis_view"]["analysis_profile_version"],
                "post-race-foundations-v15",
            )
            self.assertEqual(result["analysis_view"]["track_profile"], analysis["track_profile"])
            self.assertEqual(result["analysis_view"]["laps"], analysis["laps"])
            self.assertIn("lap_traces", result["analysis_view"])
            self.assertEqual(
                result["analysis_view"]["garage61_representative_laps"][
                    "target_derivation_version"
                ],
                workflow._GARAGE61_TARGET_DERIVATION_VERSION,
            )
            self.assertTrue((root / "archive" / "history.sqlite3").is_file())

    def test_explicit_file_does_not_require_iracing_root_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "practice.ibt"
            path.write_bytes(b"recording")
            metadata = {
                "subsession_id": 77,
                "sim_session_type": "Practice",
                "is_race": False,
                "variables": [{"name": "SessionTime"}, {"name": "Lap"}],
            }
            loaded = {
                "metadata": {},
                "session_info": {},
                "variables": [],
                "samples": {"SessionTime": [0.0, 0.05], "Lap": [1, 1]},
                "sample_rate_hz": 20.0,
                "native_tick_rate_hz": 60,
                "sample_count": 2,
                "source_record_count": 2,
            }
            with (
                mock.patch.object(workflow, "scan_ibt", return_value=metadata),
                mock.patch.object(workflow, "load_telemetry", return_value=loaded),
                mock.patch.object(workflow, "analyze_telemetry", return_value=_analysis()),
                mock.patch.object(workflow, "render_report", return_value="# Report\n"),
                mock.patch.object(workflow, "render_visuals", return_value={}),
                mock.patch.object(workflow, "discover_sessions") as discover,
            ):
                result = workflow.analyze_race_workflow(
                    selector=str(path), archive_root=Path(directory) / "archive"
                )
            discover.assert_not_called()
            self.assertEqual(result["selection"]["selector_type"], "file")
            self.assertFalse(result["selection"]["is_race"])

    def test_read_only_inventory_counts_artifacts_and_safe_app_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "telemetry").mkdir()
            (root / "replay").mkdir()
            (root / "setups" / "truck").mkdir(parents=True)
            (root / "lapfiles").mkdir()
            (root / "telemetry" / "race.ibt").write_bytes(b"ibt")
            (root / "replay" / "race.rpy").write_bytes(b"rpy")
            (root / "setups" / "truck" / "race.sto").write_bytes(b"sto")
            (root / "lapfiles" / "baseline.olap").write_bytes(b"lap")
            (root / "rendererDX11.ini").write_text("[Display]\nfoo=bar\n", encoding="utf-8")
            (root / "app.ini").write_text(
                "[Misc]\nirsdkLog360Hz=1\nsecretPassword=do-not-return\n",
                encoding="utf-8",
            )

            result = workflow.iracing_local_inventory_workflow(
                root=root,
                recent_limit=5,
                include_known_roots=False,
            )

            self.assertTrue(result["read_only"])
            self.assertEqual(result["counts"]["ibt"], 1)
            self.assertEqual(result["counts"]["replays"], 1)
            self.assertEqual(result["counts"]["setups"], 1)
            self.assertEqual(result["counts"]["lapfiles"], 1)
            self.assertEqual(result["counts"]["configs"], 2)
            self.assertEqual(result["references"]["setups"][0]["content_folder"], "truck")
            self.assertEqual(result["app_ini"]["settings"]["Misc.irsdkLog360Hz"], "1")
            self.assertEqual(
                result["app_ini"]["telemetry_logging"]["disk_sample_rate_hz"], 360
            )
            self.assertNotIn("secretPassword", json.dumps(result["app_ini"]))

    def test_inventory_honors_read_only_install_root_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            documents_root = base / "documents" / "iRacing"
            install_root = base / "programs" / "iRacing"
            documents_root.mkdir(parents=True)
            (install_root / "cars" / "stockcars2" / "supra2019").mkdir(
                parents=True
            )
            version_file = install_root / "cars" / "stockcars2" / "supra2019" / "version.txt"
            version_file.write_text("2026.07.01\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"IRACING_COACH_INSTALL_ROOT": str(install_root)},
                clear=False,
            ):
                result = workflow.iracing_local_inventory_workflow(
                    root=documents_root,
                    recent_limit=5,
                    include_known_roots=True,
                )

            configured = next(
                item
                for item in result["known_roots"]
                if item["label"] == "install_configured"
            )
            self.assertEqual(configured["path"], str(install_root.resolve()))
            self.assertTrue(configured["read_only_metadata_only"])
            self.assertEqual(configured["file_count"], 1)
            self.assertEqual(configured["extension_counts"], {".txt": 1})

    def test_companion_dashboard_joins_discovery_to_archived_analysis(self) -> None:
        sessions = {
            "root": "telemetry",
            "latest_race": {"subsession_id": 55},
            "sessions": [
                {
                    "kind": "session",
                    "subsession_id": 55,
                    "track_name": "Iowa",
                    "files": ["race.ibt"],
                }
            ],
            "errors": [],
        }
        recent = [
            {
                "analysis_id": "analysis-55",
                "subsession_id": "55",
                "analyzed_at": "2026-08-01T12:00:00+00:00",
                "analysis_path": "analysis.json",
                "report_path": "report.md",
                "race_card_path": "race-card.md",
                "analysis_available": True,
                "report_available": True,
                "race_card_available": True,
                "source_available": True,
                "summary": {"recorded_laps": 100},
            }
        ]
        store = mock.Mock()
        store.auth_dir = Path("auth")
        store.recent_analyses.return_value = recent
        store.list_tuning_packages.return_value = [{"package_id": "pkg-1"}]
        with (
            mock.patch.object(workflow, "discover_sessions_workflow", return_value=sessions),
            mock.patch.object(workflow, "ArchiveStore", return_value=store),
            mock.patch.object(workflow, "_read_json_file", return_value={"status": "pending"}),
            mock.patch.object(workflow, "credential_exists", return_value=False),
        ):
            result = workflow.companion_dashboard_workflow(limit=10)

        self.assertEqual(result["latest_race"]["analysis_status"], "analyzed")
        self.assertEqual(result["latest_race"]["analysis"]["analysis_id"], "analysis-55")
        self.assertTrue(result["latest_race"]["analysis"]["race_card_available"])
        self.assertEqual(result["latest_race"]["analysis"]["race_card_path"], "race-card.md")
        self.assertEqual(result["tuning_packages"][0]["package_id"], "pkg-1")
        self.assertEqual(result["garage61"]["api_request_status"], "pending")
        self.assertTrue(result["capabilities"]["race_card"])
        self.assertTrue(result["capabilities"]["corner_phase_coaching"])
        self.assertTrue(result["capabilities"]["groove_migration"])
        self.assertTrue(result["capabilities"]["damage_repair_awareness"])

    def test_companion_dashboard_never_cross_joins_qualifying_and_race(self) -> None:
        sessions = {
            "root": "telemetry",
            "latest_race": {"group_id": "subsession:55:1"},
            "sessions": [
                {
                    "kind": "session",
                    "group_id": "legacy-weekend:0",
                    "subsession_id": 55,
                    "sim_session_num": 0,
                    "sim_session_type": "Qualify",
                    "files": ["weekend.ibt"],
                },
                {
                    "kind": "session",
                    "group_id": "legacy-weekend:1",
                    "subsession_id": 55,
                    "sim_session_num": 1,
                    "sim_session_type": "Race",
                    "is_race": True,
                    "files": ["weekend.ibt"],
                },
            ],
            "errors": [],
        }
        recent = [
            {
                "analysis_id": "analysis-qualifying",
                "session_group_id": "subsession:55:0",
                "subsession_id": "55",
                "sim_session_type": "Qualify",
                "session_phase": "qualifying",
                "source_path": "weekend.ibt",
                "summary": {"recorded_laps": 2},
            },
            {
                "analysis_id": "analysis-race",
                "session_group_id": "subsession:55:1",
                "subsession_id": "55",
                "sim_session_type": "Race",
                "session_phase": "race",
                "source_path": "weekend.ibt",
                "summary": {"recorded_laps": 40},
            },
        ]
        store = mock.Mock()
        store.auth_dir = Path("auth")
        store.recent_analyses.return_value = recent
        store.list_tuning_packages.return_value = []
        with (
            mock.patch.object(workflow, "discover_sessions_workflow", return_value=sessions),
            mock.patch.object(workflow, "ArchiveStore", return_value=store),
            mock.patch.object(workflow, "_read_json_file", return_value={}),
            mock.patch.object(workflow, "credential_exists", return_value=False),
        ):
            result = workflow.companion_dashboard_workflow(limit=10)

        store.recent_analyses.assert_called_once_with(limit=10, phase="race")

        by_group = {item["group_id"]: item for item in result["races"]}
        self.assertEqual(by_group["legacy-weekend:0"]["analysis"]["analysis_id"], "analysis-qualifying")
        self.assertEqual(by_group["legacy-weekend:0"]["analysis"]["summary"]["recorded_laps"], 2)
        self.assertEqual(by_group["legacy-weekend:1"]["analysis"]["analysis_id"], "analysis-race")
        self.assertEqual(by_group["legacy-weekend:1"]["analysis"]["summary"]["recorded_laps"], 40)


class _FakeGarage61Client:
    def __init__(self) -> None:
        self.searches: list[str] = []
        self.downloads: list[str] = []
        self.catalog = garage61_client.ContentCatalog(
            cars=({"id": 10, "name": "NASCAR Truck", "platform_id": "99"},),
            tracks=({"id": 20, "name": "Iowa", "variant": "Oval", "platform_id": "88"},),
            seasons=({"id": 263, "name": "2026 Season 3", "platform": "iracing"},),
        )

    def health_check(self) -> dict:
        return {
            "ok": True,
            "api_permissions": ["driving_data"],
            "capabilities": {
                "personal_and_team_laps": {"available": True},
                "global_visible_laps": {
                    "available": False,
                    "diagnostic": "Own/team scope only.",
                },
            },
        }

    def content_catalog(self) -> garage61_client.ContentCatalog:
        return self.catalog

    def find_comparable_laps(
        self,
        target: dict,
        *,
        setup_type: str,
        top_n: int,
        search_limit: int,
    ) -> list[garage61_client.RankedLap]:
        self.searches.append(setup_type)
        lap_id = f"lap-{setup_type}"
        lap = {
            "id": lap_id,
            "car": {"id": 10},
            "track": {"id": 20},
            "season": {"id": 263},
            "lapTime": 29.8 if setup_type == "fixed" else 29.6,
            "clean": True,
            "canViewTelemetry": True,
            "_comparisonSetupType": setup_type,
        }
        return [
            garage61_client.RankedLap(
                lap=lap,
                score=95.0,
                pace_delta_seconds=0.4,
                setup_type=setup_type,
                reasons=("same season",),
            )
        ][:top_n]

    def download_lap_csv(
        self, lap_id: str, destination: Path, *, overwrite: bool = False
    ) -> Path:
        self.downloads.append(lap_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "Time,LapDistPct,Speed,Brake,Throttle,SteeringWheelAngle,LatAccel,FutureUnknown"
        ]
        for index in range(20):
            pct = (index + 0.5) / 20
            in_zone = 4 <= index <= 11
            speed_mph = 112.0 - (10.0 if in_zone else 0.0)
            speed_mps = speed_mph / 2.236936292
            brake = 0.22 if 3 <= index <= 6 else 0.0
            throttle = 0.35 if 4 <= index <= 8 else 0.9
            steering = 0.15 if in_zone else 0.01
            lat_accel = 9.5 if in_zone else 0.8
            lines.append(
                f"{index / 20:.3f},{pct:.5f},{speed_mps:.6f},{brake:.3f},"
                f"{throttle:.3f},{steering:.3f},{lat_accel:.3f},kept"
            )
        destination.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        return destination


class WorkflowGarage61Tests(unittest.TestCase):
    def test_garage_target_uses_canonical_fields_from_real_analyzer_output(self) -> None:
        analysis = analyze_telemetry(synthetic_telemetry())

        target = workflow._garage_target(
            analysis,
            {"id": 10, "name": "NASCAR Truck"},
            {"id": 20, "name": "Iowa", "variant": "Oval"},
            {"id": 263, "name": "2026 Season 3", "platform": "iracing"},
        )

        self.assertEqual(target["fuelLevel"], analysis["runs"][0]["fuel"]["start_l"])
        self.assertEqual(
            target["_metadataSources"]["fuelLevel"],
            "analysis.runs[0].fuel.start_l",
        )
        self.assertEqual(target["trackTemp"], analysis["identity"]["conditions"]["track_temp_c"])
        self.assertEqual(
            target["_metadataSources"]["trackTemp"],
            "analysis.identity.conditions.track_temp_c",
        )

    def test_garage_target_ignores_lap_trace_fuel_when_selecting_starting_fuel(self) -> None:
        analysis = _analysis()
        analysis["lap_traces"] = {
            "traces": [
                {
                    "points": [
                        {
                            "additional_signals": {
                                "fuel-level": 15.848,
                            }
                        }
                    ]
                }
            ]
        }

        target = workflow._garage_target(
            analysis,
            {"id": 10, "name": "NASCAR Truck"},
            {"id": 20, "name": "Iowa", "variant": "Oval"},
            {"id": 263, "name": "2026 Season 3", "platform": "iracing"},
        )

        self.assertEqual(target["fuelLevel"], 55.0)
        self.assertEqual(
            target["_metadataSources"]["fuelLevel"],
            "analysis.runs[0].fuel.start_l",
        )

    def test_garage_target_ignores_arbitrary_nested_metadata_aliases(self) -> None:
        analysis = _analysis()
        analysis["runs"] = []
        del analysis["conditions"]["track_temp"]
        analysis["unrelated"] = {
            "fuelLevel": 12.5,
            "driverRating": 9999,
            "trackTemp": 99.0,
        }

        target = workflow._garage_target(
            analysis,
            {"id": 10, "name": "NASCAR Truck"},
            {"id": 20, "name": "Iowa", "variant": "Oval"},
            {"id": 263, "name": "2026 Season 3", "platform": "iracing"},
        )

        self.assertNotIn("fuelLevel", target)
        self.assertNotIn("trackTemp", target)
        self.assertEqual(target["driverRating"], 2450.0)
        self.assertEqual(
            target["_metadataSources"]["driverRating"],
            "analysis.identity.driver_irating",
        )

    def test_garage_target_preserves_explicit_legacy_root_precedence(self) -> None:
        analysis = _analysis()
        analysis["trackTemp"] = 31.5
        analysis["fuelLevel"] = 42.0

        target = workflow._garage_target(
            analysis,
            {"id": 10, "name": "NASCAR Truck"},
            {"id": 20, "name": "Iowa", "variant": "Oval"},
            {"id": 263, "name": "2026 Season 3", "platform": "iracing"},
        )

        self.assertEqual(target["trackTemp"], 31.5)
        self.assertEqual(target["fuelLevel"], 42.0)
        self.assertEqual(target["_metadataSources"]["trackTemp"], "analysis.trackTemp")
        self.assertEqual(target["_metadataSources"]["fuelLevel"], "analysis.fuelLevel")

    def test_garage_target_preserves_identity_tire_compound_precedence(self) -> None:
        analysis = _analysis()
        analysis["identity"]["tire_compound"] = 2
        analysis["conditions"]["tire_compound"] = 1

        target = workflow._garage_target(
            analysis,
            {"id": 10, "name": "NASCAR Truck"},
            {"id": 20, "name": "Iowa", "variant": "Oval"},
            {"id": 263, "name": "2026 Season 3", "platform": "iracing"},
        )

        self.assertEqual(target["tireCompound"], 2)
        self.assertEqual(
            target["_metadataSources"]["tireCompound"],
            "analysis.identity.tire_compound",
        )

    def test_sync_separates_cohorts_and_reuses_exact_csv_cache(self) -> None:
        fake = _FakeGarage61Client()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis_path = root / "analysis.json"
            analysis_path.write_text(json.dumps(_analysis()), encoding="utf-8")
            archive = root / "archive"
            seeded_store = workflow.ArchiveStore(archive)
            seeded_context = seeded_store.context_from_analysis(_analysis())
            seeded_store.write_knowledge_bundle(
                seeded_context,
                sources=[],
                facts={},
                sim_physics_fingerprint={"sim_build": "test-build-1"},
            )
            with (
                mock.patch.object(workflow, "Garage61Client", return_value=fake),
                mock.patch.object(workflow, "render_report", return_value="# Garage report\n"),
                mock.patch.object(workflow, "render_visuals", return_value={}),
            ):
                first = workflow.garage61_sync_workflow(
                    analysis_path=analysis_path,
                    archive_root=archive,
                    maximum_laps=4,
                )
                second = workflow.garage61_sync_workflow(
                    analysis_path=analysis_path,
                    archive_root=archive,
                    maximum_laps=4,
                )
                interim_index = json.loads(
                    Path(first["cache"]["garage61_index"]).read_text(encoding="utf-8")
                )
                fixed_reference = next(
                    item
                    for item in interim_index["representative_laps"]
                    if item["setup_type"] == "fixed"
                )
                tampered_path = Path(first["cache"]["path"]) / fixed_reference["telemetry"]["path"]
                tampered_path.write_bytes(tampered_path.read_bytes() + b"\r\n")
                third = workflow.garage61_sync_workflow(
                    analysis_path=analysis_path,
                    archive_root=archive,
                    maximum_laps=4,
                )

            self.assertEqual(
                fake.searches,
                ["fixed", "open", "fixed", "open", "fixed", "open"],
            )
            self.assertEqual(fake.downloads, ["lap-fixed", "lap-open", "lap-fixed"])
            self.assertEqual(first["comparison_scope"], "own/team")
            self.assertEqual(
                first["cache"]["manifest"]["sim_physics_fingerprint"],
                {"sim_build": "test-build-1"},
            )
            self.assertEqual(first["representative_lap_count"], 2)
            self.assertEqual(first["telemetry"]["downloaded"], 2)
            self.assertEqual(second["telemetry"]["already_cached"], 2)
            self.assertEqual(third["telemetry"]["downloaded"], 1)
            self.assertEqual(third["telemetry"]["already_cached"], 1)
            self.assertTrue(first["comparison_quality"]["usable"])
            self.assertGreaterEqual(len(first["coaching_targets"]), 1)
            self.assertIn("entry_speed_delta_mph", first["coaching_targets"][0])
            self.assertIn("brake_onset_delta_lap_pct", first["coaching_targets"][0])
            self.assertGreater(
                first["coaching_targets"][0]["brake_onset_delta_lap_pct"], 0
            )
            self.assertIn(
                "earlier reference onset",
                first["coaching_targets"][0]["coaching"].lower(),
            )
            index_path = Path(first["cache"]["garage61_index"])
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(
                index["target_derivation_version"],
                workflow._GARAGE61_TARGET_DERIVATION_VERSION,
            )
            self.assertEqual(
                first["garage61_representative_laps"]["target_derivation_version"],
                workflow._GARAGE61_TARGET_DERIVATION_VERSION,
            )
            target_lap_cache = json.loads(
                Path(first["cache"]["portable_target_laps"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                target_lap_cache["target_derivation_version"],
                workflow._GARAGE61_TARGET_DERIVATION_VERSION,
            )
            self.assertEqual(set(index["cohorts"]), {"fixed", "open"})
            self.assertTrue(
                all(
                    len(item["telemetry"].get("sha256", "")) == 64
                    for item in index["representative_laps"]
                )
            )
            self.assertEqual(index["target"]["trackTemp"], 34.0)
            self.assertEqual(index["target"]["fuelLevel"], 55.0)
            self.assertEqual(index["target"]["driverRating"], 2450.0)
            self.assertEqual(index["target"]["weightPenalty"], 5.0)
            self.assertEqual(index["target"]["powerAdjust"], -1.0)
            self.assertEqual(index["target"]["maxFuelPercent"], 75.0)
            self.assertTrue(index["comparison_quality"]["usable"])
            store = workflow.ArchiveStore(archive)
            cache_status = store.cache_status(first["context"])
            self.assertEqual(cache_status["state"], "incomplete")
            incomplete_knowledge = workflow._knowledge_for_report(
                store, first["context"], cache_status
            )
            self.assertGreaterEqual(
                len(incomplete_knowledge["garage61"]["coaching_targets"]), 1
            )
            csv_paths = [
                archive / "season-cache" / "2026s3"
                / "99-nascar-truck" / "88-iowa-speedway-oval" / "fixed"
                / item["telemetry"]["path"]
                for item in index["representative_laps"]
            ]
            self.assertTrue(all(path.is_file() for path in csv_paths))
            self.assertTrue(all(b"FutureUnknown" in path.read_bytes() for path in csv_paths))

    def test_elite_clean_lap_is_seeded_for_a_four_lap_cohort(self) -> None:
        cohort = []
        for index, lap_time in enumerate((30.0, 29.8, 29.2, 29.6), 1):
            cohort.append(
                {
                    "lap": {
                        "id": f"lap-{index}",
                        "lapTime": lap_time,
                        "clean": True,
                        "canViewTelemetry": True,
                    },
                    "score": 100 - index,
                    "setup_type": "fixed",
                }
            )
        workflow._annotate_comparison_roles(cohort)
        selected = workflow._select_representatives(
            {"fixed": cohort, "open": []}, "fixed", 2
        )
        self.assertEqual(selected[0]["comparison_role"], "elite")
        self.assertEqual(selected[0]["lap"]["id"], "lap-3")

    def test_report_target_line_exposes_exact_control_deltas(self) -> None:
        line = reporting._knowledge_target_line(
            {
                "name": "Load zone 1",
                "start_pct": 0.2,
                "end_pct": 0.4,
                "entry_speed_mph": 105.0,
                "entry_speed_delta_mph": 2.4,
                "brake_onset_delta_lap_pct": 0.012,
                "brake_release_delta_lap_pct": -0.008,
                "throttle_pickup_delta_lap_pct": 0.015,
                "peak_brake_delta": 0.04,
                "steering_delta_rad": 0.025,
                "coaching": "Match the reference trace.",
            }
        )
        self.assertIn("entry +2.4 mph local-reference", line)
        self.assertIn("brake onset 1.2% lap later", line)
        self.assertIn("75% throttle 1.5% lap later", line)

    def test_status_does_not_contact_api_without_credential(self) -> None:
        with (
            mock.patch.object(workflow, "credential_exists", return_value=False),
            mock.patch.object(workflow, "Garage61Client") as client,
        ):
            result = workflow.garage61_status_workflow()
        client.assert_not_called()
        self.assertFalse(result["configured"])
        self.assertEqual(result["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
