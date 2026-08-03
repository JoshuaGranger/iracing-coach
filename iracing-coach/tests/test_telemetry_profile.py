from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "analyze-iracing-race"
    / "scripts"
    / "ibt_reader.py"
)
SPEC = importlib.util.spec_from_file_location("ibt_reader_profile_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ibt_reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ibt_reader
SPEC.loader.exec_module(ibt_reader)


HEADER = struct.Struct("<28i")
DISK_SUBHEADER = struct.Struct("<qddii")
VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")
BUFFER_LENGTH = 64
TICK_RATE = 6
RECORD_COUNT = 6


def _align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) // alignment * alignment


def _fixed_bytes(value: str, length: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= length:
        raise ValueError("fixture string is too long")
    return encoded + b"\x00" * (length - len(encoded))


def build_profile_ibt(path: Path) -> None:
    variables = [
        (5, 0, 1, False, "SessionTime", "Session time", "s"),
        (2, 8, 1, False, "RPM", "Engine speed", "rpm"),
        (4, 12, 1, False, "Speed", "Vehicle speed", "m/s"),
        (1, 16, 1, False, "OnPitRoad", "Pit state", ""),
        (3, 20, 1, False, "SessionFlags", "Session flags", "irsdk_Flags"),
        (
            4,
            24,
            3,
            True,
            "Torque_ST",
            "Synthetic sub-tick torque",
            "N*m",
        ),
        (0, 36, 12, False, "DriverName", "Synthetic driver name", ""),
    ]
    fixed_size = HEADER.size + DISK_SUBHEADER.size
    variable_header_offset = _align(fixed_size)
    sample_offset = _align(variable_header_offset + len(variables) * VAR_HEADER.size)
    output = bytearray(sample_offset + RECORD_COUNT * BUFFER_LENGTH)

    header_values = [
        2,
        1,
        TICK_RATE,
        0,
        0,
        fixed_size,
        len(variables),
        variable_header_offset,
        1,
        BUFFER_LENGTH,
        0,
        0,
        RECORD_COUNT - 1,
        sample_offset,
        0,
        0,
    ]
    header_values.extend([0] * 12)
    HEADER.pack_into(output, 0, *header_values)
    DISK_SUBHEADER.pack_into(
        output,
        HEADER.size,
        1_780_000_000,
        0.0,
        (RECORD_COUNT - 1) / TICK_RATE,
        1,
        RECORD_COUNT,
    )

    for index, variable in enumerate(variables):
        type_code, offset, count, count_as_time, name, description, unit = variable
        VAR_HEADER.pack_into(
            output,
            variable_header_offset + index * VAR_HEADER.size,
            type_code,
            offset,
            count,
            int(count_as_time),
            _fixed_bytes(name, 32),
            _fixed_bytes(description, 64),
            _fixed_bytes(unit, 32),
        )

    rpm_values = [100, 100, 120, 120, 110, 110]
    pit_values = [False, False, True, True, False, False]
    flag_values = [1, 1, 3, 3, 0, 4]
    name_values = ["A", "A", "B", "B", "", "C"]
    for index in range(RECORD_COUNT):
        record_offset = sample_offset + index * BUFFER_LENGTH
        struct.pack_into("<d", output, record_offset, index / TICK_RATE)
        struct.pack_into("<i", output, record_offset + 8, rpm_values[index])
        struct.pack_into("<f", output, record_offset + 12, 10.0 + index)
        struct.pack_into("<?", output, record_offset + 16, pit_values[index])
        struct.pack_into("<I", output, record_offset + 20, flag_values[index])
        struct.pack_into(
            "<3f",
            output,
            record_offset + 24,
            index * 3.0,
            index * 3.0 + 1.0,
            index * 3.0 + 2.0,
        )
        encoded_name = name_values[index].encode("utf-8")
        output[
            record_offset + 36 : record_offset + 36 + len(encoded_name)
        ] = encoded_name

    path.write_bytes(output)


class TelemetryProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "profile.ibt"
        build_profile_ibt(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_chunk_iterator_preserves_native_indices_and_source(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).digest()

        chunks = list(
            ibt_reader.iter_telemetry_chunks(
                self.path,
                channels=["SessionTime", "Speed", "Torque_ST"],
                target_hz=None,
                chunk_size=4,
            )
        )

        self.assertEqual([chunk["sample_count"] for chunk in chunks], [4, 2])
        self.assertEqual(chunks[0]["sample_indices"], [0, 1, 2, 3])
        self.assertEqual(chunks[1]["sample_indices"], [4, 5])
        self.assertEqual(chunks[0]["first_native_index"], 0)
        self.assertEqual(chunks[1]["last_native_index"], 5)
        self.assertEqual(chunks[0]["sample_rate_hz"], float(TICK_RATE))
        self.assertEqual(chunks[0]["samples"]["Speed"], [10.0, 11.0, 12.0, 13.0])
        self.assertEqual(chunks[1]["samples"]["Torque_ST"][-1], [15.0, 16.0, 17.0])

        downsampled = list(
            ibt_reader.iter_telemetry_chunks(
                self.path,
                channels="RPM",
                target_hz=2,
                chunk_size=1,
            )
        )
        self.assertEqual(
            [chunk["sample_indices"] for chunk in downsampled], [[0], [3]]
        )
        self.assertEqual(
            [chunk["samples"]["RPM"][0] for chunk in downsampled], [100, 120]
        )

        bounded = list(
            ibt_reader.iter_telemetry_chunks(
                self.path,
                channels="Speed",
                target_hz=None,
                chunk_size=2,
                start_record=2,
                end_record=5,
            )
        )
        self.assertEqual(
            [chunk["sample_indices"] for chunk in bounded], [[2, 3], [4]]
        )
        self.assertEqual(bounded[0]["record_start"], 2)
        self.assertEqual(bounded[0]["record_end"], 5)
        self.assertEqual(
            [value for chunk in bounded for value in chunk["samples"]["Speed"]],
            [12.0, 13.0, 14.0],
        )
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_chunk_iterator_validates_bounded_chunk_size(self) -> None:
        for invalid in (0, -1, True, 1.5, 8_193):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    list(
                        ibt_reader.iter_telemetry_chunks(
                            self.path, channels="Speed", chunk_size=invalid
                        )
                    )

        invalid_bounds = (
            {"start_record": -1},
            {"start_record": True},
            {"end_record": 7},
            {"end_record": False},
            {"start_record": 4, "end_record": 3},
        )
        for bounds in invalid_bounds:
            with self.subTest(bounds=bounds):
                with self.assertRaises(ValueError):
                    list(
                        ibt_reader.iter_telemetry_chunks(
                            self.path, channels="Speed", **bounds
                        )
                    )

    def test_profile_streams_all_types_and_array_elements(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).digest()

        profile = ibt_reader.profile_telemetry(self.path, chunk_size=2)

        self.assertEqual(profile["sample_count"], RECORD_COUNT)
        self.assertEqual(profile["source_record_count"], RECORD_COUNT)
        self.assertEqual(profile["channel_count"], 7)
        self.assertEqual(profile["chunks_processed"], 3)
        self.assertEqual(profile["profile_rate_hz"], float(TICK_RATE))
        self.assertEqual(len(profile["channel_catalog"]), 7)

        rpm = profile["channels"]["RPM"]
        self.assertEqual(rpm["min"], 100)
        self.assertEqual(rpm["max"], 120)
        self.assertEqual(rpm["mean"], 110.0)
        self.assertEqual(rpm["change_count"], 2)

        speed = profile["channels"]["Speed"]
        self.assertEqual(speed["min"], 10.0)
        self.assertEqual(speed["max"], 15.0)
        self.assertEqual(speed["mean"], 12.5)
        self.assertEqual(speed["change_count"], 5)

        pit = profile["channels"]["OnPitRoad"]
        self.assertEqual(pit["true_count"], 2)
        self.assertEqual(pit["false_count"], 4)
        self.assertEqual(pit["transitions"], 2)

        flags = profile["channels"]["SessionFlags"]
        self.assertEqual(flags["observed_or"], 7)
        self.assertEqual(flags["observed_and"], 0)
        self.assertEqual(flags["transitions"], 3)

        torque = profile["channels"]["Torque_ST"]
        self.assertEqual(torque["value_shape"], "array")
        self.assertEqual(torque["change_count"], 5)
        self.assertEqual(torque["effective_sample_rate_hz"], 18.0)
        self.assertEqual(torque["profiled_effective_sample_rate_hz"], 18.0)
        self.assertEqual(len(torque["elements"]), 3)
        self.assertEqual(torque["elements"][0]["min"], 0.0)
        self.assertEqual(torque["elements"][0]["max"], 15.0)
        self.assertEqual(torque["elements"][0]["mean"], 7.5)
        self.assertEqual(torque["elements"][2]["mean"], 9.5)

        driver = profile["channels"]["DriverName"]
        self.assertEqual(driver["value_shape"], "string")
        self.assertEqual(driver["non_empty_count"], 5)
        self.assertEqual(driver["change_count"], 3)
        self.assertEqual(driver["observed_distinct_count"], 4)

        json.dumps(profile, allow_nan=False)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_downsampled_profile_reports_native_and_profiled_array_rates(self) -> None:
        profile = ibt_reader.profile_telemetry(
            self.path, target_hz=2, chunk_size=1
        )
        torque = profile["channels"]["Torque_ST"]

        self.assertEqual(profile["sample_count"], 2)
        self.assertEqual(profile["profile_rate_hz"], 2.0)
        self.assertEqual(torque["effective_sample_rate_hz"], 18.0)
        self.assertEqual(torque["profiled_effective_sample_rate_hz"], 6.0)

    def test_profile_supports_selected_channels_and_native_record_bounds(self) -> None:
        profile = ibt_reader.profile_telemetry(
            self.path,
            channels=["RPM", "Torque_ST"],
            start_record=1,
            end_record=5,
            chunk_size=2,
        )

        self.assertEqual(profile["record_start"], 1)
        self.assertEqual(profile["record_end"], 5)
        self.assertEqual(profile["sample_count"], 4)
        self.assertEqual(profile["available_channel_count"], 7)
        self.assertEqual(profile["channel_count"], 2)
        self.assertEqual(profile["channel_selection"], "selected")
        self.assertEqual(set(profile["channels"]), {"RPM", "Torque_ST"})
        self.assertEqual(profile["channels"]["RPM"]["mean"], 112.5)
        self.assertEqual(profile["channels"]["RPM"]["change_count"], 2)
        self.assertEqual(profile["channels"]["Torque_ST"]["sample_count"], 4)


if __name__ == "__main__":
    unittest.main()
