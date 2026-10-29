"""Explicit geometry helpers for offline telemetry/route comparisons.

No transform is fitted or qualified here. Callers supply the geographic origin,
linear scale and native yaw, and retain their evidence separately. Polyline
stations are sampled chord lengths, not recovered native checkpoint stations or
SDK LapDistPct. Distances to authored routes include the driver's lane choice.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Iterable

from .native_snapshot import world_to_en


def _finite(*values: float) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("coordinates and parameters must be finite")
    return result


def gps_to_local_en(latitude_deg: float, longitude_deg: float, *,
                    origin_lat_deg: float, origin_lon_deg: float,
                    radius_m: float) -> tuple[float, float]:
    """Local equirectangular approximation with an explicit caller-owned origin.

    Longitude difference takes the shorter antimeridian branch. This is not a
    recovered native GPS conversion or a claim of geodetic accuracy. In
    particular, WeekendInfo's origin must not be substituted for the native
    world's origin without separate evidence. Polar origins are unsupported.
    """
    lat, lon, lat0, lon0, radius = _finite(
        latitude_deg, longitude_deg, origin_lat_deg, origin_lon_deg, radius_m)
    if not -90 <= lat <= 90 or not -90 < lat0 < 90:
        raise ValueError("invalid latitude or polar origin")
    if not -180 <= lon <= 180 or not -180 <= lon0 <= 180:
        raise ValueError("longitude outside [-180, 180]")
    if radius <= 0:
        raise ValueError("radius must be positive")
    delta_lon = (lon - lon0 + 180.0) % 360.0 - 180.0
    return _finite(math.radians(delta_lon) * radius * math.cos(math.radians(lat0)),
                   math.radians(lat - lat0) * radius)


def en_to_world(east_m: float, north_m: float,
                theta_rad: float) -> tuple[float, float]:
    """Inverse of native_snapshot.world_to_en, without a fitted translation."""
    east, north, theta = _finite(east_m, north_m, theta_rad)
    sine, cosine = math.sin(theta), math.cos(theta)
    return _finite(sine * east + cosine * north,
                   -cosine * east + sine * north)


def gps_to_local_wgs84_en(latitude_deg: float, longitude_deg: float, *,
                          origin_lat_deg: float,
                          origin_lon_deg: float) -> tuple[float, float]:
    """Local linear WGS84 approximation at a caller-supplied geographic origin.

    Uses separate prime-vertical and meridional radii, at zero ellipsoid height.
    This is not an ECEF transform, an exact geodesic, or native-converter parity.
    WGS84 defining constants: a=6378137 m, inverse flattening=298.257223563
    (NGA WGS84 standard). The selected origin and fitted translation still need
    independent evidence; a good station fit does not calibrate lateral limits.
    """
    east_unit, north_unit = gps_to_local_en(
        latitude_deg, longitude_deg, origin_lat_deg=origin_lat_deg,
        origin_lon_deg=origin_lon_deg, radius_m=1.0)
    flattening = 1.0 / 298.257223563
    eccentricity_squared = flattening * (2.0 - flattening)
    w = math.sqrt(1.0 - eccentricity_squared *
                  math.sin(math.radians(float(origin_lat_deg))) ** 2)
    prime_vertical = 6378137.0 / w
    meridional = 6378137.0 * (1.0 - eccentricity_squared) / w ** 3
    return _finite(east_unit * prime_vertical, north_unit * meridional)


@dataclass(frozen=True)
class RigidFrame:
    """Caller-specified native XY to local east/north rotation and translation.

    Scale is fixed at one; translation is relative to the caller's chosen
    local geographic origin. Creating this record establishes no calibration.
    """

    theta_rad: float
    east_offset_m: float = 0.0
    north_offset_m: float = 0.0

    def __post_init__(self):
        for name, value in zip(("theta_rad", "east_offset_m", "north_offset_m"),
                               _finite(self.theta_rad, self.east_offset_m,
                                       self.north_offset_m)):
            object.__setattr__(self, name, value)

    def to_en(self, x_m: float, y_m: float) -> tuple[float, float]:
        east, north = world_to_en(x_m, y_m, self.theta_rad)
        return _finite(east + self.east_offset_m, north + self.north_offset_m)

    def from_en(self, east_m: float, north_m: float) -> tuple[float, float]:
        east, north = _finite(east_m, north_m)
        return en_to_world(east - self.east_offset_m,
                           north - self.north_offset_m, self.theta_rad)


@dataclass(frozen=True)
class Projection:
    station_m: float
    length_m: float
    signed_left_m: float
    distance_m: float
    point_xy: tuple[float, float]
    segment_index: int
    segment_fraction: float
    ambiguous: bool


class CyclicPolyline:
    """Nearest-segment diagnostics for a closed, oriented polyline in metres.

    One repeated closing point is accepted. Consecutive duplicates fail rather
    than silently changing segment membership. Self intersections are allowed;
    equal nearest segments are reported as ambiguous, including corner ties.
    This projection does not reproduce native checkpoint/normal interpolation.
    """

    def __init__(self, points: Iterable[tuple[float, float]]):
        vertices = []
        for point in points:
            if len(vertices) >= 1_000_001:
                raise ValueError("polyline point budget exceeded")
            if len(point) != 2:
                raise ValueError("polyline points must have exactly two coordinates")
            vertices.append(_finite(*point))
        if vertices and vertices[0] == vertices[-1]:
            vertices.pop()
        if not 3 <= len(vertices) <= 1_000_000:
            raise ValueError("a cyclic polyline requires at least three points")
        segments = []
        station = 0.0
        for index, (x, y) in enumerate(vertices):
            end_x, end_y = vertices[(index + 1) % len(vertices)]
            dx, dy = _finite(end_x - x, end_y - y)
            length, = _finite(math.hypot(dx, dy))
            if length == 0:
                raise ValueError("consecutive polyline points must be distinct")
            segments.append((x, y, dx / length, dy / length, length, station))
            station, = _finite(station + length)
        self.points = tuple(vertices)
        self.length_m = station
        self._segments = tuple(segments)

    def project(self, x_m: float, y_m: float, *,
                tie_distance_m: float = 1e-9) -> Projection:
        """Return the nearest chord; multiple near-equal minima flag ambiguity.

        Signed lateral offset is positive left of the selected chord. At a
        clamped endpoint it need not have the magnitude of Euclidean distance.
        The lowest segment index breaks exact-distance ties deterministically.
        """
        x, y, tolerance = _finite(x_m, y_m, tie_distance_m)
        if tolerance < 0:
            raise ValueError("tie distance must be nonnegative")
        best = None
        second_distance = math.inf
        for index, (ax, ay, ux, uy, length, start) in enumerate(self._segments):
            rx, ry = _finite(x - ax, y - ay)
            along, = _finite(rx * ux + ry * uy)
            along = max(0.0, min(length, along))
            px, py = _finite(ax + along * ux, ay + along * uy)
            ex, ey = _finite(x - px, y - py)
            distance, lateral = _finite(math.hypot(ex, ey), ux * ey - uy * ex)
            station = (start + along) % self.length_m
            candidate = (distance, index, station, lateral, px, py, along / length)
            if best is None or candidate < best:
                if best is not None:
                    second_distance = min(second_distance, best[0])
                best = candidate
            else:
                second_distance = min(second_distance, distance)
        distance, index, station, lateral, px, py, fraction = best
        ambiguous = second_distance - distance <= tolerance
        return Projection(station, self.length_m, lateral, distance,
                          (px, py), index, fraction, ambiguous)


def circular_difference(a: float, b: float = 0.0) -> float:
    """Signed a-minus-b difference in turns in [-0.5, 0.5)."""
    a, b = _finite(a, b)
    return ((a % 1.0 - b % 1.0) + 0.5) % 1.0 - 0.5


@dataclass(frozen=True)
class CircularPhaseStats:
    count: int
    mean_fraction: float | None
    concentration: float
    median_absolute_residual_fraction: float | None
    p95_absolute_residual_fraction: float | None
    maximum_absolute_residual_fraction: float | None


def circular_phase_statistics(offsets: Iterable[float]) -> CircularPhaseStats:
    """Circular summary of phase offsets in turns, without inferring validity.

    Residuals are measured around the circular mean. A near-zero resultant
    (<= 1e-12) has no reported mean/residuals. High concentration alone does not
    prove an SDK/native phase relationship; samples may share laps or files.
    """
    values = [circular_difference(value) for value in offsets]
    if not values:
        raise ValueError("phase statistics require at least one observation")
    sine = math.fsum(math.sin(math.tau * value) for value in values) / len(values)
    cosine = math.fsum(math.cos(math.tau * value) for value in values) / len(values)
    concentration = min(1.0, math.hypot(sine, cosine))
    if concentration <= 1e-12:
        return CircularPhaseStats(len(values), None, concentration, None, None, None)
    mean = circular_difference(math.atan2(sine, cosine) / math.tau)
    residuals = sorted(abs(circular_difference(value, mean)) for value in values)
    position = 0.95 * (len(residuals) - 1)
    lo, hi = math.floor(position), math.ceil(position)
    p95 = residuals[lo] + (residuals[hi] - residuals[lo]) * (position - lo)
    return CircularPhaseStats(len(values), mean, concentration,
                              statistics.median(residuals), p95, residuals[-1])
