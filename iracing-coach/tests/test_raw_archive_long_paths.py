"""Raw-IBT archive paths beyond MAX_PATH on Windows without LongPathsEnabled.

DESKTOP-SIM runs with LongPathsEnabled=0. Archives created before the short
``telemetry.ibt`` name keep the simulator filename, so under a long Documents
root (OneDrive, a redirected profile, the isolated QA profile) the archived
copy sits at 258-275 characters. At that length Win32 reports the existing copy
as absent, the archive copies it again, and the final rename fails with
WinError 3, which used to abort the whole analysis.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import storage  # noqa: E402
from storage import ArchiveStore, file_sha256  # noqa: E402


WINDOWS = os.name == "nt"
# The exact legacy name and root length from the DESKTOP-SIM isolated profile,
# which produced a 270-character archive path.
LEGACY_NAME = "stockcars2-supra2019-bristol-fullpit-2026-09-20-15-09-09.ibt"
SIM_PROFILE_ROOT_LENGTH = 123


def _io(path: Path | str) -> str:
    """Extended-length spelling used by the tests themselves to read and seed."""

    text = os.path.abspath(str(path))
    return text if not WINDOWS or text.startswith("\\\\?\\") else "\\\\?\\" + text


@contextmanager
def _root_of_length(length: int) -> Iterator[Path]:
    base = Path(tempfile.mkdtemp(prefix="raw-long-"))
    try:
        padding = length - len(str(base)) - 1
        if padding < 1:
            raise unittest.SkipTest("temporary directory is already too long")
        root = base / ("r" * padding)
        root.mkdir()
        assert len(str(root)) == length
        yield root
    finally:
        shutil.rmtree(_io(base), ignore_errors=True)


def _fingerprint(source: Path) -> dict:
    stat = source.stat()
    return {
        "path": str(source),
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "sha256": file_sha256(source),
    }


@unittest.skipUnless(WINDOWS, "MAX_PATH applies to Win32 paths only")
class RawArchiveLongPathTests(unittest.TestCase):
    def test_existing_long_named_copy_beyond_max_path_is_reused_not_recopied(self) -> None:
        with _root_of_length(SIM_PROFILE_ROOT_LENGTH) as root:
            source_dir = Path(tempfile.mkdtemp(prefix="raw-src-"))
            self.addCleanup(shutil.rmtree, source_dir, True)
            source = source_dir / LEGACY_NAME
            source.write_bytes(b"finalized bristol telemetry")
            fingerprint = _fingerprint(source)
            store = ArchiveStore(root)
            archive_dir = store.telemetry_traces_dir / "raw" / fingerprint["sha256"]
            legacy = archive_dir / LEGACY_NAME
            self.assertEqual(len(str(legacy)), 270)
            self.assertLess(len(str(archive_dir / "manifest.json")), 260)

            # An archive written before the short name: the verified copy keeps
            # the simulator filename and the manifest points at it.
            archive_dir.mkdir(parents=True)
            with open(_io(legacy), "wb") as handle:
                handle.write(source.read_bytes())
            before = os.stat(_io(legacy))
            (archive_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sha256": fingerprint["sha256"],
                        "size": fingerprint["size"],
                        "archived_file": LEGACY_NAME,
                        "original_file_name": LEGACY_NAME,
                        "original_path": str(source),
                        "modified_ns": fingerprint["modified_ns"],
                        "verified_at": "2026-09-20T20:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            result = store.archive_raw_telemetry([fingerprint])

            self.assertEqual(result["status"], "verified")
            self.assertTrue(result["durably_copied"])
            item = result["items"][0]
            self.assertEqual(item["archive_path"], str(legacy))
            self.assertNotIn("\\\\?\\", item["archive_path"])
            # Deduplicated: the existing immutable copy was verified in place,
            # not copied again or renamed, and no temporary was left behind.
            after = os.stat(_io(legacy))
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(
                sorted(os.listdir(_io(archive_dir))), sorted([LEGACY_NAME, "manifest.json"])
            )
            self.assertEqual(file_sha256(_io(legacy)), fingerprint["sha256"])
            manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["archived_file"], LEGACY_NAME)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(source.read_bytes(), b"finalized bristol telemetry")

    def test_new_copy_under_a_root_beyond_max_path_is_created_verified_and_deduplicated(self) -> None:
        # raw/<sha256> is 255 characters (past CreateDirectory's 248 limit) and
        # the short telemetry.ibt name still puts the copy at 269 characters.
        with _root_of_length(170) as root:
            source_dir = Path(tempfile.mkdtemp(prefix="raw-src-"))
            self.addCleanup(shutil.rmtree, source_dir, True)
            source = source_dir / "race.ibt"
            source.write_bytes(b"new long-root telemetry")
            fingerprint = _fingerprint(source)
            store = ArchiveStore(root)

            first = store.archive_raw_telemetry([fingerprint])
            second = store.archive_raw_telemetry([fingerprint])

            archived = Path(first["items"][0]["archive_path"])
            self.assertEqual(archived.name, "telemetry.ibt")
            self.assertGreater(len(str(archived.parent)), 248)
            self.assertGreater(len(str(archived)), 260)
            self.assertTrue(first["durably_copied"])
            self.assertTrue(first["items"][0]["new_discovery"])
            self.assertTrue(second["durably_copied"])
            self.assertFalse(second["items"][0]["new_discovery"])
            self.assertEqual(second["items"][0]["archive_path"], str(archived))
            self.assertEqual(file_sha256(_io(archived)), fingerprint["sha256"])
            self.assertEqual(
                sorted(os.listdir(_io(archived.parent))), ["manifest.json", "telemetry.ibt"]
            )
            with open(_io(archived.parent / "manifest.json"), encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["source_discovery_count"], 1)
            self.assertEqual(source.read_bytes(), b"new long-root telemetry")


class RawArchiveFailurePolicyTests(unittest.TestCase):
    """TD-008: a destination failure is recorded and retried, never fatal."""

    def test_destination_failure_is_recorded_completely_and_retried_next_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "race.ibt"
            source.write_bytes(b"recorded telemetry")
            fingerprint = _fingerprint(source)
            store = ArchiveStore(root / "archive")
            archive_dir = store.telemetry_traces_dir / "raw" / fingerprint["sha256"]
            destination = archive_dir / "telemetry.ibt"
            full_disk = OSError(errno.ENOSPC, "No space left on device")

            with mock.patch.object(storage.shutil, "copyfileobj", side_effect=full_disk):
                failed = store.archive_raw_telemetry([fingerprint])

            self.assertEqual(failed["status"], "failed")
            self.assertFalse(failed["durably_copied"])
            self.assertEqual(failed["failed_count"], 1)
            item = failed["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["stage"], "copy")
            self.assertTrue(item["retryable"])
            self.assertEqual(item["archive_path"], str(destination))
            message = item["message"]
            self.assertIn("No space left on device", message)
            self.assertIn("Free space on the drive", message)
            # The complete destination survives, after the cause and action,
            # instead of being cut off behind a temporary path.
            self.assertTrue(message.endswith(str(destination)))
            self.assertNotIn(".copy-", message)
            self.assertEqual(source.read_bytes(), b"recorded telemetry")
            self.assertFalse(destination.exists())
            self.assertEqual(os.listdir(archive_dir), [])

            retried = store.archive_raw_telemetry([fingerprint])
            self.assertEqual(retried["status"], "verified")
            self.assertTrue(retried["durably_copied"])
            self.assertEqual(file_sha256(destination), fingerprint["sha256"])

    def test_prior_verified_copy_survives_a_failed_manifest_update(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "race.ibt"
            source.write_bytes(b"recorded telemetry")
            fingerprint = _fingerprint(source)
            store = ArchiveStore(root / "archive")
            archived = Path(store.archive_raw_telemetry([fingerprint])["items"][0]["archive_path"])
            manifest_path = archived.parent / "manifest.json"
            manifest_before = manifest_path.read_bytes()
            denied = PermissionError(13, "Access is denied")
            denied.winerror = 5

            with mock.patch.object(storage, "_atomic_write_text", side_effect=denied):
                result = store.archive_raw_telemetry([fingerprint])

            item = result["items"][0]
            self.assertEqual(item["stage"], "manifest")
            self.assertEqual(item["winerror"], 5)
            self.assertIn("WinError 5", item["message"])
            self.assertIn("antivirus or cloud sync", item["message"])
            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            self.assertEqual(file_sha256(archived), fingerprint["sha256"])

    def test_integrity_failures_still_stop_with_an_actionable_redacted_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "private-race-name.ibt"
            source.write_bytes(b"recorded telemetry")
            fingerprint = dict(_fingerprint(source), sha256="0" * 64)
            store = ArchiveStore(root / "archive")

            with self.assertRaises(storage.ArchiveVerificationFailed) as raised:
                store.archive_raw_telemetry([fingerprint])

            message = str(raised.exception)
            self.assertEqual(raised.exception.code, "archived-digest-mismatch")
            self.assertIn("archived-digest-mismatch", message)
            self.assertIn("Try again", message)
            self.assertNotIn("private-race-name", message)
            self.assertNotIn("0" * 64, message)


class ExtendedPathSpellingTests(unittest.TestCase):
    def test_extended_spelling_is_absolute_normalized_and_reversible(self) -> None:
        if not WINDOWS:
            # Extended-length paths are a Win32 spelling; elsewhere nothing changes.
            self.assertEqual(storage._extended_path("/tmp/raw/file.ibt"), "/tmp/raw/file.ibt")
            return
        self.assertEqual(
            storage._extended_path(r"C:\Data\Coach\..\Coach\raw/file.ibt"),
            "\\\\?\\C:\\Data\\Coach\\raw\\file.ibt",
        )
        self.assertEqual(
            storage._extended_path(r"\\server\share\raw\file.ibt"),
            "\\\\?\\UNC\\server\\share\\raw\\file.ibt",
        )
        already = "\\\\?\\C:\\Data\\file.ibt"
        self.assertEqual(storage._extended_path(already), already)
        self.assertEqual(storage._ordinary_path(already), "C:\\Data\\file.ibt")
        self.assertEqual(
            storage._ordinary_path("\\\\?\\UNC\\server\\share\\x"), "\\\\server\\share\\x"
        )
        self.assertTrue(storage._extended_path("relative.ibt").startswith("\\\\?\\"))


if __name__ == "__main__":
    unittest.main()
