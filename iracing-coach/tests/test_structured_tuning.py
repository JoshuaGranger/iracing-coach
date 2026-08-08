from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_server  # noqa: E402
import tuning_engine  # noqa: E402
import tuning_workflow  # noqa: E402
from storage import ArchiveStore  # noqa: E402


def _analysis(*, fixed: bool = False, analysis_id: str = "race-open") -> dict:
    setup = {
        "Chassis": {
            "Front": {"CrossWeight": "49.7%", "FrontBrakeBias": "64.0%"},
            "LeftRear": {"TrackBarHeight": "8.0 in"},
            "RightRear": {"TrackBarHeight": "8.0 in"},
            "LeftFront": {"Packer": "0.10 in"},
            "RightFront": {"Packer": "0.10 in"},
        }
    }
    fingerprint = tuning_engine.embedded_setup_fingerprint(setup)
    phases = [
        {
            "phase": phase,
            "status": "usable",
            "lap_numbers": laps,
            "lap_count": len(laps),
            "metrics": {
                "entry_speed_mph": 140.0,
                "brake_peak_fraction": 0.25,
                "minimum_speed_mph": 112.0,
                "steering_average_abs_rad": 0.20,
                "steering_work_proxy": 12.0,
                "steering_corrections": 1,
                "exit_speed_mph": 130.0,
                "exit_throttle_fraction": 0.85,
            },
        }
        for phase, laps in (("early", [1, 2, 3]), ("middle", [4, 5, 6]), ("late", [7, 8, 9]))
    ]
    return {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "identity": {
            "event_type": "Race",
            "season_year": 2026,
            "season_quarter": 3,
            "car_id": 120,
            "car_path": "stockcars2 supra2019",
            "car_name": "NASCAR O'Reilly Toyota Supra",
            "track_id": 95,
            "track_name": "Iowa Speedway",
            "track_config": "Oval",
            "is_fixed_setup": fixed,
            "setup_name": "Iowa open baseline",
            "setup_fingerprint": fingerprint[:16],
            "setup_modified": False,
            "setup": setup,
            "conditions": {"track_temp_c": 40.0, "air_temp_c": 27.0},
        },
        "track_geometry": {
            "status": "usable",
            "quality": {"main_loop_complete": True},
            "track_configuration_key": "95-oval",
            "coordinate_system": "normalized_local_vector",
            "geometry_hash": "a" * 64,
            "main_path": [
                {"lap_pct": 0.0, "x": 0.0, "y": 0.0},
                {"lap_pct": 0.5, "x": 1.0, "y": 1.0},
            ],
        },
        "runs": [
            {
                "run_number": 1,
                "run_id": "run-1",
                "coaching_reference_lap_numbers": list(range(1, 10)),
                "damage_repair_context": {"automatic_coaching_reference_eligible": True},
            },
            {
                "run_number": 2,
                "run_id": "run-2",
                "coaching_reference_lap_numbers": list(range(20, 26)),
                "damage_repair_context": {"automatic_coaching_reference_eligible": True},
            },
        ],
        "corner_tire_age": {
            "status": "usable",
            "runs": [
                {
                    "run_number": 1,
                    "zones": [
                        {
                            "zone_id": "load-zone-1",
                            "start_pct": 0.05,
                            "end_pct": 0.25,
                            "observational_run_phases": phases,
                        }
                    ],
                }
            ],
        },
    }


def _map() -> dict:
    value = {
        "map_identity": "iowa-official-v1",
        "track_configuration_key": "95-oval",
        "geometry_hash": "a" * 64,
        "source_type": "iracing-official",
        "source_label": "iRacing track map",
        "verified": True,
        "corners": [
            {
                "corner_id": "turn-1",
                "label": "Turn 1",
                "start_pct": 0.05,
                "apex_pct": 0.15,
                "end_pct": 0.25,
                "is_official": True,
                "user_verified": False,
            }
        ],
    }
    value["annotation_hash"] = tuning_engine.map_annotation_hash(value)
    return value


def _feedback(symptom: str = "tight", priority: int = 3) -> list[dict]:
    return [
        {
            "feedback_id": f"feedback-{symptom}",
            "corner_id": "turn-1",
            "start_pct": 0.05,
            "apex_pct": 0.15,
            "end_pct": 0.25,
            "run_phase": "late",
            "corner_phases": ["center"],
            "symptom_id": symptom,
            "severity": 3,
            "driver_confidence": 4,
            "priority": priority,
            "note": "Needs more center rotation late in the run.",
        }
    ]


class StructuredTuningEngineTests(unittest.TestCase):
    def test_annotation_hash_fixture_is_order_independent_and_cross_language_fixed(self) -> None:
        fixture = {
            "track_configuration_key": "95-oval",
            "geometry_hash": "a" * 64,
            "source_type": "iracing-official",
            "corners": [
                {"corner_id": "turn-3", "label": "Turn 3", "start_pct": .55, "apex_pct": .65, "end_pct": .75, "is_official": True, "user_verified": False},
                {"corner_id": "turn-1", "label": "Turn 1", "start_pct": .05, "apex_pct": .15, "end_pct": .25, "is_official": True, "user_verified": False},
            ],
        }
        self.assertEqual(
            tuning_engine.map_annotation_hash(fixture),
            "e15b85a5711571f5a7248007b0c5392217469cd53a2dadd403a9031ccec73d5b",
        )
        fixture["corners"].reverse()
        self.assertEqual(
            tuning_engine.map_annotation_hash(fixture),
            "e15b85a5711571f5a7248007b0c5392217469cd53a2dadd403a9031ccec73d5b",
        )

    def test_corner_bounds_require_forward_start_apex_end_order_and_allow_wrap(self) -> None:
        wrapped = {
            "track_configuration_key": "95-oval",
            "geometry_hash": "a" * 64,
            "source_type": "iracing-official",
            "corners": [
                {"corner_id": "sf-turn", "label": "Start/finish turn", "start_pct": .90, "apex_pct": .98, "end_pct": .08, "is_official": True}
            ],
        }
        self.assertRegex(tuning_engine.map_annotation_hash(wrapped), r"^[0-9a-f]{64}$")
        invalid = copy.deepcopy(wrapped)
        invalid["corners"][0].update(start_pct=.10, apex_pct=.40, end_pct=.30)
        with self.assertRaisesRegex(
            tuning_engine.StructuredTuningError, "forward start-to-apex-to-end"
        ):
            tuning_engine.map_annotation_hash(invalid)

    def test_ready_evidence_selects_longest_clean_run_and_one_manual_change(self) -> None:
        result = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback(), _map(), goal="tire-life"
        )
        self.assertTrue(result["eligibility"]["can_receive_garage_recommendation"])
        self.assertEqual(result["goal"], "tire-life")
        self.assertEqual(result["representative_runs"][0]["run_id"], "run-1")
        self.assertEqual(result["representative_runs"][0]["selection_mode"], "automatic-longest-clean-run")
        self.assertTrue(result["candidate_whitelist"])
        candidate = result["candidate_whitelist"][0]
        self.assertEqual(candidate["setting_id"], "cross-weight")
        self.assertIsNone(candidate["proposed_values"])
        self.assertTrue(candidate["manual_application_only"])
        self.assertTrue(candidate["test_protocol"]["one_change_rule"])
        self.assertEqual(
            candidate["rollback"]["setup_fingerprint"],
            _analysis()["identity"]["setup_fingerprint"],
        )
        self.assertTrue(candidate["evidence_ids"])

    def test_goal_changes_candidate_ranking_after_equal_driver_priority(self) -> None:
        reports = _feedback("tight", 3)
        reports[0]["feedback_id"] = "late-center"
        reports.append(
            {
                "feedback_id": "early-entry",
                "corner_id": "turn-1",
                "start_pct": 0.05,
                "apex_pct": 0.15,
                "end_pct": 0.25,
                "run_phase": "early",
                "corner_phases": ["entry"],
                "symptom_id": "loose",
                "severity": 3,
                "driver_confidence": 4,
                "priority": 3,
                "note": "Loose under braking at the start of a run.",
            }
        )
        long_run = tuning_engine.build_structured_tuning_evidence(
            _analysis(), reports, _map(), goal="long-run-pace"
        )
        restart = tuning_engine.build_structured_tuning_evidence(
            _analysis(), reports, _map(), goal="restart-pace"
        )
        self.assertEqual(long_run["candidate_whitelist"][0]["setting_id"], "cross-weight")
        self.assertEqual(restart["candidate_whitelist"][0]["setting_id"], "front-brake-bias")
        self.assertFalse(restart["candidate_whitelist"][0]["goal_relevance"]["causal_evidence"])

    def test_fixed_race_requires_distinct_compatible_open_target(self) -> None:
        fixed = _analysis(fixed=True, analysis_id="race-fixed")
        blocked = tuning_engine.build_structured_tuning_evidence(fixed, _feedback(), _map())
        self.assertIn("open-setup-target-required", blocked["missing_required"])
        target = _analysis(fixed=False, analysis_id="open-target")
        ready = tuning_engine.build_structured_tuning_evidence(
            fixed, _feedback(), _map(), open_target_analysis=target
        )
        self.assertTrue(ready["eligibility"]["can_receive_garage_recommendation"])
        self.assertEqual(ready["open_target_ref"]["analysis_id"], "open-target")

    def test_identity_gates_and_unsupported_car_block_without_invention(self) -> None:
        bad_map = _map()
        bad_map["geometry_hash"] = "b" * 64
        bad_map["annotation_hash"] = tuning_engine.map_annotation_hash(bad_map)
        blocked = tuning_engine.build_structured_tuning_evidence(_analysis(), _feedback(), bad_map)
        self.assertIn("exact-track-geometry-hash-mismatch", blocked["missing_required"])
        unsupported = _analysis()
        unsupported["identity"]["car_path"] = "stockcars chevycamarozl12022"
        blocked = tuning_engine.build_structured_tuning_evidence(unsupported, _feedback(), _map())
        self.assertIn("unsupported-car-ruleset", blocked["missing_required"])

    def test_equal_priority_opposing_feedback_blocks_but_unique_priority_resolves(self) -> None:
        loose = _feedback("loose", 3)[0]
        equal = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback("tight", 3) + [loose], _map(), goal="stability"
        )
        self.assertIn("unresolved-feedback-conflict", equal["missing_required"])
        lower = dict(loose, priority=2)
        resolved = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback("tight", 4) + [lower], _map(), goal="stability"
        )
        self.assertNotIn("unresolved-feedback-conflict", resolved["missing_required"])

    def test_prior_failure_suppresses_same_scoped_candidate(self) -> None:
        first = tuning_engine.build_structured_tuning_evidence(_analysis(), _feedback(), _map())
        candidate = first["candidate_whitelist"][0]
        history = [
            {
                "outcome": "worse",
                "recommendation": {"selected_candidate": candidate},
            }
        ]
        second = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback(), _map(), previous_experiments=history
        )
        self.assertFalse(second["candidate_whitelist"])
        self.assertTrue(second["suppressed_candidates"])
        self.assertIn("no-supported-single-change-candidate", second["missing_required"])

    def test_other_feedback_is_preserved_but_cannot_invent_a_candidate(self) -> None:
        result = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback("other"), _map()
        )
        self.assertEqual(result["feedback"][0]["symptom_id"], "other")
        self.assertTrue(any(item["source"] == "driver-report" for item in result["observations"]))
        self.assertFalse(result["candidate_whitelist"])
        self.assertIn("no-supported-single-change-candidate", result["missing_required"])

    def test_invalid_representative_override_does_not_silently_substitute_a_run(self) -> None:
        result = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback(), _map(), representative_run_ids=["run-1", "run-unknown"]
        )
        self.assertFalse(result["representative_runs"])
        self.assertIn("representative-clean-run-required", result["missing_required"])
        self.assertTrue(any("run-unknown" in item for item in result["limitations"]))

    def test_feedback_requires_exact_bounds_from_the_current_map(self) -> None:
        report = _feedback()[0]
        report.pop("apex_pct")

        result = tuning_engine.build_structured_tuning_evidence(
            _analysis(), [report], _map()
        )

        self.assertFalse(result["feedback"])
        self.assertIn("feedback-1-corner-bounds-required", result["missing_required"])

    def test_ai_selection_is_strict_and_invalid_response_falls_back(self) -> None:
        evidence = tuning_engine.build_structured_tuning_evidence(_analysis(), _feedback(), _map())
        deterministic = tuning_engine.select_structured_recommendation(
            evidence, {"selected_candidate_id": "invented", "summary": "x", "evidence_ids": ["invented"], "conflicts": [], "confidence_reasons": ["x"]}
        )
        self.assertEqual(deterministic["selection_source"], "deterministic-fallback")
        self.assertFalse(deterministic["ai_validation"]["valid"])
        candidate = evidence["candidate_whitelist"][0]
        valid = {
            "selected_candidate_id": candidate["candidate_id"],
            "summary": "Test the single whitelisted change.",
            "evidence_ids": [candidate["evidence_ids"][0]],
            "conflicts": [],
            "confidence_reasons": ["Exact map and setup identity passed."],
        }
        selected = tuning_engine.select_structured_recommendation(evidence, valid)
        self.assertEqual(selected["selection_source"], "validated-bounded-ai")
        request = tuning_engine.build_bounded_tuning_ai_request(evidence)
        self.assertLess(len(json.dumps(request).encode("utf-8")), 64 * 1024)
        encoded = json.dumps(request).casefold()
        self.assertNotIn("setup_fingerprint", encoded)
        self.assertNotIn('"chassis":', encoded)
        self.assertNotIn("source_sha256", encoded)

    def test_ai_cannot_cite_evidence_linked_only_to_another_candidate(self) -> None:
        evidence = tuning_engine.build_structured_tuning_evidence(
            _analysis(), _feedback(), _map()
        )
        selected = evidence["candidate_whitelist"][0]
        foreign = copy.deepcopy(selected)
        foreign["candidate_id"] = "candidate-foreign"
        foreign["evidence_ids"] = ["evidence-foreign"]
        evidence["candidate_whitelist"].append(foreign)
        evidence["observations"].append(
            {
                "evidence_id": "evidence-foreign",
                "feedback_id": "feedback-foreign",
                "source": "driver-report",
            }
        )

        result = tuning_engine.validate_tuning_ai_response(
            {
                "selected_candidate_id": selected["candidate_id"],
                "summary": "Borrow evidence from another allowed change.",
                "evidence_ids": ["evidence-foreign"],
                "conflicts": [],
                "confidence_reasons": ["Unrelated evidence."],
            },
            evidence,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("selected candidate" in item for item in result["errors"]))

    def test_ai_request_is_bounded_and_citations_are_limited_to_supplied_evidence(self) -> None:
        reports = []
        for index in range(40):
            item = _feedback()[0]
            item["feedback_id"] = f"feedback-{index:02d}"
            item["note"] = "detailed report " + ("x" * 500)
            reports.append(item)
        evidence = tuning_engine.build_structured_tuning_evidence(
            _analysis(), reports, _map(), generic_note="g" * 8000
        )
        request = tuning_engine.build_bounded_tuning_ai_request(evidence)
        self.assertLessEqual(
            len(json.dumps(request, separators=(",", ":")).encode("utf-8")),
            64 * 1024,
        )
        supplied = {item["evidence_id"] for item in request["evidence"]}
        candidate = evidence["candidate_whitelist"][0]
        omitted = next(item for item in candidate["evidence_ids"] if item not in supplied)
        invalid = tuning_engine.validate_tuning_ai_response(
            {
                "selected_candidate_id": candidate["candidate_id"],
                "summary": "Cites evidence that was not in the bounded request.",
                "evidence_ids": [omitted],
                "conflicts": [],
                "confidence_reasons": ["Not actually supplied."],
            },
            evidence,
        )
        self.assertFalse(invalid["valid"])


class StructuredTuningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.report = self.archive / "reports" / "race" / "analysis.json"
        self.report.parent.mkdir(parents=True)
        self.report.write_text(json.dumps(_analysis()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_workflow_is_idempotent_across_deterministic_and_ai_calls(self) -> None:
        first = tuning_workflow.recommend_structured_open_setup_tuning_workflow(
            analysis_path=self.report,
            feedback=_feedback(),
            map_identity=_map(),
            archive_root=self.archive,
        )
        self.assertTrue(first["ok"])
        self.assertTrue(first["persisted"])
        self.assertTrue(Path(first["draft_path"]).is_file())
        candidate = first["candidate_whitelist"][0]
        ai = {
            "selected_candidate_id": candidate["candidate_id"],
            "summary": "Use the bounded candidate.",
            "evidence_ids": [candidate["evidence_ids"][0]],
            "conflicts": [],
            "confidence_reasons": ["Exact identities passed."],
        }
        second = tuning_workflow.recommend_structured_open_setup_tuning_workflow(
            analysis_path=self.report,
            feedback=_feedback(),
            map_identity=_map(),
            archive_root=self.archive,
            ai_response=ai,
        )
        self.assertEqual(second["experiment_id"], first["experiment_id"])
        self.assertEqual(second["experiment_path"], first["experiment_path"])
        self.assertEqual(second["evidence_hash"], first["evidence_hash"])
        self.assertEqual(second["draft_path"], first["draft_path"])
        context = ArchiveStore(self.archive).context_from_analysis(_analysis())
        context["setup_type"] = "open"
        history = ArchiveStore(self.archive).tuning_history(context)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0]["recommendation"]["selection"]["selection_source"],
            "validated-bounded-ai",
        )

    def test_blocked_request_still_persists_draft_but_not_experiment(self) -> None:
        bad_map = _map()
        bad_map["geometry_hash"] = "b" * 64
        bad_map["annotation_hash"] = tuning_engine.map_annotation_hash(bad_map)
        result = tuning_workflow.recommend_structured_open_setup_tuning_workflow(
            analysis_path=self.report,
            feedback=_feedback(),
            map_identity=bad_map,
            archive_root=self.archive,
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["persisted"])
        self.assertTrue(Path(result["draft_path"]).is_file())

    def test_fixed_evidence_workflow_uses_distinct_exact_open_target(self) -> None:
        fixed_path = self.archive / "reports" / "fixed" / "analysis.json"
        target_path = self.archive / "reports" / "target" / "analysis.json"
        fixed_path.parent.mkdir(parents=True)
        target_path.parent.mkdir(parents=True)
        fixed_path.write_text(
            json.dumps(_analysis(fixed=True, analysis_id="fixed-race")), encoding="utf-8"
        )
        target_path.write_text(
            json.dumps(_analysis(fixed=False, analysis_id="open-target")), encoding="utf-8"
        )
        result = tuning_workflow.recommend_structured_open_setup_tuning_workflow(
            analysis_path=fixed_path,
            open_target_analysis_path=target_path,
            feedback=_feedback(),
            map_identity=_map(),
            archive_root=self.archive,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["tuning_evidence_v2"]["open_target_ref"]["analysis_id"],
            "open-target",
        )
        self.assertTrue(
            result["tuning_evidence_v2"]["driving_evidence_ref"]["is_fixed_setup"]
        )

    def test_result_feedback_links_exact_open_analysis_without_claiming_causality(self) -> None:
        plan = tuning_workflow.recommend_structured_open_setup_tuning_workflow(
            analysis_path=self.report,
            feedback=_feedback(),
            map_identity=_map(),
            archive_root=self.archive,
        )
        result_analysis = _analysis(analysis_id="result-open")
        result_analysis["identity"]["setup"]["Chassis"]["Front"]["CrossWeight"] = "49.6%"
        result_analysis["identity"]["setup_fingerprint"] = tuning_engine.embedded_setup_fingerprint(
            result_analysis["identity"]["setup"]
        )[:16]
        result_path = self.archive / "reports" / "result" / "analysis.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(json.dumps(result_analysis), encoding="utf-8")
        feedback = tuning_workflow.record_open_setup_feedback_workflow(
            experiment_id=plan["experiment_id"],
            outcome="improved",
            notes="Center rotation improved.",
            result_analysis_path=result_path,
            archive_root=self.archive,
        )
        self.assertEqual(feedback["comparison"]["contract"], "tuning_result_comparison_v2")
        self.assertTrue(feedback["comparison"]["setup_fingerprint_changed"])
        self.assertIn("do not prove", feedback["comparison"]["causality"])

    def test_storage_draft_roundtrip_and_mcp_contract(self) -> None:
        store = ArchiveStore(self.archive)
        saved = store.save_tuning_draft({"draft_id": "test-draft", "feedback": _feedback()})
        self.assertEqual(store.load_tuning_draft("test-draft")["feedback"], _feedback())
        self.assertTrue(Path(saved["path"]).is_file())
        tool = next(item for item in mcp_server.TOOLS if item["name"] == "recommend_structured_open_setup_tuning")
        self.assertIn("open_target_analysis_path", tool["inputSchema"]["properties"])
        self.assertEqual(
            tool["inputSchema"]["properties"]["goal"]["enum"],
            ["long-run-pace", "tire-life", "restart-pace", "stability"],
        )

    def test_mcp_dispatch_forwards_bounded_structured_contract(self) -> None:
        workflow = mock.Mock(return_value={"ok": True})
        with mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", self.archive), mock.patch.object(
            mcp_server, "_workflow_function", return_value=workflow
        ):
            result = mcp_server.call_tool(
                "recommend_structured_open_setup_tuning",
                {
                    "analysis_path": str(self.report),
                    "feedback": _feedback(),
                    "map_identity": _map(),
                    "goal": "stability",
                    "representative_run_ids": ["run-1"],
                },
            )
        self.assertTrue(result["ok"])
        self.assertEqual(workflow.call_args.kwargs["goal"], "stability")
        self.assertEqual(workflow.call_args.kwargs["representative_run_ids"], ["run-1"])


if __name__ == "__main__":
    unittest.main()
