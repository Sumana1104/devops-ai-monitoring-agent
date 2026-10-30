"""Offline readiness safety tests: real SQLite/LangGraph/ASGI, mocked external I/O."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPSPILOT_DISABLE_WATCHER", "1")
from fastapi.testclient import TestClient
from ai_service.app import readiness_remediation as repair, remediation_executor as executor
from ai_service.app import ai_agent, incident_history as history
from api_service.app import approval_store as store, incident_notification as notify
from opspilot_integration import bridge, learning, readiness_demo, operator
from opspilot_integration.demo import app as demo


def objects(ready=False):
    container = {"name": "opspilot-demo", "image": repair.IMAGE,
                 "ports": [{"name": "http", "containerPort": 8080}],
                 "volumeMounts": [{"name": "state", "mountPath": "/runtime"}],
                 "readinessProbe": {"httpGet": {"path": "/readyz", "port": "http"}},
                 "livenessProbe": {"httpGet": {"path": "/healthz", "port": "http"}}}
    podspec = {"containers": [container], "volumes": [{"name": "state", "emptyDir": {}}]}
    deployment = {"metadata": {"name": "opspilot-demo", "namespace": "opspilot", "uid": "deployment-1",
                                "generation": 3, "resourceVersion": "100"},
                  "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                           "selector": {"matchLabels": {"app": "opspilot-demo"}},
                           "template": {"spec": podspec}},
                  "status": {"observedGeneration": 3, "updatedReplicas": 1,
                             "readyReplicas": int(ready), "availableReplicas": int(ready)}}
    pod = {"metadata": {"name": "opspilot-demo-rs-pod", "uid": "pod-1", "namespace": "opspilot",
                        "ownerReferences": [{"kind": "ReplicaSet", "name": "opspilot-demo-rs", "uid": "rs-1", "controller": True}]},
           "spec": copy.deepcopy(podspec), "status": {"phase": "Running",
               "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
               "containerStatuses": [{"name": "opspilot-demo", "containerID": "containerd://container-1",
                   "ready": ready, "restartCount": 0, "state": {"running": {"startedAt": "2026-10-29T12:00:00Z"}}, "lastState": {}}]}}
    rs = {"metadata": {"name": "opspilot-demo-rs", "uid": "rs-1", "ownerReferences": [
        {"kind": "Deployment", "name": "opspilot-demo", "uid": "deployment-1", "controller": True}]}}
    service = {"metadata": {"name": "opspilot-demo", "namespace": "opspilot", "uid": "service-1"},
               "spec": {"selector": {"app": "opspilot-demo"}, "ports": [{"port": 8080, "targetPort": "http"}]}}
    slices = [{"metadata": {"ownerReferences": [{"kind": "Service", "uid": "service-1"}]}, "endpoints": [
        {"targetRef": {"kind": "Pod", "uid": "pod-1"}, "conditions": {"ready": ready}}]}]
    return {"deployment": deployment, "pod": pod, "replicaset": rs, "service": service, "slices": slices}


class ReadinessFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.install(patch.object(store, "DB_PATH", str(self.directory / "approvals.db")))
        self.install(patch.dict(os.environ, {
            "OPSPILOT_HISTORY_PATH": str(self.directory / "history.sqlite3"),
            "OPSPILOT_DISABLE_WATCHER": "1", repair.FEATURE: "1", "OPSPILOT_ENABLE_DEMO_RESTART": "0",
            "GROQ_API_KEY": "offline-not-real", "TELEGRAM_WEBHOOK_SECRET": "offline-secret",
            "TELEGRAM_APPROVER_USER_ID": "10", "TELEGRAM_CHAT_ID": "20"}))
        self.data = objects()
        self.fault = "a" * 32
        self.checks = {"fault_id": self.fault, "liveness_status": 200, "liveness_ok": True, "readiness_status": 503}
        self.mutations = []
        self.commands = []
        self.command = self.install(patch.object(repair.subprocess, "run", side_effect=self.kubectl))
        self.install(patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected external HTTP")))
        self.send = self.install(patch.object(notify, "send_approval_request", return_value=123))
        self.text = self.install(patch.object(bridge, "telegram_text"))
        self.install(patch.object(bridge, "telegram_api", return_value={"ok": True}))
        self.index = self.install(patch.object(learning, "store_incident"))
        self.install(patch.object(bridge, "alert_token", return_value="offline-alert"))
        self.verify = self.install(patch.object(bridge, "verify_current_alert", return_value={"status": "firing"}))
        self.install(patch.object(ai_agent, "kubectl", side_effect=self.graph_kubectl))
        self.install(patch.object(ai_agent.requests, "get"))
        self.refs = self.install(patch.object(ai_agent, "retrieve_similar_incidents", return_value=[{
            "document": "The controlled readiness.flag makes /readyz 503, but /healthz 200. Human approval may clear only this flag.",
            "metadata": {"type": "runbook", "source": "opspilot_readiness_flag.md"}, "distance": 0.1}]))
        self.groq = self.install(patch.object(ai_agent, "Groq"))
        self.report = {"root_cause": "Current controlled readiness flag observed, with HTTP 503/readiness and liveness 200",
                       "severity": "high", "confidence": 0.9,
                       "evidence": ["Live Pod NotReady; Service endpoint ready=false; flag identity verified"],
                       "recommended_action": repair.ACTION}
        self.set_report()
        self.client = TestClient(bridge.app)
        self.addCleanup(self.client.close)
        bridge._seen.clear()

    def install(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def set_report(self):
        self.groq.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(self.report)))])

    def kubectl(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(command[:3], ["kubectl", "--context", "opspilot"])
        args = list(command[3:])
        index = args.index("-n")
        self.assertEqual(args[index + 1], "opspilot")
        del args[index:index + 2]
        if args[0] == "get":
            name = args[1]
            result = ({"items": [self.data["pod"]]} if name == "pods" else
                      {"items": self.data["slices"]} if name == "endpointslices" else self.data[name])
            return SimpleNamespace(stdout=json.dumps(result), returncode=0)
        if args[:2] == ["rollout", "status"]:
            return SimpleNamespace(stdout="deployment successfully rolled out", returncode=0)
        if args[0] == "exec":
            self.assertEqual(args[1], self.data["pod"]["metadata"]["name"])
            self.assertEqual(args[2:7], ["-c", "opspilot-demo", "--", "python", "-c"])
            script = args[7]
            if script == repair.PROBE_SCRIPT:
                return SimpleNamespace(stdout=json.dumps(self.checks), returncode=0)
            if script == repair.REPAIR_SCRIPT:
                self.assertEqual(args[8], self.fault)
                self.mutations.append(args)
                self.checks.update({"fault_id": None, "readiness_status": 200})
                self.data = objects(ready=True)
                return SimpleNamespace(stdout="Known demo flag cleared", returncode=0)
            if script == repair.SERVICE_SCRIPT:
                return SimpleNamespace(stdout="Service HTTP 200 verified", returncode=0)
        raise AssertionError("Unexpected or mutating Kubernetes command: " + str(args))

    def graph_kubectl(self, *args):
        if args[:2] == ("get", "pods"):
            return json.dumps({"items": [self.data["pod"]]})
        if args[0] == "logs":
            return "GET /readyz 503; GET /healthz 200"
        if args[:2] == ("get", "events"):
            return json.dumps({"items": [{"type": "Warning", "reason": "Unhealthy", "message": "Readiness probe failed: HTTP 503"}]})
        raise AssertionError("Unexpected graph command")

    def event(self):
        return {"status": "firing", "labels": {"alertname": "OpsPilotDemoReadinessFailure", "service": "opspilot-demo"},
                "startsAt": "2026-10-29T12:30:00Z", "fingerprint": "offline-readiness-event"}

    def alert(self):
        response = self.client.post("/alerts", headers={"Authorization": "Bearer offline-alert"},
                                    json={"receiver": "local-opspilot", "alerts": [self.event()]})
        self.assertEqual(response.status_code, 200)
        return history.recent_history()[0]

    def callback(self, incident_id, decision="approve", user=10):
        return self.client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "offline-secret"}, json={
            "callback_query": {"id": "offline-callback", "from": {"id": user},
                "message": {"chat": {"id": 20}}, "data": decision + ":" + incident_id}})

    def approved(self):
        evidence = repair.get_live_readiness_evidence()
        self.assertTrue(evidence["confirmed"])
        incident = store.create_approval(repair.ACTION, incident_type=repair.INCIDENT_TYPE, incident_evidence=evidence)
        self.assertTrue(store.decide_approval(incident, "approve"))
        return incident

    def test_live_fault_requires_running_notready_http_and_service_evidence(self):
        evidence = repair.get_live_readiness_evidence()
        self.assertTrue(evidence["confirmed"])
        self.assertEqual(evidence["pod_uid"], "pod-1")
        self.assertEqual(evidence["ready_service_endpoints"], 0)
        self.assertEqual(self.mutations, [])

    def test_wrong_profile_or_probe_or_volume_is_never_repaired(self):
        for change in ("image", "namespace", "replicas", "readinessProbe", "livenessProbe", "volume"):
            with self.subTest(change=change):
                self.data = objects()
                deployment = self.data["deployment"]
                container = deployment["spec"]["template"]["spec"]["containers"][0]
                if change == "image": container["image"] = "nginx:alpine"
                elif change == "namespace": deployment["metadata"]["namespace"] = "production"
                elif change == "replicas": deployment["spec"]["replicas"] = 2
                elif change == "volume": deployment["spec"]["template"]["spec"]["volumes"] = []
                else: container[change]["httpGet"]["path"] = "/wrong"
                self.assertFalse(repair.get_live_readiness_evidence()["confirmed"])
        self.assertEqual(self.mutations, [])

    def test_pod_replacement_deletion_owner_or_crash_is_not_readiness_evidence(self):
        for change in ("owner", "deleting", "crash", "ready", "counter"):
            with self.subTest(change=change):
                self.data = objects()
                pod = self.data["pod"]
                if change == "owner": self.data["replicaset"]["metadata"]["uid"] = "another"
                elif change == "deleting": pod["metadata"]["deletionTimestamp"] = "now"
                elif change == "crash": pod["status"]["containerStatuses"][0]["state"] = {"waiting": {"reason": "CrashLoopBackOff"}}
                elif change == "ready": pod["status"]["containerStatuses"][0]["ready"] = True
                else: pod["status"]["containerStatuses"][0]["restartCount"] = True
                self.assertFalse(repair.get_live_readiness_evidence()["confirmed"])

    def test_unhealthy_liveness_unproven_flag_or_healthy_readiness_is_blocked(self):
        for change in ({"liveness_status": 503}, {"liveness_ok": False}, {"readiness_status": 200},
                       {"fault_id": None}, {"fault_id": "not-a-fault"}):
            with self.subTest(change=change):
                self.checks = {"fault_id": self.fault, "liveness_status": 200, "liveness_ok": True,
                               "readiness_status": 503, **change}
                self.assertFalse(repair.get_live_readiness_evidence()["confirmed"])

    def test_ready_endpoints_wrong_service_or_notready_publication_blocks(self):
        for change in ("ready_endpoint", "selector", "publish"):
            with self.subTest(change=change):
                self.data = objects()
                if change == "ready_endpoint": self.data["slices"][0]["endpoints"][0]["conditions"]["ready"] = True
                elif change == "selector": self.data["service"]["spec"]["selector"] = {"app": "other"}
                else: self.data["service"]["spec"]["publishNotReadyAddresses"] = True
                self.assertFalse(repair.get_live_readiness_evidence()["confirmed"])

    def test_unknown_kubernetes_state_is_not_confirmed(self):
        self.command.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
        self.assertFalse(repair.get_live_readiness_evidence()["confirmed"])

    def test_dry_run_keeps_flag_and_does_not_claim_approval(self):
        incident = self.approved()
        self.assertEqual(executor.execute_approved_remediation(incident)["status"], "dry_run_passed")
        self.assertEqual(store.get_approval(incident)["status"], "approved")
        self.assertEqual(self.mutations, [])

    def test_readiness_feature_gate_is_independent_of_crash_gate(self):
        incident = self.approved()
        with patch.dict(os.environ, {repair.FEATURE: "0", "OPSPILOT_ENABLE_DEMO_RESTART": "1"}):
            self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(store.get_approval(incident)["status"], "approved")
        self.assertEqual(self.mutations, [])

    def test_success_claims_once_clears_only_known_flag_and_verifies_service(self):
        incident = self.approved()
        result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovered")
        self.assertTrue(result["repair_command_succeeded"])
        self.assertFalse(result["restart_command_succeeded"])
        self.assertTrue(result["recovery"]["service_http_verified"])
        self.assertEqual(store.get_approval(incident)["status"], "claimed")
        self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(len(self.mutations), 1)
        self.assertFalse(any("restart" in c or "delete" in c or "patch" in c for c in self.commands))

    def test_expired_rejected_or_pending_approval_cannot_execute(self):
        for status in ("pending", "rejected", "expired"):
            with self.subTest(status=status):
                incident = self.approved()
                with store.connect() as db:
                    if status == "expired": db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (incident,))
                    else: db.execute("UPDATE approvals SET status=? WHERE incident_id=?", (status, incident))
                self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(self.mutations, [])

    def test_changed_fault_or_container_cannot_use_old_approval(self):
        for change in ("fault", "container", "generation"):
            with self.subTest(change=change):
                self.data, self.checks["fault_id"] = objects(), self.fault
                incident = self.approved()
                if change == "fault": self.checks["fault_id"] = "b" * 32
                elif change == "container": self.data["pod"]["status"]["containerStatuses"][0]["containerID"] = "replacement"
                else:
                    self.data["deployment"]["metadata"]["generation"] = 4
                    self.data["deployment"]["status"]["observedGeneration"] = 4
                self.assertEqual(executor.execute_approved_remediation(incident, execute=True)["status"], "blocked")
        self.assertEqual(self.mutations, [])

    def test_changed_evidence_after_claim_still_blocks_execution(self):
        incident = self.approved()
        live = repair.get_live_readiness_evidence()
        with patch.object(repair, "get_live_readiness_evidence", side_effect=[live, {**live, "fault_id": "b" * 32}]):
            result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(store.get_approval(incident)["status"], "claimed")
        self.assertEqual(self.mutations, [])

    def test_failed_service_check_is_not_verified_recovery(self):
        incident = self.approved()
        original = self.kubectl
        def fail_service(command, **kwargs):
            if repair.SERVICE_SCRIPT in command: raise subprocess.CalledProcessError(1, command)
            return original(command, **kwargs)
        self.command.side_effect = fail_service
        result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovery_not_verified")
        self.assertTrue(result["repair_command_succeeded"])
        self.assertFalse(result["recovery"]["service_http_verified"])

    def test_store_cannot_claim_mismatched_readiness_incident_type(self):
        with self.assertRaises(ValueError):
            store.create_approval(repair.ACTION, incident_type="application_failure", incident_evidence={})
        incident = self.approved()
        with store.connect() as db:
            db.execute("UPDATE approvals SET incident_type='application_failure' WHERE incident_id=?", (incident,))
        self.assertIsNone(store.claim_approved_action(incident))

    def test_full_real_graph_alert_requires_human_and_indexes_verified_recovery(self):
        row = self.alert()
        self.assertEqual(row["state"]["safe_action"], repair.ACTION)
        self.assertEqual(store.get_approval(row["approval_id"])["status"], "pending")
        self.assertEqual(self.mutations, [])
        self.index.assert_not_called()
        self.assertEqual(self.callback(row["approval_id"]).status_code, 200)
        after = history.get_investigation(row["investigation_id"])
        self.assertEqual(after["outcome"]["status"], "recovered")
        self.assertTrue(after["outcome"]["recovery"]["service_http_verified"])
        self.assertTrue(after["indexed"])
        self.assertEqual(len(self.mutations), 1)
        self.index.assert_called_once()

    def test_reject_changes_no_kubernetes_and_is_not_indexed(self):
        row = self.alert()
        self.callback(row["approval_id"], "reject")
        self.assertEqual(store.get_approval(row["approval_id"])["status"], "rejected")
        self.assertEqual(self.mutations, [])
        self.index.assert_not_called()

    def test_wrong_telegram_user_cannot_approve(self):
        row = self.alert()
        self.assertEqual(self.callback(row["approval_id"], user=99).json()["status"], "unauthorized_approver")
        self.assertEqual(store.get_approval(row["approval_id"])["status"], "pending")
        self.assertEqual(self.mutations, [])

    def test_safe_bridge_approval_performs_dry_run_only(self):
        row = self.alert()
        with patch.dict(os.environ, {repair.FEATURE: "0"}):
            self.callback(row["approval_id"])
        self.assertEqual(history.get_investigation(row["investigation_id"])["outcome"]["status"], "dry_run_passed")
        self.assertEqual(self.mutations, [])
        self.index.assert_not_called()

    def test_same_fault_receives_one_automatic_approval_even_with_new_delivery_id(self):
        self.alert()
        other = {**self.event(), "startsAt": "2026-10-29T12:35:00Z"}
        bridge.process_alert(other)
        self.send.assert_called_once()
        self.assertEqual(self.mutations, [])

    def test_uncertain_telegram_delivery_is_not_retried_automatically(self):
        self.send.side_effect = RuntimeError("Offline uncertain Telegram delivery")
        self.alert()
        bridge.process_alert({**self.event(), "startsAt": "2026-10-29T12:35:00Z"})
        self.send.assert_called_once()
        self.assertEqual(self.mutations, [])

    def test_no_rag_low_confidence_or_manual_review_never_gets_approval(self):
        for case in ("no_rag", "low_confidence", "manual_review"):
            with self.subTest(case=case):
                self.report["confidence"] = 0.4 if case == "low_confidence" else 0.9
                self.report["recommended_action"] = "manual_review" if case == "manual_review" else repair.ACTION
                self.set_report()
                if case == "no_rag": self.refs.return_value = []
                else: self.refs.return_value = [{"document": "Controlled readiness flag", "metadata": {"source": "runbook"}, "distance": 0.1}]
                bridge.process_alert({**self.event(), "fingerprint": case})
        self.send.assert_not_called()
        self.assertEqual(self.mutations, [])

    def test_repair_success_without_service_proof_is_never_indexed(self):
        row = self.alert()
        candidate = history.get_investigation(row["investigation_id"])
        candidate["outcome"] = {"status": "recovered", "action": repair.ACTION, "repair_command_succeeded": True,
                                "recovery": {"recovered": True, "service_http_verified": False}}
        self.assertFalse(learning.index_recovered_investigation(candidate))
        self.index.assert_not_called()

    def test_expired_readiness_request_can_be_reinvestigated_without_repair(self):
        row = self.alert()
        prior = row["approval_id"]
        with store.connect() as db:
            db.execute("UPDATE approvals SET expires_at=0 WHERE incident_id=?", (prior,))
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "offline-bot-not-real",
                                     "TELEGRAM_WEBHOOK_SECRET": "offline-secret"}):
            from opspilot_integration import telegram_webhook as webhook
        with patch.object(operator.requests, "get") as http, \
             patch.object(webhook, "call", return_value={"url": "https://fixture-readiness.trycloudflare.com/telegram/webhook"}), \
             patch.object(webhook, "check_route"), redirect_stdout(io.StringIO()):
            http.return_value.json.return_value = {"local_bridge": True, "runtime_version": bridge.RUNTIME_VERSION,
                "demo_restart_enabled": False, "demo_readiness_repair_enabled": True}
            fresh = operator.retry_approval(prior, confirmed=True)
        self.assertNotEqual(prior, fresh["incident_id"])
        self.assertEqual(store.get_approval(prior)["expires_at"], 0)
        self.assertEqual(store.get_approval(fresh["incident_id"])["status"], "pending")
        self.assertEqual(self.mutations, [])

    def test_replaced_pod_cannot_be_credited_as_flag_recovery(self):
        incident = self.approved()
        original = self.kubectl
        def replace_during_repair(command, **kwargs):
            result = original(command, **kwargs)
            if repair.REPAIR_SCRIPT in command:
                self.data["pod"]["metadata"]["uid"] = "pod-2"
                self.data["slices"][0]["endpoints"][0]["targetRef"]["uid"] = "pod-2"
            return result
        self.command.side_effect = replace_during_repair
        result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovery_not_verified")

    def test_invalid_evidence_cannot_create_readiness_approval(self):
        evidence = repair.get_live_readiness_evidence()
        with self.assertRaises(ValueError):
            notify.request_incident_approval(repair.ACTION, "offline", repair.INCIDENT_TYPE,
                                             {**evidence, "fault_id": "b" * 32})
        self.send.assert_not_called()

    def test_lost_kubernetes_connection_after_repair_is_not_recovery(self):
        incident = self.approved()
        with patch.object(executor, "verify_recovery", return_value={"recovered": False, "reason": "API unavailable"}):
            result = executor.execute_approved_remediation(incident, execute=True)
        self.assertEqual(result["status"], "recovery_not_verified")
        self.assertTrue(result["repair_command_succeeded"])
        self.assertFalse(result["recovery"]["service_http_verified"])


class ReadinessScriptTests(unittest.TestCase):
    def test_injection_is_exclusive_and_repair_checks_fault_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            flag = Path(directory) / "readiness.flag"
            with patch("pathlib.Path", return_value=flag), patch.object(sys, "argv", ["script", "a" * 32]), redirect_stdout(io.StringIO()):
                exec(compile(readiness_demo.INJECT_SCRIPT, "inject", "exec"), {})
                with self.assertRaises(FileExistsError):
                    exec(compile(readiness_demo.INJECT_SCRIPT, "inject", "exec"), {})
            self.assertEqual(flag.read_text(), "a" * 32)
            with patch("pathlib.Path", return_value=flag), patch.object(sys, "argv", ["script", "b" * 32]), self.assertRaises(SystemExit):
                exec(compile(repair.REPAIR_SCRIPT, "repair", "exec"), {})
            self.assertTrue(flag.exists())
            with patch("pathlib.Path", return_value=flag), patch.object(sys, "argv", ["script", "a" * 32]), redirect_stdout(io.StringIO()):
                exec(compile(repair.REPAIR_SCRIPT, "repair", "exec"), {})
            self.assertFalse(flag.exists())

    def test_repair_refuses_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "protected"
            target.write_text("a" * 32)
            flag = Path(directory) / "readiness.flag"
            flag.symlink_to(target)
            with patch("pathlib.Path", return_value=flag), patch.object(sys, "argv", ["script", "a" * 32]), self.assertRaises(SystemExit):
                exec(compile(repair.REPAIR_SCRIPT, "repair", "exec"), {})
            self.assertEqual(target.read_text(), "a" * 32)
            self.assertTrue(flag.is_symlink())

    def test_all_fixed_scripts_compile_without_running_them(self):
        for script in (repair.PROBE_SCRIPT, repair.REPAIR_SCRIPT, repair.SERVICE_SCRIPT, readiness_demo.INJECT_SCRIPT):
            compile(script, "fixed-demo-script", "exec")


class ReadinessSetupTests(unittest.TestCase):
    def test_prepare_and_inject_require_explicit_confirmation(self):
        with patch.object(readiness_demo, "bridge_health") as health:
            for function in (readiness_demo.prepare, readiness_demo.inject):
                with self.assertRaises(ValueError):
                    function()
            health.assert_not_called()

    def test_wrong_local_image_is_not_replaced_or_built(self):
        deployment = objects()["deployment"]
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "nginx:alpine"
        with patch.object(readiness_demo, "bridge_health"), patch.object(repair, "get", return_value=deployment), \
             patch.object(repair, "snapshot", side_effect=ValueError("Unexpected demo image")), \
             patch.object(readiness_demo.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                readiness_demo.prepare(confirmed=True)
            command.assert_not_called()

    def test_prepare_patch_changes_only_image_and_readiness_path(self):
        data = objects(ready=True)
        data["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = "opspilot-demo:integrated-v1"
        with patch.object(readiness_demo, "bridge_health"), patch.object(repair, "get", return_value=data["deployment"]), \
             patch.object(repair, "snapshot", return_value=tuple(data[k] for k in ("deployment", "pod", "pod", "service"))), \
             patch.object(repair, "run") as kubectl, patch.object(readiness_demo.subprocess, "run") as command, \
             redirect_stdout(io.StringIO()):
            readiness_demo.prepare(confirmed=True)
        patch_command = next(c.args for c in kubectl.call_args_list if c.args[0] == "patch")
        operations = json.loads(patch_command[-1])
        changes = [o for o in operations if o["op"] != "test"]
        self.assertEqual({o["path"] for o in changes}, {
            "/spec/template/spec/containers/0/image", "/spec/template/spec/containers/0/readinessProbe/httpGet/path"})
        self.assertTrue(all(o["op"] == "replace" for o in changes))
        self.assertTrue(any(o["path"] == "/metadata/resourceVersion" and o["op"] == "test" for o in operations))
        self.assertTrue(any(c.args[0][-2:] == ["--force-recreate", "prometheus"] for c in command.call_args_list))

    def test_unreachable_callback_stops_before_fault_or_ai(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "offline-bot-not-real",
                                     "TELEGRAM_WEBHOOK_SECRET": "offline-secret"}):
            from opspilot_integration import telegram_webhook as webhook
        with patch.object(readiness_demo, "bridge_health", return_value={"demo_readiness_repair_enabled": False}), \
             patch.object(webhook, "call", return_value={"url": "https://fixture-readiness.trycloudflare.com/telegram/webhook"}), \
             patch.object(webhook, "check_route", side_effect=ValueError("Callback unavailable")), \
             patch.object(repair, "run") as command:
            with self.assertRaises(ValueError):
                readiness_demo.inject(confirmed=True)
            command.assert_not_called()


class ReadinessAppTests(unittest.TestCase):
    def test_flag_fails_only_readiness_and_exposes_real_metric(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(demo, "READINESS_FLAG", Path(directory) / "readiness.flag"):
            with TestClient(demo.app) as client:
                self.assertEqual(client.get("/readyz").status_code, 200)
                self.assertIn("opspilot_demo_ready 1.0", client.get("/metrics").text)
                demo.READINESS_FLAG.write_text("a" * 32)
                self.assertEqual(client.get("/readyz").status_code, 503)
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.get("/").status_code, 200)
                metrics = client.get("/metrics")
                self.assertEqual(metrics.status_code, 200)
                self.assertIn("opspilot_demo_ready 0.0", metrics.text)
                demo.READINESS_FLAG.unlink()
                self.assertEqual(client.get("/readyz").status_code, 200)
                self.assertIn("opspilot_demo_ready 1.0", client.get("/metrics").text)


if __name__ == "__main__":
    unittest.main()
