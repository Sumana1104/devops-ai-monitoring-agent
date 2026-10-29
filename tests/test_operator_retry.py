"""Real temporary SQLite/journal; external AI, HTTP, Telegram and Kubernetes mocked."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_service.app import incident_history as history, remediation_executor as executor
from api_service.app import approval_store as store, incident_notification as notify
from opspilot_integration import bridge, operator
with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fixture-bot-token",
                             "TELEGRAM_WEBHOOK_SECRET": "fixture-webhook-secret"}):
    from opspilot_integration import telegram_webhook as webhook


class OperatorRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.install(patch.object(store, "DB_PATH", str(directory / "approvals.db")))
        self.install(patch.dict(os.environ, {"OPSPILOT_HISTORY_PATH": str(directory / "history.sqlite3")}))
        self.saved = {"pod": "fixture-demo", "pod_uid": "fixture-uid",
                      "waiting_reason": "CrashLoopBackOff", "termination_reason": "Error",
                      "exit_code": 1, "restart_count": 3}
        self.live = {**self.saved, "confirmed": True, "restart_count": 6}
        self.event = {"status": "firing", "labels": {
            "alertname": "OpsPilotDemoMetricsDown", "service": "opspilot-demo",
            "job": "opspilot-demo", "instance": "host.docker.internal:19110"},
            "startsAt": "2026-10-28T12:00:00Z", "fingerprint": "fixture-real-event"}
        self.root_id = history.reserve_investigation(self.event)
        self.prior = store.create_approval(
            "restart_deployment", incident_type="application_failure",
            incident_evidence=self.saved)
        history.link_approval(self.root_id, self.prior)
        with store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (self.prior,))
        self.http = self.install(patch.object(operator.requests, "get"))
        self.http.return_value.json.return_value = {
            "local_bridge": True, "demo_restart_enabled": True,
            "runtime_version": bridge.RUNTIME_VERSION}
        self.api = self.install(patch.object(webhook, "call", return_value={
            "url": "https://fixture-route.trycloudflare.com/telegram/webhook"}))
        self.route = self.install(patch.object(webhook, "check_route", return_value={"local_bridge": True}))
        self.verify = self.install(patch.object(bridge, "verify_current_alert",
                                               return_value={"status": "firing"}))
        self.inspect = self.install(patch.object(bridge, "inspect_crash", return_value=self.live))
        self.ai = self.install(patch.object(bridge, "run_ai_investigation", return_value=({
            "safe_action": "restart_deployment", "root_cause": "Controlled demo crash",
            "confidence": 0.92, "severity": "high", "policy_reason": "Verified live crash",
            "rag_status": "ready"}, None)))
        self.install(patch.object(notify, "get_live_restart_evidence",
                                  return_value={**self.live, "restart_count": 7}))
        self.send = self.install(patch.object(notify, "send_approval_request", return_value=123))
        self.execute = self.install(patch.object(executor, "execute_approved_remediation"))
        self.command = self.install(patch("subprocess.run", side_effect=AssertionError("unexpected Kubernetes call")))
        self.network = self.install(patch("requests.sessions.Session.request",
                                         side_effect=AssertionError("unexpected real HTTP call")))

    def install(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_retry(self, confirmed=True):
        with redirect_stdout(io.StringIO()):
            return operator.retry_approval(self.prior, confirmed=confirmed)

    def blocked(self):
        with self.assertRaises(ValueError):
            self.run_retry()
        self.send.assert_not_called()
        self.execute.assert_not_called()
        self.command.assert_not_called()

    def test_creates_fresh_linked_pending_request_without_approving_or_executing(self):
        result = self.run_retry()
        self.assertNotEqual(result["incident_id"], self.prior)
        new = store.get_approval(result["incident_id"])
        self.assertEqual(new["status"], "pending")
        self.assertGreater(new["expires_at"], int(time.time()))
        self.assertEqual(json.loads(new["incident_evidence"])["restart_count"], 7)
        old = store.get_approval(self.prior)
        self.assertEqual(old["status"], "pending")
        self.assertEqual(old["expires_at"], 0)
        row = history.get_investigation_for_approval(new["incident_id"])
        self.assertEqual(row["alert"]["labels"]["retry_of"], self.prior)
        self.assertEqual(operator.root_alert(row, history), self.event)
        self.ai.assert_called_once()
        self.send.assert_called_once()
        self.execute.assert_not_called()
        self.command.assert_not_called()

    def test_repeating_same_parent_does_not_send_another_request_or_call_ai(self):
        self.run_retry()
        with self.assertRaises(ValueError):
            self.run_retry()
        self.ai.assert_called_once()
        self.send.assert_called_once()

    def test_expired_child_retry_preserves_original_real_event_lineage(self):
        first = self.run_retry()
        self.prior = first["incident_id"]
        with store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (self.prior,))
        self.inspect.return_value = {**self.live, "restart_count": 8}
        notify.get_live_restart_evidence.return_value = {**self.live, "restart_count": 9}
        second = self.run_retry()
        row = history.get_investigation_for_approval(second["incident_id"])
        self.assertEqual(operator.root_alert(row, history), self.event)
        self.assertEqual(row["alert"]["labels"]["retry_of"], first["incident_id"])

    def test_unexpired_parent_is_not_renewed(self):
        with store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=? WHERE incident_id=?",
                       (int(time.time()) + 600, self.prior))
        self.blocked()
        self.ai.assert_not_called()

    def test_claimed_rejected_or_approved_without_expected_block_is_not_renewed(self):
        for status in ("claimed", "rejected", "approved"):
            with self.subTest(status=status):
                with store.connect() as db:
                    db.execute("UPDATE approvals SET status=? WHERE incident_id=?", (status, self.prior))
                self.blocked()

    def test_an_expired_counter_block_can_be_reinvestigated(self):
        with store.connect() as db:
            db.execute("UPDATE approvals SET status='approved' WHERE incident_id=?", (self.prior,))
        history.record_outcome(self.prior, {
            "status": "blocked", "reason": "Incident evidence changed after approval"})
        self.assertIn("incident_id", self.run_retry())
        self.execute.assert_not_called()

    def test_prior_attempted_execution_is_not_renewed(self):
        history.record_outcome(self.prior, {"status": "blocked", "execution_attempted": True})
        self.blocked()

    def test_requires_explicit_operator_confirmation(self):
        with self.assertRaises(ValueError):
            self.run_retry(confirmed=False)
        self.http.assert_not_called()
        self.ai.assert_not_called()

    def test_wrong_target_is_blocked(self):
        with store.connect() as db:
            db.execute("UPDATE approvals SET deployment='opspilot' WHERE incident_id=?", (self.prior,))
        self.blocked()

    def test_callback_failure_stops_before_ai_or_operator_reservation(self):
        self.route.side_effect = ValueError("Callback unavailable")
        self.blocked()
        self.ai.assert_not_called()
        self.assertEqual(len(history.recent_history(25)), 1)

    def test_old_runtime_or_disabled_execution_stops_before_ai(self):
        for changes in ({"runtime_version": "old"}, {"demo_restart_enabled": False}):
            with self.subTest(changes=changes):
                self.http.return_value.json.return_value = {
                    "local_bridge": True, "demo_restart_enabled": True,
                    "runtime_version": bridge.RUNTIME_VERSION, **changes}
                self.blocked()
        self.ai.assert_not_called()

    def test_changed_pod_crash_or_decreasing_counter_is_blocked(self):
        for changes in ({"pod_uid": "replacement"}, {"exit_code": 137},
                        {"termination_reason": "OOMKilled"}, {"restart_count": 2},
                        {"restart_count": True}, {"confirmed": False}):
            with self.subTest(changes=changes):
                self.inspect.return_value = {**self.live, **changes}
                self.blocked()
        self.ai.assert_not_called()

    def test_original_alert_must_still_be_firing(self):
        self.verify.return_value = {"status": "inactive"}
        self.blocked()
        self.ai.assert_not_called()

    def test_alert_that_clears_during_ai_does_not_get_approval(self):
        self.verify.side_effect = [{"status": "firing"}, {"status": "inactive"}]
        self.blocked()
        self.ai.assert_called_once()

    def test_manual_review_proposal_does_not_get_approval(self):
        self.ai.return_value = ({"safe_action": "manual_review"}, None)
        self.blocked()

    def test_ai_failure_does_not_get_approval(self):
        self.ai.return_value = (None, "fixture_provider_failure")
        self.blocked()

    def test_replacement_between_final_check_and_notification_is_blocked(self):
        notify.get_live_restart_evidence.return_value = {**self.live, "pod_uid": "replacement"}
        self.blocked()

    def test_lineage_claiming_another_real_alert_is_rejected(self):
        fake = {"alert": {"source": "operator_requested_retry",
                         "labels": {"alertname": "OpsPilotOperatorRetry", "retry_of": self.prior},
                         "original_alert": {**self.event, "fingerprint": "different-event"}}}
        with self.assertRaises(ValueError):
            operator.root_alert(fake, history)

    def test_cyclic_lineage_is_rejected(self):
        event = {"source": "operator_requested_retry",
                 "labels": {"alertname": "OpsPilotOperatorRetry", "retry_of": self.prior},
                 "original_alert": self.event}
        with patch.object(history, "get_investigation_for_approval", return_value={"alert": event}):
            with self.assertRaises(ValueError):
                operator.root_alert({"alert": event}, history)


if __name__ == "__main__":
    unittest.main()
