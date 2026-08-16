"""Content-addressed artifacts: dedupe the bytes, keep every revision.

`ARTIFACT-IDEMPOTENCE-001`, `IDEMPOTENCE-SEQUENCING-001`, `MIGRATION-GROWTH-001`.

Today a cache hit restamps the time and writes a new report path, so repeating
an invocation that changed nothing grows the store and produces a new identity
for the same content. Repeated compatibility repairs compound that. The
sequencing constraint is the reason this lands before any broad schema or
profile bump: a migration that rewrites artifacts without content identity
first has no way to notice that it just produced the bytes it already had.

The model here separates two things that were one.

* A **content object** is bytes, addressed by their digest. Identical bytes are
  one object no matter how many revisions point at it.
* A **revision** is an event: an invocation completed, against these
  dependencies, producing that content. Revisions are never deduplicated and
  never removed, because they are the provenance record - Joshua's requirement
  is that every historical revision stays resolvable, and a revision that
  vanished because its bytes were seen before would take its dependency
  history with it.

Idempotence is then decidable rather than approximated: an invocation whose
dependency digest and content are both unchanged is *the same event*, and
returns the existing revision instead of stamping a new one. A dependency that
changed produces exactly one new revision, even when the resulting bytes are
identical, because what the artifact was computed from is part of what it
means.

This module is the semantics and the specification. It performs no file access
and holds no lock; the production transaction, the on-disk layout and the
migration executor are the consumer phase's.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Version of the artifact identity contract.
ARTIFACT_IDENTITY_VERSION = 1

#: Migration step outcomes, recorded in the journal.
STEP_PENDING = "pending"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"

MIGRATION_STEP_STATES = (STEP_PENDING, STEP_COMPLETED, STEP_FAILED)

__all__ = [
    "ARTIFACT_IDENTITY_VERSION",
    "MIGRATION_STEP_STATES",
    "ArtifactIdentityError",
    "ArtifactStore",
    "ContentObject",
    "MigrationPlan",
    "Revision",
    "content_address",
    "dependency_digest",
]


class ArtifactIdentityError(ValueError):
    """An artifact operation violated the identity contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def content_address(payload: bytes) -> str:
    """Address bytes by their digest.

    Takes bytes rather than an object on purpose. Two structurally equal
    mappings can serialise differently, and a store that addressed the object
    would then hold two objects for one artifact - the growth this closes.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise ArtifactIdentityError("content must be addressed by its bytes")
    return "sha256:" + hashlib.sha256(bytes(payload)).hexdigest()


def dependency_digest(dependencies: Mapping[str, Any]) -> str:
    """Digest of everything an artifact was computed from.

    Includes the contract version, so a producer change invalidates derived
    artifacts even when every named input is byte-identical. Without that, a
    schema bump would silently reuse artifacts computed under the old meaning.
    """
    if not isinstance(dependencies, Mapping):
        raise ArtifactIdentityError("dependencies must be a mapping")
    return "dep:" + hashlib.sha256(
        _canonical({"version": ARTIFACT_IDENTITY_VERSION, "inputs": dict(dependencies)})
    ).hexdigest()[:32]


@dataclass(frozen=True)
class ContentObject:
    """Stored bytes, and the count of revisions that point at them."""

    address: str
    size_bytes: int
    reference_count: int


@dataclass(frozen=True)
class Revision:
    """One completed invocation. Immutable, and never removed."""

    revision_id: str
    subject: str
    dependency_digest: str
    content_address: str
    sequence: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "content_address": self.content_address,
            "dependency_digest": self.dependency_digest,
            "identity_version": ARTIFACT_IDENTITY_VERSION,
            "revision_id": self.revision_id,
            "sequence": self.sequence,
            "subject": self.subject,
        }


class ArtifactStore:
    """The dedupe-and-retain model, as an in-memory reference implementation.

    Deliberately not a persistence layer. It exists so the rules can be stated
    executably and attacked by tests, and so the consumer that does own
    persistence has something to conform to rather than a paragraph to
    interpret.
    """

    def __init__(self) -> None:
        self._content: dict[str, bytes] = {}
        self._revisions: dict[str, Revision] = {}
        self._order: list[str] = []

    # -- writing ---------------------------------------------------------

    def record(
        self,
        *,
        subject: str,
        dependencies: Mapping[str, Any],
        payload: bytes,
    ) -> tuple[Revision, bool]:
        """Record one completed invocation.

        Returns the revision and whether it is new. The three cases are
        distinct and all three matter:

        * same dependencies, same bytes - the same event. The existing revision
          is returned untouched, so a repeated invocation neither restamps nor
          grows anything.
        * changed dependencies, same bytes - a new revision pointing at the
          existing content object. Provenance is preserved; the bytes are not
          duplicated.
        * any change in bytes - a new revision and a new content object.
        """
        if not subject:
            raise ArtifactIdentityError("an artifact needs a subject")
        digest = dependency_digest(dependencies)
        address = content_address(payload)

        for revision in self._iter_revisions(subject):
            if revision.dependency_digest == digest and revision.content_address == address:
                return revision, False

        self._content.setdefault(address, bytes(payload))
        sequence = len(self._order)
        revision_id = "rev-" + hashlib.sha256(
            _canonical(
                {
                    "version": ARTIFACT_IDENTITY_VERSION,
                    "subject": subject,
                    "dependency_digest": digest,
                    "content_address": address,
                    "sequence": sequence,
                }
            )
        ).hexdigest()[:24]
        revision = Revision(
            revision_id=revision_id,
            subject=subject,
            dependency_digest=digest,
            content_address=address,
            sequence=sequence,
        )
        self._revisions[revision_id] = revision
        self._order.append(revision_id)
        return revision, True

    # -- reading ---------------------------------------------------------

    def _iter_revisions(self, subject: str) -> Iterable[Revision]:
        for revision_id in self._order:
            revision = self._revisions[revision_id]
            if revision.subject == subject:
                yield revision

    def resolve(self, revision_id: str) -> Revision:
        """Resolve any revision ever recorded. There is no pruning path."""
        revision = self._revisions.get(revision_id)
        if revision is None:
            raise ArtifactIdentityError(f"unknown revision: {revision_id!r}")
        return revision

    def read(self, revision_id: str) -> bytes:
        return self._content[self.resolve(revision_id).content_address]

    def revisions(self, subject: str | None = None) -> list[Revision]:
        if subject is None:
            return [self._revisions[key] for key in self._order]
        return list(self._iter_revisions(subject))

    def content_objects(self) -> list[ContentObject]:
        counts: dict[str, int] = {}
        for revision_id in self._order:
            address = self._revisions[revision_id].content_address
            counts[address] = counts.get(address, 0) + 1
        return [
            ContentObject(
                address=address,
                size_bytes=len(self._content[address]),
                reference_count=counts.get(address, 0),
            )
            for address in sorted(self._content)
        ]

    def latest(self, subject: str) -> Revision | None:
        found = None
        for revision in self._iter_revisions(subject):
            found = revision
        return found

    # -- invariants ------------------------------------------------------

    def orphans(self) -> list[str]:
        """Content objects no revision points at.

        There should never be any. A migration that writes content before
        committing its revision, and then fails, produces one - which is why
        the check exists rather than being assumed.
        """
        referenced = {self._revisions[key].content_address for key in self._order}
        return sorted(set(self._content) - referenced)

    def dangling(self) -> list[str]:
        """Revisions whose content is missing. Also always empty."""
        return sorted(
            revision.revision_id
            for revision in self._revisions.values()
            if revision.content_address not in self._content
        )


@dataclass
class MigrationPlan:
    """A resumable migration over an explicit list of revisions.

    The journal is the whole mechanism. A migration that recorded nothing would
    have to redo everything after an interruption, and redoing a step that
    already wrote is how a compatibility repair compounds storage growth.
    Steps are idempotent here anyway - re-running one produces the same content
    address - but a journal turns "harmless to redo" into "not redone", which
    is what makes the cost bounded.
    """

    revision_ids: tuple[str, ...]
    journal: dict[str, str] = field(default_factory=dict)

    def pending(self) -> list[str]:
        return [
            revision_id
            for revision_id in self.revision_ids
            if self.journal.get(revision_id) != STEP_COMPLETED
        ]

    def mark(self, revision_id: str, state: str) -> None:
        if revision_id not in self.revision_ids:
            raise ArtifactIdentityError(f"{revision_id!r} is not in this plan")
        if state not in MIGRATION_STEP_STATES:
            raise ArtifactIdentityError(f"unknown migration step state: {state!r}")
        if self.journal.get(revision_id) == STEP_COMPLETED and state != STEP_COMPLETED:
            # A completed step cannot be walked back. Allowing it would let a
            # later failure re-open work whose output is already referenced.
            raise ArtifactIdentityError("a completed migration step cannot be reopened")
        self.journal[revision_id] = state

    @property
    def complete(self) -> bool:
        return not self.pending()

    def to_payload(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "identity_version": ARTIFACT_IDENTITY_VERSION,
            "journal": dict(self.journal),
            "pending": self.pending(),
            "revision_ids": list(self.revision_ids),
        }
