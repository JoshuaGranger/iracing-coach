"""Portability matrix for backend root resolution (IDENTITY-PATH-001).

The closure clause for this item names four conditions - usernames, redirected
Documents, missing environment, and non-default iRacing roots - plus the
absence of any personal literal. Each has a case here. The tests inject the
environment and configuration mappings rather than mutating the real process
environment, so a failure names the rule that broke instead of leaving a
machine-shaped residue behind.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY_ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backend_roots  # noqa: E402


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path))


class UserProfileResolutionTests(unittest.TestCase):
    def test_a_redirected_profile_is_followed(self) -> None:
        environ = {"USERPROFILE": r"D:\redirected\someone-else"}
        self.assertEqual(
            _normcase(backend_roots.user_profile_root(environ)),
            _normcase(Path(r"D:\redirected\someone-else")),
        )

    def test_two_different_accounts_never_share_a_default(self) -> None:
        first = backend_roots.resolve_iracing_root(environ={"USERPROFILE": r"C:\Users\alice"})
        second = backend_roots.resolve_iracing_root(environ={"USERPROFILE": r"C:\Users\bob"})
        self.assertNotEqual(_normcase(first.path), _normcase(second.path))
        self.assertTrue(first.is_default)
        self.assertTrue(second.is_default)

    def test_a_missing_profile_variable_falls_back_to_the_home_directory(self) -> None:
        # An empty environment must still resolve. It must not raise, and it
        # must not produce a path belonging to whoever built the repository.
        resolved = backend_roots.resolve_iracing_root(environ={})
        self.assertTrue(resolved.is_default)
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path.home().joinpath(*backend_roots.IRACING_ROOT_RELATIVE).resolve()),
        )

    def test_a_whitespace_only_profile_is_treated_as_absent(self) -> None:
        resolved = backend_roots.resolve_iracing_root(environ={"USERPROFILE": "   "})
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path.home().joinpath(*backend_roots.IRACING_ROOT_RELATIVE).resolve()),
        )


class PrecedenceTests(unittest.TestCase):
    def test_an_explicit_argument_outranks_every_other_source(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            r"E:\chosen",
            environ={"USERPROFILE": r"C:\Users\alice", "IRACING_COACH_IRACING_ROOT": r"F:\env"},
            defaults={"iracing_root": r"G:\configured"},
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_ARGUMENT)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"E:\chosen")))

    def test_the_environment_outranks_configuration_and_the_default(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice", "IRACING_COACH_IRACING_ROOT": r"F:\env"},
            defaults={"iracing_root": r"G:\configured"},
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_ENVIRONMENT)
        self.assertEqual(resolved.variable, backend_roots.IRACING_ROOT_VARIABLE)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"F:\env")))

    def test_an_empty_environment_override_is_absent_rather_than_an_error(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice", "IRACING_COACH_IRACING_ROOT": ""},
        )
        self.assertTrue(resolved.is_default)

    def test_the_archive_root_uses_its_own_variable(self) -> None:
        resolved = backend_roots.resolve_archive_root(
            environ={"USERPROFILE": r"C:\Users\alice", "IRACING_COACH_DATA": r"F:\data"},
        )
        self.assertEqual(resolved.variable, backend_roots.ARCHIVE_ROOT_VARIABLE)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"F:\data")))

    def test_the_iracing_variable_does_not_move_the_archive_root(self) -> None:
        # The two roots are independent. A single shared variable would let a
        # simulator relocation silently move race history.
        resolved = backend_roots.resolve_archive_root(
            environ={"USERPROFILE": r"C:\Users\alice", "IRACING_COACH_IRACING_ROOT": r"F:\env"},
        )
        self.assertTrue(resolved.is_default)
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path(r"C:\Users\alice").joinpath(*backend_roots.ARCHIVE_ROOT_RELATIVE)),
        )


class GeneratedDefaultTests(unittest.TestCase):
    """The rule that distinguishes a stale literal from a real choice."""

    def test_another_accounts_generated_default_is_not_honored(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"iracing_root": r"C:\Users\someone-else\Documents\iRacing"},
        )
        self.assertTrue(resolved.is_default)
        self.assertIsNotNone(resolved.ignored_configuration)
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path(r"C:\Users\alice").joinpath(*backend_roots.IRACING_ROOT_RELATIVE)),
        )

    def test_the_running_accounts_generated_default_resolves_to_the_same_place(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"iracing_root": r"C:\Users\alice\Documents\iRacing"},
        )
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path(r"C:\Users\alice").joinpath(*backend_roots.IRACING_ROOT_RELATIVE)),
        )

    def test_a_generated_archive_default_is_recognised_by_its_longer_tail(self) -> None:
        resolved = backend_roots.resolve_archive_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"archive_root": r"C:\Users\someone-else\Documents\iRacing Coach\data"},
        )
        self.assertTrue(resolved.is_default)
        self.assertIsNotNone(resolved.ignored_configuration)

    def test_a_deliberate_non_default_root_is_preserved_exactly(self) -> None:
        # The whole point of the narrow shape rule. This user moved the
        # simulator to another volume and that choice must survive.
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"iracing_root": r"D:\sim\iRacing"},
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_CONFIGURATION)
        self.assertIsNone(resolved.ignored_configuration)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"D:\sim\iRacing")))

    def test_a_matching_tail_without_a_profile_head_is_still_a_real_choice(self) -> None:
        # `D:\Backup\Documents\iRacing` ends with the generated tail but has no
        # `Users\<name>` head, so it is a deliberate location, not a literal
        # left over from another machine.
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"iracing_root": r"D:\Backup\Documents\iRacing"},
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_CONFIGURATION)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"D:\Backup\Documents\iRacing")))

    def test_a_generated_default_on_a_different_drive_is_still_recognised(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={"USERPROFILE": r"C:\Users\alice"},
            defaults={"iracing_root": r"E:\Users\someone-else\Documents\iRacing"},
        )
        self.assertTrue(resolved.is_default)

    def test_an_explicit_argument_is_never_discarded_as_a_stale_literal(self) -> None:
        # The stale rule applies only to shipped configuration. A caller that
        # names a path means it, even if the path has the generated shape.
        resolved = backend_roots.resolve_iracing_root(
            r"C:\Users\someone-else\Documents\iRacing",
            environ={"USERPROFILE": r"C:\Users\alice"},
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_ARGUMENT)
        self.assertEqual(
            _normcase(resolved.path),
            _normcase(Path(r"C:\Users\someone-else\Documents\iRacing")),
        )

    def test_an_environment_override_is_never_discarded_as_a_stale_literal(self) -> None:
        resolved = backend_roots.resolve_iracing_root(
            environ={
                "USERPROFILE": r"C:\Users\alice",
                "IRACING_COACH_IRACING_ROOT": r"C:\Users\someone-else\Documents\iRacing",
            },
        )
        self.assertEqual(resolved.source, backend_roots.SOURCE_ENVIRONMENT)


class InstallRootTests(unittest.TestCase):
    def test_an_unknown_installation_location_is_reported_as_unknown(self) -> None:
        # No literal is invented when the machine names no program-files
        # directory. `None` is the truthful answer.
        self.assertIsNone(backend_roots.resolve_install_root(environ={}))

    def test_the_program_files_variable_supplies_the_default(self) -> None:
        resolved = backend_roots.resolve_install_root(
            environ={"PROGRAMFILES(X86)": r"E:\Program Files (x86)"}
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertTrue(resolved.is_default)
        self.assertEqual(
            _normcase(resolved.path), _normcase(Path(r"E:\Program Files (x86)\iRacing"))
        )

    def test_a_generated_install_literal_from_another_machine_is_ignored(self) -> None:
        resolved = backend_roots.resolve_install_root(
            environ={"PROGRAMFILES(X86)": r"E:\Program Files (x86)"},
            defaults={"install_root": r"C:\Program Files (x86)\iRacing"},
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertTrue(resolved.is_default)
        self.assertIsNotNone(resolved.ignored_configuration)
        self.assertEqual(
            _normcase(resolved.path), _normcase(Path(r"E:\Program Files (x86)\iRacing"))
        )

    def test_a_deliberate_install_root_is_preserved(self) -> None:
        resolved = backend_roots.resolve_install_root(
            environ={"PROGRAMFILES(X86)": r"E:\Program Files (x86)"},
            defaults={"install_root": r"D:\Games\iRacing"},
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.source, backend_roots.SOURCE_CONFIGURATION)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"D:\Games\iRacing")))

    def test_the_environment_override_outranks_the_program_files_default(self) -> None:
        resolved = backend_roots.resolve_install_root(
            environ={
                "PROGRAMFILES(X86)": r"E:\Program Files (x86)",
                "IRACING_COACH_INSTALL_ROOT": r"F:\iRacing",
            }
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.source, backend_roots.SOURCE_ENVIRONMENT)
        self.assertEqual(_normcase(resolved.path), _normcase(Path(r"F:\iRacing")))


class RejectionTests(unittest.TestCase):
    def test_a_network_root_is_refused_rather_than_silently_accepted(self) -> None:
        with self.assertRaises(ValueError):
            backend_roots.resolve_archive_root(
                environ={"IRACING_COACH_DATA": r"\\server\share\data"}
            )

    def test_a_device_namespace_root_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            backend_roots.resolve_iracing_root(r"\\?\C:\iRacing")


class NoPersonalLiteralTests(unittest.TestCase):
    """No tracked backend source or configuration may name a real account.

    This is the regression guard for the defect itself. The scan is over
    tracked backend sources and shipped configuration, which is where the
    literals lived; test fixtures deliberately use invented account names such
    as `alice`, and those are the point of the tests above.
    """

    #: `C:\Users\<name>\` in a Windows path, JSON-escaped or not.
    PROFILE_LITERAL = re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}(?!<)([^\\\"'\s]+)")

    #: Account names a fixture is allowed to name, because they do not exist.
    SYNTHETIC_ACCOUNTS = {
        "alice",
        "bob",
        "someone-else",
        "testuser",
        "runneradmin",
        "runner~1",
    }

    def _scanned_files(self) -> list[Path]:
        roots = [
            REPOSITORY_ROOT / "iracing-coach" / "config",
            REPOSITORY_ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts",
        ]
        files: list[Path] = []
        for root in roots:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix.lower() in {".py", ".json", ".ps1", ".md"}:
                    files.append(path)
        return files

    def test_the_scan_actually_reaches_the_files_that_carried_the_defect(self) -> None:
        # A scan over an empty set passes vacuously. Prove it sees both the
        # configuration file and the resolver before trusting its verdict.
        scanned = {path.name for path in self._scanned_files()}
        self.assertIn("defaults.json", scanned)
        self.assertIn("backend_roots.py", scanned)
        self.assertIn("workflow.py", scanned)

    def test_no_backend_source_or_configuration_names_a_real_account(self) -> None:
        offenders: list[str] = []
        for path in self._scanned_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in self.PROFILE_LITERAL.finditer(text):
                account = match.group(1).strip().casefold()
                if account in self.SYNTHETIC_ACCOUNTS:
                    continue
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                offenders.append(f"{relative}: {match.group(0)}")
        self.assertEqual(offenders, [], f"personal path literals found: {offenders}")

    def test_shipped_configuration_declares_no_root_at_all(self) -> None:
        # Roots are resolved, never shipped. A reintroduced key would silently
        # re-establish a machine-specific default for every install.
        payload = json.loads(
            (REPOSITORY_ROOT / "iracing-coach" / "config" / "defaults.json").read_text(
                encoding="utf-8"
            )
        )
        for key in (
            backend_roots.IRACING_ROOT_KEY,
            backend_roots.ARCHIVE_ROOT_KEY,
            backend_roots.INSTALL_ROOT_KEY,
        ):
            self.assertNotIn(key, payload)


class CallSiteTests(unittest.TestCase):
    """The four former copies of the chain now agree because there is one."""

    def test_the_archive_default_routes_through_the_shared_resolver(self) -> None:
        import storage

        environ = dict(os.environ)
        environ.pop("IRACING_COACH_DATA", None)
        expected = backend_roots.resolve_archive_root(environ=environ).path
        previous = os.environ.pop("IRACING_COACH_DATA", None)
        try:
            self.assertEqual(_normcase(storage.default_archive_root()), _normcase(expected))
        finally:
            if previous is not None:
                os.environ["IRACING_COACH_DATA"] = previous

    def test_the_two_workflow_modules_agree_on_the_iracing_root(self) -> None:
        import tuning_workflow
        import workflow

        self.assertEqual(
            _normcase(workflow._default_iracing_root()),
            _normcase(tuning_workflow._default_iracing_root()),
        )


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
