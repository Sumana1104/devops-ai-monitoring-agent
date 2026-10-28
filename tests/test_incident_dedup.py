"""Offline tests: temporary DB only, no Telegram/Groq/Kubernetes calls."""
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opspilot_integration import incident_dedup as dedup


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "OPSPILOT_APPROVAL_DB": str(Path(self.temporary.name) / "test.sqlite3"),
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def recovered(self, start=100):
        for now in range(start, start + 60, 15):
            self.assertFalse(dedup.observe_demo_health(True, now))
        self.assertTrue(dedup.observe_demo_health(True, start + 60))

    def test_single_notification_even_without_delivery_confirmation(self):
        self.assertIsNotNone(dedup.claim_crash_notification())
        self.assertIsNone(dedup.claim_crash_notification())

    def test_persists_across_python_processes(self):
        self.assertIsNotNone(dedup.claim_crash_notification())
        result = subprocess.run([
            sys.executable, "-c",
            "from opspilot_integration.incident_dedup import claim_crash_notification; "
            "print(claim_crash_notification())",
        ], cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(result.stdout.strip(), "None")

    def test_concurrent_sources_send_once(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: dedup.claim_crash_notification(), range(24)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_failed_or_uncertain_delivery_is_not_retried(self):
        episode = dedup.claim_crash_notification()
        dedup.finish_notification(episode)
        self.assertIsNone(dedup.claim_crash_notification())

    def test_new_episode_after_sustained_recovery(self):
        old = dedup.claim_crash_notification()
        self.recovered()
        new = dedup.claim_crash_notification()
        self.assertIsNotNone(new)
        self.assertNotEqual(new, old)

    def test_unknown_health_does_not_clear_crash(self):
        dedup.claim_crash_notification()
        dedup.observe_demo_health(True, 100)
        dedup.observe_demo_health(False, 115)
        self.assertFalse(dedup.observe_demo_health(True, 130))
        self.assertIsNone(dedup.claim_crash_notification())

    def test_long_gap_or_restart_is_not_counted_as_recovery(self):
        dedup.claim_crash_notification()
        dedup.observe_demo_health(True, 100)
        self.assertFalse(dedup.observe_demo_health(True, 1000))
        self.assertIsNone(dedup.claim_crash_notification())

    def test_confirmed_crash_interrupts_healthy_timer(self):
        dedup.claim_crash_notification()
        dedup.observe_demo_health(True, 100)
        dedup.observe_demo_health(True, 130)
        self.assertIsNone(dedup.claim_crash_notification())
        self.assertFalse(dedup.observe_demo_health(True, 160))

    def bridge_function(self):
        # Exercise the actual patched function, without starting FastAPI or
        # importing runtime dependencies that could call external services.
        tree = ast.parse((ROOT / "opspilot_integration/bridge.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "process_crash_evidence")
        scope = {"log": Mock(), "telegram_text": Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bridge.py", "exec"), scope)
        return scope["process_crash_evidence"], scope

    def test_restart_count_and_pod_changes_do_not_spam(self):
        function, _ = self.bridge_function()
        notification = types.ModuleType("api_service.app.incident_notification")
        notification.request_incident_approval = Mock(return_value={"incident_id": "abc"})
        with patch.dict(sys.modules, {notification.__name__: notification}):
            for count in range(10):
                function({"confirmed": True, "pod": f"demo-{count}",
                          "restart_count": count}, "watcher")
            importlib.reload(dedup)
            function({"confirmed": True, "pod": "replacement", "restart_count": 0}, "alert")
        notification.request_incident_approval.assert_called_once()

    def test_delivery_exception_does_not_create_repeated_error_messages(self):
        function, scope = self.bridge_function()
        notification = types.ModuleType("api_service.app.incident_notification")
        notification.request_incident_approval = Mock(side_effect=TimeoutError("uncertain"))
        with patch.dict(sys.modules, {notification.__name__: notification}):
            for count in range(5):
                function({"confirmed": True, "pod": "demo", "restart_count": count}, "watcher")
        notification.request_incident_approval.assert_called_once()
        scope["telegram_text"].assert_not_called()

    def test_unconfirmed_evidence_does_not_send(self):
        function, _ = self.bridge_function()
        function({"confirmed": False}, "watcher")
        self.assertIsNotNone(dedup.claim_crash_notification())

    @staticmethod
    def healthy_objects():
        return [
            {"metadata": {"generation": 2}, "spec": {"replicas": 1},
             "status": {"observedGeneration": 2, "readyReplicas": 1,
                        "updatedReplicas": 1, "availableReplicas": 1}},
            {"items": [{"metadata": {}, "status": {
                "phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"ready": True, "state": {"running": {}}}],
            }}]},
        ]

    def test_recovery_probe_requires_running_ready_pods(self):
        with patch.object(dedup, "_kubectl_json", side_effect=self.healthy_objects()):
            self.assertTrue(dedup.demo_is_healthy())
        objects = self.healthy_objects()
        objects[1]["items"][0]["status"]["containerStatuses"][0]["ready"] = False
        with patch.object(dedup, "_kubectl_json", side_effect=objects):
            self.assertFalse(dedup.demo_is_healthy())

    def test_api_error_or_scaled_to_zero_is_not_recovery(self):
        with patch.object(dedup, "_kubectl_json", side_effect=subprocess.CalledProcessError(1, "kubectl")):
            self.assertFalse(dedup.demo_is_healthy())
        objects = self.healthy_objects()
        objects[0]["spec"]["replicas"] = 0
        with patch.object(dedup, "_kubectl_json", side_effect=objects):
            self.assertFalse(dedup.demo_is_healthy())

    def test_recovery_probe_uses_explicit_local_context(self):
        with patch.object(dedup.subprocess, "run", return_value=types.SimpleNamespace(stdout="{}")) as run:
            dedup._kubectl_json("get", "deployment", "opspilot-demo")
        self.assertEqual(run.call_args.args[0][:5],
                         ["kubectl", "--context", "opspilot", "-n", "opspilot"])


if __name__ == "__main__":
    unittest.main()
