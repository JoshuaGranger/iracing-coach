"""D12: legacy tuning experiments move into a driver's worker archive with rewritten references."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import coach_tool_authority as authority  # noqa: E402
import legacy_import  # noqa: E402
import mcp_server  # noqa: E402
from storage import ArchiveStore  # noqa: E402

CONTEXT = {"season_key": "2026s3", "car_key": "supra", "track_key": "irp", "setup_type": "open"}
OLD_HOME = r"C:\Users\someone-else\Documents\iRacing Coach\data"


def _legacy_archive(root: Path) -> None:
    """A copied legacy archive: two experiments, one with feedback naming a result analysis."""

    store = ArchiveStore(root)
    planned = {
        "schema_version": 2,
        "experiment_id": "tune-v2-aaaa",
        "created_at": "2026-09-01T10:00:00+00:00",
        "analysis_id": "old-driving",
        "analysis_path": OLD_HOME + r"\reports\a\old-driving\analysis.json",
        "open_target_analysis_id": "old-target",
        "open_target_analysis_path": OLD_HOME + r"\reports\a\old-target\analysis.json",
        "package_id": "package-1",
        "context": dict(CONTEXT),
        "setup": {"fingerprint": "fp-1"},
        "symptoms": [{"phase": "entry", "symptom": "loose"}],
        "structured_evidence": {"evidence_hash": "aaaa", "driving_ref": {"analysis_id": "old-driving"}},
        "recommendation": {"contract": "structured_tuning_recommendation_v2"},
        "status": "planned",
    }
    store.record_tuning_experiment(planned, updated_at="2026-09-01T10:00:00+00:00")
    store.record_tuning_feedback(
        "tune-v2-aaaa",
        {"driver_outcome": "improved", "result_analysis_id": "old-result",
         "result_analysis_path": OLD_HOME + r"\reports\b\old-result\analysis.json"},
        outcome="improved",
    )
    store.record_tuning_experiment(
        {"experiment_id": "tune-bbbb", "created_at": "2026-08-01T10:00:00+00:00", "analysis_id": "old-unknown",
         "context": dict(CONTEXT), "setup": {"fingerprint": "fp-0"}, "symptoms": [], "recommendation": {}},
        updated_at="2026-08-01T10:00:00+00:00",
    )
    # The index stores the old home's absolute paths, as a copied archive does.
    connection = sqlite3.connect(root / "history.sqlite3")
    try:
        with connection:
            connection.execute("UPDATE tuning_experiments SET experiment_path = ? || experiment_id", (OLD_HOME + "\\",))
    finally:
        connection.close()


class LegacyTuningImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.legacy = base / "iracing" / "legacy"
        self.archive = base / "archive"
        _legacy_archive(self.legacy)
        self.new_driving = self.archive / "reports" / "s" / "new-driving" / "analysis.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _map(self) -> list[dict[str, object]]:
        return [
            {"legacy_analysis_id": "old-driving", "analysis_id": "new-driving", "analysis_path": str(self.new_driving)},
            {"legacy_analysis_id": "old-target", "analysis_id": "new-target", "analysis_path": None},
            {"legacy_analysis_id": "old-result", "analysis_id": "new-result", "analysis_path": None},
        ]

    def _import(self, **overrides: object) -> dict:
        return legacy_import.import_legacy_tuning_experiments_workflow(
            legacy_archive_root=self.legacy, analysis_map=self._map(), archive_root=self.archive, **overrides
        )

    def test_references_are_rewritten_and_times_and_ids_kept(self) -> None:
        result = self._import()
        self.assertEqual((result["experiment_count"], result["resolved_count"], result["unresolved_count"]), (2, 1, 1))
        imported = ArchiveStore(self.archive).load_tuning_experiment("tune-v2-aaaa")
        self.assertEqual(imported["analysis_id"], "new-driving")
        self.assertEqual(imported["analysis_path"], str(self.new_driving))
        self.assertEqual(imported["open_target_analysis_id"], "new-target")
        self.assertIsNone(imported["open_target_analysis_path"])
        self.assertEqual(imported["feedback"]["result_analysis_id"], "new-result")
        self.assertEqual(imported["outcome"], "improved")
        self.assertEqual(imported["created_at"], "2026-09-01T10:00:00+00:00")
        # The evidence is the record of its time; its hash names the experiment.
        self.assertEqual(imported["structured_evidence"]["driving_ref"]["analysis_id"], "old-driving")
        self.assertEqual(imported["legacy_import"]["source"], "record")
        history = ArchiveStore(self.archive).tuning_history(CONTEXT, limit=10)
        self.assertEqual([row["experiment_id"] for row in history], ["tune-v2-aaaa", "tune-bbbb"])

    def test_an_unmapped_reference_is_null_and_listed_never_guessed(self) -> None:
        self._import()
        imported = ArchiveStore(self.archive).load_tuning_experiment("tune-bbbb")
        self.assertIsNone(imported["analysis_id"])
        self.assertEqual(
            imported["legacy_import"]["unresolved"],
            [{"field": "experiment.analysis_id", "legacy_analysis_id": "old-unknown", "legacy_analysis_path": None}],
        )

    def test_a_missing_record_file_falls_back_to_the_index_row(self) -> None:
        for path in (self.legacy / "tuning" / "experiments").rglob("tune-bbbb.json"):
            path.unlink()
        result = self._import()
        row = next(item for item in result["experiments"] if item["experiment_id"] == "tune-bbbb")
        self.assertEqual(row["source"], "index-row")
        self.assertEqual(ArchiveStore(self.archive).load_tuning_experiment("tune-bbbb")["setup"]["fingerprint"], "fp-0")

    def test_a_dry_run_writes_nothing_and_a_rerun_is_idempotent(self) -> None:
        self.assertEqual(self._import(dry_run=True)["experiment_count"], 2)
        self.assertEqual(ArchiveStore(self.archive).tuning_history(CONTEXT, limit=10), [])
        self._import()
        self._import()
        self.assertEqual(len(ArchiveStore(self.archive).tuning_history(CONTEXT, limit=10)), 2)

    def test_the_legacy_archive_is_not_written(self) -> None:
        before = {path: path.read_bytes() for path in self.legacy.rglob("*") if path.is_file()}
        self._import()
        after = {path: path.read_bytes() for path in self.legacy.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_the_tool_is_bounded_and_classified_as_a_write(self) -> None:
        self.assertEqual(authority.TOOL_CAPABILITIES["import_legacy_tuning_experiments"], authority.CAPABILITY_WRITE)
        tool = next(item for item in mcp_server.TOOLS if item["name"] == "import_legacy_tuning_experiments")
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        with (
            mock.patch.object(mcp_server, "DEFAULT_ARCHIVE_ROOT", self.archive),
            mock.patch.object(mcp_server, "DEFAULT_IRACING_ROOT", self.legacy.parent),
        ):
            result = mcp_server.call_tool(
                "import_legacy_tuning_experiments",
                {"legacy_archive_root": str(self.legacy), "analysis_map": self._map()},
            )
            self.assertEqual(result["experiment_count"], 2)
            with self.assertRaisesRegex(ValueError, "must stay within"):
                mcp_server.call_tool(
                    "import_legacy_tuning_experiments",
                    {"legacy_archive_root": str(self.archive), "analysis_map": []},
                )
            outside = [{"legacy_analysis_id": "x", "analysis_id": "y", "analysis_path": str(self.legacy / "analysis.json")}]
            with self.assertRaisesRegex(ValueError, "must stay within"):
                mcp_server.call_tool(
                    "import_legacy_tuning_experiments",
                    {"legacy_archive_root": str(self.legacy), "analysis_map": outside},
                )


if __name__ == "__main__":
    unittest.main()
