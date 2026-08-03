from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "analyze-iracing-race"
    / "scripts"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "native_events.py"
SPEC = importlib.util.spec_from_file_location("native_events_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
native_events = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native_events
SPEC.loader.exec_module(native_events)
import workflow  # noqa: E402


HEADER = struct.Struct("<28i")
DISK_SUBHEADER = struct.Struct("<qddii")
VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")
TICK_RATE = 10
RECORDS_PER_LAP = 12
RECORD_COUNT = 48
BUFFER_LENGTH = 80


def _align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) // alignment * alignment


def _fixed(value: str, length: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= length:
        raise ValueError(value)
    return encoded + b"\x00" * (length - len(encoded))


FULL_VARIABLES = [
    (5, 0, 1, False, "SessionTime", "Session time", "s"),
    (2, 8, 1, False, "Lap", "Lap number", ""),
    (4, 12, 1, False, "LapDistPct", "Lap distance", "%"),
    (4, 16, 1, False, "Brake", "Brake input", "%"),
    (1, 20, 1, False, "OnPitRoad", "Pit state", ""),
    (4, 24, 1, False, "Speed", "Vehicle speed", "m/s"),
    (4, 28, 1, False, "LFspeed", "Left-front speed", "m/s"),
    (4, 32, 1, False, "RFspeed", "Right-front speed", "m/s"),
    (4, 36, 1, False, "LRspeed", "Left-rear speed", "m/s"),
    (4, 40, 1, False, "RRspeed", "Right-rear speed", "m/s"),
    (
        4,
        44,
        3,
        True,
        "SteeringWheelTorque_ST",
        "Steering torque sub-ticks",
        "N*m",
    ),
    (4, 56, 1, False, "LFshockVel", "LF shock velocity", "m/s"),
    (4, 60, 1, False, "RFshockVel", "RF shock velocity", "m/s"),
    (4, 64, 1, False, "Throttle", "Throttle input", "%"),
]


def build_event_ibt(
    path: Path,
    *,
    minimal: bool = False,
    second_torque_peak: bool = False,
    severity_scenario: bool = False,
) -> None:
    variables = FULL_VARIABLES[:1] if minimal else FULL_VARIABLES
    fixed_size = HEADER.size + DISK_SUBHEADER.size
    variable_offset = _align(fixed_size)
    sample_offset = _align(variable_offset + len(variables) * VAR_HEADER.size)
    output = bytearray(sample_offset + RECORD_COUNT * BUFFER_LENGTH)
    header_values = [
        2,
        1,
        TICK_RATE,
        0,
        0,
        fixed_size,
        len(variables),
        variable_offset,
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
            variable_offset + index * VAR_HEADER.size,
            type_code,
            offset,
            count,
            int(count_as_time),
            _fixed(name, 32),
            _fixed(description, 64),
            _fixed(unit, 32),
        )

    for record in range(RECORD_COUNT):
        base = sample_offset + record * BUFFER_LENGTH
        struct.pack_into("<d", output, base, record / TICK_RATE)
        if minimal:
            continue
        lap = record // RECORDS_PER_LAP + 1
        within_lap = record % RECORDS_PER_LAP
        lap_fraction = within_lap / RECORDS_PER_LAP
        brake = 0.4 if record in (4, 5) else 0.0
        pit = record in (18, 19)
        vehicle_speed = 40.0
        wheel_speeds = [vehicle_speed] * 4
        if record == 38:
            wheel_speeds[1] = vehicle_speed * 0.82
        if severity_scenario and record == 40:
            torque = [0.1, 30.0, 0.1]
        elif severity_scenario and record == 24:
            torque = [0.1, 15.0, 0.1]
        elif record == 8:
            torque = [0.1, 10.0, 0.1]
        elif record == 9 and second_torque_peak:
            torque = [0.1, 12.0, 0.1]
        else:
            torque = [0.1, 0.2, 0.1]
        lf_shock = 1.25 if record == 10 else 0.02
        rf_shock = -1.1 if record == 14 else -0.01
        struct.pack_into("<i", output, base + 8, lap)
        struct.pack_into("<f", output, base + 12, lap_fraction)
        struct.pack_into("<f", output, base + 16, brake)
        struct.pack_into("<?", output, base + 20, pit)
        struct.pack_into("<f", output, base + 24, vehicle_speed)
        struct.pack_into("<4f", output, base + 28, *wheel_speeds)
        struct.pack_into("<3f", output, base + 44, *torque)
        struct.pack_into("<f", output, base + 56, lf_shock)
        struct.pack_into("<f", output, base + 60, rf_shock)
        struct.pack_into("<f", output, base + 64, 0.5)
    path.write_bytes(output)


class NativeEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "events.ibt"
        build_event_ibt(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_detects_and_debounces_native_events_read_only(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()

        result = native_events.detect_native_telemetry_events(self.path)

        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertTrue(result["source"]["read_only"])
        self.assertTrue(result["source"]["structurally_finalized"])
        self.assertEqual(result["summary"]["scanned_record_count"], RECORD_COUNT)
        by_type: dict[str, list[dict]] = {}
        for event in result["events"]:
            by_type.setdefault(event["event_type"], []).append(event)
            self.assertIsInstance(event["source_record_index"], int)
            self.assertIsNotNone(event["session_time_s"])
            self.assertIsNotNone(event["lap"])
            self.assertIsNotNone(event["lap_distance_fraction"])

        self.assertEqual(
            [event["source_record_index"] for event in by_type["brake_onset"]],
            [4],
        )
        self.assertEqual(
            [event["source_record_index"] for event in by_type["brake_release"]],
            [6],
        )
        self.assertEqual(
            [event["measurements"]["direction"] for event in by_type["pit_transition"]],
            ["entry", "exit"],
        )
        self.assertEqual(
            [event["source_record_index"] for event in by_type["pit_transition"]],
            [18, 20],
        )
        self.assertEqual(len(by_type["steering_torque_peak"]), 1)
        self.assertEqual(len(by_type["shock_velocity_peak"]), 2)
        self.assertEqual(len(by_type["wheel_speed_divergence"]), 1)
        self.assertEqual(
            by_type["wheel_speed_divergence"][0]["source_record_index"], 38
        )

        # The complete payload, including missing/non-finite handling, must be
        # directly safe for MCP/CLI JSON serialization.
        json.dumps(result, allow_nan=False)

    def test_array_sub_ticks_report_effective_rate_and_exact_host_record(self) -> None:
        result = native_events.detect_native_telemetry_events(
            self.path,
            event_types="steering_torque_peak",
        )

        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["source_record_index"], 8)
        self.assertEqual(event["evidence"]["label"], "derived")
        self.assertEqual(event["sub_tick"]["index"], 1)
        self.assertEqual(event["sub_tick"]["count"], 3)
        self.assertEqual(event["sub_tick"]["effective_sample_rate_hz"], 30.0)
        self.assertAlmostEqual(event["sub_tick"]["offset_from_record_s"], 1 / 30)
        self.assertAlmostEqual(
            event["sub_tick"]["derived_session_time_s"],
            event["session_time_s"] + 1 / 30,
        )

    def test_nearby_peak_segments_are_debounced_to_strongest_event(self) -> None:
        nearby = Path(self.temporary.name) / "nearby.ibt"
        build_event_ibt(nearby, second_torque_peak=True)

        result = native_events.detect_native_telemetry_events(
            nearby,
            event_types="steering_torque_peak",
        )

        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["source_record_index"], 9)
        self.assertAlmostEqual(result["events"][0]["measurements"]["value"], 12.0)

    def test_record_bounds_are_inclusive_exclusive(self) -> None:
        result = native_events.detect_native_telemetry_events(
            self.path,
            event_types="pit_transition",
            start_record=15,
            end_record=22,
        )

        self.assertEqual(result["summary"]["scanned_record_count"], 7)
        self.assertEqual(
            [event["source_record_index"] for event in result["events"]],
            [18, 20],
        )
        self.assertTrue(
            all(15 <= event["source_record_index"] < 22 for event in result["events"])
        )

    def test_context_filters_return_only_requested_lap_time_and_distance(self) -> None:
        result = native_events.detect_native_telemetry_events(
            self.path,
            event_types=("brake_onset", "brake_release", "pit_transition"),
            lap=1,
            session_time_start=0.3,
            session_time_end=0.8,
            lap_distance_start=0.25,
            lap_distance_end=0.7,
        )

        self.assertEqual(
            [event["event_type"] for event in result["events"]],
            ["brake_onset", "brake_release"],
        )
        self.assertTrue(all(event["lap"] == 1 for event in result["events"]))
        self.assertEqual(result["query"]["context_filters"]["lap"], 1)

    def test_global_cap_stops_and_marks_truncation(self) -> None:
        result = native_events.detect_native_telemetry_events(
            self.path,
            max_events=2,
        )

        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(result["summary"]["truncated"])
        self.assertLess(result["summary"]["scanned_record_count"], RECORD_COUNT)
        with self.assertRaises(ValueError):
            native_events.detect_native_telemetry_events(self.path, max_events=1_001)

    def test_severity_mode_scans_full_window_and_balances_event_types(self) -> None:
        severity_path = Path(self.temporary.name) / "severity.ibt"
        build_event_ibt(severity_path, severity_scenario=True)

        result = native_events.detect_native_telemetry_events(
            severity_path,
            event_types=("brake_onset", "steering_torque_peak"),
            selection_mode="severity",
            max_events=2,
        )
        repeated = native_events.detect_native_telemetry_events(
            severity_path,
            event_types=("brake_onset", "steering_torque_peak"),
            selection_mode="severity",
            max_events=2,
        )

        self.assertEqual(result["events"], repeated["events"])
        self.assertEqual(result["summary"]["scanned_record_count"], RECORD_COUNT)
        self.assertTrue(result["summary"]["scan_complete"])
        self.assertTrue(result["summary"]["candidate_event_count_complete"])
        self.assertTrue(result["summary"]["truncated"])
        self.assertGreater(result["summary"]["omitted_event_count"], 0)
        self.assertEqual(
            {event["event_type"] for event in result["events"]},
            {"brake_onset", "steering_torque_peak"},
        )
        torque = next(
            event
            for event in result["events"]
            if event["event_type"] == "steering_torque_peak"
        )
        self.assertEqual(torque["source_record_index"], 40)
        self.assertEqual(result["query"]["selection_mode"], "severity")

    def test_invalid_selection_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection_mode"):
            native_events.detect_native_telemetry_events(
                self.path, selection_mode="latest_only"
            )

    def test_missing_channels_disable_only_affected_detectors(self) -> None:
        minimal = Path(self.temporary.name) / "minimal.ibt"
        build_event_ibt(minimal, minimal=True)

        result = native_events.detect_native_telemetry_events(minimal)

        self.assertEqual(result["events"], [])
        self.assertEqual(result["summary"]["scanned_record_count"], RECORD_COUNT)
        for event_type in native_events.SUPPORTED_EVENT_TYPES:
            self.assertEqual(result["coverage"][event_type]["status"], "unavailable")
            self.assertIn("reason", result["coverage"][event_type])

    def test_wheel_divergence_is_calibrated_proxy_not_lock_or_spin_claim(self) -> None:
        result = native_events.detect_native_telemetry_events(
            self.path,
            event_types="wheel_speed_divergence",
        )

        self.assertGreaterEqual(result["summary"]["wheel_baseline_completed_laps"], 3)
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["evidence"]["label"], "proxy")
        self.assertIn("same 1/120-lap-distance bin", event["evidence"]["method"])
        self.assertIn("not causal proof", event["evidence"]["limitation"])
        rf = event["measurements"]["per_wheel"]["RF"]
        self.assertGreaterEqual(rf["baseline_lap_count"], 2)
        self.assertLess(rf["delta"], 0)

    def test_rejects_nonfinalized_extent_and_invalid_event_type(self) -> None:
        with self.path.open("ab") as handle:
            handle.write(b"x")
        with self.assertRaises(native_events.NativeEventError):
            native_events.detect_native_telemetry_events(self.path)
        with self.assertRaises(ValueError):
            native_events.detect_native_telemetry_events(
                Path(self.temporary.name) / "missing.ibt",
                event_types="wheel_lock",
            )

    def test_workflow_selects_hashes_caches_and_flattens_bounded_events(self) -> None:
        old = self.path.stat().st_mtime - 10
        os.utime(self.path, (old, old))
        archive = Path(self.temporary.name) / "archive"

        first = workflow.native_event_search_workflow(
            selector=str(self.path),
            archive_root=archive,
            event_types=("brake_onset", "brake_release"),
            lap=1,
            max_events=10,
        )
        second = workflow.native_event_search_workflow(
            selector=str(self.path),
            archive_root=archive,
            event_types=("brake_onset", "brake_release"),
            lap=1,
            max_events=10,
        )

        self.assertEqual(first["summary"]["returned_event_count"], 2)
        self.assertTrue(all(event["source_sha256"] for event in first["events"]))
        self.assertFalse(first["sources"][0]["cache_hit"])
        self.assertTrue(second["sources"][0]["cache_hit"])
        self.assertEqual(first["events"], second["events"])

    def test_workflow_severity_mode_preserves_full_scan_and_global_cap(self) -> None:
        severity_path = Path(self.temporary.name) / "workflow-severity.ibt"
        build_event_ibt(severity_path, severity_scenario=True)
        old = severity_path.stat().st_mtime - 10
        os.utime(severity_path, (old, old))

        result = workflow.native_event_search_workflow(
            selector=str(severity_path),
            archive_root=Path(self.temporary.name) / "severity-archive",
            event_types=("brake_onset", "steering_torque_peak"),
            selection_mode="severity",
            max_events=2,
        )

        self.assertEqual(result["selection_mode"], "severity")
        self.assertEqual(result["summary"]["returned_event_count"], 2)
        self.assertTrue(result["summary"]["scan_complete"])
        self.assertTrue(result["summary"]["candidate_event_count_complete"])
        self.assertGreater(result["summary"]["omitted_event_count"], 0)
        self.assertEqual(
            {event["event_type"] for event in result["events"]},
            {"brake_onset", "steering_torque_peak"},
        )
        torque = next(
            event
            for event in result["events"]
            if event["event_type"] == "steering_torque_peak"
        )
        self.assertEqual(torque["source_record_index"], 40)


if __name__ == "__main__":
    unittest.main()
