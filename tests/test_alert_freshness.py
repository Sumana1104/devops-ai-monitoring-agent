"""Offline alert/transport checks. No Telegram, Groq, Docker or Kubernetes calls."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from opspilot_integration import alert_freshness as freshness, run_ai_bridge


class AlertFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000.0
        self.labels = {"alertname": "OpsPilotDemoMetricsDown", "service": "opspilot-demo",
                       "severity": "high", "job": "opspilot-demo", "instance": "host.docker.internal:19110"}
        self.event = {"status": "firing", "labels": dict(self.labels)}
        self.active = [{"state": "firing", "labels": dict(self.labels)}]
        self.target = {"labels": {"job": "opspilot-demo", "instance": "host.docker.internal:19110"},
                       "health": "down", "lastScrape": self.stamp(self.now - 5)}
        self.get_patch = patch.object(freshness.requests, "get", side_effect=self.get)
        self.http = self.get_patch.start()

    def tearDown(self):
        self.get_patch.stop()

    def stamp(self, value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def get(self, url, **kwargs):
        response = Mock()
        if url.endswith("/api/v1/alerts"):
            response.json.return_value = {"status": "success", "data": {"alerts": self.active}}
        elif url.endswith("/api/v1/targets?state=active"):
            response.json.return_value = {"status": "success", "data": {"activeTargets": [self.target]}}
        else:
            raise AssertionError("Unexpected HTTP request: " + url)
        return response

    def check(self):
        return freshness.verify_current_alert(self.event, now=self.now)

    def test_real_firing_scrape_failure_is_not_hidden(self):
        self.assertEqual(self.check()["status"], "firing")
        self.assertEqual(self.http.call_count, 2)

    def test_fresh_successful_scrape_clears_queued_metrics_down(self):
        self.target["health"] = "up"
        result = self.check()
        self.assertEqual(result["status"], "inactive")
        self.assertIn("successful scrape", result["reason"])

    def test_no_current_firing_alert_is_inactive_without_claiming_pod_recovery(self):
        self.active = []
        self.assertEqual(self.check()["status"], "inactive")
        self.assertEqual(self.http.call_count, 1)

    def test_pending_is_not_treated_as_a_current_firing_alert(self):
        self.active[0]["state"] = "pending"
        self.assertEqual(self.check()["status"], "inactive")

    def test_same_name_but_other_instance_is_not_a_match(self):
        self.active[0]["labels"]["instance"] = "other:19110"
        self.assertEqual(self.check()["status"], "inactive")

    def test_notifier_external_labels_are_allowed_but_conflicts_are_not(self):
        self.event["labels"]["external_cluster"] = "local"
        self.assertEqual(self.check()["status"], "firing")
        self.event["labels"]["severity"] = "medium"
        self.assertEqual(self.check()["status"], "inactive")

    def test_stale_or_future_scrape_data_is_unknown_not_healthy(self):
        for delta in (-120, 120):
            with self.subTest(delta=delta):
                self.target["health"] = "up"
                self.target["lastScrape"] = self.stamp(self.now + delta)
                self.assertEqual(self.check()["status"], "unknown")

    def test_invalid_scrape_time_or_unknown_health_never_confirms_recovery(self):
        for stamp in (None, "not-a-date", "2026-01-01T12:00:00"):
            with self.subTest(stamp=stamp):
                self.target["lastScrape"] = stamp
                self.assertEqual(self.check()["status"], "unknown")
        self.target["lastScrape"] = self.stamp(self.now)
        self.target["health"] = "unknown"
        self.assertEqual(self.check()["status"], "unknown")

    def test_absent_or_mismatched_target_is_unknown(self):
        self.target["labels"]["instance"] = "other:19110"
        self.assertEqual(self.check()["status"], "unknown")

    def test_prometheus_nanosecond_timestamp_is_supported(self):
        prefix = datetime.fromtimestamp(self.now - 5, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        self.target["lastScrape"] = prefix + ".123456789Z"
        self.assertEqual(self.check()["status"], "firing")

    def test_unavailable_monitoring_is_not_labelled_resolved(self):
        self.http.side_effect = requests.Timeout("private URL must not be printed")
        result = self.check()
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("private URL", result["reason"])

    def test_malformed_monitoring_response_is_unknown(self):
        self.http.side_effect = None
        for payload in ({"status": "error"}, {"status": "success", "data": {"alerts": [None]}},
                        {"status": "success", "data": {"alerts": "wrong"}}):
            with self.subTest(payload=payload):
                self.http.return_value.json.return_value = payload
                self.assertEqual(self.check()["status"], "unknown")

    def test_real_error_rate_and_latency_do_not_require_scrape_to_be_down(self):
        for name in ("OpsPilotHighErrorRate", "OpsPilotHighLatency"):
            with self.subTest(name=name):
                labels = {"alertname": name, "service": "opspilot-demo", "severity": "high"}
                self.event["labels"] = labels
                self.active = [{"state": "firing", "labels": dict(labels)}]
                self.target["health"] = "up"
                before = self.http.call_count
                self.assertEqual(self.check()["status"], "firing")
                self.assertEqual(self.http.call_count - before, 1)

    def test_resolved_webhook_does_not_query_or_call_anything(self):
        self.event["status"] = "resolved"
        self.assertEqual(self.check()["status"], "inactive")
        self.http.assert_not_called()

    def test_unexpected_service_is_not_verified(self):
        self.event["labels"]["service"] = "other-service"
        self.assertEqual(self.check()["status"], "unknown")
        self.http.assert_not_called()


class StartupOrderTests(unittest.TestCase):
    def run_startup(self, initialization_succeeds):
        order = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "observability").mkdir()
            (root / "observability/docker-compose.yml").write_text("services: {}\n")

            def command(args, **kwargs):
                if "ai_service.app.ingest_runbooks" in args:
                    order.append("model-warmup")
                    return SimpleNamespace(returncode=0 if initialization_succeeds else 1)
                return SimpleNamespace(returncode=0)

            environment = {name: "offline-placeholder" for name in (
                "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "TELEGRAM_APPROVER_USER_ID", "TELEGRAM_WEBHOOK_SECRET")}
            old_directory = Path.cwd()
            try:
                with patch.dict(os.environ, environment), patch.object(run_ai_bridge, "ROOT", root), \
                     patch.object(sys, "argv", ["run_ai_bridge"]), \
                     patch.object(run_ai_bridge.shutil, "which", return_value="/offline/tool"), \
                     patch.object(run_ai_bridge.socket, "socket") as socket, \
                     patch.object(run_ai_bridge.threading, "Thread") as worker, \
                     patch.object(run_ai_bridge.subprocess, "run", side_effect=command), \
                     patch.object(run_ai_bridge.subprocess, "Popen") as bridge, \
                     patch.object(requests, "get") as http, redirect_stdout(io.StringIO()):
                    socket.return_value.__enter__.return_value.connect_ex.return_value = 1
                    worker.return_value.start.side_effect = lambda: order.append("forward-worker-start")
                    http.return_value.json.return_value = {"data": {"result": [{"value": [0, "1"]}]}}
                    bridge.return_value.wait.return_value = 0
                    bridge.return_value.poll.return_value = 0
                    with self.assertRaises(SystemExit) as stopped:
                        run_ai_bridge.main()
                    self.assertLess(order.index("forward-worker-start"), order.index("model-warmup"))
                    worker.return_value.join.assert_called_once()
                    if initialization_succeeds:
                        self.assertEqual(stopped.exception.code, 0)
                        bridge.assert_called_once()
                    else:
                        self.assertIn("initialization failed", str(stopped.exception))
                        bridge.assert_not_called()
            finally:
                os.chdir(old_directory)

    def test_metrics_reconnect_starts_before_model_warmup(self):
        self.run_startup(True)

    def test_model_failure_still_cleans_up_owned_forward_worker(self):
        self.run_startup(False)


if __name__ == "__main__":
    unittest.main()
