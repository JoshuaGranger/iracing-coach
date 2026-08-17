"""Known-answer and adversarial cases for distance-domain lap alignment.

`REFERENCE-DELTA-001`.

The cases here are chosen so that each one fails loudly if a specific, quiet
error is reintroduced:

* aligning on sample index instead of distance (``test_sampling_phase_*``);
* letting the anchor leak into per-segment attribution (``test_segment_*``);
* interpolating across a recording dropout (``test_gap_*``);
* accepting samples from a car travelling backwards (``test_regression_*``).

Every lap below is synthesized from an explicit cumulative time-versus-distance
function, so the correct delta is known in closed form rather than asserted
against whatever the implementation happens to produce.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lap_reference as lr  # noqa: E402


def synth_lap(
    time_at_pct,
    *,
    samples: int = 400,
    start_time: float = 0.0,
    first_pct: float = 0.0,
    last_pct: float = 1.0,
    speed_at_pct=None,
):
    """Build channel lists for one lap from a cumulative time function."""

    distance: list[float] = []
    times: list[float] = []
    speeds: list[float] = []
    for index in range(samples + 1):
        pct = first_pct + (last_pct - first_pct) * index / samples
        distance.append(pct)
        times.append(start_time + time_at_pct(pct))
        speeds.append(speed_at_pct(pct) if speed_at_pct else 50.0)
    return distance, times, speeds


def constant(lap_time: float):
    return lambda pct: lap_time * pct


class LapTraceTests(unittest.TestCase):
    def test_constant_speed_lap_is_fully_covered(self) -> None:
        distance, times, speeds = synth_lap(constant(90.0))
        trace = lr.build_lap_trace(
            distance, times, range(len(distance)), speed_mps=speeds, lap_number=7
        )
        self.assertEqual(trace.status, lr.STATUS_USABLE)
        self.assertEqual(trace.lap_number, 7)
        self.assertAlmostEqual(trace.coverage, 1.0, places=6)
        self.assertAlmostEqual(trace.covered_time_s, 90.0, places=6)
        self.assertEqual(trace.gap_count, 0)
        self.assertEqual(trace.regressed_sample_count, 0)

    def test_linear_interpolation_is_exact_for_constant_speed(self) -> None:
        distance, times, _ = synth_lap(constant(100.0), samples=200)
        trace = lr.build_lap_trace(distance, times, range(len(distance)), bins=100)
        for index in range(101):
            self.assertIsNotNone(trace.session_time_s[index])
            self.assertAlmostEqual(trace.session_time_s[index], index, places=9)

    def test_partial_lap_reports_reduced_coverage(self) -> None:
        distance, times, _ = synth_lap(constant(80.0), first_pct=0.30, last_pct=0.90)
        trace = lr.build_lap_trace(distance, times, range(len(distance)), bins=100)
        self.assertEqual(trace.status, lr.STATUS_LIMITED)
        self.assertIsNone(trace.session_time_s[0])
        self.assertIsNone(trace.session_time_s[100])
        self.assertIsNotNone(trace.session_time_s[50])

    def test_gap_is_not_interpolated_across(self) -> None:
        # Drop every sample between 40% and 60% of the lap.
        distance, times, _ = synth_lap(constant(120.0), samples=500)
        kept = [
            index
            for index, pct in enumerate(distance)
            if not 0.40 < pct < 0.60
        ]
        trace = lr.build_lap_trace(distance, times, kept, bins=100)
        self.assertEqual(trace.gap_count, 1)
        for index in range(41, 60):
            self.assertIsNone(
                trace.session_time_s[index],
                msg=f"grid point {index} was filled across a recording dropout",
            )
        self.assertIsNotNone(trace.session_time_s[39])
        self.assertIsNotNone(trace.session_time_s[61])

    def test_small_dropout_within_tolerance_is_bridged(self) -> None:
        distance, times, _ = synth_lap(constant(120.0), samples=500)
        kept = [index for index, pct in enumerate(distance) if not 0.500 < pct < 0.505]
        trace = lr.build_lap_trace(distance, times, kept, bins=100)
        self.assertEqual(trace.gap_count, 0)
        self.assertIsNotNone(trace.session_time_s[50])

    def test_regression_samples_are_dropped_not_reordered(self) -> None:
        distance, times, _ = synth_lap(constant(90.0), samples=200)
        # A spin at mid-lap sends distance backwards for several samples.
        for offset in range(100, 106):
            distance[offset] = distance[99] - 0.001 * (offset - 99)
        trace = lr.build_lap_trace(distance, times, range(len(distance)), bins=100)
        self.assertEqual(trace.regressed_sample_count, 6)
        covered = [value for value in trace.session_time_s if value is not None]
        self.assertEqual(covered, sorted(covered), "time must not invert after a spin")

    def test_rejects_invalid_grid(self) -> None:
        with self.assertRaises(ValueError):
            lr.build_lap_trace([0.0, 1.0], [0.0, 1.0], [0, 1], bins=0)
        with self.assertRaises(ValueError):
            lr.build_lap_trace([0.0, 1.0], [0.0, 1.0], [0, 1], max_gap_pct=0.0)


class DeltaTraceTests(unittest.TestCase):
    def test_uniformly_slower_lap_accumulates_exact_difference(self) -> None:
        reference = lr.build_lap_trace(
            *synth_lap(constant(100.0))[:2], range(401), bins=100, lap_number=1
        )
        comparison = lr.build_lap_trace(
            *synth_lap(constant(101.0), start_time=500.0)[:2],
            range(401),
            bins=100,
            lap_number=2,
        )
        delta = lr.compare_laps(reference, comparison)
        self.assertEqual(delta.status, lr.STATUS_USABLE)
        self.assertEqual(delta.anchor_index, 0)
        self.assertAlmostEqual(delta.delta_s[0], 0.0, places=9)
        self.assertAlmostEqual(delta.delta_s[50], 0.5, places=6)
        self.assertAlmostEqual(delta.total_delta_s, 1.0, places=6)

    def test_sampling_phase_does_not_create_delta(self) -> None:
        """The same physical lap logged on a different sample phase is equal.

        This is the failure that anchoring on the first *sample* rather than a
        shared *distance* produces, and it is invisible unless asserted: the
        delta looks small and plausible while being pure sampling artifact.
        """

        profile = lambda pct: 90.0 * pct + 4.0 * math.sin(2.0 * math.pi * pct)
        distance_a, times_a, _ = synth_lap(profile, samples=200, first_pct=0.0)
        distance_b, times_b, _ = synth_lap(
            profile, samples=200, first_pct=0.0025, start_time=1234.5
        )
        reference = lr.build_lap_trace(distance_a, times_a, range(201), bins=200)
        comparison = lr.build_lap_trace(distance_b, times_b, range(201), bins=200)
        delta = lr.compare_laps(reference, comparison)
        for index, value in enumerate(delta.delta_s):
            if value is None:
                continue
            self.assertLess(
                abs(value),
                0.01,
                msg=f"sampling phase produced {value:.4f}s of delta at grid {index}",
            )

    def test_mismatched_grids_are_refused(self) -> None:
        reference = lr.build_lap_trace(
            *synth_lap(constant(90.0))[:2], range(401), bins=100
        )
        comparison = lr.build_lap_trace(
            *synth_lap(constant(90.0))[:2], range(401), bins=200
        )
        with self.assertRaises(ValueError):
            lr.compare_laps(reference, comparison)

    def test_no_shared_coverage_yields_no_anchor(self) -> None:
        reference = lr.build_lap_trace(
            *synth_lap(constant(90.0), first_pct=0.0, last_pct=0.30)[:2],
            range(401),
            bins=100,
        )
        comparison = lr.build_lap_trace(
            *synth_lap(constant(90.0), first_pct=0.60, last_pct=0.95)[:2],
            range(401),
            bins=100,
        )
        delta = lr.compare_laps(reference, comparison)
        self.assertIsNone(delta.anchor_index)
        self.assertEqual(delta.status, lr.STATUS_UNAVAILABLE)
        self.assertIsNone(delta.total_delta_s)


class SegmentDeltaTests(unittest.TestCase):
    """A lap that loses all its time in one place must say so."""

    @staticmethod
    def _localized_loss(lap_time: float, loss: float, start: float, end: float):
        def profile(pct: float) -> float:
            if pct <= start:
                fraction = 0.0
            elif pct >= end:
                fraction = 1.0
            else:
                fraction = (pct - start) / (end - start)
            return lap_time * pct + loss * fraction

        return profile

    def test_loss_is_attributed_to_the_segment_that_caused_it(self) -> None:
        reference = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=1000)[:2], range(1001), bins=100
        )
        comparison = lr.build_lap_trace(
            *synth_lap(
                self._localized_loss(100.0, 0.5, 0.40, 0.50), samples=1000
            )[:2],
            range(1001),
            bins=100,
        )
        segments = lr.uniform_segments(10)
        results = lr.segment_deltas(reference, comparison, segments)
        by_name = {item.name: item for item in results}
        self.assertAlmostEqual(by_name["S5"].delta_s, 0.5, places=4)
        for name in ("S1", "S2", "S3", "S4", "S6", "S9", "S10"):
            self.assertAlmostEqual(
                by_name[name].delta_s,
                0.0,
                places=4,
                msg=f"{name} absorbed time it did not lose",
            )

    def test_segment_delta_is_independent_of_anchor(self) -> None:
        """Attribution must survive a lap whose recording starts late.

        Only the anchor is varied here: the late traces are built from the very
        same samples as the full ones, restricted to the tail of the lap. That
        isolates the property under test. Varying the sample spacing as well
        would fold ordinary interpolation error into the comparison and prove
        nothing about anchoring.
        """

        profile = self._localized_loss(100.0, 0.4, 0.70, 0.80)
        reference_channels = synth_lap(constant(100.0), samples=1000)
        comparison_channels = synth_lap(profile, samples=1000)
        late_indices = [
            index
            for index, pct in enumerate(reference_channels[0])
            if pct >= 0.55
        ]

        full_reference = lr.build_lap_trace(
            *reference_channels[:2], range(1001), bins=100
        )
        full_comparison = lr.build_lap_trace(
            *comparison_channels[:2], range(1001), bins=100
        )
        late_reference = lr.build_lap_trace(
            *reference_channels[:2], late_indices, bins=100
        )
        late_comparison = lr.build_lap_trace(
            *comparison_channels[:2], late_indices, bins=100
        )

        segments = [{"name": "T7", "start_pct": 0.70, "end_pct": 0.80}]
        full = lr.segment_deltas(full_reference, full_comparison, segments)[0]
        late = lr.segment_deltas(late_reference, late_comparison, segments)[0]
        self.assertEqual(full.status, lr.STATUS_USABLE)
        self.assertEqual(late.status, lr.STATUS_USABLE)
        self.assertNotEqual(
            lr.compare_laps(full_reference, full_comparison).anchor_index,
            lr.compare_laps(late_reference, late_comparison).anchor_index,
            msg="the two comparisons must genuinely differ in anchor",
        )
        self.assertAlmostEqual(full.delta_s, late.delta_s, places=9)
        self.assertAlmostEqual(full.delta_s, 0.4, places=4)

    def test_sample_spacing_changes_attribution_only_below_a_millisecond(self) -> None:
        """Resampling the same drive more coarsely must not move a finding.

        Distinct from the anchor case above: here the *samples* differ, so a
        kink in the profile cannot land identically. The residual is ordinary
        interpolation error and is pinned well below the resolution any
        coaching claim is made at.

        The two rates bracket real recordings: 4000 samples on a 100 s lap is
        40 Hz and 2000 is 20 Hz, against the 60 Hz an IBT normally carries. The
        profile's loss also begins as an instantaneous kink, which is the worst
        case for linear interpolation and harsher than anything a car does.
        """

        profile = self._localized_loss(100.0, 0.4, 0.70, 0.80)
        segments = [{"name": "T7", "start_pct": 0.70, "end_pct": 0.80}]
        dense_reference = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=4000)[:2], range(4001), bins=100
        )
        dense_comparison = lr.build_lap_trace(
            *synth_lap(profile, samples=4000)[:2], range(4001), bins=100
        )
        sparse_reference = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=2000)[:2], range(2001), bins=100
        )
        sparse_comparison = lr.build_lap_trace(
            *synth_lap(profile, samples=2000)[:2], range(2001), bins=100
        )
        dense = lr.segment_deltas(dense_reference, dense_comparison, segments)[0]
        sparse = lr.segment_deltas(sparse_reference, sparse_comparison, segments)[0]
        self.assertLess(abs(dense.delta_s - sparse.delta_s), 0.001)

    def test_segment_without_both_boundaries_is_unavailable(self) -> None:
        reference = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=1000)[:2], range(1001), bins=100
        )
        comparison = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=1000, first_pct=0.50)[:2],
            range(1001),
            bins=100,
        )
        segments = [{"name": "T1", "start_pct": 0.10, "end_pct": 0.20}]
        result = lr.segment_deltas(reference, comparison, segments)[0]
        self.assertEqual(result.status, lr.STATUS_UNAVAILABLE)
        self.assertIsNone(result.delta_s)

    def test_malformed_segment_is_unavailable_not_an_error(self) -> None:
        trace = lr.build_lap_trace(
            *synth_lap(constant(100.0))[:2], range(401), bins=100
        )
        results = lr.segment_deltas(
            trace,
            trace,
            [
                {"name": "backwards", "start_pct": 0.8, "end_pct": 0.2},
                {"name": "missing", "start_pct": None, "end_pct": 0.5},
            ],
        )
        self.assertTrue(all(item.status == lr.STATUS_UNAVAILABLE for item in results))

    def test_minimum_speed_is_carried_for_each_segment(self) -> None:
        speed = lambda pct: 20.0 if 0.40 < pct < 0.50 else 60.0
        distance, times, speeds = synth_lap(constant(100.0), samples=1000, speed_at_pct=speed)
        trace = lr.build_lap_trace(
            distance, times, range(1001), speed_mps=speeds, bins=100
        )
        results = lr.segment_deltas(trace, trace, lr.uniform_segments(10))
        by_name = {item.name: item for item in results}
        self.assertAlmostEqual(by_name["S5"].reference_minimum_speed_mps, 20.0, places=3)
        self.assertAlmostEqual(by_name["S1"].reference_minimum_speed_mps, 60.0, places=3)


class TheoreticalBestTests(unittest.TestCase):
    def test_best_segments_are_taken_from_different_laps(self) -> None:
        segments = lr.uniform_segments(4)
        # Lap 1 is quick in the first half, lap 2 in the second.
        lap_one = lr.build_lap_trace(
            *synth_lap(
                SegmentDeltaTests._localized_loss(100.0, 2.0, 0.50, 1.00), samples=1000
            )[:2],
            range(1001),
            bins=100,
            lap_number=1,
        )
        lap_two = lr.build_lap_trace(
            *synth_lap(
                SegmentDeltaTests._localized_loss(100.0, 2.0, 0.00, 0.50), samples=1000
            )[:2],
            range(1001),
            bins=100,
            lap_number=2,
        )
        best = lr.theoretical_best([lap_one, lap_two], segments)
        self.assertEqual(best.status, lr.STATUS_USABLE)
        self.assertEqual(best.contributing_lap[0], 1)
        self.assertEqual(best.contributing_lap[3], 2)
        self.assertLess(best.total_s, 101.0)
        self.assertGreater(best.total_s, 99.9)

    def test_incomplete_segment_coverage_is_limited(self) -> None:
        trace = lr.build_lap_trace(
            *synth_lap(constant(100.0), samples=1000, first_pct=0.50)[:2],
            range(1001),
            bins=100,
            lap_number=3,
        )
        best = lr.theoretical_best([trace], lr.uniform_segments(4))
        self.assertEqual(best.status, lr.STATUS_LIMITED)
        self.assertIsNone(best.total_s)


class PayloadTests(unittest.TestCase):
    def test_payloads_are_versioned_and_json_safe(self) -> None:
        import json

        reference = lr.build_lap_trace(
            *synth_lap(constant(100.0))[:2], range(401), bins=100, lap_number=1
        )
        comparison = lr.build_lap_trace(
            *synth_lap(constant(101.0))[:2], range(401), bins=100, lap_number=2
        )
        delta = lr.compare_laps(reference, comparison)
        segments = lr.segment_deltas(reference, comparison, lr.uniform_segments(3))
        best = lr.theoretical_best([reference, comparison], lr.uniform_segments(3))
        for payload in (
            reference.to_payload(),
            delta.to_payload(),
            best.to_payload(),
            *[item.to_payload() for item in segments],
        ):
            json.dumps(payload)
        self.assertEqual(
            reference.to_payload()["contract_version"], lr.LAP_REFERENCE_VERSION
        )
        self.assertEqual(
            delta.to_payload()["delta_definition"],
            "comparison lap minus reference lap; positive is slower",
        )


if __name__ == "__main__":
    unittest.main()
