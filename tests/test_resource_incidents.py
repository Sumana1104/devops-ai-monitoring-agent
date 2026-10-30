"""Actual LangGraph/SQLite/ASGI with external Kubernetes/Groq/Telegram simulated."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from ai_service.app import ai_agent, incident_history as history, resource_incidents as resource
from ai_service.app import remediation_executor as executor
from api_service.app import approval_store as store, incident_notification as notify
from opspilot_integration import bridge, learning, resource_incidents as monitor
from test_readiness_flow import objects


class Clock:
    def __init__(self): self.value = 0
    def monotonic(self): self.value += 5; return self.value
    def sleep(self, seconds): self.value += seconds
    def time(self): return time.time()


class ResourceIncidentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.use(patch.object(store, "DB_PATH", str(self.directory / "approvals.db")))
        self.use(patch.dict(os.environ, {"OPSPILOT_HISTORY_PATH": str(self.directory / "history.db"),
            "OPSPILOT_DISABLE_WATCHER": "1", resource.FEATURE: "1", "GROQ_API_KEY": "offline-test-key",
            "TELEGRAM_WEBHOOK_SECRET": "offline-webhook", "TELEGRAM_APPROVER_USER_ID": "10", "TELEGRAM_CHAT_ID": "20"}))
        self.data = objects(False)
        self.limit = "256Mi"
        self.set_oom()
        self.commands, self.mutations = [], []
        self.use(patch.object(resource.subprocess, "run", side_effect=self.kubectl))
        self.use(patch.object(ai_agent, "kubectl", side_effect=self.graph_kubectl))
        self.use(patch.object(ai_agent.requests, "get"))
        self.use(patch("requests.sessions.Session.request", side_effect=AssertionError("Real network forbidden")))
        self.send = self.use(patch.object(notify, "send_approval_request", return_value=321))
        self.text = self.use(patch.object(bridge, "telegram_text"))
        self.use(patch.object(bridge, "telegram_api", return_value={"ok": True}))
        self.index = self.use(patch.object(learning, "store_incident"))
        self.refs = self.use(patch.object(ai_agent, "retrieve_similar_incidents", return_value=[{
            "document": "A bounded memory-limit mitigation requires actual OOM evidence and human approval.",
            "metadata": {"source": "opspilot_live_resource_incidents.md", "type": "reviewed_runbook"}, "distance": 0.1}]))
        self.groq = self.use(patch.object(ai_agent, "Groq"))
        self.report = {"root_cause": "Kubernetes confirms OOMKilled; exact cause of memory consumption is unconfirmed.",
            "severity": "high", "confidence": 0.9, "evidence": ["Current unhealthy Pod; OOMKilled/137; configured limit 256Mi"],
            "recommended_action": resource.ACTION}
        self.update_report()
        self.use(patch.object(resource, "time", Clock()))
        self.verify = self.use(patch.object(bridge, "verify_current_alert", return_value={"status": "firing"}))
        self.client = TestClient(bridge.app)
        self.addCleanup(self.client.close)

    def use(self, patcher):
        value = patcher.start(); self.addCleanup(patcher.stop); return value

    def set_oom(self):
        self.data = objects(False)
        for spec in (self.data["deployment"]["spec"]["template"]["spec"], self.data["pod"]["spec"]):
            spec["containers"][0].update({"image": "opspilot-demo:integrated-v1", "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "300m", "memory": self.limit}}})
        self.data["pod"]["spec"]["nodeName"] = "opspilot"
        row = self.data["pod"]["status"]["containerStatuses"][0]
        row.update({"restartCount": 3, "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}})
        self.events = [{"reason": "BackOff", "message": "Back-off restarting failed container",
                        "involvedObject": {"uid": self.data["pod"]["metadata"]["uid"]}}]
        self.node = {"status": {"conditions": [{"type": "MemoryPressure", "status": "False"}]}}

    def set_scheduling(self):
        self.set_oom()
        self.data["pod"]["spec"].pop("nodeName")
        self.data["pod"]["status"] = {"phase": "Pending", "containerStatuses": [], "conditions": [
            {"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}]}
        self.events = [{"reason": "FailedScheduling", "message": "0/1 nodes are available: Insufficient cpu",
                        "involvedObject": {"uid": self.data["pod"]["metadata"]["uid"]}}]

    def update_report(self):
        self.groq.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(self.report)))])

    def kubectl(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(command[:5], ["kubectl", "--context", "opspilot", "-n", "opspilot"])
        args = command[5:]
        if args[0] == "get":
            value = ({"items": [self.data["pod"]]} if args[1] == "pods" else
                     {"items": self.events} if args[1] == "events" else
                     self.node if args[1] == "node" else
                     {"items": self.data["slices"]} if args[1] == "endpointslices" else self.data[args[1]])
            return SimpleNamespace(stdout=json.dumps(value), returncode=0)
        if args[0] == "patch":
            self.assertEqual(args[1:4], ["deployment", "opspilot-demo", "--type=json"])
            changes = json.loads(args[5])
            deployment = self.data["deployment"]
            for change in changes:
                current = deployment
                keys = change["path"].strip("/").split("/")
                for key in keys[:-1]: current = current[int(key)] if isinstance(current, list) else current[key]
                key = int(keys[-1]) if isinstance(current, list) else keys[-1]
                if change["op"] == "test": self.assertEqual(current[key], change["value"])
                else: self.assertEqual(change["op"], "replace"); current[key] = change["value"]
            self.mutations.append(changes)
            deployment["metadata"].update({"generation": 4, "resourceVersion": "101"})
            deployment["status"].update({"observedGeneration": 4, "readyReplicas": 1, "availableReplicas": 1})
            self.data["pod"]["metadata"]["uid"] = "pod-after-approved-rollout"
            self.data["pod"]["spec"]["containers"] = copy.deepcopy(deployment["spec"]["template"]["spec"]["containers"])
            self.data["pod"]["status"].update({"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]})
            self.data["pod"]["status"]["containerStatuses"][0].update({"ready": True, "restartCount": 0,
                "containerID": "containerd://new-container", "state": {"running": {}}, "lastState": {}})
            self.data["slices"][0]["endpoints"][0].update({"targetRef": {"kind": "Pod", "uid": "pod-after-approved-rollout"}, "conditions": {"ready": True}})
            return SimpleNamespace(stdout="patched", returncode=0)
        if args[0] == "exec":
            self.assertEqual(args[2:7], ["-c", "opspilot-demo", "--", "python", "-c"])
            self.assertEqual(args[7], resource.SERVICE_SCRIPT)
            return SimpleNamespace(stdout=json.dumps({"service_http_status": 200}), returncode=0)
        raise AssertionError("Unexpected Kubernetes command " + str(args))

    def graph_kubectl(self, *args):
        if args[:2] == ("get", "pods"): return json.dumps({"items": [self.data["pod"]]})
        if args[:2] == ("get", "events"): return json.dumps({"items": self.events})
        if args[0] == "logs": return "Previous container terminated; no application-level cause proven."
        raise AssertionError("Unexpected graph command")

    def process(self):
        kind = "OpsPilotDemoFailedScheduling" if self.data["pod"]["status"]["phase"] == "Pending" else "OpsPilotDemoOOMKilled"
        return monitor.process_current_incident(alert={"status": "firing", "labels": {"alertname": kind, "service": "opspilot-demo"}, "startsAt": "offline-fixture"})

    def approved(self):
        evidence = resource.inspect_incident()
        self.assertTrue(evidence.get("repairable"), evidence)
        result = notify.request_oom_approval("Offline test", "oomkilled", evidence)
        self.assertTrue(store.decide_approval(result["incident_id"], "approve"))
        return result["incident_id"]

    def callback(self, incident_id, decision="approve", user=10):
        return self.client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "offline-webhook"}, json={
            "callback_query": {"id": "offline-callback", "from": {"id": user}, "message": {"chat": {"id": 20}},
                               "data": decision + ":" + incident_id}})

    def test_real_graph_and_authenticated_approval_patch_one_field_and_verify_service(self):
        pending = self.process()
        self.assertEqual(pending["status"], "awaiting_approval")
        self.assertEqual(self.mutations, [])
        self.callback(pending["incident_id"])
        row = history.get_investigation(pending["investigation_id"])
        self.assertEqual(row["outcome"]["status"], "recovered")
        self.assertEqual(row["indexed"], 1)
        self.index.assert_called_once()
        changes = [c for c in self.mutations[0] if c["op"] != "test"]
        self.assertEqual(changes, [{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "512Mi"}])
        self.assertTrue(row["outcome"]["recovery"]["service_http_verified"])
        self.assertGreaterEqual(row["outcome"]["recovery"]["stable_seconds"], 30)

    def test_failed_scheduling_uses_current_uid_event_and_forces_manual_even_if_ai_asks_for_restart(self):
        self.set_scheduling()
        self.report["recommended_action"] = "restart_deployment"; self.update_report()
        result = self.process()
        self.assertEqual(result["status"], "manual_review")
        self.send.assert_not_called(); self.assertEqual(self.mutations, [])
        row = history.get_investigation(result["investigation_id"])
        self.assertEqual(row["state"]["safe_action"], "manual_review")
        self.assertIn("Insufficient cpu", row["state"]["pod_events"])

    def test_repeated_same_pod_incident_does_not_repeat_ai_or_approval(self):
        first = self.process()
        second = self.process()
        self.assertEqual(first["status"], "awaiting_approval")
        self.assertEqual(second["status"], "already_investigated")
        self.send.assert_called_once(); self.groq.assert_called_once()

    def test_uncertain_delivery_is_not_retried(self):
        self.send.side_effect = RuntimeError("Delivery unknown")
        with self.assertRaises(RuntimeError): self.process()
        self.assertEqual(self.process()["status"], "already_investigated")
        self.send.assert_called_once(); self.assertEqual(self.mutations, [])

    def test_exit_137_without_oom_reason_is_not_confirmed(self):
        self.data["pod"]["status"]["containerStatuses"][0]["lastState"]["terminated"]["reason"] = "Error"
        self.assertFalse(resource.inspect_incident()["confirmed"])

    def test_old_oom_during_different_current_fault_is_not_confirmed(self):
        self.data["pod"]["status"]["containerStatuses"][0]["state"] = {"waiting": {"reason": "ImagePullBackOff"}}
        self.assertFalse(resource.inspect_incident()["confirmed"])

    def test_current_ready_pod_with_old_oom_is_not_an_incident(self):
        self.data["pod"]["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        self.assertFalse(resource.inspect_incident()["confirmed"])

    def test_node_pressure_unknown_or_true_blocks_memory_proposal(self):
        for pressure in ("True", "Unknown"):
            self.node["status"]["conditions"][0]["status"] = pressure
            self.assertFalse(resource.inspect_incident()["repairable"])

    def test_fixed_memory_steps_and_ceiling(self):
        for old, new in (("64Mi", "128Mi"), ("128Mi", "256Mi"), ("256Mi", "512Mi"), ("512Mi", None), ("1Gi", None)):
            self.limit = old; self.set_oom()
            live = resource.inspect_incident()
            self.assertEqual(live["proposed_memory_limit"], new)
            self.assertEqual(live["repairable"], new is not None)

    def test_foreign_pod_owner_is_not_an_incident(self):
        self.data["replicaset"]["metadata"]["ownerReferences"][0]["uid"] = "foreign-owner"
        self.assertFalse(resource.inspect_incident()["confirmed"])

    def test_pending_without_matching_failed_scheduling_event_is_not_confirmed(self):
        self.set_scheduling(); self.events[0]["involvedObject"]["uid"] = "old-pod"
        self.assertFalse(resource.inspect_incident()["confirmed"])

    def test_dry_run_does_not_consume_approval(self):
        incident = self.approved()
        self.assertEqual(executor.execute_approved_remediation(incident, execute=False)["status"], "dry_run_passed")
        self.assertEqual(store.get_approval(incident)["status"], "approved"); self.assertEqual(self.mutations, [])

    def test_disabled_feature_blocks_patch(self):
        incident = self.approved()
        with patch.dict(os.environ, {resource.FEATURE: "0"}):
            self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(self.mutations, [])

    def test_expired_oom_approval_cannot_execute(self):
        incident = self.process()["incident_id"]
        with store.connect() as db: db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (incident,))
        self.callback(incident)
        self.assertEqual(self.mutations, [])

    def test_rejected_oom_approval_cannot_execute(self):
        incident = self.process()["incident_id"]
        self.callback(incident, "reject")
        self.assertEqual(self.mutations, []); self.index.assert_not_called()

    def test_wrong_user_cannot_approve_oom(self):
        incident = self.process()["incident_id"]
        self.callback(incident, user=999)
        self.assertEqual(store.get_approval(incident)["status"], "pending")
        self.assertEqual(self.mutations, [])

    def test_changed_service_identity_after_approval_blocks_patch(self):
        incident = self.approved(); self.data["service"]["metadata"]["uid"] = "replacement-service"
        self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(self.mutations, [])

    def test_http_failure_after_patch_cannot_be_verified_recovery(self):
        incident = self.approved()
        original = resource.run
        def fail_http(*args, **kwargs):
            if args[0] == "exec": raise subprocess.CalledProcessError(1, ["kubectl"])
            return original(*args, **kwargs)
        with patch.object(resource, "run", side_effect=fail_http):
            result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovery_not_verified")

    def test_changed_pod_after_approval_blocks_patch(self):
        incident = self.approved(); self.data["pod"]["metadata"]["uid"] = "replacement-pod"
        self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(self.mutations, [])

    def test_changed_evidence_after_claim_blocks_patch(self):
        incident = self.approved(); live = resource.inspect_incident()
        with patch.object(resource, "inspect_incident", side_effect=[live, {**live, "generation": 999}]):
            self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(store.get_approval(incident)["status"], "claimed"); self.assertEqual(self.mutations, [])

    def test_normal_restart_count_increase_is_allowed_decrease_is_blocked(self):
        stored = resource.inspect_incident()
        self.assertTrue(resource.matches(stored, {**stored, "restart_count": stored["restart_count"] + 1}))
        self.assertFalse(resource.matches(stored, {**stored, "restart_count": 0}))
        self.assertFalse(resource.matches(stored, {**stored, "restart_count": True}))

    def test_patch_failure_cannot_be_reported_or_indexed_as_recovered(self):
        incident = self.approved()
        original = resource.run
        def fail_patch(*args, **kwargs):
            if args[0] == "patch": raise subprocess.CalledProcessError(1, ["kubectl"])
            return original(*args, **kwargs)
        with patch.object(resource, "run", side_effect=fail_patch):
            result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "execution_failed"); self.assertEqual(self.mutations, [])
        self.index.assert_not_called()

    def test_recovery_failure_is_not_success(self):
        incident = self.approved()
        with patch.object(resource, "verify_recovery", return_value={"recovered": False}):
            result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovery_not_verified")

    def test_duplicate_execution_claim_is_blocked(self):
        incident = self.approved()
        self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "recovered")
        self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(len(self.mutations), 1)

    def test_memory_action_cannot_be_created_or_claimed_for_scheduling(self):
        with self.assertRaises(ValueError): store.create_approval(resource.ACTION, incident_type="failed_scheduling")
        incident = self.approved()
        with store.connect() as db: db.execute("UPDATE approvals SET incident_type='failed_scheduling' WHERE incident_id=?", (incident,))
        self.assertIsNone(store.claim_approved_action(incident))

    def test_missing_rag_low_confidence_and_invented_target_are_manual(self):
        state = {"analysis": json.dumps(self.report), "resource_incident_kind": "oomkilled", "rag_status": "ready",
                 "resource_evidence": resource.inspect_incident(), "live_pods": [{"pod": "another", "uid": "another"}], "steps": []}
        self.assertEqual(ai_agent.select_action(state)["safe_action"], "manual_review")
        state["live_pods"] = [{"pod": state["resource_evidence"]["pod"], "uid": state["resource_evidence"]["pod_uid"]}]
        state["rag_status"] = "unavailable"
        self.assertEqual(ai_agent.select_action(state)["safe_action"], "manual_review")
        state["rag_status"] = "ready"; state["analysis"] = json.dumps({**self.report, "confidence": 0.4})
        self.assertEqual(ai_agent.select_action(state)["safe_action"], "manual_review")
