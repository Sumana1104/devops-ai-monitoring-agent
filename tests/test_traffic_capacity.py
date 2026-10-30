"""Offline safety/regression tests; never Kubernetes/Groq/Telegram mutations."""
import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from opspilot_integration.traffic import app, control as c, evidence as e


def sample():
    return {"deployment_uid": "d", "generation": 5, "template_hash": "t", "service_uid": "s", "replicas": 1,
            "alert_active_at": "activation-1",
            "resource_version": "9", "pods": [{"uid": "p", "name": "pod"}],
            "capacity": {"spare_cpu": .8, "spare_memory_mib": 512},
            "load": {"uid": "j", "source_hash": "code", "burst": True,
                     "client_p95": 1., "client_rps": 5., "client_error_ratio": 0.},
            "metrics": {"p95": 1., "rps": 5., "cpu": .9, "error_ratio": 0.}}


ALERT = {"status": "firing", "startsAt": "2026-10-30T16:00:00Z", "labels": {
    "alertname": "OpsPilotTrafficOverload", "service": "opspilot-traffic"}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSPILOT_TRAFFIC_DB": self.temp.name+"/traffic.sqlite3"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def ready(self):
        i = c.reserve(ALERT)
        c.update(i, "pending", evidence=sample(), analysis={"recommended_action": c.ACTION}, expires=time.time()+600)
        return i

    def test_atomic_single_claim(self):
        i = self.ready()
        self.assertTrue(c.decide(i, "approve"))
        self.assertFalse(c.decide(i, "approve"))
        self.assertTrue(c.claim(i))
        self.assertFalse(c.claim(i))

    def test_reject_cannot_claim(self):
        i = self.ready(); self.assertTrue(c.decide(i, "reject")); self.assertFalse(c.claim(i))

    def test_expired_cannot_decide(self):
        i = self.ready(); c.update(i, "pending", expires=time.time()-1)
        self.assertFalse(c.decide(i, "approve"))

    def test_duplicate_event_suppressed(self):
        self.ready(); self.assertIsNone(c.reserve(ALERT))

    def test_overlapping_event_suppressed(self):
        self.ready(); a = copy.deepcopy(ALERT); a["startsAt"] += "new"
        self.assertIsNone(c.reserve(a))

    def test_disabled_gate_no_mutation(self):
        i = self.ready(); c.decide(i, "approve")
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "0"}), patch.object(e, "run") as run:
            self.assertEqual(c.execute(i)["status"], "blocked"); run.assert_not_called()

    def test_no_approval_no_mutation(self):
        i = self.ready()
        with patch.object(e, "run") as run:
            self.assertEqual(c.execute(i)["status"], "blocked"); run.assert_not_called()

    def test_changed_identity_no_mutation(self):
        i = self.ready(); c.decide(i, "approve"); live = sample(); live["deployment_uid"] = "replacement"
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "1"}), patch.object(e, "eligible", return_value=live), patch.object(e, "run") as run:
            self.assertEqual(c.execute(i)["status"], "blocked"); run.assert_not_called()

    def test_only_replica_field_mutates(self):
        i = self.ready(); c.decide(i, "approve")
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "1"}), patch.object(e, "eligible", return_value=sample()), patch.object(e, "run") as run, patch.object(c, "verify", return_value={"recovered": True}):
            self.assertEqual(c.execute(i)["status"], "recovered")
            args = run.call_args.args
            self.assertEqual(args[:4], ("patch", "deployment", "opspilot-traffic", "--type=json"))
            changes = [x for x in json.loads(args[-1]) if x["op"] != "test"]
            self.assertEqual(changes, [{"op": "replace", "path": "/spec/replicas", "value": 2}])

    def test_changed_after_claim_cannot_patch(self):
        i = self.ready(); c.decide(i, "approve"); bad = sample(); bad["load"]["uid"] = "new-job"
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "1"}), patch.object(e, "eligible", side_effect=[sample(), bad]), patch.object(e, "run") as run:
            self.assertEqual(c.execute(i)["status"], "blocked"); run.assert_not_called()
            self.assertFalse(c.claim(i))

    def test_failed_patch_never_reported_recovered(self):
        i = self.ready(); c.decide(i, "approve")
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "1"}), patch.object(e, "eligible", return_value=sample()), patch.object(e, "run", side_effect=RuntimeError), patch.object(c, "verify") as verify:
            result = c.execute(i)
            self.assertEqual(result["status"], "execution_uncertain"); verify.assert_not_called()

    def test_no_measured_improvement_not_success(self):
        i = self.ready(); c.decide(i, "approve")
        with patch.dict(os.environ, {"OPSPILOT_ENABLE_TRAFFIC_SCALING": "1"}), patch.object(e, "eligible", return_value=sample()), patch.object(e, "run"), patch.object(c, "verify", return_value={"recovered": False}):
            self.assertEqual(c.execute(i)["status"], "recovery_not_verified")

    def test_process_requires_firing_before_ai(self):
        with patch.object(e, "firing", side_effect=ValueError), patch.object(c, "investigate") as ai, patch.object(c, "notify"):
            c.process(ALERT); ai.assert_not_called()

    def test_process_sends_new_approval_not_execute(self):
        with patch.object(e, "firing"), patch.object(c, "investigate", return_value={"safe": True, "evidence": sample(), "analysis": {"root_cause": "CPU capacity", "recommended_action": c.ACTION}}), patch.object(c, "notify") as notify, patch.object(c, "execute") as execute:
            c.process(ALERT); execute.assert_not_called()
            self.assertTrue(notify.call_args.args[1]["inline_keyboard"][0][0]["callback_data"].startswith("traffic_approve:"))

    def test_uncertain_ai_does_not_issue_approval(self):
        with patch.object(e, "firing"), patch.object(c, "investigate", side_effect=ValueError), patch.object(c, "notify") as notify:
            c.process(ALERT)
            self.assertEqual(len(notify.call_args.args), 1)


class EvidenceTests(unittest.TestCase):
    def test_identity_accepts_unchanged(self): self.assertTrue(e.same(sample(), sample()))

    def test_identity_rejects_new_pod(self):
        v = sample(); v["pods"][0]["uid"] = "new"; self.assertFalse(e.same(sample(), v))

    def test_identity_rejects_changed_job(self):
        v = sample(); v["load"]["uid"] = "new"; self.assertFalse(e.same(sample(), v))

    def test_identity_rejects_changed_service(self):
        v = sample(); v["service_uid"] = "new"; self.assertFalse(e.same(sample(), v))

    def test_identity_rejects_changed_template(self):
        v = sample(); v["template_hash"] = "new"; self.assertFalse(e.same(sample(), v))

    def test_counters_reject_nan(self):
        with self.assertRaises(ValueError): e.number("NaN")

    def test_counters_reject_negative(self):
        with self.assertRaises(ValueError): e.number(-1)

    def test_counters_reject_infinity(self):
        with self.assertRaises(ValueError): e.number("inf")

    def test_cpu_units(self): self.assertAlmostEqual(e.quantity("500000000n", True), .5)

    def test_memory_units(self): self.assertEqual(e.quantity("256Mi"), 256*1024**2)

    def test_unknown_units_rejected(self):
        with self.assertRaises(ValueError): e.quantity("500bananas")

    def test_stale_metrics_are_unknown_not_healthy(self):
        with patch.object(e, "_cache", {"observed": time.time()-90}):
            self.assertEqual(e.render(), "opspilot_traffic_evidence_available 0\n")

    def test_failed_refresh_no_metrics_success(self):
        with patch.object(e, "snapshot", side_effect=ValueError):
            e.refresh(); self.assertNotIn("opspilot_traffic_cpu_fraction", e.render())

    def test_observer_never_invokes_ai(self):
        with patch.object(e, "snapshot", return_value={"unavailable": True, "observed": time.time()}), patch.object(c, "investigate") as ai:
            e.refresh(); ai.assert_not_called()

    def test_explicit_wrong_alert_blocked(self):
        with self.assertRaises(ValueError): e.firing({"status": "firing", "labels": {"alertname": "OpsPilotDemoMetricsDown", "service": "opspilot-demo"}})

    def test_eligible_requires_burst(self):
        data = sample(); data["load"]["burst"] = False
        with patch.object(e, "firing"), patch.object(e, "snapshot", return_value=data), self.assertRaises(ValueError):
            e.eligible(ALERT)

    def test_traffic_alone_not_enough(self):
        with patch.object(e, "firing"), patch.object(e, "snapshot", return_value=sample()), patch.object(e, "measurements", return_value={"p95": .1, "rps": 50, "cpu": .1}), self.assertRaises(ValueError):
            e.eligible(ALERT)

    def test_latency_without_cpu_not_enough(self):
        with patch.object(e, "firing"), patch.object(e, "snapshot", return_value=sample()), patch.object(e, "measurements", return_value={"p95": 1, "rps": 50, "cpu": .1}), self.assertRaises(ValueError):
            e.eligible(ALERT)


class ApplicationTests(unittest.TestCase):
    def test_real_work_returns_deterministic_result(self):
        with TestClient(app.app) as client:
            a = client.get("/work"); b = client.get("/work")
        self.assertEqual(a.status_code, 200); self.assertEqual(a.json()["result"], b.json()["result"])

    def test_health_not_artificial_failure(self):
        with TestClient(app.app) as client: self.assertEqual(client.get("/healthz").status_code, 200)

    def test_old_fault_routes_not_present(self):
        with TestClient(app.app) as client:
            self.assertEqual(client.get("/slow").status_code, 404)
            self.assertEqual(client.get("/error").status_code, 404)

    def test_bounded_queue_rejects_overload(self):
        with patch.object(app, "ADMITTED", 24), TestClient(app.app) as client:
            self.assertEqual(client.get("/work").status_code, 503)

    def test_telemetry_consistent(self):
        with TestClient(app.app) as client: data = client.get("/telemetry").json()
        self.assertEqual(data["buckets"][-1], sum(data["counts"].values()))

    def test_work_does_not_sleep_or_use_demo_flags(self):
        text = Path(app.__file__).read_text()
        self.assertNotIn("asyncio.sleep", text); self.assertNotIn("crash.flag", text)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        from opspilot_integration import bridge
        self.bridge = bridge
        self.env = patch.dict(os.environ, {"OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_TRAFFIC_MONITORING": "0",
                           "TELEGRAM_WEBHOOK_SECRET": "test-only", "TELEGRAM_APPROVER_USER_ID": "1", "TELEGRAM_CHAT_ID": "2"})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_unsigned_callback_denied(self):
        with TestClient(self.bridge.app) as client: self.assertEqual(client.post("/telegram/webhook", json={}).status_code, 403)

    def test_wrong_approver_cannot_reach_traffic_callback(self):
        data = {"callback_query": {"from": {"id": 99}, "message": {"chat": {"id": 2}}, "data": "traffic_approve:"+"a"*32}}
        with patch.object(c, "callback") as callback, TestClient(self.bridge.app) as client:
            response = client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-only"}, json=data)
            self.assertEqual(response.json()["status"], "unauthorized_approver"); callback.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def check(self, change=None, metrics=None, frozen=False):
        clock = [0.]
        counts = [10]
        def current(_):
            v = sample(); v["replicas"] = 2
            v["load"].update(client_p95=.2, client_rps=6.)
            if not frozen: counts[0] += 10
            v["pods"] = [{"uid": "p", "telemetry": {"counts": {"200": counts[0]}}},
                         {"uid": "p2", "telemetry": {"counts": {"200": counts[0]}}}]
            if change: change(v)
            return v
        with patch.object(e, "snapshot", side_effect=current), patch.object(e, "measurements", return_value=metrics or {"p95": .2, "rps": 6, "error_ratio": 0, "cpu": .4}), patch.object(c.time, "monotonic", side_effect=lambda: clock[0]), patch.object(c.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0]+s)):
            return c.verify(sample(), timeout=150)

    def test_actual_both_backend_counts_and_same_load_required(self): self.assertTrue(self.check()["recovered"])

    def test_changed_job_fails_verification(self):
        self.assertFalse(self.check(change=lambda v: v["load"].update(uid="new"))["recovered"])

    def test_stopped_burst_fails_verification(self):
        self.assertFalse(self.check(change=lambda v: v["load"].update(burst=False))["recovered"])

    def test_changed_service_fails_verification(self):
        self.assertFalse(self.check(change=lambda v: v.update(service_uid="new"))["recovered"])

    def test_latency_not_improved_fails(self):
        self.assertFalse(self.check(metrics={"p95": .9, "rps": 6, "error_ratio": 0})["recovered"])

    def test_error_ratio_still_high_fails(self):
        self.assertFalse(self.check(metrics={"p95": .2, "rps": 6, "error_ratio": .02})["recovered"])

    def test_lower_throughput_is_not_recovery(self):
        self.assertFalse(self.check(metrics={"p95": .2, "rps": 2, "error_ratio": 0})["recovered"])

    def test_no_requests_to_backend_is_not_recovery(self): self.assertFalse(self.check(frozen=True)["recovered"])


class NativeGraphTests(unittest.TestCase):
    def plan(self, result):
        from types import SimpleNamespace
        import groq
        from ai_service.app import rag_tools
        provider = Mock()
        provider.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])
        with patch.dict(os.environ, {"GROQ_API_KEY": "unit-test-placeholder"}), patch.object(e, "eligible", return_value=sample()), patch.object(groq, "Groq", return_value=provider), patch.object(rag_tools, "retrieve_similar_incidents", return_value=[]):
            return c.investigate(ALERT)

    def test_native_langgraph_and_schema_with_mocked_provider(self):
        self.assertTrue(self.plan({"root_cause": "Measured CPU pressure", "recommended_action": c.ACTION, "confidence": .8, "severity": "high"})["safe"])

    def test_model_can_abstain(self):
        self.assertFalse(self.plan({"root_cause": "Uncertain cause", "recommended_action": "manual_review", "confidence": .4, "severity": "medium"})["safe"])

    def test_arbitrary_shell_action_rejected(self):
        with self.assertRaises(ValueError):
            self.plan({"root_cause": "Bad model output", "recommended_action": "kubectl delete namespace opspilot", "confidence": 1, "severity": "high"})

    def test_invalid_confidence_rejected(self):
        with self.assertRaises(ValueError):
            self.plan({"root_cause": "Bad model output", "recommended_action": c.ACTION, "confidence": 5, "severity": "high"})


class TransportIntegrationTests(StoreTests):
    # Uses actual FastAPI and SQLite; external operations are still mocked.
    def test_authenticated_alert_runs_traffic_processor(self):
        from opspilot_integration import bridge
        with patch.dict(os.environ, {"OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_TRAFFIC_MONITORING": "0"}), patch.object(bridge, "alert_token", return_value="test-only"), patch.object(c, "process") as process, TestClient(bridge.app) as client:
            with patch.dict(os.environ, {"OPSPILOT_TRAFFIC_MONITORING": "1"}):
                response = client.post("/alerts", headers={"Authorization": "Bearer test-only"}, json={"alerts": [ALERT]})
            self.assertEqual(response.json(), {"accepted": 1}); process.assert_called_once()

    def test_unsigned_alert_does_not_start_ai(self):
        from opspilot_integration import bridge
        with patch.dict(os.environ, {"OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_TRAFFIC_MONITORING": "0"}), patch.object(bridge, "alert_token", return_value="test-only"), patch.object(c, "process") as process, TestClient(bridge.app) as client:
            self.assertEqual(client.post("/alerts", json={"alerts": [ALERT]}).status_code, 403)
            process.assert_not_called()

    def test_authenticated_callback_records_and_schedules_only_once(self):
        from opspilot_integration import bridge
        i = self.ready()
        event = {"callback_query": {"id": "test", "from": {"id": 1}, "message": {"chat": {"id": 2}}, "data": "traffic_approve:"+i}}
        with patch.dict(os.environ, {"OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_TRAFFIC_MONITORING": "0", "TELEGRAM_WEBHOOK_SECRET": "test-only", "TELEGRAM_APPROVER_USER_ID": "1", "TELEGRAM_CHAT_ID": "2"}), patch.object(bridge, "telegram_api"), patch.object(c, "approved") as execute, TestClient(bridge.app) as client:
            for _ in range(2):
                client.post("/telegram/webhook", json=event, headers={"X-Telegram-Bot-Api-Secret-Token": "test-only"})
            execute.assert_called_once_with(i)
            self.assertEqual(c.record(i)["status"], "approved")

    def test_reject_callback_does_not_schedule_execution(self):
        from opspilot_integration import bridge
        i = self.ready()
        event = {"callback_query": {"id": "test", "from": {"id": 1}, "message": {"chat": {"id": 2}}, "data": "traffic_reject:"+i}}
        with patch.dict(os.environ, {"OPSPILOT_DISABLE_WATCHER": "1", "OPSPILOT_TRAFFIC_MONITORING": "0", "TELEGRAM_WEBHOOK_SECRET": "test-only", "TELEGRAM_APPROVER_USER_ID": "1", "TELEGRAM_CHAT_ID": "2"}), patch.object(bridge, "telegram_api"), patch.object(c, "approved") as execute, patch.object(c, "rejected"), TestClient(bridge.app) as client:
            client.post("/telegram/webhook", json=event, headers={"X-Telegram-Bot-Api-Secret-Token": "test-only"})
            execute.assert_not_called(); self.assertEqual(c.record(i)["status"], "rejected")


class KubernetesBindingTests(unittest.TestCase):
    def setUp(self):
        import yaml
        docs = list(yaml.safe_load_all(Path(e.__file__).with_name("workload.yaml").read_text()))
        self.d, self.s = docs
        self.d["metadata"].update(uid="deployment", generation=1, resourceVersion="10")
        self.d["status"] = {"observedGeneration": 1}
        self.s["metadata"]["uid"] = "service"
        self.p = {"metadata": {"name": "traffic-pod", "uid": "pod", "namespace": "opspilot", "ownerReferences": [{"controller": True, "kind": "ReplicaSet", "uid": "rs", "name": "rs"}]},
                  "spec": copy.deepcopy(self.d["spec"]["template"]["spec"]),
                  "status": {"conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"name": "traffic", "ready": True, "restartCount": 0, "image": e.IMAGE, "state": {"running": {"startedAt": "2026-10-30T00:00:00Z"}}}]}}
        self.p["spec"]["nodeName"] = "opspilot"
        self.rs = {"metadata": {"uid": "rs", "ownerReferences": [{"controller": True, "kind": "Deployment", "uid": "deployment"}]}}
        self.slice = {"metadata": {"ownerReferences": [{"kind": "Service", "uid": "service"}]}, "endpoints": [{"targetRef": {"uid": "pod"}, "conditions": {"ready": True}}]}
        from datetime import datetime, timezone
        self.pm = {"items": [{"metadata": {"name": "traffic-pod"}, "timestamp": datetime.now(timezone.utc).isoformat(), "containers": [{"usage": {"cpu": "400000000n"}}]}]}
        self.hp = []

    def snapshot(self):
        def get(*args):
            return copy.deepcopy({"deployment": self.d, "service": self.s, "pods": {"items": [self.p]}, "replicaset": self.rs,
                                  "hpa": {"items": self.hp}, "endpointslices": {"items": [self.slice]}}[args[0]])
        def run(*args, **kwargs):
            if args[0] == "get": return json.dumps(self.pm)
            return json.dumps({"instance": "traffic-pod", "counts": {"200": 10, "503": 0}, "buckets": [10]*10, "duration_sum": 1.})
        with patch.object(e, "get", side_effect=get), patch.object(e, "run", side_effect=run), patch.object(e, "load_job", return_value=sample()["load"]):
            return e.snapshot(1)

    def test_valid_owned_ready_workload(self): self.assertEqual(self.snapshot()["service_uid"], "service")

    def test_hpa_conflict_stops(self):
        self.hp = [{"spec": {"scaleTargetRef": {"kind": "Deployment", "name": e.TARGET}}}]
        with self.assertRaises(ValueError): self.snapshot()

    def test_existing_main_hpa_does_not_conflict(self):
        self.hp = [{"spec": {"scaleTargetRef": {"kind": "Deployment", "name": "opspilot"}}}]
        self.assertEqual(self.snapshot()["replicas"], 1)

    def test_owner_mismatch_stops(self):
        self.rs["metadata"]["ownerReferences"][0]["uid"] = "foreign"
        with self.assertRaises(ValueError): self.snapshot()

    def test_new_pod_configuration_stops(self):
        self.p["spec"]["containers"][0]["env"] = [{"name": "UNKNOWN", "value": "changed"}]
        with self.assertRaises(ValueError): self.snapshot()

    def test_unready_stops(self):
        self.p["status"]["containerStatuses"][0]["ready"] = False
        with self.assertRaises(ValueError): self.snapshot()

    def test_restarted_pod_stops(self):
        self.p["status"]["containerStatuses"][0]["restartCount"] = 1
        with self.assertRaises(ValueError): self.snapshot()

    def test_wrong_service_endpoints_stop(self):
        self.slice["endpoints"][0]["targetRef"]["uid"] = "foreign"
        with self.assertRaises(ValueError): self.snapshot()

    def test_wrong_endpoint_owner_stops(self):
        self.slice["metadata"]["ownerReferences"][0]["uid"] = "foreign"
        with self.assertRaises(ValueError): self.snapshot()

    def test_stale_cpu_metrics_stop(self):
        self.pm["items"][0]["timestamp"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(ValueError): self.snapshot()

    def test_unobserved_generation_stops(self):
        self.d["status"]["observedGeneration"] = 0
        with self.assertRaises(ValueError): self.snapshot()

    def test_no_scale_above_two(self):
        self.d["spec"]["replicas"] = 3
        with self.assertRaises(ValueError): self.snapshot()


class AlertTimestampTests(unittest.TestCase):
    def check(self, started_seconds):
        from datetime import datetime, timezone
        now = time.time()
        iso = lambda delta: datetime.fromtimestamp(now-delta, timezone.utc).isoformat()
        alert = copy.deepcopy(ALERT); alert["startsAt"] = iso(started_seconds)
        row = {"labels": alert["labels"], "state": "firing", "activeAt": iso(60)}
        response = Mock(); response.json.return_value = {"status": "success", "data": {"alerts": [row]}}
        with patch.object(e.requests, "get", return_value=response): return e.firing(alert)

    def test_pending_and_fired_timestamps_can_differ(self): self.assertIsInstance(self.check(30), str)

    def test_old_activation_is_not_reused(self):
        with self.assertRaises(ValueError): self.check(90)

    def test_future_timestamp_blocked(self):
        with self.assertRaises(ValueError): self.check(-60)


if __name__ == "__main__": unittest.main()
