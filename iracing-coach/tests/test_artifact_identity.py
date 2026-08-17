"""The idempotence closure, case by case.

`ARTIFACT-IDEMPOTENCE-001`, `IDEMPOTENCE-SEQUENCING-001`, `MIGRATION-GROWTH-001`.

The accepted closure names six properties: an identical invocation reuses one
content object, a dependency change creates one revision, all prior revision
ids resolve, an interrupted migration resumes, there is no orphan, and a
dependency mutation invalidates exactly once. Each has its own class below so a
failure names the property it broke.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import artifact_identity as ai  # noqa: E402

DEPS = {"analysis_id": "abc", "profile": "v13"}
CHANGED = {"analysis_id": "abc", "profile": "v14"}
PAYLOAD = b'{"report": 1}'
OTHER = b'{"report": 2}'


class IdenticalInvocationTests(unittest.TestCase):
    def test_a_repeated_invocation_returns_the_same_revision(self):
        store = ai.ArtifactStore()
        first, created_first = store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        second, created_second = store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.revision_id, second.revision_id)

    def test_a_repeated_invocation_grows_nothing(self):
        store = ai.ArtifactStore()
        for _ in range(10):
            store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        self.assertEqual(len(store.revisions()), 1)
        self.assertEqual(len(store.content_objects()), 1)

    def test_identical_bytes_from_different_subjects_share_one_content_object(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        store.record(subject="card", dependencies=DEPS, payload=PAYLOAD)
        self.assertEqual(len(store.revisions()), 2)
        objects = store.content_objects()
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].reference_count, 2)

    def test_the_address_is_the_digest_of_the_bytes(self):
        self.assertEqual(ai.content_address(PAYLOAD), ai.content_address(bytes(PAYLOAD)))
        self.assertNotEqual(ai.content_address(PAYLOAD), ai.content_address(OTHER))

    def test_content_must_be_addressed_as_bytes_not_as_an_object(self):
        for value in ('{"report": 1}', {"report": 1}, 1, None):
            with self.subTest(value=value):
                with self.assertRaises(ai.ArtifactIdentityError):
                    ai.content_address(value)


class DependencyChangeTests(unittest.TestCase):
    def test_a_dependency_change_creates_exactly_one_revision(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        _, created = store.record(subject="report", dependencies=CHANGED, payload=OTHER)
        self.assertTrue(created)
        self.assertEqual(len(store.revisions("report")), 2)

    def test_a_dependency_mutation_invalidates_exactly_once(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        store.record(subject="report", dependencies=CHANGED, payload=OTHER)
        for _ in range(5):
            _, created = store.record(
                subject="report", dependencies=CHANGED, payload=OTHER
            )
            self.assertFalse(created)
        self.assertEqual(len(store.revisions("report")), 2)

    def test_a_dependency_change_with_identical_bytes_still_records_provenance(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        revision, created = store.record(
            subject="report", dependencies=CHANGED, payload=PAYLOAD
        )
        self.assertTrue(created)
        self.assertEqual(len(store.revisions("report")), 2)
        self.assertEqual(len(store.content_objects()), 1)
        self.assertEqual(store.content_objects()[0].reference_count, 2)
        self.assertEqual(revision.dependency_digest, ai.dependency_digest(CHANGED))

    def test_the_dependency_digest_covers_the_contract_version(self):
        original = ai.ARTIFACT_IDENTITY_VERSION
        before = ai.dependency_digest(DEPS)
        try:
            ai.ARTIFACT_IDENTITY_VERSION = original + 1
            self.assertNotEqual(before, ai.dependency_digest(DEPS))
        finally:
            ai.ARTIFACT_IDENTITY_VERSION = original
        self.assertEqual(before, ai.dependency_digest(DEPS))

    def test_the_digest_is_stable_across_key_order(self):
        self.assertEqual(
            ai.dependency_digest({"a": 1, "b": 2}),
            ai.dependency_digest({"b": 2, "a": 1}),
        )

    def test_a_non_mapping_dependency_set_is_refused(self):
        for value in ([], "deps", None, 1):
            with self.subTest(value=value):
                with self.assertRaises(ai.ArtifactIdentityError):
                    ai.dependency_digest(value)

    def test_an_artifact_without_a_subject_is_refused(self):
        store = ai.ArtifactStore()
        with self.assertRaises(ai.ArtifactIdentityError):
            store.record(subject="", dependencies=DEPS, payload=PAYLOAD)


class RevisionRetentionTests(unittest.TestCase):
    def test_every_prior_revision_id_still_resolves(self):
        store = ai.ArtifactStore()
        ids = []
        for index in range(6):
            revision, _ = store.record(
                subject="report",
                dependencies={**DEPS, "n": index},
                payload=f"body-{index}".encode(),
            )
            ids.append(revision.revision_id)
        for revision_id in ids:
            with self.subTest(revision_id):
                self.assertEqual(store.resolve(revision_id).revision_id, revision_id)

    def test_every_prior_revision_still_reads_its_own_bytes(self):
        store = ai.ArtifactStore()
        expected = {}
        for index in range(6):
            payload = f"body-{index}".encode()
            revision, _ = store.record(
                subject="report", dependencies={**DEPS, "n": index}, payload=payload
            )
            expected[revision.revision_id] = payload
        for revision_id, payload in expected.items():
            with self.subTest(revision_id):
                self.assertEqual(store.read(revision_id), payload)

    def test_the_latest_revision_is_the_most_recently_created_one(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        newest, _ = store.record(subject="report", dependencies=CHANGED, payload=OTHER)
        self.assertEqual(store.latest("report").revision_id, newest.revision_id)

    def test_a_subject_with_no_revisions_has_no_latest(self):
        self.assertIsNone(ai.ArtifactStore().latest("nothing"))

    def test_an_unknown_revision_is_refused_rather_than_returning_nothing(self):
        with self.assertRaises(ai.ArtifactIdentityError):
            ai.ArtifactStore().resolve("rev-nope")

    def test_revisions_of_one_subject_do_not_include_another(self):
        store = ai.ArtifactStore()
        store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        store.record(subject="card", dependencies=DEPS, payload=OTHER)
        self.assertEqual(len(store.revisions("report")), 1)
        self.assertEqual(len(store.revisions()), 2)


class NoOrphanTests(unittest.TestCase):
    def _populated(self) -> ai.ArtifactStore:
        store = ai.ArtifactStore()
        for index in range(8):
            store.record(
                subject="report",
                dependencies={**DEPS, "n": index},
                payload=f"body-{index % 3}".encode(),
            )
        return store

    def test_no_content_object_is_unreferenced(self):
        self.assertEqual(self._populated().orphans(), [])

    def test_no_revision_points_at_missing_content(self):
        self.assertEqual(self._populated().dangling(), [])

    def test_deduplication_is_visible_in_the_reference_counts(self):
        store = self._populated()
        objects = store.content_objects()
        self.assertEqual(len(objects), 3)
        self.assertEqual(sum(item.reference_count for item in objects), 8)

    def test_the_orphan_check_actually_detects_one(self):
        """Guard the check itself, so a vacuous pass cannot look like a pass."""
        store = self._populated()
        store._content["sha256:unreferenced"] = b"stray"
        self.assertEqual(store.orphans(), ["sha256:unreferenced"])

    def test_the_dangling_check_actually_detects_one(self):
        store = self._populated()
        victim = store.revisions()[0]
        del store._content[victim.content_address]
        self.assertIn(victim.revision_id, store.dangling())


class MigrationResumeTests(unittest.TestCase):
    def _plan(self) -> tuple[ai.ArtifactStore, ai.MigrationPlan]:
        store = ai.ArtifactStore()
        ids = []
        for index in range(16):
            revision, _ = store.record(
                subject="report",
                dependencies={**DEPS, "n": index},
                payload=f"body-{index}".encode(),
            )
            ids.append(revision.revision_id)
        return store, ai.MigrationPlan(revision_ids=tuple(ids))

    def test_a_fresh_plan_has_every_step_pending(self):
        _, plan = self._plan()
        self.assertEqual(len(plan.pending()), 16)
        self.assertFalse(plan.complete)

    def test_an_interrupted_migration_resumes_at_the_first_incomplete_step(self):
        _, plan = self._plan()
        for revision_id in plan.revision_ids[:7]:
            plan.mark(revision_id, "completed")
        resumed = ai.MigrationPlan(revision_ids=plan.revision_ids, journal=dict(plan.journal))
        self.assertEqual(resumed.pending(), list(plan.revision_ids[7:]))

    def test_resuming_never_redoes_a_completed_step(self):
        _, plan = self._plan()
        for revision_id in plan.revision_ids:
            plan.mark(revision_id, "completed")
        self.assertEqual(plan.pending(), [])
        self.assertTrue(plan.complete)

    def test_a_failed_step_stays_pending_and_is_retried(self):
        _, plan = self._plan()
        plan.mark(plan.revision_ids[0], "failed")
        self.assertIn(plan.revision_ids[0], plan.pending())

    def test_a_completed_step_cannot_be_reopened(self):
        _, plan = self._plan()
        plan.mark(plan.revision_ids[0], "completed")
        for state in ("pending", "failed"):
            with self.subTest(state):
                with self.assertRaises(ai.ArtifactIdentityError):
                    plan.mark(plan.revision_ids[0], state)

    def test_marking_a_step_outside_the_plan_is_refused(self):
        _, plan = self._plan()
        with self.assertRaises(ai.ArtifactIdentityError):
            plan.mark("rev-not-in-plan", "completed")

    def test_an_undeclared_step_state_is_refused(self):
        _, plan = self._plan()
        with self.assertRaises(ai.ArtifactIdentityError):
            plan.mark(plan.revision_ids[0], "sort-of-done")

    def test_replaying_a_migration_step_produces_no_new_storage(self):
        """Redoing an interrupted step must be free, not merely harmless."""
        store, plan = self._plan()
        before_revisions = len(store.revisions())
        before_objects = len(store.content_objects())
        for index, revision_id in enumerate(plan.revision_ids[:5]):
            revision = store.resolve(revision_id)
            _, created = store.record(
                subject=revision.subject,
                dependencies={**DEPS, "n": index},
                payload=store.read(revision_id),
            )
            self.assertFalse(created)
        self.assertEqual(len(store.revisions()), before_revisions)
        self.assertEqual(len(store.content_objects()), before_objects)
        self.assertEqual(store.orphans(), [])

    def test_a_partially_migrated_store_still_has_no_orphan_or_dangling_entry(self):
        store, plan = self._plan()
        for revision_id in plan.revision_ids[:9]:
            plan.mark(revision_id, "completed")
        self.assertEqual(store.orphans(), [])
        self.assertEqual(store.dangling(), [])


class NoIoTests(unittest.TestCase):
    def test_the_module_touches_no_filesystem_or_clock(self):
        source = (SCRIPTS / "artifact_identity.py").read_text(encoding="utf-8")
        for forbidden in ("open(", "Path(", "os.", "shutil", "time.", "datetime", "utc_now"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_revision_field_records_a_timestamp(self):
        store = ai.ArtifactStore()
        revision, _ = store.record(subject="report", dependencies=DEPS, payload=PAYLOAD)
        payload = revision.to_payload()
        self.assertFalse(
            [key for key in payload if "time" in key or "stamp" in key or "at" == key],
            payload,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
