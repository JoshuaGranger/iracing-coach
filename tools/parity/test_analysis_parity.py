"""Regression tests for the parity gate itself (Codex Sol phase-1 review, findings 8 to 10).

Run with the oracle interpreter: ``python -X utf8 -m unittest tools/parity/test_analysis_parity.py``.
The end-to-end golden case runs when ``IRACING_COACH_NATIVE_BIN`` names ``coach-analysis``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis_parity as ap  # noqa: E402

sys.path.insert(0, str(ap.SCRIPTS))
import ibt_reader  # noqa: E402


def _completed(code: int, stdout: Any = b"", stderr: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    if not isinstance(stdout, bytes):
        stdout = json.dumps(stdout).encode("utf-8")
    return subprocess.CompletedProcess(["coach-analysis", "golden"], code, stdout, stderr)


def _truncated() -> BaseException:
    try:
        ibt_reader.load_telemetry(ap.FIXTURES / "truncated-race.ibt", target_hz=None)
    except ibt_reader.IbtTruncatedError as exc:
        return exc
    raise AssertionError("truncated-race.ibt was accepted")


TRUNCATION = {"region": "telemetry sample buffers", "start": 1856, "end": 7616, "available": 7615}


class GoldenRejectionTests(unittest.TestCase):
    """Finding 9: a native rejection counts only when it is the oracle's."""

    def test_the_same_rejection_passes(self) -> None:
        record = {"class": "IbtTruncatedError", "message": "coach-ibt wording", "truncated": TRUNCATION}
        self.assertEqual(ap.rejection_differences(_truncated(), ap.native_rejection(_completed(1, {"error": record}))), [])
        overflow = OverflowError("cannot convert float infinity to integer")
        record = {"class": "OverflowError", "message": str(overflow), "truncated": None}
        self.assertEqual(ap.rejection_differences(overflow, record), [])

    def test_a_crash_or_unrelated_failure_fails(self) -> None:
        oracle = _truncated()
        for completed in (
            _completed(101, b"", b"thread 'main' panicked"),  # panic
            _completed(1, b"", b"coach-analysis golden: I/O: denied"),  # no recorded rejection
            _completed(2, b"", b"usage"),
            _completed(1, b"not json"),
        ):
            with self.subTest(code=completed.returncode, stdout=completed.stdout):
                self.assertTrue(ap.rejection_differences(oracle, ap.native_rejection(completed)))
        for record in (
            {"class": None, "message": "column store: header", "truncated": None},
            {"class": "IbtBoundsError", "message": "x", "truncated": None},
            {"class": "FileNotFoundError", "message": "I/O", "truncated": None},
        ):
            with self.subTest(record=record):
                self.assertTrue(ap.rejection_differences(oracle, record))

    def test_stated_facts_and_own_messages_must_match(self) -> None:
        for change in ({"start": 1855}, {"end": 7617}, {"available": 7614}, {"region": "variable headers"}):
            record = {"class": "IbtTruncatedError", "message": "x", "truncated": {**TRUNCATION, **change}}
            with self.subTest(change=change):
                self.assertTrue(ap.rejection_differences(_truncated(), record))
        record = {"class": "IbtTruncatedError", "message": "x", "truncated": None}
        self.assertTrue(ap.rejection_differences(_truncated(), record))
        value_error = ValueError("target_hz must be a positive finite number or None")
        record = {"class": "ValueError", "message": "target_hz must be positive", "truncated": None}
        self.assertTrue(ap.rejection_differences(value_error, record))

    @unittest.skipUnless(os.environ.get(ap.NATIVE_BIN_ENV), "coach-analysis not configured")
    def test_gate_on_the_truncated_fixture(self) -> None:
        results = ap._decode_golden_parity(ap.FIXTURES / "truncated-race.ibt", [None, 20.0])
        for label, result in results.items():
            with self.subTest(label=label):
                self.assertTrue(result["identical"], result)
                self.assertEqual(result["rejection_differences"], [])


class SqliteRawDumpTests(unittest.TestCase):
    """Finding 10: raw mode compares SQLite values under the raw allowlist only."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="parity-sqlite-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, True))
        self.roots = ap.Roots((("<archive_root>", self.directory / "archive"),))

    def dump(self, name: str, row: dict[str, Any]) -> str:
        path = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=self.directory)) / "history.sqlite3"
        connection = sqlite3.connect(path)
        with connection:
            connection.execute(f"CREATE TABLE t ({', '.join(row)})")
            connection.execute(f"INSERT INTO t VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        connection.close()
        counts: dict[str, int] = {}
        return ap.canonical_json(
            ap._sqlite_dump(path, lambda column, value: ap.raw_sqlite_value(column, value, counts, self.roots)),
            sort_keys=True,
        )

    def test_values_outside_the_allowlist_stay_visible(self) -> None:
        for column, left, right in (
            ("created_at", "corrupt-A", "corrupt-B"),
            ("last_updated", "2026-10-03T10:00:00+00:00", "2026-10-03T10:00:01+00:00"),
            ("analysis_sha256", "not-a-hash-A", "not-a-hash-B"),
            ("total_ms", "12", "13"),  # text, not a number
            ("summary_json", '{"a_at": "corrupt-A"}', '{"a_at": "corrupt-B"}'),
        ):
            with self.subTest(column=column):
                self.assertNotEqual(self.dump("left", {column: left}), self.dump("right", {column: right}))

    def test_allowlisted_values_are_substituted(self) -> None:
        archive = str(self.directory / "archive")
        left = {
            "analyzed_at": "2026-10-03T10:00:00+00:00",
            "analysis_sha256": "a" * 64,
            "total_ms": 12.5,
            "report_path": archive + os.sep + "reports" + os.sep + "2026-10-03t10-00-00-00-00",
            "summary_json": json.dumps({"x_at": "2026-10-03T10:00:00Z", "decode_ms": 3, "root": archive}),
        }
        right = {
            "analyzed_at": "2026-10-04T11:30:00.123+02:00",
            "analysis_sha256": "b" * 64,
            "total_ms": 99,
            "report_path": archive + os.sep + "reports" + os.sep + "2026-10-04t11-30-00-02-00",
            "summary_json": json.dumps({"x_at": "2026-10-04T11:30:00Z", "decode_ms": 7, "root": archive}),
        }
        self.assertEqual(self.dump("left", left), self.dump("right", right))
        self.assertIn("<archive_root>", self.dump("left", left))


class WorkerNativeDecodeTests(unittest.TestCase):
    """Finding 8: a worker capture pair must show the native decode on the native side only."""

    @staticmethod
    def meta(calls: list[dict[str, Any]] | None, names: tuple[str, ...] = ("a.ibt", "b.ibt")) -> dict[str, Any]:
        meta: dict[str, Any] = {"capture_format": ap.CAPTURE_FORMAT, "recordings": [{"name": n} for n in names]}
        if calls is not None:
            meta["native_decode_calls"] = calls
        return meta

    def test_native_decode_on_the_native_side_only(self) -> None:
        both = [{"source": "a.ibt", "error": None}, {"source": "b.ibt", "error": None}]
        self.assertEqual(ap.worker_native_decode_problems(self.meta([]), self.meta(both), True), [])
        cases = {
            "native lost the stage (Python against Python)": (self.meta([]), self.meta([])),
            "one recording not decoded natively": (self.meta([]), self.meta(both[:1])),
            "a native call failed": (self.meta([]), self.meta([both[0], {"source": "b.ibt", "error": "NativeStageError"}])),
            "oracle used the native stage": (self.meta(both), self.meta(both)),
            "no trace recorded": (self.meta(None), self.meta(both)),
            "older capture format": ({**self.meta([]), "capture_format": 2}, self.meta(both)),
        }
        for label, (oracle, native) in cases.items():
            with self.subTest(label):
                self.assertTrue(ap.worker_native_decode_problems(oracle, native, True))

    def test_the_shim_records_each_call(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="parity-trace-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, True))
        trace = directory / "trace.jsonl"
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        ]
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-u", str(ap.WORKER_TRACE), str(trace)],
            input="".join(json.dumps(item) + "\n" for item in requests).encode("utf-8"),
            capture_output=True, timeout=120, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        response = json.loads(completed.stdout.decode("utf-8").splitlines()[0])
        self.assertEqual(response["id"], 1)
        self.assertFalse(trace.exists())  # initialize decodes nothing


if __name__ == "__main__":
    unittest.main()
