"""Offline integration: REAL LangGraph/Pydantic/SQLite and ASGI webhook.

Groq, Telegram and kubectl are mocked. A separate test exercises real local
Chroma with deterministic test vectors (not a semantic-quality benchmark).
No paid API call or Kubernetes mutation is performed by this test suite.
"""
import copy
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from groq import BadRequestError, Groq as SDKGroq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPSPILOT_DISABLE_WATCHER", "1")

from fastapi.testclient import TestClient
from ai_service.app import ai_agent, incident_history as history, remediation_executor as executor
from ai_service.app import rag_tools
from api_service.app import approval_store, incident_notification
from opspilot_integration import bridge, learning, incident_dedup
from opspilot_integration.alert_freshness import verify_current_alert

# Expected failure-path tests should not dump provider/log payloads into CI.
logging.disable(logging.CRITICAL)


def crash_pod():
    return {"metadata": {"name": "opspilot-demo-old", "uid": "pod-uid-1"}, "status": {
        "phase": "Running", "conditions": [{"type": "Ready", "status": "False"}],
        "containerStatuses": [{"name": "opspilot-demo", "ready": False, "restartCount": 4,
            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
            "lastState": {"terminated": {"reason": "Error", "exitCode": 1}}}],
    }}


def alert():
    return {"receiver": "local-opspilot", "alerts": [{"status": "firing",
        "labels": {"alertname": "OpsPilotDemoMetricsDown", "service": "opspilot-demo", "severity": "high"},
        "annotations": {"summary": "Demo scrape failed"},
        "fingerprint": "realistic-test-fingerprint", "startsAt": "2026-10-28T10:00:00Z"}]}


class AIFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.patches = []
        self.install(patch.dict(os.environ, {
            "OPSPILOT_HISTORY_PATH": str(self.directory / "history.sqlite3"),
            "OPSPILOT_APPROVAL_DB": str(self.directory / "approvals.sqlite3"),
            "OPSPILOT_CHROMA_PATH": str(self.directory / "chroma"),
            "OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_ENABLE_DEMO_RESTART": "1",
            "GROQ_API_KEY": "test-key-not-real", "TELEGRAM_WEBHOOK_SECRET": "test-webhook-secret",
            "GROQ_MODEL": "openai/gpt-oss-20b",
            "TELEGRAM_APPROVER_USER_ID": "10", "TELEGRAM_CHAT_ID": "20",
            "ANONYMIZED_TELEMETRY": "False",
        }))
        self.install(patch.object(approval_store, "DB_PATH", str(self.directory / "approvals.sqlite3")))
        self.restarted = False
        self.commands = []
        self.install(patch.object(ai_agent, "kubectl", side_effect=self.graph_kubectl))
        self.install(patch.object(executor.subprocess, "run", side_effect=self.executor_kubectl))
        self.metric = self.install(patch.object(ai_agent.requests, "get"))
        self.metric.return_value.json.return_value = {"status": "success", "data": {"result": []}}
        self.references = [{"document": "Demo crash.flag persists in emptyDir; approved rollout creates an empty new pod.",
                            "metadata": {"type": "runbook", "source": "demo-crash-runbook"}, "distance": 0.1}]
        self.retrieval = self.install(patch.object(ai_agent, "retrieve_similar_incidents", return_value=self.references))
        self.report = {"root_cause": "Intentional demo crash.flag observed; recovery is a hypothesis until verified",
                       "severity": "high", "confidence": 0.9,
                       "evidence": ["Current container is CrashLoopBackOff; prior exit 1/Error"],
                       "recommended_action": "restart_deployment"}
        self.groq = self.install(patch.object(ai_agent, "Groq"))
        self.set_report(self.report)
        self.send = self.install(patch.object(incident_notification, "send_approval_request", return_value=99))
        self.text = self.install(patch.object(bridge, "telegram_text"))
        self.install(patch.object(bridge, "telegram_api", return_value={"ok": True}))
        self.install(patch.object(bridge, "alert_token", return_value="test-alert-token"))
        self.verification = self.install(patch.object(bridge, "verify_current_alert", return_value={
            "status": "firing", "reason": "Offline fixture: current alert verified", "checked_at": 0,
        }))
        self.index = self.install(patch.object(learning, "store_incident"))
        bridge._seen.clear()
        self.client = TestClient(bridge.app)

    def install(self, patcher):
        self.patches.append(patcher)
        return patcher.start()

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def set_report(self, report):
        self.groq.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(report)), finish_reason="stop")])

    def graph_kubectl(self, *args):
        if args[:2] == ("get", "pods"):
            return json.dumps({"items": [crash_pod()]})
        if args[0] == "logs":
            return "Intentional demo CrashLoopBackOff\n"
        if args[:2] == ("get", "events"):
            return json.dumps({"items": [{"type": "Warning", "reason": "BackOff", "message": "Back-off restarting"}]})
        raise AssertionError("Unexpected/non-read-only graph command: " + str(args))

    def executor_kubectl(self, args, **kwargs):
        self.commands.append(args)
        self.assertEqual(args[:3], ["kubectl", "--context", "opspilot"])
        if "restart" in args:
            self.assertIn("deployment/opspilot-demo", args)
            self.restarted = True
            return SimpleNamespace(stdout="deployment restarted", returncode=0)
        if "rollout" in args:
            return SimpleNamespace(stdout="deployment successfully rolled out", returncode=0)
        if "deployment" in args:
            return SimpleNamespace(stdout=json.dumps({"metadata": {"generation": 2}, "spec": {"replicas": 1},
                "status": {"readyReplicas": 1, "updatedReplicas": 1, "availableReplicas": 1, "observedGeneration": 2}}))
        if "pods" in args:
            return SimpleNamespace(stdout=json.dumps({"items": [crash_pod()]}))
        raise AssertionError("Unexpected executor command: " + str(args))

    def send_alert(self, payload=None, token="test-alert-token"):
        return self.client.post("/alerts", json=payload or alert(), headers={"Authorization": "Bearer " + token})

    def callback(self, approval_id, decision="approve", secret="test-webhook-secret", user=10):
        return self.client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": secret}, json={
            "callback_query": {"id": "test-callback", "from": {"id": user},
                               "message": {"chat": {"id": 20}}, "data": decision + ":" + approval_id}})

    def test_alert_runs_real_graph_and_requires_approval(self):
        response = self.send_alert()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accepted"], 1)
        self.assertFalse(self.restarted)
        row = history.recent_history()[0]
        self.assertEqual(row["state"]["steps"], ["collect_evidence", "retrieve_knowledge", "investigate", "select_action", "persist_report"])
        self.assertEqual(row["state"]["safe_action"], "restart_deployment")
        self.assertEqual(approval_store.get_approval(row["approval_id"])["status"], "pending")
        prompt = self.groq.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("demo-crash-runbook", prompt)
        self.assertIn("Intentional demo CrashLoopBackOff", prompt)
        self.send.assert_called_once()

    def test_full_approve_recovery_history_learning_and_replay(self):
        self.send_alert()
        approval_id = history.recent_history()[0]["approval_id"]
        response = self.callback(approval_id)
        self.assertEqual(response.json()["status"], "decision_recorded")
        self.assertTrue(self.restarted)
        row = history.recent_history()[0]
        self.assertEqual(row["outcome"]["status"], "recovered")
        self.assertTrue(row["indexed"])
        self.assertTrue(self.index.call_args.kwargs["verified"])
        self.assertEqual(self.callback(approval_id).json()["status"], "invalid_or_expired_approval")
        self.assertEqual(sum("restart" in c for c in self.commands), 1)

    def test_reject_is_journalled_but_not_executed_or_indexed(self):
        self.send_alert()
        self.callback(history.recent_history()[0]["approval_id"], "reject")
        self.assertFalse(self.restarted)
        self.assertEqual(history.recent_history()[0]["stage"], "rejected")
        self.index.assert_not_called()

    def test_dry_run_does_not_index_verified_recovery(self):
        os.environ["OPSPILOT_ENABLE_DEMO_RESTART"] = "0"
        self.send_alert()
        self.callback(history.recent_history()[0]["approval_id"])
        self.assertFalse(self.restarted)
        self.assertEqual(history.recent_history()[0]["stage"], "dry_run_passed")
        self.index.assert_not_called()

    def test_wrong_alert_token_or_wrong_service_does_not_call_ai(self):
        self.assertEqual(self.send_alert(token="wrong").status_code, 403)
        payload = alert()
        payload["alerts"][0]["labels"]["service"] = "other-service"
        self.assertEqual(self.send_alert(payload).json()["accepted"], 0)
        self.groq.assert_not_called()

    def test_wrong_webhook_secret_or_approver_does_not_execute(self):
        self.send_alert()
        approval_id = history.recent_history()[0]["approval_id"]
        self.assertEqual(self.callback(approval_id, secret="wrong").status_code, 403)
        self.assertEqual(self.callback(approval_id, user=999).json()["status"], "unauthorized_approver")
        self.assertFalse(self.restarted)

    def test_redelivery_after_memory_reset_does_not_repeat_ai_or_approval(self):
        self.send_alert()
        bridge._seen.clear()
        self.send_alert()
        self.send.assert_called_once()
        self.assertEqual(self.groq.call_count, 1)
        self.assertEqual(len(history.recent_history()), 1)

    def test_missing_rag_is_manual_review(self):
        self.retrieval.side_effect = RuntimeError("RAG down")
        self.send_alert()
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["state"]["safe_action"], "manual_review")
        self.assertFalse(self.restarted)

    def test_empty_rag_does_not_offer_restart(self):
        self.retrieval.return_value = []
        self.send_alert()
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["state"]["rag_status"], "empty")

    def test_current_healthy_pod_blocks_ai_restart_proposal(self):
        with patch.object(executor, "get_live_restart_evidence", return_value={
            "confirmed": False, "reason": "No eligible active crash confirmed"}):
            self.send_alert()
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["state"]["safe_action"], "manual_review")

    def test_low_confidence_does_not_offer_restart(self):
        self.set_report({**self.report, "confidence": 0.2})
        self.send_alert()
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["state"]["safe_action"], "manual_review")

    def test_ai_manual_review_is_not_overridden_by_watcher(self):
        self.set_report({**self.report, "recommended_action": "manual_review"})
        self.send_alert()
        self.send.assert_not_called()
        with patch.object(bridge._stop, "wait", side_effect=[False, True]), \
             patch.object(bridge, "inspect_crash", return_value={"confirmed": True}), \
             patch.object(bridge, "process_crash_evidence") as bypass:
            bridge.watch_kubernetes()
        bypass.assert_not_called()

    def test_invalid_ai_action_fails_closed(self):
        self.set_report({**self.report, "recommended_action": "delete_namespace"})
        self.send_alert()
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["stage"], "ai_failed")

    def test_expired_approval_cannot_restart(self):
        self.send_alert()
        approval_id = history.recent_history()[0]["approval_id"]
        with approval_store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (approval_id,))
        self.assertEqual(self.callback(approval_id).json()["status"], "invalid_or_expired_approval")
        self.assertFalse(self.restarted)

    def test_changed_pod_uid_is_blocked_before_restart(self):
        self.send_alert()
        approval_id = history.recent_history()[0]["approval_id"]
        real = executor.get_live_restart_evidence()
        with patch.object(executor, "get_live_restart_evidence", return_value={**real, "pod_uid": "different-pod"}):
            self.callback(approval_id)
        self.assertFalse(self.restarted)
        self.assertEqual(history.recent_history()[0]["stage"], "blocked")

    def test_last_25_history_and_redaction(self):
        for index in range(30):
            event = copy.deepcopy(alert()["alerts"][0])
            event["fingerprint"] = str(index)
            investigation_id = history.reserve_investigation(event)
            history.save_investigation(investigation_id, {"pod_logs": "secret=test-key-not-real"})
        rows = history.recent_history(100)
        self.assertEqual(len(rows), 25)
        self.assertNotIn("test-key-not-real", json.dumps(rows))

    def test_last_25_actual_summaries_reach_groq_prompt(self):
        for index in range(27):
            event = copy.deepcopy(alert()["alerts"][0])
            event["fingerprint"] = str(index)
            investigation_id = history.reserve_investigation(event)
            history.save_investigation(investigation_id, {"incident": f"Old real snapshot {index}",
                                                         "safe_action": "manual_review"})
        self.send_alert()
        content = self.groq.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        summary = json.loads(content)["recent_history_summary"]
        self.assertEqual(summary.count("Prior investigation "), 25)
        self.assertIn("Old real snapshot 26", summary)
        self.assertNotIn("Old real snapshot 0;", summary)

    def test_index_failure_keeps_verified_journal_without_reexecuting(self):
        self.send_alert()
        approval_id = history.recent_history()[0]["approval_id"]
        self.index.side_effect = RuntimeError("local index unavailable")
        self.callback(approval_id)
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "recovered")
        self.assertFalse(row["indexed"])
        self.assertEqual(len(history.pending_verified_outcomes()), 1)
        commands_before_retry = len(self.commands)
        self.index.side_effect = None
        self.assertTrue(learning.index_recovered_investigation(row))
        self.assertEqual(len(self.commands), commands_before_retry)

    def test_invalid_alert_body_is_rejected_before_investigation(self):
        response = self.client.post("/alerts", json=["invalid"],
                                    headers={"Authorization": "Bearer test-alert-token"})
        self.assertEqual(response.status_code, 400)
        self.groq.assert_not_called()

    def test_only_verified_linked_recovery_can_be_learned(self):
        row = {"approval_id": None, "state": {}, "outcome": {"status": "recovered"}}
        self.assertFalse(learning.index_recovered_investigation(row))
        self.index.assert_not_called()
        with self.assertRaises(ValueError):
            rag_tools.store_incident("id", "example", "unproven", "dry run")

    def provider_error(self, code="json_validate_failed", nested=True):
        detail = {"message": "Failed to validate JSON test-key-not-real", "code": code,
                  "failed_generation": "Never echo this provider payload test-key-not-real"}
        return BadRequestError("Provider rejected output", body={"error": detail} if nested else detail,
                               response=httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")))

    def test_inactive_queued_alert_is_journalled_without_ai_or_telegram(self):
        self.verification.return_value = {"status": "inactive", "reason": "Fresh scrape is up"}
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "ignored_inactive")
        self.assertIsNone(row["approval_id"])
        self.assertFalse(row["indexed"])
        self.groq.assert_not_called()
        self.text.assert_not_called()
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)
        bridge._seen.clear()
        self.send_alert()
        self.assertEqual(len(history.recent_history()), 1)
        self.assertEqual(self.verification.call_count, 1)

    def test_alert_that_clears_during_ai_does_not_send_an_incident(self):
        self.verification.side_effect = [{"status": "firing", "reason": "Current rule firing"},
                                         {"status": "inactive", "reason": "Alert cleared while AI ran"}]
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "ignored_inactive")
        self.assertIn("analysis", row["state"])
        self.assertEqual(row["state"]["alert_verification"]["before_ai"]["status"], "firing")
        self.assertEqual(row["state"]["alert_verification"]["after_ai"]["status"], "inactive")
        self.text.assert_not_called()
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)

    def test_monitoring_unavailable_is_an_explicit_warning_not_silent_recovery(self):
        self.verification.return_value = {"status": "unknown", "reason": "Monitoring request timed out"}
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "verification_unavailable")
        self.assertIsNone(row["approval_id"])
        self.assertIn("health is NOT confirmed", str(self.text.call_args))
        self.groq.assert_not_called()
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)

    def test_monitoring_lost_after_ai_cannot_create_approval(self):
        self.verification.side_effect = [{"status": "firing", "reason": "Current rule firing"},
                                         {"status": "unknown", "reason": "Monitoring query failed"}]
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "verification_unavailable")
        self.assertIn("analysis", row["state"])
        self.assertIsNone(row["approval_id"])
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)

    def test_resolved_delivery_never_invokes_ai_or_sends_new_incident(self):
        event = alert()
        event["alerts"][0]["status"] = "resolved"
        self.assertEqual(self.send_alert(event).json()["accepted"], 0)
        self.verification.assert_not_called()
        self.groq.assert_not_called()
        self.text.assert_not_called()

    def test_new_firing_episode_is_not_lost_when_resolved_delivery_was_missed(self):
        self.send_alert()
        event = alert()
        event["alerts"][0]["startsAt"] = "2026-10-28T12:00:00Z"
        self.assertEqual(self.send_alert(event).json()["accepted"], 1)
        self.assertEqual(self.groq.call_count, 2)
        self.assertEqual(len(history.recent_history()), 2)
        # Ongoing crash approval deduplication is still independent and intact.
        self.send.assert_called_once()
        self.assertFalse(self.restarted)

    def test_current_real_alert_still_notifies_when_ai_proposes_no_action(self):
        self.set_report({**self.report, "recommended_action": "no_action"})
        self.send_alert()
        self.assertIn("currently firing", str(self.text.call_args))
        self.send.assert_not_called()
        self.assertEqual(history.recent_history()[0]["stage"], "investigated")

    def test_ai_failure_after_alert_clears_is_saved_without_new_failure_notification(self):
        self.verification.side_effect = [{"status": "firing", "reason": "Current rule firing"},
                                         {"status": "inactive", "reason": "No matching firing rule"}]
        self.groq.return_value.chat.completions.create.side_effect = self.provider_error()
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "ignored_inactive")
        self.assertEqual(row["state"]["analysis"], "groq_json_validate_failed")
        self.text.assert_not_called()
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def real_monitoring_fixture(self, scrape_health):
        from datetime import datetime, timezone
        event = alert()
        event["alerts"][0]["labels"].update({"job": "opspilot-demo", "instance": "host.docker.internal:19110"})
        labels = event["alerts"][0]["labels"]
        self.verification.side_effect = verify_current_alert

        def response(url, **kwargs):
            value = Mock()
            if url.endswith("/api/v1/alerts"):
                data = {"alerts": [{"state": "firing", "labels": labels}]}
            elif url.endswith("/api/v1/targets?state=active"):
                data = {"activeTargets": [{"labels": {"job": "opspilot-demo", "instance": labels["instance"]},
                        "health": scrape_health, "lastScrape": datetime.now(timezone.utc).isoformat()}]}
            elif url.endswith("/api/v1/query"):
                data = {"result": []}
            else:
                raise AssertionError("Unexpected monitoring URL")
            value.json.return_value = {"status": "success", "data": data}
            return value

        self.metric.side_effect = response
        return event

    def test_full_webhook_real_monitoring_check_to_human_approved_recovery(self):
        event = self.real_monitoring_fixture("down")
        self.assertEqual(self.send_alert(event).status_code, 200)
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "awaiting_approval")
        self.assertEqual(row["state"]["alert_verification"]["after_ai"]["status"], "firing")
        self.assertFalse(self.restarted)
        self.callback(row["approval_id"])
        self.assertTrue(self.restarted)
        self.assertEqual(history.recent_history()[0]["stage"], "recovered")
        self.assertTrue(history.recent_history()[0]["indexed"])

    def test_full_webhook_real_monitoring_check_suppresses_recovered_target(self):
        event = self.real_monitoring_fixture("up")
        self.send_alert(event)
        self.assertEqual(history.recent_history()[0]["stage"], "ignored_inactive")
        self.groq.assert_not_called()
        self.text.assert_not_called()
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def test_real_transport_outage_still_notifies_even_if_pod_has_no_eligible_crash(self):
        event = self.real_monitoring_fixture("down")
        with patch.object(executor, "get_live_restart_evidence", return_value={
                "confirmed": False, "reason": "Pod is healthy; no eligible application crash"}):
            self.send_alert(event)
        self.assertIn("currently firing", str(self.text.call_args))
        self.assertEqual(history.recent_history()[0]["state"]["safe_action"], "manual_review")
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def test_malformed_labels_do_not_crash_the_webhook(self):
        event = alert()
        event["alerts"][0]["labels"]["alertname"] = ["invalid"]
        self.assertEqual(self.send_alert(event).json()["accepted"], 0)
        self.verification.assert_not_called()
        self.groq.assert_not_called()

    def test_gpt_oss_uses_strict_closed_five_field_schema(self):
        self.send_alert()
        call = self.groq.return_value.chat.completions.create.call_args.kwargs
        response_format = call["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertIs(response_format["json_schema"]["strict"], True)
        schema = response_format["json_schema"]["schema"]
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(self.report))
        self.assertEqual(set(schema["properties"]), set(self.report))
        self.assertEqual(schema["properties"]["recommended_action"]["enum"],
                         ["manual_review", "restart_deployment", "no_action"])
        self.assertEqual(call["reasoning_effort"], "low")
        self.assertEqual(call["max_completion_tokens"], 4096)
        self.assertEqual(self.groq.call_args.kwargs["max_retries"], 0)
        self.groq.return_value.close.assert_called_once()

    def test_custom_model_is_preserved_without_unsupported_options(self):
        os.environ["GROQ_MODEL"] = "operator-configured-model"
        self.send_alert()
        call = self.groq.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(call["model"], "operator-configured-model")
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertNotIn("reasoning_effort", call)

    def test_json_validation_failure_retries_only_llm_once_then_requires_approval(self):
        completion = self.groq.return_value.chat.completions.create
        valid = completion.return_value
        completion.side_effect = [self.provider_error(), valid]
        self.send_alert()
        self.assertEqual(completion.call_count, 2)
        self.send.assert_called_once()
        self.assertFalse(self.restarted)
        self.assertEqual(len(history.recent_history()), 1)
        self.assertEqual(history.recent_history()[0]["stage"], "awaiting_approval")
        retry_messages = completion.call_args.kwargs["messages"]
        self.assertEqual(len(retry_messages), 3)
        self.assertNotIn("Never echo this provider payload", json.dumps(retry_messages))
        self.index.assert_not_called()
        self.groq.return_value.close.assert_called_once()

    def test_repeated_json_failure_is_journalled_without_approval_or_index(self):
        completion = self.groq.return_value.chat.completions.create
        completion.side_effect = [self.provider_error(), self.provider_error(nested=False)]
        self.send_alert()
        self.assertEqual(completion.call_count, 2)
        row = history.recent_history()[0]
        self.assertEqual(row["stage"], "ai_failed")
        self.assertEqual(row["state"]["analysis"], "groq_json_validate_failed")
        self.assertIsNone(row["approval_id"])
        self.assertNotIn("test-key-not-real", json.dumps(row))
        self.assertNotIn("Never echo this provider payload", json.dumps(row))
        self.assertIn("groq_json_validate_failed", str(self.text.call_args))
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)
        self.groq.return_value.close.assert_called_once()
        # Redelivery never retries the full failed investigation automatically.
        bridge._seen.clear()
        self.send_alert()
        self.assertEqual(completion.call_count, 2)

    def test_non_json_bad_request_is_not_retried(self):
        completion = self.groq.return_value.chat.completions.create
        completion.side_effect = self.provider_error("context_length_exceeded")
        self.send_alert()
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(history.recent_history()[0]["state"]["analysis"], "groq_context_length_exceeded")
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def test_real_sdk_serializes_strict_schema_and_handles_one_json_retry(self):
        # Real Groq SDK, HTTP replaced at the transport boundary: no network/API.
        requests_sent = []

        def transport(request):
            requests_sent.append(json.loads(request.content))
            if len(requests_sent) == 1:
                return httpx.Response(400, json={"error": {"code": "json_validate_failed",
                    "type": "invalid_request_error", "message": "JSON failed", "failed_generation": ""}})
            return httpx.Response(200, json={"id": "offline-completion", "object": "chat.completion",
                "created": 0, "model": "openai/gpt-oss-20b", "choices": [{"index": 0,
                    "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(self.report)}}]})

        http_client = httpx.Client(transport=httpx.MockTransport(transport))
        sdk = SDKGroq(api_key="offline-placeholder", max_retries=0, http_client=http_client)
        self.groq.side_effect = lambda **kwargs: sdk
        self.send_alert()
        self.assertEqual(len(requests_sent), 2)
        for request in requests_sent:
            self.assertTrue(request["response_format"]["json_schema"]["strict"])
            self.assertEqual(request["reasoning_effort"], "low")
        self.assertTrue(http_client.is_closed)
        self.send.assert_called_once()
        self.assertFalse(self.restarted)
        approval_id = history.recent_history()[0]["approval_id"]
        self.callback(approval_id)
        self.assertTrue(self.restarted)
        self.assertTrue(history.recent_history()[0]["indexed"])
        self.assertEqual(sum("restart" in c for c in self.commands), 1)

    def test_malformed_json_is_not_repaired_or_used_for_approval(self):
        self.groq.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"root_cause":'), finish_reason="stop")])
        self.send_alert()
        self.assertEqual(history.recent_history()[0]["state"]["analysis"], "invalid_ai_report")
        self.assertEqual(self.groq.return_value.chat.completions.create.call_count, 1)
        self.send.assert_not_called()
        self.index.assert_not_called()
        self.assertFalse(self.restarted)

    def test_untrusted_provider_code_cannot_leak_payloads_into_diagnostics(self):
        self.groq.return_value.chat.completions.create.side_effect = self.provider_error("test-key-not-real")
        self.send_alert()
        row = history.recent_history()[0]
        self.assertEqual(row["state"]["analysis"], "groq_bad_request")
        self.assertNotIn("test-key-not-real", json.dumps(row))
        self.assertEqual(self.groq.return_value.chat.completions.create.call_count, 1)
        self.assertFalse(self.restarted)

    def test_empty_or_truncated_completion_fails_closed(self):
        for content, finish, expected in (("", "stop", "groq_empty_response"),
                                           (json.dumps(self.report), "length", "groq_incomplete_response"),
                                           (json.dumps(self.report), "content_filter", "groq_incomplete_response")):
            with self.subTest(expected=expected, finish=finish):
                result = SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=content), finish_reason=finish)])
                self.groq.return_value.chat.completions.create.return_value = result
                report, error = bridge.run_ai_investigation("Read-only output test")
                self.assertIsNone(report)
                self.assertEqual(error, expected)
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def test_refusal_or_no_choices_cannot_be_used_as_a_report(self):
        for choices, expected in (([], "groq_empty_response"),
                                  ([SimpleNamespace(message=SimpleNamespace(
                                      content=json.dumps(self.report), refusal="Refused"), finish_reason="stop")], "groq_refusal")):
            with self.subTest(expected=expected):
                self.groq.return_value.chat.completions.create.return_value = SimpleNamespace(choices=choices)
                report, error = bridge.run_ai_investigation("Read-only output test")
                self.assertIsNone(report)
                self.assertEqual(error, expected)
        self.assertFalse(self.restarted)

    def test_local_bounds_and_extra_field_rejection_remain_enforced(self):
        for report in ({**self.report, "confidence": 1.1},
                       {**self.report, "confidence": "0.9"},
                       {**self.report, "root_cause": "x" * 2001},
                       {**self.report, "root_cause": "  "},
                       {**self.report, "evidence": ["x"] * 13},
                       {**self.report, "shell_command": "untrusted command"}):
            with self.subTest(report_keys=list(report)):
                self.set_report(report)
                analysis, error = bridge.run_ai_investigation("Read-only output test")
                self.assertIsNone(analysis)
                self.assertEqual(error, "invalid_ai_report")
        self.send.assert_not_called()
        self.assertFalse(self.restarted)

    def test_status_displays_safe_failure_code_without_another_ai_call(self):
        self.groq.return_value.chat.completions.create.side_effect = self.provider_error()
        self.send_alert()
        calls = self.groq.return_value.chat.completions.create.call_count
        from opspilot_integration import ai_status
        with patch.object(sys, "argv", ["ai_status"]), patch.object(rag_tools, "get_collection") as collection, \
             patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            collection.return_value.name = "test-collection"
            collection.return_value.count.return_value = 0
            ai_status.main()
        self.assertIn("Failure code: groq_json_validate_failed", output.getvalue())
        self.assertEqual(self.groq.return_value.chat.completions.create.call_count, calls)
        self.assertFalse(self.restarted)

    @unittest.skipUnless(os.getenv("OPSPILOT_NATIVE_RAG_TEST") == "1",
                         "Optional real Chroma test: set OPSPILOT_NATIVE_RAG_TEST=1")
    def test_actual_chroma_persists_and_retrieves_explicit_test_vectors(self):
        # Real Chroma, mocked deterministic vectors: no model download/network.
        from ai_service.app.ingest_runbooks import ingest_runbooks
        vector = [1.0] + [0.0] * 383
        with patch.object(rag_tools, "generate_embedding", return_value=vector), \
             patch("ai_service.app.ingest_runbooks.generate_embedding", return_value=vector):
            ingest_runbooks()
            collection = rag_tools.get_collection()
            self.assertGreaterEqual(collection.count(), 3)
            matches = rag_tools.retrieve_similar_incidents("demo crash", 3)
            self.assertTrue(matches)
            self.assertTrue(all(m["metadata"]["type"] == "runbook" for m in matches))
            rag_tools.store_incident("verified-test", "observed crash", "AI hypothesis", "rollout verified", verified=True)
            self.assertIsNotNone(rag_tools.get_collection().get(ids=["verified-test"])["documents"][0])


if __name__ == "__main__":
    unittest.main()
