"""The native-stage seam (worker-interface.md section 7, port phase 1).

``IRACING_COACH_NATIVE_STAGES`` selects native stages; with ``decode`` the
worker reads a coach column store written by ``coach-analysis decode`` and
must hand the analysis exactly the table ``ibt_reader.load_telemetry``
returns. The end-to-end cases need the executable (``IRACING_COACH_NATIVE_BIN``
or ``coach-analysis`` on PATH) and are skipped without it; the store reader
and the stage selection are tested without it.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
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
        with self.assertRaisesRegex(native_stages.NativeStageError, "does not provide"):
            native_stages.requested_stages({native_stages.STAGES_ENV: "decode,kernels"})

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


if __name__ == "__main__":
    unittest.main()
