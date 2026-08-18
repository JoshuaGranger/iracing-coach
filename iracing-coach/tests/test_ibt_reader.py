from __future__ import annotations

import hashlib
import importlib.util
import os
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
SPEC = importlib.util.spec_from_file_location("ibt_reader", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ibt_reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ibt_reader
SPEC.loader.exec_module(ibt_reader)


HEADER = struct.Struct("<28i")
DISK_SUBHEADER = struct.Struct("<qddii")
VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")
BUFFER_LENGTH = 64
TICK_RATE = 60


def _align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) // alignment * alignment


def _fixed_bytes(value: str, length: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= length:
        raise ValueError("fixture string is too long")
    return encoded + b"\x00" * (length - len(encoded))


def _fixture_yaml(
    subsession_id: int,
    session_id: int,
    event_type: str,
) -> str:
    return f"""WeekendInfo:
  TrackName: Iowa Speedway
  TrackDisplayName: Iowa Speedway
  TrackConfigName: Oval
  TrackID: 123
  TrackLength: 1.44 km
  SeriesID: 42
  SeasonID: 202603
  SessionID: {session_id}
  SubSessionID: {subsession_id}
  RaceWeek: 5
  EventType: {event_type}
  Category: Oval
  Official: 1
  WeekendOptions:
    IsFixedSetup: 1
DriverInfo:
  DriverCarIdx: 0
  DriverSetupName: synthetic-race.sto
  DriverSetupModified: false
  Drivers:
    - CarIdx: 0
      UserID: 777
      CarID: 67
      CarPath: stockcars nextgen mustang
      CarScreenName: NASCAR Cup Series Next Gen
SessionInfo:
  Sessions:
    - SessionNum: 0
      SessionType: Practice
    - SessionNum: 1
      SessionType: Race
CarSetup:
  Tires:
    LeftFront:
      ColdPressure: 25.0 psi
"""


def build_ibt(
    path: Path,
    *,
    base_epoch: int = 1_780_000_000,
    start_offset: float = 0.0,
    subsession_id: int = 200,
    session_id: int = 100,
    session_num: int = 1,
    session_unique_id: int = 9001,
    event_type: str = "Race",
    record_count: int = 6,
    bad_type: bool = False,
    bad_offset: bool = False,
) -> None:
    yaml_blob = _fixture_yaml(subsession_id, session_id, event_type).encode("utf-8")
    yaml_blob += b"\x00"

    variables = [
        (2, 0, 1, False, "SessionNum", "Simulator session number", ""),
        (2, 4, 1, False, "SessionUniqueID", "Simulator session ID", ""),
        (5, 8, 1, False, "SessionTime", "Session time", "s"),
        (99 if bad_type else 4, 80 if bad_offset else 16, 1, False, "Speed", "Speed", "m/s"),
        (4, 20, 3, True, "TireTemps", "Synthetic tire temperatures", "C"),
        (1, 32, 1, False, "OnPitRoad", "Car is on pit road", ""),
        (3, 36, 1, False, "SessionFlags", "Session flag bits", "irsdk_Flags"),
        (0, 40, 16, False, "DriverName", "Synthetic name", ""),
    ]

    session_info_offset = HEADER.size + DISK_SUBHEADER.size
    variable_header_offset = _align(session_info_offset + len(yaml_blob))
    sample_offset = _align(variable_header_offset + len(variables) * VAR_HEADER.size)
    file_size = sample_offset + record_count * BUFFER_LENGTH
    output = bytearray(file_size)

    header_values = [
        2,
        1,
        TICK_RATE,
        1,
        len(yaml_blob),
        session_info_offset,
        len(variables),
        variable_header_offset,
        1,
        BUFFER_LENGTH,
        0,
        0,
        max(record_count - 1, 0),
        sample_offset,
        0,
        0,
    ]
    header_values.extend([0] * 12)
    HEADER.pack_into(output, 0, *header_values)
    DISK_SUBHEADER.pack_into(
        output,
        HEADER.size,
        base_epoch,
        start_offset,
        start_offset + max(record_count - 1, 0) / TICK_RATE,
        1,
        record_count,
    )
    output[session_info_offset : session_info_offset + len(yaml_blob)] = yaml_blob

    for index, variable in enumerate(variables):
        type_code, offset, count, count_as_time, name, desc, unit = variable
        VAR_HEADER.pack_into(
            output,
            variable_header_offset + index * VAR_HEADER.size,
            type_code,
            offset,
            count,
            int(count_as_time),
            _fixed_bytes(name, 32),
            _fixed_bytes(desc, 64),
            _fixed_bytes(unit, 32),
        )

    for index in range(record_count):
        record_offset = sample_offset + index * BUFFER_LENGTH
        struct.pack_into("<i", output, record_offset + 0, session_num)
        struct.pack_into("<i", output, record_offset + 4, session_unique_id)
        struct.pack_into("<d", output, record_offset + 8, start_offset + index / TICK_RATE)
        struct.pack_into("<f", output, record_offset + 16, 100.0 + index)
        struct.pack_into(
            "<3f",
            output,
            record_offset + 20,
            80.0 + index,
            81.0 + index,
            82.0 + index,
        )
        struct.pack_into("<?", output, record_offset + 32, index >= 4)
        struct.pack_into("<I", output, record_offset + 36, 0x80000000 + index)
        name = f"Driver {index}".encode("utf-8")
        output[record_offset + 40 : record_offset + 40 + len(name)] = name

    path.write_bytes(output)


class IbtReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_blank_scalar_is_null_and_not_an_empty_mapping(self) -> None:
        # Ovals with no configuration emit `TrackConfigName: ` with nothing after
        # it. Reading that as {} made _as_text render the literal "{}" - the
        # stray glyph after Richmond - while `value or fallback` recovered the
        # track name, because {} is falsy. Race matching compares layout
        # equality, so the two paths disagreeing silently emptied Progressive
        # Tuning. yaml.safe_load returns None here; the bundled parser must too.
        parsed = ibt_reader._parse_iracing_yaml_subset(
            "WeekendInfo:\n"
            "  TrackDisplayName: Richmond Raceway\n"
            "  TrackConfigName: \n"
            "  TrackID: 561\n",
            Path("richmond.ibt"),
        )
        weekend = parsed["WeekendInfo"]

        self.assertIsNone(weekend["TrackConfigName"])
        self.assertIsNone(ibt_reader._as_text(weekend["TrackConfigName"]))
        self.assertNotIn("{}", str(ibt_reader._as_text(weekend["TrackConfigName"])))
        # A blank value must not swallow the keys that follow it.
        self.assertEqual(561, weekend["TrackID"])

    def test_scan_parses_metadata_catalogue_and_array_variable(self) -> None:
        source = self.root / "race.ibt"
        build_ibt(source)
        before = hashlib.sha256(source.read_bytes()).digest()

        metadata = ibt_reader.scan_ibt(source)

        self.assertEqual(metadata["header"]["version"], 2)
        self.assertEqual(metadata["header"]["tick_rate"], 60)
        self.assertEqual(metadata["record_count"], 6)
        self.assertEqual(metadata["subsession_id"], 200)
        self.assertEqual(metadata["sim_session_num"], 1)
        self.assertEqual(metadata["sim_session_type"], "Race")
        self.assertTrue(metadata["is_race"])
        self.assertEqual(metadata["track_config_name"], "Oval")
        self.assertEqual(metadata["car_id"], 67)
        self.assertTrue(metadata["is_fixed_setup"])
        self.assertEqual(
            metadata["session_info"]["CarSetup"]["Tires"]["LeftFront"][
                "ColdPressure"
            ],
            "25.0 psi",
        )
        tire_variable = next(
            item for item in metadata["variables"] if item["name"] == "TireTemps"
        )
        self.assertEqual(tire_variable["type"], "float")
        self.assertEqual(tire_variable["count"], 3)
        self.assertTrue(tire_variable["count_as_time"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_selected_channel_downsample_preserves_arrays_and_strings(self) -> None:
        source = self.root / "race.ibt"
        build_ibt(source)

        result = ibt_reader.load_telemetry(
            source,
            channels=["SessionTime", "Speed", "TireTemps", "DriverName", "SessionFlags"],
            target_hz=20,
        )

        self.assertEqual(result["sample_indices"], [0, 3])
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["sample_rate_hz"], 20.0)
        self.assertEqual(result["samples"]["Speed"], [100.0, 103.0])
        self.assertEqual(
            result["samples"]["TireTemps"],
            [[80.0, 81.0, 82.0], [83.0, 84.0, 85.0]],
        )
        self.assertEqual(result["samples"]["DriverName"], ["Driver 0", "Driver 3"])
        self.assertEqual(
            result["samples"]["SessionFlags"],
            [0x80000000, 0x80000003],
        )
        self.assertEqual(result["metadata"]["subsession_id"], 200)
        self.assertNotIn("session_info", result["metadata"])

    def test_full_rate_and_missing_channel_validation(self) -> None:
        source = self.root / "race.ibt"
        build_ibt(source)
        result = ibt_reader.load_telemetry(source, channels="OnPitRoad", target_hz=None)
        self.assertEqual(result["sample_count"], 6)
        self.assertEqual(
            result["samples"]["OnPitRoad"],
            [False, False, False, False, True, True],
        )
        with self.assertRaises(ibt_reader.IbtChannelError):
            ibt_reader.load_telemetry(source, channels=["NotARealChannel"])
        with self.assertRaises(ValueError):
            ibt_reader.load_telemetry(source, channels=["Speed"], target_hz=0)

    def test_truncation_type_and_variable_bounds_have_specific_errors(self) -> None:
        truncated = self.root / "truncated.ibt"
        build_ibt(truncated)
        truncated.write_bytes(truncated.read_bytes()[:-1])
        with self.assertRaises(ibt_reader.IbtTruncatedError):
            ibt_reader.scan_ibt(truncated)

        bad_type = self.root / "bad-type.ibt"
        build_ibt(bad_type, bad_type=True)
        with self.assertRaises(ibt_reader.IbtTypeError):
            ibt_reader.scan_ibt(bad_type)

        bad_bounds = self.root / "bad-bounds.ibt"
        build_ibt(bad_bounds, bad_offset=True)
        with self.assertRaises(ibt_reader.IbtBoundsError):
            ibt_reader.scan_ibt(bad_bounds)

    def test_discovery_groups_car_entries_and_selects_latest_race(self) -> None:
        base = 1_780_000_000
        build_ibt(
            self.root / "old-entry-a.ibt",
            base_epoch=base,
            start_offset=0,
            subsession_id=11,
            session_id=101,
            session_num=1,
        )
        build_ibt(
            self.root / "old-entry-b.ibt",
            base_epoch=base,
            start_offset=10,
            subsession_id=11,
            session_id=101,
            session_num=1,
        )
        build_ibt(
            self.root / "new-race.ibt",
            base_epoch=base + 100,
            subsession_id=13,
            session_id=103,
            session_num=1,
        )
        # This file is newer, but SessionNum maps it to Practice and it must not
        # displace the most recent Race.
        build_ibt(
            self.root / "newer-practice.ibt",
            base_epoch=base + 200,
            subsession_id=12,
            session_id=102,
            session_num=0,
        )

        all_results = ibt_reader.discover_sessions(self.root)
        groups = [item for item in all_results if item["kind"] == "session"]
        self.assertEqual(len(groups), 3)
        old_group = next(item for item in groups if item["subsession_id"] == 11)
        self.assertEqual(old_group["sim_session_num"], 1)
        self.assertEqual(old_group["file_count"], 2)

        latest = ibt_reader.discover_sessions(self.root, latest_only=True)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["subsession_id"], 13)
        self.assertTrue(latest[0]["is_race"])

    def test_discovery_uses_mtime_only_when_metadata_time_is_invalid(self) -> None:
        older = self.root / "older.ibt"
        newer = self.root / "newer.ibt"
        build_ibt(older, base_epoch=0, subsession_id=21, session_id=201)
        build_ibt(newer, base_epoch=0, subsession_id=22, session_id=202)
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_000_100, 1_700_000_100))

        latest = ibt_reader.discover_sessions(self.root, latest_only=True)
        self.assertEqual(latest[0]["subsession_id"], 22)
        self.assertEqual(latest[0]["time_source"], "mtime")

    def test_full_discovery_reports_bad_files_without_hiding_good_ones(self) -> None:
        good = self.root / "good.ibt"
        bad = self.root / "partial.ibt"
        build_ibt(good)
        bad.write_bytes(b"not an ibt")

        results = ibt_reader.discover_sessions(self.root)

        self.assertEqual(len([item for item in results if item["kind"] == "session"]), 1)
        errors = [item for item in results if item["kind"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error_type"], "IbtTruncatedError")


if __name__ == "__main__":
    unittest.main()
