"""Diagnose the tire data before choosing a model, and refuse to invent one.

`MODEL-UTILITY-001`, `MODEL-GATE-001`, `MODEL-GATE-BLOCKED-001`,
`MODEL-REPRESENTATION-001`.

The predictor consumes eleven whole-run aggregates while the telemetry
vocabulary is far richer, and nobody knows whether that is the right shape
because neither model has seen Joshua's archive. Gate occupancy, usable feature
coverage and held-out performance are all unmeasured. The failure this guards
against is the tempting one: build a richer model anyway, and let it report
confident numbers that its data never supported.

So the order is fixed, and it is enforced here rather than trusted.

1. **Diagnose first.** :func:`summarize_cohorts` turns observations into cohort
   counts and feature coverage and *nothing else*. It emits no raw value, no
   extreme, and no cohort small enough to identify a single run, because the
   whole point is a summary Joshua can authorize running over private data and
   then hand back without handing back the data. This module never reads the
   archive; Joshua runs the command, separately, himself.
2. **Gate on what the diagnosis says.** :func:`gate_decision` has an explicit
   blocked state and an explicit insufficient state. `MODEL-GATE-BLOCKED-001` is
   the requirement that these never quietly degrade into "proceed anyway": a
   gate that has not run is not a gate that passed, and inadequate data changes
   the scope to collection, never to a fabricated model.
3. **Beat honest baselines or do not ship.** :func:`evaluate_candidate` compares
   a candidate against laps-only and cohort-median baselines on event-held-out
   error, and additionally requires the uncertainty to be calibrated. A model
   with a better mean error and dishonest intervals is refused, because the
   intervals are what the surface would render as confidence.
4. **Represent what is actually known.** :func:`model_representation` collapses
   the verdict to the three display states the closure asks for, and the
   unavailable state is a first-class answer rather than an empty chart.

Nothing here trains anything, opens a file, or reads a clock. It decides whether
a model is allowed to exist and how honestly it may be described.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

#: Version of the model gate contract.
TIRE_MODEL_GATE_VERSION = 1

#: The smallest cohort that may be described individually. Below this a cohort
#: is counted but not detailed, so a diagnostic taken over a private archive
#: cannot single out one session.
MINIMUM_DISCLOSED_COHORT = 5

#: Held-out events required before a comparison means anything. Chosen so a
#: single lucky event cannot decide adoption.
MINIMUM_HELD_OUT_EVENTS = 8

#: How far empirical interval coverage may sit from its nominal level before the
#: uncertainty is considered miscalibrated.
CALIBRATION_TOLERANCE = 0.1

#: The candidate must beat the best baseline by more than this fraction. A model
#: that ties is not worth its own failure modes.
MINIMUM_RELATIVE_IMPROVEMENT = 0.05

GATE_BLOCKED = "blocked_awaiting_aggregate"
GATE_INSUFFICIENT = "insufficient_data"
GATE_READY_FOR_BASELINE = "ready_for_baseline"

#: Gate outcomes. There is no "proceed" that skips the baseline comparison; the
#: best a diagnosis can say is that comparing is now worthwhile.
GATE_STATES = (GATE_BLOCKED, GATE_INSUFFICIENT, GATE_READY_FOR_BASELINE)

REPRESENTATION_UNAVAILABLE = "unavailable"
REPRESENTATION_LOW_CONFIDENCE = "low_confidence"
REPRESENTATION_HIGH_CONFIDENCE = "high_confidence"

#: The three display states the closure names. Anything the model cannot
#: support is `unavailable`, which is a thing to render rather than a blank.
REPRESENTATION_STATES = (
    REPRESENTATION_UNAVAILABLE,
    REPRESENTATION_LOW_CONFIDENCE,
    REPRESENTATION_HIGH_CONFIDENCE,
)

BASELINE_LAPS_ONLY = "laps_only"
BASELINE_COHORT_MEDIAN = "cohort_median"

#: The honest baselines a candidate must beat. Both are deliberately stupid: if
#: a richer model cannot beat them it has bought complexity and nothing else.
REQUIRED_BASELINES = (BASELINE_LAPS_ONLY, BASELINE_COHORT_MEDIAN)

__all__ = [
    "AggregateDiagnostics",
    "BaselineScore",
    "CALIBRATION_TOLERANCE",
    "CandidateScore",
    "CohortSummary",
    "GATE_STATES",
    "GateDecision",
    "MINIMUM_DISCLOSED_COHORT",
    "MINIMUM_HELD_OUT_EVENTS",
    "ModelVerdict",
    "REPRESENTATION_STATES",
    "REQUIRED_BASELINES",
    "TIRE_MODEL_GATE_VERSION",
    "TireModelGateError",
    "evaluate_candidate",
    "gate_decision",
    "model_representation",
    "summarize_cohorts",
]


class TireModelGateError(ValueError):
    """A diagnostic, gate or evaluation violated the model contract."""


@dataclass(frozen=True)
class CohortSummary:
    """How much data one cohort has, and which features it actually carries.

    ``feature_coverage`` maps a feature name to the fraction of the cohort's
    observations that recorded it. It is the number that decides whether a
    richer model is even expressible, which is why it is measured rather than
    assumed from the channel vocabulary.
    """

    cohort_key: str
    observations: int
    feature_coverage: Mapping[str, float] = field(default_factory=dict)
    disclosed: bool = True

    def __post_init__(self) -> None:
        if not self.cohort_key:
            raise TireModelGateError("a cohort summary needs a cohort key")
        if isinstance(self.observations, bool) or not isinstance(self.observations, int):
            raise TireModelGateError("observations must be a JSON integer")
        if self.observations < 0:
            raise TireModelGateError("observations must not be negative")
        for name, fraction in self.feature_coverage.items():
            if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
                raise TireModelGateError(f"coverage for {name!r} must be a number")
            if not 0.0 <= float(fraction) <= 1.0:
                raise TireModelGateError(f"coverage for {name!r} must be a fraction")
        if not self.disclosed and self.feature_coverage:
            # The suppression would be pointless if the detail came anyway.
            raise TireModelGateError("a suppressed cohort cannot report feature coverage")

    def to_payload(self) -> dict[str, Any]:
        return {
            "cohort_key": self.cohort_key,
            "observations": self.observations,
            "feature_coverage": {
                name: round(float(value), 4) for name, value in sorted(self.feature_coverage.items())
            },
            "disclosed": self.disclosed,
        }


@dataclass(frozen=True)
class AggregateDiagnostics:
    """The non-sensitive result of looking at the archive.

    This is the artifact Joshua can run over private data and hand back. It
    holds counts and coverage fractions; it deliberately holds no observed
    value, no minimum, no maximum and no cohort small enough to be one session.
    """

    total_observations: int
    cohorts: tuple[CohortSummary, ...]
    features_seen: tuple[str, ...] = ()
    suppressed_cohorts: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.total_observations, bool) or not isinstance(
            self.total_observations, int
        ):
            raise TireModelGateError("total_observations must be a JSON integer")
        if self.total_observations < 0:
            raise TireModelGateError("total_observations must not be negative")
        counted = sum(cohort.observations for cohort in self.cohorts)
        if counted != self.total_observations:
            raise TireModelGateError(
                "the cohort counts must add up to the total observations"
            )

    @property
    def disclosed_cohorts(self) -> tuple[CohortSummary, ...]:
        return tuple(cohort for cohort in self.cohorts if cohort.disclosed)

    @property
    def largest_cohort(self) -> int:
        return max((cohort.observations for cohort in self.cohorts), default=0)

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": TIRE_MODEL_GATE_VERSION,
            "total_observations": self.total_observations,
            "cohort_count": len(self.cohorts),
            "suppressed_cohorts": self.suppressed_cohorts,
            "features_seen": list(self.features_seen),
            "cohorts": [cohort.to_payload() for cohort in self.cohorts],
        }


def summarize_cohorts(
    observations: Iterable[Mapping[str, Any]],
    *,
    cohort_field: str = "context_key",
    features: Sequence[str] | None = None,
    minimum_disclosed: int = MINIMUM_DISCLOSED_COHORT,
) -> AggregateDiagnostics:
    """Reduce observations to counts and coverage, and to nothing else.

    A feature counts as present when the key exists and is not ``None``. The
    value itself is never read into the result - only whether it was there -
    which is what makes the output safe to carry back from a private archive.
    """
    if minimum_disclosed < 1:
        raise TireModelGateError("the disclosure threshold must be at least one")

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    seen_features: set[str] = set()
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise TireModelGateError("every observation must be a mapping")
        key = str(observation.get(cohort_field) or "").strip()
        if not key:
            raise TireModelGateError(f"every observation needs a {cohort_field}")
        grouped.setdefault(key, []).append(observation)
        for name, value in observation.items():
            if name != cohort_field and value is not None:
                seen_features.add(name)

    tracked = tuple(features) if features is not None else tuple(sorted(seen_features))

    summaries: list[CohortSummary] = []
    suppressed = 0
    for key in sorted(grouped):
        members = grouped[key]
        if len(members) < minimum_disclosed:
            suppressed += 1
            summaries.append(
                CohortSummary(cohort_key=key, observations=len(members), disclosed=False)
            )
            continue
        coverage = {
            name: sum(
                1 for member in members if member.get(name) is not None
            ) / len(members)
            for name in tracked
        }
        summaries.append(
            CohortSummary(
                cohort_key=key, observations=len(members), feature_coverage=coverage
            )
        )

    return AggregateDiagnostics(
        total_observations=sum(len(members) for members in grouped.values()),
        cohorts=tuple(summaries),
        features_seen=tracked,
        suppressed_cohorts=suppressed,
    )


@dataclass(frozen=True)
class GateDecision:
    """Whether the data supports attempting a model at all."""

    state: str
    reasons: tuple[str, ...] = ()
    recommended_action: str = ""

    def __post_init__(self) -> None:
        if self.state not in GATE_STATES:
            raise TireModelGateError(f"unknown gate state: {self.state!r}")
        if self.state != GATE_READY_FOR_BASELINE and not self.reasons:
            raise TireModelGateError("a gate that does not open must say why")

    @property
    def may_attempt_model(self) -> bool:
        return self.state == GATE_READY_FOR_BASELINE

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": TIRE_MODEL_GATE_VERSION,
            "state": self.state,
            "may_attempt_model": self.may_attempt_model,
            "reasons": list(self.reasons),
            "recommended_action": self.recommended_action,
        }


def gate_decision(
    diagnostics: AggregateDiagnostics | None,
    *,
    required_features: Sequence[str] = (),
    minimum_coverage: float = 0.8,
    minimum_cohort: int = MINIMUM_HELD_OUT_EVENTS,
) -> GateDecision:
    """Decide whether a model attempt is justified by the diagnosis.

    ``None`` means the aggregate has not been run. That is the blocked state and
    it is deliberately not the same as a failed gate: nothing has been measured,
    so nothing may be concluded, and the remedy is to ask Joshua to run it.
    """
    if diagnostics is None:
        return GateDecision(
            state=GATE_BLOCKED,
            reasons=("the sanitized aggregate has not been run",),
            recommended_action="request_aggregate_authorization",
        )
    if not isinstance(diagnostics, AggregateDiagnostics):
        raise TireModelGateError("gate_decision needs AggregateDiagnostics or None")
    if not 0.0 <= minimum_coverage <= 1.0:
        raise TireModelGateError("minimum_coverage must be a fraction")

    reasons: list[str] = []
    usable = [
        cohort
        for cohort in diagnostics.disclosed_cohorts
        if cohort.observations >= minimum_cohort
    ]
    if not usable:
        reasons.append(
            f"no cohort reaches {minimum_cohort} observations, so held-out "
            "comparison is not possible"
        )
    for feature in required_features:
        covered = [
            cohort
            for cohort in usable
            if cohort.feature_coverage.get(feature, 0.0) >= minimum_coverage
        ]
        if not covered:
            reasons.append(
                f"no usable cohort records {feature!r} in at least "
                f"{minimum_coverage:.0%} of its observations"
            )

    if reasons:
        return GateDecision(
            state=GATE_INSUFFICIENT,
            reasons=tuple(reasons),
            # The clarification is explicit that this outcome changes the scope
            # to collection or policy. It never authorizes a smaller model.
            recommended_action="plan_data_collection",
        )
    return GateDecision(
        state=GATE_READY_FOR_BASELINE, recommended_action="compare_against_baselines"
    )


@dataclass(frozen=True)
class BaselineScore:
    """How an honest baseline did on the held-out events."""

    name: str
    held_out_events: int
    mean_absolute_error: float

    def __post_init__(self) -> None:
        if not self.name:
            raise TireModelGateError("a baseline needs a name")
        _require_count(self.held_out_events, "held_out_events")
        _require_error(self.mean_absolute_error, "mean_absolute_error")


@dataclass(frozen=True)
class CandidateScore:
    """How the proposed model did, including whether it knows what it knows.

    ``interval_coverage`` is the fraction of held-out truths that fell inside
    the model's own ``nominal_coverage`` interval. Reporting the error without
    it would let a confidently wrong model pass.
    """

    held_out_events: int
    mean_absolute_error: float
    interval_coverage: float
    nominal_coverage: float = 0.8

    def __post_init__(self) -> None:
        _require_count(self.held_out_events, "held_out_events")
        _require_error(self.mean_absolute_error, "mean_absolute_error")
        for name, value in (
            ("interval_coverage", self.interval_coverage),
            ("nominal_coverage", self.nominal_coverage),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TireModelGateError(f"{name} must be a number")
            if not 0.0 <= float(value) <= 1.0:
                raise TireModelGateError(f"{name} must be a fraction")

    @property
    def calibration_error(self) -> float:
        return abs(float(self.interval_coverage) - float(self.nominal_coverage))


def _require_count(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TireModelGateError(f"{name} must be a JSON integer")
    if value < 0:
        raise TireModelGateError(f"{name} must not be negative")


def _require_error(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TireModelGateError(f"{name} must be a number")
    if float(value) != float(value) or float(value) in (float("inf"), float("-inf")):
        raise TireModelGateError(f"{name} must be finite")
    if float(value) < 0:
        raise TireModelGateError(f"{name} must not be negative")


@dataclass(frozen=True)
class ModelVerdict:
    """Whether the candidate may be adopted, and how it may be described."""

    adopt: bool
    representation: str
    reasons: tuple[str, ...] = ()
    best_baseline: str = ""
    relative_improvement: float | None = None

    def __post_init__(self) -> None:
        if self.representation not in REPRESENTATION_STATES:
            raise TireModelGateError(
                f"unknown representation state: {self.representation!r}"
            )
        if not self.adopt and self.representation == REPRESENTATION_HIGH_CONFIDENCE:
            raise TireModelGateError("a rejected candidate cannot be shown confidently")
        if not self.reasons:
            raise TireModelGateError("a verdict must say what decided it")

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": TIRE_MODEL_GATE_VERSION,
            "adopt": self.adopt,
            "representation": self.representation,
            "reasons": list(self.reasons),
            "best_baseline": self.best_baseline,
            "relative_improvement": self.relative_improvement,
        }


def evaluate_candidate(
    candidate: CandidateScore,
    baselines: Sequence[BaselineScore],
    *,
    minimum_held_out: int = MINIMUM_HELD_OUT_EVENTS,
    minimum_improvement: float = MINIMUM_RELATIVE_IMPROVEMENT,
    calibration_tolerance: float = CALIBRATION_TOLERANCE,
) -> ModelVerdict:
    """Adopt a candidate only if it is both better and honest.

    Every refusal path lands on a representation the surface can render, because
    "we could not justify a model" is information Joshua should see rather than
    an empty panel.
    """
    if not isinstance(candidate, CandidateScore):
        raise TireModelGateError("evaluate_candidate needs a CandidateScore")
    names = {baseline.name for baseline in baselines}
    missing = [name for name in REQUIRED_BASELINES if name not in names]
    if missing:
        raise TireModelGateError(
            "the candidate must be compared against " + ", ".join(missing)
        )

    reasons: list[str] = []
    if candidate.held_out_events < minimum_held_out:
        reasons.append(
            f"only {candidate.held_out_events} held-out events, below the "
            f"{minimum_held_out} required to compare"
        )
    for baseline in baselines:
        if baseline.held_out_events != candidate.held_out_events:
            # Different held-out sets make the comparison meaningless, and it is
            # the exact way a flattering number gets produced by accident.
            reasons.append(
                f"the {baseline.name} baseline was scored on "
                f"{baseline.held_out_events} events rather than "
                f"{candidate.held_out_events}"
            )
    if reasons:
        return ModelVerdict(
            adopt=False,
            representation=REPRESENTATION_UNAVAILABLE,
            reasons=tuple(reasons),
        )

    best = min(baselines, key=lambda item: item.mean_absolute_error)
    if best.mean_absolute_error <= 0:
        raise TireModelGateError("a baseline with no error cannot be improved upon")
    improvement = (
        best.mean_absolute_error - candidate.mean_absolute_error
    ) / best.mean_absolute_error

    if improvement <= minimum_improvement:
        return ModelVerdict(
            adopt=False,
            representation=REPRESENTATION_UNAVAILABLE,
            reasons=(
                f"the candidate does not beat the {best.name} baseline by more "
                f"than {minimum_improvement:.0%}",
            ),
            best_baseline=best.name,
            relative_improvement=improvement,
        )

    if candidate.calibration_error > calibration_tolerance:
        # Better on average and wrong about its own uncertainty. The intervals
        # are what a surface renders as confidence, so this cannot be adopted
        # merely because the mean error improved.
        return ModelVerdict(
            adopt=False,
            representation=REPRESENTATION_UNAVAILABLE,
            reasons=(
                f"interval coverage {candidate.interval_coverage:.0%} misses its "
                f"nominal {candidate.nominal_coverage:.0%} by more than "
                f"{calibration_tolerance:.0%}",
            ),
            best_baseline=best.name,
            relative_improvement=improvement,
        )

    confident = (
        candidate.held_out_events >= minimum_held_out * 2
        and candidate.calibration_error <= calibration_tolerance / 2
    )
    return ModelVerdict(
        adopt=True,
        representation=(
            REPRESENTATION_HIGH_CONFIDENCE if confident else REPRESENTATION_LOW_CONFIDENCE
        ),
        reasons=(
            f"beats the {best.name} baseline by {improvement:.0%} with calibrated "
            f"intervals over {candidate.held_out_events} held-out events",
        ),
        best_baseline=best.name,
        relative_improvement=improvement,
    )


def model_representation(verdict: ModelVerdict | None) -> str:
    """The display state, including for the case where nothing was decided."""
    if verdict is None:
        return REPRESENTATION_UNAVAILABLE
    if not isinstance(verdict, ModelVerdict):
        raise TireModelGateError("model_representation needs a ModelVerdict or None")
    return verdict.representation


def cohort_median_baseline(
    observations: Iterable[Mapping[str, Any]], *, target_field: str
) -> float | None:
    """The cohort-median baseline value, or ``None`` when nothing was observed.

    Provided so the baseline a candidate must beat is computed the same way
    everywhere rather than re-derived per experiment.
    """
    values = [
        float(observation[target_field])
        for observation in observations
        if isinstance(observation, Mapping)
        and isinstance(observation.get(target_field), (int, float))
        and not isinstance(observation.get(target_field), bool)
    ]
    return median(values) if values else None
