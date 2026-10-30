"""OTEL_SDK_DISABLED prevents offline API checks from contacting a collector."""
import importlib.util
import io
import logging
import os
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class OfflineAPILoggingTests(unittest.TestCase):
    def check(self, value):
        logger = logging.getLogger("opspilot.ai")
        old = list(logger.handlers)
        spec = importlib.util.spec_from_file_location("offline_api_logging_fixture", Path(__file__).resolve().parents[1] / "ai_service/app/main.py")
        module = importlib.util.module_from_spec(spec)
        try:
            with patch.dict(os.environ, {"OTEL_SDK_DISABLED": value}), \
                 patch.dict(sys.modules, {spec.name: module}), \
                 patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden")), \
                 redirect_stdout(io.StringIO()):
                spec.loader.exec_module(module)
                self.assertIsNone(module.logger_provider)
                self.assertEqual(module.root()["status"], "AI Monitoring Agent Running")
        finally:
            logger.handlers = old

    def test_true_disables_logging_export(self): self.check("true")
    def test_one_disables_logging_export(self): self.check("1")
    def test_yes_disables_logging_export(self): self.check("yes")
