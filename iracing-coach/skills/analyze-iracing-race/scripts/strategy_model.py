"""Rank pit strategies by race time, from measured quantities only.

`STRATEGY-SIM-001`.

The engine's existing strategy output says of itself that it is a
"fuel-feasibility forecast, not an optimal-pit-call claim", and it is right:
it computes how few stops the fuel allows and where equal stints would fall.
That answers "can I finish" and never "which plan wins". This module answers
the second question, to the extent the recorded evidence honestly supports it.

What it does **not** do is worth stating first, because the tempting version of
this feature is the dishonest one. It does not roll dice on caution timing. A
Monte Carlo needs a caution arrival rate, nobody has measured one for this
driver's series, and a simulation seeded with an invented rate would return a
win probability with two decimal places and no evidence underneath it - the
"magical certainty" the project's ranked decisions explicitly reject. The
observed caution fraction of one past race is already treated elsewhere in this
codebase as scenario evidence rather than a decision, and that precedent holds
here.

What it does instead is what a crew chief actually does on the pit box:

* **Rank the plans deterministically** over quantities that were measured -
  green lap time, tire degradation slope, fuel burn, and the pit loss this
  driver's own stops cost - so the ordering is arithmetic, not opinion.
* **Report the margin**, because a plan that wins by 0.3 s over a 90-minute
  race is not meaningfully ahead of the alternative and should not be presented
  as though it were.
* **Solve the break-evens**, which is the part a strategist can act on. The
  useful output is not "two stops" but "two stops wins unless the pit loss is
  above 31 s" and "the extra stop pays only if fresh tires are worth more than
  0.11 s a lap". Those are thresholds the driver can check against reality.

Degradation is modelled as linear in tire age within a stint, resetting when
tires are changed, because a linear slope is what the analysis engine actually
measures (``green_lap_time_slope_s_per_lap``). A richer curve is available only
once the tire model gate opens, and pretending to one now would be inventing
the very physics this module refuses to invent.

Track position, traffic, and the differing consequence of a stop taken under
caution are all outside the model and are stated as such on every result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


#: Version of the strategy contract.
STRATEGY_MODEL_VERSION = 1

#: Joshua's standing reserve: one green lap of fuel beyond the plan.
DEFAULT_RESERVE_GREEN_LAPS = 1.0

#: Stop counts beyond this are not enumerated; no sensible plan for a race this
#: model is given approaches it, and the bound keeps enumeration finite.
MAXIMUM_STOPS = 8

#: A margin under this is reported as too close to separate the plans.
DECISIVE_MARGIN_S = 1.0

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


@dataclass(frozen=True)
class Strategy:
    """One plan: how many stops, how the stints are split, what it costs."""

    stop_count: int
    stint_laps: tuple[int, ...]
    total_time_s: float
    green_time_s: float
    pit_time_s: float
    degradation_time_s: float
    feasible: bool
    infeasible_reason: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "stop_count": self.stop_count,
            "stint_laps": list(self.stint_laps),
            "total_time_s": round(self.total_time_s, 3),
            "green_time_s": round(self.green_time_s, 3),
            "pit_time_s": round(self.pit_time_s, 3),
            "degradation_time_s": round(self.degradation_time_s, 3),
            "feasible": self.feasible,
            "infeasible_reason": self.infeasible_reason,
        }


@dataclass(frozen=True)
class BreakEven:
    """The value of one input at which the ranking would change."""

    quantity: str
    current_value: float
    threshold_value: float | None
    direction: str
    note: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "current_value": round(self.current_value, 4),
            "threshold_value": (
                round(self.threshold_value, 4)
                if self.threshold_value is not None
                else None
            ),
            "direction": self.direction,
            "note": self.note,
        }


@dataclass(frozen=True)
class StrategyComparison:
    """Every feasible plan, ranked, with the thresholds that would reorder it."""

    strategies: tuple[Strategy, ...]
    status: str
    reason: str | None
    best_stop_count: int | None
    margin_s: float | None
    decisive: bool | None
    break_evens: tuple[BreakEven, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": STRATEGY_MODEL_VERSION,
            "status": self.status,
            "reason": self.reason,
            "best_stop_count": self.best_stop_count,
            "margin_s": round(self.margin_s, 3) if self.margin_s is not None else None,
            "decisive": self.decisive,
            "decisive_margin_s": DECISIVE_MARGIN_S,
            "strategies": [item.to_payload() for item in self.strategies],
            "break_evens": [item.to_payload() for item in self.break_evens],
            "classification": (
                "deterministic race-time ranking over measured green pace, "
                "degradation, fuel burn and this driver's own pit loss"
            ),
            "excluded_from_model": [
                "track position and the field's pace",
                "traffic and lapped cars",
                "the different consequence of a stop taken under caution",
                "future caution timing, which is not modelled and not simulated",
                "damage, penalties and stage or race rules",
            ],
        }


@dataclass(frozen=True)
class PositionContext:
    """What a time margin was worth in places, against the observed field.

    A margin in seconds is not a decision until it is priced in positions. Ten
    seconds is enormous in a tight midfield and irrelevant when the nearest car
    finished half a minute away, and only the recorded gaps can tell the two
    apart. Cars are counted from their measured finishing gaps rather than from
    any assumption about how a race would have unfolded, so this says what the
    margin *would have spanned in this race*, not what position would result
    from running the alternative plan - the rest of the field would have raced
    differently, and nothing here pretends to model that.
    """

    margin_s: float
    cars_within_margin_ahead: int
    cars_within_margin_behind: int
    nearest_ahead_s: float | None
    nearest_behind_s: float | None
    status: str

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None) -> float | None:
            return round(value, 3) if value is not None else None

        return {
            "margin_s": round(self.margin_s, 3),
            "cars_within_margin_ahead": self.cars_within_margin_ahead,
            "cars_within_margin_behind": self.cars_within_margin_behind,
            "nearest_ahead_s": rounded(self.nearest_ahead_s),
            "nearest_behind_s": rounded(self.nearest_behind_s),
            "status": self.status,
            "interpretation": (
                "how many cars this margin would have spanned at the recorded "
                "finishing gaps; not a predicted finishing position, because the "
                "rest of the field would also have raced the alternative plan "
                "differently"
            ),
        }


def positions_from_margin(
    margin_s: Any, gaps_s: Iterable[Any]
) -> PositionContext | None:
    """Price a time margin in cars, using measured finishing gaps.

    ``gaps_s`` are time gaps to each competitor, positive when that car is
    ahead of the player.
    """

    margin = _finite(margin_s)
    if margin is None or margin < 0.0:
        return None
    ahead: list[float] = []
    behind: list[float] = []
    for value in gaps_s:
        gap = _finite(value)
        if gap is None:
            continue
        if gap > 0.0:
            ahead.append(gap)
        elif gap < 0.0:
            behind.append(-gap)
    if not ahead and not behind:
        return PositionContext(
            margin_s=margin,
            cars_within_margin_ahead=0,
            cars_within_margin_behind=0,
            nearest_ahead_s=None,
            nearest_behind_s=None,
            status=STATUS_UNAVAILABLE,
        )
    return PositionContext(
        margin_s=margin,
        cars_within_margin_ahead=sum(1 for gap in ahead if gap <= margin),
        cars_within_margin_behind=sum(1 for gap in behind if gap <= margin),
        nearest_ahead_s=min(ahead) if ahead else None,
        nearest_behind_s=min(behind) if behind else None,
        status=STATUS_USABLE,
    )


def _split_stints(race_laps: int, stop_count: int, maximum_stint: int) -> tuple[int, ...] | None:
    """Divide the race into as-equal stints that each fit the fuel limit."""

    stints = stop_count + 1
    if stints <= 0 or race_laps <= 0:
        return None
    base = race_laps // stints
    remainder = race_laps % stints
    lengths = [base + (1 if index < remainder else 0) for index in range(stints)]
    if any(length <= 0 for length in lengths):
        return None
    if max(lengths) > maximum_stint:
        return None
    return tuple(lengths)


def _stint_time(
    laps: int, base_lap_s: float, degradation_s_per_lap: float
) -> tuple[float, float]:
    """Return (total seconds, degradation seconds) for one stint.

    Tire age starts at zero on the first lap of a stint, so the degradation
    penalty over ``n`` laps is ``deg * (0 + 1 + ... + n-1)``.
    """

    green = base_lap_s * laps
    penalty = degradation_s_per_lap * (laps * (laps - 1) / 2.0)
    return green + penalty, penalty


def evaluate_strategy(
    *,
    race_laps: int,
    stop_count: int,
    base_lap_s: float,
    degradation_s_per_lap: float,
    pit_loss_s: float,
    maximum_stint_laps: int,
) -> Strategy:
    """Cost one plan end to end."""

    stints = _split_stints(race_laps, stop_count, maximum_stint_laps)
    if stints is None:
        return Strategy(
            stop_count=stop_count,
            stint_laps=(),
            total_time_s=math.inf,
            green_time_s=0.0,
            pit_time_s=0.0,
            degradation_time_s=0.0,
            feasible=False,
            infeasible_reason=(
                "stint_exceeds_fuel_range"
                if stop_count < MAXIMUM_STOPS
                else "too_many_stops"
            ),
        )
    total = 0.0
    degradation_total = 0.0
    for laps in stints:
        stint_total, penalty = _stint_time(laps, base_lap_s, degradation_s_per_lap)
        total += stint_total
        degradation_total += penalty
    pit_total = pit_loss_s * stop_count
    return Strategy(
        stop_count=stop_count,
        stint_laps=stints,
        total_time_s=total + pit_total,
        green_time_s=base_lap_s * race_laps,
        pit_time_s=pit_total,
        degradation_time_s=degradation_total,
        feasible=True,
        infeasible_reason=None,
    )


def _degradation_break_even(
    race_laps: int,
    best: Strategy,
    runner_up: Strategy,
    base_lap_s: float,
    pit_loss_s: float,
    maximum_stint_laps: int,
    current_degradation: float,
) -> float | None:
    """Find the degradation at which the top two plans tie.

    Total time is linear in the degradation slope for a fixed stint split, so
    the tie is solved directly rather than searched: each plan's time is
    ``constant + slope * weight``, and the crossing is where those lines meet.
    """

    def weight(strategy: Strategy) -> float:
        return sum(laps * (laps - 1) / 2.0 for laps in strategy.stint_laps)

    best_weight = weight(best)
    other_weight = weight(runner_up)
    if math.isclose(best_weight, other_weight):
        return None
    best_constant = base_lap_s * race_laps + pit_loss_s * best.stop_count
    other_constant = base_lap_s * race_laps + pit_loss_s * runner_up.stop_count
    crossing = (other_constant - best_constant) / (best_weight - other_weight)
    return crossing if math.isfinite(crossing) else None


def _pit_loss_break_even(
    race_laps: int,
    best: Strategy,
    runner_up: Strategy,
    base_lap_s: float,
    degradation_s_per_lap: float,
) -> float | None:
    """Find the pit loss at which the top two plans tie."""

    if best.stop_count == runner_up.stop_count:
        return None

    def constant(strategy: Strategy) -> float:
        total = 0.0
        for laps in strategy.stint_laps:
            stint_total, _ = _stint_time(laps, base_lap_s, degradation_s_per_lap)
            total += stint_total
        return total

    crossing = (constant(runner_up) - constant(best)) / (
        best.stop_count - runner_up.stop_count
    )
    return crossing if math.isfinite(crossing) and crossing >= 0.0 else None


def compare_strategies(
    *,
    race_laps: Any,
    base_lap_s: Any,
    degradation_s_per_lap: Any,
    pit_loss_s: Any,
    fuel_capacity_l: Any,
    green_burn_l_per_lap: Any,
    reserve_green_laps: float = DEFAULT_RESERVE_GREEN_LAPS,
) -> StrategyComparison:
    """Rank every feasible stop count for the race by total race time."""

    laps = _finite(race_laps)
    base = _finite(base_lap_s)
    degradation = _finite(degradation_s_per_lap)
    pit = _finite(pit_loss_s)
    capacity = _finite(fuel_capacity_l)
    burn = _finite(green_burn_l_per_lap)

    missing = [
        name
        for name, value in (
            ("race_laps", laps),
            ("base_lap_s", base),
            ("degradation_s_per_lap", degradation),
            ("pit_loss_s", pit),
            ("fuel_capacity_l", capacity),
            ("green_burn_l_per_lap", burn),
        )
        if value is None
    ]
    if missing:
        return StrategyComparison(
            strategies=(),
            status=STATUS_UNAVAILABLE,
            reason=f"missing_inputs:{','.join(missing)}",
            best_stop_count=None,
            margin_s=None,
            decisive=None,
            break_evens=(),
        )
    if laps < 1 or base <= 0.0 or burn <= 0.0 or capacity <= 0.0 or pit < 0.0:
        return StrategyComparison(
            strategies=(),
            status=STATUS_UNAVAILABLE,
            reason="inputs_out_of_range",
            best_stop_count=None,
            margin_s=None,
            decisive=None,
            break_evens=(),
        )

    race_laps_int = int(round(laps))
    usable_fuel = capacity - reserve_green_laps * burn
    maximum_stint = int(math.floor(usable_fuel / burn)) if usable_fuel > 0 else 0
    if maximum_stint < 1:
        return StrategyComparison(
            strategies=(),
            status=STATUS_UNAVAILABLE,
            reason="reserve_exceeds_capacity",
            best_stop_count=None,
            margin_s=None,
            decisive=None,
            break_evens=(),
        )

    evaluated = [
        evaluate_strategy(
            race_laps=race_laps_int,
            stop_count=stop_count,
            base_lap_s=base,
            degradation_s_per_lap=degradation,
            pit_loss_s=pit,
            maximum_stint_laps=maximum_stint,
        )
        for stop_count in range(0, MAXIMUM_STOPS + 1)
    ]
    feasible = [item for item in evaluated if item.feasible]
    if not feasible:
        return StrategyComparison(
            strategies=tuple(evaluated),
            status=STATUS_UNAVAILABLE,
            reason="no_feasible_strategy_within_fuel_range",
            best_stop_count=None,
            margin_s=None,
            decisive=None,
            break_evens=(),
        )

    ranked = sorted(feasible, key=lambda item: (item.total_time_s, item.stop_count))
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    margin = runner_up.total_time_s - best.total_time_s if runner_up else None

    break_evens: list[BreakEven] = []
    if runner_up is not None:
        degradation_threshold = _degradation_break_even(
            race_laps_int, best, runner_up, base, pit, maximum_stint, degradation
        )
        if degradation_threshold is not None:
            break_evens.append(
                BreakEven(
                    quantity="degradation_s_per_lap",
                    current_value=degradation,
                    threshold_value=degradation_threshold,
                    direction=(
                        "above" if degradation_threshold > degradation else "below"
                    ),
                    note=(
                        f"{best.stop_count} stop(s) stops winning once degradation "
                        f"passes this value; {runner_up.stop_count} takes over."
                    ),
                )
            )
        pit_threshold = _pit_loss_break_even(
            race_laps_int, best, runner_up, base, degradation
        )
        if pit_threshold is not None:
            break_evens.append(
                BreakEven(
                    quantity="pit_loss_s",
                    current_value=pit,
                    threshold_value=pit_threshold,
                    direction="above" if pit_threshold > pit else "below",
                    note=(
                        f"{best.stop_count} stop(s) stops winning once a stop costs "
                        f"past this; {runner_up.stop_count} takes over."
                    ),
                )
            )

    return StrategyComparison(
        strategies=tuple(ranked + [item for item in evaluated if not item.feasible]),
        status=STATUS_USABLE if len(feasible) > 1 else STATUS_LIMITED,
        reason=None if len(feasible) > 1 else "only_one_feasible_strategy",
        best_stop_count=best.stop_count,
        margin_s=margin,
        decisive=(margin >= DECISIVE_MARGIN_S) if margin is not None else None,
        break_evens=tuple(break_evens),
    )
