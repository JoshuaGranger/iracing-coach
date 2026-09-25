"""Steering applied, independent of the car's steering ratio.

iRacing records ``SteeringWheelAngle``: the angle of the steering wheel. The
same front-wheel steer needs 12/10 as much wheel at a 12:1 ratio as at 10:1,
so wheel angle measures the setup's ratio as much as the driving. In this
backend "steering" is the road-wheel-equivalent angle: ``SteeringWheelAngle``
divided by the steering ratio the recording's own garage setup states
(``CarSetup ... SteeringRatio: "12:1"``; O'Reilly Supra, Truck and Classic
Taurus recordings all use ``CarSetup.Chassis.Front.SteeringRatio``, fixed
setups included).

Nothing is guessed. When the ratio is absent, unreadable, stated twice with
different values, or the recording's ``SteeringWheelAngleMax`` shows the
steering changed mid-recording, steering falls back to the steering-wheel
angle, labelled as such, and a road-wheel value and a steering-wheel value are
never compared, averaged or drawn on one axis.

The setup's ``SteeringOffset`` (a fixed rotation of the wheel against the
front wheels) is recorded for provenance but not applied: the SDK does not
document how it enters ``SteeringWheelAngle``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = 1
ROAD_WHEEL = "road_wheel"
STEERING_WHEEL = "steering_wheel"
RAW_CHANNEL = "SteeringWheelAngle"
RATIO_KEY = "steeringratio"
OFFSET_KEY = "steeringoffset"

ROAD_WHEEL_LABEL = "Steering (road wheel)"
WHEEL_LABEL = "Steering wheel angle"
# Why a recording's steering stays on the steering wheel, in chart-label form.
FALLBACK_SUFFIX = {
    "not_recorded": "ratio not recorded",
    "unreadable": "ratio not readable",
    "ambiguous": "ratio not readable",
    "changed_during_recording": "ratio changed during recording",
}

# A value outside this range is not a steering ratio any car runs.
MINIMUM_RATIO = 4.0
MAXIMUM_RATIO = 40.0
# SteeringWheelAngleMax is the wheel's lock (road-wheel lock x ratio); a change
# larger than this fraction means the steering itself changed mid-recording.
MAXIMUM_LOCK_DRIFT = 0.001

# Steering thresholds (material input, correction step, turn-in demand,
# stress full scale, coaching levels) were written against steering-wheel
# angle. On road-wheel angle each is that value divided by 12, so a 12:1
# recording keeps every count it had; a recording without a ratio keeps the
# steering-wheel values unchanged.
THRESHOLD_REFERENCE_RATIO = 12.0

_RATIO_TEXT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*:\s*1(?:\.0+)?\s*")
_NUMBER_TEXT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*")


def parse_steering_ratio(value: Any) -> float | None:
    """The ratio a setup value states, or None when it is not an n:1 ratio.

    iRacing writes ``"12:1"``; ``"16.5:1"``, ``"12 : 1"`` and a bare number
    (``12``, ``"12.0"``) are accepted. ``"12:2"``, ``"1:12"``, zero, negative,
    non-finite, out-of-range and non-numeric values are not ratios.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = _RATIO_TEXT.fullmatch(value) or _NUMBER_TEXT.fullmatch(value)
        if match is None:
            return None
        number = float(match.group(1))
    else:
        return None
    if not math.isfinite(number) or not MINIMUM_RATIO <= number <= MAXIMUM_RATIO:
        return None
    return number


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _leaves(node: Any, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, Mapping):
        for key, child in node.items():
            yield from _leaves(child, path + (str(key),))
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
        for index, child in enumerate(node):
            yield from _leaves(child, path + (str(index),))
    else:
        yield path, node


def _ratio_text(ratio: float) -> str:
    return f"{ratio:g}:1"


@dataclass(frozen=True)
class SteeringRatio:
    """What the recording's garage setup says about its steering ratio."""

    status: str
    ratio: float | None = None
    text: str | None = None
    source: str | None = None
    offset_text: str | None = None
    reason: str | None = None


def recorded_steering_ratio(session_info: Any) -> SteeringRatio:
    """Find the one steering ratio a recording's ``CarSetup`` states.

    Every ``SteeringRatio`` key anywhere in ``CarSetup`` is read, so a car that
    keeps it outside ``Chassis/Front`` is still found. None, an unreadable
    value, or two different ratios all leave the ratio unknown.
    """

    setup = session_info.get("CarSetup") if isinstance(session_info, Mapping) else None
    if not isinstance(setup, Mapping) or not setup:
        return SteeringRatio("not_recorded", reason="The recording has no garage setup (CarSetup) to state a steering ratio.")
    leaves = list(_leaves(setup, ("CarSetup",)))
    found = [(path, value) for path, value in leaves if path and _normalized_key(path[-1]) == RATIO_KEY]
    offsets = [value for path, value in leaves if path and _normalized_key(path[-1]) == OFFSET_KEY]
    offset_text = str(offsets[0]).strip() if len(offsets) == 1 and str(offsets[0]).strip() else None
    if not found:
        return SteeringRatio("not_recorded", offset_text=offset_text,
                             reason="The garage setup does not state a steering ratio.")
    parsed = [(path, value, parse_steering_ratio(value)) for path, value in found]
    unreadable = next(((path, value) for path, value, ratio in parsed if ratio is None), None)
    if unreadable is not None:
        return SteeringRatio("unreadable", text=str(unreadable[1]), source=".".join(unreadable[0]),
                             offset_text=offset_text,
                             reason=f"The garage setup's steering ratio {str(unreadable[1])!r} is not an n:1 ratio.")
    ratios = sorted({ratio for _path, _value, ratio in parsed if ratio is not None})
    if len(ratios) > 1:
        return SteeringRatio("ambiguous", source=", ".join(".".join(path) for path, _value, _ratio in parsed),
                             offset_text=offset_text,
                             reason="The garage setup states more than one steering ratio ("
                             + ", ".join(_ratio_text(ratio) for ratio in ratios) + ").")
    path, value, ratio = parsed[0]
    return SteeringRatio("recorded", ratio=ratio, text=str(value).strip(), source=".".join(path),
                         offset_text=offset_text)


def _lock_changed(wheel_angle_max: Sequence[Any] | None) -> bool:
    values = [
        float(value) for value in wheel_angle_max or ()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value > 0.0
    ]
    if len(values) < 2:
        return False
    low, high = min(values), max(values)
    return (high - low) > MAXIMUM_LOCK_DRIFT * high


@dataclass(frozen=True)
class SteeringBasis:
    """What a recording's steering values mean, and how to label them."""

    kind: str
    status: str
    ratio: float | None = None
    ratio_text: str | None = None
    ratio_source: str | None = None
    offset_text: str | None = None
    reason: str | None = None
    wheel_angle_recorded: bool = True

    @property
    def road_wheel(self) -> bool:
        return self.kind == ROAD_WHEEL and self.ratio is not None

    @property
    def scale(self) -> float:
        """Multiplier from recorded steering-wheel radians to this basis."""
        return 1.0 / self.ratio if self.road_wheel and self.ratio else 1.0

    def threshold(self, wheel_value: float) -> float:
        """A threshold written against steering-wheel angle, in this basis."""
        return wheel_value / THRESHOLD_REFERENCE_RATIO if self.road_wheel else wheel_value

    @property
    def label(self) -> str:
        if self.road_wheel:
            return ROAD_WHEEL_LABEL
        return f"{WHEEL_LABEL} — {FALLBACK_SUFFIX.get(self.status, FALLBACK_SUFFIX['not_recorded'])}"

    @property
    def short_label(self) -> str:
        return ROAD_WHEEL_LABEL if self.road_wheel else WHEEL_LABEL

    @property
    def degree_digits(self) -> int:
        """Road-wheel angles are about a twelfth of wheel angles: one more digit."""
        return 2 if self.road_wheel else 1

    def degrees_text(self, radians: float, *, signed: bool = False) -> str:
        value = math.degrees(radians)
        return f"{value:+.{self.degree_digits}f}°" if signed else f"{value:.{self.degree_digits}f}°"

    @property
    def definition(self) -> str:
        if self.road_wheel:
            text = (
                f"Recorded {RAW_CHANNEL} divided by the recording's {self.ratio_text or _ratio_text(self.ratio or 0.0)} "
                "steering ratio: the steer applied at the front wheels, comparable across steering ratios."
            )
            if self.offset_text:
                text += f" The setup's steering offset ({self.offset_text}) is not applied."
            return text
        return (
            f"Recorded {RAW_CHANNEL}. Without the car's steering ratio it cannot be converted to road-wheel "
            "angle, so it is never compared with road-wheel steering."
        )

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "basis": self.kind,
            "status": self.status,
            "label": self.label,
            "short_label": self.short_label,
            "unit": "deg",
            "value_unit": "rad",
            "degree_digits": self.degree_digits,
            "steering_ratio": self.ratio,
            "steering_ratio_text": self.ratio_text,
            "ratio_source": self.ratio_source,
            "setup_steering_offset": self.offset_text,
            "steering_offset_applied": False,
            "raw_channel": RAW_CHANNEL,
            "steering_wheel_angle_recorded": self.wheel_angle_recorded,
            "evidence_class": "derived" if self.road_wheel else "measured",
            "definition": self.definition,
            "reason": self.reason,
            "threshold_reference_ratio": THRESHOLD_REFERENCE_RATIO,
        }

    @classmethod
    def from_descriptor(cls, value: Any) -> "SteeringBasis":
        """Read a descriptor back. A missing or unknown one is wheel angle.

        Analyses written before this module recorded steering-wheel angle and
        state no basis; they are wheel angle with the ratio unknown.
        """

        if not isinstance(value, Mapping):
            return cls(STEERING_WHEEL, "not_recorded",
                       reason="This analysis predates steering-ratio normalization.")
        ratio = parse_steering_ratio(value.get("steering_ratio"))
        kind = value.get("basis")
        status = str(value.get("status") or "not_recorded")
        common = dict(
            ratio_text=value.get("steering_ratio_text"),
            ratio_source=value.get("ratio_source"),
            offset_text=value.get("setup_steering_offset"),
            reason=value.get("reason"),
            wheel_angle_recorded=value.get("steering_wheel_angle_recorded") is not False,
        )
        if kind == ROAD_WHEEL and ratio is not None:
            return cls(ROAD_WHEEL, status, ratio=ratio, **common)
        return cls(STEERING_WHEEL, status if status in FALLBACK_SUFFIX else "not_recorded", **common)


def resolve_steering_basis(
    session_info: Any,
    *,
    wheel_angle_recorded: bool = True,
    wheel_angle_max: Sequence[Any] | None = None,
) -> SteeringBasis:
    """The basis for one recording: road wheel only with one stated, stable ratio."""

    recorded = recorded_steering_ratio(session_info)
    common = dict(ratio_source=recorded.source, offset_text=recorded.offset_text,
                  wheel_angle_recorded=wheel_angle_recorded)
    if recorded.status != "recorded" or recorded.ratio is None:
        return SteeringBasis(STEERING_WHEEL, recorded.status, ratio_text=recorded.text,
                             reason=recorded.reason, **common)
    if _lock_changed(wheel_angle_max):
        return SteeringBasis(
            STEERING_WHEEL, "changed_during_recording", ratio_text=recorded.text,
            reason=(f"SteeringWheelAngleMax changed during the recording, so the setup's final "
                    f"{recorded.text} ratio may not apply to every lap."),
            **common,
        )
    return SteeringBasis(ROAD_WHEEL, "recorded", ratio=recorded.ratio, ratio_text=recorded.text, **common)


def basis_of(analysis: Any) -> SteeringBasis:
    """The steering basis a saved analysis document declares."""

    if not isinstance(analysis, Mapping):
        return SteeringBasis.from_descriptor(None)
    value = analysis.get("steering")
    if not isinstance(value, Mapping):
        traces = analysis.get("lap_traces")
        value = traces.get("steering") if isinstance(traces, Mapping) else None
    return SteeringBasis.from_descriptor(value)


def comparable_across_recordings(first: SteeringBasis, second: SteeringBasis) -> bool:
    """Steering from two recordings is comparable only as road-wheel angle.

    Two steering-wheel angles with unknown ratios may come from different
    ratios, which is exactly the bias road-wheel angle removes.
    """

    return first.road_wheel and second.road_wheel


def pair_descriptor(basis: SteeringBasis, *, comparable: bool = True, reason: str | None = None) -> dict[str, Any]:
    """The compact steering declaration a pass pair carries with its rows."""

    return {
        "basis": basis.kind if comparable else None,
        "label": basis.label,
        "short_label": basis.short_label,
        "unit": "deg",
        "degree_digits": basis.degree_digits,
        "comparable": comparable,
        "reason": reason,
    }
