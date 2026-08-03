from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import race_card  # noqa: E402


def _phase(phase: str, entry: float, minimum: float, exit_speed: float) -> dict:
    return {
        "phase": phase,
        "status": "usable",
        "lap_count": 3,
        "sample_count": 120,
        "green_lap_on_set_bounds": {
            "by_tire": {
                tire: {"start": 1 if phase == "fresh" else 4 if phase == "settled" else 15,
                       "end": 3 if phase == "fresh" else 14 if phase == "settled" else 22}
                for tire in ("LF", "RF", "LR", "RR")
            }
        },
        "event_availability": {"turn_in_boundary_censored_laps": 0},
        "metrics": {
            "entry_speed_mph": entry,
            "minimum_speed_mph": minimum,
            "exit_speed_mph": exit_speed,
            "brake_peak_fraction": 0.31,
            "turn_in_lap_pct": 0.145,
        },
    }


def _analysis() -> dict:
    return {
        "analysis_id": "race-card-test",
        "identity": {
            "subsession_id": 42,
            "track_name": "New Hampshire Motor Speedway",
            "track_config": "Oval",
            "car_name": "NASCAR O'Reilly Toyota",
            "is_fixed_setup": False,
        },
        "source": {"telemetry_files": ["race.ibt"]},
        "race_summary": {
            "scheduled_laps": 100,
            "recorded_laps": 95,
            "green_laps_estimated": 82,
            "caution_laps_estimated": 13,
            "runs_detected": 3,
            "pit_stops_detected": 2,
        },
        "runs": [
            {
                "run_number": 1,
                "tire_observation": {
                    "lowest_remaining_tire": "RF",
                    "lowest_remaining_percent": 54.2,
                },
            }
        ],
        "strategy": {
            "measured_green_fuel_gal_per_lap": 0.44,
            "forecast": {
                "status": "usable",
                "minimum_stops_all_green": 1,
                "equal_stint_pit_targets_all_green": [50],
                "operational_reserve_green_laps": 2,
            },
        },
        "corner_tire_age": {
            "status": "usable",
            "runs": [
                {
                    "run_number": 1,
                    "phase_model": "confirmed_age_run_thirds_proxy",
                    "new_set_confirmed": True,
                    "eligible_lap_count": 20,
                    "zones": [
                        {
                            "zone_id": "load-zone-1",
                            "zone_label": "Load zone 1",
                            "corner_name_status": "provisional_telemetry_load_zone",
                            "start_pct": 0.14,
                            "end_pct": 0.36,
                            "tire_age_phases": [],
                            "observational_run_phases": [
                                _phase("early", 120.0, 104.0, 116.0),
                                _phase("middle", 118.0, 103.0, 115.0),
                                _phase("late", 116.0, 101.0, 112.0),
                            ],
                            "coaching": {
                                "status": "usable",
                                "action": "Finish brake release before adding steering load.",
                                "exact_target_emitted": False,
                            },
                        }
                    ],
                }
            ],
        },
        "groove_evolution": {
            "status": "available",
            "coordinate_evidence": {"absolute_groove_claimed": False},
            "zones": [
                {
                    "start_pct": 0.14,
                    "end_pct": 0.36,
                    "runs": [
                        {
                            "run_number": 1,
                            "migration": {
                                "status": "detected",
                                "direction": "left-relative-to-session-reference",
                                "first_sustained_lap": 18,
                            },
                        }
                    ],
                }
            ],
        },
        "data_quality": {"confidence": "medium"},
        "coaching_signals": [],
    }


def _knowledge(status: str) -> dict:
    return {
        "facts": {
            "coaching_context": {
                "telemetry_load_zones": [
                    {
                        "lap_distance_pct": "14\u201336",
                        "provisional_corner_group": "T1\u20132",
                        "corner_name_status": "provisional_cached_alignment",
                        "name_source": "cached NHMS coaching context",
                    }
                ]
            }
        },
        "garage61": {
            "comparison_quality": {"status": status, "setup_scope": "same_setup_only"},
            "coaching_targets": [
                {
                    "name": "Load zone 1",
                    "start_pct": 0.14,
                    "end_pct": 0.36,
                    "entry_speed_mph": 199.9,
                    "minimum_speed_mph": 155.5,
                }
            ],
        },
    }


class RaceCardTests(unittest.TestCase):
    def test_oval_card_is_bounded_ascii_multiline_and_uses_provisional_cached_label(self) -> None:
        card = race_card.build_race_card(_analysis(), knowledge=_knowledge("partial"))
        markdown = race_card.render_race_card(card)

        markdown.encode("ascii")
        self.assertIn("\n## Corner playbook\n", markdown)
        self.assertIn("- Early/new-set:", markdown)
        self.assertIn("T1-2 (provisional)", markdown)
        self.assertIn("[D] Obs E120/M104/X116; B31; BO-/BR-/TI14.5", markdown)
        self.assertNotIn("[U]", markdown)
        self.assertNotIn("unavailable", markdown.lower())
        self.assertNotIn("199.9", markdown)
        self.assertLessEqual(card["word_count_before_evidence"], 300)

    def test_exact_numeric_target_requires_usable_comparison(self) -> None:
        card = race_card.build_race_card(_analysis(), knowledge=_knowledge("usable"))

        settled = card["corner_playbook"]["rows"][0]["phase_2"]
        self.assertEqual(settled["evidence_type"], "inferred")
        self.assertIn("Target 199.9 entry / 155.5 min mph", settled["text"])
        self.assertTrue(card["target_policy"]["exact_numeric_targets_emitted"])

    def test_observational_late_phase_is_never_relabelled_worn(self) -> None:
        analysis = _analysis()
        run = analysis["corner_tire_age"]["runs"][0]
        run["phase_model"] = "run_thirds_proxy"
        run["new_set_confirmed"] = False
        zone = run["zones"][0]
        zone["tire_age_phases"] = []
        zone["observational_run_phases"] = [
            {"phase": "early", "status": "unavailable", "reason": "No samples"},
            {"phase": "middle", "status": "unavailable", "reason": "No samples"},
            _phase("late", 90.0, 70.0, 80.0),
        ]

        card = race_card.build_race_card(analysis, knowledge={})
        row = card["corner_playbook"]["rows"][0]

        self.assertEqual(row["phase_3"]["evidence_type"], "derived")
        self.assertIn("90", row["phase_3"]["text"])
        self.assertNotIn("worn", race_card.render_race_card(card).lower())

    def test_absent_corner_fields_degrade_to_explicit_unavailable_cells(self) -> None:
        analysis = _analysis()
        analysis.pop("corner_tire_age")
        analysis["track_profile"] = {
            "detected_corner_segments": [{"segment": 1, "start_pct": 0.14, "end_pct": 0.36}]
        }

        card = race_card.build_race_card(analysis, knowledge={})
        row = card["corner_playbook"]["rows"][0]

        self.assertEqual(row["phase_1"]["evidence_type"], "unavailable")
        self.assertEqual(row["phase_2"]["evidence_type"], "unavailable")
        self.assertEqual(row["phase_3"]["evidence_type"], "unavailable")

        markdown = race_card.render_race_card(card)
        self.assertNotIn("Corner playbook", markdown)
        self.assertNotIn("unavailable", markdown.lower())

    def test_many_oval_zones_are_compacted_without_silent_pruning(self) -> None:
        analysis = _analysis()
        source = analysis["corner_tire_age"]["runs"][0]["zones"][0]
        analysis["corner_tire_age"]["runs"][0]["zones"] = [
            {
                **copy.deepcopy(source),
                "zone_id": f"load-zone-{index}",
                "zone_label": f"Load zone {index}",
                "start_pct": (index - 1) / 20,
                "end_pct": index / 20,
            }
            for index in range(1, 21)
        ]

        card = race_card.build_race_card(analysis, knowledge={})

        self.assertEqual(len(card["corner_playbook"]["rows"]), 20)
        self.assertEqual(card["corner_playbook"]["omitted_row_count"], 0)
        self.assertTrue(card["within_word_limit"])
        self.assertLessEqual(card["word_count_before_evidence"], 300)
        self.assertIn("Additional measured load zones remain in the full analysis", race_card.render_race_card(card))

    def test_damage_screening_prefers_clean_then_partial_and_withholds_affected_targets(self) -> None:
        analysis = _analysis()
        source = analysis["corner_tire_age"]["runs"][0]
        affected = copy.deepcopy(source)
        affected.update({"run_number": 1, "eligible_lap_count": 30})
        partial = copy.deepcopy(source)
        partial.update({"run_number": 2, "eligible_lap_count": 8})
        clean = copy.deepcopy(source)
        clean.update({"run_number": 3, "eligible_lap_count": 3})
        analysis["corner_tire_age"]["runs"] = [affected, partial, clean]
        analysis["damage_repair"] = {
            "status": "recorded",
            "summary": {
                "recorded_repair_episodes": 1,
                "repair_required_flag_episodes": 1,
                "tow_episodes": 0,
                "confirmed_fast_repair_uses": 0,
            },
            "episodes": [],
            "run_impacts": [
                {
                    "run_number": 1,
                    "status": "repair_affected",
                    "automatic_coaching_reference_eligible": False,
                },
                {
                    "run_number": 2,
                    "status": "partial_pre_incident_proxy",
                    "automatic_coaching_reference_eligible": True,
                },
                {
                    "run_number": 3,
                    "status": "clean",
                    "automatic_coaching_reference_eligible": True,
                },
            ],
        }

        clean_card = race_card.build_race_card(
            analysis, knowledge=_knowledge("usable")
        )
        self.assertEqual(clean_card["corner_playbook"]["selected_run_number"], 3)

        analysis["corner_tire_age"]["runs"] = [affected, partial]
        analysis["damage_repair"]["run_impacts"] = analysis["damage_repair"][
            "run_impacts"
        ][:2]
        partial_card = race_card.build_race_card(
            analysis, knowledge=_knowledge("usable")
        )
        self.assertEqual(partial_card["corner_playbook"]["selected_run_number"], 2)
        self.assertTrue(partial_card["target_policy"]["exact_numeric_targets_emitted"])

        analysis["corner_tire_age"]["runs"] = [affected]
        analysis["damage_repair"]["run_impacts"] = analysis["damage_repair"][
            "run_impacts"
        ][:1]
        affected_card = race_card.build_race_card(
            analysis, knowledge=_knowledge("usable")
        )
        row = affected_card["corner_playbook"]["rows"][0]
        self.assertEqual(affected_card["corner_playbook"]["selected_run_number"], 1)
        self.assertFalse(
            affected_card["corner_playbook"][
                "selected_run_automatic_coaching_reference_eligible"
            ]
        )
        self.assertFalse(affected_card["target_policy"]["exact_numeric_targets_emitted"])
        self.assertEqual(row["phase_2"]["evidence_type"], "unavailable")
        self.assertNotIn("Target 199.9", row["phase_2"]["text"])
        self.assertEqual(row["groove"]["evidence_type"], "unavailable")
        self.assertIn(
            "repair-affected",
            affected_card["bottom_line"]["text"].lower(),
        )


if __name__ == "__main__":
    unittest.main()
