"""WS-10A atomic containment attack. NOT a discovered test.

Asserts the DESIRED post-repair containment contract against real production
code. It is RED at the parent commit by design: the typed failure family and
bounded retry do not exist yet.

Every clause binds to OBSERVED behavior - actual replace calls recorded by the
injector, actual sleep calls recorded by the sleeper, actual directory
contents snapshotted before the operation, and the exact exception object that
escaped - rather than to fields the implementation reports about itself. An
earlier revision trusted reported fields and independent helpers, and an
implementation performing one replace and zero sleeps while forging its own
bookkeeping passed. The clause surface lives in ``ws10a_support`` so
``test_ws10a_atomic_harness`` can run it against deliberately non-conforming
references and prove each is rejected WITHOUT importing this filename, which
the accepted transition deletes.

After the Codex-owned production repair this probe turns green and is renamed
UNCHANGED to test_ws10a_containment.py, at which point its identities gain
evidence declarations. The ``unittest`` cases below are what that rename
discovers: renaming a module that defines no TestCase would produce a suite of
zero tests, and a zero-test suite reports success while asserting nothing. If
any assertion here must change to make it pass, the contract moved and needs a
versioned plan correction rather than a quiet edit.

    exit 0  - every clause met; containment is present
    exit 1  - one or more clauses unmet; each is reported individually

Run:
    python -X utf8 iracing-coach/tests/probe_ws10a_atomic_containment.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ws10a_support as support

REPO = Path(__file__).resolve().parents[2]
STORAGE_PATH = (
    REPO / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts" / "storage.py"
)

INVALID_DIGEST = support.CANARY_DECLARED_DIGEST
VALID_BUT_WRONG_SHA = support.VALID_BUT_WRONG_SHA

# The prospective post-repair filename. Named here so the discovered harness
# can prove the rename yields a nonzero suite without hard-coding it twice.
PROSPECTIVE_MODULE_NAME = "test_ws10a_containment"


def load_storage():
    scripts = STORAGE_PATH.parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("ws10a_probe_storage", STORAGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_writer(storage):
    def write(path, text, *, sleeper, jitter_ms):
        return storage._atomic_write_text(path, text, sleeper=sleeper, jitter_ms=jitter_ms)
    return write


def containment_target():
    """The ``(write, types)`` pair whose containment clauses are asserted.

    Production by default. The discovered harness patches this on a freshly
    loaded copy of this module to prove that unmet clauses become unittest
    FAILURES rather than a silently successful zero-test import - a proof that
    must not depend on whether production happens to be repaired yet.
    """
    storage = load_storage()
    return production_writer(storage), storage


# --------------------------------------------------------------------------
# typed family
# --------------------------------------------------------------------------

def family_clauses(storage, clauses: support.Clauses) -> None:
    clauses.check(
        "StorageCommitError exists and derives from OSError",
        lambda: (issubclass(storage.StorageCommitError, OSError), "present"),
    )
    for name, code in (
        ("AtomicReplaceExhausted", "atomic-replace-exhausted"),
        ("ArchiveVerificationFailed", None),
        ("ArchiveSourceUnstable", None),
        ("InvalidSourceDigest", "invalid-source-digest"),
    ):
        def probe(name=name, code=code):
            klass = getattr(storage, name)
            ok = issubclass(klass, storage.StorageCommitError)
            if code is not None:
                ok = ok and getattr(klass, "code", None) == code
            return ok, f"{name} code={getattr(klass, 'code', None)!r}"

        clauses.check(f"{name} is in the StorageCommitError family", probe)

    clauses.check(
        "InvalidSourceDigest is also a ValueError for caller compatibility",
        lambda: (issubclass(storage.InvalidSourceDigest, ValueError), "dual inheritance"),
    )
    clauses.check(
        "sibling failures are NOT ValueError",
        lambda: (not issubclass(storage.ArchiveSourceUnstable, ValueError),
                 "dual inheritance confined to the one compatibility site"),
    )


# --------------------------------------------------------------------------
# the six archival redaction sites, with distinct per-channel canaries
# --------------------------------------------------------------------------

def _fixture(root: Path, payload: bytes) -> Path:
    holder = root / support.CANARY_SOURCE_PATH
    holder.mkdir(parents=True, exist_ok=True)
    source = holder / "telemetry.ibt"
    source.write_bytes(payload)
    return source


def _record(storage, source: Path, *, sha=None, size=None, modified=None):
    stat = source.stat()
    return {
        "path": str(source),
        "sha256": sha if sha is not None else storage.file_sha256(source),
        "size": size if size is not None else stat.st_size,
        "modified_ns": modified if modified is not None else stat.st_mtime_ns,
    }


def _invocation(storage, scenario: support.ArchivalScenario, source: Path, store_root: Path):
    """Build the call that drives ``scenario`` to its failure."""

    def fresh_store():
        store = storage.ArchiveStore(root=store_root)
        store.initialize()
        return store

    if scenario.key == "c1915":
        return lambda: fresh_store().archive_raw_telemetry(
            [_record(storage, source, sha=INVALID_DIGEST)])

    if scenario.key == "c1925":
        return lambda: fresh_store().archive_raw_telemetry(
            [_record(storage, source, size=999999)])

    if scenario.key == "c495":
        return lambda: fresh_store().archive_raw_telemetry(
            [_record(storage, source, sha=VALID_BUT_WRONG_SHA)])

    if scenario.key == "c481":
        def digest_changed_before_copy():
            store = fresh_store()
            store.archive_raw_telemetry(
                [_record(storage, source, sha=scenario.declared)])
            source.write_bytes(scenario.post_bytes)
            stat = source.stat()
            store.archive_raw_telemetry(
                [_record(storage, source, sha=scenario.declared,
                         size=stat.st_size, modified=stat.st_mtime_ns)])
        return digest_changed_before_copy

    if scenario.key == "c1891":
        def changed_during_hashing():
            real_hash = storage.file_sha256

            def mutating_hash(path, *args, **kwargs):
                digest = real_hash(path, *args, **kwargs)
                Path(path).write_bytes(scenario.post_bytes)
                return digest

            with mock.patch.object(storage, "file_sha256", mutating_hash):
                fresh_store().source_fingerprints([str(source)])
        return changed_during_hashing

    if scenario.key == "c1953":
        def changed_during_archival():
            store = fresh_store()
            record = _record(storage, source)
            real_copy = storage._atomic_copy_verified

            def mutating_copy(src, destination, expected):
                real_copy(src, destination, expected)
                Path(src).write_bytes(scenario.post_bytes)

            with mock.patch.object(storage, "_atomic_copy_verified", mutating_copy):
                store.archive_raw_telemetry([record])
        return changed_during_archival

    raise ValueError(f"unknown archival scenario: {scenario.key!r}")


def archival_clauses(storage, root: Path, clauses: support.Clauses) -> None:
    # the destination root itself carries a distinct canary
    store_root = root / support.CANARY_DESTINATION_PATH / "archive"

    for scenario in support.ARCHIVAL_SCENARIOS:
        source = _fixture(root / scenario.key, scenario.pre_bytes)
        klass = getattr(storage, scenario.klass_name, ())
        support.redaction_clauses(
            clauses,
            scenario.label,
            _invocation(storage, scenario, source, store_root),
            klass,
            scenario.code,
            scenario.tokens(),
        )


# --------------------------------------------------------------------------
# what the accepted rename discovers
# --------------------------------------------------------------------------

def _render(clauses: support.Clauses) -> str:
    return "\n".join(
        f"  UNMET  {record['clause']}\n         {record['detail']}"
        for record in clauses.unmet
    )


class AtomicContainmentTests(unittest.TestCase):
    """The discovered form of this probe.

    These exist so that renaming this file UNCHANGED to
    ``test_ws10a_containment.py`` produces a suite that actually asserts the
    contract. Under the current ``probe_`` filename they are not collected by
    the default ``test*.py`` discovery pattern, so the probe stays
    non-discovered and expected-red exactly as accepted.
    """

    def _assert_all_met(self, clauses: support.Clauses) -> None:
        self.assertGreater(len(clauses.records), 0, "no clause was evaluated")
        if clauses.unmet:
            self.fail(
                f"{len(clauses.unmet)} of {len(clauses.records)} clauses unmet:\n"
                + _render(clauses)
            )

    def test_the_typed_failure_family_is_present(self):
        clauses = support.Clauses()
        family_clauses(load_storage(), clauses)
        self._assert_all_met(clauses)

    def test_every_containment_clause_is_met(self):
        write, types = containment_target()
        clauses = support.Clauses()
        with support.sandbox("ws10a-discovered-containment") as root:
            support.containment_clauses(write, types, root, clauses)
        self._assert_all_met(clauses)

    def test_every_archival_redaction_clause_is_met(self):
        storage = load_storage()
        clauses = support.Clauses()
        with support.sandbox("ws10a-discovered-archival") as root:
            archival_clauses(storage, root, clauses)
        self._assert_all_met(clauses)


# --------------------------------------------------------------------------

def evaluate() -> support.Clauses:
    storage = load_storage()
    clauses = support.Clauses()
    with support.sandbox("ws10a-atomic-containment") as root:
        family_clauses(storage, clauses)
        support.containment_clauses(production_writer(storage), storage, root, clauses)
        archival_clauses(storage, root, clauses)
    return clauses


def main() -> int:
    clauses = evaluate()

    report = {
        "schema": "ws10a-atomic-containment-v3",
        "probe": "atomic-containment",
        "storage": str(STORAGE_PATH),
        "total": len(clauses.records),
        "met": len(clauses.met),
        "unmet": len(clauses.unmet),
        "clauses": clauses.records,
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    print("\n--- containment clauses ---")
    for record in clauses.records:
        print(f"  {'MET  ' if record['met'] else 'UNMET'}  {record['clause']}")
        if not record["met"]:
            print(f"           {record['detail']}")

    if clauses.unmet:
        print(
            f"\n{len(clauses.unmet)} of {len(clauses.records)} clauses unmet: "
            "containment is ABSENT at this commit (expected before the repair)."
        )
        return 1

    print(f"\nAll {len(clauses.records)} clauses met: containment is present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
