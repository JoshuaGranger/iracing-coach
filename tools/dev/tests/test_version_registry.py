"""The version registry must name which store each number describes.

`compatibility.json` previously published one `archive_schema_version` for two
independent stores: the Python backend cache manifest and the C# durable
archive in the user's Documents folder. A reader could not tell which store the
number meant, the companion store's version was unregistered, and a test
asserting "the archive schema is 2" said nothing true about the store a user
would recognise.

These tests hold the split: two independently named ranges, the companion range
bound to its stated authority, and a legacy alias that cannot quietly come to
mean the other store.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "contracts"
COMPATIBILITY = CONTRACTS / "compatibility.json"
SOURCES = CONTRACTS / "compatibility-sources.json"
DURABLE_ARCHIVE_CS = (
    ROOT / "companion-app" / "src" / "iRacingCoach.Coordinator" / "DurableArchive.cs"
)
EXPORTER = ROOT / "tools" / "export_contracts.py"


def _registry() -> dict:
    return json.loads(COMPATIBILITY.read_text(encoding="utf-8"))


def _sources() -> dict:
    return json.loads(SOURCES.read_text(encoding="utf-8"))


class ArchiveStoreSplitTests(unittest.TestCase):
    def test_the_two_stores_have_independent_named_ranges(self) -> None:
        backend = _registry()["backend"]
        for store in ("backend_archive", "companion_durable_archive"):
            for field in ("writer_version", "min_readable_version", "max_readable_version"):
                with self.subTest(key=f"{store}_{field}"):
                    self.assertIn(f"{store}_{field}", backend)

    def test_the_two_stores_do_not_share_a_value_set(self) -> None:
        # Distinct numbers are what make a cross-store substitution detectable
        # on the value as well as the name.
        backend = _registry()["backend"]
        self.assertNotEqual(
            backend["backend_archive_writer_version"],
            backend["companion_durable_archive_writer_version"],
            "if the two stores ever share a writer version, substitution tests weaken",
        )

    def test_backend_range_is_exact_because_storage_requires_equality(self) -> None:
        backend = _registry()["backend"]
        writer = backend["backend_archive_writer_version"]
        self.assertEqual(backend["backend_archive_min_readable_version"], writer)
        self.assertEqual(backend["backend_archive_max_readable_version"], writer)

    def test_companion_writer_and_maximum_are_bound_to_the_csharp_symbol(self) -> None:
        source = DURABLE_ARCHIVE_CS.read_text(encoding="utf-8")
        match = re.search(r"public\s+const\s+int\s+CurrentSchemaVersion\s*=\s*(\d+)\s*;", source)
        self.assertIsNotNone(match, "CurrentSchemaVersion must remain a readable constant")
        assert match is not None
        current = int(match.group(1))
        backend = _registry()["backend"]
        self.assertEqual(backend["companion_durable_archive_writer_version"], current)
        self.assertEqual(backend["companion_durable_archive_max_readable_version"], current)

    def test_companion_minimum_is_declared_policy_and_says_so(self) -> None:
        # The registry may publish a target floor, but the record must not imply
        # the current code enforces it, because it does not.
        record = _sources()["companion_durable_archive"]["min_readable_version"]
        self.assertEqual(record["authority"], "declared-policy")
        self.assertIsNone(record["symbol"])
        self.assertEqual(record["enforced_by"], "codex-consumer-phase")
        self.assertIn("NOT enforced today", record["current_behavior"])

    def test_current_csharp_does_not_enforce_a_lower_bound(self) -> None:
        """Characterization, not endorsement.

        The upper bound is guarded against CurrentSchemaVersion, but the lower
        branch passes every smaller integer to Migrate. This records today's
        behavior so the declared floor is never mistaken for an enforced one.
        """
        source = DURABLE_ARCHIVE_CS.read_text(encoding="utf-8")
        self.assertNotIn("MinimumReadableSchemaVersion", source)
        self.assertRegex(source, r"manifest\.SchemaVersion\s*>\s*CurrentSchemaVersion")
        self.assertRegex(source, r"manifest\.SchemaVersion\s*<\s*CurrentSchemaVersion")

    def test_each_companion_field_declares_its_authority(self) -> None:
        record = _sources()["companion_durable_archive"]
        for field in ("writer_version", "min_readable_version", "max_readable_version"):
            with self.subTest(field=field):
                entry = record[field]
                self.assertIn(entry["authority"], {"csharp-symbol", "declared-policy"})
                if entry["authority"] == "csharp-symbol":
                    self.assertTrue(entry["symbol"])
                    self.assertTrue(entry["source"])


class LegacyAliasTests(unittest.TestCase):
    def test_the_alias_names_its_target_and_meaning(self) -> None:
        alias = _registry()["legacy_aliases"]["archive_schema_version"]
        self.assertEqual(alias["targets"], "backend_archive_writer_version")
        self.assertIn("not the companion", alias["means"])
        for field in ("window", "removal_boundary"):
            self.assertTrue(alias[field], f"alias must declare {field}")

    def test_the_alias_agrees_with_its_target(self) -> None:
        registry = _registry()
        alias = registry["legacy_aliases"]["archive_schema_version"]
        self.assertEqual(
            registry["backend"]["archive_schema_version"],
            registry["backend"][alias["targets"]],
        )

    def test_the_alias_may_not_target_the_companion_store(self) -> None:
        alias = _registry()["legacy_aliases"]["archive_schema_version"]
        self.assertNotIn("companion", alias["targets"])

    def test_every_alias_target_exists(self) -> None:
        registry = _registry()
        for name, alias in registry["legacy_aliases"].items():
            with self.subTest(alias=name):
                self.assertIn(alias["targets"], registry["backend"])


class GenerationTests(unittest.TestCase):
    def test_regeneration_is_byte_identical_and_current(self) -> None:
        before = COMPATIBILITY.read_bytes()
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(EXPORTER), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(COMPATIBILITY.read_bytes(), before)

    def test_the_envelope_version_comes_from_the_producer(self) -> None:
        exporter = EXPORTER.read_text(encoding="utf-8")
        self.assertIn("workflow.ANALYSIS_VIEW_SCHEMA_VERSION", exporter)
        self.assertNotIn('"analysis_view_envelope_version": 1', exporter)


if __name__ == "__main__":
    unittest.main()
