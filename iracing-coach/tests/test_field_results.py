from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"))
from field_results import build_field_results


def fixture():
    channels = {
        "SessionTime": list(range(15)), "SessionNum": [2] * 15,
        "SessionState": [4] * 15, "SessionFlags": [4] * 15,
        "CarIdxLapCompleted": [[i // 2, i // 2] for i in range(15)],
        "CarIdxLastLapTime": [[2.0, 2.2] for _ in range(15)],
        "CarIdxPosition": [[2, 1] for _ in range(15)],
        "CarIdxOnPitRoad": [[False, False] for _ in range(15)],
    }
    info = {"DriverInfo": {"DriverCarIdx": 0, "Drivers": [
        {"CarIdx": 0, "UserName": "Player", "CarNumber": "7"},
        {"CarIdx": 1, "UserName": "Rival", "CarNumber": "12"},
        {"CarIdx": 2, "UserName": "Spectator", "IsSpectator": 1},
        {"CarIdx": 3, "UserName": "Pace", "CarIsPaceCar": 1},
    ]}, "SessionInfo": {"Sessions": [{"SessionNum": 2, "SessionType": "Race", "ResultsPositions": []}]}}
    return channels, info, {"sim_session_num": 2, "sim_session_type": "Race"}


class FieldResultsTests(unittest.TestCase):
    def build(self, channels=None, info=None, metadata=None, laps=(), runs=()):
        base = fixture()
        return build_field_results(channels if channels is not None else base[0],
                                   info if info is not None else base[1],
                                   metadata if metadata is not None else base[2], laps, runs)

    def test_order_pace_and_private_data_boundary(self):
        result = self.build()
        self.assertEqual([r["car_index"] for r in result["cars"]], [1, 0])
        rival = result["cars"][0]
        self.assertEqual(rival["pace_median_s"], 2.0)
        self.assertEqual(rival["pace_lap_count"], 6)
        self.assertEqual(rival["pit_visits"], 0)
        self.assertIsNone(rival["fuel_used_l"])
        self.assertIsNone(rival["tires_taken"])
        self.assertEqual(result["order_status"], "recorded")

    def test_pit_entry_excludes_initial_pit_state_and_cooldown(self):
        channels, info, metadata = fixture()
        for i in (0, 1, 6, 7, 14):
            channels["CarIdxOnPitRoad"][i][1] = True
        channels["SessionState"][14] = 6
        rival = self.build(channels, info, metadata)["cars"][0]
        self.assertEqual(rival["pit_visits"], 1)
        self.assertEqual(rival["pit_events"][0]["session_time_s"], 6)
        self.assertTrue(any(lap["on_pit_road"] and not lap["pace_eligible"] for lap in rival["laps"]))

    def test_missing_pit_or_flags_never_becomes_clean_pace(self):
        for name in ("CarIdxOnPitRoad", "SessionFlags"):
            channels, _, _ = fixture()
            del channels[name]
            rival = self.build(channels)["cars"][0]
            self.assertIsNone(rival["pace_median_s"])
            if name == "CarIdxOnPitRoad":
                self.assertIsNone(rival["pit_visits"])

    def test_caution_laps_not_in_green_median(self):
        channels, _, _ = fixture()
        channels["SessionFlags"] = [0x8000] * 15
        rival = self.build(channels)["cars"][0]
        self.assertIsNone(rival["pace_median_s"])
        self.assertTrue(all(lap["flag_state"] == "caution" for lap in rival["laps"]))

    def test_phase_isolation_ignores_other_race_and_qualifying(self):
        channels, _, _ = fixture()
        for i in range(8):
            channels["SessionNum"][i] = 1
            channels["CarIdxOnPitRoad"][i][1] = i % 2 == 0
        rival = self.build(channels)["cars"][0]
        self.assertEqual(rival["pit_visits"], 0)
        self.assertTrue(all(lap["lap"] >= 5 for lap in rival["laps"]))

    def test_no_lap_fabrication_across_sample_gap_or_counter_jump(self):
        channels, _, _ = fixture()
        channels["SessionTime"][6:] = list(range(30, 39))
        channels["CarIdxLapCompleted"][4] = [10, 10]
        result = self.build(channels)
        rival = result["cars"][0]
        self.assertNotIn(10, [lap["lap"] for lap in rival["laps"]])
        self.assertNotIn(3, [lap["lap"] for lap in rival["laps"]])

    def test_player_only_observed_service_excludes_after_race(self):
        result = self.build(laps=[{"lap": 1, "complete": True, "fuel": {"used_l": 2.3}},
                                 {"lap": 2, "complete": False, "fuel": {"used_l": 9}}],
                            runs=[{"pit_service": {"tires_changed_observed": ["RF", "RR"]}},
                                  {"ended_with_post_run_service": True, "pit_service": {"tires_changed_observed": ["LF", "LR"]}}])
        player = result["cars"][1]
        self.assertEqual(player["fuel_used_l"], 2.3)
        self.assertEqual(player["tires_taken"], ["RF", "RR"])

    def test_snapshot_zero_based_position_normalized_but_live_position_wins(self):
        channels, info, _ = fixture()
        info["SessionInfo"]["Sessions"][0]["ResultsPositions"] = [{"CarIdx": 0, "Position": 0}, {"CarIdx": 1, "Position": 1}]
        self.assertEqual(self.build(channels, info)["cars"][0]["car_index"], 1)
        del channels["CarIdxPosition"]
        rows = self.build(channels, info)["cars"]
        self.assertEqual([(r["car_index"], r["position"]) for r in rows], [(0, 1), (1, 2)])

    def test_player_laps_survive_missing_competitor_arrays(self):
        channels, info, metadata = fixture()
        channels = {k: v for k, v in channels.items() if not k.startswith("CarIdx")}
        channels["OnPitRoad"] = [False] * 15
        laps = [{"lap": i, "complete": True, "lap_time_s": 29 + i / 10, "position": {"end": 2},
                 "flag_state": "green", "pit_time_s": 0, "pit_entry": False, "pit_exit": False} for i in range(1, 5)]
        player = next(row for row in self.build(channels, info, metadata, laps)["cars"] if row["is_player"])
        self.assertAlmostEqual(player["pace_median_s"], 29.25)
        self.assertEqual(player["laps_completed"], 4)

    def test_delayed_scored_time_never_labels_the_wrong_lap(self):
        channels, _, _ = fixture()
        # Scoreboard reports the previous lap at a crossing, then settles later.
        channels["CarIdxLastLapTime"] = [[0.5, 0.5] if i % 2 == 0 else [2, 2] for i in range(15)]
        rival = self.build(channels)["cars"][0]
        self.assertEqual(rival["fastest_lap_s"], 2)
        self.assertEqual(rival["fastest_source"], "recorded_lap_counter_intervals")
        self.assertTrue(all(lap["lap_time_s"] == 2 for lap in rival["laps"]))
        self.assertNotIn(1, [lap["lap"] for lap in rival["laps"]])

    def test_distance_wrap_interpolates_crossings_when_counter_agrees(self):
        channels, _, _ = fixture()
        channels["CarIdxLapDistPct"] = [[0.01, 0.01] if i % 2 == 0 else [0.99, 0.99] for i in range(15)]
        rival = self.build(channels)["cars"][0]
        self.assertEqual(rival["fastest_lap_s"], 2)
        self.assertTrue(all(lap["lap_time_source"] == "recorded_interpolated_distance_crossings" for lap in rival["laps"]))

    def test_player_pit_entry_exit_keys_and_absent_channel(self):
        channels, info, metadata = fixture()
        channels = {k: v for k, v in channels.items() if not k.startswith("CarIdx")}
        channels["OnPitRoad"] = [False] * 15
        channels["OnPitRoad"][4:6] = [True, True]
        laps = [{"lap": i, "complete": True, "lap_time_s": 30,
                 "start_index": (i - 1) * 3, "end_index": i * 3 - 1,
                 "flag_state": "green", "pit_time_s": 0,
                 "pit_entry": i == 2, "pit_exit": i == 3} for i in range(1, 5)]
        player = next(row for row in self.build(channels, info, metadata, laps)["cars"] if row["is_player"])
        self.assertEqual(player["pit_visits"], 1)
        self.assertEqual(player["pit_events"][0]["session_time_s"], 4)
        self.assertEqual(player["pace_lap_count"], 2)
        self.assertTrue(player["laps"][1]["on_pit_road"])
        del channels["OnPitRoad"]
        player = next(row for row in self.build(channels, info, metadata, laps)["cars"] if row["is_player"])
        self.assertIsNone(player["pit_visits"])
        self.assertEqual(player["pace_lap_count"], 0)

    def test_player_majority_green_lap_is_not_whole_green(self):
        channels, _, _ = fixture()
        channels["OnPitRoad"] = [False] * 15
        channels["SessionFlags"][5] = 0x8000
        lap = {"lap": 1, "complete": True, "lap_time_s": 30, "flag_state": "green", "start_index": 0, "end_index": 10}
        player = next(row for row in self.build(channels, laps=[lap])["cars"] if row["is_player"])
        self.assertFalse(player["laps"][0]["pace_eligible"])

    def test_mixed_phase_player_fuel_service_and_fallback_are_scoped(self):
        channels, info, metadata = fixture()
        channels = {k: v for k, v in channels.items() if not k.startswith("CarIdx")}
        channels["SessionNum"][:5] = [1] * 5
        channels["OnPitRoad"] = [False] * 15
        laps = [{"lap": 1, "complete": True, "lap_time_s": 12, "start_index": 0, "end_index": 4, "fuel": {"used_l": 9}},
                {"lap": 2, "complete": True, "lap_time_s": 30, "start_index": 5, "end_index": 10, "fuel": {"used_l": 2}}]
        runs = [{"pit_service": {"start_index": 0, "end_index": 4, "tires_changed_observed": ["LF"]}},
                {"pit_service": {"start_index": 6, "end_index": 7, "tires_changed_observed": ["RF"]}}]
        player = next(row for row in self.build(channels, info, metadata, laps, runs)["cars"] if row["is_player"])
        self.assertEqual(player["fuel_used_l"], 2)
        self.assertEqual(player["tires_taken"], ["RF"])
        self.assertEqual([lap["lap"] for lap in player["laps"]], [2])
        # Unscoped summary in mixed data cannot establish fuel or tire service.
        player = next(row for row in self.build(channels, info, metadata, [{"complete": True, "fuel": {"used_l": 9}}], [{"pit_service": {"tires_changed_observed": ["LF"]}}])["cars"] if row["is_player"])
        self.assertIsNone(player["fuel_used_l"])
        self.assertIsNone(player["tires_taken"])

    def test_missing_tire_evidence_does_not_claim_zero_tires(self):
        player = next(row for row in self.build(runs=[{"pit_service": {"tires_changed_observed": [], "tire_change_confirmation": "not_observed"}}])["cars"] if row["is_player"])
        self.assertIsNone(player["tires_taken"])

    def test_short_phase_gap_breaks_lap_timing_even_when_clock_is_continuous(self):
        channels, _, _ = fixture()
        channels["SessionNum"][5] = 1
        rival = self.build(channels)["cars"][0]
        self.assertNotIn(3, [lap["lap"] for lap in rival["laps"]])
        self.assertNotIn(4, [lap["lap"] for lap in rival["laps"]])

    def test_tire_confirmation_lookahead_cannot_cross_phase(self):
        channels, _, _ = fixture()
        channels["SessionNum"][4] = 1
        service = {"start_index": 2, "end_index": 3, "tires_changed_observed": ["RF"]}
        player = next(row for row in self.build(channels, runs=[{"pit_service": service}])["cars"] if row["is_player"])
        self.assertIsNone(player["tires_taken"])

    def test_player_scored_time_does_not_replace_latest_completed_counter(self):
        channels, _, _ = fixture()
        channels["OnPitRoad"] = [False] * 15
        lap = {"lap": 1, "complete": True, "lap_time_s": 2, "start_index": 0, "end_index": 1}
        player = next(row for row in self.build(channels, laps=[lap])["cars"] if row["is_player"])
        self.assertEqual(player["laps_completed"], 7)

    def test_partial_results_snapshot_does_not_reassign_finishing_position(self):
        channels, info, _ = fixture()
        del channels["CarIdxPosition"]
        info["SessionInfo"]["Sessions"][0]["ResultsPositions"] = [{"CarIdx": 0, "Position": 5}, {"CarIdx": 1, "Position": 8}]
        self.assertEqual([row["position"] for row in self.build(channels, info)["cars"]], [5, 8])

    def test_sdk_one_based_results_snapshot_preserves_positions_and_holes(self):
        channels, info, _ = fixture()
        del channels["CarIdxPosition"]
        for positions in ((1, 2), (1, 4), (3, 8)):
            info["SessionInfo"]["Sessions"][0]["ResultsPositions"] = [
                {"CarIdx": 0, "Position": positions[0]}, {"CarIdx": 1, "Position": positions[1]}]
            self.assertEqual([row["position"] for row in self.build(channels, info)["cars"]], list(positions))

    def test_explicit_zero_based_results_snapshot_preserves_holes(self):
        channels, info, _ = fixture()
        del channels["CarIdxPosition"]
        info["SessionInfo"]["Sessions"][0]["ResultsPositions"] = [
            {"CarIdx": 0, "Position": 0}, {"CarIdx": 1, "Position": 4}]
        self.assertEqual([row["position"] for row in self.build(channels, info)["cars"]], [1, 5])

    def test_sanitized_fixture_matches_producer(self):
        path = Path(__file__).resolve().parents[2] / "test-data" / "field-results.json"
        self.assertEqual(json.loads(path.read_text()), self.build())


if __name__ == "__main__":
    unittest.main()
