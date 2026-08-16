"""WS-10A atomic containment attack. NOT a discovered test.

Asserts the DESIRED post-repair containment contract against real production
code. It is RED at the parent commit by design: the typed failure family and
bounded retry do not exist yet.

Every clause binds to OBSERVED behavior - actual replace calls recorded by the
injector, actual sleep calls recorded by the sleeper, and the exact exception
object that escaped - rather than to fields the implementation reports about
itself. An earlier revision trusted reported fields and independent helpers,
and an implementation performing one replace and zero sleeps while forging its
own bookkeeping passed. The clause functions are parameterised by the
primitive under test so `test_ws10a_atomic_harness` can run them against
deliberately non-conforming references and prove each is rejected.

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

import hashlib
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

INVALID_DIGEST = support.CANARY_DECLARED_DIGEST
VALID_BUT_WRONG_SHA = "0" * 64


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

    @property
    def met(self) -> list[dict]:
        return [r for r in self.records if r["met"]]


def production_writer(storage):
    def write(path, text, *, sleeper, jitter_ms):
        return storage._atomic_write_text(path, text, sleeper=sleeper, jitter_ms=jitter_ms)
    return write


def _strays(root: Path, stem: str) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith(f".{stem}"))


# --------------------------------------------------------------------------
# typed family
# --------------------------------------------------------------------------

def family_clauses(storage, clauses: Clauses) -> None:
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
# retry - bound to observed replace and sleep calls
# --------------------------------------------------------------------------

def retry_clauses(write, types, root: Path, clauses: Clauses) -> None:
    for code in support.RETRYABLE_WINERRORS:
        def probe(code=code):
            target = root / f"retry-{code}.json"
            faults = support.ReplaceFaults(
                [support.make_os_error(code), support.make_os_error(code)]
            )
            sleeper = support.RecordingSleeper()
            with support.injected_replace(faults):
                write(target, "recovered\n", sleeper=sleeper,
                      jitter_ms=support.proportional_jitter_ms(0.0))
            expected = [support.BASE_DELAYS_MS[0] / 1000.0,
                        support.BASE_DELAYS_MS[1] / 1000.0]
            return (
                faults.count == 3
                and target.read_text(encoding="utf-8") == "recovered\n"
                and sleeper.calls == expected,
                f"replaces={faults.count} sleeps={sleeper.calls} expected={expected}",
            )

        clauses.check(
            f"winerror {code} retries with the exact delay schedule and then succeeds",
            probe,
        )


# --------------------------------------------------------------------------
# invalid jitter must be attacked through the production seam
# --------------------------------------------------------------------------

def jitter_clauses(write, types, root: Path, clauses: Clauses) -> None:
    for value in support.INVALID_JITTER_RESULTS:
        def probe(value=value):
            stem = f"jitter-{abs(hash(repr(value))) % 10**8}.json"
            target = root / stem
            target.write_text("ORIGINAL\n", encoding="utf-8")
            original = target.read_bytes()
            # one retryable failure forces the primitive to compute a delay,
            # which is the only point at which jitter is consulted
            faults = support.ReplaceFaults(
                [support.make_os_error(support.RETRYABLE_WINERRORS[0])
                 for _ in range(support.MAX_ATTEMPTS)]
            )
            sleeper = support.RecordingSleeper()
            raised: BaseException | None = None
            with support.injected_replace(faults):
                try:
                    write(target, "REPLACEMENT\n", sleeper=sleeper,
                          jitter_ms=support.fixed_jitter_ms(value))
                except BaseException as exc:
                    raised = exc
            return (
                raised is not None
                and not isinstance(raised, OSError)
                and faults.count == 1
                and sleeper.calls == []
                and target.read_bytes() == original
                and _strays(root, stem) == [],
                f"raised={type(raised).__name__} replaces={faults.count} "
                f"sleeps={len(sleeper)} dest_intact={target.read_bytes() == original}",
            )

        clauses.check(
            f"invalid jitter {value!r} fails as a contract error before a second replace",
            probe,
        )


# --------------------------------------------------------------------------
# non-retry - the error must ESCAPE, not merely not-retry
# --------------------------------------------------------------------------

def nonretry_clauses(write, types, root: Path, clauses: Clauses) -> None:
    cases = {
        "winerror 13": lambda: support.make_os_error(13),
        "winerror True": lambda: support.make_os_error(True),
        "winerror '32'": lambda: support.make_os_error("32"),
        "errno-only 5": lambda: support.make_errno_only_error(5),
        "nested cause 32": lambda: support.make_nested_error(32, via="cause"),
        "nested context 32": lambda: support.make_nested_error(32, via="context"),
    }
    for label, factory in cases.items():
        def probe(label=label, factory=factory):
            error = factory()
            stem = f"nonretry-{abs(hash(label)) % 10**8}.json"
            target = root / stem
            faults = support.ReplaceFaults([error] * support.MAX_ATTEMPTS)
            sleeper = support.RecordingSleeper()
            raised: BaseException | None = None
            with support.injected_replace(faults):
                try:
                    write(target, "x\n", sleeper=sleeper,
                          jitter_ms=support.proportional_jitter_ms(0.0))
                except BaseException as exc:
                    raised = exc
            return (
                raised is error          # the exact object escaped, unwrapped
                and faults.count == 1
                and sleeper.calls == []
                and _strays(root, stem) == [],
                f"escaped={raised is error} raised={type(raised).__name__} "
                f"replaces={faults.count} sleeps={len(sleeper)}",
            )

        clauses.check(f"{label} escapes unwrapped after exactly one replace", probe)


# --------------------------------------------------------------------------
# exhaustion - bound to observed calls at both schedule endpoints
# --------------------------------------------------------------------------

def exhaustion_clauses(write, types, root: Path, clauses: Clauses) -> None:
    endpoints = (
        ("minimum", support.proportional_jitter_ms(0.0),
         [b / 1000.0 for b in support.BASE_DELAYS_MS], support.MIN_TOTAL_MS),
        ("maximum", support.proportional_jitter_ms(1.0),
         [2 * b / 1000.0 for b in support.BASE_DELAYS_MS], support.MAX_TOTAL_MS),
    )

    for label, jitter, expected_sleeps, expected_total in endpoints:
        stem = f"exhausted-{label}.json"
        target = root / stem
        target.write_text("ORIGINAL\n", encoding="utf-8")
        original = target.read_bytes()

        faults = support.ReplaceFaults([
            support.make_os_error(
                support.RETRYABLE_WINERRORS[0], f"denied {support.CANARY_INJECTED}"
            )
            for _ in range(support.MAX_ATTEMPTS)
        ])
        sleeper = support.RecordingSleeper()
        raised: BaseException | None = None
        with support.injected_replace(faults):
            try:
                write(target, "REPLACEMENT\n", sleeper=sleeper, jitter_ms=jitter)
            except BaseException as exc:
                raised = exc

        # getattr with a () default so an absent type yields False rather than
        # raising: the probe must report every clause individually, including
        # at a parent commit where the typed family does not exist yet.
        exhausted = isinstance(raised, getattr(types, "AtomicReplaceExhausted", ()))
        NOT_EXERCISED = "exhaustion did not occur; invariant not exercised"

        def gated(fn, raised=raised, exhausted=exhausted):
            def inner():
                if not exhausted:
                    return False, f"{NOT_EXERCISED} (raised {type(raised).__name__})"
                return fn()
            return inner

        clauses.check(
            f"exhaustion at the {label} schedule raises AtomicReplaceExhausted",
            lambda exhausted=exhausted, raised=raised: (
                exhausted, type(raised).__name__),
        )
        clauses.check(
            f"{label}: exactly five replace attempts were OBSERVED",
            gated(lambda faults=faults: (faults.count == support.MAX_ATTEMPTS,
                                         f"observed replaces={faults.count}")),
        )
        clauses.check(
            f"{label}: the OBSERVED sleep schedule is exact",
            gated(lambda sleeper=sleeper, expected_sleeps=expected_sleeps: (
                sleeper.calls == expected_sleeps,
                f"observed={sleeper.calls} expected={expected_sleeps}")),
        )
        clauses.check(
            f"{label}: reported attempts equal OBSERVED replace calls",
            gated(lambda raised=raised, faults=faults: (
                getattr(raised, "attempts", None) == faults.count,
                f"reported={getattr(raised, 'attempts', None)} observed={faults.count}")),
        )
        clauses.check(
            f"{label}: reported total_slept_ms equals the OBSERVED total and the bound",
            gated(lambda raised=raised, sleeper=sleeper, expected_total=expected_total: (
                getattr(raised, "total_slept_ms", None) == expected_total
                and round(sum(sleeper.calls) * 1000) == expected_total,
                f"reported={getattr(raised, 'total_slept_ms', None)} "
                f"observed={round(sum(sleeper.calls) * 1000)} bound={expected_total}")),
        )
        clauses.check(
            f"{label}: final_winerror is the retryable code",
            gated(lambda raised=raised: (
                getattr(raised, "final_winerror", None) == support.RETRYABLE_WINERRORS[0],
                f"final_winerror={getattr(raised, 'final_winerror', None)}")),
        )
        clauses.check(
            f"{label}: destination bytes are unchanged",
            gated(lambda target=target, original=original: (
                target.read_bytes() == original, repr(target.read_bytes()))),
        )
        clauses.check(
            f"{label}: no temp file survives",
            gated(lambda root=root, stem=stem: (_strays(root, stem) == [], "temp swept")),
        )
        clauses.check(
            f"{label}: __cause__ and __context__ are suppressed",
            gated(lambda raised=raised: (
                getattr(raised, "__cause__", None) is None
                and getattr(raised, "__context__", None) is None,
                f"cause={getattr(raised, '__cause__', None)!r} "
                f"context={getattr(raised, '__context__', None)!r}")),
        )
        clauses.check(
            f"{label}: no canary survives into the failure",
            gated(lambda raised=raised: (
                support.find_canaries(raised, [support.CANARY_INJECTED]) == [],
                f"leaked={support.find_canaries(raised, [support.CANARY_INJECTED])}")),
        )


def containment_clauses(write, types, root: Path, clauses: Clauses) -> None:
    """Everything that can be run against an arbitrary primitive."""
    retry_clauses(write, types, root, clauses)
    jitter_clauses(write, types, root, clauses)
    nonretry_clauses(write, types, root, clauses)
    exhaustion_clauses(write, types, root, clauses)


# --------------------------------------------------------------------------
# the six archival redaction sites, with distinct per-channel canaries
# --------------------------------------------------------------------------

def _fixture(root: Path) -> Path:
    holder = root / support.CANARY_SOURCE_PATH
    holder.mkdir(parents=True, exist_ok=True)
    source = holder / "telemetry.ibt"
    source.write_bytes(support.CANARY_SOURCE_BYTES.encode("ascii"))
    return source


def _record(storage, source: Path, *, sha=None, size=None, modified=None):
    stat = source.stat()
    return {
        "path": str(source),
        "sha256": sha if sha is not None else storage.file_sha256(source),
        "size": size if size is not None else stat.st_size,
        "modified_ns": modified if modified is not None else stat.st_mtime_ns,
    }


def _actual_digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _expect(clauses, label, fn, klass_name, code, storage, tokens):
    def probe():
        raised: BaseException | None = None
        try:
            fn()
        except BaseException as exc:
            raised = exc
        if raised is None:
            return False, "no exception was raised; the site was not exercised"
        klass = getattr(storage, klass_name)
        typed = isinstance(raised, klass)
        right_code = getattr(raised, "code", None) == code
        leaked = support.find_canaries(raised, tokens.values())
        chain_clear = (
            getattr(raised, "__cause__", None) is None
            and getattr(raised, "__context__", None) is None
        )
        channels = {k for k, v in tokens.items() if v in support.exception_surface(raised)}
        return (
            typed and right_code and not leaked and chain_clear,
            f"type={type(raised).__name__} code={getattr(raised, 'code', None)!r} "
            f"leaked_channels={sorted(channels)} chain_clear={chain_clear}",
        )

    clauses.check(label, probe)


def archival_clauses(storage, root: Path, clauses: Clauses) -> None:
    # the destination root itself carries a distinct canary
    store_root = root / support.CANARY_DESTINATION_PATH / "archive"

    def fresh_store():
        store = storage.ArchiveStore(root=store_root)
        store.initialize()
        return store

    def scenario(name):
        source = _fixture(root / name)
        return source, _actual_digest(source)

    # 1915 - invalid declared digest (public route)
    src, actual = scenario("c1915")
    _expect(clauses, "1915 invalid source digest is typed and redacted",
            lambda: fresh_store().archive_raw_telemetry(
                [_record(storage, src, sha=INVALID_DIGEST)]),
            "InvalidSourceDigest", "invalid-source-digest", storage,
            support.canary_tokens(INVALID_DIGEST, actual))

    # 1925 - record disagrees with the real stat (public route)
    src, actual = scenario("c1925")
    _expect(clauses, "1925 source changed before archival is typed and redacted",
            lambda: fresh_store().archive_raw_telemetry(
                [_record(storage, src, size=999999)]),
            "ArchiveSourceUnstable", "source-changed-before-archival", storage,
            support.canary_tokens(actual, actual))

    # 495 - declared digest does not match the bytes (public route)
    src, actual = scenario("c495")
    _expect(clauses, "495 archived digest mismatch is typed and redacted",
            lambda: fresh_store().archive_raw_telemetry(
                [_record(storage, src, sha=VALID_BUT_WRONG_SHA)]),
            "ArchiveVerificationFailed", "archived-digest-mismatch", storage,
            support.canary_tokens(VALID_BUT_WRONG_SHA, actual))

    # 481 - archive once, mutate the source, re-archive with the old digest
    src, _ = scenario("c481")
    original_digest = _actual_digest(src)

    def digest_changed_before_copy():
        store = fresh_store()
        store.archive_raw_telemetry([_record(storage, src, sha=original_digest)])
        src.write_bytes((support.CANARY_SOURCE_BYTES + "-MUTATED").encode("ascii"))
        stat = src.stat()
        store.archive_raw_telemetry([_record(storage, src, sha=original_digest,
                                             size=stat.st_size,
                                             modified=stat.st_mtime_ns)])

    _expect(clauses, "481 source digest changed before copy is typed and redacted",
            digest_changed_before_copy,
            "ArchiveVerificationFailed", "source-digest-changed-before-copy", storage,
            support.canary_tokens(original_digest,
                                  hashlib.sha256(
                                      (support.CANARY_SOURCE_BYTES + "-MUTATED")
                                      .encode("ascii")).hexdigest()))

    # 1891 - named seam: mutate the file while source_fingerprints hashes it
    src, actual = scenario("c1891")

    def changed_during_hashing():
        real_hash = storage.file_sha256

        def mutating_hash(path, *args, **kwargs):
            digest = real_hash(path, *args, **kwargs)
            Path(path).write_bytes(
                (support.CANARY_SOURCE_BYTES + "-DURING-HASH").encode("ascii"))
            return digest

        with mock.patch.object(storage, "file_sha256", mutating_hash):
            fresh_store().source_fingerprints([str(src)])

    _expect(clauses, "1891 source changed during hashing is typed and redacted",
            changed_during_hashing,
            "ArchiveSourceUnstable", "source-changed-during-hashing", storage,
            support.canary_tokens(actual, actual))

    # 1953 - named seam: mutate the source inside the verified copy
    src, actual = scenario("c1953")

    def changed_during_archival():
        store = fresh_store()
        record = _record(storage, src)
        real_copy = storage._atomic_copy_verified

        def mutating_copy(source, destination, expected):
            real_copy(source, destination, expected)
            Path(source).write_bytes(
                (support.CANARY_SOURCE_BYTES + "-DURING-ARCHIVAL").encode("ascii"))

        with mock.patch.object(storage, "_atomic_copy_verified", mutating_copy):
            store.archive_raw_telemetry([record])

    _expect(clauses, "1953 source changed during archival is typed and redacted",
            changed_during_archival,
            "ArchiveSourceUnstable", "source-changed-during-archival", storage,
            support.canary_tokens(actual, actual))


# --------------------------------------------------------------------------

def main() -> int:
    storage = load_storage()
    clauses = Clauses()

    with support.sandbox("ws10a-atomic-containment") as root:
        family_clauses(storage, clauses)
        containment_clauses(production_writer(storage), storage, root, clauses)
        archival_clauses(storage, root, clauses)

    report = {
        "schema": "ws10a-atomic-containment-v2",
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
