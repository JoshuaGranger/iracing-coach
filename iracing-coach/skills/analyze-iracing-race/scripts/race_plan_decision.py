"""The single typed race-plan decision that owns fuel strategy.

`FUEL-CONSISTENCY-001` (`FUEL-001`, `FUEL-SCENARIO-001`, `ARCH-PLAN-001`, the
fuel slice of `PRECISION-001`). This module is the producer authority for
scheduled distance, exact all-green range, minimum stop count, stint margin,
the one-green-lap reserve, and the qualified observed-caution scenario. Every
consumer - Python race card, Python reporting, the C#/Razor Planning and
Technical surfaces - reads a decision. None of them recomputes one.

The defect this replaces is worth stating exactly, because it is not a rounding
nicety. The backend emitted `all_green_range_laps` rounded to one decimal and
every consumer then re-derived the stop count from that rounded scalar:

* 50 scheduled laps against an exact 49.96-lap range. Rounded, the range reads
  50.0, the re-derivation yields zero stops, and the card renders "No fuel stop
  needed for 50 laps". Exactly, one stop is required. The car runs dry.
* 200 scheduled laps against an exact 66.66-lap range. Rounded to 66.7 the
  re-derivation yields two stops; exactly it is three.

Both are full-stop contradictions produced entirely by reparsing a display
scalar. The repair is structural rather than arithmetic: the decision carries
the *exact* range and the *decided* stop count, and re-planning at a different
distance goes back through :func:`decide_from_range`, never through a rounded
field. A consumer that wants different laps asks the authority again.

Two further rules are encoded here rather than left to prose.

* **No-stop language is a property of the decision, not of a comparison.**
  :attr:`RacePlanDecision.no_stop_language_permitted` is true only when the
  decided stop count is zero. A surface that branches on its own subtraction
  can print "no stop needed" beside a stop count of one; a surface that
  branches on this flag cannot.
* **The caution mix is scenario evidence, not a decision.** It lives in a
  separate typed record with its own evidence class, because the observed
  caution fraction of one past race is not established to transfer to the next
  one. Fusing it into the authoritative numbers would launder an unproven
  assumption into a fact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

#: Version of the race-plan decision envelope. A change to which fields are
#: decided, or to how they are decided, changes what a stored decision means,
#: so readers pin this rather than sniffing for fields.
RACE_PLAN_DECISION_VERSION = 1

#: The operational reserve, in green laps, held back from measured capacity
#: before any range is computed. It is exactly one green lap: a definitional
#: floor for finishing the race with fuel in the tank, not a fudge factor
#: absorbing arithmetic error. Slack beyond the floor is reported exactly by
#: :attr:`RacePlanDecision.final_stint_margin_laps` instead of being hidden
#: inside a larger reserve.
RESERVE_GREEN_LAPS = 1.0

#: Litres per US gallon, for the gallon projections consumers display.
LITRES_PER_GALLON = 3.785411784

#: Relative tolerance used when deciding whether a distance is an exact
#: multiple of the range.
#:
#: The quantity being tested is a ratio, so the tolerance must scale with it. A
#: fixed absolute epsilon subtracted before `ceil` - the form this module
#: replaces - is meaningless once the ratio is large and over-generous once it
#: is small. Here an exact multiple is recognised as such, and anything
#: measurably above a whole number consumes another stint.
EXACT_MULTIPLE_RELATIVE_TOLERANCE = 1e-9

#: The precision `all_green_range_laps` was rounded to before this contract
#: existed. A legacy range of 50.0 could have been anything in [49.95, 50.05],
#: and that interval is what a legacy re-plan must decide over.
LEGACY_RANGE_DISPLAY_PRECISION_LAPS = 0.1

STATUS_USABLE = "usable"
STATUS_HYBRID_UNRESOLVED = "hybrid_finish_constraint_unresolved"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_fuel_or_distance_evidence"
#: A pre-decision archive whose rounded range straddles a stint boundary at the
#: requested distance. Distinct from insufficient evidence because the remedy
#: is specific: re-analyze the race and the question becomes answerable.
STATUS_ROUNDED_RANGE_UNDECIDABLE = "rounded_range_cannot_decide"

#: Every status a decision can carry. Declared as data so the generated
#: contract and the C# consumer enumerate one list.
PLAN_STATUSES = (
    STATUS_USABLE,
    STATUS_HYBRID_UNRESOLVED,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_ROUNDED_RANGE_UNDECIDABLE,
)

#: Evidence class of the observed-caution record. It is never `measured` and
#: never `derived`: those would claim the next race resembles the last one.
CAUTION_EVIDENCE_CLASS = "scenario"

_SCENARIO_LIMITATION = (
    "The observed caution fraction describes the analyzed race only; its "
    "transfer to another race is not established, so this scenario must not "
    "replace the all-green decision."
)

_CLASSIFICATION = "fuel-feasibility decision, not an optimal-pit-call claim"

_ASSUMPTIONS = (
    "Uses the maximum fuel observed at a run start as available capacity; it "
    "may be below the car's legal tank maximum.",
    "Holds the measured green burn rate constant and reserves exactly one "
    "green lap of fuel for operational uncertainty.",
    "Equal-stint targets ignore live track position, stage breaks, pit loss, "
    "tire rules, damage, and future cautions.",
)

__all__ = [
    "CAUTION_EVIDENCE_CLASS",
    "EXACT_MULTIPLE_RELATIVE_TOLERANCE",
    "LEGACY_RANGE_DISPLAY_PRECISION_LAPS",
    "LITRES_PER_GALLON",
    "PLAN_STATUSES",
    "RACE_PLAN_DECISION_VERSION",
    "RESERVE_GREEN_LAPS",
    "STATUS_HYBRID_UNRESOLVED",
    "STATUS_INSUFFICIENT_EVIDENCE",
    "STATUS_ROUNDED_RANGE_UNDECIDABLE",
    "STATUS_USABLE",
    "CautionScenario",
    "RacePlanDecision",
    "RacePlanDecisionError",
    "decide",
    "decide_from_range",
    "from_legacy_forecast",
    "from_payload",
    "stint_count",
]


class RacePlanDecisionError(ValueError):
    """A stored decision could not be read under this contract version."""


def _finite(value: Any) -> float | None:
    """Return a finite float, or None for anything that cannot be one.

    Booleans are refused explicitly. `isinstance(True, int)` holds in Python,
    so a JSON `true` arriving where a burn rate belongs would otherwise become
    1.0 litres per lap and produce a confident, wrong plan.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0.0 else None


def stint_count(scheduled_laps: float, range_laps: float) -> int:
    """How many stints cover the distance, counting an exact multiple exactly.

    Raises rather than returning a sentinel: a caller holding a non-positive
    range has no distance question to ask, and answering "one stint" would be a
    fabricated plan.
    """
    if not (scheduled_laps > 0.0 and range_laps > 0.0):
        raise RacePlanDecisionError("stint count requires positive distance and range")
    ratio = scheduled_laps / range_laps
    nearest = round(ratio)
    if nearest >= 1 and abs(ratio - nearest) <= EXACT_MULTIPLE_RELATIVE_TOLERANCE * nearest:
        return int(nearest)
    return int(math.ceil(ratio))


@dataclass(frozen=True)
class CautionScenario:
    """The observed-caution mix, kept separate from the decision on purpose.

    This record can be shown, and it can inform a human judgement. It cannot be
    substituted for the all-green numbers, which is why it carries its own
    evidence class and its own limitation rather than being merged into
    :class:`RacePlanDecision`'s decided fields.
    """

    observed_caution_fraction: float
    mixed_burn_l_per_lap: float
    range_laps: float
    minimum_stops: int
    evidence_class: str = CAUTION_EVIDENCE_CLASS
    limitation: str = _SCENARIO_LIMITATION

    def to_payload(self) -> dict[str, Any]:
        return {
            "evidence_class": self.evidence_class,
            "limitation": self.limitation,
            "minimum_stops": self.minimum_stops,
            "mixed_burn_l_per_lap": self.mixed_burn_l_per_lap,
            "observed_caution_fraction": self.observed_caution_fraction,
            "range_laps": self.range_laps,
        }


@dataclass(frozen=True)
class RacePlanDecision:
    """The decided fuel plan. Exact values; no consumer arithmetic required.

    `all_green_range_laps` is the exact range after the one-green-lap reserve
    has been withheld. It is deliberately unrounded: rounding is a display
    concern, and the entire class of defect this type exists to remove came
    from a consumer treating a rounded display value as an input.
    """

    status: str
    scheduled_laps: float | None = None
    green_burn_l_per_lap: float | None = None
    maximum_start_fuel_l: float | None = None
    reserve_green_laps: float = RESERVE_GREEN_LAPS
    reserve_fuel_l: float | None = None
    usable_fuel_l: float | None = None
    all_green_range_laps: float | None = None
    minimum_stops: int | None = None
    stints: int | None = None
    final_stint_margin_laps: float | None = None
    equal_stint_pit_targets: tuple[float, ...] = ()
    caution_scenario: CautionScenario | None = None
    limitations: tuple[str, ...] = ()
    #: Whether this decision may be decided again at another distance. False
    #: for a decision adopted from a pre-decision archive, whose only range is
    #: a rounded display value.
    re_decidable: bool = True

    def __post_init__(self) -> None:
        if self.status not in PLAN_STATUSES:
            raise RacePlanDecisionError(f"unknown race plan status: {self.status!r}")
        if self.status != STATUS_USABLE:
            if self.minimum_stops is not None or self.all_green_range_laps is not None:
                raise RacePlanDecisionError(
                    "a decision that is not usable must not carry decided numbers"
                )
            return
        if self.minimum_stops is None or self.stints is None:
            raise RacePlanDecisionError("a usable decision must decide a stop count")
        if self.minimum_stops < 0 or self.stints != self.minimum_stops + 1:
            raise RacePlanDecisionError("stint count must be exactly one more than stops")
        if self.all_green_range_laps is None or self.all_green_range_laps <= 0.0:
            raise RacePlanDecisionError("a usable decision requires a positive range")
        if len(self.equal_stint_pit_targets) != self.minimum_stops:
            raise RacePlanDecisionError(
                "a usable decision needs exactly one pit target per decided stop"
            )

    @property
    def usable(self) -> bool:
        return self.status == STATUS_USABLE

    @property
    def no_stop_language_permitted(self) -> bool:
        """Whether a surface may say the race can be run without stopping.

        The invariant `minimum_stops > 0` never coexists with no-stop language
        is enforced here, once, rather than in each surface that renders a
        sentence. A non-usable decision permits nothing: absence of a number is
        not evidence that no stop is needed.
        """
        return self.usable and self.minimum_stops == 0

    def replan(self, scheduled_laps: float) -> "RacePlanDecision":
        """Decide again at a different distance, from the exact range.

        Planning a race other than the analyzed one is the case the rounded
        field served worst, so it is given a first-class path here. The range,
        burn, reserve and capacity carry over unchanged; only the distance and
        everything decided from it are recomputed.
        """
        if not self.usable:
            raise RacePlanDecisionError("cannot replan from a decision that is not usable")
        if not self.re_decidable:
            raise RacePlanDecisionError(
                "cannot replan from a decision whose range is a rounded display value"
            )
        assert self.all_green_range_laps is not None  # guaranteed by __post_init__
        return decide_from_range(
            scheduled_laps=scheduled_laps,
            all_green_range_laps=self.all_green_range_laps,
            green_burn_l_per_lap=self.green_burn_l_per_lap,
            maximum_start_fuel_l=self.maximum_start_fuel_l,
            reserve_fuel_l=self.reserve_fuel_l,
            usable_fuel_l=self.usable_fuel_l,
            caution_scenario=self.caution_scenario,
            limitations=self.limitations,
        )

    def to_payload(self) -> dict[str, Any]:
        """The transported form. Exact numbers; every key always present."""
        return {
            "all_green_range_laps": self.all_green_range_laps,
            "assumptions": list(_ASSUMPTIONS),
            "caution_scenario": (
                self.caution_scenario.to_payload() if self.caution_scenario else None
            ),
            "classification": _CLASSIFICATION,
            "decision_version": RACE_PLAN_DECISION_VERSION,
            "equal_stint_pit_targets": list(self.equal_stint_pit_targets),
            "final_stint_margin_laps": self.final_stint_margin_laps,
            "green_burn_l_per_lap": self.green_burn_l_per_lap,
            "limitations": list(self.limitations),
            "maximum_start_fuel_l": self.maximum_start_fuel_l,
            "minimum_stops": self.minimum_stops,
            "no_stop_language_permitted": self.no_stop_language_permitted,
            "re_decidable": self.re_decidable,
            "reserve_fuel_l": self.reserve_fuel_l,
            "reserve_green_laps": self.reserve_green_laps,
            "scheduled_laps": self.scheduled_laps,
            "status": self.status,
            "stints": self.stints,
            "usable_fuel_l": self.usable_fuel_l,
        }


def _unusable(status: str, limitations: tuple[str, ...]) -> RacePlanDecision:
    return RacePlanDecision(status=status, limitations=limitations)


def decide_from_range(
    *,
    scheduled_laps: float | None,
    all_green_range_laps: float | None,
    green_burn_l_per_lap: float | None = None,
    maximum_start_fuel_l: float | None = None,
    reserve_fuel_l: float | None = None,
    usable_fuel_l: float | None = None,
    caution_scenario: CautionScenario | None = None,
    limitations: tuple[str, ...] = (),
) -> RacePlanDecision:
    """Decide a plan from an exact range that has already been established.

    This is the entry point for re-planning and for consumers holding a stored
    exact range. It never accepts a rounded input on the caller's word: the
    range is used as given, so passing a display value here produces a plan
    whose inputs are the display value, and that is a caller defect this module
    deliberately does not paper over.
    """
    distance = _positive(scheduled_laps)
    range_laps = _positive(all_green_range_laps)
    if distance is None or range_laps is None:
        return _unusable(STATUS_INSUFFICIENT_EVIDENCE, limitations)

    stints = stint_count(distance, range_laps)
    stops = stints - 1
    targets = tuple(distance * index / stints for index in range(1, stints))
    return RacePlanDecision(
        status=STATUS_USABLE,
        scheduled_laps=distance,
        green_burn_l_per_lap=_positive(green_burn_l_per_lap),
        maximum_start_fuel_l=_finite(maximum_start_fuel_l),
        reserve_green_laps=RESERVE_GREEN_LAPS,
        reserve_fuel_l=_finite(reserve_fuel_l),
        usable_fuel_l=_finite(usable_fuel_l),
        all_green_range_laps=range_laps,
        minimum_stops=stops,
        stints=stints,
        final_stint_margin_laps=stints * range_laps - distance,
        equal_stint_pit_targets=targets,
        caution_scenario=caution_scenario,
        limitations=limitations,
    )


def decide(
    *,
    scheduled_laps: float | None,
    green_burn_l_per_lap: float | None,
    maximum_start_fuel_l: float | None,
    caution_burn_l_per_lap: float | None = None,
    observed_caution_fraction: float | None = None,
    hybrid_limits: bool = False,
    limitations: tuple[str, ...] = (),
) -> RacePlanDecision:
    """Decide a plan from measured race quantities.

    `hybrid_limits` is checked before anything else. When a race carries both a
    lap cap and a time cap the governing finish constraint is unknown, so the
    distance is unknown, so there is no honest plan to state - and stating one
    anyway is how a confident number gets attached to an unanswered question.
    """
    if hybrid_limits:
        return _unusable(STATUS_HYBRID_UNRESOLVED, limitations)

    burn = _positive(green_burn_l_per_lap)
    capacity = _positive(maximum_start_fuel_l)
    distance = _positive(scheduled_laps)
    if burn is None or capacity is None or distance is None:
        return _unusable(STATUS_INSUFFICIENT_EVIDENCE, limitations)

    reserve_fuel = burn * RESERVE_GREEN_LAPS
    usable_fuel = capacity - reserve_fuel
    if usable_fuel <= 0.0:
        # Capacity below the reserve is not a zero-range plan; it is an absent
        # one. Returning range 0.0 would invite a division and a stop count of
        # infinity dressed up as a number.
        return _unusable(STATUS_INSUFFICIENT_EVIDENCE, limitations)

    range_laps = usable_fuel / burn
    scenario = _caution_scenario(
        usable_fuel=usable_fuel,
        green_burn=burn,
        caution_burn=_positive(caution_burn_l_per_lap),
        observed_caution_fraction=observed_caution_fraction,
        scheduled_laps=distance,
    )
    return decide_from_range(
        scheduled_laps=distance,
        all_green_range_laps=range_laps,
        green_burn_l_per_lap=burn,
        maximum_start_fuel_l=capacity,
        reserve_fuel_l=reserve_fuel,
        usable_fuel_l=usable_fuel,
        caution_scenario=scenario,
        limitations=limitations,
    )


def _caution_scenario(
    *,
    usable_fuel: float,
    green_burn: float,
    caution_burn: float | None,
    observed_caution_fraction: Any,
    scheduled_laps: float,
) -> CautionScenario | None:
    """Build the qualified caution record, or none at all.

    A caution fraction of zero produces no scenario. The mixed burn would then
    equal the green burn and the record would restate the decision under a
    different name, which is precisely how a scenario acquires the authority of
    a measurement.
    """
    fraction = _finite(observed_caution_fraction)
    if fraction is None or not 0.0 < fraction <= 1.0:
        return None
    effective_caution_burn = caution_burn if caution_burn is not None else green_burn
    mixed_burn = (1.0 - fraction) * green_burn + fraction * effective_caution_burn
    if mixed_burn <= 0.0:
        return None
    range_laps = usable_fuel / mixed_burn
    return CautionScenario(
        observed_caution_fraction=fraction,
        mixed_burn_l_per_lap=mixed_burn,
        range_laps=range_laps,
        minimum_stops=stint_count(scheduled_laps, range_laps) - 1,
    )


def _legacy_decision(
    *,
    distance: float,
    range_laps: float,
    stops: int,
    targets: tuple[float, ...],
    forecast: Mapping[str, Any],
    limitation: str,
) -> RacePlanDecision:
    stints = stops + 1
    return RacePlanDecision(
        status=STATUS_USABLE,
        scheduled_laps=distance,
        green_burn_l_per_lap=None,
        maximum_start_fuel_l=_finite(forecast.get("maximum_recorded_run_start_fuel_l")),
        reserve_green_laps=_finite(forecast.get("operational_reserve_green_laps"))
        or RESERVE_GREEN_LAPS,
        reserve_fuel_l=_finite(forecast.get("operational_reserve_fuel_l")),
        usable_fuel_l=None,
        all_green_range_laps=range_laps,
        minimum_stops=stops,
        stints=stints,
        # Clamped, because this margin is computed from a rounded range while
        # the count beside it is certain. A small negative number here would
        # contradict a zero-stop count that the interval test has proved.
        final_stint_margin_laps=max(0.0, stints * range_laps - distance),
        equal_stint_pit_targets=targets,
        caution_scenario=None,
        limitations=(limitation,),
        re_decidable=False,
    )


def from_legacy_forecast(
    forecast: Mapping[str, Any],
    *,
    scheduled_laps: float | None = None,
    display_precision_laps: float = LEGACY_RANGE_DISPLAY_PRECISION_LAPS,
) -> RacePlanDecision | None:
    """Read a pre-decision archived forecast without ever trusting its range.

    The direction of trust here is the whole point. The old producer computed
    `minimum_stops_all_green` from the *exact* range and then rounded the range
    for display; only consumers re-derived. So the archived count is sound and
    the archived range is not.

    That gives two cases.

    * **At the forecast's own distance** the stored count is adopted verbatim.
      No arithmetic is performed on the rounded range at all.
    * **At any other distance** - planning a different race from this evidence -
      there is no exact range to decide from, so the count is decided over the
      whole interval the rounded value could have come from. When both ends of
      that interval yield the same count, the count is certain despite the
      rounding and may be stated. When they disagree, the rounding genuinely
      straddles a stint boundary and no count is stated, because stating either
      one is the "No fuel stop needed" contradiction in a new costume.

    The second case is deliberately not an all-or-nothing refusal. Rounding
    only obscures the answer within a narrow band around a stint boundary;
    refusing every legacy re-plan would discard a capability that is sound
    almost everywhere, and refusing none of them would keep the defect.
    """
    if not isinstance(forecast, Mapping) or forecast.get("status") != STATUS_USABLE:
        return None
    stops = forecast.get("minimum_stops_all_green")
    if isinstance(stops, bool) or not isinstance(stops, int) or stops < 0:
        return None
    range_laps = _positive(forecast.get("all_green_range_laps"))
    if range_laps is None:
        return None
    stored_distance = _positive(forecast.get("scheduled_laps"))
    targets = tuple(
        value
        for raw in (forecast.get("equal_stint_pit_targets_all_green") or ())
        if (value := _finite(raw)) is not None
    )

    requested = _positive(scheduled_laps)
    if requested is None or (
        stored_distance is not None
        and math.isclose(requested, stored_distance, rel_tol=1e-12, abs_tol=1e-9)
    ):
        distance = requested if requested is not None else stored_distance
        if distance is None or len(targets) != stops:
            return None
        return _legacy_decision(
            distance=distance,
            range_laps=range_laps,
            stops=stops,
            targets=targets,
            forecast=forecast,
            limitation=(
                "Read from a pre-decision archive: the stop count is the exact "
                "one this race was analyzed with, but the range beside it is a "
                "rounded display value."
            ),
        )

    half_width = max(0.0, display_precision_laps) / 2.0
    low = range_laps - half_width
    high = range_laps + half_width
    if low <= 0.0:
        return None
    # A larger range needs no more stints, so the two ends bracket the answer.
    fewest = stint_count(requested, high)
    most = stint_count(requested, low)
    if fewest != most:
        return RacePlanDecision(
            status=STATUS_ROUNDED_RANGE_UNDECIDABLE,
            limitations=(
                "This race was analyzed before the plan decision existed, and "
                "its rounded range straddles a stint boundary at this "
                "distance. Re-analyze the race to decide it.",
            ),
        )
    decided = fewest - 1
    return _legacy_decision(
        distance=requested,
        range_laps=range_laps,
        stops=decided,
        targets=tuple(requested * index / fewest for index in range(1, fewest)),
        forecast=forecast,
        limitation=(
            "Re-decided at a new distance from a pre-decision archive. The "
            "stored range is rounded, but every range it could have been "
            "yields this same stop count."
        ),
    )


def from_payload(payload: Mapping[str, Any]) -> RacePlanDecision:
    """Read a transported decision, failing closed on an unreadable version.

    A future decision is refused rather than partially read. The fields a newer
    producer decides are not knowable here, and reconstructing a decision from
    the subset this version recognises would silently drop whatever made the
    newer one different.
    """
    if not isinstance(payload, Mapping):
        raise RacePlanDecisionError("decision payload is not an object")
    version = payload.get("decision_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RacePlanDecisionError("decision payload has no integer decision_version")
    if version != RACE_PLAN_DECISION_VERSION:
        raise RacePlanDecisionError(
            f"decision version {version} is not readable by version "
            f"{RACE_PLAN_DECISION_VERSION}"
        )
    status = payload.get("status")
    if status not in PLAN_STATUSES:
        raise RacePlanDecisionError(f"unknown race plan status: {status!r}")
    if status != STATUS_USABLE:
        return _unusable(str(status), tuple(payload.get("limitations") or ()))

    scenario_payload = payload.get("caution_scenario")
    scenario = None
    if isinstance(scenario_payload, Mapping):
        fraction = _finite(scenario_payload.get("observed_caution_fraction"))
        mixed = _positive(scenario_payload.get("mixed_burn_l_per_lap"))
        scenario_range = _positive(scenario_payload.get("range_laps"))
        stops = scenario_payload.get("minimum_stops")
        if (
            fraction is not None
            and mixed is not None
            and scenario_range is not None
            and isinstance(stops, int)
            and not isinstance(stops, bool)
        ):
            scenario = CautionScenario(
                observed_caution_fraction=fraction,
                mixed_burn_l_per_lap=mixed,
                range_laps=scenario_range,
                minimum_stops=stops,
            )

    # Re-decide from the transported exact range rather than trusting the
    # transported stop count. A payload whose count disagrees with its own
    # range is malformed, and adopting its count would carry the disagreement
    # forward into every surface that reads it.
    return decide_from_range(
        scheduled_laps=_finite(payload.get("scheduled_laps")),
        all_green_range_laps=_finite(payload.get("all_green_range_laps")),
        green_burn_l_per_lap=_finite(payload.get("green_burn_l_per_lap")),
        maximum_start_fuel_l=_finite(payload.get("maximum_start_fuel_l")),
        reserve_fuel_l=_finite(payload.get("reserve_fuel_l")),
        usable_fuel_l=_finite(payload.get("usable_fuel_l")),
        caution_scenario=scenario,
        limitations=tuple(str(item) for item in (payload.get("limitations") or ())),
    )
