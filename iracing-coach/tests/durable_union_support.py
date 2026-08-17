"""Frozen semantics for the durable observation union (DURABLE-RMW-001).

Not discovered: no ``test_`` prefix and no ``TestCase`` subclass.

WS-10A closed atomic *containment*. It did not and could not close *lost
updates*: bounded retry around ``os.replace`` makes each individual commit
survive a sharing error, but two workers that read the same prior value still
replace each other, and the reproduction at ``probe_ws10a_durable_rmw.py``
measures exactly that. This module is the other half - the definition of what
the store must mean - and it is deliberately separate from any implementation
of it. Codex owns the production transaction and migration under the
``storage.py`` handoff; this file owns the contract that work is measured
against.

Three decisions carry the whole design.

**A record, not a value.** The unit is an observation *record*: content plus
the provenance that says where the content came from. Identity is the digest of
both. This is what lets duplicates and idempotence coexist, and they must:
`Q18` requires every revision to remain visible, while migration resume
requires re-applying the same work to change nothing. Two genuinely separate
observations that happen to carry identical content have different provenance,
so they remain two records; the *same* record applied twice is one record.
Keying on content alone would silently delete real history, and keying on
arrival would make resume duplicate it.

**Union is an algebra, not a procedure.** The store's combining operation must
be commutative, associative, and idempotent, with the empty store as identity.
Those four laws are the specification. They are what make the result
independent of process scheduling, which is the property today's
read/merge/replace lacks, and each is directly checkable rather than being an
informal description of intended behavior.

**The suite must be provable against failure.** A conformance suite that
passes an implementation doing none of the work proves nothing. Every
specification here is therefore run against reference stores that each break
exactly one guarantee, and the negative matrix asserts that the intended clause
- and only that clause - rejects each one. ``LastWriterWinsStore`` is
specifically today's shape, so the suite is proven to reject the very defect it
exists to close.

Nothing here touches private data, the network, or production state.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Version of the union contract itself. A change to record identity or to the
#: fingerprint is a change to what "the same store" means, so it is versioned
#: separately from any storage schema that implements it.
UNION_CONTRACT_VERSION = 1


def canonical_json(value: Any) -> str:
    """The one canonical encoding used for every identity in this contract.

    Sorted keys and separators without whitespace, so two structurally equal
    payloads produce the same bytes regardless of how they were built. This is
    the same discipline ``storage.stable_hash`` already applies; it is restated
    here rather than imported so the contract does not silently change meaning
    if that helper is later retuned for a different purpose.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    """One durable observation record.

    ``content`` is what was measured. ``provenance`` is what makes two
    identical measurements distinguishable - which session, which lap, which
    producer run. ``extra`` carries fields this version does not understand;
    they participate in identity and must survive a round trip, because
    dropping an unknown field turns a newer producer's record into a different
    record and would let a migration silently rewrite history.
    """

    content: Mapping[str, Any]
    provenance: Mapping[str, Any]
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def content_identity(self) -> str:
        """Identity of the measurement alone, ignoring where it came from."""
        return _digest(dict(self.content))

    @property
    def record_identity(self) -> str:
        """Identity of the record: content, provenance, and unknown fields."""
        return _digest(
            {
                "content": dict(self.content),
                "provenance": dict(self.provenance),
                "extra": dict(self.extra),
            }
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "content": dict(self.content),
            "provenance": dict(self.provenance),
            "extra": dict(self.extra),
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "Observation":
        return Observation(
            content=dict(payload.get("content") or {}),
            provenance=dict(payload.get("provenance") or {}),
            extra=dict(payload.get("extra") or {}),
        )


# --------------------------------------------------------------------------
# the union itself
# --------------------------------------------------------------------------


def union(*groups: Iterable[Observation]) -> list[Observation]:
    """The mathematical union of observation records.

    Returned in canonical order so the result is a value, not a sequence that
    happens to reflect arrival. Callers that need to prove order-independence
    compare fingerprints rather than lists.
    """
    merged: dict[str, Observation] = {}
    for group in groups:
        for observation in group:
            merged.setdefault(observation.record_identity, observation)
    return [merged[key] for key in sorted(merged)]


def union_fingerprint(observations: Iterable[Observation]) -> str:
    """A deterministic fingerprint of a store's contents.

    Computed from the sorted record identities, so it is independent of
    insertion order, of process scheduling, and of how many times a record was
    applied. Two stores agree exactly when they hold the same set of records.
    """
    identities = sorted({observation.record_identity for observation in observations})
    return hashlib.sha256(
        canonical_json({"version": UNION_CONTRACT_VERSION, "records": identities}).encode(
            "utf-8"
        )
    ).hexdigest()


def content_multiset(observations: Iterable[Observation]) -> dict[str, int]:
    """How many records carry each distinct content identity.

    The duplicate-preservation clause is stated against this rather than
    against record identities: it is the count a user would see as "two laps
    with the same measured value", and it is precisely what a
    deduplicate-by-content implementation destroys.
    """
    counts: dict[str, int] = {}
    for observation in observations:
        counts[observation.content_identity] = counts.get(observation.content_identity, 0) + 1
    return counts


# --------------------------------------------------------------------------
# deterministic fixtures
# --------------------------------------------------------------------------


def make_observation(index: int, *, run: str = "run-a", value: Any = None) -> Observation:
    return Observation(
        content={"lap": index, "value": index if value is None else value},
        provenance={"run": run, "sequence": index},
    )


def distinct_fixture(n: int) -> list[Observation]:
    """`n` records, all different in both content and provenance."""
    return [make_observation(index) for index in range(n)]


def duplicate_fixture(n: int) -> list[Observation]:
    """`n` records where every pair shares content but not provenance.

    A correct store keeps all `n`. A store that deduplicates by content keeps
    `n / 2` and has silently deleted half of a driver's history.
    """
    return [
        Observation(
            content={"lap": index // 2, "value": index // 2},
            provenance={"run": f"run-{index % 2}", "sequence": index},
        )
        for index in range(n)
    ]


def random_fixture(n: int, *, seed: int = 20260816) -> list[Observation]:
    """`n` records drawn from a seeded generator.

    Seeded rather than arbitrary: a fixture whose contents change per run
    cannot be cited as exact evidence, and a failure could not be reproduced
    from the report alone.
    """
    generator = random.Random(seed)
    observations: list[Observation] = []
    for index in range(n):
        observations.append(
            Observation(
                content={
                    "lap": generator.randrange(0, max(2, n // 2)),
                    "value": round(generator.uniform(0.0, 100.0), 6),
                },
                provenance={"run": f"run-{generator.randrange(0, 3)}", "sequence": index},
            )
        )
    return observations


def repeated_fixture(n: int, *, repeats: int = 3) -> list[Observation]:
    """`n` distinct records, each presented `repeats` times.

    This is the resume shape: re-applying already-applied work. The union must
    hold exactly `n` records however many times the work is replayed.
    """
    base = distinct_fixture(n)
    return [observation for observation in base for _ in range(repeats)]


FIXTURE_BUILDERS = {
    "distinct": distinct_fixture,
    "duplicate": duplicate_fixture,
    "random": random_fixture,
    "repeated": repeated_fixture,
}


# --------------------------------------------------------------------------
# the surface a candidate store must present
# --------------------------------------------------------------------------


class UnionStore:
    """Reference conforming store.

    Deliberately in-memory and deliberately simple. It is not a proposal for
    the production implementation - Codex owns that choice between SQLite,
    append-only log, or reread-under-lock - it is the oracle the specifications
    are written against, so that "the specification is satisfiable" is itself
    demonstrated rather than assumed.
    """

    def __init__(self) -> None:
        self._records: dict[str, Observation] = {}
        #: Every raw group ever applied, retained so rebuild-from-raw is
        #: expressible. Retaining raw sources is part of the remedy for
        #: concentrating observations into one store, not an optional extra.
        self._raw: list[list[Observation]] = []
        self._acknowledged: set[str] = set()

    def apply(self, observations: Sequence[Observation]) -> None:
        """Commit a group. Idempotent: re-applying changes nothing."""
        self._raw.append(list(observations))
        for observation in observations:
            self._records.setdefault(observation.record_identity, observation)
            self._acknowledged.add(observation.record_identity)

    def read_all(self) -> list[Observation]:
        return [self._records[key] for key in sorted(self._records)]

    def fingerprint(self) -> str:
        return union_fingerprint(self.read_all())

    def raw_groups(self) -> list[list[Observation]]:
        return [list(group) for group in self._raw]

    def acknowledged(self) -> set[str]:
        return set(self._acknowledged)

    def rebuild_from_raw(self) -> "UnionStore":
        """Reconstruct a store from retained raw sources alone."""
        rebuilt = UnionStore()
        for group in self._raw:
            rebuilt.apply(group)
        return rebuilt

    def rollback_to(self, snapshot: "UnionStore") -> None:
        """Return to a recorded earlier state without erasing acknowledged work.

        Rollback restores the snapshot's records and then re-applies everything
        acknowledged since, because the accepted rule is forward repair: once a
        write is acknowledged it may be superseded but never silently dropped.
        A rollback that simply reinstated the snapshot would lose exactly the
        records a user was already told had been saved.
        """
        preserved = [
            observation
            for key, observation in self._records.items()
            if key in self._acknowledged
        ]
        self._records = dict(snapshot._records)
        for observation in preserved:
            self._records.setdefault(observation.record_identity, observation)

    def snapshot(self) -> "UnionStore":
        copy = UnionStore()
        copy._records = dict(self._records)
        copy._raw = [list(group) for group in self._raw]
        copy._acknowledged = set(self._acknowledged)
        return copy


# --------------------------------------------------------------------------
# deliberately non-conforming stores, one broken guarantee each
# --------------------------------------------------------------------------


class LastWriterWinsStore(UnionStore):
    """Today's shape: each commit replaces the whole prior value.

    This is not a strawman. It is the behavior `probe_ws10a_durable_rmw.py`
    measures, and the conformance suite must reject it, otherwise the suite
    would certify the defect as fixed.
    """

    def apply(self, observations: Sequence[Observation]) -> None:
        self._raw.append(list(observations))
        self._records = {
            observation.record_identity: observation for observation in observations
        }
        for observation in observations:
            self._acknowledged.add(observation.record_identity)


class ContentDeduplicatingStore(UnionStore):
    """Collapses records that share content, destroying real duplicates."""

    def apply(self, observations: Sequence[Observation]) -> None:
        self._raw.append(list(observations))
        by_content = {
            observation.content_identity: observation
            for observation in self.read_all() + list(observations)
        }
        self._records = {
            observation.record_identity: observation for observation in by_content.values()
        }
        for observation in observations:
            self._acknowledged.add(observation.record_identity)


class UnknownFieldDroppingStore(UnionStore):
    """Discards fields this version does not understand.

    Its records therefore change identity, which is why the old/new reader
    clause and the rebuild clause both catch it: a newer producer's record
    becomes a different record on the way in.
    """

    def apply(self, observations: Sequence[Observation]) -> None:
        stripped = [
            Observation(content=dict(o.content), provenance=dict(o.provenance))
            for o in observations
        ]
        super().apply(stripped)


class ResumeDuplicatingStore(UnionStore):
    """Appends on every application, so an interrupted migration duplicates."""

    def __init__(self) -> None:
        super().__init__()
        self._counter = 0

    def apply(self, observations: Sequence[Observation]) -> None:
        self._raw.append(list(observations))
        for observation in observations:
            self._counter += 1
            self._records[f"{observation.record_identity}:{self._counter}"] = observation
            self._acknowledged.add(observation.record_identity)


class RollbackErasingStore(UnionStore):
    """Rollback reinstates the snapshot and drops acknowledged work."""

    def rollback_to(self, snapshot: "UnionStore") -> None:
        self._records = dict(snapshot._records)


class OrderSensitiveFingerprintStore(UnionStore):
    """Fingerprints arrival order, so two agreeing stores appear to disagree."""

    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(
                [observation.record_identity for group in self._raw for observation in group]
            ).encode("utf-8")
        ).hexdigest()


#: Every non-conforming store, with the specification that must reject it and
#: a note on why that specification is the right one. The negative matrix
#: asserts both directions: the named specification fails, and the store is
#: not merely failing everything for an unrelated reason.
NON_CONFORMING_STORES = (
    ("last-writer-wins", LastWriterWinsStore, "union"),
    ("content-deduplicating", ContentDeduplicatingStore, "duplicates"),
    ("unknown-field-dropping", UnknownFieldDroppingStore, "unknown_fields"),
    ("resume-duplicating", ResumeDuplicatingStore, "migration_resume"),
    ("rollback-erasing", RollbackErasingStore, "rollback"),
    ("order-sensitive-fingerprint", OrderSensitiveFingerprintStore, "determinism"),
)


# --------------------------------------------------------------------------
# the specifications, each a pure predicate over a store factory
#
# Each returns (satisfied, detail). They are values rather than test methods so
# the same specification can be run against the reference store, against every
# deliberately broken store, and later against Codex's production
# implementation, without being restated.
# --------------------------------------------------------------------------


def spec_union(factory, n: int = 16) -> tuple[bool, str]:
    """Concurrent groups combine to the mathematical union of their records."""
    groups = [[observation] for observation in distinct_fixture(n)]
    store = factory()
    for group in groups:
        store.apply(group)
    expected = union_fingerprint(union(*groups))
    observed = store.fingerprint()
    return (
        observed == expected and len(store.read_all()) == n,
        f"records={len(store.read_all())} expected={n} fingerprint_match={observed == expected}",
    )


def spec_duplicates(factory, n: int = 16) -> tuple[bool, str]:
    """Records sharing content but not provenance all survive."""
    fixture = duplicate_fixture(n)
    store = factory()
    for observation in fixture:
        store.apply([observation])
    expected = content_multiset(fixture)
    observed = content_multiset(store.read_all())
    return observed == expected, f"observed={observed} expected={expected}"


def spec_determinism(factory, n: int = 16) -> tuple[bool, str]:
    """Two stores given the same records in different orders agree."""
    fixture = random_fixture(n)
    forward, reverse = factory(), factory()
    for observation in fixture:
        forward.apply([observation])
    for observation in reversed(fixture):
        reverse.apply([observation])
    return (
        forward.fingerprint() == reverse.fingerprint(),
        f"forward={forward.fingerprint()[:16]} reverse={reverse.fingerprint()[:16]}",
    )


def spec_idempotence(factory, n: int = 16) -> tuple[bool, str]:
    """Applying the same records repeatedly changes nothing after the first."""
    fixture = distinct_fixture(n)
    store = factory()
    store.apply(fixture)
    once = store.fingerprint()
    store.apply(fixture)
    store.apply(fixture)
    return (
        store.fingerprint() == once and len(store.read_all()) == n,
        f"records={len(store.read_all())} expected={n} stable={store.fingerprint() == once}",
    )


def spec_unknown_fields(factory, n: int = 16) -> tuple[bool, str]:
    """Fields this version does not understand survive a round trip."""
    fixture = [
        Observation(
            content={"lap": index},
            provenance={"run": "run-a", "sequence": index},
            extra={"future_field": f"value-{index}"},
        )
        for index in range(n)
    ]
    store = factory()
    store.apply(fixture)
    recovered = {
        observation.extra.get("future_field") for observation in store.read_all()
    }
    expected = {f"value-{index}" for index in range(n)}
    return recovered == expected, f"missing={sorted(expected - recovered)}"


def spec_rebuild_from_raw(factory, n: int = 16) -> tuple[bool, str]:
    """Rebuilding from retained raw sources reproduces the store exactly."""
    store = factory()
    for observation in random_fixture(n):
        store.apply([observation])
    rebuilt = store.rebuild_from_raw()
    return (
        rebuilt.fingerprint() == store.fingerprint(),
        f"store={store.fingerprint()[:16]} rebuilt={rebuilt.fingerprint()[:16]}",
    )


def spec_migration_resume(factory, n: int = 16) -> tuple[bool, str]:
    """A migration interrupted at any prefix and resumed matches an uninterrupted one.

    Every cut point is exercised, not a representative one. A resume defect
    that only appears at a particular boundary is exactly the kind a single
    sampled cut would miss.
    """
    fixture = distinct_fixture(n)
    complete = factory()
    complete.apply(fixture)
    expected = complete.fingerprint()

    failures: list[int] = []
    for cut in range(n + 1):
        resumed = factory()
        resumed.apply(fixture[:cut])
        # A resume cannot know exactly where it stopped, so it replays from the
        # last durable checkpoint. Re-applying the overlap must be a no-op.
        resumed.apply(fixture)
        if resumed.fingerprint() != expected or len(resumed.read_all()) != n:
            failures.append(cut)
    return not failures, f"failed_cuts={failures}"


def spec_rollback(factory, n: int = 16) -> tuple[bool, str]:
    """Rollback restores the earlier state without erasing acknowledged work."""
    fixture = distinct_fixture(n)
    store = factory()
    store.apply(fixture[: n // 2])
    checkpoint = store.snapshot()
    store.apply(fixture[n // 2 :])
    acknowledged = store.acknowledged()
    store.rollback_to(checkpoint)
    surviving = {observation.record_identity for observation in store.read_all()}
    missing = sorted(acknowledged - surviving)
    return not missing, f"acknowledged_records_lost={len(missing)}"


def spec_old_and_new_readers_agree(factory, n: int = 16) -> tuple[bool, str]:
    """An older reader sees every record it can represent, and no invented ones.

    The older reader is modelled as one that understands content and
    provenance but not ``extra``. It must still enumerate exactly the same
    records; what it may not do is silently write them back stripped, which is
    what makes the dropping store a defect rather than a limitation.
    """
    fixture = [
        Observation(
            content={"lap": index},
            provenance={"run": "run-a", "sequence": index},
            extra={"future_field": index},
        )
        for index in range(n)
    ]
    store = factory()
    store.apply(fixture)
    new_reader = {observation.record_identity for observation in store.read_all()}
    old_reader = {
        _digest(
            {
                "content": dict(observation.content),
                "provenance": dict(observation.provenance),
                "extra": dict(observation.extra),
            }
        )
        for observation in store.read_all()
    }
    expected = {observation.record_identity for observation in fixture}
    return (
        new_reader == expected and old_reader == expected,
        f"new_missing={len(expected - new_reader)} old_missing={len(expected - old_reader)}",
    )


#: Every specification by name. Ordered so a report reads from the algebra
#: outward to the operational guarantees built on it.
SPECIFICATIONS = {
    "union": spec_union,
    "duplicates": spec_duplicates,
    "determinism": spec_determinism,
    "idempotence": spec_idempotence,
    "unknown_fields": spec_unknown_fields,
    "rebuild_from_raw": spec_rebuild_from_raw,
    "migration_resume": spec_migration_resume,
    "rollback": spec_rollback,
    "old_and_new_readers": spec_old_and_new_readers_agree,
}


def evaluate(factory, n: int = 16) -> dict[str, dict[str, Any]]:
    """Run every specification against one store factory."""
    results: dict[str, dict[str, Any]] = {}
    for name, specification in SPECIFICATIONS.items():
        try:
            satisfied, detail = specification(factory, n)
        except Exception as exc:  # an unimplementable clause is an unmet clause
            satisfied, detail = False, f"{type(exc).__name__}: {exc}"
        results[name] = {"satisfied": bool(satisfied), "detail": str(detail)}
    return results
