from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from analysis_engine import analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402
from workflow import ANALYSIS_CHANNELS  # noqa: E402


def telemetry_with_damage_evidence() -> dict:
    telemetry = synthetic_telemetry(lap_count=8)
    channels = telemetry["channels"]
    count = len(channels["SessionTime"])
    samples_per_lap = 80
    stop_start = samples_per_lap * 4 - 8
    stop_end = samples_per_lap * 4 + 12
    tow_start = stop_start - 30
    tow_end = stop_start - 2

    channels["PlayerCarTowTime"] = [0.0] * count
    channels["PitRepairLeft"] = [0.0] * count
    channels["PitOptRepairLeft"] = [0.0] * count
    channels["PlayerCarPitSvStatus"] = [0] * count
    channels["PlayerFastRepairsUsed"] = [0] * count
    channels["FastRepairUsed"] = [0] * count
    channels["FastRepairAvailable"] = [1] * count
    channels["PlayerCarMyIncidentCount"] = [0] * count
    channels["dpFastRepair"] = [0.0] * count

    for index in range(tow_start, tow_end + 1):
        channels["PlayerCarTowTime"][index] = (tow_end - index + 1) / 20.0
    for index in range(stop_start, stop_end + 1):
        elapsed = (index - stop_start) / 20.0
        channels["PitRepairLeft"][index] = max(0.0, 0.30 - elapsed)
        # The driver leaves with recorded optional repair time remaining.
        channels["PitOptRepairLeft"][index] = 20.0 - elapsed
        channels["PlayerCarPitSvStatus"][index] = (
            105 if index == stop_start else 102 if index == stop_start + 1 else 2
        )
        channels["PitSvFlags"][index] |= 0x40
        channels["dpFastRepair"][index] = 1.0
    for index in range(stop_end + 1, count):
        channels["PlayerFastRepairsUsed"][index] = 1
        channels["FastRepairUsed"][index] = 1
        channels["FastRepairAvailable"][index] = 0
    for index in range(tow_start - 1, count):
        channels["PlayerCarMyIncidentCount"][index] = 4
    for index in range(tow_start - 1, stop_end + 1):
        channels["SessionFlags"][index] |= 0x00100000

    telemetry["variables"].extend(
        {"name": name, "unit": "s"}
        for name in ("PlayerCarTowTime", "PitRepairLeft", "PitOptRepairLeft")
    )
    return telemetry


class DamageRepairAnalysisTests(unittest.TestCase):
    def test_records_tow_repair_timing_and_post_stop_reference_exclusion(self) -> None:
        analysis = analyze_telemetry(telemetry_with_damage_evidence())
        damage = analysis["damage_repair"]

        self.assertEqual(damage["status"], "usable")
        self.assertEqual(damage["summary"]["tow_episodes"], 1)
        self.assertEqual(damage["summary"]["recorded_repair_episodes"], 1)
        self.assertEqual(damage["summary"]["confirmed_fast_repair_uses"], 1)
        episode = damage["episodes"][0]
        self.assertEqual(
            episode["classification"], "tow_and_recorded_repair_timer"
        )
        self.assertGreater(episode["timing"]["pit_road_time_s"], 0.0)
        self.assertGreater(episode["timing"]["pit_stall_time_s"], 0.0)
        self.assertGreater(episode["timing"]["pitstop_service_active_time_s"], 0.0)
        self.assertGreater(episode["timing"]["tow_active_time_s"], 0.0)
        self.assertGreater(episode["timing"]["repair_active_time_s"], 0.0)
        self.assertIn("overlap", episode["timing"]["nonexclusive_note"].lower())

        mandatory = episode["mandatory_repair"]
        optional = episode["optional_repair"]
        self.assertEqual(mandatory["status"], "recorded_positive_timer")
        self.assertGreater(mandatory["peak_remaining_s"], 0.0)
        self.assertEqual(optional["completion_status"], "remaining_at_stall_exit")
        self.assertGreater(optional["remaining_at_stall_exit_s"], 18.0)
        self.assertGreater(optional["countdown_observed_s"], 0.0)
        self.assertGreater(optional["repair_work_completed_s"], 0.0)
        self.assertLess(
            optional["repair_work_completed_s"], optional["peak_remaining_s"]
        )
        self.assertNotIn("countdown_progress_sample_indices", optional)
        self.assertGreater(optional["countdown_progress_sample_count"], 0)

        status_labels = {
            item["label"] for item in episode["pit_service_status"]["observed"]
        }
        self.assertIn("too_far_forward", status_labels)
        self.assertIn("cant_fix_that", status_labels)
        self.assertEqual(
            episode["repair_required_state"]["status"], "recorded_active"
        )

        fast = episode["fast_repair"]
        self.assertTrue(fast["requested"])
        self.assertTrue(fast["request_confirmed_as_use"])
        self.assertEqual(fast["used_count_delta"], 1.0)
        self.assertFalse(damage["incident_points"]["damage_proof"])
        self.assertEqual(damage["incident_points"]["positive_delta"], 4.0)

        following_run = next(
            item
            for item in damage["run_impacts"]
            if item["run_number"] == episode["run_context"]["following_run_number"]
        )
        self.assertFalse(following_run["automatic_coaching_reference_eligible"])
        self.assertEqual(
            following_run["status"], "excluded_recorded_repair_remaining"
        )
        self.assertIn(
            "optional_repair_remaining_at_prior_stall_exit",
            following_run["reason_codes"],
        )
        self.assertEqual(following_run["coaching_reference_lap_numbers"], [])
        run = next(
            item for item in analysis["runs"] if item["run_number"] == following_run["run_number"]
        )
        self.assertEqual(run["coaching_reference_lap_numbers"], [])
        candidate = episode["incident_points_context"]["repair_correlated_candidate"]
        self.assertEqual(candidate["status"], "inferred_candidate_boundary")
        self.assertFalse(candidate["damage_onset_confirmed"])
        self.assertTrue(candidate["candidate_lap_numbers"])
        candidate_impact = next(
            item
            for item in damage["lap_impacts"]
            if item["lap"] == candidate["candidate_start_lap"]
        )
        self.assertIn(
            "repair_correlated_candidate", candidate_impact["exclusion_reason_codes"]
        )

    def test_normal_pit_visit_with_zero_timers_is_not_called_damage(self) -> None:
        telemetry = synthetic_telemetry(lap_count=8)
        channels = telemetry["channels"]
        count = len(channels["SessionTime"])
        channels["PlayerCarTowTime"] = [0.0] * count
        channels["PitRepairLeft"] = [0.0] * count
        channels["PitOptRepairLeft"] = [0.0] * count
        channels["PlayerFastRepairsUsed"] = [0] * count
        channels["FastRepairAvailable"] = [1] * count
        channels["PlayerCarMyIncidentCount"] = [0] * count

        damage = analyze_telemetry(telemetry)["damage_repair"]
        self.assertEqual(damage["status"], "usable")
        episode = damage["episodes"][0]
        self.assertEqual(episode["classification"], "pit_visit_no_recorded_repair")
        self.assertEqual(episode["mandatory_repair"]["status"], "recorded_zero")
        self.assertEqual(episode["optional_repair"]["status"], "recorded_zero")
        self.assertEqual(
            episode["damage_evidence"]["status"],
            "no_recorded_damage_evidence_in_episode",
        )
        following = episode["run_context"]["following_run_number"]
        following_impact = next(
            item for item in damage["run_impacts"] if item["run_number"] == following
        )
        self.assertTrue(following_impact["automatic_coaching_reference_eligible"])
        self.assertIn("never certifies", following_impact["scope_note"])

    def test_incident_points_and_pace_loss_do_not_create_damage_episode(self) -> None:
        telemetry = synthetic_telemetry(lap_count=8)
        channels = telemetry["channels"]
        count = len(channels["SessionTime"])
        channels["PlayerCarMyIncidentCount"] = [0 if index < 100 else 2 for index in range(count)]
        channels["Speed"] = [max(10.0, 80.0 - index * 0.05) for index in range(count)]

        damage = analyze_telemetry(telemetry)["damage_repair"]
        self.assertEqual(damage["status"], "partial")
        self.assertEqual(damage["incident_points"]["positive_delta"], 2.0)
        self.assertFalse(damage["incident_points"]["damage_proof"])
        self.assertTrue(
            all(
                episode["damage_evidence"]["status"]
                == "no_recorded_damage_evidence_in_episode"
                for episode in damage["episodes"]
            )
        )
        missing = {
            item["measurement"] for item in damage["unavailable_measurements"]
        }
        self.assertEqual(
            missing,
            {"tow_timer", "mandatory_repair_timer", "optional_repair_timer"},
        )
        self.assertTrue(any("Pace loss" in item for item in damage["limitations"]))
        self.assertFalse(
            any(
                "repair_correlated_candidate" in item["exclusion_reason_codes"]
                for item in damage["lap_impacts"]
            )
        )

    def test_routine_loader_requests_real_sdk_damage_channels(self) -> None:
        for channel in (
            "PlayerCarTowTime",
            "PitRepairLeft",
            "PitOptRepairLeft",
            "PlayerCarPitSvStatus",
            "PlayerFastRepairsUsed",
            "FastRepairUsed",
            "FastRepairAvailable",
            "PlayerCarMyIncidentCount",
            "PlayerCarDriverIncidentCount",
            "PlayerCarTeamIncidentCount",
            "dpFastRepair",
        ):
            self.assertIn(channel, ANALYSIS_CHANNELS)

    def test_tow_active_at_recording_end_is_not_called_complete(self) -> None:
        telemetry = synthetic_telemetry(lap_count=8)
        channels = telemetry["channels"]
        count = len(channels["SessionTime"])
        channels["PlayerCarTowTime"] = [0.0] * count
        channels["PitRepairLeft"] = [0.0] * count
        channels["PitOptRepairLeft"] = [0.0] * count
        channels["PlayerFastRepairsUsed"] = [0] * count
        channels["FastRepairAvailable"] = [0] * count
        channels["PlayerCarMyIncidentCount"] = [0] * count
        for index in range(count - 20, count):
            channels["PlayerCarTowTime"][index] = 60.0 - (index - (count - 20)) / 20.0

        damage = analyze_telemetry(telemetry)["damage_repair"]
        tow_episode = next(
            episode for episode in damage["episodes"] if episode["tow"]["status"] == "recorded_active"
        )
        self.assertEqual(tow_episode["tow"]["completion_status"], "active_at_recording_end")
        self.assertGreater(tow_episode["tow"]["last_remaining_s"], 50.0)

    def test_pre_grid_pit_state_is_separate_from_race_facing_totals(self) -> None:
        telemetry = synthetic_telemetry(lap_count=8)
        channels = telemetry["channels"]
        count = len(channels["SessionTime"])
        channels["Lap"][:80] = [0] * 80
        channels["PlayerCarTowTime"] = [0.0] * count
        channels["PitRepairLeft"] = [0.0] * count
        channels["PitOptRepairLeft"] = [0.0] * count
        channels["PlayerFastRepairsUsed"] = [0] * count
        channels["FastRepairAvailable"] = [0] * count
        channels["PlayerCarMyIncidentCount"] = [0] * count
        for index in range(20):
            channels["OnPitRoad"][index] = True

        damage = analyze_telemetry(telemetry)["damage_repair"]
        summary = damage["summary"]
        self.assertEqual(summary["pre_race_or_grid_episodes"], 1)
        self.assertGreater(
            summary["all_recording_pit_road_time_s"],
            summary["total_pit_road_time_s"],
        )
        pre_grid = next(
            episode
            for episode in damage["episodes"]
            if episode["session_phase"] == "pre_race_or_grid"
        )
        self.assertEqual(
            pre_grid["classification"],
            "pre_race_pit_state_no_recorded_damage",
        )


if __name__ == "__main__":
    unittest.main()
