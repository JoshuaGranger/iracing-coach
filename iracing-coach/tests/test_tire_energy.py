"""Cases for per-corner tire energy and the wear diagnostic.

`TIRE-ENERGY-001`.

Energy is an integral, so the known-answer cases below drive a constant
acceleration at a constant speed for a known duration and assert the exact
J/kg that must fall out. The adversarial cases cover the ways an energy
account can mislead: billing a timing gap as driving, grading a load-only
figure as slip-weighted, and reporting a correlation over too few points.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import tire_energy as te  # noqa: E402


def uniform_lap(
    *,
    samples: int = 1000,
    speed: float = 50.0,
    lat_accel=None,
    long_accel=None,
    hz: float = 50.0,
    velocity_components: bool = False,
    sideslip_rad: float = 0.0,
):
    """One lap of evenly spaced samples with prescribed accelerations."""

    lap_pct = [index / samples for index in range(samples)]
    times = [index / hz for index in range(samples)]
    speeds = [speed] * samples
    lats = [
        lat_accel(pct) if callable(lat_accel) else (lat_accel or 0.0) for pct in lap_pct
    ]
    longs = [
        long_accel(pct) if callable(long_accel) else (long_accel or 0.0)
        for pct in lap_pct
    ]
    kwargs = {}
    if velocity_components:
        kwargs["velocity_x_m_s"] = [speed * math.cos(sideslip_rad)] * samples
        kwargs["velocity_y_m_s"] = [speed * math.sin(sideslip_rad)] * samples
    return dict(
        lap_dist_pct=lap_pct,
        session_time_s=times,
        speed_m_s=speeds,
        lat_accel_m_s2=lats,
        long_accel_m_s2=longs,
        sample_rate_hz=hz,
        **kwargs,
    )


def two_segments():
    return [
        {"name": "A", "start_pct": 0.0, "end_pct": 0.5},
        {"name": "B", "start_pct": 0.5, "end_pct": 1.0},
    ]


class KnownAnswerTests(unittest.TestCase):
    def test_constant_lateral_load_integrates_exactly(self) -> None:
        # 10 m/s^2 lateral at 50 m/s for half a lap. At 50 Hz with 1000
        # samples the lap is 20 s, so each half is 10 s: 10*50*10 = 5000 J/kg.
        report = te.segment_energy(
            **uniform_lap(lat_accel=lambda pct: 10.0 if pct < 0.5 else 0.0),
            segments=two_segments(),
        )
        by_name = {item.name: item for item in report.segments}
        self.assertAlmostEqual(by_name["A"].lateral_load_j_per_kg, 5000.0, delta=10.0)
        self.assertAlmostEqual(by_name["B"].lateral_load_j_per_kg, 0.0, delta=1e-6)
        self.assertEqual(report.status, te.STATUS_USABLE)

    def test_share_of_lap_reflects_where_the_load_is(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=lambda pct: 10.0 if pct < 0.5 else 5.0),
            segments=two_segments(),
        )
        by_name = {item.name: item for item in report.segments}
        self.assertAlmostEqual(by_name["A"].share_of_lap, 2.0 / 3.0, places=3)
        self.assertAlmostEqual(by_name["B"].share_of_lap, 1.0 / 3.0, places=3)
        self.assertEqual(report.segments[0].name, "A", "ranked by share")

    def test_longitudinal_and_lateral_are_kept_separate(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=4.0, long_accel=3.0), segments=two_segments()
        )
        segment = report.segments[0]
        self.assertGreater(segment.lateral_load_j_per_kg, 0.0)
        self.assertGreater(segment.longitudinal_load_j_per_kg, 0.0)
        self.assertAlmostEqual(
            segment.total_load_j_per_kg,
            segment.lateral_load_j_per_kg + segment.longitudinal_load_j_per_kg,
            places=6,
        )

    def test_peak_lateral_g_is_reported(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=lambda pct: 19.6133 if pct < 0.25 else 1.0),
            segments=two_segments(),
        )
        by_name = {item.name: item for item in report.segments}
        self.assertAlmostEqual(by_name["A"].peak_lateral_g, 2.0, places=3)


class SlipGradingTests(unittest.TestCase):
    def test_slip_weighting_reduces_the_figure_and_grades_it(self) -> None:
        sideslip = math.radians(6.0)
        report = te.segment_energy(
            **uniform_lap(
                lat_accel=10.0, velocity_components=True, sideslip_rad=sideslip
            ),
            segments=two_segments(),
        )
        segment = report.segments[0]
        self.assertEqual(segment.grade, te.GRADE_SLIP_WEIGHTED)
        self.assertAlmostEqual(segment.slip_coverage, 1.0, places=6)
        self.assertAlmostEqual(
            segment.lateral_slip_j_per_kg,
            segment.lateral_load_j_per_kg * math.sin(sideslip),
            places=3,
        )
        self.assertLess(segment.lateral_slip_j_per_kg, segment.lateral_load_j_per_kg)

    def test_absent_velocity_components_grade_as_load_only(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=10.0), segments=two_segments()
        )
        self.assertEqual(report.grade, te.GRADE_LOAD_ONLY)
        for segment in report.segments:
            self.assertIsNone(segment.lateral_slip_j_per_kg)
            self.assertEqual(segment.slip_coverage, 0.0)

    def test_low_speed_samples_do_not_produce_slip_weighting(self) -> None:
        """Below the derivation guard, sideslip is undefined, not zero."""

        report = te.segment_energy(
            **uniform_lap(
                lat_accel=10.0,
                speed=2.0,
                velocity_components=True,
                sideslip_rad=math.radians(20.0),
            ),
            segments=two_segments(),
        )
        for segment in report.segments:
            self.assertIsNone(segment.lateral_slip_j_per_kg)
        self.assertEqual(report.grade, te.GRADE_LOAD_ONLY)


class AdversarialTests(unittest.TestCase):
    def test_a_timing_gap_is_not_billed_as_driving(self) -> None:
        """A stopped session must not accumulate a lap's worth of energy."""

        data = uniform_lap(lat_accel=10.0, samples=100, hz=50.0)
        # A 600 second pause between two samples, as a session hold produces.
        for index in range(50, 100):
            data["session_time_s"][index] += 600.0
        report = te.segment_energy(**data, segments=two_segments())
        total = report.total_load_j_per_kg
        # 100 samples at 50 Hz is 2 s of driving: 10*50*2 = 1000 J/kg.
        self.assertLess(
            total,
            1500.0,
            msg=f"a 600 s hold was billed as driving ({total:.0f} J/kg)",
        )

    def test_missing_channels_yield_unavailable_not_zero(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=10.0),
            segments=two_segments(),
            missing_channels=["LatAccel"],
        )
        self.assertEqual(report.status, te.STATUS_UNAVAILABLE)
        self.assertIsNone(report.total_load_j_per_kg)
        self.assertEqual(report.missing_channels, ("LatAccel",))
        self.assertEqual(report.segments, ())

    def test_wrapping_segment_is_not_accumulated(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=10.0),
            segments=[
                {"name": "wrap", "start_pct": 0.9, "end_pct": 0.1, "wraps_start_finish": True},
                {"name": "ok", "start_pct": 0.1, "end_pct": 0.9},
            ],
        )
        by_name = {item.name: item for item in report.segments}
        self.assertEqual(by_name["wrap"].status, te.STATUS_UNAVAILABLE)
        self.assertEqual(by_name["wrap"].sample_count, 0)
        self.assertEqual(report.status, te.STATUS_LIMITED)

    def test_stationary_samples_contribute_nothing(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=10.0, speed=0.0), segments=two_segments()
        )
        self.assertEqual(report.status, te.STATUS_UNAVAILABLE)


class WearDiagnosticTests(unittest.TestCase):
    #: Stint length and energy per lap must vary *independently*, or the two
    #: candidate explanations are collinear and the diagnostic cannot separate
    #: them however it is implemented. These stand for long gentle stints and
    #: short aggressive ones.
    LAPS = (10, 20, 12, 25, 15, 30, 18, 22)
    ENERGY_PER_LAP = (500.0, 200.0, 450.0, 180.0, 400.0, 150.0, 380.0, 220.0)

    @classmethod
    def _observations(cls, count: int, *, energy_drives_wear: bool):
        items = []
        for index in range(count):
            laps = cls.LAPS[index % len(cls.LAPS)]
            energy = laps * cls.ENERGY_PER_LAP[index % len(cls.ENERGY_PER_LAP)]
            wear = energy / 400.0 if energy_drives_wear else laps * 1.5
            items.append(
                {"energy_j_per_kg": energy, "laps": float(laps), "wear_percent": wear}
            )
        return items

    def test_the_fixture_itself_is_not_collinear(self) -> None:
        """Guard the guard: a collinear fixture would prove nothing."""

        items = self._observations(8, energy_drives_wear=True)
        correlation = te._pearson(
            [item["energy_j_per_kg"] for item in items],
            [item["laps"] for item in items],
        )
        self.assertLess(abs(correlation), 0.9)

    def test_too_few_observations_are_refused(self) -> None:
        result = te.wear_energy_diagnostic(
            self._observations(3, energy_drives_wear=True)
        )
        self.assertEqual(result.status, te.STATUS_UNAVAILABLE)
        self.assertEqual(result.reason, "insufficient_paired_observations")
        self.assertIsNone(result.energy_explains_more)

    def test_energy_explaining_wear_is_reported(self) -> None:
        result = te.wear_energy_diagnostic(
            self._observations(8, energy_drives_wear=True)
        )
        self.assertEqual(result.status, te.STATUS_USABLE)
        self.assertTrue(result.energy_explains_more)
        self.assertAlmostEqual(abs(result.energy_r), 1.0, places=6)

    def test_laps_explaining_wear_is_reported_honestly(self) -> None:
        result = te.wear_energy_diagnostic(
            self._observations(8, energy_drives_wear=False)
        )
        self.assertEqual(result.status, te.STATUS_USABLE)
        self.assertFalse(
            result.energy_explains_more,
            "an energy account that loses to lap count must say so",
        )

    def test_degenerate_variance_is_limited_not_usable(self) -> None:
        items = [
            {"energy_j_per_kg": 100.0, "laps": 10, "wear_percent": 5.0}
            for _ in range(6)
        ]
        result = te.wear_energy_diagnostic(items)
        self.assertEqual(result.status, te.STATUS_LIMITED)
        self.assertEqual(result.reason, "degenerate_variance")

    def test_incomplete_observations_are_dropped_before_counting(self) -> None:
        items = self._observations(6, energy_drives_wear=True)
        items.append({"energy_j_per_kg": None, "laps": 3, "wear_percent": 1.0})
        result = te.wear_energy_diagnostic(items)
        self.assertEqual(result.observation_count, 6)


class PayloadTests(unittest.TestCase):
    def test_payloads_are_json_safe_and_declare_units(self) -> None:
        report = te.segment_energy(
            **uniform_lap(lat_accel=10.0), segments=two_segments()
        )
        payload = report.to_payload()
        json.dumps(payload)
        self.assertEqual(payload["units"], "J/kg")
        self.assertIn("not a wear model", payload["classification"])
        diagnostic = te.wear_energy_diagnostic([]).to_payload()
        json.dumps(diagnostic)
        self.assertEqual(
            diagnostic["minimum_paired_observations"], te.MINIMUM_PAIRED_OBSERVATIONS
        )


if __name__ == "__main__":
    unittest.main()
