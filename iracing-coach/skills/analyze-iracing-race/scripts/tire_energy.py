"""Per-corner tire energy accounting, and whether it explains measured wear.

`TIRE-ENERGY-001`, feeding `MODEL-GATE-001`.

"How hard am I being on the tires?" is currently answered in this engine with
uncalibrated proxies - a brake-energy proxy, a steering-work proxy, wheel-speed
divergence - reported per run, with the honest caveat that none of them is
calibrated against anything. Real wear arrives only as a discrete reading after
pit service. The two have never been connected, so the proxies can never be
promoted and the wear readings can never be explained.

This module is the bridge. It accounts for dissipated energy *per corner* from
channels the recording already carries, in a form that can be compared directly
against those discrete readings.

The physics it leans on is deliberately shallow, because shallow physics that
is stated plainly beats deep physics that is assumed. A tire dissipates energy
at a rate set by the force it transmits and the speed at which its contact
patch slides. Vehicle acceleration stands in for force per unit mass, so
``|a| * v`` is a specific power in W/kg and its time integral is a specific
energy in J/kg. Vehicle mass cancels, which is what makes the number comparable
across cars without a mass table nobody has.

Where recorded velocity components allow vehicle sideslip to be derived, a
second lateral figure weights that power by ``sin|beta|``, which is the part of
the motion actually sliding across the patch rather than rolling along it. That
figure is strictly better founded, so it is reported separately and never
silently substituted: a consumer can see from ``grade`` and
``slip_coverage`` exactly which one it is holding.

None of this is a wear model and this module never claims to be one. It
produces the *independent variable* a wear model needs.
:func:`wear_energy_diagnostic` then asks the only question that matters before
a model may be built - does accumulated energy explain measured wear better
than lap count alone? - and refuses to answer it on too few observations, since
a correlation over three points is a coincidence with a decimal point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence


#: Version of the tire-energy contract.
TIRE_ENERGY_VERSION = 1

#: Sideslip is unstable below this planar speed, matching the engine's own
#: derivation guard, so those samples contribute load-only energy.
MINIMUM_SLIP_SPEED_M_S = 5.0

#: Fraction of a segment's samples that must carry usable sideslip before the
#: slip-weighted figure is graded as the primary one.
SLIP_COVERAGE_FOR_GRADE = 0.50

#: Paired wear observations required before any relationship is reported.
MINIMUM_PAIRED_OBSERVATIONS = 5

GRADE_SLIP_WEIGHTED = "slip_weighted"
GRADE_LOAD_ONLY = "load_only"

STATUS_USABLE = "usable"
STATUS_LIMITED = "limited"
STATUS_UNAVAILABLE = "unavailable"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sideslip_radians(forward: Any, lateral: Any) -> float | None:
    """Chassis-frame sideslip, guarded exactly as the engine guards it."""

    longitudinal = _finite(forward)
    sideways = _finite(lateral)
    if longitudinal is None or sideways is None:
        return None
    if math.hypot(longitudinal, sideways) < MINIMUM_SLIP_SPEED_M_S:
        return None
    if longitudinal <= 0.5:
        return None
    angle = math.atan2(sideways, longitudinal)
    return angle if math.isfinite(angle) else None


def _dt_series(times: Sequence[Any], fallback_hz: float) -> list[float]:
    fallback = 1.0 / fallback_hz if fallback_hz > 0 else 0.05
    result: list[float] = []
    for index in range(len(times)):
        current = _finite(times[index])
        previous = _finite(times[index - 1]) if index > 0 else None
        if current is None or previous is None or current <= previous:
            result.append(fallback)
            continue
        step = current - previous
        # A pause, a session jump or a dropout must not be billed as driving.
        result.append(step if step < fallback * 20.0 else fallback)
    return result


@dataclass(frozen=True)
class SegmentEnergy:
    """Dissipated specific energy across one stretch of track."""

    name: str
    start_pct: float
    end_pct: float
    status: str
    grade: str
    lateral_load_j_per_kg: float
    longitudinal_load_j_per_kg: float
    lateral_slip_j_per_kg: float | None
    total_load_j_per_kg: float
    share_of_lap: float | None
    slip_coverage: float
    sample_count: int
    peak_lateral_g: float | None

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None, digits: int = 3) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "name": self.name,
            "start_pct": round(self.start_pct, 6),
            "end_pct": round(self.end_pct, 6),
            "status": self.status,
            "grade": self.grade,
            "lateral_load_j_per_kg": rounded(self.lateral_load_j_per_kg),
            "longitudinal_load_j_per_kg": rounded(self.longitudinal_load_j_per_kg),
            "lateral_slip_j_per_kg": rounded(self.lateral_slip_j_per_kg),
            "total_load_j_per_kg": rounded(self.total_load_j_per_kg),
            "share_of_lap": rounded(self.share_of_lap, 4),
            "slip_coverage": rounded(self.slip_coverage, 4),
            "sample_count": self.sample_count,
            "peak_lateral_g": rounded(self.peak_lateral_g, 3),
        }


@dataclass(frozen=True)
class EnergyReport:
    """Per-segment energy for one lap or run, ranked by share of the total."""

    segments: tuple[SegmentEnergy, ...]
    status: str
    total_load_j_per_kg: float | None
    grade: str
    required_channels: tuple[str, ...]
    missing_channels: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": TIRE_ENERGY_VERSION,
            "status": self.status,
            "grade": self.grade,
            "total_load_j_per_kg": (
                round(self.total_load_j_per_kg, 3)
                if self.total_load_j_per_kg is not None
                else None
            ),
            "required_channels": list(self.required_channels),
            "missing_channels": list(self.missing_channels),
            "segments": [segment.to_payload() for segment in self.segments],
            "classification": (
                "specific energy proxy from vehicle acceleration and speed; "
                "vehicle mass cancels. Not a wear measurement and not a wear model."
            ),
            "units": "J/kg",
        }


REQUIRED_CHANNELS = ("Speed", "LatAccel", "LongAccel", "LapDistPct", "SessionTime")


def segment_energy(
    *,
    lap_dist_pct: Sequence[Any],
    session_time_s: Sequence[Any],
    speed_m_s: Sequence[Any],
    lat_accel_m_s2: Sequence[Any],
    long_accel_m_s2: Sequence[Any],
    segments: Sequence[Mapping[str, Any]],
    indices: Sequence[int] | None = None,
    velocity_x_m_s: Sequence[Any] | None = None,
    velocity_y_m_s: Sequence[Any] | None = None,
    sample_rate_hz: float = 60.0,
    missing_channels: Sequence[str] = (),
) -> EnergyReport:
    """Accumulate dissipated specific energy into each named segment.

    Energy is summed per sample rather than resampled onto a grid: an integral
    over the samples that were actually recorded cannot invent load for
    distance that was not, which resampling could.
    """

    if missing_channels:
        return EnergyReport(
            segments=(),
            status=STATUS_UNAVAILABLE,
            total_load_j_per_kg=None,
            grade=GRADE_LOAD_ONLY,
            required_channels=REQUIRED_CHANNELS,
            missing_channels=tuple(missing_channels),
        )

    span = indices if indices is not None else range(len(lap_dist_pct))
    dts = _dt_series(session_time_s, sample_rate_hz)

    buckets: list[dict[str, Any]] = []
    for ordinal, segment in enumerate(segments, start=1):
        start_pct = _finite(segment.get("start_pct"))
        end_pct = _finite(segment.get("end_pct"))
        name = str(
            segment.get("name")
            or (f"Corner {segment['segment']}" if segment.get("segment") else f"Segment {ordinal}")
        )
        buckets.append(
            {
                "name": name,
                "start_pct": start_pct,
                "end_pct": end_pct,
                "wraps": bool(segment.get("wraps_start_finish")),
                "lateral": 0.0,
                "longitudinal": 0.0,
                "slip": 0.0,
                "slip_samples": 0,
                "samples": 0,
                "peak_lat_g": None,
            }
        )

    for index in span:
        if index >= len(lap_dist_pct):
            continue
        position = _finite(lap_dist_pct[index])
        speed = _finite(speed_m_s[index]) if index < len(speed_m_s) else None
        lat = _finite(lat_accel_m_s2[index]) if index < len(lat_accel_m_s2) else None
        lon = _finite(long_accel_m_s2[index]) if index < len(long_accel_m_s2) else None
        if position is None or speed is None or speed <= 0.0:
            continue
        if lat is None and lon is None:
            continue
        dt = dts[index] if index < len(dts) else 1.0 / sample_rate_hz
        beta = None
        if velocity_x_m_s is not None and velocity_y_m_s is not None:
            if index < len(velocity_x_m_s) and index < len(velocity_y_m_s):
                beta = _sideslip_radians(velocity_x_m_s[index], velocity_y_m_s[index])

        for bucket in buckets:
            start_pct = bucket["start_pct"]
            end_pct = bucket["end_pct"]
            if start_pct is None or end_pct is None or bucket["wraps"]:
                continue
            if not start_pct <= position < end_pct:
                continue
            lateral_power = abs(lat) * speed if lat is not None else 0.0
            longitudinal_power = abs(lon) * speed if lon is not None else 0.0
            bucket["lateral"] += lateral_power * dt
            bucket["longitudinal"] += longitudinal_power * dt
            bucket["samples"] += 1
            if lat is not None:
                lateral_g = abs(lat) / 9.80665
                if bucket["peak_lat_g"] is None or lateral_g > bucket["peak_lat_g"]:
                    bucket["peak_lat_g"] = lateral_g
            if beta is not None:
                bucket["slip"] += lateral_power * abs(math.sin(beta)) * dt
                bucket["slip_samples"] += 1
            break

    totals = [bucket["lateral"] + bucket["longitudinal"] for bucket in buckets]
    lap_total = sum(totals)
    results: list[SegmentEnergy] = []
    slip_covered = 0
    all_samples = 0
    for bucket, total in zip(buckets, totals):
        samples = int(bucket["samples"])
        all_samples += samples
        slip_covered += int(bucket["slip_samples"])
        coverage = bucket["slip_samples"] / samples if samples else 0.0
        usable = samples > 0 and not bucket["wraps"]
        results.append(
            SegmentEnergy(
                name=str(bucket["name"]),
                start_pct=float(bucket["start_pct"] or 0.0),
                end_pct=float(bucket["end_pct"] or 0.0),
                status=STATUS_USABLE if usable else STATUS_UNAVAILABLE,
                grade=(
                    GRADE_SLIP_WEIGHTED
                    if coverage >= SLIP_COVERAGE_FOR_GRADE
                    else GRADE_LOAD_ONLY
                ),
                lateral_load_j_per_kg=float(bucket["lateral"]),
                longitudinal_load_j_per_kg=float(bucket["longitudinal"]),
                lateral_slip_j_per_kg=(
                    float(bucket["slip"]) if bucket["slip_samples"] else None
                ),
                total_load_j_per_kg=float(total),
                share_of_lap=(total / lap_total if lap_total > 0.0 else None),
                slip_coverage=coverage,
                sample_count=samples,
                peak_lateral_g=bucket["peak_lat_g"],
            )
        )

    ranked = sorted(
        results,
        key=lambda item: (item.status != STATUS_USABLE, -(item.share_of_lap or 0.0)),
    )
    usable_segments = [item for item in ranked if item.status == STATUS_USABLE]
    overall_coverage = slip_covered / all_samples if all_samples else 0.0
    if not usable_segments:
        status = STATUS_UNAVAILABLE
    elif len(usable_segments) < len(results):
        status = STATUS_LIMITED
    else:
        status = STATUS_USABLE
    return EnergyReport(
        segments=tuple(ranked),
        status=status,
        total_load_j_per_kg=lap_total if usable_segments else None,
        grade=(
            GRADE_SLIP_WEIGHTED
            if overall_coverage >= SLIP_COVERAGE_FOR_GRADE
            else GRADE_LOAD_ONLY
        ),
        required_channels=REQUIRED_CHANNELS,
        missing_channels=(),
    )


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    count = len(xs)
    if count < 2 or count != len(ys):
        return None
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x <= 0.0 or variance_y <= 0.0:
        return None
    return covariance / math.sqrt(variance_x * variance_y)


@dataclass(frozen=True)
class WearEnergyDiagnostic:
    """Does accumulated energy explain measured wear better than laps do?"""

    status: str
    observation_count: int
    energy_r: float | None
    laps_r: float | None
    energy_explains_more: bool | None
    reason: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": TIRE_ENERGY_VERSION,
            "status": self.status,
            "observation_count": self.observation_count,
            "minimum_paired_observations": MINIMUM_PAIRED_OBSERVATIONS,
            "energy_r": round(self.energy_r, 4) if self.energy_r is not None else None,
            "laps_r": round(self.laps_r, 4) if self.laps_r is not None else None,
            "energy_explains_more": self.energy_explains_more,
            "reason": self.reason,
            "classification": (
                "diagnostic input to the tire model gate, over this driver's own "
                "paired observations. Correlation is not a wear model, does not "
                "establish causation, and is not held-out predictive error."
            ),
        }


def wear_energy_diagnostic(
    observations: Sequence[Mapping[str, Any]],
) -> WearEnergyDiagnostic:
    """Compare energy against lap count as an explanation of measured wear.

    Each observation supplies ``energy_j_per_kg``, ``laps`` and
    ``wear_percent``. The comparison against lap count is the point: the tire
    model gate already treats a laps-only baseline as the bar a candidate model
    must clear, so an energy account that does not beat it has not earned a
    model, however physical its derivation looks.
    """

    energies: list[float] = []
    laps: list[float] = []
    wear: list[float] = []
    for observation in observations:
        energy = _finite(observation.get("energy_j_per_kg"))
        lap_count = _finite(observation.get("laps"))
        worn = _finite(observation.get("wear_percent"))
        if energy is None or lap_count is None or worn is None:
            continue
        energies.append(energy)
        laps.append(lap_count)
        wear.append(worn)

    if len(wear) < MINIMUM_PAIRED_OBSERVATIONS:
        return WearEnergyDiagnostic(
            status=STATUS_UNAVAILABLE,
            observation_count=len(wear),
            energy_r=None,
            laps_r=None,
            energy_explains_more=None,
            reason="insufficient_paired_observations",
        )

    energy_r = _pearson(energies, wear)
    laps_r = _pearson(laps, wear)
    if energy_r is None or laps_r is None:
        return WearEnergyDiagnostic(
            status=STATUS_LIMITED,
            observation_count=len(wear),
            energy_r=energy_r,
            laps_r=laps_r,
            energy_explains_more=None,
            reason="degenerate_variance",
        )
    return WearEnergyDiagnostic(
        status=STATUS_USABLE,
        observation_count=len(wear),
        energy_r=energy_r,
        laps_r=laps_r,
        energy_explains_more=abs(energy_r) > abs(laps_r),
        reason=None,
    )
