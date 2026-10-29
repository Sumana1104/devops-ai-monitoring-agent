"""Offline operator/startup tests: no real Telegram, Docker, Kubernetes or AI."""
from contextlib import redirect_stdout
import io
import json
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
from opspilot_integration import run_ai_bridge
from api_service.app import telegram_notifications
with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fixture-bot-token",
                             "TELEGRAM_WEBHOOK_SECRET": "fixture-webhook-secret"}):
    from opspilot_integration import telegram_webhook as webhook


class WebhookRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "backup.json"
        self.path.write_bytes(b'{"url":"https://render.example/telegram/webhook","allowed_updates":["message"]}')
        self.saved = self.path.read_bytes()
        self.install(patch.object(webhook, "state", self.path))
        self.install(patch.object(webhook, "token", "fixture-bot-token"))
        self.install(patch.object(webhook, "secret", "fixture-webhook-secret"))
        self.get = self.install(patch.object(webhook.requests, "get"))
        self.post = self.install(patch.object(webhook.requests, "post"))
        self.get.return_value.status_code = 200
        self.get.return_value.json.return_value = {"local_bridge": True, "demo_restart_enabled": False}
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value = {"status": "ignored_non_callback"}
        self.url = "https://fixture-route.trycloudflare.com"

    def install(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def refresh(self, **kwargs):
        with patch.object(webhook, "call") as api:
            api.return_value = {"url": self.url + "/telegram/webhook"}
            result = webhook.refresh_route(self.url, **kwargs)
        return api, result

    def test_refresh_preserves_backup_and_pending_updates(self):
        api, result = self.refresh(confirmed=True)
        self.assertEqual(self.path.read_bytes(), self.saved)
        self.assertEqual(result["url"], self.url + "/telegram/webhook")
        self.assertEqual(api.call_args_list[0].args, ("setWebhook",))
        self.assertIs(api.call_args_list[0].kwargs["drop_pending_updates"], False)
        self.assertEqual(api.call_args_list[0].kwargs["allowed_updates"], ["callback_query"])
        self.assertEqual(self.post.call_args.kwargs["json"], {})
        self.assertIs(self.post.call_args.kwargs["allow_redirects"], False)
        self.assertIs(self.get.call_args.kwargs["allow_redirects"], False)

    def test_default_requires_explicit_uppercase_confirmation(self):
        with patch("builtins.input", return_value="switch"), patch.object(webhook, "call") as api:
            with self.assertRaises(ValueError):
                webhook.refresh_route(self.url)
        api.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.saved)

    def test_missing_backup_stops_before_any_network_call(self):
        self.path.unlink()
        with patch.object(webhook, "call") as api, self.assertRaises(ValueError):
            webhook.refresh_route(self.url, confirmed=True)
        api.assert_not_called()
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_invalid_or_redirected_hosts_are_rejected(self):
        for url in ("http://fixture-route.trycloudflare.com",
                    "https://example.com", self.url + "/health",
                    self.url + ".evil.example", self.url + "?q=1",
                    "https://user@fixture-route.trycloudflare.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                webhook.check_route(url)
        self.get.assert_not_called()

    def test_public_failure_never_changes_webhook(self):
        for code in (301, 403, 502, 530):
            with self.subTest(code=code):
                self.get.return_value.status_code = code
                with patch.object(webhook, "call") as api, self.assertRaises(ValueError):
                    webhook.refresh_route(self.url, confirmed=True)
                api.assert_not_called()
        self.post.assert_not_called()

    def test_wrong_public_service_is_rejected(self):
        self.get.return_value.json.return_value = {"status": "ok", "local_bridge": False}
        with patch.object(webhook, "call") as api, self.assertRaises(ValueError):
            webhook.refresh_route(self.url, confirmed=True)
        api.assert_not_called()

    def test_wrong_shared_secret_is_rejected(self):
        self.post.return_value.status_code = 403
        with patch.object(webhook, "call") as api, self.assertRaises(ValueError):
            webhook.refresh_route(self.url, confirmed=True)
        api.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.saved)

    def test_unknown_callback_response_is_rejected(self):
        self.post.return_value.json.return_value = {"status": "unexpected"}
        with patch.object(webhook, "call") as api, self.assertRaises(ValueError):
            webhook.refresh_route(self.url, confirmed=True)
        api.assert_not_called()

    def test_uncertain_refresh_failure_keeps_restore_backup(self):
        with patch.object(webhook, "call", side_effect=RuntimeError("safe API failure")):
            with self.assertRaises(RuntimeError):
                webhook.refresh_route(self.url, confirmed=True)
        self.assertEqual(self.path.read_bytes(), self.saved)

    def test_url_verification_failure_keeps_restore_backup(self):
        with patch.object(webhook, "call", return_value={"url": "https://different.example"}):
            with self.assertRaises(ValueError):
                webhook.refresh_route(self.url, confirmed=True)
        self.assertEqual(self.path.read_bytes(), self.saved)

    def test_uncertain_first_switch_keeps_only_restore_backup(self):
        self.path.unlink()
        with patch.object(sys, "argv", ["telegram_webhook", "on", self.url]), \
             patch("builtins.input", return_value="SWITCH"), \
             patch.object(webhook, "call", side_effect=[
                 {"url": "https://render.example/telegram/webhook", "allowed_updates": ["message"]},
                 RuntimeError("uncertain API outcome")]), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                webhook.main()
        self.assertEqual(json.loads(self.path.read_bytes())["url"], "https://render.example/telegram/webhook")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_backup_is_not_overwritten_by_first_switch(self):
        with patch.object(sys, "argv", ["telegram_webhook", "on", self.url]), \
             patch.object(webhook, "call", return_value={"url": self.url + "/telegram/webhook"}), \
             redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            webhook.main()
        self.assertEqual(self.path.read_bytes(), self.saved)
        self.get.assert_not_called()

    def test_api_errors_do_not_expose_token_or_raw_url(self):
        self.post.side_effect = requests.HTTPError("https://api.telegram.org/botfixture-bot-token/getWebhookInfo")
        with self.assertRaises(RuntimeError) as stopped:
            webhook.call("getWebhookInfo")
        self.assertNotIn("fixture-bot-token", str(stopped.exception))
        self.assertNotIn("api.telegram.org", str(stopped.exception))

    def test_network_failure_does_not_change_webhook(self):
        self.get.side_effect = requests.Timeout("private request data")
        with patch.object(webhook, "call") as api, self.assertRaises(ValueError) as stopped:
            webhook.refresh_route(self.url, confirmed=True)
        api.assert_not_called()
        self.assertNotIn("private request data", str(stopped.exception))

    def test_notification_errors_do_not_expose_bot_token(self):
        self.post.side_effect = requests.HTTPError("https://api.telegram.org/botfixture-bot-token/sendMessage")
        with self.assertRaises(RuntimeError) as stopped:
            telegram_notifications._telegram_request("sendMessage", "fixture-bot-token", {})
        self.assertNotIn("fixture-bot-token", str(stopped.exception))
        self.assertNotIn("api.telegram.org", str(stopped.exception))


class LauncherExistingBridgeTests(unittest.TestCase):
    def check(self, health, success):
        environment = {name: "offline-placeholder" for name in (
            "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "TELEGRAM_APPROVER_USER_ID", "TELEGRAM_WEBHOOK_SECRET")}
        old_directory = Path.cwd()
        try:
            with patch.dict(os.environ, environment), patch.object(sys, "argv", ["run_ai_bridge"]), \
                 patch.object(run_ai_bridge.shutil, "which", return_value="/offline/tool"), \
                 patch.object(run_ai_bridge.socket, "socket") as socket, \
                 patch.object(run_ai_bridge.subprocess, "Popen") as child, \
                 patch.object(run_ai_bridge.subprocess, "run") as command, \
                 patch.object(requests, "get") as http, redirect_stdout(io.StringIO()) as output:
                socket.return_value.__enter__.return_value.connect_ex.return_value = 0
                http.return_value.json.return_value = health
                if success:
                    run_ai_bridge.main()
                    self.assertIn("already running", output.getvalue())
                else:
                    with self.assertRaises(SystemExit):
                        run_ai_bridge.main()
                child.assert_not_called()
                command.assert_not_called()
        finally:
            os.chdir(old_directory)

    def test_same_verified_mode_does_not_start_duplicate(self):
        self.check({"local_bridge": True, "demo_restart_enabled": False,
                    "runtime_version": "opspilot-stability-1"}, True)

    def test_different_mode_is_not_silently_changed(self):
        self.check({"local_bridge": True, "demo_restart_enabled": True,
                    "runtime_version": "opspilot-stability-1"}, False)

    def test_old_code_requires_explicit_reload(self):
        self.check({"local_bridge": True, "demo_restart_enabled": False}, False)

    def test_another_service_is_never_stopped(self):
        self.check({"local_bridge": False}, False)

    def test_invalid_health_response_is_rejected(self):
        self.check(["unexpected"], False)

    def test_outage_does_not_prevent_incident_bridge_start(self):
        old_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "observability").mkdir()
            (root / "observability/docker-compose.yml").write_text("services: {}\n")
            environment = {name: "offline-placeholder" for name in (
                "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "TELEGRAM_APPROVER_USER_ID", "TELEGRAM_WEBHOOK_SECRET")}
            try:
                with patch.dict(os.environ, environment), patch.object(run_ai_bridge, "ROOT", root), \
                     patch.object(sys, "argv", ["run_ai_bridge"]), \
                     patch.object(run_ai_bridge.shutil, "which", return_value="/offline/tool"), \
                     patch.object(run_ai_bridge.socket, "socket") as socket, \
                     patch.object(run_ai_bridge.threading, "Thread"), \
                     patch.object(run_ai_bridge.threading, "Event") as event, \
                     patch.object(run_ai_bridge.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                     patch.object(run_ai_bridge.subprocess, "Popen") as child, \
                     patch.object(requests, "get") as http, redirect_stdout(io.StringIO()) as output:
                    socket.return_value.__enter__.return_value.connect_ex.return_value = 1
                    event.return_value.wait.return_value = False
                    http.return_value.json.return_value = {"data": {"result": [{"value": [0, "0"]}]}}
                    child.return_value.wait.return_value = 0
                    child.return_value.poll.return_value = 0
                    with self.assertRaises(SystemExit) as stopped:
                        run_ai_bridge.main()
                    self.assertEqual(stopped.exception.code, 0)
                    child.assert_called_once()
                    self.assertIn("MONITORING DEGRADED", output.getvalue())
                    self.assertNotIn("PASS: Prometheus sees demo up=1", output.getvalue())
            finally:
                os.chdir(old_directory)


if __name__ == "__main__":
    unittest.main()
