"""The native-stage seam (worker-interface.md section 7, port phase 1).

``IRACING_COACH_NATIVE_STAGES`` selects native stages; with ``decode`` the
worker reads a coach column store written by ``coach-analysis decode`` and
must hand the analysis exactly the table ``ibt_reader.load_telemetry``
returns. The end-to-end cases need the executable (``IRACING_COACH_NATIVE_BIN``
or ``coach-analysis`` on PATH) and are skipped without it; the store reader
and the stage selection are tested without it.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import ibt_reader  # noqa: E402
import native_stages  # noqa: E402
import storage  # noqa: E402
import workflow  # noqa: E402
from test_ibt_reader import build_ibt  # noqa: E402

SYNTHETIC_RACE = WORKSPACE / "test-data" / "ibt" / "synthetic-race.ibt"


def _store_bytes(header: dict[str, Any], blocks: list[bytes], *, magic: bytes = native_stages.STORE_MAGIC, version: int = 1) -> bytes:
    """A coach column store in the documented layout (blocks already placed by the header)."""

    text = json.dumps(header).encode("utf-8")
    out = bytearray(magic + struct.pack("<II", version, len(text)) + text)
    out.extend(b"\x00" * (-len(out) % 8))
    for block in blocks:
        out.extend(block)
        out.extend(b"\x00" * (-len(out) % 8))
    return bytes(out)


def _place(blocks: list[bytes], data: bytes) -> dict[str, int]:
    offset = sum(len(block) + (-len(block) % 8) for block in blocks)
    blocks.append(data)
    return {"offset": offset, "length": len(data)}


def _sample_store(samples: int = 3) -> tuple[dict[str, Any], list[bytes]]:
    blocks: list[bytes] = []
    texts = ["a", "﻿bom", "café"][:samples]
    encoded = [text.encode("utf-8") for text in texts]
    offsets = [0]
    for item in encoded:
        offsets.append(offsets[-1] + len(item))
    columns = [
        {"name": "B", "type_code": 1, "count": 1, "var_offset": 0, "element": "bool",
         "data": _place(blocks, bytes([0, 1, 1][:samples]))},
        {"name": "I", "type_code": 2, "count": 2, "var_offset": 4, "element": "i32",
         "data": _place(blocks, struct.pack(f"<{2 * samples}i", *range(-samples, samples)))},
        {"name": "Bits", "type_code": 3, "count": 1, "var_offset": 12, "element": "u32",
         "data": _place(blocks, struct.pack(f"<{samples}I", *[2**32 - 1] * samples))},
        {"name": "F", "type_code": 4, "count": 1, "var_offset": 16, "element": "f32",
         "data": _place(blocks, struct.pack(f"<{samples}f", *[0.1, float("nan"), -0.0][:samples]))},
        {"name": "D", "type_code": 5, "count": 1, "var_offset": 20, "element": "f64",
         "data": _place(blocks, struct.pack(f"<{samples}d", *[0.1, 1e300, -2.5][:samples]))},
        {"name": "Text", "type_code": 0, "count": 8, "var_offset": 28, "element": "utf8",
         "data": _place(blocks, b"".join(encoded)),
         "text_offsets": _place(blocks, struct.pack(f"<{samples + 1}Q", *offsets))},
    ]
    indices = _place(blocks, struct.pack(f"<{samples}I", *range(0, 2 * samples, 2)))
    header = {
        "format": "coach-column-store", "version": 1, "stage": "decode", "producer": "test",
        "source": {}, "channel_mode": "all", "future_optional_field": [1, 2],
        "sampling": {"target_hz": None, "step": 1.0, "integral_step": True, "sample_rate_hz": 60.0, "sample_count": samples},
        "sample_indices": indices, "columns": columns,
    }
    return header, blocks


def _strict_equal(test: unittest.TestCase, left: Any, right: Any, where: str = "$") -> None:
    test.assertIs(type(left), type(right), where)
    if isinstance(left, dict):
        test.assertEqual(list(left), list(right), where)
        for key in left:
            _strict_equal(test, left[key], right[key], f"{where}.{key}")
    elif isinstance(left, list):
        test.assertEqual(len(left), len(right), where)
        for index, (a, b) in enumerate(zip(left, right)):
            _strict_equal(test, a, b, f"{where}[{index}]")
    elif isinstance(left, float):
        test.assertEqual(struct.pack("<d", left), struct.pack("<d", right), where)
    else:
        test.assertEqual(left, right, where)


class StageSelectionTests(unittest.TestCase):
    def test_empty_list_is_pure_python(self) -> None:
        for raw in ("", " ", ",", " , "):
            self.assertEqual(native_stages.requested_stages({native_stages.STAGES_ENV: raw}), frozenset())
        self.assertEqual(native_stages.requested_stages({}), frozenset())

    def test_decode_is_available(self) -> None:
        self.assertEqual(
            native_stages.requested_stages({native_stages.STAGES_ENV: " decode , decode"}), frozenset({"decode"})
        )

    def test_unknown_and_unprovided_stages_fail(self) -> None:
        with self.assertRaisesRegex(native_stages.NativeStageError, "unknown stage"):
            native_stages.requested_stages({native_stages.STAGES_ENV: "decode,gpu"})
        # The phase-3 stage is "writer"; the contract's earlier name is not a stage.
        with self.assertRaisesRegex(native_stages.NativeStageError, "unknown stage"):
            native_stages.requested_stages({native_stages.STAGES_ENV: "decode,json_writer"})
        with mock.patch.object(native_stages, "AVAILABLE_STAGES", frozenset({"decode", "kernels"})):
            with self.assertRaisesRegex(native_stages.NativeStageError, "does not provide"):
                native_stages.requested_stages({native_stages.STAGES_ENV: "decode,writer"})

    def test_writer_stands_alone(self) -> None:
        self.assertEqual(native_stages.requested_stages({native_stages.STAGES_ENV: "writer"}), frozenset({"writer"}))
        self.assertTrue(native_stages.writer_requested({native_stages.STAGES_ENV: "decode,kernels,writer"}))
        self.assertFalse(native_stages.writer_requested({native_stages.STAGES_ENV: "decode,kernels"}))

    def test_kernels_requires_decode(self) -> None:
        self.assertEqual(
            native_stages.requested_stages({native_stages.STAGES_ENV: "kernels,decode"}),
            frozenset({"decode", "kernels"}),
        )
        with self.assertRaisesRegex(native_stages.NativeStageError, "kernels without decode"):
            native_stages.requested_stages({native_stages.STAGES_ENV: "kernels"})

    def test_binary_resolution(self) -> None:
        with self.assertRaisesRegex(native_stages.NativeStageError, "does not name a file"):
            native_stages.native_binary({native_stages.BINARY_ENV: str(WORKSPACE / "no-such-binary")})
        with mock.patch.object(native_stages.shutil, "which", return_value=None):
            with self.assertRaisesRegex(native_stages.NativeStageError, "not on PATH"):
                native_stages.native_binary({})
        self.assertEqual(native_stages.native_binary({native_stages.BINARY_ENV: __file__}), __file__)


class StoreReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="native-store-"))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def write(self, data: bytes) -> Path:
        path = self.directory / "store.ccs"
        path.write_bytes(data)
        return path

    def test_values_are_the_reader_types(self) -> None:
        header, blocks = _sample_store()
        _, indices, samples = native_stages.read_store(self.write(_store_bytes(header, blocks)))
        self.assertEqual(indices, [0, 2, 4])
        self.assertEqual(samples["B"], [False, True, True])
        self.assertTrue(all(type(value) is bool for value in samples["B"]))
        self.assertEqual(samples["I"], [[-3, -2], [-1, 0], [1, 2]])
        self.assertEqual(samples["Bits"], [2**32 - 1] * 3)
        self.assertEqual(samples["F"][0], struct.unpack("<f", struct.pack("<f", 0.1))[0])
        self.assertTrue(math.isnan(samples["F"][1]))
        self.assertEqual(struct.pack("<d", samples["F"][2]), struct.pack("<d", -0.0))
        self.assertEqual(samples["D"], [0.1, 1e300, -2.5])
        self.assertEqual(samples["Text"], ["a", "﻿bom", "café"])
        self.assertEqual(list(samples), ["B", "I", "Bits", "F", "D", "Text"])

    def test_empty_columns(self) -> None:
        header, blocks = _sample_store(samples=0)
        _, indices, samples = native_stages.read_store(self.write(_store_bytes(header, blocks)))
        self.assertEqual(indices, [])
        self.assertEqual(samples, {"B": [], "I": [], "Bits": [], "F": [], "D": [], "Text": []})

    def test_incompatible_or_damaged_stores_fail_clearly(self) -> None:
        header, blocks = _sample_store()
        cases = {
            "not a coach column store": _store_bytes(header, blocks, magic=b"COACHXX\x00"),
            "format version 2": _store_bytes(header, blocks, version=2),
            "runs past the end": _store_bytes(header, blocks)[:-16],
        }
        wrong_type = json.loads(json.dumps(header))
        wrong_type["columns"][0]["type_code"] = 2
        cases["type does not match"] = _store_bytes(wrong_type, blocks)
        missing_count = json.loads(json.dumps(header))
        missing_count["sampling"]["sample_count"] = 4
        cases["sample indices for 4 samples"] = _store_bytes(missing_count, blocks)
        for message, data in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(native_stages.NativeStageError, message):
                    native_stages.read_store(self.write(data))

    def assert_rejected(self, header: dict[str, Any], blocks: list[bytes], message: str) -> None:
        with self.assertRaisesRegex(native_stages.NativeStageError, message):
            native_stages.read_store(self.write(_store_bytes(header, blocks)))

    def test_text_offsets_must_cover_the_text_exactly(self) -> None:
        # Review finding 5: b"ab" with offsets [0, 2, 100] sliced to ["ab", ""].
        for offsets in ([0, 2, 100], [0, 2, 1], [1, 2, 2], [0, 1, 1]):
            with self.subTest(offsets=offsets):
                blocks: list[bytes] = []
                column = {"name": "T", "type_code": 0, "count": 4, "var_offset": 0, "element": "utf8",
                          "data": _place(blocks, b"ab"), "text_offsets": _place(blocks, struct.pack("<3Q", *offsets))}
                header = {"format": "coach-column-store", "version": 1, "stage": "decode",
                          "sampling": {"sample_count": 2}, "columns": [column],
                          "sample_indices": _place(blocks, struct.pack("<2I", 0, 1))}
                self.assert_rejected(header, blocks, "text offsets must rise")
        blocks = []
        column = {"name": "T", "type_code": 0, "count": 4, "var_offset": 0, "element": "utf8",
                  "data": _place(blocks, b"\xc3\xa9"), "text_offsets": _place(blocks, struct.pack("<3Q", 0, 1, 2))}
        header = {"format": "coach-column-store", "version": 1, "stage": "decode", "sampling": {"sample_count": 2},
                  "columns": [column], "sample_indices": _place(blocks, struct.pack("<2I", 0, 1))}
        self.assert_rejected(header, blocks, "text is not UTF-8")

    def test_blocks_stay_whole_aligned_and_inside_the_data_section(self) -> None:
        # Review finding 6: a negative offset read the magic as a sample index;
        # a fractional one was truncated by int().
        header, blocks = _sample_store(samples=1)
        text = json.dumps(header).encode("utf-8")
        base = (16 + len(text) + 7) // 8 * 8
        for spec, message in (
            ({"offset": -base, "length": 4}, "bad block"),
            ({"offset": -8, "length": 4}, "bad block"),
            ({"offset": 0.5, "length": 4}, "bad block"),
            ({"offset": 0, "length": 4.0}, "bad block"),
            ({"offset": True, "length": 4}, "bad block"),
            ({"offset": 4, "length": 4}, "not a multiple of 8"),
            ({"offset": 1 << 40, "length": 4}, "past the end"),
        ):
            with self.subTest(spec=spec):
                broken = json.loads(json.dumps(header))
                broken["sample_indices"] = spec
                self.assert_rejected(broken, blocks, message)
        broken = json.loads(json.dumps(header))
        broken["columns"][1]["text_offsets"] = broken["columns"][5]["text_offsets"]
        self.assert_rejected(broken, blocks, "text_offsets on a i32 column")
        broken = json.loads(json.dumps(header))
        broken["sampling"]["sample_count"] = 1.0
        self.assert_rejected(broken, blocks, "bad sample_count")

    def test_array_columns_are_rebuilt_in_batches_with_the_same_rows(self) -> None:
        header, blocks = _sample_store()
        path = self.write(_store_bytes(header, blocks))
        _, _, expected = native_stages.read_store(path)
        for batch in (1, 2):
            with self.subTest(batch=batch), mock.patch.object(native_stages, "_ROW_BATCH", batch):
                _, _, samples = native_stages.read_store(path)
                _strict_equal(self, expected, samples)

    def test_array_column_holds_no_whole_flat_list(self) -> None:
        # Review finding 13: the whole flat list stayed alive beside the rows.
        import tracemalloc  # noqa: PLC0415

        samples, count = 20_000, 64
        blocks: list[bytes] = []
        data = bytes(4 * samples * count)  # zeros: cached small ints, so rows and lists dominate
        column = {"name": "A", "type_code": 2, "count": count, "var_offset": 0, "element": "i32",
                  "data": _place(blocks, data)}
        header = {"format": "coach-column-store", "version": 1, "stage": "decode", "sampling": {"sample_count": samples},
                  "columns": [column], "sample_indices": _place(blocks, struct.pack(f"<{samples}I", *range(samples)))}
        path = self.write(_store_bytes(header, blocks))
        flat_list_bytes = 8 * samples * count
        tracemalloc.start()
        try:
            _, indices, result = native_stages.read_store(path)
            retained, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(result["A"]), samples)
        self.assertEqual(result["A"][-1], [0] * count)
        transient = peak - retained
        # The block read itself is len(data); the old flat list added flat_list_bytes more.
        self.assertLess(transient, len(data) + flat_list_bytes // 2, (transient, len(data), flat_list_bytes))


class WorkflowSeamTests(unittest.TestCase):
    def test_decode_stage_replaces_only_the_reader_call(self) -> None:
        calls: list[Any] = []

        def native(path: Any, channels: Any = None, target_hz: Any = 20) -> dict[str, Any]:
            calls.append((path, target_hz))
            return ibt_reader.load_telemetry(path, channels=channels, target_hz=target_hz)

        source = str(SYNTHETIC_RACE)
        with mock.patch.dict(os.environ, {native_stages.STAGES_ENV: ""}), \
                mock.patch.object(workflow, "_assert_finalized_ibt"):
            python_table = workflow._load_analysis_telemetry([source], None)
        with mock.patch.dict(os.environ, {native_stages.STAGES_ENV: "decode"}), \
                mock.patch.object(native_stages, "load_telemetry", side_effect=native), \
                mock.patch.object(workflow, "_assert_finalized_ibt"):
            native_table = workflow._load_analysis_telemetry([source], None)
        self.assertEqual(calls, [(source, None)])
        _strict_equal(self, python_table, native_table)

    def test_unavailable_stage_fails_the_job(self) -> None:
        with mock.patch.dict(os.environ, {native_stages.STAGES_ENV: "kernels"}):
            with self.assertRaises(native_stages.NativeStageError):
                workflow._load_analysis_telemetry([str(SYNTHETIC_RACE)], None)


def _oracle_decode(source: str, channels: Any, target_text: str | None) -> tuple[dict[str, Any], list[int], dict[str, list[Any]]]:
    """What a correct native decode hands the seam, built from the reader itself."""

    target = None if target_text is None else float(target_text)
    table = ibt_reader.load_telemetry(source, channels=channels, target_hz=target)
    scan = ibt_reader.scan_ibt(source)
    step, rate = ibt_reader._sampling_plan(scan["header"]["tick_rate"], target)
    header = {
        "source": {
            "file_size": scan["file_size"], "record_count": scan["record_count"],
            "tick_rate": scan["header"]["tick_rate"], "buffer_offset": scan["buffer_offset"],
            "buffer_length": scan["header"]["buffer_length"], "num_variables": len(scan["variables"]),
        },
        "sampling": {"target_hz": target, "step": step, "integral_step": step.is_integer(),
                     "sample_rate_hz": rate, "sample_count": len(table["sample_indices"])},
        "channel_mode": "all" if channels is None else "selected",
        "columns": [
            {"name": v["name"], "type_code": v["type_code"], "count": v["count"], "var_offset": v["offset"]}
            for v in table["variables"]
        ],
    }
    return header, list(table["sample_indices"]), dict(table["samples"])


class SeamValidationTests(unittest.TestCase):
    """The seam checks the store's selection and sampling against the oracle (review findings 3, 4)."""

    def load(self, channels: Any, target_hz: Any, edit: Any = None) -> dict[str, Any]:
        def decode(binary: str, source: str, requested: Any, target_text: Any) -> Any:
            header, indices, samples = _oracle_decode(source, requested, target_text)
            if edit is not None:
                header, indices, samples = edit(header, indices, samples)
            return header, indices, samples

        with mock.patch.object(native_stages, "native_binary", return_value="coach-analysis"), \
                mock.patch.object(native_stages, "_decode", side_effect=decode):
            return native_stages.load_telemetry(SYNTHETIC_RACE, channels=channels, target_hz=target_hz)

    def test_a_correct_store_is_the_reader_table(self) -> None:
        for channels, target_hz in ((["Speed", "Lap", "Speed"], 20), (None, None), ("Lap", 7.5)):
            with self.subTest(channels=channels, target_hz=target_hz):
                expected = ibt_reader.load_telemetry(SYNTHETIC_RACE, channels=channels, target_hz=target_hz)
                _strict_equal(self, expected, self.load(channels, target_hz))

    def test_columns_must_be_the_exact_ordered_selection(self) -> None:
        def keep(names: Any) -> Any:
            def edit(header: Any, indices: Any, samples: Any) -> Any:
                chosen = names(list(samples))
                header["columns"] = [next(c for c in header["columns"] if c["name"] == n) for n in chosen]
                return header, indices, {name: samples[name] for name in chosen}

            return edit

        cases = {
            "only Lap": (["Speed", "Lap"], keep(lambda names: ["Lap"])),
            "reversed": (["Speed", "Lap"], keep(lambda names: names[::-1])),
            "none": (["Speed", "Lap"], keep(lambda names: [])),
            "catalogue less one": (None, keep(lambda names: names[:-1])),
            "catalogue reordered": (None, keep(lambda names: names[1:] + names[:1])),
        }
        for label, (channels, edit) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(native_stages.NativeStageError, "not the selection"):
                self.load(channels, 20, edit)

    def test_sampling_and_indices_must_be_the_reader_plan(self) -> None:
        def sampling(**fields: Any) -> Any:
            def edit(header: Any, indices: Any, samples: Any) -> Any:
                header["sampling"].update(fields)
                return header, indices, samples

            return edit

        def indices(change: Any) -> Any:
            def edit(header: Any, stored: Any, samples: Any) -> Any:
                return header, change(list(stored)), samples

            return edit

        cases = {
            "sample_rate_hz": sampling(sample_rate_hz=123.0),
            "sample_rate_hz 20 is not": sampling(sample_rate_hz=20),  # an int is not the reader's float
            "step": sampling(step=3.0000000000000004),
            "integral_step": sampling(integral_step=False),
            "target_hz": sampling(target_hz=None),
            "record index": indices(lambda stored: stored[::-1]),
        }
        cases["record index 0"] = indices(lambda stored: [stored[0]] * len(stored))
        cases["record index None"] = indices(lambda stored: stored[:-1])
        cases["the plan's is None"] = indices(lambda stored: stored + [stored[-1] + 3])
        for message, edit in cases.items():
            with self.subTest(message), self.assertRaisesRegex(native_stages.NativeStageError, message):
                self.load(["Speed"], 20, edit)


def _binary() -> str | None:
    try:
        return native_stages.native_binary()
    except native_stages.NativeStageError:
        return None


@unittest.skipUnless(_binary(), "coach-analysis not available (IRACING_COACH_NATIVE_BIN or PATH)")
class NativeDecodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="native-decode-"))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def assert_same_table(self, path: Path, channels: Any, target_hz: Any) -> None:
        expected = ibt_reader.load_telemetry(path, channels=channels, target_hz=target_hz)
        actual = native_stages.load_telemetry(path, channels=channels, target_hz=target_hz)
        _strict_equal(self, expected, actual)

    def test_every_type_rate_and_selection(self) -> None:
        recording = self.directory / "types.ibt"
        build_ibt(recording, record_count=37)
        for target_hz in (None, 20, 7.5, 59.94, 600):
            for channels in (None, ["TireTemps", "DriverName", "OnPitRoad", "TireTemps"], [], "SessionFlags"):
                with self.subTest(target_hz=target_hz, channels=channels):
                    self.assert_same_table(recording, channels, target_hz)
        for target_hz in (None, 20):
            self.assert_same_table(SYNTHETIC_RACE, None, target_hz)

    def test_reader_errors_survive(self) -> None:
        with self.assertRaises(native_stages.NativeStageError) as caught:
            native_stages.load_telemetry(SYNTHETIC_RACE, channels=["NoSuchChannel"], target_hz=None)
        self.assertIn("requested channel(s) not present: NoSuchChannel", str(caught.exception))
        with self.assertRaises(ValueError):
            native_stages.load_telemetry(SYNTHETIC_RACE, channels=None, target_hz=True)
        with self.assertRaises(ibt_reader.IbtTruncatedError):
            native_stages.load_telemetry(WORKSPACE / "test-data" / "ibt" / "truncated-race.ibt")

    def test_overflowing_step_fails_as_the_reader_fails(self) -> None:
        # Review finding 2: 60 / 5e-324 is inf; the reader raises OverflowError at
        # int(inf), and the native stage must fail rather than return [0].
        for target_hz in (5e-324, 1e-307):
            with self.subTest(target_hz=target_hz):
                with self.assertRaises(OverflowError) as oracle:
                    ibt_reader.load_telemetry(SYNTHETIC_RACE, channels=["Speed"], target_hz=target_hz)
                with self.assertRaises(native_stages.NativeStageError) as native:
                    native_stages.load_telemetry(SYNTHETIC_RACE, channels=["Speed"], target_hz=target_hz)
                self.assertTrue(str(native.exception).endswith(str(oracle.exception)), str(native.exception))
        self.assert_same_table(SYNTHETIC_RACE, ["Speed"], 1e-300)  # huge finite step: index 0 only


# ---------------------------------------------------------------------------
# Phase 2: the store-backed table and the kernels session
# ---------------------------------------------------------------------------

import analysis_engine  # noqa: E402
import native_codec  # noqa: E402

FAKE_KERNELS = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--fake-kernels"]
PYTHON_LISTS = {native_stages.STAGES_ENV: ""}
STORE_STAGES = {native_stages.STAGES_ENV: "decode,kernels"}


def _codec_store(binary: str, source: str, channels: Any, target_text: str | None) -> native_stages.StoreFile:
    """``_decode_store`` without the executable: the reader's own table written by the vector codec."""

    target = None if target_text is None else float(target_text)
    table = ibt_reader.load_telemetry(source, channels=channels, target_hz=target)
    work = tempfile.mkdtemp(prefix="coach-decode-")
    out = Path(work) / "decode.ccs"
    native_codec.write_decode_store(
        out, telemetry=table, sampling_plan=ibt_reader._sampling_plan, target_hz=target, producer="test"
    )
    return native_stages.StoreFile(out, directory=work)


def _telemetry(stages: dict[str, str], path: Path = SYNTHETIC_RACE, target_hz: Any = None) -> dict[str, Any]:
    """The worker's table for ``path``: lists, or store-backed when ``stages`` lists kernels."""

    with mock.patch.dict(os.environ, stages), \
            mock.patch.object(native_stages, "_decode_store", _codec_store), \
            mock.patch.object(native_stages, "native_binary", return_value="coach-analysis"), \
            mock.patch.object(workflow, "_assert_finalized_ibt"):
        return workflow._load_analysis_telemetry([str(path)], target_hz)


def _untimed(analysis: dict[str, Any]) -> dict[str, Any]:
    """The analysis with its wall-clock ``analyzed_at`` replaced in place (key order kept)."""

    if "analyzed_at" in analysis:
        analysis["analyzed_at"] = "<timestamp>"
    return analysis


def _analyze(telemetry: dict[str, Any], fingerprints: bool = True) -> dict[str, Any]:
    prints = [{"path": str(SYNTHETIC_RACE), "sha256": "ab" * 32}] if fingerprints else []
    return _untimed(analysis_engine.analyze_telemetry(
        telemetry, source_paths=[str(SYNTHETIC_RACE)], source_fingerprints=prints, analysis_profile={"target_hz": None}
    ))


def _entry_bindings() -> list[Any]:
    return [analysis_engine.TelemetryTable, analysis_engine._fallback_telemetry_digest, native_stages.kernel_call] + [
        getattr(native_stages._owner(owner), name) for owner, name in native_stages.KERNEL_ENTRIES
    ]


class StoreColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="native-columns-"))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def open(self, header: dict[str, Any], blocks: list[bytes], stages: Any = ("decode",)) -> native_stages.StoreFile:
        path = self.directory / f"store-{len(list(self.directory.iterdir()))}.ccs"
        path.write_bytes(_store_bytes(header, blocks))
        store = native_stages.StoreFile(path, stages=stages)
        self.addCleanup(store.close)
        return store

    def test_every_access_gives_the_reader_values(self) -> None:
        header, blocks = _sample_store()
        store = self.open(header, blocks)
        _, indices, expected = native_stages.read_store(self.directory / "store-0.ccs")
        self.assertEqual(store.sample_indices.tolist(), indices)
        for name, column in store.columns.items():
            with self.subTest(name=name):
                values = expected[name]
                self.assertEqual(len(column), len(values))
                _strict_equal(self, list(column), values)
                _strict_equal(self, column.tolist(), values)
                _strict_equal(self, [column[i] for i in range(len(values))], values)
                _strict_equal(self, [column[i] for i in range(-len(values), 0)], values)
                for part in (slice(None), slice(1, None), slice(None, None, -1), slice(0, 3, 2), slice(5, 9)):
                    _strict_equal(self, column[part], values[part])
                _strict_equal(self, list(reversed(column)), values[::-1])
                for index in (len(values), -len(values) - 1):
                    with self.assertRaisesRegex(IndexError, "list index out of range"):
                        column[index]
                with self.assertRaises(TypeError):
                    column["0"]
        self.assertIsInstance(store.columns["F"], native_stages.StoreColumn)
        self.assertTrue(math.isnan(store.columns["F"][1]))

    def test_a_closed_store_reads_nothing(self) -> None:
        header, blocks = _sample_store()
        store = self.open(header, blocks)
        column = store.columns["D"]
        store.close()
        with self.assertRaises(ValueError):
            column[0]
        store.close()  # idempotent

    def merge_store(self, present: bytes, files: list[int], **changes: Any) -> tuple[dict[str, Any], list[bytes]]:
        blocks: list[bytes] = []
        header = {
            "format": "coach-column-store", "version": 1, "stage": "merge", "producer": "test",
            "sampling": {"sample_count": 3},
            "sample_indices": _place(blocks, struct.pack("<3I", 7, 8, 0)),
            "provenance": {"file_index": _place(blocks, struct.pack(f"<{len(files)}H", *files))},
            "columns": [
                {"name": "Speed", "type_code": 4, "count": 1, "var_offset": 0, "element": "f32",
                 "data": _place(blocks, struct.pack("<3f", 1.5, 0.0, -2.0)), "present": _place(blocks, present)},
                {"name": "Lap", "type_code": 2, "count": 1, "var_offset": 4, "element": "i32",
                 "data": _place(blocks, struct.pack("<3i", 1, 1, 2))},
            ],
        }
        header.update(changes)
        return header, blocks

    def test_a_merge_store_has_missing_cells_and_provenance(self) -> None:
        store = self.open(*self.merge_store(b"\x01\x00\x01", [0, 1, 1]), stages=("merge",))
        _strict_equal(self, list(store.columns["Speed"]), [1.5, None, -2.0])
        _strict_equal(self, store.columns["Speed"][1:], [None, -2.0])
        _strict_equal(self, list(store.columns["Lap"]), [1, 1, 2])
        provenance = native_stages.ProvenanceSequence(store.sample_indices, ["a.ibt", "b.ibt"], store.file_indices)
        self.assertEqual(
            list(provenance),
            [
                {"source_file_index": 0, "source_path": "a.ibt", "native_record_index": 7},
                {"source_file_index": 1, "source_path": "b.ibt", "native_record_index": 8},
                {"source_file_index": 1, "source_path": "b.ibt", "native_record_index": 0},
            ],
        )

    def test_merge_and_decode_stores_are_not_interchangeable(self) -> None:
        header, blocks = self.merge_store(b"\x01\x00\x01", [0, 1, 1])
        for stages, message in ((("decode",), "stage 'merge' is not decode"),):
            with self.subTest(stages=stages), self.assertRaisesRegex(native_stages.NativeStageError, message):
                self.open(header, blocks, stages=stages)
        for present, message in ((b"\x01\x02\x01", "presence bytes must be 0 or 1"), (b"\x01\x00", "presence bytes for 3")):
            with self.subTest(present=present), self.assertRaisesRegex(native_stages.NativeStageError, message):
                self.open(*self.merge_store(present, [0, 1, 1]), stages=("merge",))
        with self.assertRaisesRegex(native_stages.NativeStageError, "provenance does not cover 3 samples"):
            self.open(*self.merge_store(b"\x01\x00\x01", [0, 1]), stages=("merge",))
        with self.assertRaisesRegex(native_stages.NativeStageError, "3 sample indices for 2 samples"):
            self.open(*self.merge_store(b"\x01\x00\x01", [0, 1, 1], sampling={"sample_count": 2}), stages=("merge",))
        with self.assertRaisesRegex(native_stages.NativeStageError, "a merge store needs provenance"):
            self.open(*self.merge_store(b"\x01\x00\x01", [0, 1, 1], provenance=None), stages=("merge",))
        decode_header, decode_blocks = _sample_store()
        decode_header["columns"][0]["present"] = decode_header["columns"][0]["data"]
        with self.assertRaisesRegex(native_stages.NativeStageError, "presence block in a decode store"):
            self.open(decode_header, decode_blocks)


class StoreTableTests(unittest.TestCase):
    """The oracle's own code over StoreColumn sequences: the table every Python-remaining caller reads."""

    def test_store_backed_table_is_the_list_table(self) -> None:
        lists = _telemetry(PYTHON_LISTS)
        store = _telemetry(STORE_STAGES)
        self.addCleanup(native_stages.release, store["samples"])
        self.assertIsInstance(store["samples"], native_stages.StoreSamples)
        self.assertIsInstance(store["sample_provenance"], native_stages.ProvenanceSequence)
        _strict_equal(self, list(store["sample_provenance"]), lists["sample_provenance"])
        _strict_equal(self, list(store["sample_indices"]), lists["sample_indices"])
        for key in lists:
            if key not in ("samples", "sample_provenance", "sample_indices"):
                _strict_equal(self, store[key], lists[key], key)
        _strict_equal(self, {name: list(values) for name, values in store["samples"].items()}, lists["samples"])
        oracle = analysis_engine.TelemetryTable(lists)
        table = native_stages.StoreTable(store)
        for name in ("metadata", "session_info", "variables", "available_variables", "channel_units", "length"):
            _strict_equal(self, getattr(table, name), getattr(oracle, name), name)
        self.assertEqual(list(table.channels), list(oracle.channels))

    def test_the_oracle_over_the_store_is_the_oracle(self) -> None:
        # No kernel: every Python function reads StoreColumns, so any list-only
        # behaviour the Python-remaining callers rely on shows up here.
        for fingerprints in (True, False):
            with self.subTest(fingerprints=fingerprints):
                expected = _analyze(_telemetry(PYTHON_LISTS), fingerprints)
                store = _telemetry(STORE_STAGES)
                # The session's digest shim: json.dumps(default=str) would stringify a column.
                digest = native_stages._materialised_digest(analysis_engine._fallback_telemetry_digest)
                try:
                    with mock.patch.object(analysis_engine, "TelemetryTable", native_stages.StoreTable),                             mock.patch.object(analysis_engine, "_fallback_telemetry_digest", digest):
                        actual = _analyze(store, fingerprints)
                finally:
                    native_stages.release(store["samples"])
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                _strict_equal(self, actual, expected)

    def test_the_table_refuses_lists(self) -> None:
        with self.assertRaisesRegex(native_stages.NativeStageError, "store-backed table"):
            native_stages.StoreTable(_telemetry(PYTHON_LISTS))


class KernelSessionTests(unittest.TestCase):
    """The seam with a child that runs the oracle's own entry points (``--fake-kernels``).

    It tests the hand-off (codec both ways, the table by reference, handles,
    accessed channels, raises, failures, rebinding and restore), not a kernel.
    """

    def setUp(self) -> None:
        self.bindings = _entry_bindings()
        self.addCleanup(lambda: self.assertEqual(_entry_bindings(), self.bindings, "a binding was not restored"))

    def session_analysis(self, command: list[str], fingerprints: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        store = _telemetry(STORE_STAGES)
        counts, uninstall = native_stages.install_call_counters()
        try:
            with native_stages.kernel_session(store, command=command):
                result = _analyze(store, fingerprints)
        finally:
            uninstall()
        with self.assertRaises(ValueError):  # the session closed the store
            next(iter(store["samples"].values()))[0]
        return result, counts

    def test_the_session_is_the_oracle_count_for_count(self) -> None:
        for fingerprints in (True, False):
            with self.subTest(fingerprints=fingerprints):
                counts, uninstall = native_stages.install_call_counters()
                try:
                    expected = _analyze(_telemetry(PYTHON_LISTS), fingerprints)
                finally:
                    uninstall()
                actual, native_counts = self.session_analysis(FAKE_KERNELS, fingerprints)
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                _strict_equal(self, actual, expected)
                self.assertEqual(native_stages.kernel_call_problems(counts, native_counts, True), [])
                self.assertEqual(set(native_counts["native"]) - set(native_stages.KERNEL_LABELS), set())
                self.assertGreaterEqual(sum(native_counts["native"].values()), 15)

    def test_count_rule_finds_a_missing_or_extra_call(self) -> None:
        oracle = {"oracle": {"analysis_engine._lap_summaries": 1, "analysis_engine._dt_series": 2}, "native": {}}
        native = {"oracle": {}, "native": {"analysis_engine._lap_summaries": 1, "analysis_engine._dt_series": 1}}
        self.assertEqual(
            native_stages.kernel_call_problems(oracle, native, True),
            ["analysis_engine._dt_series: 1 kernel call(s), the oracle made 2"],
        )
        self.assertIn("ran Python entry points", native_stages.kernel_call_problems(oracle, {"oracle": {"x": 1}, "native": oracle["oracle"]}, True)[0])
        self.assertIn("without the kernels stage", native_stages.kernel_call_problems(oracle, native, False)[0])
        self.assertIn("oracle capture made kernel calls", native_stages.kernel_call_problems(native, native, True)[0])

    def failing(self, *flags: str, message: str, kind: type[BaseException] = native_stages.NativeStageError) -> None:
        store = _telemetry(STORE_STAGES)
        with self.assertRaisesRegex(kind, message):
            with native_stages.kernel_session(store, command=FAKE_KERNELS + list(flags)):
                _analyze(store)
        with self.assertRaises(ValueError):  # closed on failure too
            next(iter(store["samples"].values()))[0]

    def test_a_build_without_an_entry_fails_the_job(self) -> None:
        self.failing("--drop", "pit_loss.measure_pit_loss", message="does not provide kernel.s. pit_loss.measure_pit_loss")

    def test_the_oracle_exception_crosses_and_failures_fail(self) -> None:
        self.failing("--raise-on", "analysis_engine._track_profile", message="^injected$", kind=ZeroDivisionError)
        self.failing("--error-on", "analysis_engine._lap_summaries", message="kernels analysis_engine._lap_summaries: injected")
        self.failing("--exit-on", "analysis_engine._lap_summaries", message="the process ended: fake kernels exits")
        self.failing("--bad-length", message="kernels start answered")
        self.failing("--bad-accessed", message="bad accessed channels")
        # Codex phase-2 review finding 5: the access record and a single outcome are required.
        self.failing("--no-accessed", message="answered without its accessed channels")
        self.failing("--both-outcomes", message="exactly one of result and raises")
        self.failing("--no-outcome", message="exactly one of result and raises")

    def test_kernels_never_converts_lists(self) -> None:
        with self.assertRaisesRegex(native_stages.NativeStageError, "never converts lists"):
            with native_stages.kernel_session(_telemetry(PYTHON_LISTS), command=FAKE_KERNELS):
                pass
        self.assertRaisesRegex(
            native_stages.NativeStageError, "outside a kernel session",
            native_stages.kernel_call, "analysis_engine._dt_series", ([], 60.0), {},
        )

    def test_a_group_is_merged_into_one_store(self) -> None:
        sys.path.insert(0, str(WORKSPACE / "tools" / "parity"))
        import worker_corpus  # noqa: PLC0415

        directory = Path(tempfile.mkdtemp(prefix="native-group-"))
        self.addCleanup(shutil.rmtree, directory, True)
        paths = [str(path) for path in worker_corpus.build_case("reconnect", directory)]

        def run_native(binary: str, command: str, request: Any, what: str) -> None:
            self.assertEqual(command, "merge")
            _fake_merge(request)

        available = workflow._available_channel_names

        def without(dropped: set[str]) -> Callable[[Any], Any]:
            # The second recording lacks these channels: its merged rows have missing cells.
            def names(metadata: Any) -> Any:
                found = available(metadata)
                return found - dropped if dropped and Path(metadata["path"]).name == Path(paths[1]).name else found

            return names

        def load(stages: dict[str, str], dropped: set[str]) -> dict[str, Any]:
            with mock.patch.dict(os.environ, stages), \
                    mock.patch.object(native_stages, "_decode_store", _codec_store), \
                    mock.patch.object(native_stages, "_run_native", run_native), \
                    mock.patch.object(native_stages, "native_binary", return_value="coach-analysis"), \
                    mock.patch.object(workflow, "_available_channel_names", without(dropped)), \
                    mock.patch.object(workflow, "_assert_finalized_ibt"):
                return workflow._load_analysis_telemetry(paths, None)

        for dropped in (set(), {"Speed", "FuelLevel"}):
            with self.subTest(dropped=sorted(dropped)):
                lists, store = load(PYTHON_LISTS, dropped), load(STORE_STAGES, dropped)
                self.assertEqual(native_stages.store_of(store["samples"]).stage, "merge")
                self.assertEqual(any(None in values for values in lists["samples"].values()), bool(dropped))
                _strict_equal(self, {name: list(values) for name, values in store["samples"].items()}, lists["samples"])
                # Provenance as the oracle's, without the duplicates' contributors.
                expected = [{k: item[k] for k in item if k != "contributors"} for item in lists["sample_provenance"]]
                _strict_equal(self, list(store["sample_provenance"]), expected)
                for key in lists:
                    if key not in ("samples", "sample_provenance"):
                        _strict_equal(self, store[key], lists[key], key)
                expected_analysis = _analyze(lists)
                with native_stages.kernel_session(store, command=FAKE_KERNELS):
                    actual = _analyze(store)
                self.assertEqual(json.dumps(actual), json.dumps(expected_analysis))

    def test_the_workflow_runs_the_analysis_in_the_session(self) -> None:
        outputs: dict[str, str] = {}
        session = native_stages.kernel_session

        def run(label: str, stages: dict[str, str]) -> None:
            produce = workflow.analyze_telemetry

            def capture(*args: Any, **kwargs: Any) -> Any:
                result = produce(*args, **kwargs)
                outputs[label] = json.dumps(_untimed(dict(result)))
                return result

            archive = Path(tempfile.mkdtemp(prefix="native-archive-"))
            self.addCleanup(shutil.rmtree, archive, True)
            with mock.patch.dict(os.environ, {**stages, "IRACING_COACH_RAW_ARCHIVE": "external"}), \
                    mock.patch.object(native_stages, "_decode_store", _codec_store), \
                    mock.patch.object(native_stages, "native_binary", return_value="coach-analysis"), \
                    mock.patch.object(native_stages, "kernel_session", lambda t: session(t, command=FAKE_KERNELS)), \
                    mock.patch.object(workflow, "analyze_telemetry", capture), \
                    mock.patch.object(workflow, "_assert_finalized_ibt"):
                workflow.analyze_race_workflow(
                    selector=str(SYNTHETIC_RACE), iracing_root=str(SYNTHETIC_RACE.parent), archive_root=str(archive)
                )

        run("python", PYTHON_LISTS)
        counts, uninstall = native_stages.install_call_counters()
        try:
            run("kernels", STORE_STAGES)
        finally:
            uninstall()
        self.assertEqual(outputs["kernels"], outputs["python"])
        self.assertIn("analysis_engine._lap_summaries", counts["native"])
        self.assertEqual(counts["oracle"], {})


def _kernels_entries(binary: str) -> list[str] | None:
    """The entries a ``coach-analysis kernels`` build lists in its start answer (None: no start)."""

    store = _telemetry(STORE_STAGES)
    try:
        table = native_stages.StoreTable(store)
        session = native_stages._KernelSession([binary, "kernels"], native_stages.store_of(store["samples"]))
        session.attach(table, store)
        try:
            session._start()
        except native_stages.NativeStageError as exc:
            match = re.search(r"does not provide kernel\(s\) (.*)$", str(exc))
            if match is None:
                raise
            missing = match.group(1).split(", ")
            return [label for label in native_stages.KERNEL_LABELS if label not in missing]
        finally:
            session.close(failed=True)
        return sorted(session.entries)
    finally:
        native_stages.release(store["samples"])


@unittest.skipUnless(_binary(), "coach-analysis not available (IRACING_COACH_NATIVE_BIN or PATH)")
class NativeKernelsTests(unittest.TestCase):
    def test_the_child_speaks_the_protocol(self) -> None:
        store = _telemetry(STORE_STAGES)
        self.addCleanup(native_stages.release, store["samples"])
        table = native_stages.StoreTable(store)
        process = subprocess.Popen(
            [_binary(), "kernels"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        session = native_stages._KernelSession([], native_stages.store_of(store["samples"]))
        session.attach(table, store)
        session.process = process
        basis = native_codec.encode(analysis_engine._steering_basis(table), native_codec.Context(), None)
        meta = {
            "codec": 1, "length": table.length, "channel_names": list(table.channels),
            "metadata": native_codec.encode(table.metadata, native_codec.Context(), None),
            "session_info": native_codec.encode(table.session_info, native_codec.Context(), None),
            "variables": native_codec.encode(table.variables, native_codec.Context(), None),
            "available_variables": native_codec.encode(table.available_variables, native_codec.Context(), None),
            "channel_units": table.channel_units,
        }
        start = {"version": 1, "codec": 1, "store": session.store.path, "table": meta, "steering_basis": basis}
        answer = session._exchange(start, "start")
        self.assertEqual((answer["ok"], answer["length"]), (True, table.length))
        self.assertIsInstance(answer["entries"], list)
        self.assertTrue(set(answer["entries"]) <= set(native_stages.KERNEL_LABELS), answer["entries"])
        with self.assertRaisesRegex(native_stages.NativeStageError, "entry analysis_engine.nothing is not provided"):
            session._exchange({"entry": "analysis_engine.nothing", "args": [], "kwargs": {}}, "call")
        with self.assertRaisesRegex(native_stages.NativeStageError, "unknown field"):
            session._exchange({"entry": "x", "args": [], "kwargs": {}, "more": 1}, "call")
        process.stdin.close()
        self.assertEqual(process.wait(timeout=30), 0)
        process.stdout.close()
        process.stderr.close()

    def test_the_native_merge_is_the_workflow_merge(self) -> None:
        sys.path.insert(0, str(WORKSPACE / "tools" / "parity"))
        import worker_corpus  # noqa: PLC0415

        directory = Path(tempfile.mkdtemp(prefix="native-merge-"))
        self.addCleanup(shutil.rmtree, directory, True)
        paths = [str(path) for path in worker_corpus.build_case("reconnect", directory)]
        available = workflow._available_channel_names
        for dropped in (set(), {"Speed", "FuelLevel"}):
            def names(metadata: Any, dropped: set[str] = dropped) -> Any:
                found = available(metadata)
                return found - dropped if Path(metadata["path"]).name == Path(paths[1]).name else found

            with self.subTest(dropped=sorted(dropped)), \
                    mock.patch.object(workflow, "_available_channel_names", names), \
                    mock.patch.object(workflow, "_assert_finalized_ibt"):
                with mock.patch.dict(os.environ, PYTHON_LISTS):
                    lists = workflow._load_analysis_telemetry(paths, None)
                with mock.patch.dict(os.environ, STORE_STAGES):
                    store = workflow._load_analysis_telemetry(paths, None)
                try:
                    self.assertEqual(native_stages.store_of(store["samples"]).stage, "merge")
                    _strict_equal(self, {n: list(v) for n, v in store["samples"].items()}, lists["samples"])
                    expected = [{k: item[k] for k in item if k != "contributors"} for item in lists["sample_provenance"]]
                    _strict_equal(self, list(store["sample_provenance"]), expected)
                finally:
                    native_stages.release(store["samples"])

    def test_a_refused_start_fails_the_job(self) -> None:
        completed = subprocess.run(
            [_binary(), "kernels"], input=b'{"version": 2, "codec": 1}\n', capture_output=True, check=False
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(b'"error"', completed.stdout)

    def test_the_binary_session_is_the_oracle(self) -> None:
        binary = _binary()
        entries = _kernels_entries(binary)
        missing = [label for label in native_stages.KERNEL_LABELS if label not in (entries or [])]
        store = _telemetry(STORE_STAGES)
        if missing:  # a partial build must fail the job, naming what it lacks
            with self.assertRaisesRegex(native_stages.NativeStageError, re.escape(", ".join(missing))):
                with native_stages.kernel_session(store):
                    _analyze(store)
            self.skipTest(f"this build lacks {len(missing)} kernel(s); parity needs all")
        counts, uninstall = native_stages.install_call_counters()
        try:
            expected = _analyze(_telemetry(PYTHON_LISTS))
        finally:
            uninstall()
        native_counts, uninstall = native_stages.install_call_counters()
        try:
            with native_stages.kernel_session(store):
                actual = _analyze(store)
        finally:
            uninstall()
        self.assertEqual(json.dumps(actual), json.dumps(expected))
        self.assertEqual(native_stages.kernel_call_problems(counts, native_counts, True), [])


def _fake_kernels_main(flags: list[str]) -> int:
    """A ``kernels`` child that answers every call with the oracle's own entry point.

    Flags inject the failures the seam must turn into a failed job:
    ``--drop <label>``, ``--raise-on <label>``, ``--error-on <label>``,
    ``--exit-on <label>``, ``--bad-length``, ``--bad-accessed``, ``--no-accessed``,
    ``--both-outcomes``, ``--no-outcome``.
    """

    switches = ("--bad-length", "--bad-accessed", "--no-accessed", "--both-outcomes", "--no-outcome")
    options = dict(zip(flags[0::2], flags[1::2])) if not any(flag in flags for flag in switches) else {}
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

    def write(document: Any) -> None:
        stdout.write(native_codec.dumps(document).encode("utf-8") + b"\n")
        stdout.flush()

    start = json.loads(stdin.readline())
    store = native_stages.StoreFile(start["store"], stages=("decode", "merge"))
    plain = native_codec.Context()
    mapping: dict[str, Any] = {"samples": native_stages.StoreSamples(store)}
    for key in ("metadata", "session_info", "variables", "available_variables"):
        mapping[key] = native_codec.decode(start["table"][key], plain, {})
    table = native_stages.StoreTable(mapping)
    # The parent's basis, cached where _steering_basis caches it.
    table._columns[("steering-basis", None)] = native_codec.decode(start["steering_basis"], plain, {})
    handles: dict[int, Any] = {}
    context = native_codec.Context(table, handles=handles)
    entries = [label for label in native_stages.KERNEL_LABELS if label != options.get("--drop")]
    length = table.length + 1 if "--bad-length" in flags else table.length
    write({"ok": True, "length": length, "entries": entries})
    for line in stdin:
        request = json.loads(line)
        label = request["entry"]
        if label == options.get("--exit-on"):
            sys.stderr.write("fake kernels exits\n")
            return 3
        if label == options.get("--error-on"):
            write({"error": "injected"})
            continue
        owner, _, name = label.rpartition(".")
        function = getattr(analysis_engine if owner == "analysis_engine" else getattr(analysis_engine, owner), name)
        args = native_codec.decode(request["args"], context, {})
        kwargs = native_codec.decode(request["kwargs"], context, {})
        table.accessed_channels = set()
        try:
            if label == options.get("--raise-on"):
                raise ZeroDivisionError("injected")
            result = function(*args, **kwargs)
        except Exception as exc:  # the oracle's exception, by class and message
            answer: dict[str, Any] = {"raises": {"class": type(exc).__name__, "message": str(exc)}}
        else:
            if name == "_setup_channel_series":
                handles[len(handles)] = result
                result = native_codec.Handle(len(handles) - 1)
            answer = {"result": native_codec.encode(result, context, None)}
        answer["accessed"] = ["NoSuchChannel"] if "--bad-accessed" in flags else sorted(table.accessed_channels)
        if "--no-accessed" in flags:
            del answer["accessed"]
        if "--both-outcomes" in flags:
            answer["raises"] = {"class": "ValueError", "message": "injected"}
            answer.setdefault("result", None)
        if "--no-outcome" in flags:
            answer.pop("result", None)
            answer.pop("raises", None)
        write(answer)
    return 0


def _fake_merge(request: dict[str, Any]) -> None:
    """``coach-analysis merge`` as worker-interface.md section 7 specifies it, from the oracle's merge loop."""

    loaded = [native_stages.read_store(path) for path in request["inputs"]]
    names = request["channels"]
    references: list[tuple[int, float, int, int]] = []
    for file_index, (header, _indices, samples) in enumerate(loaded):
        times = samples.get("SessionTime") or samples.get("SessionTimeOfDay") or ()
        for sample_index in range(header["sampling"]["sample_count"]):
            time_value = workflow._finite_number(times[sample_index]) if sample_index < len(times) else None
            references.append((0 if time_value is not None else 1,
                               time_value if time_value is not None else float(file_index), file_index, sample_index))
    references.sort()
    merged: dict[str, list[Any]] = {name: [] for name in names}
    files: list[int] = []
    records: list[int] = []
    previous_time: float | None = None
    for has_no_time, time_value, file_index, sample_index in references:
        samples = loaded[file_index][2]
        if not (has_no_time == 0 and previous_time is not None and abs(time_value - previous_time) <= 1e-9):
            for name in names:
                values = samples.get(name) or ()
                merged[name].append(values[sample_index] if sample_index < len(values) else None)
            files.append(file_index)
            records.append(loaded[file_index][1][sample_index])
            previous_time = time_value if has_no_time == 0 else None
            continue
        for name in names:
            values = samples.get(name) or ()
            candidate = values[sample_index] if sample_index < len(values) else None
            if merged[name][-1] is None and candidate is not None:
                merged[name][-1] = candidate
    blocks = native_codec._Blocks()
    columns = []
    for name in names:
        variable = next(column for header, _, _ in loaded for column in header["columns"] if column["name"] == name)
        values = merged[name]
        entry = {key: variable[key] for key in ("name", "type_code", "count", "var_offset", "element")}
        element, count = variable["element"], variable["count"]
        if any(value is None for value in values):
            entry["present"] = blocks.place(bytes(0 if value is None else 1 for value in values))
        if element == "utf8":
            data, offsets = native_codec._text_block(values)
            entry["data"] = blocks.place(data)
            entry["text_offsets"] = blocks.place(offsets)
        else:
            filler = [0] * count
            rows = [filler if value is None else (value if count > 1 else [value]) for value in values]
            flat = [int(item) if element == "bool" else item for row in rows for item in row]
            entry["data"] = blocks.place(struct.pack(f"<{len(flat)}{native_codec._PACK[element]}", *flat))
        columns.append(entry)
    header = {
        "format": "coach-column-store", "version": 1, "stage": "merge", "producer": "test_native_stages.py",
        "sampling": {"sample_count": len(files)}, "channel_mode": "selected",
        "sample_indices": blocks.place(struct.pack(f"<{len(records)}I", *records)),
        "provenance": {"file_index": blocks.place(struct.pack(f"<{len(files)}H", *files))},
        "columns": columns,
    }
    native_codec._write_store(Path(request["out"]), header, blocks)


# ---------------------------------------------------------------------------
# Writer (port phase 3)
# ---------------------------------------------------------------------------


def _json_oracle(value: Any, mode: str) -> Any:
    """What each writer site's own Python code returns for ``value``."""

    if mode == "bytes":
        return storage._json_text(value).encode("utf-8")
    if mode == "sha256":
        return storage.stable_hash(value, 64)
    raise AssertionError(mode)


def _outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    try:
        return ("ok", call())
    except Exception as exc:  # noqa: BLE001 - compared by class and message
        return ("raises", type(exc).__name__, str(exc))


class _Label(str):
    pass


class _Broken:
    def __str__(self) -> str:
        raise RuntimeError("str() refused")


def _writer_values() -> list[Any]:
    cyclic: list[Any] = [1]
    cyclic.append(cyclic)
    shared = [0.1, "x"]
    return [
        {"b": 1.5, "a": [1, 2.0, -0.0, 1e300, 5e-324, 0.1, 2**63, -(2**70)], "c": None, "d": (True, False)},
        {"s": "é \x00\x1f\x7f\"\\\t\n😀", "k": {"z": {}, "y": []}, "shared": [shared, shared]},
        [float("nan")],
        {"x": float("-inf")},
        {1: 2, 1.5: 3, True: 4, -7: 5},
        {1: 2, "a": 3},
        {None: 1, 2: 3},
        cyclic,
        {"label": _Label("sub"), "path": Path("relative") / "x", "bytes": b"it's"},
        [1, _Broken()],
        [10**5000],
        "a" + chr(0xD800) + chr(0xDC00) + "b",
    ]


class WriterSeamTests(unittest.TestCase):
    """The three sites, the pickler and the count rule, without the executable."""

    def test_sites_use_python_without_the_stage(self) -> None:
        value = {"b": [1.5, None], "a": "é"}
        with mock.patch.dict(os.environ, {native_stages.STAGES_ENV: "decode"}), \
                mock.patch.object(native_stages, "writer_call", side_effect=AssertionError("native")):
            self.assertEqual(storage._analysis_json_bytes(value), storage._json_text(value).encode("utf-8"))
            self.assertEqual(workflow._analysis_sha256(value), storage.stable_hash(value, 64))
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "cache" / "x.json"
                workflow._write_json_cache(target, value)
                self.assertEqual(target.read_text(encoding="utf-8"), storage._json_text(value))

    def test_sites_call_the_writer_with_their_mode(self) -> None:
        calls: list[tuple[Any, ...]] = []

        def native(site: str, mode: str, value: Any, oracle: Callable[[], Any], *, path: str | None = None) -> Any:
            calls.append((site, mode, path is not None))
            return oracle()  # the site's own code, so the results stay checkable

        value = {"b": 1, "a": [2.5]}
        with mock.patch.dict(os.environ, {native_stages.STAGES_ENV: "writer"}), \
                mock.patch.object(native_stages, "writer_call", side_effect=native):
            self.assertEqual(storage._analysis_json_bytes(value), storage._json_text(value).encode("utf-8"))
            self.assertEqual(workflow._analysis_sha256(value), storage.stable_hash(value, 64))
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "x.json"
                workflow._write_json_cache(target, value)
                self.assertEqual(target.read_text(encoding="utf-8"), storage._json_text(value))
        self.assertEqual(
            calls, [("analysis_json", "bytes", False), ("analysis_sha256", "sha256", False), ("json_cache", "file", True)]
        )

    def test_plain_values_take_the_pickler_fast_paths(self) -> None:
        seen: list[Any] = []

        class Watching(native_stages._WriterPickler):
            def reducer_override(self, obj: Any) -> Any:
                seen.append(obj)
                return super().reducer_override(obj)

        plain = {"a": [1, 2.5, None, True, "x", b"y", (1, 2), {"k": -(2**80)}]}
        Watching(io.BytesIO(), native_stages._writer_options(False, False)).dump(plain)
        self.assertEqual(seen, [])
        # Any other object stops the pickle (Codex phase-3 review, finding 1).
        with self.assertRaises(native_stages._WriterUnsupported):
            Watching(io.BytesIO(), native_stages._writer_options(False, False)).dump([_Label("q")])
        self.assertEqual(seen, ["q"])
        self.assertIs(type(seen[0]), _Label)

    def _fallback_only(self, make: Callable[[], Any], mode: str = "bytes") -> tuple[Any, Any, dict[str, Any]]:
        """``writer_call`` on a fresh ``make()`` (no executable may start) against the oracle
        on another fresh ``make()``: (native outcome, oracle outcome, counts)."""

        counts, uninstall = native_stages.install_call_counters()
        try:
            with mock.patch.object(native_stages.subprocess, "run", side_effect=AssertionError("the writer started")):
                value = make()
                native = _outcome(lambda: native_stages.writer_call(
                    "analysis_json", mode, value, lambda: _json_oracle(value, mode)
                ))
        finally:
            uninstall()
        fresh = make()
        return native, _outcome(lambda: _json_oracle(fresh, mode)), counts

    def test_default_str_runs_once_in_python_order(self) -> None:
        """Codex phase-3 review, finding 1: ``default=str`` and a subclass's methods are the
        oracle's to call, once per occurrence, in its sorted traversal order, and no fallback
        follows an earlier evaluation. Each value is fresh on both sides."""

        class Counting:
            def __init__(self, box: list[int]) -> None:
                self.box = box

            def __str__(self) -> str:
                self.box[0] += 1
                return str(self.box[0])

        def twice() -> Any:
            shared = Counting([0])
            return [shared, shared]

        def sorted_keys() -> Any:
            box = [0]
            value = {}
            value["z"] = Counting(box)
            value["a"] = Counting(box)
            return value

        def before_a_set() -> Any:
            return [Counting([0]), {1}]

        def subclass_items() -> Any:
            calls = [0]

            class Items(dict):
                def items(self):  # type: ignore[override]
                    calls[0] += 1
                    return [("n", calls[0])]

            return {"b": Items(), "a": Items()}

        # The oracle's bytes, as CPython 3.12 writes them (the review's reproductions).
        expected = {
            "twice": b'["1","2"]\n',
            "sorted_keys": b'{"a":"1","z":"2"}\n',
            "before_a_set": b'["1","{1}"]\n',
        }
        for make in (twice, sorted_keys, before_a_set, subclass_items):
            for mode in ("bytes", "sha256"):
                with self.subTest(make.__name__, mode=mode):
                    native, oracle, counts = self._fallback_only(make, mode)
                    self.assertEqual(native, oracle)
                    self.assertEqual(counts["writer"], {"analysis_json": 1})
                    self.assertEqual(counts["writer_fallback"], {"analysis_json": 1})
                    if mode == "bytes" and make.__name__ in expected:
                        self.assertEqual(native, ("ok", expected[make.__name__]))

    def test_container_subclasses_are_counted_fallbacks(self) -> None:
        """Codex phase-3 review, finding 4: a list or dict subclass (holding a set, or a whole
        ``lap_traces``) is no Python-written fragment inside a native write: the whole call
        falls back and is counted, so the gate fails on it."""

        class Seq(list):
            pass

        class Record(dict):
            pass

        sites = {"analysis_json": 1, "analysis_sha256": 1, "json_cache": 1}
        for number, make in enumerate((
            lambda: {"lap_traces": Seq([{1, 2}])},
            lambda: {"lap_traces": Seq([{"distance": [0.5, 1.5]}])},
            lambda: [Record(a=1)],
            lambda: (Seq(),),
        )):
            with self.subTest(number):
                native, oracle, counts = self._fallback_only(make)
                self.assertEqual(native, oracle)
                self.assertEqual(counts["writer_fallback"], {"analysis_json": 1})
                gate = native_stages.writer_call_problems(
                    {"writer": {}, "writer_sites": sites, "writer_fallback": {}},
                    {"writer": sites, "writer_sites": sites, "writer_fallback": counts["writer_fallback"]},
                    True,
                )
                self.assertTrue(any("fell back" in problem for problem in gate), gate)

    def test_an_object_whose_str_raises_raises_from_the_site(self) -> None:
        native, oracle, counts = self._fallback_only(lambda: [1, _Broken()])
        self.assertEqual(native, ("raises", "RuntimeError", "str() refused"))
        self.assertEqual(native, oracle)
        self.assertEqual(counts["writer_fallback"], {"analysis_json": 1})

    def test_count_rule(self) -> None:
        sites = {site: 1 for site in native_stages.WRITER_SITES}
        oracle = {"writer": {}, "writer_sites": dict(sites), "writer_fallback": {}}
        native = {"writer": dict(sites), "writer_sites": dict(sites), "writer_fallback": {}}
        self.assertEqual(native_stages.writer_call_problems(oracle, native, True), [])
        python_side = {"writer": {}, "writer_sites": dict(sites), "writer_fallback": {}}
        self.assertEqual(native_stages.writer_call_problems(oracle, python_side, False), [])
        self.assertTrue(native_stages.writer_call_problems(oracle, python_side, True))
        fell_back = {**native, "writer_fallback": {"analysis_json": 1}}
        self.assertTrue(native_stages.writer_call_problems(oracle, fell_back, True))
        fewer = {**native, "writer_sites": {**sites, "json_cache": 2}}
        self.assertTrue(native_stages.writer_call_problems(oracle, fewer, True))
        self.assertTrue(native_stages.writer_call_problems({"writer_sites": sites}, native, True))
        unreached = {"writer": {}, "writer_sites": {"analysis_json": 1}, "writer_fallback": {}}
        self.assertTrue(native_stages.writer_call_problems(unreached, unreached, False))


@unittest.skipUnless(_binary(), "coach-analysis not available (IRACING_COACH_NATIVE_BIN or PATH)")
class NativeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {native_stages.BINARY_ENV: _binary() or ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bytes_and_hash_are_the_oracle(self) -> None:
        for number, value in enumerate(_writer_values()):
            for mode in ("bytes", "sha256"):
                expected = _outcome(lambda: _json_oracle(value, mode))
                got = _outcome(
                    lambda: native_stages.writer_call("analysis_json", mode, value, lambda: _json_oracle(value, mode))
                )
                self.assertEqual(got, expected, (mode, number))

    def test_the_cache_file_is_the_oracle_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for number, value in enumerate(_writer_values()):
                python_path, native_path = Path(directory) / f"p{number}.json", Path(directory) / f"n{number}.json"

                def oracle() -> None:
                    python_path.write_text(storage._json_text(value), encoding="utf-8")

                expected = _outcome(oracle)
                got = _outcome(lambda: native_stages.writer_call(
                    # The site's own fallback returns None, as the oracle does (not write_text's count).
                    "json_cache", "file", value, lambda: [native_path.write_text(storage._json_text(value), encoding="utf-8")] and None,
                    path=str(native_path),
                ))
                self.assertEqual(got, expected, number)
                self.assertEqual(python_path.is_file(), native_path.is_file(), number)
                if python_path.is_file():
                    self.assertEqual(python_path.read_bytes(), native_path.read_bytes(), number)

    def test_values_outside_the_model_are_written_by_the_site_and_counted(self) -> None:
        counts, uninstall = native_stages.install_call_counters()
        try:
            deep: Any = 0
            for _ in range(300):
                deep = [deep]
            for value in ([{1, 2}], {(1, 2): 3}, {_Label("k"): 1}, [bytearray(b"x")], deep):
                self.assertEqual(
                    _outcome(lambda: native_stages.writer_call(
                        "analysis_json", "bytes", value, lambda: _json_oracle(value, "bytes")
                    )),
                    _outcome(lambda: _json_oracle(value, "bytes")),
                )
        finally:
            uninstall()
        self.assertEqual(counts["writer"], {"analysis_json": 5})
        self.assertEqual(counts["writer_fallback"], {"analysis_json": 5})

    def test_a_bad_request_fails_the_job(self) -> None:
        completed = subprocess.run([_binary(), "write"], input=b"{}\n", capture_output=True, check=False)
        self.assertEqual(completed.returncode, 1)
        with self.assertRaisesRegex(native_stages.NativeStageError, "unknown writer site"):
            native_stages.writer_call("elsewhere", "bytes", {}, lambda: None)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fake-kernels"]:
        raise SystemExit(_fake_kernels_main(sys.argv[2:]))
    if sys.argv[1:2] == ["--fake-merge"]:
        _fake_merge(json.loads(sys.stdin.buffer.read()))
        raise SystemExit(0)
    unittest.main()
