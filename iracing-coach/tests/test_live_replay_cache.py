"""Finalized replay caching preserves evidence and verifies current sources."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/analyze-iracing-race/scripts"))
from storage import ArchiveStore
import storage
from test_live_replay_v2 import fixture


class FinalizedReplayCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ArchiveStore(self.root)
        self.capture = self.store.telemetry_traces_dir / "live-replay" / "race"
        self.capture.mkdir(parents=True)
        self.chunk = self.capture / "chunk.ircr2"
        self.chunk.write_bytes(fixture())
        self.manifest_path = self.capture / "manifest.json"
        self.manifest = {
            "status": "finalized", "subsessionId": 10, "sessionNumber": 20,
            "sessionType": "Race", "playerCarIndex": 0, "sampleRateHz": 60,
            "coverage": [{"channel": name, "recorded": True} for name in
                         ("SessionTime", "SessionState", "CarIdxLapDistPct")],
            "participants": [{"carIndex": 0, "driverName": "Original"}],
            "chunks": [{"file": self.chunk.name, "sha256": hashlib.sha256(self.chunk.read_bytes()).hexdigest()}],
        }
        self.write_manifest()
        self.analysis = {"identity": {"subsession_id": 10}, "source": {"selection": {
            "subsession_id": "10", "sim_session_num": "20", "sim_session_type": "Race"}}}

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def replay(self):
        return self.store.live_replay_for_analysis(self.analysis)

    def test_repeated_finalized_capture_reuses_exact_answer_without_decoding(self):
        with mock.patch("storage.decode_live_replay_v2", wraps=storage.decode_live_replay_v2) as decode:
            original = self.replay()
            self.assertEqual(original, self.replay())
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(original["frame_count"], 2)
        self.assertTrue(original["frames"][1]["events"])

    def test_same_size_and_mtime_source_tampering_cannot_reuse_cached_answer(self):
        self.assertEqual(self.replay()["frame_count"], 2)
        original_stat = self.chunk.stat()
        payload = bytearray(self.chunk.read_bytes())
        payload[0] ^= 1
        self.chunk.write_bytes(payload)
        os.utime(self.chunk, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        changed = self.replay()
        self.assertEqual(changed["status"], "unavailable")
        self.assertEqual(changed["frame_count"], 0)

    def test_missing_chunk_cannot_reuse_cached_answer(self):
        self.replay()
        self.chunk.unlink()
        self.assertEqual(self.replay()["frame_count"], 0)

    def test_changed_manifest_invalidates_participant_metadata_and_session_match(self):
        self.replay()
        self.manifest["participants"][0]["driverName"] = "Updated"
        self.write_manifest()
        self.assertEqual(self.replay()["participants"][0]["driver_name"], "Updated")
        self.manifest["sessionType"] = "Qualifying"
        self.write_manifest()
        self.assertIsNone(self.replay())

    def test_added_capture_chunk_invalidates_cached_coverage(self):
        self.replay()
        second = self.capture / "second.ircr2"
        second.write_bytes(fixture())
        self.manifest["chunks"].append({"file": second.name, "sha256": hashlib.sha256(second.read_bytes()).hexdigest()})
        self.write_manifest()
        with mock.patch("storage.decode_live_replay_v2", wraps=storage.decode_live_replay_v2) as decode:
            self.replay()
            self.assertEqual(decode.call_count, 2)

    def test_damaged_cache_is_rebuilt_instead_of_returning_changed_frames(self):
        original = self.replay()
        cache = next((self.root / "replay-cache").glob("*.json"))
        for damage in ("truncated", "changed-frame"):
            with self.subTest(damage=damage):
                cached = json.loads(cache.read_text(encoding="utf-8"))
                cached["replay"]["frames"][0]["global_flags"] = 123456
                cache.write_text("{" if damage == "truncated" else json.dumps(cached), encoding="utf-8")
                with mock.patch("storage.decode_live_replay_v2", wraps=storage.decode_live_replay_v2) as decode:
                    self.assertEqual(original, self.replay())
                    self.assertEqual(decode.call_count, 1)

    def test_growing_capture_is_not_cached(self):
        self.manifest["status"] = "recording"
        self.write_manifest()
        with mock.patch("storage.decode_live_replay_v2", wraps=storage.decode_live_replay_v2) as decode:
            self.assertEqual(self.replay(), self.replay())
            self.assertEqual(decode.call_count, 2)
        self.assertFalse((self.root / "replay-cache").exists())

    def test_cache_write_failure_does_not_prevent_analysis(self):
        with mock.patch("storage._atomic_write_text", side_effect=OSError("cache unavailable")):
            self.assertEqual(self.replay()["frame_count"], 2)

    def test_decoder_revision_invalidates_cached_materialization(self):
        self.replay()
        original_hash = storage.file_sha256
        def revised_hash(path):
            return "d" * 64 if Path(path).name == "live_replay_v2.py" else original_hash(path)
        with mock.patch("storage.file_sha256", side_effect=revised_hash), mock.patch(
            "storage.decode_live_replay_v2", wraps=storage.decode_live_replay_v2
        ) as decode:
            self.assertEqual(self.replay()["frame_count"], 2)
            self.assertEqual(decode.call_count, 1)

    def test_cached_legacy_capture_preserves_gaps_and_missing_positions(self):
        self.chunk = self.capture / "legacy.json"
        self.chunk.write_text(json.dumps({"frames": [
            {"sessionTimeSeconds": time, "sessionState": 4, "sessionFlags": 4,
             "cars": [{"carIndex": 0, "lapDistancePercent": pct, "lap": 1}]}
            for time, pct in ((0, 0.2), (0.5, None), (3, 0.4))
        ]}), encoding="utf-8")
        self.manifest["sampleRateHz"] = 2
        self.manifest["chunks"] = [{"file": self.chunk.name, "sha256": hashlib.sha256(self.chunk.read_bytes()).hexdigest()}]
        self.write_manifest()
        original = self.replay()
        self.assertEqual(original, self.replay())
        self.assertTrue(original["frames"][-1]["gap_before"])
        pct_index = original["car_columns"].index("lap_pct")
        self.assertIsNone(original["frames"][1]["car_rows"][0][pct_index])


if __name__ == "__main__":
    unittest.main()
