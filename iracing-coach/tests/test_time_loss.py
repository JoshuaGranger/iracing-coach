"""Ranking cases for recoverable time.

`TIME-LOSS-RANK-001`.

The ranking is the product claim here, so the tests assert ordering and the
exact recoverable quantity, not merely that a number appeared. The adversarial
cases cover the three ways a ranking can lie: attributing a corner that spans
the start/finish line, ranking a segment on too few laps, and letting one
exceptional lap present itself as repeatable pace.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lap_reference as lr  # noqa: E402
import time_loss  # noqa: E402


def lap_from_segment_times(
    segment_times, *, lap_number: int, bins: int = 100, samples: int = 4000
):
    """Build a lap that spends an exact time in each equal-width segment."""

    count = len(segment_times)
    boundaries = [index / count for index in range(count + 1)]
    cumulative = [0.0]
    for value in segment_times:
        cumulative.append(cumulative[-1] + value)

    def time_at(pct: float) -> float:
        for index in range(count):
            if pct <= boundaries[index + 1] or index == count - 1:
                span = boundaries[index + 1] - boundaries[index]
                fraction = (pct - boundaries[index]) / span
                fraction = max(0.0, min(1.0, fraction))
                return cumulative[index] + segment_times[index] * fraction
        return cumulative[-1]

    distance = [index / samples for index in range(samples + 1)]
    times = [time_at(pct) for pct in distance]
    speeds = [50.0] * (samples + 1)
    return lr.build_lap_trace(
        distance,
        times,
        range(samples + 1),
        speed_mps=speeds,
        lap_number=lap_number,
        bins=bins,
    )


class RankingTests(unittest.TestCase):
    def test_ranks_by_recoverable_time_not_by_segment_order(self) -> None:
        # Four segments. The driver is inconsistent in S3 by 1.0s and in S1 by
        # 0.4s; S2 and S4 are metronomic.
        laps = [
            lap_from_segment_times([25.0, 25.0, 25.0, 25.0], lap_number=1),
            lap_from_segment_times([25.4, 25.0, 26.0, 25.0], lap_number=2),
            lap_from_segment_times([25.4, 25.0, 26.0, 25.0], lap_number=3),
            lap_from_segment_times([25.4, 25.0, 26.0, 25.0], lap_number=4),
        ]
        report = time_loss.analyze_time_loss(laps, lr.uniform_segments(4))
        self.assertEqual(report.status, time_loss.STATUS_USABLE)
        self.assertEqual(report.ranked_names[0], "S3")
        self.assertEqual(report.ranked_names[1], "S1")
        top = report.top(1)[0]
        self.assertAlmostEqual(top.recoverable_s, 1.0, places=3)
        self.assertAlmostEqual(top.best_s, 25.0, places=3)
        self.assertAlmostEqual(top.median_s, 26.0, places=3)
        self.assertEqual(top.best_lap, 1)

    def test_total_recoverable_is_the_sum_of_segment_recoverables(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0, 25.0, 25.0], lap_number=1),
            lap_from_segment_times([25.5, 25.0, 26.0, 25.0], lap_number=2),
            lap_from_segment_times([25.5, 25.0, 26.0, 25.0], lap_number=3),
        ]
        report = time_loss.analyze_time_loss(laps, lr.uniform_segments(4))
        self.assertAlmostEqual(report.total_recoverable_s, 1.5, places=3)

    def test_a_metronomic_driver_has_nothing_to_recover(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0, 25.0, 25.0], lap_number=number)
            for number in range(1, 5)
        ]
        report = time_loss.analyze_time_loss(laps, lr.uniform_segments(4))
        self.assertAlmostEqual(report.total_recoverable_s, 0.0, places=6)
        for segment in report.segments:
            self.assertAlmostEqual(segment.recoverable_s, 0.0, places=6)

    def test_one_exceptional_lap_is_visible_as_such(self) -> None:
        """A single outstanding lap must not read as repeatable pace."""

        laps = [lap_from_segment_times([25.0, 20.0], lap_number=1)]
        laps += [
            lap_from_segment_times([25.0, 24.0], lap_number=number)
            for number in range(2, 8)
        ]
        report = time_loss.analyze_time_loss(laps, lr.uniform_segments(2))
        by_name = {item.name: item for item in report.segments}
        outlier = by_name["S2"]
        self.assertAlmostEqual(outlier.recoverable_s, 4.0, places=3)
        self.assertEqual(
            outlier.near_best_lap_count,
            1,
            "only the exceptional lap itself is near the best time",
        )
        self.assertEqual(outlier.lap_count, 7)


class ExclusionTests(unittest.TestCase):
    def test_segment_wrapping_start_finish_is_refused_with_a_reason(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0], lap_number=number)
            for number in range(1, 5)
        ]
        segments = [
            {"segment": 1, "start_pct": 0.90, "end_pct": 0.10, "wraps_start_finish": True},
            {"segment": 2, "start_pct": 0.20, "end_pct": 0.40},
        ]
        report = time_loss.analyze_time_loss(laps, segments)
        by_name = {item.name: item for item in report.segments}
        self.assertEqual(by_name["Corner 1"].status, time_loss.STATUS_UNAVAILABLE)
        self.assertEqual(by_name["Corner 1"].reason, time_loss.WRAP_REASON)
        self.assertIsNone(by_name["Corner 1"].recoverable_s)
        self.assertEqual(by_name["Corner 2"].status, time_loss.STATUS_USABLE)
        self.assertEqual(report.status, time_loss.STATUS_LIMITED)
        self.assertEqual(report.excluded_segment_count, 1)

    def test_too_few_laps_is_not_ranked(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0], lap_number=1),
            lap_from_segment_times([26.0, 25.0], lap_number=2),
        ]
        report = time_loss.analyze_time_loss(laps, lr.uniform_segments(2))
        self.assertEqual(report.status, time_loss.STATUS_UNAVAILABLE)
        self.assertIsNone(report.total_recoverable_s)
        for segment in report.segments:
            self.assertEqual(segment.reason, "insufficient_covering_laps")
            self.assertEqual(segment.lap_count, 2)

    def test_partial_laps_contribute_only_where_they_cover(self) -> None:
        full = [
            lap_from_segment_times([25.0, 25.0], lap_number=number)
            for number in range(1, 4)
        ]
        distance = [0.50 + 0.50 * index / 2000 for index in range(2001)]
        times = [25.0 + 25.0 * (pct - 0.50) / 0.50 for pct in distance]
        partial = lr.build_lap_trace(
            distance, times, range(2001), lap_number=9, bins=100
        )
        report = time_loss.analyze_time_loss(full + [partial], lr.uniform_segments(2))
        by_name = {item.name: item for item in report.segments}
        self.assertEqual(by_name["S1"].lap_count, 3)
        self.assertEqual(by_name["S2"].lap_count, 4)

    def test_no_usable_laps_yields_unavailable(self) -> None:
        report = time_loss.analyze_time_loss([], lr.uniform_segments(3))
        self.assertEqual(report.status, time_loss.STATUS_UNAVAILABLE)
        self.assertEqual(report.ranked_names, ())
        self.assertIsNone(report.total_recoverable_s)

    def test_malformed_bounds_are_reported_not_raised(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0], lap_number=number)
            for number in range(1, 5)
        ]
        report = time_loss.analyze_time_loss(
            laps, [{"segment": 1, "start_pct": None, "end_pct": 0.5}]
        )
        self.assertEqual(report.segments[0].reason, "segment_bounds_missing")


class PayloadTests(unittest.TestCase):
    def test_payload_is_json_safe_and_states_its_classification(self) -> None:
        laps = [
            lap_from_segment_times([25.0, 25.0], lap_number=1),
            lap_from_segment_times([25.5, 25.0], lap_number=2),
            lap_from_segment_times([25.5, 25.0], lap_number=3),
        ]
        payload = time_loss.analyze_time_loss(laps, lr.uniform_segments(2)).to_payload()
        json.dumps(payload)
        self.assertEqual(payload["contract_version"], time_loss.TIME_LOSS_VERSION)
        self.assertIn("not an achievable single-lap time", payload["classification"])
        self.assertEqual(payload["minimum_laps_for_ranking"], 3)


if __name__ == "__main__":
    unittest.main()
