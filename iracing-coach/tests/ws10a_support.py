"""Shared harness support for WS-10A.

Not discovered: no ``test_`` prefix and no ``TestCase`` subclass. Every
primitive here is deliberately independent of the containment contract, so
it stays valid whichever way the retry/typed-failure details settle.

This module is also the STABLE clause API. The containment clause surface
lives here rather than in ``probe_ws10a_atomic_containment.py`` because the
accepted transition renames that probe to ``test_ws10a_containment.py``; a
discovered test importing the old filename would break at the rename. This
filename survives the transition, so both the probe and the discovered
harness import the clause surface from here.

Nothing here touches private data, the network, or production state.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from unittest import mock

RETRYABLE_WINERRORS = (5, 32)
SIGNAL_TIMEOUT = 120
JOIN_TIMEOUT = 60


# --------------------------------------------------------------------------
# deterministic naming
#
# Python randomises str/bytes hashing per process unless PYTHONHASHSEED is
# pinned, so hash() must never name a fixture: the same clause would write to
# a different path on every run and a cross-run comparison of exact evidence
# would be meaningless. This is a stable content digest instead.
# --------------------------------------------------------------------------

def stable_token(value: Any, length: int = 12) -> str:
    """A deterministic short token for ``value``, stable across processes."""
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:length]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# observed directory binding
#
# A temp-sweep check must compare against what the directory ACTUALLY held
# before the operation. Matching a guessed ".<stem>" prefix is fail-open: an
# implementation whose temporary file is named anything else - mkstemp's
# default "tmpXXXXXXXX", a sibling ".partial", an editor swap file - leaks a
# real artifact while the check reports a clean sweep.
# --------------------------------------------------------------------------

def snapshot(root: Path) -> set[str]:
    """Every entry name currently in ``root``."""
    return {entry.name for entry in root.iterdir()}


def new_entries(root: Path, before: set[str]) -> list[str]:
    """Everything that appeared in ``root`` since ``before`` was taken."""
    return sorted(snapshot(root) - before)


# --------------------------------------------------------------------------
# fault construction
# --------------------------------------------------------------------------

def make_os_error(winerror: Any, message: str = "injected", *,
                  kind: type[OSError] = PermissionError) -> OSError:
    """Build an OSError carrying an arbitrary ``winerror`` value.

    ``winerror`` is set verbatim, including non-integers, so the extraction
    rule can be attacked with ``True``, ``"32"``, and ``None``.
    """
    error = kind(message)
    error.winerror = winerror
    return error


def make_errno_only_error(errno_value: int, message: str = "injected") -> OSError:
    """An OSError with a matching errno but no winerror at all."""
    error = OSError(errno_value, message)
    if hasattr(error, "winerror"):
        try:
            delattr(error, "winerror")
        except AttributeError:
            pass
    return error


def make_nested_error(inner_winerror: int, *, via: str = "cause") -> OSError:
    """A wrapper whose only valid code hides in __cause__ or __context__.

    The extraction rule must NOT traverse into these.
    """
    inner = make_os_error(inner_winerror, "inner secret-bearing failure")
    outer = OSError("outer wrapper with no top-level winerror")
    if via == "cause":
        outer.__cause__ = inner
    else:
        outer.__context__ = inner
    return outer


class SubclassedPermissionError(PermissionError):
    """Subclass carrying a valid top-level code; must remain retryable."""


# --------------------------------------------------------------------------
# deterministic timing seams
# --------------------------------------------------------------------------

class RecordingSleeper:
    """Stands in for time.sleep; records rather than waits."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def total_ms(self) -> float:
        return sum(self.calls) * 1000.0

    def __len__(self) -> int:
        return len(self.calls)


BASE_DELAYS_MS = (10, 20, 40, 80)
MIN_TOTAL_MS = 150   # every base, zero jitter
MAX_TOTAL_MS = 300   # every base, full jitter


def fixed_jitter_ms(value: int) -> Callable[[int], int]:
    """Deterministic stand-in for the production jitter_ms(base_ms) -> int.

    Returns ``value`` regardless of base, so out-of-contract results
    (negative, above-base, boolean, float, string) can be injected to prove
    the validator refuses them instead of silently sleeping.
    """
    return lambda base_ms: value


def proportional_jitter_ms(fraction: float) -> Callable[[int], int]:
    """Integer jitter at a fixed fraction of each base; 0.0 -> min, 1.0 -> max."""
    return lambda base_ms: int(base_ms * fraction)


def is_valid_jitter_ms(value: Any, base_ms: int) -> bool:
    """The contract's validation rule, kept in one place.

    ``bool`` is excluded explicitly because ``isinstance(True, int)`` is
    True in Python and ``True`` would otherwise pass as 1 ms.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    return 0 <= value <= base_ms


INVALID_JITTER_RESULTS = (-1, 10_000, True, False, 5.0, "5", None)
MAX_ATTEMPTS = 5


# --------------------------------------------------------------------------
# reference implementations for conformance testing
#
# NOT production and never imported by production. These exist so the probe's
# clause logic can be run against deliberately non-conforming primitives and
# proven to REJECT them. A clause that passes a fake which does none of the
# work is fail-open, which is exactly the defect this machinery detects.
# --------------------------------------------------------------------------

class ReferenceStorageCommitError(OSError):
    code = "storage-commit-error"


class ReferenceAtomicReplaceExhausted(ReferenceStorageCommitError):
    code = "atomic-replace-exhausted"


class JitterContractError(AssertionError):
    """Raised when an injected jitter result violates the contract.

    Deliberately NOT an OSError: an invalid jitter is a harness/contract
    error and must never be mistaken for a replace failure or consume a
    retry attempt.
    """


LEAKED_TEMP_NAME = "unrelated-scratch.partial"


def top_level_retryable(error: BaseException) -> bool:
    """The accepted extraction rule: top level only, strict integer."""
    code = getattr(error, "winerror", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return False
    return code in RETRYABLE_WINERRORS


REFERENCE_VARIANTS = (
    "conforming",
    "ignores-jitter",
    "swallows-nonretry",
    "forges-fields",
    "leaks-temp",
)


def make_reference_primitive(variant: str = "conforming"):
    """A primitive with the accepted production seam signature.

    Variants deliberately break exactly one guarantee each:
      conforming        - implements the V4 contract
      ignores-jitter    - never calls jitter_ms, so never validates it
      swallows-nonretry - catches a non-retryable error and returns
      forges-fields     - one replace, zero sleeps, forged exhaustion fields
      leaks-temp        - fully conforming EXCEPT that it leaves behind a
                          temporary artifact whose name shares no prefix with
                          the destination. A prefix-guessing sweep check
                          cannot see it; an observed-snapshot check must.
    """
    if variant not in REFERENCE_VARIANTS:
        raise ValueError(f"unknown reference variant: {variant!r}")

    def primitive(path: Path, text: str, *, sleeper, jitter_ms) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        total_ms = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

            if variant == "leaks-temp":
                (path.parent / LEAKED_TEMP_NAME).write_bytes(b"residue\n")

            if variant == "forges-fields":
                try:
                    os.replace(temp_name, path)
                    return
                except OSError:
                    pass
                raise ReferenceAtomicReplaceExhausted(
                    "atomic replace exhausted after bounded retry"
                ) from None

            final_code = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    os.replace(temp_name, path)
                    return
                except OSError as exc:
                    if not top_level_retryable(exc):
                        if variant == "swallows-nonretry":
                            return
                        raise
                    final_code = exc.winerror
                if attempt == MAX_ATTEMPTS:
                    break
                base = BASE_DELAYS_MS[attempt - 1]
                if variant == "ignores-jitter":
                    delay = base
                else:
                    jitter = jitter_ms(base)
                    if not is_valid_jitter_ms(jitter, base):
                        raise JitterContractError(
                            f"jitter result {jitter!r} violates 0..{base}"
                        )
                    delay = base + jitter
                total_ms += delay
                sleeper(delay / 1000.0)

            # Leave the except block before raising so no implicit chaining
            # attaches the injected exception to the typed failure.
            error = ReferenceAtomicReplaceExhausted(
                "atomic replace exhausted after bounded retry"
            )
            error.attempts = MAX_ATTEMPTS
            error.total_slept_ms = total_ms
            error.final_winerror = final_code
            raise error
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    if variant == "forges-fields":
        real = primitive

        def forging(path: Path, text: str, *, sleeper, jitter_ms) -> None:
            try:
                real(path, text, sleeper=sleeper, jitter_ms=jitter_ms)
            except ReferenceAtomicReplaceExhausted as exc:
                exc.attempts = MAX_ATTEMPTS          # forged: only 1 happened
                exc.total_slept_ms = MIN_TOTAL_MS    # forged: nothing slept
                exc.final_winerror = RETRYABLE_WINERRORS[0]
                raise
        return forging

    return primitive


def make_leaking_primitive(token: str):
    """Conforming in every respect EXCEPT that it leaks ``token``.

    Used to prove that every newly bound atomic canary channel actually
    drives its clause unmet. The replacement failure is raised OUTSIDE the
    except block so ``__context__`` stays clear: a leaking primitive must
    fail the leak clause ALONE, otherwise the chain-suppression clause would
    fail too and could not be told apart from it.
    """
    conforming = make_reference_primitive("conforming")

    def primitive(path: Path, text: str, *, sleeper, jitter_ms) -> None:
        captured: ReferenceAtomicReplaceExhausted | None = None
        try:
            conforming(path, text, sleeper=sleeper, jitter_ms=jitter_ms)
        except ReferenceAtomicReplaceExhausted as exc:
            captured = exc
        if captured is None:
            return
        leaked = ReferenceAtomicReplaceExhausted(f"{captured} while committing {token}")
        leaked.attempts = captured.attempts
        leaked.total_slept_ms = captured.total_slept_ms
        leaked.final_winerror = captured.final_winerror
        raise leaked

    return primitive


class ReferenceTypes:
    """Namespace mirroring the production typed family, for fakes only."""

    StorageCommitError = ReferenceStorageCommitError
    AtomicReplaceExhausted = ReferenceAtomicReplaceExhausted


# --------------------------------------------------------------------------
# canaries - one distinct token per leak channel
# --------------------------------------------------------------------------

CANARY_SOURCE_PATH = "CANARY-SRCPATH-a1b2c3"
CANARY_DESTINATION_PATH = "CANARY-DSTPATH-b2c3d4"
CANARY_SOURCE_BYTES = "CANARY-CONTENT-d4e5f6"
CANARY_DECLARED_DIGEST = "CANARY-BADDIGEST-e5f6a7"
CANARY_INJECTED = "CANARY-INJECTED-97a8b9"

# atomic retry/exhaustion endpoints
CANARY_DESTINATION_NAME = "CANARY-DSTNAME-c3d4e5"
CANARY_ORIGINAL_CONTENT = "CANARY-ORIGINAL-f6a7b8"
CANARY_REPLACEMENT_CONTENT = "CANARY-REPLACEMENT-a7b8c9"

CANARY_CHANNELS = (
    "source_path",
    "destination_path",
    "source_bytes",
    "declared_digest",
    "expected_digest",
    "actual_digest",
    "injected",
)

ARCHIVAL_CANARY_CHANNELS = CANARY_CHANNELS + ("post_bytes",)

ATOMIC_CANARY_CHANNELS = (
    "destination_root",
    "destination_name",
    "original_content",
    "replacement_content",
    "original_digest",
    "replacement_digest",
    "injected",
)


def canary_tokens(expected_digest: str, actual_digest: str) -> dict[str, str]:
    """Every token that must not survive into a raised failure.

    The two digests are supplied by the caller because they are computed from
    the synthetic fixture at run time. They are included because a digest of a
    private file is possession-confirming material.
    """
    return {
        "source_path": CANARY_SOURCE_PATH,
        "destination_path": CANARY_DESTINATION_PATH,
        "source_bytes": CANARY_SOURCE_BYTES,
        "declared_digest": CANARY_DECLARED_DIGEST,
        "expected_digest": expected_digest,
        "actual_digest": actual_digest,
        "injected": CANARY_INJECTED,
    }


# --------------------------------------------------------------------------
# archival scenarios as data
#
# The completeness regression asserts against THESE maps rather than an
# unrelated synthetic pair, because a synthetic map cannot detect a scenario
# whose expected and actual digests were both bound to the pre-mutation
# bytes: the post-mutation digest would simply never appear in the map, and
# leaking it would be invisible.
# --------------------------------------------------------------------------

VALID_BUT_WRONG_SHA = "0" * 64


class ArchivalScenario:
    """One archival redaction site and the exact bytes it moves through."""

    def __init__(self, key: str, label: str, klass_name: str, code: str,
                 pre_bytes: bytes, post_bytes: bytes, declared: str,
                 shared: Sequence[tuple[Sequence[str], str]]) -> None:
        self.key = key
        self.label = label
        self.klass_name = klass_name
        self.code = code
        self.pre_bytes = pre_bytes
        self.post_bytes = post_bytes
        self.declared = declared
        # Groups of channels that legitimately carry the SAME token, with the
        # reason. Recorded as unordered groups so the completeness regression
        # compares sets rather than depending on declaration order. Claiming
        # eight distinct tokens would be false for every scenario that does not
        # mutate its bytes, and a false distinctness claim is the kind of
        # assertion that passes while proving nothing.
        self.shared: tuple[tuple[frozenset[str], str], ...] = tuple(
            (frozenset(channels), reason) for channels, reason in shared
        )

    @property
    def shared_groups(self) -> set[frozenset[str]]:
        return {channels for channels, _ in self.shared}

    def observed_shared_groups(self) -> set[frozenset[str]]:
        """The channel groups that actually collide in this scenario's map."""
        by_token: dict[str, set[str]] = {}
        for channel, token in self.tokens().items():
            by_token.setdefault(token, set()).add(channel)
        return {frozenset(channels) for channels in by_token.values()
                if len(channels) > 1}

    @property
    def expected_digest(self) -> str:
        """The digest of the bytes as they were BEFORE the scenario's mutation."""
        return sha256_bytes(self.pre_bytes)

    @property
    def actual_digest(self) -> str:
        """The digest of the bytes as they are AFTER the scenario's mutation."""
        return sha256_bytes(self.post_bytes)

    def tokens(self) -> dict[str, str]:
        return {
            "source_path": CANARY_SOURCE_PATH,
            "destination_path": CANARY_DESTINATION_PATH,
            "source_bytes": CANARY_SOURCE_BYTES,
            "post_bytes": self.post_bytes.decode("ascii"),
            "declared_digest": self.declared,
            "expected_digest": self.expected_digest,
            "actual_digest": self.actual_digest,
            "injected": CANARY_INJECTED,
        }


def _content(suffix: str = "") -> bytes:
    return (CANARY_SOURCE_BYTES + suffix).encode("ascii")


_UNCHANGED = "the scenario performs no mutation, so pre and post bytes are identical"
_DECLARED_IS_REAL = "the scenario declares the real pre-mutation digest"

_UNMUTATED_GROUPS = (
    (("source_bytes", "post_bytes"), _UNCHANGED),
    (("expected_digest", "actual_digest"), _UNCHANGED),
)

ARCHIVAL_SCENARIOS = (
    ArchivalScenario(
        "c1915", "1915 invalid source digest",
        "InvalidSourceDigest", "invalid-source-digest",
        _content(), _content(), CANARY_DECLARED_DIGEST,
        _UNMUTATED_GROUPS,
    ),
    ArchivalScenario(
        "c1925", "1925 source changed before archival",
        "ArchiveSourceUnstable", "source-changed-before-archival",
        _content(), _content(), sha256_bytes(_content()),
        (
            (("source_bytes", "post_bytes"), _UNCHANGED),
            (("declared_digest", "expected_digest", "actual_digest"),
             _DECLARED_IS_REAL + " and performs no mutation"),
        ),
    ),
    ArchivalScenario(
        "c495", "495 archived digest mismatch",
        "ArchiveVerificationFailed", "archived-digest-mismatch",
        _content(), _content(), VALID_BUT_WRONG_SHA,
        _UNMUTATED_GROUPS,
    ),
    ArchivalScenario(
        "c481", "481 source digest changed before copy",
        "ArchiveVerificationFailed", "source-digest-changed-before-copy",
        _content(), _content("-MUTATED"), sha256_bytes(_content()),
        ((("declared_digest", "expected_digest"), _DECLARED_IS_REAL),),
    ),
    ArchivalScenario(
        "c1891", "1891 source changed during hashing",
        "ArchiveSourceUnstable", "source-changed-during-hashing",
        _content(), _content("-DURING-HASH"), sha256_bytes(_content()),
        ((("declared_digest", "expected_digest"), _DECLARED_IS_REAL),),
    ),
    ArchivalScenario(
        "c1953", "1953 source changed during archival",
        "ArchiveSourceUnstable", "source-changed-during-archival",
        _content(), _content("-DURING-ARCHIVAL"), sha256_bytes(_content()),
        ((("declared_digest", "expected_digest"), _DECLARED_IS_REAL),),
    ),
)

MUTATING_SCENARIO_KEYS = ("c481", "c1891", "c1953")


def atomic_canary_tokens(original: bytes, replacement: bytes) -> dict[str, str]:
    """Every token reachable from an atomic retry/exhaustion failure.

    Both endpoints of the schedule carry the same channel set, so exhaustion
    at the minimum and the maximum are scanned identically.
    """
    return {
        "destination_root": CANARY_DESTINATION_PATH,
        "destination_name": CANARY_DESTINATION_NAME,
        "original_content": CANARY_ORIGINAL_CONTENT,
        "replacement_content": CANARY_REPLACEMENT_CONTENT,
        "original_digest": sha256_bytes(original),
        "replacement_digest": sha256_bytes(replacement),
        "injected": CANARY_INJECTED,
    }


ATOMIC_ORIGINAL_BYTES = (CANARY_ORIGINAL_CONTENT + "\n").encode("utf-8")
ATOMIC_REPLACEMENT_TEXT = CANARY_REPLACEMENT_CONTENT + "\n"
ATOMIC_REPLACEMENT_BYTES = ATOMIC_REPLACEMENT_TEXT.encode("utf-8")


# --------------------------------------------------------------------------
# replace-fault injection
# --------------------------------------------------------------------------

class ReplaceFaults:
    """Scripted os.replace outcomes.

    ``script`` yields either an exception to raise or None to pass through
    to the real os.replace. Exhausting the script falls through to real
    behavior, so 'fail twice then succeed' is expressed as [err, err].
    """

    def __init__(self, script: Sequence[BaseException | None]) -> None:
        self._script = list(script)
        self.attempts: list[tuple[str, str]] = []

    def __call__(self, src, dst, *args, **kwargs):
        self.attempts.append((str(src), str(dst)))
        if self._script:
            outcome = self._script.pop(0)
            if outcome is not None:
                raise outcome
        return _REAL_REPLACE(src, dst, *args, **kwargs)

    @property
    def count(self) -> int:
        return len(self.attempts)


_REAL_REPLACE = os.replace


@contextmanager
def injected_replace(faults: ReplaceFaults):
    """Patch os.replace for the duration of the block.

    storage.py resolves os.replace through the shared module object, so this
    reaches it without any production seam. Verified against both
    _atomic_write_text and _atomic_copy_verified.
    """
    with mock.patch.object(os, "replace", faults):
        yield faults


# --------------------------------------------------------------------------
# redaction canaries
# --------------------------------------------------------------------------

def exception_surface(error: BaseException) -> str:
    """Everything a generic logger or crash reporter could reach."""
    parts = [
        str(error),
        repr(getattr(error, "args", ())),
        repr(getattr(error, "__dict__", {})),
    ]
    for attribute in ("__cause__", "__context__"):
        chained = getattr(error, attribute, None)
        parts.append(f"{attribute}={chained!r}")
        if chained is not None:
            parts.append(str(chained))
            parts.append(repr(getattr(chained, "args", ())))
    return " ".join(parts)


def find_canaries(error: BaseException, canaries: Iterable[str]) -> list[str]:
    """Return every canary recoverable from the exception surface."""
    surface = exception_surface(error)
    return sorted(c for c in canaries if c in surface)


# --------------------------------------------------------------------------
# sandboxes, removed by exact literal path
# --------------------------------------------------------------------------

@contextmanager
def sandbox(name: str):
    root = Path(tempfile.gettempdir()) / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        if root.exists():
            shutil.rmtree(root)


# --------------------------------------------------------------------------
# clause recording
# --------------------------------------------------------------------------

class Clauses:
    """Records one met/unmet verdict per contract clause.

    ``check`` converts an exception from the clause body into an UNMET
    record rather than propagating it. That is deliberate and load-bearing:
    at a parent commit where the contract is absent, ``getattr`` on a missing
    typed class raises, and the probe must still report every other clause
    individually instead of aborting on the first one. It is also the single
    most dangerous line in the harness - if it ever recorded ``met=True`` on
    exception, an entirely absent contract would report as satisfied - so the
    discovered harness mutation-tests this branch directly.
    """

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


RESIDUE_CLAUSE = "no unexpected filesystem residue"


# --------------------------------------------------------------------------
# retry - bound to observed replace and sleep calls
# --------------------------------------------------------------------------

def retry_clauses(write, types, root: Path, clauses: Clauses) -> None:
    for code in RETRYABLE_WINERRORS:
        def probe(code=code):
            holder = root / f"retry-{code}"
            holder.mkdir(parents=True, exist_ok=True)
            target = holder / f"{CANARY_DESTINATION_NAME}-retry-{code}.json"
            before = snapshot(holder)
            faults = ReplaceFaults([make_os_error(code), make_os_error(code)])
            sleeper = RecordingSleeper()
            with injected_replace(faults):
                write(target, ATOMIC_REPLACEMENT_TEXT, sleeper=sleeper,
                      jitter_ms=proportional_jitter_ms(0.0))
            expected = [BASE_DELAYS_MS[0] / 1000.0, BASE_DELAYS_MS[1] / 1000.0]
            appeared = new_entries(holder, before)
            return (
                faults.count == 3
                and target.read_bytes() == ATOMIC_REPLACEMENT_BYTES
                and sleeper.calls == expected
                and appeared == [target.name],
                f"replaces={faults.count} sleeps={sleeper.calls} "
                f"expected={expected} appeared={appeared}",
            )

        clauses.check(
            f"winerror {code} retries with the exact delay schedule and then succeeds",
            probe,
        )


# --------------------------------------------------------------------------
# invalid jitter must be attacked through the production seam
# --------------------------------------------------------------------------

def jitter_clauses(write, types, root: Path, clauses: Clauses) -> None:
    for value in INVALID_JITTER_RESULTS:
        holder = root / f"jitter-{stable_token(value)}"
        holder.mkdir(parents=True, exist_ok=True)
        target = holder / f"{CANARY_DESTINATION_NAME}-jitter.json"
        target.write_bytes(ATOMIC_ORIGINAL_BYTES)
        before = snapshot(holder)
        # one retryable failure forces the primitive to compute a delay,
        # which is the only point at which jitter is consulted
        faults = ReplaceFaults([
            make_os_error(RETRYABLE_WINERRORS[0]) for _ in range(MAX_ATTEMPTS)
        ])
        sleeper = RecordingSleeper()
        raised: BaseException | None = None
        with injected_replace(faults):
            try:
                write(target, ATOMIC_REPLACEMENT_TEXT, sleeper=sleeper,
                      jitter_ms=fixed_jitter_ms(value))
            except BaseException as exc:
                raised = exc

        clauses.check(
            f"invalid jitter {value!r} fails as a contract error before a second replace",
            lambda raised=raised, faults=faults, sleeper=sleeper, target=target: (
                raised is not None
                and not isinstance(raised, OSError)
                and faults.count == 1
                and sleeper.calls == []
                and target.read_bytes() == ATOMIC_ORIGINAL_BYTES,
                f"raised={type(raised).__name__} replaces={faults.count} "
                f"sleeps={len(sleeper)} "
                f"dest_intact={target.read_bytes() == ATOMIC_ORIGINAL_BYTES}",
            ),
        )
        clauses.check(
            f"invalid jitter {value!r} leaves {RESIDUE_CLAUSE}",
            lambda holder=holder, before=before: (
                new_entries(holder, before) == [],
                f"appeared={new_entries(holder, before)}",
            ),
        )


# --------------------------------------------------------------------------
# non-retry - the error must ESCAPE, not merely not-retry
# --------------------------------------------------------------------------

def nonretry_clauses(write, types, root: Path, clauses: Clauses) -> None:
    cases = {
        "winerror 13": lambda: make_os_error(13),
        "winerror True": lambda: make_os_error(True),
        "winerror '32'": lambda: make_os_error("32"),
        "errno-only 5": lambda: make_errno_only_error(5),
        "nested cause 32": lambda: make_nested_error(32, via="cause"),
        "nested context 32": lambda: make_nested_error(32, via="context"),
    }
    for label, factory in cases.items():
        error = factory()
        holder = root / f"nonretry-{stable_token(label)}"
        holder.mkdir(parents=True, exist_ok=True)
        target = holder / f"{CANARY_DESTINATION_NAME}-nonretry.json"
        target.write_bytes(ATOMIC_ORIGINAL_BYTES)
        before = snapshot(holder)
        faults = ReplaceFaults([error] * MAX_ATTEMPTS)
        sleeper = RecordingSleeper()
        raised: BaseException | None = None
        with injected_replace(faults):
            try:
                write(target, ATOMIC_REPLACEMENT_TEXT, sleeper=sleeper,
                      jitter_ms=proportional_jitter_ms(0.0))
            except BaseException as exc:
                raised = exc

        clauses.check(
            f"{label} escapes unwrapped after exactly one replace",
            lambda raised=raised, error=error, faults=faults, sleeper=sleeper: (
                raised is error          # the exact object escaped, unwrapped
                and faults.count == 1
                and sleeper.calls == [],
                f"escaped={raised is error} raised={type(raised).__name__} "
                f"replaces={faults.count} sleeps={len(sleeper)}",
            ),
        )
        clauses.check(
            f"{label} leaves {RESIDUE_CLAUSE}",
            lambda holder=holder, before=before, target=target: (
                new_entries(holder, before) == []
                and target.read_bytes() == ATOMIC_ORIGINAL_BYTES,
                f"appeared={new_entries(holder, before)} "
                f"dest_intact={target.read_bytes() == ATOMIC_ORIGINAL_BYTES}",
            ),
        )


# --------------------------------------------------------------------------
# exhaustion - bound to observed calls at both schedule endpoints
# --------------------------------------------------------------------------

def exhaustion_clauses(write, types, root: Path, clauses: Clauses) -> None:
    endpoints = (
        ("minimum", proportional_jitter_ms(0.0),
         [b / 1000.0 for b in BASE_DELAYS_MS], MIN_TOTAL_MS),
        ("maximum", proportional_jitter_ms(1.0),
         [2 * b / 1000.0 for b in BASE_DELAYS_MS], MAX_TOTAL_MS),
    )
    tokens = atomic_canary_tokens(ATOMIC_ORIGINAL_BYTES, ATOMIC_REPLACEMENT_BYTES)

    for label, jitter, expected_sleeps, expected_total in endpoints:
        # Both endpoints write through a canary-bearing destination directory
        # and a canary-bearing destination filename. The production temp file
        # is created in that directory with the destination name embedded in
        # its prefix, so the temporary endpoint carries the same tokens: a
        # leaked temp path is detectable by exactly this scan.
        holder = root / CANARY_DESTINATION_PATH / f"exhausted-{label}"
        holder.mkdir(parents=True, exist_ok=True)
        target = holder / f"{CANARY_DESTINATION_NAME}-{label}.json"
        target.write_bytes(ATOMIC_ORIGINAL_BYTES)
        before = snapshot(holder)

        faults = ReplaceFaults([
            make_os_error(RETRYABLE_WINERRORS[0], f"denied {CANARY_INJECTED}")
            for _ in range(MAX_ATTEMPTS)
        ])
        sleeper = RecordingSleeper()
        raised: BaseException | None = None
        with injected_replace(faults):
            try:
                write(target, ATOMIC_REPLACEMENT_TEXT, sleeper=sleeper,
                      jitter_ms=jitter)
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
            gated(lambda faults=faults: (faults.count == MAX_ATTEMPTS,
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
                getattr(raised, "final_winerror", None) == RETRYABLE_WINERRORS[0],
                f"final_winerror={getattr(raised, 'final_winerror', None)}")),
        )
        clauses.check(
            f"{label}: destination bytes are unchanged",
            gated(lambda target=target: (
                target.read_bytes() == ATOMIC_ORIGINAL_BYTES,
                repr(target.read_bytes()))),
        )
        clauses.check(
            f"{label} leaves {RESIDUE_CLAUSE}",
            gated(lambda holder=holder, before=before: (
                new_entries(holder, before) == [],
                f"appeared={new_entries(holder, before)}")),
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
            f"{label}: no canary channel survives into the failure",
            gated(lambda raised=raised, tokens=tokens: (
                find_canaries(raised, tokens.values()) == [],
                f"leaked={find_canaries(raised, tokens.values())}")),
        )


def containment_clauses(write, types, root: Path, clauses: Clauses) -> None:
    """Everything that can be run against an arbitrary primitive."""
    retry_clauses(write, types, root, clauses)
    jitter_clauses(write, types, root, clauses)
    nonretry_clauses(write, types, root, clauses)
    exhaustion_clauses(write, types, root, clauses)


# --------------------------------------------------------------------------
# redaction verdicts, split so each is independently degradable
#
# V2 folded type, code, chain suppression, and leakage into ONE boolean. A
# combined verdict cannot distinguish "wrong exception type" from "leaked a
# digest", and a mutation that breaks one of them is masked whenever another
# is already failing. Each is now its own clause with its own record.
# --------------------------------------------------------------------------

REDACTION_VERDICTS = ("raises", "type", "code", "chain", "redaction")


def redaction_clauses(clauses: Clauses, label: str, invoke, klass,
                      code: str, tokens: Mapping[str, str]) -> None:
    raised: BaseException | None = None
    try:
        invoke()
    except BaseException as exc:
        raised = exc

    clauses.check(
        f"{label}: the site actually raises",
        lambda: (raised is not None,
                 "no exception was raised; the site was not exercised"
                 if raised is None else type(raised).__name__),
    )
    clauses.check(
        f"{label}: the failure is typed",
        lambda: (raised is not None and isinstance(raised, klass),
                 f"type={type(raised).__name__} expected={getattr(klass, '__name__', klass)}"),
    )
    clauses.check(
        f"{label}: the public code is exact",
        lambda: (raised is not None and getattr(raised, "code", None) == code,
                 f"code={getattr(raised, 'code', None)!r} expected={code!r}"),
    )
    clauses.check(
        f"{label}: __cause__ and __context__ are suppressed",
        lambda: (raised is not None
                 and getattr(raised, "__cause__", None) is None
                 and getattr(raised, "__context__", None) is None,
                 f"cause={getattr(raised, '__cause__', None)!r} "
                 f"context={getattr(raised, '__context__', None)!r}"),
    )
    clauses.check(
        f"{label}: no canary channel survives into the failure",
        lambda: (raised is not None
                 and find_canaries(raised, tokens.values()) == [],
                 "not raised" if raised is None else
                 f"leaked_channels="
                 f"{sorted(k for k, v in tokens.items() if v in exception_surface(raised))}"),
    )


# --------------------------------------------------------------------------
# deliberately degraded redaction sites, one verdict broken each
# --------------------------------------------------------------------------

class ReferenceRedactionError(OSError):
    code = "reference-redaction"


class ReferenceImposterError(OSError):
    """Carries the right public code but is NOT in the expected family."""

    code = "reference-redaction"


REDACTION_VARIANTS = ("clean", "no-raise", "wrong-type", "wrong-code",
                      "chained", "leaks")


def make_reference_raiser(variant: str, token: str):
    """A redaction site degrading exactly one verdict.

    ``wrong-type`` deliberately keeps the correct public ``code`` so that the
    type verdict fails ALONE; an imposter without the code would fail two
    verdicts at once and the two mutations could not be told apart.
    """
    if variant not in REDACTION_VARIANTS:
        raise ValueError(f"unknown redaction variant: {variant!r}")

    def invoke() -> None:
        if variant == "no-raise":
            return
        if variant == "wrong-type":
            raise ReferenceImposterError("refused")
        error = ReferenceRedactionError(
            f"refused, carrying {token}" if variant == "leaks" else "refused"
        )
        if variant == "wrong-code":
            error.code = "unexpected-code"
        if variant == "chained":
            error.__cause__ = OSError("the underlying cause was retained")
        raise error

    return invoke


# --------------------------------------------------------------------------
# staged cross-process read/merge/replace
# --------------------------------------------------------------------------

def _plain_atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def union_fingerprint(observations: Iterable[str]) -> str:
    return json.dumps(sorted(observations), sort_keys=True)


REQUIRED_START_METHOD = "spawn"


def stages_clean(outcome: Mapping[str, Any]) -> bool:
    """The apparatus conditions the durable reproduction depends on.

    Defined once and used by both the probe and its regression test, so that
    weakening it fails a discovered test. A test that re-stated this predicate
    inline would pass even after the probe stopped enforcing it.

    ``spawn`` is enforced rather than merely reported: under ``fork`` the
    workers would inherit state instead of re-reading, so the reproduction
    would not mean what it claims.
    """
    return (
        outcome["start_method"] == REQUIRED_START_METHOD
        and outcome["all_ready"]
        and outcome["all_read_initial"]
        and outcome["acknowledged_in_order"]
        and outcome["acknowledged_clean"]
        and not outcome["hung"]
        and not outcome["bad_exits"]
    )


def _rmw_worker(index, observation, path_text, ready_q, read_q,
                start_read, release_event, done_q):
    path = Path(path_text)
    ready_q.put(index)
    start_read.wait(timeout=SIGNAL_TIMEOUT)

    payload = json.loads(path.read_text(encoding="utf-8"))
    read_q.put((index, union_fingerprint(payload["observations"])))

    release_event.wait(timeout=SIGNAL_TIMEOUT)
    payload["observations"].append(observation)
    # The acknowledgement carries an explicit per-worker clean status. A bare
    # index would let a failed replace acknowledge as though it had succeeded,
    # with the failure only surfacing later at the exit-code check.
    try:
        _plain_atomic_write(path, json.dumps(payload, sort_keys=True) + "\n")
    except BaseException as exc:
        done_q.put((index, f"failed:{type(exc).__name__}"))
        raise
    done_q.put((index, "clean"))


def run_staged_rmw(n: int, order: str, path: Path) -> dict[str, Any]:
    """Stages 1-6 of the bound schedule. No sleep carries correctness.

    Returns the observed outcome plus the survivor predicted from the
    release order before the run.
    """
    if order not in {"forward", "reverse"}:
        raise ValueError("order must be 'forward' or 'reverse'")

    initial: dict[str, list[str]] = {"observations": []}
    _plain_atomic_write(path, json.dumps(initial, sort_keys=True) + "\n")
    initial_fingerprint = union_fingerprint(initial["observations"])

    ready_q, read_q, done_q = mp.Queue(), mp.Queue(), mp.Queue()
    start_read = mp.Event()
    release = [mp.Event() for _ in range(n)]
    observations = [f"obs-{i:03d}" for i in range(n)]

    processes = [
        mp.Process(
            target=_rmw_worker,
            args=(i, observations[i], str(path), ready_q, read_q,
                  start_read, release[i], done_q),
        )
        for i in range(n)
    ]
    for process in processes:
        process.start()

    ready = {ready_q.get(timeout=SIGNAL_TIMEOUT) for _ in range(n)}
    start_read.set()

    reads = [read_q.get(timeout=SIGNAL_TIMEOUT) for _ in range(n)]
    all_read_initial = all(fp == initial_fingerprint for _, fp in reads)

    sequence = list(range(n)) if order == "forward" else list(reversed(range(n)))
    # Record the acknowledgements instead of tracking a boolean initialised to
    # the passing value. If the wait is ever removed, this list stays empty and
    # the comparison below fails - whereas a True flag would have survived
    # untouched and reported a handshake that never happened.
    acknowledged: list[tuple[int, str]] = []
    for index in sequence:
        release[index].set()
        acknowledged.append(done_q.get(timeout=SIGNAL_TIMEOUT))
    expected_acknowledgements = [(index, "clean") for index in sequence]

    for process in processes:
        process.join(timeout=JOIN_TIMEOUT)

    survived = json.loads(path.read_text(encoding="utf-8"))["observations"]
    predicted_survivor = observations[sequence[-1]]

    return {
        "n": n,
        "order": order,
        "start_method": mp.get_start_method(),
        "all_ready": ready == set(range(n)),
        "all_read_initial": all_read_initial,
        "acknowledged_in_order": acknowledged == expected_acknowledgements,
        "acknowledged_clean": all(status == "clean" for _, status in acknowledged)
                              and len(acknowledged) == n,
        "hung": [i for i, p in enumerate(processes) if p.is_alive()],
        "bad_exits": [p.exitcode for p in processes if p.exitcode != 0],
        "survived": survived,
        "expected_union": sorted(observations),
        "lost": len(set(observations) - set(survived)),
        "predicted_survivor": predicted_survivor,
        "sole_survivor_is_last_released": survived == [predicted_survivor],
    }
