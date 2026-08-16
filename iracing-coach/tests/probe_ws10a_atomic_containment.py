"""WS-10A atomic containment attack. NOT a discovered test.

Asserts the DESIRED post-repair containment contract against real production
code. It is RED at the parent commit by design: the typed failure family and
bounded retry do not exist yet.

After the Codex-owned production repair this probe turns green and is renamed
UNCHANGED to test_ws10a_containment.py, at which point its identities gain
evidence declarations. If any assertion here must change to make it pass, the
contract moved and needs a versioned plan correction rather than a quiet edit.

    exit 0  - every clause met; containment is present
    exit 1  - one or more clauses unmet; each is reported individually

Run:
    python -X utf8 iracing-coach/tests/probe_ws10a_atomic_containment.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ws10a_support as support

REPO = Path(__file__).resolve().parents[2]
STORAGE_PATH = (
    REPO / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts" / "storage.py"
)

CANARY_PATH_PART = "CANARY-PATH-a1b2c3"
CANARY_CONTENT = b"CANARY-CONTENT-d4e5f6"
CANARY_INJECTED = "CANARY-INJECTED-97a8b9"
CANARIES = [CANARY_PATH_PART, CANARY_CONTENT.decode("ascii"), CANARY_INJECTED]

VALID_SHA = "0" * 64


def load_storage():
    scripts = STORAGE_PATH.parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("ws10a_probe_storage", STORAGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clauses:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def check(self, name: str, fn) -> None:
        try:
            met, detail = fn()
        except Exception as exc:  # an absent contract is an unmet clause
            met, detail = False, f"{type(exc).__name__}: {exc}"
        self.records.append({"clause": name, "met": bool(met), "detail": str(detail)})

    @property
    def unmet(self) -> list[dict]:
        return [r for r in self.records if not r["met"]]


# --------------------------------------------------------------------------
# typed family
# --------------------------------------------------------------------------

def family_clauses(storage, clauses: Clauses) -> None:
    expected = {
        "AtomicReplaceExhausted": "atomic-replace-exhausted",
        "ArchiveVerificationFailed": None,
        "ArchiveSourceUnstable": None,
        "InvalidSourceDigest": "invalid-source-digest",
    }

    clauses.check(
        "StorageCommitError exists and derives from OSError",
        lambda: (
            issubclass(storage.StorageCommitError, OSError),
            storage.StorageCommitError.__mro__[1].__name__,
        ),
    )

    for name, code in expected.items():
        def probe(name=name, code=code):
            klass = getattr(storage, name)
            ok = issubclass(klass, storage.StorageCommitError)
            if code is not None:
                ok = ok and getattr(klass, "code", None) == code
            return ok, f"{name} code={getattr(klass, 'code', None)!r}"

        clauses.check(f"{name} is in the StorageCommitError family", probe)

    clauses.check(
        "InvalidSourceDigest is also a ValueError for caller compatibility",
        lambda: (
            issubclass(storage.InvalidSourceDigest, ValueError),
            "dual inheritance present",
        ),
    )

    clauses.check(
        "sibling failures are NOT ValueError",
        lambda: (
            not issubclass(storage.ArchiveSourceUnstable, ValueError),
            "dual inheritance is confined to the one compatibility site",
        ),
    )


# --------------------------------------------------------------------------
# retry and non-retry
# --------------------------------------------------------------------------

def _write_with_seams(storage, path, text, faults, sleeper, jitter):
    """Call the primitive with the bound injectable seams."""
    with support.injected_replace(faults):
        storage._atomic_write_text(
            path, text, sleeper=sleeper, jitter_ms=jitter
        )


def retry_clauses(storage, root: Path, clauses: Clauses) -> None:
    for code in support.RETRYABLE_WINERRORS:
        def probe(code=code):
            target = root / f"retry-{code}.json"
            faults = support.ReplaceFaults(
                [support.make_os_error(code), support.make_os_error(code)]
            )
            sleeper = support.RecordingSleeper()
            _write_with_seams(
                storage, target, "recovered\n", faults,
                sleeper, support.proportional_jitter_ms(0.0),
            )
            return (
                faults.count == 3
                and target.read_text(encoding="utf-8") == "recovered\n"
                and len(sleeper) == 2,
                f"attempts={faults.count} sleeps={len(sleeper)}",
            )

        clauses.check(f"winerror {code} retries and then succeeds", probe)

    non_retryable = {
        "winerror 13": support.make_os_error(13),
        "winerror True": support.make_os_error(True),
        "winerror '32'": support.make_os_error("32"),
        "errno-only 5": support.make_errno_only_error(5),
        "nested cause 32": support.make_nested_error(32, via="cause"),
        "nested context 32": support.make_nested_error(32, via="context"),
    }

    for label, error in non_retryable.items():
        def probe(error=error, label=label):
            target = root / f"nonretry-{abs(hash(label)) % 10**8}.json"
            faults = support.ReplaceFaults([error, error, error, error, error])
            sleeper = support.RecordingSleeper()
            try:
                _write_with_seams(
                    storage, target, "x\n", faults,
                    sleeper, support.proportional_jitter_ms(0.0),
                )
            except OSError:
                pass
            return (
                faults.count == 1 and len(sleeper) == 0,
                f"attempts={faults.count} sleeps={len(sleeper)}",
            )

        clauses.check(f"{label} does not retry", probe)


def exhaustion_clauses(storage, root: Path, clauses: Clauses) -> None:
    target = root / "exhausted.json"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    original = target.read_bytes()

    faults = support.ReplaceFaults(
        [support.make_os_error(5, f"denied {CANARY_INJECTED}") for _ in range(5)]
    )
    sleeper = support.RecordingSleeper()
    raised: BaseException | None = None
    try:
        _write_with_seams(
            storage, target, "REPLACEMENT\n", faults,
            sleeper, support.proportional_jitter_ms(1.0),
        )
    except BaseException as exc:
        raised = exc

    # Every invariant below is only meaningful if exhaustion actually
    # occurred. Without this gate they pass vacuously whenever the call
    # fails for an unrelated reason - today a TypeError, because the seam
    # parameters do not exist yet - which would report a green invariant
    # for an operation that never ran.
    exhausted = isinstance(
        raised, getattr(storage, "AtomicReplaceExhausted", ())
    )
    NOT_EXERCISED = "exhaustion did not occur; invariant not exercised"

    def gated(fn):
        def probe():
            if not exhausted:
                return False, f"{NOT_EXERCISED} (raised {type(raised).__name__})"
            return fn()
        return probe

    clauses.check(
        "exhaustion raises AtomicReplaceExhausted",
        lambda: (exhausted, type(raised).__name__),
    )
    clauses.check(
        "exhaustion reports exactly five attempts",
        gated(lambda: (getattr(raised, "attempts", None) == 5,
                       f"attempts={getattr(raised, 'attempts', None)}")),
    )
    clauses.check(
        "total slept is within the bound 150..300 ms",
        gated(lambda: (
            150 <= getattr(raised, "total_slept_ms", -1) <= 300,
            f"total_slept_ms={getattr(raised, 'total_slept_ms', None)}",
        )),
    )
    clauses.check(
        "final_winerror is the retryable code",
        gated(lambda: (getattr(raised, "final_winerror", None) == 5,
                       f"final_winerror={getattr(raised, 'final_winerror', None)}")),
    )
    clauses.check(
        "destination bytes are unchanged after exhaustion",
        gated(lambda: (target.read_bytes() == original, repr(target.read_bytes()))),
    )
    clauses.check(
        "no temp file survives exhaustion",
        gated(lambda: (
            [p.name for p in root.iterdir() if p.name.startswith(".exhausted")] == [],
            "temp swept",
        )),
    )
    clauses.check(
        "exhaustion suppresses __cause__ and __context__",
        gated(lambda: (
            getattr(raised, "__cause__", None) is None
            and getattr(raised, "__context__", None) is None,
            f"cause={getattr(raised, '__cause__', None)!r} "
            f"context={getattr(raised, '__context__', None)!r}",
        )),
    )
    clauses.check(
        "exhaustion leaks no canary",
        gated(lambda: (
            support.find_canaries(raised, CANARIES) == [],
            f"leaked={support.find_canaries(raised, CANARIES)}",
        )),
    )


# --------------------------------------------------------------------------
# the six archival redaction sites
# --------------------------------------------------------------------------

def _fixture(root: Path):
    """A synthetic source whose PATH and CONTENT both carry canaries."""
    holder = root / CANARY_PATH_PART
    holder.mkdir(parents=True, exist_ok=True)
    source = holder / "telemetry.ibt"
    source.write_bytes(CANARY_CONTENT)
    return source


def _record(storage, source: Path, *, sha=None, size=None, modified=None):
    stat = source.stat()
    return {
        "path": str(source),
        "sha256": sha if sha is not None else storage.file_sha256(source),
        "size": size if size is not None else stat.st_size,
        "modified_ns": modified if modified is not None else stat.st_mtime_ns,
    }


def _expect(clauses: Clauses, label: str, fn, klass_name: str, code: str, storage):
    def probe():
        raised: BaseException | None = None
        try:
            fn()
        except BaseException as exc:
            raised = exc
        klass = getattr(storage, klass_name)
        typed = isinstance(raised, klass)
        right_code = getattr(raised, "code", None) == code
        leaked = support.find_canaries(raised, CANARIES) if raised else ["<no raise>"]
        chain_clear = (
            getattr(raised, "__cause__", None) is None
            and getattr(raised, "__context__", None) is None
        )
        return (
            typed and right_code and not leaked and chain_clear,
            f"type={type(raised).__name__} code={getattr(raised, 'code', None)!r} "
            f"leaked={leaked} chain_clear={chain_clear}",
        )

    clauses.check(label, probe)


def archival_clauses(storage, root: Path, clauses: Clauses) -> None:
    store_root = root / "archive"

    def fresh_store():
        store = storage.ArchiveStore(root=store_root)
        store.initialize()
        return store

    # 1915 - invalid declared digest, reachable through the public method
    def invalid_digest():
        source = _fixture(root / "c1915")
        fresh_store().archive_raw_telemetry(
            [_record(storage, source, sha="not-a-hash")]
        )

    _expect(clauses, "1915 invalid source digest is typed and redacted",
            invalid_digest, "InvalidSourceDigest", "invalid-source-digest", storage)

    # 1925 - record disagrees with the real stat, reachable publicly
    def changed_before():
        source = _fixture(root / "c1925")
        fresh_store().archive_raw_telemetry(
            [_record(storage, source, size=999999)]
        )

    _expect(clauses, "1925 source changed before archival is typed and redacted",
            changed_before, "ArchiveSourceUnstable", "source-changed-before-archival",
            storage)

    # 495 - declared digest does not match the bytes, reachable publicly
    def archived_mismatch():
        source = _fixture(root / "c495")
        fresh_store().archive_raw_telemetry(
            [_record(storage, source, sha=VALID_SHA)]
        )

    _expect(clauses, "495 archived digest mismatch is typed and redacted",
            archived_mismatch, "ArchiveVerificationFailed", "archived-digest-mismatch",
            storage)

    # 481 - archive once, mutate the source, re-archive with the old digest
    def digest_changed_before_copy():
        source = _fixture(root / "c481")
        store = fresh_store()
        original = _record(storage, source)
        store.archive_raw_telemetry([original])
        source.write_bytes(CANARY_CONTENT + b"-MUTATED")
        stat = source.stat()
        store.archive_raw_telemetry(
            [_record(storage, source, sha=original["sha256"],
                     size=stat.st_size, modified=stat.st_mtime_ns)]
        )

    _expect(clauses, "481 source digest changed before copy is typed and redacted",
            digest_changed_before_copy, "ArchiveVerificationFailed",
            "source-digest-changed-before-copy", storage)

    # 1891 - named seam: mutate the file while source_fingerprints hashes it
    def changed_during_hashing():
        source = _fixture(root / "c1891")
        real_hash = storage.file_sha256

        def mutating_hash(path, *args, **kwargs):
            digest = real_hash(path, *args, **kwargs)
            Path(path).write_bytes(CANARY_CONTENT + b"-DURING-HASH")
            return digest

        with mock.patch.object(storage, "file_sha256", mutating_hash):
            fresh_store().source_fingerprints([str(source)])

    _expect(clauses, "1891 source changed during hashing is typed and redacted",
            changed_during_hashing, "ArchiveSourceUnstable",
            "source-changed-during-hashing", storage)

    # 1953 - named seam: mutate the source inside the verified copy
    def changed_during_archival():
        source = _fixture(root / "c1953")
        store = fresh_store()
        record = _record(storage, source)
        real_copy = storage._atomic_copy_verified

        def mutating_copy(src, dst, expected):
            real_copy(src, dst, expected)
            Path(src).write_bytes(CANARY_CONTENT + b"-DURING-ARCHIVAL")

        with mock.patch.object(storage, "_atomic_copy_verified", mutating_copy):
            store.archive_raw_telemetry([record])

    _expect(clauses, "1953 source changed during archival is typed and redacted",
            changed_during_archival, "ArchiveSourceUnstable",
            "source-changed-during-archival", storage)


# --------------------------------------------------------------------------

def main() -> int:
    storage = load_storage()
    clauses = Clauses()

    with support.sandbox("ws10a-atomic-containment") as root:
        family_clauses(storage, clauses)
        retry_clauses(storage, root, clauses)
        exhaustion_clauses(storage, root, clauses)
        archival_clauses(storage, root, clauses)

    report = {
        "schema": "ws10a-atomic-containment-v1",
        "probe": "atomic-containment",
        "storage": str(STORAGE_PATH),
        "total": len(clauses.records),
        "met": len(clauses.records) - len(clauses.unmet),
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
