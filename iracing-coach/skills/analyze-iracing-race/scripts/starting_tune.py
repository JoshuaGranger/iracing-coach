"""What a Starting Tune candidate can actually do, decided by purpose and shape.

`ST-QUAL-ONLY-001`, `START-TUNE-SOURCE-001`. Two questions were being answered
by the same absent-or-present test, and they are not the same question.

* **Which purpose was asked for?** A week's setups may contain a qualifying
  file and no race file. That is not an empty result; it is exact evidence for
  a different purpose than the one requested. Collapsing it to "nothing found"
  discards a real, correctly identified setup and sends the user to a donor
  track for advice they already have on disk.
* **What shape is the source?** iRacing's `.sto` is the loadable artifact. Its
  companion `.htm` export is readable, and reading it is how the backend knows
  what the setup contains - but it cannot be loaded into the simulator. A
  candidate with HTML and no `.sto` is therefore full evidence and zero load
  capability, and offering "Load the source setup" for one promises something
  the file cannot do.

This module decides both, together, as one typed capability. The requested
purpose is part of the identity, so a result computed for qualifying can never
publish onto a race request - the staleness that :mod:`identity_contract`
exists to prevent, applied to the field that actually varies here.

Future automated simulator setup loading and `.sto` generation are explicitly
outside this iteration. Nothing here writes to, renames, or modifies a source
setup file; `SOURCE_FILES_ARE_READ_ONLY` records that as a checkable claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Version of the Starting Tune capability contract.
STARTING_TUNE_CONTRACT_VERSION = 1

PURPOSE_QUALIFYING = "qualifying"
PURPOSE_RACE = "race"
PURPOSE_ENDURANCE = "endurance"

#: Every purpose a request may ask for. Matches the role vocabulary the setup
#: catalog already infers from filenames, so the two cannot drift.
PURPOSES = (PURPOSE_QUALIFYING, PURPOSE_RACE, PURPOSE_ENDURANCE)

SHAPE_PAIRED = "paired"
SHAPE_STO_ONLY = "sto_only"
SHAPE_HTML_ONLY = "html_only"
SHAPE_AMBIGUOUS = "ambiguous"
SHAPE_ABSENT = "absent"

#: The source shapes a catalog entry can have. `ambiguous` means more than one
#: file of a kind shares the stem, so no single artifact is identified.
SOURCE_SHAPES = (
    SHAPE_PAIRED,
    SHAPE_STO_ONLY,
    SHAPE_HTML_ONLY,
    SHAPE_AMBIGUOUS,
    SHAPE_ABSENT,
)

#: Shapes that contain exactly one loadable `.sto`. Load capability is decided
#: from this set and from validation, never from the presence of a candidate.
SHAPES_WITH_ONE_STO = (SHAPE_PAIRED, SHAPE_STO_ONLY)

#: Shapes whose HTML the backend can read for evidence.
SHAPES_WITH_READABLE_HTML = (SHAPE_PAIRED, SHAPE_HTML_ONLY)

MATCH_EXACT = "exact-purpose"
MATCH_OTHER_PURPOSE = "other-purpose-only"
MATCH_NONE = "no-candidate"

PURPOSE_MATCHES = (MATCH_EXACT, MATCH_OTHER_PURPOSE, MATCH_NONE)

EVIDENCE_PARAMETERS = "parameters-readable"
EVIDENCE_IDENTITY_ONLY = "identity-only"
EVIDENCE_NONE = "none"

EVIDENCE_LEVELS = (EVIDENCE_PARAMETERS, EVIDENCE_IDENTITY_ONLY, EVIDENCE_NONE)

REASON_NO_CANDIDATE = "no-candidate-for-any-purpose"
REASON_PURPOSE_SUBSTITUTED = "candidate-is-for-another-purpose"
REASON_NO_STO = "no-loadable-sto-in-source"
REASON_AMBIGUOUS_SOURCE = "several-files-share-this-stem"
REASON_STO_NOT_VALIDATED = "sto-present-but-not-validated"
REASON_HTML_UNPARSED = "html-present-but-not-parsed"

CAPABILITY_REASONS = (
    REASON_NO_CANDIDATE,
    REASON_PURPOSE_SUBSTITUTED,
    REASON_NO_STO,
    REASON_AMBIGUOUS_SOURCE,
    REASON_STO_NOT_VALIDATED,
    REASON_HTML_UNPARSED,
)

#: Reasons that withhold Load. Deliberately not "any reason at all":
#: `REASON_HTML_UNPARSED` limits what the backend can *show* about a setup and
#: says nothing about whether the `.sto` beside it loads. Blocking on it would
#: make a paired candidate stricter than a bare `.sto`, which carries strictly
#: less information and is permitted - an inconsistency, not a safeguard.
LOAD_BLOCKING_REASONS = (
    REASON_NO_CANDIDATE,
    REASON_PURPOSE_SUBSTITUTED,
    REASON_NO_STO,
    REASON_AMBIGUOUS_SOURCE,
    REASON_STO_NOT_VALIDATED,
)

#: A claim, not a comment. The catalog opens sources read-only and this module
#: performs no file access at all; a test asserts both.
SOURCE_FILES_ARE_READ_ONLY = True

__all__ = [
    "CAPABILITY_REASONS",
    "EVIDENCE_LEVELS",
    "LOAD_BLOCKING_REASONS",
    "PURPOSES",
    "PURPOSE_MATCHES",
    "SHAPES_WITH_ONE_STO",
    "SHAPES_WITH_READABLE_HTML",
    "SOURCE_FILES_ARE_READ_ONLY",
    "SOURCE_SHAPES",
    "STARTING_TUNE_CONTRACT_VERSION",
    "StartingTuneCapability",
    "StartingTuneError",
    "capability",
    "capability_matrix",
    "source_shape",
]


class StartingTuneError(ValueError):
    """A capability could not be decided from the supplied description."""


@dataclass(frozen=True)
class StartingTuneCapability:
    """What may be done with one Starting Tune candidate, and why.

    `load_permitted` is the field that matters most, and it is deliberately
    narrow: it is true only when a single `.sto` was found *and* validated. An
    entry with readable HTML and rich parsed parameters still returns false,
    because the affordance it would enable does not work.
    """

    requested_purpose: str
    resolved_purpose: str | None
    purpose_match: str
    source_shape: str
    load_permitted: bool
    evidence_level: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.requested_purpose not in PURPOSES:
            raise StartingTuneError(f"unknown requested purpose: {self.requested_purpose!r}")
        if self.resolved_purpose is not None and self.resolved_purpose not in PURPOSES:
            raise StartingTuneError(f"unknown resolved purpose: {self.resolved_purpose!r}")
        if self.purpose_match not in PURPOSE_MATCHES:
            raise StartingTuneError(f"unknown purpose match: {self.purpose_match!r}")
        if self.source_shape not in SOURCE_SHAPES:
            raise StartingTuneError(f"unknown source shape: {self.source_shape!r}")
        if self.evidence_level not in EVIDENCE_LEVELS:
            raise StartingTuneError(f"unknown evidence level: {self.evidence_level!r}")
        unknown = [reason for reason in self.reasons if reason not in CAPABILITY_REASONS]
        if unknown:
            raise StartingTuneError(f"undeclared reason(s): {unknown}")
        if self.load_permitted and self.source_shape not in SHAPES_WITH_ONE_STO:
            raise StartingTuneError(
                "load cannot be permitted for a source shape with no single .sto"
            )
        blocking = [reason for reason in self.reasons if reason in LOAD_BLOCKING_REASONS]
        if self.load_permitted and blocking:
            raise StartingTuneError(f"load cannot be permitted alongside {blocking}")
        if self.purpose_match == MATCH_NONE and self.resolved_purpose is not None:
            raise StartingTuneError("no candidate cannot resolve to a purpose")
        if self.purpose_match == MATCH_EXACT and self.resolved_purpose != self.requested_purpose:
            raise StartingTuneError("an exact match must resolve to the requested purpose")

    @property
    def usable_as_evidence(self) -> bool:
        """Whether anything at all may be shown from this candidate.

        A candidate for another purpose is still evidence. This is the whole
        `ST-QUAL-ONLY-001` correction: an exact qualifying file, when a race
        setup was requested, is not an empty result.
        """
        return self.evidence_level != EVIDENCE_NONE

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": STARTING_TUNE_CONTRACT_VERSION,
            "evidence_level": self.evidence_level,
            "load_permitted": self.load_permitted,
            "purpose_match": self.purpose_match,
            "reasons": list(self.reasons),
            "requested_purpose": self.requested_purpose,
            "resolved_purpose": self.resolved_purpose,
            "source_files_read_only": SOURCE_FILES_ARE_READ_ONLY,
            "source_shape": self.source_shape,
            "usable_as_evidence": self.usable_as_evidence,
        }


def source_shape(entry: Mapping[str, Any] | None) -> str:
    """Classify a catalog entry's files by counting them, not by trusting a label.

    `pair_status` is recomputed here from the source lists rather than read
    from the entry. The two should agree, and where they do not the counts are
    the fact: a label can be stale, and this decision gates a Load affordance.
    """
    if entry is None:
        return SHAPE_ABSENT
    sources = entry.get("sources")
    if not isinstance(sources, Mapping):
        return SHAPE_ABSENT
    sto = sources.get("sto") or []
    html = sources.get("html") or []
    if not isinstance(sto, Sequence) or isinstance(sto, (str, bytes)):
        raise StartingTuneError("sources.sto must be a list")
    if not isinstance(html, Sequence) or isinstance(html, (str, bytes)):
        raise StartingTuneError("sources.html must be a list")
    if len(sto) > 1 or len(html) > 1:
        return SHAPE_AMBIGUOUS
    if len(sto) == 1 and len(html) == 1:
        return SHAPE_PAIRED
    if len(sto) == 1:
        return SHAPE_STO_ONLY
    if len(html) == 1:
        return SHAPE_HTML_ONLY
    return SHAPE_ABSENT


def _evidence_level(shape: str, entry: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if shape == SHAPE_ABSENT:
        return EVIDENCE_NONE, reasons
    if shape == SHAPE_AMBIGUOUS:
        reasons.append(REASON_AMBIGUOUS_SOURCE)
        return EVIDENCE_IDENTITY_ONLY, reasons
    if shape in SHAPES_WITH_READABLE_HTML:
        parsed = (entry or {}).get("parsed_html")
        if isinstance(parsed, Mapping) and parsed:
            return EVIDENCE_PARAMETERS, reasons
        reasons.append(REASON_HTML_UNPARSED)
        return EVIDENCE_IDENTITY_ONLY, reasons
    # An .sto with no HTML companion. The binary is opaque to the backend, so
    # the only evidence is the identity the filename carries.
    return EVIDENCE_IDENTITY_ONLY, reasons


def capability(
    *,
    requested_purpose: str,
    candidate: Mapping[str, Any] | None,
    candidate_purpose: str | None = None,
    sto_validated: bool = False,
) -> StartingTuneCapability:
    """Decide what one candidate permits for one requested purpose.

    `sto_validated` is a required input rather than an inferred one. Whether a
    `.sto` parses as a setup for this car is not knowable from its path, and
    defaulting it to true would make Load available for every file with the
    right extension - which is the failure this decision exists to prevent.
    """
    if requested_purpose not in PURPOSES:
        raise StartingTuneError(f"unknown requested purpose: {requested_purpose!r}")
    if candidate_purpose is not None and candidate_purpose not in PURPOSES:
        raise StartingTuneError(f"unknown candidate purpose: {candidate_purpose!r}")

    shape = source_shape(candidate)
    if candidate is None or shape == SHAPE_ABSENT:
        return StartingTuneCapability(
            requested_purpose=requested_purpose,
            resolved_purpose=None,
            purpose_match=MATCH_NONE,
            source_shape=SHAPE_ABSENT,
            load_permitted=False,
            evidence_level=EVIDENCE_NONE,
            reasons=(REASON_NO_CANDIDATE,),
        )

    match = MATCH_EXACT if candidate_purpose == requested_purpose else MATCH_OTHER_PURPOSE
    if candidate_purpose is None:
        # A candidate whose filename declares no role is not evidence for the
        # requested purpose. Treating it as exact would let an unlabelled file
        # silently answer a qualifying request.
        match = MATCH_OTHER_PURPOSE

    evidence, reasons = _evidence_level(shape, candidate)
    if match == MATCH_OTHER_PURPOSE:
        reasons.insert(0, REASON_PURPOSE_SUBSTITUTED)

    load_permitted = False
    if shape not in SHAPES_WITH_ONE_STO:
        reasons.append(REASON_NO_STO)
    elif not sto_validated:
        reasons.append(REASON_STO_NOT_VALIDATED)
    else:
        load_permitted = True

    # A substituted purpose withholds Load even when the file itself is fine:
    # loading a qualifying setup in answer to a race request is exactly the
    # confusion the purpose split exists to stop. An unparsed HTML companion
    # does not withhold it, because it constrains the evidence rather than the
    # artifact.
    if load_permitted and any(reason in LOAD_BLOCKING_REASONS for reason in reasons):
        load_permitted = False

    return StartingTuneCapability(
        requested_purpose=requested_purpose,
        resolved_purpose=candidate_purpose,
        purpose_match=match,
        source_shape=shape,
        load_permitted=load_permitted,
        evidence_level=evidence,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _entry_for(shape: str, *, parsed: bool) -> Mapping[str, Any] | None:
    """Build the minimal catalog-shaped entry for a matrix cell."""
    if shape == SHAPE_ABSENT:
        return None
    counts = {
        SHAPE_PAIRED: (1, 1),
        SHAPE_STO_ONLY: (1, 0),
        SHAPE_HTML_ONLY: (0, 1),
        SHAPE_AMBIGUOUS: (2, 1),
    }[shape]
    entry: dict[str, Any] = {
        "sources": {
            "sto": [{"relative_path": f"s{index}.sto"} for index in range(counts[0])],
            "html": [{"relative_path": f"h{index}.htm"} for index in range(counts[1])],
        }
    }
    if parsed:
        entry["parsed_html"] = {"identity": {"filename": {}}, "fields": {"a": 1}}
    return entry


def capability_matrix() -> list[dict[str, Any]]:
    """The full requested-purpose x candidate-purpose x source-shape table.

    Rendered as data so the C# consumer maps a table rather than reimplementing
    the decision, and so a change to any rule shows up as a changed cell in
    review rather than as a subtle behavioural difference in one branch.
    """
    rows: list[dict[str, Any]] = []
    for requested in PURPOSES:
        for candidate_purpose in (*PURPOSES, None):
            for shape in SOURCE_SHAPES:
                for parsed in (True, False):
                    for validated in (True, False):
                        entry = _entry_for(shape, parsed=parsed)
                        decided = capability(
                            requested_purpose=requested,
                            candidate=entry,
                            candidate_purpose=candidate_purpose,
                            sto_validated=validated,
                        )
                        rows.append(
                            {
                                "requested_purpose": requested,
                                "candidate_purpose": candidate_purpose,
                                "source_shape": shape,
                                "html_parsed": parsed,
                                "sto_validated": validated,
                                "expected": decided.to_payload(),
                            }
                        )
    return rows
