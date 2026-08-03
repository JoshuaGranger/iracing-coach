from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tuning_workflow  # noqa: E402
from storage import ArchiveStore  # noqa: E402


def _html(name: str, track_header: str = "newhampshire oval") -> str:
    return f"""<!DOCTYPE HTML><html><body>
<H2 align="center">iRacing.com Motorsport Simulations<br>
stockcars2 supra2019 setup: {name}<br>track: {track_header}</H2><br>
<H2><U>LEFT FRONT:</U></H2>
Cold pressure:<U>15.0 psi</U><br>Last hot pressure:<U>17.0 psi</U><br>
Last temps O M I:<U>99F</U><br><U>100F</U><br><U>101F</U><br>
Tread remaining:<U>100%</U><br><U>100%</U><br><U>100%</U><br>
<H2><U>FRONT:</U></H2>
Cross weight:<U>49.7%</U><br>Front brake bias:<U>64.0%</U><br>
<H2><U>LEFT FRONT:</U></H2>
Spring perch offset:<U>-3.812&quot;</U><br>Ride height:<U>4.254 in</U><br>
<H2><U>Notes:</U></H2>
to loosen, RIGHT on LR spring perch offset, LEFT on RR spring perch offset.<br>
</body></html>"""


def _analysis(*, fixed: bool = False, fingerprint: str = "setup-a") -> dict:
    return {
        "schema_version": 1,
        "analysis_id": f"analysis-{fingerprint}",
        "analyzed_at": "2026-08-01T12:00:00+00:00",
        "identity": {
            "season_year": 2026,
            "season_quarter": 3,
            "car_id": 120,
            "car_path": "stockcars2 supra2019",
            "car_name": "NASCAR O'Reilly Toyota Supra",
            "track_id": 95,
            "track_name": "Iowa Speedway",
            "track_config": "Oval",
            "is_fixed_setup": fixed,
            "setup_name": "NOAPS_MaconiSetupShop Iowa 26S3 R.sto",
            "setup_fingerprint": fingerprint,
            "setup_modified": False,
            "setup_parameter_count": 2,
            "conditions": {"track_temp_c": 40.0, "air_temp_c": 27.0},
            "setup": {
                "Chassis": {
                    "Front": {"CrossWeight": "49.7%", "FrontBrakeBias": "64.0%"},
                    "LeftFront": {"SpringPerchOffset": "-97 mm", "RideHeight": "108 mm"},
                }
            },
        },
        "race_summary": {"scheduled_laps": 80},
        "runs": [],
        "setup_telemetry": {
            "available_channels": ["CFSRrideHeight"],
            "platform": {"center_front_splitter_min_in": 0.27},
            "limits": ["Controlled A/B comparison required."],
        },
    }


class TuningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.iracing = self.root / "iRacing"
        self.setup_dir = self.iracing / "setups" / "stockcars2 supra2019"
        self.setup_dir.mkdir(parents=True)
        for role in ("Q", "R"):
            stem = f"NOAPS_MaconiSetupShop Iowa 26S3 {role}"
            (self.setup_dir / f"{stem}.htm").write_text(_html(stem), encoding="utf-8")
            (self.setup_dir / f"{stem}.sto").write_bytes(b"opaque" + role.encode("ascii"))
        self.archive = self.root / "archive"
        self.analysis_path = self.root / "analysis.json"
        self.analysis_path.write_text(json.dumps(_analysis()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_semantic_match_requires_zero_unmatched_material_fields(self) -> None:
        exact_summary = {
            "common_fields": 20,
            "different_fields": 0,
            "only_left_fields": 0,
            "only_right_fields": 0,
        }
        self.assertTrue(tuning_workflow._comparison_is_exact({"summary": exact_summary}))
        for side in ("only_left_fields", "only_right_fields"):
            changed = dict(exact_summary)
            changed[side] = 1
            self.assertFalse(tuning_workflow._comparison_is_exact({"summary": changed}))

    def test_catalog_package_recommend_feedback_history_end_to_end(self) -> None:
        catalog = tuning_workflow.catalog_iracing_setups_workflow(
            root=self.iracing,
            archive_root=self.archive,
            car="O'Reilly Toyota Supra",
            track="Iowa",
            season="2026S3",
        )
        self.assertTrue(catalog["read_only_sources"])
        self.assertEqual(catalog["counts"]["matching_entries"], 2)
        self.assertTrue(Path(catalog["archive_index"]).is_file())

        package = tuning_workflow.build_open_setup_package_workflow(
            analysis_path=self.analysis_path,
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
        )
        self.assertEqual(package["status"], "exact-track-baseline")
        self.assertTrue(package["baseline_confirmation"]["confirmed"])
        self.assertTrue(package["baseline"]["identity_warnings"])
        self.assertIsNotNone(package["qualifying"])

        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=self.analysis_path,
            archive_root=self.archive,
            package_id=package["package_id"],
            symptoms="Tight in the center after lap 10",
        )
        self.assertTrue(plan["persisted"])
        self.assertEqual(plan["primary_recommendation"]["source"], "setup-builder-note")
        self.assertTrue(plan["recommendation"]["builder_note_provenance"]["used"])

        result_path = self.root / "result-analysis.json"
        result_path.write_text(json.dumps(_analysis(fingerprint="setup-b")), encoding="utf-8")
        feedback = tuning_workflow.record_open_setup_feedback_workflow(
            experiment_id=plan["experiment_id"],
            outcome="improved",
            notes="Rotated better and kept the RF alive through lap 15.",
            archive_root=self.archive,
            result_analysis_path=result_path,
        )
        self.assertEqual(feedback["comparison"]["status"], "comparable-candidate")
        history = tuning_workflow.iracing_setup_history_workflow(
            archive_root=self.archive,
            package_id=package["package_id"],
        )
        self.assertEqual(history["experiment_count"], 1)
        self.assertEqual(history["experiments"][0]["outcome"], "improved")

    def test_conflicting_export_header_without_ibt_is_provisional(self) -> None:
        package = tuning_workflow.build_open_setup_package_workflow(
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
            car="O'Reilly Toyota Supra",
            track="Iowa",
        )
        self.assertEqual(package["status"], "provisional-exact-track-baseline")
        self.assertFalse(package["baseline_confirmation"]["confirmed"])
        self.assertEqual(
            package["baseline_confirmation"]["status"],
            "provisional-conflicting-export-header",
        )
        self.assertFalse(package["simulator_loadable_setup_produced"])

    def test_exact_analysis_car_directory_wins_over_same_named_camaro_artifact(self) -> None:
        camaro_dir = self.iracing / "setups" / "stockcars2 camaro2019"
        camaro_dir.mkdir(parents=True)
        for role in ("Q", "R"):
            stem = f"NOAPS_MaconiSetupShop Iowa 26S3 {role}"
            (camaro_dir / f"{stem}.htm").write_text(
                _html(stem).replace(
                    "to loosen, RIGHT on LR spring perch offset, LEFT on RR spring perch offset.",
                    "WRONG CAR DIRECTORY NOTE: change everything.",
                ),
                encoding="utf-8",
            )
            (camaro_dir / f"{stem}.sto").write_bytes(b"same-name-camaro" + role.encode("ascii"))

        package = tuning_workflow.build_open_setup_package_workflow(
            analysis_path=self.analysis_path,
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
        )

        self.assertEqual(package["baseline"]["car_folder"], "stockcars2 supra2019")
        self.assertTrue(package["baseline_confirmation"]["confirmed"])
        self.assertTrue(
            package["baseline_confirmation"]["car_directory_match"]["confirmed"]
        )
        self.assertNotIn("WRONG CAR DIRECTORY", package["baseline"]["builder_notes"])

    def test_wrong_car_baseline_can_never_supply_builder_notes_or_semantic_match(self) -> None:
        package_result = tuning_workflow.build_open_setup_package_workflow(
            analysis_path=self.analysis_path,
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
        )
        store = ArchiveStore(self.archive)
        package = store.load_tuning_package(package_result["package_id"])
        package["baseline"]["car_folder"] = "stockcars2 camaro2019"
        package["baseline"]["builder_notes"] = "to loosen, apply the wrong Camaro note"
        store.save_tuning_package(package)

        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=self.analysis_path,
            archive_root=self.archive,
            package_id=package_result["package_id"],
            symptoms="Tight in the center",
        )

        provenance = plan["recommendation"]["builder_note_provenance"]
        self.assertTrue(plan["persisted"])
        self.assertFalse(provenance["used"])
        self.assertFalse(provenance["semantic_parameter_match"])
        self.assertFalse(provenance["artifact_car_directory_match"]["confirmed"])
        self.assertIn("identity.car_path", provenance["reason"])
        self.assertEqual(
            plan["primary_recommendation"]["source"],
            "vehicle-family tuning rule",
        )

    def test_provisional_builder_notes_are_suppressed_until_ibt_confirmation(self) -> None:
        package = tuning_workflow.build_open_setup_package_workflow(
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
            car="O'Reilly Toyota Supra",
            track="Iowa",
        )
        analysis = _analysis()
        analysis["identity"]["setup_name"] = "unrelated-custom-setup.sto"
        analysis_path = self.root / "unrelated-analysis.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=analysis_path,
            archive_root=self.archive,
            package_id=package["package_id"],
            symptoms="Tight in the center from lap 10",
        )
        self.assertTrue(plan["persisted"])
        self.assertEqual(plan["primary_recommendation"]["source"], "vehicle-family tuning rule")
        self.assertFalse(plan["recommendation"]["builder_note_provenance"]["used"])
        saved = ArchiveStore(self.archive).load_tuning_package(package["package_id"])
        self.assertEqual(saved["status"], "provisional-exact-track-baseline")

    def test_stored_confirmation_does_not_authorize_notes_for_different_current_setup(self) -> None:
        package = tuning_workflow.build_open_setup_package_workflow(
            analysis_path=self.analysis_path,
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
        )
        self.assertTrue(package["baseline_confirmation"]["confirmed"])
        analysis = _analysis(fingerprint="unrelated")
        analysis["identity"]["setup_name"] = "unrelated-custom-setup.sto"
        analysis["identity"]["setup"] = {
            "Chassis": {"Front": {"CrossWeight": "55.0%"}}
        }
        analysis_path = self.root / "different-current-setup.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=analysis_path,
            archive_root=self.archive,
            package_id=package["package_id"],
            symptoms="Tight in the center from lap 10",
        )
        self.assertTrue(plan["persisted"])
        self.assertEqual(plan["primary_recommendation"]["source"], "vehicle-family tuning rule")
        self.assertFalse(plan["recommendation"]["builder_note_provenance"]["used"])

    def test_unknown_setup_type_never_persists_a_tuning_experiment(self) -> None:
        analysis = _analysis()
        analysis["identity"].pop("is_fixed_setup")
        unknown_path = self.root / "unknown-setup-type.json"
        unknown_path.write_text(json.dumps(analysis), encoding="utf-8")
        result = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=unknown_path,
            archive_root=self.archive,
            symptoms="Loose on entry",
        )
        self.assertEqual(result["status"], "needs-open-setup-confirmation")
        self.assertFalse(result["persisted"])

    def test_donor_builder_notes_are_never_applied_as_target_track_directions(self) -> None:
        target_track = "Fictional Compact Oval"
        package = tuning_workflow.build_open_setup_package_workflow(
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
            car="O'Reilly Toyota Supra",
            track=target_track,
            track_characteristics={"layout": "compact moderate bank short oval"},
        )
        self.assertEqual(package["status"], "donor-baseline")
        self.assertEqual(package["donor"]["donor"], "Iowa")
        analysis = _analysis()
        analysis["identity"]["track_name"] = target_track
        analysis_path = self.root / "donor-target-analysis.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=analysis_path,
            archive_root=self.archive,
            package_id=package["package_id"],
            symptoms="Tight in the center",
        )
        self.assertTrue(plan["persisted"])
        self.assertFalse(plan["recommendation"]["builder_note_provenance"]["used"])
        self.assertEqual(plan["primary_recommendation"]["source"], "vehicle-family tuning rule")

    def test_explicit_package_must_match_current_car_track_and_season(self) -> None:
        package = tuning_workflow.build_open_setup_package_workflow(
            analysis_path=self.analysis_path,
            iracing_root=self.iracing,
            archive_root=self.archive,
            season="2026S3",
        )
        analysis = _analysis()
        analysis["identity"]["track_name"] = "Michigan International Speedway"
        wrong_track_path = self.root / "wrong-track-analysis.json"
        wrong_track_path.write_text(json.dumps(analysis), encoding="utf-8")
        with self.assertRaises(tuning_workflow.TuningWorkflowError):
            tuning_workflow.recommend_open_setup_tuning_workflow(
                analysis_path=wrong_track_path,
                archive_root=self.archive,
                package_id=package["package_id"],
                symptoms="Tight in the center",
            )

    def test_fixed_analysis_never_persists_a_tuning_experiment(self) -> None:
        fixed_path = self.root / "fixed.json"
        fixed_path.write_text(json.dumps(_analysis(fixed=True)), encoding="utf-8")
        result = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=fixed_path,
            archive_root=self.archive,
            symptoms="Loose on entry",
        )
        self.assertEqual(result["status"], "not-applicable-fixed-session")
        self.assertFalse(result["persisted"])
        store = ArchiveStore(self.archive)
        context = store.context_from_analysis(_analysis(fixed=True))
        context["setup_type"] = "open"
        self.assertEqual(store.tuning_history(context), [])

    def test_tuning_reuses_only_exact_source_native_event_cache(self) -> None:
        digest = "a" * 64
        analysis = _analysis()
        analysis["source"] = {
            "fingerprints": [
                {
                    "path": str(self.root / "source.ibt"),
                    "sha256": digest,
                    "size": 123,
                    "modified_ns": 456,
                }
            ]
        }
        analysis_path = self.root / "analysis-with-native-source.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
        result = {
            "schema_version": 1,
            "source": {"sha256": digest},
            "query": {
                "event_types": [
                    "brake_onset",
                    "brake_release",
                    "wheel_speed_divergence",
                ],
                "start_record": 0,
                "end_record": 5000,
                "context_filters": {"lap": 8},
            },
            "events": [
                {
                    "event_type": "brake_onset",
                    "source_record_index": 1000,
                    "session_time_s": 25.0,
                    "lap": 8,
                    "lap_distance_fraction": 0.72,
                    "evidence": {
                        "label": "derived",
                        "measured_channels": ["Brake"],
                        "method": "two-native-record hysteresis transition",
                    },
                    "measurements": {"value": 0.07, "threshold": 0.05},
                },
                {
                    "event_type": "wheel_speed_divergence",
                    "source_record_index": 1010,
                    "session_time_s": 25.16,
                    "lap": 8,
                    "lap_distance_fraction": 0.73,
                    "evidence": {
                        "label": "proxy",
                        "measured_channels": ["Speed", "RFspeed"],
                        "method": "same-distance clean-lap baseline",
                        "limitation": "diagnostic context only",
                    },
                    "measurements": {
                        "peak_threshold_score": 1.4,
                        "per_wheel": {
                            "RF": {
                                "delta": -0.08,
                                "threshold": 0.06,
                                "baseline_lap_count": 3,
                            }
                        },
                    },
                },
            ],
        }
        cache_dir = self.archive / "telemetry-events" / digest
        cache_dir.mkdir(parents=True)
        (cache_dir / "query.json").write_text(
            json.dumps(
                {
                    "cache_schema": 1,
                    "cache_key": "query",
                    "result_sha256": tuning_workflow.stable_hash(result, 64),
                    "result": result,
                }
            ),
            encoding="utf-8",
        )

        plan = tuning_workflow.recommend_open_setup_tuning_workflow(
            analysis_path=analysis_path,
            archive_root=self.archive,
            symptoms="Loose on entry under braking",
        )

        native = plan["recommendation"]["telemetry_evidence"]["native_events"]
        self.assertEqual(native["status"], "available")
        self.assertTrue(native["cache_only"])
        self.assertFalse(native["detector_invoked_by_tuning"])
        self.assertEqual(native["counts_by_type"]["brake_onset"], 1)
        self.assertEqual(native["counts_by_type"]["wheel_speed_divergence"], 1)
        self.assertEqual(native["event_samples"][0]["source_record_index"], 1000)
        self.assertTrue(
            any("exact-record A/B alignment" in item for item in plan["primary_recommendation"]["evidence"])
        )
        self.assertTrue(any("not proof" in item for item in plan["primary_recommendation"]["evidence"]))


if __name__ == "__main__":
    unittest.main()
