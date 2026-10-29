"""Offline regression: real approval SQLite, mocked Kubernetes only."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_service.app import remediation_executor as executor
from api_service.app import approval_store


class RestartEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = {
            "pod": "opspilot-demo-fixture", "pod_uid": "fixture-demo-uid",
            "waiting_reason": "CrashLoopBackOff",
            "termination_reason": "Error", "exit_code": 1, "restart_count": 3,
        }
        self.install(patch.object(approval_store, "DB_PATH", str(Path(self.temp.name) / "approvals.db")))
        self.install(patch.dict(executor.os.environ, {"OPSPILOT_ENABLE_DEMO_RESTART": "1"}))
        self.incident = approval_store.create_approval(
            "restart_deployment", incident_type="application_failure", incident_evidence=self.saved)
        self.assertTrue(approval_store.decide_approval(self.incident, "approve"))
        self.live = self.install(patch.object(executor, "get_live_restart_evidence"))
        self.command = self.install(patch.object(executor.subprocess, "run", return_value=SimpleNamespace(stdout="")))
        self.recovery = self.install(patch.object(executor, "verify_recovery", return_value={"recovered": True}))

    def install(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def evidence(self, **changes):
        return {**self.saved, "confirmed": True, **changes}

    def execute(self):
        return executor.execute_approved_remediation(self.incident, execute=True)

    def test_retry_count_increases_before_and_after_claim(self):
        self.live.side_effect = [self.evidence(restart_count=6), self.evidence(restart_count=7)]
        self.assertEqual(self.execute()["status"], "recovered")
        self.command.assert_called_once()
        self.assertEqual(self.command.call_args.args[0], [
            "kubectl", "--context", "opspilot", "rollout", "restart",
            "deployment/opspilot-demo", "-n", "opspilot"])
        self.assertEqual(approval_store.get_approval(self.incident)["status"], "claimed")
        self.assertEqual(self.execute()["status"], "blocked")
        self.command.assert_called_once()

    def test_unchanged_counter_still_works(self):
        self.live.return_value = self.evidence()
        self.assertEqual(self.execute()["status"], "recovered")

    def test_changed_pod_or_crash_signature_is_blocked(self):
        for changes in ({"pod": "another-pod"}, {"pod_uid": "another-uid"},
                        {"waiting_reason": "ImagePullBackOff"},
                        {"termination_reason": "OOMKilled"}, {"exit_code": 137},
                        {"confirmed": False, "reason": "Pod recovered"}):
            with self.subTest(changes=changes):
                self.live.return_value = self.evidence(**changes)
                self.assertEqual(self.execute()["status"], "blocked")
                self.command.assert_not_called()

    def test_missing_invalid_or_decreasing_live_counter_is_blocked(self):
        for count in (None, "6", True, -1, 2):
            with self.subTest(count=count):
                self.live.return_value = self.evidence(restart_count=count)
                self.assertEqual(self.execute()["status"], "blocked")
                self.command.assert_not_called()

    def test_invalid_saved_counter_is_blocked(self):
        for count in (None, "3", True, -1):
            with self.subTest(count=count):
                evidence = {**self.saved, "restart_count": count}
                with approval_store.connect() as db:
                    db.execute("UPDATE approvals SET incident_evidence=? WHERE incident_id=?",
                               (json.dumps(evidence), self.incident))
                self.live.return_value = self.evidence(restart_count=6)
                self.assertEqual(self.execute()["status"], "blocked")
                self.command.assert_not_called()

    def test_pod_change_after_claim_is_blocked(self):
        self.live.side_effect = [self.evidence(restart_count=6), self.evidence(pod_uid="replacement", restart_count=7)]
        self.assertEqual(self.execute()["status"], "blocked")
        self.command.assert_not_called()

    def test_healthy_after_claim_is_blocked(self):
        self.live.side_effect = [self.evidence(restart_count=6), self.evidence(confirmed=False)]
        self.assertEqual(self.execute()["status"], "blocked")
        self.command.assert_not_called()

    def test_expired_approval_is_blocked(self):
        with approval_store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (self.incident,))
        self.assertEqual(self.execute()["status"], "blocked")
        self.live.assert_not_called()
        self.command.assert_not_called()

    def test_disabled_feature_does_not_consume_approval(self):
        self.live.return_value = self.evidence(restart_count=6)
        with patch.dict(executor.os.environ, {"OPSPILOT_ENABLE_DEMO_RESTART": "0"}):
            self.assertEqual(self.execute()["status"], "blocked")
        self.assertEqual(approval_store.get_approval(self.incident)["status"], "approved")
        self.command.assert_not_called()

    def test_dry_run_does_not_consume_approval(self):
        self.live.return_value = self.evidence(restart_count=6)
        result = executor.execute_approved_remediation(self.incident, execute=False)
        self.assertEqual(result["status"], "dry_run_passed")
        self.assertEqual(approval_store.get_approval(self.incident)["status"], "approved")
        self.command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
