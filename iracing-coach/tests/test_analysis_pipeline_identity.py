from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analysis_engine  # noqa: E402
import workflow  # noqa: E402


class AnalysisPipelineIdentityTests(unittest.TestCase):
    """Guard the boundary between 'the analyzer changed' and 'the answers changed'.

    The persistent analysis cache is keyed on `_analysis_pipeline_sha256`. That
    fingerprint used to hash the analyzer's source bytes, so a reformat or a
    corrected comment silently orphaned every cached analysis - 19 live
    generations holding 2.42 GB on one real archive. It now hashes the declared
    contract instead, which is only safe if something forces a maintainer to
    notice when the analyzer's behaviour changes. That is this file's job.
    """

    def test_the_analyzer_bundle_matches_its_recorded_review(self) -> None:
        actual = analysis_engine.analyzer_bundle_sha256()
        self.assertEqual(
            actual,
            workflow.ANALYZER_BUNDLE_REVIEWED_SHA256,
            msg=(
                "The analyzer bundle changed. The analysis cache is no longer keyed on "
                "analyzer source, so this will NOT invalidate itself - you must choose:\n"
                "  * analysis math or emitted output changed -> bump ANALYSIS_SCHEMA_VERSION "
                "or ANALYSIS_PROFILE_VERSION in analysis_engine.py, then set "
                "ANALYZER_BUNDLE_REVIEWED_SHA256 in workflow.py to:\n"
                f"        {actual}\n"
                "    Cached analyses become unreachable, which is correct - they are wrong.\n"
                "  * only formatting, comments, typing or naming changed -> update "
                "ANALYZER_BUNDLE_REVIEWED_SHA256 alone, to the same value above.\n"
                "    Cached analyses stay valid, which is the point of the split.\n"
                "Do not update this digest to make a red test green without deciding which "
                "case you are in."
            ),
        )

    def test_the_fingerprint_covers_only_the_declared_contract(self) -> None:
        # Pin the exact input set rather than mocking a collaborator, so that
        # re-introducing source hashing - the defect this file exists to prevent -
        # fails here immediately instead of quietly orphaning caches again.
        expected = workflow.stable_hash(
            {
                "schema": 2,
                "analysis_channels": workflow.ANALYSIS_CHANNELS,
                "analysis_schema_version": workflow.ANALYSIS_SCHEMA_VERSION,
                "analysis_profile_version": workflow.ANALYSIS_PROFILE_VERSION,
            },
            64,
        )
        self.assertEqual(workflow._analysis_pipeline_sha256(), expected)

    def test_a_declared_version_bump_does_invalidate_cached_analyses(self) -> None:
        before = workflow._analysis_pipeline_sha256()
        with mock.patch.object(
            workflow, "ANALYSIS_PROFILE_VERSION", "post-race-foundations-test"
        ):
            profile_changed = workflow._analysis_pipeline_sha256()
        with mock.patch.object(workflow, "ANALYSIS_SCHEMA_VERSION", 9999):
            schema_changed = workflow._analysis_pipeline_sha256()

        self.assertNotEqual(
            before, profile_changed, "A profile bump must retire cached analyses."
        )
        self.assertNotEqual(
            before, schema_changed, "A schema bump must retire cached analyses."
        )

    def test_the_fingerprint_is_stable_across_calls(self) -> None:
        self.assertEqual(
            workflow._analysis_pipeline_sha256(),
            workflow._analysis_pipeline_sha256(),
        )
        self.assertEqual(len(workflow._analysis_pipeline_sha256()), 64)


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()
