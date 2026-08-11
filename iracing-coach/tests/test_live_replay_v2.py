import gzip
import hashlib
import json
import math
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"))

from live_replay_v2 import (
    MAX_CHUNK_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    LiveReplayV2Error,
    decode_live_replay_v2,
    read_live_replay_v2_header,
)
from storage import ArchiveStore, _ReplayDisplaySampler


def boolean(value):
    return struct.pack("<B", 1 if value else 0)


def int32(value):
    return struct.pack("<i", value)


def uint32(value):
    return struct.pack("<I", value)


def int64(value):
    return struct.pack("<q", value)


def float32(value):
    return struct.pack("<f", value)


def float64(value):
    return struct.pack("<d", value)


def seven_bit(value):
    result = bytearray()
    while value >= 0x80:
        result.append((value | 0x80) & 0xFF)
        value >>= 7
    result.append(value)
    return bytes(result)


def text(value):
    encoded = value.encode("utf-8")
    return seven_bit(len(encoded)) + encoded


def optional(value, encoder):
    return boolean(value is not None) + (encoder(value) if value is not None else b"")


def player(values, mask):
    field_encoders = (
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        boolean,
        boolean,
        boolean,
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, int32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
    )
    result = bytearray(b"\x01" + uint32(mask))
    for bit, encode in enumerate(field_encoders):
        if mask & (1 << bit):
            result.extend(encode(values[bit]))
    return bytes(result)


def car(car_index, values, mask):
    field_encoders = (
        lambda value: optional(value, float32),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, boolean),
        lambda value: optional(value, int32),
        lambda value: optional(value, int32),
        lambda value: optional(value, float32),
        lambda value: optional(value, float32),
    )
    result = bytearray(int32(car_index) + uint32(mask))
    for bit, encode in enumerate(field_encoders):
        if mask & (1 << bit):
            result.extend(encode(values[bit]))
    return bytes(result)


def fixture():
    common = bytearray()
    common.extend(text("session-a"))
    common.extend(optional(10, int64))
    common.extend(optional(20, int64))
    common.extend(optional(0, int32))
    common.extend(optional("Race", text))
    common.extend(optional(0, int32))
    common.extend(int32(1))
    common.extend(text("CarIdxLapDistPct") + boolean(True) + optional(None, text))
    common.extend(int32(1))
    common.extend(int32(0))
    common.extend(optional("7", text))
    common.extend(optional(1, int32))
    common.extend(optional("Class", text))
    common.extend(optional("Car", text))
    common.extend(optional("Driver", text))
    common.extend(optional(None, text))
    common.extend(optional(False, boolean))

    player_values = (0, 0, 0, 3, False, False, False, 0.0, 0.0, 45.0, 0.8, 0.0, 0.1, 4, 7500.0, 0.02, 1.2, 0.1)
    car_values = (0.1, 1, 0, 1, 1, False, 3, 0, 24.5, 24.2)
    frames = bytearray(int32(2))
    frames.extend(int64(1_786_363_200_000))
    frames.extend(optional(0.0, float64) + optional(4, int32) + optional(4, int64))
    frames.extend(int32(0) + int32(60))
    frames.extend(player(player_values, (1 << 18) - 1))
    frames.extend(int32(0))
    frames.extend(int32(1) + car(0, car_values, (1 << 10) - 1))

    frames.extend(int64(1_786_363_200_017))
    frames.extend(optional(1 / 60, float64) + optional(4, int32) + optional(4, int64))
    frames.extend(int32(1) + int32(60))
    changed_player = list(player_values)
    changed_player[0] = 2
    changed_player[10] = 0.75
    frames.extend(player(changed_player, (1 << 0) | (1 << 10)))
    frames.extend(int32(1))
    frames.extend(text("incident_points") + text("Incident points changed") + text("PlayerCarMyIncidentCount") + optional(2.0, float64))
    changed_car = list(car_values)
    changed_car[0] = 0.101
    frames.extend(int32(1) + car(0, changed_car, 1 << 0))

    raw = bytes(common + frames)
    compressed = gzip.compress(raw)
    return (
        b"IRCRPLY2"
        + int32(2)
        + int32(2)
        + int32(len(raw))
        + int32(len(compressed))
        + int64(1_786_363_200_000)
        + int64(1_786_363_200_017)
        + float64(0.0)
        + float64(1 / 60)
        + compressed
    )


class LiveReplayV2Tests(unittest.TestCase):
    def test_decodes_delta_gzip_chunk_without_inventing_incident_type(self):
        decoded = decode_live_replay_v2(fixture())

        self.assertEqual(decoded["schemaVersion"], 2)
        self.assertEqual(decoded["frameCount"], 2)
        self.assertEqual(decoded["sessionKey"], "session-a")
        self.assertEqual(decoded["participants"][0]["driverName"], "Driver")
        self.assertAlmostEqual(decoded["frames"][1]["cars"][0]["lapDistancePercent"], 0.101, places=5)
        self.assertEqual(decoded["frames"][1]["cars"][0]["overallPosition"], 1)
        self.assertEqual(decoded["frames"][1]["playerTelemetry"]["incidentPoints"], 2)
        self.assertAlmostEqual(decoded["frames"][1]["playerTelemetry"]["throttle"], 0.75, places=5)
        event = decoded["frames"][1]["events"][0]
        self.assertEqual(event["kind"], "incident_points")
        self.assertEqual(event["sourceChannel"], "PlayerCarMyIncidentCount")
        self.assertNotIn("contact", event["label"].lower())

    def test_header_rejects_truncation_and_unknown_schema(self):
        payload = fixture()
        with self.assertRaises(LiveReplayV2Error):
            read_live_replay_v2_header(payload[:-1])
        invalid = payload[:8] + int32(99) + payload[12:]
        with self.assertRaises(LiveReplayV2Error):
            decode_live_replay_v2(invalid)

    def test_decoder_rejects_declared_expansion_and_oversized_file_before_read(self):
        payload = fixture()
        oversized_raw = payload[:16] + int32(MAX_UNCOMPRESSED_BYTES + 1) + payload[20:]
        with self.assertRaises(LiveReplayV2Error):
            decode_live_replay_v2(oversized_raw)

        dishonest_raw = payload[:16] + int32(32) + payload[20:]
        with self.assertRaises(LiveReplayV2Error):
            decode_live_replay_v2(dishonest_raw)

        with tempfile.TemporaryDirectory() as folder:
            sparse = Path(folder) / "oversized.ircr2"
            with sparse.open("wb") as stream:
                stream.seek(MAX_CHUNK_BYTES)
                stream.write(b"\0")
            with self.assertRaises(LiveReplayV2Error):
                decode_live_replay_v2(sparse)

    def test_hour_scale_60hz_64_car_display_materialization_is_bounded_and_seekable(self):
        started = time.perf_counter()
        source_frames = 60 * 60 * 60
        car_rows = [
            [index, index / 64, 1, 0, index + 1, index + 1, False, 3, 0, 24.5, 24.2]
            for index in range(64)
        ]
        sampler = _ReplayDisplaySampler(3_600)
        exact_times = set()
        for index in range(source_frames):
            session_time = index / 60
            keyframe = index in (0, source_frames - 1) or index % 18_000 == 0
            events = []
            if keyframe and index not in (0, source_frames - 1):
                events = [{"kind": "session_flags", "label": "Flag changed", "sourceChannel": "SessionFlags"}]
                exact_times.add(session_time)
            sampler.observe(
                {
                    "session_time_s": session_time,
                    "session_state": "racing",
                    "global_flags": 4,
                    "global_flag_labels": ["green"],
                    "car_rows": car_rows,
                    "player_telemetry": {
                        "incidentPoints": 0,
                        "onPitRoad": False,
                        "towing": False,
                        "repairRequired": False,
                        "speedMetersPerSecond": 45,
                        "throttle": 0.8,
                        "brake": 0.05,
                        "steeringWheelAngleRadians": 0.1,
                        "gear": 4,
                        "rpm": 7_500,
                    },
                    "events": events,
                },
                keyframe=keyframe,
            )
        selected = sampler.finish()

        self.assertLessEqual(len(selected), 10_000)
        self.assertLessEqual(len(selected) * 64, 10_000 * 64)
        selected_times = [frame["session_time_s"] for frame in selected]
        self.assertEqual(selected_times, sorted(selected_times))
        self.assertTrue(exact_times.issubset(set(selected_times)))
        self.assertEqual(selected_times[0], 0)
        self.assertAlmostEqual(selected_times[-1], 3_600 - 1 / 60, places=6)
        # Binary seek over the bounded representation is deterministic.
        import bisect
        for target in (0, 1, 1_799.9, 3_599.9):
            index = max(0, bisect.bisect_right(selected_times, target) - 1)
            self.assertLessEqual(selected_times[index], target + 1e-9)
        serialized_bytes = 2 + sum(
            len(json.dumps(frame, separators=(",", ":")).encode("utf-8")) + 1
            for frame in selected
        )
        elapsed = time.perf_counter() - started
        self.assertLess(serialized_bytes, 150 * 1024 * 1024)
        self.assertLess(elapsed, 30)
        print(
            f"hour replay: {source_frames:,} source -> {len(selected):,} display frames, "
            f"{len(selected) * 64:,} car rows, {serialized_bytes / 1024 / 1024:.1f} MiB JSON, {elapsed:.2f}s"
        )

    def test_event_pressure_stays_hard_bounded_and_keeps_gap_boundaries(self):
        sampler = _ReplayDisplaySampler(600)
        gap_times = {100.0, 100.1}
        for index in range(10_250):
            session_time = index / 10
            reason = "gap" if session_time in gap_times else "event"
            sampler.observe(
                {
                    "session_time_s": session_time,
                    "session_state": "racing",
                    "global_flags": 4,
                    "global_flag_labels": ["green"],
                    "cars": [{"car_index": 0, "lap_pct": 0.2}],
                    "events": [{"kind": "test", "label": f"event-{index}", "sourceChannel": "test"}],
                },
                keyframe=True,
                keyframe_reason=reason,
            )
            self.assertLessEqual(len(sampler._keyframes), 10_000)
        selected = sampler.finish()
        selected_times = {frame["session_time_s"] for frame in selected}

        self.assertEqual(len(selected), 10_000)
        self.assertTrue(gap_times.issubset(selected_times))
        self.assertFalse(sampler.keyframes_preserved)
        self.assertEqual(sampler.dropped_keyframe_count, 250)

    def test_display_sampler_orders_prestart_zero_and_positive_session_time(self):
        sampler = _ReplayDisplaySampler(2)
        for session_time in (-0.5, 0.0, 0.5):
            sampler.observe(
                {
                    "session_time_s": session_time,
                    "session_state": "racing",
                    "global_flags": 4,
                    "global_flag_labels": ["green"],
                    "cars": [{"car_index": 0, "lap_pct": 0.2}],
                    "events": [],
                },
                keyframe=True,
                keyframe_reason="boundary",
            )

        self.assertEqual(
            [frame["session_time_s"] for frame in sampler.finish()],
            [-0.5, 0.0, 0.5],
        )

    def test_archive_store_ingests_sha_verified_v2_chunk_and_preserves_strict_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            capture = root / "telemetry-traces" / "live-replay" / "session-a"
            capture.mkdir(parents=True)
            payload = fixture()
            chunk_path = capture / "chunk-000000.ircr2"
            chunk_path.write_bytes(payload)
            manifest = {
                "schemaVersion": 2,
                "status": "finalized",
                "sessionKey": "session-a",
                "subsessionId": 10,
                "sessionNumber": 20,
                "sessionType": "Race",
                "playerCarIndex": 0,
                "sampleRateHz": 60,
                "coverage": [
                    {"channel": channel, "recorded": True}
                    for channel in ("SessionTime", "SessionState", "CarIdxLapDistPct")
                ],
                "participants": [
                    {
                        "carIndex": 0,
                        "carNumber": "7",
                        "classId": 1,
                        "className": "Class",
                        "carName": "Car",
                        "driverName": "Driver",
                    }
                ],
                "chunks": [
                    {
                        "file": chunk_path.name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "frameCount": 2,
                    }
                ],
            }
            (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            replay = ArchiveStore(root).live_replay_for_analysis(
                {
                    "identity": {"subsession_id": 10},
                    "source": {
                        "selection": {
                            "subsession_id": "10",
                            "sim_session_num": "20",
                            "sim_session_type": "Race",
                        }
                    },
                }
            )

            self.assertIsNotNone(replay)
            self.assertEqual(replay["status"], "usable")
            self.assertEqual(replay["frame_count"], 2)
            self.assertEqual(replay["sample_rate_hz"], 60)
            self.assertEqual(replay["player_car_index"], 0)
            self.assertEqual(replay["frames"][1]["player_telemetry"]["incidentPoints"], 2)
            event = replay["frames"][1]["events"][0]
            self.assertEqual(event["kind"], "incident_points")
            self.assertNotIn("contact", event["label"].lower())

    def test_archive_store_preserves_missing_and_sentinel_lap_positions_as_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            capture = root / "telemetry-traces" / "live-replay" / "session-null"
            capture.mkdir(parents=True)
            frames = []
            for index, lap_pct in enumerate((0.2, None, -1, 0.4)):
                frames.append(
                    {
                        "capturedAt": f"2026-08-10T12:00:0{index}Z",
                        "sessionTimeSeconds": index * 0.5,
                        "sessionState": 4,
                        "sessionFlags": 4,
                        "cars": [{"carIndex": 0, "lapDistancePercent": lap_pct, "lap": 1}],
                    }
                )
            payload = json.dumps({"frames": frames}).encode("utf-8")
            chunk_path = capture / "chunk-000000.json"
            chunk_path.write_bytes(payload)
            manifest = {
                "schemaVersion": 1,
                "status": "finalized",
                "subsessionId": 10,
                "sessionNumber": 20,
                "sessionType": "Race",
                "playerCarIndex": 0,
                "sampleRateHz": 2,
                "coverage": [
                    {"channel": channel, "recorded": True}
                    for channel in ("SessionTime", "SessionState", "CarIdxLapDistPct")
                ],
                "participants": [{"carIndex": 0, "driverName": "Player"}],
                "chunks": [{"file": chunk_path.name, "sha256": hashlib.sha256(payload).hexdigest()}],
            }
            (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            replay = ArchiveStore(root).live_replay_for_analysis(
                {"identity": {"subsession_id": 10}, "source": {"selection": {"subsession_id": "10", "sim_session_num": "20", "sim_session_type": "Race"}}}
            )

            self.assertIsNotNone(replay)
            lap_percent_index = replay["car_columns"].index("lap_pct")
            positions = [frame["car_rows"][0][lap_percent_index] for frame in replay["frames"]]
            self.assertEqual(positions, [0.2, None, None, 0.4])
            self.assertNotIn(0, positions)


if __name__ == "__main__":
    unittest.main()
