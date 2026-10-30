"""Offline V1 contracts: actual ASGI/graph/SQLite; no external services/faults."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from ai_service.app import resource_incidents as resource, incident_history as history
from opspilot_integration import bridge, kubernetes_metrics as metrics, scenario
from opspilot_integration.demo import app as demo
from test_readiness_flow import objects
import test_resource_incidents as resource_fixture


class KubernetesMetricTests(unittest.TestCase):
    def setUp(self):
        self.data = objects(True)
        self.data["pod"]["spec"]["nodeName"] = "opspilot"
        self.commands = []
        def get(*args):
            self.commands.append(args)
            return {"items": [self.data["pod"]]} if args[0] == "pods" else self.data[args[0]]
        self.read = patch.object(resource, "get", side_effect=get)
        self.read.start(); self.addCleanup(self.read.stop)
        self.addCleanup(setattr, metrics, "_values", None)

    def failed(self, reason="OOMKilled", code=137):
        p = self.data["pod"]
        p["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
        p["status"]["containerStatuses"][0].update({"ready": False,
            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
            "lastState": {"terminated": {"reason": reason, "exitCode": code}}})

    def test_healthy_pod_emits_zero_incidents(self):
        values = metrics.observe()
        self.assertEqual(values["opspilot_demo_pod_ready"], 1)
        self.assertEqual(values["opspilot_demo_oom_active"], 0)
        self.assertEqual(values["opspilot_demo_crash_active"], 0)

    def test_actual_oom_evidence_changes_only_oom_gauge(self):
        self.failed()
        values = metrics.observe()
        self.assertEqual(values["opspilot_demo_oom_active"], 1)
        self.assertEqual(values["opspilot_demo_crash_active"], 0)

    def test_error_exit_one_is_crash_not_oom(self):
        self.failed("Error", 1)
        values = metrics.observe()
        self.assertEqual(values["opspilot_demo_crash_active"], 1)
        self.assertEqual(values["opspilot_demo_oom_active"], 0)

    def test_exit_137_alone_cannot_be_oom_metric(self):
        self.failed("Error", 137)
        self.assertEqual(metrics.observe()["opspilot_demo_oom_active"], 0)

    def test_old_oom_on_ready_pod_does_not_fire(self):
        self.failed()
        p = self.data["pod"]
        p["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        p["status"]["containerStatuses"][0]["ready"] = True
        self.assertEqual(metrics.observe()["opspilot_demo_oom_active"], 0)

    def test_unknown_evidence_is_not_fake_healthy_zero(self):
        with patch.object(metrics, "observe", side_effect=OSError): metrics.refresh()
        text = metrics.render()
        self.assertIn('opspilot_kubernetes_evidence_available{service="opspilot-demo"} 0', text)
        self.assertNotIn('opspilot_demo_oom_active{', text)

    def test_stale_observation_is_unavailable(self):
        with patch.object(metrics.time, "monotonic", return_value=100): metrics.refresh()
        with patch.object(metrics.time, "monotonic", return_value=131):
            self.assertNotIn('opspilot_demo_oom_active{', metrics.render())

    def test_export_is_numeric_and_omits_private_pod_identity(self):
        metrics.refresh()
        text = metrics.render()
        self.assertNotIn("pod-1", text); self.assertNotIn("deployment-1", text)
        self.assertIn('opspilot_demo_pod_ready{service="opspilot-demo"} 1', text)

    def test_unknown_image_is_not_exported_as_known_health(self):
        self.data["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = "untrusted:latest"
        metrics.refresh()
        self.assertNotIn('opspilot_demo_pod_ready{', metrics.render())

    def test_scrape_endpoint_does_not_start_ai(self):
        metrics.refresh()
        with TestClient(bridge.app) as client, patch.object(bridge, "run_ai_investigation") as ai:
            response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        ai.assert_not_called()

    def test_observer_never_calls_ai_or_creates_approval(self):
        from opspilot_integration import incident_dedup, resource_incidents as monitor
        with patch.object(bridge._stop, "wait", side_effect=[False, True]), \
             patch.object(metrics, "refresh") as refresh, \
             patch.object(bridge, "inspect_crash", return_value={"confirmed": False}), \
             patch.object(incident_dedup, "demo_is_healthy", return_value=True), \
             patch.object(incident_dedup, "observe_demo_health", return_value=False), \
             patch.object(bridge, "run_ai_investigation") as ai, \
             patch.object(monitor, "process_current_incident") as investigation:
            bridge.watch_kubernetes()
        refresh.assert_called_once(); ai.assert_not_called(); investigation.assert_not_called()


class MetricsResourceFlowTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing evidence fixture, not any of its test methods.
        self.f = resource_fixture.ResourceIncidentTests("runTest")
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.token = patch.object(bridge, "alert_token", return_value="offline-alert")
        self.token.start(); self.addCleanup(self.token.stop)
        bridge._seen.clear()

    def event(self):
        return {"status": "firing", "labels": {"alertname": "OpsPilotDemoOOMKilled", "service": "opspilot-demo",
                "job": "opspilot-kubernetes", "instance": "host.docker.internal:18008"}, "startsAt": "2026-10-30T00:00:00Z"}

    def send(self, event=None, token="offline-alert"):
        return self.f.client.post("/alerts", headers={"Authorization": "Bearer " + token},
                                  json={"receiver": "local-opspilot", "alerts": [event or self.event()]})

    def test_metric_alert_real_graph_approval_repair_verified_and_journalled(self):
        self.assertEqual(self.send().json(), {"accepted": 1})
        self.assertEqual(self.f.mutations, [])
        row = history.recent_history()[0]
        self.assertEqual(row["alert"]["labels"]["alertname"], "OpsPilotDemoOOMKilled")
        self.assertEqual(row["state"]["alert_verification"]["before_ai"]["status"], "firing")
        self.f.callback(row["approval_id"])
        result = history.get_investigation(row["investigation_id"])
        self.assertEqual(result["outcome"]["status"], "recovered")
        self.assertTrue(result["outcome"]["recovery"]["service_http_verified"])
        self.assertEqual(len(self.f.mutations), 1)

    def test_wrong_alert_secret_cannot_start_investigation(self):
        self.assertEqual(self.send(token="wrong").status_code, 403)
        self.f.groq.assert_not_called(); self.f.send.assert_not_called()

    def test_without_metric_event_no_resource_investigation(self):
        from opspilot_integration.resource_incidents import process_current_incident
        self.assertEqual(process_current_incident()["status"], "metric_trigger_required")
        self.f.groq.assert_not_called()

    def test_cleared_alert_does_not_start_ai(self):
        self.f.verify.return_value = {"status": "inactive"}
        self.send()
        self.f.groq.assert_not_called(); self.f.send.assert_not_called()

    def test_cleared_during_ai_does_not_issue_approval(self):
        self.f.verify.side_effect = [{"status": "firing"}, {"status": "inactive"}]
        self.send()
        self.f.groq.assert_called_once(); self.f.send.assert_not_called()

    def test_repeat_rule_activation_same_pod_does_not_spam(self):
        self.send()
        self.send({**self.event(), "startsAt": "2026-10-30T00:01:00Z"})
        self.f.send.assert_called_once(); self.f.groq.assert_called_once()

    def test_metric_type_must_match_actual_incident(self):
        self.f.set_scheduling()
        self.send()
        self.f.groq.assert_not_called(); self.f.send.assert_not_called()

    def test_healthy_pod_does_not_repair_from_oom_alert(self):
        self.f.data["pod"]["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        self.send()
        self.f.send.assert_not_called(); self.assertEqual(self.f.mutations, [])


class ControlledScenarioTests(unittest.TestCase):
    def setUp(self):
        self.data = objects(True)
        self.deployment = self.data["deployment"]
        self.container = self.deployment["spec"]["template"]["spec"]["containers"][0]
        self.container["resources"] = {"requests": {"memory": "64Mi", "cpu": "50m"}, "limits": {"memory": "256Mi", "cpu": "300m"}}
        self.container["env"] = [{"name": "UNRELATED", "value": "preserve"}]
        self.data["pod"]["spec"]["nodeName"] = "opspilot"
        self.patches = []
        for patcher in (patch.object(scenario, "check_routes"),
                        patch.object(scenario, "healthy_demo", return_value=(self.deployment,self.data["pod"],{},self.data["service"])),
                        patch.object(scenario, "memory_headroom", return_value=1536)):
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(scenario.ready, "run", return_value="")
        self.run = patcher.start(); self.addCleanup(patcher.stop)

    def test_no_opt_in_means_no_test_mutation(self):
        with self.assertRaises(ValueError): scenario.inject_oom(False)
        self.run.assert_not_called()

    def test_oom_injection_changes_only_explicit_test_env(self):
        before = copy.deepcopy(self.deployment)
        with redirect_stdout(io.StringIO()): scenario.inject_oom(True)
        args = self.run.call_args.args
        changes = json.loads(args[-1])
        edits = [x for x in changes if x["op"] != "test"]
        self.assertEqual(edits, [{"op":"replace","path":"/spec/template/spec/containers/0/env",
            "value":[{"name":"UNRELATED","value":"preserve"},{"name":scenario.MEMORY_ENV,"value":"320"}]}])
        self.assertEqual(self.deployment, before)
        self.assertTrue(any(x["path"] == "/metadata/resourceVersion" and x["op"] == "test" for x in changes))

    def test_512_limit_cannot_be_reinjected_or_lowered(self):
        self.container["resources"]["limits"]["memory"] = "512Mi"
        with self.assertRaises(ValueError): scenario.inject_oom(True)
        self.run.assert_not_called()

    def test_request_memory_is_never_changed_to_make_test_work(self):
        self.container["resources"]["requests"]["memory"] = "128Mi"
        with self.assertRaises(ValueError): scenario.inject_oom(True)
        self.run.assert_not_called()

    def test_duplicate_injection_refused(self):
        self.container["env"].append({"name":scenario.MEMORY_ENV,"value":"320"})
        with self.assertRaises(ValueError): scenario.inject_oom(True)
        self.run.assert_not_called()

    def test_insufficient_headroom_stops_before_mutation(self):
        with patch.object(scenario,"memory_headroom",side_effect=ValueError("Insufficient memory")),self.assertRaises(ValueError):
            scenario.inject_oom(True)
        self.run.assert_not_called()

    def test_dead_tunnel_stops_before_mutation(self):
        with patch.object(scenario,"check_routes",side_effect=ValueError("Tunnel unavailable")),self.assertRaises(ValueError):
            scenario.inject_oom(True)
        self.run.assert_not_called()

    def test_memory_allocation_is_fixed_and_page_touched_without_real_allocation(self):
        block = Mock()
        block.__setitem__ = Mock()
        factory = Mock(return_value=block)
        with redirect_stdout(io.StringIO()): value = demo.allocate_demo_memory("320",factory)
        factory.assert_called_once_with(320*1024*1024)
        self.assertIs(value,block)
        self.assertEqual(block.__setitem__.call_count,81920)

    def test_normal_startup_never_allocates_demo_load(self):
        factory = Mock()
        self.assertIsNone(demo.allocate_demo_memory(None,factory)); factory.assert_not_called()

    def test_unknown_memory_demands_rejected_without_allocating(self):
        factory=Mock()
        for value in ("512","-1","320; touch /tmp/no",320,"320Mi","100000"):
            with self.subTest(value=value),self.assertRaises(ValueError): demo.allocate_demo_memory(value,factory)
        factory.assert_not_called()

    def test_quantities_are_parsed_without_shell_or_guessing(self):
        self.assertEqual(scenario.quantity("1Gi"),1024**3)
        self.assertEqual(scenario.quantity("1024Mi"),1024**3)
        for value in ("1G","2.5Gi","-1Mi","100m","$(id)"):
            with self.subTest(value=value),self.assertRaises(ValueError): scenario.quantity(value)


class NodeHeadroomTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.node = {"status": {"allocatable": {"memory":"3Gi"}, "conditions": [
            {"type":"Ready","status":"True"},{"type":"MemoryPressure","status":"False"}]}}
        self.sample = {"metadata":{"name":"opspilot"},"timestamp":datetime.fromtimestamp(self.now,timezone.utc).isoformat(),"usage":{"memory":"1Gi"}}
        for patcher in (patch.object(resource,"get",return_value=self.node),
                        patch.object(resource,"run",side_effect=lambda *a:json.dumps(self.sample)),
                        patch.object(scenario.time,"time",return_value=self.now),
                        patch.object(Path,"read_text",return_value="MemAvailable: 2097152 kB\n")):
            patcher.start(); self.addCleanup(patcher.stop)
        self.pod = {"spec":{"nodeName":"opspilot"}}

    def test_fresh_ample_headroom_is_accepted(self): self.assertEqual(scenario.memory_headroom(self.pod),2048)

    def test_node_memory_pressure_refuses_test(self):
        self.node["status"]["conditions"][1]["status"]="True"
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)

    def test_unknown_node_pressure_refuses_test(self):
        self.node["status"]["conditions"]=[]
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)

    def test_low_node_headroom_refuses_test(self):
        self.sample["usage"]["memory"]="2500Mi"
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)

    def test_stale_sample_refuses_test(self):
        self.sample["timestamp"]=datetime.fromtimestamp(self.now-61,timezone.utc).isoformat()
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)

    def test_future_sample_refuses_test(self):
        self.sample["timestamp"]=datetime.fromtimestamp(self.now+20,timezone.utc).isoformat()
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)

    def test_low_wsl_headroom_refuses_test(self):
        with patch.object(Path,"read_text",return_value="MemAvailable: 100000 kB\n"),self.assertRaises(ValueError):
            scenario.memory_headroom(self.pod)

    def test_another_node_refuses_test(self):
        self.pod["spec"]["nodeName"]="production"
        with self.assertRaises(ValueError): scenario.memory_headroom(self.pod)


if __name__ == "__main__":
    unittest.main()
