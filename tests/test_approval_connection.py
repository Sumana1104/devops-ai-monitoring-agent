"""No network/Kubernetes: connection closure and approval semantics."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_service.app import approval_store


class ApprovalConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "approval-test.sqlite3")
        self.target = patch.object(approval_store, "DB_PATH", self.path)
        self.target.start()

    def tearDown(self):
        self.target.stop()
        self.temp.cleanup()

    def test_success_commits_and_closes(self):
        with approval_store.connect() as db:
            db.execute("CREATE TABLE closure_test (value INTEGER)")
            db.execute("INSERT INTO closure_test VALUES (7)")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with approval_store.connect() as check:
            self.assertEqual(check.execute("SELECT value FROM closure_test").fetchone()[0], 7)

    def test_failure_rolls_back_and_closes(self):
        with approval_store.connect() as db:
            db.execute("CREATE TABLE closure_test (value INTEGER)")
        with self.assertRaisesRegex(RuntimeError, "test rollback"):
            with approval_store.connect() as db:
                db.execute("INSERT INTO closure_test VALUES (7)")
                raise RuntimeError("test rollback")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with approval_store.connect() as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM closure_test").fetchone()[0], 0)

    def test_approval_and_atomic_claim_still_work_once(self):
        incident_id = approval_store.create_approval(
            "restart_deployment", incident_type="application_failure",
            incident_evidence={"pod": "demo-test", "pod_uid": "test-only"},
        )
        self.assertTrue(approval_store.decide_approval(incident_id, "approve"))
        self.assertFalse(approval_store.decide_approval(incident_id, "approve"))
        self.assertIsNotNone(approval_store.claim_approved_action(incident_id))
        self.assertIsNone(approval_store.claim_approved_action(incident_id))
        self.assertEqual(approval_store.get_approval(incident_id)["status"], "claimed")


if __name__ == "__main__":
    unittest.main()
