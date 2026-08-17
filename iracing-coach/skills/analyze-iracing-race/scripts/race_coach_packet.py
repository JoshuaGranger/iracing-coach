"""The numeric packet the Race Coach reasons over, and what it may not be.

`AI-PACKET-DEPTH-001`, `AI-EVIDENCE-LINK-001`, and the producer half of
`AI-COACH-CAPABILITY-001`.

Joshua's direction is visual-first and numeric, and the rejected alternatives
are explicit: a prose-only Race Coach is not acceptable, and neither is a broker
that completes without saying anything measured. The packet is what makes the
difference structural instead of stylistic.

A section of this packet cannot exist without numbers. :class:`PacketSection`
refuses to hold text alone, so "the coach said something reasonable" is not a
representable state - if there is nothing measured to say, the correct output is
an unavailable section carrying the reason, which :func:`unavailable_section`
builds and which is a different thing from silence.

Every section also carries a :class:`~evidence_records.ClaimLink`, so each
number is traceable to the record that supports it. That is `AI-EVIDENCE-LINK-001`,
and it is enforced by construction rather than by review: there is no way to add
a section without passing the link through :func:`evidence_records.link`, which
refuses bindings the record cannot bear.

Finally the packet is identified by its numbers. :attr:`CoachPacket.packet_id`
digests the values and the evidence ids and deliberately excludes the prose, so
two packets whose sentences match but whose measurements differ are different
packets. The closure asks for exactly that case, because it is how a cached or
replayed answer gets attached to the wrong race.

The windows are part of the contract too. A number without the span it was
measured over invites the reader to apply it to the whole race, which is the
quiet way a corner-window observation becomes a claim about a stint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from evidence_records import (
    CLAIM_UNAVAILABLE,
    ClaimLink,
    EvidenceRecord,
    link,
    unavailable_record,
)

#: Version of the coach packet contract.
COACH_PACKET_VERSION = 1

WINDOW_LAP = "lap"
WINDOW_RUN = "run"
WINDOW_CORNER = "corner"
WINDOW_SESSION = "session"

#: What a window is measured in. A corner window and a session window are not
#: interchangeable even when the numbers look alike, which is why the unit is
#: carried rather than inferred.
WINDOW_KINDS = (WINDOW_LAP, WINDOW_RUN, WINDOW_CORNER, WINDOW_SESSION)

SUBJECT_LAP_SERIES = "lap_series"
SUBJECT_RUN_SERIES = "run_series"
SUBJECT_CORNER_WINDOW = "corner_window"
SUBJECT_PLAN = "plan"
SUBJECT_TIRE = "tire"
SUBJECT_SETUP = "setup"
SUBJECT_PROGRESS = "progress"

#: The subjects a packet may speak about. The clarification says these are
#: included "only when supported", so the list is what may appear rather than
#: what must: a packet with three supported sections is complete.
PACKET_SUBJECTS = (
    SUBJECT_LAP_SERIES,
    SUBJECT_RUN_SERIES,
    SUBJECT_CORNER_WINDOW,
    SUBJECT_PLAN,
    SUBJECT_TIRE,
    SUBJECT_SETUP,
    SUBJECT_PROGRESS,
)

__all__ = [
    "COACH_PACKET_VERSION",
    "CoachPacket",
    "CoachPacketError",
    "EvidenceWindow",
    "NumericSeries",
    "PACKET_SUBJECTS",
    "PacketSection",
    "WINDOW_KINDS",
    "build_packet",
    "supported_section",
    "unavailable_section",
]


class CoachPacketError(ValueError):
    """A packet violated the depth, evidence or numeric contract."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoachPacketError(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise CoachPacketError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class EvidenceWindow:
    """The span a number was measured over, in units that say which span."""

    kind: str
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.kind not in WINDOW_KINDS:
            raise CoachPacketError(f"unknown window kind: {self.kind!r}")
        start = _finite(self.start, "window start")
        end = _finite(self.end, "window end")
        if end < start:
            raise CoachPacketError("a window cannot end before it starts")

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "start": float(self.start), "end": float(self.end)}


@dataclass(frozen=True)
class NumericSeries:
    """Named, united numbers with the window they were measured over."""

    name: str
    unit: str
    values: tuple[float, ...]
    window: EvidenceWindow

    def __post_init__(self) -> None:
        if not self.name:
            raise CoachPacketError("a series needs a name")
        if not self.unit:
            # A bare number is the precision defect in miniature: the reader
            # supplies a unit, and half the time supplies the wrong one.
            raise CoachPacketError(f"series {self.name!r} must state its unit")
        if not self.values:
            raise CoachPacketError(f"series {self.name!r} carries no values")
        for index, value in enumerate(self.values):
            _finite(value, f"{self.name}[{index}]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "values": [float(value) for value in self.values],
            "window": self.window.to_payload(),
        }


@dataclass(frozen=True)
class PacketSection:
    """One subject, its numbers, and the claim those numbers support.

    An available section must carry at least one series. That single rule is
    what makes a prose-only coach unrepresentable rather than merely
    discouraged.
    """

    subject: str
    claim: ClaimLink
    series: tuple[NumericSeries, ...] = ()
    available: bool = True

    def __post_init__(self) -> None:
        if self.subject not in PACKET_SUBJECTS:
            raise CoachPacketError(f"unknown packet subject: {self.subject!r}")
        if not isinstance(self.claim, ClaimLink):
            raise CoachPacketError("a section must carry a linked claim")
        if self.available and not self.series:
            raise CoachPacketError(
                f"section {self.subject!r} states a claim with no numbers behind it"
            )
        if not self.available:
            if self.series:
                raise CoachPacketError(
                    "an unavailable section cannot carry measured series"
                )
            if self.claim.claim_kind != CLAIM_UNAVAILABLE:
                raise CoachPacketError(
                    "an unavailable section must carry an unavailable claim"
                )

    def to_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "available": self.available,
            "claim": {
                "kind": self.claim.claim_kind,
                "text": self.claim.text,
                "evidence_id": self.claim.evidence_id,
                "evidence_class": self.claim.evidence_class,
            },
            "series": [item.to_payload() for item in self.series],
        }


def supported_section(
    subject: str, claim_kind: str, text: str, record: EvidenceRecord, series: Sequence[NumericSeries]
) -> PacketSection:
    """Build a section, refusing the binding if the record cannot bear it."""
    if not series:
        raise CoachPacketError(
            f"section {subject!r} needs at least one series; use "
            "unavailable_section when nothing was measured"
        )
    return PacketSection(
        subject=subject,
        claim=link(claim_kind, text, record),
        series=tuple(series),
    )


def unavailable_section(subject: str, source: str, reason: str) -> PacketSection:
    """The honest section for a subject nothing measured.

    Present so that "we have nothing for this" is a thing the packet says,
    rather than a subject that silently disappears and lets the reader assume
    it was fine.
    """
    record = unavailable_record(subject=subject, source=source, reason=reason)
    return PacketSection(
        subject=subject,
        claim=link(CLAIM_UNAVAILABLE, reason, record),
        series=(),
        available=False,
    )


@dataclass(frozen=True)
class CoachPacket:
    """Everything the coach is allowed to reason from, identified by its numbers."""

    sections: tuple[PacketSection, ...]

    def __post_init__(self) -> None:
        if not self.sections:
            raise CoachPacketError("a packet needs at least one section")
        subjects = [section.subject for section in self.sections]
        if len(set(subjects)) != len(subjects):
            raise CoachPacketError("a packet cannot state one subject twice")

    @property
    def supported_subjects(self) -> tuple[str, ...]:
        return tuple(
            sorted(section.subject for section in self.sections if section.available)
        )

    @property
    def packet_id(self) -> str:
        """Identity derived from measurements and evidence, never from prose.

        The claim text is excluded on purpose. Two packets whose sentences are
        identical but whose numbers differ must not share an id, because that
        collision is how one race's answer gets served for another's.
        """
        material = [
            {
                "subject": section.subject,
                "available": section.available,
                "evidence_id": section.claim.evidence_id,
                "series": [
                    {
                        "name": item.name,
                        "unit": item.unit,
                        "values": [float(value) for value in item.values],
                        "window": item.window.to_payload(),
                    }
                    for item in section.series
                ],
            }
            for section in sorted(self.sections, key=lambda item: item.subject)
        ]
        digest = hashlib.sha256(
            json.dumps(
                {"version": COACH_PACKET_VERSION, "sections": material},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return "pk-" + digest[:24]

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": COACH_PACKET_VERSION,
            "packet_id": self.packet_id,
            "supported_subjects": list(self.supported_subjects),
            "sections": [section.to_payload() for section in self.sections],
        }


def build_packet(sections: Iterable[PacketSection]) -> CoachPacket:
    """Assemble a packet in a stable subject order."""
    ordered = sorted(sections, key=lambda section: PACKET_SUBJECTS.index(section.subject))
    return CoachPacket(sections=tuple(ordered))
