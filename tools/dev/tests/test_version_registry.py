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
import shutil
import subprocess
import sys
import tempfile
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


class CompanionRangeMutationTests(unittest.TestCase):
    """Drive the real exporter against mutated declarations.

    The previous version of this file asserted that the committed values were
    correct. That is not mutation coverage: it cannot notice that a *different*
    declaration would also have been accepted. Codex demonstrated the gap by
    setting the minimum to 2 while the maximum stayed 1; generation emitted the
    contradiction and every assertion here still passed. These tests run the
    shipping exporter over a mutated copy and require it to refuse.
    """

    def _generate_with(self, mutate) -> subprocess.CompletedProcess:
        """Copy the tree's contracts, mutate the source record, regenerate."""
        with tempfile.TemporaryDirectory(prefix="registry-mutation-") as raw:
            sandbox = Path(raw)
            shutil.copytree(ROOT / "contracts", sandbox / "contracts")
            shutil.copytree(ROOT / "tools", sandbox / "tools",
                            ignore=shutil.ignore_patterns("dev", "__pycache__"))
            shutil.copytree(ROOT / "iracing-coach", sandbox / "iracing-coach",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            # Generation now reads the C# constant, so the source must be present.
            shutil.copytree(ROOT / "companion-app" / "src", sandbox / "companion-app" / "src")
            target = sandbox / "contracts" / "compatibility-sources.json"
            document = json.loads(target.read_text(encoding="utf-8"))
            mutate(document)
            target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-X", "utf8", str(sandbox / "tools" / "export_contracts.py")],
                cwd=sandbox, capture_output=True, text=True, check=False, timeout=120,
            )

    def test_the_unmutated_declaration_still_generates(self) -> None:
        # Control: the harness must be capable of succeeding, or every refusal
        # below would be meaningless.
        self.assertEqual(self._generate_with(lambda d: None).returncode, 0)

    def test_minimum_above_maximum_is_refused(self) -> None:
        """The originally reported attack: minimum 2 while maximum is 1.

        It is now caught by the exact policy-value binding rather than the range
        invariant, because the exact check runs first and is strictly stronger.
        Worth stating plainly: with all three fields pinned - minimum to exactly
        0, writer and maximum to the C# constant - the range invariant is no
        longer independently reachable. It stays as a defensive check against a
        future field gaining a looser binding, not as live coverage, and this
        test asserts refusal rather than a particular message.
        """
        def mutate(document):
            document["companion_durable_archive"]["min_readable_version"]["value"] = 2
        completed = self._generate_with(mutate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("min_readable_version", completed.stderr)

    def test_each_companion_field_mutation_is_refused_or_reflected(self) -> None:
        for field, mutated in (
            ("writer_version", 5),
            ("max_readable_version", 0),
            ("min_readable_version", 3),
        ):
            with self.subTest(field=field):
                def mutate(document, field=field, mutated=mutated):
                    document["companion_durable_archive"][field]["value"] = mutated
                self.assertNotEqual(self._generate_with(mutate).returncode, 0)

    def test_a_negative_floor_is_refused(self) -> None:
        def mutate(document):
            document["companion_durable_archive"]["min_readable_version"]["value"] = -1
        self.assertNotEqual(self._generate_with(mutate).returncode, 0)

    def test_a_boolean_value_is_refused(self) -> None:
        def mutate(document):
            document["companion_durable_archive"]["writer_version"]["value"] = True
        self.assertNotEqual(self._generate_with(mutate).returncode, 0)

    def test_weakened_authority_metadata_is_refused(self) -> None:
        cases = {
            "policy claiming a symbol": lambda d: d["companion_durable_archive"]
            ["min_readable_version"].update({"symbol": "DurableArchiveService.CurrentSchemaVersion"}),
            "symbol field downgraded to policy": lambda d: d["companion_durable_archive"]
            ["writer_version"].update({"authority": "declared-policy"}),
            "symbol field missing its source": lambda d: d["companion_durable_archive"]
            ["max_readable_version"].update({"source": ""}),
            "policy missing enforced_by": lambda d: d["companion_durable_archive"]
            ["min_readable_version"].pop("enforced_by"),
            "policy missing current_behavior": lambda d: d["companion_durable_archive"]
            ["min_readable_version"].pop("current_behavior"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                self.assertNotEqual(self._generate_with(mutate).returncode, 0)

    def test_a_backend_value_substituted_into_the_companion_range_is_refused(self) -> None:
        """Substitution must fail the invoked gate, not merely be described.

        An earlier version of this test asserted the exporter *succeeds* for
        max = 2 and then compared the unmutated repository's backend value to the
        C# constant - a comparison independent of the mutated sandbox, so it did
        not catch the substitution it was named for. Generation now binds the
        value to the constant read from C# source, so the substitution fails here.
        """
        backend_writer = _registry()["backend"]["backend_archive_writer_version"]

        def mutate(document):
            document["companion_durable_archive"]["max_readable_version"]["value"] = backend_writer

        completed = self._generate_with(mutate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CurrentSchemaVersion", completed.stderr)

    def test_an_in_range_but_unauthorized_policy_floor_is_refused(self) -> None:
        # 1 <= 1 <= 1 satisfies the invariant, so only the exact accepted policy
        # value catches it.
        def mutate(document):
            document["companion_durable_archive"]["min_readable_version"]["value"] = 1
        completed = self._generate_with(mutate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("must be exactly 0", completed.stderr)

    def test_wrong_but_non_empty_authority_metadata_is_refused(self) -> None:
        # Checking a symbol string is non-empty accepts `Bogus.CurrentVersion`.
        def mutate(document):
            entry = document["companion_durable_archive"]["writer_version"]
            entry["symbol"] = "Bogus.CurrentVersion"
            entry["source"] = "README.md"
        completed = self._generate_with(mutate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("must be exactly", completed.stderr)

    def test_the_declared_value_is_bound_to_the_csharp_constant(self) -> None:
        for field in ("writer_version", "max_readable_version"):
            with self.subTest(field=field):
                def mutate(document, field=field):
                    document["companion_durable_archive"][field]["value"] = 7
                completed = self._generate_with(mutate)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("CurrentSchemaVersion", completed.stderr)


class GeneratedRegistryMutationTests(unittest.TestCase):
    """Each of the six published range keys must be independently detectable.

    The source-record tests above cover three declared companion fields. These
    mutate the six *generated* keys in the committed registry and require
    `export_contracts.py --check` to refuse each stale artifact, which is what
    the accepted plan promised and what my earlier matrix did not do.
    """

    SIX_KEYS = (
        "backend_archive_writer_version",
        "backend_archive_min_readable_version",
        "backend_archive_max_readable_version",
        "companion_durable_archive_writer_version",
        "companion_durable_archive_min_readable_version",
        "companion_durable_archive_max_readable_version",
    )

    def _check_with_registry(self, mutate) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="registry-generated-") as raw:
            sandbox = Path(raw)
            shutil.copytree(ROOT / "contracts", sandbox / "contracts")
            shutil.copytree(ROOT / "tools", sandbox / "tools",
                            ignore=shutil.ignore_patterns("dev", "__pycache__"))
            shutil.copytree(ROOT / "iracing-coach", sandbox / "iracing-coach",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            shutil.copytree(ROOT / "companion-app" / "src",
                            sandbox / "companion-app" / "src")
            target = sandbox / "contracts" / "compatibility.json"
            document = json.loads(target.read_text(encoding="utf-8"))
            mutate(document)
            target.write_text(json.dumps(document, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(sandbox / "tools" / "export_contracts.py"), "--check"],
                cwd=sandbox, capture_output=True, text=True, check=False, timeout=120,
            )

    def test_the_unmutated_registry_passes_check(self) -> None:
        self.assertEqual(self._check_with_registry(lambda d: None).returncode, 0)

    def test_each_of_the_six_generated_keys_is_independently_detected(self) -> None:
        for key in self.SIX_KEYS:
            with self.subTest(key=key):
                def mutate(document, key=key):
                    document["backend"][key] = document["backend"][key] + 41
                self.assertNotEqual(self._check_with_registry(mutate).returncode, 0)

    def test_cross_store_substitution_in_both_directions_is_detected(self) -> None:
        cases = {
            "backend value into companion key":
                ("companion_durable_archive_writer_version", "backend_archive_writer_version"),
            "companion value into backend key":
                ("backend_archive_writer_version", "companion_durable_archive_writer_version"),
        }
        for label, (target, donor) in cases.items():
            with self.subTest(case=label):
                def mutate(document, target=target, donor=donor):
                    document["backend"][target] = document["backend"][donor]
                self.assertNotEqual(self._check_with_registry(mutate).returncode, 0)


class CSharpConstantParserTests(unittest.TestCase):
    """The parser must fail clearly rather than guess."""

    def _with_source(self, replacement: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="registry-csharp-") as raw:
            sandbox = Path(raw)
            shutil.copytree(ROOT / "contracts", sandbox / "contracts")
            shutil.copytree(ROOT / "tools", sandbox / "tools",
                            ignore=shutil.ignore_patterns("dev", "__pycache__"))
            shutil.copytree(ROOT / "iracing-coach", sandbox / "iracing-coach",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            shutil.copytree(ROOT / "companion-app" / "src", sandbox / "companion-app" / "src")
            target = (sandbox / "companion-app" / "src" / "iRacingCoach.Coordinator"
                      / "DurableArchive.cs")
            source = target.read_text(encoding="utf-8")
            target.write_text(
                source.replace("public const int CurrentSchemaVersion = 1;", replacement),
                encoding="utf-8",
            )
            return subprocess.run(
                [sys.executable, "-X", "utf8", str(sandbox / "tools" / "export_contracts.py")],
                cwd=sandbox, capture_output=True, text=True, check=False, timeout=120,
            )

    def test_a_missing_constant_is_refused(self) -> None:
        completed = self._with_source("// constant removed")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("was not found", completed.stderr)

    def test_a_duplicate_constant_is_refused(self) -> None:
        completed = self._with_source(
            "public const int CurrentSchemaVersion = 1;\n"
            "    public const int CurrentSchemaVersion = 2;"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing to guess", completed.stderr)

    def test_a_non_integer_constant_is_refused(self) -> None:
        for replacement in ('public const string CurrentSchemaVersion = "1";',
                            "public const bool CurrentSchemaVersion = true;"):
            with self.subTest(declaration=replacement):
                completed = self._with_source(replacement)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("was not found", completed.stderr)

    def test_a_changed_constant_is_reflected_and_the_declaration_must_follow(self) -> None:
        # Bumping the C# constant without updating the declaration must fail,
        # which is the drift detection this binding exists to provide.
        completed = self._with_source("public const int CurrentSchemaVersion = 2;")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CurrentSchemaVersion", completed.stderr)

    def test_a_commented_out_declaration_is_not_live_authority(self) -> None:
        """Dead text must not carry authority.

        An unanchored regex over raw C# matched a declaration inside a comment,
        so generation would have bound compatibility values to code that does
        not exist. Commenting the constant out is a *missing* constant.
        """
        for label, replacement in (
            ("line comment", "// public const int CurrentSchemaVersion = 1;"),
            ("block comment", "/*\npublic const int CurrentSchemaVersion = 1;\n*/"),
            ("trailing line comment", "int Unused = 0; // public const int CurrentSchemaVersion = 1;"),
        ):
            with self.subTest(case=label):
                completed = self._with_source(replacement)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("was not found as a live integer constant", completed.stderr)

    def test_a_declaration_inside_a_string_literal_is_not_live_authority(self) -> None:
        for label, replacement in (
            ("regular string", 'const string Doc = "public const int CurrentSchemaVersion = 1;";'),
            ("verbatim string", 'const string Doc = @"public const int CurrentSchemaVersion = 1;";'),
        ):
            with self.subTest(case=label):
                completed = self._with_source(replacement)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("was not found as a live integer constant", completed.stderr)

    def test_a_live_declaration_beside_commented_decoys_still_binds(self) -> None:
        # The scanner must not become so strict that real source stops parsing:
        # one live declaration among commented copies is unambiguous.
        completed = self._with_source(
            "// public const int CurrentSchemaVersion = 9;\n"
            "    public const int CurrentSchemaVersion = 1;\n"
            "    /* public const int CurrentSchemaVersion = 8; */"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_an_unterminated_block_comment_is_refused(self) -> None:
        completed = self._with_source("/* public const int CurrentSchemaVersion = 1;")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Unterminated", completed.stderr)


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
