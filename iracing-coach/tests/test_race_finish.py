"""Backlog #97: a race recording that stops before the race does has no finish.

Joshua: "On a race where I wreck out and leave, it doesn't have the rest of the
race and where I really finished. I did not finish 6th at the 9:09 race. I got
wrecked out." The shapes below are the 2026-10-06 Charlotte races' own ends:
the 21:09 race (session 670) stopped in the pit stall on lap 14 at P6 with 77
laps to go and SessionState still racing; the 2026-10-02 Vegas win reached
checkered and cool-down.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_engine import RACE_FINISH_RULE, TelemetryTable, _race_finish, analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402


def _table(states, *, session_type="Race", session_num=2, numbers=None, remaining=None):
    channels = {"SessionState": list(states)}
    if numbers is not None:
        channels["SessionNum"] = list(numbers)
    if remaining is not None:
        channels["SessionLapsRemainEx"] = list(remaining)
    return TelemetryTable(
        {
            "channels": channels,
            "metadata": {"sim_session_type": session_type, "sim_session_num": session_num},
        }
    )


def _laps(last_lap, end_position):
    return [
        {"lap": 0, "position": {"start": 7, "end": 6}},
        {"lap": last_lap, "position": {"start": end_position, "end": end_position}},
    ]


class RaceFinishTests(unittest.TestCase):
    def test_a_race_left_while_racing_ended_before_its_finish(self) -> None:
        # Session 670: grid, parade, racing, then the recording stops in the stall.
        table = _table([1, 2, 3, 4, 4, 4], numbers=[2] * 6, remaining=[90, 90, 90, 78, 77, 77])
        finish = _race_finish(table, _laps(14, 6))
        self.assertEqual(
            finish,
            {
                "status": "recording_ended",
                "finished": False,
                "last_lap": 14,
                "position_at_end": 6,
                "last_session_state": "racing",
                "laps_remaining_at_end": 77,
                "evidence_class": "measured_session_state",
                "rule": RACE_FINISH_RULE,
            },
        )

    def test_a_race_that_reached_the_checkered_flag_finished(self) -> None:
        # The 10/02 Vegas win: racing, checkered when the leader finished, then cool-down.
        table = _table([3, 4, 4, 5, 6, 6], numbers=[2] * 6, remaining=[45, 2, 1, 1, 0, 0])
        finish = _race_finish(table, _laps(46, 1))
        self.assertEqual(finish["status"], "checkered")
        self.assertIs(finish["finished"], True)
        self.assertEqual(finish["last_session_state"], "cooldown")
        self.assertEqual(finish["laps_remaining_at_end"], 0)

    def test_leaving_after_the_leader_finished_still_counts_as_finished(self) -> None:
        table = _table([4, 4, 5, 5], numbers=[2] * 4)
        self.assertIs(_race_finish(table, _laps(44, 9))["finished"], True)

    def test_an_earlier_sessions_checkered_does_not_finish_the_race(self) -> None:
        # One recording that runs from qualifying (session 1, checkered) into the race (session 2).
        table = _table([4, 5, 6, 1, 3, 4, 4], numbers=[1, 1, 1, 2, 2, 2, 2])
        finish = _race_finish(table, _laps(20, 11))
        self.assertEqual(finish["status"], "recording_ended")
        self.assertIs(finish["finished"], False)

    def test_without_session_numbers_every_sample_counts(self) -> None:
        self.assertEqual(_race_finish(_table([4, 4, 5]), _laps(30, 3))["status"], "checkered")

    def test_a_race_never_seen_racing_is_unknown(self) -> None:
        # Left on the grid: nothing says how the race went.
        finish = _race_finish(_table([1, 2, 3], numbers=[2, 2, 2]), [])
        self.assertEqual(finish["status"], "unknown")
        self.assertIsNone(finish["finished"])
        self.assertIsNone(finish["last_lap"])
        self.assertIsNone(finish["position_at_end"])
        self.assertEqual(finish["last_session_state"], "parade_laps")

    def test_without_session_state_the_finish_is_unknown(self) -> None:
        table = TelemetryTable({"channels": {"Lap": [1, 2]}, "metadata": {"sim_session_type": "Race"}})
        finish = _race_finish(table, _laps(2, 4))
        self.assertEqual(finish["status"], "unknown")
        self.assertIsNone(finish["finished"])
        self.assertIsNone(finish["last_session_state"])
        self.assertEqual(finish["last_lap"], 2)

    def test_an_unlimited_lap_count_is_not_a_laps_remaining_figure(self) -> None:
        table = _table([4, 4], numbers=[2, 2], remaining=[32767, 32767])
        self.assertIsNone(_race_finish(table, _laps(5, 2))["laps_remaining_at_end"])

    def test_practice_and_qualifying_have_no_finish(self) -> None:
        for session_type in ("Practice", "Lone Qualify", "Offline Testing", ""):
            with self.subTest(session_type=session_type):
                self.assertIsNone(_race_finish(_table([4, 4], session_type=session_type), _laps(3, 1)))

    def test_the_analysis_carries_it_on_the_race_summary(self) -> None:
        telemetry = synthetic_telemetry()
        count = len(telemetry["channels"]["SessionTime"])
        telemetry["channels"]["SessionState"] = [4] * count
        telemetry["metadata"] = {**telemetry["metadata"], "sim_session_type": "Race"}
        finish = analyze_telemetry(telemetry)["race_summary"]["finish"]
        self.assertEqual(finish["status"], "recording_ended")
        self.assertEqual(finish["last_lap"], 8)

    def test_an_analysis_without_a_session_type_has_no_finish(self) -> None:
        self.assertIsNone(analyze_telemetry(synthetic_telemetry())["race_summary"]["finish"])


if __name__ == "__main__":
    unittest.main()
