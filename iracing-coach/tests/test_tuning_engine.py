from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tuning_engine  # noqa: E402


def _analysis(*, fixed: bool = False) -> dict:
    return {
        "identity": {
            "is_fixed_setup": fixed,
            "setup_name": "NOAPS Iowa R.sto",
            "setup_fingerprint": "abc123",
            "setup_parameter_count": 84,
            "setup": {"Chassis": {"Front": {"CrossWeight": "49.7%"}}},
        },
        "setup_telemetry": {
            "available_channels": ["CFSRrideHeight", "RFpressure"],
            "platform": {"center_front_splitter_min_in": 0.18},
            "shocks": {},
            "tires": {},
            "limits": ["A/B comparison required."],
        },
        "runs": [
            {
                "run_number": 1,
                "tire_observation": {
                    "remaining": {"LF": 94.0, "RF": 86.0, "LR": 95.0, "RR": 91.0}
                },
            }
        ],
    }


class TuningEngineTests(unittest.TestCase):
    def test_symptoms_keep_phase_balance_and_onset(self) -> None:
        symptoms = tuning_engine.parse_handling_symptoms(
            "Tight in the center; loose on exit after lap 12"
        )
        self.assertEqual(symptoms[0]["phases"], ["center"])
        self.assertEqual(symptoms[0]["balances"], ["tight"])
        self.assertIn("exit", symptoms[1]["phases"])
        self.assertIn("long_run", symptoms[1]["phases"])
        self.assertEqual(symptoms[1]["balances"], ["loose"])
        self.assertEqual(symptoms[1]["onset_lap"], 12)

    def test_compound_symptoms_do_not_create_false_phase_balance_pairs(self) -> None:
        symptoms = tuning_engine.parse_handling_symptoms(
            "Tight center and loose off from lap 10"
        )
        self.assertEqual(len(symptoms), 2)
        self.assertEqual(symptoms[0]["balances"], ["tight"])
        self.assertIn("center", symptoms[0]["phases"])
        self.assertNotIn("exit", symptoms[0]["phases"])
        self.assertEqual(symptoms[0]["onset_lap"], 10)
        self.assertEqual(symptoms[1]["balances"], ["loose"])
        self.assertIn("exit", symptoms[1]["phases"])
        self.assertNotIn("center", symptoms[1]["phases"])
        self.assertEqual(symptoms[1]["onset_lap"], 10)
        keys = tuning_engine._symptom_keys(symptoms)
        self.assertNotIn(("center", "loose"), keys)
        self.assertNotIn(("exit", "tight"), keys)

    def test_exact_track_character_is_classified_to_donor_family(self) -> None:
        result = tuning_engine.choose_oreilly_donor(
            {
                "layout": "oval",
                "shape": "flat paperclip",
                "demand": "heavy braking and drive-off",
            }
        )
        self.assertEqual(result["donor"], "New Hampshire")
        self.assertEqual(result["family"], "flat-brake-and-drive")

    def test_unknown_track_does_not_guess_a_donor(self) -> None:
        result = tuning_engine.choose_oreilly_donor({"name": "Future Raceway"})
        self.assertEqual(result["status"], "needs-track-classification")
        self.assertIsNone(result["donor"])

    def test_builder_note_outranks_generic_center_tight_rule(self) -> None:
        result = tuning_engine.recommend_tuning(
            _analysis(),
            "Tight in the center after lap 10",
            builder_notes=(
                "**adjustments** to loosen, RIGHT on LR spring perch offset, "
                "LEFT on RR spring perch offset."
            ),
        )
        self.assertEqual(result["status"], "ready")
        first = result["recommendations"][0]
        self.assertEqual(first["source"], "setup-builder-note")
        self.assertIn("LR spring perch", first["change"])
        self.assertTrue(result["test_protocol"]["one_change_rule"])

    def test_platform_damage_is_prioritized_and_uses_trace(self) -> None:
        result = tuning_engine.recommend_tuning(
            _analysis(), "The splitter bottoms under normal braking; tight center"
        )
        first = result["recommendations"][0]
        self.assertEqual(first["system"], "aero-platform")
        self.assertTrue(any("0.180" in item for item in first["evidence"]))

    def test_fixed_session_is_advisory_only(self) -> None:
        result = tuning_engine.recommend_tuning(
            _analysis(fixed=True), "Loose on entry under braking"
        )
        self.assertEqual(result["status"], "advisory-fixed-session")
        self.assertTrue(result["blockers"])

    def test_unknown_setup_type_never_issues_garage_change(self) -> None:
        analysis = _analysis()
        analysis["identity"].pop("is_fixed_setup")
        result = tuning_engine.recommend_tuning(analysis, "Loose on entry under braking")
        self.assertEqual(result["status"], "needs-open-setup-confirmation")
        self.assertTrue(result["blockers"])

    def test_no_symptom_does_not_tune_from_telemetry_alone(self) -> None:
        result = tuning_engine.recommend_tuning(_analysis(), "")
        self.assertEqual(result["status"], "needs-driver-feedback")
        self.assertEqual(result["recommendations"], [])

    def test_native_events_are_alignment_evidence_not_a_causal_diagnosis(self) -> None:
        native = {
            "status": "available",
            "cache_only": True,
            "event_count": 3,
            "counts_by_type": {
                "brake_onset": 1,
                "brake_release": 1,
                "wheel_speed_divergence": 1,
            },
            "event_samples": [
                {
                    "event_type": "brake_release",
                    "source_record_index": 1234,
                    "lap": 8,
                    "evidence": {
                        "label": "derived",
                        "measured_channels": ["Brake"],
                        "method": "two-native-record hysteresis transition",
                    },
                    "measurements": {"value": 0.01},
                }
            ],
        }
        result = tuning_engine.recommend_tuning(
            _analysis(),
            "Loose on entry under braking",
            native_event_evidence=native,
        )
        first = result["recommendations"][0]
        self.assertTrue(any("exact-record A/B alignment" in item for item in first["evidence"]))
        self.assertTrue(any("not proof" in item for item in first["evidence"]))
        self.assertTrue(any("cached native event records" in item for item in first["verify"]))
        self.assertEqual(result["telemetry_evidence"]["native_events"]["event_count"], 3)
        self.assertFalse(
            result["telemetry_evidence"]["native_events"]["detector_invoked_by_tuning"]
        )
        self.assertIn("not causal classifications", result["causality"])

    def test_native_events_without_driver_feedback_never_trigger_a_change(self) -> None:
        result = tuning_engine.recommend_tuning(
            _analysis(),
            "",
            native_event_evidence={
                "status": "available",
                "event_count": 1,
                "counts_by_type": {"shock_velocity_peak": 1},
            },
        )
        self.assertEqual(result["status"], "needs-driver-feedback")
        self.assertEqual(result["recommendations"], [])

    def test_material_damage_context_blocks_setup_recommendations(self) -> None:
        analysis = _analysis()
        analysis["damage_repair"] = {
            "status": "recorded",
            "summary": {
                "tow_episodes": 0,
                "recorded_repair_episodes": 1,
                "repair_required_flag_episodes": 1,
                "confirmed_fast_repair_uses": 0,
            },
            "incident_points": {"positive_delta": 2},
        }

        result = tuning_engine.recommend_tuning(
            analysis, "Tight in the center after lap 10"
        )

        self.assertEqual(result["status"], "needs-clean-repaired-run")
        self.assertEqual(result["recommendations"], [])
        self.assertTrue(
            any("unsuitable for a controlled setup conclusion" in item for item in result["blockers"])
        )
        self.assertEqual(
            result["telemetry_evidence"]["damage_repair"]["summary"][
                "recorded_repair_episodes"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
