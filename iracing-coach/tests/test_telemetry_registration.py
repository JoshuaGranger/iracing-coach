"""Synthetic coordinate frames and routes only; no private track or race data."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "parsers"))
from coach_parsers import native_snapshot
from coach_parsers import telemetry_registration as tr


class GeographicFrameTests(unittest.TestCase):
    def test_wgs84_keeps_distinct_equatorial_axis_scales(self):
        east, north = tr.gps_to_local_wgs84_en(
            1, 1, origin_lat_deg=0, origin_lon_deg=0)
        self.assertAlmostEqual(east, 111319.49079327357, places=6)
        self.assertAlmostEqual(north, 110574.27582159435, places=6)
        self.assertGreater(east, north)
        self.assertEqual(tr.gps_to_local_wgs84_en(
            45, 0, origin_lat_deg=45, origin_lon_deg=0), (0, 0))
        wrapped = tr.gps_to_local_wgs84_en(
            0, -179.999, origin_lat_deg=0, origin_lon_deg=179.999)
        self.assertAlmostEqual(wrapped[0], 222.638981587, places=5)
        for bad in (float('nan'), 91):
            with self.assertRaises(ValueError):
                tr.gps_to_local_wgs84_en(bad, 0, origin_lat_deg=0,
                                        origin_lon_deg=0)

    def test_explicit_equirectangular_origin_radius_and_axis_scale(self):
        east, north = tr.gps_to_local_en(61, 11, origin_lat_deg=60,
                                        origin_lon_deg=10, radius_m=180 / math.pi)
        self.assertAlmostEqual(east, 0.5)
        self.assertAlmostEqual(north, 1.0)
        self.assertEqual(tr.gps_to_local_en(60, 10, origin_lat_deg=60,
                                           origin_lon_deg=10, radius_m=123), (0, 0))

    def test_longitude_wrap_is_local_across_antimeridian(self):
        east, north = tr.gps_to_local_en(0, -179.9, origin_lat_deg=0,
                                        origin_lon_deg=179.9, radius_m=180 / math.pi)
        self.assertAlmostEqual(east, 0.2)
        self.assertEqual(north, 0)

    def test_inverse_matches_existing_native_rotation(self):
        for theta in (0, math.pi / 2, -0.7, 3.1235):
            for point in ((1, 0), (0, 1), (-87.2, 450.3)):
                en = native_snapshot.world_to_en(*point, theta)
                native = tr.en_to_world(*en, theta)
                for actual, expected in zip(native, point):
                    self.assertAlmostEqual(actual, expected, places=11)
        self.assertEqual(tr.en_to_world(-7, 3, 0), (3, 7))

    def test_translation_is_applied_in_en_frame_and_preserves_distances(self):
        frame = tr.RigidFrame(0, 100, -20)
        self.assertEqual(frame.to_en(3, 7), (93, -17))
        self.assertEqual(frame.from_en(93, -17), (3, 7))
        other = tr.RigidFrame(0.47, -18, 31)
        a, b = other.to_en(2, 3), other.to_en(5, 7)
        self.assertAlmostEqual(math.dist(a, b), 5)
        for point in ((-8, 123), (0, 0), (66, -51)):
            result = other.from_en(*other.to_en(*point))
            for actual, expected in zip(result, point):
                self.assertAlmostEqual(actual, expected)

    def test_invalid_geographic_and_frame_inputs_fail(self):
        kwargs = dict(origin_lat_deg=0, origin_lon_deg=0, radius_m=1)
        for changed in ({"origin_lat_deg": 90}, {"origin_lon_deg": 181},
                        {"radius_m": 0}, {"radius_m": math.inf}):
            with self.assertRaises(ValueError):
                tr.gps_to_local_en(0, 0, **(kwargs | changed))
        for point in ((91, 0), (0, -181), (math.nan, 0)):
            with self.assertRaises(ValueError):
                tr.gps_to_local_en(*point, **kwargs)
        with self.assertRaises(ValueError):
            tr.RigidFrame(math.nan)
        with self.assertRaises(ValueError):
            tr.en_to_world(0, math.inf, 0)


class CyclicProjectionTests(unittest.TestCase):
    def setUp(self):
        self.square = tr.CyclicPolyline([(0, 0), (10, 0), (10, 10), (0, 10)])

    def test_station_sign_and_distance_on_known_segment(self):
        inside = self.square.project(4, 2)
        self.assertEqual(inside.station_m, 4)
        self.assertEqual(inside.length_m, 40)
        self.assertEqual(inside.signed_left_m, 2)
        self.assertEqual(inside.distance_m, 2)
        self.assertEqual(inside.point_xy, (4, 0))
        self.assertEqual(inside.segment_index, 0)
        self.assertAlmostEqual(inside.segment_fraction, 0.4)
        self.assertFalse(inside.ambiguous)
        self.assertEqual(self.square.project(4, -2).signed_left_m, -2)

    def test_closed_seam_and_repeated_endpoint(self):
        closed = tr.CyclicPolyline([*self.square.points, self.square.points[0]])
        self.assertEqual(closed.project(0, 0).station_m, 0)
        self.assertEqual(closed.length_m, self.square.length_m)
        result = closed.project(-2, 3)
        self.assertEqual(result.station_m, 37)
        self.assertEqual(result.signed_left_m, -2)
        self.assertFalse(result.ambiguous)

    def test_reversing_direction_changes_station_and_lateral_sign(self):
        reverse = tr.CyclicPolyline([(0, 0), (0, 10), (10, 10), (10, 0)])
        result = reverse.project(4, 2)
        self.assertEqual(result.station_m, 36)
        self.assertEqual(result.signed_left_m, -2)
        self.assertEqual(result.distance_m, 2)

    def test_ambiguous_nearest_branches_and_endpoint_components(self):
        middle = self.square.project(5, 5)
        self.assertTrue(middle.ambiguous)
        self.assertEqual(middle.segment_index, 0)
        corner = self.square.project(-3, -4)
        self.assertTrue(corner.ambiguous)
        self.assertEqual(corner.distance_m, 5)
        self.assertEqual(corner.signed_left_m, -4)
        bowtie = tr.CyclicPolyline([(0, 0), (10, 10), (0, 10), (10, 0)])
        self.assertTrue(bowtie.project(5, 5).ambiguous)

    def test_invalid_polylines_or_queries_fail(self):
        for points in ([], [(0, 0), (1, 1)],
                       [(0, 0), (1, 1), (1, 1)],
                       [(0, 0), (1, 1), (math.inf, 0)],
                       [(0, 0, 1), (1, 1), (2, 2)]):
            with self.assertRaises(ValueError):
                tr.CyclicPolyline(points)
        with self.assertRaises(ValueError):
            self.square.project(math.nan, 0)
        with self.assertRaises(ValueError):
            self.square.project(0, 0, tie_distance_m=-1)


class CircularPhaseTests(unittest.TestCase):
    def test_phase_wrap_and_known_shift(self):
        self.assertAlmostEqual(tr.circular_difference(0.01, 0.99), 0.02)
        self.assertAlmostEqual(tr.circular_difference(0.99, 0.01), -0.02)
        self.assertEqual(tr.circular_difference(0.5), -0.5)
        self.assertEqual(tr.circular_difference(100.25, 99.25), 0)
        summary = tr.circular_phase_statistics([0.99, 0.0, 0.01])
        self.assertAlmostEqual(summary.mean_fraction, 0)
        self.assertEqual(summary.count, 3)
        self.assertGreater(summary.concentration, 0.99)
        self.assertAlmostEqual(summary.median_absolute_residual_fraction, 0.01)
        self.assertAlmostEqual(summary.p95_absolute_residual_fraction, 0.01)

    def test_antipodal_or_uniform_observations_do_not_invent_mean(self):
        for values in ([0, 0.5], [0, 0.25, 0.5, 0.75]):
            summary = tr.circular_phase_statistics(values)
            self.assertIsNone(summary.mean_fraction)
            self.assertIsNone(summary.p95_absolute_residual_fraction)
        single = tr.circular_phase_statistics([0.2])
        self.assertAlmostEqual(single.mean_fraction, 0.2)
        self.assertEqual(single.maximum_absolute_residual_fraction, 0)

    def test_empty_or_nonfinite_phase_fails(self):
        for values in ([], [math.nan], [math.inf]):
            with self.assertRaises(ValueError):
                tr.circular_phase_statistics(values)


if __name__ == "__main__":
    unittest.main()
