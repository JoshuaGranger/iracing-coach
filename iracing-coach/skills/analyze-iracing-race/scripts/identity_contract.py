"""The four request identities and the rule that decides publication.

`REQUEST-IDENTITY-001`, `IDENTITY-SPLIT-001`, `ANALYSIS-ORDER-001`,
`PLAN-STALE-001`, `TUNE-STALE-001`. This module is the producer authority: it
declares the fields of each identity and the predicates that decide whether a
finished result may publish and whether it may be cached. Codex implements the
consumer side - immutable requests, workflow epochs, staged results and guarded
commits - against these declarations rather than restating them, because a
restated rule is a rule that can drift between the two languages.

The split is the correction, and it is worth stating plainly because collapsing
it is the tempting simplification.

* An **operation key** names the work. Two requests with the same operation key
  are the same computation and may legitimately share one execution. It must
  therefore contain nothing that changes after dispatch - no epoch, no
  surface, no clock - or identical work would never join.
* A **publication key** names the place a result is allowed to land: a surface
  together with its epoch. Publication is permitted only while the key
  captured at dispatch still equals the surface's current key. This is what
  stops a slow analysis of race A from committing after the user has already
  opened race B.
* A **content identity** names what the result is *about*. Caching is keyed on
  it. It is deliberately independent of both keys above, because work that
  arrives too late to display is not thereby wrong: it may still be cached, but
  only under the content identity it actually computed.
* A **result provenance** records how the result was produced, so a displayed
  value can be traced back to the exact request and producer versions.

A single "request id" cannot express those four different questions, and the
failure mode of trying is silent: the result looks current because it came from
a request that was current when it started.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Version of the identity contract. Any change to which fields participate in
#: a key changes what "the same request" means, so consumers pin it.
IDENTITY_CONTRACT_VERSION = 1

#: Fields that constitute an operation key. Declared as data so the C# consumer
#: and any generated contract are produced from one list.
OPERATION_KEY_FIELDS: tuple[str, ...] = (
    "workflow",
    "selection",
    "options",
    "producer_version",
)

#: Fields that constitute a publication key.
PUBLICATION_KEY_FIELDS: tuple[str, ...] = ("surface", "epoch")

#: Fields that constitute a result provenance record.
RESULT_PROVENANCE_FIELDS: tuple[str, ...] = (
    "operation_key",
    "content_identity",
    "producer_version",
    "completed_sequence",
)

#: Reasons a staged result may be refused publication. Typed so a consumer
#: renders a specific state instead of a generic failure, and so a refusal can
#: never be confused with an error.
REFUSAL_SUPERSEDED = "superseded-by-newer-epoch"
REFUSAL_DIFFERENT_SURFACE = "different-surface"
REFUSAL_CANCELED = "canceled"
REFUSAL_FAULTED = "faulted"
REFUSAL_INCOMPLETE_CONTENT = "incomplete-content-identity"
#: The staged result and the provenance travelling with it describe different
#: work, different content, or different producers. Neither publication nor
#: caching is permitted, because there is no answer to "what is this a result
#: of" - and a cache written under the wrong content identity is how one race's
#: analysis is later served for another's.
REFUSAL_PROVENANCE_MISMATCH = "provenance-does-not-match-staged-result"
#: An identity input was not of the type the contract requires. Distinct from a
#: mismatch: nothing was compared, because one side was not a valid identity.
REFUSAL_MALFORMED_IDENTITY = "malformed-identity"

PUBLICATION_REFUSALS = (
    REFUSAL_SUPERSEDED,
    REFUSAL_DIFFERENT_SURFACE,
    REFUSAL_CANCELED,
    REFUSAL_FAULTED,
    REFUSAL_INCOMPLETE_CONTENT,
    REFUSAL_PROVENANCE_MISMATCH,
    REFUSAL_MALFORMED_IDENTITY,
)


class IdentityContractError(ValueError):
    """An identity could not be formed from the supplied fields."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(kind: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical(
            {"version": IDENTITY_CONTRACT_VERSION, "kind": kind, "payload": dict(payload)}
        ).encode("utf-8")
    ).hexdigest()[:32]


def _require(payload: Mapping[str, Any], fields: tuple[str, ...], kind: str) -> dict[str, Any]:
    """Fail closed on a missing field rather than hashing a hole.

    Defaulting an absent field would make two materially different requests
    share a key, which is the exact class of collision these identities exist
    to prevent.
    """
    missing = [name for name in fields if name not in payload]
    if missing:
        raise IdentityContractError(f"{kind} requires {missing}")
    return {name: payload[name] for name in fields}


@dataclass(frozen=True)
class OperationKey:
    """Names the work, and nothing about when or where it was asked for."""

    workflow: str
    selection: Mapping[str, Any]
    options: Mapping[str, Any]
    producer_version: str

    @property
    def value(self) -> str:
        return _digest(
            "operation",
            {
                "workflow": self.workflow,
                "selection": dict(self.selection),
                "options": dict(self.options),
                "producer_version": self.producer_version,
            },
        )

    @staticmethod
    def from_mapping(payload: Mapping[str, Any]) -> "OperationKey":
        fields = _require(payload, OPERATION_KEY_FIELDS, "operation key")
        return OperationKey(
            workflow=str(fields["workflow"]),
            selection=dict(fields["selection"] or {}),
            options=dict(fields["options"] or {}),
            producer_version=str(fields["producer_version"]),
        )


@dataclass(frozen=True)
class PublicationKey:
    """Names where a result may land, and for how long that stays true.

    The epoch is monotonic per surface. Every navigation, selection change, or
    explicit refresh advances it, which is what makes "still current" a
    decidable question at commit time rather than a guess.
    """

    surface: str
    epoch: int

    def __post_init__(self) -> None:
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int):
            raise IdentityContractError("publication epoch must be a JSON integer")
        if self.epoch < 0:
            raise IdentityContractError("publication epoch must not be negative")

    @property
    def value(self) -> str:
        return _digest("publication", {"surface": self.surface, "epoch": self.epoch})

    def advanced(self) -> "PublicationKey":
        return PublicationKey(surface=self.surface, epoch=self.epoch + 1)

    @staticmethod
    def from_mapping(payload: Mapping[str, Any]) -> "PublicationKey":
        fields = _require(payload, PUBLICATION_KEY_FIELDS, "publication key")
        return PublicationKey(surface=str(fields["surface"]), epoch=fields["epoch"])


@dataclass(frozen=True)
class ContentIdentity:
    """Names what a result is about, independent of who asked or when.

    `complete` is not decoration. A result computed from partial or unverified
    inputs has no valid content identity, and caching it would let a later
    request reuse something that was never entitled to be reused. The
    incomplete case is therefore representable rather than being signalled by
    an empty digest.
    """

    digest: str
    complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or not self.digest:
            raise IdentityContractError("content identity requires a non-empty digest")
        if not isinstance(self.complete, bool):
            # Exact, not truthy. `complete="false"` is a non-empty string, so a
            # truthiness test reads it as complete and publishes a result
            # computed from inputs that were never verified.
            raise IdentityContractError(
                f"content completeness must be a JSON boolean, got {self.complete!r}"
            )

    @property
    def value(self) -> str:
        return self.digest


@dataclass(frozen=True)
class ResultProvenance:
    """How a result was produced, carried with it so a display can be traced."""

    operation_key: str
    content_identity: str
    producer_version: str
    completed_sequence: int

    def __post_init__(self) -> None:
        for name in ("operation_key", "content_identity", "producer_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise IdentityContractError(f"provenance {name} must be a non-empty string")
        if isinstance(self.completed_sequence, bool) or not isinstance(
            self.completed_sequence, int
        ):
            raise IdentityContractError("completed sequence must be a JSON integer")
        if self.completed_sequence < 0:
            raise IdentityContractError("completed sequence must not be negative")

    def to_payload(self) -> dict[str, Any]:
        return {
            "operation_key": self.operation_key,
            "content_identity": self.content_identity,
            "producer_version": self.producer_version,
            "completed_sequence": self.completed_sequence,
        }


@dataclass(frozen=True)
class StagedResult:
    """A finished result that has not yet been allowed to publish.

    Staging is the whole mechanism. A result that writes directly into shared
    state has already published by the time anyone can check whether it should
    have.
    """

    operation: OperationKey
    publication: PublicationKey
    content: ContentIdentity
    provenance: ResultProvenance
    canceled: bool = False
    faulted: bool = False
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationDecision:
    """The outcome of asking whether a staged result may publish."""

    may_publish: bool
    may_cache: bool
    refusal: str | None = None


def _structural_refusal(staged: StagedResult, current: PublicationKey) -> str | None:
    """Refuse a staged result whose own identities do not agree, or are not identities.

    The provenance is what a displayed value is traced back through, so it is
    not decoration travelling beside the result - it is a claim about which
    operation produced which content, and an unchecked claim is worth nothing.
    Without this, a `StagedResult` for one race paired with provenance naming
    another published and cached happily: the epoch matched, the surface
    matched, and nothing ever asked whether the four identities described the
    same piece of work.

    Both answers are withheld together. A mismatch gives no reason to trust the
    content identity either, and caching under an identity that may belong to
    different content is the durable version of the same defect.
    """
    if not isinstance(staged, StagedResult) or not isinstance(current, PublicationKey):
        return REFUSAL_MALFORMED_IDENTITY
    if not isinstance(staged.faulted, bool) or not isinstance(staged.canceled, bool):
        return REFUSAL_MALFORMED_IDENTITY
    if not isinstance(staged.operation, OperationKey):
        return REFUSAL_MALFORMED_IDENTITY
    if not isinstance(staged.publication, PublicationKey):
        return REFUSAL_MALFORMED_IDENTITY
    if not isinstance(staged.content, ContentIdentity):
        return REFUSAL_MALFORMED_IDENTITY
    if not isinstance(staged.provenance, ResultProvenance):
        return REFUSAL_MALFORMED_IDENTITY
    if staged.provenance.operation_key != staged.operation.value:
        return REFUSAL_PROVENANCE_MISMATCH
    if staged.provenance.content_identity != staged.content.value:
        return REFUSAL_PROVENANCE_MISMATCH
    if staged.provenance.producer_version != staged.operation.producer_version:
        return REFUSAL_PROVENANCE_MISMATCH
    return None


def decide(staged: StagedResult, current: PublicationKey) -> PublicationDecision:
    """Decide publication and caching for one staged result.

    The two answers are independent, and that independence is the point.
    Work that finished too late to display is not wrong; refusing to cache it
    would discard a correct computation. Work whose content identity is
    incomplete must be refused for both, because there is no key under which it
    could be safely reused.

    Order matters here: cancellation and faults are checked before staleness so
    a canceled operation is never reported as merely superseded, which would
    tell a consumer to retry work the user deliberately stopped. Structural
    validity is checked before all of it, because a decision about a result
    whose own identities disagree is not a decision - it is a guess about which
    half to believe.
    """
    problem = _structural_refusal(staged, current)
    if problem is not None:
        return PublicationDecision(False, False, problem)
    if staged.faulted:
        return PublicationDecision(False, False, REFUSAL_FAULTED)
    if staged.canceled:
        return PublicationDecision(False, False, REFUSAL_CANCELED)
    if not staged.content.complete:
        return PublicationDecision(False, False, REFUSAL_INCOMPLETE_CONTENT)
    if staged.publication.surface != current.surface:
        return PublicationDecision(False, True, REFUSAL_DIFFERENT_SURFACE)
    if staged.publication.epoch != current.epoch:
        return PublicationDecision(False, True, REFUSAL_SUPERSEDED)
    return PublicationDecision(True, True, None)


def may_join(first: OperationKey, second: OperationKey) -> bool:
    """Whether two requests describe the same work and may share one execution.

    Deliberately independent of publication: two surfaces at different epochs
    asking for the same analysis are the same computation, and joining them is
    the behavior that makes the split worth having.
    """
    return first.value == second.value
