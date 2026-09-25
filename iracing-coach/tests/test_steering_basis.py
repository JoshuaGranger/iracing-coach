"""Steering is the steering applied at the front wheels, whatever the ratio.

Joshua's requirement (2026-09-25): every "steering" reference means the amount
of steering applied, irrespective of the steering ratio. iRacing records the
steering-wheel angle; the backend divides it by the ratio the recording's own
garage setup states and never guesses a missing ratio.
"""

from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import driving_trials  # noqa: E402
import race_foundations  # noqa: E402
import steering_basis  # noqa: E402
import tuning_engine  # noqa: E402
import workflow  # noqa: E402
from analysis_engine import (  # noqa: E402
    TelemetryTable,
    _lap_summaries,
    _steering_basis,
    _steering_column,
    analyze_telemetry,
    build_technical_insights,
)
from reporting import render_report  # noqa: E402
from steering_basis import (  # noqa: E402
    ROAD_WHEEL,
    STEERING_WHEEL,
    SteeringBasis,
    basis_of,
    comparable_across_recordings,
    parse_steering_ratio,
    recorded_steering_ratio,
    resolve_steering_basis,
)
from test_analysis_engine import synthetic_telemetry  # noqa: E402
from test_structured_tuning import _analysis as tuning_analysis, _feedback as tuning_feedback, _map as tuning_map  # noqa: E402

# The O'Reilly Supra's recorded lock is 72.7 deg of road wheel: its
# SteeringWheelAngleMax reads 872.4 deg at 12:1 and 727.0 deg at 10:1.
SUPRA_LOCK_RAD = math.radians(72.7)


def supra_session(ratio: str | None, offset: str = "+6 deg") -> dict:
    """The CarSetup shape of a real O'Reilly Supra IBT (Chassis/Front)."""

    front = {"BallastForward": "914 mm", "NoseWeight": "50.7%", "CrossWeight": "58.0%",
             "SteeringOffset": offset, "FrontBrakeBias": "68.0%", "TapeConfiguration": "Qualifying"}
    if ratio is not None:
        front["SteeringRatio"] = ratio
    return {"CarSetup": {"UpdateCount": 10, "Chassis": {"Front": front, "Rear": {"RearEndRatio": 5.5}}}}


def road_wheel_input(count: int, samples_per_lap: int = 80) -> list[float]:
    """A left-turn oval steering trace in road-wheel radians.

    Every value is a whole number of 1/4096 rad, so 10x and 12x are exact
    binary floats and dividing them back by the ratio is exact: the two
    recordings below carry the identical steering applied.
    """

    values = []
    for index in range(count):
        pct = (index % samples_per_lap) / samples_per_lap
        corner = 0.12 <= pct <= 0.38 or 0.62 <= pct <= 0.88
        radians = 0.004
        if corner:
            phase = ((pct - 0.12) if pct < 0.5 else (pct - 0.62)) / 0.26
            radians += 0.07 * math.sin(math.pi * phase) ** 2
            # A small sawing correction: alternating steps of ~0.2 deg road wheel.
            radians += 0.0035 if (index // 2) % 2 else -0.0035
        values.append(round(radians * 4096) / 4096)
    return values


def recording(ratio: int | None, *, wheel_scale: int, stated_ratio: str | None = "auto") -> dict:
    telemetry = synthetic_telemetry()
    road = road_wheel_input(len(telemetry["channels"]["SteeringWheelAngle"]))
    telemetry["channels"]["SteeringWheelAngle"] = [value * wheel_scale for value in road]
    if ratio is not None:
        telemetry["channels"]["SteeringWheelAngleMax"] = [SUPRA_LOCK_RAD * ratio] * len(road)
    if stated_ratio == "auto":
        stated_ratio = f"{ratio}:1" if ratio is not None else None
    telemetry["session_info"]["CarSetup"] = supra_session(stated_ratio)["CarSetup"]
    return telemetry


def _quantized(radians: float) -> float:
    return round(radians * 4096) / 4096


def oval_run(wheel_scale: int, stated_ratio: str | None, *, laps: int = 18, hz: float = 20.0,
             offset: str = "+6 deg") -> dict:
    """One green run on a 1 km two-corner oval, sampled like a recording.

    Laps from the middle of the run on lose time in the second corner while
    the driver adds steering there, so the investigation has a pace change,
    detected sections, a steering comparison and a steering action, and the
    corner phases have steering metrics. Steering applied is the same for
    every ratio; the recorded wheel angle is that times ``wheel_scale``.
    ``offset`` is the setup's stated SteeringOffset.
    """

    names = ("SessionTime", "Lap", "LapDistPct", "Speed", "Throttle", "Brake", "SteeringWheelAngle",
             "SteeringWheelAngleMax", "LatAccel", "LongAccel", "FuelLevel", "SessionFlags", "SessionState",
             "OnPitRoad", "PlayerTrackSurface", "Lat", "Lon", "YawRate")
    channels: dict[str, list] = {name: [] for name in names}
    time, fuel, index = 0.0, 60.0, 0
    for lap in range(1, laps + 1):
        late = lap > laps // 2
        pct = 0.0
        while pct < 1.0:
            first_corner, second_corner = 0.12 <= pct <= 0.38, 0.62 <= pct <= 0.88
            corner = first_corner or second_corner
            brake = 0.3 if (0.08 <= pct < 0.13 or 0.58 <= pct < 0.63) else 0.0
            speed = 55.0 - (9.0 if corner else 0.0) - (2.0 if late and second_corner else 0.0) - brake * 6.0
            road = 0.004
            if corner:
                phase = ((pct - 0.12) if first_corner else (pct - 0.62)) / 0.26
                road += 0.07 * math.sin(math.pi * phase) ** 2
                road += 0.0035 if (index // 2) % 2 else -0.0035
                if late and second_corner:
                    road += 0.012
            channels["SessionTime"].append(time)
            channels["Lap"].append(lap)
            channels["LapDistPct"].append(pct)
            channels["Speed"].append(speed)
            channels["Throttle"].append(0.3 if brake else (0.7 if corner else 1.0))
            channels["Brake"].append(brake)
            channels["SteeringWheelAngle"].append(_quantized(road) * wheel_scale)
            channels["SteeringWheelAngleMax"].append(SUPRA_LOCK_RAD * wheel_scale)
            channels["LatAccel"].append(12.0 if corner else 0.5)
            channels["LongAccel"].append(-6.0 if brake else 0.5)
            channels["FuelLevel"].append(fuel)
            channels["SessionFlags"].append(0x0004)
            channels["SessionState"].append(4)
            channels["OnPitRoad"].append(False)
            channels["PlayerTrackSurface"].append(3)
            channels["Lat"].append(39.81 + 0.003 * math.sin(2 * math.pi * pct))
            channels["Lon"].append(-86.23 + 0.004 * math.cos(2 * math.pi * pct))
            channels["YawRate"].append(0.35 if corner else 0.0)
            pct += speed / hz / 1000.0
            time += 1.0 / hz
            fuel -= 0.0004
            index += 1
    return {
        "channels": channels,
        "variables": [{"name": "SteeringWheelAngle", "unit": "rad"}, {"name": "SteeringWheelAngleMax", "unit": "rad"}],
        "metadata": {"sample_rate": hz},
        "session_info": {
            "WeekendInfo": {"SessionID": 91, "SubSessionID": 92, "EventType": "Race", "TrackID": 232,
                            "TrackDisplayName": "Synthetic Oval", "TrackConfigName": "Oval", "TrackType": "short oval",
                            "WeekendOptions": {"IsFixedSetup": 0}},
            "DriverInfo": {"DriverCarIdx": 0, "DriverSetupName": "synthetic.sto",
                           "Drivers": [{"CarIdx": 0, "CarID": 190, "CarScreenName": "Synthetic Stock Car", "CarPath": "synthetic"}]},
            "SessionInfo": {"Sessions": [{"SessionNum": 0, "SessionType": "Race", "SessionLaps": str(laps)}]},
            "CarSetup": supra_session(stated_ratio, offset)["CarSetup"],
        },
    }


def steering_sections(analysis: dict) -> dict:
    """Everything the app presents or reasons about for steering."""

    traces = [
        [(point["steering_rad"], point["steering_abs_peak_rad"], point["tire_stress_proxy"]) for point in trace["points"]]
        for trace in analysis["lap_traces"]["traces"]
    ]
    insights = [
        (metric["label"], metric["value"])
        for insight in analysis["technical_insights"]
        for metric in insight.get("metrics") or ()
        if "steer" in metric["label"].lower()
    ]
    investigation = analysis["race_investigation"]
    investigation_steering = [
        [(metric["label"], metric["baseline"], metric["compared"], metric["delta"])
         for metric in comparison["metrics"] if metric["key"] in ("steering", "corrections", "steering_work")]
        + [(metric["label"], metric["delta"]) for corner in comparison["corners"]
           for metric in corner["metrics"] if metric["key"] == "steering"]
        + [(action["title"], tuple(action["evidence"])) for action in comparison["actions"]]
        for comparison in investigation["comparisons"]
    ]
    zones = [
        (phase.get("metrics") or {})
        for run in analysis["corner_tire_age"].get("runs") or ()
        for zone in run.get("zones") or ()
        for phase in zone.get("observational_run_phases") or ()
    ]
    return {
        "controls": [
            {key: lap["controls"][key] for key in ("steering_abs_mean_rad", "steering_work_proxy",
                                                   "steering_corrections", "brake_steer_overlap_s")}
            for lap in analysis["laps"]
        ],
        "driving_load": [run["driving_load"] for run in analysis["runs"]],
        "traces": traces,
        "profile": [(item["bin"], item["steering_abs_rad"]) for item in analysis["track_profile"]["profile"]],
        "segments": analysis["track_profile"]["detected_corner_segments"],
        "zones": zones,
        "insights": insights,
        "investigation": investigation_steering,
    }


class SteeringRatioParsingTests(unittest.TestCase):
    def test_valid_ratios(self) -> None:
        for value, expected in (("12:1", 12.0), ("10:1", 10.0), ("16.5:1", 16.5), (" 12 : 1 ", 12.0),
                                ("14:1.0", 14.0), (12, 12.0), (15.5, 15.5), ("12", 12.0), ("12.0", 12.0)):
            with self.subTest(value=value):
                self.assertEqual(parse_steering_ratio(value), expected)

    def test_malformed_ratios_are_not_guessed(self) -> None:
        for value in ("12:2", "1:12", "0:1", "-12:1", "12:1 turns", "twelve", "", "  ", None, True,
                      "nan", "inf:1", float("nan"), float("inf"), 2, "3:1", 41, "100:1", [12], {"ratio": 12}):
            with self.subTest(value=value):
                self.assertIsNone(parse_steering_ratio(value))

    def test_supra_field_path_and_offset_are_read(self) -> None:
        recorded = recorded_steering_ratio(supra_session("12:1"))
        self.assertEqual((recorded.status, recorded.ratio, recorded.text), ("recorded", 12.0, "12:1"))
        self.assertEqual(recorded.source, "CarSetup.Chassis.Front.SteeringRatio")
        self.assertEqual(recorded.offset_text, "+6 deg")

    def test_a_ratio_kept_elsewhere_in_the_setup_is_found(self) -> None:
        session = {"CarSetup": {"InCarDials": {"Steering": {"Steering ratio": "15.2:1"}}}}
        recorded = recorded_steering_ratio(session)
        self.assertEqual((recorded.status, recorded.ratio), ("recorded", 15.2))
        self.assertEqual(recorded.source, "CarSetup.InCarDials.Steering.Steering ratio")

    def test_missing_setup_or_ratio_is_not_recorded(self) -> None:
        for session in ({}, {"CarSetup": {}}, {"CarSetup": None}, None, supra_session(None)):
            with self.subTest(session=session):
                self.assertEqual(recorded_steering_ratio(session).status, "not_recorded")
        self.assertIn("does not state a steering ratio", recorded_steering_ratio(supra_session(None)).reason)

    def test_unreadable_and_ambiguous_ratios(self) -> None:
        unreadable = recorded_steering_ratio(supra_session("12:2"))
        self.assertEqual((unreadable.status, unreadable.ratio), ("unreadable", None))
        self.assertIn("'12:2'", unreadable.reason)
        twice = {"CarSetup": {"Chassis": {"Front": {"SteeringRatio": "12:1"}, "Rear": {"SteeringRatio": "10:1"}}}}
        ambiguous = recorded_steering_ratio(twice)
        self.assertEqual((ambiguous.status, ambiguous.ratio), ("ambiguous", None))
        self.assertIn("10:1, 12:1", ambiguous.reason)
        same = {"CarSetup": {"Chassis": {"Front": {"SteeringRatio": "12:1"}, "Rear": {"SteeringRatio": "12.0:1"}}}}
        self.assertEqual(recorded_steering_ratio(same).ratio, 12.0)


class SteeringBasisTests(unittest.TestCase):
    def test_a_stated_stable_ratio_gives_road_wheel(self) -> None:
        basis = resolve_steering_basis(supra_session("12:1"), wheel_angle_max=[SUPRA_LOCK_RAD * 12] * 5)
        self.assertEqual((basis.kind, basis.status, basis.ratio), (ROAD_WHEEL, "recorded", 12.0))
        self.assertEqual(basis.label, "Steering (road wheel)")
        self.assertAlmostEqual(basis.scale, 1 / 12)
        self.assertAlmostEqual(basis.threshold(0.08), 0.08 / 12)
        self.assertEqual(basis.degree_digits, 2)
        described = basis.describe()
        self.assertEqual((described["basis"], described["steering_ratio"], described["steering_ratio_text"]),
                         ("road_wheel", 12.0, "12:1"))
        self.assertEqual(described["ratio_source"], "CarSetup.Chassis.Front.SteeringRatio")
        self.assertEqual((described["setup_steering_offset"], described["steering_offset_applied"]), ("+6 deg", False))
        self.assertIn("divided by the recording's 12:1 steering ratio", described["definition"])
        self.assertEqual(SteeringBasis.from_descriptor(described), basis)

    def test_fallbacks_are_labelled_steering_wheel_angle(self) -> None:
        cases = (
            (supra_session(None), None, "Steering wheel angle — ratio not recorded", "not_recorded"),
            (supra_session("12:2"), None, "Steering wheel angle — ratio not readable", "unreadable"),
            (supra_session("12:1"), [SUPRA_LOCK_RAD * 12] * 3 + [SUPRA_LOCK_RAD * 10] * 3,
             "Steering wheel angle — ratio changed during recording", "changed_during_recording"),
        )
        for session, lock, label, status in cases:
            with self.subTest(status=status):
                basis = resolve_steering_basis(session, wheel_angle_max=lock)
                self.assertEqual((basis.kind, basis.status, basis.ratio), (STEERING_WHEEL, status, None))
                self.assertEqual(basis.label, label)
                self.assertEqual((basis.scale, basis.threshold(0.08)), (1.0, 0.08))
                self.assertEqual(basis.describe()["evidence_class"], "measured")
                self.assertTrue(basis.reason)

    def test_float_noise_in_the_lock_is_not_a_ratio_change(self) -> None:
        lock = [SUPRA_LOCK_RAD * 12 * (1 + 1e-6 * (index % 3)) for index in range(9)]
        self.assertTrue(resolve_steering_basis(supra_session("12:1"), wheel_angle_max=lock).road_wheel)

    def test_an_analysis_without_a_descriptor_is_wheel_angle(self) -> None:
        legacy = basis_of({"lap_traces": {"traces": []}})
        self.assertEqual((legacy.kind, legacy.label), (STEERING_WHEEL, "Steering wheel angle — ratio not recorded"))
        self.assertEqual(basis_of({"lap_traces": {"steering": resolve_steering_basis(supra_session("10:1")).describe()}}).ratio, 10.0)

    def test_only_road_wheel_steering_compares_across_recordings(self) -> None:
        twelve = resolve_steering_basis(supra_session("12:1"))
        ten = resolve_steering_basis(supra_session("10:1"))
        unknown = resolve_steering_basis(supra_session(None))
        self.assertTrue(comparable_across_recordings(twelve, ten))
        self.assertFalse(comparable_across_recordings(twelve, unknown))
        self.assertFalse(comparable_across_recordings(unknown, unknown))


class SteeringOffsetTests(unittest.TestCase):
    """The setup's steering offset turns the front wheels against the wheel.

    It is recorded but not applied, so steering from setups whose offsets
    differ is never compared across recordings: Joshua's own Supra setups use
    +0, +4 and +6 deg, worth 0.33-0.5 deg of road wheel at 12:1.
    """

    @staticmethod
    def basis(ratio: str, offset: str | None) -> SteeringBasis:
        front = {"SteeringRatio": ratio}
        if offset is not None:
            front["SteeringOffset"] = offset
        return resolve_steering_basis({"CarSetup": {"Chassis": {"Front": front}}})

    def test_offsets_are_read_in_degrees(self) -> None:
        parse = steering_basis.parse_steering_offset
        for value, expected in (("+6 deg", 6.0), ("-2 deg", -2.0), ("+0 deg", 0.0), ("0 deg", 0.0), ("6°", 6.0),
                                (" +4 DEG ", 4.0), ("4.5 deg", 4.5), (6, 6.0), (-1.5, -1.5)):
            with self.subTest(value=value):
                self.assertEqual(parse(value), expected)
        for value in (None, "", "left", "6 mm", "+6 deg deg", True, float("nan"), float("inf"), [6]):
            with self.subTest(value=value):
                self.assertIsNone(parse(value))

    def test_setups_with_different_offsets_are_not_compared_across_recordings(self) -> None:
        plus_six = self.basis("12:1", "+6 deg")
        self.assertTrue(comparable_across_recordings(plus_six, self.basis("10:1", "+6 deg")))
        self.assertTrue(comparable_across_recordings(self.basis("12:1", "+0 deg"), self.basis("10:1", "0 deg")))
        self.assertFalse(comparable_across_recordings(plus_six, self.basis("12:1", "+0 deg")))
        self.assertFalse(comparable_across_recordings(self.basis("12:1", "+4 deg"), plus_six))
        # A setup without an offset matches only another without one.
        self.assertTrue(comparable_across_recordings(self.basis("12:1", None), self.basis("10:1", None)))
        self.assertFalse(comparable_across_recordings(self.basis("12:1", None), self.basis("12:1", "+0 deg")))
        # A saved declaration keeps the offset it was recorded with.
        saved = SteeringBasis.from_descriptor(plus_six.describe())
        self.assertTrue(comparable_across_recordings(saved, self.basis("10:1", "+6 deg")))
        self.assertFalse(comparable_across_recordings(saved, self.basis("12:1", "+0 deg")))


class DerivedChannelTests(unittest.TestCase):
    def test_road_wheel_channel_is_wheel_angle_divided_by_the_ratio(self) -> None:
        telemetry = recording(12, wheel_scale=12)
        table = TelemetryTable(telemetry)
        basis = _steering_basis(table)
        values, complete = _steering_column(table, basis)
        wheel = telemetry["channels"]["SteeringWheelAngle"]
        self.assertTrue(complete)
        self.assertEqual(values, [value / 12.0 for value in wheel])
        self.assertIn("SteeringWheelAngleMax", table.accessed_channels)

    def test_without_a_ratio_the_recorded_column_is_unchanged(self) -> None:
        telemetry = recording(None, wheel_scale=12)
        table = TelemetryTable(telemetry)
        values, _complete = _steering_column(table)
        self.assertEqual(values, [float(value) for value in telemetry["channels"]["SteeringWheelAngle"]])
        self.assertEqual(_steering_basis(table).status, "not_recorded")

    def test_traces_declare_the_basis_and_keep_the_wheel_angle_secondary(self) -> None:
        analysis = analyze_telemetry(recording(12, wheel_scale=12))
        traces = analysis["lap_traces"]
        self.assertEqual(traces["steering"]["basis"], "road_wheel")
        self.assertEqual(analysis["steering"], traces["steering"])
        catalog = {item["id"]: item for item in traces["additional_signal_catalog"]}
        wheel = catalog["steering-wheel-angle"]
        self.assertEqual((wheel["name"], wheel["unit"], wheel["category"], wheel["source_channels"]),
                         ("Steering wheel angle", "deg", "Controls", ["SteeringWheelAngle"]))
        for point in traces["traces"][2]["points"]:
            road = point["steering_rad"]
            wheel_deg = point["additional_signals"]["steering-wheel-angle"]
            self.assertAlmostEqual(math.degrees(road) * 12, wheel_deg, delta=0.01)
        self.assertEqual(analysis["runs"][0]["driving_load"]["steering_basis"], "road_wheel")

    def test_without_a_ratio_there_is_no_second_wheel_trace(self) -> None:
        analysis = analyze_telemetry(recording(None, wheel_scale=12))
        self.assertEqual(analysis["steering"]["label"], "Steering wheel angle — ratio not recorded")
        self.assertNotIn("steering-wheel-angle",
                         {item["id"] for item in analysis["lap_traces"]["additional_signal_catalog"]})
        insight = [metric for item in analysis["technical_insights"] for metric in item.get("metrics") or ()
                   if metric["label"] == "Average steering-wheel angle"]
        self.assertEqual(len(insight), 1)
        self.assertIn("not comparable with a setup that uses another ratio", insight[0]["detail"])
        self.assertTrue(any("Steering is steering-wheel angle, not road-wheel steering" in line
                            for line in analysis["race_investigation"]["limitations"]))


class RatioIndependenceRegressionTests(unittest.TestCase):
    """The same steering applied at 10:1 and 12:1 gives the same steering answers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ten = analyze_telemetry(oval_run(10, "10:1"))
        cls.twelve = analyze_telemetry(oval_run(12, "12:1"))

    def test_every_steering_metric_is_identical(self) -> None:
        ten, twelve = steering_sections(self.ten), steering_sections(self.twelve)
        for key in ten:
            with self.subTest(section=key):
                self.assertEqual(ten[key], twelve[key])
        # The comparison is not vacuous: corrections, overlap, load zones,
        # corner phases, the investigation and its coaching all carry steering.
        self.assertTrue(all(lap["steering_corrections"] for lap in ten["controls"]))
        self.assertTrue(all(lap["brake_steer_overlap_s"] for lap in ten["controls"]))
        self.assertEqual(len(ten["segments"]), 2)
        self.assertTrue(ten["zones"] and all("steering_average_abs_rad" in zone for zone in ten["zones"] if zone))
        self.assertEqual(ten["insights"][-1][0], "Average steering (road wheel)")
        actions = [title for comparison in ten["investigation"] for title, *_ in comparison if title == "Test a smoother steering arc"]
        self.assertTrue(actions)

    def test_the_steering_action_states_road_wheel_degrees(self) -> None:
        action = next(action for comparison in self.twelve["race_investigation"]["comparisons"]
                      for action in comparison["actions"] if action["title"] == "Test a smoother steering arc")
        self.assertIn("Median road-wheel steering increased 0.66° in the same section.", action["evidence"])
        self.assertIn("steering", action["signal_keys"])

    def test_the_road_wheel_peak_matches_the_input(self) -> None:
        peak = max(point["steering_abs_peak_rad"] for trace in self.ten["lap_traces"]["traces"] for point in trace["points"])
        # 0.004 + 0.07 + 0.0035 + 0.012 rad at the apex of a late second corner.
        self.assertAlmostEqual(math.degrees(peak), math.degrees(_quantized(0.0895)), delta=0.02)

    def test_the_wheel_angles_still_differ_by_the_ratio(self) -> None:
        def wheel_peak(analysis: dict) -> float:
            return max(abs(point["additional_signals"]["steering-wheel-angle"])
                       for trace in analysis["lap_traces"]["traces"] for point in trace["points"])
        self.assertAlmostEqual(wheel_peak(self.twelve) / wheel_peak(self.ten), 1.2, places=3)

    def test_wheel_angle_answers_were_biased_by_the_ratio(self) -> None:
        """What the ratio did before: the same steering read 20% larger at 12:1."""
        ten = analyze_telemetry(oval_run(10, None))
        twelve = analyze_telemetry(oval_run(12, None))
        self.assertEqual(ten["steering"]["basis"], "steering_wheel")
        means = [(a["controls"]["steering_abs_mean_rad"], b["controls"]["steering_abs_mean_rad"])
                 for a, b in zip(ten["laps"], twelve["laps"]) if a["controls"]["steering_abs_mean_rad"]]
        self.assertTrue(means)
        for low, high in means:
            self.assertAlmostEqual(high / low, 1.2, places=2)
        before, after = steering_sections(ten), steering_sections(twelve)
        self.assertNotEqual(before["controls"], after["controls"])
        self.assertNotEqual(before["investigation"], after["investigation"])


class CrossRecordingTests(unittest.TestCase):
    def test_saved_test_steering_is_drawn_only_between_road_wheel_recordings(self) -> None:
        source = analyze_telemetry(oval_run(12, "12:1"))
        comparison, action = next((comparison, action) for comparison in source["race_investigation"]["comparisons"]
                                  for action in comparison["actions"] if action.get("trial"))
        trial = driving_trials.create_trial(source, analysis_path=None, comparison_id=comparison["id"],
                                            action_priority=action["priority"], created_at="2026-09-25T00:00:00Z")
        self.assertEqual(trial["baseline_pass"]["steering"]["basis"], "road_wheel")
        legacy = copy.deepcopy(trial)
        del legacy["baseline_pass"]["steering"]  # a test saved before the basis existed

        def pair(saved: dict, stated: str | None) -> dict:
            later = analyze_telemetry(oval_run(10, stated))
            later["identity"]["subsession_id"] = 93
            later["analysis_id"] = f"later-{stated}"
            result = driving_trials.evaluate_trial(saved, later, evaluated_at="2026-09-26T00:00:00Z")
            self.assertIn("pair", result, result.get("reasons"))
            return result["pair"]

        kept = pair(trial, "10:1")
        self.assertEqual((kept["steering"]["basis"], kept["steering"]["comparable"]), ("road_wheel", True))
        # 12:1 before and 10:1 after: the same steering applied draws the same line.
        drawn = [(row["a"]["steering"], row["b"]["steering"]) for row in kept["rows"] if row["a"]["steering"] is not None]
        self.assertTrue(drawn and all(abs(a) < 10 and abs(b) < 10 for a, b in drawn))
        for saved, stated in ((trial, None), (legacy, "10:1")):
            with self.subTest(saved_basis=saved["baseline_pass"].get("steering", {}).get("basis"), later=stated):
                withheld = pair(saved, stated)
                self.assertEqual((withheld["steering"]["basis"], withheld["steering"]["comparable"]), (None, False))
                self.assertIn("Steering is not compared", withheld["steering"]["reason"])
                self.assertTrue(all(row["a"]["steering"] is None and row["b"]["steering"] is None for row in withheld["rows"]))
                self.assertTrue(any(row["a"]["speed"] is not None for row in withheld["rows"]))

    def test_saved_test_steering_is_withheld_when_the_steering_offset_changed(self) -> None:
        source = analyze_telemetry(oval_run(12, "12:1", offset="+6 deg"))
        comparison, action = next((comparison, action) for comparison in source["race_investigation"]["comparisons"]
                                  for action in comparison["actions"] if action.get("trial"))
        trial = driving_trials.create_trial(source, analysis_path=None, comparison_id=comparison["id"],
                                            action_priority=action["priority"], created_at="2026-09-25T00:00:00Z")
        self.assertEqual(trial["baseline_pass"]["steering"]["setup_steering_offset"], "+6 deg")

        def pair(offset: str) -> dict:
            later = analyze_telemetry(oval_run(12, "12:1", offset=offset))
            later["identity"]["subsession_id"] = 93
            later["analysis_id"] = f"later-{offset}"
            result = driving_trials.evaluate_trial(trial, later, evaluated_at="2026-09-26T00:00:00Z")
            self.assertIn("pair", result, result.get("reasons"))
            return result["pair"]

        self.assertTrue(pair("+6 deg")["steering"]["comparable"])
        changed = pair("+0 deg")
        self.assertEqual((changed["steering"]["basis"], changed["steering"]["comparable"]), (None, False))
        self.assertIn("steering offset changed (+6 deg → +0 deg)", changed["steering"]["reason"])
        self.assertTrue(all(row["a"]["steering"] is None and row["b"]["steering"] is None for row in changed["rows"]))
        self.assertTrue(any(row["a"]["speed"] is not None for row in changed["rows"]))

    def test_tire_learning_matches_steering_work_only_between_equal_steering_offsets(self) -> None:
        self.assertEqual(analyze_telemetry(oval_run(12, "12:1"))["runs"][0]["driving_load"]["steering_offset"], "+6 deg")
        corners = ("LF", "RF", "LR", "RR")
        conditions = {"track_temp_c": 32.0, "air_temp_c": 22.0, "caution_fraction": 0.1, "tire_compound": 0}

        def load(offset: str | None, work: float) -> dict:
            value = {"early_brake_energy_proxy": 10.0 + work / 100, "late_brake_energy_proxy": 12.0,
                     "early_steering_work_proxy": work, "late_steering_work_proxy": work,
                     "steering_basis": "road_wheel", "steering_offset": offset}
            if offset == "missing":  # persisted before the offset was stored
                del value["steering_offset"]
            return value

        def observations(offset: str | None) -> list[dict]:
            return [{"observation_id": f"{offset}-{index}", "analysis_id": f"{offset}-{index}", "eligible_for_rate_model": True,
                     "tires": {corner: {"green_laps_on_set": 20,
                                        "remaining_percent_omi": {"outer": 80, "middle": 82, "inner": 84}} for corner in corners},
                     "pace": {}, "conditions": dict(conditions), "load": load(offset, 40.0 + index),
                     "vehicle_dynamics": {}} for index in range(3)]

        age = {corner: {"green_laps": 10} for corner in corners}
        context = {"conditions": dict(conditions), "load": load("+0 deg", 41.0), "vehicle_dynamics": {}}
        same = race_foundations.build_tire_prediction(observations("0 deg"), age, context)
        self.assertIn("steering_work", same["matched_features"])
        for offset in ("+6 deg", None, "missing"):
            with self.subTest(observation_offset=offset):
                other = race_foundations.build_tire_prediction(observations(offset), age, context)
                self.assertEqual(other["status"], "predicted")
                self.assertNotIn("steering_work", other["matched_features"])
                self.assertIn("brake_energy", other["matched_features"])

    def test_tire_learning_matches_steering_work_only_on_road_wheel(self) -> None:
        def observation(basis: str | None, work: float) -> dict:
            load = {"early_brake_energy_proxy": 10.0, "late_brake_energy_proxy": 12.0,
                    "early_steering_work_proxy": work, "late_steering_work_proxy": work}
            if basis is not None:
                load["steering_basis"] = basis
            return {"load": load, "conditions": {}, "vehicle_dynamics": {}}
        features = race_foundations.tire_feature_values
        self.assertEqual(features(observation("road_wheel", 40.0))["steering_work"], 40.0)
        # A steering-wheel or pre-basis observation keeps its brake load but
        # contributes no steering work to the match.
        for basis in ("steering_wheel", None):
            with self.subTest(basis=basis):
                matched = features(observation(basis, 480.0))
                self.assertNotIn("steering_work", matched)
                self.assertEqual(matched["brake_energy"], 11.0)
        self.assertEqual(race_foundations.TIRE_MODEL_VERSION, "nascar-tire-condition-load-match-v2")

    def test_garage61_reference_steering_is_never_a_delta(self) -> None:
        self.assertIn("steering_wheel_abs_rad", workflow._CSV_SIGNAL_ALIASES)
        self.assertNotIn("steering_abs_rad", workflow._CSV_SIGNAL_ALIASES)
        local = [{"bin": index, "lap_pct": (index + 0.5) / 20, "speed_mph": 100.0 - index, "brake": 0.0,
                  "throttle": 0.8, "steering_abs_rad": 0.05, "lateral_g": 1.0} for index in range(20)]
        reference = [{"bin": index, "lap_pct": (index + 0.5) / 20, "speed_mph": 101.0 - index, "brake": 0.0,
                      "throttle": 0.8, "steering_wheel_abs_rad": 0.9, "lateral_g": 1.0} for index in range(20)]
        target = workflow._zone_coaching_target({"segment": 1, "start_pct": 0.1, "end_pct": 0.6}, local, reference, 1)
        self.assertIsNone(target["steering_delta_rad"])
        self.assertIn("steering ratio", target["quality"]["steering_comparison"])
        self.assertNotIn("steering angle", target["coaching"])


class PresentationTests(unittest.TestCase):
    def test_report_and_insights_name_the_basis(self) -> None:
        analysis = analyze_telemetry(oval_run(12, "12:1"))
        report = render_report(analysis)
        self.assertIn("steer (road wheel) 2.", report)
        self.assertNotIn(" rad;", report)
        metric = next(metric for item in analysis["technical_insights"] for metric in item.get("metrics") or ()
                      if metric["label"] == "Average steering (road wheel)")
        self.assertIn("12:1 steering ratio", metric["detail"])
        self.assertIn("At the steering wheel:", metric["detail"])
        overlap = next(metric for item in analysis["technical_insights"] for metric in item.get("metrics") or ()
                       if metric["label"] == "Brake / steer overlap")
        self.assertIn("0.38° of road-wheel steering", overlap["detail"])

    def test_insights_rebuilt_from_a_saved_analysis_keep_the_basis(self) -> None:
        analysis = analyze_telemetry(oval_run(10, "10:1"))
        rebuilt = build_technical_insights(
            analysis["laps"], analysis["runs"], analysis["race_summary"], analysis["strategy"],
            analysis["damage_repair"], analysis["tire_learning"],
            race_investigation=analysis["race_investigation"], steering=analysis["steering"],
        )
        self.assertEqual(rebuilt, analysis["technical_insights"])

    def test_investigation_and_pairs_carry_the_basis(self) -> None:
        analysis = analyze_telemetry(oval_run(12, "12:1"))
        investigation = analysis["race_investigation"]
        self.assertEqual(investigation["steering"]["label"], "Steering (road wheel)")
        pairs = [pair for comparison in investigation["comparisons"]
                 for pair in (comparison.get("inspection") or {}).get("sections", {}).values()]
        self.assertTrue(pairs)
        for pair in pairs:
            self.assertEqual((pair["steering"]["basis"], pair["steering"]["label"]), ("road_wheel", "Steering (road wheel)"))
            # Road-wheel degrees keep two decimals in the section rows.
            steering = [row["a"]["steering"] for row in pair["rows"] if row["a"]["steering"] is not None]
            self.assertTrue(steering and max(steering) < 10.0)
        corner_labels = {metric["label"] for comparison in investigation["comparisons"] for corner in comparison["corners"]
                         for metric in corner["metrics"] if metric["key"] == "steering"}
        self.assertEqual(corner_labels, {"Steering (road wheel)"})
        labels = {metric["label"] for comparison in investigation["comparisons"] for metric in comparison["metrics"]
                  if metric["key"] == "steering"}
        self.assertEqual(labels, {"Average steering (road wheel)"})


class TuningFeedbackTests(unittest.TestCase):
    def test_every_observation_with_a_steering_metric_names_its_basis(self) -> None:
        analysis = tuning_analysis()
        analysis["steering"] = resolve_steering_basis(supra_session("12:1")).describe()
        runs = [{"run_number": 1, "run_id": "run-1"}]
        observed = {}
        for phase in ("whole", "center", "entry"):
            report = dict(tuning_feedback()[0], feedback_id=f"feedback-{phase}", corner_label="Turn 1", corner_phases=[phase])
            observations, _limitations = tuning_engine.build_feedback_observations(analysis, [report], runs, tuning_map())
            observed[phase] = observations[0]
        # The whole-corner summary carries road-wheel steering work but no
        # average steering; it is labelled all the same.
        self.assertIn("steering_work_proxy", observed["whole"]["metrics"])
        self.assertNotIn("steering_average_abs_rad", observed["whole"]["metrics"])
        self.assertEqual(observed["whole"]["steering_label"], "Steering (road wheel)")
        self.assertEqual(observed["center"]["steering_label"], "Steering (road wheel)")
        self.assertNotIn("steering_label", observed["entry"])
        legacy = dict(tuning_feedback()[0], corner_label="Turn 1", corner_phases=["whole"])
        observations, _limitations = tuning_engine.build_feedback_observations(tuning_analysis(), [legacy], runs, tuning_map())
        self.assertEqual(observations[0]["steering_label"], "Steering wheel angle — ratio not recorded")


class LapSummaryThresholdTests(unittest.TestCase):
    def test_overlap_and_corrections_use_road_wheel_thresholds(self) -> None:
        # 0.5 deg of road wheel is material steering (>= 0.38 deg); at 12:1 the
        # wheel reads 6 deg, at 10:1 5 deg, and both count the same overlap.
        for ratio in (10, 12):
            with self.subTest(ratio=ratio):
                telemetry = recording(ratio, wheel_scale=ratio)
                telemetry["channels"]["SteeringWheelAngle"] = [math.radians(0.5) * ratio] * len(
                    telemetry["channels"]["SteeringWheelAngle"])
                laps = _lap_summaries(TelemetryTable(telemetry))
                overlaps = [lap["controls"]["brake_steer_overlap_s"] for lap in laps]
                self.assertTrue(all(value and value > 0 for value in overlaps[1:3]))
                if ratio == 10:
                    first = overlaps
                else:
                    self.assertEqual(first, overlaps)


if __name__ == "__main__":
    unittest.main()
