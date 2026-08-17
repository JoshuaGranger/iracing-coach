"""Stable evidence records, so a displayed claim can be traced to its support.

`UI-EVIDENCE-LOSS-001`, `INCIDENT-ABSENCE-001`, `EVIDENCE-DEAD-001`, and the
reachable core of `ARCH-001`. The backend already labels claims `measured`,
`derived`, `inferred` and `proxy`, but a bare label is not traceable: two
claims with the same word behind them can rest on ten laps and on one, and a
surface that carries only the word cannot tell them apart. Worse, the label is
dropped entirely on the way to Planning, so identical prose arrives with no
provenance at all.

An evidence record is the unit that survives that trip. It has a **stable id**
so the same support keeps the same identity across runs and can be linked to
from more than one claim; a **class** (the existing vocabulary); a **source**
naming what produced it; **coverage** saying how much of the thing it actually
observed; **confidence**; and **limitations** in the record rather than in
prose beside it.

Two rules are enforced here rather than left to the surfaces.

* **Absence is not zero.** `INCIDENT-ABSENCE-001`: when the incident channel
  was not recorded, the count is unknown. Rendering "None" for it states a fact
  about a clean race that nobody observed. :func:`count_record` therefore
  distinguishes an observed zero from an unobserved count, and the two produce
  different records rather than the same numeral. The separation is carried
  through to the claim: an unavailable record can be linked only to a
  :data:`CLAIM_UNAVAILABLE` claim, so "zero incidents occurred" cannot be
  attached to evidence that never counted any.
* **A cause needs support.** An unsupported causal claim is absent, not
  hedged. :func:`link` refuses to attach a claim to a record that does not
  support it, so the "dead evidence" case - a contract carrying provenance no
  reachable surface consumes - fails loudly at the producer instead of quietly
  at the consumer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: Version of the evidence record contract.
EVIDENCE_RECORD_VERSION = 1

CLASS_MEASURED = "measured"
CLASS_DERIVED = "derived"
CLASS_INFERRED = "inferred"
CLASS_PROXY = "proxy"
CLASS_SCENARIO = "scenario"
CLASS_UNAVAILABLE = "unavailable"

#: The evidence vocabulary. `scenario` is added for evidence that describes one
#: observed situation whose transfer to another is not established - the
#: observed-caution mix is the first of these - because calling it `derived`
#: would give a hypothesis the standing of a calculation.
EVIDENCE_CLASSES = (
    CLASS_MEASURED,
    CLASS_DERIVED,
    CLASS_INFERRED,
    CLASS_PROXY,
    CLASS_SCENARIO,
    CLASS_UNAVAILABLE,
)

#: Classes strong enough to carry a causal claim. Deliberately short: a proxy
#: metric can describe what happened without establishing why, and a scenario
#: describes a situation rather than a mechanism.
CLASSES_SUPPORTING_CAUSE = (CLASS_MEASURED, CLASS_DERIVED)

COVERAGE_COMPLETE = "complete"
COVERAGE_PARTIAL = "partial"
COVERAGE_ABSENT = "absent"
COVERAGE_UNKNOWN = "unknown"

#: How much of the subject the evidence observed. `absent` and `unknown` are
#: different answers: the channel was recorded and held nothing, or the channel
#: was never recorded. Conflating them is `INCIDENT-ABSENCE-001`.
COVERAGE_STATES = (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    COVERAGE_ABSENT,
    COVERAGE_UNKNOWN,
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

CONFIDENCE_LEVELS = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
    CONFIDENCE_NONE,
)

CLAIM_FACT = "fact"
CLAIM_COMPARISON = "comparison"
CLAIM_CAUSE = "cause"
CLAIM_RECOMMENDATION = "recommendation"
#: A statement that something was *not observed*. Its own kind, because it is a
#: different assertion from a fact about the subject and needs different
#: evidence - and because the alternative is policing prose.
#:
#: "Zero incidents occurred" and "the incident channel was not recorded" are
#: both sentences a surface might render from an unavailable record. Only the
#: second is true. Before this kind existed the two were indistinguishable to
#: :func:`link`, which checked the claim kind and not what the claim asserts, so
#: an unobserved count could be published as an observed zero.
CLAIM_UNAVAILABLE = "unavailable"

#: What a claim asserts. The kind decides what evidence it needs, which is why
#: it is a declared value rather than a property of the sentence.
CLAIM_KINDS = (
    CLAIM_FACT,
    CLAIM_COMPARISON,
    CLAIM_CAUSE,
    CLAIM_RECOMMENDATION,
    CLAIM_UNAVAILABLE,
)

__all__ = [
    "CLAIM_KINDS",
    "CLAIM_UNAVAILABLE",
    "CLASSES_SUPPORTING_CAUSE",
    "CONFIDENCE_LEVELS",
    "COVERAGE_STATES",
    "EVIDENCE_CLASSES",
    "EVIDENCE_RECORD_VERSION",
    "EvidenceError",
    "EvidenceRecord",
    "count_record",
    "link",
    "unavailable_record",
]


class EvidenceError(ValueError):
    """A record or a link violated the evidence contract."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class EvidenceRecord:
    """One piece of support, identified stably enough to be linked to.

    The id is derived from the content, not assigned by a counter. Two runs
    over the same telemetry produce the same id for the same support, so a
    stored link keeps resolving and a surface can cache by it - and a record
    whose coverage changed gets a different id, which is what stops a stale
    link from resolving to evidence that no longer says the same thing.
    """

    subject: str
    evidence_class: str
    source: str
    coverage: str
    confidence: str
    observations: int | None = None
    limitations: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject:
            raise EvidenceError("an evidence record needs a subject")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise EvidenceError(f"unknown evidence class: {self.evidence_class!r}")
        if self.coverage not in COVERAGE_STATES:
            raise EvidenceError(f"unknown coverage state: {self.coverage!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise EvidenceError(f"unknown confidence level: {self.confidence!r}")
        if not self.source:
            raise EvidenceError("an evidence record must name its source")
        if self.observations is not None:
            if isinstance(self.observations, bool) or not isinstance(self.observations, int):
                raise EvidenceError("observations must be a JSON integer")
            if self.observations < 0:
                raise EvidenceError("observations must not be negative")
        if self.evidence_class == CLASS_UNAVAILABLE:
            if self.coverage not in (COVERAGE_ABSENT, COVERAGE_UNKNOWN):
                raise EvidenceError("unavailable evidence cannot report coverage of a subject")
            if self.confidence != CONFIDENCE_NONE:
                raise EvidenceError("unavailable evidence has no confidence")
        if self.coverage == COVERAGE_UNKNOWN and self.observations is not None:
            # An unknown coverage with a count is the exact shape of the
            # incident defect: a number presented for something never observed.
            raise EvidenceError("coverage cannot be unknown while reporting a count")
        if self.coverage == COVERAGE_COMPLETE and self.confidence == CONFIDENCE_NONE:
            raise EvidenceError("complete coverage with no confidence is contradictory")

    @property
    def evidence_id(self) -> str:
        return "ev-" + hashlib.sha256(
            _canonical(
                {
                    "version": EVIDENCE_RECORD_VERSION,
                    "subject": self.subject,
                    "class": self.evidence_class,
                    "source": self.source,
                    "coverage": self.coverage,
                    "confidence": self.confidence,
                    "observations": self.observations,
                    "limitations": list(self.limitations),
                    "detail": dict(self.detail),
                }
            ).encode("utf-8")
        ).hexdigest()[:20]

    @property
    def supports_cause(self) -> bool:
        """Whether a causal claim may rest on this record.

        Partial coverage does not support a cause. Observing some of a race and
        concluding why something happened in the part not observed is precisely
        the unsupported causal claim this record exists to prevent.
        """
        return (
            self.evidence_class in CLASSES_SUPPORTING_CAUSE
            and self.coverage == COVERAGE_COMPLETE
            and self.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)
        )

    @property
    def displayable(self) -> bool:
        """Whether anything may be shown from this record.

        An unavailable record is displayable: showing that a thing was not
        observed is the honest output, and suppressing it is how a missing
        measurement becomes an implied zero.
        """
        return True

    def to_payload(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "coverage": self.coverage,
            "detail": dict(self.detail),
            "evidence_class": self.evidence_class,
            "evidence_id": self.evidence_id,
            "limitations": list(self.limitations),
            "observations": self.observations,
            "record_version": EVIDENCE_RECORD_VERSION,
            "source": self.source,
            "subject": self.subject,
            "supports_cause": self.supports_cause,
        }


def unavailable_record(subject: str, source: str, reason: str) -> EvidenceRecord:
    """The record for something that was not observed.

    Every surface needs one of these, because the alternative to rendering an
    unavailable state is rendering a default, and a default is a claim.
    """
    return EvidenceRecord(
        subject=subject,
        evidence_class=CLASS_UNAVAILABLE,
        source=source,
        coverage=COVERAGE_UNKNOWN,
        confidence=CONFIDENCE_NONE,
        limitations=(reason,),
    )


def count_record(
    *,
    subject: str,
    source: str,
    count: int | None,
    channel_recorded: bool,
    limitations: Sequence[str] = (),
) -> EvidenceRecord:
    """A record for a counted thing, keeping an unobserved count unobserved.

    `INCIDENT-ABSENCE-001` in one function. A recorded channel that counted
    zero is a measured fact and says zero. An unrecorded channel is unknown,
    carries no number at all, and cannot be rendered as "None" - there is
    nothing for a consumer to render but the unavailable state, which is the
    intended outcome.
    """
    if not channel_recorded:
        return EvidenceRecord(
            subject=subject,
            evidence_class=CLASS_UNAVAILABLE,
            source=source,
            coverage=COVERAGE_UNKNOWN,
            confidence=CONFIDENCE_NONE,
            observations=None,
            limitations=(
                *tuple(limitations),
                "The source channel was not recorded, so this count is unknown "
                "rather than zero.",
            ),
        )
    if count is None or isinstance(count, bool) or not isinstance(count, int):
        raise EvidenceError("a recorded channel must supply an integer count")
    if count < 0:
        raise EvidenceError("a count must not be negative")
    return EvidenceRecord(
        subject=subject,
        evidence_class=CLASS_MEASURED,
        source=source,
        coverage=COVERAGE_COMPLETE,
        confidence=CONFIDENCE_HIGH,
        observations=count,
        limitations=tuple(limitations),
    )


@dataclass(frozen=True)
class ClaimLink:
    """A claim bound to the record that supports it."""

    claim_kind: str
    text: str
    evidence_id: str
    evidence_class: str


def link(claim_kind: str, text: str, record: EvidenceRecord) -> ClaimLink:
    """Bind a claim to its evidence, refusing a binding the record cannot bear.

    Refusing at the producer is the whole design. `EVIDENCE-DEAD-001` is a
    contract that carries provenance no surface consumes; the mirror failure is
    a surface that renders a claim its evidence never supported. Both are
    silent at runtime, so this is where they are made loud.
    """
    if claim_kind not in CLAIM_KINDS:
        raise EvidenceError(f"unknown claim kind: {claim_kind!r}")
    if not text or not text.strip():
        raise EvidenceError("a claim needs text")
    # The binding is structural in both directions, and it has to be: the words
    # of a claim are not inspectable here, so the only way to stop an
    # unobserved count from being published as an observed zero is to make
    # unavailable evidence incapable of carrying any claim except a statement of
    # unavailability.
    if record.evidence_class == CLASS_UNAVAILABLE and claim_kind != CLAIM_UNAVAILABLE:
        raise EvidenceError(
            f"unavailable evidence supports only an {CLAIM_UNAVAILABLE} claim, "
            f"never a {claim_kind}: it observed nothing, so any value stated "
            "from it would be invented"
        )
    if claim_kind == CLAIM_UNAVAILABLE and record.evidence_class != CLASS_UNAVAILABLE:
        raise EvidenceError(
            f"an {CLAIM_UNAVAILABLE} claim needs an {CLASS_UNAVAILABLE} record; "
            f"{record.evidence_class} evidence observed the subject"
        )
    if claim_kind == CLAIM_CAUSE and not record.supports_cause:
        raise EvidenceError(
            "a causal claim needs complete, measured or derived evidence with "
            f"at least medium confidence; got {record.evidence_class}/"
            f"{record.coverage}/{record.confidence}"
        )
    if claim_kind == CLAIM_COMPARISON and record.coverage == COVERAGE_UNKNOWN:
        raise EvidenceError("a comparison needs evidence of known coverage")
    return ClaimLink(
        claim_kind=claim_kind,
        text=text,
        evidence_id=record.evidence_id,
        evidence_class=record.evidence_class,
    )
