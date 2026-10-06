"""clean_context says which side of the car a lap's close traffic was on.

The close-traffic screen reads the recorded CarDistAhead and CarDistBehind: a
car ahead within max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) for at
least 10% of the observed lap time screens the lap (close_traffic). Each lap's
clean_context also states each side's share of the observed lap
(traffic_ahead_fraction, traffic_behind_fraction) and the side (traffic_side):
None without traffic, "both" when each side alone reaches 10%, else the side
that does, else the larger share ("both" when equal). The combined
traffic_proximity_fraction and the screen are unchanged.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_engine import (  # noqa: E402
    TelemetryTable,
    _corner_lap_exclusion_reasons,
    _lap_summaries,
    _traffic_sides,
    analyze_telemetry,
)
from test_analysis_engine import synthetic_telemetry  # noqa: E402

SAMPLES = 200  # per lap, at 20 Hz: one sample is 0.5 % of the lap
SPEED = 40.0  # m/s: the ahead limit is max(25, 30) = 30 m, the behind limit max(15, 16) = 16 m
FAR = 500.0
NAN = float("nan")
SIDE_KEYS = ("traffic_ahead_fraction", "traffic_behind_fraction", "traffic_side")


def _field(laps: list[tuple[dict[int, float], dict[int, float]]], speed: float = SPEED) -> dict:
    """One lap per entry: {sample offset: distance} of the car ahead and of the car behind (else FAR)."""

    channels: dict[str, list] = {
        "SessionTime": [], "Lap": [], "LapDistPct": [], "Speed": [], "CarDistAhead": [], "CarDistBehind": [],
    }
    for number, (ahead, behind) in enumerate(laps, start=1):
        for offset in range(SAMPLES):
            channels["SessionTime"].append(len(channels["Lap"]) / 20.0)
            channels["Lap"].append(number)
            channels["LapDistPct"].append(offset / SAMPLES)
            channels["Speed"].append(speed)
            channels["CarDistAhead"].append(ahead.get(offset, FAR))
            channels["CarDistBehind"].append(behind.get(offset, FAR))
    return {"channels": channels, "metadata": {"sample_rate": 20}}


def _close(offsets: range, distance: float) -> dict[int, float]:
    return {offset: distance for offset in offsets}


def _contexts(telemetry: dict) -> dict[int, dict]:
    return {lap["lap"]: lap["clean_context"] for lap in _lap_summaries(TelemetryTable(telemetry))}


class TrafficSideTests(unittest.TestCase):
    def assert_sides(self, context: dict, ahead: float | None, behind: float | None, side: str | None) -> None:
        self.assertEqual(
            {key: context[key] for key in SIDE_KEYS},
            {"traffic_ahead_fraction": ahead, "traffic_behind_fraction": behind, "traffic_side": side},
        )

    def test_a_car_ahead_alone_is_traffic_ahead(self) -> None:
        context = _contexts(_field([(_close(range(0, 60), 10.0), {})]))[1]
        self.assertEqual(context["traffic_proximity_fraction"], 0.3)
        self.assert_sides(context, 0.3, 0.0, "ahead")

    def test_a_car_behind_alone_is_traffic_behind(self) -> None:
        context = _contexts(_field([({}, _close(range(100, 150), 8.0))]))[1]
        self.assertEqual(context["traffic_proximity_fraction"], 0.25)
        self.assert_sides(context, 0.0, 0.25, "behind")

    def test_cars_on_both_sides_each_reaching_the_screen_are_both(self) -> None:
        contexts = _contexts(_field([
            # Apart: 20 % ahead, 15 % behind.
            (_close(range(0, 40), 10.0), _close(range(100, 130), 8.0)),
            # Together: the same 12 % with a car on each side, counted once by the screen.
            (_close(range(50, 74), 10.0), _close(range(50, 74), 8.0)),
        ]))
        self.assertEqual(contexts[1]["traffic_proximity_fraction"], 0.35)
        self.assert_sides(contexts[1], 0.2, 0.15, "both")
        self.assertEqual(contexts[2]["traffic_proximity_fraction"], 0.12)
        self.assert_sides(contexts[2], 0.12, 0.12, "both")

    def test_only_the_union_reaching_the_screen_names_the_larger_side(self) -> None:
        contexts = _contexts(_field([
            (_close(range(0, 12), 10.0), _close(range(100, 110), 8.0)),  # 6 % + 5 %
            (_close(range(0, 8), 10.0), _close(range(100, 114), 8.0)),  # 4 % + 7 %
        ]))
        self.assertEqual(contexts[1]["traffic_proximity_fraction"], 0.11)
        self.assert_sides(contexts[1], 0.06, 0.05, "ahead")
        self.assertEqual(contexts[2]["traffic_proximity_fraction"], 0.11)
        self.assert_sides(contexts[2], 0.04, 0.07, "behind")

    def test_a_lap_under_the_screen_still_names_its_side_and_open_air_names_none(self) -> None:
        contexts = _contexts(_field([
            ({}, {}),  # open air
            (_close(range(0, 6), 10.0), {}),  # 3 % ahead
            (_close(range(0, 8), 10.0), _close(range(100, 108), 8.0)),  # 4 % each: equal shares
        ]))
        self.assertEqual(contexts[1]["traffic_proximity_fraction"], 0.0)
        self.assert_sides(contexts[1], 0.0, 0.0, None)
        self.assert_sides(contexts[2], 0.03, 0.0, "ahead")
        self.assertEqual(contexts[3]["traffic_proximity_fraction"], 0.08)
        self.assert_sides(contexts[3], 0.04, 0.04, "both")

    def test_each_side_uses_its_own_rule_distance(self) -> None:
        ahead = {**_close(range(0, 20), 30.0), **_close(range(20, 40), 30.5), **_close(range(40, 60), 0.0),
                 **_close(range(60, 80), -1.0), **_close(range(80, 100), NAN)}
        behind = {**_close(range(100, 120), 16.0), **_close(range(120, 140), 16.5), **_close(range(140, 160), 0.0)}
        fast = _contexts(_field([(ahead, behind)]))[1]
        # At 40 m/s: 30.0 m ahead (0.75 s) and 16.0 m behind (0.40 s) are close, each exactly 10 %;
        # 30.5 m, 16.5 m, no car (0 or -1) and a missing distance are not.
        self.assertEqual(fast["traffic_proximity_fraction"], 0.2)
        self.assert_sides(fast, 0.1, 0.1, "both")
        # At 10 m/s the floors hold: 25 m ahead, 15 m behind, so none of those distances is close.
        slow = _contexts(_field([(ahead, behind)], speed=10.0))[1]
        self.assertEqual(slow["traffic_proximity_fraction"], 0.0)
        self.assert_sides(slow, 0.0, 0.0, None)

    def test_the_screen_still_reads_the_combined_fraction(self) -> None:
        laps = _lap_summaries(TelemetryTable(_field([
            (_close(range(0, 60), 10.0), {}),
            (_close(range(0, 12), 10.0), _close(range(100, 110), 8.0)),
            (_close(range(0, 6), 10.0), {}),
            ({}, {}),
        ])))
        screened = [lap["lap"] for lap in laps if "close_traffic" in _corner_lap_exclusion_reasons(lap, "green")]
        self.assertEqual(screened, [1, 2])
        for lap in laps:
            context = lap["clean_context"]
            self.assertIs(context["traffic_screened"], True)
            self.assertEqual(
                context["traffic_rule"],
                "car ahead within max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) "
                "for at least 10% of observed lap time",
            )
            # Each side's share is part of the union, which is at most their sum.
            self.assertLessEqual(context["traffic_ahead_fraction"], context["traffic_proximity_fraction"])
            self.assertLessEqual(context["traffic_behind_fraction"], context["traffic_proximity_fraction"])
            self.assertLessEqual(
                context["traffic_proximity_fraction"],
                round(context["traffic_ahead_fraction"] + context["traffic_behind_fraction"], 4),
            )

    def test_without_the_distance_channels_nothing_is_stated(self) -> None:
        telemetry = _field([(_close(range(0, 60), 10.0), {})])
        del telemetry["channels"]["CarDistAhead"], telemetry["channels"]["CarDistBehind"]
        context = _contexts(telemetry)[1]
        self.assertIs(context["traffic_screened"], False)
        self.assertIsNone(context["traffic_proximity_fraction"])
        self.assert_sides(context, None, None, None)

    def test_the_rule_on_rounded_fractions(self) -> None:
        # (near, ahead, behind) seconds of a 100 s observed lap -> fractions and side.
        cases = {
            (0.0, 0.0, 0.0): (0.0, 0.0, None),
            (30.0, 30.0, 0.0): (0.3, 0.0, "ahead"),
            (25.0, 0.0, 25.0): (0.0, 0.25, "behind"),
            (35.0, 20.0, 15.0): (0.2, 0.15, "both"),
            (14.0, 10.0, 9.0): (0.1, 0.09, "ahead"),
            (11.0, 6.0, 5.0): (0.06, 0.05, "ahead"),
            (11.0, 4.0, 7.0): (0.04, 0.07, "behind"),
            (8.0, 4.0, 4.0): (0.04, 0.04, "both"),
            # 0.099996 rounds to 0.1, which reaches the screen on both sides.
            (19.9996, 9.9996, 9.9996): (0.1, 0.1, "both"),
        }
        for (near, ahead, behind), (ahead_fraction, behind_fraction, side) in cases.items():
            with self.subTest(near=near, ahead=ahead, behind=behind):
                self.assertEqual(
                    _traffic_sides(near, ahead, behind, 100.0),
                    {"traffic_ahead_fraction": ahead_fraction, "traffic_behind_fraction": behind_fraction,
                     "traffic_side": side},
                )
        self.assertEqual(
            _traffic_sides(0.0, 0.0, 0.0, 0.0),
            {"traffic_ahead_fraction": None, "traffic_behind_fraction": None, "traffic_side": None},
        )

    def test_the_analysis_carries_the_side_on_every_lap(self) -> None:
        telemetry = synthetic_telemetry(lap_count=6)
        count = len(telemetry["channels"]["Lap"])
        telemetry["channels"]["CarDistAhead"] = [1000.0] * count
        telemetry["channels"]["CarDistBehind"] = [1000.0] * count
        for index, lap in enumerate(telemetry["channels"]["Lap"]):
            if lap == 3:
                telemetry["channels"]["CarDistAhead"][index] = 10.0
            if lap == 4:
                telemetry["channels"]["CarDistBehind"][index] = 5.0
        analysis = analyze_telemetry(telemetry)
        by_lap = {lap["lap"]: lap["clean_context"] for lap in analysis["laps"]}
        self.assertIsNone(by_lap[2]["traffic_side"])
        self.assertEqual((by_lap[3]["traffic_side"], by_lap[3]["traffic_ahead_fraction"]), ("ahead", 1.0))
        self.assertEqual((by_lap[4]["traffic_side"], by_lap[4]["traffic_behind_fraction"]), ("behind", 1.0))
        for context in by_lap.values():
            self.assertEqual(list(context)[-3:], list(SIDE_KEYS))
        json.dumps(analysis["laps"], allow_nan=False)


if __name__ == "__main__":
    unittest.main()
