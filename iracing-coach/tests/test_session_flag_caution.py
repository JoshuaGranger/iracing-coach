"""One-lap-to-green outside a Race is not a caution.

iRacing holds SessionFlags 0x0200 (one-lap-to-green) on every sample of an
Offline Testing session - the 2026-09-20 16:16 IRP test reads 0x10040200 from
the garage to the last lap - and on a Lone Qualify out-lap and after its
checkered, with no yellow or caution bit. Counting that bit as a caution made
every lap of a test at race pace a caution lap: no green laps, no screened
laps, no track sections. In a Race the bit belongs to the caution and restart
sequence and must keep its meaning.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_engine import TelemetryTable, _lap_summaries, _runs, analyze_telemetry  # noqa: E402
from test_analysis_engine import synthetic_telemetry  # noqa: E402

# Recorded words from the IRP recordings: startHidden | servicible |
# one-lap-to-green in Offline Testing, and the same plus yellow as a control.
OFFLINE_TEST_FLAGS = 0x10040200
OFFLINE_TEST_WITH_YELLOW = OFFLINE_TEST_FLAGS | 0x0008


def _session(telemetry: dict, session_type: str | None, flags: int, session_num: int = 0) -> dict:
    result = copy.deepcopy(telemetry)
    count = len(result["channels"]["SessionFlags"])
    result["channels"]["SessionFlags"] = [flags] * count
    if session_type is None:
        result["session_info"]["SessionInfo"] = {"Sessions": []}
        result["metadata"] = {"sample_rate": 20}
        return result
    result["channels"]["SessionNum"] = [session_num] * count
    result["session_info"]["SessionInfo"] = {"Sessions": [
        {"SessionNum": session_num, "SessionType": session_type, "SessionLaps": "unlimited"},
    ]}
    result["metadata"] = {"sample_rate": 20, "sim_session_num": session_num, "sim_session_type": session_type}
    return result


def _complete_laps(laps: list[dict]) -> list[dict]:
    return [lap for lap in laps if lap["complete"] and lap["lap"] > 0]


class OneLapToGreenOutsideRaceTests(unittest.TestCase):
    def test_offline_testing_one_lap_to_green_keeps_race_pace_laps_green(self) -> None:
        table = TelemetryTable(_session(synthetic_telemetry(), "Offline Testing", OFFLINE_TEST_FLAGS))
        laps = _lap_summaries(table)
        complete = _complete_laps(laps)
        self.assertTrue(complete)
        self.assertEqual({lap["flag_state"] for lap in complete}, {"green"})
        self.assertTrue(all(lap["caution_fraction"] == 0.0 for lap in complete))
        self.assertTrue(all("yellow" not in lap["flag_states"] for lap in complete))
        runs = _runs(table, laps)
        self.assertGreater(sum(run["green_laps"] for run in runs), 0.0)
        self.assertEqual(sum(run["caution_laps"] for run in runs), 0.0)
        self.assertTrue(any(run["valid_green_lap_numbers"] for run in runs))
        self.assertFalse(any(run["ended_under_caution"] for run in runs))

    def test_offline_testing_analysis_keeps_screened_laps_and_track_profile(self) -> None:
        analysis = analyze_telemetry(_session(synthetic_telemetry(), "Offline Testing", OFFLINE_TEST_FLAGS))
        rows = analysis["race_investigation"]["laps"]
        self.assertFalse(any("caution_or_mixed" in row["exclusion_reasons"] for row in rows))
        self.assertGreaterEqual(sum(row["eligible"] for row in rows), 4)
        self.assertEqual(analysis["race_investigation"]["status"], "available")
        # Section detection and setup ranges read only non-caution samples.
        self.assertTrue((analysis["track_profile"] or {}).get("profile"))
        self.assertGreater(analysis["setup_telemetry"]["sample_count"], 0)

    def test_lone_qualify_out_lap_state_is_not_a_caution(self) -> None:
        table = TelemetryTable(_session(synthetic_telemetry(), "Lone Qualify", OFFLINE_TEST_FLAGS, session_num=1))
        self.assertEqual({lap["flag_state"] for lap in _complete_laps(_lap_summaries(table))}, {"green"})

    def test_race_keeps_one_lap_to_green_as_caution(self) -> None:
        table = TelemetryTable(_session(synthetic_telemetry(), "Race", OFFLINE_TEST_FLAGS, session_num=2))
        complete = _complete_laps(_lap_summaries(table))
        self.assertEqual({lap["flag_state"] for lap in complete}, {"caution"})

    def test_unknown_session_type_keeps_one_lap_to_green_as_caution(self) -> None:
        table = TelemetryTable(_session(synthetic_telemetry(), None, OFFLINE_TEST_FLAGS))
        complete = _complete_laps(_lap_summaries(table))
        self.assertEqual({lap["flag_state"] for lap in complete}, {"caution"})

    def test_real_yellow_in_offline_testing_is_still_a_caution(self) -> None:
        table = TelemetryTable(_session(synthetic_telemetry(), "Offline Testing", OFFLINE_TEST_WITH_YELLOW))
        complete = _complete_laps(_lap_summaries(table))
        self.assertEqual({lap["flag_state"] for lap in complete}, {"caution"})

    def test_recording_that_runs_into_a_race_keeps_the_race_meaning_there(self) -> None:
        telemetry = _session(synthetic_telemetry(), "Lone Qualify", OFFLINE_TEST_FLAGS, session_num=1)
        telemetry["session_info"]["SessionInfo"]["Sessions"].append(
            {"SessionNum": 2, "SessionType": "Race", "SessionLaps": "8"}
        )
        laps_channel = telemetry["channels"]["Lap"]
        telemetry["channels"]["SessionNum"] = [1 if lap <= 4 else 2 for lap in laps_channel]
        by_lap = {lap["lap"]: lap for lap in _complete_laps(_lap_summaries(TelemetryTable(telemetry)))}
        self.assertEqual(by_lap[2]["flag_state"], "green")
        self.assertEqual(by_lap[6]["flag_state"], "caution")


if __name__ == "__main__":
    unittest.main()
