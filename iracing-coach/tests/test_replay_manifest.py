"""The replay contract closure: discriminate strictly, page honestly.

`REPLAY-MANIFEST-COMPAT-001`, `REPLAY-CONTRACT-001`, `PERF-REPLAY-PAYLOAD-001`.

The accepted closure names 10m/1h/4h at 1, 24 and 64 cars; malformed, future
and old inline manifests; cursor invalidation; and gaps, events, offline reopen
and cancel. The duration/car matrix is exercised against the row budget here
because that is the producer-side half - the exact host budget is a measurement
Codex owns, so these tests check that *whatever* budget arrives is respected,
never that a particular number is fast enough.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import replay_manifest as rm  # noqa: E402

#: The closure's duration matrix at the 60 Hz source cadence.
DURATIONS = {"10m": 10 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60}
CAR_COUNTS = (1, 24, 64)
DISPLAY_HZ = 60


def manifest(
    *,
    seconds=600,
    cars=24,
    status=rm.STATUS_COMPLETE,
    revision="rev-1",
    gaps=(),
    schema_version=rm.SUPPORTED_SCHEMA_VERSION,
    fmt=rm.FORMAT_WINDOWED,
):
    return rm.ReplayManifest(
        schema_version=schema_version,
        format=fmt,
        status=status,
        revision=revision,
        frame_count=seconds * DISPLAY_HZ,
        car_count=cars,
        cadence_hz=DISPLAY_HZ,
        gaps=tuple(gaps),
    )


class DiscriminationTests(unittest.TestCase):
    """Strict discrimination is not contingent on any measurement."""

    def test_a_future_schema_is_refused_rather_than_read_optimistically(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(schema_version=rm.SUPPORTED_SCHEMA_VERSION + 1)

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(fmt="streaming")

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(status="probably_fine")

    def test_a_manifest_must_carry_a_revision(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(revision="")

    def test_a_non_object_payload_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            rm.read_manifest([1, 2, 3])

    def test_a_payload_without_a_schema_version_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            rm.read_manifest({"format": rm.FORMAT_INLINE, "status": rm.STATUS_COMPLETE})

    def test_an_implausible_cadence_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            rm.ReplayManifest(
                schema_version=2,
                format=rm.FORMAT_INLINE,
                status=rm.STATUS_COMPLETE,
                revision="r",
                frame_count=10,
                car_count=1,
                cadence_hz=0,
            )


class OldInlineCompatibilityTests(unittest.TestCase):
    """The compatibility half: the previous delivery keeps working."""

    def test_a_pre_contract_manifest_reads_as_complete_inline(self):
        read = rm.read_manifest(
            {"schemaVersion": 2, "frame_count": 600, "car_count": 24}
        )
        self.assertEqual(read.format, rm.FORMAT_INLINE)
        self.assertEqual(read.status, rm.STATUS_COMPLETE)

    def test_a_pre_contract_manifest_keeps_a_stable_revision(self):
        read = rm.read_manifest({"schemaVersion": 2, "frame_count": 1, "car_count": 1})
        self.assertEqual(read.revision, "legacy-inline")

    def test_a_half_present_discriminator_is_malformed_not_legacy(self):
        # Guessing the missing half is how a windowed recording gets read as an
        # inline one.
        with self.assertRaises(rm.ReplayManifestError):
            rm.read_manifest({"schemaVersion": 2, "format": rm.FORMAT_WINDOWED})
        with self.assertRaises(rm.ReplayManifestError):
            rm.read_manifest({"schemaVersion": 2, "status": rm.STATUS_COMPLETE})

    def test_a_fully_declared_manifest_round_trips(self):
        read = rm.read_manifest(manifest().to_payload())
        self.assertEqual(read.format, rm.FORMAT_WINDOWED)
        self.assertEqual(read.revision, "rev-1")


class FalseCompletenessTests(unittest.TestCase):
    """A capture that lost frames must not be advertised as whole."""

    def test_a_complete_recording_cannot_declare_gaps(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(status=rm.STATUS_COMPLETE, gaps=(rm.FrameGap(10, 20),))

    def test_an_incomplete_recording_may_declare_gaps(self):
        built = manifest(status=rm.STATUS_INCOMPLETE, gaps=(rm.FrameGap(10, 20),))
        self.assertEqual(built.missing_frames, 11)

    def test_a_failed_recording_cannot_claim_frames(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(status=rm.STATUS_FAILED)

    def test_a_failed_recording_is_not_readable(self):
        built = rm.ReplayManifest(
            schema_version=2,
            format=rm.FORMAT_WINDOWED,
            status=rm.STATUS_FAILED,
            revision="r",
            frame_count=0,
            car_count=24,
            cadence_hz=60,
        )
        self.assertFalse(built.is_readable)

    def test_overlapping_gaps_are_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(
                status=rm.STATUS_INCOMPLETE,
                gaps=(rm.FrameGap(10, 20), rm.FrameGap(15, 25)),
            )

    def test_a_gap_outside_the_frame_range_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            manifest(seconds=1, status=rm.STATUS_INCOMPLETE, gaps=(rm.FrameGap(0, 999),))

    def test_a_gap_cannot_end_before_it_starts(self):
        with self.assertRaises(rm.ReplayManifestError):
            rm.FrameGap(20, 10)


class CursorInvalidationTests(unittest.TestCase):
    """A cursor belongs to a revision, not merely to an offset."""

    def test_a_cursor_from_the_same_revision_is_valid(self):
        built = manifest()
        self.assertTrue(rm.cursor_is_valid(built, rm.WindowCursor("rev-1", 100)))

    def test_a_re_encoded_replay_invalidates_the_cursor(self):
        # Same offset, different recording. Serving this would return frames
        # from another encode at the same index.
        self.assertFalse(
            rm.cursor_is_valid(manifest(revision="rev-2"), rm.WindowCursor("rev-1", 100))
        )

    def test_a_cursor_past_the_end_is_invalid(self):
        built = manifest(seconds=10)
        beyond = rm.WindowCursor("rev-1", built.frame_count + 1)
        self.assertFalse(rm.cursor_is_valid(built, beyond))

    def test_planning_from_a_stale_cursor_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            list(
                rm.plan_windows(
                    manifest(revision="rev-2"),
                    row_budget=100_000,
                    cursor=rm.WindowCursor("rev-1", 0),
                )
            )

    def test_a_cursor_must_name_its_revision(self):
        with self.assertRaises(rm.ReplayManifestError):
            rm.WindowCursor("", 0)

    def test_a_window_hands_back_the_cursor_that_follows_it(self):
        built = manifest(seconds=10, cars=1)
        first = next(rm.plan_windows(built, row_budget=100))
        self.assertEqual(first.cursor.next_frame, first.end_frame + 1)
        self.assertTrue(rm.cursor_is_valid(built, first.cursor))


class PayloadBudgetTests(unittest.TestCase):
    """The 10m/1h/4h by 1/24/64 matrix, against the budget rather than a clock."""

    def test_every_window_respects_the_row_budget(self):
        budget = 50_000
        for label, seconds in DURATIONS.items():
            for cars in CAR_COUNTS:
                with self.subTest(duration=label, cars=cars):
                    built = manifest(seconds=seconds, cars=cars)
                    for window in rm.plan_windows(built, row_budget=budget):
                        self.assertLessEqual(window.rows, budget)

    def test_the_windows_cover_every_frame_exactly_once(self):
        for label, seconds in DURATIONS.items():
            for cars in CAR_COUNTS:
                with self.subTest(duration=label, cars=cars):
                    built = manifest(seconds=seconds, cars=cars)
                    covered = 0
                    expected_start = 0
                    for window in rm.plan_windows(built, row_budget=50_000):
                        self.assertEqual(window.start_frame, expected_start)
                        covered += window.frame_count
                        expected_start = window.end_frame + 1
                    self.assertEqual(covered, built.frame_count)

    def test_the_one_hour_sixty_four_car_case_is_the_documented_size(self):
        # 512,768 rows is the number the finding was raised against; it is the
        # display projection rather than the 216,000 source frames.
        built = manifest(seconds=3600, cars=64)
        self.assertEqual(built.total_rows, 3600 * DISPLAY_HZ * 64)

    def test_a_bigger_budget_produces_fewer_windows(self):
        built = manifest(seconds=3600, cars=64)
        small = len(list(rm.plan_windows(built, row_budget=10_000)))
        large = len(list(rm.plan_windows(built, row_budget=100_000)))
        self.assertLess(large, small)

    def test_a_budget_too_small_for_one_frame_is_refused_not_stalled(self):
        with self.assertRaises(rm.ReplayManifestError):
            list(rm.plan_windows(manifest(cars=64), row_budget=32))

    def test_a_zero_budget_is_refused(self):
        with self.assertRaises(rm.ReplayManifestError):
            list(rm.plan_windows(manifest(), row_budget=0))

    def test_resuming_from_a_cursor_continues_rather_than_restarts(self):
        built = manifest(seconds=60, cars=24)
        windows = list(rm.plan_windows(built, row_budget=24_000))
        resumed = list(
            rm.plan_windows(built, row_budget=24_000, cursor=windows[0].cursor)
        )
        self.assertEqual(resumed[0].start_frame, windows[1].start_frame)

    def test_an_unreadable_replay_plans_no_windows(self):
        empty = rm.ReplayManifest(
            schema_version=2,
            format=rm.FORMAT_WINDOWED,
            status=rm.STATUS_FAILED,
            revision="r",
            frame_count=0,
            car_count=24,
            cadence_hz=60,
        )
        self.assertEqual(list(rm.plan_windows(empty, row_budget=1000)), [])


class GapReportingTests(unittest.TestCase):
    """A short window and a dropped range must be distinguishable."""

    def test_a_window_over_a_gap_says_so(self):
        built = manifest(seconds=10, cars=1, status=rm.STATUS_INCOMPLETE, gaps=(rm.FrameGap(50, 60),))
        windows = list(rm.plan_windows(built, row_budget=100))
        self.assertTrue(any(window.contains_gap for window in windows))

    def test_a_window_clear_of_the_gap_does_not_claim_one(self):
        built = manifest(seconds=10, cars=1, status=rm.STATUS_INCOMPLETE, gaps=(rm.FrameGap(500, 510),))
        first = next(rm.plan_windows(built, row_budget=100))
        self.assertFalse(first.contains_gap)

    def test_the_manifest_totals_the_missing_frames(self):
        built = manifest(
            status=rm.STATUS_INCOMPLETE,
            gaps=(rm.FrameGap(10, 19), rm.FrameGap(100, 104)),
        )
        self.assertEqual(built.missing_frames, 15)

    def test_the_payload_carries_the_gaps_for_the_consumer(self):
        built = manifest(status=rm.STATUS_INCOMPLETE, gaps=(rm.FrameGap(10, 19),))
        self.assertEqual(len(built.to_payload()["gaps"]), 1)


if __name__ == "__main__":
    unittest.main()
