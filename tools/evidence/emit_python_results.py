#!/usr/bin/env python3
"""Run a unittest family and atomically emit machine-readable raw results."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
import uuid


SCHEMA_VERSION = 1
FAMILIES = ("backend", "devtools")


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class StructuredResult(unittest.TextTestResult):
    """Record outcomes without persisting tracebacks, source, or environment data."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, object]] = []
        self._started: dict[int, float] = {}
        self._recorded: set[int] = set()

    def startTest(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        self._started[id(test)] = time.perf_counter()
        super().startTest(test)

    def _duration_ms(self, test: unittest.case.TestCase) -> float:
        started = self._started.get(id(test), time.perf_counter())
        return round(max(0.0, (time.perf_counter() - started) * 1000.0), 3)

    def _record(self, test: unittest.case.TestCase, outcome: str, skip_reason: str | None = None) -> None:
        key = id(test)
        if key in self._recorded:
            return
        self._recorded.add(key)
        identity = test.id()
        self.records.append(
            {
                "id": identity,
                "displayId": identity,
                "outcome": outcome,
                "durationMs": self._duration_ms(test),
                "skipReason": skip_reason,
            }
        )

    def addSuccess(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test: unittest.case.TestCase, err: object) -> None:  # noqa: N802
        super().addFailure(test, err)
        self._record(test, "failed")

    def addError(self, test: unittest.case.TestCase, err: object) -> None:  # noqa: N802
        super().addError(test, err)
        self._record(test, "failed")

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:  # noqa: N802
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(self, test: unittest.case.TestCase, err: object) -> None:  # noqa: N802
        super().addExpectedFailure(test, err)
        self._record(test, "skipped", "expected-failure")

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        super().addUnexpectedSuccess(test)
        self._record(test, "failed")


def _invalid(family: str, filter_expression: str | None, reason: str) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "family": family,
        "discoveryComplete": False,
        "runState": "invalid",
        "filter": filter_expression,
        "results": [],
        "failure": reason,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--start-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--filter")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    start_directory = arguments.start_dir.resolve()
    output = arguments.out.resolve()
    loader = unittest.TestLoader()
    if arguments.filter:
        loader.testNamePatterns = [arguments.filter]

    try:
        suite = loader.discover(str(start_directory), pattern=arguments.pattern)
        if loader.errors:
            _atomic_json(output, _invalid(arguments.family, arguments.filter, "discovery-error"))
            return 2
        discovered = suite.countTestCases()
        if discovered <= 0:
            _atomic_json(output, _invalid(arguments.family, arguments.filter, "zero-discovery"))
            return 2

        stream = io.StringIO()
        runner = unittest.TextTestRunner(
            stream=stream,
            verbosity=1,
            resultclass=StructuredResult,
            buffer=False,
        )
        result = runner.run(suite)
        if not isinstance(result, StructuredResult):
            _atomic_json(output, _invalid(arguments.family, arguments.filter, "unexpected-result-type"))
            return 2

        records = sorted(result.records, key=lambda item: (str(item["id"]), str(item["displayId"])))
        if len(records) != discovered:
            _atomic_json(output, _invalid(arguments.family, arguments.filter, "incomplete-result-set"))
            return 2

        record: dict[str, object] = {
            "schemaVersion": SCHEMA_VERSION,
            "family": arguments.family,
            "discoveryComplete": True,
            "runState": "partial" if arguments.filter else "complete",
            "filter": arguments.filter,
            "results": records,
            "failure": None,
        }
        _atomic_json(output, record)
        return 0 if result.wasSuccessful() else 1
    except BaseException as error:  # the raw producer must fail closed on interruption
        reason = "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "runner-error"
        try:
            _atomic_json(output, _invalid(arguments.family, arguments.filter, reason))
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
