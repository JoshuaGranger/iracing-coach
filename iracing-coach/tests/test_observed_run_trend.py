from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/analyze-iracing-race/scripts"))
from analysis_engine import _corner_lap_exclusion_reasons
from observed_run_trend import annotate_observed_trends


class ObservedRunTrendTests(unittest.TestCase):
    def laps(self):
        return [{"lap": number, "lap_time_s": 30 + (number - 33) * .02,
                 "complete": True, "flag_state": "green", "pit_time_s": 0,
                 "racing_state_fraction": 1, "pit_entry": False, "pit_exit": False,
                 "clean_context": {"on_track_fraction": 1, "traffic_proximity_fraction": .2},
                 "damage_repair_context": {"automatic_coaching_reference_eligible": False,
                                           "exclusion_reason_codes": [],
                                           "run_reference_status": "excluded_recorded_repair_remaining"}}
                for number in range(33, 40)]

    def test_repair_context_keeps_observed_line_and_reference_screen_unchanged(self):
        laps = self.laps()
        original = copy.deepcopy(laps)
        runs = [{"run_number": 2, "lap_numbers": list(range(32, 41))}]
        annotate_observed_trends(laps, runs, {}, _corner_lap_exclusion_reasons)
        self.assertEqual(runs[0]["observed_trend"], {
            "slope_s_per_lap": .02, "intercept_s": 29.34,
            "first_lap": 33, "last_lap": 39, "laps_used": 7,
        })
        self.assertEqual(laps, original)
        self.assertIn("damage_repair_context", _corner_lap_exclusion_reasons(laps[0], None))

    def test_incident_restart_pit_tow_and_other_screens_stay_excluded(self):
        laps = self.laps()
        laps[0]["flag_state"] = "caution"
        # L34 is the restart; L35 an incident; L36 a pit exit; L37 tow.
        laps[3]["pit_exit"] = True
        laps[4]["damage_repair_context"]["exclusion_reason_codes"] = ["tow"]
        runs = [{"run_number": 2, "lap_numbers": list(range(33, 40))}]
        damage = {"incident_points": {"events": [{"candidate_lap": 35}]}}
        annotate_observed_trends(laps, runs, damage, _corner_lap_exclusion_reasons)
        self.assertIsNone(runs[0]["observed_trend"])

    def test_unknown_run_membership_and_nonfinite_times_do_not_count(self):
        laps = self.laps()
        laps[0]["lap_time_s"] = float("nan")
        runs = [{"run_number": 1, "lap_numbers": [1, 2, 3]},
                {"run_number": 2, "lap_numbers": [33, 34, 35]}]
        annotate_observed_trends(laps, runs, {}, _corner_lap_exclusion_reasons)
        self.assertTrue(all(run["observed_trend"] is None for run in runs))


if __name__ == "__main__":
    unittest.main()
