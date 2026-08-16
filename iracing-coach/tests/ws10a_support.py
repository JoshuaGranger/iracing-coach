"""Shared harness support for WS-10A.

Not discovered: no ``test_`` prefix and no ``TestCase`` subclass. Every
primitive here is deliberately independent of the containment contract, so
it stays valid whichever way the retry/typed-failure details settle.

Nothing here touches private data, the network, or production state.
"""
from __future__ import annotations

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


def top_level_retryable(error: BaseException) -> bool:
    """The accepted extraction rule: top level only, strict integer."""
    code = getattr(error, "winerror", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return False
    return code in RETRYABLE_WINERRORS


def make_reference_primitive(variant: str = "conforming"):
    """A primitive with the accepted production seam signature.

    Variants deliberately break exactly one guarantee each:
      conforming        - implements the V4 contract
      ignores-jitter    - never calls jitter_ms, so never validates it
      swallows-nonretry - catches a non-retryable error and returns
      forges-fields     - one replace, zero sleeps, forged exhaustion fields
    """

    def primitive(path: Path, text: str, *, sleeper, jitter_ms) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        total_ms = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

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

CANARY_CHANNELS = (
    "source_path",
    "destination_path",
    "source_bytes",
    "declared_digest",
    "expected_digest",
    "actual_digest",
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
